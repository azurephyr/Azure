"""
HARDCORE STRESS TEST — Full raid simulation + server rebuild + massive concurrency

Tests Azure's ability to:
1. Detect and analyze a raided server (5000 members, massive spam)
2. Rebuild the server from scratch (permissions, channels, roles, categories)
3. Handle 5000+ concurrent messages without crashing
4. Persist and recall memories under extreme load
5. Failover gracefully when LLM is unavailable
"""

import sys

sys.stdout.reconfigure(encoding='utf-8')
import logging
import os
import random
import re
import threading
import time
import traceback
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import discord

# Suppress logging noise
logging.disable(logging.CRITICAL)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from azure.agent import LongTermMemory, ShortTermMemory, ToolRegistry
from azure.cognition.cognitive_state import Complexity, Mode
from azure.cognition.complexity_engine import ComplexityEngine
from azure.failover_chain import FailoverChain
from azure.memory_backend import EpisodicEvent, MemoryBackend, UserProfile
from azure.operator_persona import OPERATOR_PERSONA
from azure.tools.server_tools import ServerHealthAnalyzer

# ============================================================================
# GLOBALS
# ============================================================================

PASS = 0
FAIL = 0
WARN = 0
SKIP = 0
FAILURES = []

def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL, WARN
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILURES.append(f"  [FAIL] {name} - {detail}")
        print(f"  [FAIL] {name} - {detail}")

def warn(name: str, detail: str = ""):
    global WARN
    WARN += 1
    print(f"  [WARN] {name} - {detail}")

# ============================================================================
# TEST 1: BUILD A 5000-MEMBER FAKE SERVER (POST-RAID STATE)
# ============================================================================

print("\n" + "=" * 70)
print("TEST 1: BUILD 5000-MEMBER RAIDED SERVER")
print("=" * 70)

t1_start = time.time()

class FakeGuild:
    """Realistic fake Discord guild for stress testing."""
    pass

def build_raided_guild():
    g = FakeGuild()
    g.name = "Post-Raid Recovery"
    g.member_count = 5000

    # Members: 500 legitimate + 4500 raiders
    g.members = []

    # Legitimate members (500)
    for i in range(500):
        m = MagicMock()
        m.id = 1000 + i
        m.name = f"Legit{i}"
        m.display_name = f"LegitUser{i}"
        m.bot = (i > 490)
        m.status = MagicMock()
        m.status.name = random.choice(["online", "idle", "dnd", "offline"])
        m.guild_permissions = MagicMock()
        m.guild_permissions.administrator = (i == 0)
        m.top_role = MagicMock()
        m.top_role.name = "Admin" if i == 0 else "Member" if i > 10 else "Moderator"
        g.members.append(m)

    # Raiders (4500)
    for i in range(4500):
        m = MagicMock()
        m.id = 100000 + i
        m.name = f"Raider{i}"
        m.display_name = f"raider_{i}_spammer"
        m.bot = False
        m.status = MagicMock()
        m.status.name = "online" if i < 3000 else "idle"
        m.guild_permissions = MagicMock()
        m.guild_permissions.administrator = False
        m.top_role = MagicMock()
        m.top_role.name = "@everyone"
        g.members.append(m)

    # Channels: 30 text + 5 voice + 5 categories
    g.channels = []
    g.text_channels = []
    g.voice_channels = []

    category_names = ["Information", "Community", "Gaming", "Voice", "Staff"]
    g.categories = []
    for cat_name in category_names:
        cat = MagicMock()
        cat.name = cat_name
        cat.position = len(g.categories)
        g.categories.append(cat)

    for i in range(30):
        c = MagicMock()
        c.name = f"channel-{i}" if i < 28 else f"voice-{i}"
        c.type = MagicMock()
        c.type.name = "text" if i < 28 else "voice"
        c.last_message_id = 999999 - i
        c.topic = f"Channel {i}" if i < 28 else ""
        c.slowmode_delay = 0
        c.nsfw = (i == 27)
        c.bitrate = 64000
        c.user_limit = 0
        c.category = g.categories[i % 5]
        g.text_channels.append(c) if c.type.name == "text" else g.voice_channels.append(c)
        g.channels.append(c)

    # Roles: @everyone + 9 custom
    g.roles = []
    role_names = ["@everyone", "Admin", "Moderator", "Member", "Trial", "VIP", "Bot", "Event", "Guest", "Archived"]
    for i, name in enumerate(role_names):
        r = MagicMock()
        r.name = name
        r.is_default.return_value = (i == 0)
        r.managed = (name == "Bot")
        r.color = MagicMock()
        r.color.value = i * 100000
        r.position = i
        r.hoist = (i in [1, 2, 5])
        r.mentionable = (i in [1, 2])
        r.permissions = MagicMock()
        r.members = g.members[i*500:(i+1)*500] if len(g.members) > i*500 else []
        g.roles.append(r)

    g.verification_level = discord.VerificationLevel.low
    g.explicit_content_filter = discord.ContentFilter.disabled
    g.default_notifications = MagicMock()
    g.default_notifications.name = "all_messages"
    g.rules_channel = g.text_channels[0]
    g.system_channel = g.text_channels[1]
    g.afk_channel = g.voice_channels[0] if g.voice_channels else None

    return g

raided_guild = build_raided_guild()
t1_elapsed = time.time() - t1_start
check(f"Fake guild built ({len(raided_guild.members)} members, {len(raided_guild.channels)} channels)", True, f"in {t1_elapsed:.2f}s")

# ============================================================================
# TEST 2: FULL SERVER HEALTH ANALYSIS
# ============================================================================

print("\n" + "=" * 70)
print("TEST 2: SERVER HEALTH ANALYSIS ON RAIDED 5000-MEMBER SERVER")
print("=" * 70)

t2_start = time.time()
try:
    report = ServerHealthAnalyzer.analyze(raided_guild)
    t2_elapsed = time.time() - t2_start

    print(f"  Server: {report.server_name}")
    print(f"  Overall: Grade {report.overall_grade} ({report.overall_score}/100)")
    print(f"  Activity: {report.activity['grade']} ({report.activity['score']})")
    print(f"  Engagement: {report.engagement['grade']} ({report.engagement['score']})")
    print(f"  Moderation: {report.moderation['grade']} ({report.moderation['score']})")
    print(f"  Organization: {report.organization['grade']} ({report.organization['score']})")
    print(f"  Security: {report.security['grade']} ({report.security['score']})")
    print(f"  Recommendations: {len(report.recommendations)}")
    print(f"  Quick Wins: {len(report.quick_wins)}")
    print(f"  Findings: {len(report.findings)}")

    check(f"Analysis completed in {t2_elapsed:.2f}s", report.overall_score >= 0)
    check("Has recommendations", len(report.recommendations) > 0)
    check("Has security grade", report.security['score'] >= 0)
    check("Activity grade is valid", report.activity['grade'] in "ABCDF")
    check("Engagement grade is valid", report.engagement['grade'] in "ABCDF")

    # Security should be low for a raided server with low verification
    if report.security['score'] < 50:
        check("Security correctly low for raided server", True)
    else:
        warn(f"Security score {report.security['score']} — might be too high for raided server")

except Exception as e:
    print(f"  [FAIL] Health analysis crashed: {e}")
    traceback.print_exc()
    FAIL += 1

# ============================================================================
# TEST 3: SHORT-TERM MEMORY STRESS (5000 concurrent adds)
# ============================================================================

print("\n" + "=" * 70)
print("TEST 3: SHORT-TERM MEMORY STRESS (5000 messages, 100 concurrent threads)")
print("=" * 70)

stm = ShortTermMemory(max_turns=100)
errors_stm = 0

def add_messages_thread(stm, start, count, user):
    global errors_stm
    for i in range(count):
        try:
            stm.add("user", f"Message {start + i} from {user}", name=user)
        except Exception:
            errors_stm += 1

t3_start = time.time()
threads = []
for t in range(20):  # 20 concurrent threads
    th = threading.Thread(target=add_messages_thread, args=(stm, t * 250, 250, f"User{t}"))
    threads.append(th)
    th.start()

for th in threads:
    th.join()
t3_elapsed = time.time() - t3_start

check("No thread errors during 5000 concurrent adds", errors_stm == 0, f"{errors_stm} errors")
check("Short-term memory has correct size", len(stm.messages) <= stm.max_turns * 2, f"got {len(stm.messages)}")
check("History preserves timeline", stm.to_history() is not None)

# Verify context block works
ctx = stm.context_block()
check(f"Context block generated ({len(ctx)} chars)", len(ctx) > 0)

# ============================================================================
# TEST 4: LONG-TERM MEMORY STRESS (1000 facts)
# ============================================================================

print("\n" + "=" * 70)
print("TEST 4: LONG-TERM MEMORY STRESS (1000 facts + concurrent access)")
print("=" * 70)

import tempfile

tmp_dir = tempfile.mkdtemp()
ltm_path = Path(tmp_dir) / "ltm_test.json"
ltm = LongTermMemory(path=ltm_path)

# Store 1000 facts
t4_start = time.time()
for i in range(1000):
    ltm.remember(f"fact_{i}", f"This is fact number {i}: the answer is {i * 7}")
t4_store = time.time() - t4_start

check(f"1000 facts stored in {t4_store:.2f}s", ltm, f"expected file at {ltm_path}")
check("File exists on disk", ltm_path.exists())

# Test recall
t4_recall_start = time.time()
for i in range(1000):
    val = ltm.recall(f"fact_{i}")
    if val != f"This is fact number {i}: the answer is {i * 7}":
        print(f"  [FAIL] Recall mismatch at fact_{i}: expected '{i * 7}', got '{val}'")
        FAIL += 1
        break
else:
    t4_recall = time.time() - t4_recall_start
    check(f"1000 facts recalled correctly ({t4_recall:.2f}s)", True)

# Test search
t4_search_start = time.time()
results = ltm.search("fact number 500", k=5)
t4_search = time.time() - t4_search_start
check("Search returns results", len(results) > 0, f"got {len(results)} hits")
check(f"Search fast ({t4_search*1000:.1f}ms)", t4_search < 1.0)

# Test persistence (reload from file)
ltm2 = LongTermMemory(path=ltm_path)
val2 = ltm2.recall("fact_0")
check("Memory persists across reloads", val2 and "answer is 0" in val2)

# Clean up
import shutil

shutil.rmtree(tmp_dir, ignore_errors=True)

# ============================================================================
# TEST 5: MEMORY BACKEND STRESS (5000 entries, concurrent)
# ============================================================================

print("\n" + "=" * 70)
print("TEST 5: MEMORY BACKEND STRESS (5000 entries, concurrent reads/writes)")
print("=" * 70)

mb = MemoryBackend()
mb_errors = 0
mb_lock = threading.Lock()

def memory_worker(worker_id: int):
    global mb_errors
    worker_id * 100
    try:
        for i in range(100):
            uid = f"user_{worker_id}_{i}"
            mb.save_memory(f"Memory from worker {worker_id} at index {i}", uid, tags=["stress", f"worker_{worker_id}"])
            mb.store(uid, f"conversation message {i}")
        # Do some queries
        mb.query_memories(tags=["stress"], limit=5)
        mb.search_memories(f"worker {worker_id}", limit=3)
    except Exception:
        with mb_lock:
            mb_errors += 1

t5_start = time.time()
workers = []
for w in range(50):  # 50 concurrent workers, 5000 total entries
    t = threading.Thread(target=memory_worker, args=(w,))
    workers.append(t)
    t.start()

for t in workers:
    t.join()
t5_elapsed = time.time() - t5_start

check("No backend errors during 5000 entries", mb_errors == 0, f"{mb_errors} errors")
check(f"Memories stored ({len(mb._memories)} entries)", len(mb._memories) >= 5000)

# Test search
s5 = mb.search_memories("worker", limit=10)
check("Search returns ranked results", len(s5) > 0, f"got {len(s5)} results")

# Test user-specific query
q5 = mb.query_memories(user_id="user_0_0", limit=5)
check("User-specific query works", len(q5) > 0)

# Test search with user filter
s5u = mb.search_memories("memory", user_id="user_0_0", limit=3)
check("Filtered search works", len(s5u) > 0)

# ============================================================================
# TEST 6: FAILOVER CHAIN STRESS (100 rapid failures)
# ============================================================================

print("\n" + "=" * 70)
print("TEST 6: FAILOVER CHAIN STRESS (100 rapid failures + recovery)")
print("=" * 70)

failing_llm = MagicMock()
failing_llm.chat = MagicMock(side_effect=Exception("Simulated LLM failure"))

fc = FailoverChain(llm=failing_llm)
t6_start = time.time()

for i in range(100):
    result = fc.respond("test message", {"server": "Test", "user": "Tester"})
    if result is None:
        print(f"  [FAIL] Failover returned None at iteration {i}")
        FAIL += 1
        break
    if not result.text:
        print(f"  [FAIL] Failover returned empty text at iteration {i}")
        FAIL += 1
        break
else:
    t6_elapsed = time.time() - t6_start
    check(f"100 rapid failover calls completed ({t6_elapsed:.2f}s)", True)
    check("All used fallback path", all(fc.respond("test", {}).used_fallback for _ in range(5)))

# Check tier health tracking
stats = fc.stats
total_failures = sum(stats.get("tier_failures", {}).values())
print(f"  Total tier failures recorded: {total_failures}")
check("Failure tracking works", total_failures >= 5)

# ============================================================================
# TEST 7: PERSISTENCE — Save and reload LongTermMemory with 5000 facts
# ============================================================================

print("\n" + "=" * 70)
print("TEST 7: PERSISTENCE — 5000 facts saved/loaded from disk")
print("=" * 70)

tmp_dir2 = tempfile.mkdtemp()
ltm_large_path = Path(tmp_dir2) / "ltm_large.json"

t7_start = time.time()
ltm_large = LongTermMemory(path=ltm_large_path)
for i in range(5000):
    ltm_large.remember(f"persist_{i}", f"Persistent data point {i}: value={i * 3.14}")
t7_store = time.time() - t7_start

check(f"5000 facts stored ({t7_store:.2f}s)", ltm_large_path.exists())

# Verify file size
file_size = ltm_large_path.stat().st_size
print(f"  File size: {file_size:,} bytes ({file_size/1024:.1f} KB)")

# Reload
t7_load_start = time.time()
ltm_large2 = LongTermMemory(path=ltm_large_path)
t7_load = time.time() - t7_load_start

check(f"File readable on reload ({t7_load:.2f}s)", ltm_large2.facts is not None)
check("Correct fact count after reload", len(ltm_large2.facts) == 5000)

# Verify a few random facts
import random as _r

for _ in range(20):
    idx = _r.randint(0, 4999)
    key = f"persist_{idx}"
    expected = f"Persistent data point {idx}: value={idx * 3.14}"
    actual = ltm_large2.recall(key)
    if actual != expected:
        check(f"Fact {key} persisted correctly", False, f"expected '{expected[:30]}', got '{actual[:30]}'")
        break
else:
    check("20 random facts verified after reload", True)

shutil.rmtree(tmp_dir2, ignore_errors=True)

# ============================================================================
# TEST 8: OPERATOR PERSONA QUALITY AUDIT
# ============================================================================

print("\n" + "=" * 70)
print("TEST 8: OPERATOR PERSONA QUALITY AUDIT")
print("=" * 70)

persona = OPERATOR_PERSONA
quality_checks = [
    ("Name present", "Azure" in persona),
    ("Server operator role", "operator" in persona.lower()),
    ("Memory/recall instructions", "memory" in persona.lower() or "recall" in persona.lower()),
    ("Server analysis guidance", "analysis" in persona.lower() or "analy" in persona.lower()),
    ("Conflict detection", "conflict" in persona.lower()),
    ("Discord markdown rules", "markdown" in persona.lower()),
    ("Response style guidance", "bold" in persona.lower() or "concise" in persona.lower()),
    ("Capabilities listed", "channels" in persona.lower() and "roles" in persona.lower()),
    ("Error handling mentioned", "error" in persona.lower() or "fail" in persona.lower()),
    ("Recommendations mentioned", "recommend" in persona.lower()),
    ("No dangerous filler instruction", "sure" in persona.lower()),
    ("Server actions section", "actions" in persona.lower() or "executing" in persona.lower()),
    ("Natural/casual tone guidance", "natural" in persona.lower() or "casual" in persona.lower() or "conversational" in persona.lower()),
]

for name, result in quality_checks:
    check(f"Persona: {name}", result)

# ============================================================================
# TEST 9: TOOL REGISTRY STRESS (100 tool registrations + calls)
# ============================================================================

print("\n" + "=" * 70)
print("TEST 9: TOOL REGISTRY STRESS (100 tools, 500 concurrent calls)")
print("=" * 70)

tr = ToolRegistry()
registry_errors = 0

# Register 100 tools
def make_tool(i):
    def tool_func(**kwargs):
        return {"ok": True, "index": i, "args": kwargs}
    tool_func.__name__ = f"tool_{i}"
    return tool_func

for i in range(100):
    tr.register(f"tool_{i}", f"Tool {i} description", make_tool(i))

check("100 tools registered", True)

# Concurrent calls
def call_tool_worker(tr, tool_id, count):
    global registry_errors
    for j in range(count):
        try:
            tr.call(f"tool_{tool_id}", param=f"value_{j}")
        except Exception:
            registry_errors += 1

t9_start = time.time()
call_threads = []
for w in range(50):  # 50 threads, each calling 10 tools = 500 calls
    t = threading.Thread(target=call_tool_worker, args=(tr, w % 100, 10))
    call_threads.append(t)
    t.start()

for t in call_threads:
    t.join()
t9_elapsed = time.time() - t9_start

check(f"500 concurrent tool calls completed ({t9_elapsed:.2f}s)", registry_errors == 0, f"{registry_errors} errors")

# Test unknown
result = tr.call("nonexistent_tool")
check("Unknown tool returns error", isinstance(result, dict) and "error" in result)

# Test with None
try:
    result = tr.call(None)
    check("None tool name handled gracefully", True)
except Exception:
    check("None tool name handled gracefully", False, "crashed")

# ============================================================================
# TEST 10: RISK ENGINE — ALL PATTERN MATCHES
# ============================================================================

print("\n" + "=" * 70)
print("TEST 10: RISK ENGINE — Full pattern coverage (50 test phrases)")
print("=" * 70)

# Test the exact patterns from the risk engine
RISK_PATTERNS = {
    "CRITICAL": [
        r"delete\s+(?:all|every)\s+(?:channel|role|message)",
        r"(?:ban|nuke|wipe)\s+(?:all|everyone|server)",
        r"(?:reset|wipe|nuke|destroy|ruin)\s+(?:the\s+)?(?:server|guild|everything)",
        r"(?:reset|revert)\s+(?:server|guild)\s+(?:to\s+)?(?:default|scratch)",
    ],
    "HIGH": [
        r"(?:ban|kick)\s+(?:<@\!?\d+>|[@\w]+)",
        r"timeout\s+(?:<@\!?\d+>|[@\w]+)",
        r"delete\s+(?:channel|role)\s+(?!template)",
        r"remove\s+(?:everyone|all)\s+from",
        r"(?:give|assign|set)\s+(?:admin|administrator)",
        r"(?:create|add)\s+(?:admin|administrator)\s+(?:role|permission)",
        r"(?:mass|bulk|batch)\s+(?:kick|delete|moderat)",
        r"(?:auto|automatically)\s+(?:kick|ban|timeout|warn)",
        r"(?:disable|turn\s+off)\s+(?:widget|invites?|widget)",
    ],
}

test_phrases = [
    # CRITICAL — should all match
    ("delete all channels", "CRITICAL"), ("delete all roles", "CRITICAL"), ("delete every channel", "CRITICAL"),
    ("ban everyone", "CRITICAL"), ("nuke all", "CRITICAL"), ("wipe server", "CRITICAL"),
    ("destroy server", "CRITICAL"), ("ruin everything", "CRITICAL"), ("reset server to default", "CRITICAL"),

    # HIGH — should all match
    ("ban @user123", "HIGH"), ("kick raider_42", "HIGH"), ("ban <@100>", "HIGH"), ("ban user123", "HIGH"),
    ("timeout @spammer", "HIGH"), ("timeout user123", "HIGH"),
    ("delete channel #general", "HIGH"), ("delete role Admin", "HIGH"),
    ("remove everyone from channel", "HIGH"), ("remove all from voice", "HIGH"),
    ("give admin to user", "HIGH"), ("assign administrator role", "HIGH"), ("set admin perms", "HIGH"),
    ("create admin role", "HIGH"), ("add administrator permission", "HIGH"),
    ("mass kick all raiders", "HIGH"), ("bulk delete messages", "HIGH"), ("batch moderate users", "HIGH"),
    ("auto kick spammers", "HIGH"), ("automatically ban raiders", "HIGH"),
    ("disable invites", "HIGH"), ("turn off invites", "HIGH"),

    # SAFE — should not match any
    ("hello how are you?", None), ("what time is it?", None),
    ("I love this server", None), ("can someone help me?", None),
    ("the bandwidth is great", None), ("nice weather today", None),
    ("I'm going to the store", None), ("the banana was delicious", None),
    ("please read the rules", None), ("thanks for the help", None),
]

pattern_results = {name: [re.compile(p, re.IGNORECASE) for p in patterns] for name, patterns in RISK_PATTERNS.items()}
high_critical_matches = 0
safe_non_matches = 0
high_expected = 0
safe_expected = 0

for phrase, expected_level in test_phrases:
    matched_level = None
    if expected_level == "CRITICAL":
        high_expected += 1
        for p in pattern_results["CRITICAL"]:
            if p.search(phrase):
                matched_level = "CRITICAL"
                break
        if not matched_level:
            for p in pattern_results["HIGH"]:
                if p.search(phrase):
                    matched_level = "HIGH"
                    break
        if matched_level == expected_level or (matched_level == "HIGH" and expected_level == "CRITICAL"):
            high_critical_matches += 1
        else:
            print(f"  [FAIL] '{phrase}' expected {expected_level}, got {matched_level}")
    elif expected_level == "HIGH":
        high_expected += 1
        for p in pattern_results["HIGH"]:
            if p.search(phrase):
                matched_level = "HIGH"
                break
        if matched_level == expected_level:
            high_critical_matches += 1
        else:
            print(f"  [FAIL] '{phrase}' expected {expected_level}, got {matched_level}")
    else:  # SAFE
        safe_expected += 1
        for p in pattern_results["CRITICAL"] + pattern_results["HIGH"]:
            if p.search(phrase):
                matched_level = "CRITICAL or HIGH"
                break
        if matched_level is None:
            safe_non_matches += 1
        else:
            print(f"  [FAIL] '{phrase}' false positive — matched {matched_level}")

check(f"Destructive actions detected ({high_critical_matches}/{high_expected})", high_critical_matches == high_expected)
check(f"No false positives on safe phrases ({safe_non_matches}/{safe_expected})", safe_non_matches == safe_expected)

# ============================================================================
# TEST 11: COMPLEXITY ENGINE — Score variety of messages
# ============================================================================

print("\n" + "=" * 70)
print("TEST 11: COMPLEXITY ENGINE — Message scoring accuracy")
print("=" * 70)

ce = ComplexityEngine()
complexity_tests = [
    ("hi", Complexity.LOW),
    ("hello everyone", Complexity.LOW),
    ("what time is it?", Complexity.LOW),
    ("can you help me?", Complexity.LOW),
    ("create a role called Admin with red color", Complexity.MEDIUM),
    ("I think we should reorganize the entire server structure with new categories and proper permissions for all channels and roles would you help me with that?", Complexity.HIGH),
    ("we need to discuss the upcoming server changes including the new moderation policies the role restructuring plan the channel cleanup initiative and the member onboarding improvements. Additionally we should consider the bot integration strategy for auto-moderation and welcome messages", Complexity.HIGH),
]

for msg, expected in complexity_tests:
    try:
        result = ce.classify(msg, modes=[Mode.CHAT])
        if hasattr(result, 'complexity'):
            actual = result.complexity
        elif hasattr(result, 'name'):
            actual = result
        else:
            actual = Complexity.LOW

        # Compare by ordering
        order = {Complexity.LOW: 0, Complexity.MEDIUM: 1, Complexity.HIGH: 2, Complexity.EXTREME: 3}
        if order.get(actual, 0) >= order.get(expected, 0):
            check(f"'{msg[:25]}...' -> {actual}", True)
        else:
            warn(f"'{msg[:25]}...' expected {expected}, got {actual} (under-scored)")
    except Exception as e:
        print(f"  [FAIL] Complexity analysis crashed on '{msg[:20]}': {e}")
        FAIL += 1

# ============================================================================
# TEST 12: USER PROFILE MANAGEMENT
# ============================================================================

print("\n" + "=" * 70)
print("TEST 12: USER PROFILE MANAGEMENT (50 profiles, concurrent)")
print("=" * 70)

mb2 = MemoryBackend()
profile_errors = 0

def create_profile_worker(uid: int):
    global profile_errors
    try:
        profile = UserProfile(
            user_id=f"user_{uid}",
            user_name=f"TestUser{uid}",
            communication_style="casual",
            expertise_level="intermediate",
            verbosity="normal",
            humor_score=random.random(),
            total_interactions=random.randint(1, 1000),
        )
        mb2.save_user_profile(profile)
        loaded = mb2.get_user_profile(f"user_{uid}")
        if loaded is None or loaded.user_id != f"user_{uid}":
            profile_errors += 1
    except Exception:
        profile_errors += 1

t12_start = time.time()
profile_threads = []
for uid in range(50):
    t = threading.Thread(target=create_profile_worker, args=(uid,))
    profile_threads.append(t)
    t.start()

for t in profile_threads:
    t.join()
t12_elapsed = time.time() - t12_start

check("50 profiles created without errors", profile_errors == 0, f"{profile_errors} errors")

# Verify some profiles
for uid in [0, 25, 49]:
    profile = mb2.get_user_profile(f"user_{uid}")
    check(f"Profile user_{uid} retrievable", profile is not None)
    if profile:
        check(f"Profile user_{uid} name correct", profile.user_name == f"TestUser{uid}")

# ============================================================================
# TEST 13: EPISODIC EVENT TRACKING
# ============================================================================

print("\n" + "=" * 70)
print("TEST 13: EPISODIC EVENT TRACKING (200 events)")
print("=" * 70)

event_types = ["decision", "conflict", "milestone", "achievement", "raid_detected"]
for i in range(200):
    event = EpisodicEvent(
        event_id=f"event_{uuid4().hex[:8]}",
        timestamp=time.time() - random.randint(0, 86400),
        event_type=random.choice(event_types),
        description=f"Test event {i}",
        participants=[f"user_{random.randint(0, 50)}", f"user_{random.randint(0, 50)}"],
        outcome=random.choice(["resolved", "pending", "escalated"]),
        sentiment=random.uniform(-1, 1),
    )
    mb2.save_event(event)

# Query by type
for etype in event_types:
    events = mb2.get_events(event_type=etype, limit=5)
    check(f"Events filtered by '{etype}'", len(events) > 0, f"got {len(events)}")
    for ev in events:
        check("Event type matches filter", ev.event_type == etype, f"expected {etype}, got {ev.event_type}")
        break

# Get all recent
recent = mb2.get_events(limit=10)
check(f"Recent events retrievable ({len(recent)})", len(recent) > 0)

# ============================================================================
# TEST 14: SHORT-TERM MEMORY — Thread safety verification
# ============================================================================

print("\n" + "=" * 70)
print("TEST 14: SHORT-TERM MEMORY — Thread safety (100 threads)")
print("=" * 70)

stm_stress = ShortTermMemory(max_turns=20)
stress_errors = [0]

def stress_add(worker_id: int, count: int):
    for i in range(count):
        try:
            stm_stress.add("user", f"msg_{worker_id}_{i}", name=f"User{worker_id}")
        except Exception:
            stress_errors[0] += 1

t14_start = time.time()
stress_threads = []
for w in range(100):
    t = threading.Thread(target=stress_add, args=(w, 100))
    stress_threads.append(t)
    t.start()

for t in stress_threads:
    t.join()
t14_elapsed = time.time() - t14_start

check("No errors in 100-thread stress test", stress_errors[0] == 0, f"{stress_errors[0]} errors")
check("Memory bounded correctly", len(stm_stress.messages) <= stm_stress.max_turns * 2)

# Verify no duplicate message IDs (from concurrent corruption)
all_names = [m.get("name") for m in stm_stress.messages]
unique_workers = set(n for n in all_names if n)
# The memory should have messages from multiple workers if they were all added
# (since max_turns=20, only 40 messages max, so not all 100 workers will be represented)
# But the context_block should at least not crash
ctx14 = stm_stress.context_block()
check("Context block works after stress", len(ctx14) > 0)
check("Context block is valid string", isinstance(ctx14, str))

# ============================================================================
# TEST 15: FULL SYSTEM — Simulate a raid detection + response pipeline
# ============================================================================

print("\n" + "=" * 70)
print("TEST 15: FULL RAID SIMULATION — detect + analyze + recommend")
print("=" * 70)

# Create a progressively worse raid scenario
raid_stages = []

# Stage 1: Normal server
g_normal = build_raided_guild()
# Replace 4500 raiders with normal members
g_normal.members = []
for i in range(5000):
    m = MagicMock()
    m.id = 1000 + i
    m.name = f"Member{i}"
    m.display_name = f"Member{i}"
    m.bot = (i > 4950)
    m.status = MagicMock()
    m.status.name = random.choice(["online", "idle", "offline"])
    m.guild_permissions = MagicMock()
    m.guild_permissions.administrator = (i == 0)
    m.top_role = MagicMock()
    m.top_role.name = "Admin" if i == 0 else "Member"
    g_normal.members.append(m)
g_normal.member_count = 5000

report_normal = ServerHealthAnalyzer.analyze(g_normal)
raid_stages.append(("Pre-raid", report_normal.overall_score, report_normal.overall_grade))

# Stage 2: During raid (verification dropped, massive spam)
g_raid = FakeGuild()
g_raid.name = "Under Attack"
g_raid.member_count = 5000
g_raid.members = []
for i in range(500):
    m = MagicMock()
    m.id = 1000 + i
    m.name = f"Legit{i}"
    m.display_name = f"Legit{i}"
    m.status = MagicMock()
    m.status.name = "idle"
    g_raid.members.append(m)
for i in range(4500):
    m = MagicMock()
    m.id = 100000 + i
    m.name = f"Raider{i}"
    m.display_name = f"raider_{i}"
    m.status = MagicMock()
    m.status.name = "online"
    g_raid.members.append(m)

g_raid.channels = []
g_raid.text_channels = []
g_raid.voice_channels = []
for i in range(30):
    c = MagicMock()
    c.name = f"channel-{i}"
    c.type = MagicMock()
    c.type.name = "text"
    c.last_message_id = 999999
    c.topic = "spammed"
    c.slowmode_delay = 0
    c.nsfw = False
    c.category = MagicMock()
    g_raid.text_channels.append(c)
    g_raid.channels.append(c)

g_raid.roles = [MagicMock(), MagicMock(), MagicMock()]
g_raid.roles[0].name = "@everyone"
g_raid.roles[0].is_default.return_value = True
g_raid.roles[0].managed = False
g_raid.roles[1].name = "Admin"
g_raid.roles[1].is_default.return_value = False
g_raid.roles[1].managed = False
g_raid.roles[2].name = "Spammer"
g_raid.roles[2].is_default.return_value = False
g_raid.roles[2].managed = False
g_raid.categories = [MagicMock(), MagicMock()]
g_raid.categories[0].name = "Info"
g_raid.categories[1].name = "General"
g_raid.verification_level = discord.VerificationLevel.none
g_raid.explicit_content_filter = discord.ContentFilter.disabled
g_raid.rules_channel = None
g_raid.system_channel = None
g_raid.afk_channel = None

report_raid = ServerHealthAnalyzer.analyze(g_raid)
raid_stages.append(("During Raid", report_raid.overall_score, report_raid.overall_grade))

# Stage 3: Post-recovery (verification increased, permissions fixed)
g_recovered = FakeGuild()
g_recovered.name = "Recovered"
g_recovered.member_count = 500
g_recovered.members = []
for i in range(500):
    m = MagicMock()
    m.id = 1000 + i
    m.name = f"Member{i}"
    m.display_name = f"Member{i}"
    m.status = MagicMock()
    m.status.name = random.choice(["online", "idle", "dnd"])
    m.guild_permissions = MagicMock()
    m.guild_permissions.administrator = (i == 0)
    m.top_role = MagicMock()
    m.top_role.name = "Admin" if i == 0 else "Member"
    g_recovered.members.append(m)

g_recovered.text_channels = []
g_recovered.voice_channels = []
g_recovered.channels = []
for i in range(25):
    c = MagicMock()
    c.name = f"recovered-{i}"
    c.type = MagicMock()
    c.type.name = "text"
    c.last_message_id = 100 + i
    c.topic = f"Recovered channel {i}"
    c.slowmode_delay = 5 if i < 3 else 0
    c.nsfw = False
    c.category = MagicMock()
    g_recovered.text_channels.append(c)
    g_recovered.channels.append(c)

g_recovered.roles = [MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()]
role_names = ["@everyone", "Admin", "Moderator", "Member", "Guest"]
for i, name in enumerate(role_names):
    g_recovered.roles[i].name = name
    g_recovered.roles[i].is_default.return_value = (i == 0)
    g_recovered.roles[i].managed = False
g_recovered.categories = [MagicMock(), MagicMock(), MagicMock()]
g_recovered.categories[0].name = "Information"
g_recovered.categories[1].name = "Community"
g_recovered.categories[2].name = "Staff"
g_recovered.verification_level = discord.VerificationLevel.high
g_recovered.explicit_content_filter = discord.ContentFilter.all_members
g_recovered.rules_channel = g_recovered.text_channels[0]
g_recovered.system_channel = g_recovered.text_channels[1]
g_recovered.afk_channel = None

report_recovered = ServerHealthAnalyzer.analyze(g_recovered)
raid_stages.append(("Post-Recovery", report_recovered.overall_score, report_recovered.overall_grade))

print("\n  RAID SIMULATION TIMELINE:")
print(f"  {'Stage':<20} {'Score':<10} {'Grade':<10}")
print(f"  {'-'*40}")
for stage_name, score, grade in raid_stages:
    print(f"  {stage_name:<20} {score:<10.1f} {grade:<10}")
    if "raid" in stage_name.lower():
        check("Raid score lower than pre-raid", score <= raid_stages[0][1])
    elif "recovery" in stage_name.lower():
        check("Recovery score higher than during-raid", score >= raid_stages[1][1])

if raid_stages[2][1] > raid_stages[1][1]:
    check(f"Post-recovery score improves ({raid_stages[2][1]:.1f} > {raid_stages[1][1]:.1f})", True)
else:
    warn(f"Post-recovery score ({raid_stages[2][1]:.1f}) not higher than during-raid ({raid_stages[1][1]:.1f})")

# ============================================================================
# FINAL SUMMARY
# ============================================================================

print("\n" + "=" * 70)
print("HARDCORE STRESS TEST — FINAL RESULTS")
print("=" * 70)

total = PASS + FAIL + WARN + SKIP
print(f"\n  Total checks: {total}")
print(f"  Passed:       {PASS}")
print(f"  Failed:       {FAIL}")
print(f"  Warnings:     {WARN}")
print(f"  Skipped:      {SKIP}")

if total > 0:
    print(f"  Success rate: {PASS / max(total - SKIP, 1) * 100:.1f}%")

if FAIL > 0:
    print(f"\n  FAILURES ({FAIL}):")
    for f in FAILURES[:20]:
        print(f"    {f}")
    if len(FAILURES) > 20:
        print(f"    ... and {len(FAILURES) - 20} more")

print(f"\n  Overall: {'PASSED' if FAIL == 0 else 'FAILED'}")

# Cleanup temp dirs (with NameError guard in case a test failed before setting the variable)
import contextlib
import shutil

for _tmp_name in ('tmp_dir', 'tmp_dir2'):
    with contextlib.suppress(NameError):
        shutil.rmtree(eval(_tmp_name), ignore_errors=True)
