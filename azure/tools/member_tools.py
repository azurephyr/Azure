"""Member management tools."""
import datetime
import logging

import discord

from .server_tools import _llm_reason
from .types import StepResult

logger = logging.getLogger("tools.member_tools")


def _hierarchy_error(guild, member) -> str | None:
    """Reject targets the bot cannot safely moderate in Discord's hierarchy."""
    if member.id == getattr(getattr(guild, "me", None), "id", None):
        return "Cannot moderate the bot itself"
    if getattr(guild, "owner_id", None) == member.id:
        return "Cannot moderate the server owner"
    bot_role = getattr(getattr(guild, "me", None), "top_role", None)
    target_role = getattr(member, "top_role", None)
    if bot_role is not None and target_role is not None and not bot_role > target_role:
        return "Bot's role is not high enough to moderate this member"
    return None


class MemberToolsMixin:
    """Mixin providing member management for DiscordManagementTools."""

    async def kick_member(self, guild: discord.Guild, member_name_or_id: str, reason: str = "Azure") -> StepResult:
        try:
            member = await self._resolve_member(guild, member_name_or_id)
            if not member:
                return StepResult(success=False, action="kick", name=member_name_or_id, error="Member not found")
            if guild.me.top_role <= member.top_role:
                return StepResult(success=False, action="kick", name=member_name_or_id, error="Bot's role is not high enough to kick this member")
            await member.kick(reason=reason)
            return StepResult(success=True, action="kick", name=member.display_name, detail=f"Reason: {reason}")
        except Exception as e:
            return StepResult(success=False, action="kick", name=member_name_or_id, error=str(e))

    async def ban_member(self, guild: discord.Guild, member_name_or_id: str, reason: str = "Azure", delete_message_days: int = 0) -> StepResult:
        try:
            member = await self._resolve_member(guild, member_name_or_id)
            if not member:
                try:
                    uid = int(member_name_or_id)
                    user = await self.bot.fetch_user(uid)
                    await guild.ban(user, reason=reason, delete_message_days=delete_message_days)
                    return StepResult(success=True, action="ban", name=str(uid), detail=f"Reason: {reason}")
                except Exception:
                    return StepResult(success=False, action="ban", name=member_name_or_id, error="Member not found")
            hierarchy_error = _hierarchy_error(guild, member)
            if hierarchy_error:
                return StepResult(success=False, action="ban", name=member_name_or_id, error=hierarchy_error)
            await member.ban(reason=reason, delete_message_days=delete_message_days)
            return StepResult(success=True, action="ban", name=member.display_name, detail=f"Reason: {reason}")
        except Exception as e:
            return StepResult(success=False, action="ban", name=member_name_or_id, error=str(e))

    async def unban_member(self, guild: discord.Guild, user_id: int, reason: str = "Azure") -> StepResult:
        try:
            user = await self.bot.fetch_user(user_id)
            await guild.unban(user, reason=reason)
            return StepResult(success=True, action="unban", name=str(user_id), detail=f"Unbanned {user}")
        except Exception as e:
            return StepResult(success=False, action="unban", name=str(user_id), error=str(e))

    async def timeout_member(self, guild: discord.Guild, member_name_or_id: str, duration_minutes: int = 60,
                              reason: str = "Azure") -> StepResult:
        try:
            member = await self._resolve_member(guild, member_name_or_id)
            if not member:
                return StepResult(success=False, action="timeout", name=member_name_or_id, error="Member not found")
            hierarchy_error = _hierarchy_error(guild, member)
            if hierarchy_error:
                return StepResult(success=False, action="timeout", name=member_name_or_id, error=hierarchy_error)
            duration_minutes = max(1, min(int(duration_minutes), 28 * 24 * 60))
            until = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=duration_minutes)
            await member.timeout(until, reason=reason)
            return StepResult(success=True, action="timeout", name=member.display_name,
                               detail=f"Duration: {duration_minutes} min, Reason: {reason}")
        except Exception as e:
            return StepResult(success=False, action="timeout", name=member_name_or_id, error=str(e))

    async def set_nickname(self, guild: discord.Guild, member_name_or_id: str, nickname: str) -> StepResult:
        try:
            member = await self._resolve_member(guild, member_name_or_id)
            if not member:
                return StepResult(success=False, action="set_nickname", name=member_name_or_id, error="Member not found")
            hierarchy_error = _hierarchy_error(guild, member)
            if hierarchy_error:
                return StepResult(success=False, action="set_nickname", name=member_name_or_id, error=hierarchy_error)
            before = member.nick or member.display_name
            await member.edit(nick=nickname, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_nickname", name=member.display_name,
                               detail=f"Changed from '{before}' to '{nickname}'",
                               before_state={"nickname": before}, after_state={"nickname": nickname})
        except Exception as e:
            return StepResult(success=False, action="set_nickname", name=member_name_or_id, error=str(e))

    async def move_member_to_voice(self, guild: discord.Guild, member_name_or_id: str, channel_name: str) -> StepResult:
        try:
            member = await self._resolve_member(guild, member_name_or_id)
            if not member:
                return StepResult(success=False, action="move_voice", name=member_name_or_id, error="Member not found")
            channel = discord.utils.get(guild.voice_channels, name=channel_name)
            if not channel:
                return StepResult(success=False, action="move_voice", name=channel_name, error="Voice channel not found")
            if not member.voice or not member.voice.channel:
                return StepResult(success=False, action="move_voice", name=member_name_or_id, error="Member not in a voice channel")
            await member.move_to(channel, reason=_llm_reason("management"))
            return StepResult(success=True, action="move_voice", name=member.display_name, detail=f"Moved to {channel_name}")
        except Exception as e:
            return StepResult(success=False, action="move_voice", name=member_name_or_id, error=str(e))

    async def deafen_member(self, guild: discord.Guild, member_name_or_id: str, deafen: bool = True) -> StepResult:
        try:
            member = await self._resolve_member(guild, member_name_or_id)
            if not member:
                return StepResult(success=False, action="deafen", name=member_name_or_id, error="Member not found")
            if not member.voice:
                return StepResult(success=False, action="deafen", name=member_name_or_id, error="Member is not in a voice channel")
            await member.edit(deafen=deafen, reason=_llm_reason("management"))
            action_name = "deafen" if deafen else "undeafen"
            return StepResult(success=True, action=action_name, name=member.display_name)
        except Exception as e:
            return StepResult(success=False, action="deafen", name=member_name_or_id, error=str(e))

    async def mute_member(self, guild: discord.Guild, member_name_or_id: str, mute: bool = True) -> StepResult:
        try:
            member = await self._resolve_member(guild, member_name_or_id)
            if not member:
                return StepResult(success=False, action="mute", name=member_name_or_id, error="Member not found")
            if not member.voice:
                return StepResult(success=False, action="mute", name=member_name_or_id, error="Member is not in a voice channel")
            await member.edit(mute=mute, reason=_llm_reason("management"))
            action_name = "mute" if mute else "unmute"
            return StepResult(success=True, action=action_name, name=member.display_name)
        except Exception as e:
            return StepResult(success=False, action="mute", name=member_name_or_id, error=str(e))
