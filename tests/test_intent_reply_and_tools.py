"""Reply utils + LLM-first intent/tool engine unit tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from azure.intent_classifier import IntentClassifier, UserIntent
from azure.tool_engine import ToolDecision, ToolEngine
from bot.handlers.message_handler import _generate_agentic_reply
from bot.handlers.reply_utils import clamp_discord, format_final_reply


def test_clamp_discord_short():
    assert clamp_discord("hi") == "hi"


def test_clamp_discord_long():
    body = "x" * 5000
    out = clamp_discord(body, limit=100)
    assert len(out) <= 100
    assert "truncated" in out.lower()


def test_format_final_reply_clamps():
    out = format_final_reply("a" * 4000)
    assert len(out) <= 2000


def test_tool_engine_no_keyword_fallback():
    class _LLM:
        def chat(self, messages, max_tokens=0, temperature=0):
            return "not json at all"

    eng = ToolEngine(llm=_LLM())
    d = eng.decide("create a channel please", "User", "Server")
    assert isinstance(d, ToolDecision)
    assert d.action == "chat"


def test_tool_engine_parses_plan_json():
    class _LLM:
        def chat(self, messages, max_tokens=0, temperature=0):
            return '{"action":"plan","confidence":0.88,"plan_description":"Build welcome"}'

    eng = ToolEngine(llm=_LLM())
    d = eng.decide("build welcome", "User")
    assert d.action == "plan"
    assert d.plan is not None


def test_intent_structural_ignore():
    clf = IntentClassifier(bot_name="Azure")
    intent = clf.classify("random chat", is_dm=False, is_mentioned=False)
    assert intent.route == "ignore"
    assert isinstance(intent, UserIntent)


def test_intent_structural_dm_chat():
    clf = IntentClassifier(bot_name="Azure")
    intent = clf.classify("hey there", is_dm=True)
    assert intent.route == "chat"
    assert intent.is_directed


@pytest.mark.asyncio
async def test_member_action_uses_authorized_plan_execution():
    decision = ToolDecision(
        action="member_action",
        confidence=0.95,
        tool_call={"tool": "kick_member", "member": "123", "reason": "spam"},
    )
    runtime_ctx = SimpleNamespace(
        tool_engine=MagicMock(decide=MagicMock(return_value=decision)),
        mgmt_tools=MagicMock(),
        cognitive_pipeline=None,
        cognitive_mode=False,
        agent=None,
    )
    runtime_ctx.mgmt_tools.kick_member = AsyncMock()
    runtime_ctx.mgmt_tools.execute_plan = AsyncMock(
        return_value=[SimpleNamespace(success=True, error="")]
    )

    message = MagicMock()
    message.guild = MagicMock()
    message.channel = MagicMock()
    message.author.id = 42
    routed = SimpleNamespace(route="tool", action="tool")

    with (
        patch("bot.context.ctx", runtime_ctx),
        patch(
            "bot.handlers.message_handler.asyncio.to_thread",
            new=AsyncMock(return_value=decision),
        ),
    ):
        reply = await _generate_agentic_reply(
            message=message,
            text="kick user 123 for spam",
            user="Owner",
            is_directed=True,
            is_dm=False,
            mentioned=True,
            server_name="Test Server",
            routed_intent=routed,
            tracker=None,
            progress_callback=None,
            event_loop=None,
        )

    assert reply == "Completed kick member for 123."
    runtime_ctx.mgmt_tools.execute_plan.assert_awaited_once_with(
        message.guild,
        {
            "analysis": "Member action: kick_member",
            "steps": [
                {
                    "action": "kick",
                    "params": {"member": "123", "reason": "spam"},
                }
            ],
        },
        message.channel,
        requester_name="Owner",
        requester_id=42,
    )


@pytest.mark.asyncio
async def test_low_confidence_mutation_is_clarified_before_execution():
    decision = ToolDecision(
        action="member_action",
        confidence=0.4,
        tool_call={"tool": "ban_member", "member": "123", "reason": "unclear"},
    )
    runtime_ctx = SimpleNamespace(
        tool_engine=MagicMock(decide=MagicMock(return_value=decision)),
        mgmt_tools=MagicMock(),
        cognitive_pipeline=None,
        cognitive_mode=False,
        agent=MagicMock(),
    )
    runtime_ctx.mgmt_tools.execute_plan = AsyncMock()
    message = MagicMock()
    message.guild = MagicMock()
    message.channel = MagicMock()
    message.author.id = 42
    routed = SimpleNamespace(route="tool", action="tool")

    with (
        patch("bot.context.ctx", runtime_ctx),
        patch(
            "bot.handlers.message_handler.asyncio.to_thread",
            new=AsyncMock(return_value=decision),
        ),
    ):
        reply = await _generate_agentic_reply(
            message=message,
            text="ban someone maybe",
            user="Owner",
            is_directed=True,
            is_dm=False,
            mentioned=True,
            server_name="Test Server",
            routed_intent=routed,
            tracker=None,
            progress_callback=None,
            event_loop=None,
        )

    assert "not confident" in reply
    runtime_ctx.mgmt_tools.execute_plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_logs_require_permission_and_use_live_tool():
    decision = ToolDecision(
        action="audit_logs",
        confidence=0.95,
        params={"limit": 2, "action_type": "channel_delete"},
    )
    runtime_ctx = SimpleNamespace(
        tool_engine=MagicMock(decide=MagicMock(return_value=decision)),
        mgmt_tools=MagicMock(),
        cognitive_pipeline=None,
        cognitive_mode=False,
        agent=None,
    )
    runtime_ctx.mgmt_tools.get_audit_logs = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            after_state={"logs": [{"action": "channel_delete", "user": "Mod", "target": "old", "created_at": "now", "reason": "cleanup"}]},
        )
    )
    message = MagicMock()
    message.guild = MagicMock(name="Test Server")
    message.channel = MagicMock()
    message.author.id = 42
    message.author.guild_permissions.administrator = True
    routed = SimpleNamespace(route="tool", action="tool")

    with (
        patch("bot.context.ctx", runtime_ctx),
        patch("bot.handlers.message_handler.asyncio.to_thread", new=AsyncMock(return_value=decision)),
    ):
        reply = await _generate_agentic_reply(
            message=message, text="who deleted the channel?", user="Owner",
            is_directed=True, is_dm=False, mentioned=True, server_name="Test Server",
            routed_intent=routed, tracker=None, progress_callback=None, event_loop=None,
        )

    assert "channel_delete" in reply
    runtime_ctx.mgmt_tools.get_audit_logs.assert_awaited_once_with(
        message.guild, limit=2, action_type="channel_delete"
    )


@pytest.mark.asyncio
async def test_health_request_uses_live_health_analyzer():
    decision = ToolDecision(action="health_check", confidence=0.9)
    runtime_ctx = SimpleNamespace(
        tool_engine=MagicMock(decide=MagicMock(return_value=decision)),
        mgmt_tools=MagicMock(),
        cognitive_pipeline=None,
        cognitive_mode=False,
        agent=None,
    )
    runtime_ctx.mgmt_tools.health.analyze = AsyncMock(return_value={
        "server_name": "Test Server",
        "score": 88,
        "categories": {"security": {"score": 90}},
        "issues": ["Missing rules channel"],
        "recommendations": [{"text": "Create #rules"}],
    })
    message = MagicMock()
    message.guild = MagicMock()
    message.channel = MagicMock()
    message.author.id = 42
    routed = SimpleNamespace(route="health_check", action="health_check")

    with (
        patch("bot.context.ctx", runtime_ctx),
        patch("bot.handlers.message_handler.asyncio.to_thread", new=AsyncMock(return_value=decision)),
    ):
        reply = await _generate_agentic_reply(
            message=message, text="audit server health", user="Owner",
            is_directed=True, is_dm=False, mentioned=True, server_name="Test Server",
            routed_intent=routed, tracker=None, progress_callback=None, event_loop=None,
        )

    assert "88/100" in reply
    assert "Missing rules channel" in reply
    runtime_ctx.mgmt_tools.health.analyze.assert_awaited_once_with(message.guild)


@pytest.mark.asyncio
async def test_template_list_is_available_through_agent_router():
    decision = ToolDecision(
        action="template", confidence=0.95,
        params={"template_action": "list"},
    )
    runtime_ctx = SimpleNamespace(
        tool_engine=MagicMock(decide=MagicMock(return_value=decision)),
        mgmt_tools=MagicMock(), cognitive_pipeline=None,
        cognitive_mode=False, agent=None, bot=None,
    )
    runtime_ctx.mgmt_tools.templates.list_templates.return_value = [
        {"name": "gaming", "description": "Gaming layout"},
    ]
    message = MagicMock()
    message.guild = MagicMock()
    message.channel = MagicMock()
    message.author.id = 42
    routed = SimpleNamespace(route="tool", action="tool")

    with (
        patch("bot.context.ctx", runtime_ctx),
        patch("bot.handlers.message_handler.asyncio.to_thread", new=AsyncMock(return_value=decision)),
    ):
        reply = await _generate_agentic_reply(
            message=message, text="list server templates", user="Owner",
            is_directed=True, is_dm=False, mentioned=True, server_name="Test Server",
            routed_intent=routed, tracker=None, progress_callback=None, event_loop=None,
        )

    assert "gaming" in reply


@pytest.mark.asyncio
async def test_undo_requires_server_management_permission():
    decision = ToolDecision(action="undo", confidence=0.95, params={"count": 1})
    runtime_ctx = SimpleNamespace(
        tool_engine=MagicMock(decide=MagicMock(return_value=decision)),
        mgmt_tools=MagicMock(), cognitive_pipeline=None,
        cognitive_mode=False, agent=None, bot=None,
    )
    runtime_ctx.mgmt_tools.undo_last = AsyncMock()
    message = MagicMock()
    message.guild = MagicMock(owner_id=999)
    message.channel = MagicMock()
    message.author.id = 42
    message.author.guild_permissions.administrator = False
    message.author.guild_permissions.manage_guild = False
    routed = SimpleNamespace(route="tool", action="tool")

    with (
        patch("bot.context.ctx", runtime_ctx),
        patch("bot.handlers.message_handler.asyncio.to_thread", new=AsyncMock(return_value=decision)),
    ):
        reply = await _generate_agentic_reply(
            message=message, text="undo the last change", user="Member",
            is_directed=True, is_dm=False, mentioned=True, server_name="Test Server",
            routed_intent=routed, tracker=None, progress_callback=None, event_loop=None,
        )

    assert "owner or an administrator" in reply
    runtime_ctx.mgmt_tools.undo_last.assert_not_awaited()


@pytest.mark.asyncio
async def test_member_info_uses_live_guild_member():
    decision = ToolDecision(
        action="member_info", confidence=0.95, params={"member": "42"}
    )
    runtime_ctx = SimpleNamespace(
        tool_engine=MagicMock(decide=MagicMock(return_value=decision)),
        mgmt_tools=None, cognitive_pipeline=None, cognitive_mode=False,
        agent=None, bot=None,
    )
    member = SimpleNamespace(
        id=42, name="Alex", display_name="Alex", bot=False, status=SimpleNamespace(name="online"),
        joined_at=None, roles=[],
    )
    guild = MagicMock()
    guild.get_member.return_value = member
    message = MagicMock()
    message.guild = guild
    message.channel = MagicMock()
    message.author.id = 7
    routed = SimpleNamespace(route="info", action="info")

    with (
        patch("bot.context.ctx", runtime_ctx),
        patch("bot.handlers.message_handler.asyncio.to_thread", new=AsyncMock(return_value=decision)),
    ):
        reply = await _generate_agentic_reply(
            message=message, text="what roles does user 42 have", user="Owner",
            is_directed=True, is_dm=False, mentioned=True, server_name="Test Server",
            routed_intent=routed, tracker=None, progress_callback=None, event_loop=None,
        )

    assert "Member information: Alex" in reply
    assert "Status: **online**" in reply


@pytest.mark.asyncio
async def test_channel_info_uses_live_guild_channel():
    decision = ToolDecision(
        action="channel_info", confidence=0.95, params={"channel": "general"}
    )
    runtime_ctx = SimpleNamespace(
        tool_engine=MagicMock(decide=MagicMock(return_value=decision)),
        mgmt_tools=None, cognitive_pipeline=None, cognitive_mode=False,
        agent=None, bot=None,
    )
    channel = SimpleNamespace(
        id=9, name="general", type="text", category=SimpleNamespace(name="Community"),
        topic="Talk here", nsfw=False, slowmode_delay=0,
    )
    guild = MagicMock(channels=[channel])
    guild.get_channel.return_value = None
    message = MagicMock(guild=guild)
    message.channel = MagicMock()
    message.author.id = 7
    routed = SimpleNamespace(route="info", action="info")

    with (
        patch("bot.context.ctx", runtime_ctx),
        patch("bot.handlers.message_handler.asyncio.to_thread", new=AsyncMock(return_value=decision)),
    ):
        reply = await _generate_agentic_reply(
            message=message, text="what is the topic of general", user="Owner",
            is_directed=True, is_dm=False, mentioned=True, server_name="Test Server",
            routed_intent=routed, tracker=None, progress_callback=None, event_loop=None,
        )

    assert "Channel information: #general" in reply
    assert "Talk here" in reply


@pytest.mark.asyncio
async def test_role_info_uses_live_guild_role():
    decision = ToolDecision(
        action="role_info", confidence=0.95, params={"role": "Moderators"}
    )
    runtime_ctx = SimpleNamespace(
        tool_engine=MagicMock(decide=MagicMock(return_value=decision)),
        mgmt_tools=None, cognitive_pipeline=None, cognitive_mode=False,
        agent=None, bot=None,
    )
    role = SimpleNamespace(
        id=12, name="Moderators", position=3, managed=False,
        hoist=True, mentionable=True, members=[SimpleNamespace(id=7)],
        permissions=SimpleNamespace(to_dict=lambda: {"manage_messages": True, "ban_members": False}),
    )
    guild = MagicMock(roles=[role])
    guild.get_role.return_value = None
    message = MagicMock(guild=guild)
    message.channel = MagicMock()
    message.author.id = 7
    routed = SimpleNamespace(route="info", action="info")

    with (
        patch("bot.context.ctx", runtime_ctx),
        patch("bot.handlers.message_handler.asyncio.to_thread", new=AsyncMock(return_value=decision)),
    ):
        reply = await _generate_agentic_reply(
            message=message, text="what permissions does Moderators have", user="Owner",
            is_directed=True, is_dm=False, mentioned=True, server_name="Test Server",
            routed_intent=routed, tracker=None, progress_callback=None, event_loop=None,
        )

    assert "Role: Moderators" in reply
    assert "manage messages" in reply


@pytest.mark.asyncio
async def test_server_data_uses_live_automod_rules():
    decision = ToolDecision(
        action="server_data", confidence=0.95,
        params={"data_type": "automod_rules", "limit": 10},
    )
    runtime_ctx = SimpleNamespace(
        tool_engine=MagicMock(decide=MagicMock(return_value=decision)),
        mgmt_tools=MagicMock(), cognitive_pipeline=None, cognitive_mode=False,
        agent=None, bot=None,
    )
    runtime_ctx.mgmt_tools.get_automod_rules = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            after_state={"rules": [{"name": "Spam Guard", "enabled": True, "trigger_type": "keyword"}]},
        )
    )
    message = MagicMock()
    message.guild = MagicMock(name="Test Server")
    message.channel = MagicMock()
    message.author.guild_permissions.administrator = True
    routed = SimpleNamespace(route="info", action="info")

    with (
        patch("bot.context.ctx", runtime_ctx),
        patch("bot.handlers.message_handler.asyncio.to_thread", new=AsyncMock(return_value=decision)),
    ):
        reply = await _generate_agentic_reply(
            message=message, text="show the AutoMod rules", user="Owner",
            is_directed=True, is_dm=False, mentioned=True, server_name="Test Server",
            routed_intent=routed, tracker=None, progress_callback=None, event_loop=None,
        )

    assert "Spam Guard" in reply
    runtime_ctx.mgmt_tools.get_automod_rules.assert_awaited_once_with(message.guild)
