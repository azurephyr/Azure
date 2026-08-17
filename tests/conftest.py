"""Shared discord mock infrastructure for test files that need it.

Usage in test file (at module level, BEFORE azure imports):
    from tests.conftest import REAL_DISCORD as _real_discord, MOCK as _discord_mock, reset_utils_get
    _orig = sys.modules.pop("discord", None)
    sys.modules["discord"] = _discord_mock
    reset_utils_get()
    # ... import azure modules ...
    if _orig is not None:
        sys.modules["discord"] = _orig
"""

import sys as _sys
from unittest.mock import MagicMock

import discord as _real

REAL_DISCORD = _real

MOCK = MagicMock()
MOCK.Discord = MagicMock
MOCK.TextChannel = _real.TextChannel
MOCK.VoiceChannel = _real.VoiceChannel
MOCK.CategoryChannel = _real.CategoryChannel
MOCK.ForumChannel = _real.ForumChannel
MOCK.StageChannel = _real.StageChannel
MOCK.Thread = _real.Thread
MOCK.DMChannel = _real.DMChannel
MOCK.GuildChannel = _real.abc.GuildChannel
MOCK.abc = _real.abc
MOCK.ChannelType = _real.ChannelType
MOCK.VerificationLevel = _real.VerificationLevel
MOCK.NotificationLevel = _real.NotificationLevel
MOCK.Status = _real.Status
MOCK.MFALevel = MagicMock()
MOCK.MFALevel.none = 0
MOCK.MFALevel.required = 1
MOCK.VoiceRegion = MagicMock(side_effect=lambda region: region)
MOCK.ExplicitContentFilter = _real.ContentFilter
MOCK.ContentFilter = _real.ContentFilter
MOCK.EventStatus = _real.EventStatus
MOCK.Embed = _real.Embed
MOCK.File = _real.File
MOCK.ForumTag = _real.ForumTag
MOCK.WelcomeChannel = _real.WelcomeChannel
MOCK.WelcomeScreen = _real.WelcomeScreen
MOCK.PermissionOverwrite = _real.PermissionOverwrite
MOCK.Permissions = _real.Permissions
MOCK.Colour = _real.Colour
MOCK.Object = _real.Object

MOCK.__path__ = []
MOCK.__package__ = "discord"
MOCK.__spec__ = None

MOCK.utils = MagicMock()
def _utils_get_side_effect(collection, **attrs):
    return (
    next((o for o in collection if all(getattr(o, k, None) == v for k, v in attrs.items())), None)
    if isinstance(collection, list)
    else None
)
MOCK.utils.get = MagicMock(side_effect=_utils_get_side_effect)


def reset_utils_get():
    """Restore the default side_effect-based utils.get (tests may override)."""
    MOCK.utils.get = MagicMock(side_effect=_utils_get_side_effect)


class _MockLoop:
    """Real class so isinstance(_, discord.ext.tasks.Loop) works.
    Behaves like a real discord Loop: wraps a function and has hook attrs."""
    def __init__(self, func=None, **kwargs):
        self.func = func
        self.before_loop = lambda f: f
        self.after_loop = lambda f: f
        self.error = lambda f: f
        self.start = MagicMock()
        self.stop = MagicMock()
        self.cancel = MagicMock()
        self.restart = MagicMock()
        self.is_running = MagicMock(return_value=False)
        self.is_finished = MagicMock(return_value=False)
        self.delta = None
        self.hours = kwargs.get("hours")
        self.minutes = kwargs.get("minutes")
        self.seconds = kwargs.get("seconds")
        self.time = None
        self.count = None
        self.reconnect = True
    def __call__(self, func=None):
        if func is not None:
            self.func = func
            return self
        return self
    def __repr__(self):
        return f"<MockLoop func={self.func.__name__ if self.func else None}>"


_subs = [
    "ext", "ext.commands", "ext.tasks", "app_commands",
    "webhook", "webhook.async_", "player", "opus", "voice_client",
]
_registry: dict = {}
for _sub in _subs:
    _name = f"discord.{_sub}"
    if _name not in _sys.modules:
        if _sub == "ext.tasks":
            _m = MagicMock(__name__=_name, __package__="discord")
            _m.Loop = _MockLoop
            _m.loop = MagicMock(return_value=_MockLoop())
        else:
            _m = MagicMock(__name__=_name, __package__="discord")
        _sys.modules[_name] = _m
        _registry[_name] = _m
for _name, _m in _registry.items():
    _parts = _name.split(".")
    if len(_parts) >= 2:
        _parent_name = ".".join(_parts[:-1])
        _parent = _registry.get(_parent_name) or _sys.modules.get(_parent_name)
        if _parent is not None:
            setattr(_parent, _parts[-1], _m)

# FastAPI 0.14x can expose internal included-router objects in app.routes
# that do not carry the public Starlette Route `.path` attribute. The test
# suite only needs route-path introspection, so give that internal object a
# harmless placeholder rather than relying on a private framework detail.
try:
    from fastapi.routing import _IncludedRouter
    if not hasattr(_IncludedRouter, "path"):
        _IncludedRouter.path = ""
except (ImportError, AttributeError):
    pass

collect_ignore = ["test_hardcore_stress.py"]
