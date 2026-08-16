"""
Azure Moderation: Phase Definitions & Permission Tiers

Phases enforce a trust escalation path with emergency rollback support:

  DRY_RUN          -> classify only, no actions (observation mode)
  REACTIVE_LIMITED -> delete messages, warn users, short timeouts only
  REACTIVE_FULL    -> all moderation actions including kick/ban/lockdown

Phase transitions are BIDIRECTIONAL for emergency rollback.
If something breaks, you can always downgrade to dry_run immediately.

Allowed actions per phase:
┌─────────────────┬──────────┬─────────────────┬─────────────┐
│ Action          │ dry_run  │ reactive_limited│ reactive_full│
├─────────────────┼──────────┼─────────────────┼─────────────┤
│ Classify/Log    │    ✓     │       ✓         │      ✓      │
│ Delete message  │    ✗     │       ✓         │      ✓      │
│ Warn user       │    ✗     │       ✓         │      ✓      │
│ Timeout (≤5min) │    ✗     │       ✓         │      ✓      │
│ Timeout (>5min) │    ✗     │       ✗         │      ✓      │
│ Kick            │    ✗     │       ✗         │      ✓      │
│ Ban             │    ✗     │       ✗         │      ✓      │
│ Channel lockdown│    ✗     │       ✗         │      ✓      │
└─────────────────┴──────────┴─────────────────┴─────────────┘
"""

from __future__ import annotations

from enum import Enum


class ModerationPhase(Enum):
    DRY_RUN = "dry_run"
    REACTIVE_LIMITED = "reactive_limited"
    REACTIVE_FULL = "reactive_full"


# Phase transition rules: BIDIRECTIONAL for emergency rollback support
# You can always downgrade to dry_run or reactive_limited from any higher phase
VALID_TRANSITIONS = {
    ModerationPhase.DRY_RUN: {ModerationPhase.REACTIVE_LIMITED},
    ModerationPhase.REACTIVE_LIMITED: {ModerationPhase.DRY_RUN, ModerationPhase.REACTIVE_FULL},
    ModerationPhase.REACTIVE_FULL: {ModerationPhase.DRY_RUN, ModerationPhase.REACTIVE_LIMITED},
}


# Maximum timeout duration (minutes) per phase
MAX_TIMEOUT_MINUTES = {
    ModerationPhase.DRY_RUN: 0,
    ModerationPhase.REACTIVE_LIMITED: 5,
    ModerationPhase.REACTIVE_FULL: 2880,  # 48 hours (Discord max)
}


# Allowed action types per phase
ALLOWED_ACTIONS = {
    ModerationPhase.DRY_RUN: {"log"},
    ModerationPhase.REACTIVE_LIMITED: {"log", "delete", "warn", "timeout"},
    ModerationPhase.REACTIVE_FULL: {"log", "delete", "warn", "timeout", "kick", "ban", "report"},
}


def can_transition(from_phase: ModerationPhase, to_phase: ModerationPhase) -> bool:
    """Check if a phase transition is valid."""
    if from_phase == to_phase:
        return True
    return to_phase in VALID_TRANSITIONS.get(from_phase, set())


def action_allowed(phase: ModerationPhase, action: str) -> bool:
    """Check if an action type is permitted in the current phase."""
    return action.lower() in ALLOWED_ACTIONS.get(phase, set())


def max_timeout_minutes(phase: ModerationPhase) -> int:
    """Return the maximum timeout duration allowed in this phase."""
    return MAX_TIMEOUT_MINUTES.get(phase, 0)
