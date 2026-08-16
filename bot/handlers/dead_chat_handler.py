"""Dead Chat Revival handler.

Commands:
  /revival on [#channel]       — Enable dead chat revival for a channel (or current)
  /revival off [#channel]      — Disable dead chat revival for a channel
  /revival status              — Show revival status and stats for this server
  /revival threshold <minutes> — Set inactivity threshold before revival
  /revival cooldown <minutes>  — Set minimum time between revivals
  /revival prompt <text>       — Set a custom revival prompt
  /revival test                — Send a test revival message to current channel
  /revival history             — Show revival history for this server
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from azure.dead_chat_revival import (
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_REVIVAL_HOURS,
    DEFAULT_THRESHOLD_MINUTES,
    get_db,
    select_revival_prompt,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger("azure.discord.revival")

COLOR_INFO = 0x5865F2
COLOR_SUCCESS = 0x57F287
COLOR_WARNING = 0xFEE75C
COLOR_NEUTRAL = 0x95A5A6
COLOR_REVIVAL = 0x9B59B6


def _is_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    user = interaction.user
    if isinstance(user, discord.Member):
        return user.guild_permissions.administrator or interaction.guild.owner_id == user.id
    return False


def register_revival_commands(tree: app_commands.CommandTree) -> None:
    """Register all /revival slash commands."""

    revival_group = app_commands.Group(
        name="revival",
        description="Dead chat revival commands",
    )

    # ── /revival on ───────────────────────────────────────────────────
    @revival_group.command(
        name="on",
        description="Enable dead chat revival for a channel (admin only)",
    )
    @app_commands.describe(
        channel="Channel to enable revival for (default: this channel)",
    )
    async def on_cmd(
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only admins can enable revival.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            target = channel or interaction.channel
            if not isinstance(target, discord.TextChannel):
                return await interaction.response.send_message(
                    "Can only enable revival in text channels.", ephemeral=True,
                )

            db = get_db()
            db.set_enabled(str(interaction.guild.id), str(target.id), True)

            embed = discord.Embed(
                title="💬 Revival Enabled",
                description=(
                    f"Dead chat revival is now enabled in {target.mention}.\n\n"
                    f"**Current settings:**\n"
                    f"• Inactivity threshold: {DEFAULT_THRESHOLD_MINUTES} min\n"
                    f"• Cooldown: {DEFAULT_COOLDOWN_MINUTES} min\n"
                    f"• Revival window: {DEFAULT_REVIVAL_HOURS}h\n\n"
                    f"Use `/revival threshold`, `/revival cooldown`, and "
                    f"`/revival prompt` to customize."
                ),
                color=COLOR_SUCCESS,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[revival on] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /revival off ──────────────────────────────────────────────────
    @revival_group.command(
        name="off",
        description="Disable dead chat revival for a channel (admin only)",
    )
    @app_commands.describe(
        channel="Channel to disable revival for (default: this channel)",
    )
    async def off_cmd(
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only admins can disable revival.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            target = channel or interaction.channel
            if not isinstance(target, discord.TextChannel):
                return await interaction.response.send_message(
                    "Can only disable revival in text channels.", ephemeral=True,
                )

            db = get_db()
            db.set_enabled(str(interaction.guild.id), str(target.id), False)

            await interaction.response.send_message(
                f"💤 Revival disabled in {target.mention}.", ephemeral=True,
            )

        except Exception:
            logger.exception("[revival off] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /revival status ───────────────────────────────────────────────
    @revival_group.command(
        name="status",
        description="Show revival status for this server",
    )
    async def status_cmd(interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            if not interaction.guild:
                return await interaction.followup.send(
                    "This command can only be used in a server.", ephemeral=True,
                )

            db = get_db()
            enabled_channels = db.get_enabled_channels(str(interaction.guild.id))
            activity = db.get_activity_summary(str(interaction.guild.id))
            stats = db.get_stats()

            embed = discord.Embed(
                title="💬 Dead Chat Revival Status",
                color=COLOR_REVIVAL,
            )
            embed.add_field(name="Channels with Revival", value=str(len(enabled_channels)), inline=True)
            embed.add_field(name="Total Revivals Sent", value=str(stats["total_revivals"]), inline=True)
            embed.add_field(name="Successful Revivals", value=str(stats["successful_revivals"]), inline=True)

            embed.add_field(
                name="Server Activity",
                value=(
                    f"Tracked channels: {activity.get('channels', 0)}\n"
                    f"Active (last hour): {activity.get('active_channels', 0)}\n"
                    f"Messages (24h): {activity.get('messages_24h', 0)}"
                ),
                inline=False,
            )

            if enabled_channels:
                lines = []
                for cfg in enabled_channels[:5]:
                    ch = interaction.guild.get_channel(int(cfg["channel_id"]))
                    ch_name = ch.mention if ch else f"`{cfg['channel_id']}`"
                    last_msg = db.get_last_message_time(
                        str(interaction.guild.id), cfg["channel_id"],
                    )
                    if last_msg:
                        silence = int((time.time() - last_msg) / 60)
                        lines.append(f"{ch_name} — silent {silence} min")
                if lines:
                    embed.add_field(
                        name=f"Active Channels ({len(enabled_channels)})",
                        value="\n".join(lines[:5])[:1024],
                        inline=False,
                    )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[revival status] failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send("An error occurred.", ephemeral=True)

    # ── /revival threshold ────────────────────────────────────────────
    @revival_group.command(
        name="threshold",
        description="Set inactivity threshold before revival (admin only)",
    )
    @app_commands.describe(
        minutes="Minutes of inactivity before revival (min: 30, max: 1440)",
        channel="Channel to configure (default: this channel)",
    )
    async def threshold_cmd(
        interaction: discord.Interaction,
        minutes: int,
        channel: discord.TextChannel | None = None,
    ):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only admins can change threshold.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            if minutes < 30 or minutes > 1440:
                return await interaction.response.send_message(
                    "Threshold must be between 30 and 1440 minutes (1 day).", ephemeral=True,
                )

            target = channel or interaction.channel
            if not isinstance(target, discord.TextChannel):
                return await interaction.response.send_message(
                    "Can only configure text channels.", ephemeral=True,
                )

            db = get_db()
            db.update_config(
                str(interaction.guild.id), str(target.id),
                threshold_minutes=minutes,
            )

            await interaction.response.send_message(
                f"⏱️ Revival threshold set to **{minutes} minutes** of inactivity for {target.mention}.",
                ephemeral=True,
            )

        except Exception:
            logger.exception("[revival threshold] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /revival cooldown ─────────────────────────────────────────────
    @revival_group.command(
        name="cooldown",
        description="Set minimum time between revivals (admin only)",
    )
    @app_commands.describe(
        minutes="Minutes between revivals (min: 15, max: 1440)",
        channel="Channel to configure (default: this channel)",
    )
    async def cooldown_cmd(
        interaction: discord.Interaction,
        minutes: int,
        channel: discord.TextChannel | None = None,
    ):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only admins can change cooldown.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            if minutes < 15 or minutes > 1440:
                return await interaction.response.send_message(
                    "Cooldown must be between 15 and 1440 minutes (1 day).", ephemeral=True,
                )

            target = channel or interaction.channel
            if not isinstance(target, discord.TextChannel):
                return await interaction.response.send_message(
                    "Can only configure text channels.", ephemeral=True,
                )

            db = get_db()
            db.update_config(
                str(interaction.guild.id), str(target.id),
                cooldown_minutes=minutes,
            )

            await interaction.response.send_message(
                f"⏳ Revival cooldown set to **{minutes} minutes** for {target.mention}.",
                ephemeral=True,
            )

        except Exception:
            logger.exception("[revival cooldown] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /revival prompt ───────────────────────────────────────────────
    @revival_group.command(
        name="prompt",
        description="Set a custom revival prompt for a channel (admin only)",
    )
    @app_commands.describe(
        text="Custom prompt text (leave empty to use random prompts)",
        channel="Channel to configure (default: this channel)",
    )
    async def prompt_cmd(
        interaction: discord.Interaction,
        text: str = "",
        channel: discord.TextChannel | None = None,
    ):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only admins can set prompts.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            target = channel or interaction.channel
            if not isinstance(target, discord.TextChannel):
                return await interaction.response.send_message(
                    "Can only configure text channels.", ephemeral=True,
                )

            db = get_db()
            db.update_config(
                str(interaction.guild.id), str(target.id),
                custom_prompt=text,
            )

            if text:
                await interaction.response.send_message(
                    f"✏️ Custom prompt set for {target.mention}:\n> {text[:500]}",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"✏️ Custom prompt cleared for {target.mention}. "
                    f"Random prompts will be used instead.",
                    ephemeral=True,
                )

        except Exception:
            logger.exception("[revival prompt] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /revival test ─────────────────────────────────────────────────
    @revival_group.command(
        name="test",
        description="Send a test revival prompt to this channel",
    )
    @app_commands.describe(
        prompt="Custom prompt for the test (optional)",
    )
    async def test_cmd(interaction: discord.Interaction, prompt: str = ""):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only admins can test revival.", ephemeral=True,
                )
            if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
                return await interaction.response.send_message(
                    "This command can only be used in a text channel.", ephemeral=True,
                )

            await interaction.response.defer(ephemeral=True)

            text = prompt or select_revival_prompt()

            embed = discord.Embed(
                title="💬 Thread Revival (Test)",
                description=text,
                color=COLOR_REVIVAL,
            )
            embed.set_footer(text="This is a test revival prompt")

            await interaction.channel.send(embed=embed)
            await interaction.followup.send(
                "✅ Test revival sent!", ephemeral=True,
            )

        except Exception:
            logger.exception("[revival test] failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send("An error occurred.", ephemeral=True)

    # ── /revival history ──────────────────────────────────────────────
    @revival_group.command(
        name="history",
        description="Show revival history for this server",
    )
    @app_commands.describe(
        limit="Number of entries to show (default: 10)",
    )
    async def history_cmd(interaction: discord.Interaction, limit: int = 10):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only admins can view history.", ephemeral=True,
                )
            if not interaction.guild:
                return await interaction.response.send_message(
                    "This command can only be used in a server.", ephemeral=True,
                )

            await interaction.response.defer(ephemeral=True)

            db = get_db()
            logs = db.get_revival_history(str(interaction.guild.id), limit=limit)

            if not logs:
                return await interaction.followup.send(
                    "No revival history for this server.", ephemeral=True,
                )

            embed = discord.Embed(
                title="💬 Revival History",
                color=COLOR_INFO,
            )

            for log in logs[:10]:
                ch = interaction.guild.get_channel(int(log["channel_id"]))
                ch_name = ch.mention if ch else f"`{log['channel_id']}`"
                ts = time.strftime("%m-%d %H:%M", time.gmtime(log["sent_at"]))
                revived = "✅" if log["revived"] else "❌"
                name_str = f"{revived} [{ts}] {ch_name}"
                val = (
                    f"Response count: {log['response_count']}\n"
                    f"Prompt: {log['prompt'][:100]}"
                )
                embed.add_field(name=name_str, value=val[:200], inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[revival history] failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send("An error occurred.", ephemeral=True)

    tree.add_command(revival_group)
