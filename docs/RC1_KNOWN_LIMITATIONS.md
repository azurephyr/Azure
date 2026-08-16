# RC1 Known Limitations

**Audience:** Anyone validating Azure v1.0.0-rc1 (`HEAD 19412a9`).  
**Purpose:** Document what is *known* to be incomplete, *tested* to be incomplete, or *deferred* from v1.0.  
**Source of truth:** Each entry below points to a specific prior report, code site, or test result. Do not invent; do not speculate; do not generalize beyond cited evidence.

---

## 1. Items that block v1.0 GA promotion

These are limitations that the permanent regression harness (`tests/certification/run_all.py`) cannot pass with `KNOWN_LIMITATION`; they need a real Discord server / real LLM / real network to confirm.

### KL-12 — Manual runtime tests not yet executed
- **Status:** Open. Pending execution of `docs/RC1_TESTING_GUIDE.md` §2 R-1 … R-5, §3 W-1 … W-4, §4 D-1 … D-3, §5 E-1 … E-5.
- **Why:** `docs/RC1_VERIFICATION_REPORT.md` Phase 3 / Phase 4 is marked "PENDING / ⏳ NEXT" at the time of `RC1_CHANGELOG.md`.
- **Resolution:** A signed `RC1_TESTING_GUIDE.md` run sheet. No code change implied.

### KL-13 — H-11 Configuration documentation drift
- **Status:** Open. Quick docs fix.
- **Why:** `docs/v1.0.0_release_status.md` row H-11 says runtime uses `AZURE_*` prefixes; some locations in legacy docs reference pre-prefix names. `docs/v1.0.0_release_status.md` decision: "Quick docs fix before release".
- **Resolution:** Edit only `docs/*.md`; no code change.

---

## 2. Items deliberately deferred to v1.1

These were classified by `docs/v1.0.0_release_status.md` "Remaining HIGH Priority" and accepted-for-v1.0.0 there. Recorded here to make sure they're not re-discovered as bugs.

### KL-14 — H-01 Docker Compose alignment
- **Status:** PARTIALLY RESOLVED. `589ca02` fixed Dockerfile. Compose not fully reconciled with FastAPI dashboard.
- **Decision (per release status file):** Post-v1.0 cleanup.
- **Do NOT file as RC1 bug.**

### KL-15 — H-02 WebSocket privacy controls
- **Status:** PARTIALLY RESOLVED. JWT auth added by `4fd5377`. Cross-user broadcast still exposes some data.
- **Decision:** Full privacy controls in v1.1.
- **Do NOT file as RC1 bug.**

### KL-16 — H-06 Plugin API mismatch
- **Status:** KNOWN. Plugins deliberately disabled.
- **Decision:** Remove or document as experimental in v1.0.
- **Do NOT file as RC1 bug.**

### KL-17 — H-07 Privacy & GDPR
- **Status:** NOT IMPLEMENTED. No retention policies, no user-data deletion API, cross-user RAG retrieval.
- **Decision:** v1.1 feature (privacy policy required).
- **Do NOT file as RC1 bug.**

### KL-18 — H-08 Input filtering gaps
- **Status:** ACCEPTED RISK. Regex-based validation has documented limitations; not all paths validated.
- **Decision:** Monitor in production, enhance in v1.1.
- **Fix that did land (still RC1):** FIX-1 (chat-message boundary now uses `is_blocked`, not `is_safe`).

### KL-19 — H-09 AGRE recovery privileges
- **Status:** KNOWN LIMITATION. AGRE can install packages, create files; no external approval boundary.
- **Decision:** Document and sandbox in v1.1.
- **Do NOT file as RC1 bug.**

### KL-20 — H-10 Agent context race condition
- **Status:** MITIGATED. TaskManager serializes normal path; per-call context overrides reduce risk.
- **Decision:** Monitor, refactor in v1.1 if issues arise.
- **Do NOT file as RC1 bug, but monitor.**

---

## 3. Items the certification harness flagged as KNOWN_LIMITATION

`tests/certification/run_all.py` reports `KNOWN_LIMITATION` to indicate a passing assertion that the certification author documented as acceptable-for-RC1 rather than blocking. Counts as of `RC1_CHANGELOG.md` line 80:

- `test_rc1_certification.py` — **1 KNOWN_LIMITATION**.
- `test_rc1_subsystems.py` — **4 KNOWN_LIMITATION**.
- `test_rc1_stress.py` — **0 KNOWN_LIMITATION**.
- `test_rc1_module_coverage.py` — **1 KNOWN_LIMITATION**.

Total = **6 KNOWN_LIMITATION** at certification time. **No FAIL.**

The text of each individual limitation lives inside the test files. RC1 testers should re-run the harness before tagging v1.0.0 and re-record any drift.

---

## 4. Items the RC1 audit found and explicitly closed

These are *not* limitations; they were open before certification and are now resolved. Recorded here so they are not reopened by accident.

| Item | Source | Resolution commit / path |
|---|---|---|
| KL-1 … KL-3 | engineering audit | Various pre-certification fixes (see `docs/engineering_audit.md`) |
| **KL-4** SQLite shared-connection concurrency | `RC1_CHANGELOG.md` §Concurrency reliability fix | `19412a9` commit; `azure/database.py` `threading.Lock` + `_locked_conn()`; `scratch/kl4_repro.py` regression harness |
| KL-5 (FIX-2) telemetry dispatch/presenter split | `RC1_CHANGELOG.md` FIX-2 | `azure/telemetry.py`; assertion in `scratch/test_control_center.py` |
| KL-6 (FIX-A12) critical-DM dropped by `if channel:` | `RC1_CHANGELOG.md` FIX-A12 | `azure/audit.py` |

---

## 5. How to use this file

1. **Run `RC1_TESTING_GUIDE.md`.** Each PASS / FAIL becomes a row here.
2. **Run `tests/certification/run_all.py`.** Any new `KNOWN_LIMITATION` is appended with date.
3. **Anything outside this list that surfaces as FAIL** during RC1 testing is a real bug; file using `BUG_REPORT_TEMPLATE.md`.

---

End of file.
