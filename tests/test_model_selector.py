"""Comprehensive tests for ModelSelector with mocked APIs.

Tests all providers with different payment plans and subscription tiers.
Verifies tier detection, health tracking, smart mode, and fallback logic.
"""

import json
import time
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from azure.model_selector import (
    ALL_PROVIDERS,
    ModelSelector,
    ProviderHealth,
    _parse_google_429_tier,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def selector(tmp_path):
    """Fresh ModelSelector with temp config dir and no API keys."""
    import azure.model_selector as ms
    original_dir = ms.CONFIG_DIR
    ms.CONFIG_DIR = tmp_path
    ms.HEALTH_FILE = tmp_path / "model_health.json"
    env_clean = {
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "GOOGLE_API_KEY": "",
        "GEMINI_API_KEY": "",
        "AZURE_GOOGLE_API_KEY": "",
        "GROQ_API_KEY": "",
        "AZURE_GROQ_API_KEY": "",
        "MISTRAL_API_KEY": "",
        "AZURE_MISTRAL_API_KEY": "",
        "OPENROUTER_API_KEY": "",
        "AZURE_OPENROUTER_API_KEY": "",
    }
    with patch.dict("os.environ", env_clean, clear=False):
        sel = ModelSelector()
    yield sel
    ms.CONFIG_DIR = original_dir
    ms.HEALTH_FILE = original_dir / "model_health.json"


@pytest.fixture
def selector_with_keys(tmp_path):
    """ModelSelector with fake API keys set."""
    import azure.model_selector as ms
    original_dir = ms.CONFIG_DIR
    ms.CONFIG_DIR = tmp_path
    ms.HEALTH_FILE = tmp_path / "model_health.json"
    with patch.dict("os.environ", {
        "OPENAI_API_KEY": "sk-test-openai",
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "GOOGLE_API_KEY": "test-google-key",
        "GROQ_API_KEY": "gsk_test",
        "MISTRAL_API_KEY": "test-mistral",
        "OPENROUTER_API_KEY": "sk-or-test",
        "NARAROUTER_API_KEY": "sk-nry-test",
    }):
        sel = ModelSelector()
        yield sel
    ms.CONFIG_DIR = original_dir
    ms.HEALTH_FILE = original_dir / "model_health.json"


# ---------------------------------------------------------------------------
# Initialization Tests
# ---------------------------------------------------------------------------

class TestInitialization:
    def test_all_providers_initialized(self, selector):
        for name in ALL_PROVIDERS:
            assert name in selector._providers

    def test_default_settings(self, selector):
        settings = selector.get_settings()
        assert settings["smart_mode"] is True
        assert settings["provider"] == "openrouter"
        assert "nemotron" in settings["model"]
        assert settings["fallback_provider"] == "openrouter"

    def test_api_key_detection(self, selector_with_keys):
        for name in ALL_PROVIDERS:
            assert selector_with_keys._providers[name].health.has_api_key is True

    def test_no_keys_detected(self, selector):
        for name in ALL_PROVIDERS:
            assert selector._providers[name].health.has_api_key is False


# ---------------------------------------------------------------------------
# Health Tracking Tests
# ---------------------------------------------------------------------------

class TestHealthTracking:
    def test_record_success(self, selector_with_keys):
        selector_with_keys.record_success("openai", "gpt-4o")
        h = selector_with_keys._providers["openai"].health
        assert h.success_count == 1
        assert h.consecutive_failures == 0
        assert h.last_success_time > 0

    def test_record_failure(self, selector_with_keys):
        selector_with_keys.record_failure("openai", "gpt-4o", "rate limited")
        h = selector_with_keys._providers["openai"].health
        assert h.failure_count == 1
        assert h.consecutive_failures == 1
        assert h.last_error == "rate limited"

    def test_consecutive_failures_accumulate(self, selector_with_keys):
        for i in range(3):
            selector_with_keys.record_failure("google", "gemini-2.0-flash", f"error {i}")
        h = selector_with_keys._providers["google"].health
        assert h.consecutive_failures == 3

    def test_success_resets_consecutive_failures(self, selector_with_keys):
        selector_with_keys.record_failure("google", "gemini-2.0-flash", "err")
        selector_with_keys.record_failure("google", "gemini-2.0-flash", "err")
        selector_with_keys.record_success("google", "gemini-2.0-flash")
        h = selector_with_keys._providers["google"].health
        assert h.consecutive_failures == 0

    def test_is_healthy_no_key(self, selector):
        h = ProviderHealth(has_api_key=False)
        assert h.is_healthy is False

    def test_is_healthy_with_key(self, selector):
        h = ProviderHealth(has_api_key=True)
        assert h.is_healthy is True

    def test_is_healthy_consecutive_failures_blocks(self, selector):
        h = ProviderHealth(
            has_api_key=True,
            consecutive_failures=5,
            last_failure_time=time.time(),
        )
        assert h.is_healthy is False

    def test_is_healthy_consecutive_failures_recovers(self, selector):
        h = ProviderHealth(
            has_api_key=True,
            consecutive_failures=5,
            last_failure_time=time.time() - 400,
        )
        assert h.is_healthy is True

    def test_health_score_no_key(self, selector):
        h = ProviderHealth(has_api_key=False)
        assert h.health_score == 0.0

    def test_health_score_perfect(self, selector):
        h = ProviderHealth(has_api_key=True, success_count=10, failure_count=0)
        assert h.health_score > 0.9

    def test_health_score_poor(self, selector):
        h = ProviderHealth(
            has_api_key=True,
            success_count=2,
            failure_count=8,
            consecutive_failures=5,
            last_failure_time=time.time(),
        )
        assert h.health_score < 0.2

    def test_status_emoji(self, selector):
        assert ProviderHealth(has_api_key=False).status_emoji == "\u274c"
        assert ProviderHealth(has_api_key=True).status_emoji == "\u2705"
        assert ProviderHealth(has_api_key=True, consecutive_failures=3).status_emoji == "\U0001f7e1"
        assert ProviderHealth(
            has_api_key=True, consecutive_failures=5, last_failure_time=time.time()
        ).status_emoji == "\u26a0\ufe0f"


# ---------------------------------------------------------------------------
# Smart Mode Selection Tests
# ---------------------------------------------------------------------------

class TestSmartMode:
    def test_smart_mode_picks_healthy_provider(self, selector_with_keys):
        selector_with_keys.update_settings(smart_mode=True, provider="openai")
        selector_with_keys.record_success("openai", "gpt-4o")
        selector_with_keys.record_failure("anthropic", "claude-3", "timeout")
        selected = selector_with_keys.select_provider()
        assert selected == "openai"

    def test_smart_mode_falls_back(self, selector_with_keys):
        selector_with_keys.update_settings(
            smart_mode=True,
            provider="openai",
            fallback_provider="google",
        )
        for _ in range(6):
            selector_with_keys.record_failure("openai", "gpt-4o", "down")
        selected = selector_with_keys.select_provider()
        assert selected in ("google", "openrouter", "groq", "mistral", "anthropic")
        assert selected != "openai"

    def test_smart_mode_picks_best_score(self, selector_with_keys):
        selector_with_keys.update_settings(smart_mode=True, provider="mistral")
        for _ in range(10):
            selector_with_keys.record_success("groq", "llama-3.3-70b")
        for _ in range(5):
            selector_with_keys.record_failure("mistral", "mistral-large", "err")
        for _ in range(5):
            selector_with_keys.record_failure("openrouter", "nemotron", "err")
        selected = selector_with_keys.select_provider()
        assert selected == "groq"

    def test_manual_mode_uses_selected(self, selector_with_keys):
        selector_with_keys.update_settings(smart_mode=False, provider="groq")
        config = selector_with_keys.get_active_config()
        assert config["provider"] == "groq"
        assert config["smart_mode"] is False


# ---------------------------------------------------------------------------
# Google Tier Detection Tests (with mocked HTTP)
# ---------------------------------------------------------------------------

class TestGoogleTierDetection:
    def _mock_http_200_no_headers(self, url, payload, timeout=10, **kwargs):
        return 200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}, {}

    def _mock_http_200_with_low_limit(self, url, payload, timeout=10, **kwargs):
        return 200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}, {
            "RateLimit-Limit": "15",
            "RateLimit-Remaining": "14",
        }

    def _mock_http_200_with_high_limit(self, url, payload, timeout=10, **kwargs):
        return 200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}, {
            "RateLimit-Limit": "60",
            "RateLimit-Remaining": "59",
        }

    def _mock_http_429_free(self, url, payload, timeout=10, **kwargs):
        return 429, {
            "error": {
                "code": 429,
                "message": "Resource has been exhausted",
                "status": "RESOURCE_EXHAUSTED",
                "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "57s"}],
            }
        }, {"Retry-After": "57"}

    def _mock_http_429_no_details(self, url, payload, timeout=10, **kwargs):
        return 429, {"error": {"code": 429, "message": "quota exceeded"}}, {}

    def _mock_http_first_200_second_429(self, url, payload, timeout=10, **kwargs):
        """First call returns 200, second returns 429 (simulates free tier RPM)."""
        call_count = getattr(self.__class__, "_probe_calls", 0)
        self.__class__._probe_calls = call_count + 1
        if call_count == 0:
            return 200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}, {}
        return 429, {
            "error": {
                "code": 429,
                "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "57s"}],
            }
        }, {"Retry-After": "57"}

    def test_free_tier_429_with_retry(self, selector_with_keys):
        with patch("azure.model_selector._http_post_json", side_effect=self._mock_http_429_free):
            tier = selector_with_keys.detect_google_tier("test-key")
        assert tier == "free"

    def test_free_tier_429_no_details(self, selector_with_keys):
        with patch("azure.model_selector._http_post_json", side_effect=self._mock_http_429_no_details):
            tier = selector_with_keys.detect_google_tier("test-key")
        assert tier == "free"

    def test_paid_tier_high_limit(self, selector_with_keys):
        with patch("azure.model_selector._http_post_json", side_effect=self._mock_http_200_with_high_limit):
            tier = selector_with_keys.detect_google_tier("test-key")
        assert tier == "paid"

    def test_free_tier_low_limit(self, selector_with_keys):
        with patch("azure.model_selector._http_post_json", side_effect=self._mock_http_200_with_low_limit):
            tier = selector_with_keys.detect_google_tier("test-key")
        assert tier == "free"

    def test_two_rpm_probe_free(self, selector_with_keys):
        self.__class__._probe_calls = 0
        with patch("azure.model_selector._http_post_json", side_effect=self._mock_http_first_200_second_429):
            with patch("azure.model_selector.time.sleep"):
                tier = selector_with_keys.detect_google_tier("test-key")
        assert tier == "free"

    def test_no_key(self, selector):
        tier = selector.detect_google_tier()
        assert tier == "no_key"

    def test_paid_tier_assumed_when_both_succeed(self, selector_with_keys):
        with patch("azure.model_selector._http_post_json", side_effect=self._mock_http_200_no_headers):
            with patch("azure.model_selector.time.sleep"):
                tier = selector_with_keys.detect_google_tier("test-key")
        assert tier == "paid"


# ---------------------------------------------------------------------------
# Google 429 Tier Parser Tests
# ---------------------------------------------------------------------------

class TestGoogle429Parser:
    def test_free_from_retry_after(self):
        body = {}
        headers = {"Retry-After": "57"}
        assert _parse_google_429_tier(body, headers) == "free"

    def test_free_from_details(self):
        body = {"error": {"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "60s"}]}}
        assert _parse_google_429_tier(body, {}) == "free"

    def test_free_no_info(self):
        assert _parse_google_429_tier({}, {}) == "free"

    def test_paid_short_delay(self):
        body = {"error": {"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "10s"}]}}
        assert _parse_google_429_tier(body, {}) == "paid"


# ---------------------------------------------------------------------------
# OpenRouter Model Fetching Tests (mocked)
# ---------------------------------------------------------------------------

class TestOpenRouterModels:
    def _mock_openrouter_response(self):
        return {
            "data": [
                {"id": "nvidia/nemotron-3-ultra-550b-a55b:free", "name": "Nemotron Ultra", "context_length": 1000000,
                 "pricing": {"prompt": "0", "completion": "0"}},
                {"id": "openai/gpt-4o", "name": "GPT-4o", "context_length": 128000,
                 "pricing": {"prompt": "0.0025", "completion": "0.01"}},
                {"id": "meta-llama/llama-4-maverick:free", "name": "Llama 4 Maverick", "context_length": 1000000,
                 "pricing": {"prompt": "0", "completion": "0"}},
                {"id": "anthropic/claude-3.5-sonnet", "name": "Claude 3.5", "context_length": 200000,
                 "pricing": {"prompt": "0.003", "completion": "0.015"}},
            ]
        }

    def test_fetch_models_filters_free(self, selector_with_keys):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(self._mock_openrouter_response()).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            models = selector_with_keys.fetch_openrouter_models(force=True)

        free = [m for m in models if m["is_free"]]
        paid = [m for m in models if not m["is_free"]]
        assert len(free) == 2
        assert len(paid) == 2
        assert any("nemotron" in m["id"] for m in free)
        assert any("gpt-4o" in m["id"] for m in paid)

    def test_fetch_models_caches(self, selector_with_keys):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(self._mock_openrouter_response()).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            selector_with_keys.fetch_openrouter_models(force=True)
            second_call = selector_with_keys.fetch_openrouter_models(force=False)

        assert len(second_call) == 4

    def test_fetch_models_fallback_on_error(self, selector_with_keys):
        with patch("urllib.request.urlopen", side_effect=Exception("network error")):
            models = selector_with_keys.fetch_openrouter_models(force=True)
        assert isinstance(models, list)


# ---------------------------------------------------------------------------
# Test Provider (Real API Call Simulation)
# ---------------------------------------------------------------------------

class TestProviderTesting:
    def _mock_openai_response(self, *args, **kwargs):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "ok"}}]
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def _mock_anthropic_response(self, *args, **kwargs):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "content": [{"type": "text", "text": "ok"}]
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def _mock_google_response(self, url, payload, timeout=10):
        return 200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}, {}

    def test_test_openai_success(self, selector_with_keys):
        selector_with_keys.update_settings(provider="openai", model="gpt-4o")
        with patch("urllib.request.urlopen", side_effect=self._mock_openai_response):
            result = selector_with_keys.test_provider("openai", "gpt-4o")
        assert result["success"] is True
        assert result["latency"] >= 0
        assert result["provider"] == "openai"

    def test_test_anthropic_success(self, selector_with_keys):
        with patch("urllib.request.urlopen", side_effect=self._mock_anthropic_response):
            result = selector_with_keys.test_provider("anthropic", "claude-sonnet-4-20250514")
        assert result["success"] is True
        assert result["provider"] == "anthropic"

    def test_test_google_success(self, selector_with_keys):
        with patch("azure.model_selector._http_post_json", side_effect=self._mock_google_response):
            result = selector_with_keys.test_provider("google", "gemini-2.0-flash")
        assert result["success"] is True
        assert result["provider"] == "google"

    def test_test_no_api_key(self, selector):
        with patch.dict("os.environ", {
            "OPENAI_API_KEY": "", "AZURE_OPENAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "", "AZURE_ANTHROPIC_API_KEY": "",
            "GOOGLE_API_KEY": "", "GEMINI_API_KEY": "", "AZURE_GOOGLE_API_KEY": "",
            "GROQ_API_KEY": "", "AZURE_GROQ_API_KEY": "",
            "MISTRAL_API_KEY": "", "AZURE_MISTRAL_API_KEY": "",
            "OPENROUTER_API_KEY": "", "AZURE_OPENROUTER_API_KEY": "",
        }, clear=False):
            selector._detect_api_keys()
            result = selector.test_provider("openai", "gpt-4o")
        assert result["success"] is False
        assert "No API key" in result["error"]

    def test_test_api_error(self, selector_with_keys):
        error_resp = MagicMock()
        error_resp.code = 401
        error_resp.read.return_value = json.dumps({
            "error": {"message": "Invalid API key"}
        }).encode()
        error_resp.__enter__ = lambda s: s
        error_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            url="", code=401, msg="Unauthorized", hdrs={}, fp=error_resp
        )):
            result = selector_with_keys.test_provider("openai", "gpt-4o")
        assert result["success"] is False
        assert "Invalid API key" in result["error"]


# ---------------------------------------------------------------------------
# Settings Persistence Tests
# ---------------------------------------------------------------------------

class TestSettingsPersistence:
    def test_health_writes_are_debounced(self, tmp_path, monkeypatch):
        import azure.model_selector as ms

        monkeypatch.delenv("AZURE_LLM_PROVIDER", raising=False)
        monkeypatch.setattr(ms, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(ms, "HEALTH_FILE", tmp_path / "model_health.json")
        selector = ModelSelector()

        selector.record_success("mistral", "mistral-large")
        assert ms.HEALTH_FILE.exists()
        first_saved_at = selector._last_health_save

        selector.record_success("mistral", "mistral-large")
        assert selector._health_save_pending is True
        assert selector._last_health_save == first_saved_at

        selector.flush_health()
        assert selector._health_save_pending is False

    def test_settings_saved(self, selector_with_keys):
        selector_with_keys.update_settings(provider="groq", model="llama-3.3-70b")
        settings = selector_with_keys.get_settings()
        assert settings["provider"] == "groq"
        assert settings["model"] == "llama-3.3-70b"

    def test_settings_persist_across_instances(self, tmp_path, monkeypatch):
        import azure.model_selector as ms
        original_dir = ms.CONFIG_DIR
        monkeypatch.delenv("AZURE_LLM_PROVIDER", raising=False)
        ms.CONFIG_DIR = tmp_path
        ms.HEALTH_FILE = tmp_path / "model_health.json"

        sel1 = ModelSelector()
        sel1.update_settings(provider="mistral", model="mistral-large")
        sel1.record_success("mistral", "mistral-large")

        sel2 = ModelSelector()
        assert sel2.get_settings()["provider"] == "mistral"

        ms.CONFIG_DIR = original_dir
        ms.HEALTH_FILE = original_dir / "model_health.json"


# ---------------------------------------------------------------------------
# Recommended Model Tests
# ---------------------------------------------------------------------------

class TestRecommendedModel:
    def test_openrouter_free_models(self, selector_with_keys):
        model = selector_with_keys.get_recommended_model("openrouter")
        assert "free" in model or "nemotron" in model

    def test_google_free_tier(self, selector_with_keys):
        selector_with_keys._providers["google"].health.tier = "free"
        model = selector_with_keys.get_recommended_model("google")
        assert "gemini" in model.lower() or "gemma" in model.lower()

    def test_unknown_provider_returns_default(self, selector_with_keys):
        model = selector_with_keys.get_recommended_model("nonexistent")
        assert model == selector_with_keys.get_settings()["model"]


# ---------------------------------------------------------------------------
# Display Name Tests
# ---------------------------------------------------------------------------

class TestDisplayNames:
    def test_all_providers_have_names(self, selector_with_keys):
        for name in ALL_PROVIDERS:
            display = selector_with_keys.get_provider_display_name(name)
            assert display and len(display) > 0
            assert display != name or name in ("openrouter",)
