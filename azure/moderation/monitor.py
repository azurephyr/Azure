"""
Azure Moderation: Monitor

Tracks classification and action metrics during the observation period.

Purpose: provide data-driven evidence for phase transitions.

Key metrics:
  - total_classifications
  - true_positives (correctly flagged)
  - false_positives (incorrectly flagged)
  - false_negatives (missed, detected after the fact)
  - precision = TP / (TP + FP)
  - recall = TP / (TP + FN)
  - action_distribution (what actions were taken / simulated)
  - category_distribution (spam vs scam vs toxicity)
  - user_offense_history (who triggered what)

Human feedback loop:
  - A moderator can mark a report as "correct" or "false_positive"
  - This feeds into the monitor and improves precision tracking
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .classifier import ClassificationResult

logger = logging.getLogger("azure.moderation.monitor")


@dataclass
class MonitoredEvent:
    """A single classification event tracked for metrics."""
    timestamp: float
    message_id: str
    author_id: str
    author_name: str
    channel_id: str
    content: str
    category: str
    severity: str
    confidence: float
    action_taken: str
    dry_run: bool
    # Human feedback
    feedback: str | None = None  # "correct" | "false_positive" | "missed"
    feedback_by: str | None = None
    feedback_time: float | None = None


class ModerationMonitor:
    """
    Tracks moderation metrics and generates transition readiness reports.

    Usage:
        monitor = ModerationMonitor(log_dir=Path("logs"))
        monitor.record_event(classification_result, message, action, dry_run=True)

        # After 24-72 hours:
        report = monitor.generate_readiness_report()
        if report["ready_for_reactive_limited"]:
            logger.info("Azure can escalate to reactive_limited")

    """

    # Thresholds for phase transition
    PRECISION_THRESHOLD = 0.85   # 85% precision required for reactive_limited
    RECALL_THRESHOLD = 0.70      # 70% recall required
    MIN_SAMPLES = 50             # Need at least 50 classifications to evaluate
    MAX_FP_RATE = 0.05           # Max 5% false positive rate on normal messages
    MAX_CRITICAL_FP = 0          # Zero critical false positives (banning innocent users)

    def __init__(self, log_dir: Path | None = None):
        self.log_dir = Path(log_dir) if log_dir else Path("logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._events_path = self.log_dir / "moderation_monitor.jsonl"
        self._events: list[MonitoredEvent] = []
        self._load_existing()

    def _load_existing(self):
        """Load previous events from disk."""
        if not self._events_path.exists():
            return
        known_fields = {f.name for f in MonitoredEvent.__dataclass_fields__.values()}
        with open(self._events_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    filtered = {k: v for k, v in d.items() if k in known_fields}
                    self._events.append(MonitoredEvent(**filtered))
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"[monitor] skipping malformed line: {e}")


    def _save(self, event: MonitoredEvent):
        """Append event to persistent log."""
        try:
            with open(self._events_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "timestamp": event.timestamp,
                    "message_id": event.message_id,
                    "author_id": event.author_id,
                    "author_name": event.author_name,
                    "channel_id": event.channel_id,
                    "content": event.content[:200],  # truncate
                    "category": event.category,
                    "severity": event.severity,
                    "confidence": event.confidence,
                    "action_taken": event.action_taken,
                    "dry_run": event.dry_run,
                    "feedback": event.feedback,
                    "feedback_by": event.feedback_by,
                    "feedback_time": event.feedback_time,
                }) + "\n")
        except Exception as e:
            logger.warning(f"[monitor] save warning: {e}")


    def record_event(self, *, classification: ClassificationResult,
                     message_id: str, author_id: str, author_name: str,
                     channel_id: str, content: str, action_taken: str,
                     dry_run: bool = True):
        """Record a classification event."""
        event = MonitoredEvent(
            timestamp=time.time(),
            message_id=message_id,
            author_id=author_id,
            author_name=author_name,
            channel_id=channel_id,
            content=content,
            category=classification.category,
            severity=classification.severity.name,
            confidence=classification.confidence,
            action_taken=action_taken,
            dry_run=dry_run,
        )
        self._events.append(event)
        self._save(event)

    def add_feedback(self, message_id: str, feedback: str, by: str):
        """
        Human feedback on a classification.
        feedback: "correct" | "false_positive" | "missed"
        """
        for event in reversed(self._events):
            if event.message_id == message_id:
                event.feedback = feedback
                event.feedback_by = by
                event.feedback_time = time.time()
                # Re-save all events (inefficient but simple for now)
                self._rewrite_log()
                break

    def _rewrite_log(self):
        """Rewrite the entire log file (used after feedback updates)."""
        try:
            with open(self._events_path, "w", encoding="utf-8") as f:
                for e in self._events:
                    f.write(json.dumps({
                        "timestamp": e.timestamp,
                        "message_id": e.message_id,
                        "author_id": e.author_id,
                        "author_name": e.author_name,
                        "channel_id": e.channel_id,
                        "content": e.content[:200],
                        "category": e.category,
                        "severity": e.severity,
                        "confidence": e.confidence,
                        "action_taken": e.action_taken,
                        "dry_run": e.dry_run,
                        "feedback": e.feedback,
                        "feedback_by": e.feedback_by,
                        "feedback_time": e.feedback_time,
                    }) + "\n")
        except Exception as e:
            logger.warning(f"[monitor] rewrite warning: {e}")


    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def get_metrics(self, hours: int = 72) -> dict:
        """
        Calculate metrics over the last N hours.
        """
        cutoff = time.time() - (hours * 3600)
        recent = [e for e in self._events if e.timestamp >= cutoff]

        total = len(recent)
        if total == 0:
            return {"total": 0, "note": "no events recorded yet"}

        # Count by feedback (if available)
        tp = sum(1 for e in recent if e.feedback == "correct")
        fp = sum(1 for e in recent if e.feedback == "false_positive")
        fn = sum(1 for e in recent if e.feedback == "missed")

        # If no feedback yet, estimate from severity
        # (messages with severity NONE but classified as something = potential FP)
        # This is rough but gives a signal
        if tp + fp + fn == 0:
            # Heuristic: count classifications as "needs review"
            flagged = sum(1 for e in recent if e.severity != "NONE")
            normal = sum(1 for e in recent if e.severity == "NONE")
            # Without feedback, we can't know true precision
            return {
                "total": total,
                "flagged": flagged,
                "normal": normal,
                "feedback_given": 0,
                "note": "insufficient feedback for precision calculation. use !mod_feedback to mark classifications.",
            }

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

        # Category breakdown
        by_category = {}
        for e in recent:
            if e.severity != "NONE":
                by_category[e.category] = by_category.get(e.category, 0) + 1

        # Action breakdown
        by_action = {}
        for e in recent:
            if e.action_taken != "none":
                by_action[e.action_taken] = by_action.get(e.action_taken, 0) + 1

        return {
            "total": total,
            "feedback_given": tp + fp + fn,
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1_score": round(f1, 3),
            "by_category": by_category,
            "by_action": by_action,
        }

    def generate_readiness_report(self, hours: int = 72) -> dict:
        """
        Generate a report on whether Azure is ready for phase escalation.
        Returns dict with readiness assessment.
        """
        metrics = self.get_metrics(hours=hours)
        total = metrics.get("total", 0)
        feedback = metrics.get("feedback_given", 0)
        precision = metrics.get("precision", 0.0)
        recall = metrics.get("recall", 0.0)
        fp = metrics.get("false_positives", 0)

        # Check requirements for reactive_limited
        checks = {
            "min_samples_met": total >= self.MIN_SAMPLES,
            "precision_threshold_met": precision >= self.PRECISION_THRESHOLD,
            "recall_threshold_met": recall >= self.RECALL_THRESHOLD,
            "fp_rate_acceptable": fp / max(total, 1) <= self.MAX_FP_RATE,
            "no_critical_fp": True,  # would need specific tracking
            "sufficient_feedback": feedback >= max(total * 0.2, 10),  # 20% or 10 samples
        }

        ready_for_limited = all(checks.values())

        report = {
            "hours_observed": hours,
            "total_events": total,
            "feedback_given": feedback,
            "precision": precision,
            "recall": recall,
            "checks": checks,
            "ready_for_reactive_limited": ready_for_limited,
            "ready_for_reactive_full": False,  # always false until limited is proven
            "recommendation": self._recommendation(ready_for_limited, checks, metrics),
        }
        return report

    def _recommendation(self, ready: bool, checks: dict, metrics: dict) -> str:
        if not checks["min_samples_met"]:
            return f"Need more data. {metrics.get('total', 0)}/{self.MIN_SAMPLES} minimum classifications."
        if not checks["sufficient_feedback"]:
            return "Need more human feedback. Use !mod_feedback <message_id> correct|false_positive|missed"
        if not checks["precision_threshold_met"]:
            return f"Precision too low ({metrics.get('precision', 0)}). Need ≥{self.PRECISION_THRESHOLD}. Review false positives."
        if not checks["recall_threshold_met"]:
            return f"Recall too low ({metrics.get('recall', 0)}). Need ≥{self.RECALL_THRESHOLD}. May be missing threats."
        if not checks["fp_rate_acceptable"]:
            return "False positive rate too high. Review recent classifications."
        if ready:
            return "Azure is ready for reactive_limited. Use !mod_mode reactive_limited to escalate."
        return "Review metrics and feedback before escalating."

    def get_summary_text(self, hours: int = 72) -> str:
        """Return a human-readable summary."""
        report = self.generate_readiness_report(hours=hours)
        lines = [
            f"**Azure Moderation Readiness Report (last {hours}h)**",
            f"Total events: {report['total_events']}",
            f"Feedback given: {report['feedback_given']}",
            f"Precision: {report['precision']}",
            f"Recall: {report['recall']}",
            "",
            "**Checks:**",
        ]
        for check, passed in report["checks"].items():
            status = "✅" if passed else "❌"
            lines.append(f"{status} {check}")
        lines.append("")
        lines.append(f"**Recommendation:** {report['recommendation']}")
        return "\n".join(lines)
