"""Settings Management API — provider config, fallback chain, moderation, bot settings, .env viewer."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from .api_auth import get_current_user, require_admin

logger = logging.getLogger("web.settings")

router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

VALID_PROVIDERS = {"openai", "anthropic", "google", "groq", "mistral", "openrouter", "nararouter"}
VALID_MODERATION_PHASES = {"dry_run", "reactive_limited", "reactive_full"}
VALID_CONFIRMATION_MODES = {"none", "destructive", "all"}

# ---------------------------------------------------------------------------
# Provider-test cache (60-second TTL)
# ---------------------------------------------------------------------------

_PROVIDER_TEST_CACHE: dict[str, dict] = {}
_PROVIDER_TEST_CACHE_TTL = 60  # seconds


def _get_cached_test_result(provider_id: str) -> dict | None:
    entry = _PROVIDER_TEST_CACHE.get(provider_id)
    if entry and (time.time() - entry["ts"]) < _PROVIDER_TEST_CACHE_TTL:
        return entry["result"]
    return None


def _set_cached_test_result(provider_id: str, result: dict) -> None:
    _PROVIDER_TEST_CACHE[provider_id] = {"result": result, "ts": time.time()}


def _get_model_selector(request: Request):
    """Return the ModelSelector from the agent or module-level singleton."""
    agent = getattr(request.app.state, "agent", None)
    selector = getattr(agent, "_model_selector", None)
    if selector is None:
        try:
            from azure.model_selector import ModelSelector
            selector = getattr(ModelSelector, "_active_selector", None)
        except Exception:
            logger.exception("[api_settings] model selector lookup failed")
    return selector


_env_lock = threading.Lock()
_audit_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)


def _env_write_key(key: str, value: str) -> None:
    """Write or update a single key in the .env file. Preserves comments and structure."""
    with _env_lock:
        text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        line = f"{key}={value}"
        if pattern.search(text):
            # Pass a replacement *function* so backslashes and group references
            # (\1, \g<..>) in the value are written literally instead of being
            # interpreted as re.sub template syntax (which corrupts or raises).
            text = pattern.sub(lambda _m: line, text)
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            text += line + "\n"
        ENV_PATH.write_text(text, encoding="utf-8")


def _sync_env_from_selector(selector) -> None:
    """Sync provider to .env — models are managed exclusively via Discord/web/health-file."""
    settings = selector.get_settings()
    provider = settings.get("provider", "openrouter")
    os.environ["AZURE_LLM_PROVIDER"] = provider
    _env_write_key("AZURE_LLM_PROVIDER", provider)


def _reload_active_llm() -> None:
    """Hot-reload the running ApiLLM instance after settings change."""
    try:
        from azure.api_llm import ApiLLM
        if ApiLLM._active_llm is not None:
            ok = ApiLLM._active_llm.reload_from_selector()
            logger.info("[settings] reload ActiveLLM ok=%s", ok)
        else:
            logger.warning("[settings] reload skipped: ApiLLM._active_llm is None")
    except Exception as e:
        logger.warning("[settings] reload ActiveLLM failed: %s", e)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_provider(provider: str) -> str:
    """Validate and normalize a provider name."""
    provider = provider.strip().lower()
    if provider not in VALID_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Invalid provider '{provider}'. Must be one of: {', '.join(sorted(VALID_PROVIDERS))}")
    return provider


def _validate_numeric_range(value: float | int, min_val: float, max_val: float, name: str) -> float:
    """Validate a numeric value is within range."""
    if value < min_val or value > max_val:
        raise HTTPException(status_code=400, detail=f"{name} must be between {min_val} and {max_val}, got {value}")
    return float(value)


def _validate_api_key_format(key: str, provider: str) -> str:
    """Basic API key format validation per provider."""
    key = key.strip()
    if not key:
        return key
    patterns = {
        "openai": r"^sk-",
        "anthropic": r"^sk-ant-",
        "google": r"^AI",
        "groq": r"^gsk_",
        "mistral": r"^[a-zA-Z0-9]",
        "openrouter": r"^sk-or-",
        "nararouter": r"^[a-zA-Z0-9]",
    }
    pat = patterns.get(provider)
    if pat and not re.match(pat, key):
        logger.warning("[settings] API key for %s doesn't match expected pattern (proceeding anyway)", provider)
    return key

PROVIDERS = {
    "openai": {
        "name": "OpenAI",
        "key_envs": ["AZURE_OPENAI_API_KEY", "OPENAI_API_KEY"],
        "model_env": "AZURE_OPENAI_MODEL",
        "fallback_model_env": "AZURE_OPENAI_FALLBACK_MODEL",
        "api_base_env": "AZURE_OPENAI_API_BASE",
        "default_model": "gpt-4o-mini",
        "default_api_base": "https://api.openai.com/v1",
        "icon": "🤖",
    },
    "anthropic": {
        "name": "Anthropic",
        "key_envs": ["AZURE_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"],
        "model_env": "AZURE_ANTHROPIC_MODEL",
        "fallback_model_env": "AZURE_ANTHROPIC_FALLBACK_MODEL",
        "api_base_env": "AZURE_ANTHROPIC_API_BASE",
        "default_model": "claude-sonnet-4-20250514",
        "default_api_base": "https://api.anthropic.com/v1",
        "icon": "🧠",
    },
    "google": {
        "name": "Google",
        "key_envs": ["AZURE_GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "model_env": "AZURE_GOOGLE_MODEL",
        "fallback_model_env": "AZURE_GOOGLE_FALLBACK_MODEL",
        "api_base_env": "AZURE_GOOGLE_API_BASE",
        "default_model": "gemini-3.1-flash-lite",
        "default_api_base": "https://generativelanguage.googleapis.com/v1beta",
        "icon": "💎",
    },
    "groq": {
        "name": "Groq",
        "key_envs": ["AZURE_GROQ_API_KEY", "GROQ_API_KEY"],
        "model_env": "AZURE_GROQ_MODEL",
        "fallback_model_env": "AZURE_GROQ_FALLBACK_MODEL",
        "api_base_env": "AZURE_GROQ_API_BASE",
        "default_model": "llama-3.3-70b-versatile",
        "default_api_base": "https://api.groq.com/openai/v1",
        "icon": "⚡",
    },
    "mistral": {
        "name": "Mistral",
        "key_envs": ["AZURE_MISTRAL_API_KEY", "MISTRAL_API_KEY"],
        "model_env": "AZURE_MISTRAL_MODEL",
        "fallback_model_env": "AZURE_MISTRAL_FALLBACK_MODEL",
        "api_base_env": "AZURE_MISTRAL_API_BASE",
        "default_model": "mistral-large-latest",
        "default_api_base": "https://api.mistral.ai/v1",
        "icon": "🌀",
    },
    "openrouter": {
        "name": "OpenRouter",
        "key_envs": ["AZURE_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"],
        "model_env": "AZURE_OPENROUTER_MODEL",
        "fallback_model_env": "AZURE_OPENROUTER_FALLBACK_MODEL",
        "api_base_env": "AZURE_OPENROUTER_API_BASE",
        "default_model": "openai/gpt-4o-mini",
        "default_api_base": "https://openrouter.ai/api/v1",
        "icon": "🌐",
    },
    "nararouter": {
        "name": "NaraRouter",
        "key_envs": ["AZURE_NARAROUTER_API_KEY", "NARAROUTER_API_KEY"],
        "model_env": "AZURE_NARAROUTER_MODEL",
        "fallback_model_env": "AZURE_NARAROUTER_FALLBACK_MODEL",
        "api_base_env": "AZURE_NARAROUTER_API_BASE",
        "default_model": "deepseek-3.2",
        "default_api_base": "https://router.bynara.id/v1",
        "icon": "🔮",
    },
}

# Masking helper — keep first 4 and last 4 chars
_SENSITIVE_KEYS = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|HASH|CREDENTIALS?)", re.IGNORECASE
)


def _mask_value(val: str) -> str:
    if not val or len(val) <= 12:
        return "••••••••" if val else ""
    return val[:4] + "•" * (len(val) - 8) + val[-4:]


def _read_env() -> dict[str, str]:
    """Parse .env into a dict (handles KEY=VALUE, ignores comments/blanks)."""
    result: dict[str, str] = {}
    if not ENV_PATH.exists():
        return result
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


def _format_env_value(value: str) -> str:
    """Quote a value if it needs it, mirroring _read_env's quote-stripping.

    _read_env strips surrounding quotes, so writing a raw value back would
    corrupt anything that requires quoting (spaces, '#', leading/trailing
    whitespace, or embedded quotes) on the next round-trip.
    """
    if value == "":
        return ""
    needs_quotes = (
        value != value.strip()
        or " " in value
        or "#" in value
        or value[0] in ('"', "'")
    )
    if not needs_quotes:
        return value
    # _read_env strips surrounding quotes but does NOT unescape, so pick a
    # quote char the value doesn't contain rather than backslash-escaping.
    if '"' not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"
    # Value contains both quote types — best effort: double-quote and hope the
    # reader's simple strip still balances (rare in practice for env values).
    return f'"{value}"'


def _write_env(data: dict[str, str]) -> None:
    """Rewrite .env preserving comments and structure."""
    with _env_lock:
        lines = [] if not ENV_PATH.exists() else ENV_PATH.read_text(encoding="utf-8").splitlines()

        keys_written = set()
        new_lines: list[str] = []
        for raw_line in lines:
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(raw_line)
                continue
            if "=" not in stripped:
                new_lines.append(raw_line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in data:
                new_lines.append(f"{key}={_format_env_value(data[key])}")
                keys_written.add(key)
            else:
                new_lines.append(raw_line)

        # Append any new keys not already in file
        for key, val in data.items():
            if key not in keys_written:
                new_lines.append(f"{key}={_format_env_value(val)}")

        ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ProviderUpdate(BaseModel):
    api_key: str | None = None
    model: str | None = None
    fallback_model: str | None = None
    api_base: str | None = None

    @field_validator("api_key")
    @classmethod
    def strip_api_key(cls, v):
        return v.strip() if v else v

    @field_validator("model")
    @classmethod
    def strip_model(cls, v):
        return v.strip() if v else v


class FallbackChainUpdate(BaseModel):
    chain: list[str]

    @field_validator("chain")
    @classmethod
    def validate_chain(cls, v):
        if not v:
            raise ValueError("Chain must not be empty")
        for pid in v:
            if pid not in VALID_PROVIDERS:
                raise ValueError(f"Unknown provider '{pid}' in chain")
        return v


class ModerationSettingsUpdate(BaseModel):
    phase: str | None = None
    confirmation_mode: str | None = None
    confirmation_threshold: float | None = None
    cognitive_mode: int | None = None
    semantic_threshold: float | None = None

    @field_validator("phase")
    @classmethod
    def validate_phase(cls, v):
        if v is not None and v not in VALID_MODERATION_PHASES:
            raise ValueError(f"Invalid phase '{v}'. Must be one of: {', '.join(sorted(VALID_MODERATION_PHASES))}")
        return v

    @field_validator("confirmation_mode")
    @classmethod
    def validate_confirmation_mode(cls, v):
        if v is not None and v not in VALID_CONFIRMATION_MODES:
            raise ValueError(f"Invalid confirmation_mode '{v}'. Must be one of: {', '.join(sorted(VALID_CONFIRMATION_MODES))}")
        return v

    @field_validator("confirmation_threshold", "semantic_threshold")
    @classmethod
    def validate_threshold(cls, v):
        if v is not None and (v < 0.0 or v > 1.0):
            raise ValueError(f"Threshold must be between 0.0 and 1.0, got {v}")
        return v

    @field_validator("cognitive_mode")
    @classmethod
    def validate_cognitive_mode(cls, v):
        if v is not None and v not in (0, 1):
            raise ValueError(f"cognitive_mode must be 0 or 1, got {v}")
        return v


class BotSettingsUpdate(BaseModel):
    system_prompt: str | None = None
    default_max_tokens: int | None = None
    default_temperature: float | None = None
    command_cooldown: float | None = None
    rate_limit_window: float | None = None
    rate_limit_max: int | None = None
    rate_limit_cooldown: float | None = None
    response_cache_size: int | None = None
    response_cache_ttl: float | None = None
    context_memory_size: int | None = None
    context_memory_max_users: int | None = None
    max_retries: int | None = None
    retry_delay_base: float | None = None
    retry_delay_max: float | None = None

    @field_validator("default_max_tokens")
    @classmethod
    def validate_max_tokens(cls, v):
        if v is not None and (v < 1 or v > 128000):
            raise ValueError(f"default_max_tokens must be between 1 and 128000, got {v}")
        return v

    @field_validator("default_temperature")
    @classmethod
    def validate_temperature(cls, v):
        if v is not None and (v < 0.0 or v > 2.0):
            raise ValueError(f"default_temperature must be between 0.0 and 2.0, got {v}")
        return v

    @field_validator("command_cooldown")
    @classmethod
    def validate_command_cooldown(cls, v):
        if v is not None and (v < 0.0 or v > 300.0):
            raise ValueError(f"command_cooldown must be between 0.0 and 300.0, got {v}")
        return v

    @field_validator("rate_limit_window")
    @classmethod
    def validate_rate_limit_window(cls, v):
        if v is not None and (v < 1.0 or v > 3600.0):
            raise ValueError(f"rate_limit_window must be between 1.0 and 3600.0, got {v}")
        return v

    @field_validator("rate_limit_max")
    @classmethod
    def validate_rate_limit_max(cls, v):
        if v is not None and (v < 1 or v > 1000):
            raise ValueError(f"rate_limit_max must be between 1 and 1000, got {v}")
        return v

    @field_validator("rate_limit_cooldown")
    @classmethod
    def validate_rate_limit_cooldown(cls, v):
        if v is not None and (v < 0.0 or v > 3600.0):
            raise ValueError(f"rate_limit_cooldown must be between 0.0 and 3600.0, got {v}")
        return v

    @field_validator("response_cache_size")
    @classmethod
    def validate_cache_size(cls, v):
        if v is not None and (v < 0 or v > 10000):
            raise ValueError(f"response_cache_size must be between 0 and 10000, got {v}")
        return v

    @field_validator("response_cache_ttl")
    @classmethod
    def validate_cache_ttl(cls, v):
        if v is not None and (v < 0.0 or v > 86400.0):
            raise ValueError(f"response_cache_ttl must be between 0.0 and 86400.0, got {v}")
        return v

    @field_validator("context_memory_size")
    @classmethod
    def validate_context_memory_size(cls, v):
        if v is not None and (v < 0 or v > 100):
            raise ValueError(f"context_memory_size must be between 0 and 100, got {v}")
        return v

    @field_validator("context_memory_max_users")
    @classmethod
    def validate_context_memory_max_users(cls, v):
        if v is not None and (v < 1 or v > 100000):
            raise ValueError(f"context_memory_max_users must be between 1 and 100000, got {v}")
        return v

    @field_validator("max_retries")
    @classmethod
    def validate_max_retries(cls, v):
        if v is not None and (v < 0 or v > 20):
            raise ValueError(f"max_retries must be between 0 and 20, got {v}")
        return v

    @field_validator("retry_delay_base")
    @classmethod
    def validate_retry_delay_base(cls, v):
        if v is not None and (v < 0.1 or v > 60.0):
            raise ValueError(f"retry_delay_base must be between 0.1 and 60.0, got {v}")
        return v

    @field_validator("retry_delay_max")
    @classmethod
    def validate_retry_delay_max(cls, v):
        if v is not None and (v < 1.0 or v > 300.0):
            raise ValueError(f"retry_delay_max must be between 1.0 and 300.0, got {v}")
        return v


class EnvUpdate(BaseModel):
    key: str
    value: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/all")
async def get_all_settings(request: Request, user: dict = Depends(get_current_user)):
    """Return every settings section in a single payload."""
    env = _read_env()
    agent = getattr(request.app.state, "agent", None)

    # --- Provider cards ---
    provider_cards = {}
    for pid, pconf in PROVIDERS.items():
        raw_key = ""
        for ek in pconf["key_envs"]:
            if env.get(ek):
                raw_key = env[ek]
                break
        provider_cards[pid] = {
            "id": pid,
            "name": pconf["name"],
            "icon": pconf["icon"],
            "has_key": bool(raw_key),
            "key_masked": _mask_value(raw_key) if raw_key else "Not set",
            "model": env.get(pconf["model_env"], pconf["default_model"]),
            "fallback_model": env.get(pconf["fallback_model_env"], ""),
            "api_base": env.get(pconf["api_base_env"], pconf["default_api_base"]),
            "default_model": pconf["default_model"],
            "key_envs": pconf["key_envs"],
            "model_env": pconf["model_env"],
        }

    # --- Fallback chain ---
    current_provider = env.get("AZURE_LLM_PROVIDER", "openrouter")
    fallback_chain = _parse_fallback_chain(env)
    provider_health = {}
    try:
        # Attempt to read live health from the class-level selector
        selector = getattr(agent, "_model_selector", None) or getattr(
            __import__("azure.model_selector", fromlist=["ModelSelector"]),
            "_active_selector",
            None,
        )
        if selector:
            for pid in PROVIDERS:
                h = selector.get_provider_health(pid)
                provider_health[pid] = {
                    "healthy": h.get("is_healthy", False) if isinstance(h, dict) else getattr(h, "is_healthy", False),
                    "has_key": h.get("has_api_key", False) if isinstance(h, dict) else getattr(h, "has_api_key", False),
                    "consecutive_failures": h.get("consecutive_failures", 0) if isinstance(h, dict) else getattr(h, "consecutive_failures", 0),
                    "score": h.get("health_score", 0.0) if isinstance(h, dict) else getattr(h, "health_score", 0.0),
                }
    except Exception:
        # Health not available — just report key status
        for pid in PROVIDERS:
            provider_health[pid] = {
                "healthy": provider_cards[pid]["has_key"],
                "has_key": provider_cards[pid]["has_key"],
                "consecutive_failures": 0,
                "score": 1.0 if provider_cards[pid]["has_key"] else 0.0,
            }

    # --- Moderation settings ---
    def _safe_float(val: str, default: float) -> float:
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def _safe_int(val: str, default: int) -> int:
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    moderation = {
        "phase": env.get("AZURE_MODERATION_PHASE", "dry_run"),
        "confirmation_mode": env.get("AZURE_CONFIRMATION_MODE", "destructive"),
        "confirmation_threshold": _safe_float(env.get("AZURE_CONFIRMATION_THRESHOLD", "0.75"), 0.75),
        "cognitive_mode": _safe_int(env.get("AZURE_COGNITIVE_MODE", "0"), 0),
        "semantic_threshold": _safe_float(env.get("AZURE_SEMANTIC_THRESHOLD", "0.75"), 0.75),
    }

    # --- Bot settings ---
    bot = {
        "system_prompt": _get_system_prompt(),
        "default_max_tokens": _safe_int(env.get("AZURE_DEFAULT_MAX_TOKENS", "150"), 150),
        "default_temperature": _safe_float(env.get("AZURE_DEFAULT_TEMPERATURE", "0.7"), 0.7),
        "command_cooldown": _safe_float(env.get("AZURE_COMMAND_COOLDOWN", "5"), 5),
        "rate_limit_window": _safe_float(env.get("AZURE_RATE_LIMIT_WINDOW", "60.0"), 60.0),
        "rate_limit_max": _safe_int(env.get("AZURE_RATE_LIMIT_MAX", "10"), 10),
        "rate_limit_cooldown": _safe_float(env.get("AZURE_RATE_LIMIT_COOLDOWN", "30.0"), 30.0),
        "response_cache_size": _safe_int(env.get("AZURE_RESPONSE_CACHE_SIZE", "100"), 100),
        "response_cache_ttl": _safe_float(env.get("AZURE_RESPONSE_CACHE_TTL", "3600"), 3600),
        "context_memory_size": _safe_int(env.get("AZURE_CONTEXT_MEMORY_SIZE", "10"), 10),
        "context_memory_max_users": _safe_int(env.get("AZURE_CONTEXT_MEMORY_MAX_USERS", "100"), 100),
        "max_retries": _safe_int(env.get("AZURE_MAX_RETRIES", "3"), 3),
        "retry_delay_base": _safe_float(env.get("AZURE_RETRY_DELAY_BASE", "1.0"), 1.0),
        "retry_delay_max": _safe_float(env.get("AZURE_RETRY_DELAY_MAX", "10.0"), 10.0),
    }

    # --- Environment viewer (masked) ---
    env_viewer = []
    for key, val in sorted(env.items()):
        is_sensitive = bool(_SENSITIVE_KEYS.search(key))
        env_viewer.append({
            "key": key,
            "value": _mask_value(val) if is_sensitive else val,
            "is_sensitive": is_sensitive,
        })

    return {
        "current_provider": current_provider,
        "providers": provider_cards,
        "fallback_chain": fallback_chain,
        "provider_health": provider_health,
        "moderation": moderation,
        "bot": bot,
        "env": env_viewer,
    }


@router.post("/provider/{provider_id}")
async def update_provider(
    provider_id: str,
    req: ProviderUpdate,
    request: Request,
    user: dict = Depends(require_admin),
):
    """Update a single provider's API key, model, or endpoint.

    Persists via ModelSelector.update_settings() and .env via _env_write_key().
    """
    if provider_id not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_id}")

    pconf = PROVIDERS[provider_id]
    env = _read_env()
    changes: dict[str, str] = {}
    old_values: dict[str, str] = {}

    # API key — write to the first env key name
    if req.api_key is not None and req.api_key.strip():
        key_var = pconf["key_envs"][0]
        old_values[key_var] = env.get(key_var, "")
        env[key_var] = req.api_key.strip()
        changes[key_var] = req.api_key.strip()
        os.environ[key_var] = req.api_key.strip()
        _env_write_key(key_var, req.api_key.strip())

    # API base
    if req.api_base is not None and pconf.get("api_base_env"):
        base_env = pconf["api_base_env"]
        old_values[base_env] = env.get(base_env, "")
        env[base_env] = req.api_base.strip()
        changes[base_env] = req.api_base.strip()
        os.environ[base_env] = req.api_base.strip()
        _env_write_key(base_env, req.api_base.strip())

    # Update ModelSelector in-memory state
    selector = _get_model_selector(request)
    if selector is not None:
        selector_kwargs: dict = {}
        if req.model is not None:
            selector_kwargs["model"] = req.model.strip()
        if provider_id:
            selector_kwargs["provider"] = provider_id
        if selector_kwargs:
            selector.update_settings(**selector_kwargs)
            selector.refresh_api_keys()

    # Hot-reload the running LLM instance so changes take effect immediately
    _reload_active_llm()

    _audit(request, user, f"update_provider_{provider_id}", json.dumps(old_values), json.dumps(changes))
    return {"status": "success", "provider": provider_id, "changes": list(changes.keys())}


@router.post("/provider/{provider_id}/test")
async def test_provider(
    provider_id: str,
    request: Request,
    user: dict = Depends(require_admin),
):
    """Test a provider's API key via ModelSelector.test_provider() with 60 s caching."""
    if provider_id not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_id}")

    cached = _get_cached_test_result(provider_id)
    if cached is not None:
        return cached

    selector = _get_model_selector(request)
    if selector is None:
        raise HTTPException(status_code=503, detail="ModelSelector not initialised")

    t0 = time.time()
    result = await asyncio.to_thread(selector.test_provider, provider_id)
    latency_ms = int((time.time() - t0) * 1000)
    result["latency_ms"] = latency_ms
    _set_cached_test_result(provider_id, result)
    return result


@router.post("/fallback-chain")
async def update_fallback_chain(
    req: FallbackChainUpdate,
    request: Request,
    user: dict = Depends(require_admin),
):
    """Update the fallback chain. Validates no duplicates and all valid providers."""
    if not req.chain:
        raise HTTPException(status_code=400, detail="Chain must not be empty")

    # Validate all provider IDs
    for pid in req.chain:
        if pid not in PROVIDERS:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {pid}")

    # Check for duplicates
    if len(req.chain) != len(set(req.chain)):
        raise HTTPException(status_code=400, detail="Duplicate providers in chain")

    primary = req.chain[0]
    fallback = req.chain[1] if len(req.chain) > 1 else primary

    # Update .env
    _env_write_key("AZURE_LLM_PROVIDER", primary)
    os.environ["AZURE_LLM_PROVIDER"] = primary

    # Update ModelSelector in-memory
    selector = _get_model_selector(request)
    if selector is not None:
        selector.update_settings(
            smart_mode=False,
            provider=primary,
            model=selector.get_recommended_model(primary),
            fallback_provider=fallback,
        )
        selector.refresh_api_keys()

    # Hot-reload the running LLM instance so changes take effect immediately
    _reload_active_llm()

    _audit(request, user, "update_fallback_chain", req.chain[0], ",".join(req.chain))
    return {"status": "success", "chain": req.chain}


@router.post("/moderation")
async def update_moderation(
    req: ModerationSettingsUpdate,
    request: Request,
    user: dict = Depends(require_admin),
):
    """Update moderation settings."""
    env = _read_env()
    changes: dict[str, str] = {}

    mapping = {
        "phase": ("AZURE_MODERATION_PHASE", req.phase),
        "confirmation_mode": ("AZURE_CONFIRMATION_MODE", req.confirmation_mode),
        "confirmation_threshold": ("AZURE_CONFIRMATION_THRESHOLD",
                                   str(req.confirmation_threshold) if req.confirmation_threshold is not None else None),
        "cognitive_mode": ("AZURE_COGNITIVE_MODE",
                           str(req.cognitive_mode) if req.cognitive_mode is not None else None),
        "semantic_threshold": ("AZURE_SEMANTIC_THRESHOLD",
                               str(req.semantic_threshold) if req.semantic_threshold is not None else None),
    }

    for _field, (env_key, val) in mapping.items():
        if val is not None:
            changes[env_key] = val

    old_values = {k: env.get(k, "") for k in changes}

    for _field, (env_key, val) in mapping.items():
        if val is not None:
            env[env_key] = val

    if changes:
        _write_env(env)
        _apply_to_environ(changes)
        _audit(request, user, "update_moderation", json.dumps(old_values), json.dumps(changes))

    # Also update in-memory moderation phase if agent is available
    agent = getattr(request.app.state, "agent", None)
    if agent and req.phase and agent.moderation:
        with contextlib.suppress(Exception):
            agent.set_moderation_phase(req.phase)

    return {"status": "success", "changes": list(changes.keys())}


@router.post("/bot")
async def update_bot_settings(
    req: BotSettingsUpdate,
    request: Request,
    user: dict = Depends(require_admin),
):
    """Update bot configuration settings."""
    env = _read_env()
    changes: dict[str, str] = {}

    mapping = {
        "default_max_tokens": "AZURE_DEFAULT_MAX_TOKENS",
        "default_temperature": "AZURE_DEFAULT_TEMPERATURE",
        "command_cooldown": "AZURE_COMMAND_COOLDOWN",
        "rate_limit_window": "AZURE_RATE_LIMIT_WINDOW",
        "rate_limit_max": "AZURE_RATE_LIMIT_MAX",
        "rate_limit_cooldown": "AZURE_RATE_LIMIT_COOLDOWN",
        "response_cache_size": "AZURE_RESPONSE_CACHE_SIZE",
        "response_cache_ttl": "AZURE_RESPONSE_CACHE_TTL",
        "context_memory_size": "AZURE_CONTEXT_MEMORY_SIZE",
        "context_memory_max_users": "AZURE_CONTEXT_MEMORY_MAX_USERS",
        "max_retries": "AZURE_MAX_RETRIES",
        "retry_delay_base": "AZURE_RETRY_DELAY_BASE",
        "retry_delay_max": "AZURE_RETRY_DELAY_MAX",
    }

    for field, env_key in mapping.items():
        val = getattr(req, field, None)
        if val is not None:
            changes[env_key] = str(val)

    old_values = {k: env.get(k, "") for k in changes}

    for field, env_key in mapping.items():
        val = getattr(req, field, None)
        if val is not None:
            env[env_key] = str(val)

    if changes:
        _write_env(env)
        _apply_to_environ(changes)
        _audit(request, user, "update_bot_settings", json.dumps(old_values), json.dumps(changes))

    return {"status": "success", "changes": list(changes.keys())}


@router.post("/env")
async def update_env_var(
    req: EnvUpdate,
    request: Request,
    user: dict = Depends(require_admin),
):
    """Update a single .env variable."""
    key = req.key.strip()
    if not key or "=" in key:
        raise HTTPException(status_code=400, detail="Invalid environment variable name")

    # Block system-critical env vars
    _blocked_env_keys = {"PATH", "PYTHONPATH", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
                         "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PSModulePath"}
    if key.upper() in _blocked_env_keys:
        raise HTTPException(status_code=400, detail=f"Cannot modify system variable: {key}")

    env = _read_env()
    old = env.get(key, "")
    env[key] = req.value
    _write_env(env)
    _apply_to_environ({key: req.value})
    _audit(request, user, f"update_env_{key}", old, req.value)

    return {"status": "success", "key": key}


@router.post("/save-all")
async def save_all_settings(
    request: Request,
    user: dict = Depends(require_admin),
):
    """Persist current in-memory changes to .env. Called after bulk edits."""
    # Start from what's on disk, then overlay the live in-memory values from
    # os.environ for every key .env already tracks. Reading .env and writing it
    # straight back would persist nothing and silently revert in-memory edits.
    env = _read_env()
    for key in list(env.keys()):
        if key in os.environ:
            env[key] = os.environ[key]
    _write_env(env)
    _audit(request, user, "save_all_settings", "", "persisted")
    return {"status": "success", "message": "Settings persisted to .env"}


@router.post("/reset")
async def reset_settings(
    request: Request,
    user: dict = Depends(require_admin),
):
    """Reload settings from .env (discard unsaved changes)."""
    env = _read_env()
    _apply_to_environ(env)
    _audit(request, user, "reset_settings", "", "reloaded")
    return {"status": "success", "message": "Settings reloaded from .env"}


@router.get("/catalog")
async def get_model_catalog(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Return the full model catalog from azure.model_catalog.py."""
    from azure.model_catalog import PROVIDER_CATALOGS

    catalog: dict[str, list] = {}
    for provider_id, cat in PROVIDER_CATALOGS.items():
        catalog[provider_id] = [
            {
                "id": m.id,
                "name": m.name,
                "context_window": m.context_window,
                "input_price": m.input_price,
                "output_price": m.output_price,
                "free_tier": m.free_tier,
                "max_output": m.max_output,
                "description": m.description,
            }
            for m in cat["models"]
        ]
    return {"catalog": catalog}


@router.get("/active")
async def get_active_model_info(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Return current provider, model, fallback chain, and smart mode from ModelSelector."""
    selector = _get_model_selector(request)

    if selector is None:
        env = _read_env()
        current_provider = env.get("AZURE_LLM_PROVIDER", "openrouter")
        return {
            "provider": current_provider,
            "model": env.get("AZURE_OPENAI_MODEL", "gpt-4o-mini"),
            "fallback_chain": _parse_fallback_chain(env),
            "smart_mode": False,
            "active_config": None,
            "health": None,
        }

    settings = selector.get_settings()
    active_config = selector.get_active_config()
    health = selector.get_provider_health()

    fallback_chain = _parse_fallback_chain(_read_env())
    fallback_provider = settings.get("fallback_provider", "")
    if fallback_provider and fallback_provider not in fallback_chain:
        fallback_chain.append(fallback_provider)

    return {
        "provider": active_config.get("provider", settings.get("provider", "")),
        "model": active_config.get("model", settings.get("model", "")),
        "fallback_chain": fallback_chain,
        "smart_mode": settings.get("smart_mode", False),
        "active_config": active_config,
        "health": health,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_fallback_chain(env: dict[str, str]) -> list[str]:
    """Build fallback chain from current provider config."""
    primary = env.get("AZURE_LLM_PROVIDER", "openrouter")
    chain = [primary]
    # Add remaining providers in detection order
    detect_order = ["openai", "anthropic", "google", "groq", "mistral", "openrouter", "nararouter"]
    for p in detect_order:
        if p not in chain:
            chain.append(p)
    return chain


def _get_system_prompt() -> str:
    """Read the default system prompt from the bot."""
    try:
        from azure.api_llm import ApiLLM
        return ApiLLM.__init__.__kwdefaults__.get("system_prompt", "") or ""
    except Exception:
        return ""


def _apply_to_environ(changes: dict[str, str]) -> None:
    """Write changes to os.environ so they take effect immediately."""
    for k, v in changes.items():
        os.environ[k] = v


def _audit(request: Request, user: dict, action: str, old: str, new: str) -> None:
    """Best-effort audit log write."""
    db = getattr(request.app.state, "db", None)
    if not db:
        return

    def _write():
        try:
            with db._wlock:
                conn = db._get_connection()
                cursor = conn.cursor()
                import time as _time
                cursor.execute(
                    "INSERT INTO audit_logs (timestamp, user_name, discord_id, ip_address, "
                    "session_id, action, old_value, new_value, reason, subsystem) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_time.time(), user.get("username", "admin"), "web", "", "",
                     action, old, new, "dashboard settings change", "web_settings"),
                )
                conn.commit()
        except Exception as e:
            logger.debug("audit log write failed: %s", e)

    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(_audit_executor, _write)
    except Exception:
        logger.exception("[api_settings] audit log write failed")
