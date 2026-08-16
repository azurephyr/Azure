"""Dashboard API endpoints for the web control center.

Provides stats, providers, models, logs, users, moderation, health history,
errors, and activity timeline for the frontend dashboard pages.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from .api_auth import get_current_user, require_admin

router = APIRouter()
logger = logging.getLogger("web.dashboard")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_agent(request: Request):
    return getattr(request.app.state, "agent", None)


def _get_bot(request: Request):
    return getattr(request.app.state, "bot", None)


def _get_db(request: Request):
    return getattr(request.app.state, "db", None)


WEB_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = WEB_DIR.parent
_DATA_DIR = PROJECT_ROOT / "data"
_LOGS_DIR = PROJECT_ROOT / "logs"
_CONFIGS_DIR = PROJECT_ROOT / "configs"
_MODEL_HEALTH_PATH = _CONFIGS_DIR / "model_health.json"


def _model_health_path() -> Path:
    return Path(os.environ.get("AZURE_MODEL_HEALTH", str(_MODEL_HEALTH_PATH)))


def _load_model_health() -> dict:
    p = _model_health_path()
    if not p.exists():
        p = _MODEL_HEALTH_PATH
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read model_health.json: %s", exc)
    return {"providers": {}, "settings": {}, "saved_at": ""}


# ---------------------------------------------------------------------------
# 1. GET /api/stats — Bot statistics
# ---------------------------------------------------------------------------

@router.get("/stats")
async def get_stats(request: Request, user: dict = Depends(get_current_user)):
    messages_today = 0
    active_users = 0
    llm_calls = 0
    errors = 0

    db = getattr(request.app.state, "db", None)
    if db:
        try:
            stats = db.get_aggregate_stats(hours=24)
            messages_today = stats.get("total_messages", 0)
            active_users = stats.get("peak_users", 0)
            llm_calls = stats.get("total_tokens", 0)
            errors = stats.get("total_errors", 0)
        except Exception as exc:
            logger.debug("get_aggregate_stats failed: %s", exc)

    mod_stats = {}
    agent = getattr(request.app.state, "agent", None)
    if agent and hasattr(agent, "get_moderation_stats"):
        with contextlib.suppress(Exception):
            mod_stats = agent.get_moderation_stats()

    active_moderations = mod_stats.get("pending_actions", 0) if isinstance(mod_stats, dict) else 0

    health_score = 100
    if messages_today > 0 and errors > 0:
        error_rate = errors / messages_today
        health_score = max(0, int(100 - (error_rate * 100)))
    elif errors > 0:
        health_score = 50

    return {
        "messages_today": messages_today,
        "active_users": active_users,
        "llm_calls": llm_calls,
        "health_score": health_score,
        "active_moderations": active_moderations,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 2. GET /api/providers — All 7 providers with status
# ---------------------------------------------------------------------------

PROVIDER_NAMES = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google AI Studio",
    "groq": "Groq",
    "mistral": "Mistral AI",
    "openrouter": "OpenRouter",
    "nararouter": "NaraRouter",
}

@router.get("/providers")
async def get_providers(request: Request, user: dict = Depends(get_current_user)):
    health = _load_model_health()
    provider_health = health.get("providers", {})

    try:
        from azure.model_catalog import PROVIDER_CATALOGS as provider_catalogs  # noqa: N811
    except ImportError:
        try:
            provider_catalogs = {}
            import azure.model_catalog as _mc
            if hasattr(_mc, "PROVIDER_CATALOGS"):
                provider_catalogs = _mc.PROVIDER_CATALOGS
        except ImportError:
            provider_catalogs = {}

    providers = []
    for pid, display_name in PROVIDER_NAMES.items():
        hp = provider_health.get(pid, {})
        catalog = provider_catalogs.get(pid, {})
        model_count = len(catalog.get("models", []))

        has_api_key = hp.get("has_api_key", False)
        consecutive_failures = hp.get("consecutive_failures", 0)
        last_success = hp.get("last_success_time", 0.0)
        last_failure = hp.get("last_failure_time", 0.0)

        if not has_api_key:
            status = "unconfigured"
        elif consecutive_failures > 3:
            status = "down"
        elif consecutive_failures > 0:
            status = "degraded"
        elif last_success > 0:
            status = "healthy"
        else:
            status = "unknown"

        providers.append({
            "id": pid,
            "name": display_name,
            "status": status,
            "health": {
                "success_count": hp.get("success_count", 0),
                "failure_count": hp.get("failure_count", 0),
                "consecutive_failures": consecutive_failures,
                "last_error": hp.get("last_error", ""),
                "last_error_time": hp.get("last_error_time", 0.0),
                "last_success_time": last_success,
                "tier": hp.get("tier", "unknown"),
                "rpm_limit": hp.get("rpm_limit", 0),
                "rpm_remaining": hp.get("rpm_remaining", 0),
                "has_api_key": has_api_key,
            },
            "model_count": model_count,
            "last_check": max(last_success, last_failure),
        })

    return providers


# ---------------------------------------------------------------------------
# 3. GET /api/models — Full model catalog with usage stats
# ---------------------------------------------------------------------------

@router.get("/models")
async def get_models(
    request: Request,
    provider: str | None = Query(None, description="Filter by provider ID"),
    free_only: bool = Query(False, description="Only free-tier models"),
    user: dict = Depends(get_current_user),
):
    try:
        from azure.model_catalog import PROVIDER_CATALOGS
    except ImportError:
        return {"providers": [], "total_models": 0}

    result_providers = []
    total = 0

    for pid, cat in PROVIDER_CATALOGS.items():
        if provider and pid != provider:
            continue

        models = cat.get("models", [])
        if free_only:
            models = [m for m in models if m.free_tier]

        model_list = []
        for m in models:
            model_list.append({
                "id": m.id,
                "name": m.name,
                "context_window": m.context_window,
                "input_price": m.input_price,
                "output_price": m.output_price,
                "free_tier": m.free_tier,
                "max_output": m.max_output,
                "description": m.description,
                "label": m.label,
            })

        total += len(model_list)
        result_providers.append({
            "id": pid,
            "name": cat.get("display_name", pid),
            "model_count": len(model_list),
            "models": model_list,
        })

    return {
        "providers": result_providers,
        "total_models": total,
    }


# ---------------------------------------------------------------------------
# 4. GET /api/logs — Recent log entries with filtering
# ---------------------------------------------------------------------------

@router.get("/logs")
async def get_logs(
    request: Request,
    level: str | None = Query(None, description="Filter by log level: INFO, WARNING, ERROR"),
    module: str | None = Query(None, description="Filter by module name"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    db = getattr(request.app.state, "db", None)
    if not db:
        return {"logs": [], "total": 0}

    try:
        with db._wlock:
            conn = db._get_connection()
            cursor = conn.cursor()

            query = "SELECT * FROM telemetry_logs WHERE 1=1"
            params: list = []

            if level:
                query += " AND status = ?"
                params.append(level.lower())

            if module:
                query += " AND subsystem LIKE ?"
                params.append(f"%{module}%")

            count_query = query.replace("SELECT *", "SELECT COUNT(*)")
            cursor.execute(count_query, params)
            total = cursor.fetchone()[0]

            query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor.execute(query, params)
            rows = cursor.fetchall()

        logs = []
        for row in rows:
            logs.append({
                "id": row["id"],
                "execution_id": row["execution_id"],
                "timestamp": row["timestamp"],
                "subsystem": row["subsystem"],
                "action": row["action"],
                "message": row["message"],
                "status": row["status"],
            })

        return {"logs": logs, "total": total}
    except Exception as exc:
        logger.warning("get_logs failed: %s", exc)
        return {"logs": [], "total": 0}


# ---------------------------------------------------------------------------
# 5. GET /api/users — User data with activity stats
# ---------------------------------------------------------------------------

@router.get("/users")
async def get_users(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    db = getattr(request.app.state, "db", None)
    if not db:
        return {"users": [], "total": 0}

    try:
        with db._wlock:
            conn = db._get_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM user_preferences ORDER BY updated_at DESC LIMIT ?", (limit,))
            pref_rows = cursor.fetchall()

            cursor.execute("""
                SELECT user_id, user_name,
                       COUNT(*) as message_count,
                       MAX(timestamp) as last_active,
                       SUM(tokens_used) as total_tokens
                FROM conversation_history
                GROUP BY user_id
                ORDER BY message_count DESC
                LIMIT ?
            """, (limit,))
            activity_rows = cursor.fetchall()

        activity_map = {}
        for row in activity_rows:
            activity_map[row["user_id"]] = {
                "message_count": row["message_count"],
                "last_active": row["last_active"],
                "total_tokens": row["total_tokens"] or 0,
            }

        users = []
        seen = set()

        for row in pref_rows:
            uid = row["user_id"]
            seen.add(uid)
            activity = activity_map.get(uid, {})
            users.append({
                "user_id": uid,
                "user_name": row["user_name"],
                "tier": row["tier"],
                "language": row["language"],
                "disabled": bool(row["disabled"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "message_count": activity.get("message_count", 0),
                "last_active": activity.get("last_active", 0),
                "total_tokens": activity.get("total_tokens", 0),
            })

        for uid, activity in activity_map.items():
            if uid not in seen:
                seen.add(uid)
                users.append({
                    "user_id": uid,
                    "user_name": uid,
                    "tier": "free",
                    "language": "en",
                    "disabled": False,
                    "created_at": 0,
                    "updated_at": 0,
                    "message_count": activity.get("message_count", 0),
                    "last_active": activity.get("last_active", 0),
                    "total_tokens": activity.get("total_tokens", 0),
                })

        total = len(users)
        return {"users": users, "total": total}
    except Exception as exc:
        logger.warning("get_users failed: %s", exc)
        return {"users": [], "total": 0}


# ---------------------------------------------------------------------------
# 6. GET /api/moderation/stats — Moderation statistics
# ---------------------------------------------------------------------------

@router.get("/moderation/stats")
async def get_moderation_stats(request: Request, user: dict = Depends(get_current_user)):
    agent = getattr(request.app.state, "agent", None)
    db = getattr(request.app.state, "db", None)

    mod_stats = {}
    if agent and hasattr(agent, "get_moderation_stats"):
        with contextlib.suppress(Exception):
            mod_stats = agent.get_moderation_stats()

    security_events = []
    if db:
        try:
            with db._wlock:
                conn = db._get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT event_type, COUNT(*) as count
                    FROM security_events
                    WHERE timestamp > ?
                    GROUP BY event_type
                """, (time.time() - 86400,))
                for row in cursor.fetchall():
                    security_events.append({
                        "type": row["event_type"],
                        "count": row["count"],
                    })
        except Exception as exc:
            logger.debug("security_events query failed: %s", exc)

    pending = []
    if agent and agent.moderation and hasattr(agent.moderation, "list_pending_confirmations"):
        try:
            raw = agent.moderation.list_pending_confirmations()
            for p in (raw or []):
                if isinstance(p, dict):
                    item = dict(p)
                    item["action"] = item.get("action") or item.get("action_type") or ""
                    pending.append(item)
        except Exception:
            logger.exception("[api_dashboard] pending confirmations fetch failed")

    return {
        "stats": mod_stats,
        "security_events_24h": security_events,
        "pending_actions": pending,
        "pending_count": len(pending),
    }


# ---------------------------------------------------------------------------
# 7. POST /api/provider/test — Test a provider connection
# ---------------------------------------------------------------------------

class ProviderTestRequest(BaseModel):
    provider: str

@router.post("/provider/test")
async def test_provider(
    req: ProviderTestRequest,
    request: Request,
    user: dict = Depends(require_admin),
):
    provider_id = req.provider.lower().strip()

    try:
        from azure.model_catalog import PROVIDER_CATALOGS as provider_catalogs  # noqa: N811
    except ImportError:
        try:
            provider_catalogs = {}
            import azure.model_catalog as _mc
            if hasattr(_mc, "PROVIDER_CATALOGS"):
                provider_catalogs = _mc.PROVIDER_CATALOGS
        except ImportError:
            provider_catalogs = {}

    if provider_id not in provider_catalogs:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_id}")

    cat = provider_catalogs[provider_id]
    api_key_envs = cat.get("api_key_envs", ())
    has_key = any(os.environ.get(env) for env in api_key_envs)

    if not has_key:
        return {
            "provider": provider_id,
            "status": "unconfigured",
            "message": f"No API key found. Set one of: {', '.join(api_key_envs)}",
            "latency_ms": 0,
        }

    # Try a lightweight API call
    start = time.time()
    try:
        if provider_id == "openai":
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"},
                )
                if resp.status_code == 200:
                    latency = int((time.time() - start) * 1000)
                    return {"provider": provider_id, "status": "healthy", "message": "Connection OK", "latency_ms": latency}
                else:
                    latency = int((time.time() - start) * 1000)
                    return {"provider": provider_id, "status": "error", "message": f"HTTP {resp.status_code}", "latency_ms": latency}
        elif provider_id == "anthropic":
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
                        "anthropic-version": "2023-06-01",
                    },
                )
                latency = int((time.time() - start) * 1000)
                if resp.status_code == 200:
                    return {"provider": provider_id, "status": "healthy", "message": "Connection OK", "latency_ms": latency}
                return {"provider": provider_id, "status": "error", "message": f"HTTP {resp.status_code}", "latency_ms": latency}
        elif provider_id == "google":
            import httpx
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models", headers={"X-Goog-Api-Key": api_key},
                )
                latency = int((time.time() - start) * 1000)
                if resp.status_code == 200:
                    return {"provider": provider_id, "status": "healthy", "message": "Connection OK", "latency_ms": latency}
                return {"provider": provider_id, "status": "error", "message": f"HTTP {resp.status_code}", "latency_ms": latency}
        elif provider_id in ("groq", "openrouter", "nararouter", "mistral"):
            import httpx
            # All use OpenAI-compatible endpoints
            base_urls = {
                "groq": "https://api.groq.com/openai",
                "openrouter": "https://openrouter.ai/api/v1",
                "nararouter": os.environ.get(
                    "AZURE_NARAROUTER_API_BASE", "https://router.bynara.id/v1"
                ),
                "mistral": "https://api.mistral.ai/v1",
            }
            key_envs = {
                "groq": "GROQ_API_KEY",
                "openrouter": "OPENROUTER_API_KEY",
                "nararouter": "AZURE_NARAROUTER_API_KEY",
                "mistral": "MISTRAL_API_KEY",
            }
            base = base_urls[provider_id]
            key = os.environ.get(key_envs[provider_id], "")
            if provider_id == "nararouter":
                key = key or os.environ.get("NARAROUTER_API_KEY", "")
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{base}/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                latency = int((time.time() - start) * 1000)
                if resp.status_code == 200:
                    return {"provider": provider_id, "status": "healthy", "message": "Connection OK", "latency_ms": latency}
                return {"provider": provider_id, "status": "error", "message": f"HTTP {resp.status_code}", "latency_ms": latency}
        else:
            latency = int((time.time() - start) * 1000)
            return {"provider": provider_id, "status": "unknown", "message": "Test not implemented for this provider", "latency_ms": latency}
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        return {"provider": provider_id, "status": "error", "message": str(e), "latency_ms": latency}


# ---------------------------------------------------------------------------
# 8. POST /api/provider/config — Update provider configuration
# ---------------------------------------------------------------------------

class ProviderConfigRequest(BaseModel):
    provider: str
    api_key: str | None = None
    model: str | None = None

@router.post("/provider/config")
async def update_provider_config(
    req: ProviderConfigRequest,
    request: Request,
    user: dict = Depends(require_admin),
):
    provider_id = req.provider.lower().strip()

    try:
        from azure.model_catalog import PROVIDER_CATALOGS as provider_catalogs  # noqa: N811
    except ImportError:
        raise HTTPException(status_code=500, detail="Model catalog not available") from None

    if provider_id not in provider_catalogs:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_id}")

    # Only allow setting via environment for now (API keys shouldn't persist to DB)
    # Log the config change for audit purposes
    updates = {}
    if req.api_key:
        updates["api_key"] = "(set via env)"
    if req.model:
        updates["model"] = req.model

    # Write to model_health.json settings if model is provided
    if req.model:
        health = _load_model_health()
        settings = dict(health.get("settings", {}))
        settings["provider"] = provider_id
        settings["model"] = req.model
        health["settings"] = settings
        try:
            _model_health_path().write_text(json.dumps(health, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to write config: {e}") from e

    return {
        "status": "success",
        "provider": provider_id,
        "updates": updates,
        "message": "Provider config updated. API keys should be set via environment variables.",
    }


# ---------------------------------------------------------------------------
# 9. GET /api/health/history — Health check history
# ---------------------------------------------------------------------------

@router.get("/health/history")
async def get_health_history(
    request: Request,
    hours: int = Query(24, ge=1, le=168),
    user: dict = Depends(get_current_user),
):
    db = _get_db(request)
    history = []

    if db:
        stats = db.get_stats_history(hours=hours, limit=500)
        for s in stats:
            history.append({
                "timestamp": s.timestamp,
                "messages_processed": s.messages_processed,
                "cache_hits": s.cache_hits,
                "cache_misses": s.cache_misses,
                "errors": s.errors,
                "avg_response_time_ms": s.avg_response_time_ms,
                "total_tokens_used": s.total_tokens_used,
                "active_users": s.active_users,
                "active_servers": s.active_servers,
            })

    # Also include model_health.json provider states
    health = _load_model_health()
    provider_states = health.get("providers", {})

    return {
        "history": history,
        "providers": provider_states,
        "settings": health.get("settings", {}),
        "saved_at": health.get("saved_at", ""),
    }


# ---------------------------------------------------------------------------
# 10. GET /api/errors — Recent errors with context
# ---------------------------------------------------------------------------

@router.get("/errors")
async def get_errors(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    user: dict = Depends(get_current_user),
):
    db = _get_db(request)
    errors = []

    if db:
        with db._wlock:
            conn = db._get_connection()
            cursor = conn.cursor()

            # From telemetry_logs
            cursor.execute("""
                SELECT * FROM telemetry_logs
                WHERE status = 'error' OR action = 'ERROR'
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))
            for row in cursor.fetchall():
                errors.append({
                    "id": row["id"],
                    "execution_id": row["execution_id"],
                    "timestamp": row["timestamp"],
                    "subsystem": row["subsystem"],
                    "action": row["action"],
                    "message": row["message"],
                    "status": row["status"],
                    "source": "telemetry",
                })

            # From security_events
            cursor.execute("""
                SELECT * FROM security_events
                WHERE severity IN ('high', 'critical')
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))
            for row in cursor.fetchall():
                errors.append({
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "user_id": row["user_id"],
                    "guild_id": row["guild_id"],
                    "subsystem": row["event_type"],
                    "action": row["event_type"],
                    "message": row["details"],
                    "status": row["severity"],
                    "source": "security",
                })

    # Sort by timestamp descending and limit
    errors.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    errors = errors[:limit]

    return {"errors": errors, "total": len(errors)}


# ---------------------------------------------------------------------------
# 11. GET /api/activity — Recent bot activity timeline
# ---------------------------------------------------------------------------

@router.get("/activity")
async def get_activity(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    db = _get_db(request)
    activity = []

    if db:
        conn = db._get_connection()
        cursor = conn.cursor()

        # Recent conversations
        cursor.execute("""
            SELECT user_name, server_name, channel_name, message, response,
                   timestamp, cached, tokens_used, response_time_ms
            FROM conversation_history
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        for row in cursor.fetchall():
            activity.append({
                "type": "message",
                "user": row["user_name"],
                "server": row["server_name"],
                "channel": row["channel_name"],
                "preview": (row["message"] or "")[:200],
                "response_preview": (row["response"] or "")[:200],
                "timestamp": row["timestamp"],
                "cached": bool(row["cached"]),
                "tokens": row["tokens_used"],
                "latency_ms": row["response_time_ms"],
            })

        # Recent audit events
        cursor.execute("""
            SELECT user_name, action, subsystem, timestamp
            FROM audit_logs
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit // 2,))
        for row in cursor.fetchall():
            activity.append({
                "type": "audit",
                "user": row["user_name"],
                "action": row["action"],
                "subsystem": row["subsystem"],
                "timestamp": row["timestamp"],
            })

    # Sort by timestamp descending
    activity.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    activity = activity[:limit]

    return {"activity": activity, "total": len(activity)}
