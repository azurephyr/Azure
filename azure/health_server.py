"""
Azure Health Check Server

A lightweight HTTP server that provides:
  - GET /health  → JSON status (uptime, memory, moderation stats)
  - GET /stats   → Full stats dashboard JSON
  - GET /        → Simple HTML dashboard

Usage:
    from azure.health_server import HealthServer
    server = HealthServer(port=8088, agent=agent)
    server.start()

The server runs in a background thread so it doesn't block the bot.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logger = logging.getLogger("azure.health_server")


_start_time = time.time()
_agent_ref: Any | None = None


def get_uptime_seconds() -> float:
    return time.time() - _start_time


def get_memory_usage() -> dict[str, float]:
    """Get process memory usage in MB."""
    try:
        import psutil
        proc = psutil.Process()
        mem = proc.memory_info()
        return {
            "rss_mb": round(mem.rss / (1024 * 1024), 2),
            "vms_mb": round(mem.vms / (1024 * 1024), 2),
            "percent": round(proc.memory_percent(), 2),
        }
    except ImportError:
        return {"rss_mb": 0, "vms_mb": 0, "percent": 0}


class HealthHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the health check server."""

    def log_message(self, format, *args):
        """Suppress default HTTP request logging (too noisy for bots)."""
        pass

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, indent=2)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/health", "/ping"):
            self._handle_health()
        elif self.path == "/ready":
            self._handle_ready()
        elif self.path == "/stats":
            self._handle_stats()
        elif self.path == "/":
            self._handle_dashboard()
        else:
            self._send_json({"error": "not found", "path": self.path}, status=404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _handle_health(self):
        healthy = True
        errors = []

        # Check agent
        if _agent_ref is None:
            healthy = False
            errors.append("agent not initialized")
        else:
            agent = _agent_ref
            llm_type = getattr(agent, "_llm_type", "none")
            if llm_type == "none" and getattr(agent, "llm", None) is None:
                healthy = False
                errors.append("no LLM available")
            elif llm_type == "local" and getattr(agent, "local_llm", None) is None:
                errors.append("local LLM not loaded")

        # Check moderation
        mod_stats = {}
        if _agent_ref and _agent_ref.moderation:
            try:
                mod_stats = _agent_ref.get_moderation_stats()
            except Exception as e:
                errors.append(f"moderation stats error: {e}")

        readiness = {}
        try:
            from bot.context import ctx
            readiness = ctx.readiness_summary()
            if not readiness.get("ready", False):
                healthy = False
                if "core path not ready" not in errors:
                    errors.append("core path not ready")
        except Exception as e:
            errors.append(f"readiness error: {e}")

        data = {
            "status": "ok" if healthy else "degraded",
            "healthy": healthy,
            "uptime_seconds": round(get_uptime_seconds(), 1),
            "memory": get_memory_usage(),
            "errors": errors,
            "moderation": mod_stats,
            "readiness": readiness,
            "version": "azure-v2",
        }
        self._send_json(data, status=200 if healthy else 503)

    def _handle_ready(self):
        """Kubernetes-style readiness: 200 only when golden path can chat."""
        try:
            from bot.context import ctx
            summary = ctx.readiness_summary()
            ready = bool(summary.get("ready"))
            self._send_json({"ready": ready, **summary}, status=200 if ready else 503)
        except Exception as e:
            self._send_json({"ready": False, "error": str(e)[:200]}, status=503)

    def _handle_stats(self):
        uptime = get_uptime_seconds()
        hours, remainder = divmod(uptime, 3600)
        minutes, seconds = divmod(remainder, 60)

        stats = {
            "uptime": f"{int(hours)}h {int(minutes)}m {int(seconds)}s",
            "uptime_seconds": round(uptime, 1),
            "memory": get_memory_usage(),
            "version": "azure-v2",
        }

        if _agent_ref:
            agent = _agent_ref
            info = agent.get_info()
            stats["agent"] = info

            if agent.moderation:
                try:
                    stats["moderation"] = agent.get_moderation_stats()
                except Exception as e:
                    stats["moderation_error"] = str(e)

        self._send_json(stats)

    def _handle_dashboard(self):
        uptime = get_uptime_seconds()
        hours, remainder = divmod(uptime, 3600)
        minutes, seconds = divmod(remainder, 60)
        mem = get_memory_usage()

        # Build a simple but nice HTML dashboard
        mod_html = "<p>Moderation engine not initialized.</p>"
        agent_mode = "not initialized"
        if _agent_ref:
            agent = _agent_ref
            agent_mode = agent.get_info().get("mode", "unknown")
            if agent.moderation:
                try:
                    ms = agent.get_moderation_stats()
                    mod_html = f"""
                    <div class="stat-row"><span>Phase:</span><span>{ms.get('phase', 'unknown')}</span></div>
                    <div class="stat-row"><span>Mode:</span><span>{ms.get('mode', 'unknown')}</span></div>
                    <div class="stat-row"><span>Dry Run:</span><span>{ms.get('dry_run', True)}</span></div>
                    <div class="stat-row"><span>Actions Taken:</span><span>{json.dumps(ms.get('actions_taken', {}))}</span></div>
                    <div class="stat-row"><span>Cache Size:</span><span>{ms.get('cache_size', 0)}</span></div>
                    """
                except Exception as e:
                    mod_html = f"<p>Error: {e}</p>"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Azure Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #eee; min-height: 100vh; }}
        .container {{ max-width: 800px; margin: 0 auto; padding: 2rem; }}
        h1 {{ font-size: 2rem; margin-bottom: 0.5rem; color: #e94560; }}
        .subtitle {{ color: #888; margin-bottom: 2rem; }}
        .card {{ background: #16213e; border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; border: 1px solid #0f3460; }}
        .card h2 {{ font-size: 1.2rem; margin-bottom: 1rem; color: #e94560; }}
        .stat-row {{ display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #0f3460; }}
        .stat-row:last-child {{ border-bottom: none; }}
        .stat-row span:first-child {{ color: #888; }}
        .badge {{ display: inline-block; padding: 0.25rem 0.75rem; border-radius: 999px; font-size: 0.8rem; font-weight: 600; }}
        .badge-ok {{ background: #2ecc71; color: #1a1a2e; }}
        .badge-warn {{ background: #f39c12; color: #1a1a2e; }}
        .badge-err {{ background: #e74c3c; color: white; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }}
        footer {{ text-align: center; color: #555; font-size: 0.8rem; margin-top: 2rem; }}
    </style>
    <meta http-equiv="refresh" content="30">
</head>
<body>
    <div class="container">
        <h1>🛡️ Azure</h1>
        <p class="subtitle">Autonomous Discord Guardian — Dashboard</p>

        <div class="grid">
            <div class="card">
                <h2>⏱ Uptime</h2>
                <div class="stat-row"><span>Running</span><span>{int(hours)}h {int(minutes)}m {int(seconds)}s</span></div>
                <div class="stat-row"><span>Agent Mode</span><span>{agent_mode}</span></div>
            </div>

            <div class="card">
                <h2>💾 Memory</h2>
                <div class="stat-row"><span>RSS</span><span>{mem.get('rss_mb', 0)} MB</span></div>
                <div class="stat-row"><span>VMS</span><span>{mem.get('vms_mb', 0)} MB</span></div>
                <div class="stat-row"><span>Percent</span><span>{mem.get('percent', 0)}%</span></div>
            </div>
        </div>

        <div class="card">
            <h2>🛡️ Moderation</h2>
            {mod_html}
        </div>

        <footer>
            Azure v2 &middot; Refreshes every 30s &middot; <a href="/health" style="color:#e94560">JSON API</a>
        </footer>
    </div>
</body>
</html>"""
        self._send_html(html)


class HealthServer:
    """Health check server that runs in a background thread."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8088, agent=None):
        self.host = host
        self.port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._running = False

        # Set the global agent reference for the handler
        global _agent_ref
        _agent_ref = agent

    def start(self):
        """Start the health check server in a background thread."""
        if self._running:
            return

        self._server = HTTPServer((self.host, self.port), HealthHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._running = True
        logger.info(f"[health_server] listening on http://{self.host}:{self.port}/health")


    def stop(self):
        """Stop the health check server."""
        if self._server:
            self._server.shutdown()
            self._running = False
            logger.info("[health_server] stopped")


    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"
