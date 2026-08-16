"""Server state and utility tools."""
import asyncio
import contextlib
import datetime
import io
import logging
from dataclasses import dataclass

import discord

from .types import StepResult

logger = logging.getLogger("tools.server_tools")


# ---------------------------------------------------------------------------
# Server Health Analyzer — deep metrics, grading, and recommendations
# ---------------------------------------------------------------------------

@dataclass
class ServerHealthReport:
    """Comprehensive server health analysis with letter grades."""
    server_name: str
    member_count: int
    online_count: int
    overall_grade: str          # A, B, C, D, F
    overall_score: float        # 0.0 – 100.0
    activity: dict              # grade, score, detail
    engagement: dict            # grade, score, detail
    moderation: dict            # grade, score, detail
    organization: dict          # grade, score, detail
    security: dict              # grade, score, detail
    recommendations: list[str]  # prioritized action items
    quick_wins: list[str]       # easy fixes
    findings: list[dict]        # detailed findings with severity


class ServerHealthAnalyzer:
    """Analyzes a Discord server's health and produces actionable reports."""

    @staticmethod
    def analyze(guild: discord.Guild, message_history: list | None = None) -> ServerHealthReport:
        """Full server health analysis."""

        # Gather raw data
        members = guild.members
        online = sum(1 for m in members if m.status != discord.Status.offline)
        channels = guild.channels
        roles = guild.roles

        # 1. Activity score
        active_channels = sum(1 for c in channels if hasattr(c, 'last_message_id') and c.last_message_id)
        channel_utilization = (active_channels / max(len(channels), 1)) * 100
        online_ratio = (online / max(guild.member_count, 1)) * 100
        activity_score = min(100, (channel_utilization * 0.5 + online_ratio * 0.5) * 1.5)

        # 2. Engagement score
        bot_count = sum(1 for m in members if m.bot)
        member_count = max(guild.member_count - bot_count, 1)
        # Check roles beyond everyone
        meaningful_roles = [r for r in roles if not r.is_default() and not r.managed]
        role_participation = min(100, (len(meaningful_roles) / max(member_count, 1)) * 500)
        engagement_score = min(100, role_participation * 0.7 + (online_ratio * 0.3))

        # 3. Organization score
        has_categories = len(guild.categories) > 0
        has_rules = guild.rules_channel is not None
        has_system_channel = guild.system_channel is not None
        has_afk = guild.afk_channel is not None
        org_score = sum([
            30 if has_categories else 0,
            25 if has_rules else 0,
            25 if has_system_channel else 0,
            20 if has_afk else 0,
        ])

        # 4. Security score
        sec_score = 0
        if guild.verification_level >= discord.VerificationLevel.medium:
            sec_score += 40
        elif guild.verification_level >= discord.VerificationLevel.low:
            sec_score += 20
        try:
            _ecf = discord.ExplicitContentFilter
        except AttributeError:
            _ecf = discord.ContentFilter
        if guild.explicit_content_filter == _ecf.all_members:
            sec_score += 40
        elif guild.explicit_content_filter >= _ecf.no_role:
            sec_score += 30
        if hasattr(guild, 'mfa_level') and guild.mfa_level:
            sec_score += 20

        # 5. Moderation score (based on server settings, not async audit log)
        mod_score = 60.0
        if hasattr(guild, 'mfa_level') and guild.mfa_level:
            mod_score += 15
        if guild.explicit_content_filter != _ecf.disabled:
            mod_score += 15

        # Overall score
        overall = activity_score * 0.2 + engagement_score * 0.2 + org_score * 0.2 + sec_score * 0.2 + mod_score * 0.2

        # Grade
        def _grade(s: float) -> str:
            if s >= 90:
                return "A"
            if s >= 80:
                return "B"
            if s >= 65:
                return "C"
            if s >= 50:
                return "D"
            return "F"

        # Build recommendations
        recs: list[str] = []
        quick_wins: list[str] = []

        if not has_categories:
            recs.append("Create channel categories to organize your channels")
            quick_wins.append("Group related channels into categories (e.g., #welcome → Info category)")
        if not has_rules:
            recs.append("Set a rules channel — essential for onboarding and moderation")
            quick_wins.append("Create #rules and set it as the server's rules channel")
        if not has_system_channel:
            recs.append("Set a system message channel for join/leave messages")
            quick_wins.append("Pick an existing channel as the system channel in Server Settings")
        if guild.verification_level < discord.VerificationLevel.medium:
            recs.append("Increase verification level to at least Medium (requires verified email)")
            quick_wins.append("Go to Server Settings → Safety Setup → Verification Level → Medium")
        if activity_score < 40:
            recs.append("Server activity is low — consider events, announcements, or engaging prompts")
        if engagement_score < 30:
            recs.append("Low role distribution — create meaningful roles members want to earn")
            quick_wins.append("Add fun/optional roles like colors, game roles, or level roles")
        if online_ratio < 15:
            recs.append("Low online ratio — consider posting announcements when members are most active")

        # Detailed findings
        findings: list[dict] = []

        # Member insights
        bot_pct = (bot_count / max(guild.member_count, 1)) * 100
        if bot_pct > 30:
            findings.append({"severity": "warning", "category": "members", "message": f"{bot_pct:.0f}% of members are bots"})

        # Channel insights
        text_channels = sum(1 for c in channels if isinstance(c, discord.TextChannel))
        voice_channels = sum(1 for c in channels if isinstance(c, discord.VoiceChannel))
        if text_channels < 3:
            findings.append({"severity": "info", "category": "channels", "message": "Consider adding more text channels for different topics"})
        if voice_channels < 1:
            findings.append({"severity": "info", "category": "channels", "message": "No voice channels — consider adding at least one for voice chat"})

        return ServerHealthReport(
            server_name=guild.name,
            member_count=guild.member_count,
            online_count=online,
            overall_grade=_grade(overall),
            overall_score=round(overall, 1),
            activity={"grade": _grade(activity_score), "score": round(activity_score, 1), "detail": f"{active_channels}/{len(channels)} channels active, {online}/{guild.member_count} online"},
            engagement={"grade": _grade(engagement_score), "score": round(engagement_score, 1), "detail": f"{len(meaningful_roles)} non-default roles, {online_ratio:.0f}% online"},
            moderation={"grade": _grade(mod_score), "score": round(mod_score, 1), "detail": f"Verification: {guild.verification_level}, MFA: {'on' if hasattr(guild, 'mfa_level') and guild.mfa_level else 'off'}"},
            organization={"grade": _grade(org_score), "score": round(org_score, 1), "detail": f"{'Categories: yes' if has_categories else 'No categories'}, {'Rules: yes' if has_rules else 'No rules channel'}"},
            security={"grade": _grade(sec_score), "score": round(sec_score, 1), "detail": f"Verification: {guild.verification_level}"},
            recommendations=recs,
            quick_wins=quick_wins,
            findings=findings,
        )

    @staticmethod
    def format_report(report: ServerHealthReport) -> str:
        """Format a health report as a readable Discord message."""
        lines = [
            f"**Server Health Report — {report.server_name}**",
            f"**Overall Grade: {report.overall_grade}** ({report.overall_score}/100)",
            f"Members: {report.member_count} ({report.online_count} online)",
            "",
            "**Category Scores:**",
            f"  Activity: {report.activity['grade']} ({report.activity['score']}) — {report.activity['detail']}",
            f"  Engagement: {report.engagement['grade']} ({report.engagement['score']}) — {report.engagement['detail']}",
            f"  Moderation: {report.moderation['grade']} ({report.moderation['score']}) — {report.moderation['detail']}",
            f"  Organization: {report.organization['grade']} ({report.organization['score']}) — {report.organization['detail']}",
            f"  Security: {report.security['grade']} ({report.security['score']}) — {report.security['detail']}",
        ]
        if report.recommendations:
            lines.extend(["", "**Recommendations:**"] + [f"  {i + 1}. {r}" for i, r in enumerate(report.recommendations)])
        if report.quick_wins:
            lines.extend(["", "**Quick Wins:**"] + [f"  ⚡ {q}" for q in report.quick_wins])
        if report.findings:
            lines.extend(["", "**Additional Findings:**"] + [f"  {'⚠️' if f['severity'] == 'warning' else 'ℹ️'} {f['message']}" for f in report.findings])
        return "\n".join(lines)


def _resolve_color(color_name: str) -> int:
    basic = {
        "red": 0xE74C3C, "blue": 0x3498DB, "green": 0x2ECC71,
        "yellow": 0xF1C40F, "purple": 0x9B59B6, "orange": 0xE67E22,
        "pink": 0xE91E63, "white": 0xFFFFFF, "black": 0x000000,
        "grey": 0x95A5A6, "gray": 0x95A5A6, "brown": 0x8B4513,
        "cyan": 0x00FFFF, "magenta": 0xFF00FF, "lime": 0x00FF00,
    }
    if color_name is None:
        return 0x99AAB5
    return basic.get(color_name.lower().strip(), 0x99AAB5)


def _llm_reason(action: str, context: str = "") -> str:
    return f"Azure: {action} - {context}" if context else f"Azure: {action}"


def _embed_color(status: str = "info") -> int:
    colors = {"info": 0x3498DB, "success": 0x2ECC71, "warning": 0xE67E22, "error": 0xE74C3C}
    return colors.get(status, 0x3498DB)


class ServerToolsMixin:
    """Mixin providing server state, settings, and utility methods for DiscordManagementTools."""

    async def get_server_state(self, guild: discord.Guild) -> dict:
        roles = []
        for r in guild.roles:
            if r.is_default() or r.managed:
                continue
            roles.append({
                "name": r.name, "color": str(r.color), "position": r.position,
                "member_count": len(r.members), "hoist": r.hoist,
                "mentionable": r.mentionable,
                "permissions": [p[0] for p in r.permissions if p[1]],
            })

        channels = []
        for c in guild.channels:
            ch = {"name": c.name, "type": str(c.type), "id": c.id, "position": c.position}
            if hasattr(c, "category") and c.category:
                ch["category"] = c.category.name
            if hasattr(c, "topic"):
                ch["topic"] = c.topic or ""
            if hasattr(c, "slowmode_delay"):
                ch["slowmode"] = c.slowmode_delay
            if hasattr(c, "nsfw"):
                ch["nsfw"] = c.nsfw
            if hasattr(c, "bitrate"):
                ch["bitrate"] = c.bitrate
            if hasattr(c, "user_limit"):
                ch["user_limit"] = c.user_limit
            # Include permission overrides for auditing
            overrides = []
            for target, overwrite in c.overwrites.items():
                if overwrite.is_empty():
                    continue
                allow = [p[0] for p in overwrite.pair()[0] if p[1] is True]
                deny = [p[0] for p in overwrite.pair()[1] if p[1] is True]
                if allow or deny:
                    overrides.append({
                        "target": getattr(target, "name", str(target)),
                        "type": "role" if isinstance(target, discord.Role) else "member",
                        "allow": allow,
                        "deny": deny,
                    })
            if overrides:
                ch["overrides"] = overrides
            channels.append(ch)

        categories = []
        for cat in guild.categories:
            categories.append({
                "name": cat.name, "position": cat.position,
                "channels": [c.name for c in cat.channels],
            })

        members = guild.member_count
        online = sum(1 for m in guild.members if m.status != discord.Status.offline)

        return {
            "server_name": guild.name, "member_count": members,
            "online_count": online, "roles": roles, "channels": channels,
            "categories": categories,
            "verification_level": str(guild.verification_level),
            "default_notifications": str(guild.default_notifications),
            "explicit_content_filter": str(guild.explicit_content_filter),
        }

    def _parse_color(self, color: str | int | None) -> int:
        if color is None:
            return 0
        if isinstance(color, int):
            return color
        if isinstance(color, str):
            color = color.lower().strip().replace("#", "")
            try:
                return int(color, 16)
            except ValueError:
                return _resolve_color(color)
        return 0

    def _build_permissions(self, perm_list: list[str]) -> discord.Permissions:
        perms = discord.Permissions()
        for p in perm_list:
            p_lower = p.lower().strip()
            if hasattr(perms, p_lower):
                setattr(perms, p_lower, True)
        return perms

    async def _resolve_member(self, guild: discord.Guild, identifier: str) -> discord.Member | None:
        if not identifier:
            return None
        try:
            uid = int(identifier)
            return guild.get_member(uid) or await guild.fetch_member(uid)
        except ValueError:
            pass
        identifier_lower = identifier.lower()
        for m in guild.members:
            if m.name.lower() == identifier_lower or m.display_name.lower() == identifier_lower:
                return m
        if identifier.startswith("<@") and identifier.endswith(">"):
            try:
                uid = int(identifier.replace("<@", "").replace(">", "").replace("!", ""))
                return guild.get_member(uid) or await guild.fetch_member(uid)
            except ValueError:
                pass
        return None

    async def create_webhook(self, guild: discord.Guild, channel_name: str, webhook_name: str = "Azure Webhook") -> StepResult:
        try:
            ch = discord.utils.get(guild.channels, name=channel_name)
            if not ch:
                return StepResult(success=False, action="create_webhook", name=webhook_name, error=f"Channel '{channel_name}' not found")
            if not isinstance(ch, discord.TextChannel):
                return StepResult(success=False, action="create_webhook", name=webhook_name, error="Webhooks only work in text channels")
            webhook = await ch.create_webhook(name=webhook_name, reason=_llm_reason("management"))
            return StepResult(success=True, action="create_webhook", name=webhook_name,
                               detail=f"Channel: {channel_name}", target_id=webhook.id)
        except Exception as e:
            return StepResult(success=False, action="create_webhook", name=webhook_name, error=str(e))

    async def delete_webhook(self, guild: discord.Guild, webhook_name: str) -> StepResult:
        try:
            webhooks = await guild.webhooks()
            for wh in webhooks:
                if wh.name == webhook_name:
                    await wh.delete(reason=_llm_reason("management"))
                    return StepResult(success=True, action="delete_webhook", name=webhook_name)
            return StepResult(success=False, action="delete_webhook", name=webhook_name, error="Webhook not found")
        except Exception as e:
            return StepResult(success=False, action="delete_webhook", name=webhook_name, error=str(e))

    async def set_server_name(self, guild: discord.Guild, name: str) -> StepResult:
        try:
            before = guild.name
            await guild.edit(name=name, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_server_name", name=name, detail=f"Changed from '{before}'", before_state={"name": before})
        except Exception as e:
            return StepResult(success=False, action="set_server_name", name=name, error=str(e))

    async def set_verification_level(self, guild: discord.Guild, level: str) -> StepResult:
        try:
            try:
                level_map = {
                    "none": discord.VerificationLevel.none,
                    "low": discord.VerificationLevel.low,
                    "medium": discord.VerificationLevel.medium,
                    "high": discord.VerificationLevel.high,
                    "very_high": discord.VerificationLevel.very_high,
                }
            except AttributeError:
                level_map = {
                    "none": 0, "low": 1, "medium": 2, "high": 3, "very_high": 4,
                }
            vlevel = level_map.get(level.lower(), 1)
            before = str(guild.verification_level)
            await guild.edit(verification_level=vlevel, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_verification_level", name=level, detail=f"Changed from {before}", before_state={"level": before})
        except Exception as e:
            return StepResult(success=False, action="set_verification_level", name=level, error=str(e))

    async def set_content_filter(self, guild: discord.Guild, filter_level: str) -> StepResult:
        try:
            try:
                _ecf = discord.ExplicitContentFilter
            except AttributeError:
                _ecf = discord.ContentFilter
            filter_map = {
                "disabled": _ecf.disabled,
                "no_role": _ecf.no_role,
                "all_members": _ecf.all_members,
            }
            flevel = filter_map.get(filter_level.lower(), 1)
            before = str(guild.explicit_content_filter)
            await guild.edit(explicit_content_filter=flevel, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_content_filter", name=filter_level, detail=f"Changed from {before}", before_state={"filter": before})
        except Exception as e:
            return StepResult(success=False, action="set_content_filter", name=filter_level, error=str(e))

    async def set_notifications(self, guild: discord.Guild, level: str) -> StepResult:
        try:
            try:
                notif_map = {
                    "all_messages": discord.NotificationLevel.all_messages,
                    "mentions_only": discord.NotificationLevel.only_mentions,
                    "none": discord.NotificationLevel.only_mentions,
                }
            except AttributeError:
                notif_map = {
                    "all_messages": 0, "mentions_only": 1, "none": 1,
                }
            nlevel = notif_map.get(level.lower(), 1)
            before = str(guild.default_notifications)
            await guild.edit(default_notifications=nlevel, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_notifications", name=level, detail=f"Changed from {before}", before_state={"notifications": before})
        except Exception as e:
            return StepResult(success=False, action="set_notifications", name=level, error=str(e))

    async def set_afk_channel(self, guild: discord.Guild, channel_name: str, timeout: int = 300) -> StepResult:
        try:
            ch = discord.utils.get(guild.voice_channels, name=channel_name)
            if not ch:
                return StepResult(success=False, action="set_afk_channel", name=channel_name, error="Voice channel not found")
            before = guild.afk_channel.name if guild.afk_channel else "None"
            await guild.edit(afk_channel=ch, afk_timeout=timeout, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_afk_channel", name=channel_name, detail=f"Timeout: {timeout}s", before_state={"afk_channel": before})
        except Exception as e:
            return StepResult(success=False, action="set_afk_channel", name=channel_name, error=str(e))

    async def set_system_channel(self, guild: discord.Guild, channel_name: str) -> StepResult:
        try:
            ch = discord.utils.get(guild.text_channels, name=channel_name)
            if not ch:
                return StepResult(success=False, action="set_system_channel", name=channel_name, error="Text channel not found")
            before = guild.system_channel.name if guild.system_channel else "None"
            await guild.edit(system_channel=ch, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_system_channel", name=channel_name, before_state={"system_channel": before})
        except Exception as e:
            return StepResult(success=False, action="set_system_channel", name=channel_name, error=str(e))

    async def set_rules_channel(self, guild: discord.Guild, channel_name: str) -> StepResult:
        try:
            ch = discord.utils.get(guild.text_channels, name=channel_name)
            if not ch:
                return StepResult(success=False, action="set_rules_channel", name=channel_name, error="Text channel not found")
            before = guild.rules_channel.name if guild.rules_channel else "None"
            await guild.edit(rules_channel=ch, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_rules_channel", name=channel_name, before_state={"rules_channel": before})
        except Exception as e:
            return StepResult(success=False, action="set_rules_channel", name=channel_name, error=str(e))

    async def create_scheduled_event(self, guild: discord.Guild, name: str, description: str,
                                      start_time: str, end_time: str = None,
                                      location: str = None, channel_name: str = None) -> StepResult:
        try:
            start = datetime.datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            end = datetime.datetime.fromisoformat(end_time.replace("Z", "+00:00")) if end_time else None

            if channel_name:
                ch = discord.utils.get(guild.channels, name=channel_name)
                if ch and isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
                    event = await guild.create_scheduled_event(
                        name=name, description=description, start_time=start,
                        end_time=end, channel=ch, reason=_llm_reason("management"),
                    )
                else:
                    event = await guild.create_scheduled_event(
                        name=name, description=description, start_time=start,
                        end_time=end, location=location, reason=_llm_reason("management"),
                    )
            else:
                event = await guild.create_scheduled_event(
                    name=name, description=description, start_time=start,
                    end_time=end, location=location, reason=_llm_reason("management"),
                )
            return StepResult(success=True, action="create_scheduled_event", name=name,
                               detail=f"Starts: {start_time}", target_id=event.id)
        except Exception as e:
            return StepResult(success=False, action="create_scheduled_event", name=name, error=str(e))

    async def delete_scheduled_event(self, guild: discord.Guild, event_name: str) -> StepResult:
        try:
            events = await guild.fetch_scheduled_events()
            for ev in events:
                if ev.name == event_name:
                    await ev.delete(reason=_llm_reason("management"))
                    return StepResult(success=True, action="delete_scheduled_event", name=event_name)
            return StepResult(success=False, action="delete_scheduled_event", name=event_name, error="Event not found")
        except Exception as e:
            return StepResult(success=False, action="delete_scheduled_event", name=event_name, error=str(e))

    async def create_sticker(self, guild: discord.Guild, name: str, description: str,
                             emoji: str, file_path: str = None, file_data: bytes = None) -> StepResult:
        try:
            if file_path:
                loop = asyncio.get_running_loop()
                def _read_file():
                    with open(file_path, "rb") as f:
                        return f.read()
                file_data = await loop.run_in_executor(None, _read_file)
            if not file_data:
                return StepResult(success=False, action="create_sticker", name=name,
                                 error="No file data provided")
            sticker = await guild.create_sticker(
                name=name, description=description, emoji=emoji,
                file=discord.File(io.BytesIO(file_data), filename=f"{name}.png"),
                reason=_llm_reason("management")
            )
            return StepResult(success=True, action="create_sticker", name=name,
                             detail=f"ID: {sticker.id}, Emoji: {emoji}", target_id=sticker.id)
        except Exception as e:
            return StepResult(success=False, action="create_sticker", name=name, error=str(e))

    async def delete_sticker(self, guild: discord.Guild, sticker_name: str) -> StepResult:
        try:
            stickers = await guild.fetch_stickers()
            for sticker in stickers:
                if sticker.name.lower() == sticker_name.lower():
                    await sticker.delete(reason=_llm_reason("management"))
                    return StepResult(success=True, action="delete_sticker", name=sticker_name,
                                     detail=f"Deleted sticker ID: {sticker.id}")
            return StepResult(success=False, action="delete_sticker", name=sticker_name,
                             error="Sticker not found")
        except Exception as e:
            return StepResult(success=False, action="delete_sticker", name=sticker_name, error=str(e))

    async def create_emoji(self, guild: discord.Guild, name: str, image_data: bytes = None,
                           image_path: str = None, roles: list[str] = None) -> StepResult:
        try:
            if image_path:
                loop = asyncio.get_running_loop()
                def _read(p):
                    with open(p, "rb") as f:
                        return f.read()
                image_data = await loop.run_in_executor(None, _read, image_path)
            if not image_data:
                return StepResult(success=False, action="create_emoji", name=name,
                                 error="No image data provided")
            role_objects = []
            if roles:
                for role_name in roles:
                    role = discord.utils.get(guild.roles, name=role_name)
                    if role:
                        role_objects.append(role)
            emoji = await guild.create_custom_emoji(
                name=name, image=image_data,
                roles=role_objects if role_objects else None,
                reason=_llm_reason("management")
            )
            return StepResult(success=True, action="create_emoji", name=name,
                             detail=f"ID: {emoji.id}, Restricted: {len(role_objects) > 0}",
                             target_id=emoji.id)
        except Exception as e:
            return StepResult(success=False, action="create_emoji", name=name, error=str(e))

    async def delete_emoji(self, guild: discord.Guild, emoji_name: str) -> StepResult:
        try:
            for emoji in guild.emojis:
                if emoji.name.lower() == emoji_name.lower():
                    await emoji.delete(reason=_llm_reason("management"))
                    return StepResult(success=True, action="delete_emoji", name=emoji_name,
                                     detail=f"Deleted emoji ID: {emoji.id}")
            return StepResult(success=False, action="delete_emoji", name=emoji_name,
                             error="Emoji not found")
        except Exception as e:
            return StepResult(success=False, action="delete_emoji", name=emoji_name, error=str(e))

    async def create_automod_rule(self, guild: discord.Guild, name: str, rule_type: str,
                                   keywords: list[str] = None, mention_limit: int = None,
                                   actions: list[str] = None, **kwargs) -> StepResult:
        try:
            trigger_type_cls = getattr(discord, 'AutoModRuleTriggerType', None) or getattr(discord, 'AutoModTriggerType', None)
            if not trigger_type_cls:
                return StepResult(success=False, action="create_automod_rule", name=name,
                                 error="AutoMod not available on this discord.py version")
            trigger_map = {
                "keyword": getattr(trigger_type_cls, 'keyword', None) or 1,
                "spam": getattr(trigger_type_cls, 'spam', None) or 3,
                "keyword_preset": getattr(trigger_type_cls, 'keyword_preset', None) or 4,
                "mention_spam": getattr(trigger_type_cls, 'mention_spam', None) or 5,
            }
            trigger = trigger_map.get(rule_type, getattr(trigger_type_cls, 'keyword', 1))
            metadata = {}
            if keywords:
                metadata["keyword_filter"] = keywords
            if mention_limit:
                metadata["mention_total_limit"] = mention_limit

            action_cls = getattr(discord, 'AutoModRuleAction', None) or getattr(discord, 'AutoModAction', None)
            action_type_cls = getattr(discord, 'AutoModRuleActionType', None) or getattr(discord, 'AutoModActionType', None)
            if not action_cls or not action_type_cls:
                return StepResult(success=False, action="create_automod_rule", name=name,
                                 error="AutoModAction types not available on this discord.py version")

            action_list = []
            if actions:
                for action_type in actions:
                    if action_type == "block":
                        action_list.append(action_cls(type=action_type_cls.block_message))
                    elif action_type == "timeout":
                        meta_cls = getattr(discord, 'AutoModRuleActionMetadata', None) or getattr(discord, 'AutoModActionMetadata', None)
                        if meta_cls:
                            action_list.append(action_cls(
                                type=action_type_cls.timeout,
                                metadata=meta_cls(duration_seconds=60)
                            ))
                        else:
                            action_list.append(action_cls(type=action_type_cls.timeout))
            else:
                action_list = [action_cls(type=action_type_cls.block_message)]

            event_type = getattr(discord, 'AutoModRuleEventType', None) or getattr(discord, 'AutoModEventType', None)
            event = getattr(event_type, 'message_send', None) if event_type else None

            rule = await guild.create_automod_rule(
                name=name,
                event_type=event,
                trigger_type=trigger,
                trigger_metadata=metadata if metadata else None,
                actions=action_list,
                enabled=True,
                reason=_llm_reason("management")
            )
            return StepResult(success=True, action="create_automod_rule", name=name,
                             detail=f"Type: {rule_type}, ID: {rule.id}", target_id=rule.id)
        except AttributeError as e:
            return StepResult(success=False, action="create_automod_rule", name=name,
                             error=f"Auto-moderation attribute error: {e}")
        except Exception as e:
            return StepResult(success=False, action="create_automod_rule", name=name, error=str(e))

    async def enable_spam_filter(self, guild: discord.Guild, mention_limit: int = 5) -> StepResult:
        try:
            result1 = await self.create_automod_rule(
                guild, name="Mention Spam Filter", rule_type="mention_spam",
                mention_limit=mention_limit, actions=["block", "timeout"]
            )
            result2 = await self.create_automod_rule(
                guild, name="Message Spam Filter", rule_type="spam",
                actions=["block"]
            )
            if result1.success and result2.success:
                return StepResult(success=True, action="enable_spam_filter",
                                 name="Spam Protection",
                                 detail=f"Created 2 rules: mention limit={mention_limit}")
            else:
                errors = []
                if not result1.success:
                    errors.append(result1.error)
                if not result2.success:
                    errors.append(result2.error)
                return StepResult(success=False, action="enable_spam_filter",
                                 name="Spam Protection", error="; ".join(errors))
        except Exception as e:
            return StepResult(success=False, action="enable_spam_filter",
                             name="Spam Protection", error=str(e))

    async def enable_keyword_filter(self, guild: discord.Guild, blocked_words: list[str]) -> StepResult:
        try:
            result = await self.create_automod_rule(
                guild, name="Keyword Filter", rule_type="keyword",
                keywords=blocked_words, actions=["block"]
            )
            return result
        except Exception as e:
            return StepResult(success=False, action="enable_keyword_filter",
                             name="Keyword Filter", error=str(e))

    async def get_audit_logs(self, guild: discord.Guild, limit: int = 50,
                             action_type: str = None) -> StepResult:
        try:
            action_map = {
                "ban": discord.AuditLogAction.ban,
                "kick": discord.AuditLogAction.kick,
                "channel_delete": discord.AuditLogAction.channel_delete,
                "channel_create": discord.AuditLogAction.channel_create,
                "role_delete": discord.AuditLogAction.role_delete,
                "member_update": discord.AuditLogAction.member_update,
            }
            action_filter = action_map.get(action_type) if action_type else None
            logs = []
            async for entry in guild.audit_logs(limit=limit, action=action_filter):
                logs.append({
                    "action": str(entry.action),
                    "user": str(entry.user),
                    "target": str(entry.target),
                    "reason": entry.reason or "No reason provided",
                    "created_at": str(entry.created_at),
                })
            return StepResult(success=True, action="get_audit_logs",
                             name=f"{len(logs)} entries",
                             detail=f"Action filter: {action_type or 'all'}",
                             after_state={"logs": logs})
        except Exception as e:
            return StepResult(success=False, action="get_audit_logs",
                             name="Audit Logs", error=str(e))

    async def find_who_did_action(self, guild: discord.Guild, action_type: str,
                                   target_name: str = None) -> StepResult:
        try:
            result = await self.get_audit_logs(guild, limit=100, action_type=action_type)
            if not result.success:
                return result
            logs = result.after_state.get("logs", [])
            if target_name:
                logs = [log for log in logs if target_name.lower() in log["target"].lower()]
            if logs:
                recent = logs[0]
                return StepResult(success=True, action="find_who_did_action",
                                 name=action_type,
                                 detail=f"User: {recent['user']}, Target: {recent['target']}, Reason: {recent['reason']}",
                                 after_state={"log": recent})
            else:
                return StepResult(success=False, action="find_who_did_action",
                                 name=action_type, error="No matching audit log found")
        except Exception as e:
            return StepResult(success=False, action="find_who_did_action",
                             name=action_type, error=str(e))

    async def set_welcome_screen(self, guild: discord.Guild, description: str,
                                  welcome_channels: list[str]) -> StepResult:
        try:
            channel_objects = []
            for ch_name in welcome_channels:
                ch = discord.utils.get(guild.channels, name=ch_name)
                if ch:
                    channel_objects.append(discord.WelcomeChannel(
                        channel=ch,
                        description=f"Check out {ch.name}"
                    ))
            if not channel_objects:
                return StepResult(success=False, action="set_welcome_screen",
                                 name="Welcome Screen",
                                 error="No valid channels found")
            if hasattr(guild, 'edit_welcome_screen'):
                await guild.edit_welcome_screen(
                    description=description,
                    welcome_channels=channel_objects[:5],
                    reason=_llm_reason("management")
                )
            else:
                await guild.edit(
                    welcome_screen=discord.WelcomeScreen(
                        data={"description": description, "welcome_channels": channel_objects[:5]},
                        guild=guild,
                    ),
                    reason=_llm_reason("management")
                )
            return StepResult(success=True, action="set_welcome_screen",
                             name="Welcome Screen",
                             detail=f"Channels: {', '.join(welcome_channels)}")
        except AttributeError:
            return StepResult(success=False, action="set_welcome_screen",
                             name="Welcome Screen",
                             error="Welcome screens require discord.py 2.0+")
        except Exception as e:
            return StepResult(success=False, action="set_welcome_screen",
                             name="Welcome Screen", error=str(e))

    async def create_server_template(self, guild: discord.Guild, name: str,
                                      description: str = None) -> StepResult:
        try:
            template = await guild.create_template(
                name=name,
                description=description or "Created by Azure bot",
                reason=_llm_reason("management")
            )
            return StepResult(success=True, action="create_server_template",
                             name=name,
                             detail=f"Code: {template.code}",
                             after_state={"code": template.code, "url": template.url})
        except Exception as e:
            return StepResult(success=False, action="create_server_template",
                             name=name, error=str(e))

    async def set_server_icon(self, guild: discord.Guild, image_data: bytes = None, image_url: str = None, image_path: str = None) -> StepResult:
        try:
            if image_path:
                loop = asyncio.get_running_loop()
                def _read_icon():
                    with open(image_path, 'rb') as f:
                        return f.read()
                image_data = await loop.run_in_executor(None, _read_icon)
            if image_data is None and image_url:
                import aiohttp
                async with aiohttp.ClientSession() as session, session.get(image_url) as resp:
                    image_data = await resp.read()
            if image_data is None:
                return StepResult(success=False, action="set_server_icon", name="icon", error="No image data provided")
            before = guild.icon.url if guild.icon else None
            await guild.edit(icon=image_data, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_server_icon", name="Server Icon",
                             detail="Icon updated", before_state={"icon": before})
        except Exception as e:
            return StepResult(success=False, action="set_server_icon", name="Server Icon", error=str(e))

    async def set_server_banner(self, guild: discord.Guild, image_data: bytes = None, image_url: str = None, image_path: str = None) -> StepResult:
        try:
            if image_path:
                loop = asyncio.get_running_loop()
                def _read_banner():
                    with open(image_path, 'rb') as f:
                        return f.read()
                image_data = await loop.run_in_executor(None, _read_banner)
            if image_data is None and image_url:
                import aiohttp
                async with aiohttp.ClientSession() as session, session.get(image_url) as resp:
                    image_data = await resp.read()
            if image_data is None:
                return StepResult(success=False, action="set_server_banner", name="banner", error="No image data provided")
            before = guild.banner.url if guild.banner else None
            await guild.edit(banner=image_data, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_server_banner", name="Server Banner",
                             detail="Banner updated", before_state={"banner": before})
        except Exception as e:
            return StepResult(success=False, action="set_server_banner", name="Server Banner", error=str(e))

    async def set_server_splash(self, guild: discord.Guild, image_data: bytes = None, image_url: str = None, image_path: str = None) -> StepResult:
        try:
            if image_path:
                loop = asyncio.get_running_loop()
                def _read_splash():
                    with open(image_path, 'rb') as f:
                        return f.read()
                image_data = await loop.run_in_executor(None, _read_splash)
            if image_data is None and image_url:
                import aiohttp
                async with aiohttp.ClientSession() as session, session.get(image_url) as resp:
                    image_data = await resp.read()
            if image_data is None:
                return StepResult(success=False, action="set_server_splash", name="splash", error="No image data provided")
            before = guild.splash.url if guild.splash else None
            await guild.edit(splash=image_data, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_server_splash", name="Server Splash",
                             detail="Splash updated", before_state={"splash": before})
        except Exception as e:
            return StepResult(success=False, action="set_server_splash", name="Server Splash", error=str(e))

    async def set_server_description(self, guild: discord.Guild, description: str) -> StepResult:
        try:
            before = guild.description
            await guild.edit(description=description, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_server_description", name="Server Description",
                             detail=f"Updated to: {description[:50]}" if description else "Cleared",
                             before_state={"description": before})
        except Exception as e:
            return StepResult(success=False, action="set_server_description", name="Server Description", error=str(e))

    async def set_public_updates_channel(self, guild: discord.Guild, channel_name: str) -> StepResult:
        try:
            ch = discord.utils.get(guild.text_channels, name=channel_name)
            if not ch:
                return StepResult(success=False, action="set_public_updates_channel", name=channel_name, error="Text channel not found")
            before = guild.public_updates_channel.name if guild.public_updates_channel else "None"
            await guild.edit(public_updates_channel=ch, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_public_updates_channel", name=channel_name,
                             before_state={"public_updates_channel": before})
        except Exception as e:
            return StepResult(success=False, action="set_public_updates_channel", name=channel_name, error=str(e))

    async def set_mfa_level(self, guild: discord.Guild, required: bool) -> StepResult:
        try:
            await guild.edit(mfa_level=discord.MFALevel.required if required else discord.MFALevel.none,
                            reason=_llm_reason("management"))
            return StepResult(success=True, action="set_mfa_level", name=f"MFA {'Required' if required else 'Disabled'}",
                             detail=f"MFA requirement set to {required}")
        except Exception as e:
            return StepResult(success=False, action="set_mfa_level", name="MFA Level", error=str(e))

    async def set_preferred_locale(self, guild: discord.Guild, locale: str) -> StepResult:
        try:
            await guild.edit(preferred_locale=locale, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_preferred_locale", name=locale,
                             detail=f"Locale set to {locale}")
        except Exception as e:
            return StepResult(success=False, action="set_preferred_locale", name=locale, error=str(e))

    async def set_vanity_url(self, guild: discord.Guild, code: str) -> StepResult:
        try:
            await guild.edit(vanity_url_code=code, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_vanity_url", name=code,
                             detail=f"Vanity URL: discord.gg/{code}")
        except Exception as e:
            return StepResult(success=False, action="set_vanity_url", name=code, error=str(e))

    async def get_vanity_url(self, guild: discord.Guild) -> StepResult:
        try:
            vanity = await guild.vanity_invite()
            if vanity:
                return StepResult(success=True, action="get_vanity_url", name="Vanity URL",
                                 detail=f"Code: {vanity.code}, URL: {vanity.url}")
            return StepResult(success=False, action="get_vanity_url", name="Vanity URL", error="No vanity URL set")
        except Exception as e:
            return StepResult(success=False, action="get_vanity_url", name="Vanity URL", error=str(e))

    async def get_ban_list(self, guild: discord.Guild, limit: int = 100) -> StepResult:
        try:
            bans = []
            async for ban_entry in guild.bans(limit=limit):
                bans.append({"user_id": ban_entry.user.id, "username": str(ban_entry.user),
                            "reason": ban_entry.reason or "No reason"})
            return StepResult(success=True, action="get_ban_list", name=f"{len(bans)} banned users",
                             after_state={"bans": bans})
        except Exception as e:
            return StepResult(success=False, action="get_ban_list", name="Ban List", error=str(e))

    async def estimate_prune_members(self, guild: discord.Guild, days: int = 30, roles: list[str] = None) -> StepResult:
        try:
            role_objects = [discord.utils.get(guild.roles, name=r) for r in (roles or []) if r]
            role_objects = [r for r in role_objects if r]
            count = await guild.estimate_pruned_members(days=days, roles=role_objects or None)
            return StepResult(success=True, action="estimate_prune_members", name=f"{count} members",
                             detail=f"Estimated {count} members inactive for {days}+ days")
        except Exception as e:
            return StepResult(success=False, action="estimate_prune_members", name="Prune Estimate", error=str(e))

    async def prune_members(self, guild: discord.Guild, days: int = 30, roles: list[str] = None, reason: str = "Azure prune") -> StepResult:
        try:
            role_objects = [discord.utils.get(guild.roles, name=r) for r in (roles or []) if r]
            role_objects = [r for r in role_objects if r]
            count = await guild.prune_members(days=days, roles=role_objects or None,
                                               reason=_llm_reason(reason))
            return StepResult(success=True, action="prune_members", name=f"{count} members pruned",
                             detail=f"Pruned {count} inactive members ({days}+ days)",
                             before_state={"pruned_count": count})
        except Exception as e:
            return StepResult(success=False, action="prune_members", name="Prune Members", error=str(e))

    async def get_automod_rules(self, guild: discord.Guild) -> StepResult:
        try:
            rules = await guild.fetch_automod_rules()
            rules_info = [{"id": r.id, "name": r.name, "enabled": r.enabled,
                          "trigger_type": str(r.trigger_type)} for r in rules]
            return StepResult(success=True, action="get_automod_rules", name=f"{len(rules_info)} rules",
                             after_state={"rules": rules_info})
        except Exception as e:
            return StepResult(success=False, action="get_automod_rules", name="AutoMod Rules", error=str(e))

    async def edit_automod_rule(self, guild: discord.Guild, rule_name: str, name: str = None,
                                 enabled: bool = None, actions: list[str] = None) -> StepResult:
        try:
            rules = await guild.fetch_automod_rules()
            target = None
            for r in rules:
                if r.name.lower() == rule_name.lower():
                    target = r
                    break
            if not target:
                return StepResult(success=False, action="edit_automod_rule", name=rule_name, error="Rule not found")
            kwargs = {}
            if name:
                kwargs["name"] = name
            if enabled is not None:
                kwargs["enabled"] = enabled
            if actions:
                action_cls = getattr(discord, 'AutoModRuleAction', None) or getattr(discord, 'AutoModAction', None)
                action_type_cls = getattr(discord, 'AutoModRuleActionType', None) or getattr(discord, 'AutoModActionType', None)
                if action_cls and action_type_cls:
                    action_list = []
                    for a in actions:
                        if a == "block":
                            action_list.append(action_cls(type=action_type_cls.block_message))
                        elif a == "timeout":
                            action_list.append(action_cls(type=action_type_cls.timeout))
                    if action_list:
                        kwargs["actions"] = action_list
            if kwargs:
                await target.edit(**kwargs, reason=_llm_reason("management"))
                return StepResult(success=True, action="edit_automod_rule", name=rule_name,
                                 detail=f"Updated: {', '.join(kwargs.keys())}")
            return StepResult(success=True, action="edit_automod_rule", name=rule_name, detail="No changes requested")
        except Exception as e:
            return StepResult(success=False, action="edit_automod_rule", name=rule_name, error=str(e))

    async def delete_automod_rule(self, guild: discord.Guild, rule_name: str) -> StepResult:
        try:
            rules = await guild.fetch_automod_rules()
            for r in rules:
                if r.name.lower() == rule_name.lower():
                    await r.delete(reason=_llm_reason("management"))
                    return StepResult(success=True, action="delete_automod_rule", name=rule_name)
            return StepResult(success=False, action="delete_automod_rule", name=rule_name, error="Rule not found")
        except Exception as e:
            return StepResult(success=False, action="delete_automod_rule", name=rule_name, error=str(e))

    async def edit_scheduled_event(self, guild: discord.Guild, event_name: str, name: str = None,
                                    description: str = None, start_time: str = None, end_time: str = None,
                                    location: str = None, channel_name: str = None, status: str = None) -> StepResult:
        try:
            events = await guild.fetch_scheduled_events()
            target = None
            for ev in events:
                if ev.name.lower() == event_name.lower():
                    target = ev
                    break
            if not target:
                return StepResult(success=False, action="edit_scheduled_event", name=event_name, error="Event not found")
            kwargs = {}
            if name:
                kwargs["name"] = name
            if description:
                kwargs["description"] = description
            if start_time:
                kwargs["start_time"] = datetime.datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            if end_time:
                kwargs["end_time"] = datetime.datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            if channel_name:
                ch = discord.utils.get(guild.channels, name=channel_name)
                if ch:
                    kwargs["channel"] = ch
            if location:
                kwargs["location"] = location
            if status:
                with contextlib.suppress(Exception):
                    kwargs["status"] = discord.EventStatus(status.lower())
            if kwargs:
                await target.edit(**kwargs, reason=_llm_reason("management"))
                return StepResult(success=True, action="edit_scheduled_event", name=event_name,
                                 detail=f"Updated: {', '.join(kwargs.keys())}")
            return StepResult(success=True, action="edit_scheduled_event", name=event_name, detail="No changes")
        except Exception as e:
            return StepResult(success=False, action="edit_scheduled_event", name=event_name, error=str(e))

    async def edit_emoji(self, guild: discord.Guild, emoji_name: str, name: str = None,
                          roles: list[str] = None) -> StepResult:
        try:
            target = None
            for emoji in guild.emojis:
                if emoji.name.lower() == emoji_name.lower():
                    target = emoji
                    break
            if not target:
                return StepResult(success=False, action="edit_emoji", name=emoji_name, error="Emoji not found")
            kwargs = {}
            if name:
                kwargs["name"] = name
            if roles is not None:
                role_objects = [discord.utils.get(guild.roles, name=r) for r in roles if r]
                kwargs["roles"] = [r for r in role_objects if r] or None
            await target.edit(**kwargs, reason=_llm_reason("management"))
            return StepResult(success=True, action="edit_emoji", name=emoji_name,
                             detail=f"Updated: {', '.join(kwargs.keys())}")
        except Exception as e:
            return StepResult(success=False, action="edit_emoji", name=emoji_name, error=str(e))

    async def edit_sticker(self, guild: discord.Guild, sticker_name: str, name: str = None,
                           description: str = None, emoji: str = None) -> StepResult:
        try:
            stickers = await guild.fetch_stickers()
            target = None
            for s in stickers:
                if s.name.lower() == sticker_name.lower():
                    target = s
                    break
            if not target:
                return StepResult(success=False, action="edit_sticker", name=sticker_name, error="Sticker not found")
            kwargs = {}
            if name:
                kwargs["name"] = name
            if description is not None:
                kwargs["description"] = description
            if emoji:
                kwargs["emoji"] = emoji
            await target.edit(**kwargs, reason=_llm_reason("management"))
            return StepResult(success=True, action="edit_sticker", name=sticker_name,
                             detail=f"Updated: {', '.join(kwargs.keys())}")
        except Exception as e:
            return StepResult(success=False, action="edit_sticker", name=sticker_name, error=str(e))

    async def edit_webhook(self, guild: discord.Guild, webhook_name: str, name: str = None,
                            channel_name: str = None) -> StepResult:
        try:
            webhooks = await guild.webhooks()
            target = None
            for wh in webhooks:
                if wh.name.lower() == webhook_name.lower():
                    target = wh
                    break
            if not target:
                return StepResult(success=False, action="edit_webhook", name=webhook_name, error="Webhook not found")
            kwargs = {}
            if name:
                kwargs["name"] = name
            if channel_name:
                ch = discord.utils.get(guild.text_channels, name=channel_name)
                if ch:
                    kwargs["channel"] = ch
            if not kwargs:
                return StepResult(success=True, action="edit_webhook", name=webhook_name, detail="No changes")
            await target.edit(**kwargs, reason=_llm_reason("management"))
            return StepResult(success=True, action="edit_webhook", name=webhook_name,
                             detail=f"Updated: {', '.join(kwargs.keys())}")
        except Exception as e:
            return StepResult(success=False, action="edit_webhook", name=webhook_name, error=str(e))

    async def get_channel_webhooks(self, guild: discord.Guild, channel_name: str) -> StepResult:
        try:
            ch = discord.utils.get(guild.channels, name=channel_name)
            if not ch:
                return StepResult(success=False, action="get_channel_webhooks", name=channel_name, error="Channel not found")
            webhooks = await ch.webhooks()
            info = [{"name": wh.name, "id": wh.id, "channel": wh.channel.name, "token": "***"} for wh in webhooks]
            return StepResult(success=True, action="get_channel_webhooks", name=f"{len(info)} webhooks",
                             after_state={"webhooks": info})
        except Exception as e:
            return StepResult(success=False, action="get_channel_webhooks", name=channel_name, error=str(e))

    async def get_guild_webhooks(self, guild: discord.Guild) -> StepResult:
        try:
            webhooks = await guild.webhooks()
            info = [{"name": wh.name, "id": wh.id, "channel": wh.channel.name, "type": str(wh.type)} for wh in webhooks]
            return StepResult(success=True, action="get_guild_webhooks", name=f"{len(info)} webhooks",
                             after_state={"webhooks": info})
        except Exception as e:
            return StepResult(success=False, action="get_guild_webhooks", name="Guild Webhooks", error=str(e))

    async def delete_server_template(self, guild: discord.Guild, template_code: str) -> StepResult:
        try:
            templates = await guild.templates()
            for tmpl in templates:
                if tmpl.code == template_code:
                    await tmpl.delete(reason=_llm_reason("management"))
                    return StepResult(success=True, action="delete_server_template", name=template_code)
            return StepResult(success=False, action="delete_server_template", name=template_code, error="Template not found")
        except Exception as e:
            return StepResult(success=False, action="delete_server_template", name=template_code, error=str(e))

    async def edit_server_template(self, guild: discord.Guild, template_code: str, name: str = None,
                                    description: str = None) -> StepResult:
        try:
            templates = await guild.templates()
            target = None
            for tmpl in templates:
                if tmpl.code == template_code:
                    target = tmpl
                    break
            if not target:
                return StepResult(success=False, action="edit_server_template", name=template_code, error="Template not found")
            kwargs = {}
            if name:
                kwargs["name"] = name
            if description is not None:
                kwargs["description"] = description
            await target.edit(**kwargs, reason=_llm_reason("management"))
            return StepResult(success=True, action="edit_server_template", name=template_code,
                             detail=f"Updated: {', '.join(kwargs.keys())}")
        except Exception as e:
            return StepResult(success=False, action="edit_server_template", name=template_code, error=str(e))

    async def get_guild_templates(self, guild: discord.Guild) -> StepResult:
        try:
            templates = await guild.templates()
            info = [{"code": t.code, "name": t.name, "usage_count": t.usage_count,
                    "created_at": str(t.created_at)} for t in templates]
            return StepResult(success=True, action="get_guild_templates", name=f"{len(info)} templates",
                             after_state={"templates": info})
        except Exception as e:
            return StepResult(success=False, action="get_guild_templates", name="Templates", error=str(e))

    async def end_stage_instance(self, stage_channel: discord.StageChannel) -> StepResult:
        try:
            instance = stage_channel.instance
            if not instance:
                return StepResult(success=False, action="end_stage_instance", name=stage_channel.name, error="No active instance")
            await instance.delete(reason=_llm_reason("management"))
            return StepResult(success=True, action="end_stage_instance", name=stage_channel.name, detail="Stage ended")
        except Exception as e:
            return StepResult(success=False, action="end_stage_instance", name=stage_channel.name, error=str(e))

    async def edit_stage_instance_topic(self, stage_channel: discord.StageChannel, topic: str) -> StepResult:
        try:
            instance = stage_channel.instance
            if not instance:
                return StepResult(success=False, action="edit_stage_instance_topic", name=stage_channel.name, error="No active instance")
            await instance.edit(topic=topic, reason=_llm_reason("management"))
            return StepResult(success=True, action="edit_stage_instance_topic", name=stage_channel.name, detail=f"Topic: {topic}")
        except Exception as e:
            return StepResult(success=False, action="edit_stage_instance_topic", name=stage_channel.name, error=str(e))

    async def get_onboarding(self, guild: discord.Guild) -> StepResult:
        try:
            onboarding = await guild.fetch_onboarding()
            info = {
                "enabled": onboarding.enabled if hasattr(onboarding, 'enabled') else True,
                "mode": str(onboarding.mode) if hasattr(onboarding, 'mode') else "unknown",
                "default_channels": [c.name for c in onboarding.default_channels] if hasattr(onboarding, 'default_channels') else [],
                "prompts": [{"title": p.title, "options": [o.label for o in p.options]}
                           for p in (onboarding.prompts if hasattr(onboarding, 'prompts') else [])]
            }
            return StepResult(success=True, action="get_onboarding", name="Onboarding",
                             after_state={"onboarding": info})
        except AttributeError:
            return StepResult(success=False, action="get_onboarding", name="Onboarding",
                             error="Onboarding not available on this discord.py version")
        except Exception as e:
            return StepResult(success=False, action="get_onboarding", name="Onboarding", error=str(e))

    async def edit_onboarding(self, guild: discord.Guild, enabled: bool = None, default_channels: list[str] = None,
                               prompts: list[dict] = None) -> StepResult:
        try:
            onboarding = await guild.fetch_onboarding()
            kwargs = {}
            if enabled is not None:
                kwargs["enabled"] = enabled
            if default_channels is not None:
                ch_objects = []
                for name in default_channels:
                    ch = discord.utils.get(guild.channels, name=name)
                    if ch:
                        ch_objects.append(ch)
                kwargs["default_channels"] = ch_objects if ch_objects else onboarding.default_channels
            if prompts is not None:
                prompt_objects = []
                try:
                    onboarding_prompt = getattr(discord, 'OnboardingPrompt', None)
                    if onboarding_prompt:
                        for p in prompts:
                            options = [{"label": opt, "description": "", "emoji": None, "channel_ids": []}
                                      for opt in p.get("options", [])]
                            prompt_objects.append(onboarding_prompt(
                                title=p.get("title", ""),
                                type=1, single_select=p.get("single_select", False),
                                required=p.get("required", True), options=options
                            ))
                except Exception:
                    logger.exception("[server_tools] onboarding prompt parse failed")
                if prompt_objects:
                    kwargs["prompts"] = prompt_objects
            if kwargs:
                await onboarding.edit(**kwargs, reason=_llm_reason("management"))
                return StepResult(success=True, action="edit_onboarding", name="Onboarding",
                                 detail=f"Updated: {', '.join(kwargs.keys())}")
            return StepResult(success=True, action="edit_onboarding", name="Onboarding", detail="No changes")
        except AttributeError:
            return StepResult(success=False, action="edit_onboarding", name="Onboarding",
                             error="Onboarding not available on this discord.py version")
        except Exception as e:
            return StepResult(success=False, action="edit_onboarding", name="Onboarding", error=str(e))

    async def enable_community_mode(self, guild: discord.Guild, rules_channel: str, public_updates_channel: str,
                                     system_channel: str = None, description: str = None) -> StepResult:
        try:
            rules = discord.utils.get(guild.text_channels, name=rules_channel)
            if not rules:
                return StepResult(success=False, action="enable_community_mode", name="Community",
                                 error=f"Rules channel '{rules_channel}' not found")
            updates = discord.utils.get(guild.text_channels, name=public_updates_channel)
            if not updates:
                return StepResult(success=False, action="enable_community_mode", name="Community",
                                 error=f"Public updates channel '{public_updates_channel}' not found")
            sys_ch = None
            if system_channel:
                sys_ch = discord.utils.get(guild.text_channels, name=system_channel)
            kwargs = {
                "rules_channel": rules,
                "public_updates_channel": updates,
                "premium_progress_bar_enabled": True,
            }
            if sys_ch:
                kwargs["system_channel"] = sys_ch
            if description:
                kwargs["description"] = description
            await guild.edit(**kwargs, reason=_llm_reason("management"))
            return StepResult(success=True, action="enable_community_mode", name="Community Mode",
                             detail=f"Community enabled: rules=#{rules_channel}, updates=#{public_updates_channel}")
        except Exception as e:
            return StepResult(success=False, action="enable_community_mode", name="Community", error=str(e))

    async def set_widget(self, guild: discord.Guild, enabled: bool, channel_name: str = None) -> StepResult:
        try:
            ch = None
            if channel_name:
                ch = discord.utils.get(guild.channels, name=channel_name)
            await guild.edit(widget_enabled=enabled, widget_channel=ch, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_widget", name="Widget",
                             detail=f"Widget {'enabled' if enabled else 'disabled'}" +
                                    (f" in #{channel_name}" if ch else ""))
        except Exception as e:
            return StepResult(success=False, action="set_widget", name="Widget", error=str(e))

    async def get_widget(self, guild: discord.Guild) -> StepResult:
        try:
            widget = await guild.widget()
            return StepResult(success=True, action="get_widget", name="Widget",
                             detail=f"Enabled: {widget.enabled}, Channel: {widget.channel.name if widget.channel else 'None'}")
        except Exception as e:
            return StepResult(success=False, action="get_widget", name="Widget", error=str(e))

    async def sync_server_template(self, guild: discord.Guild, template_code: str) -> StepResult:
        try:
            templates = await guild.templates()
            for template in templates:
                if template.code == template_code:
                    await template.sync()
                    return StepResult(success=True, action="sync_server_template",
                                     name=template.name,
                                     detail=f"Template synced: {template_code}")
            return StepResult(success=False, action="sync_server_template",
                             name=template_code, error="Template not found")
        except Exception as e:
            return StepResult(success=False, action="sync_server_template",
                             name=template_code, error=str(e))
