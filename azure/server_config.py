"""
Azure Multi-Server Configuration

Per-server configuration for multi-guild support.
Each Discord server gets its own moderation policy, chat mode,
admin channel, confirmation settings, and exemptions.

This is essential for:
  - Bot listing sites (top.gg, discord.bots.gg, etc.)
  - Server owners wanting different moderation strictness
  - Testing vs. production servers

Usage:
    from azure.server_config import ServerConfigManager
    man = ServerConfigManager()
    cfg = man.get_or_create(guild_id=123)
    cfg.update(phase="reactive_limited", admin_channel_id="456")
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("azure.server_config")


@dataclass
class ServerConfig:
    """Per-server configuration."""
    guild_id: str
    guild_name: str = ""

    # Moderation
    moderation_phase: str = "dry_run"  # dry_run | reactive_limited | reactive_full
    admin_channel_id: str = ""
    confirmation_mode: str = "destructive"  # none | destructive | all
    confirmation_threshold: float = 0.75

    # Chat
    chat_mode: str = "anyone"  # anyone | owner_only | specific_users | dm_only | mention_only
    allowed_users: list[str] = field(default_factory=list)

    # Exemptions
    exempt_channels: list[str] = field(default_factory=list)
    exempt_users: list[str] = field(default_factory=list)
    exempt_roles: list[str] = field(default_factory=list)
    trusted_roles: list[str] = field(default_factory=list)

    # Limits
    max_timeouts_per_hour: int = 10
    max_bans_per_hour: int = 3
    max_deletions_per_minute: int = 20

    # Metadata
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    joined_at: float = 0.0

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, data: dict) -> ServerConfig:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class ServerConfigManager:
    """
    Manages per-server configurations with JSON persistence.
    Thread-safe for the config store.
    """

    def __init__(self, config_dir: Path | None = None):
        if config_dir is None:
            # Default config dir is PROJECT_ROOT/configs
            project_root = Path(__file__).resolve().parent.parent
            config_dir = project_root / "configs"
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, ServerConfig] = {}
        self._load_all()

    def _config_path(self, guild_id: str) -> Path:
        return self.config_dir / f"guild_{guild_id}.json"

    def _load_all(self):
        """Pre-load all server configs into cache."""
        for f in self.config_dir.glob("guild_*.json"):
            try:
                guild_id = f.stem.replace("guild_", "")
                data = json.loads(f.read_text(encoding="utf-8"))
                self._cache[guild_id] = ServerConfig.from_dict(data)
            except Exception as e:
                logger.error(f"[server_config] failed to load {f.name}: {e}")


    def get(self, guild_id: str) -> ServerConfig | None:
        """Get config for a guild, or None if not configured."""
        return self._cache.get(guild_id)

    def get_or_create(self, guild_id: str, guild_name: str = "") -> ServerConfig:
        """Get existing config or create a new one with defaults."""
        if guild_id in self._cache:
            return self._cache[guild_id]
        cfg = ServerConfig(guild_id=guild_id, guild_name=guild_name, joined_at=time.time())
        self._cache[guild_id] = cfg
        self._save(cfg)
        logger.info(f"[server_config] created config for {guild_name or guild_id}")

        return cfg

    def update(self, guild_id: str, **kwargs) -> ServerConfig:
        """Update fields on a server config. Creates if doesn't exist."""
        cfg = self.get_or_create(guild_id)
        for key, value in kwargs.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        cfg.updated_at = time.time()
        self._save(cfg)
        return cfg

    def remove(self, guild_id: str):
        """Remove a server's config when the bot leaves."""
        if guild_id in self._cache:
            del self._cache[guild_id]
        path = self._config_path(guild_id)
        if path.exists():
            path.unlink()
            logger.info(f"[server_config] removed config for guild {guild_id}")


    def list_all(self) -> list[dict]:
        """Return summary of all configured servers."""
        return [
            {
                "guild_id": c.guild_id,
                "guild_name": c.guild_name,
                "phase": c.moderation_phase,
                "joined_at": c.joined_at,
            }
            for c in self._cache.values()
        ]

    def count(self) -> int:
        return len(self._cache)

    def _save(self, cfg: ServerConfig):
        path = self._config_path(cfg.guild_id)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(
            json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def apply_policy(self, guild_id: str, policy) -> None:
        """Apply saved config to a ModerationPolicy instance."""
        cfg = self.get(guild_id)
        if not cfg:
            return

        # Map phase string to ModerationPhase enum
        from .moderation.phase import ModerationPhase
        phase_map = {
            "dry_run": ModerationPhase.DRY_RUN,
            "reactive_limited": ModerationPhase.REACTIVE_LIMITED,
            "reactive_full": ModerationPhase.REACTIVE_FULL,
        }
        policy.phase = phase_map.get(cfg.moderation_phase, ModerationPhase.DRY_RUN)
        policy.mode = "dry_run" if policy.phase == ModerationPhase.DRY_RUN else "reactive"
        policy.admin_report_channel = cfg.admin_channel_id
        policy.exempt_channels = cfg.exempt_channels
        policy.exempt_users = cfg.exempt_users
        policy.exempt_roles = cfg.exempt_roles
        policy.exempt_trusted_roles = cfg.trusted_roles
        policy.max_timeouts_per_hour = cfg.max_timeouts_per_hour
        policy.max_bans_per_hour = cfg.max_bans_per_hour
        policy.max_deletions_per_minute = cfg.max_deletions_per_minute
