"""
AI-Powered Moderation System for Azure Discord Bot - Schema v1

This package provides context-aware AI moderation using LLM models.

SCHEMA V1 MIGRATION (2026-07-09):
- ✅ Separated Analysis / Policy / Action layers
- ✅ Eliminated contradictory fields (is_toxic + context_safe)
- ✅ Type-safe enums (no string parsing)
- ✅ PolicyEngine for decision logic
- ✅ Moderation Schema v1.0.0 (frozen)
- ✅ Production-ready architecture
"""

# Core base class
from .base_ai import BaseAI, InputValidator, JSONParser, PromptBuilder

# Schema v1 data models
from .models import (
    ConfidenceLevel,
    Intensity,
    Intent,
    JoinEvent,
    # Analysis types
    MessageAnalysis,
    # Enums
    MessageType,
    PolicyAction,
    # Policy types
    PolicyDecision,
    PolicyReason,
    RaidAnalysis,
    ScamAnalysis,
    SpamAnalysis,
    Specificity,
    Target,
)

# Main engine
from .moderation_engine import (
    AIModerationEngine,
    ModerationResult,
)

# PolicyEngine
from .policy_engine import (
    LENIENT_POLICY,
    MODERATE_POLICY,
    STRICT_POLICY,
    Condition,
    Policy,
    PolicyEngine,
    Rule,
    ServerConfig,
    get_policy_by_name,
)
from .raid_ai import RaidAI
from .scam_ai import ScamAI
from .spam_ai import SpamAI

# AI components (return Schema v1 types)
from .toxicity_ai import ToxicityAI

__all__ = [
    # Base infrastructure
    "BaseAI",
    "InputValidator",
    "PromptBuilder",
    "JSONParser",

    # Main engine
    "AIModerationEngine",
    "ModerationResult",

    # AI components
    "ToxicityAI",
    "SpamAI",
    "ScamAI",
    "RaidAI",

    # Schema v1 - Analysis types
    "MessageAnalysis",
    "SpamAnalysis",
    "ScamAnalysis",
    "RaidAnalysis",
    "JoinEvent",

    # Schema v1 - Enums
    "MessageType",
    "Target",
    "Intent",
    "Intensity",
    "Specificity",
    "ConfidenceLevel",

    # Schema v1 - Policy types
    "PolicyDecision",
    "PolicyAction",
    "PolicyReason",

    # PolicyEngine
    "PolicyEngine",
    "ServerConfig",
    "Policy",
    "Rule",
    "Condition",
    "STRICT_POLICY",
    "MODERATE_POLICY",
    "LENIENT_POLICY",
    "get_policy_by_name",
]

__version__ = "3.0.0"  # Major version - Schema v1 migration complete

