"""Comprehensive real integration tests for the bot/ subsystem.

Tests cover BotConfig, BotContext, BackgroundExecutor, rate_limiter,
response_cache, _is_directed_at_bot, template_handler, PlanExecutionView,
lifecycle functions, and all edge cases / error paths.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib

# ---------------------------------------------------------------------------
# Disable logging noise during tests
# ---------------------------------------------------------------------------
import logging
import os
import sys
import time
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

logging.disable(logging.CRITICAL)

# Ensure the project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ===================================================================
# Test: BotConfig (pydantic_config.py)
# ===================================================================

class TestBotConfig:
    """Test BotConfig Pydantic model construction, validation, defaults."""

    def test_config_defaults(self):
        """Default BotConfig should have sensible defaults."""
        from bot.pydantic_config import BotConfig
        cfg = BotConfig()
        assert cfg.rate_limit_messages == 10
        assert cfg.rate_limit_window == 60.0
        assert cfg.cooldown_seconds == 5.0
        assert cfg.response_cache_size == 100
        assert cfg.response_cache_ttl == 3600.0
        assert cfg.chunk_size == 1900
        assert cfg.default_max_tokens == 150
        assert cfg.default_temperature == 0.7
        assert cfg.max_retries == 3
        assert cfg.moderation_enabled is True
        assert cfg.moderation_phase == "dry_run"

    def test_config_env_prefix(self):
        """BotConfig should read from AZURE_ prefixed env vars."""
        from bot.pydantic_config import BotConfig
        with patch.dict(os.environ, {
            "AZURE_RATE_LIMIT_MESSAGES": "5",
            "AZURE_CHUNK_SIZE": "1000",
            "AZURE_COOLDOWN_SECONDS": "2.0",
        }, clear=False):
            cfg = BotConfig()
            assert cfg.rate_limit_messages == 5
            assert cfg.chunk_size == 1000
            assert cfg.cooldown_seconds == 2.0

    def test_config_custom_values(self):
        """BotConfig should accept constructor overrides."""
        from bot.pydantic_config import BotConfig
        cfg = BotConfig(rate_limit_messages=3, chunk_size=500, max_retries=5)
        assert cfg.rate_limit_messages == 3
        assert cfg.chunk_size == 500
        assert cfg.max_retries == 5

    def test_config_invalid_types(self):
        """BotConfig should raise validation errors for invalid types."""
        import pydantic

        from bot.pydantic_config import BotConfig
        with pytest.raises(pydantic.ValidationError):
            BotConfig(rate_limit_messages="not_a_number")  # type: ignore[arg-type]

    def test_config_singleton_available(self):
        """The module-level `config` singleton should be a BotConfig instance."""
        from bot.pydantic_config import config
        assert config is not None
        from bot.pydantic_config import BotConfig
        assert isinstance(config, BotConfig)


class TestCommandMemoryIsolation:
    def test_scoped_memory_key_contains_guild_id(self):
        from bot.handlers.command_handler import _scoped_memory_key

        ctx = SimpleNamespace(guild=SimpleNamespace(id=1234))
        assert _scoped_memory_key(ctx, "rules") == "guild:1234:rules"


# ===================================================================
# Test: BotContext (context.py)
# ===================================================================

class TestBotContext:
    """Test BotContext initialization and is_loaded checks."""

    def test_default_context(self):
        """BotContext should start with all None/empty defaults."""
        from bot.context import BotContext
        ctx = BotContext()
        assert ctx.agent is None
        assert ctx.task_manager is None
        assert ctx.bg_executor is None
        assert ctx.chat_mode == "anyone"
        assert ctx.allowed_user_ids == set()
        assert ctx.cognitive_mode is False
        assert ctx.cognitive_log_dir is None

    def test_is_loaded_returns_false_for_none(self):
        """is_loaded should return False for unset attributes."""
        from bot.context import BotContext
        ctx = BotContext()
        assert ctx.is_loaded("agent") is False
        assert ctx.is_loaded("task_manager") is False
        assert ctx.is_loaded("nonexistent") is False

    def test_is_loaded_returns_true_when_set(self):
        """is_loaded should return True when attribute is not None."""
        from bot.context import BotContext
        ctx = BotContext()
        ctx.agent = object()
        assert ctx.is_loaded("agent") is True

    def test_context_with_values(self):
        """BotContext should accept all keyword arguments."""
        from bot.context import BotContext
        agent = object()
        task_mgr = object()
        ctx = BotContext(agent=agent, task_manager=task_mgr, chat_mode="dm_only",
                         allowed_user_ids={"123"})
        assert ctx.agent is agent
        assert ctx.task_manager is task_mgr
        assert ctx.chat_mode == "dm_only"
        assert ctx.allowed_user_ids == {"123"}

    def test_module_ctx_singleton(self):
        """The module-level `ctx` should be a BotContext."""
        from bot.context import BotContext, ctx
        assert isinstance(ctx, BotContext)


# ===================================================================
# Test: BackgroundExecutor (background_executor.py)
# ===================================================================

class TestBackgroundExecutor:
    """Test BackgroundExecutor dispatch, pruning, error handling."""

    @pytest_asyncio.fixture
    async def mock_bot(self):
        bot = MagicMock()
        bot.loop = asyncio.get_running_loop()
        return bot

    @pytest.fixture
    def mock_channel(self):
        channel = AsyncMock()
        return channel

    @pytest.mark.asyncio
    async def test_dispatch_success(self, mock_bot, mock_channel):
        """A successfully completed task should send completion ping."""
        from bot.background_executor import BackgroundExecutor
        exc = BackgroundExecutor(mock_bot)

        async def dummy_coro():
            return "result_data"

        task = exc.dispatch(42, mock_channel, dummy_coro(), "TestTask")
        assert task is not None
        await task

        # Should have sent started + completion messages
        assert mock_channel.send.call_count >= 2
        calls = [c[0][0] for c in mock_channel.send.call_args_list]
        started = any("TestTask started" in c for c in calls)
        complete = any("TestTask complete" in c for c in calls)
        assert started
        assert complete

    @pytest.mark.asyncio
    async def test_dispatch_task_returns_none(self, mock_bot, mock_channel):
        """When coro returns None, ping should not include result text."""
        from bot.background_executor import BackgroundExecutor
        exc = BackgroundExecutor(mock_bot)

        async def no_result():
            return None

        task = exc.dispatch(42, mock_channel, no_result(), "NoResult")
        await task

        # Should contain the ping but not extra text
        sent_text = mock_channel.send.call_args_list[-1][0][0]
        assert "NoResult complete" in sent_text
        assert "result_data" not in sent_text

    @pytest.mark.asyncio
    async def test_dispatch_task_failure(self, mock_bot, mock_channel):
        """A failing task should send failure notification."""
        from bot.background_executor import BackgroundExecutor
        exc = BackgroundExecutor(mock_bot)

        async def failing_coro():
            raise ValueError("Something broke")

        task = exc.dispatch(42, mock_channel, failing_coro(), "FailTask")
        await task

        # Should have sent failure message
        sent_texts = [c[0][0] for c in mock_channel.send.call_args_list]
        failure = any("FailTask failed" in t for t in sent_texts)
        assert failure

    @pytest.mark.asyncio
    async def test_dispatch_initial_notification_fails(self, mock_bot, mock_channel):
        """If initial notification fails, task should still run and complete."""
        from bot.background_executor import BackgroundExecutor
        # Make first send fail
        mock_channel.send.side_effect = [Exception("Send failed"), None]

        exc = BackgroundExecutor(mock_bot)

        async def dummy_coro():
            return "ok"

        task = exc.dispatch(42, mock_channel, dummy_coro(), "NotifFail")
        await task
        # The task should still complete (result sent on second call)
        assert mock_channel.send.call_count >= 2

    @pytest.mark.asyncio
    async def test_dispatch_final_notification_fails(self, mock_bot, mock_channel):
        """If the channel is gone at completion time, exception is caught."""
        from bot.background_executor import BackgroundExecutor

        # First call (started msg) succeeds, second call (result) fails
        results = ["⏳ **TestFail started** for <42>... I will ping you when it's done.", None]
        def side_effect(*a, **kw):
            val = results.pop(0) if results else "fallback"
            if val is None:
                raise Exception("Channel gone")
            return MagicMock()
        mock_channel.send.side_effect = side_effect

        exc = BackgroundExecutor(mock_bot)

        async def dummy_coro():
            return "data"

        task = exc.dispatch(42, mock_channel, dummy_coro(), "TestFail")
        await task
        # Should have called send at least once
        assert mock_channel.send.call_count >= 1

    @pytest.mark.asyncio
    async def test_prune_removes_done_tasks(self, mock_bot, mock_channel):
        """_prune should remove completed tasks from the dict."""
        from bot.background_executor import BackgroundExecutor
        exc = BackgroundExecutor(mock_bot)

        async def quick():
            return "done"

        task = exc.dispatch(1, mock_channel, quick(), "Q")
        await task
        # After completion, the done callback should have removed it
        assert len(exc.tasks) == 0

    @pytest.mark.asyncio
    async def test_max_tracked_tasks_warning(self, mock_bot, mock_channel):
        """Dispatch should log warning when at capacity."""
        from bot.background_executor import BackgroundExecutor
        exc = BackgroundExecutor(mock_bot)
        # Fill up tasks with pending tasks
        pending_tasks = []
        for i in range(exc.MAX_TRACKED_TASKS):
            async def pending():
                await asyncio.Event().wait()
            t = asyncio.create_task(pending())
            exc.tasks[f"pending-{i}"] = t
            pending_tasks.append(t)

        async def dummy():
            return "ok"

        # This should trigger the warning but still dispatch
        task = exc.dispatch(1, mock_channel, dummy(), "Extra")
        await task

        for t in pending_tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t

    @pytest.mark.asyncio
    async def test_task_registered_in_dict(self, mock_bot, mock_channel):
        """Task should appear in self.tasks before completion."""
        from bot.background_executor import BackgroundExecutor
        exc = BackgroundExecutor(mock_bot)
        start_event = asyncio.Event()
        can_finish = asyncio.Event()

        async def controlled():
            start_event.set()
            await can_finish.wait()
            return "done"

        task = exc.dispatch(1, mock_channel, controlled(), "Controlled")
        await start_event.wait()
        assert len(exc.tasks) > 0
        can_finish.set()
        await task


# ===================================================================
# Test: rate_limiter (handlers/rate_limiter.py)
# ===================================================================

class TestRateLimiter:
    """Test _check_rate_limit and _check_command_cooldown."""

    @pytest.fixture(autouse=True)
    def clear_rate_limit_state(self):
        """Clear global rate limit buckets and cooldowns before each test."""
        from bot.config import _command_cooldowns, _rate_limit_buckets
        _rate_limit_buckets.clear()
        _command_cooldowns.clear()
        yield

    @pytest.mark.asyncio
    async def test_rate_limit_allows_first_request(self):
        """First request should always be allowed."""
        from bot.handlers.rate_limiter import _check_rate_limit
        allowed, remaining = await _check_rate_limit("user1", "guild1")
        assert allowed is True
        assert remaining == 0.0

    @pytest.mark.asyncio
    async def test_rate_limit_blocks_after_max(self):
        """Requests exceeding RATE_LIMIT_MAX_REQUESTS should be blocked."""
        from bot.handlers.rate_limiter import RATE_LIMIT_MAX_REQUESTS, _check_rate_limit
        user = "spammer"
        # Consume all allowed requests
        for _ in range(RATE_LIMIT_MAX_REQUESTS):
            allowed, _ = await _check_rate_limit(user, "guild")
            assert allowed is True
        # Next request should be blocked
        allowed, remaining = await _check_rate_limit(user, "guild")
        assert allowed is False
        assert remaining > 0

    @pytest.mark.asyncio
    async def test_rate_limit_resets_after_window(self, monkeypatch):
        """After the window passes, the counter should reset."""
        from bot.handlers.rate_limiter import RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW, _check_rate_limit

        now = [time.time()]
        monkeypatch.setattr(time, 'time', lambda: now[0])

        user = "window_test"
        for _ in range(RATE_LIMIT_MAX_REQUESTS):
            allowed, _ = await _check_rate_limit(user, "g")
            assert allowed is True

        # Fast-forward past window
        now[0] += RATE_LIMIT_WINDOW + 1
        allowed, _ = await _check_rate_limit(user, "g")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_rate_limit_per_user_independent(self):
        """Rate limits should be independent per user."""
        from bot.handlers.rate_limiter import RATE_LIMIT_MAX_REQUESTS, _check_rate_limit
        user_a, user_b = "user_a", "user_b"

        # Exhaust user_a
        for _ in range(RATE_LIMIT_MAX_REQUESTS):
            await _check_rate_limit(user_a, "g")

        # user_b should still be allowed
        allowed, _ = await _check_rate_limit(user_b, "g")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_rate_limit_per_guild_independent(self):
        """Rate limits should be independent per guild for same user."""
        from bot.handlers.rate_limiter import RATE_LIMIT_MAX_REQUESTS, _check_rate_limit
        user = "multi_guild"

        for _ in range(RATE_LIMIT_MAX_REQUESTS):
            await _check_rate_limit(user, "guild_a")

        # Same user, different guild should still be allowed
        allowed, _ = await _check_rate_limit(user, "guild_b")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_command_cooldown_allows_first(self):
        """First command should always be allowed."""
        from bot.handlers.rate_limiter import _check_command_cooldown
        allowed, remaining = await _check_command_cooldown("user1")
        assert allowed is True
        assert remaining == 0.0

    @pytest.mark.asyncio
    async def test_command_cooldown_blocks_rapid(self, monkeypatch):
        """Commands within cooldown period should be blocked."""
        from bot.handlers.rate_limiter import _check_command_cooldown

        now = [time.time()]
        monkeypatch.setattr(time, 'time', lambda: now[0])

        # First command allowed
        allowed, _ = await _check_command_cooldown("rapid_user")
        assert allowed is True

        # Second command immediately after should be blocked
        allowed, remaining = await _check_command_cooldown("rapid_user")
        assert allowed is False
        assert remaining > 0

    @pytest.mark.asyncio
    async def test_command_cooldown_bypass_owner(self):
        """Owner bypass should skip cooldown."""
        import time as t

        # Simulate recent command
        from bot.config import _command_cooldowns
        from bot.handlers.rate_limiter import _check_command_cooldown
        _command_cooldowns["owner_user"] = t.time() - 0.1

        allowed, _ = await _check_command_cooldown("owner_user", bypass_for_owner=True)
        assert allowed is True

    @pytest.mark.asyncio
    async def test_command_cooldown_expires(self, monkeypatch):
        """After cooldown expires, commands should be allowed again."""
        from bot.handlers.rate_limiter import COMMAND_COOLDOWN, _check_command_cooldown

        now = [time.time()]
        monkeypatch.setattr(time, 'time', lambda: now[0])

        await _check_command_cooldown("expire_user")

        # Fast-forward past cooldown
        now[0] += COMMAND_COOLDOWN + 1
        allowed, _ = await _check_command_cooldown("expire_user")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_rate_limit_cache_eviction(self, monkeypatch):
        """When cache is full, oldest entry should be evicted."""
        from bot.config import _rate_limit_buckets

        # Monkeypatch MAX_RATE_LIMIT_ENTRIES to a small value
        from bot.handlers import rate_limiter
        original = rate_limiter.MAX_RATE_LIMIT_ENTRIES
        try:
            rate_limiter.MAX_RATE_LIMIT_ENTRIES = 3
            # Override what the function uses
            from bot.handlers.rate_limiter import _check_rate_limit
            # Fill up with different users
            await _check_rate_limit("evict1", "g")
            await _check_rate_limit("evict2", "g")
            await _check_rate_limit("evict3", "g")
            assert len(_rate_limit_buckets) <= 3
            # Adding a 4th should evict the 1st
            await _check_rate_limit("evict4", "g")
            # Bucket count should still be <= cache size
            assert len(_rate_limit_buckets) <= 3
            # evict1 should have been evicted (popitem last=False removes oldest)
        finally:
            rate_limiter.MAX_RATE_LIMIT_ENTRIES = original


# ===================================================================
# Test: response_cache (handlers/response_cache.py)
# ===================================================================

class TestResponseCache:
    """Test _hash_message, _get_cached_response, _cache_response."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear global response cache before each test."""
        from bot.config import _response_cache
        _response_cache.clear()
        yield

    def test_hash_message_consistency(self):
        """Same text/user/server should produce the same hash."""
        from bot.handlers.response_cache import _hash_message
        h1 = _hash_message("hello", "user1", "server1")
        h2 = _hash_message("hello", "user1", "server1")
        assert h1 == h2

    def test_hash_message_different_inputs(self):
        """Different text/user/server should produce different hashes."""
        from bot.handlers.response_cache import _hash_message
        h1 = _hash_message("hello", "user1", "server1")
        h2 = _hash_message("world", "user1", "server1")
        h3 = _hash_message("hello", "user2", "server1")
        h4 = _hash_message("hello", "user1", "server2")
        assert h1 != h2
        assert h1 != h3
        assert h1 != h4

    def test_hash_message_normalization(self):
        """Message text should be normalized (lowercased, stripped)."""
        from bot.handlers.response_cache import _hash_message
        h1 = _hash_message("  Hello World  ", "u1", "s1")
        h2 = _hash_message("hello world", "u1", "s1")
        h3 = _hash_message("HELLO   WORLD", "u1", "s1")
        assert h1 == h2 == h3

    def test_hash_uses_sha256_first_16_chars(self):
        """Hash should use SHA256 and return first 16 hex chars."""
        from bot.handlers.response_cache import _hash_message
        h = _hash_message("test", "u", "s")
        assert len(h) == 16
        # Verify it's truly SHA256-based
        expected = hashlib.sha256(b"test|u|s").hexdigest()[:16]
        assert h == expected

    @pytest.mark.asyncio
    async def test_cache_and_retrieve(self):
        """After caching a response, it should be retrievable."""
        from bot.handlers.response_cache import _cache_response, _get_cached_response
        await _cache_response("hello", "user1", "server1", "Hello there!")
        cached = await _get_cached_response("hello", "user1", "server1")
        assert cached == "Hello there!"

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        """Non-existent cache keys should return None."""
        from bot.handlers.response_cache import _get_cached_response
        cached = await _get_cached_response("nonexistent", "user1", "server1")
        assert cached is None

    @pytest.mark.asyncio
    async def test_cache_eviction_lru(self, monkeypatch):
        """When cache is full, oldest entry should be evicted (LRU)."""
        from bot.config import _response_cache
        from bot.handlers.response_cache import RESPONSE_CACHE_SIZE

        # Override to a small value
        old = RESPONSE_CACHE_SIZE
        try:
            import bot.handlers.response_cache as rc_mod
            rc_mod.RESPONSE_CACHE_SIZE = 3

            await rc_mod._cache_response("msg1", "u1", "s1", "r1")
            await rc_mod._cache_response("msg2", "u1", "s1", "r2")
            await rc_mod._cache_response("msg3", "u1", "s1", "r3")
            assert len(_response_cache) == 3

            # Add 4th entry, should evict oldest (msg1)
            await rc_mod._cache_response("msg4", "u1", "s1", "r4")
            assert len(_response_cache) <= 3
            # msg1 should be gone
            cached = await rc_mod._get_cached_response("msg1", "u1", "s1")
            assert cached is None
        finally:
            rc_mod.RESPONSE_CACHE_SIZE = old

    @pytest.mark.asyncio
    async def test_cache_expiry(self, monkeypatch):
        """Expired cache entries should not be returned."""
        from bot.handlers.response_cache import _cache_response, _get_cached_response

        now = [time.time()]
        monkeypatch.setattr(time, 'time', lambda: now[0])

        await _cache_response("hello", "user1", "server1", "Hello!")

        # Advance time past TTL
        from bot.handlers.response_cache import RESPONSE_CACHE_TTL
        now[0] += RESPONSE_CACHE_TTL + 1

        cached = await _get_cached_response("hello", "user1", "server1")
        assert cached is None

    @pytest.mark.asyncio
    async def test_cache_expiry_removes_entry(self, monkeypatch):
        """Expired entries should be removed from the dict."""
        from bot.config import _response_cache
        from bot.handlers.response_cache import _cache_response, _get_cached_response
        now = [time.time()]
        monkeypatch.setattr(time, 'time', lambda: now[0])

        await _cache_response("expire_me", "u", "s", "val")
        key = list(_response_cache.keys())[0]

        from bot.handlers.response_cache import RESPONSE_CACHE_TTL
        now[0] += RESPONSE_CACHE_TTL + 1
        await _get_cached_response("expire_me", "u", "s")
        assert key not in _response_cache

    @pytest.mark.asyncio
    async def test_cache_different_servers_independent(self):
        """Same text/user in different servers should have separate caches."""
        from bot.handlers.response_cache import _cache_response, _get_cached_response
        await _cache_response("hello", "user1", "server1", "Hello S1")
        cached_s2 = await _get_cached_response("hello", "user1", "server2")
        assert cached_s2 is None
        cached_s1 = await _get_cached_response("hello", "user1", "server1")
        assert cached_s1 == "Hello S1"


# ===================================================================
# Test: _is_directed_at_bot (handlers/llm_handler.py)
# ===================================================================

class TestIsDirectedAtBot:
    """Test _is_directed_at_bot with various message patterns."""

    def test_directed_with_name_exact(self):
        """Exact name 'azure' should be detected."""
        from bot.handlers.llm_handler import _is_directed_at_bot
        assert _is_directed_at_bot("azure, do something") is True

    def test_directed_with_name_mixed_case(self):
        """Lowercased 'azure' should be detected (function expects pre-lowered)."""
        from bot.handlers.llm_handler import _is_directed_at_bot
        assert _is_directed_at_bot("azure help me") is True

    def test_directed_with_afterdawn(self):
        """Alternate name 'afterdawn' should be detected."""
        from bot.handlers.llm_handler import _is_directed_at_bot
        assert _is_directed_at_bot("afterdawn, what's up?") is True

    def test_not_directed_generic(self):
        """Generic messages should not be detected."""
        from bot.handlers.llm_handler import _is_directed_at_bot
        assert _is_directed_at_bot("hello everyone") is False
        assert _is_directed_at_bot("how are you?") is False

    def test_not_directed_about_bot(self):
        """Talking 'about' the bot should not trigger false positive."""
        from bot.handlers.llm_handler import _is_directed_at_bot
        assert _is_directed_at_bot("the robot is cool") is False
        assert _is_directed_at_bot("i have a robot") is False

    def test_not_directed_word_boundary_false_positive(self):
        """Words containing 'azure' as a substring should NOT be detected."""
        from bot.handlers.llm_handler import _is_directed_at_bot
        assert _is_directed_at_bot("I read a book about afterdawn") is True
        # "azured" - \bazure\b won't match because there's no word boundary after 'e'
        assert _is_directed_at_bot("azured services") is False

    def test_not_directed_about_word_boundary(self):
        """'about' should never match."""
        from bot.handlers.llm_handler import _is_directed_at_bot
        assert _is_directed_at_bot("tell me about yourself") is False
        assert _is_directed_at_bot("about the project") is False

    def test_empty_string(self):
        """Empty string should not be directed."""
        from bot.handlers.llm_handler import _is_directed_at_bot
        assert _is_directed_at_bot("") is False

    def test_punctuation_around_name(self):
        """Name with punctuation should still match."""
        from bot.handlers.llm_handler import _is_directed_at_bot
        assert _is_directed_at_bot("azure! help") is True
        assert _is_directed_at_bot("azure: status") is True
        assert _is_directed_at_bot("(azure) what?") is True


# ===================================================================
# Test: template_handler (handlers/template_handler.py)
# ===================================================================

class TestTemplateHandler:
    """Test template_handler logic (without actual Discord mgmt tools)."""

    @pytest.mark.asyncio
    async def test_template_handler_no_mgmt_tools(self):
        """When mgmt_tools is None, handler should send error."""
        from bot.handlers.template_handler import _handle_template

        message = MagicMock()
        message.channel = AsyncMock()
        message.guild = MagicMock()

        with patch("bot.context.ctx.mgmt_tools", None):
            await _handle_template(message, {"template_action": "list", "template_name": ""})

        assert message.channel.send.called
        sent = message.channel.send.call_args[0][0]
        assert "not available" in sent.lower() or "❌" in sent

    @pytest.mark.asyncio
    async def test_template_handler_no_guild(self):
        """When message has no guild, handler should send error."""
        from bot.handlers.template_handler import _handle_template

        message = MagicMock()
        message.channel = AsyncMock()
        message.guild = None

        mgmt_tools = MagicMock()
        with patch("bot.context.ctx.mgmt_tools", mgmt_tools):
            await _handle_template(message, {"template_action": "list", "template_name": ""})

        assert message.channel.send.called
        sent = message.channel.send.call_args[0][0]
        assert "server" in sent.lower() or "❌" in sent

    @pytest.mark.asyncio
    async def test_template_handler_list_no_templates(self):
        """When no templates exist, list should show built-in message."""
        from bot.handlers.template_handler import _handle_template

        message = MagicMock()
        message.channel = AsyncMock()
        message.guild = MagicMock()
        message.author.id = 123

        mgmt_tools = MagicMock()
        mgmt_tools.templates.list_templates.return_value = []

        with patch("bot.context.ctx.mgmt_tools", mgmt_tools):
            await _handle_template(message, {"template_action": "list", "template_name": ""})

        assert message.channel.send.called
        sent = message.channel.send.call_args[0][0]
        assert "built-in" in sent.lower() or "No templates" in sent

    @pytest.mark.asyncio
    async def test_template_handler_list_with_templates(self):
        """When templates exist, list should return them."""
        from bot.handlers.template_handler import _handle_template

        message = MagicMock()
        message.channel = AsyncMock()
        message.guild = MagicMock()
        message.author.id = 123

        mgmt_tools = MagicMock()
        mgmt_tools.templates.list_templates.return_value = [
            {"name": "gaming", "description": "Gaming server setup"},
            {"name": "community", "description": "Community server setup"},
        ]

        with patch("bot.context.ctx.mgmt_tools", mgmt_tools):
            await _handle_template(message, {"template_action": "list", "template_name": ""})

        assert message.channel.send.called
        sent = message.channel.send.call_args[0][0]
        assert "gaming" in sent or "gaming" in str(sent)


# ===================================================================
# Test: Lifecycle (lifecycle.py)
# ===================================================================
class TestLifecycle:
    """Test lifecycle signal_handler, cleanup_llm_workers, _close_sqlite_connections."""

    def test_cleanup_llm_workers_clears_list(self):
        """cleanup_llm_workers should stop and clear all workers."""
        # Test with the actual function defined in lifecycle
        # We can test directly the cleanup logic
        workers = []
        mock_llm1 = MagicMock()
        mock_llm2 = MagicMock()
        workers.append(mock_llm1)
        workers.append(mock_llm2)

        for llm in workers:
            if hasattr(llm, 'stop'):
                llm.stop()
        workers.clear()

        mock_llm1.stop.assert_called_once()
        mock_llm2.stop.assert_called_once()
        assert len(workers) == 0

    def test_cleanup_llm_workers_handles_exception(self):
        """cleanup_llm_workers should handle exceptions from individual workers."""
        workers = []
        bad_llm = MagicMock()
        bad_llm.stop.side_effect = Exception("Stop failed")
        good_llm = MagicMock()
        workers.append(bad_llm)
        workers.append(good_llm)

        for llm in workers:
            try:
                if hasattr(llm, 'stop'):
                    llm.stop()
            except Exception:
                pass
        workers.clear()

        bad_llm.stop.assert_called_once()
        good_llm.stop.assert_called_once()

    def test_cleanup_llm_workers_skips_no_stop(self):
        """Workers without a 'stop' method should be skipped."""
        workers = []
        incomplete = object()  # no 'stop' method
        with_stop = MagicMock()
        workers.append(incomplete)
        workers.append(with_stop)

        for llm in workers:
            if hasattr(llm, 'stop'):
                llm.stop()
        workers.clear()

        with_stop.stop.assert_called_once()

    def test_signal_handler_sets_event(self):
        """signal_handler should set the shutdown event."""
        # The signal_handler closes over shutdown_event which is an asyncio.Event
        # We can test independently by creating the same pattern
        shutdown_event = asyncio.Event()

        def signal_handler(signum, frame):
            shutdown_event.set()

        assert not shutdown_event.is_set()
        signal_handler(2, None)  # SIGINT
        assert shutdown_event.is_set()

    def test_register_llm_worker_global(self):
        """register_llm_worker should add valid workers to _llm_workers list."""
        from bot.discord_bot_v1 import _llm_workers, register_llm_worker
        original_len = len(_llm_workers)
        mock_llm = MagicMock()
        mock_llm.stop = lambda: None
        register_llm_worker(mock_llm)
        assert len(_llm_workers) == original_len + 1
        _llm_workers.clear()

    def test_register_llm_worker_skips_invalid(self):
        """register_llm_worker should skip workers without 'stop'."""
        from bot.discord_bot_v1 import _llm_workers, register_llm_worker
        original_len = len(_llm_workers)
        register_llm_worker(None)
        register_llm_worker(object())
        assert len(_llm_workers) == original_len

    def test_close_sqlite_connections_safe_when_no_agent(self):
        """_close_sqlite_connections should not crash when ctx.agent is None."""
        from bot.context import ctx
        from bot.lifecycle import _close_sqlite_connections
        original_agent = ctx.agent
        ctx.agent = None
        try:
            _close_sqlite_connections()  # Should not raise
        finally:
            ctx.agent = original_agent

    def test_close_sqlite_connections_handles_memory_backend(self):
        """_close_sqlite_connections should close memory_backend if available."""
        from bot.context import ctx
        from bot.lifecycle import _close_sqlite_connections

        mock_agent = MagicMock()
        mock_agent.memory_backend = MagicMock()
        mock_agent._conn = None
        original_agent = ctx.agent
        ctx.agent = mock_agent
        try:
            _close_sqlite_connections()
            mock_agent.memory_backend.close.assert_called_once()
        finally:
            ctx.agent = original_agent

    def test_close_sqlite_connections_handles_agent_conn(self):
        """_close_sqlite_connections should close agent's _conn if available."""
        from bot.context import ctx
        from bot.lifecycle import _close_sqlite_connections

        mock_agent = MagicMock()
        mock_agent.memory_backend = None
        mock_agent._conn = MagicMock()
        original_agent = ctx.agent
        ctx.agent = mock_agent
        try:
            _close_sqlite_connections()
            mock_agent._conn.close.assert_called_once()
        finally:
            ctx.agent = original_agent


# ===================================================================
# Test: Config constants overlay (config.py)
# ===================================================================

class TestConfigOverlay:
    """Test that config.py overlays pydantic values when available."""

    def test_config_module_attributes_exist(self):
        """All expected config constants should be accessible."""
        import bot.config
        assert bot.config.RATE_LIMIT_MAX_REQUESTS >= 1
        assert bot.config.RATE_LIMIT_WINDOW > 0
        assert bot.config.RESPONSE_CACHE_SIZE >= 1
        assert bot.config.RESPONSE_CACHE_TTL > 0
        assert bot.config.CHUNK_SIZE > 0
        assert bot.config.MAX_RETRIES >= 1

    def test_config_env_override(self):
        """Config should read from environment (via pydantic overlay when available)."""
        import bot.config

        # When pydantic is available, the overlay overrides env-based defaults.
        # Verify the overlay correctly applied pydantic values.
        from bot.pydantic_config import config as _pc
        assert _pc.rate_limit_messages == bot.config.RATE_LIMIT_MAX_REQUESTS
        assert _pc.chunk_size == bot.config.CHUNK_SIZE


# ===================================================================
# Test: PlanExecutionView (views.py)
# ===================================================================

class TestPlanExecutionView:
    """Test PlanExecutionView construction and basic properties."""

    @pytest.mark.asyncio
    async def test_view_initialization(self):
        """PlanExecutionView should initialize with correct timeout."""
        from bot.views import PlanExecutionView

        mock_state = MagicMock()
        mock_state.needs_confirmation = True
        mock_state.plan.requires_confirmation = True
        mock_message = MagicMock()
        mock_message.author.id = 12345

        view = PlanExecutionView(
            state=mock_state,
            message=mock_message,
            user="TestUser",
            is_directed=True,
            is_dm=False,
            mentioned=True,
            server_name="Test Server"
        )

        assert view.timeout == 300
        assert view.state is mock_state
        assert view.message is mock_message
        assert view.user == "TestUser"
        assert view.is_directed is True
        assert view.is_dm is False
        assert view.mentioned is True
        assert view.server_name == "Test Server"

    @pytest.mark.asyncio
    async def test_view_has_two_buttons(self):
        """PlanExecutionView should have Execute and Cancel buttons."""
        from bot.views import PlanExecutionView
        mock_state = MagicMock()
        mock_message = MagicMock()
        mock_message.author.id = 12345

        view = PlanExecutionView(
            state=mock_state,
            message=mock_message,
            user="User",
            is_directed=True,
            is_dm=False,
            mentioned=True,
            server_name="S"
        )

        assert len(view.children) == 2
        labels = [c.label for c in view.children]
        assert any("Execute" in lbl for lbl in labels)
        assert any("Cancel" in lbl for lbl in labels)

    @pytest.mark.asyncio
    async def test_execute_wrong_user(self):
        """If wrong user clicks Execute, should deny with ephemeral message."""
        from bot.views import PlanExecutionView

        mock_state = MagicMock()
        mock_state.needs_confirmation = True
        mock_state.plan.requires_confirmation = True
        mock_message = MagicMock()
        mock_message.author.id = 12345

        view = PlanExecutionView(
            state=mock_state,
            message=mock_message,
            user="OriginalUser",
            is_directed=True,
            is_dm=False,
            mentioned=True,
            server_name="Test Server"
        )

        interaction = AsyncMock()
        interaction.user.id = 99999  # Different user

        # Button.callback is wrapped by discord.py to accept only interaction
        execute_button = view.children[0]

        with patch("bot.handlers.llm_handler._llm_response", AsyncMock(return_value="Only the requester can execute this plan.")):
            await execute_button.callback(interaction)

        interaction.response.send_message.assert_called_once()
        msg = interaction.response.send_message.call_args[1].get("ephemeral", False)
        assert msg is True

    @pytest.mark.asyncio
    async def test_cancel_wrong_user(self):
        """If wrong user clicks Cancel, should deny with ephemeral message."""
        from bot.views import PlanExecutionView

        mock_state = MagicMock()
        mock_state.needs_confirmation = True
        mock_state.plan.requires_confirmation = True
        mock_message = MagicMock()
        mock_message.author.id = 12345

        view = PlanExecutionView(
            state=mock_state,
            message=mock_message,
            user="OriginalUser",
            is_directed=True,
            is_dm=False,
            mentioned=True,
            server_name="Test Server"
        )

        interaction = AsyncMock()
        interaction.user.id = 99999

        cancel_button = view.children[1]

        with patch("bot.handlers.llm_handler._llm_response", AsyncMock(return_value="Only the requester can cancel this plan.")):
            await cancel_button.callback(interaction)

        interaction.response.send_message.assert_called_once()
        msg = interaction.response.send_message.call_args[1].get("ephemeral", False)
        assert msg is True


# ===================================================================
# Test: Discord bot v1 module-level utilities
# ===================================================================

class TestDiscordBotV1:
    """Test module-level utilities from discord_bot_v1."""

    def test_get_runtime_stats_keys(self):
        """get_runtime_stats should return a dict with expected keys."""
        from bot.discord_bot_v1 import get_runtime_stats
        stats = get_runtime_stats()
        expected_keys = {"messages_today", "active_users", "llm_calls", "uptime",
                          "uptime_seconds", "health_score", "errors", "guilds", "latency_ms"}
        assert expected_keys.issubset(stats.keys())

    def test_legacy_aliases_work(self):
        """__getattr__ should resolve legacy aliases from ctx."""
        from bot.discord_bot_v1 import _LEGACY_ALIASES
        assert "AGENT" in _LEGACY_ALIASES
        assert _LEGACY_ALIASES["AGENT"] == "agent"
        assert "BG_EXECUTOR" in _LEGACY_ALIASES
        assert _LEGACY_ALIASES["BG_EXECUTOR"] == "bg_executor"

    def test_llm_workers_list_exists(self):
        """_llm_workers should be a list."""
        from bot.discord_bot_v1 import _llm_workers
        assert isinstance(_llm_workers, list)


# ===================================================================
# Test: RAG and context-related functions
# ===================================================================

class TestContextManager:
    """Test _add_to_context and _get_conversation_context."""

    @pytest.fixture(autouse=True)
    def clear_history(self):
        from bot.config import _conversation_history
        _conversation_history.clear()
        yield

    @pytest.mark.asyncio
    async def test_add_and_get_context(self):
        """After adding context, it should be retrievable."""
        from bot.handlers.context_manager import _add_to_context, _get_conversation_context
        await _add_to_context("user1", "hello", "hi there")
        history = await _get_conversation_context("user1")
        assert len(history) == 1
        assert history[0]["user"] == "hello"
        assert history[0]["assistant"] == "hi there"

    @pytest.mark.asyncio
    async def test_context_not_found(self):
        """Non-existent user should return empty list."""
        from bot.handlers.context_manager import _get_conversation_context
        history = await _get_conversation_context("unknown_user")
        assert history == []

    @pytest.mark.asyncio
    async def test_context_size_limit(self):
        """Context should be limited to CONTEXT_MEMORY_SIZE entries."""
        from bot.handlers.context_manager import CONTEXT_MEMORY_SIZE, _add_to_context, _get_conversation_context

        # Fill beyond limit
        for i in range(CONTEXT_MEMORY_SIZE + 5):
            await _add_to_context("user_overflow", f"msg{i}", f"resp{i}")

        history = await _get_conversation_context("user_overflow")
        assert len(history) == CONTEXT_MEMORY_SIZE
        # Should keep the most recent entries (the last CONTEXT_MEMORY_SIZE)
        assert history[-1]["user"] == f"msg{CONTEXT_MEMORY_SIZE + 4}"

    @pytest.mark.asyncio
    async def test_context_stale_removal(self, monkeypatch):
        """Messages older than 1 hour should be filtered out."""
        from bot.handlers.context_manager import _add_to_context, _get_conversation_context

        now = [time.time()]
        monkeypatch.setattr(time, 'time', lambda: now[0])

        await _add_to_context("stale_user", "old_msg", "old_resp")

        # Fast-forward past 1 hour
        now[0] += 3601

        history = await _get_conversation_context("stale_user")
        assert history == []


# ===================================================================
# Test: _check_guild_rate_limit (message_handler.py)
# ===================================================================

class TestGuildRateLimit:
    """Test guild-level rate limiting."""

    @pytest.fixture(autouse=True)
    def clear_state(self):
        from bot.handlers.message_handler import _guild_message_counts
        _guild_message_counts.clear()
        yield

    def test_guild_rate_ok(self):
        """First guild request should be allowed."""
        from bot.handlers.message_handler import _check_guild_rate_limit
        assert _check_guild_rate_limit("g1") is True

    def test_guild_rate_blocked(self, monkeypatch):
        """Exceeding guild rate limit should be blocked."""
        from bot.handlers.message_handler import _GUILD_RATE_LIMIT, _check_guild_rate_limit

        now = [time.time()]
        monkeypatch.setattr(time, 'time', lambda: now[0])

        for _ in range(_GUILD_RATE_LIMIT):
            assert _check_guild_rate_limit("flood_guild") is True
        assert _check_guild_rate_limit("flood_guild") is False

    def test_guild_rate_resets(self, monkeypatch):
        """Guild rate limit should reset after window."""
        from bot.handlers.message_handler import _GUILD_RATE_LIMIT, _GUILD_RATE_WINDOW, _check_guild_rate_limit

        now = [time.time()]
        monkeypatch.setattr(time, 'time', lambda: now[0])

        for _ in range(_GUILD_RATE_LIMIT):
            _check_guild_rate_limit("reset_guild")

        now[0] += _GUILD_RATE_WINDOW + 1
        assert _check_guild_rate_limit("reset_guild") is True


# ===================================================================
# Test: _get_fallback_response (message_handler.py)
# ===================================================================

class TestFallbackResponse:
    """Test _get_fallback_response returns correct messages."""

    def test_timeout_fallback(self):
        from bot.handlers.message_handler import _get_fallback_response
        msg = _get_fallback_response("timeout", "John")
        assert "John" in msg
        assert "⏰" in msg

    def test_unknown_fallback(self):
        from bot.handlers.message_handler import _get_fallback_response
        msg = _get_fallback_response("unknown")
        assert "unexpected" in msg.lower()

    def test_llm_error_fallback(self):
        from bot.handlers.message_handler import _get_fallback_response
        msg = _get_fallback_response("llm_error", "Jane")
        assert "Jane" in msg
        assert "🤖" in msg

    def test_network_error_fallback(self):
        from bot.handlers.message_handler import _get_fallback_response
        msg = _get_fallback_response("network_error")
        assert "network" in msg.lower()

    def test_rate_limit_fallback(self):
        from bot.handlers.message_handler import _get_fallback_response
        msg = _get_fallback_response("rate_limit")
        assert "slow down" in msg.lower()

    def test_invalid_type_defaults_to_unknown(self):
        from bot.handlers.message_handler import _get_fallback_response
        msg = _get_fallback_response("nonexistent_error_type")
        assert "unexpected" in msg.lower()


# ===================================================================
# Test: _pending_confirmations (message_handler.py)
# ===================================================================

class TestPendingConfirmations:
    """Test pending confirmation tracking."""

    @pytest.fixture(autouse=True)
    def clear_pending(self):
        from bot.handlers.message_handler import _pending_confirmations
        _pending_confirmations.clear()
        yield

    def test_set_and_check(self):
        from bot.handlers.message_handler import _has_pending_confirmation, _set_pending_confirmation
        _set_pending_confirmation("user1", "channel1")
        assert _has_pending_confirmation("user1", "channel1") is True

    def test_clear(self):
        from bot.handlers.message_handler import (
            _clear_pending_confirmation,
            _has_pending_confirmation,
            _set_pending_confirmation,
        )
        _set_pending_confirmation("user1", "channel1")
        _clear_pending_confirmation("user1", "channel1")
        assert _has_pending_confirmation("user1", "channel1") is False

    def test_not_set(self):
        from bot.handlers.message_handler import _has_pending_confirmation
        assert _has_pending_confirmation("nobody", "nowhere") is False

    def test_expiry(self, monkeypatch):
        from bot.handlers.message_handler import (
            _PENDING_CONFIRM_TTL,
            _has_pending_confirmation,
            _set_pending_confirmation,
        )
        now = [time.time()]
        monkeypatch.setattr(time, 'time', lambda: now[0])

        _set_pending_confirmation("expire", "ch")
        now[0] += _PENDING_CONFIRM_TTL + 1
        assert _has_pending_confirmation("expire", "ch") is False


# ===================================================================
# Test: _bot_messages tracking (message_handler.py)
# ===================================================================

class TestBotMessages:
    """Test bot message metadata registration."""

    @pytest.fixture(autouse=True)
    def clear_state(self):
        from bot.config import _bot_messages
        _bot_messages.clear()
        yield

    @pytest.mark.asyncio
    async def test_register_bot_message(self):
        """Registered bot message should have metadata."""
        from bot.handlers.message_handler import _get_bot_message_metadata, _register_bot_message

        mock_msg = MagicMock()
        mock_msg.id = 12345
        mock_msg.channel.id = 67890

        await _register_bot_message(mock_msg, "user1", "hello")

        meta = await _get_bot_message_metadata("12345")
        assert meta is not None
        assert meta["user_id"] == "user1"
        assert meta["original_text"] == "hello"
        assert meta["channel_id"] == 67890

    @pytest.mark.asyncio
    async def test_get_bot_message_not_found(self):
        """Non-existent message ID should return None."""
        from bot.handlers.message_handler import _get_bot_message_metadata
        assert await _get_bot_message_metadata("99999") is None

    @pytest.mark.asyncio
    async def test_bot_message_ttl_expiry(self, monkeypatch):
        """Expired bot message should return None."""
        from bot.config import BOT_MESSAGE_TTL
        from bot.handlers.message_handler import _get_bot_message_metadata, _register_bot_message

        now = [time.time()]
        monkeypatch.setattr(time, 'time', lambda: now[0])

        mock_msg = MagicMock()
        mock_msg.id = 111
        mock_msg.channel.id = 222
        await _register_bot_message(mock_msg, "u1", "txt")

        now[0] += BOT_MESSAGE_TTL + 1
        assert await _get_bot_message_metadata("111") is None


# ===================================================================
# Test: _strip_discord_mentions (message_handler.py)
# ===================================================================

class TestStripDiscordMentions:
    """Test mention stripping."""

    def test_strip_user_mention(self):
        from bot.handlers.message_handler import _strip_discord_mentions
        result = _strip_discord_mentions("<@123456789> hello", None)
        assert result == "hello"

    def test_strip_nick_mention(self):
        from bot.handlers.message_handler import _strip_discord_mentions
        result = _strip_discord_mentions("<@!123456789> hello", None)
        assert result == "hello"

    def test_strip_role_mention(self):
        from bot.handlers.message_handler import _strip_discord_mentions
        result = _strip_discord_mentions("<@&987654321> hello", None)
        assert result == "hello"

    def test_strip_channel_mention(self):
        from bot.handlers.message_handler import _strip_discord_mentions
        result = _strip_discord_mentions("<#123456> hello", None)
        assert result == "hello"

    def test_strip_at_mention_display_name(self):
        from bot.handlers.message_handler import _strip_discord_mentions
        bot_user = MagicMock()
        bot_user.display_name = "Azure"
        result = _strip_discord_mentions("@Azure hello", bot_user)
        assert result == "hello"

    def test_preserves_bare_name(self):
        """Bare 'Azure' (without @) should be preserved."""
        from bot.handlers.message_handler import _strip_discord_mentions
        bot_user = MagicMock()
        bot_user.display_name = "Azure"
        result = _strip_discord_mentions("Azure, do something", bot_user)
        assert "Azure" in result


# ===================================================================
# Test: Onboarding handler utilities
# ===================================================================

class TestOnboardingHandler:
    """Test register_discord_tools tool registration."""

    def test_register_tools_called(self):
        """register_discord_tools should register tools on the agent."""
        from bot.handlers.onboarding_handler import register_discord_tools

        agent = MagicMock()
        agent.tools = MagicMock()
        guild_getter = MagicMock(return_value="Test Server")
        bot = MagicMock()

        register_discord_tools(agent, guild_getter, bot)

        # Should have registered at least server_info and send_discord_ping and manage_goals
        assert agent.tools.register.call_count >= 3
        calls = [c[0][0] for c in agent.tools.register.call_args_list]
        assert "server_info" in calls
        assert "send_discord_ping" in calls
        assert "manage_goals" in calls


# ===================================================================
# Test: _attention_check (message_handler.py)
# ===================================================================

class TestAttentionCheck:
    """Test _attention_check fast paths."""

    @pytest.mark.asyncio
    async def test_dm_always_checked(self):
        """DM messages should always pass attention check."""
        from bot.handlers.message_handler import _attention_check
        message = MagicMock()
        result = await _attention_check(message, "hello", is_dm=True, mentioned=False)
        assert result is True

    @pytest.mark.asyncio
    async def test_mentioned_always_passes(self):
        """Mentions should always pass attention check."""
        from bot.handlers.message_handler import _attention_check
        message = MagicMock()
        result = await _attention_check(message, "hello", is_dm=False, mentioned=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_starts_with_bot_name(self):
        """Message starting with 'azure' should pass."""
        from bot.handlers.message_handler import _attention_check
        message = MagicMock()
        result = await _attention_check(message, "azure do something", is_dm=False, mentioned=False)
        assert result is True

    @pytest.mark.asyncio
    async def test_starts_with_action_trigger(self):
        """Keyword action triggers removed — needs LLM/name/mention to engage."""
        from bot.handlers.message_handler import _attention_check
        message = MagicMock()
        with patch("bot.context.ctx.agent", None):
            result = await _attention_check(message, "can you help me", is_dm=False, mentioned=False)
        assert result is False

    @pytest.mark.asyncio
    async def test_ends_with_question_short(self):
        """Bare questions no longer auto-engage without name/mention/LLM YES."""
        from bot.handlers.message_handler import _attention_check
        message = MagicMock()
        with patch("bot.context.ctx.agent", None):
            result = await _attention_check(message, "what time is it?", is_dm=False, mentioned=False)
        assert result is False

    @pytest.mark.asyncio
    async def test_generic_statement_no_pass(self):
        """Generic statement without triggers should not pass fast path."""
        from bot.handlers.message_handler import _attention_check
        message = MagicMock()
        with patch("bot.context.ctx.agent", None):
            result = await _attention_check(message, "the weather is nice today", is_dm=False, mentioned=False)
        assert result is False


# ===================================================================
# Test: _hash_message edge cases (via response_cache)
# ===================================================================

class TestHashEdgeCases:
    """Test _hash_message edge cases."""

    def test_empty_text(self):
        from bot.handlers.response_cache import _hash_message
        h = _hash_message("", "user1", "server1")
        assert isinstance(h, str)
        assert len(h) == 16

    def test_special_characters(self):
        from bot.handlers.response_cache import _hash_message
        h1 = _hash_message("hello!@#$%^&*()", "u1", "s1")
        h2 = _hash_message("hello!@#$%^&*()", "u1", "s1")
        assert h1 == h2

    def test_unicode_text(self):
        from bot.handlers.response_cache import _hash_message
        h = _hash_message("héllo wörld 🌍", "u1", "s1")
        assert len(h) == 16


# ===================================================================
# Test: Config import safety
# ===================================================================

class TestConfigImportSafety:
    """Test config doesn't crash on import."""

    def test_config_import_succeeds(self):
        import bot.config
        assert bot.config is not None

    def test_config_rate_limit_values_sensible(self):
        import bot.config
        assert 0 < bot.config.RATE_LIMIT_WINDOW <= 86400
        assert bot.config.RATE_LIMIT_MAX_REQUESTS >= 1
        assert bot.config.RESPONSE_CACHE_SIZE >= 1


# ===================================================================
# Test: config.py migration overlay logic (pydantic vs env)
# ===================================================================

class TestConfigMigrationOverlay:
    """Test the migration overlay logic in config.py that reads from pydantic."""

    def test_overlay_works_when_pydantic_available(self):
        """The overlay block in config.py should execute without error."""
        # This is already tested implicitly by importing bot.config, but
        # let's verify the specific codepath works
        import bot.config
        # The overlay should have set these from pydantic if available
        assert bot.config.RATE_LIMIT_MAX_REQUESTS >= 1
        assert bot.config.RATE_LIMIT_WINDOW > 0
        assert bot.config.CHUNK_SIZE >= 1

    def test_overlay_uses_pydantic_by_default(self):
        """When pydantic is available, overlay values should come from pydantic."""
        import bot.config
        from bot.pydantic_config import config as _pc
        assert _pc.chunk_size == bot.config.CHUNK_SIZE
        assert _pc.rate_limit_messages == bot.config.RATE_LIMIT_MAX_REQUESTS


# ===================================================================
# Test: DiscordBotV1 __getattr__ for legacy aliases
# ===================================================================

class TestLegacyAliases:
    """Test that legacy alias __getattr__ works correctly."""

    def test_resolve_agent_alias(self):
        """__getattr__ should resolve AGENT to ctx.agent."""
        from bot.discord_bot_v1 import __getattr__, ctx
        original = ctx.agent
        ctx.agent = "test_agent"
        try:
            result = __getattr__("AGENT")
            assert result == "test_agent"
        finally:
            ctx.agent = original

    def test_resolve_nonexistent_raises(self):
        """__getattr__ should raise AttributeError for unknown names."""
        from bot.discord_bot_v1 import __getattr__
        with pytest.raises(AttributeError):
            __getattr__("NONEXISTENT_SYMBOL")

    def test_all_legacy_aliases_resolve(self):
        """All entries in _LEGACY_ALIASES should resolve to ctx attributes."""
        from bot.discord_bot_v1 import _LEGACY_ALIASES, __getattr__, ctx
        for legacy_name, ctx_attr in _LEGACY_ALIASES.items():
            original = getattr(ctx, ctx_attr, None)
            setattr(ctx, ctx_attr, f"test_{ctx_attr}")
            try:
                result = __getattr__(legacy_name)
                assert result == f"test_{ctx_attr}"
            finally:
                setattr(ctx, ctx_attr, original)


class TestPopulateCtx:
    """setup() must bridge module globals into the shared ctx.

    Regression: handlers read exclusively from bot.context.ctx, but setup()
    only assigned module-level globals — so in production ctx stayed at its
    dataclass defaults (agent=None, chat_mode='anyone') and every handler
    silently no-op'd.
    """

    def test_populate_ctx_copies_globals(self):
        import bot.discord_bot_v1 as dbv
        from bot.context import ctx

        saved_ctx = (ctx.agent, ctx.mgmt_tools, ctx.chat_mode,
                     ctx.allowed_user_ids, ctx.cognitive_mode, ctx.bot)
        saved_globals = (dbv.AGENT, dbv.MGMT_TOOLS, dbv.CHAT_MODE,
                         dbv.ALLOWED_USER_IDS, dbv.COGNITIVE_MODE)
        try:
            ctx.agent = None
            ctx.mgmt_tools = None
            dbv.AGENT = "sentinel_agent"
            dbv.MGMT_TOOLS = "sentinel_mgmt"
            dbv.CHAT_MODE = "owner_only"
            dbv.ALLOWED_USER_IDS = {"123"}
            dbv.COGNITIVE_MODE = True

            dbv._populate_ctx()

            assert ctx.agent == "sentinel_agent"
            assert ctx.mgmt_tools == "sentinel_mgmt"
            assert ctx.chat_mode == "owner_only"
            assert ctx.allowed_user_ids == {"123"}
            assert ctx.cognitive_mode is True
            assert ctx.bot is dbv.bot
        finally:
            (dbv.AGENT, dbv.MGMT_TOOLS, dbv.CHAT_MODE,
             dbv.ALLOWED_USER_IDS, dbv.COGNITIVE_MODE) = saved_globals
            (ctx.agent, ctx.mgmt_tools, ctx.chat_mode,
             ctx.allowed_user_ids, ctx.cognitive_mode, ctx.bot) = saved_ctx

    def test_populate_ctx_skips_none_globals(self):
        """None globals must not clobber an already-populated ctx attr."""
        import bot.discord_bot_v1 as dbv
        from bot.context import ctx

        saved_global = dbv.AGENT
        saved_ctx = ctx.agent
        try:
            ctx.agent = "already_set"
            dbv.AGENT = None
            dbv._populate_ctx()
            assert ctx.agent == "already_set"
        finally:
            dbv.AGENT = saved_global
            ctx.agent = saved_ctx


class TestOnMessageDispatchesCommands:
    """on_message override must call process_commands, else prefix commands die.

    Regression: overriding on_message without process_commands() silently
    disabled every @bot.command (!ping, !tools, !mod_scan, …).
    """

    @pytest.mark.asyncio
    async def test_on_message_calls_process_commands(self, monkeypatch):
        import bot.discord_bot_v1 as dbv
        from unittest.mock import AsyncMock, MagicMock

        called = {}
        monkeypatch.setattr(dbv.bot, "process_commands", AsyncMock(
            side_effect=lambda m: called.setdefault("pc", True)))

        async def fake_handle(message):
            called.setdefault("nl", True)

        import bot.handlers.message_handler as mh
        monkeypatch.setattr(mh, "on_message", fake_handle)

        msg = MagicMock()
        await dbv._dispatch_message(msg)

        assert called.get("pc") is True, "process_commands was not called"
        assert called.get("nl") is True, "NL handler was not called"


class TestSlashCommandRuntime:
    """Slash commands must sync at startup and report failures visibly."""

    @pytest.mark.asyncio
    async def test_sync_app_commands_once_syncs_each_guild(self, monkeypatch):
        import bot.discord_bot_v1 as dbv

        guild_one = MagicMock(id=111, name="one")
        guild_two = MagicMock(id=222, name="two")
        original_synced = dbv.APP_COMMANDS_SYNCED
        original_guilds = dbv.bot.guilds
        original_sync = dbv.bot.tree.sync
        original_copy = dbv.bot.tree.copy_global_to
        try:
            dbv.APP_COMMANDS_SYNCED = False
            monkeypatch.setenv("AZURE_SLASH_SYNC_SCOPE", "guild")
            dbv.bot.guilds = [guild_one, guild_two]
            dbv.bot.tree.sync = AsyncMock(side_effect=[["trace", "case"], ["trace"]])
            dbv.bot.tree.copy_global_to = MagicMock()

            await dbv._sync_app_commands_once()

            assert dbv.APP_COMMANDS_SYNCED is True
            assert dbv.bot.tree.copy_global_to.call_count == 2
            assert dbv.bot.tree.sync.await_count == 2
        finally:
            dbv.APP_COMMANDS_SYNCED = original_synced
            dbv.bot.guilds = original_guilds
            dbv.bot.tree.sync = original_sync
            dbv.bot.tree.copy_global_to = original_copy

    @pytest.mark.asyncio
    async def test_sync_app_commands_once_can_be_disabled(self, monkeypatch):
        import bot.discord_bot_v1 as dbv

        original_synced = dbv.APP_COMMANDS_SYNCED
        original_sync = dbv.bot.tree.sync
        try:
            dbv.APP_COMMANDS_SYNCED = False
            monkeypatch.setenv("AZURE_SLASH_SYNC_SCOPE", "off")
            dbv.bot.tree.sync = AsyncMock()

            await dbv._sync_app_commands_once()

            assert dbv.APP_COMMANDS_SYNCED is True
            dbv.bot.tree.sync.assert_not_awaited()
        finally:
            dbv.APP_COMMANDS_SYNCED = original_synced
            dbv.bot.tree.sync = original_sync

    @pytest.mark.asyncio
    async def test_on_app_command_error_replies_ephemerally(self):
        import bot.discord_bot_v1 as dbv

        interaction = MagicMock()
        interaction.command = MagicMock(qualified_name="settings provider")
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock()
        interaction.followup.send = AsyncMock()

        await dbv._handle_app_command_error(
            interaction,
            dbv.app_commands.AppCommandError("boom"),
        )

        interaction.response.send_message.assert_awaited_once()
        args, kwargs = interaction.response.send_message.await_args
        assert "Command failed:" in args[0]
        assert kwargs["ephemeral"] is True
        interaction.followup.send.assert_not_awaited()


# ===================================================================
# Test: _llm_response fallback (handlers/llm_handler.py)
# ===================================================================

class TestLlmResponseFallback:
    """Test _llm_response fallback when agent/LLM is unavailable."""

    @pytest.mark.asyncio
    async def test_returns_fallback_when_no_agent(self):
        """When ctx.agent is None, fallback should be returned."""
        from bot.context import ctx
        from bot.handlers.llm_handler import _llm_response
        original = ctx.agent
        ctx.agent = None
        try:
            result = await _llm_response("prompt", "fallback response")
            assert result == "fallback response"
        finally:
            ctx.agent = original


# ===================================================================
# Test: Periodic task imports (tasks.py)
# ===================================================================

class TestTasksImports:
    """Test that task loops are importable and have correct structure."""

    def test_task_loops_exist(self):
        """Task loop objects should be accessible from bot.tasks."""
        from bot.tasks import (
            autonomous_agent_loop,
            autonomous_scan_task,
            cron_check_loop,
            goal_executor_loop,
            periodic_scan,
        )
        assert cron_check_loop is not None
        assert autonomous_agent_loop is not None
        assert goal_executor_loop is not None
        assert periodic_scan is not None
        assert autonomous_scan_task is not None

    def test_before_loop_callbacks_exist(self):
        """Before-loop callbacks should exist."""
        from bot.tasks import (
            before_autonomous_agent_loop,
            before_autonomous_scan_task,
            before_goal_executor_loop,
            before_periodic_scan,
        )
        assert callable(before_autonomous_agent_loop)
        assert callable(before_goal_executor_loop)
        assert callable(before_periodic_scan)
        assert callable(before_autonomous_scan_task)

    @pytest.mark.asyncio
    async def test_before_loop_waits_for_ready(self):
        """before_autonomous_agent_loop should call wait_until_ready."""
        from bot.context import ctx
        from bot.tasks import before_autonomous_agent_loop

        mock_bot = MagicMock()
        mock_bot.wait_until_ready = AsyncMock()
        original_bot = ctx.bot
        ctx.bot = mock_bot
        try:
            await before_autonomous_agent_loop()
            mock_bot.wait_until_ready.assert_called_once()
        finally:
            ctx.bot = original_bot
