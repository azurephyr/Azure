"""
Recovery Executor

Executes recovery strategies safely.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .strategy import ActionType, RecoveryStrategy

logger = logging.getLogger("azure.recovery.executor")


@dataclass
class RecoveryResult:
    """Result of a recovery attempt."""
    success: bool
    message: str
    context_updates: dict[str, Any] | None = None
    error: Exception | None = None


class RecoveryExecutor:
    """Executes recovery strategies."""

    def execute(self, strategy: RecoveryStrategy, context: dict[str, Any]) -> RecoveryResult:
        """
        Execute a recovery strategy.

        Args:
            strategy: The recovery strategy to execute
            context: Execution context

        Returns:
            RecoveryResult indicating success/failure
        """
        logger.info(f"[Executor] Executing strategy: {strategy.name}")

        if (strategy.requires_approval or strategy.destructive) and context.get("approved") is not True:
            logger.warning(
                "[Executor] Refusing unapproved recovery strategy: %s",
                strategy.name,
            )
            return RecoveryResult(
                success=False,
                message=f"Recovery strategy '{strategy.name}' requires explicit approval",
            )

        try:
            # Execute each action in the strategy
            updates = {}
            for action in strategy.actions:
                result = self._execute_action(action, context)
                if not result.success:
                    return result
                if result.context_updates:
                    updates.update(result.context_updates)

            return RecoveryResult(
                success=True,
                message=f"Strategy '{strategy.name}' executed successfully",
                context_updates=updates if updates else None
            )

        except Exception as e:
            logger.error(f"[Executor] Strategy execution failed: {e}")
            return RecoveryResult(
                success=False,
                message=f"Execution error: {str(e)}",
                error=e
            )

    def _execute_action(self, action, context: dict[str, Any]) -> RecoveryResult:
        """Execute a single recovery action."""

        if action.action_type == ActionType.INSTALL_PACKAGE:
            return self._install_package(action.params)

        elif action.action_type == ActionType.CREATE_FILE:
            return self._create_file(action.params)

        elif action.action_type == ActionType.CREATE_DIRECTORY:
            return self._create_directory(action.params)

        elif action.action_type == ActionType.SET_ENV_VAR:
            return self._set_env_var(action.params)

        elif action.action_type == ActionType.RETRY_WITH_BACKOFF:
            return self._retry_with_backoff(action.params, context)

        elif action.action_type == ActionType.WAIT_AND_RETRY:
            return self._wait_and_retry(action.params)

        elif action.action_type == ActionType.USE_FALLBACK:
            return self._use_fallback(action.params)

        elif action.action_type == ActionType.CLEAR_CACHE:
            return self._clear_cache()

        else:
            return RecoveryResult(
                success=False,
                message=f"Unsupported action type: {action.action_type}"
            )

    def _install_package(self, params: dict[str, Any]) -> RecoveryResult:
        """Install a Python package."""
        package = params.get("package")
        if not package:
            return RecoveryResult(success=False, message="No package specified")

        try:
            logger.info(f"[Executor] Installing package: {package}")
            result = subprocess.run(
                ["pip", "install", package],
                capture_output=True,
                text=True,
                timeout=300
            )

            if result.returncode == 0:
                logger.info(f"[Executor] Package installed successfully: {package}")
                return RecoveryResult(
                    success=True,
                    message=f"Installed {package}",
                    context_updates={"installed_packages": [package]}
                )
            else:
                return RecoveryResult(
                    success=False,
                    message=f"Failed to install {package}: {result.stderr}"
                )

        except subprocess.TimeoutExpired:
            return RecoveryResult(success=False, message=f"Installation of {package} timed out")
        except Exception as e:
            return RecoveryResult(success=False, message=f"Installation error: {str(e)}", error=e)

    def _create_file(self, params: dict[str, Any]) -> RecoveryResult:
        """Create a file."""
        path = params.get("path")
        content = params.get("content", "")
        copy_from = params.get("copy_from")

        if not path:
            return RecoveryResult(success=False, message="No path specified")

        try:
            file_path = Path(path)

            # Create parent directories if needed
            file_path.parent.mkdir(parents=True, exist_ok=True)

            if copy_from:
                # Copy from template
                template_path = Path(copy_from)
                if not template_path.exists():
                    return RecoveryResult(
                        success=False,
                        message=f"Template not found: {copy_from}"
                    )

                logger.info(f"[Executor] Copying {copy_from} to {path}")
                file_path.write_text(template_path.read_text())
            else:
                # Create with content
                logger.info(f"[Executor] Creating file: {path}")
                file_path.write_text(content)

            return RecoveryResult(
                success=True,
                message=f"Created file: {path}",
                context_updates={"created_files": [str(path)]}
            )

        except Exception as e:
            return RecoveryResult(success=False, message=f"File creation error: {str(e)}", error=e)

    def _create_directory(self, params: dict[str, Any]) -> RecoveryResult:
        """Create a directory."""
        path = params.get("path")
        if not path:
            return RecoveryResult(success=False, message="No path specified")

        try:
            dir_path = Path(path)
            dir_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"[Executor] Created directory: {path}")
            return RecoveryResult(
                success=True,
                message=f"Created directory: {path}",
                context_updates={"created_directories": [str(path)]}
            )
        except Exception as e:
            return RecoveryResult(success=False, message=f"Directory creation error: {str(e)}", error=e)

    def _set_env_var(self, params: dict[str, Any]) -> RecoveryResult:
        """Set an environment variable."""
        key = params.get("key")
        value = params.get("value")

        if not key or not value:
            return RecoveryResult(success=False, message="Missing key or value")

        try:
            os.environ[key] = value
            logger.info(f"[Executor] Set environment variable: {key}")
            return RecoveryResult(
                success=True,
                message=f"Set {key}={value}",
                context_updates={"env_vars_set": {key: "[REDACTED]"}}
            )
        except Exception as e:
            return RecoveryResult(success=False, message=f"Env var error: {str(e)}", error=e)

    def _retry_with_backoff(self, params: dict[str, Any], context: dict[str, Any]) -> RecoveryResult:
        """Signal that operation should be retried with backoff."""
        # This doesn't execute anything, just signals to retry
        max_retries = params.get("max_retries", 3)
        base_delay = params.get("base_delay", 1.0)

        return RecoveryResult(
            success=True,
            message=f"Will retry with backoff (max {max_retries} attempts)",
            context_updates={
                "retry_config": {
                    "max_retries": max_retries,
                    "base_delay": base_delay,
                    "current_attempt": 0
                }
            }
        )

    def _wait_and_retry(self, params: dict[str, Any]) -> RecoveryResult:
        """Wait for a period before retrying."""
        wait_seconds = params.get("wait_seconds", 60)

        try:
            logger.info(f"[Executor] Waiting {wait_seconds} seconds before retry...")
            time.sleep(wait_seconds)
            return RecoveryResult(
                success=True,
                message=f"Waited {wait_seconds} seconds",
                context_updates={"waited_seconds": wait_seconds}
            )
        except Exception as e:
            return RecoveryResult(success=False, message=f"Wait error: {str(e)}", error=e)

    def _use_fallback(self, params: dict[str, Any]) -> RecoveryResult:
        """Use a fallback value."""
        key = params.get("key")
        default = params.get("default")

        return RecoveryResult(
            success=True,
            message=f"Using fallback value for {key}",
            context_updates={"fallback_values": {key: default}}
        )

    def _clear_cache(self) -> RecoveryResult:
        """Clear caches."""
        try:
            import gc
            gc.collect()
            logger.info("[Executor] Cleared memory caches")
            return RecoveryResult(success=True, message="Caches cleared")
        except Exception as e:
            return RecoveryResult(success=False, message=f"Cache clear error: {str(e)}", error=e)
