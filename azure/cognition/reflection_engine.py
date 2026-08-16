"""
ReflectionEngine — Upgrade 6: Scoring and Extraction

Creates reflections from the cognitive pipeline's output and decides which
are worth remembering. Scores them 0-100 and stores high-value ones.

Also retrieves relevant reflections before reasoning to inject warnings
and corrections into the pipeline.

Usage:
    engine = ReflectionEngine(reflection_memory)

    # After processing a message:
    engine.create_reflections(state, response)

    # Before processing a message:
    relevant = engine.retrieve_for_message("create a gaming channel")
    # inject into context
"""

from __future__ import annotations

from .cognitive_state import CognitiveState, Complexity, Risk, ToolDecision
from .reflection_memory import Reflection, ReflectionMemory


class ReflectionEngine:
    """
    Scores and extracts reflections from cognitive state.

    The engine analyzes the complete cognitive state after processing and
    creates reflections for:
      - Intent misclassification (wrong intent was detected)
      - Tool mismatch (wrong tool was chosen)
      - Plan failure (plan failed at some step)
      - Risky output (adversarial review found issues)
      - Confidence miscalibration (heuristic was wrong)
      - Success pattern (something worked well)
    """

    # Scoring weights — higher = more severe
    SEVERITY_WEIGHTS = {
        "intent_misclassification": 15,
        "tool_mismatch": 20,
        "plan_failure": 25,
        "risky_output": 30,
        "confidence_miscalibration": 10,
        "success_pattern": 5,
    }

    # Minimum score to store (reflection_memory has its own threshold too)
    STORE_THRESHOLD = 60

    def __init__(self, memory: ReflectionMemory | None = None):
        """
        Args:
            memory: ReflectionMemory instance. If None, creates one with defaults.
        """
        self.memory = memory if memory is not None else ReflectionMemory()
        self._session_reflections = 0

    # -----------------------------------------------------------------------
    # Reflection creation
    # -----------------------------------------------------------------------

    def create_reflections(self, state: CognitiveState, response: str) -> list[Reflection]:
        """
        Analyze cognitive state and create reflections for all notable events.

        Returns:
            List of created reflections (some may be rejected by memory threshold).
        """
        reflections = []

        # Check each reflection type
        r = self._check_intent_misclassification(state)
        if r:
            reflections.append(r)

        r = self._check_tool_mismatch(state)
        if r:
            reflections.append(r)

        r = self._check_plan_failure(state)
        if r:
            reflections.append(r)

        r = self._check_risky_output(state)
        if r:
            reflections.append(r)

        r = self._check_confidence_miscalibration(state)
        if r:
            reflections.append(r)

        r = self._check_success_pattern(state)
        if r:
            reflections.append(r)

        # Store high-value reflections
        stored = []
        for reflection in reflections:
            if self.memory.add(reflection):
                stored.append(reflection)
                self._session_reflections += 1

        return stored

    # -----------------------------------------------------------------------
    # Individual reflection checks
    # -----------------------------------------------------------------------

    def _check_intent_misclassification(self, state: CognitiveState) -> Reflection | None:
        """
        Detect if the intent was likely misclassified.

        Triggers when:
          - User later corrected the bot (e.g., "no, I meant...")
          - Bot had to ask for clarification
          - Overall confidence was very low
        """
        # Heuristic: if clarification was needed, intent was wrong
        if state.tool_decision != ToolDecision.CLARIFICATION:
            return None

        score = self.SEVERITY_WEIGHTS["intent_misclassification"] * (1 + len(state.ambiguities))
        score = min(100, score)

        return Reflection(
            message_pattern=state.raw_message,
            true_intent="unknown (clarification needed)",
            predicted_intent=state.true_intent,
            correction="Ask for clarification before acting",
            category="intent_misclassification",
            score=score,
            context={
                "modes": [m.value for m in state.modes],
                "confidence": state.overall_confidence,
                "ambiguities": state.ambiguities,
                "missing_info": state.missing_info,
            },
        )

    def _check_tool_mismatch(self, state: CognitiveState) -> Reflection | None:
        """
        Detect if the wrong tool was chosen.

        Triggers when:
          - Tool execution failed
          - Tool validation was rejected
          - Multiple tools were selected but only one was needed
        """
        if not state.selected_tools:
            return None

        # Tool execution failed
        if not state.execution_success:
            score = self.SEVERITY_WEIGHTS["tool_mismatch"] * 2
        # Tool validation failed
        elif state.review_issues and any("tool" in i.lower() for i in state.review_issues):
            score = self.SEVERITY_WEIGHTS["tool_mismatch"] * 1.5
        else:
            return None

        score = min(100, score)

        return Reflection(
            message_pattern=state.raw_message,
            true_intent=state.true_intent,
            predicted_intent="",
            correction=f"Use different tool for: {state.true_intent}",
            category="tool_mismatch",
            score=score,
            context={
                "selected_tools": state.selected_tools,
                "execution_success": state.execution_success,
                "review_issues": state.review_issues,
                "risk": state.risk.value,
            },
        )

    def _check_plan_failure(self, state: CognitiveState) -> Reflection | None:
        """
        Detect if a plan failed.

        Triggers when:
          - Plan was created but execution failed
          - Plan required confirmation but user cancelled
        """
        if not state.plan.execution_order:
            return None

        if state.execution_success and not state.confirmation_required:
            return None  # Plan succeeded

        if state.confirmation_required and not state.execution_result:
            # User cancelled or didn't confirm
            score = self.SEVERITY_WEIGHTS["plan_failure"] * 1.2
        else:
            # Plan failed during execution
            score = self.SEVERITY_WEIGHTS["plan_failure"] * 2

        score = min(100, score)

        total_steps = len(state.plan.execution_order)
        failed_step = state.execution_result or "unknown"

        return Reflection(
            message_pattern=state.raw_message,
            true_intent=state.true_intent,
            predicted_intent="",
            correction=f"Fix plan step: {failed_step}",
            category="plan_failure",
            score=score,
            context={
                "plan_steps": total_steps,
                "failed_step": failed_step,
                "risk": state.risk.value,
                "confirmation_required": state.confirmation_required,
            },
        )

    def _check_risky_output(self, state: CognitiveState) -> Reflection | None:
        """
        Detect if the output was flagged as risky by the critic.

        Triggers when:
          - Adversarial review found issues
          - Critic required override
        """
        review_issues = getattr(state, 'review_issues', None)
        if not review_issues:
            return None

        risk_level = 1
        if state.risk == Risk.HIGH:
            risk_level = 2
        elif state.risk == Risk.CRITICAL:
            risk_level = 3

        score = self.SEVERITY_WEIGHTS["risky_output"] * risk_level
        score = min(100, score)

        return Reflection(
            message_pattern=state.raw_message,
            true_intent=state.true_intent,
            predicted_intent="",
            correction=f"Be more cautious: {review_issues[0]}",
            category="risky_output",
            score=score,
            context={
                "review_issues": review_issues,
                "risk": state.risk.value,
                "review_passed": getattr(state, 'review_passed', True),
            },
        )

    def _check_confidence_miscalibration(self, state: CognitiveState) -> Reflection | None:
        """
        Detect if heuristic confidence was wrong.

        Triggers when:
          - Heuristic confidence was high but review found issues
          - Semantic reasoning was used and found different results than heuristic
        """
        if not state.semantic_reasoning_used:
            return None

        # Semantic was used because heuristic was uncertain
        # This is normal for complex messages
        if state.overall_confidence > 0.85 and state.review_issues:
            # Heuristic was overconfident but wrong
            score = self.SEVERITY_WEIGHTS["confidence_miscalibration"] * 2
        elif state.overall_confidence < 0.60 and not state.review_issues:
            # Heuristic was underconfident but correct
            score = self.SEVERITY_WEIGHTS["confidence_miscalibration"] * 1.5
        else:
            return None

        score = min(100, score)

        return Reflection(
            message_pattern=state.raw_message,
            true_intent=state.true_intent,
            predicted_intent="",
            correction=f"Adjust confidence for pattern: {state.raw_message[:50]}",
            category="confidence_miscalibration",
            score=score,
            context={
                "heuristic_confidence": state.overall_confidence,
                "semantic_used": state.semantic_reasoning_used,
                "review_issues": state.review_issues,
            },
        )

    def _check_success_pattern(self, state: CognitiveState) -> Reflection | None:
        """
        Detect successful patterns that should be repeated.

        Triggers when:
          - Plan executed successfully with no review issues
          - High confidence, high complexity task completed well
        """
        if not state.execution_success or state.review_issues:
            return None

        if state.complexity not in (Complexity.HIGH, Complexity.EXTREME):
            return None

        if state.overall_confidence < 0.80:
            return None

        score = self.SEVERITY_WEIGHTS["success_pattern"] * (1 + len(state.selected_tools))
        score = min(100, score)

        return Reflection(
            message_pattern=state.raw_message,
            true_intent=state.true_intent,
            predicted_intent="",
            correction="This approach worked well — repeat for similar requests",
            category="success_pattern",
            score=score,
            context={
                "modes": [m.value for m in state.modes],
                "tools": state.selected_tools,
                "complexity": state.complexity.value,
                "confidence": state.overall_confidence,
            },
        )

    # -----------------------------------------------------------------------
    # Retrieval for pipeline injection
    # -----------------------------------------------------------------------

    def retrieve_for_message(self, message: str, k: int = 3) -> list[Reflection]:
        """
        Retrieve relevant reflections for an incoming message.

        Returns reflections that might help the pipeline avoid past mistakes.
        """
        # Normalize message: lowercase, remove short words
        normalized = " ".join(
            w for w in message.lower().split()
            if len(w) > 3
        )
        return self.memory.retrieve(normalized, k=k)

    def retrieve_warnings(self, message: str) -> list[str]:
        """
        Get human-readable warning strings from relevant reflections.

        These can be injected into the context_summary for the pipeline.
        """
        reflections = self.retrieve_for_message(message, k=2)
        warnings = []
        for r in reflections:
            if r.category in ("risky_output", "plan_failure", "tool_mismatch"):
                warnings.append(f"WARNING: {r.correction}")
            elif r.category == "intent_misclassification":
                warnings.append(f"HINT: {r.correction}")
        return warnings

    def get_stats(self) -> dict:
        """Return engine + memory statistics."""
        return {
            "session_reflections": self._session_reflections,
            **self.memory.get_stats(),
        }
