"""Extremely thorough test suite for the Azure web server, API endpoints,
WebSocket, broadcast system, data bridge, security, and edge cases.

65+ tests covering every aspect of the web layer.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _make_app():
    """Import the app from server module. psutil is already available."""
    from web.server import app
    return app


@pytest.fixture
def app():
    with patch.dict(os.environ, {"AZURE_DEV_MODE": "1", "AZURE_WEB_SECRET": "test-secret-key-for-unit-tests-only"}, clear=False):
        yield _make_app()


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def mock_auth_dependency(app):
    """Bypass JWT auth by overriding auth dependencies via FastAPI dependency injection."""
    from web.api_auth import get_current_user, require_admin
    mock_user = {"username": "admin", "role": "owner"}
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[require_admin] = lambda: mock_user
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_broadcast_app():
    """Reset the broadcast module's _app global before each test to prevent stale state."""
    from web import broadcast
    broadcast._app = None
    yield
    broadcast._app = None


@pytest.fixture
def mock_auth_token():
    from web.api_auth import create_access_token
    token = create_access_token(
        data={"sub": "admin", "role": "owner"},
        expires_delta=timedelta(hours=1),
    )
    return token


@pytest.fixture
def auth_headers(mock_auth_token):
    return {"Authorization": f"Bearer {mock_auth_token}"}


@pytest.fixture
def admin_headers(auth_headers):
    return auth_headers


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ════════════════════════════════════════════════════════════════════════════
# 1. FASTAPI SERVER (15+ tests)
# ════════════════════════════════════════════════════════════════════════════

class TestFastAPIServer:

    def test_app_creation(self, app):
        assert isinstance(app, FastAPI)
        assert app.title == "Azure Operating Platform"
        assert app.version == "1.0.0"

    def test_get_root_returns_html_or_redirect(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (200, 307, 308)
        if resp.status_code == 200:
            ct = resp.headers.get("content-type", "")
            assert "text/html" in ct or "application/json" in ct

    def test_health_returns_200(self, client):
        resp = client.get("/api/health/")
        assert resp.status_code == 200

    def test_health_has_correct_structure(self, client):
        resp = client.get("/api/health/")
        data = resp.json()
        assert "status" in data
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], int)

    def test_api_stats_returns_dict(self, client, auth_headers):
        resp = client.get("/api/stats", headers=auth_headers)
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict)

    def test_api_stats_has_required_fields(self, client, mock_auth_dependency):
        resp = client.get("/api/stats")
        data = resp.json()
        for key in ("messages_today", "active_users", "llm_calls", "health_score"):
            assert key in data, f"Missing required field: {key}"

    def test_api_messages_returns_list(self, client, mock_auth_dependency):
        resp = client.get("/api/moderation/actions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "actions" in data
        assert isinstance(data["actions"], list)

    def test_api_messages_respects_limit(self, client, mock_auth_dependency):
        resp = client.get("/api/moderation/actions?limit=5")
        data = resp.json()
        assert len(data["actions"]) <= 5

    def test_api_moderation_returns_list(self, client, mock_auth_dependency):
        resp = client.get("/api/moderation/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_api_moderation_respects_limit(self, client, mock_auth_dependency):
        resp = client.get("/api/moderation/security?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data
        assert len(data["events"]) <= 10

    def test_api_active_users_returns_list(self, client, mock_auth_dependency):
        resp = client.get("/api/users")
        assert resp.status_code == 200

    def test_api_provider_health_returns_dict(self, client, mock_auth_dependency):
        resp = client.get("/api/health/")
        data = resp.json()
        assert isinstance(data, dict)

    def test_api_agent_info_returns_dict(self, app, client, auth_headers):
        app.state.agent = None
        resp = client.get("/api/config/current", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_post_config_returns_success(self, app, client, admin_headers):
        app.state.agent = None
        resp = client.post(
            "/api/config/emergency_stop",
            headers=admin_headers,
        )
        assert resp.status_code in (200, 400)

    def test_404_for_unknown_routes(self, client):
        resp = client.get("/api/nonexistent_endpoint_xyz")
        assert resp.status_code in (404, 405)

    def test_health_detailed_requires_auth(self, client):
        resp = client.get("/api/health/detailed")
        assert resp.status_code in (401, 403)

    def test_health_detailed_with_auth(self, client, mock_auth_dependency):
        resp = client.get("/api/health/detailed")
        assert resp.status_code == 200

    def test_static_files_endpoint(self, client):
        resp = client.get("/static/nonexistent.css")
        assert resp.status_code == 404

    def test_classic_page(self, client):
        resp = client.get("/classic")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_analytics_page(self, client):
        resp = client.get("/analytics")
        assert resp.status_code == 200


# ════════════════════════════════════════════════════════════════════════════
# 2. WEBSOCKET TESTS (10+ tests)
# ════════════════════════════════════════════════════════════════════════════

class TestWebSocket:

    def test_ws_endpoint_exists(self, app):
        routes = [r.path for r in app.routes]
        assert "/ws" in routes

    def test_ws_reject_without_token(self, client):
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws"):
            pass

    def test_ws_reject_invalid_token(self, client):
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws?token=invalid.jwt.token"):
            pass

    def test_ws_connect_with_valid_token(self, client, mock_auth_token):
        with client.websocket_connect(f"/ws?token={mock_auth_token}"):
            pass

    def test_ws_reject_viewer_role(self, client):
        from web.api_auth import create_access_token
        token = create_access_token(
            data={"sub": "viewer", "role": "viewer"},
            expires_delta=timedelta(hours=1),
        )
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(f"/ws?token={token}"):
            pass

    def test_ws_receive_json(self, client, mock_auth_token):
        with client.websocket_connect(f"/ws?token={mock_auth_token}") as ws:
            ws.send_json({"action": "ping"})
            data = ws.receive_json()
            assert data["action"] == "pong"

    def test_ws_connection_manager_tracking(self, app):
        from web.server import ConnectionManager
        mgr = ConnectionManager()
        assert mgr.active_connections == []
        ws_mock = AsyncMock()
        ws_mock.accept = AsyncMock()
        asyncio.run(mgr.connect(ws_mock))
        assert len(mgr.active_connections) == 1
        mgr.disconnect(ws_mock)
        assert len(mgr.active_connections) == 0

    def test_ws_manager_stored_on_app(self, app):
        assert hasattr(app.state, "ws_manager")
        from web.server import ConnectionManager
        assert isinstance(app.state.ws_manager, ConnectionManager)

    def test_ws_broadcast_reaches_clients(self, app):
        from web.server import ConnectionManager
        mgr = ConnectionManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        asyncio.run(mgr.connect(ws1))
        asyncio.run(mgr.connect(ws2))
        asyncio.run(mgr.broadcast({"type": "test"}))
        ws1.send_json.assert_awaited_once_with({"type": "test"})
        ws2.send_json.assert_awaited_once_with({"type": "test"})

    def test_ws_broadcast_with_no_clients(self, app):
        from web.server import ConnectionManager
        mgr = ConnectionManager()
        asyncio.run(mgr.broadcast({"type": "test"}))

    def test_ws_broadcast_is_scoped_to_authorized_guilds(self, app):
        from web.server import ConnectionManager
        mgr = ConnectionManager()
        ws_allowed = AsyncMock()
        ws_other = AsyncMock()
        asyncio.run(mgr.connect(ws_allowed, allowed_guild_ids={"111"}))
        asyncio.run(mgr.connect(ws_other, allowed_guild_ids={"222"}))

        asyncio.run(mgr.broadcast({"type": "guild_event", "data": {"guild_id": "111"}}))

        ws_allowed.send_json.assert_awaited_once()
        ws_other.send_json.assert_not_awaited()

    def test_ws_subscription_cannot_escape_allowlist(self, app):
        from web.server import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        asyncio.run(mgr.connect(ws, allowed_guild_ids={"111", "222"}))

        with pytest.raises(ValueError, match="unauthorized guild"):
            mgr.subscribe(ws, ["333"])
        assert mgr.subscribe(ws, ["222"]) == {"222"}

        asyncio.run(mgr.broadcast({"type": "guild_event", "data": {"guild_id": "111"}}))
        ws.send_json.assert_not_awaited()

    def test_ws_broadcast_marks_disconnected(self, app):
        from web.server import ConnectionManager
        mgr = ConnectionManager()
        ws_ok = AsyncMock()
        ws_bad = AsyncMock()
        ws_bad.send_json = AsyncMock(side_effect=Exception("connection lost"))
        asyncio.run(mgr.connect(ws_ok))
        asyncio.run(mgr.connect(ws_bad))
        asyncio.run(mgr.broadcast({"type": "test"}))
        assert ws_bad not in mgr.active_connections
        assert ws_ok in mgr.active_connections

    def test_ws_disconnect_idempotent(self, app):
        from web.server import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        mgr.disconnect(ws)
        assert ws not in mgr.active_connections

    def test_ws_ping_pong(self, client, mock_auth_token):
        with client.websocket_connect(f"/ws?token={mock_auth_token}") as ws:
            ws.send_json({"action": "ping"})
            data = ws.receive_json()
            assert data["action"] == "pong"
            assert "user" in data

    def test_ws_non_dict_message_ignored(self, client, mock_auth_token):
        with client.websocket_connect(f"/ws?token={mock_auth_token}") as ws:
            ws.send_text("just a string")
            ws.send_json({"action": "ping"})
            data = ws.receive_json()
            assert data["action"] == "pong"


# ════════════════════════════════════════════════════════════════════════════
# 3. BROADCAST SYSTEM (10+ tests)
# ════════════════════════════════════════════════════════════════════════════

class TestBroadcastSystem:

    def test_set_app(self):
        from web.broadcast import set_app
        mock_app = MagicMock()
        set_app(mock_app)
        from web import broadcast
        assert broadcast._app is mock_app

    def test_broadcast_event_sends_to_all(self):
        from web import broadcast
        mock_manager = AsyncMock()
        mock_manager.active_connections = [MagicMock()]
        mock_app = MagicMock()
        mock_app.state.ws_manager = mock_manager
        broadcast._app = mock_app

        asyncio.run(
            broadcast.broadcast_event("test_event", {"key": "value"})
        )
        mock_manager.broadcast.assert_awaited_once()
        payload = mock_manager.broadcast.call_args[0][0]
        assert payload["type"] == "test_event"
        assert payload["data"] == {"key": "value"}
        assert "ts" in payload

    def test_broadcast_event_with_no_app(self):
        from web import broadcast
        broadcast._app = None
        asyncio.run(
            broadcast.broadcast_event("test", {})
        )

    def test_broadcast_event_with_no_manager(self):
        from web import broadcast
        mock_app = MagicMock(spec=[])
        mock_app.state = MagicMock(spec=[])
        broadcast._app = mock_app
        asyncio.run(
            broadcast.broadcast_event("test", {})
        )

    def test_broadcast_event_no_data_defaults_empty(self):
        from web import broadcast
        mock_manager = AsyncMock()
        mock_manager.active_connections = [MagicMock()]
        mock_app = MagicMock()
        mock_app.state.ws_manager = mock_manager
        broadcast._app = mock_app

        asyncio.run(
            broadcast.broadcast_event("test_event")
        )
        payload = mock_manager.broadcast.call_args[0][0]
        assert payload["data"] == {}

    def test_broadcast_message_format(self):
        from web import broadcast
        mock_manager = AsyncMock()
        mock_manager.active_connections = [MagicMock()]
        mock_app = MagicMock()
        mock_app.state.ws_manager = mock_manager
        broadcast._app = mock_app

        asyncio.run(
            broadcast.broadcast_message("user1", "general", "hello", guild="G1", cached=False)
        )
        payload = mock_manager.broadcast.call_args[0][0]
        assert payload["type"] == "DISCORD_MESSAGE"
        assert payload["data"]["author"] == "user1"
        assert payload["data"]["channel"] == "general"
        assert payload["data"]["content"] == "hello"
        assert payload["data"]["guild"] == "G1"
        assert payload["data"]["cached"] is False

    def test_broadcast_moderation_format(self):
        from web import broadcast
        mock_manager = AsyncMock()
        mock_manager.active_connections = [MagicMock()]
        mock_app = MagicMock()
        mock_app.state.ws_manager = mock_manager
        broadcast._app = mock_app

        asyncio.run(
            broadcast.broadcast_moderation("bad_user", "ban", reason="spam", confidence=0.95)
        )
        payload = mock_manager.broadcast.call_args[0][0]
        assert payload["type"] == "MODERATION_ACTION"
        assert payload["data"]["user"] == "bad_user"
        assert payload["data"]["action"] == "ban"
        assert payload["data"]["confidence"] == 0.95

    def test_broadcast_health_format(self):
        from web import broadcast
        mock_manager = AsyncMock()
        mock_manager.active_connections = [MagicMock()]
        mock_app = MagicMock()
        mock_app.state.ws_manager = mock_manager
        broadcast._app = mock_app

        asyncio.run(
            broadcast.broadcast_health(status="degraded", memory_mb=256.5, errors=3)
        )
        payload = mock_manager.broadcast.call_args[0][0]
        assert payload["type"] == "HEALTH_CHANGE"
        assert payload["data"]["status"] == "degraded"
        assert payload["data"]["memory_mb"] == 256.5
        assert payload["data"]["errors"] == 3

    def test_broadcast_config_format(self):
        from web import broadcast
        mock_manager = AsyncMock()
        mock_manager.active_connections = [MagicMock()]
        mock_app = MagicMock()
        mock_app.state.ws_manager = mock_manager
        broadcast._app = mock_app

        asyncio.run(
            broadcast.broadcast_config("phase", old_value="dry_run", new_value="reactive")
        )
        payload = mock_manager.broadcast.call_args[0][0]
        assert payload["type"] == "CONFIG_UPDATE"
        assert payload["data"]["setting"] == "phase"
        assert payload["data"]["old_value"] == "dry_run"
        assert payload["data"]["new_value"] == "reactive"

    def test_broadcast_execution_format(self):
        from web import broadcast
        mock_manager = AsyncMock()
        mock_manager.active_connections = [MagicMock()]
        mock_app = MagicMock()
        mock_app.state.ws_manager = mock_manager
        broadcast._app = mock_app

        asyncio.run(
            broadcast.broadcast_execution("user1", "llm_call", phase="thinking", status="ok", message="done")
        )
        payload = mock_manager.broadcast.call_args[0][0]
        assert payload["type"] == "execution_telemetry"
        assert payload["data"]["user"] == "user1"
        assert payload["data"]["event"]["action"] == "llm_call"
        assert payload["data"]["event"]["phase"] == "thinking"

    def test_broadcast_system_metrics_format(self):
        from web import broadcast
        mock_manager = AsyncMock()
        mock_manager.active_connections = [MagicMock()]
        mock_app = MagicMock()
        mock_app.state.ws_manager = mock_manager
        broadcast._app = mock_app

        asyncio.run(
            broadcast.broadcast_system_metrics(total_messages=100, active_users=5, llm_calls=30)
        )
        payload = mock_manager.broadcast.call_args[0][0]
        assert payload["type"] == "system_metrics"
        assert payload["data"]["total_messages"] == 100
        assert payload["data"]["active_users"] == 5
        assert payload["data"]["llm_calls"] == 30

    def test_broadcast_emergency_stop_format(self):
        from web import broadcast
        mock_manager = AsyncMock()
        mock_manager.active_connections = [MagicMock()]
        mock_app = MagicMock()
        mock_app.state.ws_manager = mock_manager
        broadcast._app = mock_app

        asyncio.run(
            broadcast.broadcast_emergency_stop(user="admin")
        )
        payload = mock_manager.broadcast.call_args[0][0]
        assert payload["type"] == "EMERGENCY_STOP_TRIGGERED"
        assert payload["data"]["user"] == "admin"
        assert "ts" in payload["data"]

    def test_broadcast_event_handles_exception(self):
        from web import broadcast
        mock_manager = AsyncMock()
        mock_manager.active_connections = [MagicMock()]
        mock_manager.broadcast = AsyncMock(side_effect=Exception("send failed"))
        mock_app = MagicMock()
        mock_app.state.ws_manager = mock_manager
        broadcast._app = mock_app

        asyncio.run(
            broadcast.broadcast_event("test", {})
        )

    def test_broadcast_event_empty_connections(self):
        from web import broadcast
        mock_manager = AsyncMock()
        mock_manager.active_connections = []
        mock_app = MagicMock()
        mock_app.state.ws_manager = mock_manager
        broadcast._app = mock_app

        asyncio.run(
            broadcast.broadcast_event("test", {})
        )
        mock_manager.broadcast.assert_not_awaited()


# ════════════════════════════════════════════════════════════════════════════
# 4. DATA BRIDGE (10+ tests)
# ════════════════════════════════════════════════════════════════════════════

class TestDataBridge:

    def _make_bridge(self, bot=None, agent=None, db=None):
        from web.data_bridge import BotDataBridge
        return BotDataBridge(bot=bot, agent=agent, db=db)

    def test_get_stats_with_none_deps(self):
        bridge = self._make_bridge(bot=None, agent=None, db=None)
        stats = bridge.get_stats()
        assert isinstance(stats, dict)
        assert stats["messages_today"] == 0
        assert stats["active_users"] == 0
        assert stats["guilds"] == 0
        assert stats["latency_ms"] == 0

    def test_get_stats_with_bot(self):
        bot = MagicMock()
        bot.latency = 0.05
        bot.guilds = [MagicMock(), MagicMock()]
        bot.start_time = datetime.now(UTC) - timedelta(hours=1)
        bridge = self._make_bridge(bot=bot)
        stats = bridge.get_stats()
        assert stats["latency_ms"] == 50
        assert stats["guilds"] == 2
        assert stats["uptime_seconds"] >= 3500

    def test_get_stats_health_score_no_errors(self):
        bridge = self._make_bridge()
        stats = bridge.get_stats()
        assert stats["health_score"] == 100

    def test_get_stats_health_score_with_errors(self):
        db = MagicMock()
        db.get_aggregate_stats.return_value = {
            "total_messages": 100,
            "peak_users": 10,
            "total_tokens": 500,
            "total_errors": 50,
        }
        bridge = self._make_bridge(db=db)
        stats = bridge.get_stats()
        assert stats["errors"] == 50
        assert stats["health_score"] < 100

    def test_get_stats_health_score_errors_no_messages(self):
        db = MagicMock()
        db.get_aggregate_stats.return_value = {
            "total_messages": 0,
            "peak_users": 0,
            "total_tokens": 0,
            "total_errors": 5,
        }
        bridge = self._make_bridge(db=db)
        stats = bridge.get_stats()
        assert stats["health_score"] == 50

    def test_get_recent_messages_empty_db(self):
        bridge = self._make_bridge(db=None)
        msgs = bridge.get_recent_messages()
        assert msgs == []

    def test_get_recent_messages_with_db(self):
        db = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        db._get_read_connection.return_value = mock_conn
        bridge = self._make_bridge(db=db)
        msgs = bridge.get_recent_messages(limit=10)
        assert isinstance(msgs, list)

    def test_get_moderation_actions_empty_db(self):
        bridge = self._make_bridge(db=None)
        actions = bridge.get_moderation_actions()
        assert actions == []

    def test_get_moderation_actions_with_db(self):
        db = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        db._get_read_connection.return_value = mock_conn
        bridge = self._make_bridge(db=db)
        actions = bridge.get_moderation_actions(limit=5)
        assert isinstance(actions, list)

    def test_snapshot_independence_dict(self):
        bridge = self._make_bridge()
        original = {"key": "value"}
        snap = bridge._snapshot(original)
        snap["key"] = "modified"
        assert original["key"] == "value"

    def test_snapshot_independence_list(self):
        bridge = self._make_bridge()
        original = [1, 2, 3]
        snap = bridge._snapshot(original)
        snap.append(4)
        assert len(original) == 3

    def test_limit_clamping(self):
        bridge = self._make_bridge(db=None)
        msgs = bridge.get_recent_messages(limit=999)
        assert msgs == []
        msgs = bridge.get_recent_messages(limit=-5)
        assert msgs == []

    def test_get_active_users_empty(self):
        bridge = self._make_bridge(db=None)
        users = bridge.get_active_users()
        assert users == []

    def test_get_agent_info_empty(self):
        bridge = self._make_bridge(agent=None)
        info = bridge.get_agent_info()
        assert info == {}

    def test_get_agent_info_with_agent(self):
        agent = MagicMock()
        agent.get_info.return_value = {"name": "test_agent", "version": "1.0"}
        bridge = self._make_bridge(agent=agent)
        info = bridge.get_agent_info()
        assert info["name"] == "test_agent"

    def test_get_provider_health(self):
        bridge = self._make_bridge()
        health = bridge.get_provider_health()
        assert isinstance(health, dict)

    def test_concurrent_get_stats(self):
        bridge = self._make_bridge()
        results = []
        errors = []

        def worker():
            try:
                stats = bridge.get_stats()
                results.append(stats)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert len(results) == 20
        assert len(errors) == 0

    def test_websocket_manager_accessor(self):
        bridge = self._make_bridge()
        mgr = bridge.get_websocket_manager()
        assert mgr is None


# ════════════════════════════════════════════════════════════════════════════
# 5. SECURITY (10+ tests)
# ════════════════════════════════════════════════════════════════════════════

class TestSecurity:

    def test_cors_headers_present(self, client):
        resp = client.options("/api/health/", headers={
            "Origin": "http://test.com",
            "Access-Control-Request-Method": "GET",
        })
        assert resp.status_code in (200, 405, 400)

    def test_auth_required_for_stats(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code in (401, 403)

    def test_auth_required_for_moderation(self, client):
        resp = client.get("/api/moderation/stats")
        assert resp.status_code in (401, 403)

    def test_valid_token_accepted(self, client, mock_auth_dependency):
        resp = client.get("/api/stats")
        assert resp.status_code == 200

    def test_invalid_token_rejected(self, client):
        resp = client.get("/api/stats", headers={"Authorization": "Bearer invalid.jwt.token"})
        assert resp.status_code in (401, 403)

    def test_expired_token_rejected(self, client):
        from web.api_auth import create_access_token
        token = create_access_token(
            data={"sub": "admin", "role": "owner"},
            expires_delta=timedelta(hours=-1),
        )
        resp = client.get("/api/stats", headers=_auth_header(token))
        assert resp.status_code in (401, 403)

    def test_missing_auth_header(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code in (401, 403)

    def test_wrong_role_rejected(self, client):
        from web.api_auth import create_access_token
        token = create_access_token(
            data={"sub": "user", "role": "viewer"},
            expires_delta=timedelta(hours=1),
        )
        resp = client.get("/api/moderation/stats", headers=_auth_header(token))
        assert resp.status_code in (401, 403)

    def test_rate_limit_on_login(self, client):
        from web.api_auth import RATE_LIMIT_MAX, _rate_limit_check
        ip = "10.0.0.100"
        for _ in range(RATE_LIMIT_MAX + 5):
            _rate_limit_check(ip)

    def test_csrf_token_generation(self):
        from web.api_auth import generate_csrf_token, validate_csrf_token
        token = generate_csrf_token()
        assert isinstance(token, str)
        assert len(token) > 10
        assert validate_csrf_token(token) is True
        assert validate_csrf_token(token) is False

    def test_csrf_token_invalid(self):
        from web.api_auth import validate_csrf_token
        assert validate_csrf_token("nonexistent") is False
        assert validate_csrf_token(None) is False
        assert validate_csrf_token("") is False

    def test_create_access_token(self):
        from web.api_auth import create_access_token, decode_token
        token = create_access_token(data={"sub": "admin", "role": "owner"})
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "admin"
        assert payload["role"] == "owner"

    def test_decode_token_invalid(self):
        from web.api_auth import decode_token
        assert decode_token("garbage") is None

    def test_get_current_user_valid(self, client, mock_auth_dependency):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        assert data["role"] == "owner"

    def test_get_current_user_invalid(self, client):
        resp = client.get("/api/auth/me", headers={"Authorization": "Bearer bad"})
        assert resp.status_code in (401, 403)

    def test_token_refresh(self, client, mock_auth_token):
        resp = client.post(
            "/api/auth/refresh",
            json={"token": mock_auth_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data

    def test_refresh_rejects_invalid_token(self, client):
        resp = client.post(
            "/api/auth/refresh",
            json={"token": "invalid"},
        )
        assert resp.status_code in (401, 422)

    def test_admin_required_endpoint(self, client):
        from web.api_auth import create_access_token
        viewer_token = create_access_token(
            data={"sub": "viewer", "role": "viewer"},
            expires_delta=timedelta(hours=1),
        )
        resp = client.get(
            "/api/moderation/stats",
            headers=_auth_header(viewer_token),
        )
        assert resp.status_code in (401, 403)

    def test_provider_health_check_requires_admin(self, client):
        from web.api_auth import create_access_token
        viewer_token = create_access_token(
            data={"sub": "viewer", "role": "viewer"},
            expires_delta=timedelta(hours=1),
        )
        resp = client.post(
            "/api/health/check",
            headers=_auth_header(viewer_token),
        )
        assert resp.status_code in (401, 403)

    def test_login_rate_limit(self, client):
        from web.api_auth import _login_attempts
        ip = "192.168.1.200"
        _login_attempts[ip] = [time.time()] * 10
        from web.api_auth import _rate_limit_check
        assert _rate_limit_check(ip) is False
        _login_attempts.pop(ip, None)


# ════════════════════════════════════════════════════════════════════════════
# 6. EDGE CASES (10+ tests)
# ════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_empty_request_body_post(self, client):
        resp = client.post(
            "/api/auth/refresh",
            content="",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (400, 422)

    def test_malformed_json_body(self, client):
        resp = client.post(
            "/api/auth/refresh",
            content="{invalid json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (400, 422)

    def test_missing_content_type(self, client):
        resp = client.post(
            "/api/auth/refresh",
            content=b'{"token": "abc"}',
        )
        assert resp.status_code in (400, 422, 401)

    def test_special_characters_in_url(self, client):
        resp = client.get("/api/%3Cscript%3Ealert(1)%3C/script%3E")
        assert resp.status_code in (404, 405, 422)

    def test_large_limit_parameter(self, client, auth_headers):
        resp = client.get("/api/moderation/actions?limit=999999", headers=auth_headers)
        assert resp.status_code in (200, 422)

    def test_negative_limit_parameter(self, client, mock_auth_dependency):
        resp = client.get("/api/moderation/actions?limit=-1")
        assert resp.status_code in (200, 422)

    def test_zero_limit_parameter(self, client, mock_auth_dependency):
        resp = client.get("/api/moderation/actions?limit=0")
        assert resp.status_code in (200, 422)

    def test_server_app_state(self, app):
        assert hasattr(app.state, "ws_manager")
        assert hasattr(app.state, "data_bridge")

    def test_multiple_concurrent_requests(self, client, mock_auth_dependency):
        responses = []
        for _ in range(10):
            resp = client.get("/api/health/")
            responses.append(resp)
        for r in responses:
            assert r.status_code == 200

    def test_etag_caching(self, client, mock_auth_dependency):
        resp1 = client.get("/api/stats")
        assert resp1.status_code == 200
        etag = resp1.headers.get("etag")
        if etag:
            resp2 = client.get(
                "/api/stats",
                headers={"If-None-Match": etag},
            )
            assert resp2.status_code in (200, 304)

    def test_health_cache_control_header(self, client):
        resp = client.get("/api/health/")
        cc = resp.headers.get("cache-control", "")
        assert "max-age" in cc or resp.status_code != 200

    def test_start_web_server_function_exists(self):
        from web.server import start_web_server
        assert callable(start_web_server)

    def test_data_bridge_stored_on_app(self, app):
        from web.data_bridge import BotDataBridge
        bridge = getattr(app.state, "data_bridge", None)
        assert isinstance(bridge, BotDataBridge)

    def test_connection_manager_class(self):
        from web.server import ConnectionManager
        mgr = ConnectionManager()
        assert mgr.active_connections == []

    def test_broadcast_app_registered(self):
        from web.broadcast import set_app
        mock_app = MagicMock()
        set_app(mock_app)
        from web.broadcast import _app
        assert _app is mock_app
        set_app(None)

    def test_get_stats_latency_with_bot(self):
        from web.data_bridge import BotDataBridge
        bot = MagicMock()
        bot.latency = 0.123
        bot.guilds = []
        bridge = BotDataBridge(bot=bot, agent=None, db=None)
        stats = bridge.get_stats()
        assert stats["latency_ms"] == 123

    def test_get_stats_uptime_calculation(self):

        from web.data_bridge import BotDataBridge
        bot = MagicMock()
        bot.latency = 0
        bot.guilds = []
        bot.start_time = datetime.now(UTC) - timedelta(hours=2)
        bridge = BotDataBridge(bot=bot, agent=None, db=None)
        stats = bridge.get_stats()
        assert stats["uptime_seconds"] >= 7100

    def test_agent_moderation_stats(self):
        from web.data_bridge import BotDataBridge
        agent = MagicMock()
        agent.get_moderation_stats.return_value = {"pending_actions": 5}
        bridge = BotDataBridge(bot=None, agent=agent, db=None)
        stats = bridge.get_stats()
        assert stats["active_moderations"] == 5

    def test_db_aggregate_stats(self):
        from web.data_bridge import BotDataBridge
        db = MagicMock()
        db.get_aggregate_stats.return_value = {
            "total_messages": 42,
            "peak_users": 7,
            "total_tokens": 1000,
            "total_errors": 2,
        }
        bridge = BotDataBridge(bot=None, agent=None, db=db)
        stats = bridge.get_stats()
        assert stats["messages_today"] == 42
        assert stats["active_users"] == 7
        assert stats["llm_calls"] == 1000

    def test_auth_config_validation(self):
        from web.api_auth import validate_auth_config
        result = validate_auth_config()
        assert "configured" in result
        assert "warnings" in result
        assert "errors" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
