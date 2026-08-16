"""
Live execution telemetry for Discord progress + web dashboard.

Design goals:
  - Every visible step comes from a real emit() during request handling
  - Stages form a live pipeline (running → done/error) with real timings
  - Discord and web share the same presentation snapshot
  - No fake/hardcoded step lists — empty until real events arrive
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("azure.telemetry")

# Optional process-wide DB for durable telemetry (set by bot startup / web).
_TELEMETRY_DB = None
_WS_MANAGER = None  # cached reference to avoid per-event import
_MAIN_LOOP = None  # cached main thread event loop for background threads


def set_telemetry_db(db) -> None:
    """Wire DatabaseManager so emit() persists to telemetry_logs."""
    global _TELEMETRY_DB
    _TELEMETRY_DB = db


def set_main_loop(loop) -> None:
    """Cache the main event loop so background threads can schedule work."""
    global _MAIN_LOOP
    _MAIN_LOOP = loop


# ---------------------------------------------------------------------------
# Stage labels (presentation only — does NOT invent steps)
# ---------------------------------------------------------------------------

# Friendly titles when action is known; unknown actions use title-case of action.
_ACTION_LABELS: dict[str, str] = {
    "START": "Starting",
    "GREETING": "Greeting",
    "RECALL": "Recalling memory",
    "MEMORY": "Memory",
    "RAG": "Searching knowledge",
    "ANALYZING": "Analyzing",
    "UNDERSTANDING": "Understanding",
    "DECIDING": "Deciding intent",
    "PLANNING": "Planning",
    "EXECUTING": "Executing",
    "STEP": "Step",
    "TOOL": "Tool",
    "GENERATING": "Generating reply",
    "LLM_CALL": "Calling AI",
    "FAILOVER": "Trying backup",
    "REASONING": "Reasoning",
    "RESPONSE_GENERATION": "Formulating reply",
    "ADAPTING": "Adapting tone",
    "SERVER_CONTEXT": "Loading server info",
    "CIRCUIT_BREAKER": "Circuit breaker",
    "AGENT_STEP": "Working",
    "ERROR": "Error",
    "COMPLETE": "Complete",
    "DONE": "Done",
}

# Actions that always appear in the user-facing pipeline
_PIPELINE_ACTIONS = frozenset({
    "START", "GREETING", "RECALL", "MEMORY", "RAG", "ANALYZING", "UNDERSTANDING",
    "DECIDING", "PLANNING", "EXECUTING", "STEP", "TOOL", "GENERATING",
    "LLM_CALL", "REASONING", "RESPONSE_GENERATION",
    "ADAPTING", "SERVER_CONTEXT", "CIRCUIT_BREAKER",
    "AGENT_STEP", "ERROR", "COMPLETE", "DONE",
})

# Status icons for Discord (unicode — works without custom emoji)
_ICON = {
    "running": "🔄",
    "done": "✅",
    "error": "❌",
    "warning": "⚠️",
    "info": "•",
}


@dataclass
class TelemetryEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    execution_id: str = ""
    timestamp: float = field(default_factory=time.time)
    subsystem: str = "core"
    action: str = ""
    message: str = ""
    status: str = "info"  # info, success, warning, error, running, done
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Stage:
    """One user-visible pipeline stage, driven only by real emits."""
    stage_id: str
    action: str
    label: str
    detail: str
    status: str  # running | done | error | warning
    started_at: float
    ended_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        end = self.ended_at if self.ended_at is not None else time.time()
        return max(0, int((end - self.started_at) * 1000))

    def to_dict(self) -> dict:
        return {
            "stage_id": self.stage_id,
            "action": self.action,
            "label": self.label,
            "detail": self.detail,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }


class ExecutionTracker:
    """Tracks live execution events for a single request.

    Emits events to callbacks (Discord message updater) and broadcasts
    globally for the web dashboard. Presentation is built only from
    events that actually happened.
    """

    def __init__(self, user: str, guild: str, request_text: str):
        self.execution_id = str(uuid.uuid4())
        self.user = user
        self.guild = guild
        self.request_text = request_text or ""
        self.start_time = time.time()
        self.events: list[TelemetryEvent] = []
        self.stages: list[Stage] = []
        self._lock = threading.RLock()
        self._finished = False
        self._finish_status: str = "running"  # running | success | error
        self._dot_counter: int = 0
        self._events_max: int = 200  # Cap event list to prevent unbounded growth

        # Local callbacks (e.g. to update the Discord message)
        self.callbacks: list[Callable[[TelemetryEvent], None]] = []

        logger.debug(
            "[telemetry] Started execution %s for %s in %s",
            self.execution_id, user, guild,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_callback(self, cb: Callable[[TelemetryEvent], None]) -> None:
        self.callbacks.append(cb)

    def emit(
        self,
        action: str,
        message: str,
        subsystem: str = "core",
        status: str = "info",
        **kwargs,
    ) -> TelemetryEvent:
        """Record a real runtime event and refresh the presentation pipeline."""
        action = (action or "EVENT").strip().upper().replace(" ", "_")
        message = (message or "").strip()
        meta = dict(kwargs) if kwargs else {}

        event = TelemetryEvent(
            execution_id=self.execution_id,
            subsystem=subsystem,
            action=action,
            message=message,
            status=status,
            metadata=meta,
        )

        with self._lock:
            self.events.append(event)
            self._update_stages(event)
            # Cap event list to prevent unbounded memory growth per request
            if len(self.events) > self._events_max:
                self.events = self.events[-self._events_max:]

        logger.debug(
            "[telemetry] emit %s: %s (stages=%d)",
            action, message[:120], len(self.stages),
        )

        # Callbacks / web / DB outside lock (may re-enter get_presentation)
        for i, cb in enumerate(self.callbacks):
            try:
                cb(event)
            except Exception as e:
                logger.error("[telemetry] Callback #%s failed: %s", i + 1, e)

        if _TELEMETRY_DB is not None:
            try:
                _TELEMETRY_DB.log_telemetry(
                    self.execution_id,
                    subsystem,
                    action,
                    message[:500],
                    status,
                )
            except Exception as e:
                logger.debug("[telemetry] DB persist failed: %s", e)

        self._broadcast_to_web(event)
        return event

    @property
    def is_finished(self) -> bool:
        """Whether this execution has been marked complete."""
        return self._finished

    def complete(self, success: bool = True, message: str = "") -> None:
        """Mark the whole execution finished (real terminal state)."""
        with self._lock:
            if self._finished:
                return
            self._finished = True
            self._finish_status = "success" if success else "error"
        elapsed = self.elapsed_ms
        if not message:
            with self._lock:
                msg_draft = (
                    f"Done in {self._format_duration(elapsed)}"
                    if success
                    else f"Failed after {self._format_duration(elapsed)}"
                )
            message = message or msg_draft
        self.emit(
            "COMPLETE" if success else "ERROR",
            message,
            subsystem="agent",
            status="success" if success else "error",
            elapsed_ms=elapsed,
        )
        # Close any still-running stages
        with self._lock:
            now = time.time()
            for stage in self.stages:
                if stage.status == "running":
                    stage.status = "done" if success else "error"
                    stage.ended_at = now

    @property
    def elapsed_ms(self) -> int:
        return max(0, int((time.time() - self.start_time) * 1000))

    def get_presentation(self) -> dict:
        """Snapshot for Discord + web — only real stages."""
        with self._lock:
            stages = [s.to_dict() for s in self.stages]
            running = next(
                (s.to_dict() for s in reversed(self.stages) if s.status == "running"),
                None,
            )
            preview = (self.request_text or "").replace("\n", " ").strip()
            if len(preview) > 80:
                preview = preview[:77] + "..."

            return {
                "execution_id": self.execution_id,
                "user": self.user,
                "guild": self.guild,
                "request_preview": preview,
                "elapsed_ms": self.elapsed_ms,
                "elapsed_label": self._format_duration(self.elapsed_ms),
                "finished": self._finished,
                "finish_status": self._finish_status,
                "stage_count": len(stages),
                "running": running,
                "stages": stages,
                "discord_text": self._format_discord(stages, running),
            }

    def get_discord_progress_text(self) -> str:
        """Format live progress for Discord message edits."""
        return self.get_presentation()["discord_text"]

    # ------------------------------------------------------------------
    # Stage pipeline (real events only)
    # ------------------------------------------------------------------

    def _is_known_action(self, action: str, status: str) -> bool:
        """Check if this event should be tracked in the stage pipeline."""
        if action in _PIPELINE_ACTIONS:
            return True
        # Failover is internal — don't show as a visible stage
        if action == "FAILOVER":
            return False
        return status in ("error", "warning", "running", "done", "success")

    @staticmethod
    def _is_error_status(event: TelemetryEvent) -> bool:
        return event.status == "error" or event.action == "ERROR"

    def _close_running_stages(self, now: float, status: str = "done") -> None:
        """Mark all running stages as finished."""
        for stage in self.stages:
            if stage.status == "running":
                stage.status = status
                stage.ended_at = now

    def _handle_terminal_action(self, action: str, detail: str, label: str, now: float, event: TelemetryEvent) -> None:
        """Handle COMPLETE/DONE/ERROR terminal actions."""
        terminal_status = "error" if self._is_error_status(event) else "done"
        self._close_running_stages(now, terminal_status)

        if self.stages and self.stages[-1].action == action:
            self.stages[-1].detail = detail or self.stages[-1].detail
            self.stages[-1].status = terminal_status
            self.stages[-1].ended_at = now
        else:
            self.stages.append(Stage(
                stage_id=str(uuid.uuid4()),
                action=action, label=label, detail=detail,
                status=terminal_status, started_at=now, ended_at=now,
                metadata=dict(event.metadata),
            ))

    def _try_refresh_running(self, action: str, detail: str, now: float, event: TelemetryEvent) -> bool:
        """Refresh an existing running stage if same action. Returns True if refreshed."""
        if action in ("STEP", "TOOL"):
            return False
        if not self.stages:
            return False
        last = self.stages[-1]
        if last.action != action or last.status != "running":
            return False
        if detail:
            last.detail = detail
        if event.metadata:
            last.metadata.update(event.metadata)
        if event.status == "error":
            last.status = "error"
            last.ended_at = now
        elif event.status in ("success", "done"):
            last.status = "done"
            last.ended_at = now
        return True

    @staticmethod
    def _determine_stage_status(action: str, event: TelemetryEvent) -> str:
        """Determine the status for a new stage."""
        if event.status == "error":
            return "error"
        if event.status in ("success", "done"):
            return "done"
        if action == "STEP" and event.status == "info":
            return "done"
        return "running"

    def _update_stages(self, event: TelemetryEvent) -> None:
        """Mutate stages based on a real event. Caller holds _lock."""
        action = event.action
        if not self._is_known_action(action, event.status):
            return

        now = event.timestamp
        detail = event.message or ""
        label = self._label_for(action, event.metadata)

        if action in ("COMPLETE", "DONE", "ERROR"):
            self._handle_terminal_action(action, detail, label, now, event)
            return

        if self._try_refresh_running(action, detail, now, event):
            return

        self._close_running_stages(now, "done")

        stage_status = self._determine_stage_status(action, event)
        self.stages.append(Stage(
            stage_id=str(uuid.uuid4()),
            action=action, label=label, detail=detail,
            status=stage_status, started_at=now,
            ended_at=now if stage_status != "running" else None,
            metadata=dict(event.metadata),
        ))

        if len(self.stages) > 12:
            self.stages = self.stages[:1] + self.stages[-11:]

    def _label_for(self, action: str, meta: dict[str, Any]) -> str:
        if meta.get("label"):
            return str(meta["label"])
        if action in _ACTION_LABELS:
            return _ACTION_LABELS[action]
        return action.replace("_", " ").title()

    def _format_discord(self, stages: list[dict], running: dict | None) -> str:
        elapsed = self._format_duration(self.elapsed_ms)

        if not self._finished:
            self._dot_counter += 1
            dots = "." * ((self._dot_counter % 3) + 1)
            return f"🧠 **Thinking{dots}**"

        # Finished
        if self._finish_status == "success":
            return f"✅ **Done** · `{elapsed}`"
        else:
            return f"❌ **Failed** · `{elapsed}`"

    @staticmethod
    def _format_duration(ms: int) -> str:
        if ms < 1000:
            return f"{ms}ms"
        sec = ms / 1000.0
        if sec < 60:
            return f"{sec:.1f}s"
        mins = int(sec // 60)
        rem = sec - mins * 60
        return f"{mins}m {rem:.0f}s"

    # ------------------------------------------------------------------
    # Web broadcast
    # ------------------------------------------------------------------

    def _broadcast_to_web(self, event: TelemetryEvent) -> None:
        """Send event + full presentation snapshot to the web socket manager."""
        global _WS_MANAGER
        try:
            if _WS_MANAGER is None:
                try:
                    from web.server import manager as _mgr
                    _WS_MANAGER = _mgr
                except ImportError:
                    _WS_MANAGER = False  # sentinel: import failed, don't retry
            if not _WS_MANAGER:
                return

            presentation = self.get_presentation()
            payload = {
                "type": "execution_telemetry",
                "execution_id": self.execution_id,
                "user": self.user,
                "guild": self.guild,
                "event": event.to_dict(),
                "presentation": presentation,
            }

            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_WS_MANAGER.broadcast(payload))
            except RuntimeError:
                # Called from a background thread — use cached main loop
                if _MAIN_LOOP and not _MAIN_LOOP.is_closed() and _MAIN_LOOP.is_running():
                    asyncio.run_coroutine_threadsafe(_WS_MANAGER.broadcast(payload), _MAIN_LOOP)
                else:
                    logger.debug("[telemetry] Background thread skipped broadcast — main loop unavailable")
        except Exception as e:
            logger.error("[telemetry] Web broadcast failed: %s", e)
