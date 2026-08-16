"""
Azure Structured Logger

Replaces scattered print() calls with structured JSON-lines logging.
Supports file rotation, log levels, and module-level filtering.

Usage:
    from azure.logger import get_logger
    log = get_logger("moderation")
    log.info("message processed", user_id="123", action="delete")
    log.warning("rate limit hit", bucket="delete", count=5)
    log.error("action failed", error=str(e), user_id="123")
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path


class StructuredLogger:
    """JSON-lines logger with level filtering and file output."""

    LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "CRITICAL": 50}

    def __init__(self, name: str, log_dir: Path | None = None,
                 level: str = "INFO", to_file: bool = True, to_stdout: bool = True):
        self.name = name
        self.level = self.LEVELS.get(level.upper(), 20)
        self.to_file = to_file
        self.to_stdout = to_stdout

        if log_dir:
            self.log_dir = Path(log_dir)
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._log_file = self.log_dir / f"{name}.jsonl"
        else:
            self._log_file = None

    def _emit(self, level: str, message: str, **kwargs):
        if self.LEVELS.get(level, 0) < self.level:
            return

        record = {
            "ts": datetime.now(UTC).isoformat(),
            "level": level,
            "module": self.name,
            "message": message,
            **kwargs,
        }
        line = json.dumps(record, ensure_ascii=False)

        if self.to_stdout:
            print(line, flush=True)

        if self.to_file and self._log_file:
            try:
                with open(self._log_file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                print(line, flush=True)  # fallback to stdout

    def debug(self, message: str, **kwargs): self._emit("DEBUG", message, **kwargs)
    def info(self, message: str, **kwargs): self._emit("INFO", message, **kwargs)
    def warning(self, message: str, **kwargs): self._emit("WARN", message, **kwargs)
    def error(self, message: str, **kwargs): self._emit("ERROR", message, **kwargs)
    def critical(self, message: str, **kwargs): self._emit("CRITICAL", message, **kwargs)

    def exception(self, message: str, exc: Exception, **kwargs):
        """Log an exception with traceback."""
        import traceback
        self._emit("ERROR", message, error=str(exc), traceback=traceback.format_exc(), **kwargs)


_loggers: dict[str, StructuredLogger] = {}
_default_log_dir: Path | None = None


def set_log_dir(path: Path | str):
    global _default_log_dir
    _default_log_dir = Path(path)


def get_logger(name: str, level: str | None = None) -> StructuredLogger:
    """Get or create a named logger."""
    if name not in _loggers:
        env_level = os.environ.get("AZURE_LOG_LEVEL", "INFO")
        _loggers[name] = StructuredLogger(
            name=name,
            log_dir=_default_log_dir,
            level=level or env_level,
        )
    return _loggers[name]
