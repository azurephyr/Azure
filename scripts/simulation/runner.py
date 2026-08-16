"""Scenario runner — executes scenarios and collects results."""

import asyncio
import time
import traceback
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

from bot.context import ctx

from .core import SimEnv
from .scenarios import Scenario


@dataclass
class SimResult:
    scenario_id: str
    scenario_name: str
    subsystem: str
    passed: bool
    response: str | None
    latency: float
    errors: list = field(default_factory=list)
    assertion_results: dict = field(default_factory=dict)


def configure_ctx(env: SimEnv):
    """Configure the global BotContext singleton for simulation."""
    ctx.agent = env.agent
    ctx.bot = env.bot
    ctx.chat_mode = "anyone"
    ctx.allowed_user_ids = set()
    ctx.moderation_service = None
    ctx.admin_channel = env.channels.get("admin")
    ctx.intent_classifier = None
    ctx.plugin_manager = None
    ctx.task_manager = None
    ctx.mgmt_tools = None
    ctx.cognitive_pipeline = None
    ctx.db = MagicMock()
    ctx.db.get_access_control = MagicMock(return_value=None)


def _check_response_contains(response: str | None, terms: list[str]) -> bool:
    if response is None:
        return False
    r_lower = response.lower()
    return any(t.lower() in r_lower for t in terms)


def _check_response_not_contains(response: str | None, terms: list[str]) -> bool:
    if response is None:
        return True
    r_lower = response.lower()
    return not any(t.lower() in r_lower for t in terms)


def _run_assertions(scenario: Scenario, response: str | None,
                    env: SimEnv, lenient: bool = False) -> dict:
    results = {}
    expected = scenario.expected

    if "response_contains" in expected:
        terms = expected["response_contains"]
        ok = _check_response_contains(response, terms)
        if lenient and not ok and response and "error" not in response.lower():
            ok = True  # relaxed — response is non-empty and non-error
        results["response_contains"] = {"passed": ok, "expected": terms, "actual": response}

    if "response_not_contains" in expected:
        terms = expected["response_not_contains"]
        ok = _check_response_not_contains(response, terms)
        results["response_not_contains"] = {"passed": ok, "expected": terms, "actual": response}

    if "no_response" in expected:
        has_none = response is None or response.strip() == ""
        results["no_response"] = {"passed": has_none, "expected": True, "actual": response}

    if "action_called" in expected:
        action_path = expected["action_called"]
        parts = action_path.split(".")
        obj = env
        for part in parts:
            obj = getattr(obj, part, None)
            if obj is None:
                break
        was_called = obj is not None and getattr(obj, "called", False) if obj else False
        results["action_called"] = {"passed": was_called, "expected": action_path, "actual": str(obj)}

    return results


async def _run_direct(env: SimEnv, scenario: Scenario) -> SimResult:
    """Execute scenario via direct agent.handle() calls."""
    t0 = time.perf_counter()
    errors = []
    final_response = None

    try:
        for msg in scenario.messages:
            user_name = msg["user"]
            text = msg["text"]
            user_id = env.members[user_name].id

            reply = await env.agent.handle(
                user=user_name,
                message=text,
                server_name=env.guild.name,
                user_id=str(user_id),
                guild=env.guild,
                channel=env.channels.get(msg.get("channel", "general")),
                event_loop=asyncio.get_running_loop(),
            )
            final_response = reply
    except Exception as e:
        errors.append(f"{type(e).__name__}: {e}")
        errors.append(traceback.format_exc()[:500])

    latency = time.perf_counter() - t0
    assertion_results = _run_assertions(scenario, final_response, env, lenient=scenario.lenient)
    all_assertions_ok = all(
        r["passed"] for r in assertion_results.values()
    ) if assertion_results else True
    passed = all_assertions_ok and not errors

    return SimResult(
        scenario_id=scenario.id, scenario_name=scenario.name,
        subsystem=scenario.subsystem, passed=passed,
        response=final_response, latency=latency,
        errors=errors, assertion_results=assertion_results,
    )


async def _run_pipeline(env: SimEnv, scenario: Scenario) -> SimResult:
    """Execute scenario through the full on_message() pipeline."""
    t0 = time.perf_counter()
    errors = []
    final_response = None

    _patches = [
        patch("bot.handlers.message_handler._check_rate_limit",
              new_callable=AsyncMock, return_value=(True, 0.0)),
        patch("bot.handlers.message_handler._check_guild_rate_limit",
              return_value=True),
        patch("bot.handlers.message_handler._check_command_cooldown",
              new_callable=AsyncMock, return_value=(True, 0.0)),
        patch("azure.input_validator.validate_input"),
        patch("bot.handlers.message_handler._attention_check",
              new_callable=AsyncMock, return_value=True),
        patch("bot.handlers.message_handler._get_cached_response",
              new_callable=AsyncMock, return_value=None),
    ]

    mocked = []
    for p in _patches:
        mocked.append(p.start())
    # validate_input mock — allow everything
    mocked[3].return_value = MagicMock(
        is_blocked=False, sanitized_input="", violations=[]
    )

    try:
        from bot.handlers.message_handler import on_message

        for msg_data in scenario.messages:
            user_name = msg_data["user"]
            text = msg_data["text"]
            channel_name = msg_data.get("channel", "general")
            mentions_bot = msg_data.get("mentions_bot", False)

            msg = env.create_pipeline_message(
                content=text,
                author_name=user_name,
                channel_name=channel_name,
                mentions_bot=mentions_bot,
            )
            # Update validate_input's sanitized_input with actual text
            mocked[3].return_value = MagicMock(
                is_blocked=False, sanitized_input=text, violations=[]
            )

            await on_message(msg)
            await asyncio.sleep(0.05)

    except Exception as e:
        errors.append(f"{type(e).__name__}: {e}")
        if "discord" not in str(e).lower():
            errors.append(traceback.format_exc()[:500])
    finally:
        for p in _patches:
            p.stop()

    latency = time.perf_counter() - t0

    # Try to get response from last sent message
    for sent in reversed(env.sent_messages):
        if sent.content and "Thinking" not in sent.content:
            final_response = sent.content
            break
    if final_response is None and env.sent_messages:
        final_response = env.sent_messages[-1].content
    assertion_results = _run_assertions(scenario, final_response, env, lenient=scenario.lenient)

    all_assertions_ok = all(
        r["passed"] for r in assertion_results.values()
    ) if assertion_results else True
    passed = all_assertions_ok and not errors

    return SimResult(
        scenario_id=scenario.id, scenario_name=scenario.name,
        subsystem=scenario.subsystem, passed=passed,
        response=final_response, latency=latency,
        errors=errors, assertion_results=assertion_results,
    )


async def run_scenario(env: SimEnv, scenario: Scenario) -> SimResult:
    """Execute a single scenario (auto-selects direct or pipeline mode)."""
    env.reset()

    if scenario.responses:
        env.agent.responses = dict(scenario.responses)
    else:
        env.agent.responses = {}

    if scenario.setup:
        try:
            scenario.setup(env)
        except Exception as e:
            return SimResult(
                scenario_id=scenario.id, scenario_name=scenario.name,
                subsystem=scenario.subsystem, passed=False,
                response=None, latency=0.0,
                errors=[f"Setup failed: {e}"],
            )

    if scenario.mode == "pipeline":
        return await _run_pipeline(env, scenario)
    return await _run_direct(env, scenario)


async def run_batch(env: SimEnv, scenarios: list[Scenario]) -> list[SimResult]:
    """Execute a batch of scenarios sequentially (env is shared)."""
    results = []
    for s in scenarios:
        result = await run_scenario(env, s)
        results.append(result)
    return results
