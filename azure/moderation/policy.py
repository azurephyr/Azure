"""
Azure Moderation: Policy Engine

Defines the severity-to-action mapping and per-server configuration.

Policy is the "constitution" of the moderation engine. It answers:
  - What severity triggers what action?
  - Which actions require human confirmation?
  - What are the escalation rules?
  - What channels are exempt?

Modes:
  dry_run    -> classify only, log, NO actions taken
  reactive   -> act on real-time messages only
  proactive  -> periodic scans + reactive
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .phase import ModerationPhase, action_allowed, can_transition, max_timeout_minutes


class ActionType(Enum):
    NONE = "none"
    LOG = "log"
    WARN = "warn"
    DELETE = "delete"
    TIMEOUT = "timeout"
    KICK = "kick"
    BAN = "ban"
    REPORT = "report"


@dataclass
class ModerationPolicy:
    """
    Configurable moderation policy for a Discord server.

    Supports phased permission escalation:
      dry_run          -> classify only, no actions
      reactive_limited -> delete, warn, short timeout (≤5 min)
      reactive_full    -> all actions including kick, ban, lockdown

    All fields have sensible defaults. Override per-server as needed.
    """
    # Phase (trust escalation)
    phase: ModerationPhase = field(default=ModerationPhase.DRY_RUN)
    # Legacy mode string (kept for compatibility, but phase takes precedence)
    mode: str = "dry_run"   # "dry_run" | "reactive" | "proactive"

    # Action thresholds (severity -> action)
    # LOW: just log, no action
    # MEDIUM: warn the user (DM or reply)
    # HIGH: delete message + timeout user
    # CRITICAL: delete + ban + report to admin channel
    low_action: ActionType = ActionType.LOG
    medium_action: ActionType = ActionType.WARN
    high_action: ActionType = ActionType.TIMEOUT
    critical_action: ActionType = ActionType.BAN

    # Escalation: repeated offenses from same user
    escalation_enabled: bool = True
    escalation_window_minutes: int = 60
    max_warnings_before_timeout: int = 2
    max_timeouts_before_kick: int = 2
    max_kicks_before_ban: int = 1

    # Timeouts (in minutes) — clamped by phase max_timeout_minutes()
    timeout_duration_minutes: int = 5

    # Proactive scanning
    proactive_scan_interval_seconds: int = 300   # 5 minutes
    proactive_scan_lookback_minutes: int = 30

    # Exemptions
    exempt_roles: list[str] = field(default_factory=list)  # role names or IDs
    exempt_channels: list[str] = field(default_factory=list)  # channel names or IDs
    exempt_users: list[str] = field(default_factory=list)      # user IDs

    # Whitelist (always exempt, even if they trigger rule-based signals)
    exempt_owner: bool = True          # never moderate the server owner
    exempt_admins: bool = True       # never moderate users with Administrator permission
    exempt_bots: bool = True         # never moderate bot accounts
    exempt_trusted_roles: list[str] = field(default_factory=list)  # role names/IDs that are always safe

    # Limits (safety rails)
    max_actions_per_minute: int = 10
    max_deletions_per_minute: int = 20
    max_bans_per_hour: int = 3
    max_timeouts_per_hour: int = 10

    # Reporting
    admin_report_channel: str | None = None  # channel ID or name for admin reports
    report_format: str = "embed"  # "embed" | "text"
    report_aggregated: bool = True  # batch reports every N minutes instead of instant
    report_interval_seconds: int = 60

    # Confirmation (phase-aware: kicks/bans require confirmation in limited mode)
    require_confirmation_for_ban: bool = True
    require_confirmation_for_kick: bool = False
    confirmation_mode: str = "destructive"  # "none" | "destructive" | "all"
    confirmation_threshold: float = 0.75

    # Classification tuning
    spam_score_threshold: float = 0.6
    scam_score_threshold: float = 0.5
    toxicity_score_threshold: float = 0.6

    def get_action_for(self, severity_name: str) -> ActionType:
        """Map severity string to action type, respecting phase boundaries."""
        mapping = {
            "low": self.low_action,
            "medium": self.medium_action,
            "high": self.high_action,
            "critical": self.critical_action,
        }
        action = mapping.get(severity_name.lower(), ActionType.NONE)
        return self._clamp_action(action)

    def _clamp_action(self, action: ActionType) -> ActionType:
        """Clamp action to phase-permitted actions with intelligent degradation.

        If the requested action (e.g. BAN) is not allowed in the current phase,
        degrade to the highest severity action that IS allowed.
        Example: critical -> BAN (not allowed in limited) -> KICK (not allowed) -> TIMEOUT (allowed)
        """
        if action == ActionType.NONE:
            return ActionType.NONE
        if action_allowed(self.phase, action.value):
            return action

        # Degradation hierarchy: most severe to least severe
        hierarchy = [
            ActionType.BAN,
            ActionType.KICK,
            ActionType.TIMEOUT,
            ActionType.WARN,
            ActionType.DELETE,
            ActionType.LOG,
        ]
        for fallback in hierarchy:
            if action_allowed(self.phase, fallback.value):
                return fallback
        return ActionType.NONE

    def get_effective_timeout_minutes(self) -> int:
        """Return the effective timeout duration, clamped by phase."""
        phase_max = max_timeout_minutes(self.phase)
        return min(self.timeout_duration_minutes, phase_max)

    def is_exempt_user(self, user_id: str) -> bool:
        return user_id in self.exempt_users

    def is_exempt_channel(self, channel_id: str) -> bool:
        return channel_id in self.exempt_channels

    def is_exempt_role(self, role_name: str) -> bool:
        return role_name in self.exempt_roles

    def is_whitelisted(self, member=None) -> bool:
        """
        Check if a Discord member is whitelisted (never to be moderated).
        Checks: owner, administrator, bot, trusted roles.

        member: discord.Member object (or None, in which case returns False)
        """
        if member is None:
            return False

        # Defensive: objects that lack a guild (e.g., DM-context User) are
        # not subject to moderation — but they are also not whitelisted in
        # the sense used elsewhere (further checks assume we have a guild).
        guild = getattr(member, "guild", None)
        if guild is None:
            return False

        # Bot accounts
        if self.exempt_bots and getattr(member, "bot", False):
            return True

        # Server owner
        if self.exempt_owner and getattr(guild, "owner_id", None) == getattr(member, "id", None):
            return True

        # Administrator permission
        if self.exempt_admins:
            perms = getattr(member, "guild_permissions", None)
            if perms and getattr(perms, "administrator", False):
                return True

        # Trusted roles
        if self.exempt_trusted_roles:
            for role in getattr(member, "roles", []):
                role_name = str(getattr(role, "name", ""))
                role_id = str(getattr(role, "id", ""))
                if role_name in self.exempt_trusted_roles or role_id in self.exempt_trusted_roles:
                    return True

        return False

    def should_report(self) -> bool:
        return self.admin_report_channel is not None

    def is_dry_run(self) -> bool:
        return self.phase == ModerationPhase.DRY_RUN or self.mode == "dry_run"

    def can_transition_to(self, new_phase: ModerationPhase) -> bool:
        return can_transition(self.phase, new_phase)

    def get_phase_description(self) -> str:
        return {
            ModerationPhase.DRY_RUN: "Dry Run — classify only, no actions taken",
            ModerationPhase.REACTIVE_LIMITED: "Reactive Limited — delete, warn, short timeout (≤5 min) only",
            ModerationPhase.REACTIVE_FULL: "Reactive Full — all moderation actions enabled",
        }.get(self.phase, "Unknown")

