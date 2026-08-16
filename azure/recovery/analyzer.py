"""
Root Cause Analyzer

Performs structured root-cause analysis on failures.
"""

from __future__ import annotations

import logging
import re
import traceback
from dataclasses import dataclass
from typing import Any

from .classifier import FailureType

logger = logging.getLogger("azure.recovery.analyzer")


@dataclass
class RootCause:
    """A potential root cause of a failure."""
    category: str
    description: str
    confidence: float  # 0.0 to 1.0
    evidence: list[str]
    suggested_fix: str


class RootCauseAnalyzer:
    """Analyzes failures to determine root causes."""

    def analyze(
        self,
        exception: Exception,
        failure_type: FailureType,
        context: dict[str, Any]
    ) -> list[RootCause]:
        """
        Analyze an exception to determine root causes.

        Args:
            exception: The exception that occurred
            failure_type: Classified failure type
            context: Execution context

        Returns:
            List of potential root causes, sorted by confidence
        """
        error_message = str(exception)
        stack_trace = traceback.format_exc()

        root_causes = []

        # Analyze based on failure type
        if failure_type == FailureType.MISSING_DEPENDENCY:
            root_causes.extend(self._analyze_missing_dependency(exception, error_message))

        elif failure_type == FailureType.MISSING_FILE:
            root_causes.extend(self._analyze_missing_file(exception, error_message, context))

        elif failure_type == FailureType.MISSING_CONFIG:
            root_causes.extend(self._analyze_missing_config(exception, error_message, context))

        elif failure_type == FailureType.MISSING_API_KEY:
            root_causes.extend(self._analyze_missing_api_key(exception, error_message, context))

        elif failure_type == FailureType.PERMISSION_DENIED:
            root_causes.extend(self._analyze_permission_denied(exception, error_message, context))

        elif failure_type == FailureType.NETWORK_ERROR:
            root_causes.extend(self._analyze_network_error(exception, error_message))

        elif failure_type == FailureType.TIMEOUT:
            root_causes.extend(self._analyze_timeout(exception, error_message, context))

        elif failure_type == FailureType.RATE_LIMIT:
            root_causes.extend(self._analyze_rate_limit(exception, error_message))

        elif failure_type == FailureType.INVALID_INPUT:
            root_causes.extend(self._analyze_invalid_input(exception, error_message, stack_trace))

        elif failure_type == FailureType.TYPE_ERROR:
            root_causes.extend(self._analyze_type_error(exception, error_message, stack_trace))

        elif failure_type == FailureType.ATTRIBUTE_ERROR:
            root_causes.extend(self._analyze_attribute_error(exception, error_message, stack_trace))

        elif failure_type == FailureType.KEY_ERROR:
            root_causes.extend(self._analyze_key_error(exception, error_message, stack_trace))

        elif failure_type == FailureType.OUT_OF_MEMORY:
            root_causes.extend(self._analyze_out_of_memory(exception, error_message, context))

        else:
            # Generic analysis for unknown types
            root_causes.append(RootCause(
                category="unknown",
                description=f"Unknown failure: {type(exception).__name__}",
                confidence=0.3,
                evidence=[error_message],
                suggested_fix="Manual investigation required"
            ))

        # Sort by confidence (highest first)
        root_causes.sort(key=lambda rc: rc.confidence, reverse=True)

        return root_causes

    def _analyze_missing_dependency(self, exception: Exception, message: str) -> list[RootCause]:
        """Analyze missing dependency errors."""
        # Extract module name
        module_match = re.search(r"No module named '([^']+)'", message)
        module_name = module_match.group(1) if module_match else "unknown"

        return [
            RootCause(
                category="missing_package",
                description=f"Python package '{module_name}' is not installed",
                confidence=0.95,
                evidence=[message],
                suggested_fix=f"Install package: pip install {module_name}"
            )
        ]

    def _analyze_missing_file(self, exception: Exception, message: str, context: dict) -> list[RootCause]:
        """Analyze missing file errors."""
        # Extract filename
        file_match = re.search(r"'([^']+)'", message)
        filename = file_match.group(1) if file_match else "unknown"

        causes = [
            RootCause(
                category="file_not_created",
                description=f"File '{filename}' does not exist",
                confidence=0.9,
                evidence=[message],
                suggested_fix=f"Create or restore the file: {filename}"
            )
        ]

        # Check if it's a configuration file
        if any(ext in filename for ext in ['.env', '.config', '.json', '.yaml', '.yml']):
            causes.append(RootCause(
                category="missing_configuration_file",
                description=f"Configuration file '{filename}' is missing",
                confidence=0.85,
                evidence=[message, "Configuration file pattern detected"],
                suggested_fix=f"Copy from template or create: {filename}"
            ))

        return causes

    def _analyze_missing_config(self, exception: Exception, message: str, context: dict) -> list[RootCause]:
        """Analyze missing configuration errors."""
        # Extract key name
        key_match = re.search(r"'([^']+)'", message)
        key_name = key_match.group(1) if key_match else "unknown"

        return [
            RootCause(
                category="missing_environment_variable",
                description=f"Environment variable or config key '{key_name}' is not set",
                confidence=0.9,
                evidence=[message],
                suggested_fix=f"Set {key_name} in .env file or environment"
            )
        ]

    def _analyze_missing_api_key(self, exception: Exception, message: str, context: dict) -> list[RootCause]:
        """Analyze missing API key errors."""
        return [
            RootCause(
                category="missing_api_credentials",
                description="API key or authentication token is missing or invalid",
                confidence=0.95,
                evidence=[message],
                suggested_fix="Set API key in .env file (OPENAI_API_KEY, GOOGLE_API_KEY, etc.)"
            )
        ]

    def _analyze_permission_denied(self, exception: Exception, message: str, context: dict) -> list[RootCause]:
        """Analyze permission denied errors."""
        return [
            RootCause(
                category="insufficient_permissions",
                description="Insufficient file system or resource permissions",
                confidence=0.85,
                evidence=[message],
                suggested_fix="Run with elevated permissions or change file/directory permissions"
            )
        ]

    def _analyze_network_error(self, exception: Exception, message: str) -> list[RootCause]:
        """Analyze network errors."""
        causes = []

        if "connection refused" in message.lower():
            causes.append(RootCause(
                category="service_not_running",
                description="Target service is not running or not accessible",
                confidence=0.8,
                evidence=[message],
                suggested_fix="Start the service or check firewall rules"
            ))

        if "timeout" in message.lower():
            causes.append(RootCause(
                category="network_timeout",
                description="Network request timed out",
                confidence=0.75,
                evidence=[message],
                suggested_fix="Check internet connection or increase timeout"
            ))

        if not causes:
            causes.append(RootCause(
                category="network_connectivity",
                description="Network connectivity issue",
                confidence=0.7,
                evidence=[message],
                suggested_fix="Check internet connection and DNS"
            ))

        return causes

    def _analyze_timeout(self, exception: Exception, message: str, context: dict) -> list[RootCause]:
        """Analyze timeout errors."""
        return [
            RootCause(
                category="operation_timeout",
                description="Operation exceeded time limit",
                confidence=0.8,
                evidence=[message],
                suggested_fix="Increase timeout or optimize operation performance"
            )
        ]

    def _analyze_rate_limit(self, exception: Exception, message: str) -> list[RootCause]:
        """Analyze rate limit errors."""
        return [
            RootCause(
                category="api_rate_limit_exceeded",
                description="API rate limit exceeded",
                confidence=0.95,
                evidence=[message],
                suggested_fix="Wait and retry, or implement exponential backoff"
            )
        ]

    def _analyze_invalid_input(self, exception: Exception, message: str, stack_trace: str) -> list[RootCause]:
        """Analyze invalid input errors."""
        return [
            RootCause(
                category="data_validation_failed",
                description="Input data failed validation",
                confidence=0.75,
                evidence=[message],
                suggested_fix="Validate and sanitize input data before use"
            )
        ]

    def _analyze_type_error(self, exception: Exception, message: str, stack_trace: str) -> list[RootCause]:
        """Analyze type errors."""
        return [
            RootCause(
                category="type_mismatch",
                description=f"Type mismatch: {message}",
                confidence=0.85,
                evidence=[message],
                suggested_fix="Check data types and add type validation"
            )
        ]

    def _analyze_attribute_error(self, exception: Exception, message: str, stack_trace: str) -> list[RootCause]:
        """Analyze attribute errors."""
        # Extract attribute name
        attr_match = re.search(r"'([^']+)' object has no attribute '([^']+)'", message)

        if attr_match:
            obj_type, attr_name = attr_match.groups()
            return [
                RootCause(
                    category="missing_attribute",
                    description=f"Object of type '{obj_type}' missing attribute '{attr_name}'",
                    confidence=0.9,
                    evidence=[message],
                    suggested_fix=f"Check if object is initialized correctly or add attribute '{attr_name}'"
                )
            ]

        return [
            RootCause(
                category="attribute_access_error",
                description=f"Attribute access error: {message}",
                confidence=0.7,
                evidence=[message],
                suggested_fix="Check object initialization and attribute names"
            )
        ]

    def _analyze_key_error(self, exception: Exception, message: str, stack_trace: str) -> list[RootCause]:
        """Analyze key errors."""
        # Extract key name
        key_match = re.search(r"'([^']+)'", message)
        key_name = key_match.group(1) if key_match else "unknown"

        return [
            RootCause(
                category="missing_dictionary_key",
                description=f"Dictionary key '{key_name}' not found",
                confidence=0.85,
                evidence=[message],
                suggested_fix=f"Add key '{key_name}' to dictionary or use .get() with default"
            )
        ]

    def _analyze_out_of_memory(self, exception: Exception, message: str, context: dict) -> list[RootCause]:
        """Analyze out of memory errors."""
        return [
            RootCause(
                category="memory_exhausted",
                description="System ran out of available memory",
                confidence=0.9,
                evidence=[message],
                suggested_fix="Reduce memory usage or increase available RAM"
            )
        ]
