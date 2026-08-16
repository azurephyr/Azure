"""Cross-server reputation database.

Stores ban/kick/warn events across servers and enables:
- Shared ban network (opt-in servers share reputation data)
- Auto-alert when a known bad actor joins an opt-in server
- Manual reputation checks via /reputation check

Uses SQLite for zero-config deployment.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("azure.reputation")

_REPUTATION_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "reputation.db"


@dataclass
class ReputationEvent:
    target_id: str = ""
    target_name: str = ""
    action_type: str = ""  # ban, kick, warn, timeout
    reason: str = ""
    source_guild_id: str = ""
    source_guild_name: str = ""
    moderator_id: str = ""
    moderator_name: str = ""
    timestamp: float = 0.0
    evidence_link: str = ""


@dataclass
class ReputationSummary:
    total_events: int = 0
    ban_count: int = 0
    kick_count: int = 0
    warn_count: int = 0
    timeout_count: int = 0
    unique_servers: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    servers: list[str] = field(default_factory=list)


class ReputationDatabase:
    """Thread-safe SQLite database for cross-server reputation data."""

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = Path(db_path or _REPUTATION_DB_PATH)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS reputation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    reason TEXT DEFAULT '',
                    source_guild_id TEXT NOT NULL,
                    source_guild_name TEXT NOT NULL,
                    moderator_id TEXT DEFAULT '',
                    moderator_name TEXT DEFAULT '',
                    timestamp REAL NOT NULL,
                    evidence_link TEXT DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_reputation_target
                    ON reputation_events(target_id);
                CREATE INDEX IF NOT EXISTS idx_reputation_guild
                    ON reputation_events(source_guild_id);
                CREATE INDEX IF NOT EXISTS idx_reputation_timestamp
                    ON reputation_events(timestamp);

                CREATE TABLE IF NOT EXISTS reputation_opt_in (
                    guild_id TEXT PRIMARY KEY,
                    guild_name TEXT NOT NULL,
                    opted_in_at REAL NOT NULL,
                    alert_channel_id TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS reputation_queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id TEXT NOT NULL,
                    queried_by_guild_id TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );
            """)
            self._conn.commit()

    # ── Event recording ─────────────────────────────────────────────────

    def record_event(self, event: ReputationEvent) -> None:
        """Record a reputation event (ban/kick/warn/timeout)."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO reputation_events
                   (target_id, target_name, action_type, reason,
                    source_guild_id, source_guild_name,
                    moderator_id, moderator_name, timestamp, evidence_link)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event.target_id, event.target_name, event.action_type,
                 event.reason, event.source_guild_id, event.source_guild_name,
                 event.moderator_id, event.moderator_name,
                 event.timestamp or time.time(), event.evidence_link),
            )
            self._conn.commit()

    # ── Queries ─────────────────────────────────────────────────────────

    def get_reputation(self, target_id: str) -> ReputationSummary:
        """Get the full reputation summary for a target user."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM reputation_events WHERE target_id = ? ORDER BY timestamp DESC",
                (target_id,),
            ).fetchall()

        if not rows:
            return ReputationSummary()

        summary = ReputationSummary()
        summary.total_events = len(rows)
        seen_servers: set[str] = set()
        for row in rows:
            action = row["action_type"]
            if action == "ban":
                summary.ban_count += 1
            elif action == "kick":
                summary.kick_count += 1
            elif action == "warn":
                summary.warn_count += 1
            elif action == "timeout":
                summary.timeout_count += 1
            seen_servers.add(row["source_guild_name"])
            ts = row["timestamp"]
            if summary.first_seen == 0 or ts < summary.first_seen:
                summary.first_seen = ts
            if ts > summary.last_seen:
                summary.last_seen = ts
        summary.unique_servers = len(seen_servers)
        summary.servers = sorted(seen_servers)
        return summary

    def has_reputation(self, target_id: str) -> bool:
        """Check if a target has any reputation events."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM reputation_events WHERE target_id = ?",
                (target_id,),
            ).fetchone()
            return row["cnt"] > 0 if row else False

    def get_events_for_guild(self, guild_id: str, limit: int = 50) -> list[dict]:
        """Get all reputation events originating from a specific guild."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM reputation_events WHERE source_guild_id = ? ORDER BY timestamp DESC LIMIT ?",
                (guild_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Opt-in management ─────────────────────────────────────────────

    def is_opted_in(self, guild_id: str) -> bool:
        """Check if a guild is opted into the shared reputation network."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM reputation_opt_in WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
            return row is not None

    def opt_in(self, guild_id: str, guild_name: str, alert_channel_id: str = "") -> None:
        """Opt a guild into the shared reputation network."""
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO reputation_opt_in
                   (guild_id, guild_name, opted_in_at, alert_channel_id)
                   VALUES (?, ?, ?, ?)""",
                (guild_id, guild_name, time.time(), alert_channel_id),
            )
            self._conn.commit()

    def opt_out(self, guild_id: str) -> None:
        """Opt a guild out of the shared reputation network."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM reputation_opt_in WHERE guild_id = ?",
                (guild_id,),
            )
            self._conn.commit()

    def set_alert_channel(self, guild_id: str, channel_id: str) -> None:
        """Set the alert channel for a guild."""
        with self._lock:
            self._conn.execute(
                "UPDATE reputation_opt_in SET alert_channel_id = ? WHERE guild_id = ?",
                (channel_id, guild_id),
            )
            self._conn.commit()

    def get_alert_channel(self, guild_id: str) -> str:
        """Get the alert channel for a guild."""
        with self._lock:
            row = self._conn.execute(
                "SELECT alert_channel_id FROM reputation_opt_in WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
            return row["alert_channel_id"] if row else ""

    def record_query(self, target_id: str, guild_id: str) -> None:
        """Record that a reputation query was made."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO reputation_queries (target_id, queried_by_guild_id, timestamp) VALUES (?, ?, ?)",
                (target_id, guild_id, time.time()),
            )
            self._conn.commit()

    def count_opted_in(self) -> int:
        """Count how many guilds are opted in."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM reputation_opt_in",
            ).fetchone()
            return row["cnt"] if row else 0

    # ── Stats ─────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get database statistics."""
        with self._lock:
            total_events = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM reputation_events",
            ).fetchone()["cnt"]
            unique_targets = self._conn.execute(
                "SELECT COUNT(DISTINCT target_id) as cnt FROM reputation_events",
            ).fetchone()["cnt"]
            unique_guilds = self._conn.execute(
                "SELECT COUNT(DISTINCT source_guild_id) as cnt FROM reputation_events",
            ).fetchone()["cnt"]
            opted_in = self.count_opted_in()
        return {
            "total_events": total_events,
            "unique_targets": unique_targets,
            "unique_guilds": unique_guilds,
            "opted_in_guilds": opted_in,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
