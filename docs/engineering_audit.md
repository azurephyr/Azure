# Engineering Audit — Azure AI

**Audit date:** 2026-07-10  
**Scope:** Current working tree, reviewed without changing application code or configuration. The only artifact created for this phase is this audit.  
**Validation performed:** static source/configuration review; AST parsing of 158 Python files succeeded; `pytest -q -p no:cacheprovider` discovered no tests. No Discord, LLM-provider, Docker, or live-dashboard integration was exercised.

## Executive assessment

Azure is an ambitious single-process Discord automation platform with a substantial feature surface: local/API LLMs, moderation, memory, a cognitive pipeline, Discord administration tools, recovery, and a web control plane. Its strongest foundations are explicit moderation phases, typed domain models in several areas, conservative moderation defaults, and a useful separation of Discord mixins.

It is **not release-ready**. The active worktree has a broken build/CI contract, an incomplete dashboard/plugin/integration layer, no executable test suite, and critical authorization and confirmation gaps on the primary LLM-driven Discord-management path. The system should be treated as a development prototype until the critical and high findings below are closed and verified.

## 1. Repository architecture

```text
run_bot.py
  └─ bot/discord_bot_v1.py
       ├─ AzureAgent (azure/agent.py)
       │    ├─ LLMs: local_llm.py, api_llm.py, failover_chain.py
       │    ├─ memory_backend.py, rag_engine.py, rag_enhanced.py
       │    ├─ moderation/ and ai_moderation/
       │    ├─ agentic_tools.py and DiscordManagementTools
       │    └─ recovery/ (AGRE)
       ├─ bot/handlers/ (message, command, moderation, onboarding)
       ├─ cognition/ (separate CognitivePipeline instance)
       ├─ health_server.py (threaded HTTP server)
       └─ web/server.py (FastAPI/Uvicorn task)
            ├─ REST API modules
            ├─ WebSocket manager
            └─ static SPA

Persistent state: SQLite/JSON/JSONL files under data/, logs/, and project root.
Deployment intent: Dockerfile + Docker Compose + GitHub Actions.
```

The architecture is modular by directory, but runtime ownership is largely global-state based. `bot/discord_bot_v1.py` (1,536 lines) initializes and coordinates most subsystems; `bot/handlers/message_handler.py` (1,798 lines) implements the central request path. This creates a de facto monolith despite the broad module count.

## 2. Major subsystems

| Subsystem | Current design | Assessment |
|---|---|---|
| Discord runtime | `run_bot.py` loads `.env`; `discord_bot_v1.py` builds the Discord client, initializes all services, starts loops, and handles shutdown. | Functional central path, but overloaded startup/global state. |
| Azure core / LLM | `AzureAgent` selects API, local, or hybrid LLM; failover sends progressively smaller prompts. | Multiple provider support is useful; routing and failover are only partly integrated. |
| Discord pipeline | Handler processes commands, access control, rate limits, moderation, plugin interception, input validation, cache/quick replies, then the agent. | Correctly ordered in places, but privileged tool execution is unsafe. |
| Moderation | Rule-based classifier, behavioral/temporal/risk/decision engines, phased policy, confirmation queue, action executor, reporter. | Best-contained subsystem; dry-run default and action limits are strengths. |
| Cognitive system | `azure/cognition/` implements router, reasoner, planner, executor, critic, reflection, goals, tool tiers, and output. | Feature-rich but not the normal interactive execution path. |
| Tools | Agentic web/file/Python tools plus expanded Discord tool mixins and LLM planner. | Broad capability, insufficient capability/authorization boundaries. |
| Memory / RAG | Short/long-term in-process memory, SQLite/Redis/in-memory backend, embedding RAG, hybrid SQLite RAG, JSON cognition memories. | Redundant stores with inconsistent persistence and isolation. |
| Database | `DatabaseManager` owns conversation/cache/stats/audit/access-control/telemetry/web-user schema. | Schema exists, but most observability and dashboard writes are unwired. |
| Web dashboard | FastAPI SPA, JWT login, REST APIs, WebSocket broadcast; launched from bot process. | Incomplete, insecure defaults, and deployment references a deleted app. |
| Telemetry / audit | `ExecutionTracker` broadcasts progress; `AuditSystem` can persist and notify. | Implemented components are not consistently instantiated or persisted. |
| Recovery | AGRE wraps Discord messages and plan execution; recovery executor can install packages, create files, set environment variables, and retry. | Recovery is powerful but over-privileged and blocking. |
| Plugins/integrations | Minimal registries and abstract bases. | Lifecycle and command contracts are incompatible; no production plugin discovery/load path. |

## 3. Runtime flow and request map

### Startup

1. `run_bot.py` locates and loads `.env`, then calls `bot.discord_bot_v1.main()`.
2. `setup()` requires a configured local model or cloud key, creates `AzureAgent`, moderation, management tools, task manager, health server, a global `CognitivePipeline`, and optional systems.
3. `main()` starts Discord, the shutdown monitor, and FastAPI concurrently; it also creates a separate `DatabaseManager` for the web service.
4. On ready, the bot starts cron, moderation scans, and optional proactive/goal loops.

### Discord message path

1. Discord invokes `bot.discord_bot_v1.on_message`, wrapped by AGRE, then delegates to `bot.handlers.message_handler.on_message`.
2. The handler runs prefix commands; non-command messages then pass database access-control checks, rate limiting, command cooldown, moderation, and plugin interception.
3. It verifies chat eligibility, strips Discord mentions, calls the regex-based input validator, checks cache and short-circuits trivial replies.
4. It queues the remaining request in the global `TaskManager`, runs `AzureAgent.handle()` in an executor thread, and edits a Discord progress message with telemetry.
5. `AzureAgent.handle()` records message state, persists to its memory/RAG stores, asks an LLM whether Discord administration is needed, asks the LLM planner for a plan, and directly calls the expanded `execute_plan()` when a plan exists. Otherwise it uses the failover LLM chain.
6. A second listener mirrors every Discord message to the web WebSocket manager.

### Important divergence

The global `COGNITIVE_PIPELINE` is created in `setup()`, but ordinary message handling calls `AGENT.handle()`, not that global pipeline. `AzureAgent.handle()` only invokes an agent-owned `_cognitive_pipeline` if it was previously created through `AzureAgent.cognitize()`; normal setup does not do this. Consequently, the cognitive tool-tier confirmation gate is not reliably on the active message path.

## 4. Strengths

| Rank | Strength |
|---|---|
| Medium | Moderation defaults to `dry_run`, clamps actions by phase, keeps action rate limits, and checks bot permissions (`azure/moderation/`). |
| Medium | User message rate limits, command cooldowns, bounded in-memory caches, and Discord response chunking exist in the primary handler. |
| Medium | SQLite queries use parameter binding; operational state has explicit tables and indexes. |
| Medium | The Discord management code is decomposed into role/channel/member/server/plan mixins, making eventual testing/refactoring tractable. |
| Low | LLM provider fallback, subprocess local-LLM support, health reporting, graceful-shutdown intent, and change tracking provide a useful operational starting point. |
| Low | `.env` is ignored and `.env.example` documents the actively used Discord-token and moderation variables better than the older markdown configuration guide. |

## 5. Findings by severity

### Critical

| ID | Finding | Evidence and impact |
|---|---|---|
| C-01 | Any normally allowed chatter can cause LLM-planned server mutations; destructive actions are not code-gated in the active path. | The primary handler permits `AZURE_CHAT_MODE=anyone`; `AzureAgent.handle()` invokes `execute_plan()` without requester identity or Discord permission validation (`azure/agent.py`). `PlanToolsMixin.execute_plan()` accepts `confirm_destructive` but does not use it to hold deletes, bans, kicks, role changes, or permission changes (`azure/tools/plan_tools.py`). `preflight_check()` exists but is never called. This enables unauthorized server takeover/deletion when the bot has permissions. |
| C-02 | `execute_python` is arbitrary process-level code execution, not a sandbox. | `azure/agentic_tools.py` passes LLM-controlled code to `exec()` with full builtins. The file sandbox only constrains the separate file helpers; it does not constrain Python code. A prompt/tool-call compromise can read secrets, access the network, alter files, or run subprocesses as the bot user. |
| C-03 | Dashboard authentication ships with a predictable signing key and password. | `web/api_auth.py` defaults JWT signing to a known development string and permits `admin` with a default `admin` password. Any reachable deployment without overrides is trivially compromised; JWTs then authorize configuration/moderation APIs. |
| C-04 | Release build and CI are broken in the current checkout. | `Dockerfile` copies and installs deleted `requirements-test.txt`; the development stage also requires it. Both GitHub workflows install/run deleted `tests/` and CI uses the deleted file. `pytest` discovers no tests. Docker image builds and CI cannot be release evidence. |

### High

| ID | Finding | Evidence and impact |
|---|---|
| H-01 | Docker Compose dashboard is non-runnable and health checks target the wrong service/port. | Compose runs deleted `web.app` and development invokes Flask against it, while the repository now has FastAPI `web.server`. Compose publishes/checks port 8080 `/health`; the dedicated health server defaults to 8088 and FastAPI health is `/api/health/`. Dockerfile health check imports undeclared `requests`. |
| H-02 | WebSocket endpoint is unauthenticated and broadcasts Discord content/telemetry. | `web/server.py` accepts any WebSocket; the bot listener broadcasts author IDs, names, content, channel, and guild. REST CORS allows every origin with credentials. This exposes community data to any party that can reach the server. |
| H-03 | Authorization is not enforced for web mutations or management tools. | REST dependencies validate a token but do not check role before phase/mode/emergency/moderation actions. The active Discord planner does not pass or verify caller permissions. Access-control `allow`/`admin` values are stored but only `deny` is consulted. |
| H-04 | Persistent SQLite memory writes are not committed or closed. | `SQLiteMemoryBackend.save_memory()` inserts through `self._conn` without `commit`; `close()` is a no-op. Separate query connections cannot reliably observe writes and a restart can discard them. This invalidates the persistent-memory claim. |
| H-05 | Dashboard analytics, conversation persistence, database telemetry, and audit trail are mostly disconnected. | `DatabaseManager` exposes save methods, but no active callers save conversations or statistics; `ExecutionTracker` broadcasts but never calls `log_telemetry`; `AuditSystem` is not created by runtime wiring. The UI can show an online shell without trustworthy historical data. |
| H-06 | Plugin and integration command APIs do not match their implementations; plugins are deliberately not loaded. | Setup logs plugin loading as disabled. `PluginManager` lacks `enable`, `disable`, `reload`, `shutdown_all`, and the dict-shaped list expected by `!azure_plugin`. `IntegrationRegistry` lacks `get_help_text`, `is_available`, and `query` expected by `!azure_integrations`. |
| H-07 | Retrieval is cross-user/cross-server and persists sensitive content without a privacy policy. | Hybrid RAG records messages with server name/tags but query has no guild/user filter. Moderation JSONL and cognitive state files include message content; retention/deletion/consent controls are absent. |
| H-08 | Input filtering is not a reliable trust boundary. | Regex matching can block benign language, while fetched web content, RAG content, scheduled prompts, and many tool/cognitive paths are not equivalently constrained. The LLM is allowed to generate plans from untrusted text. |
| H-09 | Recovery executor can make unrestricted environmental changes and blocks execution. | `azure/recovery/executor.py` can run `pip install`, create arbitrary paths, alter process environment, and call synchronous `time.sleep`. Its safety depends on strategy metadata rather than an allowlisted, externally approved recovery boundary. |
| H-10 | Global mutable agent context can target the wrong guild/channel if calls occur concurrently outside the global task queue. | `AzureAgent.handle()` writes per-request guild/channel/event loop/tool objects to shared instance attributes. The main handler serializes normal tasks only when `TaskManager` initializes; other paths/fallbacks can interleave and action execution reads those mutable attributes later. |
| H-11 | Configuration and operational docs conflict with runtime. | `docs/CONFIGURATION.md` documents `DISCORD_TOKEN`, `AZURE_LLM_MODEL`, `AZURE_LOCAL_LLM_PATH`, and `AZURE_LLM_THREADS`; runtime uses `AZURE_DISCORD_TOKEN`, `AZURE_MODEL_PATH`, and `AZURE_N_THREADS`. This creates failed deployments and insecure operator workarounds. |

### Medium

| ID | Finding | Evidence and impact |
|---|---|---|
| M-01 | The normal bot path is a global single-task queue with no capacity limit. | `TaskManager` serializes every major task across every guild and stores an unbounded queue. Slow local LLM calls can create multi-minute latency and a memory/Discord-message denial of service. |
| M-02 | Startup and request costs are unnecessarily duplicated. | `DiscordRAG` loads sentence-transformers during construction despite its “lazy” comment; hybrid RAG later initializes another embedding function. The request path can make a decision LLM call, plan LLM call, and execute multiple retried Discord steps. |
| M-03 | RAG does not scale predictably. | `DiscordRAG.search()` stacks all embeddings in memory for each query; hybrid dense search loads every stored embedding from SQLite and has no eviction. Hybrid knowledge-graph state is process-only and is not rebuilt from persisted rows. |
| M-04 | SQLite concurrency discipline is inconsistent. | `DatabaseManager` shares one `check_same_thread=False` connection without a lock/WAL configuration. Memory mixes one long-lived connection with per-operation connections. Concurrent executor/web/loop work can produce locking, visibility, or transaction issues. |
| M-05 | Telemetry delivery is lossy and not thread-safe by design. | `ExecutionTracker` silently drops web broadcasts from worker threads when no running loop is found; callbacks mutate progress state across thread/loop boundaries. Errors are often swallowed. |
| M-06 | Several safety systems are implemented but bypassed or inconsistent. | The cognitive `ToolTierDispatcher` defaults unknown tools to `WRITE_SAFE`, has no populated registry in its default construction, and is not the active planner path. The separate moderation confirmation queue and plan confirmation mechanisms are not one policy boundary. |
| M-07 | Recovery/retry can repeat non-idempotent Discord changes. | AGRE retries a plan as a whole and plan tools retry individual failed steps. There is no idempotency key, compensation transaction, or durable state to prevent duplicate roles/channels/messages after partial completion. |
| M-08 | Shutdown is incomplete/inconsistent. | Main calls nonexistent `PLUGIN_MANAGER.shutdown_all()` (caught), `SQLiteMemoryBackend.close()` does nothing, and cancellation of Uvicorn/Discord tasks has no explicit server shutdown coordination. |
| M-09 | Broad exception handling hides failed safety/observability operations. | Many `except Exception: pass` blocks in handler, telemetry, web broadcast, and integrations conceal failures and prevent an operator from distinguishing safe degradation from silent loss. |
| M-10 | Cron and background goal processing lack user/guild authorization and ownership scope. | Persistent goals and schedules are globally loaded; autonomous loops can surface/advance them without verifying original requester permission or server-specific context. |

### Low

| ID | Finding | Evidence and impact |
|---|---|---|
| L-01 | The worktree has substantial unrelated churn and whitespace warnings. | `git status` reports 96 changed/deleted/untracked paths and `git diff --check` reports trailing whitespace. It obscures review provenance and makes release validation difficult. |
| L-02 | Documentation/repository identity is inconsistent. | Names vary between Azure, Adam-1, Azure v2/v3, and the README still uses placeholder repository URLs; visible encoding corruption reduces operator usability. |
| L-03 | Redundant/legacy implementations increase cognitive load. | There are parallel moderation implementations (`moderation/`, `ai_moderation/`, `auto_moderation.py`), two Discord management implementations, multiple memory/RAG paths, and old/new web designs. |
| L-04 | Static-quality tooling is configured but not available locally. | Ruff configuration and pre-commit hooks exist, but `ruff` is not installed in the audited environment; CI quality/security steps are `continue-on-error`, so they do not gate releases. |

## 6. Technical debt and code duplication

| Rank | Debt |
|---|---|
| High | Duplicate architectures coexist instead of having one authoritative path: legacy `azure/discord_tools.py` and expanded mixins; `rag_engine.py` plus `rag_enhanced.py` plus memory backends; global pipeline plus agent-owned pipeline. |
| High | `discord_bot_v1.py` and `message_handler.py` are orchestration god-modules with globals and imports inside request handlers. This creates implicit dependencies and makes isolated tests difficult. |
| Medium | The current implementation mixes sync SQLite/file/LLM work with async Discord/FastAPI flow. Executor boundaries are ad hoc and repeated. |
| Medium | Multiple old interfaces remain referenced after the web/plugin/integration rewrite, rather than being removed or adapted together. |
| Low | Error-message/emoji encoding and duplicate logger imports reduce maintainability but are secondary to the runtime defects. |

## 7. Race conditions and consistency hazards

| Rank | Hazard |
|---|---|
| High | Shared mutable AzureAgent Discord context can be overwritten between request planning and execution outside the single task-manager path (H-10). |
| High | Database/memory connections are used across threads without a transaction/locking policy (M-04). |
| High | Whole-plan recovery and per-step retries can duplicate successful side effects after partial failure (M-07). |
| Medium | Concurrent JSON/JSONL writes for goals, schedules, logs, and long-term memory have no file locks or atomic replacement. A crash/write collision can corrupt state. |
| Medium | WebSocket connection list is mutated while broadcasts iterate; all sends are serialized and one slow client delays all recipients. |
| Medium | Confirmation/pending-action state is in memory only. Restart loses confirmation records and can leave Discord state partially changed without a durable audit. |

## 8. Missing tests and incomplete implementations

### Missing tests — High

- The tracked `tests/`, `requirements-test.txt`, `pytest.ini`, and startup test are deleted; no pytest tests are currently discovered.
- No retained tests cover privileged Discord planning, caller authorization, destructive confirmation, tool injection, recovery idempotency, multi-guild isolation, SQLite persistence/concurrency, dashboard auth/WebSocket authorization, or Docker startup.
- No contract tests verify that bot commands match PluginManager/IntegrationRegistry interfaces.
- No migration/schema-version tests exist for the several SQLite/JSON stores.

### Incomplete implementations — High

- Plugin loading is intentionally disabled, while management commands call nonexistent APIs.
- Integration commands call nonexistent registry methods.
- Compose points to deleted Flask `web.app`; the active FastAPI implementation has no standalone CLI entry point.
- Cognitive pipeline confirmation/tool-tier functionality exists but is not wired as the authoritative normal execution path.
- `DatabaseManager` tables for audit/telemetry/conversations/stats exist without end-to-end producers.
- Health, dashboard, and compose port/routes disagree.

### Incomplete implementations — Medium

- Vision routing is explicitly a placeholder; several optional subsystems are initialized but not connected to the main path.
- `ModelRouter` specialist and cognitive tiers return `None`; its stats do not demonstrate meaningful routing usage.
- `SelfRepair` reports fixes but does not reliably retry the original operation; recovery policy has no bounded side-effect sandbox.
- RAG persistence is not invoked after ordinary `DiscordRAG.add()` calls, so persisted vector memory is not guaranteed on restart.

## 9. Security concerns

The highest-risk attack chain is: a user permitted to talk to the bot influences LLM planning, the LLM selects a destructive Discord operation, and the active plan executor acts with the bot’s permissions without an authorization or confirmation gate (C-01). Prompt-injection regexes cannot safely compensate for this architecture.

Additional required controls before any production use are: remove or isolate arbitrary Python execution; use a mandatory, rotated secret and an identity provider or stored password hash; authenticate/authorize WebSockets and apply role checks on every mutation; restrict CORS; bind and protect operator endpoints intentionally; allowlist outbound URLs and block private/link-local targets; scope/retain/encrypt user data; and make recovery actions allowlisted and explicitly approved.

## 10. Performance concerns

- Local inference plus repeated decision/planning/review calls can exceed Discord interaction expectations; configured planner/decision timeouts permit ten-minute waits.
- The one global task queue trades races for system-wide head-of-line blocking across all guilds.
- Embedding models and duplicate RAG systems add high startup/RAM cost; both RAG search strategies are linear with data growth.
- Synchronous file/SQLite work and `time.sleep` in recovery can block execution; retries compound Discord rate-limit pressure.
- Dashboard broadcast waits on each socket sequentially and has no back-pressure, size cap, or authentication.

## 11. Production risks

| Rank | Risk |
|---|---|
| Critical | Unauthorized destructive Discord changes or host compromise through the LLM tool surface. |
| Critical | No reproducible CI/Docker release artifact from the current checkout. |
| High | Data exposure via WebSocket, logs, cross-scope RAG, and insecure dashboard defaults. |
| High | Silent loss of memory/telemetry/audit data and non-idempotent replay after partial failures. |
| High | Whole-service latency/outage under LLM slowness, queue growth, or SQLite contention. |
| Medium | Documentation-driven misconfiguration causes startup/deployment failures and bypasses intended safety controls. |

## 12. Release blockers

**Critical — must close before release**

1. Enforce Discord caller authorization and mandatory code-level confirmation for every destructive/privileged plan step; make the active route use one authoritative policy gate.
2. Remove/strictly isolate arbitrary Python execution and constrain recovery actions/outbound fetches.
3. Eliminate default dashboard credentials/signing key; secure REST and WebSocket identity, role authorization, and CORS.
4. Restore a runnable test/dependency contract and fix Docker/CI so a clean checkout builds, starts, and tests successfully.

**High — must close before production rollout**

1. Reconcile the dashboard implementation, command, ports, routes, and health checks.
2. Commit/close persistent memory writes and add thread-safe database access with tested isolation.
3. Wire telemetry/audit/conversation/stats end-to-end, add privacy retention/deletion rules, and scope memory by guild/user.
4. Either complete plugins/integrations or remove their commands and claims until their contracts work.
5. Add automated coverage for all critical paths and make quality/security checks release-gating.

## 13. Recommended sequencing after this audit

1. Freeze feature expansion and create a clean, reproducible baseline from the current worktree.
2. Close C-01 through C-04 with focused security/deployment tests.
3. Consolidate the message execution path around one authorization, confirmation, audit, and idempotency boundary.
4. Reconcile persistence/observability and dashboard architecture.
5. Delete or quarantine unused legacy paths only after behavior-preserving tests cover the retained path.

No implementation changes are included in this phase.
