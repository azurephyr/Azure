import hashlib
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class TemporalEvent:
    """A lightweight event for temporal analysis."""
    message_id: str
    user_id: str
    guild_id: str
    channel_id: str
    timestamp: datetime
    content_hash: str
    content_simhash: str
    has_link: bool
    has_mention: bool
    severity: float
    category: str


@dataclass
class TemporalSignals:
    """Signals from temporal analysis."""
    burst_score: float
    coordination_score: float
    cross_channel_score: float
    raid_probability: float
    novelty_score: float
    involved_users: list[str]
    involved_channels: list[str]
    matched_messages: int
    explanation: str
    is_raid: bool
    is_spam_wave: bool
    is_coordination: bool

    def to_dict(self) -> dict:
        return {
            "burst_score": self.burst_score,
            "coordination_score": self.coordination_score,
            "cross_channel_score": self.cross_channel_score,
            "raid_probability": self.raid_probability,
            "novelty_score": self.novelty_score,
            "involved_users": self.involved_users,
            "involved_channels": self.involved_channels,
            "matched_messages": self.matched_messages,
            "explanation": self.explanation,
            "is_raid": self.is_raid,
            "is_spam_wave": self.is_spam_wave,
            "is_coordination": self.is_coordination,
        }


class TemporalAnalyzer:
    """Detects temporal patterns: bursts, coordination, raids, spam waves.

    Pure statistics. No ML. O(n) per analysis where n is the event window.
    Designed for real-time CPU-only operation on moderate event volumes.
    """

    def __init__(self, max_events: int = 5000, event_ttl_minutes: int = 30):
        self.events: deque = deque(maxlen=max_events)
        self.event_ttl = timedelta(minutes=event_ttl_minutes)
        self.user_events: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self.channel_events: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self._link_re = re.compile(r"https?://\S+", re.IGNORECASE)

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------
    def ingest_event(self, message_id: str, user_id: str, guild_id: str,
                     channel_id: str, content: str, severity: float,
                     category: str) -> TemporalEvent:
        """Ingest a message as a temporal event."""
        now = datetime.now()
        norm = content.lower().strip()
        clean = re.sub(r"\s+", " ", norm)
        content_hash = hashlib.md5(clean.encode()).hexdigest()

        # Simple simhash for near-duplicate: first 10 words
        words = re.findall(r"\b\w+\b", norm)[:10]
        simhash = " ".join(words)

        event = TemporalEvent(
            message_id=message_id,
            user_id=user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            timestamp=now,
            content_hash=content_hash,
            content_simhash=simhash,
            has_link=bool(self._link_re.search(content)),
            has_mention="<@" in content,
            severity=severity,
            category=category,
        )

        self.events.append(event)
        self.user_events[user_id].append(event)
        self.channel_events[channel_id].append(event)
        return event

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    def analyze_situation(self, guild_id: str, window_seconds: int = 300) -> TemporalSignals:
        """Analyze the current temporal situation for a guild."""
        now = datetime.now()
        window = timedelta(seconds=window_seconds)

        recent = [e for e in self.events
                  if e.guild_id == guild_id and e.timestamp > now - window]

        if not recent:
            return TemporalSignals(
                burst_score=0.0, coordination_score=0.0, cross_channel_score=0.0,
                raid_probability=0.0, novelty_score=0.0,
                involved_users=[], involved_channels=[], matched_messages=0,
                explanation="no recent activity", is_raid=False,
                is_spam_wave=False, is_coordination=False,
            )

        burst_score = self._detect_burst(recent)
        coordination_score, coord_users, coord_msgs = self._detect_coordination(recent)
        cross_channel_score, channels = self._detect_cross_channel_spread(recent)
        raid_probability = self._compute_raid_probability(
            recent, burst_score, coordination_score, cross_channel_score
        )
        novelty_score = self._compute_novelty(recent)

        involved_users = list(set(e.user_id for e in recent))
        involved_channels = list(set(e.channel_id for e in recent))

        is_raid = raid_probability > 0.8
        is_spam_wave = coordination_score > 0.7 and len(recent) >= 5
        is_coordination = coordination_score > 0.6

        explanation = self._generate_explanation(
            recent, burst_score, coordination_score, cross_channel_score,
            raid_probability, involved_users, involved_channels
        )

        return TemporalSignals(
            burst_score=round(burst_score, 3),
            coordination_score=round(coordination_score, 3),
            cross_channel_score=round(cross_channel_score, 3),
            raid_probability=round(raid_probability, 3),
            novelty_score=round(novelty_score, 3),
            involved_users=involved_users,
            involved_channels=involved_channels,
            matched_messages=len(recent),
            explanation=explanation,
            is_raid=is_raid,
            is_spam_wave=is_spam_wave,
            is_coordination=is_coordination,
        )

    def get_user_events(self, user_id: str, window_seconds: int = 300) -> list[TemporalEvent]:
        """Get recent events for a user."""
        now = datetime.now()
        window = timedelta(seconds=window_seconds)
        return [e for e in self.user_events.get(user_id, [])
                if e.timestamp > now - window]

    def get_recent_events(self, guild_id: str, window_seconds: int = 300) -> list[TemporalEvent]:
        """Get all recent events for a guild."""
        now = datetime.now()
        window = timedelta(seconds=window_seconds)
        return [e for e in self.events
                if e.guild_id == guild_id and e.timestamp > now - window]

    # ------------------------------------------------------------------
    # Internal detection algorithms
    # ------------------------------------------------------------------
    def _detect_burst(self, events: list[TemporalEvent]) -> float:
        """Detect message burstiness."""
        if len(events) < 5:
            return 0.0

        time_buckets = defaultdict(int)
        for e in events:
            bucket = e.timestamp.replace(second=0, microsecond=0)
            time_buckets[bucket] += 1

        max_bucket = max(time_buckets.values()) if time_buckets else 0
        if max_bucket >= 10:
            return 0.9
        elif max_bucket >= 5:
            return 0.7
        elif max_bucket >= 3:
            return 0.5
        elif len(time_buckets) > 0 and sum(time_buckets.values()) / len(time_buckets) > 2:
            return 0.3
        return 0.0

    def _detect_coordination(self, events: list[TemporalEvent]) -> tuple[float, set[str], int]:
        """Detect multiple users posting similar content."""
        if len(events) < 3:
            return 0.0, set(), 0

        # Group by exact content hash
        hash_groups: dict[str, list[TemporalEvent]] = defaultdict(list)
        for e in events:
            hash_groups[e.content_hash].append(e)

        # Also group by simhash (near-duplicates)
        simhash_groups: dict[str, list[TemporalEvent]] = defaultdict(list)
        for e in events:
            simhash_groups[e.content_simhash].append(e)

        max_coord = 0
        max_users = set()

        for group in list(hash_groups.values()) + list(simhash_groups.values()):
            if len(group) >= 2:
                users = set(e.user_id for e in group)
                if len(users) > 1:
                    max_coord = max(max_coord, len(group))
                    max_users.update(users)

        if max_coord >= 10:
            return 0.9, max_users, max_coord
        elif max_coord >= 5:
            return 0.7, max_users, max_coord
        elif max_coord >= 3:
            return 0.5, max_users, max_coord
        elif max_coord >= 2 and len(max_users) >= 2:
            return 0.3, max_users, max_coord
        return 0.0, set(), 0

    def _detect_cross_channel_spread(self, events: list[TemporalEvent]) -> tuple[float, list[str]]:
        """Detect same content spread across multiple channels."""
        if len(events) < 3:
            return 0.0, []

        hash_channels: dict[str, set[str]] = defaultdict(set)
        for e in events:
            hash_channels[e.content_hash].add(e.channel_id)

        cross_channel_content = sum(1 for channels in hash_channels.values() if len(channels) > 1)
        channels = list(set(e.channel_id for e in events))

        if cross_channel_content >= 3 and len(channels) >= 2:
            return 0.8, channels
        elif cross_channel_content >= 1 and len(channels) >= 2:
            return 0.5, channels
        elif len(channels) >= 2:
            return 0.2, channels
        return 0.0, channels

    def _compute_raid_probability(self, events: list[TemporalEvent],
                                  burst_score: float, coordination_score: float,
                                  cross_channel_score: float) -> float:
        """Compute raid probability from all temporal signals."""
        if len(events) < 3:
            return 0.0

        score = 0.0
        score += burst_score * 0.25
        score += coordination_score * 0.35
        score += cross_channel_score * 0.25

        unique_users = len(set(e.user_id for e in events))
        if unique_users >= 10:
            score += 0.15
        elif unique_users >= 5:
            score += 0.1
        elif unique_users >= 3:
            score += 0.05

        high_severity = sum(1 for e in events if e.severity > 0.5)
        if high_severity >= 5:
            score += 0.1

        return min(score, 1.0)

    def _compute_novelty(self, events: list[TemporalEvent]) -> float:
        """Detect if this is a new attack pattern (many unique messages, all spam)."""
        if not events or len(events) < 3:
            return 0.0
        unique_hashes = len(set(e.content_hash for e in events))
        total = len(events)
        uniqueness = unique_hashes / total
        if uniqueness > 0.8 and sum(e.severity for e in events) / total > 0.3:
            return 0.5
        return 0.0

    def _generate_explanation(self, events: list[TemporalEvent],
                               burst_score: float, coordination_score: float,
                               cross_channel_score: float, raid_probability: float,
                               users: list[str], channels: list[str]) -> str:
        """Generate human-readable explanation of temporal patterns."""
        parts = []
        if raid_probability > 0.8:
            parts.append("RAID DETECTED")
        elif raid_probability > 0.5:
            parts.append("possible raid")

        if burst_score > 0.5:
            parts.append(f"burst activity ({len(events)} messages)")

        if coordination_score > 0.5:
            parts.append(f"coordinated posting ({len(users)} users, similar content)")

        if cross_channel_score > 0.5:
            parts.append(f"cross-channel spread ({len(channels)} channels)")

        if not parts:
            parts.append("no significant temporal patterns")

        return "; ".join(parts)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def cleanup(self):
        """Remove old events beyond TTL."""
        now = datetime.now()
        cutoff = now - self.event_ttl

        # Clean user_events
        to_remove = []
        for uid, events in self.user_events.items():
            while events and events[0].timestamp < cutoff:
                events.popleft()
            if not events:
                to_remove.append(uid)
        for uid in to_remove:
            del self.user_events[uid]

        # Clean channel_events
        to_remove = []
        for cid, events in self.channel_events.items():
            while events and events[0].timestamp < cutoff:
                events.popleft()
            if not events:
                to_remove.append(cid)
        for cid in to_remove:
            del self.channel_events[cid]
