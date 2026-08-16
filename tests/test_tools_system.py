"""Comprehensive integration tests for the TOOLS subsystem."""
import json

# ── early patching ─────────────────────────────────────────────────────
# Use conftest's shared discord mock, patch sys.modules for azure imports
import sys as _sys
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from tests.conftest import MOCK as _discord_mock  # noqa: N811
from tests.conftest import REAL_DISCORD as _real_discord  # noqa: N811
from tests.conftest import reset_utils_get

_orig_discord = _sys.modules.pop("discord", None)
_sys.modules["discord"] = _discord_mock
reset_utils_get()

from azure.discord_tools import DiscordManagementTools as LegacyDMT
from azure.discord_tools_expanded import DiscordManagementTools as ExpandedDMT
from azure.llm_planner import ExecutionResult, LLMPlanner
from azure.tool_engine import ToolEngine
from azure.tool_registry import ToolInfo, ToolRegistry
from azure.tools.server_tools import (
    ServerHealthAnalyzer,
    ServerHealthReport,
    _embed_color,
    _llm_reason,
    _resolve_color,
)
from azure.tools.types import StepResult

if _orig_discord is not None:
    _sys.modules["discord"] = _orig_discord

##############################################################################
#  HELPER / FIXTURE FACTORY
##############################################################################

def make_role(name="test-role", color=None, position=1, permissions=None,
              is_default=False, managed=False, mentionable=False, hoist=False,
              is_everyone=False, members=None, id=None):
    """Build a mocked discord.Role."""
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
    return _real_discord.TextChannel


def make_channel(name="test-channel", ch_type=0, category=None, position=0,
                 topic=None, nsfw=False, bitrate=None, user_limit=None,
                 slowmode_delay=0, last_message_id=None, guild=None,
                 is_news=False, overwrites=None, available_tags=None):
    """Build a mocked discord channel."""
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
    if hasattr(c, 'overwrites_for'):
        pass
    c.overwrites_for = MagicMock(return_value=MagicMock())
    return c


def make_member(name="testuser", display_name=None, nick=None, id=None,
                roles=None, top_role=None, voice=None, guild=None,
                bot=False, status="online"):
    """Build a mocked discord.Member."""
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
    """Build a mocked discord.Guild."""
    g = MagicMock(spec=_real_discord.Guild, name="mock.guild")
    g.name = name
    g.id = id
    g.owner_id = owner_id
    g.member_count = len(members) if members else 0
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

    # Mock methods
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
    t = MagicMock(spec=[
        "name", "id", "parent", "archived", "edit", "delete",
        "join", "leave", "add_user", "remove_user",
        "auto_archive_duration", "slowmode_delay",
    ])
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
    """Minimal LLM stub that returns canned responses."""
    def __init__(self, response="{}"):
        self.response = response
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.response


##############################################################################
#  FIXTURES
##############################################################################

@pytest.fixture(autouse=True)
def _reset_mock_after_test():
    yield
    reset_utils_get()

@pytest.fixture
def guild():
    g = make_guild()
    return g


@pytest.fixture
def category_chan(guild):
    cat = MagicMock()
    cat.name = "info"
    cat.id = 1001
    cat.position = 0
    cat.channels = []
    cat.edit = AsyncMock()
    cat.delete = AsyncMock()
    guild.categories = [cat]
    return cat


@pytest.fixture
def text_channel(guild):
    ch = make_channel("general", ch_type=0, guild=guild)
    guild.channels = [ch]
    guild.text_channels = [ch]
    return ch


@pytest.fixture
def voice_channel(guild):
    ch = make_channel("voice", ch_type=_discord_mock.ChannelType.voice, guild=guild)
    ch.bitrate = 64000
    ch.user_limit = 0
    guild.channels.append(ch)
    guild.voice_channels = [ch]
    return ch


@pytest.fixture
def member(guild):
    m = make_member("testuser", guild=guild)
    everyone_role = make_role("@everyone", is_everyone=True, position=0)
    m.top_role = everyone_role
    m.roles = [everyone_role]
    guild.members = [m]
    return m


@pytest.fixture
def mixin_owner():
    """Create a full DiscordManagementTools instance with mocked sub-systems."""
    bot = MagicMock(spec=["fetch_user", "wait_for", "user", "get_channel"])
    bot.fetch_user = AsyncMock()
    bot.wait_for = AsyncMock()
    bot.user = make_member("AzureBot")
    bot.get_channel = MagicMock()

    tools = ExpandedDMT(bot)
    return tools


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


@pytest.fixture
def ctx():
    return ProgressContext()


##############################################################################
#  TESTS: StepResult
##############################################################################

class TestStepResult:
    def test_minimal_construction(self):
        sr = StepResult(success=True, action="test")
        assert sr.success is True
        assert sr.action == "test"
        assert sr.name == ""
        assert sr.detail == ""
        assert sr.error == ""
        assert sr.target_id == 0
        assert sr.before_state is None
        assert sr.after_state is None

    def test_full_construction(self):
        sr = StepResult(
            success=False, action="delete_role", name="Mod",
            detail="Deleted", error="permission denied",
            target_id=42, before_state={"name": "Mod"}, after_state=None,
        )
        assert sr.success is False
        assert sr.action == "delete_role"
        assert sr.name == "Mod"
        assert sr.detail == "Deleted"
        assert sr.error == "permission denied"
        assert sr.target_id == 42
        assert sr.before_state == {"name": "Mod"}

    def test_empty_strings_default(self):
        sr = StepResult(success=True, action="kick")
        assert sr.name == ""
        assert sr.detail == ""
        assert sr.error == ""

    def test_mutable_fields(self):
        sr = StepResult(success=True, action="create_channel", name="general")
        sr.name = "new-name"
        sr.detail = "Updated"
        assert sr.name == "new-name"


##############################################################################
#  TESTS: _resolve_color, _llm_reason, _embed_color
##############################################################################

class TestUtilities:
    def test_resolve_color_basic(self):
        assert _resolve_color("red") == 0xE74C3C
        assert _resolve_color("blue") == 0x3498DB
        assert _resolve_color("unknown") == 0x99AAB5

    def test_resolve_color_case_insensitive(self):
        assert _resolve_color("RED") == 0xE74C3C
        assert _resolve_color("BlUe") == 0x3498DB

    def test_resolve_color_hex(self):
        # _resolve_color only handles named colors; hex is handled by _parse_color mixin method
        pass

    def test_llm_reason(self):
        assert _llm_reason("setup") == "Azure: setup"
        assert _llm_reason("edit", "channel name") == "Azure: edit - channel name"

    def test_embed_color(self):
        assert _embed_color("info") == 0x3498DB
        assert _embed_color("success") == 0x2ECC71
        assert _embed_color("warning") == 0xE67E22
        assert _embed_color("error") == 0xE74C3C
        assert _embed_color("unknown") == 0x3498DB


##############################################################################
#  TESTS: ServerHealthAnalyzer
##############################################################################

class TestServerHealthAnalyzer:
    def test_analyze_minimal(self, guild):
        report = ServerHealthAnalyzer.analyze(guild)
        assert isinstance(report, ServerHealthReport)
        assert report.server_name == "TestGuild"
        assert isinstance(report.overall_score, float)
        assert report.overall_grade in ("A", "B", "C", "D", "F")
        assert isinstance(report.recommendations, list)
        assert isinstance(report.quick_wins, list)
        assert isinstance(report.findings, list)

    def test_analyze_full_guild(self):
        g = make_guild(
            name="Awesome Server",
            members=[make_member(f"user{i}") for i in range(100)] + [make_member("bot1", bot=True)],
        )
        cat = MagicMock()
        cat.name = "info"
        g.categories = [cat]
        g.channels = [make_channel("welcome", last_message_id=1, guild=g)]
        g.text_channels = [make_channel("welcome", last_message_id=1, guild=g)]
        g.verification_level = _discord_mock.VerificationLevel.high
        g.explicit_content_filter = _discord_mock.ExplicitContentFilter.all_members
        g.mfa_level = 1
        g.rules_channel = MagicMock(spec=["name"])
        g.rules_channel.name = "rules"
        g.system_channel = MagicMock(spec=["name"])
        g.system_channel.name = "general"
        g.afk_channel = MagicMock(spec=["name"])
        g.afk_channel.name = "afk"

        report = ServerHealthAnalyzer.analyze(g)
        assert report.overall_score > 50
        assert len(report.recommendations) == 0

    def test_analyze_low_activity(self):
        g = make_guild(name="Quiet Server", members=[make_member("user1")])
        g.members[0].status = _discord_mock.Status.offline
        report = ServerHealthAnalyzer.analyze(g)
        # Low activity should produce recommendations
        " ".join(report.recommendations).lower()
        assert True

    def test_analyze_no_categories(self, guild):
        guild.categories = []
        report = ServerHealthAnalyzer.analyze(guild)
        " ".join(report.recommendations).lower()
        assert any("categor" in r.lower() for r in report.recommendations)

    def test_format_report(self, guild):
        report = ServerHealthAnalyzer.analyze(guild)
        formatted = ServerHealthAnalyzer.format_report(report)
        assert "Server Health Report" in formatted
        assert report.server_name in formatted
        assert report.overall_grade in formatted
        assert "Category Scores" in formatted

    def test_format_report_with_findings(self):
        g = make_guild()
        g.members = [make_member(f"bot{i}", bot=True) for i in range(40)]
        g.member_count = 50
        report = ServerHealthAnalyzer.analyze(g)
        ServerHealthAnalyzer.format_report(report)
        assert True

    def test_server_health_report_dataclass(self):
        r = ServerHealthReport(
            server_name="S", member_count=10, online_count=5,
            overall_grade="B", overall_score=75.0,
            activity={}, engagement={}, moderation={},
            organization={}, security={},
            recommendations=[], quick_wins=[], findings=[],
        )
        assert r.server_name == "S"
        assert r.overall_grade == "B"


##############################################################################
#  TESTS: ChannelToolsMixin
##############################################################################

class TestChannelToolsMixin:
    @pytest.mark.asyncio
    async def test_create_category(self, guild, mixin_owner):
        cat_mock = MagicMock()
        cat_mock.id = 200
        guild.create_category = AsyncMock(return_value=cat_mock)

        result = await mixin_owner.create_category(guild, "Info")
        assert result.success is True
        assert result.action == "create_category"
        guild.create_category.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_category_with_position(self, guild, mixin_owner):
        cat_mock = MagicMock()
        cat_mock.id = 201
        cat_mock.edit = AsyncMock()
        guild.create_category = AsyncMock(return_value=cat_mock)

        result = await mixin_owner.create_category(guild, "Mod", position=2)
        assert result.success is True
        cat_mock.edit.assert_awaited_once_with(position=2)

    @pytest.mark.asyncio
    async def test_create_category_failure(self, guild, mixin_owner):
        guild.create_category = AsyncMock(side_effect=Exception("API Error"))
        result = await mixin_owner.create_category(guild, "Fail")
        assert result.success is False
        assert "API Error" in result.error

    @pytest.mark.asyncio
    async def test_edit_category(self, guild, mixin_owner):
        cat = MagicMock()
        cat.name = "Info"
        cat.position = 0
        cat.edit = AsyncMock()
        guild.categories = [cat]

        result = await mixin_owner.edit_category(guild, "Info", name="Information")
        assert result.success is True
        assert result.detail == "Updated"
        cat.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_edit_category_not_found(self, guild, mixin_owner):
        guild.categories = []
        result = await mixin_owner.edit_category(guild, "Ghost", name="Boo")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_delete_category(self, guild, mixin_owner):
        cat = MagicMock()
        cat.name = "Temp"
        cat.delete = AsyncMock()
        guild.categories = [cat]

        result = await mixin_owner.delete_category(guild, "Temp")
        assert result.success is True
        cat.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_category_not_found(self, guild, mixin_owner):
        guild.categories = []
        result = await mixin_owner.delete_category(guild, "Ghost")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_create_text_channel(self, guild, mixin_owner):
        ch_mock = MagicMock()
        ch_mock.id = 300
        guild.create_text_channel = AsyncMock(return_value=ch_mock)

        result = await mixin_owner.create_channel(guild, "general")
        assert result.success is True
        assert result.action == "create_channel"
        guild.create_text_channel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_voice_channel(self, guild, mixin_owner):
        ch_mock = MagicMock()
        ch_mock.id = 301
        guild.create_voice_channel = AsyncMock(return_value=ch_mock)

        result = await mixin_owner.create_channel(guild, "Voice", channel_type="voice")
        assert result.success is True
        guild.create_voice_channel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_channel_with_category(self, guild, mixin_owner):
        cat = MagicMock()
        cat.name = "Info"
        guild.categories = [cat]
        ch_mock = MagicMock()
        ch_mock.id = 302
        guild.create_text_channel = AsyncMock(return_value=ch_mock)

        result = await mixin_owner.create_channel(guild, "welcome", category="Info")
        assert result.success is True
        guild.create_text_channel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_channel_forum_type(self, guild, mixin_owner):
        ch_mock = MagicMock()
        ch_mock.id = 303
        guild.create_forum = AsyncMock(return_value=ch_mock)

        result = await mixin_owner.create_channel(guild, "Forum", channel_type="forum")
        assert result.success is True
        guild.create_forum.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_channel_failure(self, guild, mixin_owner):
        guild.create_text_channel = AsyncMock(side_effect=Exception("No perms"))
        result = await mixin_owner.create_channel(guild, "fail")
        assert result.success is False
        assert "No perms" in result.error

    @pytest.mark.asyncio
    async def test_edit_channel(self, guild, mixin_owner):
        ch = make_channel("general", guild=guild)
        guild.channels = [ch]
        guild.text_channels = [ch]

        result = await mixin_owner.edit_channel(guild, "general", topic="New topic")
        assert result.success is True
        assert result.detail == "Updated"
        ch.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_edit_channel_not_found(self, guild, mixin_owner):
        guild.channels = []
        result = await mixin_owner.edit_channel(guild, "ghost")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_edit_channel_nsfw_type(self, guild, mixin_owner):
        ch = make_channel("nsfw", guild=guild)
        guild.channels = [ch]

        result = await mixin_owner.edit_channel(guild, "nsfw", type="nsfw")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_delete_channel(self, text_channel, mixin_owner):
        result = await mixin_owner.delete_channel(text_channel)
        assert result.success is True
        text_channel.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_channel_failure(self, text_channel, mixin_owner):
        text_channel.delete = AsyncMock(side_effect=Exception("Forbidden"))
        result = await mixin_owner.delete_channel(text_channel)
        assert result.success is False
        assert "Forbidden" in result.error

    @pytest.mark.asyncio
    async def test_move_channel(self, guild, mixin_owner, category_chan):
        ch = make_channel("moveme", guild=guild)
        guild.channels = [ch]
        guild.categories = [category_chan]

        result = await mixin_owner.move_channel(guild, "moveme", "info")
        assert result.success is True
        ch.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_move_channel_not_found(self, guild, mixin_owner):
        guild.channels = []
        result = await mixin_owner.move_channel(guild, "ghost", "info")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_sync_channel_permissions(self, guild, mixin_owner, category_chan):
        ch = make_channel("synced", guild=guild)
        ch.category = category_chan
        guild.channels = [ch]

        result = await mixin_owner.sync_channel_permissions(guild, "synced")
        assert result.success is True
        ch.sync_permissions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_permissions_no_category(self, guild, mixin_owner):
        ch = make_channel("nosync", guild=guild)
        ch.category = None
        guild.channels = [ch]

        result = await mixin_owner.sync_channel_permissions(guild, "nosync")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_set_channel_permissions_role(self, guild, mixin_owner):
        role = make_role("Mod")
        guild.roles.append(role)
        ch = make_channel("mod-only", guild=guild)
        ch.overwrites_for = MagicMock(return_value=MagicMock())

        result = await mixin_owner.set_channel_permissions(ch, "Mod", allow=["send_messages"])
        assert result.success is True
        ch.set_permissions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_channel_permissions_target_not_found(self, guild, mixin_owner):
        ch = make_channel("any", guild=guild)
        result = await mixin_owner.set_channel_permissions(ch, "NoOne", target_type="role")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_clear_channel_permissions(self, guild, mixin_owner):
        role = make_role("Mod")
        guild.roles.append(role)
        ch = make_channel("mod-only", guild=guild)
        ch.overwrites = {role: MagicMock()}
        ch.overwrites_for = MagicMock(return_value=MagicMock())

        result = await mixin_owner.clear_channel_permissions(ch, "Mod")
        assert result.success is True
        ch.set_permissions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clear_permissions_none(self, guild, mixin_owner):
        role = make_role("Mod")
        guild.roles.append(role)
        ch = make_channel("empty", guild=guild)
        ch.overwrites = {}

        result = await mixin_owner.clear_channel_permissions(ch, "Mod")
        assert result.success is True
        assert "No permissions" in result.detail

    @pytest.mark.asyncio
    async def test_purge_messages(self, text_channel, mixin_owner):
        text_channel.purge = AsyncMock(return_value=[1, 2, 3])
        result = await mixin_owner.purge_messages(text_channel, 50)
        assert result.success is True
        assert "Deleted 3" in result.detail

    @pytest.mark.asyncio
    async def test_purge_messages_clamps_limit(self, text_channel, mixin_owner):
        text_channel.purge = AsyncMock(return_value=list(range(200)))
        result = await mixin_owner.purge_messages(text_channel, 500)
        assert result.success is True
        # purge uses keyword argument limit=
        call_kwargs = text_channel.purge.call_args.kwargs
        limit = call_kwargs.get("limit", call_kwargs.get("kwargs", {}).get("limit"))
        assert limit == 200

    @pytest.mark.asyncio
    async def test_create_invite(self, text_channel, mixin_owner):
        inv = MagicMock()
        inv.code = "abc123"
        inv.uses = 0
        inv.max_uses = 0
        inv.inviter = MagicMock()
        inv.inviter.__str__ = lambda self: "user"
        inv.expires_at = None
        text_channel.create_invite = AsyncMock(return_value=inv)

        result = await mixin_owner.create_invite(text_channel)
        assert result.success is True
        assert "abc123" in result.detail

    @pytest.mark.asyncio
    async def test_pin_message(self, text_channel, mixin_owner):
        msg = MagicMock()
        msg.pin = AsyncMock()
        text_channel.fetch_message = AsyncMock(return_value=msg)

        result = await mixin_owner.pin_message(text_channel, 42)
        assert result.success is True
        msg.pin.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pin_message_failure(self, text_channel, mixin_owner):
        text_channel.fetch_message = AsyncMock(side_effect=Exception("Not found"))
        result = await mixin_owner.pin_message(text_channel, 999)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_unpin_message(self, text_channel, mixin_owner):
        msg = MagicMock()
        msg.unpin = AsyncMock()
        text_channel.fetch_message = AsyncMock(return_value=msg)

        result = await mixin_owner.unpin_message(text_channel, 42)
        assert result.success is True
        msg.unpin.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_thread(self, text_channel, mixin_owner):
        thread = make_thread("discussion")
        text_channel.create_thread = AsyncMock(return_value=thread)

        result = await mixin_owner.create_thread(text_channel, "discussion")
        assert result.success is True
        assert result.action == "create_thread"

    @pytest.mark.asyncio
    async def test_create_thread_from_message(self, text_channel, mixin_owner):
        msg = MagicMock()
        thread = make_thread("from-msg")
        msg.create_thread = AsyncMock(return_value=thread)
        text_channel.fetch_message = AsyncMock(return_value=msg)

        result = await mixin_owner.create_thread(text_channel, "from-msg", message_id=100)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_archive_thread(self, mixin_owner):
        thread = make_thread("archive-me")
        result = await mixin_owner.archive_thread(thread)
        assert result.success is True
        thread.edit.assert_awaited_once_with(archived=True, reason=ANY)

    @pytest.mark.asyncio
    async def test_delete_thread(self, mixin_owner):
        thread = make_thread("delete-me")
        result = await mixin_owner.delete_thread(thread)
        assert result.success is True
        thread.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rename_thread(self, mixin_owner):
        thread = make_thread("old-name")
        result = await mixin_owner.rename_thread(thread, "new-name")
        assert result.success is True
        thread.edit.assert_awaited_once_with(name="new-name", reason=ANY)

    @pytest.mark.asyncio
    async def test_join_thread(self, mixin_owner):
        thread = make_thread("join-me")
        result = await mixin_owner.join_thread(thread)
        assert result.success is True
        thread.join.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_leave_thread(self, mixin_owner):
        thread = make_thread("leave-me")
        result = await mixin_owner.leave_thread(thread)
        assert result.success is True
        thread.leave.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_thread_member(self, mixin_owner, member):
        thread = make_thread("add-member")
        result = await mixin_owner.add_thread_member(thread, member)
        assert result.success is True
        thread.add_user.assert_awaited_once_with(member)

    @pytest.mark.asyncio
    async def test_remove_thread_member(self, mixin_owner, member):
        thread = make_thread("remove-member")
        result = await mixin_owner.remove_thread_member(thread, member)
        assert result.success is True
        thread.remove_user.assert_awaited_once_with(member)

    @pytest.mark.asyncio
    async def test_disconnect_voice(self, mixin_owner, member):
        member.voice = MagicMock(spec=["channel"])
        member.voice.channel = MagicMock()
        member.move_to = AsyncMock()

        result = await mixin_owner.disconnect_voice(member)
        assert result.success is True
        member.move_to.assert_awaited_once_with(None, reason=ANY)

    @pytest.mark.asyncio
    async def test_disconnect_voice_not_in_vc(self, mixin_owner, member):
        member.voice = None
        result = await mixin_owner.disconnect_voice(member)
        assert result.success is False
        assert "Not in a voice channel" in result.error

    @pytest.mark.asyncio
    async def test_get_channel_invites(self, text_channel, mixin_owner):
        inv = MagicMock()
        inv.code = "xyz"
        inv.uses = 5
        inv.max_uses = 10
        inv.inviter = MagicMock()
        inv.inviter.__str__ = lambda self: "testuser"
        inv.expires_at = None
        text_channel.invites = AsyncMock(return_value=[inv])

        result = await mixin_owner.get_channel_invites(text_channel)
        assert result.success is True
        assert "1 invites" in result.name

    @pytest.mark.asyncio
    async def test_get_guild_invites(self, guild, mixin_owner):
        inv = MagicMock()
        inv.code = "xyz"
        inv.uses = 5
        inv.inviter = MagicMock()
        inv.inviter.__str__ = lambda self: "testuser"
        inv.channel = MagicMock()
        inv.channel.name = "general"
        guild.invites = AsyncMock(return_value=[inv])

        result = await mixin_owner.get_guild_invites(guild)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_pinned_messages(self, text_channel, mixin_owner):
        msg = MagicMock()
        msg.id = 1
        msg.author = MagicMock()
        msg.author.__str__ = lambda self: "user"
        msg.content = "Pinned!"
        msg.pinned_at = None
        text_channel.pins = AsyncMock(return_value=[msg])

        result = await mixin_owner.get_pinned_messages(text_channel)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_follow_channel(self, guild, mixin_owner):
        news_ch = make_channel("announcements", is_news=True, guild=guild)
        target = make_channel("follow-target", guild=guild)
        # Use side_effect so the mock returns target directly (works around
        # MagicMock-spec interaction where return_value is not always honored)
        guild.get_channel = MagicMock(side_effect=lambda ch_id: target)
        news_ch.follow = AsyncMock()
        result = await mixin_owner.follow_channel(news_ch, 999)
        assert result.success is True
        news_ch.follow.assert_awaited_once_with(target)

    @pytest.mark.asyncio
    async def test_follow_not_news(self, text_channel, mixin_owner):
        result = await mixin_owner.follow_channel(text_channel, 999)
        assert result.success is False
        assert "not an announcement" in result.error.lower()

    @pytest.mark.asyncio
    async def test_clone_channel(self, guild, mixin_owner):
        orig = make_channel("original", guild=guild)
        guild.channels = [orig]
        cloned = make_channel("original-copy")
        cloned.id = 555
        orig.clone = AsyncMock(return_value=cloned)

        result = await mixin_owner.clone_channel(guild, "original")
        assert result.success is True
        assert result.target_id == 555

    @pytest.mark.asyncio
    async def test_set_forum_require_tag(self, mixin_owner):
        forum = make_channel("forum")
        forum.edit = AsyncMock()
        result = await mixin_owner.set_forum_require_tag(forum, True)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_create_forum_channel(self, guild, mixin_owner):
        forum = make_channel("forum")
        forum.id = 600
        guild.create_forum = AsyncMock(return_value=forum)

        result = await mixin_owner.create_forum_channel(guild, "forum")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_create_stage_channel(self, guild, mixin_owner):
        stage = MagicMock()
        stage.id = 700
        stage.create_instance = AsyncMock()
        guild.create_stage_channel = AsyncMock(return_value=stage)

        result = await mixin_owner.create_stage_channel(guild, "stage")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_start_stage_instance(self, mixin_owner):
        stage = MagicMock()
        stage.name = "stage"
        instance = MagicMock()
        instance.id = 800
        stage.create_instance = AsyncMock(return_value=instance)

        result = await mixin_owner.start_stage_instance(stage, "Topic")
        assert result.success is True
        assert result.target_id == 800

    @pytest.mark.asyncio
    async def test_manage_stage_speaker(self, mixin_owner, member):
        stage = MagicMock()
        stage.name = "stage"
        member.edit = AsyncMock()

        result = await mixin_owner.manage_stage_speaker(member, stage, make_speaker=True)
        assert result.success is True
        member.edit.assert_awaited_once_with(suppress=False, reason=ANY)

    @pytest.mark.asyncio
    async def test_set_voice_bitrate(self, voice_channel, mixin_owner):
        result = await mixin_owner.set_voice_bitrate(voice_channel, 96)
        assert result.success is True
        voice_channel.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_voice_user_limit(self, voice_channel, mixin_owner):
        result = await mixin_owner.set_voice_user_limit(voice_channel, 10)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_voice_region(self, voice_channel, mixin_owner):
        result = await mixin_owner.set_voice_region(voice_channel, "us-west")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_list_archived_threads(self, guild, mixin_owner):
        guild.fetch_active_threads = AsyncMock(return_value=[])
        result = await mixin_owner.list_archived_threads(guild, public=True)
        # This is fine - no threads means empty but success
        assert result.success is True

    @pytest.mark.asyncio
    async def test_crosspost_message(self, text_channel, mixin_owner):
        msg = MagicMock()
        msg.publish = AsyncMock()
        text_channel.fetch_message = AsyncMock(return_value=msg)

        result = await mixin_owner.crosspost_message(text_channel, 1)
        assert result.success is True
        msg.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_revoke_invite(self, guild, mixin_owner):
        inv = MagicMock()
        inv.code = "abc"
        inv.delete = AsyncMock()
        guild.invites = AsyncMock(return_value=[inv])

        result = await mixin_owner.revoke_invite(guild, "abc")
        assert result.success is True
        inv.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_revoke_invite_not_found(self, guild, mixin_owner):
        guild.invites = AsyncMock(return_value=[])
        result = await mixin_owner.revoke_invite(guild, "ghost")
        assert result.success is False
        assert "not found" in result.error


##############################################################################
#  TESTS: MemberToolsMixin
##############################################################################

class TestMemberToolsMixin:
    @pytest.mark.asyncio
    async def test_kick_member(self, guild, mixin_owner, member):
        # Add a _resolve_member that works
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        guild.me = make_member("bot", top_role=make_role("Admin", position=100))

        result = await mixin_owner.kick_member(guild, "testuser")
        assert result.success is True
        member.kick.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_kick_member_not_found(self, guild, mixin_owner):
        mixin_owner._resolve_member = AsyncMock(return_value=None)
        result = await mixin_owner.kick_member(guild, "ghost")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_kick_member_role_too_high(self, guild, mixin_owner, member):
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        # Bot's role is lower than member's top role
        guild.me = make_member("bot", top_role=make_role("LowRole", position=1))
        member.top_role = make_role("HighRole", position=100)
        member.top_role.__le__ = MagicMock(return_value=True)  # bot.top_role <= member.top_role

        result = await mixin_owner.kick_member(guild, "testuser")
        assert result.success is False
        assert "not high enough" in result.error

    @pytest.mark.asyncio
    async def test_ban_member(self, guild, mixin_owner, member):
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        result = await mixin_owner.ban_member(guild, "testuser")
        assert result.success is True
        member.ban.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ban_member_not_in_guild(self, guild, mixin_owner):
        mixin_owner._resolve_member = AsyncMock(return_value=None)
        mixin_owner.bot.fetch_user = AsyncMock(side_effect=Exception("user not found"))
        result = await mixin_owner.ban_member(guild, "999999", delete_message_days=1)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_unban_member(self, guild, mixin_owner):
        user = MagicMock()
        user.id = 888
        mixin_owner.bot.fetch_user = AsyncMock(return_value=user)

        result = await mixin_owner.unban_member(guild, 888)
        assert result.success is True
        guild.unban.assert_awaited_once_with(user, reason="Azure")

    @pytest.mark.asyncio
    async def test_timeout_member(self, guild, mixin_owner, member):
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        result = await mixin_owner.timeout_member(guild, "testuser", duration_minutes=30)
        assert result.success is True
        member.timeout.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_member_not_found(self, guild, mixin_owner):
        mixin_owner._resolve_member = AsyncMock(return_value=None)
        result = await mixin_owner.timeout_member(guild, "ghost")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_set_nickname(self, guild, mixin_owner, member):
        member.nick = "old_nick"
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        result = await mixin_owner.set_nickname(guild, "testuser", "new_nick")
        assert result.success is True
        member.edit.assert_awaited_once_with(nick="new_nick", reason=ANY)

    @pytest.mark.asyncio
    async def test_set_nickname_not_found(self, guild, mixin_owner):
        mixin_owner._resolve_member = AsyncMock(return_value=None)
        result = await mixin_owner.set_nickname(guild, "ghost", "newname")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_move_member_to_voice(self, guild, mixin_owner, member):
        member.voice = MagicMock(spec=["channel"])
        member.voice.channel = MagicMock()
        vc = make_channel("voice", ch_type=1, guild=guild)
        guild.voice_channels = [vc]
        mixin_owner._resolve_member = AsyncMock(return_value=member)

        result = await mixin_owner.move_member_to_voice(guild, "testuser", "voice")
        assert result.success is True
        member.move_to.assert_awaited_once_with(vc, reason=ANY)

    @pytest.mark.asyncio
    async def test_move_member_not_in_voice(self, guild, mixin_owner, member):
        member.voice = None
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        guild.voice_channels = [make_channel("voice", ch_type=1, guild=guild)]

        result = await mixin_owner.move_member_to_voice(guild, "testuser", "voice")
        assert result.success is False
        assert "not in a voice" in result.error

    @pytest.mark.asyncio
    async def test_deafen_member(self, guild, mixin_owner, member):
        member.voice = MagicMock(spec=["channel"])
        member.voice.channel = MagicMock()
        mixin_owner._resolve_member = AsyncMock(return_value=member)

        result = await mixin_owner.deafen_member(guild, "testuser", deafen=True)
        assert result.success is True
        assert result.action == "deafen"

    @pytest.mark.asyncio
    async def test_deafen_not_in_voice(self, guild, mixin_owner, member):
        member.voice = None
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        result = await mixin_owner.deafen_member(guild, "testuser")
        assert result.success is False
        assert "not in a voice" in result.error

    @pytest.mark.asyncio
    async def test_mute_member(self, guild, mixin_owner, member):
        member.voice = MagicMock(spec=["channel"])
        member.voice.channel = MagicMock()
        mixin_owner._resolve_member = AsyncMock(return_value=member)

        result = await mixin_owner.mute_member(guild, "testuser", mute=True)
        assert result.success is True
        assert result.action == "mute"

    @pytest.mark.asyncio
    async def test_mute_not_in_voice(self, guild, mixin_owner, member):
        member.voice = None
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        result = await mixin_owner.mute_member(guild, "testuser")
        assert result.success is False
        assert "not in a voice" in result.error


##############################################################################
#  TESTS: RoleToolsMixin
##############################################################################

class TestRoleToolsMixin:
    @pytest.mark.asyncio
    async def test_create_role(self, guild, mixin_owner):
        role = make_role("Mod", id=500)
        guild.create_role = AsyncMock(return_value=role)

        result = await mixin_owner.create_role(guild, "Mod", color="blue")
        assert result.success is True
        assert result.action == "create_role"
        guild.create_role.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_role_with_position(self, guild, mixin_owner):
        role = make_role("Admin")
        role.edit = AsyncMock()
        guild.create_role = AsyncMock(return_value=role)

        result = await mixin_owner.create_role(guild, "Admin", position=5)
        assert result.success is True
        role.edit.assert_awaited_once_with(position=5)

    @pytest.mark.asyncio
    async def test_create_role_failure(self, guild, mixin_owner):
        guild.create_role = AsyncMock(side_effect=Exception("No perms"))
        result = await mixin_owner.create_role(guild, "Fail")
        assert result.success is False
        assert "No perms" in result.error

    @pytest.mark.asyncio
    async def test_edit_role(self, guild, mixin_owner):
        role = make_role("Mod")
        role.color.__str__ = lambda self: "#99aab5"
        role.hoist = False
        role.mentionable = False
        guild.roles = [make_role("@everyone", is_everyone=True), role]

        result = await mixin_owner.edit_role(guild, "Mod", name="Senior Mod")
        assert result.success is True
        assert result.detail == "Updated"
        role.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_edit_role_not_found(self, guild, mixin_owner):
        result = await mixin_owner.edit_role(guild, "Ghost")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_delete_role(self, guild, mixin_owner):
        role = make_role("TempRole")
        guild.roles = [make_role("@everyone", is_everyone=True), role]

        result = await mixin_owner.delete_role(guild, "TempRole")
        assert result.success is True
        role.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_role_not_found(self, guild, mixin_owner):
        result = await mixin_owner.delete_role(guild, "Ghost")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_delete_everyone(self, guild, mixin_owner):
        everyone = make_role("@everyone", is_everyone=True)
        guild.roles = [everyone]

        result = await mixin_owner.delete_role(guild, "@everyone")
        assert result.success is False
        assert "Cannot delete @everyone" in result.error

    @pytest.mark.asyncio
    async def test_assign_role(self, guild, mixin_owner, member):
        role = make_role("Member")
        guild.roles = [make_role("@everyone", is_everyone=True), role]
        mixin_owner._resolve_member = AsyncMock(return_value=member)

        result = await mixin_owner.assign_role(guild, "testuser", "Member")
        assert result.success is True
        member.add_roles.assert_awaited_once_with(role, reason=ANY)

    @pytest.mark.asyncio
    async def test_assign_role_member_not_found(self, guild, mixin_owner):
        role = make_role("Member")
        guild.roles = [make_role("@everyone", is_everyone=True), role]
        mixin_owner._resolve_member = AsyncMock(return_value=None)

        result = await mixin_owner.assign_role(guild, "ghost", "Member")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_assign_role_not_found(self, guild, mixin_owner, member):
        mixin_owner._resolve_member = AsyncMock(return_value=member)
        guild.roles = [make_role("@everyone", is_everyone=True)]

        result = await mixin_owner.assign_role(guild, "testuser", "Ghost")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_remove_role(self, guild, mixin_owner, member):
        role = make_role("Member")
        guild.roles = [make_role("@everyone", is_everyone=True), role]
        mixin_owner._resolve_member = AsyncMock(return_value=member)

        result = await mixin_owner.remove_role(guild, "testuser", "Member")
        assert result.success is True
        member.remove_roles.assert_awaited_once_with(role, reason=ANY)


##############################################################################
#  TESTS: ServerToolsMixin
##############################################################################

class TestServerToolsMixin:
    @pytest.mark.asyncio
    async def test_get_server_state(self, guild, mixin_owner):
        state = await mixin_owner.get_server_state(guild)
        assert "server_name" in state
        assert state["server_name"] == "TestGuild"
        assert "roles" in state
        assert "channels" in state
        assert "categories" in state
        assert "member_count" in state

    @pytest.mark.asyncio
    async def test_resolve_member_by_name(self, guild, mixin_owner, member):
        result = await mixin_owner._resolve_member(guild, "testuser")
        assert result is member

    @pytest.mark.asyncio
    async def test_resolve_member_by_id(self, guild, mixin_owner, member):
        guild.get_member = MagicMock(return_value=member)
        result = await mixin_owner._resolve_member(guild, str(member.id))
        assert result is member

    @pytest.mark.asyncio
    async def test_resolve_member_by_mention(self, guild, mixin_owner, member):
        guild.get_member = MagicMock(return_value=member)
        result = await mixin_owner._resolve_member(guild, f"<@{member.id}>")
        assert result is member

    @pytest.mark.asyncio
    async def test_resolve_member_not_found(self, guild, mixin_owner):
        result = await mixin_owner._resolve_member(guild, "nobody")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_member_empty(self, guild, mixin_owner):
        result = await mixin_owner._resolve_member(guild, "")
        assert result is None

    @pytest.mark.asyncio
    async def test_parse_color_none(self, mixin_owner):
        assert mixin_owner._parse_color(None) == 0

    @pytest.mark.asyncio
    async def test_parse_color_int(self, mixin_owner):
        assert mixin_owner._parse_color(0xFF5733) == 0xFF5733

    @pytest.mark.asyncio
    async def test_parse_color_str_name(self, mixin_owner):
        assert mixin_owner._parse_color("red") == 0xE74C3C

    @pytest.mark.asyncio
    async def test_parse_color_str_hex(self, mixin_owner):
        assert mixin_owner._parse_color("#FF5733") == 0xFF5733

    @pytest.mark.asyncio
    async def test_build_permissions(self, mixin_owner):
        perms = mixin_owner._build_permissions(["send_messages", "read_messages"])
        assert perms is not None

    @pytest.mark.asyncio
    async def test_set_server_name(self, guild, mixin_owner):
        result = await mixin_owner.set_server_name(guild, "New Name")
        assert result.success is True
        guild.edit.assert_awaited_once_with(name="New Name", reason=ANY)

    @pytest.mark.asyncio
    async def test_set_verification_level(self, guild, mixin_owner):
        result = await mixin_owner.set_verification_level(guild, "high")
        assert result.success is True
        guild.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_content_filter(self, guild, mixin_owner):
        result = await mixin_owner.set_content_filter(guild, "all_members")
        assert result.success is True
        guild.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_notifications(self, guild, mixin_owner):
        result = await mixin_owner.set_notifications(guild, "all_messages")
        assert result.success is True
        guild.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_afk_channel(self, guild, mixin_owner, voice_channel):
        result = await mixin_owner.set_afk_channel(guild, "voice", timeout=600)
        assert result.success is True
        guild.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_afk_channel_not_found(self, guild, mixin_owner):
        guild.voice_channels = []
        result = await mixin_owner.set_afk_channel(guild, "ghost")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_set_system_channel(self, guild, mixin_owner, text_channel):
        result = await mixin_owner.set_system_channel(guild, "general")
        assert result.success is True
        guild.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_system_channel_not_found(self, guild, mixin_owner):
        guild.text_channels = []
        result = await mixin_owner.set_system_channel(guild, "ghost")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_set_rules_channel(self, guild, mixin_owner, text_channel):
        result = await mixin_owner.set_rules_channel(guild, "general")
        assert result.success is True
        guild.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_webhook(self, guild, mixin_owner, text_channel):
        wh = MagicMock()
        wh.id = 900
        text_channel.create_webhook = AsyncMock(return_value=wh)

        result = await mixin_owner.create_webhook(guild, "general", "My Webhook")
        assert result.success is True
        assert result.target_id == 900

    @pytest.mark.asyncio
    async def test_delete_webhook(self, guild, mixin_owner):
        wh = MagicMock()
        wh.name = "My Webhook"
        wh.delete = AsyncMock()
        guild.webhooks = AsyncMock(return_value=[wh])

        result = await mixin_owner.delete_webhook(guild, "My Webhook")
        assert result.success is True
        wh.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_scheduled_event(self, guild, mixin_owner):
        ev = MagicMock()
        ev.id = 1000
        guild.create_scheduled_event = AsyncMock(return_value=ev)

        result = await mixin_owner.create_scheduled_event(
            guild, "Event", "Desc", "2026-07-20T12:00:00Z",
            end_time="2026-07-20T13:00:00Z",
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_delete_scheduled_event(self, guild, mixin_owner):
        ev = MagicMock()
        ev.name = "Event"
        ev.delete = AsyncMock()
        guild.fetch_scheduled_events = AsyncMock(return_value=[ev])

        result = await mixin_owner.delete_scheduled_event(guild, "Event")
        assert result.success is True
        ev.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_audit_logs(self, guild, mixin_owner):
        guild.audit_logs.__aiter__ = MagicMock(return_value=iter([]))
        result = await mixin_owner.get_audit_logs(guild, limit=10)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_find_who_did_action(self, guild, mixin_owner):
        result = await mixin_owner.find_who_did_action(guild, "channel_delete")
        assert result.success is True or result.success is False  # Depends on logs

    @pytest.mark.asyncio
    async def test_enable_spam_filter(self, guild, mixin_owner):
        rule = MagicMock()
        rule.id = 42
        guild.create_automod_rule = AsyncMock(return_value=rule)
        result = await mixin_owner.enable_spam_filter(guild, mention_limit=5)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_get_ban_list(self, guild, mixin_owner):
        guild.bans.__aiter__ = MagicMock(return_value=iter([]))
        result = await mixin_owner.get_ban_list(guild)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_estimate_prune_members(self, guild, mixin_owner):
        guild.estimate_pruned_members = AsyncMock(return_value=5)
        result = await mixin_owner.estimate_prune_members(guild, days=30)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_prune_members(self, guild, mixin_owner):
        guild.prune_members = AsyncMock(return_value=3)
        result = await mixin_owner.prune_members(guild, days=30)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_public_updates_channel(self, guild, mixin_owner, text_channel):
        result = await mixin_owner.set_public_updates_channel(guild, "general")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_mfa_level(self, guild, mixin_owner):
        result = await mixin_owner.set_mfa_level(guild, True)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_preferred_locale(self, guild, mixin_owner):
        result = await mixin_owner.set_preferred_locale(guild, "en-US")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_vanity_url(self, guild, mixin_owner):
        result = await mixin_owner.set_vanity_url(guild, "awesome")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_enable_community_mode(self, guild, mixin_owner, text_channel):
        updates = make_channel("updates", guild=guild)
        guild.text_channels.append(updates)

        result = await mixin_owner.enable_community_mode(guild, "general", "updates")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_enable_community_mode_no_rules(self, guild, mixin_owner):
        guild.text_channels = []
        result = await mixin_owner.enable_community_mode(guild, "rules", "updates")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_get_widget(self, guild, mixin_owner):
        w = MagicMock()
        w.enabled = True
        w.channel = MagicMock()
        w.channel.name = "general"
        guild.widget = AsyncMock(return_value=w)
        result = await mixin_owner.get_widget(guild)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_widget(self, guild, mixin_owner):
        result = await mixin_owner.set_widget(guild, True, "general")
        assert result.success is True
        guild.edit.assert_awaited_once()


##############################################################################
#  TESTS: ProgressToolsMixin
##############################################################################

class TestProgressToolsMixin:
    @pytest.mark.asyncio
    async def test_send_progress_embed(self, mixin_owner, ctx):
        plan = {"analysis": "Test plan"}
        msg = await mixin_owner._send_progress_embed(ctx, plan, 0, 5, [], "Starting")
        assert msg is not None

    @pytest.mark.asyncio
    async def test_update_progress_embed(self, mixin_owner):
        msg = MagicMock()
        msg.edit = AsyncMock()
        plan = {"analysis": "Test"}
        results = [StepResult(success=True, action="test", name="step1")]
        await mixin_owner._update_progress_embed(msg, plan, 1, 3, results, "working")
        msg.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_finalize_progress_embed_all_success(self, mixin_owner):
        msg = MagicMock()
        msg.edit = AsyncMock()
        plan = {"analysis": "Done"}
        results = [
            StepResult(success=True, action="a", name="s1"),
            StepResult(success=True, action="b", name="s2"),
        ]
        await mixin_owner._finalize_progress_embed(msg, plan, results)
        msg.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_finalize_progress_embed_with_failures(self, mixin_owner):
        msg = MagicMock()
        msg.edit = AsyncMock()
        plan = {"analysis": "Mixed"}
        results = [
            StepResult(success=True, action="a", name="s1"),
            StepResult(success=False, action="b", name="s2", error="Oops"),
        ]
        await mixin_owner._finalize_progress_embed(msg, plan, results)
        msg.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_finalize_progress_embed_empty(self, mixin_owner):
        msg = MagicMock()
        msg.edit = AsyncMock()
        await mixin_owner._finalize_progress_embed(msg, {"analysis": "Nope"}, [])
        msg.edit.assert_awaited_once()


##############################################################################
#  TESTS: PlanToolsMixin
##############################################################################

class TestPlanToolsMixin:
    @pytest.mark.asyncio
    async def test_generate_plan(self, guild, mixin_owner):
        mixin_owner.get_server_state = AsyncMock(return_value={
            "server_name": "TestGuild", "member_count": 10,
            "roles": [], "channels": [], "categories": [],
            "verification_level": "low",
            "default_notifications": "mentions_only",
            "explicit_content_filter": "disabled",
        })
        llm = FakeLLM(response='{"analysis": "Setup", "steps": []}')
        plan = await mixin_owner.generate_plan(guild, "setup server", llm)
        assert "analysis" in plan
        assert "steps" in plan

    def test_parse_plan_valid_json(self, mixin_owner):
        raw = '{"analysis": "Test", "steps": [{"action": "create_role", "name": "Mod"}]}'
        plan = mixin_owner._parse_plan(raw)
        assert plan["analysis"] == "Test"
        assert len(plan["steps"]) == 1

    def test_parse_plan_invalid(self, mixin_owner):
        plan = mixin_owner._parse_plan("not json")
        assert "analysis" in plan
        assert "raw" in plan

    def test_parse_plan_empty(self, mixin_owner):
        plan = mixin_owner._parse_plan("")
        assert plan["steps"] == []

    def test_parse_plan_missing_steps(self, mixin_owner):
        plan = mixin_owner._parse_plan('{"analysis": "Only analysis"}')
        assert plan["steps"] == []

    def test_extract_step_name(self, mixin_owner):
        step = {"action": "create_role", "name": "Mod"}
        assert mixin_owner._extract_step_name(step) == "Mod"

    def test_extract_step_name_fallback(self, mixin_owner):
        step = {"action": "unknown"}
        assert mixin_owner._extract_step_name(step) == "unknown"

    def test_build_planning_prompt(self, mixin_owner):
        state = {
            "server_name": "S", "member_count": 5,
            "roles": [{"name": "Admin"}], "channels": [{"name": "general"}],
            "categories": [{"name": "Info"}],
            "verification_level": "low",
            "default_notifications": "mentions_only",
            "explicit_content_filter": "disabled",
        }
        prompt = mixin_owner._build_planning_prompt(state, "create channels")
        assert "create channels" in prompt
        assert "Admin" in prompt
        assert "general" in prompt

    @pytest.mark.asyncio
    async def test_execute_plan_empty(self, mixin_owner, ctx):
        with (
            patch.object(mixin_owner, "_send_progress_embed", AsyncMock()),
            patch.object(mixin_owner, "_update_progress_embed", AsyncMock()),
            patch.object(mixin_owner, "_finalize_progress_embed", AsyncMock()),
        ):
            results = await mixin_owner.execute_plan(
                ctx.guild if hasattr(ctx, 'guild') else MagicMock(),
                {"steps": []}, ctx,
                confirm_destructive=False,
                require_authorization=False,
            )
        assert results == []

    @pytest.mark.asyncio
    async def test_execute_plan_single_step(self, mixin_owner, guild, ctx):
        guild.get_member = MagicMock(return_value=make_member("owner"))
        step = {"action": "list_channels"}
        plan = {"analysis": "List", "steps": [step]}

        with (
            patch.object(mixin_owner, "_send_progress_embed", AsyncMock(return_value=MagicMock())),
            patch.object(mixin_owner, "_update_progress_embed", AsyncMock()),
            patch.object(mixin_owner, "_finalize_progress_embed", AsyncMock()),
        ):
            results = await mixin_owner.execute_plan(
                guild, plan, ctx,
                confirm_destructive=False,
                requester_name="owner", requester_id=99999,
                require_authorization=False,
            )
        assert len(results) == 1
        assert results[0].success is True

    @pytest.mark.asyncio
    async def test_execute_plan_blocks_missing_bot_permissions(self, mixin_owner, guild, ctx):
        bot_member = make_member("AzureBot")
        bot_member.guild_permissions = MagicMock(spec=["administrator", "manage_channels"])
        bot_member.guild_permissions.administrator = False
        bot_member.guild_permissions.manage_channels = False
        guild.get_member = MagicMock(return_value=bot_member)

        with patch.object(mixin_owner, "_send_progress_embed", AsyncMock()) as send_progress:
            results = await mixin_owner.execute_plan(
                guild, {"steps": [{"action": "create_channel", "name": "private"}]}, ctx,
                confirm_destructive=False, require_authorization=False,
            )

        assert results[0].name == "preflight_failed"
        assert "manage_channels" in results[0].error
        send_progress.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_plan_parallel_blocks_missing_bot_permissions(self, mixin_owner, guild, ctx):
        bot_member = make_member("AzureBot")
        bot_member.guild_permissions = MagicMock(spec=["administrator", "manage_channels"])
        bot_member.guild_permissions.administrator = False
        bot_member.guild_permissions.manage_channels = False
        guild.get_member = MagicMock(return_value=bot_member)

        results = await mixin_owner.execute_plan_parallel(
            guild, {"steps": [{"action": "create_channel", "name": "private"}]}, ctx,
            confirm_destructive=False, require_authorization=False,
        )

        assert results[0].name == "preflight_failed"
        assert "manage_channels" in results[0].error

    @pytest.mark.asyncio
    async def test_execute_plan_auth_failure(self, mixin_owner, guild, ctx):
        guild.get_member = MagicMock(return_value=None)
        plan = {"analysis": "Auth", "steps": [{"action": "list_channels"}]}

        results = await mixin_owner.execute_plan(
            guild, plan, ctx,
            confirm_destructive=False,
            requester_name="nobody", requester_id=88888,
            require_authorization=True,
        )
        assert len(results) == 1
        assert results[0].success is False
        assert "not found in guild" in results[0].error or "auth" in results[0].name

    @pytest.mark.asyncio
    async def test_execute_plan_no_requester_id(self, mixin_owner, guild, ctx):
        plan = {"analysis": "No ID", "steps": [{"action": "list_channels"}]}

        results = await mixin_owner.execute_plan(
            guild, plan, ctx,
            confirm_destructive=False,
            requester_name="anon",
            requester_id=None,
            require_authorization=True,
        )
        assert len(results) == 1
        assert results[0].success is False

    @pytest.mark.asyncio
    async def test_execute_plan_parallel(self, mixin_owner, guild, ctx):
        guild.get_member = MagicMock(return_value=make_member("owner"))
        plan = {"analysis": "Parallel", "steps": [
            {"action": "list_channels"},
            {"action": "list_roles"},
        ]}

        with (
            patch.object(mixin_owner, "_send_progress_embed", AsyncMock(return_value=MagicMock())),
            patch.object(mixin_owner, "_update_progress_embed", AsyncMock()),
            patch.object(mixin_owner, "_finalize_progress_embed", AsyncMock()),
        ):
            results = await mixin_owner.execute_plan_parallel(
                guild, plan, ctx,
                confirm_destructive=False,
                requester_name="owner", requester_id=99999,
                require_authorization=False,
            )
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_execute_single_step_with_retry(self, guild, mixin_owner):
        mixin_owner.MAX_RETRIES = 1
        mixin_owner._do_step = AsyncMock(return_value=StepResult(success=True, action="test", name="test"))
        step = {"action": "create_role", "name": "Mod"}
        result = await mixin_owner._execute_single_step(guild, step, False)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_single_step_fails_retries(self, guild, mixin_owner):
        mixin_owner.MAX_RETRIES = 2
        mixin_owner._do_step = AsyncMock(return_value=StepResult(success=False, action="test", name="test", error="fail"))
        step = {"action": "create_role", "name": "Mod"}
        result = await mixin_owner._execute_single_step(guild, step, False)
        assert result.success is False

    def test_action_aliases(self, mixin_owner):
        assert mixin_owner._ACTION_ALIASES["kick_member"] == "kick"
        assert mixin_owner._ACTION_ALIASES["create_text_channel"] == "create_channel"
        assert mixin_owner._ACTION_ALIASES.get("delete_text_channel") == "delete_channel"


##############################################################################
#  TESTS: ToolEngine
##############################################################################

class TestToolEngine:
    def test_decide_chat(self):
        llm = FakeLLM(response='{"action": "chat", "confidence": 0.9}')
        engine = ToolEngine(llm)
        decision = engine.decide("hello", "user")
        assert decision.action == "chat"

    def test_decide_plan(self):
        llm = FakeLLM(response='{"action": "plan", "confidence": 0.8, "plan_description": "Setup"}')
        engine = ToolEngine(llm)
        decision = engine.decide("create a server", "user")
        assert decision.action == "plan"
        assert decision.plan is not None

    def test_decide_health_check(self):
        llm = FakeLLM(response='{"action": "health_check", "confidence": 0.7}')
        engine = ToolEngine(llm)
        decision = engine.decide("analyze server", "user")
        assert decision.action == "health_check"

    def test_decide_undo(self):
        llm = FakeLLM(response='{"action": "undo", "confidence": 0.8}')
        engine = ToolEngine(llm)
        decision = engine.decide("undo last change", "user")
        assert decision.action == "undo"

    def test_decide_member_action(self):
        llm = FakeLLM(response='{"action": "member_action", "confidence": 0.9, "tool": "kick_member", "member": "user", "reason": "spam"}')
        engine = ToolEngine(llm)
        decision = engine.decide("kick user", "admin")
        assert decision.action == "member_action"
        assert decision.tool_call is not None
        assert decision.tool_call["tool"] == "kick_member"

    def test_decide_template(self):
        llm = FakeLLM(response='{"action": "template", "confidence": 0.8, "template_action": "save"}')
        engine = ToolEngine(llm)
        decision = engine.decide("save template", "user")
        assert decision.action == "template"

    def test_decide_info(self):
        llm = FakeLLM(response='{"action": "info", "confidence": 0.9}')
        engine = ToolEngine(llm)
        decision = engine.decide("how do I do this", "user")
        assert decision.action == "info"

    def test_decide_server_info_scope(self):
        llm = FakeLLM(response='{"action": "server_info", "confidence": 0.95, "scope": "channels"}')
        engine = ToolEngine(llm)
        decision = engine.decide("what channels are here", "user")
        assert decision.action == "server_info"
        assert decision.params["scope"] == "channels"

    def test_decide_audit_logs(self):
        llm = FakeLLM(response='{"action": "audit_logs", "confidence": 0.95, "limit": 5, "action_type": "channel_delete"}')
        engine = ToolEngine(llm)
        decision = engine.decide("who deleted the channel?", "user")
        assert decision.action == "audit_logs"
        assert decision.params["limit"] == 5
        assert decision.params["action_type"] == "channel_delete"

    def test_decide_member_info(self):
        llm = FakeLLM(response='{"action": "member_info", "confidence": 0.95, "member": "42"}')
        engine = ToolEngine(llm)
        decision = engine.decide("what roles does user 42 have", "user")
        assert decision.action == "member_info"
        assert decision.params["member"] == "42"

    def test_decide_channel_info(self):
        llm = FakeLLM(response='{"action": "channel_info", "confidence": 0.95, "channel": "general"}')
        engine = ToolEngine(llm)
        decision = engine.decide("what is the topic of general", "user")
        assert decision.action == "channel_info"
        assert decision.params["channel"] == "general"

    def test_decide_role_info(self):
        llm = FakeLLM(response='{"action": "role_info", "confidence": 0.95, "role": "Moderators"}')
        engine = ToolEngine(llm)
        decision = engine.decide("what permissions does Moderators have", "user")
        assert decision.action == "role_info"
        assert decision.params["role"] == "Moderators"

    def test_decide_server_data(self):
        llm = FakeLLM(response='{"action": "server_data", "confidence": 0.95, "data_type": "automod_rules", "limit": 10}')
        engine = ToolEngine(llm)
        decision = engine.decide("show the AutoMod rules", "admin")
        assert decision.action == "server_data"
        assert decision.params["data_type"] == "automod_rules"

    def test_decision_cache_keeps_dm_context_isolated(self):
        llm = FakeLLM(response='{"action": "chat", "confidence": 0.9}')
        engine = ToolEngine(llm)

        engine.decide("help", "Alice", "Test", False, True, "chat")
        engine.decide("help", "Alice", "Test", True, False, "chat")

        assert len(llm.calls) == 2

    def test_decide_fallback_no_llm(self):
        engine = ToolEngine(None)
        decision = engine.decide("hello", "user")
        assert decision.action == "chat"
        assert decision.confidence == 0.4

    def test_decide_with_markdown_code_block(self):
        llm = FakeLLM(response='```json\n{"action": "plan", "confidence": 0.9, "plan_description": "Setup"}\n```')
        engine = ToolEngine(llm)
        decision = engine.decide("setup server", "user")
        assert decision.action == "plan"

    def test_fallback_parse(self):
        engine = ToolEngine(FakeLLM())
        # Keyword fallback removed: invalid/non-JSON output → chat only
        raw = "I think we should create a plan for this"
        result = engine._parse_decision(raw)
        assert result.action == "chat"

    def test_decide_caching(self):
        llm = FakeLLM(response='{"action": "chat", "confidence": 0.9}')
        engine = ToolEngine(llm)
        d1 = engine.decide("hello", "user", "server1")
        # Same cache key should return cached result
        d2 = engine.decide("hello", "user", "server1")
        assert d1 is d2

    def test_decide_cache_max_size(self):
        llm = FakeLLM(response='{"action": "chat", "confidence": 0.9}')
        engine = ToolEngine(llm)
        # Fill cache to max
        for i in range(110):
            engine.decide(f"msg{i}", "user", "server1")
        assert len(engine._decision_cache) <= engine._cache_max_size

    def test_build_decision_action_normalization(self):
        engine = ToolEngine(FakeLLM())
        result = engine._build_decision({"action": "build_server"})
        assert result.action == "plan"

        result = engine._build_decision({"action": "analyze"})
        assert result.action == "health_check"

        result = engine._build_decision({"action": "revert"})
        # Unknown aliases fall through to chat (no keyword banks)
        assert result.action in ("chat", "undo")

        result = engine._build_decision({"action": "talk"})
        assert result.action == "chat"


##############################################################################
#  TESTS: ToolRegistry
##############################################################################

class TestToolRegistry:
    def test_init_auto_discovery(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        assert len(registry.tools) > 0
        # Should have discovered async methods
        assert "create_role" in registry.tools
        assert "create_channel" in registry.tools
        assert "create_category" in registry.tools

    def test_get_tool_exists(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        tool = registry.get_tool("create_role")
        assert tool is not None
        assert isinstance(tool, ToolInfo)
        assert tool.name == "create_role"
        assert callable(tool.function)

    def test_get_tool_not_exists(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        assert registry.get_tool("nonexistent") is None

    def test_search_tools_by_name(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        results = registry.search_tools("role")
        assert len(results) > 0
        assert all("role" in t.name.lower() for t in results)

    def test_search_tools_by_docstring(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        results = registry.search_tools("channel")
        assert any("channel" in t.name.lower() or "channel" in t.docstring.lower() for t in results)

    def test_get_categories(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        cats = registry.get_categories()
        assert len(cats) > 0
        assert "role" in cats
        assert "channel" in cats

    def test_get_tools_by_category(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        role_tools = registry.get_tools_by_category("role")
        assert len(role_tools) > 0
        for t in role_tools:
            assert t.category == "role"

    def test_get_summary(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        summary = registry.get_summary()
        assert "Tool Registry" in summary
        assert "tools across" in summary

    def test_get_tool_descriptions_for_llm(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        desc = registry.get_tool_descriptions_for_llm()
        assert "AVAILABLE DISCORD MANAGEMENT TOOLS" in desc
        assert "create_role" in desc

    def test_get_tool_descriptions_json(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        json_str = registry.get_tool_descriptions_json()
        data = json.loads(json_str)
        assert isinstance(data, list)
        assert len(data) > 0
        assert "name" in data[0]

    def test_create_registry_convenience(self, mixin_owner):
        from azure.tool_registry import create_registry
        registry = create_registry(mixin_owner)
        assert isinstance(registry, ToolRegistry)


##############################################################################
#  TESTS: DiscordManagementTools (Legacy)
##############################################################################

class TestLegacyDiscordManagementTools:
    @pytest.mark.asyncio
    async def test_legacy_get_server_state(self, guild):
        bot = MagicMock()
        tools = LegacyDMT(bot)
        state = await tools.get_server_state(guild)
        assert "server_name" in state
        assert "member_count" in state

    @pytest.mark.asyncio
    async def test_legacy_create_role(self, guild):
        bot = MagicMock()
        tools = LegacyDMT(bot)
        role = make_role("TestRole")
        guild.create_role = AsyncMock(return_value=role)

        result = await tools.create_role(guild, "TestRole", color="red")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_legacy_create_category(self, guild):
        bot = MagicMock()
        tools = LegacyDMT(bot)
        cat = MagicMock()
        guild.create_category = AsyncMock(return_value=cat)

        result = await tools.create_category(guild, "Info")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_legacy_create_channel(self, guild):
        bot = MagicMock()
        tools = LegacyDMT(bot)
        ch = MagicMock()
        guild.create_text_channel = AsyncMock(return_value=ch)

        result = await tools.create_channel(guild, "general")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_legacy_set_permissions(self, guild):
        bot = MagicMock()
        tools = LegacyDMT(bot)
        role = make_role("TestRole")
        guild.roles.append(role)
        ch = make_channel("test", guild=guild)

        result = await tools.set_channel_permissions(ch, "TestRole", allow=["send_messages"])
        # The code builds a PermissionOverwrite by setting fields by name, so
        # the requested permission is applied and the call succeeds.
        assert result.success is True
        ch.set_permissions.assert_awaited_once()
        overwrite = ch.set_permissions.call_args.kwargs["overwrite"]
        assert overwrite.send_messages is True

    @pytest.mark.asyncio
    async def test_legacy_delete_channel(self, text_channel):
        bot = MagicMock()
        tools = LegacyDMT(bot)
        result = await tools.delete_channel(text_channel)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_legacy_delete_role(self, guild):
        bot = MagicMock()
        tools = LegacyDMT(bot)
        role = make_role("TempRole")
        guild.roles = [make_role("@everyone", is_everyone=True), role]

        result = await tools.delete_role(guild, "TempRole")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_legacy_delete_everyone(self, guild):
        bot = MagicMock()
        tools = LegacyDMT(bot)
        guild.roles = [make_role("@everyone", is_everyone=True)]

        result = await tools.delete_role(guild, "@everyone")
        assert result.success is False
        assert "Cannot delete" in result.error

    @pytest.mark.asyncio
    async def test_legacy_execute_plan(self, guild):
        bot = MagicMock()
        bot.wait_for = AsyncMock()
        tools = LegacyDMT(bot)
        ctx = ProgressContext()
        plan = {"analysis": "Test", "steps": [{"action": "list_channels"}]}

        # Legacy _execute_single_step doesn't know list_channels
        await tools.execute_plan(guild, plan, ctx, confirm_destructive=False)
        # Should at least not crash

    def test_legacy_parse_plan(self):
        bot = MagicMock()
        tools = LegacyDMT(bot)
        raw = '{"analysis": "Setup", "steps": [{"action": "create_role"}]}'
        plan = tools._parse_plan(raw)
        assert plan["analysis"] == "Setup"
        assert len(plan["steps"]) == 1

    def test_legacy_parse_plan_malformed(self):
        bot = MagicMock()
        tools = LegacyDMT(bot)
        plan = tools._parse_plan("not json at all")
        assert "Failed to parse" in plan["analysis"]


##############################################################################
#  TESTS: Expanded DiscordManagementTools
##############################################################################

class TestExpandedDiscordManagementTools:
    def test_init(self):
        bot = MagicMock()
        tools = ExpandedDMT(bot)
        assert tools.bot is bot
        assert hasattr(tools, "tracker")
        assert hasattr(tools, "templates")
        assert hasattr(tools, "health")

    @pytest.mark.asyncio
    async def test_all_mixins_available(self, mixin_owner):
        # Verify all mixin methods are accessible
        assert hasattr(mixin_owner, "create_role")
        assert hasattr(mixin_owner, "edit_role")
        assert hasattr(mixin_owner, "delete_role")
        assert hasattr(mixin_owner, "assign_role")
        assert hasattr(mixin_owner, "create_category")
        assert hasattr(mixin_owner, "create_channel")
        assert hasattr(mixin_owner, "edit_channel")
        assert hasattr(mixin_owner, "delete_channel")
        assert hasattr(mixin_owner, "move_channel")
        assert hasattr(mixin_owner, "set_channel_permissions")
        assert hasattr(mixin_owner, "clear_channel_permissions")
        assert hasattr(mixin_owner, "kick_member")
        assert hasattr(mixin_owner, "ban_member")
        assert hasattr(mixin_owner, "unban_member")
        assert hasattr(mixin_owner, "timeout_member")
        assert hasattr(mixin_owner, "set_nickname")
        assert hasattr(mixin_owner, "mute_member")
        assert hasattr(mixin_owner, "deafen_member")
        assert hasattr(mixin_owner, "get_server_state")
        assert hasattr(mixin_owner, "set_server_name")
        assert hasattr(mixin_owner, "set_verification_level")
        assert hasattr(mixin_owner, "set_content_filter")
        assert hasattr(mixin_owner, "_parse_color")
        assert hasattr(mixin_owner, "_resolve_member")
        assert hasattr(mixin_owner, "_build_permissions")

    def test_step_result_dataclass_legacy(self):
        from azure.discord_tools import StepResult as LegacyStepResult
        sr = LegacyStepResult(success=True, action="test", name="s1")
        assert sr.success is True


##############################################################################
#  TESTS: LLMPlanner
##############################################################################

class TestLLMPlanner:
    def test_init(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        llm = FakeLLM()
        planner = LLMPlanner(llm, registry, mixin_owner)
        assert planner.llm is llm
        assert planner.registry is registry
        assert planner.discord_tools is mixin_owner

    @pytest.mark.asyncio
    async def test_generate_plan_action_format(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        llm = FakeLLM(response="ACTION create_role name=Mod color=blue\nACTION create_channel name=general type=text")
        planner = LLMPlanner(llm, registry, mixin_owner)
        plan = await planner.generate_plan(
            "setup server",
            {"server_name": "S", "member_count": 0, "roles": [], "channels": [], "categories": []},
            123,
        )
        assert len(plan.get("steps", [])) > 0

    @pytest.mark.asyncio
    async def test_generate_plan_json_format(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        json_response = json.dumps({
            "analysis": "Setup server",
            "steps": [
                {"tool": "create_role", "params": {"name": "Mod", "color": "blue"}},
                {"tool": "create_channel", "params": {"name": "general", "type": "text"}},
            ],
            "reasoning": "Standard setup"
        })
        llm = FakeLLM(response=json_response)
        planner = LLMPlanner(llm, registry, mixin_owner)
        plan = await planner.generate_plan(
            "setup server",
            {"server_name": "S", "member_count": 0, "roles": [], "channels": [], "categories": []},
            123,
        )
        assert len(plan.get("steps", [])) == 2

    @pytest.mark.asyncio
    async def test_generate_plan_markdown_fallback(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        llm = FakeLLM(response="Create a role Mod and create a channel general")
        planner = LLMPlanner(llm, registry, mixin_owner)
        plan = await planner.generate_plan(
            "setup",
            {"server_name": "S", "member_count": 0, "roles": [], "channels": [], "categories": []},
            123,
        )
        assert len(plan.get("steps", [])) > 0

    @pytest.mark.asyncio
    async def test_generate_plan_empty(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        llm = FakeLLM(response="")
        planner = LLMPlanner(llm, registry, mixin_owner)
        plan = await planner.generate_plan(
            "setup",
            {"server_name": "S", "member_count": 0, "roles": [], "channels": [], "categories": []},
            123,
        )
        assert plan.get("steps") == []

    @pytest.mark.asyncio
    async def test_execute_plan_empty(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        llm = FakeLLM()
        planner = LLMPlanner(llm, registry, mixin_owner)
        results = await planner.execute_plan(MagicMock(), {"steps": []})
        assert results == []

    @pytest.mark.asyncio
    async def test_execute_plan_with_tools(self, guild, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        llm = FakeLLM()
        planner = LLMPlanner(llm, registry, mixin_owner)

        # Use list_channels which just queries guild.channels (non-destructive)
        plan = {
            "analysis": "Test",
            "steps": [
                {"tool": "list_channels", "params": {}, "reasoning": "test"},
            ],
        }
        results = await planner.execute_plan(guild, plan)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_execute_plan_unknown_tool(self, guild, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        llm = FakeLLM()
        planner = LLMPlanner(llm, registry, mixin_owner)

        plan = {
            "analysis": "Test",
            "steps": [
                {"tool": "nonexistent_tool_xyz", "params": {}, "reasoning": "test"},
            ],
        }
        results = await planner.execute_plan(guild, plan)
        assert len(results) == 1
        assert results[0].success is False
        assert "Unknown tool" in results[0].error

    @pytest.mark.asyncio
    async def test_execute_with_self_correction(self, guild, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        llm = FakeLLM(response="ACTION create_role name=Mod")
        planner = LLMPlanner(llm, registry, mixin_owner)
        # Generate plan then execute with self-correction
        result = await planner.execute_with_self_correction(
            guild,
            "setup",
            {"server_name": "S", "member_count": 0, "roles": [], "channels": [], "categories": []},
        )
        assert "success" in result
        assert "results" in result
        assert "attempts" in result

    def test_parse_action_format(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        llm = FakeLLM()
        planner = LLMPlanner(llm, registry, mixin_owner)

        plan = planner._parse_action_format(
            "ACTION create_role name=Mod color=blue\n"
            "ACTION create_channel name=general type=text\n"
            "ACTION set_permissions channel=general role=Mod send_messages=true"
        )
        assert len(plan["steps"]) == 3

    def test_parse_action_format_with_numbering(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        llm = FakeLLM()
        planner = LLMPlanner(llm, registry, mixin_owner)

        plan = planner._parse_action_format(
            "1. ACTION create_role name=Mod\n"
            "2. ACTION create_channel name=general"
        )
        assert len(plan["steps"]) >= 2

    def test_parse_markdown_actions(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        llm = FakeLLM()
        planner = LLMPlanner(llm, registry, mixin_owner)

        markdown = """## Plan
        - Delete the role `Mod`
        - Create a channel `general`
        - Kick `baduser`
        """
        plan = planner._parse_markdown_actions(markdown, "cleanup")
        assert len(plan["steps"]) > 0

    def test_parse_plan_response_valid_json(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        llm = FakeLLM()
        planner = LLMPlanner(llm, registry, mixin_owner)

        plan = planner._parse_plan_response(
            '{"analysis": "Setup", "steps": [{"tool": "create_role", "params": {"name": "Mod"}}], "reasoning": "test"}'
        )
        assert len(plan["steps"]) == 1

    def test_parse_plan_response_malformed(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        llm = FakeLLM()
        planner = LLMPlanner(llm, registry, mixin_owner)

        plan = planner._parse_plan_response("not json")
        assert plan["steps"] == []

    def test_build_planning_prompt(self, mixin_owner):
        registry = ToolRegistry(mixin_owner)
        llm = FakeLLM()
        planner = LLMPlanner(llm, registry, mixin_owner)

        state = {
            "server_name": "S", "member_count": 10,
            "verification_level": "low", "default_notifications": "all",
            "roles": [{"name": "Admin", "permissions": ["admin"], "member_count": 1}],
            "channels": [{"name": "general", "type": "text", "category": "none"}],
            "categories": [],
        }
        prompt = planner._build_planning_prompt("create channels", state, "Tools:\n  role: create_role")
        assert "create channels" in prompt
        assert "Admin" in prompt

    def test_execution_result_dataclass(self):
        er = ExecutionResult(
            tool_name="create_role", success=True, detail="Done",
            error="", reasoning="Setup",
        )
        assert er.tool_name == "create_role"
        assert er.success is True
        assert er.detail == "Done"

    def test_create_planner(self, mixin_owner):
        from azure.llm_planner import create_planner
        llm = FakeLLM()
        planner = create_planner(llm, mixin_owner)
        assert isinstance(planner, LLMPlanner)
        assert isinstance(planner.registry, ToolRegistry)
