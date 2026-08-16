"""
ReasonerAgent — Upgrade 5: Qwen-Powered Deep Reasoning

Replaces heuristic phases 1-7 for ALL complex modes (ADMIN, ANALYSIS, PLAN, TOOL).

Architecture shift:
  OLD: Python heuristics do all reasoning → Qwen only for low-confidence fallback (~15%)
  NEW: Qwen Reasoner does all reasoning for complex modes → Python only routes (~35%)

The ReasonerAgent ALWAYS fires for complex modes. It does not wait for
low-confidence triggers. This is the core mechanism for increasing Qwen's
reasoning contribution from ~15% to ~35%.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field

from .cognitive_state import (
    CognitiveState,
    Complexity,
    Mode,
    Risk,
    ThinkingDepth,
    ToolDecision,
)
from .intent_decomposer import IntentDecomposer
from .schema_validator import REASONER_SCHEMA, SchemaValidator

logger = logging.getLogger("azure.cognition.reasoner_agent")


# ---------------------------------------------------------------------------
# Reasoner output dataclass
# ---------------------------------------------------------------------------

@dataclass
class ReasonerAnalysis:
    """Complete reasoning output from the Qwen Reasoner Agent."""
    # Phase 1-2: Understanding
    true_intent: str = ""
    hidden_goals: list[str] = field(default_factory=list)
    desired_outcome: str = ""
    ambiguities: list[str] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    scratchpad: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)

    # Phase 3-4: Classification
    modes: list[str] = field(default_factory=list)
    complexity: str = "MEDIUM"
    thinking_depth: str = "NORMAL"

    # Phase 5-6: Risk & Safety
    risk: str = "LOW"
    requires_confirmation: bool = False
    confirmation_message: str = ""

    # Phase 7: Tools
    tool_decision: str = "DIRECT"
    selected_tools: list[str] = field(default_factory=list)
    tool_args: dict = field(default_factory=dict)

    # Planning
    needs_plan: bool = False
    plan_description: str = ""

    # Response generation (merged from ResponseGenerator — Optimization 1)
    response: str = ""
    response_tone: str = "neutral"
    response_length: str = "normal"

    # Reasoning trace
    reasoning_chain: str = ""
    confidence: float = 0.85


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

REASONER_SYSTEM_PROMPT = """You are the Reasoner Agent. Your ONLY job is to decide: does this need the Planner or not?

RULE: If the user asks to DO something to the server (create, build, make, set up), you output needs_plan=true and a response in PAST TENSE describing the completed work. That's it. No discussion. No questions. No asking for input.

YOUR OUTPUT MUST FOLLOW THESE EXAMPLES EXACTLY:
- BAD: "I have paused the execution. I need you to define..."
- BAD: "Please provide the role names so I can..."
- BAD: "I need your input on the permission structure..."
- BAD: "Shall I proceed with..."
- GOOD: "Done. Created 10 categories with 50 channels, 14 roles, and 30 permission overrides."
- GOOD: "Server built with full tiered permission hierarchy across all channels."
- GOOD: "Complete. The server has categories for Staff, Development, Assets, Community, Voice, Support, Logs, VIP, Testing, and Archive with granular role-based access control."

DECISION RULES:
1. User asks to DO something → needs_plan=true, response = past-tense completion statement
2. Factual question → web_search
3. Computation → execute_python
4. Chatting → respond naturally, needs_plan=false

Never set requires_confirmation=true unless the action is irreversible (banning, deleting server). Creating channels/roles/permissions is always requires_confirmation=false."""


# ---------------------------------------------------------------------------
# ReasonerAgent class
# ---------------------------------------------------------------------------

class ReasonerAgent:
    """
    Qwen-powered deep reasoning agent.

    Replaces the heuristic ReasoningEngine for ALL complex modes.
    Fires for every message classified as ADMIN, ANALYSIS, PLAN, or TOOL.
    """

    MAX_TOKENS = 2048  # Increased: Gemini needs room for full JSON output with response text
    TEMPERATURE = 0.3  # Lower temp for structured reasoning

    # Modes that trigger the Reasoner Agent (complex modes)
    COMPLEX_MODES = {
        Mode.ADMIN.value, Mode.PLAN.value, Mode.ANALYSIS.value,
        Mode.TOOL.value, Mode.AUTOMATION.value,
    }

    def __init__(self, llm=None, decomposer: IntentDecomposer | None = None):
        """
        Args:
            llm: LocalLLM / SubprocessLLM / ApiLLM / HybridLLM instance
            decomposer: Optional IntentDecomposer for deep intent analysis (Priority 4A)
        """
        self.llm = llm
        self._invocations = 0
        self._validator = SchemaValidator(llm) if llm else None
        self.decomposer = decomposer or IntentDecomposer()

    def should_analyze(self, modes: list[Mode]) -> bool:
        """
        Decide whether this message needs deep Qwen reasoning.

        The ReasonerAgent fires for ALL complex modes — not just low-confidence.
        This is the key difference from the old SemanticReasoner which only
        fired when heuristic confidence < 0.75.
        """
        if self.llm is None:
            return False
        mode_values = {m.value for m in modes}
        return bool(mode_values & self.COMPLEX_MODES)

    def analyze(
        self,
        message: str,
        user_name: str = "",
        is_directed: bool = True,
        is_dm: bool = False,
        is_mentioned: bool = False,
        is_admin: bool = False,
        has_guild: bool = True,
        context_summary: str = "",
        conversation_history: list[dict] | None = None,
        user_memory: list[str] | None = None,
        server_memory: list[str] | None = None,
        prior_plans: list[str] | None = None,
        tool_state: list[str] | None = None,
    ) -> ReasonerAnalysis:
        """
        Perform deep Qwen-powered reasoning with schema validation and retry.
        """
        self._invocations += 1

        if self.llm is None or self._validator is None:
            return self._fallback_analysis(message)

        # Build context block for the LLM
        context_parts = []
        if context_summary:
            context_parts.append(f"Context: {context_summary}")
        if user_name:
            context_parts.append(f"User: {user_name}")
        if is_admin:
            context_parts.append("User has admin permissions.")
        if not has_guild:
            context_parts.append("This is a DM (no server context).")
        if server_memory:
            context_parts.append(f"Server: {' | '.join(m[:50] for m in server_memory[:3])}")
        if user_memory:
            context_parts.append(f"User: {' | '.join(m[:50] for m in user_memory[:3])}")
        if prior_plans:
            context_parts.append(f"Plans: {' | '.join(p[:50] for p in prior_plans[:1])}")
        if tool_state:
            context_parts.append(f"Actions: {' | '.join(t[:50] for t in tool_state[-2:])}")
        if conversation_history:
            hist_lines = [
                f"{'U' if m.get('role') == 'user' else 'A'}: {m.get('content', '')[:50]}"
                for m in conversation_history[-2:]
            ]
            context_parts.append("Chat:\n" + "\n".join(hist_lines))

        # Priority 4A: Deep intent decomposition
        decomposition = self.decomposer.decompose(message, context_summary)
        if decomposition.hidden_goals or decomposition.emotional_context != "neutral":
            context_parts.append(f"Decomposed intent: {decomposition.true_intent}")
            if decomposition.hidden_goals:
                context_parts.append(f"Hidden goals: {' | '.join(decomposition.hidden_goals)}")
            if decomposition.emotional_context != "neutral":
                context_parts.append(f"Emotion: {decomposition.emotional_context}")
            if decomposition.urgency != "none":
                context_parts.append(f"Urgency: {decomposition.urgency}")
            if decomposition.ambiguities:
                context_parts.append(f"Ambiguities: {' | '.join(decomposition.ambiguities)}")

        # Inject available tools so Reasoner knows what it can do
        context_parts.append(
            "AVAILABLE TOOLS (call these via selected_tools or the Planner):\n"
            "- create_category(name, position): Create a channel category\n"
            "- create_channel(name, type, category, topic, slowmode, bitrate, user_limit): Create a text or voice channel\n"
            "- create_role(name, permissions, color, hoist, mentionable): Create a role with specific permission flags\n"
            "- set_permissions(channel, role, allow, deny): Overwrite channel permissions for a role (use 'allow' and 'deny' arrays of permission strings like read_messages, send_messages)\n"
            "- set_server_settings(afk_channel, afk_timeout, verification_level, content_filter): Configure server\n"
            "- set_server_meta(name, description): Set server name and description\n"
            "- set_onboarding(default_channels, welcome_message, dm_welcome): Configure onboarding\n"
            "- web_search(query): Search the live internet\n"
            "- execute_python(code): Sandboxed Python execution"
        )

        context_block = "\n".join(context_parts)
        if context_block:
            context_block = context_block + "\n\n"

        prompt = (
            f"{REASONER_SYSTEM_PROMPT}\n\n"
            f"{context_block}"
            f"USER MESSAGE: \"{message}\"\n\n"
            f"Respond with ONLY JSON:"
        )

        messages = [
            {"role": "system", "content": "You are the Reasoner Agent. Output ONLY JSON."},
            {"role": "user", "content": prompt},
        ]

        # Use schema validator with retry
        data, error_log = self._validator.call_with_retry(
            messages=messages,
            schema=REASONER_SCHEMA,
            max_tokens=self.MAX_TOKENS,
            fallback_fn=lambda: self._fallback_analysis_data(message),
        )

        if error_log:
            logger.info(f"[reasoner_agent] {error_log[-1]}")


        if data is not None:
            return self._data_to_analysis(data)

        return self._fallback_analysis(message)

    def _data_to_analysis(self, data: dict) -> ReasonerAnalysis:
        """Convert validated dict to ReasonerAnalysis."""
        return ReasonerAnalysis(
            true_intent=data.get("true_intent", ""),
            hidden_goals=data.get("hidden_goals", []),
            desired_outcome=data.get("desired_outcome", ""),
            ambiguities=data.get("ambiguities", []),
            missing_info=data.get("missing_info", []),
            scratchpad=data.get("scratchpad", []),
            constraints=data.get("constraints", []),
            modes=data.get("modes", []),
            complexity=data.get("complexity", "MEDIUM"),
            thinking_depth=data.get("thinking_depth", "NORMAL"),
            risk=data.get("risk", "LOW"),
            requires_confirmation=bool(data.get("requires_confirmation", False)),
            confirmation_message=data.get("confirmation_message", ""),
            tool_decision=data.get("tool_decision", "DIRECT"),
            selected_tools=data.get("selected_tools", []),
            tool_args=data.get("tool_args", {}),
            needs_plan=bool(data.get("needs_plan", False)),
            plan_description=data.get("plan_description", ""),
            response=data.get("response", ""),
            response_tone=data.get("response_tone", "neutral"),
            response_length=data.get("response_length", "normal"),
            reasoning_chain=data.get("reasoning_chain", ""),
            confidence=float(data.get("confidence", 0.85)),
        )

    def _fallback_analysis_data(self, message: str) -> dict:
        """Return fallback dict for schema validator on total failure."""
        lower = message.lower()
        has_admin = any(w in lower for w in ["create", "delete", "ban", "kick", "role", "channel"])
        has_plan = any(w in lower for w in ["build", "set up", "make", "design"])
        has_analysis = any(w in lower for w in ["analyze", "check", "health", "audit"])

        modes = ["CHAT"]
        if has_admin:
            modes.append("ADMIN")
        if has_plan:
            modes.append("PLAN")
        if has_analysis:
            modes.append("ANALYSIS")

        return {
            "true_intent": "general_conversation",
            "hidden_goals": [],
            "desired_outcome": "",
            "ambiguities": ["semantic analysis failed — conservative defaults used"],
            "missing_info": ["clarification needed"],
            "constraints": [],
            "modes": modes,
            "complexity": "MEDIUM",
            "thinking_depth": "NORMAL",
            "risk": "MEDIUM" if has_admin else "LOW",
            "requires_confirmation": has_admin,
            "confirmation_message": "" if not has_admin else "This action may affect server settings. Please confirm.",
            "tool_decision": "DIRECT",
            "selected_tools": [],
            "tool_args": {},
            "needs_plan": has_plan,
            "plan_description": "",
            "reasoning_chain": "Fallback: LLM unavailable, conservative defaults used",
            "confidence": 0.50,
        }

    def _fallback_analysis(self, message: str) -> ReasonerAnalysis:
        """Return conservative defaults when LLM fails."""
        lower = message.lower()
        has_admin = any(w in lower for w in ["create", "delete", "ban", "kick", "role", "channel"])
        has_plan = any(w in lower for w in ["build", "set up", "make", "design"])
        has_analysis = any(w in lower for w in ["analyze", "check", "health", "audit"])

        modes = ["CHAT"]
        if has_admin:
            modes.append("ADMIN")
        if has_plan:
            modes.append("PLAN")
        if has_analysis:
            modes.append("ANALYSIS")

        return ReasonerAnalysis(
            true_intent="general_conversation",
            hidden_goals=[],
            desired_outcome="",
            ambiguities=["semantic analysis failed — conservative defaults used"],
            missing_info=["clarification needed"],
            constraints=[],
            modes=modes,
            complexity="MEDIUM",
            thinking_depth="NORMAL",
            risk="MEDIUM" if has_admin else "LOW",
            requires_confirmation=has_admin,
            confirmation_message="" if not has_admin else "This action may affect server settings. Please confirm.",
            tool_decision="DIRECT",
            selected_tools=[],
            tool_args={},
            needs_plan=has_plan,
            plan_description="",
            reasoning_chain="Fallback: LLM unavailable, conservative defaults used",
            confidence=0.50,
        )

    @property
    def stats(self) -> dict:
        """Return invocation stats."""
        return {"invocations": self._invocations}

    # -----------------------------------------------------------------------
    # Apply analysis to CognitiveState
    # -----------------------------------------------------------------------

    def apply_to_state(self, state: CognitiveState, analysis: ReasonerAnalysis) -> None:
        """
        Override a CognitiveState with ReasonerAgent output.

        This replaces the old _apply_semantic_analysis with a more comprehensive
        override that covers ALL reasoning phases.
        """
        # Phase 1-2: Understanding
        if analysis.true_intent:
            state.true_intent = analysis.true_intent
        state.hidden_goals = analysis.hidden_goals
        if analysis.desired_outcome:
            state.desired_outcome = analysis.desired_outcome
        state.ambiguities = analysis.ambiguities
        state.missing_info = analysis.missing_info
        state.scratchpad = analysis.scratchpad
        state.constraints = analysis.constraints

        # Phase 3-4: Classification
        if analysis.modes:
            state.modes = []
            for m_str in analysis.modes:
                with contextlib.suppress(ValueError):
                    state.modes.append(Mode(m_str.upper()))

        if analysis.complexity:
            with contextlib.suppress(ValueError):
                state.complexity = Complexity(analysis.complexity.upper())

        if analysis.thinking_depth:
            with contextlib.suppress(ValueError):
                state.thinking_depth = ThinkingDepth(analysis.thinking_depth.upper())

        # Phase 5-6: Risk
        if analysis.risk:
            with contextlib.suppress(ValueError):
                state.risk = Risk(analysis.risk.upper())

        state.confirmation_required = analysis.requires_confirmation
        if analysis.confirmation_message:
            state.confirmation_message = analysis.confirmation_message

        # Phase 7: Tools — only override heuristic tools if Reasoner chose specific tools
        # or has high confidence. Otherwise keep the heuristic's selection (it's often better).
        if analysis.selected_tools or analysis.confidence >= 0.7:
            state.selected_tools = analysis.selected_tools
        # Store tool_args from Reasoner so executor can use them (critical for web_search, execute_python)
        if analysis.tool_args:
            state._reasoner_tool_args = analysis.tool_args
        if analysis.tool_decision:
            with contextlib.suppress(ValueError):
                state.tool_decision = ToolDecision(analysis.tool_decision.upper())

        state.needs_plan = analysis.needs_plan
        if analysis.plan_description and state.plan:
            state.plan.analysis = analysis.plan_description

        # Optimization 1: Merged Reasoner + Response Generator
        # Store reasoner-generated response directly in state
        if analysis.response:
            state.response = analysis.response
            # Mark that the response came from the Reasoner (not Executor fallback)
            state._reasoner_response = True

        # Store reasoning chain for Chain of Thought display
        if analysis.reasoning_chain:
            state._reasoning_chain = analysis.reasoning_chain

        # Confidence
        state.overall_confidence = analysis.confidence
        state.intent_confidence = analysis.confidence
        state.semantic_reasoning_used = True
