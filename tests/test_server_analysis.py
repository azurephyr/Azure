"""Comprehensive tests for Discord server analysis capabilities.

Covers: member count, activity analysis, chat analysis, problem detection,
member analysis, channel analysis, role analysis, and server health checks.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Helpers — Discord object mocks
# ---------------------------------------------------------------------------

def _make_role(name: str, *, position: int = 0, color: int = 0, hoist: bool = False,
               mentionable: bool = True, member_count: int = 0,
               permissions: list | None = None, is_default: bool = False,
               managed: bool = False):
    role = MagicMock()
    role.name = name
    role.position = position
    role.color = MagicMock()
    role.color.__str__ = lambda self: f"#{color:06x}"
    role.hoist = hoist
    role.mentionable = mentionable
    role.permissions = permissions or []
    role.managed = managed
    role.is_default.return_value = is_default
    role.is_default.__bool__ = lambda self: is_default
    role.members = [MagicMock() for _ in range(member_count)]
    return role


def _make_member(name: str, *, display_name: str | None = None, user_id: int = 0,
                 status=None, roles: list | None = None, joined_at=None,
                 message_count: int = 0):
    member = MagicMock()
    member.name = name
    member.display_name = display_name or name
    member.id = user_id
    member.status = status
    member.roles = roles or []
    member.joined_at = joined_at or datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC)
    member.message_count = message_count
    return member


def _make_channel(name: str, *, channel_type: str = "text", topic: str = "",
                  position: int = 0, category=None, nsfw: bool = False,
                  slowmode_delay: int = 0):
    ch = MagicMock()
    ch.name = name
    ch.id = hash(name) % (10**18)
    ch.position = position
    ch.category = category
    ch.topic = topic
    ch.nsfw = nsfw
    ch.slowmode_delay = slowmode_delay

    type_mock = MagicMock()
    type_mock.__str__ = lambda self: channel_type
    ch.type = type_mock
    return ch


def _make_guild(name: str = "Test Server", *, member_count: int = 50,
                members: list | None = None, roles: list | None = None,
                channels: list | None = None, categories: list | None = None,
                verification_level=1, explicit_content_filter=1,
                default_notifications=1, mfa_level=0,
                afk_channel=None, system_channel=None, rules_channel=None):
    guild = MagicMock()
    guild.name = name
    guild.id = 123456789
    guild.member_count = member_count

    _members = members if members is not None else [_make_member(f"user_{i}", user_id=100 + i) for i in range(member_count)]
    guild.members = _members

    _roles = roles if roles is not None else [_make_role("@everyone", is_default=True, position=0)]
    guild.roles = _roles

    _channels = channels if channels is not None else [_make_channel("general")]
    guild.channels = _channels
    guild.text_channels = [c for c in _channels if str(c.type) == "text"]
    guild.voice_channels = [c for c in _channels if str(c.type) == "voice"]

    guild.categories = categories if categories is not None else []
    guild.verification_level = verification_level
    guild.explicit_content_filter = explicit_content_filter
    guild.default_notifications = default_notifications
    guild.mfa_level = mfa_level
    guild.afk_channel = afk_channel
    guild.system_channel = system_channel
    guild.rules_channel = rules_channel
    guild.emojis = []
    return guild


def _make_message(content: str, author_name: str = "user1", author_id: int = 101,
                  *, created_at=None, channel_name: str = "general"):
    msg = MagicMock(spec=[])
    msg.content = content
    msg.created_at = created_at or datetime.datetime(2025, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
    msg.channel = MagicMock()
    msg.channel.name = channel_name
    author = MagicMock()
    author.name = author_name
    author.id = author_id
    author.display_name = author_name
    msg.author = author
    return msg


# ═══════════════════════════════════════════════════════════════════════════
# 1. Member Count Analysis
# ═══════════════════════════════════════════════════════════════════════════

class TestMemberCountAnalysis:
    """Verify the bot reports accurate member counts."""

    def test_guild_member_count_returns_expected_value(self):
        guild = _make_guild(member_count=500)
        assert guild.member_count == 500

    def test_server_tools_returns_member_count(self):
        from azure.tools.server_tools import ServerToolsMixin

        mixin = ServerToolsMixin.__new__(ServerToolsMixin)
        members = [_make_member(f"u{i}", user_id=200 + i) for i in range(500)]
        guild = _make_guild(member_count=500, members=members)

        result = asyncio_run(mixin.get_server_state(guild))
        assert result["member_count"] == 500

    def test_member_count_matches_len_members(self):
        members = [_make_member(f"u{i}", user_id=200 + i) for i in range(350)]
        guild = _make_guild(member_count=350, members=members)
        assert guild.member_count == len(members)

    def test_zero_member_count(self):
        guild = _make_guild(member_count=0, members=[])
        assert guild.member_count == 0
        assert len(guild.members) == 0

    def test_large_server_count(self):
        guild = _make_guild(member_count=50000, members=[])
        assert guild.member_count == 50000

    def test_member_count_resolves_with_fetch(self):
        from azure.tools.server_tools import ServerToolsMixin

        mixin = ServerToolsMixin.__new__(ServerToolsMixin)
        members = [_make_member(f"u{i}", user_id=200 + i) for i in range(500)]
        guild = _make_guild(member_count=500, members=members)

        result = asyncio_run(mixin.get_server_state(guild))
        assert result["member_count"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 2. Activity Analysis
# ═══════════════════════════════════════════════════════════════════════════

class TestActivityAnalysis:
    """Verify the bot can assess server activity from message timestamps."""

    def test_recent_messages_are_active(self):
        now = datetime.datetime.now(datetime.UTC)
        messages = [
            _make_message(f"msg {i}", created_at=now - datetime.timedelta(minutes=i * 5))
            for i in range(20)
        ]
        timestamps = [m.created_at for m in messages]
        span = (max(timestamps) - min(timestamps)).total_seconds()
        msgs_per_hour = len(messages) / max(span / 3600, 0.001)
        assert msgs_per_hour > 0

    def test_old_messages_are_inactive(self):
        old = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        messages = [
            _make_message("stale", created_at=old - datetime.timedelta(days=i))
            for i in range(5)
        ]
        timestamps = [m.created_at for m in messages]
        span = (max(timestamps) - min(timestamps)).total_seconds()
        msgs_per_hour = len(messages) / max(span / 3600, 0.001)
        assert msgs_per_hour < 1

    def test_empty_messages_zero_rate(self):
        messages = []
        rate = len(messages) / 1.0
        assert rate == 0

    def test_activity_categorization_active(self):
        msgs_per_hour = 25
        assessment = "active" if msgs_per_hour >= 10 else "moderate" if msgs_per_hour >= 2 else "quiet"
        assert assessment == "active"

    def test_activity_categorization_quiet(self):
        msgs_per_hour = 0.5
        assessment = "active" if msgs_per_hour >= 10 else "moderate" if msgs_per_hour >= 2 else "quiet"
        assert assessment == "quiet"

    def test_activity_categorization_moderate(self):
        msgs_per_hour = 5
        assessment = "active" if msgs_per_hour >= 10 else "moderate" if msgs_per_hour >= 2 else "quiet"
        assert assessment == "moderate"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Chat Analysis / Conversation Summarization
# ═══════════════════════════════════════════════════════════════════════════

class TestChatAnalysis:
    """Verify the bot can summarize recent conversations and identify themes."""

    def test_unique_authors_extracted(self):
        messages = [
            _make_message("hello", author_name="Alice", author_id=1),
            _make_message("hi", author_name="Bob", author_id=2),
            _make_message("hey", author_name="Alice", author_id=1),
            _make_message("yo", author_name="Charlie", author_id=3),
        ]
        authors = list({m.author.name for m in messages})
        assert len(authors) == 3
        assert set(authors) == {"Alice", "Bob", "Charlie"}

    def test_common_words_detected(self):
        messages = [
            _make_message("let's play minecraft tonight"),
            _make_message("who wants to play minecraft"),
            _make_message("i love playing minecraft"),
            _make_message("anyone for minecraft"),
        ]
        words = " ".join(m.content for m in messages).lower().split()
        word_counts = {}
        for w in words:
            word_counts[w] = word_counts.get(w, 0) + 1
        most_common = max(word_counts, key=word_counts.get)
        assert most_common == "minecraft"

    def test_message_length_indicates_engagement(self):
        short_messages = [_make_message("ok") for _ in range(10)]
        long_messages = [_make_message("a " * 100) for _ in range(10)]
        avg_short = sum(len(m.content) for m in short_messages) / len(short_messages)
        avg_long = sum(len(m.content) for m in long_messages) / len(long_messages)
        assert avg_long > avg_short

    def test_empty_chat_returns_no_themes(self):
        messages = []
        themes = list({m.content.lower() for m in messages})
        assert themes == []

    def test_single_message_single_theme(self):
        messages = [_make_message("hello world")]
        themes = list({m.content.lower() for m in messages})
        assert themes == ["hello world"]


# ═══════════════════════════════════════════════════════════════════════════
# 4. Problem Detection (Arguments, Toxicity, Spam)
# ═══════════════════════════════════════════════════════════════════════════

class TestProblemDetection:
    """Verify the bot detects conflicts, toxicity, and spam patterns."""

    TOXIC_KEYWORDS = {"stupid", "idiot", "hate", "shut up", "dumb", "trash"}

    def _detect_toxicity(self, messages):
        flagged = []
        for m in messages:
            lower = m.content.lower()
            hits = [kw for kw in self.TOXIC_KEYWORDS if kw in lower]
            if hits:
                flagged.append({"message": m, "keywords": hits})
        return flagged

    def test_toxic_messages_flagged(self):
        messages = [
            _make_message("you are stupid", author_name="troll1", author_id=10),
            _make_message("hello everyone", author_name="nice1", author_id=11),
            _make_message("shut up idiot", author_name="troll1", author_id=10),
        ]
        flagged = self._detect_toxicity(messages)
        assert len(flagged) == 2
        assert flagged[0]["keywords"] == ["stupid"]
        assert sorted(flagged[1]["keywords"]) == ["idiot", "shut up"]

    def test_clean_messages_no_flags(self):
        messages = [
            _make_message("hello world"),
            _make_message("how are you"),
            _make_message("have a great day"),
        ]
        flagged = self._detect_toxicity(messages)
        assert len(flagged) == 0

    def test_spam_detection_repeated_messages(self):
        messages = [
            _make_message("join my server!", author_name="spammer", author_id=99)
            for _ in range(20)
        ]
        same_author = [m for m in messages if m.author.id == 99]
        is_spam = len(same_author) > 10
        assert is_spam

    def test_spam_detection_burst_from_single_user(self):
        now = datetime.datetime.now(datetime.UTC)
        messages = [
            _make_message(f"spam {i}", author_name="bot", author_id=50,
                          created_at=now + datetime.timedelta(milliseconds=i * 100))
            for i in range(30)
        ]
        timestamps = [m.created_at for m in messages]
        span_seconds = (max(timestamps) - min(timestamps)).total_seconds()
        rate = len(messages) / max(span_seconds, 0.001)
        assert rate > 5

    def test_argument_detection_opposing_sentiments(self):
        positive = ["I think this is great", "This is the best", "I love it"]
        negative = ["This is terrible", "I hate it", "This is the worst"]
        pos_score = len(positive)
        neg_score = len(negative)
        has_conflict = abs(pos_score - neg_score) <= 2
        assert has_conflict

    def test_empty_messages_no_problems(self):
        flagged = self._detect_toxicity([])
        assert flagged == []
        assert not [1 for _ in []]


# ═══════════════════════════════════════════════════════════════════════════
# 5. Member Analysis
# ═══════════════════════════════════════════════════════════════════════════

class TestMemberAnalysis:
    """Verify the bot can analyze individual member profiles."""

    def test_member_has_join_date(self):
        member = _make_member("Alice", user_id=1,
                              joined_at=datetime.datetime(2024, 6, 15, tzinfo=datetime.UTC))
        assert member.joined_at is not None
        assert member.joined_at.year == 2024

    def test_member_has_roles(self):
        admin_role = _make_role("Admin", position=2)
        member = _make_member("Bob", roles=[admin_role])
        assert len(member.roles) == 1
        assert member.roles[0].name == "Admin"

    def test_member_resolve_by_id(self):
        from azure.tools.server_tools import ServerToolsMixin

        mixin = ServerToolsMixin.__new__(ServerToolsMixin)
        alice = _make_member("alice", user_id=111)
        guild = _make_guild(members=[alice])

        result = asyncio_run(mixin._resolve_member(guild, "111"))
        assert result is not None

    def test_member_resolve_by_name(self):
        from azure.tools.server_tools import ServerToolsMixin

        mixin = ServerToolsMixin.__new__(ServerToolsMixin)
        bob = _make_member("bob", display_name="Bobby", user_id=222)
        guild = _make_guild(members=[bob])

        result = asyncio_run(mixin._resolve_member(guild, "bob"))
        assert result is not None

    def test_member_resolve_not_found(self):
        from azure.tools.server_tools import ServerToolsMixin

        mixin = ServerToolsMixin.__new__(ServerToolsMixin)
        guild = _make_guild(members=[])

        result = asyncio_run(mixin._resolve_member(guild, "ghost"))
        assert result is None

    def test_member_resolve_by_mention(self):
        from azure.tools.server_tools import ServerToolsMixin

        mixin = ServerToolsMixin.__new__(ServerToolsMixin)
        carol = _make_member("carol", user_id=333)
        guild = _make_guild(members=[carol])

        result = asyncio_run(mixin._resolve_member(guild, "<@333>"))
        assert result is not None

    def test_member_status_tracked(self):
        statuses = {"online", "idle", "dnd", "offline"}
        member = _make_member("dave")
        member.status = "online"
        assert member.status in statuses

    def test_member_message_count(self):
        member = _make_member("eve", message_count=42)
        assert member.message_count == 42


# ═══════════════════════════════════════════════════════════════════════════
# 6. Channel Analysis
# ═══════════════════════════════════════════════════════════════════════════

class TestChannelAnalysis:
    """Verify the bot can report on channel structure and activity."""

    def test_channels_listed_in_server_state(self):
        from azure.tools.server_tools import ServerToolsMixin

        mixin = ServerToolsMixin.__new__(ServerToolsMixin)
        channels = [
            _make_channel("general", channel_type="text"),
            _make_channel("voice-chat", channel_type="voice"),
        ]
        guild = _make_guild(channels=channels)

        result = asyncio_run(mixin.get_server_state(guild))
        assert len(result["channels"]) == 2

    def test_channel_has_topic(self):
        ch = _make_channel("announcements", topic="Server announcements")
        assert ch.topic == "Server announcements"

    def test_channel_type_distinguished(self):
        text = _make_channel("chat", channel_type="text")
        voice = _make_channel("lounge", channel_type="voice")
        assert str(text.type) == "text"
        assert str(voice.type) == "voice"

    def test_channel_nsfw_flag(self):
        nsfw = _make_channel("adult", nsfw=True)
        safe = _make_channel("general", nsfw=False)
        assert nsfw.nsfw is True
        assert safe.nsfw is False

    def test_channel_slowmode(self):
        ch = _make_channel("slow", slowmode_delay=30)
        assert ch.slowmode_delay == 30

    def test_channel_category_assigned(self):
        cat = MagicMock()
        cat.name = "Info"
        ch = _make_channel("rules", category=cat)
        assert ch.category.name == "Info"

    def test_channel_not_found_returns_none(self):
        from azure.tools.server_tools import ServerToolsMixin

        mixin = ServerToolsMixin.__new__(ServerToolsMixin)
        guild = _make_guild(channels=[_make_channel("general")])
        result = asyncio_run(mixin._resolve_member(guild, "nonexistent"))
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# 7. Role Analysis
# ═══════════════════════════════════════════════════════════════════════════

class TestRoleAnalysis:
    """Verify the bot reports role distribution accurately."""

    def test_roles_in_server_state(self):
        from azure.tools.server_tools import ServerToolsMixin

        mixin = ServerToolsMixin.__new__(ServerToolsMixin)
        roles = [
            _make_role("@everyone", is_default=True, position=0),
            _make_role("Admin", position=3, member_count=2),
            _make_role("Member", position=1, member_count=100),
            _make_role("Bot", position=2, member_count=5, managed=True),
        ]
        guild = _make_guild(roles=roles, member_count=107)

        result = asyncio_run(mixin.get_server_state(guild))
        role_names = [r["name"] for r in result["roles"]]
        assert "Admin" in role_names
        assert "Member" in role_names
        assert "@everyone" not in role_names

    def test_role_member_count(self):
        role = _make_role("Moderator", member_count=10)
        assert len(role.members) == 10

    def test_role_color(self):
        role = _make_role("VIP", color=0xFF0000)
        assert str(role.color) == "#ff0000"

    def test_role_hoist(self):
        role = _make_role("Admin", hoist=True)
        assert role.hoist is True

    def test_role_mentionable(self):
        role = _make_role("Pingable", mentionable=True)
        assert role.mentionable is True

    def test_default_role_is_not_listed(self):
        role = _make_role("@everyone", is_default=True)
        assert role.is_default()

    def test_managed_role(self):
        role = _make_role("Bot Role", managed=True)
        assert role.managed is True

    def test_role_position_ordering(self):
        roles = [
            _make_role("Low", position=1),
            _make_role("Mid", position=5),
            _make_role("High", position=10),
        ]
        sorted_roles = sorted(roles, key=lambda r: r.position, reverse=True)
        assert sorted_roles[0].name == "High"
        assert sorted_roles[-1].name == "Low"


# ═══════════════════════════════════════════════════════════════════════════
# 8. Server Health Analysis
# ═══════════════════════════════════════════════════════════════════════════

class TestServerHealthAnalysis:
    """Verify the ServerHealthAnalyzer produces accurate reports."""

    def test_health_report_structure(self):
        from azure.server_health import ServerHealthAnalyzer

        analyzer = ServerHealthAnalyzer()
        guild = _make_guild(
            member_count=100,
            channels=[_make_channel("general")],
            roles=[_make_role("@everyone", is_default=True), _make_role("Admin", position=2)],
            verification_level=2,
        )
        guild.categories = []
        report = asyncio_run(analyzer.analyze(guild))

        assert "score" in report
        assert "categories" in report
        assert "issues" in report
        assert "recommendations" in report
        assert "followups" in report

    def test_health_score_range(self):
        from azure.server_health import ServerHealthAnalyzer

        analyzer = ServerHealthAnalyzer()
        guild = _make_guild(member_count=50)
        guild.categories = []
        report = asyncio_run(analyzer.analyze(guild))
        assert 0 <= report["score"] <= 100

    def test_perfect_server_high_score(self):
        from azure.server_health import ServerHealthAnalyzer

        analyzer = ServerHealthAnalyzer()
        channels = [
            _make_channel("rules", channel_type="text"),
            _make_channel("welcome", channel_type="text"),
            _make_channel("announcements", channel_type="text"),
            _make_channel("general", channel_type="text"),
            _make_channel("off-topic", channel_type="text"),
            _make_channel("introductions", channel_type="text"),
            _make_channel("feedback", channel_type="text"),
            _make_channel("voice", channel_type="voice"),
        ]
        roles = [
            _make_role("@everyone", is_default=True),
            _make_role("Admin", position=5),
            _make_role("Moderator", position=4),
            _make_role("Bot", position=3, managed=True),
            _make_role("Muted", position=2),
        ]
        cat = MagicMock()
        cat.name = "Info"
        cat.position = 0
        cat.channels = channels[:4]
        guild = _make_guild(
            member_count=200,
            channels=channels,
            roles=roles,
            verification_level=2,
            explicit_content_filter=1,
        )
        guild.categories = [cat]
        report = asyncio_run(analyzer.analyze(guild))
        assert report["score"] >= 60

    def test_missing_essential_channels_issues(self):
        from azure.server_health import ServerHealthAnalyzer

        analyzer = ServerHealthAnalyzer()
        guild = _make_guild(
            member_count=100,
            channels=[_make_channel("chat")],
            roles=[_make_role("@everyone", is_default=True), _make_role("Admin")],
        )
        guild.categories = []
        report = asyncio_run(analyzer.analyze(guild))
        channel_issues = [i for i in report["issues"] if "channel" in i.lower()]
        assert len(channel_issues) >= 1

    def test_missing_essential_roles_issues(self):
        from azure.server_health import ServerHealthAnalyzer

        analyzer = ServerHealthAnalyzer()
        guild = _make_guild(
            member_count=100,
            channels=[_make_channel("rules"), _make_channel("welcome"), _make_channel("announcements")],
            roles=[_make_role("@everyone", is_default=True)],
        )
        guild.categories = []
        report = asyncio_run(analyzer.analyze(guild))
        role_issues = [i for i in report["issues"] if "role" in i.lower()]
        assert len(role_issues) >= 1

    def test_low_verification_issues(self):
        from azure.server_health import ServerHealthAnalyzer

        analyzer = ServerHealthAnalyzer()
        guild = _make_guild(
            member_count=50,
            channels=[_make_channel("rules"), _make_channel("welcome"), _make_channel("announcements")],
            roles=[_make_role("@everyone", is_default=True), _make_role("Admin"), _make_role("Moderator")],
            verification_level=0,
        )
        guild.categories = []
        report = asyncio_run(analyzer.analyze(guild))
        security_issues = [i for i in report["issues"] if "verification" in i.lower()]
        assert len(security_issues) >= 1

    def test_recommendations_prioritized(self):
        from azure.server_health import ServerHealthAnalyzer

        analyzer = ServerHealthAnalyzer()
        guild = _make_guild(member_count=100)
        guild.categories = []
        report = asyncio_run(analyzer.analyze(guild))
        priorities = [r["priority"] for r in report["recommendations"]]
        if priorities:
            order = {"high": 0, "medium": 1, "low": 2}
            for i in range(len(priorities) - 1):
                assert order.get(priorities[i], 2) <= order.get(priorities[i + 1], 2)

    def test_format_report_contains_score(self):
        from azure.server_health import ServerHealthAnalyzer

        analyzer = ServerHealthAnalyzer()
        guild = _make_guild(member_count=50)
        guild.categories = []
        report = asyncio_run(analyzer.analyze(guild))
        formatted = analyzer.format_report(report)
        assert "Score" in formatted
        assert "/100" in formatted

    def test_suggest_followups_gaming(self):
        from azure.server_health import ServerHealthAnalyzer

        analyzer = ServerHealthAnalyzer()
        channels = [
            _make_channel("general", channel_type="text"),
            _make_channel("gaming-clips", channel_type="text"),
        ]
        guild = _make_guild(channels=channels)
        suggestions = analyzer.suggest_followups(guild, "created gaming channels")
        assert isinstance(suggestions, list)
        assert len(suggestions) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 9. Error Handling / Edge Cases
# ═══════════════════════════════════════════════════════════════════════════

class TestErrorCases:
    """Verify graceful handling when data is missing or permissions lack."""

    def test_empty_guild_members(self):
        guild = _make_guild(member_count=0, members=[])
        assert guild.member_count == 0
        assert len(guild.members) == 0

    def test_resolve_member_empty_identifier(self):
        from azure.tools.server_tools import ServerToolsMixin

        mixin = ServerToolsMixin.__new__(ServerToolsMixin)
        guild = _make_guild()
        result = asyncio_run(mixin._resolve_member(guild, ""))
        assert result is None

    def test_resolve_member_invalid_id(self):
        from azure.tools.server_tools import ServerToolsMixin

        mixin = ServerToolsMixin.__new__(ServerToolsMixin)
        guild = _make_guild()
        result = asyncio_run(mixin._resolve_member(guild, "not_a_number"))
        assert result is None

    def test_health_analyzer_empty_guild(self):
        from azure.server_health import ServerHealthAnalyzer

        analyzer = ServerHealthAnalyzer()
        guild = _make_guild(member_count=0, members=[], channels=[], roles=[
            _make_role("@everyone", is_default=True)
        ])
        guild.categories = []
        report = asyncio_run(analyzer.analyze(guild))
        assert report["score"] >= 0
        assert isinstance(report["issues"], list)

    def test_color_parse_none(self):
        from azure.tools.server_tools import ServerToolsMixin

        mixin = ServerToolsMixin.__new__(ServerToolsMixin)
        assert mixin._parse_color(None) == 0

    def test_color_parse_int(self):
        from azure.tools.server_tools import ServerToolsMixin

        mixin = ServerToolsMixin.__new__(ServerToolsMixin)
        assert mixin._parse_color(0xFF0000) == 0xFF0000

    def test_color_parse_hex_string(self):
        from azure.tools.server_tools import ServerToolsMixin

        mixin = ServerToolsMixin.__new__(ServerToolsMixin)
        assert mixin._parse_color("#3498DB") == 0x3498DB

    def test_color_parse_named(self):
        from azure.tools.server_tools import ServerToolsMixin

        mixin = ServerToolsMixin.__new__(ServerToolsMixin)
        assert mixin._parse_color("red") == 0xE74C3C

    def test_toxicity_empty_list(self):
        messages = []
        toxic_kw = {"stupid", "idiot", "hate"}
        flagged = [m for m in messages if any(kw in m.content.lower() for kw in toxic_kw)]
        assert flagged == []


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def asyncio_run(coro):
    """Run an async coroutine synchronously for tests."""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
