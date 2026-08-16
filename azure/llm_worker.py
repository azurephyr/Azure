#!/usr/bin/env python3
"""
LLM Worker Process

Runs in a separate process to avoid blocking the asyncio event loop.
The main bot communicates via JSON over stdin/stdout.

Protocol:
  Worker stdout: ONLY JSON. No plain text.
  Worker stderr: debug logs (safe to print anything).

  Startup:  {"status": "ready"}
  Success:  {"status": "ok", "response": "..."}
  Error:    {"status": "error", "error": "..."}
"""

import json
import os
import sys

# Add project root to path
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root not in sys.path:
    sys.path.insert(0, root)

from azure.local_llm import LocalLLM  # noqa: E402


def main():
    model_path = sys.argv[1] if len(sys.argv) > 1 else None
    n_threads = int(sys.argv[2]) if len(sys.argv) > 2 else None

    if not model_path:
        print(json.dumps({"status": "error", "error": "No model path provided"}), flush=True)
        sys.stderr.write("[llm_worker] ERROR: No model path provided\n")
        sys.exit(1)

    # Suppress LocalLLM's print statements by redirecting stdout to stderr.
    # We MUST only emit JSON on stdout for the parent process.
    original_stdout = sys.stdout
    sys.stdout = sys.stderr

    sys.stderr.write("[llm_worker] loading model...\n")
    try:
        llm = LocalLLM(model_path, n_threads=n_threads)
        sys.stderr.write("[llm_worker] model loaded successfully\n")
    except Exception as e:
        sys.stderr.write(f"[llm_worker] FAILED to load model: {e}\n")
        # Restore stdout to emit the error JSON
        sys.stdout = original_stdout
        print(json.dumps({"status": "error", "error": f"Failed to load model: {e}"}), flush=True)
        sys.exit(1)
    finally:
        # Restore stdout for JSON communication
        sys.stdout = original_stdout

    # Signal ready to parent
    print(json.dumps({"status": "ready"}), flush=True)

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                sys.stderr.write("[llm_worker] stdin closed, exiting\n")
                break

            req = json.loads(line)
            messages = req["messages"]
            max_tokens = req.get("max_tokens", 256)
            temperature = req.get("temperature", 0.7)

            # Run inference (stdout is clean, LocalLLM prints go to stderr)
            response = llm.chat(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            print(json.dumps({"status": "ok", "response": response}), flush=True)

        except Exception as e:
            sys.stderr.write(f"[llm_worker] error during inference: {e}\n")
            print(json.dumps({"status": "error", "error": str(e)}), flush=True)


if __name__ == "__main__":
    main()
