"""Discord /trace slash command for scam DM source tracing.

When a user receives a DM from a suspicious bot or user, they run:
    /trace @suspicious_account

Azure will:
  1. Check the target's account age and profile signals
  2. Find mutual guilds between Azure, the reporter, and the target
  3. Scan audit logs in each guild for when/how the target was added
  4. Cross-reference against known scam bot patterns
  5. Return a detailed embed with the likely source and next steps
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.enums import AuditLogAction

if TYPE_CHECKING:
    pass

logger = logging.getLogger("azure.discord.trace")

# ── Known scam signals ──────────────────────────────────────────────────────
_SCAM_USERNAME_PATTERNS: list[str] = [
    r"^free\s*nitro",
    r"^nitro\s*giveaway",
    r"^steam\s*community",
    r"discord\s*nitro\s*",
    r"boost\s*server",
    r"free\s*robux",
    r"free\s*steam",
    r"giveaway",
    r"airdrop",
    r"claim\s*reward",
    r"verify\s*account",
]

SUSPICIOUS_AVATAR_HASHES: set[str] = set()  # populated from known scam reports

# ── Scam bot report database ────────────────────────────────────────────────
_REPORT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "scam_reports.jsonl"
_REPORT_DB_LOCK = asyncio.Lock()
_MAX_REPORT_AGE = 86400 * 90  # 90 days

# ── Colours ─────────────────────────────────────────────────────────────────
COLOR_DANGER = 0xED4245
COLOR_WARNING = 0xFEE75C
COLOR_SAFE = 0x57F287
COLOR_INFO = 0x5865F2


@dataclass
class ScamReport:
    """A report of a suspected scam/DM-spam account."""
    target_id: str
    target_name: str
    reporter_id: str
    reported_at: float
    source_guild_id: str | None
    source_guild_name: str | None
    notes: str = ""


@dataclass
class TraceResult:
    target_id: str
    target_name: str
    is_bot: bool
    account_age_days: float
    has_custom_avatar: bool
    username_suspicious: bool
    mutual_guilds_count: int
    guild_findings: list[dict] = field(default_factory=list)
    risk_score: int = 0  # 0-100
    likely_source: str | None = None
    known_reports: int = 0


# ── Database helpers ────────────────────────────────────────────────────────

async def _load_reports() -> list[ScamReport]:
    """Load all non-expired scam reports from the database."""
    if not _REPORT_DB_PATH.exists():
        return []
    reports = []
    now = time.time()
    async with _REPORT_DB_LOCK:
        try:
            text = await asyncio.to_thread(_REPORT_DB_PATH.read_text, encoding="utf-8")
            for line in text.strip().splitlines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    report = ScamReport(**data)
                    if now - report.reported_at < _MAX_REPORT_AGE:
                        reports.append(report)
                except (json.JSONDecodeError, TypeError):
                    continue
        except Exception:
            logger.exception("Failed to load scam reports")
    return reports


async def _save_report(report: ScamReport) -> None:
    """Append a scam report to the database."""
    _REPORT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with _REPORT_DB_LOCK:
        try:
            line = json.dumps(asdict(report)) + "\n"
            with open(_REPORT_DB_PATH, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            logger.exception("Failed to save scam report")


async def _count_reports_for_target(target_id: str) -> int:
    """Count how many reports exist for a given target user."""
    reports = await _load_reports()
    return sum(1 for r in reports if r.target_id == target_id)


# ── Analysis helpers ────────────────────────────────────────────────────────

def _check_username_pattern(name: str) -> bool:
    """Check if the target's username matches known scam patterns."""
    import re
    return any(re.search(pattern, name, re.IGNORECASE) for pattern in _SCAM_USERNAME_PATTERNS)


def _risk_label(score: int) -> tuple[str, int]:
    if score >= 70:
        return "HIGH", COLOR_DANGER
    if score >= 40:
        return "MEDIUM", COLOR_WARNING
    return "LOW", COLOR_SAFE


# ── The slash command ───────────────────────────────────────────────────────

def register_trace_commands(tree: app_commands.CommandTree) -> None:
    """Register the /trace slash command on the given command tree."""

    @tree.command(
        name="trace",
        description="Trace a suspicious DM bot/user and find which server it came from",
    )
    @app_commands.describe(
        user="The suspicious user or bot that DM'd you",
    )
    async def trace_cmd(
        interaction: discord.Interaction,
        user: discord.User,
    ) -> None:
        """Trace a suspicious account and identify the likely source server."""
        try:
            await interaction.response.defer(ephemeral=True)

            # ── Gather signals ────────────────────────────────────────────
            now = discord.utils.utcnow()
            account_age = (now - user.created_at).days
            is_bot = user.bot
            has_avatar = user.avatar is not None
            suspicious_name = _check_username_pattern(user.global_name or user.name)

            # Mutual guilds (guilds Azure shares with the target)
            mutuals = list(user.mutual_guilds)
            mutual_count = len(mutuals)

            # Filter to guilds where reporter is ALSO a member
            reporter = interaction.user
            relevant_mutuals: list[discord.Guild] = []
            for guild in mutuals:
                try:
                    member = guild.get_member(reporter.id)
                    if member is not None:
                        relevant_mutuals.append(guild)
                except Exception:
                    continue

            # ── Check audit logs in relevant guilds ──────────────────────
            guild_findings: list[dict] = []
            audit_log_tasks = []
            for guild in relevant_mutuals[:10]:  # limit to 10 for rate limits
                audit_log_tasks.append(_inspect_guild(guild, user))

            results = await asyncio.gather(*audit_log_tasks, return_exceptions=True)
            for guild, result in zip(relevant_mutuals[:10], results, strict=False):
                if isinstance(result, Exception):
                    guild_findings.append({
                        "guild_name": guild.name,
                        "guild_id": str(guild.id),
                        "error": str(result),
                        "added_at": None,
                        "reporter_present": True,
                    })
                else:
                    guild_findings.append(result)

            # ── Check known reports ───────────────────────────────────────
            known_reports = await _count_reports_for_target(str(user.id))

            # ── Calculate risk score ──────────────────────────────────────
            risk = 0
            if account_age < 7:
                risk += 30
            elif account_age < 30:
                risk += 20
            elif account_age < 90:
                risk += 10

            if is_bot:
                risk += 5
            if not has_avatar:
                risk += 15
            if suspicious_name:
                risk += 25
            if known_reports >= 3:
                risk += 25
            elif known_reports >= 1:
                risk += 15
            if mutual_count == 0:
                risk += 10  # no mutual guilds — harder to trace
            if mutual_count > 10 and is_bot:
                risk += 10  # bot in many servers — more likely to be spam

            # Find the most likely source (most recently added guild).
            # added_at is a formatted "%Y-%m-%d %H:%M UTC" string (lexically
            # sortable), or a "KICKED: ..." marker which is not an add event.
            likely_source = None
            newest_added = ""
            for finding in guild_findings:
                added = finding.get("added_at")
                if added and not added.startswith("KICKED:") and added > newest_added:
                    newest_added = added
                    likely_source = finding["guild_name"]

            risk = min(risk, 100)

            # ── Build embed ───────────────────────────────────────────────
            label, color = _risk_label(risk)
            embed = discord.Embed(
                title=f"Trace Report: {user}",
                description=(
                    f"**Risk Level:** {label} ({risk}/100)\n"
                    f"**Account Created:** {user.created_at.strftime('%Y-%m-%d')} "
                    f"({account_age} days ago)\n"
                    f"**Bot Account:** {'Yes' if is_bot else 'No'}\n"
                    f"**Custom Avatar:** {'Yes' if has_avatar else 'No'}\n"
                    f"**Suspicious Username:** {'Yes' if suspicious_name else 'No'}\n"
                    f"**Mutual Servers with You:** {len(relevant_mutuals)}\n"
                    f"**Known Reports:** {known_reports}"
                ),
                color=color,
            )
            embed.set_footer(text="Run in ephemeral • Your report is private")

            if likely_source:
                embed.add_field(
                    name="Likely Source Server",
                    value=f"**{likely_source}** — this looks like the most recent server the target joined.",
                    inline=False,
                )

            # Guild findings
            if guild_findings:
                findings_text = ""
                for gf in guild_findings[:5]:
                    if gf.get("error"):
                        findings_text += f"• {gf['guild_name']}: ⚠️ {gf['error']}\n"
                    elif gf.get("added_at"):
                        ts = gf["added_at"]
                        findings_text += f"• {gf['guild_name']}: joined {ts}\n"
                    else:
                        findings_text += f"• {gf['guild_name']}: no join record found\n"
                if findings_text:
                    embed.add_field(
                        name="Server Scan Results",
                        value=findings_text[:1024],
                        inline=False,
                    )

            # Recommended actions
            actions = []
            if risk >= 40:
                actions.append("🚫 Block the user — they cannot DM you after blocking")
                actions.append("📢 Check which servers have this bot and consider removing it")
            if likely_source:
                actions.append(f"🔍 Review Server Settings → Integrations in **{likely_source}**")
            actions.append("❓ Run `/trace` again later if more evidence surfaces")
            if actions:
                embed.add_field(
                    name="Recommended Actions",
                    value="\n".join(actions),
                    inline=False,
                )

            # Additional info
            if known_reports > 0:
                embed.add_field(
                    name="Community Intelligence",
                    value=f"This account has been reported {known_reports} time(s) before by other users.",
                    inline=False,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

            # ── Save report for future cross-references ───────────────────
            report = ScamReport(
                target_id=str(user.id),
                target_name=str(user),
                reporter_id=str(interaction.user.id),
                reported_at=time.time(),
                source_guild_id=str(relevant_mutuals[0].id) if relevant_mutuals else None,
                source_guild_name=likely_source,
            )
            await _save_report(report)

        except Exception:
            logger.exception("[trace] command failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send(
                    "An error occurred while tracing. Please try again later.",
                    ephemeral=True,
                )


async def _inspect_guild(guild: discord.Guild, target: discord.User) -> dict:
    """Inspect a single guild for info about when/how the target was added."""
    result = {
        "guild_name": guild.name,
        "guild_id": str(guild.id),
        "added_at": None,
        "error": None,
        "reporter_present": True,
    }
    try:
        # Check audit log for bot_add event (for bot targets)
        if target.bot:
            async for entry in guild.audit_logs(
                action=AuditLogAction.bot_add,
                limit=20,
            ):
                if entry.target and entry.target.id == target.id:
                    result["added_at"] = entry.created_at.strftime("%Y-%m-%d %H:%M UTC")
                    break

        # Check audit log for member_kick (if the target was previously kicked)
        if not result["added_at"]:
            async for entry in guild.audit_logs(
                action=AuditLogAction.kick,
                limit=20,
            ):
                if entry.target and entry.target.id == target.id:
                    result["added_at"] = f"KICKED: {entry.created_at.strftime('%Y-%m-%d %H:%M UTC')}"
                    break

        # Check audit log for member_prune (they got pruned)
        if not result["added_at"]:
            async for _entry in guild.audit_logs(
                action=AuditLogAction.member_prune,
                limit=20,
            ):
                # No easy way to match a specific user here, skip
                pass

    except discord.Forbidden:
        result["error"] = "bot lacks View Audit Log permission"
    except discord.HTTPException as e:
        result["error"] = f"rate limited or API error: {e}"
    except Exception as e:
        result["error"] = str(e)

    return result
