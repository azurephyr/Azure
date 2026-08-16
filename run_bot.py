"""
Adam-1 Discord Bot Launcher (thin wrapper).

This is the main entry point. It loads environment variables from .env
then delegates to bot/discord_bot_v1.py.

Usage:
    python run_bot.py

Environment variables (can also be set in .env file):
    AZURE_DISCORD_TOKEN      (required) Discord bot token
    AZURE_MODERATION_PHASE   (optional) dry_run | reactive_limited | reactive_full
    AZURE_ADMIN_CHANNEL_ID   (optional) Admin channel for reports
    AZURE_CHAT_MODE          (optional) anyone | owner_only | specific_users | dm_only | mention_only
    AZURE_ALLOWED_USERS      (optional) Comma-separated user IDs for specific_users mode
    AZURE_CONFIRMATION_MODE  (optional) none | destructive | all
    AZURE_CONFIRMATION_THRESHOLD (optional) 0.0 to 1.0

How to create .env:
    1. Copy .env.example to .env
    2. Replace "your-token-here" with your real Discord token
    3. Uncomment and fill any optional settings you want
    4. Save. The .env file is already in .gitignore and won't be committed.
"""

import logging
import os
import sys
from pathlib import Path

from bot.lifecycle import main

# Configure logging early to capture INFO-level messages
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger("run_bot")

# Fix Windows console encoding so emoji/Unicode prints don't crash
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# Detect project root — try multiple strategies for reliability
script_dir = Path(__file__).resolve().parent
cwd = Path(os.getcwd()).resolve()

# Use script_dir if it contains .env or .env.example, otherwise fall back to cwd
ROOT = script_dir if (script_dir / ".env").exists() or (script_dir / ".env.example").exists() else cwd

sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Load .env file — with diagnostics and Windows-specific fixes
# ---------------------------------------------------------------------------

def _find_env_file(search_dir: Path) -> Path | None:
    """Find the actual .env file, handling common Windows gotchas."""
    # Direct match
    direct = search_dir / ".env"
    if direct.exists() and direct.is_file():
        return direct

    # Windows Notepad gotcha: file is actually named .env.txt
    txt_variant = search_dir / ".env.txt"
    if txt_variant.exists() and txt_variant.is_file():
        return txt_variant

    # Also check .env with spaces or other hidden characters
    for f in search_dir.iterdir():
        if not f.is_file():
            continue
        name = f.name.lower().strip()
        if name in (".env", ".env.txt", "env"):
            return f

    return None


env_file = _find_env_file(ROOT)

if env_file is not None:
    logger.info(f"[azure] found env file: {env_file}")

    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=env_file, override=False)
        logger.info(f"[azure] loaded environment from {env_file.name}")

    except ImportError:
        logger.warning("[azure] warning: python-dotenv not installed. Run: pip install python-dotenv")

        logger.info("[azure] environment variables must be set manually.")

else:
    # Diagnostic output — show exactly where we looked and what we found
    logger.info("=" * 60)
    logger.info("[azure] .env FILE NOT FOUND")
    logger.info("=" * 60)
    logger.info("Searched in: %s", ROOT)
    logger.info("Files in that folder:")
    try:
        for f in sorted(ROOT.iterdir()):
            if f.is_file():
                logger.info("  %s", f.name)
    except Exception as e:
        logger.warning("  (could not list: %s)", e)
    logger.info("HOW TO FIX:")
    logger.info("  Step 1: Open File Explorer and go to: %s", ROOT)
    logger.info("  Step 2: Check if your file is named .env.txt instead of .env")
    logger.info("  Windows hides file extensions by default. To see them:")
    logger.info("  File Explorer → View → Show → File name extensions")
    logger.info("  Step 3: If it is .env.txt, rename it to just .env")
    logger.info('  OR create it from the template:')
    logger.info('  copy "%s" "%s"', ROOT / ".env.example", ROOT / ".env")
    logger.info("  Step 4: Open .env in a text editor and set AZURE_DISCORD_TOKEN")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
