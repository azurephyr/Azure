"""Pipeline initialization helpers, context injection, mode classification, and swarm consensus."""

from __future__ import annotations

import asyncio
import logging
import time

from ..cognitive_state import CognitiveState, PhaseLog

logger = logging.getLogger(__name__)


class SetupPhaseMixin:
    """Mixin for pipeline setup, context injection, and mode classification."""

    def _build_context(
        self,
        message: str,
        user_name: str,
        extra_context: str,
        conversation_history: list[dict] | None,
        user_memory: list[str] | None,
        server_memory: list[str] | None,
        prior_plans: list[str] | None,
        tool_state: list[str] | None,
    ) -> tuple[list[str], str]:
        """Build context summary from memory sources and user profiles."""
        context_parts = []
        if extra_context:
            context_parts.append(f"Server: {extra_context}")
        if user_name:
            context_parts.append(f"User: {user_name}")

        if hasattr(self, 'user_profiles') and self.user_profiles is not None and user_name:
            self.user_profiles.record_interaction(user_name, message)
            profile = self.user_profiles.get_profile(user_name)
            if profile.get("facts"):
                context_parts.append(f"User Facts: {' | '.join(profile['facts'])}")
            if profile.get("communication_style") and profile["communication_style"] != "neutral":
                context_parts.append(f"User Preference: preferred response style is {profile['communication_style']}")

        if hasattr(self, 'episodic_memory') and self.episodic_memory is not None:
            temp_history = (conversation_history or []) + [{"role": "user", "name": user_name, "content": message}]
            self.episodic_memory.add_message("user", user_name, message, temp_history)
            episodes = self.episodic_memory.get_recent_episodes()
            if episodes:
                context_parts.append(f"Past Conversation Summaries: {' | '.join(episodes)}")

        if hasattr(self, 'server_knowledge') and self.server_knowledge is not None and extra_context and extra_context != "Direct Message":
            sk_summary = self.server_knowledge.get_summary(extra_context)
            if sk_summary:
                context_parts.append(sk_summary)

        if server_memory:
            context_parts.append(f"Server facts: {' | '.join(server_memory[:5])}")
        if user_memory:
            context_parts.append(f"User facts: {' | '.join(user_memory[:5])}")
        if prior_plans:
            context_parts.append(f"Recent plans: {' | '.join(prior_plans[:2])}")
        if tool_state:
            context_parts.append(f"Recent actions: {' | '.join(tool_state[-3:])}")
        context_summary = " | ".join(context_parts)
        return context_parts, context_summary

    def _handle_goal_command(
        self,
        message: str,
        user_name: str,
        is_directed: bool,
        t_start: float,
    ) -> CognitiveState | None:
        """Handle explicit goal commands. Returns state if handled, else None."""
        goal_response = None
        if self.proactive_engine:
            goal_response = self.proactive_engine.handle_goal_command(message, user_name)
        if goal_response:
            state = CognitiveState(raw_message=message, user_name=user_name)
            state.is_directed = is_directed
            state.response = goal_response
            state.response_final = True
            state.phases.append(PhaseLog(
                phase="GOAL_COMMAND",
                duration_ms=0.0,
                result="handled goal command",
            ))
            if self.save_states:
                self._save_state(state)
            return state
        return None

    def _inject_goal_context(self, message: str, context_parts: list[str]) -> str:
        """Inject relevant active goals into context."""
        if self.goal_manager:
            relevant_goals = self.goal_manager.find_relevant(message, k=2)
            if relevant_goals:
                goal_summaries = [g.surface() for g, _ in relevant_goals[:1]]
                context_parts.append(f"Goals: {' | '.join(goal_summaries)}")
                context_summary = " | ".join(context_parts)
                return context_summary
        return " | ".join(context_parts)

    def _retrieve_reflections(self, message: str, context_parts: list[str]) -> str:
        """Retrieve reflection warnings from past mistakes."""
        if self.reflection_engine:
            reflection_warnings = self.reflection_engine.retrieve_warnings(message)
            if reflection_warnings:
                context_parts.append(f"Reflections: {' | '.join(reflection_warnings)}")
        return " | ".join(context_parts)

    async def _router_phase(
        self,
        message: str,
        user_name: str,
        is_directed: bool,
        is_dm: bool,
        is_mentioned: bool,
        params: dict,
        session_id: str = "",
    ) -> tuple[CognitiveState, float]:
        """Phase 1: Router — mode classification via ReasoningEngine."""
        t_router = time.perf_counter()
        try:
            _loop = asyncio.get_running_loop()
        except RuntimeError:
            state = self.reasoning.think(
                raw_message=message,
                user_name=user_name,
                is_directed=is_directed,
                is_dm=is_dm,
                is_mentioned=is_mentioned,
                params=params,
                session_id=session_id,
            )
            return state, t_router
        state = await self.reasoning.think_async(
                raw_message=message,
                user_name=user_name,
                is_directed=is_directed,
                is_dm=is_dm,
                is_mentioned=is_mentioned,
                params=params,
                session_id=session_id,
            )
        return state, t_router

    async def _swarm_consensus(
        self,
        state: CognitiveState,
        message: str,
        user_name: str,
        t_start: float,
    ) -> CognitiveState | None:
        """Swarm consensus for uncertain classifications. Returns state to return, or None."""
        router_confidence = state.overall_confidence
        num_modes = len(state.modes)
        use_swarm = (
            self.swarm is not None
            and (router_confidence < 0.6 or num_modes > 2 or router_confidence == 0.0)
        )
        if use_swarm:
            t_swarm = time.perf_counter()
            try:
                consensus = self.swarm.process(message, context={"user": user_name, "modes": [m.value for m in state.modes]})
                if consensus and consensus.confidence > router_confidence:
                    state.phases.append(PhaseLog(
                        phase="SWARM",
                        duration_ms=(time.perf_counter() - t_swarm) * 1000,
                        result=f"consensus from {', '.join(consensus.contributing_agents)}, conf={consensus.confidence:.2f}",
                        confidence=consensus.confidence,
                    ))
                    if consensus.text and len(consensus.text) > 10:
                        from ..cognitive_state import Mode
                        if Mode.CHAT in state.modes:
                            state.response = consensus.text
                            state.response_final = True
                            state.phases.append(PhaseLog(
                                phase="TOTAL",
                                duration_ms=(time.perf_counter() - t_start) * 1000,
                                result=f"SWARM FAST PATH in {(time.perf_counter() - t_start) * 1000:.1f}ms",
                            ))
                            if self.save_states:
                                self._save_state(state)
                            return state
            except Exception as e:
                logger.warning(f"[cognitive_pipeline] swarm error: {e}")
                state.phases.append(PhaseLog(
                    phase="SWARM",
                    duration_ms=(time.perf_counter() - t_swarm) * 1000,
                    result=f"error: {e}",
                ))
        return None

    def _detect_operator_mode(self, state: CognitiveState, message: str) -> None:
        """Detect objective-driven requests and trigger operator pipeline."""
        t_operator = time.perf_counter()
        operator_result = self.operator_router.classify(
            message, modes=[m.value for m in state.modes]
        )
        if operator_result.triggered:
            state.phases.append(PhaseLog(
                phase="OPERATOR",
                duration_ms=(time.perf_counter() - t_operator) * 1000,
                result=f"triggered (conf={operator_result.confidence:.2f}, audit={operator_result.audit_needed}, plan={operator_result.plan_needed})",
                confidence=operator_result.confidence,
            ))
            state._operator_mode = True
            state._operator_objective = operator_result.objective
