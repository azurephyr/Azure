#!/usr/bin/env python3
"""
Master End-to-End Simulation & Subsystem Verification Runner.
This script performs a complete live/simulated run of the entire Azure AI bot
platform, including config validation, database retry wrapper, agent pipeline, RAG vector
store search, moderation confirmation queue, circuit breaker state-machine, and the
Uvicorn FastAPI app server.

No third-party testing dependencies are needed; uses only standard library components.
"""

from __future__ import annotations

import asyncio
import http.client
import logging
import os
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# Configure path resolution
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("verify_e2e")

# Colors for pretty terminal logs
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def check(name: str, status: bool, info: str = "") -> None:
    """Print a styled verification checkpoint status."""
    if status:
        print(f"  {GREEN}[OK]{RESET} {name:<45} {GREEN}SUCCESS{RESET} {info}")
    else:
        print(f"  {RED}[ERROR]{RESET} {name:<45} {RED}FAILED{RESET} {info}")
        sys.exit(1)


def get_free_port() -> int:
    """Dynamically allocate a free local TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_fastapi_server(port: int, stop_event: threading.Event) -> None:
    """Start Uvicorn server in a daemon thread."""
    import uvicorn
    from web.server import app

    # Configure dummy authentication and mock objects for application state
    app.state.agent = MagicMock()
    app.state.bot = MagicMock()
    
    # Initialize mock database manager
    mock_db = MagicMock()
    mock_db.execute.return_value = []
    mock_db.get_aggregate_stats.return_value = {}
    app.state.db = mock_db

    # Wire up a mock data bridge
    from web.data_bridge import BotDataBridge
    app.state.data_bridge = BotDataBridge(app.state.bot, app.state.agent, app.state.db)

    # Bypass JWT authentication for stats and config endpoints
    from web.api_auth import get_current_user, require_admin
    mock_user = {"username": "admin", "role": "owner"}
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[require_admin] = lambda: mock_user

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        loop="asyncio"
    )
    server = uvicorn.Server(config)
    
    # Run the server loop in asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    async def serve_with_stop():
        serve_task = asyncio.create_task(server.serve())
        while not stop_event.is_set():
            await asyncio.sleep(0.1)
        server.should_exit = True
        await serve_task

    loop.run_until_complete(serve_with_stop())
    loop.close()


def test_http_endpoint(port: int, path: str) -> dict | list | str:
    """Perform a simple GET request on the local test server and return JSON, with retries on connection failure."""
    import json
    max_attempts = 15
    for attempt in range(max_attempts):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", path)
            resp = conn.getresponse()
            data = resp.read().decode("utf-8")
            conn.close()
            if resp.status == 200:
                try:
                    return json.loads(data)
                except json.JSONDecodeError:
                    return data
            else:
                raise RuntimeError(f"HTTP {resp.status}: {data}")
        except (ConnectionRefusedError, ConnectionResetError, OSError) as e:
            if attempt == max_attempts - 1:
                raise
            time.sleep(0.5)


def main() -> None:
    print(f"\n=======================================================")
    print(f"         AZURE PLATFORM SYSTEM VERIFICATION RUNNER     ")
    print(f"=======================================================\n")

    # ----------------------------------------------------
    # PHASE 1: CONFIGURATION VALIDATION
    # ----------------------------------------------------
    print(f"PHASE 1: Config & Env Validation")
    from dotenv import load_dotenv
    env_loaded = load_dotenv(ROOT / ".env")
    check("Load local .env configuration", env_loaded, f"(File size: {ROOT.joinpath('.env').stat().st_size} bytes)")

    # ----------------------------------------------------
    # PHASE 2: DATABASE RESILIENCE & RETRIES
    # ----------------------------------------------------
    print(f"\nPHASE 2: Database Subsystem Verification")
    import sqlite3
    from azure.database import DatabaseManager

    db_path = ROOT / "data" / "verify_e2e_test.db"
    if db_path.exists():
        db_path.unlink()

    db = DatabaseManager(db_path=db_path)
    check("Initialize DatabaseManager instance", db_path.exists(), f"({db_path.name})")

    # Test basic writes & reads
    from azure.database import UserPreference
    pref_obj = UserPreference(
        user_id="12345",
        user_name="TestUser",
        tier="premium",
        context_size=20,
        temperature=0.5,
        language="en",
        created_at=time.time(),
        updated_at=time.time(),
    )
    db.save_user_preference(pref_obj)
    pref = db.get_user_preference("12345")
    check("Save and retrieve user preferences", pref is not None and pref.tier == "premium", f"(Got: {pref.tier if pref else None})")

    # Verify retry resilience on database operational lockups
    attempts = [0]
    def failing_db_op():
        attempts[0] += 1
        if attempts[0] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "success_after_retries"

    res = db._execute_with_retry(failing_db_op, max_retries=5)
    check("Database transient error retry wrapper", res == "success_after_retries" and attempts[0] == 3)

    db.close()
    if db_path.exists():
        db_path.unlink()

    # ----------------------------------------------------
    # PHASE 3: AGENT & LOCAL RAG
    # ----------------------------------------------------
    print(f"\nPHASE 3: Agent & Local RAG Simulation")
    from azure.rag_engine import DiscordRAG

    mock_st = MagicMock()
    mock_st.return_value.get_embedding_dimension.return_value = 384
    mock_st.return_value.encode.return_value = [0.15] * 384

    # Patch SentenceTransformer so we don't fetch anything over the network
    with patch("azure.rag_engine._SentenceTransformer", mock_st):
        rag = DiscordRAG(persist_path=ROOT / "data" / "verify_rag.json", max_docs=100)
        check("Initialize Local RAG Engine", rag is not None)

        # Test index, add & search lifecycle
        doc_id = rag.add("Rule 1: Admins must confirm destructive actions.", {"rule_id": 1})
        check("RAG Index Document Storage", doc_id is not None)

        search_results = rag.search("destructive action rules", k=1)
        check("RAG Vector Index Search Query", len(search_results) > 0 and "destructive" in search_results[0]["text"])

        # Clean up test files
        if rag.persist_path and rag.persist_path.exists():
            rag.persist_path.unlink()

    # ----------------------------------------------------
    # PHASE 4: MODERATION PIPELINE
    # ----------------------------------------------------
    print(f"\nPHASE 4: Moderation Confirmation Queue")
    from azure.moderation.confirmation import ConfirmationQueue

    q = ConfirmationQueue(timeout_seconds=60)
    check("Initialize ConfirmationQueue", q is not None)

    # Put action into confirmation queue
    pending_action = q.add(
        message_id="msg_999",
        user_id="user_abc",
        user_name="SpammyUser",
        action_type="kick",
        reason="Sending excessive spam links",
        channel_id="chan_gen",
        channel_name="general",
        confidence=0.92,
        risk_score=0.88,
        explanation="Detected burst of links"
    )
    check("Add to Confirmation Queue", pending_action.message_id == "msg_999")

    # Confirm action
    confirmed = q.confirm("msg_999")
    check("Confirm Queue Action", confirmed is not None and confirmed.action_type == "kick")
    
    # Ensure queue has zero items left
    check("Queue Empty After Confirmation", len(q.list_pending()) == 0)

    # ----------------------------------------------------
    # PHASE 5: CIRCUIT BREAKER STATE MACHINE
    # ----------------------------------------------------
    print(f"\nPHASE 5: Circuit Breaker Verification")
    from azure.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.02)
    check("CLOSED: Allows requests initially", cb.allow_request() is True and cb.state == "CLOSED")

    cb.record_failure()
    cb.record_failure()
    check("OPEN: Blocks requests after threshold", cb.allow_request() is False and cb.state == "OPEN")

    # Advance time manually to verify transitions to HALF_OPEN
    time.sleep(0.05)
    check("HALF_OPEN: Cooldown elapsed", cb.state == "HALF_OPEN" and cb.allow_request() is True)

    cb.record_success()
    check("CLOSED: Success resets breaker state", cb.state == "CLOSED" and cb.allow_request() is True)

    # ----------------------------------------------------
    # PHASE 6: FASTAPI SERVER & WEB DASHBOARD API
    # ----------------------------------------------------
    print(f"\nPHASE 6: Uvicorn FastAPI Server & Dashboard API")
    port = get_free_port()
    stop_event = threading.Event()

    server_thread = threading.Thread(
        target=run_fastapi_server,
        args=(port, stop_event),
        daemon=True
    )
    server_thread.start()
    
    # Wait for server to boot up
    time.sleep(1.0)
    
    try:
        health_resp = test_http_endpoint(port, "/api/health/")
        check("GET /api/health/ (Status API)", isinstance(health_resp, dict) and "status" in health_resp)

        stats_resp = test_http_endpoint(port, "/api/stats")
        check("GET /api/stats (Metrics API)", isinstance(stats_resp, dict) and "health_score" in stats_resp)

        config_resp = test_http_endpoint(port, "/api/config/current")
        check("GET /api/config/current (Settings API)", isinstance(config_resp, dict))

        classic_page = test_http_endpoint(port, "/classic")
        check("GET /classic (Alias redirect page)", isinstance(classic_page, str) and "html" in classic_page.lower())

    finally:
        # Trigger clean shutdown
        stop_event.set()
        server_thread.join(timeout=3.0)
        check("FastAPI Server Clean Shutdown", not server_thread.is_alive())

    print(f"\n=======================================================")
    print(f"  {GREEN}*** ALL INTEGRATION VERIFICATION CHECKS PASSED ***{RESET}")
    print(f"=======================================================\n")


if __name__ == "__main__":
    main()
