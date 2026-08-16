"""Structured logging with per-request context."""
from __future__ import annotations

import logging
import threading
import uuid

_context = threading.local()

class ContextFilter(logging.Filter):
    """Logging filter that injects per-request context into log records.

    Attaches ``execution_id`` and ``user_id`` attributes to every log record
    so that structured log output can be correlated with individual requests.
    """

    def filter(self, record):
        """Add execution_id and user_id attributes to the log record.

        Args:
            record: The log record to enrich.

        Returns:
            Always returns True to allow the record to pass through.
        """
        record.execution_id = getattr(_context, "execution_id", "none")
        record.user_id = getattr(_context, "user_id", "none")
        return True

def setup_logging(level: int = logging.INFO) -> None:
    """Configure structured logging for the bot.

    Sets up a stream handler with a format that includes timestamps, log
    levels, logger names, and per-request context fields (execution_id,
    user_id).  Clears any existing root handlers before adding the new one.

    Args:
        level: The minimum logging level (default: logging.INFO).
    """
    fmt = "%(asctime)s [%(levelname)s] %(name)s (exec=%(execution_id)s user=%(user_id)s) %(message)s"
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    filt = ContextFilter()
    root.addFilter(filt)
    handler.addFilter(filt)

def set_request_context(execution_id: str | None = None, user_id: str | None = None) -> None:
    """Set per-request context for the current thread's log output.

    Args:
        execution_id: Unique identifier for the current execution.
        user_id: Identifier for the user associated with the request.
    """
    if execution_id:
        _context.execution_id = execution_id
    if user_id:
        _context.user_id = user_id

def clear_request_context() -> None:
    """Clear per-request context for the current thread.

    Should be called at the end of each request to prevent context leakage
    between requests handled by the same thread.
    """
    _context.execution_id = "none"
    _context.user_id = "none"

def generate_execution_id() -> str:
    """Generate a short unique execution ID.

    Returns:
        A 12-character hexadecimal string derived from a UUID4.
    """
    return uuid.uuid4().hex[:12]
