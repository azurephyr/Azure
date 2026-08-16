"""
Azure API-Backed LLM — Cloud Intelligence with Local Fallback

Provides a unified interface compatible with LocalLLM/SubprocessLLM but
backed by cloud APIs.

Supported providers (set AZURE_LLM_PROVIDER=<id> in .env to force one):
    - openai       — OpenAI GPT-4o, GPT-4o-mini, o1-preview, GPT-4-turbo, ...
                     Keys tried in order: AZURE_OPENAI_API_KEY, OPENAI_API_KEY
                     Models env: AZURE_OPENAI_MODEL
                     Custom endpoint: AZURE_OPENAI_API_BASE  (OpenAI-compatible servers)

    - anthropic    — Anthropic Claude 4 / 3.5 Sonnet/Haiku/Opus
                     Keys: AZURE_ANTHROPIC_API_KEY, ANTHROPIC_API_KEY
                     Models: AZURE_ANTHROPIC_MODEL
                     Custom endpoint: AZURE_ANTHROPIC_API_BASE

    - google       — Google Gemini (gemini-2.0-flash, gemini-1.5-pro, ...)
                     Keys: AZURE_GOOGLE_API_KEY, GEMINI_API_KEY, GOOGLE_API_KEY
                     Models: AZURE_GOOGLE_MODEL
                     Custom endpoint: AZURE_GOOGLE_API_BASE

    - groq         — Groq (Llama-3.x, Mixtral, Gemma — fast OpenAI-compatible)
                     Keys: AZURE_GROQ_API_KEY, GROQ_API_KEY
                     Models: AZURE_GROQ_MODEL

    - mistral      — Mistral La Plateforme (mistral-large, mistral-medium, ...)
                     Keys: AZURE_MISTRAL_API_KEY, MISTRAL_API_KEY
                     Models: AZURE_MISTRAL_MODEL

    - openrouter   — OpenRouter (unified gateway to many models)
                     Keys: AZURE_OPENROUTER_API_KEY, OPENROUTER_API_KEY
                     Models: AZURE_OPENROUTER_MODEL
                     (e.g. "anthropic/claude-3.5-sonnet", "google/gemini-2.0-flash")

    - nararouter     — NaraRouter (OpenAI-compatible gateway)
                     Keys: AZURE_NARAROUTER_API_KEY, NARAROUTER_API_KEY
                     Models: AZURE_NARAROUTER_MODEL
                     Custom endpoint: AZURE_NARAROUTER_API_BASE

When no provider is forced via AZURE_LLM_PROVIDER, the auto-detector returns
the first provider with a known API key in the environment. Build order is
`openai > anthropic > google > groq > mistral > openrouter > nararouter`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger("azure.api_llm")

# Global rate limiter — max concurrent API calls across all providers/instances.
# Prevents multiple Discord users from overwhelming the API simultaneously.
_API_SEMAPHORE = threading.Semaphore(2)
_API_CALL_TIMEOUT = int(os.environ.get("AZURE_API_CALL_TIMEOUT", "60"))


class ProviderRequestError(RuntimeError):
    """An API request failed with a provider status that callers can classify."""

    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


def _first_env(*names: str, default: str = "") -> str:
    """Return the first env var in `names` that is set."""
    for n in names:
        v = os.environ.get(n)
        if v and v.strip():
            return v.strip()
    return default


class ApiLLM:
    """
    Cloud API-backed LLM with a unified interface matching LocalLLM.

    Resolution order:
      1. Explicit `provider` / `model` / `api_key` kwargs.
      2. AZURE_LLM_PROVIDER env (force a provider).
      3. Auto-detect from env keys.

    Resolution for env-key fallbacks:
      - openai:    AZURE_OPENAI_API_KEY > OPENAI_API_KEY
      - anthropic: AZURE_ANTHROPIC_API_KEY > ANTHROPIC_API_KEY
      - google:    AZURE_GOOGLE_API_KEY > GEMINI_API_KEY > GOOGLE_API_KEY
      - groq:      AZURE_GROQ_API_KEY > GROQ_API_KEY
      - mistral:   AZURE_MISTRAL_API_KEY > MISTRAL_API_KEY
      - openrouter: AZURE_OPENROUTER_API_KEY > OPENROUTER_API_KEY
    """

    PROVIDER_CONFIGS: dict[str, dict] = {
        "openai": {
            "default_model": "gpt-4o-mini",
            "api_base": "https://api.openai.com/v1",
            "env_key_names": ("AZURE_OPENAI_API_KEY", "OPENAI_API_KEY"),
            "model_env_names": ("AZURE_OPENAI_MODEL",),
            "api_base_env_names": ("AZURE_OPENAI_API_BASE",),
            "protocol": "openai",
        },
        "anthropic": {
            "default_model": "claude-sonnet-4-20250514",
            "api_base": "https://api.anthropic.com/v1",
            "env_key_names": ("AZURE_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
            "model_env_names": ("AZURE_ANTHROPIC_MODEL",),
            "api_base_env_names": ("AZURE_ANTHROPIC_API_BASE",),
            "protocol": "anthropic",
        },
        "google": {
            "default_model": "gemini-3.1-flash-lite",
            "api_base": "https://generativelanguage.googleapis.com/v1beta",
            "env_key_names": (
                "AZURE_GOOGLE_API_KEY",
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
            ),
            "model_env_names": ("AZURE_GOOGLE_MODEL",),
            "fallback_model_env_names": ("AZURE_GOOGLE_FALLBACK_MODEL",),
            "api_base_env_names": ("AZURE_GOOGLE_API_BASE",),
            "protocol": "google",
        },
        "groq": {
            "default_model": "llama-3.3-70b-versatile",
            "api_base": "https://api.groq.com/openai/v1",
            "env_key_names": ("AZURE_GROQ_API_KEY", "GROQ_API_KEY"),
            "model_env_names": ("AZURE_GROQ_MODEL",),
            "api_base_env_names": ("AZURE_GROQ_API_BASE",),
            "protocol": "openai",
        },
        "mistral": {
            "default_model": "mistral-large-latest",
            "api_base": "https://api.mistral.ai/v1",
            "env_key_names": ("AZURE_MISTRAL_API_KEY", "MISTRAL_API_KEY"),
            "model_env_names": ("AZURE_MISTRAL_MODEL",),
            "api_base_env_names": ("AZURE_MISTRAL_API_BASE",),
            "protocol": "openai",
        },
        "openrouter": {
            "default_model": "openai/gpt-4o-mini",
            "api_base": "https://openrouter.ai/api/v1",
            "env_key_names": (
                "AZURE_OPENROUTER_API_KEY",
                "OPENROUTER_API_KEY",
            ),
            "model_env_names": ("AZURE_OPENROUTER_MODEL",),
            "fallback_model_env_names": ("AZURE_OPENROUTER_FALLBACK_MODEL",),
            "api_base_env_names": ("AZURE_OPENROUTER_API_BASE",),
            "protocol": "openai",
        },
        "nararouter": {
            "default_model": "agnes-2.5-flash",
            "api_base": "https://router.bynara.id/v1",
            "env_key_names": (
                "AZURE_NARAROUTER_API_KEY",
                "NARAROUTER_API_KEY",
            ),
            "model_env_names": ("AZURE_NARAROUTER_MODEL",),
            "fallback_model_env_names": ("AZURE_NARAROUTER_FALLBACK_MODEL",),
            "api_base_env_names": ("AZURE_NARAROUTER_API_BASE",),
            "protocol": "openai",
        },
    }

    # Order in which auto-detection scans env keys.
    _DETECT_ORDER = ("openai", "anthropic", "google", "groq", "mistral", "openrouter", "nararouter")

    # Class-level ModelSelector reference (set by bot after init)
    _model_selector = None
    # Class-level reference to the active ApiLLM instance used by the agent.
    # Settings handler calls reload_from_selector() on this after changes.
    _active_llm: ApiLLM | None = None

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        system_prompt: str | None = None,
        n_ctx: int = 8192,
        **kwargs,
    ):
        """
        Args:
            provider: One of PROVIDER_CONFIGS keys. Auto-detected if None.
            model: Specific model name. Provider-specific default if None.
            api_key: API key. Falls back to env var.
            temperature: Sampling temperature (0.0-2.0).
            max_tokens: Max tokens per response.
            system_prompt: Default system prompt prepended to conversations.
            n_ctx: Context window size (compat with LocalLLM interface).
        """
        self.temperature = max(0.0, min(2.0, temperature))
        self.max_tokens = max_tokens
        self.n_ctx = n_ctx
        self.system_prompt = system_prompt or (
            "You are Azure, a composed and exceptionally capable technical aide "
            "for a Discord server. Be precise, calm, observant, and concise. "
            "Anticipate useful next steps, surface risks, never use filler, "
            "never pretend certainty, and report actions honestly."
        )
        self._loaded = False
        self._provider = (provider or "").lower().strip() or None
        self._model = model
        self._api_key = api_key
        self._invocations = 0
        self._total_tokens = 0
        self._last_model_used = None

        # Force-via-env: AZURE_LLM_PROVIDER overrides the constructor argument
        # if explicit kwargs aren't passed. This lets .env switch providers
        # without touching code.
        forced = os.environ.get("AZURE_LLM_PROVIDER", "").strip().lower()
        if not self._provider and forced:
            self._provider = forced
        if self._provider and self._provider not in self.PROVIDER_CONFIGS:
            raise RuntimeError(
                f"Unknown provider '{self._provider}'. Supported: "
                f"{sorted(self.PROVIDER_CONFIGS)}"
            )

        # Auto-detect if still not set
        if not self._provider:
            detected = self._detect_provider()
            if detected is None:
                raise RuntimeError(
                    "No API provider configured. Set AZURE_LLM_PROVIDER in "
                    ".env (one of: " + ", ".join(self._DETECT_ORDER) +
                    ") and the corresponding API key.\n"
                    "  Examples:\n"
                    "    AZURE_LLM_PROVIDER=nararouter\n"
                    "    NARAROUTER_API_KEY=your-key\n"
                    "Or specify via kwargs: ApiLLM(provider='nararouter', api_key='...')"
                )
            self._provider = detected

        config = self.PROVIDER_CONFIGS[self._provider]

        # Resolve API key (kwarg > env)
        if not self._api_key:
            self._api_key = _first_env(*config["env_key_names"])
        if not self._api_key:
            raise RuntimeError(
                f"API key not found for provider '{self._provider}'. Set one of: "
                + ", ".join(config["env_key_names"])
                + " in your environment or .env file."
            )

        # Resolve model (kwarg > env > default)
        if not self._model:
            self._model = _first_env(*config["model_env_names"]) or config["default_model"]

        # Resolve fallback model (env only — optional)
        fb_env_names = config.get("fallback_model_env_names", ())
        self._fallback_model = _first_env(*fb_env_names) if fb_env_names else None

        # Resolve API base (env > default)
        self._api_base = _first_env(*config["api_base_env_names"]) or config["api_base"]
        # Trailing-slash safety:
        self._api_base = self._api_base.rstrip("/")

        # Protocol tag dispatched on in chat()
        self._protocol = config["protocol"]
        self._loaded = True

        logger.info(
            "[api_llm] provider=%s model=%s fallback=%s protocol=%s base=%s",
            self._provider, self._model, self._fallback_model or "none",
            self._protocol, self._api_base,
        )

    @classmethod
    def _detect_provider(cls) -> str | None:
        """Auto-detect which API provider has a key configured (in priority order).

        If AZURE_LLM_PROVIDER is explicitly set, use that provider directly
        instead of scanning — this lets users pin a specific provider when
        multiple API keys are present in .env.
        """
        explicit = os.environ.get("AZURE_LLM_PROVIDER", "").strip().lower()
        if explicit and explicit in cls.PROVIDER_CONFIGS:
            cfg = cls.PROVIDER_CONFIGS[explicit]
            for env_name in cfg["env_key_names"]:
                v = os.environ.get(env_name)
                if v and v.strip():
                    return explicit
        for provider in cls._DETECT_ORDER:
            cfg = cls.PROVIDER_CONFIGS[provider]
            for env_name in cfg["env_key_names"]:
                v = os.environ.get(env_name)
                if v and v.strip():
                    return provider
        return None

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def reload_from_selector(self) -> bool:
        """Reload provider/model from ModelSelector if available.

        Uses the explicit settings (not smart_mode evaluated config) so that
        a user's /provider or /model choice isn't overridden by health-based
        provider selection.

        Returns True if config was updated.
        """
        selector = ApiLLM._model_selector
        if selector is None:
            return False
        try:
            settings = selector.get_settings()
            new_provider = settings.get("provider", "")
            new_model = settings.get("model", "")
            if new_provider and new_provider != self._provider:
                if new_provider not in self.PROVIDER_CONFIGS:
                    return False
                # Check that the new provider actually has an API key
                # before switching — don't clear the old key if we can't
                # resolve a new one.
                new_key = self._resolve_key_from_env(new_provider)
                if not new_key:
                    logger.warning(
                        "[api_llm] skipping provider switch to %s — no API key in env",
                        new_provider,
                    )
                    return False
                self._provider = new_provider
                self._api_key = new_key
                cfg = self.PROVIDER_CONFIGS[new_provider]
                api_base = ""
                for env_var in cfg.get("api_base_env_names", ()):
                    val = os.environ.get(env_var, "").strip()
                    if val:
                        api_base = val
                        break
                self._api_base = api_base or cfg["api_base"]
                self._protocol = cfg["protocol"]
                self._fallback_model = os.environ.get(
                    cfg.get("fallback_model_env_names", ("",))[0] if cfg.get("fallback_model_env_names") else "",
                    None,
                )
                logger.info("[api_llm] reloaded provider to %s", new_provider)
            if new_model and new_model != self._model:
                self._model = new_model
                logger.info("[api_llm] reloaded model to %s", new_model)
            return True
        except Exception as e:
            logger.debug("[api_llm] reload_from_selector failed: %s", e)
            return False

    def _resolve_key(self, provider: str) -> str:
        if self._api_key:
            return self._api_key
        return self._resolve_key_from_env(provider)

    @staticmethod
    def _resolve_key_from_env(provider: str) -> str:
        """Resolve API key from env vars for a provider (ignores instance state)."""
        cfg = ApiLLM.PROVIDER_CONFIGS.get(provider, {})
        for env_name in cfg.get("env_key_names", ()):
            v = os.environ.get(env_name, "").strip()
            if v:
                return v
        return ""

    # ------------------------------------------------------------------
    # Public API (matches LocalLLM interface)
    # ------------------------------------------------------------------

    def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        """Chat completion. Matches LocalLLM.chat() interface."""
        temp = kwargs.get("temperature", self.temperature)
        max_tok = kwargs.get("max_tokens", self.max_tokens)
        response_mime_type = kwargs.get("response_mime_type")

        start = time.time()
        self._invocations += 1
        # Do not report a model from an earlier successful request when this
        # request fails or falls through to another provider path.
        self._last_model_used = None

        try:
            if self._protocol == "openai":
                text = self._chat_openai(messages, temp, max_tok)
            elif self._protocol == "anthropic":
                text = self._chat_anthropic(messages, temp, max_tok)
            elif self._protocol == "google":
                text = self._strip_thinking(
                    self._chat_google(messages, temp, max_tok,
                                      response_mime_type=response_mime_type)
                )
            else:
                raise RuntimeError(f"Unknown protocol: {self._protocol}")
        except Exception as e:
            elapsed = time.time() - start
            logger.info("[api_llm] error after %.1fs: %s", elapsed, e)
            raise

        elapsed = time.time() - start
        est_tokens = max(1, int(len(text.split()) * 1.3))
        speed = est_tokens / elapsed if elapsed > 0 else 0
        self._total_tokens += est_tokens
        logger.info(
            "[api_llm] %s/%s: %d chars in %.1fs (~%.1f tok/s)",
            self._provider, getattr(self, "_last_model_used", None) or self._model,
            len(text), elapsed, speed,
        )
        return text

    def generate(self, prompt: str, **kwargs) -> str:
        """Raw text generation (not chat format)."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def count_tokens(self, text: str) -> int:
        """Rough token estimate."""
        return len(text) // 4

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Strip chain-of-thought reasoning from Gemma model output.

        Gemma outputs reasoning as bullet points before the actual answer.
        Only strips if reasoning pattern is detected (blank line separating
        reasoning from answer). Preserves valid markdown in responses.
        """
        if not text:
            return text
        text = text.strip()
        lines = text.split("\n")

        # If response is short, it's likely already clean
        if len(text) < 100:
            return text

        # Gemma pattern: a leading block of bullet-point reasoning, a blank
        # line, then the actual answer. Split on the FIRST blank line and only
        # strip the leading block if it genuinely looks like reasoning bullets.
        # Using the last blank line (previous behavior) truncated any normal
        # multi-paragraph answer down to just its final paragraph.
        first_blank = -1
        for i, line in enumerate(lines):
            if line.strip() == "":
                first_blank = i
                break

        if 0 < first_blank < len(lines) - 1:
            head_lines = [ln.strip() for ln in lines[:first_blank] if ln.strip()]
            is_reasoning = bool(head_lines) and all(
                ln.startswith(("- ", "* ", "•")) or ln[0:2].rstrip(".").isdigit()
                for ln in head_lines
            )
            if is_reasoning:
                after_blank = "\n".join(lines[first_blank + 1:]).strip()
                if after_blank and len(after_blank) > 5:
                    return after_blank

        # No reasoning pattern detected — return as-is (valid markdown)
        return text

    def get_info(self) -> dict:
        """Return model info."""
        return {
            "provider": self._provider,
            "model": self._model,
            "last_model_used": getattr(self, "_last_model_used", None) or self._model,
            "protocol": self._protocol,
            "api_base": self._api_base,
            "loaded": self._loaded,
            "n_ctx": self.n_ctx,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "invocations": self._invocations,
            "total_estimated_tokens": self._total_tokens,
        }

    # ------------------------------------------------------------------
    # OpenAI-compatible protocol (OpenAI, Groq, Mistral, OpenRouter)
    # ------------------------------------------------------------------

    def _chat_openai(self, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> str:
        """OpenAI Chat Completions API (also Groq/Mistral/OpenRouter)."""
        try:
            return self._chat_openai_model(messages, temperature, max_tokens, self._model)
        except ProviderRequestError as primary_error:
            # Authentication, billing, and permission failures cannot be
            # repaired by trying another model on the same provider.
            if primary_error.status_code in {401, 402, 403}:
                raise
            fallback = (self._fallback_model or "").strip()
            if not fallback or fallback == self._model:
                raise
            logger.warning(
                "[api_llm] primary model %s failed; trying fallback model %s: %s",
                self._model, fallback, primary_error,
            )
            return self._chat_openai_model(messages, temperature, max_tokens, fallback)
        except Exception as primary_error:
            fallback = (self._fallback_model or "").strip()
            if not fallback or fallback == self._model:
                raise
            logger.warning(
                "[api_llm] primary model %s failed; trying fallback model %s: %s",
                self._model, fallback, primary_error,
            )
            return self._chat_openai_model(messages, temperature, max_tokens, fallback)

    def _chat_openai_model(self, messages: list[dict[str, str]], temperature: float,
                           max_tokens: int, model: str) -> str:
        """Make one OpenAI-compatible request for a specific model."""
        url = f"{self._api_base}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        # Add OpenRouter-tracking headers (required to identify the bot).
        if self._provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/azure-ai/bot"
            headers["X-Title"] = "Azure Discord Bot"

        # Inject system prompt if not already present
        if messages and messages[0].get("role") != "system":
            messages = [{"role": "system", "content": self.system_prompt}] + messages

        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Some OpenAI-compatible servers (e.g., newer o1 models) require
        # using `max_completion_tokens` instead of `max_tokens`.
        model_lower = model.lower()
        if model_lower.startswith("o1") or model_lower.startswith("o3") or model_lower.startswith("o4"):
            payload["max_completion_tokens"] = max_tokens
            payload.pop("max_tokens", None)
            # o1 family ignores `temperature` in chat — leave it set anyway.

        data = self._http_request(
            url,
            headers,
            payload,
            max_retries=max(1, int(os.environ.get("AZURE_API_RETRIES", "2"))),
        )
        try:
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("message.content is not a string")
            self._last_model_used = model
            return content.strip()
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(
                f"OpenAI-protocol returned unexpected shape: {data!r}"
            ) from e

    # ------------------------------------------------------------------
    # Anthropic API
    # ------------------------------------------------------------------

    def _chat_anthropic(self, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> str:
        """Anthropic Messages API."""
        url = f"{self._api_base}/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
        }

        # Anthropic uses separate system parameter
        system_text = self.system_prompt
        chat_messages = []
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "system":
                system_text = system_text + "\n\n" + content if system_text else content
            else:
                chat_messages.append({"role": role, "content": content})

        payload = {
            "model": self._model,
            "messages": chat_messages,
            "system": system_text,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        data = self._http_request(url, headers, payload)
        try:
            text = data["content"][0]["text"] or ""
            return text.strip()
        except (KeyError, IndexError) as e:
            raise RuntimeError(
                f"Anthropic returned unexpected shape: {data!r}"
            ) from e

    # ------------------------------------------------------------------
    # Google Gemini API
    # ------------------------------------------------------------------

    def _call_google_model(
        self, model_name: str, messages: list[dict[str, str]],
        temperature: float, max_tokens: int,
        response_mime_type: str | None = None,
    ) -> str:
        """Single Google Gemini generateContent call for a given model."""
        url = f"{self._api_base}/models/{model_name}:generateContent"
        headers = {"Content-Type": "application/json", "X-Goog-Api-Key": self._api_key}

        contents: list[dict] = []
        system_text = self.system_prompt or ""
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system_text = system_text + "\n\n" + content if system_text else content
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})
            else:
                contents.append({"role": "user", "parts": [{"text": content}]})

        is_gemma = "gemma" in model_name.lower()
        payload: dict[str, object] = {"contents": contents}
        gen_config: dict[str, object] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if response_mime_type:
            gen_config["responseMimeType"] = response_mime_type
        payload["generationConfig"] = gen_config
        if system_text and not is_gemma:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}

        data = self._http_request(url, headers, payload)
        candidates = data.get("candidates", [])
        if not candidates:
            feedback = data.get("promptFeedback", {})
            block_reason = feedback.get("blockReason")
            if block_reason:
                raise RuntimeError(f"Gemini blocked the prompt: {block_reason}")
            raise RuntimeError("Gemini returned no candidates")
        try:
            parts = candidates[0]["content"]["parts"]
            if not parts:
                raise RuntimeError("Gemini returned empty parts list")
            text = parts[0]["text"] or ""
            return text.strip()
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Gemini returned unexpected shape: {data!r}") from e

    def _chat_google(self, messages: list[dict[str, str]], temperature: float, max_tokens: int,
                     response_mime_type: str | None = None) -> str:
        """Google Gemini API with automatic fallback model on failure."""
        model_name = (self._model or "").strip().lstrip("/")
        if not model_name:
            raise RuntimeError("Gemini model name is empty; set AZURE_GOOGLE_MODEL.")

        try:
            return self._call_google_model(model_name, messages, temperature, max_tokens,
                                           response_mime_type=response_mime_type)
        except RuntimeError as exc:
            if not self._fallback_model:
                raise
            fb = self._fallback_model.strip()
            if fb == model_name:
                raise
            logger.warning(
                "[api_llm] primary model %s failed (%s), trying fallback %s",
                model_name, exc, fb,
            )
            return self._call_google_model(fb, messages, temperature, max_tokens,
                                           response_mime_type=response_mime_type)

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    def _http_request(self, url: str, headers: dict, payload: dict,
                      max_retries: int = 2) -> dict:
        """Make an HTTP POST request with retry, rate limiting, and backoff.

        Retry policy:
          - 429: parse retry-after, exponential backoff (capped at 30s)
          - 5xx: exponential backoff
          - 4xx (other): no retry
          - Global semaphore limits concurrent calls to 2
        """
        body = json.dumps(payload).encode("utf-8")

        for attempt in range(max_retries):
            acquired = False
            try:
                if not _API_SEMAPHORE.acquire(timeout=10):
                    raise TimeoutError("API semaphore unavailable after 10s")
                acquired = True
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=_API_CALL_TIMEOUT) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                error_body = ""
                with contextlib.suppress(Exception):
                    error_body = e.read().decode("utf-8")[:800]
                if attempt < max_retries - 1 and e.code in (408, 429, 500, 502, 503, 504):
                    # Prefer the provider's standard header, then inspect
                    # provider-specific JSON hints before using backoff.
                    wait = 2 ** attempt
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    if retry_after:
                        with contextlib.suppress(TypeError, ValueError):
                            wait = max(wait, float(retry_after))
                    if e.code == 429:
                        try:
                            err_data = json.loads(error_body)
                            for detail in err_data.get("error", {}).get("details", []):
                                if "retryDelay" in detail:
                                    delay_str = detail["retryDelay"].replace("s", "")
                                    wait = max(wait, float(delay_str))
                                    break
                            # Also check message for "Please retry in Xs"
                            msg = err_data.get("error", {}).get("message", "")
                            if "retry in" in msg.lower():
                                m = re.search(r"retry in (\d+\.?\d*)s", msg)
                                if m:
                                    wait = max(wait, float(m.group(1)))
                        except Exception:
                            logger.exception("[api_llm] retry-delay parse failed")
                    wait = min(wait, 30)  # Cap at 30s
                    logger.warning(
                        "[api_llm] HTTP %s from %s, retrying in %.1fs (attempt %d/%d)",
                        e.code, self._provider, wait, attempt + 1, max_retries,
                    )
                    time.sleep(wait)
                    continue
                logger.error(
                    "[api_llm] HTTP %s from %s — body[:800]=%s",
                    e.code, self._provider, error_body,
                )
                raise ProviderRequestError(
                    f"{self._provider} API request failed (HTTP {e.code})",
                    status_code=e.code,
                ) from e
            except urllib.error.URLError as e:
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "[api_llm] %s connection error, retrying in %ds (attempt %d/%d): %s",
                        self._provider, wait, attempt + 1, max_retries, e.reason,
                    )
                    time.sleep(wait)
                    continue
                raise RuntimeError(
                    f"{self._provider} API connection failed: {e.reason}"
                ) from e
            finally:
                if acquired:
                    with contextlib.suppress(ValueError, TypeError):
                        _API_SEMAPHORE.release()
        raise RuntimeError("HTTP request failed after all retries")


# ---------------------------------------------------------------------------
# HybridLLM — tries API first, falls back to local
# ---------------------------------------------------------------------------

class HybridLLM:
    """
    Wrapper that tries API LLM first, falls back to local LLM on failure.
    Provides the same interface as LocalLLM/SubprocessLLM.
    """

    def __init__(self, api_llm: ApiLLM | None = None, local_llm=None):
        self.api_llm = api_llm
        self.local_llm = local_llm
        self.temperature = (
            api_llm.temperature if api_llm
            else (local_llm.temperature if local_llm else 0.7)
        )
        self.max_tokens = (
            api_llm.max_tokens if api_llm
            else (local_llm.max_tokens if local_llm else 256)
        )
        self.n_ctx = (
            api_llm.n_ctx if api_llm
            else (getattr(local_llm, "n_ctx", 2048) if local_llm else 2048)
        )
        self._loaded = bool(api_llm or local_llm)
        self._last_used = "none"

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        """Try API first, fall back to local."""
        if self.api_llm and self.api_llm.is_loaded:
            try:
                text = self.api_llm.chat(messages, **kwargs)
                self._last_used = "api"
                return text
            except Exception as e:
                logger.error("[hybrid_llm] API failed, falling back to local: %s", e)

        if self.local_llm and hasattr(self.local_llm, 'chat'):
            try:
                text = self.local_llm.chat(messages, **kwargs)
                self._last_used = "local"
                return text
            except Exception as e:
                logger.error("[hybrid_llm] local LLM also failed: %s", e)

        return "[HybridLLM: API and local both failed. Check logs.]"

    def generate(self, prompt: str, **kwargs) -> str:
        """Raw text generation."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def get_info(self) -> dict:
        info = {
            "type": "hybrid",
            "last_used": self._last_used,
            "loaded": self._loaded,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.api_llm:
            info["api"] = self.api_llm.get_info()
        if self.local_llm:
            info["local"] = self.local_llm.get_info()
        return info


def create_api_llm_from_env() -> ApiLLM | None:
    """
    Convenience factory: build an ApiLLM purely from environment variables.

    Useful when the agent bootstrap wants to know whether an API key has been
    configured without constructing the agent first.

    Returns:
        ApiLLM instance if any provider is detected/configured, else None.
    """
    try:
        return ApiLLM()
    except RuntimeError as e:
        logger.debug("[api_llm] not configured: %s", e)
        return None
