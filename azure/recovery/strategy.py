"""
Recovery Strategy Generator

Generates recovery strategies based on root causes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .analyzer import RootCause
from .classifier import FailureType

logger = logging.getLogger("azure.recovery.strategy")


class ActionType(Enum):
    """Types of recovery actions."""
    INSTALL_PACKAGE = "install_package"
    CREATE_FILE = "create_file"
    CREATE_DIRECTORY = "create_directory"
    SET_ENV_VAR = "set_env_var"
    MODIFY_CONFIG = "modify_config"
    RETRY_WITH_BACKOFF = "retry_with_backoff"
    USE_FALLBACK = "use_fallback"
    INCREASE_RESOURCE = "increase_resource"
    WAIT_AND_RETRY = "wait_and_retry"
    FIX_PERMISSIONS = "fix_permissions"
    VALIDATE_INPUT = "validate_input"
    CLEAR_CACHE = "clear_cache"


@dataclass
class RecoveryAction:
    """A single recovery action."""
    action_type: ActionType
    params: dict[str, Any]
    description: str


@dataclass
class RecoveryStrategy:
    """A recovery strategy consisting of one or more actions."""
    name: str
    description: str
    confidence: float  # 0.0 to 1.0
    actions: list[RecoveryAction]
    requires_approval: bool = False
    destructive: bool = False  # Modifies production code/config
    safe: bool = True  # Safe to auto-execute


class RecoveryStrategyGenerator:
    """Generates recovery strategies from root causes."""

    def generate(
        self,
        goal: str,
        failure_type: FailureType,
        root_causes: list[RootCause],
        context: dict[str, Any]
    ) -> list[RecoveryStrategy]:
        """
        Generate recovery strategies.

        Args:
            goal: Original user goal
            failure_type: Type of failure
            root_causes: Analyzed root causes
            context: Execution context

        Returns:
            List of recovery strategies, sorted by confidence
        """
        strategies = []

        # Generate strategies for each root cause
        for root_cause in root_causes:
            if root_cause.category == "missing_package":
                strategies.extend(self._generate_install_package_strategies(root_cause, context))

            elif root_cause.category == "file_not_created":
                strategies.extend(self._generate_create_file_strategies(root_cause, context))

            elif root_cause.category == "missing_configuration_file":
                strategies.extend(self._generate_config_file_strategies(root_cause, context))

            elif root_cause.category == "missing_environment_variable":
                strategies.extend(self._generate_env_var_strategies(root_cause, context))

            elif root_cause.category == "missing_api_credentials":
                strategies.extend(self._generate_api_key_strategies(root_cause, context))

            elif root_cause.category == "insufficient_permissions":
                strategies.extend(self._generate_permission_strategies(root_cause, context))

            elif root_cause.category == "network_connectivity" or root_cause.category == "network_timeout":
                strategies.extend(self._generate_network_strategies(root_cause, context))

            elif root_cause.category == "api_rate_limit_exceeded":
                strategies.extend(self._generate_rate_limit_strategies(root_cause, context))

            elif root_cause.category == "operation_timeout":
                strategies.extend(self._generate_timeout_strategies(root_cause, context))

            elif root_cause.category == "type_mismatch":
                strategies.extend(self._generate_type_error_strategies(root_cause, context))

            elif root_cause.category == "missing_attribute" or root_cause.category == "attribute_access_error":
                strategies.extend(self._generate_attribute_strategies(root_cause, context))

            elif root_cause.category == "missing_dictionary_key":
                strategies.extend(self._generate_key_error_strategies(root_cause, context))

            elif root_cause.category == "memory_exhausted":
                strategies.extend(self._generate_memory_strategies(root_cause, context))

        # Sort by confidence (highest first)
        strategies.sort(key=lambda s: s.confidence, reverse=True)

        return strategies

    def _generate_install_package_strategies(self, root_cause: RootCause, context: dict) -> list[RecoveryStrategy]:
        """Generate strategies for missing packages."""
        # Extract package name from suggested_fix
        import re
        match = re.search(r"pip install ([^\s]+)", root_cause.suggested_fix)
        package_name = match.group(1) if match else "unknown"

        return [
            RecoveryStrategy(
                name="install_missing_package",
                description=f"Install missing Python package: {package_name}",
                confidence=root_cause.confidence * 0.95,  # Slightly lower than root cause
                actions=[
                    RecoveryAction(
                        action_type=ActionType.INSTALL_PACKAGE,
                        params={"package": package_name},
                        description=f"Run: pip install {package_name}"
                    )
                ],
                requires_approval=True,
                destructive=True,
                safe=False
            )
        ]

    def _generate_create_file_strategies(self, root_cause: RootCause, context: dict) -> list[RecoveryStrategy]:
        """Generate strategies for missing files."""
        import re
        match = re.search(r"'([^']+)'", root_cause.description)
        filename = match.group(1) if match else "unknown"

        return [
            RecoveryStrategy(
                name="create_empty_file",
                description=f"Create empty file: {filename}",
                confidence=root_cause.confidence * 0.7,  # Lower confidence for auto-creation
                actions=[
                    RecoveryAction(
                        action_type=ActionType.CREATE_FILE,
                        params={"path": filename, "content": ""},
                        description=f"Create empty file: {filename}"
                    )
                ],
                requires_approval=True,  # Requires approval
                destructive=False,
                safe=True
            )
        ]

    def _generate_config_file_strategies(self, root_cause: RootCause, context: dict) -> list[RecoveryStrategy]:
        """Generate strategies for missing config files."""
        import re
        match = re.search(r"'([^']+)'", root_cause.description)
        filename = match.group(1) if match else "unknown"

        strategies = []

        # Strategy 1: Copy from template
        if ".env" in filename:
            template = filename.replace(".env", ".env.example")
            strategies.append(RecoveryStrategy(
                name="copy_from_template",
                description=f"Copy from template: {template} → {filename}",
                confidence=root_cause.confidence * 0.9,
                actions=[
                    RecoveryAction(
                        action_type=ActionType.CREATE_FILE,
                        params={"path": filename, "copy_from": template},
                        description=f"Copy {template} to {filename}"
                    )
                ],
                requires_approval=True,
                destructive=False,
                safe=True
            ))

        return strategies

    def _generate_env_var_strategies(self, root_cause: RootCause, context: dict) -> list[RecoveryStrategy]:
        """Generate strategies for missing environment variables."""
        import re
        match = re.search(r"'([^']+)'", root_cause.description)
        var_name = match.group(1) if match else "unknown"

        # Cannot auto-set without knowing the value
        # This would require user input
        return [
            RecoveryStrategy(
                name="prompt_for_env_var",
                description=f"Environment variable {var_name} needs to be set manually",
                confidence=root_cause.confidence * 0.5,  # Low confidence for auto-fix
                actions=[],  # No automatic action
                requires_approval=True,
                destructive=False,
                safe=True
            )
        ]

    def _generate_api_key_strategies(self, root_cause: RootCause, context: dict) -> list[RecoveryStrategy]:
        """Generate strategies for missing API keys."""
        # Cannot auto-recover - user must provide API key
        return [
            RecoveryStrategy(
                name="request_api_key",
                description="API key must be provided by user",
                confidence=root_cause.confidence * 0.3,
                actions=[],
                requires_approval=True,
                destructive=False,
                safe=True
            )
        ]

    def _generate_permission_strategies(self, root_cause: RootCause, context: dict) -> list[RecoveryStrategy]:
        """Generate strategies for permission errors."""
        return [
            RecoveryStrategy(
                name="fix_permissions",
                description="Attempt to fix file permissions",
                confidence=root_cause.confidence * 0.6,
                actions=[
                    RecoveryAction(
                        action_type=ActionType.FIX_PERMISSIONS,
                        params={},
                        description="Fix file/directory permissions"
                    )
                ],
                requires_approval=True,
                destructive=False,
                safe=True
            )
        ]

    def _generate_network_strategies(self, root_cause: RootCause, context: dict) -> list[RecoveryStrategy]:
        """Generate strategies for network errors."""
        return [
            RecoveryStrategy(
                name="retry_with_backoff",
                description="Retry operation with exponential backoff",
                confidence=root_cause.confidence * 0.8,
                actions=[
                    RecoveryAction(
                        action_type=ActionType.RETRY_WITH_BACKOFF,
                        params={"max_retries": 3, "base_delay": 1.0, "max_delay": 30.0},
                        description="Retry with exponential backoff (1s, 2s, 4s...)"
                    )
                ],
                requires_approval=False,
                destructive=False,
                safe=True
            )
        ]

    def _generate_rate_limit_strategies(self, root_cause: RootCause, context: dict) -> list[RecoveryStrategy]:
        """Generate strategies for rate limit errors."""
        return [
            RecoveryStrategy(
                name="wait_for_rate_limit",
                description="Wait and retry after rate limit cooldown",
                confidence=root_cause.confidence * 0.9,
                actions=[
                    RecoveryAction(
                        action_type=ActionType.WAIT_AND_RETRY,
                        params={"wait_seconds": 60},
                        description="Wait 60 seconds for rate limit reset"
                    )
                ],
                requires_approval=False,
                destructive=False,
                safe=True
            )
        ]

    def _generate_timeout_strategies(self, root_cause: RootCause, context: dict) -> list[RecoveryStrategy]:
        """Generate strategies for timeout errors."""
        return [
            RecoveryStrategy(
                name="increase_timeout",
                description="Increase operation timeout and retry",
                confidence=root_cause.confidence * 0.7,
                actions=[
                    RecoveryAction(
                        action_type=ActionType.MODIFY_CONFIG,
                        params={"key": "timeout", "value": "increased"},
                        description="Increase timeout limit"
                    )
                ],
                requires_approval=False,
                destructive=False,
                safe=True
            )
        ]

    def _generate_type_error_strategies(self, root_cause: RootCause, context: dict) -> list[RecoveryStrategy]:
        """Generate strategies for type errors."""
        return [
            RecoveryStrategy(
                name="validate_types",
                description="Add type validation before operation",
                confidence=root_cause.confidence * 0.5,
                actions=[
                    RecoveryAction(
                        action_type=ActionType.VALIDATE_INPUT,
                        params={},
                        description="Validate and convert types"
                    )
                ],
                requires_approval=False,
                destructive=False,
                safe=True
            )
        ]

    def _generate_attribute_strategies(self, root_cause: RootCause, context: dict) -> list[RecoveryStrategy]:
        """Generate strategies for attribute errors."""
        return [
            RecoveryStrategy(
                name="check_object_initialization",
                description="Verify object is properly initialized",
                confidence=root_cause.confidence * 0.4,
                actions=[],  # Requires code fix
                requires_approval=True,
                destructive=False,
                safe=False
            )
        ]

    def _generate_key_error_strategies(self, root_cause: RootCause, context: dict) -> list[RecoveryStrategy]:
        """Generate strategies for key errors."""
        import re
        match = re.search(r"'([^']+)'", root_cause.description)
        key_name = match.group(1) if match else "unknown"

        return [
            RecoveryStrategy(
                name="use_default_value",
                description=f"Use default value for missing key: {key_name}",
                confidence=root_cause.confidence * 0.6,
                actions=[
                    RecoveryAction(
                        action_type=ActionType.USE_FALLBACK,
                        params={"key": key_name, "default": None},
                        description=f"Use None as default for {key_name}"
                    )
                ],
                requires_approval=False,
                destructive=False,
                safe=True
            )
        ]

    def _generate_memory_strategies(self, root_cause: RootCause, context: dict) -> list[RecoveryStrategy]:
        """Generate strategies for memory errors."""
        return [
            RecoveryStrategy(
                name="clear_cache_and_retry",
                description="Clear caches and retry operation",
                confidence=root_cause.confidence * 0.6,
                actions=[
                    RecoveryAction(
                        action_type=ActionType.CLEAR_CACHE,
                        params={},
                        description="Clear memory caches"
                    )
                ],
                requires_approval=False,
                destructive=False,
                safe=True
            )
        ]
