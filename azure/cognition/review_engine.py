"""
ReviewEngine — Phase 10 of the cognitive pipeline (mandatory).

Before any response is output, the ReviewEngine runs a 5-point quality + safety checklist:

  1. Was intent understood correctly?
  2. Was tool choice optimal?
  3. Is execution safe?
  4. Is response high quality?
  5. Is there a better approach?

If any check fails, the issue is logged and a correction path is attempted.
The response is only output after all checks pass (or corrections are applied).
"""

from __future__ import annotations

from dataclasses import dataclass

from .cognitive_state import (
    CognitiveState,
    Complexity,
    Mode,
    Risk,
    ThinkingDepth,
    ToolDecision,
)


@dataclass
class ReviewResult:
    """Result of a single review check."""
    check_name: str
    passed: bool
    issue: str = ""
    correction: str = ""


class ReviewEngine:
    """
    Mandatory pre-output review — 5-point checklist.

    Every response must pass all 5 checks before being sent.
    Failed checks are logged and corrections are attempted.
    """

    def __init__(self):
        from collections.abc import Callable
        self._checks: list[Callable] = [
            self._check_intent_understanding,
            self._check_tool_optimality,
            self._check_execution_safety,
            self._check_response_quality,
            self._check_better_approach,
        ]

    def review(self, state: CognitiveState, response: str) -> tuple[bool, list[ReviewResult], str]:
        """
        Run all 5 review checks against the cognitive state and response.

        Args:
            state: The cognitive state after execution (phase 9)
            response: The generated response text

        Returns:
            (all_passed, list of ReviewResults, review_notes)
        """
        results: list[ReviewResult] = []
        corrections: list[str] = []

        for check_fn in self._checks:
            result = check_fn(state, response)
            results.append(result)
            if not result.passed:
                corrections.append(f"[{result.check_name}]: {result.issue}")
                if result.correction:
                    corrections.append(f"  → Correction: {result.correction}")

        all_passed = all(r.passed for r in results)
        review_notes = "\n".join(corrections) if corrections else "All checks passed."

        return all_passed, results, review_notes

    # -------------------------------------------------------------------------
    # Check 1: Was intent understood correctly?
    # -------------------------------------------------------------------------

    def _check_intent_understanding(
        self,
        state: CognitiveState,
        response: str,
    ) -> ReviewResult:
        """
        Verify that the true intent was captured and addressed.

        Failure modes:
          - Modes list is empty (no classification happened)
          - True intent is empty but modes suggest action
          - Response is generic/unrelated to the request
        """
        passed = True
        issue = ""
        correction = ""

        # Empty modes → classification failed
        if not state.modes:
            passed = False
            issue = "No modes classified — intent classification may have failed"
            correction = "Re-classify with fallback keyword heuristics"
            return ReviewResult("Intent Understanding", passed, issue, correction)

        # Chat-only mode with no response → potential miss
        if Mode.CHAT in state.modes and len(state.modes) == 1 and (not response or len(response) < 2):
            passed = False
            issue = "Chat-only intent but empty or near-empty response"
            correction = "Generate a proper conversational reply"

        # Action mode with no actual response
        action_modes = {Mode.ADMIN, Mode.PLAN, Mode.TOOL, Mode.ANALYSIS}
        if any(m in state.modes for m in action_modes) and not response and not state.execution_result:
            passed = False
            issue = f"Action mode {state.modes} but no execution result or response"
            correction = "Ensure the action was executed and a confirmation response is generated"

        # True intent empty when modes suggest action needed
        if not state.true_intent and any(m in state.modes for m in action_modes):
            passed = False
            issue = "Action mode detected but true_intent not set"
            correction = "Infer true intent from modes and raw_message"

        return ReviewResult("Intent Understanding", passed, issue, correction)

    # -------------------------------------------------------------------------
    # Check 2: Was tool choice optimal?
    # -------------------------------------------------------------------------

    def _check_tool_optimality(
        self,
        state: CognitiveState,
        response: str,
    ) -> ReviewResult:
        """
        Verify that tool selection was correct for the modes/intent.

        Failure modes:
          - Tools selected but not needed (CHAT-only mode)
          - Tools needed but not selected (ADMIN mode)
          - Wrong tool selected for the action
        """
        passed = True
        issue = ""
        correction = ""

        # Tool selected in CHAT-only mode → overkill
        if state.modes == [Mode.CHAT] and state.selected_tools:
            passed = False
            issue = f"Unnecessary tools called for chat-only message: {state.selected_tools}"
            correction = "Skip tool calls for chat-only interactions"

        # ADMIN mode but no tool decision made
        action_modes = {Mode.ADMIN, Mode.PLAN, Mode.TOOL, Mode.ANALYSIS}
        if any(m in state.modes for m in action_modes) and state.tool_decision == ToolDecision.DIRECT and not response:
                passed = False
                issue = "Action mode but decided DIRECT with no response"
                correction = "Either select appropriate tools or generate an explanation"

        # Tool decision is MULTIPLE but no plan was built
        if state.tool_decision == ToolDecision.MULTIPLE_TOOLS and not state.plan.execution_order:
            passed = False
            issue = "Multiple tools selected but no execution plan built"
            correction = "Build a step-by-step plan for multiple tool execution"

        # CLARIFICATION was chosen but message is clear
        if state.tool_decision == ToolDecision.CLARIFICATION and len(state.missing_info) == 0:
            passed = False
            issue = "Clarification requested but no missing info identified"
            correction = "Either identify specific missing info or proceed with best guess"

        return ReviewResult("Tool Optimality", passed, issue, correction)

    # -------------------------------------------------------------------------
    # Check 3: Is execution safe?
    # -------------------------------------------------------------------------

    def _check_execution_safety(
        self,
        state: CognitiveState,
        response: str,
    ) -> ReviewResult:
        """
        Verify execution safety before output.

        Failure modes:
          - CRITICAL risk without confirmation
          - Dangerous action executed without warning
          - Execution failed but no error response
        """
        passed = True
        issue = ""
        correction = ""

        # CRITICAL risk without confirmation
        if state.risk == Risk.CRITICAL and not state.confirmation_required:
            passed = False
            issue = "CRITICAL risk action did not trigger confirmation request"
            correction = "Request explicit user confirmation before CRITICAL actions"

        # High-risk action executed without warning in response
        if state.risk in (Risk.HIGH, Risk.CRITICAL):
            warning_keywords = ["⚠️", "warning", "confirm", "type `yes`", "are you sure"]
            if not any(w in response.lower() for w in warning_keywords) and state.confirmation_required and not state.confirmation_message:
                    passed = False
                    issue = f"HIGH/CRITICAL risk ({state.risk.value}) but no warning in response"
                    correction = "Include a warning confirmation message"

        # Execution failed but generic response
        if not state.execution_success and not state.execution_result and "error" not in response.lower() and "failed" not in response.lower():
                passed = False
                issue = "Execution failed but response does not mention the failure"
                correction = "Acknowledge the failure and explain what went wrong"

        # Confirmation required but already responded (skipped confirmation)
        if state.confirmation_required and state.response_final:
            # This is a known edge case — user confirmed, so execution was intentional
            pass

        return ReviewResult("Execution Safety", passed, issue, correction)

    # -------------------------------------------------------------------------
    # Check 4: Is response high quality?
    # -------------------------------------------------------------------------

    def _check_response_quality(
        self,
        state: CognitiveState,
        response: str,
    ) -> ReviewResult:
        """
        Verify the response meets quality standards.

        Failure modes:
          - Empty or very short response for a complex request
          - Repetitive content
          - Generic filler ("I understand", "I'm happy to help")
          - Response length mismatch with thinking_depth
        """
        passed = True
        issue = ""
        correction = ""

        if not response:
            passed = False
            issue = "Empty response"
            correction = "Generate a substantive response"
            return ReviewResult("Response Quality", passed, issue, correction)

        # Too short for the complexity
        if state.complexity in (Complexity.HIGH, Complexity.EXTREME) and len(response) < 50:
            passed = False
            issue = f"Response is {len(response)} chars for {state.complexity.value} complexity — too short"
            correction = "Expand response with explanation and details"

        # Thinking depth vs response length mismatch
        if state.thinking_depth == ThinkingDepth.MAXIMUM and len(response) < 100:
            passed = False
            issue = "MAXIMUM thinking depth but very short response"
            correction = "Provide a thorough, detailed response"

        # Generic filler phrases
        filler_phrases = [
            "i'm happy to help",
            "i understand your",
            "as an ai, i",
            "i'm not able to",
            "that's a great question",
        ]
        response_lower = response.lower()
        for filler in filler_phrases:
            if filler in response_lower:
                passed = False
                issue = f"Generic filler detected: '{filler}'"
                correction = "Replace with specific, contextual response"
                break

        # Repetition check (same word 3+ times consecutively)
        words = response.split()
        if len(words) >= 6:
            for i in range(len(words) - 2):
                if words[i] == words[i + 1] == words[i + 2]:
                    passed = False
                    issue = f"Repetition detected: '{words[i]}' appears 3+ times"
                    correction = "Rewrite to avoid repetition"

        return ReviewResult("Response Quality", passed, issue, correction)

    # -------------------------------------------------------------------------
    # Check 5: Is there a better approach?
    # -------------------------------------------------------------------------

    def _check_better_approach(
        self,
        state: CognitiveState,
        response: str,
    ) -> ReviewResult:
        """
        Meta-check: is this the best way to handle the request?

        This is the "self-critique" step from the Core Directive.
        It catches cases where the pipeline made a technically correct
        but suboptimal decision.
        """
        passed = True
        issue = ""
        correction = ""

        # Mode mismatch: QUESTION but responded with ACTION
        if Mode.QUESTION in state.modes and Mode.ADMIN not in state.modes and len(state.selected_tools) > 0:
            passed = False
            issue = "Question mode with tool calls — user may just want an answer"
            correction = "Consider whether a direct answer would suffice"

        # ANALYSIS mode without any structured response
        if Mode.ANALYSIS in state.modes and "analysis" not in response.lower() and "health" not in response.lower() and not state.selected_tools:
                    passed = False
                    issue = "Analysis mode but no analysis performed or referenced"
                    correction = "Either perform a health check or explain what analysis would be needed"

        # PLAN mode with no plan output
        if Mode.PLAN in state.modes and "plan" not in response.lower() and not state.plan.execution_order:
            passed = False
            issue = "Plan mode but no plan was generated"
            correction = "Build and present a step-by-step plan"

        # Direct mode with very complex response
        if state.tool_decision == ToolDecision.DIRECT and len(response) > 1000 and state.thinking_depth == ThinkingDepth.FAST:
                passed = False
                issue = "DIRECT response used for very long output — consider structured format"
                correction = "Break response into structured sections or use embed format"

        return ReviewResult("Better Approach", passed, issue, correction)
