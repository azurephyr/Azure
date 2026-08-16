"""
Azure Discord Agentic Management Tools — Expanded

Comprehensive Discord server management with ALL Discord features:
  Roles, Channels, Categories, Members, Permissions, Webhooks,
  Invites, Server Settings, Scheduled Events, AutoMod, Threads,
  Stage Channels, Forum Channels, Message pins, Voice management.

Features:
  - Parallel execution (faster plans)
  - Retry with exponential backoff
  - Pre-flight permission checks
  - Live progress embeds
  - Natural language confirmation
  - Undo/Redo with change tracking
  - Server templates integration
  - Server health analysis

Usage:
    from azure.discord_tools_expanded import DiscordManagementTools
    tools = DiscordManagementTools(bot)
    plan = await tools.generate_plan(guild, "make the server good", llm)
    await tools.execute_plan(guild, plan, ctx)
"""

from __future__ import annotations

from pathlib import Path

from .change_tracker import ChangeTracker
from .server_health import ServerHealthAnalyzer
from .server_templates import ServerTemplateManager
from .tools.channel_tools import ChannelToolsMixin
from .tools.member_tools import MemberToolsMixin
from .tools.plan_tools import PlanToolsMixin
from .tools.progress_tools import ProgressToolsMixin
from .tools.role_tools import RoleToolsMixin
from .tools.server_tools import ServerToolsMixin


class DiscordManagementTools(
    RoleToolsMixin, ChannelToolsMixin, MemberToolsMixin,
    PlanToolsMixin, ServerToolsMixin, ProgressToolsMixin
):
    """Comprehensive Discord server management toolkit."""

    def __init__(self, bot):
        self.bot = bot
        self.tracker = ChangeTracker(log_dir=Path("logs/changes"))
        self.templates = ServerTemplateManager(template_dir=Path("templates"))
        self.health = ServerHealthAnalyzer()
        self.templates.create_default_templates()

        try:
            from .self_repair import SelfRepair
            self.repair = SelfRepair()
        except ImportError:
            self.repair = None

        try:
            from .task_manager import TaskManager
            self.tasks = TaskManager()
        except ImportError:
            self.tasks = None
