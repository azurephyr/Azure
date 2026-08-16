"""Channel and category management tools."""
import logging

import discord
from discord import PermissionOverwrite

from .server_tools import _llm_reason
from .types import StepResult

logger = logging.getLogger("tools.channel_tools")


class ChannelToolsMixin:
    """Mixin providing channel and category management for DiscordManagementTools."""

    async def create_category(self, guild: discord.Guild, name: str, position: int = None) -> StepResult:
        try:
            cat = await guild.create_category(name, reason=_llm_reason("setup"))
            if position is not None:
                try:
                    await cat.edit(position=position)
                except Exception as e:
                    logger.error(f"[tools] position edit error: {e}")
            return StepResult(success=True, action="create_category", name=name, detail=f"ID: {cat.id}", target_id=cat.id)
        except Exception as e:
            return StepResult(success=False, action="create_category", name=name, error=str(e))

    async def edit_category(self, guild: discord.Guild, category_name: str, **kwargs) -> StepResult:
        try:
            cat = discord.utils.get(guild.categories, name=category_name)
            if not cat:
                return StepResult(success=False, action="edit_category", name=category_name, error="Category not found")
            before = {"name": cat.name, "position": cat.position}
            await cat.edit(**kwargs, reason=_llm_reason("edit"))
            return StepResult(success=True, action="edit_category", name=category_name, detail="Updated", before_state=before, after_state=kwargs)
        except Exception as e:
            return StepResult(success=False, action="edit_category", name=category_name, error=str(e))

    async def delete_category(self, guild: discord.Guild, category_name: str) -> StepResult:
        try:
            cat = discord.utils.get(guild.categories, name=category_name)
            if not cat:
                return StepResult(success=False, action="delete_category", name=category_name, error="Category not found")
            await cat.delete(reason=_llm_reason("cleanup"))
            return StepResult(success=True, action="delete_category", name=category_name)
        except Exception as e:
            return StepResult(success=False, action="delete_category", name=category_name, error=str(e))

    async def create_channel(self, guild: discord.Guild, name: str, channel_type: str = "text",
                              category: str = None, topic: str = None, slowmode: int = None,
                              nsfw: bool = False, bitrate: int = None, user_limit: int = None) -> StepResult:
        try:
            cat_obj = discord.utils.get(guild.categories, name=category) if category else None
            ctype = getattr(discord.ChannelType, channel_type, discord.ChannelType.text)

            kwargs = {"reason": "Azure agentic setup"}
            if cat_obj:
                kwargs["category"] = cat_obj
            if topic and ctype == discord.ChannelType.text:
                kwargs["topic"] = topic
            if slowmode is not None and ctype == discord.ChannelType.text:
                kwargs["slowmode_delay"] = slowmode
            if nsfw and ctype == discord.ChannelType.text:
                kwargs["nsfw"] = nsfw
            if bitrate and ctype == discord.ChannelType.voice:
                kwargs["bitrate"] = bitrate
            if user_limit is not None and ctype == discord.ChannelType.voice:
                kwargs["user_limit"] = user_limit

            if ctype == discord.ChannelType.text:
                ch = await guild.create_text_channel(name, **kwargs)
            elif ctype == discord.ChannelType.voice:
                ch = await guild.create_voice_channel(name, **kwargs)
            elif ctype == discord.ChannelType.forum:
                ch = await guild.create_forum(name, **kwargs)
            elif ctype == discord.ChannelType.stage_voice:
                ch = await guild.create_stage_channel(name, **kwargs)
            elif ctype == discord.ChannelType.news:
                kwargs["news"] = True
                ch = await guild.create_text_channel(name, **kwargs)
            else:
                ch = await guild.create_text_channel(name, **kwargs)

            return StepResult(success=True, action="create_channel", name=name,
                               detail=f"Type: {channel_type}, ID: {ch.id}", target_id=ch.id)
        except Exception as e:
            return StepResult(success=False, action="create_channel", name=name, error=str(e))

    async def edit_channel(self, guild: discord.Guild, channel_name: str, **kwargs) -> StepResult:
        try:
            ch = discord.utils.get(guild.channels, name=channel_name)
            if not ch:
                return StepResult(success=False, action="edit_channel", name=channel_name, error="Channel not found")
            before = {"name": ch.name}
            if hasattr(ch, "topic"):
                before["topic"] = ch.topic or ""
            if hasattr(ch, "slowmode_delay"):
                before["slowmode"] = ch.slowmode_delay
            if hasattr(ch, "nsfw"):
                before["nsfw"] = ch.nsfw

            if "category" in kwargs:
                cat = discord.utils.get(guild.categories, name=kwargs.pop("category"))
                if cat:
                    kwargs["category"] = cat
            if "type" in kwargs:
                type_val = kwargs.pop("type")
                if type_val == "nsfw":
                    kwargs["nsfw"] = True
                else:
                    logger.warning("[tools] edit_channel ignoring unsupported type=%r", type_val)

            await ch.edit(**kwargs, reason=_llm_reason("edit"))
            return StepResult(success=True, action="edit_channel", name=channel_name, detail="Updated", before_state=before, after_state=kwargs)
        except Exception as e:
            return StepResult(success=False, action="edit_channel", name=channel_name, error=str(e))

    async def delete_channel(self, channel: discord.abc.GuildChannel, safe: bool = True) -> StepResult:
        try:
            before = {"name": channel.name, "type": str(channel.type)}
            await channel.delete(reason=_llm_reason("cleanup"))
            return StepResult(success=True, action="delete_channel", name=channel.name, before_state=before)
        except Exception as e:
            return StepResult(success=False, action="delete_channel", name=channel.name, error=str(e))

    async def move_channel(self, guild: discord.Guild, channel_name: str, category_name: str) -> StepResult:
        try:
            ch = discord.utils.get(guild.channels, name=channel_name)
            if not ch:
                return StepResult(success=False, action="move_channel", name=channel_name, error="Channel not found")
            cat = discord.utils.get(guild.categories, name=category_name)
            if not cat:
                return StepResult(success=False, action="move_channel", name=channel_name, error=f"Category '{category_name}' not found")
            before_cat = ch.category.name if ch.category else None
            await ch.edit(category=cat, reason=_llm_reason("management"))
            return StepResult(success=True, action="move_channel", name=channel_name,
                               detail=f"Moved from '{before_cat or 'none'}' to '{category_name}'",
                               before_state={"category": before_cat}, after_state={"category": category_name})
        except Exception as e:
            return StepResult(success=False, action="move_channel", name=channel_name, error=str(e))

    async def sync_channel_permissions(self, guild: discord.Guild, channel_name: str) -> StepResult:
        try:
            ch = discord.utils.get(guild.channels, name=channel_name)
            if not ch:
                return StepResult(success=False, action="sync_permissions", name=channel_name, error="Channel not found")
            if not hasattr(ch, "sync_permissions") or ch.category is None:
                return StepResult(success=False, action="sync_permissions", name=channel_name, error="No category to sync with")
            await ch.sync_permissions(reason=_llm_reason("management"))
            return StepResult(success=True, action="sync_permissions", name=channel_name, detail=f"Synced with category '{ch.category.name}'")
        except Exception as e:
            return StepResult(success=False, action="sync_permissions", name=channel_name, error=str(e))

    async def set_channel_permissions(self, channel: discord.abc.GuildChannel, target_name: str,
                                        allow: list = None, deny: list = None,
                                        target_type: str = "role") -> StepResult:
        try:
            guild = channel.guild
            if target_type == "role":
                target = discord.utils.get(guild.roles, name=target_name)
            else:
                target = await self._resolve_member(guild, target_name)
            if not target:
                return StepResult(success=False, action="set_permissions", name=target_name,
                                   error=f"{target_type.title()} '{target_name}' not found")

            before = {}
            try:
                existing = channel.overwrites_for(target)
                if existing:
                    before = {"pair": str(existing)}
            except Exception:
                logger.warning("[tools] could not fetch before-state overwrites for %s", target_name)

            overwrite = channel.overwrites_for(target) or PermissionOverwrite()
            for perm in (allow or []):
                p = perm.lower().strip()
                if p and hasattr(overwrite, p):
                    setattr(overwrite, p, True)
            for perm in (deny or []):
                p = perm.lower().strip()
                if p and hasattr(overwrite, p):
                    setattr(overwrite, p, False)

            await channel.set_permissions(target, overwrite=overwrite, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_permissions", name=target_name,
                               detail=f"Channel: {channel.name}, Allow: {allow or []}, Deny: {deny or []}",
                               before_state=before, after_state={"allow": allow or [], "deny": deny or []})
        except Exception as e:
            return StepResult(success=False, action="set_permissions", name=target_name, error=str(e))

    async def clear_channel_permissions(self, channel: discord.abc.GuildChannel, target_name: str,
                                          target_type: str = "role") -> StepResult:
        try:
            guild = channel.guild
            if target_type == "role":
                target = discord.utils.get(guild.roles, name=target_name)
            else:
                target = await self._resolve_member(guild, target_name)
            if not target:
                return StepResult(success=False, action="clear_permissions", name=target_name,
                                   error=f"{target_type.title()} '{target_name}' not found")
            if target not in channel.overwrites:
                return StepResult(success=True, action="clear_permissions", name=target_name,
                                   detail="No permissions to clear")
            await channel.set_permissions(target, overwrite=None, reason=_llm_reason("management"))
            return StepResult(success=True, action="clear_permissions", name=target_name,
                               detail=f"Cleared permissions in {channel.name}")
        except Exception as e:
            return StepResult(success=False, action="clear_permissions", name=target_name, error=str(e))

    async def purge_messages(self, channel: discord.TextChannel, limit: int = 100) -> StepResult:
        try:
            if limit > 200:
                limit = 200
            deleted = await channel.purge(limit=limit, reason=_llm_reason("management"))
            return StepResult(success=True, action="purge_messages", name=channel.name,
                              detail=f"Deleted {len(deleted)} messages")
        except Exception as e:
            return StepResult(success=False, action="purge_messages", name=channel.name, error=str(e))

    async def create_invite(self, channel: discord.abc.GuildChannel, max_age: int = 86400,
                              max_uses: int = 0, temporary: bool = False) -> StepResult:
        try:
            invite = await channel.create_invite(max_age=max_age, max_uses=max_uses,
                                                    temporary=temporary, reason=_llm_reason("management"))
            return StepResult(success=True, action="create_invite", name=channel.name,
                               detail=f"Code: {invite.code}, Expires: {max_age}s, Uses: {max_uses or 'unlimited'}")
        except Exception as e:
            return StepResult(success=False, action="create_invite", name=channel.name, error=str(e))

    async def pin_message(self, channel: discord.TextChannel, message_id: int) -> StepResult:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.pin(reason=_llm_reason("management"))
            return StepResult(success=True, action="pin_message", name=str(message_id), detail=f"Pinned in #{channel.name}")
        except Exception as e:
            return StepResult(success=False, action="pin_message", name=str(message_id), error=str(e))

    async def unpin_message(self, channel: discord.TextChannel, message_id: int) -> StepResult:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.unpin(reason=_llm_reason("management"))
            return StepResult(success=True, action="unpin_message", name=str(message_id), detail=f"Unpinned in #{channel.name}")
        except Exception as e:
            return StepResult(success=False, action="unpin_message", name=str(message_id), error=str(e))

    async def create_thread(self, channel: discord.TextChannel, name: str, message_id: int = None,
                             thread_type: str = "public") -> StepResult:
        try:
            if thread_type == "public":
                ttype = discord.ChannelType.public_thread
            elif thread_type == "private":
                ttype = discord.ChannelType.private_thread
            else:
                ttype = discord.ChannelType.public_thread

            if message_id:
                msg = await channel.fetch_message(message_id)
                thread = await msg.create_thread(name=name, auto_archive_duration=1440)
            else:
                thread = await channel.create_thread(name=name, type=ttype, auto_archive_duration=1440)
            return StepResult(success=True, action="create_thread", name=name, detail=f"Type: {thread_type}, ID: {thread.id}", target_id=thread.id)
        except Exception as e:
            return StepResult(success=False, action="create_thread", name=name, error=str(e))

    async def archive_thread(self, channel: discord.Thread) -> StepResult:
        try:
            await channel.edit(archived=True, reason=_llm_reason("management"))
            return StepResult(success=True, action="archive_thread", name=channel.name)
        except Exception as e:
            return StepResult(success=False, action="archive_thread", name=channel.name, error=str(e))

    async def delete_thread(self, channel: discord.Thread) -> StepResult:
        try:
            await channel.delete(reason=_llm_reason("management"))
            return StepResult(success=True, action="delete_thread", name=channel.name)
        except Exception as e:
            return StepResult(success=False, action="delete_thread", name=channel.name, error=str(e))

    async def rename_thread(self, channel: discord.Thread, name: str) -> StepResult:
        try:
            before = channel.name
            await channel.edit(name=name, reason=_llm_reason("management"))
            return StepResult(success=True, action="rename_thread", name=name,
                             before_state={"name": before})
        except Exception as e:
            return StepResult(success=False, action="rename_thread", name=name, error=str(e))

    async def set_thread_auto_archive(self, channel: discord.Thread, duration_minutes: int = 1440) -> StepResult:
        try:
            valid = [60, 1440, 4320, 10080]
            duration = min(valid, key=lambda x: abs(x - duration_minutes))
            await channel.edit(auto_archive_duration=duration, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_thread_auto_archive", name=channel.name,
                             detail=f"Auto-archive: {duration}min")
        except Exception as e:
            return StepResult(success=False, action="set_thread_auto_archive", name=channel.name, error=str(e))

    async def set_thread_slowmode(self, channel: discord.Thread, slowmode_seconds: int = 0) -> StepResult:
        try:
            await channel.edit(slowmode_delay=slowmode_seconds, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_thread_slowmode", name=channel.name,
                             detail=f"Slowmode: {slowmode_seconds}s")
        except Exception as e:
            return StepResult(success=False, action="set_thread_slowmode", name=channel.name, error=str(e))

    async def join_thread(self, channel: discord.Thread) -> StepResult:
        try:
            if hasattr(channel, 'join'):
                await channel.join()
                return StepResult(success=True, action="join_thread", name=channel.name)
            return StepResult(success=False, action="join_thread", name=channel.name, error="Thread join not available")
        except Exception as e:
            return StepResult(success=False, action="join_thread", name=channel.name, error=str(e))

    async def leave_thread(self, channel: discord.Thread) -> StepResult:
        try:
            if hasattr(channel, 'leave'):
                await channel.leave()
                return StepResult(success=True, action="leave_thread", name=channel.name)
            return StepResult(success=False, action="leave_thread", name=channel.name, error="Thread leave not available")
        except Exception as e:
            return StepResult(success=False, action="leave_thread", name=channel.name, error=str(e))

    async def add_thread_member(self, channel: discord.Thread, member: discord.Member) -> StepResult:
        try:
            if hasattr(channel, 'add_user'):
                await channel.add_user(member)
                return StepResult(success=True, action="add_thread_member", name=member.display_name)
            return StepResult(success=False, action="add_thread_member", name=member.display_name, error="add_user not available")
        except Exception as e:
            return StepResult(success=False, action="add_thread_member", name=member.display_name, error=str(e))

    async def remove_thread_member(self, channel: discord.Thread, member: discord.Member) -> StepResult:
        try:
            if hasattr(channel, 'remove_user'):
                await channel.remove_user(member)
                return StepResult(success=True, action="remove_thread_member", name=member.display_name)
            return StepResult(success=False, action="remove_thread_member", name=member.display_name, error="remove_user not available")
        except Exception as e:
            return StepResult(success=False, action="remove_thread_member", name=member.display_name, error=str(e))

    async def list_archived_threads(self, guild: discord.Guild, public: bool = True, limit: int = 50) -> StepResult:
        try:
            if public:
                threads_data = await guild.fetch_active_threads()
            else:
                threads_data = []
                for ch in guild.text_channels:
                    try:
                        archived = [t async for t in ch.archived_threads(limit=limit)]
                        threads_data.extend(archived)
                    except Exception:
                        logger.warning("[tools] could not fetch archived threads for %s", ch.name)
            info = [{"name": t.name, "id": t.id, "parent": t.parent.name if t.parent else "None",
                    "archived": t.archived if hasattr(t, 'archived') else False}
                   for t in threads_data]
            return StepResult(success=True, action="list_active_threads", name=f"{len(info)} threads",
                             after_state={"threads": info})
        except Exception as e:
            return StepResult(success=False, action="list_archived_threads", name="Threads", error=str(e))

    async def clone_channel(self, guild: discord.Guild, channel_name: str, name: str = None,
                             reason: str = "clone") -> StepResult:
        try:
            ch = discord.utils.get(guild.channels, name=channel_name)
            if not ch:
                return StepResult(success=False, action="clone_channel", name=channel_name, error="Channel not found")
            new_name = name or f"{ch.name}-copy"
            new_ch = await ch.clone(name=new_name, reason=_llm_reason(reason))
            return StepResult(success=True, action="clone_channel", name=new_name,
                             detail=f"Cloned from #{channel_name}, ID: {new_ch.id}", target_id=new_ch.id)
        except Exception as e:
            return StepResult(success=False, action="clone_channel", name=name or channel_name, error=str(e))

    async def follow_channel(self, channel: discord.TextChannel, target_channel_id: int) -> StepResult:
        try:
            if not isinstance(channel, discord.TextChannel) or not channel.is_news():
                return StepResult(success=False, action="follow_channel", name=channel.name, error="Channel is not an announcement channel")
            target = channel.guild.get_channel(target_channel_id)
            if not target:
                return StepResult(success=False, action="follow_channel", name=channel.name, error="Target channel not found")
            if hasattr(channel, 'follow'):
                await channel.follow(target)
                return StepResult(success=True, action="follow_channel", name=channel.name,
                                 detail=f"Following to {target.name}")
            return StepResult(success=False, action="follow_channel", name=channel.name, error="Follow not available")
        except Exception as e:
            return StepResult(success=False, action="follow_channel", name=channel.name, error=str(e))

    async def crosspost_message(self, channel: discord.TextChannel, message_id: int) -> StepResult:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.publish()
            return StepResult(success=True, action="crosspost_message", name=str(message_id),
                             detail=f"Published in #{channel.name}")
        except Exception as e:
            return StepResult(success=False, action="crosspost_message", name=str(message_id), error=str(e))

    async def set_forum_require_tag(self, forum_channel: discord.ForumChannel, require_tag: bool = True) -> StepResult:
        try:
            if isinstance(require_tag, str):
                require_tag = require_tag.lower() in ("true", "yes", "1", "on")
            await forum_channel.edit(require_tag=require_tag, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_forum_require_tag", name=forum_channel.name,
                             detail=f"Require tag: {require_tag}")
        except Exception as e:
            return StepResult(success=False, action="set_forum_require_tag", name=forum_channel.name, error=str(e))

    async def set_forum_default_reaction(self, forum_channel: discord.ForumChannel, emoji: str = None) -> StepResult:
        try:
            await forum_channel.edit(default_reaction_emoji=emoji, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_forum_default_reaction", name=forum_channel.name,
                             detail=f"Default reaction: {emoji or 'none'}")
        except Exception as e:
            return StepResult(success=False, action="set_forum_default_reaction", name=forum_channel.name, error=str(e))

    async def set_forum_default_slowmode(self, forum_channel: discord.ForumChannel, slowmode_seconds: int = 0) -> StepResult:
        try:
            await forum_channel.edit(default_thread_slowmode_delay=slowmode_seconds, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_forum_default_slowmode", name=forum_channel.name,
                             detail=f"Default slowmode: {slowmode_seconds}s")
        except Exception as e:
            return StepResult(success=False, action="set_forum_default_slowmode", name=forum_channel.name, error=str(e))

    async def disconnect_voice(self, member: discord.Member) -> StepResult:
        try:
            if not member.voice or not member.voice.channel:
                return StepResult(success=False, action="disconnect_voice", name=member.display_name, error="Not in a voice channel")
            await member.move_to(None, reason=_llm_reason("management"))
            return StepResult(success=True, action="disconnect_voice", name=member.display_name, detail="Disconnected from voice")
        except Exception as e:
            return StepResult(success=False, action="disconnect_voice", name=member.display_name, error=str(e))

    async def get_channel_invites(self, channel: discord.abc.GuildChannel) -> StepResult:
        try:
            invites = await channel.invites()
            info = [{"code": inv.code, "uses": inv.uses, "max_uses": inv.max_uses,
                    "created_by": str(inv.inviter) if inv.inviter else "unknown",
                    "expires": str(inv.expires_at) if inv.expires_at else "never"} for inv in invites]
            return StepResult(success=True, action="get_channel_invites", name=f"{len(info)} invites",
                             after_state={"invites": info})
        except Exception as e:
            return StepResult(success=False, action="get_channel_invites", name="Invites", error=str(e))

    async def get_guild_invites(self, guild: discord.Guild) -> StepResult:
        try:
            invites = await guild.invites()
            info = [{"code": inv.code, "channel": inv.channel.name, "uses": inv.uses,
                    "created_by": str(inv.inviter) if inv.inviter else "unknown"} for inv in invites]
            return StepResult(success=True, action="get_guild_invites", name=f"{len(info)} invites",
                             after_state={"invites": info})
        except Exception as e:
            return StepResult(success=False, action="get_guild_invites", name="Invites", error=str(e))

    async def revoke_invite(self, guild: discord.Guild, code: str) -> StepResult:
        try:
            invites = await guild.invites()
            for inv in invites:
                if inv.code == code:
                    await inv.delete(reason=_llm_reason("management"))
                    return StepResult(success=True, action="revoke_invite", name=code, detail="Invite revoked")
            return StepResult(success=False, action="revoke_invite", name=code, error="Invite not found")
        except Exception as e:
            return StepResult(success=False, action="revoke_invite", name=code, error=str(e))

    async def get_pinned_messages(self, channel: discord.TextChannel) -> StepResult:
        try:
            pins = await channel.pins()
            info = [{"id": m.id, "author": str(m.author), "content": m.content[:100],
                    "pinned_at": str(m.pinned_at) if hasattr(m, 'pinned_at') and m.pinned_at else ""} for m in pins]
            return StepResult(success=True, action="get_pinned_messages", name=f"{len(info)} pins",
                             after_state={"pins": info})
        except Exception as e:
            return StepResult(success=False, action="get_pinned_messages", name="Pins", error=str(e))

    async def create_forum_channel(self, guild: discord.Guild, name: str, topic: str = None,
                                    category: str = None, default_sort_order: int = 0,
                                    default_layout: int = 1) -> StepResult:
        try:
            cat_obj = discord.utils.get(guild.categories, name=category) if category else None
            kwargs = {"reason": "Azure agentic setup"}
            if cat_obj:
                kwargs["category"] = cat_obj
            if topic:
                kwargs["topic"] = topic
            try:
                if hasattr(discord, "ForumLayoutType"):
                    kwargs["default_layout"] = discord.ForumLayoutType(default_layout)
                if hasattr(discord, "ForumOrderType"):
                    kwargs["default_sort_order"] = discord.ForumOrderType(default_sort_order)
            except Exception:
                logger.warning("[tools] could not set forum layout/order for %s", name)
            forum = await guild.create_forum(name, **kwargs)
            return StepResult(success=True, action="create_forum_channel", name=name,
                             detail=f"ID: {forum.id}, Topic: {topic or 'none'}", target_id=forum.id)
        except Exception as e:
            return StepResult(success=False, action="create_forum_channel", name=name, error=str(e))

    async def create_forum_post(self, forum_channel: discord.ForumChannel, title: str,
                                content: str, tags: list = None) -> StepResult:
        try:
            available_tags = forum_channel.available_tags if hasattr(forum_channel, "available_tags") else []
            tag_objects = []
            if tags and available_tags:
                for tag_name in tags:
                    for tag_obj in available_tags:
                        if tag_obj.name.lower() == tag_name.lower():
                            tag_objects.append(tag_obj)
                            break
            thread = await forum_channel.create_thread(
                name=title,
                content=content,
                applied_tags=tag_objects[:5] if tag_objects else [],
                reason=_llm_reason("management")
            )
            return StepResult(success=True, action="create_forum_post", name=title,
                             detail=f"Forum: {forum_channel.name}, ID: {thread.thread.id}",
                             target_id=thread.thread.id)
        except Exception as e:
            return StepResult(success=False, action="create_forum_post", name=title, error=str(e))

    async def manage_forum_tags(self, forum_channel: discord.ForumChannel, tag_name: str,
                                emoji: str = None, action_type: str = "create") -> StepResult:
        try:
            if action_type == "create":
                try:
                    from discord import ForumTag
                    tag = ForumTag(name=tag_name, emoji=emoji)
                    existing_tags = list(forum_channel.available_tags) if hasattr(forum_channel, "available_tags") else []
                    existing_tags.append(tag)
                    await forum_channel.edit(available_tags=existing_tags, reason=_llm_reason("management"))
                    return StepResult(success=True, action="create_forum_tag", name=tag_name,
                                     detail=f"Forum: {forum_channel.name}")
                except ImportError:
                    return StepResult(success=False, action="create_forum_tag", name=tag_name,
                                     error="Forum tags require discord.py 2.0+")
            elif action_type == "delete":
                available_tags = list(forum_channel.available_tags) if hasattr(forum_channel, "available_tags") else []
                new_tags = [t for t in available_tags if t.name.lower() != tag_name.lower()]
                await forum_channel.edit(available_tags=new_tags, reason=_llm_reason("management"))
                return StepResult(success=True, action="delete_forum_tag", name=tag_name,
                                 detail=f"Forum: {forum_channel.name}")
            else:
                return StepResult(success=False, action="manage_forum_tags", name=tag_name,
                                 error=f"Unknown action: {action_type}")
        except Exception as e:
            return StepResult(success=False, action="manage_forum_tags", name=tag_name, error=str(e))

    async def create_stage_channel(self, guild: discord.Guild, name: str, topic: str = None,
                                    category: str = None, bitrate: int = 64000) -> StepResult:
        try:
            cat_obj = discord.utils.get(guild.categories, name=category) if category else None
            kwargs = {"reason": "Azure agentic setup"}
            if cat_obj:
                kwargs["category"] = cat_obj
            if bitrate:
                kwargs["bitrate"] = min(bitrate, 384000)
            stage = await guild.create_stage_channel(name, **kwargs)
            if topic:
                try:
                    await stage.create_instance(topic=topic, reason=_llm_reason("management"))
                except Exception:
                    logger.warning("[tools] could not create stage instance topic for %s", name)
            return StepResult(success=True, action="create_stage_channel", name=name,
                             detail=f"ID: {stage.id}, Topic: {topic or 'none'}", target_id=stage.id)
        except Exception as e:
            return StepResult(success=False, action="create_stage_channel", name=name, error=str(e))

    async def start_stage_instance(self, stage_channel: discord.StageChannel, topic: str,
                                    privacy_level: int = 2) -> StepResult:
        try:
            instance = await stage_channel.create_instance(
                topic=topic,
                privacy_level=privacy_level,
                reason=_llm_reason("management")
            )
            return StepResult(success=True, action="start_stage_instance", name=topic,
                             detail=f"Stage: {stage_channel.name}, ID: {instance.id}",
                             target_id=instance.id)
        except Exception as e:
            return StepResult(success=False, action="start_stage_instance", name=topic, error=str(e))

    async def manage_stage_speaker(self, member: discord.Member, stage_channel: discord.StageChannel,
                                    make_speaker: bool = True) -> StepResult:
        try:
            if make_speaker:
                await member.edit(suppress=False, reason=_llm_reason("management"))
                action_name = "add_speaker"
            else:
                await member.edit(suppress=True, reason=_llm_reason("management"))
                action_name = "remove_speaker"
            return StepResult(success=True, action=action_name, name=member.display_name,
                             detail=f"Stage: {stage_channel.name}")
        except Exception as e:
            return StepResult(success=False, action="manage_stage_speaker",
                             name=member.display_name, error=str(e))

    async def set_voice_bitrate(self, voice_channel: discord.VoiceChannel,
                                bitrate_kbps: int = 64) -> StepResult:
        try:
            if isinstance(bitrate_kbps, str):
                bitrate_kbps = bitrate_kbps.lower().replace("kbps", "").replace(" ", "").strip()
                bitrate_kbps = int(bitrate_kbps) if bitrate_kbps else 64
            bitrate = bitrate_kbps * 1000
            max_bitrate = voice_channel.guild.bitrate_limit if hasattr(voice_channel.guild, "bitrate_limit") else 384000
            bitrate = min(bitrate, max_bitrate)
            await voice_channel.edit(bitrate=bitrate, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_voice_bitrate",
                             name=voice_channel.name,
                             detail=f"Bitrate: {bitrate_kbps}kbps")
        except Exception as e:
            return StepResult(success=False, action="set_voice_bitrate",
                             name=voice_channel.name, error=str(e))

    async def set_voice_user_limit(self, voice_channel: discord.VoiceChannel,
                                    user_limit: int = 0) -> StepResult:
        try:
            await voice_channel.edit(user_limit=user_limit, reason=_llm_reason("management"))
            limit_text = f"{user_limit} users" if user_limit > 0 else "unlimited"
            return StepResult(success=True, action="set_voice_user_limit",
                             name=voice_channel.name,
                             detail=f"User limit: {limit_text}")
        except Exception as e:
            return StepResult(success=False, action="set_voice_user_limit",
                             name=voice_channel.name, error=str(e))

    async def set_voice_region(self, voice_channel: discord.VoiceChannel,
                                region: str = None) -> StepResult:
        try:
            region_obj = None if region is None else discord.VoiceRegion(region)
            await voice_channel.edit(rtc_region=region_obj, reason=_llm_reason("management"))
            return StepResult(success=True, action="set_voice_region",
                             name=voice_channel.name,
                             detail=f"Region: {region or 'automatic'}")
        except Exception as e:
            return StepResult(success=False, action="set_voice_region",
                             name=voice_channel.name, error=str(e))
