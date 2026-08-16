"""
ComplexityEngine — Phase 4 of the cognitive pipeline.

Classifies task complexity: LOW / MEDIUM / HIGH / EXTREME
Also determines whether a formal plan is needed.

Complexity signals:
  - Number of distinct actions requested
  - Scope (single entity vs. whole server)
  - Reversibility (easy undo vs. permanent)
  - Cross-system dependencies
  - Number of targets / parameters
"""

from __future__ import annotations

import re

from .cognitive_state import Complexity, Mode

# ---------------------------------------------------------------------------
# Complexity signals
# ---------------------------------------------------------------------------

# HIGH complexity indicators
HIGH_COMPLEXITY_PATTERNS = [
    # Multi-step or structural
    r"(?:build|create|make|design)\s+(?:a\s+)?(?:whole|entire|full|complete)",
    r"(?:whole|entire|full)\s+server",
    r"(?:set up|setup)\s+(?:from scratch|from the ground)",
    r"(?:rebuild|restructure|redo|re-do)",
    r"(?:migration|migrate|transfer|convert)",
    r"(?:plan|blueprint|roadmap)\s+(?:for|to)",
    r"(?:audit|analyze)\s+(?:the\s+)?(?:entire|whole)?\s*server",
    r"(?:mass|bulk|batch)\s+(?:ban|kick|delete|moderat)",
    r"(?:automate|automation)\s+(?:the|this|all)",
    # Many moving parts
    r"(?:channels?|roles?|categories?)\s+(?:and|or)\s+(?:channels?|roles?|categories?)",
    r"(?:roles?)\s+(?:with|for)\s+(?:specific|complex|multiple)",
    # Permissions complexity
    r"(?:sync|inherit|override)\s+(?:permissions?|perms)",
    r"(?:permission|role)\s+(?:for|to)\s+(?:everyone|all|here)",
    # Data/state complexity
    r"(?:remember|recall|store|log)\s+(?:everything|all|multiple|these)",
    r"(?:database|state|memory)\s+(?:setup|config|init)",
]

# EXTREME complexity indicators
EXTREME_COMPLEXITY_PATTERNS = [
    # Destructive / irreversible
    r"(?:delete\s+all\s+(?:channels?|roles?|messages?|members?))",
    r"(?:purge|clear\s+all)",
    r"(?:reset|factory\s+reset| wipe)",
    r"(?:nuke|burn\s+it\s+down)",
    # Multi-server / cross-context
    r"(?:all\s+servers?|every\s+server)",
    r"(?:cross[-\s]server|cross[-\s]guild)",
    # Complex automation
    r"(?:build\s+a\s+(?:moderation|management|analytics)\s+system)",
    r"(?:create\s+a\s+(?:workflow|pipeline|bot)\s+that)",
    # Security-sensitive
    r"(?:change\s+(?:everyone|all)\s+(?:permissions?|roles?|nicknames?))",
    r"(?:admin|administrator)\s+(?:for\s+)?(?:everyone|all|here)",
]

# Simple / LOW complexity patterns
LOW_COMPLEXITY_PATTERNS = [
    r"^(?:hi|hello|hey|yo|sup|howdy|bye)\b",
    r"^(?:thanks?|thx|ty|appreciate)\b",
    r"^(?:what|how|why|who|when|where)\b.+\?$",
    r"^(?:cool|nice|ok|okay|sounds?\s+good|lgtm)\s*[.!]?\s*$",
    r"^lol{1,}\s*[.!]?\s*$",
    r"^lmao{1,}\s*[.!]?\s*$",
    r"^bruh\s*[.!]?\s*$",
]


class ComplexityEngine:
    """
    Determines how complex a user request is.

    Scoring factors:
      - Message length and structural complexity
      - Number of distinct intents/actions
      - Scope (single entity vs. entire server)
      - Reversibility
      - Ambiguity level
    """

    # Point thresholds
    EXTREME_THRESHOLD = 8
    HIGH_THRESHOLD    = 5
    MEDIUM_THRESHOLD  = 2

    def classify(
        self,
        message: str,
        modes: list[Mode],
        params: dict | None = None,
        _return_confidence: bool = False,
    ) -> Complexity | tuple[Complexity, float]:
        """
        Classify request complexity.

        Args:
            message: Raw user message
            modes: List of active modes from ModeClassifier
            params: Extracted parameters (optional)

        Returns:
            Complexity enum value
        """
        raw = message.strip()
        lower = raw.lower()
        params = params or {}

        score = 0

        # === LENGTH & STRUCTURE ===
        word_count = len(lower.split())
        if word_count <= 3:
            score -= 1  # Short → likely simple
        elif word_count >= 50:
            score += 3
        elif word_count >= 35:
            # Long multi-clause requests are operationally complex even when
            # they do not contain a recognized planning keyword.
            score += 4
        elif word_count >= 20:
            score += 1

        # Contains multiple sentences (complex structure)
        sentences = [s for s in re.split(r"[.!?]+", lower) if s.strip()]
        if len(sentences) >= 3:
            score += 1
        if len(sentences) >= 5:
            score += 1

        # === MODES ===
        if Mode.ANALYSIS in modes:
            score += 1
        if Mode.PLAN in modes:
            score += 2
        if Mode.ADMIN in modes:
            score += 1
        if Mode.AUTOMATION in modes:
            score += 2
        if Mode.TOOL in modes:
            score += 1
        if Mode.MEMORY in modes:
            score += 1

        # === KEYWORD PATTERNS ===
        # Check EXTREME patterns first (highest signal)
        for pat in EXTREME_COMPLEXITY_PATTERNS:
            if re.search(pat, lower):
                score += 4
                break

        for pat in HIGH_COMPLEXITY_PATTERNS:
            if re.search(pat, lower):
                score += 2
                break

        # A concrete management action still needs a deliberate tool call,
        # even when the user phrases it as casual conversation.
        if re.search(
            r"(?:create|edit|set|configure)\s+(?:a\s+)?"
            r"(?:role|channel|category|webhook|event)\b",
            lower,
        ):
            score += 1

        # Check LOW patterns (reduces score slightly)
        for pat in LOW_COMPLEXITY_PATTERNS:
            if re.match(pat, lower):
                score -= 1
                break

        # === PARAMETER COUNT ===
        # More extracted parameters → more complex request
        param_count = len(params)
        if param_count >= 5:
            score += 2
        elif param_count >= 3:
            score += 1

        # === MULTIPLE TARGETS ===
        # "channels and roles", "kick everyone", etc.
        if re.search(r"(?:all|every)\s+(?:the\s+)?(?:channels?|roles?|members?)", lower):
            score += 2
        if re.search(r"(?:channels?|roles?)\s+(?:and|or|,)\s+(?:channels?|roles?)\b", lower):
            score += 2

        # === IRREVERSIBILITY ===
        danger_words = ["delete", "ban", "kick", "remove", "purge", "clear", "reset", "wipe"]
        if any(w in lower for w in danger_words):
            score += 1

        # === SCOPE ===
        scope_signals = ["whole", "entire", "full", "all", "every", "complete"]
        if any(s in lower for s in scope_signals):
            score += 1

        # === PARAMETER-BASED OVERRIDES ===
        if params.get("target") in ("channels", "roles", "categories") and \
           params.get("theme"):
            score += 1  # Channel setup with theme = medium+

        # Clamp score
        score = max(0, score)

        # Classify based on score
        if score >= self.EXTREME_THRESHOLD:
            result = Complexity.EXTREME
        elif score >= self.HIGH_THRESHOLD:
            result = Complexity.HIGH
        elif score >= self.MEDIUM_THRESHOLD:
            result = Complexity.MEDIUM
        else:
            result = Complexity.LOW

        # Confidence: based on how far score is from the threshold boundary
        if result == Complexity.EXTREME:
            confidence = min(0.95, 0.75 + (score - self.EXTREME_THRESHOLD) * 0.05)
        elif result == Complexity.HIGH:
            confidence = min(0.90, 0.70 + (score - self.HIGH_THRESHOLD) * 0.05)
        elif result == Complexity.MEDIUM:
            confidence = min(0.85, 0.65 + (score - self.MEDIUM_THRESHOLD) * 0.05)
        else:
            confidence = min(0.90, 0.75 + (self.MEDIUM_THRESHOLD - score) * 0.1)

        if _return_confidence:
            return result, confidence
        return result

    def needs_plan(self, complexity: Complexity) -> bool:
        """Does this complexity level require a formal step-by-step plan?"""
        return complexity in (Complexity.HIGH, Complexity.EXTREME)
