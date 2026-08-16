"""
Recovery Learner

Learns from successful recoveries to improve future recovery attempts.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .analyzer import RootCause
from .classifier import FailureType
from .strategy import RecoveryStrategy

logger = logging.getLogger("azure.recovery.learner")


@dataclass
class RecoveryPattern:
    """A learned recovery pattern."""
    failure_type: str
    root_cause_category: str
    strategy_name: str
    success_count: int
    total_attempts: int

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_attempts == 0:
            return 0.0
        return self.success_count / self.total_attempts

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            **asdict(self),
            "success_rate": self.success_rate
        }


class RecoveryLearner:
    """Learns from recovery attempts to improve future performance."""

    def __init__(self, storage_path: Path | None = None):
        self.storage_path = storage_path or Path("logs/recovery_patterns.json")
        self.patterns: dict[str, RecoveryPattern] = {}
        self._load_patterns()

    def record_success(
        self,
        failure_type: FailureType,
        root_causes: list[RootCause],
        strategy: RecoveryStrategy
    ):
        """
        Record a successful recovery.

        Args:
            failure_type: Type of failure that was recovered from
            root_causes: Root causes that were identified
            strategy: Strategy that succeeded
        """
        for root_cause in root_causes:
            key = self._make_key(failure_type, root_cause.category, strategy.name)

            if key in self.patterns:
                pattern = self.patterns[key]
                pattern.success_count += 1
                pattern.total_attempts += 1
            else:
                self.patterns[key] = RecoveryPattern(
                    failure_type=failure_type.name,
                    root_cause_category=root_cause.category,
                    strategy_name=strategy.name,
                    success_count=1,
                    total_attempts=1
                )

            logger.info(f"[Learner] Recorded success: {key}")

        self._save_patterns()

    def record_failure(
        self,
        failure_type: FailureType,
        root_causes: list[RootCause],
        strategy: RecoveryStrategy
    ):
        """
        Record a failed recovery attempt.

        Args:
            failure_type: Type of failure
            root_causes: Root causes that were identified
            strategy: Strategy that failed
        """
        for root_cause in root_causes:
            key = self._make_key(failure_type, root_cause.category, strategy.name)

            if key in self.patterns:
                pattern = self.patterns[key]
                pattern.total_attempts += 1
            else:
                self.patterns[key] = RecoveryPattern(
                    failure_type=failure_type.name,
                    root_cause_category=root_cause.category,
                    strategy_name=strategy.name,
                    success_count=0,
                    total_attempts=1
                )

            logger.info(f"[Learner] Recorded failure: {key}")

        self._save_patterns()

    def get_best_strategies(
        self,
        failure_type: FailureType,
        root_cause_category: str
    ) -> list[str]:
        """
        Get best strategies for a failure type and root cause.

        Args:
            failure_type: Type of failure
            root_cause_category: Root cause category

        Returns:
            List of strategy names, sorted by success rate
        """
        relevant_patterns = [
            pattern for key, pattern in self.patterns.items()
            if pattern.failure_type == failure_type.name
            and pattern.root_cause_category == root_cause_category
        ]

        # Sort by success rate
        relevant_patterns.sort(key=lambda p: p.success_rate, reverse=True)

        return [p.strategy_name for p in relevant_patterns]

    def get_insights(self) -> dict[str, Any]:
        """Get insights from learned patterns."""
        if not self.patterns:
            return {"total_patterns": 0, "insights": "No recovery patterns learned yet"}

        # Calculate statistics
        total_successes = sum(p.success_count for p in self.patterns.values())
        total_attempts = sum(p.total_attempts for p in self.patterns.values())
        overall_success_rate = total_successes / total_attempts if total_attempts > 0 else 0

        # Find best strategies
        best_strategies = sorted(
            self.patterns.values(),
            key=lambda p: (p.success_rate, p.success_count),
            reverse=True
        )[:5]

        # Find worst strategies
        worst_strategies = sorted(
            [p for p in self.patterns.values() if p.total_attempts >= 2],
            key=lambda p: p.success_rate
        )[:5]

        return {
            "total_patterns": len(self.patterns),
            "total_successes": total_successes,
            "total_attempts": total_attempts,
            "overall_success_rate": overall_success_rate,
            "best_strategies": [p.to_dict() for p in best_strategies],
            "worst_strategies": [p.to_dict() for p in worst_strategies],
        }

    def _make_key(self, failure_type: FailureType, root_cause_category: str, strategy_name: str) -> str:
        """Make a unique key for a pattern."""
        return f"{failure_type.name}::{root_cause_category}::{strategy_name}"

    def _load_patterns(self):
        """Load patterns from storage."""
        if not self.storage_path.exists():
            return

        try:
            with open(self.storage_path) as f:
                data = json.load(f)

            for key, pattern_dict in data.items():
                self.patterns[key] = RecoveryPattern(**pattern_dict)

            logger.info(f"[Learner] Loaded {len(self.patterns)} recovery patterns")
        except Exception as e:
            logger.warning(f"[Learner] Failed to load patterns: {e}")

    def _save_patterns(self):
        """Save patterns to storage."""
        try:
            # Create directory if needed
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)

            # Convert patterns to dict
            data = {
                key: {
                    "failure_type": pattern.failure_type,
                    "root_cause_category": pattern.root_cause_category,
                    "strategy_name": pattern.strategy_name,
                    "success_count": pattern.success_count,
                    "total_attempts": pattern.total_attempts,
                }
                for key, pattern in self.patterns.items()
            }

            tmp = self.storage_path.with_suffix('.tmp')
            with open(tmp, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.storage_path)

        except Exception as e:
            logger.warning(f"[Learner] Failed to save patterns: {e}")
