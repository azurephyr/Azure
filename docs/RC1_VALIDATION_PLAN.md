# RC1 Production Validation Plan

**Goal:** Validate Azure v1.0.0-rc1 in production over the next 2–5 days without modifying production code unless a real RC1-runtime bug is reproduced.  
**Inputs:** `docs/RC1_TESTING_GUIDE.md`, `docs/RC1_RUNTIME_CHECKLIST.md`, `tests/certification/`, `BUG_REPORT_TEMPLATE.md`.  
**Out of scope:** v1.1 work; redesign; performance tuning beyond tolerable thresholds.

---

## 0. Constraints

- HEAD must remain `19412a9`. **No commits to code** during the plan. Documentation commits are fine.
- Every test produces a record:
  - timestamp;
  - HEAD SHA;
  - pass / fail / known-limitation;
  - the log excerpt (≤ 200 lines) or test output.

If a real, reproducible bug is found, see **§6**.

---

## 1. Day 1 — Bring-up + harness

1. Verify `git rev-parse HEAD` is `19412a9`. If not, **stop**.
2. Re-read `docs/RC1_TESTING_GUIDE.md` §0 / §1. Run all 9 pre-flight checks (P-1 … P-9) and capture in a daily log.
3. Run the permanent regression harness:
   ```
   python tests/certification/run_all.py
   ```
   Goal: `OVERALL: 4 suites in N s. ALL 4 SUITES PASSED (exit 0; KNOWN_LIMITATION accepted)`.
4. Start the bot.
5. Execute the §B first-day smoke checklist from `docs/RC1_RUNTIME_CHECKLIST.md` against a real server.

**Day-1 exit criterion:** harness green; B fully ticked; no `Stop conditions` from `docs/RC1_RUNTIME_CHECKLIST.md` §F.

---

## 2. Day 2 — Functional tests

Execute `docs/RC1_TESTING_GUIDE.md` §2 (R-1 … R-5):

- R-1: Telemetry presentation — `**Thinking...**` → stage sequence → response.
- R-2: SQLite persistence — restart-safe memory.
- R-3: Graceful shutdown — no zombies; ordered teardown.
- R-4: AGRE recovery.
- R-5: KL-4 concurrency — `python scratch/kl4_repro.py`.

**Day-2 exit criterion:** all five PASS; output captured.

---

## 3. Day 3 — Auth + Web

Execute `docs/RC1_TESTING_GUIDE.md` §3 (W-1 … W-4) and §4 (D-1 … D-3).

Particular care:
- W-1: Generate bcrypt hash from `python scripts/generate_credentials.py` if not already done.
- W-4: WebSocket auth must close with no token.

**Day-3 exit criterion:** all W-* and D-* PASS.

---

## 4. Day 4 — Edge cases + soak

Execute `docs/RC1_TESTING_GUIDE.md` §5 (E-1 … E-5).

Then leave the bot running unattended and let it accumulate ≥ 6 hours of natural traffic (or replay a 6-hour scripted trace if you do not have volume).

**Day-4 exit criterion:** five E-* pass; soak shows:
- no growth in log file > 50 MB/day;
- no `cannot start a transaction within a transaction`;
- audit log monotonic.

---

## 5. Day 5 — Decision

Two outcomes, mutually exclusive:

### 5.A Promote to v1.0 (only if every one of the following is true)

- [ ] Day 1 … Day 4 done.
- [ ] `tests/certification/run_all.py` exits 0.
- [ ] No FAIL anywhere in `docs/RC1_TESTING_GUIDE.md` §2, §3, §4, §5.
- [ ] No unaddressed issue in `docs/RC1_KNOWN_LIMITATIONS.md` §1.

If all of (a-d) are true, proceed per `docs/RC1_TO_V1.0_CHECKLIST.md`.

### 5.B Hold at RC1

If any of the above fails, do **not** promote. File every failure using `BUG_REPORT_TEMPLATE.md`. Decide per §6.

---

## 6. Real bug discovery protocol

If a runtime failure is reproduced in this plan, follow this exact six-step protocol before any code change:

1. **Reproduce at least once** in a controlled setting.
2. **Capture the excerpt** (logs, stack, audit log row).
3. **Open a bug report** using `BUG_REPORT_TEMPLATE.md`. Do not move on until the report has all six fields filled.
4. **Triage** with the release manager: confirm this is *not* a known-limitation entry in `docs/RC1_KNOWN_LIMITATIONS.md` §1 or §2.
5. **Decide** one of:
   1. Code fix → open a `dev/rc1-hotfix-*` branch; patch; add a regression test under `tests/certification/`; re-run the harness.
   2. Documentation update only.
   3. Promote to v1.1 deferral (add to `docs/RC1_KNOWN_LIMITATIONS.md` §3).
6. Re-run `docs/RC1_TESTING_GUIDE.md` section that triggered the failure.

No hotfix may weaken authorization, persistence, or telemetry. See `docs/RC1_KNOWN_LIMITATIONS.md` §4 for the closed-but-monitored items.

---

## 7. Reporting cadence

- End of each day: append a short note to `docs/RC1_VERIFICATION_REPORT.md` (this is allowed; it is documentation, not production code).
- Each PASS / FAIL is signed in the daily note.
- At end of Day 5, the release manager signs the top of `docs/RC1_VERIFICATION_REPORT.md` to indicate RC1 verdict: HOLD or PROMOTE.

---

## 8. What this plan deliberately does not do

- No code refactors.
- No new features.
- No performance engineering beyond verifying the exit criteria above.
- No GDPR / privacy work (deferred to v1.1; `docs/RC1_KNOWN_LIMITATIONS.md` §2 item H-07).

---

End of plan.
