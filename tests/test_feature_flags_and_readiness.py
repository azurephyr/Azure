"""Feature flags + readiness contract tests."""

from __future__ import annotations

import os

from bot.context import BotContext
from bot.features import FeatureFlags, load_feature_flags


def test_load_feature_flags_defaults_are_safe(monkeypatch):
    for key in list(os.environ):
        if key.startswith("AZURE_FEATURE_") or key == "AZURE_COGNITIVE_MODE":
            monkeypatch.delenv(key, raising=False)
    flags = load_feature_flags()
    assert flags.web is True
    assert flags.health is True
    assert flags.cognitive is False
    assert flags.live_intel is False
    assert flags.voice is False
    assert flags.plugins is False


def test_cognitive_legacy_env(monkeypatch):
    monkeypatch.setenv("AZURE_COGNITIVE_MODE", "1")
    monkeypatch.delenv("AZURE_FEATURE_COGNITIVE", raising=False)
    flags = load_feature_flags()
    assert flags.cognitive is True
    assert flags.autonomous is True


def test_feature_flag_explicit_on(monkeypatch):
    monkeypatch.setenv("AZURE_FEATURE_LIVE_INTEL", "true")
    monkeypatch.setenv("AZURE_FEATURE_WEB", "0")
    flags = load_feature_flags()
    assert flags.live_intel is True
    assert flags.web is False


def test_ctx_core_ready_requires_agent_llm():
    c = BotContext()
    assert c.core_ready() is False
    c.bot = object()
    assert c.core_ready() is False

    class _Agent:
        llm = object()

    c.agent = _Agent()
    assert c.core_ready() is True
    assert c.runtime_ready() is False
    c.discord_connected = True
    c.mark_ready()
    assert c.ready is True
    assert c.runtime_ready() is True


def test_readiness_summary_shape():
    c = BotContext()
    c.set_feature_flags(FeatureFlags(web=True, health=True))
    summary = c.readiness_summary()
    assert "ready" in summary
    assert "core_ready" in summary
    assert "features" in summary
    assert "subsystems" in summary
    assert isinstance(summary["subsystems"], list)


def test_subsystem_report_lists_agent():
    c = BotContext()
    names = [s.name for s in c.subsystem_report()]
    assert "agent" in names
    assert "moderation_service" in names
