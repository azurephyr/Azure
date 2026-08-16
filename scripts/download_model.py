"""
Adam-1 Model Download Helper

Downloads a local LLM for the chat engine. No API keys needed.

Recommended models (3B, runs on CPU):
  - Qwen2.5-3B-Instruct Q4_K_M (~2GB) — best overall
  - Phi-3.5-mini-instruct Q4_K_M (~2GB) — excellent reasoning
  - Qwen2.5-1.5B-Instruct Q4_K_M (~1GB) — smaller, still good
  - TinyLlama-1.1B Q4_K_M (~600MB) — minimum viable

Usage:
    python scripts/download_model.py

The model will be saved to models/ in your project directory.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Model registry: name -> (url, size_gb, description)
MODELS = {
    "qwen2.5-3b": {
        "url": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
        "filename": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "size_gb": 2.0,
        "description": "Qwen2.5-3B-Instruct Q4_K_M — Best 3B model, excellent instruction following",
    },
    "phi-3.5-mini": {
        "url": "https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF/resolve/main/Phi-3.5-mini-instruct-Q4_K_M.gguf",
        "filename": "phi-3.5-mini-instruct-q4_k_m.gguf",
        "size_gb": 2.0,
        "description": "Phi-3.5-mini-instruct Q4_K_M — Microsoft's 3.8B model, very capable",
    },
    "qwen2.5-1.5b": {
        "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "filename": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "size_gb": 1.0,
        "description": "Qwen2.5-1.5B-Instruct Q4_K_M — Smaller, faster, still decent",
    },
    "tinyllama-1.1b": {
        "url": "https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
        "filename": "tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
        "size_gb": 0.6,
        "description": "TinyLlama-1.1B Q4_K_M — Minimum viable, basic coherence only",
    },
}


def download_file(url: str, dest: Path, chunk_size: int = 8192) -> bool:
    """Download a file with progress."""
    print(f"Downloading: {url}")
    print(f"Destination: {dest}")
    print()

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Adam-1/1.0"})
        with urllib.request.urlopen(req) as response:
            total_size = int(response.headers.get("Content-Length", 0))
            downloaded = 0

            with open(dest, "wb") as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        pct = downloaded / total_size * 100
                        mb = downloaded / (1024 * 1024)
                        print(f"\r  Progress: {mb:.1f} MB / {total_size/(1024*1024):.1f} MB ({pct:.1f}%)", end="")

        print()
        print(f"✅ Download complete: {dest}")
        return True

    except Exception as e:
        print(f"\n❌ Download failed: {e}")
        if dest.exists():
            dest.unlink()  # Remove partial file
        return False


def main():
    print("=" * 60)
    print("  Adam-1 Model Download Helper")
    print("=" * 60)
    print()
    print("Available models:")
    for i, (key, info) in enumerate(MODELS.items(), 1):
        print(f"  {i}. {key}")
        print(f"     {info['description']}")
        print(f"     Size: ~{info['size_gb']} GB")
        print()

    print(f"Choose a model to download (1-{len(MODELS)}), or 'all' to download all:")
    choice = input("> ").strip().lower()

    if choice == "all":
        to_download = list(MODELS.keys())
    else:
        try:
            idx = int(choice) - 1
            to_download = [list(MODELS.keys())[idx]]
        except (ValueError, IndexError):
            print("Invalid choice. Exiting.")
            sys.exit(1)

    successful = []
    for key in to_download:
        info = MODELS[key]
        dest = MODELS_DIR / info["filename"]

        if dest.exists():
            print(f"\n⚠️  {dest.name} already exists. Skip? (y/n)")
            skip = input("> ").strip().lower()
            if skip == "y":
                successful.append(key)
                continue

        print(f"\n--- Downloading {key} ---")
        if download_file(info["url"], dest):
            successful.append(key)
            print("\nTo use this model, set in your .env:")
            print(f"  AZURE_MODEL_PATH=models/{info['filename']}")
        else:
            print(f"\nFailed to download {key}. Try another model or check your internet connection.")

    if successful:
        print("\n" + "=" * 60)
        print("  Download complete!")
        print("=" * 60)
        print(f"\nModels saved to: {MODELS_DIR}")
        print("\nNext steps:")
        print("  1. Add to your .env file:")
        for key in successful:
            print(f"     AZURE_MODEL_PATH=models/{MODELS[key]['filename']}")
        print("  2. Run: python run_bot.py")


if __name__ == "__main__":
    main()
