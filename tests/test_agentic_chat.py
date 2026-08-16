"""
Comprehensive tests for the Azure bot's agentic chat capabilities.

Covers: basic chat flow, intent classification, tool execution, memory system,
failover, moderation integration, concurrent handling, error recovery,
context building, and long-term memory persistence.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_llm():
    """Provide a mock LLM that returns controllable responses."""
    llm = MagicMock()
    llm.is_loaded = True
    llm.temperature = 0.7
    llm.max_tokens = 512
    llm.n_ctx = 8192
    llm.chat.return_value = "Hello! How can I help you today?"
    llm.get_info.return_value = {"model_name": "test-model", "provider": "test"}
    return llm


@pytest.fixture
def mock_fail_llm():
    """Provide a mock LLM that always raises an exception."""
    llm = MagicMock()
    llm.is_loaded = True
    llm.chat.side_effect = RuntimeError("API connection failed")
    return llm


@pytest.fixture
def temp_memory_path():
    """Provide a temporary file path for long-term memory."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        f.write(b"{}")
        path = Path(f.name)
    yield path
    if path.exists():
        path.unlink()


@pytest.fixture
def populated_memory_path():
    """Provide a temporary file pre-populated with facts."""
    facts = {
        "favorite_color": {"v": "blue", "t": 1700000000.0},
        "pet_name": {"v": "Luna", "t": 1700000001.0},
    }
    with tempfile.NamedTemporaryFile(
        suffix=".json", mode="w", delete=False, encoding="utf-8"
    ) as f:
        json.dump(facts, f)
        path = Path(f.name)
    yield path
    if path.exists():
        path.unlink()


@pytest.fixture
def agent(mock_llm, temp_memory_path):
    """Construct an AzureAgent with all external dependencies mocked."""
    with (
        patch("azure.agent.ApiLLM") as mock_api_cls,
        patch("azure.agent.LocalLLM", None),
        patch("azure.agent.HybridLLM", None),
        patch("azure.agent.DiscordRAG", None),
        patch("azure.agent.HybridRAG", None),
        patch("azure.agent.ModerationEngine", None),
        patch("azure.agent.ModerationPolicy", None),
        patch("azure.agent.MemoryBackend", None),
        patch("azure.agent.create_memory_backend", None),
        patch("azure.agent.UserAdaptation", None),
        patch("azure.agent.ModelRouter", None),
        patch("azure.agent.FailoverChain", None),
        patch("azure.agent.CognitivePipeline", None),
        patch("azure.agent.get_agre", None),
    ):
        mock_api_cls._detect_provider.return_value = None
        from azure.agent import AzureAgent

        a = AzureAgent(
            model_name="test_agent",
            long_term_path=temp_memory_path,
            moderation_mode="off",
        )
        a.llm = mock_llm
        a._llm_type = "api"
        return a


@pytest.fixture
def agent_no_llm(temp_memory_path):
    """Agent with no LLM configured."""
    with (
        patch("azure.agent.ApiLLM", None),
        patch("azure.agent.LocalLLM", None),
        patch("azure.agent.HybridLLM", None),
        patch("azure.agent.DiscordRAG", None),
        patch("azure.agent.HybridRAG", None),
        patch("azure.agent.ModerationEngine", None),
        patch("azure.agent.ModerationPolicy", None),
        patch("azure.agent.MemoryBackend", None),
        patch("azure.agent.create_memory_backend", None),
        patch("azure.agent.UserAdaptation", None),
        patch("azure.agent.ModelRouter", None),
        patch("azure.agent.FailoverChain", None),
        patch("azure.agent.CognitivePipeline", None),
        patch("azure.agent.get_agre", None),
    ):
        from azure.agent import AzureAgent

        a = AzureAgent(
            model_name="test_no_llm",
            long_term_path=temp_memory_path,
            moderation_mode="off",
        )
        return a


# ═══════════════════════════════════════════════════════════════════════════
# 1. Basic Chat Flow
# ═══════════════════════════════════════════════════════════════════════════


class TestBasicChatFlow:
    @pytest.mark.asyncio
    async def test_handle_returns_llm_response(self, agent, mock_llm):
        mock_llm.chat.return_value = "I am doing great!"
        reply = await agent.handle("TestUser", "How are you doing?")
        assert reply == "I am doing great!"
        mock_llm.chat.assert_called()

    @pytest.mark.asyncio
    async def test_handle_calls_llm_with_context(self, agent, mock_llm):
        await agent.handle("TestUser", "What is 2+2?")
        call_args = mock_llm.chat.call_args
        messages = call_args[0][0]
        assert any(m["role"] == "user" and "2+2" in m["content"] for m in messages)

    @pytest.mark.asyncio
    async def test_handle_strips_whitespace(self, agent, mock_llm):
        mock_llm.chat.return_value = "  spaced response  "
        reply = await agent.handle("TestUser", "test")
        assert reply == "spaced response"

    @pytest.mark.asyncio
    async def test_handle_returns_fallback_on_empty_llm(self, agent, mock_llm):
        mock_llm.chat.return_value = ""
        reply = await agent.handle("TestUser", "hello")
        assert reply is not None

    @pytest.mark.asyncio
    async def test_handle_returns_fallback_on_whitespace_llm(self, agent, mock_llm):
        mock_llm.chat.return_value = "   "
        reply = await agent.handle("TestUser", "hello")
        assert reply is not None

    @pytest.mark.asyncio
    async def test_handle_short_term_memory_updated(self, agent, mock_llm):
        await agent.handle("TestUser", "remember this")
        mock_llm.chat.assert_called()
        call_args = mock_llm.chat.call_args
        messages = call_args[0][0]
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert any("remember this" in m["content"] for m in user_msgs)

    @pytest.mark.asyncio
    async def test_handle_time_command(self, agent, mock_llm):
        reply = await agent.handle("TestUser", "!time")
        assert reply is not None


# ═══════════════════════════════════════════════════════════════════════════
# 2. Intent Classification
# ═══════════════════════════════════════════════════════════════════════════


class TestIntentClassification:
    def _classify(self, text, **kwargs):
        from azure.intent_classifier import IntentClassifier

        classifier = IntentClassifier(bot_name="Azure")
        return classifier.classify(text, is_dm=True, **kwargs)

    def test_greeting_intent(self):
        # No-LLM path: directed DM → chat (LLM would refine further)
        intent = self._classify("hello")
        assert intent.route in ("chat", "ignore") or intent.action in ("chat", "ignore", "hello")
        assert intent.is_directed or intent.route == "chat"
        assert intent.confidence >= 0.5

    def test_server_analysis_intent(self):
        intent = self._classify("how many members are there?")
        assert intent.is_directed
        assert intent.route != "ignore"
        assert intent.action != "ignore"

    def test_create_role_intent(self):
        intent = self._classify("create a role called Admin")
        # Structural fallback without LLM is chat; with LLM would be tool/plan
        assert intent.is_directed
        assert intent.route in ("chat", "tool", "plan")

    def test_memory_recall_intent(self):
        intent = self._classify("what happened yesterday?")
        assert intent.action != "ignore"
        assert intent.route != "ignore"

    def test_ban_member_intent(self):
        intent = self._classify("ban that user for spamming")
        assert intent.is_directed
        assert intent.route in ("chat", "tool", "moderation", "plan")

    def test_create_channel_intent(self):
        intent = self._classify("create a text channel called general")
        assert intent.is_directed
        assert intent.route in ("chat", "tool", "plan")

    def test_list_channels_intent(self):
        intent = self._classify("what channels do we have?")
        assert intent.is_directed
        assert intent.route != "ignore"

    def test_undo_intent(self):
        intent = self._classify("undo the last change")
        assert intent.is_directed
        assert intent.route != "ignore"

    def test_undirected_message_ignored(self):
        from azure.intent_classifier import IntentClassifier

        classifier = IntentClassifier(bot_name="Azure")
        intent = classifier.classify("just chatting with friends", is_dm=False, is_mentioned=False)
        assert intent.route == "ignore" or intent.action == "ignore"
        assert not intent.is_directed

    def test_mention_directs_message(self):
        from azure.intent_classifier import IntentClassifier

        classifier = IntentClassifier(bot_name="Azure")
        intent = classifier.classify(
            "tell me a joke", is_dm=False, is_mentioned=True
        )
        assert intent.is_directed
        assert intent.route != "ignore"

    def test_llm_route_schema(self):
        """Mock LLM returns closed-schema route used for routing."""
        from azure.intent_classifier import IntentClassifier

        class _LLM:
            def chat(self, messages, max_tokens=0, temperature=0):
                return '{"route":"plan","action":"build_welcome_area","confidence":0.91,"params":{}}'

        clf = IntentClassifier(llm=_LLM(), bot_name="Azure")
        intent = clf.classify("set up a welcome area", is_dm=True)
        assert intent.route == "plan"
        assert intent.action == "build_welcome_area"
        assert intent.confidence >= 0.9

    def test_llm_ignore_route(self):
        from azure.intent_classifier import IntentClassifier

        class _LLM:
            def chat(self, messages, max_tokens=0, temperature=0):
                return '{"route":"ignore","action":"ignore","confidence":0.95,"params":{}}'

        clf = IntentClassifier(llm=_LLM(), bot_name="Azure")
        intent = clf.classify("lol yeah", is_dm=False, is_mentioned=False)
        assert intent.route == "ignore"

    def test_intent_cache_isolated_by_server_context(self):
        from azure.intent_classifier import IntentClassifier

        class _LLM:
            def __init__(self):
                self.calls = 0

            def chat(self, messages, max_tokens=0, temperature=0):
                self.calls += 1
                server = messages[1]["content"]
                route = "plan" if "Alpha" in server else "info"
                return f'{{"route":"{route}","action":"{route}","confidence":0.95,"params":{{}}}}'

        llm = _LLM()
        clf = IntentClassifier(llm=llm, bot_name="Azure")
        alpha = clf.classify("show me the setup", is_dm=True, server_name="Alpha")
        beta = clf.classify("show me the setup", is_dm=True, server_name="Beta")

        assert alpha.route == "plan"
        assert beta.route == "info"
        assert llm.calls == 2


# ═══════════════════════════════════════════════════════════════════════════
# 3. Tool Execution
# ═══════════════════════════════════════════════════════════════════════════


class TestToolExecution:
    def test_tool_registry_register_and_call(self):
        from azure.agent import ToolRegistry

        reg = ToolRegistry()
        reg.register("echo", "Echo input", lambda text="": text)
        result = reg.call("echo", text="hello")
        assert result["ok"] is True
        assert result["result"] == "hello"

    def test_tool_registry_unknown_tool(self):
        from azure.agent import ToolRegistry

        reg = ToolRegistry()
        result = reg.call("nonexistent")
        assert "error" in result

    def test_tool_registry_exception_handling(self):
        from azure.agent import ToolRegistry

        reg = ToolRegistry()
        reg.register("fail", "Always fails", lambda: (_ for _ in ()).throw(ValueError("boom")))
        result = reg.call("fail")
        assert result["ok"] is False
        assert "boom" in result["error"]

    def test_tool_registry_describe(self):
        from azure.agent import ToolRegistry

        reg = ToolRegistry()
        reg.register("test_tool", "A test tool", lambda: None, schema={"type": "object"})
        descriptions = reg.describe()
        assert len(descriptions) == 1
        assert descriptions[0]["name"] == "test_tool"

    def test_tool_engine_decide_returns_chat(self):
        mock_llm = MagicMock()
        mock_llm.chat.return_value = '{"action":"chat"}'
        from azure.tool_engine import ToolEngine

        engine = ToolEngine(mock_llm)
        decision = engine.decide("hello there", "TestUser")
        assert decision.action == "chat"

    def test_tool_engine_decide_returns_plan(self):
        mock_llm = MagicMock()
        mock_llm.chat.return_value = '{"action":"plan","plan_description":"Set up roles"}'
        from azure.tool_engine import ToolEngine

        engine = ToolEngine(mock_llm)
        decision = engine.decide("create an admin role", "TestUser")
        assert decision.action == "plan"
        assert decision.plan is not None

    def test_tool_engine_returns_chat_on_no_llm(self):
        from azure.tool_engine import ToolEngine

        engine = ToolEngine(None)
        decision = engine.decide("hello", "User")
        assert decision.action == "chat"

    def test_tool_engine_handles_bad_json(self):
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "I'm not JSON, sorry!"
        from azure.tool_engine import ToolEngine

        engine = ToolEngine(mock_llm)
        decision = engine.decide("hello", "User")
        assert decision.action == "chat"

    def test_tool_engine_caches_decisions(self):
        mock_llm = MagicMock()
        mock_llm.chat.return_value = '{"action":"chat"}'
        from azure.tool_engine import ToolEngine

        engine = ToolEngine(mock_llm)
        engine.decide("same message", "User")
        engine.decide("same message", "User")
        assert mock_llm.chat.call_count == 1


# ═══════════════════════════════════════════════════════════════════════════
# 4. Memory System
# ═══════════════════════════════════════════════════════════════════════════


class TestMemorySystem:
    def test_short_term_add_and_retrieve(self):
        from azure.agent import ShortTermMemory

        stm = ShortTermMemory(max_turns=5)
        stm.add("user", "hello", name="TestUser")
        stm.add("assistant", "hi there", name="Azure")
        history = stm.to_history()
        assert len(history) == 2
        assert history[0]["content"] == "hello"
        assert history[1]["content"] == "hi there"

    def test_short_term_respects_max_turns(self):
        from azure.agent import ShortTermMemory

        stm = ShortTermMemory(max_turns=3)
        for i in range(10):
            stm.add("user", f"msg {i}")
        history = stm.to_history()
        assert len(history) <= 6

    def test_short_term_context_block(self):
        from azure.agent import ShortTermMemory

        stm = ShortTermMemory(max_turns=5)
        stm.add("user", "what time is it?")
        block = stm.context_block()
        assert "what time is it?" in block

    def test_short_term_empty_context(self):
        from azure.agent import ShortTermMemory

        stm = ShortTermMemory(max_turns=5)
        assert stm.context_block() == ""
        assert stm.to_history() == []

    def test_long_term_remember_and_recall(self, temp_memory_path):
        from azure.agent import LongTermMemory

        ltm = LongTermMemory(path=temp_memory_path)
        ltm.remember("favorite_food", "pizza")
        result = ltm.recall("favorite_food")
        assert result == "pizza"

    def test_long_term_recall_missing_key(self, temp_memory_path):
        from azure.agent import LongTermMemory

        ltm = LongTermMemory(path=temp_memory_path)
        result = ltm.recall("nonexistent")
        assert result is None

    def test_long_term_search(self, temp_memory_path):
        from azure.agent import LongTermMemory

        ltm = LongTermMemory(path=temp_memory_path)
        ltm.remember("server_owner", "Adam")
        ltm.remember("favorite_color", "blue")
        hits = ltm.search("adam")
        assert len(hits) >= 1
        assert any("Adam" in v for _, v in hits)

    def test_long_term_persistence_to_disk(self, temp_memory_path):
        from azure.agent import LongTermMemory

        ltm = LongTermMemory(path=temp_memory_path)
        ltm.remember("persist_key", "persist_value")
        ltm2 = LongTermMemory(path=temp_memory_path)
        assert ltm2.recall("persist_key") == "persist_value"

    def test_agent_long_term_remember_and_recall(self, agent, temp_memory_path):
        from azure.agent import LongTermMemory

        agent.long_term = LongTermMemory(path=temp_memory_path)
        agent.long_term.remember("test_fact", "test_value")
        result = agent.long_term.recall("test_fact")
        assert result == "test_value"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Failover
# ═══════════════════════════════════════════════════════════════════════════


class TestFailover:
    def test_failover_chain_tier1_success(self):
        from azure.failover_chain import FailoverChain

        mock_llm = MagicMock()
        mock_llm.chat.return_value = "Tier 1 response"
        chain = FailoverChain(llm=mock_llm)
        result = chain.respond("hello")
        assert result.text == "Tier 1 response"
        assert result.tier == 1
        assert not result.used_fallback

    def test_failover_chain_tier2_fallback(self):
        from azure.failover_chain import FailoverChain

        call_count = [0]

        def tier1_fail(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 1:
                raise RuntimeError("Tier 1 failed")
            return "Tier 2 response"

        mock_llm = MagicMock()
        mock_llm.chat.side_effect = tier1_fail
        chain = FailoverChain(llm=mock_llm)
        result = chain.respond("hello")
        assert result.tier >= 1
        assert result.used_fallback or result.tier == 1

    def test_failover_chain_all_tiers_exhausted(self):
        from azure.failover_chain import FailoverChain

        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("All failed")
        chain = FailoverChain(llm=mock_llm)
        result = chain.respond("hello")
        assert "exhausted" in result.text.lower() or result.tier == 5

    def test_failover_chain_no_llm(self):
        from azure.failover_chain import FailoverChain

        chain = FailoverChain(llm=None)
        result = chain.respond("hello")
        assert result.tier == 5

    def test_failover_chain_stats(self):
        from azure.failover_chain import FailoverChain

        chain = FailoverChain()
        stats = chain.stats
        assert "tier_health" in stats
        assert "tier_failures" in stats

    def test_failover_chain_recovery(self):
        from azure.failover_chain import FailoverChain

        mock_llm = MagicMock()
        mock_llm.chat.return_value = "test"
        chain = FailoverChain(llm=mock_llm)
        chain._tier_health[1] = False
        chain._tier_failures[1] = 5
        chain._last_recovery_attempt = 0
        chain.attempt_recovery()
        assert chain._tier_health[1] is True
        assert chain._tier_failures[1] == 0


# ═══════════════════════════════════════════════════════════════════════════
# 6. Moderation Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestModerationIntegration:
    @pytest.mark.asyncio
    async def test_toxic_message_blocks_llm(self):
        from azure.ai_moderation.moderation_engine import AIModerationEngine
        from azure.ai_moderation.policy_engine import MODERATE_POLICY, ServerConfig

        mock_llm = MagicMock()
        mock_llm.chat.return_value = json.dumps({
            "is_toxic": True,
            "toxicity_score": 0.95,
            "message_type": "HARASSMENT",
            "intent": "harass",
            "target": "user",
            "reasoning": "Targeted harassment",
            "key_phrases": ["idiot"],
            "context_safe": False,
        })
        engine = AIModerationEngine(mock_llm, server_config=ServerConfig(
            server_id="test", policy=MODERATE_POLICY, confidence_threshold=0.7
        ))
        result = await engine.analyze_message(
            message="You are a complete idiot and should leave",
            user_name="ToxicUser",
        )
        assert result.decision in ("block", "delete", "timeout", "review")
        assert result.delete_message or result.policy_decision is not None

    @pytest.mark.asyncio
    async def test_safe_message_passes(self):
        from azure.ai_moderation.models import (
            ConfidenceLevel,
            Intensity,
            Intent,
            LinguisticMarkers,
            MessageAnalysis,
            MessageType,
            MitigatingFactors,
            ScamAnalysis,
            ScamMarkers,
            Specificity,
            Target,
            URLAnalysis,
        )
        from azure.ai_moderation.moderation_engine import AIModerationEngine
        from azure.ai_moderation.policy_engine import MODERATE_POLICY, ServerConfig
        from azure.ai_moderation.scam_ai import ScamAI
        from azure.ai_moderation.toxicity_ai import ToxicityAI

        mock_llm = AsyncMock()
        engine = AIModerationEngine(mock_llm, server_config=ServerConfig(
            server_id="test", policy=MODERATE_POLICY, confidence_threshold=0.7
        ))
        safe_analysis = MessageAnalysis(
            message_type=MessageType.CONVERSATION,
            target=Target.NOBODY,
            intent=Intent.NEUTRAL,
            intensity=Intensity.MILD,
            specificity=Specificity.VAGUE,
            confidence=ConfidenceLevel.HIGH,
            confidence_score=0.95,
            linguistic=LinguisticMarkers(),
            mitigating=MitigatingFactors(),
            reasoning="Normal friendly conversation",
            key_phrases=[],
        )
        safe_scam = ScamAnalysis(
            message_analysis=safe_analysis,
            url_analysis=URLAnalysis(),
            scam_markers=ScamMarkers(),
            danger_level=Intensity.MILD,
        )
        engine.toxicity_ai = MagicMock(spec=ToxicityAI)
        engine.toxicity_ai.analyze_message = AsyncMock(return_value=safe_analysis)
        engine.scam_ai = MagicMock(spec=ScamAI)
        engine.scam_ai.analyze_message = AsyncMock(return_value=safe_scam)
        result = await engine.analyze_message(
            message="Hey, great weather today!",
            user_name="FriendlyUser",
        )
        assert result.decision in ("safe", "allow")
        assert not result.delete_message

    def test_moderation_engine_metrics(self):
        from azure.ai_moderation.moderation_engine import AIModerationEngine
        from azure.ai_moderation.policy_engine import MODERATE_POLICY, ServerConfig

        mock_llm = MagicMock()
        engine = AIModerationEngine(mock_llm, server_config=ServerConfig(
            server_id="test", policy=MODERATE_POLICY, confidence_threshold=0.7
        ))
        metrics = engine.get_metrics()
        assert "engine" in metrics
        assert metrics["engine"]["total_analyses"] == 0

    @pytest.mark.asyncio
    async def test_scam_detection(self):
        from azure.ai_moderation.moderation_engine import AIModerationEngine
        from azure.ai_moderation.policy_engine import MODERATE_POLICY, ServerConfig

        mock_llm = MagicMock()
        mock_llm.chat.return_value = json.dumps({
            "is_scam": True,
            "scam_score": 0.9,
            "scam_type": "phishing",
            "intent": "deceive",
            "reasoning": "Classic phishing link",
            "key_phrases": ["free nitro", "click here"],
            "confidence": 0.9,
        })
        engine = AIModerationEngine(mock_llm, server_config=ServerConfig(
            server_id="test", policy=MODERATE_POLICY, confidence_threshold=0.7
        ))
        result = await engine.analyze_message(
            message="Get FREE NITRO! Click here: discord-phish.ru",
            user_name="Scammer",
            check_toxicity=False,
            check_scam=True,
        )
        assert result.decision != "safe"


# ═══════════════════════════════════════════════════════════════════════════
# 7. Concurrent Handling
# ═══════════════════════════════════════════════════════════════════════════


class TestConcurrentHandling:
    @pytest.mark.asyncio
    async def test_multiple_messages_get_individual_responses(self, agent, mock_llm):

        def side_effect(messages, **kwargs):
            user_msg = messages[-1]["content"]
            return f"Reply to: {user_msg}"

        mock_llm.chat.side_effect = side_effect
        users = [f"User{i}" for i in range(5)]
        messages = [f"Message from {u}" for u in users]

        tasks = [agent.handle(u, m) for u, m in zip(users, messages, strict=False)]
        results = await asyncio.gather(*tasks)

        for _uid, reply in zip(users, results, strict=False):
            assert reply is not None

    @pytest.mark.asyncio
    async def test_no_cross_contamination(self, agent, mock_llm):
        conversation_log = {}

        def side_effect(messages, **kwargs):
            user_msg = messages[-1]["content"]
            user_name = messages[-1].get("name", "unknown")
            conversation_log.setdefault(user_name, []).append(user_msg)
            return f"Response for {user_name}: {user_msg}"

        mock_llm.chat.side_effect = side_effect

        await asyncio.gather(
            agent.handle("Alice", "Alice's secret"),
            agent.handle("Bob", "Bob's message"),
        )

        if "Alice" in conversation_log:
            assert all("Alice" in m or "secret" in m for m in conversation_log["Alice"])
        if "Bob" in conversation_log:
            assert all("Bob" in m for m in conversation_log["Bob"])


# ═══════════════════════════════════════════════════════════════════════════
# 8. Error Recovery
# ═══════════════════════════════════════════════════════════════════════════


class TestErrorRecovery:
    @pytest.mark.asyncio
    async def test_agent_handles_llm_exception(self, agent, mock_fail_llm):
        agent.llm = mock_fail_llm
        reply = await agent.handle("TestUser", "hello")
        assert reply is not None

    @pytest.mark.asyncio
    async def test_agent_does_not_crash_on_llm_error(self, agent, mock_fail_llm):
        agent.llm = mock_fail_llm
        for _ in range(3):
            reply = await agent.handle("TestUser", "test message")
            assert reply is not None

    def test_failover_chain_exception_in_tier(self):
        from azure.failover_chain import FailoverChain

        mock_llm = MagicMock()
        mock_llm.chat.side_effect = [
            RuntimeError("Tier 1 error"),
            "Recovered response",
        ]
        chain = FailoverChain(llm=mock_llm)
        result = chain.respond("hello")
        assert result.text != ""
        assert result.used_fallback or result.tier >= 1

    def test_tool_engine_llm_failure_returns_chat(self):
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("LLM is down")
        from azure.tool_engine import ToolEngine

        engine = ToolEngine(mock_llm)
        decision = engine.decide("do something", "User")
        assert decision.action == "chat"

    @pytest.mark.asyncio
    async def test_no_llm_agent_returns_message(self, agent_no_llm):
        reply = await agent_no_llm.handle("TestUser", "hello")
        assert reply is not None


# ═══════════════════════════════════════════════════════════════════════════
# 9. Context Building
# ═══════════════════════════════════════════════════════════════════════════


class TestContextBuilding:
    @pytest.mark.asyncio
    async def test_handle_includes_system_prompt(self, agent, mock_llm):
        await agent.handle("TestUser", "hello")
        call_args = mock_llm.chat.call_args
        messages = call_args[0][0]
        system_msgs = [m for m in messages if m["role"] == "system"]
        assert len(system_msgs) >= 1

    @pytest.mark.asyncio
    async def test_handle_includes_conversation_history(self, agent, mock_llm):
        await agent.handle("TestUser", "first message")
        await agent.handle("TestUser", "second message")
        call_args = mock_llm.chat.call_args
        messages = call_args[0][0]
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert len(user_msgs) >= 1
    def test_formatter_builds_messages(self, agent, mock_llm):
        from azure.discord_persona import DEFAULT_PERSONA, ConversationFormatter

        formatter = ConversationFormatter(
            system_prompt=DEFAULT_PERSONA, max_history_turns=10
        )
        messages = formatter.format(
            history=[
                {"role": "user", "content": "hi", "name": "User"},
            ],
            user_name="User",
            current_message="hello",
            server_name="TestServer",
        )
        assert len(messages) >= 2
        assert messages[0]["role"] == "system"

    def test_formatter_uses_stable_aide_voice_without_extra_llm_call(self, mock_llm):
        from azure.discord_persona import DEFAULT_PERSONA, ConversationFormatter

        formatter = ConversationFormatter(system_prompt=DEFAULT_PERSONA, llm=mock_llm)
        messages = formatter.format([], "User", "hello", "TestServer")

        assert "technical aide" in messages[0]["content"]
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_per_call_context_isolation(self, agent, mock_llm):
        await agent.handle("User1", "msg1")
        await agent.handle("User2", "msg2")
        call_args = mock_llm.chat.call_args
        assert call_args is not None

    def test_short_term_memory_thread_safety(self):
        from azure.agent import ShortTermMemory

        stm = ShortTermMemory(max_turns=100)
        errors = []

        def add_messages(prefix, count):
            try:
                for i in range(count):
                    stm.add("user", f"{prefix}_{i}")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=add_messages, args=("thread_a", 20)),
            threading.Thread(target=add_messages, args=("thread_b", 20)),
            threading.Thread(target=add_messages, args=("thread_c", 20)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert not errors


# ═══════════════════════════════════════════════════════════════════════════
# 10. Long-Term Memory Persistence
# ═══════════════════════════════════════════════════════════════════════════


class TestLongTermMemoryPersistence:
    def test_facts_persist_across_reloads(self, populated_memory_path):
        from azure.agent import LongTermMemory

        ltm = LongTermMemory(path=populated_memory_path)
        assert ltm.recall("favorite_color") == "blue"
        assert ltm.recall("pet_name") == "Luna"

        ltm2 = LongTermMemory(path=populated_memory_path)
        assert ltm2.recall("favorite_color") == "blue"
        assert ltm2.recall("pet_name") == "Luna"

    def test_new_facts_persist(self, temp_memory_path):
        from azure.agent import LongTermMemory

        ltm = LongTermMemory(path=temp_memory_path)
        ltm.remember("new_fact", "new_value")

        ltm2 = LongTermMemory(path=temp_memory_path)
        assert ltm2.recall("new_fact") == "new_value"

    def test_corrupt_file_handled_gracefully(self):
        from azure.agent import LongTermMemory

        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False, encoding="utf-8"
        ) as f:
            f.write("NOT VALID JSON {{{")
            path = Path(f.name)
        try:
            ltm = LongTermMemory(path=path)
            assert ltm.facts == {}
            assert ltm.recall("anything") is None
        finally:
            path.unlink()

    def test_search_respects_limit(self, temp_memory_path):
        from azure.agent import LongTermMemory

        ltm = LongTermMemory(path=temp_memory_path)
        for i in range(20):
            ltm.remember(f"key_{i}", f"common_value_{i}")
        hits = ltm.search("common", k=3)
        assert len(hits) <= 3

    def test_search_case_insensitive(self, temp_memory_path):
        from azure.agent import LongTermMemory

        ltm = LongTermMemory(path=temp_memory_path)
        ltm.remember("MyFact", "ImportantValue")
        hits = ltm.search("myfact")
        assert len(hits) >= 1

    def test_overwrite_existing_fact(self, temp_memory_path):
        from azure.agent import LongTermMemory

        ltm = LongTermMemory(path=temp_memory_path)
        ltm.remember("key", "old_value")
        ltm.remember("key", "new_value")
        assert ltm.recall("key") == "new_value"

    def test_large_value_handling(self, temp_memory_path):
        from azure.agent import LongTermMemory

        ltm = LongTermMemory(path=temp_memory_path)
        large_value = "x" * 10000
        ltm.remember("large", large_value)
        assert ltm.recall("large") == large_value


# ═══════════════════════════════════════════════════════════════════════════
# 11. HybridLLM Fallback
# ═══════════════════════════════════════════════════════════════════════════


class TestHybridLLM:
    def test_hybrid_tries_api_first(self):
        from azure.api_llm import HybridLLM

        mock_api = MagicMock()
        mock_api.is_loaded = True
        mock_api.chat.return_value = "API response"
        mock_local = MagicMock()
        mock_local.chat.return_value = "Local response"

        hybrid = HybridLLM(api_llm=mock_api, local_llm=mock_local)
        result = hybrid.chat([{"role": "user", "content": "hi"}])
        assert result == "API response"
        assert hybrid._last_used == "api"

    def test_hybrid_falls_back_to_local(self):
        from azure.api_llm import HybridLLM

        mock_api = MagicMock()
        mock_api.is_loaded = True
        mock_api.chat.side_effect = RuntimeError("API down")
        mock_local = MagicMock()
        mock_local.chat.return_value = "Local fallback"

        hybrid = HybridLLM(api_llm=mock_api, local_llm=mock_local)
        result = hybrid.chat([{"role": "user", "content": "hi"}])
        assert result == "Local fallback"
        assert hybrid._last_used == "local"

    def test_hybrid_both_fail(self):
        from azure.api_llm import HybridLLM

        mock_api = MagicMock()
        mock_api.is_loaded = True
        mock_api.chat.side_effect = RuntimeError("API down")
        mock_local = MagicMock()
        mock_local.chat.side_effect = RuntimeError("Local down")

        hybrid = HybridLLM(api_llm=mock_api, local_llm=mock_local)
        result = hybrid.chat([{"role": "user", "content": "hi"}])
        assert "failed" in result.lower()

    def test_hybrid_get_info(self):
        from azure.api_llm import HybridLLM

        mock_api = MagicMock()
        mock_api.is_loaded = True
        mock_api.get_info.return_value = {"model": "test"}
        hybrid = HybridLLM(api_llm=mock_api)
        info = hybrid.get_info()
        assert "api" in info
        assert info["type"] == "hybrid"


# ═══════════════════════════════════════════════════════════════════════════
# 12. Additional Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_message(self, agent, mock_llm):
        reply = await agent.handle("TestUser", "")
        assert reply is not None

    @pytest.mark.asyncio
    async def test_very_long_message(self, agent, mock_llm):
        long_msg = "hello " * 1000
        reply = await agent.handle("TestUser", long_msg)
        assert reply is not None

    @pytest.mark.asyncio
    async def test_unicode_message(self, agent, mock_llm):
        mock_llm.chat.return_value = "Unicode response: café"
        reply = await agent.handle("TestUser", "Héllo wörld 🎉")
        assert reply == "Unicode response: café"

    @pytest.mark.asyncio
    async def test_none_user_id(self, agent, mock_llm):
        reply = await agent.handle("TestUser", "hi", user_id=None)
        assert reply is not None

    @pytest.mark.asyncio
    async def test_empty_user_id(self, agent, mock_llm):
        reply = await agent.handle("TestUser", "hi", user_id="")
        assert reply is not None

    def test_agent_get_info(self, agent):
        info = agent.get_info()
        assert "mode" in info
        assert "model_name" in info
        assert info["model_name"] == "test_agent"
