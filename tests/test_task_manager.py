"""Tests for TaskManager (azure/task_manager.py)."""

import asyncio

import pytest

from azure.task_manager import TaskManager


@pytest.fixture
def tm():
    return TaskManager()


# ---- Basic lifecycle ------------------------------------------------------


@pytest.mark.asyncio
async def test_task_starts_immediately_when_idle(tm):
    async def dummy():
        return "done"

    result = await tm.start_task("test", dummy())
    assert result == "done"
    assert tm.is_busy is False


@pytest.mark.asyncio
async def test_task_sets_busy_during_execution(tm):
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow():
        started.set()
        await release.wait()
        return "ok"

    task = asyncio.create_task(tm.start_task("slow", slow()))
    await started.wait()
    assert tm.is_busy is True

    release.set()
    await task
    assert tm.is_busy is False


@pytest.mark.asyncio
async def test_task_records_history(tm):
    async def work():
        return 42

    await tm.start_task("work", work())
    history = tm.get_history()
    assert len(history) == 1
    assert history[0].name == "work"
    assert history[0].success is True


# ---- Busy / queue behaviour -----------------------------------------------


@pytest.mark.asyncio
async def test_busy_rejects_new_task(tm):
    release = asyncio.Event()

    async def slow():
        await release.wait()

    task = asyncio.create_task(tm.start_task("slow", slow()))
    await asyncio.sleep(0)  # let it start

    async def fast():
        return "x"
    result = await tm.start_task("fast", fast())
    assert result is None

    release.set()
    await task


@pytest.mark.asyncio
async def test_queue_if_busy(tm):
    release = asyncio.Event()
    order = []

    async def slow():
        await release.wait()
        order.append("slow_done")

    async def queued():
        order.append("queued_done")
        return "q"

    task = asyncio.create_task(tm.start_task("slow", slow()))
    await asyncio.sleep(0)

    await tm.start_task("queued", queued(), queue_if_busy=True)
    assert len(tm._queue) == 1

    release.set()
    await task
    await asyncio.sleep(0.05)  # let queue processor run

    assert "queued_done" in order


@pytest.mark.asyncio
async def test_queued_task_runs_after_current_finishes(tm):
    results = []

    async def first():
        await asyncio.sleep(0.02)
        results.append("first")

    async def second():
        results.append("second")

    await tm.start_task("first", first(), queue_if_busy=True)
    # first is running, queue second
    await tm.start_task("second", second(), queue_if_busy=True)

    await asyncio.sleep(0.1)
    assert results == ["first", "second"]


# ---- Cancellation ---------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_current(tm):
    async def infinite():
        while True:
            await asyncio.sleep(0.01)

    asyncio.create_task(tm.start_task("inf", infinite()))
    await asyncio.sleep(0.01)

    await tm.cancel_current()
    assert tm.is_busy is False
    assert tm.get_current_task() == ""


# ---- Queue management -----------------------------------------------------


def test_queue_size(tm):
    assert tm.queue_size() == 0


def test_clear_queue(tm):
    tm._queue = [{"name": "a"}, {"name": "b"}]
    tm.clear_queue()
    assert tm.queue_size() == 0


def test_get_queue_names(tm):
    tm._queue = [{"name": "a"}, {"name": "b"}]
    assert tm.get_queue_names() == ["a", "b"]


# ---- Stats ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_stats(tm):
    async def work():
        return True

    await tm.start_task("work", work())
    s = tm.get_stats()
    assert s["total_tasks"] == 1
    assert s["successful"] == 1
    assert s["success_rate"] == 1.0


@pytest.mark.asyncio
async def test_failed_task_recorded(tm):
    async def fail():
        raise ValueError("boom")

    await tm.start_task("fail", fail())
    history = tm.get_history()
    assert len(history) == 1
    assert history[0].success is False
    assert "boom" in history[0].error
