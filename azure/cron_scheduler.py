"""
CronScheduler — Milestone 3: Natural Language Cron Jobs (#8)

Allows users to schedule tasks in plain English:
  "Check server health every morning at 9am"
  "Remind me to review moderation logs every Monday"

Stores schedules persistently in logs/cron/schedules.json
Uses the croniter library for schedule evaluation.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ScheduledTask:
    task_id: str
    name: str
    description: str           # original natural language
    cron_expression: str       # e.g. "0 9 * * *" = every day at 9am
    channel_id: str
    user_id: str
    action: str                # "message" | "pipeline"
    action_args: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_run: float = 0.0
    run_count: int = 0
    enabled: bool = True


class CronScheduler:
    """
    Persistent cron scheduler for Azure.
    """
    DEFAULT_DIR = "logs/cron"

    def __init__(self, path: str | Path = None):
        self.path = Path(path or self.DEFAULT_DIR) / "schedules.json"
        self.tasks: dict[str, ScheduledTask] = {}
        self._callbacks: dict[str, Callable] = {}  # action -> async callable
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                for tid, t in data.items():
                    self.tasks[tid] = ScheduledTask(**t)
            except Exception as e:
                logger.error(f"Failed to load cron schedules: {e}")

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {tid: asdict(t) for tid, t in self.tasks.items()}
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:
            logger.error(f"Failed to save cron schedules: {e}")

    def register_callback(self, action: str, fn: Callable):
        """Register a callback for a specific action type."""
        self._callbacks[action] = fn

    def add_task(self, name: str, description: str, cron_expr: str,
                 channel_id: str, user_id: str, action: str = "message",
                 action_args: dict = None) -> ScheduledTask:
        import uuid
        task = ScheduledTask(
            task_id=str(uuid.uuid4())[:8],
            name=name,
            description=description,
            cron_expression=cron_expr,
            channel_id=channel_id,
            user_id=user_id,
            action=action,
            action_args=action_args or {}
        )
        self.tasks[task.task_id] = task
        self._save()
        logger.info(f"[cron] Scheduled '{name}' ({cron_expr})")
        return task

    def remove_task(self, task_id: str) -> bool:
        if task_id in self.tasks:
            del self.tasks[task_id]
            self._save()
            return True
        return False

    def get_due_tasks(self) -> list[ScheduledTask]:
        """Return tasks that are due to run now."""
        due = []
        now = time.time()
        try:
            from croniter import croniter
        except ImportError:
            logger.warning("[cron] croniter not installed, skipping schedule evaluation")
            return []
        for task in self.tasks.values():
            if not task.enabled:
                continue
            try:
                last = task.last_run or (now - 86400)  # default: 24h ago
                cron = croniter(task.cron_expression, last)
                next_run = cron.get_next(float)
                if next_run <= now:
                    due.append(task)
            except Exception as e:
                logger.error(f"[cron] Error evaluating task {task.task_id}: {e}")
        return due

    def mark_ran(self, task_id: str):
        task = self.tasks.get(task_id)
        if task:
            task.last_run = time.time()
            task.run_count += 1
            self._save()

    def natural_language_to_cron(self, text: str) -> str | None:
        """
        Convert simple NL expressions to cron.
        Supports common patterns; falls back to None for complex ones.
        """
        text = text.lower().strip()
        if "every hour" in text:
            return "0 * * * *"
        if "every 30 minutes" in text:
            return "*/30 * * * *"
        if "every 15 minutes" in text:
            return "*/15 * * * *"
        if "every day" in text or "daily" in text:
            hour = 9
            if "at " in text:
                parts = text.split("at ")
                if len(parts) > 1:
                    try:
                        hour_str = parts[1].split()[0].replace("am", "").replace("pm", "")
                        hour = int(hour_str)
                        # 12-hour -> 24-hour: 12am is 0, 12pm is 12, and
                        # 1..11pm add 12. Avoids producing an invalid hour 24.
                        if "pm" in parts[1]:
                            if hour != 12:
                                hour += 12
                        elif "am" in parts[1] and hour == 12:
                            hour = 0
                    except ValueError:
                        logger.warning("[cron] Failed to parse hour from '%s'", parts[1])
            return f"0 {hour} * * *"
        day_map = {
            "monday": 1, "tuesday": 2, "wednesday": 3,
            "thursday": 4, "friday": 5, "saturday": 6, "sunday": 0
        }
        for day, num in day_map.items():
            if day in text:
                return f"0 9 * * {num}"
        if "every morning" in text:
            return "0 9 * * *"
        if "every night" in text or "every evening" in text:
            return "0 20 * * *"
        return None

    def list_tasks(self) -> list[ScheduledTask]:
        return list(self.tasks.values())
