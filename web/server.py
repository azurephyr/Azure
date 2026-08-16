import asyncio
import hashlib
import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .api_analytics import router as analytics_router
from .api_auth import router as auth_router
from .api_config import router as config_router
from .api_dashboard import router as dashboard_router
from .api_health import router as health_router
from .api_logs import router as logs_router
from .api_moderation import router as mod_router
from .api_settings import router as settings_router
from .api_users import router as users_router
from .broadcast import set_app as _register_broadcast_app
from .data_bridge import BotDataBridge

logger = logging.getLogger("azure.web")

app = FastAPI(
    title="Azure Operating Platform",
    description="Central control center for Azure AI",
    version="1.0.0"
)

# CORS config - restrict in production.
allowed_origins_str = os.environ.get("AZURE_WEB_ALLOWED_ORIGINS", "")
allow_credentials = False
if not allowed_origins_str:
    allowed_origins = []
    logger.warning(
        "CORS is locked down (no origins configured). "
        "Set AZURE_WEB_ALLOWED_ORIGINS to a comma-separated list of allowed "
        "origins to permit browser access. Credentials will not be sent."
    )
elif allowed_origins_str.strip() == "*":
    allowed_origins = ["*"]
    allow_credentials = False
    logger.warning(
        "CORS set to '*'; credentials disabled (browser spec violation otherwise). "
        "For production: set AZURE_WEB_ALLOWED_ORIGINS=https://yourdomain.com"
    )
else:
    allowed_origins = [origin.strip() for origin in allowed_origins_str.split(",") if origin.strip()]
    allow_credentials = True
    logger.info("CORS restricted to origins: %s", allowed_origins)

# Middleware always needs a non-empty list; fall back to "null" when locked down.
origins_for_middleware = allowed_origins if allowed_origins else ["null"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins_for_middleware,
    allow_credentials=allow_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)


# ---------------------------------------------------------------------------
# Performance Middleware: Cache headers + ETag
# ---------------------------------------------------------------------------

@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path

    if path.startswith("/static/"):
        # Static assets: long cache (1 hour)
        if hasattr(response, "headers"):
            response.headers["Cache-Control"] = "public, max-age=3600, immutable"
    elif path.startswith("/api/"):
        # API endpoints: short cache (30s) for GET non-critical, no cache for POST
        if request.method == "GET" and hasattr(response, "headers") and not any(p in path for p in ["/auth", "/config/phase", "/config/mode",
                                                                                                        "/config/emergency", "/provider/test",
                                                                                                        "/provider/config"]):
                chunks = []
                async for chunk in response.body_iterator:
                    chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
                body = b"".join(chunks)
                # The original response may carry a Content-Length from a
                # compression or streaming layer. Reusing it after consuming
                # and rebuilding the body can make Uvicorn report a shorter
                # response than advertised, especially for 304 responses.
                response_headers = dict(response.headers)
                response_headers.pop("content-length", None)
                etag = hashlib.md5(body).hexdigest()
                response = Response(
                    content=body,
                    status_code=response.status_code,
                    headers=response_headers,
                    media_type=response.media_type,
                )
                # "private" (not "public"): API responses are authenticated and
                # per-user. "public" would let shared proxies/CDNs cache one
                # user's data and serve it to another.
                response.headers["Cache-Control"] = "private, max-age=30"
                response.headers["ETag"] = f'"{etag}"'

                # Check If-None-Match for 304
                if_none_match = request.headers.get("if-none-match")
                if if_none_match and if_none_match.strip('"') == etag:
                    not_modified_headers = dict(response.headers)
                    not_modified_headers.pop("content-length", None)
                    not_modified_headers.pop("content-encoding", None)
                    response = Response(status_code=304, headers=not_modified_headers)
    elif path.startswith("/") and path.endswith(".html") and path.count("/") <= 2 and hasattr(response, "headers"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    return response


# Mount static files
web_dir = Path(__file__).parent
static_dir = web_dir / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# WebSocket Connection Manager for real-time Sync
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        # Keep the public list for compatibility with existing integrations,
        # while storing authorization metadata separately per socket.
        self._connection_meta: dict[WebSocket, dict[str, object]] = {}

    async def connect(
        self,
        websocket: WebSocket,
        *,
        username: str = "",
        role: str = "",
        allowed_guild_ids: set[str] | None = None,
    ):
        await websocket.accept()
        self.active_connections.append(websocket)
        self._connection_meta[websocket] = {
            "username": username,
            "role": role,
            "allowed_guild_ids": allowed_guild_ids,
            # None means every authorized guild. An empty set means no
            # guild-scoped events until an allowlist is configured.
            "guild_ids": None if allowed_guild_ids is None else set(allowed_guild_ids),
        }

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self._connection_meta.pop(websocket, None)

    def subscribe(self, websocket: WebSocket, guild_ids: list[str] | set[str]) -> set[str]:
        """Set the guild event filter for a connected socket.

        An allowlist is enforced server-side so a client cannot subscribe to
        a guild that was not authorized when its connection was established.
        """
        if websocket not in self.active_connections:
            raise ValueError("WebSocket is not connected")
        requested = {str(guild_id) for guild_id in guild_ids if str(guild_id).isdigit()}
        if len(requested) > 100:
            raise ValueError("A maximum of 100 guilds may be subscribed")
        metadata = self._connection_meta.setdefault(websocket, {
            "allowed_guild_ids": None,
            "guild_ids": None,
        })
        allowed = metadata.get("allowed_guild_ids")
        if allowed is not None and not requested.issubset(allowed):
            raise ValueError("Subscription includes an unauthorized guild")
        metadata["guild_ids"] = requested
        return requested

    @staticmethod
    def _event_guild_id(message: dict) -> str:
        data = message.get("data")
        if isinstance(data, dict):
            value = data.get("guild_id")
            if value:
                return str(value)
        value = message.get("guild_id")
        return str(value) if value else ""

    def _should_deliver(self, connection: WebSocket, message: dict, guild_id: str | None) -> bool:
        metadata = self._connection_meta.get(connection)
        if metadata is None:
            # Preserve compatibility with callers that manage the public list
            # directly, including test and embedding integrations.
            return True
        target_guild = str(guild_id or self._event_guild_id(message))
        if not target_guild:
            return True
        allowed = metadata.get("allowed_guild_ids")
        if allowed is not None and target_guild not in allowed:
            return False
        subscribed = metadata.get("guild_ids")
        return subscribed is None or target_guild in subscribed

    async def broadcast(self, message: dict, *, guild_id: str | None = None):
        # Snapshot the list: each await yields to the event loop, so another
        # coroutine can connect/disconnect mid-iteration. Mutating the live
        # list during iteration raises RuntimeError or skips clients.
        # O(n) copy of references only — not of message payloads.
        disconnected = []
        for connection in list(self.active_connections):
            if not self._should_deliver(connection, message, guild_id):
                continue
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.debug("WebSocket send failed, marking disconnected: %s", e)
                disconnected.append(connection)

        for d in disconnected:
            self.disconnect(d)


def _allowed_web_guild_ids() -> set[str]:
    """Return the guilds permitted for dashboard real-time events."""
    return {
        value.strip()
        for value in os.environ.get("AZURE_WEB_ALLOWED_GUILD_IDS", "").split(",")
        if value.strip().isdigit()
    }

manager = ConnectionManager()
app.state.ws_manager = manager

_register_broadcast_app(app)

data_bridge = BotDataBridge(bot=None, agent=None, db=None)
app.state.data_bridge = data_bridge

# ---------------------------------------------------------------------------
# API Routing
# ---------------------------------------------------------------------------

app.include_router(health_router, prefix="/api/health", tags=["Health"])
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(mod_router, prefix="/api/moderation", tags=["Moderation"])
app.include_router(config_router, prefix="/api/config", tags=["Config"])
app.include_router(analytics_router, prefix="/api/analytics", tags=["Analytics"])
app.include_router(settings_router, prefix="/api/settings", tags=["Settings"])
app.include_router(dashboard_router, prefix="/api", tags=["Dashboard"])
app.include_router(logs_router, prefix="/api", tags=["Logs"])
app.include_router(users_router, prefix="/api/users", tags=["Users"])

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time updates.

    SECURITY: Requires authentication via query parameter 'token'
    Example: ws://host/ws?token=<jwt-token>

    To connect:
    1. Login via POST /api/auth/token to get JWT
    2. Connect to /ws?token=<jwt>
    """
    # Extract token from query parameters
    token = websocket.query_params.get("token")

    if not token:
        await websocket.close(code=1008, reason="Missing authentication token")
        logger.warning("WebSocket connection rejected: no token provided")
        return

    # Validate token
    try:
        from jose import JWTError, jwt

        from .api_auth import ALGORITHM, SECRET_KEY

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        role = payload.get("role")

        if not username:
            await websocket.close(code=1008, reason="Invalid token")
            logger.warning("WebSocket connection rejected: invalid token")
            return
        if role not in {"owner", "admin"}:
            await websocket.close(code=1008, reason="Insufficient role")
            logger.warning("WebSocket connection rejected for %s: role %s", username, role)
            return

        logger.info(f"WebSocket authenticated: {username} ({role})")

    except JWTError as e:
        await websocket.close(code=1008, reason="Authentication failed")
        logger.warning(f"WebSocket authentication failed: {e}")
        return

    # Accept connection after authentication
    # An explicit allowlist is required for guild-scoped events. System
    # events such as health updates remain available without a guild target.
    await manager.connect(
        websocket,
        username=username,
        role=role,
        allowed_guild_ids=_allowed_web_guild_ids(),
    )
    try:
        while True:
            # Accept JSON or text pings; never crash the socket on bad payloads
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception as e:
                # Non-JSON frames: ignore
                logger.debug("Non-JSON WebSocket frame from %s: %s", username, e)
                continue
            if not isinstance(data, dict):
                continue
            if data.get("action") == "ping":
                await websocket.send_json({"action": "pong", "user": username})
            elif data.get("action") == "subscribe":
                requested = data.get("guild_ids", [])
                if not isinstance(requested, list):
                    await websocket.send_json({"action": "subscription_error", "reason": "guild_ids must be a list"})
                    continue
                try:
                    subscribed = manager.subscribe(websocket, requested)
                except ValueError as exc:
                    await websocket.send_json({"action": "subscription_error", "reason": str(exc)})
                else:
                    await websocket.send_json({
                        "action": "subscribed",
                        "guild_ids": sorted(subscribed),
                    })
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info(f"WebSocket disconnected: {username}")
    except Exception as e:
        manager.disconnect(websocket)
        logger.warning(f"WebSocket error for {username}: {e}")

async def _serve_template(name: str, fallback: str) -> HTMLResponse:
    """Read and serve an HTML template file, with fallback text."""
    path = web_dir / "templates" / name
    if not path.exists():
        return HTMLResponse(fallback)
    def _read():
        with open(path, encoding="utf-8") as f:
            return f.read()
    content = await asyncio.get_running_loop().run_in_executor(None, _read)
    return HTMLResponse(content)


@app.get("/")
async def serve_dashboard():
    return await _serve_template("dashboard.html", "<h1>Azure Web Platform</h1><p>Building...</p>")


@app.get("/index.html")
async def serve_classic():
    return await _serve_template("index.html", "<h1>Azure Web Platform</h1><p>Classic view not found.</p>")


@app.get("/classic")
async def serve_classic_alias():
    return await _serve_template("index.html", "<h1>Azure Web Platform</h1><p>Classic view not found.</p>")


@app.get("/analytics")
async def serve_analytics():
    return await _serve_template("analytics.html", "<h1>Analytics</h1><p>Page not found.</p>")


@app.get("/logs")
async def serve_logs():
    return await _serve_template("logs.html", "<h1>Logs</h1><p>Page not built yet.</p>")


@app.get("/users")
async def serve_users():
    return await _serve_template("users.html", "<h1>Users</h1><p>Page not built yet.</p>")


@app.get("/settings")
async def serve_settings():
    return await _serve_template("settings.html", "<h1>Settings</h1><p>Settings page not found.</p>")





def start_web_server(agent, bot, db, host="127.0.0.1", port=8080):
    """
    Start the FastAPI server via Uvicorn programmatically.
    This function is intended to be run in a separate thread OR as an asyncio task.
    Since we are already in the Discord.py event loop, we will run the uvicorn Server
    as an async task.
    """
    # Validate authentication configuration before starting
    from .api_auth import validate_auth_config
    auth_status = validate_auth_config()

    if not auth_status["configured"]:
        logger.error("=" * 70)
        logger.error("⚠️  WEB DASHBOARD AUTHENTICATION NOT CONFIGURED")
        logger.error("=" * 70)
        for error in auth_status["errors"]:
            logger.error(f"   ERROR: {error}")
        logger.error("")
        logger.error("   Dashboard startup refused. Configure credentials and a stable secret.")
        logger.error("=" * 70)
        raise RuntimeError("Web dashboard authentication is not configured securely")

    if auth_status["warnings"]:
        logger.warning("Web dashboard authentication warnings:")
        for warning in auth_status["warnings"]:
            logger.warning(f"  - {warning}")

    # Inject dependencies — single source of truth for API routers
    app.state.agent = agent
    app.state.bot = bot
    app.state.db = db
    app.state.ws_manager = manager

    # Wire data bridge to live objects so API endpoints get real-time data
    bridge = getattr(app.state, "data_bridge", None)
    if bridge is not None:
        bridge._bot = bot
        bridge._agent = agent
        bridge._db = db

    # Register process-wide DB for audit/telemetry/access-control readers
    try:
        from azure.database import set_shared_db
        if db is not None:
            set_shared_db(db)
    except Exception as e:
        logger.warning("[web] set_shared_db failed: %s", e)
    try:
        from azure.telemetry import set_telemetry_db
        if db is not None:
            set_telemetry_db(db)
    except Exception as e:
        logger.warning("[web] set_telemetry_db failed: %s", e)

    config = uvicorn.Config(app, host=host, port=port, loop="asyncio", log_level="info")
    server = uvicorn.Server(config)

    logger.info(f"[web] Starting Azure Platform on http://{host}:{port}")
    return server.serve()  # Returns a coroutine we can await in the main loop
