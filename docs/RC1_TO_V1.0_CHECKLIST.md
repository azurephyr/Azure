# v1.0.0 Promotion Checklist (RC1 → GA)

**Purpose:** Gate criteria that *must* be satisfied before the `-rc1` label is removed and the codebase is tagged `v1.0.0`.  
**Source of truth:** This file plus `docs/RC1_TESTING_GUIDE.md`, `docs/RC1_RUNTIME_CHECKLIST.md`, `docs/RC1_VALIDATION_PLAN.md`, `docs/RC1_KNOWN_LIMITATIONS.md`, `RC1_CHANGELOG.md`.  
**Ownership:** Release manager.

---

## 0. Invariants (must hold throughout)

- HEAD is on a commit whose ancestry includes `19412a9`. No rebases that erase history.
- `tests/certification/run_all.py` exits **0** every day of validation.
- No new production code introduced without a passing regression test in `tests/certification/`.
- Database schema unchanged from `19412a9` (no migrations required).
- `run_bot.py` entry point unchanged.
- Public env-var prefixes remain `AZURE_*`.

---

## 1. Required sign-offs (must all be complete)

- [ ] All of `docs/RC1_TESTING_GUIDE.md` §2 R-1 … R-5: **PASS**.
- [ ] All of `docs/RC1_TESTING_GUIDE.md` §3 W-1 … W-4: **PASS**.
- [ ] All of `docs/RC1_TESTING_GUIDE.md` §4 D-1 … D-3: **PASS**.
- [ ] All of `docs/RC1_TESTING_GUIDE.md` §5 E-1 … E-5: **PASS**.
- [ ] `python tests/certification/run_all.py` exit 0 on the candidate HEAD.
- [ ] `git rev-parse HEAD` is recorded in the release manager's daily note.
- [ ] No open bug reports filed under `BUG_REPORT_TEMPLATE.md` that are unclassified.
- [ ] All `Blocker`-severity bugs closed.
- [ ] Two independent operators have run §1 of `docs/RC1_RUNTIME_CHECKLIST.md` and have signed the daily note.

---

## 2. Documentation must be current

- [ ] `RC1_CHANGELOG.md` reflects the actual SHA.
- [ ] `README.md` mentions RC1 status (does not promise pre-RC1 features).
- [ ] `docs/MODEL_SETUP.md` mentions the cloud-API recommendation.
- [ ] `docs/CONFIGURATION.md` matches current env-var names (`AZURE_*`).
- [ ] `docs/CONFIGURATION.md` mentions `AZURE_WEB_DASHBOARD=1` requirement for the secret (per FIX-22).
- [ ] `docs/TROUBLESHOOTING.md` updated with any production-real symptom from RC1 week.
- [ ] `CHANGELOG.md` (cumulative) updated to bump heading to `[1.0.0] - <date>`.

---

## 3. Knowledge guard-rails in place

- [ ] All KL-1 … KL-20 from `docs/RC1_KNOWN_LIMITATIONS.md` either resolved, or moved to `docs/v1.0.0_release_status.md` "Future Enhancements (v1.1+)".
- [ ] Each item listed under "Known Limitations" in `CHANGELOG.md` matches a row in `docs/RC1_KNOWN_LIMITATIONS.md` §2.
- [ ] The css-mode of `git ls-files tests/certification/` lists 6 files (orchestrator + 5 harnesses), confirmed `[ ]`.

---

## 4. Tag-and-release sequence (release manager runs in order)

1. Bump `CHANGELOG.md` heading from `[1.0.0-rc1]` to `[1.0.0]`.
2. Bump `__version__` if present in `azure/__init__.py` (or document the version commit).
3. Commit docs only:

   ```
   git add CHANGELOG.md docs/
   git commit -m "Release v1.0.0: <one-line summary>"
   ```

4. Tag the commit:

   ```
   git tag -a v1.0.0 -m "Azure v1.0.0 GA"
   ```

5. Push branch and tag.
6. Open the GitHub release notes from `RC1_CHANGELOG.md`, drop `rc1` sections, keep all resolved items.
7. Update `docs/v1.0.0_release_status.md` to `Status: RELEASED`.

---

## 5. What v1.0 does **not** ship (carried to v1.1)

These remain explicitly out of scope and remain in `docs/RC1_KNOWN_LIMITATIONS.md` §2:

- H-01 Compose alignment.
- H-02 WebSocket broadcast privacy.
- H-06 Plugin system.
- H-07 GDPR / privacy.
- H-08 Input filter completeness.
- H-09 AGRE sandboxing.
- H-10 Per-guild queue.
- H-11 Configuration documentation drift (must resolve before promotion — see §2 above).

---

## 6. Rollback criteria (if discovered post-tag)

- Any `blocker` bug surfaced within 48 hours ⇒ re-tag as `v1.0.1-rc1`, reopen RC1 process.
- Any unauthorized code change detected in v1.0 commit ⇒ revert, hotfix via `RC1_KNOWN_LIMITATIONS.md` §6 protocol.

---

End of checklist.
