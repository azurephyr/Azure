"""
Integration module for AGRE with Azure's execution pipeline.

This module provides integration points for the Adaptive Goal Recovery Engine
with Azure's planner and executor.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

try:
    from .engine import AdaptiveGoalRecoveryEngine, GoalExecutionTrace, RecoveryConfig
except Exception as e:
    logging.getLogger("azure.recovery.integration").error(f"AGRE engine import failed: {e}")
    AdaptiveGoalRecoveryEngine = None  # type: ignore
    RecoveryConfig = None  # type: ignore
    GoalExecutionTrace = None  # type: ignore

logger = logging.getLogger("azure.recovery.integration")


class AGREIntegration:
    """Integration layer for AGRE with Azure."""

    def __init__(self, config: RecoveryConfig | None = None):
        if AdaptiveGoalRecoveryEngine is None:
            raise ImportError(
                "AdaptiveGoalRecoveryEngine could not be imported. "
                "Ensure all AGRE dependencies are available."
            )
        self.agre = AdaptiveGoalRecoveryEngine(config)
        logger.info("[AGRE Integration] Initialized")

    def with_recovery(self, goal: str, context: dict[str, Any] | None = None):
        """
        Decorator to add AGRE recovery to any function.
        """
        def decorator(func: Callable):
            @wraps(func)
            def wrapper(*args, **kwargs):
                exec_context = dict(context) if context else {}
                exec_context.update(kwargs)

                success, result, trace = self.agre.execute_with_recovery(
                    goal=goal,
                    execution_func=lambda ctx: func(*args, **kwargs),
                    context=exec_context
                )

                if not success:
                    logger.error(f"[AGRE] Goal failed: {goal}")
                    logger.error(f"[AGRE] Trace: {trace.to_dict()}")
                    raise RuntimeError(f"Goal '{goal}' failed after {trace.total_retries} retries")

                return result

            return wrapper
        return decorator

    def with_recovery_async(self, goal: str, context: dict[str, Any] | None = None):
        def decorator(func: Callable):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                exec_context = dict(context) if context else {}
                for k, v in kwargs.items():
                    if isinstance(v, (str, int, float, bool)):
                        exec_context[k] = v

                success, result, trace = await self.agre.execute_with_recovery_async(
                    goal=goal,
                    execution_func=lambda ctx: func(*args, **kwargs),
                    context=exec_context
                )

                if not success:
                    logger.error(f"[AGRE] Goal failed: {goal}")
                    raise RuntimeError(f"Goal '{goal}' failed after {trace.total_retries} retries")

                return result
            return wrapper
        return decorator
    def execute_goal(
        self,
        goal: str,
        execution_func: Callable,
        context: dict[str, Any] | None = None
    ) -> tuple[bool, Any, GoalExecutionTrace]:
        """
        Execute a goal with recovery.

        Args:
            goal: User goal description
            execution_func: Function to execute
            context: Execution context

        Returns:
            (success, result, trace)
        """
        return self.agre.execute_with_recovery(goal, execution_func, context)

    def get_insights(self) -> dict[str, Any]:
        """Get recovery insights."""
        return self.agre.get_recovery_insights()


# Global instance for easy access
_agre_instance: AGREIntegration | None = None


def get_agre() -> AGREIntegration:
    """Get or create global AGRE instance."""
    global _agre_instance
    if _agre_instance is None:
        _agre_instance = AGREIntegration()
    return _agre_instance


def with_agre_recovery(goal: str, context: dict[str, Any] | None = None):
    """
    Decorator to add AGRE recovery to functions.

    Usage:
        @with_agre_recovery("Process user message")
        def process_message(message):
            # Your code here
            pass
    """
    agre = get_agre()
    return agre.with_recovery(goal, context)

def with_agre_recovery_async(goal: str, context: dict[str, Any] | None = None):
    agre = get_agre()
    return agre.with_recovery_async(goal, context)
