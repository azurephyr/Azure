"""
Adaptive Goal Recovery Engine (AGRE) for Azure

Makes Azure resilient, persistent, and goal-oriented by:
- Detecting and classifying failures
- Performing root-cause analysis
- Generating recovery hypotheses
- Attempting recovery strategies
- Learning from successful recoveries
"""

from .analyzer import RootCauseAnalyzer
from .classifier import FailureClassifier
from .engine import AdaptiveGoalRecoveryEngine
from .executor import RecoveryExecutor
from .learner import RecoveryLearner
from .strategy import RecoveryStrategyGenerator

__all__ = [
    "AdaptiveGoalRecoveryEngine",
    "FailureClassifier",
    "RootCauseAnalyzer",
    "RecoveryStrategyGenerator",
    "RecoveryExecutor",
    "RecoveryLearner",
]
