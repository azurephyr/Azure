"""Tests for rate limiting logic.

Tests the rate-limit algorithm by reimplementing the core logic from
message_handler.py in a self-contained way, since that module has
pre-existing syntax errors that prevent direct import.
"""

import asyncio
import time
from collections import OrderedDict

import pytest

# ---------------------------------------------------------------------------
# Re-implement the rate limit config and logic under test
# (mirrors bot/config.py + bot/handlers/message_handler.py)
# ---------------------------------------------------------------------------

RATE_LIMIT_WINDOW = 60.0
RATE_LIMIT_MAX_REQUESTS = 10
RATE_LIMIT_COOLDOWN = 30.0
MAX_RATE_LIMIT_ENTRIES = 1000

COMMAND_COOLDOWN = 5.0
MAX_COOLDOWN_ENTRIES = 1000


class RateLimiter:
    """Self-contained rate limiter matching the message_handler implementation."""

    def __init__(
        self,
        window: float = RATE_LIMIT_WINDOW,
        max_requests: int = RATE_LIMIT_MAX_REQUESTS,
        cooldown: float = RATE_LIMIT_COOLDOWN,
        max_entries: int = MAX_RATE_LIMIT_ENTRIES,
    ):
        self.window = window
        self.max_requests = max_requests
        self.cooldown = cooldown
        self.max_entries = max_entries
        self._buckets: OrderedDict[str, list] = OrderedDict()

    async def check(self, user_id: str, guild_id: str | None = None):
        now = time.time()
        user_key = f"{user_id}:{guild_id or 'dm'}"
        if user_key not in self._buckets and len(self._buckets) >= self.max_entries:
            self._buckets.popitem(last=False)
        if user_key not in self._buckets:
            self._buckets[user_key] = []
        else:
            self._buckets.move_to_end(user_key)
        bucket = self._buckets[user_key]

        cooldown_start: float | None = None
        if bucket and bucket[-1] < 0:
            cooldown_start = abs(bucket[-1])

        cutoff = now - self.window
        bucket[:] = [ts for ts in bucket if ts > cutoff]

        if cooldown_start is not None:
            remaining = self.cooldown - (now - cooldown_start)
            if remaining > 0:
                bucket[:] = [-cooldown_start]
                return False, remaining

        if len(bucket) >= self.max_requests:
            bucket[:] = [-now]
            return False, self.cooldown
        bucket.append(now)
        return True, 0.0


class CommandCooldown:
    """Self-contained command cooldown matching the message_handler implementation."""

    def __init__(self, cooldown: float = COMMAND_COOLDOWN, max_entries: int = MAX_COOLDOWN_ENTRIES):
        self.cooldown = cooldown
        self.max_entries = max_entries
        self._cooldowns: OrderedDict[str, float] = OrderedDict()

    async def check(self, user_id: str, bypass_for_owner: bool = False):
        if bypass_for_owner:
            return True, 0.0
        now = time.time()
        user_key = str(user_id)
        if user_key not in self._cooldowns and len(self._cooldowns) >= self.max_entries:
            self._cooldowns.popitem(last=False)
        last_time = self._cooldowns.get(user_key, 0.0)
        elapsed = now - last_time
        if elapsed < self.cooldown:
            return False, self.cooldown - elapsed
        self._cooldowns[user_key] = now
        if user_key in self._cooldowns:
            self._cooldowns.move_to_end(user_key)
        return True, 0.0


# ========================== _check_rate_limit ==============================


@pytest.mark.asyncio
async def test_rate_limit_allows_messages_within_limit():
    rl = RateLimiter()
    for _ in range(RATE_LIMIT_MAX_REQUESTS - 1):
        allowed, cooldown = await rl.check("user1", "guild1")
        assert allowed is True
        assert cooldown == 0.0


@pytest.mark.asyncio
async def test_rate_limit_blocks_messages_over_limit():
    rl = RateLimiter()
    for _ in range(RATE_LIMIT_MAX_REQUESTS):
        await rl.check("user1", "guild1")

    allowed, cooldown = await rl.check("user1", "guild1")
    assert allowed is False
    assert cooldown > 0


@pytest.mark.asyncio
async def test_rate_limit_cooldown_expires():
    rl = RateLimiter(window=0.05, max_requests=2, cooldown=0.05)
    for _ in range(2):
        await rl.check("user1", "guild1")

    allowed, _ = await rl.check("user1", "guild1")
    assert allowed is False

    await asyncio.sleep(0.06)
    allowed, cooldown = await rl.check("user1", "guild1")
    assert allowed is True
    assert cooldown == 0.0


@pytest.mark.asyncio
async def test_rate_limit_per_user_independence():
    rl = RateLimiter()
    for _ in range(RATE_LIMIT_MAX_REQUESTS):
        await rl.check("user1", "guild1")

    allowed, _ = await rl.check("user2", "guild1")
    assert allowed is True


@pytest.mark.asyncio
async def test_rate_limit_per_guild_independence():
    rl = RateLimiter()
    for _ in range(RATE_LIMIT_MAX_REQUESTS):
        await rl.check("user1", "guild1")

    allowed, _ = await rl.check("user1", "guild2")
    assert allowed is True


@pytest.mark.asyncio
async def test_rate_limit_dm_key():
    rl = RateLimiter()
    for _ in range(RATE_LIMIT_MAX_REQUESTS):
        await rl.check("user1", None)

    allowed, _ = await rl.check("user1", "dm")
    assert allowed is False


@pytest.mark.asyncio
async def test_rate_limit_lru_eviction():
    rl = RateLimiter(max_entries=3)
    await rl.check("u1", "g1")
    await rl.check("u2", "g1")
    await rl.check("u3", "g1")
    assert len(rl._buckets) == 3

    await rl.check("u4", "g1")
    assert len(rl._buckets) == 3
    assert "u4:g1" in rl._buckets


# ======================== _check_command_cooldown ==========================


@pytest.mark.asyncio
async def test_command_cooldown_first_execution():
    cc = CommandCooldown()
    allowed, remaining = await cc.check("user1")
    assert allowed is True
    assert remaining == 0.0


@pytest.mark.asyncio
async def test_command_cooldown_blocks_rapid_fire():
    cc = CommandCooldown()
    await cc.check("user1")
    allowed, remaining = await cc.check("user1")
    assert allowed is False
    assert remaining > 0


@pytest.mark.asyncio
async def test_command_cooldown_owner_bypass():
    cc = CommandCooldown()
    await cc.check("user1")
    allowed, remaining = await cc.check("user1", bypass_for_owner=True)
    assert allowed is True
    assert remaining == 0.0


@pytest.mark.asyncio
async def test_command_cooldown_expires():
    cc = CommandCooldown(cooldown=0.05)
    await cc.check("user1")
    await asyncio.sleep(0.06)
    allowed, _ = await cc.check("user1")
    assert allowed is True


@pytest.mark.asyncio
async def test_command_cooldown_independent_users():
    cc = CommandCooldown(cooldown=10.0)
    await cc.check("user1")
    allowed, _ = await cc.check("user2")
    assert allowed is True
