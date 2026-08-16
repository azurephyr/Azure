"""
Failure Classifier

Classifies execution failures into categories for targeted recovery.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

logger = logging.getLogger("azure.recovery.classifier")


class FailureType(Enum):
    """Types of failures that can occur during execution."""

    # Environment failures
    MISSING_DEPENDENCY = "missing_dependency"
    MISSING_FILE = "missing_file"
    MISSING_DIRECTORY = "missing_directory"
    PERMISSION_DENIED = "permission_denied"
    NETWORK_ERROR = "network_error"

    # Configuration failures
    MISSING_CONFIG = "missing_config"
    INVALID_CONFIG = "invalid_config"
    MISSING_API_KEY = "missing_api_key"

    # Resource failures
    OUT_OF_MEMORY = "out_of_memory"
    DISK_FULL = "disk_full"
    TIMEOUT = "timeout"

    # Data failures
    INVALID_INPUT = "invalid_input"
    CORRUPTED_DATA = "corrupted_data"
    MISSING_DATA = "missing_data"

    # API/Service failures
    API_ERROR = "api_error"
    RATE_LIMIT = "rate_limit"
    SERVICE_UNAVAILABLE = "service_unavailable"

    # Code failures
    TYPE_ERROR = "type_error"
    VALUE_ERROR = "value_error"
    ATTRIBUTE_ERROR = "attribute_error"
    KEY_ERROR = "key_error"
    INDEX_ERROR = "index_error"

    # Unknown
    UNKNOWN = "unknown"


class FailureClassifier:
    """Classifies exceptions into failure types."""

    def __init__(self):
        self.classification_rules = {
            # Module/Import errors
            "ModuleNotFoundError": FailureType.MISSING_DEPENDENCY,
            "ImportError": FailureType.MISSING_DEPENDENCY,

            # File system errors
            "FileNotFoundError": FailureType.MISSING_FILE,
            "IsADirectoryError": FailureType.INVALID_INPUT,
            "NotADirectoryError": FailureType.INVALID_INPUT,
            "PermissionError": FailureType.PERMISSION_DENIED,
            "FileExistsError": FailureType.INVALID_INPUT,

            # OS errors
            "OSError": FailureType.DISK_FULL,  # Can also be network, will check message

            # Network errors
            "ConnectionError": FailureType.NETWORK_ERROR,
            "TimeoutError": FailureType.TIMEOUT,
            "HTTPError": FailureType.API_ERROR,
            "URLError": FailureType.NETWORK_ERROR,

            # Memory errors
            "MemoryError": FailureType.OUT_OF_MEMORY,

            # Type errors
            "TypeError": FailureType.TYPE_ERROR,
            "ValueError": FailureType.VALUE_ERROR,
            "AttributeError": FailureType.ATTRIBUTE_ERROR,
            "KeyError": FailureType.KEY_ERROR,
            "IndexError": FailureType.INDEX_ERROR,

            # Configuration (original code reused "KeyError" which silently
            # overwrote the KEY_ERROR entry above; renaming surfaces the
            # intent in logs without changing behavioral contract here —
            # callers still match on the dict value).
            "ConfigKeyError": FailureType.MISSING_CONFIG,
        }

    def classify(self, exception: Exception, context: dict[str, Any]) -> FailureType:
        """
        Classify an exception into a failure type.

        Args:
            exception: The exception that occurred
            context: Execution context

        Returns:
            FailureType enum
        """
        exception_type = type(exception).__name__
        error_message = str(exception).lower()

        # Check direct mapping
        if exception_type in self.classification_rules:
            base_type = self.classification_rules[exception_type]

            # Refine based on message
            if base_type == FailureType.KEY_ERROR:
                # Check if it's a config key or data key
                if "env" in error_message or "config" in error_message:
                    return FailureType.MISSING_CONFIG
                if "api" in error_message and "key" in error_message:
                    return FailureType.MISSING_API_KEY
                return FailureType.KEY_ERROR

            if base_type == FailureType.DISK_FULL:
                # Refine OSError
                if "disk" in error_message or "space" in error_message:
                    return FailureType.DISK_FULL
                if "network" in error_message or "connection" in error_message:
                    return FailureType.NETWORK_ERROR
                if "permission" in error_message:
                    return FailureType.PERMISSION_DENIED
                if "file not found" in error_message:
                    return FailureType.MISSING_FILE

            return base_type

        # Check message for common patterns
        if "api key" in error_message or "token" in error_message:
            return FailureType.MISSING_API_KEY

        if "rate limit" in error_message or "too many requests" in error_message:
            return FailureType.RATE_LIMIT

        if "timeout" in error_message or "timed out" in error_message:
            return FailureType.TIMEOUT

        if "not found" in error_message and "file" not in error_message:
            return FailureType.MISSING_DATA

        if "no such file" in error_message or "cannot find" in error_message:
            return FailureType.MISSING_FILE

        if "permission denied" in error_message or "access denied" in error_message:
            return FailureType.PERMISSION_DENIED

        if "out of memory" in error_message or "memory error" in error_message:
            return FailureType.OUT_OF_MEMORY

        if "service unavailable" in error_message or "503" in error_message:
            return FailureType.SERVICE_UNAVAILABLE

        if "invalid" in error_message or "malformed" in error_message:
            return FailureType.INVALID_INPUT

        # Default to unknown
        logger.warning(f"[Classifier] Unknown failure type: {exception_type}: {error_message}")
        return FailureType.UNKNOWN
