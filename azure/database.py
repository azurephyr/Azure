"""
Azure Discord Bot - SQLite Database Layer

Provides persistent storage for:
- Conversation history
- User preferences
- Response cache (survives restarts)
- Statistics and analytics
- Subscription data

Uses SQLite for simplicity and zero-config deployment.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("azure.database")


@contextmanager
def _locked_conn_helper(get_conn, lock) -> Iterator[sqlite3.Connection]:
    """Serialize access to a shared SQLite connection.

    SQLite is not thread-safe across a single connection even with
    `check_same_thread=False`. Without this serialization, concurrent
    callers from different threads (bot + scheduler + web dashboard)
    surface as `cannot start a transaction within a transaction` and
    silently drop writes (KL-4).
    """
    with lock:
        yield get_conn()


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class ConversationMessage:
    """Represents a single message in conversation history."""
    id: int | None = None
    user_id: str = ""
    user_name: str = ""
    server_id: str = ""
    server_name: str = ""
    channel_id: str = ""
    channel_name: str = ""
    message: str = ""
    response: str = ""
    timestamp: float = 0.0
    cached: bool = False
    tokens_used: int = 0
    response_time_ms: int = 0


@dataclass
class UserPreference:
    """User-specific preferences and settings."""
    user_id: str = ""
    user_name: str = ""
    tier: str = "free"  # free, premium, enterprise
    context_size: int = 10
    temperature: float = 0.7
    language: str = "en"
    custom_system_prompt: str | None = None
    disabled: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class CacheEntry:
    """Persistent cache entry."""
    cache_key: str = ""
    prompt: str = ""
    response: str = ""
    user_id: str = ""
    server_id: str = ""
    hit_count: int = 0
    created_at: float = 0.0
    last_accessed: float = 0.0
    expires_at: float = 0.0


@dataclass
class BotStats:
    """Bot statistics and metrics."""
    id: int | None = None
    timestamp: float = 0.0
    messages_processed: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    errors: int = 0
    avg_response_time_ms: float = 0.0
    total_tokens_used: int = 0
    active_users: int = 0
    active_servers: int = 0


@dataclass
class AuditLogEntry:
    id: int | None = None
    timestamp: float = 0.0
    user_name: str = ""
    discord_id: str = ""
    ip_address: str = ""
    session_id: str = ""
    action: str = ""
    old_value: str = ""
    new_value: str = ""
    reason: str = ""
    subsystem: str = ""

@dataclass
class WebUser:
    discord_id: str = ""
    username: str = ""
    avatar_url: str = ""
    role: str = "user"
    last_login: float = 0.0
    created_at: float = 0.0

# =============================================================================
# Database Manager
# =============================================================================

class DatabaseManager:
    """Manages SQLite database connections and operations."""

    def _execute_with_retry(self, operation, max_retries=3):
        """Execute a database write with retry on transient errors."""
        import time as _time
        for attempt in range(max_retries):
            try:
                return operation()
            except sqlite3.OperationalError as e:
                if ("locked" in str(e).lower() or "busy" in str(e).lower()) and attempt < max_retries - 1:
                        _time.sleep(0.1 * (2 ** attempt))
                        continue
                raise

    def __init__(self, db_path: str | Path = "data/azure_bot.db") -> None:
        """Initialize database manager.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = None
        # Serialize concurrent access to the single shared SQLite
        # connection. SQLite is not thread-safe across a single
        # connection even with `check_same_thread=False`; without
        # serialization concurrent writers (bot + scheduler + web
        # dashboard) surface as `cannot start a transaction within a
        # transaction` and silently drop writes (KL-4 fix).
        self._wlock = threading.RLock()
        # Read-only connection pool — allows concurrent dashboard reads
        # without blocking writers.  SQLite WAL mode supports multiple
        # concurrent readers alongside a single writer.
        self._read_connections: list[sqlite3.Connection] = []
        self._read_pool_lock = threading.Lock()
        self._read_pool_index = 0  # round-robin counter
        self._init_database()
        logger.info(f"[database] Initialized: {self.db_path}")

    def _locked_conn(self) -> Iterator[sqlite3.Connection]:
        """Context manager that yields the shared connection under
        self._wlock. Use for any operation that performs SQL+commit.

        External callers (audit.py, web/api_moderation.py) that need
        raw access to the connection should also acquire this lock
        before using the connection (audit.py path uses
        `with self.db._wlock:` directly).
        """
        return _locked_conn_helper(self._get_connection, self._wlock)

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._connection is None:
            with self._wlock:
                if self._connection is None:
                    self._connection = sqlite3.connect(
                        str(self.db_path),
                        check_same_thread=False,
                        timeout=30.0,
                    )
                    self._connection.row_factory = sqlite3.Row
                    try:
                        self._connection.execute("PRAGMA journal_mode=WAL")
                        self._connection.execute("PRAGMA busy_timeout=5000")
                    except sqlite3.Error as e:
                        logger.warning("[database] PRAGMA setup failed: %s", e)
        return self._connection

    def _get_read_connection(self) -> sqlite3.Connection:
        """Return a read-only connection from the pool (round-robin).

        Read connections are created lazily on first access.  Each has
        ``PRAGMA journal_mode=WAL`` (for shared-state consistency with
        the writer) and ``PRAGMA query_only=ON`` so accidental writes
        raise an error instead of corrupting the pool semantics.
        """
        with self._read_pool_lock:
            pool_size = 3
            if len(self._read_connections) < pool_size:
                conn = sqlite3.connect(
                    str(self.db_path),
                    check_same_thread=False,
                    timeout=30.0,
                )
                conn.row_factory = sqlite3.Row
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA query_only=ON")
                    conn.execute("PRAGMA busy_timeout=5000")
                except sqlite3.Error as e:
                    logger.warning("[database] read-conn PRAGMA setup failed: %s", e)
                self._read_connections.append(conn)
                idx = len(self._read_connections) - 1
            else:
                idx = self._read_pool_index % pool_size
                self._read_pool_index = self._read_pool_index + 1
            return self._read_connections[idx]

    def _init_database(self) -> None:
        """Create database tables if they don't exist."""
        # Ensure the connection is created *before* acquiring _wlock.
        # _get_connection() itself acquires _wlock internally during
        # first-time creation, so nesting it inside our own
        # ``with self._wlock:`` block would deadlock because
        # threading.Lock is not reentrant.
        self._get_connection()
        with self._wlock:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Each statement is wrapped so a single failure doesn't block the rest.
            _statements = [
                # Conversation history table
                """CREATE TABLE IF NOT EXISTS conversation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    server_name TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    message TEXT NOT NULL,
                    response TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    cached INTEGER DEFAULT 0,
                    tokens_used INTEGER DEFAULT 0,
                    response_time_ms INTEGER DEFAULT 0
                )""",
                "CREATE INDEX IF NOT EXISTS idx_user_id ON conversation_history(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_server_id ON conversation_history(server_id)",
                "CREATE INDEX IF NOT EXISTS idx_timestamp ON conversation_history(timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_user_ts ON conversation_history(user_id, timestamp)",
                # User preferences table
                """CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id TEXT PRIMARY KEY,
                    user_name TEXT NOT NULL,
                    tier TEXT DEFAULT 'free',
                    context_size INTEGER DEFAULT 10,
                    temperature REAL DEFAULT 0.7,
                    language TEXT DEFAULT 'en',
                    custom_system_prompt TEXT,
                    disabled INTEGER DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )""",
                # Cache table
                """CREATE TABLE IF NOT EXISTS response_cache (
                    cache_key TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL,
                    response TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    hit_count INTEGER DEFAULT 0,
                    created_at REAL NOT NULL,
                    last_accessed REAL NOT NULL,
                    expires_at REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_cache_expires ON response_cache(expires_at)",
                "CREATE INDEX IF NOT EXISTS idx_cache_user ON response_cache(user_id)",
                # Statistics table
                """CREATE TABLE IF NOT EXISTS bot_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    messages_processed INTEGER DEFAULT 0,
                    cache_hits INTEGER DEFAULT 0,
                    cache_misses INTEGER DEFAULT 0,
                    errors INTEGER DEFAULT 0,
                    avg_response_time_ms REAL DEFAULT 0.0,
                    total_tokens_used INTEGER DEFAULT 0,
                    active_users INTEGER DEFAULT 0,
                    active_servers INTEGER DEFAULT 0
                )""",
                "CREATE INDEX IF NOT EXISTS idx_stats_timestamp ON bot_stats(timestamp)",
                # Audit logs
                """CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    user_name TEXT NOT NULL,
                    discord_id TEXT NOT NULL,
                    ip_address TEXT,
                    session_id TEXT,
                    action TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    reason TEXT,
                    subsystem TEXT NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(discord_id)",
                # Access Control table
                """CREATE TABLE IF NOT EXISTS access_control (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    added_by TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_ac_target ON access_control(target_id)",
                # Security Events table
                """CREATE TABLE IF NOT EXISTS security_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    user_id TEXT NOT NULL,
                    guild_id TEXT,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    details TEXT NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_sec_time ON security_events(timestamp)",
                # Telemetry Logs table
                """CREATE TABLE IF NOT EXISTS telemetry_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    subsystem TEXT NOT NULL,
                    action TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_tel_exec ON telemetry_logs(execution_id)",
                # Web users
                """CREATE TABLE IF NOT EXISTS web_users (
                    discord_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    avatar_url TEXT,
                    role TEXT DEFAULT 'user',
                    last_login REAL NOT NULL,
                    created_at REAL NOT NULL
                )""",
                # API keys
                """CREATE TABLE IF NOT EXISTS api_keys (
                    key_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    scopes TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_used REAL NOT NULL,
                    expires_at REAL NOT NULL
                )""",
            ]

            for stmt in _statements:
                try:
                    cursor.execute(stmt)
                except Exception as e:
                    logger.warning("[database] Schema statement failed: %s — %s", e, stmt.strip()[:80])

            conn.commit()
        logger.info("[database] Schema initialized")

    # =========================================================================
    # Conversation History
    # =========================================================================

    def save_conversation(self, msg: ConversationMessage) -> int:
        """Save a conversation message to database.

        Args:
            msg: ConversationMessage instance

        Returns:
            Database row ID
        """
        def _do_save():
            with self._locked_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO conversation_history (
                        user_id, user_name, server_id, server_name,
                        channel_id, channel_name, message, response,
                        timestamp, cached, tokens_used, response_time_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    msg.user_id, msg.user_name, msg.server_id, msg.server_name,
                    msg.channel_id, msg.channel_name, msg.message, msg.response,
                    msg.timestamp, int(msg.cached), msg.tokens_used, msg.response_time_ms
                ))
                conn.commit()
                return cursor.lastrowid
        return self._execute_with_retry(_do_save)

    def get_conversation_history(
        self,
        user_id: str | None = None,
        server_id: str | None = None,
        limit: int = 100,
        since: float | None = None
    ) -> list[ConversationMessage]:
        """Retrieve conversation history with filters.

        Args:
            user_id: Filter by user ID (optional)
            server_id: Filter by server ID (optional)
            limit: Maximum number of results
            since: Only return messages after this timestamp (optional)

        Returns:
            List of ConversationMessage objects
        """
        conn = self._get_read_connection()
        cursor = conn.cursor()

        query = "SELECT * FROM conversation_history WHERE 1=1"
        params: list[Any] = []

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)

        if server_id:
            query += " AND server_id = ?"
            params.append(server_id)

        if since:
            query += " AND timestamp > ?"
            params.append(since)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()

        return [ConversationMessage(
            id=row["id"],
            user_id=row["user_id"],
            user_name=row["user_name"],
            server_id=row["server_id"],
            server_name=row["server_name"],
            channel_id=row["channel_id"],
            channel_name=row["channel_name"],
            message=row["message"],
            response=row["response"],
            timestamp=row["timestamp"],
            cached=bool(row["cached"]),
            tokens_used=row["tokens_used"],
            response_time_ms=row["response_time_ms"]
        ) for row in rows]

    # =========================================================================
    # User Preferences
    # =========================================================================

    def save_user_preference(self, pref: UserPreference) -> None:
        """Save or update user preferences.

        Args:
            pref: UserPreference instance
        """
        def _do_save():
            with self._locked_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO user_preferences (
                        user_id, user_name, tier, context_size, temperature,
                        language, custom_system_prompt, disabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    pref.user_id, pref.user_name, pref.tier, pref.context_size,
                    pref.temperature, pref.language, pref.custom_system_prompt,
                    int(pref.disabled), pref.created_at, pref.updated_at
                ))
                conn.commit()
        self._execute_with_retry(_do_save)

    def get_user_preference(self, user_id: str) -> UserPreference | None:
        """Retrieve user preferences.

        Args:
            user_id: User ID to lookup

        Returns:
            UserPreference instance or None if not found
        """
        conn = self._get_read_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()

        if not row:
            return None

        return UserPreference(
            user_id=row["user_id"],
            user_name=row["user_name"],
            tier=row["tier"],
            context_size=row["context_size"],
            temperature=row["temperature"],
            language=row["language"],
            custom_system_prompt=row["custom_system_prompt"],
            disabled=bool(row["disabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"]
        )

    # =========================================================================
    # Response Cache
    # =========================================================================

    def save_cache_entry(self, entry: CacheEntry) -> None:
        """Save a cache entry to database.

        Args:
            entry: CacheEntry instance
        """
        def _do_save():
            with self._locked_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO response_cache (
                        cache_key, prompt, response, user_id, server_id,
                        hit_count, created_at, last_accessed, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    entry.cache_key, entry.prompt, entry.response, entry.user_id,
                    entry.server_id, entry.hit_count, entry.created_at,
                    entry.last_accessed, entry.expires_at
                ))
                conn.commit()
        self._execute_with_retry(_do_save)

    def get_cache_entry(self, cache_key: str) -> CacheEntry | None:
        """Retrieve a cache entry and bump hit stats.

        Must hold `_wlock` for the SELECT+UPDATE+commit unit. A bare
        `_get_connection()` write races with other locked writers and
        reproduces the KL-4 failure class
        (`cannot start a transaction within a transaction` / dropped hits).
        """
        now = time.time()
        with self._locked_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM response_cache
                WHERE cache_key = ? AND expires_at > ?
            """, (cache_key, now))
            row = cursor.fetchone()
            if not row:
                return None

            # Hit accounting is a write — keep it inside the same locked txn.
            cursor.execute("""
                UPDATE response_cache
                SET hit_count = hit_count + 1, last_accessed = ?
                WHERE cache_key = ?
            """, (now, cache_key))
            conn.commit()

            return CacheEntry(
                cache_key=row["cache_key"],
                prompt=row["prompt"],
                response=row["response"],
                user_id=row["user_id"],
                server_id=row["server_id"],
                hit_count=row["hit_count"] + 1,
                created_at=row["created_at"],
                last_accessed=now,
                expires_at=row["expires_at"]
            )

    def cleanup_expired_cache(self) -> int:
        """Remove expired cache entries.

        Returns:
            Number of entries removed
        """
        with self._locked_conn() as conn:
            cursor = conn.cursor()

            now = time.time()
            cursor.execute("DELETE FROM response_cache WHERE expires_at <= ?", (now,))
            conn.commit()

            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"[database] Cleaned up {deleted} expired cache entries")

            return deleted

    # =========================================================================
    # Statistics
    # =========================================================================

    def save_stats(self, stats: BotStats) -> int:
        """Save bot statistics snapshot.

        Args:
            stats: BotStats instance

        Returns:
            Database row ID
        """
        def _do_save():
            with self._locked_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO bot_stats (
                        timestamp, messages_processed, cache_hits, cache_misses,
                        errors, avg_response_time_ms, total_tokens_used,
                        active_users, active_servers
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    stats.timestamp, stats.messages_processed, stats.cache_hits,
                    stats.cache_misses, stats.errors, stats.avg_response_time_ms,
                    stats.total_tokens_used, stats.active_users, stats.active_servers
                ))
                conn.commit()
                return cursor.lastrowid
        return self._execute_with_retry(_do_save)

    def get_stats_history(
        self,
        hours: int = 24,
        limit: int = 1000
    ) -> list[BotStats]:
        """Retrieve recent statistics history.

        Args:
            hours: Number of hours to look back
            limit: Maximum number of results

        Returns:
            List of BotStats objects
        """
        since = time.time() - (hours * 3600)
        conn = self._get_read_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM bot_stats
            WHERE timestamp > ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (since, limit))
        rows = cursor.fetchall()

        return [BotStats(
            id=row["id"],
            timestamp=row["timestamp"],
            messages_processed=row["messages_processed"],
            cache_hits=row["cache_hits"],
            cache_misses=row["cache_misses"],
            errors=row["errors"],
            avg_response_time_ms=row["avg_response_time_ms"],
            total_tokens_used=row["total_tokens_used"],
            active_users=row["active_users"],
            active_servers=row["active_servers"]
        ) for row in rows]

    def get_aggregate_stats(self, hours: int = 24) -> dict[str, Any]:
        """Get aggregated statistics for a time period.

        Args:
            hours: Number of hours to aggregate

        Returns:
            Dictionary with aggregated stats
        """
        since = time.time() - (hours * 3600)
        conn = self._get_read_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                SUM(messages_processed) as total_messages,
                SUM(cache_hits) as total_cache_hits,
                SUM(cache_misses) as total_cache_misses,
                SUM(errors) as total_errors,
                AVG(avg_response_time_ms) as avg_response_time,
                SUM(total_tokens_used) as total_tokens,
                MAX(active_users) as peak_users,
                MAX(active_servers) as peak_servers
            FROM bot_stats
            WHERE timestamp > ?
        """, (since,))
        row = cursor.fetchone()

        if not row:
            return {
                "total_messages": 0,
                "total_cache_hits": 0,
                "total_cache_misses": 0,
                "total_errors": 0,
                "avg_response_time_ms": 0.0,
                "total_tokens": 0,
                "peak_users": 0,
                "peak_servers": 0,
                "cache_hit_rate": 0.0,
            }

        hits = row["total_cache_hits"] or 0
        misses = row["total_cache_misses"] or 0
        return {
            "total_messages": row["total_messages"] or 0,
            "total_cache_hits": hits,
            "total_cache_misses": misses,
            "total_errors": row["total_errors"] or 0,
            "avg_response_time_ms": row["avg_response_time"] or 0.0,
            "total_tokens": row["total_tokens"] or 0,
            "peak_users": row["peak_users"] or 0,
            "peak_servers": row["peak_servers"] or 0,
            "cache_hit_rate": (hits / (hits + misses)) if (hits + misses) > 0 else 0.0,
        }

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def vacuum(self) -> None:
        """Optimize database (reclaim space, rebuild indexes)."""
        with self._locked_conn() as conn:
            conn.execute("VACUUM")
            conn.commit()
            logger.info("[database] Database optimized")

    def close(self) -> None:
        """Close all database connections under the write lock (no in-flight SQL)."""
        with self._wlock:
            if self._connection:
                try:
                    self._connection.close()
                finally:
                    self._connection = None
                logger.info("[database] Write connection closed")
        with self._read_pool_lock:
            for conn in self._read_connections:
                try:
                    conn.close()
                except Exception as e:
                    logger.error("Failed to close read connection: %s", e)
            self._read_connections.clear()
            logger.info("[database] Read pool closed")

    # =========================================================================
    # Control Center: Access Control, Security, Telemetry
    # =========================================================================

    def set_access_control(self, target_type: str, target_id: str, permission: str, added_by: str) -> None:
        """Set an access control rule (whitelist/blacklist/role) for a user, channel, or guild."""
        with self._locked_conn() as conn:
            cursor = conn.cursor()
            # Remove any existing conflicting rule for this target_id
            cursor.execute("DELETE FROM access_control WHERE target_id = ?", (target_id,))
            cursor.execute("""
                INSERT INTO access_control (target_type, target_id, permission, enabled, added_by, timestamp)
                VALUES (?, ?, ?, 1, ?, ?)
            """, (target_type, target_id, permission, added_by, time.time()))
            conn.commit()

    def get_access_control(self, target_id: str) -> str | None:
        """Get the access permission for a target_id, if any."""
        conn = self._get_read_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT permission FROM access_control WHERE target_id = ? AND enabled = 1", (target_id,))
        row = cursor.fetchone()
        return row["permission"] if row else None

    def log_security_event(self, user_id: str, guild_id: str, event_type: str, severity: str, details: str) -> None:
        """Log a security event like a jailbreak attempt or spam."""
        with self._locked_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO security_events (timestamp, user_id, guild_id, event_type, severity, details)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (time.time(), user_id, guild_id, event_type, severity, details))
            conn.commit()

    def log_telemetry(self, execution_id: str, subsystem: str, action: str, message: str, status: str) -> None:
        """Log a telemetry trace from an execution."""
        with self._locked_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO telemetry_logs (execution_id, timestamp, subsystem, action, message, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (execution_id, time.time(), subsystem, action, message, status))
            conn.commit()

    def __enter__(self) -> DatabaseManager:
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()


# =============================================================================
# Process-wide singleton — bot, web dashboard, handlers share one DB instance
# =============================================================================

_shared_db: DatabaseManager | None = None
_shared_db_lock = threading.Lock()


def get_shared_db(db_path: str | Path = "data/azure_bot.db") -> DatabaseManager:
    """Return the process-wide DatabaseManager (create once).

    Multiple `DatabaseManager()` constructions open multiple connections and
    lose shared in-memory coordination. Bot + FastAPI must share this instance
    so dashboard stats/audit/telemetry match the live bot writes.
    """
    global _shared_db
    with _shared_db_lock:
        if _shared_db is None:
            _shared_db = DatabaseManager(db_path=db_path)
        return _shared_db


def set_shared_db(db: DatabaseManager) -> DatabaseManager:
    """Install an existing manager as the process singleton (tests / custom paths)."""
    global _shared_db
    with _shared_db_lock:
        _shared_db = db
        return _shared_db
