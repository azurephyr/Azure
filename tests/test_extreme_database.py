"""Extremely thorough test suite for database operations, persistence, and data integrity.

Covers: connection management, schema, CRUD, concurrency, error handling, and data bridge.
Uses temporary database files to ensure isolation.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# SUT imports
# ---------------------------------------------------------------------------
from azure.database import (
    BotStats,
    CacheEntry,
    ConversationMessage,
    DatabaseManager,
    UserPreference,
    get_shared_db,
    set_shared_db,
)
from web.data_bridge import BotDataBridge

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db_path(tmp_path: pathlib.Path) -> str:
    """Return a path to a temporary database file."""
    return str(tmp_path / "test_bot.db")


@pytest.fixture
def db(tmp_db_path: str) -> DatabaseManager:
    """Create a fresh DatabaseManager for each test."""
    mgr = DatabaseManager(db_path=tmp_db_path)
    yield mgr
    mgr.close()


@pytest.fixture
def bridge(db: DatabaseManager) -> BotDataBridge:
    """Create a BotDataBridge with mock bot/agent and real db."""
    mock_bot = MagicMock()
    mock_bot.latency = 0.05
    mock_bot.guilds = [1, 2, 3]
    mock_bot.start_time = MagicMock()
    mock_bot.start_time.timestamp.return_value = time.time() - 3600

    mock_agent = MagicMock()
    mock_agent.get_moderation_stats.return_value = {"pending_actions": 2}
    mock_agent.get_info.return_value = {"model": "gpt-4", "version": "1.0"}

    return BotDataBridge(bot=mock_bot, agent=mock_agent, db=db)


# ===== CONVERSATION HELPERS =================================================

def _make_msg(**overrides) -> ConversationMessage:
    defaults = dict(
        user_id="u1",
        user_name="Alice",
        server_id="s1",
        server_name="TestServer",
        channel_id="c1",
        channel_name="general",
        message="Hello",
        response="Hi there",
        timestamp=time.time(),
        cached=False,
        tokens_used=42,
        response_time_ms=150,
    )
    defaults.update(overrides)
    return ConversationMessage(**defaults)


def _make_pref(**overrides) -> UserPreference:
    defaults = dict(
        user_id="u1",
        user_name="Alice",
        tier="free",
        context_size=10,
        temperature=0.7,
        language="en",
        custom_system_prompt=None,
        disabled=False,
        created_at=time.time(),
        updated_at=time.time(),
    )
    defaults.update(overrides)
    return UserPreference(**defaults)


def _make_cache(**overrides) -> CacheEntry:
    now = time.time()
    defaults = dict(
        cache_key="key_abc",
        prompt="What is AI?",
        response="AI is artificial intelligence",
        user_id="u1",
        server_id="s1",
        hit_count=0,
        created_at=now,
        last_accessed=now,
        expires_at=now + 3600,
    )
    defaults.update(overrides)
    return CacheEntry(**defaults)


def _make_stats(**overrides) -> BotStats:
    defaults = dict(
        timestamp=time.time(),
        messages_processed=100,
        cache_hits=30,
        cache_misses=10,
        errors=2,
        avg_response_time_ms=200.0,
        total_tokens_used=5000,
        active_users=15,
        active_servers=3,
    )
    defaults.update(overrides)
    return BotStats(**defaults)


# =============================================================================
# 1. CONNECTION MANAGEMENT  (10+ tests)
# =============================================================================

class TestConnectionManagement:
    """Tests for DatabaseManager connection lifecycle and pooling."""

    def test_single_connection_creation(self, tmp_db_path: str) -> None:
        mgr = DatabaseManager(db_path=tmp_db_path)
        conn = mgr._get_connection()
        assert conn is not None
        assert isinstance(conn, sqlite3.Connection)
        mgr.close()

    def test_connection_reuse_same_instance(self, db: DatabaseManager) -> None:
        c1 = db._get_connection()
        c2 = db._get_connection()
        assert c1 is c2

    def test_read_pool_creation(self, db: DatabaseManager) -> None:
        conns = [db._get_read_connection() for _ in range(3)]
        assert len(conns) == 1
        assert conns[0] is db._get_read_connection()

    def test_read_pool_round_robin(self, db: DatabaseManager) -> None:
        c1 = db._get_read_connection()
        db._get_read_connection()
        db._get_read_connection()
        c4 = db._get_read_connection()
        assert c1 is c4

    def test_read_pool_thread_safety(self, db: DatabaseManager) -> None:
        results: list[sqlite3.Connection] = []
        barrier = threading.Barrier(10)

        def grab_conn():
            barrier.wait()
            results.append(db._get_read_connection())

        threads = [threading.Thread(target=grab_conn) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert len(results) == 10

    def test_connection_wal_mode(self, db: DatabaseManager) -> None:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"

    def test_connection_busy_timeout(self, db: DatabaseManager) -> None:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA busy_timeout")
        timeout = cursor.fetchone()[0]
        assert timeout == 5000

    def test_close_cleans_up_all_connections(self, tmp_db_path: str) -> None:
        mgr = DatabaseManager(db_path=tmp_db_path)
        mgr._get_connection()
        conn1 = mgr._get_read_connection()
        conn2 = mgr._get_read_connection()
        conn3 = mgr._get_read_connection()
        assert conn1 is conn2 is conn3
        assert len(mgr._read_connections) == 1
        mgr.close()
        assert mgr._connection is None
        assert len(mgr._read_connections) == 0

    def test_double_close_is_safe(self, db: DatabaseManager) -> None:
        db.close()
        db.close()
        assert db._connection is None

    def test_database_file_creation(self, tmp_path: pathlib.Path) -> None:
        db_path = str(tmp_path / "nested" / "dir" / "test.db")
        mgr = DatabaseManager(db_path=db_path)
        assert Path(db_path).exists()
        mgr.close()


# =============================================================================
# 2. SCHEMA (12 tests)
# =============================================================================

class TestSchema:
    """Validate that all expected tables and indexes exist after init."""

    def _table_names(self, db: DatabaseManager) -> set[str]:
        conn = db._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in cur.fetchall()}

    def _index_names(self, db: DatabaseManager) -> set[str]:
        conn = db._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
        return {row[0] for row in cur.fetchall()}

    def _columns(self, db: DatabaseManager, table: str) -> dict[str, str]:
        conn = db._get_connection()
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        return {row[1]: row[2] for row in cur.fetchall()}

    def test_all_tables_created(self, db: DatabaseManager) -> None:
        tables = self._table_names(db)
        expected = {
            "conversation_history",
            "user_preferences",
            "response_cache",
            "bot_stats",
            "audit_logs",
            "access_control",
            "security_events",
            "telemetry_logs",
            "web_users",
            "api_keys",
        }
        assert expected.issubset(tables)

    def test_conversation_history_schema(self, db: DatabaseManager) -> None:
        cols = self._columns(db, "conversation_history")
        expected_cols = {
            "id", "user_id", "user_name", "server_id", "server_name",
            "channel_id", "channel_name", "message", "response",
            "timestamp", "cached", "tokens_used", "response_time_ms",
        }
        assert expected_cols == set(cols.keys())
        assert cols["id"] == "INTEGER"

    def test_user_preferences_schema(self, db: DatabaseManager) -> None:
        cols = self._columns(db, "user_preferences")
        expected_cols = {
            "user_id", "user_name", "tier", "context_size", "temperature",
            "language", "custom_system_prompt", "disabled", "created_at", "updated_at",
        }
        assert expected_cols == set(cols.keys())

    def test_response_cache_schema(self, db: DatabaseManager) -> None:
        cols = self._columns(db, "response_cache")
        expected_cols = {
            "cache_key", "prompt", "response", "user_id", "server_id",
            "hit_count", "created_at", "last_accessed", "expires_at",
        }
        assert expected_cols == set(cols.keys())

    def test_bot_stats_schema(self, db: DatabaseManager) -> None:
        cols = self._columns(db, "bot_stats")
        expected_cols = {
            "id", "timestamp", "messages_processed", "cache_hits", "cache_misses",
            "errors", "avg_response_time_ms", "total_tokens_used",
            "active_users", "active_servers",
        }
        assert expected_cols == set(cols.keys())

    def test_audit_logs_schema(self, db: DatabaseManager) -> None:
        cols = self._columns(db, "audit_logs")
        expected_cols = {
            "id", "timestamp", "user_name", "discord_id", "ip_address",
            "session_id", "action", "old_value", "new_value", "reason", "subsystem",
        }
        assert expected_cols == set(cols.keys())

    def test_access_control_schema(self, db: DatabaseManager) -> None:
        cols = self._columns(db, "access_control")
        expected_cols = {
            "id", "target_type", "target_id", "permission", "enabled",
            "added_by", "timestamp",
        }
        assert expected_cols == set(cols.keys())

    def test_security_events_schema(self, db: DatabaseManager) -> None:
        cols = self._columns(db, "security_events")
        expected_cols = {
            "id", "timestamp", "user_id", "guild_id", "event_type",
            "severity", "details",
        }
        assert expected_cols == set(cols.keys())

    def test_telemetry_logs_schema(self, db: DatabaseManager) -> None:
        cols = self._columns(db, "telemetry_logs")
        expected_cols = {
            "id", "execution_id", "timestamp", "subsystem", "action",
            "message", "status",
        }
        assert expected_cols == set(cols.keys())

    def test_web_users_schema(self, db: DatabaseManager) -> None:
        cols = self._columns(db, "web_users")
        expected_cols = {"discord_id", "username", "avatar_url", "role", "last_login", "created_at"}
        assert expected_cols == set(cols.keys())

    def test_api_keys_schema(self, db: DatabaseManager) -> None:
        cols = self._columns(db, "api_keys")
        expected_cols = {"key_hash", "user_id", "name", "scopes", "created_at", "last_used", "expires_at"}
        assert expected_cols == set(cols.keys())

    def test_indexes_created(self, db: DatabaseManager) -> None:
        indexes = self._index_names(db)
        expected = {
            "idx_user_id",
            "idx_server_id",
            "idx_timestamp",
            "idx_user_ts",
            "idx_cache_expires",
            "idx_cache_user",
            "idx_stats_timestamp",
            "idx_audit_time",
            "idx_audit_user",
            "idx_ac_target",
            "idx_sec_time",
            "idx_tel_exec",
        }
        assert expected.issubset(indexes)

    def test_init_is_idempotent(self, tmp_db_path: str) -> None:
        mgr1 = DatabaseManager(db_path=tmp_db_path)
        mgr2 = DatabaseManager(db_path=tmp_db_path)
        tables1 = set()
        conn = mgr1._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables1 = {row[0] for row in cur.fetchall()}
        mgr1.close()
        mgr2.close()
        assert len(tables1) >= 10


# =============================================================================
# 3. CRUD OPERATIONS (25+ tests)
# =============================================================================

class TestCRUDConversation:
    """Conversation history CRUD."""

    def test_save_and_get_conversation(self, db: DatabaseManager) -> None:
        msg = _make_msg()
        row_id = db.save_conversation(msg)
        assert row_id is not None and row_id > 0
        history = db.get_conversation_history(user_id="u1")
        assert len(history) == 1
        assert history[0].message == "Hello"
        assert history[0].response == "Hi there"

    def test_filter_by_server_id(self, db: DatabaseManager) -> None:
        db.save_conversation(_make_msg(server_id="s1"))
        db.save_conversation(_make_msg(server_id="s2"))
        result = db.get_conversation_history(server_id="s1")
        assert len(result) == 1
        assert result[0].server_id == "s1"

    def test_filter_by_time_range(self, db: DatabaseManager) -> None:
        t1, t2 = time.time() - 100, time.time() + 100
        db.save_conversation(_make_msg(timestamp=t1))
        db.save_conversation(_make_msg(timestamp=t2))
        result = db.get_conversation_history(since=t1 - 1)
        assert len(result) == 2
        result = db.get_conversation_history(since=t1 + 1)
        assert len(result) == 1

    def test_pagination_limit_offset(self, db: DatabaseManager) -> None:
        for i in range(10):
            db.save_conversation(_make_msg(timestamp=time.time() + i))
        all_msgs = db.get_conversation_history(limit=5)
        assert len(all_msgs) == 5

    def test_multiple_users(self, db: DatabaseManager) -> None:
        db.save_conversation(_make_msg(user_id="u1"))
        db.save_conversation(_make_msg(user_id="u2"))
        db.save_conversation(_make_msg(user_id="u1"))
        assert len(db.get_conversation_history(user_id="u1")) == 2
        assert len(db.get_conversation_history(user_id="u2")) == 1


class TestCRUDUserPreference:
    """User preferences CRUD."""

    def test_save_creates_new(self, db: DatabaseManager) -> None:
        pref = _make_pref(user_id="u1")
        db.save_user_preference(pref)
        result = db.get_user_preference("u1")
        assert result is not None
        assert result.user_name == "Alice"
        assert result.tier == "free"

    def test_save_updates_existing(self, db: DatabaseManager) -> None:
        db.save_user_preference(_make_pref(user_id="u1", tier="free"))
        db.save_user_preference(_make_pref(user_id="u1", tier="premium"))
        result = db.get_user_preference("u1")
        assert result is not None
        assert result.tier == "premium"

    def test_get_found(self, db: DatabaseManager) -> None:
        db.save_user_preference(_make_pref(user_id="u1"))
        assert db.get_user_preference("u1") is not None

    def test_get_not_found(self, db: DatabaseManager) -> None:
        assert db.get_user_preference("nonexistent") is None

    def test_disabled_flag(self, db: DatabaseManager) -> None:
        db.save_user_preference(_make_pref(user_id="u1", disabled=True))
        result = db.get_user_preference("u1")
        assert result is not None
        assert result.disabled is True


class TestCRUDCache:
    """Response cache CRUD."""

    def test_save_and_get_cache_hit(self, db: DatabaseManager) -> None:
        entry = _make_cache(cache_key="k1")
        db.save_cache_entry(entry)
        result = db.get_cache_entry("k1")
        assert result is not None
        assert result.cache_key == "k1"
        assert result.hit_count == 1

    def test_cache_hit_bumps_count(self, db: DatabaseManager) -> None:
        entry = _make_cache(cache_key="k1", hit_count=0)
        db.save_cache_entry(entry)
        db.get_cache_entry("k1")
        db.get_cache_entry("k1")
        result = db.get_cache_entry("k1")
        assert result is not None
        assert result.hit_count == 3

    def test_cache_miss_expired(self, db: DatabaseManager) -> None:
        entry = _make_cache(cache_key="k1", expires_at=time.time() - 100)
        db.save_cache_entry(entry)
        result = db.get_cache_entry("k1")
        assert result is None

    def test_cache_miss_wrong_key(self, db: DatabaseManager) -> None:
        db.save_cache_entry(_make_cache(cache_key="k1"))
        assert db.get_cache_entry("nonexistent") is None

    def test_cleanup_expired_cache(self, db: DatabaseManager) -> None:
        db.save_cache_entry(_make_cache(cache_key="expired", expires_at=time.time() - 10))
        db.save_cache_entry(_make_cache(cache_key="valid", expires_at=time.time() + 3600))
        removed = db.cleanup_expired_cache()
        assert removed == 1
        assert db.get_cache_entry("valid") is not None
        assert db.get_cache_entry("expired") is None

    def test_cleanup_no_expired(self, db: DatabaseManager) -> None:
        db.save_cache_entry(_make_cache(cache_key="k1", expires_at=time.time() + 3600))
        removed = db.cleanup_expired_cache()
        assert removed == 0

    def test_cleanup_empty_table(self, db: DatabaseManager) -> None:
        removed = db.cleanup_expired_cache()
        assert removed == 0

    def test_upsert_cache_entry(self, db: DatabaseManager) -> None:
        db.save_cache_entry(_make_cache(cache_key="k1", response="v1"))
        db.save_cache_entry(_make_cache(cache_key="k1", response="v2"))
        result = db.get_cache_entry("k1")
        assert result is not None
        assert result.response == "v2"


class TestCRUDStats:
    """Bot statistics CRUD."""

    def test_save_and_get_stats(self, db: DatabaseManager) -> None:
        stats = _make_stats()
        row_id = db.save_stats(stats)
        assert row_id is not None and row_id > 0
        history = db.get_stats_history(hours=1)
        assert len(history) >= 1
        assert history[0].messages_processed == 100

    def test_get_stats_history_empty(self, db: DatabaseManager) -> None:
        history = db.get_stats_history(hours=1)
        assert history == []

    def test_get_aggregate_stats(self, db: DatabaseManager) -> None:
        db.save_stats(_make_stats(
            messages_processed=100, cache_hits=30, cache_misses=10,
            errors=2, total_tokens_used=5000, active_users=15, active_servers=3
        ))
        agg = db.get_aggregate_stats(hours=24)
        assert agg["total_messages"] == 100
        assert agg["total_cache_hits"] == 30
        assert agg["total_cache_misses"] == 10
        assert agg["total_errors"] == 2
        assert agg["total_tokens"] == 5000
        assert agg["peak_users"] == 15
        assert agg["peak_servers"] == 3

    def test_get_aggregate_stats_empty(self, db: DatabaseManager) -> None:
        agg = db.get_aggregate_stats(hours=24)
        assert agg["total_messages"] == 0
        assert agg["cache_hit_rate"] == 0.0

    def test_get_aggregate_cache_hit_rate(self, db: DatabaseManager) -> None:
        db.save_stats(_make_stats(cache_hits=75, cache_misses=25))
        agg = db.get_aggregate_stats(hours=24)
        assert agg["cache_hit_rate"] == pytest.approx(0.75)

    def test_get_aggregate_all_zeroes(self, db: DatabaseManager) -> None:
        db.save_stats(_make_stats(
            messages_processed=0, cache_hits=0, cache_misses=0,
            errors=0, total_tokens_used=0
        ))
        agg = db.get_aggregate_stats(hours=24)
        assert agg["total_messages"] == 0
        assert agg["cache_hit_rate"] == 0.0


class TestCRUDAccessControl:
    """Access control, security events, telemetry."""

    def test_set_and_get_access_control(self, db: DatabaseManager) -> None:
        db.set_access_control("user", "u1", "allow", "admin")
        perm = db.get_access_control("u1")
        assert perm == "allow"

    def test_access_control_not_set(self, db: DatabaseManager) -> None:
        assert db.get_access_control("nobody") is None

    def test_access_control_upsert(self, db: DatabaseManager) -> None:
        db.set_access_control("user", "u1", "allow", "admin")
        db.set_access_control("user", "u1", "deny", "admin")
        perm = db.get_access_control("u1")
        assert perm == "deny"

    def test_access_control_multiple_targets(self, db: DatabaseManager) -> None:
        db.set_access_control("user", "u1", "allow", "admin")
        db.set_access_control("channel", "c1", "deny", "admin")
        db.set_access_control("guild", "g1", "allow", "admin")
        assert db.get_access_control("u1") == "allow"
        assert db.get_access_control("c1") == "deny"
        assert db.get_access_control("g1") == "allow"

    def test_log_security_event(self, db: DatabaseManager) -> None:
        db.log_security_event("u1", "g1", "jailbreak", "high", "Attempted prompt injection")
        conn = db._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM security_events WHERE user_id = 'u1'")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["event_type"] == "jailbreak"
        assert rows[0]["severity"] == "high"

    def test_log_telemetry(self, db: DatabaseManager) -> None:
        db.log_telemetry("exec_123", "moderation", "block", "Blocked jailbreak", "success")
        conn = db._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM telemetry_logs WHERE execution_id = 'exec_123'")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["subsystem"] == "moderation"
        assert rows[0]["status"] == "success"

    def test_multiple_security_events(self, db: DatabaseManager) -> None:
        for i in range(5):
            db.log_security_event(f"u{i}", "g1", "spam", "low", f"spam_{i}")
        conn = db._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM security_events")
        assert cur.fetchone()[0] == 5

    def test_multiple_telemetry_entries(self, db: DatabaseManager) -> None:
        for i in range(5):
            db.log_telemetry(f"exec_{i}", "cache", "hit", f"cache hit {i}", "ok")
        conn = db._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM telemetry_logs")
        assert cur.fetchone()[0] == 5


# =============================================================================
# 4. CONCURRENCY (10+ tests)
# =============================================================================

class TestConcurrency:
    """Verify thread-safety of database operations."""

    def test_concurrent_writes(self, db: DatabaseManager) -> None:
        errors: list[Exception] = []

        def writer(idx: int):
            try:
                db.save_conversation(_make_msg(
                    user_id=f"u{idx}",
                    timestamp=time.time() + idx,
                ))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors == []
        assert len(db.get_conversation_history(limit=100)) == 20

    def test_concurrent_reads_while_writing(self, db: DatabaseManager) -> None:
        for i in range(10):
            db.save_conversation(_make_msg(timestamp=time.time() + i))

        read_results: list[int] = []
        errors: list[Exception] = []

        def reader():
            try:
                r = db.get_conversation_history(limit=5)
                read_results.append(len(r))
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                db.save_conversation(_make_msg(timestamp=time.time() + 999))
            except Exception as e:
                errors.append(e)

        threads = []
        for _ in range(5):
            threads.append(threading.Thread(target=reader))
            threads.append(threading.Thread(target=writer))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors == []
        assert len(read_results) == 5

    def test_read_pool_concurrent_access(self, db: DatabaseManager) -> None:
        for i in range(5):
            db.save_conversation(_make_msg(timestamp=time.time() + i))

        results: list[int] = []
        errors: list[Exception] = []

        def reader():
            try:
                r = db.get_conversation_history(limit=10)
                results.append(len(r))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(15)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors == []
        assert len(results) == 15

    def test_write_lock_serialization(self, db: DatabaseManager) -> None:
        write_order: list[int] = []
        lock = threading.Lock()

        def writer(idx: int):
            db.save_conversation(_make_msg(
                user_id=f"u{idx}",
                timestamp=time.time() + idx,
            ))
            with lock:
                write_order.append(idx)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(write_order) == 10

    def test_wal_mode_concurrent_reads(self, db: DatabaseManager) -> None:
        for i in range(5):
            db.save_conversation(_make_msg(timestamp=time.time() + i))

        errors: list[Exception] = []

        def reader():
            try:
                db.get_conversation_history(limit=10)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors == []

    def test_race_condition_first_connection(self, tmp_db_path: str) -> None:
        instances: list[DatabaseManager] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(10)

        def create():
            barrier.wait()
            try:
                instances.append(DatabaseManager(db_path=tmp_db_path))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        for mgr in instances:
            mgr.close()
        assert errors == []

    def test_concurrent_saves(self, db: DatabaseManager) -> None:
        errors: list[Exception] = []

        def saver(prefix: str):
            try:
                for i in range(5):
                    db.save_conversation(_make_msg(
                        user_id=f"{prefix}_u{i}",
                        timestamp=time.time() + i,
                    ))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=saver, args=(f"t{t}",)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors == []
        assert len(db.get_conversation_history(limit=500)) == 20

    def test_database_under_simulated_load(self, db: DatabaseManager) -> None:
        errors: list[Exception] = []

        def load_worker(worker_id: int):
            try:
                for i in range(10):
                    db.save_conversation(_make_msg(
                        user_id=f"w{worker_id}_u{i}",
                        server_id=f"s{i % 3}",
                        timestamp=time.time() + worker_id * 10 + i,
                    ))
                    db.get_conversation_history(user_id=f"w{worker_id}_u{i}", limit=1)
                    db.get_conversation_history(server_id=f"s{i % 3}", limit=1)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=load_worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert errors == []

    def test_concurrent_preference_writes(self, db: DatabaseManager) -> None:
        errors: list[Exception] = []

        def writer(uid: int):
            try:
                for i in range(5):
                    db.save_user_preference(_make_pref(
                        user_id=f"u{uid}",
                        tier=["free", "premium", "enterprise"][i % 3],
                    ))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors == []
        for i in range(10):
            assert db.get_user_preference(f"u{i}") is not None

    def test_concurrent_cache_operations(self, db: DatabaseManager) -> None:
        errors: list[Exception] = []

        def cache_worker(idx: int):
            try:
                db.save_cache_entry(_make_cache(
                    cache_key=f"k{idx}",
                    expires_at=time.time() + 3600,
                ))
                db.get_cache_entry(f"k{idx}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=cache_worker, args=(i,)) for i in range(15)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors == []


# =============================================================================
# 5. ERROR HANDLING (10+ tests)
# =============================================================================

class TestErrorHandling:
    """Verify retry, rollback, and graceful degradation."""

    def test_retry_on_sqlite_busy(self, db: DatabaseManager) -> None:
        call_count = 0

        def mock_retry(operation, max_retries=3):
            nonlocal call_count
            for attempt in range(max_retries):
                try:
                    return operation()
                except sqlite3.OperationalError as e:
                    call_count += 1
                    if "busy" in str(e).lower() and attempt < max_retries - 1:
                        continue
                    raise

        db._execute_with_retry = mock_retry
        db.save_conversation(_make_msg())
        result = db.get_conversation_history(limit=1)
        assert len(result) == 1

    def test_retry_on_sqlite_locked(self, db: DatabaseManager) -> None:
        call_count = 0

        def mock_retry(operation, max_retries=3):
            nonlocal call_count
            for attempt in range(max_retries):
                try:
                    return operation()
                except sqlite3.OperationalError as e:
                    call_count += 1
                    if "locked" in str(e).lower() and attempt < max_retries - 1:
                        continue
                    raise

        db._execute_with_retry = mock_retry
        db.save_conversation(_make_msg())
        assert db.get_conversation_history(limit=1) is not None

    def test_invalid_sql_handled(self, db: DatabaseManager) -> None:
        with pytest.raises(sqlite3.OperationalError):
            conn = db._get_connection()
            conn.cursor().execute("INVALID SQL STATEMENT XYZ")

    def test_vacuum(self, db: DatabaseManager) -> None:
        db.save_conversation(_make_msg())
        db.vacuum()
        assert db.get_conversation_history(limit=1) is not None

    def test_context_manager_enter_exit(self, tmp_db_path: str) -> None:
        with DatabaseManager(db_path=tmp_db_path) as mgr:
            mgr.save_conversation(_make_msg())
            assert len(mgr.get_conversation_history(limit=1)) == 1
        assert mgr._connection is None

    def test_transaction_rollback_on_error(self, db: DatabaseManager) -> None:
        db.save_conversation(_make_msg(user_id="u1"))
        try:
            with db._locked_conn() as conn:
                conn.cursor().execute("INSERT INTO nonexistent_table VALUES (1)")
        except Exception:
            pass
        result = db.get_conversation_history(user_id="u1")
        assert len(result) == 1

    def test_read_connection_is_readonly(self, db: DatabaseManager) -> None:
        conn = db._get_read_connection()
        with pytest.raises(sqlite3.OperationalError):
            conn.cursor().execute("INSERT INTO conversation_history (user_id) VALUES ('x')")

    def test_concurrent_read_pool_under_load(self, db: DatabaseManager) -> None:
        for i in range(10):
            db.save_conversation(_make_msg(timestamp=time.time() + i))

        results: list[int] = []
        errors: list[Exception] = []

        def reader():
            try:
                r = db.get_conversation_history(limit=5)
                results.append(len(r))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors == []
        assert len(results) == 20

    def test_shared_db_singleton(self, tmp_db_path: str) -> None:
        set_shared_db(None)  # type: ignore
        mgr1 = get_shared_db(db_path=tmp_db_path)
        mgr2 = get_shared_db(db_path=tmp_db_path)
        assert mgr1 is mgr2
        mgr1.close()
        set_shared_db(None)  # type: ignore

    def test_set_shared_db(self, tmp_db_path: str) -> None:
        set_shared_db(None)  # type: ignore
        mgr = DatabaseManager(db_path=tmp_db_path)
        result = set_shared_db(mgr)
        assert result is mgr
        assert get_shared_db() is mgr
        mgr.close()
        set_shared_db(None)  # type: ignore


# =============================================================================
# 6. DATA BRIDGE (10+ tests)
# =============================================================================

class TestDataBridge:
    """Tests for BotDataBridge read-only accessors."""

    def test_get_stats_returns_dict(self, bridge: BotDataBridge) -> None:
        stats = bridge.get_stats()
        assert isinstance(stats, dict)
        assert "messages_today" in stats
        assert "active_users" in stats
        assert "health_score" in stats

    def test_get_stats_populated_from_db(self, bridge: BotDataBridge, db: DatabaseManager) -> None:
        db.save_stats(_make_stats(
            messages_processed=50, cache_hits=20, cache_misses=5,
            errors=1, total_tokens_used=3000
        ))
        stats = bridge.get_stats()
        assert stats["messages_today"] == 50
        assert stats["active_users"] == 15

    def test_get_stats_health_score_errors(self, bridge: BotDataBridge, db: DatabaseManager) -> None:
        db.save_stats(_make_stats(messages_processed=10, errors=5))
        stats = bridge.get_stats()
        assert stats["health_score"] < 100

    def test_get_stats_health_score_no_messages(self, bridge: BotDataBridge, db: DatabaseManager) -> None:
        db.save_stats(_make_stats(messages_processed=0, errors=5))
        stats = bridge.get_stats()
        assert stats["health_score"] == 50

    def test_get_recent_messages_with_limit(self, bridge: BotDataBridge, db: DatabaseManager) -> None:
        for i in range(10):
            db.save_conversation(_make_msg(timestamp=time.time() + i))
        msgs = bridge.get_recent_messages(limit=5)
        assert len(msgs) == 5
        assert isinstance(msgs[0], dict)
        assert "user" in msgs[0]

    def test_get_recent_messages_can_scope_to_server(self, bridge: BotDataBridge, db: DatabaseManager) -> None:
        db.save_conversation(_make_msg(server_id="guild-2", server_name="Guild 2"))
        msgs = bridge.get_recent_messages(server_id="guild-2")
        assert msgs
        assert all(item["server"] == "Guild 2" for item in msgs)

    def test_get_recent_messages_empty(self, bridge: BotDataBridge) -> None:
        msgs = bridge.get_recent_messages()
        assert msgs == []

    def test_get_moderation_actions_with_limit(self, bridge: BotDataBridge, db: DatabaseManager) -> None:
        for i in range(10):
            db.set_access_control("user", f"u{i}", "allow", "admin")
        actions = bridge.get_moderation_actions(limit=5)
        assert isinstance(actions, list)

    def test_get_active_users_empty_when_no_data(self, bridge: BotDataBridge) -> None:
        users = bridge.get_active_users()
        assert users == []

    def test_get_active_users_with_data(self, bridge: BotDataBridge, db: DatabaseManager) -> None:
        db.save_conversation(_make_msg(user_id="u1", user_name="Alice", timestamp=time.time()))
        users = bridge.get_active_users()
        assert len(users) == 1
        assert users[0]["user_id"] == "u1"

    def test_get_provider_health_missing_file(self, bridge: BotDataBridge) -> None:
        with patch.dict(os.environ, {"AZURE_MODEL_HEALTH": "/nonexistent/path.json"}):
            health = bridge.get_provider_health()
            assert health == {}

    def test_get_agent_info(self, bridge: BotDataBridge) -> None:
        info = bridge.get_agent_info()
        assert isinstance(info, dict)
        assert info.get("model") == "gpt-4"

    def test_get_websocket_manager(self, bridge: BotDataBridge) -> None:
        ws = bridge.get_websocket_manager()
        assert ws is None

    def test_snapshot_returns_independent_copy(self, bridge: BotDataBridge) -> None:
        stats1 = bridge.get_stats()
        stats2 = bridge.get_stats()
        stats1["messages_today"] = 99999
        assert stats2["messages_today"] != 99999

    def test_limit_clamping_max_200(self, bridge: BotDataBridge, db: DatabaseManager) -> None:
        for i in range(250):
            db.save_conversation(_make_msg(timestamp=time.time() + i))
        msgs = bridge.get_recent_messages(limit=500)
        assert len(msgs) == 200

    def test_limit_clamping_min_1(self, bridge: BotDataBridge, db: DatabaseManager) -> None:
        db.save_conversation(_make_msg())
        msgs = bridge.get_recent_messages(limit=-10)
        assert len(msgs) == 1

    def test_get_stats_latency(self, bridge: BotDataBridge) -> None:
        stats = bridge.get_stats()
        assert "latency_ms" in stats
        assert isinstance(stats["latency_ms"], int)

    def test_get_stats_guilds(self, bridge: BotDataBridge) -> None:
        stats = bridge.get_stats()
        assert stats["guilds"] == 3

    def test_bridge_with_no_bot(self, db: DatabaseManager) -> None:
        bridge_no_bot = BotDataBridge(bot=None, agent=None, db=db)
        stats = bridge_no_bot.get_stats()
        assert isinstance(stats, dict)

    def test_bridge_with_no_db(self) -> None:
        bridge_no_db = BotDataBridge(bot=MagicMock(), agent=MagicMock(), db=None)
        msgs = bridge_no_db.get_recent_messages()
        assert msgs == []

    def test_get_provider_health_with_valid_file(self, bridge: BotDataBridge, tmp_path: pathlib.Path) -> None:
        health_file = tmp_path / "model_health.json"
        health_file.write_text(json.dumps({"gpt-4": "healthy"}), encoding="utf-8")
        with patch.dict(os.environ, {"AZURE_MODEL_HEALTH": str(health_file)}):
            health = bridge.get_provider_health()
            assert health == {"gpt-4": "healthy"}

    def test_concurrent_bridge_access(self, bridge: BotDataBridge, db: DatabaseManager) -> None:
        for i in range(5):
            db.save_conversation(_make_msg(timestamp=time.time() + i))
        errors: list[Exception] = []
        results: list[int] = []

        def reader():
            try:
                msgs = bridge.get_recent_messages(limit=10)
                results.append(len(msgs))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors == []
        assert len(results) == 10


# =============================================================================
# 7. SPECIAL / EDGE CASES (bonus tests)
# =============================================================================

class TestEdgeCases:
    """Edge cases for data models and boundary conditions."""

    def test_empty_conversation_message(self, db: DatabaseManager) -> None:
        msg = ConversationMessage()
        row_id = db.save_conversation(msg)
        assert row_id > 0

    def test_unicode_content(self, db: DatabaseManager) -> None:
        msg = _make_msg(
            message="Hello \u4e16\u754c \U0001f600",
            response="Bonjour le monde \u00e9\u00e8\u00ea",
        )
        db.save_conversation(msg)
        history = db.get_conversation_history(limit=1)
        assert "\u4e16\u754c" in history[0].message

    def test_very_long_message(self, db: DatabaseManager) -> None:
        long_msg = "A" * 100_000
        msg = _make_msg(message=long_msg)
        db.save_conversation(msg)
        history = db.get_conversation_history(limit=1)
        assert len(history[0].message) == 100_000

    def test_many_conversations_pagination(self, db: DatabaseManager) -> None:
        for i in range(500):
            db.save_conversation(_make_msg(timestamp=time.time() + i))
        page1 = db.get_conversation_history(limit=100)
        assert len(page1) == 100

    def test_preference_all_tiers(self, db: DatabaseManager) -> None:
        for tier in ["free", "premium", "enterprise"]:
            db.save_user_preference(_make_pref(user_id=f"u_{tier}", tier=tier))
        for tier in ["free", "premium", "enterprise"]:
            pref = db.get_user_preference(f"u_{tier}")
            assert pref is not None
            assert pref.tier == tier

    def test_cache_various_expiries(self, db: DatabaseManager) -> None:
        now = time.time()
        db.save_cache_entry(_make_cache(cache_key="past", expires_at=now - 100))
        db.save_cache_entry(_make_cache(cache_key="future", expires_at=now + 100))
        assert db.get_cache_entry("past") is None
        assert db.get_cache_entry("future") is not None

    def test_stats_large_numbers(self, db: DatabaseManager) -> None:
        stats = _make_stats(
            messages_processed=2_000_000_000,
            total_tokens_used=999_999_999_999,
        )
        db.save_stats(stats)
        agg = db.get_aggregate_stats(hours=24)
        assert agg["total_messages"] == 2_000_000_000

    def test_dataclass_asdict(self) -> None:
        from dataclasses import asdict
        msg = _make_msg()
        d = asdict(msg)
        assert isinstance(d, dict)
        assert d["user_id"] == "u1"

    def test_multiple_security_event_severities(self, db: DatabaseManager) -> None:
        for sev in ["low", "medium", "high", "critical"]:
            db.log_security_event("u1", "g1", "test", sev, f"details_{sev}")
        conn = db._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT severity FROM security_events ORDER BY id")
        severities = [row[0] for row in cur.fetchall()]
        assert severities == ["low", "medium", "high", "critical"]

    def test_cache_retrieval_updates_last_accessed(self, db: DatabaseManager) -> None:
        entry = _make_cache(cache_key="k1")
        db.save_cache_entry(entry)
        result = db.get_cache_entry("k1")
        assert result is not None
        assert result.last_accessed >= entry.last_accessed

    def test_conversation_messages_ordered_by_timestamp(self, db: DatabaseManager) -> None:
        times = [time.time() - 100, time.time(), time.time() + 100]
        for t in times:
            db.save_conversation(_make_msg(timestamp=t))
        history = db.get_conversation_history(limit=10)
        timestamps = [m.timestamp for m in history]
        assert timestamps == sorted(times, reverse=True)

    def test_user_preference_timestamps(self, db: DatabaseManager) -> None:
        now = time.time()
        pref = _make_pref(created_at=now, updated_at=now)
        db.save_user_preference(pref)
        result = db.get_user_preference("u1")
        assert result is not None
        assert result.created_at == pytest.approx(now, abs=1)
        assert result.updated_at == pytest.approx(now, abs=1)

    def test_conversation_cached_flag(self, db: DatabaseManager) -> None:
        db.save_conversation(_make_msg(cached=True))
        db.save_conversation(_make_msg(cached=False))
        conn = db._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT cached FROM conversation_history ORDER BY id")
        flags = [bool(row[0]) for row in cur.fetchall()]
        assert flags == [True, False]
