"""Response cache get/set/hash functions."""
from __future__ import annotations

import hashlib
import logging
import time

# Import shared state from config.py (single source of truth for dict and lock)
from ..config import (
    _cache_lock,
    _response_cache,
)

# Config values: prefer pydantic if available, fall back to config.py defaults
try:
    from ..pydantic_config import config as _pc
    if _pc is not None:
        RESPONSE_CACHE_SIZE = _pc.response_cache_size
        RESPONSE_CACHE_TTL = _pc.response_cache_ttl
    else:
        raise ImportError("pydantic config not available")
except (ImportError, AttributeError):
    from ..config import (
        RESPONSE_CACHE_SIZE,
        RESPONSE_CACHE_TTL,
    )

logger = logging.getLogger("azure.discord.message")


def _hash_message(text: str, user_id: str, server_id: str = "") -> str:
    """
    Create a cache key from message text, user, and server.
    Uses SHA256 hash for consistent key generation.
    """
    # Normalize text: lowercase, strip whitespace, remove extra spaces
    normalized = " ".join(text.lower().strip().split())
    # Include user and server for context-aware caching
    cache_input = f"{normalized}|{user_id}|{server_id}"
    return hashlib.sha256(cache_input.encode()).hexdigest()[:16]  # First 16 chars


async def _get_cached_response(text: str, user_id: str, server_id: str = "") -> str | None:
    """
    Check if we have a cached response for this message.
    Returns cached response if found and not expired, None otherwise.
    """
    with _cache_lock:
        cache_key = _hash_message(text, user_id, server_id)

        if cache_key in _response_cache:
            cached_data = _response_cache[cache_key]
            cached_response = cached_data["response"]
            cached_time = cached_data["timestamp"]

            # Check if cache is still valid
            age = time.time() - cached_time
            if age < RESPONSE_CACHE_TTL:
                # Move to end (LRU)
                _response_cache.move_to_end(cache_key)
                logger.info(f"[cache] HIT for user {user_id} (age: {age:.1f}s)")
                return cached_response
            else:
                # Expired, remove it
                del _response_cache[cache_key]
                logger.info(f"[cache] EXPIRED for user {user_id} (age: {age:.1f}s)")

        return None


async def _cache_response(text: str, user_id: str, server_id: str, response: str) -> None:
    """
    Store a response in the cache for future instant retrieval.
    """
    with _cache_lock:
        cache_key = _hash_message(text, user_id, server_id)

        # Evict oldest entry if cache is full
        if cache_key not in _response_cache and len(_response_cache) >= RESPONSE_CACHE_SIZE:
            _response_cache.popitem(last=False)

        _response_cache[cache_key] = {
            "response": response,
            "timestamp": time.time(),
            "user_id": user_id,
            "server_id": server_id,
        }

        logger.info(f"[cache] STORED for user {user_id} (cache size: {len(_response_cache)})")
