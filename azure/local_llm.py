"""
Azure Local LLM Chat Engine

Replaces the 1M-parameter babbling LSTM with a real instruction-tuned model.
Supports two backends for Windows compatibility:
  1. ctransformers  (easier install on Windows, pre-built wheels)
  2. llama-cpp-python (more features, harder to install on Windows)

Uses ~2GB RAM with Q4 quantization.

Target model: Qwen2.5-3B-Instruct Q4_K_M.gguf (~2GB)
  - 3B parameters, actually coherent
  - Follows instructions, reasons, holds context
  - ~5-15 tokens/sec on i7-8550U (slow but usable)
  - Entirely local, no API calls

Alternatives (smaller if 2GB is too much):
  - Phi-3.5-mini-instruct Q4 (~2GB) - excellent reasoning
  - Qwen2.5-1.5B-Instruct Q4 (~1GB) - smaller, still decent
  - TinyLlama-1.1B Q4 (~600MB) - minimum viable, basic coherence

Usage:
    from azure.local_llm import LocalLLM
    llm = LocalLLM("models/qwen2.5-3b-instruct-q4_k_m.gguf")
    response = llm.chat([
        {"role": "system", "content": "You are a helpful Discord bot."},
        {"role": "user", "content": "Hello!"}
    ])
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger("azure.local_llm")


class LocalLLM:
    """
    Local instruction-tuned LLM. CPU-optimized, no GPU required.
    Auto-detects backend: ctransformers (Windows-friendly) or llama-cpp-python.

    Model is loaded immediately at construction time.
    """

    def __init__(self, model_path: str | Path, n_ctx: int = 2048,
                 n_threads: int | None = None, temperature: float = 0.7,
                 max_tokens: int = 256, top_p: float = 0.9, verbose: bool = False) -> None:
        """
        Args:
            model_path: Path to .gguf model file
            n_ctx: Context window size (default 2048 — smaller = faster load)
            n_threads: CPU threads for inference (default: os.cpu_count() // 2)
            temperature: Sampling temperature
            max_tokens: Max tokens per response
            top_p: Nucleus sampling threshold
            verbose: Print internal logs
        """
        model_path = Path(model_path)
        if not model_path.exists():
            raise FileNotFoundError(
                f"Model file not found: {model_path}\n"
                f"Download a model first. Run:\n"
                f"  python scripts/download_model.py"
            )

        if n_threads is None:
            n_threads = max(1, (os.cpu_count() or 4) // 2)

        self.model_path = model_path
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self.verbose = verbose

        # Auto-detect backend
        self._backend = self._detect_backend()
        self._model_type = self._detect_model_type()

        self._llm = None
        self._loaded = False

        logger.info(f"[local_llm] config: {model_path.name}, backend={self._backend}, threads={n_threads}, ctx={n_ctx}")


        # Load immediately at startup
        self._load_model()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_model(self) -> None:
        """Load the model immediately. Called once during __init__."""
        logger.info("[local_llm] loading model from disk...")

        start = time.time()

        try:
            if self._backend == "ctransformers":
                self._init_ctransformers()
            elif self._backend == "llama-cpp-python":
                self._init_llama_cpp()
            else:
                raise ImportError(
                    "No local LLM backend found. Install one of:\n"
                    "  pip install ctransformers          (Windows-friendly)\n"
                    "  pip install llama-cpp-python       (more features)\n"
                )
            self._loaded = True
            load_time = time.time() - start
            logger.info(f"[local_llm] model loaded in {load_time:.1f}s")

        except Exception as e:
            load_time = time.time() - start
            logger.info(f"[local_llm] FAILED to load model after {load_time:.1f}s: {e}")

            raise

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    # ------------------------------------------------------------------
    # Backend detection
    # ------------------------------------------------------------------

    def _detect_backend(self) -> str:
        """Detect which backend is available. Prefer llama-cpp-python (better model support)."""
        try:
            import llama_cpp  # noqa: F401
            return "llama-cpp-python"
        except ImportError:
            pass
        try:
            import ctransformers  # noqa: F401
            return "ctransformers"
        except ImportError:
            pass
        return "none"

    def _detect_model_type(self) -> str:
        """Guess model type from filename for prompt formatting."""
        name = self.model_path.name.lower()
        if "qwen" in name:
            return "qwen"
        if "phi" in name:
            return "phi"
        if "tinyllama" in name or "llama" in name:
            return "llama"
        return "generic"

    # ------------------------------------------------------------------
    # ctransformers backend
    # ------------------------------------------------------------------

    def _init_ctransformers(self) -> None:
        # Workaround: some environments (Git Bash, etc.) return empty
        # platform.machine() which breaks py-cpuinfo (a ctransformers dependency).
        import platform
        if not platform.machine():
            platform.machine = lambda: "AMD64"

        from ctransformers import AutoModelForCausalLM
        self._llm = AutoModelForCausalLM.from_pretrained(
            str(self.model_path),
            model_type=self._model_type if self._model_type != "generic" else None,
            threads=self.n_threads,
            context_length=self.n_ctx,
            gpu_layers=0,  # CPU only
        )

    def _chat_ctransformers(self, messages: list[dict[str, str]],
                           temperature: float = None, max_tokens: int = None,
                           top_p: float = None) -> str:
        """Format messages to prompt and generate with ctransformers."""
        prompt = self._format_chat_prompt(messages)
        response = self._llm(
            prompt,
            max_new_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            temperature=temperature if temperature is not None else self.temperature,
            top_p=top_p if top_p is not None else self.top_p,
            stop=self._stop_tokens(),
        )
        return response.strip()

    # ------------------------------------------------------------------
    # llama-cpp-python backend
    # ------------------------------------------------------------------

    def _init_llama_cpp(self) -> None:
        from llama_cpp import Llama
        self._llm = Llama(
            model_path=str(self.model_path),
            n_ctx=self.n_ctx,
            n_threads=self.n_threads,
            n_batch=256,  # smaller batch = faster loading on CPU
            verbose=self.verbose,
        )

    def _chat_llama_cpp(self, messages: list[dict[str, str]],
                        temperature: float = None, max_tokens: int = None,
                        top_p: float = None) -> str:
        """Use llama-cpp-python's chat_completion."""
        response = self._llm.create_chat_completion(
            messages=messages,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            top_p=top_p if top_p is not None else self.top_p,
            stop=self._stop_tokens(),
        )
        return response["choices"][0]["message"]["content"].strip()

    def _stop_tokens(self) -> list[str]:
        """Per-model stop tokens. Empty strings excluded (would stop immediately)."""
        if self._model_type == "qwen":
            return ["</s>", "<|im_end|>", "<|endoftext|>"]
        if self._model_type == "phi":
            return ["</s>", "<|end|>", "<|endoftext|>"]
        if self._model_type == "llama":
            return ["</s>", "<|eot_id|>"]
        return ["</s>"]

    # ------------------------------------------------------------------
    # Prompt formatting (for ctransformers which lacks chat templates)
    # ------------------------------------------------------------------

    def _format_chat_prompt(self, messages: list[dict[str, str]]) -> str:
        """Format chat messages into a prompt string for the model."""
        if self._model_type == "qwen":
            return self._format_qwen(messages)
        if self._model_type == "phi":
            return self._format_phi(messages)
        if self._model_type == "llama":
            return self._format_llama(messages)
        return self._format_generic(messages)

    def _format_qwen(self, messages: list[dict[str, str]]) -> str:
        """Qwen chat format: <|im_start|>role\ncontent</s>"""
        parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            parts.append(f"<|im_start|>{role}\n{content}</s>")
        parts.append("<|im_start|>assistant\n")
        return "\n".join(parts)

    def _format_phi(self, messages: list[dict[str, str]]) -> str:
        """Phi chat format: <|user|>\ncontent\n<|assistant|>\n"""
        parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                parts.append(f"<|system|>\n{content}")
            elif role == "user":
                parts.append(f"<|user|>\n{content}")
            elif role == "assistant":
                parts.append(f"<|assistant|>\n{content}")
        parts.append("<|assistant|>\n")
        return "\n".join(parts)

    def _format_llama(self, messages: list[dict[str, str]]) -> str:
        """Llama-2/3 chat format."""
        parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                parts.append(f"<s>[INST] <<SYS>>\n{content}\n<</SYS>>\n")
            elif role == "user":
                parts.append(f"{content} [/INST]")
            elif role == "assistant":
                parts.append(f" {content} </s><s>[INST]")
        parts.append(" ")
        return "".join(parts)

    def _format_generic(self, messages: list[dict[str, str]]) -> str:
        """Generic format if model type unknown."""
        parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            parts.append(f"{role}: {content}")
        parts.append("assistant:")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        """
        Chat completion with the model.

        Args:
            messages: List of {"role": "system"|"user"|"assistant", "content": str}
            **kwargs: Override temperature, max_tokens, top_p for this call

        Returns:
            Generated text response
        """
        temp = kwargs.get("temperature", self.temperature)
        max_tok = kwargs.get("max_tokens", self.max_tokens)
        top_p = kwargs.get("top_p", self.top_p)

        # Use local variables instead of mutating self (thread-safe)
        start = time.time()
        if self._backend == "ctransformers":
            text = self._chat_ctransformers(messages, temperature=temp, max_tokens=max_tok, top_p=top_p)
        else:
            text = self._chat_llama_cpp(messages, temperature=temp, max_tokens=max_tok, top_p=top_p)

        elapsed = time.time() - start
        # Estimate token count (rough)
        est_tokens = len(text.split()) * 1.3
        speed = est_tokens / elapsed if elapsed > 0 else 0

        # CRITICAL: log to STDERR, never stdout.
        # When this class is used inside llm_worker.py (SubprocessLLM), the
        # parent process reads JSON exclusively from stdout. Printing to
        # stdout here corrupts the JSON protocol and every chat call fails
        # with "LLM worker sent invalid JSON".
        logger.info(
            "[local_llm] %d chars in %.1fs (~%.1f tok/s)",
            len(text), elapsed, speed,
        )

        return text

    def generate(self, prompt: str, **kwargs) -> str:
        """Raw text generation (not chat format)."""
        if self._backend == "ctransformers":
            response = self._llm(
                prompt,
                max_new_tokens=kwargs.get("max_tokens", self.max_tokens),
                temperature=kwargs.get("temperature", self.temperature),
                top_p=kwargs.get("top_p", self.top_p),
                stop=self._stop_tokens(),
            )
            return response.strip()
        else:
            response = self._llm(
                prompt,
                temperature=kwargs.get("temperature", self.temperature),
                max_tokens=kwargs.get("max_tokens", self.max_tokens),
                top_p=kwargs.get("top_p", self.top_p),
                stop=self._stop_tokens(),
                echo=False,
            )
            return response["choices"][0]["text"].strip()

    def count_tokens(self, text: str) -> int:
        """Count tokens in a string."""
        if not self._loaded:
            return len(text) // 4  # rough estimate
        if self._backend == "llama-cpp-python":
            return len(self._llm.tokenize(text.encode("utf-8")))
        # ctransformers doesn't expose tokenize, estimate
        return len(text) // 4

    def get_info(self) -> dict:
        """Return model info."""
        info = {
            "model_path": str(self.model_path),
            "backend": self._backend,
            "model_type": self._model_type,
            "n_ctx": self.n_ctx,
            "n_threads": self.n_threads,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "loaded": self._loaded,
        }
        return info
# ---------------------------------------------------------------------------
# Subprocess wrapper (avoids GIL blocking)
# ---------------------------------------------------------------------------

class SubprocessLLM:
    """
    LLM wrapper that runs inference in a separate process to avoid blocking
    the asyncio event loop. The llama-cpp-python C extension holds the GIL
    during inference, so even ThreadPoolExecutor doesn't help. A subprocess
    completely sidesteps the issue.

    IMPORTANT: Always call stop() when done to prevent zombie processes.
    """

    def __init__(self, model_path: str | Path, n_threads: int | None = None,
                 startup_timeout: int = 60, inference_timeout: int = 600, **kwargs):  # 10 minutes - effectively unlimited
        self.model_path = Path(model_path)
        self.n_threads = n_threads
        self.temperature = kwargs.get("temperature", 0.7)
        self.max_tokens = kwargs.get("max_tokens", 256)
        self.startup_timeout = startup_timeout  # Timeout for model loading
        self.inference_timeout = inference_timeout  # 600s = 10min (wait for response)
        self._proc = None
        self._ready = False
        self._start_called = False
        self._lock = threading.Lock()
        self._stderr_thread = None
        self._stopping = False

        # Register cleanup handlers to prevent zombie processes
        import atexit
        atexit.register(self.stop)

    def stop(self) -> None:
        """
        Gracefully stop the LLM worker process.
        CRITICAL: Prevents zombie processes that consume memory/CPU.
        """
        if self._stopping:
            return  # Already stopping

        self._stopping = True

        with self._lock:
            if self._proc is not None:
                try:
                    # Try graceful shutdown first (close stdin = worker exits cleanly)
                    if self._proc.stdin:
                        self._proc.stdin.close()

                    # Wait up to 5 seconds for graceful exit
                    try:
                        self._proc.wait(timeout=5)
                        logger.info("[subprocess_llm] Worker stopped gracefully")

                    except Exception:
                        # Force kill if graceful shutdown times out
                        self._proc.kill()
                        self._proc.wait()
                        logger.info("[subprocess_llm] Worker force-killed")

                except Exception as e:
                    logger.info(f"[subprocess_llm] Error stopping worker: {e}")

                finally:
                    self._proc = None
                    self._ready = False
                    self._start_called = False

    def __del__(self):
        """Cleanup on garbage collection."""
        self.stop()

    def __enter__(self):
        """Context manager support."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager cleanup."""
        self.stop()

    def _readline_with_timeout(self, timeout_seconds: float) -> str:
        """
        Read a line from subprocess stdout with timeout.
        CRITICAL: Prevents infinite hangs if worker crashes mid-response.

        Args:
            timeout_seconds: Maximum time to wait for response

        Returns:
            Line from stdout

        Raises:
            TimeoutError: If timeout expires
            RuntimeError: If worker process died
        """
        import queue

        result_queue = queue.Queue()

        def read_thread():
            try:
                line = self._proc.stdout.readline()
                result_queue.put(("line", line))
            except Exception as e:
                result_queue.put(("error", e))

        thread = threading.Thread(target=read_thread, daemon=True)
        thread.start()

        try:
            event_type, data = result_queue.get(timeout=timeout_seconds)

            if event_type == "error":
                raise data

            if not data:  # Empty line = process died
                raise RuntimeError("LLM worker process died unexpectedly")

            return data

        except queue.Empty as exc:
            # Timeout - kill the process and restart
            logger.info(f"[subprocess_llm] TIMEOUT after {timeout_seconds}s, killing worker")

            if self._proc:
                self._proc.kill()
                self._proc.wait()
            self._ready = False
            self._start_called = False
            raise TimeoutError(f"LLM worker timeout after {timeout_seconds}s") from exc

    def _start_stderr_reader(self):
        """
        Start background thread to read stderr.
        CRITICAL: Prevents buffer overflow that causes worker to hang.
        """
        def read_stderr():
            try:
                for line in self._proc.stderr:
                    if line:
                        # Forward to parent's stderr with prefix
                        logger.info(f"[llm_worker] {line.rstrip()}")

            except Exception as e:
                logger.error(f"[subprocess_llm] stderr reader error: {e}")


        self._stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        self._stderr_thread.start()

    def _start_worker(self) -> bool:
        """Start the worker process. Returns True if successful."""
        import subprocess

        if self._start_called:
            return True
        self._start_called = True

        worker_path = Path(__file__).parent / "llm_worker.py"
        if not worker_path.exists():
            raise FileNotFoundError(f"LLM worker script not found: {worker_path}")

        args = [sys.executable, str(worker_path), str(self.model_path.resolve())]
        if self.n_threads is not None:
            args.append(str(self.n_threads))

        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',  # CRITICAL: Windows requires explicit UTF-8 encoding
            bufsize=1,
        )

        # Start stderr reader thread to prevent buffer overflow
        self._start_stderr_reader()

        try:
            # Read until we get a valid JSON status message (WITH TIMEOUT)
            # The worker emits ONLY JSON on stdout (debug goes to stderr)
            try:
                line = self._readline_with_timeout(self.startup_timeout)
            except TimeoutError as exc:
                raise RuntimeError(f"LLM worker startup timeout after {self.startup_timeout}s") from exc

            try:
                result = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"LLM worker sent invalid JSON: {line.strip()[:200]}") from exc

            if result.get("status") == "error":
                raise RuntimeError(f"LLM worker failed to start: {result.get('error')}")
            if result.get("status") != "ready":
                raise RuntimeError(f"LLM worker unexpected status: {result.get('status')}")

            self._ready = True
            logger.info("[subprocess_llm] Worker started successfully")
            return True
        except Exception:
            self.stop()
            raise

    def start(self) -> bool:
        """Start the worker process synchronously."""
        with self._lock:
            return self._start_worker()

    def _ensure_alive(self) -> bool:
        """Check if the worker process is still alive. Restart if not."""
        if self._proc is None:
            return self._start_worker()
        if self._proc.poll() is not None:
            # Process died, restart it
            logger.info(f"[subprocess_llm] worker died (exit code {self._proc.poll()}), restarting...")

            self._start_called = False
            self._ready = False
            return self._start_worker()
        return True

    def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        """Send a chat request and return the response. Thread-safe."""

        with self._lock:
            if not self._ready:
                self._start_worker()

            self._ensure_alive()

            req = {
                "messages": messages,
                "max_tokens": kwargs.get("max_tokens", self.max_tokens),
                "temperature": kwargs.get("temperature", self.temperature),
            }
            self._proc.stdin.write(json.dumps(req) + "\n")
            self._proc.stdin.flush()

            # Read with timeout to prevent infinite hangs
            timeout = kwargs.get("timeout", self.inference_timeout)
            try:
                line = self._readline_with_timeout(timeout)
            except TimeoutError as exc:
                # Timeout - worker will be restarted on next request
                raise TimeoutError(f"LLM inference timeout after {timeout}s") from exc

            try:
                result = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"LLM worker sent invalid JSON: {line.strip()[:200]}") from exc

            if result.get("status") == "error":
                raise Exception(result["error"])
            return result["response"]

    def generate(self, prompt: str, **kwargs) -> str:
        """Raw text generation (not chat format)."""
        messages = [{"role": "user", "content": prompt}]
        return self.chat(messages, **kwargs)

    def count_tokens(self, text: str) -> int:
        """Rough token estimate."""
        return len(text) // 4

    def get_info(self) -> dict[str, object]:
        return {
            "model_path": str(self.model_path),
            "backend": "subprocess",
            "loaded": self._ready,
            "n_threads": self.n_threads,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
