"""Thread-safe read bridge between the Discord bot runtime and the web dashboard.

All accessors return *snapshots* (plain dicts / lists) so the web layer never
mutates live bot state.  A threading.Lock serialises reads when the bot or
database is being updated concurrently.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger("azure.web.data_bridge")


class BotDataBridge:
    """Read-only view of live bot data for the web dashboard."""

    def __init__(self, bot: Any, agent: Any, db: Any) -> None:
        self._bot = bot
        self._agent = agent
        self._db = db
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _snapshot(self, obj: Any) -> Any:
        """Return a shallow copy so callers can't mutate live state.

        Shallow copies are sufficient here because the dicts/lists contain
        only primitive values (strings, ints, floats) — not nested mutable
        objects.  This avoids the O(n) deep-copy cost on every dashboard
        API call.
        """
        if isinstance(obj, dict):
            return obj.copy()
        if isinstance(obj, list):
            return list(obj)
        return obj

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return live bot statistics.

        Pulls from the database aggregate counters, the agent moderation
        stats, and runtime bot attributes (latency, uptime, guild count).
        """
        with self._lock:
            stats: dict[str, Any] = {
                "messages_today": 0,
                "active_users": 0,
                "llm_calls": 0,
                "uptime_seconds": 0,
                "health_score": 100,
                "active_moderations": 0,
                "errors": 0,
                "guilds": 0,
                "latency_ms": 0,
            }

            # --- Database aggregates (24 h) ---
            if self._db and hasattr(self._db, "get_aggregate_stats"):
                try:
                    db_stats = self._db.get_aggregate_stats(hours=24)
                    stats["messages_today"] = db_stats.get("total_messages", 0)
                    stats["active_users"] = db_stats.get("peak_users", 0)
                    stats["llm_calls"] = db_stats.get("total_tokens", 0)
                    stats["errors"] = db_stats.get("total_errors", 0)
                except Exception as e:
                    logger.warning("[data_bridge] db stats unavailable: %s", e)

            # --- Bot runtime counters ---
            bot = self._bot
            if bot is not None:
                # Uptime
                if hasattr(bot, "start_time"):
                    try:
                        stats["uptime_seconds"] = int(
                            (time.time() - bot.start_time.timestamp())
                            if hasattr(bot.start_time, "timestamp")
                            else 0
                        )
                    except Exception as e:
                        logger.warning("[data_bridge] uptime calculation failed: %s", e)
                        stats["uptime_seconds"] = 0

                # Latency (websocket round-trip)
                try:
                    stats["latency_ms"] = round(getattr(bot, "latency", 0) * 1000)
                except Exception as e:
                    logger.warning("[data_bridge] latency read failed: %s", e)

                # Guild count
                try:
                    stats["guilds"] = len(bot.guilds)
                except Exception as e:
                    logger.warning("[data_bridge] guild count failed: %s", e)

            # --- Health score from error rate ---
            if stats["messages_today"] > 0 and stats["errors"] > 0:
                error_rate = stats["errors"] / stats["messages_today"]
                stats["health_score"] = max(0, int(100 - error_rate * 100))
            elif stats["errors"] > 0:
                stats["health_score"] = 50

            # --- Moderation pending actions ---
            agent = self._agent
            if agent and hasattr(agent, "get_moderation_stats"):
                try:
                    mod = agent.get_moderation_stats()
                    if isinstance(mod, dict):
                        stats["active_moderations"] = mod.get("pending_actions", 0)
                except Exception as e:
                    logger.warning("[data_bridge] moderation stats failed: %s", e)

            return self._snapshot(stats)

    def get_recent_messages(self, limit: int = 50, server_id: str | None = None) -> list:
        """Return recent messages, optionally restricted to one Discord guild."""
        with self._lock:
            if not self._db:
                return []
            # Clamp limit to prevent unbounded queries
            limit = max(1, min(int(limit), 200))
            try:
                conn = self._db._get_read_connection()
                cursor = conn.cursor()
                query = """
                    SELECT user_name, server_name, channel_name,
                           message, response, timestamp, tokens_used
                    FROM conversation_history
                """
                params: list[Any] = []
                if server_id:
                    query += " WHERE server_id = ?"
                    params.append(str(server_id))
                query += " ORDER BY timestamp DESC LIMIT ?"
                params.append(limit)
                cursor.execute(query, tuple(params))
                rows = cursor.fetchall()
                return self._snapshot(
                    [
                        {
                            "user": r["user_name"],
                            "server": r["server_name"],
                            "channel": r["channel_name"],
                            "message": (r["message"] or "")[:500],
                            "response": (r["response"] or "")[:500],
                            "timestamp": r["timestamp"],
                            "tokens": r["tokens_used"],
                        }
                        for r in rows
                    ]
                )
            except Exception as e:
                logger.warning("[data_bridge] recent messages unavailable: %s", e)
                return []

    def get_moderation_actions(self, limit: int = 50) -> list:
        """Return recent moderation actions from audit_logs."""
        with self._lock:
            if not self._db:
                return []
            # Clamp limit to prevent unbounded queries
            limit = max(1, min(int(limit), 200))
            try:
                conn = self._db._get_read_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT user_name, discord_id, action, reason, subsystem, timestamp
                    FROM audit_logs
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                rows = cursor.fetchall()
                return self._snapshot(
                    [
                        {
                            "user": r["user_name"],
                            "target": r["discord_id"],
                            "action": r["action"],
                            "reason": r["reason"],
                            "subsystem": r["subsystem"],
                            "timestamp": r["timestamp"],
                        }
                        for r in rows
                    ]
                )
            except Exception as e:
                logger.warning("[data_bridge] moderation actions unavailable: %s", e)
                return []

    def get_provider_health(self) -> dict:
        """Return current provider health status from model_health.json."""
        import json
        import os
        from pathlib import Path

        default_path = Path(__file__).resolve().parent.parent / "configs" / "model_health.json"
        health_path = Path(
            os.environ.get("AZURE_MODEL_HEALTH", str(default_path))
        )
        if health_path.exists():
            try:
                data = json.loads(health_path.read_text(encoding="utf-8"))
                return self._snapshot(data)
            except Exception as e:
                logger.warning("[data_bridge] model_health.json unreadable: %s", e)
        return {}

    def get_active_users(self) -> list:
        """Return currently active users (messed in last hour)."""
        with self._lock:
            if not self._db:
                return []
            try:
                conn = self._db._get_read_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT user_id, user_name,
                           COUNT(*) as message_count,
                           MAX(timestamp) as last_active
                    FROM conversation_history
                    WHERE timestamp > ?
                    GROUP BY user_id
                    ORDER BY message_count DESC
                    LIMIT 50
                    """,
                    (time.time() - 3600,),
                )
                rows = cursor.fetchall()
                return self._snapshot(
                    [
                    {
                        "user_id": r["user_id"],
                        "user_name": r["user_name"],
                        "message_count": r["message_count"],
                        "last_active": r["last_active"],
                    }
                    for r in rows
                ]
            )
            except Exception as e:
                logger.warning("[data_bridge] active users unavailable: %s", e)
                return []

    def get_agent_info(self) -> dict:
        """Return agent configuration snapshot."""
        with self._lock:
            agent = self._agent
            if agent and hasattr(agent, "get_info"):
                try:
                    return self._snapshot(agent.get_info())
                except Exception as e:
                    logger.warning("[data_bridge] agent info failed: %s", e)
            return {}

    def get_websocket_manager(self) -> Any:
        """Return the WebSocket connection manager (for real-time push)."""
        return None
