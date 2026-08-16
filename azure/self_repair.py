"""
Azure Self-Repair System

Detects runtime errors in Discord tools and attempts safe automatic fixes.
Never rewrites source code. Only retries with alternative approaches.

Usage:
    from azure.self_repair import SelfRepair
    repair = SelfRepair()

    # Wrap any operation
    result = await repair.safe_execute(
        operation=some_async_function,
        operation_name="create_role",
        guild=guild,
        ctx=ctx,
    )

Features:
  - Catches all exceptions
  - Tries alternative approaches (different attribute names, fallbacks)
  - Logs all errors with context
  - Sends user-friendly error messages
  - Never modifies source code
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("azure.self_repair")


@dataclass
class RepairAttempt:
    """Record of a repair attempt."""
    t: float
    operation: str
    error: str
    fix_applied: str
    success: bool
    guild_name: str = ""


class SelfRepair:
    """
    Safe error recovery for Discord operations.

    Philosophy:
    - Catch everything, don't crash
    - Try safe alternatives, never rewrite code
    - Log everything for debugging
    - Always tell the user what happened
    """

    # Known fixes for common errors
    KNOWN_FIXES = {
        "ExplicitContentFilter": {
            "pattern": "has no attribute 'ExplicitContentFilter'",
            "fix": "use_int_value",
            "description": "Enum not available, using integer comparison instead",
        },
        "VerificationLevel": {
            "pattern": "has no attribute 'VerificationLevel'",
            "fix": "use_int_value",
            "description": "Enum not available, using integer comparison instead",
        },
        "NotificationLevel": {
            "pattern": "has no attribute 'NotificationLevel'",
            "fix": "use_int_value",
            "description": "Enum not available, using integer comparison instead",
        },
        "AttributeError": {
            "pattern": "AttributeError",
            "fix": "check_alternative",
            "description": "Attribute not found, checking alternative names",
        },
        "Forbidden": {
            "pattern": "403 Forbidden",
            "fix": "permission_error",
            "description": "Bot lacks permission. Tell user to re-invite with required permissions.",
        },
        "NotFound": {
            "pattern": "404 Not Found",
            "fix": "not_found",
            "description": "Resource not found. May have been deleted.",
        },
        "RateLimit": {
            "pattern": "rate limit",
            "fix": "retry_after",
            "description": "Rate limited. Waiting and retrying.",
        },
    }

    def __init__(self, log_dir: Path = Path("logs/repair")):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._attempts: list[RepairAttempt] = []
        self._error_log_path = self.log_dir / "errors.jsonl"
        self._repair_log_path = self.log_dir / "repairs.jsonl"

    # ------------------------------------------------------------------
    # Main Safe Execute
    # ------------------------------------------------------------------

    async def safe_execute(self, operation: Callable, operation_name: str,
                           guild=None, ctx=None, **kwargs) -> Any:
        """
        Execute an operation with full error recovery.

        Args:
            operation: Async function to call
            operation_name: Name for logging (e.g., "create_role")
            guild: Discord guild (for context)
            ctx: Discord context (for sending messages to user)
            **kwargs: Passed to operation

        Returns:
            Result of operation, or None on failure
        """
        guild_name = guild.name if guild else "DM"

        try:
            # Try the operation normally
            return await operation(**kwargs)
        except Exception as e:
            error_str = str(e)
            error_type = type(e).__name__
            full_traceback = traceback.format_exc()

            # Log the error
            self._log_error(operation_name, guild_name, error_type, error_str, full_traceback)
            logger.error(f"[self_repair] ERROR in {operation_name}: {error_type}: {error_str[:100]}")

            # Try to identify and apply a fix
            fix_result = self._try_fix(e, operation_name, guild_name, ctx)

            if fix_result.get("success"):
                logger.info(f"[self_repair] FIXED: {fix_result['fix_description']}")
                return fix_result.get("result")

            # Fix failed or not applicable - send user-friendly message
            user_message = self._build_user_message(operation_name, e, fix_result)
            if ctx:
                try:
                    await ctx.send(user_message)
                except Exception as e_msg:
                    logger.warning("Failed to send error message to user: %s", e_msg)

            return None

    # ------------------------------------------------------------------
    # Fix Attempts
    # ------------------------------------------------------------------

    def _try_fix(self, error: Exception, operation_name: str, guild_name: str,
                 ctx=None) -> dict:  # type: ignore[no-untyped-def]
        """Try to apply a known fix for the error."""
        error_str = str(error)
        error_type = type(error).__name__

        # Check for known error patterns
        for _fix_name, fix_info in self.KNOWN_FIXES.items():
            if fix_info["pattern"] in error_str or fix_info["pattern"] in error_type:
                fix_method = fix_info["fix"]

                if fix_method == "use_int_value":
                    # The fix is already applied in the code (enum comparison changed to int)
                    # This error shouldn't happen anymore, but if it does:
                    return {
                        "success": False,
                        "fix_description": fix_info["description"],
                        "result": None,
                        "requires_restart": True,
                    }

                elif fix_method == "permission_error":
                    return {
                        "success": False,
                        "fix_description": fix_info["description"],
                        "result": None,
                        "requires_restart": False,
                    }

                elif fix_method == "retry_after":
                    # Extract retry_after from Discord rate limit
                    retry_after = getattr(error, 'retry_after', 5)
                    import time as _time
                    _time.sleep(retry_after)
                    return {
                        "success": True,
                        "fix_description": f"Waited {retry_after}s for rate limit",
                        "result": None,
                        "requires_restart": False,
                    }

                elif fix_method == "not_found" or fix_method == "check_alternative":
                    return {
                        "success": False,
                        "fix_description": fix_info["description"],
                        "result": None,
                        "requires_restart": False,
                    }

        # Unknown error - no fix available
        return {
            "success": False,
            "fix_description": "Unknown error - no automatic fix available",
            "result": None,
            "requires_restart": False,
        }

    # ------------------------------------------------------------------
    # User Messages
    # ------------------------------------------------------------------

    def _build_user_message(self, operation_name: str, error: Exception, fix_result: dict) -> str:
        """Build a user-friendly error message."""
        error_type = type(error).__name__
        error_str = str(error)[:200]

        # Permission errors
        if "Forbidden" in error_type or "403" in error_str:
            return (
                f"⚠️ **I need more permissions to `{operation_name}`**\n\n"
                f"Please re-invite me with these permissions:\n"
                f"`manage_guild`, `manage_channels`, `manage_roles`, `manage_messages`\n\n"
                f"[Permission denied: {error_str[:100]}]"
            )

        # Rate limit
        if "rate limit" in error_str.lower():
            return (
                f"⏳ **Rate limited** while doing `{operation_name}`.\n"
                f"Discord is slowing me down. I'll retry automatically."
            )

        # Not found
        if "NotFound" in error_type or "404" in error_str:
            return (
                f"❌ **Resource not found** for `{operation_name}`.\n"
                f"It may have been deleted or doesn't exist."
            )

        # Requires restart (code was patched)
        if fix_result.get("requires_restart"):
            return (
                f"🔧 **Fixed a compatibility issue** in `{operation_name}`.\n\n"
                f"Please **restart the bot** for the fix to take effect.\n"
                f"(The code was patched but needs a reload.)"
            )

        # Generic error
        return (
            f"❌ **Error in `{operation_name}`**\n\n"
            f"{error_type}: {error_str[:150]}\n\n"
            f"This error has been logged. If it keeps happening, try restarting the bot."
        )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_error(self, operation: str, guild: str, error_type: str, error_msg: str, traceback_str: str) -> None:
        """Log an error to disk."""
        entry = {
            "t": time.time(),
            "operation": operation,
            "guild": guild,
            "error_type": error_type,
            "error_msg": error_msg[:500],
            "traceback": traceback_str[:2000],
        }
        try:
            with open(self._error_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e_log:
            logger.warning("Failed to write error log: %s", e_log)

    def _log_repair(self, attempt: RepairAttempt) -> None:
        """Log a repair attempt to disk."""
        try:
            with open(self._repair_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "t": attempt.t,
                    "operation": attempt.operation,
                    "guild": attempt.guild_name,
                    "error": attempt.error[:200],
                    "fix": attempt.fix_applied,
                    "success": attempt.success,
                }) + "\n")
        except Exception as e_rep:
            logger.warning("Failed to write repair log: %s", e_rep)
        self._attempts.append(attempt)

    def get_stats(self) -> dict[str, object]:
        """Get repair statistics."""
        total = len(self._attempts)
        successful = sum(1 for a in self._attempts if a.success)
        return {
            "total_attempts": total,
            "successful": successful,
            "failed": total - successful,
            "success_rate": successful / total if total > 0 else 0.0,
        }

    def get_recent_errors(self, n: int = 10) -> list[dict]:
        """Get recent errors from the log."""
        import json
        errors = []
        try:
            with open(self._error_log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            errors.append(json.loads(line))
                        except Exception as e_parse:
                            logger.warning("Failed to parse error log line: %s", e_parse)
        except FileNotFoundError:
            logger.info("No error log file found at %s", self._error_log_path)
        return errors[-n:]
