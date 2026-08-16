import contextlib
import json
import logging
import os
import time
from contextvars import ContextVar
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from .api_auth import require_admin
from .broadcast import broadcast_event

logger = logging.getLogger("web.api_moderation")

router = APIRouter()

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
ACTIONS_LOG = LOGS_DIR / "moderation_actions.jsonl"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_actions_log(limit: int = 500) -> list[dict]:
    """Read moderation_actions.jsonl newest-first."""
    entries: list[dict] = []
    if not ACTIONS_LOG.exists():
        return entries
    try:
        with open(ACTIONS_LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning("Failed to read %s: %s", ACTIONS_LOG, e)
    entries.reverse()
    return entries[:limit]


def _read_audit_log(limit: int = 500) -> list[dict]:
    """Read audit_logs from SQLite if available, else empty list."""
    request = _get_request()
    if not request:
        return []
    db = getattr(getattr(request, "app", None), "state", None)
    db = getattr(db, "db", None) if db else None
    if not db:
        return []
    try:
        with db._wlock:
            conn = db._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.warning("Failed to read audit_logs: %s", e)
        return []


_request_ctx: ContextVar[Request | None] = ContextVar("_request_ctx", default=None)


def _set_request(req: Request) -> None:
    _request_ctx.set(req)


def _get_request() -> Request | None:
    return _request_ctx.get()


class ActionRequest(BaseModel):
    action_type: str  # warn, timeout, kick, ban, delete
    guild_id: str
    user_id: str
    user_name: str = ""
    reason: str = ""
    duration_minutes: int = 5  # for timeout
    message_id: str = ""
    channel_id: str = ""


def _allowed_web_guild_ids() -> set[str]:
    """Guilds explicitly authorized for dashboard mutations."""
    return {
        value.strip()
        for value in os.environ.get("AZURE_WEB_ALLOWED_GUILD_IDS", "").split(",")
        if value.strip().isdigit()
    }


# ---------------------------------------------------------------------------
# 1. GET /api/moderation/stats — real stats from engine + logs
# ---------------------------------------------------------------------------

@router.get("/stats")
async def get_moderation_stats(request: Request, user: dict = Depends(require_admin)):
    """
    Real moderation stats sourced from:
      - logs/moderation_actions.jsonl  (action history)
      - agent.moderation engine state  (ModerationEngine)
      - auto_moderation graduated response data
    """
    _set_request(request)

    # --- Action history from JSONL ---
    all_actions = _read_actions_log(limit=5000)
    total_actions = len(all_actions)
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_threat: dict[str, int] = {}
    recent_actions_24h = 0
    now = time.time()
    cutoff_24h = now - 86400
    executed_count = 0

    for a in all_actions:
        atype = a.get("action_type") or a.get("action") or "unknown"
        by_type[atype] = by_type.get(atype, 0) + 1

        sev = a.get("severity", "none")
        by_severity[sev] = by_severity.get(sev, 0) + 1

        threat = a.get("threat_level", "none")
        if threat:
            by_threat[threat] = by_threat.get(threat, 0) + 1

        ts = a.get("timestamp", 0)
        if isinstance(ts, (int, float)) and ts > cutoff_24h:
            recent_actions_24h += 1

        if a.get("executed"):
            executed_count += 1

    # --- Engine state from ModerationEngine ---
    engine_stats = {}
    engine = getattr(request.app.state, "agent", None)
    moderation = getattr(engine, "moderation", None) if engine else None
    if moderation and hasattr(moderation, "get_stats"):
        try:
            engine_stats = moderation.get_stats()
        except Exception as e:
            logger.warning("Failed to get engine stats: %s", e)

    # --- Auto-moderation graduated response data ---
    auto_mod_stats = {}
    if moderation:
        # ModerationEngine wraps ConfirmationQueue
        pending_count = 0
        if hasattr(moderation, "confirmation_queue"):
            try:
                pending = moderation.confirmation_queue.list_pending()
                pending_count = len(pending)
            except Exception:
                logger.exception("[api_moderation] pending confirmations list failed")
        auto_mod_stats["pending_confirmations"] = pending_count
        auto_mod_stats["phase"] = getattr(
            getattr(moderation, "policy", None), "phase", None
        )
        if hasattr(auto_mod_stats["phase"], "value"):
            auto_mod_stats["phase"] = auto_mod_stats["phase"].value

    return {
        "total_actions": total_actions,
        "executed_actions": executed_count,
        "actions_24h": recent_actions_24h,
        "by_action_type": by_type,
        "by_severity": by_severity,
        "by_threat_level": by_threat,
        "engine": engine_stats,
        "auto_moderation": auto_mod_stats,
    }


# ---------------------------------------------------------------------------
# 2. GET /api/moderation/actions — paginated action history
# ---------------------------------------------------------------------------

@router.get("/actions")
async def get_moderation_actions(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action_type: str | None = Query(None),
    user: dict = Depends(require_admin),
):
    """Recent moderation actions with pagination, from logs/moderation_actions.jsonl."""
    _set_request(request)
    all_actions = _read_actions_log(limit=2000)

    # Filter by action type if requested
    if action_type:
        all_actions = [
            a for a in all_actions
            if (a.get("action_type") or a.get("action", "")).lower() == action_type.lower()
        ]

    total = len(all_actions)
    page = all_actions[offset : offset + limit]

    # Normalise fields for the dashboard
    out = []
    for a in page:
        ts = a.get("timestamp", 0)
        if isinstance(ts, (int, float)):
            try:
                ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
            except Exception:
                ts_str = str(ts)
        else:
            ts_str = str(ts)

        out.append({
            "timestamp": ts_str,
            "timestamp_epoch": ts if isinstance(ts, (int, float)) else 0,
            "user_id": a.get("user_id", ""),
            "user_name": a.get("user_name", ""),
            "action_type": a.get("action_type") or a.get("action", ""),
            "reason": a.get("reason", ""),
            "severity": a.get("severity", "none"),
            "threat_level": a.get("threat_level", ""),
            "confidence": a.get("confidence", 0),
            "executed": a.get("executed", False),
            "dry_run": a.get("dry_run", False),
            "channel_id": a.get("channel_id", ""),
            "message_content": (a.get("content") or a.get("message_content", ""))[:200],
        })

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "actions": out,
    }


# ---------------------------------------------------------------------------
# 3. GET /api/moderation/rules — current moderation policy/rules
# ---------------------------------------------------------------------------

@router.get("/rules")
async def get_moderation_rules(request: Request, user: dict = Depends(require_admin)):
    """Return current moderation rules/policies from the engine."""
    engine = getattr(request.app.state, "agent", None)
    moderation = getattr(engine, "moderation", None) if engine else None

    if not moderation or not hasattr(moderation, "policy"):
        return {
            "error": "Moderation engine not available",
            "rules": _default_rules(),
        }

    policy = moderation.policy
    phase = getattr(policy, "phase", None)
    phase_value = getattr(phase, "value", str(phase)) if phase else "unknown"

    # Severity-to-action mapping
    severity_map = {}
    for sev_name in ("low", "medium", "high", "critical"):
        action = getattr(policy, f"{sev_name}_action", None)
        severity_map[sev_name] = getattr(action, "value", str(action)) if action else "none"

    # Escalation settings
    escalation = {
        "enabled": getattr(policy, "escalation_enabled", True),
        "window_minutes": getattr(policy, "escalation_window_minutes", 60),
        "warnings_before_timeout": getattr(policy, "max_warnings_before_timeout", 2),
        "timeouts_before_kick": getattr(policy, "max_timeouts_before_kick", 2),
        "kicks_before_ban": getattr(policy, "max_kicks_before_ban", 1),
    }

    # Rate limits
    rate_limits = {
        "max_actions_per_minute": getattr(policy, "max_actions_per_minute", 10),
        "max_deletions_per_minute": getattr(policy, "max_deletions_per_minute", 20),
        "max_bans_per_hour": getattr(policy, "max_bans_per_hour", 3),
        "max_timeouts_per_hour": getattr(policy, "max_timeouts_per_hour", 10),
    }

    # Exemptions
    exemptions = {
        "exempt_roles": getattr(policy, "exempt_roles", []),
        "exempt_channels": getattr(policy, "exempt_channels", []),
        "exempt_users": getattr(policy, "exempt_users", []),
        "exempt_owner": getattr(policy, "exempt_owner", True),
        "exempt_admins": getattr(policy, "exempt_admins", True),
        "exempt_bots": getattr(policy, "exempt_bots", True),
        "exempt_trusted_roles": getattr(policy, "exempt_trusted_roles", []),
    }

    # Confirmation requirements
    confirmation = {
        "require_confirmation_for_ban": getattr(policy, "require_confirmation_for_ban", True),
        "require_confirmation_for_kick": getattr(policy, "require_confirmation_for_kick", False),
        "confirmation_mode": getattr(policy, "confirmation_mode", "destructive"),
        "confirmation_threshold": getattr(policy, "confirmation_threshold", 0.75),
    }

    # Phase permissions
    try:
        from azure.moderation.phase import ALLOWED_ACTIONS as allowed_actions  # noqa: N811
        from azure.moderation.phase import MAX_TIMEOUT_MINUTES as max_timeout_minutes  # noqa: N811
    except ImportError:
        allowed_actions = {}
        max_timeout_minutes = {}
    phase_permissions = {}
    for p_name, p_actions in allowed_actions.items():
        phase_permissions[p_name.value] = {
            "allowed_actions": sorted(p_actions),
            "max_timeout_minutes": max_timeout_minutes.get(p_name, 0),
        }

    # Classification thresholds
    classification = {
        "spam_score_threshold": getattr(policy, "spam_score_threshold", 0.6),
        "scam_score_threshold": getattr(policy, "scam_score_threshold", 0.5),
        "toxicity_score_threshold": getattr(policy, "toxicity_score_threshold", 0.6),
    }

    return {
        "phase": phase_value,
        "mode": getattr(policy, "mode", "dry_run"),
        "dry_run": getattr(policy, "is_dry_run", lambda: True)(),
        "phase_description": getattr(policy, "get_phase_description", lambda: "")(),
        "severity_to_action": severity_map,
        "timeout_duration_minutes": getattr(policy, "timeout_duration_minutes", 5),
        "escalation": escalation,
        "rate_limits": rate_limits,
        "exemptions": exemptions,
        "confirmation": confirmation,
        "phase_permissions": phase_permissions,
        "classification": classification,
    }


def _default_rules() -> dict:
    """Fallback rules when engine is unavailable."""
    return {
        "phase": "dry_run",
        "severity_to_action": {"low": "log", "medium": "warn", "high": "timeout", "critical": "ban"},
        "escalation": {"enabled": True, "window_minutes": 60},
    }


# ---------------------------------------------------------------------------
# 4. GET /api/moderation/pending — pending confirmations
# ---------------------------------------------------------------------------

@router.get("/pending")
async def get_pending_confirmations(request: Request, user: dict = Depends(require_admin)):
    """Return moderation actions awaiting admin confirmation."""
    engine = getattr(request.app.state, "agent", None)
    moderation = getattr(engine, "moderation", None) if engine else None

    if not moderation:
        return {"pending": [], "error": "Moderation engine offline"}

    raw = []
    if hasattr(moderation, "list_pending_confirmations"):
        try:
            raw = moderation.list_pending_confirmations()
        except Exception as e:
            logger.warning("Failed to list pending: %s", e)
    elif hasattr(moderation, "confirmation_queue"):
        try:
            raw = [
                p.__dict__ if hasattr(p, "__dict__") else p
                for p in moderation.confirmation_queue.list_pending()
            ]
        except Exception as e:
            logger.warning("Failed to list pending from queue: %s", e)

    out = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        item = dict(p)
        # Normalise field names (engine uses action_type, UI may expect action)
        item["action"] = item.get("action") or item.get("action_type") or ""
        # datetime objects → epoch for JSON
        for k in ("requested_at", "expires_at"):
            v = item.get(k)
            if hasattr(v, "timestamp"):
                try:
                    item[k] = v.timestamp()
                except Exception:
                    item[k] = str(v)
        out.append(item)

    return {"pending": out, "count": len(out)}


# ---------------------------------------------------------------------------
# 5. POST /api/moderation/action — execute a moderation decision
# ---------------------------------------------------------------------------

@router.post("/action")
async def execute_moderation_action(
    req: ActionRequest,
    request: Request,
    user: dict = Depends(require_admin),
):
    """
    Execute a moderation action (warn, timeout, kick, ban, delete).
    - Logs to logs/moderation_actions.jsonl
    - Broadcasts via WebSocket
    """
    action_type = req.action_type.lower()
    valid_actions = {"warn", "timeout", "kick", "ban", "delete"}
    if action_type not in valid_actions:
        raise HTTPException(status_code=400, detail=f"Invalid action_type: {action_type}. Use: {sorted(valid_actions)}")

    engine = getattr(request.app.state, "agent", None)
    moderation = getattr(engine, "moderation", None) if engine else None
    bot = getattr(request.app.state, "bot", None)

    if not req.guild_id.isdigit():
        raise HTTPException(status_code=400, detail="guild_id must be a Discord guild ID")
    allowed_guilds = _allowed_web_guild_ids()
    if req.guild_id not in allowed_guilds:
        raise HTTPException(
            status_code=403,
            detail="Guild is not authorized for web dashboard mutations",
        )
    guild = bot.get_guild(int(req.guild_id)) if bot else None
    if guild is None:
        raise HTTPException(status_code=404, detail="Guild is not available to the bot")
    if not req.user_id.isdigit():
        raise HTTPException(status_code=400, detail="user_id must be a Discord user ID")
    member = guild.get_member(int(req.user_id))
    if member is None:
        raise HTTPException(status_code=404, detail="Member is not in the requested guild")

    channel = None
    if req.channel_id:
        if not req.channel_id.isdigit():
            raise HTTPException(status_code=400, detail="channel_id must be a Discord channel ID")
        channel = bot.get_channel(int(req.channel_id)) if bot else None
        if channel is None or getattr(getattr(channel, "guild", None), "id", None) != guild.id:
            raise HTTPException(status_code=400, detail="channel_id does not belong to guild_id")

    action_record = {
        "timestamp": time.time(),
        "guild_id": req.guild_id,
        "action_type": action_type,
        "user_id": req.user_id,
        "user_name": req.user_name or f"User {req.user_id[:8]}",
        "reason": req.reason or f"Admin {user.get('username', '?')} action",
        "severity": "admin_action",
        "confidence": 1.0,
        "executed": False,
        "dry_run": False,
        "channel_id": req.channel_id,
        "message_id": req.message_id,
        "admin_user": user.get("username", "unknown"),
        "source": "web_dashboard",
    }

    executed = False
    error_msg = ""

    # Try to execute via the real engine's ActionExecutor
    if moderation and hasattr(moderation, "actions") and bot:
        try:
            from azure.moderation.policy import ActionType as ModActionType

            action_map = {
                "warn": ModActionType.WARN,
                "timeout": ModActionType.TIMEOUT,
                "kick": ModActionType.KICK,
                "ban": ModActionType.BAN,
                "delete": ModActionType.DELETE,
            }
            mod_action = action_map.get(action_type)

            if member and mod_action:
                message_obj = None
                if req.message_id and req.message_id.isdigit() and channel:
                    with contextlib.suppress(Exception):
                        message_obj = await channel.fetch_message(int(req.message_id))

                from azure.moderation.reporter import ActionReport
                ActionReport(
                    timestamp=time.time(),
                    action_type=action_type,
                    target_user_id=req.user_id,
                    target_user_name=req.user_name or member.display_name,
                    target_message_id=req.message_id or "",
                    channel_id=req.channel_id,
                    channel_name=str(getattr(channel, "name", "unknown")),
                    severity="high",
                    category="admin_action",
                    reason=action_record["reason"],
                    confidence=1.0,
                    dry_run=False,
                    message_content="",
                )

                # Use the engine's action executor
                from azure.moderation.phase import action_allowed
                if action_allowed(moderation.policy.phase, action_type):
                    if action_type == "delete" and message_obj:
                        executed = bool(await moderation.actions.delete_message(message_obj, reason=action_record["reason"]))
                    elif action_type == "timeout" and member:
                        effective_timeout = min(req.duration_minutes, moderation.policy.get_effective_timeout_minutes())
                        executed = bool(await moderation.actions.timeout_member(
                            member, duration_minutes=effective_timeout, reason=action_record["reason"],
                        ))
                    elif action_type == "kick" and member:
                        executed = bool(await moderation.actions.kick_member(member, reason=action_record["reason"]))
                    elif action_type == "ban" and member:
                        executed = bool(await moderation.actions.ban_member(member, reason=action_record["reason"]))
                    elif action_type == "warn" and member:
                        executed = bool(await moderation.actions.warn_member(member, action_record["reason"], channel=channel))
                    if not executed:
                        error_msg = "Discord rejected or failed to complete the action"
                else:
                    error_msg = f"Action '{action_type}' not allowed in phase {moderation.policy.phase.value}"
        except Exception as e:
            error_msg = str(e)
            logger.error("Engine action execution failed: %s", e)
    else:
        error_msg = "Moderation engine or bot not available"

    action_record["executed"] = executed
    if error_msg:
        action_record["error"] = error_msg

    # --- Log to JSONL ---
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(ACTIONS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(action_record, default=str) + "\n")
    except Exception as e:
        logger.error("Failed to write action log: %s", e)

    # --- Broadcast via WebSocket ---
    try:
        await broadcast_event("moderation_action", {
            "action_type": action_type,
            "user_id": req.user_id,
            "user_name": action_record["user_name"],
            "reason": action_record["reason"],
            "admin": user.get("username", "unknown"),
            "executed": executed,
            "error": error_msg or None,
        })
    except Exception as e:
        logger.warning("WebSocket broadcast failed: %s", e)

    return {
        "status": "success" if executed else "failed",
        "executed": executed,
        "action": action_record,
        "error": error_msg or None,
    }


# ---------------------------------------------------------------------------
# 6. GET /api/moderation/security — security events from audit log
# ---------------------------------------------------------------------------

@router.get("/security")
async def get_security_events(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_admin),
):
    """Return security events from the audit log and moderation actions."""
    _set_request(request)
    events: list[dict] = []

    # --- From audit_logs (SQLite) ---
    audit_rows = _read_audit_log(limit=limit)
    for row in audit_rows:
        ts = row.get("timestamp", "")
        events.append({
            "timestamp": ts,
            "type": "audit_log",
            "action": row.get("action", row.get("event_type", "")),
            "user": row.get("target_user", row.get("user", "")),
            "detail": row.get("reason", row.get("detail", "")),
            "ip": row.get("ip_address", row.get("ip", "")),
            "severity": row.get("severity", "info"),
        })

    # --- From moderation_actions.jsonl (high-severity only) ---
    mod_actions = _read_actions_log(limit=limit * 2)
    for a in mod_actions:
        severity = a.get("severity", "none")
        action_type = a.get("action_type") or a.get("action", "")
        if severity in ("high", "critical") or action_type in ("kick", "ban"):
            ts = a.get("timestamp", 0)
            if isinstance(ts, (int, float)):
                try:
                    ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
                except Exception:
                    ts_str = str(ts)
            else:
                ts_str = str(ts)
            events.append({
                "timestamp": ts_str,
                "type": "moderation_action",
                "action": action_type,
                "user": a.get("user_name", a.get("user_id", "")),
                "detail": a.get("reason", ""),
                "severity": severity,
                "confidence": a.get("confidence", 0),
                "executed": a.get("executed", False),
            })

    # Sort newest first, cap to limit
    events.sort(key=lambda e: str(e.get("timestamp", "")), reverse=True)
    events = events[:limit]

    return {
        "total": len(events),
        "events": events,
    }


# ---------------------------------------------------------------------------
# Existing endpoints (preserved)
# ---------------------------------------------------------------------------

class ActionConfirmRequest(BaseModel):
    message_id: str


@router.get("/logs")
async def get_mod_logs(request: Request, limit: int = 100, user: dict = Depends(require_admin)):
    """Audit-log retrieval. Admin-only."""
    db = getattr(request.app.state, "db", None)
    if not db:
        return []
    with db._wlock:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]


@router.get("/queue")
async def get_confirmation_queue(request: Request, user: dict = Depends(require_admin)):
    """Pending confirmation queue. Admin-only."""
    return await get_pending_confirmations(request, user)


@router.post("/confirm")
async def confirm_action(req: ActionConfirmRequest, request: Request, user: dict = Depends(require_admin)):
    agent = getattr(request.app.state, "agent", None)
    if not agent or not agent.moderation:
        raise HTTPException(status_code=400, detail="Moderation engine offline")
    success, msg = await agent.moderation.confirm_action(req.message_id)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}


@router.post("/cancel")
async def cancel_action(req: ActionConfirmRequest, request: Request, user: dict = Depends(require_admin)):
    agent = getattr(request.app.state, "agent", None)
    if not agent or not agent.moderation:
        raise HTTPException(status_code=400, detail="Moderation engine offline")
    success = agent.moderation.cancel_action(req.message_id)
    if not success:
        raise HTTPException(status_code=404, detail="Action not found in queue")
    return {"status": "success", "message": "Action cancelled."}
