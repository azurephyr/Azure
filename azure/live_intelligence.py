"""
Azure Live Intelligence Master Integration

This module ties together all live monitoring systems into a unified interface:
- ServerAwarenessEngine (event tracking)
- ModerationIntelligence (violation detection)
- ProactiveInsights (helpful suggestions)
- AutoModeration (action execution)

Provides a single entry point for the Discord bot to interact with
the entire live intelligence system.

Usage in discord_bot_v1.py:
    intelligence = LiveIntelligence(bot, llm, memory_backend)
    await intelligence.initialize()

    # On every message
    await intelligence.on_message(message)

    # Periodic proactive checks (every 30 min)
    suggestions = await intelligence.generate_suggestions(guild)

    # Query current state
    insights = intelligence.get_server_insights(guild_id)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from azure.auto_moderation import AutoModConfig, AutoModeration
from azure.live_awareness import ServerAwarenessEngine, ServerInsights
from azure.moderation_intelligence import ModerationIntelligence, ThreatLevel
from azure.proactive_insights import ProactiveInsights, Suggestion

logger = logging.getLogger("azure.live_intelligence")


class LiveIntelligence:
    """
    Master orchestrator for all live intelligence systems.

    This is the single interface the Discord bot needs to interact with
    for all monitoring, moderation, and proactive features.
    """

    def __init__(self, bot, llm=None, memory_backend=None,
                 config: dict | None = None,
                 log_dir: Path = None):
        """
        Initialize live intelligence system.

        Args:
            bot: Discord bot instance
            llm: Optional local LLM for semantic analysis
            memory_backend: Memory backend for persistence
            config: Configuration dict (optional)
            log_dir: Directory for logs
        """
        self.bot = bot
        self.llm = llm
        self.memory = memory_backend
        self.config = config or {}
        self.log_dir = log_dir or Path("logs")

        # Initialize subsystems
        logger.info("[live_intel] Initializing subsystems...")

        # 1. Awareness (tracks all events)
        self.awareness = ServerAwarenessEngine(
            memory_backend=memory_backend,
            max_events_memory=self.config.get("max_events_memory", 10000)
        )

        # 2. Moderation Intelligence (detects violations)
        self.moderation = ModerationIntelligence(
            llm=llm,
            awareness_engine=self.awareness,
            strict_mode=self.config.get("strict_moderation", False)
        )

        # 3. Proactive Insights (generates suggestions)
        self.insights = ProactiveInsights(
            llm=llm,
            awareness_engine=self.awareness
        )

        # 4. Auto Moderation (executes actions)
        auto_mod_config = AutoModConfig(
            enabled=self.config.get("auto_mod_enabled", True),
            dry_run=self.config.get("auto_mod_dry_run", False),
            auto_delete_threshold=self.config.get("auto_delete_threshold", 0.6),
            auto_warn_threshold=self.config.get("auto_warn_threshold", 0.7),
            auto_timeout_threshold=self.config.get("auto_timeout_threshold", 0.8),
            never_auto_kick=self.config.get("never_auto_kick", True),
            never_auto_ban=self.config.get("never_auto_ban", True),
            notify_admins_on_timeout=self.config.get("notify_on_timeout", True),
            notify_admins_on_kick=self.config.get("notify_on_kick", True),
            notify_admins_on_ban=self.config.get("notify_on_ban", True),
            admin_channel_id=self.config.get("admin_channel_id"),
        )

        self.auto_mod = AutoModeration(
            bot=bot,
            awareness_engine=self.awareness,
            mod_intelligence=self.moderation,
            config=auto_mod_config,
            log_dir=self.log_dir
        )

        # Background tasks
        self._cleanup_task: asyncio.Task | None = None
        self._proactive_task: asyncio.Task | None = None

        logger.info("[live_intel] ✅ All subsystems initialized")

    async def initialize(self):
        """Start background tasks."""
        # Start cleanup task (hourly)
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        logger.info("[live_intel] ✅ Background tasks started")

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("[live_intel] Shutting down...")

        if self._cleanup_task:
            self._cleanup_task.cancel()

        if self._proactive_task:
            self._proactive_task.cancel()

        logger.info("[live_intel] ✅ Shutdown complete")

    # -------------------------------------------------------------------------
    # Event Handlers (called by Discord bot)
    # -------------------------------------------------------------------------

    async def on_message(self, message) -> dict:
        """
        Process a message through the full intelligence pipeline.

        Returns dict with:
            - awareness_event: ServerEvent
            - moderation_result: ModerationResult (if analyzed)
            - action_taken: ModerationAction (if action executed)
        """
        result = {}

        # Skip bot messages
        if message.author.bot:
            return result

        try:
            # 1. Track in awareness system
            event = await self.awareness.on_message(message)
            result["awareness_event"] = event

            # 2. Get user activity for context
            user_activity = None
            if message.guild:
                guild_id = str(message.guild.id)
                user_id = str(message.author.id)
                user_activity = self.awareness.get_user_activity(guild_id, user_id)

            # 3. Analyze for violations
            mod_result = await self.moderation.analyze_message(message, user_activity)
            result["moderation_result"] = mod_result

            # 4. Take action if needed
            if mod_result.threat_level != ThreatLevel.INFO and message.guild:
                action = await self.auto_mod.process_violation(message, mod_result)
                if action:
                    result["action_taken"] = action

                    # Update user trust score based on violation
                    if user_activity:
                        # Decrease trust score
                        severity_penalty = {
                            ThreatLevel.WARNING: 5,
                            ThreatLevel.DANGEROUS: 15,
                            ThreatLevel.CRITICAL: 30,
                        }
                        penalty = severity_penalty.get(mod_result.threat_level, 0)
                        user_activity.trust_score = max(0.0, user_activity.trust_score - penalty)

        except Exception as e:
            logger.error(f"[live_intel] Error processing message: {e}")

        return result

    async def on_message_edit(self, before, after):
        """Track message edits."""
        try:
            await self.awareness.on_message_edit(before, after)
        except Exception as e:
            logger.error(f"[live_intel] Error on_message_edit: {e}")

    async def on_message_delete(self, message):
        """Track message deletions."""
        try:
            await self.awareness.on_message_delete(message)
        except Exception as e:
            logger.error(f"[live_intel] Error on_message_delete: {e}")

    async def on_reaction_add(self, reaction, user):
        """Track reactions."""
        try:
            await self.awareness.on_reaction_add(reaction, user)
        except Exception as e:
            logger.error(f"[live_intel] Error on_reaction_add: {e}")

    async def on_member_join(self, member):
        """Track new members."""
        try:
            await self.awareness.on_member_join(member)

            # Check for potential raid
            guild_id = str(member.guild.id)
            raid_info = await self.moderation.detect_raid(guild_id)
            if raid_info and raid_info.get("raid_detected"):
                logger.critical(f"[live_intel] RAID DETECTED in {member.guild.name}: {raid_info}")

        except Exception as e:
            logger.error(f"[live_intel] Error on_member_join: {e}")

    async def on_member_leave(self, member):
        """Track members leaving."""
        try:
            await self.awareness.on_member_leave(member)
        except Exception as e:
            logger.error(f"[live_intel] Error on_member_leave: {e}")

    async def on_voice_state_update(self, member, before, after):
        """Track voice activity."""
        try:
            await self.awareness.on_voice_state_update(member, before, after)
        except Exception as e:
            logger.error(f"[live_intel] Error on_voice_state_update: {e}")

    # -------------------------------------------------------------------------
    # Query Interface
    # -------------------------------------------------------------------------

    def get_server_insights(self, guild_id: str) -> ServerInsights:
        """Get real-time server insights."""
        return self.awareness.get_server_insights(guild_id)

    def get_user_activity(self, guild_id: str, user_id: str):
        """Get user activity data."""
        return self.awareness.get_user_activity(guild_id, user_id)

    async def generate_suggestions(self, guild, max_suggestions: int = 5) -> list[Suggestion]:
        """Generate proactive suggestions for a server."""
        guild_id = str(guild.id)
        return await self.insights.generate_suggestions(guild_id, guild, max_suggestions)

    def get_moderation_stats(self) -> dict:
        """Get moderation statistics."""
        return {
            "detection": self.moderation.get_stats(),
            "execution": self.auto_mod.get_statistics(),
        }

    def get_user_moderation_history(self, user_id: str):
        """Get moderation history for a user."""
        return self.auto_mod.get_user_history(str(user_id))

    # -------------------------------------------------------------------------
    # Admin Commands Interface
    # -------------------------------------------------------------------------

    def record_suggestion_feedback(self, suggestion_id: str, helpful: bool, feedback: str = ""):
        """Record feedback on a proactive suggestion."""
        self.insights.record_feedback(suggestion_id, helpful, feedback)

    def record_moderation_feedback(self, message_id: str, correct: bool):
        """Record feedback on a moderation decision."""
        self.moderation.record_feedback(message_id, correct)

    async def export_analytics(self, guild_id: str, filepath: str):
        """Export analytics to file."""
        await self.awareness.export_analytics(guild_id, filepath)

    def set_admin_channel(self, channel_id: str):
        """Set admin notification channel."""
        self.auto_mod.config.admin_channel_id = channel_id
        logger.info(f"[live_intel] Admin channel set to {channel_id}")

    def configure_auto_mod(self, **kwargs):
        """Update auto-moderation config."""
        for key, value in kwargs.items():
            if hasattr(self.auto_mod.config, key):
                setattr(self.auto_mod.config, key, value)
                logger.info(f"[live_intel] Auto-mod config: {key} = {value}")

    # -------------------------------------------------------------------------
    # Background Tasks
    # -------------------------------------------------------------------------

    async def _cleanup_loop(self):
        """Periodic cleanup of old data."""
        while True:
            try:
                await asyncio.sleep(3600)  # 1 hour

                # Clean up old awareness data (72 hours)
                await self.awareness.cleanup_old_data(max_age_hours=72)

                # Reset hourly counters
                self.awareness.reset_hourly_counters()

                logger.info("[live_intel] Cleanup complete")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[live_intel] Cleanup error: {e}")

    # -------------------------------------------------------------------------
    # Diagnostic Commands
    # -------------------------------------------------------------------------

    def get_system_status(self) -> dict:
        """Get status of all subsystems."""
        status = {
            "awareness": {
                "enabled": True,
                "guilds_tracked": len(self.awareness.users),
                "total_events": sum(len(events) for events in self.awareness.events.values()),
            },
            "moderation": {
                "enabled": True,
                "llm_available": self.llm is not None,
                "strict_mode": self.moderation.strict_mode,
                "stats": self.moderation.get_stats().__dict__,
            },
            "auto_mod": {
                "enabled": self.auto_mod.config.enabled,
                "dry_run": self.auto_mod.config.dry_run,
                "stats": self.auto_mod.get_statistics(),
            },
            "insights": {
                "enabled": True,
                "llm_available": self.llm is not None,
                "stats": self.insights.get_statistics(),
            },
        }
        return status
