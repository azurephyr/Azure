"""
CognitivePipeline — Upgrade 5.5: Multi-Agent Council with Optimizations

The pipeline now follows the council architecture with three optimizations:

  1. Dynamic Critic Invocation — skip critic for LOW/MEDIUM complexity + LOW/MEDIUM risk
  2. Schema Validation — strict JSON validation with retry for all Qwen outputs
  3. Response Generator — dedicated Qwen-powered response generation phase

Full flow:
  Heuristic Router (Python) -> fast mode classification
    | (if ADMIN/ANALYSIS/PLAN/TOOL/AUTOMATION)
  Reasoner Agent (Qwen) -> deep intent, hidden goals, complexity, risk, tools
    |
  Planning Engine (Python) -> static templates
    |
  Executor (Python) -> validate, execute tools
    | (if HIGH/EXTREME complexity OR HIGH/CRITICAL risk)
  Critic Agent (Qwen) -> adversarial review, assumption checking
    | (always for complex modes)
  Response Generator (Qwen) -> natural user-facing response
    |
  Final Output (Python) -> format and deliver

For simple modes (CHAT/QUESTION), the pipeline skips the council.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from .adversarial_review_engine import AdversarialReviewEngine
from .clarification_agent import ClarificationAgent
from .cognitive_state import (
    CognitiveState,
    Complexity,
    PhaseLog,
    Risk,
)
from .complexity_engine import ComplexityEngine
from .critic_agent import CriticAgent
from .goal_manager import GoalManager
from .intent_decomposer import IntentDecomposer
from .pattern_extractor import PatternExtractor
from .planning_engine import PlanningEngine
from .proactive_engine import ProactiveEngine
from .reasoner_agent import ReasonerAgent
from .reasoning_engine import ReasoningEngine
from .reflection_engine import ReflectionEngine
from .response_generator import ResponseGenerator
from .review_engine import ReviewEngine
from .semantic_reasoner import SemanticReasoner
from .tool_chain_planner import ToolChainPlanner

# v3: Swarm integration
try:
    from ..swarm import SwarmCoordinator
except ImportError:
    SwarmCoordinator = None

# Response cache for performance
try:
    from ..response_cache import ResponseCache
except ImportError:
    ResponseCache = None

from .phases.analysis_phase import AnalysisPhaseMixin
from .phases.execution_phase import ExecutionPhaseMixin
from .phases.output_phase import OutputPhaseMixin
from .phases.reasoning_phase import ReasoningPhaseMixin
from .phases.review_phase import ReviewPhaseMixin
from .phases.setup_phase import SetupPhaseMixin

logger = logging.getLogger(__name__)


class CognitivePipeline(
    SetupPhaseMixin, AnalysisPhaseMixin, ReasoningPhaseMixin,
    ReviewPhaseMixin, ExecutionPhaseMixin, OutputPhaseMixin
):
    """
    Multi-Agent Council orchestrator.

    The pipeline now routes messages through the council:
      1. ROUTER (Python) -> fast mode classification (<2ms)
      2. REASONER (Qwen) -> deep reasoning for complex modes
      3. PLANNER (Python) -> static templates + optional Qwen for novel tasks
      4. EXECUTOR (Python) -> tool validation, execution, permissions
      5. CRITIC (Qwen) -> adversarial review, assumption checking
      6. OUTPUT (Python) -> format final response

    For CHAT/QUESTION modes, steps 2-5 are skipped for speed.
    """

    def __init__(
        self,
        agent=None,
        llm=None,
        extra_tools: list | None = None,
        log_dir: str | Path | None = "logs",
        save_states: bool = True,
        semantic_threshold: float = 0.75,
        use_council: bool = True,
    ):
        """
        Args:
            agent: AzureAgent instance
            llm: LocalLLM / SubprocessLLM / ApiLLM / HybridLLM
            extra_tools: Extra tool specs
            log_dir: Log directory
            save_states: Save states to disk
            semantic_threshold: Confidence threshold for semantic reasoning
            use_council: If True, use Multi-Agent Council for complex tasks
        """
        self.agent = agent
        self.llm = llm
        self.use_council = use_council

        # Core components
        self.reasoning = ReasoningEngine(llm=llm, extra_tools=extra_tools)
        self.planning = PlanningEngine(llm=llm)
        self.complexity = ComplexityEngine()
        self.review = ReviewEngine()
        self.adversarial_review = AdversarialReviewEngine()
        self.semantic = SemanticReasoner(llm=llm, threshold=semantic_threshold)

        # Upgrade 5: Council agents
        decomposer = IntentDecomposer(llm=llm)
        self.reasoner = ReasonerAgent(llm=llm, decomposer=decomposer) if use_council else None
        self.critic = CriticAgent(llm=llm) if use_council else None
        self.response_generator = ResponseGenerator(llm=llm) if use_council else None
        self.clarification_agent = ClarificationAgent(llm=llm)

        # v3: Swarm coordinator for uncertain classifications
        self.swarm = SwarmCoordinator(llm=llm) if (SwarmCoordinator is not None and use_council) else None

        # Priority 4B: Tool chain planner
        self.tool_chain_planner = ToolChainPlanner(llm=llm)

        # Deliverable 2: Tool-tier dispatcher (confirmation gate for destructive actions)
        from .tool_tier_dispatcher import ToolTierDispatcher
        self.tier_dispatcher = ToolTierDispatcher()

        # Deliverable 3: Operator mode router (detects objective-driven requests)
        from .operator_mode_router import OperatorModeRouter
        self.operator_router = OperatorModeRouter()

        # Deliverable 4: Audit engine (READ tools for operator mode)
        from .audit_engine import AuditEngine
        self.audit_engine = AuditEngine()

        # Upgrade 6: Reflection system (always active for learning)
        self.reflection_engine = ReflectionEngine()
        self.pattern_extractor = PatternExtractor(self.reflection_engine.memory)

        # Upgrade 7: Goal Persistence (proactive behavior)
        self.goal_manager = GoalManager()
        self.proactive_engine = ProactiveEngine(self.goal_manager)

        # Response cache (performance optimization)
        self.response_cache = ResponseCache(max_size=100, ttl_seconds=3600) if ResponseCache else None

        # Milestone 2: Memory & Context
        from .episodic_memory import EpisodicMemory
        from .server_knowledge import ServerKnowledgeBase
        from .user_profiles import UserProfileManager

        memory_dir = Path("logs/memory")
        memory_dir.mkdir(parents=True, exist_ok=True)
        self.user_profiles = UserProfileManager(memory_dir / "user_profiles.json")
        self.episodic_memory = EpisodicMemory(memory_dir / "episodes.json", llm=self.llm)
        self.server_knowledge = ServerKnowledgeBase(memory_dir / "server_knowledge.json")

        self.log_dir = Path(log_dir) if log_dir else None
        self.save_states = save_states

        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)

    def shutdown(self) -> None:
        """Release thread-pool and other resources."""
        self.reasoning.shutdown()

    async def process(
        self,
        message: str,
        user_name: str = "",
        is_directed: bool = True,
        is_dm: bool = False,
        is_mentioned: bool = False,
        params: dict | None = None,
        is_admin: bool = False,
        has_guild: bool = True,
        extra_context: str = "",
        conversation_history: list[dict] | None = None,
        user_memory: list[str] | None = None,
        server_memory: list[str] | None = None,
        prior_plans: list[str] | None = None,
        tool_state: list[str] | None = None,
        session_id: str = "",
        adversarial_review: bool = True,
        tracker=None,
    ) -> CognitiveState:
        """
        Process an incoming message through the Multi-Agent Council.

        If use_council=True and the message is a complex mode, the full
        council is convened: Router -> Reasoner -> Planner -> Executor -> Critic.

        If the message is simple (CHAT/QUESTION) or use_council=False,
        the standard pipeline is used for speed.
        """
        t_start = time.perf_counter()
        params = params or {}

        # Reset per-request flags
        self._skip_response_gen = False

        # === RESPONSE CACHE CHECK ===
        if self.response_cache:
            cache_context = {
                "is_dm": is_dm,
                "is_directed": is_directed,
            }
            cached_response = self.response_cache.get(
                message,
                user_id=user_name,
                context=cache_context,
            )
            if cached_response:
                logger.info(f"[cache] HIT for message: {message[:50]}")
                state = CognitiveState(raw_message=message, user_name=user_name)
                state.is_directed = is_directed
                state.response = cached_response
                state.response_final = True
                state.overall_confidence = 1.0
                state.complexity = Complexity.LOW
                state.risk = Risk.LOW
                state.phases.append(PhaseLog(
                    phase="CACHE_HIT",
                    duration_ms=(time.perf_counter() - t_start) * 1000,
                    result="returned cached response",
                    confidence=1.0,
                ))
                return state
            else:
                logger.debug(f"[cache] MISS for message: {message[:50]}")

        if tracker:
            tracker.emit("REASONING", "Deep reasoning...", subsystem="reasoning")
        # === MEMORY & CONTEXT INJECTION ===
        context_parts, context_summary = self._build_context(
            message, user_name, extra_context, conversation_history,
            user_memory, server_memory, prior_plans, tool_state,
        )

        # === GOAL COMMAND HANDLING ===
        goal_result = self._handle_goal_command(message, user_name, is_directed, t_start)
        if goal_result:
            return goal_result

        # === GOAL CONTEXT INJECTION ===
        context_summary = self._inject_goal_context(message, context_parts)

        # === REFLECTION RETRIEVAL ===
        context_summary = self._retrieve_reflections(message, context_parts)

        # === PHASE 1: ROUTER (Python, always runs) ===
        state, t_router = await self._router_phase(
            message, user_name, is_directed, is_dm, is_mentioned, params, session_id,
        )

        # Inject memory
        state.conversation_history = conversation_history or []
        state.user_memory = user_memory or []
        state.server_memory = server_memory or []
        state.prior_plans = prior_plans or []
        state.tool_state = tool_state or []
        state.context_summary = context_summary
        state.context = f"{state.context} | {extra_context}" if extra_context and state.context else extra_context or state.context

        state.phases.append(PhaseLog(
            phase="ROUTER",
            duration_ms=(time.perf_counter() - t_router) * 1000,
            result=f"modes={[m.value for m in state.modes]}, heuristic_conf={state.overall_confidence:.2f}",
        ))

        # === SWARM CONSENSUS (uncertain classifications) ===
        swarm_result = await self._swarm_consensus(state, message, user_name, t_start)
        if swarm_result:
            return swarm_result

        # === OPERATOR MODE DETECTION ===
        self._detect_operator_mode(state, message)

        # === DECISION: Run Council ===
        use_council_for_this = self.use_council and self.reasoner is not None

        if not use_council_for_this:
            return self._fast_premium_pipeline(
                state, message, params, is_admin, has_guild,
                adversarial_review, t_start, context_summary,
                user_name, extra_context,
            )

        # === MULTI-AGENT COUNCIL ===
        logger.info(f"[council] Convening council for: {message[:60]} | modes={[m.value for m in state.modes]}")

        # === DEEP ROLE MIRRORING ===
        role_ctx = getattr(state, 'role_context', None)
        if role_ctx is not None:
            role_summary = role_ctx.summary
            context_summary = f"{context_summary} | {role_summary}" if context_summary else role_summary
            state.context_summary = context_summary

        # === REACT LOOPS (Multi-step Reasoning) ===
        react_iters = 0
        max_react_iters = 3
        while react_iters < max_react_iters:
            # --- PHASE 2: REASONER AGENT (Qwen) ---
            analysis = self._phase_reasoning(
                state, message, user_name, is_directed, is_dm, is_mentioned,
                is_admin, has_guild, context_summary,
            )

            # === CLARIFICATION INTERCEPT ===
            if self._check_clarification(state):
                return state

            # --- PHASE 3: PLANNING (Python + optional Qwen) ---
            self._phase_planning(state, params, analysis)

            # --- PHASE 4: EXECUTOR (Python) ---
            # --- PHASE 5: CRITIC AGENT (Qwen) ---
            t_exec = time.perf_counter()
            t_critic = time.perf_counter()
            critique = None
            run_critic = self.critic and self.critic.should_review(state)

            if run_critic and state.selected_tools:
                import copy
                state_snapshot = copy.deepcopy(state)
                planned_response = state.response or ""
                critic_task = asyncio.create_task(
                    asyncio.to_thread(self.critic.review, state_snapshot, planned_response)
                )
                response, success = await self._execute(state, params, is_admin, has_guild)
                try:
                    critique = await asyncio.wait_for(critic_task, timeout=120)
                except Exception as e:
                    logger.error(f"[cognitive_pipeline] Critic parallel error: {e}")
                    critique = None
            else:
                response, success = await self._execute(state, params, is_admin, has_guild)
                if run_critic:
                    critique = self.critic.review(state, response)

            state.execution_result = response
            state.execution_success = success
            if not state.response:
                state.response = response

            state.phases.append(PhaseLog(
                phase="EXECUTE",
                duration_ms=(time.perf_counter() - t_exec) * 1000,
                result=f"{'success' if success else 'failed'} ({len(response)} chars)",
            ))

            # Log Critic phase
            self._phase_critic_review(state, critique, run_critic, t_critic)

            # ReAct Loop Check
            if self._check_react_loop(state, response, success, react_iters, max_react_iters):
                react_iters += 1
                continue
            break

        if tracker:
            tracker.emit("RESPONSE_GENERATION", "Formulating natural response...", subsystem="cognition")
        # --- PHASE 6: RESPONSE GENERATOR (Qwen) ---
        self._phase_response_generation(state, critique, t_start)

        return state
