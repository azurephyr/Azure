"""
IntentDecomposer — Priority 4A: Deep Intent Understanding

Distinguishes literal request from true intent, detects hidden goals,
emotional context, and urgency. Runs in Python for speed, with Qwen
fallback for genuinely ambiguous cases.

Architecture:
  Router → IntentDecomposer (Python heuristic) → ReasonerAgent (Qwen)

Heuristic decomposer handles:
  - Emotional keywords (frustrated, angry, worried, excited, etc.)
  - Urgency signals (ASAP, now, urgent, emergency, etc.)
  - Hidden goal patterns ("mods are strict" → retention issue)
  - Ambiguity indicators (vague pronouns, missing context)

LLM fallback triggers when:
  - heuristic confidence < 0.6
  - emotional context detected but ambiguous
  - multiple hidden goals possible
  - message has high ambiguity count

Output: IntentDecomposition dataclass
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field

logger = logging.getLogger("azure.cognition.intent_decomposer")


# ---------------------------------------------------------------------------
# Emotional keywords
# ---------------------------------------------------------------------------

EMOTIONAL_KEYWORDS = {
    "frustrated": "frustration",
    "annoyed": "frustration",
    "angry": "anger",
    "mad": "anger",
    "pissed": "anger",
    "worried": "anxiety",
    "concerned": "anxiety",
    "nervous": "anxiety",
    "scared": "anxiety",
    "excited": "excitement",
    "hyped": "excitement",
    "stoked": "excitement",
    "happy": "happiness",
    "grateful": "gratitude",
    "thanks": "gratitude",
    "sad": "sadness",
    "disappointed": "sadness",
    "confused": "confusion",
    "lost": "confusion",
    "overwhelmed": "overwhelm",
    "stressed": "stress",
    "bored": "boredom",
    "tired": "fatigue",
}

# ---------------------------------------------------------------------------
# Urgency signals
# ---------------------------------------------------------------------------

URGENCY_PATTERNS = [
    (r"\b(asap|a\.s\.a\.p\.)\b", "ASAP mentioned"),
    (r"\b(right now|immediately|urgent|emergency|critical)\b", "urgency keyword"),
    (r"\b(quickly|fast|hurry|rush)\b", "speed pressure"),
    (r"\b(before|deadline|tonight|today|this minute)\b", "time constraint"),
    (r"!{2,}", "multiple exclamation marks"),
    (r"\b(please|pls|plz)\b.*\b(now|asap|quickly)\b", "polite urgency"),
]

# ---------------------------------------------------------------------------
# Hidden goal patterns (literal → true intent mapping)
# ---------------------------------------------------------------------------

HIDDEN_GOAL_PATTERNS = [
    # Moderation complaints
    (r"\bmods?\b.*\b(strict|aggressive|unfair|biased|power\s*tripp|abusing)\b",
     "moderation reform", "user feels unfairly treated by moderators"),
    (r"\bmods?\b.*\b(aren't?\s*doing|not\s*doing|do\s*nothing|inactive)\b",
     "moderation reform", "moderators are perceived as inactive or ineffective"),
    (r"\b(toxic|troll|spam|raid|harass|bully)\b",
     "toxicity mitigation", "server has behavioral problems"),

    # Retention / growth complaints
    (r"\b(people|members|users|folks)\b.*\b(leaving|left|quit|gone|died|dead)\b",
     "member retention", "members are leaving the server"),
    (r"\b(server|community|guild)\b.*\b(dying|dead|inactive|quiet|empty)\b",
     "member retention", "server is becoming inactive"),
    (r"\b(quiet|silent|no\s*one|nobody|ghost\s*town)\b",
     "member retention", "low activity detected"),

    # Growth requests
    (r"\b(grow|bigger|more\s*people|more\s*members|invite|promote|advertise)\b",
     "growth strategy", "user wants to grow the server"),
    (r"\b(boost|premium|perks|features|upgrade)\b",
     "server enhancement", "user wants to improve server capabilities"),

    # Engagement / events
    (r"\b(event|game|tournament|competition|contest|activity|fun)\b",
     "engagement boost", "user wants more activities or events"),
    (r"\b(boring|nothing\s*to\s*do|no\s*events|stale|stagnant)\b",
     "engagement boost", "server lacks activities"),

    # Structure / organization
    (r"\b(restructure|reorganize|redesign|rebuild|fix\s*layout|clean\s*up)\b",
     "structural reform", "user wants to reorganize server structure"),
    (r"\b(too\s*many|too\s*few|clutter|mess|confusing|channels?)\b",
     "structural reform", "channel/category structure needs work"),

    # Role / permissions
    (r"\b(role|permission|access|can't|cannot|not\s*allowed|need\s*access)\b",
     "role reform", "user has or wants role/permission changes"),

    # Automation
    (r"\b(bot|automate|auto|schedule| recurring|reminder|workflow)\b",
     "automation setup", "user wants automated processes"),

    # Onboarding
    (r"\b(new\s*member|welcome|onboard|join|first\s*time|newbie|noob)\b",
     "onboarding improvement", "new member experience needs work"),

    # Content / rules
    (r"\b(rule|guideline|policy|code|tos|conduct)\b",
     "policy review", "rules or guidelines need attention"),
]


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class IntentDecomposition:
    """Deep decomposition of a user message into layers of intent."""
    literal_request: str = ""
    true_intent: str = ""
    hidden_goals: list[str] = field(default_factory=list)
    emotional_context: str = ""
    urgency: str = ""
    ambiguities: list[str] = field(default_factory=list)
    confidence: float = 0.0
    llm_used: bool = False

    def to_dict(self) -> dict:
        return {
            "literal_request": self.literal_request,
            "true_intent": self.true_intent,
            "hidden_goals": self.hidden_goals,
            "emotional_context": self.emotional_context,
            "urgency": self.urgency,
            "ambiguities": self.ambiguities,
            "confidence": self.confidence,
            "llm_used": self.llm_used,
        }


# ---------------------------------------------------------------------------
# Decomposer
# ---------------------------------------------------------------------------

class IntentDecomposer:
    """
    Decomposes user messages into literal vs true intent, hidden goals,
    emotional context, and urgency.

    Design:
      - Python heuristics: fast (<1ms), handles common patterns
      - LLM fallback: only for genuinely ambiguous or nuanced messages
      - Confidence threshold: 0.6 (heuristic must be confident enough)
    """

    HEURISTIC_CONFIDENCE_THRESHOLD = 0.6
    MAX_LITERAL_LEN = 80

    def __init__(self, llm=None):
        self.llm = llm
        self._heuristic_count = 0
        self._llm_count = 0

    def decompose(self, message: str, context: str = "") -> IntentDecomposition:
        """
        Decompose a message into intent layers.

        Returns IntentDecomposition with all fields populated.
        """
        # Step 1: Heuristic decomposition (always runs)
        heuristic = self._heuristic_decompose(message, context)

        # Step 2: Decide if LLM fallback is needed
        if self._should_use_llm(heuristic):
            self._llm_count += 1
            llm_result = self._llm_decompose(message, context, heuristic)
            # Merge: LLM fills gaps, heuristic provides confidence
            return self._merge(heuristic, llm_result)

        self._heuristic_count += 1
        return heuristic

    # -----------------------------------------------------------------------
    # Heuristic decomposition
    # -----------------------------------------------------------------------

    def _heuristic_decompose(self, message: str, context: str = "") -> IntentDecomposition:
        """Fast Python-only decomposition."""
        lower = message.lower()

        # Literal request: truncate and clean
        literal = message[:self.MAX_LITERAL_LEN].strip()

        # True intent: extract from action verbs and objects
        true_intent = self._extract_true_intent(message)

        # Hidden goals: pattern matching
        hidden_goals = self._detect_hidden_goals(lower)

        # Emotional context: keyword scan
        emotional = self._detect_emotion(lower)

        # Urgency: pattern matching
        urgency = self._detect_urgency(lower, message)

        # Ambiguities: pronoun resolution, missing context
        ambiguities = self._detect_ambiguities(message, context)

        # Confidence: based on how many layers we detected confidently
        confidence = self._calculate_confidence(
            true_intent, hidden_goals, emotional, urgency, ambiguities
        )

        return IntentDecomposition(
            literal_request=literal,
            true_intent=true_intent,
            hidden_goals=hidden_goals,
            emotional_context=emotional,
            urgency=urgency,
            ambiguities=ambiguities,
            confidence=confidence,
            llm_used=False,
        )

    def _extract_true_intent(self, message: str) -> str:
        """Extract the true intent from action verbs and objects."""
        lower = message.lower()

        # Look for action verbs
        action_patterns = [
            (r"\b(need|want|would\s*like|should|could|can\s*you|please)\s+(\w+)\b", 2),
            (r"\b(let'?s?|make|create|set\s*up|build|design)\b", 1),
            (r"\b(fix|solve|address|handle|deal\s*with|resolve)\b", 1),
            (r"\b(analyze|check|audit|review|inspect|look\s*at)\b", 1),
            (r"\b(ban|kick|mute|timeout|warn|remove)\b", 1),
        ]

        for pattern, group in action_patterns:
            match = re.search(pattern, lower)
            if match:
                action = match.group(group)
                # Get the object of the action
                rest = message[match.end():].strip()
                obj = rest.split()[0] if rest.split() else ""
                if obj:
                    return f"{action} {obj}"
                return action

        # Fallback: extract first verb-noun pair
        words = message.split()
        if len(words) >= 2:
            return f"{words[0]} {words[1]}"
        return message[:50]

    def _detect_hidden_goals(self, lower: str) -> list[str]:
        """Detect hidden goals from pattern matching."""
        goals = []
        seen = set()

        for pattern, goal_category, reason in HIDDEN_GOAL_PATTERNS:
            if re.search(pattern, lower) and goal_category not in seen:
                goals.append(f"{goal_category}: {reason}")
                seen.add(goal_category)

        return goals

    def _detect_emotion(self, lower: str) -> str:
        """Detect emotional context from keywords."""
        emotions = []
        for keyword, emotion in EMOTIONAL_KEYWORDS.items():
            if keyword in lower:
                emotions.append(emotion)

        if not emotions:
            return "neutral"

        # Return the most common emotion
        counts = Counter(emotions)
        most_common = counts.most_common(1)[0][0]
        return most_common

    def _detect_urgency(self, lower: str, original: str) -> str:
        """Detect urgency from patterns."""
        signals = []
        for pattern, reason in URGENCY_PATTERNS:
            if re.search(pattern, lower, re.IGNORECASE) or re.search(pattern, original):
                signals.append(reason)

        if not signals:
            return "none"

        if len(signals) >= 2:
            return "high"
        return "medium"

    def _detect_ambiguities(self, message: str, context: str) -> list[str]:
        """Detect ambiguities in the message."""
        ambiguities = []
        lower = message.lower()

        # Vague pronouns
        if re.search(r"\b(it|this|that|they|them|those|these)\b", lower):
            ambiguities.append("Vague pronoun reference — unclear what 'it/this/that' refers to")

        # Missing context
        if re.search(r"\b(earlier|before|last time|previous|the other day)\b", lower) and not context:
            ambiguities.append("References prior context but no conversation history available")

        # Unspecified quantities
        if re.search(r"\b(some|a few|many|lots|more|less)\b", lower):
            ambiguities.append("Unspecified quantity — needs clarification")

        # Unspecified targets
        if re.search(r"\b(someone|somebody|everyone|all|them)\b", lower):
            ambiguities.append("Unspecified target — who specifically?")

        return ambiguities

    def _calculate_confidence(
        self, true_intent: str, hidden_goals: list,
        emotional: str, urgency: str, ambiguities: list
    ) -> float:
        """Calculate confidence score based on detection completeness."""
        score = 0.5  # Base confidence

        if true_intent:
            score += 0.1
        if hidden_goals:
            score += 0.15
        if emotional != "neutral":
            score += 0.1
        if urgency != "none":
            score += 0.05
        if ambiguities:
            score -= 0.1  # Ambiguities reduce confidence

        return min(1.0, max(0.0, score))

    # -----------------------------------------------------------------------
    # LLM fallback
    # -----------------------------------------------------------------------

    def _should_use_llm(self, heuristic: IntentDecomposition) -> bool:
        """Decide if LLM decomposition is needed."""
        if self.llm is None:
            return False

        # Low confidence → need LLM
        if heuristic.confidence < self.HEURISTIC_CONFIDENCE_THRESHOLD:
            return True

        # Emotional but ambiguous → need LLM
        if heuristic.emotional_context != "neutral" and len(heuristic.hidden_goals) > 1:
            return True

        # Many hidden goals → need LLM to disambiguate
        if len(heuristic.hidden_goals) >= 3:
            return True

        # High urgency with unclear intent → need LLM
        return bool(heuristic.urgency == "high" and not heuristic.true_intent)

    def _llm_decompose(
        self, message: str, context: str, heuristic: IntentDecomposition
    ) -> IntentDecomposition:
        """Qwen-powered deep decomposition for ambiguous messages."""
        prompt = f"""Decompose this Discord message into intent layers.

Message: "{message}"
Context: {context or 'none'}

Heuristic guess: {heuristic.to_dict()}

Return ONLY JSON:
{{
  "literal_request": "what the user literally asked for",
  "true_intent": "what they actually want",
  "hidden_goals": ["goal 1: reason", "goal 2: reason"],
  "emotional_context": "neutral|frustration|anger|anxiety|excitement|happiness|sadness|confusion",
  "urgency": "none|medium|high",
  "ambiguities": ["what's unclear"]
}}"""

        try:
            raw = self.llm.chat(
                [
                    {"role": "system", "content": "You are an intent decomposer. Output ONLY JSON."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=200,
                temperature=0.3,
            )

            # Extract JSON
            extracted = self._extract_json(raw)
            if extracted:
                data = json.loads(extracted)
                return IntentDecomposition(
                    literal_request=data.get("literal_request", heuristic.literal_request),
                    true_intent=data.get("true_intent", heuristic.true_intent),
                    hidden_goals=data.get("hidden_goals", heuristic.hidden_goals),
                    emotional_context=data.get("emotional_context", heuristic.emotional_context),
                    urgency=data.get("urgency", heuristic.urgency),
                    ambiguities=data.get("ambiguities", heuristic.ambiguities),
                    confidence=0.85,  # LLM output is higher confidence
                    llm_used=True,
                )
        except Exception as e:
            logger.error(f"[intent_decomposer] LLM error: {e}")


        # Fallback to heuristic
        return heuristic

    @staticmethod
    def _extract_json(raw: str) -> str | None:
        """Extract JSON from raw text."""
        raw = raw.strip()
        code_block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
        if code_block:
            return code_block.group(1)

        start = raw.find("{")
        if start == -1:
            return None

        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == '{':
                depth += 1
            elif raw[i] == '}':
                depth -= 1
                if depth == 0:
                    return raw[start:i + 1]
        return None

    def _merge(
        self, heuristic: IntentDecomposition, llm: IntentDecomposition
    ) -> IntentDecomposition:
        """Merge heuristic and LLM results. LLM fills gaps."""
        return IntentDecomposition(
            literal_request=llm.literal_request or heuristic.literal_request,
            true_intent=llm.true_intent or heuristic.true_intent,
            hidden_goals=llm.hidden_goals if llm.hidden_goals else heuristic.hidden_goals,
            emotional_context=llm.emotional_context or heuristic.emotional_context,
            urgency=llm.urgency or heuristic.urgency,
            ambiguities=llm.ambiguities if llm.ambiguities else heuristic.ambiguities,
            confidence=max(heuristic.confidence, llm.confidence),
            llm_used=llm.llm_used,
        )

    # -----------------------------------------------------------------------
    # Stats
    # -----------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return decomposition statistics."""
        total = self._heuristic_count + self._llm_count
        return {
            "total": total,
            "heuristic": self._heuristic_count,
            "llm": self._llm_count,
            "llm_rate": self._llm_count / total if total else 0,
        }
