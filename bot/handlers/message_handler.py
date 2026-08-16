"""Message handler functions."""

import asyncio
import contextlib
import logging
import re
import threading
import time

import discord

from azure.errors import AzureError, LLMError, RateLimitError

from ..config import (
    BOT_MESSAGE_CACHE_SIZE,
    BOT_MESSAGE_TTL,
    COMMAND_COOLDOWN,
    DELETE_AFTER_SECONDS,
    LOG_MAX_AGE_DAYS,
    RAG_TOP_K,
    RATE_LIMIT_COOLDOWN,
    TRUNC_RAG_LINES,
    TRUNC_RESPONSE_DISPLAY,
    TRUNC_SMALL,
    TRUNC_USER_FACTS,
    TRUNC_VIOLATIONS,
    _bot_messages,
    _bot_messages_lock,
    _cache_lock,
    _response_cache,
)
from .rate_limiter import _check_command_cooldown, _check_rate_limit
from .response_cache import _cache_response, _get_cached_response, _hash_message

logger = logging.getLogger("azure.discord.message")

# Track active confirmation prompts so we only suppress keywords when a
# confirmation is actually pending (prevents blocking normal chat).
_pending_confirmations: dict[str, float] = {}  # key: "user_id:channel_id" → timestamp
_pending_confirm_lock = threading.Lock()
_PENDING_CONFIRM_TTL = 60.0  # seconds
_pending_confirm_last_cleanup: float = 0.0
_PENDING_CLEANUP_INTERVAL = 120.0  # seconds

def _cleanup_pending_confirmations() -> None:
    """Evict expired pending confirmations to prevent unbounded growth."""
    global _pending_confirm_last_cleanup
    now = time.time()
    with _pending_confirm_lock:
        if now - _pending_confirm_last_cleanup < _PENDING_CLEANUP_INTERVAL:
            return
        _pending_confirm_last_cleanup = now
        expired = [k for k, ts in _pending_confirmations.items() if now - ts > _PENDING_CONFIRM_TTL]
        for k in expired:
            del _pending_confirmations[k]

# Guild-level rate limiting
_guild_message_counts: dict[str, list[float]] = {}
_guild_rate_lock = threading.Lock()
_GUILD_RATE_LIMIT = 50  # messages per minute per guild
_GUILD_RATE_WINDOW = 60.0

_guild_last_cleanup: float = 0.0
_GUILD_CLEANUP_INTERVAL = 300.0  # seconds
_GUILD_MAX_TRACKED = 500  # hard cap on guilds tracked

def _check_guild_rate_limit(guild_id: str) -> bool:
    """Check if guild has exceeded its message rate. Returns True if OK."""
    global _guild_last_cleanup
    now = time.time()
    with _guild_rate_lock:
        if guild_id not in _guild_message_counts:
            _guild_message_counts[guild_id] = []
        timestamps = _guild_message_counts[guild_id]
        # Remove old entries
        timestamps[:] = [t for t in timestamps if now - t < _GUILD_RATE_WINDOW]
        # Periodically evict inactive guilds to prevent unbounded growth
        if now - _guild_last_cleanup > _GUILD_CLEANUP_INTERVAL:
            _guild_last_cleanup = now
            stale_guilds = [gid for gid, ts in _guild_message_counts.items() if not ts or (now - ts[-1]) > _GUILD_RATE_WINDOW * 2]
            for gid in stale_guilds:
                del _guild_message_counts[gid]
            # Hard cap: if still too many, drop oldest
            if len(_guild_message_counts) > _GUILD_MAX_TRACKED:
                sorted_keys = sorted(
                    _guild_message_counts.keys(),
                    key=lambda g: _guild_message_counts[g][-1] if _guild_message_counts[g] else 0,
                )
                for k in sorted_keys[: len(sorted_keys) - _GUILD_MAX_TRACKED]:
                    _guild_message_counts.pop(k, None)
        if len(timestamps) >= _GUILD_RATE_LIMIT:
            return False
        timestamps.append(now)
        return True


def _set_pending_confirmation(user_id: str, channel_id: str) -> None:
    with _pending_confirm_lock:
        _pending_confirmations[f"{user_id}:{channel_id}"] = time.time()


def _clear_pending_confirmation(user_id: str, channel_id: str) -> None:
    with _pending_confirm_lock:
        _pending_confirmations.pop(f"{user_id}:{channel_id}", None)


def _has_pending_confirmation(user_id: str, channel_id: str) -> bool:
    _cleanup_pending_confirmations()
    key = f"{user_id}:{channel_id}"
    with _pending_confirm_lock:
        ts = _pending_confirmations.get(key)
        if ts is None:
            return False
        if time.time() - ts > _PENDING_CONFIRM_TTL:
            del _pending_confirmations[key]
            return False
    return True


def is_owner(user, guild=None) -> bool:
    """Check if a user is the server owner."""
    from bot.context import ctx
    if isinstance(user, discord.Member):
        guild = user.guild
    if guild is None and ctx.bot.guilds:
        guild = ctx.bot.guilds[0]
    if guild is None:
        return False
    return guild.owner_id == user.id


def is_allowed_to_chat(message) -> bool:
    """Check if a user is allowed to chat with Azure."""
    from bot.context import ctx
    user_id = str(message.author.id)
    if ctx.bot.application and ctx.bot.application.owner and message.author.id == ctx.bot.application.owner.id:
        return True
    if message.guild and message.guild.owner_id == message.author.id:
        return True
    if message.guild and isinstance(message.author, discord.Member) and message.author.guild_permissions.administrator:
            return True
    if ctx.chat_mode == "anyone":
        return True
    if ctx.chat_mode == "owner_only":
        return is_owner(message.author, message.guild)
    if ctx.chat_mode == "specific_users":
        return user_id in ctx.allowed_user_ids
    if ctx.chat_mode == "dm_only":
        return message.guild is None
    if ctx.chat_mode == "mention_only":
        if not message.guild:
            return False
        return ctx.bot.user in message.mentions
    return True


async def _attention_check(message, text: str, is_dm: bool, mentioned: bool) -> bool:
    """Decide if the bot should engage. No keyword action banks.

    Structural fast path: DMs, @mentions, bot display name.
    Everything else: LLM YES/NO gate (or intent router when available).
    """
    if is_dm or mentioned:
        return True

    # The server owner and administrators are trusted operators. Do not send
    # their explicit requests through a second LLM attention gate; that gate
    # was both slow and prone to dropping valid management commands.
    if message.guild:
        author = getattr(message, "author", None)
        member_permissions = getattr(author, "guild_permissions", None)
        owner_id = getattr(message.guild, "owner_id", None)
        author_id = getattr(author, "id", None)
        if (
            isinstance(owner_id, (int, str))
            and isinstance(author_id, (int, str))
            and owner_id == author_id
        ) or getattr(member_permissions, "administrator", False) is True:
            return True

    text_lower = (text or "").lower().strip()
    if not text_lower:
        return False

    from bot.context import ctx

    from .llm_handler import _is_directed_at_bot

    # Structural only: bot's configured / display name word boundary
    if _is_directed_at_bot(text_lower):
        return True
    bot_user = getattr(ctx.bot, "user", None) if ctx.bot else None
    display = getattr(bot_user, "display_name", None) if bot_user else None
    if isinstance(display, str) and display.strip():
        name = display.lower().strip()
        if name and re.search(r"\b" + re.escape(name) + r"\b", text_lower):
            return True

    # LLM attention check for ambiguous messages (intent routing happens once later)
    prompt = (
        f"Determine if this Discord message needs my attention as the server AI.\n"
        f"Reply ONLY with YES or NO.\n\n"
        f"YES = directed at me, requesting an action from me, or about me\n"
        f"NO = general chat between others not involving me\n\n"
        f"Channel: #{getattr(message.channel, 'name', 'dm')}\n"
        f"Message: \"{text[:200]}\""
    )
    try:
        if not ctx.agent:
            return False
        llm = getattr(ctx.agent, "llm", None)
        if not llm:
            return False
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: llm.chat(
                [
                    {"role": "system", "content": "You are an attention gate. Reply only YES or NO."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=2,
                temperature=0,
            )
        )
        if resp and "YES" in resp.strip().upper():
            return True
    except Exception as e:
        logger.warning("[attention] LLM gate failed: %s", e)
    return False


# _check_rate_limit, _check_command_cooldown — extracted to rate_limiter.py
# _hash_message, _get_cached_response, _cache_response — extracted to response_cache.py
from .context_manager import _add_to_context, _get_conversation_context  # noqa: E402, F401 — re-export for patching


def _get_fallback_response(error_type: str, user_name: str = "") -> str:
    """
    Get a friendly fallback response based on error type.

    Args:
        error_type: Type of error (timeout, llm_error, network_error, unknown)
        user_name: Optional user name for personalization

    Returns:
        Friendly error message
    """
    mention = f" {user_name}" if user_name else ""

    fallbacks = {
        "timeout": f"⏰ Hey{mention}, that's taking longer than expected. The model might be processing a complex request. Try simplifying your question?",
        "llm_error": f"🤖 Oops{mention}! My AI brain hiccupped. Mind trying that again?",
        "network_error": f"📡 Network issue{mention}. Give me a moment and try again?",
        "rate_limit": f"⏱️ Slow down a bit{mention}! I'm getting too many requests. Take a breather and try again in a few seconds.",
        "unknown": f"❌ Something unexpected happened{mention}. I've logged it for investigation. Please try again!",
    }

    return fallbacks.get(error_type, fallbacks["unknown"])


async def _register_bot_message(bot_msg: discord.Message, user_id: str, original_text: str) -> None:
    """
    Register a bot message for reaction-based controls.

    Args:
        bot_msg: The bot's response message
        user_id: ID of the user who triggered the response
        original_text: The original user message text
    """
    msg_id = str(bot_msg.id)
    with _bot_messages_lock:
        # Evict oldest if cache is full
        if msg_id not in _bot_messages and len(_bot_messages) >= BOT_MESSAGE_CACHE_SIZE:
            _bot_messages.popitem(last=False)

        _bot_messages[msg_id] = {
            "user_id": user_id,
            "original_text": original_text,
            "timestamp": time.time(),
            "channel_id": bot_msg.channel.id,
        }

    # Add reaction controls OUTSIDE the lock to avoid holding a
    # threading.Lock across await points (deadlock risk).
    try:
        await bot_msg.add_reaction("❌")  # Delete
        await bot_msg.add_reaction("🔄")  # Regenerate
    except Exception as e:
        logger.warning(f"[reactions] Failed to add reactions: {e}")


async def _get_bot_message_metadata(message_id: str) -> dict | None:
    """
    Retrieve metadata for a bot message.

    Args:
        message_id: Discord message ID

    Returns:
        Metadata dict or None if not found/expired
    """
    with _bot_messages_lock:
        if message_id not in _bot_messages:
            return None

        metadata = _bot_messages[message_id]

        # Check if expired
        age = time.time() - metadata["timestamp"]
        if age > BOT_MESSAGE_TTL:
            del _bot_messages[message_id]
            return None

        # Move to end (LRU)
        _bot_messages.move_to_end(message_id)

        return metadata


async def handle_bot_message_reaction(reaction: discord.Reaction, user: discord.User) -> bool:
    """Handle reactions on bot messages (delete, regenerate)."""
    if user.bot:
        return False

    message = reaction.message
    emoji = str(reaction.emoji)
    metadata = await _get_bot_message_metadata(str(message.id))
    if not metadata:
        return False

    if str(user.id) != metadata["user_id"]:
        try:
            await reaction.remove(user)
        except Exception as e:
            logger.warning("[reactions] Failed to remove reaction: %s", e)
        return False

    if emoji == "\u274c":
        return await _handle_delete_reaction(message)

    if emoji == "\U0001f504":
        return await _handle_regenerate_reaction(message, metadata, user)

    return False


async def _handle_delete_reaction(message: discord.Message) -> bool:
    """Handle delete reaction on a bot message."""
    try:
        await message.delete()
        with _bot_messages_lock:
            _bot_messages.pop(str(message.id), None)
        return True
    except Exception as e:
        logger.error(f"[reactions] Failed to delete message: {e}")
        return False


async def _handle_regenerate_reaction(message: discord.Message, metadata: dict, user: discord.User) -> bool:
    """Handle regenerate reaction on a bot message."""
    try:
        from azure.discord_responses import format_reply, short_reply
        from bot.context import ctx


        channel = ctx.bot.get_channel(metadata["channel_id"])
        if not channel:
            return False

        server_id = str(message.guild.id) if message.guild else "dm"
        cache_key = _hash_message(metadata["original_text"], metadata["user_id"], server_id)
        with _cache_lock:
            _response_cache.pop(cache_key, None)

        try:
            await message.edit(content="\U0001f504 *Regenerating response...*")
        except Exception as e:
            logger.warning("[reactions] Failed to edit message to 'regenerating': %s", e)

        async with channel.typing():
            try:
                reply = await ctx.agent.handle(
                    user=user.display_name,
                    message=metadata["original_text"],
                    server_name=message.guild.name if message.guild else "DM",
                    user_id=metadata["user_id"],
                    guild=message.guild,
                    channel=channel,
                    event_loop=asyncio.get_running_loop(),
                    discord_tools=ctx.mgmt_tools if ctx.mgmt_tools else None,
                )
                if not reply:
                    return False

                from .reply_utils import deliver_one_message, format_final_reply
                formatted = format_final_reply(reply, user.display_name, format_reply, short_reply)
                await _cache_response(metadata["original_text"], metadata["user_id"], server_id, reply)

                new_msg = await deliver_one_message(channel=channel, content=formatted, existing=message)
                if new_msg is not None:
                    await _register_bot_message(new_msg, metadata["user_id"], metadata["original_text"])
                return True
            except Exception as e:
                logger.error(f"[reactions] Regeneration failed: {e}")
                fallback = _get_fallback_response("llm_error", user.display_name)
                from .reply_utils import deliver_one_message as _deliver
                await _deliver(channel=channel, content=fallback, existing=message)
                return False
    except Exception as e:
        logger.error(f"[reactions] Failed to regenerate: {e}")
        return False




async def _cognitize(
    message,
    text: str,
    user: str,
    is_directed: bool,
    is_dm: bool,
    mentioned: bool,
    server_name: str,
):
    """Run the message through the 10-phase cognitive pipeline."""
    from bot.context import ctx

    from ..discord_bot_v1 import _last_cognitive_state
    if not ctx.cognitive_pipeline:
        return None, ""

    try:
        history = ctx.agent.short_term.to_history() if ctx.agent else []
        user_facts = []
        server_facts = []

        if ctx.agent and ctx.agent.long_term:
            for k in list(ctx.agent.long_term.facts.keys())[:TRUNC_USER_FACTS]:
                v = ctx.agent.long_term.facts[k].get("v", "")
                if v:
                    user_facts.append(f"{k}: {v}")

        role_ctx = None
        is_admin = False
        try:
            from azure.cognition.role_context import RoleContext
            if message.guild and isinstance(message.author, discord.Member):
                role_ctx = RoleContext.from_member(message.author)
                is_admin = role_ctx.is_administrator or role_ctx.is_server_owner
            else:
                role_ctx = RoleContext.dm()
        except Exception as role_err:
            logger.error(f"[cognitize] RoleContext build failed: {role_err}")
            if message.guild and isinstance(message.author, discord.Member):
                is_admin = message.author.guild_permissions.administrator

        rag_context = ""
        if ctx.agent and ctx.agent.rag:
            try:
                memory_scope = (
                    f"guild:{message.guild.id}" if message.guild
                    else f"dm:{message.author.id}"
                )
                rag_context = ctx.agent.rag.search_as_context(
                    text, k=RAG_TOP_K, scope=memory_scope,
                )
                if rag_context:
                    for line in rag_context.split("\n")[:TRUNC_RAG_LINES]:
                        if ":" in line:
                            server_facts.append(line.strip()[:100])
            except Exception as e:
                logger.warning("[cognitize] RAG search failed: %s", e)

        state = await ctx.cognitive_pipeline.process(
            message=text,
            user_name=user,
            is_directed=is_directed,
            is_dm=is_dm,
            is_mentioned=mentioned,
            params={},
            is_admin=is_admin,
            has_guild=message.guild is not None,
            extra_context=server_name,
            conversation_history=history,
            user_memory=user_facts,
            server_memory=server_facts,
            prior_plans=[],
            tool_state=[],
            adversarial_review=True,
        )
        if role_ctx is not None:
            state.role_context = role_ctx

        user_id = str(message.author.id)
        _last_cognitive_state[user_id] = state
        # Evict oldest if over limit. _last_cognitive_state is a plain dict, so
        # popitem() takes no args (the last= kwarg is OrderedDict-only). Dicts
        # preserve insertion order, so the first key is the oldest entry.
        if len(_last_cognitive_state) > 5000:
            oldest = next(iter(_last_cognitive_state))
            del _last_cognitive_state[oldest]

        if message.guild:
            await _update_cognition_panel(message, state)

        return state, state.response
    except Exception as e:
        logger.error(f"[cognitize] error: {e}")
        return None, ""

async def _update_cognition_panel(message, cog_state):
    """Edit the persistent panel message in-place if one exists for this server."""

    from ..discord_bot_v1 import _cognition_panel_messages
    if not cog_state or not message.guild:
        return
    guild_id = str(message.guild.id)
    msg_id = _cognition_panel_messages.get(guild_id)
    if not msg_id:
        return
    try:
        channel = message.channel
        panel_msg = await channel.fetch_message(msg_id)
        embed = _build_cognition_embed(cog_state)
        await panel_msg.edit(embed=embed)
    except discord.NotFound:
        _cognition_panel_messages.pop(guild_id, None)
    except Exception as e:
        logger.error(f"[cognition_panel] edit error: {e}")

def _build_cognition_embed(state):
    """Build the rich embed for the persistent cognitive panel."""
    from azure.discord_responses import BLUE, EmbedBuilder
    conf = state.confidence_summary()
    risk_color_map = {"LOW": 0x2ecc71, "MEDIUM": 0xf1c40f, "HIGH": 0xe67e22, "CRITICAL": 0xe74c3c}
    risk_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}

    phase_lines = []
    for p in state.phases:
        if p.phase in ("TOTAL",):
            continue
        conf_str = f" (conf={p.confidence:.0%})" if p.confidence > 0 else ""
        phase_lines.append(f"`{p.phase}` {p.duration_ms:.1f}ms → {p.result}{conf_str}")

    embed = (
        EmbedBuilder()
        .title(f"🧠 Cognitive Panel · {state.true_intent}")
        .description(
            f"**User:** {state.user_name} · **{state.context or 'general'}** · "
            f"{risk_emoji.get(state.risk.value, '❓')} **{state.risk.value}** · "
            f"Overall conf: `{conf['overall']:.0%}`\n"
            f"**Modes:** {', '.join(f'__{m.value}__' for m in state.modes) if state.modes else '_none_'}\n"
            f"**Tool:** `{state.tool_decision.value}` → "
            f"{', '.join(state.selected_tools) or '_direct_'}"
        )
        .color(risk_color_map.get(state.risk.value, BLUE))
        .field(
            "Hidden Goals",
            state.hidden_goals and "\n".join(f"• {g}" for g in state.hidden_goals) or "_none detected_",
            inline=False,
        )
        .field(
            "Phase Breakdown",
            "\n".join(phase_lines[:8]) or "_no phases_",
            inline=False,
        )
        .field(
            "Plan",
            f"{len(state.plan.execution_order)} steps"
            + (" · ⚠️ confirm required" if state.plan.requires_confirmation else "")
            + (f"\n__{state.plan.objective}__" if state.plan.objective else ""),
            inline=False,
        )
        .field(
            "Response",
            (state.response or "")[:TRUNC_RESPONSE_DISPLAY] + ("..." if len(state.response or "") > TRUNC_RESPONSE_DISPLAY else "")
            or "_no response generated_",
            inline=False,
        )
        .footer(
            f"Azure v2 Cognitive · {state.phase_time_total():.1f}ms total"
            f" · Semantic: {'Yes' if state.semantic_reasoning_used else 'No'}"
        )
        .timestamp()
        .build()
    )
    return embed


def _format_server_info(state: dict, guild, scope: str = "overview") -> str:
    """Format live, read-only guild data without exposing permission internals."""
    scope = (scope or "overview").strip().lower()
    if scope not in {"overview", "members", "channels", "roles", "settings"}:
        scope = "overview"

    name = state.get("server_name") or getattr(guild, "name", "this server")
    lines = [f"**Server information: {name}**"]
    if scope in {"overview", "members"}:
        members = state.get("member_count", getattr(guild, "member_count", 0))
        online = state.get("online_count", 0)
        lines.append(f"Members: **{members}** ({online} online)")
    if scope in {"overview", "channels"}:
        channels = [str(item.get("name", "?")) for item in state.get("channels", [])]
        lines.append("Channels: " + (", ".join(channels[:40]) if channels else "none"))
    if scope in {"overview", "roles"}:
        roles = [str(item.get("name", "?")) for item in state.get("roles", [])]
        lines.append("Roles: " + (", ".join(roles[:40]) if roles else "none"))
    if scope in {"overview", "settings"}:
        lines.append(f"Verification: {state.get('verification_level', 'unknown')}")
        lines.append(f"Content filter: {state.get('explicit_content_filter', 'unknown')}")
        lines.append(f"Categories: {len(state.get('categories', []))}")
    if scope == "members":
        member_names = [getattr(member, "display_name", getattr(member, "name", "?")) for member in getattr(guild, "members", [])]
        if member_names:
            lines.append("Visible members: " + ", ".join(str(name) for name in member_names[:30]))
    return "\n".join(lines)[:1900]


def _format_member_info(member, guild) -> str:
    """Format non-sensitive member metadata available from Discord."""
    roles = [
        str(getattr(role, "name", ""))
        for role in getattr(member, "roles", [])
        if not getattr(role, "is_default", lambda: False)()
    ]
    status = getattr(getattr(member, "status", None), "name", None) or str(getattr(member, "status", "unknown"))
    joined = getattr(member, "joined_at", None)
    joined_text = joined.strftime("%Y-%m-%d") if joined is not None and hasattr(joined, "strftime") else "unknown"
    display_name = getattr(member, "display_name", None) or getattr(member, "name", "unknown")
    username = getattr(member, "name", display_name)
    lines = [
        f"**Member information: {display_name}**",
        f"Username: `{username}`",
        f"User ID: `{getattr(member, 'id', 'unknown')}`",
        f"Bot account: {'yes' if getattr(member, 'bot', False) else 'no'}",
        f"Status: **{status}**",
        f"Joined server: **{joined_text}**",
        "Roles: " + (", ".join(roles[:30]) if roles else "none beyond @everyone"),
    ]
    return "\n".join(lines)[:1900]


def _format_channel_info(channel) -> str:
    """Format safe, read-only metadata for one guild channel."""
    category = getattr(getattr(channel, "category", None), "name", None) or "none"
    channel_type = str(getattr(channel, "type", "unknown"))
    lines = [
        f"**Channel information: #{getattr(channel, 'name', 'unknown')}**",
        f"Type: **{channel_type}**",
        f"Category: **{category}**",
        f"Channel ID: `{getattr(channel, 'id', 'unknown')}`",
    ]
    topic = getattr(channel, "topic", None)
    if topic:
        lines.append(f"Topic: {str(topic)[:500]}")
    if hasattr(channel, "nsfw"):
        lines.append(f"NSFW: **{'yes' if channel.nsfw else 'no'}**")
    if hasattr(channel, "slowmode_delay"):
        lines.append(f"Slowmode: **{channel.slowmode_delay}s**")
    if hasattr(channel, "user_limit"):
        lines.append(f"User limit: **{channel.user_limit or 'unlimited'}**")
    if hasattr(channel, "bitrate"):
        lines.append(f"Bitrate: **{channel.bitrate // 1000}kbps**")
    return "\n".join(lines)[:1900]


def _format_role_info(role, guild) -> str:
    """Format safe, read-only metadata for one guild role."""
    permissions = getattr(role, "permissions", None)
    try:
        permission_map = permissions.to_dict() if permissions is not None else {}
    except Exception:
        permission_map = {}
    enabled = [name.replace("_", " ") for name, value in permission_map.items() if value]
    members = list(getattr(role, "members", []) or [])
    return "\n".join([
        f"**Role: {getattr(role, 'name', 'unknown')}**",
        f"ID: `{getattr(role, 'id', 'unknown')}`",
        f"Position: {getattr(role, 'position', 'unknown')}",
        f"Managed: {'yes' if getattr(role, 'managed', False) else 'no'}",
        f"Displayed separately: {'yes' if getattr(role, 'hoist', False) else 'no'}",
        f"Mentionable: {'yes' if getattr(role, 'mentionable', False) else 'no'}",
        f"Members: {len(members)}",
        "Enabled permissions: " + (", ".join(enabled[:24]) if enabled else "none"),
    ])[:1900]


def _format_server_data(result, data_type: str, guild, limit: int = 20) -> str:
    """Format privileged live server data without returning raw Discord objects."""
    if not getattr(result, "success", False):
        return f"I couldn't read that server data: {getattr(result, 'error', '') or 'Discord denied the request.'}"
    state = getattr(result, "after_state", {}) or {}
    data_type = data_type.replace("-", "_").lower()
    title = {
        "automod_rules": "AutoMod rules",
        "ban_list": "Banned users",
        "onboarding": "Server onboarding",
    }.get(data_type, "Server data")
    lines = [f"**{title}: {getattr(guild, 'name', 'this server')}**"]
    if data_type == "automod_rules":
        rules = state.get("rules", []) if isinstance(state, dict) else []
        if not rules:
            return "**AutoMod rules**\nNo AutoMod rules are configured."
        for item in rules[:limit]:
            lines.append(
                f"- {item.get('name', 'unnamed')} | "
                f"{'enabled' if item.get('enabled') else 'disabled'} | "
                f"{item.get('trigger_type', 'unknown')}"
            )
    elif data_type == "ban_list":
        bans = state.get("bans", []) if isinstance(state, dict) else []
        if not bans:
            return "**Banned users**\nNo banned users were returned."
        for item in bans[:limit]:
            lines.append(
                f"- {item.get('username', 'unknown')} (`{item.get('user_id', 'unknown')}`): "
                f"{str(item.get('reason', 'No reason'))[:180]}"
            )
    elif data_type == "onboarding":
        info = state.get("onboarding", {}) if isinstance(state, dict) else {}
        lines.append(f"Enabled: **{'yes' if info.get('enabled') else 'no'}**")
        lines.append(f"Mode: **{info.get('mode', 'unknown')}**")
        defaults = info.get("default_channels", [])
        lines.append("Default channels: " + (", ".join(map(str, defaults[:20])) if defaults else "none"))
        prompts = info.get("prompts", [])
        lines.append(f"Prompts: **{len(prompts)}**")
        for prompt in prompts[:10]:
            lines.append(f"- {prompt.get('title', 'Untitled')}")
    return "\n".join(lines)[:1900]


def _format_audit_logs(result, guild, limit: int = 10, target_name: str = "") -> str:
    """Format privileged live audit entries without exposing raw objects."""
    if not getattr(result, "success", False):
        return f"I couldn't read this server's audit log: {getattr(result, 'error', '') or 'Discord denied the request.'}"
    state = getattr(result, "after_state", {}) or {}
    entries = state.get("logs", []) if isinstance(state, dict) else []
    if target_name:
        needle = target_name.casefold()
        entries = [
            entry for entry in entries
            if isinstance(entry, dict) and needle in str(entry.get("target", "")).casefold()
        ]
    if not entries:
        return f"**Audit log: {getattr(guild, 'name', 'this server')}**\nNo matching entries found."
    lines = [f"**Recent audit activity: {getattr(guild, 'name', 'this server')}**"]
    for entry in entries[: max(1, min(int(limit or 10), 20))]:
        if not isinstance(entry, dict):
            continue
        lines.append(
            f"- `{entry.get('action', 'unknown')}` by **{entry.get('user', 'unknown')}** "
            f"on **{entry.get('target', 'unknown')}** ({entry.get('created_at', 'time unknown')})"
            f"\n  Reason: {entry.get('reason', 'No reason provided')}"
        )
    return "\n".join(lines)[:1900]


def _format_health_analysis(report: dict, server_name: str) -> str:
    """Format the live analyzer output for a Discord-sized response."""
    if not isinstance(report, dict):
        return f"**Server health: {server_name}**\nNo health report was returned."
    score = report.get("score", "unknown")
    lines = [f"**Server health: {report.get('server_name', server_name)}**", f"Overall score: **{score}/100**"]
    categories = report.get("categories") or {}
    for name, data in categories.items():
        if isinstance(data, dict):
            component = data.get("score", data.get("score_component", "?"))
            lines.append(f"- {str(name).title()}: **{component}**")
    issues = [str(item) for item in (report.get("issues") or [])[:6]]
    recommendations = [
        str(item.get("text", item)) if isinstance(item, dict) else str(item)
        for item in (report.get("recommendations") or [])[:5]
    ]
    if issues:
        lines.extend(["", "**Issues:**"] + [f"- {item}" for item in issues])
    if recommendations:
        lines.extend(["", "**Recommendations:**"] + [f"- {item}" for item in recommendations])
    return "\n".join(lines)[:1900]


def _can_manage_server(message) -> bool:
    """Return whether the author may change this guild's configuration."""
    guild = getattr(message, "guild", None)
    author = getattr(message, "author", None)
    if not guild or not author:
        return False
    if getattr(guild, "owner_id", None) == getattr(author, "id", None):
        return True
    permissions = getattr(author, "guild_permissions", None)
    return bool(
        getattr(permissions, "administrator", False)
        or getattr(permissions, "manage_guild", False)
    )

def _rotate_cognition_logs(log_dir, max_age_days=None):
    """Delete cognition log files older than max_age_days."""
    max_age_days = max_age_days or LOG_MAX_AGE_DAYS
    if not log_dir.exists():
        return
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    for f in log_dir.glob("cognitive_*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except Exception as e:
            logger.warning("[cognition] Failed to remove stale log file: %s", e)
    if removed:
        logger.info(f"[azure] cognition log rotation: removed {removed} stale log(s)")

def _persist_interaction(
    message,
    user: str,
    text: str,
    reply: str,
    tracker=None,
    *,
    cached: bool = False,
    t0: float | None = None,
    tokens_used: int = 0,
    error: bool = False,
) -> None:
    """Persist conversation + rolling stats for the web dashboard (best-effort).

    Called on the successful agent path. Never raises into the Discord pipeline.
    """
    try:
        from azure.database import (
            BotStats,
            ConversationMessage,
            get_shared_db,
        )

        db = get_shared_db()
        if db is None:
            return

        now = time.time()
        elapsed_ms = int(max(0.0, (now - t0) * 1000)) if t0 else 0
        guild = getattr(message, "guild", None)
        channel = getattr(message, "channel", None)

        db.save_conversation(
            ConversationMessage(
                user_id=str(getattr(message.author, "id", "")),
                user_name=str(user or getattr(message.author, "display_name", "")),
                server_id=str(guild.id) if guild else "dm",
                server_name=(guild.name if guild else "DM"),
                channel_id=str(getattr(channel, "id", "") or ""),
                channel_name=getattr(channel, "name", None) or (
                    "DM" if not guild else str(getattr(channel, "id", ""))
                ),
                message=(text or "")[:4000],
                response=(reply or "")[:8000],
                timestamp=now,
                cached=bool(cached),
                tokens_used=int(tokens_used or 0),
                response_time_ms=elapsed_ms,
            )
        )

        # One lightweight stats sample per interaction so aggregate dashboard
        # metrics (messages, cache rate, latency) stay non-zero.
        active_servers = 0
        try:
            from bot.context import ctx
            active_servers = len(getattr(ctx.bot, "guilds", []) or [])
        except Exception as e:
            logger.warning("[persist] Failed to get active server count: %s", e)
            active_servers = 1 if guild else 0

        db.save_stats(
            BotStats(
                timestamp=now,
                messages_processed=1,
                cache_hits=1 if cached else 0,
                cache_misses=0 if cached else 1,
                errors=1 if error else 0,
                avg_response_time_ms=float(elapsed_ms),
                total_tokens_used=int(tokens_used or 0),
                active_users=1,
                active_servers=active_servers,
            )
        )
    except Exception as e:
        logger.warning("[persist] interaction write failed: %s", e)


async def on_message(message):
    from azure.logging_config import clear_request_context, generate_execution_id, set_request_context

    exec_id = generate_execution_id()
    set_request_context(execution_id=exec_id, user_id=str(message.author.id))

    # Start typing indicator immediately so user sees feedback right away
    typing_task = None
    async def _keep_typing():
        while True:
            try:
                async with message.channel.typing():
                    await asyncio.sleep(5)
            except Exception:
                return
    typing_task = asyncio.create_task(_keep_typing())
    try:
        await _on_message_inner(message)
    except RateLimitError as e:
        logger.warning("[on_message] rate limited: retry_after=%.1fs", getattr(e, 'retry_after', 0))
        try:
            await message.reply(f"Slow down! Please wait {e.retry_after:.0f}s before trying again.")
        except Exception:
            with contextlib.suppress(Exception):
                await message.channel.send(f"Slow down! Please wait {e.retry_after:.0f}s before trying again.")
    except LLMError as e:
        logger.error("[on_message] LLM error: %s", str(e)[:200])
        try:
            await message.reply("My AI model is temporarily unavailable. Please try again shortly.")
        except Exception:
            with contextlib.suppress(Exception):
                await message.channel.send("My AI model is temporarily unavailable. Please try again shortly.")
    except AzureError as e:
        logger.error("[on_message] Azure error: %s", str(e)[:200])
        try:
            await message.reply("An internal error occurred while processing your request.")
        except Exception:
            with contextlib.suppress(Exception):
                await message.channel.send("An internal error occurred while processing your request.")
    except Exception as e:
        logger.exception("[FATAL] on_message crashed: %s", str(e)[:300])
        try:
            await message.reply("An internal error occurred while processing your request.")
        except Exception:
            try:
                await message.channel.send("An internal error occurred while processing your request.")
            except Exception as e:
                logger.warning("[on_message] Failed to send error fallback: %s", e)
    finally:
        clear_request_context()
        if typing_task and not typing_task.done():
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task


async def _on_message_inner(message):
    """Natural language agentic pipeline. No `!` prefix required."""
    from azure.discord_responses import format_reply, short_reply
    from azure.input_validator import validate_input
    from bot.context import ctx

    from .llm_handler import _is_directed_at_bot, _llm_response

    if message.author == ctx.bot.user:
        return
    if message.author.bot and not message.webhook_id:
        return

    # Prefix commands are dispatched separately via bot.process_commands();
    # don't also answer them as natural-language chat.
    prefix = getattr(ctx.bot, "command_prefix", None)
    if isinstance(prefix, str) and prefix and message.content.startswith(prefix):
        return

    # === GUILD-LEVEL RATE LIMITING ===
    # If the guild is exceeding its overall message rate, only respond to
    # @mentions and DMs (skip non-mentioned messages to reduce load)
    if message.guild:
        guild_id = str(message.guild.id)
        if not _check_guild_rate_limit(guild_id):
            mentioned = ctx.bot.user in message.mentions
            is_dm = False
            if not mentioned and not is_dm:
                return

    # === DATABASE ACCESS CONTROL CHECK ===
    try:
        from azure.database import get_shared_db
        db = get_shared_db()

        # Check user ban (offload to thread — DB I/O blocks the event loop)
        user_ac = await asyncio.to_thread(db.get_access_control, str(message.author.id))
        if user_ac == "deny":
            return

        # Check guild ban
        if message.guild:
            guild_ac = await asyncio.to_thread(db.get_access_control, str(message.guild.id))
            if guild_ac == "deny":
                return
    except Exception as e:
        logger.error(f"[access] DB check failed: {e}")

    # === RATE LIMITING ===
    is_allowed, cooldown = await _check_rate_limit(message.author.id, str(message.guild.id) if message.guild else None)
    if not is_allowed:
        if cooldown >= RATE_LIMIT_COOLDOWN - 1:
            try:
                await message.channel.send(
                    f"⏱️ {message.author.mention}, you're sending messages too quickly. Please wait {int(cooldown)}s.",
                    delete_after=DELETE_AFTER_SECONDS
                )
            except Exception as e:
                logger.warning("[rate_limit] Failed to send cooldown message: %s", e)
        return

    # === COMMAND COOLDOWN ===
    is_owner_or_admin = False
    if message.guild:
        member = message.guild.get_member(message.author.id)
        is_owner_or_admin = (
            message.guild.owner_id == message.author.id or
            (member and member.guild_permissions.administrator)
        )

    can_execute, cmd_cooldown = await _check_command_cooldown(
        message.author.id,
        bypass_for_owner=is_owner_or_admin
    )

    if not can_execute:
        try:
            await message.add_reaction("⏰")
            if cmd_cooldown >= COMMAND_COOLDOWN - 0.5:
                await message.channel.send(
                    f"⏳ {message.author.mention}, please wait {cmd_cooldown:.1f}s before your next command.",
                    delete_after=max(3, cmd_cooldown)
                )
        except Exception as e:
            logger.warning("[cooldown] Failed to send cooldown reaction/message: %s", e)
        return

    # === MODERATION PIPELINE ===
    if message.guild:
        try:
            if ctx.moderation_service and ctx.moderation_service.engine:
                msg_data = {
                    "user_id": str(message.author.id),
                    "user_name": message.author.display_name,
                    "guild_id": str(message.guild.id),
                    "guild_name": message.guild.name,
                    "channel_id": str(message.channel.id),
                    "channel_name": getattr(message.channel, "name", ""),
                    "message_id": str(message.id),
                    "content": message.content,
                }
                report = await ctx.moderation_service.classify(msg_data)
                if report and report.action != "allow":
                    if ctx.admin_channel:
                        embed = discord.Embed(
                            title="Moderation Report",
                            description=report.reason,
                            color=0xe74c3c if report.action in ("ban", "kick") else 0xf1c40f,
                        )
                        embed.add_field(name="User", value=report.user_name, inline=True)
                        embed.add_field(name="Action", value=report.action, inline=True)
                        embed.add_field(name="Confidence", value=f"{report.confidence:.0%}", inline=True)
                        embed.add_field(name="Subsystem", value=report.subsystem, inline=True)
                        if report.content:
                            embed.add_field(name="Content", value=report.content[:200], inline=False)
                        await ctx.admin_channel.send(embed=embed)
                    await ctx.moderation_service.take_action(report)
            elif ctx.agent and ctx.agent.moderation:
                mod_report = await ctx.agent.moderation.on_message(message)
                if mod_report and ctx.admin_channel and not ctx.agent.moderation.policy.report_aggregated:
                        embed_dict = mod_report.to_embed_dict()
                        embed = discord.Embed.from_dict(embed_dict)
                        await ctx.admin_channel.send(embed=embed)
        except Exception as e:
            logger.error(f"[moderation] error: {e}")

    # === CONFIRMATION RESPONSE FILTER ===
    text_stripped = message.content.strip()
    if text_stripped.upper() in ("CONFIRM", "CANCEL", "YES", "NO", "Y", "APPROVE") and len(text_stripped) <= 10 and _has_pending_confirmation(str(message.author.id), str(message.channel.id)):
            return

    # === v3: PLUGIN MESSAGE INTERCEPTION ===
    if ctx.plugin_manager is not None:
        try:
            plugin_response = ctx.plugin_manager.handle_message(message, {"bot": ctx.bot, "agent": ctx.agent})
            if plugin_response:
                await message.channel.send(plugin_response)
                return
        except Exception as e:
            logger.error(f"[plugin] message handling error: {e}")

    # === INTENT CLASSIFICATION ===
    if not is_allowed_to_chat(message):
        return

    is_dm = message.guild is None
    mentioned = ctx.bot.user in message.mentions
    user = message.author.display_name
    text = _strip_discord_mentions(message.content, ctx.bot.user)

    if not text.strip():
        return

    # === INPUT VALIDATION (Security Layer) ===
    # Offload to thread — regex/ML-heavy validation blocks the event loop
    validation_result = await asyncio.to_thread(validate_input, text, "message")

    if validation_result.is_blocked:
        logger.warning(
            "[security] Blocked malicious input from %s (%s): violations=%s",
            str(user)[:32], str(message.author.id)[:20],
            [v[:80] for v in validation_result.violations[:5]],
        )
        try:
            await message.channel.send(
                f"⚠️ {message.author.mention}, your message contains suspicious patterns. "
                f"Reason: {', '.join(validation_result.violations[:TRUNC_VIOLATIONS])}",
                delete_after=DELETE_AFTER_SECONDS
            )
        except Exception as e:
            logger.warning("[security] Failed to send block notification: %s", e)
        return

    text = validation_result.sanitized_input

    is_directed = _is_directed_at_bot(text.lower())

    has_images = message.attachments and any(
        a.content_type and a.content_type.startswith("image/") for a in message.attachments
    )

    if has_images:
        stripped = text.strip()
        if not stripped or len(stripped) < 5:
            if is_directed or is_dm or mentioned:
                with contextlib.suppress(Exception):
                    await message.channel.send(
                        "I can't see images directly — I'm a text-only model. "
                        "If you tell me what you're showing me, I can help!"
                    )
            return

    if not await _attention_check(message, text, is_dm, mentioned):
        return

    user_roles_list = []
    member_obj = message.guild.get_member(message.author.id) if message.guild else None
    if member_obj:
        user_roles_list = [r.name for r in member_obj.roles if not r.is_default()]

    # LLM-first intent — used for routing (not fire-and-forget)
    routed_intent = None
    if ctx.intent_classifier is not None:
        try:
            routed_intent = await asyncio.to_thread(
                ctx.intent_classifier.classify,
                text=text,
                user_name=user,
                is_dm=is_dm,
                is_mentioned=mentioned,
                server_name=message.guild.name if message.guild else "",
                channel_name=message.channel.name if hasattr(message.channel, "name") else "",
                user_roles=user_roles_list,
                server_member_count=message.guild.member_count if message.guild else 0,
            )
            route = getattr(routed_intent, "route", None) or routed_intent.action
            logger.info(
                "[intent] route=%s action=%s conf=%.2f user=%s",
                route, routed_intent.action, routed_intent.confidence, user[:32],
            )
            if str(route).lower() == "ignore" and not mentioned and not is_dm:
                return
        except Exception:
            logger.exception("[message_handler] intent classify failed")
            routed_intent = None

    if ctx.chat_mode == 'dm_only' and not is_dm:
        return
    if ctx.chat_mode == 'mention_only' and not mentioned:
        return

    server_id = str(message.guild.id) if message.guild else "dm"
    cached_response = await _get_cached_response(text, str(message.author.id), server_id)

    if cached_response:
        from azure.discord_responses import format_reply, short_reply

        from .reply_utils import deliver_one_message, format_final_reply
        formatted = format_final_reply(cached_response, user, format_reply, short_reply)
        sent = await deliver_one_message(channel=message.channel, content=formatted)
        if sent is not None:
            await _register_bot_message(sent, str(message.author.id), text)
        await asyncio.to_thread(
            _persist_interaction,
            message, user, text, cached_response,
            cached=True, t0=time.time(),
        )
        return

    from azure.telemetry import ExecutionTracker
    server_name = message.guild.name if message.guild else 'DM'
    tracker = ExecutionTracker(user=user, guild=server_name, request_text=text)

    # Find a safe channel for progress updates (survives original channel deletion)
    async def _find_safe_channel(guild):
        """Find general or first text channel for agentic updates."""
        if not guild:
            return None
        # Prefer "general"
        for ch in guild.text_channels:
            if ch.name.lower() in ("general", "chat", "main"):
                return ch
        # Fallback to first text channel the bot can send to
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                return ch
        return None

    safe_channel = await _find_safe_channel(message.guild) if message.guild else None

    # Initial progress message — real presentation (no hardcoded fake steps)
    progress_msg = None
    for _attempt in range(3):
        try:
            progress_msg = await message.channel.send(tracker.get_discord_progress_text())
            break
        except discord.HTTPException:
            if _attempt < 2:
                await asyncio.sleep(0.5)
            else:
                logger.error("[agent] Failed to send progress message after retries")
                return

    # Get the event loop BEFORE entering the executor thread
    event_loop = asyncio.get_running_loop()

    # Debounced Discord edits driven by real ExecutionTracker stages
    progress_ref = {"msg": progress_msg}  # mutable container for closure
    edit_state = {
        "last_time": 0.0,
        "pending": False,
        "pending_timer": None,
        "last_text": progress_msg.content if hasattr(progress_msg, "content") else "",
        "closed": False,
    }
    edit_min_interval = 0.45  # Discord-friendly: avoid edit spam

    def _schedule_progress_edit(force: bool = False):
        """Push latest presentation text to Discord (debounced unless force)."""
        if edit_state["closed"]:
            return

        def _fire():
            if edit_state["closed"]:
                return
            new_text = tracker.get_discord_progress_text()
            if not force and new_text == edit_state["last_text"]:
                edit_state["pending"] = False
                return
            edit_state["last_time"] = time.time()
            edit_state["pending"] = True
            edit_state["last_text"] = new_text
            current_msg = progress_ref["msg"]

            async def _do_edit():
                try:
                    if edit_state["closed"]:
                        return
                    await current_msg.edit(content=new_text)
                except discord.NotFound:
                    # Original channel or message was deleted — migrate to safe channel
                    if safe_channel and not edit_state["closed"]:
                        try:
                            new_msg = await safe_channel.send(new_text)
                            progress_ref["msg"] = new_msg
                            edit_state["last_text"] = new_text
                        except Exception as e:
                            logger.warning("[telemetry] Safe channel fallback failed: %s", e)
                            edit_state["closed"] = True
                            _stop_heartbeat()
                    else:
                        edit_state["closed"] = True
                        _stop_heartbeat()
                except Exception as e:
                    logger.warning("[telemetry] Discord progress edit failed: %s", e)
                finally:
                    edit_state["pending"] = False

            try:
                asyncio.run_coroutine_threadsafe(_do_edit(), event_loop)
            except Exception as e:
                logger.warning("[telemetry] schedule edit failed: %s", e)
                edit_state["pending"] = False

        now = time.time()
        time_since_last = now - edit_state["last_time"]

        if edit_state["pending_timer"] is not None:
            try:
                edit_state["pending_timer"].cancel()
            except Exception as e:
                logger.warning("[telemetry] Failed to cancel pending timer: %s", e)
            edit_state["pending_timer"] = None

        if force or (time_since_last >= edit_min_interval and not edit_state["pending"]):
            _fire()
        else:
            delay = max(0.05, edit_min_interval - time_since_last)

            def _delayed():
                edit_state["pending_timer"] = None
                if not edit_state["pending"]:
                    _fire()

            # call_later is NOT thread-safe; use call_soon_threadsafe to schedule
            # the timer on the event loop thread safely from any worker thread.
            def _schedule_on_loop():
                edit_state["pending_timer"] = event_loop.call_later(delay, _delayed)
            event_loop.call_soon_threadsafe(_schedule_on_loop)

    def on_telemetry_event(event):
        # Force immediate edit on errors / completion so users see final stage
        force = (
            getattr(event, "status", "") == "error"
            or getattr(event, "action", "") in ("COMPLETE", "ERROR", "DONE")
        )
        _schedule_progress_edit(force=force)

    tracker.add_callback(on_telemetry_event)

    def _stop_heartbeat():
        pass  # No heartbeat to stop — uses callback-based updates

    def cb(status: str):
        # Legacy hook — agent no longer relies on this for pipeline stages.
        # Keep as optional free-form note without inventing stages.
        if status:
            logger.debug("[telemetry] progress_callback: %s", str(status)[:120])

    try:
        routed_route = str(
            getattr(routed_intent, "route", None)
            or getattr(routed_intent, "action", None)
            or "chat"
        ).lower()
        # Serialize mutating work, but let ordinary conversations run
        # concurrently. The previous global queue made every chat message
        # wait behind long server-management tasks.
        use_task_manager = bool(
            ctx.task_manager
            and routed_route in {"plan", "tool", "moderation"}
        )
        if use_task_manager:
            t_agent_start = time.time()
            async def do_agent_response():
                try:
                    loop = asyncio.get_running_loop()
                    reply = await _generate_agentic_reply(
                        message=message,
                        text=text,
                        user=user,
                        is_directed=is_directed,
                        is_dm=is_dm,
                        mentioned=mentioned,
                        server_name=server_name,
                        routed_intent=routed_intent,
                        tracker=tracker,
                        progress_callback=cb,
                        event_loop=loop,
                    )
                    _stop_heartbeat()

                    if reply:
                        await _send_agent_reply(message, progress_msg, reply, text, user, str(message.author.id), server_id, tracker, t_agent_start, edit_state, format_reply, short_reply)
                    else:
                        await _send_empty_reply(progress_msg, tracker, text, edit_state)
                except Exception as e:
                    logger.error("[agent] error: %s", str(e)[:200])
                    _stop_heartbeat()
                    if not tracker.is_finished:
                        tracker.complete(False, "Handler error")
                    await _send_error_fallback(progress_msg, edit_state)

            task_label = text[:40].strip() + ('...' if len(text) > 40 else '')
            await ctx.task_manager.start_task(
                name=task_label,
                coro=do_agent_response,
                ctx=message.channel,
                queue_if_busy=True,
            )
        else:
            # No task manager — inline
            try:
                loop = asyncio.get_running_loop()
                t_agent_start = time.time()
                reply = await _generate_agentic_reply(
                    message=message,
                    text=text,
                    user=user,
                    is_directed=is_directed,
                    is_dm=is_dm,
                    mentioned=mentioned,
                    server_name=server_name,
                    routed_intent=routed_intent,
                    tracker=tracker,
                    progress_callback=cb,
                    event_loop=loop,
                )
                _schedule_progress_edit(force=True)
                _stop_heartbeat()
                await asyncio.sleep(0.15)

                if reply:
                    await _send_agent_reply(message, progress_msg, reply, text, user, str(message.author.id), server_id, tracker, t_agent_start, edit_state, format_reply, short_reply)
                else:
                    await _send_empty_reply(progress_msg, tracker, text, edit_state)
            except Exception as e:
                logger.error("[agent] error: %s", str(e)[:200])
                _stop_heartbeat()
                if not tracker.is_finished:
                    tracker.complete(False, "Handler error")
                await _send_error_fallback(progress_msg, edit_state)
    except Exception as e:
        logger.error("[agent] fatal error: %s", str(e)[:200])
        _stop_heartbeat()
        edit_state["closed"] = True
        if not tracker.is_finished:
            tracker.complete(False, "Fatal error")
        try:
            err_msg = await _llm_response("Fatal error", "⚠️ Something went wrong while processing your request.")
            await progress_msg.edit(content=err_msg)
        except discord.NotFound:
            if safe_channel:
                with contextlib.suppress(Exception):
                    await safe_channel.send("⚠️ Something went wrong while processing your request.")
        except Exception:
            try:
                await progress_msg.edit(content="⚠️ Something went wrong while processing your request.")
            except Exception as e:
                logger.warning("[agent] Failed to send fallback fatal message: %s", str(e)[:100])

def _strip_discord_mentions(text: str, bot_user) -> str:
    """Strip Discord mention tokens and bot name from text."""
    text = re.sub(r'<@!?\d+>', '', text).strip()
    text = re.sub(r'<@&\d+>', '', text).strip()
    text = re.sub(r'<#\d+>', '', text).strip()
    if bot_user and bot_user.display_name:
        # Only strip @mention form, not bare name (avoids stripping "Azure" from "Azure DevOps")
        text = text.replace(f"@{bot_user.display_name}", "").strip()
    return text


async def _generate_agentic_reply(
    *,
    message,
    text: str,
    user: str,
    is_directed: bool,
    is_dm: bool,
    mentioned: bool,
    server_name: str,
    routed_intent,
    tracker,
    progress_callback,
    event_loop,
) -> str | None:
    """Produce a single final reply using ToolEngine + agent (LLM-first).

    Prefix/slash commands are handled outside this path.
    """
    from bot.context import ctx

    route = "chat"
    if routed_intent is not None:
        route = str(getattr(routed_intent, "route", None) or routed_intent.action or "chat").lower()

    # Optional ToolEngine high-level decision for agentic routes
    tool_decision = None
    if ctx.tool_engine is not None and route in {"plan", "tool", "health_check", "moderation", "info", "memory"}:
        try:
            tool_decision = await asyncio.to_thread(
                ctx.tool_engine.decide,
                text,
                user,
                server_name,
                is_dm,
                mentioned,
                route,
            )
            if tool_decision and tool_decision.action:
                route = tool_decision.action
                logger.info(
                    "[tool_engine] action=%s conf=%.2f",
                    tool_decision.action,
                    tool_decision.confidence,
                )
        except Exception as e:
            logger.warning("[tool_engine] decide failed: %s", e)
            tool_decision = None

    # Never let an uncertain LLM classification reach a mutating Discord path.
    # Read-only answers can tolerate lower confidence; server changes require
    # either a clear request or a follow-up clarification.
    if (
        tool_decision is not None
        and tool_decision.action in {"plan", "member_action", "template", "undo"}
        and tool_decision.confidence < 0.75
    ):
        return (
            "I'm not confident I understood the requested server change. "
            "Please specify exactly what should change and who or what it applies to."
        )

    # Direct health path
    if route == "health_check" and ctx.mgmt_tools is not None and message.guild:
        try:
            health = getattr(ctx.mgmt_tools, "health", None)
            if health is not None and hasattr(health, "analyze"):
                report = await health.analyze(message.guild)
                return _format_health_analysis(report, server_name)
            if hasattr(ctx.mgmt_tools, "health_check"):
                report = await ctx.mgmt_tools.health_check(message.guild)
                if isinstance(report, str) and report.strip():
                    return report
            if hasattr(ctx.mgmt_tools, "get_server_state"):
                state = await ctx.mgmt_tools.get_server_state(message.guild)
                return f"**Server health snapshot for {server_name}**\n```json\n{str(state)[:1600]}\n```"
        except Exception as e:
            logger.warning("[agentic] health_check failed: %s", e)

    # Read-only server data requests must use the live Discord snapshot rather
    # than asking the language model to infer channels, roles, or members.
    if (
        tool_decision is not None
        and tool_decision.action == "server_info"
        and ctx.mgmt_tools is not None
        and message.guild
        and hasattr(ctx.mgmt_tools, "get_server_state")
    ):
        try:
            state = await ctx.mgmt_tools.get_server_state(message.guild)
            scope = str(tool_decision.params.get("scope") or "overview")
            if isinstance(state, dict):
                return _format_server_info(state, message.guild, scope)
        except Exception as e:
            logger.warning("[agentic] server_info failed: %s", e)

    if (
        tool_decision is not None
        and tool_decision.action == "member_info"
        and message.guild
    ):
        identifier = str((tool_decision.params or {}).get("member") or "").strip()
        if not identifier:
            return "Please specify a member name, mention, or user ID."
        member = None
        mention_match = re.fullmatch(r"<@!?([0-9]+)>", identifier)
        lookup = mention_match.group(1) if mention_match else identifier
        try:
            if lookup.isdigit():
                member = message.guild.get_member(int(lookup))
                if member is None and hasattr(message.guild, "fetch_member"):
                    member = await message.guild.fetch_member(int(lookup))
        except Exception as e:
            logger.info("[agentic] member fetch unavailable: %s", e)
        if member is None:
            needle = lookup.casefold()
            member = next(
                (
                    item for item in getattr(message.guild, "members", [])
                    if needle in {
                        str(getattr(item, "name", "")).casefold(),
                        str(getattr(item, "display_name", "")).casefold(),
                    }
                ),
                None,
            )
        if member is None:
            return f"I couldn't find a visible member matching **{identifier}**."
        return _format_member_info(member, message.guild)

    if (
        tool_decision is not None
        and tool_decision.action == "channel_info"
        and message.guild
    ):
        identifier = str((tool_decision.params or {}).get("channel") or "").strip()
        if not identifier:
            return "Please specify a channel name, mention, or channel ID."
        mention_match = re.fullmatch(r"<#([0-9]+)>", identifier)
        lookup = mention_match.group(1) if mention_match else identifier
        channel = None
        if lookup.isdigit():
            channel = message.guild.get_channel(int(lookup))
        if channel is None:
            needle = lookup.lstrip("#").casefold()
            channel = next(
                (
                    item for item in getattr(message.guild, "channels", [])
                    if str(getattr(item, "name", "")).casefold() == needle
                ),
                None,
            )
        if channel is None:
            return f"I couldn't find a visible channel matching **{identifier}**."
        return _format_channel_info(channel)

    if tool_decision is not None and tool_decision.action == "role_info" and message.guild:
        identifier = str((tool_decision.params or {}).get("role") or "").strip()
        if not identifier:
            return "Please specify a role name, mention, or role ID."
        mention_match = re.fullmatch(r"<@&([0-9]+)>", identifier)
        lookup = mention_match.group(1) if mention_match else identifier
        role = message.guild.get_role(int(lookup)) if lookup.isdigit() else None
        if role is None:
            needle = lookup.lstrip("@").casefold()
            role = next(
                (
                    item for item in getattr(message.guild, "roles", [])
                    if str(getattr(item, "name", "")).casefold() == needle
                ),
                None,
            )
        if role is None:
            return f"I couldn't find a visible role matching **{identifier}**."
        return _format_role_info(role, message.guild)

    if (
        tool_decision is not None
        and tool_decision.action == "server_data"
        and ctx.mgmt_tools is not None
        and message.guild
    ):
        params = tool_decision.params or {}
        data_type = str(params.get("data_type") or "").strip().lower().replace("-", "_")
        methods = {
            "automod_rules": "get_automod_rules",
            "ban_list": "get_ban_list",
            "onboarding": "get_onboarding",
        }
        method_name = methods.get(data_type)
        if method_name is None or not hasattr(ctx.mgmt_tools, method_name):
            return "Supported server data requests are AutoMod rules, the ban list, and onboarding."
        permissions = getattr(getattr(message, "author", None), "guild_permissions", None)
        is_admin = bool(getattr(permissions, "administrator", False))
        if data_type == "ban_list":
            allowed = is_admin or bool(getattr(permissions, "ban_members", False))
        else:
            allowed = is_admin or bool(getattr(permissions, "manage_guild", False))
        if not allowed:
            return "You need the appropriate server-management permission to request that information."
        try:
            limit = max(1, min(int(params.get("limit", 20)), 20))
            fn = getattr(ctx.mgmt_tools, method_name)
            result = await fn(message.guild, limit=limit) if data_type == "ban_list" else await fn(message.guild)
            return _format_server_data(result, data_type, message.guild, limit)
        except (TypeError, ValueError):
            return "Please provide a valid limit between 1 and 20."
        except Exception as e:
            logger.warning("[agentic] server_data failed: %s", e)
            return "I couldn't read that server data right now."

    # Audit logs contain privileged server information and must never be
    # exposed to ordinary members through a model-generated route.
    if (
        tool_decision is not None
        and tool_decision.action == "audit_logs"
        and ctx.mgmt_tools is not None
        and message.guild
        and hasattr(ctx.mgmt_tools, "get_audit_logs")
    ):
        permissions = getattr(getattr(message, "author", None), "guild_permissions", None)
        can_view = bool(
            getattr(permissions, "administrator", False)
            or getattr(permissions, "view_audit_log", False)
        )
        if not can_view:
            return "You need the Discord **View Audit Log** permission to request that information."
        try:
            params = tool_decision.params or {}
            limit = max(1, min(int(params.get("limit", 10)), 20))
            result = await ctx.mgmt_tools.get_audit_logs(
                message.guild,
                limit=limit,
                action_type=params.get("action_type"),
            )
            return _format_audit_logs(
                result, message.guild, limit, str(params.get("target_name") or "")
            )
        except (TypeError, ValueError):
            return "Please provide a valid audit-log limit between 1 and 20."
        except Exception as e:
            logger.warning("[agentic] audit_logs failed: %s", e)
            return "I couldn't read the server audit log right now."

    # Conversational template management shares the same authorization and
    # confirmation boundary as explicit template commands.
    if (
        tool_decision is not None
        and tool_decision.action == "template"
        and ctx.mgmt_tools is not None
        and message.guild
        and getattr(ctx.mgmt_tools, "templates", None) is not None
    ):
        params = tool_decision.params or {}
        template_action = str(params.get("template_action") or "list").lower()
        template_name = str(params.get("template_name") or "").strip()
        templates = ctx.mgmt_tools.templates
        if template_action in {"list", "auto"}:
            available = templates.list_templates() or []
            if not available:
                return "No server templates are available yet."
            return "**Server templates**\n" + "\n".join(
                f"- **{item.get('name', 'unnamed')}**: {item.get('description', '')}"
                for item in available[:20]
            )
        if not _can_manage_server(message):
            return "Only the server owner or an administrator can manage templates."
        if not template_name:
            return "Please specify a template name."
        if template_action == "save":
            try:
                await templates.save_template(
                    message.guild, template_name,
                    f"Saved by {getattr(message.author, 'display_name', user)}",
                )
                return f"Saved server template **{template_name}**."
            except Exception as e:
                logger.warning("[agentic] template save failed: %s", e)
                return "I couldn't save that server template."
        if template_action == "load":
            template = templates.load_template(template_name)
            if not template:
                return f"I couldn't find a server template named **{template_name}**."
            plan = templates.to_plan(template_name)
            steps = plan.get("steps", []) if isinstance(plan, dict) else []
            if not steps:
                return f"Template **{template_name}** has no executable steps."
            if not getattr(ctx, "bot", None) or not hasattr(ctx.bot, "wait_for"):
                return "Template application needs an active Discord confirmation flow."
            await message.channel.send(
                f"Template **{template_name}** contains {len(steps)} changes. Reply **yes** within 60 seconds to apply it."
            )
            key_user = str(message.author.id)
            key_channel = str(message.channel.id)
            _set_pending_confirmation(key_user, key_channel)
            try:
                reply = await ctx.bot.wait_for(
                    "message", timeout=60,
                    check=lambda item: item.author == message.author and item.channel == message.channel,
                )
                if str(getattr(reply, "content", "")).strip().lower() not in {"yes", "go", "do it", "ok", "sure"}:
                    return "Template application cancelled."
                results = await ctx.mgmt_tools.execute_plan(
                    message.guild, plan, message.channel,
                    requester_name=user, requester_id=message.author.id,
                )
                success = sum(1 for item in (results or []) if getattr(item, "success", False))
                return f"Applied template **{template_name}**: {success}/{len(steps)} changes completed."
            except TimeoutError:
                return "Template application timed out and was cancelled."
            finally:
                _clear_pending_confirmation(key_user, key_channel)
        return "Supported template actions are list, save, and load."

    if (
        tool_decision is not None
        and tool_decision.action == "undo"
        and ctx.mgmt_tools is not None
        and message.guild
        and hasattr(ctx.mgmt_tools, "undo_last")
    ):
        if not _can_manage_server(message):
            return "Only the server owner or an administrator can undo server changes."
        try:
            count = max(1, min(int((tool_decision.params or {}).get("count", 1)), 5))
        except (TypeError, ValueError):
            return "Please provide an undo count between 1 and 5."

        class _UndoReply:
            content = ""

            async def send(self, content):
                self.content = str(content)

        sink = _UndoReply()
        results = await ctx.mgmt_tools.undo_last(message.guild, sink, n=count)
        if sink.content:
            return sink.content
        if not results:
            return "Nothing is available to undo."
        success = sum(1 for item in results if getattr(item, "success", False))
        return f"Undid {success}/{len(results)} recent server changes."

    # Member action via mgmt tools when ToolEngine provided a tool_call
    if (
        tool_decision is not None
        and tool_decision.action == "member_action"
        and tool_decision.tool_call
        and ctx.mgmt_tools is not None
        and message.guild
    ):
        try:
            call = tool_decision.tool_call
            tool_name = call.get("tool")
            if tool_name and hasattr(ctx.mgmt_tools, tool_name):
                fn = getattr(ctx.mgmt_tools, tool_name)
                # Prefer execute_plan style if available
                if hasattr(ctx.mgmt_tools, "execute_plan"):
                    action_names = {
                        "kick_member": "kick",
                        "ban_member": "ban",
                        "unban_member": "unban",
                        "timeout_member": "timeout",
                        "mute_member": "mute",
                        "deafen_member": "deafen",
                        "assign_role": "assign_role",
                        "remove_role": "remove_role",
                        "set_nickname": "set_nickname",
                        "move_member_to_voice": "move_voice",
                    }
                    action = action_names.get(tool_name)
                    if action is None:
                        logger.warning("[agentic] unsupported member tool: %s", tool_name)
                        return "I can't safely perform that member action."

                    params = {k: v for k, v in call.items() if k != "tool"}
                    if action == "unban":
                        params.pop("member", None)
                    plan = {
                        "analysis": f"Member action: {tool_name}",
                        "steps": [{"action": action, "params": params}],
                    }
                    result = await ctx.mgmt_tools.execute_plan(
                        message.guild,
                        plan,
                        message.channel,
                        requester_name=user,
                        requester_id=message.author.id,
                    )
                    if isinstance(result, list):
                        successful = sum(1 for item in result if getattr(item, "success", False))
                        if successful:
                            return f"Completed {tool_name.replace('_', ' ')} for {call.get('member', 'the member')}."
                        detail = next((getattr(item, "error", "") for item in result if getattr(item, "error", "")), "Action was not completed.")
                        return f"I couldn't complete that action: {detail}"
                    if isinstance(result, str) and result.strip():
                        return result
                else:
                    result = await fn(message.guild, **{k: v for k, v in call.items() if k != "tool"})
                    if result:
                        return str(result)
        except Exception as e:
            logger.warning("[agentic] member_action failed: %s", e)

    # Execute an LLM-produced server plan directly when it already contains
    # concrete steps. Previously this fell through to Agent.handle(), which
    # invoked a second planner and often replied without changing Discord.
    if (
        tool_decision is not None
        and tool_decision.action == "plan"
        and tool_decision.plan
        and tool_decision.plan.get("steps")
        and ctx.mgmt_tools is not None
        and message.guild
    ):
        if not _can_manage_server(message):
            return "Only the server owner or an administrator can apply server changes."
        try:
            results = await ctx.mgmt_tools.execute_plan(
                message.guild,
                tool_decision.plan,
                message.channel,
                requester_name=user,
                requester_id=message.author.id,
                require_authorization=True,
                confirm_destructive=True,
            )
            results = results or []
            succeeded = sum(1 for item in results if getattr(item, "success", False))
            failed = len(results) - succeeded
            if tracker:
                tracker.complete(failed == 0, f"Plan finished: {succeeded} ok, {failed} failed")
            if not results:
                return "I understood the request, but the plan contained no executable steps."
            return f"Completed {succeeded}/{len(results)} requested server changes."
        except Exception as e:
            logger.warning("[agentic] direct plan execution failed: %s", e)
            if tracker:
                tracker.complete(False, "Plan execution failed")
            return "I couldn't apply that server plan. Check my permissions and try again."

    # Cognitive pipeline (optional feature)
    if ctx.cognitive_pipeline and ctx.cognitive_mode:
        try:
            _, reply = await _cognitize(message, text, user, is_directed, is_dm, mentioned, server_name)
            if reply:
                return reply
        except Exception as e:
            logger.warning("[agentic] cognitive path failed: %s", e)

    # Core agent path — LLM planner + chat
    if not ctx.agent:
        return "I'm not fully online yet — agent is still starting."

    # If ToolEngine already decided chat and provided a response, use it
    if tool_decision is not None and tool_decision.action == "chat" and tool_decision.chat_response:
        return tool_decision.chat_response

    # Inject plan description into agent when ToolEngine chose plan
    agent_message = text
    if tool_decision is not None and tool_decision.action == "plan" and tool_decision.plan:
        desc = tool_decision.plan.get("analysis") or ""
        if desc:
            agent_message = f"{text}\n\n[Operator intent: execute server plan — {desc}]"

    return await ctx.agent.handle(
        user=user,
        message=agent_message,
        server_name=server_name,
        user_id=str(message.author.id),
        progress_callback=progress_callback,
        tracker=tracker,
        guild=message.guild,
        channel=message.channel,
        event_loop=event_loop,
        discord_tools=ctx.mgmt_tools if ctx.mgmt_tools else None,
        skip_discord_planner=(
            route in {"chat", "info", "memory", "moderation"}
            or (
                tool_decision is not None
                and tool_decision.action != "plan"
            )
        ),
    )


async def _send_agent_reply(message, progress_msg, reply, text, user, user_id, server_id, tracker, t_agent_start, edit_state, format_reply, short_reply):
    """Edit the single progress message into the final reply. Never multi-part."""
    from .reply_utils import deliver_one_message, format_final_reply

    if not isinstance(reply, str) or not reply.strip():
        logger.warning("[agent] Ignoring non-text response of type %s", type(reply).__name__)
        await _send_empty_reply(progress_msg, tracker, text, edit_state)
        return

    formatted = format_final_reply(reply, user, format_reply, short_reply)
    edit_state["closed"] = True
    await _cache_response(text, user_id, server_id, reply)
    await asyncio.to_thread(
        _persist_interaction, message, user, text, reply, tracker,
        cached=False, t0=t_agent_start,
    )

    target_channel = message.channel
    if message.guild:
        try:
            if target_channel and not target_channel.permissions_for(message.guild.me).send_messages:
                target_channel = None
        except Exception:
            target_channel = None
        if not target_channel:
            for ch in message.guild.text_channels:
                if ch.permissions_for(message.guild.me).send_messages:
                    target_channel = ch
                    break
    if not target_channel:
        return

    final_msg = await deliver_one_message(
        channel=target_channel,
        content=formatted,
        existing=progress_msg,
    )
    if final_msg is not None:
        await _register_bot_message(final_msg, user_id, text)


async def _send_empty_reply(progress_msg, tracker, text, edit_state):
    """Handle an empty agent reply without claiming the request succeeded."""
    from .reply_utils import deliver_one_message

    logger.warning("[agent] empty reply for: %s", text[:TRUNC_SMALL])
    edit_state["closed"] = True
    body = "I couldn't produce a response for that request. Please try again shortly."
    try:
        await deliver_one_message(
            channel=progress_msg.channel,
            content=body,
            existing=progress_msg,
        )
    except Exception as e:
        logger.warning("[agent] empty reply deliver failed: %s", e)


async def _handle_health_check(message, progress_msg=None) -> None:
    """Send health statistics as a single message."""
    from ..discord_bot_v1 import get_runtime_stats
    from .llm_handler import _llm_response
    from .reply_utils import deliver_one_message
    stats = get_runtime_stats()
    health_str = (
        f"**Azure Health Report**\n"
        f"Uptime: {stats['uptime_seconds'] // 3600}h {(stats['uptime_seconds'] % 3600) // 60}m\n"
        f"Servers: {stats['guilds']}\n"
        f"Active users (1h): {stats['active_users']}\n"
        f"Messages today: {stats['messages_today']}\n"
        f"Health score: {stats['health_score']}/100\n"
        f"Latency: {stats['latency_ms']}ms"
    )
    reply = await _llm_response(
        f"Show health: uptime={stats['uptime_seconds']}s, servers={stats['guilds']}, health={stats['health_score']}",
        health_str,
    )
    await deliver_one_message(channel=message.channel, content=reply, existing=progress_msg)


async def _send_error_fallback(progress_msg, edit_state):
    """Show error message when agent response fails — one edit only."""
    from .reply_utils import deliver_one_message
    edit_state["closed"] = True
    try:
        from bot.handlers.llm_handler import _llm_response
        err_msg = await _llm_response(
            "An error occurred",
            "⚠️ An error occurred while processing your request. Please try again.",
        )
        await deliver_one_message(
            channel=progress_msg.channel,
            content=err_msg,
            existing=progress_msg,
        )
    except Exception as e:
        logger.warning("[agent] Failed to send fallback error message: %s", e)
        try:
            await deliver_one_message(
                channel=progress_msg.channel,
                content="⚠️ An error occurred while processing your request.",
                existing=progress_msg,
            )
        except Exception as e2:
            logger.warning("[agent] Failed to send fallback error message: %s", e2)
