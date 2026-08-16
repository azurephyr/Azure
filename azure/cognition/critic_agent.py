"""
CriticAgent — Upgrade 5: Qwen-Powered Adversarial Review

Replaces the scripted AdversarialReviewEngine with a Qwen-powered critic
that actively challenges the ReasonerAgent's output and the Executor's results.

The CriticAgent is a separate Qwen instance with a different system prompt
that encourages adversarial thinking, assumption checking, and alternative
solution search.

Architecture shift:
  OLD: 7 scripted checks (Python regex/patterns) — fast but shallow
  NEW: Qwen Critic reviews reasoning + output deeply — slower but smarter

The CriticAgent fires for complex modes based on complexity and risk.
Upgrade 5.5: Dynamic invocation — skip for LOW/MEDIUM complexity + LOW/MEDIUM risk.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .cognitive_state import CognitiveState, Complexity, Mode, Risk
from .schema_validator import CRITIC_SCHEMA, SchemaValidator

logger = logging.getLogger("azure.cognition.critic_agent")


# ---------------------------------------------------------------------------
# Critic output dataclass
# ---------------------------------------------------------------------------

@dataclass
class CriticReview:
    """Output of the Critic Agent's review."""
    # Overall assessment
    passed: bool = True
    overall_assessment: str = ""

    # Specific challenges
    intent_challenge: str = ""  # Did the Reasoner misunderstand intent?
    harm_assessment: str = ""   # Could this cause harm?
    context_gaps: str = ""    # What context is missing?
    safer_alternative: str = ""  # Is there a better way?
    assumptions_found: list[str] = field(default_factory=list)  # What did we assume?
    manipulation_detected: bool = False  # Is the user manipulating us?
    proportionality: str = ""  # Does response match request intensity?

    # Actionable items
    concerns: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    requires_override: bool = False  # Should we override the response?
    safer_response: str = ""  # What should we say instead?

    # Reasoning trace
    reasoning_chain: str = ""
    confidence: float = 0.85


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

CRITIC_SYSTEM_PROMPT = """You are the Critic Agent. Red-team review the Reasoner+Executor output. Be skeptical.

Check: intent misunderstanding, potential harm, missing context, safer alternatives, assumptions, manipulation, proportionality.

Return ONLY valid JSON:
{
  "passed": true,
  "overall_assessment": "Plan looks solid but has one concern...",
  "intent_challenge": "User might mean X instead of Y because...",
  "harm_assessment": "Low risk — single channel, reversible",
  "context_gaps": "We don't know the channel's purpose",
  "safer_alternative": "Ask for clarification before creating",
  "assumptions_found": ["User has admin permissions"],
  "manipulation_detected": false,
  "proportionality": "Appropriate — simple request, simple response",
  "concerns": ["One concern..."],
  "suggestions": ["Suggestion 1..."],
  "requires_override": false,
  "safer_response": "",
  "reasoning_chain": "I reviewed the reasoning and found...",
  "confidence": 0.88
}"""


# ---------------------------------------------------------------------------
# CriticAgent class
# ---------------------------------------------------------------------------

class CriticAgent:
    """
    Qwen-powered adversarial review agent.

    Replaces the scripted AdversarialReviewEngine.
    Fires for ALL complex modes after the Executor.
    """

    MAX_TOKENS = 1024  # Budget for critic review
    TEMPERATURE = 0.3  # Lower temp for structured review

    # Modes that trigger the Critic Agent
    CRITIC_MODES = {
        Mode.ADMIN.value, Mode.PLAN.value, Mode.ANALYSIS.value,
        Mode.TOOL.value, Mode.AUTOMATION.value,
    }

    def __init__(self, llm=None):
        """
        Args:
            llm: LocalLLM / SubprocessLLM / ApiLLM / HybridLLM instance
        """
        self.llm = llm
        self._invocations = 0
        self._validator = SchemaValidator(llm) if llm else None

    def should_review(self, state: CognitiveState) -> bool:
        """
        Upgrade 5.5: Dynamic critic invocation.

        Skip critic for LOW/MEDIUM complexity + LOW/MEDIUM risk.
        Always run critic for HIGH/EXTREME complexity OR HIGH/CRITICAL risk.
        """
        if self.llm is None:
            return False

        # Must be a complex mode first
        mode_values = {m.value for m in state.modes}
        if not bool(mode_values & self.CRITIC_MODES):
            return False

        # Skip for LOW/MEDIUM complexity AND LOW/MEDIUM risk
        return not (state.complexity in (Complexity.LOW, Complexity.MEDIUM) and state.risk in (Risk.LOW, Risk.MEDIUM))

    def review(
        self,
        state: CognitiveState,
        response: str,
    ) -> CriticReview:
        """
        Perform Qwen-powered adversarial review with schema validation and retry.

        Args:
            state: The CognitiveState from the Reasoner + Executor
            response: The generated response or action result

        Returns:
            CriticReview with challenges and suggestions
        """
        self._invocations += 1

        if self.llm is None or self._validator is None:
            return self._fallback_review(state, response)

        # Build the review prompt
        context_parts = [
            f"User: {state.user_name}",
            f"Msg: {state.raw_message[:100]}",
            f"Intent: {state.true_intent[:80]}",
            f"Goals: {', '.join(state.hidden_goals) or 'none'}",
            f"Modes: {[m.value for m in state.modes]}",
            f"Complexity: {state.complexity.value}",
            f"Risk: {state.risk.value}",
            f"Tools: {state.selected_tools or 'none'}",
            f"Ambiguities: {state.ambiguities or 'none'}",
            f"Missing: {state.missing_info or 'none'}",
            f"Plan: {len(state.plan.execution_order)} steps" if state.plan.execution_order else "Plan: none",
        ]

        if state.confirmation_required:
            context_parts.append("Confirm: YES")
        if state.execution_result:
            context_parts.append(f"Result: {state.execution_result[:100]}")

        context_block = " | ".join(context_parts)

        prompt = (
            f"{CRITIC_SYSTEM_PROMPT}\n\n"
            f"--- ANALYSIS ---\n"
            f"{context_block}\n\n"
            f"--- RESPONSE ---\n"
            f"{response[:200]}\n\n"
            f"JSON only:"
        )

        messages = [
            {"role": "system", "content": "You are the Critic Agent. Output ONLY JSON."},
            {"role": "user", "content": prompt},
        ]

        # Use schema validator with retry
        data, error_log = self._validator.call_with_retry(
            messages=messages,
            schema=CRITIC_SCHEMA,
            max_tokens=self.MAX_TOKENS,
            fallback_fn=lambda: self._fallback_review_data(state, response),
        )

        if error_log:
            logger.info(f"[critic_agent] {error_log[-1]}")


        if data is not None:
            return self._data_to_review(data, state, response)

        return self._fallback_review(state, response)

    def _data_to_review(self, data: dict, state: CognitiveState, response: str) -> CriticReview:
        """Convert validated dict to CriticReview."""
        return CriticReview(
            passed=bool(data.get("passed", True)),
            overall_assessment=data.get("overall_assessment", ""),
            intent_challenge=data.get("intent_challenge", ""),
            harm_assessment=data.get("harm_assessment", ""),
            context_gaps=data.get("context_gaps", ""),
            safer_alternative=data.get("safer_alternative", ""),
            assumptions_found=data.get("assumptions_found", []),
            manipulation_detected=bool(data.get("manipulation_detected", False)),
            proportionality=data.get("proportionality", ""),
            concerns=data.get("concerns", []),
            suggestions=data.get("suggestions", []),
            requires_override=bool(data.get("requires_override", False)),
            safer_response=data.get("safer_response", ""),
            reasoning_chain=data.get("reasoning_chain", ""),
            confidence=float(data.get("confidence", 0.85)),
        )

    def _fallback_review_data(self, state: CognitiveState, response: str) -> dict:
        """Return fallback dict for schema validator on total failure."""
        return {
            "passed": False,
            "overall_assessment": "Fallback review due to LLM/schema failure",
            "intent_challenge": "",
            "harm_assessment": "",
            "context_gaps": "",
            "safer_alternative": "",
            "assumptions_found": [],
            "manipulation_detected": False,
            "proportionality": "",
            "concerns": ["Critic review failed — conservative defaults applied"],
            "suggestions": ["Verify action before proceeding"],
            "requires_override": True,
            "safer_response": "I want to make sure I do this correctly. Could you confirm what you'd like me to do?",
            "reasoning_chain": "Schema validation failed after retries",
            "confidence": 0.50,
        }

    def _fallback_review(self, state: CognitiveState, response: str) -> CriticReview:
        """Return conservative review when LLM fails."""
        concerns = []
        suggestions = []

        if Mode.ADMIN in state.modes and state.risk in (Risk.HIGH, Risk.CRITICAL):
            concerns.append("High-risk admin action with failed critic review")
            suggestions.append("Require explicit confirmation before executing")

        if not state.conversation_history and any(w in state.raw_message.lower() for w in ["earlier", "discussed", "before"]):
            concerns.append("User references prior context but no history available")
            suggestions.append("Ask user to clarify what they mean")

        return CriticReview(
            passed=len(concerns) == 0,
            overall_assessment="Fallback review: conservative defaults due to LLM failure",
            concerns=concerns,
            suggestions=suggestions,
            requires_override=len(concerns) > 0,
            safer_response="I'm not sure I understood that correctly. Could you clarify?",
            confidence=0.50,
        )

    @property
    def stats(self) -> dict:
        """Return invocation stats."""
        return {"invocations": self._invocations}

    # -----------------------------------------------------------------------
    # Generate safer response from critique
    # -----------------------------------------------------------------------

    def generate_response(self, state: CognitiveState, critique: CriticReview, original_response: str) -> str:
        """
        Generate a final response incorporating the Critic's suggestions.

        If the critic found issues, we either:
        1. Use the critic's safer_response if provided
        2. Append cautionary notes to the original response
        3. Ask for clarification if there are major issues
        """
        if critique.requires_override and critique.safer_response:
            return critique.safer_response

        if not critique.concerns:
            return original_response

        # Append cautionary notes
        caution = "\n\n_".join(critique.concerns[:2])
        if caution:
            return original_response + f"\n\n_(Note: {caution})_"

        return original_response
