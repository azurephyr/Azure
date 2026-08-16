"""Phase A — 20 scenarios across chat, moderation, management, attention, resilience."""

from dataclasses import dataclass, field


@dataclass
class Scenario:
    """A single simulation scenario."""
    id: str
    name: str
    subsystem: str
    users: list
    messages: list
    expected: dict
    setup: callable = None
    tags: list = field(default_factory=list)
    mode: str = "direct"
    responses: dict | None = None  # per-scenario agent response overrides
    lenient: bool = False  # relax response_contains checks (for real-agent mode)
