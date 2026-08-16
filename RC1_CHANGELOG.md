# RC1 Changelog

All notable changes to Azure v1.0 since the previous release are
documented in this file. This is the **Release Candidate 1** preview.
Real-world testing is required before promoting to v1.0 GA.

## [1.0.0-rc1] — 2026-07-11

### Static-remediation cycle (pre-certification)

This release candidate contains the cumulative fixes from three
review rounds prior to the certification pass. Each fix was
reproduced, root-caused, fixed with the smallest production-quality
change, regression-tested, and re-run.

#### Production-correctness fixes (High)

- **FIX-1 — `azure/input_validator.py`, `bot/handlers/message_handler.py`**: prompt-injection gate was `is_safe` (returns True for SUSPICIOUS). Added `is_blocked` predicate. Rewired `bot/handlers/message_handler.py:893` to gate on `is_blocked` so SUSPICIOUS inputs with detected violations are blocked at the chat-message boundary. **Security-relevant.**
- **FIX-2 — `azure/telemetry.py`**: callbacks were silently dropped for non-`SIGNIFICANT_EVENTS` actions, breaking live-telemetry subscribers. Decoupled dispatch/broadcast from the presentation filter; `scratch/test_control_center.py` rewritten to assert the right split. **UX-relevant.**
- **FIX-3 — `bot/handlers/message_handler.py`**: `MGMT_TOOLS` `NameError` in `_handle_info_request`. Added import.
- **FIX-5 — `azure/auto_moderation.py`**: `discord.errors.*` referenced without `import discord`. Guarded try-import with fallback `discord = None`.
- **FIX-10 — `azure/cognition/audit_engine.py`, `azure/cognition/thinking_visualizer.py`**: `Path` / `time` undefined (F821). Imports added.
- **FIX-11 — `azure/llm_planner.py`**: `failures` referenced before assignment on retry branch. Initialized `failures = []` before the loop.
- **FIX-12 — `azure/tools/server_tools.py`**: `io` undefined. Import added.

#### Stability / static-bug fixes (Medium)

- **FIX-7 — `azure/cognition/adversarial_review_engine.py`**: dead `lower` / `msg_lower` / `safer_parts` locals. Removed unused; inlined `lower()` calls.
- **FIX-8 — `azure/moderation/actions.py` (+ 4 more sites)**: `datetime.utcnow()` and `datetime.timezone.utc` deprecated. Switched to `datetime.UTC` (Python 3.11+). Aware datetimes satisfy discord.py 2.0+ requirement.
- **FIX-9 — `azure/agent.py`**: dead `_cognitive_pipeline` branch. Removed.
- **FIX-13 — `azure/cognition/mode_classifier.py`**: duplicate set literal in `CHAT_TRAILERS`. Removed.
- **FIX-15 — `azure/api_llm.py`**: API error-body echoed into `RuntimeError` (PII-risk). Logs status only; raises with HTTP code.
- **FIX-21 — `web/api_auth.py`**: plaintext password comparison was `==`. Switched to `hmac.compare_digest`.
- **FIX-22 — `web/api_auth.py`**: Random `SECRET_KEY` fallback used silently. Raises `RuntimeError` if `AZURE_WEB_DASHBOARD=1` without `AZURE_WEB_SECRET`.

#### Code hygiene / static-bug fixes (Low)

- **FIX-A1 — `azure/auto_moderation.py`**: `discord.errors.*` `except` clauses changed to `except Exception:` after adding the import guard.
- **FIX-3a — `azure/agent.py`**: dead `_cognitive_pipeline` branch unreachable; removed.
- **FIX-A4 — `azure/cognition/intent_decomposer.py`**: duplicate `"excited"` dict key. Removed.
- **FIX-A5 — `azure/cognition/tool_tier_dispatcher.py`**: duplicate `"get_server_state": ToolTier.READ` key. Removed.
- **FIX-A6 — `azure/recovery/classifier.py`**: duplicate `"KeyError"` key mapped to two different `FailureType` values; renamed second entry to `"ConfigKeyError"` to surface the design.
- **FIX-A7 — `azure/cognition/cognitive_pipeline.py`**: `IntentDecomposer` redundantly re-imported locally. Removed.
- **FIX-A8 — `azure/local_llm.py`, `azure/memory_backend.py`, `web/api_auth.py`**: `raise X` inside `except` lost exception chain (7 sites). All converted to `raise X from err`.
- **FIX-A9 — `azure/moderation/scanner.py`**: `B009 getattr(message, "created_at").timestamp()` redundant. Inlined.
- **FIX-A10 — `azure/moderation/engine.py`**: unused `ingested` binding in `periodic_scan`. Removed.
- **FIX-A11 — `azure/streaming.py`**: two sites of unused `streamer = ResponseStreamer()`. Renamed to `_streamer`.
- **FIX-A12 — `azure/audit.py`**: critical-security DM was nested INSIDE `if channel: get_channel()` so all critical alerts (jailbreak attempts etc.) silently dropped when `AZURE_ADMIN_CHANNEL_ID` was unset. Restructured so critical-DM always fires when bot is reachable, independent of admin channel configuration. **Critical bug fixed.**
- **FIX-16 — `bot/discord_bot_v1.py`**: misleading "needs async fix" plugin log replaced with honest docstring on intentional deferral.
- **FIX-A2 — `bot/handlers/moderation_handler.py`**: `!mod_channel` was binding local `ADMIN_CHANNEL=False` and never propagating to `discord_bot_v1.ADMIN_CHANNEL`. Rewritten via `discord_bot_v1.ADMIN_CHANNEL = channel`.
- **FIX-24 — `.gitignore`**: extended to `.db-shm/.db-wal` and trailing test/sim sqlite files.
- **FIX-6 — 9 sites across 5 files**: `except:` → `except Exception:` (E722); preserves Ctrl+C semantics.

### Concurrency reliability fix (RC1-critical)

- **KL-4 — `azure/database.py`, `azure/audit.py`, `web/api_moderation.py`**: `DatabaseManager` shared a single SQLite connection across threads with `check_same_thread=False` but no serialization. Reproduced concurrent-writer losses of up to **91.6%** in telemetry-storm (3,200 events → 270 written) and `OperationalError: cannot start a transaction within a transaction` on Windows (surfacing as `SystemError: error return without exception set`). Fix:
  - `azure/database.py`: added `self._wlock = threading.Lock()` and a `_locked_conn()` context manager that yields the shared connection under the lock.
  - Wrapped all 9 mutator methods (`save_conversation`, `save_user_preference`, `save_cache_entry`, `cleanup_expired_cache`, `save_stats`, `vacuum`, `set_access_control`, `log_security_event`, `log_telemetry`) in `with self._locked_conn() as conn:` blocks.
  - Reads (`get_*`) intentionally left lock-free; SQLite single-connection concurrent reads are safe.
  - External raw-conn writers (`audit.py:40-52,93-98` and `web/api_moderation.py:17-20`) now acquire `self.db._wlock` before using the connection.
  - Totals: 198-line diff, +198/−147, no architectural change.

  **Reproduction harness**: `scratch/kl4_repro.py` (regression-enforcing, kept).
  **Post-fix evidence**: `VERDICT: KL-4 did NOT reproduce. All writers succeeded; counts match expectations.` — 4 threads × 200 telemetry writes = 800/800; 32 concurrent audit spikes = 32/32.

### Test infrastructure (permanent)

- `tests/certification/test_rc1_certification.py` (29 PASS / 1 KNOWN_LIMITATION / 0 FAIL after 30 checks) — behavior harness.
- `tests/certification/test_rc1_subsystems.py` (7 PASS / 4 KNOWN_LIMITATION / 0 FAIL) — life-cycle, edge case, classification.
- `tests/certification/test_rc1_stress.py` (7 PASS / 0 KNOWN_LIMITATION / 0 FAIL) — real-GGUF round-trip, validator throughput, RAG corpus precision.
- `tests/certification/test_rc1_module_coverage.py` (156 PASS / 1 KNOWN_LIMITATION / 0 FAIL) — every production `.py` imports cleanly.
- `tests/certification/test_rc1_adversarial.py` — 100+ adversarial sample suite, classifier breakdown (passing classifications are documented; see `RC1_KNOWN_LIMITATIONS.md`).
- `tests/certification/run_all.py` — orchestrator that runs all four harnesses; **exit 0 means no FAIL across all suites**.

### Verified at certification

- Local GGUF model (`models/Qwen2.5-7B-Instruct-Q4_K_M.gguf`, 4.7 GB) loads via `LocalLLM(...)` and returns the prompted answer `rc1-ok` end-to-end. Sample: `[INTEGRATION] PASS — LocalLLM OK: load 1.3 s, chat 4.61 s, len=6, sample='rc1-ok'`.
- SubprocessLLM stdio contract verified by spawning `azure/llm_worker.py`, sending a JSON request line, receiving a JSON response line. Sample: `[INTEGRATION-PROC] PASS — end-to-end round-trip OK in 5.83 s`.
- 2 concurrent SubprocessLLM clients completed independently in 23.2 s.
- All 4 harnesses + module coverage pass; 199 PASS / 0 FAIL / 6 KNOWN_LIMITATION across `tests/certification/run_all.py`.

### Unchanged public behavior

- HTTP API surface area, 49 prefix commands, 5 periodic task loops, intent configuration unchanged.
- `run_bot.py` entry point unchanged.
- Database schema unchanged; `data/*.db-shm`, `data/*.db-wal` covered by `.gitignore`.

### Release confidence

- **Confidence**: 90%.
- **Verdict**: **READY FOR RC1 ONLY**.
- Promotion to v1.0 GA requires manual runtime tests listed in `RC1_TESTING_GUIDE.md` plus resolution of the KNOWN_LIMITATIONS in `RC1_KNOWN_LIMITATIONS.md`.

[1.0.0-rc1]: https://github.com/your-org/azure/releases/tag/v1.0.0-rc1
