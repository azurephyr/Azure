"""LLM response generation handler."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

try:
    from ..pydantic_config import config as _pc
    TRUNC_SMALL = 80
    if _pc is not None:
        DEFAULT_MAX_TOKENS = _pc.default_max_tokens
        DEFAULT_TEMPERATURE = _pc.default_temperature
        CHUNK_SIZE = _pc.chunk_size
    else:
        raise ImportError("pydantic config not available")
except (ImportError, AttributeError):
    pass

logger = logging.getLogger("azure.discord.llm")


def _is_directed_at_bot(text_lower: str) -> bool:
    """Check if a message is directed at the bot (name mention, @mention, etc.)."""
    # Use word-boundary matching to avoid false positives like "robot", "about", "botanical"
    bot_names = ["azure", "afterdawn"]
    return any(re.search(r'\b' + re.escape(name) + r'\b', text_lower) for name in bot_names)


async def _classify_intent_background(
    classifier: Any,
    text: str,
    user: str,
    is_dm: bool,
    mentioned: bool,
    server_name: str,
    channel_name: str,
    user_roles: list[str],
    member_count: int
) -> None:
    """Fire-and-forget intent classification using the LLM (non-blocking).

    Args:
        classifier: Intent classifier instance
        text: Message text to classify
        user: Username of message author
        is_dm: Whether message is from DM
        mentioned: Whether bot was mentioned
        server_name: Name of the server
        channel_name: Name of the channel
        user_roles: List of user's role names
        member_count: Total server member count
    """
    from ..config import TRUNC_SMALL
    try:
        loop = asyncio.get_running_loop()
        intent = await loop.run_in_executor(
            None,
            lambda: classifier.classify_llm(
                text=text, user_name=user, is_dm=is_dm, is_mentioned=mentioned,
                server_name=server_name, channel_name=channel_name,
                user_roles=user_roles, server_member_count=member_count,
            )
        )
        logger.info(
            f"[intent] {intent.action} (conf:{intent.confidence:.2f}) "
            f"from {user}: {text[:TRUNC_SMALL]}"
        )
    except Exception as e:
        logger.error(f"[intent] background error: {e}")


async def _llm_response(
    prompt_context: str,
    fallback: str,
    max_tokens: int | None = None
) -> str:
    """Generate a response via LLM with fallback.

    Args:
        prompt_context: The prompt to send to the LLM
        fallback: Fallback response if LLM fails
        max_tokens: Maximum tokens to generate (optional)

    Returns:
        Generated response text or fallback
    """
    from bot.context import ctx

    from ..config import CHUNK_SIZE, DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE
    if not ctx.agent:
        return fallback
    try:
        llm = getattr(ctx.agent, 'llm', None) or getattr(ctx.agent, 'local_llm', None)
        if not llm:
            return fallback
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: llm.chat(
                [
                    {"role": "system", "content": (
                        "You are Azure, a composed and exceptionally capable technical aide "
                        "for a Discord server. Be precise, calm, concise, and honest."
                    )},
                    {"role": "user", "content": prompt_context},
                ],
                max_tokens=max_tokens or DEFAULT_MAX_TOKENS,
                temperature=DEFAULT_TEMPERATURE,
            )
        )
        if resp and resp.strip():
            return resp.strip()[:CHUNK_SIZE]
    except Exception as e:
        logger.debug("LLM response failed: %s", e)
    return fallback
