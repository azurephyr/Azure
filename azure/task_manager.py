"""
Azure Uninterruptible Task Manager

Ensures the bot completes tasks even if:
  - Discord disconnects temporarily
  - User sends more messages
  - Rate limits are hit
  - Network hiccups occur

Usage:
    from azure.task_manager import TaskManager
    tm = TaskManager()

    # Start a task that can't be interrupted
    await tm.start_task(
        name="server_setup",
        coro=tools.execute_plan(guild, plan, ctx),
        ctx=ctx,
        on_busy="I'm still working on the previous task...",
    )

Features:
  - Busy flag: bot won't start new tasks while one is running
  - Queue: new requests are queued and processed after current task
  - Background execution: tasks run in asyncio tasks, not blocking the main loop
  - Auto-retry: rate limits, temporary disconnects are handled
  - Progress reporting: user knows the bot is still working
  - Task history: what tasks were completed, failed, or retried
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("azure.task_manager")


def _close_coro_safe(coro):
    """Close a coroutine object safely to avoid 'was never awaited' warnings."""
    if coro is not None and hasattr(coro, 'close') and callable(coro.close):
        with contextlib.suppress(Exception):
            coro.close()


@dataclass
class TaskRecord:
    """Record of a task execution."""
    name: str
    t_start: float
    t_end: float = 0
    success: bool = False
    error: str = ""
    retries: int = 0
    guild_name: str = ""


class TaskManager:
    """
    Manages long-running Discord operations.

    The bot can only do ONE major task at a time.
    New requests are either queued or acknowledged with a busy message.
    """

    # Max retries for a single task
    MAX_RETRIES = 5
    RETRY_DELAY_BASE = 2

    # Max time to wait for a task (10 minutes - allows large server setups)
    TASK_TIMEOUT = 600

    _MAX_QUEUE_SIZE = 20
    _MAX_DEAD_LETTER = 50

    def __init__(self):
        self._busy = False
        self._lock = asyncio.Lock()
        self._current_task: asyncio.Task | None = None
        self._current_task_name: str = ""
        self._queue: list[dict] = []
        self._history: list[TaskRecord] = []
        self._cancelled = False
        self._history_max = 200  # Prevent unbounded growth
        self._dead_letter: list[TaskRecord] = []

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def is_busy(self) -> bool:
        """Is the bot currently working on a task?"""
        return self._busy

    def get_current_task(self) -> str:
        """Name of the current task."""
        return self._current_task_name

    # ------------------------------------------------------------------
    # Task Execution
    # ------------------------------------------------------------------

    async def start_task(self, name: str, coro, ctx=None,
                         on_busy: str = "I'm busy with another task. Please wait...",
                         queue_if_busy: bool = False) -> Any:
        """
        Start a task. If busy, either queue it or tell the user to wait.

        Args:
            name: Task name for logging
            coro: A coroutine OR a callable that returns a new coroutine (for retries)
            ctx: Discord context (for sending busy messages)
            on_busy: Message to send if busy
            queue_if_busy: If True, queue the task. If False, tell user to wait.

        Returns:
            Task result, or None if busy/queued
        """
        guild_name = ctx.guild.name if ctx and ctx.guild else "DM"

        # Wrap bare coroutine in a factory (for retry support)
        if asyncio.iscoroutine(coro):
            _coro = coro
            def coro_factory():
                return _coro
            _original_coro = coro
        elif callable(coro):
            coro_factory = coro
            _original_coro = None
        else:
            raise TypeError(f"start_task expects a coroutine or callable, got {type(coro)}")

        # Atomic busy-check under lock
        async with self._lock:
            if self._busy:
                if queue_if_busy:
                    if len(self._queue) >= self._MAX_QUEUE_SIZE:
                        if ctx:
                            with contextlib.suppress(Exception):
                                await ctx.send("⏳ Task queue is full. Please wait for current tasks to finish.")
                        logger.warning("[task_manager] Queue full (%d), rejecting task '%s'", len(self._queue), name)
                        _close_coro_safe(_original_coro)
                        return None
                    self._queue.append({
                        "name": name,
                        "coro_factory": coro_factory,
                        "ctx": ctx,
                        "_coro": _original_coro,
                    })
                    if ctx:
                        try:
                            position = len(self._queue)
                            await ctx.send(f"⏳ **Queued:** position #{position}\nI'll start after the current task finishes.")
                        except Exception as e_q:
                            logger.warning("Failed to send queue message: %s", e_q)
                    return None
                else:
                    _close_coro_safe(_original_coro)
                    if ctx:
                        try:
                            await ctx.send(on_busy)
                        except Exception as e_b:
                            logger.warning("Failed to send busy message: %s", e_b)
                    return None
            else:
                self._busy = True
                self._current_task_name = name

        record = TaskRecord(
            name=name,
            t_start=time.time(),
            guild_name=guild_name,
        )

        # Wrap execution in a tracked asyncio.Task
        async def _tracked():
            from azure.logging_config import clear_request_context, generate_execution_id, set_request_context
            exec_id = generate_execution_id()
            ctx_user_id = ""
            if ctx and hasattr(ctx, "author"):
                ctx_user_id = str(ctx.author.id)
            elif ctx and hasattr(ctx, "guild"):
                ctx_user_id = f"guild:{ctx.guild.id}" if ctx.guild else "dm"
            set_request_context(execution_id=exec_id, user_id=ctx_user_id)
            try:
                return await self._execute_with_retries(coro_factory, name, record)
            finally:
                clear_request_context()

        task = asyncio.create_task(_tracked())
        self._current_task = task

        try:
            result = await task
            record.success = True
            return result
        except Exception as e:
            record.error = str(e)[:200]
            logger.error(f"[task_manager] Task '{name}' failed after retries: {e}")
            if len(self._dead_letter) >= self._MAX_DEAD_LETTER:
                self._dead_letter.pop(0)
            self._dead_letter.append(record)

            if ctx:
                try:
                    await ctx.send(f"❌ Task '{name}' failed: {e}")
                except Exception as e_fail:
                    logger.warning("Failed to send failure message: %s", e_fail)
            return None
        finally:
            record.t_end = time.time()
            # Trim history to prevent memory leak
            if len(self._history) > self._history_max:
                self._history = self._history[-self._history_max:]
            self._history.append(record)
            self._current_task = None
            async with self._lock:
                self._busy = False
                self._current_task_name = ""
            # Process queued tasks
            await self._process_queue()

    async def _execute_with_retries(self, coro_factory, name: str,
                                     record: TaskRecord) -> Any:
        """Execute a coroutine with retries on transient errors.

        Args:
            coro_factory: A callable that returns a NEW coroutine each time.
                          This avoids "cannot reuse already awaited coroutine" errors.
        """
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                # Create a fresh coroutine for each attempt
                result = await asyncio.wait_for(coro_factory(), timeout=self.TASK_TIMEOUT)
                record.retries = attempt - 1
                return result
            except TimeoutError:
                logger.info(f"[task_manager] Task '{name}' timed out (attempt {attempt})")

                if attempt == self.MAX_RETRIES:
                    raise
                await asyncio.sleep(self.RETRY_DELAY_BASE * attempt)
            except Exception as e:
                error_str = str(e).lower()
                if any(w in error_str for w in ["rate limit", "timeout", "connection", "disconnect", "websocket"]):
                    logger.info(f"[task_manager] Task '{name}' transient error (attempt {attempt}): {e}")

                    record.retries = attempt
                    if attempt < self.MAX_RETRIES:
                        await asyncio.sleep(self.RETRY_DELAY_BASE * attempt)
                        continue
                raise

    async def _process_queue(self) -> None:
        """Process queued tasks."""
        if not self._queue or self._busy:
            return

        next_task = self._queue.pop(0)
        try:
            await self.start_task(
                name=next_task["name"],
                coro=next_task["coro_factory"],
                ctx=next_task["ctx"],
                queue_if_busy=True,
            )
        except Exception as e:
            logger.error(f"[task_manager] Queued task failed: {e}")


    # ------------------------------------------------------------------
    # Queue Management
    # ------------------------------------------------------------------

    def queue_size(self) -> int:
        """How many tasks are queued?"""
        return len(self._queue)

    def clear_queue(self) -> None:
        """Clear all queued tasks."""
        for item in self._queue:
            _close_coro_safe(item.get("_coro"))
        self._queue.clear()

    def get_queue_names(self) -> list[str]:
        """Get names of queued tasks."""
        return [t["name"] for t in self._queue]

    def get_dead_letter(self) -> list[TaskRecord]:
        """Return the dead-letter queue (permanently failed tasks)."""
        return list(self._dead_letter)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_history(self, n: int = 10) -> list[TaskRecord]:
        """Get recent task history."""
        return self._history[-n:]

    def get_stats(self) -> dict[str, object]:
        """Get task statistics."""
        total = len(self._history)
        successful = sum(1 for t in self._history if t.success)
        avg_duration = sum(t.t_end - t.t_start for t in self._history if t.t_end > 0) / total if total > 0 else 0
        return {
            "total_tasks": total,
            "successful": successful,
            "failed": total - successful,
            "success_rate": successful / total if total > 0 else 0.0,
            "avg_duration": avg_duration,
            "currently_busy": self._busy,
            "current_task": self._current_task_name,
            "queued": self.queue_size(),
            "dead_letter": len(self._dead_letter),
        }

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def cancel_current(self) -> None:
        """Cancel the current task."""
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            try:
                await self._current_task
            except asyncio.CancelledError:
                logger.info("Task '%s' was cancelled", self._current_task_name)
        self._busy = False
        self._current_task_name = ""
        for item in self._queue:
            _close_coro_safe(item.get("_coro"))
        self._queue.clear()
