"""
Azure Channel Lifecycle Manager

Monitors channel activity and manages lifecycle:
- Auto-archive inactive channels (>30 days)
- Suggest merges for overlapping topics
- Create temp channels for trending topics
- Weekly health reports
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass

logger = logging.getLogger("azure.channel_lifecycle")


@dataclass
class ChannelHealth:
    """Health metrics for a single channel."""
    channel_id: str
    channel_name: str
    message_count: int = 0
    unique_users: int = 0
    last_message_time: float = 0.0
    days_inactive: float = 0.0
    category: str = ""
    recommendation: str = ""


class ChannelLifecycleManager:
    """
    Smart channel lifecycle management.

    Usage:
        clm = ChannelLifecycleManager()
        health = await clm.analyze_channel(channel)
        if health.days_inactive > 30:
            await clm.archive_channel(channel)
    """

    ARCHIVE_THRESHOLD_DAYS = 30
    MERGE_THRESHOLD_DAYS = 14
    TEMP_CHANNEL_LIFETIME_DAYS = 7

    def __init__(self):
        self.channel_stats: dict[str, dict] = {}
        self.archived_channels: list[str] = []
        self.temp_channels: dict[str, float] = {}  # channel_id -> creation_time

    async def analyze_channel(self, channel) -> ChannelHealth:
        """Analyze a single channel's health."""
        now = time.time()
        stats = await self._get_channel_stats(channel)

        days_inactive = (now - stats.get("last_message_time", now)) / 86400

        health = ChannelHealth(
            channel_id=str(channel.id),
            channel_name=channel.name,
            message_count=stats.get("message_count", 0),
            unique_users=stats.get("unique_users", 0),
            last_message_time=stats.get("last_message_time", now),
            days_inactive=days_inactive,
            category=getattr(channel, "category", None) and channel.category.name or "",
        )

        # Generate recommendation
        if health.days_inactive > self.ARCHIVE_THRESHOLD_DAYS:
            health.recommendation = "archive"
        elif health.days_inactive > self.MERGE_THRESHOLD_DAYS and health.message_count < 10:
            health.recommendation = "merge"
        elif health.message_count > 500 and health.days_inactive < 1:
            health.recommendation = "trending"
        else:
            health.recommendation = "healthy"

        return health

    async def analyze_server(self, guild) -> list[ChannelHealth]:
        """Analyze all channels in a server."""
        results = []
        for channel in guild.text_channels:
            try:
                health = await self.analyze_channel(channel)
                results.append(health)
            except Exception:
                logger.exception("[channel_lifecycle] analyze_channel failed for %s", channel.name)
        return results

    async def auto_archive(self, guild, dry_run: bool = True) -> list[str]:
        """Auto-archive inactive channels. Returns list of archived channel names."""
        archived = []
        healths = await self.analyze_server(guild)

        for health in healths:
            if health.recommendation == "archive":
                if not dry_run:
                    try:
                        # Move to "Archive" category or rename
                        archive_cat = await self._get_or_create_archive_category(guild)
                        channel = guild.get_channel(int(health.channel_id))
                        if channel and archive_cat:
                            await channel.edit(category=archive_cat, sync_permissions=True)
                            self.archived_channels.append(health.channel_id)
                            archived.append(health.channel_name)
                    except Exception as e:
                        logger.error(f"[channel_lifecycle] archive error: {e}")

                else:
                    archived.append(f"[DRY RUN] {health.channel_name}")

        return archived

    async def suggest_merges(self, guild) -> list[tuple[str, str, str]]:
        """Suggest channel merges. Returns [(channel1, channel2, reason)]."""
        suggestions = []
        healths = await self.analyze_server(guild)

        # Group by category and find similar names
        by_category = defaultdict(list)
        for h in healths:
            by_category[h.category].append(h)

        for cat, channels in by_category.items():
            for i, c1 in enumerate(channels):
                for c2 in channels[i + 1:]:
                    if self._names_similar(c1.channel_name, c2.channel_name):
                        suggestions.append((
                            c1.channel_name,
                            c2.channel_name,
                            f"Similar names in {cat or 'uncategorized'}"
                        ))

        return suggestions

    async def create_temp_channel(self, guild, name: str, topic: str = ""):
        """Create a temporary channel that auto-deletes after inactivity."""
        try:
            channel = await guild.create_text_channel(name, topic=topic)
            self.temp_channels[str(channel.id)] = time.time()
            return channel
        except Exception as e:
            logger.error(f"[channel_lifecycle] temp channel error: {e}")

            return None

    async def cleanup_temp_channels(self, guild):
        """Remove expired temporary channels."""
        now = time.time()
        expired = [
            cid for cid, created in self.temp_channels.items()
            if (now - created) > self.TEMP_CHANNEL_LIFETIME_DAYS * 86400
        ]
        for cid in expired:
            try:
                channel = guild.get_channel(int(cid))
                if channel:
                    await channel.delete(reason="Auto-cleanup: temp channel expired")
                self.temp_channels.pop(cid, None)
            except Exception:
                logger.exception("[channel_lifecycle] temp channel cleanup failed for %s", cid)

    async def generate_health_report(self, guild) -> str:
        """Generate a weekly health report for admins."""
        healths = await self.analyze_server(guild)

        lines = [f"📊 **Channel Health Report for {guild.name}**"]
        lines.append("")

        healthy = [h for h in healths if h.recommendation == "healthy"]
        archive = [h for h in healths if h.recommendation == "archive"]
        merge = [h for h in healths if h.recommendation == "merge"]
        trending = [h for h in healths if h.recommendation == "trending"]

        lines.append(f"✅ Healthy: {len(healthy)}")
        lines.append(f"📦 Archive candidates: {len(archive)}")
        lines.append(f"🔗 Merge candidates: {len(merge)}")
        lines.append(f"🔥 Trending: {len(trending)}")
        lines.append("")

        if archive:
            lines.append("**Channels to Archive:**")
            for h in archive:
                lines.append(f"- #{h.channel_name} (inactive {h.days_inactive:.0f} days)")
            lines.append("")

        if merge:
            lines.append("**Channels to Merge:**")
            for h in merge:
                lines.append(f"- #{h.channel_name} (low activity, {h.days_inactive:.0f} days inactive)")
            lines.append("")

        if trending:
            lines.append("**🔥 Trending Channels:**")
            for h in trending:
                lines.append(f"- #{h.channel_name} ({h.message_count} msgs today)")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_channel_stats(self, channel) -> dict:
        """Get message stats for a channel."""
        stats = {"message_count": 0, "unique_users": 0, "last_message_time": 0}
        try:
            users = set()
            last_time = 0
            count = 0
            async for msg in channel.history(limit=100):
                count += 1
                users.add(str(msg.author.id))
                last_time = max(last_time, msg.created_at.timestamp())
            stats["message_count"] = count
            stats["unique_users"] = len(users)
            stats["last_message_time"] = last_time or time.time()
        except Exception:
            logger.exception("[channel_lifecycle] channel stats failed for %s", getattr(channel, 'name', 'unknown'))
        return stats

    async def _get_or_create_archive_category(self, guild):
        """Get or create an Archive category."""
        for cat in guild.categories:
            if "archive" in cat.name.lower():
                return cat
        try:
            return await guild.create_category("📦 Archive")
        except Exception:
            return None

    def _names_similar(self, n1: str, n2: str) -> bool:
        """Check if two channel names are similar."""
        if n1 == n2:
            return False
        s1, s2 = set(n1.lower().split("-")), set(n2.lower().split("-"))
        overlap = s1 & s2
        return len(overlap) >= 1 and len(overlap) / max(len(s1), len(s2)) > 0.5
