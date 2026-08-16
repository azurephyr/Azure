"""
Azure Intent Classifier — LLM-first structured routing.

Primary path: one structured LLM call that returns a closed-schema route.
Fallback: minimal structural defaults (chat/ignore) when no LLM is available.
No keyword banks for greetings, tools, or management actions.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("azure.intent_classifier")

# Closed route set used by the message pipeline
VALID_ROUTES = frozenset({
    "ignore",
    "chat",
    "plan",
    "tool",
    "info",
    "memory",
    "moderation",
    "health_check",
})

ROUTER_SYSTEM = """You are Azure's intent router for a Discord server AI.
Return ONLY valid JSON. No markdown. No prose.

Schema:
{
  "route": "ignore|chat|plan|tool|info|memory|moderation|health_check",
  "action": "short snake_case label of the goal",
  "confidence": 0.0-1.0,
  "params": {}
}

Route meanings:
- ignore: not directed at the bot; general chat between humans
- chat: normal conversation / Q&A / greeting directed at the bot
- plan: multi-step server build/restructure/setup the bot should execute
- tool: single Discord management action (channel/role/member/settings)
- info: ask about bot capabilities, status, how things work
- memory: remember/recall facts about users or the server
- moderation: moderation policy questions or review (not executing bans unless explicit tool)
- health_check: analyze/audit server health

Rules:
- Prefer chat when unsure.
- Prefer ignore unless the user is talking to the bot, @mentioned it, DM'd it, or clearly requesting an action from it.
- Never invent tools. Put free-form goals in action + params.
- confidence must reflect certainty.
"""


@dataclass
class UserIntent:
    """Result of intent classification."""

    action: str
    confidence: float
    params: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    is_directed: bool = False
    route: str = "chat"


def _extract_mention(text: str) -> str | None:
    m = re.search(r"<@!?(\d+)>", text)
    return m.group(1) if m else None


def _extract_channel_ref(text: str) -> str | None:
    m = re.search(r"<#(\d+)>", text)
    return m.group(1) if m else None


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    text = raw.strip()
    code = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code:
        text = code.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _normalize_route(value: Any) -> str:
    if not value:
        return "chat"
    r = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "greeting": "chat",
        "talk": "chat",
        "question": "chat",
        "build": "plan",
        "setup": "plan",
        "server_setup": "plan",
        "manage": "tool",
        "member_action": "tool",
        "action": "tool",
        "help": "info",
        "capabilities": "info",
        "status": "info",
        "recall": "memory",
        "remember": "memory",
        "mod": "moderation",
        "analyze": "health_check",
        "audit": "health_check",
        "skip": "ignore",
        "none": "ignore",
    }
    r = aliases.get(r, r)
    return r if r in VALID_ROUTES else "chat"


class IntentClassifier:
    """LLM-first intent router with closed schema routes."""

    def __init__(self, llm: Any | None = None, bot_name: str = "Azure"):
        self.llm = llm
        self.bot_name = bot_name or "Azure"
        self._has_llm = llm is not None
        self._cache: dict[tuple, UserIntent] = {}
        self._cache_max = 256

    def set_llm(self, llm: Any | None) -> None:
        self.llm = llm
        self._has_llm = llm is not None

    def classify(
        self,
        text: str,
        user_name: str = "",
        is_dm: bool = False,
        is_mentioned: bool = False,
        server_name: str = "",
        channel_name: str = "",
        user_roles: list[str] | None = None,
        server_member_count: int = 0,
    ) -> UserIntent:
        raw = (text or "").strip()
        if not raw:
            return UserIntent(action="ignore", confidence=1.0, raw_text=raw, is_directed=False, route="ignore")

        directed = bool(is_mentioned or is_dm)
        if not directed and self.bot_name and re.search(
            r"\b" + re.escape(self.bot_name.lower()) + r"\b", raw.lower()
        ):
            # Structural name check only (word boundary) — not keyword intent
            directed = True

        # A decision can depend on authorization and server context. Never
        # reuse an LLM route learned in another guild, channel, or role set.
        cache_key = (
            raw[:160].lower(),
            user_name,
            server_name,
            channel_name,
            bool(is_dm),
            bool(is_mentioned),
            tuple(user_roles or [])[:12],
            int(server_member_count or 0),
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if self._has_llm and self.llm is not None:
            intent = self._classify_with_llm(
                raw,
                directed=directed,
                is_dm=is_dm,
                is_mentioned=is_mentioned,
                user_name=user_name,
                server_name=server_name,
                channel_name=channel_name,
                user_roles=user_roles or [],
                server_member_count=server_member_count,
            )
        else:
            intent = self._structural_fallback(raw, directed=directed, is_dm=is_dm, is_mentioned=is_mentioned)

        # Enrich params with structural Discord refs when present
        if intent.params is None:
            intent.params = {}
        mention = _extract_mention(raw)
        channel_ref = _extract_channel_ref(raw)
        if mention and "target" not in intent.params:
            intent.params["target"] = mention
        if channel_ref and "channel_id" not in intent.params:
            intent.params["channel_id"] = channel_ref

        if len(self._cache) >= self._cache_max:
            self._cache.pop(next(iter(self._cache)))
        self._cache[cache_key] = intent
        return intent

    def classify_llm(self, *args, **kwargs) -> UserIntent:
        """Alias used by background tasks — same as classify."""
        return self.classify(*args, **kwargs)

    def _structural_fallback(
        self,
        text: str,
        *,
        directed: bool,
        is_dm: bool,
        is_mentioned: bool,
    ) -> UserIntent:
        """No-LLM path: never keyword-maps actions; only directed vs ignore vs chat."""
        if not (directed or is_dm or is_mentioned):
            return UserIntent(
                action="ignore",
                confidence=0.7,
                raw_text=text,
                is_directed=False,
                route="ignore",
            )
        return UserIntent(
            action="chat",
            confidence=0.55,
            raw_text=text,
            is_directed=True,
            route="chat",
        )

    def _classify_with_llm(
        self,
        text: str,
        *,
        directed: bool,
        is_dm: bool,
        is_mentioned: bool,
        user_name: str,
        server_name: str,
        channel_name: str,
        user_roles: list[str],
        server_member_count: int,
    ) -> UserIntent:
        roles_str = ", ".join(user_roles[:12]) if user_roles else "none"
        user_prompt = (
            f"CONTEXT:\n"
            f"  Server: {server_name or 'DM'}\n"
            f"  Channel: #{channel_name or 'unknown'}\n"
            f"  User: {user_name or 'unknown'}\n"
            f"  User roles: {roles_str}\n"
            f"  Members: {server_member_count or 'N/A'}\n"
            f"  Is DM: {is_dm}\n"
            f"  Bot @mentioned: {is_mentioned}\n"
            f"  Name-directed hint: {directed}\n"
            f"  Bot name: {self.bot_name}\n\n"
            f"USER MESSAGE:\n{text[:800]}\n"
        )
        messages = [
            {"role": "system", "content": ROUTER_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]
        try:
            raw = self.llm.chat(messages, max_tokens=180, temperature=0.0)
        except Exception as e:
            logger.error("[intent] LLM call failed: %s", e)
            return self._structural_fallback(text, directed=directed, is_dm=is_dm, is_mentioned=is_mentioned)

        data = _parse_json_object(raw or "")
        if not data:
            logger.warning("[intent] unparseable LLM output; falling back to chat/ignore")
            return self._structural_fallback(text, directed=directed, is_dm=is_dm, is_mentioned=is_mentioned)

        route = _normalize_route(data.get("route") or data.get("action"))
        action = str(data.get("action") or route).strip() or route
        try:
            confidence = float(data.get("confidence", 0.75))
        except (TypeError, ValueError):
            confidence = 0.75
        confidence = max(0.0, min(1.0, confidence))
        params = data.get("params") if isinstance(data.get("params"), dict) else {}

        # If not directed and LLM says chat/tool/plan with low conf, prefer ignore
        is_dir = directed or is_dm or is_mentioned
        if route == "ignore":
            is_dir = False
        elif not is_dir and confidence < 0.8 and route in {"chat", "info"}:
            route = "ignore"
            action = "ignore"
            is_dir = False

        return UserIntent(
            action=action,
            confidence=confidence,
            params=params,
            raw_text=text,
            is_directed=is_dir,
            route=route,
        )
