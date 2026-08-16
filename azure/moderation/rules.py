"""Simple, deterministic moderation rules with no external services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True, slots=True)
class RuleResult:
    matched: bool
    action: str = "none"
    reason: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    check: Callable[[str], bool]
    action: str
    reason: str

    def evaluate(self, content: str) -> RuleResult:
        matched = self.check(content)
        return RuleResult(matched, self.action if matched else "none", self.reason if matched else "")


class RuleEngine:
    def __init__(self, rules: list[Rule] | None = None) -> None:
        self.rules = list(rules or [])

    def add(self, rule: Rule) -> None:
        self.rules.append(rule)

    def evaluate(self, content: str) -> list[RuleResult]:
        return [result for rule in self.rules if (result := rule.evaluate(content)).matched]
