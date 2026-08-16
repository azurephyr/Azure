from __future__ import annotations

from web.api_moderation import _allowed_web_guild_ids


def test_web_guild_allowlist_denies_by_default(monkeypatch):
    monkeypatch.delenv("AZURE_WEB_ALLOWED_GUILD_IDS", raising=False)
    assert _allowed_web_guild_ids() == set()


def test_web_guild_allowlist_accepts_only_discord_ids(monkeypatch):
    monkeypatch.setenv("AZURE_WEB_ALLOWED_GUILD_IDS", " 123,invalid,456, ")
    assert _allowed_web_guild_ids() == {"123", "456"}
