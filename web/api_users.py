"""
User Management API Router

Connects to the real memory system for user data:
- azure/memory_backend.py MemoryBackend for user profiles and memories
- SQLite database for conversation history, moderation, and preferences
- logs/moderation_actions.jsonl for per-user moderation history
"""

import contextlib
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .api_auth import get_current_user

logger = logging.getLogger("web.users")

router = APIRouter()

DATA_DIR = Path(__file__).parent.parent / "data"
LOGS_DIR = Path(__file__).parent.parent / "logs"


def _get_db(request: Request):
    return getattr(request.app.state, "db", None)


def _get_memory_backend(request: Request):
    agent = getattr(request.app.state, "agent", None)
    if agent and hasattr(agent, "memory_backend"):
        return agent.memory_backend
    return None


def _get_memory_backend_user_ids(memory_backend) -> set:
    user_ids = set()
    if memory_backend is None:
        return user_ids
    try:
        conn = memory_backend._conn
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM user_profiles")
        for row in cursor.fetchall():
            if row[0]:
                user_ids.add(row[0])
    except Exception as e:
        logger.warning("[users] Memory backend user_profiles query failed: %s", e)
    try:
        conn = memory_backend._conn
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT user_id FROM memories WHERE user_id IS NOT NULL AND user_id != ''")
        for row in cursor.fetchall():
            if row[0]:
                user_ids.add(row[0])
    except Exception as e:
        logger.warning("[users] Memory backend memories query failed: %s", e)
    return user_ids


def _read_moderation_actions() -> list[dict]:
    actions_path = LOGS_DIR / "moderation_actions.jsonl"
    if not actions_path.exists():
        return []
    entries = []
    try:
        with open(actions_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning("[users] Failed to read moderation_actions.jsonl: %s", e)
    return entries


def _compute_risk_score(moderation_count: int, ban_count: int, timeout_count: int) -> float:
    score = 0.0
    score += ban_count * 0.4
    score += timeout_count * 0.25
    score += moderation_count * 0.05
    return min(1.0, score)


def _build_user_profile(user_id: str, db, mod_actions: list, memory_backend=None) -> dict:
    now_ms = int(time.time() * 1000)

    profile = {
        "id": user_id,
        "name": user_id,
        "avatar": None,
        "join_date": now_ms,
        "last_active": None,
        "message_count": 0,
        "avg_message_length": 0,
        "recent_messages": [],
        "moderation_history": [],
        "behavioral_profile": {
            "avg_message_length": 0,
            "peak_hour": None,
            "sentiment": "Neutral",
            "toxicity_flags": 0,
        },
        "risk_score": 0.0,
        "risk_level": "low",
        "knowledge_contributions": [],
        "activity_log": [],
    }

    # --- Memory backend profile (UserProfile dataclass) ---
    if memory_backend:
        try:
            mb_profile = memory_backend.get_user_profile(user_id)
            if mb_profile:
                profile["communication_style"] = mb_profile.communication_style
                profile["expertise_level"] = mb_profile.expertise_level
                profile["verbosity"] = mb_profile.verbosity
                profile["humor_score"] = mb_profile.humor_score
                profile["preferred_topics"] = mb_profile.preferred_topics
                profile["disliked_topics"] = mb_profile.disliked_topics
                profile["total_interactions"] = mb_profile.total_interactions
                profile["corrections_received"] = mb_profile.corrections_received
                profile["thumbs_up"] = mb_profile.thumbs_up
                profile["thumbs_down"] = mb_profile.thumbs_down
                if mb_profile.last_interaction and mb_profile.last_interaction > 0:
                    profile["last_active"] = int(mb_profile.last_interaction * 1000)
                if mb_profile.total_interactions > 0:
                    profile["message_count"] = max(
                        profile["message_count"], mb_profile.total_interactions
                    )
                if mb_profile.user_name:
                    profile["name"] = mb_profile.user_name
        except Exception as e:
            logger.debug("[users] Memory backend profile read failed for %s: %s", user_id, e)

    # --- Memory backend memories ---
    memory_entries = []
    if memory_backend:
        try:
            memories = memory_backend.query_memories(user_id=user_id, limit=50)
            for mem in memories:
                memory_entries.append({
                    "id": mem.get("id", ""),
                    "text": mem.get("text", "")[:500],
                    "source": mem.get("source", "memory"),
                    "tags": mem.get("tags", []),
                    "timestamp": mem.get("timestamp", 0),
                })
        except Exception as e:
            logger.debug("[users] Memory backend memories query failed for %s: %s", user_id, e)

    # --- Moderation actions from JSONL ---
    mod_count = 0
    ban_count = 0
    timeout_count = 0

    for action in mod_actions:
        if action.get("user_id") == user_id or action.get("target_user") == user_id:
            mod_entry = {
                "action": action.get("action", ""),
                "reason": action.get("reason", ""),
                "timestamp": action.get("timestamp", 0),
            }
            profile["moderation_history"].append(mod_entry)
            profile["activity_log"].append({
                "type": "moderation",
                "action": action.get("action", ""),
                "detail": action.get("reason", ""),
                "timestamp": action.get("timestamp", 0),
            })
            act = action.get("action", "")
            if act == "ban":
                ban_count += 1
            elif act == "timeout":
                timeout_count += 1
            elif act in ("warn", "kick", "delete"):
                mod_count += 1

    # --- Bot database (conversation_history, audit_logs, security_events) ---
    if db:
        try:
            with db._wlock:
                conn = db._get_connection()
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT user_name FROM user_preferences WHERE user_id = ?
                """, (user_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    profile["name"] = row[0]

                cursor.execute("""
                    SELECT user_name, message, response, timestamp
                    FROM conversation_history
                    WHERE user_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 20
                """, (user_id,))
                db_msgs = cursor.fetchall()
                if db_msgs:
                    profile["recent_messages"] = []
                    for m in db_msgs:
                        profile["recent_messages"].append({
                            "text": m[1][:200] if m[1] else "",
                            "response": m[2][:200] if m[2] else "",
                            "timestamp": m[3],
                        })
                    if not profile["name"] or profile["name"] == user_id:
                        profile["name"] = db_msgs[0][0] or user_id
                    if db_msgs[0][3]:
                        profile["last_active"] = db_msgs[0][3]

                cursor.execute("""
                    SELECT COUNT(*) FROM conversation_history WHERE user_id = ?
                """, (user_id,))
                db_msg_count = cursor.fetchone()[0]
                if db_msg_count > 0:
                    profile["message_count"] = max(profile["message_count"], db_msg_count)

                cursor.execute("""
                    SELECT action, reason, timestamp
                    FROM audit_logs
                    WHERE discord_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 50
                """, (user_id,))
                audit_rows = cursor.fetchall()
                for ar in audit_rows:
                    entry = {"action": ar[0], "reason": ar[1], "timestamp": ar[2]}
                    if entry not in profile["moderation_history"]:
                        profile["moderation_history"].append(entry)
                        profile["activity_log"].append({
                            "type": "audit",
                            "action": ar[0],
                            "detail": ar[1],
                            "timestamp": ar[2],
                        })

                cursor.execute("""
                    SELECT event_type, severity, details, timestamp
                    FROM security_events
                    WHERE user_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 20
                """, (user_id,))
                sec_rows = cursor.fetchall()
                for sr in sec_rows:
                    profile["activity_log"].append({
                        "type": "security",
                        "action": sr[0],
                        "detail": sr[2],
                        "timestamp": sr[3],
                    })
                    if sr[1] in ("high", "critical"):
                        mod_count += 1

        except Exception as e:
            logger.warning("[users] DB query failed for user %s: %s", user_id, e)

    # Merge memory backend entries into recent_messages and knowledge
    if memory_entries:
        profile["knowledge_contributions"] = memory_entries
        if not profile["recent_messages"]:
            profile["recent_messages"] = [
                {"text": e["text"][:200], "source": e["source"]}
                for e in memory_entries[-20:]
            ]

    # Behavioral profile from memory backend
    if memory_backend:
        try:
            mb_profile = memory_backend.get_user_profile(user_id)
            if mb_profile:
                profile["behavioral_profile"]["communication_style"] = mb_profile.communication_style
                profile["behavioral_profile"]["expertise_level"] = mb_profile.expertise_level
                profile["behavioral_profile"]["humor_score"] = mb_profile.humor_score
                if mb_profile.thumbs_up + mb_profile.thumbs_down > 0:
                    up_ratio = mb_profile.thumbs_up / (mb_profile.thumbs_up + mb_profile.thumbs_down)
                    profile["behavioral_profile"]["sentiment"] = (
                        "Positive" if up_ratio > 0.6
                        else "Negative" if up_ratio < 0.4
                        else "Neutral"
                    )
        except Exception:
            logger.exception("[api_users] behavior profile enrich failed")

    profile["risk_score"] = _compute_risk_score(mod_count, ban_count, timeout_count)
    profile["risk_level"] = (
        "high" if profile["risk_score"] >= 0.7
        else "medium" if profile["risk_score"] >= 0.35
        else "low"
    )

    if not profile["name"] or profile["name"] == user_id:
        profile["name"] = f"User {user_id[:8]}"
    if not profile["last_active"]:
        profile["last_active"] = profile["join_date"]

    return profile


def _collect_all_user_ids(request: Request) -> set:
    db = _get_db(request)
    memory_backend = _get_memory_backend(request)

    all_user_ids = set()

    # Memory backend: user_profiles + memories tables
    all_user_ids |= _get_memory_backend_user_ids(memory_backend)

    # Bot database
    if db:
        try:
            with db._wlock:
                conn = db._get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT user_id FROM conversation_history")
                for row in cursor.fetchall():
                    all_user_ids.add(row[0])
                cursor.execute("SELECT DISTINCT discord_id FROM audit_logs")
                for row in cursor.fetchall():
                    if row[0]:
                        all_user_ids.add(row[0])
                cursor.execute("SELECT DISTINCT user_id FROM security_events")
                for row in cursor.fetchall():
                    if row[0]:
                        all_user_ids.add(row[0])
                cursor.execute("SELECT DISTINCT user_id FROM user_preferences")
                for row in cursor.fetchall():
                    if row[0]:
                        all_user_ids.add(row[0])
        except Exception as e:
            logger.warning("[users] Failed to enumerate user IDs from bot DB: %s", e)

    return all_user_ids


@router.get("")
async def list_users(request: Request, user: dict = Depends(get_current_user)):
    all_user_ids = _collect_all_user_ids(request)
    db = _get_db(request)
    memory_backend = _get_memory_backend(request)
    mod_actions = _read_moderation_actions()

    users = []
    for uid in sorted(all_user_ids):
        profile = _build_user_profile(uid, db, mod_actions, memory_backend)
        users.append(profile)

    return users


@router.get("/high-risk")
async def high_risk_users(request: Request, threshold: float = Query(0.7, ge=0, le=1), user: dict = Depends(get_current_user)):
    all_user_ids = _collect_all_user_ids(request)
    db = _get_db(request)
    memory_backend = _get_memory_backend(request)
    mod_actions = _read_moderation_actions()

    high_risk = []
    for uid in all_user_ids:
        profile = _build_user_profile(uid, db, mod_actions, memory_backend)
        if profile["risk_score"] > threshold:
            high_risk.append(profile)

    high_risk.sort(key=lambda u: u["risk_score"], reverse=True)
    return high_risk


@router.get("/search")
async def search_users(request: Request, q: str = Query(..., min_length=1), user: dict = Depends(get_current_user)):
    all_user_ids = _collect_all_user_ids(request)
    db = _get_db(request)
    memory_backend = _get_memory_backend(request)
    mod_actions = _read_moderation_actions()

    q_lower = q.lower()
    matches = []

    # Memory backend text search across memories table
    if memory_backend:
        try:
            conn = memory_backend._conn
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT user_id FROM memories WHERE text LIKE ?",
                (f"%{q}%",),
            )
            for row in cursor.fetchall():
                if row[0]:
                    all_user_ids.add(row[0])
        except Exception as e:
            logger.debug("[users] Memory backend memories search failed: %s", e)

    seen = set()
    for uid in all_user_ids:
        if uid in seen:
            continue
        profile = _build_user_profile(uid, db, mod_actions, memory_backend)
        name_match = q_lower in profile.get("name", "").lower()
        id_match = q_lower in uid.lower()
        style_match = q_lower in profile.get("communication_style", "").lower()
        topic_match = any(q_lower in t.lower() for t in profile.get("preferred_topics", []))
        if id_match or name_match or style_match or topic_match:
            matches.append(profile)
            seen.add(uid)

    return matches


@router.get("/{user_id}")
async def get_user(user_id: str, request: Request, user: dict = Depends(get_current_user)):
    db = _get_db(request)
    memory_backend = _get_memory_backend(request)
    mod_actions = _read_moderation_actions()

    profile = _build_user_profile(user_id, db, mod_actions, memory_backend)

    user_exists = (
        profile.get("message_count", 0) > 0
        or len(profile.get("moderation_history", [])) > 0
        or len(profile.get("knowledge_contributions", [])) > 0
        or profile.get("total_interactions", 0) > 0
    )

    if not user_exists and memory_backend:
        try:
            mb_profile = memory_backend.get_user_profile(user_id)
            if mb_profile:
                user_exists = True
        except Exception:
            logger.exception("[api_users] memory_backend profile check failed")

    if not user_exists and db:
        try:
            with db._wlock:
                conn = db._get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM user_preferences WHERE user_id = ?", (user_id,))
                if cursor.fetchone():
                    user_exists = True
                else:
                    cursor.execute("SELECT 1 FROM conversation_history WHERE user_id = ?", (user_id,))
                    if cursor.fetchone():
                        user_exists = True
                    else:
                        cursor.execute("SELECT 1 FROM audit_logs WHERE discord_id = ?", (user_id,))
                        if cursor.fetchone():
                            user_exists = True
        except Exception as e:
            logger.warning("[users] Existence check failed for %s: %s", user_id, e)

    if not user_exists:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    return profile


@router.get("/{user_id}/messages")
async def get_user_messages(
    user_id: str,
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    db = _get_db(request)
    memory_backend = _get_memory_backend(request)

    all_messages = []

    # Memory backend memories for this user
    if memory_backend:
        try:
            memories = memory_backend.query_memories(user_id=user_id, limit=limit * 5)
            for mem in memories:
                all_messages.append({
                    "text": (mem.get("text", "") or "")[:500],
                    "source": mem.get("source", "memory"),
                    "timestamp": mem.get("timestamp", 0),
                    "tags": mem.get("tags", []),
                    "memory_id": mem.get("id", ""),
                })
        except Exception as e:
            logger.debug("[users] Memory backend message query failed for %s: %s", user_id, e)

    # Bot database conversation_history
    if db:
        try:
            with db._wlock:
                conn = db._get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT user_name, message, response, timestamp, server_name, channel_name
                    FROM conversation_history
                    WHERE user_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?
                """, (user_id, limit * 10, (page - 1) * limit * 10))
                for row in cursor.fetchall():
                    all_messages.append({
                        "text": row[1][:500] if row[1] else "",
                        "response": row[2][:500] if row[2] else "",
                        "timestamp": row[3],
                        "source": "database",
                        "server": row[4],
                        "channel": row[5],
                    })
        except Exception as e:
            logger.warning("[users] Message query failed for %s: %s", user_id, e)

    all_messages.sort(key=lambda m: m.get("timestamp", 0) or 0, reverse=True)

    total = len(all_messages)
    start = (page - 1) * limit
    end = start + limit
    paginated = all_messages[start:end]

    return {
        "user_id": user_id,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
        "messages": paginated,
    }


@router.get("/{user_id}/knowledge")
async def get_user_knowledge(user_id: str, request: Request, user: dict = Depends(get_current_user)):
    db = _get_db(request)
    memory_backend = _get_memory_backend(request)

    knowledge_entries = []

    # Memory backend memories
    if memory_backend:
        try:
            memories = memory_backend.query_memories(user_id=user_id, limit=50)
            for mem in memories:
                knowledge_entries.append({
                    "id": mem.get("id", ""),
                    "text": mem.get("text", "")[:500],
                    "source": mem.get("source", "memory"),
                    "tags": mem.get("tags", []),
                    "timestamp": mem.get("timestamp", 0),
                })
        except Exception as e:
            logger.debug("[users] Memory backend knowledge query failed for %s: %s", user_id, e)

    # Bot database memories table
    if db:
        try:
            with db._wlock:
                conn = db._get_connection()
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT id, user_id, text, source, tags, timestamp
                    FROM memories
                    WHERE user_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 50
                """, (user_id,))
                for row in cursor.fetchall():
                    tags = []
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        tags = json.loads(row[4]) if row[4] else []
                    knowledge_entries.append({
                        "id": row[0],
                        "text": row[2],
                        "source": row[3],
                        "tags": tags,
                        "timestamp": row[5],
                    })
        except Exception as e:
            logger.warning("[users] Knowledge query failed for %s: %s", user_id, e)

    # Memory backend user profile as knowledge
    if memory_backend:
        try:
            mb_profile = memory_backend.get_user_profile(user_id)
            if mb_profile:
                knowledge_entries.append({
                    "id": f"profile_{user_id}",
                    "text": (
                        f"User profile: expertise={mb_profile.expertise_level}, "
                        f"style={mb_profile.communication_style}, "
                        f"topics={', '.join(mb_profile.preferred_topics or [])}"
                    ),
                    "source": "profile",
                    "tags": ["profile", mb_profile.expertise_level],
                    "timestamp": mb_profile.last_interaction,
                })
        except Exception:
            logger.exception("[api_users] profile knowledge export failed")

    return {
        "user_id": user_id,
        "total": len(knowledge_entries),
        "entries": knowledge_entries,
    }
