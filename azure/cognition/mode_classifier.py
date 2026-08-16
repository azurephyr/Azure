"""
ModeClassifier — Phase 3 of the cognitive pipeline.

Classifies an incoming message into one or more modes:
  CHAT, QUESTION, MEMORY, TOOL, ADMIN, PLAN, ANALYSIS, AUTOMATION

Supports multi-label classification (e.g., "ban user X" → ADMIN + TOOL).
Uses keyword heuristics by default, LLM-assisted when available.
"""

from __future__ import annotations

import re

from .cognitive_state import Mode

# ---------------------------------------------------------------------------
# Keyword fingerprints per mode
# ---------------------------------------------------------------------------

# CHAT — casual conversation, greetings, off-topic chatter
CHAT_LEADERS = {
    "hi", "hello", "hey", "yo", "sup", "wassup", "howdy",
    "bye", "goodbye", "see ya", "nice", "cool", "lol", "lmao",
    "haha", "omg", "wtf", "bruh", "dude", "man", "bro",
    "thanks", "thank you", "thx", "ty", "appreciate",
    "no worries", "np", "yw",
}

CHAT_TRAILERS = {
    "what do you think", "how are you", "you good", "how's it going",
    "what's up", "how've you been", "long time",
    "enjoying", "loving this", "love it", "that's funny",
}

CHAT_NEUTRAL = {
    "just", "tbh", "imo", "idk", "btw", "fyi", "ngl", "smh",
    "literally", "basically", "actually", "honestly",
}

# QUESTION — user is asking for information
QUESTION_KEYWORDS = {
    "how", "what", "why", "who", "when", "where", "which",
    "can you", "could you", "would you", "is it", "are they",
    "does", "do you", "tell me", "explain", "show me",
    "what's the", "what are", "what is", "how do i",
    "how does", "why does", "why is", "what's your",
    "what should", "what would", "any idea", "can i",
    "wondering", "curious", "question", "ask",
}

QUESTION_PATTERNS = [
    r"\?$",                    # ends with ?
    r"^what(?:'s| is) .+\?$",  # "What is Discord?"
    r"^how(?:'s| is| do) .+\?$",  # "How do I..."
    r"^why(?:'s| is| do) .+\?$",  # "Why is..."
    r"\bwhat\b.+\bthan\b",     # "what is better than..."
]

# MEMORY — operations involving memory / recall
MEMORY_KEYWORDS = {
    "remember", "recall", "forget", "earlier", "before",
    "that time", "when i said", "you said", "do you remember",
    "what did i say", "what did you say", "remind me",
    "I told you", "as i said", "as you know", "fact check",
    "store this", "log this", "note that", "save this",
    "nevermind", "never mind", "actually scrap that",
}

# TOOL — tool execution requested
TOOL_KEYWORDS = {
    "calculate", "run", "execute", "call", "use tool",
    "do that", "make it", "get me", "fetch", "lookup",
    "search for", "find the", "check the", "look up",
    "run a", "do a", "call the", "use the",
}

# ADMIN — server administration / management
ADMIN_KEYWORDS = {
    "create", "make", "add", "set up", "setup", "configure",
    "organize", "build", "design", "fix", "change", "edit",
    "delete", "remove", "update", "modify", "arrange", "move",
    "better", "improve", "upgrade", "enhance", "optimize",
}

ADMIN_TARGETS = {
    "server", "channel", "role", "category", "permission",
    "channels", "roles", "categories", "permissions",
    "discord", "guild", "emoji", "invite", "widget",
    "afk", "system", "rules", "moderation",
}

ADMIN_ACTIONS = {
    "kick", "ban", "unban", "timeout", "mute", "deafen",
    "move", "nickname", "assign", "remove", "role",
    "give role", "take role", "warn", "strike",
}

# PLAN — planning, building, structuring
PLAN_KEYWORDS = {
    "plan", "planning", "design", "blueprint", "roadmap",
    "outline", "structure", "framework", "architecture",
    "build a", "create a", "set up a", "make a",
    "how should i", "what if we", "let's plan", "idea",
    "want a", "need a", "could we", "should we",
}

# ANALYSIS — analysis, health checks, auditing
ANALYSIS_KEYWORDS = {
    "analyze", "analysis", "health", "check", "audit", "review",
    "inspect", "scan", "diagnose", "evaluate", "assess",
    "how is the server", "what's missing", "recommend",
    "improve", "optimize", "impressions", "engagement",
    "status", "report", "summary", "overview", "breakdown",
}

# AUTOMATION — automation, scheduled tasks, batch operations
AUTOMATION_KEYWORDS = {
    "auto", "automate", "schedule", "cron", "routine",
    "every day", "every week", "every month", "repeat",
    "batch", "bulk", "mass", "many at once", "all at once",
    "when i", "after this", "once a", "daily", "weekly",
    "trigger", "on event", "webhook", "integration",
}


class ModeClassifier:
    """
    Multi-label mode classifier for Azure.

    A single message can belong to multiple modes simultaneously.
    Example: "ban user X" → ADMIN + TOOL
             "analyze the server health" → ANALYSIS + ADMIN
             "can you remember that for me?" → QUESTION + MEMORY

    Uses keyword fingerprints + regex patterns.
    Optionally uses LLM for ambiguous cases.
    """

    def __init__(self, llm=None):
        self.llm = llm

    def classify(
        self,
        message: str,
        user_name: str = "",
        is_directed: bool = True,
        is_dm: bool = False,
        is_mentioned: bool = False,
        _return_confidence: bool = False,
    ) -> list[Mode] | tuple[list[Mode], float]:
        """
        Classify a message into one or more modes.

        Args:
            message: Raw user message
            user_name: Display name of the user
            is_directed: True if the message was directed at the bot
            is_dm: True if this is a DM
            is_mentioned: True if bot was @mentioned

        Returns:
            List of Mode values (multi-label). Always returns at least [CHAT].
        """
        raw = message.strip()
        lower = raw.lower()
        words = set(re.findall(r"\b\w+\b", lower))

        scores: dict[Mode, float] = {m: 0.0 for m in Mode}

        # --- CHAT ---
        scores[Mode.CHAT] += self._score_chat(lower, words, is_directed, is_dm)

        # --- QUESTION ---
        scores[Mode.QUESTION] += self._score_question(lower, raw)

        # --- MEMORY ---
        scores[Mode.MEMORY] += self._score_memory(lower)

        # --- TOOL ---
        scores[Mode.TOOL] += self._score_tool(lower, words)

        # --- ADMIN ---
        scores[Mode.ADMIN] += self._score_admin(lower, words)

        # --- PLAN ---
        scores[Mode.PLAN] += self._score_plan(lower)

        # --- ANALYSIS ---
        scores[Mode.ANALYSIS] += self._score_analysis(lower)

        # --- AUTOMATION ---
        scores[Mode.AUTOMATION] += self._score_automation(lower)

        # If not directed at bot and no strong mode signals → CHAT only
        if not is_directed and not is_dm and not is_mentioned and max(scores.values()) <= 0.1:
            if _return_confidence:
                return [Mode.CHAT], 0.1
            return [Mode.CHAT]

        # Threshold: only return modes with positive scores
        threshold = 0.1
        active = [m for m, s in scores.items() if s >= threshold]

        # Always have at least CHAT as a fallback
        if not active:
            active = [Mode.CHAT]

        # Sort by score descending
        active.sort(key=lambda m: scores[m], reverse=True)

        # Calculate confidence: based on score spread and agreement
        if not active:
            confidence = 0.5
        else:
            top_score = scores[active[0]]
            second_score = scores[active[1]] if len(active) > 1 else 0.0
            # High top score, big gap to second → high confidence
            gap = top_score - second_score
            confidence = min(0.95, max(0.3, top_score * 0.6 + gap * 0.4))

        if _return_confidence:
            return active, confidence
        return active

    # -------------------------------------------------------------------------
    # Per-mode scoring helpers
    # -------------------------------------------------------------------------

    def _score_chat(self, lower: str, words: set, is_directed: bool, is_dm: bool) -> float:
        score = 0.0
        # Greeting leaders
        if any(g in lower for g in CHAT_LEADERS):
            score += 0.5
        # Question trailers
        if any(t in lower for t in CHAT_TRAILERS):
            score += 0.3
        # Neutral filler words (conversational, not a command)
        filler_count = sum(1 for f in CHAT_NEUTRAL if f in lower)
        score += min(filler_count * 0.05, 0.2)
        # Short messages that are directed are often chat
        if is_directed and len(lower) < 40 and "?" not in lower:
            score += 0.2
        # DM is usually chat
        if is_dm and score > 0:
            score += 0.1
        return score

    def _score_question(self, lower: str, raw: str) -> float:
        score = 0.0
        # Question mark at end
        if raw.strip().endswith("?"):
            score += 0.6
        # Question keywords
        if any(q in lower for q in QUESTION_KEYWORDS):
            score += 0.4
        # Regex patterns
        for pat in QUESTION_PATTERNS:
            if re.search(pat, raw, re.IGNORECASE):
                score += 0.4
                break
        # No action keywords (not a command)
        if not any(a in lower for a in ADMIN_KEYWORDS | ADMIN_TARGETS):
            score += 0.1
        return score

    def _score_memory(self, lower: str) -> float:
        score = 0.0
        if any(m in lower for m in MEMORY_KEYWORDS):
            score += 0.7
        # "I told you", "remember that", "as I said"
        if re.search(r"(?:i told you|remember|as i said|forgot|noted)", lower):
            score += 0.5
        return score

    def _score_tool(self, lower: str, words: set) -> float:
        score = 0.0
        if any(t in lower for t in TOOL_KEYWORDS):
            score += 0.5
        # Explicit tool call patterns
        if re.search(r"(?:call|use|run|execute)\s+(?:\w+\s+)?(?:tool|function|command)", lower):
            score += 0.6
        return score

    def _score_admin(self, lower: str, words: set) -> float:
        score = 0.0
        has_action = any(a in lower for a in ADMIN_KEYWORDS)
        has_target = any(t in lower for t in ADMIN_TARGETS)
        has_admin_action = any(a in lower for a in ADMIN_ACTIONS)

        if has_admin_action:
            score += 0.7
        if has_action and has_target:
            score += 0.6
        if has_action and not has_target:
            # "create something" without explicit target → possible admin
            score += 0.2
        # High-priority admin indicators
        if any(w in lower for w in ["kick", "ban", "unban", "delete channel", "delete role"]):
            score += 0.8
        return score

    def _score_plan(self, lower: str) -> float:
        score = 0.0
        if any(p in lower for p in PLAN_KEYWORDS):
            score += 0.6
        # Planning phrasings
        if re.search(r"(?:let'?s plan|we should|i want to build|i need to set up)", lower):
            score += 0.7
        # Multi-step framing
        if re.search(r"(?:first|then|next|finally|steps? to)", lower) and any(
            k in lower for k in ["create", "build", "make", "set up"]
        ):
            score += 0.4
        return score

    def _score_analysis(self, lower: str) -> float:
        score = 0.0
        if any(a in lower for a in ANALYSIS_KEYWORDS):
            score += 0.6
        # "how is X doing" → analysis
        if re.search(r"how (?:is|are|going|doing)", lower) and any(
            w in lower for w in ["server", "it", "this", "things", "members"]
        ):
            score += 0.5
        return score

    def _score_automation(self, lower: str) -> float:
        score = 0.0
        if any(a in lower for a in AUTOMATION_KEYWORDS):
            score += 0.6
        if re.search(r"(?:every|once|daily|weekly|monthly|on schedule)", lower):
            score += 0.5
        return score
