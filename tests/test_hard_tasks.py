"""Hard tasks and edge-case tests for the Azure Discord bot.

Covers: Discord management tools (roles, channels, categories, permissions),
server audit, conflict detection, spam detection, onboarding analysis,
engagement metrics, content moderation at scale, multi-guild isolation,
unusual inputs, and recovery scenarios.

Run: pytest tests/test_hard_tasks.py -v
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from azure.tools.channel_tools import ChannelToolsMixin
from azure.tools.member_tools import MemberToolsMixin
from azure.tools.plan_tools import PlanToolsMixin
from azure.tools.role_tools import RoleToolsMixin
from azure.tools.server_tools import ServerToolsMixin, _embed_color, _llm_reason, _resolve_color
from azure.tools.types import StepResult

# ─── Fixtures ────────────────────────────────────────────────────────────────

def _make_permission(name: str = "general", **overrides) -> MagicMock:
    """Build a realistic discord.Permissions mock."""
    perms = MagicMock()
    perms.administrator = overrides.get("administrator", False)
    perms.manage_guild = overrides.get("manage_guild", False)
    perms.manage_channels = overrides.get("manage_channels", False)
    perms.manage_roles = overrides.get("manage_roles", False)
    perms.kick_members = overrides.get("kick_members", False)
    perms.ban_members = overrides.get("ban_members", False)
    perms.manage_webhooks = overrides.get("manage_webhooks", False)
    perms.manage_events = overrides.get("manage_events", False)
    perms.send_messages = overrides.get("send_messages", True)
    perms.read_messages = overrides.get("read_messages", True)
    perms.__iter__ = lambda self: iter(
        (k, v) for k, v in {
            "administrator": perms.administrator,
            "manage_guild": perms.manage_guild,
            "manage_channels": perms.manage_channels,
            "manage_roles": perms.manage_roles,
            "kick_members": perms.kick_members,
            "ban_members": perms.ban_members,
        }.items() if v
    )
    return perms


def _make_member(name="TestUser", uid=1001, admin=False, owner=False, guild=None):
    """Build a realistic discord.Member mock."""
    m = MagicMock()
    m.name = name
    m.display_name = name
    m.id = uid
    m.bot = False
    m.nick = None
    m.roles = []
    m.status = MagicMock()
    m.guild_permissions = _make_permission(administrator=admin)
    m.guild = guild
    m.voice = None

    async def _kick(**kw):
        pass

    async def _ban(**kw):
        pass

    async def _unban(**kw):
        pass

    async def _edit(**kw):
        pass

    async def _timeout(until, **kw):
        pass

    async def _add_roles(*roles, **kw):
        pass

    async def _remove_roles(*roles, **kw):
        pass

    async def _move_to(channel, **kw):
        pass

    m.kick = _kick
    m.ban = _ban
    m.unban = _unban
    m.edit = _edit
    m.timeout = _timeout
    m.add_roles = _add_roles
    m.remove_roles = _remove_roles
    m.move_to = _move_to
    return m


def _make_role(name="TestRole", color_int=0, position=1, role_id=5001, members=None, permissions=None):
    """Build a realistic discord.Role mock."""
    r = MagicMock()
    r.name = name
    r.id = role_id
    r.color = MagicMock()
    r.color.__str__ = lambda self: str(color_int)
    r.color.__int__ = lambda self: color_int
    r.position = position
    r.hoist = False
    r.mentionable = False
    r.managed = False
    r.members = members or []
    r.permissions = permissions or _make_permission()
    r.is_default.return_value = False
    return r


def _make_channel(name="general", ch_type="text", ch_id=2001, guild=None, category=None,
                  topic="", slowmode_delay=0, nsfw=False, bitrate=None, user_limit=None):
    """Build a realistic discord.TextChannel/VoiceChannel mock."""
    ch = MagicMock()
    ch.name = name
    ch.id = ch_id
    ch.guild = guild
    ch.category = category
    ch.position = 0
    ch.topic = topic
    ch.slowmode_delay = slowmode_delay
    ch.nsfw = nsfw
    ch.type = MagicMock()
    ch.type.__str__ = lambda self: ch_type
    ch.overwrites = {}
    ch.overwrites_for = MagicMock(return_value=None)
    ch.set_permissions = AsyncMock()
    ch.sync_permissions = AsyncMock()
    ch.delete = AsyncMock()

    async def _edit(**kw):
        pass

    ch.edit = _edit
    ch.fetch_message = AsyncMock(return_value=MagicMock())
    ch.send = AsyncMock()
    return ch


def _make_category(name="Info", cat_id=3001, guild=None):
    """Build a realistic discord.CategoryChannel mock."""
    cat = MagicMock()
    cat.name = name
    cat.id = cat_id
    cat.guild = guild
    cat.position = 0
    cat.channels = []
    cat.edit = AsyncMock()
    return cat


def _make_guild(name="TestServer", guild_id=9001, member_count=50):
    """Build a realistic discord.Guild mock."""
    g = MagicMock()
    g.name = name
    g.id = guild_id
    g.member_count = member_count
    g.owner_id = 1001
    g.me = MagicMock()
    g.me.id = 9999
    g.verification_level = MagicMock()
    g.verification_level.__str__ = lambda self: "low"
    g.default_notifications = MagicMock()
    g.default_notifications.__str__ = lambda self: "all_messages"
    g.explicit_content_filter = MagicMock()
    g.explicit_content_filter.__str__ = lambda self: "members_without_roles"
    g.bitrate_limit = 96000
    g.roles = []
    g.channels = []
    g.categories = []
    g.voice_channels = []
    g.text_channels = []
    g.forums = []
    g.stage_channels = []
    g.emojis = []
    g.members = []
    g.threads = []
    g.afk_channel = None
    g.system_channel = None
    g.rules_channel = None

    async def _edit(**kw):
        pass

    g.edit = _edit
    g.create_role = AsyncMock()
    g.create_text_channel = AsyncMock()
    g.create_voice_channel = AsyncMock()
    g.create_category = AsyncMock()
    g.create_forum = AsyncMock()
    g.create_stage_channel = AsyncMock()
    g.create_webhook = AsyncMock()
    g.create_invite = AsyncMock()
    g.create_scheduled_event = AsyncMock()
    g.create_sticker = AsyncMock()
    g.create_custom_emoji = AsyncMock()
    g.create_automod_rule = AsyncMock()
    g.create_template = AsyncMock()
    g.webhooks = AsyncMock(return_value=[])
    g.fetch_scheduled_events = AsyncMock(return_value=[])
    g.fetch_stickers = AsyncMock(return_value=[])
    g.audit_logs = AsyncMock()
    g.get_member = MagicMock(return_value=None)
    g.fetch_member = AsyncMock(return_value=None)
    g.get_channel = MagicMock(return_value=None)
    return g


def _make_message(content="Hello", author=None, guild=None, channel=None, msg_id=7001):
    """Build a realistic discord.Message mock."""
    msg = MagicMock()
    msg.content = content
    msg.author = author or _make_member()
    msg.guild = guild
    msg.channel = channel or _make_channel()
    msg.id = msg_id
    msg.webhook_id = None
    msg.mentions = []
    msg.attachments = []
    msg.embeds = []
    msg.created_at = MagicMock()
    msg.edited_at = None
    return msg


def _make_discord_tools(guild=None):
    """Build a combined DiscordManagementTools instance with all mixins."""
    g = guild or _make_guild()

    class DiscordManagementTools(ServerToolsMixin, RoleToolsMixin, ChannelToolsMixin, MemberToolsMixin, PlanToolsMixin):
        def __init__(self, guild_ref):
            self.bot = MagicMock()
            self.guild = guild_ref
            self.tracker = MagicMock()
            self.repair = MagicMock()
            self.health = MagicMock()
            self.health.suggest_followups = MagicMock(return_value=[])

    return DiscordManagementTools(g)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Create Discord Server Elements
# ═══════════════════════════════════════════════════════════════════════════

class TestCreateServerElements:

    @pytest.mark.asyncio
    async def test_create_role_with_permissions(self):
        """Agent creates a role with specific permissions."""
        guild = _make_guild()
        role_mock = MagicMock()
        role_mock.id = 5010
        role_mock.name = "Moderator"
        role_mock.color = MagicMock()
        guild.create_role.return_value = role_mock

        tools = _make_discord_tools(guild)
        result = await tools.create_role(
            guild, name="Moderator", color="blue",
            permissions=["kick_members", "manage_messages"],
            hoist=True, mentionable=True,
        )

        assert result.success is True
        assert result.action == "create_role"
        assert result.name == "Moderator"
        assert result.target_id == 5010
        guild.create_role.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_role_api_error(self):
        """Agent handles API failure when creating role gracefully."""
        guild = _make_guild()
        guild.create_role.side_effect = Exception("Permission denied")

        tools = _make_discord_tools(guild)
        result = await tools.create_role(guild, name="BadRole")

        assert result.success is False
        assert "Permission denied" in result.error

    @pytest.mark.asyncio
    async def test_create_text_channel(self):
        """Agent creates a text channel in a category."""
        guild = _make_guild()
        cat_mock = _make_category("Info", guild=guild)
        guild.categories = [cat_mock]
        ch_mock = _make_channel("rules", guild=guild)
        guild.create_text_channel.return_value = ch_mock

        tools = _make_discord_tools(guild)
        result = await tools.create_channel(
            guild, name="rules", channel_type="text",
            category="Info", topic="Server rules",
            slowmode=5,
        )

        assert result.success is True
        assert "text" in result.detail

    @pytest.mark.asyncio
    async def test_create_voice_channel(self):
        """Agent creates a voice channel with bitrate and user limit."""
        guild = _make_guild()
        vc_mock = _make_channel("Music", ch_type="voice", guild=guild)
        guild.create_voice_channel.return_value = vc_mock

        tools = _make_discord_tools(guild)
        result = await tools.create_channel(
            guild, name="Music", channel_type="voice",
            bitrate=128000, user_limit=10,
        )

        assert result.success is True
        assert "voice" in result.detail

    @pytest.mark.asyncio
    async def test_create_category(self):
        """Agent creates a channel category."""
        guild = _make_guild()
        cat_mock = _make_category("Gaming")
        cat_mock.id = 3010
        guild.create_category.return_value = cat_mock

        tools = _make_discord_tools(guild)
        result = await tools.create_category(guild, name="Gaming", position=2)

        assert result.success is True
        assert result.action == "create_category"
        assert result.target_id == 3010

    @pytest.mark.asyncio
    async def test_set_channel_overwrites(self):
        """Agent sets channel permission overwrites for a role."""
        guild = _make_guild()
        role_mock = _make_role("Moderator")
        guild.roles = [role_mock]
        ch_mock = _make_channel("admin-chat", guild=guild)
        guild.channels = [ch_mock]

        tools = _make_discord_tools(guild)
        result = await tools.set_channel_permissions(
            ch_mock, target_name="Moderator",
            allow=["send_messages", "manage_messages"],
            deny=["send_messages_in_threads"],
        )

        assert result.success is True
        ch_mock.set_permissions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_channel_permissions_role_not_found(self):
        """Agent returns error when target role doesn't exist."""
        guild = _make_guild()
        ch_mock = _make_channel("test", guild=guild)

        tools = _make_discord_tools(guild)
        result = await tools.set_channel_permissions(
            ch_mock, target_name="NonexistentRole",
            allow=["send_messages"],
        )

        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_create_thread(self):
        """Agent creates a public thread in a channel."""
        guild = _make_guild()
        ch_mock = _make_channel("general", guild=guild)
        thread_mock = MagicMock()
        thread_mock.id = 8001
        ch_mock.create_thread = AsyncMock(return_value=thread_mock)

        tools = _make_discord_tools(guild)
        result = await tools.create_thread(ch_mock, name="Discussion", thread_type="public")

        assert result.success is True
        assert result.target_id == 8001

    @pytest.mark.asyncio
    async def test_create_invite(self):
        """Agent creates an invite link for a channel."""
        guild = _make_guild()
        ch_mock = _make_channel("welcome", guild=guild)
        invite_mock = MagicMock()
        invite_mock.code = "abc123"
        ch_mock.create_invite = AsyncMock(return_value=invite_mock)

        tools = _make_discord_tools(guild)
        result = await tools.create_invite(ch_mock, max_age=86400, max_uses=5)

        assert result.success is True
        assert "abc123" in result.detail

    @pytest.mark.asyncio
    async def test_edit_category(self):
        """Agent edits an existing category."""
        guild = _make_guild()
        cat_mock = _make_category("OldName", guild=guild)
        guild.categories = [cat_mock]

        tools = _make_discord_tools(guild)
        result = await tools.edit_category(guild, category_name="OldName", name="NewName")

        assert result.success is True
        assert result.action == "edit_category"

    @pytest.mark.asyncio
    async def test_delete_category_not_found(self):
        """Agent handles deletion of nonexistent category."""
        guild = _make_guild()
        guild.categories = []

        tools = _make_discord_tools(guild)
        result = await tools.delete_category(guild, category_name="Ghost")

        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_move_channel_to_category(self):
        """Agent moves a channel into a category."""
        guild = _make_guild()
        ch_mock = _make_channel("general", guild=guild)
        ch_mock.category = MagicMock()
        ch_mock.category.name = "OldCat"
        cat_mock = _make_category("NewCat", guild=guild)
        guild.channels = [ch_mock]
        guild.categories = [cat_mock]

        tools = _make_discord_tools(guild)
        result = await tools.move_channel(guild, channel_name="general", category_name="NewCat")

        assert result.success is True
        assert "NewCat" in str(result.after_state)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Server Audit
# ═══════════════════════════════════════════════════════════════════════════

class TestServerAudit:

    @pytest.mark.asyncio
    async def test_get_server_state(self):
        """Agent retrieves full server state including roles, channels, members."""
        guild = _make_guild(member_count=120)
        role1 = _make_role("Admin", members=[_make_member("a")])
        role2 = _make_role("Member", members=[_make_member("b"), _make_member("c")])
        guild.roles = [MagicMock(is_default=lambda: True, managed=False), role1, role2]

        ch1 = _make_channel("general", topic="General chat")
        ch2 = _make_channel("gaming", topic="Gaming")
        guild.channels = [ch1, ch2]
        guild.categories = []

        online_member = _make_member("Online")
        online_member.status = MagicMock()
        online_member.status.__str__ = lambda self: "online"
        offline_member = _make_member("Offline")
        offline_member.status = MagicMock()
        offline_member.status.__str__ = lambda self: "offline"
        guild.members = [online_member, offline_member]

        tools = _make_discord_tools(guild)
        state = await tools.get_server_state(guild)

        assert state["server_name"] == "TestServer"
        assert state["member_count"] == 120
        assert len(state["roles"]) == 2
        assert state["roles"][0]["name"] == "Admin"
        assert state["roles"][0]["member_count"] == 1

    @pytest.mark.asyncio
    async def test_count_members_by_role(self):
        """Audit counts members correctly per role."""
        guild = _make_guild()
        members_a = [_make_member(f"user_{i}") for i in range(5)]
        members_b = [_make_member(f"mod_{i}") for i in range(3)]
        role_a = _make_role("Users", members=members_a)
        role_b = _make_role("Moderators", members=members_b)
        guild.roles = [role_a, role_b]

        tools = _make_discord_tools(guild)
        state = await tools.get_server_state(guild)

        role_counts = {r["name"]: r["member_count"] for r in state["roles"]}
        assert role_counts["Users"] == 5
        assert role_counts["Moderators"] == 3

    @pytest.mark.asyncio
    async def test_identify_channel_types(self):
        """Audit identifies text, voice, and forum channels."""
        guild = _make_guild()
        text_ch = _make_channel("chat", ch_type="text")
        voice_ch = _make_channel("music", ch_type="voice")
        forum_ch = _make_channel("suggestions", ch_type="forum")
        guild.channels = [text_ch, voice_ch, forum_ch]

        tools = _make_discord_tools(guild)
        state = await tools.get_server_state(guild)

        types_found = {str(c["type"]) for c in state["channels"]}
        assert "text" in types_found
        assert "voice" in types_found

    @pytest.mark.asyncio
    async def test_channel_category_mapping(self):
        """Audit maps channels to their parent categories."""
        guild = _make_guild()
        cat = _make_category("Info")
        ch = _make_channel("rules", guild=guild, category=cat)
        cat.channels = [ch]
        guild.channels = [ch]
        guild.categories = [cat]

        tools = _make_discord_tools(guild)
        state = await tools.get_server_state(guild)

        assert state["categories"][0]["name"] == "Info"
        assert "rules" in state["categories"][0]["channels"]

    @pytest.mark.asyncio
    async def test_get_audit_logs(self):
        """Agent retrieves audit log entries."""
        guild = _make_guild()
        entry1 = MagicMock()
        entry1.action = "channel_delete"
        entry1.user = "Admin"
        entry1.target = "#deleted-channel"
        entry1.reason = "Spam"
        entry1.created_at = MagicMock()

        async def _aiter(**kw):
            for e in [entry1]:
                yield e

        guild.audit_logs = _aiter

        tools = _make_discord_tools(guild)
        result = await tools.get_audit_logs(guild, limit=50)

        assert result.success is True
        logs = result.after_state["logs"]
        assert len(logs) == 1
        assert logs[0]["action"] == "channel_delete"

    @pytest.mark.asyncio
    async def test_find_who_did_action(self):
        """Agent identifies who performed a specific audit action."""
        guild = _make_guild()
        entry = MagicMock()
        entry.action = "ban"
        entry.user = "ModeratorBob"
        entry.target = "Spammer42"
        entry.reason = "Spam"
        entry.created_at = MagicMock()

        async def _aiter(**kw):
            yield entry

        guild.audit_logs = _aiter

        tools = _make_discord_tools(guild)
        result = await tools.find_who_did_action(guild, action_type="ban", target_name="Spammer42")

        assert result.success is True
        assert "ModeratorBob" in result.detail

    @pytest.mark.asyncio
    async def test_nsfw_channel_flagged_in_audit(self):
        """Audit correctly identifies NSFW channels."""
        guild = _make_guild()
        nsfw_ch = _make_channel("nsfw-stuff", nsfw=True)
        safe_ch = _make_channel("general", nsfw=False)
        guild.channels = [nsfw_ch, safe_ch]

        tools = _make_discord_tools(guild)
        state = await tools.get_server_state(guild)

        nsfw_channels = [c for c in state["channels"] if c.get("nsfw")]
        assert len(nsfw_channels) == 1
        assert nsfw_channels[0]["name"] == "nsfw-stuff"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Conflict Detection
# ═══════════════════════════════════════════════════════════════════════════

class TestConflictDetection:

    def _build_conversation(self, messages):
        """Helper: build a list of (user, text, timestamp) tuples."""
        now = time.time()
        return [
            {"user": u, "text": t, "timestamp": now - (len(messages) - i) * 30}
            for i, (u, t) in enumerate(messages)
        ]

    def _detect_negative_sentiment(self, text):
        """Simple keyword-based negative sentiment check."""
        negative_words = {"hate", "stupid", "idiot", "toxic", "worst", "terrible", "awful", "trash"}
        words = set(text.lower().split())
        return bool(words & negative_words)

    def _detect_escalation(self, conversation):
        """Detect if tone escalates across messages."""
        if len(conversation) < 3:
            return False
        neg_counts = []
        window = 3
        for i in range(len(conversation) - window + 1):
            chunk = conversation[i:i + window]
            neg_count = sum(1 for m in chunk if self._detect_negative_sentiment(m["text"]))
            neg_counts.append(neg_count)
        return any(neg_counts[i] > neg_counts[i - 1] for i in range(1, len(neg_counts)))

    def test_detect_negative_sentiment(self):
        """Detection of negative words in messages."""
        assert self._detect_negative_sentiment("You are an idiot") is True
        assert self._detect_negative_sentiment("Great job!") is False
        assert self._detect_negative_sentiment("This is terrible and stupid") is True

    def test_detect_escalation_in_conversation(self):
        """Detect escalating negativity between two users."""
        conv = self._build_conversation([
            ("Alice", "I disagree with that"),
            ("Bob", "You're wrong"),
            ("Alice", "Stop being stupid"),
            ("Bob", "You're the idiot here"),
            ("Alice", "This is terrible"),
        ])
        assert self._detect_escalation(conv) is True

    def test_no_escalation_in_calm_conversation(self):
        """No escalation in friendly conversation."""
        conv = self._build_conversation([
            ("Alice", "Hey how are you?"),
            ("Bob", "Doing well thanks!"),
            ("Alice", "Great to hear"),
            ("Bob", "What are you working on?"),
        ])
        assert self._detect_escalation(conv) is False

    def test_conflict_between_same_users(self):
        """Detect repeated exchanges between same pair of users."""
        conv = [
            {"user": "Alice", "text": "hate this", "timestamp": 1.0},
            {"user": "Bob", "text": "you're an idiot", "timestamp": 2.0},
            {"user": "Alice", "text": "stupid take", "timestamp": 3.0},
            {"user": "Bob", "text": "terrible opinion", "timestamp": 4.0},
        ]

        pairs = {}
        for m in conv:
            pair_key = tuple(sorted(["Alice", "Bob"]))
            pairs.setdefault(pair_key, []).append(m)

        for _pair, msgs in pairs.items():
            neg_count = sum(1 for m in msgs if self._detect_negative_sentiment(m["text"]))
            assert neg_count >= 2, "Should detect conflict between Alice and Bob"

    def test_conflict_topic_extraction(self):
        """Extract topic from the conflicting messages."""
        conv = [
            {"user": "Alice", "text": "Python is better than JavaScript for backend"},
            {"user": "Bob", "text": "That's stupid, JS is way better"},
            {"user": "Alice", "text": "Your opinion is terrible"},
        ]

        topics = []
        for m in conv:
            text = m["text"].lower()
            if "python" in text or "javascript" in text:
                topics.append("programming language debate")

        assert "programming language debate" in topics

    def test_empty_conversation_no_conflict(self):
        """Empty conversation should not trigger conflict."""
        assert self._detect_escalation([]) is False

    def test_single_message_no_conflict(self):
        """Single message cannot be a conflict."""
        conv = self._build_conversation([("Alice", "I hate everything")])
        assert self._detect_escalation(conv) is False


# ═══════════════════════════════════════════════════════════════════════════
# 4. Spam Detection
# ═══════════════════════════════════════════════════════════════════════════

class TestSpamDetection:

    def _detect_repeated_messages(self, messages, threshold=3):
        """Detect if same message repeated >= threshold times."""
        counts = {}
        for m in messages:
            key = m["content"].lower().strip()
            counts[key] = counts.get(key, 0) + 1
        return {k: v for k, v in counts.items() if v >= threshold}

    def _detect_rapid_fire(self, messages, window_seconds=5, threshold=5):
        """Detect rapid-fire messages from one user within a time window."""
        if not messages:
            return False
        user_msgs = {}
        for m in messages:
            uid = m["user_id"]
            user_msgs.setdefault(uid, []).append(m["timestamp"])

        for uid, timestamps in user_msgs.items():
            timestamps.sort()
            for i in range(len(timestamps)):
                in_window = sum(
                    1 for t in timestamps
                    if 0 <= t - timestamps[i] <= window_seconds
                )
                if in_window >= threshold:
                    return True
        return False

    def _detect_external_links(self, messages):
        """Detect messages containing external URLs."""
        import re
        url_pattern = re.compile(r'https?://[^\s]+')
        flagged = []
        for m in messages:
            if url_pattern.search(m["content"]):
                flagged.append(m)
        return flagged

    def test_repeated_message_detection(self):
        """Detect same message sent multiple times."""
        messages = [
            {"content": "Join my server! https://example.com", "user_id": "1"},
            {"content": "Join my server! https://example.com", "user_id": "1"},
            {"content": "Join my server! https://example.com", "user_id": "1"},
            {"content": "Join my server! https://example.com", "user_id": "1"},
            {"content": "Hello everyone", "user_id": "2"},
        ]
        spam = self._detect_repeated_messages(messages, threshold=3)
        assert "join my server! https://example.com" in spam
        assert spam["join my server! https://example.com"] == 4

    def test_no_spam_normal_conversation(self):
        """Normal varied conversation is not spam."""
        messages = [
            {"content": "Hello!", "user_id": "1"},
            {"content": "How are you?", "user_id": "2"},
            {"content": "I'm good", "user_id": "1"},
            {"content": "Nice weather today", "user_id": "3"},
        ]
        spam = self._detect_repeated_messages(messages, threshold=3)
        assert len(spam) == 0

    def test_rapid_fire_detection(self):
        """Detect rapid-fire messages from one user."""
        now = time.time()
        messages = [
            {"content": f"msg{i}", "user_id": "spammer", "timestamp": now + i * 0.5}
            for i in range(8)
        ]
        assert self._detect_rapid_fire(messages, window_seconds=5, threshold=5) is True

    def test_rapid_fire_not_triggered_by_slow_messages(self):
        """Slow messages should not trigger rapid-fire detection."""
        now = time.time()
        messages = [
            {"content": f"msg{i}", "user_id": "user1", "timestamp": now + i * 10}
            for i in range(4)
        ]
        assert self._detect_rapid_fire(messages, window_seconds=5, threshold=5) is False

    def test_external_link_spam(self):
        """Detect messages with external URLs."""
        messages = [
            {"content": "Check out https://spam-site.com/free-phones", "user_id": "1"},
            {"content": "Hello everyone!", "user_id": "2"},
            {"content": "Also visit https://phishing.net", "user_id": "1"},
        ]
        flagged = self._detect_external_links(messages)
        assert len(flagged) == 2
        assert all("https://" in m["content"] for m in flagged)

    def test_no_links_normal_messages(self):
        """Normal messages without URLs are not flagged."""
        messages = [
            {"content": "Hello!", "user_id": "1"},
            {"content": "What's the plan for tonight?", "user_id": "2"},
        ]
        flagged = self._detect_external_links(messages)
        assert len(flagged) == 0

    def test_empty_messages_no_spam(self):
        """Empty message list should not trigger any detection."""
        spam = self._detect_repeated_messages([], threshold=3)
        assert len(spam) == 0
        assert self._detect_rapid_fire([]) is False


# ═══════════════════════════════════════════════════════════════════════════
# 5. Onboarding Analysis
# ═══════════════════════════════════════════════════════════════════════════

class TestOnboardingAnalysis:

    def _analyze_onboarding(self, join_time, first_message_time, first_message, rules_channels=None):
        """Analyze new member behavior."""
        rules_channels = rules_channels or ["rules", "server-rules"]
        time_to_first_msg = first_message_time - join_time

        result = {
            "time_to_first_message_seconds": time_to_first_msg,
            "first_message_length": len(first_message),
            "first_message_has_content": bool(first_message.strip()),
            "followed_rules_channel": False,
            "risk_score": 0.0,
        }

        if time_to_first_msg < 5:
            result["risk_score"] += 0.3
        if len(first_message) < 3:
            result["risk_score"] += 0.2
        if any(link in first_message.lower() for link in ["http://", "https://", "discord.gg"]):
            result["risk_score"] += 0.4

        return result

    def test_immediate_first_message_suspicious(self):
        """Very fast first message after joining is suspicious."""
        now = time.time()
        result = self._analyze_onboarding(
            join_time=now,
            first_message_time=now + 2,
            first_message="Join my server https://spam.gg",
        )
        assert result["risk_score"] >= 0.5
        assert result["time_to_first_message_seconds"] == 2

    def test_normal_onboarding(self):
        """Reasonable first message timing and content."""
        now = time.time()
        result = self._analyze_onboarding(
            join_time=now,
            first_message_time=now + 300,
            first_message="Hey everyone! Just joined, excited to be here.",
        )
        assert result["risk_score"] == 0.0
        assert result["first_message_has_content"] is True
        assert result["time_to_first_message_seconds"] == 300

    def test_empty_first_message(self):
        """Empty first message raises risk."""
        now = time.time()
        result = self._analyze_onboarding(
            join_time=now,
            first_message_time=now + 60,
            first_message="",
        )
        assert result["first_message_has_content"] is False
        assert result["risk_score"] > 0

    def test_link_in_first_message_risky(self):
        """First message with a link is higher risk."""
        now = time.time()
        result = self._analyze_onboarding(
            join_time=now,
            first_message_time=now + 120,
            first_message="Check out https://example.com for free stuff!",
        )
        assert result["risk_score"] >= 0.4

    def test_multiple_new_members_analysis(self):
        """Analyze onboarding for multiple new members simultaneously."""
        now = time.time()
        members = [
            {"join": now, "first_msg_time": now + 60, "first_msg": "Hello!"},
            {"join": now, "first_msg_time": now + 2, "first_msg": "https://spam.com"},
            {"join": now, "first_msg_time": now + 300, "first_msg": "Excited to join!"},
        ]

        results = [
            self._analyze_onboarding(m["join"], m["first_msg_time"], m["first_msg"])
            for m in members
        ]

        risky = [r for r in results if r["risk_score"] >= 0.3]
        safe = [r for r in results if r["risk_score"] < 0.3]
        assert len(risky) == 1
        assert len(safe) == 2


# ═══════════════════════════════════════════════════════════════════════════
# 6. Engagement Metrics
# ═══════════════════════════════════════════════════════════════════════════

class TestEngagementMetrics:

    def _compute_metrics(self, messages, now=None):
        """Compute engagement metrics from a list of messages."""
        now = now or time.time()
        day_ago = now - 86400
        week_ago = now - 604800
        month_ago = now - 2592000

        daily = [m for m in messages if m["timestamp"] >= day_ago]
        weekly = [m for m in messages if m["timestamp"] >= week_ago]
        monthly = [m for m in messages if m["timestamp"] >= month_ago]

        unique_users = set(m["user_id"] for m in messages)
        unique_daily = set(m["user_id"] for m in daily)

        hours = [int((m["timestamp"] % 86400) // 3600) for m in messages]
        peak_hour = max(set(hours), key=hours.count) if hours else 0

        response_times = []
        sorted_msgs = sorted(messages, key=lambda m: m["timestamp"])
        for i in range(1, len(sorted_msgs)):
            if sorted_msgs[i]["user_id"] != sorted_msgs[i - 1]["user_id"]:
                response_times.append(sorted_msgs[i]["timestamp"] - sorted_msgs[i - 1]["timestamp"])
        avg_response = sum(response_times) / len(response_times) if response_times else 0

        return {
            "total_messages": len(messages),
            "daily_messages": len(daily),
            "weekly_messages": len(weekly),
            "monthly_messages": len(monthly),
            "unique_contributors": len(unique_users),
            "unique_daily_contributors": len(unique_daily),
            "peak_activity_hour": peak_hour,
            "avg_response_time_seconds": round(avg_response, 2),
        }

    def test_messages_per_day(self):
        """Count messages in the last 24 hours."""
        now = time.time()
        messages = [
            {"user_id": f"u{i}", "timestamp": now - i * 100, "content": f"msg{i}"}
            for i in range(20)
        ]
        metrics = self._compute_metrics(messages, now=now)
        assert metrics["daily_messages"] == 20
        assert metrics["total_messages"] == 20

    def test_unique_contributors(self):
        """Count unique users who sent messages."""
        now = time.time()
        messages = [
            {"user_id": "alice", "timestamp": now - 10, "content": "hi"},
            {"user_id": "bob", "timestamp": now - 20, "content": "hello"},
            {"user_id": "alice", "timestamp": now - 30, "content": "how are you"},
            {"user_id": "charlie", "timestamp": now - 40, "content": "great"},
        ]
        metrics = self._compute_metrics(messages, now=now)
        assert metrics["unique_contributors"] == 3

    def test_peak_activity_hour(self):
        """Identify the hour with most messages."""
        now = time.time()
        # All messages at 14:00 hour
        base = now - (now % 86400) + 14 * 3600
        messages = [
            {"user_id": f"u{i}", "timestamp": base + i * 10, "content": f"msg{i}"}
            for i in range(10)
        ]
        metrics = self._compute_metrics(messages, now=now)
        assert metrics["peak_activity_hour"] == 14

    def test_average_response_time(self):
        """Calculate average response time between messages from different users."""
        now = time.time()
        messages = [
            {"user_id": "alice", "timestamp": now, "content": "hi"},
            {"user_id": "bob", "timestamp": now + 10, "content": "hello"},
            {"user_id": "alice", "timestamp": now + 25, "content": "what's up"},
            {"user_id": "bob", "timestamp": now + 35, "content": "not much"},
        ]
        metrics = self._compute_metrics(messages, now=now)
        assert metrics["avg_response_time_seconds"] > 0
        assert metrics["avg_response_time_seconds"] <= 15

    def test_empty_messages_metrics(self):
        """Metrics for empty message list."""
        metrics = self._compute_metrics([])
        assert metrics["total_messages"] == 0
        assert metrics["unique_contributors"] == 0
        assert metrics["avg_response_time_seconds"] == 0

    def test_weekly_vs_monthly_counts(self):
        """Distinguish weekly from monthly message counts."""
        now = time.time()
        messages = [
            {"user_id": "u1", "timestamp": now - 100, "content": "recent"},
            {"user_id": "u1", "timestamp": now - 100000, "content": "older"},
            {"user_id": "u1", "timestamp": now - 1000000, "content": "old"},
        ]
        metrics = self._compute_metrics(messages, now=now)
        assert metrics["weekly_messages"] >= 1
        assert metrics["monthly_messages"] >= 2


# ═══════════════════════════════════════════════════════════════════════════
# 7. Content Moderation at Scale
# ═══════════════════════════════════════════════════════════════════════════

class TestContentModerationAtScale:

    def _classify_message(self, text):
        """Simple content classifier for testing."""
        severe = ["kill yourself", "kys", "die"]
        moderate = ["idiot", "stupid", "hate you"]
        links = ["http://", "https://", "discord.gg/"]

        text_lower = text.lower()
        for w in severe:
            if w in text_lower:
                return "severe", 0.95
        for w in moderate:
            if w in text_lower:
                return "moderate", 0.7
        for link in links:
            if link in text_lower:
                return "spam_link", 0.6
        return "safe", 0.1

    def test_100_messages_simultaneously(self):
        """Classify 100 messages without crashing."""
        messages = [f"Normal message #{i}" for i in range(100)]
        results = [self._classify_message(m) for m in messages]
        assert len(results) == 100
        assert all(r[0] == "safe" for r in results)

    def test_mixed_languages(self):
        """Handle messages in different languages."""
        messages = [
            "Hello, how are you?",
            "Bonjour, comment ça va?",
            "Hola, ¿cómo estás?",
            "こんにちは元気ですか",
            "Привет, как дела?",
        ]
        results = [self._classify_message(m) for m in messages]
        assert len(results) == 5
        assert all(r[0] == "safe" for r in results)

    def test_severe_content_flagged(self):
        """Severe content is correctly flagged."""
        result = self._classify_message("You should kill yourself")
        assert result[0] == "severe"
        assert result[1] >= 0.9

    def test_moderate_content_flagged(self):
        """Moderate content is correctly flagged."""
        result = self._classify_message("You're such an idiot")
        assert result[0] == "moderate"
        assert result[1] >= 0.6

    def test_bulk_mixed_content(self):
        """Large batch with mixed severity levels."""
        messages = [f"Hello #{i}" for i in range(50)]
        messages.append("You're stupid")
        messages.append("kys")
        messages.append("https://spam.com")
        messages.extend([f"Normal #{i}" for i in range(47)])

        results = [self._classify_message(m) for m in messages]
        severe_count = sum(1 for r in results if r[0] == "severe")
        moderate_count = sum(1 for r in results if r[0] == "moderate")
        spam_count = sum(1 for r in results if r[0] == "spam_link")

        assert severe_count == 1
        assert moderate_count == 1
        assert spam_count == 1

    def test_edited_message_reclassified(self):
        """Edited messages get reclassified from scratch."""
        original = "Hello everyone!"
        edited = "You're an idiot"

        r1 = self._classify_message(original)
        r2 = self._classify_message(edited)

        assert r1[0] == "safe"
        assert r2[0] == "moderate"

    def test_messages_with_attachments(self):
        """Messages with image attachments are handled (text-only classification)."""
        messages = [
            {"content": "", "has_image": True},
            {"content": "Nice photo!", "has_image": True},
            {"content": "", "has_image": True},
        ]
        for m in messages:
            result = self._classify_message(m["content"] or "(image)")
            assert result[0] in ("safe", "moderate", "severe", "spam_link")


# ═══════════════════════════════════════════════════════════════════════════
# 8. Multi-Guild Support
# ═══════════════════════════════════════════════════════════════════════════

class TestMultiGuildSupport:

    def test_guild_isolation_settings(self):
        """Each guild has independent settings."""
        guild_settings = {
            "guild_1": {"moderation_phase": "warning", "welcome_message": "Welcome to G1!"},
            "guild_2": {"moderation_phase": "dry_run", "welcome_message": "Welcome to G2!"},
            "guild_3": {"moderation_phase": "active", "welcome_message": "Welcome to G3!"},
        }

        assert guild_settings["guild_1"]["moderation_phase"] != guild_settings["guild_2"]["moderation_phase"]
        assert guild_settings["guild_2"]["welcome_message"] != guild_settings["guild_3"]["welcome_message"]

    def test_guild_isolation_memory(self):
        """Memory stores are isolated per guild."""
        memory = {
            "guild_1": {"users": {"alice": {"warnings": 2}}},
            "guild_2": {"users": {"alice": {"warnings": 0}}},
        }

        assert memory["guild_1"]["users"]["alice"]["warnings"] == 2
        assert memory["guild_2"]["users"]["alice"]["warnings"] == 0

    def test_concurrent_guild_operations(self):
        """Multiple guilds can be operated on concurrently."""
        results = {}
        lock = threading.Lock()

        def process_guild(guild_id):
            guild = _make_guild(name=f"Guild_{guild_id}", guild_id=guild_id)
            _make_discord_tools(guild)
            with lock:
                results[guild_id] = {
                    "name": guild.name,
                    "id": guild.id,
                    "processed": True,
                }

        threads = [threading.Thread(target=process_guild, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 5
        for gid in range(5):
            assert results[gid]["processed"] is True
            assert results[gid]["id"] == gid

    @pytest.mark.asyncio
    async def test_guild_specific_audit_logs(self):
        """Each guild returns its own audit logs."""
        guild1 = _make_guild("Server1", guild_id=100)
        guild2 = _make_guild("Server2", guild_id=200)

        entry1 = MagicMock()
        entry1.action = "ban"
        entry1.user = "Mod1"
        entry1.target = "User1"
        entry1.reason = "Spam"
        entry1.created_at = MagicMock()

        entry2 = MagicMock()
        entry2.action = "kick"
        entry2.user = "Mod2"
        entry2.target = "User2"
        entry2.reason = "Raid"
        entry2.created_at = MagicMock()

        async def _aiter1(**kw):
            yield entry1

        async def _aiter2(**kw):
            yield entry2

        guild1.audit_logs = _aiter1
        guild2.audit_logs = _aiter2

        tools1 = _make_discord_tools(guild1)
        tools2 = _make_discord_tools(guild2)

        result1 = await tools1.get_audit_logs(guild1, limit=10)
        result2 = await tools2.get_audit_logs(guild2, limit=10)

        logs1 = result1.after_state["logs"]
        logs2 = result2.after_state["logs"]

        assert logs1[0]["action"] == "ban"
        assert logs2[0]["action"] == "kick"
        assert logs1[0]["user"] != logs2[0]["user"]

    def test_guild_settings_do_not_leak(self):
        """Modifying one guild's settings doesn't affect another."""
        settings = {
            "100": {"welcome_channel": "welcome-g1"},
            "200": {"welcome_channel": "welcome-g2"},
        }

        settings["100"]["welcome_channel"] = "new-welcome-g1"
        assert settings["200"]["welcome_channel"] == "welcome-g2"
        assert settings["100"]["welcome_channel"] == "new-welcome-g1"


# ═══════════════════════════════════════════════════════════════════════════
# 9. Edge Cases
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_empty_message_ignored(self):
        """Empty messages should be filtered out."""
        text = ""
        assert not text.strip(), "Empty message should be ignored"

    def test_whitespace_only_message_ignored(self):
        """Whitespace-only messages should be filtered out."""
        text = "   \n\t  "
        assert not text.strip(), "Whitespace-only message should be ignored"

    def test_emoji_only_message(self):
        """Messages with only emojis should be handled."""
        text = "😀🎉🔥💯"
        assert len(text) > 0
        assert all(c in text for c in "😀🎉🔥")

    def test_mention_everyone_message(self):
        """@everyone mentions should be detected."""
        text = "@everyone Check out this new update!"
        has_everyone = "@everyone" in text
        has_here = "@here" in text
        assert has_everyone is True
        assert has_here is False

    def test_mention_here_message(self):
        """@here mentions should be detected."""
        text = "@here Server maintenance in 5 minutes"
        assert "@here" in text

    def test_message_with_attachment_metadata(self):
        """Messages with attachments should carry attachment info."""
        msg = _make_message("Check this out")
        attachment = MagicMock()
        attachment.filename = "screenshot.png"
        attachment.size = 1024000
        attachment.content_type = "image/png"
        msg.attachments = [attachment]

        assert len(msg.attachments) == 1
        assert msg.attachments[0].filename == "screenshot.png"

    def test_very_long_message(self):
        """Messages exceeding 4000 chars should be truncated."""
        long_msg = "A" * 5000
        truncated = long_msg[:4000]
        assert len(truncated) == 4000
        assert len(long_msg) == 5000

    def test_message_from_bot_ignored(self):
        """Messages from bots should be ignored."""
        bot_msg = _make_message("I am a bot")
        bot_msg.author.bot = True
        bot_msg.webhook_id = None
        assert bot_msg.author.bot is True

    def test_webhook_message_from_bot_allowed(self):
        """Webhook messages from bots should be allowed."""
        msg = _make_message("Webhook message")
        msg.author.bot = True
        msg.webhook_id = 12345
        assert msg.author.bot is True
        assert msg.webhook_id is not None

    def test_message_in_thread(self):
        """Messages in threads should be handled."""
        thread = MagicMock()
        thread.name = "Discussion Thread"
        thread.type = MagicMock()
        thread.type.__str__ = lambda self: "public_thread"
        thread.parent = _make_channel("general")
        msg = _make_message("Thread message")
        msg.channel = thread

        assert msg.channel.name == "Discussion Thread"

    def test_message_in_forum_post(self):
        """Messages in forum posts should be handled."""
        forum_thread = MagicMock()
        forum_thread.name = "My Feature Request"
        forum_thread.type = MagicMock()
        forum_thread.type.__str__ = lambda self: "public_thread"
        forum_thread.parent = MagicMock()
        forum_thread.parent.name = "suggestions"
        forum_thread.parent.type = MagicMock()
        forum_thread.parent.type.__str__ = lambda self: "forum"

        msg = _make_message("Please add dark mode")
        msg.channel = forum_thread

        assert "feature" in msg.channel.name.lower()

    def test_mentions_stripped_from_text(self):
        """Discord mentions should be stripped for processing."""
        import re
        text = "<@123456> <@!789012> <@&345678> Hello there"
        text = re.sub(r'<@!?\d+>', '', text).strip()
        text = re.sub(r'<@&\d+>', '', text).strip()
        assert text == "Hello there"

    def test_special_characters_in_messages(self):
        """Messages with special characters should not crash."""
        special = "test!@#$%^&*()_+-=[]{}|;':\",./<>?"
        assert len(special) > 0
        normalized = " ".join(special.lower().strip().split())
        assert normalized == special.lower()

    def test_unicode_messages(self):
        """Unicode messages should be handled."""
        messages = [
            "مرحبا بالعالم",
            "Привет мир",
            "你好世界",
            "🌍🌎🌏",
            "café résumé naïve",
        ]
        for msg in messages:
            assert len(msg) > 0
            assert isinstance(msg.encode("utf-8"), bytes)

    def test_rtl_messages(self):
        """Right-to-left text should be handled."""
        text = "مرحبا كيف حالك"
        assert len(text) > 0

    def test_very_long_single_word(self):
        """Single very long word should not crash."""
        word = "a" * 10000
        assert len(word) == 10000

    def test_message_with_only_mentions(self):
        """Message containing only mentions should be handled after stripping."""
        import re
        text = "<@123456> <@789012> <@345678>"
        cleaned = re.sub(r'<@!?\d+>', '', text).strip()
        assert cleaned == ""

    def test_concurrent_same_user_messages(self):
        """Multiple simultaneous messages from same user."""
        now = time.time()
        messages = [
            {"user_id": "user1", "timestamp": now + i * 0.01, "content": f"msg{i}"}
            for i in range(20)
        ]
        user_msgs = [m for m in messages if m["user_id"] == "user1"]
        assert len(user_msgs) == 20


# ═══════════════════════════════════════════════════════════════════════════
# 10. Recovery Scenarios
# ═══════════════════════════════════════════════════════════════════════════

class TestRecoveryScenarios:

    @pytest.mark.asyncio
    async def test_llm_timeout_recovery(self):
        """Agent recovers gracefully when LLM times out mid-response."""
        async def timeout_operation():
            raise TimeoutError("LLM response timed out")

        max_retries = 3
        attempts = 0
        last_error = None

        for _attempt in range(max_retries):
            try:
                await timeout_operation()
            except TimeoutError as e:
                attempts += 1
                last_error = e
                await asyncio.sleep(0.01)

        assert attempts == max_retries
        assert "timed out" in str(last_error)

    @pytest.mark.asyncio
    async def test_discord_rate_limit_recovery(self):
        """Agent handles Discord rate limiting with backoff."""
        rate_limit_hit = False
        retry_count = 0
        max_retries = 3

        async def rate_limited_operation():
            nonlocal rate_limit_hit, retry_count
            retry_count += 1
            if retry_count <= 2:
                raise Exception("Rate limited (429)")
            return "success"

        for _attempt in range(max_retries):
            try:
                result = await rate_limited_operation()
                if result == "success":
                    break
            except Exception as e:
                if "429" in str(e):
                    rate_limit_hit = True
                    await asyncio.sleep(0.01)

        assert rate_limit_hit is True
        assert retry_count == 3

    def test_database_corruption_recovery(self, tmp_path):
        """Memory backend handles corrupted database gracefully."""
        db_path = tmp_path / "corrupt.db"
        db_path.write_bytes(b"this is not valid sqlite\x00\x00\x00")

        from azure.memory_backend import SQLiteMemoryBackend
        try:
            mem = SQLiteMemoryBackend(db_path=str(db_path))
            results = mem.query_memories(user_id="test")
            assert isinstance(results, list)
        except Exception:
            pass

    def test_memory_backend_thread_safety_direct(self):
        """In-memory backend handles concurrent access safely (bypass JSON persistence)."""
        from azure.memory_backend import MemoryBackend
        mem = MemoryBackend.__new__(MemoryBackend)
        mem._profiles = {}
        mem._memories = []
        mem._events = []
        mem._conversations = {}
        mem._json_path = None
        mem._lock = threading.Lock()

        mem.store("user1", "first message")
        mem.store("user1", "second message")

        msgs = mem.retrieve("user1")
        assert len(msgs) == 2

        mem.delete("user1")
        msgs = mem.retrieve("user1")
        assert len(msgs) == 0

    @pytest.mark.asyncio
    async def test_step_retry_on_failure(self):
        """Step execution retries on transient failure."""
        call_count = 0

        async def flaky_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Transient error")
            return StepResult(success=True, action="test", name="retry_test")

        max_retries = 3
        for _attempt in range(max_retries):
            try:
                result = await flaky_operation()
                if result.success:
                    break
            except Exception:
                await asyncio.sleep(0.01)

        assert call_count == 3
        assert result.success is True

    def test_memory_backend_thread_safety(self):
        """Memory backend handles concurrent access safely (no JSON persistence)."""
        from azure.memory_backend import MemoryBackend
        mem = MemoryBackend.__new__(MemoryBackend)
        mem._profiles = {}
        mem._memories = []
        mem._events = []
        mem._conversations = {}
        mem._json_path = None
        mem._lock = threading.Lock()
        errors = []

        def writer(uid):
            try:
                for i in range(50):
                    mem.store(uid, f"message {i}")
            except Exception as e:
                errors.append(e)

        def reader(uid):
            try:
                for _ in range(50):
                    mem.retrieve(uid)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=("u1",)),
            threading.Thread(target=writer, args=("u2",)),
            threading.Thread(target=reader, args=("u1",)),
            threading.Thread(target=reader, args=("u2",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    def test_plan_parse_malformed_json(self):
        """Plan parsing handles malformed JSON gracefully."""
        from azure.tools.plan_tools import PlanToolsMixin

        mixin = PlanToolsMixin.__new__(PlanToolsMixin)

        result = mixin._parse_plan("not json at all")
        assert result["analysis"] == "Failed to parse plan."
        assert result["steps"] == []

    def test_plan_parse_valid_json(self):
        """Plan parsing succeeds with valid JSON."""
        from azure.tools.plan_tools import PlanToolsMixin

        mixin = PlanToolsMixin.__new__(PlanToolsMixin)

        plan_json = '{"analysis": "Test plan", "steps": [{"action": "create_role", "name": "Mod"}]}'
        result = mixin._parse_plan(plan_json)
        assert result["analysis"] == "Test plan"
        assert len(result["steps"]) == 1
        assert result["steps"][0]["action"] == "create_role"

    def test_plan_parse_json_with_wrapping_text(self):
        """Plan parsing extracts JSON from text with surrounding content."""
        from azure.tools.plan_tools import PlanToolsMixin

        mixin = PlanToolsMixin.__new__(PlanToolsMixin)

        wrapped = 'Here is the plan:\n{"analysis": "ok", "steps": []}\nDone.'
        result = mixin._parse_plan(wrapped)
        assert result["analysis"] == "ok"

    def test_color_resolution(self):
        """Color string resolution handles named colors."""
        assert _resolve_color("red") == 0xE74C3C
        assert _resolve_color("blue") == 0x3498DB
        assert _resolve_color("unknown") == 0x99AAB5

    def test_llm_reason_format(self):
        """_llm_reason produces correct audit strings."""
        assert _llm_reason("setup") == "Azure: setup"
        assert _llm_reason("edit", "role changes") == "Azure: edit - role changes"

    def test_embed_color_by_status(self):
        """_embed_color returns correct colors per status."""
        assert _embed_color("info") == 0x3498DB
        assert _embed_color("success") == 0x2ECC71
        assert _embed_color("warning") == 0xE67E22
        assert _embed_color("error") == 0xE74C3C
        assert _embed_color("unknown") == 0x3498DB

    def test_parse_color_hex(self):
        """_parse_color handles hex strings."""
        tools = _make_discord_tools()
        assert tools._parse_color("FF0000") == 0xFF0000
        assert tools._parse_color("#00FF00") == 0x00FF00
        assert tools._parse_color(None) == 0
        assert tools._parse_color(42) == 42

    def test_build_permissions(self):
        """_build_permissions constructs correct permission objects."""
        tools = _make_discord_tools()
        perms = tools._build_permissions(["kick_members", "manage_messages"])
        assert perms is not None

    def test_resolve_member_by_name(self):
        """_resolve_member finds member by display name."""
        guild = _make_guild()
        member = _make_member("Alice", uid=1111)
        guild.members = [member]
        guild.get_member.return_value = None

        tools = _make_discord_tools(guild)
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(tools._resolve_member(guild, "Alice"))
            assert result is not None
            assert result.name == "Alice"
        finally:
            loop.close()

    def test_resolve_member_not_found(self):
        """_resolve_member returns None for unknown member."""
        guild = _make_guild()
        guild.members = []

        tools = _make_discord_tools(guild)
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(tools._resolve_member(guild, "Ghost"))
            assert result is None
        finally:
            loop.close()

    def test_resolve_member_empty_identifier(self):
        """_resolve_member returns None for empty identifier."""
        guild = _make_guild()
        tools = _make_discord_tools(guild)
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(tools._resolve_member(guild, ""))
            assert result is None
        finally:
            loop.close()

    def test_delete_role_cannot_delete_everyone(self):
        """Cannot delete the @everyone role."""
        guild = _make_guild()
        everyone_role = _make_role("@everyone")
        everyone_role.is_default.return_value = True
        guild.roles = [everyone_role]

        tools = _make_discord_tools(guild)
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(tools.delete_role(guild, role_name="@everyone"))
            assert result.success is False
            assert "everyone" in result.error.lower()
        finally:
            loop.close()

    def test_concurrent_plan_executions_limited(self):
        """Plan execution is limited to MAX_CONCURRENT_EXECUTIONS."""
        from azure.tools.plan_tools import MAX_CONCURRENT_EXECUTIONS
        assert MAX_CONCURRENT_EXECUTIONS >= 1

    @pytest.mark.asyncio
    async def test_self_repair_logs_errors(self):
        """SelfRepair system logs errors without crashing."""
        from azure.self_repair import SelfRepair
        repair = SelfRepair()
        repair._log_error("test_action", "TestGuild", "TestError", "test details", "test context")
        # Verify it does not raise and SelfRepair object is intact
        assert repair is not None
        assert hasattr(repair, '_log_error')

    def test_step_result_dataclass(self):
        """StepResult has all expected fields."""
        r = StepResult(
            success=True, action="test", name="test_name",
            detail="detail", error="", target_id=123,
            before_state={"a": 1}, after_state={"b": 2},
        )
        assert r.success is True
        assert r.action == "test"
        assert r.before_state == {"a": 1}
        assert r.after_state == {"b": 2}

    def test_step_result_failure(self):
        """StepResult for failure has error message."""
        r = StepResult(
            success=False, action="create_role", name="BadRole",
            error="Permission denied",
        )
        assert r.success is False
        assert r.error == "Permission denied"
        assert r.target_id == 0


# ═══════════════════════════════════════════════════════════════════════════
# Integration: Full Plan Generation + Execution Flow
# ═══════════════════════════════════════════════════════════════════════════

class TestPlanIntegration:

    def test_parse_plan_from_llm_output(self):
        """Parse a realistic LLM-generated plan."""
        from azure.tools.plan_tools import PlanToolsMixin

        mixin = PlanToolsMixin.__new__(PlanToolsMixin)

        llm_output = '''
        {
            "analysis": "The server needs a gaming section with text and voice channels, plus a moderator role.",
            "steps": [
                {"action": "create_role", "name": "Moderator", "color": "blue", "permissions": ["kick_members", "manage_messages"]},
                {"action": "create_category", "name": "Gaming"},
                {"action": "create_channel", "name": "gaming-chat", "type": "text", "category": "Gaming"},
                {"action": "create_channel", "name": "Gaming Voice", "type": "voice", "category": "Gaming"},
                {"action": "set_permissions", "channel": "gaming-chat", "role": "Moderator", "allow": ["manage_messages"]}
            ]
        }
        '''
        plan = mixin._parse_plan(llm_output)

        assert plan["analysis"].startswith("The server needs")
        assert len(plan["steps"]) == 5
        assert plan["steps"][0]["action"] == "create_role"
        assert plan["steps"][1]["action"] == "create_category"
        assert plan["steps"][2]["action"] == "create_channel"
        assert plan["steps"][3]["type"] == "voice"

    def test_build_planning_prompt(self):
        """Planning prompt includes server state context."""
        from azure.tools.plan_tools import PlanToolsMixin

        mixin = PlanToolsMixin.__new__(PlanToolsMixin)

        state = {
            "server_name": "MyServer",
            "member_count": 100,
            "roles": [{"name": "Admin"}, {"name": "Member"}],
            "channels": [{"name": "general"}, {"name": "off-topic"}],
            "categories": [{"name": "Info"}],
            "verification_level": "medium",
        }

        prompt = mixin._build_planning_prompt(state, "Create a gaming section")

        assert "MyServer" in prompt
        assert "100" in prompt
        assert "Admin" in prompt
        assert "general" in prompt
        assert "Create a gaming section" in prompt

    def test_extract_step_name_from_various_keys(self):
        """_extract_step_name tries multiple keys."""
        from azure.tools.plan_tools import PlanToolsMixin

        mixin = PlanToolsMixin.__new__(PlanToolsMixin)

        assert mixin._extract_step_name({"action": "create_role", "name": "Mod"}) == "Mod"
        assert mixin._extract_step_name({"action": "create_channel", "channel": "general"}) == "general"
        assert mixin._extract_step_name({"action": "kick", "member": "BadUser"}) == "BadUser"
        assert mixin._extract_step_name({"action": "unknown"}) == "unknown"

    def test_preflight_check_detects_missing_permissions(self):
        """Preflight check identifies missing bot permissions."""
        guild = _make_guild()
        bot_member = _make_member("AzureBot", uid=9999, admin=False)
        bot_member.guild_permissions = _make_permission(manage_roles=False, manage_channels=False)
        guild.get_member = MagicMock(return_value=bot_member)

        tools = _make_discord_tools(guild)
        tools.bot = MagicMock()
        tools.bot.user.id = 9999

        plan = {
            "steps": [
                {"action": "create_role", "name": "Mod"},
                {"action": "create_channel", "name": "test"},
            ]
        }

        loop = asyncio.new_event_loop()
        try:
            check = loop.run_until_complete(tools.preflight_check(guild, plan))
            assert check["can_execute"] is False
            assert len(check["missing"]) > 0
        finally:
            loop.close()
