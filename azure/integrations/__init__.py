"""
Azure Integration Framework - extensible integration points.
"""
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("azure.integrations")


class IntegrationResult:
    """Structured result for !azure_integrations (command_handler expects attributes)."""

    __slots__ = ("success", "text", "error", "data")

    def __init__(
        self,
        success: bool = True,
        text: str = "",
        error: str = "",
        data: Any = None,
    ):
        self.success = success
        self.text = text
        self.error = error
        self.data = data

    def __bool__(self) -> bool:
        return self.success


class BaseIntegration(ABC):
    """Base class for all integrations."""

    def __init__(self, name: str, config: dict[str, Any] | None = None):
        self.name = name
        self.config = config or {}
        self._enabled = True

    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize the integration. Return True if successful."""
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """Clean shutdown of the integration."""
        ...

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def disable(self) -> None:
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    def query(self, command: str, **kwargs: Any) -> Any:
        """Optional command surface for !azure_integrations."""
        raise NotImplementedError(f"Integration '{self.name}' does not implement query()")


class IntegrationRegistry:
    """Registry for managing multiple integrations.

    Public API is consumed by bot/handlers/command_handler.py:
      - get_help_text()
      - is_available(name)
      - query(connector, command, **kwargs)
      - list_available()
    """

    def __init__(self):
        self._integrations: dict[str, BaseIntegration] = {}

    def register(self, integration: BaseIntegration) -> None:
        self._integrations[integration.name] = integration
        logger.info(f"Registered integration: {integration.name}")

    def get(self, name: str) -> BaseIntegration | None:
        return self._integrations.get(name)

    def list(self) -> "list[str]":
        return list(self._integrations.keys())

    def list_available(self) -> "list[str]":
        return [
            name
            for name, integ in self._integrations.items()
            if getattr(integ, "is_enabled", True)
        ]

    def is_available(self, name: str) -> bool:
        integ = self._integrations.get(name)
        if integ is None:
            return False
        return bool(getattr(integ, "is_enabled", True))

    def get_help_text(self) -> str:
        available = self.list_available()
        if not available:
            return (
                "**Integration Hub**\n"
                "No connectors registered.\n"
                "Use `!azure_integrations <connector> <command>` once connectors are loaded."
            )
        lines = ["**Available Integrations:**"]
        for name in available:
            integ = self._integrations[name]
            lines.append(f"• `{name}` — {type(integ).__name__}")
        lines.append("")
        lines.append("Usage: `!azure_integrations <connector> <command> [key=value ...]`")
        return "\n".join(lines)

    def query(self, connector: str, command: str, **kwargs: Any) -> IntegrationResult:
        integ = self._integrations.get(connector)
        if integ is None:
            return IntegrationResult(success=False, error=f"Unknown connector: {connector}")
        if not getattr(integ, "is_enabled", True):
            return IntegrationResult(success=False, error=f"Connector disabled: {connector}")
        if not command:
            return IntegrationResult(success=False, error="Missing command")
        if hasattr(integ, "query"):
            try:
                raw = integ.query(command, **kwargs)
                if isinstance(raw, IntegrationResult):
                    return raw
                if isinstance(raw, dict):
                    if raw.get("error"):
                        return IntegrationResult(success=False, error=str(raw["error"]), data=raw)
                    return IntegrationResult(
                        success=True,
                        text=str(raw.get("text") or raw.get("message") or raw),
                        data=raw,
                    )
                return IntegrationResult(success=True, text=str(raw), data=raw)
            except NotImplementedError as e:
                return IntegrationResult(success=False, error=str(e))
            except Exception as e:
                logger.error(f"Integration query failed ({connector}/{command}): {e}")
                return IntegrationResult(success=False, error=str(e))
        return IntegrationResult(
            success=False,
            error=f"Connector '{connector}' has no query interface",
        )

    async def initialize_all(self) -> dict[str, bool]:
        results = {}
        for name, integration in self._integrations.items():
            try:
                results[name] = await integration.initialize()
            except Exception as e:
                logger.error(f"Failed to initialize integration {name}: {e}")
                results[name] = False
        return results

    async def shutdown_all(self) -> None:
        for name, integration in self._integrations.items():
            try:
                await integration.shutdown()
            except Exception as e:
                logger.error(f"Failed to shutdown integration {name}: {e}")


# Aliases for backward compatibility
IntegrationHub = IntegrationRegistry


def create_integration_hub() -> IntegrationHub:
    return IntegrationHub()
