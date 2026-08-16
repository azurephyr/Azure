"""Deterministic authorization and moderation policy primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str
    rule: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)


class Policy(Protocol):
    name: str

    def evaluate(self, context: dict[str, Any]) -> Decision:
        ...


class PolicyEngine:
    """Evaluate policies in order; the first explicit denial wins."""

    def __init__(self, policies: list[Policy] | None = None) -> None:
        self._policies = list(policies or [])

    def add(self, policy: Policy) -> None:
        self._policies.append(policy)

    def evaluate(self, context: dict[str, Any]) -> Decision:
        for policy in self._policies:
            decision = policy.evaluate(context)
            if not decision.allowed:
                return decision
        return Decision(allowed=True, reason="No policy denied the action")
