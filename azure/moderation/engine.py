"""
Azure Moderation: Engine

The central orchestrator that wires together:
  scanner     -> reads messages
  classifier  -> scores them
  behavioral  -> tracks per-user patterns
  temporal    -> detects cross-message raids/spam waves
  risk        -> dynamic multi-factor scoring
  decision    -> intelligent action selection
  policy      -> decides what to do (respecting phase boundaries)
  actions     -> executes actions (clamped by phase)
  reporter    -> logs and reports
  monitor     -> tracks metrics for phase transition readiness

Phased permission escalation:
  DRY_RUN          -> classify only, no actions
  REACTIVE_LIMITED -> delete, warn, short timeout (≤5 min)
  REACTIVE_FULL    -> all actions including kick/ban/lockdown

Example usage (from Discord bot):
    engine = ModerationEngine(bot=bot, policy=ModerationPolicy(phase=ModerationPhase.DRY_RUN))

    # On every message:
    await engine.on_message(message)

    # Periodic autonomous scan (raid/spam wave detection):
    await engine.autonomous_scan(guild)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from ..behavioral import BehavioralAnalyzer
from ..risk import RiskEngine
from ..temporal import TemporalAnalyzer
from .actions import ActionExecutor
from .classifier import MessageClassifier
from .confirmation import ConfirmationQueue, requires_confirmation
from .monitor import ModerationMonitor
from .phase import ModerationPhase, action_allowed
from .policy import ActionType, ModerationPolicy
from .reporter import ActionReport, ModerationReporter
from .scanner import ChannelScanner

logger = logging.getLogger("azure.moderation.engine")

# Sentiment-aware moderation (v3)
try:
    from .sentiment_engine import SentimentAnalysis, SentimentEngine
except ImportError:
    SentimentEngine = None
    SentimentAnalysis = None


class ModerationEngine:
    """
    Autonomous moderation engine for Azure with phased trust escalation
    and Phase Alpha intelligence (behavioral, temporal, risk, decision).

    Usage flow:
      1. Create engine with policy (default: DRY_RUN)
      2. Call engine.on_message(message) for every new Discord message
      3. Call engine.autonomous_scan(guild) periodically (raid/spam wave detection)
      4. Check engine.get_stats() for action summary
      5. Check engine.get_readiness_report() for phase transition data
    """

    def __init__(self, bot=None, policy: ModerationPolicy | None = None,
                 log_dir=None):
        self.bot = bot
        self.policy = policy or ModerationPolicy()
        self.classifier = MessageClassifier()
        self.scanner = ChannelScanner(
            lookback_minutes=self.policy.proactive_scan_lookback_minutes,
        )
        self.actions = ActionExecutor(policy=self.policy, bot=bot)

        if log_dir is None:
            log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
        self.reporter = ModerationReporter(policy=self.policy, log_dir=log_dir)
        self.monitor = ModerationMonitor(log_dir=log_dir)
        self._user_offense_history: dict[str, list[float]] = {}  # user_id -> list of offense timestamps

        # Phase Alpha: intelligence engines (no ML, pure statistics)
        self.behavioral_analyzer = BehavioralAnalyzer()
        self.temporal_analyzer = TemporalAnalyzer()
        self.risk_engine = RiskEngine()
        # Lazy import to break circular dependency
        from ..decision import DecisionEngine
        self.decision_engine = DecisionEngine(policy=self.policy)
        self.confirmation_queue = ConfirmationQueue(timeout_seconds=60)

        # v3: Sentiment-aware moderation
        self.sentiment_engine = SentimentEngine() if SentimentEngine is not None else None
        if self.sentiment_engine:
            logger.info("[moderation] sentiment engine enabled")


    # ------------------------------------------------------------------
    # Sentiment integration helpers
    # ------------------------------------------------------------------

    def _analyze_sentiment(self, message, cached, user_id) -> SentimentAnalysis | None:
        """Run sentiment analysis on a message. Returns None if engine unavailable."""
        if self.sentiment_engine is None:
            return None
        try:
            return self.sentiment_engine.analyze(
                content=cached.content,
                user_id=user_id,
                timestamp=cached.timestamp,
            )
        except Exception as e:
            logger.error(f"[moderation] sentiment analysis error: {e}")

            return None

    def _sentiment_to_risk_boost(self, sentiment: SentimentAnalysis | None, user_id: str = "") -> float:
        """Convert sentiment analysis to a risk score boost (0-1)."""
        if sentiment is None:
            return 0.0
        boost = 0.0
        # Sarcasm is a red flag for trolling
        if sentiment.sarcasm_probability > 0.7:
            boost += 0.15
        # Passive aggression in conflict threads
        if sentiment.passive_aggression > 0.6:
            boost += 0.1
        # Manipulation attempts
        if sentiment.manipulation_score > 0.6:
            boost += 0.2
        # Rapid escalation (getting angrier)
        if sentiment.escalation_delta < -0.5:
            boost += 0.15
        # Consistently negative trajectory
        if self.sentiment_engine and user_id:
            trajectory = self.sentiment_engine.get_user_trajectory(user_id)
            if trajectory == "declining":
                boost += 0.1
        return min(boost, 0.5)  # Cap at 0.5 to not over-dominate

    # ------------------------------------------------------------------
    # Main entry points
    # ------------------------------------------------------------------

    async def on_message(self, message) -> ActionReport | None:
        """
        Process a single incoming Discord message with full intelligence pipeline.
        Called from the bot's on_message event.

        Phase Alpha flow:
          1. Fast classification (rule-based)
          2. Behavioral analysis (stats-based)
          3. Temporal analysis (cross-message patterns)
          4. Risk scoring (dynamic, multi-factor)
          5. Intelligent decision (least intrusive action)
          6. Execution (phase-aware clamping)

        Returns ActionReport if action was taken, None if no action.
        """
        # Skip bot's own messages
        author = getattr(message, "author", None)
        if author and getattr(author, "bot", False):
            return None

        # IDs
        guild_id = str(getattr(message.guild, "id", "dm"))
        user_id = str(getattr(author, "id", "unknown"))
        channel_id = str(getattr(getattr(message, "channel", None), "id", "unknown"))

        # Exemptions
        if self.policy.is_exempt_user(user_id):
            return None
        if self.policy.is_exempt_channel(channel_id):
            return None

        # Whitelist check (owner, admin, bot, trusted roles)
        is_whitelisted = self.policy.is_whitelisted(author)

        # Ingest into scanner cache
        cached = self.scanner.ingest(message)
        if not cached:
            return None

        # Ingest into behavioral analyzer
        self.behavioral_analyzer.ingest_message(
            guild_id=guild_id,
            user_id=user_id,
            content=cached.content,
            message_id=str(getattr(message, "id", "")),
        )

        # Get author's recent messages for repetition detection
        recent = self.scanner.get_recent_by_author(user_id, minutes=10)
        recent_dicts = [{"content": m.content, "timestamp": m.timestamp} for m in recent]

        # 1. Classify (fast content detection)
        result = self.classifier.classify(
            text=cached.content,
            author_id=user_id,
            recent_messages=recent_dicts,
        )

        # v3: Sentiment analysis (auto-triggered on every message)
        sentiment = self._analyze_sentiment(message, cached, user_id)
        sentiment_boost = self._sentiment_to_risk_boost(sentiment, user_id)

        # 2. Behavioral analysis (stats-based)
        behavioral_signals = self.behavioral_analyzer.analyze_message(
            guild_id=guild_id, user_id=user_id, content=cached.content
        )

        # 3. Temporal analysis (cross-message patterns)
        severity_numeric = self._severity_to_numeric(result.severity)
        self.temporal_analyzer.ingest_event(
            message_id=str(getattr(message, "id", "")),
            user_id=user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            content=cached.content,
            severity=severity_numeric,
            category=result.category,
        )
        temporal_signals = self.temporal_analyzer.analyze_situation(guild_id)

        # Account age for risk scoring
        account_age_days = None
        if hasattr(author, "created_at") and author.created_at:
            try:
                now = datetime.now(UTC)
                created = author.created_at
                # Normalize to UTC-aware datetimes
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                account_age_days = (now - created).days
            except Exception as e:
                logger.error(f"[engine] account_age calc error: {e}")


        # 4. Risk scoring (dynamic, multi-factor)
        risk_profile = self.risk_engine.compute_full_risk(
            guild_id=guild_id,
            user_id=user_id,
            channel_id=channel_id,
            content_severity=severity_numeric,
            content_confidence=result.confidence,
            behavioral_signals=behavioral_signals,
            temporal_signals=temporal_signals.to_dict(),
            account_age_days=account_age_days,
        )

        # v3: Apply sentiment boost to risk
        if sentiment_boost > 0:
            risk_profile.total_risk = min(1.0, risk_profile.total_risk + sentiment_boost)
            risk_profile.confidence = min(1.0, risk_profile.confidence + sentiment_boost * 0.5)
            report_content = cached.content
            if sentiment and sentiment.sarcasm_probability > 0.7:
                report_content = f"[SARCASM DETECTED: {sentiment.sarcasm_probability:.0%}] {report_content}"
            if sentiment and sentiment.manipulation_score > 0.6:
                report_content = f"[MANIPULATION DETECTED: {sentiment.manipulation_score:.0%}] {report_content}"

        # 5. Intelligent decision
        decision = self.decision_engine.decide(
            content_severity=severity_numeric,
            content_confidence=result.confidence,
            content_category=result.category,
            behavioral_signals=behavioral_signals,
            temporal_signals=temporal_signals.to_dict(),
            risk_profile=risk_profile.to_dict(),
            phase=self.policy.phase,
            is_whitelisted=is_whitelisted,
            author_name=getattr(author, "display_name", "unknown"),
        )

        # If no action needed, still return None but we've tracked everything
        if decision.action == ActionType.NONE:
            return None

        # Build report with full explanation from decision engine
        report = ActionReport(
            timestamp=time.time(),
            action_type=decision.action.value,
            target_user_id=user_id,
            target_user_name=getattr(author, "display_name", "unknown"),
            target_message_id=str(getattr(message, "id", "unknown")),
            channel_id=channel_id,
            channel_name=str(getattr(getattr(message, "channel", None), "name", "unknown")),
            severity=result.severity.name.lower(),
            category=result.category,
            reason=decision.explanation,
            confidence=decision.confidence,
            dry_run=self.policy.is_dry_run(),
            message_content=cached.content,
        )

        # Record in monitor (for phase transition metrics)
        self.monitor.record_event(
            classification=result,
            message_id=str(getattr(message, "id", "unknown")),
            author_id=user_id,
            author_name=getattr(author, "display_name", "unknown"),
            channel_id=channel_id,
            content=cached.content,
            action_taken=decision.action.value,
            dry_run=self.policy.is_dry_run(),
        )

        # 6. Execute action (if not dry_run and action is permitted by phase)
        if not self.policy.is_dry_run() and decision.action != ActionType.NONE:
            if action_allowed(self.policy.phase, decision.action.value):
                # Check if confirmation is required
                needs_confirm = requires_confirmation(
                    decision.action, decision.confidence,
                    risk_profile.total_risk, self.policy
                )
                if needs_confirm and self.bot and hasattr(self.bot, 'get_channel'):
                    # Queue for confirmation instead of executing
                    self.confirmation_queue.add(
                        message_id=str(getattr(message, "id", "unknown")),
                        user_id=user_id,
                        user_name=getattr(author, "display_name", "unknown"),
                        action_type=decision.action.value,
                        reason=decision.explanation,
                        channel_id=channel_id,
                        channel_name=str(getattr(getattr(message, "channel", None), "name", "unknown")),
                        confidence=decision.confidence,
                        risk_score=risk_profile.total_risk,
                        explanation=decision.explanation,
                        content_preview=cached.content,
                    )
                    # Send confirmation request to admin channel
                    await self._send_confirmation_request(
                        str(getattr(message, "id", "unknown"))
                    )
                    report.reason += " [AWAITING CONFIRMATION]"
                else:
                    await self._execute_action(decision.action, message, author, report)
            else:
                report.reason += f" [PHASE BLOCKED: {decision.action.value} not allowed in {self.policy.phase.value}]"

        # Report
        self.reporter.report(report)

        # Record offense for escalation tracking and risk engine
        self._record_offense(user_id)
        self.risk_engine.record_user_offense(guild_id, user_id)
        self.behavioral_analyzer.record_offense(guild_id, user_id)

        # If content was spam/scam, update channel spam count
        if result.category in ("spam", "scam"):
            self.risk_engine.update_channel_spam(guild_id, channel_id)

        return report

    # ------------------------------------------------------------------
    # Confirmation helpers
    # ------------------------------------------------------------------
    async def _send_confirmation_request(self, message_id: str):
        """Send a confirmation request to the admin channel."""
        import os
        if not self.bot:
            return
        admin_id = os.environ.get("AZURE_ADMIN_CHANNEL_ID")
        if not admin_id:
            return
        pending = self.confirmation_queue.get(message_id)
        if not pending:
            return
        try:
            channel = self.bot.get_channel(int(admin_id))
            if channel:
                text = self.confirmation_queue.format_request(pending)
                await channel.send(text)
        except Exception as e:
            logger.error(f"[confirmation] failed to send request: {e}")


    async def confirm_action(self, message_id: str):
        """Execute a previously queued action after admin confirmation."""
        pending = self.confirmation_queue.confirm(message_id)
        if not pending:
            return False, "Action not found or already expired."

        # Re-fetch the Discord message
        if not self.bot:
            return False, "Bot not available."
        try:
            channel = self.bot.get_channel(int(pending.channel_id))
            if not channel:
                return False, "Channel not found."
            msg = await channel.fetch_message(int(pending.message_id))
            if not msg:
                return False, "Message not found."

            author = msg.author
            action_map = {
                "delete": ActionType.DELETE,
                "timeout": ActionType.TIMEOUT,
                "kick": ActionType.KICK,
                "ban": ActionType.BAN,
                "warn": ActionType.WARN,
            }
            action_type = action_map.get(pending.action_type)
            if not action_type:
                return False, f"Unknown action: {pending.action_type}"

            report = ActionReport(
                timestamp=time.time(),
                action_type=action_type.value,
                target_user_id=pending.user_id,
                target_user_name=pending.user_name,
                target_message_id=pending.message_id,
                channel_id=pending.channel_id,
                channel_name=pending.channel_name,
                severity="high",
                category="confirmed",
                reason=f"[CONFIRMED] {pending.reason}",
                confidence=pending.confidence,
                dry_run=False,
                message_content=pending.content_preview,
            )
            await self._execute_action(action_type, msg, author, report)
            self.reporter.report(report)
            return True, f"Executed {pending.action_type} on {pending.user_name}"
        except Exception as e:
            return False, f"Execution failed: {e}"

    def cancel_action(self, message_id: str) -> bool:
        """Cancel a pending action."""
        return self.confirmation_queue.cancel(message_id) is not None

    def list_pending_confirmations(self) -> list[dict]:
        """Return all pending confirmations as dicts."""
        return [p.__dict__ for p in self.confirmation_queue.list_pending()]

    # ------------------------------------------------------------------
    # Severity helper
    # ------------------------------------------------------------------
    def _severity_to_numeric(self, severity) -> float:
        """Map Severity enum to 0.0–1.0 score."""
        return {
            "NONE": 0.0,
            "LOW": 0.25,
            "MEDIUM": 0.5,
            "HIGH": 0.75,
            "CRITICAL": 1.0,
        }.get(severity.name, 0.0)

    async def periodic_scan(self, guild) -> list[ActionReport]:
        """
        Proactive scan of all channels in a guild.
        Finds spam clusters, scam campaigns, etc. across channels.
        """
        reports = []

        # Find spam clusters
        clusters = self.scanner.find_spam_clusters(min_size=3, similarity=0.80)
        for cluster in clusters:
            # Report the cluster as a single campaign
            author_ids = set(m.author_id for m in cluster)
            channel_ids = set(m.channel_id for m in cluster)
            report = ActionReport(
                timestamp=time.time(),
                action_type="report",
                target_user_id=", ".join(author_ids)[:100],
                target_user_name="spam campaign",
                target_message_id=None,
                channel_id=", ".join(channel_ids)[:100],
                channel_name="multiple",
                severity="high",
                category="spam",
                reason=f"Spam campaign detected: {len(cluster)} similar messages across {len(channel_ids)} channels",
                confidence=0.95,
                dry_run=self.policy.is_dry_run(),
                message_content=cluster[0].content[:200] if cluster else "",
            )
            reports.append(report)
            self.reporter.report(report)

            # In proactive mode, take action against the campaign
            # Only in reactive_full or reactive_limited (delete only)
            if not self.policy.is_dry_run() and action_allowed(self.policy.phase, "delete"):
                for _m in cluster:
                        # Try to delete messages
                        # Note: we need the actual discord.Message object to delete
                        # The scanner cache only has CachedMessage, so we need to
                        # find the actual message or use message ID
                        logger.info("[engine] cluster action skipped: no discord message objects in scanner cache")


        return reports

    async def scan_and_report(self, guild, admin_channel) -> str:
        """
        Full scan + report. Returns a summary string.
        Used for manual !scan command.
        """
        ingested = await self.scanner.scan_all_channels(guild)
        clusters = self.scanner.find_spam_clusters(min_size=2, similarity=0.75)
        summary = f"Scan complete. Ingested {len(ingested)} messages. Found {len(clusters)} spam clusters."
        for cluster in clusters:
            summary += f"\n  - {len(cluster)} msgs: {cluster[0].content[:80]!r}"
        await self.reporter.send_batch_to_discord_channel(admin_channel, [
            ActionReport(
                timestamp=time.time(),
                action_type="scan",
                target_user_id="system",
                target_user_name="Azure",
                target_message_id=None,
                channel_id="all",
                channel_name="all",
                severity="low",
                category="scan",
                reason=summary,
                confidence=1.0,
                dry_run=self.policy.is_dry_run(),
                message_content="",
            )
        ])
        return summary

    # ------------------------------------------------------------------
    # Autonomous scan (Phase Alpha: situation detection)
    # ------------------------------------------------------------------

    async def autonomous_scan(self, guild) -> dict:
        """
        Autonomous cross-channel scan using temporal + risk + decision engines.
        Detects raids, spam waves, coordinated attacks without fetching new messages.
        Uses the temporal analyzer's existing event cache.

        Returns a situation report dict with full explanation.
        """
        guild_id = str(getattr(guild, "id", "unknown"))

        # Analyze temporal situation
        temporal_signals = self.temporal_analyzer.analyze_situation(guild_id, window_seconds=300)

        # If no significant threat, return early
        if not temporal_signals.is_raid and temporal_signals.raid_probability < 0.5:
            return {
                "guild_id": guild_id,
                "threat_detected": False,
                "raid_probability": temporal_signals.raid_probability,
                "explanation": "No significant temporal patterns detected.",
            }

        # Compute situation risk
        risk_profile = self.risk_engine.compute_situation_risk(
            burst_score=temporal_signals.burst_score,
            coordination_score=temporal_signals.coordination_score,
            cross_channel_score=temporal_signals.cross_channel_score,
            novelty_score=temporal_signals.novelty_score,
            unique_user_count=len(temporal_signals.involved_users),
        )

        # Make situation-level decision
        decision = self.decision_engine.decide_situation(
            temporal_signals=temporal_signals.to_dict(),
            risk_profile={"total_risk": risk_profile, "confidence": temporal_signals.raid_probability},
            phase=self.policy.phase,
            involved_users=temporal_signals.involved_users,
        )

        # Build situation report
        report = {
            "guild_id": guild_id,
            "threat_detected": True,
            "timestamp": time.time(),
            "messages_flagged": temporal_signals.matched_messages,
            "users_involved": temporal_signals.involved_users,
            "channels_involved": temporal_signals.involved_channels,
            "raid_probability": temporal_signals.raid_probability,
            "action": decision.action.value,
            "action_confidence": decision.confidence,
            "explanation": decision.explanation,
            "human_review": decision.human_review,
            "dry_run": self.policy.is_dry_run(),
            "enforcement_enabled": os.environ.get("AZURE_AUTONOMOUS_ENFORCEMENT", "0").lower() in ("1", "true", "yes", "on"),
        }

        # If in dry_run, just log the report
        if (
            self.policy.is_dry_run()
            or decision.action == ActionType.NONE
            or not report["enforcement_enabled"]
        ):
            report["action_taken"] = False
            if not report["enforcement_enabled"] and decision.action != ActionType.NONE:
                report["human_review"] = True
                report["explanation"] = f"{decision.explanation} [AUTONOMOUS ENFORCEMENT DISABLED]"
            return report

        # Execute batch action on involved messages
        action_count = 0
        if decision.action in (ActionType.DELETE, ActionType.TIMEOUT):
            recent_events = self.temporal_analyzer.get_recent_events(guild_id, window_seconds=300)
            for event in recent_events:
                if event.severity > 0.3:  # Only act on actually suspicious messages
                    # Try to find the actual Discord message object
                    msg_obj = None
                    if self.bot:
                        channel = self.bot.get_channel(int(event.channel_id))
                        if channel:
                            try:
                                msg_obj = await channel.fetch_message(int(event.message_id))
                            except Exception as e:
                                logger.error(f"[autonomous_scan] fetch message error: {e}")

                    if msg_obj:
                        if decision.action == ActionType.DELETE:
                            try:
                                await msg_obj.delete(reason=f"Azure autonomous scan: {decision.explanation}")
                                action_count += 1
                            except Exception as e:
                                logger.error(f"[autonomous_scan] delete failed: {e}")

                        elif decision.action == ActionType.TIMEOUT:
                            member = getattr(msg_obj.guild, "get_member", lambda x: None)(int(event.user_id))
                            if member:
                                try:
                                    effective_timeout = self.policy.get_effective_timeout_minutes()
                                    await self.actions.timeout_member(
                                        member,
                                        duration_minutes=effective_timeout,
                                        reason=f"Azure autonomous scan: {decision.explanation}",
                                    )
                                    action_count += 1
                                except Exception as e:
                                    logger.error(f"[autonomous_scan] timeout failed: {e}")


        report["action_taken"] = action_count > 0
        report["actions_executed"] = action_count

        # Send report to admin
        self.reporter.report(ActionReport(
            timestamp=time.time(),
            action_type=decision.action.value,
            target_user_id=", ".join(temporal_signals.involved_users)[:100],
            target_user_name="situation",
            target_message_id=None,
            channel_id=", ".join(temporal_signals.involved_channels)[:100],
            channel_name="multiple",
            severity="high" if temporal_signals.is_raid else "medium",
            category="raid" if temporal_signals.is_raid else "coordination",
            reason=decision.explanation,
            confidence=decision.confidence,
            dry_run=self.policy.is_dry_run(),
            message_content="",
        ))

        return report

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    async def _execute_action(self, action_type: ActionType, message, member, report: ActionReport):
        """Execute the action on Discord, respecting phase limits."""
        # Final safety check: is this action allowed?
        if not action_allowed(self.policy.phase, action_type.value):
            logger.info(f"[engine] BLOCKED: {action_type.value} not allowed in phase {self.policy.phase.value}")

            return

        channel = getattr(message, "channel", None)
        result = self.actions.execute(
            action_type=action_type,
            message=message,
            member=member,
            channel=channel,
            reason=report.reason,
        )

        if not result.success:
            logger.error(f"[engine] action failed: {result.error}")

            return

        # Async execution for actions that need it
        if action_type == ActionType.DELETE:
            await self.actions.delete_message(message, reason=report.reason)
        elif action_type == ActionType.TIMEOUT:
            # Clamp timeout duration to phase maximum
            effective_timeout = self.policy.get_effective_timeout_minutes()
            if effective_timeout > 0:
                await self.actions.timeout_member(
                    member,
                    duration_minutes=effective_timeout,
                    reason=report.reason,
                )
        elif action_type == ActionType.KICK:
            if action_allowed(self.policy.phase, "kick"):
                await self.actions.kick_member(member, reason=report.reason)
        elif action_type == ActionType.BAN:
            if action_allowed(self.policy.phase, "ban"):
                await self.actions.ban_member(member, reason=report.reason)
        elif action_type == ActionType.WARN:
            await self.actions.warn_member(member, report.reason, channel=channel)

    # ------------------------------------------------------------------
    # Offense tracking
    # ------------------------------------------------------------------

    def _record_offense(self, user_id: str):
        now = time.time()
        if user_id not in self._user_offense_history:
            self._user_offense_history[user_id] = []
        self._user_offense_history[user_id].append(now)
        # Clean old entries
        window = self.policy.escalation_window_minutes * 60
        self._user_offense_history[user_id] = [
            t for t in self._user_offense_history[user_id] if now - t < window
        ]

    def _count_recent_offenses(self, user_id: str, minutes: int) -> int:
        now = time.time()
        window = minutes * 60
        if user_id not in self._user_offense_history:
            return 0
        return sum(1 for t in self._user_offense_history[user_id] if now - t < window)

    # ------------------------------------------------------------------
    # Stats & control
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return moderation stats."""
        action_stats = self.actions.get_stats()
        report_stats = self.reporter.get_summary(hours=24)
        return {
            "actions_taken": action_stats,
            "reports_24h": report_stats,
            "cache_size": self.scanner.cache_size(),
            "phase": self.policy.phase.value,
            "mode": self.policy.mode,
            "dry_run": self.policy.is_dry_run(),
        }

    def get_readiness_report(self, hours: int = 72) -> dict:
        """Generate a readiness report for phase escalation."""
        return self.monitor.generate_readiness_report(hours=hours)

    def get_readiness_text(self, hours: int = 72) -> str:
        """Human-readable readiness report."""
        return self.monitor.get_summary_text(hours=hours)

    def add_feedback(self, message_id: str, feedback: str, by: str):
        """Human feedback on a classification."""
        self.monitor.add_feedback(message_id, feedback, by)

    def set_mode(self, mode: str):
        """Switch mode: 'dry_run', 'reactive', 'proactive'."""
        self.policy.mode = mode
        logger.info(f"[moderation] mode set to: {mode}")


    def set_phase(self, phase_name: str):
        """Switch phase with validation. Supports bidirectional transitions for emergency rollback."""
        from .phase import ModerationPhase
        phase_map = {
            "dry_run": ModerationPhase.DRY_RUN,
            "reactive_limited": ModerationPhase.REACTIVE_LIMITED,
            "reactive_full": ModerationPhase.REACTIVE_FULL,
        }
        new_phase = phase_map.get(phase_name.lower())
        if not new_phase:
            raise ValueError(f"Unknown phase: {phase_name}. Use: dry_run, reactive_limited, reactive_full")

        if not self.policy.can_transition_to(new_phase):
            valid_next = {
                ModerationPhase.DRY_RUN: ["reactive_limited"],
                ModerationPhase.REACTIVE_LIMITED: ["dry_run", "reactive_full"],
                ModerationPhase.REACTIVE_FULL: ["dry_run", "reactive_limited"],
            }.get(self.policy.phase, [])
            raise ValueError(
                f"Cannot transition from {self.policy.phase.value} to {new_phase.value}. "
                f"Valid transitions from {self.policy.phase.value}: {valid_next}"
            )

        self.policy.phase = new_phase
        # Sync mode for backward compatibility
        if new_phase == ModerationPhase.DRY_RUN:
            self.policy.mode = "dry_run"
        else:
            self.policy.mode = "reactive"
        logger.info(f"[moderation] phase set to: {new_phase.value} ({self.policy.get_phase_description()})")


    def emergency_stop(self):
        """
        Emergency kill switch.
        Immediately:
          - Force phase to DRY_RUN
          - Stop all actions (actions become dry_run)
          - Flush pending reports

        This is the safest state. Use it when something goes wrong.
        """
        old_phase = self.policy.phase.value
        self.policy.phase = ModerationPhase.DRY_RUN
        self.policy.mode = "dry_run"
        self.flush_reports()
        logger.info(f"[EMERGENCY STOP] Phase forced from {old_phase} to dry_run. All actions disabled.")

        logger.info("[EMERGENCY STOP] To resume, use set_phase('reactive_limited') or set_phase('reactive_full').")


    def flush_reports(self):
        """Force-send any pending reports."""
        self.reporter.flush()
