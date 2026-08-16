"""REAL simulation test lab — agentic/server tools integration scenarios."""
import json
import os
import sys as _sys
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from tests.conftest import MOCK as _discord_mock
from tests.conftest import REAL_DISCORD as _real_discord
from tests.conftest import reset_utils_get

_orig_discord = _sys.modules.pop("discord", None)
_sys.modules["discord"] = _discord_mock
reset_utils_get()

# Now import azure modules under test
from azure.agentic_tools import (
    _safe_path,
    execute_python,
    file_list,
    file_read,
    file_write,
    web_fetch,
    web_search,
)
from azure.discord_tools_expanded import DiscordManagementTools as ExpandedDMT
from azure.tools.server_tools import ServerHealthAnalyzer, ServerHealthReport

if _orig_discord is not None:
    _sys.modules["discord"] = _orig_discord


##############################################################################
#  HELPER FACTORIES
##############################################################################

def make_role(name="test-role", color=None, position=1, permissions=None,
              is_default=False, managed=False, mentionable=False, hoist=False,
              is_everyone=False, members=None, id=None):
    r = MagicMock(spec=_real_discord.Role)
    r.name = name
    r.color = color or MagicMock()
    r.color.value = 0x99AAB5
    r.position = position
    if is_everyone:
        r.permissions = MagicMock(spec=_real_discord.Permissions)
        r.permissions.administrator = True
        r.permissions.read_messages = True
        r.permissions.send_messages = True
    elif permissions is not None:
        r.permissions = permissions
    else:
        r.permissions = MagicMock(spec=_real_discord.Permissions)
        r.permissions.read_messages = False
        r.permissions.send_messages = False
    r.__le__ = MagicMock(side_effect=lambda other: r.position <= getattr(other, 'position', 0))
    r.__lt__ = MagicMock(side_effect=lambda other: r.position < getattr(other, 'position', 0))
    r.__ge__ = MagicMock(side_effect=lambda other: r.position >= getattr(other, 'position', 0))
    r.__gt__ = MagicMock(side_effect=lambda other: r.position > getattr(other, 'position', 0))
    r.mentionable = mentionable
    r.hoist = hoist
    r.is_default = MagicMock(return_value=is_default or name == "@everyone")
    r.managed = managed
    r.members = members or []
    r.id = id if id is not None else hash(name) % (2**31)
    r.edit = AsyncMock()
    r.delete = AsyncMock()
    return r


def _channel_type_spec(ch_type):
    ct = _discord_mock.ChannelType
    if ch_type == 0 or ch_type == ct.text:
        return _real_discord.TextChannel
    if ch_type == 1 or ch_type == ct.voice:
        return _real_discord.VoiceChannel
    if ch_type == ct.forum:
        return _real_discord.ForumChannel
    if ch_type == ct.stage_voice:
        return _real_discord.StageChannel
    if ch_type == ct.public_thread or ch_type == ct.private_thread:
        return _real_discord.Thread
    if ch_type == ct.news or ch_type == 5:
        return _real_discord.TextChannel
    return _real_discord.TextChannel


def make_channel(name="test-channel", ch_type=0, category=None, position=0,
                 topic=None, nsfw=False, bitrate=None, user_limit=None,
                 slowmode_delay=0, last_message_id=None, guild=None,
                 is_news=False, overwrites=None, available_tags=None):
    c = MagicMock(spec=_channel_type_spec(ch_type))
    c.name = name
    c.id = hash(name + str(ch_type)) % (2**31)
    c.type = ch_type
    c.position = position
    c.category = category
    c.topic = topic
    c.nsfw = nsfw
    c.bitrate = bitrate
    c.user_limit = user_limit
    c.slowmode_delay = slowmode_delay
    c.last_message_id = last_message_id
    c.guild = guild
    c.overwrites = {}
    if overwrites:
        c.overwrites = overwrites
    c.available_tags = available_tags or []
    c.is_news = MagicMock(return_value=is_news)
    c.edit = AsyncMock()
    c.delete = AsyncMock()
    c.clone = AsyncMock()
    c.create_invite = AsyncMock()
    c.invites = AsyncMock(return_value=[])
    c.set_permissions = AsyncMock()
    c.sync_permissions = AsyncMock()
    c.fetch_message = AsyncMock()
    c.pins = AsyncMock(return_value=[])
    c.purge = AsyncMock(return_value=[])
    c.create_thread = AsyncMock()
    c.follow = AsyncMock()
    c.webhooks = AsyncMock(return_value=[])
    c.send = AsyncMock()
    c.publish = AsyncMock()
    c.create_webhook = AsyncMock()
    c.overwrites_for = MagicMock(return_value=MagicMock())
    return c


def make_member(name="testuser", display_name=None, nick=None, id=None,
                roles=None, top_role=None, voice=None, guild=None,
                bot=False, status="online"):
    m = MagicMock(spec=_real_discord.Member)
    m.name = name
    m.display_name = display_name or name
    m.nick = nick
    m.id = id or hash(name) % (2**31)
    m.roles = roles or []
    if top_role is not None:
        m.top_role = top_role
    else:
        tr = MagicMock(spec=_real_discord.Role)
        tr.position = 0
        m.top_role = tr
    m.voice = voice
    m.guild = guild
    m.bot = bot
    m.status = _discord_mock.Status.online if status == "online" else _discord_mock.Status.offline
    m.mention = f"<@{m.id}>"
    m.guild_permissions = MagicMock(spec=["administrator"])
    m.guild_permissions.administrator = True
    m.edit = AsyncMock()
    m.kick = AsyncMock()
    m.ban = AsyncMock()
    m.timeout = AsyncMock()
    m.add_roles = AsyncMock()
    m.remove_roles = AsyncMock()
    m.move_to = AsyncMock()
    return m


def make_guild(name="TestGuild", id=12345, owner_id=99999,
               members=None, channels=None, categories=None, roles=None,
               threads=None, text_channels=None, voice_channels=None,
               forums=None, stage_channels=None, emojis=None):
    g = MagicMock(spec=_real_discord.Guild, name="mock.guild")
    g.name = name
    g.id = id
    g.owner_id = owner_id
    g.member_count = len(members) if members else 50
    g.members = members or []
    g.channels = channels or []
    g.categories = categories or []
    g.roles = roles or [make_role("@everyone", is_everyone=True, position=0)]
    g.threads = threads or []
    g.text_channels = text_channels or []
    g.voice_channels = voice_channels or []
    g.forums = forums or []
    g.stage_channels = stage_channels or []
    g.emojis = emojis or []
    g.description = "A test guild"
    g.verification_level = _discord_mock.VerificationLevel.low
    g.explicit_content_filter = _discord_mock.ExplicitContentFilter.disabled
    g.default_notifications = _discord_mock.NotificationLevel.only_mentions
    g.afk_channel = None
    g.afk_timeout = 300
    g.system_channel = None
    g.rules_channel = None
    g.public_updates_channel = None
    g.icon = None
    g.banner = None
    g.splash = None
    g.mfa_level = 0
    g.bitrate_limit = 384000
    g.premium_progress_bar_enabled = False
    g.preferred_locale = "en-US"
    g.vanity_url = None
    g.vanity_url_code = None
    g.me = make_member("AzureBot", top_role=make_role("Admin", position=100))
    g.create_category = AsyncMock()
    g.create_text_channel = AsyncMock()
    g.create_voice_channel = AsyncMock()
    g.create_forum = AsyncMock()
    g.create_stage_channel = AsyncMock()
    g.create_role = AsyncMock()
    g.fetch_member = AsyncMock()
    g.fetch_user = AsyncMock()
    g.fetch_scheduled_events = AsyncMock(return_value=[])
    g.fetch_stickers = AsyncMock(return_value=[])
    g.fetch_automod_rules = AsyncMock(return_value=[])
    g.fetch_onboarding = AsyncMock()
    g.fetch_active_threads = AsyncMock(return_value=[])
    g.get_member = MagicMock(return_value=None)
    g.get_channel = MagicMock(return_value=None)
    g.ban = AsyncMock()
    g.unban = AsyncMock()
    g.kick = AsyncMock()
    g.invites = AsyncMock(return_value=[])
    g.webhooks = AsyncMock(return_value=[])
    g.templates = AsyncMock(return_value=[])
    g.bans = MagicMock()
    g.bans.__aiter__ = MagicMock(return_value=iter([]))
    g.estimate_pruned_members = AsyncMock(return_value=0)
    g.prune_members = AsyncMock(return_value=0)
    g.create_scheduled_event = AsyncMock()
    g.create_template = AsyncMock()
    g.create_custom_emoji = AsyncMock()
    g.create_sticker = AsyncMock()
    g.create_automod_rule = AsyncMock()
    g.widget = AsyncMock()
    g.vanity_invite = AsyncMock()
    g.audit_logs = MagicMock()
    g.audit_logs.__aiter__ = MagicMock(return_value=iter([]))
    g.edit = AsyncMock()
    return g


def make_thread(name="test-thread", thread_id=None, parent=None, archived=False):
    t = MagicMock(spec=_real_discord.Thread)
    t.name = name
    t.id = thread_id or hash(name) % (2**31)
    t.parent = parent
    t.archived = archived
    t.auto_archive_duration = 1440
    t.slowmode_delay = 0
    t.edit = AsyncMock()
    t.delete = AsyncMock()
    t.join = AsyncMock()
    t.leave = AsyncMock()
    t.add_user = AsyncMock()
    t.remove_user = AsyncMock()
    return t


class FakeLLM:
    def __init__(self, response="{}"):
        self.response = response

    def chat(self, messages, **kwargs):
        return self.response


class ProgressContext:
    """Mimics a Discord text channel for sending progress messages."""
    def __init__(self):
        self.sent_messages = []
        self.channel = self
        self.id = 99999
        self.send = AsyncMock(side_effect=self._send)

    async def _send(self, *args, **kwargs):
        msg = MagicMock()
        msg.id = 50000 + len(self.sent_messages)
        msg.edit = AsyncMock()
        msg.add_reaction = AsyncMock()
        msg.channel = self
        self.sent_messages.append(msg)
        return msg


##############################################################################
#  FIXTURES
##############################################################################

@pytest.fixture(autouse=True)
def _reset_mock_after_test():
    yield
    reset_utils_get()

@pytest.fixture
def guild():
    return make_guild()


@pytest.fixture
def mixin_owner():
    bot = MagicMock(spec=["fetch_user", "wait_for", "user", "get_channel"])
    bot.fetch_user = AsyncMock()
    bot.wait_for = AsyncMock()
    bot.user = make_member("AzureBot")
    bot.get_channel = MagicMock()
    tools = ExpandedDMT(bot)
    return tools


@pytest.fixture
def ctx():
    return ProgressContext()


@pytest.fixture
def sandbox(tmp_path):
    old = os.environ.get("AZURE_SANDBOX_DIR")
    os.environ["AZURE_SANDBOX_DIR"] = str(tmp_path)
    yield tmp_path
    if old is not None:
        os.environ["AZURE_SANDBOX_DIR"] = old
    else:
        os.environ.pop("AZURE_SANDBOX_DIR", None)


@pytest.fixture
def member(guild):
    m = make_member("testuser", guild=guild)
    everyone_role = make_role("@everyone", is_everyone=True, position=0)
    m.top_role = everyone_role
    m.roles = [everyone_role]
    guild.members = [m]
    return m


##############################################################################
#  SCENARIO 1: Server Health Analysis
##############################################################################

class TestScenario1_ServerHealth:
    """Scenario 1: Full server health analysis with rich guild data."""

    def test_health_analysis_full_guild(self):
        """Build a guild with roles, channels, categories, bots — analyze it."""
        roles = [
            make_role("@everyone", is_everyone=True, position=0),
            make_role("Admin", position=5),
            make_role("Moderator", position=4),
            make_role("Member", position=3),
            make_role("Bot", position=2),
        ]
        cat_info = MagicMock(spec=_real_discord.CategoryChannel)
        cat_info.name = "Info"
        cat_info.position = 0
        cat_game = MagicMock(spec=_real_discord.CategoryChannel)
        cat_game.name = "Gaming"
        cat_game.position = 1
        categories = [cat_info, cat_game]

        members = []
        for i in range(20):
            members.append(make_member(f"user{i}", status="online"))
        for i in range(5):
            members.append(make_member(f"bot{i}", bot=True, status="online"))

        channels = [
            make_channel("welcome", last_message_id=100, guild=None),
            make_channel("rules", last_message_id=200, guild=None),
            make_channel("general", last_message_id=300, guild=None),
        ]

        g = make_guild(
            name="Health Test Server",
            members=members,
            roles=roles,
            categories=categories,
            channels=channels,
        )
        for ch in channels:
            ch.guild = g
        g.member_count = 25
        g.verification_level = _discord_mock.VerificationLevel.medium
        g.explicit_content_filter = _discord_mock.ExplicitContentFilter.all_members
        g.mfa_level = 1

        report = ServerHealthAnalyzer.analyze(g)

        assert isinstance(report, ServerHealthReport)
        assert report.server_name == "Health Test Server"
        assert report.member_count == 25
        assert 0 <= report.overall_score <= 100
        assert report.overall_grade in ("A", "B", "C", "D", "F")
        assert isinstance(report.activity, dict)
        assert isinstance(report.engagement, dict)
        assert isinstance(report.moderation, dict)
        assert isinstance(report.organization, dict)
        assert isinstance(report.security, dict)
        assert isinstance(report.recommendations, list)
        assert isinstance(report.quick_wins, list)
        assert isinstance(report.findings, list)

    def test_health_analysis_minimal_guild(self, guild):
        """Guild with no categories, no rules — expect low scores and recs."""
        report = ServerHealthAnalyzer.analyze(guild)
        assert report.overall_score < 80
        " ".join(report.recommendations).lower()
        assert any("categor" in r.lower() for r in report.recommendations)

    def test_health_analysis_high_bot_ratio(self):
        """Over 30% bots should produce a warning finding."""
        members = [make_member(f"user{i}") for i in range(6)]
        bots = [make_member(f"bot{i}", bot=True) for i in range(4)]
        g = make_guild(members=members + bots)
        g.member_count = 10
        report = ServerHealthAnalyzer.analyze(g)
        warnings = [f for f in report.findings if f.get("severity") == "warning"]
        bot_warnings = [f for f in warnings if "bot" in f.get("message", "").lower()]
        assert len(bot_warnings) > 0

    def test_health_format_report(self, guild):
        """format_report produces readable output with score and grade."""
        report = ServerHealthAnalyzer.analyze(guild)
        formatted = ServerHealthAnalyzer.format_report(report)
        assert "Server Health Report" in formatted
        assert report.server_name in formatted
        assert report.overall_grade in formatted
        assert "Category Scores" in formatted

    def test_sub_scores_exist(self, guild):
        """Each sub-score is a float or int between 0-100."""
        report = ServerHealthAnalyzer.analyze(guild)
        for key in ("activity", "engagement", "moderation", "organization", "security"):
            sub = getattr(report, key)
            assert isinstance(sub, dict)
            assert "score" in sub
            assert 0 <= sub["score"] <= 100

    def test_recommendations_present_when_missing_features(self):
        """Guild missing rules/system channel gets recommendations."""
        g = make_guild()
        g.rules_channel = None
        g.system_channel = None
        g.categories = []
        report = ServerHealthAnalyzer.analyze(g)
        assert len(report.recommendations) >= 3


##############################################################################
#  SCENARIO 2: Full Server Creation Workflow
##############################################################################

class TestScenario2_ServerCreation:
    """Scenario 2: generate_plan → execute_plan with mocked LLM."""

    @pytest.mark.asyncio
    async def test_generate_plan_returns_parsed_json(self, guild, mixin_owner):
        """generate_plan calls LLM and returns parsed plan dict."""
        llm = FakeLLM(response=json.dumps({
            "analysis": "Create a gaming server setup.",
            "steps": [
                {"action": "create_role", "name": "Gamer", "color": "green"},
                {"action": "create_category", "name": "Welcome"},
                {"action": "create_channel", "name": "welcome", "type": "text", "category": "Welcome"},
            ]
        }))
        plan = await mixin_owner.generate_plan(guild, "set up gaming server", llm)
        assert "analysis" in plan
        assert "steps" in plan
        assert len(plan["steps"]) == 3

    @pytest.mark.asyncio
    async def test_generate_plan_fallback_on_bad_json(self, guild, mixin_owner):
        """Bad LLM JSON falls back gracefully."""
        llm = FakeLLM(response="this is not json")
        plan = await mixin_owner.generate_plan(guild, "do stuff", llm)
        assert "steps" in plan

    @pytest.mark.asyncio
    async def test_execute_plan_create_role(self, guild, mixin_owner, ctx):
        """Execute a single create_role step."""
        role = make_role("Gamer", id=500)
        guild.create_role = AsyncMock(return_value=role)
        plan = {
            "analysis": "Create a gamer role",
            "steps": [{"action": "create_role", "name": "Gamer", "color": "green"}]
        }
        results = await mixin_owner.execute_plan(
            guild, plan, ctx,
            confirm_destructive=False,
            requester_name="Tester",
            requester_id=99999,
            require_authorization=False,
        )
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].action == "create_role"
        guild.create_role.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_plan_create_category_then_channel(self, guild, mixin_owner, ctx):
        """Execute role → category → channel in sequence."""
        cat_mock = MagicMock()
        cat_mock.id = 200
        cat_mock.name = "Welcome"
        guild.create_category = AsyncMock(return_value=cat_mock)
        ch_mock = MagicMock()
        ch_mock.id = 300
        guild.create_text_channel = AsyncMock(return_value=ch_mock)
        role_mock = make_role("Member", id=400)
        guild.create_role = AsyncMock(return_value=role_mock)

        plan = {
            "analysis": "Build server foundation",
            "steps": [
                {"action": "create_role", "name": "Member", "color": "blue", "permissions": []},
                {"action": "create_category", "name": "Welcome"},
                {"action": "create_channel", "name": "welcome", "type": "text", "category": "Welcome"},
            ]
        }
        results = await mixin_owner.execute_plan(
            guild, plan, ctx,
            confirm_destructive=False,
            requester_name="Tester",
            requester_id=99999,
            require_authorization=False,
        )
        assert len(results) == 3
        assert all(r.success for r in results)
        guild.create_role.assert_awaited_once()
        guild.create_category.assert_awaited_once()
        guild.create_text_channel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_plan_set_permissions(self, guild, mixin_owner, ctx):
        """Execute set_permissions step."""
        role = make_role("Mod", id=600)
        guild.roles.append(role)
        ch = make_channel("mod-only", guild=guild)
        guild.channels = [ch]
        plan = {
            "analysis": "Set permissions",
            "steps": [{"action": "set_permissions", "channel": "mod-only", "role": "Mod", "allow": ["send_messages"], "deny": []}]
        }
        results = await mixin_owner.execute_plan(
            guild, plan, ctx,
            confirm_destructive=False,
            requester_name="Tester",
            requester_id=99999,
            require_authorization=False,
        )
        assert len(results) == 1
        assert results[0].success is True

    @pytest.mark.asyncio
    async def test_execute_plan_empty_steps(self, guild, mixin_owner, ctx):
        """Plan with no steps returns empty list."""
        plan = {"analysis": "nothing", "steps": []}
        results = await mixin_owner.execute_plan(
            guild, plan, ctx,
            confirm_destructive=False,
            requester_name="Tester",
            requester_id=99999,
            require_authorization=False,
        )
        assert results == []


##############################################################################
#  SCENARIO 3: Channel Management Workflow
##############################################################################

class TestScenario3_ChannelManagement:
    """Scenario 3: Full channel lifecycle — create, edit, move, clone, delete."""

    @pytest.mark.asyncio
    async def test_create_all_channel_types(self, guild, mixin_owner):
        """Create text, voice, forum, stage, and news channels."""
        for ch_type, method_name, _ch_mock_name in [
            ("text", "create_text_channel", "text"),
            ("voice", "create_voice_channel", "voice"),
            ("forum", "create_forum", "forum"),
            ("stage_voice", "create_stage_channel", "stage"),
        ]:
            ch_mock = MagicMock()
            ch_mock.id = hash(ch_type) % (2**31)
            setattr(guild, method_name, AsyncMock(return_value=ch_mock))
            result = await mixin_owner.create_channel(guild, f"test-{ch_type}", channel_type=ch_type)
            assert result.success is True, f"Failed to create {ch_type}: {result.error}"
            assert result.action == "create_channel"

    @pytest.mark.asyncio
    async def test_create_channel_with_category_and_topic(self, guild, mixin_owner):
        """Create channel with category reference and topic."""
        cat = MagicMock()
        cat.name = "Info"
        guild.categories = [cat]
        ch_mock = MagicMock()
        ch_mock.id = 301
        guild.create_text_channel = AsyncMock(return_value=ch_mock)
        result = await mixin_owner.create_channel(guild, "announcements", category="Info", topic="Server news")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_edit_channel_properties(self, guild, mixin_owner):
        """Edit channel name, topic, nsfw."""
        ch = make_channel("general", guild=guild)
        guild.channels = [ch]
        guild.text_channels = [ch]
        result = await mixin_owner.edit_channel(guild, "general", name="main-chat", topic="Chat here")
        assert result.success is True
        assert result.detail == "Updated"
        ch.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_edit_channel_not_found(self, guild, mixin_owner):
        """Edit non-existent channel returns error."""
        guild.channels = []
        result = await mixin_owner.edit_channel(guild, "ghost")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_move_channel_between_categories(self, guild, mixin_owner):
        """Move channel from one category to another."""
        cat_old = MagicMock()
        cat_old.name = "Old"
        cat_new = MagicMock()
        cat_new.name = "New"
        guild.categories = [cat_old, cat_new]
        ch = make_channel("moveme", guild=guild, category=cat_old)
        guild.channels = [ch]
        result = await mixin_owner.move_channel(guild, "moveme", "New")
        assert result.success is True
        ch.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clone_channel(self, guild, mixin_owner):
        """Clone a channel."""
        orig = make_channel("original", guild=guild)
        cloned = make_channel("original-copy")
        cloned.id = 555
        orig.clone = AsyncMock(return_value=cloned)
        guild.channels = [orig]
        result = await mixin_owner.clone_channel(guild, "original")
        assert result.success is True
        assert result.target_id == 555

    @pytest.mark.asyncio
    async def test_delete_channel(self, guild, mixin_owner):
        """Delete a channel."""
        ch = make_channel("delete-me", guild=guild)
        result = await mixin_owner.delete_channel(ch)
        assert result.success is True
        ch.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_channel_permissions(self, guild, mixin_owner):
        """Set permission overwrites for a role on a channel."""
        role = make_role("Mod")
        guild.roles.append(role)
        ch = make_channel("mod-only", guild=guild)
        result = await mixin_owner.set_channel_permissions(ch, "Mod", allow=["send_messages", "read_messages"])
        assert result.success is True
        ch.set_permissions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clear_channel_permissions(self, guild, mixin_owner):
        """Clear permission overwrites."""
        role = make_role("Mod")
        guild.roles.append(role)
        ch = make_channel("mod-only", guild=guild)
        ch.overwrites = {role: MagicMock()}
        result = await mixin_owner.clear_channel_permissions(ch, "Mod")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_purge_messages(self, guild, mixin_owner):
        """Purge messages with limit."""
        ch = make_channel("chat", guild=guild)
        ch.purge = AsyncMock(return_value=[1, 2, 3])
        result = await mixin_owner.purge_messages(ch, 50)
        assert result.success is True
        assert "Deleted 3" in result.detail

    @pytest.mark.asyncio
    async def test_create_invite(self, guild, mixin_owner):
        """Create channel invite."""
        ch = make_channel("general", guild=guild)
        inv = MagicMock()
        inv.code = "abc123"
        ch.create_invite = AsyncMock(return_value=inv)
        result = await mixin_owner.create_invite(ch)
        assert result.success is True
        assert "abc123" in result.detail

    @pytest.mark.asyncio
    async def test_revoke_invite(self, guild, mixin_owner):
        """Revoke an invite by code."""
        inv = MagicMock()
        inv.code = "abc"
        inv.delete = AsyncMock()
        guild.invites = AsyncMock(return_value=[inv])
        result = await mixin_owner.revoke_invite(guild, "abc")
        assert result.success is True
        inv.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pin_unpin_message(self, guild, mixin_owner):
        """Pin and unpin a message."""
        ch = make_channel("general", guild=guild)
        msg = MagicMock()
        msg.pin = AsyncMock()
        msg.unpin = AsyncMock()
        ch.fetch_message = AsyncMock(return_value=msg)

        pin_result = await mixin_owner.pin_message(ch, 42)
        assert pin_result.success is True
        msg.pin.assert_awaited_once()

        unpin_result = await mixin_owner.unpin_message(ch, 42)
        assert unpin_result.success is True
        msg.unpin.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_follow_channel(self, guild, mixin_owner):
        """Follow announcement channel."""
        news_ch = make_channel("announcements", is_news=True, guild=guild)
        target = make_channel("follow-target", guild=guild)
        guild.get_channel = MagicMock(side_effect=lambda ch_id: target)
        news_ch.follow = AsyncMock()
        result = await mixin_owner.follow_channel(news_ch, 999)
        assert result.success is True
        news_ch.follow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_follow_not_news_fails(self, guild, mixin_owner):
        """Follow non-news channel returns error."""
        ch = make_channel("general", guild=guild)
        result = await mixin_owner.follow_channel(ch, 999)
        assert result.success is False
        assert "not an announcement" in result.error.lower()


##############################################################################
#  SCENARIO 4: Role Management Workflow
##############################################################################

class TestScenario4_RoleManagement:
    """Scenario 4: Full role lifecycle — create, edit, assign, delete."""

    @pytest.mark.asyncio
    async def test_create_role_with_colors(self, guild, mixin_owner):
        """Create roles with various color names."""
        for color_name in ("red", "blue", "green", "purple", "yellow", None):
            role = make_role(f"Role-{color_name or 'default'}", id=hash(str(color_name)) % (2**31))
            guild.create_role = AsyncMock(return_value=role)
            kwargs = {"name": f"Role-{color_name or 'default'}"}
            if color_name:
                kwargs["color"] = color_name
            result = await mixin_owner.create_role(guild, **kwargs)
            assert result.success is True, f"Failed with color={color_name}"

    @pytest.mark.asyncio
    async def test_edit_role_name_color_permissions(self, guild, mixin_owner):
        """Edit role name, color, hoist, mentionable."""
        role = make_role("Mod")
        role.color.__str__ = lambda self: "#99aab5"
        role.hoist = False
        role.mentionable = False
        guild.roles = [make_role("@everyone", is_everyone=True), role]
        result = await mixin_owner.edit_role(guild, "Mod", name="Senior Mod", color="blue", hoist=True, mentionable=True)
        assert result.success is True
        assert result.detail == "Updated"
        role.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_assign_role_to_member(self, guild, mixin_owner, member):
        """Assign a role to a member."""
        role = make_role("Gamer")
        guild.roles.append(role)
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        result = await mixin_owner.assign_role(guild, "testuser", "Gamer")
        assert result.success is True
        member.add_roles.assert_awaited_once_with(role, reason=ANY)

    @pytest.mark.asyncio
    async def test_remove_role_from_member(self, guild, mixin_owner, member):
        """Remove a role from a member."""
        role = make_role("Gamer")
        guild.roles.append(role)
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        result = await mixin_owner.remove_role(guild, "testuser", "Gamer")
        assert result.success is True
        member.remove_roles.assert_awaited_once_with(role, reason=ANY)

    @pytest.mark.asyncio
    async def test_delete_role(self, guild, mixin_owner):
        """Delete a role."""
        role = make_role("TempRole")
        guild.roles.append(role)
        result = await mixin_owner.delete_role(guild, "TempRole")
        assert result.success is True
        role.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_everyone_protected(self, guild, mixin_owner):
        """Deleting @everyone is prevented."""
        result = await mixin_owner.delete_role(guild, "@everyone")
        assert result.success is False
        assert "everyone" in result.error.lower()

    @pytest.mark.asyncio
    async def test_create_role_with_position(self, guild, mixin_owner):
        """Create role and set position."""
        role = make_role("Admin")
        role.edit = AsyncMock()
        guild.create_role = AsyncMock(return_value=role)
        result = await mixin_owner.create_role(guild, "Admin", position=5)
        assert result.success is True
        role.edit.assert_awaited_once_with(position=5)

    @pytest.mark.asyncio
    async def test_edit_role_not_found(self, guild, mixin_owner):
        """Edit non-existent role returns error."""
        result = await mixin_owner.edit_role(guild, "Ghost")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_delete_role_not_found(self, guild, mixin_owner):
        """Delete non-existent role returns error."""
        result = await mixin_owner.delete_role(guild, "Ghost")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_assign_role_not_found_member(self, guild, mixin_owner):
        """Assign role to non-existent member returns error."""
        mixin_owner._resolve_member = AsyncMock(return_value=None)
        result = await mixin_owner.assign_role(guild, "ghost", "Gamer")
        assert result.success is False
        assert "not found" in result.error


##############################################################################
#  SCENARIO 5: Member Management Workflow
##############################################################################

class TestScenario5_MemberManagement:
    """Scenario 5: Full member management — kick, ban, timeout, nickname, voice."""

    @pytest.mark.asyncio
    async def test_kick_member(self, guild, mixin_owner, member):
        """Kick a member from the guild."""
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        guild.me = make_member("bot", top_role=make_role("Admin", position=100))
        result = await mixin_owner.kick_member(guild, "testuser")
        assert result.success is True
        member.kick.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_kick_member_role_too_high(self, guild, mixin_owner, member):
        """Kick fails if bot's role is lower than member's."""
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        guild.me = make_member("bot", top_role=make_role("LowRole", position=1))
        member.top_role = make_role("HighRole", position=100)
        result = await mixin_owner.kick_member(guild, "testuser")
        assert result.success is False
        assert "not high enough" in result.error

    @pytest.mark.asyncio
    async def test_ban_member(self, guild, mixin_owner, member):
        """Ban a member from the guild."""
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        result = await mixin_owner.ban_member(guild, "testuser")
        assert result.success is True
        member.ban.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ban_member_not_in_guild_falls_back(self, guild, mixin_owner):
        """Ban by ID when member not in guild."""
        mixin_owner._resolve_member = AsyncMock(return_value=None)
        user = MagicMock()
        user.id = 888
        mixin_owner.bot.fetch_user = AsyncMock(return_value=user)
        result = await mixin_owner.ban_member(guild, "888", delete_message_days=1)
        assert result.success is True
        guild.ban.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unban_member(self, guild, mixin_owner):
        """Unban a user by ID."""
        user = MagicMock()
        user.id = 777
        mixin_owner.bot.fetch_user = AsyncMock(return_value=user)
        result = await mixin_owner.unban_member(guild, 777)
        assert result.success is True
        guild.unban.assert_awaited_once_with(user, reason="Azure")

    @pytest.mark.asyncio
    async def test_timeout_member(self, guild, mixin_owner, member):
        """Timeout a member."""
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        result = await mixin_owner.timeout_member(guild, "testuser", duration_minutes=30)
        assert result.success is True
        member.timeout.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_nickname(self, guild, mixin_owner, member):
        """Set a member's nickname."""
        member.nick = "old_nick"
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        result = await mixin_owner.set_nickname(guild, "testuser", "new_nick")
        assert result.success is True
        member.edit.assert_awaited_once_with(nick="new_nick", reason=ANY)

    @pytest.mark.asyncio
    async def test_move_member_to_voice(self, guild, mixin_owner, member):
        """Move member to a voice channel."""
        member.voice = MagicMock(spec=["channel"])
        member.voice.channel = MagicMock()
        vc = make_channel("Game Voice", ch_type=1, guild=guild)
        guild.voice_channels = [vc]
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        result = await mixin_owner.move_member_to_voice(guild, "testuser", "Game Voice")
        assert result.success is True
        member.move_to.assert_awaited_once_with(vc, reason=ANY)

    @pytest.mark.asyncio
    async def test_deafen_member(self, guild, mixin_owner, member):
        """Deafen a member in voice."""
        member.voice = MagicMock(spec=["channel"])
        member.voice.channel = MagicMock()
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        result = await mixin_owner.deafen_member(guild, "testuser", deafen=True)
        assert result.success is True
        assert result.action == "deafen"

    @pytest.mark.asyncio
    async def test_mute_member(self, guild, mixin_owner, member):
        """Mute a member in voice."""
        member.voice = MagicMock(spec=["channel"])
        member.voice.channel = MagicMock()
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        result = await mixin_owner.mute_member(guild, "testuser", mute=True)
        assert result.success is True
        assert result.action == "mute"

    @pytest.mark.asyncio
    async def test_disconnect_member(self, guild, mixin_owner, member):
        """Disconnect member from voice."""
        member.voice = MagicMock(spec=["channel"])
        member.voice.channel = MagicMock()
        result = await mixin_owner.disconnect_voice(member)
        assert result.success is True
        member.move_to.assert_awaited_once_with(None, reason=ANY)

    @pytest.mark.asyncio
    async def test_member_not_found_errors(self, guild, mixin_owner):
        """All member tools return error for unknown members."""
        mixin_owner._resolve_member = AsyncMock(return_value=None)
        for method, args in [
            ("kick_member", ("ghost",)),
            ("ban_member", ("ghost",)),
            ("timeout_member", ("ghost", 60)),
            ("set_nickname", ("ghost", "new")),
        ]:
            result = await getattr(mixin_owner, method)(guild, *args)
            assert result.success is False


##############################################################################
#  SCENARIO 6: Server Config Workflow
##############################################################################

class TestScenario6_ServerConfig:
    """Scenario 6: Full server configuration — name, security, channels, community."""

    @pytest.mark.asyncio
    async def test_set_server_name(self, guild, mixin_owner):
        """Change server name."""
        result = await mixin_owner.set_server_name(guild, "New Name")
        assert result.success is True
        guild.edit.assert_awaited_once_with(name="New Name", reason=ANY)

    @pytest.mark.asyncio
    async def test_set_verification_level(self, guild, mixin_owner):
        """Set verification level to medium."""
        result = await mixin_owner.set_verification_level(guild, "medium")
        assert result.success is True
        guild.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_content_filter(self, guild, mixin_owner):
        """Set explicit content filter."""
        result = await mixin_owner.set_content_filter(guild, "all_members")
        assert result.success is True
        guild.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_notifications(self, guild, mixin_owner):
        """Set default notification level."""
        result = await mixin_owner.set_notifications(guild, "mentions_only")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_afk_channel(self, guild, mixin_owner):
        """Set AFK channel and timeout."""
        vc = make_channel("AFK", ch_type=1, guild=guild)
        guild.voice_channels = [vc]
        result = await mixin_owner.set_afk_channel(guild, "AFK", timeout=600)
        assert result.success is True
        guild.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_system_channel(self, guild, mixin_owner):
        """Set system channel for welcome messages."""
        ch = make_channel("welcome", guild=guild)
        guild.text_channels = [ch]
        result = await mixin_owner.set_system_channel(guild, "welcome")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_rules_channel(self, guild, mixin_owner):
        """Set rules channel."""
        ch = make_channel("rules", guild=guild)
        guild.text_channels = [ch]
        result = await mixin_owner.set_rules_channel(guild, "rules")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_public_updates_channel(self, guild, mixin_owner):
        """Set public updates channel."""
        ch = make_channel("updates", guild=guild)
        guild.text_channels = [ch]
        result = await mixin_owner.set_public_updates_channel(guild, "updates")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_server_description(self, guild, mixin_owner):
        """Set server description."""
        result = await mixin_owner.set_server_description(guild, "A cool server")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_server_icon(self, guild, mixin_owner):
        """Set server icon with byte data."""
        result = await mixin_owner.set_server_icon(guild, image_data=b"fake_image_bytes")
        assert result.success is True
        guild.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enable_community_mode(self, guild, mixin_owner):
        """Enable community mode with required channels."""
        rules = make_channel("rules", guild=guild)
        updates = make_channel("updates", guild=guild)
        guild.text_channels = [rules, updates]
        result = await mixin_owner.enable_community_mode(guild, "rules", "updates")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_welcome_screen(self, guild, mixin_owner):
        """Set welcome screen with channels."""
        ch = make_channel("welcome", guild=guild)
        guild.channels = [ch]
        result = await mixin_owner.set_welcome_screen(guild, "Welcome!", ["welcome"])
        assert result.success is True

    @pytest.mark.asyncio
    async def test_create_server_template(self, guild, mixin_owner):
        """Create a server template."""
        template_mock = MagicMock()
        template_mock.code = "abc123"
        template_mock.url = "https://discord.new/abc123"
        guild.create_template = AsyncMock(return_value=template_mock)
        result = await mixin_owner.create_server_template(guild, "My Template")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_afk_channel_not_found(self, guild, mixin_owner):
        """Set AFK with non-existent voice channel fails."""
        guild.voice_channels = []
        result = await mixin_owner.set_afk_channel(guild, "nonexistent")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_set_verification_level_invalid(self, guild, mixin_owner):
        """Invalid verification level falls back to low."""
        result = await mixin_owner.set_verification_level(guild, "super_high")
        assert result.success is True


##############################################################################
#  SCENARIO 7: AutoMod Rules
##############################################################################

class TestScenario7_AutoMod:
    """Scenario 7: AutoMod rules — create, list, edit, delete."""

    @pytest.mark.asyncio
    async def test_create_keyword_automod_rule(self, guild, mixin_owner):
        """Create keyword filter rule."""
        rule_mock = MagicMock()
        rule_mock.id = 100
        guild.create_automod_rule = AsyncMock(return_value=rule_mock)
        result = await mixin_owner.create_automod_rule(
            guild, "Bad Words", rule_type="keyword",
            keywords=["badword1", "badword2"], actions=["block"]
        )
        assert result.success is True
        guild.create_automod_rule.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_spam_automod_rule(self, guild, mixin_owner):
        """Create mention_spam rule."""
        rule_mock = MagicMock()
        rule_mock.id = 101
        guild.create_automod_rule = AsyncMock(return_value=rule_mock)
        result = await mixin_owner.create_automod_rule(
            guild, "Mention Spam", rule_type="mention_spam",
            mention_limit=5, actions=["block", "timeout"]
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_enable_spam_filter(self, guild, mixin_owner):
        """enable_spam_filter creates both mention and message spam rules."""
        rule_mock = MagicMock()
        rule_mock.id = 200
        guild.create_automod_rule = AsyncMock(return_value=rule_mock)
        result = await mixin_owner.enable_spam_filter(guild, mention_limit=5)
        assert result.success is True
        assert guild.create_automod_rule.call_count == 2

    @pytest.mark.asyncio
    async def test_enable_keyword_filter(self, guild, mixin_owner):
        """enable_keyword_filter creates a keyword rule."""
        rule_mock = MagicMock()
        rule_mock.id = 300
        guild.create_automod_rule = AsyncMock(return_value=rule_mock)
        result = await mixin_owner.enable_keyword_filter(guild, ["bad", "words"])
        assert result.success is True

    @pytest.mark.asyncio
    async def test_get_automod_rules(self, guild, mixin_owner):
        """List automod rules."""
        guild.fetch_automod_rules = AsyncMock(return_value=[])
        result = await mixin_owner.get_automod_rules(guild)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_edit_automod_rule(self, guild, mixin_owner):
        """Edit/enable-disable automod rule."""
        rule = MagicMock()
        rule.name = "Bad Words"
        rule.enabled = True
        rule.trigger_type = "keyword"
        rule.id = 400
        rule.edit = AsyncMock()
        guild.fetch_automod_rules = AsyncMock(return_value=[rule])
        result = await mixin_owner.edit_automod_rule(guild, "Bad Words", enabled=False)
        assert result.success is True
        rule.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_automod_rule(self, guild, mixin_owner):
        """Delete automod rule."""
        rule = MagicMock()
        rule.name = "Bad Words"
        rule.delete = AsyncMock()
        guild.fetch_automod_rules = AsyncMock(return_value=[rule])
        result = await mixin_owner.delete_automod_rule(guild, "Bad Words")
        assert result.success is True
        rule.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_automod_rule_not_found(self, guild, mixin_owner):
        """Delete non-existent rule."""
        guild.fetch_automod_rules = AsyncMock(return_value=[])
        result = await mixin_owner.delete_automod_rule(guild, "Ghost")
        assert result.success is False
        assert "not found" in result.error


##############################################################################
#  SCENARIO 8: Thread Operations
##############################################################################

class TestScenario8_ThreadOperations:
    """Scenario 8: Thread lifecycle — create, archive, delete, manage members."""

    @pytest.mark.asyncio
    async def test_create_public_thread(self, guild, mixin_owner):
        """Create public thread from a text channel."""
        ch = make_channel("general", guild=guild)
        thread = make_thread("discussion")
        ch.create_thread = AsyncMock(return_value=thread)
        result = await mixin_owner.create_thread(ch, "discussion", thread_type="public")
        assert result.success is True
        assert result.action == "create_thread"

    @pytest.mark.asyncio
    async def test_create_private_thread_from_message(self, guild, mixin_owner):
        """Create private thread from an existing message."""
        ch = make_channel("general", guild=guild)
        thread = make_thread("private-chat")
        msg = MagicMock()
        msg.create_thread = AsyncMock(return_value=thread)
        ch.fetch_message = AsyncMock(return_value=msg)
        result = await mixin_owner.create_thread(ch, "private-chat", message_id=100, thread_type="private")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_archive_thread(self, guild, mixin_owner):
        """Archive a thread."""
        thread = make_thread("archive-me")
        result = await mixin_owner.archive_thread(thread)
        assert result.success is True
        thread.edit.assert_awaited_once_with(archived=True, reason=ANY)

    @pytest.mark.asyncio
    async def test_delete_thread(self, guild, mixin_owner):
        """Delete a thread."""
        thread = make_thread("delete-me")
        result = await mixin_owner.delete_thread(thread)
        assert result.success is True
        thread.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rename_thread(self, guild, mixin_owner):
        """Rename a thread."""
        thread = make_thread("old-name")
        result = await mixin_owner.rename_thread(thread, "new-name")
        assert result.success is True
        thread.edit.assert_awaited_once_with(name="new-name", reason=ANY)

    @pytest.mark.asyncio
    async def test_set_thread_slowmode(self, guild, mixin_owner):
        """Set thread slowmode."""
        thread = make_thread("slow-down")
        result = await mixin_owner.set_thread_slowmode(thread, 30)
        assert result.success is True
        thread.edit.assert_awaited_once_with(slowmode_delay=30, reason=ANY)

    @pytest.mark.asyncio
    async def test_set_thread_auto_archive(self, guild, mixin_owner):
        """Set thread auto-archive duration."""
        thread = make_thread("archive-later")
        result = await mixin_owner.set_thread_auto_archive(thread, 4320)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_add_remove_thread_member(self, guild, mixin_owner, member):
        """Add and remove a member from a thread."""
        thread = make_thread("team-chat")
        add_result = await mixin_owner.add_thread_member(thread, member)
        assert add_result.success is True
        thread.add_user.assert_awaited_once_with(member)

        remove_result = await mixin_owner.remove_thread_member(thread, member)
        assert remove_result.success is True
        thread.remove_user.assert_awaited_once_with(member)

    @pytest.mark.asyncio
    async def test_join_leave_thread(self, guild, mixin_owner):
        """Join and leave a thread."""
        thread = make_thread("public-chat")
        join_result = await mixin_owner.join_thread(thread)
        assert join_result.success is True
        thread.join.assert_awaited_once()

        leave_result = await mixin_owner.leave_thread(thread)
        assert leave_result.success is True
        thread.leave.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_archived_threads(self, guild, mixin_owner):
        """List archived threads."""
        guild.fetch_active_threads = AsyncMock(return_value=[])
        result = await mixin_owner.list_archived_threads(guild, public=True)
        assert result.success is True


##############################################################################
#  SCENARIO 9: Webhooks & Emojis & Stickers
##############################################################################

class TestScenario9_WebhooksEmojisStickers:
    """Scenario 9: Webhooks, emoji, and sticker operations."""

    @pytest.mark.asyncio
    async def test_create_webhook(self, guild, mixin_owner):
        """Create a webhook in a text channel."""
        ch = make_channel("general", guild=guild)
        guild.channels = [ch]
        wh_mock = MagicMock()
        wh_mock.id = 500
        ch.create_webhook = AsyncMock(return_value=wh_mock)
        result = await mixin_owner.create_webhook(guild, "general", "My Webhook")
        assert result.success is True
        ch.create_webhook.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_webhook_not_text_channel(self, guild, mixin_owner):
        """Creating webhook in non-text channel fails."""
        vc = make_channel("voice", ch_type=1, guild=guild)
        guild.channels = [vc]
        result = await mixin_owner.create_webhook(guild, "voice", "Bad Webhook")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_delete_webhook(self, guild, mixin_owner):
        """Delete a webhook by name."""
        wh = MagicMock()
        wh.name = "My Webhook"
        wh.delete = AsyncMock()
        guild.webhooks = AsyncMock(return_value=[wh])
        result = await mixin_owner.delete_webhook(guild, "My Webhook")
        assert result.success is True
        wh.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_emoji(self, guild, mixin_owner):
        """Create a custom emoji."""
        emoji_mock = MagicMock()
        emoji_mock.id = 600
        emoji_mock.name = "myemoji"
        guild.create_custom_emoji = AsyncMock(return_value=emoji_mock)
        result = await mixin_owner.create_emoji(guild, "myemoji", image_data=b"fake_png")
        assert result.success is True
        guild.create_custom_emoji.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_emoji(self, guild, mixin_owner):
        """Delete a custom emoji."""
        emoji = MagicMock()
        emoji.name = "myemoji"
        emoji.id = 601
        emoji.delete = AsyncMock()
        guild.emojis = [emoji]
        result = await mixin_owner.delete_emoji(guild, "myemoji")
        assert result.success is True
        emoji.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_emoji_not_found(self, guild, mixin_owner):
        """Delete non-existent emoji returns error."""
        guild.emojis = []
        result = await mixin_owner.delete_emoji(guild, "ghost")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_create_sticker(self, guild, mixin_owner):
        """Create a sticker from byte data."""
        sticker_mock = MagicMock()
        sticker_mock.id = 700
        sticker_mock.name = "mysticker"
        guild.create_sticker = AsyncMock(return_value=sticker_mock)
        result = await mixin_owner.create_sticker(guild, "mysticker", "A sticker", "👍", file_data=b"fake_png")
        assert result.success is True
        guild.create_sticker.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_sticker(self, guild, mixin_owner):
        """Delete a sticker by name."""
        sticker = MagicMock()
        sticker.name = "mysticker"
        sticker.id = 701
        sticker.delete = AsyncMock()
        guild.fetch_stickers = AsyncMock(return_value=[sticker])
        result = await mixin_owner.delete_sticker(guild, "mysticker")
        assert result.success is True
        sticker.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_sticker_not_found(self, guild, mixin_owner):
        """Delete non-existent sticker returns error."""
        guild.fetch_stickers = AsyncMock(return_value=[])
        result = await mixin_owner.delete_sticker(guild, "ghost")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_get_channel_webhooks(self, guild, mixin_owner):
        """Get webhooks for a channel."""
        ch = make_channel("general", guild=guild)
        guild.channels = [ch]
        wh = MagicMock()
        wh.name = "wh1"
        wh.id = 1
        wh.channel.name = "general"
        ch.webhooks = AsyncMock(return_value=[wh])
        result = await mixin_owner.get_channel_webhooks(guild, "general")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_get_guild_webhooks(self, guild, mixin_owner):
        """Get all guild webhooks."""
        guild.webhooks = AsyncMock(return_value=[])
        result = await mixin_owner.get_guild_webhooks(guild)
        assert result.success is True


##############################################################################
#  SCENARIO 10: Scheduled Events
##############################################################################

class TestScenario10_ScheduledEvents:
    """Scenario 10: Scheduled events — create voice/stage/external, edit, delete."""

    @pytest.mark.asyncio
    async def test_create_voice_event(self, guild, mixin_owner):
        """Create a voice channel event."""
        vc = make_channel("Game Night", ch_type=1, guild=guild)
        guild.channels = [vc]
        event_mock = MagicMock()
        event_mock.id = 800
        guild.create_scheduled_event = AsyncMock(return_value=event_mock)
        result = await mixin_owner.create_scheduled_event(
            guild, "Game Night", "Play together",
            "2026-07-20T18:00:00+00:00", end_time="2026-07-20T21:00:00+00:00",
            channel_name="Game Night"
        )
        assert result.success is True
        guild.create_scheduled_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_external_event(self, guild, mixin_owner):
        """Create an external (location-based) event."""
        event_mock = MagicMock()
        event_mock.id = 801
        guild.create_scheduled_event = AsyncMock(return_value=event_mock)
        result = await mixin_owner.create_scheduled_event(
            guild, "Meetup", "In person meeting",
            "2026-08-01T12:00:00+00:00", end_time="2026-08-01T14:00:00+00:00",
            location="Central Park"
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_edit_scheduled_event(self, guild, mixin_owner):
        """Edit an existing event."""
        event = MagicMock()
        event.name = "Game Night"
        event.edit = AsyncMock()
        guild.fetch_scheduled_events = AsyncMock(return_value=[event])
        result = await mixin_owner.edit_scheduled_event(guild, "Game Night", name="Game Night v2", description="Updated")
        assert result.success is True
        event.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_edit_scheduled_event_not_found(self, guild, mixin_owner):
        """Edit non-existent event returns error."""
        guild.fetch_scheduled_events = AsyncMock(return_value=[])
        result = await mixin_owner.edit_scheduled_event(guild, "Ghost")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_delete_scheduled_event(self, guild, mixin_owner):
        """Delete an event."""
        event = MagicMock()
        event.name = "Game Night"
        event.delete = AsyncMock()
        guild.fetch_scheduled_events = AsyncMock(return_value=[event])
        result = await mixin_owner.delete_scheduled_event(guild, "Game Night")
        assert result.success is True
        event.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_scheduled_event_not_found(self, guild, mixin_owner):
        """Delete non-existent event returns error."""
        guild.fetch_scheduled_events = AsyncMock(return_value=[])
        result = await mixin_owner.delete_scheduled_event(guild, "Ghost")
        assert result.success is False
        assert "not found" in result.error


##############################################################################
#  SCENARIO 11: Agentic Tools
##############################################################################

class TestScenario11_AgenticTools:
    """Scenario 11: Agentic tools — web search, web fetch, file ops, code exec."""

    @patch("azure.agentic_tools._fetch_url")
    def test_web_search_returns_results(self, mock_fetch):
        """web_search returns formatted results."""
        mock_fetch.return_value = json.dumps({
            "query": {"search": [{"title": "Python", "snippet": "Python is a <b>language</b>"}]}
        }).encode()
        result = web_search("Python", max_results=1)
        assert "WEB SEARCH" in result
        assert "Python" in result

    @patch("azure.agentic_tools._fetch_url")
    def test_web_search_no_results(self, mock_fetch):
        """web_search with no results."""
        mock_fetch.return_value = json.dumps({"query": {"search": []}}).encode()
        result = web_search("zzz_nonexistent_zzz")
        assert "No results" in result

    @patch("azure.agentic_tools._fetch_url")
    def test_web_search_error(self, mock_fetch):
        """web_search on network error."""
        mock_fetch.side_effect = Exception("Network error")
        result = web_search("test")
        assert "failed" in result.lower()

    @patch("azure.agentic_tools.urllib.request.urlopen")
    def test_web_fetch_json(self, mock_urlopen):
        """web_fetch returns JSON content."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"key": "value"}).encode()
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        result = web_fetch("https://example.com/data.json")
        assert "key" in result and "value" in result

    @patch("azure.agentic_tools.urllib.request.urlopen")
    def test_web_fetch_html(self, mock_urlopen):
        """web_fetch extracts text from HTML."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<html><body><p>Hello World</p></body></html>"
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        result = web_fetch("https://example.com")
        assert "Hello World" in result

    @patch("azure.agentic_tools.urllib.request.urlopen")
    def test_web_fetch_failed(self, mock_urlopen):
        """web_fetch on connection error."""
        mock_urlopen.side_effect = Exception("Connection refused")
        result = web_fetch("https://bad.example")
        assert "failed" in result.lower()

    @patch("azure.agentic_tools.urllib.request.urlopen")
    def test_web_fetch_max_chars(self, mock_urlopen):
        """web_fetch respects max_chars limit."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"a" * 5000
        mock_resp.headers = {"Content-Type": "text/plain"}
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        result = web_fetch("https://example.com", max_chars=100)
        assert len(result) <= 100

    def test_safe_path_normal(self, sandbox):
        """_safe_path resolves within sandbox."""
        p = _safe_path("test.txt")
        assert str(p).startswith(str(sandbox))
        assert p.name == "test.txt"

    def test_safe_path_subdir(self, sandbox):
        """_safe_path creates subdirectory paths within sandbox."""
        p = _safe_path("sub/file.txt")
        assert str(p).startswith(str(sandbox))

    def test_safe_path_traversal_blocked(self, sandbox):
        """_safe_path blocks path traversal."""
        with pytest.raises(PermissionError):
            _safe_path("../etc/passwd")

    def test_safe_path_absolute_blocked(self, sandbox):
        """_safe_path blocks absolute paths."""
        with pytest.raises(PermissionError):
            _safe_path("/etc/passwd")

    def test_file_write_and_read(self, sandbox):
        """file_write then file_read round-trip."""
        result = file_write("hello.txt", "Hello World")
        assert "Written" in result
        content = file_read("hello.txt")
        assert content == "Hello World"

    def test_file_write_overwrite(self, sandbox):
        """file_write overwrites existing content."""
        file_write("hello.txt", "first")
        result = file_write("hello.txt", "second")
        assert "Written" in result
        content = file_read("hello.txt")
        assert content == "second"

    def test_file_write_subdir(self, sandbox):
        """file_write creates intermediate directories."""
        result = file_write("sub/dir/file.txt", "nested")
        assert "Written" in result

    def test_file_read_not_found(self, sandbox):
        """file_read returns error for missing file."""
        result = file_read("nonexistent.txt")
        assert "not found" in result.lower()

    def test_file_list_empty(self, sandbox):
        """file_list on empty sandbox."""
        result = file_list("")
        assert result == "(empty)"

    def test_file_list_with_files(self, sandbox):
        """file_list shows created files."""
        file_write("a.txt", "aaa")
        file_write("b.txt", "bbb")
        result = file_list("")
        assert "a.txt" in result
        assert "b.txt" in result

    def test_file_traversal_blocked(self, sandbox):
        """file_write and file_read with traversal are blocked."""
        result = file_write("../escape.txt", "bad")
        assert "error" in result.lower() or "Error" in result
        result = file_read("../etc/passwd")
        assert "error" in result.lower() or "Error" in result

    def test_execute_python_disabled_by_default(self):
        """execute_python returns disabled message when not enabled."""
        old = os.environ.pop("AZURE_ALLOW_CODE_EXECUTION", None)
        try:
            result = execute_python("print('hello')")
            assert "disabled" in result.lower()
        finally:
            if old is not None:
                os.environ["AZURE_ALLOW_CODE_EXECUTION"] = old

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_python_valid_code(self):
        """execute_python with valid print code."""
        result = execute_python("print('hello')")
        assert "hello" in result

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_python_syntax_error(self):
        """execute_python reports syntax errors."""
        result = execute_python("print('hello")
        assert "error" in result.lower() or "Syntax" in result

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_python_runtime_error(self):
        """execute_python reports runtime errors."""
        result = execute_python("1/0")
        assert "error" in result.lower() or "ZeroDivision" in result

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_python_blocked_import(self):
        """execute_python blocks import keyword."""
        result = execute_python("import os")
        assert "blocked" in result.lower()

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_python_blocked_exec(self):
        """execute_python blocks exec keyword."""
        result = execute_python("exec('print(1)')")
        assert "blocked" in result.lower()

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_python_blocked_eval(self):
        """execute_python blocks eval keyword."""
        result = execute_python("eval('1+1')")
        assert "blocked" in result.lower()

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_python_blocked_open(self):
        """execute_python blocks open keyword."""
        result = execute_python("open('/etc/passwd')")
        assert "blocked" in result.lower()

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_python_blocked_dunder(self):
        """execute_python blocks __ dunder access."""
        result = execute_python("__import__('os')")
        assert "blocked" in result.lower()

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_python_code_fences_stripped(self):
        """execute_python strips markdown code fences."""
        result = execute_python("```python\nprint('fenced')\n```")
        assert "fenced" in result

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_python_no_output(self):
        """execute_python with no print."""
        result = execute_python("x = 1 + 1")
        assert "no output" in result.lower() or "success" in result.lower()


##############################################################################
#  SCENARIO 12: Full Workflow "Create a Gaming Community Server"
##############################################################################

class TestScenario12_FullWorkflow:
    """Scenario 12: End-to-end gaming server creation and cleanup."""

    @pytest.mark.asyncio
    async def test_full_gaming_server_workflow(self, guild, ctx):
        """Complete workflow: build a gaming community server from scratch."""
        bot = MagicMock(spec=["fetch_user", "wait_for", "user", "get_channel"])
        bot.fetch_user = AsyncMock()
        bot.wait_for = AsyncMock()
        bot.user = make_member("AzureBot")
        bot.get_channel = MagicMock()
        tools = ExpandedDMT(bot)

        role_admin = make_role("Admin", id=1001)
        role_cl = make_role("Clan Leader", id=1002, color=MagicMock())
        role_cl.color.value = 0x3498DB
        role_gamer = make_role("Gamer", id=1003, color=MagicMock())
        role_gamer.color.value = 0x2ECC71
        guild.roles = [make_role("@everyone", is_everyone=True, position=0)]

        cat_welcome = MagicMock()
        cat_welcome.name = "Welcome"
        cat_welcome.id = 2001
        cat_game = MagicMock()
        cat_game.name = "Game-Lobby"
        cat_game.id = 2002
        cat_voice = MagicMock()
        cat_voice.name = "Voice-Chat"
        cat_voice.id = 2003
        cat_admin = MagicMock()
        cat_admin.name = "Admin"
        cat_admin.id = 2004

        ch_welcome = make_channel("welcome", guild=guild, category=cat_welcome)
        ch_rules = make_channel("rules", guild=guild, category=cat_welcome)
        ch_lfg = make_channel("looking-for-group", guild=guild, category=cat_game)
        ch_general_vc = make_channel("General", ch_type=1, guild=guild, category=cat_voice)
        ch_admin = make_channel("admin-chat", guild=guild, category=cat_admin)

        created_objects = {
            "roles": {"Admin": role_admin, "Clan Leader": role_cl, "Gamer": role_gamer},
            "categories": {
                "Welcome": cat_welcome, "Game-Lobby": cat_game,
                "Voice-Chat": cat_voice, "Admin": cat_admin,
            },
            "channels": {
                "welcome": ch_welcome, "rules": ch_rules,
                "looking-for-group": ch_lfg, "General": ch_general_vc,
                "admin-chat": ch_admin,
            },
        }

        # Step 2: Create roles with colors
        for role_name, color, position in [
            ("Gamer", "green", 1),
            ("Clan Leader", "blue", 2),
            ("Admin", "red", 3),
        ]:
            role_mock = created_objects["roles"][role_name]
            guild.roles.append(role_mock)
            guild.create_role = AsyncMock(return_value=role_mock)
            result = await tools.create_role(guild, role_name, color=color, position=position)
            assert result.success is True, f"Failed creating role {role_name}: {result.error}"

        # Verify all roles exist
        role_names = [r.name for r in guild.roles]
        assert "Gamer" in role_names
        assert "Clan Leader" in role_names
        assert "Admin" in role_names

        # Step 3: Create categories
        for cat_name, cat_mock in [
            ("Welcome", cat_welcome), ("Game-Lobby", cat_game),
            ("Voice-Chat", cat_voice), ("Admin", cat_admin),
        ]:
            guild.create_category = AsyncMock(return_value=cat_mock)
            result = await tools.create_category(guild, cat_name)
            assert result.success is True, f"Failed creating category {cat_name}: {result.error}"

        guild.categories = [cat_welcome, cat_game, cat_voice, cat_admin]
        assert len(guild.categories) == 4

        # Step 4: Create channels under categories
        async def create_and_track(name, ch_type, cat_name, **kwargs):
            ch_mock = created_objects["channels"][name]
            ch_mock.guild = guild
            guild.create_text_channel = AsyncMock(return_value=ch_mock)
            guild.create_voice_channel = AsyncMock(return_value=ch_mock)
            result = await tools.create_channel(guild, name, channel_type=ch_type, category=cat_name, **kwargs)
            assert result.success is True, f"Failed creating channel {name}: {result.error}"
            guild.channels.append(ch_mock)
            return ch_mock

        await create_and_track("welcome", "text", "Welcome", topic="New member welcome")
        await create_and_track("rules", "text", "Welcome", topic="Server rules")
        await create_and_track("looking-for-group", "text", "Game-Lobby", topic="Find teammates")
        await create_and_track("General", "voice", "Voice-Chat")
        await create_and_track("admin-chat", "text", "Admin", topic="Admin discussion")

        assert len(guild.channels) >= 5

        # Step 5: Set permission overwrites for roles
        for ch_name, role_name, allow_list in [
            ("admin-chat", "Admin", ["send_messages", "read_messages", "manage_messages"]),
            ("admin-chat", "Gamer", ["read_messages"]),
        ]:
            ch = created_objects["channels"][ch_name]
            role = created_objects["roles"][role_name]
            guild.roles.append(role)
            result = await tools.set_channel_permissions(
                ch, role_name, allow=allow_list
            )
            assert result.success is True, f"Failed set_perms {ch_name}/{role_name}: {result.error}"

        # Step 6: Create AutoMod spam rules
        rule_mock = MagicMock()
        rule_mock.id = 5000
        rule_mock.name = "Spam Filter"
        guild.create_automod_rule = AsyncMock(return_value=rule_mock)
        result = await tools.enable_spam_filter(guild, mention_limit=3)
        assert result.success is True
        assert guild.create_automod_rule.call_count >= 2

        # Step 7: Set welcome screen
        guild.channels = list(created_objects["channels"].values())
        result = await tools.set_welcome_screen(guild, "Welcome to our gaming server!", ["welcome", "rules"])
        assert result.success is True

        # Step 8: Create server template
        template_mock = MagicMock()
        template_mock.code = "gaming123"
        template_mock.url = "https://discord.new/gaming123"
        guild.create_template = AsyncMock(return_value=template_mock)
        result = await tools.create_server_template(guild, "Gaming Template")
        assert result.success is True

        # Step 9: Verify the server state
        state = await tools.get_server_state(guild)
        assert state["server_name"] == "TestGuild"

        # Step 10: Cleanup — delete channels
        for ch_name in ["welcome", "rules", "looking-for-group", "General", "admin-chat"]:
            ch = created_objects["channels"][ch_name]
            ch.delete = AsyncMock()
            result = await tools.delete_channel(ch)
            assert result.success is True, f"Failed deleting channel {ch_name}: {result.error}"

        # Step 11: Cleanup — delete categories
        for cat_name in ["Welcome", "Game-Lobby", "Voice-Chat", "Admin"]:
            cat = created_objects["categories"][cat_name]
            cat.delete = AsyncMock()
            guild.categories = list(created_objects["categories"].values())
            result = await tools.delete_category(guild, cat_name)
            assert result.success is True, f"Failed deleting category {cat_name}: {result.error}"

        # Step 12: Cleanup — delete roles (not @everyone)
        for role_name in ["Gamer", "Clan Leader", "Admin"]:
            role = created_objects["roles"][role_name]
            role.delete = AsyncMock()
            guild.roles = [make_role("@everyone", is_everyone=True, position=0)] + [r for r in created_objects["roles"].values()]
            result = await tools.delete_role(guild, role_name)
            assert result.success is True, f"Failed deleting role {role_name}: {result.error}"

        # Step 13: Error recovery — delete already-deleted objects
        for ch_name in ["welcome", "rules"]:
            ch = created_objects["channels"][ch_name]
            ch.delete.side_effect = Exception("Unknown Channel")
            result = await tools.delete_channel(ch)
            assert result.success is False
            assert "Unknown Channel" in result.error

        # Verify @everyone is still protected
        result = await tools.delete_role(guild, "@everyone")
        assert result.success is False
        assert "everyone" in result.error.lower()

    @pytest.mark.asyncio
    async def test_health_analysis_of_created_server(self, guild):
        """Health analysis on a newly created server with basic setup."""
        g = make_guild(
            name="Gaming Server",
            members=[make_member(f"user{i}") for i in range(30)],
        )
        g.categories = [MagicMock()]
        g.categories[0].name = "General"
        g.rules_channel = MagicMock()
        g.rules_channel.name = "rules"
        g.system_channel = MagicMock()
        g.system_channel.name = "general"
        g.mfa_level = 1
        g.verification_level = _discord_mock.VerificationLevel.medium

        report = ServerHealthAnalyzer.analyze(g)
        assert report.overall_score > 40
        assert isinstance(report.recommendations, list)
        formatted = ServerHealthAnalyzer.format_report(report)
        assert report.server_name in formatted


##############################################################################
#  SCENARIO 12b: Parallel Plan Execution
##############################################################################

class TestScenario12b_ParallelExecution:
    """Test parallel plan execution for the gaming server workflow."""

    @pytest.mark.asyncio
    async def test_execute_plan_parallel(self, guild, mixin_owner, ctx):
        """Execute a multi-step plan in parallel phases."""
        role_admin = make_role("Admin", id=3001)
        role_member = make_role("Member", id=3002)
        cat_info = MagicMock()
        cat_info.name = "Info"
        cat_info.id = 4001
        ch_welcome = make_channel("welcome", guild=guild)

        guild.create_role = AsyncMock(side_effect=[role_admin, role_member])
        guild.create_category = AsyncMock(return_value=cat_info)
        guild.create_text_channel = AsyncMock(return_value=ch_welcome)

        plan = {
            "analysis": "Set up basic server",
            "steps": [
                {"action": "create_role", "name": "Admin", "color": "red"},
                {"action": "create_role", "name": "Member", "color": "green"},
                {"action": "create_category", "name": "Info"},
                {"action": "create_channel", "name": "welcome", "type": "text", "category": "Info"},
            ]
        }
        results = await mixin_owner.execute_plan_parallel(
            guild, plan, ctx,
            confirm_destructive=False,
            requester_name="Tester",
            requester_id=99999,
            require_authorization=False,
        )
        assert len(results) == 4
        assert all(r.success for r in results)
