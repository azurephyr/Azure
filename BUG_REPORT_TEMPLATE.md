# Bug Report — Azure v1.0.0-rc1

> Use this template for **every** bug discovered after the RC1 label has been applied.
> Fill in *all* six sections. Empty reports will be returned without triage.
> Reference this report by file name and SHA-1 (`git hash-object BUG_REPORT_<id>.md`) once filed.

---

## 0. Metadata

| Field | Value |
|---|---|
| **Bug ID** | `BUG-RC1-NNN` (assigned at triage) |
| **Filed by** |  |
| **Filing date** | YYYY-MM-DD |
| **Git HEAD at time of report** | `git rev-parse HEAD` — must be on `19412a9` or a documented hotfix |
| **Branch / environment** | (e.g. `dev/rc1-hotfix-foo` in staging) |
| **Severity** | `blocker` / `critical` / `high` / `medium` / `low` |
| **Affected area** | (e.g. `azure/database.py`, `bot/handlers/message_handler.py`, web dashboard, moderation) |
| **Tags** | (e.g. `kl-4`, `auth`, `telemetry`, `recovery`) |

---

## 1. Reproduction steps

> Numbered, exact, reproducible by another operator.

1.
2.
3.
4.

### Environment

| Field | Value |
|---|---|
| OS |  |
| Python |  |
| LLM backend (local/cloud) |  |
| Discord client version |  |
| Bot uptime at reproduction |  |
| Single user / multi-user state |  |
| Approx message volume / minute |  |

### Configuration

> Paste any relevant `.env` keys (de-tokenized). If secrets, mask values:

```
AZURE_DISCORD_TOKEN=***REDACTED***
AZURE_LLM_BACKEND=
AZURE_WEB_DASHBOARD=
AZURE_MODERATION_PHASE=
```

---

## 2. Expected behavior

> What should have happened, citing the relevant contract:
> - the relevant doc (`docs/RC1_TESTING_GUIDE.md` §X),
> - or the relevant function in the codebase,
> - or the API surface.

---

## 3. Actual behavior

> What actually did happen. Include the exact user-visible symptom.

---

## 4. Logs

> Attach the smallest log excerpt that fully shows the failure.
> Include line numbers, file path, timestamp(s). For runtime errors, include the full traceback.

```
[file:line] timestamp   message
```

```
<full stack trace here, if any>
```

Audit log row if relevant:

```
sqlite> SELECT * FROM audit_logs WHERE id = X;
```

Web dashboard relevant endpoint response:

```
$ curl -i …
```

For ≥ 200 lines of logs, attach as a separate file and link it here.

---

## 5. Root cause

> Leave this empty if you do not know. Do **not** speculate. State explicitly:
> - "Unknown — needs investigation." (acceptable)
> - "Confirmed: <file:line> — <explanation>." (required before any code fix)

Cross-reference:

- Similar closed issue: (e.g. `KL-4` closed in commit `19412a9` — `azure/database.py` wlock).
- Doc reference: (e.g. `docs/RC1_KNOWN_LIMITATIONS.md` §1).
- Test reference: (e.g. `tests/certification/run_all.py` suite name).

---

## 6. Regression test required

> **Every bug fix requires a regression test that fails before the fix and passes after.**
> Define the test here; review with the release manager.

- **Suite:** (one of `tests/certification/test_rc1_*.py`, or a new `test_rc1_<id>_regression_<bug>.py`)
- **Test name:**
- **Assertion:**
- **Locally runnable:** `python tests/certification/run_all.py`
- **Expected before fix:** exit 1 / `FAIL`.
- **Expected after fix:** exit 0 / `PASS`.

If the bug cannot be expressed as a deterministic regression test (e.g. it requires a live Discord), the test must at minimum pin a constant that fails the assertion when violated (e.g. require `_locked_conn` to exist in `azure/database.py` via import).

---

## 7. Triage (release manager use only)

| Field | Value |
|---|---|
| Triaged by |  |
| Triage date |  |
| Classification | hotfix / doc-only / known-limitation / not-a-bug |
| Linked KL |  |
| Linked commit (if fixed) |  |

---

End of template.
