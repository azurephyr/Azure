"""Single-message Discord reply helpers.

Always prefer editing one status message into the final reply.
Never send multi-part continuations for the same response.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

logger = logging.getLogger("azure.discord.reply")

# Discord hard limit for message content
DISCORD_MSG_LIMIT = 2000
# Leave room for a truncation notice
SAFE_LIMIT = 1900


def clamp_discord(text: str, limit: int = SAFE_LIMIT) -> str:
    """Clamp text to one Discord message; append notice if truncated."""
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    notice = "\n\n_(truncated — reply kept to one message)_"
    budget = max(0, limit - len(notice))
    return text[:budget].rstrip() + notice


async def deliver_one_message(
    *,
    channel: discord.abc.Messageable,
    content: str,
    existing: discord.Message | None = None,
    allowed_mentions: discord.AllowedMentions | None = None,
) -> discord.Message | None:
    """Deliver exactly one message: edit existing if possible, else send once.

    Long content is truncated with a notice — never multi-part sends.
    """
    body = clamp_discord(content)
    mentions = allowed_mentions if allowed_mentions is not None else discord.AllowedMentions.none()

    if existing is not None:
        try:
            return await existing.edit(content=body, allowed_mentions=mentions)
        except discord.NotFound:
            logger.debug("[reply] existing message gone; sending new one")
        except discord.HTTPException as e:
            logger.warning("[reply] edit failed (%s); sending new one", e)

    try:
        return await channel.send(body, allowed_mentions=mentions)
    except Exception as e:
        logger.error("[reply] send failed: %s", e)
        return None


async def deliver_embed_one(
    *,
    channel: discord.abc.Messageable,
    embed: discord.Embed,
    existing: discord.Message | None = None,
    content: str | None = None,
) -> discord.Message | None:
    """Deliver one embed message (edit-in-place when possible)."""
    body = clamp_discord(content) if content else None
    if existing is not None:
        try:
            return await existing.edit(content=body, embed=embed)
        except (discord.NotFound, discord.HTTPException):
            pass
    try:
        return await channel.send(content=body, embed=embed)
    except Exception as e:
        logger.error("[reply] embed send failed: %s", e)
        return None


def format_final_reply(reply: str, user_name: str = "", format_reply=None, short_reply=None) -> str:
    """Apply persona formatting then clamp to one message."""
    text = reply or ""
    if format_reply is not None and "```" not in text:
        try:
            text = format_reply(text)
        except Exception:
            pass
    if short_reply is not None and len(text) >= 300 and user_name:
        try:
            text = short_reply(text, user_name)
        except Exception:
            pass
    return clamp_discord(text)
