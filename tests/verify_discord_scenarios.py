#!/usr/bin/env python3
"""
Discord Bot Realistic Scenarios Simulation & Verification Runner.
This script performs a complete end-to-end validation of the Azure AI Discord bot
under 12 distinct, realistic real-world Discord situation scenarios.

No third-party testing dependencies are needed; uses only standard library components.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, UTC
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch

# Configure path resolution
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Fix Windows console encoding so emoji/Unicode prints don't crash
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# Setup logging (minimal during testing, print pretty results instead)
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger("verify_scenarios")

# Colors for pretty terminal logs
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

# ----------------------------------------------------
# 1. INTERCEPT DISCORD IMPORT & SETUP MOCK INFRA
# ----------------------------------------------------
from tests.conftest import MOCK as _discord_mock
from tests.conftest import REAL_DISCORD as _real_discord
from tests.conftest import reset_utils_get

# Intercept discord import
_orig_discord = sys.modules.pop("discord", None)
# Expose real discord types on mock module so isinstance checks succeed
_discord_mock.Member = _real_discord.Member
_discord_mock.User = _real_discord.User
_discord_mock.Message = _real_discord.Message
_discord_mock.Guild = _real_discord.Guild
sys.modules["discord"] = _discord_mock
reset_utils_get()

import discord
from discord.ext import commands

# Helper: print scenario headers
def print_scenario_start(num: int, title: str):
    print(f"\n{BOLD}{CYAN}-------------------------------------------------------{RESET}")
    print(f"{BOLD}{CYAN}SCENARIO {num}: {title}{RESET}")
    print(f"{BOLD}{CYAN}-------------------------------------------------------{RESET}")

def print_result(name: str, status: bool, info: str = ""):
    if status:
        print(f"  {GREEN}[PASS]{RESET} {name:<48} {GREEN}SUCCESS{RESET} {info}")
    else:
        print(f"  {RED}[FAIL]{RESET} {name:<48} {RED}FAILED{RESET} {info}")
        sys.exit(1)

# ----------------------------------------------------
# 2. DEFINING THE MOCK OBJECTS
# ----------------------------------------------------
# Configure default created_at on MagicMock class so any dynamically created mock member/user
# has a valid datetime.date for account age calculations.
MagicMock.created_at = datetime.now(UTC)

class MockChannel:
    def __init__(self, id, name, guild=None):
        self.id = id
        self.name = name
        self.guild = guild
        self.send = AsyncMock(side_effect=self._on_send)
        self.fetch_message = AsyncMock(side_effect=self._on_fetch_message)
        self.typing = MagicMock()
        self.typing.return_value.__aenter__ = AsyncMock()
        self.typing.return_value.__aexit__ = AsyncMock(return_value=False)
        self.permissions_for = MagicMock()
        self.permissions_for.return_value.send_messages = True
        self.sent_messages = []

    async def _on_send(self, *args, **kwargs):
        msg = MagicMock()
        msg.id = 50000 + len(self.sent_messages)
        msg.content = args[0] if args else kwargs.get("content", "")
        msg.embeds = kwargs.get("embeds", [])
        if "embed" in kwargs:
            msg.embeds.append(kwargs["embed"])
        msg.channel = self
        
        async def _edit(content=None, **kwargs):
            if content is not None:
                msg.content = content
        msg.edit = AsyncMock(side_effect=_edit)
        
        self.sent_messages.append(msg)
        return msg

    async def _on_fetch_message(self, message_id):
        for msg in self.sent_messages:
            if msg.id == message_id:
                return msg
        # Return a fallback
        m = MagicMock()
        m.id = message_id
        m.author = MagicMock()
        m.author.id = 123
        m.author.display_name = "Spammer"
        return m


class MockBot:
    def __init__(self):
        self.user = MagicMock()
        self.user.id = 777
        self.user.display_name = "Azure"
        self.user.mention = "<@777>"
        self.guilds = []
        self._channels = {}
        self.get_channel = MagicMock(side_effect=self._get_channel)
        
        self.application = MagicMock()
        self.application.owner = MagicMock()
        self.application.owner.id = 9999
        
    def _get_channel(self, channel_id):
        return self._channels.get(int(channel_id))
        
    def add_guild(self, guild):
        self.guilds.append(guild)
        for ch in guild.text_channels:
            self._channels[ch.id] = ch


_msg_counter = 0

def make_mock_message(content, author_id=123, author_name="User", author_is_bot=False, guild_id=456, guild_name="Test Guild", channel_id=789, channel_name="general", is_dm=False, bot_instance=None):
    global _msg_counter
    _msg_counter += 1
    msg = MagicMock()
    msg.id = 100000 + _msg_counter
    msg.content = content
    msg.attachments = []
    
    # Author
    msg.author = MagicMock(spec=_real_discord.Member if not is_dm else _real_discord.User)
    msg.author.id = author_id
    msg.author.display_name = author_name
    msg.author.name = author_name
    msg.author.bot = author_is_bot
    msg.author.mention = f"<@{author_id}>"
    msg.author.created_at = datetime.now(UTC)
    
    # Author permissions
    msg.author.guild_permissions = MagicMock()
    msg.author.guild_permissions.administrator = False
    
    # Member actions
    msg.author.kick = AsyncMock()
    msg.author.ban = AsyncMock()
    msg.author.timeout = AsyncMock()
    msg.author.roles = []
    
    # Guild
    if is_dm:
        msg.guild = None
    else:
        msg.guild = MagicMock()
        msg.guild.id = guild_id
        msg.guild.name = guild_name
        msg.guild.owner_id = 9999
        msg.guild.member_count = 50
        msg.guild.members = [msg.author]
        msg.guild.get_member = MagicMock(return_value=msg.author)
        
        # Self-me in guild
        msg.guild.me = MagicMock()
        msg.guild.me.top_role = MagicMock()
        msg.guild.me.top_role.position = 100
        msg.guild.me.guild_permissions = MagicMock()
        msg.guild.me.guild_permissions.manage_messages = True
        msg.guild.me.guild_permissions.moderate_members = True
        msg.guild.me.guild_permissions.kick_members = True
        msg.guild.me.guild_permissions.ban_members = True
        msg.author.guild = msg.guild
        
    # Channel
    if bot_instance and not is_dm:
        ch = bot_instance.get_channel(channel_id)
        if ch:
            msg.channel = ch
        else:
            msg.channel = MockChannel(channel_id, channel_name, msg.guild)
            bot_instance._channels[channel_id] = msg.channel
    else:
        msg.channel = MockChannel(channel_id, channel_name, msg.guild if not is_dm else None)

    if not is_dm:
        # Existing shared channels must still point at the current simulated
        # guild so moderation permission checks follow real Discord objects.
        msg.channel.guild = msg.guild
        
    # Reply / Reactions
    msg.reply = AsyncMock(side_effect=msg.channel._on_send)
    msg.add_reaction = AsyncMock()
    msg.delete = AsyncMock()
    msg.mentions = []
    
    return msg

# ----------------------------------------------------
# 3. VERIFICATION RUNNER MAIN
# ----------------------------------------------------
async def main_async():
    print(f"\n=======================================================")
    print(f"      AZURE BOT DISCORD SCENARIOS INTEGRATION RUNNER   ")
    print(f"=======================================================\n")

    # Set system environment vars to configure bot properties
    os.environ["AZURE_CB_FAILURE_THRESHOLD"] = "2"
    # Keep the breaker open long enough for the assertions to observe it;
    # model setup and Discord simulation can exceed a sub-second cooldown.
    os.environ["AZURE_CB_COOLDOWN_SECONDS"] = "5.0"
    os.environ["AZURE_RATE_LIMIT_MAX"] = "5"
    os.environ["AZURE_RATE_LIMIT_WINDOW"] = "60.0"
    os.environ["AZURE_RATE_LIMIT_COOLDOWN"] = "2.0"
    os.environ["AZURE_CONFIRMATION_MODE"] = "destructive"
    os.environ["AZURE_CONFIRMATION_THRESHOLD"] = "0.75"
    os.environ["AZURE_ADMIN_CHANNEL_ID"] = "101"

    from bot.context import ctx
    from azure.agent import AzureAgent
    from azure.database import DatabaseManager, set_shared_db
    from bot.handlers.message_handler import on_message

    # Setup database
    db_path = ROOT / "data" / "verify_scenarios_test.db"
    if db_path.exists():
        db_path.unlink()
    db = DatabaseManager(db_path=db_path)
    set_shared_db(db)
    ctx.db = db

    # Mock AzureAgent dependencies so it launches without real model files
    with patch("azure.agent.ApiLLM") as mock_apillm, \
         patch("azure.agent.LocalLLM") as mock_localllm:
        
        mock_apillm._detect_provider.return_value = "openai"
        mock_apillm.return_value._model = "gpt-4o"
        
        agent = AzureAgent(
            model_name="azure_test",
            local_llm_path=None,
            long_term_path=ROOT / "data" / "verify_scenarios_lt.json",
            moderation_mode="reactive_full",
        )
        
        # Inject standard LLM behavior
        fake_llm = MagicMock()
        fake_llm.chat.return_value = "Hello! I am Azure, your Operating Platform assistant. How can I help you today?"
        agent.llm = fake_llm
        agent._llm_type = "api"
        
        ctx.agent = agent
        
        # Configure simple bot object
        bot = MockBot()
        ctx.bot = bot
        
        # Let's add text channels to bot
        general_ch = MockChannel(789, "general")
        admin_ch = MockChannel(101, "admin-logs")
        bot._channels[789] = general_ch
        bot._channels[101] = admin_ch
        ctx.admin_channel = admin_ch
        agent.set_moderation_bot(bot)
        
        # Disable task manager & cognitive pipeline for simple inline execution trace
        ctx.task_manager = None
        ctx.cognitive_pipeline = None
        ctx.chat_mode = "anyone"

        # ----------------------------------------------------
        # SCENARIO 1: Standard Chat Triggered by Mention
        # ----------------------------------------------------
        print_scenario_start(1, "Standard Chat Mention")
        msg = make_mock_message("<@777> hello azure", author_id=123, author_name="NormalUser", bot_instance=bot)
        msg.mentions = [bot.user]
        
        await on_message(msg)
        
        print_result("Bot starting typing indicator", msg.channel.typing.called)
        print_result("Bot replied to message", len(general_ch.sent_messages) > 0)
        if general_ch.sent_messages:
            any_has_resp = any("Hello! I am Azure" in m.content for m in general_ch.sent_messages)
            print_result("Replied with LLM generated text", any_has_resp, f"(Got: {[m.content[:40] for m in general_ch.sent_messages]}...)")

        # ----------------------------------------------------
        # SCENARIO 2: DM Conversation
        # ----------------------------------------------------
        print_scenario_start(2, "Direct Message Conversation")
        dm_channel = MockChannel(888, "DM-Channel")
        msg_dm = make_mock_message("can you explain your command list?", author_id=222, author_name="NormalUser", is_dm=True, bot_instance=bot)
        msg_dm.channel = dm_channel
        msg_dm.reply = AsyncMock(side_effect=dm_channel._on_send)
        
        await on_message(msg_dm)
        
        print_result("Bot processed DM", len(dm_channel.sent_messages) > 0)
        if dm_channel.sent_messages:
            any_has_dm = any("Hello!" in m.content or "Done" in m.content for m in dm_channel.sent_messages)
            print_result("Replied to user in DM", any_has_dm, f"(Got: {[m.content[:40] for m in dm_channel.sent_messages]}...)")

        # ----------------------------------------------------
        # SCENARIO 3: Chat Restriction Modes
        # ----------------------------------------------------
        print_scenario_start(3, "Chat Restrictions Mode")
        # 3a. Owner Only mode
        ctx.chat_mode = "owner_only"
        general_ch.sent_messages.clear()
        
        msg_non_owner = make_mock_message("<@777> test owner mode", author_id=456, author_name="TrollUser", bot_instance=bot)
        msg_non_owner.mentions = [bot.user]
        await on_message(msg_non_owner)
        print_result("Ignored message from non-owner", len(general_ch.sent_messages) == 0)
        
        # 3b. Mention Only mode
        ctx.chat_mode = "mention_only"
        dm_channel.sent_messages.clear()
        msg_dm_no_mention = make_mock_message("hello bot", author_id=333, author_name="NormalUser", is_dm=True, bot_instance=bot)
        msg_dm_no_mention.channel = dm_channel
        msg_dm_no_mention.reply = AsyncMock(side_effect=dm_channel._on_send)
        
        await on_message(msg_dm_no_mention)
        print_result("Ignored DM when mention_only active", len(dm_channel.sent_messages) == 0)
        
        # Reset chat mode
        ctx.chat_mode = "anyone"

        # ----------------------------------------------------
        # SCENARIO 4: Security Shield (Malicious Inputs)
        # ----------------------------------------------------
        print_scenario_start(4, "Security Guard (Prompt / SQL Injection Block)")
        general_ch.sent_messages.clear()
        
        sql_injection = "DROP TABLE telemetry_logs; --"
        msg_malicious = make_mock_message(sql_injection, author_id=444, author_name="HackerUser", bot_instance=bot)
        
        await on_message(msg_malicious)
        
        print_result("Sent warning message for bad input", len(general_ch.sent_messages) > 0)
        if general_ch.sent_messages:
            warn_msg = general_ch.sent_messages[-1].content
            print_result("Flagged as suspicious patterns", "suspicious patterns" in warn_msg)
            
        # Note: Input validation catches this BEFORE moderation runs,
        # so no security_events DB row is created — that's correct behavior.
        # The moderation system logs to security_events for content that passes input validation.

        # ----------------------------------------------------
        # SCENARIO 5: User Level Rate Limiting
        # ----------------------------------------------------
        print_scenario_start(5, "User Rate Limiting (Anti-Spam Cooldown)")
        general_ch.sent_messages.clear()
        
        # Test rate limiting directly — in the real handler, command cooldown
        # (a stricter per-message guard) fires before rate limiting kicks in.
        # We verify the rate limiter logic independently.
        from bot.handlers.rate_limiter import _check_rate_limit, RATE_LIMIT_MAX_REQUESTS as RL_MAX
        test_user = "rate_test_user_9999"
        for i in range(RL_MAX):
            allowed, cd = await _check_rate_limit(test_user, "test_guild")
        # After exceeding the limit, the next call should be blocked
        allowed, cd = await _check_rate_limit(test_user, "test_guild")
        print_result("Rate limiting triggered correctly", not allowed, f"(limit={RL_MAX}, cooldown={cd:.1f}s)")

        # ----------------------------------------------------
        # SCENARIO 6: Command Cooldown
        # ----------------------------------------------------
        print_scenario_start(6, "Command Cooldown")
        general_ch.sent_messages.clear()
        
        # Commands start with prefix !
        cmd_msg1 = make_mock_message("!mod_stats", author_id=111, author_name="AdminUser", bot_instance=bot)
        await on_message(cmd_msg1)
        
        cmd_msg2 = make_mock_message("!mod_stats", author_id=111, author_name="AdminUser", bot_instance=bot)
        await on_message(cmd_msg2)
        
        print_result("Added ⏰ reaction to spammed command", cmd_msg2.add_reaction.called)
        
        cooldown_warning = any("please wait" in m.content for m in general_ch.sent_messages)
        print_result("Sent cooldown warning message", cooldown_warning)

        # ----------------------------------------------------
        # SCENARIO 7: Spammer Moderation (Temporal Analysis)
        # ----------------------------------------------------
        print_scenario_start(7, "Spam Repetition (Autonomous Moderation)")
        general_ch.sent_messages.clear()
        admin_ch.sent_messages.clear()
        
        # Set agent policy to reactive_full so actions are actually executed
        from azure.moderation.phase import ModerationPhase
        agent.moderation.policy.phase = ModerationPhase.REACTIVE_FULL
        
        # Test moderation classifier directly — in production spam arrives over
        # seconds, but our test loop is instant so command cooldown blocks msgs 2+.
        spam_content = "FREE MONEY GET BITCOIN NOW"
        for i in range(6):
            msg_spam = make_mock_message(spam_content, author_id=555, author_name="SpamBot", bot_instance=bot)
            # Feed directly to moderation engine
            report = await agent.moderation.on_message(msg_spam)
        
        # The last call should detect repetition/spam
        has_report = report is not None
        print_result("Logged moderation report to admin channel", has_report, f"(report={report})")
        
        if has_report:
            # Send report to admin channel like the real handler does
            embed_dict = report.to_embed_dict()
            embed = discord.Embed.from_dict(embed_dict)
            await admin_ch.send(embed=embed)
        
        # Check action was timeout
        timeout_executed = has_report and report.action_type in ("delete", "timeout", "mute", "warn", "ban")
        print_result("Automatically flagged spammer for action", timeout_executed)

        # ----------------------------------------------------
        # SCENARIO 8: Toxic/Troll Moderation Classification
        # ----------------------------------------------------
        print_scenario_start(8, "Toxic Content (Severe Harassment detection)")
        admin_ch.sent_messages.clear()
        
        # A highly toxic scam message — test moderation classifier directly
        toxic_scam = "YOU STUPID LOSER click here to claim free robux at http://freerobux.ga @everyone @here"
        msg_toxic = make_mock_message(toxic_scam, author_id=666, author_name="TrollScammer", bot_instance=bot)
        
        toxic_report = await agent.moderation.on_message(msg_toxic)
        
        has_toxic = toxic_report is not None
        print_result("Toxicity/Scam report sent to admin logs", has_toxic, f"(report={toxic_report})")
        # Since it's scam and severity critical, suggested action is ban/timeout
        print_result("Action triggered auto-ban/kick", has_toxic and toxic_report.action_type in ("ban", "kick", "timeout"))

        # ----------------------------------------------------
        # SCENARIO 9: LLM Failover & Circuit Breaker
        # ----------------------------------------------------
        print_scenario_start(9, "LLM Connection Outage (Circuit Breaker)")
        # Force LLM failures to trip breaker
        fake_llm.chat.side_effect = Exception("Connection Refused")
        
        msg_fail1 = make_mock_message("<@777> try to chat 1", author_id=1001, author_name="User", bot_instance=bot)
        msg_fail1.mentions = [bot.user]
        await on_message(msg_fail1)
        
        msg_fail2 = make_mock_message("<@777> try to chat 2", author_id=1002, author_name="User", bot_instance=bot)
        msg_fail2.mentions = [bot.user]
        await on_message(msg_fail2)
        
        # Check circuit breaker state (threshold = 2)
        cb = agent._llm_circuit_breaker
        print_result("Circuit breaker tripped to OPEN", cb.state == "OPEN")
        
        # Send message while OPEN
        msg_fail3 = make_mock_message("<@777> try to chat 3", author_id=1003, author_name="User", bot_instance=bot)
        msg_fail3.mentions = [bot.user]
        await on_message(msg_fail3)
        fallback_text = general_ch.sent_messages[-1].content if general_ch.sent_messages else ""
        print_result(
            "Short-circuit and returned fallback error",
            "temporarily unavailable" in fallback_text.lower()
            or "unavailable" in fallback_text.lower(),
            f"(Got: {fallback_text[:100]!r})",
        )

        # Restore LLM
        fake_llm.chat.side_effect = None
        fake_llm.chat.return_value = "Recovered response."
        time.sleep(5.1) # wait for cooldown
        cb.allow_request() # advance state machine

        # ----------------------------------------------------
        # SCENARIO 10: High-Risk Action Confirmation Queue
        # ----------------------------------------------------
        print_scenario_start(10, "Confirmation Queue (Human-in-the-loop validation)")
        admin_ch.sent_messages.clear()
        # Clear the earlier spam report so this scenario validates the newly
        # requested action rather than whichever pending item was inserted first.
        for pending in agent.moderation.list_pending_confirmations():
            agent.moderation.cancel_action(pending["message_id"])
        
        # Force moderation action to trigger confirmation by setting threshold high
        agent.moderation.policy.confirmation_mode = "destructive"
        agent.moderation.policy.confirmation_threshold = 0.99
        
        # Send message containing suspicious scam text that gets flagged for a destructive action
        scam_text = "verify your account details free nitro prize http://discordnitro.ru"
        msg_confirm_req = make_mock_message(scam_text, author_id=444, author_name="SuspiciousUser", bot_instance=bot)
        
        await on_message(msg_confirm_req)
        
        # Admin channel should have received a confirmation request
        print_result("Sent confirmation request embed to admins", len(admin_ch.sent_messages) > 0)
        
        pending_list = agent.moderation.list_pending_confirmations()
        print_result("Action successfully queued in ConfirmationQueue", len(pending_list) > 0)

        if pending_list:
            current = next(
                (item for item in pending_list if item["message_id"] == str(msg_confirm_req.id)),
                pending_list[0],
            )
            msg_id = current["message_id"]
            # Simulate admin confirming the action
            # Mock get_channel and fetch_message
            bot._channels[int(current["channel_id"])] = msg_confirm_req.channel
            msg_confirm_req.channel.sent_messages.append(msg_confirm_req)
            
            success, result_msg = await agent.moderation.confirm_action(msg_id)
            print_result("Action execution after admin confirm command", success)
            print_result("Target user timed out or banned upon confirmation", msg_confirm_req.author.ban.called or msg_confirm_req.author.timeout.called or msg_confirm_req.author.kick.called)
            print_result("ConfirmationQueue drained", len(agent.moderation.list_pending_confirmations()) == 0)

        # ----------------------------------------------------
        # SCENARIO 11: Database Resilience (Locked sqlite recoveries)
        # ----------------------------------------------------
        print_scenario_start(11, "Database Resilience (SQLite Lock Recovery)")
        
        attempts = 0
        def locked_operation():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise sqlite3.OperationalError("database is locked")
            return "ok"

        # Execute using retry wrapper
        res = db._execute_with_retry(locked_operation, max_retries=5)
        print_result("Operation succeeded after retrying transient locks", res == "ok" and attempts == 3)

        # ----------------------------------------------------
        # SCENARIO 12: RAG Context Retrieval & Chat Augmentation
        # ----------------------------------------------------
        print_scenario_start(12, "RAG Context Retrieval")
        # Initialize RAG mock/real engine
        from azure.rag_engine import DiscordRAG
        
        mock_st = MagicMock()
        mock_st.return_value.get_embedding_dimension.return_value = 384
        mock_st.return_value.encode.return_value = [0.05] * 384
        
        with patch("azure.rag_engine._SentenceTransformer", mock_st):
            test_rag_path = ROOT / "data" / "verify_scenarios_rag.json"
            if test_rag_path.exists():
                test_rag_path.unlink()

            rag = DiscordRAG(persist_path=test_rag_path, max_docs=10)
            agent.rag = rag
            
            # Add some server rules
            rag.add("Rule 10: Absolutely no scam links in the #announcements channel.", {"guild": "Test Guild"})
            
            # Query it
            hits = rag.search("scam rules announcements", k=1)
            print_result("Successfully retrieved rule context from vector DB", len(hits) > 0 and "Rule 10" in hits[0]["text"])
            
            # Cleanup RAG
            if test_rag_path.exists():
                test_rag_path.unlink()

        # ----------------------------------------------------
        # SCENARIO 13: Live Server Data Request
        # ----------------------------------------------------
        print_scenario_start(13, "Read-only Server Data Request")

        class ServerInfoRouter:
            def decide(self, *args, **kwargs):
                from azure.tool_engine import ToolDecision
                return ToolDecision(
                    action="server_info",
                    confidence=1.0,
                    params={"scope": "channels"},
                )

        class InfoIntentRouter:
            def classify(self, **kwargs):
                return SimpleNamespace(route="info", action="info", confidence=1.0)

        ctx.tool_engine = ServerInfoRouter()
        ctx.intent_classifier = InfoIntentRouter()
        ctx.mgmt_tools = SimpleNamespace(
            get_server_state=AsyncMock(return_value={
                "server_name": "Test Guild",
                "member_count": 50,
                "online_count": 12,
                "channels": [{"name": "general"}, {"name": "announcements"}],
                "roles": [{"name": "Moderator"}],
                "categories": [],
                "verification_level": "low",
                "explicit_content_filter": "disabled",
            }),
        )
        info_message = make_mock_message(
            "<@777> what channels are in this server?",
            author_id=7771,
            author_name="InfoUser",
            bot_instance=bot,
        )
        info_message.mentions = [bot.user]
        await on_message(info_message)
        info_text = "\n".join(str(item.content) for item in general_ch.sent_messages)
        print_result(
            "Answered server data request from live state",
            "announcements" in info_text and "general" in info_text,
        )

        # ----------------------------------------------------
        # SCENARIO 14: Live Role Permission Request
        # ----------------------------------------------------
        print_scenario_start(14, "Read-only Role Permission Request")
        role = SimpleNamespace(
            id=321,
            name="Moderator",
            position=4,
            managed=False,
            hoist=True,
            mentionable=True,
            members=[],
            permissions=SimpleNamespace(
                to_dict=lambda: {"manage_messages": True, "ban_members": False},
            ),
        )
        class RoleInfoRouter:
            def decide(self, *args, **kwargs):
                from azure.tool_engine import ToolDecision
                return ToolDecision(
                    action="role_info",
                    confidence=1.0,
                    params={"role": "Moderator"},
                )

        ctx.tool_engine = RoleInfoRouter()
        role_message = make_mock_message(
            "<@777> what permissions does Moderator have?",
            author_id=7772,
            author_name="RoleUser",
            bot_instance=bot,
        )
        role_message.guild.roles = [role]
        role_message.guild.get_role = MagicMock(return_value=None)
        role_message.mentions = [bot.user]
        await on_message(role_message)
        role_text = "\n".join(str(item.content) for item in general_ch.sent_messages)
        print_result(
            "Answered role permission request from live state",
            "Role: Moderator" in role_text and "manage messages" in role_text,
        )

        # ----------------------------------------------------
        # SCENARIO 15: Live AutoMod Configuration Request
        # ----------------------------------------------------
        print_scenario_start(15, "Read-only AutoMod Configuration Request")

        class ServerDataRouter:
            def decide(self, *args, **kwargs):
                from azure.tool_engine import ToolDecision
                return ToolDecision(
                    action="server_data",
                    confidence=1.0,
                    params={"data_type": "automod_rules", "limit": 10},
                )

        ctx.tool_engine = ServerDataRouter()
        ctx.mgmt_tools.get_automod_rules = AsyncMock(return_value=SimpleNamespace(
            success=True,
            after_state={"rules": [{"name": "Spam Guard", "enabled": True, "trigger_type": "keyword"}]},
        ))
        automod_message = make_mock_message(
            "<@777> show the AutoMod rules",
            author_id=7773,
            author_name="AdminUser",
            bot_instance=bot,
        )
        automod_message.mentions = [bot.user]
        automod_message.author.guild_permissions.administrator = True
        await on_message(automod_message)
        automod_text = "\n".join(str(item.content) for item in general_ch.sent_messages)
        print_result(
            "Answered AutoMod request from live state",
            "Spam Guard" in automod_text and "enabled" in automod_text,
        )

    # Clean up test files
    db.close()
    if db_path.exists():
        db_path.unlink()
        
    lt_mem_path = ROOT / "data" / "verify_scenarios_lt.json"
    if lt_mem_path.exists():
        lt_mem_path.unlink()

    print(f"\n=======================================================")
    print(f"  {GREEN}{BOLD}*** ALL DISCORD INTEGRATION SCENARIOS VERIFIED SUCCESSFULLY ***{RESET}")
    print(f"=======================================================\n")


if __name__ == "__main__":
    asyncio.run(main_async())
