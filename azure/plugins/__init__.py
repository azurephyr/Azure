"""
Azure Plugin Framework - extensible plugin system for bot functionality.
"""
import contextlib
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("azure.plugins")


class BasePlugin(ABC):
    """Base class for all plugins."""

    name: str = "unnamed"
    version: str = "0.0.0"
    description: str = ""

    def __init__(self, bot=None, config: dict[str, Any] | None = None):
        self.bot = bot
        self.config = config or {}
        self._loaded = False
        self._enabled = True

    @abstractmethod
    async def on_load(self) -> None:
        """Called when plugin is loaded."""
        ...

    @abstractmethod
    async def on_unload(self) -> None:
        """Called when plugin is unloaded."""
        ...

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False


class PluginManager:
    """Manages plugin lifecycle.

    Public API is consumed by:
      - bot/handlers/command_handler.py (!azure_plugin)
      - bot/handlers/message_handler.py (handle_message)
      - bot/discord_bot_v1.py (shutdown_all)
    """

    def __init__(self, bot=None):
        self.bot = bot
        self._plugins: dict[str, BasePlugin] = {}
        self._enabled: dict[str, bool] = {}

    def register(self, name: str, plugin: BasePlugin) -> None:
        self._plugins[name] = plugin
        self._enabled[name] = True
        if not getattr(plugin, "name", None) or plugin.name == "unnamed":
            plugin.name = name
        logger.info(f"Registered plugin: {name}")

    def unregister(self, name: str) -> None:
        self._plugins.pop(name, None)
        self._enabled.pop(name, None)
        logger.info(f"Unregistered plugin: {name}")

    def get(self, name: str) -> BasePlugin | None:
        return self._plugins.get(name)

    def list_plugins(self) -> list[dict[str, Any]]:
        """Return plugin metadata dicts expected by !azure_plugin list."""
        out: list[dict[str, Any]] = []
        for name, plugin in self._plugins.items():
            enabled = self._enabled.get(name, True) and getattr(plugin, "is_enabled", True)
            out.append({
                "name": name,
                "version": getattr(plugin, "version", "0.0.0") or "0.0.0",
                "description": getattr(plugin, "description", "") or "",
                "enabled": bool(enabled),
                "loaded": bool(getattr(plugin, "is_loaded", False)),
            })
        return out

    def enable(self, name: str) -> bool:
        plugin = self._plugins.get(name)
        if not plugin:
            return False
        self._enabled[name] = True
        if hasattr(plugin, "enable"):
            try:
                plugin.enable()
            except Exception as e:
                logger.error(f"Plugin {name} enable() failed: {e}")
        return True

    def disable(self, name: str) -> bool:
        plugin = self._plugins.get(name)
        if not plugin:
            return False
        self._enabled[name] = False
        if hasattr(plugin, "disable"):
            try:
                plugin.disable()
            except Exception as e:
                logger.error(f"Plugin {name} disable() failed: {e}")
        return True

    def reload(self, name: str | None = None) -> None:
        """Best-effort reload. Without installed plugin packages this is a no-op reset."""
        targets = [name] if name else list(self._plugins.keys())
        for n in targets:
            plugin = self._plugins.get(n)
            if not plugin:
                continue
            try:
                # Sync path used by Discord command (no await in command handler)
                if getattr(plugin, "_loaded", False) and hasattr(plugin, "on_unload"):
                    # Fire-and-forget unload if coroutine not awaited here
                    result = plugin.on_unload()
                    if hasattr(result, "close"):
                        with contextlib.suppress(Exception):
                            result.close()
                plugin._loaded = False
                result = plugin.on_load()
                if hasattr(result, "close"):
                    with contextlib.suppress(Exception):
                        result.close()
                plugin._loaded = True
                self._enabled[n] = True
                logger.info(f"Reloaded plugin: {n}")
            except Exception as e:
                logger.error(f"Failed to reload plugin {n}: {e}")

    async def load_all(self) -> None:
        for name, plugin in self._plugins.items():
            try:
                await plugin.on_load()
                plugin._loaded = True
                self._enabled[name] = True
                logger.info(f"Loaded plugin: {name}")
            except Exception as e:
                logger.error(f"Failed to load plugin {name}: {e}")

    def handle_message(self, message: Any, context: dict[str, Any]) -> str | None:
        for name, plugin in self._plugins.items():
            if not self._enabled.get(name, True):
                continue
            if not getattr(plugin, "_loaded", False):
                continue
            if not getattr(plugin, "is_enabled", True):
                continue
            if hasattr(plugin, "handle_message"):
                try:
                    response = plugin.handle_message(message, context)
                    if response:
                        return response
                except Exception as e:
                    logger.error(f"Failed handling message in plugin {name}: {e}")
        return None

    async def unload_all(self) -> None:
        for name, plugin in self._plugins.items():
            try:
                await plugin.on_unload()
                plugin._loaded = False
                logger.info(f"Unloaded plugin: {name}")
            except Exception as e:
                logger.error(f"Failed to unload plugin {name}: {e}")

    def shutdown_all(self) -> None:
        """Sync shutdown hook used by bot graceful shutdown."""
        for name, plugin in list(self._plugins.items()):
            try:
                if hasattr(plugin, "on_unload"):
                    result = plugin.on_unload()
                    # If coroutine returned without running, close it to avoid warnings
                    if hasattr(result, "close"):
                        with contextlib.suppress(Exception):
                            result.close()
                plugin._loaded = False
                logger.info(f"Shutdown plugin: {name}")
            except Exception as e:
                logger.error(f"Failed to shutdown plugin {name}: {e}")
