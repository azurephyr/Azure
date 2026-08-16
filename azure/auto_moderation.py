"""
Azure Auto-Moderation System

Graduated response system that executes moderation actions automatically.

Response Ladder (escalates based on severity and history):
1. INFO → No action (just log)
2. WARNING → Delete message + DM warning
3. DANGEROUS → Timeout (5min → 1hr → 1day) + notify admins
4. CRITICAL → Kick/Ban + notify admins + log evidence

Features:
- Graduated responses (warnings before bans)
- Configurable thresholds and cooldowns
- Admin approval for serious actions (optional)
- Action logging with evidence
- Appeal system
- Undo functionality
- Rate limiting (prevent runaway bans)

Safety:
- Dry-run mode (test without taking action)
- Admin override always available
- All actions logged with full context
- Cannot target server owner or admins
- Rate limits prevent mass actions

Usage:
    auto_mod = AutoModeration(bot, awareness, mod_intel)
    await auto_mod.process_violation(message, moderation_result)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

try:
    import discord
except ImportError:  # Allow module to import without discord.py installed
    discord = None  # type: ignore[assignment]

import contextlib

from .moderation_intelligence import ViolationType

logger = logging.getLogger("azure.auto_moderation")


class ActionType(Enum):
    """Types of moderation actions."""
    DELETE = "delete"
    WARN = "warn"
    TIMEOUT = "timeout"
    KICK = "kick"
    BAN = "ban"
    ALERT_SUPPORT = "alert_support"  # For self-harm cases


@dataclass
class ModerationAction:
    """A single moderation action taken."""
    action_id: str
    action_type: ActionType
    timestamp: float

    # Target
    user_id: str
    user_name: str
    guild_id: str

    # Context
    message_id: str
    message_content: str
    channel_id: str
    violation_type: str
    threat_level: str
    confidence: float

    # Execution
    executed: bool = False
    execution_time: float = 0.0
    error: str = ""

    # Evidence
    evidence: list[str] = field(default_factory=list)
    moderator: str = "auto"  # "auto" or admin name

    # Appeal
    can_appeal: bool = True
    appealed: bool = False
    appeal_reason: str = ""
    appeal_approved: bool | None = None


@dataclass
class AutoModConfig:
    """Configuration for auto-moderation."""
    enabled: bool = True
    dry_run: bool = False  # If True, log but don't execute

    # Auto-execute thresholds
    auto_delete_threshold: float = 0.6  # Delete if confidence > this
    auto_warn_threshold: float = 0.7
    auto_timeout_threshold: float = 0.8

    # Never auto-execute (always require admin approval)
    never_auto_kick: bool = True
    never_auto_ban: bool = True

    # Timeout durations (seconds)
    timeout_first: int = 300  # 5 minutes
    timeout_second: int = 3600  # 1 hour
    timeout_third: int = 86400  # 1 day

    # Rate limits (prevent runaway moderation)
    max_actions_per_minute: int = 10
    max_timeouts_per_hour: int = 20
    max_kicks_per_hour: int = 5
    max_bans_per_hour: int = 3

    # Cooldowns (time before action can be repeated on same user)
    warn_cooldown: int = 3600  # 1 hour
    timeout_cooldown: int = 7200  # 2 hours

    # Admin notification
    notify_admins_on_timeout: bool = True
    notify_admins_on_kick: bool = True
    notify_admins_on_ban: bool = True
    admin_channel_id: str | None = None


class AutoModeration:
    """
    Automatic moderation execution system.

    Takes ModerationResult from ModerationIntelligence and executes
    appropriate actions based on configuration and user history.
    """

    def __init__(self, bot, awareness_engine=None, mod_intelligence=None,
                 config: AutoModConfig | None = None,
                 log_dir: Path = None):
        """
        Initialize auto-moderation system.

        Args:
            bot: Discord bot instance
            awareness_engine: ServerAwarenessEngine for context
            mod_intelligence: ModerationIntelligence for analysis
            config: AutoModConfig (uses defaults if None)
            log_dir: Directory for action logs
        """
        self.bot = bot
        self.awareness = awareness_engine
        self.mod_intel = mod_intelligence
        self.config = config or AutoModConfig()

        # Action history
        self.actions: list[ModerationAction] = []
        self.action_log_path = (log_dir or Path("logs")) / "moderation_actions.jsonl"
        self.action_log_path.parent.mkdir(parents=True, exist_ok=True)

        # Rate limiting trackers
        self.action_timestamps: list[float] = []
        self.timeout_timestamps: list[float] = []
        self.kick_timestamps: list[float] = []
        self.ban_timestamps: list[float] = []

        # User action history (for graduated responses)
        self.user_actions: dict[str, list[ModerationAction]] = {}  # user_id -> actions

        logger.info(f"[auto_mod] Initialized (dry_run={self.config.dry_run})")

    async def process_violation(self, message, moderation_result) -> ModerationAction | None:
        """
        Process a rule violation and take appropriate action.

        Args:
            message: Discord message object
            moderation_result: ModerationResult from ModerationIntelligence

        Returns:
            ModerationAction if action was taken, None otherwise
        """
        if not self.config.enabled:
            return None

        # Safety checks
        if not await self._can_moderate(message.author):
            logger.warning(f"[auto_mod] Cannot moderate {message.author.name} (admin/owner)")
            return None

        # Check rate limits
        if not self._check_rate_limits():
            logger.warning("[auto_mod] Rate limit exceeded, skipping action")
            return None

        # Determine action based on threat level and history
        action_type = self._determine_action(message.author.id, moderation_result)

        if action_type is None:
            return None

        # Create action record
        action = ModerationAction(
            action_id=f"action_{time.time()}_{message.id}",
            action_type=action_type,
            timestamp=time.time(),
            user_id=str(message.author.id),
            user_name=message.author.display_name,
            guild_id=str(message.guild.id) if message.guild else "DM",
            message_id=str(message.id),
            message_content=message.content[:500],
            channel_id=str(getattr(getattr(message, "channel", None), "id", "")),
            violation_type=", ".join(v.value for v in moderation_result.violation_types),
            threat_level=moderation_result.threat_level.value,
            confidence=moderation_result.confidence,
            evidence=moderation_result.rule_matches + moderation_result.behavioral_flags
        )

        # Check if should auto-execute
        should_execute = self._should_auto_execute(action, moderation_result)

        if should_execute:
            await self._execute_action(message, action)
        else:
            # Requires admin approval
            await self._request_admin_approval(message, action, moderation_result)

        # Log action
        self._log_action(action)

        # Store in history
        self.actions.append(action)
        if action.user_id not in self.user_actions:
            self.user_actions[action.user_id] = []
        self.user_actions[action.user_id].append(action)

        return action

    async def _can_moderate(self, member) -> bool:
        """Check if we can moderate this user (not admin/owner)."""
        if not hasattr(member, 'guild'):
            return False

        guild = member.guild

        # Cannot moderate server owner
        if guild.owner_id == member.id:
            return False

        # Cannot moderate administrators
        if member.guild_permissions.administrator:
            return False

        # Cannot moderate the bot itself
        return member.id != self.bot.user.id

    def _check_rate_limits(self) -> bool:
        """Check if rate limits allow another action."""
        now = time.time()

        # Clean old timestamps (older than 1 hour)
        cutoff = now - 3600
        self.action_timestamps = [t for t in self.action_timestamps if t > cutoff]
        self.timeout_timestamps = [t for t in self.timeout_timestamps if t > cutoff]
        self.kick_timestamps = [t for t in self.kick_timestamps if t > cutoff]
        self.ban_timestamps = [t for t in self.ban_timestamps if t > cutoff]

        # Check limits
        minute_cutoff = now - 60
        recent_actions = [t for t in self.action_timestamps if t > minute_cutoff]

        if len(recent_actions) >= self.config.max_actions_per_minute:
            return False

        if len(self.timeout_timestamps) >= self.config.max_timeouts_per_hour:
            return False

        if len(self.kick_timestamps) >= self.config.max_kicks_per_hour:
            return False

        return not len(self.ban_timestamps) >= self.config.max_bans_per_hour

    def _determine_action(self, user_id: str, moderation_result) -> ActionType | None:
        """Determine what action to take based on threat level and history."""
        threat = moderation_result.threat_level.value

        # Special case: self-harm
        if ViolationType.SELF_HARM in moderation_result.violation_types:
            return ActionType.ALERT_SUPPORT

        # Get user history
        user_history = self.user_actions.get(str(user_id), [])
        prior_warnings = sum(1 for a in user_history if a.action_type == ActionType.WARN)
        prior_timeouts = sum(1 for a in user_history if a.action_type == ActionType.TIMEOUT)

        # INFO level → no action
        if threat == "info":
            return None

        # WARNING level → delete + warn (unless already warned recently)
        if threat == "warning":
            # Check cooldown
            recent_warns = [a for a in user_history
                           if a.action_type == ActionType.WARN
                           and time.time() - a.timestamp < self.config.warn_cooldown]
            if recent_warns:
                return ActionType.DELETE  # Just delete, skip warning
            return ActionType.WARN

        # DANGEROUS level → timeout (graduated)
        if threat == "dangerous":
            # Check cooldown
            recent_timeouts = [a for a in user_history
                              if a.action_type == ActionType.TIMEOUT
                              and time.time() - a.timestamp < self.config.timeout_cooldown]
            if recent_timeouts:
                return ActionType.WARN  # Downgrade to warning if on cooldown

            # Escalate based on history
            if prior_timeouts >= 2:
                return ActionType.KICK
            else:
                return ActionType.TIMEOUT

        # CRITICAL level → kick or ban
        if threat == "critical":
            if prior_timeouts >= 1 or prior_warnings >= 3:
                return ActionType.BAN
            else:
                return ActionType.KICK

        return None

    def _should_auto_execute(self, action: ModerationAction, moderation_result) -> bool:
        """Check if action should be auto-executed or require approval."""
        if self.config.dry_run:
            return False

        # Never auto-execute kicks/bans if configured
        if action.action_type == ActionType.KICK and self.config.never_auto_kick:
            return False
        if action.action_type == ActionType.BAN and self.config.never_auto_ban:
            return False

        # Check confidence thresholds
        if action.action_type == ActionType.DELETE:
            return moderation_result.confidence >= self.config.auto_delete_threshold

        if action.action_type == ActionType.WARN:
            return moderation_result.confidence >= self.config.auto_warn_threshold

        if action.action_type == ActionType.TIMEOUT:
            return moderation_result.confidence >= self.config.auto_timeout_threshold

        # Kicks and bans never auto-execute by default
        return False

    async def _execute_action(self, message, action: ModerationAction):
        """Execute a moderation action."""
        try:
            if self.config.dry_run:
                logger.info(f"[auto_mod] DRY RUN: {action.action_type.value} on {action.user_name}")
                action.executed = False
                return

            if action.action_type == ActionType.DELETE:
                await message.delete()
                logger.info(f"[auto_mod] Deleted message from {action.user_name}")

            elif action.action_type == ActionType.WARN:
                # Delete message
                await message.delete()

                # DM warning to user
                with contextlib.suppress(Exception):
                    await message.author.send(
                        f"⚠️ **Warning from {message.guild.name}**\n\n"
                        f"Your message violated server rules: {action.violation_type}\n"
                        f"Message: `{action.message_content[:200]}`\n\n"
                        f"Further violations may result in timeout or removal from the server.\n"
                        f"If you believe this was a mistake, contact a moderator."
                    )

                logger.info(f"[auto_mod] Warned {action.user_name}")

            elif action.action_type == ActionType.TIMEOUT:
                # Delete message
                await message.delete()

                # Determine timeout duration based on history.
                # The outer process_violation() appends this action to
                # history AFTER _execute_action returns, so we count the new
                # action explicitly via "existing_count + 1" here. This
                # prevents the previous double-append bug where timeouts on
                # the first offense were treated as a second strike.
                user_history = self.user_actions.get(action.user_id, [])
                timeout_count = sum(
                    1 for a in user_history if a.action_type == ActionType.TIMEOUT
                ) + 1

                if timeout_count <= 1:
                    duration = self.config.timeout_first
                elif timeout_count == 2:
                    duration = self.config.timeout_second
                else:
                    duration = self.config.timeout_third

                # Apply timeout
                from datetime import timedelta
                await message.author.timeout(
                    timedelta(seconds=duration),
                    reason=f"Auto-mod: {action.violation_type} (confidence: {action.confidence:.0%})"
                )

                # DM user
                with contextlib.suppress(Exception):
                    await message.author.send(
                        f"⏱️ **Timeout from {message.guild.name}**\n\n"
                        f"You have been timed out for {duration//60} minutes due to: {action.violation_type}\n"
                        f"You can appeal this by contacting a moderator.\n\n"
                        f"Repeated violations will result in removal from the server."
                    )

                # Notify admins if configured
                if self.config.notify_admins_on_timeout and self.config.admin_channel_id:
                    await self._notify_admins(message.guild, action, f"Timeout: {duration//60}min")

                logger.info(f"[auto_mod] Timed out {action.user_name} for {duration}s")

            elif action.action_type == ActionType.KICK:
                await message.author.kick(reason=f"Auto-mod: {action.violation_type}")

                # Notify admins
                if self.config.notify_admins_on_kick and self.config.admin_channel_id:
                    await self._notify_admins(message.guild, action, "Kicked")

                logger.warning(f"[auto_mod] Kicked {action.user_name}")

            elif action.action_type == ActionType.BAN:
                await message.author.ban(
                    reason=f"Auto-mod: {action.violation_type}",
                    delete_message_days=1
                )

                # Notify admins
                if self.config.notify_admins_on_ban and self.config.admin_channel_id:
                    await self._notify_admins(message.guild, action, "Banned")

                logger.critical(f"[auto_mod] Banned {action.user_name}")

            elif action.action_type == ActionType.ALERT_SUPPORT:
                # Self-harm case - alert support channel. NEVER silently drop.
                alert_msg = (
                    f"🆘 **Mental Health Alert**\n\n"
                    f"User {message.author.mention} may need support.\n"
                    f"Message: `{action.message_content[:200]}`\n\n"
                    f"Please reach out privately to offer resources:\n"
                    f"• National Suicide Prevention Lifeline: 988\n"
                    f"• Crisis Text Line: Text HOME to 741741"
                )
                alert_sent = False
                if self.config.admin_channel_id:
                    channel = self.bot.get_channel(int(self.config.admin_channel_id))
                    if channel:
                        await channel.send(alert_msg)
                        alert_sent = True
                # Fallback: try guild system channel or audit log channel
                if not alert_sent and message.guild:
                    for fallback_ch in [message.guild.system_channel, message.guild.rules_channel]:
                        if fallback_ch:
                            try:
                                await fallback_ch.send(alert_msg)
                                alert_sent = True
                                break
                            except Exception:
                                logger.exception("[auto_mod] Fallback channel alert delivery failed")
                if not alert_sent:
                    logger.critical(
                        f"[auto_mod] SELF-HARM ALERT COULD NOT BE DELIVERED for {action.user_name}: "
                        f"No admin channel configured. Configure admin_channel_id immediately."
                    )
                logger.warning(f"[auto_mod] Self-harm alert for {action.user_name}")

            action.executed = True
            action.execution_time = time.time()

            # Update rate limit trackers
            self.action_timestamps.append(time.time())
            if action.action_type == ActionType.TIMEOUT:
                self.timeout_timestamps.append(time.time())
            elif action.action_type == ActionType.KICK:
                self.kick_timestamps.append(time.time())
            elif action.action_type == ActionType.BAN:
                self.ban_timestamps.append(time.time())

        except Exception as e:
            action.error = str(e)
            logger.error(f"[auto_mod] Failed to execute {action.action_type.value}: {e}")

    async def _request_admin_approval(self, message, action: ModerationAction, moderation_result):
        """Request admin approval for an action."""
        if not self.config.admin_channel_id:
            logger.warning("[auto_mod] Admin approval required but no admin channel configured")
            return

        channel = self.bot.get_channel(int(self.config.admin_channel_id))
        if not channel:
            return

        import discord
        embed = discord.Embed(
            title="⚠️ Moderation Action Requires Approval",
            description=f"Action: **{action.action_type.value.upper()}**\nUser: {message.author.mention}",
            color=0xe74c3c
        )

        embed.add_field(name="Violation", value=action.violation_type, inline=True)
        embed.add_field(name="Threat Level", value=action.threat_level.upper(), inline=True)
        embed.add_field(name="Confidence", value=f"{action.confidence:.0%}", inline=True)
        embed.add_field(name="Message", value=action.message_content[:500], inline=False)
        embed.add_field(name="Evidence", value="\n".join(action.evidence[:5]) or "None", inline=False)

        embed.set_footer(text=f"Action ID: {action.action_id}")

        await channel.send(
            content="React ✅ to approve or ❌ to reject",
            embed=embed
        )

    async def _notify_admins(self, guild, action: ModerationAction, action_summary: str):
        """Notify admins of an executed action."""
        if not self.config.admin_channel_id:
            return

        channel = self.bot.get_channel(int(self.config.admin_channel_id))
        if not channel:
            return

        import discord
        embed = discord.Embed(
            title=f"🤖 Auto-Mod Action: {action_summary}",
            description=f"User: {action.user_name} (`{action.user_id}`)",
            color=0xf39c12,
            timestamp=discord.utils.utcnow()
        )

        embed.add_field(name="Violation", value=action.violation_type, inline=True)
        embed.add_field(name="Confidence", value=f"{action.confidence:.0%}", inline=True)
        embed.add_field(name="Message", value=action.message_content[:200], inline=False)

        await channel.send(embed=embed)

    def _log_action(self, action: ModerationAction):
        """Log action to file. Schema matches what the web dashboard reads."""
        try:
            threat = action.threat_level
            severity_map = {
                "info": "low", "warning": "medium",
                "dangerous": "high", "critical": "critical",
            }

            log_entry = {
                "timestamp": action.timestamp,
                "action_type": action.action_type.value,
                "user_id": action.user_id,
                "user_name": action.user_name,
                "guild_id": action.guild_id,
                "channel_id": action.channel_id,
                "message_id": action.message_id,
                "content": action.message_content[:500],
                "severity": severity_map.get(threat, "none"),
                "threat_level": threat,
                "category": action.violation_type,
                "reason": f"Auto-mod: {action.violation_type} (confidence: {action.confidence:.0%})",
                "confidence": action.confidence,
                "executed": action.executed,
                "dry_run": self.config.dry_run,
                "error": action.error,
                "source": "auto_moderation",
            }

            with open(self.action_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry) + "\n")

        except Exception as e:
            logger.error(f"[auto_mod] Failed to log action: {e}")

    def get_user_history(self, user_id: str) -> list[ModerationAction]:
        """Get moderation history for a user."""
        return self.user_actions.get(str(user_id), [])

    def get_statistics(self) -> dict:
        """Get moderation statistics."""
        total = len(self.actions)
        if total == 0:
            return {"total": 0}

        executed = sum(1 for a in self.actions if a.executed)

        by_action = {}
        by_threat = {}

        for action in self.actions:
            action_name = action.action_type.value
            by_action[action_name] = by_action.get(action_name, 0) + 1

            threat_name = action.threat_level
            by_threat[threat_name] = by_threat.get(threat_name, 0) + 1

        return {
            "total_actions": total,
            "executed": executed,
            "pending_approval": total - executed,
            "by_action_type": by_action,
            "by_threat_level": by_threat,
            "avg_confidence": sum(a.confidence for a in self.actions) / total if total > 0 else 0.0,
        }
