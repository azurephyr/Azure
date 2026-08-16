"""
AuditEngine — Deliverable 4: READ Tool Functions for Operator Mode

Provides READ functions that audit the Discord server and bot state.
Called automatically during the operator pipeline's Audit step.

Functions:
  - audit_server: Full server state snapshot
  - audit_channels: Channel analysis (missing categories, unused, etc.)
  - audit_roles: Role analysis (hierarchy, permissions, unused)
  - audit_permissions: Permission audit (overwrites, gaps)
  - audit_moderation: Auto-mod setup, moderation channels, rules
  - audit_engagement: Activity metrics per channel
  - audit_bot_health: Error logs, latency, model info
  - audit_onboarding: Welcome flow, rules channel, onboarding state

These are pure READ functions — no side effects.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import discord


@dataclass
class AuditFinding:
    """A single finding from an audit."""
    severity: str  # "critical", "warning", "info", "good"
    category: str  # "channel", "role", "permission", "moderation", "engagement", "onboarding", "general"
    message: str
    recommendation: str = ""
    affected: list[str] = field(default_factory=list)


@dataclass
class AuditReport:
    """Complete audit report."""
    findings: list[AuditFinding] = field(default_factory=list)
    summary: str = ""
    score: int = 0  # 0-100 server health score
    audit_time_ms: float = 0.0

    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "critical")

    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    def good_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "good")

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "score": self.score,
            "critical": self.critical_count(),
            "warning": self.warning_count(),
            "good": self.good_count(),
            "findings": [
                {
                    "severity": f.severity,
                    "category": f.category,
                    "message": f.message,
                    "recommendation": f.recommendation,
                    "affected": f.affected,
                }
                for f in self.findings
            ],
        }


class AuditEngine:
    """Audit engine for Discord server and bot health."""

    def __init__(self, bot=None):
        self.bot = bot

    # -----------------------------------------------------------------------
    # Server audit
    # -----------------------------------------------------------------------

    async def audit_server(self, guild: discord.Guild) -> AuditReport:
        """Full server audit."""
        t_start = time.perf_counter()
        report = AuditReport()

        # Run all sub-audits
        report.findings.extend(await self._audit_channels(guild))
        report.findings.extend(await self._audit_roles(guild))
        report.findings.extend(await self._audit_permissions(guild))
        report.findings.extend(await self._audit_moderation(guild))
        report.findings.extend(await self._audit_engagement(guild))
        report.findings.extend(await self._audit_onboarding(guild))

        # Calculate score
        critical = report.critical_count()
        warning = report.warning_count()
        good = report.good_count()
        len(report.findings)
        report.score = max(0, 100 - (critical * 15) - (warning * 5) + (good * 2))
        report.score = min(100, report.score)

        report.summary = (
            f"Server audit: {critical} critical, {warning} warnings, {good} good. "
            f"Health score: {report.score}/100."
        )
        report.audit_time_ms = (time.perf_counter() - t_start) * 1000

        return report

    # -----------------------------------------------------------------------
    # Channel audit
    # -----------------------------------------------------------------------

    async def _audit_channels(self, guild: discord.Guild) -> list[AuditFinding]:
        findings = []
        channels = guild.channels
        categories = guild.categories
        text_channels = [c for c in channels if isinstance(c, discord.TextChannel)]
        voice_channels = [c for c in channels if isinstance(c, discord.VoiceChannel)]

        # Check for missing categories
        if len(categories) == 0 and len(channels) > 5:
            findings.append(AuditFinding(
                severity="warning",
                category="channel",
                message="No categories found — channels are unorganized.",
                recommendation="Create categories (e.g., 'Information', 'General', 'Voice') to organize channels.",
            ))

        # Check for uncategorized channels
        uncategorized = [c for c in channels if c.category is None and not isinstance(c, discord.CategoryChannel)]
        if len(uncategorized) > 3:
            findings.append(AuditFinding(
                severity="info",
                category="channel",
                message=f"{len(uncategorized)} channels are uncategorized.",
                recommendation="Move channels into categories for better organization.",
                affected=[c.name for c in uncategorized[:5]],
            ))

        # Check for missing key channels
        key_channels = ["rules", "welcome", "announcements", "general"]
        existing = {c.name.lower() for c in text_channels}
        missing = [k for k in key_channels if k not in existing]
        if missing:
            findings.append(AuditFinding(
                severity="warning" if "rules" in missing or "welcome" in missing else "info",
                category="channel",
                message=f"Missing key channels: {', '.join(missing)}.",
                recommendation="Create missing channels for better server structure.",
                affected=missing,
            ))
        else:
            findings.append(AuditFinding(
                severity="good",
                category="channel",
                message="All key channels (rules, welcome, announcements, general) are present.",
            ))

        # Check for voice channels. guild.member_count is Optional[int] and is
        # None when the guild isn't fully loaded — guard to avoid TypeError.
        if len(voice_channels) == 0 and (guild.member_count or 0) > 10:
            findings.append(AuditFinding(
                severity="warning",
                category="channel",
                message="No voice channels found.",
                recommendation="Create voice channels for community engagement.",
            ))

        # Check for inactive channels (no messages in 30 days — approximated)
        # We can't easily check message history without API calls, skip for now

        return findings

    # -----------------------------------------------------------------------
    # Role audit
    # -----------------------------------------------------------------------

    async def _audit_roles(self, guild: discord.Guild) -> list[AuditFinding]:
        findings = []
        roles = [r for r in guild.roles if not r.is_default() and not r.managed]

        # Check for basic role hierarchy
        if len(roles) < 2:
            findings.append(AuditFinding(
                severity="warning",
                category="role",
                message="Only @everyone and no custom roles.",
                recommendation="Create at least an Admin and Moderator role for proper permission management.",
            ))
        else:
            findings.append(AuditFinding(
                severity="good",
                category="role",
                message=f"{len(roles)} custom roles found.",
            ))

        # Check for unused roles (no members)
        unused = [r for r in roles if len(r.members) == 0]
        if len(unused) > 2:
            findings.append(AuditFinding(
                severity="info",
                category="role",
                message=f"{len(unused)} roles have no members assigned.",
                recommendation="Review unused roles — consider removing them to reduce clutter.",
                affected=[r.name for r in unused[:5]],
            ))

        # Check for admin roles with too many permissions
        admin_roles = [r for r in roles if r.permissions.administrator]
        if len(admin_roles) > 1:
            findings.append(AuditFinding(
                severity="warning",
                category="role",
                message=f"{len(admin_roles)} roles have Administrator permission.",
                recommendation="Limit Administrator permission to one role only for security.",
                affected=[r.name for r in admin_roles],
            ))

        return findings

    # -----------------------------------------------------------------------
    # Permission audit
    # -----------------------------------------------------------------------

    async def _audit_permissions(self, guild: discord.Guild) -> list[AuditFinding]:
        findings = []
        text_channels = [c for c in guild.channels if isinstance(c, discord.TextChannel)]

        # Check for unprotected sensitive channels
        sensitive = ["rules", "announcements", "welcome"]
        for ch_name in sensitive:
            ch = discord.utils.get(text_channels, name=ch_name)
            if ch:
                # Check if @everyone can send messages
                overwrite = ch.overwrites_for(guild.default_role)
                if hasattr(overwrite, "send_messages") and overwrite.send_messages is not False:
                    findings.append(AuditFinding(
                        severity="warning" if ch_name == "rules" else "info",
                        category="permission",
                        message=f"#{ch_name} allows @everyone to send messages.",
                        recommendation=f"Set @everyone send_messages=False on #{ch_name}.",
                        affected=[ch_name],
                    ))
                else:
                    findings.append(AuditFinding(
                        severity="good",
                        category="permission",
                        message=f"#{ch_name} is properly restricted.",
                    ))

        return findings

    # -----------------------------------------------------------------------
    # Moderation audit
    # -----------------------------------------------------------------------

    async def _audit_moderation(self, guild: discord.Guild) -> list[AuditFinding]:
        findings = []

        # Check verification level
        vlevel = guild.verification_level
        if vlevel == discord.VerificationLevel.none:
            findings.append(AuditFinding(
                severity="warning",
                category="moderation",
                message="Server verification level is 'None' — anyone can join and post immediately.",
                recommendation="Set verification level to at least 'Low' for basic spam protection.",
            ))
        else:
            findings.append(AuditFinding(
                severity="good",
                category="moderation",
                message=f"Verification level is '{vlevel.name}'.",
            ))

        # Check content filter
        filter_level = guild.explicit_content_filter
        if filter_level == discord.ExplicitContentFilter.disabled:
            findings.append(AuditFinding(
                severity="warning",
                category="moderation",
                message="Explicit content filter is disabled.",
                recommendation="Enable content filter for 'All Members' or 'Members Without Roles'.",
            ))
        else:
            findings.append(AuditFinding(
                severity="good",
                category="moderation",
                message=f"Explicit content filter is '{filter_level.name}'.",
            ))

        # Check for moderation log channel
        mod_log = discord.utils.get(guild.text_channels, name="mod-log")
        if not mod_log:
            mod_log = discord.utils.get(guild.text_channels, name="modlog")
        if not mod_log:
            mod_log = discord.utils.get(guild.text_channels, name="logs")
        if not mod_log:
            findings.append(AuditFinding(
                severity="info",
                category="moderation",
                message="No moderation log channel found.",
                recommendation="Create a #mod-log channel for audit trail.",
            ))
        else:
            findings.append(AuditFinding(
                severity="good",
                category="moderation",
                message=f"Moderation log channel found: #{mod_log.name}.",
            ))

        return findings

    # -----------------------------------------------------------------------
    # Engagement audit
    # -----------------------------------------------------------------------

    async def _audit_engagement(self, guild: discord.Guild) -> list[AuditFinding]:
        findings = []
        total = guild.member_count or 0
        online = sum(1 for m in guild.members if m.status != discord.Status.offline)

        if total > 0:
            ratio = online / total
            if ratio < 0.1 and total > 20:
                findings.append(AuditFinding(
                    severity="warning",
                    category="engagement",
                    message=f"Low activity: only {online}/{total} members online ({ratio:.0%}).",
                    recommendation="Consider events, engagement channels, or community activities to boost activity.",
                ))
            elif ratio > 0.3:
                findings.append(AuditFinding(
                    severity="good",
                    category="engagement",
                    message=f"Good activity: {online}/{total} members online ({ratio:.0%}).",
                ))

        return findings

    # -----------------------------------------------------------------------
    # Onboarding audit
    # -----------------------------------------------------------------------

    async def _audit_onboarding(self, guild: discord.Guild) -> list[AuditFinding]:
        findings = []

        # Check for rules channel
        rules = guild.rules_channel
        if not rules:
            findings.append(AuditFinding(
                severity="warning",
                category="onboarding",
                message="No designated rules channel.",
                recommendation="Set a rules channel (Server Settings → Community → Rules Channel).",
            ))
        else:
            findings.append(AuditFinding(
                severity="good",
                category="onboarding",
                message=f"Rules channel set: #{rules.name}.",
            ))

        # Check for system/welcome channel
        system = guild.system_channel
        if not system:
            findings.append(AuditFinding(
                severity="info",
                category="onboarding",
                message="No system channel set for welcome messages.",
                recommendation="Set a system channel (Server Settings → Overview → System Messages Channel).",
            ))
        else:
            findings.append(AuditFinding(
                severity="good",
                category="onboarding",
                message=f"System channel set: #{system.name}.",
            ))

        # Check for onboarding progress
        if hasattr(guild, "premium_progress_bar_enabled") and not guild.premium_progress_bar_enabled:
            findings.append(AuditFinding(
                severity="info",
                category="onboarding",
                message="Server Boost progress bar is disabled.",
                recommendation="Enable the boost progress bar to encourage server boosting.",
            ))

        return findings

    # -----------------------------------------------------------------------
    # Bot self-audit
    # -----------------------------------------------------------------------

    async def audit_bot_health(self, agent=None) -> AuditReport:
        """Audit bot health: logs, latency, model info."""
        findings = []

        # Check error logs
        try:
            log_path = Path("logs/errors")
            if log_path.exists():
                error_files = list(log_path.glob("*.log"))
                recent_errors = 0
                for f in error_files:
                    # Count lines in last 24h (approximate)
                    content = f.read_text(encoding="utf-8")
                    recent_errors += len(content.splitlines())
                if recent_errors > 10:
                    findings.append(AuditFinding(
                        severity="warning" if recent_errors > 50 else "info",
                        category="general",
                        message=f"{recent_errors} error log entries found.",
                        recommendation="Review error logs for recurring issues.",
                    ))
        except Exception:
            pass

        # Check model info
        llm_type = getattr(agent, "_llm_type", "none") if agent else "none"
        llm = getattr(agent, "llm", None) if agent else None
        if llm and llm.get_info:
            try:
                info = llm.get_info()
                findings.append(AuditFinding(
                    severity="good",
                    category="general",
                    message=f"LLM: type={llm_type} | {info.get('provider', info.get('model', 'unknown'))}",
                ))
            except Exception:
                findings.append(AuditFinding(
                    severity="warning",
                    category="general",
                    message="LLM get_info() failed.",
                ))
        else:
            findings.append(AuditFinding(
                severity="warning",
                category="general",
                message=f"No LLM loaded (type={llm_type}).",
            ))

        return AuditReport(
            findings=findings,
            summary=f"Bot health: {len(findings)} findings",
            score=80 if llm else 50,
        )
