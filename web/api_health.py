import contextlib
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil
from fastapi import APIRouter, Depends, Query, Request

from .api_auth import get_current_user, require_admin

logger = logging.getLogger("azure.web.health")

router = APIRouter()

START_TIME = time.time()

try:
    from azure.model_selector import ALL_PROVIDERS, PROVIDER_CATALOGS, ModelSelector
except ImportError:
    ModelSelector = None
    ALL_PROVIDERS = []
    PROVIDER_CATALOGS = {}

try:
    from azure.model_catalog import get_models_for_provider
except ImportError:
    get_models_for_provider = None

HEALTH_FILE = Path(__file__).parent.parent / "configs" / "model_health.json"

_selector = ModelSelector() if ModelSelector is not None else None


def _read_health_file() -> dict:
    try:
        return json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"providers": {}, "settings": {}, "saved_at": ""}


def _get_key_status(provider: str) -> dict:
    cat = PROVIDER_CATALOGS.get(provider, {})
    envs = cat.get("api_key_envs", ())
    for name in envs:
        val = os.environ.get(name, "").strip()
        if val:
            return {"set": True, "env": name, "masked": val[:4] + "..." + val[-4:] if len(val) > 8 else "***"}
    return {"set": False, "env": envs[0] if envs else "", "masked": ""}


def _provider_status(health: dict) -> str:
    if not health.get("has_api_key"):
        return "no_key"
    if health.get("consecutive_failures", 0) >= 5:
        return "down"
    if health.get("consecutive_failures", 0) > 0:
        return "degraded"
    return "healthy"


def _compute_success_rate(health: dict, hours: float = 24) -> float:
    total = health.get("success_count", 0) + health.get("failure_count", 0)
    if total == 0:
        return 0.0
    now = time.time()
    window = hours * 3600
    last_success = health.get("last_success_time", 0.0)
    last_failure = health.get("last_failure_time", 0.0)
    if last_success < now - window and last_failure < now - window:
        return 0.0
    return health.get("success_count", 0) / total


def _get_system_info() -> dict:
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()

    try:
        disk_path = os.path.splitdrive(os.getcwd())[0] + "\\" if os.name == "nt" else "/"
        disk = psutil.disk_usage(disk_path)
    except Exception:
        disk = psutil.disk_usage("/")

    # Match the actual default path used by DatabaseManager (database.py line 151)
    db_path = Path(__file__).parent.parent / "data" / "azure_bot.db"
    db_size = 0
    if db_path.exists():
        with contextlib.suppress(Exception):
            db_size = db_path.stat().st_size

    return {
        "uptime_seconds": int(time.time() - START_TIME),
        "uptime_human": _format_uptime(time.time() - START_TIME),
        "memory": {
            "used_mb": round(mem_info.rss / (1024 * 1024), 1),
            "available_mb": round(psutil.virtual_memory().available / (1024 * 1024), 1),
            "percent": psutil.virtual_memory().percent,
        },
        "cpu_percent": process.cpu_percent(interval=0.1),
        "disk": {
            "total_gb": round(disk.total / (1024 ** 3), 1),
            "used_gb": round(disk.used / (1024 ** 3), 1),
            "percent": disk.percent,
        },
        "database": {
            "size_bytes": db_size,
            "size_human": _format_bytes(db_size),
        },
        "threads": process.num_threads(),
    }


def _format_uptime(seconds: float) -> str:
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _get_all_provider_health() -> dict:
    """Return provider health from ModelSelector or fallback to JSON file."""
    if _selector is not None:
        return _selector.get_provider_health()
    health_file = _read_health_file()
    return health_file.get("providers", {})


def _get_provider_display_name(provider: str) -> str:
    if _selector is not None:
        return _selector.get_provider_display_name(provider)
    cat = PROVIDER_CATALOGS.get(provider, {})
    return cat.get("display_name", provider.title())


def _build_alerts() -> list[dict]:
    alerts = []
    now = time.time()
    health_data = _get_all_provider_health()

    for provider, h in health_data.items():
        display = _get_provider_display_name(provider)

        if not h.get("has_api_key"):
            alerts.append({
                "severity": "warning",
                "provider": provider,
                "provider_name": display,
                "message": f"{display}: No API key configured",
                "timestamp": now,
            })
            continue

        consecutive = h.get("consecutive_failures", 0)
        if consecutive >= 5:
            alerts.append({
                "severity": "critical",
                "provider": provider,
                "provider_name": display,
                "message": f"{display}: Down ({consecutive} consecutive failures)",
                "timestamp": h.get("last_failure_time", now),
            })
        elif consecutive > 0:
            alerts.append({
                "severity": "warning",
                "provider": provider,
                "provider_name": display,
                "message": f"{display}: Degraded ({consecutive} recent failures)",
                "timestamp": h.get("last_failure_time", now),
            })

        total = h.get("success_count", 0) + h.get("failure_count", 0)
        if total >= 10:
            rate = h.get("success_count", 0) / total
            if rate < 0.5:
                alerts.append({
                    "severity": "warning",
                    "provider": provider,
                    "provider_name": display,
                    "message": f"{display}: Low success rate ({rate:.0%})",
                    "timestamp": now,
                })

    return sorted(alerts, key=lambda a: a["timestamp"], reverse=True)


def _public_health(request: Request) -> dict:
    from bot.context import ctx

    readiness = ctx.readiness_summary()
    status = "online" if readiness.get("ready") else "degraded"
    health_file = {"providers": {}, "settings": {}, "saved_at": ""}
    provider_health = {}
    providers_summary = {}
    system_info = {}
    try:
        health_file = _read_health_file()
    except Exception as e:
        logger.warning("[health] Failed to read health file: %s", e)
        status = "degraded"
    try:
        provider_health = _get_all_provider_health()
        for name, h in provider_health.items():
            providers_summary[name] = {
                "status": _provider_status(h),
                "has_api_key": h.get("has_api_key", False),
                "consecutive_failures": h.get("consecutive_failures", 0),
                "success_rate": round(_compute_success_rate(h, 24), 2),
            }
    except Exception as e:
        logger.warning("[health] Failed to get provider health: %s", e)
        status = "degraded"
    try:
        system_info = {
            "cpu_percent": psutil.Process(os.getpid()).cpu_percent(),
            "memory_mb": psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024),
            "threads": psutil.Process(os.getpid()).num_threads(),
        }
    except Exception as e:
        logger.warning("[health] Failed to get system info: %s", e)
        system_info = {}
    return {
        "status": status,
        "ready": bool(readiness.get("ready")),
        "discord_connected": bool(readiness.get("discord_connected")),
        "uptime_seconds": int(time.time() - START_TIME),
        "saved_at": health_file.get("saved_at", ""),
        "providers": providers_summary,
        "settings": health_file.get("settings", {}),
        "system": system_info,
    }


@router.get("/")
async def get_system_health(request: Request):
    """Unauthenticated liveness probe."""
    return _public_health(request)


@router.get("/detailed")
async def get_system_health_detailed(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Authenticated health with agent / moderation / database internals."""
    base = _public_health(request)
    agent = getattr(request.app.state, "agent", None)
    db = getattr(request.app.state, "db", None)

    agent_info = {}
    mod_stats = {}
    db_stats = {}
    try:
        if agent is not None and hasattr(agent, "get_info"):
            agent_info = agent.get_info()
    except Exception as e:
        logger.warning("[health] Failed to get agent info: %s", e)
        base["status"] = "degraded"
    try:
        if agent is not None and hasattr(agent, "get_moderation_stats"):
            mod_stats = agent.get_moderation_stats()
    except Exception as e:
        logger.warning("[health] Failed to get moderation stats: %s", e)
    try:
        if db is not None and hasattr(db, "get_aggregate_stats"):
            db_stats = db.get_aggregate_stats(hours=24)
    except Exception as e:
        logger.warning("[health] Failed to get db stats: %s", e)

    base.update(
        {
            "agent": agent_info,
            "moderation": mod_stats,
            "database": db_stats,
            "viewer": user.get("username"),
        }
    )
    return base


@router.get("/providers")
async def get_provider_details(user: dict = Depends(get_current_user)):
    """Return detailed info for each provider."""
    health_file = _read_health_file()
    provider_health = _get_all_provider_health()
    all_models = _selector.get_all_models() if _selector is not None else {}

    providers = []
    for name in ALL_PROVIDERS:
        h = provider_health.get(name, {})
        display = _get_provider_display_name(name)
        key_status = _get_key_status(name)
        status = _provider_status(h)
        models = all_models.get(name, {})

        providers.append({
            "name": name,
            "display_name": display,
            "base_url": _get_base_url(name),
            "api_key": key_status,
            "status": status,
            "has_api_key": h.get("has_api_key", False),
            "last_check": h.get("last_success_time") or h.get("last_failure_time") or 0,
            "last_check_human": _timestamp_to_human(h.get("last_success_time") or h.get("last_failure_time") or 0),
            "success_rate": round(_compute_success_rate(h, 24), 2),
            "success_count": h.get("success_count", 0),
            "failure_count": h.get("failure_count", 0),
            "consecutive_failures": h.get("consecutive_failures", 0),
            "avg_response_time": _estimate_avg_latency(h),
            "tier": h.get("tier", "unknown"),
            "rpm_limit": h.get("rpm_limit", 60),
            "rpm_remaining": h.get("rpm_remaining", 60),
            "available_models": {
                "total": len(models.get("all", [])),
                "free": len(models.get("free", [])),
            },
            "last_error": h.get("last_error", ""),
        })

    active = _selector.select_provider() if _selector is not None else health_file.get("settings", {}).get("provider", "")
    return {"providers": providers, "active_provider": active}


@router.post("/check")
async def trigger_health_check(user: dict = Depends(require_admin)):
    """Trigger immediate health check of all providers."""
    import asyncio
    results = {}
    if _selector is not None:
        for provider in ALL_PROVIDERS:
            try:
                result = await asyncio.get_running_loop().run_in_executor(
                    None, _selector.test_provider, provider
                )
                results[provider] = result
            except Exception as e:
                results[provider] = {"success": False, "error": str(e), "provider": provider}
    else:
        for provider in ALL_PROVIDERS:
            results[provider] = {"success": False, "error": "ModelSelector not available", "provider": provider}

    health_file = _read_health_file()
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "results": results,
        "saved_at": health_file.get("saved_at", ""),
    }


@router.get("/history")
async def get_health_history(
    hours: int = Query(default=24, ge=1, le=168),
    user: dict = Depends(get_current_user),
):
    """Return health check history for charting."""
    health_file = _read_health_file()
    now = time.time()
    cutoff = now - (hours * 3600)

    provider_health = _get_all_provider_health()
    history = {}

    for provider in ALL_PROVIDERS:
        h = provider_health.get(provider, {})
        display = _get_provider_display_name(provider)

        events = []
        if h.get("last_success_time", 0) > cutoff:
            events.append({
                "type": "success",
                "timestamp": h["last_success_time"],
                "provider": provider,
            })
        if h.get("last_failure_time", 0) > cutoff:
            events.append({
                "type": "failure",
                "timestamp": h["last_failure_time"],
                "provider": provider,
                "error": h.get("last_error", ""),
            })

        h.get("success_count", 0) + h.get("failure_count", 0)
        rate = _compute_success_rate(h, hours)

        history[provider] = {
            "display_name": display,
            "status": _provider_status(h),
            "success_rate": round(rate, 2),
            "success_count": h.get("success_count", 0),
            "failure_count": h.get("failure_count", 0),
            "events": sorted(events, key=lambda e: e["timestamp"]),
        }

    return {
        "hours": hours,
        "history": history,
        "saved_at": health_file.get("saved_at", ""),
    }


@router.get("/system")
async def get_system_stats(user: dict = Depends(get_current_user)):
    """Return system resource metrics."""
    return _get_system_info()


@router.get("/alerts")
async def get_health_alerts(user: dict = Depends(get_current_user)):
    """Return current health alerts."""
    return {"alerts": _build_alerts()}


def _get_base_url(provider: str) -> str:
    urls = {
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com/v1",
        "google": "https://generativelanguage.googleapis.com/v1beta",
        "groq": "https://api.groq.com/openai/v1",
        "mistral": "https://api.mistral.ai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "nararouter": "https://router.bynara.id/v1",
    }
    return urls.get(provider, "")


def _timestamp_to_human(ts: float) -> str:
    if not ts:
        return "never"
    diff = time.time() - ts
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff // 60)}m ago"
    if diff < 86400:
        return f"{int(diff // 3600)}h ago"
    return f"{int(diff // 86400)}d ago"


def _estimate_avg_latency(health: dict) -> float:
    """Return last known latency from health data, or 0.0 if unavailable."""
    return health.get("last_latency", 0.0) or 0.0
