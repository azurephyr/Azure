import json
import logging
import time
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from .api_auth import get_current_user

logger = logging.getLogger("web.analytics")

router = APIRouter()

@router.get("/timeseries")
async def get_timeseries_data(request: Request, hours: int = 24, user: dict = Depends(get_current_user)):
    """Fetch historical timeseries data for charts."""
    db = getattr(request.app.state, "db", None)
    if not db:
        return {"error": "Database not connected"}

    # We want roughly hourly data points. If there are many, we could bucket them,
    # but for simplicity we return the raw history and let the frontend format it.
    history = db.get_stats_history(hours=hours, limit=100)

    # Sort chronological for charts (oldest first)
    history.sort(key=lambda x: x.timestamp)

    labels = []
    messages = []
    latency = []
    cache_hits = []
    cache_misses = []
    tokens = []

    for h in history:
        labels.append(h.timestamp)
        messages.append(h.messages_processed)
        latency.append(h.avg_response_time_ms)
        cache_hits.append(h.cache_hits)
        cache_misses.append(h.cache_misses)
        tokens.append(h.total_tokens_used)

    return {
        "labels": labels,
        "messages": messages,
        "latency": latency,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "tokens": tokens
    }

@router.get("/servers")
async def get_servers_data(request: Request, user: dict = Depends(get_current_user)):
    """Fetch active discord server details."""
    bot = getattr(request.app.state, "bot", None)
    if not bot:
        return []

    servers = []
    for guild in bot.guilds:
        servers.append({
            "id": str(guild.id),
            "name": guild.name,
            "member_count": guild.member_count or 0,
            "icon_url": guild.icon.url if guild.icon else None,
            "created_at": guild.created_at.timestamp()
        })

    # Sort by member count descending
    servers.sort(key=lambda x: x["member_count"], reverse=True)
    return servers


# ---------------------------------------------------------------------------
# Deep Analytics Endpoints
# ---------------------------------------------------------------------------

@router.get("/message-volume")
async def get_message_volume(request: Request, days: int = 7, user: dict = Depends(get_current_user)):
    """Hourly and daily message volume from conversation_history."""
    db = getattr(request.app.state, "db", None)
    if not db:
        return {"hourly": {}, "daily": {}}

    since = time.time() - (days * 86400)
    msgs = db.get_conversation_history(limit=50000, since=since)

    hourly = Counter()
    daily = Counter()
    for m in msgs:
        t = m.timestamp
        local = time.localtime(t)
        hour_key = time.strftime("%Y-%m-%d %H:00", local)
        day_key = time.strftime("%Y-%m-%d", local)
        hourly[hour_key] += 1
        daily[day_key] += 1

    return {
        "hourly": dict(sorted(hourly.items())),
        "daily": dict(sorted(daily.items())),
    }


@router.get("/provider-performance")
async def get_provider_performance(request: Request, user: dict = Depends(get_current_user)):
    """Provider health, response times, success/fail from model_health.json."""
    health_path = Path(__file__).resolve().parent.parent / "configs" / "model_health.json"
    providers = {}
    if health_path.exists():
        try:
            data = json.loads(health_path.read_text(encoding="utf-8"))
            for name, info in data.get("providers", {}).items():
                total = info.get("success_count", 0) + info.get("failure_count", 0)
                success = info.get("success_count", 0)
                failure = info.get("failure_count", 0)
                rate = (success / total * 100) if total > 0 else 0
                providers[name] = {
                    "success": success,
                    "failure": failure,
                    "total": total,
                    "success_rate": round(rate, 1),
                    "consecutive_failures": info.get("consecutive_failures", 0),
                    "has_api_key": info.get("has_api_key", False),
                    "tier": info.get("tier", "unknown"),
                    "rpm_limit": info.get("rpm_limit", 0),
                    "rpm_remaining": info.get("rpm_remaining", 0),
                    "last_success": info.get("last_success_time", 0),
                    "last_failure": info.get("last_failure_time", 0),
                    "last_error": info.get("last_error", ""),
                }
        except Exception:
            logger.exception("[api_analytics] provider health lookup failed")

    # Also pull avg response times from bot_stats
    db = getattr(request.app.state, "db", None)
    avg_latency = 0
    if db:
        try:
            agg = db.get_aggregate_stats(hours=24)
            avg_latency = round(agg.get("avg_response_time", 0), 1)
        except Exception:
            logger.exception("[api_analytics] aggregate stats lookup failed")

    return {"providers": providers, "avg_latency_ms": avg_latency}


@router.get("/moderation-stats")
async def get_moderation_stats(request: Request, user: dict = Depends(get_current_user)):
    """Moderation actions from moderation_actions.jsonl."""
    log_path = Path(__file__).resolve().parent.parent / "logs" / "moderation_actions.jsonl"
    actions = []
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            for line in lines[-500:]:
                line = line.strip()
                if not line:
                    continue
                # Parse each line independently so a single blank/malformed
                # entry doesn't abort the whole read and truncate stats.
                try:
                    actions.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    logger.warning("[api_analytics] skipping malformed moderation log line")
        except Exception:
            logger.exception("[api_analytics] moderation actions log read failed")

    total = len(actions)
    violations = sum(1 for a in actions if a.get("category") not in ("normal", "none", ""))
    dry_run = sum(1 for a in actions if a.get("dry_run"))
    severity_dist = Counter(a.get("severity", "none") for a in actions)
    category_dist = Counter(a.get("category", "unknown") for a in actions)
    action_dist = Counter(a.get("action", "unknown") for a in actions)

    return {
        "total": total,
        "violations": violations,
        "dry_run_count": dry_run,
        "severity": dict(severity_dist),
        "categories": dict(category_dist),
        "actions": dict(action_dist),
    }


@router.get("/user-activity")
async def get_user_activity(request: Request, days: int = 7, user: dict = Depends(get_current_user)):
    """Most active users and message patterns."""
    db = getattr(request.app.state, "db", None)
    if not db:
        return {"top_users": [], "hourly_pattern": {}}

    since = time.time() - (days * 86400)
    msgs = db.get_conversation_history(limit=50000, since=since)

    user_counter = Counter()
    hourly_pattern = Counter()
    for m in msgs:
        user_counter[m.user_name] += 1
        h = time.localtime(m.timestamp).tm_hour
        hourly_pattern[h] += 1

    top_users = [{"name": name, "count": count} for name, count in user_counter.most_common(20)]
    pattern = {str(h): hourly_pattern.get(h, 0) for h in range(24)}

    return {"top_users": top_users, "hourly_pattern": pattern}


@router.get("/cost-tracker")
async def get_cost_tracker(request: Request, days: int = 7, user: dict = Depends(get_current_user)):
    """Estimated token usage and cost breakdown from conversation_history."""
    db = getattr(request.app.state, "db", None)
    if not db:
        return {"total_tokens": 0, "daily": {}, "estimated_cost": 0}

    since = time.time() - (days * 86400)
    msgs = db.get_conversation_history(limit=50000, since=since)

    total_tokens = 0
    daily_tokens = Counter()
    for m in msgs:
        total_tokens += m.tokens_used
        day_key = time.strftime("%Y-%m-%d", time.localtime(m.timestamp))
        daily_tokens[day_key] += m.tokens_used

    # Rough cost estimate: $0.15 / 1M input tokens (average across providers)
    estimated_cost = total_tokens * 0.15 / 1_000_000

    return {
        "total_tokens": total_tokens,
        "daily": dict(sorted(daily_tokens.items())),
        "estimated_cost": round(estimated_cost, 4),
    }


@router.get("/health-trend")
async def get_health_trend(user: dict = Depends(get_current_user)):
    """Provider health over time from model_health.json snapshot."""
    health_path = Path(__file__).resolve().parent.parent / "configs" / "model_health.json"
    if not health_path.exists():
        return {"providers": {}, "saved_at": ""}

    try:
        data = json.loads(health_path.read_text(encoding="utf-8"))
        providers = {}
        for name, info in data.get("providers", {}).items():
            total = info.get("success_count", 0) + info.get("failure_count", 0)
            providers[name] = {
                "success_count": info.get("success_count", 0),
                "failure_count": info.get("failure_count", 0),
                "total": total,
                "consecutive_failures": info.get("consecutive_failures", 0),
                "healthy": info.get("consecutive_failures", 0) < 3,
            }
        return {"providers": providers, "saved_at": data.get("saved_at", "")}
    except Exception:
        return {"providers": {}, "saved_at": ""}


@router.get("/errors")
async def get_errors(request: Request, limit: int = 100, user: dict = Depends(get_current_user)):
    """Recent errors from error logs and telemetry."""
    errors = []

    # From errors.jsonl
    err_path = Path(__file__).resolve().parent.parent / "logs" / "repair" / "errors.jsonl"
    if err_path.exists():
        try:
            lines = err_path.read_text(encoding="utf-8").strip().splitlines()
            for line in lines[-limit:]:
                entry = json.loads(line)
                errors.append({
                    "timestamp": entry.get("t", 0),
                    "source": "repair",
                    "operation": entry.get("operation", ""),
                    "guild": entry.get("guild", ""),
                    "error_type": entry.get("error_type", ""),
                    "message": entry.get("error_msg", ""),
                })
        except Exception:
            logger.exception("[api_analytics] repair log read failed")

    # From telemetry_logs (status=error)
    db = getattr(request.app.state, "db", None)
    if db:
        try:
            with db._wlock:
                conn = db._get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT timestamp, subsystem, action, message
                    FROM telemetry_logs
                    WHERE status = 'error'
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))
                rows = cursor.fetchall()
                for row in rows:
                    errors.append({
                        "timestamp": row["timestamp"],
                        "source": "telemetry",
                        "operation": row["action"],
                        "guild": "",
                        "error_type": row["subsystem"],
                        "message": row["message"],
                    })
        except Exception:
            logger.exception("[api_analytics] telemetry error query failed")

    # Sort by timestamp descending
    errors.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
    return {"errors": errors[:limit]}
