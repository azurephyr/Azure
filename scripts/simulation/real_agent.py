"""Real AzureAgent integration — drives simulation through the actual LLM pipeline."""

import logging
import os
from pathlib import Path

from bot.context import ctx

logger = logging.getLogger("simulation.real_agent")


def _ensure_env_loaded():
    """Load .env file if not already loaded by the caller."""
    if os.environ.get("AZURE_LLM_PROVIDER"):
        return
    try:
        from dotenv import load_dotenv
        env_path = Path(".env")
        if env_path.exists():
            load_dotenv(dotenv_path=str(env_path), override=False)
            logger.info("Loaded .env for real-agent mode")
    except ImportError:
        logger.warning("python-dotenv not available — env vars must be set manually")


class WrappedAgent:
    """Wraps the real AzureAgent for simulation use.

    Lazily instantiates the agent on first use so import-time dependencies
    (discord mock injection) have already been set up.
    """

    def __init__(self):
        self._agent = None
        self._init_error = None

    def _ensure_agent(self):
        if self._agent is not None:
            return
        if self._init_error:
            return

        try:
            from azure.agent import AzureAgent
            self._agent = AzureAgent(
                model_name=os.environ.get("AZURE_LLM_PROVIDER", "azure_local"),
                local_llm_path=os.environ.get("AZURE_MODEL_PATH") or None,
                long_term_path=Path("data/simulation_memory.json"),
                moderation_mode="dry_run",
                log_dir=Path("logs/simulation"),
            )
            logger.info("Real AzureAgent initialized (provider=%s, model=%s)",
                        self._agent.model_name,
                        getattr(self._agent.api_llm, "_model", "n/a") if self._agent.api_llm else "local")
        except Exception as e:
            self._init_error = str(e)
            logger.error("Failed to initialize AzureAgent: %s", e)

    @property
    def available(self) -> bool:
        self._ensure_agent()
        return self._agent is not None

    @property
    def llm(self):
        self._ensure_agent()
        return self._agent.llm if self._agent else None

    @property
    def moderation(self):
        self._ensure_agent()
        return self._agent.moderation if self._agent else None

    async def handle(self, user="", message="", server_name="Discord",
                     user_id="", progress_callback=None, tracker=None,
                     guild=None, channel=None, event_loop=None, discord_tools=None):
        self._ensure_agent()
        if self._agent is None:
            return f"[RealAgent unavailable: {self._init_error or 'unknown error'}]"
        return await self._agent.handle(
            user=user, message=message, server_name=server_name,
            user_id=user_id, progress_callback=progress_callback,
            tracker=tracker, guild=guild, channel=channel,
            event_loop=event_loop, discord_tools=discord_tools,
        )

    async def cognitize(self, message="", user_name="", is_directed=True,
                        is_dm=False, is_mentioned=False, params=None,
                        is_admin=False, has_guild=True, event_loop=None):
        self._ensure_agent()
        if self._agent is None:
            from unittest.mock import MagicMock
            return (MagicMock(), "[RealAgent unavailable]")
        return await self._agent.cognitize(
            message=message, user_name=user_name,
            is_directed=is_directed, is_dm=is_dm,
            is_mentioned=is_mentioned, params=params,
            is_admin=is_admin, has_guild=has_guild,
            event_loop=event_loop,
        )

    def set_discord_context(self, discord_tools=None, guild=None, channel=None, event_loop=None):
        self._ensure_agent()
        if self._agent:
            self._agent.set_discord_context(
                discord_tools=discord_tools, guild=guild,
                channel=channel, event_loop=event_loop,
            )


def setup_real_agent(env):
    """Configure ctx with a real AzureAgent for simulation."""
    _ensure_env_loaded()
    wrapped = WrappedAgent()
    wrapped._ensure_agent()

    if not wrapped.available:
        logger.warning("Real agent unavailable, falling back to FakeAgent")
        return False

    # Override the FakeAgent on the env with our real wrapper
    env.agent = wrapped

    # Update ctx to point at the real agent
    ctx.agent = wrapped

    if wrapped._agent and wrapped._agent.api_llm:
        logger.info("API provider: %s  Model: %s",
                    wrapped._agent.api_llm._provider,
                    wrapped._agent.api_llm._model)
    return True
