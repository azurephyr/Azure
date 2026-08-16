"""Feature flags for optional Azure subsystems.

Core path (Discord + LLM chat + dry-run moderation + slash commands) is always on.
Everything else is opt-in via env so the bot stays reliable by default.

Flags (all accept 1/true/yes/on):
  AZURE_FEATURE_COGNITIVE      10-phase cognitive pipeline + goal loops
  AZURE_FEATURE_LIVE_INTEL     LiveIntelligence + related prefix commands
  AZURE_FEATURE_VOICE          Voice system
  AZURE_FEATURE_PLUGINS        Plugin manager
  AZURE_FEATURE_VISION         Vision processor
  AZURE_FEATURE_GAMES          Game master
  AZURE_FEATURE_INTEGRATIONS   External integration hub
  AZURE_FEATURE_PROACTIVE      Proactive engine / welcome suggestions
  AZURE_FEATURE_JARVIS         JARVIS integration layer
  AZURE_FEATURE_WEB            Web dashboard (lifecycle still respects this)
  AZURE_FEATURE_HEALTH         Health HTTP server
  AZURE_FEATURE_AUTONOMOUS     Autonomous agent / goal executor loops
  AZURE_FEATURE_REVIVAL        Dead chat revival background loop
  AZURE_FEATURE_GHOST_LOOP     Ghost mute maintenance loop
  AZURE_FEATURE_CRON           Cron/scheduled task loop

Legacy:
  AZURE_COGNITIVE_MODE=1 still enables cognitive (same as FEATURE_COGNITIVE).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class FeatureFlags:
    cognitive: bool = False
    live_intel: bool = False
    voice: bool = False
    plugins: bool = False
    vision: bool = False
    games: bool = False
    integrations: bool = False
    proactive: bool = False
    jarvis: bool = False
    web: bool = True
    health: bool = True
    autonomous: bool = False
    revival: bool = True
    ghost_loop: bool = True
    cron: bool = True

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)

    def enabled_names(self) -> list[str]:
        return [k for k, v in self.as_dict().items() if v]


def load_feature_flags() -> FeatureFlags:
    """Load flags from environment with safe production defaults."""
    # Cognitive: either explicit feature flag OR legacy AZURE_COGNITIVE_MODE
    cognitive = _flag("AZURE_FEATURE_COGNITIVE", False) or _flag("AZURE_COGNITIVE_MODE", False)
    return FeatureFlags(
        cognitive=cognitive,
        live_intel=_flag("AZURE_FEATURE_LIVE_INTEL", False),
        voice=_flag("AZURE_FEATURE_VOICE", False),
        plugins=_flag("AZURE_FEATURE_PLUGINS", False),
        vision=_flag("AZURE_FEATURE_VISION", False),
        games=_flag("AZURE_FEATURE_GAMES", False),
        integrations=_flag("AZURE_FEATURE_INTEGRATIONS", False),
        proactive=_flag("AZURE_FEATURE_PROACTIVE", False) or cognitive,
        jarvis=_flag("AZURE_FEATURE_JARVIS", False),
        web=_flag("AZURE_FEATURE_WEB", True),
        health=_flag("AZURE_FEATURE_HEALTH", True),
        autonomous=_flag("AZURE_FEATURE_AUTONOMOUS", False) or cognitive,
        revival=_flag("AZURE_FEATURE_REVIVAL", True),
        ghost_loop=_flag("AZURE_FEATURE_GHOST_LOOP", True),
        cron=_flag("AZURE_FEATURE_CRON", True),
    )
