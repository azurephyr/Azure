"""
SemanticReasoner — Upgrade 2: Hybrid Semantic Reasoning

Uses Qwen to reason semantically when the heuristic pipeline has low
confidence (< 0.75). This handles nuanced, ambiguous, and complex intent
that keyword/regex routing cannot reliably detect.

Example:
  "the mods are too aggressive and members keep leaving after arguments"
  → heuristic: LOW confidence (no keywords)
  → semantic (Qwen): "moderation_policy_review" + ["member_retention", "tension_resolution"]

The SemanticReasoner is NOT called for every message — only when:
  1. Overall confidence < threshold (default 0.75)
  2. Hidden goals detected
  3. High ambiguity in analysis phase
  4. Mode classification has multiple competing modes
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("azure.cognition.semantic_reasoner")


# ---------------------------------------------------------------------------
# Semantic analysis result
# ---------------------------------------------------------------------------

@dataclass
class SemanticAnalysis:
    """Output of semantic reasoning — overrides or enriches heuristic output."""
    # Override fields (these replace what the heuristic produced)
    true_intent: str = ""
    modes: list[str] = field(default_factory=list)  # List of Mode.value strings
    complexity: str = "MEDIUM"
    risk: str = "LOW"
    tool_required: bool = False
    selected_tools: list[str] = field(default_factory=list)

    # Enrichment fields (these are added to heuristic output)
    hidden_intent: str = ""
    hidden_goals: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    reasoning_chain: str = ""  # Brief explanation of how the conclusion was reached
    confidence: float = 0.85  # Higher than heuristic because it used deep reasoning
    requires_confirmation: bool = False


# ---------------------------------------------------------------------------
# System prompt for semantic reasoning
# ---------------------------------------------------------------------------

SEMANTIC_REASONER_PROMPT = """You are a deep semantic reasoner embedded in a Discord AI agent called Azure.

Given a user message and context, perform deep semantic analysis:

1. TRUE INTENT: What is the user ACTUALLY trying to accomplish? Not the literal words but the underlying goal.
2. HIDDEN INTENT: What are they NOT saying but probably want?
3. HIDDEN GOALS: Secondary objectives they may not have stated.
4. MODES: Classify into: CHAT, QUESTION, MEMORY, TOOL, ADMIN, PLAN, ANALYSIS, AUTOMATION (can be multiple).
5. COMPLEXITY: LOW / MEDIUM / HIGH / EXTREME
6. RISK: LOW / MEDIUM / HIGH / CRITICAL
7. TOOL REQUIRED: true / false
8. SELECTED TOOLS: list of tool names (or empty if no tool needed)
9. AMBIGUITIES: What is unclear or ambiguous in their request?
10. MISSING INFO: What would you need to act confidently?
11. CONSTRAINTS: Any stated limitations or requirements?
12. CONFIRMATION NEEDED: Should this action require explicit user confirmation before executing?

CRITICAL EXAMPLES:
- "the mods are too aggressive and members keep leaving after arguments"
  → true_intent: "moderation_policy_review_and_member_retention"
  → hidden_goals: ["member_retention", "tension_resolution", "policy_recalibration"]
  → modes: ["ADMIN", "ANALYSIS"]
  → risk: "HIGH" (moderation changes affect everyone)
  → missing_info: ["which specific mod actions caused departures", "what is acceptable behavior"]

- "do what we discussed earlier"
  → true_intent: "retrieve_prior_context"
  → hidden_goals: ["context_continuity"]
  → modes: ["MEMORY", "CHAT"]
  → missing_info: ["what was previously discussed"]

Return ONLY valid JSON like this:
{
  "true_intent": "...",
  "hidden_intent": "...",
  "hidden_goals": [...],
  "modes": [...],
  "complexity": "...",
  "risk": "...",
  "tool_required": true/false,
  "selected_tools": [...],
  "ambiguities": [...],
  "missing_info": [...],
  "constraints": [...],
  "reasoning_chain": "...",
  "confidence": 0.87,
  "requires_confirmation": true/false
}"""



class SemanticReasoner:
    """
    Qwen-powered semantic reasoning for low-confidence heuristic cases.

    Triggered when:
      - Overall heuristic confidence < threshold (default 0.75)
      - Ambiguous message with multiple possible interpretations
      - Hidden intent suspected (user's true goal differs from literal words)
      - Moderation-sensitive or emotionally-charged language

    The SemanticReasoner uses the full LLM to reason deeply about context,
    inference, and hidden goals — the same way Codex-style systems approach
    novel inputs.
    """

    CONFIDENCE_THRESHOLD = 0.75   # Below this → call semantic reasoner
    MAX_INFERENCE_TOKENS = 300    # Budget for semantic reasoning call

    def __init__(self, llm=None, threshold: float = 0.75):
        """
        Args:
            llm: LocalLLM instance (SubprocessLLM or LocalLLM)
            threshold: Confidence threshold below which semantic reasoning is triggered
        """
        self.llm = llm
        self.threshold = threshold
        self._invocations = 0

    def should_use(self, heuristic_confidence: float, has_ambiguities: bool = False,
                   has_hidden_goals: bool = False, message_length: int = 0,
                   modes: list = None) -> bool:
        """
        Decide whether semantic reasoning is needed.

        Called after heuristic classification. Triggers if:
          - Overall confidence is below threshold, OR
          - Has unresolved ambiguities, OR
          - Hidden goals suspected, OR
          - Message is long and vague (complex nuance), OR
          - [NEW] Mode is ADMIN, PLAN, or ANALYSIS (always use semantic reasoning for complex modes)

        UPGRADE #4: Complex modes (ADMIN/PLAN/ANALYSIS) now ALWAYS trigger semantic reasoning,
        even when heuristic confidence is high. These modes benefit from deep understanding.
        """
        if self.llm is None:
            return False  # No LLM available → stick with heuristics

        # NEW: Always use semantic reasoning for complex administrative modes
        # These benefit from deep understanding even when heuristics are confident
        if modes:
            mode_values = [m.value if hasattr(m, 'value') else str(m) for m in modes]
            complex_modes = {"ADMIN", "PLAN", "ANALYSIS", "AUTOMATION"}
            if any(mode in complex_modes for mode in mode_values):
                return True

        if heuristic_confidence < self.threshold:
            return True

        if has_ambiguities and heuristic_confidence < 0.85:
            return True

        if has_hidden_goals and heuristic_confidence < 0.80:
            return True

        # Long, vague messages → likely nuanced intent
        return bool(message_length > 80 and heuristic_confidence < 0.8)

    def analyze(
        self,
        message: str,
        user_name: str = "",
        conversation_history: list[dict] | None = None,
        user_memory: list[str] | None = None,
        server_memory: list[str] | None = None,
    ) -> SemanticAnalysis:
        """
        Perform deep semantic reasoning using Qwen.

        Args:
            message: Raw user message
            user_name: Display name of the user
            conversation_history: Recent turns [{role, content, name}]
            user_memory: Facts known about this user
            server_memory: Facts known about this server

        Returns:
            SemanticAnalysis with enriched understanding
        """
        self._invocations += 1

        if self.llm is None:
            return self._fallback_analysis(message)

        # Build context string for the LLM
        context_parts = []
        if user_name:
            context_parts.append(f"User: {user_name}")
        if server_memory:
            context_parts.append(f"Server context: {'; '.join(server_memory[:3])}")
        if user_memory:
            context_parts.append(f"User history: {'; '.join(user_memory[:3])}")
        if conversation_history:
            hist_lines = [
                f"{'User' if m.get('role')=='user' else 'Azure'}: {m.get('content','')[:80]}"
                for m in conversation_history[-4:]
            ]
            context_parts.append("Recent conversation:\n" + "\n".join(hist_lines))

        context_block = "\n".join(context_parts)
        if context_block:
            context_block = context_block + "\n\n"

        safe_message = message.replace("{", "{{").replace("}", "}}")
        prompt = (
            f"{SEMANTIC_REASONER_PROMPT}\n\n"
            f"{context_block}"
            f"USER MESSAGE: \"{safe_message}\"\n\n"
            f"Respond with ONLY JSON:"
        )

        messages = [
            {"role": "system", "content": "You are a deep semantic reasoner. Output ONLY JSON."},
            {"role": "user", "content": prompt},
        ]

        try:
            raw = self.llm.chat(messages, max_tokens=self.MAX_INFERENCE_TOKENS, temperature=0.3)
            return self._parse(raw, message)
        except Exception as e:
            logger.error(f"[semantic_reasoner] LLM error: {e}")

            return self._fallback_analysis(message)

    def _parse(self, raw: str, original_message: str) -> SemanticAnalysis:
        """Parse LLM output into SemanticAnalysis."""
        raw = raw.strip()

        # Try markdown code block first
        code_block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
        if code_block:
            json_str = code_block.group(1)
        else:
            # Find first { and parse
            start = raw.find("{")
            if start != -1:
                depth = 0
                for i in range(start, len(raw)):
                    if raw[i] == '{':
                        depth += 1
                    elif raw[i] == '}':
                        depth -= 1
                        if depth == 0:
                            json_str = raw[start:i + 1]
                            break
                else:
                    json_str = raw
            else:
                json_str = raw

        try:
            d = json.loads(json_str)
            return SemanticAnalysis(
                true_intent=d.get("true_intent", ""),
                modes=d.get("modes", []),
                complexity=d.get("complexity", "MEDIUM"),
                risk=d.get("risk", "LOW"),
                tool_required=d.get("tool_required", False),
                selected_tools=d.get("selected_tools", []),
                hidden_intent=d.get("hidden_intent", ""),
                hidden_goals=d.get("hidden_goals", []),
                ambiguities=d.get("ambiguities", []),
                missing_info=d.get("missing_info", []),
                constraints=d.get("constraints", []),
                reasoning_chain=d.get("reasoning_chain", ""),
                confidence=float(d.get("confidence", 0.85)),
                requires_confirmation=bool(d.get("requires_confirmation", False)),
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.info("[semantic_reasoner] parse error, using fallback")

            return self._fallback_analysis(original_message)

    def _fallback_analysis(self, message: str) -> SemanticAnalysis:
        """Return a conservative analysis when LLM fails."""
        lower = message.lower()
        return SemanticAnalysis(
            true_intent="general_conversation",
            modes=["CHAT", "QUESTION"],
            complexity="MEDIUM",
            risk="MEDIUM" if any(w in lower for w in ["ban", "kick", "delete", "admin"]) else "LOW",
            tool_required=False,
            selected_tools=[],
            hidden_intent="unclear",
            hidden_goals=[],
            ambiguities=["message is ambiguous — semantic analysis failed"],
            missing_info=["clarification needed"],
            constraints=[],
            reasoning_chain="Fallback: LLM unavailable, conservative defaults used",
            confidence=0.50,
            requires_confirmation=False,
        )

    @property
    def stats(self) -> dict:
        """Return invocation stats."""
        return {"invocations": self._invocations}
