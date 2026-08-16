"""
Azure Cognition Module

Formal cognitive pipeline for elite autonomous reasoning.
Every incoming message passes through 10 phases before a response is generated.

Phases:
  1. UNDERSTAND     — parse intent, true intent, desired outcome
  2. ANALYZE       — context, constraints, ambiguities, dependencies
  3. CLASSIFY      — assign one or more modes
  4. COMPLEXITY    — LOW / MEDIUM / HIGH / EXTREME
  5. THINKING_DEPTH — FAST / NORMAL / DEEP / MAXIMUM
  6. RISK          — LOW / MEDIUM / HIGH / CRITICAL
  7. TOOL_DECISION — direct / single tool / multiple tools / clarification
  8. PLAN          — step-by-step plan (for complex tasks)
  9. EXECUTE       — run the decided action
  10. REVIEW       — mandatory pre-output quality + safety check
"""

from __future__ import annotations

from .adversarial_review_engine import AdversarialReviewEngine
from .audit_engine import AuditEngine, AuditFinding, AuditReport
from .cognitive_pipeline import CognitivePipeline
from .cognitive_state import (
    CognitiveState,
    Complexity,
    ExecutionPlan,
    Mode,
    PhaseLog,
    PlanStep,
    Risk,
    ThinkingDepth,
    ToolDecision,
)
from .complexity_engine import ComplexityEngine
from .critic_agent import CriticAgent, CriticReview
from .goal_manager import GoalManager
from .goal_state import Blocker, Goal, GoalPriority, GoalStatus, Subgoal
from .intent_decomposer import IntentDecomposer, IntentDecomposition
from .mode_classifier import ModeClassifier
from .operator_mode_router import OperatorModeResult, OperatorModeRouter
from .pattern_extractor import PatternExtractor, PatternInsight
from .planning_engine import PlanningEngine
from .proactive_engine import ProactiveEngine
from .reasoner_agent import ReasonerAgent, ReasonerAnalysis
from .reasoning_engine import ReasoningEngine
from .reflection_engine import ReflectionEngine
from .reflection_memory import Reflection, ReflectionMemory
from .response_generator import GeneratedResponse, ResponseGenerator
from .review_engine import ReviewEngine, ReviewResult
from .risk_engine import RiskEngine
from .schema_validator import CRITIC_SCHEMA, REASONER_SCHEMA, SchemaValidator
from .semantic_reasoner import SemanticAnalysis, SemanticReasoner
from .thinking_depth_engine import ThinkingDepthEngine
from .tool_chain_planner import ToolChainPlan, ToolChainPlanner
from .tool_decision_engine import ToolDecisionEngine, ToolSpec
from .tool_tier_dispatcher import DispatchedResult, PendingConfirmation, ToolTier, ToolTierDispatcher
from .vision_router import ImageDescription, VisionRouter

__all__ = [
    # Core
    "CognitiveState",
    "CognitivePipeline",
    "ReasoningEngine",
    # Enums
    "Mode",
    "Complexity",
    "ThinkingDepth",
    "Risk",
    "ToolDecision",
    # Plan structures
    "ExecutionPlan",
    "PlanStep",
    "PhaseLog",
    # Engines
    "ModeClassifier",
    "ComplexityEngine",
    "ThinkingDepthEngine",
    "RiskEngine",
    "PlanningEngine",
    "ToolDecisionEngine",
    "ToolSpec",
    "ReviewEngine",
    "ReviewResult",
    # Semantic reasoning
    "SemanticReasoner",
    "SemanticAnalysis",
    # Adversarial review
    "AdversarialReviewEngine",
    # Multi-Agent Council (Upgrade 5)
    "ReasonerAgent",
    "ReasonerAnalysis",
    "CriticAgent",
    "CriticReview",
    # Upgrade 5.5
    "ResponseGenerator",
    "GeneratedResponse",
    "SchemaValidator",
    "REASONER_SCHEMA",
    "CRITIC_SCHEMA",
    # Upgrade 6: Reflection Memory
    "ReflectionMemory",
    "Reflection",
    "ReflectionEngine",
    "PatternExtractor",
    "PatternInsight",
    # Upgrade 7: Goal Persistence
    "Goal",
    "GoalStatus",
    "GoalPriority",
    "Subgoal",
    "Blocker",
    "GoalManager",
    "ProactiveEngine",
    # Priority 4A: Intent Decomposition
    "IntentDecomposer",
    "IntentDecomposition",
    # Priority 4B: Tool Chain Planning
    "ToolChainPlanner",
    "ToolChainPlan",
    # Deliverable 2: Tool-Tier Dispatcher
    "ToolTierDispatcher",
    "ToolTier",
    "PendingConfirmation",
    "DispatchedResult",
    # Deliverable 3: Operator Mode Router
    "OperatorModeRouter",
    "OperatorModeResult",
    # Deliverable 4: Audit Engine
    "AuditEngine",
    "AuditReport",
    "AuditFinding",
    # Deliverable 6: Vision Router
    "VisionRouter",
    "ImageDescription",
]
