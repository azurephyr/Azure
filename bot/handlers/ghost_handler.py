"""Ghost/Invisible Moderation handler.

Provides:
  /ghost on        — Enable ghost moderation for this server
  /ghost off       — Disable ghost moderation for this server
  /ghost status    — Show ghost moderation status
  /ghost log       — Show ghost moderation log
  /ghost warn      — Invisibly warn a user (DM only)
  /ghost mute      — Shadow mute a user (role-based, no audit log)
  /ghost unmute    — Remove a shadow mute
  /ghost kick      — Ghost kick a user (minimal audit log detail)
  /ghost delete    — Silently delete a message
  /ghost stealth   — Toggle stealth mode for auto-moderation
  /ghost setlog    — Set the ghost moderation log channel
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from azure.ghost_moderation import (
    apply_ghost_kick,
    apply_invisible_warn,
    apply_shadow_mute,
    apply_silent_delete,
    get_db,
    remove_shadow_mute,
    send_ghost_log_embed,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger("azure.discord.ghost")

COLOR_GHOST = 0x9B59B6
COLOR_INFO = 0x5865F2
COLOR_SUCCESS = 0x57F287
COLOR_WARNING = 0xFEE75C
COLOR_DANGER = 0xED4245
COLOR_NEUTRAL = 0x95A5A6


def _is_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    user = interaction.user
    if isinstance(user, discord.Member):
        return user.guild_permissions.administrator or interaction.guild.owner_id == user.id
    return False


def _mod_permission(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    user = interaction.user
    if isinstance(user, discord.Member):
        return (
            user.guild_permissions.administrator
            or user.guild_permissions.moderate_members
            or interaction.guild.owner_id == user.id
        )
    return False


def register_ghost_commands(tree: app_commands.CommandTree) -> None:
    """Register all /ghost slash commands."""

    ghost_group = app_commands.Group(
        name="ghost",
        description="Ghost/invisible moderation commands",
    )

    # ── /ghost on ─────────────────────────────────────────────────────
    @ghost_group.command(
        name="on",
        description="Enable ghost moderation for this server (admin only)",
    )
    async def on_cmd(interaction: discord.Interaction):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only server admins can enable ghost moderation.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            db = get_db()
            db.set_enabled(str(interaction.guild.id), True)

            embed = discord.Embed(
                title="👻 Ghost Moderation Enabled",
                description=(
                    "Ghost moderation is now active on this server.\n\n"
                    "**Available actions:**\n"
                    "• `/ghost warn @user` — Invisible warning (DM only)\n"
                    "• `/ghost mute @user` — Shadow mute (no audit log entry)\n"
                    "• `/ghost delete` — Silent message deletion\n"
                    "• `/ghost kick @user` — Ghost kick\n"
                    "• `/ghost stealth` — Toggle auto-mod stealth mode\n\n"
                    "All actions are logged to the ghost log.\n"
                    "Use `/ghost setlog` to configure a log channel."
                ),
                color=COLOR_GHOST,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[ghost on] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /ghost off ────────────────────────────────────────────────────
    @ghost_group.command(
        name="off",
        description="Disable ghost moderation for this server (admin only)",
    )
    async def off_cmd(interaction: discord.Interaction):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only server admins can disable ghost moderation.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            db = get_db()
            db.set_enabled(str(interaction.guild.id), False)

            embed = discord.Embed(
                title="👻 Ghost Moderation Disabled",
                description=(
                    "Ghost moderation has been disabled.\n"
                    "Existing shadow mutes remain active. Use `/ghost unmute` to remove them."
                ),
                color=COLOR_NEUTRAL,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[ghost off] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /ghost status ─────────────────────────────────────────────────
    @ghost_group.command(
        name="status",
        description="Show ghost moderation status for this server",
    )
    async def status_cmd(interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            if not interaction.guild:
                return await interaction.followup.send(
                    "This command can only be used in a server.", ephemeral=True,
                )

            db = get_db()
            config = db.get_config(str(interaction.guild.id))
            active_mutes = db.get_active_mutes(str(interaction.guild.id))
            log_count = len(db.get_log(str(interaction.guild.id), limit=1000))

            embed = discord.Embed(
                title="👻 Ghost Moderation Status",
                color=COLOR_GHOST,
            )
            embed.add_field(name="Enabled", value="✅ Yes" if config["enabled"] else "❌ No", inline=True)
            embed.add_field(name="Stealth Mode", value="✅ On" if config["stealth_mode"] else "❌ Off", inline=True)
            embed.add_field(name="Active Shadow Mutes", value=str(len(active_mutes)), inline=True)
            embed.add_field(name="Total Ghost Actions", value=str(log_count), inline=True)

            if config.get("log_channel_id"):
                channel = interaction.guild.get_channel(int(config["log_channel_id"]))
                embed.add_field(
                    name="Log Channel",
                    value=channel.mention if channel else "Deleted channel",
                    inline=False,
                )
            else:
                embed.add_field(name="Log Channel", value="Not set", inline=False)

            if config.get("muted_role_id"):
                role = interaction.guild.get_role(int(config["muted_role_id"]))
                embed.add_field(
                    name="Muted Role",
                    value=role.name if role else "Deleted role",
                    inline=False,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[ghost status] failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send("An error occurred.", ephemeral=True)

    # ── /ghost warn ───────────────────────────────────────────────────
    @ghost_group.command(
        name="warn",
        description="Invisibly warn a user via DM only",
    )
    @app_commands.describe(
        user="The user to warn",
        reason="Reason for the warning",
    )
    async def warn_cmd(interaction: discord.Interaction, user: discord.User, reason: str):
        try:
            if not _mod_permission(interaction):
                return await interaction.response.send_message(
                    "You need `Moderate Members` permission.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            db = get_db()
            if not db.is_enabled(str(interaction.guild.id)):
                return await interaction.response.send_message(
                    "Ghost moderation is not enabled. Use `/ghost on` first.", ephemeral=True,
                )

            member = interaction.guild.get_member(user.id)
            if not member:
                return await interaction.response.send_message(
                    "That user is not in this server.", ephemeral=True,
                )

            notified = await apply_invisible_warn(
                member, reason,
                moderator_id=str(interaction.user.id),
                moderator_name=str(interaction.user),
            )

            embed = discord.Embed(
                title="👻 Ghost Warning Sent",
                color=COLOR_WARNING,
            )
            embed.add_field(name="User", value=user.mention, inline=True)
            embed.add_field(name="DM Delivered", value="✅ Yes" if notified else "❌ No (DMs closed)", inline=True)
            embed.add_field(name="Reason", value=reason[:1000], inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)

            config = db.get_config(str(interaction.guild.id))
            if config.get("log_channel_id"):
                channel = interaction.guild.get_channel(int(config["log_channel_id"]))
                if channel:
                    log_entry = db.get_log(str(interaction.guild.id), limit=1)[0]
                    await send_ghost_log_embed(channel, log_entry)

        except Exception:
            logger.exception("[ghost warn] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /ghost mute ───────────────────────────────────────────────────
    @ghost_group.command(
        name="mute",
        description="Shadow mute a user (role-based, no audit log entry)",
    )
    @app_commands.describe(
        user="The user to mute",
        duration="Duration in minutes (0 = permanent, default: 60)",
        reason="Reason for the mute",
    )
    async def mute_cmd(
        interaction: discord.Interaction,
        user: discord.User,
        duration: int = 60,
        reason: str = "",
    ):
        try:
            if not _mod_permission(interaction):
                return await interaction.response.send_message(
                    "You need `Moderate Members` permission.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            db = get_db()
            if not db.is_enabled(str(interaction.guild.id)):
                return await interaction.response.send_message(
                    "Ghost moderation is not enabled. Use `/ghost on` first.", ephemeral=True,
                )

            member = interaction.guild.get_member(user.id)
            if not member:
                return await interaction.response.send_message(
                    "That user is not in this server.", ephemeral=True,
                )

            success = await apply_shadow_mute(
                member, reason=reason or "No reason provided",
                duration_minutes=duration,
                moderator_id=str(interaction.user.id),
                moderator_name=str(interaction.user),
            )

            if not success:
                return await interaction.response.send_message(
                    "Failed to apply shadow mute. Check bot permissions.", ephemeral=True,
                )

            duration_str = "Permanent" if duration <= 0 else f"{duration} minutes"
            embed = discord.Embed(
                title="🔇 Shadow Mute Applied",
                color=COLOR_GHOST,
            )
            embed.add_field(name="User", value=user.mention, inline=True)
            embed.add_field(name="Duration", value=duration_str, inline=True)
            if reason:
                embed.add_field(name="Reason", value=reason[:1000], inline=False)
            embed.set_footer(text="No audit log entry was created")
            await interaction.response.send_message(embed=embed, ephemeral=True)

            config = db.get_config(str(interaction.guild.id))
            if config.get("log_channel_id"):
                channel = interaction.guild.get_channel(int(config["log_channel_id"]))
                if channel:
                    log_entry = db.get_log(str(interaction.guild.id), limit=1)[0]
                    await send_ghost_log_embed(channel, log_entry)

        except Exception:
            logger.exception("[ghost mute] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /ghost unmute ─────────────────────────────────────────────────
    @ghost_group.command(
        name="unmute",
        description="Remove a shadow mute from a user",
    )
    @app_commands.describe(
        user="The user to unmute",
    )
    async def unmute_cmd(interaction: discord.Interaction, user: discord.User):
        try:
            if not _mod_permission(interaction):
                return await interaction.response.send_message(
                    "You need `Moderate Members` permission.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            member = interaction.guild.get_member(user.id)
            if not member:
                return await interaction.response.send_message(
                    "That user is not in this server.", ephemeral=True,
                )

            success = await remove_shadow_mute(
                member,
                moderator_id=str(interaction.user.id),
                moderator_name=str(interaction.user),
            )

            if not success:
                return await interaction.response.send_message(
                    "That user is not shadow muted or role could not be removed.", ephemeral=True,
                )

            embed = discord.Embed(
                title="🔊 Shadow Mute Removed",
                description=f"Shadow mute removed from {user.mention}",
                color=COLOR_SUCCESS,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[ghost unmute] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /ghost kick ───────────────────────────────────────────────────
    @ghost_group.command(
        name="kick",
        description="Ghost kick a user (minimal audit log detail)",
    )
    @app_commands.describe(
        user="The user to kick",
        reason="Reason (visible only in ghost log)",
    )
    async def kick_cmd(interaction: discord.Interaction, user: discord.User, reason: str = ""):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only admins can ghost kick.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            db = get_db()
            if not db.is_enabled(str(interaction.guild.id)):
                return await interaction.response.send_message(
                    "Ghost moderation is not enabled. Use `/ghost on` first.", ephemeral=True,
                )

            member = interaction.guild.get_member(user.id)
            if not member:
                return await interaction.response.send_message(
                    "That user is not in this server.", ephemeral=True,
                )

            success = await apply_ghost_kick(
                member, reason=reason,
                moderator_id=str(interaction.user.id),
                moderator_name=str(interaction.user),
            )

            if not success:
                return await interaction.response.send_message(
                    "Failed to kick user. Check bot permissions.", ephemeral=True,
                )

            embed = discord.Embed(
                title="👢 Ghost Kick Executed",
                description=f"{user.mention} has been kicked.",
                color=COLOR_DANGER,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[ghost kick] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /ghost delete ─────────────────────────────────────────────────
    @ghost_group.command(
        name="delete",
        description="Silently delete a message (provide message link or ID)",
    )
    @app_commands.describe(
        message_id="The Discord message ID or link",
        reason="Reason (visible only in ghost log)",
    )
    async def delete_cmd(interaction: discord.Interaction, message_id: str, reason: str = ""):
        try:
            if not _mod_permission(interaction):
                return await interaction.response.send_message(
                    "You need `Moderate Members` permission.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            db = get_db()
            if not db.is_enabled(str(interaction.guild.id)):
                return await interaction.response.send_message(
                    "Ghost moderation is not enabled. Use `/ghost on` first.", ephemeral=True,
                )

            mid = message_id.strip()
            if "/" in mid:
                parts = mid.split("/")
                mid = parts[-1] if parts[-1].isdigit() else ""

            if not mid.isdigit():
                return await interaction.response.send_message(
                    "Invalid message ID. Provide a numeric message ID or message link.", ephemeral=True,
                )

            try:
                msg = await interaction.channel.fetch_message(int(mid))
            except (discord.NotFound, discord.Forbidden):
                return await interaction.response.send_message(
                    "Message not found in this channel.", ephemeral=True,
                )

            success = await apply_silent_delete(
                msg,
                moderator_id=str(interaction.user.id),
                moderator_name=str(interaction.user),
            )

            if not success:
                return await interaction.response.send_message(
                    "Could not delete message. Check permissions.", ephemeral=True,
                )

            embed = discord.Embed(
                title="🗑️ Message Silently Deleted",
                color=COLOR_DANGER,
            )
            if reason:
                embed.add_field(name="Reason", value=reason[:1000], inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[ghost delete] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /ghost stealth ────────────────────────────────────────────────
    @ghost_group.command(
        name="stealth",
        description="Toggle stealth mode for automatic moderation",
    )
    @app_commands.describe(enabled="Enable stealth mode?")
    async def stealth_cmd(interaction: discord.Interaction, enabled: bool):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only admins can toggle stealth mode.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            db = get_db()
            if not db.is_enabled(str(interaction.guild.id)):
                return await interaction.response.send_message(
                    "Ghost moderation is not enabled. Use `/ghost on` first.", ephemeral=True,
                )

            db.set_stealth(str(interaction.guild.id), enabled)
            db.log_action(
                "stealth_on" if enabled else "stealth_off",
                str(interaction.user.id),
                str(interaction.user),
                str(interaction.user.id),
                str(interaction.user),
                str(interaction.guild.id),
                reason=f"Stealth mode {'enabled' if enabled else 'disabled'}",
            )

            embed = discord.Embed(
                title=f"👻 Stealth Mode {'Enabled' if enabled else 'Disabled'}",
                description=(
                    "Auto-moderation actions will use generic audit log reasons "
                    "and avoid admin channel announcements."
                    if enabled else
                    "Auto-moderation will resume normal audit log behavior."
                ),
                color=COLOR_GHOST if enabled else COLOR_NEUTRAL,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[ghost stealth] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /ghost log ────────────────────────────────────────────────────
    @ghost_group.command(
        name="log",
        description="Show ghost moderation log",
    )
    @app_commands.describe(
        user="Filter by user (optional)",
        limit="Number of entries to show (default: 10)",
    )
    async def log_cmd(
        interaction: discord.Interaction,
        user: discord.User | None = None,
        limit: int = 10,
    ):
        try:
            if not _mod_permission(interaction):
                return await interaction.response.send_message(
                    "You need `Moderate Members` permission.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            await interaction.response.defer(ephemeral=True)

            db = get_db()
            if user:
                entries = db.get_user_log(str(user.id), str(interaction.guild.id), limit=limit)
            else:
                entries = db.get_log(str(interaction.guild.id), limit=limit)

            if not entries:
                return await interaction.followup.send(
                    "No ghost log entries found.", ephemeral=True,
                )

            embed = discord.Embed(
                title=f"👻 Ghost Moderation Log ({len(entries)} entries)",
                color=COLOR_GHOST,
            )

            action_emoji = {
                "delete": "🗑️", "warn": "⚠️", "mute": "🔇",
                "unmute": "🔊", "kick": "👢", "stealth_on": "👻", "stealth_off": "👻",
            }

            for entry in entries[:10]:
                emoji = action_emoji.get(entry["action"], "👻")
                ts = time.strftime("%m-%d %H:%M", time.gmtime(entry["timestamp"]))
                name_str = f"{emoji} [{ts}] {entry['action'].title()} — {entry['target_name']}"
                val = f"Mod: {entry['moderator_name']}"
                if entry.get("reason"):
                    val += f"\nReason: {entry['reason'][:150]}"
                embed.add_field(name=name_str, value=val[:200], inline=False)

            if len(entries) > 10:
                embed.set_footer(text=f"Showing 10 of {len(entries)} entries")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[ghost log] failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send("An error occurred.", ephemeral=True)

    # ── /ghost setlog ─────────────────────────────────────────────────
    @ghost_group.command(
        name="setlog",
        description="Set the ghost moderation log channel (admin only)",
    )
    @app_commands.describe(channel="The channel to receive ghost log alerts")
    async def setlog_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only admins can set the log channel.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            db = get_db()
            if not db.is_enabled(str(interaction.guild.id)):
                return await interaction.response.send_message(
                    "Ghost moderation is not enabled. Use `/ghost on` first.", ephemeral=True,
                )

            db.set_log_channel(str(interaction.guild.id), str(channel.id))
            await interaction.response.send_message(
                f"👻 Ghost log channel set to {channel.mention}", ephemeral=True,
            )

        except Exception:
            logger.exception("[ghost setlog] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    tree.add_command(ghost_group)
