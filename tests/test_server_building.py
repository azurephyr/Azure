"""
Test: Full server building flow — "make my discord server better"
Simulates what the LLM-generated plan would look like and executes it.
"""
import sys

sys.stdout.reconfigure(encoding='utf-8')
import asyncio
import logging
import os
import random
from unittest.mock import AsyncMock, MagicMock

logging.disable(logging.CRITICAL)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from azure.cognition.cognitive_state import Mode
from azure.cognition.complexity_engine import Complexity, ComplexityEngine
from azure.cognition.risk_engine import Risk, RiskEngine
from azure.discord_tools import DiscordManagementTools, StepResult

PASS = 0; FAIL = 0; WARN = 0
def check(name, ok, detail=""):
    global PASS, FAIL, WARN
    if ok:
        PASS += 1; print(f"  [PASS] {name}")
    else:
        FAIL += 1; print(f"  [FAIL] {name} - {detail}")
def warn(name, detail=""):
    global WARN; WARN += 1; print(f"  [WARN] {name} - {detail}")

class FakeGuild:
    pass

class MockColor:
    def __init__(self, value=0): self.value = value
    def __str__(self): return f"#{self.value:06x}"
    def __eq__(self, o): return isinstance(o, MockColor) and self.value == o.value

class MockRole:
    def __init__(self, name, color=None, position=0, hoist=False, mentionable=False, permissions=None):
        self.name = name
        self.id = random.randint(10000, 99999)
        self.color = color or MockColor()
        self.position = position
        self.hoist = hoist
        self.mentionable = mentionable
        self.permissions = permissions or []
        self.members = []
        self.is_default = lambda: name == "@everyone"
        self.managed = False
    def __repr__(self): return f"MockRole({self.name})"

class MockChannel:
    def __init__(self, name, ch_type="text", category=None, topic=None, position=0):
        self.name = name
        self.id = random.randint(100000, 999999)
        self.type = ch_type
        self.category = category
        self.topic = topic
        self.position = position
        self.guild = None
        self.members = []
        self.set_permissions = AsyncMock(return_value=None)
    def __repr__(self): return f"MockChannel({self.name}, {self.type})"

class MockCategory:
    def __init__(self, name, position=0):
        self.name = name
        self.id = random.randint(50000, 99999)
        self.position = position
        self.channels = []
    def __repr__(self): return f"MockCategory({self.name})"

def build_empty_guild():
    """A brand-new server with absolutely nothing set up."""
    g = FakeGuild()
    g.name = "New YouTube Server"
    g.member_count = 1
    g.members = []

    # Owner
    owner = MagicMock()
    owner.id = 1; owner.name = "Creator"; owner.display_name = "Creator"
    owner.bot = False; owner.guild_permissions = MagicMock()
    owner.guild_permissions.administrator = True
    owner.top_role = MagicMock(); owner.top_role.name = "@everyone"
    owner.mention = "@Creator"; owner.roles = []
    g.members.append(owner)
    g.owner_id = 1; g.owner = owner; g.me = owner

    # @everyone role
    everyone = MockRole("@everyone", color=MockColor(0), position=0, permissions=["read_messages"])
    everyone.members = g.members

    g.roles = [everyone]
    g.channels = []
    g.categories = []
    g.text_channels = []
    g.voice_channels = []
    g.forums = []
    g.stage_channels = []
    g.threads = []
    g.system_channel = None
    g.afk_channel = None
    g.rules_channel = None
    g.public_updates_channel = None
    g.widget_channel = None
    g.get_member = lambda uid: next((m for m in g.members if m.id == uid), None)

    g._find_channel_by_name = lambda name: next((c for c in g.channels if c.name == name), None)

    # Real discord.Guild API methods
    async def create_role(name, color=0, permissions=None, hoist=False, mentionable=False, reason=""):
        role = MockRole(name, color=MockColor(color) if isinstance(color, int) else (color or MockColor()),
                       position=len(g.roles), hoist=hoist, mentionable=mentionable,
                       permissions=permissions or [])
        role.guild = g
        g.roles.append(role)
        # Update everyone's role list
        for m in g.members:
            if hasattr(m, 'roles') and role not in m.roles:
                m.roles.append(role)
        return role

    async def create_category(name, reason=""):
        cat = MockCategory(name, position=len(g.categories))
        cat.guild = g
        g.categories.append(cat)
        return cat

    async def create_text_channel(name, category=None, topic=None, reason=""):
        ch = MockChannel(name, "text", category=category, topic=topic, position=len(g.channels))
        ch.guild = g
        g.channels.append(ch)
        g.text_channels.append(ch)
        if category:
            category.channels.append(ch)
        return ch

    async def create_voice_channel(name, category=None, reason=""):
        ch = MockChannel(name, "voice", category=category, position=len(g.channels))
        ch.guild = g
        g.channels.append(ch)
        g.voice_channels.append(ch)
        if category:
            category.channels.append(ch)
        return ch

    g.create_role = create_role
    g.create_category = create_category
    g.create_text_channel = create_text_channel
    g.create_voice_channel = create_voice_channel

    return g

# Step 1: Build the empty guild
print("\n" + "=" * 70)
print("STEP 1: BUILD EMPTY GUILD (brand new server, nothing configured)")
print("=" * 70)
guild = build_empty_guild()
check("Guild created", guild.name == "New YouTube Server")
check("0 channels", len(guild.channels) == 0)
check("0 categories", len(guild.categories) == 0)
check("1 role (@everyone only)", len(guild.roles) == 1)
print(f"  Server: '{guild.name}', Members: {guild.member_count}")

# Step 2: The LLM-generated plan for a YouTube community server
# This is what the LLM would produce given the prompt.
# The actual LLM would generate something like this:
print("\n" + "=" * 70)
print('STEP 2: SIMULATE LLM PLAN GENERATION')
print('  Prompt: "I want you to make my discord server way better')
print('           looking with channels and everything. its for a')
print('           community server for my youtube channel"')
print("=" * 70)

llm_plan = {
    "analysis": "This is a brand new YouTube community server. We need to build the entire structure from scratch: roles for subscribers/mods, organized categories with themed channels, voice channels for streaming, and proper permission setup.",
    "steps": [
        {"action": "create_role", "name": "Subscribers", "color": "red", "hoist": True},
        {"action": "create_role", "name": "Moderators", "color": "green", "hoist": True, "mentionable": True},
        {"action": "create_role", "name": "VIP", "color": "purple", "hoist": True, "mentionable": True},
        {"action": "create_category", "name": "📢 ANNOUNCEMENTS"},
        {"action": "create_category", "name": "💬 COMMUNITY"},
        {"action": "create_category", "name": "🎮 GAMING"},
        {"action": "create_category", "name": "🔊 VOICE CHANNELS"},
        {"action": "create_channel", "name": "welcome", "type": "text", "category": "📢 ANNOUNCEMENTS", "topic": "Welcome to the community! Read the rules and introduce yourself."},
        {"action": "create_channel", "name": "announcements", "type": "text", "category": "📢 ANNOUNCEMENTS", "topic": "Official server announcements and updates."},
        {"action": "create_channel", "name": "rules", "type": "text", "category": "📢 ANNOUNCEMENTS", "topic": "Server rules — please read before participating."},
        {"action": "create_channel", "name": "general-chat", "type": "text", "category": "💬 COMMUNITY", "topic": "General discussion about videos and anything else!"},
        {"action": "create_channel", "name": "introductions", "type": "text", "category": "💬 COMMUNITY", "topic": "New here? Tell us about yourself!"},
        {"action": "create_channel", "name": "media-share", "type": "text", "category": "💬 COMMUNITY", "topic": "Share your memes, clips, and fan art."},
        {"action": "create_channel", "name": "suggestions", "type": "text", "category": "💬 COMMUNITY", "topic": "Suggest video ideas and server improvements."},
        {"action": "create_channel", "name": "looking-to-play", "type": "text", "category": "🎮 GAMING", "topic": "Find people to game with!"},
        {"action": "create_channel", "name": "game-clips", "type": "text", "category": "🎮 GAMING", "topic": "Share your best gaming moments."},
        {"action": "create_channel", "name": "stream-announcements", "type": "text", "category": "🎮 GAMING", "topic": "Go live alerts and stream schedules."},
        {"action": "create_channel", "name": "General VC", "type": "voice", "category": "🔊 VOICE CHANNELS"},
        {"action": "create_channel", "name": "Gaming VC", "type": "voice", "category": "🔊 VOICE CHANNELS"},
        {"action": "create_channel", "name": "Music VC", "type": "voice", "category": "🔊 VOICE CHANNELS"},
        {"action": "set_permissions", "channel_name": "announcements", "role": "@everyone", "deny": ["send_messages"]},
        {"action": "set_permissions", "channel_name": "rules", "role": "@everyone", "deny": ["send_messages"]},
        {"action": "set_permissions", "channel": "mod-chat", "role": "Moderators", "allow": ["read_messages", "send_messages", "manage_messages"]},
    ]
}

check("Plan has analysis", "analysis" in llm_plan)
check("Plan has steps", len(llm_plan["steps"]) >= 20)
print(f"  Analysis: {llm_plan['analysis'][:80]}...")
print(f"  Steps: {len(llm_plan['steps'])} actions planned")

# Step 3: Check permissions required
print("\n" + "=" * 70)
print("STEP 3: PERMISSION CHECK")
print("=" * 70)

perms_needed = set()
for step in llm_plan["steps"]:
    a = step["action"]
    if a in ("create_role",):
        perms_needed.add("manage_roles")
    if a in ("create_category", "create_channel"):
        perms_needed.add("manage_channels")
    if a in ("set_permissions",):
        perms_needed.add("manage_channels")
check(f"Permissions needed: {', '.join(sorted(perms_needed))}", len(perms_needed) > 0)
check("manage_roles required", "manage_roles" in perms_needed)
check("manage_channels required", "manage_channels" in perms_needed)

# Step 4: Check risk levels
print("\n" + "=" * 70)
print("STEP 4: RISK ANALYSIS (what would RiskEngine say?)")
print("=" * 70)

re = RiskEngine()
modes = [Mode.TOOL]
risk_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
risky_steps = []
for step in llm_plan["steps"]:
    text = f"{step['action']} {step.get('name', '')}"
    result = re.classify(text, modes)
    risk = result[0]
    risk_counts[risk.name] = risk_counts.get(risk.name, 0) + 1
    if risk in (Risk.HIGH, Risk.CRITICAL):
        risky_steps.append(f"  {risk.name}: {text}")

check("No CRITICAL risk actions", risk_counts.get("CRITICAL", 0) == 0)
check("All actions LOW/MEDIUM", risk_counts.get("HIGH", 0) == 0 and risk_counts.get("CRITICAL", 0) == 0)
for rs in risky_steps:
    print(f"  {rs}")
print(f"  Risk breakdown: {risk_counts}")

# Step 5: Preflight check
print("\n" + "=" * 70)
print("STEP 5: COMPLEXITY CLASSIFICATION")
print("=" * 70)

ce = ComplexityEngine()
full_text = "I want you to make my discord server way better looking with channels and everything. its for a community server for my youtube channel"
complexity = ce.classify(full_text, modes)
check(f"Complexity classified: {complexity}", complexity in Complexity)
print(f"  Raw text complexity: {complexity}")

# Step 6: Plan structure validation
print("\n" + "=" * 70)
print("STEP 6: PLAN STRUCTURE VALIDATION")
print("=" * 70)

# Check valid action types
valid_actions = {"create_role", "edit_role", "delete_role", "assign_role", "remove_role",
    "create_category", "edit_category", "delete_category",
    "create_channel", "edit_channel", "delete_channel", "move_channel",
    "set_permissions", "clear_permissions", "sync_permissions",
    "set_server_name", "set_verification_level", "set_content_filter",
    "set_afk_channel", "set_system_channel", "set_rules_channel",
    "kick", "ban", "unban", "timeout", "set_nickname",
    "create_webhook", "delete_webhook",
    "create_scheduled_event", "delete_scheduled_event",
    "create_invite", "pin_message", "unpin_message",
    "create_thread", "archive_thread",
    "create_forum_channel", "create_forum_post", "manage_forum_tags",
    "create_stage_channel", "start_stage_instance", "manage_stage_speaker",
    "create_sticker", "delete_sticker", "create_emoji", "delete_emoji",
    "create_automod_rule", "enable_spam_filter", "enable_keyword_filter",
    "set_welcome_screen", "create_server_template", "sync_server_template",
    "set_voice_bitrate", "set_voice_user_limit", "set_voice_region",
    "get_audit_logs", "find_who_did_action",
    "list_channels", "list_roles", "move_voice", "deafen", "mute"}

invalid = []
for step in llm_plan["steps"]:
    if step["action"] not in valid_actions:
        invalid.append(f"  {step['action']}")
check("All step actions are valid", len(invalid) == 0)
if invalid:
    for i in invalid:
        print(i)

# Check ordering: categories before channels, roles before permissions
categories_before = {s["name"]: i for i, s in enumerate(llm_plan["steps"])
                     if s["action"] == "create_category"}
channels_after = [(s.get("category", ""), i) for i, s in enumerate(llm_plan["steps"])
                  if s["action"] == "create_channel"]
order_ok = True
for cat_name, ch_idx in channels_after:
    if cat_name and cat_name in categories_before and categories_before[cat_name] >= ch_idx:
        order_ok = False
        break
check("Categories created before channels", order_ok)

# Step 7: Try to execute the plan (simulated - no real Discord)
print("\n" + "=" * 70)
print("STEP 7: SIMULATED EXECUTION (trace through tools)")
print("=" * 70)

# Create a mock bot
bot = MagicMock()
bot.user = MagicMock()
bot.user.id = 999
bot.user.name = "Azure"
bot.wait_for = AsyncMock()

# Initialize DiscordManagementTools
tools = DiscordManagementTools(bot)

async def simulate_plan():
    results = []
    for i, step in enumerate(llm_plan["steps"]):
        action = step["action"]
        name = step.get("name", "")
        try:
            if action == "create_role":
                r = await tools.create_role(guild, name=name, color=step.get("color"),
                    hoist=step.get("hoist", False), mentionable=step.get("mentionable", False))
            elif action == "create_category":
                r = await tools.create_category(guild, name=name)
            elif action == "create_channel":
                r = await tools.create_channel(guild, name=name, channel_type=step.get("type", "text"),
                    category=step.get("category"), topic=step.get("topic"))
            elif action == "set_permissions":
                ch_name = step.get("channel", step.get("channel_name", ""))
                ch = guild._find_channel_by_name(ch_name)
                if ch:
                    r = await tools.set_channel_permissions(ch, role_name=step.get("role", "@everyone"),
                        allow=step.get("allow"), deny=step.get("deny"))
                else:
                    r = StepResult(success=False, action=action, name=name, error=f"Channel '{ch_name}' not found")
            else:
                r = StepResult(success=False, action=action, name=name, error=f"Unknown action: {action}")
            results.append(r)
        except Exception as e:
            results.append(StepResult(success=False, action=action, name=name, error=str(e)))

        status = "+" if results[-1].success else "-"
        print(f"  {i+1:2d}. [{status}] {action}: '{name}'" +
              (f" -> {results[-1].detail}" if results[-1].success else f" -> FAIL: {results[-1].error}"))
    return results

results = asyncio.run(simulate_plan())
success = sum(1 for r in results if r.success)
check(f"Planned execution: {success}/{len(results)} steps simulated", success > 0)

# Step 8: Post-execution guild state
print("\n" + "=" * 70)
print("STEP 8: POST-EXECUTION GUILD STATE")
print("=" * 70)

# Check what was actually created
check("Roles created", len(guild.roles) > 1)
for r in guild.roles:
    if hasattr(r, 'name'):
        print(f"  Role: {r.name} (color: {r.color})" if r.name != "@everyone" else f"  Role: {r.name}")

check("Categories created", len(guild.categories) > 0)
for c in guild.categories:
    print(f"  Category: {c.name}")

check("Channels created", len(guild.channels) > 0)
for c in guild.channels:
    cat_info = f" in {c.category.name}" if hasattr(c, 'category') and c.category else ""
    print(f"  Channel: {c.name} ({c.type}){cat_info}")

# Step 9: Final scores
print("\n" + "=" * 70)
print("FINAL REPORT: SERVER BUILDING TEST")
print("=" * 70)

categories_built = len(guild.categories)
channels_built = len(guild.channels)
roles_built = len(guild.roles) - 1  # exclude @everyone

print("\n  BEFORE: Nothing (0 channels, 0 categories, 1 role)\n")
print("  AFTER: ")
print(f"    Roles:      {roles_built}")
print(f"    Categories: {categories_built}")
print(f"    Channels:   {channels_built} ({sum(1 for c in guild.channels if c.type == 'text')} text, {sum(1 for c in guild.channels if c.type == 'voice')} voice)")
print(f"    Executed:   {success}/{len(results)} steps succeeded")

score = (roles_built >= 2) + (categories_built >= 3) + (channels_built >= 8) + (success >= 18)
grade = ["F", "D", "C", "B", "A"][score]
print(f"\n  Server Building Score: {score}/4 — Grade: {grade}")

print(f"\n{'='*70}")
print(f"  TOTAL: {PASS + FAIL} checks | PASS: {PASS} | FAIL: {FAIL} | WARN: {WARN}")
print(f"{'='*70}")
if FAIL:
    sys.exit(1)
print("  SERVER BUILDING: ALL CHECKS PASSED")
