"""
Adaptive Goal Recovery Engine (AGRE)

Main orchestrator for goal-oriented recovery from failures.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .analyzer import RootCause, RootCauseAnalyzer
from .classifier import FailureClassifier, FailureType
from .executor import RecoveryExecutor, RecoveryResult
from .learner import RecoveryLearner
from .strategy import RecoveryStrategy, RecoveryStrategyGenerator

logger = logging.getLogger("azure.recovery")


@dataclass
class RecoveryConfig:
    """Configuration for recovery engine."""
    max_retries: int = 3
    max_recovery_attempts_per_retry: int = 5
    learn_from_recoveries: bool = True
    require_approval_for_destructive: bool = True
    verbose_logging: bool = True
    timeout_seconds: int = 300


@dataclass
class ExecutionAttempt:
    """Record of a single execution attempt."""
    attempt_number: int
    timestamp: datetime
    error: Exception | None
    error_type: FailureType | None
    root_causes: list[RootCause]
    recovery_strategies: list[RecoveryStrategy]
    recovery_results: list[RecoveryResult]
    success: bool
    execution_time_ms: float


@dataclass
class GoalExecutionTrace:
    """Complete trace of goal execution and recovery."""
    goal: str
    original_context: dict[str, Any]
    attempts: list[ExecutionAttempt] = field(default_factory=list)
    final_success: bool = False
    total_retries: int = 0
    total_recoveries_attempted: int = 0
    total_recoveries_successful: int = 0
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert trace to dictionary for logging/storage."""
        return {
            "goal": self.goal,
            "final_success": self.final_success,
            "total_retries": self.total_retries,
            "total_recoveries_attempted": self.total_recoveries_attempted,
            "total_recoveries_successful": self.total_recoveries_successful,
            "duration_seconds": (self.end_time - self.start_time).total_seconds() if self.end_time else None,
            "attempts": [
                {
                    "attempt": attempt.attempt_number,
                    "success": attempt.success,
                    "error_type": attempt.error_type.name if attempt.error_type else None,
                    "root_causes": [rc.category for rc in attempt.root_causes],
                    "strategies_tried": len(attempt.recovery_strategies),
                    "recoveries_successful": len([r for r in attempt.recovery_results if r.success]),
                }
                for attempt in self.attempts
            ]
        }


class AdaptiveGoalRecoveryEngine:
    """
    Adaptive Goal Recovery Engine (AGRE)

    Makes Azure resilient by:
    1. Persisting the original goal throughout execution
    2. Detecting and classifying failures
    3. Analyzing root causes
    4. Generating recovery hypotheses
    5. Attempting recoveries in order of confidence
    6. Learning from successful recoveries
    """

    def __init__(self, config: RecoveryConfig | None = None):
        self.config = config or RecoveryConfig()

        # Initialize components
        self.classifier = FailureClassifier()
        self.analyzer = RootCauseAnalyzer()
        self.strategy_generator = RecoveryStrategyGenerator()
        self.executor = RecoveryExecutor()
        self.learner = RecoveryLearner() if self.config.learn_from_recoveries else None

        logger.info("[AGRE] Adaptive Goal Recovery Engine initialized")
        logger.info(f"[AGRE] Config: max_retries={self.config.max_retries}, "
                   f"max_recovery_attempts={self.config.max_recovery_attempts_per_retry}")

    def execute_with_recovery(
        self,
        goal: str,
        execution_func: Callable,
        context: dict[str, Any] | None = None
    ) -> tuple[bool, Any, GoalExecutionTrace]:
        """
        Execute a goal with automatic recovery on failure.

        Args:
            goal: The user's goal (persisted throughout execution)
            execution_func: Function to execute (should return result or raise exception)
            context: Additional context for execution

        Returns:
            (success, result, trace)
        """
        context = context or {}
        trace = GoalExecutionTrace(goal=goal, original_context=context)

        logger.info(f"[AGRE] Starting goal execution: {goal}")

        for retry in range(self.config.max_retries):
            attempt_start = datetime.now()
            attempt = ExecutionAttempt(
                attempt_number=retry + 1,
                timestamp=attempt_start,
                error=None,
                error_type=None,
                root_causes=[],
                recovery_strategies=[],
                recovery_results=[],
                success=False,
                execution_time_ms=0
            )

            # Inner loop: allow one recovery cycle per retry
            recovered = False
            while True:
                try:
                    # Attempt execution
                    logger.info(f"[AGRE] Attempt {retry + 1}/{self.config.max_retries}")
                    result = execution_func(context)

                    # Success!
                    attempt.success = True
                    attempt.execution_time_ms = (datetime.now() - attempt_start).total_seconds() * 1000
                    trace.attempts.append(attempt)
                    trace.final_success = True
                    trace.end_time = datetime.now()

                    logger.info(f"[AGRE] ✓ Goal achieved on attempt {retry + 1}")
                    return True, result, trace

                except Exception as e:
                    # Execution failed
                    logger.warning(f"[AGRE] ✗ Attempt {retry + 1} failed: {type(e).__name__}: {str(e)}")

                    attempt.error = e
                    attempt.execution_time_ms = (datetime.now() - attempt_start).total_seconds() * 1000

                    if recovered:
                        # Already tried recovery once this retry, still failing
                        logger.warning("[AGRE]   Execution still failing after recovery")
                        break

                    # Step 1: Classify failure
                    failure_type = self.classifier.classify(e, context)
                    attempt.error_type = failure_type
                    logger.info(f"[AGRE]   Failure type: {failure_type.name}")

                    # Step 2: Analyze root causes
                    root_causes = self.analyzer.analyze(e, failure_type, context)
                    attempt.root_causes = root_causes
                    logger.info(f"[AGRE]   Root causes identified: {len(root_causes)}")
                    for rc in root_causes:
                        logger.info(f"[AGRE]     - {rc.category}: {rc.description} (confidence: {rc.confidence:.2f})")

                    # Step 3: Generate recovery strategies
                    strategies = self.strategy_generator.generate(
                        goal=goal,
                        failure_type=failure_type,
                        root_causes=root_causes,
                        context=context
                    )
                    attempt.recovery_strategies = strategies
                    logger.info(f"[AGRE]   Recovery strategies generated: {len(strategies)}")

                    # Step 4: Attempt recoveries
                    for strategy in strategies[:self.config.max_recovery_attempts_per_retry]:
                        logger.info(f"[AGRE]   Trying recovery: {strategy.name} (confidence: {strategy.confidence:.2f})")
                        logger.info(f"[AGRE]     Description: {strategy.description}")

                        # Check if approval needed
                        if strategy.requires_approval and self.config.require_approval_for_destructive:
                            logger.warning(f"[AGRE]     ⚠ Strategy requires approval (destructive: {strategy.destructive})")
                            logger.warning("[AGRE]     ⚠ Skipping (auto-approval disabled)")
                            continue

                        # Execute recovery
                        recovery_result = self.executor.execute(strategy, context)
                        attempt.recovery_results.append(recovery_result)
                        trace.total_recoveries_attempted += 1

                        if recovery_result.success:
                            logger.info(f"[AGRE]     ✓ Recovery successful: {recovery_result.message}")
                            trace.total_recoveries_successful += 1
                            recovered = True

                            # Learn from success
                            if self.learner:
                                self.learner.record_success(failure_type, root_causes, strategy)

                            # Update context with recovery changes
                            if recovery_result.context_updates:
                                context.update(recovery_result.context_updates)

                            break  # Exit strategy loop, retry execution
                        else:
                            logger.info(f"[AGRE]     ✗ Recovery failed: {recovery_result.message}")

                    if not recovered:
                        # All strategies failed, exit inner loop
                        break

            trace.attempts.append(attempt)
            trace.total_retries += 1

            if attempt.success:
                return True, result, trace

            # If recovery succeeded and execution was retried in inner
            # loop, we already returned above – fallthrough means failure.
            if retry == self.config.max_retries - 1:
                logger.error(f"[AGRE] ✗ Goal failed after {self.config.max_retries} attempts")
                trace.final_success = False
                trace.end_time = datetime.now()
                return False, None, trace

            logger.info("[AGRE]   No successful recovery, trying next attempt...")

        # Exhausted all retries
        trace.final_success = False
        trace.end_time = datetime.now()
        return False, None, trace

    async def execute_with_recovery_async(
        self,
        goal: str,
        execution_func: Callable,
        context: dict[str, Any] | None = None
    ) -> tuple[bool, Any, GoalExecutionTrace]:
        """
        Execute a goal with automatic recovery on failure.

        Args:
            goal: The user's goal (persisted throughout execution)
            execution_func: Function to execute (should return result or raise exception)
            context: Additional context for execution

        Returns:
            (success, result, trace)
        """
        context = context or {}
        trace = GoalExecutionTrace(goal=goal, original_context=context)

        logger.info(f"[AGRE] Starting goal execution: {goal}")

        for retry in range(self.config.max_retries):
            attempt_start = datetime.now()
            attempt = ExecutionAttempt(
                attempt_number=retry + 1,
                timestamp=attempt_start,
                error=None,
                error_type=None,
                root_causes=[],
                recovery_strategies=[],
                recovery_results=[],
                success=False,
                execution_time_ms=0
            )

            # Inner loop: allow one recovery cycle per retry
            recovered = False
            while True:
                try:
                    # Attempt execution
                    logger.info(f"[AGRE] Attempt {retry + 1}/{self.config.max_retries}")
                    result = await execution_func(context)

                    # Success!
                    attempt.success = True
                    attempt.execution_time_ms = (datetime.now() - attempt_start).total_seconds() * 1000
                    trace.attempts.append(attempt)
                    trace.final_success = True
                    trace.end_time = datetime.now()

                    logger.info(f"[AGRE] ✓ Goal achieved on attempt {retry + 1}")
                    return True, result, trace

                except Exception as e:
                    # Execution failed
                    logger.warning(f"[AGRE] ✗ Attempt {retry + 1} failed: {type(e).__name__}: {str(e)}")

                    attempt.error = e
                    attempt.execution_time_ms = (datetime.now() - attempt_start).total_seconds() * 1000

                    if recovered:
                        # Already tried recovery once this retry, still failing
                        logger.warning("[AGRE]   Execution still failing after recovery")
                        break

                    # Step 1: Classify failure
                    failure_type = self.classifier.classify(e, context)
                    attempt.error_type = failure_type
                    logger.info(f"[AGRE]   Failure type: {failure_type.name}")

                    # Step 2: Analyze root causes
                    root_causes = self.analyzer.analyze(e, failure_type, context)
                    attempt.root_causes = root_causes
                    logger.info(f"[AGRE]   Root causes identified: {len(root_causes)}")
                    for rc in root_causes:
                        logger.info(f"[AGRE]     - {rc.category}: {rc.description} (confidence: {rc.confidence:.2f})")

                    # Step 3: Generate recovery strategies
                    strategies = self.strategy_generator.generate(
                        goal=goal,
                        failure_type=failure_type,
                        root_causes=root_causes,
                        context=context
                    )
                    attempt.recovery_strategies = strategies
                    logger.info(f"[AGRE]   Recovery strategies generated: {len(strategies)}")

                    # Step 4: Attempt recoveries
                    for strategy in strategies[:self.config.max_recovery_attempts_per_retry]:
                        logger.info(f"[AGRE]   Trying recovery: {strategy.name} (confidence: {strategy.confidence:.2f})")
                        logger.info(f"[AGRE]     Description: {strategy.description}")

                        # Check if approval needed
                        if strategy.requires_approval and self.config.require_approval_for_destructive:
                            logger.warning(f"[AGRE]     ⚠ Strategy requires approval (destructive: {strategy.destructive})")
                            logger.warning("[AGRE]     ⚠ Skipping (auto-approval disabled)")
                            continue

                        # Execute recovery
                        recovery_result = self.executor.execute(strategy, context)
                        attempt.recovery_results.append(recovery_result)
                        trace.total_recoveries_attempted += 1

                        if recovery_result.success:
                            logger.info(f"[AGRE]     ✓ Recovery successful: {recovery_result.message}")
                            trace.total_recoveries_successful += 1
                            recovered = True

                            # Learn from success
                            if self.learner:
                                self.learner.record_success(failure_type, root_causes, strategy)

                            # Update context with recovery changes
                            if recovery_result.context_updates:
                                context.update(recovery_result.context_updates)

                            break  # Exit strategy loop, retry execution
                        else:
                            logger.info(f"[AGRE]     ✗ Recovery failed: {recovery_result.message}")

                    if not recovered:
                        break

            trace.attempts.append(attempt)
            trace.total_retries += 1

            if attempt.success:
                return True, result, trace

            if retry == self.config.max_retries - 1:
                logger.error(f"[AGRE] ✗ Goal failed after {self.config.max_retries} attempts")
                trace.final_success = False
                trace.end_time = datetime.now()
                return False, None, trace

            logger.info("[AGRE]   No successful recovery, trying next attempt...")

        # Exhausted all retries
        trace.final_success = False
        trace.end_time = datetime.now()
        return False, None, trace

    def get_recovery_insights(self) -> dict[str, Any]:
        """Get insights from learned recovery patterns."""
        if not self.learner:
            return {"learning_disabled": True}

        return self.learner.get_insights()
