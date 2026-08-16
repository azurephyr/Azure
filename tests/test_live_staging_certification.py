from __future__ import annotations

import pytest

from scripts.live_staging_certification import PREFIX, certification_plan, validate_live_gate


def test_certification_plan_is_safe_and_complete():
    plan = certification_plan()
    assert len(plan) >= 8
    assert any("role" in step for step in plan)
    assert any("channel" in step for step in plan)
    assert any("delete" in step for step in plan)
    assert not any(action in " ".join(plan) for action in ("ban member", "kick member", "timeout member"))
    assert PREFIX == "azure-cert-"


def test_live_gate_allows_plan_without_environment(monkeypatch):
    monkeypatch.delenv("AZURE_LIVE_TEST_GUILD_ID", raising=False)
    validate_live_gate(123, execute=False)


def test_live_gate_requires_exact_guild_match(monkeypatch):
    monkeypatch.setenv("AZURE_LIVE_TEST_GUILD_ID", "456")
    with pytest.raises(RuntimeError, match="exactly match"):
        validate_live_gate(123, execute=True)
    validate_live_gate(456, execute=True)
