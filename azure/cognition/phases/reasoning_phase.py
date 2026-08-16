"""Reasoning and planning phases — reasoner agent, planning engine, clarification intercept, ReAct loop."""

import logging
import time

from ..cognitive_state import CognitiveState, PhaseLog

logger = logging.getLogger(__name__)


class ReasoningPhaseMixin:
    """Mixin for reasoning, planning, and ReAct loop phases."""

    def _phase_reasoning(
        self,
        state: CognitiveState,
        message: str,
        user_name: str,
        is_directed: bool,
        is_dm: bool,
        is_mentioned: bool,
        is_admin: bool,
        has_guild: bool,
        context_summary: str,
    ) -> object:
        """Phase 2: Reasoner Agent (Qwen) — deep intent, hidden goals, complexity, risk."""
        t_reasoner = time.perf_counter()
        # Include heuristic classification results so Reasoner knows the context
        heuristic_modes = [m.value for m in state.modes] if state.modes else []
        enriched_context = context_summary
        if heuristic_modes:
            enriched_context = f"Modes: {heuristic_modes} | {enriched_context}"
        analysis = self.reasoner.analyze(
            message=message,
            user_name=user_name,
            is_directed=is_directed,
            is_dm=is_dm,
            is_mentioned=is_mentioned,
            is_admin=is_admin,
            has_guild=has_guild,
            context_summary=enriched_context,
            conversation_history=state.conversation_history,
            user_memory=state.user_memory,
            server_memory=state.server_memory,
            prior_plans=state.prior_plans,
            tool_state=state.tool_state,
        )
        self.reasoner.apply_to_state(state, analysis)

        state.phases.append(PhaseLog(
            phase="REASONER",
            duration_ms=(time.perf_counter() - t_reasoner) * 1000,
            result=f"intent={state.true_intent[:40]}, complexity={state.complexity.value}, risk={state.risk.value}",
            confidence=analysis.confidence,
        ))
        return analysis

    def _check_clarification(self, state: CognitiveState) -> bool:
        """Check if clarification is needed. Returns True if intercepted."""
        if self.clarification_agent.should_clarify(state):
            question = self.clarification_agent.generate_clarification(state)
            state.response = question
            if not hasattr(state, '_reasoning_chain'):
                state._reasoning_chain = ""
            state._reasoning_chain += "\n[Clarification Intercept] Confidence too low or missing info. Asking user for clarification instead of guessing."
            state.phases.append(PhaseLog(
                phase="CLARIFY",
                duration_ms=0.0,
                result="intercepted",
            ))
            self._skip_response_gen = True

            if self.save_states:
                self._save_state(state)
            return True
        return False

    def _phase_planning(
        self,
        state: CognitiveState,
        params: dict,
        analysis: object,
    ) -> None:
        """Phase 3: Planning (Python + optional Qwen)."""
        t_plan = time.perf_counter()
        if self.complexity.needs_plan(state.complexity) or analysis.needs_plan:
            state.plan = self.planning.plan(state, params)
            if analysis.plan_description and not state.plan.analysis:
                state.plan.analysis = analysis.plan_description
            # Clear selected_tools only if the plan actually has steps (plan path takes priority)
            if state.plan.execution_order:
                state.selected_tools = []
            plan_summary = f"{len(state.plan.execution_order)} steps, requires_confirm={state.plan.requires_confirmation}"
        else:
            state.plan.execution_order = []
            plan_summary = "no plan needed"
        state.phases.append(PhaseLog(
            phase="PLAN",
            duration_ms=(time.perf_counter() - t_plan) * 1000,
            result=plan_summary,
        ))

    def _check_react_loop(
        self,
        state: CognitiveState,
        response: str,
        success: bool,
        react_iters: int,
        max_react_iters: int,
    ) -> bool:
        """Check if ReAct loop should continue. Returns True if continuing."""
        if success and state.selected_tools and not state.needs_confirmation:
            has_agentic_tool = any(t in ('web_search', 'execute_python') for t in state.selected_tools)
            if has_agentic_tool and react_iters < max_react_iters - 1:
                state.tool_state.append(f"Result of {', '.join(state.selected_tools)}: {response}")
                state.selected_tools = []
                state.response = ""
                if not hasattr(state, '_reasoning_chain'):
                    state._reasoning_chain = ""
                state._reasoning_chain += f"\n[ReAct Loop {react_iters + 1}] Tool finished. Thinking again..."
                return True
        return False
