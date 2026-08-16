import hashlib
import re
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class UserBehavioralProfile:
    """Per-user behavioral tracking. Lightweight, no ML."""
    user_id: str
    guild_id: str

    # Sliding window history
    messages: deque = field(default_factory=lambda: deque(maxlen=100))
    timestamps: deque = field(default_factory=lambda: deque(maxlen=100))
    content_hashes: deque = field(default_factory=lambda: deque(maxlen=50))

    # Counters
    total_messages: int = 0
    total_links: int = 0
    total_mentions: int = 0
    total_caps_chars: int = 0
    total_chars: int = 0
    total_emojis: int = 0

    # Word diversity
    unique_words: set = field(default_factory=set)
    total_words: int = 0

    # Offense history
    offense_count_24h: int = 0
    offense_count_7d: int = 0
    last_offense: datetime | None = None

    # Derived properties
    @property
    def caps_ratio(self) -> float:
        return self.total_caps_chars / max(self.total_chars, 1)

    @property
    def emoji_ratio(self) -> float:
        return self.total_emojis / max(self.total_chars, 1)

    @property
    def word_diversity(self) -> float:
        return len(self.unique_words) / max(self.total_words, 1)

    @property
    def link_ratio(self) -> float:
        return self.total_links / max(self.total_messages, 1)

    @property
    def mention_ratio(self) -> float:
        return self.total_mentions / max(self.total_messages, 1)

    @property
    def messages_per_minute(self) -> float:
        if len(self.timestamps) < 2:
            return 0.0
        recent = [t for t in self.timestamps if t > datetime.now() - timedelta(minutes=5)]
        if len(recent) < 2:
            return 0.0
        span = (recent[-1] - recent[0]).total_seconds() / 60.0
        return len(recent) / max(span, 0.5)

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "total_messages": self.total_messages,
            "link_ratio": round(self.link_ratio, 3),
            "mention_ratio": round(self.mention_ratio, 3),
            "caps_ratio": round(self.caps_ratio, 3),
            "emoji_ratio": round(self.emoji_ratio, 3),
            "word_diversity": round(self.word_diversity, 3),
            "messages_per_minute": round(self.messages_per_minute, 2),
            "offense_count_24h": self.offense_count_24h,
            "offense_count_7d": self.offense_count_7d,
        }


class BehavioralAnalyzer:
    """Fast, statistics-based behavioral analysis. No ML. CPU-efficient.

    Tracks per-user messaging patterns and detects anomalies using simple
    thresholds and ratios. Designed for real-time CPU-only operation.
    """

    URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
    MENTION_PATTERN = re.compile(r"<@!?(\d+)>")
    EMOJI_PATTERN = re.compile(
        r"<a?:\w+:\d+>|[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251]+"
    )

    def __init__(self, max_profiles_per_guild: int = 5000):
        self.profiles: dict[str, dict[str, UserBehavioralProfile]] = defaultdict(dict)
        self.max_profiles_per_guild = max_profiles_per_guild
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------
    def ingest_message(self, guild_id: str, user_id: str, content: str, message_id: str = ""):
        """Ingest a message into the behavioral tracker."""
        with self._lock:
            profile = self._get_or_create_profile(guild_id, user_id)
            now = datetime.now()

            # Store lightweight metadata
            profile.messages.append({
                "id": message_id,
                "content": content[:500],  # Truncate for memory
                "timestamp": now,
            })
            profile.timestamps.append(now)

            # Normalize
            norm = content.lower().strip()

            # Update counters
            profile.total_messages += 1
            profile.total_chars += len(content)
            profile.total_caps_chars += sum(1 for c in content if c.isupper())

            # Links
            links = self.URL_PATTERN.findall(content)
            if links:
                profile.total_links += len(links)

            # Mentions
            mentions = self.MENTION_PATTERN.findall(content)
            if mentions:
                profile.total_mentions += len(mentions)

            # Emojis
            emojis = self.EMOJI_PATTERN.findall(content)
            if emojis:
                profile.total_emojis += len(emojis)

            # Words
            words = re.findall(r"\b\w+\b", norm)
            profile.total_words += len(words)
            profile.unique_words.update(words)

            # Content hash for copy-paste detection
            clean = re.sub(r"\s+", " ", norm)
            h = hashlib.md5(clean.encode()).hexdigest()
            profile.content_hashes.append(h)

    def record_offense(self, guild_id: str, user_id: str):
        """Record that a user received a moderation action."""
        with self._lock:
            profile = self._get_or_create_profile(guild_id, user_id)
            now = datetime.now()
            if profile.last_offense:
                hours_since = (now - profile.last_offense).total_seconds() / 3600
                if hours_since >= 24:
                    profile.offense_count_24h = 0
                if hours_since >= 168:
                    profile.offense_count_7d = 0
            profile.last_offense = now
            profile.offense_count_24h += 1
            profile.offense_count_7d += 1

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    def analyze_message(self, guild_id: str, user_id: str, content: str) -> dict:
        """Analyze a message and return behavioral signals."""
        with self._lock:
            profile = self._get_or_create_profile(guild_id, user_id)

        frequency_score = self._compute_frequency_score(profile)
        link_ratio = profile.link_ratio
        current_links = len(self.URL_PATTERN.findall(content))
        link_spike = current_links > 0 and link_ratio > 0.3
        mention_ratio = profile.mention_ratio
        caps_ratio = profile.caps_ratio
        current_caps = sum(1 for c in content if c.isupper()) / max(len(content), 1)
        emoji_ratio = profile.emoji_ratio
        similarity_score = self._compute_similarity_score(profile, content)
        word_diversity = profile.word_diversity
        anomaly_score = self._compute_anomaly_score(
            profile, frequency_score, link_ratio, similarity_score
        )

        return {
            "frequency_score": round(frequency_score, 3),
            "link_ratio": round(link_ratio, 3),
            "link_spike": link_spike,
            "mention_ratio": round(mention_ratio, 3),
            "caps_ratio": round(caps_ratio, 3),
            "current_caps_ratio": round(current_caps, 3),
            "emoji_ratio": round(emoji_ratio, 3),
            "similarity_score": round(similarity_score, 3),
            "word_diversity": round(word_diversity, 3),
            "anomaly_score": round(anomaly_score, 3),
            "offense_count_24h": profile.offense_count_24h,
            "offense_count_7d": profile.offense_count_7d,
            "messages_per_minute": round(profile.messages_per_minute, 2),
            "explanation": self._generate_explanation(
                profile, frequency_score, link_spike, similarity_score, anomaly_score
            ),
        }

    def get_profile(self, guild_id: str, user_id: str) -> UserBehavioralProfile | None:
        with self._lock:
            return self.profiles.get(guild_id, {}).get(user_id)

    def get_anomaly_score(self, guild_id: str, user_id: str) -> float:
        """Quick anomaly score lookup."""
        with self._lock:
            profile = self.get_profile(guild_id, user_id)
        if not profile:
            return 0.0
        return self._compute_anomaly_score(
            profile,
            self._compute_frequency_score(profile),
            profile.link_ratio,
            0.0,
        )

    # ------------------------------------------------------------------
    # Internal computation
    # ------------------------------------------------------------------
    def _get_or_create_profile(self, guild_id: str, user_id: str) -> UserBehavioralProfile:
        if user_id not in self.profiles[guild_id]:
            self.profiles[guild_id][user_id] = UserBehavioralProfile(
                user_id=user_id, guild_id=guild_id
            )
            # Lazy cleanup if guild is getting too full
            if len(self.profiles[guild_id]) > self.max_profiles_per_guild:
                oldest_uid = min(
                    self.profiles[guild_id],
                    key=lambda uid: self.profiles[guild_id][uid].timestamps[-1]
                    if self.profiles[guild_id][uid].timestamps else datetime.min
                )
                del self.profiles[guild_id][oldest_uid]
        return self.profiles[guild_id][user_id]

    def _compute_frequency_score(self, profile: UserBehavioralProfile) -> float:
        """Score message frequency in last 5 minutes."""
        recent = [t for t in profile.timestamps if t > datetime.now() - timedelta(minutes=5)]
        count = len(recent)
        if count < 3:
            return 0.0
        elif count < 6:
            return 0.3
        elif count < 10:
            return 0.6
        return 0.9

    def _compute_similarity_score(self, profile: UserBehavioralProfile, content: str) -> float:
        """Detect copy-paste by comparing to recent messages."""
        if len(profile.content_hashes) < 2:
            return 0.0

        norm = content.lower().strip()
        clean = re.sub(r"\s+", " ", norm)
        h = hashlib.md5(clean.encode()).hexdigest()

        # Exact match in last 10 messages
        recent_hashes = list(profile.content_hashes)[-10:]
        if h in recent_hashes:
            return 1.0

        # Near-duplicate with word overlap (Jaccard)
        recent_words = []
        for msg in list(profile.messages)[-10:]:
            words = set(re.findall(r"\b\w+\b", msg["content"].lower()))
            if words:
                recent_words.append(words)

        current_words = set(re.findall(r"\b\w+\b", norm))
        if not current_words or not recent_words:
            return 0.0

        max_overlap = 0.0
        for words in recent_words:
            overlap = len(current_words & words) / len(current_words | words)
            max_overlap = max(max_overlap, overlap)

        return max_overlap

    def _compute_anomaly_score(self, profile: UserBehavioralProfile,
                               frequency_score: float, link_ratio: float,
                               similarity_score: float) -> float:
        """Compute overall behavioral anomaly score (0.0–1.0)."""
        score = 0.0

        # Frequency
        score += frequency_score * 0.3

        # Link spike
        if link_ratio > 0.5:
            score += 0.25
        elif link_ratio > 0.3:
            score += 0.15

        # Copy-paste
        if similarity_score > 0.8:
            score += 0.25

        # Offense history
        if profile.offense_count_24h >= 3:
            score += 0.2
        elif profile.offense_count_24h >= 1:
            score += 0.1

        # Low word diversity (repetitive)
        if profile.word_diversity < 0.3 and profile.total_messages > 5:
            score += 0.1

        return min(score, 1.0)

    def _generate_explanation(self, profile: UserBehavioralProfile,
                             frequency_score: float, link_spike: bool,
                             similarity_score: float, anomaly_score: float) -> str:
        """Generate human-readable explanation of behavioral signals."""
        parts = []
        if frequency_score > 0.6:
            parts.append(f"high frequency ({profile.messages_per_minute:.1f} msg/min)")
        if link_spike:
            parts.append("link spike detected")
        if similarity_score > 0.8:
            parts.append("repeated/duplicate content")
        if profile.offense_count_24h > 0:
            parts.append(f"{profile.offense_count_24h} offenses in 24h")
        if anomaly_score > 0.7:
            parts.append("strong behavioral anomaly")
        elif anomaly_score > 0.4:
            parts.append("mild behavioral anomaly")

        if not parts:
            return "normal behavior"
        return "; ".join(parts)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def reset_user(self, guild_id: str, user_id: str):
        """Reset a user's behavioral profile."""
        with self._lock:
            if user_id in self.profiles.get(guild_id, {}):
                del self.profiles[guild_id][user_id]

    def cleanup(self, guild_id: str | None = None):
        """Remove stale profiles (no activity in 7 days)."""
        with self._lock:
            now = datetime.now()
            cutoff = now - timedelta(days=7)

            target_guilds = [guild_id] if guild_id else list(self.profiles.keys())
            for gid in target_guilds:
                to_remove = []
                for uid, profile in self.profiles.get(gid, {}).items():
                    if profile.timestamps and profile.timestamps[-1] < cutoff:
                        to_remove.append(uid)
                for uid in to_remove:
                    del self.profiles[gid][uid]
