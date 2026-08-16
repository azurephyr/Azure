"""
Azure Moderation: Confirmation System

Provides YES/NO confirmation before destructive actions.
When a destructive action (ban, kick, timeout) is decided with low confidence
or when the policy requires confirmation, the action is queued instead of
executed immediately. An admin must confirm via !confirm or react to proceed.

Configuration (via env or ModerationPolicy):
  confirmation_mode: "none" | "destructive" | "all"
    - none:        no confirmation, act immediately (current behavior)
    - destructive: confirm only BAN, KICK, TIMEOUT
    - all:         confirm every non-trivial action

  confirmation_threshold: float (0.0–1.0)
    Actions with confidence below this threshold require confirmation.

Usage in engine:
    from .confirmation import ConfirmationQueue, requires_confirmation

    if requires_confirmation(decision, policy):
        queue.add(message_id, user_id, action, ...)
        # Send confirmation request to admin
    else:
        await execute_action(...)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from .policy import ActionType, ModerationPolicy

logger = logging.getLogger("azure.moderation.confirmation")


@dataclass
class PendingAction:
    """A moderation action waiting for human confirmation."""
    message_id: str
    user_id: str
    user_name: str
    action_type: str
    reason: str
    channel_id: str
    channel_name: str
    confidence: float
    risk_score: float
    explanation: str
    requested_at: datetime
    expires_at: datetime
    # These are stored as strings since we can't persist Discord objects
    # When confirmed, the engine will re-fetch the message from Discord
    content_preview: str = ""


class ConfirmationQueue:
    """Queue of pending moderation actions awaiting human confirmation."""

    def __init__(self, timeout_seconds: int = 60):
        self.pending: dict[str, PendingAction] = {}
        self._lock = threading.RLock()
        self.timeout = timedelta(seconds=timeout_seconds)
        self.timeout_seconds = timeout_seconds
        self._stats: dict[str, int] = {"expired": 0, "confirmed": 0, "cancelled": 0}
        # Run opportunistic cleanup every Nth add so the dict doesn't
        # leak expired actions forever. Cheap O(n) for typical sizes and
        # we never block real-time handlers.
        self._ops_since_cleanup = 0
        self._cleanup_every = 25

    def _maybe_cleanup(self) -> None:
        """Periodically evict expired entries on every Nth operation.

        NOTE: Must be called with self._lock *already held* by the caller.
        The counter increment is done under the caller's lock; the
        cleanup itself is done *outside* the lock to avoid deadlocking
        callers that hold a plain (non-reentrant) lock copy.
        """
        self._ops_since_cleanup += 1
        if self._ops_since_cleanup < self._cleanup_every:
            return
        self._ops_since_cleanup = 0
        try:
            self.cleanup_expired()
        except Exception:
            logger.exception("[confirmation] cleanup_expired failed")

    def add(self, message_id: str, user_id: str, user_name: str,
            action_type: str, reason: str, channel_id: str, channel_name: str,
            confidence: float, risk_score: float, explanation: str,
            content_preview: str = "") -> PendingAction:
        """Queue an action for confirmation."""
        with self._lock:
            self._maybe_cleanup()
            now = datetime.now()
            pending = PendingAction(
                message_id=message_id,
                user_id=user_id,
                user_name=user_name,
                action_type=action_type,
                reason=reason,
                channel_id=channel_id,
                channel_name=channel_name,
                confidence=confidence,
                risk_score=risk_score,
                explanation=explanation,
                requested_at=now,
                expires_at=now + self.timeout,
                content_preview=content_preview[:200],
            )
            self.pending[message_id] = pending
        return pending

    def confirm(self, message_id: str) -> PendingAction | None:
        """Confirm and remove an action from the queue. Returns None if expired."""
        with self._lock:
            pending = self.pending.pop(message_id, None)
            if pending and pending.expires_at < datetime.now():
                self._stats["expired"] += 1
                return None
            if pending:
                self._stats["confirmed"] += 1
        return pending

    def cancel(self, message_id: str) -> PendingAction | None:
        """Cancel and remove an action from the queue."""
        with self._lock:
            pending = self.pending.pop(message_id, None)
            if pending:
                self._stats["cancelled"] += 1
        return pending

    def get(self, message_id: str) -> PendingAction | None:
        """Get a pending action without removing it."""
        with self._lock:
            return self.pending.get(message_id)

    def list_pending(self) -> list[PendingAction]:
        """Return all pending actions."""
        with self._lock:
            return list(self.pending.values())

    def cleanup_expired(self) -> list[str]:
        """Remove expired actions. Returns list of removed IDs."""
        with self._lock:
            now = datetime.now()
            expired = [
                msg_id for msg_id, action in self.pending.items()
                if action.expires_at < now
            ]
            for msg_id in expired:
                del self.pending[msg_id]
        return expired

    def is_pending(self, message_id: str) -> bool:
        """Check if an action is still pending."""
        return message_id in self.pending

    def format_request(self, pending: PendingAction) -> str:
        """Format a human-readable confirmation request."""
        lines = [
            "🛡️ **Azure Confirmation Required**",
            f"**Action:** {pending.action_type}",
            f"**Target:** {pending.user_name} ({pending.user_id})",
            f"**Channel:** <#{pending.channel_id}>",
            f"**Confidence:** {pending.confidence:.0%}",
            f"**Risk:** {pending.risk_score:.0%}",
            f"**Reason:** {pending.explanation}",
            f"**Content:** {pending.content_preview}",
            "",
            "React ✅ to confirm, ❌ to cancel, or type:",
            f"`!azure_confirm {pending.message_id}`",
            f"`!azure_cancel {pending.message_id}`",
            "",
            f"⏰ Expires in {self.timeout_seconds}s",
        ]
        return "\n".join(lines)


def requires_confirmation(decision_action: ActionType, confidence: float,
                          risk_score: float, policy: ModerationPolicy) -> bool:
    """
    Determine if a moderation action requires human confirmation.

    Rules:
      - If policy.confirmation_mode == "none": never confirm
      - If policy.confirmation_mode == "destructive": confirm BAN, KICK, TIMEOUT
      - If policy.confirmation_mode == "all": confirm everything except NONE/LOG
      - If confidence < policy.confirmation_threshold: always confirm
      - If risk_score > 0.9 and action is destructive: confirm
    """
    # Read mode from policy (with env fallback)
    import os
    mode = getattr(policy, "confirmation_mode", os.environ.get("AZURE_CONFIRMATION_MODE", "destructive"))
    threshold = getattr(policy, "confirmation_threshold", float(os.environ.get("AZURE_CONFIRMATION_THRESHOLD", "0.75")))

    if mode == "none":
        return False

    if mode == "all":
        return decision_action not in (ActionType.NONE, ActionType.LOG)

    if mode == "destructive":
        destructive = (ActionType.BAN, ActionType.KICK, ActionType.TIMEOUT)
        if decision_action in destructive:
            return True

    # Low-confidence override
    if confidence < threshold and decision_action != ActionType.NONE:
        return True

    # High-risk override for destructive actions
    return bool(risk_score > 0.9 and decision_action in (ActionType.BAN, ActionType.KICK, ActionType.TIMEOUT))
