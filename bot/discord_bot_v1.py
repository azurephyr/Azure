"""
Azure Discord Bot (Phase 7-8: Chat + Autonomous Moderation).

This is the Discord integration layer for Azure. It supports two modes:

  CHAT MODE:        conversational replies via the language model
  MODERATION MODE:  autonomous spam/scam/toxicity detection and action

Architecture:
  - on_message:     routes to chat engine AND moderation engine
  - moderation:     classifies -> decides -> acts -> reports
  - admin commands: !mod_mode, !mod_stats, !mod_scan, !mod_report, !mod_feedback

Safety:
  - Moderation starts in DRY_RUN phase (classify only, no actions)
  - Phase escalation requires data-driven evidence (readiness report)
  - reactive_limited allows: delete, warn, timeout (<=5 min)
  - reactive_full allows: all actions including kick/ban/lockdown
  - All actions are logged to moderation_actions.jsonl
  - Rate limits prevent runaway moderation

To use:
  1. pip install discord.py
  2. Create a Discord bot at https://discord.com/developers
  3. Set AZURE_DISCORD_TOKEN in your environment
  4. Invite the bot to your server with these permissions:
     - moderate_members, manage_messages, kick_members, ban_members
  5. python discord_bot_v1.py
  6. Use !mod_phase dry_run to test classification
  7. Use !mod_readiness to check if Azure can escalate
  8. Use !mod_phase reactive_limited to enable limited actions
  9. Use !mod_phase reactive_full only after proving limited mode works
"""

# ruff: noqa: E402  -- Imports are intentionally placed after the discord availability
#                       check. If discord is not installed, the process exits immediately,
#                       so all subsequent imports are only reached when discord is present.

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger("azure.discord")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.context import ctx  # noqa: F401

# We import discord lazily so the rest of the project still runs without it
try:
    import discord
    from discord import app_commands
    from discord.ext import commands, tasks
except Exception as e:
    logger.error(f"Import failed: {e}")
    logger.info("discord.py is not installed.")

    logger.info("Install it with:  pip install discord.py")

    sys.exit(1)

from azure.agent import AzureAgent
from azure.model_selector import ModelSelector
from azure.recovery.integration import with_agre_recovery_async

# Agentic Discord management tools (optional import)
try:
    from azure.discord_tools_expanded import DiscordManagementTools
except Exception as e:
    logger.error(f"Import failed: {e}")
    # Fallback to basic tools
    try:
        from azure.discord_tools import DiscordManagementTools
    except Exception as e:
        logger.error(f"Import failed: {e}")
        DiscordManagementTools = None

# Health check server
try:
    from azure.health_server import HealthServer
except Exception as e:
    logger.error(f"Import failed: {e}")
    HealthServer = None

# Multi-server config manager
try:
    from azure.server_config import ServerConfigManager
except Exception as e:
    logger.error(f"Import failed: {e}")
    ServerConfigManager = None

# Natural language intent classifier (keyword-based fallback)
try:
    from azure.intent_classifier import IntentClassifier
except Exception as e:
    logger.error(f"Import failed: {e}")
    IntentClassifier = None

# LLM Tool Engine (smart tool selection - no keywords)
try:
    from azure.tool_engine import ToolEngine
except Exception as e:
    logger.error(f"Import failed: {e}")
    ToolEngine = None

# Cognitive pipeline (Phase A — 10-phase reasoning system)
try:
    from azure.cognition import CognitivePipeline, CognitiveState, VisionRouter
except Exception as e:
    logger.error(f"Import failed: {e}")
    CognitivePipeline = None
    CognitiveState = None
    VisionRouter = None

# Task Manager (uninterruptible task execution)
try:
    from azure.task_manager import TaskManager
except Exception as e:
    logger.error(f"Import failed: {e}")
    TaskManager = None

# Self-repair system
try:
    from azure.self_repair import SelfRepair
except Exception as e:
    logger.error(f"Import failed: {e}")
    SelfRepair = None

# New v3 intelligence modules
try:
    from azure.game_master import GameMaster
except Exception as e:
    logger.error(f"Import failed: {e}")
    GameMaster = None

try:
    from azure.document_intelligence import DocumentIntelligence
except Exception as e:
    logger.error(f"Import failed: {e}")
    DocumentIntelligence = None

try:
    from azure.voice_system import VoiceConfig, VoiceSystem
except Exception as e:
    logger.error(f"Import failed: {e}")
    VoiceSystem = None
    VoiceConfig = None

try:
    from azure.channel_lifecycle import ChannelLifecycleManager
except Exception as e:
    logger.error(f"Import failed: {e}")
    ChannelLifecycleManager = None

try:
    from azure.plugins import PluginManager
except Exception as e:
    logger.error(f"Import failed: {e}")
    PluginManager = None

try:
    from azure.integrations import IntegrationHub, create_integration_hub
except Exception as e:
    logger.error(f"Import failed: {e}")
    IntegrationHub = None
    create_integration_hub = None

try:
    from azure.vision_processor import VisionProcessor
except Exception as e:
    logger.error(f"Import failed: {e}")
    VisionProcessor = None

# Live Intelligence System (v3)
try:
    from azure.live_commands import setup_live_commands
    from azure.live_intelligence import LiveIntelligence
except Exception as e:
    logger.error(f"Import failed: {e}")
    LiveIntelligence = None
    setup_live_commands = None

# Visual response system (thinking animations, rich embeds)
try:
    from azure.discord_responses import (
        PLANNING_PHASES,
        THINKING_PHASES,
        EmbedBuilder,
        ThinkingAnimation,
        callout_block,
        error_embed,
        format_reply,
        health_embed,
        info_embed,
        memory_reveal_embed,
        plan_embed,
        short_reply,
        success_embed,
    )
except Exception as e:
    logger.error(f"Import failed: {e}")
    ThinkingAnimation = None
    EmbedBuilder = None
    # Fallback — module not installed
    def format_reply(t): return t
    def short_reply(t, n=""): return t
    def callout_block(t, k="note"): return t
    def success_embed(t, d=""): return None
    def error_embed(t, d=""): return None
    def info_embed(t, d="", f=None): return None
    def health_embed(s): return None
    def plan_embed(steps, req): return None
    def memory_reveal_embed(q, r): return None
    THINKING_PHASES = None
    PLANNING_PHASES = None

# Handler module imports
from .handlers.case_handler import register_case_commands
from .handlers.command_handler import register_commands
from .handlers.config_handler import register_config_commands
from .handlers.dead_chat_handler import register_revival_commands
from .handlers.ghost_handler import register_ghost_commands
from .handlers.llm_handler import _llm_response
from .handlers.message_handler import (
    _rotate_cognition_logs,
    handle_bot_message_reaction,
)
from .handlers.moderation_handler import register_moderation_commands
from .handlers.onboarding_handler import register_discord_tools
from .handlers.settings_handler import register_settings
from .handlers.reputation_handler import (
    check_reputation_on_join,
    register_reputation_commands,
)
from .handlers.trace_handler import register_trace_commands

# Global instances (set in setup)
MODEL_SELECTOR = None
MGMT_TOOLS = None
INTENT_CLASSIFIER = None
TOOL_ENGINE = None
HEALTH_SERVER = None
SERVER_CONFIGS = None
TASK_MANAGER = None
REPAIR = None
GAME_MASTER = None
DOC_INTEL = None
VOICE_SYSTEM = None
CHANNEL_LIFECYCLE = None
PLUGIN_MANAGER = None
INTEGRATION_HUB = None
VISION_PROCESSOR = None
PROACTIVE_ENGINE = None
LIVE_INTELLIGENCE = None  # Live Intelligence System
MODERATION_SERVICE = None  # Transport-agnostic moderation service

# Cognitive pipeline (Phase A)
COGNITIVE_PIPELINE = None
VISION_ROUTER = VisionRouter() if VisionRouter is not None else None
from bot.features import load_feature_flags  # noqa: E402
_FEATURES = load_feature_flags()
COGNITIVE_MODE = _FEATURES.cognitive
COGNITIVE_LOG_DIR = ROOT / "logs" / "cognition"

# Milestone 3: Cron Scheduler
CRON_SCHEDULER = None

# Track last cognitive state per user (for !azure_cognition)
_last_cognitive_state: dict[str, CognitiveState] = {}  # type: ignore[name-defined]

# Persistent cognitive panel per server (guild_id → discord.Message.id)
_cognition_panel_messages: dict[str, int] = {}

# Semantic reasoning confidence threshold (configurable via env)
SEMANTIC_THRESHOLD: float = float(
    os.environ.get("AZURE_SEMANTIC_THRESHOLD", "0.75")
)

# Rate limiting for on_message handler (prevents spam/abuse)
from collections import OrderedDict

_rate_limit_buckets = OrderedDict()  # LRU cache: user_id -> list of timestamps
_rate_limit_lock = asyncio.Lock()  # Protects rate limit bucket mutations
MAX_RATE_LIMIT_ENTRIES = int(os.environ.get("AZURE_RATE_LIMIT_CACHE_SIZE", "1000"))  # Prevent memory leak
RATE_LIMIT_WINDOW = float(os.environ.get("AZURE_RATE_LIMIT_WINDOW", "60.0"))  # seconds
RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("AZURE_RATE_LIMIT_MAX", "10"))  # max messages per user per window
RATE_LIMIT_COOLDOWN = float(os.environ.get("AZURE_RATE_LIMIT_COOLDOWN", "30.0"))  # cooldown period after hitting limit

# Additional configurable constants
CHUNK_SIZE = int(os.environ.get("AZURE_CHUNK_SIZE", "1900"))
DELETE_AFTER_SECONDS = int(os.environ.get("AZURE_DELETE_AFTER", "10"))
LOG_MAX_AGE_DAYS = int(os.environ.get("AZURE_LOG_MAX_AGE", "7"))
DEFAULT_MAX_TOKENS = int(os.environ.get("AZURE_DEFAULT_MAX_TOKENS", "150"))
DEFAULT_TEMPERATURE = float(os.environ.get("AZURE_DEFAULT_TEMPERATURE", "0.7"))
DEFAULT_LOOKBACK_HOURS = int(os.environ.get("AZURE_LOOKBACK_HOURS", "72"))
CONFIRM_TIMEOUT = float(os.environ.get("AZURE_CONFIRM_TIMEOUT", "30.0"))
SETUP_TIMEOUT = float(os.environ.get("AZURE_SETUP_TIMEOUT", "60.0"))
MOD_LOOKBACK_HOURS = int(os.environ.get("AZURE_MOD_LOOKBACK_HOURS", "24"))
MAX_USER_FACTS = int(os.environ.get("AZURE_MAX_USER_FACTS", "5"))
RAG_TOP_K = int(os.environ.get("AZURE_RAG_TOP_K", "3"))
MAX_GOAL_DESC_LENGTH = int(os.environ.get("AZURE_MAX_GOAL_DESC_LENGTH", "100"))
CACHE_TOP_N = int(os.environ.get("AZURE_CACHE_TOP_N", "5"))
COGNITION_LOG_LIMIT = int(os.environ.get("AZURE_COGNITION_LOG_LIMIT", "5"))
MAX_STEPS_PREVIEW = int(os.environ.get("AZURE_MAX_STEPS_PREVIEW", "12"))
PROGRESS_LAST_N = int(os.environ.get("AZURE_PROGRESS_LAST_N", "5"))
AUTONOMOUS_SCAN_INTERVAL = int(os.environ.get("AZURE_AUTONOMOUS_SCAN_INTERVAL", "30"))
PERIODIC_SCAN_INTERVAL = int(os.environ.get("AZURE_PERIODIC_SCAN_INTERVAL", "5"))
TRUNC_LABEL = int(os.environ.get("AZURE_TRUNC_LABEL", "40"))
TRUNC_DESC = int(os.environ.get("AZURE_TRUNC_DESC", "100"))
TRUNC_PREVIEW = int(os.environ.get("AZURE_TRUNC_PREVIEW", "200"))
TRUNC_SMALL = int(os.environ.get("AZURE_TRUNC_SMALL", "80"))
TRUNC_VIOLATIONS = int(os.environ.get("AZURE_TRUNC_VIOLATIONS", "2"))
TRUNC_USER_FACTS = int(os.environ.get("AZURE_TRUNC_USER_FACTS", "5"))
TRUNC_RAG_LINES = int(os.environ.get("AZURE_TRUNC_RAG_LINES", "3"))
TRUNC_PHASE_LINES = int(os.environ.get("AZURE_TRUNC_PHASE_LINES", "8"))
TRUNC_RISK_TOP_USERS = int(os.environ.get("AZURE_TRUNC_RISK_TOP_USERS", "5"))
TRUNC_RISK_TOP_CHANNELS = int(os.environ.get("AZURE_TRUNC_RISK_TOP_CHANNELS", "3"))
TRUNC_CACHE_TOP = int(os.environ.get("AZURE_TRUNC_CACHE_TOP", "5"))
TRUNC_GOALS_DISPLAY = int(os.environ.get("AZURE_TRUNC_GOALS_DISPLAY", "3"))
TRUNC_RAG_RESULTS = int(os.environ.get("AZURE_TRUNC_RAG_RESULTS", "3"))
TRUNC_TOPICS = int(os.environ.get("AZURE_TRUNC_TOPICS", "5"))
TRUNC_SCHEDULE_LIST = int(os.environ.get("AZURE_TRUNC_SCHEDULE_LIST", "40"))
TRUNC_PLAN_STEPS = int(os.environ.get("AZURE_TRUNC_PLAN_STEPS", "12"))
TRUNC_PROGRESS_STEPS = int(os.environ.get("AZURE_TRUNC_PROGRESS_STEPS", "5"))
TRUNC_RESPONSE_DISPLAY = int(os.environ.get("AZURE_TRUNC_RESPONSE_DISPLAY", "300"))
TRUNC_REPAIR_MSG = int(os.environ.get("AZURE_TRUNC_REPAIR_MSG", "80"))
TRUNC_FEEDBACK_PREVIEW = int(os.environ.get("AZURE_TRUNC_FEEDBACK_PREVIEW", "100"))
TRUNC_PLAN_PREVIEW = int(os.environ.get("AZURE_TRUNC_PLAN_PREVIEW", "200"))
TRUNC_EPISODE = int(os.environ.get("AZURE_TRUNC_EPISODE", "200"))
TRUNC_PENDING = int(os.environ.get("AZURE_TRUNC_PENDING", "10"))
TRUNC_STEPS_PREVIEW = int(os.environ.get("AZURE_TRUNC_STEPS_PREVIEW", "12"))
TRUNC_CRON_NAME = int(os.environ.get("AZURE_TRUNC_CRON_NAME", "50"))

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True   # required to read message text
intents.guilds = True
intents.messages = True
intents.members = True            # needed for moderation actions
intents.presences = True          # needed for online/activity awareness

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    description="Azure v2 — Chat + Autonomous Moderation",
)

# Admin channel for moderation reports (set via !mod_channel or env)
ADMIN_CHANNEL_ID = os.environ.get("AZURE_ADMIN_CHANNEL_ID")
ADMIN_CHANNEL: discord.TextChannel | None = None

# ---------------------------------------------------------------------------
# Chat restriction configuration
# ---------------------------------------------------------------------------
# Who can talk to Azure? (chat mode, not moderation)
#   "anyone"          -> responds to DMs and @mentions from anyone
#   "owner_only"      -> only responds to the server owner + DMs from owner
#   "specific_users"  -> only responds to comma-separated user IDs in AZURE_ALLOWED_USERS
#   "dm_only"         -> only responds in DMs, ignores @mentions in servers
#   "mention_only"    -> only responds when @mentioned, ignores DMs
#
CHAT_MODE = os.environ.get("AZURE_CHAT_MODE", "anyone").lower().strip()
ALLOWED_USERS_RAW = os.environ.get("AZURE_ALLOWED_USERS", "")
ALLOWED_USER_IDS = {u.strip() for u in ALLOWED_USERS_RAW.split(",") if u.strip()}

# Owner detection cache
OWNER_ID: int | None = None

APP_COMMANDS_SYNCED = False


# is_owner, is_allowed_to_chat moved to handlers/message_handler.py

import contextlib

from bot.background_executor import BackgroundExecutor

# ---------------------------------------------------------------------------
# Globals, populated in setup()
AGENT: AzureAgent | None = None
MODEL = None
TOK = None
CHECKPOINT_PATH = ROOT / "checkpoints" / "AZURE_v1_best.pt"
LOG_DIR = ROOT / "logs"
BG_EXECUTOR: BackgroundExecutor | None = None

# register_discord_tools moved to handlers/onboarding_handler.py

# ---------------------------------------------------------------------------
# JARVIS Integration
# ---------------------------------------------------------------------------
try:
    from azure.jarvis_integration import init_jarvis
    JARVIS_ENABLED = True
except Exception:
    JARVIS_ENABLED = False
    logger.debug("[azure] JARVIS integration not installed (optional)")


JARVIS_BOT = None

# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@bot.event
async def on_ready():
    global JARVIS_BOT

    # Track bot start time for uptime calculation
    from datetime import datetime
    if not hasattr(bot, 'start_time'):
        bot.start_time = datetime.now()

    # Initialize JARVIS interface
    if JARVIS_ENABLED and _FEATURES.jarvis and AGENT:
        try:
            JARVIS_BOT = init_jarvis(bot, AGENT)
            await JARVIS_BOT.on_ready()
        except Exception as e:
            logger.error(f"[azure] JARVIS init error: {e}")

    logger.info(f"[azure] logged in as {bot.user} (id={bot.user.id})")

    logger.info(f"[azure] connected to {len(bot.guilds)} guild(s)")

    for g in bot.guilds:
        logger.info(f"  - {g.name} ({g.member_count} members)")

    await _sync_app_commands_once()

    # Milestone 2: Server Knowledge Base caching
    if COGNITIVE_PIPELINE and hasattr(COGNITIVE_PIPELINE, 'server_knowledge'):
        for guild in bot.guilds:
            channels = []
            for ch in guild.channels:
                if isinstance(ch, discord.TextChannel):
                    channels.append({
                        "name": ch.name,
                        "id": str(ch.id),
                        "type": "text",
                        "purpose": ch.topic or "No topic set"
                    })
            roles = []
            for r in guild.roles:
                roles.append({
                    "name": r.name,
                    "id": str(r.id),
                    "position": r.position
                })
            COGNITIVE_PIPELINE.server_knowledge.update_server_state(
                guild.name, channels, roles, guild.member_count
            )
            logger.info(f"[azure] cached knowledge base for server: {guild.name}")

    # Resolve admin channel and publish into shared ctx for background tasks
    global ADMIN_CHANNEL
    if ADMIN_CHANNEL_ID:
        for guild in bot.guilds:
            ch = guild.get_channel(int(ADMIN_CHANNEL_ID))
            if ch:
                ADMIN_CHANNEL = ch
                ctx.admin_channel = ch
                logger.info(f"[azure] admin report channel: {ch.name}")
                break

    flags = ctx.features if ctx.features else _FEATURES

    # Wire the moderation engine to the bot
    if AGENT and AGENT.moderation:
        AGENT.set_moderation_bot(bot)
        # Start periodic scan if proactive
        if AGENT.moderation.policy.mode == "proactive" and not periodic_scan.is_running():
            periodic_scan.start()
        # Start Phase Alpha autonomous scan (always runs for temporal analysis)
        if flags.autonomous and not autonomous_scan_task.is_running():
            autonomous_scan_task.start()
        logger.info(f"[azure] moderation phase: {AGENT.moderation.policy.phase.value}")
        logger.info(f"[azure] {AGENT.moderation.policy.get_phase_description()}")
        if flags.autonomous:
            logger.info("[azure] autonomous scan task: ACTIVE (30s interval)")

    # Autonomous loops only when feature flags allow
    if flags.autonomous and flags.cognitive and not autonomous_agent_loop.is_running():
        autonomous_agent_loop.start()
        logger.info("[azure] autonomous agent heartbeat: ACTIVE (30m interval)")

    if flags.autonomous and flags.cognitive and not goal_executor_loop.is_running():
        goal_executor_loop.start()
        logger.info("[azure] goal executor: ACTIVE (2m interval)")

    # Milestone 3: Start cron scheduler loop and publish into ctx
    global CRON_SCHEDULER
    try:
        from azure.cron_scheduler import CronScheduler
        if CRON_SCHEDULER is None:
            CRON_SCHEDULER = CronScheduler()
        ctx.cron_scheduler = CRON_SCHEDULER
        if flags.cron and not cron_check_loop.is_running():
            cron_check_loop.start()
        logger.info(f"[azure] cron scheduler: ACTIVE ({len(CRON_SCHEDULER.tasks)} tasks loaded)")
    except Exception as e:
        logger.info(f"[azure] cron scheduler unavailable: {e}")

    # Ghost mute maintenance
    if flags.ghost_loop and not ghost_maintenance_loop.is_running():
        ghost_maintenance_loop.start()
        logger.info("[azure] ghost maintenance: ACTIVE (5m interval)")

    # Dead chat revival scanning
    if flags.revival and not revival_scan_loop.is_running():
        revival_scan_loop.start()
        logger.info("[azure] revival scan: ACTIVE (3m interval)")

    # Keep shared ctx in sync with runtime state resolved after Discord connects
    ctx.bot = bot
    ctx.start_time = getattr(bot, "start_time", None)
    if AGENT is not None:
        ctx.agent = AGENT
    if COGNITIVE_PIPELINE is not None:
        ctx.cognitive_pipeline = COGNITIVE_PIPELINE
    if ctx.core_ready():
        ctx.discord_connected = True
        ctx.mark_ready()
    logger.info(
        "[azure] on_ready complete — core_ready=%s guilds=%d",
        ctx.core_ready(),
        len(bot.guilds),
    )


async def _sync_app_commands_once() -> None:
    """Sync registered slash commands once after Discord is ready."""
    global APP_COMMANDS_SYNCED
    if APP_COMMANDS_SYNCED:
        return

    mode = os.environ.get("AZURE_SLASH_SYNC_SCOPE", "guild").strip().lower()
    if mode in {"0", "false", "no", "off", "disabled", "none"}:
        logger.info("[azure] slash command sync disabled (AZURE_SLASH_SYNC_SCOPE=%s)", mode)
        APP_COMMANDS_SYNCED = True
        return

    try:
        if mode == "global":
            synced = await bot.tree.sync()
            APP_COMMANDS_SYNCED = True
            logger.info("[azure] synced %d global slash command(s)", len(synced))
            return

        guilds = list(bot.guilds)
        if not guilds:
            logger.info("[azure] no guilds available for slash command sync yet")
            return

        total = 0
        for guild in guilds:
            target = discord.Object(id=guild.id)
            bot.tree.copy_global_to(guild=target)
            synced = await bot.tree.sync(guild=target)
            total += len(synced)
            logger.info("[azure] synced %d slash command(s) for guild %s (%s)", len(synced), guild.name, guild.id)

        APP_COMMANDS_SYNCED = True
        logger.info("[azure] slash command sync complete (%d command registration(s), scope=%s)", total, mode)
    except Exception:
        logger.exception("[azure] slash command sync failed")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Surface slash-command failures to the operator instead of failing silently."""
    await _handle_app_command_error(interaction, error)


async def _handle_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Shared app-command error implementation, kept directly testable."""
    original = getattr(error, "original", error)
    command_name = getattr(getattr(interaction, "command", None), "qualified_name", "unknown")
    logger.error(
        "[azure] slash command /%s failed",
        command_name,
        exc_info=(type(original), original, getattr(original, "__traceback__", None)),
    )

    if isinstance(error, app_commands.CommandOnCooldown):
        detail = f"Command is on cooldown. Try again in {error.retry_after:.1f}s."
    elif isinstance(error, app_commands.MissingPermissions):
        detail = "You do not have the required Discord permissions for that command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        detail = "Azure is missing the Discord permissions required to run that command."
    elif isinstance(error, app_commands.CheckFailure):
        detail = "You are not allowed to use that command here."
    else:
        detail = "That command hit an internal error. Check the bot logs for details."

    message = f"Command failed: {detail}"
    try:
        response = getattr(interaction, "response", None)
        if response and not response.is_done():
            await response.send_message(message, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)
    except Exception:
        logger.exception("[azure] failed to report slash command error to Discord")


# Background loops are defined in bot/tasks.py (ctx-based, testable).
# These imports replace the previous inline definitions that used module-level
# globals — the tasks.py versions read from bot.context.ctx which is populated
# by setup()/_populate_ctx().
from bot.tasks import (
    autonomous_agent_loop,
    autonomous_scan_task,
    cron_check_loop,
    ghost_maintenance_loop,
    goal_executor_loop,
    periodic_scan,
    revival_scan_loop,
)


def _get_thinking_temperature(depth) -> str:
    """Return the LLM temperature string for a given ThinkingDepth level."""
    temps = {
        "FAST": float(os.environ.get("AZURE_TEMP_FAST", "0.5")),
        "NORMAL": float(os.environ.get("AZURE_TEMP_NORMAL", "0.7")),
        "DEEP": float(os.environ.get("AZURE_TEMP_DEEP", "0.6")),
        "MAXIMUM": float(os.environ.get("AZURE_TEMP_MAXIMUM", "0.5")),
    }
    return temps.get(depth.value, "?")


# ---------------------------------------------------------------------------
# Message Dispatch (on_message entry point)
# ---------------------------------------------------------------------------

async def _dispatch_message(message):
    """Route an incoming message to prefix commands then the NL pipeline.

    Overriding on_message replaces discord.py's default, which is what
    dispatches registered @bot.command handlers. We must call
    process_commands() ourselves or every prefix command (!ping, !tools,
    !mod_scan, …) silently stops working.

    Kept as a module-level function (separate from the @bot.event wrapper)
    so it stays directly testable.
    """
    # Dispatch registered prefix commands first.
    await bot.process_commands(message)

    # Then run the natural-language pipeline. It skips command-prefixed
    # messages internally so a "!command" isn't also answered as chat.
    from .handlers.message_handler import on_message as _handle_message
    await _handle_message(message)


@bot.event
@with_agre_recovery_async("Process Discord message")
async def on_message(message):
    """Route to handler module."""
    await _dispatch_message(message)


@bot.event
async def on_disconnect():
    """Make readiness fail immediately when the gateway disconnects."""
    ctx.discord_connected = False


@bot.event
async def on_resumed():
    """Restore runtime readiness after a successful gateway resume."""
    ctx.discord_connected = True


# ---------------------------------------------------------------------------
# v3: Discord Event Handlers (Auto-trigger systems)
# ---------------------------------------------------------------------------

@bot.event
async def on_member_join(member):
    """Handle new member joins."""
    await check_reputation_on_join(member)
    proactive = ctx.proactive_engine if ctx.proactive_engine is not None else PROACTIVE_ENGINE
    if proactive is not None and member.guild:
        try:
            # Check if we should suggest a welcome
            suggestions = proactive.generate_suggestions(
                str(member.guild.id),
                lookback_hours=int(os.environ.get("AZURE_WELCOME_LOOKBACK_HOURS", "1")),
            )
            if suggestions and suggestions[0].confidence > 0.8:
                system_channel = member.guild.system_channel
                if system_channel:
                    msg = await _llm_response(
                        f"New member '{member.display_name}' joined the server. Generate a brief welcome message.",
                        f"👋 Welcome {member.mention}! Check out #welcome and #rules to get started.",
                        max_tokens=60
                    )
                    if msg:
                        await system_channel.send(msg)
        except Exception as e:
            logger.info(f"[azure] welcome suggestion error: {e}")


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup(checkpoint_path: Path = CHECKPOINT_PATH, moderation_phase: str = "dry_run"):
    global AGENT, MODEL, TOK, LLM, COGNITIVE_MODE, _FEATURES

    _FEATURES = load_feature_flags()
    ctx.ready = False
    ctx.discord_connected = False
    ctx.shutting_down = False
    COGNITIVE_MODE = _FEATURES.cognitive
    ctx.set_feature_flags(_FEATURES)
    logger.info(
        "[azure] feature flags: %s",
        ", ".join(_FEATURES.enabled_names()) or "(core only)",
    )

    # LLM Configuration: Supports local model OR cloud API
    local_llm_path = os.environ.get("AZURE_MODEL_PATH")
    if local_llm_path and local_llm_path.strip().lower() in {"none", "off", "false", "0"}:
        local_llm_path = None

    # Check if we have EITHER a local model OR any supported cloud API key
    has_local_model = local_llm_path is not None
    _api_key_env_names = (
        "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY",
        "ANTHROPIC_API_KEY", "AZURE_ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY", "AZURE_GOOGLE_API_KEY", "GEMINI_API_KEY",
        "GROQ_API_KEY", "AZURE_GROQ_API_KEY",
        "MISTRAL_API_KEY", "AZURE_MISTRAL_API_KEY",
        "OPENROUTER_API_KEY", "AZURE_OPENROUTER_API_KEY",
        "NARAROUTER_API_KEY", "AZURE_NARAROUTER_API_KEY",
    )
    has_api_key = any(os.environ.get(k) for k in _api_key_env_names)
    if not has_api_key:
        try:
            from azure.api_llm import ApiLLM as _ApiLLMDetect
            has_api_key = _ApiLLMDetect._detect_provider() is not None
        except Exception:
            pass

    if not has_local_model and not has_api_key:
        raise RuntimeError(
            "No LLM configured. You need EITHER:\n"
            "  1. Local model: Set AZURE_MODEL_PATH in .env\n"
            "     Download with: python scripts/download_model.py\n"
            "  OR\n"
            "  2. Cloud API: Set one of these in .env:\n"
            "     - OPENAI_API_KEY / ANTHROPIC_API_KEY / GOOGLE_API_KEY\n"
            "     - OPENROUTER_API_KEY / NARAROUTER_API_KEY\n"
            "     - GROQ_API_KEY / MISTRAL_API_KEY\n"
            "     (AZURE_* prefixed variants also work)\n"
        )

    # Validate local model path if provided
    if has_local_model:
        model_path_obj = Path(local_llm_path)
        if not model_path_obj.is_absolute():
            model_path_obj = ROOT / model_path_obj

        if not model_path_obj.exists():
            if has_api_key:
                logger.warning(f"[azure] Local model not found: {local_llm_path}")
                logger.info("[azure] Falling back to API LLM")
                local_llm_path = None  # Don't use local model
            else:
                raise RuntimeError(
                    f"Local model file not found: {local_llm_path}\n"
                    "Either:\n"
                    "  1. Download model: python scripts/download_model.py\n"
                    "  2. Fix path in .env: AZURE_MODEL_PATH=...\n"
                    "  3. Use cloud API instead (set OPENAI_API_KEY, GOOGLE_API_KEY, or ANTHROPIC_API_KEY)"
                )
        else:
            local_llm_path = str(model_path_obj)
            logger.info(f"[azure] Loading local LLM: {local_llm_path}")

    n_threads = os.environ.get("AZURE_N_THREADS")
    n_threads = int(n_threads) if n_threads else None

    AGENT = AzureAgent(
        model_name="azure_local",
        local_llm_path=local_llm_path,  # Can be None if using API only
        long_term_path=ROOT / "memory_v2.json",
        moderation_mode=moderation_phase,
        log_dir=LOG_DIR,
        n_threads=n_threads,
    )

    # Register LLM workers for cleanup (prevents zombie processes)
    if AGENT and AGENT.local_llm and hasattr(AGENT.local_llm, 'stop') and hasattr(main, '_register_llm_worker'):
        main._register_llm_worker(AGENT.local_llm)
        logger.debug("[azure] Registered LLM worker for cleanup")

    # Shorthands for other systems that expect an LLM object
    LLM = AGENT.llm

    info = AGENT.get_info()
    if info.get("mode") in ("local", "api", "hybrid"):
        logger.info(f"[azure] LLM READY (mode={info.get('mode')})")

        if info.get('model_path'):
            logger.info(f"[azure] model: {info.get('model_path')}")

        elif info.get('api'):
            api_info = info['api']
            logger.info(f"[azure] API: {api_info.get('provider')} / {api_info.get('model')}")

    elif info.get("mode") == "none":
        raise RuntimeError("No LLM available. Set AZURE_MODEL_PATH or an API key in .env")
    else:
        raise RuntimeError(f"Unexpected LLM mode: {info.get('mode')}")

    def guild_name_getter():
        return None

    global MODEL_SELECTOR
    MODEL_SELECTOR = ModelSelector()
    ctx.model_selector = MODEL_SELECTOR
    try:
        from azure.api_llm import ApiLLM
        ApiLLM._model_selector = MODEL_SELECTOR
        ApiLLM._active_llm = AGENT.api_llm if AGENT else None
    except Exception as e:
        logger.warning("[azure] could not wire live LLM settings: %s", e)

    register_discord_tools(AGENT, guild_name_getter, bot)
    register_commands(bot)
    register_moderation_commands(bot)
    register_trace_commands(bot.tree)
    register_reputation_commands(bot.tree)
    register_case_commands(bot.tree)
    register_config_commands(bot.tree)
    register_ghost_commands(bot.tree)
    register_revival_commands(bot.tree)
    register_settings(bot.tree, MODEL_SELECTOR)

    global BG_EXECUTOR
    BG_EXECUTOR = BackgroundExecutor(bot)

    # Initialize agentic Discord management tools
    global MGMT_TOOLS
    if DiscordManagementTools is not None:
        try:
            MGMT_TOOLS = DiscordManagementTools(bot)
            logger.info("[azure] agentic management tools initialized")

        except Exception as e:
            logger.warning(f"[azure] warning: could not initialize management tools: {e}")

    else:
        logger.error("[azure] management tools not available (import failed)")


    # Initialize pure-AI intent classifier (LLM generates intent labels dynamically)
    global INTENT_CLASSIFIER
    if IntentClassifier is not None:
        try:
            bot_name = getattr(bot, "user", None) and getattr(bot.user, "display_name", "Azure") or "Azure"
            INTENT_CLASSIFIER = IntentClassifier(llm=AGENT.llm if AGENT and AGENT.llm else None, bot_name=bot_name)
            logger.info("[azure] intent classifier initialized (AI-generated intents)")

        except Exception as e:
            logger.error(f"[azure] intent classifier error: {e}")

            # Ultimate fallback: no-op classifier
            INTENT_CLASSIFIER = IntentClassifier()
    else:
        logger.error("[azure] intent classifier not available (import failed)")


    # Initialize LLM Tool Engine (smart tool selection - no keywords!)
    global TOOL_ENGINE
    if ToolEngine is not None:
        try:
            llm = AGENT.llm if (AGENT and AGENT.llm) else None
            if llm:
                TOOL_ENGINE = ToolEngine(llm=llm)
                logger.info("[azure] LLM Tool Engine initialized (the LLM decides what to do, no keywords!)")

            else:
                logger.info("[azure] LLM Tool Engine not initialized (no LLM available, falling back to keyword classifier)")

        except Exception as e:
            logger.warning(f"[azure] warning: could not initialize Tool Engine: {e}")

    else:
        logger.error("[azure] Tool Engine not available (import failed)")


    # Initialize Task Manager (uninterruptible task execution)
    global TASK_MANAGER
    if TaskManager is not None and MGMT_TOOLS is not None:
        try:
            TASK_MANAGER = TaskManager()
            # Attach to management tools so they share the same task manager
            MGMT_TOOLS.tasks = TASK_MANAGER
            logger.info("[azure] Task Manager initialized (tasks can't be interrupted)")

        except Exception as e:
            logger.warning(f"[azure] warning: could not initialize Task Manager: {e}")

    else:
        logger.info("[azure] Task Manager not available")


    logger.info(f"[azure] agent ready (moderation phase: {moderation_phase})")


    # Initialize self-repair system
    global REPAIR
    if SelfRepair is not None:
        try:
            REPAIR = SelfRepair()
            if MGMT_TOOLS is not None:
                MGMT_TOOLS.repair = REPAIR
            logger.info("[azure] self-repair system initialized")

        except Exception as e:
            logger.warning(f"[azure] warning: could not initialize self-repair: {e}")

    else:
        logger.error("[azure] self-repair not available (import failed)")


    # Initialize multi-server config manager
    global SERVER_CONFIGS
    if ServerConfigManager is not None:
        try:
            SERVER_CONFIGS = ServerConfigManager()
            logger.info(f"[azure] multi-server config manager ready ({SERVER_CONFIGS.count()} guilds)")

        except Exception as e:
            logger.error(f"[azure] server_config init error: {e}")


    # Start health check HTTP server (opt-in via feature flag, default on)
    global HEALTH_SERVER
    if _FEATURES.health and HealthServer is not None:
        try:
            health_port = int(os.environ.get("AZURE_HEALTH_PORT", "8088"))
            HEALTH_SERVER = HealthServer(port=health_port, agent=AGENT)
            HEALTH_SERVER.start()
            logger.info(f"[azure] health server: {HEALTH_SERVER.url}")
        except Exception as e:
            logger.error(f"[azure] health server init error: {e}")
    elif not _FEATURES.health:
        logger.info("[azure] health server disabled (AZURE_FEATURE_HEALTH=0)")

    # Cognitive pipeline — only when flag enabled
    global COGNITIVE_PIPELINE
    if _FEATURES.cognitive and CognitivePipeline is not None:
        try:
            llm = AGENT.llm if (AGENT and AGENT.llm) else None
            COGNITIVE_PIPELINE = CognitivePipeline(
                agent=AGENT,
                llm=llm,
                log_dir=COGNITIVE_LOG_DIR,
                save_states=True,
                semantic_threshold=SEMANTIC_THRESHOLD,
            )
            _rotate_cognition_logs(COGNITIVE_LOG_DIR)
            logger.info(
                "[azure] cognitive pipeline ready (threshold=%.2f, dir=%s)",
                SEMANTIC_THRESHOLD, COGNITIVE_LOG_DIR,
            )
        except Exception as e:
            logger.error(f"[azure] cognitive pipeline init error: {e}")
    elif not _FEATURES.cognitive:
        logger.info("[azure] cognitive pipeline skipped (enable AZURE_FEATURE_COGNITIVE=1)")
    else:
        logger.error("[azure] cognitive pipeline not available (import failed)")

    # Optional v3 systems — each gated by feature flags
    global GAME_MASTER, DOC_INTEL, VOICE_SYSTEM, CHANNEL_LIFECYCLE, PLUGIN_MANAGER, INTEGRATION_HUB, VISION_PROCESSOR, PROACTIVE_ENGINE
    if _FEATURES.games and GameMaster is not None:
        try:
            GAME_MASTER = GameMaster()
            logger.info("[azure] game master initialized")
        except Exception as e:
            logger.error(f"[azure] game master init error: {e}")

    if DocumentIntelligence is not None:
        try:
            DOC_INTEL = DocumentIntelligence()
            logger.info("[azure] document intelligence initialized")
        except Exception as e:
            logger.error(f"[azure] document intelligence init error: {e}")

    if _FEATURES.voice and VoiceSystem is not None:
        try:
            VOICE_SYSTEM = VoiceSystem()
            logger.info(f"[azure] voice system initialized (ready: {VOICE_SYSTEM.is_ready()})")
        except Exception as e:
            logger.error(f"[azure] voice system init error: {e}")

    if ChannelLifecycleManager is not None:
        try:
            CHANNEL_LIFECYCLE = ChannelLifecycleManager()
            logger.info("[azure] channel lifecycle manager initialized")
        except Exception as e:
            logger.error(f"[azure] channel lifecycle init error: {e}")

    if _FEATURES.plugins and PluginManager is not None:
        try:
            PLUGIN_MANAGER = PluginManager()
            logger.info("[azure] plugin manager initialized (empty set until async load)")
        except Exception as e:
            logger.error(f"[azure] plugin manager init error: {e}")

    if _FEATURES.integrations and create_integration_hub is not None:
        try:
            INTEGRATION_HUB = create_integration_hub()
            logger.info(f"[azure] integration hub initialized ({INTEGRATION_HUB.list_available()})")
        except Exception as e:
            logger.error(f"[azure] integration hub init error: {e}")

    if _FEATURES.vision and VisionProcessor is not None:
        try:
            VISION_PROCESSOR = VisionProcessor()
            logger.info("[azure] vision processor initialized")
        except Exception as e:
            logger.error(f"[azure] vision processor init error: {e}")

    # ProactiveEngine — requires cognitive pipeline + proactive flag
    if _FEATURES.proactive and COGNITIVE_PIPELINE and hasattr(COGNITIVE_PIPELINE, "goal_manager"):
        try:
            from azure.cognition import ProactiveEngine
            PROACTIVE_ENGINE = ProactiveEngine(COGNITIVE_PIPELINE.goal_manager)
            logger.info("[azure] proactive engine initialized")
        except Exception as e:
            logger.error(f"[azure] proactive engine init error: {e}")

    # Transport-agnostic moderation service (core — always when engine exists)
    global MODERATION_SERVICE
    MODERATION_SERVICE = None
    if AGENT is not None and getattr(AGENT, "moderation", None) is not None:
        try:
            from azure.moderation_service import ModerationService
            MODERATION_SERVICE = ModerationService(engine=AGENT.moderation)
            logger.info("[azure] moderation service ready (engine-backed)")
        except Exception as e:
            logger.error(f"[azure] moderation service init error: {e}")

    # Live Intelligence — opt-in
    global LIVE_INTELLIGENCE
    if _FEATURES.live_intel and LiveIntelligence is not None and AGENT is not None:
        try:
            LIVE_INTELLIGENCE = LiveIntelligence(
                bot=bot,
                llm=getattr(AGENT, "llm", None),
                memory_backend=getattr(AGENT, "memory_backend", None),
                log_dir=LOG_DIR,
            )
            if setup_live_commands is not None:
                try:
                    setup_live_commands(bot, LIVE_INTELLIGENCE)
                except Exception as cmd_err:
                    logger.warning("[azure] live command registration partial/failed: %s", cmd_err)
            logger.info("[azure] live intelligence initialized")
        except Exception as e:
            logger.error(f"[azure] live intelligence init error: {e}")
            LIVE_INTELLIGENCE = None
    elif not _FEATURES.live_intel:
        logger.info("[azure] live intelligence skipped (enable AZURE_FEATURE_LIVE_INTEL=1)")

    # Bridge module globals into shared ctx (single source of truth at runtime)
    _populate_ctx()
    if ctx.core_ready():
        logger.info("[azure] CORE LOADED — waiting for Discord gateway readiness")
    else:
        ctx.mark_failed("agent or llm missing after setup")
        logger.error("[azure] CORE NOT READY — check LLM provider/key configuration")
    for status in ctx.subsystem_report():
        mark = "OK" if status.ready else ("--" if not status.loaded else "!!")
        logger.info("[azure] subsystem %-20s %s %s", status.name, mark, status.detail)


def _populate_ctx():
    """Copy the subsystem singletons assigned in setup() into the shared ctx."""
    g = globals()
    _mapping = {
        "agent": "AGENT",
        "task_manager": "TASK_MANAGER",
        "mgmt_tools": "MGMT_TOOLS",
        "cognitive_pipeline": "COGNITIVE_PIPELINE",
        "intent_classifier": "INTENT_CLASSIFIER",
        "tool_engine": "TOOL_ENGINE",
        "health_server": "HEALTH_SERVER",
        "server_configs": "SERVER_CONFIGS",
        "bg_executor": "BG_EXECUTOR",
        "repair": "REPAIR",
        "moderation_service": "MODERATION_SERVICE",
        "game_master": "GAME_MASTER",
        "doc_intel": "DOC_INTEL",
        "voice_system": "VOICE_SYSTEM",
        "channel_lifecycle": "CHANNEL_LIFECYCLE",
        "plugin_manager": "PLUGIN_MANAGER",
        "integration_hub": "INTEGRATION_HUB",
        "vision_processor": "VISION_PROCESSOR",
        "proactive_engine": "PROACTIVE_ENGINE",
        "live_intelligence": "LIVE_INTELLIGENCE",
        "cron_scheduler": "CRON_SCHEDULER",
    }
    for attr, global_name in _mapping.items():
        val = g.get(global_name)
        if val is not None:
            setattr(ctx, attr, val)

    # Runtime/config fields that don't depend on subsystem init.
    ctx.bot = bot
    ctx.chat_mode = CHAT_MODE
    ctx.allowed_user_ids = ALLOWED_USER_IDS
    ctx.cognitive_mode = COGNITIVE_MODE
    ctx.cognitive_log_dir = COGNITIVE_LOG_DIR
    ctx.admin_channel = ADMIN_CHANNEL
    if CRON_SCHEDULER is not None:
        ctx.cron_scheduler = CRON_SCHEDULER


# ---------------------------------------------------------------------------
# Milestone 3: Cron Scheduling Commands
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Milestone 3: Live Dashboard Command
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Milestone 4: Feedback Loop — 👍/👎 reaction tracking
# ---------------------------------------------------------------------------

FEEDBACK_LOG_PATH = ROOT / "logs" / "feedback.jsonl"

# ---------------------------------------------------------------------------
# Milestone 4: Permission Audit Command
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Milestone 4: Self-Evolving System Prompt
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# V3 New Feature Commands
# ---------------------------------------------------------------------------

# Global registry of LLM workers to cleanup
_llm_workers = []

def register_llm_worker(llm):
    """Register an LLM worker for cleanup."""
    if llm and hasattr(llm, "stop"):
        _llm_workers.append(llm)

def cleanup_llm_workers():
    """Clean up all LLM workers."""
    logger.info("[azure] Cleaning up LLM workers...")
    for llm in _llm_workers:
        with contextlib.suppress(Exception):
            if hasattr(llm, "stop"):
                llm.stop()
                logger.debug("[azure] Stopped LLM worker")
    _llm_workers.clear()

def _is_valid_number(val):
    try:
        import math
        return val is not None and not math.isnan(val) and not math.isinf(val)
    except (TypeError, ValueError):
        return False

def get_runtime_stats():
    """Fetch aggregate stats from database and bot properties."""
    _db = None
    messages_today = 0
    llm_calls = 0
    errors = 0
    active_users_count = 0
    try:
        from azure.database import get_shared_db
        _db = get_shared_db()
        if _db and hasattr(_db, "get_aggregate_stats"):
            _agg = _db.get_aggregate_stats(hours=24)
            messages_today = _agg.get("total_messages", 0)
            llm_calls = _agg.get("total_tokens", 0)
            errors = _agg.get("total_errors", 0)
    except Exception:
        logger.exception("[bot] aggregate stats fetch failed")

    # Active users (messaged in the last hour)
    try:
        if _db:
            conn = _db._get_connection()
            cur = conn.cursor()
            import time as _time
            cur.execute(
                "SELECT COUNT(DISTINCT user_id) FROM conversation_history "
                "WHERE timestamp > ?",
                (_time.time() - 3600,),
            )
            row = cur.fetchone()
            active_users_count = row[0] if row else 0
    except Exception:
        logger.exception("[bot] active users query failed")

    # Uptime from bot start_time
    uptime_seconds = 0
    if hasattr(bot, "start_time"):
        try:
            import time as _time
            uptime_seconds = int(_time.time() - bot.start_time.timestamp())
        except Exception:
            logger.exception("[bot] uptime calculation failed")
            uptime_seconds = 0

    # Health score
    health_score = 100
    if messages_today > 0 and errors > 0:
        health_score = max(0, int(100 - (errors / messages_today) * 100))
    elif errors > 0:
        health_score = 50

    return {
        "messages_today": messages_today,
        "active_users": active_users_count,
        "llm_calls": llm_calls,
        "uptime": uptime_seconds,
        "uptime_seconds": uptime_seconds,
        "health_score": health_score,
        "errors": errors,
        "guilds": len(bot.guilds) if bot.guilds else 0,
        "latency_ms": round(getattr(bot, "latency", 0.0) * 1000) if _is_valid_number(getattr(bot, "latency", 0.0)) else 0,
    }

# ---------------------------------------------------------------------------
# Backward-compat lazy aliases — allows ``from ..discord_bot_v1 import AGENT``
# to resolve to ctx.agent even though the old module-level global is gone.
# New code should use ``from ..context import ctx`` instead.
# ---------------------------------------------------------------------------
_LEGACY_ALIASES: dict[str, str] = {
    "AGENT": "agent",
    "MGMT_TOOLS": "mgmt_tools",
    "TASK_MANAGER": "task_manager",
    "COGNITIVE_PIPELINE": "cognitive_pipeline",
    "INTENT_CLASSIFIER": "intent_classifier",
    "TOOL_ENGINE": "tool_engine",
    "HEALTH_SERVER": "health_server",
    "SERVER_CONFIGS": "server_configs",
    "BG_EXECUTOR": "bg_executor",
    "REPAIR": "repair",
    "MODERATION_SERVICE": "moderation_service",
    "GAME_MASTER": "game_master",
    "DOC_INTEL": "doc_intel",
    "VOICE_SYSTEM": "voice_system",
    "CHANNEL_LIFECYCLE": "channel_lifecycle",
    "PLUGIN_MANAGER": "plugin_manager",
    "INTEGRATION_HUB": "integration_hub",
    "VISION_PROCESSOR": "vision_processor",
    "PROACTIVE_ENGINE": "proactive_engine",
    "LIVE_INTELLIGENCE": "live_intelligence",
    "CRON_SCHEDULER": "cron_scheduler",
    "MODEL_SELECTOR": "model_selector",
    "ADMIN_CHANNEL": "admin_channel",
}

def __getattr__(name: str):
    if name in _LEGACY_ALIASES:
        from bot.context import ctx
        return getattr(ctx, _LEGACY_ALIASES[name], None)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main():
    """Delegate to bot.lifecycle — single production entrypoint.

    Keeps ``python -m bot.discord_bot_v1`` and legacy imports working while
    ensuring web dashboard, shared DB, and graceful shutdown all run together.
    """
    main._register_llm_worker = register_llm_worker
    from bot.lifecycle import main as lifecycle_main
    lifecycle_main()


if __name__ == "__main__":
    main()
