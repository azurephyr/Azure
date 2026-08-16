"""
Azure Moderation: Action Executor

Wraps Discord moderation actions with:
  - Permission checks (never try to act without permission)
  - Dry-run support (log what WOULD happen, don't do it)
  - Rate limiting (respect safety rails)
  - Audit logging (every action is recorded)

All methods return ActionResult for reporter integration.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .policy import ActionType, ModerationPolicy

logger = logging.getLogger("azure.moderation.actions")


@dataclass
class ActionResult:
    action: str
    target_user_id: str
    target_message_id: str | None
    channel_id: str
    success: bool
    reason: str = ""
    dry_run: bool = False
    timestamp: float = field(default_factory=time.time)
    error: str | None = None


class ActionExecutor:
    """
    Executes Discord moderation actions safely.

    Lazy-imports discord.py so the module can be imported and tested
    without discord.py installed.
    """

    def __init__(self, policy: ModerationPolicy, bot=None):
        self.policy = policy
        self.bot = bot  # discord.py Bot instance
        self._action_log: list[ActionResult] = []
        self._rate_limit_buckets: dict[str, list[float]] = {}

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _check_rate_limit(self, bucket: str, max_count: int, window_seconds: int) -> bool:
        """Return True if action is allowed within rate limit."""
        now = time.time()
        window = window_seconds
        if bucket not in self._rate_limit_buckets:
            self._rate_limit_buckets[bucket] = []
        entries = self._rate_limit_buckets[bucket]
        # Drop old entries
        entries = [t for t in entries if now - t < window]
        self._rate_limit_buckets[bucket] = entries
        if len(entries) >= max_count:
            return False
        entries.append(now)
        return True

    # ------------------------------------------------------------------
    # Core actions
    # ------------------------------------------------------------------

    def execute(self, action_type: ActionType, *,
                message=None, member=None, channel=None,
                reason: str = "Azure moderation") -> ActionResult:
        """
        Main entry point. Dispatches to the correct action handler.

        message: discord.Message (optional, for delete)
        member: discord.Member (optional, for timeout/kick/ban)
        channel: discord.TextChannel (for context)
        """
        dry = self.policy.is_dry_run()
        user_id = str(member.id) if member else "unknown"
        msg_id = str(message.id) if message else None
        ch_id = str(channel.id) if channel else "unknown"

        result = ActionResult(
            action=action_type.value,
            target_user_id=user_id,
            target_message_id=msg_id,
            channel_id=ch_id,
            success=False,
            dry_run=dry,
        )

        if dry:
            result.success = True
            result.reason = f"[DRY RUN] Would have performed: {action_type.value}"
            self._log(result)
            return result

        # Permission check helpers
        def _can_delete():
            if not message or not channel:
                return False
            return channel.permissions_for(channel.guild.me).manage_messages

        def _can_timeout():
            if not member:
                return False
            return member.guild.me.guild_permissions.moderate_members

        def _can_kick():
            if not member:
                return False
            return member.guild.me.guild_permissions.kick_members

        def _can_ban():
            if not member:
                return False
            return member.guild.me.guild_permissions.ban_members

        # Dispatch
        try:
            if action_type == ActionType.DELETE:
                if not _can_delete():
                    result.error = "missing manage_messages permission"
                elif not self._check_rate_limit("delete", self.policy.max_deletions_per_minute, 60):
                    result.error = "rate limited: max deletions per minute"
                else:
                    # Execute
                    if message:
                        # message.delete() is async; the caller must await it
                        result.reason = reason
                        result.success = True  # mark for async execution
            elif action_type == ActionType.TIMEOUT:
                if not _can_timeout():
                    result.error = "missing moderate_members permission"
                elif not self._check_rate_limit("timeout", self.policy.max_timeouts_per_hour, 3600):
                    result.error = "rate limited: max timeouts per hour"
                else:
                    result.reason = reason
                    result.success = True
            elif action_type == ActionType.KICK:
                if not _can_kick():
                    result.error = "missing kick_members permission"
                else:
                    result.reason = reason
                    result.success = True
            elif action_type == ActionType.BAN:
                if not _can_ban():
                    result.error = "missing ban_members permission"
                elif not self._check_rate_limit("ban", self.policy.max_bans_per_hour, 3600):
                    result.error = "rate limited: max bans per hour"
                else:
                    result.reason = reason
                    result.success = True
            elif action_type == ActionType.WARN or action_type == ActionType.LOG or action_type == ActionType.REPORT:
                result.reason = reason
                result.success = True

        except Exception as e:
            result.error = str(e)

        self._log(result)
        return result

    # ------------------------------------------------------------------
    # Async helpers (must be awaited by caller)
    # ------------------------------------------------------------------

    async def delete_message(self, message, reason: str = "Azure moderation"):
        """Actually delete a message. Must be awaited."""
        if not message:
            return False
        try:
            await message.delete(reason=reason)
            return True
        except Exception as e:
            logger.error(f"[moderation] delete failed: {e}")

            return False

    async def timeout_member(self, member, duration_minutes: int = 5, reason: str = "Azure moderation"):
        """Timeout a member. Must be awaited."""
        if not member:
            return False
        try:
            import datetime
            # Use a timezone-aware UTC datetime: discord.py >= 2.0 requires
            # aware datetimes for member.timeout(), and datetime.utcnow() is
            # deprecated in Python 3.12+ (naïve return value).
            until = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=duration_minutes)
            await member.timeout(until, reason=reason)
            return True
        except Exception as e:
            logger.error(f"[moderation] timeout failed: {e}")

            return False

    async def kick_member(self, member, reason: str = "Azure moderation"):
        if not member:
            return False
        try:
            await member.kick(reason=reason)
            return True
        except Exception as e:
            logger.error(f"[moderation] kick failed: {e}")

            return False

    async def ban_member(self, member, reason: str = "Azure moderation"):
        if not member:
            return False
        try:
            await member.ban(reason=reason, delete_message_days=1)
            return True
        except Exception as e:
            logger.error(f"[moderation] ban failed: {e}")

            return False

    async def warn_member(self, member, warning_text: str, channel=None):
        """Send a warning DM or reply."""
        try:
            await member.send(f"[Azure Moderation] Warning: {warning_text}")
            return True
        except Exception as e:
            logger.error(f"[moderation] warn DM failed, trying channel reply: {e}")

            # DM failed, try to reply in channel
            if channel:
                try:
                    await channel.send(f"{member.mention} Warning: {warning_text}")
                    return True
                except Exception as e2:
                    logger.error(f"[moderation] warn channel reply also failed: {e2}")

            return False

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(self, result: ActionResult):
        self._action_log.append(result)

    def get_logs(self, since: float = 0.0) -> list[ActionResult]:
        return [r for r in self._action_log if r.timestamp >= since]

    def get_stats(self) -> dict:
        """Return summary stats of actions taken."""
        counts = {}
        for r in self._action_log:
            if r.success:
                counts[r.action] = counts.get(r.action, 0) + 1
        return counts

    def clear_logs(self):
        self._action_log.clear()
        self._rate_limit_buckets.clear()
