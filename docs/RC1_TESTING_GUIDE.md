# RC1 Testing Guide

**Audience:** Anyone validating Azure v1.0.0-rc1 (`HEAD 19412a9`).  
**Scope:** Manual runtime tests required to validate RC1 in a real Discord server.  
**Out of scope:** Anything not produced by `12412a9`. Do not edit code while running this guide.

---

## 0. What this guide does

RC1 certification is **technical and engineered**. This guide covers the *operational* tests that cannot be replaced by the permanent regression harness (`tests/certification/run_all.py`). Everything in this document was implied by, or directly inherited from:

- `RC1_CHANGELOG.md` (release-time claims).
- `docs/RC1_VERIFICATION_REPORT.md` Phase 2 / 3 / 4.
- `docs/v1.0.0_release_status.md` Release Criteria ("should have" items).
- `run_bot.py` (the single entry point).

Sign each test PASS / FAIL / KNOWN_LIMITATION. A FAIL anywhere blocks v1.0 promotion; a KNOWN_LIMITATION does not.

---

## 1. Pre-flight (do this first; 30 minutes)

| # | Action | Pass criterion | Evidence |
|---|---|---|---|
| P-1 | `git log -1` shows `19412a9` | HEAD matches RC1 certification commit | `git rev-parse HEAD` |
| P-2 | `git status` clean | "nothing to commit, working tree clean" | `git status` |
| P-3 | `git ls-files tests/certification/` lists 6 files | `run_all.py` + 5 `test_rc1_*.py` | `git ls-files` |
| P-4 | `python -m py_compile azure/agent.py bot/discord_bot_v1.py run_bot.py` returns 0 | All three exit 0 | `python -m py_compile …` |
| P-5 | `python tests/certification/run_all.py` exits 0 | All 4 harness suites pass (KNOWN_LIMITATION accepted) | harness output |
| P-6 | Confirm `.env` has `AZURE_DISCORD_TOKEN` and exactly one LLM backend (cloud API key OR `AZURE_MODEL_PATH=…`) | `.env.example` §REQUIRED" matches current `.env` | `Get-Content .env` |
| P-7 | Confirm `AZURE_WEB_SECRET` and `AZURE_ADMIN_PASSWORD_HASH` (or `AZURE_ADMIN_PASSWORD`) are set | `validate_auth_config()` accepts (logged at startup) | `logs/*.log` startup lines |
| P-8 | If using local LLM, confirm `models/Qwen2.5-7B-Instruct-Q4_K_M.gguf` present | file exists, ~4.7 GB | `Get-Item models/*.gguf` |
| P-9 | If using API LLM, confirm at least one of `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` set, and the matching `AZURE_LLM_BACKEND` chosen | startup does not log "fallback to local model" | startup log |

If any P-N fails, stop. Do not run the runtime tests below.

---

## 2. Runtime behavior tests (per `docs/RC1_VERIFICATION_REPORT.md` Phase 2)

These were verified by static analysis in the certification pass. They must now be reconfirmed **on a live Discord server**.

### R-1: Telemetry presentation (PASS criterion = visible timing)

1. Start bot: `python run_bot.py`. Watch stdout until "Ready" / login confirmation.
2. In a guild channel that the bot can see, send **`hello`**.
3. Expect, on the bot's progress message:
   - `**Thinking...**`
   - `⏳ Generating response...`
   - Replace with the final reply within ~1 second of GENERATING.
4. Send **`please summarize our last 4 conversations`**.
5. Expect intermediate stages, at minimum: `Thinking` → `Recalling previous conversations` → `Generating response`, each held ≥ 800 ms.
6. Send a destructive Discord command (admin only) like `create a role called rc-validation-temp colored blue`.
7. Expect planning stage `Planning: …` and execution stage `Executing N step(s)...` to appear with ≥ 800 ms separation.

**PASS:** Each request type produces the expected stage sequence; no flicker; minimum display time respected.

### R-2: SQLite persistence (PASS criterion = restart-safe memory)

1. Start bot.
2. Send a message that is persisted (e.g. `remember my favorite color is teal`).
3. Confirm reply: `...remembered...`.
4. Stop the bot (`Ctrl+C`).
5. Re-start bot.
6. Send `what is my favorite color?`.
7. Expect the bot to reply `teal` (or equivalent confirmation derived from saved memory).

**PASS:** Memory survives restart. If FAIL, file a bug referencing `azure/memory_backend.py`.

### R-3: Graceful shutdown (PASS criterion = no zombie workers, no orphans)

1. Start bot (local LLM backend recommended so worker processes exist).
2. Send enough messages to enqueue LLM work (≥ 20 in quick succession).
3. Send **SIGTERM** (`Stop-Process -Id <pid> -Force` or `taskkill /PID …`).
4. Watch shutdown log. The shutdown order in `azure/audit.py` / `run_bot.py` must, in order:
   1. LLM workers terminated.
   2. Discord client closed.
   3. Health server stopped.
   4. Plugin system shut down.
   5. Moderation data flushed.
   6. SQLite committed and closed.
   7. Cron scheduler stopped.
   8. Voice connections cleaned up.
5. Confirm no `python llm_worker.py` or `python run_bot.py` child processes remain (`Get-Process python`).

**PASS:** Order matches; no zombies.

### R-4: AGRE recovery (PASS criterion = one transient failure recovers)

1. Start bot with local LLM backend.
2. Briefly interrupt the LLM backend (e.g. lock the `.gguf` file from `models/`).
3. Send a message that triggers LLM call.
4. Expect the bot to keep responding with an explanatory fallback OR temporary failure, then recover when the lock is removed (within `max_recovery_attempts_per_retry=5`).
5. Confirm audit log has a `recovery_attempt` event.

**PASS:** Bot recovers without manual restart; logs include `recovery_attempt`.

### R-5: KL-4 concurrency (PASS criterion = no silent drops)

1. Start bot.
2. With the bot connected, run from another terminal:
   ```
   python scratch/kl4_repro.py
   ```
3. Expect lines of the form: `800/800 telemetry writes`, `32/32 audit spikes`.
4. Repeat 3 times. Expect identical numbers within ±1.

**PASS:** No `cannot start a transaction within a transaction` warnings, no dropped rows. If FAIL, file a bug pinned to `azure/database.py` wlock.

---

## 3. Web dashboard tests (per docs H-03 and C-03)

### W-1: Authentication with bcrypt hash

1. Set `AZURE_ADMIN_PASSWORD_HASH=$(python scripts/generate_credentials.py)` (or generate manually) and restart.
2. `POST /api/auth/token` (form: `username`, `password`).
3. Expect HTTP 200 and JSON `{access_token, ...}`.

### W-2: Read endpoint allowed for authenticated user

1. With the token, `GET /api/moderation/logs?limit=50`.
2. Expect HTTP 200, JSON list.

### W-3: Mutating endpoint requires admin

1. With the token, `POST /api/config/emergency_stop` with JSON `{ "value": true }`.
2. Expect HTTP 200 if role is `admin` / `owner`, else HTTP 403.

### W-4: WebSocket auth

1. Open `ws://host/ws?token=<jwt>` with a valid JWT.
2. Expect connection accepted; live events stream.
3. Open the same URL with no token or an invalid token.
4. Expect immediate close with code 1008 / 1011; logged `[ws] auth failed`.

**PASS:** All four sub-tests pass.

---

## 4. Discord authorization gates (per C-01)

### D-1: Non-admin cannot run destructive plan

1. As a non-admin guild member, send `delete the #general channel`.
2. Expect explicit refusal with reason citing `requester_id not admin` or equivalent (bot replies in chat, no Discord action performed).

### D-2: Admin must confirm destructive plan

1. As admin, send `kick @some-user`.
2. Expect bot reply listing destructive actions and asking `CONFIRM?`.
3. Reply `CANCEL`.
4. Expect bot: `cancelled`, audit log shows `moderation:cancel`.

### D-3: Admin confirms destructive plan

1. As admin, send `timeout @some-user 5 minutes`.
2. Expect confirmation request.
3. Reply `CONFIRM` within 60 s.
4. Expect timeout applied; audit log shows `moderation:confirm` + `moderation:execute`.

**PASS:** D-1, D-2, D-3 all pass.

---

## 5. Edge cases (per `docs/RC1_VERIFICATION_REPORT.md` Phase 4)

| # | Test | PASS criterion | Notes |
|---|---|---|---|
| E-1 | Two guilds, two simultaneous users, separate contexts | No cross-guild behavior / no shared memory bleed | Watch audit logs for `guild_id` |
| E-2 | Discord rate-limit near `5 edits / 5s` from progress messages | Bot does not 429-storm; continues after cooldown | Inspect network log |
| E-3 | Cache expiry: same query 2× within `AZURE_CACHE_TTL` | Second query returns `cached=true` in metadata | Watch audit log |
| E-4 | DB pool: harness `scratch/kl4_repro.py` 5× consecutively | Always 800/800; no `OperationalError` | Same harness |
| E-5 | LLM fallback: API key removed mid-session | Bot logs `fallback to local model failed` and stops / degrades cleanly, not crashes | Manual: revoke env var and reload |

---

## 6. Records to keep

After each section passes, capture:
- timestamp
- `git rev-parse HEAD`
- `python tests/certification/run_all.py` excerpt (first/last 20 lines)
- `tail -200 logs/rc1_run_<timestamp>.log`

These records are inputs to `docs/v1.0.0_release_status.md` "promote to v1.0" gate.

---

## 7. What this guide does NOT cover

- v1.1 features (H-01…H-10 from `docs/v1.0.0_release_status.md`). Those are deferred.
- GDPR / retention (H-07). Defer to v1.1.
- Plugin system (H-06). Deliberately disabled.
- Performance tuning beyond the convergence of telemetry timing.

---

End of guide.
