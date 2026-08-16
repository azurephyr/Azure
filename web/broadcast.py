"""
WebSocket broadcast helper for Azure Operating Platform.

Usage from any part of the bot:
    from azure.web.broadcast import broadcast_event
    await broadcast_event("moderation_action", {"user": "x", "action": "warn"})

Convenience helpers for common events:
    from azure.web.broadcast import broadcast_message, broadcast_moderation
    from azure.web.broadcast import broadcast_health, broadcast_config
    from azure.web.broadcast import broadcast_execution
"""

import logging
import time as _time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("azure.web.broadcast")

_app: "FastAPI | None" = None


def set_app(app: "FastAPI") -> None:
    """Register the FastAPI app instance so broadcast_event can reach the manager."""
    global _app
    _app = app


def _get_manager():
    if _app is None:
        return None
    return getattr(_app.state, "ws_manager", None)


async def broadcast_event(event_type: str, data: dict | None = None) -> None:
    """Send a structured event to every connected WebSocket client.

    Parameters
    ----------
    event_type : str
        A short identifier such as ``"message_processed"``,
        ``"moderation_action"``, ``"health_change"``, ``"config_update"``.
    data : dict, optional
        Arbitrary JSON-serialisable payload.  The envelope sent to clients is::

            {"type": event_type, "data": data, "ts": <unix timestamp>}
    """
    manager = _get_manager()
    if manager is None:
        return
    connections = getattr(manager, "active_connections", None)
    if not connections:
        return

    payload = {
        "type": event_type,
        "data": data or {},
        "ts": _time.time(),
    }

    try:
        guild_id = data.get("guild_id") if isinstance(data, dict) else None
        await manager.broadcast(payload, guild_id=str(guild_id) if guild_id else None)
    except Exception as exc:
        logger.warning("broadcast_event(%s) failed: %s", event_type, exc)


# ---------------------------------------------------------------------------
# Convenience helpers for common bot events
# ---------------------------------------------------------------------------

async def broadcast_message(author: str, channel: str, content: str,
                            guild: str = "", cached: bool = False,
                            guild_id: str = "") -> None:
    """Broadcast a Discord message event to all dashboard clients."""
    await broadcast_event("DISCORD_MESSAGE", {
        "author": author,
        "channel": channel,
        "content": content,
        "guild": guild,
        "guild_id": guild_id,
        "cached": cached,
    })


async def broadcast_moderation(user: str, action: str, reason: str = "",
                               confidence: float = 0.0,
                               message_id: str = "", guild_id: str = "") -> None:
    """Broadcast a moderation action (warn, mute, ban, etc.)."""
    await broadcast_event("MODERATION_ACTION", {
        "user": user,
        "action": action,
        "reason": reason,
        "confidence": confidence,
        "message_id": message_id,
        "guild_id": guild_id,
    })


async def broadcast_health(status: str = "ok", memory_mb: float = 0,
                           errors: int = 0, **kwargs) -> None:
    """Broadcast a health status change."""
    await broadcast_event("HEALTH_CHANGE", {
        "status": status,
        "memory_mb": memory_mb,
        "errors": errors,
        **kwargs,
    })


async def broadcast_config(setting: str, old_value=None, new_value=None,
                           user: str = "system") -> None:
    """Broadcast a configuration update."""
    await broadcast_event("CONFIG_UPDATE", {
        "setting": setting,
        "old_value": old_value,
        "new_value": new_value,
        "user": user,
    })


async def broadcast_execution(user: str, action: str, phase: str = "",
                              status: str = "info", message: str = "",
                              guild: str = "", **kwargs) -> None:
    """Broadcast an execution telemetry event (pipeline step, LLM call, etc.)."""
    await broadcast_event("execution_telemetry", {
        "user": user,
        "event": {
            "action": action,
            "phase": phase,
            "status": status,
            "message": message,
            **kwargs,
        },
        "guild": guild,
    })


async def broadcast_system_metrics(total_messages: int = 0,
                                   active_users: int = 0,
                                   llm_calls: int = 0, **kwargs) -> None:
    """Broadcast system-wide metric updates."""
    await broadcast_event("system_metrics", {
        "total_messages": total_messages,
        "active_users": active_users,
        "llm_calls": llm_calls,
        **kwargs,
    })


async def broadcast_emergency_stop(user: str = "admin") -> None:
    """Broadcast an emergency stop trigger."""
    await broadcast_event("EMERGENCY_STOP_TRIGGERED", {
        "user": user,
        "ts": _time.time(),
    })
