"""Typed configuration using Pydantic BaseSettings.

Requires: pip install pydantic-settings
Falls back gracefully to None if not installed.
"""
from __future__ import annotations

try:
    from pathlib import Path

    from pydantic import Field
    from pydantic_settings import BaseSettings

    class BotConfig(BaseSettings):
        """All bot configuration with validation and defaults."""

        # Paths
        root_dir: Path | None = Field(default=None)
        data_dir: Path | None = Field(default=None)

        # Discord
        discord_token: str = ""
        admin_channel_id: str = ""
        guild_id: str = ""
        chat_mode: str = "anyone"
        allowed_users: str = ""

        # LLM
        llm_provider: str = "llama"
        model_path: str = ""
        llm_max_tokens: int = 2048
        llm_temperature: float = 0.7
        api_timeout: int = 90
        n_threads: int = 0

        # Rate limiting
        rate_limit_messages: int = 10
        rate_limit_window: float = 60.0
        rate_limit_cooldown: float = 30.0
        cooldown_seconds: float = 5.0
        rate_limit_cache_size: int = 1000

        # Cache
        cache_max_size: int = 256
        cache_ttl: float = 600.0
        response_cache_size: int = 100
        response_cache_ttl: float = 3600.0

        # Moderation
        moderation_enabled: bool = True
        moderation_phase: str = "dry_run"
        toxicity_threshold: float = 0.7

        # Circuit breaker
        cb_failure_threshold: int = 5
        cb_cooldown_seconds: float = 60.0

        # Cognitive pipeline
        cognitive_mode: bool = True
        semantic_threshold: float = 0.75

        # Context memory
        context_memory_size: int = 10
        context_memory_max_users: int = 100

        # Error recovery
        max_retries: int = 3
        retry_delay_base: float = 1.0
        retry_delay_max: float = 10.0

        # Cooldown cache
        cooldown_cache_size: int = 1000

        # Bot message tracking
        bot_message_cache_size: int = 100
        bot_message_ttl: float = 3600.0

        # Display
        chunk_size: int = 1900
        delete_after_seconds: int = 10
        default_max_tokens: int = 150
        default_temperature: float = 0.7

        # Health / web server
        health_port: int = 8088
        web_port: int = 8080

        # Welcome
        welcome_lookback_hours: int = 1

        model_config = {"env_prefix": "AZURE_", "env_file": ".env", "extra": "ignore"}

    # Singleton - import and use: from .pydantic_config import config
    config = BotConfig()

except ImportError:
    # pydantic-settings not installed — config is unavailable
    config = None
