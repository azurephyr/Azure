# Azure Grok Optimization Report

**Project:** Azure v1.0 RC1 (`C:/Users/Adam2/Desktop/Azure AI/Azure`)  
**Auditor:** Grok 4.5 Staff Systems / Performance pass  
**Date:** 2026-07-12  
**Constraint:** RC1 freeze — only proven failure modes and measurable bottlenecks fixed. No cosmetic rewrites.

---

## Executive summary

| Phase | Result |
|---|---|
| Directory audit | 154 Python modules mapped; entry path `run_bot.py` → Discord bot → `AzureAgent.handle` / FastAPI `web.server` → SQLite / Hybrid RAG / SubprocessLLM |
| Bugs fixed | **5** proven correctness defects |
| Bottlenecks optimized | **4** hot paths (RAG dense search, fusion fetch, embedding matrix, SQLite WAL) |
| Certification | **`61 passed`** in `tests/certification/` (91s) |
| Deferred / skipped | Security/product items already marked KNOWN_LIMITATION or v1.1 in `docs/RC1_KNOWN_LIMITATIONS.md` |

---

## Phase 1 — Execution map

```text
run_bot.py
  └─ bot/discord_bot_v1.py
       ├─ AzureAgent (azure/agent.py)
       │    ├─ SubprocessLLM / LocalLLM / api_llm + failover_chain
       │    ├─ DiscordRAG (rag_engine) + HybridRAG (rag_enhanced)
       │    ├─ SQLiteMemoryBackend (memory_backend)
       │    ├─ TaskManager (global serialize)
       │    └─ Discord tools → execute_plan (authorization gated)
       ├─ handlers/message_handler.py  (main Discord ingress)
       ├─ cognition/CognitivePipeline  (optional / parallel path)
       ├─ health_server.py
       └─ web/server.py (FastAPI + JWT + WebSocket)
            └─ DatabaseManager (azure/database.py)
```

**Concurrency surfaces reviewed**

| Surface | Mechanism | Status before this pass |
|---|---|---|
| `DatabaseManager` shared conn | `threading.Lock` `_wlock` | KL-4 fixed for most writers; **cache hit UPDATE unlocked** |
| `SQLiteMemoryBackend` | `_wlock` + WAL | Solid (apex suite) |
| HybridRAG | Per-op `sqlite3.connect` | No WAL; **no emb cache**; **N SELECTs**; KG RAM-only |
| DiscordRAG | In-memory list | **`np.stack` every search**; **add never persisted** |
| WebSocket manager | Async list | **List mutated across `await`** |
| Telemetry from executor threads | `get_running_loop` only | **Silent drop off event loop** |
| TaskManager | `asyncio.Lock` | Serializes main path (H-10 mitigated) |

---

## Phase 2 — Triage & proof

### BUG-1 — `DatabaseManager.get_cache_entry` write without lock

**File:** `azure/database.py` (pre-fix ~L532–552)  
**Failure class:** KL-4 sibling — concurrent SELECT+UPDATE+`commit` on shared `check_same_thread=False` connection.

```text
Thread A: get_cache_entry → UPDATE hit_count → commit
Thread B: save_conversation → INSERT → commit  (under _wlock)
→ "cannot start a transaction within a transaction" / dropped hit accounting
```

**Proof:** Read path performed an **unlocked write**. Same mathematical race as concurrent memory writes before `_wlock`.

### BUG-2 — Tag filter applied after SQL `LIMIT` under-returns rows

**File:** `azure/memory_backend.py` `query_memories`  
**Failure:** For `limit=10, tags=["chat"]`, SQL returns newest 10 rows; Python drops non-matching tags → often **0 results** even when older matching rows exist.

**Proof:** Order of operations was `LIMIT` then filter; correct order is filter then limit (or over-fetch).

### BUG-3 — WebSocket broadcast iterates live connection list across `await`

**File:** `web/server.py` `ConnectionManager.broadcast`  
**Failure:** `await send_json` yields; another task can `connect`/`disconnect` mid-loop → `RuntimeError: list changed size during iteration` or skipped clients.

**Proof:** Single-threaded asyncio still interleaves at await points.

### BUG-4 — Hybrid RAG knowledge graph empty after process restart

**File:** `azure/rag_enhanced.py`  
**Failure:** `kg` was only mutated in `add_memory`; never rebuilt from SQLite → after restart `_kg_boost` always `{}` (0.20 weight of fusion silent).

**Proof:** `_init_db` created tables only; no KG load.

### BUG-5 — DiscordRAG `add()` never persisted to disk

**File:** `azure/rag_engine.py`  
**Failure:** `persist_path` only loaded at init; `add` never called `save()` → restart loses all vector memory despite configured path.

**Proof:** `save()` existed but had no callers on the write path.

---

### BOTTLENECK-A — Hybrid dense search: O(n) JSON parse every query

**File:** `azure/rag_enhanced.py` `_dense_search`  
**Before:** Each query `SELECT id, embedding FROM memories` → `json.loads` × n → normalize × n → Python loop dots.  
**Complexity:** **O(n · (JSON + d))** per query.

### BOTTLENECK-B — Hybrid fusion: N+1 SELECTs

**File:** `query()` candidate loop  
**Before:** One `SELECT … WHERE id = ?` per candidate (up to `top_k * 3`).  
**Complexity:** **O(k) round-trips**.

### BOTTLENECK-C — DiscordRAG restacks matrix every search

**File:** `rag_engine.search`  
**Before:** `np.stack([d.embedding for d in self.docs])` every call → **O(n·d) alloc+copy** before the same **O(n·d)** matmul.

### BOTTLENECK-D — Main DB without WAL

**File:** `database.py` `_get_connection`  
**Before:** Default rollback journal; readers block more under multi-thread bot+web.

---

## Phase 3 — Surgical fixes (what changed)

| # | File | Change | Complexity before → after |
|---|---|---|---|
| 1 | `azure/database.py` | `get_cache_entry` fully under `_locked_conn()`; WAL + `busy_timeout`; `close()` under lock | Unlocked RMW → serialized txn; journal → WAL |
| 2 | `azure/memory_backend.py` | Tag queries over-fetch then filter until `limit` matches | False empty set → correct top-k tags |
| 3 | `web/server.py` | `broadcast` iterates `list(self.active_connections)` snapshot | Race on mutate → stable O(n) snapshot |
| 4 | `azure/rag_enhanced.py` | Emb matrix cache + BLAS `@`; batch `IN (...)` fetch; WAL connections; lock; KG rebuild | Dense O(n·JSON·d)/q → O(n·d) matmul; fusion O(k) SQL → O(1) SQL |
| 5 | `azure/rag_engine.py` | Cached matrix + append; RLock; periodic atomic save every 25 adds | Stack-every-search → amortized O(1) matrix maintain; durable store |
| 6 | `azure/telemetry.py` | Worker-thread path uses `run_coroutine_threadsafe` when loop running | Silent drop → best-effort dashboard delivery |

Inline comments in code document the algorithmic rationale (no drive-by refactors).

---

## Phase 4 — Regression check

| Check | Result |
|---|---|
| `pytest tests/certification/ -q` | **61 passed**, 1 unrelated deprecation warning |
| Apex concurrent memory suite | **PASS** (8×200 inserts, 0 errors) |
| Concurrent `get_cache_entry` stress (8×50) | **0 errors**, hits accumulate |
| HybridRAG synthetic emb (30 docs) | matrix `(30,16)`, query returns 5, KG rebuild on reopen |
| DiscordRAG fake model | eviction max_docs=5, search, save/load **OK** |
| Discord plan auth / TaskManager / SubprocessLLM protocol | **Not modified** — behavioral compatibility preserved |
| RAG fusion weights (0.35 / 0.45 / 0.20) | Unchanged |
| JWT / WebSocket token gate | Unchanged (auth path intact) |

---

## Bugs squashed (summary table)

| ID | Bug | Severity | Before | After |
|---|---|---|---|---|
| G-01 | Cache hit UPDATE without `_wlock` | High (data race) | Unlocked write on shared conn | Locked SELECT+UPDATE+commit |
| G-02 | `query_memories` tag+LIMIT wrong order | Medium (logic) | Under-return / empty | Over-fetch + correct limit of matches |
| G-03 | WS list mutation during broadcast | Medium (asyncio race) | Live list + await | Snapshot copy |
| G-04 | Hybrid KG not restored | Medium (silent quality loss) | Empty KG post-restart | Rebuild from rows |
| G-05 | DiscordRAG never saves on add | Medium (memory loss) | Persist path useless | Atomic save every 25 adds |

---

## Bottlenecks optimized (summary table)

| ID | Hot path | Before | After |
|---|---|---|---|
| P-01 | Hybrid dense search | O(n) JSON + Python dots / query | Cached float matrix × query (BLAS) |
| P-02 | Hybrid candidate materialize | k individual SELECTs | Single `WHERE id IN (…)` |
| P-03 | DiscordRAG search | `np.stack` every search | Dirty-flag matrix + vstack append |
| P-04 | Main SQLite | Default journal | WAL + busy_timeout=5000 |

---

## Files analyzed but **not** changed (and why)

| Area | Why skipped |
|---|---|
| `docs/RC1_KNOWN_LIMITATIONS.md` KL-14…KL-20 | Explicitly deferred to v1.1 / accepted risk |
| Plan authorization / destructive tools | Working under `require_authorization=True`; security product work, not a new proven bug |
| `execute_python` restricted sandbox | Already disabled by default; no failure under RC1 defaults |
| Global `TaskManager` single queue (M-01) | Intentional HOL blocking vs race tradeoff; redesign is architectural |
| Agent shared `_current_guild` (H-10) | Mitigated by TaskManager; full context object redesign is v1.1 |
| AGRE recovery privileges (KL-19) | Documented known limitation |
| Web CORS `*` default | Config/ops concern; env already supports restriction |
| `cognition/` full multi-agent pipeline | Parallel path; not hot path for ordinary chat; no proven crash in certification |
| BOM (`U+FEFF`) on many files | Import works; mass re-save is cosmetic RC1 risk |
| `models/*.gguf`, `data/*.db`, logs | Binary/runtime artifacts |
| Docker Compose alignment (KL-14) | Deferred infra |
| Plugin manager (KL-16) | Deliberately disabled |

---

## Residual risks (monitor, do not “fix” blindly)

1. **Hybrid embedding_fn still None at agent init** (`agent.py` HybridRAG constructed with `embedding_fn=None`) — dense path only activates if wiring is completed elsewhere; BM25+KG still work. Separate product ticket, not a regression from this pass.
2. **Telemetry thread→loop** still best-effort: if no loop is registered on the worker’s policy, events drop (cannot invent a loop).
3. **Unbounded TaskManager queue** remains a DoS/latency risk under multi-guild load.
4. **Dense matrix is full-scan** — correct for current scale; true ANN (HNSW/FAISS) is v1.1+ when n ≫ 10k.

---

## Deliverable checklist

- [x] Phase 1 deep directory audit  
- [x] Phase 2 triage with file-level proof  
- [x] Phase 3 surgical optimizations  
- [x] Phase 4 regression (`61` certification tests green)  
- [x] This report: `AZURE_GROK_OPTIMIZATION_REPORT.md`

---

*End of report.*
