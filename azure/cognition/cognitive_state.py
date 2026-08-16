"""
CognitiveState — the internal state object produced by the 10-phase pipeline.

This dataclass is the canonical record of how Azure reasoned about a message.
It flows through every phase and is stored in logs for debugging.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .role_context import RoleContext


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Mode(StrEnum):
    """One or more operational modes a message can belong to."""
    CHAT       = "CHAT"        # Normal conversation, casual chat
    QUESTION   = "QUESTION"    # User is asking a question
    MEMORY     = "MEMORY"      # Involves memory / recall operations
    TOOL       = "TOOL"        # Tool execution required
    ADMIN      = "ADMIN"       # Server administration / management
    PLAN       = "PLAN"        # Planning, building, structuring
    ANALYSIS   = "ANALYSIS"    # Analysis, health check, audit
    AUTOMATION = "AUTOMATION"  # Automation / scheduled / batch operations


class Complexity(StrEnum):
    LOW      = "LOW"
    MEDIUM   = "MEDIUM"
    HIGH     = "HIGH"
    EXTREME  = "EXTREME"


class ThinkingDepth(StrEnum):
    FAST     = "FAST"     # Instant response, no LLM needed
    NORMAL   = "NORMAL"   # Standard LLM call
    DEEP     = "DEEP"     # Longer inference, more context
    MAXIMUM  = "MAXIMUM"  # Full reasoning, multiple passes


class Risk(StrEnum):
    LOW      = "LOW"
    MEDIUM   = "MEDIUM"
    HIGH     = "HIGH"
    CRITICAL = "CRITICAL"


class ToolDecision(StrEnum):
    DIRECT         = "DIRECT"          # LLM direct response, no tools
    SINGLE_TOOL    = "SINGLE_TOOL"     # One tool call
    MULTIPLE_TOOLS = "MULTIPLE_TOOLS"  # Multiple tool calls
    CLARIFICATION  = "CLARIFICATION"   # Not enough info, ask user


# ---------------------------------------------------------------------------
# Plan dataclass
# ---------------------------------------------------------------------------

@dataclass
class PlanStep:
    order: int
    action: str
    description: str
    tool: str | None = None
    args: dict = field(default_factory=dict)
    risk: str = "LOW"
    can_fail: bool = False
    fallback: str | None = None


@dataclass
class ExecutionPlan:
    """Structured plan for complex tasks."""
    objective: str = ""
    constraints: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    execution_order: list[PlanStep] = field(default_factory=list)
    fallback_paths: list[str] = field(default_factory=list)
    analysis: str = ""
    tool_chain: object | None = None
    requires_confirmation: bool = False
    notes: str = ""


# ---------------------------------------------------------------------------
# Phase log entry
# ---------------------------------------------------------------------------

@dataclass
class PhaseLog:
    phase: str
    duration_ms: float
    result: str
    notes: str = ""
    confidence: float = 0.0  # Phase-level confidence score


# ---------------------------------------------------------------------------
# CognitiveState
# ---------------------------------------------------------------------------

@dataclass
class CognitiveState:
    """
    The internal cognitive state object — produced by the 10-phase pipeline.

    This is the Azure Core Directive's internal reasoning format made concrete.
    Stored in logs for every message processed.
    """
    # Metadata
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)
    is_directed: bool = False  # Was message directed at the bot?
    is_dm: bool = False        # Is this a direct message?

    # Phase 1 — UNDERSTAND
    raw_message: str = ""
    user_name: str = ""
    user_intent: str = ""         # What the user literally said
    true_intent: str = ""         # What the user actually wants (inferred)
    desired_outcome: str = ""
    hidden_goals: list[str] = field(default_factory=list)

    # Phase 2 — ANALYZE
    context: str = ""
    scratchpad: list[str] = field(default_factory=list) # Working memory mid-reasoning
    constraints: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)

    # Phase 3 — CLASSIFY
    modes: list[Mode] = field(default_factory=list)  # Multi-label
    mode_confidence: float = 0.0       # Confidence in mode classification

    # Confidence scores — every phase outputs one
    # Upgrade 1: Confidence Scoring
    intent_confidence: float = 0.0      # Phase 1: confidence in true_intent
    analyze_confidence: float = 0.0     # Phase 2: confidence in analysis
    complexity_confidence: float = 0.0  # Phase 4: confidence in complexity
    risk_confidence: float = 0.0        # Phase 6: confidence in risk classification
    tool_confidence: float = 0.0        # Phase 7: confidence in tool decision
    semantic_reasoning_used: bool = False  # Phase 1-7: was Qwen fallback triggered?
    overall_confidence: float = 0.0    # Weighted aggregate confidence

    # Upgrade 3: Memory Injection
    # Memory context is injected BEFORE Phase 1
    conversation_history: list[dict] = field(default_factory=list)  # Recent turns
    user_memory: list[str] = field(default_factory=list)            # Facts about this user
    server_memory: list[str] = field(default_factory=list)         # Facts about this server
    prior_plans: list[str] = field(default_factory=list)            # Recent plans made
    tool_state: list[str] = field(default_factory=list)            # Recent tool calls
    context_summary: str = ""              # Compact context string for LLM

    # Phase 4 — COMPLEXITY
    complexity: Complexity = Complexity.LOW

    # Phase 5 — THINKING_DEPTH
    thinking_depth: ThinkingDepth = ThinkingDepth.NORMAL

    # Phase 6 — RISK
    risk: Risk = Risk.LOW
    risk_flags: list[str] = field(default_factory=list)
    confirmation_required: bool = False
    confirmation_message: str = ""

    # Phase 7 — TOOL_DECISION
    tool_decision: ToolDecision = ToolDecision.DIRECT
    selected_tools: list[str] = field(default_factory=list)

    # Phase 8 — PLAN
    plan: ExecutionPlan = field(default_factory=ExecutionPlan)

    # Phase 9 — EXECUTE
    execution_result: str = ""
    execution_success: bool = True

    # Phase 10 — REVIEW
    review_passes: list[bool] = field(default_factory=list)
    review_issues: list[str] = field(default_factory=list)
    review_notes: str = ""
    review_passed: bool = True  # Aggregate review result (True = all checks passed)

    # Security context — live Discord role hierarchy
    # Set by the bot before process() is called; flows through all phases.
    role_context: RoleContext | None = None

    # Undeclared attribute holders (must precede final output)
    _reasoner_tool_args: dict = field(default_factory=dict)
    _reasoner_response: bool = False
    _reasoning_chain: str = ""

    # Final output
    response: str = ""
    response_final: bool = False

    # Phase timing
    phases: list[PhaseLog] = field(default_factory=list)

    # LLM prompt hints (set based on thinking_depth)
    prompt_depth: str = "normal"   # normal | detailed | comprehensive | exhaustive
    token_budget: int = 256        # max_tokens for LLM call
    reasoning_verbosity: str = "normal"  # brief | normal | verbose | thorough

    def to_json(self) -> str:
        """Serialize to JSON for logging.

        Defensive: asdict() will fail on non-dataclass attributes such as
        `role_context` (an Optional live-object slot). We coerce unknown /
        non-serializable fields to strings before json.dumps so logging
        never crashes mid-pipeline.
        """
        d = asdict(self)
        d["modes"] = [m.value for m in self.modes]
        d["complexity"] = self.complexity.value
        d["thinking_depth"] = self.thinking_depth.value
        d["risk"] = self.risk.value
        d["tool_decision"] = self.tool_decision.value
        d["plan"] = asdict(self.plan)
        d["phases"] = [asdict(p) for p in self.phases]
        d["_reasoner_tool_args"] = self._reasoner_tool_args
        d["_reasoner_response"] = self._reasoner_response
        d["_reasoning_chain"] = self._reasoning_chain

        # Strict fallback: at this point role_context should already be a
        # safe value (None in practice). If a future caller assigns a Discord
        # object, coerce it to a string description so JSON.dump succeeds.
        if d.get("role_context") is not None and not isinstance(d["role_context"], (str, int, float, bool, list, dict)):
            try:
                d["role_context"] = str(d["role_context"])[:500]
            except Exception:
                d["role_context"] = None

        return json.dumps(d, indent=2, ensure_ascii=False)

    def confidence_summary(self) -> dict:
        """Return a summary dict of all confidence scores."""
        return {
            "overall":        round(self.overall_confidence, 3),
            "intent":         round(self.intent_confidence, 3),
            "analyze":        round(self.analyze_confidence, 3),
            "mode":           round(self.mode_confidence, 3),
            "complexity":     round(self.complexity_confidence, 3),
            "risk":           round(self.risk_confidence, 3),
            "tool":           round(self.tool_confidence, 3),
            "semantic_used":  self.semantic_reasoning_used,
        }

    def confidence_is_low(self, threshold: float = 0.75) -> bool:
        """Return True if overall confidence is below threshold."""
        return self.overall_confidence < threshold

    @property
    def needs_llm(self) -> bool:
        """Does this message require an LLM call?"""
        return self.tool_decision in (ToolDecision.DIRECT, ToolDecision.SINGLE_TOOL, ToolDecision.MULTIPLE_TOOLS)

    @property
    def needs_confirmation(self) -> bool:
        """Does this action require user confirmation?"""
        return self.confirmation_required or self.risk == Risk.CRITICAL

    def phase_time_total(self) -> float:
        """Total time spent across all phases (ms)."""
        return sum(p.duration_ms for p in self.phases)

    def phase_summary(self) -> str:
        """Human-readable phase summary."""
        lines = []
        for p in self.phases:
            lines.append(f"  [{p.phase}] {p.duration_ms:.1f}ms → {p.result}")
        return "\n".join(lines) if lines else "  (no phases completed)"
