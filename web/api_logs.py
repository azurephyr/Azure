import contextlib
import json
import logging
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from .api_auth import get_current_user

router = APIRouter()

logger = logging.getLogger("web.logs")

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
with contextlib.suppress(Exception):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _parse_std_log_line(line: str) -> dict:
    """Parse a Python-style log line: '2024-01-01 12:00:00,000 - name - LEVEL - message'"""
    m = re.match(
        r"^(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}[,.]?\d*)\s*-\s*(\S+)\s*-\s*(\w+)\s*-\s*(.*)",
        line,
    )
    if m:
        return {
            "timestamp": m.group(1),
            "module": m.group(2),
            "level": m.group(3).upper(),
            "message": m.group(4),
        }
    return {"timestamp": "", "module": "unknown", "level": "INFO", "message": line}


def _read_text_log(path: Path, limit: int = 500) -> list[dict]:
    """Read a plain-text log file, parse lines, return newest-first."""
    entries = []
    if not path.exists():
        return entries
    try:
        if path.stat().st_size == 0:
            return entries
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            if not lines:
                return entries
    except Exception:
        return entries
    for line in lines[-limit:]:
        line = line.rstrip("\n\r")
        if not line.strip():
            continue
        parsed = _parse_std_log_line(line)
        parsed["source_file"] = path.name
        parsed["category"] = "bot"
        entries.append(parsed)
    return entries


def _read_jsonl_log(path: Path, limit: int = 500) -> list[dict]:
    """Read a JSONL log file, return newest-first."""
    entries = []
    if not path.exists():
        return entries
    try:
        if path.stat().st_size == 0:
            return entries
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            if not lines:
                return entries
    except Exception:
        return entries
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            entries.append({"timestamp": "", "module": "unknown", "level": "INFO", "message": line, "source_file": path.name})
            continue
        ts = obj.get("timestamp") or obj.get("t") or 0
        if isinstance(ts, (int, float)):
            try:
                ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
            except Exception:
                ts_str = str(ts)
        else:
            ts_str = str(ts)
        level = obj.get("severity", obj.get("level", "INFO")).upper()
        if level == "NONE":
            level = "INFO"
        module = path.stem
        msg = obj.get("message", obj.get("error_msg", obj.get("reason", json.dumps(obj, default=str)[:300])))
        if path.name == "moderation_actions.jsonl":
            module = "moderation"
            act = obj.get("action", "")
            msg = f"[{act}] {obj.get('user_name', '?')}: {obj.get('reason', '')} (conf={obj.get('confidence', 0)})"
            level = "WARN" if obj.get("severity") not in (None, "none", "NONE", "normal") else "INFO"
        elif path.name == "moderation_monitor.jsonl":
            module = "moderation"
            msg = f"[{obj.get('action_taken', '')}] {obj.get('author_name', '?')}: {obj.get('category', '')} (sev={obj.get('severity', '')})"
            level = "WARN" if obj.get("severity") not in (None, "none", "NONE", "normal") else "INFO"
        elif path.name.startswith("errors"):
            module = "error"
            level = "ERROR"
            msg = f"[{obj.get('operation', '')}] {obj.get('error_type', '')}: {obj.get('error_msg', '')}"
        elif "guild" in path.name:
            module = "agent"
            ok = obj.get("success", True)
            level = "ERROR" if not ok else "INFO"
            msg = f"[{obj.get('action', '')}] by {obj.get('performed_by', '?')}: {obj.get('request_text', '')[:100]}"

        entries.append({
            "timestamp": ts_str,
            "module": module,
            "level": level,
            "message": msg,
            "source_file": path.name,
            "category": _guess_category(path.name, level),
        })
    return entries


def _guess_category(filename: str, level: str) -> str:
    if "error" in filename.lower():
        return "errors"
    if "moderation" in filename.lower():
        return "moderation"
    if "guild" in filename.lower() or "changes" in filename.lower():
        return "agent"
    return "bot"


@router.get("/logs")
def get_logs(
    level: str | None = Query(None),
    keyword: str | None = Query(None),
    module: str | None = Query(None),
    category: str | None = Query(None),
    since: str | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    user: dict = Depends(get_current_user),
):
    """Read all log files, parse, filter, return newest-first."""
    all_entries: list[dict] = []

    # Standard Python log files
    for name in [
        "qa_boot_attempt.stderr.log",
        "qa_boot_attempt.stdout.log",
        "qa_boot.stderr.log",
        "qa_live_run1.txt",
        "qa_live_run2.txt",
        "qa_live_run3.txt",
        "qa_live_run4.txt",
        "qa_live_run5.txt",
        "qa_harness_run.txt",
        "qa_kl4_baseline.txt",
        "qa_kl4_amp.txt",
    ]:
        p = LOGS_DIR / name
        if p.exists():
            all_entries.extend(_read_text_log(p, limit=200))

    # JSONL files
    for name in [
        "moderation_actions.jsonl",
        "moderation_monitor.jsonl",
    ]:
        p = LOGS_DIR / name
        if p.exists():
            all_entries.extend(_read_jsonl_log(p, limit=500))

    # Subdirectory JSONL files
    repair_errors = LOGS_DIR / "repair" / "errors.jsonl"
    if repair_errors.exists():
        all_entries.extend(_read_jsonl_log(repair_errors, limit=200))

    changes_dir = LOGS_DIR / "changes"
    if changes_dir.is_dir():
        for f in changes_dir.glob("*.jsonl"):
            all_entries.extend(_read_jsonl_log(f, limit=200))

    # Filter by level
    if level and level.upper() != "ALL":
        all_entries = [e for e in all_entries if e.get("level", "").upper() == level.upper()]

    # Filter by keyword
    if keyword:
        kw = keyword.lower()
        all_entries = [e for e in all_entries if kw in e.get("message", "").lower() or kw in e.get("module", "").lower()]

    # Filter by module
    if module and module.lower() != "all":
        all_entries = [e for e in all_entries if e.get("module", "").lower() == module.lower()]

    # Filter by category
    if category and category.lower() != "all":
        all_entries = [e for e in all_entries if e.get("category", "").lower() == category.lower()]

    # Filter by since timestamp (ISO format or epoch string)
    if since:
        try:
            from datetime import datetime
            # Try parsing as ISO format
            since_dt = datetime.fromisoformat(since.replace('Z', '+00:00'))
            # Log timestamps are local wall-clock strings. If `since` is
            # tz-aware (e.g. a UTC 'Z' value), convert it to local time before
            # formatting, otherwise the comparison is off by the UTC offset.
            if since_dt.tzinfo is not None:
                since_dt = since_dt.astimezone().replace(tzinfo=None)
            since_ts = since_dt.strftime("%Y-%m-%d %H:%M:%S")
            all_entries = [e for e in all_entries if e.get("timestamp", "") >= since_ts]
        except (ValueError, TypeError):
            # Try parsing as epoch timestamp
            try:
                since_epoch = float(since)
                since_ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since_epoch))
                all_entries = [e for e in all_entries if e.get("timestamp", "") >= since_ts]
            except (ValueError, TypeError):
                pass

    # Sort newest first
    all_entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    total = len(all_entries)
    all_entries = all_entries[:limit]

    levels = {}
    modules = {}
    for e in all_entries:
        lv = e.get("level", "UNKNOWN")
        levels[lv] = levels.get(lv, 0) + 1
        mod = e.get("module", "unknown")
        modules[mod] = modules.get(mod, 0) + 1

    return {
        "total": total,
        "logs": all_entries,
        "stats": {
            "levels": levels,
            "modules": modules,
            "error_rate": round(levels.get("ERROR", 0) / max(total, 1) * 100, 1),
        },
    }
