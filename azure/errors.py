"""Structured error types for the Azure bot."""

from __future__ import annotations


class AzureError(Exception):
    """Base error for all Azure bot errors."""
    pass


class LLMError(AzureError):
    """LLM API call failed."""

    def __init__(self, provider: str, message: str, status_code: int | None = None):
        """Initialize an LLMError.

        Args:
            provider: Name of the LLM provider (e.g. "openai", "llama").
            message: Human-readable error description.
            status_code: Optional HTTP status code from the API response.
        """
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"{provider}: {message}")


class RateLimitError(AzureError):
    """Rate limit exceeded."""

    def __init__(self, retry_after: float = 0):
        """Initialize a RateLimitError.

        Args:
            retry_after: Seconds to wait before retrying the request.
        """
        self.retry_after = retry_after
        super().__init__(f"Rate limited, retry after {retry_after}s")


class ToolExecutionError(AzureError):
    """Tool execution failed."""

    def __init__(self, tool_name: str, message: str):
        """Initialize a ToolExecutionError.

        Args:
            tool_name: Name of the tool that failed.
            message: Human-readable error description.
        """
        self.tool_name = tool_name
        super().__init__(f"Tool {tool_name} failed: {message}")


class ModerationError(AzureError):
    """Moderation action failed."""
    pass


class DatabaseError(AzureError):
    """Database operation failed."""
    pass
