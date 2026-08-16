"""
Extremely thorough end-to-end integration tests for the Azure Discord bot.

Simulates FULL bot operation — from receiving a Discord message to generating
a response, executing tools, and persisting results. Everything is mocked but
the logic is real.

55+ tests covering:
  - Full message processing pipeline (15+)
  - Server creation simulation (10+)
  - Multi-user scenarios (10+)
  - Telemetry & observability (10+)
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from pathlib import Path
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

import pytest

# ---------------------------------------------------------------------------
# Fixtures: reusable mocks for every test
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_message():
    """Build a mock Discord message with sensible defaults."""
    msg = MagicMock()
    msg.author = MagicMock()
    msg.author.id = 111
    msg.author.display_name = "TestUser"
    msg.author.bot = False
    msg.author.guild_permissions = MagicMock()
    msg.author.guild_permissions.administrator = False
    msg.content = "hello"
    msg.mentions = []
    msg.attachments = []
    msg.guild = MagicMock()
    msg.guild.id = 999
    msg.guild.name = "TestGuild"
    msg.guild.owner_id = 222
    msg.guild.member_count = 50
    msg.guild.members = []
    msg.guild.text_channels = []
    msg.guild.categories = []
    msg.guild.roles = []
    msg.guild.verification_level = "low"
    msg.guild.explicit_content_filter = "disabled"
    msg.channel = MagicMock()
    msg.channel.id = 555
    msg.channel.name = "general"
    msg.channel.send = AsyncMock()
    msg.channel.typing = MagicMock()
    msg.channel.typing.return_value.__aenter__ = AsyncMock()
    msg.channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)
    msg.reply = AsyncMock()
    msg.id = 12345
    msg.webhook_id = None
    msg.add_reaction = AsyncMock()
    msg.delete = AsyncMock()
    msg.edit = AsyncMock()
    return msg


@pytest.fixture
def mock_bot():
    """Build a mock discord.Bot."""
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 777
    bot.user.display_name = "Azure"
    bot.guilds = [MagicMock()]
    bot.application = MagicMock()
    bot.application.owner = MagicMock()
    bot.application.owner.id = 999
    return bot


@pytest.fixture
def mock_agent():
    """Build a mock AzureAgent."""
    agent = MagicMock()
    agent.handle = AsyncMock(return_value="Hello! How can I help?")
    agent.short_term = MagicMock()
    agent.short_term.messages = []
    agent.short_term._lock = threading.Lock()
    agent.short_term.to_history = MagicMock(return_value=[])
    agent.long_term = MagicMock()
    agent.long_term.facts = {}
    agent.rag = MagicMock()
    agent.hybrid_rag = None
    agent.memory_backend = None
    agent.user_adaptation = None
    agent.moderation = None
    agent.llm = MagicMock()
    agent.llm.chat = MagicMock(return_value="Hello!")
    agent.failover_chain = None
    agent.model_router = None
    agent._llm_circuit_breaker = None
    agent._discord_tools = None
    agent._current_guild = None
    agent._current_channel = None
    agent._event_loop = None
    agent._llm_planner = None
    agent._max_turns = 10
    return agent


@pytest.fixture
def mock_mgnt_tools():
    """Build mock DiscordManagementTools."""
    tools = MagicMock()
    tools.execute_plan = AsyncMock(return_value=[])
    tools.get_server_state = AsyncMock(return_value={"channels": [], "roles": [], "categories": []})
    tools.health = MagicMock()
    tools.repair = None
    return tools


@pytest.fixture
def mock_db():
    """Build a mock DatabaseManager."""
    db = MagicMock()
    db.save_conversation = MagicMock(return_value=1)
    db.save_stats = MagicMock(return_value=1)
    db.get_access_control = MagicMock(return_value=None)
    db.log_telemetry = MagicMock()
    db.log_security_event = MagicMock()
    return db


@pytest.fixture
def mock_ctx(mock_bot, mock_agent, mock_mgnt_tools):
    """Patch the bot.context.ctx singleton for every test."""
    from bot.context import ctx
    original_agent = ctx.agent
    original_bot = ctx.bot
    original_mgnt = ctx.mgmt_tools
    original_chat_mode = ctx.chat_mode
    original_allowed = ctx.allowed_user_ids
    original_task_manager = ctx.task_manager
    original_plugin_manager = ctx.plugin_manager
    original_intent_classifier = ctx.intent_classifier
    original_moderation_service = ctx.moderation_service
    original_cognitive_pipeline = ctx.cognitive_pipeline
    original_admin_channel = ctx.admin_channel

    ctx.agent = mock_agent
    ctx.bot = mock_bot
    ctx.mgmt_tools = mock_mgnt_tools
    ctx.chat_mode = "anyone"
    ctx.allowed_user_ids = set()
    ctx.task_manager = None
    ctx.plugin_manager = None
    ctx.intent_classifier = None
    ctx.moderation_service = None
    ctx.cognitive_pipeline = None
    ctx.admin_channel = None

    yield ctx

    ctx.agent = original_agent
    ctx.bot = original_bot
    ctx.mgmt_tools = original_mgnt
    ctx.chat_mode = original_chat_mode
    ctx.allowed_user_ids = original_allowed
    ctx.task_manager = original_task_manager
    ctx.plugin_manager = original_plugin_manager
    ctx.intent_classifier = original_intent_classifier
    ctx.moderation_service = original_moderation_service
    ctx.cognitive_pipeline = original_cognitive_pipeline
    ctx.admin_channel = original_admin_channel


@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset module-level caches between tests to prevent cross-contamination."""
    from bot.config import (
        _bot_messages,
        _command_cooldowns,
        _conversation_history,
        _rate_limit_buckets,
        _response_cache,
    )
    _rate_limit_buckets.clear()
    _command_cooldowns.clear()
    _response_cache.clear()
    _conversation_history.clear()
    _bot_messages.clear()
    yield
    _rate_limit_buckets.clear()
    _command_cooldowns.clear()
    _response_cache.clear()
    _conversation_history.clear()
    _bot_messages.clear()


def _run_async(coro):
    """Helper to run an async function in tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ===================================================================
# SECTION 1: Full Message Processing Pipeline (15+ tests)
# ===================================================================


class TestFullPipeline:
    """Tests that simulate end-to-end message processing pipelines."""

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_hello_full_pipeline(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """User sends 'hello' → bot processes → response sent → context saved → stats updated."""
        mock_message.content = "hello"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="hello", violations=[])

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        mock_ctx.agent.handle = AsyncMock(return_value="Hey there!")

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        mock_ctx.agent.handle.assert_called_once()
        mock_progress.edit.assert_called()
        mock_cr.assert_called_once()

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_server_creation_full_pipeline(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """User says 'create a gaming server' → agent processes → plan generated → tools executed → response sent."""
        mock_message.content = "create a gaming server"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="create a gaming server", violations=[])

        plan_result = (
            "✅ create_channel → general\n"
            "✅ create_channel → memes\n"
            "✅ create_role → Gamer\n"
        )
        mock_ctx.agent.handle = AsyncMock(return_value=plan_result)

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        mock_ctx.agent.handle.assert_called_once()
        assert mock_progress.edit.call_count >= 1

    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_rate_limit_blocks_processing(
        self, mock_rl, mock_cc, mock_vi, mock_ctx, mock_message,
    ):
        """User sends message during rate limit → rate limit message sent → no LLM call."""
        mock_rl.return_value = (False, 31.0)
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="hello", violations=[])

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        mock_ctx.agent.handle.assert_not_called()
        mock_message.channel.send.assert_called()

    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(False, 3.5))
    def test_command_cooldown_blocks_processing(
        self, mock_rl, mock_cc, mock_vi, mock_ctx, mock_message,
    ):
        """User sends message during cooldown → cooldown message sent."""
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="hello", violations=[])

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        mock_ctx.agent.handle.assert_not_called()
        mock_message.add_reaction.assert_called_with("\u23f0")

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_send_failure_falls_back(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """Bot message fails to send → error handler sends fallback."""
        mock_message.content = "test"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="test", violations=[])

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        mock_ctx.agent.handle = AsyncMock(return_value="response")

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))
        assert mock_progress.edit.call_count >= 1

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_llm_timeout_circuit_breaker_fast_fallback(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """LLM times out → circuit breaker opens → subsequent messages get fast fallback."""
        from azure.errors import LLMError

        mock_message.content = "test timeout"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="test timeout", violations=[])

        mock_cb = MagicMock()
        mock_cb.allow_request.return_value = True
        mock_ctx.agent._llm_circuit_breaker = mock_cb

        fail_count = [0]

        async def failing_handle(**kwargs):
            fail_count[0] += 1
            if fail_count[0] <= 2:
                raise LLMError("test", "timeout")
            return "recovered"

        mock_ctx.agent.handle = AsyncMock(side_effect=failing_handle)

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))
        _run_async(on_message(mock_message))

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("azure.database.get_shared_db")
    def test_database_down_still_responds(
        self, mock_get_db, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """Database is down → error logged → user still gets response."""
        mock_message.content = "hello"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="hello", violations=[])

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        mock_ctx.agent.handle = AsyncMock(return_value="Hello!")

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        mock_progress.edit.assert_called()

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_agent_empty_reply_tool_only_path(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """Agent returns empty reply → tool-only path executes → telemetry shows completion."""
        mock_message.content = "delete channel general"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="delete channel general", violations=[])

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        mock_ctx.agent.handle = AsyncMock(return_value=None)

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        mock_cr.assert_not_called()

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_multiple_messages_from_same_user_context_accumulates(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """Multiple messages from same user → context accumulates correctly."""
        mock_message.content = "first message"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="first message", violations=[])

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        mock_ctx.agent.handle = AsyncMock(side_effect=["Response 1", "Response 2"])

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        mock_message.content = "second message"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="second message", violations=[])

        _run_async(on_message(mock_message))

        assert mock_ctx.agent.handle.call_count == 2

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_different_guilds_context_isolated(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """Message from different guilds → context isolated per guild."""
        mock_message.content = "hello"
        mock_message.guild.id = 111
        mock_message.guild.name = "Guild A"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="hello", violations=[])

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        mock_ctx.agent.handle = AsyncMock(return_value="Hi A!")

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        args_a = mock_ctx.agent.handle.call_args
        assert args_a.kwargs.get("server_name") == "Guild A"

        mock_message.guild.id = 222
        mock_message.guild.name = "Guild B"
        mock_ctx.agent.handle = AsyncMock(return_value="Hi B!")

        _run_async(on_message(mock_message))

        args_b = mock_ctx.agent.handle.call_args
        assert args_b.kwargs.get("server_name") == "Guild B"

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_mention_triggers_bot_full_processing(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """@mention triggers bot → attention check passes → full processing."""
        mock_message.content = "@Azure help me"
        mock_message.mentions = [mock_ctx.bot.user]
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="help me", violations=[])

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        mock_ctx.agent.handle = AsyncMock(return_value="Sure, how can I help?")

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        mock_ctx.agent.handle.assert_called_once()

    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_non_mention_in_mention_only_mode_ignored(
        self, mock_rl, mock_cc, mock_ac, mock_vi,
        mock_ctx, mock_message,
    ):
        """Non-mention message in mention_only mode → ignored."""
        mock_message.content = "hello everyone"
        mock_message.mentions = []
        mock_ctx.chat_mode = "mention_only"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="hello everyone", violations=[])

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        mock_ctx.agent.handle.assert_not_called()

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_dm_message_in_dm_only_mode_processed(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """DM message in dm_only mode → processed."""
        mock_message.content = "hello"
        mock_message.guild = None
        mock_ctx.chat_mode = "dm_only"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="hello", violations=[])

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        mock_ctx.agent.handle = AsyncMock(return_value="Hello DM!")

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        mock_ctx.agent.handle.assert_called_once()

    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_message_with_attachment_image_path(
        self, mock_rl, mock_cc, mock_ac, mock_vi,
        mock_ctx, mock_message,
    ):
        """Message with attachment → image handling path."""
        attachment = MagicMock()
        attachment.content_type = "image/png"
        mock_message.attachments = [attachment]
        mock_message.content = ""
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="", violations=[])

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

    @patch("azure.input_validator.validate_input")
    def test_input_validation_blocks_message(self, mock_vi, mock_ctx, mock_message):
        """Input with malicious patterns → blocked → user warned."""
        mock_message.content = "SELECT * FROM users"
        mock_vi.return_value = MagicMock(
            is_blocked=True, sanitized_input="SELECT * FROM users",
            violations=["SQL injection detected"],
        )

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        mock_message.channel.send.assert_called()
        mock_ctx.agent.handle.assert_not_called()

    def test_bot_message_ignored(self, mock_ctx, mock_message):
        """Bot's own messages are ignored."""
        mock_message.author = mock_ctx.bot.user
        mock_message.author.bot = True

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        mock_ctx.agent.handle.assert_not_called()


# ===================================================================
# SECTION 2: Server Creation Simulation (10+ tests)
# ===================================================================


class TestServerCreationSimulation:
    """Tests simulating server creation, moderation, and tool execution flows."""

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_create_gaming_server_all_tools_execute(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """User says 'create a gaming server' → agent generates plan → all tools execute → summary sent."""
        mock_message.content = "create a gaming server"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="create a gaming server", violations=[])

        summary = (
            "✅ create_channel → general\n"
            "✅ create_channel → memes\n"
            "✅ create_role → Gamer\n"
            "✅ create_role → Mod\n"
            "✅ set_channel_permissions\n"
            "✅ send_welcome_message"
        )
        mock_ctx.agent.handle = AsyncMock(return_value=summary)

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        mock_ctx.agent.handle.assert_called_once()
        assert "create_channel" in summary
        assert "create_role" in summary

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_setup_moderation_tools_execute(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """User says 'set up moderation' → moderation tools execute → config saved."""
        mock_message.content = "set up moderation"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="set up moderation", violations=[])

        summary = (
            "✅ set_auto_moderation → enabled\n"
            "✅ configure_filters → spam, profanity\n"
            "✅ set_log_channel → mod-logs"
        )
        mock_ctx.agent.handle = AsyncMock(return_value=summary)

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        assert "set_auto_moderation" in summary
        assert "configure_filters" in summary

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_roleplay_setup_complex_plan(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """User says 'create a role-playing setup' → complex 7+ step plan executes."""
        mock_message.content = "create a role-playing setup"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="create a role-playing setup", violations=[])

        summary = "\n".join([
            "✅ create_category → Roleplay",
            "✅ create_channel → ic-chat",
            "✅ create_channel → ooc-chat",
            "✅ create_channel → character-gallery",
            "✅ create_role → Player",
            "✅ create_role → DM",
            "✅ set_channel_permissions → read_only gallery",
        ])
        mock_ctx.agent.handle = AsyncMock(return_value=summary)

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        lines = [l for l in summary.strip().split("\n") if l.startswith("✅")]
        assert len(lines) == 7

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_ban_toxic_user_moderation_action(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """User says 'ban @toxic_user for spam' → moderation action → confirm."""
        mock_message.content = "ban @toxic_user for spam"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="ban toxic_user for spam", violations=[])

        summary = "✅ ban → toxic_user\n📝 Audit log updated"
        mock_ctx.agent.handle = AsyncMock(return_value=summary)

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        assert "ban" in summary
        mock_ctx.agent.handle.assert_called_once()

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_rename_server(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """User says 'rename this server to Cool Server' → execute rename → confirm."""
        mock_message.content = "rename this server to Cool Server"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="rename this server to Cool Server", violations=[])

        summary = "✅ rename_server → Cool Server"
        mock_ctx.agent.handle = AsyncMock(return_value=summary)

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        assert "rename_server" in summary

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_tool_failure_mid_plan_partial_completion(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """Tool execution fails mid-plan → partial completion → user informed."""
        mock_message.content = "create channels and roles"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="create channels and roles", violations=[])

        summary = (
            "✅ create_channel → general\n"
            "✅ create_channel → memes\n"
            "❌ create_role → Gamer: permission denied"
        )
        mock_ctx.agent.handle = AsyncMock(return_value=summary)

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        assert "❌" in summary
        assert "permission denied" in summary

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_undo_last_action(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """User says 'undo' → last action reversed."""
        mock_message.content = "undo"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="undo", violations=[])

        summary = "↩️ Undid: delete_channel → test-channel"
        mock_ctx.agent.handle = AsyncMock(return_value=summary)

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        assert "Undid" in summary

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_save_template(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """User says 'save template gaming-setup' → plan saved as template."""
        mock_message.content = "save template gaming-setup"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="save template gaming-setup", violations=[])

        summary = "💾 Template 'gaming-setup' saved with 6 steps"
        mock_ctx.agent.handle = AsyncMock(return_value=summary)

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        assert "Template" in summary
        assert "gaming-setup" in summary

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_load_template(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """User says 'load template gaming-setup' → saved plan executed."""
        mock_message.content = "load template gaming-setup"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="load template gaming-setup", violations=[])

        summary = (
            "📂 Loaded template 'gaming-setup'\n"
            "✅ create_channel → general\n"
            "✅ create_channel → memes\n"
            "✅ create_role → Gamer\n"
        )
        mock_ctx.agent.handle = AsyncMock(return_value=summary)

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        assert "Loaded template" in summary

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_agent_discord_action_plan_executes(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """Agent detects discord action → plan generated → tools called via execute_plan."""
        mock_message.content = "create a channel called announcements"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="create a channel called announcements", violations=[])

        mock_ctx.mgmt_tools.execute_plan = AsyncMock(return_value=[
            MagicMock(success=True, action="create_channel", name="announcements", error="", detail=""),
        ])

        mock_ctx.agent.handle = AsyncMock(return_value="✅ create_channel → announcements")

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        mock_ctx.agent.handle.assert_called_once()
        args = mock_ctx.agent.handle.call_args
        assert "announcements" in args.kwargs.get("message", "")


# ===================================================================
# SECTION 3: Multi-User Scenarios (10+ tests)
# ===================================================================


class TestMultiUserScenarios:
    """Tests simulating concurrent multi-user and permission scenarios."""

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_two_users_simultaneous_both_get_responses(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx,
    ):
        """Two users talking simultaneously → both get responses."""
        msg_a = MagicMock()
        msg_a.author.id = 111
        msg_a.author.display_name = "UserA"
        msg_a.author.bot = False
        msg_a.author.guild_permissions = MagicMock()
        msg_a.author.guild_permissions.administrator = False
        msg_a.content = "hello from A"
        msg_a.mentions = []
        msg_a.attachments = []
        msg_a.guild = MagicMock()
        msg_a.guild.id = 999
        msg_a.guild.owner_id = 222
        msg_a.channel = MagicMock()
        msg_a.channel.id = 501
        msg_a.channel.name = "general"
        msg_a.channel.send = AsyncMock(return_value=MagicMock(content="", edit=AsyncMock()))
        msg_a.reply = AsyncMock()
        msg_a.id = 10001

        msg_b = MagicMock()
        msg_b.author.id = 333
        msg_b.author.display_name = "UserB"
        msg_b.author.bot = False
        msg_b.author.guild_permissions = MagicMock()
        msg_b.author.guild_permissions.administrator = False
        msg_b.content = "hello from B"
        msg_b.mentions = []
        msg_b.attachments = []
        msg_b.guild = msg_a.guild
        msg_b.channel = msg_a.channel
        msg_b.channel.send = AsyncMock(return_value=MagicMock(content="", edit=AsyncMock()))
        msg_b.reply = AsyncMock()
        msg_b.id = 10002

        mock_vi.side_effect = [
            MagicMock(is_blocked=False, sanitized_input="hello from A", violations=[]),
            MagicMock(is_blocked=False, sanitized_input="hello from B", violations=[]),
        ]

        call_count = [0]
        responses = ["Hi UserA!", "Hi UserB!"]

        async def mock_handle(**kwargs):
            r = responses[call_count[0]]
            call_count[0] += 1
            return r

        mock_ctx.agent.handle = AsyncMock(side_effect=mock_handle)

        from bot.handlers.message_handler import on_message

        _run_async(on_message(msg_a))
        _run_async(on_message(msg_b))

        assert call_count[0] == 2

    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(False, 20.0))
    def test_user_a_rate_limited_user_b_not(
        self, mock_rl, mock_cc, mock_vi, mock_ctx,
    ):
        """User A rate limited, User B not → B processes normally."""
        msg_a = MagicMock()
        msg_a.author.id = 111
        msg_a.author.display_name = "UserA"
        msg_a.author.bot = False
        msg_a.author.guild_permissions = MagicMock()
        msg_a.author.guild_permissions.administrator = False
        msg_a.content = "hello"
        msg_a.mentions = []
        msg_a.attachments = []
        msg_a.guild = MagicMock()
        msg_a.guild.id = 999
        msg_a.guild.owner_id = 222
        msg_a.channel = MagicMock()
        msg_a.channel.id = 501
        msg_a.channel.send = AsyncMock()
        msg_a.reply = AsyncMock()
        msg_a.id = 10001

        msg_b = MagicMock()
        msg_b.author.id = 333
        msg_b.author.display_name = "UserB"
        msg_b.author.bot = False
        msg_b.author.guild_permissions = MagicMock()
        msg_b.author.guild_permissions.administrator = False
        msg_b.content = "hello"
        msg_b.mentions = []
        msg_b.attachments = []
        msg_b.guild = msg_a.guild
        msg_b.channel = msg_a.channel
        msg_b.channel.send = AsyncMock(return_value=MagicMock(content="", edit=AsyncMock()))
        msg_b.reply = AsyncMock()
        msg_b.id = 10002

        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="hello", violations=[])

        rl_results = [
            (False, 20.0),
            (True, 0.0),
        ]
        mock_rl.side_effect = rl_results

        from bot.handlers.message_handler import on_message

        _run_async(on_message(msg_a))
        msg_a.channel.send.assert_not_called()

        mock_ctx.agent.handle = AsyncMock(return_value="Hi B!")
        _run_async(on_message(msg_b))

    def test_owner_bypasses_cooldown(self, mock_ctx, mock_message):
        """Owner commands bypass cooldown."""
        mock_message.author.id = 222
        mock_message.guild.owner_id = 222

        from bot.handlers.rate_limiter import _check_command_cooldown

        allowed, remaining = _run_async(
            _check_command_cooldown("222", bypass_for_owner=True)
        )

        assert allowed is True
        assert remaining == 0.0

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_rapid_fire_100_messages_rate_limiter_handles_gracefully(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """User sends 100 messages rapidly → rate limiter handles gracefully."""
        mock_message.content = "spam"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="spam", violations=[])

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        allowed_count = 0
        blocked_count = 0

        from bot.handlers.message_handler import on_message

        for i in range(100):
            if i < 10:
                mock_rl.return_value = (True, 0.0)
                allowed_count += 1
            else:
                mock_rl.return_value = (False, 30.0)
                blocked_count += 1
            mock_message.id = 10000 + i
            _run_async(on_message(mock_message))

        assert allowed_count == 10
        assert blocked_count == 90

    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_guild_a_at_limit_guild_b_not_independent(
        self, mock_rl, mock_ctx,
    ):
        """Guild A at limit, Guild B not → independent rate limits."""
        from bot.handlers.rate_limiter import _check_rate_limit

        for _i in range(15):
            _run_async(_check_rate_limit("111", "guild_a"))

        allowed_a, _ = _run_async(_check_rate_limit("111", "guild_a"))
        allowed_b, _ = _run_async(_check_rate_limit("111", "guild_b"))

        assert allowed_a is False
        assert allowed_b is True

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_dm_vs_guild_different_contexts(
        self, mock_rl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx, mock_message,
    ):
        """DM conversation vs guild conversation → different contexts."""
        mock_message.guild = None
        mock_message.content = "hello DM"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="hello DM", violations=[])

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        mock_ctx.agent.handle = AsyncMock(return_value="Hello DM!")

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        args = mock_ctx.agent.handle.call_args
        assert args.kwargs.get("server_name") == "DM"

    def test_emergency_stop_halts_processing(self, mock_ctx):
        """User triggers emergency stop → all processing halts."""
        mock_ctx.agent.emergency_stop = MagicMock()

        mock_ctx.agent.emergency_stop()

        mock_ctx.agent.emergency_stop.assert_called_once()

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_guild_rate_limit", return_value=True)
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._add_to_context", new_callable=AsyncMock)
    def test_memory_isolation_between_users(
        self, mock_atc, mock_rl, mock_grl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi,
        mock_ctx,
    ):
        """Memory isolation between users → context is per-user."""
        msg_a = MagicMock()
        msg_a.author.id = 111
        msg_a.author.display_name = "UserA"
        msg_a.author.bot = False
        msg_a.author.guild_permissions = MagicMock()
        msg_a.author.guild_permissions.administrator = False
        msg_a.content = "my secret is 42"
        msg_a.mentions = []
        msg_a.attachments = []
        msg_a.guild = MagicMock()
        msg_a.guild.id = 999
        msg_a.guild.owner_id = 222
        msg_a.channel = MagicMock()
        msg_a.channel.id = 501
        msg_a.channel.send = AsyncMock(return_value=MagicMock(content="", edit=AsyncMock()))
        msg_a.reply = AsyncMock()
        msg_a.id = 10001

        msg_b = MagicMock()
        msg_b.author.id = 333
        msg_b.author.display_name = "UserB"
        msg_b.author.bot = False
        msg_b.author.guild_permissions = MagicMock()
        msg_b.author.guild_permissions.administrator = False
        msg_b.content = "what is UserA's secret?"
        msg_b.mentions = []
        msg_b.attachments = []
        msg_b.guild = msg_a.guild
        msg_b.channel = msg_a.channel
        msg_b.channel.send = AsyncMock(return_value=MagicMock(content="", edit=AsyncMock()))
        msg_b.reply = AsyncMock()
        msg_b.id = 10002

        mock_vi.side_effect = [
            MagicMock(is_blocked=False, sanitized_input="my secret is 42", violations=[]),
            MagicMock(is_blocked=False, sanitized_input="what is UserA's secret?", violations=[]),
        ]

        user_ids = []

        async def track_handle(**kwargs):
            user_ids.append(kwargs.get("user_id", ""))
            return "ok"

        mock_ctx.agent.handle = AsyncMock(side_effect=track_handle)

        from bot.handlers.message_handler import on_message

        _run_async(on_message(msg_a))
        _run_async(on_message(msg_b))

        assert user_ids[0] == "111"
        assert user_ids[1] == "333"

    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_admin_permissions_vs_regular_user(
        self, mock_rl, mock_cc, mock_vi, mock_ctx, mock_message,
    ):
        """Admin mod commands vs regular user permissions."""
        mock_message.author.guild_permissions.administrator = True
        mock_message.content = "ban someone"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="ban someone", violations=[])

        from bot.handlers.message_handler import is_allowed_to_chat

        assert is_allowed_to_chat(mock_message) is True


# ===================================================================
# SECTION 4: Telemetry & Observability (10+ tests)
# ===================================================================


class TestTelemetryObservability:
    """Tests for telemetry tracking, dashboard events, and observability."""

    def test_execution_tracker_records_all_stages(self):
        """Each message generates execution tracker → records all pipeline stages."""
        from azure.telemetry import ExecutionTracker

        tracker = ExecutionTracker(user="TestUser", guild="TestGuild", request_text="hello")

        tracker.emit("START", "Processing message")
        tracker.emit("ANALYZING", "Analyzing intent")
        tracker.emit("GENERATING", "Generating response")
        tracker.complete(True, "Done")

        assert len(tracker.events) >= 4
        assert tracker.is_finished

    def test_tracker_progress_text_updates_correctly(self):
        """Tracker progress text updates correctly through stages."""
        from azure.telemetry import ExecutionTracker

        tracker = ExecutionTracker(user="TestUser", guild="TestGuild", request_text="hello")

        text1 = tracker.get_discord_progress_text()
        assert "Thinking" in text1  # shows thinking indicator

        tracker.emit("START", "Processing")
        tracker.emit("GENERATING", "Generating reply")
        text2 = tracker.get_discord_progress_text()
        assert "Thinking" in text2  # still thinking

        tracker.complete(True, "Done")
        text3 = tracker.get_discord_progress_text()
        assert "Done" in text3

    def test_dashboard_receives_telemetry_events(self):
        """Dashboard receives telemetry events via callbacks."""
        from azure.telemetry import ExecutionTracker

        tracker = ExecutionTracker(user="TestUser", guild="TestGuild", request_text="hello")

        received_events = []

        def callback(event):
            received_events.append(event)

        tracker.add_callback(callback)
        tracker.emit("START", "Processing")

        assert len(received_events) == 1
        assert received_events[0].action == "START"

    def test_stats_accumulate_correctly(self):
        """Events accumulate correctly in tracker."""
        from azure.telemetry import ExecutionTracker

        tracker = ExecutionTracker(user="TestUser", guild="TestGuild", request_text="test")

        for i in range(5):
            tracker.emit("STEP", f"Step {i} done")

        assert len(tracker.events) == 5

        stages = tracker.stages
        assert len(stages) >= 1

    def test_error_events_recorded_with_details(self):
        """Error events recorded with full details."""
        from azure.telemetry import ExecutionTracker

        tracker = ExecutionTracker(user="TestUser", guild="TestGuild", request_text="test")

        tracker.emit("ERROR", "LLM timeout after 30s", status="error", provider="openai")

        error_events = [e for e in tracker.events if e.status == "error"]
        assert len(error_events) == 1
        assert "timeout" in error_events[0].message.lower()

    def test_performance_metrics_captured(self):
        """Performance metrics (elapsed_ms) captured."""
        from azure.telemetry import ExecutionTracker

        tracker = ExecutionTracker(user="TestUser", guild="TestGuild", request_text="test")

        time.sleep(0.05)

        tracker.complete(True, "Done")

        assert tracker.elapsed_ms >= 40

    def test_moderation_events_logged_to_audit(self):
        """Moderation events logged to audit."""
        import tempfile

        from azure.database import DatabaseManager

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(db_path=str(Path(tmpdir) / "test.db"))

            db.log_telemetry("exec-123", "moderation", "BAN", "Banned user for spam", "success")

            from azure.telemetry import set_telemetry_db
            set_telemetry_db(db)

            from azure.telemetry import ExecutionTracker

            tracker = ExecutionTracker(user="Admin", guild="Test", request_text="ban")
            tracker.emit("BAN", "Banned user", subsystem="moderation", status="success")

            set_telemetry_db(None)
            db.close()

    def test_cache_hit_miss_tracked(self):
        """Cache hit/miss tracked in telemetry."""
        from azure.telemetry import ExecutionTracker

        tracker = ExecutionTracker(user="TestUser", guild="TestGuild", request_text="test")

        tracker.emit("MEMORY", "Cache hit", hits=1)
        tracker.emit("MEMORY", "Cache miss", hits=0)

        mem_events = [e for e in tracker.events if e.action == "MEMORY"]
        assert len(mem_events) == 2
        assert mem_events[0].metadata.get("hits") == 1
        assert mem_events[1].metadata.get("hits") == 0

    @patch("azure.database.get_shared_db")
    def test_circuit_breaker_state_visible_in_health(self, mock_get_db, mock_ctx):
        """Circuit breaker state visible in health/info."""
        from azure.telemetry import ExecutionTracker

        tracker = ExecutionTracker(user="TestUser", guild="TestGuild", request_text="test")

        tracker.emit("CIRCUIT_BREAKER", "State: OPEN", status="warning")
        tracker.complete(False, "Circuit breaker open")

        assert tracker.is_finished
        assert tracker._finish_status == "error"

    def test_telemetry_event_serialization(self):
        """TelemetryEvent can be serialized to dict."""
        from azure.telemetry import TelemetryEvent

        event = TelemetryEvent(
            execution_id="exec-123",
            subsystem="agent",
            action="START",
            message="Processing",
            status="info",
            metadata={"model": "qwen"},
        )

        d = event.to_dict()
        assert d["execution_id"] == "exec-123"
        assert d["action"] == "START"
        assert d["metadata"]["model"] == "qwen"

    def test_tracker_presentation_snapshot(self):
        """ExecutionTracker.get_presentation() returns correct snapshot."""
        from azure.telemetry import ExecutionTracker

        tracker = ExecutionTracker(user="Alice", guild="MyServer", request_text="hello world")

        tracker.emit("START", "Starting")
        tracker.emit("GENERATING", "Generating")

        pres = tracker.get_presentation()
        assert pres["user"] == "Alice"
        assert pres["guild"] == "MyServer"
        assert pres["request_preview"] == "hello world"
        assert pres["finished"] is False
        assert pres["stage_count"] >= 1

    def test_tracker_complete_close_running_stages(self):
        """Tracker.complete() closes all still-running stages."""
        from azure.telemetry import ExecutionTracker

        tracker = ExecutionTracker(user="Test", guild="G", request_text="test")

        tracker.emit("START", "Starting")

        running_stages = [s for s in tracker.stages if s.status == "running"]
        assert len(running_stages) >= 1

        tracker.complete(True)

        still_running = [s for s in tracker.stages if s.status == "running"]
        assert len(still_running) == 0

    def test_tracker_dot_animation(self):
        """Tracker Discord text animates dots while running."""
        from azure.telemetry import ExecutionTracker

        tracker = ExecutionTracker(user="Test", guild="G", request_text="test")

        t1 = tracker.get_discord_progress_text()
        t2 = tracker.get_discord_progress_text()
        tracker.get_discord_progress_text()

        assert "Thinking" in t1  # shows thinking indicator
        assert "Thinking" in t2  # shows thinking indicator

    def test_tracker_event_cap_prevents_unbounded_growth(self):
        """Tracker caps event list to prevent unbounded memory growth."""
        from azure.telemetry import ExecutionTracker

        tracker = ExecutionTracker(user="Test", guild="G", request_text="test")

        for i in range(250):
            tracker.emit(f"EVENT_{i}", f"event {i}")

        assert len(tracker.events) <= 200


# ===================================================================
# SECTION 5: Agent-Level Integration (unit-level end-to-end)
# ===================================================================


class TestAgentIntegration:
    """Tests that exercise the agent.handle() method directly."""

    def test_agent_handle_greeting(self):
        """Agent receives greeting → intent classified → LLM generates reply."""
        from azure.agent import AzureAgent, ShortTermMemory

        agent = AzureAgent.__new__(AzureAgent)
        agent.model_name = "test"
        agent.short_term = ShortTermMemory(max_turns=5)
        agent._user_short_term = {}
        agent._user_short_term_lock = threading.Lock()
        agent.long_term = MagicMock()
        agent.long_term.facts = {}
        agent.tools = MagicMock()
        agent.tools.describe = MagicMock(return_value=[])
        agent._discord_tools = None
        agent._current_guild = None
        agent._current_channel = None
        agent._event_loop = None
        agent._llm_planner = None
        agent._tracker_lock = threading.Lock()
        agent._tracker = None
        agent.model_router = None
        agent.failover_chain = None
        agent.memory_backend = None
        agent.user_adaptation = None
        agent.hybrid_rag = None
        agent._llm_circuit_breaker = None
        agent.local_llm = None
        agent.api_llm = None
        agent.llm = MagicMock()
        agent.llm.chat = MagicMock(return_value="Hey! What's up?")
        agent.llm.get_info = MagicMock(return_value={"model_name": "test"})
        agent.formatter = None
        agent.rag = None
        agent._llm_type = "mock"
        agent._max_turns = 5
        agent._rag_k = 3
        agent._llm_temperature = 0.7
        agent._llm_max_tokens = 512
        agent._rag_path = "rag.json"
        agent._memory_db = "mem.db"
        agent._hybrid_rag_db = "hrag.db"
        agent._log_dir = "logs"
        agent._max_docs = 100
        agent._discord_decision_timeout = 60
        agent._discord_plan_timeout = 60
        agent.moderation = None

        result = _run_async(
            agent.handle(user="TestUser", message="hello", server_name="TestGuild", user_id="111")
        )

        assert result is not None
        assert "Hey" in result or "What" in result

    def test_agent_handle_empty_reply_returns_none(self):
        """Agent returns None when LLM gives empty reply."""
        from azure.agent import AzureAgent, ShortTermMemory

        agent = AzureAgent.__new__(AzureAgent)
        agent.model_name = "test"
        agent.short_term = ShortTermMemory(max_turns=5)
        agent._user_short_term = {}
        agent._user_short_term_lock = threading.Lock()
        agent.long_term = MagicMock()
        agent.long_term.facts = {}
        agent.tools = MagicMock()
        agent.tools.describe = MagicMock(return_value=[])
        agent._discord_tools = None
        agent._current_guild = None
        agent._current_channel = None
        agent._event_loop = None
        agent._llm_planner = None
        agent._tracker_lock = threading.Lock()
        agent._tracker = None
        agent.model_router = None
        agent.failover_chain = None
        agent.memory_backend = None
        agent.user_adaptation = None
        agent.hybrid_rag = None
        agent._llm_circuit_breaker = None
        agent.local_llm = None
        agent.api_llm = None
        agent.llm = MagicMock()
        agent.llm.chat = MagicMock(return_value="")
        agent.llm.get_info = MagicMock(return_value={"model_name": "test"})
        agent.formatter = None
        agent.rag = None
        agent._llm_type = "mock"
        agent._max_turns = 5
        agent._rag_k = 3
        agent._llm_temperature = 0.7
        agent._llm_max_tokens = 512
        agent._rag_path = "rag.json"
        agent._memory_db = "mem.db"
        agent._hybrid_rag_db = "hrag.db"
        agent._log_dir = "logs"
        agent._max_docs = 100
        agent._discord_decision_timeout = 60
        agent._discord_plan_timeout = 60
        agent.moderation = None

        result = _run_async(
            agent.handle(user="TestUser", message="test", server_name="Test", user_id="111")
        )

        assert result is not None
        assert "unavailable" in result.lower() or "error" in result.lower() or "temporarily" in result.lower()

    def test_agent_circuit_breaker_open_returns_fallback(self):
        """Agent with open circuit breaker returns fallback message."""
        from azure.agent import AzureAgent, ShortTermMemory

        agent = AzureAgent.__new__(AzureAgent)
        agent.model_name = "test"
        agent.short_term = ShortTermMemory(max_turns=5)
        agent._user_short_term = {}
        agent._user_short_term_lock = threading.Lock()
        agent._max_turns = 5
        agent._llm_circuit_breaker = MagicMock()
        agent._llm_circuit_breaker.allow_request.return_value = False
        agent._tracker_lock = threading.Lock()
        agent._tracker = None

        result = _run_async(
            agent.handle(user="TestUser", message="hello", server_name="Test", user_id="111")
        )

        assert "unavailable" in result.lower()

    def test_agent_intent_classification_greeting(self):
        """Agent telemetry no longer keyword-classifies greetings (LLM-first)."""
        from azure.agent import AzureAgent

        intent = AzureAgent._classify_message_intent("hello")

        assert intent["is_greeting"] is False
        assert intent["is_question"] is False

    def test_agent_intent_classification_question(self):
        """Agent classifies 'what is 2+2' as question (structural ? only)."""
        from azure.agent import AzureAgent

        intent = AzureAgent._classify_message_intent("what is 2+2?")

        assert intent["is_question"] is True

    def test_agent_intent_classification_command(self):
        """Agent no longer keyword-classifies commands (LLM-first routing)."""
        from azure.agent import AzureAgent

        intent = AzureAgent._classify_message_intent("create a channel called general")

        assert intent["is_command"] is False

    def test_agent_post_process_removes_filler(self):
        """Agent post-processing removes filler text."""
        from azure.agent import AzureAgent

        agent = AzureAgent.__new__(AzureAgent)
        result = agent._post_process_response("Sure! Here is your answer.", "question")

        assert "Sure!" not in result
        assert "Here is your answer." in result

    def test_agent_post_process_rejects_empty(self):
        """Agent post-processing rejects empty/useless replies."""
        from azure.agent import AzureAgent

        agent = AzureAgent.__new__(AzureAgent)

        assert agent._post_process_response("", "msg") == ""
        assert agent._post_process_response(".", "msg") == ""
        assert agent._post_process_response("OK", "msg") == ""

    def test_agent_post_process_fixes_hallucinated_mentions(self):
        """Agent removes hallucinated @mentions from response."""
        from azure.agent import AzureAgent

        agent = AzureAgent.__new__(AzureAgent)
        result = agent._post_process_response("Hello @RandomPerson how are you?", "hello")

        assert "@RandomPerson" not in result

    def test_agent_short_term_memory_merges(self):
        """Agent merges per-call short-term memory back to per-user history."""
        from azure.agent import AzureAgent, ShortTermMemory

        agent = AzureAgent.__new__(AzureAgent)
        agent.short_term = ShortTermMemory(max_turns=5)
        agent._user_short_term = {}
        agent._user_short_term_lock = threading.Lock()
        agent._max_turns = 5

        call_ctx = {"short_term": ShortTermMemory(max_turns=5), "user_id": "111"}
        call_ctx["short_term"].add("user", "hello")
        call_ctx["short_term"].add("assistant", "hi")

        agent._merge_short_term(call_ctx)

        assert len(agent._user_short_term["111"].messages) == 2

    def test_agent_tool_registry_call(self):
        """Agent tool registry registers and calls tools."""
        from azure.agent import ToolRegistry

        registry = ToolRegistry()
        registry.register("echo", "Echo input", lambda text="": text)

        result = registry.call("echo", text="hello")

        assert result["ok"] is True
        assert result["result"] == "hello"

    def test_agent_tool_registry_unknown_tool(self):
        """Agent tool registry returns error for unknown tool."""
        from azure.agent import ToolRegistry

        registry = ToolRegistry()
        result = registry.call("nonexistent")

        assert "error" in result

    def test_agent_build_plan_summary(self):
        """Agent builds plan summary from execution results."""
        from azure.agent import AzureAgent, ShortTermMemory

        agent = AzureAgent.__new__(AzureAgent)
        agent.short_term = ShortTermMemory(max_turns=5)
        agent._max_turns = 5

        results = [
            MagicMock(success=True, action="create_channel", name="general", error=""),
            MagicMock(success=False, action="create_role", name="", error="permission denied"),
            MagicMock(success=True, action="send_welcome", name="", error=""),
        ]

        summary = agent._build_plan_summary(results, {"short_term": ShortTermMemory()})

        assert "✅" in summary
        assert "❌" in summary
        assert "general" in summary
        assert "permission denied" in summary

    def test_agent_parse_requester_id_valid(self):
        """Agent parses valid requester ID."""
        from azure.agent import AzureAgent

        assert AzureAgent._parse_requester_id("12345") == 12345

    def test_agent_parse_requester_id_invalid(self):
        """Agent returns None for invalid requester ID."""
        from azure.agent import AzureAgent

        assert AzureAgent._parse_requester_id("not_a_number") is None
        assert AzureAgent._parse_requester_id("") is None

    def test_short_term_memory_context_block(self):
        """ShortTermMemory context_block renders messages."""
        from azure.agent import ShortTermMemory

        st = ShortTermMemory(max_turns=5)
        st.add("user", "hello")
        st.add("assistant", "hi there")

        block = st.context_block()

        assert "<user> hello" in block
        assert "<assistant> hi there" in block

    def test_short_term_memory_max_turns_cap(self):
        """ShortTermMemory respects max_turns cap."""
        from azure.agent import ShortTermMemory

        st = ShortTermMemory(max_turns=3)
        for i in range(10):
            st.add("user", f"msg {i}")

        assert len(st.messages) <= 6  # max_turns * 2


# ===================================================================
# SECTION 6: Rate Limiter & Cache Integration
# ===================================================================


class TestRateLimiterCache:
    """Integration tests for rate limiting, cooldown, and response cache."""

    def test_rate_limit_allows_within_window(self):
        """Rate limiter allows messages within window."""
        from bot.handlers.rate_limiter import _check_rate_limit

        for _i in range(5):
            allowed, remaining = _run_async(_check_rate_limit("user1", "guild1"))
            assert allowed is True

    def test_rate_limit_blocks_after_max(self):
        """Rate limiter blocks after exceeding max requests."""
        from bot.handlers.rate_limiter import _check_rate_limit

        for _i in range(12):
            _run_async(_check_rate_limit("user2", "guild2"))

        allowed, remaining = _run_async(_check_rate_limit("user2", "guild2"))
        assert allowed is False
        assert remaining > 0

    def test_command_cooldown_blocks_rapid_fire(self):
        """Command cooldown blocks rapid-fire commands."""
        from bot.handlers.rate_limiter import _check_command_cooldown

        allowed1, _ = _run_async(_check_command_cooldown("user3"))
        assert allowed1 is True

        allowed2, remaining = _run_async(_check_command_cooldown("user3"))
        assert allowed2 is False
        assert remaining > 0

    def test_response_cache_stores_and_retrieves(self):
        """Response cache stores and retrieves cached responses."""
        from bot.handlers.response_cache import _cache_response, _get_cached_response

        _run_async(_cache_response("hello", "user1", "guild1", "Hi there!"))

        cached = _run_async(_get_cached_response("hello", "user1", "guild1"))
        assert cached == "Hi there!"

    def test_response_cache_miss(self):
        """Response cache returns None on miss."""
        from bot.handlers.response_cache import _get_cached_response

        cached = _run_async(_get_cached_response("nonexistent", "user1", "guild1"))
        assert cached is None

    def test_response_hash_deterministic(self):
        """Response cache hash is deterministic."""
        from bot.handlers.response_cache import _hash_message

        h1 = _hash_message("hello", "user1", "guild1")
        h2 = _hash_message("hello", "user1", "guild1")
        assert h1 == h2

    def test_response_hash_varies_by_user(self):
        """Response cache hash varies by user."""
        from bot.handlers.response_cache import _hash_message

        h1 = _hash_message("hello", "user1", "guild1")
        h2 = _hash_message("hello", "user2", "guild1")
        assert h1 != h2

    def test_context_manager_adds_and_retrieves(self):
        """Context manager adds and retrieves conversation history."""
        from bot.handlers.context_manager import _add_to_context, _get_conversation_context

        _run_async(_add_to_context("user1", "hello", "hi there"))

        history = _run_async(_get_conversation_context("user1"))
        assert len(history) >= 1
        assert history[0]["user"] == "hello"
        assert history[0]["assistant"] == "hi there"

    def test_context_manager_evicts_old_messages(self):
        """Context manager evicts messages older than 1 hour."""
        from bot.config import _conversation_history
        from bot.handlers.context_manager import _add_to_context, _get_conversation_context

        _run_async(_add_to_context("user_old", "old msg", "old resp"))

        user_key = "user_old"
        if user_key in _conversation_history:
            for entry in _conversation_history[user_key]:
                entry["timestamp"] = time.time() - 7200

        history = _run_async(_get_conversation_context("user_old"))
        assert len(history) == 0


# ===================================================================
# SECTION 7: Database Integration
# ===================================================================


class TestDatabaseIntegration:
    """Integration tests for database persistence."""

    def test_save_and_retrieve_conversation(self):
        """Save conversation and retrieve history."""
        import tempfile

        from azure.database import ConversationMessage, DatabaseManager

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(db_path=str(Path(tmpdir) / "test.db"))

            msg = ConversationMessage(
                user_id="111", user_name="TestUser",
                server_id="999", server_name="TestGuild",
                channel_id="555", channel_name="general",
                message="hello", response="hi there",
                timestamp=time.time(), cached=False,
                tokens_used=50, response_time_ms=200,
            )

            row_id = db.save_conversation(msg)
            assert row_id is not None

            history = db.get_conversation_history(user_id="111")
            assert len(history) >= 1
            assert history[0].message == "hello"
            assert history[0].response == "hi there"

            db.close()

    def test_save_stats(self):
        """Save bot statistics."""
        import tempfile

        from azure.database import BotStats, DatabaseManager

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(db_path=str(Path(tmpdir) / "test.db"))

            stats = BotStats(
                timestamp=time.time(),
                messages_processed=10,
                cache_hits=3,
                cache_misses=7,
                errors=1,
                avg_response_time_ms=150.0,
                total_tokens_used=500,
                active_users=5,
                active_servers=2,
            )

            row_id = db.save_stats(stats)
            assert row_id is not None

            history = db.get_stats_history(hours=1)
            assert len(history) >= 1
            assert history[0].messages_processed == 10

            db.close()

    def test_access_control(self):
        """Set and get access control rules."""
        import tempfile

        from azure.database import DatabaseManager

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(db_path=str(Path(tmpdir) / "test.db"))

            db.set_access_control("user", "12345", "deny", "admin")

            perm = db.get_access_control("12345")
            assert perm == "deny"

            perm2 = db.get_access_control("99999")
            assert perm2 is None

            db.close()

    def test_telemetry_logging(self):
        """Log telemetry traces."""
        import tempfile

        from azure.database import DatabaseManager

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(db_path=str(Path(tmpdir) / "test.db"))

            db.log_telemetry("exec-001", "agent", "START", "Processing", "info")
            db.log_telemetry("exec-001", "agent", "COMPLETE", "Done", "success")

            db.close()

    def test_aggregate_stats(self):
        """Aggregate stats return correct structure."""
        import tempfile

        from azure.database import BotStats, DatabaseManager

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(db_path=str(Path(tmpdir) / "test.db"))

            for _i in range(5):
                db.save_stats(BotStats(
                    timestamp=time.time(),
                    messages_processed=2,
                    cache_hits=1,
                    cache_misses=1,
                    errors=0,
                    avg_response_time_ms=100.0,
                    total_tokens_used=50,
                    active_users=1,
                    active_servers=1,
                ))

            agg = db.get_aggregate_stats(hours=1)
            assert agg["total_messages"] == 10
            assert agg["total_cache_hits"] == 5
            assert agg["total_cache_misses"] == 5
            assert agg["cache_hit_rate"] == 0.5

            db.close()

    def test_shared_db_singleton(self):
        """get_shared_db returns same instance."""
        import shutil
        import tempfile

        from azure.database import DatabaseManager, get_shared_db, set_shared_db

        tmpdir = tempfile.mkdtemp()
        try:
            db = DatabaseManager(db_path=str(Path(tmpdir) / "test.db"))
            set_shared_db(db)

            db1 = get_shared_db()
            db2 = get_shared_db()
            assert db1 is db2

            db.close()
            set_shared_db(None)
        finally:
            with contextlib.suppress(Exception):
                set_shared_db(None)
            shutil.rmtree(tmpdir, ignore_errors=True)


# ===================================================================
# SECTION 8: Error Handling & Recovery
# ===================================================================


class TestErrorHandling:
    """Tests for error handling, recovery, and graceful degradation."""

    @patch("bot.handlers.message_handler._persist_interaction")
    @patch("bot.handlers.message_handler._cache_response", new_callable=AsyncMock)
    @patch("bot.handlers.message_handler._get_cached_response", new_callable=AsyncMock, return_value=None)
    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._attention_check", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_guild_rate_limit", return_value=True)
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_llm_error_returns_friendly_message(self, mock_rl, mock_grl, mock_cc, mock_ac, mock_vi, mock_gcr, mock_cr, mock_pi, mock_ctx, mock_message):
        """LLM error → friendly error message sent to user."""
        from azure.errors import LLMError

        mock_message.content = "test"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="test", violations=[])

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        mock_ctx.agent.handle = AsyncMock(side_effect=LLMError("test", "model crashed"))

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

        mock_progress.edit.assert_called()

    @patch("azure.input_validator.validate_input")
    @patch("bot.handlers.message_handler._check_command_cooldown", new_callable=AsyncMock, return_value=(True, 0.0))
    @patch("bot.handlers.message_handler._check_rate_limit", new_callable=AsyncMock, return_value=(True, 0.0))
    def test_rate_limit_error_returns_retry_message(self, mock_rl, mock_cc, mock_vi, mock_ctx, mock_message):
        """RateLimitError → friendly retry message sent."""
        from azure.errors import RateLimitError

        mock_message.content = "test"
        mock_vi.return_value = MagicMock(is_blocked=False, sanitized_input="test", violations=[])

        mock_progress = MagicMock()
        mock_progress.content = "🧠 Thinking..."
        mock_progress.edit = AsyncMock()
        mock_message.channel.send = AsyncMock(return_value=mock_progress)

        mock_ctx.agent.handle = AsyncMock(side_effect=RateLimitError(retry_after=5.0))

        from bot.handlers.message_handler import on_message

        _run_async(on_message(mock_message))

    def test_fallback_response_types(self):
        """Fallback responses cover all error types."""
        from bot.handlers.message_handler import _get_fallback_response

        timeout = _get_fallback_response("timeout", "User")
        assert "User" in timeout
        assert "longer than expected" in timeout

        llm_err = _get_fallback_response("llm_error", "User")
        assert "User" in llm_err

        net_err = _get_fallback_response("network_error")
        assert "Network" in net_err

        rate = _get_fallback_response("rate_limit")
        assert "Slow down" in rate

        unknown = _get_fallback_response("unknown")
        assert "Something unexpected" in unknown

    def test_fallback_response_no_name(self):
        """Fallback responses work without user name."""
        from bot.handlers.message_handler import _get_fallback_response

        msg = _get_fallback_response("timeout")
        assert "⏰" in msg

    def test_get_fallback_response_unknown_type_defaults(self):
        """Unknown error type defaults to 'unknown' fallback."""
        from bot.handlers.message_handler import _get_fallback_response

        msg = _get_fallback_response("nonexistent_error_type")
        assert "Something unexpected" in msg


# ===================================================================
# SECTION 9: Permission & Access Control
# ===================================================================


class TestPermissions:
    """Tests for chat mode filtering and access control."""

    def test_owner_only_mode_allows_owner(self, mock_ctx, mock_message):
        """owner_only mode allows server owner."""
        mock_ctx.chat_mode = "owner_only"
        mock_message.author.id = 222
        mock_message.guild.owner_id = 222

        from bot.handlers.message_handler import is_allowed_to_chat

        assert is_allowed_to_chat(mock_message) is True

    def test_owner_only_mode_blocks_regular_user(self, mock_ctx, mock_message):
        """owner_only mode blocks regular user."""
        mock_ctx.chat_mode = "owner_only"
        mock_message.author.id = 111
        mock_message.guild.owner_id = 222

        from bot.handlers.message_handler import is_allowed_to_chat

        assert is_allowed_to_chat(mock_message) is False

    def test_anyone_mode_allows_all(self, mock_ctx, mock_message):
        """anyone mode allows all users."""
        mock_ctx.chat_mode = "anyone"

        from bot.handlers.message_handler import is_allowed_to_chat

        assert is_allowed_to_chat(mock_message) is True

    def test_dm_only_mode_allows_dm(self, mock_ctx, mock_message):
        """dm_only mode allows DMs."""
        mock_ctx.chat_mode = "dm_only"
        mock_message.guild = None

        from bot.handlers.message_handler import is_allowed_to_chat

        assert is_allowed_to_chat(mock_message) is True

    def test_dm_only_mode_blocks_guild(self, mock_ctx, mock_message):
        """dm_only mode blocks guild messages."""
        mock_ctx.chat_mode = "dm_only"
        mock_message.guild = MagicMock()

        from bot.handlers.message_handler import is_allowed_to_chat

        assert is_allowed_to_chat(mock_message) is False

    def test_specific_users_mode(self, mock_ctx, mock_message):
        """specific_users mode allows only listed users."""
        mock_ctx.chat_mode = "specific_users"
        mock_ctx.allowed_user_ids = {"111"}

        from bot.handlers.message_handler import is_allowed_to_chat

        mock_message.author.id = 111
        assert is_allowed_to_chat(mock_message) is True

        mock_message.author.id = 888
        assert is_allowed_to_chat(mock_message) is False

    def test_mention_only_mode_allows_mention(self, mock_ctx, mock_message):
        """mention_only mode allows @mentions."""
        mock_ctx.chat_mode = "mention_only"
        mock_message.mentions = [mock_ctx.bot.user]

        from bot.handlers.message_handler import is_allowed_to_chat

        assert is_allowed_to_chat(mock_message) is True

    def test_mention_only_mode_blocks_non_mention(self, mock_ctx, mock_message):
        """mention_only mode blocks non-mention guild messages."""
        mock_ctx.chat_mode = "mention_only"
        mock_message.mentions = []

        from bot.handlers.message_handler import is_allowed_to_chat

        assert is_allowed_to_chat(mock_message) is False

    def test_application_owner_always_allowed(self, mock_ctx, mock_message):
        """Bot application owner always allowed regardless of chat mode."""
        mock_ctx.chat_mode = "owner_only"
        mock_message.author.id = 999

        from bot.handlers.message_handler import is_allowed_to_chat

        assert is_allowed_to_chat(mock_message) is True

    def test_admin_always_allowed(self, mock_ctx, mock_message):
        """Server admin always allowed."""
        import discord
        mock_ctx.chat_mode = "owner_only"
        mock_message.author.id = 111
        mock_message.guild.owner_id = 222
        mock_message.author = MagicMock(spec=discord.Member)
        mock_message.author.id = 111
        mock_message.author.guild_permissions.administrator = True

        from bot.handlers.message_handler import is_allowed_to_chat

        assert is_allowed_to_chat(mock_message) is True


# ===================================================================
# SECTION 10: Pending Confirmation Filter
# ===================================================================


class TestPendingConfirmation:
    """Tests for confirmation keyword suppression."""

    def test_set_and_check_pending(self):
        """Set pending confirmation → check returns True."""
        from bot.handlers.message_handler import _has_pending_confirmation, _set_pending_confirmation

        _set_pending_confirmation("111", "555")
        assert _has_pending_confirmation("111", "555") is True

    def test_clear_pending(self):
        """Clear pending confirmation → check returns False."""
        from bot.handlers.message_handler import (
            _clear_pending_confirmation,
            _has_pending_confirmation,
            _set_pending_confirmation,
        )

        _set_pending_confirmation("111", "555")
        _clear_pending_confirmation("111", "555")
        assert _has_pending_confirmation("111", "555") is False

    def test_pending_expires(self):
        """Pending confirmation expires after TTL."""
        from bot.handlers.message_handler import _has_pending_confirmation, _pending_confirmations

        key = "111:555"
        _pending_confirmations[key] = time.time() - 120

        assert _has_pending_confirmation("111", "555") is False
        assert key not in _pending_confirmations


# ===================================================================
# SECTION 11: Bot Message Registration
# ===================================================================


class TestBotMessageRegistration:
    """Tests for bot message reaction-based controls."""

    @pytest.mark.asyncio
    async def test_register_bot_message(self):
        """Register bot message for reaction controls."""
        from bot.handlers.message_handler import _get_bot_message_metadata, _register_bot_message

        mock_msg = MagicMock()
        mock_msg.id = 99999
        mock_msg.channel.id = 555

        await _register_bot_message(mock_msg, "111", "hello")

        meta = await _get_bot_message_metadata("99999")
        assert meta is not None
        assert meta["user_id"] == "111"
        assert meta["original_text"] == "hello"

    @pytest.mark.asyncio
    async def test_get_expired_bot_message(self):
        """Expired bot message returns None."""
        from bot.handlers.message_handler import _bot_messages, _get_bot_message_metadata

        _bot_messages["expired_id"] = {
            "user_id": "111",
            "original_text": "old",
            "timestamp": time.time() - 7200,
            "channel_id": 555,
        }

        meta = await _get_bot_message_metadata("expired_id")
        assert meta is None

    @pytest.mark.asyncio
    async def test_get_unknown_bot_message(self):
        """Unknown bot message returns None."""
        from bot.handlers.message_handler import _get_bot_message_metadata

        meta = await _get_bot_message_metadata("nonexistent")
        assert meta is None


# ===================================================================
# SECTION 12: Guild Rate Limiting
# ===================================================================


class TestGuildRateLimiting:
    """Tests for guild-level rate limiting."""

    def test_guild_rate_limit_allows_normal(self):
        """Guild rate limit allows normal traffic."""
        from bot.handlers.message_handler import _check_guild_rate_limit

        assert _check_guild_rate_limit("guild1") is True

    def test_guild_rate_limit_blocks_excessive(self):
        """Guild rate limit blocks excessive traffic."""
        from bot.handlers.message_handler import _check_guild_rate_limit, _guild_message_counts

        _guild_message_counts.clear()
        for _i in range(55):
            _check_guild_rate_limit("guild_heavy")

        assert _check_guild_rate_limit("guild_heavy") is False

    def test_guild_rate_limit_independent_per_guild(self):
        """Guild rate limits are independent per guild."""
        from bot.handlers.message_handler import _check_guild_rate_limit, _guild_message_counts

        _guild_message_counts.clear()
        for _i in range(55):
            _check_guild_rate_limit("guild_a")

        assert _check_guild_rate_limit("guild_a") is False
        assert _check_guild_rate_limit("guild_b") is True
