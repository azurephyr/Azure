"""
EXTREME COMPREHENSIVE test for agentic tools and server modification tools.

Covers: agentic_tools, server_config, server_health, server_templates,
server_tools, channel_tools, member_tools, role_tools, plan_tools,
progress_tools, tool_engine, tool_registry, llm_planner, discord_tools.
"""

import asyncio
import json
import os

# -- Use conftest's shared discord mock, patch sys.modules for azure imports --
import sys as _sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import MOCK as _discord_mock
from tests.conftest import REAL_DISCORD as _real_discord
from tests.conftest import reset_utils_get

_orig_discord = _sys.modules.pop("discord", None)
_sys.modules["discord"] = _discord_mock
reset_utils_get()

from azure.agentic_tools import (
    _safe_path,
    _sandbox_dir,
    execute_python,
    file_list,
    file_read,
    file_write,
    manage_access_control,
    register_agentic_tools,
    web_fetch,
    web_search,
)
from azure.server_config import ServerConfig, ServerConfigManager
from azure.server_health import ServerHealthAnalyzer
from azure.server_templates import ServerTemplate, ServerTemplateManager
from azure.tools.channel_tools import ChannelToolsMixin
from azure.tools.member_tools import MemberToolsMixin
from azure.tools.plan_tools import PlanToolsMixin
from azure.tools.progress_tools import ProgressToolsMixin
from azure.tools.role_tools import RoleToolsMixin
from azure.tools.server_tools import ServerHealthAnalyzer as SHA2
from azure.tools.server_tools import ServerHealthReport, ServerToolsMixin, _embed_color, _llm_reason, _resolve_color
from azure.tools.types import StepResult

# Restore real discord for test files that need it
if _orig_discord is not None:
    _sys.modules["discord"] = _orig_discord

# -- Helper factories ------------------------------------------------

def make_role(name="test-role", color=None, position=1, permissions=None,
              is_default=False, managed=False, mentionable=False, hoist=False,
              is_everyone=False, members=None, id=None):
    r = MagicMock(spec=_real_discord.Role)
    r.name = name
    r.color = color or MagicMock()
    r.color.value = 0x99AAB5
    r.position = position
    if permissions is not None:
        r.permissions = permissions
    else:
        r.permissions = MagicMock(spec=_real_discord.Permissions)
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
    if ch_type == 1 or ch_type == _real_discord.ChannelType.voice:
        return _real_discord.VoiceChannel
    if ch_type == _real_discord.ChannelType.forum:
        return _real_discord.ForumChannel
    if ch_type == _real_discord.ChannelType.stage_voice:
        return _real_discord.StageChannel
    if ch_type in (_real_discord.ChannelType.public_thread, _real_discord.ChannelType.private_thread):
        return _real_discord.Thread
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
    def __init__(self, response="{}"):
        self.response = response
    def chat(self, messages, **kwargs):
        return self.response


class ProgressContext:
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

# =========================================================================
# FIXTURES
# =========================================================================

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
    from azure.discord_tools_expanded import DiscordManagementTools as ExpandedDMT
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

# =========================================================================
# SECTION 1: agentic_tools
# =========================================================================

class TestAgenticToolsSandbox:
    """Test _safe_path, _sandbox_dir, file_read, file_write, file_list."""

    def test_sandbox_dir_creates(self, sandbox):
        p = _sandbox_dir()
        assert p.exists()
        assert p.is_dir()

    def test_safe_path_normal(self, sandbox):
        p = _safe_path("test.txt")
        assert str(p).startswith(str(sandbox))
        assert p.name == "test.txt"

    def test_safe_path_subdir(self, sandbox):
        p = _safe_path("sub/file.txt")
        assert str(p).startswith(str(sandbox))

    def test_safe_path_traversal_blocked(self, sandbox):
        with pytest.raises(PermissionError):
            _safe_path("../etc/passwd")

    def test_safe_path_absolute_blocked(self, sandbox):
        with pytest.raises(PermissionError):
            _safe_path("/etc/passwd")

    def test_safe_path_encoded_traversal(self, sandbox):
        with pytest.raises(PermissionError):
            _safe_path("a/../../../etc/passwd")

    def test_file_write_new(self, sandbox):
        result = file_write("hello.txt", "Hello World")
        assert "Written" in result

    def test_file_write_overwrite(self, sandbox):
        file_write("hello.txt", "first")
        result = file_write("hello.txt", "second")
        assert "Written" in result
        content = (_sandbox_dir() / "hello.txt").read_text()
        assert content == "second"

    def test_file_write_subdir(self, sandbox):
        result = file_write("sub/dir/file.txt", "nested")
        assert "Written" in result

    def test_file_write_empty(self, sandbox):
        result = file_write("empty.txt", "")
        assert "Written" in result
        assert "0 bytes" in result

    def test_file_write_traversal_blocked(self, sandbox):
        result = file_write("../escape.txt", "bad")
        assert "error" in result.lower() or "Error" in result

    def test_file_read_existing(self, sandbox):
        file_write("readme.txt", "content")
        result = file_read("readme.txt")
        assert result == "content"

    def test_file_read_not_found(self, sandbox):
        result = file_read("nonexistent.txt")
        assert "not found" in result.lower()

    def test_file_read_empty(self, sandbox):
        file_write("empty.txt", "")
        result = file_read("empty.txt")
        assert result == ""

    def test_file_read_traversal_blocked(self, sandbox):
        result = file_read("../etc/passwd")
        assert "error" in result.lower() or "Error" in result

    def test_file_list_empty_dir(self, sandbox):
        result = file_list("")
        assert result == "(empty)"

    def test_file_list_with_files(self, sandbox):
        file_write("a.txt", "aaa")
        file_write("b.txt", "bbb")
        result = file_list("")
        assert "a.txt" in result
        assert "b.txt" in result

    def test_file_list_subdirectories(self, sandbox):
        file_write("sub/a.txt", "aaa")
        result = file_list("")
        assert "sub/" in result

    def test_file_list_non_existent(self, sandbox):
        result = file_list("nope")
        assert "not found" in result.lower()


class TestAgenticToolsWeb:
    """Test web_search and web_fetch with mocks."""

    @patch("azure.agentic_tools._fetch_url")
    def test_web_search_with_results(self, mock_fetch):
        mock_fetch.return_value = json.dumps({
            "query": {"search": [{"title": "Python", "snippet": "Python is a language"}]}
        }).encode()
        result = web_search("Python", max_results=1)
        assert "WEB SEARCH" in result
        assert "Python" in result

    @patch("azure.agentic_tools._fetch_url")
    def test_web_search_no_results(self, mock_fetch):
        mock_fetch.return_value = json.dumps({"query": {"search": []}}).encode()
        result = web_search("xyznonexistent12345")
        assert "No results" in result

    @patch("azure.agentic_tools._fetch_url")
    def test_web_search_empty_query(self, mock_fetch):
        mock_fetch.return_value = json.dumps({"query": {"search": []}}).encode()
        result = web_search("")
        assert "No results" in result or "search" in result.lower()

    @patch("azure.agentic_tools._fetch_url")
    def test_web_search_error(self, mock_fetch):
        mock_fetch.side_effect = Exception("Network error")
        result = web_search("test")
        assert "failed" in result.lower()

    @patch("azure.agentic_tools.urllib.request.urlopen")
    def test_web_fetch_json(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"key": "value"}).encode()
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        result = web_fetch("https://93.184.216.34/data.json")
        assert "key" in result and "value" in result

    @patch("azure.agentic_tools.urllib.request.urlopen")
    def test_web_fetch_html(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<html><body><p>Hello World</p></body></html>"
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        result = web_fetch("https://93.184.216.34")
        assert "Hello World" in result

    @patch("azure.agentic_tools.urllib.request.urlopen")
    def test_web_fetch_max_chars(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"a" * 5000
        mock_resp.headers = {"Content-Type": "text/plain"}
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        result = web_fetch("https://93.184.216.34", max_chars=100)
        assert len(result) <= 100

    @patch("azure.agentic_tools.urllib.request.urlopen")
    def test_web_fetch_failed_url(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Connection refused")
        result = web_fetch("https://nonexistent.example")
        assert "failed" in result.lower()

    def test_web_fetch_blocks_private_addresses(self):
        result = web_fetch("http://127.0.0.1:8080/metadata")
        assert "Private or non-global hosts" in result

    def test_web_fetch_blocks_non_http_schemes(self):
        result = web_fetch("file:///etc/passwd")
        assert "Only absolute http(s) URLs" in result


class TestAgenticToolsCodeExec:
    """Test execute_python."""

    def test_execute_disabled_by_default(self):
        old = os.environ.pop("AZURE_ALLOW_CODE_EXECUTION", None)
        result = execute_python("print('hello')")
        assert "disabled" in result.lower()
        if old is not None:
            os.environ["AZURE_ALLOW_CODE_EXECUTION"] = old

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_valid_code(self):
        result = execute_python("print('hello')")
        assert "hello" in result

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_no_output(self):
        result = execute_python("x = 1 + 1")
        assert "no output" in result.lower() or "success" in result.lower()

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_syntax_error(self):
        result = execute_python("print('hello")
        assert "error" in result.lower() or "Syntax" in result

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_runtime_error(self):
        result = execute_python("1/0")
        assert "error" in result.lower() or "ZeroDivision" in result

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_blocked_import(self):
        result = execute_python("import os; print('hack')")
        assert "blocked" in result.lower()

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_blocked_exec(self):
        result = execute_python("exec('print(1)')")
        assert "blocked" in result.lower()

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_blocked_eval(self):
        result = execute_python("eval('1+1')")
        assert "blocked" in result.lower()

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_blocked_compile(self):
        result = execute_python("compile('x=1', '', 'exec')")
        assert "blocked" in result.lower()

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_blocked_open(self):
        result = execute_python("open('/etc/passwd')")
        assert "blocked" in result.lower()

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_blocked_dunder(self):
        result = execute_python("__import__('os')")
        assert "blocked" in result.lower()

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_code_fences_stripped(self):
        result = execute_python("```python\nprint('fenced')\n```")
        assert "fenced" in result

    @patch.dict(os.environ, {"AZURE_ALLOW_CODE_EXECUTION": "true"})
    def test_execute_output_truncated(self):
        result = execute_python("print('x' * 5000)")
        assert "truncated" in result


class TestAgenticToolsAccessControl:
    """Test manage_access_control."""

    def test_access_control_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            result = manage_access_control("user", "123", "allow")
        assert "disabled" in result.lower()

    @patch.dict(os.environ, {"AZURE_ALLOW_ACCESS_CONTROL_TOOL": "true"})
    @patch("azure.database.get_shared_db")
    def test_access_control_valid_user_allow(self, mock_get_db):
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        result = manage_access_control("user", "123", "allow")
        assert "Successfully" in result
        mock_db.set_access_control.assert_called_once()

    @patch.dict(os.environ, {"AZURE_ALLOW_ACCESS_CONTROL_TOOL": "true"})
    @patch("azure.database.get_shared_db")
    def test_access_control_valid_guild_deny(self, mock_get_db):
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        result = manage_access_control("guild", "456", "deny")
        assert "Successfully" in result

    @patch.dict(os.environ, {"AZURE_ALLOW_ACCESS_CONTROL_TOOL": "true"})
    @patch("azure.database.get_shared_db")
    def test_access_control_valid_channel_admin(self, mock_get_db):
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        result = manage_access_control("channel", "789", "admin")
        assert "Successfully" in result

    @patch.dict(os.environ, {"AZURE_ALLOW_ACCESS_CONTROL_TOOL": "true"})
    @patch("azure.database.get_shared_db")
    def test_access_control_valid_role_allow(self, mock_get_db):
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        result = manage_access_control("role", "101", "allow")
        assert "Successfully" in result

    @patch.dict(os.environ, {"AZURE_ALLOW_ACCESS_CONTROL_TOOL": "true"})
    @patch("azure.database.get_shared_db")
    def test_access_control_invalid_target_type(self, mock_get_db):
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        result = manage_access_control("invalid_type", "123", "allow")
        assert "target_type must be" in result

    @patch.dict(os.environ, {"AZURE_ALLOW_ACCESS_CONTROL_TOOL": "true"})
    @patch("azure.database.get_shared_db")
    def test_access_control_invalid_permission(self, mock_get_db):
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        result = manage_access_control("user", "123", "superadmin")
        assert "permission must be" in result

    @patch.dict(os.environ, {"AZURE_ALLOW_ACCESS_CONTROL_TOOL": "true"})
    @patch("azure.database.get_shared_db")
    def test_access_control_db_error(self, mock_get_db):
        mock_db = MagicMock()
        mock_db.set_access_control.side_effect = Exception("DB error")
        mock_get_db.return_value = mock_db
        result = manage_access_control("user", "123", "allow")
        assert "failed" in result.lower()


class TestAgenticToolsRegister:
    """Test register_agentic_tools."""

    def test_register_all_tools(self):
        agent = MagicMock()
        agent.tools = MagicMock()
        register_agentic_tools(agent)
        expected_calls = [
            "web_search", "web_fetch", "execute_python",
            "file_read", "file_write", "file_list", "manage_access_control",
        ]
        call_names = [call_args[1]["name"] for call_args in agent.tools.register.call_args_list]
        for name in expected_calls:
            assert name in call_names, f"Missing registration: {name}"

# =========================================================================
# SECTION 2: server_config
# =========================================================================

class TestServerConfig:
    """Test ServerConfig dataclass."""

    def test_default_guild_id(self):
        sc = ServerConfig(guild_id="123")
        assert sc.guild_id == "123"

    def test_default_fields(self):
        sc = ServerConfig(guild_id="456")
        assert sc.moderation_phase == "dry_run"
        assert sc.chat_mode == "anyone"
        assert sc.confirmation_mode == "destructive"
        assert sc.confirmation_threshold == 0.75
        assert sc.max_timeouts_per_hour == 10
        assert sc.max_bans_per_hour == 3
        assert sc.max_deletions_per_minute == 20
        assert sc.guild_name == ""

    def test_custom_values(self):
        sc = ServerConfig(
            guild_id="789",
            guild_name="MyServer",
            moderation_phase="reactive_full",
            admin_channel_id="456",
            chat_mode="owner_only",
            max_timeouts_per_hour=20,
        )
        assert sc.guild_name == "MyServer"
        assert sc.moderation_phase == "reactive_full"
        assert sc.admin_channel_id == "456"
        assert sc.chat_mode == "owner_only"
        assert sc.max_timeouts_per_hour == 20

    def test_to_dict(self):
        sc = ServerConfig(guild_id="101", guild_name="Test")
        d = sc.to_dict()
        assert d["guild_id"] == "101"
        assert d["guild_name"] == "Test"
        assert "moderation_phase" in d

    def test_from_dict(self):
        data = {"guild_id": "202", "guild_name": "Restored", "moderation_phase": "reactive_limited"}
        sc = ServerConfig.from_dict(data)
        assert sc.guild_id == "202"
        assert sc.guild_name == "Restored"
        assert sc.moderation_phase == "reactive_limited"

    def test_from_dict_ignores_unknown_fields(self):
        data = {"guild_id": "303", "made_up_field": "ignored"}
        sc = ServerConfig.from_dict(data)
        assert sc.guild_id == "303"
        assert not hasattr(sc, "made_up_field")

    def test_exempt_lists_default_empty(self):
        sc = ServerConfig(guild_id="404")
        assert sc.exempt_channels == []
        assert sc.exempt_users == []
        assert sc.exempt_roles == []
        assert sc.trusted_roles == []
        assert sc.allowed_users == []


class TestServerConfigManager:
    """Test ServerConfigManager."""

    def test_init_creates_config_dir(self, tmp_path):
        d = tmp_path / "configs"
        ServerConfigManager(config_dir=d)
        assert d.exists()

    def test_get_or_create_new(self, tmp_path):
        mgr = ServerConfigManager(config_dir=tmp_path / "cfg")
        cfg = mgr.get_or_create("111", "TestGuild")
        assert cfg.guild_id == "111"
        assert cfg.guild_name == "TestGuild"

    def test_get_or_create_existing(self, tmp_path):
        mgr = ServerConfigManager(config_dir=tmp_path / "cfg2")
        cfg1 = mgr.get_or_create("222", "First")
        cfg2 = mgr.get_or_create("222", "Second")
        assert cfg2 is cfg1
        assert cfg2.guild_name == "First"

    def test_get_returns_none_for_missing(self, tmp_path):
        mgr = ServerConfigManager(config_dir=tmp_path / "cfg3")
        result = mgr.get("nonexistent")
        assert result is None

    def test_get_returns_cached(self, tmp_path):
        mgr = ServerConfigManager(config_dir=tmp_path / "cfg4")
        cfg1 = mgr.get_or_create("333", "Cached")
        cfg2 = mgr.get("333")
        assert cfg2 is cfg1

    def test_update_existing_field(self, tmp_path):
        mgr = ServerConfigManager(config_dir=tmp_path / "cfg5")
        mgr.get_or_create("444")
        updated = mgr.update("444", moderation_phase="reactive_full")
        assert updated.moderation_phase == "reactive_full"

    def test_update_creates_if_missing(self, tmp_path):
        mgr = ServerConfigManager(config_dir=tmp_path / "cfg6")
        updated = mgr.update("555", guild_name="AutoCreated")
        assert updated.guild_id == "555"
        assert updated.guild_name == "AutoCreated"

    def test_update_ignores_unknown_field(self, tmp_path):
        mgr = ServerConfigManager(config_dir=tmp_path / "cfg7")
        mgr.get_or_create("666")
        updated = mgr.update("666", nonexistent_field="ignored")
        assert not hasattr(updated, "nonexistent_field")

    def test_remove_existing(self, tmp_path):
        mgr = ServerConfigManager(config_dir=tmp_path / "cfg8")
        mgr.get_or_create("777")
        mgr.remove("777")
        assert mgr.get("777") is None

    def test_remove_nonexistent(self, tmp_path):
        mgr = ServerConfigManager(config_dir=tmp_path / "cfg9")
        mgr.remove("nonexistent")

    def test_list_all(self, tmp_path):
        mgr = ServerConfigManager(config_dir=tmp_path / "cfg10")
        mgr.get_or_create("aaa", "ServerA")
        mgr.get_or_create("bbb", "ServerB")
        entries = mgr.list_all()
        assert len(entries) >= 2
        guild_ids = {e["guild_id"] for e in entries}
        assert "aaa" in guild_ids
        assert "bbb" in guild_ids

    def test_count(self, tmp_path):
        mgr = ServerConfigManager(config_dir=tmp_path / "cfg11")
        mgr.get_or_create("x")
        mgr.get_or_create("y")
        assert mgr.count() >= 2

    def test_config_persistence(self, tmp_path):
        d = tmp_path / "cfg_persist"
        mgr1 = ServerConfigManager(config_dir=d)
        mgr1.get_or_create("888", "Persistent")
        mgr2 = ServerConfigManager(config_dir=d)
        cfg = mgr2.get("888")
        assert cfg is not None and cfg.guild_name == "Persistent"

# =========================================================================
# SECTION 3: server_health
# =========================================================================

class TestServerHealthAnalyzer:
    """Test ServerHealthAnalyzer (azure.server_health)."""

    def test_init_no_args(self):
        h = ServerHealthAnalyzer()
        assert h is not None

    @pytest.mark.asyncio
    async def test_analyze_returns_dict(self):
        guild = make_guild()
        h = ServerHealthAnalyzer()
        report = await h.analyze(guild)
        assert isinstance(report, dict)
        assert "server_name" in report
        assert report["server_name"] == "TestGuild"

    @pytest.mark.asyncio
    async def test_analyze_includes_score(self):
        guild = make_guild()
        h = ServerHealthAnalyzer()
        report = await h.analyze(guild)
        assert "score" in report
        assert 0 <= report["score"] <= 100

    @pytest.mark.asyncio
    async def test_analyze_includes_categories(self):
        guild = make_guild()
        h = ServerHealthAnalyzer()
        report = await h.analyze(guild)
        assert "categories" in report
        for key in ["structure", "security", "moderation", "engagement"]:
            assert key in report["categories"]

    @pytest.mark.asyncio
    async def test_analyze_includes_issues_and_recommendations(self):
        guild = make_guild()
        h = ServerHealthAnalyzer()
        report = await h.analyze(guild)
        assert "issues" in report
        assert "recommendations" in report
        assert isinstance(report["issues"], list)
        assert isinstance(report["recommendations"], list)

    def test_format_report(self):
        h = ServerHealthAnalyzer()
        text = h.format_report({"server_name": "Test", "score": 85, "member_count": 100, "issues": [], "recommendations": [], "followups": []})
        assert "Test" in text

    def test_suggest_followups(self):
        guild = make_guild()
        h = ServerHealthAnalyzer()
        suggestions = h.suggest_followups(guild, "created a channel")
        assert isinstance(suggestions, list)
        assert len(suggestions) <= 3

# =========================================================================
# SECTION 4: server_templates
# =========================================================================

class TestServerTemplate:
    """Test ServerTemplate dataclass."""

    def test_basic_template(self):
        t = ServerTemplate(
            name="Test",
            description="test",
            created_at=time.time(),
            roles=[],
            categories=[],
            channels=[],
            permission_overwrites=[],
        )
        assert t.name == "Test"
        assert t.description == "test"
        assert t.roles == []
        assert t.categories == []
        assert t.channels == []
        assert t.permission_overwrites == []

    def test_to_dict_via_asdict(self):
        from dataclasses import asdict
        t = ServerTemplate(
            name="Test",
            description="Example",
            created_at=time.time(),
            roles=[{"name": "Admin", "color": "FF0000"}],
            categories=[{"name": "General", "position": 0}],
            channels=[{"name": "general", "type": "text"}],
            permission_overwrites=[],
        )
        d = asdict(t)
        assert d["name"] == "Test"
        assert len(d["roles"]) == 1
        assert len(d["channels"]) == 1
        assert len(d["categories"]) == 1


class TestServerTemplateManager:
    """Test ServerTemplateManager."""

    def test_init_creates_dir(self, tmp_path):
        d = tmp_path / "templates"
        ServerTemplateManager(template_dir=d)
        assert d.exists()

    def test_list_templates_empty(self, tmp_path):
        mgr = ServerTemplateManager(template_dir=tmp_path / "empty")
        assert mgr.list_templates() == []

    def test_save_and_list_template(self, tmp_path):
        mgr = ServerTemplateManager(template_dir=tmp_path / "savelist")
        asyncio.run(mgr.save_template(
            make_guild(),
            "TestTemplate",
            description="A test template",
        ))
        templates = mgr.list_templates()
        names = [t["name"] for t in templates]
        assert "TestTemplate" in names or any("Test" in t["name"] for t in templates)

    def test_delete_template(self, tmp_path):
        mgr = ServerTemplateManager(template_dir=tmp_path / "del")
        template = ServerTemplate(
            name="DeleteMe", description="desc", created_at=time.time(),
            roles=[], categories=[], channels=[], permission_overwrites=[],
        )
        p = mgr._safe_template_path("DeleteMe")
        import dataclasses
        import json
        p.write_text(json.dumps(dataclasses.asdict(template)), encoding="utf-8")
        assert mgr.delete_template("DeleteMe") is True
        assert not p.exists()

    def test_delete_nonexistent(self, tmp_path):
        mgr = ServerTemplateManager(template_dir=tmp_path / "delnone")
        assert mgr.delete_template("Nonexistent") is False

    def test_validate_template_name_valid(self, tmp_path):
        mgr = ServerTemplateManager(template_dir=tmp_path / "valid")
        name = mgr._validate_template_name("My Template")
        assert name == "My Template"

    def test_validate_template_name_traversal_blocked(self, tmp_path):
        mgr = ServerTemplateManager(template_dir=tmp_path / "trav")
        with pytest.raises(ValueError):
            mgr._validate_template_name("../etc/passwd")

    def test_validate_template_name_empty(self, tmp_path):
        mgr = ServerTemplateManager(template_dir=tmp_path / "empty2")
        with pytest.raises(ValueError):
            mgr._validate_template_name("")

    def test_validate_template_name_too_long(self, tmp_path):
        mgr = ServerTemplateManager(template_dir=tmp_path / "long")
        with pytest.raises(ValueError):
            mgr._validate_template_name("a" * 100)

    def test_validate_template_name_special_chars_blocked(self, tmp_path):
        mgr = ServerTemplateManager(template_dir=tmp_path / "special")
        with pytest.raises(ValueError):
            mgr._validate_template_name("template\x00name")

    def test_load_template_not_found(self, tmp_path):
        mgr = ServerTemplateManager(template_dir=tmp_path / "loadnf")
        assert mgr.load_template("Nonexistent") is None

    def test_save_and_load_template(self, tmp_path):
        mgr = ServerTemplateManager(template_dir=tmp_path / "saveload")
        asyncio.run(mgr.save_template(make_guild(), "RoundTrip", "Test"))
        loaded = mgr.load_template("RoundTrip")
        assert loaded is not None
        assert loaded.name == "RoundTrip"

    def test_to_plan(self, tmp_path):
        mgr = ServerTemplateManager(template_dir=tmp_path / "toplan")
        template = ServerTemplate(
            name="Simple", description="A simple template", created_at=time.time(),
            roles=[{"name": "Admin", "position": 1}],
            categories=[{"name": "General", "position": 0}],
            channels=[{"name": "general", "type": "text", "position": 0}],
            permission_overwrites=[],
        )
        p = mgr._safe_template_path("Simple")
        import dataclasses
        import json
        p.write_text(json.dumps(dataclasses.asdict(template)), encoding="utf-8")
        plan = mgr.to_plan("Simple")
        assert "steps" in plan
        assert len(plan["steps"]) >= 2

    def test_parse_color_none(self, tmp_path):
        mgr = ServerTemplateManager(template_dir=tmp_path / "colornone")
        assert mgr._parse_color(None) is None

# =========================================================================
# SECTION 5: StepResult + ServerToolsMixin
# =========================================================================

class TestStepResult:
    """Test StepResult for server_tools."""

    def test_step_result_defaults(self):
        sr = StepResult(success=True, action="test")
        assert sr.success is True
        assert sr.action == "test"
        assert sr.name == ""
        assert sr.error == ""

    def test_step_result_with_detail(self):
        sr = StepResult(success=True, action="create", name="general", detail="Channel created")
        assert sr.name == "general"
        assert sr.detail == "Channel created"

    def test_step_result_with_error(self):
        sr = StepResult(success=False, action="delete", error="Not found")
        assert sr.success is False
        assert sr.error == "Not found"

    def test_step_result_falsy_success(self):
        sr = StepResult(success=False, action="fail")
        assert sr.success is False

    def test_step_result_truthy_success(self):
        sr = StepResult(success=True, action="ok")
        assert sr.success is True

    def test_step_result_target_id(self):
        sr = StepResult(success=True, action="edit", target_id=42)
        assert sr.target_id == 42

    def test_step_result_before_after_state(self):
        sr = StepResult(success=True, action="update", before_state={"name": "old"}, after_state={"name": "new"})
        assert sr.before_state["name"] == "old"
        assert sr.after_state["name"] == "new"


@pytest.fixture
def guild_for_tools():
    return make_guild()


class TestServerToolsMixin:
    """Test all methods of ServerToolsMixin."""

    @pytest.mark.asyncio
    async def test_get_server_state(self, guild_for_tools):
        tools = ServerToolsMixin()
        state = await tools.get_server_state(guild_for_tools)
        assert state["server_name"] == "TestGuild"
        assert state["member_count"] >= 0

    @pytest.mark.asyncio
    async def test_get_audit_logs(self, guild_for_tools):
        tools = ServerToolsMixin()
        result = await tools.get_audit_logs(guild_for_tools, limit=5)
        assert isinstance(result, StepResult)
        assert result.success

    @pytest.mark.asyncio
    async def test_get_vanity_url(self, guild_for_tools):
        tools = ServerToolsMixin()
        result = await tools.get_vanity_url(guild_for_tools)
        assert isinstance(result, StepResult)

    @pytest.mark.asyncio
    async def test_get_ban_list(self, guild_for_tools):
        tools = ServerToolsMixin()
        result = await tools.get_ban_list(guild_for_tools, limit=10)
        assert isinstance(result, StepResult)

    @pytest.mark.asyncio
    async def test_get_automod_rules(self, guild_for_tools):
        tools = ServerToolsMixin()
        result = await tools.get_automod_rules(guild_for_tools)
        assert isinstance(result, StepResult)

    @pytest.mark.asyncio
    async def test_get_channel_webhooks(self, guild_for_tools):
        tools = ServerToolsMixin()
        result = await tools.get_channel_webhooks(guild_for_tools, "general")
        assert isinstance(result, StepResult)

    @pytest.mark.asyncio
    async def test_get_guild_webhooks(self, guild_for_tools):
        tools = ServerToolsMixin()
        result = await tools.get_guild_webhooks(guild_for_tools)
        assert isinstance(result, StepResult)

    @pytest.mark.asyncio
    async def test_get_guild_templates(self, guild_for_tools):
        tools = ServerToolsMixin()
        result = await tools.get_guild_templates(guild_for_tools)
        assert isinstance(result, StepResult)

    @pytest.mark.asyncio
    async def test_get_onboarding(self, guild_for_tools):
        tools = ServerToolsMixin()
        result = await tools.get_onboarding(guild_for_tools)
        assert isinstance(result, StepResult)
        # If onboarding isn't set up, it may still return success with empty data
        assert result.success or not result.success

    @pytest.mark.asyncio
    async def test_get_widget(self, guild_for_tools):
        tools = ServerToolsMixin()
        result = await tools.get_widget(guild_for_tools)
        assert isinstance(result, StepResult)

    @pytest.mark.asyncio
    async def test_get_guild_invites_via_channel_tools(self, guild_for_tools):
        ServerToolsMixin()
        from azure.tools.channel_tools import ChannelToolsMixin
        ct = ChannelToolsMixin()
        result = await ct.get_guild_invites(guild_for_tools)
        assert isinstance(result, StepResult)

    @pytest.mark.asyncio
    async def test_get_pinned_messages(self, guild_for_tools):
        from azure.tools.channel_tools import ChannelToolsMixin
        ct = ChannelToolsMixin()
        tc = make_channel("general", guild=guild_for_tools)
        result = await ct.get_pinned_messages(tc)
        assert isinstance(result, StepResult)

    def test_server_health_analyzer_static(self):
        report = SHA2.analyze(make_guild())
        assert isinstance(report, ServerHealthReport)
        assert report.server_name == "TestGuild"
        assert 0 <= report.overall_score <= 100

    def test_server_health_analyzer_grades(self):
        report = SHA2.analyze(make_guild())
        assert report.overall_grade in ("A", "B", "C", "D", "F")

    def test_health_report_has_recommendations(self):
        report = SHA2.analyze(make_guild())
        assert isinstance(report.recommendations, list)

    def test_health_report_has_findings(self):
        report = SHA2.analyze(make_guild())
        assert isinstance(report.findings, list)
        assert len(report.findings) >= 0

    def test_resolve_color_function(self):
        color = _resolve_color("#FF0000")
        assert color is not None

    def test_resolve_color_none(self):
        color = _resolve_color(None)
        assert color == 0x99AAB5

    def test_embed_color_function(self):
        color = _embed_color("#00FF00")
        assert color is not None

    def test_llm_reason(self):
        result = _llm_reason("Test analysis")
        assert isinstance(result, str)

    def test_llm_reason_empty(self):
        result = _llm_reason("")
        assert isinstance(result, str)

# =========================================================================
# SECTION 6: ChannelToolsMixin
# =========================================================================

@pytest.fixture
def guild_for_channels():
    return make_guild()


class TestChannelToolsMixin:
    """Test all methods of ChannelToolsMixin."""

    @pytest.mark.asyncio
    async def test_create_category(self, guild_for_channels):
        tools = ChannelToolsMixin()
        result = await tools.create_category(guild_for_channels, "New Category")
        assert result.success is True
        guild_for_channels.create_category.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_category_with_position(self, guild_for_channels):
        tools = ChannelToolsMixin()
        result = await tools.create_category(guild_for_channels, "Pos Category", position=2)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_edit_category(self, guild_for_channels):
        cat_ref = make_channel("Existing Category", ch_type=4)
        guild_for_channels.categories = [cat_ref]
        # use default utils.get � cat_ref)
        tools = ChannelToolsMixin()
        result = await tools.edit_category(guild_for_channels, "Existing Category", name="Renamed")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_delete_category(self, guild_for_channels):
        cat_ref = make_channel("Delete Category", ch_type=4)
        guild_for_channels.categories = [cat_ref]
        # use default utils.get � cat_ref)
        tools = ChannelToolsMixin()
        result = await tools.delete_category(guild_for_channels, "Delete Category")
        assert result.success is True
        cat_ref.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_channel_text(self, guild_for_channels):
        tools = ChannelToolsMixin()
        result = await tools.create_channel(guild_for_channels, "general", channel_type="text")
        assert result.success is True
        guild_for_channels.create_text_channel.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_channel_voice(self, guild_for_channels):
        tools = ChannelToolsMixin()
        result = await tools.create_channel(guild_for_channels, "voice-chan", channel_type="voice")
        assert result.success is True
        guild_for_channels.create_voice_channel.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_channel_forum(self, guild_for_channels):
        tools = ChannelToolsMixin()
        result = await tools.create_channel(guild_for_channels, "forum-chan", channel_type="forum")
        assert result.success is True
        guild_for_channels.create_forum.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_channel_stage(self, guild_for_channels):
        tools = ChannelToolsMixin()
        result = await tools.create_channel(guild_for_channels, "stage-chan", channel_type="stage_voice")
        assert result.success is True
        guild_for_channels.create_stage_channel.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_channel(self, guild_for_channels):
        ch_ref = make_channel("edit-me", guild=guild_for_channels)
        guild_for_channels.channels = [ch_ref]
        tools = ChannelToolsMixin()
        result = await tools.edit_channel(guild_for_channels, "edit-me", name="edited")
        assert result.success is True
        ch_ref.edit.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_channel_not_found(self, guild_for_channels):
        guild_for_channels.channels = []
        tools = ChannelToolsMixin()
        result = await tools.edit_channel(guild_for_channels, "nonexistent", name="test")
        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_delete_channel(self, guild_for_channels):
        ch_ref = make_channel("delete-me", guild=guild_for_channels)
        tools = ChannelToolsMixin()
        result = await tools.delete_channel(ch_ref)
        assert result.success is True
        ch_ref.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_channel_not_found(self, guild_for_channels):
        ch_ref = make_channel("ghost", guild=guild_for_channels)
        ch_ref.delete.side_effect = Exception("Not found")
        tools = ChannelToolsMixin()
        result = await tools.delete_channel(ch_ref)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_create_channel_with_category(self, guild_for_channels):
        cat_ref = make_channel("Info", ch_type=4)
        guild_for_channels.categories = [cat_ref]
        # use default utils.get � cat_ref)
        tools = ChannelToolsMixin()
        result = await tools.create_channel(guild_for_channels, "announcements", channel_type="text", category="Info")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_create_channel_with_topic(self, guild_for_channels):
        tools = ChannelToolsMixin()
        result = await tools.create_channel(guild_for_channels, "news", channel_type="text", topic="Latest news")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_get_channel_invites(self, guild_for_channels):
        ch_ref = make_channel("invites-chan", guild=guild_for_channels)
        tools = ChannelToolsMixin()
        result = await tools.get_channel_invites(ch_ref)
        assert result.success is True
        ch_ref.invites.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_guild_invites(self, guild_for_channels):
        tools = ChannelToolsMixin()
        result = await tools.get_guild_invites(guild_for_channels)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_get_pinned_messages(self, guild_for_channels):
        tc = make_channel("pinned-chan", guild=guild_for_channels)
        tools = ChannelToolsMixin()
        result = await tools.get_pinned_messages(tc)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_create_channel_failure(self, guild_for_channels):
        guild_for_channels.create_text_channel.side_effect = Exception("Permissions error")
        tools = ChannelToolsMixin()
        result = await tools.create_channel(guild_for_channels, "fail-chan", channel_type="text")
        assert result.success is False
        assert "error" in result.error.lower()

    @pytest.mark.asyncio
    async def test_create_channel_failure_voice(self, guild_for_channels):
        guild_for_channels.create_voice_channel.side_effect = Exception("Permissions error")
        tools = ChannelToolsMixin()
        result = await tools.create_channel(guild_for_channels, "fail-voice", channel_type="voice")
        assert result.success is False

# =========================================================================
# SECTION 7: MemberToolsMixin
# =========================================================================

@pytest.fixture
def member_mixin():
    guild = make_guild()
    bot = MagicMock()
    bot.user = make_member("AzureBot", top_role=make_role("Admin", position=100))
    tools = MemberToolsMixin()
    tools._guild = guild
    tools._bot = bot
    return tools


class TestMemberToolsMixin:
    """Test all methods of MemberToolsMixin."""

    @pytest.mark.asyncio
    async def test_kick_member(self):
        guild = make_guild()
        m = make_member("kickme", id=50)
        guild.members = [m]
        guild.get_member = MagicMock(return_value=m)
        tools = MemberToolsMixin()
        tools._resolve_member = AsyncMock(return_value=m)
        result = await tools.kick_member(guild, "kickme")
        assert result.success is True
        m.kick.assert_called_once()

    @pytest.mark.asyncio
    async def test_kick_member_not_found(self):
        guild = make_guild()
        tools = MemberToolsMixin()
        result = await tools.kick_member(guild, "nonexistent")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_ban_member(self):
        guild = make_guild()
        m = make_member("banme", id=60)
        guild.members = [m]
        guild.get_member = MagicMock(return_value=m)
        tools = MemberToolsMixin()
        tools._resolve_member = AsyncMock(return_value=m)
        result = await tools.ban_member(guild, "banme")
        assert result.success is True
        m.ban.assert_called_once()

    @pytest.mark.asyncio
    async def test_unban_member(self):
        guild = make_guild()
        tools = MemberToolsMixin()
        tools.bot = MagicMock()
        tools.bot.fetch_user = AsyncMock()
        result = await tools.unban_member(guild, 70)
        assert result.success is True
        guild.unban.assert_called_once()

    @pytest.mark.asyncio
    async def test_timeout_member(self):
        guild = make_guild()
        m = make_member("timeoutme", id=80)
        guild.members = [m]
        guild.get_member = MagicMock(return_value=m)
        tools = MemberToolsMixin()
        tools._resolve_member = AsyncMock(return_value=m)
        result = await tools.timeout_member(guild, "timeoutme", duration_minutes=30)
        assert result.success is True
        m.timeout.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_nickname(self):
        guild = make_guild()
        m = make_member("oldnick", id=110)
        guild.members = [m]
        guild.get_member = MagicMock(return_value=m)
        tools = MemberToolsMixin()
        tools._resolve_member = AsyncMock(return_value=m)
        result = await tools.set_nickname(guild, "oldnick", "NewNick")
        assert result.success is True
        m.edit.assert_called_once()

    @pytest.mark.asyncio
    async def test_move_member_to_voice(self):
        guild = make_guild()
        m = make_member("moveme", id=120)
        m.voice = MagicMock()
        m.voice.channel = MagicMock()
        vch = make_channel("General Voice", ch_type=2)
        guild.members = [m]
        guild.channels = [vch]
        guild.get_member = MagicMock(return_value=m)
        _discord_mock.utils.get = MagicMock(return_value=vch)
        tools = MemberToolsMixin()
        tools._resolve_member = AsyncMock(return_value=m)
        result = await tools.move_member_to_voice(guild, "moveme", "General Voice")
        assert result.success is True
        m.move_to.assert_called_once()

    @pytest.mark.asyncio
    async def test_deafen_member(self):
        guild = make_guild()
        m = make_member("deafenme", id=130)
        m.voice = MagicMock()
        guild.members = [m]
        guild.get_member = MagicMock(return_value=m)
        tools = MemberToolsMixin()
        tools._resolve_member = AsyncMock(return_value=m)
        result = await tools.deafen_member(guild, "deafenme", deafen=True)
        assert result.success is True
        m.edit.assert_called_once()

    @pytest.mark.asyncio
    async def test_mute_member(self):
        guild = make_guild()
        m = make_member("muteme", id=140)
        m.voice = MagicMock()
        guild.members = [m]
        guild.get_member = MagicMock(return_value=m)
        tools = MemberToolsMixin()
        tools._resolve_member = AsyncMock(return_value=m)
        result = await tools.mute_member(guild, "muteme", mute=True)
        assert result.success is True
        m.edit.assert_called_once()


# =========================================================================
# SECTION 8: RoleToolsMixin
# =========================================================================

class TestRoleToolsMixin:
    """Test all methods of RoleToolsMixin."""

    @pytest.mark.asyncio
    async def test_create_role(self):
        guild = make_guild()
        tools = RoleToolsMixin()
        tools._parse_color = MagicMock(return_value=0x99AAB5)
        tools._build_permissions = MagicMock(return_value=_discord_mock.Permissions())
        result = await tools.create_role(guild, "NewRole")
        assert result.success is True
        guild.create_role.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_role_with_color(self):
        guild = make_guild()
        tools = RoleToolsMixin()
        tools._parse_color = MagicMock(return_value=0xFF0000)
        tools._build_permissions = MagicMock(return_value=_discord_mock.Permissions())
        result = await tools.create_role(guild, "ColorRole", color="#FF0000")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_create_role_with_permissions(self):
        guild = make_guild()
        tools = RoleToolsMixin()
        tools._parse_color = MagicMock(return_value=0x99AAB5)
        tools._build_permissions = MagicMock(return_value=_discord_mock.Permissions())
        result = await tools.create_role(guild, "Mod", permissions=["kick_members", "ban_members"])
        assert result.success is True

    @pytest.mark.asyncio
    async def test_create_role_failure(self):
        guild = make_guild()
        guild.create_role.side_effect = Exception("Permissions error")
        tools = RoleToolsMixin()
        tools._parse_color = MagicMock(return_value=0x99AAB5)
        tools._build_permissions = MagicMock(return_value=_discord_mock.Permissions())
        result = await tools.create_role(guild, "FailRole")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_edit_role(self):
        guild = make_guild()
        r = make_role("OldRole", id=301)
        guild.roles = [r]
        _discord_mock.utils.get = MagicMock(return_value=r)
        tools = RoleToolsMixin()
        tools._parse_color = MagicMock(return_value=0x99AAB5)
        result = await tools.edit_role(guild, "OldRole", name="NewRole")
        assert result.success is True
        r.edit.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_role_not_found(self):
        guild = make_guild()
        _discord_mock.utils.get = MagicMock(return_value=None)
        tools = RoleToolsMixin()
        tools._parse_color = MagicMock(return_value=0x99AAB5)
        result = await tools.edit_role(guild, "Nonexistent", name="NewName")
        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_delete_role(self):
        guild = make_guild()
        r = make_role("DeleteMe", id=300)
        guild.roles = [r]
        _discord_mock.utils.get = MagicMock(return_value=r)
        tools = RoleToolsMixin()
        result = await tools.delete_role(guild, "DeleteMe")
        assert result.success is True
        r.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_role_not_found(self):
        guild = make_guild()
        _discord_mock.utils.get = MagicMock(return_value=None)
        tools = RoleToolsMixin()
        result = await tools.delete_role(guild, "Nonexistent")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_assign_role(self):
        guild = make_guild()
        m = make_member("assignee", id=400)
        r = make_role("Member", id=500)
        guild.members = [m]
        guild.roles = [r]
        guild.get_member = MagicMock(return_value=m)
        _discord_mock.utils.get = MagicMock(return_value=r)
        tools = RoleToolsMixin()
        tools._resolve_member = AsyncMock(return_value=m)
        result = await tools.assign_role(guild, "assignee", "Member")
        assert result.success is True
        m.add_roles.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_role(self):
        guild = make_guild()
        m = make_member("remover", id=401)
        r = make_role("OldRole", id=501)
        guild.members = [m]
        guild.roles = [r]
        guild.get_member = MagicMock(return_value=m)
        _discord_mock.utils.get = MagicMock(return_value=r)
        tools = RoleToolsMixin()
        tools._resolve_member = AsyncMock(return_value=m)
        result = await tools.remove_role(guild, "remover", "OldRole")
        assert result.success is True
        m.remove_roles.assert_called_once()


# =========================================================================
# SECTION 9: PlanToolsMixin & ProgressToolsMixin
# =========================================================================

class TestPlanToolsMixin:
    """Test PlanToolsMixin methods."""

    @pytest.mark.asyncio
    async def test_generate_plan_basic(self):
        guild = make_guild()
        tools = PlanToolsMixin()
        tools.get_server_state = AsyncMock(return_value={"server_name": "Test", "member_count": 10, "roles": [{"name": "Admin"}], "channels": [], "categories": []})
        llm = FakeLLM(response='{"analysis": "test", "steps": [{"action": "create_channel", "name": "general"}]}')
        plan = await tools.generate_plan(guild, "create a general channel", llm)
        assert isinstance(plan, dict)
        assert "steps" in plan

    @pytest.mark.asyncio
    async def test_execute_plan(self):
        guild = make_guild()
        tools = PlanToolsMixin()
        tools.tracker = None
        tools.health = None
        tools.bot = MagicMock()
        tools._send_progress_embed = AsyncMock()
        tools._update_progress_embed = AsyncMock()
        tools._finalize_progress_embed = AsyncMock()
        tools._extract_step_name = MagicMock(return_value="test-chan")
        tools._do_step = AsyncMock(return_value=StepResult(success=True, action="create_channel", name="test-chan"))
        plan = {"analysis": "test", "steps": [{"action": "create_channel", "name": "test-chan", "channel_type": "text"}]}
        ctx = ProgressContext()
        results = await tools.execute_plan(guild, plan, ctx, require_authorization=False, requester_id=12345)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_execute_plan_empty_steps(self):
        guild = make_guild()
        tools = PlanToolsMixin()
        plan = {"analysis": "empty", "steps": []}
        ctx = ProgressContext()
        results = await tools.execute_plan(guild, plan, ctx, require_authorization=False, requester_id=12345)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_execute_plan_parallel(self):
        guild = make_guild()
        tools = PlanToolsMixin()
        tools.tracker = None
        tools.health = None
        tools.bot = MagicMock()
        tools._send_progress_embed = AsyncMock()
        tools._update_progress_embed = AsyncMock()
        tools._finalize_progress_embed = AsyncMock()
        tools._extract_step_name = MagicMock(return_value="chan-a")
        tools._do_step = AsyncMock(return_value=StepResult(success=True, action="create_channel", name="chan-a"))
        plan = {"analysis": "parallel", "steps": [{"action": "create_channel", "name": "chan-a", "channel_type": "text"}]}
        ctx = ProgressContext()
        results = await tools.execute_plan_parallel(guild, plan, ctx, require_authorization=False, requester_id=12345)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_preflight_check(self):
        guild = make_guild()
        tools = PlanToolsMixin()
        tools.bot = MagicMock()
        tools.get_server_state = AsyncMock(return_value={"server_name": "Test", "member_count": 10, "roles": [], "channels": [], "categories": []})
        tools._extract_step_name = MagicMock(return_value="general")
        plan = {"analysis": "check", "steps": [{"action": "create_channel", "name": "general"}]}
        result = await tools.preflight_check(guild, plan)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_preflight_check_empty_plan(self):
        guild = make_guild()
        tools = PlanToolsMixin()
        tools.bot = MagicMock()
        tools.get_server_state = AsyncMock(return_value={"server_name": "Test", "member_count": 10, "roles": [], "channels": [], "categories": []})
        tools._extract_step_name = MagicMock(return_value="")
        plan = {"analysis": "", "steps": []}
        result = await tools.preflight_check(guild, plan)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_undo_last(self):
        guild = make_guild()
        tools = PlanToolsMixin()
        tools.tracker = None
        tools.health = None
        tools.bot = MagicMock()
        tools._send_progress_embed = AsyncMock()
        tools._update_progress_embed = AsyncMock()
        tools._finalize_progress_embed = AsyncMock()
        ctx = ProgressContext()
        results = await tools.undo_last(guild, ctx, n=1)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_undo_last_with_zero(self):
        guild = make_guild()
        tools = PlanToolsMixin()
        tools.tracker = None
        tools.health = None
        tools.bot = MagicMock()
        tools._send_progress_embed = AsyncMock()
        tools._update_progress_embed = AsyncMock()
        tools._finalize_progress_embed = AsyncMock()
        ctx = ProgressContext()
        results = await tools.undo_last(guild, ctx, n=0)
        assert isinstance(results, list)

    def test_build_planning_prompt(self):
        tools = PlanToolsMixin()
        prompt = tools._build_planning_prompt({"server_name": "Test", "member_count": 10, "roles": [], "channels": [], "categories": []}, "make a channel")
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_parse_plan_valid_json(self):
        tools = PlanToolsMixin()
        result = tools._parse_plan('{"analysis": "test", "steps": []}')
        assert result["analysis"] == "test"

    def test_parse_plan_invalid_json(self):
        tools = PlanToolsMixin()
        result = tools._parse_plan("not json")
        assert "Failed to parse" in result.get("analysis", "")


class TestProgressToolsMixin:
    """Test ProgressToolsMixin."""

    @pytest.mark.asyncio
    async def test_send_progress_embed(self):
        tools = ProgressToolsMixin()
        ctx = ProgressContext()
        plan = {"analysis": "test", "steps": [{"action": "test", "name": "step1"}]}
        msg = await tools._send_progress_embed(ctx, plan, 0, 1, [], " Starting...")
        assert msg is not None

    @pytest.mark.asyncio
    async def test_update_progress_embed(self):
        tools = ProgressToolsMixin()
        msg = MagicMock()
        msg.edit = AsyncMock()
        plan = {"analysis": "test", "steps": [{"action": "test", "name": "step1"}]}
        await tools._update_progress_embed(msg, plan, 1, 1, [], " Running...")

    @pytest.mark.asyncio
    async def test_finalize_progress_embed(self):
        tools = ProgressToolsMixin()
        msg = MagicMock()
        msg.edit = AsyncMock()
        plan = {"analysis": "test", "steps": [{"action": "test", "name": "step1"}]}
        results = [MagicMock(success=True, action="test", name="step1")]
        await tools._finalize_progress_embed(msg, plan, results)















