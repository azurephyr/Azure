"""Member management commands (kick, ban, timeout, nickname, role)."""
from __future__ import annotations

import logging
from typing import Any

from ..config import CHUNK_SIZE

logger = logging.getLogger("azure.discord.message")


async def _handle_member_management(message: Any, params: dict[str, str]) -> None:
    """Handle member management commands (kick, ban, timeout, nickname, role).

    Parses the user's message to determine the intended moderation action,
    verifies the user has the required permissions, and delegates to the
    appropriate management tool.

    Args:
        message: The Discord message triggering the command.
        params: Parsed parameters from the message handler.

    The supported actions are:
        - kick: Remove a member from the server.
        - ban: Permanently ban a member.
        - timeout: Temporarily mute a member for a specified duration.
        - nickname: Change a member's server nickname.
        - role_assign: Assign a role to a member.
        - role_remove: Remove a role from a member.
    """
    from bot.context import ctx

    from .llm_handler import _llm_response
    if not ctx.mgmt_tools or not message.guild:
        msg = await _llm_response("Member management attempted outside a server.", "❌ Member management only works in servers.")
        await message.channel.send(msg)
        return

    member = message.guild.get_member(message.author.id)
    is_owner = message.guild.owner_id == message.author.id
    is_admin = member and member.guild_permissions.administrator
    can_kick = member and member.guild_permissions.kick_members
    can_ban = member and member.guild_permissions.ban_members
    can_moderate = member and member.guild_permissions.moderate_members
    can_role = member and member.guild_permissions.manage_roles
    if not (is_owner or is_admin or can_kick or can_ban or can_moderate or can_role):
        msg = await _llm_response(
            f"User {message.author.name} lacks moderator permissions to manage members.",
            "⚠️ You need moderator permissions to manage members."
        )
        await message.channel.send(msg)
        return

    target_member = message.mentions[0] if message.mentions else None
    if not target_member:
        import re
        mention_match = re.search(r'<@!?(\d+)>', message.content)
        if mention_match:
            target_member = message.guild.get_member(int(mention_match.group(1)))

    if not target_member:
        msg = await _llm_response("User mentioned no member to manage.", "❌ Please mention a member to manage. Example: `@user`")
        await message.channel.send(msg)
        return

    intent_prompt = (
        f"User '{message.author.name}' said: '{message.content}'. "
        f"Target member: '{target_member.display_name}'. "
        f"User has permissions: kick={can_kick}, ban={can_ban}, moderate={can_moderate}, manage_roles={can_role}, admin={is_admin}, owner={is_owner}. "
        f"Classify the intent into exactly one of: kick, ban, timeout, nickname, role_assign, role_remove, or unknown. "
        f"Also extract any duration (for timeout), nickname text, or role name. "
        f"Reply ONLY with: ACTION:<action> DURATION:<mins> NICKNAME:<name> ROLE:<name>"
    )
    intent = await _llm_response(intent_prompt, "ACTION:unknown", max_tokens=40)

    import re
    action = "unknown"
    duration_min = 60
    nickname = ""
    role_name = ""
    m_action = re.search(r'ACTION:(\w+)', intent)
    if m_action:
        action = m_action.group(1)
    m_dur = re.search(r'DURATION:(\d+)', intent)
    if m_dur:
        duration_min = int(m_dur.group(1))
    # Stop each capture before the next "LABEL:" field so a value doesn't
    # swallow the following field's label (e.g. "NICKNAME:CoolDude ROLE:"
    # must yield "CoolDude", not "CoolDude ROLE").
    m_nick = re.search(r'NICKNAME:([\w\s-]+?)(?=\s+[A-Z]+:|$)', intent)
    if m_nick:
        nickname = m_nick.group(1).strip()
    m_role = re.search(r'ROLE:([\w\s-]+?)(?=\s+[A-Z]+:|$)', intent)
    if m_role:
        role_name = m_role.group(1).strip()

    if action == "kick":
        if not (is_owner or is_admin or can_kick):
            perm_msg = await _llm_response(
                f"User {message.author.name} wants to kick but lacks kick_members permission.",
                "⚠️ You need kick_members permission."
            )
            await message.channel.send(perm_msg)
            return
        result = await ctx.mgmt_tools.kick_member(message.guild, str(target_member.id), reason="Azure")
        resp = await _llm_response(
            f"Kick result: success={result.success}, detail='{result.detail}'. Generate response.",
            f"{'✅' if result.success else '❌'} {result.detail}" if result.detail else f"{'✅' if result.success else '❌'} Kicked {target_member.display_name}"
        )
        await message.channel.send(resp[:CHUNK_SIZE])

    elif action == "ban":
        if not (is_owner or is_admin or can_ban):
            perm_msg = await _llm_response(
                f"User {message.author.name} wants to ban but lacks ban_members permission.",
                "⚠️ You need ban_members permission."
            )
            await message.channel.send(perm_msg)
            return
        result = await ctx.mgmt_tools.ban_member(message.guild, str(target_member.id), reason="Azure")
        resp = await _llm_response(
            f"Ban result: success={result.success}, detail='{result.detail}'. Generate response.",
            f"{'✅' if result.success else '❌'} {result.detail}" if result.detail else f"{'✅' if result.success else '❌'} Banned {target_member.display_name}"
        )
        await message.channel.send(resp[:CHUNK_SIZE])

    elif action == "timeout":
        if not (is_owner or is_admin or can_moderate):
            perm_msg = await _llm_response(
                f"User {message.author.name} wants to timeout but lacks moderate_members permission.",
                "⚠️ You need moderate_members permission."
            )
            await message.channel.send(perm_msg)
            return
        result = await ctx.mgmt_tools.timeout_member(message.guild, str(target_member.id), duration_minutes=duration_min, reason="Azure")
        resp = await _llm_response(
            f"Timeout result: success={result.success}, duration={duration_min}min, detail='{result.detail}'. Generate response.",
            f"{'✅' if result.success else '❌'} {result.detail}" if result.detail else f"{'✅' if result.success else '❌'} Timed out {target_member.display_name}"
        )
        await message.channel.send(resp[:CHUNK_SIZE])

    elif action == "nickname":
        if not nickname:
            parts = message.content.split()
            nickname = parts[-1].strip() if len(parts) >= 3 else ""
        if nickname:
            result = await ctx.mgmt_tools.set_nickname(message.guild, str(target_member.id), nickname=nickname)
            resp = await _llm_response(
                f"Nickname change result: success={result.success}, detail='{result.detail}'. Generate response.",
                f"{'✅' if result.success else '❌'} Nickname changed"
            )
            await message.channel.send(resp[:CHUNK_SIZE])
        else:
            msg = await _llm_response("No nickname provided.", "❌ Usage: `change @user nickname to NewName`")
            await message.channel.send(msg)

    elif action in ("role_assign", "role_remove"):
        if not (is_owner or is_admin or can_role):
            perm_msg = await _llm_response(
                f"User {message.author.name} wants to manage roles but lacks manage_roles permission.",
                "⚠️ You need manage_roles permission."
            )
            await message.channel.send(perm_msg)
            return
        if not role_name:
            role_match = re.search(r'["\']([\w\s-]+)["\']', message.content)
            role_name = role_match.group(1) if role_match else ""
        if not role_name:
            msg = await _llm_response("No role name provided.", "❌ Please specify a role name in quotes. Example: `give @user role 'Moderator'`")
            await message.channel.send(msg)
            return
        if action == "role_remove":
            result = await ctx.mgmt_tools.remove_role(message.guild, str(target_member.id), role_name=role_name)
        else:
            result = await ctx.mgmt_tools.assign_role(message.guild, str(target_member.id), role_name=role_name)
        resp = await _llm_response(
            f"Role {action} result: success={result.success}, role='{role_name}', detail='{result.detail}'. Generate response.",
            f"{'✅' if result.success else '❌'} Role updated"
        )
        await message.channel.send(resp[:CHUNK_SIZE])

    else:
        msg = await _llm_response(
            f"User said '{message.content}' but no valid management action was identified.",
            "🛠️ **Member Management Commands:**\n• `kick @user`\n• `ban @user`\n• `timeout @user 60 min`\n• `nickname @user to NewName`\n• `give @user role 'RoleName'`\n• `remove @user role 'RoleName'`"
        )
        await message.channel.send(msg[:CHUNK_SIZE])
