"""
Response Cache System for Azure

LRU cache for common queries to provide instant responses without LLM calls.
Dramatically improves latency for repeated queries (greetings, help, etc).

Features:
- LRU eviction policy (least recently used)
- Configurable max size and TTL
- Context-aware hashing (user + message + modes)
- Cache hit/miss metrics
- Thread-safe operations
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class CacheEntry:
    """Single cache entry with metadata."""
    response: str
    timestamp: float
    user_id: str = ""
    hit_count: int = 0
    modes: list[str] = field(default_factory=list)
    complexity: str = "LOW"
    confidence: float = 1.0


class ResponseCache:
    """
    LRU cache for bot responses.

    Usage:
        cache = ResponseCache(max_size=100, ttl_seconds=3600)

        # Try to get cached response
        cached = cache.get("hello", user_id="12345", modes=["CHAT"])
        if cached:
            return cached

        # Generate new response...
        response = llm.generate(...)

        # Store in cache
        cache.set("hello", response, user_id="12345", modes=["CHAT"])
    """

    def __init__(self, max_size: int = 100, ttl_seconds: int = 3600):
        """
        Args:
            max_size: Maximum number of entries (LRU eviction when exceeded)
            ttl_seconds: Time-to-live for entries (0 = no expiration)
        """
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = Lock()

        # Metrics
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _make_key(self, message: str, user_id: str = "",
                  context: dict[str, Any] | None = None) -> str:
        """
        Create cache key from message + context.

        Context-aware: same message from different users or in different
        contexts gets different cache entries (prevents privacy leaks).
        """
        # Normalize message
        msg_normalized = message.strip().lower()

        # Build key components
        key_parts = [msg_normalized]

        # Add user context (optional - disable for pure message caching)
        if user_id:
            key_parts.append(f"user:{user_id}")

        # Add context hints
        if context:
            # Include modes for mode-specific caching
            if "modes" in context:
                modes_str = ",".join(sorted(context["modes"]))
                key_parts.append(f"modes:{modes_str}")

            # Include is_dm flag
            if "is_dm" in context:
                key_parts.append(f"dm:{context['is_dm']}")

        # Hash to fixed-length key
        key_string = "|".join(key_parts)
        return hashlib.sha256(key_string.encode()).hexdigest()[:16]

    def get(self, message: str, user_id: str = "",
            context: dict[str, Any] | None = None) -> str | None:
        """
        Get cached response if available and not expired.

        Returns:
            Cached response string, or None if not found/expired
        """
        key = self._make_key(message, user_id, context)

        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None

            entry = self._cache[key]

            # Check TTL
            if self.ttl_seconds > 0:
                age = time.time() - entry.timestamp
                if age > self.ttl_seconds:
                    # Expired - remove and count as miss
                    del self._cache[key]
                    self._misses += 1
                    return None

            # Move to end (mark as recently used)
            self._cache.move_to_end(key)

            # Update metrics
            entry.hit_count += 1
            self._hits += 1

            return entry.response

    def set(self, message: str, response: str, user_id: str = "",
            context: dict[str, Any] | None = None,
            modes: list[str] | None = None,
            complexity: str = "LOW",
            confidence: float = 1.0) -> None:
        """
        Store response in cache.

        Only caches high-confidence, low-complexity responses by default.
        """
        # Only cache simple, high-confidence responses
        if complexity not in ("LOW", "MEDIUM"):
            return
        if confidence < 0.85:
            return

        key = self._make_key(message, user_id, context)

        with self._lock:
            # Create entry
            entry = CacheEntry(
                response=response,
                timestamp=time.time(),
                user_id=user_id or "",
                modes=modes or [],
                complexity=complexity,
                confidence=confidence,
            )

            # Add to cache
            self._cache[key] = entry
            self._cache.move_to_end(key)

            # LRU eviction if needed
            while len(self._cache) > self.max_size:
                evicted_key, _ = self._cache.popitem(last=False)
                self._evictions += 1

    def invalidate(self, message: str = "", user_id: str = "",
                   pattern: str = "") -> int:
        """
        Invalidate cache entries.

        Args:
            message: Specific message to invalidate
            user_id: Invalidate all entries for a user
            pattern: Invalidate entries matching pattern (substring of original message, not hash)

        Returns:
            Number of entries removed
        """
        with self._lock:
            if message:
                key = self._make_key(message, user_id)
                if key in self._cache:
                    del self._cache[key]
                    return 1
                return 0

            if user_id:
                to_remove = [k for k, v in self._cache.items() if v.user_id == user_id]
                for key in to_remove:
                    del self._cache[key]
                return len(to_remove)

            # Pattern-based invalidation matches against stored original messages
            if pattern:
                to_remove = []
                for key, entry in self._cache.items():
                    original = entry.response
                    if pattern.lower() in original.lower():
                        to_remove.append(key)

                for key in to_remove:
                    del self._cache[key]
                return len(to_remove)

            return 0

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()

    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = self._hits / total_requests if total_requests > 0 else 0.0

            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "hit_rate": hit_rate,
                "total_requests": total_requests,
            }

    def top_entries(self, n: int = 10) -> list[dict[str, Any]]:
        """Get top N most-hit cache entries."""
        with self._lock:
            sorted_entries = sorted(
                self._cache.items(),
                key=lambda item: item[1].hit_count,
                reverse=True
            )

            return [
                {
                    "key": key[:8] + "...",
                    "response": entry.response[:50] + "..." if len(entry.response) > 50 else entry.response,
                    "hit_count": entry.hit_count,
                    "age_seconds": int(time.time() - entry.timestamp),
                    "modes": entry.modes,
                    "complexity": entry.complexity,
                }
                for key, entry in sorted_entries[:n]
            ]


# Default global cache instance
_global_cache: ResponseCache | None = None
_global_cache_lock = Lock()


def get_cache(max_size: int = 100, ttl_seconds: int = 3600) -> ResponseCache:
    """Get or create the global cache instance (thread-safe)."""
    global _global_cache
    if _global_cache is None:
        with _global_cache_lock:
            if _global_cache is None:
                _global_cache = ResponseCache(max_size=max_size, ttl_seconds=ttl_seconds)
    return _global_cache
