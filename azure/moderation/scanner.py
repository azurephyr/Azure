"""
Azure Moderation: Channel Scanner

Reads recent messages from Discord channels and builds a searchable
message cache for the moderation engine.

Features:
  - Scan single or multiple channels
  - Message deduplication (avoid double-processing)
  - Rate-limit aware (respect Discord API limits)
  - Clustering: find related messages (e.g. spam campaigns)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger("azure.moderation.scanner")


@dataclass
class CachedMessage:
    """Internal representation of a Discord message for moderation."""
    id: str
    author_id: str
    author_name: str
    content: str
    channel_id: str
    channel_name: str
    timestamp: float  # unix timestamp
    has_attachment: bool = False
    has_embed: bool = False


class ChannelScanner:
    """
    Scans Discord channels and maintains a message cache.

    This class is designed to work with or without discord.py.
    When discord.py is available, it can fetch live messages.
    For testing, it can also ingest mock messages.
    """

    def __init__(self, lookback_minutes: int = 30, max_cache_size: int = 5000):
        self.lookback_minutes = lookback_minutes
        self.max_cache_size = max_cache_size
        self._cache: list[CachedMessage] = []
        self._seen_ids: set[str] = set()
        self._last_scan: float = 0.0

    # ------------------------------------------------------------------
    # Ingestion (from Discord events or testing)
    # ------------------------------------------------------------------

    def ingest(self, message) -> CachedMessage | None:
        """
        Ingest a discord.Message (or mock object) into the cache.
        Returns None if already seen.
        """
        msg_id = str(getattr(message, "id", "unknown"))
        if msg_id in self._seen_ids:
            return None

        cm = CachedMessage(
            id=msg_id,
            author_id=str(getattr(getattr(message, "author", None), "id", "unknown")),
            author_name=str(getattr(getattr(message, "author", None), "display_name", "unknown")),
            content=getattr(message, "content", ""),
            channel_id=str(getattr(getattr(message, "channel", None), "id", "unknown")),
            channel_name=str(getattr(getattr(message, "channel", None), "name", "unknown")),
            timestamp=getattr(message, "created_at", None)
                     and message.created_at.timestamp()
                     or time.time(),
            has_attachment=bool(getattr(message, "attachments", [])),
            has_embed=bool(getattr(message, "embeds", [])),
        )
        self._add(cm)
        return cm

    def ingest_dict(self, d: dict) -> CachedMessage | None:
        """Ingest from a plain dict (for testing / external sources)."""
        msg_id = str(d.get("id", d.get("message_id", "unknown")))
        if msg_id in self._seen_ids:
            return None
        cm = CachedMessage(
            id=msg_id,
            author_id=str(d.get("author_id", "unknown")),
            author_name=str(d.get("author_name", "unknown")),
            content=str(d.get("content", "")),
            channel_id=str(d.get("channel_id", "unknown")),
            channel_name=str(d.get("channel_name", "unknown")),
            timestamp=d.get("timestamp", time.time()),
            has_attachment=d.get("has_attachment", False),
            has_embed=d.get("has_embed", False),
        )
        self._add(cm)
        return cm

    def _add(self, cm: CachedMessage):
        self._cache.append(cm)
        self._seen_ids.add(cm.id)
        # Trim old cache
        cutoff = time.time() - (self.lookback_minutes * 60)
        if len(self._cache) > self.max_cache_size:
            self._cache = [m for m in self._cache if m.timestamp > cutoff]
            self._seen_ids = {m.id for m in self._cache}

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_recent_by_author(self, author_id: str, minutes: int = 10) -> list[CachedMessage]:
        """Get recent messages from a specific author."""
        cutoff = time.time() - (minutes * 60)
        return [
            m for m in self._cache
            if m.author_id == author_id and m.timestamp > cutoff
        ]

    def get_recent_by_channel(self, channel_id: str, minutes: int = 10) -> list[CachedMessage]:
        """Get recent messages from a specific channel."""
        cutoff = time.time() - (minutes * 60)
        return [
            m for m in self._cache
            if m.channel_id == channel_id and m.timestamp > cutoff
        ]

    def get_all_recent(self, minutes: int = 10) -> list[CachedMessage]:
        """Get all recent messages across all channels."""
        cutoff = time.time() - (minutes * 60)
        return [m for m in self._cache if m.timestamp > cutoff]

    def find_similar(self, content: str, threshold: float = 0.85) -> list[CachedMessage]:
        """
        Find messages similar to the given content.
        Simple Jaccard similarity on word sets.
        """
        words = set(content.lower().split())
        if not words:
            return []
        matches = []
        for m in self._cache:
            m_words = set(m.content.lower().split())
            if not m_words:
                continue
            inter = len(words & m_words)
            union = len(words | m_words)
            if union == 0:
                continue
            sim = inter / union
            if sim >= threshold:
                matches.append(m)
        return matches

    def find_spam_clusters(self, min_size: int = 3, similarity: float = 0.80) -> list[list[CachedMessage]]:
        """
        Find clusters of near-identical messages (spam campaigns).
        Returns list of clusters, each cluster is a list of messages.
        """
        unclustered = set(range(len(self._cache)))
        clusters = []
        cutoff = time.time() - (self.lookback_minutes * 60)
        recent = [i for i in unclustered if self._cache[i].timestamp > cutoff]

        for i in recent:
            if i not in unclustered:
                continue
            msg = self._cache[i]
            cluster = [msg]
            unclustered.remove(i)
            for j in list(unclustered):
                other = self._cache[j]
                if other.timestamp <= cutoff:
                    unclustered.discard(j)
                    continue
                # Check similarity
                w1 = set(msg.content.lower().split())
                w2 = set(other.content.lower().split())
                if not w1 or not w2:
                    continue
                sim = len(w1 & w2) / len(w1 | w2)
                if sim >= similarity:
                    cluster.append(other)
                    unclustered.remove(j)
            if len(cluster) >= min_size:
                clusters.append(cluster)

        return clusters

    # ------------------------------------------------------------------
    # Discord.py async scanning (optional)
    # ------------------------------------------------------------------

    async def scan_channel(self, channel, limit: int = 100) -> list[CachedMessage]:
        """
        Fetch recent messages from a discord.TextChannel and ingest them.
        Must be awaited.
        """
        ingested = []
        try:
            async for msg in channel.history(limit=limit):
                cm = self.ingest(msg)
                if cm:
                    ingested.append(cm)
        except Exception as e:
            logger.error(f"[scanner] scan_channel failed: {e}")

        self._last_scan = time.time()
        return ingested

    async def scan_all_channels(self, guild, exclude: list[str] | None = None) -> list[CachedMessage]:
        """Scan all text channels in a guild."""
        exclude = set(exclude or [])
        all_ingested = []
        for channel in guild.text_channels:
            if channel.name in exclude or str(channel.id) in exclude:
                continue
            try:
                ingested = await self.scan_channel(channel, limit=50)
                all_ingested.extend(ingested)
            except Exception as e:
                logger.error(f"[scanner] failed to scan {channel.name}: {e}")

        return all_ingested

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def cache_size(self) -> int:
        return len(self._cache)

    def clear(self):
        self._cache.clear()
        self._seen_ids.clear()
