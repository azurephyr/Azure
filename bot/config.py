"""
Shared configuration constants for the bot package.

Both discord_bot_v1.py and handlers/ import from here to avoid duplication.
"""

import os
import threading
from collections import OrderedDict

# Rate limiting
_rate_limit_buckets: OrderedDict = OrderedDict()
_rate_limit_lock = threading.Lock()
MAX_RATE_LIMIT_ENTRIES = int(os.environ.get("AZURE_RATE_LIMIT_CACHE_SIZE", "1000"))
RATE_LIMIT_WINDOW = float(os.environ.get("AZURE_RATE_LIMIT_WINDOW", "60.0"))
RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("AZURE_RATE_LIMIT_MAX", "10"))
RATE_LIMIT_COOLDOWN = float(os.environ.get("AZURE_RATE_LIMIT_COOLDOWN", "30.0"))

# Command cooldown
_command_cooldowns: OrderedDict = OrderedDict()
_cooldown_lock = threading.Lock()
COMMAND_COOLDOWN = float(os.environ.get("AZURE_COMMAND_COOLDOWN", "5.0"))
MAX_COOLDOWN_ENTRIES = int(os.environ.get("AZURE_COOLDOWN_CACHE_SIZE", "1000"))

# Response cache
_response_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()
RESPONSE_CACHE_SIZE = int(os.environ.get("AZURE_RESPONSE_CACHE_SIZE", "100"))
RESPONSE_CACHE_TTL = float(os.environ.get("AZURE_RESPONSE_CACHE_TTL", "3600"))

# Context memory
_conversation_history: OrderedDict = OrderedDict()
_history_lock = threading.Lock()
CONTEXT_MEMORY_SIZE = int(os.environ.get("AZURE_CONTEXT_MEMORY_SIZE", "10"))
CONTEXT_MEMORY_MAX_USERS = int(os.environ.get("AZURE_CONTEXT_MEMORY_MAX_USERS", "100"))

# Error recovery
MAX_RETRIES = int(os.environ.get("AZURE_MAX_RETRIES", "3"))
RETRY_DELAY_BASE = float(os.environ.get("AZURE_RETRY_DELAY_BASE", "1.0"))
RETRY_DELAY_MAX = float(os.environ.get("AZURE_RETRY_DELAY_MAX", "10.0"))

# Reaction controls
_bot_messages: OrderedDict = OrderedDict()
_bot_messages_lock = threading.Lock()
BOT_MESSAGE_CACHE_SIZE = int(os.environ.get("AZURE_BOT_MESSAGE_CACHE_SIZE", "100"))
BOT_MESSAGE_TTL = float(os.environ.get("AZURE_BOT_MESSAGE_TTL", "3600"))

# Display / truncation
CHUNK_SIZE = int(os.environ.get("AZURE_CHUNK_SIZE", "1900"))
DELETE_AFTER_SECONDS = int(os.environ.get("AZURE_DELETE_AFTER", "10"))
LOG_MAX_AGE_DAYS = int(os.environ.get("AZURE_LOG_MAX_AGE", "7"))
DEFAULT_MAX_TOKENS = int(os.environ.get("AZURE_DEFAULT_MAX_TOKENS", "150"))
DEFAULT_TEMPERATURE = float(os.environ.get("AZURE_DEFAULT_TEMPERATURE", "0.7"))
DEFAULT_LOOKBACK_HOURS = int(os.environ.get("AZURE_LOOKBACK_HOURS", "72"))
CONFIRM_TIMEOUT = float(os.environ.get("AZURE_CONFIRM_TIMEOUT", "30.0"))
SETUP_TIMEOUT = float(os.environ.get("AZURE_SETUP_TIMEOUT", "60.0"))
MOD_LOOKBACK_HOURS = int(os.environ.get("AZURE_MOD_LOOKBACK_HOURS", "24"))
MAX_USER_FACTS = int(os.environ.get("AZURE_MAX_USER_FACTS", "5"))
RAG_TOP_K = int(os.environ.get("AZURE_RAG_TOP_K", "3"))
MAX_GOAL_DESC_LENGTH = int(os.environ.get("AZURE_MAX_GOAL_DESC_LENGTH", "100"))
CACHE_TOP_N = int(os.environ.get("AZURE_CACHE_TOP_N", "5"))
COGNITION_LOG_LIMIT = int(os.environ.get("AZURE_COGNITION_LOG_LIMIT", "5"))
MAX_STEPS_PREVIEW = int(os.environ.get("AZURE_MAX_STEPS_PREVIEW", "12"))
PROGRESS_LAST_N = int(os.environ.get("AZURE_PROGRESS_LAST_N", "5"))
AUTONOMOUS_SCAN_INTERVAL = int(os.environ.get("AZURE_AUTONOMOUS_SCAN_INTERVAL", "30"))
PERIODIC_SCAN_INTERVAL = int(os.environ.get("AZURE_PERIODIC_SCAN_INTERVAL", "5"))

# Truncation constants
TRUNC_LABEL = int(os.environ.get("AZURE_TRUNC_LABEL", "40"))
TRUNC_DESC = int(os.environ.get("AZURE_TRUNC_DESC", "100"))
TRUNC_PREVIEW = int(os.environ.get("AZURE_TRUNC_PREVIEW", "200"))
TRUNC_SMALL = int(os.environ.get("AZURE_TRUNC_SMALL", "80"))
TRUNC_VIOLATIONS = int(os.environ.get("AZURE_TRUNC_VIOLATIONS", "2"))
TRUNC_USER_FACTS = int(os.environ.get("AZURE_TRUNC_USER_FACTS", "5"))
TRUNC_RAG_LINES = int(os.environ.get("AZURE_TRUNC_RAG_LINES", "3"))
TRUNC_PHASE_LINES = int(os.environ.get("AZURE_TRUNC_PHASE_LINES", "8"))
TRUNC_RISK_TOP_USERS = int(os.environ.get("AZURE_TRUNC_RISK_TOP_USERS", "5"))
TRUNC_RISK_TOP_CHANNELS = int(os.environ.get("AZURE_TRUNC_RISK_TOP_CHANNELS", "3"))
TRUNC_CACHE_TOP = int(os.environ.get("AZURE_TRUNC_CACHE_TOP", "5"))
TRUNC_GOALS_DISPLAY = int(os.environ.get("AZURE_TRUNC_GOALS_DISPLAY", "3"))
TRUNC_RAG_RESULTS = int(os.environ.get("AZURE_TRUNC_RAG_RESULTS", "3"))
TRUNC_TOPICS = int(os.environ.get("AZURE_TRUNC_TOPICS", "5"))
TRUNC_SCHEDULE_LIST = int(os.environ.get("AZURE_TRUNC_SCHEDULE_LIST", "40"))
TRUNC_PLAN_STEPS = int(os.environ.get("AZURE_TRUNC_PLAN_STEPS", "12"))
TRUNC_PROGRESS_STEPS = int(os.environ.get("AZURE_TRUNC_PROGRESS_STEPS", "5"))
TRUNC_RESPONSE_DISPLAY = int(os.environ.get("AZURE_TRUNC_RESPONSE_DISPLAY", "300"))
TRUNC_REPAIR_MSG = int(os.environ.get("AZURE_TRUNC_REPAIR_MSG", "80"))
TRUNC_FEEDBACK_PREVIEW = int(os.environ.get("AZURE_TRUNC_FEEDBACK_PREVIEW", "100"))
TRUNC_PLAN_PREVIEW = int(os.environ.get("AZURE_TRUNC_PLAN_PREVIEW", "200"))
TRUNC_EPISODE = int(os.environ.get("AZURE_TRUNC_EPISODE", "200"))
TRUNC_PENDING = int(os.environ.get("AZURE_TRUNC_PENDING", "10"))
TRUNC_STEPS_PREVIEW = int(os.environ.get("AZURE_TRUNC_STEPS_PREVIEW", "12"))
TRUNC_CRON_NAME = int(os.environ.get("AZURE_TRUNC_CRON_NAME", "50"))

# ---------------------------------------------------------------------------
# Migration: overlay pydantic_config values where they exist
# ---------------------------------------------------------------------------
try:
    from .pydantic_config import config as _pc

    if _pc is not None:
        # Rate limiting
        RATE_LIMIT_MAX_REQUESTS = _pc.rate_limit_messages
        RATE_LIMIT_WINDOW = _pc.rate_limit_window
        RATE_LIMIT_COOLDOWN = _pc.rate_limit_cooldown
        MAX_RATE_LIMIT_ENTRIES = _pc.rate_limit_cache_size

        # Command cooldown
        COMMAND_COOLDOWN = _pc.cooldown_seconds
        MAX_COOLDOWN_ENTRIES = _pc.cooldown_cache_size

        # Response cache
        RESPONSE_CACHE_SIZE = _pc.response_cache_size
        RESPONSE_CACHE_TTL = _pc.response_cache_ttl

        # Context memory
        CONTEXT_MEMORY_SIZE = _pc.context_memory_size
        CONTEXT_MEMORY_MAX_USERS = _pc.context_memory_max_users

        # Error recovery
        MAX_RETRIES = _pc.max_retries
        RETRY_DELAY_BASE = _pc.retry_delay_base
        RETRY_DELAY_MAX = _pc.retry_delay_max

        # Bot message cache
        BOT_MESSAGE_CACHE_SIZE = _pc.bot_message_cache_size
        BOT_MESSAGE_TTL = _pc.bot_message_ttl

        # Display
        CHUNK_SIZE = _pc.chunk_size
        DELETE_AFTER_SECONDS = _pc.delete_after_seconds
        DEFAULT_MAX_TOKENS = _pc.default_max_tokens
        DEFAULT_TEMPERATURE = _pc.default_temperature

except (ImportError, AttributeError):
    pass  # Pydantic not available, use existing os.environ defaults
