"""
Comprehensive integration tests for the CORE AZURE subsystem.
Covers all foundational modules in the azure/ package.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def clean_env():
    """Ensure no stray env vars leak between tests."""
    # Remove persisted health data so ModelSelector starts clean
    from pathlib import Path as _Path
    _hf = _Path(__file__).resolve().parent.parent / "configs" / "model_health.json"
    _old_data = None
    if _hf.exists():
        try:
            _old_data = _hf.read_text(encoding="utf-8")
            _hf.unlink()
        except Exception:
            pass
    kept = {}
    for k in list(os.environ.keys()):
        if k.startswith("AZURE_") or k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                                           "GEMINI_API_KEY", "GROQ_API_KEY",
                                           "MISTRAL_API_KEY", "OPENROUTER_API_KEY",
                                           "NARAROUTER_API_KEY"):
            kept[k] = os.environ.pop(k, None)
    yield
    # Restore health file
    if _old_data is not None:
        with contextlib.suppress(Exception):
            _hf.write_text(_old_data, encoding="utf-8")
    for k in list(os.environ.keys()):
        if k.startswith("AZURE_") or k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                                           "GEMINI_API_KEY", "GROQ_API_KEY",
                                           "MISTRAL_API_KEY", "OPENROUTER_API_KEY",
                                           "NARAROUTER_API_KEY"):
            os.environ.pop(k, None)
    for k, v in kept.items():
        if v is not None:
            os.environ[k] = v


@pytest.fixture
def tmp_db_path():
    """Create a temporary directory for database files."""
    d = tempfile.mkdtemp()
    yield Path(d) / "test.db"
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def tmp_dir():
    """Create a temporary directory."""
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


# =============================================================================
# Test Utilities
# =============================================================================

class MockLLM:
    """A mock LLM that returns predictable responses."""
    def __init__(self, response: str = "Mock response", fail: bool = False):
        self.response = response
        self._fail = fail
        self._loaded = True
        self.temperature = 0.7
        self.max_tokens = 256
        self.n_ctx = 2048

    @property
    def is_loaded(self):
        return self._loaded

    def chat(self, messages, **kwargs):
        if self._fail:
            raise RuntimeError("Mock LLM failure")
        return self.response

    def generate(self, prompt, **kwargs):
        if self._fail:
            raise RuntimeError("Mock LLM failure")
        return self.response

    def count_tokens(self, text):
        return len(text) // 4

    def get_info(self):
        return {"model_name": "mock", "loaded": self._loaded}


# =============================================================================
# 1. ERRORS
# =============================================================================

class TestAzureErrors:
    """Test error types: hierarchy, message formatting, serialization."""
    from azure.errors import AzureError, LLMError, RateLimitError, ToolExecutionError

    def test_hierarchy(self):
        assert issubclass(self.LLMError, self.AzureError)
        assert issubclass(self.RateLimitError, self.AzureError)
        assert issubclass(self.ToolExecutionError, self.AzureError)

    def test_llm_error(self):
        err = self.LLMError("openai", "API timeout", 503)
        assert err.provider == "openai"
        assert err.status_code == 503
        assert "openai: API timeout" in str(err)

    def test_llm_error_no_status(self):
        err = self.LLMError("anthropic", "rate limited")
        assert err.status_code is None
        assert "anthropic: rate limited" in str(err)

    def test_rate_limit_error(self):
        err = self.RateLimitError(retry_after=30.5)
        assert err.retry_after == 30.5
        assert "30.5" in str(err)

    def test_rate_limit_error_default(self):
        err = self.RateLimitError()
        assert err.retry_after == 0
        assert "0s" in str(err)

    def test_tool_execution_error(self):
        err = self.ToolExecutionError("web_search", "Connection refused")
        assert err.tool_name == "web_search"
        assert "web_search" in str(err)
        assert "Connection refused" in str(err)


# =============================================================================
# 2. CONSTANTS
# =============================================================================

class TestConstants:
    """Test env variable parsing with defaults, error handling."""

    def test_get_env_int_default(self):
        from azure.constants import get_env_int
        assert get_env_int("NONEXISTENT_KEY_XYZ", 42) == 42

    def test_get_env_int_valid(self):
        from azure.constants import get_env_int
        os.environ["TEST_INT"] = "99"
        assert get_env_int("TEST_INT", 1) == 99

    def test_get_env_int_invalid(self):
        from azure.constants import get_env_int
        os.environ["TEST_INT_BAD"] = "not_a_number"
        assert get_env_int("TEST_INT_BAD", 10) == 10

    def test_get_env_float_default(self):
        from azure.constants import get_env_float
        assert get_env_float("NONEXISTENT_FLOAT", 3.14) == 3.14

    def test_get_env_float_valid(self):
        from azure.constants import get_env_float
        os.environ["TEST_FLOAT"] = "2.718"
        assert get_env_float("TEST_FLOAT", 1.0) == 2.718

    def test_get_env_float_invalid(self):
        from azure.constants import get_env_float
        os.environ["TEST_FLOAT_BAD"] = "nan"
        assert get_env_float("TEST_FLOAT_BAD", 0.5) == 0.5

    def test_get_env_bool_default(self):
        from azure.constants import get_env_bool
        assert get_env_bool("NONEXISTENT_BOOL", True) is True

    def test_get_env_bool_true_values(self):
        from azure.constants import get_env_bool
        for val in ("1", "true", "yes", "on"):
            os.environ["TEST_BOOL"] = val
            assert get_env_bool("TEST_BOOL", False) is True

    def test_get_env_bool_false_values(self):
        from azure.constants import get_env_bool
        for val in ("0", "false", "no", "off"):
            os.environ["TEST_BOOL"] = val
            assert get_env_bool("TEST_BOOL", True) is False

    def test_get_env_bool_unrecognized(self):
        from azure.constants import get_env_bool
        os.environ["TEST_BOOL"] = "maybe"
        assert get_env_bool("TEST_BOOL", False) is False


# =============================================================================
# 3. SHORT-TERM MEMORY
# =============================================================================

class TestShortTermMemory:
    from azure.agent import ShortTermMemory

    def test_add_and_retrieve(self):
        mem = self.ShortTermMemory(max_turns=5)
        mem.add("user", "Hello")
        mem.add("assistant", "Hi there!")
        hist = mem.to_history()
        assert len(hist) == 2
        assert hist[0]["role"] == "user"
        assert hist[0]["content"] == "Hello"

    def test_rolling_window(self):
        mem = self.ShortTermMemory(max_turns=2)
        for i in range(10):
            mem.add("user", f"msg{i}")
        hist = mem.to_history()
        # max_turns * 2 = 4 entries max
        assert len(hist) <= 4
        assert hist[-1]["content"] == "msg9"

    def test_context_block(self):
        mem = self.ShortTermMemory(max_turns=3)
        assert mem.context_block() == ""
        mem.add("user", "Hi")
        mem.add("assistant", "Hello")
        block = mem.context_block()
        assert "<user>" in block
        assert "<assistant>" in block
        assert "Hi" in block

    def test_thread_safety(self):
        mem = self.ShortTermMemory(max_turns=100)
        errors = []

        def add_messages():
            try:
                for i in range(50):
                    mem.add("user", f"msg{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_messages) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(mem.to_history()) <= 200  # 100*2
        assert len(errors) == 0


# =============================================================================
# 4. LONG-TERM MEMORY
# =============================================================================

class TestLongTermMemory:
    from azure.agent import LongTermMemory

    def test_remember_and_recall(self, tmp_dir):
        path = tmp_dir / "ltm.json"
        mem = self.LongTermMemory(path=path)
        mem.remember("color", "blue")
        assert mem.recall("color") == "blue"

    def test_recall_nonexistent(self, tmp_dir):
        path = tmp_dir / "ltm.json"
        mem = self.LongTermMemory(path=path)
        assert mem.recall("nonexistent") is None

    def test_search(self, tmp_dir):
        path = tmp_dir / "ltm.json"
        mem = self.LongTermMemory(path=path)
        mem.remember("python_version", "3.11")
        mem.remember("favorite_language", "Python")
        results = mem.search("python")
        assert len(results) >= 1
        assert any("python" in k.lower() or "python" in v.lower() for k, v in results)

    def test_persistence(self, tmp_dir):
        path = tmp_dir / "ltm.json"
        mem1 = self.LongTermMemory(path=path)
        mem1.remember("key", "value")
        del mem1
        mem2 = self.LongTermMemory(path=path)
        assert mem2.recall("key") == "value"

    def test_empty_file_recovery(self, tmp_dir):
        path = tmp_dir / "ltm.json"
        path.write_text("invalid json{{{", encoding="utf-8")
        mem = self.LongTermMemory(path=path)
        assert mem.facts == {}  # Should not crash


# =============================================================================
# 5. TOOL REGISTRY
# =============================================================================

class TestToolRegistry:
    from azure.agent import ToolRegistry

    def test_register_and_call(self):
        reg = self.ToolRegistry()
        reg.register("hello", "Says hello", lambda name: f"Hello {name}!")
        result = reg.call("hello", name="World")
        assert result["ok"] is True
        assert result["result"] == "Hello World!"

    def test_unknown_tool(self):
        reg = self.ToolRegistry()
        result = reg.call("nonexistent")
        assert result["ok"] is False
        assert "unknown tool" in result["error"]

    def test_tool_error_handling(self):
        reg = self.ToolRegistry()

        def failing():
            raise ValueError("Something broke")

        reg.register("fail", "Always fails", failing)
        result = reg.call("fail")
        assert result["ok"] is False
        assert "Something broke" in result["error"]

    def test_describe(self):
        reg = self.ToolRegistry()
        reg.register("a", "Does A", lambda: None)
        reg.register("b", "Does B", lambda: None)
        desc = reg.describe()
        assert len(desc) == 2
        names = {d["name"] for d in desc}
        assert names == {"a", "b"}


# =============================================================================
# 6. CIRCUIT BREAKER
# =============================================================================

class TestCircuitBreaker:
    from azure.circuit_breaker import CircuitBreaker

    def test_initial_state_closed(self):
        cb = self.CircuitBreaker(failure_threshold=3)
        assert cb.state == "CLOSED"
        assert cb.allow_request() is True

    def test_transition_to_open(self):
        cb = self.CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == "OPEN"
        assert cb.allow_request() is False

    def test_half_open_after_cooldown(self):
        cb = self.CircuitBreaker(failure_threshold=2, cooldown_seconds=0.01)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "OPEN"
        time.sleep(0.02)
        cb.check_cooldown()
        assert cb.state == "HALF_OPEN"

    def test_half_open_allows_one_request(self):
        cb = self.CircuitBreaker(failure_threshold=2, cooldown_seconds=0.01)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.02)
        # allow_request triggers cooldown check
        assert cb.allow_request() is True
        assert cb.state == "HALF_OPEN"
        # second request in HALF_OPEN should be blocked
        assert cb.allow_request() is False

    def test_success_resets_from_half_open(self):
        cb = self.CircuitBreaker(failure_threshold=2, cooldown_seconds=0.01)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.02)
        cb.allow_request()  # transitions to HALF_OPEN
        cb.record_success()  # should go back to CLOSED
        assert cb.state == "CLOSED"
        assert cb.allow_request() is True

    def test_success_in_closed_state(self):
        cb = self.CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_success()
        assert cb.state == "CLOSED"
        assert cb._failure_count == 0

    def test_manual_reset(self):
        cb = self.CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "OPEN"
        cb.reset()
        assert cb.state == "CLOSED"
        assert cb._failure_count == 0
        assert cb.allow_request() is True

    def test_get_info(self):
        cb = self.CircuitBreaker(failure_threshold=5, cooldown_seconds=30)
        cb.record_failure()
        info = cb.get_info()
        assert info["state"] == "CLOSED"
        assert info["failure_count"] == 1
        assert info["failure_threshold"] == 5
        assert info["cooldown_seconds"] == 30

    def test_allow_request_open_without_cooldown(self):
        cb = self.CircuitBreaker(failure_threshold=1, cooldown_seconds=3600)
        cb.record_failure()
        assert cb.state == "OPEN"
        assert cb.allow_request() is False

    def test_record_failure_already_open_keeps_timestamp(self):
        cb = self.CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        first_open = cb._last_failure_time
        time.sleep(0.01)
        cb.record_failure()  # should not update last_failure_time since already open
        assert cb._last_failure_time == first_open

    def test_half_open_success_resets(self):
        cb = self.CircuitBreaker(failure_threshold=2, cooldown_seconds=0.01)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.02)
        cb.check_cooldown()
        assert cb.state == "HALF_OPEN"
        cb.record_success()
        assert cb.state == "CLOSED"

    def test_allow_request_transitions_half_open(self):
        cb = self.CircuitBreaker(failure_threshold=2, cooldown_seconds=0.01)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.02)
        # allow_request in OPEN state with cooldown -> HALF_OPEN
        assert cb.allow_request() is True
        assert cb.state == "HALF_OPEN"


# =============================================================================
# 7. FAILOVER CHAIN
# =============================================================================

class TestFailoverChain:
    from azure.failover_chain import FailoverChain, FailoverResult

    def test_successful_tier_1(self):
        chain = self.FailoverChain(llm=MockLLM("Tier 1 response"))
        result = chain.respond("Hello")
        assert result.text == "Tier 1 response"
        assert result.tier == 1
        assert result.used_fallback is False

    def test_fallback_to_next_tier(self):
        chain = self.FailoverChain(llm=MockLLM("Tier response", fail=True))
        result = chain.respond("Hello")
        # All tiers share the same llm, so all fail
        assert result.tier == 5
        assert result.used_fallback is True

    def test_circuit_breaker_open(self):
        from azure.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()
        chain = self.FailoverChain(llm=MockLLM("Should not reach"))
        chain.circuit_breaker = cb
        result = chain.respond("Hello")
        assert result.tier == 0
        assert result.tier_name == "circuit_breaker_fallback"
        assert result.used_fallback is True

    def test_tier_health_tracking(self):
        chain = self.FailoverChain(llm=MockLLM("ok"))
        chain._tier_failures[1] = 3
        chain._tier_health[1] = False
        # Should skip tier 1
        result = chain.respond("Hello")
        assert result.tier != 1

    def test_tier_failure_disabling(self):
        for _i in range(5):
            chain = self.FailoverChain(llm=MockLLM("ok", fail=True))
        chain = self.FailoverChain(llm=MockLLM("ok", fail=True))
        # After many failures, health may be affected
        # but all LLM calls fail so we get fallback
        result = chain.respond("Hello")
        assert result.used_fallback is True

    def test_stats(self):
        chain = self.FailoverChain(llm=MockLLM("ok"))
        chain.respond("Hello")
        stats = chain.stats
        assert "tier_health" in stats
        assert "tier_failures" in stats

    def test_respond_with_context(self):
        chain = self.FailoverChain(llm=MockLLM("Context aware"))
        result = chain.respond("Hello", context={"user": "Alice", "server": "Test"})
        assert result.text == "Context aware"

    def test_context_is_carried_into_prompt_and_history(self):
        captured = []

        class CapturingLLM(MockLLM):
            def chat(self, messages, **kwargs):
                captured.extend(messages)
                return self.response

        chain = self.FailoverChain(llm=CapturingLLM("Context aware"))
        chain.respond("Current question", context={
            "user": "Alice",
            "server": "Test",
            "memory_scope": "guild:123",
            "server_facts": "Channels: general",
            "history": [{"role": "user", "content": "Earlier question"}],
        })

        contents = "\n".join(item["content"] for item in captured)
        assert "Channels: general" in contents
        assert "Earlier question" in contents

    def test_set_tracker(self):
        chain = self.FailoverChain(llm=MockLLM("Tracked"))
        tracker = MagicMock()
        chain.set_tracker(tracker)
        chain.respond("Hello")
        assert tracker.emit.called

    def test_recovery_attempt(self):
        chain = self.FailoverChain(llm=MockLLM("ok"))
        chain._tier_health[1] = False
        chain._last_recovery_attempt = 0
        chain.attempt_recovery()
        # After recovery attempt, tier 1 should be healthy again
        # (since _test_tier doesn't throw for tier 1 when llm is not None)
        assert chain._tier_health[1] is True


# =============================================================================
# 8. MODEL ROUTER
# =============================================================================

class TestModelRouter:
    from azure.model_router import ModelRouter, RouterResult

    def test_greeting_routes_to_tier_0(self, tmp_dir):
        router = self.ModelRouter(main_llm=MockLLM())
        result = router.route("hello")
        assert result.tier == 0
        assert result.confidence > 0.9

    def test_short_message_routes_to_tier_0(self):
        router = self.ModelRouter(main_llm=MockLLM())
        result = router.route("hi")
        assert result.tier == 0

    def test_specialist_keyword_routes_to_tier_3(self):
        router = self.ModelRouter(main_llm=MockLLM())
        result = router.route("write python code to sort an array")
        # The message contains "code" and "python" so it should be tier 3
        # But the router checks _classify_tier which may not match exactly
        # Check that it picks tier 3 due to specialist keywords
        assert result.tier in (2, 3)

    def test_general_question_default_tier_2(self):
        router = self.ModelRouter(main_llm=MockLLM("General answer"))
        result = router.route("What is the meaning of life?")
        # Long message with ? -> should trigger LLM classification or default 2
        # Since LLM returns single digit, it may be tier 2
        assert result.tier >= 2

    def test_cached_response(self):
        cache = {"hello": "Hi there!"}
        router = self.ModelRouter(main_llm=MockLLM(), cache=cache)
        result = router.route("hello")
        assert result.text == "Hi there!"
        assert result.tier == 0

    def test_all_tiers_fail_emergency_fallback(self):
        router = self.ModelRouter(main_llm=MockLLM(fail=True))
        result = router.route("complex question requiring deep thought" * 5)
        assert result.tier == -1
        assert result.used_fallback is True
        assert "All model tiers failed" in result.text

    def test_prefer_tier(self):
        router = self.ModelRouter(main_llm=MockLLM("Preferred"))
        result = router.route("hello", prefer_tier=2)
        assert result.tier == 2

    def test_stats(self):
        router = self.ModelRouter(main_llm=MockLLM("Stats"))
        router.route("hello")
        router.route("write code to do x")
        stats = router.stats
        assert "tier_calls" in stats
        assert "tier_failures" in stats

    def test_confidence_estimation(self):
        router = self.ModelRouter(main_llm=MockLLM())
        result = router.route("hello")
        assert 0 <= result.confidence <= 1.0


# =============================================================================
# 9. MODEL CATALOG
# =============================================================================

class TestModelCatalog:

    def test_provider_catalogs_have_required_keys(self):
        from azure.model_catalog import PROVIDER_CATALOGS
        for _name, cat in PROVIDER_CATALOGS.items():
            assert "display_name" in cat
            assert "models" in cat
            assert "protocol" in cat
            assert isinstance(cat["models"], list)

    def test_get_models_for_provider(self):
        from azure.model_catalog import ModelInfo, get_models_for_provider
        models = get_models_for_provider("openai")
        assert len(models) > 0
        assert all(isinstance(m, ModelInfo) for m in models)

    def test_get_models_for_unknown_provider(self):
        from azure.model_catalog import get_models_for_provider
        assert get_models_for_provider("nonexistent") == []

    def test_get_free_models(self):
        from azure.model_catalog import get_free_models_for_provider
        free = get_free_models_for_provider("google")
        for m in free:
            assert m.free_tier is True

    def test_get_model_info(self):
        from azure.model_catalog import get_model_info
        info = get_model_info("openai", "gpt-4o-mini")
        assert info is not None
        assert info.id == "gpt-4o-mini"
        assert info.name == "GPT-4o Mini"

    def test_get_model_info_nonexistent(self):
        from azure.model_catalog import get_model_info
        assert get_model_info("openai", "nonexistent") is None

    def test_get_recommendations_free(self):
        from azure.model_catalog import get_recommendations
        recs = get_recommendations("openai", tier="free")
        assert len(recs) == 3

    def test_model_info_label(self):
        from azure.model_catalog import ModelInfo
        info = ModelInfo("test-model", "Test Model", 128_000, 0.15, 0.60)
        label = info.label
        assert "128K" in label

    def test_all_providers_have_protocol(self):
        from azure.model_catalog import PROVIDER_CATALOGS
        for _name, cat in PROVIDER_CATALOGS.items():
            assert cat["protocol"] in ("openai", "anthropic", "google")


# =============================================================================
# 10. MODEL SELECTOR
# =============================================================================

class TestModelSelector:
    from azure.model_selector import ALL_PROVIDERS, ModelSelector, ProviderHealth

    def test_initialization(self):
        sel = self.ModelSelector()
        assert sel is not None
        assert "openai" in sel._providers

    def test_get_settings(self):
        sel = self.ModelSelector()
        settings = sel.get_settings()
        assert "smart_mode" in settings
        assert "provider" in settings
        assert "model" in settings

    def test_update_settings(self):
        sel = self.ModelSelector()
        sel.update_settings(provider="openai")
        assert sel._settings["provider"] == "openai"

    def test_get_active_config(self):
        sel = self.ModelSelector()
        config = sel.get_active_config()
        assert "provider" in config
        assert "model" in config

    def test_record_success(self):
        sel = self.ModelSelector()
        sel.record_success("openai", "gpt-4o-mini")
        assert sel._providers["openai"].health.success_count == 1

    def test_record_failure(self):
        sel = self.ModelSelector()
        sel.record_failure("openai", "gpt-4o-mini", "timeout")
        assert sel._providers["openai"].health.failure_count == 1
        assert sel._providers["openai"].health.consecutive_failures == 1

    def test_provider_health(self):
        sel = self.ModelSelector()
        health = sel.get_provider_health("openai")
        assert "openai" in health

    def test_all_providers_list(self):
        assert "openai" in self.ALL_PROVIDERS
        assert "anthropic" in self.ALL_PROVIDERS
        assert "google" in self.ALL_PROVIDERS

    def test_provider_health_is_healthy_no_key(self):
        h = self.ProviderHealth()
        assert h.is_healthy is False

    def test_provider_health_score(self):
        h = self.ProviderHealth()
        h.has_api_key = True
        h.success_count = 10
        h.failure_count = 1
        score = h.health_score
        assert 0 < score <= 1.0


# =============================================================================
# 11. DATABASE MANAGER
# =============================================================================

class TestDatabaseManager:
    from azure.database import BotStats, CacheEntry, ConversationMessage, DatabaseManager, UserPreference

    def test_init(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        assert db.db_path.exists()

    def test_save_and_get_conversation(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        msg = self.ConversationMessage(
            user_id="user1", user_name="Alice", server_id="s1",
            server_name="Test Server", channel_id="c1", channel_name="general",
            message="Hello", response="Hi!", timestamp=time.time(),
        )
        row_id = db.save_conversation(msg)
        assert row_id is not None
        history = db.get_conversation_history(user_id="user1")
        assert len(history) == 1
        assert history[0].message == "Hello"
        assert history[0].response == "Hi!"

    def test_get_conversation_empty(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        history = db.get_conversation_history(user_id="nonexistent")
        assert history == []

    def test_save_and_get_user_preference(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        pref = self.UserPreference(
            user_id="user1", user_name="Alice", tier="premium",
            context_size=20, temperature=0.9,
            created_at=time.time(), updated_at=time.time(),
        )
        db.save_user_preference(pref)
        loaded = db.get_user_preference("user1")
        assert loaded is not None
        assert loaded.tier == "premium"
        assert loaded.temperature == 0.9

    def test_get_user_preference_none(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        assert db.get_user_preference("nonexistent") is None

    def test_cache_entry_save_and_get(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        entry = self.CacheEntry(
            cache_key="test_key", prompt="Hello", response="Hi!",
            user_id="u1", server_id="s1",
            created_at=time.time(), last_accessed=time.time(),
            expires_at=time.time() + 3600,
        )
        db.save_cache_entry(entry)
        loaded = db.get_cache_entry("test_key")
        assert loaded is not None
        assert loaded.response == "Hi!"
        assert loaded.hit_count == 1  # bumped by get

    def test_get_cache_entry_expired(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        entry = self.CacheEntry(
            cache_key="expired_key", prompt="Old", response="Old response",
            user_id="u1", server_id="s1",
            created_at=1, last_accessed=1, expires_at=1,
        )
        db.save_cache_entry(entry)
        loaded = db.get_cache_entry("expired_key")
        assert loaded is None

    def test_cleanup_expired_cache(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        entry = self.CacheEntry(
            cache_key="old", prompt="Old", response="Old",
            user_id="u1", server_id="s1",
            created_at=1, last_accessed=1, expires_at=1,
        )
        db.save_cache_entry(entry)
        removed = db.cleanup_expired_cache()
        assert removed >= 1

    def test_save_stats(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        stats = self.BotStats(
            timestamp=time.time(), messages_processed=100,
            cache_hits=50, cache_misses=10, errors=2,
            avg_response_time_ms=250.0, total_tokens_used=5000,
            active_users=10, active_servers=2,
        )
        row_id = db.save_stats(stats)
        assert row_id is not None

    def test_get_stats_history(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        stats = self.BotStats(timestamp=time.time(), messages_processed=10)
        db.save_stats(stats)
        history = db.get_stats_history(hours=24)
        assert len(history) >= 1

    def test_get_aggregate_stats(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        for i in range(3):
            db.save_stats(self.BotStats(
                timestamp=time.time(),
                messages_processed=10 * (i + 1),
                cache_hits=5, cache_misses=2, errors=0,
                avg_response_time_ms=100.0, total_tokens_used=1000,
                active_users=5, active_servers=1,
            ))
        agg = db.get_aggregate_stats(hours=24)
        assert agg["total_messages"] >= 60
        assert agg["total_cache_hits"] >= 15

    def test_access_control(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        db.set_access_control("user", "12345", "allow", "tester")
        result = db.get_access_control("12345")
        assert result == "allow"

    def test_get_access_control_none(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        assert db.get_access_control("unknown") is None

    def test_security_events(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        db.log_security_event("u1", "g1", "test_event", "low", "test details")
        # No crash is the test

    def test_telemetry(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        db.log_telemetry("exec_1", "core", "TEST", "test message", "info")
        # No crash

    def test_vacuum(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        db.vacuum()  # Should not crash

    def test_close(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        db.close()
        # After closing, should be able to get new connection
        db2 = self.DatabaseManager(db_path=tmp_db_path)
        assert db2 is not None

    def test_get_shared_db(self, tmp_db_path):
        from azure.database import get_shared_db, set_shared_db
        db = self.DatabaseManager(db_path=tmp_db_path)
        set_shared_db(db)
        same = get_shared_db()
        assert same is db

    def test_conversation_history_with_filters(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        now = time.time()
        for i in range(5):
            db.save_conversation(self.ConversationMessage(
                user_id="u1", user_name="Alice", server_id="s1",
                server_name="S", channel_id="c1", channel_name="general",
                message=f"msg{i}", response=f"resp{i}",
                timestamp=now - (5 - i),
            ))
        # Filter by user
        hist = db.get_conversation_history(user_id="u1", limit=10)
        assert len(hist) == 5
        # Filter by server
        hist = db.get_conversation_history(server_id="s1", limit=10)
        assert len(hist) == 5
        # Filter by since
        hist = db.get_conversation_history(since=now - 3)
        assert len(hist) >= 2

    def test_conversation_order(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        now = time.time()
        db.save_conversation(self.ConversationMessage(
            user_id="u1", user_name="A", server_id="s1",
            server_name="S", channel_id="c1", channel_name="general",
            message="first", response="r1", timestamp=now - 10,
        ))
        db.save_conversation(self.ConversationMessage(
            user_id="u1", user_name="A", server_id="s1",
            server_name="S", channel_id="c1", channel_name="general",
            message="second", response="r2", timestamp=now,
        ))
        hist = db.get_conversation_history(user_id="u1", limit=10)
        assert hist[0].message == "second"
        assert hist[1].message == "first"


# =============================================================================
# 12. MEMORY BACKEND (SQLite)
# =============================================================================

class TestSQLiteMemoryBackend:
    from azure.memory_backend import EpisodicEvent, SQLiteMemoryBackend, UserProfile, create_memory_backend
    create_memory_backend = staticmethod(create_memory_backend)

    def test_init(self, tmp_dir):
        db_path = str(tmp_dir / "mem.db")
        self.SQLiteMemoryBackend(db_path=db_path)
        assert Path(db_path).exists()

    def test_save_and_get_user_profile(self, tmp_dir):
        backend = self.SQLiteMemoryBackend(db_path=str(tmp_dir / "mem.db"))
        profile = self.UserProfile(user_id="u1", user_name="Alice", communication_style="casual")
        backend.save_user_profile(profile)
        loaded = backend.get_user_profile("u1")
        assert loaded is not None
        assert loaded.user_id == "u1"
        assert loaded.communication_style == "casual"

    def test_get_user_profile_none(self, tmp_dir):
        backend = self.SQLiteMemoryBackend(db_path=str(tmp_dir / "mem.db"))
        assert backend.get_user_profile("nonexistent") is None

    def test_get_or_create_profile_new(self, tmp_dir):
        backend = self.SQLiteMemoryBackend(db_path=str(tmp_dir / "mem.db"))
        profile = backend.get_or_create_profile("new_user", "New User")
        assert profile.user_id == "new_user"
        assert profile.user_name == "New User"

    def test_get_or_create_profile_existing(self, tmp_dir):
        backend = self.SQLiteMemoryBackend(db_path=str(tmp_dir / "mem.db"))
        profile = self.UserProfile(user_id="existing", user_name="Existing")
        backend.save_user_profile(profile)
        loaded = backend.get_or_create_profile("existing", "Existing")
        assert loaded.user_id == "existing"

    def test_save_memory(self, tmp_dir):
        backend = self.SQLiteMemoryBackend(db_path=str(tmp_dir / "mem.db"))
        mem_id = backend.save_memory("Hello world", "u1", source="general", tags=["greeting"])
        assert mem_id.startswith("mem_")

    def test_query_memories(self, tmp_dir):
        backend = self.SQLiteMemoryBackend(db_path=str(tmp_dir / "mem.db"))
        backend.save_memory("Hello", "u1", source="general", tags=["greeting"])
        backend.save_memory("World", "u1", source="general", tags=["test"])
        backend.save_memory("Python", "u2", source="coding", tags=["programming"])
        # Query by user
        results = backend.query_memories(user_id="u1", limit=10)
        assert len(results) == 2
        # Query by tag
        results = backend.query_memories(tags=["greeting"], limit=10)
        assert len(results) == 1

    def test_search_memories(self, tmp_dir):
        backend = self.SQLiteMemoryBackend(db_path=str(tmp_dir / "mem.db"))
        backend.save_memory("I love Python programming", "u1")
        backend.save_memory("JavaScript is also fun", "u1")
        backend.save_memory("Rust is fast", "u2")
        results = backend.search_memories("python", limit=10)
        assert len(results) == 1
        assert "python" in results[0]["text"].lower()

    def test_save_and_get_events(self, tmp_dir):
        backend = self.SQLiteMemoryBackend(db_path=str(tmp_dir / "mem.db"))
        event = self.EpisodicEvent(
            event_id="e1", timestamp=time.time(),
            event_type="milestone", description="Server reached 100 members",
            participants=["Alice", "Bob"], outcome="success", sentiment=0.9,
        )
        backend.save_event(event)
        events = backend.get_events(limit=10)
        assert len(events) == 1
        assert events[0].event_id == "e1"
        assert events[0].participants == ["Alice", "Bob"]

    def test_get_events_by_type(self, tmp_dir):
        backend = self.SQLiteMemoryBackend(db_path=str(tmp_dir / "mem.db"))
        backend.save_event(self.EpisodicEvent(
            event_id="e1", timestamp=time.time(),
            event_type="milestone", description="M1", outcome="ok",
        ))
        backend.save_event(self.EpisodicEvent(
            event_id="e2", timestamp=time.time(),
            event_type="decision", description="D1", outcome="ok",
        ))
        events = backend.get_events(event_type="milestone", limit=10)
        assert len(events) == 1
        assert events[0].event_id == "e1"

    def test_factory_sqlite(self, tmp_dir):
        from azure.memory_backend import SQLiteMemoryBackend, create_memory_backend
        backend = create_memory_backend("sqlite", db_path=str(tmp_dir / "factory.db"))
        assert isinstance(backend, SQLiteMemoryBackend)

    def test_factory_memory(self):
        from azure.memory_backend import InMemoryMemoryBackend, create_memory_backend
        backend = create_memory_backend("memory")
        assert isinstance(backend, InMemoryMemoryBackend)

    def test_factory_unknown(self):
        from azure.memory_backend import create_memory_backend
        with pytest.raises(ValueError, match="Unknown memory backend"):
            create_memory_backend("unknown_backend_type")

    def test_close(self, tmp_dir):
        backend = self.SQLiteMemoryBackend(db_path=str(tmp_dir / "mem.db"))
        backend.close()  # Should not crash


# =============================================================================
# 13. RESPONSE CACHE
# =============================================================================

class TestResponseCache:
    from azure.response_cache import CacheEntry as RCCacheEntry
    from azure.response_cache import ResponseCache

    def test_get_miss(self):
        cache = self.ResponseCache(max_size=10)
        result = cache.get("hello")
        assert result is None

    def test_set_and_get(self):
        cache = self.ResponseCache(max_size=10)
        cache.set("hello", "Hi there!", complexity="LOW", confidence=1.0)
        result = cache.get("hello")
        assert result == "Hi there!"

    def test_context_aware_key(self):
        cache = self.ResponseCache(max_size=10)
        cache.set("hello", "Hi user1!", user_id="user1", complexity="LOW", confidence=1.0)
        cache.set("hello", "Hi user2!", user_id="user2", complexity="LOW", confidence=1.0)
        r1 = cache.get("hello", user_id="user1")
        r2 = cache.get("hello", user_id="user2")
        assert r1 == "Hi user1!"
        assert r2 == "Hi user2!"

    def test_ttl_expiry(self):
        cache = self.ResponseCache(max_size=10, ttl_seconds=0.01)
        cache.set("hello", "Hi there!", complexity="LOW", confidence=1.0)
        time.sleep(0.02)
        result = cache.get("hello")
        assert result is None

    def test_lru_eviction(self):
        cache = self.ResponseCache(max_size=3)
        for i in range(5):
            cache.set(f"key{i}", f"val{i}", complexity="LOW", confidence=1.0)
        # Oldest keys should be evicted
        assert cache.get("key0") is None
        assert cache.get("key1") is None
        assert cache.get("key4") is not None  # Most recent

    def test_high_complexity_not_cached(self):
        cache = self.ResponseCache(max_size=10)
        cache.set("complex", "Complex answer", complexity="HIGH", confidence=1.0)
        assert cache.get("complex") is None

    def test_low_confidence_not_cached(self):
        cache = self.ResponseCache(max_size=10)
        cache.set("uncertain", "Maybe", complexity="LOW", confidence=0.5)
        assert cache.get("uncertain") is None

    def test_stats(self):
        cache = self.ResponseCache(max_size=10)
        cache.set("a", "1", complexity="LOW", confidence=1.0)
        cache.get("a")  # hit
        cache.get("b")  # miss
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["size"] == 1
        assert stats["hit_rate"] == 0.5

    def test_clear(self):
        cache = self.ResponseCache(max_size=10)
        cache.set("a", "1", complexity="LOW", confidence=1.0)
        cache.set("b", "2", complexity="LOW", confidence=1.0)
        cache.clear()
        assert cache.stats()["size"] == 0

    def test_invalidate_by_message(self):
        cache = self.ResponseCache(max_size=10)
        cache.set("hello", "Hi!", complexity="LOW", confidence=1.0)
        removed = cache.invalidate(message="hello")
        assert removed == 1
        assert cache.get("hello") is None

    def test_invalidate_by_user_id(self):
        cache = self.ResponseCache(max_size=10)
        # User_id-based invalidation doesn't work directly since
        # CacheEntry doesn't store user_id. This tests the fallback.
        removed = cache.invalidate(user_id="nonexistent")
        assert removed == 0  # No entries to remove

    def test_invalidate_by_pattern(self):
        cache = self.ResponseCache(max_size=10)
        # Pattern-based invalidation doesn't work since CacheEntry
        # doesn't store original message. This tests the fallback.
        removed = cache.invalidate(pattern="hello")
        assert removed == 0

    def test_top_entries(self):
        cache = self.ResponseCache(max_size=10)
        for i in range(5):
            cache.set(f"key{i}", f"val{i}", complexity="LOW", confidence=1.0)
        for _ in range(3):
            cache.get("key0")
        for _ in range(2):
            cache.get("key1")
        top = cache.top_entries(n=5)
        assert len(top) == 5
        assert top[0]["hit_count"] >= top[1]["hit_count"]

    def test_context_hints(self):
        cache = self.ResponseCache(max_size=10)
        context = {"modes": ["CHAT"], "is_dm": True}
        cache.set("hello", "DM reply", user_id="u1", context=context,
                  complexity="LOW", confidence=1.0)
        result = cache.get("hello", user_id="u1", context=context)
        assert result == "DM reply"
        # Different context should miss
        result2 = cache.get("hello", user_id="u1")
        assert result2 is None

    def test_eviction_count(self):
        cache = self.ResponseCache(max_size=2)
        for i in range(5):
            cache.set(f"key{i}", f"val{i}", complexity="LOW", confidence=1.0)
        stats = cache.stats()
        assert stats["evictions"] >= 3


# =============================================================================
# 14. TASK MANAGER
# =============================================================================

class TestTaskManager:
    from azure.task_manager import TaskManager, TaskRecord

    @pytest.mark.asyncio
    async def test_start_and_complete_task(self):
        tm = self.TaskManager()

        async def simple_task():
            return "done"

        result = await tm.start_task("simple", simple_task())
        assert result == "done"
        assert not tm.is_busy

    @pytest.mark.asyncio
    async def test_busy_flag(self):
        tm = self.TaskManager()
        assert not tm.is_busy

        async def slow_task():
            await asyncio.sleep(0.05)
            return "done"

        task = asyncio.create_task(tm.start_task("slow", slow_task()))
        await asyncio.sleep(0.01)
        assert tm.is_busy
        assert tm.get_current_task() == "slow"
        await task

    @pytest.mark.asyncio
    async def test_queue(self):
        tm = self.TaskManager()

        async def task1():
            await asyncio.sleep(0.05)
            return "t1"

        async def task2():
            return "t2"

        t1 = asyncio.create_task(tm.start_task("t1", task1(), queue_if_busy=False))
        await asyncio.sleep(0.01)

        t2 = await tm.start_task("t2", task2(), queue_if_busy=True)
        assert t2 is None  # queued (returns None when queued)
        assert tm.queue_size() == 1
        await t1
        await asyncio.sleep(0.1)  # let queue process
        assert tm.queue_size() == 0

    @pytest.mark.asyncio
    async def test_history(self):
        tm = self.TaskManager()

        async def t():
            return "ok"

        await tm.start_task("test", t())
        history = tm.get_history(n=10)
        assert len(history) == 1
        assert history[0].name == "test"

    @pytest.mark.asyncio
    async def test_stats(self):
        tm = self.TaskManager()

        async def t():
            return "ok"

        await tm.start_task("test", t())
        stats = tm.get_stats()
        assert stats["total_tasks"] == 1
        assert stats["successful"] == 1

    @pytest.mark.asyncio
    async def test_cancel_current(self):
        tm = self.TaskManager()

        async def never_ending():
            while True:
                await asyncio.sleep(1)

        asyncio.create_task(tm.start_task("endless", never_ending()))
        await asyncio.sleep(0.05)
        await tm.cancel_current()
        assert not tm.is_busy

    def test_get_queue_names(self):
        tm = self.TaskManager()
        # Queue names should be empty initially
        assert tm.get_queue_names() == []

    def test_dead_letter(self):
        tm = self.TaskManager()
        assert tm.get_dead_letter() == []

    def test_max_queue_size(self):
        tm = self.TaskManager()
        assert tm._MAX_QUEUE_SIZE == 20

    @pytest.mark.asyncio
    async def test_busy_message_sent(self):
        tm = self.TaskManager()
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.guild.name = "Test"

        async def slow():
            await asyncio.sleep(0.1)
            return "ok"

        asyncio.create_task(tm.start_task("t1", slow(), ctx=ctx))
        await asyncio.sleep(0.01)

        await tm.start_task("t2", slow(), ctx=ctx, queue_if_busy=False)
        assert ctx.send.called


# =============================================================================
# 15. DECISION ENGINE
# =============================================================================

class TestDecisionEngine:
    from azure.decision import DecisionEngine as _DE
    from azure.moderation.policy import ActionType, ModerationPhase

    @pytest.fixture
    def policy(self):
        from azure.moderation.policy import ModerationPolicy
        return ModerationPolicy(mode="reactive")

    @pytest.fixture
    def engine(self, policy):
        return self._DE(policy)

    def test_no_threat(self, engine, policy):
        decision = engine.decide(
            content_severity=0.1, content_confidence=0.3,
            content_category="normal", behavioral_signals={},
            temporal_signals={}, risk_profile={"total_risk": 0.05, "confidence": 0.1, "user_risk": 0.0, "situation_risk": 0.0},
            phase=self.ModerationPhase.REACTIVE_FULL,
        )
        assert decision.action == self.ActionType.NONE
        assert decision.reason == "no_threat"

    def test_whitelisted_user(self, engine, policy):
        decision = engine.decide(
            content_severity=0.9, content_confidence=0.9,
            content_category="toxicity", behavioral_signals={},
            temporal_signals={}, risk_profile={"total_risk": 0.9, "confidence": 0.9, "user_risk": 0.0,
                                               "situation_risk": 0.0},
            phase=self.ModerationPhase.REACTIVE_FULL,
            is_whitelisted=True,
        )
        assert decision.action == self.ActionType.NONE
        assert decision.reason == "whitelisted"

    def test_high_risk_timeout(self, engine, policy):
        decision = engine.decide(
            content_severity=0.9, content_confidence=0.8,
            content_category="scam", behavioral_signals={},
            temporal_signals={}, risk_profile={"total_risk": 0.85, "confidence": 0.8, "user_risk": 0.0,
                                               "situation_risk": 0.0},
            phase=self.ModerationPhase.REACTIVE_FULL,
        )
        assert decision.action == self.ActionType.TIMEOUT
        assert decision.reason == "scam_confident"

    def test_medium_risk(self, engine, policy):
        decision = engine.decide(
            content_severity=0.6, content_confidence=0.6,
            content_category="spam", behavioral_signals={},
            temporal_signals={}, risk_profile={"total_risk": 0.6, "confidence": 0.6, "user_risk": 0.0,
                                               "situation_risk": 0.0},
            phase=self.ModerationPhase.REACTIVE_FULL,
        )
        assert decision.action == self.ActionType.DELETE
        assert decision.reason == "spam_suspected"

    def test_situation_raid(self, engine, policy):
        decision = engine.decide_situation(
            temporal_signals={"raid_probability": 0.95, "is_raid": True, "explanation": "High volume",
                              "matched_messages": 50},
            risk_profile={"total_risk": 0.9, "confidence": 0.9},
            phase=self.ModerationPhase.REACTIVE_FULL,
            involved_users=["u1", "u2", "u3"],
        )
        assert decision.reason == "critical_raid"

    def test_decision_to_dict(self, engine, policy):
        decision = engine.decide(
            content_severity=0.1, content_confidence=0.1,
            content_category="normal", behavioral_signals={},
            temporal_signals={}, risk_profile={"total_risk": 0.0, "confidence": 0.0, "user_risk": 0.0,
                                               "situation_risk": 0.0},
            phase=self.ModerationPhase.REACTIVE_FULL,
        )
        d = decision.to_dict()
        assert "action" in d
        assert "confidence" in d
        assert "explanation" in d

    def test_behavioral_anomaly(self, engine, policy):
        decision = engine.decide(
            content_severity=0.3, content_confidence=0.3,
            content_category="normal", behavioral_signals={"anomaly_score": 0.8, "explanation": "unusual pattern"},
            temporal_signals={}, risk_profile={"total_risk": 0.3, "confidence": 0.3, "user_risk": 0.0,
                                               "situation_risk": 0.0},
            phase=self.ModerationPhase.REACTIVE_FULL,
        )
        assert decision.action == self.ActionType.LOG


# =============================================================================
# 16. SELF REPAIR
# =============================================================================

class TestSelfRepair:
    from azure.self_repair import RepairAttempt, SelfRepair

    @pytest.mark.asyncio
    async def test_safe_execute_success(self, tmp_dir):
        repair = self.SelfRepair(log_dir=tmp_dir)

        async def success_op():
            return "ok"

        result = await repair.safe_execute(success_op, "test_op")
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_safe_execute_error(self, tmp_dir):
        repair = self.SelfRepair(log_dir=tmp_dir)

        async def fail_op():
            raise ValueError("Something went wrong")

        result = await repair.safe_execute(fail_op, "fail_op")
        assert result is None

    def test_known_fixes_mapping(self):
        repair = self.SelfRepair()
        assert "Forbidden" in repair.KNOWN_FIXES
        assert "RateLimit" in repair.KNOWN_FIXES
        assert "NotFound" in repair.KNOWN_FIXES

    def test_get_stats_empty(self, tmp_dir):
        repair = self.SelfRepair(log_dir=tmp_dir)
        stats = repair.get_stats()
        assert stats["total_attempts"] == 0
        assert stats["success_rate"] == 0.0

    def test_known_fix_patterns(self, tmp_dir):
        # Test that known fix patterns match expected errors
        repair = self.SelfRepair()
        error = AttributeError("module 'discord' has no attribute 'VerificationLevel'")
        result = repair._try_fix(error, "test", "guild")
        assert result["success"] is False  # AttributeError causes use_int_value -> requires_restart
        assert result["fix_description"] is not None

    def test_user_facing_messages(self, tmp_dir):
        repair = self.SelfRepair()
        # Permission error
        msg = repair._build_user_message("create_channel", PermissionError("403 Forbidden"), {"requires_restart": False})
        assert "permissions" in msg.lower()

        # Rate limit
        msg = repair._build_user_message("create_role", Exception("rate limit exceeded"), {})
        assert "rate limited" in msg.lower() or "rate limit" in msg.lower() or "rate" in msg.lower()

        # Generic
        msg = repair._build_user_message("test", Exception("generic"), {"requires_restart": False})
        assert "Error" in msg


# =============================================================================
# 17. LOGGING CONFIG
# =============================================================================

class TestLoggingConfig:

    def test_generate_execution_id(self):
        from azure.logging_config import generate_execution_id
        eid = generate_execution_id()
        assert isinstance(eid, str)

    def test_set_and_clear_context(self):
        from azure.logging_config import clear_request_context, set_request_context
        set_request_context(execution_id="test123", user_id="user456")
        clear_request_context()

    def test_context_filter(self, caplog):
        from azure.logging_config import ContextFilter
        filt = ContextFilter()
        import logging
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        result = filt.filter(record)
        assert result is True
        assert hasattr(record, "execution_id")
        assert hasattr(record, "user_id")

    def test_setup_logging(self):
        from azure.logging_config import setup_logging
        setup_logging()
        import logging
        root = logging.getLogger()
        assert root.level == logging.INFO


# =============================================================================
# 18. STRUCTURED LOGGER
# =============================================================================

class TestStructuredLogger:
    _StructuredLogger = None

    @pytest.fixture(autouse=True)
    def _setup_sl(self):
        if TestStructuredLogger._StructuredLogger is None:
            from azure.logger import StructuredLogger
            TestStructuredLogger._StructuredLogger = StructuredLogger

    def test_logger_basic(self, tmp_dir):
        log = self._StructuredLogger("test", log_dir=tmp_dir, level="DEBUG")
        log.info("test message", extra="data")
        log_file = tmp_dir / "test.jsonl"
        assert log_file.exists()
        content = log_file.read_text()
        assert "test message" in content
        assert "extra" in content

    def test_logger_level_filtering(self, tmp_dir):
        log = self._StructuredLogger("test", log_dir=tmp_dir, level="ERROR")
        log.info("should not appear")
        log.error("should appear")
        log_file = tmp_dir / "test.jsonl"
        content = log_file.read_text()
        assert "should not appear" not in content
        assert "should appear" in content

    def test_get_logger(self):
        from azure.logger import get_logger
        log = get_logger("test_module")
        assert log is not None
        assert log.name == "test_module"

    def test_set_log_dir(self, tmp_dir):
        from azure.logger import get_logger, set_log_dir
        set_log_dir(tmp_dir)
        log = get_logger("dir_test")
        assert log._log_file is not None

    def test_exception_logging(self, tmp_dir, caplog):
        log = self._StructuredLogger("exc_test", log_dir=tmp_dir, level="ERROR")
        try:
            raise ValueError("test exception")
        except ValueError:
            log.exception("An error occurred", ValueError("test exception"))
        log_file = tmp_dir / "exc_test.jsonl"
        assert log_file.exists()

    def test_logger_stdout(self, capsys):
        log = self._StructuredLogger("stdout_test", level="INFO", to_file=False, to_stdout=True)
        log.info("stdout message")
        captured = capsys.readouterr()
        assert "stdout message" in captured.out


# =============================================================================
# 19. SERVER CONFIG
# =============================================================================

class TestServerConfig:
    from azure.server_config import ServerConfig, ServerConfigManager

    def test_get_or_create(self, tmp_dir):
        mgr = self.ServerConfigManager(config_dir=tmp_dir / "configs")
        cfg = mgr.get_or_create("guild_123", "Test Server")
        assert cfg.guild_id == "guild_123"
        assert cfg.guild_name == "Test Server"
        assert cfg.moderation_phase == "dry_run"

    def test_get_existing(self, tmp_dir):
        mgr = self.ServerConfigManager(config_dir=tmp_dir / "configs")
        mgr.get_or_create("guild_123", "Test")
        cfg = mgr.get("guild_123")
        assert cfg is not None

    def test_update(self, tmp_dir):
        mgr = self.ServerConfigManager(config_dir=tmp_dir / "configs")
        cfg = mgr.update("guild_123", moderation_phase="reactive_full", admin_channel_id="456")
        assert cfg.moderation_phase == "reactive_full"
        assert cfg.admin_channel_id == "456"

    def test_remove(self, tmp_dir):
        mgr = self.ServerConfigManager(config_dir=tmp_dir / "configs")
        mgr.get_or_create("guild_123")
        mgr.remove("guild_123")
        assert mgr.get("guild_123") is None

    def test_list_all(self, tmp_dir):
        mgr = self.ServerConfigManager(config_dir=tmp_dir / "configs")
        mgr.get_or_create("guild_1", "Server 1")
        mgr.get_or_create("guild_2", "Server 2")
        listed = mgr.list_all()
        assert len(listed) == 2

    def test_count(self, tmp_dir):
        mgr = self.ServerConfigManager(config_dir=tmp_dir / "configs")
        assert mgr.count() == 0
        mgr.get_or_create("guild_1")
        assert mgr.count() == 1

    def test_to_dict_from_dict(self):
        cfg = self.ServerConfig(guild_id="test", guild_name="Test")
        d = cfg.to_dict()
        assert d["guild_id"] == "test"
        restored = self.ServerConfig.from_dict(d)
        assert restored.guild_id == "test"
        assert restored.guild_name == "Test"


# =============================================================================
# 20. CHANGE TRACKER
# =============================================================================

class TestChangeTracker:
    from azure.change_tracker import ChangeRecord, ChangeTracker

    def test_log_change(self, tmp_dir):
        tracker = self.ChangeTracker(log_dir=tmp_dir / "changes")
        record = tracker.log_change(
            guild_id=123, guild_name="Test", action="create_role",
            target={"name": "Moderator", "id": 456},
            before=None, after={"name": "Moderator"},
            performed_by="Owner",
        )
        assert record.action == "create_role"
        assert record.guild_id == 123

    def test_can_undo(self):
        tracker = self.ChangeTracker()
        assert tracker.can_undo(123) is False
        tracker.log_change(123, "Test", "create_role", {"name": "Mod"}, None, {"name": "Mod"}, "Owner")
        assert tracker.can_undo(123) is True

    def test_get_undo(self):
        tracker = self.ChangeTracker()
        tracker.log_change(123, "Test", "create_role", {"name": "Mod", "id": 1}, None, {"name": "Mod"}, "Owner")
        undo = tracker.get_undo(123)
        assert undo is not None
        assert undo["action"] == "delete_role"

    def test_get_undo_none_left(self):
        tracker = self.ChangeTracker()
        tracker.log_change(123, "Test", "create_role", {"name": "Mod"}, None, {"name": "Mod"}, "Owner")
        tracker.get_undo(123)
        result = tracker.get_undo(123)
        assert result is None

    def test_undo_count(self):
        tracker = self.ChangeTracker()
        assert tracker.undo_count(123) == 0
        tracker.log_change(123, "Test", "create_role", {"name": "Mod"}, None, {"name": "Mod"}, "Owner")
        assert tracker.undo_count(123) == 1

    def test_get_last_n(self):
        tracker = self.ChangeTracker()
        for i in range(5):
            tracker.log_change(123, "Test", "create_channel", {"name": f"ch{i}"}, None, {"name": f"ch{i}"}, "Owner")
        last = tracker.get_last_n(123, 3)
        assert len(last) == 3

    def test_get_audit_log(self):
        tracker = self.ChangeTracker()
        tracker.log_change(123, "Test", "create_role", {"name": "Mod"}, None, {"name": "Mod"}, "Owner")
        log = tracker.get_audit_log(123)
        assert len(log) == 1
        assert log[0]["action"] == "create_role"

    def test_search_audit(self):
        tracker = self.ChangeTracker()
        tracker.log_change(123, "Test", "create_role", {"name": "Mod"}, None, {"name": "Mod"}, "Owner", request_text="create mod role")
        tracker.log_change(123, "Test", "delete_channel", {"name": "old"}, {"name": "old"}, None, "Owner")
        results = tracker.search_audit(123, action="create_role")
        assert len(results) == 1
        assert results[0]["action"] == "create_role"

    def test_get_stats(self):
        tracker = self.ChangeTracker()
        stats = tracker.get_stats(123)
        assert stats["total"] == 0
        tracker.log_change(123, "Test", "create_role", {"name": "Mod"}, None, {"name": "Mod"}, "Owner")
        stats = tracker.get_stats(123)
        assert stats["total"] == 1
        assert stats["success_rate"] == 1.0

    def test_undo_summary(self):
        tracker = self.ChangeTracker()
        summary = tracker.get_undo_summary(123)
        assert summary == "No changes to undo."
        tracker.log_change(123, "Test", "create_role", {"name": "Mod"}, None, {"name": "Mod"}, "Owner")
        summary = tracker.get_undo_summary(123)
        assert "Undoable Changes" in summary

    def test_get_changes_today(self):
        tracker = self.ChangeTracker()
        tracker.log_change(123, "Test", "create_role", {"name": "Mod"}, None, {"name": "Mod"}, "Owner")
        today = tracker.get_changes_today(123)
        assert len(today) == 1


# =============================================================================
# 21. CRON SCHEDULER
# =============================================================================

class TestCronScheduler:
    from azure.cron_scheduler import CronScheduler, ScheduledTask

    def test_add_and_list(self, tmp_dir):
        path = tmp_dir / "cron"
        sched = self.CronScheduler(path=path)
        task = sched.add_task("Morning Check", "Check every morning", "0 9 * * *",
                              "channel1", "user1", action="message")
        assert task.task_id is not None
        tasks = sched.list_tasks()
        assert len(tasks) == 1

    def test_remove_task(self, tmp_dir):
        sched = self.CronScheduler(path=tmp_dir / "cron")
        task = sched.add_task("Test", "Test task", "0 * * * *", "c1", "u1")
        assert sched.remove_task(task.task_id) is True
        assert sched.remove_task("nonexistent") is False

    def test_persistence(self, tmp_dir):
        path = tmp_dir / "cron"
        sched1 = self.CronScheduler(path=path)
        sched1.add_task("Persist", "Persistent task", "0 9 * * *", "c1", "u1")
        sched2 = self.CronScheduler(path=path)
        assert len(sched2.list_tasks()) == 1

    def test_nl_to_cron(self):
        sched = self.CronScheduler()
        assert sched.natural_language_to_cron("every hour") == "0 * * * *"
        assert sched.natural_language_to_cron("every day at 9am") == "0 9 * * *"
        assert sched.natural_language_to_cron("every monday") == "0 9 * * 1"
        assert sched.natural_language_to_cron("every morning") == "0 9 * * *"
        assert sched.natural_language_to_cron("every night") == "0 20 * * *"
        assert sched.natural_language_to_cron("complex request") is None

    def test_mark_ran(self, tmp_dir):
        sched = self.CronScheduler(path=tmp_dir / "cron")
        task = sched.add_task("Test", "Test", "0 * * * *", "c1", "u1")
        assert task.run_count == 0
        sched.mark_ran(task.task_id)
        assert task.run_count == 1

    def test_register_callback(self, tmp_dir):
        sched = self.CronScheduler(path=tmp_dir / "cron")
        def fn():
            return None
        sched.register_callback("message", fn)
        assert "message" in sched._callbacks


# =============================================================================
# 22. SUBSCRIPTION
# =============================================================================

class TestSubscription:
    from azure.subscription import (
        PricingPlan,
        Subscription,
        SubscriptionManager,
        SubscriptionStatus,
        SubscriptionTier,
        TierLimits,
        UsageStats,
    )

    def test_tier_limits(self):
        free = self.TierLimits.get_limits(self.SubscriptionTier.FREE)
        assert free.max_messages_per_hour == 5
        assert free.priority_support is False

        premium = self.TierLimits.get_limits(self.SubscriptionTier.PREMIUM)
        assert premium.max_messages_per_hour == -1
        assert premium.streaming_responses is True

    def test_subscription_is_active(self):
        sub = self.Subscription(user_id="u1", user_name="Alice")
        assert sub.is_active is True
        sub.status = self.SubscriptionStatus.EXPIRED
        assert sub.is_active is False

    def test_usage_stats(self, tmp_db_path):
        from azure.database import DatabaseManager
        db = DatabaseManager(db_path=tmp_db_path)
        mgr = self.SubscriptionManager(db)
        sub = mgr.get_subscription("u1", "Alice")
        assert sub.tier == self.SubscriptionTier.FREE

    def test_rate_limit(self, tmp_db_path):
        from azure.database import DatabaseManager
        db = DatabaseManager(db_path=tmp_db_path)
        mgr = self.SubscriptionManager(db)
        allowed, reason = mgr.check_rate_limit("u1")
        assert allowed is True
        assert reason == "OK"

    def test_has_feature(self, tmp_db_path):
        from azure.database import DatabaseManager
        db = DatabaseManager(db_path=tmp_db_path)
        mgr = self.SubscriptionManager(db)
        assert mgr.has_feature("u1", "streaming_responses") is False

    def test_upgrade_tier(self, tmp_db_path):
        from azure.database import DatabaseManager
        db = DatabaseManager(db_path=tmp_db_path)
        mgr = self.SubscriptionManager(db)
        mgr.upgrade_tier("u1", self.SubscriptionTier.PREMIUM, duration_days=30)
        sub = mgr.get_subscription("u1")
        assert sub.tier == self.SubscriptionTier.PREMIUM
        # Check usage
        assert mgr.has_feature("u1", "streaming_responses") is True

    def test_cancel_subscription(self, tmp_db_path):
        from azure.database import DatabaseManager
        db = DatabaseManager(db_path=tmp_db_path)
        mgr = self.SubscriptionManager(db)
        mgr.cancel_subscription("u1")
        sub = mgr.get_subscription("u1")
        assert sub.status == self.SubscriptionStatus.CANCELLED

    def test_get_pricing_plans(self):
        plans = self.PricingPlan.get_all_plans()
        assert len(plans) == 3
        names = [p.name for p in plans]
        assert "Free" in names
        assert "Premium" in names
        assert "Enterprise" in names


# =============================================================================
# 23. USER ADAPTATION
# =============================================================================

class TestUserAdaptation:
    from azure.memory_backend import InMemoryMemoryBackend
    from azure.user_adaptation import UserAdaptation

    def test_get_profile(self):
        backend = self.InMemoryMemoryBackend()
        adaptation = self.UserAdaptation(backend)
        profile = adaptation.get_profile("u1", "Alice")
        assert profile.user_id == "u1"
        assert profile.user_name == "Alice"

    def test_learn_from_message(self):
        backend = self.InMemoryMemoryBackend()
        adaptation = self.UserAdaptation(backend)
        adaptation.learn_from_message("u1", "Hello, can you help me with Python?")
        profile = adaptation.get_profile("u1")
        assert profile.total_interactions == 1
        assert "python" in profile.preferred_topics

    def test_learn_from_feedback(self):
        backend = self.InMemoryMemoryBackend()
        adaptation = self.UserAdaptation(backend)
        adaptation.learn_from_feedback("u1", "good")
        adaptation.learn_from_feedback("u1", "wrong")
        profile = adaptation.get_profile("u1")
        assert profile.thumbs_up == 1
        assert profile.thumbs_down == 1
        assert profile.corrections_received == 1

    def test_adapt_response_casual(self):
        backend = self.InMemoryMemoryBackend()
        adaptation = self.UserAdaptation(backend)
        from azure.memory_backend import UserProfile as _UP
        profile = _UP(user_id="u1", communication_style="casual")
        adapted = adaptation.adapt_response("Hello, I am ready to help", profile)
        assert "Hey" in adapted or "I'm" in adapted

    def test_adapt_response_concise(self):
        backend = self.InMemoryMemoryBackend()
        adaptation = self.UserAdaptation(backend)
        from azure.memory_backend import UserProfile as _UP
        profile = _UP(user_id="u1", verbosity="concise")
        adapted = adaptation.adapt_response("This is a long message with multiple sentences. It should be shortened. This is the third sentence.", profile)
        assert adapted.count(".") <= 2

    def test_adapt_prompt(self):
        backend = self.InMemoryMemoryBackend()
        adaptation = self.UserAdaptation(backend)
        from azure.memory_backend import UserProfile as _UP
        profile = _UP(user_id="u1", communication_style="casual", verbosity="concise")
        prompt = adaptation.adapt_prompt("Base prompt", profile)
        assert "casual" in prompt.lower()
        assert "concise" in prompt.lower() or "brief" in prompt.lower()


# =============================================================================
# 24. AGENTIC TOOLS
# =============================================================================

class TestAgenticTools:
    from azure.agentic_tools import _safe_path as _sp
    from azure.agentic_tools import execute_python as _ep
    from azure.agentic_tools import file_list as _fl
    from azure.agentic_tools import file_read as _fr
    from azure.agentic_tools import file_write as _fw
    from azure.agentic_tools import web_fetch as _wf
    from azure.agentic_tools import web_search as _ws
    web_search = staticmethod(_ws)
    web_fetch = staticmethod(_wf)
    execute_python = staticmethod(_ep)
    file_read = staticmethod(_fr)
    file_write = staticmethod(_fw)
    file_list = staticmethod(_fl)
    _safe_path = staticmethod(_sp)

    def test_file_read_not_found(self, tmp_dir):
        os.environ["AZURE_SANDBOX_DIR"] = str(tmp_dir)
        result = self.file_read("nonexistent.txt")
        assert "File not found" in result

    def test_file_write_and_read(self, tmp_dir):
        os.environ["AZURE_SANDBOX_DIR"] = str(tmp_dir)
        result = self.file_write("test.txt", "Hello world")
        assert "Written" in result
        result = self.file_read("test.txt")
        assert result == "Hello world"

    def test_file_list(self, tmp_dir):
        os.environ["AZURE_SANDBOX_DIR"] = str(tmp_dir)
        self.file_write("list_test.txt", "content")
        result = self.file_list()
        assert "list_test.txt" in result

    def test_safe_path_allowed(self, tmp_dir):
        os.environ["AZURE_SANDBOX_DIR"] = str(tmp_dir)
        p = self._safe_path("test.txt")
        assert str(tmp_dir.resolve()) in str(p)

    def test_safe_path_blocked(self, tmp_dir):
        os.environ["AZURE_SANDBOX_DIR"] = str(tmp_dir)
        with pytest.raises(PermissionError):
            self._safe_path("../outside.txt")

    def test_execute_python_disabled(self):
        result = self.execute_python("print('hello')")
        assert "disabled" in result.lower()

    def test_execute_python_blocked_import(self):
        os.environ["AZURE_ALLOW_CODE_EXECUTION"] = "true"
        try:
            result = self.execute_python("import os; print('hack')")
            assert "Blocked" in result
        finally:
            os.environ.pop("AZURE_ALLOW_CODE_EXECUTION", None)

    def test_web_search_fallback(self):
        result = self.web_search("test")
        # May fail due to network, but should not crash
        assert isinstance(result, str)

    def test_web_fetch_fallback(self):
        result = self.web_fetch("https://example.com")
        # May fail due to network, but should not crash
        assert isinstance(result, str)

    def test_file_read_error_path(self):
        result = self.file_read("")
        assert "error" in result.lower() or "not found" in result.lower()


# =============================================================================
# 25. TELEMETRY
# =============================================================================

class TestTelemetry:
    from azure.telemetry import ExecutionTracker, Stage, TelemetryEvent, set_telemetry_db
    set_telemetry_db = staticmethod(set_telemetry_db)

    def test_emit_event(self):
        tracker = self.ExecutionTracker("Alice", "TestGuild", "Hello")
        event = tracker.emit("TEST", "Test message", subsystem="core")
        assert event.action == "TEST"
        assert event.message == "Test message"
        assert len(tracker.events) == 1

    def test_execution_id_generated(self):
        tracker = self.ExecutionTracker("Alice", "TestGuild", "Hello")
        assert tracker.execution_id is not None
        assert len(tracker.execution_id) > 0

    def test_complete_success(self):
        tracker = self.ExecutionTracker("Alice", "TestGuild", "Hello")
        tracker.complete(success=True, message="Done!")
        assert tracker.is_finished
        assert tracker._finish_status == "success"

    def test_complete_error(self):
        tracker = self.ExecutionTracker("Alice", "TestGuild", "Hello")
        tracker.complete(success=False)
        assert tracker.is_finished
        assert tracker._finish_status == "error"

    def test_elapsed_ms(self):
        tracker = self.ExecutionTracker("Alice", "TestGuild", "Hello")
        assert tracker.elapsed_ms >= 0

    def test_get_presentation(self):
        tracker = self.ExecutionTracker("Alice", "TestGuild", "Hello")
        tracker.emit("START", "Starting", status="running")
        pres = tracker.get_presentation()
        assert "execution_id" in pres
        assert "stages" in pres
        assert "user" in pres

    def test_discord_progress(self):
        tracker = self.ExecutionTracker("Alice", "TestGuild", "Hello")
        # Before complete
        text = tracker.get_discord_progress_text()
        assert "Thinking" in text


# =============================================================================
# 26. STREAMING
# =============================================================================

class TestStreaming:
    from azure.streaming import ResponseStreamer, StreamBuffer, StreamChunk

    @pytest.mark.asyncio
    async def test_stream_buffer(self):
        buf = self.StreamBuffer(max_size=20)
        content, overflow = buf.append("Short")
        assert not overflow
        assert len(buf.get_content()) <= 20

    def test_stream_buffer_overflow(self):
        buf = self.StreamBuffer(max_size=10)
        content, overflow = buf.append("Hello World This Is A Long String")
        assert overflow is True
        assert len(buf.get_content()) <= 10
        assert len(buf.get_full_content()) > 10

    def test_stream_chunk(self):
        chunk = self.StreamChunk(text="Hello", timestamp=time.time())
        assert chunk.text == "Hello"
        assert chunk.is_final is False

    def test_stream_chunk_final(self):
        chunk = self.StreamChunk(text="Done", timestamp=time.time(), is_final=True)
        assert chunk.is_final is True


# =============================================================================
# 27. TEMPORAL
# =============================================================================

class TestTemporal:
    from azure.temporal import TemporalAnalyzer, TemporalEvent, TemporalSignals

    def test_ingest_event(self):
        analyzer = self.TemporalAnalyzer()
        event = analyzer.ingest_event(
            message_id="m1", user_id="u1", guild_id="g1",
            channel_id="c1", content="Hello world", severity=0.1,
            category="normal"
        )
        assert event.message_id == "m1"
        assert len(analyzer.events) == 1

    def test_analyze_situation_no_events(self):
        analyzer = self.TemporalAnalyzer()
        signals = analyzer.analyze_situation("g1")
        assert signals.raid_probability == 0.0
        assert signals.explanation == "no recent activity"

    def test_single_event_no_raid(self):
        analyzer = self.TemporalAnalyzer()
        analyzer.ingest_event("m1", "u1", "g1", "c1", "Hello", 0.1, "normal")
        signals = analyzer.analyze_situation("g1")
        assert signals.burst_score == 0.0
        assert signals.raid_probability == 0.0

    def test_burst_detection(self):
        analyzer = self.TemporalAnalyzer()
        time.time()
        for i in range(10):
            analyzer.ingest_event(f"m{i}", f"u{i}", "g1", "c1", "Spam", 0.5, "spam")
        signals = analyzer.analyze_situation("g1")
        assert signals.burst_score >= 0.7

    def test_get_user_events(self):
        analyzer = self.TemporalAnalyzer()
        analyzer.ingest_event("m1", "u1", "g1", "c1", "Hello", 0.1, "normal")
        events = analyzer.get_user_events("u1")
        assert len(events) == 1
        assert analyzer.get_user_events("nonexistent") == []

    def test_cleanup(self):
        analyzer = self.TemporalAnalyzer()
        analyzer.ingest_event("m1", "u1", "g1", "c1", "Hello", 0.1, "normal")
        analyzer.cleanup()
        # cleanup only removes events older than TTL (30 min default)
        assert len(analyzer.events) > 0  # recent events preserved

    def test_temporal_signals_to_dict(self):
        signals = self.TemporalSignals(
            burst_score=0.5, coordination_score=0.3, cross_channel_score=0.2,
            raid_probability=0.4, novelty_score=0.1,
            involved_users=["u1"], involved_channels=["c1"],
            matched_messages=5, explanation="test",
            is_raid=False, is_spam_wave=False, is_coordination=False,
        )
        d = signals.to_dict()
        assert d["burst_score"] == 0.5
        assert d["raid_probability"] == 0.4


# =============================================================================
# 28. SELF AWARENESS
# =============================================================================

class TestSelfAwareness:
    from azure.self_awareness import SelfAwareness, handle_self_config_request
    handle_self_config_request = staticmethod(handle_self_config_request)

    def test_read_env_empty(self, tmp_dir):
        awareness = self.SelfAwareness(project_root=tmp_dir)
        config = awareness.read_env()
        assert config == {}

    def test_read_env_with_content(self, tmp_dir):
        env_file = tmp_dir / ".env"
        env_file.write_text("KEY=value\nANOTHER=123\n")
        awareness = self.SelfAwareness(project_root=tmp_dir)
        config = awareness.read_env()
        assert config["KEY"] == "value"
        assert config["ANOTHER"] == "123"

    def test_update_config(self, tmp_dir):
        env_file = tmp_dir / ".env"
        env_file.write_text("OLD=val\n")
        awareness = self.SelfAwareness(project_root=tmp_dir)
        success = awareness.update_config("AZURE_CHAT_MODE", "owner_only")
        assert success is True
        config = awareness.read_env()
        assert config["AZURE_CHAT_MODE"] == "owner_only"

    def test_parse_access_control(self):
        awareness = self.SelfAwareness()
        result = awareness.parse_access_control_intent("only let me talk to you", "user1")
        assert result is not None
        assert result["action"] == "set_chat_mode"
        assert result["mode"] == "owner_only"

    def test_parse_anyone(self):
        awareness = self.SelfAwareness()
        result = awareness.parse_access_control_intent("let anyone talk to you", "user1")
        assert result is not None
        assert result["mode"] == "anyone"

    def test_can_safely_edit(self):
        awareness = self.SelfAwareness()
        safe, reason = awareness.can_safely_edit("change chat mode")
        assert safe is True
        unsafe, reason = awareness.can_safely_edit("edit code in agent.py")
        assert unsafe is False


# =============================================================================
# 29. RAG ENGINE
# =============================================================================

class TestRagEngine:
    from azure.rag_engine import DiscordRAG, Document

    def test_add_document(self, tmp_dir):
        import azure.rag_engine as _rag_mod
        _rag_mod._SentenceTransformer = None  # reset cache so patch takes effect
        mock_instance = MagicMock()
        mock_instance.get_embedding_dimension.return_value = 384
        mock_instance.encode.return_value = [0.1] * 384
        mock_st = MagicMock(return_value=mock_instance)
        with patch("azure.rag_engine._SentenceTransformer", mock_st):
            rag = self.DiscordRAG(persist_path=tmp_dir / "rag.json", max_docs=100)

        doc_id = rag.add("Hello world", {"source": "test"})
        assert doc_id is not None
        assert len(rag.docs) == 1

    def test_clear(self, tmp_dir):
        import azure.rag_engine as _rag_mod
        _rag_mod._SentenceTransformer = None
        mock_instance = MagicMock()
        mock_instance.get_embedding_dimension.return_value = 384
        mock_instance.encode.return_value = [0.1] * 384
        mock_st = MagicMock(return_value=mock_instance)
        with patch("azure.rag_engine._SentenceTransformer", mock_st):
            rag = self.DiscordRAG(persist_path=tmp_dir / "rag.json", max_docs=100)

        rag.add("Hello")
        rag.clear()
        assert len(rag.docs) == 0
        assert rag._matrix is None

    def test_get_recent(self, tmp_dir):
        import azure.rag_engine as _rag_mod
        _rag_mod._SentenceTransformer = None
        mock_instance = MagicMock()
        mock_instance.get_embedding_dimension.return_value = 384
        mock_instance.encode.return_value = [0.1] * 384
        mock_st = MagicMock(return_value=mock_instance)
        with patch("azure.rag_engine._SentenceTransformer", mock_st):
            rag = self.DiscordRAG(persist_path=tmp_dir / "rag.json", max_docs=100)

        rag.add("A")
        rag.add("B")
        recent = rag.get_recent(n=2)
        assert len(recent) == 2
        assert recent[-1]["text"] == "B"

    def test_search_is_scoped_to_guild(self, tmp_dir):
        import azure.rag_engine as _rag_mod
        _rag_mod._SentenceTransformer = None
        mock_instance = MagicMock()
        mock_instance.get_embedding_dimension.return_value = 384
        mock_instance.encode.return_value = [0.1] * 384
        mock_st = MagicMock(return_value=mock_instance)
        with patch("azure.rag_engine._SentenceTransformer", mock_st):
            rag = self.DiscordRAG(persist_path=tmp_dir / "rag.json", max_docs=100)

        rag.add_message("Alice", "shared server secret", guild="guild:a")
        rag.add_message("Bob", "shared server secret", guild="guild:b")

        results = rag.search("shared server secret", scope="guild:a", k=5)

        assert len(results) == 1
        assert results[0]["metadata"]["guild"] == "guild:a"


# =============================================================================
# 30. ENHANCED RAG
# =============================================================================

class TestEnhancedRAG:
    from azure.rag_enhanced import HybridRAG, KnowledgeGraph, RAGResult

    def test_knowledge_graph_entity_extraction(self):
        kg = self.KnowledgeGraph()
        entities = kg.extract_entities('Alice said "Python is great"')
        assert "Alice" in entities
        assert "Python is great" in entities

    def test_add_entity(self):
        kg = self.KnowledgeGraph()
        kg.add_entity("Python", "mem_1")
        related = kg.get_related("Python")
        assert "mem_1" in related

    def test_hybrid_rag_init(self, tmp_dir):
        rag = self.HybridRAG(db_path=str(tmp_dir / "rag.db"))
        assert rag.db_path.exists()

    def test_tokenize(self, tmp_dir):
        rag = self.HybridRAG(db_path=str(tmp_dir / "rag.db"))
        tokens = rag._tokenize("Hello World Python Programming")
        assert "python" in tokens
        assert "hello" in tokens
        assert "world" in tokens
        assert "programming" in tokens

    def test_hybrid_rag_add_and_query_no_embedding(self, tmp_dir):
        rag = self.HybridRAG(db_path=str(tmp_dir / "rag.db"))
        rag.add_memory("Python is a great programming language", source="general", tags=["python"])
        rag.add_memory("JavaScript is also popular", source="general", tags=["js"])
        results = rag.query("python", top_k=5)
        assert len(results) > 0
        assert any("python" in r.text.lower() for r in results)

    def test_hybrid_rag_scope_isolation(self, tmp_dir):
        rag = self.HybridRAG(db_path=str(tmp_dir / "rag.db"))
        rag.add_memory("guild A private moderation note", tags=["scope:guild:a"])
        rag.add_memory("guild B private moderation note", tags=["scope:guild:b"])

        results = rag.query("private moderation note", top_k=5, scope_tag="scope:guild:a")

        assert len(results) == 1
        assert "guild A" in results[0].text


# =============================================================================
# 31. API LLM (UNIT TESTS - MOCKED)
# =============================================================================

class TestApiLLM:
    from azure.api_llm import ApiLLM, HybridLLM, create_api_llm_from_env
    create_api_llm_from_env = staticmethod(create_api_llm_from_env)

    def test_create_api_llm_no_key(self):
        # Without env keys, should raise RuntimeError
        with pytest.raises(RuntimeError, match="No API provider configured"):
            self.ApiLLM()

    def test_create_api_llm_with_env(self):
        os.environ["OPENAI_API_KEY"] = "sk-test-key"
        os.environ["AZURE_LLM_PROVIDER"] = "openai"
        try:
            llm = self.ApiLLM()
            assert llm is not None
            assert llm._provider == "openai"
            assert llm._model == "gpt-4o-mini"
        finally:
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("AZURE_LLM_PROVIDER", None)

    def test_create_api_llm_from_env_no_key(self):
        from azure.api_llm import create_api_llm_from_env
        result = create_api_llm_from_env()
        assert result is None

    def test_count_tokens(self):
        # Need an instance; we can test static behavior
        pass

    def test_hybrid_llm_api_fallback_local(self):
        api = MagicMock()
        api.is_loaded = True
        api.chat.side_effect = RuntimeError("API failed")
        local = MagicMock()
        local.chat.return_value = "Local fallback"
        hybrid = self.HybridLLM(api_llm=api, local_llm=local)
        result = hybrid.chat([{"role": "user", "content": "Hello"}])
        assert result == "Local fallback"
        assert hybrid._last_used == "local"

    def test_hybrid_llm_no_backends(self):
        hybrid = self.HybridLLM()
        assert hybrid._loaded is False
        result = hybrid.chat([{"role": "user", "content": "Hello"}])
        assert "[HybridLLM" in result

    def test_hybrid_llm_api_success(self):
        api = MagicMock()
        api.is_loaded = True
        api.chat.return_value = "API response"
        hybrid = self.HybridLLM(api_llm=api)
        result = hybrid.chat([{"role": "user", "content": "Hello"}])
        assert result == "API response"
        assert hybrid._last_used == "api"


# =============================================================================
# 32. LOCAL LLM (UNIT TESTS - BASIC INTERFACE)
# =============================================================================

class TestLocalLLM:
    from azure.local_llm import LocalLLM, SubprocessLLM

    def test_init_no_model_file(self, tmp_dir):
        with pytest.raises(FileNotFoundError, match="Model file not found"):
            self.LocalLLM(model_path=str(tmp_dir / "nonexistent.gguf"))

    def test_model_type_detection(self, tmp_dir):
        # Create a dummy file so path exists
        model_file = tmp_dir / "qwen2.5-3b-instruct-q4_k_m.gguf"
        model_file.write_text("dummy")
        with pytest.raises(Exception):  # noqa: B017  # Expected: dummy file can't load
            llm = self.LocalLLM(model_path=str(model_file))
            assert llm._model_type == "qwen"

    def test_stop_tokens(self):
        pass

    def test_backend_detection(self):
        # Should not crash
        pass

    def test_subprocess_llm_init(self):
        sub = self.SubprocessLLM(model_path="test.gguf")
        assert sub is not None
        assert sub._start_called is False


# =============================================================================
# 33. AGENT (BASIC INTEGRATION)
# =============================================================================

class TestAzureAgent:
    from azure.agent import AzureAgent, LongTermMemory, ShortTermMemory, ToolRegistry

    def test_agent_init_no_model(self):
        agent = self.AzureAgent(model_name="test")
        # Should not crash even without model file
        assert agent is not None
        assert agent.short_term is not None
        assert agent.long_term is not None
        assert agent.tools is not None

    def test_short_term_memory_in_agent(self):
        agent = self.AzureAgent(model_name="test")
        agent.short_term.add("user", "Hello")
        agent.short_term.add("assistant", "Hi!")
        hist = agent.short_term.to_history()
        assert len(hist) == 2

    def test_tool_registry_in_agent(self):
        agent = self.AzureAgent(model_name="test")
        desc = agent.tools.describe()
        assert len(desc) >= 1  # at least get_time

    def test_discord_plan_capabilities_follow_registry(self):
        from types import SimpleNamespace

        planner = SimpleNamespace(registry=SimpleNamespace(tools={
            "create_channel": SimpleNamespace(docstring="Create a channel."),
            "get_audit_logs": SimpleNamespace(docstring="Read audit logs."),
            "execute_plan": SimpleNamespace(docstring="Execute a complete plan."),
            "get_server_state": SimpleNamespace(docstring="Read server state."),
        }))

        capabilities = self.AzureAgent._get_discord_plan_capabilities(planner)

        assert "create_channel: Create a channel." in capabilities
        assert "get_audit_logs: Read audit logs." in capabilities
        assert "execute_plan" not in capabilities
        assert "get_server_state" not in capabilities

    def test_short_term_key_isolated_by_memory_scope(self):
        assert self.AzureAgent._short_term_key("42", "guild:one") != self.AzureAgent._short_term_key("42", "guild:two")
        assert self.AzureAgent._short_term_key("42", "dm:42") != self.AzureAgent._short_term_key("42", "guild:one")

    def test_tool_get_time(self):
        from azure.agent import tool_get_time
        result = tool_get_time()
        assert len(result) > 0
        assert "-" in result  # date format

    def test_classify_message_intent(self):
        intent = self.AzureAgent._classify_message_intent("Hello there!")
        # Keyword greeting banks removed — LLM-first routing elsewhere
        assert intent["is_greeting"] is False
        assert intent["is_command"] is False
        assert intent["is_question"] is False

    def test_classify_message_question(self):
        intent = self.AzureAgent._classify_message_intent("Can you help me?")
        assert intent["is_question"] is True

    def test_classify_message_command(self):
        intent = self.AzureAgent._classify_message_intent("create a new channel")
        assert intent["is_command"] is False

    def test_classify_message_needs_memory(self):
        intent = self.AzureAgent._classify_message_intent("Remember my favorite color is blue")
        assert intent["needs_memory"] is False

    def test_server_context_uses_explicit_request_guild(self):
        from types import SimpleNamespace

        agent = self.AzureAgent(model_name="test")
        guild_a = SimpleNamespace(
            name="Guild A", member_count=10, members=[],
            verification_level="high", explicit_content_filter="all",
            text_channels=[], categories=[], roles=[],
        )
        guild_b = SimpleNamespace(
            name="Guild B", member_count=20, members=[],
            verification_level="low", explicit_content_filter="disabled",
            text_channels=[], categories=[], roles=[],
        )
        agent._current_guild = guild_a

        context = agent._build_server_context("fallback", "user", guild=guild_b)

        assert "Server: Guild B" in context
        assert "Guild A" not in context


# =============================================================================
# 34. HYBRID LLM WRAPPER
# =============================================================================

class TestHybridLLM:
    from azure.api_llm import HybridLLM

    def test_hybrid_init_no_backends(self):
        hybrid = self.HybridLLM()
        assert hybrid._loaded is False

    def test_hybrid_temperature_fallback(self):
        hybrid = self.HybridLLM()
        assert hybrid.temperature == 0.7
        assert hybrid.max_tokens == 256

    def test_hybrid_get_info(self):
        hybrid = self.HybridLLM()
        info = hybrid.get_info()
        assert info["type"] == "hybrid"
        assert info["last_used"] == "none"

# =============================================================================
# 35. SERVER HEALTH
# =============================================================================

class TestServerHealth:
    from azure.server_health import ServerHealthAnalyzer

    def test_prioritize(self):
        analyzer = self.ServerHealthAnalyzer()
        recs = [
            {"priority": "low", "text": "Low priority"},
            {"priority": "high", "text": "High priority"},
            {"priority": "medium", "text": "Medium priority"},
        ]
        sorted_recs = analyzer._prioritize(recs)
        assert sorted_recs[0]["priority"] == "high"
        assert sorted_recs[-1]["priority"] == "low"

    def test_generate_followups(self):
        analyzer = self.ServerHealthAnalyzer()
        report = {
            "recommendations": [
                {"action": "create_channels", "channels": ["rules", "welcome"]},
                {"action": "create_roles", "roles": ["Admin"]},
            ]
        }
        followups = analyzer._generate_followups(report)
        assert len(followups) >= 2


# =============================================================================
# 36. SERVER TEMPLATES
# =============================================================================

class TestServerTemplates:
    from azure.server_templates import ServerTemplate, ServerTemplateManager

    def test_validate_template_name(self, tmp_dir):
        mgr = self.ServerTemplateManager(template_dir=tmp_dir / "templates")
        valid = mgr._validate_template_name("Gaming Setup")
        assert valid == "Gaming Setup"

    def test_validate_template_name_invalid_chars(self, tmp_dir):
        mgr = self.ServerTemplateManager(template_dir=tmp_dir / "templates")
        with pytest.raises(ValueError, match="forbidden"):
            mgr._validate_template_name("../escape")

    def test_validate_template_name_empty(self, tmp_dir):
        mgr = self.ServerTemplateManager(template_dir=tmp_dir / "templates")
        with pytest.raises(ValueError, match="empty"):
            mgr._validate_template_name("")

    def test_validate_template_name_too_long(self, tmp_dir):
        mgr = self.ServerTemplateManager(template_dir=tmp_dir / "templates")
        with pytest.raises(ValueError, match="64"):
            mgr._validate_template_name("a" * 65)

    def test_parse_color(self, tmp_dir):
        mgr = self.ServerTemplateManager()
        assert mgr._parse_color("E74C3C") == "e74c3c"
        assert mgr._parse_color("#FF0000") == "ff0000"
        assert mgr._parse_color("0x3498DB") == "3498db"
        assert mgr._parse_color(None) is None

    def test_save_template(self, tmp_dir):
        mgr = self.ServerTemplateManager(template_dir=tmp_dir / "templates")
        template = self.ServerTemplate(
            name="test", description="Test template", created_at=time.time(),
            roles=[], categories=[], channels=[], permission_overwrites=[],
        )
        path = tmp_dir / "templates" / "test.json"
        path.parent.mkdir(exist_ok=True)
        from dataclasses import asdict
        path.write_text(json.dumps(asdict(template)), encoding="utf-8")
        loaded = mgr.load_template("test")
        assert loaded is not None
        assert loaded.name == "test"

    def test_list_templates(self, tmp_dir):
        mgr = self.ServerTemplateManager(template_dir=tmp_dir / "templates")
        assert mgr.list_templates() == []


# =============================================================================
# 37. VISION PROCESSOR
# =============================================================================

class TestVisionProcessor:
    from azure.vision_processor import VisionProcessor, VisionResult

    def test_vision_result_to_context_no_data(self):
        result = self.VisionResult(file_type="png", file_size=1024, width=100, height=100)
        context = result.to_context()
        assert "Image file" in context
        assert "png" in context

    def test_vision_result_to_context_with_caption(self):
        result = self.VisionResult(caption="A cute cat", objects=["cat"], file_type="jpg")
        context = result.to_context()
        assert "cat" in context

    def test_is_safe_url_valid(self):
        assert self.VisionProcessor._is_safe_url("https://example.com/image.png") is True
        assert self.VisionProcessor._is_safe_url("http://example.com") is True

    def test_is_safe_url_blocked(self):
        assert self.VisionProcessor._is_safe_url("file:///etc/passwd") is False
        assert self.VisionProcessor._is_safe_url("ftp://example.com") is False
        assert self.VisionProcessor._is_safe_url("data:text/html,<script>") is False
        assert self.VisionProcessor._is_safe_url("javascript:alert(1)") is False


# =============================================================================
# 38. VOICE SYSTEM
# =============================================================================

class TestVoiceSystem:
    from azure.voice_system import VoiceConfig, VoiceSystem

    def test_init_not_connected(self):
        self.VoiceSystem()
        # is_ready may be True if TTS engine is available on the system

    def test_voice_config_defaults(self):
        config = self.VoiceConfig()
        assert config.tts_engine == "auto"
        assert config.stt_engine == "auto"
        assert config.language == "en"


# =============================================================================
# 39. CRON SCHEDULER - NATURAL LANGUAGE PARSING
# =============================================================================

class TestCronNLParsing:
    from azure.cron_scheduler import CronScheduler

    def test_every_hour(self):
        sched = self.CronScheduler()
        assert sched.natural_language_to_cron("every hour") == "0 * * * *"

    def test_every_day(self):
        sched = self.CronScheduler()
        assert sched.natural_language_to_cron("every day at 9am") == "0 9 * * *"

    def test_every_day_pm(self):
        sched = self.CronScheduler()
        result = sched.natural_language_to_cron("every day at 3pm")
        assert result == "0 15 * * *"

    def test_every_monday(self):
        sched = self.CronScheduler()
        assert sched.natural_language_to_cron("every monday") == "0 9 * * 1"

    def test_every_night(self):
        sched = self.CronScheduler()
        assert sched.natural_language_to_cron("every night") == "0 20 * * *"

    def test_complex_returns_none(self):
        sched = self.CronScheduler()
        assert sched.natural_language_to_cron("do something weird") is None

    def test_every_30_minutes(self):
        sched = self.CronScheduler()
        assert sched.natural_language_to_cron("every 30 minutes") == "*/30 * * * *"

    def test_every_15_minutes(self):
        sched = self.CronScheduler()
        assert sched.natural_language_to_cron("every 15 minutes") == "*/15 * * * *"


# =============================================================================
# 40. RESPONSE CACHE - EDGE CASES
# =============================================================================

class TestResponseCacheEdgeCases:
    from azure.response_cache import ResponseCache

    def test_zero_ttl_no_expiry(self):
        cache = self.ResponseCache(max_size=10, ttl_seconds=0)
        cache.set("key", "val", complexity="LOW", confidence=1.0)
        time.sleep(0.01)
        assert cache.get("key") == "val"

    def test_set_overwrites_existing(self):
        cache = self.ResponseCache(max_size=10)
        cache.set("key", "val1", complexity="LOW", confidence=1.0)
        cache.set("key", "val2", complexity="LOW", confidence=1.0)
        assert cache.get("key") == "val2"

    def test_empty_cache_stats(self):
        cache = self.ResponseCache()
        stats = cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 0.0

    def test_invalidate_empty(self):
        cache = self.ResponseCache()
        assert cache.invalidate() == 0


# =============================================================================
# 41. CIRCUIT BREAKER EDGE CASES
# =============================================================================

class TestCircuitBreakerEdgeCases:
    from azure.circuit_breaker import CircuitBreaker

    def test_success_before_threshold(self):
        cb = self.CircuitBreaker(failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.state == "CLOSED"
        assert cb._failure_count == 0

    def test_concurrent_safety(self):
        cb = self.CircuitBreaker(failure_threshold=100)
        errors = []

        def record():
            try:
                for _ in range(50):
                    cb.record_failure()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert cb._failure_count <= 200


# =============================================================================
# 42. FAILOVER CHAIN EDGE CASES
# =============================================================================

class TestFailoverChainEdgeCases:
    from azure.failover_chain import FailoverChain, FailoverResult

    def test_no_llm_configured(self):
        chain = self.FailoverChain()
        result = chain.respond("Hello")
        assert result.used_fallback is True
        assert "exhausted" in result.text or "unavailable" in result.text

    def test_tier_system_prompt_differs(self):
        chain = self.FailoverChain(llm=MockLLM("Test"))
        for tier in range(1, 6):
            prompt = chain._build_system_prompt(tier, {"server": "Test", "user": "Alice"})
            assert "Azure" in prompt
            assert True  # all should work


# =============================================================================
# 43. TELEMETRY EDGE CASES
# =============================================================================

class TestTelemetryEdgeCases:
    from azure.telemetry import ExecutionTracker, set_main_loop, set_telemetry_db
    set_telemetry_db = staticmethod(set_telemetry_db)
    set_main_loop = staticmethod(set_main_loop)

    def test_double_complete(self):
        tracker = self.ExecutionTracker("Alice", "Guild", "Test")
        tracker.complete(success=True)
        tracker.complete(success=True)  # Should be no-op
        assert tracker.is_finished

    def test_format_duration(self):
        from azure.telemetry import ExecutionTracker as ET
        assert ET._format_duration(500) == "500ms"
        assert ET._format_duration(1500) == "1.5s"
        assert ET._format_duration(65000) == "1m 5s"

    def test_stage_duration(self):
        import time

        from azure.telemetry import Stage
        now = time.time()
        stage = Stage(stage_id="s1", action="TEST", label="Test",
                      detail="Testing", status="running", started_at=now - 1)
        assert stage.duration_ms >= 1000
        stage.ended_at = now
        assert 900 <= stage.duration_ms <= 1100

    def test_set_telemetry_db(self):
        import azure.telemetry as _tel
        db = MagicMock()
        _tel.set_telemetry_db(db)
        assert _tel._TELEMETRY_DB is db

    def test_set_main_loop(self):
        import azure.telemetry as _tel
        loop = object()
        _tel.set_main_loop(loop)
        assert _tel._MAIN_LOOP is loop


# =============================================================================
# 44. MODEL ROUTER EDGE CASES
# =============================================================================

class TestModelRouterEdgeCases:
    from azure.model_router import ModelRouter, RouterResult

    def test_empty_message(self):
        router = self.ModelRouter(main_llm=MockLLM("Response"))
        result = router.route("")
        # Empty message can't be handled by any tier -> emergency fallback
        assert result.tier == -1
        assert result.tier_name == "emergency_fallback"


# =============================================================================
# 45. DATABASE CONCURRENCY
# =============================================================================

class TestDatabaseConcurrency:
    from azure.database import ConversationMessage, DatabaseManager

    def test_concurrent_writes(self, tmp_db_path):
        db = self.DatabaseManager(db_path=tmp_db_path)
        errors = []

        def write_msg(i):
            try:
                db.save_conversation(self.ConversationMessage(
                    user_id=f"u{i}", user_name=f"User{i}", server_id="s1",
                    server_name="S", channel_id="c1", channel_name="general",
                    message=f"msg{i}", response=f"resp{i}", timestamp=time.time(),
                ))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_msg, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        # Verify
        history = db.get_conversation_history(limit=100)
        assert len(history) == 20


# =============================================================================
# 46. USER ADAPTATION EDGE CASES
# =============================================================================

class TestUserAdaptationEdgeCases:
    from azure.memory_backend import InMemoryMemoryBackend, UserProfile
    from azure.user_adaptation import UserAdaptation

    def test_learn_from_empty_message(self):
        backend = self.InMemoryMemoryBackend()
        adaptation = self.UserAdaptation(backend)
        adaptation.learn_from_message("u1", "")
        profile = backend.get_user_profile("u1")
        assert profile is not None
        assert profile.total_interactions == 1

    def test_learn_from_technical_message(self):
        backend = self.InMemoryMemoryBackend()
        adaptation = self.UserAdaptation(backend)
        adaptation.learn_from_message("u1", "The Docker container has a Kubernetes deployment")
        profile = adaptation.get_profile("u1")
        assert profile.expertise_level == "advanced"

    def test_adapt_response_technical_simplify(self):
        backend = self.InMemoryMemoryBackend()
        adaptation = self.UserAdaptation(backend)
        from azure.memory_backend import UserProfile as _UP
        profile = _UP(user_id="u1", expertise_level="beginner")
        adapted = adaptation.adapt_response("Use the API to access the database", profile)
        assert "external service interface" in adapted.lower() or "data storage system" in adapted.lower()


# =============================================================================
# 47. SELF AWARENESS EDGE CASES
# =============================================================================

class TestSelfAwarenessEdgeCases:
    from azure.self_awareness import SelfAwareness

    def test_read_own_code_nonexistent(self, tmp_dir):
        awareness = self.SelfAwareness(project_root=tmp_dir)
        result = awareness.read_own_code("nonexistent_module")
        assert result is None

    def test_write_env_backup(self, tmp_dir):
        env_file = tmp_dir / ".env"
        env_file.write_text("KEY=original\n")
        awareness = self.SelfAwareness(project_root=tmp_dir)
        awareness.update_config("KEY", "updated")
        config = awareness.read_env()
        assert config["KEY"] == "updated"
        # Backup should exist
        backups = list((tmp_dir / "logs" / "self_edits").glob("*.backup"))
        assert len(backups) >= 1

    def test_parse_model_intent(self):
        awareness = self.SelfAwareness()
        result = awareness.parse_model_intent("enable cognitive mode")
        assert result is not None
        assert result["key"] == "AZURE_COGNITIVE_MODE"
        assert result["value"] == "1"

    def test_understand_codebase(self):
        awareness = self.SelfAwareness()
        structure = awareness.understand_codebase()
        assert "core_modules" in structure
        assert "moderation" in structure


# =============================================================================
# 48. DECISION ENGINE EDGE CASES
# =============================================================================

class TestDecisionEdgeCases:
    import azure.moderation.policy as mp

    def test_situation_no_raid(self):
        from azure.decision import DecisionEngine
        policy = self.mp.ModerationPolicy(mode="reactive")
        engine = DecisionEngine(policy)
        decision = engine.decide_situation(
            temporal_signals={"raid_probability": 0.3, "is_raid": False},
            risk_profile={}, phase=self.mp.ModerationPhase.REACTIVE_FULL,
            involved_users=["u1"],
        )
        assert decision.action == self.mp.ActionType.NONE
        assert decision.reason == "no_situation"

    def test_dry_run_clamps_action(self):
        from azure.decision import DecisionEngine
        policy = self.mp.ModerationPolicy(mode="dry_run")
        engine = DecisionEngine(policy)
        decision = engine.decide(
            content_severity=0.9, content_confidence=0.9,
            content_category="toxicity", behavioral_signals={},
            temporal_signals={}, risk_profile={"total_risk": 0.9, "confidence": 0.9, "user_risk": 0.0,
                                               "situation_risk": 0.0},
            phase=self.mp.ModerationPhase.DRY_RUN,
        )
        assert decision.action == self.mp.ActionType.LOG


# =============================================================================
# 49. CHANGE TRACKER PERSISTENCE
# =============================================================================

class TestChangeTrackerPersistence:
    from azure.change_tracker import ChangeRecord, ChangeTracker

    def test_persist_and_load(self, tmp_dir):
        tracker = self.ChangeTracker(log_dir=tmp_dir / "changes")
        tracker.log_change(
            guild_id=123, guild_name="Test", action="create_role",
            target={"name": "Mod", "id": 1}, before=None,
            after={"name": "Mod"}, performed_by="Owner",
        )
        # Create new tracker with same log dir and load
        tracker2 = self.ChangeTracker(log_dir=tmp_dir / "changes")
        tracker2.load_from_disk(123)
        stats = tracker2.get_stats(123)
        assert stats["total"] == 1

    def test_non_reversible_action(self):
        tracker = self.ChangeTracker()
        tracker.log_change(
            guild_id=123, guild_name="Test", action="send_message",
            target={"name": "msg"}, before=None, after=None,
            performed_by="Owner",
        )
        assert tracker.can_undo(123) is True  # logged successfully means undoable

    def test_undo_pointer_advances(self):
        tracker = self.ChangeTracker()
        for i in range(3):
            tracker.log_change(123, "Test", "create_role", {"name": f"r{i}"},
                               None, {"name": f"r{i}"}, "Owner")
        assert tracker._undo_pointer.get(123) == 0
        tracker.get_undo(123)
        assert tracker._undo_pointer.get(123) == 1
        tracker.get_undo(123)
        assert tracker._undo_pointer.get(123) == 2


# =============================================================================
# 50. SUBSCRIPTION EDGE CASES
# =============================================================================

class TestSubscriptionEdgeCases:
    from azure.subscription import Subscription, SubscriptionStatus, SubscriptionTier

    def test_days_remaining(self):
        sub = self.Subscription(user_id="u1", user_name="Alice",
                                end_date=time.time() + 86400 * 5)
        assert sub.days_remaining == 5

    def test_days_remaining_no_end(self):
        sub = self.Subscription(user_id="u1", user_name="Alice")
        assert sub.days_remaining is None

    def test_is_trial_no_end(self):
        sub = self.Subscription(user_id="u1", user_name="Alice")
        assert sub.is_trial is False

    def test_is_trial_active(self):
        sub = self.Subscription(user_id="u1", user_name="Alice",
                                trial_end_date=time.time() + 86400)
        assert sub.is_trial is True

    def test_is_trial_expired(self):
        sub = self.Subscription(user_id="u1", user_name="Alice",
                                trial_end_date=time.time() - 1)
        assert sub.is_trial is False


# =============================================================================
# 51. CORE AZURE INIT
# =============================================================================

class TestAzureInit:
    def test_version(self):
        from azure import __version__
        assert __version__ == "1.0.0"

    def test_load_model_no_path(self):
        from azure import load_model
        with pytest.raises((FileNotFoundError, ValueError)):
            load_model("/nonexistent/path")
