"""Ghost/Invisible Moderation engine.

Provides stealth moderation actions with minimized visibility:
  - Silent message deletion (generic audit log reason)
  - Invisible warnings (DM only, no channel announcement)
  - Shadow mute (role-based, no audit log entry)
  - Ghost kick (minimal audit log detail)
  - Stealth mode toggle (suppresses admin channel reports)

All actions are logged to a private ghost log accessible via /ghost log.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import discord

logger = logging.getLogger("azure.ghost_moderation")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "ghost_moderation.db"
MUTED_ROLE_NAME = "Shadow Muted"


class GhostAction(Enum):
    DELETE = "delete"
    WARN = "warn"
    MUTE = "mute"
    UNMUTE = "unmute"
    KICK = "kick"
    STEALTH_ON = "stealth_on"
    STEALTH_OFF = "stealth_off"


@dataclass
class GhostLogEntry:
    action: str
    target_id: str
    target_name: str
    moderator_id: str
    moderator_name: str
    guild_id: str
    reason: str = ""
    timestamp: float = 0.0
    details: str = ""


class GhostDatabase:
    """SQLite-backed ghost moderation database."""

    def __init__(self, db_path: str | Path = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS ghost_config (
                guild_id        TEXT PRIMARY KEY,
                enabled         INTEGER NOT NULL DEFAULT 0,
                stealth_mode    INTEGER NOT NULL DEFAULT 0,
                log_channel_id  TEXT NOT NULL DEFAULT '',
                muted_role_id   TEXT NOT NULL DEFAULT '',
                updated_at      REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS ghost_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                action          TEXT NOT NULL,
                target_id       TEXT NOT NULL,
                target_name     TEXT NOT NULL DEFAULT '',
                moderator_id    TEXT NOT NULL DEFAULT '',
                moderator_name  TEXT NOT NULL DEFAULT '',
                guild_id        TEXT NOT NULL,
                reason          TEXT NOT NULL DEFAULT '',
                details         TEXT NOT NULL DEFAULT '',
                timestamp       REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS shadow_mutes (
                guild_id        TEXT NOT NULL,
                user_id         TEXT NOT NULL,
                role_id         TEXT NOT NULL,
                muted_by_id     TEXT NOT NULL DEFAULT '',
                muted_by_name   TEXT NOT NULL DEFAULT '',
                reason          TEXT NOT NULL DEFAULT '',
                muted_at        REAL NOT NULL DEFAULT 0,
                expires_at      REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ghost_log_guild ON ghost_log(guild_id);
            CREATE INDEX IF NOT EXISTS idx_ghost_log_target ON ghost_log(target_id);
        """)
        self._conn.commit()

    # ── Config ────────────────────────────────────────────────────────

    def is_enabled(self, guild_id: str) -> bool:
        row = self._conn.execute(
            "SELECT enabled FROM ghost_config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        return bool(row and row["enabled"])

    def is_stealth(self, guild_id: str) -> bool:
        row = self._conn.execute(
            "SELECT stealth_mode FROM ghost_config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        return bool(row and row["stealth_mode"])

    def set_enabled(self, guild_id: str, enabled: bool) -> None:
        self._conn.execute(
            """INSERT INTO ghost_config (guild_id, enabled, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at""",
            (guild_id, int(enabled), time.time()),
        )
        self._conn.commit()

    def set_stealth(self, guild_id: str, enabled: bool) -> None:
        self._conn.execute(
            """INSERT INTO ghost_config (guild_id, stealth_mode, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET stealth_mode=excluded.stealth_mode, updated_at=excluded.updated_at""",
            (guild_id, int(enabled), time.time()),
        )
        self._conn.commit()

    def set_log_channel(self, guild_id: str, channel_id: str) -> None:
        self._conn.execute(
            """INSERT INTO ghost_config (guild_id, log_channel_id, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET log_channel_id=excluded.log_channel_id, updated_at=excluded.updated_at""",
            (guild_id, channel_id, time.time()),
        )
        self._conn.commit()

    def set_muted_role(self, guild_id: str, role_id: str) -> None:
        self._conn.execute(
            """INSERT INTO ghost_config (guild_id, muted_role_id, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET muted_role_id=excluded.muted_role_id, updated_at=excluded.updated_at""",
            (guild_id, role_id, time.time()),
        )
        self._conn.commit()

    def get_config(self, guild_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM ghost_config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        if row:
            return dict(row)
        return {
            "guild_id": guild_id,
            "enabled": 0,
            "stealth_mode": 0,
            "log_channel_id": "",
            "muted_role_id": "",
            "updated_at": 0.0,
        }

    # ── Log ───────────────────────────────────────────────────────────

    def log_action(
        self,
        action: str,
        target_id: str,
        target_name: str,
        moderator_id: str,
        moderator_name: str,
        guild_id: str,
        reason: str = "",
        details: str = "",
    ) -> None:
        self._conn.execute(
            """INSERT INTO ghost_log
               (action, target_id, target_name, moderator_id, moderator_name,
                guild_id, reason, details, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (action, target_id, target_name, moderator_id, moderator_name,
             guild_id, reason, details, time.time()),
        )
        self._conn.commit()

    def get_log(
        self, guild_id: str, limit: int = 50, action: str = "",
    ) -> list[dict[str, Any]]:
        if action:
            rows = self._conn.execute(
                "SELECT * FROM ghost_log WHERE guild_id = ? AND action = ? ORDER BY timestamp DESC LIMIT ?",
                (guild_id, action, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM ghost_log WHERE guild_id = ? ORDER BY timestamp DESC LIMIT ?",
                (guild_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_user_log(self, target_id: str, guild_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM ghost_log WHERE target_id = ? AND guild_id = ? ORDER BY timestamp DESC LIMIT ?",
            (target_id, guild_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Shadow Mutes ──────────────────────────────────────────────────

    def add_shadow_mute(
        self,
        guild_id: str,
        user_id: str,
        role_id: str,
        muted_by_id: str,
        muted_by_name: str,
        reason: str = "",
        duration_minutes: int = 0,
    ) -> None:
        now = time.time()
        expires = now + (duration_minutes * 60) if duration_minutes > 0 else 0
        self._conn.execute(
            """INSERT OR REPLACE INTO shadow_mutes
               (guild_id, user_id, role_id, muted_by_id, muted_by_name, reason, muted_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, user_id, role_id, muted_by_id, muted_by_name, reason, now, expires),
        )
        self._conn.commit()

    def remove_shadow_mute(self, guild_id: str, user_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM shadow_mutes WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def is_shadow_muted(self, guild_id: str, user_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM shadow_mutes WHERE guild_id = ? AND user_id = ? AND (expires_at = 0 OR expires_at > ?)",
            (guild_id, user_id, time.time()),
        ).fetchone()
        return row is not None

    def get_active_mutes(self, guild_id: str) -> list[dict[str, Any]]:
        now = time.time()
        rows = self._conn.execute(
            "SELECT * FROM shadow_mutes WHERE guild_id = ? AND (expires_at = 0 OR expires_at > ?) ORDER BY muted_at DESC",
            (guild_id, now),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_expired_mutes(self) -> list[dict[str, Any]]:
        """Get all shadow mutes that have expired."""
        now = time.time()
        rows = self._conn.execute(
            "SELECT * FROM shadow_mutes WHERE expires_at > 0 AND expires_at <= ?",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]

    def cleanup_expired_mutes(self) -> int:
        rows = self._conn.execute(
            "SELECT * FROM shadow_mutes WHERE expires_at > 0 AND expires_at <= ?",
            (time.time(),),
        ).fetchall()
        self._conn.execute(
            "DELETE FROM shadow_mutes WHERE expires_at > 0 AND expires_at <= ?",
            (time.time(),),
        )
        self._conn.commit()
        return len(rows)

    def get_stats(self) -> dict[str, int]:
        total = self._conn.execute("SELECT COUNT(*) FROM ghost_log").fetchone()[0]
        by_guild = self._conn.execute("SELECT COUNT(DISTINCT guild_id) FROM ghost_log").fetchone()[0]
        total_mutes = self._conn.execute("SELECT COUNT(*) FROM shadow_mutes").fetchone()[0]
        active_mutes = self._conn.execute(
            "SELECT COUNT(*) FROM shadow_mutes WHERE (expires_at = 0 OR expires_at > ?)",
            (time.time(),),
        ).fetchone()[0]
        enabled_count = self._conn.execute(
            "SELECT COUNT(*) FROM ghost_config WHERE enabled = 1"
        ).fetchone()[0]
        return {
            "total_actions": total,
            "guilds_with_actions": by_guild,
            "total_mutes": total_mutes,
            "active_mutes": active_mutes,
            "enabled_servers": enabled_count,
        }

    def close(self) -> None:
        self._conn.close()


# ── Ghost Moderation Engine ────────────────────────────────────────────────

_db_instance: GhostDatabase | None = None


def get_db() -> GhostDatabase:
    global _db_instance
    if _db_instance is not None:
        try:
            _db_instance._conn.execute("SELECT 1")
            return _db_instance
        except sqlite3.ProgrammingError:
            _db_instance = None
    _db_instance = GhostDatabase()
    return _db_instance


async def ensure_muted_role(guild: discord.Guild) -> discord.Role | None:
    """Find or create the Shadow Muted role for a guild."""
    db = get_db()
    config = db.get_config(str(guild.id))
    role_id = config.get("muted_role_id", "")

    if role_id:
        role = guild.get_role(int(role_id))
        if role:
            return role

    existing = discord.utils.get(guild.roles, name=MUTED_ROLE_NAME)
    if existing:
        db.set_muted_role(str(guild.id), str(existing.id))
        return existing

    try:
        role = await guild.create_role(
            name=MUTED_ROLE_NAME,
            reason="Ghost moderation: shadow mute role",
            permissions=discord.Permissions.none(),
            mentionable=False,
        )
        db.set_muted_role(str(guild.id), str(role.id))

        for channel in guild.channels:
            with contextlib.suppress(discord.Forbidden):
                await channel.set_permissions(
                    role,
                    send_messages=False,
                    add_reactions=False,
                    speak=False,
                    create_public_threads=False,
                    create_private_threads=False,
                    send_messages_in_threads=False,
                )

        return role
    except discord.Forbidden:
        logger.warning("Missing permissions to create role in %s", guild.name)
        return None


async def apply_silent_delete(
    message: discord.Message,
    moderator_id: str = "",
    moderator_name: str = "",
) -> bool:
    """Silently delete a message using a generic audit log reason."""
    guild_id = str(message.guild.id) if message.guild else "dm"
    try:
        await message.delete(reason="Moderation action")
        db = get_db()
        db.log_action(
            GhostAction.DELETE.value,
            str(message.author.id) if message.author else "unknown",
            str(message.author) if message.author else "unknown",
            moderator_id or guild_id,
            moderator_name or "Auto-mod",
            guild_id,
            reason="Silent delete",
            details=f"Channel: {message.channel.name if hasattr(message.channel, 'name') else 'DM'}, Content preview: {message.content[:200] if message.content else 'N/A'}",
        )
        return True
    except discord.Forbidden:
        logger.warning("Missing permissions to delete message in %s", guild_id)
        return False
    except discord.NotFound:
        return False


async def apply_invisible_warn(
    member: discord.Member,
    reason: str,
    moderator_id: str = "",
    moderator_name: str = "",
) -> bool:
    """Warn a user via DM only, no channel announcement."""
    try:
        embed = discord.Embed(
            title="Moderation Notice",
            description=f"You have received a warning in **{member.guild.name}**.",
            color=0xFEE75C,
        )
        embed.add_field(name="Reason", value=reason[:1000], inline=False)
        embed.set_footer(text="This is an automated moderation notice.")
        await member.send(embed=embed)
        notified = True
    except (discord.Forbidden, discord.HTTPException):
        notified = False

    db = get_db()
    db.log_action(
        GhostAction.WARN.value,
        str(member.id),
        str(member),
        moderator_id or str(member.guild.id),
        moderator_name or "Auto-mod",
        str(member.guild.id),
        reason=reason,
        details=f"DM sent: {notified}",
    )
    return notified


async def apply_shadow_mute(
    member: discord.Member,
    reason: str = "",
    duration_minutes: int = 0,
    moderator_id: str = "",
    moderator_name: str = "",
) -> bool:
    """Shadow mute a user by assigning a muted role instead of timeout.

    Role assignments do not create audit log entries, making this
    completely invisible.
    """
    role = await ensure_muted_role(member.guild)
    if not role:
        return False

    try:
        await member.add_roles(role, reason="Moderation action")

        db = get_db()
        db.add_shadow_mute(
            str(member.guild.id),
            str(member.id),
            str(role.id),
            moderator_id or str(member.guild.id),
            moderator_name or "Auto-mod",
            reason=reason,
            duration_minutes=duration_minutes,
        )
        db.log_action(
            GhostAction.MUTE.value,
            str(member.id),
            str(member),
            moderator_id or str(member.guild.id),
            moderator_name or "Auto-mod",
            str(member.guild.id),
            reason=reason,
            details=f"Duration: {'permanent' if duration_minutes <= 0 else f'{duration_minutes} min'}, Role: {role.name}",
        )
        return True
    except discord.Forbidden:
        logger.warning("Missing permissions to add role in %s", member.guild.name)
        return False


async def remove_shadow_mute(
    member: discord.Member,
    moderator_id: str = "",
    moderator_name: str = "",
) -> bool:
    """Remove a shadow mute from a user."""
    db = get_db()
    role_id = db.get_config(str(member.guild.id)).get("muted_role_id", "")
    role = member.guild.get_role(int(role_id)) if role_id else None
    if not role:
        role = discord.utils.get(member.guild.roles, name=MUTED_ROLE_NAME)
    if not role:
        return False

    if role not in member.roles:
        db.remove_shadow_mute(str(member.guild.id), str(member.id))
        return True

    try:
        await member.remove_roles(role, reason="Moderation action")
        db.remove_shadow_mute(str(member.guild.id), str(member.id))
        db.log_action(
            GhostAction.UNMUTE.value,
            str(member.id),
            str(member),
            moderator_id or str(member.guild.id),
            moderator_name or "Auto-mod",
            str(member.guild.id),
            reason="Shadow mute removed",
        )
        return True
    except discord.Forbidden:
        return False


async def apply_ghost_kick(
    member: discord.Member,
    reason: str = "",
    moderator_id: str = "",
    moderator_name: str = "",
) -> bool:
    """Kick a user with minimal audit log detail."""
    try:
        await member.kick(reason="Moderation action")
        db = get_db()
        db.log_action(
            GhostAction.KICK.value,
            str(member.id),
            str(member),
            moderator_id or str(member.guild.id),
            moderator_name or "Auto-mod",
            str(member.guild.id),
            reason=reason,
            details="Ghost kick executed",
        )
        return True
    except discord.Forbidden:
        return False


async def send_ghost_log_embed(
    channel: discord.TextChannel,
    entry: dict[str, Any],
    db: GhostDatabase | None = None,
) -> None:
    """Send a ghost log embed to the designated log channel."""
    action_emoji = {
        "delete": "🗑️",
        "warn": "⚠️",
        "mute": "🔇",
        "unmute": "🔊",
        "kick": "👢",
        "stealth_on": "👻",
        "stealth_off": "👻",
    }
    emoji = action_emoji.get(entry["action"], "👻")
    color_map = {
        "delete": 0xED4245,
        "warn": 0xFEE75C,
        "mute": 0x5865F2,
        "unmute": 0x57F287,
        "kick": 0xED4245,
    }
    color = color_map.get(entry["action"], 0x95A5A6)

    embed = discord.Embed(
        title=f"{emoji} Ghost {entry['action'].title()}",
        color=color,
    )
    embed.add_field(name="Target", value=f"<@{entry['target_id']}> ({entry['target_name']})", inline=True)
    embed.add_field(name="Moderator", value=entry["moderator_name"], inline=True)
    if entry.get("reason"):
        embed.add_field(name="Reason", value=entry["reason"][:1000], inline=False)
    if entry.get("details"):
        embed.add_field(name="Details", value=entry["details"][:1000], inline=False)
    embed.set_footer(text=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(entry["timestamp"])))
    with contextlib.suppress(discord.Forbidden):
        await channel.send(embed=embed)


async def cleanup_expired_shadow_mutes(bot: discord.ext.commands.Bot) -> int:
    """Check all guilds for expired shadow mutes and remove them."""
    db = get_db()
    expired = db.get_expired_mutes()

    count = 0
    for row in expired:
        try:
            guild_id = int(row["guild_id"])
            user_id = int(row["user_id"])
        except (ValueError, TypeError):
            continue
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
        member = guild.get_member(user_id)
        if member:
            role_id = row["role_id"]
            role = guild.get_role(int(role_id)) if role_id else None
            if role and role in member.roles:
                with contextlib.suppress(discord.Forbidden):
                    await member.remove_roles(role, reason="Shadow mute expired")
        db.remove_shadow_mute(row["guild_id"], row["user_id"])
        count += 1

    return count
