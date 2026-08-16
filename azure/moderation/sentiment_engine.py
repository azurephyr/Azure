"""
Azure Sentiment-Aware Moderation Engine

Extends the moderation system with:
- Sarcasm and passive-aggression detection
- Conversation tone escalation tracking
- Coordinated manipulation detection
- Sentiment trajectory analysis per user
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class SentimentAnalysis:
    """Result of sentiment analysis on a message."""
    sentiment_score: float  # -1 to 1
    sarcasm_probability: float
    passive_aggression: float
    escalation_delta: float  # change from previous message
    manipulation_score: float
    emotional_keywords: list[str] = field(default_factory=list)


class SentimentEngine:
    """
    Sentiment-aware moderation enhancement.

    Usage:
        engine = SentimentEngine()
        analysis = engine.analyze(message_content, user_history)
        if analysis.sarcasm_probability > 0.7:
            flag_for_review()
    """

    # Pattern sets for detection
    SARCASM_PATTERNS = [
        r"oh,? (really|great|wonderful|perfect|nice|sure)",
        r"(?:yeah|yes|sure),? (?:right|of course|totally|definitely)",
        r"because that'?s (?:obviously|clearly) what (?:i|we|everyone) wanted",
        r"(?:love|adore|enjoy) how",
        r"(?<!\w)lol(?!\w).*?but (?:actually|seriously|no)",
        r"(?:clearly|obviously) (?:you know|i'm sure|everyone knows)",
    ]

    PASSIVE_AGGRESSIVE_PATTERNS = [
        r"(?:just|simply) (?:trying to|attempting to)",
        r"(?:no offense|not to be rude|don't take this the wrong way)",
        r"(?:if you actually|if you really|if you could just)",
        r"(?:per my last email|as previously mentioned|as i already said)",
        r"(?:bless your heart|good for you|how nice for you)",
    ]

    MANIPULATION_PATTERNS = [
        r"(?:everyone knows|we all know|people are saying)",
        r"(?:obviously|clearly|undoubtedly) (?:you|he|she|they) (?:is|are|was|were)",
        r"(?:don't you think|wouldn't you agree|isn't it obvious)",
    ]

    EMOTIONAL_KEYWORDS = {
        "positive": ["great", "awesome", "love", "fantastic", "amazing", "excellent", "wonderful"],
        "negative": ["hate", "terrible", "awful", "stupid", "idiot", "worst", "disgusting", "pathetic"],
        "anger": ["furious", "angry", "rage", "pissed", "mad", "livid", "outraged"],
        "fear": ["scared", "afraid", "terrified", "worried", "anxious", "nervous"],
        "sadness": ["sad", "depressed", "upset", "heartbroken", "devastated", "miserable"],
    }

    def __init__(self):
        self._user_history: dict[str, list[tuple[float, float]]] = defaultdict(list)  # user -> [(timestamp, sentiment)]
        self._compiled_sarcasm = [re.compile(p, re.IGNORECASE) for p in self.SARCASM_PATTERNS]
        self._compiled_pa = [re.compile(p, re.IGNORECASE) for p in self.PASSIVE_AGGRESSIVE_PATTERNS]
        self._compiled_manip = [re.compile(p, re.IGNORECASE) for p in self.MANIPULATION_PATTERNS]

    def analyze(self, content: str, user_id: str, timestamp: float) -> SentimentAnalysis:
        """Analyze a message for sentiment and behavioral markers."""
        text_lower = content.lower()

        # Base sentiment
        sentiment = self._calculate_sentiment(text_lower)

        # Sarcasm detection
        sarcasm = self._detect_sarcasm(text_lower, content)

        # Passive aggression
        pa = self._detect_passive_aggressive(text_lower)

        # Manipulation
        manip = self._detect_manipulation(text_lower)

        # Emotional keywords
        emotions = self._extract_emotional_keywords(text_lower)

        # Escalation tracking
        escalation = self._calculate_escalation(user_id, timestamp, sentiment)

        # Store for history
        self._user_history[user_id].append((timestamp, sentiment))
        # Keep only last 50 entries
        if len(self._user_history[user_id]) > 50:
            self._user_history[user_id] = self._user_history[user_id][-50:]

        return SentimentAnalysis(
            sentiment_score=sentiment,
            sarcasm_probability=sarcasm,
            passive_aggression=pa,
            escalation_delta=escalation,
            manipulation_score=manip,
            emotional_keywords=emotions,
        )

    def detect_coordinated_manipulation(self, messages: list[tuple[str, str, float]]) -> list[tuple[str, float]]:
        """
        Detect coordinated manipulation across multiple messages.
        Returns [(user_id, confidence)].
        """
        # Group by user
        user_msgs = defaultdict(list)
        for user_id, content, _ts in messages:
            user_msgs[user_id].append(content.lower())

        # Find users with similar phrasing patterns
        coordinated = []
        users = list(user_msgs.keys())
        for i, u1 in enumerate(users):
            for u2 in users[i + 1:]:
                similarity = self._phrase_similarity(user_msgs[u1], user_msgs[u2])
                if similarity > 0.7:
                    coordinated.append((u1, similarity))
                    coordinated.append((u2, similarity))

        return coordinated

    def get_user_trajectory(self, user_id: str) -> str:
        """Get a summary of the user's sentiment trajectory."""
        history = self._user_history.get(user_id, [])
        if len(history) < 3:
            return "insufficient_data"

        recent = history[-10:]
        sentiments = [s for _, s in recent]
        avg = sum(sentiments) / len(sentiments)
        trend = sentiments[-1] - sentiments[0]

        if trend < -0.3:
            return "declining"
        elif trend > 0.3:
            return "improving"
        elif avg < -0.5:
            return "consistently_negative"
        elif avg > 0.5:
            return "consistently_positive"
        return "stable"

    # ------------------------------------------------------------------
    # Detection methods
    # ------------------------------------------------------------------

    def _calculate_sentiment(self, text: str) -> float:
        """Simple lexicon-based sentiment scoring."""
        positive = sum(1 for w in self.EMOTIONAL_KEYWORDS["positive"] if w in text)
        negative = sum(1 for w in self.EMOTIONAL_KEYWORDS["negative"] if w in text)
        anger = sum(1 for w in self.EMOTIONAL_KEYWORDS["anger"] if w in text)

        total = positive + negative + anger + 1
        score = (positive - negative - anger * 1.5) / total
        return max(-1.0, min(1.0, score))

    def _detect_sarcasm(self, text: str, original: str = "") -> float:
        """Detect sarcasm probability."""
        matches = sum(1 for p in self._compiled_sarcasm if p.search(text))
        # Punctuation patterns: excessive exclamation, question marks, ellipsis
        bonus = 0.0
        if "..." in text or text.count("!") > 2:
            bonus = 0.1
        # Capitalization patterns (check original text before lowering)
        check = original if original else text
        if check.upper().count("THANK") > 0 or check.upper().count("GREAT") > 0:
            bonus += 0.1
        return min(1.0, matches * 0.25 + bonus)

    def _detect_passive_aggressive(self, text: str) -> float:
        """Detect passive-aggressive probability."""
        matches = sum(1 for p in self._compiled_pa if p.search(text))
        # Backhanded compliments
        if "but" in text and any(p in text for p in ["good", "nice", "great", "smart"]):
            matches += 0.5
        return min(1.0, matches * 0.3)

    def _detect_manipulation(self, text: str) -> float:
        """Detect manipulation probability."""
        matches = sum(1 for p in self._compiled_manip if p.search(text))
        # Peer pressure language
        peer_pressure = ["everyone else", "all of us", "the group", "no one else"]
        for phrase in peer_pressure:
            if phrase in text:
                matches += 0.5
        return min(1.0, matches * 0.25)

    def _extract_emotional_keywords(self, text: str) -> list[str]:
        """Extract emotional keywords present in text."""
        found = []
        for category, words in self.EMOTIONAL_KEYWORDS.items():
            for w in words:
                if w in text:
                    found.append(f"{category}:{w}")
        return found

    def _calculate_escalation(self, user_id: str, timestamp: float, sentiment: float) -> float:
        """Calculate sentiment escalation from previous messages."""
        history = self._user_history.get(user_id, [])
        if not history:
            return 0.0
        last_sentiment = history[-1][1]
        return sentiment - last_sentiment

    def _phrase_similarity(self, msgs1: list[str], msgs2: list[str]) -> float:
        """Calculate similarity between two users' message phrases."""
        if not msgs1 or not msgs2:
            return 0.0

        # Extract 3-grams
        def get_ngrams(texts, n=3):
            ngrams = set()
            for text in texts:
                words = text.split()
                for i in range(len(words) - n + 1):
                    ngrams.add(" ".join(words[i:i + n]))
            return ngrams

        ngrams1 = get_ngrams(msgs1)
        ngrams2 = get_ngrams(msgs2)

        if not ngrams1 or not ngrams2:
            return 0.0

        intersection = ngrams1 & ngrams2
        union = ngrams1 | ngrams2
        return len(intersection) / len(union) if union else 0.0
