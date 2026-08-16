"""
GoalState — Upgrade 7: Long-Term Goal Persistence

Defines the dataclasses for goals, subgoals, and blockers.

Goal format:
  {
    "goal_id": "uuid",
    "description": "grow Discord server to 1000 members",
    "status": "active",
    "priority": "high",
    "progress": 0.35,
    "subgoals": [...],
    "blockers": [...],
    "context": {...},
    "created_at": timestamp,
    "updated_at": timestamp,
    "completed_at": timestamp | None
  }

Goal status lifecycle:
  PROPOSED → ACTIVE → COMPLETED | PAUSED | ABANDONED

Priority levels:
  CRITICAL, HIGH, MEDIUM, LOW

Azure becomes proactive instead of purely reactive by tracking goals
and surfacing them when relevant to user conversations.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum


class GoalStatus(StrEnum):
    PROPOSED = "PROPOSED"      # Just created, not yet approved
    ACTIVE = "ACTIVE"          # Currently being pursued
    PAUSED = "PAUSED"          # Temporarily on hold
    COMPLETED = "COMPLETED"    # Done
    ABANDONED = "ABANDONED"    # Explicitly cancelled


class GoalPriority(StrEnum):
    CRITICAL = "CRITICAL"      # Must happen now
    HIGH = "HIGH"              # Important, do soon
    MEDIUM = "MEDIUM"          # Normal priority
    LOW = "LOW"                # Nice to have


@dataclass
class Blocker:
    """Something preventing a goal from progressing."""
    blocker_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str = ""
    severity: GoalPriority = GoalPriority.MEDIUM
    resolved: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Blocker:
        # Handle enum deserialization
        if isinstance(d.get("severity"), str):
            d = {**d, "severity": GoalPriority(d["severity"])}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Subgoal:
    """A milestone or sub-task within a larger goal."""
    subgoal_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str = ""
    status: GoalStatus = GoalStatus.PROPOSED
    progress: float = 0.0        # 0.0 to 1.0
    completed_at: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Subgoal:
        if isinstance(d.get("status"), str):
            d = {**d, "status": GoalStatus(d["status"])}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Goal:
    """
    A long-term goal that Azure is tracking.

    Goals make Azure proactive instead of purely reactive.
    When a user conversation touches on a goal area, Azure can
    surface progress, suggest next steps, or ask for help.
    """
    goal_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str = ""
    status: GoalStatus = GoalStatus.PROPOSED
    priority: GoalPriority = GoalPriority.MEDIUM
    progress: float = 0.0          # 0.0 to 1.0 (aggregate of subgoals)
    subgoals: list[Subgoal] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
    context: dict = field(default_factory=dict)  # server_id, channel_id, etc.
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    # How many times this goal was surfaced in conversation (for relevance tracking)
    surface_count: int = 0
    last_surfaced_at: float | None = None

    def __post_init__(self):
        # Ensure progress is bounded
        self.progress = max(0.0, min(1.0, self.progress))

    def update_progress(self):
        """Recalculate aggregate progress from subgoals."""
        if not self.subgoals:
            return
        total = sum(sg.progress for sg in self.subgoals)
        self.progress = round(total / len(self.subgoals), 3)
        self.updated_at = time.time()

    def add_subgoal(self, description: str) -> Subgoal:
        """Add a new subgoal and update progress."""
        sg = Subgoal(description=description)
        self.subgoals.append(sg)
        self.update_progress()
        return sg

    def add_blocker(self, description: str, severity: GoalPriority = GoalPriority.MEDIUM) -> Blocker:
        """Add a blocker."""
        b = Blocker(description=description, severity=severity)
        self.blockers.append(b)
        self.updated_at = time.time()
        return b

    def resolve_blocker(self, blocker_id: str) -> bool:
        """Mark a blocker as resolved."""
        for b in self.blockers:
            if b.blocker_id == blocker_id:
                b.resolved = True
                self.updated_at = time.time()
                return True
        return False

    def complete_subgoal(self, subgoal_id: str) -> bool:
        """Mark a subgoal as completed."""
        for sg in self.subgoals:
            if sg.subgoal_id == subgoal_id:
                sg.status = GoalStatus.COMPLETED
                sg.progress = 1.0
                sg.completed_at = time.time()
                self.update_progress()
                # Auto-complete goal if all subgoals are done
                if all(s.status == GoalStatus.COMPLETED for s in self.subgoals):
                    self.set_status(GoalStatus.COMPLETED)
                return True
        return False

    def set_status(self, status: GoalStatus):
        """Update goal status and timestamps."""
        self.status = status
        self.updated_at = time.time()
        if status == GoalStatus.COMPLETED:
            self.completed_at = time.time()
            self.progress = 1.0

    def surface(self) -> str:
        """Generate a human-readable summary of this goal."""
        self.surface_count += 1
        self.last_surfaced_at = time.time()

        pct = int(self.progress * 100)
        status_str = self.status.value
        priority_str = self.priority.value

        lines = [f"🎯 **Goal**: {self.description} ({priority_str})"]
        lines.append(f"   Status: {status_str} | Progress: {pct}%")

        if self.subgoals:
            active = [sg for sg in self.subgoals if sg.status != GoalStatus.COMPLETED]
            done = [sg for sg in self.subgoals if sg.status == GoalStatus.COMPLETED]
            if active:
                lines.append(f"   Active subgoals: {len(active)} ({len(done)} completed)")
            elif done:
                lines.append(f"   All {len(done)} subgoals completed")

        active_blockers = [b for b in self.blockers if not b.resolved]
        if active_blockers:
            lines.append(f"   ⚠️ Blockers: {len(active_blockers)}")
            for b in active_blockers[:2]:
                lines.append(f"      - {b.description}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "goal_id": self.goal_id,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "progress": self.progress,
            "subgoals": [sg.to_dict() for sg in self.subgoals],
            "blockers": [b.to_dict() for b in self.blockers],
            "context": self.context,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "surface_count": self.surface_count,
            "last_surfaced_at": self.last_surfaced_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Goal:
        d = dict(d)
        d["status"] = GoalStatus(d["status"])
        d["priority"] = GoalPriority(d["priority"])
        d["subgoals"] = [Subgoal.from_dict(sg) for sg in d.get("subgoals", [])]
        d["blockers"] = [Blocker.from_dict(b) for b in d.get("blockers", [])]
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
