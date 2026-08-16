"""
Azure Smart Model Router with Cascade Architecture

Routes queries to the appropriate model tier:
- Tier 0: Cached / Rule-based (instant, no LLM needed)
- Tier 1: Tiny model (1B) for simple greetings/chitchat
- Tier 2: Main model (3B Qwen) for general reasoning
- Tier 3: Specialist tools for coding, math, research
- Tier 4: Full cognitive pipeline for complex multi-step tasks

Auto-fallback chain: if any tier fails, cascade down to the next.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

logger = logging.getLogger("azure.model_router")


@dataclass
class RouterResult:
    """Output from the model router."""
    text: str
    tier: int
    tier_name: str
    latency_ms: float
    confidence: float
    used_fallback: bool = False


class ModelRouter:
    """
    Intelligent query routing with automatic fallback.

    Usage:
        router = ModelRouter(agent_llm)
        result = router.route("hello", user_context=ctx)
        # result.tier -> 0 (cache) or 2 (main model) etc.
    """

    # Tier thresholds — can be tuned via config
    TIER_CONFIG = {
        0: {"name": "cache", "max_latency_ms": 10},
        1: {"name": "tiny", "max_latency_ms": 500},
        2: {"name": "main", "max_latency_ms": 3000},
        3: {"name": "specialist", "max_latency_ms": 5000},
        4: {"name": "cognitive_pipeline", "max_latency_ms": 10000},
    }

    def __init__(self, main_llm, tiny_llm=None, cache=None):
        """
        Args:
            main_llm: The primary LLM (e.g., Qwen 3B)
            tiny_llm: Optional fast model for simple queries
            cache: Optional response cache (dict-like)
        """
        self.main_llm = main_llm
        self.tiny_llm = tiny_llm
        self.cache = cache or {}
        self._tier_stats = {t: {"calls": 0, "failures": 0} for t in self.TIER_CONFIG}

        # Build classification patterns and keywords
        patterns_and_keywords = self._build_patterns()
        self._greeting_patterns = patterns_and_keywords["greeting_patterns"]
        self._specialist_keywords = patterns_and_keywords["specialist_keywords"]
        self._cognitive_keywords = patterns_and_keywords["cognitive_keywords"]

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def route(self, message: str, user_context: dict | None = None,
              prefer_tier: int | None = None) -> RouterResult:
        """
        Route a message to the best tier and return the result.
        """
        t0 = time.perf_counter()
        user_context = user_context or {}

        # Determine target tier
        target = prefer_tier if prefer_tier is not None else self._classify_tier(message)

        # Try tiers from target down to 0 (cache/rules)
        for tier in range(target, -1, -1):
            result = self._try_tier(tier, message, user_context)
            if result is not None:
                latency_ms = (time.perf_counter() - t0) * 1000
                used_fallback = tier < target
                self._tier_stats[tier]["calls"] += 1
                return RouterResult(
                    text=result,
                    tier=tier,
                    tier_name=self.TIER_CONFIG.get(tier, {}).get("name", "unknown"),
                    latency_ms=latency_ms,
                    confidence=self._estimate_confidence(tier, message),
                    used_fallback=used_fallback,
                )

        # Absolute fallback — never return empty (all tiers including LLM failed)
        self._tier_stats[0]["failures"] += 1
        return RouterResult(
            text="[All model tiers failed. Check logs for details.]",
            tier=-1,
            tier_name="emergency_fallback",
            latency_ms=(time.perf_counter() - t0) * 1000,
            confidence=0.0,
            used_fallback=True,
        )

    def _classify_tier(self, message: str) -> int:
        """Classify the optimal tier for a message."""
        msg = message.strip().lower()

        # Tier 0: Simple greetings and common phrases (cached/rule-based)
        if any(pattern.match(msg) for pattern in self._greeting_patterns):
            return 0

        # Tier 0: Short casual queries under 20 chars
        if len(msg) < 20 and "?" not in msg:
            return 0

        # Tier 3: Code / math / technical markers
        if any(keyword in msg for keyword in self._specialist_keywords):
            return 3

        # Tier 4: Multi-step / planning / admin requests
        if any(keyword in msg for keyword in self._cognitive_keywords):
            return 4

        # v3: LLM fallback classification for ambiguous messages
        # If we have an LLM and the message is unclear, use LLM to classify
        if self.main_llm is not None and len(message) > 30:
            try:
                llm_tier = self._llm_classify_tier(message)
                if llm_tier is not None:
                    return llm_tier
            except Exception:
                pass

        # Tier 2: Default — general reasoning
        return 2

    def _llm_classify_tier(self, message: str) -> int | None:
        """Use LLM for ambiguous classification. Returns None on failure."""
        prompt = (
            "Classify this user message into exactly one category. "
            "Respond with ONLY the category number (0, 1, 2, 3, or 4).\n\n"
            "0 = Simple greeting or very short casual message (hi, hello, thanks, bye)\n"
            "1 = Simple chitchat or small talk (how are you, what's up, casual conversation)\n"
            "2 = General question or request (explain something, give advice, general chat)\n"
            "3 = Technical coding/math/research task (write code, debug, calculate, search facts)\n"
            "4 = Complex multi-step planning or admin task (plan, setup, configure, organize, design)\n\n"
            f"Message: \"{message[:200]}\"\n\n"
            "Category:"
        )
        try:
            raw = self.main_llm.chat([{"role": "user", "content": prompt}], max_tokens=10, temperature=0.1)
            raw = raw.strip()
            # Extract first digit
            for char in raw:
                if char in "01234":
                    return int(char)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Tier execution
    # ------------------------------------------------------------------

    def _try_tier(self, tier: int, message: str, ctx: dict) -> str | None:
        """Attempt to fulfill the request at the given tier."""
        try:
            if tier == 0:
                return self._tier_cache(message, ctx)
            elif tier == 1:
                return self._tier_tiny(message, ctx)
            elif tier == 2:
                return self._tier_main(message, ctx)
            elif tier == 3:
                return self._tier_specialist(message, ctx)
            elif tier == 4:
                return self._tier_cognitive(message, ctx)
        except Exception as e:
            self._tier_stats[tier]["failures"] += 1
            logger.error(f"[model_router] tier {tier} failed: {e}")

        return None

    def _tier_cache(self, message: str, ctx: dict) -> str | None:
        """Tier 0: Check cache or handle common greetings inline."""
        cache_key = message.strip().lower()
        if not cache_key:
            return None
        if cache_key in self.cache:
            return self.cache[cache_key]
        # Handle common greetings directly (no LLM needed)
        if any(pattern.match(cache_key) for pattern in self._greeting_patterns):
            return "Hello! How can I help you today?"
        return None

    def _tier_tiny(self, message: str, ctx: dict) -> str | None:
        """Tier 1: Fast tiny model for simple chitchat."""
        if self.tiny_llm is None:
            return None
        try:
            messages = [{"role": "user", "content": message}]
            raw = self.tiny_llm.chat(messages, max_tokens=64, temperature=0.7)
            return raw.strip() if raw else None
        except Exception:
            return None

    def _tier_main(self, message: str, ctx: dict) -> str | None:
        """Tier 2: Main model (Qwen 3B)."""
        if self.main_llm is None:
            return None
        try:
            messages = [{"role": "user", "content": message}]
            raw = self.main_llm.chat(messages, max_tokens=512, temperature=0.7)
            return raw.strip() if raw else None
        except Exception:
            return None

    def _tier_specialist(self, message: str, ctx: dict) -> str | None:
        """Tier 3: Specialist tools (coding, math, research)."""
        # This delegates to the ToolRegistry which handles code/python/web_search
        # We just signal that a specialist response is needed
        return None  # Let the agent's tool layer handle this

    def _tier_cognitive(self, message: str, ctx: dict) -> str | None:
        """Tier 4: Full cognitive pipeline."""
        # This is handled by the cognitive pipeline; router just signals intent
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_patterns(self) -> dict:
        """
        Build classification patterns and keywords.

        Returns dict with:
            - greeting_patterns: compiled regex patterns for greetings
            - specialist_keywords: keyword strings for specialist tier
            - cognitive_keywords: keyword strings for cognitive tier
        """
        return {
            "greeting_patterns": [
                re.compile(r"^(hi|hello|hey|yo|sup|what's up|how are you|howdy|greetings)\b.*"),
            ],
            "specialist_keywords": [
                "code", "python", "javascript", "bug", "error", "debug", "fix",
                "calculate", "math", "solve", "equation", "formula",
                "search", "find", "lookup", "research", "what is", "who is", "when did",
            ],
            "cognitive_keywords": [
                "plan", "create", "setup", "configure", "organize", "design",
                "analyze", "review", "audit", "strategy", "architecture",
            ],
        }

    def _estimate_confidence(self, tier: int, message: str) -> float:
        """Estimate confidence based on tier and message clarity."""
        base = {0: 0.95, 1: 0.85, 2: 0.75, 3: 0.80, 4: 0.70}.get(tier, 0.50)
        # Boost for short clear messages
        if 10 < len(message) < 200:
            base += 0.05
        return min(base, 0.99)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict:
        return {
            "tier_calls": {self.TIER_CONFIG[t]["name"]: s["calls"] for t, s in self._tier_stats.items()},
            "tier_failures": {self.TIER_CONFIG[t]["name"]: s["failures"] for t, s in self._tier_stats.items()},
        }
