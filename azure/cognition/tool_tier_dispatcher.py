"""
ToolTierDispatcher — Deliverable 2: Tool-Tier Classification + Confirmation Gate

The critical structural change: every tool call is intercepted before reaching
the Discord API. This is NOT a prompt-level instruction — it is enforced in code.

Tool tiers:
  READ            — read_channels, read_roles, read_permissions, read_logs, etc.
  WRITE_SAFE      — send_message, create_channel, add_unused_role, etc.
  WRITE_DESTRUCTIVE — delete_channel, delete_role, ban_member, kick_member, etc.

Dispatch rules:
  READ            → execute immediately, feed result back to model context
  WRITE_SAFE      → execute immediately, feed result back to model context
  WRITE_DESTRUCTIVE → HOLD. Render proposed action as plan, require confirmation.

Per-call enforcement: if one safe and one destructive call are in the same turn,
the safe one runs and the destructive one pauses.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ToolTier(StrEnum):
    READ = "READ"
    WRITE_SAFE = "WRITE_SAFE"
    WRITE_DESTRUCTIVE = "WRITE_DESTRUCTIVE"


# ---------------------------------------------------------------------------
# Static tool tier map — every tool the model can call must be classified
# ---------------------------------------------------------------------------

TOOL_TIERS = {
    # READ — always safe, no side effects
    "read_channels": ToolTier.READ,
    "read_roles": ToolTier.READ,
    "read_permissions": ToolTier.READ,
    "read_logs": ToolTier.READ,
    "read_member_activity": ToolTier.READ,
    "read_moderation_setup": ToolTier.READ,
    "get_server_state": ToolTier.READ,
    "health_check": ToolTier.READ,
    "audit_server": ToolTier.READ,
    "get_time": ToolTier.READ,
    "read_bot_logs": ToolTier.READ,
    "get_bot_status": ToolTier.READ,
    "web_search": ToolTier.READ,
    "execute_python": ToolTier.WRITE_DESTRUCTIVE,
    # DiscordManagementTools read-style aliases
    "analyze_server_health": ToolTier.READ,
    # WRITE_SAFE — reversible, low impact
    "send_message": ToolTier.WRITE_SAFE,
    "create_channel": ToolTier.WRITE_SAFE,
    "create_category": ToolTier.WRITE_SAFE,
    "create_role": ToolTier.WRITE_SAFE,
    "add_unused_role": ToolTier.WRITE_SAFE,
    "edit_role": ToolTier.WRITE_SAFE,
    "edit_channel": ToolTier.WRITE_SAFE,
    "edit_category": ToolTier.WRITE_SAFE,
    "set_nickname": ToolTier.WRITE_SAFE,
    "move_member_to_voice": ToolTier.WRITE_SAFE,
    "move_channel": ToolTier.WRITE_SAFE,
    "sync_channel_permissions": ToolTier.WRITE_SAFE,
    "set_channel_permissions": ToolTier.WRITE_SAFE,
    "clear_channel_permissions": ToolTier.WRITE_SAFE,
    "assign_role": ToolTier.WRITE_SAFE,
    "remove_role": ToolTier.WRITE_SAFE,
    "create_webhook": ToolTier.WRITE_SAFE,
    "create_invite": ToolTier.WRITE_SAFE,
    "pin_message": ToolTier.WRITE_SAFE,
    "unpin_message": ToolTier.WRITE_SAFE,
    "create_thread": ToolTier.WRITE_SAFE,
    "archive_thread": ToolTier.WRITE_SAFE,
    "create_scheduled_event": ToolTier.WRITE_SAFE,
    "set_server_name": ToolTier.WRITE_SAFE,
    "set_verification_level": ToolTier.WRITE_SAFE,
    "set_content_filter": ToolTier.WRITE_SAFE,
    "set_notifications": ToolTier.WRITE_SAFE,
    "set_afk_channel": ToolTier.WRITE_SAFE,
    "set_system_channel": ToolTier.WRITE_SAFE,
    "set_rules_channel": ToolTier.WRITE_SAFE,
    "timeout_member": ToolTier.WRITE_SAFE,  # reversible via timeout(None)
    # WRITE_DESTRUCTIVE — irreversible or high-impact
    "delete_channel": ToolTier.WRITE_DESTRUCTIVE,
    "delete_role": ToolTier.WRITE_DESTRUCTIVE,
    "delete_category": ToolTier.WRITE_DESTRUCTIVE,
    "delete_webhook": ToolTier.WRITE_DESTRUCTIVE,
    "delete_scheduled_event": ToolTier.WRITE_DESTRUCTIVE,
    "kick_member": ToolTier.WRITE_DESTRUCTIVE,
    "ban_member": ToolTier.WRITE_DESTRUCTIVE,
    "unban_member": ToolTier.WRITE_SAFE,  # unban is a fix, not destruction
    "bulk_edit_permissions": ToolTier.WRITE_DESTRUCTIVE,
    "mass_message": ToolTier.WRITE_DESTRUCTIVE,
    "deafen_member": ToolTier.WRITE_SAFE,  # reversible
    "mute_member": ToolTier.WRITE_SAFE,  # reversible
    "delete_message": ToolTier.WRITE_DESTRUCTIVE,
    # Batch operations (3+ entities) are always destructive regardless of individual tier
    "batch_delete_channels": ToolTier.WRITE_DESTRUCTIVE,
    "batch_delete_roles": ToolTier.WRITE_DESTRUCTIVE,
    "batch_kick": ToolTier.WRITE_DESTRUCTIVE,
    "batch_ban": ToolTier.WRITE_DESTRUCTIVE,
}


@dataclass
class DispatchedResult:
    """Result of a single tool dispatch."""
    tool_name: str
    tier: ToolTier
    executed: bool = False
    held: bool = False
    result: Any = None
    error: str = ""
    confirmation_id: str | None = None
    user_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "tier": self.tier.value,
            "executed": self.executed,
            "held": self.held,
            "result": str(self.result) if self.result else None,
            "error": self.error,
            "confirmation_id": self.confirmation_id,
        }


@dataclass
class PendingConfirmation:
    """A destructive action held for confirmation."""
    confirmation_id: str
    user_id: str
    tool_name: str
    args: dict
    proposed_action: str
    guild_id: int | None = None
    created_at: float = field(default_factory=time.time)
    confirmed: bool = False
    executed: bool = False
    result: Any = None


class ToolTierDispatcher:
    """
    Intercepts every tool call before it reaches the Discord API.

    Enforces autonomy tiers in code:
      - READ / WRITE_SAFE: execute immediately, return result to model
      - WRITE_DESTRUCTIVE: hold, create pending confirmation, return hold notice
    """

    def __init__(self, tool_registry: dict = None, confirmation_timeout: float = 300.0):
        """
        Args:
            tool_registry: Map of tool_name -> callable
            confirmation_timeout: Seconds before a pending confirmation expires (default 5 min)
        """
        self.tool_registry = tool_registry or {}
        self.confirmation_timeout = confirmation_timeout
        # Pending confirmations: confirmation_id → PendingConfirmation
        self._pending: dict[str, PendingConfirmation] = {}
        # Stats
        self._stats = {"read": 0, "write_safe": 0, "write_destructive": 0, "held": 0, "expired": 0}

    def classify(self, tool_name: str) -> ToolTier:
        """Classify a tool by name. Unknown tools fail closed."""
        return TOOL_TIERS.get(tool_name, ToolTier.WRITE_DESTRUCTIVE)

    def dispatch(self, tool_name: str, args: dict, user_id: str = "",
                 guild_id: int | None = None) -> DispatchedResult:
        """
        Dispatch a single tool call.

        Returns DispatchedResult:
          - If READ/WRITE_SAFE: result contains the tool output
          - If WRITE_DESTRUCTIVE: result is None, held=True, confirmation_id set
        """
        if tool_name not in TOOL_TIERS:
            return DispatchedResult(
                tool_name=tool_name,
                tier=ToolTier.WRITE_DESTRUCTIVE,
                error=f"Tool not found: {tool_name}",
            )

        tier = self.classify(tool_name)

        # Check if this is a batch operation (3+ entities)
        if self._is_batch_operation(tool_name, args):
            tier = ToolTier.WRITE_DESTRUCTIVE

        if tier == ToolTier.WRITE_DESTRUCTIVE:
            return self._hold_for_confirmation(tool_name, args, user_id, guild_id)

        # Execute immediately
        return self._execute_immediately(tool_name, args, tier)

    def dispatch_multi(self, calls: list[dict], user_id: str = "",
                       guild_id: int | None = None) -> list[DispatchedResult]:
        """
        Dispatch multiple tool calls in one turn.

        Safe calls execute immediately. Destructive calls are held.
        Per-call enforcement: safe ones run, destructive ones pause.
        """
        results = []
        for call in calls:
            tool_name = call.get("tool", call.get("action", ""))
            args = call.get("args", call.get("kwargs", {}))
            result = self.dispatch(tool_name, args, user_id, guild_id)
            results.append(result)
        return results

    def confirm(self, confirmation_id: str, confirming_user_id: str, has_permission: bool = False) -> PendingConfirmation | None:
        """
        Confirm a held destructive action.

        Authorization policy: only the original requester (user_id who triggered
        the action) can confirm it. This prevents privilege escalation where a
        non-mod confirms a moderator's proposed destructive action. Admins can
        override this if has_permission is True.

        Args:
            confirmation_id: The confirmation ID from the hold notice
            confirming_user_id: The Discord user ID of the person confirming
            has_permission: Whether the user has admin/override permission

        Returns:
            PendingConfirmation with confirmed=True, or None if:
              - confirmation_id not found
              - confirming_user_id != original requester and not has_permission
              - confirmation has expired
        """
        pending = self._pending.get(confirmation_id)
        if not pending:
            return None

        # Authorization check: only the original requester can confirm (unless admin override)
        if confirming_user_id != pending.user_id and not has_permission:
            return None

        # Expiration check
        import time
        if time.time() - pending.created_at > self.confirmation_timeout:
            # Auto-cancel expired confirmations
            del self._pending[confirmation_id]
            self._stats["expired"] += 1
            return None

        pending.confirmed = True
        return pending

    def execute_confirmed(self, confirmation_id: str, executing_user_id: str = "", has_permission: bool = False) -> DispatchedResult:
        """
        Execute a confirmed destructive action and return the result.

        Args:
            confirmation_id: The confirmation ID
            executing_user_id: The user attempting to execute (must match requester)
            has_permission: Whether the user has admin/override permission

        Returns:
            DispatchedResult with the tool output, or error if not found/not confirmed
        """
        import time
        pending = self._pending.get(confirmation_id)
        if not pending:
            return DispatchedResult(
                tool_name="", tier=ToolTier.WRITE_DESTRUCTIVE,
                error="Confirmation not found",
            )

        # Authorization check at execution time too
        if executing_user_id and executing_user_id != pending.user_id and not has_permission:
            return DispatchedResult(
                tool_name=pending.tool_name, tier=ToolTier.WRITE_DESTRUCTIVE,
                error="Not authorized — only the original requester can execute this action",
            )

        # Expiration check at execution time too
        if time.time() - pending.created_at > self.confirmation_timeout:
            del self._pending[confirmation_id]
            self._stats["expired"] += 1
            return DispatchedResult(
                tool_name=pending.tool_name, tier=ToolTier.WRITE_DESTRUCTIVE,
                error="Confirmation expired — action was auto-cancelled",
            )

        if not pending.confirmed:
            return DispatchedResult(
                tool_name=pending.tool_name, tier=ToolTier.WRITE_DESTRUCTIVE,
                error="Not yet confirmed",
            )

        result = self._execute_immediately(pending.tool_name, pending.args, ToolTier.WRITE_DESTRUCTIVE)
        pending.executed = True
        pending.result = result.result
        return result

    def cancel(self, confirmation_id: str, cancelling_user_id: str = "", has_permission: bool = False) -> bool:
        """
        Cancel a pending confirmation.

        Authorization policy: only the original requester can cancel their own action (unless admin override).
        """
        pending = self._pending.get(confirmation_id)
        if not pending:
            return False
        if cancelling_user_id and cancelling_user_id != pending.user_id and not has_permission:
            return False
        del self._pending[confirmation_id]
        return True

    def get_pending_for_user(self, user_id: str) -> list[PendingConfirmation]:
        """Get all pending confirmations for a user."""
        import time
        now = time.time()
        valid = []
        for cid, p in list(self._pending.items()):
            if p.user_id == user_id:
                if now - p.created_at <= self.confirmation_timeout:
                    valid.append(p)
                else:
                    del self._pending[cid]
                    self._stats["expired"] += 1
        return valid

    def get_pending_for_guild(self, guild_id: int) -> list[PendingConfirmation]:
        """Get all pending confirmations for a guild."""
        import time
        now = time.time()
        valid = []
        for cid, p in list(self._pending.items()):
            if p.guild_id == guild_id:
                if now - p.created_at <= self.confirmation_timeout:
                    valid.append(p)
                else:
                    del self._pending[cid]
                    self._stats["expired"] += 1
        return valid

    def cleanup_expired(self) -> int:
        """Remove all expired confirmations. Returns count removed."""
        import time
        now = time.time()
        expired_ids = [
            cid for cid, p in self._pending.items()
            if now - p.created_at >= self.confirmation_timeout
        ]
        for cid in expired_ids:
            del self._pending[cid]
        self._stats["expired"] += len(expired_ids)
        return len(expired_ids)

    def format_hold_message(self, pending: PendingConfirmation) -> str:
        """Format a human-readable hold message for a destructive action."""
        return (
            f"⚠️ **Action held for confirmation**\n\n"
            f"Tool: `{pending.tool_name}`\n"
            f"Details: {pending.proposed_action}\n\n"
            f"This action is classified as **destructive** and requires your explicit approval.\n"
            f"Reply with **confirm** to proceed, or **cancel** to abort."
        )

    def format_confirmation_embed(self, pending: PendingConfirmation) -> dict:
        """Format a Discord embed for the confirmation request."""
        return {
            "title": "⚠️ Destructive Action Pending Confirmation",
            "description": pending.proposed_action,
            "fields": [
                {"name": "Tool", "value": f"`{pending.tool_name}`", "inline": True},
                {"name": "Tier", "value": "WRITE_DESTRUCTIVE", "inline": True},
                {"name": "Action Required", "value": "Reply `confirm` or `cancel`", "inline": False},
            ],
            "color": 0xE74C3C,  # red
        }

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _execute_immediately(self, tool_name: str, args: dict, tier: ToolTier) -> DispatchedResult:
        """Execute a tool immediately and return the result."""
        stat_key = tier.value.lower()
        self._stats[stat_key] = self._stats.get(stat_key, 0) + 1

        tool_fn = self.tool_registry.get(tool_name)

        if tool_fn is None:
            return DispatchedResult(
                tool_name=tool_name, tier=tier,
                error=f"Tool '{tool_name}' not found in registry",
            )

        try:
            result = tool_fn(**args)
            return DispatchedResult(
                tool_name=tool_name, tier=tier,
                executed=True, result=result,
            )
        except Exception as e:
            return DispatchedResult(
                tool_name=tool_name, tier=tier,
                error=str(e),
            )

    def _hold_for_confirmation(self, tool_name: str, args: dict, user_id: str,
                               guild_id: int | None) -> DispatchedResult:
        """Hold a destructive action for confirmation."""
        import time
        import uuid
        confirmation_id = str(uuid.uuid4())[:8]

        proposed = self._build_proposed_action(tool_name, args)

        pending = PendingConfirmation(
            confirmation_id=confirmation_id,
            user_id=user_id,
            tool_name=tool_name,
            args=args,
            proposed_action=proposed,
            guild_id=guild_id,
            created_at=time.time(),
        )
        self._pending[confirmation_id] = pending
        self._stats["held"] += 1

        return DispatchedResult(
            tool_name=tool_name, tier=ToolTier.WRITE_DESTRUCTIVE,
            held=True, confirmation_id=confirmation_id,
        )

    def _build_proposed_action(self, tool_name: str, args: dict) -> str:
        """Build a human-readable description of the proposed action."""
        parts = [f"{tool_name}"]
        for k, v in args.items():
            if k in ("guild", "ctx", "bot"):
                continue
            parts.append(f"{k}={v}")
        return " ".join(parts)

    def _is_batch_operation(self, tool_name: str, args: dict) -> bool:
        """Detect if this is a batch operation touching 3+ entities."""
        # Check for list-type arguments with 3+ items
        for v in args.values():
            if isinstance(v, list) and len(v) >= 3:
                return True
        # Check for batch in tool name
        return bool("batch" in tool_name.lower() or "bulk" in tool_name.lower())

    def get_stats(self) -> dict:
        """Return dispatch statistics."""
        return {
            **self._stats,
            "pending_count": len(self._pending),
        }
