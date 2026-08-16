"""Small, dependency-free event primitives used by Azure Core."""

from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

Handler = Callable[["Event"], Any | Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class Event:
    """An immutable event entering or leaving the core."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "core"
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class EventBus:
    """Minimal async-capable in-process event bus."""

    def __init__(self) -> None:
        self._handlers: defaultdict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_name: str, handler: Handler) -> None:
        if handler not in self._handlers[event_name]:
            self._handlers[event_name].append(handler)

    def unsubscribe(self, event_name: str, handler: Handler) -> None:
        handlers = self._handlers.get(event_name, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: Event) -> None:
        for handler in tuple(self._handlers.get(event.name, ())):
            result = handler(event)
            if inspect.isawaitable(result):
                await result
