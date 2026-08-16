"""
Azure Change Tracker & Audit Log

Tracks every structural change the bot makes to a Discord server.
Provides:
  - Persistent audit trail (JSONL log)
  - Undo/Redo capability (reverse operations)
  - Rollback by time or count
  - Change summaries for user review

Usage:
    from azure.change_tracker import ChangeTracker
    tracker = ChangeTracker(log_dir=Path("logs/changes"))

    # Log a change
    tracker.log_change(
        guild_id=123456789,
        action="create_role",
        target={"name": "Moderator", "id": 987654321},
        before=None,
        after={"name": "Moderator", "color": "blue"},
        performed_by="Owner",
    )

    # Undo last change
    undo = tracker.get_undo(guild_id)
    # Returns a dict that can be passed to the undo executor
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("azure.change_tracker")


@dataclass
class ChangeRecord:
    """Single recorded change."""
    t: float               # timestamp
    guild_id: int
    guild_name: str
    action: str            # e.g. "create_role", "delete_channel"
    target: dict           # {name, id, type}
    before: dict | None # state before change (for undo)
    after: dict | None  # state after change (for redo)
    performed_by: str      # who triggered it
    request_text: str      # original natural language request
    success: bool = True
    error: str = ""


class ChangeTracker:
    """
    Tracks all bot-made changes to Discord servers.
    Provides undo, redo, and audit logging.
    """

    # Actions that can be undone and their reverse
    REVERSIBLE_ACTIONS = {
        "create_role": "delete_role",
        "delete_role": "create_role",
        "create_channel": "delete_channel",
        "delete_channel": "create_channel",
        "create_category": "delete_category",
        "delete_category": "create_category",
        "create_webhook": "delete_webhook",
        "delete_webhook": "create_webhook",
        "set_permissions": "restore_permissions",  # special handling
        "assign_role": "remove_role",
        "remove_role": "assign_role",
        "set_nickname": "restore_nickname",
        "create_scheduled_event": "delete_scheduled_event",
        "delete_scheduled_event": "create_scheduled_event",
        "create_auto_mod_rule": "delete_auto_mod_rule",
        "delete_auto_mod_rule": "create_auto_mod_rule",
    }

    def __init__(self, log_dir: Path | None = None,
                 max_per_guild: int = 1000):
        """
        Args:
            log_dir: Directory for audit logs. If None, uses memory only.
            max_per_guild: Max changes kept per guild in memory.
        """
        self.log_dir = Path(log_dir) if log_dir else None
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)

        self._changes: dict[int, list[ChangeRecord]] = {}  # guild_id -> list
        self._max_per_guild = max_per_guild
        self._undo_pointer: dict[int, int] = {}  # guild_id -> index (0 = newest)
        self._persist_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log_change(self, guild_id: int, guild_name: str, action: str,
                   target: dict, before: dict | None, after: dict | None,
                   performed_by: str, request_text: str = "",
                   success: bool = True, error: str = "") -> ChangeRecord:
        """Log a single change."""
        record = ChangeRecord(
            t=time.time(),
            guild_id=guild_id,
            guild_name=guild_name,
            action=action,
            target=target,
            before=before,
            after=after,
            performed_by=performed_by,
            request_text=request_text,
            success=success,
            error=error,
        )

        if guild_id not in self._changes:
            self._changes[guild_id] = []
        self._changes[guild_id].insert(0, record)  # newest first

        # Trim
        if len(self._changes[guild_id]) > self._max_per_guild:
            self._changes[guild_id] = self._changes[guild_id][:self._max_per_guild]

        # Reset undo pointer to newest
        self._undo_pointer[guild_id] = 0

        # Persist to disk
        self._persist(record)

        return record

    def _persist(self, record: ChangeRecord):
        """Append record to guild-specific log file."""
        if not self.log_dir:
            return
        path = self.log_dir / f"guild_{record.guild_id}.jsonl"
        with self._persist_lock, open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False, default=str) + "\n")

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------

    def can_undo(self, guild_id: int) -> bool:
        """Can we undo at least one change?"""
        ptr = self._undo_pointer.get(guild_id, 0)
        changes = self._changes.get(guild_id, [])
        return ptr < len(changes) and changes[ptr].success

    def get_undo(self, guild_id: int) -> dict | None:
        """
        Get the next undoable action as a dict that can be executed.
        Returns None if nothing to undo.
        """
        ptr = self._undo_pointer.get(guild_id, 0)
        changes = self._changes.get(guild_id, [])
        if ptr >= len(changes):
            return None

        record = changes[ptr]
        if not record.success:
            return None

        reverse_action = self.REVERSIBLE_ACTIONS.get(record.action)
        if not reverse_action:
            return None

        # Build undo step
        undo = {
            "undo_of": record,
            "action": reverse_action,
            "target": record.target,
            "before": record.before,
            "after": record.after,
        }

        # Advance pointer (consuming this for undo)
        self._undo_pointer[guild_id] = ptr + 1
        return undo

    def undo_count(self, guild_id: int) -> int:
        """How many changes can be undone?"""
        ptr = self._undo_pointer.get(guild_id, 0)
        changes = self._changes.get(guild_id, [])
        count = 0
        for i in range(ptr, len(changes)):
            if changes[i].success and changes[i].action in self.REVERSIBLE_ACTIONS:
                count += 1
        return count

    def get_last_n(self, guild_id: int, n: int = 5) -> list[ChangeRecord]:
        """Get last N changes (newest first)."""
        changes = self._changes.get(guild_id, [])
        ptr = self._undo_pointer.get(guild_id, 0)
        return changes[ptr:ptr + n]

    def get_changes_today(self, guild_id: int) -> list[ChangeRecord]:
        """Get all changes from today."""
        now = time.time()
        day_start = now - (now % 86400)
        return [
            c for c in self._changes.get(guild_id, [])
            if c.t >= day_start
        ]

    def get_undo_summary(self, guild_id: int, n: int = 5) -> str:
        """Human-readable summary of what can be undone."""
        changes = self.get_last_n(guild_id, n)
        if not changes:
            return "No changes to undo."

        lines = ["🔄 **Undoable Changes:**"]
        for i, c in enumerate(changes, 1):
            status = "✅" if c.success else "❌"
            lines.append(
                f"{i}. {status} `{c.action}` → **{c.target.get('name', 'unknown')}** "
                f"(by {c.performed_by})"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Audit Queries
    # ------------------------------------------------------------------

    def get_audit_log(self, guild_id: int, limit: int = 50) -> list[dict]:
        """Get full audit log for a guild."""
        return [asdict(c) for c in self._changes.get(guild_id, [])[:limit]]

    def search_audit(self, guild_id: int, action: str = None,
                     user: str = None, limit: int = 50) -> list[dict]:
        """Search audit log by action or user."""
        results = []
        for c in self._changes.get(guild_id, []):
            if action and c.action != action:
                continue
            if user and c.performed_by != user:
                continue
            results.append(asdict(c))
            if len(results) >= limit:
                break
        return results

    def get_stats(self, guild_id: int) -> dict:
        """Summary stats for a guild."""
        changes = self._changes.get(guild_id, [])
        if not changes:
            return {"total": 0, "today": 0, "undoable": 0, "success_rate": 0.0}

        now = time.time()
        day_start = now - (now % 86400)
        total = len(changes)
        today = sum(1 for c in changes if c.t >= day_start)
        successful = sum(1 for c in changes if c.success)
        undoable = self.undo_count(guild_id)

        return {
            "total": total,
            "today": today,
            "undoable": undoable,
            "success_rate": successful / total if total > 0 else 0.0,
            "most_common_actions": self._top_actions(changes),
        }

    def _top_actions(self, changes: list[ChangeRecord]) -> list[tuple]:
        """Count most frequent actions."""
        counts = {}
        for c in changes:
            counts[c.action] = counts.get(c.action, 0) + 1
        return sorted(counts.items(), key=lambda x: x[1], reverse=True)[:5]

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def load_from_disk(self, guild_id: int):
        """Load changes from disk for a guild."""
        if not self.log_dir:
            return
        path = self.log_dir / f"guild_{guild_id}.jsonl"
        if not path.exists():
            return

        records = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    records.append(ChangeRecord(**data))
                except Exception as e:
                    logger.info(f"[change_tracker] corrupted log line skipped: {e}")


        # Sort by time, newest first
        records.sort(key=lambda r: r.t, reverse=True)
        self._changes[guild_id] = records[:self._max_per_guild]
        self._undo_pointer[guild_id] = 0
