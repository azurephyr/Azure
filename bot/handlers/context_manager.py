"""Conversation history management."""
from __future__ import annotations

import logging
import time

from ..config import (
    CONTEXT_MEMORY_MAX_USERS,
    CONTEXT_MEMORY_SIZE,
    _conversation_history,
    _history_lock,
)

logger = logging.getLogger("azure.discord.message")


async def _add_to_context(user_id: str, user_message: str, bot_response: str) -> None:
    """
    Add a message exchange to the user's conversation history.
    This enables the bot to remember recent context in conversations.
    """
    with _history_lock:
        user_key = str(user_id)

        # Evict oldest user if we're at capacity
        if user_key not in _conversation_history and len(_conversation_history) >= CONTEXT_MEMORY_MAX_USERS:
            _conversation_history.popitem(last=False)

        # Initialize history for new users
        if user_key not in _conversation_history:
            _conversation_history[user_key] = []
        else:
            _conversation_history.move_to_end(user_key)

        # Add the exchange
        _conversation_history[user_key].append({
            "user": user_message,
            "assistant": bot_response,
            "timestamp": time.time()
        })

        # Keep only last N messages
        if len(_conversation_history[user_key]) > CONTEXT_MEMORY_SIZE:
            _conversation_history[user_key] = _conversation_history[user_key][-CONTEXT_MEMORY_SIZE:]

        logger.info(f"[context] Stored for user {user_id} (history size: {len(_conversation_history[user_key])})")


async def _get_conversation_context(user_id: str) -> list[dict]:
    """
    Retrieve recent conversation history for a user.
    Returns a list of message exchanges in chat format.
    """
    with _history_lock:
        user_key = str(user_id)

        if user_key not in _conversation_history:
            return []

        # Move to end (LRU)
        _conversation_history.move_to_end(user_key)

        # Filter out old messages (>1 hour)
        now = time.time()
        history = _conversation_history[user_key]
        fresh_history = [msg for msg in history if now - msg["timestamp"] < 3600]

        # Update with fresh history
        _conversation_history[user_key] = fresh_history

        return fresh_history
