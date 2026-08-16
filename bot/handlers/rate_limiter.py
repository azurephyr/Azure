"""Rate limiting and cooldown logic."""
from __future__ import annotations

import logging
import time

# Import shared state from config.py (single source of truth for dicts and locks)
from ..config import (
    _command_cooldowns,
    _cooldown_lock,
    _rate_limit_buckets,
    _rate_limit_lock,
)

# Config values: prefer pydantic if available, fall back to config.py defaults
try:
    from ..pydantic_config import config as _pc
    if _pc is not None:
        MAX_RATE_LIMIT_ENTRIES = _pc.rate_limit_cache_size
        RATE_LIMIT_WINDOW = _pc.rate_limit_window
        RATE_LIMIT_MAX_REQUESTS = _pc.rate_limit_messages
        RATE_LIMIT_COOLDOWN = _pc.rate_limit_cooldown
        COMMAND_COOLDOWN = _pc.cooldown_seconds
        MAX_COOLDOWN_ENTRIES = _pc.cooldown_cache_size
    else:
        raise ImportError("pydantic config not available")
except (ImportError, AttributeError):
    from ..config import (
        COMMAND_COOLDOWN,
        MAX_COOLDOWN_ENTRIES,
        MAX_RATE_LIMIT_ENTRIES,
        RATE_LIMIT_COOLDOWN,
        RATE_LIMIT_MAX_REQUESTS,
        RATE_LIMIT_WINDOW,
    )

logger = logging.getLogger("azure.discord.message")


async def _check_rate_limit(user_id: str, guild_id: str | None = None) -> tuple[bool, float]:
    """Check if a user has exceeded rate limits. Returns (is_allowed, cooldown_remaining).

    The bucket key includes both user_id and guild_id so each user gets
    independent budgets per guild — activity in one guild doesn't spill over.

    The bucket stores positive timestamps (request times) AND a single negative
    sentinel value (a cooldown start time). When the user exceeds
    RATE_LIMIT_MAX_REQUESTS inside RATE_LIMIT_WINDOW seconds, the bucket is
    replaced by `[-now]` to mark the start of a RATE_LIMIT_COOLDOWN-second freeze.

    IMPORTANT: The pandas-like `bucket[:] = [ts for ts in bucket if ts > cutoff]`
    filter would normally drop the negative marker. We preserve it explicitly
    so the cooldown survives even as old positive entries are aged out.
    """
    with _rate_limit_lock:
        now = time.time()
        user_key = f"{user_id}:{guild_id or 'dm'}"
        if user_key not in _rate_limit_buckets and len(_rate_limit_buckets) >= MAX_RATE_LIMIT_ENTRIES:
            _rate_limit_buckets.popitem(last=False)
        if user_key not in _rate_limit_buckets:
            _rate_limit_buckets[user_key] = []
        else:
            _rate_limit_buckets.move_to_end(user_key)
        bucket = _rate_limit_buckets[user_key]

        # Pull out cooldown marker (if any) BEFORE filtering positives.
        cooldown_start: float | None = None
        if bucket and bucket[-1] < 0:
            cooldown_start = abs(bucket[-1])

        cutoff = now - RATE_LIMIT_WINDOW
        bucket[:] = [ts for ts in bucket if ts > cutoff]

        if cooldown_start is not None:
            cooldown_remaining = RATE_LIMIT_COOLDOWN - (now - cooldown_start)
            if cooldown_remaining > 0:
                # Still cooling down — restore the marker so future calls see it.
                bucket[:] = [-cooldown_start]
                return False, cooldown_remaining
            # Cooldown expired. Don't restore the marker; user gets a fresh start.

        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            bucket[:] = [-now]
            return False, RATE_LIMIT_COOLDOWN
        bucket.append(now)
        return True, 0.0


async def _check_command_cooldown(user_id: str, bypass_for_owner: bool = False) -> tuple[bool, float]:
    """
    Check if a user can execute a command (per-command cooldown).
    Returns (is_allowed, cooldown_remaining_seconds).

    Args:
        user_id: User ID to check
        bypass_for_owner: If True, skip cooldown for server owners/admins

    Returns:
        Tuple of (can_execute: bool, remaining_cooldown: float)
    """
    if bypass_for_owner:
        return True, 0.0

    with _cooldown_lock:
        now = time.time()
        user_key = str(user_id)

        # Clean up old entries if cache is too large
        if user_key not in _command_cooldowns and len(_command_cooldowns) >= MAX_COOLDOWN_ENTRIES:
            _command_cooldowns.popitem(last=False)

        # Get last command time for this user
        last_command_time = _command_cooldowns.get(user_key, 0.0)
        time_since_last = now - last_command_time

        if time_since_last < COMMAND_COOLDOWN:
            # Still on cooldown
            remaining = COMMAND_COOLDOWN - time_since_last
            return False, remaining

        # Update last command time
        _command_cooldowns[user_key] = now
        _command_cooldowns.move_to_end(user_key)

        return True, 0.0
