"""Smart Model Auto-Selector with provider health tracking and fallback.

Covers all 6 supported providers: openai, anthropic, google, groq, mistral, openrouter.
Persists health state and user settings to configs/model_health.json.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from azure.model_catalog import (
    PROVIDER_CATALOGS,
    get_recommendations,
)

logger = logging.getLogger("azure.model_selector")

CONFIG_DIR = Path(__file__).parent.parent / "configs"
HEALTH_FILE = CONFIG_DIR / "model_health.json"
MODEL_CACHE_TTL = 3600
AUTO_RECOVER_SECONDS = 300

# ── Model listing API endpoints (override via env) ───────────────────
# Env vars provide the BASE URL (e.g. "https://api.openai.com/v1"),
# "/models" is appended if not already present.
def _models_url(env_var: str, default: str) -> str:
    base = os.environ.get(env_var, "").strip()
    if not base:
        return default
    if not base.endswith("/models"):
        base = base.rstrip("/") + "/models"
    return base

OPENAI_MODELS_URL   = _models_url("AZURE_OPENAI_API_BASE", "https://api.openai.com/v1/models")
ANTHROPIC_MODELS_URL= _models_url("AZURE_ANTHROPIC_API_BASE", "https://api.anthropic.com/v1/models")
GOOGLE_MODELS_URL   = _models_url("AZURE_GOOGLE_API_BASE", "https://generativelanguage.googleapis.com/v1beta/models")
GROQ_MODELS_URL     = _models_url("AZURE_GROQ_API_BASE", "https://api.groq.com/openai/v1/models")
MISTRAL_MODELS_URL  = _models_url("AZURE_MISTRAL_API_BASE", "https://api.mistral.ai/v1/models")
OPENROUTER_MODELS_URL = _models_url("AZURE_OPENROUTER_API_BASE", "https://openrouter.ai/api/v1/models")



ALL_PROVIDERS = ("openai", "anthropic", "google", "groq", "mistral", "openrouter", "nararouter")


def _first_env(*names: str) -> str:
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


def _urlopen_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "AzureBot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _http_post_json(url: str, payload: dict, timeout: int = 10, extra_headers: dict | None = None) -> tuple[int, dict, dict]:
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "AzureBot/1.0"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        url, data=data, headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode()), dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = {}
        with contextlib.suppress(Exception):
            body = json.loads(e.read().decode())
        return e.code, body, dict(e.headers)


def _parse_google_429_tier(body: dict, headers: dict) -> str:
    """Parse Google 429 response to determine free vs paid tier."""
    # Check Retry-After header
    retry_after = headers.get("Retry-After", "")
    if retry_after:
        try:
            secs = int(retry_after)
            if secs >= 55:
                return "free"
        except ValueError:
            pass

    # Check error details for RetryInfo
    details = body.get("error", {}).get("details", [])
    if isinstance(details, list):
        for d in details:
            if d.get("@type", "").endswith("RetryInfo"):
                delay_str = d.get("retryDelay", "60s")
                try:
                    secs = int(delay_str.replace("s", ""))
                    return "free" if secs >= 55 else "paid"
                except ValueError:
                    return "free"

    # 429 with no details → likely free
    return "free"


@dataclass
class ProviderHealth:
    success_count: int = 0
    failure_count: int = 0
    last_error: str = ""
    last_error_time: float = 0.0
    last_success_time: float = 0.0
    last_failure_time: float = 0.0
    consecutive_failures: int = 0
    tier: str = "unknown"
    rpm_limit: int = 60
    rpm_remaining: int = 60
    has_api_key: bool = False

    @property
    def is_healthy(self) -> bool:
        if not self.has_api_key:
            return False
        return not (self.consecutive_failures >= 5 and time.time() - self.last_failure_time < AUTO_RECOVER_SECONDS)

    @property
    def health_score(self) -> float:
        if not self.has_api_key:
            return 0.0
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.5
        base = self.success_count / total
        if not self.is_healthy:
            base *= 0.1
        if self.last_success_time:
            age = time.time() - self.last_success_time
            if age < 60:
                base *= 1.2
            elif age > 300:
                base *= 0.8
        return min(base, 1.0)

    @property
    def status_emoji(self) -> str:
        if not self.has_api_key:
            return "\u274c"  # red X
        if not self.is_healthy:
            return "\u26a0\ufe0f"  # warning
        if self.consecutive_failures > 0:
            return "\U0001f7e1"  # yellow circle
        return "\u2705"  # green check


@dataclass
class ProviderState:
    health: ProviderHealth = field(default_factory=ProviderHealth)
    all_models_cache: list[dict] = field(default_factory=list)
    free_models_cache: list[dict] = field(default_factory=list)
    cache_time: float = 0.0


class ModelSelector:
    def __init__(self):
        self._lock = threading.RLock()
        self._last_health_save = 0.0
        self._health_save_pending = False
        self._health_save_debounce_seconds = 5.0
        self._providers: dict[str, ProviderState] = {name: ProviderState() for name in ALL_PROVIDERS}
        self._settings: dict = {
            "smart_mode": True,
            "provider": "openrouter",
            "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
            "fallback_provider": "openrouter",
            "fallback_model": "nvidia/nemotron-3-super-120b-a12b:free",
        }
        self._load_health()
        self._detect_api_keys()
        self._select_configured_provider()
        self._load_default_models()
        # If AZURE_LLM_PROVIDER is explicitly set in .env, it takes
        # precedence over whatever was saved from a previous /settings
        # session — makes it easy to swap providers by editing .env.
        env_provider = os.environ.get("AZURE_LLM_PROVIDER", "").strip().lower()
        if env_provider and env_provider in ALL_PROVIDERS:
            self._settings["provider"] = env_provider
            # Respect an explicit model from .env. Previously this always
            # selected the first catalog entry, which silently forced
            # OpenRouter onto a slow or unreliable free model.
            env_model = os.environ.get(
                f"AZURE_{env_provider.upper()}_MODEL", ""
            ).strip()
            cat = PROVIDER_CATALOGS.get(env_provider, {})
            if env_model:
                self._settings["model"] = env_model
            elif cat.get("models"):
                self._settings["model"] = cat["models"][0].id

            env_fallback_model = os.environ.get(
                f"AZURE_{env_provider.upper()}_FALLBACK_MODEL", ""
            ).strip()
            if env_fallback_model:
                self._settings["fallback_model"] = env_fallback_model

    def _select_configured_provider(self):
        """Use an available provider when persisted settings point elsewhere."""
        current = self._settings.get("provider", "")
        if current in self._providers and self._providers[current].health.has_api_key:
            return
        for provider in ALL_PROVIDERS:
            if self._providers[provider].health.has_api_key:
                self._settings["provider"] = provider
                models = PROVIDER_CATALOGS.get(provider, {}).get("models", [])
                if models:
                    self._settings["model"] = models[0].id
                return

    def _detect_api_keys(self):
        for name, cat in PROVIDER_CATALOGS.items():
            key = _first_env(*cat["api_key_envs"])
            self._providers[name].health.has_api_key = bool(key)

    def _load_health(self):
        try:
            if HEALTH_FILE.exists():
                data = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
                for pname, h_data in data.get("providers", {}).items():
                    if pname in self._providers:
                        for k, v in h_data.items():
                            if k != "has_api_key":
                                setattr(self._providers[pname].health, k, v)
                for key, val in data.get("settings", {}).items():
                    if key in self._settings:
                        self._settings[key] = val
        except Exception:
            logger.exception("[model_selector] health load failed")

    def _save_health(self, *, force: bool = False):
        """Persist health/settings to disk, debounced to avoid a write per API call.

        record_success/record_failure fire on every LLM call — without
        debouncing this becomes a synchronous disk write in the hot path.
        Pass force=True (e.g. from update_settings) to flush immediately
        since those are rare, user-initiated changes that should be durable
        right away.
        """
        now = time.time()
        with self._lock:
            if not force and now - self._last_health_save < self._health_save_debounce_seconds:
                self._health_save_pending = True
                return
            self._last_health_save = now
            self._health_save_pending = False
            data = {
                "providers": {
                    name: asdict(state.health)
                    for name, state in self._providers.items()
                },
                "settings": dict(self._settings),
                "saved_at": datetime.now(UTC).isoformat(),
            }
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = HEALTH_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(HEALTH_FILE)
        except Exception:
            logger.exception("[model_selector] health save failed")

    def flush_health(self) -> None:
        """Persist a pending debounced health update during graceful shutdown."""
        with self._lock:
            if not self._health_save_pending:
                return
        self._save_health(force=True)

    def _load_default_models(self):
        for name, cat in PROVIDER_CATALOGS.items():
            state = self._providers[name]
            if not state.all_models_cache:
                state.all_models_cache = [
                    {"id": m.id, "name": m.name, "is_free": m.free_tier,
                     "context_length": m.context_window, "prompt_price": m.input_price,
                     "completion_price": m.output_price, "description": m.description}
                    for m in cat["models"]
                ]
            if not state.free_models_cache:
                state.free_models_cache = [
                    {"id": m.id, "name": m.name, "is_free": True,
                     "context_length": m.context_window, "prompt_price": m.input_price,
                     "completion_price": m.output_price, "description": m.description}
                    for m in cat["models"] if m.free_tier
                ]

    def refresh_api_keys(self):
        self._detect_api_keys()
        self._save_health(force=True)

    def update_settings(self, **kwargs):
        with self._lock:
            for key in ("smart_mode", "provider", "model", "fallback_provider", "fallback_model"):
                if key in kwargs:
                    val = kwargs[key]
                    if key in ("model", "fallback_model") and isinstance(val, str) and val:
                        prov_key = "provider" if key == "model" else "fallback_provider"
                        provider = kwargs.get(prov_key, self._settings.get(prov_key, ""))
                        if provider and provider in self._providers:
                            cached = self._providers[provider].all_models_cache
                            valid_ids = {m["id"] for m in cached if m.get("id")}
                            if valid_ids and val not in valid_ids:
                                logger.warning(
                                    "[model_selector] model '%s' not in %s model list — accepting anyway",
                                    val, provider,
                                )
                    self._settings[key] = val
            self._save_health(force=True)

    def get_settings(self) -> dict:
        with self._lock:
            return dict(self._settings)

    def get_active_config(self) -> dict:
        with self._lock:
            if self._settings["smart_mode"]:
                provider = self.select_provider()
                model = self.get_recommended_model(provider)
                return {
                    "provider": provider,
                    "model": model,
                    "smart_mode": True,
                    "tier": self._providers[provider].health.tier,
                }
            provider = self._settings["provider"]
            # Settings may name a provider that isn't registered; don't KeyError.
            prov = self._providers.get(provider)
            return {
                "provider": provider,
                "model": self._settings["model"],
                "smart_mode": False,
                "tier": prov.health.tier if prov else "unknown",
            }

    def select_provider(self) -> str:
        with self._lock:
            primary = self._settings.get("provider", "")
            if primary not in self._providers:
                primary = ""
            if primary and self._providers[primary].health.is_healthy:
                return primary

            fallback = self._settings.get("fallback_provider", "")
            if fallback and fallback in self._providers and self._providers[fallback].health.is_healthy:
                return fallback

            best_name = max(self._providers, key=lambda n: self._providers[n].health.health_score) if self._providers else ""
            return best_name

    def record_success(self, provider: str, model: str):
        with self._lock:
            if provider not in self._providers:
                return
            state = self._providers[provider]
            state.health.success_count += 1
            state.health.consecutive_failures = 0
            state.health.last_success_time = time.time()
            existing_ids = {m["id"] for m in state.all_models_cache}
            if model not in existing_ids:
                state.all_models_cache.append({"id": model, "name": model, "is_free": ":free" in model})
            self._save_health()

    def record_failure(self, provider: str, model: str, error: str):
        with self._lock:
            if provider not in self._providers:
                return
            state = self._providers[provider]
            state.health.failure_count += 1
            state.health.consecutive_failures += 1
            state.health.last_error = error
            state.health.last_error_time = time.time()
            state.health.last_failure_time = time.time()
            self._save_health()

    def get_recommended_model(self, provider: str) -> str:
        with self._lock:
            if provider not in self._providers:
                return self._settings["model"]

            state = self._providers[provider]
            tier = state.health.tier

            recs = get_recommendations(provider, tier)
            if recs:
                chosen = self._settings.get("model", "")
                ids = [m.id for m in recs]
                if chosen in ids:
                    return chosen
                return recs[0].id

            return self._settings["model"]

    def get_provider_display_name(self, provider: str) -> str:
        cat = PROVIDER_CATALOGS.get(provider, {})
        return cat.get("display_name", provider.title())

    def fetch_openrouter_models(self, force: bool = False) -> list[dict]:
        with self._lock:
            state = self._providers["openrouter"]
            if not force and state.cache_time and (time.time() - state.cache_time) < MODEL_CACHE_TTL:
                return state.all_models_cache

        try:
            data = _urlopen_json(OPENROUTER_MODELS_URL)
            models = data.get("data", [])
            free_models = []
            all_models = []
            for m in models:
                mid = m.get("id", "")
                pricing = m.get("pricing", {})
                prompt_price = float(pricing.get("prompt", "0") or "0")
                completion_price = float(pricing.get("completion", "0") or "0")
                is_free = ":free" in mid or (prompt_price == 0 and completion_price == 0)
                entry = {
                    "id": mid,
                    "name": m.get("name", mid),
                    "context_length": m.get("context_length", 0),
                    "prompt_price": prompt_price,
                    "completion_price": completion_price,
                    "is_free": is_free,
                }
                all_models.append(entry)
                if is_free:
                    free_models.append(entry)

            with self._lock:
                state = self._providers["openrouter"]
                state.all_models_cache = all_models
                state.free_models_cache = free_models
                state.cache_time = time.time()
            return all_models
        except Exception as e:
            logger.warning("Failed to fetch OpenRouter models: %s", e)
            with self._lock:
                return self._providers["openrouter"].all_models_cache

    def fetch_groq_models(self, force: bool = False) -> list[dict]:
        key = _first_env("AZURE_GROQ_API_KEY", "GROQ_API_KEY")
        if not key:
            from azure.model_catalog import get_models_for_provider
            return [{"id": m.id, "name": m.name, "is_free": m.free_tier,
                     "context_length": m.context_window, "prompt_price": m.input_price,
                     "completion_price": m.output_price, "description": m.description}
                    for m in get_models_for_provider("groq")]

        with self._lock:
            state = self._providers["groq"]
            if not force and state.cache_time and (time.time() - state.cache_time) < MODEL_CACHE_TTL:
                return state.all_models_cache

        try:
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}", "User-Agent": "AzureBot/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            models = data.get("data", [])
            result = []
            for m in models:
                result.append({"id": m.get("id", ""), "name": m.get("id", ""), "is_free": False})
            with self._lock:
                state = self._providers["groq"]
                state.all_models_cache = result
                state.cache_time = time.time()
            return result
        except Exception as e:
            logger.warning("Failed to fetch Groq models: %s", e)
            from azure.model_catalog import get_models_for_provider
            return [{"id": m.id, "name": m.name, "is_free": m.free_tier,
                     "context_length": m.context_window, "prompt_price": m.input_price,
                     "completion_price": m.output_price, "description": m.description}
                    for m in get_models_for_provider("groq")]

    # ------------------------------------------------------------------
    # Dynamic model fetchers — each provider gets its own method so the
    # dispatch below can route cleanly.  All fall back to the hardcoded
    # catalog when the API call fails or no key is available.
    # ------------------------------------------------------------------

    def _fetch_models_catalog(self, provider: str) -> list[dict]:
        """Fallback: return models from the hardcoded catalog."""
        cat = PROVIDER_CATALOGS.get(provider, {})
        return [
            {"id": m.id, "name": m.name, "is_free": m.free_tier,
             "context_length": m.context_window, "prompt_price": m.input_price,
             "completion_price": m.output_price, "description": m.description}
            for m in cat.get("models", [])
        ]

    def _fetch_via_bearer_api(
        self, url: str, key: str, provider: str, *, data_key: str = "data",
        force: bool = False,
        extract: callable = lambda m, _p: {"id": m.get("id", ""), "name": m.get("id", ""), "is_free": False},
    ) -> list[dict]:
        """Generic GET with Bearer token — caches into provider state."""
        state = self._providers[provider]
        with self._lock:
            if not force and state.cache_time and (time.time() - state.cache_time) < MODEL_CACHE_TTL:
                return state.all_models_cache

        try:
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {key}", "User-Agent": "AzureBot/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            raw = data.get(data_key, []) if isinstance(data, dict) else data
            result = [extract(m, provider) for m in raw if extract(m, provider)]
            with self._lock:
                state.all_models_cache = result
                state.cache_time = time.time()
            return result
        except Exception as e:
            logger.warning("Failed to fetch %s models: %s", provider, e)
            return self._fetch_models_catalog(provider)

    def fetch_openai_models(self, force: bool = False) -> list[dict]:
        key = _first_env("AZURE_OPENAI_API_KEY", "OPENAI_API_KEY")
        if not key:
            return self._fetch_models_catalog("openai")
        return self._fetch_via_bearer_api(
            OPENAI_MODELS_URL, key, "openai", force=force,
            extract=lambda m, _p: {"id": m.get("id", ""), "name": m.get("id", ""), "is_free": False},
        )

    def fetch_anthropic_models(self, force: bool = False) -> list[dict]:
        key = _first_env("AZURE_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
        if not key:
            return self._fetch_models_catalog("anthropic")
        state = self._providers["anthropic"]
        with self._lock:
            if not force and state.cache_time and (time.time() - state.cache_time) < MODEL_CACHE_TTL:
                return state.all_models_cache
        try:
            req = urllib.request.Request(
                ANTHROPIC_MODELS_URL,
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "User-Agent": "AzureBot/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            raw = data.get("data", [])
            result = []
            for m in raw:
                mid = m.get("id", "")
                if mid:
                    result.append({
                        "id": mid,
                        "name": m.get("display_name", mid),
                        "is_free": False,
                    })
            with self._lock:
                state.all_models_cache = result
                state.cache_time = time.time()
            return result
        except Exception as e:
            logger.warning("Failed to fetch Anthropic models: %s", e)
            return self._fetch_models_catalog("anthropic")

    def fetch_google_models(self, force: bool = False) -> list[dict]:
        key = _first_env("AZURE_GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
        if not key:
            return self._fetch_models_catalog("google")
        state = self._providers["google"]
        with self._lock:
            if not force and state.cache_time and (time.time() - state.cache_time) < MODEL_CACHE_TTL:
                return state.all_models_cache
        try:
            data = _urlopen_json(f"{GOOGLE_MODELS_URL}?key={key}")
            raw = data.get("models", [])
            result = []
            for m in raw:
                mid = m.get("name", "")
                methods = m.get("supportedGenerationMethods", [])
                # Skip non-chat models
                if "generateContent" not in methods and "generateMessage" not in methods:
                    continue
                mid_clean = mid.replace("models/", "", 1) if mid.startswith("models/") else mid
                result.append({
                    "id": mid_clean,
                    "name": m.get("displayName", mid_clean),
                    "is_free": False,
                    "context_length": m.get("inputTokenLimit", 0),
                })
            with self._lock:
                state.all_models_cache = result
                state.cache_time = time.time()
            return result
        except Exception as e:
            logger.warning("Failed to fetch Google models: %s", e)
            return self._fetch_models_catalog("google")

    def fetch_mistral_models(self, force: bool = False) -> list[dict]:
        key = _first_env("AZURE_MISTRAL_API_KEY", "MISTRAL_API_KEY")
        if not key:
            return self._fetch_models_catalog("mistral")
        return self._fetch_via_bearer_api(
            MISTRAL_MODELS_URL, key, "mistral", force=force,
            extract=lambda m, _p: {"id": m.get("id", ""), "name": m.get("id", ""), "is_free": False},
        )

    def fetch_nararouter_models(self, force: bool = False) -> list[dict]:
        """NaraRouter is OpenRouter-compatible — same response format."""
        key = _first_env("AZURE_NARAROUTER_API_KEY", "NARAROUTER_API_KEY")
        if not key:
            return self._fetch_models_catalog("nararouter")
        base = _first_env("AZURE_NARAROUTER_API_BASE") or "https://router.bynara.id/v1"
        url = f"{base.rstrip('/')}/models"
        state = self._providers["nararouter"]
        with self._lock:
            if not force and state.cache_time and (time.time() - state.cache_time) < MODEL_CACHE_TTL:
                return state.all_models_cache
        try:
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {key}", "User-Agent": "AzureBot/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            raw = data.get("data", [])
            result = []
            for m in raw:
                mid = m.get("id", "")
                pricing = m.get("pricing", {})
                prompt_price = float(pricing.get("prompt", "0") or "0")
                completion_price = float(pricing.get("completion", "0") or "0")
                is_free = ":free" in mid or (prompt_price == 0 and completion_price == 0)
                result.append({
                    "id": mid,
                    "name": m.get("name", mid),
                    "context_length": m.get("context_length", 0),
                    "is_free": is_free,
                })
            with self._lock:
                state.all_models_cache = result
                state.cache_time = time.time()
            return result
        except Exception as e:
            logger.warning("Failed to fetch NaraRouter models: %s", e)
            return self._fetch_models_catalog("nararouter")

    def fetch_provider_models(self, provider: str, force: bool = False) -> list[dict]:
        """Dispatch to the right fetcher — returns models for any provider."""
        dispatch = {
            "openai":      self.fetch_openai_models,
            "anthropic":   self.fetch_anthropic_models,
            "google":      self.fetch_google_models,
            "groq":        self.fetch_groq_models,
            "mistral":     self.fetch_mistral_models,
            "openrouter":  self.fetch_openrouter_models,
            "nararouter":  self.fetch_nararouter_models,
        }
        fetcher = dispatch.get(provider)
        if fetcher:
            return fetcher(force=force)
        return []

    def detect_google_tier(self, api_key: str | None = None) -> str:
        """Detect Google AI Studio tier by probing the API.

        Strategy:
        1. Send first request — if 429, it's free (quota exhausted).
        2. Send second request 0.5s later — if 429, it's free (5 RPM limit).
        3. Both succeed + low RateLimit-Limit header → free.
        4. Both succeed + high/missing header → paid.
        """
        if not api_key:
            api_key = _first_env("AZURE_GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
        if not api_key:
            return "no_key"

        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent"
        payload = {"contents": [{"parts": [{"text": "hi"}]}]}

        # First request
        status1, body1, headers1 = _http_post_json(url, payload, extra_headers={"X-Goog-Api-Key": api_key})

        with self._lock:
            state = self._providers["google"]

            # 429 on first try → definitely free (quota exhausted)
            if status1 == 429:
                tier = _parse_google_429_tier(body1, headers1)
                state.health.tier = tier
                state.health.has_api_key = True
                state.health.rpm_limit = 5 if tier == "free" else 60
                state.health.rpm_remaining = 0
                return tier

            # 200 on first try — send a second rapid request to test RPM
            if status1 == 200:
                # Check rate limit headers (Google uses both formats)
                limit_header = headers1.get("RateLimit-Limit") or headers1.get("X-RateLimit-Limit")
                remaining_header = headers1.get("RateLimit-Remaining") or headers1.get("X-RateLimit-Remaining")

                if limit_header:
                    try:
                        limit = int(limit_header)
                        tier = "free" if limit <= 30 else "paid"
                        state.health.tier = tier
                        state.health.has_api_key = True
                        state.health.rpm_limit = limit
                        state.health.rpm_remaining = int(remaining_header) if remaining_header else limit
                        return tier
                    except ValueError:
                        logger.warning("[model_selector] failed to parse rate limit header: limit=%s, remaining=%s", limit_header, remaining_header)

                # No rate limit headers — probe with second request
                import time as _time
                _time.sleep(0.5)
                status2, body2, headers2 = _http_post_json(url, payload, extra_headers={"X-Goog-Api-Key": api_key})

                if status2 == 429:
                    tier = _parse_google_429_tier(body2, headers2)
                    state.health.tier = tier
                    state.health.has_api_key = True
                    state.health.rpm_limit = 5 if tier == "free" else 60
                    state.health.rpm_remaining = 0
                    return tier

                # Both succeeded — check headers on second response
                limit2 = headers2.get("RateLimit-Limit") or headers2.get("X-RateLimit-Limit")
                if limit2:
                    try:
                        limit_val = int(limit2)
                        tier = "free" if limit_val <= 30 else "paid"
                        state.health.tier = tier
                        state.health.has_api_key = True
                        state.health.rpm_limit = limit_val
                        state.health.rpm_remaining = int(headers2.get("RateLimit-Remaining", limit2))
                        return tier
                    except ValueError:
                        pass

                # Both succeeded, no rate limit headers → assume paid
                # Free tier would likely 429 on the second request
                state.health.tier = "paid"
                state.health.has_api_key = True
                state.health.rpm_limit = 60
                state.health.rpm_remaining = 60
                return "paid"

            # Other error
            state.health.tier = "unknown"
            state.health.has_api_key = True
            return "unknown"

    def get_provider_health(self, provider: str | None = None) -> dict:
        with self._lock:
            if provider:
                if provider in self._providers:
                    return {provider: asdict(self._providers[provider].health)}
                return {}
            return {name: asdict(state.health) for name, state in self._providers.items()}

    def get_all_models(self, provider: str | None = None) -> dict:
        with self._lock:
            if provider:
                if provider in self._providers:
                    state = self._providers[provider]
                    return {provider: {"all": state.all_models_cache, "free": state.free_models_cache}}
                return {}
            return {
                name: {"all": state.all_models_cache, "free": state.free_models_cache}
                for name, state in self._providers.items()
            }

    def test_provider(self, provider: str, model: str | None = None) -> dict:
        if not model:
            model = self.get_recommended_model(provider)

        info = PROVIDER_CATALOGS.get(provider, {})
        api_key = _first_env(*info.get("api_key_envs", ()))
        if not api_key:
            return {"success": False, "error": "No API key configured", "provider": provider, "model": model}

        start = time.time()
        try:
            if provider == "google":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                status, body, _ = _http_post_json(url, {"contents": [{"parts": [{"text": "Say 'ok'"}]}]})
                latency = time.time() - start
                if status == 200:
                    self.record_success(provider, model)
                    return {"success": True, "latency": round(latency, 2), "provider": provider, "model": model}
                error_msg = body.get("error", {}).get("message", f"HTTP {status}")
                self.record_failure(provider, model, error_msg)
                return {"success": False, "error": error_msg, "latency": round(latency, 2), "provider": provider, "model": model}

            if provider == "anthropic":
                url = "https://api.anthropic.com/v1/messages"
                payload = {"model": model, "max_tokens": 10, "messages": [{"role": "user", "content": "Say ok"}]}
                data = json.dumps(payload).encode()
                req = urllib.request.Request(url, data=data, headers={
                    "x-api-key": api_key, "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json", "User-Agent": "AzureBot/1.0",
                })
                with urllib.request.urlopen(req, timeout=15) as resp:
                    body = json.loads(resp.read().decode())
                latency = time.time() - start
                self.record_success(provider, model)
                return {"success": True, "latency": round(latency, 2), "provider": provider, "model": model}

            openai_compat = {"openai", "groq", "mistral", "openrouter", "nararouter"}
            if provider in openai_compat:
                bases = {
                    "openai": "https://api.openai.com/v1",
                    "groq": "https://api.groq.com/openai/v1",
                    "mistral": "https://api.mistral.ai/v1",
                    "openrouter": "https://openrouter.ai/api/v1",
                    "nararouter": "https://router.bynara.id/v1",
                }
                url = f"{bases[provider]}/chat/completions"
                payload = {"model": model, "messages": [{"role": "user", "content": "Say ok"}], "max_tokens": 10}
                data = json.dumps(payload).encode()
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "AzureBot/1.0"}
                if provider == "openrouter":
                    headers["HTTP-Referer"] = "https://github.com/azure-bot"
                    headers["X-Title"] = "AzureBot"
                req = urllib.request.Request(url, data=data, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    body = json.loads(resp.read().decode())
                latency = time.time() - start
                self.record_success(provider, model)
                return {"success": True, "latency": round(latency, 2), "provider": provider, "model": model}

            return {"success": False, "error": f"Unknown provider: {provider}", "provider": provider, "model": model}

        except urllib.error.HTTPError as e:
            latency = time.time() - start
            error_msg = f"HTTP {e.code}"
            try:
                error_body = json.loads(e.read().decode())
                error_msg = error_body.get("error", {}).get("message", error_msg)
            except Exception:
                logger.exception("[model_selector] failed to parse error body for %s/%s", provider, model)
            self.record_failure(provider, model, error_msg)
            return {"success": False, "error": error_msg, "latency": round(latency, 2), "provider": provider, "model": model}
        except Exception as e:
            latency = time.time() - start
            self.record_failure(provider, model, str(e))
            return {"success": False, "error": str(e), "latency": round(latency, 2), "provider": provider, "model": model}
