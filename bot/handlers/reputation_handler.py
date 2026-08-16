"""Cross-server reputation handler.

Provides:
  /reputation check @user  — Check a user's reputation across all opt-in servers
  /reputation optin        — Opt this server into the shared reputation network
  /reputation optout       — Opt this server out
  /reputation setalert     — Set the channel for reputation alerts
  /reputation stats        — Show reputation network statistics
  /reputation report       — Manually report a user action to the network

Also hooks on_member_join to auto-alert when a known bad actor joins.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from azure.reputation_db import ReputationDatabase, ReputationEvent

if TYPE_CHECKING:
    pass

logger = logging.getLogger("azure.discord.reputation")

COLOR_INFO = 0x5865F2
COLOR_SUCCESS = 0x57F287
COLOR_WARNING = 0xFEE75C
COLOR_DANGER = 0xED4245
COLOR_NEUTRAL = 0x95A5A6

_db: ReputationDatabase | None = None


def get_db() -> ReputationDatabase:
    global _db
    if _db is None:
        _db = ReputationDatabase()
    return _db


# ── Member join hook ────────────────────────────────────────────────────────

async def check_reputation_on_join(member: discord.Member) -> None:
    """Check a new member's reputation and alert if they have a record.

    Hook this into the bot's on_member_join event.
    """
    db = get_db()
    guild = member.guild
    if not guild or not db.is_opted_in(str(guild.id)):
        return

    if not db.has_reputation(str(member.id)):
        return

    summary = db.get_reputation(str(member.id))
    if summary.total_events == 0:
        return

    alert_channel_id = db.get_alert_channel(str(guild.id))
    if not alert_channel_id:
        return

    channel = guild.get_channel(int(alert_channel_id))
    if not channel or not isinstance(channel, discord.TextChannel):
        return

    embed = discord.Embed(
        title="Reputation Alert",
        description=(
            f"A user with a history of moderation actions joined this server.\n\n"
            f"**User:** {member} ({member.id})\n"
            f"**Account Created:** {member.created_at.strftime('%Y-%m-%d')}\n"
            f"**Total Events:** {summary.total_events}\n"
            f"**Bans:** {summary.ban_count} | **Kicks:** {summary.kick_count} | "
            f"**Warns:** {summary.warn_count} | **Timeouts:** {summary.timeout_count}\n"
            f"**Servers Reporting:** {summary.unique_servers}\n"
            f"**First Seen:** {time.strftime('%Y-%m-%d', time.localtime(summary.first_seen))}\n"
            f"**Last Seen:** {time.strftime('%Y-%m-%d', time.localtime(summary.last_seen))}"
        ),
        color=COLOR_WARNING,
    )
    embed.set_footer(text="Cross-server reputation network • Verify before taking action")
    try:
        await channel.send(embed=embed)
    except Exception:
        logger.exception("Failed to send reputation alert")


# ── Audit log monitor ───────────────────────────────────────────────────────

async def record_from_moderation_action(
    guild: discord.Guild,
    target_id: str,
    target_name: str,
    action_type: str,
    reason: str = "",
    moderator_name: str = "",
) -> None:
    """Record a moderation event from any server (auto-detected via audit log)."""
    db = get_db()
    if not db.is_opted_in(str(guild.id)):
        return

    event = ReputationEvent(
        target_id=target_id,
        target_name=target_name,
        action_type=action_type,
        reason=reason or "No reason provided",
        source_guild_id=str(guild.id),
        source_guild_name=guild.name,
        moderator_name=moderator_name,
        timestamp=time.time(),
    )
    db.record_event(event)
    logger.info(
        "Recorded %s for %s in %s (reason: %s)",
        action_type, target_name, guild.name, reason,
    )


async def scan_audit_log_for_reputation(guild: discord.Guild, minutes: int = 5) -> int:
    """Scan recent audit log events and record ban/kick actions from opt-in servers.

    Returns the number of events recorded.
    """
    db = get_db()
    if not db.is_opted_in(str(guild.id)):
        return 0

    after = discord.utils.utcnow().timestamp() - (minutes * 60)
    recorded = 0

    try:
        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.ban,
            limit=20,
        ):
            if entry.target and entry.created_at.timestamp() > after:
                target_name = str(entry.target) if entry.target else "unknown"
                reason = entry.reason or "No reason provided"
                mod = str(entry.user) if entry.user else "unknown"
                await record_from_moderation_action(
                    guild, str(entry.target.id), target_name,
                    "ban", reason, mod,
                )
                recorded += 1

        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.kick,
            limit=20,
        ):
            if entry.target and entry.created_at.timestamp() > after:
                target_name = str(entry.target) if entry.target else "unknown"
                reason = entry.reason or "No reason provided"
                mod = str(entry.user) if entry.user else "unknown"
                await record_from_moderation_action(
                    guild, str(entry.target.id), target_name,
                    "kick", reason, mod,
                )
                recorded += 1

        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.member_disconnect,
            limit=10,
        ):
            if entry.target and entry.created_at.timestamp() > after:
                target_name = str(entry.target) if entry.target else "unknown"
                reason = entry.reason or "No reason provided"
                mod = str(entry.user) if entry.user else "unknown"
                await record_from_moderation_action(
                    guild, str(entry.target.id), target_name,
                    "timeout", f"Disconnected: {reason}", mod,
                )
                recorded += 1

    except discord.Forbidden:
        logger.warning("Missing View Audit Log permission in %s", guild.name)
    except Exception:
        logger.exception("Error scanning audit log in %s", guild.name)

    return recorded


# ── Admin permission check ──────────────────────────────────────────────────

def _is_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    user = interaction.user
    if isinstance(user, discord.Member):
        return user.guild_permissions.administrator or interaction.guild.owner_id == user.id
    return False


# ── Slash command registration ──────────────────────────────────────────────

def register_reputation_commands(tree: app_commands.CommandTree) -> None:
    """Register all /reputation slash commands."""

    reputation_group = app_commands.Group(
        name="reputation",
        description="Cross-server reputation network commands",
    )

    # ── /reputation check ──────────────────────────────────────────────
    @reputation_group.command(
        name="check",
        description="Check a user's reputation across the shared network",
    )
    @app_commands.describe(user="The user to check")
    async def check_cmd(interaction: discord.Interaction, user: discord.User):
        try:
            await interaction.response.defer(ephemeral=True)
            db = get_db()
            summary = db.get_reputation(str(user.id))

            embed = discord.Embed(
                title=f"Reputation Report: {user}",
                color=COLOR_INFO,
            )

            if summary.total_events == 0:
                embed.description = "No reputation events found for this user."
                embed.color = COLOR_SUCCESS
            else:
                embed.description = (
                    f"This user has **{summary.total_events}** recorded moderation event(s)"
                    f" across **{summary.unique_servers}** server(s)."
                )
                embed.add_field(name="Bans", value=str(summary.ban_count), inline=True)
                embed.add_field(name="Kicks", value=str(summary.kick_count), inline=True)
                embed.add_field(name="Warnings", value=str(summary.warn_count), inline=True)
                embed.add_field(name="Timeouts", value=str(summary.timeout_count), inline=True)
                embed.add_field(
                    name="First Seen",
                    value=time.strftime("%Y-%m-%d", time.localtime(summary.first_seen)),
                    inline=True,
                )
                embed.add_field(
                    name="Last Seen",
                    value=time.strftime("%Y-%m-%d", time.localtime(summary.last_seen)),
                    inline=True,
                )
                if summary.servers:
                    servers_str = ", ".join(summary.servers[:10])
                    if len(summary.servers) > 10:
                        servers_str += f" ... and {len(summary.servers) - 10} more"
                    embed.add_field(
                        name=f"Servers ({len(summary.servers)})",
                        value=servers_str[:1024],
                        inline=False,
                    )

            embed.set_footer(text="Data from opt-in servers only")
            await interaction.followup.send(embed=embed, ephemeral=True)

            db.record_query(str(user.id), str(interaction.guild_id or "dm"))

        except Exception:
            logger.exception("[reputation check] command failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send("An error occurred.", ephemeral=True)

    # ── /reputation optin ──────────────────────────────────────────────
    @reputation_group.command(
        name="optin",
        description="Opt this server into the shared reputation network (admin only)",
    )
    @app_commands.describe(
        channel="Channel to receive reputation alerts (default: system channel)",
    )
    async def optin_cmd(
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only server admins can opt in.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            db = get_db()
            channel_id = str(channel.id) if channel else ""
            db.opt_in(str(interaction.guild.id), interaction.guild.name, channel_id)

            embed = discord.Embed(
                title="Opted into Reputation Network",
                description=(
                    "This server is now participating in the cross-server reputation network.\n\n"
                    "**What happens:**\n"
                    "• Bans and kicks from this server will be recorded\n"
                    "• When a known bad actor joins, you'll get an alert\n"
                    "• You can check any user with `/reputation check`\n\n"
                    "**Privacy:** Only action type, reason, and user ID are shared."
                ),
                color=COLOR_SUCCESS,
            )
            if channel:
                embed.add_field(
                    name="Alert Channel",
                    value=f"Alerts will be sent to {channel.mention}",
                    inline=False,
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)

            # Scan recent audit log for initial data
            count = await scan_audit_log_for_reputation(interaction.guild, minutes=60)
            if count > 0:
                await interaction.followup.send(
                    f"📋 Recorded {count} past moderation events from audit log.",
                    ephemeral=True,
                )

        except Exception:
            logger.exception("[reputation optin] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /reputation optout ─────────────────────────────────────────────
    @reputation_group.command(
        name="optout",
        description="Opt this server out of the shared reputation network (admin only)",
    )
    async def optout_cmd(interaction: discord.Interaction):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only server admins can opt out.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            db = get_db()
            db.opt_out(str(interaction.guild.id))

            embed = discord.Embed(
                title="Opted Out of Reputation Network",
                description=(
                    "This server has left the reputation network.\n\n"
                    "Existing reputation data from this server will remain "
                    "but new events will not be recorded."
                ),
                color=COLOR_NEUTRAL,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[reputation optout] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /reputation setalert ───────────────────────────────────────────
    @reputation_group.command(
        name="setalert",
        description="Set the channel for reputation alerts (admin only)",
    )
    @app_commands.describe(channel="The channel to receive alerts")
    async def setalert_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only server admins can set the alert channel.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            db = get_db()
            if not db.is_opted_in(str(interaction.guild.id)):
                return await interaction.response.send_message(
                    "This server is not opted in. Use `/reputation optin` first.",
                    ephemeral=True,
                )

            db.set_alert_channel(str(interaction.guild.id), str(channel.id))
            await interaction.response.send_message(
                f"Alert channel set to {channel.mention}", ephemeral=True,
            )

        except Exception:
            logger.exception("[reputation setalert] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /reputation stats ──────────────────────────────────────────────
    @reputation_group.command(
        name="stats",
        description="Show reputation network statistics",
    )
    async def stats_cmd(interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            db = get_db()
            stats = db.get_stats()

            embed = discord.Embed(
                title="Reputation Network Stats",
                color=COLOR_INFO,
            )
            embed.add_field(name="Total Events Recorded", value=str(stats["total_events"]), inline=True)
            embed.add_field(name="Unique Users Tracked", value=str(stats["unique_targets"]), inline=True)
            embed.add_field(name="Participating Servers", value=str(stats["opted_in_guilds"]), inline=True)
            embed.add_field(name="Servers Reporting Events", value=str(stats["unique_guilds"]), inline=True)

            if interaction.guild:
                db_local = get_db()
                opted = db_local.is_opted_in(str(interaction.guild.id))
                embed.add_field(
                    name="This Server",
                    value="✅ Opted in" if opted else "❌ Not opted in",
                    inline=False,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[reputation stats] failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send("An error occurred.", ephemeral=True)

    # ── /reputation report ─────────────────────────────────────────────
    @reputation_group.command(
        name="report",
        description="Manually report a moderation action to the reputation network",
    )
    @app_commands.describe(
        user="The user who was actioned",
        action="The action taken (ban, kick, warn, timeout)",
        reason="The reason for the action",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Ban", value="ban"),
        app_commands.Choice(name="Kick", value="kick"),
        app_commands.Choice(name="Warn", value="warn"),
        app_commands.Choice(name="Timeout", value="timeout"),
    ])
    async def report_cmd(
        interaction: discord.Interaction,
        user: discord.User,
        action: str,
        reason: str = "No reason provided",
    ):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only server admins can report actions.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            db = get_db()
            if not db.is_opted_in(str(interaction.guild.id)):
                return await interaction.response.send_message(
                    "This server is not opted in. Use `/reputation optin` first.",
                    ephemeral=True,
                )

            event = ReputationEvent(
                target_id=str(user.id),
                target_name=str(user),
                action_type=action,
                reason=reason,
                source_guild_id=str(interaction.guild.id),
                source_guild_name=interaction.guild.name,
                moderator_id=str(interaction.user.id),
                moderator_name=str(interaction.user),
                timestamp=time.time(),
            )
            db.record_event(event)

            await interaction.response.send_message(
                f"Reported **{user}** for **{action}** — reason: {reason}",
                ephemeral=True,
            )

        except Exception:
            logger.exception("[reputation report] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    tree.add_command(reputation_group)
