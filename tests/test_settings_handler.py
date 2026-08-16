"""Tests for settings_handler — tests handler logic directly without CommandTree."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from azure.model_selector import ModelSelector
from bot.handlers.settings_handler import (
    ProviderSelectView,
    ResetConfirmView,
    _admin_check,
    _build_health_embed,
    _ModelAutocomplete,
    _sync_env_from_selector,
    _test_result_embed,
)


@pytest.fixture
def selector(tmp_path):
    import azure.model_selector as ms
    original_dir = ms.CONFIG_DIR
    ms.CONFIG_DIR = tmp_path
    ms.HEALTH_FILE = tmp_path / "model_health.json"
    env_clean = {
        "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "",
        "GOOGLE_API_KEY": "", "GEMINI_API_KEY": "", "AZURE_GOOGLE_API_KEY": "",
        "GROQ_API_KEY": "", "AZURE_GROQ_API_KEY": "",
        "MISTRAL_API_KEY": "", "AZURE_MISTRAL_API_KEY": "",
        "OPENROUTER_API_KEY": "", "AZURE_OPENROUTER_API_KEY": "",
    }
    with patch.dict("os.environ", env_clean, clear=False):
        sel = ModelSelector()
    yield sel
    ms.CONFIG_DIR = original_dir
    ms.HEALTH_FILE = original_dir / "model_health.json"


@pytest.fixture
def admin():
    i = AsyncMock(spec=discord.Interaction)
    i.user = MagicMock(spec=discord.Member)
    i.user.guild_permissions = MagicMock(spec=discord.Permissions)
    i.user.guild_permissions.administrator = True
    i.response = AsyncMock()
    i.followup = AsyncMock()
    i.namespace = MagicMock()
    return i


@pytest.fixture
def non_admin():
    i = AsyncMock(spec=discord.Interaction)
    i.user = MagicMock(spec=discord.Member)
    i.user.guild_permissions = MagicMock(spec=discord.Permissions)
    i.user.guild_permissions.administrator = False
    i.response = AsyncMock()
    return i


class TestAdminCheck:
    def test_admin(self, admin):
        assert _admin_check(admin) is True

    def test_non_admin(self, non_admin):
        assert _admin_check(non_admin) is False


class TestSyncEnv:
    def test_sets_provider(self, selector):
        selector.update_settings(provider="groq", model="llama-3.3-70b-versatile")
        with patch.dict("os.environ", {}, clear=False):
            _sync_env_from_selector(selector)
            assert os.environ["AZURE_LLM_PROVIDER"] == "groq"

    def test_does_not_set_model_env(self, selector):
        selector.update_settings(provider="openrouter", model="nvidia/nemotron-3-ultra-550b-a55b:free")
        with patch.dict("os.environ", {}, clear=False):
            _sync_env_from_selector(selector)
            assert "AZURE_OPENROUTER_MODEL" not in os.environ

    def test_sets_nothing_for_unknown_provider(self, selector):
        selector.update_settings(provider="unknown", model="m")
        with patch.dict("os.environ", {}, clear=False):
            _sync_env_from_selector(selector)
            assert os.environ["AZURE_LLM_PROVIDER"] == "unknown"


class TestHealthEmbed:
    def test_basic(self, selector):
        embed = _build_health_embed(selector)
        assert embed.title == "\u2699\ufe0f LLM Settings"
        assert len(embed.fields) >= 2

    def test_shows_all_providers(self, selector):
        embed = _build_health_embed(selector)
        provider_field = [f for f in embed.fields if f.name == "Provider Health"][0]
        from azure.model_selector import ALL_PROVIDERS
        for p in ALL_PROVIDERS:
            display = selector.get_provider_display_name(p)
            assert display in provider_field.value


class TestTestResultEmbed:
    def test_success(self):
        embed = _test_result_embed({"success": True, "provider": "openai", "model": "gpt-4o", "latency": 0.5})
        assert "\u2705" in embed.title
        assert "openai" in embed.description

    def test_failure(self):
        embed = _test_result_embed({"success": False, "provider": "openai", "model": "gpt-4o", "error": "timeout", "latency": 5.0})
        assert "\u274c" in embed.title
        assert "timeout" in embed.description


class TestProviderSelectView:
    @pytest.mark.asyncio
    async def test_creates(self, selector):
        v = ProviderSelectView(selector)
        assert len(v.children) == 2

    @pytest.mark.asyncio
    async def test_smart_toggle(self, selector, admin):
        v = ProviderSelectView(selector)
        btn = v.children[1]
        await btn.callback(admin)
        admin.response.send_message.assert_called_once()
        assert selector.get_settings()["smart_mode"] is False

    @pytest.mark.asyncio
    async def test_select_non_admin(self, selector, non_admin):
        v = ProviderSelectView(selector)
        btn = v.children[0]
        await btn.callback(non_admin)
        non_admin.response.send_message.assert_called_once_with("Admin only.", ephemeral=True)


class TestResetConfirmView:
    @pytest.mark.asyncio
    async def test_creates(self, selector):
        v = ResetConfirmView(selector)
        assert len(v.children) == 2

    @pytest.mark.asyncio
    async def test_confirm(self, selector, admin):
        v = ResetConfirmView(selector)
        btn = v.children[0]
        await btn.callback(admin)
        admin.response.edit_message.assert_called_once()
        assert selector.get_settings()["smart_mode"] is True
        assert selector.get_settings()["provider"] == "openrouter"

    @pytest.mark.asyncio
    async def test_cancel(self, selector, admin):
        v = ResetConfirmView(selector)
        btn = v.children[1]
        await btn.callback(admin)
        admin.response.edit_message.assert_called_once()


class TestAutocomplete:
    @pytest.mark.asyncio
    async def test_returns_choices_for_openrouter(self, selector):
        ac = _ModelAutocomplete(selector)
        i = AsyncMock(spec=discord.Interaction)
        i.namespace = MagicMock()
        i.namespace.provider = "openrouter"
        i.namespace.name = None
        with patch.object(selector, "fetch_openrouter_models", return_value=[
            {"id": "nvidia/nemotron:free", "is_free": True, "name": "Nemotron"},
            {"id": "openai/gpt-4o", "is_free": False, "name": "GPT-4o"},
        ]):
            choices = await ac.get_choices(i, "")
        assert len(choices) == 2
        assert choices[0].value == "nvidia/nemotron:free"

    @pytest.mark.asyncio
    async def test_filters_by_query(self, selector):
        ac = _ModelAutocomplete(selector)
        i = AsyncMock(spec=discord.Interaction)
        i.namespace = MagicMock()
        i.namespace.provider = "openrouter"
        i.namespace.name = None
        with patch.object(selector, "fetch_openrouter_models", return_value=[
            {"id": "nvidia/nemotron:free", "is_free": True},
            {"id": "openai/gpt-4o", "is_free": False},
        ]):
            choices = await ac.get_choices(i, "gpt")
        assert len(choices) == 1
        assert "gpt" in choices[0].value

    @pytest.mark.asyncio
    async def test_limit_25(self, selector):
        ac = _ModelAutocomplete(selector)
        i = AsyncMock(spec=discord.Interaction)
        i.namespace = MagicMock()
        i.namespace.provider = "openrouter"
        i.namespace.name = None
        models = [{"id": f"m-{x}", "is_free": False} for x in range(30)]
        with patch.object(selector, "fetch_openrouter_models", return_value=models):
            choices = await ac.get_choices(i, "")
        assert len(choices) == 25

    @pytest.mark.asyncio
    async def test_selects_provider_from_namespace(self, selector):
        ac = _ModelAutocomplete(selector)
        i = AsyncMock(spec=discord.Interaction)
        i.namespace = MagicMock()
        i.namespace.provider = "google"
        i.namespace.name = None
        choices = await ac.get_choices(i, "")
        assert len(choices) > 0

    @pytest.mark.asyncio
    async def test_fallback_to_active_provider(self, selector):
        ac = _ModelAutocomplete(selector)
        i = AsyncMock(spec=discord.Interaction)
        i.namespace = MagicMock()
        i.namespace.provider = None
        i.namespace.name = None
        selector.update_settings(smart_mode=False, provider="groq", model="llama-3.3-70b")
        choices = await ac.get_choices(i, "")
        assert len(choices) > 0
        from azure.model_catalog import PROVIDER_CATALOGS
        expected_ids = [m.id for m in PROVIDER_CATALOGS["groq"]["models"]]
        for c in choices:
            assert c.value in expected_ids


class TestSelectorIntegration:
    def test_update_and_get(self, selector):
        selector.update_settings(provider="groq", model="llama-3.3-70b-versatile", smart_mode=False)
        s = selector.get_settings()
        assert s["provider"] == "groq"
        assert s["model"] == "llama-3.3-70b-versatile"
        assert s["smart_mode"] is False

    def test_health_tracking(self, selector):
        selector.record_success("groq", "llama-3.3-70b")
        selector.record_failure("groq", "llama-3.3-70b", "error")
        h = selector.get_provider_health("groq")
        assert h["groq"]["success_count"] == 1
        assert h["groq"]["failure_count"] == 1

    def test_smart_select_picks_best(self, selector):
        selector.update_settings(smart_mode=True, provider="groq")
        selector._providers["groq"].health.has_api_key = True
        for _ in range(10):
            selector.record_success("groq", "llama-3.3-70b")
        provider = selector.select_provider()
        assert provider == "groq"

    def test_test_provider_no_key(self, selector):
        with patch.dict("os.environ", {
            "OPENAI_API_KEY": "", "AZURE_OPENAI_API_KEY": "",
        }, clear=False):
            selector._detect_api_keys()
            result = selector.test_provider("openai", "gpt-4o")
        assert result["success"] is False
        assert "No API key" in result["error"]

    def test_get_active_config(self, selector):
        selector.update_settings(smart_mode=False, provider="openrouter", model="nvidia/nemotron-3-ultra-550b-a55b:free")
        config = selector.get_active_config()
        assert config["provider"] == "openrouter"
        assert config["model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"
        assert config["smart_mode"] is False
