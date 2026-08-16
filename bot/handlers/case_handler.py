"""Unified Moderation Case Management handler.

Provides:
  /case create  — Create a new case for a user
  /case view    — View a case with all details
  /case search  — Search cases by target name or reason
  /case list    — List cases (filtered by status/severity)
  /case note    — Add a note to a case
  /case evidence — Add evidence to a case
  /case assign  — Assign a case to a moderator
  /case close   — Close a case
  /case reopen  — Reopen a closed case
  /case appeal  — Submit an appeal for a case
  /case decide  — Decide on an appeal
  /case optin   — Opt this server into the unified case system
  /case optout  — Opt this server out
  /case setalert — Set the alert channel
  /case stats   — Show case management statistics
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from azure.case_db import CaseDatabase

if TYPE_CHECKING:
    pass

logger = logging.getLogger("azure.discord.cases")

COLOR_INFO = 0x5865F2
COLOR_SUCCESS = 0x57F287
COLOR_WARNING = 0xFEE75C
COLOR_DANGER = 0xED4245
COLOR_NEUTRAL = 0x95A5A6
COLOR_APPEAL = 0x9B59B6

_db: CaseDatabase | None = None


def get_db() -> CaseDatabase:
    global _db
    if _db is None:
        _db = CaseDatabase()
    return _db


def _is_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    user = interaction.user
    if isinstance(user, discord.Member):
        return user.guild_permissions.administrator or interaction.guild.owner_id == user.id
    return False


def _mod_permission(interaction: discord.Interaction) -> bool:
    """Check if user has moderate_members permission."""
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


STATUS_CHOICES = [
    app_commands.Choice(name="Open", value="open"),
    app_commands.Choice(name="Investigating", value="investigating"),
    app_commands.Choice(name="Resolved", value="resolved"),
    app_commands.Choice(name="Closed", value="closed"),
    app_commands.Choice(name="Appealed", value="appealed"),
]

SEVERITY_CHOICES = [
    app_commands.Choice(name="Low", value="low"),
    app_commands.Choice(name="Medium", value="medium"),
    app_commands.Choice(name="High", value="high"),
    app_commands.Choice(name="Critical", value="critical"),
]

ACTION_CHOICES = [
    app_commands.Choice(name="Warn", value="warn"),
    app_commands.Choice(name="Timeout", value="timeout"),
    app_commands.Choice(name="Kick", value="kick"),
    app_commands.Choice(name="Ban", value="ban"),
    app_commands.Choice(name="Mute", value="mute"),
    app_commands.Choice(name="Other", value="other"),
]


def _build_case_embed(case: dict) -> discord.Embed:
    status_emoji = {
        "open": "🟢", "investigating": "🔍",
        "resolved": "✅", "closed": "🔒", "appealed": "🟣",
    }
    severity_color = {
        "low": COLOR_INFO, "medium": COLOR_WARNING,
        "high": COLOR_DANGER, "critical": 0x000000,
    }
    emoji = status_emoji.get(case["status"], "📋")
    color = severity_color.get(case["severity"], COLOR_NEUTRAL)

    embed = discord.Embed(
        title=f"{emoji} Case {case['case_id']}",
        description=case.get("reason", "No reason provided")[:2000],
        color=color,
    )
    embed.add_field(name="Target", value=f"<@{case['target_id']}> ({case['target_name']})", inline=True)
    embed.add_field(name="Status", value=case["status"].title(), inline=True)
    embed.add_field(name="Severity", value=case["severity"].title(), inline=True)
    embed.add_field(name="Action", value=case["action_type"].title(), inline=True)
    embed.add_field(name="Server", value=case.get("guild_name", case["guild_id"][:10]), inline=True)

    if case.get("assigned_to_name"):
        embed.add_field(name="Assigned To", value=case["assigned_to_name"], inline=True)

    embed.add_field(
        name="Created",
        value=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(case["created_at"])),
        inline=True,
    )

    if case.get("closed_at") and case["closed_at"] > 0:
        embed.add_field(
            name="Closed",
            value=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(case["closed_at"])),
            inline=True,
        )

    if case.get("tags"):
        embed.add_field(name="Tags", value=case["tags"][:1024], inline=False)

    embed.set_footer(text=f"Created by {case.get('created_by_name', 'Unknown')}")
    return embed


def _case_list_embed(cases: list[dict]) -> discord.Embed:
    embed = discord.Embed(
        title=f"Cases ({len(cases)} found)",
        color=COLOR_INFO,
    )
    for case in cases[:15]:
        status_icon = {"open": "🟢", "investigating": "🔍", "resolved": "✅", "closed": "🔒", "appealed": "🟣"}
        icon = status_icon.get(case["status"], "📋")
        name_str = f"{icon} {case['case_id']} — {case['target_name']}"
        val = (
            f"Status: {case['status'].title()} | Severity: {case['severity'].title()}\n"
            f"Reason: {case.get('reason', 'N/A')[:120]}"
        )
        embed.add_field(name=name_str, value=val[:200], inline=False)
    return embed


# ── Slash command registration ──────────────────────────────────────────────

def register_case_commands(tree: app_commands.CommandTree) -> None:
    """Register all /case slash commands."""

    case_group = app_commands.Group(
        name="case",
        description="Unified Moderation Case Management commands",
    )

    # ── /case create ──────────────────────────────────────────────────
    @case_group.command(
        name="create",
        description="Create a new moderation case",
    )
    @app_commands.describe(
        user="The user this case is about",
        action="The action taken",
        severity="Severity level",
        reason="Reason for the case",
    )
    async def create_cmd(
        interaction: discord.Interaction,
        user: discord.User,
        action: str,
        severity: str = "medium",
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
            case_id = db.create_case(
                target_id=str(user.id),
                target_name=str(user),
                guild_id=str(interaction.guild.id),
                guild_name=interaction.guild.name,
                severity=severity,
                action_type=action,
                reason=reason or "No reason provided",
                created_by_id=str(interaction.user.id),
                created_by_name=str(interaction.user),
            )

            case = db.get_case(case_id)
            embed = _build_case_embed(case) if case else discord.Embed(title=f"Case {case_id} created")
            await interaction.response.send_message(embed=embed, ephemeral=False)

        except Exception:
            logger.exception("[case create] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /case view ────────────────────────────────────────────────────
    @case_group.command(
        name="view",
        description="View a case with all details",
    )
    @app_commands.describe(case_id="The case ID to view")
    async def view_cmd(interaction: discord.Interaction, case_id: str):
        try:
            await interaction.response.defer(ephemeral=True)
            db = get_db()
            case = db.get_case(case_id)
            if not case:
                return await interaction.followup.send(
                    f"Case `{case_id}` not found.", ephemeral=True,
                )

            embed = _build_case_embed(case)

            notes = db.get_notes(case_id)
            evidence = db.get_evidence(case_id)
            appeal = db.get_appeal(case_id)

            if notes:
                note_lines = []
                for n in notes[-5:]:
                    prefix = "🔒 " if n["is_internal"] else "📝 "
                    note_lines.append(
                        f"{prefix}**{n['author_name']}**: {n['content'][:200]}"
                    )
                embed.add_field(
                    name=f"Notes ({len(notes)} total, showing last {min(5, len(notes))})",
                    value="\n".join(note_lines)[:1024],
                    inline=False,
                )

            if evidence:
                ev_lines = []
                for e in evidence[:5]:
                    ev_lines.append(
                        f"📎 **{e['evidence_type'].replace('_', ' ').title()}**: "
                        f"{e['evidence_value'][:100]}"
                    )
                embed.add_field(
                    name=f"Evidence ({len(evidence)} items)",
                    value="\n".join(ev_lines)[:1024],
                    inline=False,
                )

            if appeal:
                status_icon = {"pending": "⏳", "approved": "✅", "denied": "❌"}
                icon = status_icon.get(appeal["status"], "❓")
                appeal_str = (
                    f"{icon} **Status:** {appeal['status'].title()}\n"
                    f"**Reason:** {appeal['reason'][:500]}"
                )
                if appeal["decision_reason"]:
                    appeal_str += f"\n**Decision:** {appeal['decision_reason'][:500]}"
                embed.add_field(name="Appeal", value=appeal_str[:1024], inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[case view] failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send("An error occurred.", ephemeral=True)

    # ── /case search ──────────────────────────────────────────────────
    @case_group.command(
        name="search",
        description="Search cases by target name, reason, or tags",
    )
    @app_commands.describe(query="Search query")
    async def search_cmd(interaction: discord.Interaction, query: str):
        try:
            await interaction.response.defer(ephemeral=True)
            db = get_db()
            cases = db.search_cases(query)
            if not cases:
                return await interaction.followup.send(
                    f"No cases found matching `{query}`.", ephemeral=True,
                )

            embed = _case_list_embed(cases)
            embed.title = f"Case Search: \"{query}\" ({len(cases)} found)"
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[case search] failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send("An error occurred.", ephemeral=True)

    # ── /case list ────────────────────────────────────────────────────
    @case_group.command(
        name="list",
        description="List cases, optionally filtered by status or severity",
    )
    @app_commands.describe(
        status="Filter by status",
        severity="Filter by severity",
        user="Filter by user",
    )
    async def list_cmd(
        interaction: discord.Interaction,
        status: str = "",
        severity: str = "",
        user: discord.User | None = None,
    ):
        try:
            await interaction.response.defer(ephemeral=True)
            db = get_db()

            if not interaction.guild:
                return await interaction.followup.send(
                    "This command can only be used in a server.", ephemeral=True,
                )

            target_id = str(user.id) if user else ""
            cases = db.find_cases(
                target_id=target_id,
                guild_id=str(interaction.guild.id),
                status=status,
                severity=severity,
                limit=25,
            )

            if not cases:
                msg = "No cases found"
                if status:
                    msg += f" with status `{status}`"
                if severity:
                    msg += f" with severity `{severity}`"
                if user:
                    msg += f" for {user}"
                return await interaction.followup.send(msg + ".", ephemeral=True)

            embed = _case_list_embed(cases)
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[case list] failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send("An error occurred.", ephemeral=True)

    # ── /case note ────────────────────────────────────────────────────
    @case_group.command(
        name="note",
        description="Add a note to a case",
    )
    @app_commands.describe(
        case_id="The case ID",
        content="Note content",
        internal="If true, only mods can see this note",
    )
    async def note_cmd(
        interaction: discord.Interaction,
        case_id: str,
        content: str,
        internal: bool = False,
    ):
        try:
            if not _mod_permission(interaction):
                return await interaction.response.send_message(
                    "You need `Moderate Members` permission.", ephemeral=True,
                )

            db = get_db()
            case = db.get_case(case_id)
            if not case:
                return await interaction.response.send_message(
                    f"Case `{case_id}` not found.", ephemeral=True,
                )

            db.add_note(
                case_id=case_id,
                author_id=str(interaction.user.id),
                author_name=str(interaction.user),
                content=content,
                is_internal=internal,
            )

            vis = "🔒 internal" if internal else "📝 public"
            await interaction.response.send_message(
                f"{vis} note added to case `{case_id}`.", ephemeral=True,
            )

        except Exception:
            logger.exception("[case note] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /case evidence ────────────────────────────────────────────────
    @case_group.command(
        name="evidence",
        description="Add evidence to a case",
    )
    @app_commands.describe(
        case_id="The case ID",
        evidence_type="Type of evidence",
        value="URL or text content",
        description="Optional description",
    )
    @app_commands.choices(evidence_type=[
        app_commands.Choice(name="Message Link", value="message_link"),
        app_commands.Choice(name="Image URL", value="image_url"),
        app_commands.Choice(name="File URL", value="file_url"),
        app_commands.Choice(name="Text", value="text"),
        app_commands.Choice(name="Screenshot", value="screenshot"),
    ])
    async def evidence_cmd(
        interaction: discord.Interaction,
        case_id: str,
        evidence_type: str,
        value: str,
        description: str = "",
    ):
        try:
            if not _mod_permission(interaction):
                return await interaction.response.send_message(
                    "You need `Moderate Members` permission.", ephemeral=True,
                )

            db = get_db()
            case = db.get_case(case_id)
            if not case:
                return await interaction.response.send_message(
                    f"Case `{case_id}` not found.", ephemeral=True,
                )

            db.add_evidence(case_id, evidence_type, value, description)
            await interaction.response.send_message(
                f"📎 Evidence added to case `{case_id}`.", ephemeral=True,
            )

        except Exception:
            logger.exception("[case evidence] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /case assign ──────────────────────────────────────────────────
    @case_group.command(
        name="assign",
        description="Assign a case to a moderator",
    )
    @app_commands.describe(
        case_id="The case ID",
        moderator="The moderator to assign this case to",
    )
    async def assign_cmd(
        interaction: discord.Interaction,
        case_id: str,
        moderator: discord.User,
    ):
        try:
            if not _mod_permission(interaction):
                return await interaction.response.send_message(
                    "You need `Moderate Members` permission.", ephemeral=True,
                )

            db = get_db()
            case = db.get_case(case_id)
            if not case:
                return await interaction.response.send_message(
                    f"Case `{case_id}` not found.", ephemeral=True,
                )

            db.update_case(
                case_id,
                assigned_to_id=str(moderator.id),
                assigned_to_name=str(moderator),
            )

            embed = discord.Embed(
                title=f"Case {case_id} Assigned",
                description=f"Assigned to {moderator.mention}",
                color=COLOR_INFO,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[case assign] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /case close ───────────────────────────────────────────────────
    @case_group.command(
        name="close",
        description="Close a case",
    )
    @app_commands.describe(
        case_id="The case ID",
        reason="Closing reason",
    )
    async def close_cmd(interaction: discord.Interaction, case_id: str, reason: str = ""):
        try:
            if not _mod_permission(interaction):
                return await interaction.response.send_message(
                    "You need `Moderate Members` permission.", ephemeral=True,
                )

            db = get_db()
            case = db.get_case(case_id)
            if not case:
                return await interaction.response.send_message(
                    f"Case `{case_id}` not found.", ephemeral=True,
                )

            now = time.time()
            db.update_case(
                case_id,
                status="closed",
                closed_by_id=str(interaction.user.id),
                closed_by_name=str(interaction.user),
                closed_at=now,
            )

            embed = discord.Embed(
                title=f"Case {case_id} Closed",
                color=COLOR_NEUTRAL,
            )
            if reason:
                embed.description = reason
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[case close] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /case reopen ──────────────────────────────────────────────────
    @case_group.command(
        name="reopen",
        description="Reopen a closed case",
    )
    @app_commands.describe(
        case_id="The case ID",
        reason="Reason for reopening",
    )
    async def reopen_cmd(interaction: discord.Interaction, case_id: str, reason: str = ""):
        try:
            if not _mod_permission(interaction):
                return await interaction.response.send_message(
                    "You need `Moderate Members` permission.", ephemeral=True,
                )

            db = get_db()
            case = db.get_case(case_id)
            if not case:
                return await interaction.response.send_message(
                    f"Case `{case_id}` not found.", ephemeral=True,
                )

            db.update_case(
                case_id,
                status="open",
                closed_by_id="",
                closed_by_name="",
                closed_at=0,
            )

            embed = discord.Embed(
                title=f"Case {case_id} Reopened",
                color=COLOR_WARNING,
            )
            if reason:
                embed.description = reason
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[case reopen] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /case appeal ──────────────────────────────────────────────────
    @case_group.command(
        name="appeal",
        description="Submit an appeal for a case",
    )
    @app_commands.describe(
        case_id="The case ID to appeal",
        reason="Reason for the appeal",
    )
    async def appeal_cmd(interaction: discord.Interaction, case_id: str, reason: str):
        try:
            db = get_db()
            case = db.get_case(case_id)
            if not case:
                return await interaction.response.send_message(
                    f"Case `{case_id}` not found.", ephemeral=True,
                )

            if case["status"] != "closed":
                return await interaction.response.send_message(
                    "Only closed cases can be appealed.", ephemeral=True,
                )

            success = db.create_appeal(
                case_id=case_id,
                reason=reason,
                appealed_by_id=str(interaction.user.id),
                appealed_by_name=str(interaction.user),
            )

            if not success:
                return await interaction.response.send_message(
                    "This case already has an appeal pending.", ephemeral=True,
                )

            embed = discord.Embed(
                title=f"Appeal Submitted for Case {case_id}",
                description=reason[:2000],
                color=COLOR_APPEAL,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[case appeal] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /case decide ──────────────────────────────────────────────────
    @case_group.command(
        name="decide",
        description="Decide on a pending appeal (admin only)",
    )
    @app_commands.describe(
        case_id="The case ID",
        decision="Approve or deny the appeal",
        reason="Decision reason",
    )
    @app_commands.choices(decision=[
        app_commands.Choice(name="Approve", value="approved"),
        app_commands.Choice(name="Deny", value="denied"),
    ])
    async def decide_cmd(
        interaction: discord.Interaction,
        case_id: str,
        decision: str,
        reason: str = "",
    ):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only admins can decide appeals.", ephemeral=True,
                )

            db = get_db()
            case = db.get_case(case_id)
            if not case:
                return await interaction.response.send_message(
                    f"Case `{case_id}` not found.", ephemeral=True,
                )

            appeal = db.get_appeal(case_id)
            if not appeal:
                return await interaction.response.send_message(
                    "This case has no appeal.", ephemeral=True,
                )
            if appeal["status"] != "pending":
                return await interaction.response.send_message(
                    "This appeal has already been decided.", ephemeral=True,
                )

            db.decide_appeal(
                case_id=case_id,
                status=decision,
                decision_reason=reason or f"Appeal {decision}",
                decided_by_id=str(interaction.user.id),
                decided_by_name=str(interaction.user),
            )

            embed = discord.Embed(
                title=f"Appeal {decision.title()} for Case {case_id}",
                color=COLOR_SUCCESS if decision == "approved" else COLOR_DANGER,
            )
            if reason:
                embed.description = reason
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[case decide] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /case optin ───────────────────────────────────────────────────
    @case_group.command(
        name="optin",
        description="Opt this server into the unified case system (admin only)",
    )
    @app_commands.describe(
        channel="Channel for case alerts (default: system channel)",
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
                title="Opted into Unified Case System",
                description=(
                    "This server is now participating in the shared case management system.\n\n"
                    "**What you can do:**\n"
                    "• Create and track cases across servers\n"
                    "• Add notes and evidence to cases\n"
                    "• Search case history by user\n"
                    "• Handle appeals through the system\n\n"
                    "Use `/case create` to start tracking."
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

        except Exception:
            logger.exception("[case optin] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /case optout ──────────────────────────────────────────────────
    @case_group.command(
        name="optout",
        description="Opt this server out of the unified case system (admin only)",
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
                title="Opted Out of Case System",
                description=(
                    "This server has left the unified case management system.\n\n"
                    "Existing cases are preserved but new cases will not be recorded."
                ),
                color=COLOR_NEUTRAL,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[case optout] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /case setalert ────────────────────────────────────────────────
    @case_group.command(
        name="setalert",
        description="Set the channel for case alerts (admin only)",
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
                    "This server is not opted in. Use `/case optin` first.",
                    ephemeral=True,
                )

            db.set_alert_channel(str(interaction.guild.id), str(channel.id))
            await interaction.response.send_message(
                f"Case alert channel set to {channel.mention}", ephemeral=True,
            )

        except Exception:
            logger.exception("[case setalert] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /case stats ───────────────────────────────────────────────────
    @case_group.command(
        name="stats",
        description="Show case management statistics",
    )
    async def stats_cmd(interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            db = get_db()
            stats = db.get_stats()

            embed = discord.Embed(
                title="Case Management Stats",
                color=COLOR_INFO,
            )
            embed.add_field(name="Total Cases", value=str(stats["total"]), inline=True)
            embed.add_field(name="Open / Investigating", value=str(stats["open"]), inline=True)
            embed.add_field(name="Closed / Resolved", value=str(stats["closed"]), inline=True)
            embed.add_field(name="Appealed", value=str(stats["appealed"]), inline=True)
            embed.add_field(name="Unique Users Tracked", value=str(stats["unique_targets"]), inline=True)
            embed.add_field(name="Servers Using Cases", value=str(stats["unique_guilds"]), inline=True)
            embed.add_field(name="Participating Servers", value=str(stats["opted_in"]), inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[case stats] failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send("An error occurred.", ephemeral=True)

    tree.add_command(case_group)
