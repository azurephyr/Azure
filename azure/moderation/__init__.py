"""
Azure Moderation Package

Autonomous Discord moderation engine for Azure.

Architecture:
  scanner     -> reads channels, collects messages
  classifier  -> scores messages (spam, scam, toxicity)
  policy      -> maps severity to actions
  actions     -> executes Discord moderation actions
  reporter    -> logs and reports actions taken
  engine      -> orchestrates the full pipeline

Modes:
  dry_run     -> classify only, no actions taken (default for testing)
  reactive    -> scan on every new message
  proactive   -> periodic channel scans + reactive
"""

from .actions import ActionExecutor, ActionResult
from .classifier import ClassificationResult, MessageClassifier, Severity
from .engine import ModerationEngine
from .monitor import ModerationMonitor
from .phase import ModerationPhase, action_allowed, can_transition, max_timeout_minutes
from .policy import ActionType, ModerationPolicy
from .reporter import ActionReport, ModerationReporter
from .scanner import CachedMessage, ChannelScanner

__all__ = [
    "MessageClassifier",
    "ClassificationResult",
    "Severity",
    "ModerationPolicy",
    "ActionType",
    "ActionExecutor",
    "ActionResult",
    "ChannelScanner",
    "CachedMessage",
    "ModerationReporter",
    "ActionReport",
    "ModerationEngine",
    "ModerationMonitor",
    "ModerationPhase",
    "action_allowed",
    "max_timeout_minutes",
    "can_transition",
]
