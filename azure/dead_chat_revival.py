"""Dead Chat Revival — automatically revive inactive channels.

Monitors message activity per channel and sends engaging prompts
when a channel has been silent beyond a configurable threshold.
"""

from __future__ import annotations

import logging
import random
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("azure.dead_chat_revival")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "dead_chat_revival.db"

DEFAULT_THRESHOLD_MINUTES = 120
DEFAULT_COOLDOWN_MINUTES = 60
DEFAULT_REVIVAL_HOURS = 8
MIN_INTERVAL_MINUTES = 15

REVIVAL_PROMPTS = [
    "Anyone working on something interesting today?",
    "What's everyone up to?",
    "Got any cool projects you're working on?",
    "Anyone read anything interesting lately?",
    "What's the hottest take you have right now?",
    "If you could learn any new skill instantly, what would it be?",
    "What's something you've changed your mind about recently?",
    "What's the best piece of advice you've ever received?",
    "What are you looking forward to this week?",
    "Drop a random fact — go!",
    "What's your go-to productivity tip?",
    "If money were no object, what would you be doing right now?",
    "What's a problem you've been trying to solve lately?",
    "What's something you think is underrated?",
    "What's a small win you had today?",
    "What's your favorite tool or software right now?",
    "What's a question you wish someone would ask you?",
    "What's the most interesting thing you learned this week?",
    "What's a trend you're tired of?",
    "What's something you're excited to learn more about?",
]


class RevivalDatabase:
    """SQLite-backed dead chat revival database."""

    def __init__(self, db_path: str | Path = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS revival_config (
                guild_id            TEXT NOT NULL,
                channel_id          TEXT NOT NULL,
                enabled             INTEGER NOT NULL DEFAULT 0,
                threshold_minutes   INTEGER NOT NULL DEFAULT 120,
                cooldown_minutes    INTEGER NOT NULL DEFAULT 60,
                revival_hours       INTEGER NOT NULL DEFAULT 8,
                custom_prompt       TEXT NOT NULL DEFAULT '',
                last_revival_at     REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, channel_id)
            );
            CREATE TABLE IF NOT EXISTS revival_log (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id            TEXT NOT NULL,
                channel_id          TEXT NOT NULL,
                prompt              TEXT NOT NULL,
                message_id          TEXT NOT NULL DEFAULT '',
                response_count      INTEGER NOT NULL DEFAULT 0,
                sent_at             REAL NOT NULL DEFAULT 0,
                revived             INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS channel_activity (
                guild_id            TEXT NOT NULL,
                channel_id          TEXT NOT NULL,
                last_message_at     REAL NOT NULL DEFAULT 0,
                message_count_24h   INTEGER NOT NULL DEFAULT 0,
                avg_daily_messages  REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, channel_id)
            );
            CREATE INDEX IF NOT EXISTS idx_revival_log_guild ON revival_log(guild_id);
            CREATE INDEX IF NOT EXISTS idx_revival_log_channel ON revival_log(channel_id);
            CREATE INDEX IF NOT EXISTS idx_channel_activity_guild ON channel_activity(guild_id);
        """)
        self._conn.commit()

    # ── Config ────────────────────────────────────────────────────────

    def is_enabled(self, guild_id: str, channel_id: str) -> bool:
        row = self._conn.execute(
            "SELECT enabled FROM revival_config WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        ).fetchone()
        return bool(row and row["enabled"])

    def is_guild_enabled(self, guild_id: str) -> bool:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM revival_config WHERE guild_id = ? AND enabled = 1",
            (guild_id,),
        ).fetchone()
        return bool(row and row[0] > 0)

    def set_enabled(self, guild_id: str, channel_id: str, enabled: bool) -> None:
        self._conn.execute(
            """INSERT INTO revival_config (guild_id, channel_id, enabled, threshold_minutes, cooldown_minutes, revival_hours)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(guild_id, channel_id) DO UPDATE SET enabled=excluded.enabled""",
            (guild_id, channel_id, int(enabled), DEFAULT_THRESHOLD_MINUTES, DEFAULT_COOLDOWN_MINUTES, DEFAULT_REVIVAL_HOURS),
        )
        self._conn.commit()

    def set_enabled_all_channels(self, guild_id: str, enabled: bool) -> None:
        self._conn.execute(
            """INSERT INTO revival_config (guild_id, channel_id, enabled, threshold_minutes, cooldown_minutes, revival_hours)
               SELECT ?, channel_id, ?, ?, ?, ?
               FROM channel_activity WHERE guild_id = ?
               ON CONFLICT(guild_id, channel_id) DO UPDATE SET enabled=excluded.enabled""",
            (guild_id, int(enabled), DEFAULT_THRESHOLD_MINUTES, DEFAULT_COOLDOWN_MINUTES, DEFAULT_REVIVAL_HOURS, guild_id),
        )
        self._conn.commit()

    def get_config(self, guild_id: str, channel_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM revival_config WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        ).fetchone()
        if row:
            return dict(row)
        return {
            "guild_id": guild_id,
            "channel_id": channel_id,
            "enabled": 0,
            "threshold_minutes": DEFAULT_THRESHOLD_MINUTES,
            "cooldown_minutes": DEFAULT_COOLDOWN_MINUTES,
            "revival_hours": DEFAULT_REVIVAL_HOURS,
            "custom_prompt": "",
            "last_revival_at": 0,
        }

    def update_config(self, guild_id: str, channel_id: str, **kwargs: Any) -> None:
        allowed = {"threshold_minutes", "cooldown_minutes", "revival_hours", "custom_prompt", "last_revival_at"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [guild_id, channel_id]
        self._conn.execute(
            f"UPDATE revival_config SET {set_clause} WHERE guild_id = ? AND channel_id = ?",
            values,
        )
        self._conn.commit()

    def get_enabled_channels(self, guild_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM revival_config WHERE guild_id = ? AND enabled = 1",
            (guild_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Activity tracking ─────────────────────────────────────────────

    def record_message(self, guild_id: str, channel_id: str) -> None:
        now = time.time()
        row = self._conn.execute(
            "SELECT * FROM channel_activity WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        ).fetchone()

        if row:
            self._conn.execute(
                """UPDATE channel_activity
                   SET last_message_at = ?,
                       message_count_24h = CASE WHEN ? - last_message_at < 86400 THEN message_count_24h + 1 ELSE 1 END
                   WHERE guild_id = ? AND channel_id = ?""",
                (now, now, guild_id, channel_id),
            )
        else:
            self._conn.execute(
                "INSERT INTO channel_activity (guild_id, channel_id, last_message_at, message_count_24h, avg_daily_messages) VALUES (?, ?, ?, 1, 0)",
                (guild_id, channel_id, now),
            )

        if row:
            old_count = row["message_count_24h"]
            if old_count == 0:
                pass
            if row["last_message_at"] and now - row["last_message_at"] > 86400:
                self._conn.execute(
                    "UPDATE channel_activity SET avg_daily_messages = message_count_24h WHERE guild_id = ? AND channel_id = ?",
                    (guild_id, channel_id),
                )

        self._conn.commit()

    def get_last_message_time(self, guild_id: str, channel_id: str) -> float:
        row = self._conn.execute(
            "SELECT last_message_at FROM channel_activity WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        ).fetchone()
        return row["last_message_at"] if row else 0.0

    def get_activity_summary(self, guild_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            """SELECT COUNT(*) as channels,
                      COUNT(CASE WHEN last_message_at > ? THEN 1 END) as active_channels,
                      MAX(last_message_at) as last_activity,
                      SUM(message_count_24h) as messages_24h
               FROM channel_activity WHERE guild_id = ?""",
            (time.time() - 3600, guild_id),
        ).fetchone()
        if row:
            return dict(row)
        return {"channels": 0, "active_channels": 0, "last_activity": 0, "messages_24h": 0}

    # ── Revival log ───────────────────────────────────────────────────

    def log_revival(
        self,
        guild_id: str,
        channel_id: str,
        prompt: str,
        message_id: str = "",
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO revival_log (guild_id, channel_id, prompt, message_id, sent_at) VALUES (?, ?, ?, ?, ?)",
            (guild_id, channel_id, prompt, message_id, time.time()),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    def mark_revived(self, log_id: int) -> None:
        self._conn.execute(
            "UPDATE revival_log SET revived = 1 WHERE id = ?",
            (log_id,),
        )
        self._conn.commit()

    def mark_response(self, log_id: int) -> None:
        self._conn.execute(
            "UPDATE revival_log SET response_count = response_count + 1 WHERE id = ?",
            (log_id,),
        )
        self._conn.commit()

    def get_revival_history(self, guild_id: str, channel_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
        if channel_id:
            rows = self._conn.execute(
                "SELECT * FROM revival_log WHERE guild_id = ? AND channel_id = ? ORDER BY sent_at DESC LIMIT ?",
                (guild_id, channel_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM revival_log WHERE guild_id = ? ORDER BY sent_at DESC LIMIT ?",
                (guild_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict[str, int]:
        total_revivals = self._conn.execute("SELECT COUNT(*) FROM revival_log").fetchone()[0]
        successful = self._conn.execute("SELECT COUNT(*) FROM revival_log WHERE revived = 1").fetchone()[0]
        total_responses = self._conn.execute("SELECT SUM(response_count) FROM revival_log").fetchone()[0] or 0
        unique_guilds = self._conn.execute("SELECT COUNT(DISTINCT guild_id) FROM revival_log").fetchone()[0]
        enabled_channels = self._conn.execute("SELECT COUNT(*) FROM revival_config WHERE enabled = 1").fetchone()[0]
        unique_channels = self._conn.execute("SELECT COUNT(DISTINCT channel_id) FROM channel_activity").fetchone()[0]
        return {
            "total_revivals": total_revivals,
            "successful_revivals": successful,
            "total_responses": total_responses,
            "unique_guilds": unique_guilds,
            "enabled_channels": enabled_channels,
            "tracked_channels": unique_channels,
        }

    def close(self) -> None:
        self._conn.close()


_db_instance: RevivalDatabase | None = None


def get_db() -> RevivalDatabase:
    global _db_instance
    if _db_instance is None:
        _db_instance = RevivalDatabase()
    return _db_instance


# ── Revival Engine ──────────────────────────────────────────────────────────

def select_revival_prompt(custom_prompt: str = "") -> str:
    """Select a revival prompt, using custom if provided."""
    if custom_prompt:
        return custom_prompt
    return random.choice(REVIVAL_PROMPTS)


def should_revive(
    guild_id: str,
    channel_id: str,
    now: float | None = None,
    db: RevivalDatabase | None = None,
) -> tuple[bool, str]:
    """Check if a channel should be revived.

    Returns (should_revive, reason).
    """
    if now is None:
        now = time.time()

    if db is None:
        db = get_db()
    config = db.get_config(guild_id, channel_id)

    if not config["enabled"]:
        return False, "Revival not enabled for this channel"

    last_message = db.get_last_message_time(guild_id, channel_id)
    if last_message == 0:
        return False, "No message history for this channel"

    silent_duration = now - last_message
    threshold = config["threshold_minutes"] * 60

    if silent_duration < threshold:
        remaining = threshold - silent_duration
        return False, f"Channel still active ({int(remaining/60)} min until threshold)"

    last_revival = config["last_revival_at"]
    cooldown = config["cooldown_minutes"] * 60

    if last_revival > 0 and (now - last_revival) < cooldown:
        remaining = cooldown - (now - last_revival)
        return False, f"In cooldown ({int(remaining/60)} min remaining)"

    revival_hours = config["revival_hours"] * 3600
    recent_logs = db.get_revival_history(guild_id, channel_id, limit=10)
    revivals_in_window = sum(
        1 for log in recent_logs
        if (now - log["sent_at"]) < revival_hours
    )
    if revivals_in_window >= 3:
        return False, f"Too many revivals in the last {config['revival_hours']} hours"

    return True, "Channel is ready for revival"


def get_all_revivable_channels(
    guild_id: str,
    db: RevivalDatabase | None = None,
) -> list[dict[str, Any]]:
    """Get all enabled channels that are ready for revival."""
    if db is None:
        db = get_db()
    channels = db.get_enabled_channels(guild_id)
    now = time.time()
    result: list[dict[str, Any]] = []

    for ch in channels:
        last_msg = db.get_last_message_time(guild_id, ch["channel_id"])
        if last_msg == 0:
            continue
        silent_duration = now - last_msg
        threshold = ch["threshold_minutes"] * 60
        if silent_duration < threshold:
            continue
        last_revival = ch["last_revival_at"]
        cooldown = ch["cooldown_minutes"] * 60
        if last_revival > 0 and (now - last_revival) < cooldown:
            continue
        result.append(ch)

    return result


async def send_revival(
    channel: Any,
    prompt: str | None = None,
    moderator_id: str = "",
    moderator_name: str = "",
) -> bool:
    """Send a revival prompt to a channel.

    Returns True if the message was sent successfully.
    """
    import discord

    guild_id = str(channel.guild.id)
    channel_id = str(channel.id)

    db = get_db()
    config = db.get_config(guild_id, channel_id)

    text = prompt or select_revival_prompt(config.get("custom_prompt", ""))

    embed = discord.Embed(
        title="💬 Thread Revival",
        description=text,
        color=0x5865F2,
    )
    embed.set_footer(text="This channel has been quiet — let's get talking!")

    try:
        msg = await channel.send(embed=embed)
        db.log_revival(guild_id, channel_id, text, str(msg.id))
        db.update_config(guild_id, channel_id, last_revival_at=time.time())
        logger.info("Sent revival to %s/%s: %s", channel.guild.name, channel.name, text[:50])
        return True
    except discord.Forbidden:
        logger.warning("Missing permissions to send revival in %s/%s", channel.guild.name, channel.name)
        return False
    except discord.HTTPException as e:
        logger.error("HTTP error sending revival: %s", e)
        return False
