from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class RiskProfile:
    """Complete risk assessment for a moderation decision."""
    user_risk: float
    message_risk: float
    situation_risk: float
    channel_risk: float
    total_risk: float
    confidence: float
    factors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "user_risk": round(self.user_risk, 3),
            "message_risk": round(self.message_risk, 3),
            "situation_risk": round(self.situation_risk, 3),
            "channel_risk": round(self.channel_risk, 3),
            "total_risk": round(self.total_risk, 3),
            "confidence": round(self.confidence, 3),
            "factors": self.factors,
        }


class RiskEngine:
    """Dynamic risk scoring. No ML. Pure statistics and heuristics.

    Computes risk scores for users, messages, situations, and channels.
    Risk decays over time for minor offenses and accelerates for repeats.
    """

    def __init__(self):
        # guild_id -> user_id -> risk_score
        self.user_risk: dict[str, dict[str, float]] = {}
        # guild_id:channel_id -> list of offense datetimes
        self.user_offense_history: dict[str, list[datetime]] = {}
        # guild_id -> channel_id -> risk_score
        self.channel_risk: dict[str, dict[str, float]] = {}
        # guild_id -> channel_id -> spam_count
        self.channel_spam_count: dict[str, dict[str, int]] = {}

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def update_channel_spam(self, guild_id: str, channel_id: str, increment: int = 1):
        """Record spam activity in a channel."""
        if guild_id not in self.channel_spam_count:
            self.channel_spam_count[guild_id] = {}
        self.channel_spam_count[guild_id][channel_id] = \
            self.channel_spam_count[guild_id].get(channel_id, 0) + increment

    def record_user_offense(self, guild_id: str, user_id: str):
        """Record a moderation offense for a user."""
        key = f"{guild_id}:{user_id}"
        if key not in self.user_offense_history:
            self.user_offense_history[key] = []
        self.user_offense_history[key].append(datetime.now())

        # Trim old offenses (>30 days)
        cutoff = datetime.now() - timedelta(days=30)
        self.user_offense_history[key] = [
            d for d in self.user_offense_history[key] if d > cutoff
        ]

    # ------------------------------------------------------------------
    # Core scoring
    # ------------------------------------------------------------------
    def compute_user_risk(self, guild_id: str, user_id: str,
                         account_age_days: float | None = None,
                         behavioral_anomaly: float = 0.0,
                         offense_count_24h: int = 0,
                         offense_count_7d: int = 0) -> float:
        """Compute user risk score (0.0–1.0)."""
        score = 0.0
        factors = []

        # New account risk
        if account_age_days is not None:
            if account_age_days < 1:
                score += 0.25
                factors.append("account < 1 day old")
            elif account_age_days < 7:
                score += 0.15
                factors.append("account < 1 week old")

        # Behavioral anomaly
        if behavioral_anomaly > 0.7:
            score += 0.25
            factors.append("strong behavioral anomaly")
        elif behavioral_anomaly > 0.4:
            score += 0.15
            factors.append("mild behavioral anomaly")

        # Recent offense history
        if offense_count_24h >= 3:
            score += 0.3
            factors.append(f"{offense_count_24h} offenses in 24h")
        elif offense_count_24h >= 1:
            score += 0.15
            factors.append(f"{offense_count_24h} offenses in 24h")
        elif offense_count_7d >= 3:
            score += 0.1
            factors.append(f"{offense_count_7d} offenses in 7d")

        # Historical offense decay
        key = f"{guild_id}:{user_id}"
        history = self.user_offense_history.get(key, [])
        if history:
            recent_7d = [d for d in history if d > datetime.now() - timedelta(days=7)]
            if recent_7d:
                score += min(len(recent_7d) * 0.05, 0.2)
                factors.append(f"{len(recent_7d)} offenses in history (7d)")
            # Decay based on days since last offense
            days_since = (datetime.now() - history[-1]).days
            decay = 0.9 ** max(days_since, 0)
            score *= decay

        score = min(score, 1.0)

        # Store
        if guild_id not in self.user_risk:
            self.user_risk[guild_id] = {}
        self.user_risk[guild_id][user_id] = score

        return score

    def compute_message_risk(self, content_severity: float, content_confidence: float,
                              author_risk: float, channel_risk: float,
                              time_risk: float = 0.0) -> float:
        """Compute message risk score (0.0–1.0)."""
        base_content_risk = content_severity * content_confidence
        weighted_risk = (
            content_severity * content_confidence * 0.4 +
            author_risk * 0.3 +
            channel_risk * 0.2 +
            time_risk * 0.1
        )
        return min(max(base_content_risk, weighted_risk), 1.0)

    def compute_situation_risk(self, burst_score: float, coordination_score: float,
                                cross_channel_score: float, novelty_score: float,
                                unique_user_count: int) -> float:
        """Compute situation risk score (0.0–1.0)."""
        score = 0.0
        score += burst_score * 0.3
        score += coordination_score * 0.35
        score += cross_channel_score * 0.2
        if unique_user_count >= 10:
            score += 0.15
        elif unique_user_count >= 5:
            score += 0.1
        score += novelty_score * 0.1
        return min(score, 1.0)

    def compute_channel_risk(self, guild_id: str, channel_id: str,
                              recent_spam_count: int = 0,
                              recent_toxicity_count: int = 0) -> float:
        """Compute channel risk score (0.0–1.0)."""
        score = 0.0
        if recent_spam_count >= 10:
            score += 0.4
        elif recent_spam_count >= 5:
            score += 0.25
        elif recent_spam_count >= 2:
            score += 0.1

        if recent_toxicity_count >= 5:
            score += 0.3
        elif recent_toxicity_count >= 2:
            score += 0.15

        if guild_id not in self.channel_risk:
            self.channel_risk[guild_id] = {}
        self.channel_risk[guild_id][channel_id] = score
        return score

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------
    def compute_full_risk(self, guild_id: str, user_id: str, channel_id: str,
                          content_severity: float, content_confidence: float,
                          behavioral_signals: dict,
                          temporal_signals: dict,
                          account_age_days: float | None = None) -> RiskProfile:
        """Compute complete risk profile from all signal layers."""
        factors = []

        # User risk
        user_risk = self.compute_user_risk(
            guild_id, user_id,
            account_age_days=account_age_days,
            behavioral_anomaly=behavioral_signals.get("anomaly_score", 0.0),
            offense_count_24h=behavioral_signals.get("offense_count_24h", 0),
            offense_count_7d=behavioral_signals.get("offense_count_7d", 0),
        )

        # Channel risk
        channel_risk = self.compute_channel_risk(
            guild_id, channel_id,
            recent_spam_count=self.channel_spam_count.get(guild_id, {}).get(channel_id, 0),
        )

        # Time risk (late night, weekend)
        now = datetime.now()
        time_risk = 0.0
        if now.hour < 6 or now.hour > 23:
            time_risk = 0.1
        if now.weekday() >= 5:
            time_risk += 0.05

        # Message risk
        message_risk = self.compute_message_risk(
            content_severity, content_confidence,
            user_risk, channel_risk, time_risk
        )

        # Situation risk
        situation_risk = self.compute_situation_risk(
            temporal_signals.get("burst_score", 0.0),
            temporal_signals.get("coordination_score", 0.0),
            temporal_signals.get("cross_channel_score", 0.0),
            temporal_signals.get("novelty_score", 0.0),
            len(temporal_signals.get("involved_users", [])),
        )

        # Total risk = max of message and situation (situation can override)
        total_risk = max(message_risk, situation_risk)

        if total_risk > 0.7:
            factors.append(f"total risk: {total_risk:.0%}")
        if user_risk > 0.5:
            factors.append(f"user risk: {user_risk:.0%}")
        if situation_risk > 0.5:
            factors.append(f"situation risk: {situation_risk:.0%}")

        # Confidence synthesis
        confidence = content_confidence
        if behavioral_signals.get("anomaly_score", 0) > 0.5:
            confidence = max(confidence, 0.7)
        if temporal_signals.get("raid_probability", 0) > 0.5:
            confidence = max(confidence, 0.8)

        return RiskProfile(
            user_risk=round(user_risk, 3),
            message_risk=round(message_risk, 3),
            situation_risk=round(situation_risk, 3),
            channel_risk=round(channel_risk, 3),
            total_risk=round(total_risk, 3),
            confidence=round(confidence, 3),
            factors=factors,
        )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------
    def get_user_risk(self, guild_id: str, user_id: str) -> float:
        return self.user_risk.get(guild_id, {}).get(user_id, 0.0)

    def get_channel_risk(self, guild_id: str, channel_id: str) -> float:
        return self.channel_risk.get(guild_id, {}).get(channel_id, 0.0)

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    def decay_all(self):
        """Apply time-based decay to all risk scores."""
        for _guild_id, users in self.user_risk.items():
            for user_id, risk in list(users.items()):
                new_risk = risk * 0.95
                if new_risk < 0.01:
                    del users[user_id]
                else:
                    users[user_id] = new_risk

    def reset_user(self, guild_id: str, user_id: str):
        """Reset risk for a user."""
        key = f"{guild_id}:{user_id}"
        if guild_id in self.user_risk and user_id in self.user_risk[guild_id]:
            del self.user_risk[guild_id][user_id]
        if key in self.user_offense_history:
            del self.user_offense_history[key]

    def reset_channel(self, guild_id: str, channel_id: str):
        """Reset risk for a channel."""
        if guild_id in self.channel_risk and channel_id in self.channel_risk[guild_id]:
            del self.channel_risk[guild_id][channel_id]
        if guild_id in self.channel_spam_count and channel_id in self.channel_spam_count[guild_id]:
            del self.channel_spam_count[guild_id][channel_id]
