"""
Azure Discord Bot - Configuration Constants

This module defines all magic numbers, default values, and configuration
constants used throughout the bot. Centralizing these values makes the
codebase more maintainable and easier to configure.
"""

from __future__ import annotations

import math
import os
from typing import Final

# =============================================================================
# LLM Configuration
# =============================================================================

# Token estimation: rough conversion ratio for text -> tokens
TOKEN_ESTIMATION_RATIO: Final[float] = 4.0
"""Approximate characters per token (e.g., 'hello' = ~1.25 tokens)"""

# Default LLM parameters
DEFAULT_LLM_TEMPERATURE: Final[float] = 0.7
"""Default sampling temperature (0.0 = deterministic, 1.0 = creative)"""

DEFAULT_LLM_MAX_TOKENS: Final[int] = 512
"""Default maximum tokens to generate per response"""

DEFAULT_LLM_TOP_P: Final[float] = 0.9
"""Default nucleus sampling threshold"""

DEFAULT_LLM_CONTEXT_WINDOW: Final[int] = 2048
"""Default context window size (tokens)"""

# LLM subprocess configuration
LLM_BATCH_SIZE: Final[int] = 256
"""Batch size for LLM inference (smaller = faster loading on CPU)"""

LLM_STARTUP_TIMEOUT_SECONDS: Final[int] = 60
"""Maximum time to wait for LLM model to load (seconds)"""

LLM_INFERENCE_TIMEOUT_SECONDS: Final[int] = 600
"""Maximum time to wait for LLM response (10 minutes - effectively unlimited)"""

LLM_SUBPROCESS_SHUTDOWN_TIMEOUT: Final[int] = 5
"""Time to wait for graceful LLM subprocess shutdown (seconds)"""

# =============================================================================
# Discord Bot Configuration
# =============================================================================

# Typing indicator
TYPING_INDICATOR_REFRESH_INTERVAL: Final[int] = 5
"""How often to refresh Discord typing indicator (seconds)"""

# Rate limiting & cooldowns
DEFAULT_COMMAND_COOLDOWN: Final[int] = 5
"""Default cooldown between commands per user (seconds)"""

DEFAULT_RATE_LIMIT_MAX: Final[int] = 10
"""Maximum messages per rate limit window"""

DEFAULT_RATE_LIMIT_WINDOW: Final[int] = 60
"""Rate limit window duration (seconds)"""

DEFAULT_RATE_LIMIT_COOLDOWN: Final[int] = 30
"""Cooldown duration after hitting rate limit (seconds)"""

# Response caching
DEFAULT_CACHE_SIZE: Final[int] = 100
"""Number of Q&A pairs to cache in memory"""

DEFAULT_CACHE_TTL: Final[int] = 3600
"""Cache entry time-to-live (1 hour in seconds)"""

CACHE_KEY_HASH_LENGTH: Final[int] = 32
"""Length of SHA256 hash for cache keys (bytes)"""

# Context memory
DEFAULT_CONTEXT_MEMORY_SIZE: Final[int] = 10
"""Number of message exchanges to remember per user"""

DEFAULT_CONTEXT_MEMORY_MAX_USERS: Final[int] = 100
"""Maximum number of users to track in context memory"""

DEFAULT_CONTEXT_MEMORY_TTL: Final[int] = 3600
"""Time-to-live for context memory entries (1 hour in seconds)"""

# Reaction controls
DEFAULT_BOT_MESSAGE_CACHE_SIZE: Final[int] = 100
"""Number of bot messages to track for reaction controls"""

DEFAULT_BOT_MESSAGE_TTL: Final[int] = 3600
"""Time-to-live for bot message tracking (1 hour in seconds)"""

REACTION_DELETE: Final[str] = "❌"
"""Reaction emoji for deleting bot messages"""

REACTION_REGENERATE: Final[str] = "🔄"
"""Reaction emoji for regenerating responses"""

REACTION_CACHED: Final[str] = "⚡"
"""Reaction emoji indicating cached response"""

REACTION_COOLDOWN: Final[str] = "⏰"
"""Reaction emoji indicating cooldown active"""

# Error recovery
DEFAULT_MAX_RETRIES: Final[int] = 3
"""Maximum number of retry attempts for failed operations"""

DEFAULT_RETRY_BASE_DELAY: Final[float] = 1.0
"""Base delay for exponential backoff (seconds)"""

DEFAULT_RETRY_MAX_DELAY: Final[float] = 10.0
"""Maximum delay between retries (seconds)"""

# Message truncation
TRUNCATE_SMALL: Final[int] = 80
"""Characters to show in log previews"""

TRUNCATE_MEDIUM: Final[int] = 200
"""Characters to show in error messages"""

TRUNCATE_LARGE: Final[int] = 1000
"""Characters to show in detailed logs"""

# Discord limits
DISCORD_MESSAGE_MAX_LENGTH: Final[int] = 2000
"""Discord's maximum message length (characters)"""

DISCORD_EMBED_MAX_LENGTH: Final[int] = 4096
"""Discord's maximum embed description length (characters)"""

DISCORD_EMBED_FIELD_MAX_LENGTH: Final[int] = 1024
"""Discord's maximum embed field value length (characters)"""

# =============================================================================
# Memory & Storage
# =============================================================================

# Short-term memory
DEFAULT_SHORT_TERM_MEMORY_TURNS: Final[int] = 10
"""Number of conversation turns to keep in short-term memory"""

# RAG configuration
DEFAULT_RAG_MAX_DOCS: Final[int] = 1000
"""Maximum documents to store in RAG"""

DEFAULT_RAG_TOP_K: Final[int] = 3
"""Number of top RAG results to retrieve"""

DEFAULT_RAG_SIMILARITY_THRESHOLD: Final[float] = 0.7
"""Minimum similarity score for RAG retrieval"""

# Database
DEFAULT_DB_PATH: Final[str] = "data/memory.db"
"""Default SQLite database path"""

DEFAULT_HYBRID_RAG_DB_PATH: Final[str] = "data/hybrid_rag.db"
"""Default Hybrid RAG database path"""

# =============================================================================
# Cognitive Pipeline
# =============================================================================

# Timeouts for cognitive operations
COGNITIVE_DECISION_TIMEOUT: Final[int] = 600
"""Timeout for cognitive decision-making (10 minutes)"""

COGNITIVE_PLAN_TIMEOUT: Final[int] = 600
"""Timeout for cognitive planning (10 minutes)"""

# Logging
DEFAULT_LOG_DIR: Final[str] = "logs/cognition"
"""Default directory for cognitive pipeline logs"""

# =============================================================================
# Moderation
# =============================================================================

MODERATION_READINESS_HOURS: Final[int] = 72
"""Hours of data for moderation readiness report"""

# =============================================================================
# Subscription Tiers (Future Feature)
# =============================================================================

# Free tier limits
FREE_TIER_MAX_MESSAGES_PER_HOUR: Final[int] = 5
"""Maximum messages per hour for free tier users"""

FREE_TIER_CONTEXT_SIZE: Final[int] = 5
"""Maximum context memory size for free tier"""

# Premium tier limits
PREMIUM_TIER_MAX_MESSAGES_PER_HOUR: Final[int] = -1  # Unlimited
"""Maximum messages per hour for premium tier (-1 = unlimited)"""

PREMIUM_TIER_CONTEXT_SIZE: Final[int] = 20
"""Maximum context memory size for premium tier"""

PREMIUM_TIER_PRIORITY_RESPONSE_TIME: Final[float] = 2.0
"""Target response time for premium tier (seconds)"""

# Enterprise tier limits
ENTERPRISE_TIER_SLA_UPTIME: Final[float] = 0.999
"""SLA uptime guarantee for enterprise tier (99.9%)"""

# =============================================================================
# Performance Tuning
# =============================================================================

# Thread pool sizing
DEFAULT_CPU_THREAD_RATIO: Final[float] = 0.5
"""Ratio of CPU cores to use for LLM inference (0.5 = 50%)"""

MIN_THREADS: Final[int] = 1
"""Minimum number of threads for LLM inference"""

# Memory management
MEMORY_WARNING_THRESHOLD_MB: Final[int] = 4096
"""Memory usage threshold for warnings (4GB in MB)"""

MEMORY_CRITICAL_THRESHOLD_MB: Final[int] = 6144
"""Memory usage threshold for critical alerts (6GB in MB)"""

# =============================================================================
# Development & Debugging
# =============================================================================

# Verbose logging
DEBUG_MODE: Final[bool] = os.environ.get("AZURE_DEBUG", "0") == "1"
"""Enable debug mode with verbose logging"""

# Feature flags
FEATURE_COGNITIVE_PIPELINE: Final[bool] = os.environ.get("AZURE_COGNITIVE_MODE", "0") == "1"
"""Enable cognitive pipeline (adds 2-5s overhead)"""

FEATURE_WEB_DASHBOARD: Final[bool] = os.environ.get("AZURE_WEB_DASHBOARD", "0") == "1"
"""Enable web dashboard"""

FEATURE_STREAMING_RESPONSES: Final[bool] = os.environ.get("AZURE_STREAMING", "0") == "1"
"""Enable streaming responses"""

# =============================================================================
# Helper Functions
# =============================================================================

def get_env_int(key: str, default: int) -> int:
    """Get integer value from environment variable with fallback.

    Args:
        key: Environment variable name
        default: Default value if not set or invalid

    Returns:
        Integer value from environment or default
    """
    try:
        return int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def get_env_float(key: str, default: float) -> float:
    """Get float value from environment variable with fallback.

    Args:
        key: Environment variable name
        default: Default value if not set or invalid

    Returns:
        Float value from environment or default
    """
    try:
        val = float(os.environ.get(key, str(default)))
        if math.isfinite(val):
            return val
        return default
    except (ValueError, TypeError):
        return default


def get_env_bool(key: str, default: bool) -> bool:
    """Get boolean value from environment variable with fallback.

    Args:
        key: Environment variable name
        default: Default value if not set or invalid

    Returns:
        Boolean value from environment or default
    """
    value = os.environ.get(key, "").lower()
    if value in ("1", "true", "yes", "on"):
        return True
    elif value in ("0", "false", "no", "off"):
        return False
    return default
