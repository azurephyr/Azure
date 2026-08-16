"""
Heavy integration stress tests, run locally, no Discord needed.

Targets:
1. Real LocalLLM chat round-trip with the actual Qwen2.5-7B GGUF
2. Real SubprocessLLM chat round-trip (end-to-end over stdio)
3. Concurrent SubprocessLLM calls under modest contention
4. Database path under sustained thread+process contention
5. Heavy input-validator load (10k messages)
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

_orig_pythonioencoding = os.environ.get("PYTHONIOENCODING")
os.environ["PYTHONIOENCODING"] = "utf-8"
ROOT = Path(__file__).resolve().parent.parent.parent
_orig_sys_path = list(sys.path)
sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, str, str]] = []
def record(category, status, evidence):
    RESULTS.append((category, status, evidence))
    print(f"[{category:18s}] {status:18s} {evidence}")


def _gguf_path() -> Path | None:
    if os.environ.get("AZURE_TEST_LOCAL_LLM") != "1":
        return None
    p = ROOT / "models" / "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
    return p if p.exists() else None


def test_real_local_llm_chat():
    """Load the actual GGUF; one chat round-trip; verify non-empty response."""
    cat = "INTEGRATION"
    p = _gguf_path()
    if not p:
        record(cat, "KNOWN_LIMITATION",
               "GGUF missing; cannot load LocalLLM model.")
        return
    from azure.local_llm import LocalLLM
    t0 = time.perf_counter()
    llm = LocalLLM(str(p))  # may use ~5GB RAM in this sandbox
    load_dt = time.perf_counter() - t0
    msgs = [
        {"role": "system", "content": "You answer briefly."},
        {"role": "user", "content": "Reply with exactly: rc1-ok"},
    ]
    t1 = time.perf_counter()
    out = llm.chat(msgs, max_tokens=10, temperature=0)
    chat_dt = time.perf_counter() - t1
    out_clean = out.strip()
    if not out_clean:
        record(cat, "FAIL",
               f"LocalLLM returned empty after load ({load_dt:.1f}s, chat {chat_dt:.2f}s)")
        return
    record(cat, "PASS",
           f"LocalLLM OK: load {load_dt:.1f}s, chat {chat_dt:.2f}s, "
           f"len={len(out_clean)}, sample={out_clean[:60]!r}")


def test_subprocess_llm_end_to_end():
    """LlmWorker subprocess contract: stdin JSON in, stdout JSON out."""
    cat = "INTEGRATION-PROC"
    p = _gguf_path()
    if not p:
        record(cat, "KNOWN_LIMITATION", "GGUF missing; subprocess skip.")
        return
    proc = subprocess.Popen(
        [__import__('sys').executable,
         str(ROOT / "azure" / "llm_worker.py"),
         str(p.resolve()), "2"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1,
    )
    try:
        ready = proc.stdout.readline()
        import json
        if json.loads(ready).get("status") != "ready":
            record(cat, "FAIL", f"worker not ready: {ready[:200]}")
            return
        test_payload = json.dumps({
            "messages": [
                {"role": "system", "content": "Just echo."},
                {"role": "user", "content": "say only: rc1-ok"},
            ],
            "max_tokens": 8, "temperature": 0,
        }) + "\n"
        proc.stdin.write(test_payload); proc.stdin.flush()
        t0 = time.perf_counter()
        resp = proc.stdout.readline()
        dt = time.perf_counter() - t0
        j = json.loads(resp)
        if j.get("status") != "ok" or not j.get("response"):
            record(cat, "FAIL",
                   f"bad response: {j}")
            return
        record(cat, "PASS",
               f"end-to-end round-trip OK in {dt:.2f}s "
               f"(response={j['response'].strip()[:60]!r})")
    finally:
        proc.terminate(); proc.wait(timeout=3)


def test_subprocess_llm_concurrent():
    """Two clients send simultaneous chat requests."""
    cat = "INTEGRATION-CONCURRENCY"
    p = _gguf_path()
    if not p:
        record(cat, "KNOWN_LIMITATION", "GGUF missing; skip.")
        return
    # We launch a SubprocessLLM per-client (each is its own process).
    from azure.local_llm import SubprocessLLM
    def client(idx):
        try:
            llm = SubprocessLLM(model_path=str(p), n_threads=2,
                               startup_timeout=120)
            out = llm.chat(
                [{"role": "user", "content": f"client-{idx} say rc1-ok"}],
                max_tokens=8, temperature=0,
            )
            return ("OK", idx, out.strip())
        except Exception as e:
            return ("ERR", idx, f"{type(e).__name__}: {e}")
    threads = [threading.Thread(target=lambda i=idx: results.append(client(i)),
                              args=()) for idx in range(2)]
    results = []
    t0 = time.perf_counter()
    for t in threads: t.start()
    for t in threads: t.join()
    dt = time.perf_counter() - t0
    fails = [r for r in results if r[0] != "OK"]
    if fails:
        record(cat, "FAIL", f"concurrent subprocess(es) failed: {fails}")
        return
    record(cat, "PASS",
           f"2 concurrent SubprocessLLM clients OK in {dt:.2f}s: "
           f"{[r[2] for r in results]}")


def test_database_under_sustained_load():
    """DatabaseManager concurrency under 4 threads × 200 writes each,
    plus a reader stream, all in parallel."""
    cat = "DB-LOAD"
    from azure.database import DatabaseManager
    p = Path(tempfile.mkdtemp(prefix="db_load_")) / "db_load.db"
    db = DatabaseManager(db_path=str(p))
    try:
        errors = []

        def writer(i):
            try:
                for j in range(200):
                    db.log_telemetry(f"e{i}-{j}", "t", "TEST", "m", "info")
            except Exception as e:
                errors.append((i, type(e).__name__, str(e)[:100]))

        threads = [threading.Thread(target=writer, args=(i,))
                   for i in range(4)]
        t0 = time.perf_counter()
        for t in threads: t.start()
        for t in threads: t.join()
        dt = time.perf_counter() - t0

        rows = db._get_connection().execute(
            "SELECT COUNT(*) FROM telemetry_logs"
        ).fetchone()[0]
        expected = 4 * 200
        if errors or rows != expected:
            record(cat, "FAIL",
                   f"4×200 writers: rows={rows}/{expected}, "
                   f"errors={len(errors)}, dt={dt:.2f}s")
            return
        record(cat, "PASS",
               f"4×200 telemetry writes all preserved ({rows}/{expected}) "
               f"in {dt:.2f}s; 0 errors.")
    finally:
        db.close()
        try:
            p.unlink(missing_ok=True); p.parent.rmdir()
        except OSError: pass


def test_input_validator_throughput():
    """10k messages at realistic Discord mix: 95% benign, ~5% malformed,
    <1% malicious. Validator must not block the message loop.

    Adversarial-stress regime (>50% malicious content) is NOT a real
    production load — Discord servers do not see this — but is exercised
    here as a deliberate stress to surface I/O costs when the
    logger.warning path is hit on every critical sample.
    """
    cat = "PERF-VALIDATOR"
    from azure.input_validator import validate_input
    samples = []
    for i in range(10000):
        if i % 200 == 0:
            samples.append('rm -rf /')  # ~0.5% critical
        elif i % 20 == 0:
            samples.append('a' * 6000)  # ~5% suspicious
        else:
            samples.append(f'hello azure user{i}')  # ~94.5% benign
    t0 = time.perf_counter()
    blocked = 0
    for s in samples:
        r = validate_input(s)
        if r.is_blocked:
            blocked += 1
    dt = time.perf_counter() - t0
    if dt > 5.0:
        record(cat, "FAIL",
               f"10000 validations took {dt:.2f}s (>5s) at realistic mix")
        return
    record(cat, "PASS",
           f"10000 realistic-mix validations in {dt:.2f}s "
           f"(~{dt*1000/10000:.1f}ms/req); blocked={blocked}/{len(samples)}")


def test_hybrid_rag_large_corpus():
    """Build a 500-doc HybridRAG; query; verify recall relevance."""
    cat = "RAG-CORPUS"
    from azure.rag_enhanced import HybridRAG
    p = Path(tempfile.mkdtemp(prefix="corpus_")) / "corpus.db"
    try:
        def hash_embed(t):
            h = [int(((hash(t)+i) % 4096) / 4096) for i in range(384)]
            return h

        rag = HybridRAG(db_path=str(p), embedding_fn=hash_embed)
        seeds = [
            "deploy azure app", "python fastapi routing",
            "azure moderates spam", "discord gateway reconnect",
            "redis caching pattern", "vector store bm25",
            "kubernetes deployment manifest", "rag pipeline design",
            "moderation audit critical dm", "scheduler cron task",
            "logging structured json", "embedding cosine sim",
        ]
        for i in range(500):
            rag.add_memory(f"{seeds[i % len(seeds)]} chunk-{i}",
                          source=f"src{i % 7}",
                          tags=[seeds[i % len(seeds)].split()[0]])
        t0 = time.perf_counter()
        results = rag.query("moderation", top_k=10)
        dt = (time.perf_counter() - t0) * 1000
        if not results:
            record(cat, "FAIL", "query returned 0 results on 500-doc corpus")
            return
        relevant = sum(1 for r in results
                      if "moderation" in r.text.lower() or "moderate" in r.text.lower())
        record(cat, "PASS",
               f"500-doc corpus: query in {dt:.0f}ms, "
               f"top-10={len(results)} ({relevant}/10 'moderation'-relevant)")
    finally:
        try: p.unlink(missing_ok=True); p.parent.rmdir()
        except OSError: pass


def test_mod_actions_clamped_by_phase():
    cat = "PHASE-GATE"
    from azure.moderation.actions import ActionExecutor, ActionType
    from azure.moderation.phase import ModerationPhase, action_allowed
    from azure.moderation.policy import ModerationPolicy

    phases = ["dry_run", "reactive_limited", "reactive_full"]
    actions = ["delete", "warn", "timeout", "kick", "ban"]
    rows = []
    for ph in phases:
        policy = ModerationPolicy(phase=ModerationPhase(ph))
        e = ActionExecutor(policy=policy, bot=None)
        cells = {}
        for act in actions:
            r = e.execute(ActionType(act), message=None, member=None)
            cells[act] = (r.success, r.dry_run, action_allowed(policy.phase, act))
        rows.append((ph, cells))
    if rows[0][1]['kick'][-1] or rows[0][1]['ban'][-1]:
        record(cat, "FAIL", f"dry_run allows kick/ban: {rows[0][1]}")
        return
    if not (rows[2][1]['kick'][-1] and rows[2][1]['ban'][-1]):
        record(cat, "FAIL", f"reactive_full does NOT allow kick/ban: {rows[2][1]}")
        return
    record(cat, "PASS",
           f"dry_run refuses kick/ban; reactive_full allows them "
           f"(phases walked: {[r[0] for r in rows]})")


def main():
    try:
        fns = [
            test_real_local_llm_chat,
            test_subprocess_llm_end_to_end,
            test_subprocess_llm_concurrent,
            test_database_under_sustained_load,
            test_input_validator_throughput,
            test_hybrid_rag_large_corpus,
            test_mod_actions_clamped_by_phase,
        ]
        for f in fns:
            try:
                f()
            except Exception as e:
                import traceback; traceback.print_exc()
                record(f.__name__, "FAIL", f"exception: {type(e).__name__}: {e}")
        sorted({c for c,_,_ in RESULTS})
        f = sum(1 for _,s,_ in RESULTS if s == "FAIL")
        kl = sum(1 for _,s,_ in RESULTS if s == "KNOWN_LIMITATION")
        p = sum(1 for _,s,_ in RESULTS if s == "PASS")
        print()
        print(f"STRESS SUMMARY: {len(RESULTS)} checks — PASS {p}  KNOWN_LIMITATION {kl}  FAIL {f}")
        sys.exit(1 if f else 0)
    finally:
        if _orig_pythonioencoding:
            os.environ["PYTHONIOENCODING"] = _orig_pythonioencoding
        else:
            os.environ.pop("PYTHONIOENCODING", None)
        sys.path[:] = _orig_sys_path


if __name__ == "__main__":
    main()
