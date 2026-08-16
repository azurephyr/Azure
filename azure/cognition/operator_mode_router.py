"""
OperatorModeRouter — Deliverable 3: Objective-Driven Request Detection

Lightweight classifier (rule-based) that detects objective-driven requests
vs. simple questions/chat.

When triggered, runs the pipeline:
  Objective → Audit (Tier 1 READ calls) → Diagnosis → Plan (tiered) → Execute → Report

No explicit "mode switch" announcement — it just looks like the bot got proactive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Objective-driven keyword patterns
# ---------------------------------------------------------------------------

OPERATOR_KEYWORDS = [
    # Audit / analysis requests
    r"\b(audit|analyze|review|inspect|diagnose|check|assess|evaluate|scan)\b",
    r"\b(what'?s?\s*wrong|what'?s?\s*the\s*problem|what'?s?\s*going\s*on|why\s*(is|are))\b",
    # Improvement / optimization requests
    r"\b(make\s*this\s*server\s*better|improve\s*the\s*server|fix\s*the\s*server|optimize\s*the\s*server)\b",
    r"\b(clean\s*up|tidy\s*up|reorganize|restructure|redesign|rebuild|overhaul)\b",
    r"\b(upgrade|enhance|boost|improve|fix|solve|address|handle|deal\s*with)\b.*\b(server|community|guild|bot)\b",
    # Setup / configuration requests
    r"\b(set\s*up|configure|build|create|design|make)\b.*\b(server|community|guild|bot|system|workflow)\b",
    r"\b(full\s*setup|from\s*scratch|start\s*fresh|new\s*server)\b",
    # Moderation / safety requests
    r"\b(fix\s*moderation|improve\s*moderation|moderation\s*issue|toxic|spam|raid|harassment)\b",
    # Growth / engagement requests
    r"\b(grow\s*the\s*server|get\s*more\s*members|increase\s*engagement|boost\s*activity)\b",
    # Automation requests
    r"\b(automate|automation|workflow|bot\s*setup|schedule|reminder|recurring)\b",
    # General objective phrasing
    r"\b(help\s*me\s*(with|make|fix|build)|i\s*want\s*to|i\s*need\s*to|can\s*you\s*help\s*me)\b.*\b(server|community|guild)\b",
]

# Simple chat patterns that should NOT trigger operator mode
SIMPLE_CHAT_PATTERNS = [
    r"\b(hello|hi|hey|yo|sup|howdy|greetings)\b",
    r"\b(how\s*are\s*you|what'?s?\s*up|what\s*are\s*you\s*doing)\b",
    r"\b(thanks|thank\s*you|ty|appreciate)\b",
    r"\b(bye|goodbye|cya|see\s*ya|later)\b",
    r"\b(yes|no|yep|nope|sure|ok|okay|fine)\b",
    r"\b(what\s*do\s*you\s*think|tell\s*me\s*about|what\s*is|who\s*is|when\s*is|where\s*is)\b",
    r"\b(joke|fact|story|trivia|quiz|game)\b",
]


@dataclass
class OperatorModeResult:
    """Result of operator mode classification."""
    triggered: bool = False
    objective: str = ""
    confidence: float = 0.0
    audit_needed: bool = False
    plan_needed: bool = False
    diagnosis: str = ""

    def to_dict(self) -> dict:
        return {
            "triggered": self.triggered,
            "objective": self.objective,
            "confidence": self.confidence,
            "audit_needed": self.audit_needed,
            "plan_needed": self.plan_needed,
            "diagnosis": self.diagnosis,
        }


class OperatorModeRouter:
    """
    Lightweight intent router for operator mode.

    Detects objective-driven requests and triggers the full operator pipeline.
    """

    def __init__(self):
        self._operator_patterns = [re.compile(p, re.IGNORECASE) for p in OPERATOR_KEYWORDS]
        self._simple_patterns = [re.compile(p, re.IGNORECASE) for p in SIMPLE_CHAT_PATTERNS]
        self._triggers = 0
        self._total = 0

    def classify(self, message: str, modes: list[str] = None) -> OperatorModeResult:
        """
        Classify a message. Returns OperatorModeResult.

        If triggered, the caller should run the full operator pipeline.
        """
        self._total += 1
        lower = message.lower().strip()

        # Check for simple chat first (fast path)
        mode_triggers = {"ADMIN", "PLAN", "ANALYSIS", "AUTOMATION"}
        has_operator_mode = any(m in mode_triggers for m in (modes or []))
        is_simple = any(p.search(lower) for p in self._simple_patterns)
        if is_simple and len(message) < 50 and not has_operator_mode:
            return OperatorModeResult(triggered=False, confidence=0.1)

        # Check for operator keywords
        matches = []
        for p in self._operator_patterns:
            m = p.search(lower)
            if m:
                matches.append(m.group(0))

        # Modes can also trigger operator mode
        mode_triggers = {"ADMIN", "PLAN", "ANALYSIS", "AUTOMATION"}
        mode_match = any(m in mode_triggers for m in (modes or []))

        if not matches and not mode_match:
            return OperatorModeResult(triggered=False, confidence=0.0)

        # Triggered
        self._triggers += 1
        confidence = min(1.0, 0.4 + 0.15 * len(matches))
        if mode_match:
            confidence = max(confidence, 0.7)

        # Extract objective from the message
        objective = self._extract_objective(message, matches)

        # Determine what pipeline steps are needed
        audit_needed = any(
            k in lower for k in ("audit", "analyze", "review", "diagnose", "check", "inspect", "what's wrong", "problem")
        )
        plan_needed = any(
            k in lower for k in ("build", "create", "set up", "make", "restructure", "redesign", "organize", "fix")
        )

        return OperatorModeResult(
            triggered=True,
            objective=objective,
            confidence=confidence,
            audit_needed=audit_needed,
            plan_needed=plan_needed,
        )

    def _extract_objective(self, message: str, matches: list[str]) -> str:
        """Extract the user's objective from the message."""
        # Strip fluff and extract the core ask
        lower = message.lower().strip()

        # Remove leading fluff
        fluff = ["azure", "Azure", "hey", "hi", "hello", "can you", "could you",
                 "please", "would you", "i want to", "i need to", "help me"]
        for f in fluff:
            if lower.startswith(f):
                lower = lower[len(f):].strip()
                if lower.startswith((",", " ", "!", "?", "to")):
                    lower = lower[1:].strip()
                if lower.startswith("to "):
                    lower = lower[3:].strip()

        # Extract first verb-object pair
        verbs = ["make", "build", "create", "fix", "improve", "optimize", "audit",
                 "analyze", "check", "review", "diagnose", "set up", "configure",
                 "restructure", "redesign", "clean up", "tidy up", "organize"]
        for verb in verbs:
            if verb in lower:
                idx = lower.find(verb)
                rest = lower[idx + len(verb):].strip()
                # Take up to 15 words
                words = rest.split()[:15]
                return f"{verb} {' '.join(words)}"

        return lower[:100]

    def get_stats(self) -> dict:
        """Return router statistics."""
        return {
            "total": self._total,
            "triggers": self._triggers,
            "trigger_rate": self._triggers / self._total if self._total else 0,
        }
