"""Tests for DatabaseManager (azure/database.py)."""

import threading
import time

import pytest

import azure.database as db_mod
from azure.database import (
    BotStats,
    CacheEntry,
    ConversationMessage,
    DatabaseManager,
    UserPreference,
    get_shared_db,
    set_shared_db,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """Create a fresh in-memory-style temp DB for each test."""
    path = tmp_path / "test.db"
    mgr = DatabaseManager(db_path=str(path))
    yield mgr
    mgr.close()


@pytest.fixture(autouse=True)
def _reset_shared_db():
    """Reset the process-wide singleton so tests don't leak."""
    yield
    db_mod._shared_db = None


# ---- Connection / Init ----------------------------------------------------


def test_connection_creation(db):
    conn = db._get_connection()
    assert conn is not None


def test_tables_created(db):
    cursor = db._get_connection().cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cursor.fetchall()}
    assert "conversation_history" in tables
    assert "user_preferences" in tables
    assert "response_cache" in tables
    assert "bot_stats" in tables
    assert "audit_logs" in tables
    assert "access_control" in tables
    assert "security_events" in tables
    assert "telemetry_logs" in tables
    assert "web_users" in tables
    assert "api_keys" in tables


# ---- Conversation History -------------------------------------------------


def test_save_and_retrieve_conversation(db):
    msg = ConversationMessage(
        user_id="123",
        user_name="TestUser",
        server_id="s1",
        server_name="TestServer",
        channel_id="c1",
        channel_name="general",
        message="Hello!",
        response="Hi there!",
        timestamp=time.time(),
        cached=False,
        tokens_used=10,
        response_time_ms=200,
    )
    row_id = db.save_conversation(msg)
    assert row_id is not None

    history = db.get_conversation_history(user_id="123")
    assert len(history) == 1
    assert history[0].message == "Hello!"
    assert history[0].response == "Hi there!"
    assert history[0].user_name == "TestUser"


def test_conversation_filter_by_server(db):
    t = time.time()
    db.save_conversation(ConversationMessage(user_id="1", user_name="A", server_id="s1", server_name="S1",
        channel_id="c1", channel_name="ch", message="m1", response="r1", timestamp=t))
    db.save_conversation(ConversationMessage(user_id="2", user_name="B", server_id="s2", server_name="S2",
        channel_id="c2", channel_name="ch", message="m2", response="r2", timestamp=t))

    result = db.get_conversation_history(server_id="s1")
    assert len(result) == 1
    assert result[0].server_id == "s1"


def test_conversation_filter_since(db):
    old = time.time() - 100
    new = time.time()
    db.save_conversation(ConversationMessage(user_id="1", user_name="A", server_id="s1", server_name="S",
        channel_id="c1", channel_name="ch", message="old", response="r", timestamp=old))
    db.save_conversation(ConversationMessage(user_id="1", user_name="A", server_id="s1", server_name="S",
        channel_id="c1", channel_name="ch", message="new", response="r", timestamp=new))

    result = db.get_conversation_history(since=old + 1)
    assert len(result) == 1
    assert result[0].message == "new"


# ---- User Preferences -----------------------------------------------------


def test_save_and_get_user_preference(db):
    pref = UserPreference(
        user_id="u1",
        user_name="TestUser",
        tier="premium",
        context_size=20,
        temperature=0.5,
        language="en",
        created_at=time.time(),
        updated_at=time.time(),
    )
    db.save_user_preference(pref)
    got = db.get_user_preference("u1")
    assert got is not None
    assert got.tier == "premium"
    assert got.context_size == 20


def test_user_preference_not_found(db):
    assert db.get_user_preference("nonexistent") is None


def test_user_preference_upsert(db):
    t = time.time()
    db.save_user_preference(UserPreference(user_id="u1", user_name="A", tier="free", created_at=t, updated_at=t))
    db.save_user_preference(UserPreference(user_id="u1", user_name="A", tier="enterprise", created_at=t, updated_at=t))
    got = db.get_user_preference("u1")
    assert got.tier == "enterprise"


# ---- Response Cache -------------------------------------------------------


def test_cache_entry_save_and_get(db):
    entry = CacheEntry(
        cache_key="k1",
        prompt="hello",
        response="hi",
        user_id="u1",
        server_id="s1",
        hit_count=0,
        created_at=time.time(),
        last_accessed=time.time(),
        expires_at=time.time() + 3600,
    )
    db.save_cache_entry(entry)
    got = db.get_cache_entry("k1")
    assert got is not None
    assert got.response == "hi"
    assert got.hit_count == 1  # bumped by get


def test_cache_entry_expired(db):
    entry = CacheEntry(
        cache_key="k1",
        prompt="hello",
        response="hi",
        user_id="u1",
        server_id="s1",
        created_at=time.time() - 100,
        last_accessed=time.time() - 100,
        expires_at=time.time() - 1,  # expired
    )
    db.save_cache_entry(entry)
    assert db.get_cache_entry("k1") is None


def test_cleanup_expired_cache(db):
    now = time.time()
    db.save_cache_entry(CacheEntry(cache_key="expired", prompt="p", response="r", user_id="u", server_id="s",
        created_at=now - 100, last_accessed=now - 100, expires_at=now - 1))
    db.save_cache_entry(CacheEntry(cache_key="valid", prompt="p", response="r", user_id="u", server_id="s",
        created_at=now, last_accessed=now, expires_at=now + 3600))

    removed = db.cleanup_expired_cache()
    assert removed == 1
    assert db.get_cache_entry("valid") is not None
    assert db.get_cache_entry("expired") is None


# ---- Stats ----------------------------------------------------------------


def test_save_and_get_stats(db):
    stats = BotStats(
        timestamp=time.time(),
        messages_processed=10,
        cache_hits=5,
        cache_misses=5,
        errors=1,
        avg_response_time_ms=150.0,
        total_tokens_used=500,
        active_users=3,
        active_servers=1,
    )
    db.save_stats(stats)
    history = db.get_stats_history(hours=1)
    assert len(history) == 1
    assert history[0].messages_processed == 10


def test_aggregate_stats(db):
    t = time.time()
    db.save_stats(BotStats(timestamp=t, messages_processed=5, cache_hits=2, cache_misses=3, errors=0,
        avg_response_time_ms=100, total_tokens_used=200, active_users=1, active_servers=1))
    db.save_stats(BotStats(timestamp=t, messages_processed=3, cache_hits=1, cache_misses=2, errors=1,
        avg_response_time_ms=200, total_tokens_used=100, active_users=2, active_servers=1))

    agg = db.get_aggregate_stats(hours=1)
    assert agg["total_messages"] == 8
    assert agg["total_cache_hits"] == 3
    assert agg["total_errors"] == 1
    assert agg["total_tokens"] == 300


# ---- Access Control -------------------------------------------------------


def test_access_control_set_and_get(db):
    db.set_access_control("user", "uid123", "deny", "admin")
    assert db.get_access_control("uid123") == "deny"


def test_access_control_not_set(db):
    assert db.get_access_control("unknown") is None


def test_access_control_upsert(db):
    db.set_access_control("user", "u1", "deny", "admin")
    db.set_access_control("user", "u1", "allow", "admin")
    assert db.get_access_control("u1") == "allow"


# ---- Security Events ------------------------------------------------------


def test_log_security_event(db):
    db.log_security_event("u1", "g1", "jailbreak", "HIGH", "attempted prompt injection")
    # Should not raise


# ---- Telemetry Logs -------------------------------------------------------


def test_log_telemetry(db):
    db.log_telemetry("exec-1", "llm", "generate", "called model", "ok")
    # Should not raise


# ---- Thread Safety --------------------------------------------------------


def test_concurrent_writes(db):
    """Multiple threads writing to the same DB should not crash."""
    errors = []

    def writer(thread_id):
        try:
            for i in range(5):
                db.save_conversation(ConversationMessage(
                    user_id=f"t{thread_id}",
                    user_name=f"Thread{thread_id}",
                    server_id="s1",
                    server_name="S",
                    channel_id="c1",
                    channel_name="ch",
                    message=f"msg{i}",
                    response=f"resp{i}",
                    timestamp=time.time(),
                ))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Errors: {errors}"
    history = db.get_conversation_history()
    assert len(history) == 25


# ---- Shared DB singleton --------------------------------------------------


def test_shared_db_singleton(tmp_path):
    path = tmp_path / "shared.db"
    db_mod._shared_db = None
    db1 = get_shared_db(str(path))
    db2 = get_shared_db(str(path))
    assert db1 is db2
    db1.close()
    db_mod._shared_db = None


def test_set_shared_db(tmp_path):
    path = tmp_path / "custom.db"
    mgr = DatabaseManager(db_path=str(path))
    set_shared_db(mgr)
    assert get_shared_db() is mgr
    mgr.close()
    db_mod._shared_db = None
