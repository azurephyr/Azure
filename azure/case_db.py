"""Unified Moderation Case Management database.

Tracks moderation cases across all opt-in servers, with notes,
evidence, and appeal workflows.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("azure.case_db")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "cases.db"


@dataclass
class CaseRecord:
    """Represents a single moderation case."""
    case_id: str
    target_id: str
    target_name: str
    guild_id: str
    guild_name: str
    status: str  # open, investigating, resolved, closed, appealed
    severity: str  # low, medium, high, critical
    action_type: str  # warn, timeout, kick, ban, mute, other
    reason: str
    created_by_id: str
    created_by_name: str
    assigned_to_id: str = ""
    assigned_to_name: str = ""
    closed_by_id: str = ""
    closed_by_name: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    closed_at: float = 0.0
    tags: str = ""


@dataclass
class CaseNote:
    """A note attached to a case."""
    case_id: str
    author_id: str
    author_name: str
    content: str
    timestamp: float = 0.0
    is_internal: bool = False


@dataclass
class CaseEvidence:
    """Evidence attached to a case."""
    case_id: str
    evidence_type: str  # message_link, file_url, image_url, text, screenshot
    evidence_value: str
    description: str = ""
    timestamp: float = 0.0


@dataclass
class CaseAppeal:
    """An appeal for a case."""
    case_id: str
    reason: str
    appealed_by_id: str
    appealed_by_name: str
    appealed_at: float = 0.0
    status: str = "pending"  # pending, approved, denied
    decision_reason: str = ""
    decided_by_id: str = ""
    decided_by_name: str = ""
    decided_at: float = 0.0


_case_id_counter = 0


def _generate_case_id(guild_id: str) -> str:
    """Generate a unique case ID in the format GUILD-XXXXXX."""
    import random
    global _case_id_counter
    _case_id_counter = (_case_id_counter + 1) % 1000
    short = guild_id[-5:] if len(guild_id) > 5 else guild_id
    # Millisecond + counter + random so concurrent/rapid creates never collide
    ms = int(time.time() * 1000) % 1_000_000
    suffix = f"{ms:06d}{_case_id_counter:03d}{random.randint(0, 99):02d}"
    return f"{short}-{suffix}"


class CaseDatabase:
    """SQLite-backed case management database."""

    def __init__(self, db_path: str | Path = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_tables()

    def _init_tables(self) -> None:
        cur = self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS cases (
                case_id         TEXT PRIMARY KEY,
                target_id       TEXT NOT NULL,
                target_name     TEXT NOT NULL,
                guild_id        TEXT NOT NULL,
                guild_name      TEXT NOT NULL DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'open',
                severity        TEXT NOT NULL DEFAULT 'medium',
                action_type     TEXT NOT NULL DEFAULT 'other',
                reason          TEXT NOT NULL DEFAULT '',
                created_by_id   TEXT NOT NULL DEFAULT '',
                created_by_name TEXT NOT NULL DEFAULT '',
                assigned_to_id  TEXT NOT NULL DEFAULT '',
                assigned_to_name TEXT NOT NULL DEFAULT '',
                closed_by_id    TEXT NOT NULL DEFAULT '',
                closed_by_name  TEXT NOT NULL DEFAULT '',
                created_at      REAL NOT NULL DEFAULT 0,
                updated_at      REAL NOT NULL DEFAULT 0,
                closed_at       REAL NOT NULL DEFAULT 0,
                tags            TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS case_notes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id         TEXT NOT NULL,
                author_id       TEXT NOT NULL,
                author_name     TEXT NOT NULL DEFAULT '',
                content         TEXT NOT NULL,
                timestamp       REAL NOT NULL DEFAULT 0,
                is_internal     INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );

            CREATE TABLE IF NOT EXISTS case_evidence (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id         TEXT NOT NULL,
                evidence_type   TEXT NOT NULL,
                evidence_value  TEXT NOT NULL,
                description     TEXT NOT NULL DEFAULT '',
                timestamp       REAL NOT NULL DEFAULT 0,
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );

            CREATE TABLE IF NOT EXISTS case_appeals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id         TEXT NOT NULL UNIQUE,
                reason          TEXT NOT NULL,
                appealed_by_id  TEXT NOT NULL,
                appealed_by_name TEXT NOT NULL DEFAULT '',
                appealed_at     REAL NOT NULL DEFAULT 0,
                status          TEXT NOT NULL DEFAULT 'pending',
                decision_reason TEXT NOT NULL DEFAULT '',
                decided_by_id   TEXT NOT NULL DEFAULT '',
                decided_by_name TEXT NOT NULL DEFAULT '',
                decided_at      REAL NOT NULL DEFAULT 0,
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );

            CREATE TABLE IF NOT EXISTS case_opt_in (
                guild_id        TEXT PRIMARY KEY,
                guild_name      TEXT NOT NULL DEFAULT '',
                alert_channel_id TEXT NOT NULL DEFAULT '',
                opted_in_at     REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS case_queries (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                queried_by_guild_id TEXT NOT NULL,
                timestamp       REAL NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_cases_target ON cases(target_id);
            CREATE INDEX IF NOT EXISTS idx_cases_guild ON cases(guild_id);
            CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
            CREATE INDEX IF NOT EXISTS idx_notes_case ON case_notes(case_id);
            CREATE INDEX IF NOT EXISTS idx_evidence_case ON case_evidence(case_id);
            CREATE INDEX IF NOT EXISTS idx_appeals_case ON case_appeals(case_id);
        """)
        cur.close()

    # ── Case CRUD ─────────────────────────────────────────────────────

    def create_case(
        self,
        target_id: str,
        target_name: str,
        guild_id: str,
        guild_name: str,
        severity: str = "medium",
        action_type: str = "other",
        reason: str = "",
        created_by_id: str = "",
        created_by_name: str = "",
        assigned_to_id: str = "",
        assigned_to_name: str = "",
        tags: str = "",
    ) -> str:
        now = time.time()
        # Retry on rare primary-key collision (same ms under load)
        last_err: Exception | None = None
        for _ in range(5):
            case_id = _generate_case_id(guild_id)
            try:
                self._conn.execute(
                    """INSERT INTO cases
                       (case_id, target_id, target_name, guild_id, guild_name,
                        status, severity, action_type, reason,
                        created_by_id, created_by_name,
                        assigned_to_id, assigned_to_name,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (case_id, target_id, target_name, guild_id, guild_name,
                     severity, action_type, reason,
                     created_by_id, created_by_name,
                     assigned_to_id, assigned_to_name,
                     now, now),
                )
                self._conn.commit()
                logger.info("Created case %s for %s in %s", case_id, target_name, guild_name)
                return case_id
            except sqlite3.IntegrityError as e:
                last_err = e
                continue
        raise last_err if last_err is not None else RuntimeError("failed to create case id")

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        return dict(row) if row else None

    def find_cases(
        self,
        target_id: str = "",
        guild_id: str = "",
        status: str = "",
        severity: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where_clauses: list[str] = []
        params: list[str] = []

        if target_id:
            where_clauses.append("target_id = ?")
            params.append(target_id)
        if guild_id:
            where_clauses.append("guild_id = ?")
            params.append(guild_id)
        if status:
            where_clauses.append("status = ?")
            params.append(status)
        if severity:
            where_clauses.append("severity = ?")
            params.append(severity)

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM cases WHERE {where_sql} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_cases(self, query: str, limit: int = 25) -> list[dict[str, Any]]:
        """Search cases by target name, reason, or tags."""
        like = f"%{query}%"
        rows = self._conn.execute(
            """SELECT * FROM cases
               WHERE target_name LIKE ? OR reason LIKE ? OR tags LIKE ?
               ORDER BY created_at DESC LIMIT ?""",
            (like, like, like, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_case(
        self,
        case_id: str,
        **kwargs: Any,
    ) -> bool:
        allowed = {
            "status", "severity", "action_type", "reason",
            "assigned_to_id", "assigned_to_name",
            "closed_by_id", "closed_by_name", "closed_at",
            "tags", "target_name",
        }
        updates: dict[str, Any] = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        updates["updated_at"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [case_id]
        cur = self._conn.execute(
            f"UPDATE cases SET {set_clause} WHERE case_id = ?", values,
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ── Notes ─────────────────────────────────────────────────────────

    def add_note(
        self,
        case_id: str,
        author_id: str,
        author_name: str,
        content: str,
        is_internal: bool = False,
    ) -> int:
        now = time.time()
        cur = self._conn.execute(
            """INSERT INTO case_notes
               (case_id, author_id, author_name, content, timestamp, is_internal)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (case_id, author_id, author_name, content, now, int(is_internal)),
        )
        self._conn.commit()
        self._conn.execute(
            "UPDATE cases SET updated_at = ? WHERE case_id = ?",
            (now, case_id),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    def get_notes(self, case_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM case_notes WHERE case_id = ? ORDER BY timestamp ASC",
            (case_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Evidence ──────────────────────────────────────────────────────

    def add_evidence(
        self,
        case_id: str,
        evidence_type: str,
        evidence_value: str,
        description: str = "",
    ) -> int:
        now = time.time()
        cur = self._conn.execute(
            """INSERT INTO case_evidence
               (case_id, evidence_type, evidence_value, description, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (case_id, evidence_type, evidence_value, description, now),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    def get_evidence(self, case_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM case_evidence WHERE case_id = ? ORDER BY timestamp ASC",
            (case_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Appeals ───────────────────────────────────────────────────────

    def create_appeal(
        self,
        case_id: str,
        reason: str,
        appealed_by_id: str,
        appealed_by_name: str,
    ) -> bool:
        now = time.time()
        try:
            self._conn.execute(
                """INSERT INTO case_appeals
                   (case_id, reason, appealed_by_id, appealed_by_name, appealed_at, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                (case_id, reason, appealed_by_id, appealed_by_name, now),
            )
            self._conn.execute(
                "UPDATE cases SET status = 'appealed', updated_at = ? WHERE case_id = ?",
                (now, case_id),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # Already has an appeal

    def get_appeal(self, case_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM case_appeals WHERE case_id = ?", (case_id,)
        ).fetchone()
        return dict(row) if row else None

    def decide_appeal(
        self,
        case_id: str,
        status: str,
        decision_reason: str,
        decided_by_id: str,
        decided_by_name: str,
    ) -> bool:
        now = time.time()
        cur1 = self._conn.execute(
            """UPDATE case_appeals
               SET status = ?, decision_reason = ?, decided_by_id = ?,
                   decided_by_name = ?, decided_at = ?
               WHERE case_id = ?""",
            (status, decision_reason, decided_by_id, decided_by_name, now, case_id),
        )
        case_status = "closed" if status == "denied" else "resolved"
        self._conn.execute(
            "UPDATE cases SET status = ?, updated_at = ?, closed_at = ? WHERE case_id = ?",
            (case_status, now, now, case_id),
        )
        self._conn.commit()
        return cur1.rowcount > 0

    # ── Server opt-in ─────────────────────────────────────────────────

    def is_opted_in(self, guild_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM case_opt_in WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        return row is not None

    def opt_in(self, guild_id: str, guild_name: str, alert_channel_id: str = "") -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO case_opt_in
               (guild_id, guild_name, alert_channel_id, opted_in_at)
               VALUES (?, ?, ?, ?)""",
            (guild_id, guild_name, alert_channel_id, time.time()),
        )
        self._conn.commit()

    def opt_out(self, guild_id: str) -> None:
        self._conn.execute("DELETE FROM case_opt_in WHERE guild_id = ?", (guild_id,))
        self._conn.commit()

    def get_alert_channel(self, guild_id: str) -> str:
        row = self._conn.execute(
            "SELECT alert_channel_id FROM case_opt_in WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()
        return row["alert_channel_id"] if row else ""

    def set_alert_channel(self, guild_id: str, channel_id: str) -> None:
        self._conn.execute(
            "UPDATE case_opt_in SET alert_channel_id = ? WHERE guild_id = ?",
            (channel_id, guild_id),
        )
        self._conn.commit()

    # ── Stats ─────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, int]:
        total = self._conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        open_cases = self._conn.execute(
            "SELECT COUNT(*) FROM cases WHERE status IN ('open', 'investigating')"
        ).fetchone()[0]
        closed = self._conn.execute(
            "SELECT COUNT(*) FROM cases WHERE status IN ('closed', 'resolved')"
        ).fetchone()[0]
        appealed = self._conn.execute(
            "SELECT COUNT(*) FROM cases WHERE status = 'appealed'"
        ).fetchone()[0]
        unique_targets = self._conn.execute(
            "SELECT COUNT(DISTINCT target_id) FROM cases"
        ).fetchone()[0]
        unique_guilds = self._conn.execute(
            "SELECT COUNT(DISTINCT guild_id) FROM cases"
        ).fetchone()[0]
        opted_in = self._conn.execute("SELECT COUNT(*) FROM case_opt_in").fetchone()[0]
        return {
            "total": total,
            "open": open_cases,
            "closed": closed,
            "appealed": appealed,
            "unique_targets": unique_targets,
            "unique_guilds": unique_guilds,
            "opted_in": opted_in,
        }

    def close(self) -> None:
        self._conn.close()
