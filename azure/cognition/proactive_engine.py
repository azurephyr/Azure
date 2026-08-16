"""
ProactiveEngine — Upgrade 7: Proactive Behavior Engine

Decides when Azure should act proactively based on goals.

Instead of only responding to user messages, Azure can:
  - Surface relevant goals when the conversation touches on them
  - Suggest next steps when a goal is stalled
  - Celebrate progress when a goal advances
  - Ask for help when blockers are unresolved

The engine is conservative: it only proacts when the relevance score
is high enough and enough time has passed since the last proactive
message (to avoid spamming).

Usage:
    engine = ProactiveEngine(goal_manager)

    # After processing a user message
    suggestion = engine.check(message="how do we get more members?",
                              user_name="azure",
                              context={"server_id": "123"})
    if suggestion:
        # Append to Azure's response
        response += "\n\n" + suggestion
"""

from __future__ import annotations

import time

from .goal_manager import GoalManager
from .goal_state import Goal, GoalPriority, GoalStatus


class ProactiveEngine:
    """
    Conservative proactive behavior engine.

    Rules:
      1. Minimum relevance score (0.3) to surface anything
      2. Minimum time between proactive messages (5 minutes per user)
      3. Only surface ACTIVE goals
      4. Prefer goals with blockers or low progress (they need attention)
      5. Never interrupt the user's direct question with a goal unless highly relevant
    """

    MIN_RELEVANCE = 0.3
    MIN_TIME_BETWEEN_PROACTIVE = 300  # 5 minutes in seconds
    MAX_DAILY_PER_USER = 3  # Max proactive suggestions per user per day

    def __init__(self, goal_manager: GoalManager):
        self.goal_manager = goal_manager
        # Track when we last proacted to each user
        self._user_last_proactive: dict[str, float] = {}
        self._user_daily_count: dict[str, int] = {}
        self._user_day_key: dict[str, str] = {}  # "user:YYYY-MM-DD"

    # -----------------------------------------------------------------------
    # Main entry point
    # -----------------------------------------------------------------------

    def check(self, message: str, user_name: str, context: dict | None = None) -> str | None:
        """
        Check if a proactive suggestion should be made for this message.

        Returns:
            A suggestion string, or None if nothing to say.
        """
        # Rate limiting
        if not self._can_proact(user_name):
            return None

        # Find relevant goals
        relevant = self.goal_manager.find_relevant(message, k=2)
        if not relevant:
            return None

        # Only consider goals above minimum relevance
        relevant = [(g, s) for g, s in relevant if s >= self.MIN_RELEVANCE]
        if not relevant:
            return None

        # Pick the most relevant goal
        goal, score = relevant[0]

        # Check if we already have an active blocker for this goal
        active_blockers = [b for b in goal.blockers if not b.resolved]

        # Generate appropriate suggestion based on goal state
        suggestion = self._generate_suggestion(goal, score, active_blockers)

        if suggestion:
            self._record_proact(user_name)
            return suggestion

        return None

    # -----------------------------------------------------------------------
    # Suggestion generators
    # -----------------------------------------------------------------------

    def _generate_suggestion(self, goal: Goal, score: float,
                             blockers: list) -> str | None:
        """Generate a human-readable proactive suggestion."""
        pct = int(goal.progress * 100)
        desc = goal.description

        # If there are blockers, ask for help
        if blockers and score > 0.5:
            blocker = blockers[0]
            return (
                f"💡 By the way, we're working on **{desc}** ({pct}% done), "
                f"but there's a blocker: *{blocker.description}*. "
                f"Any ideas on how to resolve this?"
            )

        # If progress is very low, suggest getting started
        if goal.progress < 0.2 and score > 0.4:
            return (
                f"💡 This reminds me of our goal to **{desc}**. "
                f"We've just started ({pct}%). Want to plan the first step?"
            )

        # If progress is high, celebrate and suggest next steps
        if goal.progress > 0.7 and score > 0.4:
            return (
                f"🎉 Great progress on **{desc}**! We're at {pct}%. "
                f"Almost there — want to push it to completion?"
            )

        # General relevance — light touch
        if score > 0.5:
            return (
                f"💡 FYI: we're tracking **{desc}** ({pct}% complete). "
                f"Let me know if you'd like to focus on this."
            )

        return None

    # -----------------------------------------------------------------------
    # Rate limiting
    # -----------------------------------------------------------------------

    def _can_proact(self, user_name: str) -> bool:
        """Check if we're allowed to proact to this user right now."""
        now = time.time()

        # Check daily limit
        day_key = f"{user_name}:{time.strftime('%Y-%m-%d')}"
        existing_key = self._user_day_key.get(user_name)
        if existing_key is not None and existing_key != day_key:
            # New day, reset count
            self._user_day_key[user_name] = day_key
            self._user_daily_count[user_name] = 0
        elif existing_key is None:
            # First time this user is checked, initialize
            self._user_day_key[user_name] = day_key

        if self._user_daily_count.get(user_name, 0) >= self.MAX_DAILY_PER_USER:
            return False

        # Check time since last proactive message
        last = self._user_last_proactive.get(user_name, 0)
        return not now - last < self.MIN_TIME_BETWEEN_PROACTIVE

    def _record_proact(self, user_name: str):
        """Record that we just proacted to this user."""
        self._user_last_proactive[user_name] = time.time()
        self._user_daily_count[user_name] = self._user_daily_count.get(user_name, 0) + 1

    # -----------------------------------------------------------------------
    # Explicit goal commands (for user-initiated goal management)
    # -----------------------------------------------------------------------

    def handle_goal_command(self, message: str, user_name: str) -> str | None:
        """
        Handle user commands like:
          - "show my goals"
          - "add goal: grow server"
          - "mark subgoal X done"
          - "what's the blocker for Y?"

        Returns a response string, or None if not a goal command.
        """
        msg = message.lower().strip()

        # Show goals
        if any(k in msg for k in ("show goals", "list goals", "my goals", "what are our goals")):
            active = self.goal_manager.get_active()
            if not active:
                return "No active goals right now. Want to set one?"
            lines = ["🎯 Active Goals:"]
            for g in active:
                lines.append(f"  • {g.description} ({int(g.progress * 100)}%)")
            return "\n".join(lines)

        # Add goal
        if msg.startswith("add goal") or msg.startswith("create goal") or msg.startswith("new goal"):
            # Extract description after colon or the rest of the message
            desc = msg.replace("add goal", "").replace("create goal", "").replace("new goal", "").strip(": ")
            if desc:
                goal = self.goal_manager.create(description=desc, priority=GoalPriority.MEDIUM)
                return f"✅ Goal created: **{goal.description}** (ID: `{goal.goal_id}`)"
            return "What should the goal be? Try: `add goal: grow server to 1000 members`"

        # Mark complete (simple heuristic)
        if "mark" in msg and "done" in msg:
            # Try to find a goal by description match
            active = self.goal_manager.get_active()
            for g in active:
                if any(w in msg for w in g.description.lower().split() if len(w) > 3):
                    g.set_status(GoalStatus.COMPLETED)
                    self.goal_manager._save()
                    return f"🎉 Goal completed: **{g.description}**"
            return "Which goal did you want to mark as done?"

        return None  # Not a goal command
