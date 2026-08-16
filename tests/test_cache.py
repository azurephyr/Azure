"""Tests for response caching.

Tests the response-cache algorithm from message_handler.py and the
standalone ResponseCache from azure/response_cache.py.
"""

import hashlib
import time
from collections import OrderedDict
from unittest.mock import patch

import pytest

RESPONSE_CACHE_SIZE = 100
RESPONSE_CACHE_TTL = 3600.0


def _hash_message(text: str, user_id: str, server_id: str = "") -> str:
    normalized = " ".join(text.lower().strip().split())
    cache_input = f"{normalized}|{user_id}|{server_id}"
    return hashlib.sha256(cache_input.encode()).hexdigest()[:16]


class AsyncResponseCache:
    def __init__(self, max_size: int = RESPONSE_CACHE_SIZE, ttl: float = RESPONSE_CACHE_TTL):
        self.max_size = max_size
        self.ttl = ttl
        self._cache: OrderedDict[str, dict] = OrderedDict()

    async def get(self, text: str, user_id: str, server_id: str = "") -> str | None:
        key = _hash_message(text, user_id, server_id)
        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry["timestamp"] < self.ttl:
                self._cache.move_to_end(key)
                return entry["response"]
            del self._cache[key]
        return None

    async def set(self, text: str, user_id: str, server_id: str, response: str) -> None:
        key = _hash_message(text, user_id, server_id)
        if key not in self._cache and len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)
        self._cache[key] = {
            "response": response,
            "timestamp": time.time(),
            "user_id": user_id,
            "server_id": server_id,
        }


# ---- _hash_message --------------------------------------------------------


def test_hash_message_deterministic():
    assert _hash_message("hello", "u1", "s1") == _hash_message("hello", "u1", "s1")


def test_hash_message_normalises_whitespace():
    assert _hash_message("  hello   world  ", "u1", "s1") == _hash_message("hello world", "u1", "s1")


def test_hash_message_different_users():
    assert _hash_message("hello", "u1", "s1") != _hash_message("hello", "u2", "s1")


def test_hash_message_different_servers():
    assert _hash_message("hello", "u1", "s1") != _hash_message("hello", "u1", "s2")


# ---- AsyncResponseCache ---------------------------------------------------


@pytest.mark.asyncio
async def test_cache_miss_returns_none():
    assert await AsyncResponseCache().get("hello", "u1", "s1") is None


@pytest.mark.asyncio
async def test_cache_hit_returns_stored_response():
    c = AsyncResponseCache()
    await c.set("hello", "u1", "s1", "Hi there!")
    assert await c.get("hello", "u1", "s1") == "Hi there!"


@pytest.mark.asyncio
async def test_cache_miss_different_server():
    c = AsyncResponseCache()
    await c.set("hello", "u1", "s1", "Hi!")
    assert await c.get("hello", "u1", "s2") is None


@pytest.mark.asyncio
async def test_cache_miss_different_user():
    c = AsyncResponseCache()
    await c.set("hello", "u1", "s1", "Hi!")
    assert await c.get("hello", "u2", "s1") is None


@pytest.mark.asyncio
async def test_cache_expiration():
    c = AsyncResponseCache(ttl=10.0)
    with patch("tests.test_cache.time.time") as mock_time:
        mock_time.return_value = 1000.0
        await c.set("hello", "u1", "s1", "Hi!")
        mock_time.return_value = 1011.0
        assert await c.get("hello", "u1", "s1") is None


@pytest.mark.asyncio
async def test_cache_size_limit():
    c = AsyncResponseCache(max_size=3)
    for i in range(5):
        await c.set(f"msg{i}", "u1", "s1", f"resp{i}")
    assert len(c._cache) == 3


@pytest.mark.asyncio
async def test_cache_lru_eviction():
    c = AsyncResponseCache(max_size=3)
    await c.set("a", "u1", "s1", "ra")
    await c.set("b", "u1", "s1", "rb")
    await c.set("c", "u1", "s1", "rc")
    await c.get("a", "u1", "s1")  # touch 'a'
    await c.set("d", "u1", "s1", "rd")  # evicts 'b'
    assert await c.get("a", "u1", "s1") == "ra"
    assert await c.get("b", "u1", "s1") is None


@pytest.mark.asyncio
async def test_cache_overwrite():
    c = AsyncResponseCache()
    await c.set("hello", "u1", "s1", "v1")
    await c.set("hello", "u1", "s1", "v2")
    assert await c.get("hello", "u1", "s1") == "v2"


# ---- ResponseCache (azure/response_cache.py) ------------------------------

from azure.response_cache import ResponseCache


def test_response_cache_miss():
    assert ResponseCache(max_size=10, ttl_seconds=60).get("hello", user_id="u1") is None


def test_response_cache_hit():
    c = ResponseCache(max_size=10, ttl_seconds=60)
    c.set("hello", "Hi!", user_id="u1")
    assert c.get("hello", user_id="u1") == "Hi!"


def test_response_cache_expiration():
    c = ResponseCache(max_size=10, ttl_seconds=10.0)
    with patch("azure.response_cache.time.time") as mock_time:
        mock_time.return_value = 1000.0
        c.set("hello", "Hi!", user_id="u1")
        mock_time.return_value = 1011.0
        assert c.get("hello", user_id="u1") is None


def test_response_cache_size_limit():
    c = ResponseCache(max_size=3, ttl_seconds=60)
    for i in range(5):
        c.set(f"msg{i}", f"resp{i}", user_id="u1")
    assert c.stats()["size"] == 3


def test_response_cache_high_complexity_not_cached():
    c = ResponseCache(max_size=10, ttl_seconds=60)
    c.set("hello", "Hi!", user_id="u1", complexity="HIGH")
    assert c.get("hello", user_id="u1") is None


def test_response_cache_low_confidence_not_cached():
    c = ResponseCache(max_size=10, ttl_seconds=60)
    c.set("hello", "Hi!", user_id="u1", confidence=0.5)
    assert c.get("hello", user_id="u1") is None


def test_response_cache_stats():
    c = ResponseCache(max_size=10, ttl_seconds=60)
    c.set("a", "ra", user_id="u1")
    c.get("a", user_id="u1")
    c.get("missing", user_id="u1")
    s = c.stats()
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["size"] == 1


def test_response_cache_invalidate():
    c = ResponseCache(max_size=10, ttl_seconds=60)
    c.set("hello", "Hi!", user_id="u1")
    assert c.invalidate("hello", user_id="u1") == 1
    assert c.get("hello", user_id="u1") is None


def test_response_cache_clear():
    c = ResponseCache(max_size=10, ttl_seconds=60)
    c.set("a", "ra")
    c.set("b", "rb")
    c.clear()
    assert c.stats()["size"] == 0
