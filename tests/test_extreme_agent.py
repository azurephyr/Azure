"""
Extremely thorough test suite for Azure Agent core systems.

Covers: CircuitBreaker, Typed Errors, SubsystemRegistry, LoggingConfig,
Telemetry (ExecutionTracker), TaskManager, Agent memory classes,
ToolRegistry, and helper utilities.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the azure package is importable
# ---------------------------------------------------------------------------
_AZURE_DIR = Path(__file__).resolve().parent.parent / "azure"
if str(_AZURE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_AZURE_DIR.parent))

from azure.agent import (
    LongTermMemory,
    ShortTermMemory,
    ToolRegistry,
    _retry_transient,
    tool_get_time,
)
from azure.circuit_breaker import CircuitBreaker
from azure.errors import (
    AzureError,
    DatabaseError,
    LLMError,
    ModerationError,
    RateLimitError,
    ToolExecutionError,
)
from azure.logging_config import (
    ContextFilter,
    clear_request_context,
    generate_execution_id,
    set_request_context,
    setup_logging,
)
from azure.subsystem_status import SubsystemInfo, SubsystemRegistry
from azure.task_manager import TaskManager
from azure.telemetry import (
    ExecutionTracker,
    Stage,
    TelemetryEvent,
)

# ============================================================================
# CIRCUIT BREAKER TESTS
# ============================================================================

class TestCircuitBreakerStateTransitions:
    """State machine correctness for CircuitBreaker."""

    def test_closed_allows_all_requests(self):
        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
        for _ in range(4):
            assert cb.allow_request() is True

    def test_failures_increment_counter(self):
        cb = CircuitBreaker(failure_threshold=5)
        for i in range(4):
            cb.record_failure()
            info = cb.get_info()
            assert info["failure_count"] == i + 1

    def test_threshold_triggers_open(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.allow_request() is True
        cb.record_failure()
        assert cb.state == "OPEN"

    def test_open_blocks_all_requests(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=9999)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "OPEN"
        assert cb.allow_request() is False
        assert cb.allow_request() is False

    def test_cooldown_transitions_to_half_open(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.02)
        cb.record_failure()
        assert cb.state == "OPEN"
        time.sleep(0.05)
        assert cb.state == "HALF_OPEN"

    def test_half_open_allows_one_request(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.02)
        cb.record_failure()
        time.sleep(0.05)
        assert cb.allow_request() is True

    def test_success_in_half_open_closes_circuit(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.02)
        cb.record_failure()
        time.sleep(0.05)
        assert cb.allow_request() is True
        cb.record_success()
        assert cb.state == "CLOSED"
        assert cb.get_info()["failure_count"] == 0

    def test_failure_in_half_open_reopens_circuit(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.02)
        cb.record_failure()
        time.sleep(0.05)
        assert cb.allow_request() is True
        cb.record_failure()
        assert cb.state == "OPEN"

    def test_get_info_returns_correct_state(self):
        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=30)
        info = cb.get_info()
        assert info["state"] == "CLOSED"
        assert info["failure_count"] == 0
        assert info["failure_threshold"] == 5
        assert info["cooldown_seconds"] == 30
        assert info["seconds_since_failure"] is None

    def test_custom_threshold_and_cooldown(self):
        cb = CircuitBreaker(failure_threshold=10, cooldown_seconds=120)
        assert cb.get_info()["failure_threshold"] == 10
        assert cb.get_info()["cooldown_seconds"] == 120

    def test_threshold_one_immediate_open(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()
        assert cb.state == "OPEN"
        assert cb.allow_request() is False

    def test_cooldown_zero_immediate_half_open(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0)
        cb.record_failure()
        assert cb.get_info()["state"] == "OPEN"
        assert cb.allow_request() is True
        assert cb.state == "HALF_OPEN"

    def test_multiple_circuits_are_independent(self):
        cb1 = CircuitBreaker(failure_threshold=1)
        cb2 = CircuitBreaker(failure_threshold=5)
        cb1.record_failure()
        assert cb1.state == "OPEN"
        assert cb2.state == "CLOSED"

    def test_reset_after_partial_failures(self):
        cb = CircuitBreaker(failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.allow_request() is True
        cb.record_success()
        assert cb.get_info()["failure_count"] == 0
        assert cb.state == "CLOSED"

    def test_rapid_state_transitions(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0)
        for _ in range(5):
            cb.record_failure()
            assert cb.get_info()["state"] == "OPEN"
            assert cb.allow_request() is True
            assert cb.state == "HALF_OPEN"
            cb.record_success()
            assert cb.state == "CLOSED"

    def test_seconds_since_failure_populated(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        info = cb.get_info()
        assert info["seconds_since_failure"] is not None
        assert info["seconds_since_failure"] >= 0

    def test_state_property_thread_safety(self):
        cb = CircuitBreaker(failure_threshold=50, cooldown_seconds=9999)
        errors = []

        def worker():
            try:
                for _ in range(100):
                    cb.allow_request()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

    def test_concurrent_allow_request_and_record_failure(self):
        cb = CircuitBreaker(failure_threshold=20, cooldown_seconds=9999)
        errors = []

        def reader():
            try:
                for _ in range(200):
                    cb.allow_request()
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                for _ in range(200):
                    cb.record_failure()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(5)]
        threads += [threading.Thread(target=writer) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert cb.state == "OPEN"

    def test_record_failure_increments_count(self):
        cb = CircuitBreaker(failure_threshold=100)
        for _i in range(10):
            cb.record_failure()
        assert cb.get_info()["failure_count"] == 10

    def test_half_open_blocks_after_one_request(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.02)
        cb.record_failure()
        time.sleep(0.05)
        assert cb.allow_request() is True
        cb.record_failure()
        assert cb.allow_request() is False


# ============================================================================
# TYPED ERRORS TESTS
# ============================================================================

class TestTypedErrors:
    """Structured error hierarchy tests."""

    def test_azure_error_is_base_of_all(self):
        assert issubclass(LLMError, AzureError)
        assert issubclass(RateLimitError, AzureError)
        assert issubclass(ToolExecutionError, AzureError)
        assert issubclass(ModerationError, AzureError)
        assert issubclass(DatabaseError, AzureError)

    def test_llm_error_has_provider_message_status_code(self):
        err = LLMError(provider="openai", message="timeout", status_code=504)
        assert err.provider == "openai"
        assert err.status_code == 504
        assert "timeout" in str(err)

    def test_llm_error_no_status_code(self):
        err = LLMError(provider="llama", message="bad output")
        assert err.status_code is None

    def test_rate_limit_error_has_retry_after(self):
        err = RateLimitError(retry_after=30.5)
        assert err.retry_after == 30.5
        assert "30.5" in str(err)

    def test_rate_limit_error_default_retry_after(self):
        err = RateLimitError()
        assert err.retry_after == 0

    def test_tool_execution_error_has_tool_name(self):
        err = ToolExecutionError(tool_name="web_search", message="DNS failure")
        assert err.tool_name == "web_search"
        assert "web_search" in str(err)

    def test_all_catchable_as_azure_error(self):
        errors = [
            LLMError(provider="x", message="y"),
            RateLimitError(retry_after=1),
            ToolExecutionError(tool_name="t", message="m"),
            ModerationError("mod fail"),
            DatabaseError("db fail"),
        ]
        for err in errors:
            with pytest.raises(AzureError):
                raise err

    def test_string_representation(self):
        err = LLMError(provider="openai", message="rate limit exceeded", status_code=429)
        s = str(err)
        assert "openai" in s
        assert "rate limit exceeded" in s

    def test_exception_chaining(self):
        try:
            try:
                raise ValueError("root cause")
            except ValueError as e:
                raise LLMError(provider="x", message="wrapped") from e
        except LLMError as err:
            assert err.__cause__ is not None
            assert isinstance(err.__cause__, ValueError)

    def test_moderation_error_standalone(self):
        err = ModerationError("blocked by policy")
        assert "blocked" in str(err)

    def test_database_error_standalone(self):
        err = DatabaseError("connection refused")
        assert "connection refused" in str(err)

    def test_azure_error_standalone(self):
        err = AzureError("generic")
        assert isinstance(err, Exception)
        assert str(err) == "generic"


# ============================================================================
# SUBSYSTEM REGISTRY TESTS
# ============================================================================

class TestSubsystemRegistry:
    """SubsystemRegistry health tracking tests."""

    def test_register_ok_subsystem(self):
        reg = SubsystemRegistry()
        reg.register("llm", status="ok")
        assert reg.is_available("llm") is True

    def test_register_degraded_subsystem(self):
        reg = SubsystemRegistry()
        reg.register("rag", status="degraded", error="slow queries")
        assert reg.is_available("rag") is False

    def test_register_unavailable_subsystem(self):
        reg = SubsystemRegistry()
        reg.register("db", status="unavailable", error="connection lost")
        assert reg.is_available("db") is False

    def test_is_available_for_each_state(self):
        reg = SubsystemRegistry()
        reg.register("a", status="ok")
        reg.register("b", status="degraded")
        reg.register("c", status="unavailable")
        assert reg.is_available("a") is True
        assert reg.is_available("b") is False
        assert reg.is_available("c") is False

    def test_get_summary_returns_all_states(self):
        reg = SubsystemRegistry()
        reg.register("x", status="ok")
        reg.register("y", status="degraded")
        summary = reg.get_summary()
        assert summary == {"x": "ok", "y": "degraded"}

    def test_log_summary_output(self, caplog):
        reg = SubsystemRegistry()
        reg.register("llm", status="ok")
        reg.register("rag", status="degraded", error="slow")
        with caplog.at_level(logging.WARNING, logger="azure.subsystem_status"):
            reg.log_summary()
        assert "llm" in caplog.text or "rag" in caplog.text

    def test_multiple_subsystems(self):
        reg = SubsystemRegistry()
        for i in range(20):
            reg.register(f"sub_{i}", status="ok")
        summary = reg.get_summary()
        assert len(summary) == 20

    def test_overwrite_existing_registration(self):
        reg = SubsystemRegistry()
        reg.register("llm", status="ok")
        assert reg.is_available("llm") is True
        reg.register("llm", status="unavailable", error="crashed")
        assert reg.is_available("llm") is False
        assert reg.get_summary()["llm"] == "unavailable"

    def test_is_available_unknown_name(self):
        reg = SubsystemRegistry()
        assert reg.is_available("nonexistent") is False

    def test_empty_registry_summary(self):
        reg = SubsystemRegistry()
        assert reg.get_summary() == {}

    def test_subsystem_info_dataclass(self):
        info = SubsystemInfo(name="test", status="ok", error="")
        assert info.name == "test"
        assert info.status == "ok"
        assert info.error == ""

    def test_registry_module_level_instance(self):
        from azure.subsystem_status import registry
        assert isinstance(registry, SubsystemRegistry)

    def test_multiple_status_overwrites(self):
        reg = SubsystemRegistry()
        for status in ("ok", "degraded", "unavailable", "ok"):
            reg.register("x", status=status)
        assert reg.get_summary()["x"] == "ok"


# ============================================================================
# LOGGING CONFIG TESTS
# ============================================================================

class TestLoggingConfig:
    """Logging setup, context propagation, and execution ID tests."""

    def test_setup_logging_configures_root_logger(self):
        setup_logging(level=logging.DEBUG)
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) >= 1

    def test_set_request_context_stores_values(self):
        setup_logging()
        set_request_context(execution_id="abc123", user_id="user42")
        filt = ContextFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "", (), None)
        filt.filter(record)
        assert record.execution_id == "abc123"
        assert record.user_id == "user42"
        clear_request_context()

    def test_clear_request_context_resets_values(self):
        setup_logging()
        set_request_context(execution_id="abc", user_id="u1")
        clear_request_context()
        filt = ContextFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "", (), None)
        filt.filter(record)
        assert record.execution_id == "none"
        assert record.user_id == "none"

    def test_generate_execution_id_is_unique(self):
        ids = {generate_execution_id() for _ in range(200)}
        assert len(ids) == 200

    def test_generate_execution_id_length(self):
        eid = generate_execution_id()
        assert len(eid) == 12
        assert all(c in "0123456789abcdef" for c in eid)

    def test_context_filter_adds_fields_to_records(self):
        setup_logging()
        set_request_context(execution_id="test_exec", user_id="test_user")
        filt = ContextFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "", (), None)
        result = filt.filter(record)
        assert result is True
        assert record.execution_id == "test_exec"
        assert record.user_id == "test_user"
        clear_request_context()

    def test_thread_isolation(self):
        setup_logging()
        set_request_context(execution_id="main_exec", user_id="main_user")
        results = {}

        def worker(name):
            set_request_context(execution_id=f"{name}_exec", user_id=f"{name}_user")
            filt = ContextFilter()
            record = logging.LogRecord("test", logging.INFO, "", 0, "", (), None)
            filt.filter(record)
            results[name] = (record.execution_id, record.user_id)

        t1 = threading.Thread(target=worker, args=("t1",))
        t2 = threading.Thread(target=worker, args=("t2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results["t1"] == ("t1_exec", "t1_user")
        assert results["t2"] == ("t2_exec", "t2_user")
        clear_request_context()

    def test_multiple_context_updates(self):
        setup_logging()
        set_request_context(execution_id="v1", user_id="u1")
        set_request_context(execution_id="v2", user_id="u2")
        filt = ContextFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "", (), None)
        filt.filter(record)
        assert record.execution_id == "v2"
        assert record.user_id == "u2"
        clear_request_context()

    def test_none_values_not_overwritten(self):
        setup_logging()
        set_request_context(execution_id="keep_this", user_id="keep_this")
        set_request_context(execution_id=None, user_id=None)
        filt = ContextFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "", (), None)
        filt.filter(record)
        assert record.execution_id == "keep_this"
        assert record.user_id == "keep_this"
        clear_request_context()

    def test_partial_context_update(self):
        setup_logging()
        set_request_context(execution_id="only_exec")
        filt = ContextFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "", (), None)
        filt.filter(record)
        assert record.execution_id == "only_exec"
        clear_request_context()


# ============================================================================
# TELEMETRY TESTS
# ============================================================================

class TestTelemetry:
    """ExecutionTracker, TelemetryEvent, and Stage tests."""

    def test_tracker_creation(self):
        t = ExecutionTracker(user="alice", guild="test-guild", request_text="hello")
        assert t.execution_id
        assert t.user == "alice"
        assert t.guild == "test-guild"
        assert t.request_text == "hello"
        assert t.is_finished is False
        assert len(t.events) == 0

    def test_emit_adds_events(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        t.emit("START", "Beginning work")
        t.emit("ANALYZING", "Looking at message")
        assert len(t.events) == 2
        assert t.events[0].action == "START"
        assert t.events[1].action == "ANALYZING"

    def test_emit_normalizes_action(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        t.emit("  my action  ", "msg")
        assert t.events[0].action == "MY_ACTION"

    def test_emit_empty_action_becomes_event(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        t.emit("", "msg")
        assert t.events[0].action == "EVENT"

    def test_get_discord_progress_text_formats(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        t.emit("START", "Starting")
        text = t.get_discord_progress_text()
        assert "Thinking" in text or "Done" in text

    def test_complete_marks_finished(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        t.emit("START", "go")
        t.complete(success=True, message="All done")
        assert t.is_finished is True

    def test_complete_emits_complete_event(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        t.complete(success=True, message="done")
        complete_events = [e for e in t.events if e.action == "COMPLETE"]
        assert len(complete_events) == 1

    def test_complete_error_emits_error_event(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        t.complete(success=False, message="failed")
        error_events = [e for e in t.events if e.action == "ERROR"]
        assert len(error_events) == 1

    def test_complete_idempotent(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        t.complete(success=True)
        t.complete(success=True)
        completes = [e for e in t.events if e.action == "COMPLETE"]
        assert len(completes) == 1

    def test_thread_safety_concurrent_emits(self):
        with patch("azure.telemetry._WS_MANAGER", False):
            t = ExecutionTracker(user="a", guild="g", request_text="m")
            t._events_max = 1000
            errors = []

            def emit_many():
                try:
                    for i in range(100):
                        t.emit("STEP", f"step {i}")
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=emit_many) for _ in range(5)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            assert errors == []
            assert len(t.events) == 500

    def test_progress_dot_animation(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        t.emit("START", "go")
        texts = [t.get_discord_progress_text() for _ in range(6)]
        dots = [t.count(".") for t in texts]
        assert max(dots) >= 1

    def test_get_presentation_returns_dict(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        t.emit("START", "go")
        p = t.get_presentation()
        assert isinstance(p, dict)
        assert "execution_id" in p
        assert "stages" in p
        assert "elapsed_ms" in p
        assert "discord_text" in p

    def test_multiple_stages(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        t.emit("START", "go")
        t.emit("ANALYZING", "thinking")
        t.emit("GENERATING", "writing")
        t.complete(success=True)
        p = t.get_presentation()
        assert len(p["stages"]) >= 3

    def test_error_events(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        t.emit("ERROR", "something broke", status="error")
        assert len(t.events) == 1
        assert t.events[0].status == "error"

    def test_timeout_formatting(self):
        assert ExecutionTracker._format_duration(500) == "500ms"
        assert ExecutionTracker._format_duration(1500) == "1.5s"
        assert ExecutionTracker._format_duration(65000) == "1m 5s"

    def test_event_capping(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        t._events_max = 10
        for i in range(20):
            t.emit("STEP", f"step {i}")
        assert len(t.events) == 10
        assert t.events[0].message == "step 10"

    def test_complete_closes_running_stages(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        t.emit("GENERATING", "working", status="running")
        running_stages = [s for s in t.stages if s.status == "running"]
        assert len(running_stages) == 1
        t.complete(success=True)
        running_stages = [s for s in t.stages if s.status == "running"]
        assert len(running_stages) == 0

    def test_callback_called_on_emit(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        cb = MagicMock()
        t.add_callback(cb)
        t.emit("START", "hello")
        cb.assert_called_once()

    def test_callback_error_does_not_crash(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        bad_cb = MagicMock(side_effect=RuntimeError("boom"))
        t.add_callback(bad_cb)
        t.emit("START", "hello")
        bad_cb.assert_called_once()

    def test_telemetry_event_to_dict(self):
        e = TelemetryEvent(
            execution_id="exec1",
            subsystem="agent",
            action="START",
            message="hello",
            status="info",
        )
        d = e.to_dict()
        assert d["execution_id"] == "exec1"
        assert d["action"] == "START"

    def test_stage_duration_ms(self):
        now = time.time()
        s = Stage(
            stage_id="s1",
            action="TEST",
            label="Test",
            detail="detail",
            status="done",
            started_at=now - 0.1,
            ended_at=now,
        )
        assert s.duration_ms >= 90

    def test_stage_duration_ms_running(self):
        s = Stage(
            stage_id="s1",
            action="TEST",
            label="Test",
            detail="detail",
            status="running",
            started_at=time.time() - 0.05,
        )
        assert s.duration_ms >= 40

    def test_stage_to_dict(self):
        s = Stage(
            stage_id="s1",
            action="TEST",
            label="Test",
            detail="d",
            status="done",
            started_at=1.0,
            ended_at=2.0,
        )
        d = s.to_dict()
        assert d["stage_id"] == "s1"
        assert d["duration_ms"] == 1000

    def test_stages_capped_at_12(self):
        t = ExecutionTracker(user="a", guild="g", request_text="m")
        for i in range(15):
            t.emit("STEP", f"step {i}")
        assert len(t.stages) <= 12


# ============================================================================
# TASK MANAGER TESTS
# ============================================================================

class TestTaskManager:
    """TaskManager lifecycle, queue, and stats tests."""

    @pytest.fixture
    def tm(self):
        return TaskManager()

    @pytest.mark.asyncio
    async def test_task_starts_immediately_when_idle(self, tm):
        async def dummy():
            return "result"

        result = await tm.start_task("test", dummy())
        assert result == "result"

    @pytest.mark.asyncio
    async def test_task_sets_busy_during_execution(self, tm):
        started = asyncio.Event()
        can_continue = asyncio.Event()

        async def slow_task():
            started.set()
            await can_continue.wait()
            return "done"

        task = asyncio.create_task(tm.start_task("slow", slow_task()))
        await started.wait()
        assert tm.is_busy is True
        can_continue.set()
        await task
        assert tm.is_busy is False

    @pytest.mark.asyncio
    async def test_queue_if_busy(self, tm):
        gate = asyncio.Event()

        async def blocker():
            await gate.wait()
            return "blocker_done"

        async def queued():
            return "queued_done"

        t1 = asyncio.create_task(tm.start_task("blocker", blocker()))
        await asyncio.sleep(0.01)
        assert tm.is_busy is True

        result2 = await tm.start_task("queued", queued(), queue_if_busy=True)
        assert result2 is None
        assert tm.queue_size() == 1

        gate.set()
        await t1
        assert tm.queue_size() == 0

    @pytest.mark.asyncio
    async def test_not_queue_returns_busy_message(self, tm):
        ctx = AsyncMock()
        gate = asyncio.Event()

        async def blocker():
            await gate.wait()

        t1 = asyncio.create_task(tm.start_task("blocker", blocker()))
        await asyncio.sleep(0.01)

        result = await tm.start_task("second", AsyncMock()(), ctx=ctx, on_busy="Busy!", queue_if_busy=False)
        assert result is None
        ctx.send.assert_called_once_with("Busy!")

        gate.set()
        await t1

    @pytest.mark.asyncio
    async def test_queue_size_limit(self, tm):
        gate = asyncio.Event()

        async def blocker():
            await gate.wait()

        tm._MAX_QUEUE_SIZE = 3
        t1 = asyncio.create_task(tm.start_task("blocker", blocker()))
        await asyncio.sleep(0.01)

        for _ in range(3):
            await tm.start_task("q", AsyncMock()(), queue_if_busy=True)

        ctx = AsyncMock()
        await tm.start_task("over", AsyncMock()(), ctx=ctx, queue_if_busy=True)
        assert tm.queue_size() == 3

        gate.set()
        await t1

    @pytest.mark.asyncio
    async def test_dead_letter_on_failure(self, tm):
        async def failing():
            raise RuntimeError("boom")

        ctx = AsyncMock()
        ctx.guild = None
        await tm.start_task("fail", failing(), ctx=ctx)
        dead = tm.get_dead_letter()
        assert len(dead) == 1
        assert dead[0].name == "fail"
        assert "boom" in dead[0].error

    @pytest.mark.asyncio
    async def test_cancel_current(self, tm):
        gate = asyncio.Event()

        async def blocker():
            await gate.wait()

        asyncio.create_task(tm.start_task("blocker", blocker()))
        await asyncio.sleep(0.01)
        await tm.cancel_current()
        assert tm.is_busy is False
        assert tm.get_current_task() == ""

    @pytest.mark.asyncio
    async def test_get_stats(self, tm):
        async def ok():
            return "ok"

        await tm.start_task("t1", ok())
        stats = tm.get_stats()
        assert stats["total_tasks"] >= 1
        assert stats["successful"] >= 1
        assert "avg_duration" in stats

    @pytest.mark.asyncio
    async def test_history_tracking(self, tm):
        async def ok():
            return "ok"

        await tm.start_task("t1", ok())
        await tm.start_task("t2", ok())
        hist = tm.get_history()
        assert len(hist) >= 2

    @pytest.mark.asyncio
    async def test_task_timeout_triggers_retry(self, tm):
        attempts = []

        def slow_factory():
            async def slow():
                attempts.append(time.time())
                await asyncio.sleep(10)
            return slow()

        tm.TASK_TIMEOUT = 0.1
        tm.MAX_RETRIES = 2
        ctx = AsyncMock()
        ctx.guild = None
        result = await tm.start_task("timeout", slow_factory, ctx=ctx)
        assert result is None
        assert len(attempts) >= 2

    @pytest.mark.asyncio
    async def test_transient_error_retries(self, tm):
        attempts = []

        def factory():
            async def attempt():
                attempts.append(len(attempts))
                if len(attempts) < 2:
                    raise ConnectionError("connection lost")
                return "ok"
            return attempt()

        result = await tm.start_task("transient", factory)
        assert result == "ok"
        assert len(attempts) == 2

    @pytest.mark.asyncio
    async def test_non_transient_error_raises(self, tm):
        async def fail():
            raise ValueError("bad value")

        ctx = AsyncMock()
        ctx.guild = None
        result = await tm.start_task("bad", fail(), ctx=ctx)
        assert result is None
        dead = tm.get_dead_letter()
        assert any(d.name == "bad" for d in dead)

    @pytest.mark.asyncio
    async def test_clear_queue(self, tm):
        gate = asyncio.Event()

        async def blocker():
            await gate.wait()

        t1 = asyncio.create_task(tm.start_task("blocker", blocker()))
        await asyncio.sleep(0.01)
        await tm.start_task("q1", AsyncMock()(), queue_if_busy=True)
        await tm.start_task("q2", AsyncMock()(), queue_if_busy=True)
        assert tm.queue_size() == 2
        tm.clear_queue()
        assert tm.queue_size() == 0
        gate.set()
        await t1

    @pytest.mark.asyncio
    async def test_get_queue_names(self, tm):
        gate = asyncio.Event()

        async def blocker():
            await gate.wait()

        t1 = asyncio.create_task(tm.start_task("blocker", blocker()))
        await asyncio.sleep(0.01)
        await tm.start_task("alpha", AsyncMock()(), queue_if_busy=True)
        await tm.start_task("beta", AsyncMock()(), queue_if_busy=True)
        names = tm.get_queue_names()
        assert "alpha" in names
        assert "beta" in names
        gate.set()
        await t1

    @pytest.mark.asyncio
    async def test_coroutine_factory_for_retry(self, tm):
        call_count = 0

        def factory():
            nonlocal call_count
            call_count += 1

            async def attempt():
                if call_count < 2:
                    raise TimeoutError("timeout")
                return "recovered"
            return attempt()

        result = await tm.start_task("retry", factory)
        assert result == "recovered"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_start_task_invalid_coro_type(self, tm):
        with pytest.raises(TypeError):
            await tm.start_task("bad", "not a coroutine")

    @pytest.mark.asyncio
    async def test_concurrent_task_starts(self, tm):
        results = []

        async def work(name):
            await asyncio.sleep(0.01)
            results.append(name)

        await tm.start_task("t1", work("t1"))
        await tm.start_task("t2", work("t2"))
        assert "t1" in results
        assert "t2" in results


# ============================================================================
# AGENT: SHORT-TERM MEMORY TESTS
# ============================================================================

class TestShortTermMemory:
    """ShortTermMemory rolling window tests."""

    def test_add_and_to_history(self):
        mem = ShortTermMemory(max_turns=5)
        mem.add("user", "hello")
        mem.add("assistant", "hi there")
        hist = mem.to_history()
        assert len(hist) == 2
        assert hist[0]["role"] == "user"
        assert hist[1]["role"] == "assistant"

    def test_rolling_window(self):
        mem = ShortTermMemory(max_turns=3)
        for i in range(10):
            mem.add("user", f"msg {i}")
        hist = mem.to_history()
        assert len(hist) == 6
        assert hist[-1]["content"] == "msg 9"

    def test_context_block(self):
        mem = ShortTermMemory(max_turns=5)
        mem.add("user", "hello")
        mem.add("assistant", "hi")
        block = mem.context_block()
        assert "<user> hello" in block
        assert "<assistant> hi" in block

    def test_empty_context_block(self):
        mem = ShortTermMemory(max_turns=5)
        assert mem.context_block() == ""

    def test_thread_safety(self):
        mem = ShortTermMemory(max_turns=500)
        errors = []

        def add_messages():
            try:
                for i in range(100):
                    mem.add("user", f"msg {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_messages) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(mem.to_history()) == 500

    def test_name_field_preserved(self):
        mem = ShortTermMemory(max_turns=5)
        mem.add("user", "hi", name="Alice")
        hist = mem.to_history()
        assert hist[0]["name"] == "Alice"

    def test_timestamp_added(self):
        mem = ShortTermMemory(max_turns=5)
        mem.add("user", "hi")
        assert "t" in mem.messages[0]


# ============================================================================
# AGENT: LONG-TERM MEMORY TESTS
# ============================================================================

class TestLongTermMemory:
    """LongTermMemory persistence and search tests."""

    def test_remember_and_recall(self, tmp_path):
        p = tmp_path / "mem.json"
        mem = LongTermMemory(path=p)
        mem.remember("color", "blue")
        assert mem.recall("color") == "blue"

    def test_recall_missing_key(self, tmp_path):
        p = tmp_path / "mem.json"
        mem = LongTermMemory(path=p)
        assert mem.recall("nonexistent") is None

    def test_search(self, tmp_path):
        p = tmp_path / "mem.json"
        mem = LongTermMemory(path=p)
        mem.remember("fav_food", "pizza")
        mem.remember("fav_color", "blue")
        hits = mem.search("food")
        assert len(hits) >= 1
        assert hits[0][0] == "fav_food"

    def test_search_no_matches(self, tmp_path):
        p = tmp_path / "mem.json"
        mem = LongTermMemory(path=p)
        mem.remember("x", "y")
        hits = mem.search("zzz")
        assert len(hits) == 0

    def test_persistence(self, tmp_path):
        p = tmp_path / "mem.json"
        mem = LongTermMemory(path=p)
        mem.remember("key", "value")
        mem2 = LongTermMemory(path=p)
        assert mem2.recall("key") == "value"

    def test_search_limit(self, tmp_path):
        p = tmp_path / "mem.json"
        mem = LongTermMemory(path=p)
        for i in range(10):
            mem.remember(f"item_{i}", "test")
        hits = mem.search("test", k=3)
        assert len(hits) == 3

    def test_corrupt_file_recovery(self, tmp_path):
        p = tmp_path / "mem.json"
        p.write_text("not json !!!")
        mem = LongTermMemory(path=p)
        assert mem.facts == {}

    def test_search_case_insensitive(self, tmp_path):
        p = tmp_path / "mem.json"
        mem = LongTermMemory(path=p)
        mem.remember("MyKey", "MyValue")
        hits = mem.search("mykey")
        assert len(hits) == 1

    def test_thread_safety(self, tmp_path):
        p = tmp_path / "mem.json"
        mem = LongTermMemory(path=p)
        errors = []

        def writer():
            try:
                for i in range(50):
                    mem.remember(f"k{i}", f"v{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(mem.facts) > 0


# ============================================================================
# AGENT: TOOL REGISTRY TESTS
# ============================================================================

class TestToolRegistry:
    """ToolRegistry register, call, describe tests."""

    def test_register_and_call(self):
        reg = ToolRegistry()
        reg.register("add", "Add two numbers", lambda a, b: a + b)
        result = reg.call("add", a=3, b=4)
        assert result == {"ok": True, "result": 7}

    def test_call_unknown_tool(self):
        reg = ToolRegistry()
        result = reg.call("nonexistent")
        assert "error" in result

    def test_call_tool_with_exception(self):
        reg = ToolRegistry()
        reg.register("fail", "Always fails", lambda: 1 / 0)
        result = reg.call("fail")
        assert result["ok"] is False
        assert "error" in result

    def test_describe(self):
        reg = ToolRegistry()
        reg.register("tool1", "desc1", lambda: None)
        reg.register("tool2", "desc2", lambda: None, schema={"type": "object"})
        desc = reg.describe()
        assert len(desc) == 2
        assert desc[0]["name"] == "tool1"

    def test_overwrite_tool(self):
        reg = ToolRegistry()
        reg.register("x", "v1", lambda: 1)
        reg.register("x", "v2", lambda: 2)
        result = reg.call("x")
        assert result["result"] == 2


# ============================================================================
# AGENT: RETRY TRANSIENT TESTS
# ============================================================================

class TestRetryTransient:
    """_retry_transient helper tests."""

    def test_success_first_attempt(self):
        result = _retry_transient(lambda: "ok", max_retries=3)
        assert result == "ok"

    def test_retries_on_connection_error(self):
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("fail")
            return "ok"

        result = _retry_transient(flaky, max_retries=3, base_delay=0)
        assert result == "ok"
        assert len(attempts) == 3

    def test_exhausts_retries(self):
        with pytest.raises(OSError):
            _retry_transient(lambda: (_ for _ in ()).throw(OSError("always")), max_retries=2, base_delay=0)

    def test_does_not_retry_value_error(self):
        with pytest.raises(ValueError):
            _retry_transient(lambda: (_ for _ in ()).throw(ValueError("no")), max_retries=3, base_delay=0)


# ============================================================================
# AGENT: TOOL_GET_TIME TESTS
# ============================================================================

class TestToolGetTime:
    """Built-in get_time tool test."""

    def test_returns_string(self):
        result = tool_get_time()
        assert isinstance(result, str)

    def test_format(self):
        result = tool_get_time()
        assert len(result) == 19
        assert result[4] == "-"
        assert result[7] == "-"
        assert result[10] == " "
        assert result[13] == ":"
        assert result[16] == ":"


# ============================================================================
# AGENT: MESSAGE INTENT CLASSIFICATION TESTS
# ============================================================================

class TestClassifyMessageIntent:
    """AzureAgent._classify_message_intent — structural telemetry only (LLM-first)."""

    def test_greeting(self):
        from azure.agent import AzureAgent
        intent = AzureAgent._classify_message_intent("hello there")
        # Keyword greeting banks removed; routing is IntentClassifier/ToolEngine
        assert intent["is_greeting"] is False
        assert intent["length"] > 0

    def test_question(self):
        from azure.agent import AzureAgent
        intent = AzureAgent._classify_message_intent("what is the weather?")
        assert intent["is_question"] is True

    def test_command(self):
        from azure.agent import AzureAgent
        intent = AzureAgent._classify_message_intent("create a new channel")
        assert intent["is_command"] is False

    def test_memory(self):
        from azure.agent import AzureAgent
        intent = AzureAgent._classify_message_intent("remember that I like pizza")
        assert intent["needs_memory"] is False

    def test_plain_message(self):
        from azure.agent import AzureAgent
        intent = AzureAgent._classify_message_intent("just saying hi")
        assert intent["is_greeting"] is False
        assert intent["is_question"] is False
        assert intent["is_command"] is False

    def test_recall_intent(self):
        from azure.agent import AzureAgent
        intent = AzureAgent._classify_message_intent("what did we talk about last time?")
        assert intent["needs_memory"] is False
        assert intent["is_question"] is True


# ============================================================================
# AGENT: PARSE_REQUESTER_ID TESTS
# ============================================================================

class TestParseRequesterId:
    """AzureAgent._parse_requester_id tests."""

    def test_valid_id(self):
        from azure.agent import AzureAgent
        assert AzureAgent._parse_requester_id("12345") == 12345

    def test_empty_string(self):
        from azure.agent import AzureAgent
        assert AzureAgent._parse_requester_id("") is None

    def test_none(self):
        from azure.agent import AzureAgent
        assert AzureAgent._parse_requester_id(None) is None

    def test_invalid_format(self):
        from azure.agent import AzureAgent
        assert AzureAgent._parse_requester_id("abc") is None


# ============================================================================
# AGENT: POST-PROCESS RESPONSE TESTS
# ============================================================================

class TestPostProcessResponse:
    """AzureAgent._post_process_response quality filter tests."""

    def _get_agent(self):
        from azure.agent import AzureAgent
        with patch.object(AzureAgent, "__init__", lambda self, *a, **kw: None):
            agent = AzureAgent.__new__(AzureAgent)
            return agent

    def test_empty_reply(self):
        agent = self._get_agent()
        assert agent._post_process_response("", "hello") == ""

    def test_single_punctuation(self):
        agent = self._get_agent()
        assert agent._post_process_response(".", "hello") == ""

    def test_removes_filler_prefix(self):
        agent = self._get_agent()
        result = agent._post_process_response("Sure! Here you go.", "help me")
        assert not result.startswith("Sure!")

    def test_fixes_hallucinated_mentions(self):
        agent = self._get_agent()
        result = agent._post_process_response("Hello @RandomPerson how are you?", "hi @bob")
        assert "@RandomPerson" not in result or "RandomPerson" not in result

    def test_collapses_blank_lines(self):
        agent = self._get_agent()
        result = agent._post_process_response("a\n\n\n\nb", "test")
        assert "\n\n\n" not in result

    def test_echo_detection(self):
        agent = self._get_agent()
        result = agent._post_process_response("hello world", "hello world")
        assert result == ""

    def test_valid_reply_preserved(self):
        agent = self._get_agent()
        result = agent._post_process_response("This is a helpful reply.", "question")
        assert result == "This is a helpful reply."


# ============================================================================
# AGENT: BUILD CALL CONTEXT TESTS
# ============================================================================

class TestBuildCallContext:
    """AzureAgent._build_call_context static method tests."""

    def test_empty_context(self):
        from azure.agent import AzureAgent
        ctx = AzureAgent._build_call_context(None, None, None, None)
        assert ctx == {}

    def test_full_context(self):
        from azure.agent import AzureAgent
        ctx = AzureAgent._build_call_context("guild", "channel", "loop", "tools")
        assert ctx["guild"] == "guild"
        assert ctx["channel"] == "channel"
        assert ctx["event_loop"] == "loop"
        assert ctx["discord_tools"] == "tools"

    def test_partial_context(self):
        from azure.agent import AzureAgent
        ctx = AzureAgent._build_call_context("guild", None, None, None)
        assert ctx == {"guild": "guild"}


# ============================================================================
# AGENT: BUILD PLAN SUMMARY TESTS
# ============================================================================

class TestBuildPlanSummary:
    """AzureAgent._build_plan_summary tests."""

    def _get_agent(self):
        from azure.agent import AzureAgent
        with patch.object(AzureAgent, "__init__", lambda self, *a, **kw: None):
            agent = AzureAgent.__new__(AzureAgent)
            agent.short_term = ShortTermMemory(max_turns=5)
            return agent

    def test_successful_results(self):
        agent = self._get_agent()
        r1 = MagicMock(success=True, action="create_channel", name="general", error="")
        r2 = MagicMock(success=True, action="set_permissions", name="everyone", error="")
        summary = agent._build_plan_summary([r1, r2], {"short_term": ShortTermMemory(max_turns=5)})
        assert "create_channel" in summary
        assert "set_permissions" in summary

    def test_failed_results(self):
        agent = self._get_agent()
        r1 = MagicMock(success=False, action="delete_role", name="", error="permission denied")
        summary = agent._build_plan_summary([r1], {"short_term": ShortTermMemory(max_turns=5)})
        assert "delete_role" in summary
        assert "permission denied" in summary

    def test_empty_results(self):
        agent = self._get_agent()
        summary = agent._build_plan_summary([], {"short_term": ShortTermMemory(max_turns=5)})
        assert summary == ""


# ============================================================================
# INTEGRATION: MULTI-SUBSYSTEM OVERVIEW
# ============================================================================

class TestIntegrationOverview:
    """Cross-cutting integration scenarios."""

    def test_circuit_breaker_with_telemetry(self):
        with patch("azure.telemetry._WS_MANAGER", False):
            cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.01)
            t = ExecutionTracker(user="u", guild="g", request_text="m")
            t.emit("START", "begin")

            for _ in range(3):
                cb.record_failure()
                t.emit("ERROR", "failure", status="error")

            assert cb.state == "OPEN"
            assert len(t.events) == 4
            assert t.events[-1].action == "ERROR"

    @pytest.mark.asyncio
    async def test_task_manager_with_tracker(self):
        with patch("azure.telemetry._WS_MANAGER", False):
            tm = TaskManager()
            t = ExecutionTracker(user="u", guild="g", request_text="m")
            t.emit("START", "begin")

            async def work():
                t.emit("STEP", "doing stuff")
                return "done"

            result = await tm.start_task("tracked", work())
            assert result == "done"
            assert len(t.events) == 2

    def test_subsystem_registry_with_logging(self, caplog):
        reg = SubsystemRegistry()
        reg.register("llm", status="ok")
        reg.register("rag", status="degraded", error="slow")
        with caplog.at_level(logging.WARNING, logger="azure.subsystem_status"):
            reg.log_summary()
        assert "rag" in caplog.text

    def test_error_hierarchy_with_logging(self):
        with pytest.raises(AzureError) as exc_info:
            try:
                raise ValueError("root")
            except ValueError as e:
                raise LLMError(provider="test", message="wrapped") from e
        assert exc_info.value.__cause__ is not None

    def test_memory_roundtrip_with_agent(self, tmp_path):
        p = tmp_path / "ltm.json"
        ltm = LongTermMemory(path=p)
        stm = ShortTermMemory(max_turns=5)
        stm.add("user", "my name is Alice")
        ltm.remember("user_name", "Alice")
        assert stm.to_history()[0]["content"] == "my name is Alice"
        assert ltm.recall("user_name") == "Alice"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
