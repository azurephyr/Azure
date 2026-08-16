"""Standalone moderation service -- transport-agnostic.

This service classifies messages and takes moderation actions.
It can be used by the Discord bot, a web API, or any other transport.

The service wraps the existing ModerationEngine without replacing it.
Transport-specific action handlers (Discord mute/kick/ban) are registered
at startup, so the core service has zero dependency on discord.py.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("azure.moderation_service")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ModerationReport:
    """Result of classifying a message."""

    user_id: str
    user_name: str
    guild_id: str
    channel_id: str
    message_id: str
    content: str
    action: str  # "allow", "warn", "mute", "ban", "kick", "delete", "timeout"
    confidence: float
    reason: str
    subsystem: str  # "toxicity", "spam", "raid", "jailbreak", "moderation_engine"
    details: dict = field(default_factory=dict)


@dataclass
class ModerationAction:
    """Action taken by the moderation system."""

    report: ModerationReport
    performed: bool
    result: str  # "success", "failed", "skipped"
    error: str = ""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ModerationService:
    """Transport-agnostic moderation service.

    Wraps the existing ``ModerationEngine`` and exposes a clean interface
    that any transport (Discord, web, CLI) can use.

    Usage::

        service = ModerationService(engine=agent.moderation)

        # Register transport-specific handlers (Discord example)
        service.register_action_handler("mute", discord_mute_handler)
        service.register_action_handler("kick", discord_kick_handler)

        # Classify a message
        report = await service.classify(message_data)
        if report.action != "allow":
            action = await service.take_action(report)

    The service never imports discord.py. All transport coupling lives
    in the registered action handlers.
    """

    def __init__(self, engine: Any | None = None):
        """
        Args:
            engine: An optional ``ModerationEngine`` instance.  When provided
                    the service delegates classification to the engine's
                    full intelligence pipeline.  When *None* the service
                    runs in standalone mode (classify returns "allow").
        """
        self._engine = engine
        self._action_handlers: dict[str, Callable] = {}
        self._enabled = True
        self._stats: dict[str, int] = {
            "classified": 0,
            "allowed": 0,
            "acted": 0,
            "failed": 0,
        }

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def register_action_handler(self, action: str, handler: Callable) -> None:
        """Register an async handler for a specific moderation action.

        ``handler`` must be an async callable that accepts a
        ``ModerationReport`` and performs the transport-specific work
        (e.g. Discord timeout, kick, ban).
        """
        self._action_handlers[action] = handler
        logger.debug("[moderation_service] registered handler for action=%s", action)

    def unregister_action_handler(self, action: str) -> None:
        """Remove a previously registered action handler.

        Args:
            action: The moderation action whose handler should be removed.
        """
        self._action_handlers.pop(action, None)

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    async def classify(self, message_data: dict) -> ModerationReport:
        """Classify a message for moderation.

        ``message_data`` keys:
            user_id, user_name, guild_id, channel_id, message_id, content

        When an engine is attached the message is run through the full
        Phase Alpha intelligence pipeline (classification, behavioral,
        temporal, risk, decision).  Otherwise the report is ``allow``.
        """
        self._stats["classified"] += 1

        # Build a minimal report for standalone / fallback mode
        report = ModerationReport(
            user_id=message_data.get("user_id", ""),
            user_name=message_data.get("user_name", ""),
            guild_id=message_data.get("guild_id", ""),
            channel_id=message_data.get("channel_id", ""),
            message_id=message_data.get("message_id", ""),
            content=message_data.get("content", ""),
            action="allow",
            confidence=0.0,
            reason="",
            subsystem="moderation_service",
        )

        if self._engine is None or not self._enabled:
            self._stats["allowed"] += 1
            return report

        # Delegate to the engine via a lightweight adapter that produces
        # a ModerationReport from the engine's ActionReport.
        try:
            action_report = await self._classify_with_engine(message_data)
            if action_report is not None:
                report = action_report
        except Exception as e:
            logger.error("[moderation_service] engine classification failed: %s", e)

        if report.action == "allow":
            self._stats["allowed"] += 1
        return report

    async def _classify_with_engine(self, message_data: dict) -> ModerationReport | None:
        """Run the engine's on_message pipeline using a synthetic message proxy.

        The engine expects a discord.Message-like object.  We build a
        minimal ``_ProxyMessage`` that satisfies the attributes the engine
        actually reads (author, guild, channel, content, id).
        """
        engine = self._engine
        if engine is None:
            return None

        class _Author:
            def __init__(self, uid: str, uname: str, bot: bool = False):
                self.id = int(uid) if uid and uid.isdigit() else 0
                self.display_name = uname
                self.name = uname
                self.bot = bot
                self.guild_permissions = type("_Perms", (), {"administrator": False})()
                self.created_at = None

        class _Channel:
            def __init__(self, cid: str, name: str = ""):
                self.id = int(cid) if cid and cid.isdigit() else 0
                self.name = name or str(cid)

        class _Guild:
            def __init__(self, gid: str, name: str = ""):
                self.id = int(gid) if gid and gid.isdigit() else 0
                self.name = name or str(gid)
                self.system_channel = None
                self.owner_id = 0
                self.members = []

        proxy = type("_Msg", (), {
            "author": _Author(
                message_data.get("user_id", "0"),
                message_data.get("user_name", "unknown"),
            ),
            "guild": _Guild(
                message_data.get("guild_id", "0"),
                message_data.get("guild_name", ""),
            ),
            "channel": _Channel(
                message_data.get("channel_id", "0"),
                message_data.get("channel_name", ""),
            ),
            "content": message_data.get("content", ""),
            "id": int(message_data.get("message_id", "0") or 0),
            "mentions": [],
            "attachments": [],
            "webhook_id": None,
        })()

        # The engine's on_message returns an ActionReport or None
        engine_report = await engine.on_message(proxy)
        if engine_report is None:
            return None

        return ModerationReport(
            user_id=message_data.get("user_id", ""),
            user_name=message_data.get("user_name", ""),
            guild_id=message_data.get("guild_id", ""),
            channel_id=message_data.get("channel_id", ""),
            message_id=message_data.get("message_id", ""),
            content=message_data.get("content", ""),
            action=getattr(engine_report, "action_type", "allow"),
            confidence=getattr(engine_report, "confidence", 0.0),
            reason=getattr(engine_report, "reason", ""),
            subsystem="moderation_engine",
            details={
                "severity": getattr(engine_report, "severity", ""),
                "category": getattr(engine_report, "category", ""),
                "dry_run": getattr(engine_report, "dry_run", True),
            },
        )

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    async def take_action(self, report: ModerationReport) -> ModerationAction:
        """Execute the moderation action from a report.

        Dispatches to the registered handler for ``report.action``.
        If no handler is registered the action is skipped.
        """
        if not self._enabled:
            return ModerationAction(report=report, performed=False, result="skipped")

        if report.action == "allow":
            return ModerationAction(report=report, performed=False, result="skipped")

        handler = self._action_handlers.get(report.action)
        if not handler:
            logger.warning("[moderation_service] no handler for action=%s", report.action)
            return ModerationAction(report=report, performed=False, result="skipped")

        try:
            await handler(report)
            self._stats["acted"] += 1
            return ModerationAction(report=report, performed=True, result="success")
        except Exception as e:
            logger.error("[moderation_service] action %s failed: %s", report.action, e)
            self._stats["failed"] += 1
            return ModerationAction(report=report, performed=False, result="failed", error=str(e))

    # ------------------------------------------------------------------
    # Engine delegation helpers
    # ------------------------------------------------------------------

    def set_phase(self, phase_name: str) -> None:
        """Delegate a phase change to the underlying moderation engine.

        Args:
            phase_name: Name of the moderation phase to activate.
        """
        if self._engine and hasattr(self._engine, "set_phase"):
            self._engine.set_phase(phase_name)

    def emergency_stop(self) -> None:
        """Trigger an emergency stop on the underlying moderation engine.

        Immediately halts all moderation processing.  Should only be used
        in critical failure scenarios.
        """
        if self._engine and hasattr(self._engine, "emergency_stop"):
            self._engine.emergency_stop()

    def get_stats(self) -> dict[str, Any]:
        """Return combined statistics from the service and its engine.

        Returns:
            Dictionary containing classification counts, action results,
            enabled state, registered handler names, and engine-specific
            stats if an engine is attached.
        """
        stats = dict(self._stats)
        stats["enabled"] = self._enabled
        stats["handlers"] = list(self._action_handlers.keys())
        if self._engine and hasattr(self._engine, "get_stats"):
            stats["engine"] = self._engine.get_stats()
        return stats

    def get_readiness_report(self, hours: int = 72) -> dict[str, Any]:
        """Return a readiness report from the underlying engine.

        Args:
            hours: Look-back window in hours for the readiness assessment.

        Returns:
            Dictionary with readiness data, or an error dict if no engine
            is attached.
        """
        if self._engine and hasattr(self._engine, "get_readiness_report"):
            return self._engine.get_readiness_report(hours=hours)
        return {"error": "no engine attached"}

    def add_feedback(self, message_id: str, verdict: str, by: str) -> None:
        """Submit human feedback on a moderation decision to the engine.

        Args:
            message_id: The ID of the moderated message.
            verdict: The feedback verdict (e.g. "correct", "incorrect").
            by: Identifier of the person providing feedback.
        """
        if self._engine and hasattr(self._engine, "add_feedback"):
            self._engine.add_feedback(message_id, verdict, by)

    @property
    def enabled(self) -> bool:
        """Whether the moderation service is currently active."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Enable or disable the moderation service.

        Args:
            value: True to enable, False to disable.
        """
        self._enabled = value

    @property
    def engine(self) -> Any:
        """The underlying moderation engine instance."""
        return self._engine

    @engine.setter
    def engine(self, value: Any) -> None:
        """Set the moderation engine instance.

        Args:
            value: A ModerationEngine instance or None.
        """
        self._engine = value
