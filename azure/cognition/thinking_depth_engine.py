"""
ThinkingDepthEngine — Phase 5 of the cognitive pipeline.

Maps complexity to thinking depth and configures:
  - prompt_depth: how detailed the system prompt should be
  - token_budget: max_tokens for the LLM call
  - reasoning_verbosity: how verbose the internal reasoning should be

Rules (from Azure Core Directive):
  LOW      → FAST
  MEDIUM   → NORMAL
  HIGH     → DEEP
  EXTREME  → MAXIMUM
"""

from __future__ import annotations

from .cognitive_state import CognitiveState, Complexity, ThinkingDepth


class ThinkingDepthEngine:
    """
    Selects thinking depth based on complexity classification.

    Also configures LLM inference parameters per depth level,
    accounting for the CPU-only hardware constraint.
    """

    # Complexity → ThinkingDepth mapping
    COMPLEXITY_MAP = {
        Complexity.LOW:      ThinkingDepth.FAST,
        Complexity.MEDIUM:   ThinkingDepth.NORMAL,
        Complexity.HIGH:     ThinkingDepth.DEEP,
        Complexity.EXTREME:  ThinkingDepth.MAXIMUM,
    }

    # Token budget per depth (accounts for CPU inference being slow)
    TOKEN_BUDGETS = {
        ThinkingDepth.FAST:     64,   # Short reply, no context needed
        ThinkingDepth.NORMAL:   192,  # Standard reply
        ThinkingDepth.DEEP:     384,  # Longer reasoning
        ThinkingDepth.MAXIMUM:  512,  # Full reasoning (slower)
    }

    # Prompt depth hints
    PROMPT_DEPTHS = {
        ThinkingDepth.FAST:     "normal",
        ThinkingDepth.NORMAL:   "normal",
        ThinkingDepth.DEEP:     "detailed",
        ThinkingDepth.MAXIMUM: "comprehensive",
    }

    # Reasoning verbosity
    REASONING_VERBOSITY = {
        ThinkingDepth.FAST:     "brief",
        ThinkingDepth.NORMAL:   "normal",
        ThinkingDepth.DEEP:     "verbose",
        ThinkingDepth.MAXIMUM:  "thorough",
    }

    # Temperature per depth (creative vs. precise)
    TEMPERATURES = {
        ThinkingDepth.FAST:     0.5,   # Low creativity, fast
        ThinkingDepth.NORMAL:   0.7,   # Balanced
        ThinkingDepth.DEEP:     0.6,  # Slightly more precise for complex tasks
        ThinkingDepth.MAXIMUM:  0.5,  # Lowest creativity, most factual
    }

    def select(self, complexity: Complexity) -> ThinkingDepth:
        """Map complexity to thinking depth."""
        return self.COMPLEXITY_MAP.get(complexity, ThinkingDepth.NORMAL)

    def configure(self, depth: ThinkingDepth) -> dict:
        """
        Return LLM inference parameters for this depth.

        Returns:
            dict with keys: token_budget, prompt_depth, reasoning_verbosity, temperature
        """
        return {
            "token_budget":         self.TOKEN_BUDGETS[depth],
            "prompt_depth":         self.PROMPT_DEPTHS[depth],
            "reasoning_verbosity":  self.REASONING_VERBOSITY[depth],
            "temperature":         self.TEMPERATURES[depth],
        }

    def apply_to_state(self, state: CognitiveState) -> CognitiveState:
        """
        Apply thinking depth configuration to a CognitiveState.

        Mutates state in place and returns it.
        """
        cfg = self.configure(state.thinking_depth)
        state.token_budget        = cfg["token_budget"]
        state.prompt_depth        = cfg["prompt_depth"]
        state.reasoning_verbosity = cfg["reasoning_verbosity"]
        return state
