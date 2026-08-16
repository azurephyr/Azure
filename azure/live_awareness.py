"""
Azure Live Server Awareness Engine

Real-time monitoring and intelligence system that tracks ALL server activity:
- Messages (sent, edited, deleted)
- User actions (joins, leaves, nicknames, roles)
- Voice activity (joins, leaves, mutes, streams)
- Reactions (added, removed)
- Server changes (channels, roles, settings)

This engine provides the foundation for:
- Live moderation
- Behavioral analysis
- Proactive insights
- Anomaly detection
- Conversation tracking

Architecture:
- Event-driven: processes Discord events as they happen
- Non-blocking: uses async processing with queues
- Persistent: logs to SQLite for historical analysis
- Real-time: maintains in-memory state for instant queries
- Scalable: handles 1000+ members with sub-second latency

Usage:
    awareness = ServerAwarenessEngine(memory_backend)
    await awareness.on_message(message)
    insights = awareness.get_server_insights(guild_id)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger("azure.live_awareness")


class EventType(Enum):
    """Types of server events we track."""
    MESSAGE = "message"
    MESSAGE_EDIT = "message_edit"
    MESSAGE_DELETE = "message_delete"
    REACTION_ADD = "reaction_add"
    REACTION_REMOVE = "reaction_remove"
    MEMBER_JOIN = "member_join"
    MEMBER_LEAVE = "member_leave"
    MEMBER_UPDATE = "member_update"  # role/nickname changes
    VOICE_JOIN = "voice_join"
    VOICE_LEAVE = "voice_leave"
    CHANNEL_CREATE = "channel_create"
    CHANNEL_DELETE = "channel_delete"
    ROLE_CREATE = "role_create"
    ROLE_DELETE = "role_delete"


@dataclass
class ServerEvent:
    """A single server event with metadata."""
    event_id: str
    event_type: EventType
    timestamp: float
    guild_id: str
    user_id: str
    channel_id: str = ""
    content: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class UserActivity:
    """Real-time user activity tracking."""
    user_id: str
    user_name: str
    guild_id: str

    # Activity metrics
    message_count: int = 0
    messages_last_hour: int = 0
    messages_last_5min: int = 0
    last_message_time: float = 0.0

    # Behavioral indicators
    avg_message_length: float = 0.0
    link_count: int = 0
    mention_count: int = 0
    caps_messages: int = 0
    emoji_count: int = 0

    # Voice activity
    voice_minutes: float = 0.0
    voice_joins: int = 0

    # Moderation
    warnings: int = 0
    timeouts: int = 0
    deleted_messages: int = 0

    # Engagement
    reactions_given: int = 0
    reactions_received: int = 0
    replies_given: int = 0

    # Risk indicators
    burst_detected: bool = False
    suspicious_patterns: list[str] = field(default_factory=list)
    trust_score: float = 50.0  # 0-100, starts neutral

    # Temporal
    first_seen: float = 0.0
    last_seen: float = 0.0
    active_hours: set[int] = field(default_factory=set)  # 0-23


@dataclass
class ChannelActivity:
    """Real-time channel activity tracking."""
    channel_id: str
    channel_name: str
    guild_id: str

    # Activity metrics
    message_count: int = 0
    messages_last_hour: int = 0
    active_users: set[str] = field(default_factory=set)
    active_users_last_hour: set[str] = field(default_factory=set)

    # Content analysis
    avg_message_length: float = 0.0
    link_density: float = 0.0

    # Conversation state
    current_topic: str = ""
    topic_keywords: list[str] = field(default_factory=list)
    sentiment_score: float = 0.0  # -1 to 1

    # Moderation
    spam_score: float = 0.0
    toxicity_score: float = 0.0
    warnings_issued: int = 0

    # Temporal
    last_activity: float = 0.0
    peak_hours: list[int] = field(default_factory=list)


@dataclass
class ServerInsights:
    """Real-time server health and insights."""
    guild_id: str
    timestamp: float

    # Activity summary
    total_users: int = 0
    active_users_now: int = 0
    active_users_hour: int = 0
    active_users_day: int = 0

    messages_last_hour: int = 0
    messages_last_day: int = 0

    # Health indicators
    health_score: float = 100.0  # 0-100
    engagement_rate: float = 0.0  # % of members active

    # Risk indicators
    spam_incidents: int = 0
    raid_probability: float = 0.0
    suspicious_users: list[str] = field(default_factory=list)

    # Conversation insights
    trending_topics: list[str] = field(default_factory=list)
    most_active_channels: list[str] = field(default_factory=list)

    # Recommendations
    suggestions: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)


class ServerAwarenessEngine:
    """
    Real-time server awareness and intelligence engine.

    Tracks all server activity and provides:
    - Live activity metrics
    - User behavioral profiles
    - Channel health monitoring
    - Anomaly detection
    - Proactive insights

    This is the brain that understands what's happening in your server.
    """

    def __init__(self, memory_backend=None, max_events_memory: int = 10000):
        """
        Initialize the awareness engine.

        Args:
            memory_backend: Optional persistent storage backend
            max_events_memory: Max events to keep in memory (oldest dropped)
        """
        self.memory = memory_backend

        # Real-time state (in-memory)
        self.users: dict[str, dict[str, UserActivity]] = defaultdict(dict)  # guild_id -> user_id -> activity
        self.channels: dict[str, dict[str, ChannelActivity]] = defaultdict(dict)  # guild_id -> channel_id -> activity
        self.events: dict[str, deque[ServerEvent]] = defaultdict(lambda: deque(maxlen=max_events_memory))  # guild_id -> events

        # Active conversations (last 100 messages per channel)
        self.conversation_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=100))

        # Real-time counters (reset hourly)
        self.hourly_stats: dict[str, dict] = defaultdict(lambda: {
            "messages": 0,
            "users": set(),
            "hour_start": time.time()
        })

        logger.info("[awareness] ServerAwarenessEngine initialized")

    # -------------------------------------------------------------------------
    # Event Handlers (called by Discord bot)
    # -------------------------------------------------------------------------

    async def on_message(self, message) -> ServerEvent:
        """Track a new message."""
        event = ServerEvent(
            event_id=f"msg_{message.id}",
            event_type=EventType.MESSAGE,
            timestamp=time.time(),
            guild_id=str(message.guild.id) if message.guild else "DM",
            user_id=str(message.author.id),
            channel_id=str(message.channel.id),
            content=message.content[:500],  # Truncate for storage
            metadata={
                "author_name": message.author.display_name,
                "channel_name": message.channel.name if hasattr(message.channel, 'name') else "DM",
                "has_attachments": len(message.attachments) > 0,
                "mention_count": len(message.mentions),
                "has_links": "http://" in message.content or "https://" in message.content,
            }
        )

        await self._process_event(event)
        return event

    async def on_message_edit(self, before, after):
        """Track message edits."""
        if before.content == after.content:
            return  # No actual edit

        event = ServerEvent(
            event_id=f"edit_{after.id}",
            event_type=EventType.MESSAGE_EDIT,
            timestamp=time.time(),
            guild_id=str(after.guild.id) if after.guild else "DM",
            user_id=str(after.author.id),
            channel_id=str(after.channel.id),
            content=after.content[:500],
            metadata={
                "before": before.content[:200],
                "after": after.content[:200],
            }
        )

        await self._process_event(event)

    async def on_message_delete(self, message):
        """Track message deletions."""
        event = ServerEvent(
            event_id=f"del_{message.id}",
            event_type=EventType.MESSAGE_DELETE,
            timestamp=time.time(),
            guild_id=str(message.guild.id) if message.guild else "DM",
            user_id=str(message.author.id),
            channel_id=str(message.channel.id),
            content=message.content[:200],
            metadata={
                "deleted_by_bot": message.author.bot,
            }
        )

        await self._process_event(event)

        # Update user stats
        if message.guild:
            guild_id = str(message.guild.id)
            user_id = str(message.author.id)
            if user_id in self.users[guild_id]:
                self.users[guild_id][user_id].deleted_messages += 1

    async def on_reaction_add(self, reaction, user):
        """Track reactions added."""
        event = ServerEvent(
            event_id=f"react_{reaction.message.id}_{user.id}",
            event_type=EventType.REACTION_ADD,
            timestamp=time.time(),
            guild_id=str(reaction.message.guild.id) if reaction.message.guild else "DM",
            user_id=str(user.id),
            channel_id=str(reaction.message.channel.id),
            metadata={
                "emoji": str(reaction.emoji),
                "message_author": str(reaction.message.author.id),
            }
        )

        await self._process_event(event)

        # Update engagement metrics
        if reaction.message.guild:
            guild_id = str(reaction.message.guild.id)
            user_id = str(user.id)
            if user_id in self.users[guild_id]:
                self.users[guild_id][user_id].reactions_given += 1

            msg_author_id = str(reaction.message.author.id)
            if msg_author_id in self.users[guild_id]:
                self.users[guild_id][msg_author_id].reactions_received += 1

    async def on_member_join(self, member):
        """Track new members joining."""
        event = ServerEvent(
            event_id=f"join_{member.id}",
            event_type=EventType.MEMBER_JOIN,
            timestamp=time.time(),
            guild_id=str(member.guild.id),
            user_id=str(member.id),
            metadata={
                "user_name": member.display_name,
                # member.created_at is tz-aware (discord.py 2.x); use an aware
                # "now" so the subtraction doesn't raise TypeError.
                "account_age_days": (datetime.now(timezone.utc) - member.created_at).days,
                "is_bot": member.bot,
            }
        )

        await self._process_event(event)

        # Initialize user activity
        guild_id = str(member.guild.id)
        user_id = str(member.id)
        if user_id not in self.users[guild_id]:
            self.users[guild_id][user_id] = UserActivity(
                user_id=user_id,
                user_name=member.display_name,
                guild_id=guild_id,
                first_seen=time.time(),
                last_seen=time.time(),
            )

    async def on_member_leave(self, member):
        """Track members leaving."""
        event = ServerEvent(
            event_id=f"leave_{member.id}",
            event_type=EventType.MEMBER_LEAVE,
            timestamp=time.time(),
            guild_id=str(member.guild.id),
            user_id=str(member.id),
            metadata={
                "user_name": member.display_name,
            }
        )

        await self._process_event(event)

    async def on_voice_state_update(self, member, before, after):
        """Track voice channel activity."""
        if before.channel != after.channel:
            if after.channel:
                # Joined voice
                event = ServerEvent(
                    event_id=f"voice_join_{member.id}_{after.channel.id}",
                    event_type=EventType.VOICE_JOIN,
                    timestamp=time.time(),
                    guild_id=str(member.guild.id),
                    user_id=str(member.id),
                    channel_id=str(after.channel.id),
                    metadata={
                        "channel_name": after.channel.name,
                    }
                )
                await self._process_event(event)

                guild_id = str(member.guild.id)
                user_id = str(member.id)
                if user_id in self.users[guild_id]:
                    self.users[guild_id][user_id].voice_joins += 1

            if before.channel:
                # Left voice
                event = ServerEvent(
                    event_id=f"voice_leave_{member.id}_{before.channel.id}",
                    event_type=EventType.VOICE_LEAVE,
                    timestamp=time.time(),
                    guild_id=str(member.guild.id),
                    user_id=str(member.id),
                    channel_id=str(before.channel.id),
                    metadata={
                        "channel_name": before.channel.name,
                    }
                )
                await self._process_event(event)

    # -------------------------------------------------------------------------
    # Event Processing (internal)
    # -------------------------------------------------------------------------

    async def _process_event(self, event: ServerEvent):
        """Process a server event and update real-time state."""
        guild_id = event.guild_id
        user_id = event.user_id
        channel_id = event.channel_id

        # Store event
        self.events[guild_id].append(event)

        # Update user activity
        if user_id and guild_id != "DM":
            if user_id not in self.users[guild_id]:
                self.users[guild_id][user_id] = UserActivity(
                    user_id=user_id,
                    user_name=event.metadata.get("author_name", "Unknown"),
                    guild_id=guild_id,
                    first_seen=event.timestamp,
                )

            user = self.users[guild_id][user_id]
            user.last_seen = event.timestamp

            # Add active hour
            hour = datetime.fromtimestamp(event.timestamp).hour
            user.active_hours.add(hour)

            # Process by event type
            if event.event_type == EventType.MESSAGE:
                user.message_count += 1
                user.messages_last_hour += 1
                user.messages_last_5min += 1
                user.last_message_time = event.timestamp

                # Update averages
                content_len = len(event.content)
                user.avg_message_length = (user.avg_message_length * (user.message_count - 1) + content_len) / user.message_count

                # Track patterns
                if event.metadata.get("has_links"):
                    user.link_count += 1
                if event.metadata.get("mention_count", 0) > 0:
                    user.mention_count += event.metadata["mention_count"]
                if event.content.isupper() and len(event.content) > 10:
                    user.caps_messages += 1

                # Detect burst (5+ messages in 5 seconds)
                recent_times = [e.timestamp for e in self.events[guild_id]
                               if e.user_id == user_id and e.event_type == EventType.MESSAGE
                               and event.timestamp - e.timestamp < 5.0]
                if len(recent_times) >= 5:
                    user.burst_detected = True
                    if "message_burst" not in user.suspicious_patterns:
                        user.suspicious_patterns.append("message_burst")

        # Update channel activity
        if channel_id and guild_id != "DM":
            if channel_id not in self.channels[guild_id]:
                self.channels[guild_id][channel_id] = ChannelActivity(
                    channel_id=channel_id,
                    channel_name=event.metadata.get("channel_name", "Unknown"),
                    guild_id=guild_id,
                )

            channel = self.channels[guild_id][channel_id]
            channel.last_activity = event.timestamp

            if event.event_type == EventType.MESSAGE:
                channel.message_count += 1
                channel.messages_last_hour += 1
                channel.active_users.add(user_id)
                channel.active_users_last_hour.add(user_id)

                # Store in conversation history
                self.conversation_history[channel_id].append({
                    "user_id": user_id,
                    "content": event.content,
                    "timestamp": event.timestamp,
                })

        # Update hourly stats
        if guild_id != "DM":
            stats = self.hourly_stats[guild_id]

            # Reset if new hour
            current_hour = time.time()
            if current_hour - stats["hour_start"] > 3600:
                stats["messages"] = 0
                stats["users"] = set()
                stats["hour_start"] = current_hour

            if event.event_type == EventType.MESSAGE:
                stats["messages"] += 1
                stats["users"].add(user_id)

        # Persist to database (async)
        if self.memory:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._persist_event,
                    event
                )
            except Exception as e:
                logger.error(f"[awareness] Failed to persist event: {e}")

    def _persist_event(self, event: ServerEvent):
        """Persist event to database (called in executor)."""
        if not self.memory:
            return

        try:
            # Store in memory backend
            self.memory.save_memory(
                text=f"{event.event_type.value}: {event.content}",
                user_id=event.user_id,
                source="live_awareness",
                tags=[event.event_type.value, event.guild_id],
            )
        except Exception as e:
            logger.error(f"[awareness] Persist error: {e}")

    # -------------------------------------------------------------------------
    # Query Interface (used by other systems)
    # -------------------------------------------------------------------------

    def get_user_activity(self, guild_id: str, user_id: str) -> UserActivity | None:
        """Get real-time activity for a user."""
        return self.users.get(guild_id, {}).get(user_id)

    def get_channel_activity(self, guild_id: str, channel_id: str) -> ChannelActivity | None:
        """Get real-time activity for a channel."""
        return self.channels.get(guild_id, {}).get(channel_id)

    def get_recent_events(self, guild_id: str, event_type: EventType | None = None,
                          limit: int = 100) -> list[ServerEvent]:
        """Get recent events for a guild."""
        events = list(self.events.get(guild_id, []))

        if event_type:
            events = [e for e in events if e.event_type == event_type]

        return events[-limit:]

    def get_active_users(self, guild_id: str, window_seconds: int = 3600) -> list[UserActivity]:
        """Get currently active users (messaged in last N seconds)."""
        cutoff = time.time() - window_seconds
        active = []

        for _user_id, activity in self.users.get(guild_id, {}).items():
            if activity.last_seen >= cutoff:
                active.append(activity)

        return sorted(active, key=lambda u: u.last_seen, reverse=True)

    def get_server_insights(self, guild_id: str) -> ServerInsights:
        """Generate real-time server insights."""
        insights = ServerInsights(
            guild_id=guild_id,
            timestamp=time.time(),
        )

        # Count users
        all_users = self.users.get(guild_id, {})
        insights.total_users = len(all_users)

        # Active users (different time windows)
        now = time.time()
        insights.active_users_now = len([u for u in all_users.values() if now - u.last_seen < 300])  # 5 min
        insights.active_users_hour = len([u for u in all_users.values() if now - u.last_seen < 3600])  # 1 hour
        insights.active_users_day = len([u for u in all_users.values() if now - u.last_seen < 86400])  # 24 hours

        # Messages
        stats = self.hourly_stats.get(guild_id, {})
        insights.messages_last_hour = stats.get("messages", 0)

        # Calculate engagement rate
        if insights.total_users > 0:
            insights.engagement_rate = (insights.active_users_day / insights.total_users) * 100

        # Health score (0-100)
        health = 100.0
        if insights.engagement_rate < 10:
            health -= 20  # Low engagement
        if insights.messages_last_hour < 10 and insights.total_users > 50:
            health -= 15  # Dead server

        # Find suspicious users
        suspicious = [u for u in all_users.values() if len(u.suspicious_patterns) > 0 or u.trust_score < 30]
        insights.suspicious_users = [u.user_id for u in suspicious[:5]]

        # Detect raid
        recent_joins = [e for e in self.events.get(guild_id, [])
                       if e.event_type == EventType.MEMBER_JOIN and now - e.timestamp < 300]
        if len(recent_joins) > 10:
            insights.raid_probability = min(1.0, len(recent_joins) / 50.0)
            health -= insights.raid_probability * 30

        insights.health_score = max(0.0, health)

        # Most active channels
        channels = self.channels.get(guild_id, {})
        active_channels = sorted(channels.values(), key=lambda c: c.messages_last_hour, reverse=True)[:5]
        insights.most_active_channels = [c.channel_name for c in active_channels]

        # Generate suggestions
        if insights.engagement_rate < 20:
            insights.suggestions.append("Low engagement - consider running events or activities")
        if insights.messages_last_hour < 5 and insights.total_users > 20:
            insights.suggestions.append("Server is quiet - try posting conversation starters")
        if len(suspicious) > 0:
            insights.suggestions.append(f"Review {len(suspicious)} suspicious user(s)")

        # Generate alerts
        if insights.raid_probability > 0.5:
            insights.alerts.append(f"⚠️ Possible raid detected - {len(recent_joins)} joins in 5 minutes")
        if health < 50:
            insights.alerts.append("⚠️ Server health is degraded")

        return insights

    def get_conversation_context(self, channel_id: str, limit: int = 20) -> list[dict]:
        """Get recent conversation history for a channel."""
        history = list(self.conversation_history.get(channel_id, []))
        return history[-limit:]

    # -------------------------------------------------------------------------
    # Maintenance
    # -------------------------------------------------------------------------

    async def cleanup_old_data(self, max_age_hours: int = 72):
        """Clean up old in-memory data (run periodically)."""
        cutoff = time.time() - (max_age_hours * 3600)

        # Clean up user activity
        for guild_id in list(self.users.keys()):
            for user_id in list(self.users[guild_id].keys()):
                user = self.users[guild_id][user_id]
                if user.last_seen < cutoff:
                    # Archive to database before deleting
                    if self.memory:
                        with contextlib.suppress(Exception):
                            self.memory.save_memory(
                                text=f"User activity archived: {user.user_name}",
                                user_id=user_id,
                                source="awareness_archive",
                                tags=["user_activity", guild_id],
                            )

                    del self.users[guild_id][user_id]

        # Clean up channel activity (keep last 24h only)
        day_cutoff = time.time() - 86400
        for guild_id in list(self.channels.keys()):
            for channel_id in list(self.channels[guild_id].keys()):
                channel = self.channels[guild_id][channel_id]
                if channel.last_activity < day_cutoff:
                    del self.channels[guild_id][channel_id]

        logger.info("[awareness] Cleaned up old data")

    def reset_hourly_counters(self):
        """Reset hourly statistics (called by scheduler)."""
        for guild_id in self.hourly_stats:
            self.hourly_stats[guild_id] = {
                "messages": 0,
                "users": set(),
                "hour_start": time.time()
            }

        # Also reset user burst flags
        for guild_id in self.users:
            for user_id in self.users[guild_id]:
                self.users[guild_id][user_id].burst_detected = False
                self.users[guild_id][user_id].messages_last_hour = 0
                self.users[guild_id][user_id].messages_last_5min = 0

        logger.info("[awareness] Reset hourly counters")

    async def export_analytics(self, guild_id: str, filepath: str):
        """Export analytics to JSON file."""
        data = {
            "guild_id": guild_id,
            "exported_at": time.time(),
            "users": {uid: asdict(u) for uid, u in self.users.get(guild_id, {}).items()},
            "channels": {cid: asdict(c) for cid, c in self.channels.get(guild_id, {}).items()},
            "insights": asdict(self.get_server_insights(guild_id)),
        }

        # Convert sets to lists for JSON serialization
        for user_data in data["users"].values():
            if "active_hours" in user_data:
                user_data["active_hours"] = list(user_data["active_hours"])
            if "suspicious_patterns" in user_data:
                user_data["suspicious_patterns"] = list(user_data["suspicious_patterns"])

        for channel_data in data["channels"].values():
            if "active_users" in channel_data:
                channel_data["active_users"] = list(channel_data["active_users"])
            if "active_users_last_hour" in channel_data:
                channel_data["active_users_last_hour"] = list(channel_data["active_users_last_hour"])

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        logger.info(f"[awareness] Exported analytics to {filepath}")
