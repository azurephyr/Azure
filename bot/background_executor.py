from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import string
from collections.abc import Coroutine
from typing import Any

import discord

logger = logging.getLogger(__name__)


class BackgroundExecutor:
    """Manages background tasks so command handlers don't block.

    Notes:
    - Each dispatched task is stored in `self.tasks` keyed by a short handle.
    - Tasks are pruned once they complete (via a done-callback) so the dict
      does not grow unboundedly across the bot's lifetime.
    """

    # Soft cap on tracked tasks. Old finished entries are pruned before
    # any new dispatch so we never accumulate forever.
    MAX_TRACKED_TASKS = 200

    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self.tasks: dict[str, asyncio.Task] = {}

    def _prune(self) -> None:
        """Remove done tasks to keep memory bounded."""
        done_keys = [k for k, t in self.tasks.items() if t.done()]
        for k in done_keys:
            self.tasks.pop(k, None)

    def dispatch(self, user_id: int, channel: discord.TextChannel,
                 coro: Coroutine[Any, Any, Any], task_name: str = "Task") -> asyncio.Task:
        """Dispatches a coroutine as a background task."""
        self._prune()
        if len(self.tasks) >= self.MAX_TRACKED_TASKS:
            logger.warning(
                "Background executor at capacity (%d tasks). "
                "Old finished tasks should have been pruned.",
                len(self.tasks),
            )

        async def _wrapper():
            try:
                try:
                    await channel.send(
                        f"⏳ **{task_name} started** for <@{user_id}>... "
                        f"I will ping you when it's done."
                    )
                except Exception:
                    logger.warning("Background task '%s': initial notification failed", task_name)
                result = await coro
                ping = f"<@{user_id}> 🔔 **{task_name} complete!**"
                if result:
                    res_text = str(result)
                    if len(res_text) > 1800:
                        res_text = res_text[:1800] + "...\n(truncated)"
                    await channel.send(f"{ping}\n{res_text}")
                else:
                    await channel.send(ping)
            except asyncio.CancelledError:
                logger.info("Background task '%s' cancelled", task_name)
                raise
            except Exception as e:
                logger.error("Background task '%s' failed: %s", task_name, e)
                with contextlib.suppress(Exception):
                    await channel.send(f"<@{user_id}> ⚠️ **{task_name} failed:** {e}")

        task = asyncio.get_running_loop().create_task(_wrapper())
        handle = f"{task_name}-{id(task)}-{''.join(random.choices(string.ascii_lowercase, k=4))}"
        self.tasks[handle] = task

        # Prune this task handle once it finishes.
        def _on_done(_t: asyncio.Task, _h: str = handle) -> None:
            self.tasks.pop(_h, None)

        task.add_done_callback(_on_done)
        return task
