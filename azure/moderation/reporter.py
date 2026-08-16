"""
Azure Moderation: Reporter

Generates reports of moderation actions and sends them to admin channels.

Supports:
  - Real-time single-action reports
  - Aggregated batch reports (recommended for high-volume servers)
  - Discord embed formatting
  - Persistent action log file
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .policy import ModerationPolicy

logger = logging.getLogger("azure.moderation.reporter")


@dataclass
class ActionReport:
    """A single moderation report entry."""
    timestamp: float
    action_type: str
    target_user_id: str
    target_user_name: str
    target_message_id: str | None
    channel_id: str
    channel_name: str
    severity: str
    category: str
    reason: str
    confidence: float
    dry_run: bool
    message_content: str = ""  # truncated for privacy

    def to_embed_dict(self) -> dict:
        """Return a Discord embed-compatible dict."""
        color = {
            "low": 0x3498db,
            "medium": 0xf39c12,
            "high": 0xe74c3c,
            "critical": 0x8e44ad,
            "none": 0x2ecc71,
        }.get(self.severity.lower(), 0x95a5a6)

        return {
            "title": f"[{self.severity.upper()}] {self.category}",
            "color": color,
            "fields": [
                {"name": "Action", "value": self.action_type, "inline": True},
                {"name": "User", "value": f"{self.target_user_name} ({self.target_user_id})", "inline": True},
                {"name": "Channel", "value": f"<#{self.channel_id}>", "inline": True},
                {"name": "Reason", "value": self.reason[:1000], "inline": False},
                {"name": "Confidence", "value": f"{self.confidence:.0%}", "inline": True},
                {"name": "Content", "value": (self.message_content[:300] + "...") if len(self.message_content) > 300 else self.message_content, "inline": False},
            ],
            "footer": {"text": f"Azure Moderation{' [DRY RUN]' if self.dry_run else ''}"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.timestamp)),
        }

    def to_text(self) -> str:
        """Return a plain text report line."""
        dry = "[DRY] " if self.dry_run else ""
        return (
            f"{dry}[{self.severity.upper()}] {self.action_type} "
            f"user={self.target_user_name} channel=<#{self.channel_id}> "
            f"reason={self.reason[:80]}"
        )


class ModerationReporter:
    """
    Collects moderation reports and dispatches them to admin channels.

    Two modes:
      - Immediate: send report instantly (good for low-volume)
      - Aggregated: batch reports and send every N seconds (good for high-volume)
    """

    def __init__(self, policy: ModerationPolicy, log_dir: Path | None = None):
        self.policy = policy
        self._pending: list[ActionReport] = []
        self._last_batch_send: float = 0.0
        self._log_dir = Path(log_dir) if log_dir else Path("logs")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = self._log_dir / "moderation_actions.jsonl"

    def report(self, report: ActionReport):
        """Log a report and optionally dispatch immediately."""
        self._append_log(report)
        if self.policy.report_aggregated:
            self._pending.append(report)
            self._maybe_send_batch()
        else:
            self._dispatch(report)

    def _append_log(self, report: ActionReport):
        """Write to persistent JSONL log."""
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "timestamp": report.timestamp,
                    "action": report.action_type,
                    "user_id": report.target_user_id,
                    "user_name": report.target_user_name,
                    "channel_id": report.channel_id,
                    "severity": report.severity,
                    "category": report.category,
                    "reason": report.reason,
                    "confidence": report.confidence,
                    "dry_run": report.dry_run,
                    "content": report.message_content[:500],
                }) + "\n")
        except Exception as e:
            logger.error(f"[reporter] log write failed: {e}")


    def _maybe_send_batch(self):
        """Send pending reports if the batch interval has elapsed."""
        now = time.time()
        if not self._pending:
            return
        interval = self.policy.report_interval_seconds
        if now - self._last_batch_send >= interval:
            self._send_batch(self._pending)
            self._pending.clear()
            self._last_batch_send = now

    def flush(self):
        """Force-send any pending reports immediately."""
        if self._pending:
            self._send_batch(self._pending)
            self._pending.clear()
            self._last_batch_send = time.time()

    def _send_batch(self, reports: list[ActionReport]):
        """Dispatch a batch of reports. Override in Discord bot."""
        # Base implementation just prints to console
        for r in reports:
            logger.info(f"[REPORT] {r.to_text()}")


    def _dispatch(self, report: ActionReport):
        """Dispatch a single report. Base implementation prints to console."""
        logger.info(f"[REPORT] {report.to_text()}")


    # ------------------------------------------------------------------
    # Discord-specific dispatch (called by the bot layer)
    # ------------------------------------------------------------------

    async def send_to_discord_channel(self, channel, report: ActionReport):
        """Send a report to a Discord channel. Must be awaited."""
        if self.policy.report_format == "embed":
            embed_dict = report.to_embed_dict()
            try:
                import discord
                embed = discord.Embed.from_dict(embed_dict)
                await channel.send(embed=embed)
            except Exception as e:
                logger.error(f"[reporter] embed send failed: {e}")
                try:
                    await channel.send(report.to_text()[:2000])
                except Exception as e2:
                    logger.error(f"[reporter] fallback text send also failed: {e2}")
        else:
            await channel.send(report.to_text()[:2000])

    async def send_batch_to_discord_channel(self, channel, reports: list[ActionReport]):
        """Send a batch summary to a Discord channel."""
        if not reports:
            return
        lines = [f"**Azure Moderation Report ({len(reports)} actions)**"]
        for r in reports:
            lines.append(f"- {r.to_text()}")
        text = "\n".join(lines)
        # Chunk for Discord 2000 char limit
        for i in range(0, len(text), 1900):
            await channel.send(text[i:i+1900])

    # ------------------------------------------------------------------
    # Summary stats
    # ------------------------------------------------------------------

    def get_summary(self, hours: int = 24) -> dict:
        """Read the log file and return summary stats."""
        cutoff = time.time() - (hours * 3600)
        total = 0
        by_category = {}
        by_severity = {}
        by_action = {}

        if not self._log_file.exists():
            return {"total": 0, "by_category": {}, "by_severity": {}, "by_action": {}}

        try:
            with open(self._log_file, encoding="utf-8") as f:
                for line in f:
                    entry = json.loads(line.strip())
                    if entry.get("timestamp", 0) < cutoff:
                        continue
                    total += 1
                    by_category[entry.get("category", "unknown")] = \
                        by_category.get(entry.get("category", "unknown"), 0) + 1
                    by_severity[entry.get("severity", "unknown")] = \
                        by_severity.get(entry.get("severity", "unknown"), 0) + 1
                    by_action[entry.get("action", "unknown")] = \
                        by_action.get(entry.get("action", "unknown"), 0) + 1
        except Exception as e:
            logger.error(f"[reporter] summary aggregation error: {e}")


        return {
            "total": total,
            "by_category": by_category,
            "by_severity": by_severity,
            "by_action": by_action,
        }
