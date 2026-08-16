"""
Shared types for Discord management tools.
"""
from dataclasses import dataclass


@dataclass
class StepResult:
    success: bool
    action: str
    name: str = ""
    detail: str = ""
    error: str = ""
    target_id: int = 0
    before_state: dict | None = None
    after_state: dict | None = None
