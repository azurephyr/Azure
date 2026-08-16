"""Analysis phases — intent decomposition, complexity, thinking depth, tool decisions, semantic analysis."""

import contextlib
import logging
import time

from ..cognitive_state import CognitiveState, PhaseLog

logger = logging.getLogger(__name__)


class AnalysisPhaseMixin:
    """Mixin for analysis phases: intent decomposition, semantic reasoning, tool decisions."""

    def _apply_semantic_analysis(self, state: CognitiveState, analysis) -> None:
        """Apply SemanticReasoner output to state."""
        from ..cognitive_state import Complexity, Mode, Risk, ToolDecision
        if analysis.true_intent:
            state.true_intent = analysis.true_intent
        if analysis.hidden_intent:
            existing = set(state.hidden_goals)
            existing.update(analysis.hidden_goals)
            state.hidden_goals = list(existing)
        if analysis.modes:
            semantic_modes = []
            for m_str in analysis.modes:
                with contextlib.suppress(ValueError):
                    semantic_modes.append(Mode(m_str.upper()))
            if semantic_modes:
                state.modes = semantic_modes
        if analysis.complexity:
            with contextlib.suppress(ValueError):
                state.complexity = Complexity(analysis.complexity.upper())
        if analysis.risk:
            try:
                new_risk = Risk(analysis.risk.upper())
                if new_risk.value in ("CRITICAL", "HIGH"):
                    state.risk = new_risk
                    if analysis.requires_confirmation:
                        state.confirmation_required = True
                        state.confirmation_message = "\u26a0\ufe0f This action requires confirmation. Type `yes` to proceed or `no` to cancel."
            except ValueError:
                pass
        if analysis.selected_tools:
            state.selected_tools = analysis.selected_tools
            state.tool_decision = ToolDecision.SINGLE_TOOL if len(analysis.selected_tools) == 1 else ToolDecision.MULTIPLE_TOOLS

            if len(state.selected_tools) > 1 and self.tool_chain_planner:
                chain_plan = self.tool_chain_planner.plan(
                    selected_tools=state.selected_tools,
                    intent=state.true_intent,
                    complexity=state.complexity.value,
                )
                state.selected_tools = [chain_plan.tools[i] for i in chain_plan.execution_order]
                state.plan.tool_chain = chain_plan

        if analysis.ambiguities:
            for a in analysis.ambiguities:
                if a not in state.ambiguities:
                    state.ambiguities.append(a)
        if analysis.missing_info:
            for m in analysis.missing_info:
                if m not in state.missing_info:
                    state.missing_info.append(m)
        state.overall_confidence = max(state.overall_confidence, analysis.confidence)
        state.intent_confidence = analysis.confidence

    def _phase_intent_decomposition(
        self,
        state: CognitiveState,
        message: str,
        context_summary: str,
    ) -> None:
        """Decompose intent for simple messages (fast premium path)."""
        t_decompose = time.perf_counter()
        from ..intent_decomposer import IntentDecomposer
        decomposer = IntentDecomposer(llm=self.reasoner.llm if self.reasoner else None)
        decomposition = decomposer.decompose(message, context_summary)
        if decomposition.hidden_goals:
            state.hidden_goals = [g.split(":")[0] for g in decomposition.hidden_goals]
        if decomposition.emotional_context != "neutral":
            state.emotional_context = decomposition.emotional_context
        state.phases.append(PhaseLog(
            phase="DECOMPOSE",
            duration_ms=(time.perf_counter() - t_decompose) * 1000,
            result=f"{decomposition.emotional_context}, urgency={decomposition.urgency}, goals={len(decomposition.hidden_goals)}",
            confidence=decomposition.confidence,
        ))

    def _phase_semantic_analysis(
        self,
        state: CognitiveState,
        message: str,
        context_summary: str,
    ) -> bool:
        """Run semantic analysis if needed (fast premium path). Returns True if used."""
        t_semantic = time.perf_counter()
        semantic_used = False
        if self.semantic.should_use(
            heuristic_confidence=state.overall_confidence,
            has_ambiguities=len(state.ambiguities) > 0,
            has_hidden_goals=len(state.hidden_goals) > 0,
            message_length=len(message),
            modes=state.modes,
        ):
            try:
                analysis = self.semantic.analyze(
                    message=message,
                    user_name=state.user_name,
                    conversation_history=state.conversation_history,
                    user_memory=state.user_memory,
                    server_memory=state.server_memory,
                )
                semantic_used = True
                self._apply_semantic_analysis(state, analysis)
                state.semantic_reasoning_used = True
            except Exception as e:
                logger.warning(f"[cognitive_pipeline] semantic reasoning error: {e}")
                semantic_used = False

        state.phases.append(PhaseLog(
            phase="SEMANTIC",
            duration_ms=(time.perf_counter() - t_semantic) * 1000,
            result=f"{'used' if semantic_used else 'skipped'} (heuristic_conf={state.overall_confidence:.2f})",
            confidence=0.90 if semantic_used else 0.0,
        ))
        return semantic_used
