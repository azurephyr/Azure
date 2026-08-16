"""
ResponseGenerator — Upgrade 5.5: Dedicated Response Generation Phase

After the Council completes (Router → Reasoner → Executor → Critic),
this phase generates the final user-facing response.

The ResponseGenerator is a Qwen-powered agent that:
- Takes the full cognitive state as input
- Generates a natural, context-aware response
- Considers the Critic's feedback (if any)
- Ensures tone and length match the request intensity

Architecture:
  Router → Reasoner → Executor → Critic → ResponseGenerator → Output

This separates response generation from decision-making, improving quality.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from .cognitive_state import CognitiveState

logger = logging.getLogger("azure.cognition.response_generator")


# ---------------------------------------------------------------------------
# Response output
# ---------------------------------------------------------------------------

@dataclass
class GeneratedResponse:
    """Output of the Response Generator."""
    text: str
    tone: str = "neutral"  # casual, formal, cautious, enthusiastic
    length: str = "normal"  # short, normal, detailed
    confidence: float = 0.85


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

RESPONSE_GENERATOR_PROMPT = """You are Azure's voice. You take raw system state and produce the final text the user sees.

Your job: the tools executed successfully. Describe what was done in past tense. Be direct and confident.

CRITICAL RULES:
1. Lead with a single sentence summary using the EXACT tool counts from the state below.
2. Then list the structure in a clean bullet format.
3. Never ask questions, never say "I paused", never ask for input.
4. Never mention internal systems or cognitive state.
5. Remove any caution, hesitation, or requests from the raw state.

STRIP THESE PHRASES if they appear in the raw response:
- "I have paused"
- "I need you to define"
- "Please provide"
- "Shall I"
- "Would you like"
- "Confirm if"

Return ONLY valid JSON:
{
  "text": "Done. [summary of what was built]",
  "tone": "direct",
  "length": "detailed",
  "confidence": 0.95
}"""


# ---------------------------------------------------------------------------
# ResponseGenerator class
# ---------------------------------------------------------------------------

class ResponseGenerator:
    """
    Qwen-powered response generation agent.

    Generates the final user-facing response after the Council completes.
    """

    MAX_TOKENS = 400  # Increased: need room for detailed, high-quality responses
    TEMPERATURE = 0.7  # Slightly higher for natural language

    def __init__(self, llm=None):
        """
        Args:
            llm: LocalLLM / SubprocessLLM / ApiLLM / HybridLLM instance
        """
        self.llm = llm
        self._invocations = 0

    def generate(
        self,
        state: CognitiveState,
        critique: object | None = None,  # CriticReview or None
        executor_response: str = "",
    ) -> GeneratedResponse:
        """
        Generate the final response based on the full cognitive state.

        Args:
            state: Complete CognitiveState from the Council
            critique: CriticReview output (if Critic was invoked)
            executor_response: Raw response from the Executor

        Returns:
            GeneratedResponse with the final user-facing text
        """
        self._invocations += 1

        if self.llm is None:
            return self._fallback_generate(state, executor_response)

        # Build context for the LLM
        # Compute tool call counts from plan steps
        tool_counts = {}
        if state.plan and state.plan.execution_order:
            for step in state.plan.execution_order:
                t = step.tool or "unknown"
                tool_counts[t] = tool_counts.get(t, 0) + 1

        context_parts = [
            f"User: {state.user_name}",
            f"Message: \"{state.raw_message}\"",
            f"True intent: {state.true_intent}",
            f"Modes: {[m.value for m in state.modes]}",
            f"Complexity: {state.complexity.value}",
            f"Risk: {state.risk.value}",
        ]
        if tool_counts:
            summary = ", ".join(f"{k}={v}" for k, v in sorted(tool_counts.items()))
            context_parts.append(f"Tool calls: {summary}")

        if state.selected_tools:
            context_parts.append(f"Tools executed: {state.selected_tools}")
        if state.execution_result:
            context_parts.append(f"Result: {state.execution_result[:200]}")
        if state.confirmation_required:
            context_parts.append(f"Confirmation needed: {state.confirmation_message}")

        if critique and hasattr(critique, 'concerns'):
            if critique.concerns:
                context_parts.append(f"Critic concerns: {', '.join(critique.concerns[:2])}")
            if critique.suggestions:
                context_parts.append(f"Critic suggestions: {', '.join(critique.suggestions[:2])}")

        context_block = "\n".join(context_parts)

        prompt = (
            f"{RESPONSE_GENERATOR_PROMPT}\n\n"
            f"--- COGNITIVE STATE ---\n"
            f"{context_block}\n\n"
            f"--- EXECUTOR RESPONSE ---\n"
            f"{executor_response[:500]}\n\n"
            f"Generate the final user-facing response as JSON:"
        )

        messages = [
            {"role": "system", "content": "You are the Response Generator. Output ONLY JSON."},
            {"role": "user", "content": prompt},
        ]

        try:
            raw = self.llm.chat(messages, max_tokens=self.MAX_TOKENS, temperature=self.TEMPERATURE)
            return self._parse(raw, state, executor_response)
        except Exception as e:
            logger.error(f"[response_generator] LLM error: {e}")

            return self._fallback_generate(state, executor_response)

    def _parse(self, raw: str, state: CognitiveState, executor_response: str) -> GeneratedResponse:
        """Parse LLM output into GeneratedResponse."""
        raw = raw.strip()

        # Extract JSON
        code_block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
        if code_block:
            json_str = code_block.group(1)
        else:
            json_str = None
            start = -1
            while True:
                start = raw.find("{", start + 1)
                if start == -1:
                    break
                depth = 0
                for i in range(start, len(raw)):
                    if raw[i] == '{':
                        depth += 1
                    elif raw[i] == '}':
                        depth -= 1
                        if depth == 0:
                            candidate = raw[start:i + 1]
                            try:
                                json.loads(candidate)
                                json_str = candidate
                                break
                            except json.JSONDecodeError:
                                break
                if json_str:
                    break

        if json_str is None:
            return self._fallback_generate(state, executor_response)

        try:
            d = json.loads(json_str)
            return GeneratedResponse(
                text=d.get("text", executor_response),
                tone=d.get("tone", "neutral"),
                length=d.get("length", "normal"),
                confidence=float(d.get("confidence", 0.85)),
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            return self._fallback_generate(state, executor_response)

    def _fallback_generate(self, state: CognitiveState, executor_response: str) -> GeneratedResponse:
        """Fallback when LLM pipeline fails. Only uses executor response."""
        if executor_response:
            return GeneratedResponse(text=executor_response, tone="neutral", length="normal", confidence=0.70)
        return GeneratedResponse(text="[Cognitive pipeline error.]", tone="neutral", length="short", confidence=0.0)

    @property
    def stats(self) -> dict:
        """Return invocation stats."""
        return {"invocations": self._invocations}
