"""Centralized bot context — single source of truth for all subsystems.

Import the module-level ``ctx`` instance. Never rely on discord_bot_v1 module
globals for runtime decisions; those exist only as thin legacy aliases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.features import FeatureFlags, load_feature_flags


@dataclass
class SubsystemStatus:
    """Health snapshot for one subsystem."""

    name: str
    loaded: bool = False
    ready: bool = False
    detail: str = ""


@dataclass
class BotContext:
    """Holds all bot subsystem references and runtime config."""

    # Core
    agent: Any = None
    task_manager: Any = None
    mgmt_tools: Any = None

    # Pipeline
    cognitive_pipeline: Any = None
    intent_classifier: Any = None
    tool_engine: Any = None

    # Infrastructure
    health_server: Any = None
    server_configs: Any = None
    bg_executor: Any = None

    # Moderation / repair
    repair: Any = None
    moderation_service: Any = None

    # Optional intelligence
    game_master: Any = None
    doc_intel: Any = None
    voice_system: Any = None
    channel_lifecycle: Any = None
    plugin_manager: Any = None
    integration_hub: Any = None
    vision_processor: Any = None
    proactive_engine: Any = None
    live_intelligence: Any = None

    # Scheduling
    cron_scheduler: Any = None

    # Model selection
    model_selector: Any = None

    # Discord
    bot: Any = None
    admin_channel: Any = None

    # Database
    db: Any = None

    # Config / state
    chat_mode: str = "anyone"
    allowed_user_ids: set = field(default_factory=set)
    cognitive_mode: bool = False
    cognitive_log_dir: Any = None
    start_time: Any = None
    features: FeatureFlags = field(default_factory=load_feature_flags)
    ready: bool = False
    discord_connected: bool = False
    shutting_down: bool = False
    last_error: str = ""

    def is_loaded(self, name: str) -> bool:
        """Check if a subsystem is loaded and not None."""
        return getattr(self, name, None) is not None

    def set_feature_flags(self, flags: FeatureFlags | None = None) -> None:
        self.features = flags or load_feature_flags()
        self.cognitive_mode = self.features.cognitive

    def mark_ready(self) -> None:
        self.ready = True
        self.last_error = ""

    def mark_failed(self, error: str) -> None:
        self.ready = False
        self.last_error = (error or "")[:500]

    def core_ready(self) -> bool:
        """True when static dependencies for the golden path are loaded."""
        return bool(
            self.bot is not None
            and self.agent is not None
            and getattr(self.agent, "llm", None) is not None
        )

    def runtime_ready(self) -> bool:
        """True only after Discord has connected and startup is complete."""
        return bool(
            self.ready
            and self.discord_connected
            and not self.shutting_down
            and self.core_ready()
            and not (self.bot is not None and getattr(self.bot, "is_closed", lambda: False)())
        )

    def subsystem_report(self) -> list[SubsystemStatus]:
        """Human-readable status of major subsystems."""
        specs = [
            ("agent", self.agent, "llm" if getattr(self.agent, "llm", None) else "no llm"),
            ("moderation_service", self.moderation_service, "engine-backed" if self.moderation_service else ""),
            ("model_selector", self.model_selector, ""),
            ("mgmt_tools", self.mgmt_tools, ""),
            ("tool_engine", self.tool_engine, ""),
            ("intent_classifier", self.intent_classifier, ""),
            ("cognitive_pipeline", self.cognitive_pipeline, "flag off" if not self.features.cognitive else ""),
            ("live_intelligence", self.live_intelligence, "flag off" if not self.features.live_intel else ""),
            ("health_server", self.health_server, "flag off" if not self.features.health else ""),
            ("cron_scheduler", self.cron_scheduler, ""),
            ("voice_system", self.voice_system, "flag off" if not self.features.voice else ""),
            ("plugin_manager", self.plugin_manager, "flag off" if not self.features.plugins else ""),
        ]
        out: list[SubsystemStatus] = []
        for name, obj, detail in specs:
            loaded = obj is not None
            ready = loaded
            if name == "agent" and loaded:
                ready = getattr(obj, "llm", None) is not None
            out.append(SubsystemStatus(name=name, loaded=loaded, ready=ready, detail=detail or ("ok" if ready else "missing")))
        return out

    def readiness_summary(self) -> dict[str, Any]:
        """Compact readiness dict for health/dashboard."""
        llm_info: dict[str, Any] = {}
        if self.agent is not None and hasattr(self.agent, "get_info"):
            try:
                info = self.agent.get_info() or {}
                llm_info = {
                    "mode": info.get("mode"),
                    "provider": (info.get("llm") or {}).get("provider") if isinstance(info.get("llm"), dict) else None,
                    "model": (info.get("llm") or {}).get("model") if isinstance(info.get("llm"), dict) else None,
                }
            except Exception as e:
                llm_info = {"error": str(e)[:120]}
        return {
            "ready": self.runtime_ready(),
            "core_ready": self.core_ready(),
            "discord_connected": self.discord_connected,
            "shutting_down": self.shutting_down,
            "chat_mode": self.chat_mode,
            "cognitive_mode": self.cognitive_mode,
            "features": self.features.as_dict() if self.features else {},
            "llm": llm_info,
            "subsystems": [
                {"name": s.name, "loaded": s.loaded, "ready": s.ready, "detail": s.detail}
                for s in self.subsystem_report()
            ],
            "last_error": self.last_error,
        }


# Module-level singleton
ctx = BotContext()
