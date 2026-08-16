"""
GoalManager — Upgrade 7: Persistent Goal Storage & Lifecycle

Manages all long-term goals for Azure. Provides CRUD operations,
progress tracking, and relevance scoring for proactive surfacing.

Storage: JSON file in logs/goals/goals.json with in-memory cache.

Usage:
    manager = GoalManager()

    # Create a goal
    goal = manager.create(
        description="grow server to 1000 members",
        priority=GoalPriority.HIGH,
        context={"server_id": "123", "channel_id": "456"}
    )

    # Add subgoals
    manager.add_subgoal(goal.goal_id, "Set up welcome bot")
    manager.add_subgoal(goal.goal_id, "Create invite rewards")

    # Mark progress
    manager.complete_subgoal(goal.goal_id, subgoal_id)

    # Get active goals for a context
    active = manager.get_active_for_context({"server_id": "123"})

    # Surface relevant goals in conversation
    relevant = manager.find_relevant("how do we get more members?")
"""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from pathlib import Path

from .goal_state import Blocker, Goal, GoalPriority, GoalStatus, Subgoal

logger = logging.getLogger("azure.cognition.goal_manager")


class GoalManager:
    """
    Persistent goal manager with relevance scoring.

    Design:
      - All goals stored in logs/goals/goals.json
      - In-memory OrderedDict for fast lookup by goal_id
      - Relevance matching based on keyword overlap between user messages
        and goal descriptions + subgoals
    """

    DEFAULT_LOG_DIR = "logs/goals"
    MAX_GOALS = 100

    def __init__(self, log_dir: str | Path = DEFAULT_LOG_DIR):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.goals_file = self.log_dir / "goals.json"

        # In-memory cache: goal_id -> Goal
        self._cache: OrderedDict[str, Goal] = OrderedDict()
        self._stats = {"created": 0, "updated": 0, "completed": 0, "retrieved": 0}

        self._load()

    # -----------------------------------------------------------------------
    # CRUD
    # -----------------------------------------------------------------------

    def create(self, description: str, priority: GoalPriority = GoalPriority.MEDIUM,
               context: dict | None = None, subgoals: list[str] | None = None) -> Goal:
        """Create a new goal and persist it."""
        goal = Goal(
            description=description,
            priority=priority,
            status=GoalStatus.ACTIVE,
            context=context or {},
        )
        if subgoals:
            for sg_desc in subgoals:
                goal.add_subgoal(sg_desc)

        self._cache[goal.goal_id] = goal
        self._stats["created"] += 1
        self._save()
        return goal

    def get(self, goal_id: str) -> Goal | None:
        """Retrieve a goal by ID."""
        return self._cache.get(goal_id)

    def get_all(self, status: GoalStatus | None = None) -> list[Goal]:
        """Return all goals, optionally filtered by status."""
        goals = list(self._cache.values())
        if status:
            goals = [g for g in goals if g.status == status]
        return goals

    def get_active(self) -> list[Goal]:
        """Return all active goals, sorted by priority."""
        active = [g for g in self._cache.values() if g.status == GoalStatus.ACTIVE]
        # Priority sort: CRITICAL > HIGH > MEDIUM > LOW
        priority_order = {GoalPriority.CRITICAL: 0, GoalPriority.HIGH: 1,
                          GoalPriority.MEDIUM: 2, GoalPriority.LOW: 3}
        active.sort(key=lambda g: (priority_order.get(g.priority, 2), -g.progress))
        return active

    def get_active_for_context(self, context: dict) -> list[Goal]:
        """
        Return active goals matching a context (e.g., server_id).
        Matches if ALL provided context keys match.
        """
        active = self.get_active()
        matches = []
        for g in active:
            if all(g.context.get(k) == v for k, v in context.items()):
                matches.append(g)
        return matches

    def update(self, goal_id: str, **kwargs) -> bool:
        """Update goal fields."""
        goal = self._cache.get(goal_id)
        if not goal:
            return False
        for k, v in kwargs.items():
            if hasattr(goal, k):
                setattr(goal, k, v)
        goal.updated_at = time.time()
        self._stats["updated"] += 1
        self._save()
        return True

    def delete(self, goal_id: str) -> bool:
        """Remove a goal permanently."""
        if goal_id in self._cache:
            del self._cache[goal_id]
            self._save()
            return True
        return False

    # -----------------------------------------------------------------------
    # Subgoal & Blocker helpers
    # -----------------------------------------------------------------------

    def add_subgoal(self, goal_id: str, description: str) -> Subgoal | None:
        """Add a subgoal to a goal."""
        goal = self._cache.get(goal_id)
        if not goal:
            return None
        sg = goal.add_subgoal(description)
        self._save()
        return sg

    def complete_subgoal(self, goal_id: str, subgoal_id: str) -> bool:
        """Mark a subgoal as completed."""
        goal = self._cache.get(goal_id)
        if not goal:
            return False
        ok = goal.complete_subgoal(subgoal_id)
        if ok:
            if all(sg.status == GoalStatus.COMPLETED for sg in goal.subgoals):
                goal.set_status(GoalStatus.COMPLETED)
                self._stats["completed"] += 1
            self._save()
        return ok

    def add_blocker(self, goal_id: str, description: str,
                    severity: GoalPriority = GoalPriority.MEDIUM) -> Blocker | None:
        """Add a blocker to a goal."""
        goal = self._cache.get(goal_id)
        if not goal:
            return None
        b = goal.add_blocker(description, severity)
        self._save()
        return b

    def resolve_blocker(self, goal_id: str, blocker_id: str) -> bool:
        """Resolve a blocker."""
        goal = self._cache.get(goal_id)
        if not goal:
            return False
        ok = goal.resolve_blocker(blocker_id)
        if ok:
            self._save()
        return ok

    # -----------------------------------------------------------------------
    # Relevance scoring
    # -----------------------------------------------------------------------

    def find_relevant(self, query: str, k: int = 3) -> list[tuple[Goal, float]]:
        """
        Find goals relevant to a user query/message.

        Uses simple keyword overlap scoring. Returns top-k by score.
        """
        query_words = set(
            ''.join(c for c in w.lower() if c.isalnum())
            for w in query.split() if len(w) > 3
        )
        query_words.discard("")
        if not query_words:
            return []

        scored = []
        for goal in self._cache.values():
            if goal.status not in (GoalStatus.ACTIVE, GoalStatus.PAUSED):
                continue

            score = self._score_relevance(goal, query_words)
            if score > 0:
                scored.append((goal, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        self._stats["retrieved"] += len(scored)
        return scored[:k]

    def _score_relevance(self, goal: Goal, query_words: set[str]) -> float:
        """Compute relevance score between a goal and query words."""
        # Extract keywords from goal description, subgoals, and context
        goal_text = goal.description.lower()
        for sg in goal.subgoals:
            goal_text += " " + sg.description.lower()
        for k, v in goal.context.items():
            goal_text += " " + str(k).lower() + " " + str(v).lower()

        # Count matching query words (substring match in goal text)
        matches = 0
        for qw in query_words:
            if qw in goal_text:
                matches += 1

        if not matches:
            return 0.0

        # Base score: overlap ratio
        score = matches / max(len(query_words), 1)

        # Boost for high-priority goals
        priority_boost = {
            GoalPriority.CRITICAL: 1.5,
            GoalPriority.HIGH: 1.2,
            GoalPriority.MEDIUM: 1.0,
            GoalPriority.LOW: 0.8,
        }.get(goal.priority, 1.0)

        # Boost for goals with more progress (momentum)
        progress_boost = 1.0 + (goal.progress * 0.5)

        return round(score * priority_boost * progress_boost, 3)

    # -----------------------------------------------------------------------
    # Proactive surfacing
    # -----------------------------------------------------------------------

    def get_proactive_suggestions(self, context: dict | None = None) -> list[str]:
        """
        Get goals that should be proactively surfaced.

        Returns summaries of high-priority active goals with blockers
        or low progress, sorted by urgency.
        """
        goals = self.get_active()
        if context:
            goals = [g for g in goals if all(g.context.get(k) == v for k, v in context.items())]

        suggestions = []
        for g in goals:
            active_blockers = [b for b in g.blockers if not b.resolved]
            if active_blockers or g.progress < 0.3:
                suggestions.append(g.surface())
        return suggestions

    # -----------------------------------------------------------------------
    # Stats
    # -----------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return manager statistics."""
        all_goals = list(self._cache.values())
        return {
            **self._stats,
            "total_goals": len(all_goals),
            "active": len([g for g in all_goals if g.status == GoalStatus.ACTIVE]),
            "completed": len([g for g in all_goals if g.status == GoalStatus.COMPLETED]),
            "paused": len([g for g in all_goals if g.status == GoalStatus.PAUSED]),
            "abandoned": len([g for g in all_goals if g.status == GoalStatus.ABANDONED]),
            "avg_progress": sum(g.progress for g in all_goals) / len(all_goals) if all_goals else 0,
        }

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    def _save(self):
        """Save all goals to disk."""
        data = [g.to_dict() for g in self._cache.values()]
        try:
            tmp = self.goals_file.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.goals_file)
        except Exception as e:
            logger.error(f"[goal_manager] save error: {e}")


    def _load(self):
        """Load goals from disk into cache."""
        if not self.goals_file.exists():
            return
        try:
            data = json.loads(self.goals_file.read_text(encoding="utf-8"))
            for item in data:
                g = Goal.from_dict(item)
                self._cache[g.goal_id] = g
            self._stats["created"] = len(self._cache)
        except Exception as e:
            logger.error(f"[goal_manager] load error: {e}")

