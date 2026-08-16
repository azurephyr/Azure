# RC1 Runtime Checklist (single real Discord server)

**Purpose:** The minimum operational checklist for running Azure v1.0.0-rc1 on a real Discord server.  
**Use once per server bring-up, then daily.**  
**Do not edit the bot during this checklist.**

---

## A. One-time bring-up

### A.1 Repository

- [ ] `git rev-parse HEAD` returns `19412a9`.
- [ ] `git status` says `nothing to commit, working tree clean`.
- [ ] `git ls-files tests/certification/` lists 6 files (1 orchestrator + 5 harnesses).

### A.2 Python

- [ ] Python 3.11+ (required for `datetime.UTC`; see `RC1_CHANGELOG.md` FIX-8).
- [ ] `python -m pip install -r requirements.txt -r requirements-web.txt` exit 0.

### A.3 Configuration

- [ ] `.env` present (copied / generated from `.env.example`).
- [ ] `AZURE_DISCORD_TOKEN` set.
- [ ] Exactly one of (a) `AZURE_MODEL_PATH` + `AZURE_LLM_BACKEND=ctransformers`, or (b) `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` is set.
- [ ] If web dashboard is exposed: `AZURE_WEB_SECRET` set to ≥ 32 random bytes (per `web/api_auth.py` and `RC1_CHANGELOG.md` FIX-22).
- [ ] `AZURE_ADMIN_PASSWORD_HASH` (recommended) or `AZURE_ADMIN_PASSWORD` (with warning).
- [ ] `AZURE_WEB_ALLOWED_ORIGINS` set to the actual dashboard domain if dashboard is enabled.
- [ ] `AZURE_MODERATION_PHASE=dry_run` for first deployment.
- [ ] `AZURE_CHAT_MODE=owner_only` for first deployment.

### A.4 Bot code

- [ ] `python -m py_compile run_bot.py bot/discord_bot_v1.py azure/agent.py` exit 0.
- [ ] `python tests/certification/run_all.py` exits 0.

### A.5 Launch

- [ ] Start: `python run_bot.py`.
- [ ] Wait for "Ready" / login confirmation.
- [ ] Confirm `validate_auth_config()` returned OK in startup log (if dashboard enabled).

---

## B. First-day smoke (≤ 60 minutes)

Tick each, or **stop and file a bug using `BUG_REPORT_TEMPLATE.md`** if FAIL.

- [ ] Pings back on `!ping`.
- [ ] Mentions respond with `**Thinking...**` then a reply.
- [ ] Two distinct users get distinct profiles (memory does not bleed).
- [ ] `!status` returns expected fields.
- [ ] Web dashboard reachable at the configured URL with valid JWT.

---

## C. Snapshot permissions on first day (recommended for ops)

- [ ] Audit log inspected: `audit_logs` table, or `GET /api/moderation/logs?limit=20`.
- [ ] No `discord.errors.Forbidden` lines at startup.
- [ ] Admin channel (if `AZURE_ADMIN_CHANNEL_ID` is set) receives a startup ping (it is OK if no admin channel is set; per FIX-A12 critical alerts still go to the bot owner).

---

## D. Daily operational checklist

- [ ] Bot process is running.
- [ ] Health endpoint responds: `GET /health` (port from `Dockerfile` / `docker-compose.yml`: 8088 internal).
- [ ] No SQL warning `cannot start a transaction within a transaction` in newest `logs/*.log`.
- [ ] SQLite database size weekly snapshot (sanity; not a hard cap).
- [ ] `tail` of latest log shows graceful, ordered shutdown (whenever the bot was last restarted). The shutdown order is documented in `RC1_TESTING_GUIDE.md` §2 R-3.

---

## E. Per-change (every time `.env` or code changes)

- [ ] `git diff` clean and explained in commit message.
- [ ] `python tests/certification/run_all.py` exits 0.
- [ ] Restart bot; first ping response in < 5 s.

---

## F. Stop conditions (any of these ⇒ roll back)

- [ ] Telemetry progress message permanently stuck on one stage for > 60 s (related to `azure/telemetry.py` `MIN_DISPLAY_TIME_MS=800`).
- [ ] `OperationalError: cannot start a transaction within a transaction` repeats.
- [ ] Destructive action (kick/ban/delete/timeout) executes without recording `moderation:confirm` audit row.
- [ ] Web dashboard returns 200 to requests without an Authorization header.

---

End of checklist.
