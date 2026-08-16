"""
Azure Server Health Analyzer

Analyzes a Discord server and provides recommendations on what's missing,
what's misconfigured, and what improvements can be made.

Also provides proactive suggestions after completing tasks.

Usage:
    from azure.server_health import ServerHealthAnalyzer
    analyzer = ServerHealthAnalyzer()
    report = await analyzer.analyze(guild)

    # Proactive suggestions after a task
    suggestions = analyzer.suggest_followups(guild, "created gaming channels")
"""

from __future__ import annotations

import logging

import discord

logger = logging.getLogger("azure.server_health")


class ServerHealthAnalyzer:
    """
    Analyzes Discord server health and provides actionable recommendations.
    """

    # Best practice checklists
    ESSENTIAL_CHANNELS = ["rules", "welcome", "announcements"]
    RECOMMENDED_CHANNELS = ["general", "off-topic", "introductions", "feedback"]
    ESSENTIAL_ROLES = ["Admin", "Moderator"]
    RECOMMENDED_ROLES = ["Bot", "Muted"]

    # Thresholds
    NO_MOD_CHANNEL_THRESHOLD = 50  # members without mod channel
    NO_RULES_THRESHOLD = 10       # members without rules channel

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # Full Analysis
    # ------------------------------------------------------------------

    async def analyze(self, guild: discord.Guild) -> dict:
        """
        Perform a full health analysis of a server.

        Returns:
            dict with categories, scores, issues, recommendations
        """
        report = {
            "server_name": guild.name,
            "member_count": guild.member_count,
            "score": 0,  # 0-100
            "categories": {},
            "issues": [],
            "recommendations": [],
            "followups": [],
        }

        # Structure analysis
        struct = self._analyze_structure(guild)
        report["categories"]["structure"] = struct
        report["issues"].extend(struct.get("issues", []))
        report["recommendations"].extend(struct.get("recommendations", []))

        # Security analysis
        security = self._analyze_security(guild)
        report["categories"]["security"] = security
        report["issues"].extend(security.get("issues", []))
        report["recommendations"].extend(security.get("recommendations", []))

        # Moderation analysis
        moderation = self._analyze_moderation(guild)
        report["categories"]["moderation"] = moderation
        report["issues"].extend(moderation.get("issues", []))
        report["recommendations"].extend(moderation.get("recommendations", []))

        # Engagement analysis
        engagement = self._analyze_engagement(guild)
        report["categories"]["engagement"] = engagement
        report["issues"].extend(engagement.get("issues", []))
        report["recommendations"].extend(engagement.get("recommendations", []))

        # Calculate overall score
        report["score"] = self._calculate_score(report)

        # Sort recommendations by priority
        report["recommendations"] = self._prioritize(report["recommendations"])
        report["followups"] = self._generate_followups(report)

        return report

    # ------------------------------------------------------------------
    # Category Analyzers
    # ------------------------------------------------------------------

    def _analyze_structure(self, guild: discord.Guild) -> dict:
        """Analyze server structure (channels, categories, roles)."""
        issues = []
        recommendations = []

        channel_names = [c.name.lower() for c in guild.channels]
        role_names = [r.name.lower() for r in guild.roles]

        # Check for essential channels
        missing_essential = [ch for ch in self.ESSENTIAL_CHANNELS if ch.lower() not in channel_names]
        if missing_essential:
            issues.append(f"Missing essential channels: {', '.join(missing_essential)}")
            recommendations.append({
                "priority": "high",
                "text": f"Create essential channels: {', '.join(missing_essential)}",
                "action": "create_channels",
                "channels": missing_essential,
            })

        # Check for recommended channels
        missing_recommended = [ch for ch in self.RECOMMENDED_CHANNELS if ch.lower() not in channel_names]
        if missing_recommended:
            recommendations.append({
                "priority": "medium",
                "text": f"Consider adding: {', '.join(missing_recommended)}",
                "action": "create_channels",
                "channels": missing_recommended,
            })

        # Check for categories
        if not guild.categories:
            issues.append("No channel categories — channels are disorganized")
            recommendations.append({
                "priority": "medium",
                "text": "Organize channels into categories (e.g., Info, General, Voice)",
                "action": "create_categories",
            })

        # Check for essential roles
        missing_roles = [r for r in self.ESSENTIAL_ROLES if r.lower() not in role_names]
        if missing_roles:
            issues.append(f"Missing essential roles: {', '.join(missing_roles)}")
            recommendations.append({
                "priority": "high",
                "text": f"Create moderation roles: {', '.join(missing_roles)}",
                "action": "create_roles",
                "roles": missing_roles,
            })

        # Check for too many uncategorized channels
        uncategorized = [c.name for c in guild.channels if not hasattr(c, "category") or c.category is None]
        if len(uncategorized) > 5:
            recommendations.append({
                "priority": "low",
                "text": f"{len(uncategorized)} channels are uncategorized — consider organizing them",
                "action": "organize_categories",
            })

        return {
            "issues": issues,
            "recommendations": recommendations,
            "score_component": max(0, 100 - len(issues) * 20 - len(missing_essential) * 15),
        }

    def _analyze_security(self, guild: discord.Guild) -> dict:
        """Analyze security settings."""
        issues = []
        recommendations = []

        # Verification level (compare by value to avoid enum version issues)
        try:
            verif_level = guild.verification_level
            if isinstance(verif_level, int):
                verif_val = verif_level
            else:
                verif_val = verif_level.value if hasattr(verif_level, 'value') else 0
        except Exception:
            verif_val = 0

        if verif_val == 0:  # none
            issues.append("No verification level — anyone can join and post immediately")
            recommendations.append({
                "priority": "high",
                "text": "Set verification level to Low (requires email verification)",
                "action": "set_verification_level",
                "level": "low",
            })
        elif verif_val == 1:  # low
            recommendations.append({
                "priority": "medium",
                "text": "Consider Medium verification for larger servers (requires 5 min membership)",
                "action": "set_verification_level",
                "level": "medium",
            })

        # Explicit content filter (compare by value to avoid enum version issues)
        try:
            content_filter = guild.explicit_content_filter
            if isinstance(content_filter, int):
                filter_val = content_filter
            else:
                filter_val = content_filter.value if hasattr(content_filter, 'value') else 0
        except Exception:
            filter_val = 0

        if filter_val == 0:  # disabled
            issues.append("Explicit content filter is disabled")
            recommendations.append({
                "priority": "medium",
                "text": "Enable explicit content filter for scanned images",
                "action": "set_content_filter",
                "filter": "members_without_roles",
            })

        # Default notifications (compare by value to avoid enum version issues)
        try:
            notif_level = guild.default_notifications
            if isinstance(notif_level, int):
                notif_val = notif_level
            else:
                notif_val = notif_level.value if hasattr(notif_level, 'value') else 0
        except Exception:
            notif_val = 0

        if notif_val == 0:  # all_messages
            recommendations.append({
                "priority": "low",
                "text": "Default notifications set to @everyone — consider @mentions only to reduce spam",
                "action": "set_notifications",
                "level": "mentions_only",
            })

        # 2FA requirement for moderation
        if not guild.mfa_level:
            recommendations.append({
                "priority": "medium",
                "text": "Consider requiring 2FA for moderation actions (Admin → Server Settings → Safety)",
                "action": "note_only",
            })

        return {
            "issues": issues,
            "recommendations": recommendations,
            "score_component": max(0, 100 - len(issues) * 25),
        }

    def _analyze_moderation(self, guild: discord.Guild) -> dict:
        """Analyze moderation setup."""
        issues = []
        recommendations = []

        # Check for admin/mod channels
        mod_channel_names = ["mod-log", "admin-log", "logs", "audit-log", "bot-log"]
        channel_names = [c.name.lower() for c in guild.channels]
        has_mod_channel = any(m in channel_names for m in mod_channel_names)

        if not has_mod_channel and guild.member_count > self.NO_MOD_CHANNEL_THRESHOLD:
            recommendations.append({
                "priority": "medium",
                "text": f"Server has {guild.member_count} members but no mod-log channel",
                "action": "create_channel",
                "channel": "mod-log",
            })

        # Check for auto-mod (Discord native)
        has_auto_mod = False
        try:
            # auto_moderation_rules is available in discord.py 2.1+
            if hasattr(guild, "auto_moderation_rules"):
                has_auto_mod = len(guild.auto_moderation_rules) > 0
        except Exception as e:
            logger.info(f"[server_health] auto_mod check skipped: {e}")


        if not has_auto_mod and guild.member_count > 50:
            recommendations.append({
                "priority": "medium",
                "text": "Consider setting up AutoMod rules (spam, mention spam, banned words)",
                "action": "setup_auto_mod",
            })

        # Check for mute role
        role_names = [r.name.lower() for r in guild.roles]
        if "muted" not in role_names and guild.member_count > 20:
            recommendations.append({
                "priority": "medium",
                "text": "Create a 'Muted' role for timeout functionality",
                "action": "create_role",
                "role": "Muted",
                "permissions": [],
            })

        return {
            "issues": issues,
            "recommendations": recommendations,
            "score_component": max(0, 100 - len(issues) * 25),
        }

    def _analyze_engagement(self, guild: discord.Guild) -> dict:
        """Analyze engagement features."""
        issues = []
        recommendations = []

        channel_names = [c.name.lower() for c in guild.channels]

        # Check for voice channels
        voice_channels = [c for c in guild.channels if c.type == discord.ChannelType.voice]
        if not voice_channels:
            recommendations.append({
                "priority": "medium",
                "text": "No voice channels — add at least one for community interaction",
                "action": "create_voice_channels",
            })

        # Check for stage channel (for larger communities)
        if guild.member_count > 100:
            stage_channels = [c for c in guild.channels if c.type == discord.ChannelType.stage_voice]
            if not stage_channels:
                recommendations.append({
                    "priority": "low",
                    "text": "Consider a Stage channel for announcements and community events",
                    "action": "create_stage_channel",
                })

        # Check for welcome/intro channel
        if "introductions" not in channel_names and "introduce-yourself" not in channel_names:
            recommendations.append({
                "priority": "low",
                "text": "Add an introductions channel so members can meet each other",
                "action": "create_channel",
                "channel": "introductions",
            })

        # Check for forum channel (for larger communities)
        if guild.member_count > 50:
            forum_channels = [c for c in guild.channels if c.type == discord.ChannelType.forum]
            if not forum_channels:
                recommendations.append({
                    "priority": "low",
                    "text": "Consider a Forum channel for organized discussions (e.g., 'Discussions' or 'Support')",
                    "action": "create_forum_channel",
                })

        return {
            "issues": issues,
            "recommendations": recommendations,
            "score_component": max(0, 100 - len(issues) * 25),
        }

    # ------------------------------------------------------------------
    # Scoring & Prioritization
    # ------------------------------------------------------------------

    def _calculate_score(self, report: dict) -> int:
        """Calculate overall health score (0-100)."""
        categories = report.get("categories", {})
        scores = [cat.get("score_component", 50) for cat in categories.values()]
        if not scores:
            return 50
        return int(sum(scores) / len(scores))

    def _prioritize(self, recommendations: list[dict]) -> list[dict]:
        """Sort recommendations by priority."""
        priority_order = {"high": 0, "medium": 1, "low": 2}
        return sorted(recommendations, key=lambda x: priority_order.get(x.get("priority", "low"), 2))

    def _generate_followups(self, report: dict) -> list[str]:
        """Generate quick follow-up suggestions based on the report."""
        followups = []
        recs = report.get("recommendations", [])

        # Group by action type
        actions = {}
        for r in recs:
            action = r.get("action", "")
            if action not in actions:
                actions[action] = []
            actions[action].append(r)

        # Generate natural followups
        if "create_channels" in actions:
            channels = []
            for r in actions["create_channels"]:
                channels.extend(r.get("channels", []))
            if channels:
                followups.append(f"Want me to create the missing channels? ({', '.join(channels[:3])})")

        if "create_roles" in actions:
            roles = []
            for r in actions["create_roles"]:
                roles.extend(r.get("roles", []))
            if roles:
                followups.append(f"Should I set up the moderation roles? ({', '.join(roles)})")

        if "create_categories" in actions:
            followups.append("Want me to organize channels into categories?")

        if "set_verification_level" in actions:
            followups.append("Should I tighten the security settings?")

        if "setup_auto_mod" in actions:
            followups.append("Want me to set up AutoMod for spam and banned words?")

        return followups

    # ------------------------------------------------------------------
    # Proactive Suggestions (After Task Completion)
    # ------------------------------------------------------------------

    def suggest_followups(self, guild: discord.Guild, completed_task: str) -> list[str]:
        """
        Suggest follow-up actions after completing a task.

        Args:
            guild: The Discord guild
            completed_task: Description of what was just done (e.g., "created gaming channels")

        Returns:
            List of natural language suggestion strings.
        """
        suggestions = []
        task_lower = completed_task.lower()
        channel_names = [c.name.lower() for c in guild.channels]

        # Gaming-related followups
        if "gaming" in task_lower or "game" in task_lower:
            if "looking-for-group" not in channel_names:
                suggestions.append("Want me to add a 'looking-for-group' channel?")
            if not any("voice" in c.name.lower() for c in guild.channels if c.type == discord.ChannelType.voice):
                suggestions.append("Should I add voice channels for squads?")
            if "clips" not in " ".join(channel_names):
                suggestions.append("A 'clips-and-highlights' channel would be great for sharing gameplay.")

        # Role-related followups
        if "role" in task_lower:
            suggestions.append("Want me to assign these roles to specific members?")
            role_names = [r.name.lower() for r in guild.roles]
            if "muted" not in role_names:
                suggestions.append("Should I also create a 'Muted' role for moderation?")

        # Channel creation followups
        if "channel" in task_lower:
            if not guild.categories:
                suggestions.append("Should I organize these channels into categories?")
            suggestions.append("Want me to set permissions on these channels so only certain roles can access them?")

        # Category creation followups
        if "category" in task_lower:
            suggestions.append("Should I move existing channels into these categories?")

        # General followups
        if guild.member_count > 50 and not any("rules" in c for c in channel_names):
            suggestions.append("With this many members, you might want a rules channel. Want me to create one?")

        if not suggestions:
            suggestions.append("Is there anything else you'd like me to adjust?")

        return suggestions[:3]  # Cap at 3 suggestions

    # ------------------------------------------------------------------
    # Formatting for Discord
    # ------------------------------------------------------------------

    def format_report(self, report: dict) -> str:
        """Format analysis report as Discord-friendly text."""
        score = report.get("score", 50)
        score_emoji = "🟢" if score >= 80 else "🟡" if score >= 50 else "🔴"

        lines = [
            f"## {score_emoji} Server Health Report: {report.get('server_name', 'Unknown')}",
            f"**Score:** {score}/100 | **Members:** {report.get('member_count', 0)}\n",
        ]

        # Issues
        issues = report.get("issues", [])
        if issues:
            lines.append("**⚠️ Issues Found:**")
            for issue in issues:
                lines.append(f"• {issue}")
            lines.append("")

        # Recommendations
        recs = report.get("recommendations", [])[:5]  # Top 5
        if recs:
            lines.append("**💡 Recommendations:**")
            for rec in recs:
                priority = rec.get("priority", "low")
                emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "⚪")
                lines.append(f"{emoji} {rec['text']}")
            lines.append("")

        # Followups
        followups = report.get("followups", [])
        if followups:
            lines.append("**🚀 Quick Actions:**")
            for i, f in enumerate(followups, 1):
                lines.append(f"{i}. {f}")

        return "\n".join(lines)
