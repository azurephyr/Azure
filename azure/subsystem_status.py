"""Track which subsystems loaded successfully."""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("azure.subsystem_status")

@dataclass
class SubsystemInfo:
    """Status information for a single subsystem.

    Attributes:
        name: Unique name identifying the subsystem.
        status: Current health status — one of "ok", "degraded", or "unavailable".
        error: Optional error message when the subsystem is not healthy.
    """
    name: str
    status: str = "unknown"  # "ok", "degraded", "unavailable"
    error: str = ""

class SubsystemRegistry:
    """Registry tracking subsystem health.

    Maintains a dictionary of ``SubsystemInfo`` objects keyed by subsystem
    name.  Provides methods to register, query, and log subsystem status.
    """

    def __init__(self) -> None:
        """Initialize an empty subsystem registry."""
        self._subsystems: dict[str, SubsystemInfo] = {}

    def register(self, name: str, status: str = "ok", error: str = "") -> None:
        """Register or update a subsystem's health status.

        Args:
            name: Unique name identifying the subsystem.
            status: Health status — "ok", "degraded", or "unavailable".
            error: Optional error message explaining why the subsystem is unhealthy.
        """
        self._subsystems[name] = SubsystemInfo(name=name, status=status, error=error)
        if status != "ok":
            logger.warning("[subsystem] %s: %s%s", name, status, f" ({error})" if error else "")

    def is_available(self, name: str) -> bool:
        """Check whether a registered subsystem is healthy.

        Args:
            name: The subsystem name to query.

        Returns:
            True if the subsystem exists and has status "ok", False otherwise.
        """
        info = self._subsystems.get(name)
        return info is not None and info.status == "ok"

    def get_summary(self) -> dict[str, str]:
        """Return a mapping of subsystem names to their current status.

        Returns:
            Dictionary mapping subsystem name to status string.
        """
        return {name: info.status for name, info in self._subsystems.items()}

    def log_summary(self) -> None:
        """Log a summary of all subsystem health statuses.

        Logs the count of available vs total subsystems at INFO level.
        Any unhealthy subsystems are logged at WARNING level with their
        error details.
        """
        available = sum(1 for i in self._subsystems.values() if i.status == "ok")
        total = len(self._subsystems)
        logger.info("[subsystem] %d/%d subsystems available", available, total)
        for name, info in self._subsystems.items():
            if info.status != "ok":
                logger.warning("[subsystem]   %s: %s — %s", name, info.status, info.error)

registry = SubsystemRegistry()
