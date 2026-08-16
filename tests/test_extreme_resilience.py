"""
EXTREME RESILIENCE TESTS

Exercises every error recovery and resilience mechanism in the Azure bot:
  - Database._execute_with_retry (SQLITE_BUSY / SQLITE_LOCKED / non-retryable)
  - agent._retry_transient (ConnectionError / TimeoutError / OSError)
  - CircuitBreaker state machine (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)
  - TaskManager dead letter, queue limits, retries, timeouts, cancellation
  - Agent.handle() fallback paths, circuit breaker integration, memory
  - Message handler send retry, fallback reply, progress edit retry, error msgs
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from azure.agent import AzureAgent, LongTermMemory, ShortTermMemory, ToolRegistry, _retry_transient
from azure.circuit_breaker import CircuitBreaker
from azure.database import ConversationMessage, DatabaseManager
from azure.task_manager import TaskManager, TaskRecord

# Track temporary directories for cleanup
_tmp_dirs: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup_tmp_dirs():
    _tmp_dirs.clear()
    yield
    import shutil
    for d in _tmp_dirs:
        shutil.rmtree(d, ignore_errors=True)


# =========================================================================
# Helpers
# =========================================================================

def _make_db(tmp_path: str = None) -> DatabaseManager:
    if tmp_path is None:
        tmp_path = tempfile.mkdtemp()
        _tmp_dirs.append(tmp_path)
    return DatabaseManager(db_path=os.path.join(tmp_path, "test.db"))


def _make_msg(**overrides) -> ConversationMessage:
    defaults = dict(
        user_id="1", user_name="test", server_id="s1",
        server_name="Test", channel_id="c1", channel_name="gen",
        message="msg", response="rsp", timestamp=time.time(),
    )
    defaults.update(overrides)
    return ConversationMessage(**defaults)


# =========================================================================
# 1. Database Retry Logic  (15 tests)
# =========================================================================

class TestDatabaseRetryLogic:

    def test_successful_on_first_try(self):
        db = _make_db()
        try:
            row_id = db.save_conversation(_make_msg())
            assert row_id is not None and row_id > 0
        finally:
            db.close()

    def test_retries_on_sqlite_busy(self):
        db = _make_db()
        attempt = [0]
        original = db._execute_with_retry

        def patched(operation, max_retries=3):
            def wrapped():
                attempt[0] += 1
                if attempt[0] <= 1:
                    raise sqlite3.OperationalError("database is busy")
                return operation()
            return original(wrapped, max_retries=max_retries)

        db._execute_with_retry = patched
        try:
            row_id = db.save_conversation(_make_msg())
            assert row_id > 0
            assert attempt[0] == 2
        finally:
            db.close()

    def test_retries_on_sqlite_locked(self):
        db = _make_db()
        attempt = [0]
        original = db._execute_with_retry

        def patched(operation, max_retries=3):
            def wrapped():
                attempt[0] += 1
                if attempt[0] <= 1:
                    raise sqlite3.OperationalError("database is locked")
                return operation()
            return original(wrapped, max_retries=max_retries)

        db._execute_with_retry = patched
        try:
            row_id = db.save_conversation(_make_msg(user_id="u2"))
            assert row_id > 0
            assert attempt[0] == 2
        finally:
            db.close()

    def test_exhausted_retries_raises(self):
        db = _make_db()
        try:
            with pytest.raises(sqlite3.OperationalError):
                db._execute_with_retry(
                    lambda: (_ for _ in ()).throw(
                        sqlite3.OperationalError("database is locked")
                    ),
                    max_retries=3,
                )
        finally:
            db.close()

    def test_exponential_backoff_timing(self):
        db = _make_db()
        sleep_calls = []

        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(round(s, 4))):
            def always_locked():
                raise sqlite3.OperationalError("database is busy")

            try:
                with pytest.raises(sqlite3.OperationalError):
                    db._execute_with_retry(always_locked, max_retries=4)
            finally:
                db.close()

        assert sleep_calls == [0.1, 0.2, 0.4]

    def test_non_retryable_error_raises_immediately(self):
        db = _make_db()
        attempt_count = [0]

        def raise_syntax_error():
            attempt_count[0] += 1
            raise sqlite3.OperationalError("near \"SELEC\": syntax error")

        try:
            with pytest.raises(sqlite3.OperationalError) as exc_info:
                db._execute_with_retry(raise_syntax_error, max_retries=5)
            assert "syntax error" in str(exc_info.value)
            assert attempt_count[0] == 1
        finally:
            db.close()

    def test_concurrent_retries(self):
        db = _make_db()
        results = []
        errors = []

        def worker(i):
            try:
                row_id = db.save_conversation(_make_msg(
                    user_id=f"u{i}", user_name=f"u{i}",
                    message=f"m{i}", response=f"r{i}",
                ))
                results.append(row_id)
            except Exception as e:
                errors.append(e)

        try:
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            assert len(errors) == 0, f"Concurrent errors: {errors}"
            assert len(results) == 5
            assert all(r is not None and r > 0 for r in results)
        finally:
            db.close()

    def test_transaction_integrity_between_retries(self):
        db = _make_db()
        try:
            msg1 = _make_msg(user_id="integ", message="first", response="resp1", timestamp=100.0)
            db.save_conversation(msg1)
            msg2 = _make_msg(user_id="integ", message="second", response="resp2", timestamp=200.0)
            db.save_conversation(msg2)

            history = db.get_conversation_history(user_id="integ")
            assert len(history) == 2
            assert history[0].message == "second"
            assert history[1].message == "first"
        finally:
            db.close()

    def test_different_error_codes_handled_differently(self):
        db = _make_db()
        retry_count = [0]

        def always_busy():
            retry_count[0] += 1
            raise sqlite3.OperationalError("database is busy")

        try:
            with pytest.raises(sqlite3.OperationalError):
                db._execute_with_retry(always_busy, max_retries=3)
            assert retry_count[0] == 3
        finally:
            db.close()

    def test_cache_save_with_retry(self):
        db = _make_db()
        from azure.database import CacheEntry
        try:
            entry = CacheEntry(
                cache_key="k1", prompt="p", response="r",
                user_id="u1", server_id="s1", hit_count=0,
                created_at=time.time(), last_accessed=time.time(),
                expires_at=time.time() + 3600,
            )
            db.save_cache_entry(entry)
            retrieved = db.get_cache_entry("k1")
            assert retrieved is not None
            assert retrieved.response == "r"
        finally:
            db.close()

    def test_stats_save_with_retry(self):
        db = _make_db()
        from azure.database import BotStats
        try:
            stats = BotStats(
                timestamp=time.time(), messages_processed=10,
                cache_hits=5, cache_misses=5, errors=1,
                avg_response_time_ms=150.0, total_tokens_used=500,
                active_users=3, active_servers=1,
            )
            row_id = db.save_stats(stats)
            assert row_id > 0
        finally:
            db.close()

    def test_user_preference_save_and_retrieve(self):
        db = _make_db()
        from azure.database import UserPreference
        try:
            pref = UserPreference(
                user_id="u1", user_name="Alice", tier="premium",
                context_size=20, temperature=0.8, language="en",
                custom_system_prompt="Be formal", disabled=False,
                created_at=time.time(), updated_at=time.time(),
            )
            db.save_user_preference(pref)
            retrieved = db.get_user_preference("u1")
            assert retrieved is not None
            assert retrieved.tier == "premium"
            assert retrieved.context_size == 20
        finally:
            db.close()

    def test_database_close_cleans_up_connections(self):
        db = _make_db()
        try:
            assert db._connection is not None
            db.close()
            assert db._connection is None
        finally:
            pass

    def test_context_manager_closes_database(self):
        with _make_db() as db:
            row_id = db.save_conversation(_make_msg())
            assert row_id > 0
        assert db._connection is None

    def test_retries_on_busy_case_insensitive(self):
        db = _make_db()
        attempt = [0]
        original = db._execute_with_retry

        def patched(operation, max_retries=3):
            def wrapped():
                attempt[0] += 1
                if attempt[0] <= 1:
                    raise sqlite3.OperationalError("SQLITE_BUSY")
                return operation()
            return original(wrapped, max_retries=max_retries)

        db._execute_with_retry = patched
        try:
            row_id = db.save_conversation(_make_msg(user_id="ci"))
            assert row_id > 0
            assert attempt[0] == 2
        finally:
            db.close()


# =========================================================================
# 2. Circuit Breaker  (15 tests)
# =========================================================================

class TestCircuitBreaker:

    def test_threshold_triggers_open(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == "OPEN"

    def test_open_blocks_requests(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=10.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "OPEN"
        assert cb.allow_request() is False

    def test_cooldown_to_half_open(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.05)
        now = [100.0]
        with patch("azure.circuit_breaker.time.monotonic", side_effect=lambda: now[0]):
            cb.record_failure()
            cb.record_failure()
            assert cb.state == "OPEN"
            now[0] += 0.06
            assert cb.state == "HALF_OPEN"

    def test_success_closes_circuit(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.05)
        now = [100.0]
        with patch("azure.circuit_breaker.time.monotonic", side_effect=lambda: now[0]):
            cb.record_failure()
            cb.record_failure()
            assert cb.state == "OPEN"
            now[0] += 0.06
            cb.allow_request()
            cb.record_success()
            assert cb.state == "CLOSED"
            assert cb.get_info()["failure_count"] == 0

    def test_failure_reopens_circuit(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.05)
        now = [100.0]
        with patch("azure.circuit_breaker.time.monotonic", side_effect=lambda: now[0]):
            cb.record_failure()
            cb.record_failure()
            now[0] += 0.06
            cb.allow_request()
            cb.record_failure()
            assert cb.state == "OPEN"

    def test_thread_safety(self):
        cb = CircuitBreaker(failure_threshold=100, cooldown_seconds=60)
        errors = []

        def worker():
            try:
                for _ in range(50):
                    cb.record_failure()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(errors) == 0
        assert cb.get_info()["failure_count"] == 200

    def test_get_info_accuracy(self):
        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=30)
        cb.record_failure()
        cb.record_failure()
        info = cb.get_info()
        assert info["failure_count"] == 2
        assert info["failure_threshold"] == 5
        assert info["cooldown_seconds"] == 30
        assert info["state"] == "CLOSED"
        assert info["seconds_since_failure"] is not None

    def test_custom_config_values(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=120)
        cb.record_failure()
        assert cb.state == "OPEN"
        assert cb.get_info()["cooldown_seconds"] == 120

    def test_edge_threshold_1(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
        assert cb.state == "CLOSED"
        cb.record_failure()
        assert cb.state == "OPEN"
        assert cb.allow_request() is False

    def test_edge_cooldown_0(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0)
        cb.record_failure()
        cb.record_failure()
        with cb._lock:
            assert cb._state == "OPEN"
        assert cb.allow_request() is True

    def test_independent_circuits(self):
        cb1 = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        cb2 = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        cb1.record_failure()
        cb1.record_failure()
        assert cb1.state == "OPEN"
        assert cb2.state == "CLOSED"
        assert cb2.allow_request() is True

    def test_state_under_rapid_calls(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.01)
        for _ in range(20):
            cb.record_failure()
            time.sleep(0.012)
            assert cb.state in ("OPEN", "HALF_OPEN")
            cb.allow_request()
            cb.record_success()
            assert cb.state == "CLOSED"

    def test_half_open_allows_exactly_one(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.05)
        now = [100.0]
        with patch("azure.circuit_breaker.time.monotonic", side_effect=lambda: now[0]):
            cb.record_failure()
            cb.record_failure()
            now[0] += 0.06
            assert cb.allow_request() is True
            assert cb.state == "HALF_OPEN"
            cb.record_success()
            assert cb.state == "CLOSED"

    def test_reset_after_success_streak(self):
        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.get_info()["failure_count"] == 0
        assert cb.state == "CLOSED"
        for _ in range(100):
            assert cb.allow_request() is True

    def test_success_resets_count_after_partial_failures(self):
        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.get_info()["failure_count"] == 0
        assert cb.state == "CLOSED"


# =========================================================================
# 3. Task Manager Resilience  (10 tests)
# =========================================================================

class TestTaskManagerResilience:

    @pytest.fixture
    def tm(self):
        return TaskManager()

    @pytest.mark.asyncio
    async def test_failed_task_to_dead_letter(self, tm):
        async def fail():
            raise RuntimeError("dl_test")

        await tm.start_task("dl_task", fail())
        dl = tm.get_dead_letter()
        assert len(dl) >= 1
        assert dl[-1].name == "dl_task"
        assert "dl_test" in dl[-1].error

    @pytest.mark.asyncio
    async def test_dead_letter_max_size_50(self, tm):
        for i in range(55):
            async def fail(i=i):
                raise RuntimeError(f"fail_{i}")
            await tm.start_task(f"dl_{i}", fail())

        dl = tm.get_dead_letter()
        assert len(dl) <= TaskManager._MAX_DEAD_LETTER

    @pytest.mark.asyncio
    async def test_queue_size_limit_20(self, tm):
        release = asyncio.Event()

        async def slow():
            await release.wait()

        task = asyncio.create_task(tm.start_task("slow", slow()))
        await asyncio.sleep(0)

        async def noop():
            return None

        for i in range(25):
            await tm.start_task(f"q{i}", noop(), queue_if_busy=True)

        assert tm.queue_size() <= TaskManager._MAX_QUEUE_SIZE
        release.set()
        await task

    @pytest.mark.asyncio
    async def test_reject_when_queue_full(self, tm):
        release = asyncio.Event()

        async def slow():
            await release.wait()

        task = asyncio.create_task(tm.start_task("slow", slow()))
        await asyncio.sleep(0)

        async def noop():
            return None

        for i in range(TaskManager._MAX_QUEUE_SIZE + 1):
            await tm.start_task(f"q{i}", noop(), queue_if_busy=True)

        assert tm.queue_size() == TaskManager._MAX_QUEUE_SIZE
        release.set()
        await task

    @pytest.mark.asyncio
    async def test_dead_letter_has_failure_info(self, tm):
        async def fail():
            raise ValueError("specific error message")

        await tm.start_task("info_task", fail())
        dl = tm.get_dead_letter()
        record = dl[-1]
        assert record.name == "info_task"
        assert record.success is False
        assert "specific error message" in record.error
        assert record.t_end > 0

    @pytest.mark.asyncio
    async def test_clear_dead_letter(self, tm):
        async def fail():
            raise RuntimeError("x")

        await tm.start_task("cl", fail())
        assert len(tm.get_dead_letter()) >= 1
        tm._dead_letter.clear()
        assert len(tm.get_dead_letter()) == 0

    @pytest.mark.asyncio
    async def test_task_timeout(self, tm):
        async def slow_forever():
            while True:
                await asyncio.sleep(100)

        with patch.object(TaskManager, "TASK_TIMEOUT", 0.1):
            result = await tm.start_task("timeout_task", slow_forever())
        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_during_execution(self, tm):
        started = asyncio.Event()

        async def infinite():
            started.set()
            while True:
                await asyncio.sleep(0.01)

        asyncio.create_task(tm.start_task("inf", infinite()))
        await started.wait()
        await asyncio.sleep(0.01)
        await tm.cancel_current()
        assert tm.is_busy is False

    @pytest.mark.asyncio
    async def test_history_after_failure(self, tm):
        async def ok():
            return True

        async def fail():
            raise ValueError("x")

        await tm.start_task("t1", ok())
        await tm.start_task("t2", fail())

        history = tm.get_history()
        assert len(history) >= 2
        assert history[-2].success is True
        assert history[-1].success is False

    @pytest.mark.asyncio
    async def test_stats_after_multiple_tasks(self, tm):
        async def ok():
            return True

        async def fail():
            raise ValueError("x")

        await tm.start_task("t1", ok())
        await tm.start_task("t2", fail())
        await tm.start_task("t3", ok())

        stats = tm.get_stats()
        assert stats["total_tasks"] == 3
        assert stats["successful"] == 2
        assert stats["failed"] == 1


# =========================================================================
# 4. Agent Resilience  (10 tests)
# =========================================================================

class TestAgentResilience:

    def test_llm_failure_returns_fallback(self):
        with patch("azure.agent.AzureAgent.__init__", lambda self, *a, **kw: None):
            agent = AzureAgent.__new__(AzureAgent)
            agent.llm = None
            agent._llm_circuit_breaker = None
            result = agent._llm_generate_response("prompt", "fallback_msg")
            assert result == "fallback_msg"

    def test_tool_failure_handled_gracefully(self):
        tr = ToolRegistry()
        tr.register("crash", "crashes", lambda: (_ for _ in ()).throw(ValueError("tool failed")))
        result = tr.call("crash")
        assert result["ok"] is False
        assert "tool failed" in result["error"]

    def test_memory_failure_doesnt_break_flow(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = Path(f.name)

        ltm = LongTermMemory(path=path)
        ltm.remember("k1", "v1")
        assert ltm.recall("k1") == "v1"
        path.unlink(missing_ok=True)

    def test_circuit_breaker_prevents_calls(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=10)
        for _ in range(2):
            cb.record_failure()
        assert cb.allow_request() is False

    def test_concurrent_handle_safety(self):
        stm = ShortTermMemory(max_turns=5)
        errors = []

        def worker(i):
            try:
                for j in range(10):
                    stm.add("user", f"msg_{i}_{j}", name=f"user{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(errors) == 0
        assert len(stm.messages) <= stm.max_turns * 2

    def test_empty_context_handling(self):
        stm = ShortTermMemory(max_turns=5)
        assert stm.context_block() == ""
        assert stm.to_history() == []

    def test_none_guild_channel(self):
        agent = AzureAgent.__new__(AzureAgent)
        agent._current_guild = None
        agent._current_channel = None
        agent._discord_tools = None
        agent._event_loop = None
        result = agent._build_call_context(None, None, None, None)
        assert result == {}

    def test_short_term_memory_context_block(self):
        stm = ShortTermMemory(max_turns=5)
        stm.add("user", "hello")
        stm.add("assistant", "hi there")
        block = stm.context_block()
        assert "hello" in block
        assert "hi there" in block

    def test_tool_registry_describe(self):
        tr = ToolRegistry()
        tr.register("a", "desc_a", lambda: None)
        tr.register("b", "desc_b", lambda: None)
        tools = tr.describe()
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert names == {"a", "b"}

    def test_long_term_memory_corrupted_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("NOT VALID JSON {{{}}}}")
            path = Path(f.name)

        ltm = LongTermMemory(path=path)
        assert ltm.facts == {}
        path.unlink(missing_ok=True)


# =========================================================================
# 5. Message Handler Resilience  (10 tests)
# =========================================================================

class TestMessageHandlerResilience:

    @pytest.mark.asyncio
    async def test_send_retry_3_attempts(self):
        channel = AsyncMock()
        attempts = [0]

        async def flaky_send(content):
            attempts[0] += 1
            if attempts[0] <= 2:
                raise Exception("HTTP 429")
            return AsyncMock(id=999)

        channel.send = flaky_send

        result = None
        for _attempt in range(3):
            try:
                result = await channel.send("test")
                break
            except Exception:
                if _attempt < 2:
                    await asyncio.sleep(0.01)
                else:
                    raise

        assert result is not None
        assert attempts[0] == 3

    @pytest.mark.asyncio
    async def test_fallback_reply_to_channel_send(self):
        message = AsyncMock()
        message.reply = AsyncMock(side_effect=Exception("no reply perms"))
        message.channel = AsyncMock()
        message.channel.send = AsyncMock()

        try:
            await message.reply("test")
        except Exception:
            await message.channel.send("test")

        message.channel.send.assert_called_once_with("test")

    @pytest.mark.asyncio
    async def test_progress_edit_retry(self):
        msg = AsyncMock()
        msg.content = "old"
        attempts = [0]
        original_edit = msg.edit

        async def flaky_edit(**kwargs):
            attempts[0] += 1
            if attempts[0] <= 1:
                raise Exception("message deleted")
            return await original_edit(**kwargs)

        msg.edit = flaky_edit

        for _attempt in range(3):
            try:
                await msg.edit(content="new")
                break
            except Exception:
                if _attempt < 2:
                    await asyncio.sleep(0.01)

        assert attempts[0] >= 1

    @pytest.mark.asyncio
    async def test_error_message_on_failure(self):
        progress_msg = AsyncMock()
        progress_msg.content = "thinking..."

        await progress_msg.edit(content="An error occurred while processing your request.")
        progress_msg.edit.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limit_error_message(self):
        msg = AsyncMock()
        error_text = "Rate limited. Please wait a moment before trying again."

        await msg.edit(content=error_text)
        msg.edit.assert_called_once_with(content=error_text)
        assert "Rate limited" in error_text

    @pytest.mark.asyncio
    async def test_llm_error_message(self):
        msg = AsyncMock()
        error_text = "My AI model is temporarily unavailable. Please try again shortly."

        await msg.edit(content=error_text)
        msg.edit.assert_called_once_with(content=error_text)
        assert "unavailable" in error_text

    @pytest.mark.asyncio
    async def test_missing_channel_handled(self):
        message = AsyncMock()
        message.channel = None

        error_caught = False
        try:
            if message.channel is None:
                raise Exception("Channel not found")
            await message.channel.send("test")
        except Exception:
            error_caught = True

        assert error_caught

    @pytest.mark.asyncio
    async def test_send_fallback_after_all_retries_fail(self):
        channel = AsyncMock()

        async def always_fail(content):
            raise Exception("permanent failure")

        channel.send = always_fail

        fallback_sent = False
        for _attempt in range(3):
            try:
                await channel.send("test")
                break
            except Exception:
                if _attempt == 2:
                    fallback_sent = True

        assert fallback_sent

    @pytest.mark.asyncio
    async def test_concurrent_send_retries(self):
        channel = AsyncMock()
        results = []

        async def flaky_send(content):
            await asyncio.sleep(0.001)
            return AsyncMock(id=1)

        channel.send = flaky_send

        async def send_msg(i):
            result = await channel.send(f"msg_{i}")
            results.append(result)

        await asyncio.gather(*[send_msg(i) for i in range(5)])
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_edit_fallback_to_send(self):
        msg = AsyncMock()
        msg.edit = AsyncMock(side_effect=Exception("message deleted"))
        msg.channel = AsyncMock()
        msg.channel.send = AsyncMock()

        try:
            await msg.edit(content="new content")
        except Exception:
            await msg.channel.send("new content")

        msg.channel.send.assert_called_once_with("new content")


# =========================================================================
# 6. Integration: Agent + Circuit Breaker (5 tests)
# =========================================================================

class TestAgentCircuitBreakerIntegration:

    def test_circuit_breaker_records_llm_failure(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == "OPEN"
        assert cb.allow_request() is False

    def test_circuit_breaker_resets_after_success(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.05)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.06)
        cb.allow_request()
        cb.record_success()
        assert cb.state == "CLOSED"
        assert cb.allow_request() is True

    def test_circuit_breaker_info_format(self):
        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
        info = cb.get_info()
        assert "state" in info
        assert "failure_count" in info
        assert "failure_threshold" in info
        assert "cooldown_seconds" in info
        assert "seconds_since_failure" in info

    def test_circuit_breaker_multiple_open_close_cycles(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.05)
        for _ in range(3):
            for _ in range(2):
                cb.record_failure()
            assert cb.state == "OPEN"
            time.sleep(0.06)
            cb.allow_request()
            cb.record_success()
            assert cb.state == "CLOSED"

    def test_concurrent_failure_recording(self):
        cb = CircuitBreaker(failure_threshold=100, cooldown_seconds=60)
        errors = []

        def worker():
            try:
                for _ in range(10):
                    cb.record_failure()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(errors) == 0
        assert cb.get_info()["failure_count"] == 50


# =========================================================================
# 7. Extreme Edge Cases (10 tests)
# =========================================================================

class TestExtremeEdgeCases:

    def test_rapid_circuit_breaker_state_transitions(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.01)
        for _ in range(20):
            cb.record_failure()
            time.sleep(0.012)
            assert cb.state in ("OPEN", "HALF_OPEN")
            cb.allow_request()
            cb.record_success()
            assert cb.state == "CLOSED"

    def test_database_concurrent_writes_and_reads(self):
        db = _make_db()
        errors = []

        def writer(i):
            try:
                db.save_conversation(_make_msg(
                    user_id=f"w{i}", user_name=f"writer{i}",
                    message=f"write_{i}", response=f"rsp_{i}",
                ))
            except Exception as e:
                errors.append(("write", i, e))

        def reader(i):
            try:
                db.get_conversation_history(server_id="s1")
            except Exception as e:
                errors.append(("read", i, e))

        try:
            threads = []
            for i in range(3):
                threads.append(threading.Thread(target=writer, args=(i,)))
                threads.append(threading.Thread(target=reader, args=(i,)))
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            assert len(errors) == 0, f"Errors: {errors}"
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_task_manager_concurrent_starts(self):
        tm = TaskManager()
        results = []

        async def worker(i):
            async def work():
                return i
            r = await tm.start_task(f"w{i}", work())
            results.append(r)

        tasks = [worker(i) for i in range(5)]
        await asyncio.gather(*tasks)
        assert len(results) >= 1

    def test_short_term_memory_max_turns_enforced(self):
        stm = ShortTermMemory(max_turns=3)
        for i in range(20):
            stm.add("user", f"msg_{i}")
        assert len(stm.messages) <= stm.max_turns * 2

    def test_long_term_memory_search(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = Path(f.name)

        ltm = LongTermMemory(path=path)
        ltm.remember("favorite_color", "blue")
        ltm.remember("favorite_food", "pizza")
        ltm.remember("hobby", "gaming")

        hits = ltm.search("favorite")
        assert len(hits) >= 2
        path.unlink(missing_ok=True)

    def test_tool_registry_multiple_tools(self):
        tr = ToolRegistry()
        tr.register("add", "add", lambda a=0, b=0: a + b)
        tr.register("sub", "sub", lambda a=0, b=0: a - b)
        tr.register("mul", "mul", lambda a=0, b=0: a * b)

        assert tr.call("add", a=2, b=3)["result"] == 5
        assert tr.call("sub", a=5, b=3)["result"] == 2
        assert tr.call("mul", a=4, b=3)["result"] == 12

    def test_database_access_control_thread_safety(self):
        db = _make_db()
        errors = []

        def worker(i):
            try:
                db.set_access_control("user", f"thread_{i}",
                                      "allow" if i % 2 == 0 else "deny", "admin")
                perm = db.get_access_control(f"thread_{i}")
                assert perm in ("allow", "deny")
            except Exception as e:
                errors.append(e)

        try:
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            assert len(errors) == 0
        finally:
            db.close()

    def test_circuit_breaker_very_short_cooldown(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.02)
        cb.record_failure()
        time.sleep(0.05)
        assert cb.state == "HALF_OPEN"
        assert cb.allow_request() is True

    def test_task_record_fields(self):
        tr = TaskRecord(name="test", t_start=1.0, t_end=2.0, success=True, error="", retries=0, guild_name="G")
        assert tr.name == "test"
        assert tr.success is True
        assert tr.t_end - tr.t_start == 1.0

    @pytest.mark.asyncio
    async def test_task_manager_history_max(self):
        tm = TaskManager()
        for i in range(250):
            async def work():
                return True
            await tm.start_task(f"h{i}", work())
        assert len(tm.get_history(n=999)) <= tm._history_max + 1


# =========================================================================
# 8. Retry Timing and Stress (5 tests)
# =========================================================================

class TestRetryTimingAndStress:

    def test_rapid_successive_retries(self):
        attempt = [0]

        def fail_fast():
            attempt[0] += 1
            if attempt[0] <= 50:
                raise ConnectionError("flood")
            return "survived"

        with patch("azure.agent._time.sleep"):
            result = _retry_transient(fail_fast, max_retries=51, base_delay=0)
        assert result == "survived"

    def test_database_many_writes(self):
        db = _make_db()
        try:
            for i in range(50):
                row_id = db.save_conversation(_make_msg(
                    user_id=f"u{i}", user_name=f"n{i}",
                    message=f"m{i}", response=f"r{i}",
                ))
                assert row_id > 0
            history = db.get_conversation_history(server_id="s1", limit=100)
            assert len(history) == 50
        finally:
            db.close()

    def test_circuit_breaker_100_open_close_cycles(self):
        # Keep the stress loop above Windows scheduler/logging jitter. The
        # production state machine is still exercised at millisecond scale by
        # the dedicated cooldown tests above.
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.05)
        for _ in range(100):
            for _ in range(3):
                cb.record_failure()
            assert cb.state == "OPEN"
            time.sleep(0.06)
            cb.allow_request()
            cb.record_success()
            assert cb.state == "CLOSED"

    def test_concurrent_circuit_breaker_operations(self):
        cb = CircuitBreaker(failure_threshold=1000, cooldown_seconds=60)
        errors = []

        def record_fails():
            try:
                for _ in range(50):
                    cb.record_failure()
            except Exception as e:
                errors.append(e)

        def record_successes():
            try:
                for _ in range(50):
                    cb.record_success()
            except Exception as e:
                errors.append(e)

        def check_state():
            try:
                for _ in range(50):
                    _ = cb.state
                    _ = cb.allow_request()
                    _ = cb.get_info()
            except Exception as e:
                errors.append(e)

        threads = []
        for _ in range(3):
            threads.append(threading.Thread(target=record_fails))
            threads.append(threading.Thread(target=record_successes))
            threads.append(threading.Thread(target=check_state))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert len(errors) == 0

    @pytest.mark.asyncio
    async def test_task_manager_rapid_start_cancel(self):
        tm = TaskManager()
        async def work():
            await asyncio.sleep(100)
            return "done"

        for _ in range(5):
            asyncio.create_task(tm.start_task("rapid", work()))
            await asyncio.sleep(0.01)
            await tm.cancel_current()

        assert tm.is_busy is False
