import concurrent.futures
import contextlib
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .api_auth import get_current_user, require_admin

router = APIRouter()

_audit_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

class PhaseUpdate(BaseModel):
    phase: str

class ModeUpdate(BaseModel):
    mode: str

@router.get("/current")
async def get_current_config(request: Request, user: dict = Depends(get_current_user)):
    agent = getattr(request.app.state, "agent", None)
    if not agent:
        return {}

    info = agent.get_info()
    return info

def _audit_config_change(request: Request, user: dict, action: str, old: str, new: str) -> None:
    """Best-effort audit row so Operations Center shows dashboard mutations."""
    import asyncio
    db = getattr(request.app.state, "db", None)
    if not db:
        return
    def _write():
        try:
            with db._wlock:
                conn = db._get_connection()
                cursor = conn.cursor()
                import time as _time
                cursor.execute(
                    "INSERT INTO audit_logs (timestamp, user_name, discord_id, ip_address, "
                    "session_id, action, old_value, new_value, reason, subsystem) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_time.time(), user.get("username", "admin"), "web", "", "",
                     action, old, new, "dashboard config change", "web_config"),
                )
                conn.commit()
        except Exception as e:
            logging.getLogger("web.config").debug("audit log write failed: %s", e)
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(_audit_executor, _write)
    except Exception:
        logging.getLogger("web.config").exception("[api_config] audit log write failed")



@router.post("/phase")
async def update_phase(req: PhaseUpdate, request: Request, user: dict = Depends(require_admin)):
    agent = getattr(request.app.state, "agent", None)
    if not agent or not agent.moderation:
        raise HTTPException(status_code=400, detail="Moderation engine offline")

    old = ""
    with contextlib.suppress(Exception):
        old = agent.moderation.policy.phase.value
    try:
        agent.set_moderation_phase(req.phase)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to set phase: {e}") from e
    _audit_config_change(request, user, "set_moderation_phase", old, req.phase)
    # Broadcast state change to all websockets
    ws = getattr(request.app.state, "ws_manager", None)
    if ws is not None:
        await ws.broadcast({"type": "CONFIG_UPDATE", "data": {"phase": req.phase}})

    return {"status": "success", "phase": req.phase}

@router.post("/mode")
async def update_mode(req: ModeUpdate, request: Request, user: dict = Depends(require_admin)):
    agent = getattr(request.app.state, "agent", None)
    if not agent or not agent.moderation:
        raise HTTPException(status_code=400, detail="Moderation engine offline")

    old = getattr(getattr(agent.moderation, "policy", None), "mode", "")
    try:
        agent.set_moderation_mode(req.mode)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to set mode: {e}") from e
    _audit_config_change(request, user, "set_moderation_mode", str(old), req.mode)

    ws = getattr(request.app.state, "ws_manager", None)
    if ws is not None:
        await ws.broadcast({"type": "CONFIG_UPDATE", "data": {"mode": req.mode}})

    return {"status": "success", "mode": req.mode}

@router.post("/emergency_stop")
async def emergency_stop(request: Request, user: dict = Depends(require_admin)):
    agent = getattr(request.app.state, "agent", None)
    if not agent:
        raise HTTPException(status_code=400, detail="Agent offline")

    if not agent.moderation:
        raise HTTPException(status_code=400, detail="Moderation engine offline")

    try:
        agent.moderation.emergency_stop()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Emergency stop failed: {e}") from e
    _audit_config_change(request, user, "emergency_stop", "", "dry_run")

    ws = getattr(request.app.state, "ws_manager", None)
    if ws is not None:
        await ws.broadcast({"type": "EMERGENCY_STOP_TRIGGERED"})

    return {"status": "success", "message": "Emergency stop activated. Phase set to DRY_RUN."}
