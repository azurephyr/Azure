"""Simulation environment — mock Discord world + BotContext setup."""

import contextlib
import sys as _sys
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import MOCK as _discord_mock  # noqa: N811
from tests.conftest import REAL_DISCORD as _real_discord  # noqa: N811
from tests.conftest import reset_utils_get

_orig_discord = _sys.modules.pop("discord", None)
_sys.modules["discord"] = _discord_mock
reset_utils_get()

from tests.test_discord_simulation import (  # noqa: E402, F811
    FakeLLM,
    make_channel,
    make_guild,
    make_member,
    make_role,
)

if _orig_discord is not None:
    _sys.modules["discord"] = _orig_discord


class FakeAgent:
    """Mock agent returning canned responses for simulation scenarios."""

    def __init__(self, default_response="Hello! I'm Azure. How can I help you today?"):
        self.default_response = default_response
        self.responses: dict[str | callable, str] = {}
        self.llm = FakeLLM(default_response)
        self.moderation = None
        self.short_term = MagicMock()
        self.long_term = MagicMock()
        self.rag = MagicMock()
        self._discord_tools = None
        self._current_guild = None
        self._current_channel = None
        self._event_loop = None

    def set_discord_context(self, discord_tools=None, guild=None, channel=None, event_loop=None):
        self._discord_tools = discord_tools
        self._current_guild = guild
        self._current_channel = channel
        self._event_loop = event_loop

    async def handle(self, user="", message="", server_name="Discord",
                     user_id="", progress_callback=None, tracker=None,
                     guild=None, channel=None, event_loop=None, discord_tools=None):
        if not message.strip():
            return ""
        for key, response in self.responses.items():
            if callable(key):
                if key(user, message):
                    return response
            elif isinstance(key, str) and key and key.lower() in message.lower():
                return response
        return self.default_response

    async def cognitize(self, message="", user_name="", is_directed=True,
                        is_dm=False, is_mentioned=False, params=None,
                        is_admin=False, has_guild=True, event_loop=None):
        return (MagicMock(), self.default_response)


@dataclass
class SimEnv:
    """Holds the full simulation world state."""

    guild: MagicMock = None
    channels: dict = field(default_factory=dict)
    members: dict = field(default_factory=dict)
    sent_messages: list = field(default_factory=list)
    agent: FakeAgent = None
    bot: MagicMock = None

    def setup(self):
        everyone_role = make_role("@everyone", is_everyone=True, position=0)
        member_role = make_role("Member", position=1)
        mod_role = make_role("Mod", position=50)
        admin_role = make_role("Admin", position=100)

        fred = make_member("Friendly Fred", id=1001, roles=[everyone_role, member_role])
        tom = make_member("Troll Tom", id=1002, roles=[everyone_role, member_role])
        amy = make_member("Admin Amy", id=1003, roles=[everyone_role, admin_role])
        bob = make_member("Bot Bob", id=1004, roles=[everyone_role, member_role])
        nelly = make_member("Newbie Nelly", id=1005, roles=[everyone_role, member_role])
        sam = make_member("Silent Sam", id=1006, roles=[everyone_role, member_role])
        carla = make_member("Confused Carla", id=1007, roles=[everyone_role, member_role])
        mike = make_member("Manager Mike", id=1008, roles=[everyone_role, member_role])
        bot_member = make_member("AzureBot", id=999, roles=[everyone_role, admin_role], bot=True)

        self.members = {
            "fred": fred, "tom": tom, "amy": amy, "bob": bob,
            "nelly": nelly, "sam": sam, "carla": carla, "mike": mike,
            "azurebot": bot_member,
        }

        general = make_channel("general", guild=None)
        admin_ch = make_channel("admin", guild=None)
        bot_cmds = make_channel("bot-commands", guild=None)
        voice_ch = make_channel("voice", ch_type=1, guild=None)

        self.channels = {
            "general": general, "admin": admin_ch,
            "bot-commands": bot_cmds, "voice": voice_ch,
        }

        for ch in self.channels.values():
            ch.send = AsyncMock(side_effect=self._capture_send)
            ch.typing = MagicMock(return_value=contextlib.nullcontext())
            ch.permissions_for = MagicMock(return_value=MagicMock(send_messages=True))

        self.guild = make_guild(
            name="TestGuild", id=12345, owner_id=amy.id,
            members=list(self.members.values()),
            text_channels=[general, admin_ch, bot_cmds],
            voice_channels=[voice_ch],
            roles=[everyone_role, member_role, mod_role, admin_role],
        )

        for m in self.members.values():
            m.guild = self.guild
        for ch in self.channels.values():
            ch.guild = self.guild

        self.guild.get_member = MagicMock(side_effect=lambda uid: next(
            (m for m in self.members.values() if m.id == uid), None
        ))
        self.guild.get_channel = MagicMock(side_effect=lambda cid: next(
            (ch for ch in self.channels.values() if ch.id == cid), None
        ))

        self.agent = FakeAgent()
        self.agent.llm = FakeLLM("Hello! I'm Azure. How can I help you today?")

        self.bot = MagicMock()
        self.bot.user = bot_member
        self.bot.get_channel = MagicMock(side_effect=self.guild.get_channel)
        self.bot.guilds = [self.guild]
        self.bot.application = MagicMock()

    def create_message(self, content, author_name="fred", channel_name="general",
                       mentions_bot=False):
        author = self.members[author_name]
        channel = self.channels[channel_name]
        msg = MagicMock()
        msg.id = abs(hash(content + author_name + channel_name)) % (2 ** 31)
        msg.content = content
        msg.author = author
        msg.channel = channel
        msg.guild = self.guild
        msg.mentions = [self.bot.user] if mentions_bot else []
        msg.attachments = []
        msg.webhook_id = None
        msg.type = _real_discord.MessageType.default
        msg.reply = AsyncMock()
        msg.add_reaction = AsyncMock()
        return msg

    def create_pipeline_message(self, content, author_name="fred", channel_name="general",
                                mentions_bot=False):
        """Create a message mock with all attributes on_message() needs.

        Adds channel.typing(), perms checks, guild wiring required by
        the full message_handler pipeline.
        """
        author = self.members[author_name]
        channel = self.channels[channel_name]
        guild = self.guild

        msg = MagicMock()
        msg.id = abs(hash(content + author_name + channel_name + "pipeline")) % (2 ** 31)
        msg.content = content
        msg.author = author
        msg.channel = channel
        msg.guild = guild
        msg.mentions = [self.bot.user] if mentions_bot else []
        msg.attachments = []
        msg.webhook_id = None
        msg.type = _real_discord.MessageType.default
        msg.reply = AsyncMock()
        msg.add_reaction = AsyncMock()

        author.guild_permissions = MagicMock()
        author.guild_permissions.administrator = author_name in ("amy", "azurebot")
        guild.owner_id = self.members["amy"].id
        guild.get_member = MagicMock(return_value=author)
        guild.me = self.members["azurebot"]
        guild.text_channels = list(self.channels.values())[:3]

        channel.typing = MagicMock(return_value=contextlib.nullcontext())
        channel.permissions_for = MagicMock(return_value=MagicMock(send_messages=True))
        channel.send = AsyncMock(side_effect=self._capture_send)
        return msg

    def _capture_send(self, *args, **kwargs):
        msg = MagicMock()
        msg.id = 50000 + len(self.sent_messages)
        msg.content = args[0] if args else kwargs.get("content", "")
        def _edit_side(*a, **kw):
            new_content = kw.get("content", a[0] if a else None)
            if new_content is not None:
                msg.content = new_content
        msg.edit = AsyncMock(side_effect=_edit_side)
        msg.add_reaction = AsyncMock()
        msg.delete = AsyncMock()
        msg.channel = MagicMock()
        self.sent_messages.append(msg)
        return msg

    def last_response(self):
        return self.sent_messages[-1].content if self.sent_messages else None

    def reset(self):
        self.sent_messages.clear()
