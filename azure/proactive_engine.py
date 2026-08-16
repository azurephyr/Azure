"""
Azure Predictive Proactive Intelligence

Monitors server patterns and acts before being asked.
Predicts needs based on observed behavior and suggests actions.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class ProactiveSuggestion:
    """A proactive suggestion from the engine."""
    suggestion_id: str
    description: str
    action_type: str
    confidence: float
    expected_outcome: str
    auto_execute: bool = False


class ProactiveEngine:
    """
    Predictive proactive intelligence engine.

    Usage:
        engine = ProactiveEngine()
        engine.record_event("user_join", user_id="123", guild_id="456")
        suggestions = engine.generate_suggestions(guild_id="456")
    """

    def __init__(self):
        self._events: list[dict] = []
        self._user_activity: dict[str, list[dict]] = defaultdict(list)
        self._guild_patterns: dict[str, dict] = defaultdict(lambda: {"joins": 0, "messages": 0, "last_peak": 0})
        self._suggestion_history: list[dict] = []
        self._MAX_SUGGESTION_HISTORY = 1000
        self._accepted_suggestions = 0
        self._rejected_suggestions = 0

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    def record_event(self, event_type: str, **kwargs):
        """Record a server event for pattern analysis."""
        event = {"type": event_type, "timestamp": time.time(), **kwargs}
        self._events.append(event)
        if len(self._events) > 10000:
            self._events = self._events[-5000:]

        guild_id = kwargs.get("guild_id", "global")
        user_id = kwargs.get("user_id", "")

        if user_id:
            self._user_activity[user_id].append(event)
            if len(self._user_activity[user_id]) > 1000:
                self._user_activity[user_id] = self._user_activity[user_id][-500:]

        if event_type == "user_join":
            self._guild_patterns[guild_id]["joins"] += 1
        elif event_type == "message":
            self._guild_patterns[guild_id]["messages"] += 1

    # ------------------------------------------------------------------
    # Suggestion generation
    # ------------------------------------------------------------------

    def generate_suggestions(self, guild_id: str = "", lookback_hours: int = 24) -> list[ProactiveSuggestion]:
        """Generate proactive suggestions based on observed patterns."""
        suggestions = []
        cutoff = time.time() - (lookback_hours * 3600)

        # Analyze recent events for this guild
        recent = [e for e in self._events if e.get("guild_id", "") == guild_id and e["timestamp"] > cutoff]

        # Pattern 1: Multiple new joins in short time -> suggest welcome/onboarding
        joins = [e for e in recent if e["type"] == "user_join"]
        if len(joins) >= 3:
            time_span = joins[-1]["timestamp"] - joins[0]["timestamp"] if len(joins) > 1 else 0
            if time_span < 3600:  # 3 joins in 1 hour
                suggestions.append(ProactiveSuggestion(
                    suggestion_id=f"{guild_id}_welcome_{int(time.time())}",
                    description=f"{len(joins)} new members joined recently. Consider creating a temporary welcome thread or onboarding channel.",
                    action_type="create_welcome",
                    confidence=min(0.95, 0.5 + len(joins) * 0.1),
                    expected_outcome="New members feel welcomed and engaged",
                ))

        # Pattern 2: High message volume in a channel -> suggest pinning or archiving
        msg_counts = defaultdict(int)
        for e in recent:
            if e["type"] == "message":
                msg_counts[e.get("channel_id", "")] += 1
        for ch_id, count in msg_counts.items():
            if count > 100:
                suggestions.append(ProactiveSuggestion(
                    suggestion_id=f"{guild_id}_trending_{ch_id}_{int(time.time())}",
                    description=f"Channel <#{ch_id}> is trending with {count} messages in {lookback_hours}h. Consider creating a dedicated topic channel.",
                    action_type="suggest_channel",
                    confidence=min(0.9, 0.5 + count * 0.005),
                    expected_outcome="Better organization of active discussions",
                ))

        # Pattern 3: Inactive channels -> suggest cleanup
        # (This would need channel history data)

        # Pattern 4: Repeated questions -> suggest FAQ or pinned message
        question_keywords = ["how do i", "how to", "what is", "where is", "can someone", "help with"]
        question_count = sum(1 for e in recent if e["type"] == "message" and any(q in e.get("content", "").lower() for q in question_keywords))
        if question_count > 10:
            suggestions.append(ProactiveSuggestion(
                suggestion_id=f"{guild_id}_faq_{int(time.time())}",
                description=f"{question_count} questions asked recently. Consider creating a FAQ or pinned help message.",
                action_type="suggest_faq",
                confidence=min(0.85, 0.4 + question_count * 0.02),
                expected_outcome="Reduced repetitive questions",
            ))

        return suggestions

    def get_top_suggestion(self, guild_id: str = "") -> ProactiveSuggestion | None:
        """Get the highest-confidence suggestion."""
        suggestions = self.generate_suggestions(guild_id)
        if not suggestions:
            return None
        suggestions.sort(key=lambda s: s.confidence, reverse=True)
        return suggestions[0]

    # ------------------------------------------------------------------
    # Feedback learning
    # ------------------------------------------------------------------

    def accept_suggestion(self, suggestion_id: str):
        """Mark a suggestion as accepted."""
        self._accepted_suggestions += 1
        self._suggestion_history.append({"id": suggestion_id, "accepted": True, "time": time.time()})
        if len(self._suggestion_history) > self._MAX_SUGGESTION_HISTORY:
            self._suggestion_history = self._suggestion_history[-self._MAX_SUGGESTION_HISTORY:]

    def reject_suggestion(self, suggestion_id: str):
        """Mark a suggestion as rejected."""
        self._rejected_suggestions += 1
        self._suggestion_history.append({"id": suggestion_id, "accepted": False, "time": time.time()})
        if len(self._suggestion_history) > self._MAX_SUGGESTION_HISTORY:
            self._suggestion_history = self._suggestion_history[-self._MAX_SUGGESTION_HISTORY:]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        total = self._accepted_suggestions + self._rejected_suggestions
        return {
            "events_recorded": len(self._events),
            "active_users": len(self._user_activity),
            "suggestions_accepted": self._accepted_suggestions,
            "suggestions_rejected": self._rejected_suggestions,
            "acceptance_rate": self._accepted_suggestions / total if total > 0 else 0.0,
        }
