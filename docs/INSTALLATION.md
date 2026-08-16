# Azure Discord Bot - Installation Guide

## System Requirements

### Minimum Requirements
- **OS:** Windows 10/11, Ubuntu 20.04+, macOS 11+
- **Python:** 3.11 or higher
- **RAM:** 8GB
- **Disk:** 2GB free space
- **Network:** Internet connection for Discord API

### Recommended Requirements
- **RAM:** 16GB (for local LLM)
- **CPU:** 4+ cores
- **Disk:** 10GB (for local LLM models)

## Pre-Installation Checklist

- [ ] Python 3.11+ installed and in PATH
- [ ] pip installed and working
- [ ] Git installed (optional, for cloning)
- [ ] Discord bot token obtained
- [ ] Discord bot invited to test server

## Step-by-Step Installation

### Step 1: Get Discord Bot Token

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application"
3. Name your application (e.g., "Azure Bot")
4. Go to "Bot" section
5. Click "Add Bot"
6. Under "Token" click "Copy"
7. **Save this token securely** - you'll need it for configuration

### Step 2: Set Bot Permissions

1. In Developer Portal, go to "OAuth2" → "URL Generator"
2. Select scopes:
   - `bot`
   - `applications.commands`
3. Select bot permissions:
   - Read Messages/View Channels
   - Send Messages
   - Manage Messages
   - Embed Links
   - Attach Files
   - Read Message History
   - Add Reactions
   - Use Slash Commands
   - **OR** just select `Administrator` for full access
4. Copy the generated URL
5. Visit the URL in browser
6. Select your test server
7. Click "Authorize"

### Step 3: Download Azure

#### Option A: Clone with Git
```bash
git clone <repository-url>
cd adam1
```

#### Option B: Download ZIP
1. Download ZIP from repository
2. Extract to desired location
3. Open terminal in extracted folder

### Step 4: Install Python Dependencies

```bash
# Verify Python version
python --version
# Should show Python 3.11 or higher

# Install dependencies
pip install -r requirements.txt

# This installs:
# - discord.py (Discord API)
# - aiohttp (async HTTP)
# - numpy (vector operations)
# - Other dependencies
```

**Troubleshooting:**
- If `pip` not found, try `pip3` or `python -m pip`
- On Windows, ensure Python is in PATH
- On Linux/Mac, may need `sudo pip install` or use virtual environment

### Step 5: Create Configuration File

```bash
# Copy example config
cp .env.example .env

# On Windows PowerShell:
Copy-Item .env.example .env
```

Edit `.env` file:

```env
# === REQUIRED ===
DISCORD_TOKEN=your_bot_token_here

# === OPTIONAL - LLM Configuration ===
# Options: local, openai, anthropic, none (fallback mode)
AZURE_LLM_MODEL=none

# If using local LLM (requires model file)
AZURE_LOCAL_LLM_PATH=models/qwen2.5-3b.gguf
AZURE_LLM_THREADS=4

# If using OpenAI
# OPENAI_API_KEY=your_key_here
# AZURE_LLM_MODEL=openai

# === OPTIONAL - Paths ===
AZURE_RAG_PATH=rag_store.json
AZURE_MEMORY_DB=data/memory.db
AZURE_HYBRID_RAG_DB=data/hybrid_rag.db
AZURE_LOG_DIR=logs/cognition

# === OPTIONAL - Performance ===
AZURE_MEMORY_TURNS=10
AZURE_RAG_MAX_DOCS=1000
AZURE_RAG_K=3
AZURE_LLM_TEMPERATURE=0.7
AZURE_LLM_MAX_TOKENS=512
```

**Important:** Replace `your_bot_token_here` with your actual Discord bot token!

### Step 6: Initialize Database

```bash
# Create data directories
mkdir -p data logs/cognition

# On Windows PowerShell:
New-Item -Path data -ItemType Directory -Force
New-Item -Path logs\cognition -ItemType Directory -Force

# Database will auto-initialize on first run
```

### Step 7: First Run

```bash
python run_bot.py
```

Expected output:
```
[Azure] Initializing...
[Azure] Loading configuration from environment
[Azure] Connecting to Discord...
[Azure] Connected as AzureBot#1234
[Azure] Ready to serve X servers
```

**Success indicators:**
- No error messages
- "Connected as..." appears
- "Ready to serve" appears
- Bot shows as online in Discord

### Step 8: Test Basic Functionality

In your Discord server:

```
@Azure hello
```

Expected response: Bot replies with a greeting

```
@Azure help
```

Expected response: Bot shows help message

**If bot doesn't respond:**
- Check bot is online (green status)
- Check bot has permission to read/send messages
- Check you @mentioned the bot correctly
- Check run_bot.py is still running (no errors in terminal)

## Verification Checklist

- [ ] Python 3.11+ installed
- [ ] Dependencies installed (pip install -r requirements.txt)
- [ ] .env file created with Discord token
- [ ] data/ and logs/ directories created
- [ ] run_bot.py starts without errors
- [ ] Bot shows as online in Discord
- [ ] Bot responds to @mentions
- [ ] No error messages in terminal

## Common Installation Issues

### Issue: "ModuleNotFoundError: No module named 'discord'"
**Solution:** Run `pip install -r requirements.txt`

### Issue: "discord.errors.LoginFailure: Improper token"
**Solution:** Check DISCORD_TOKEN in .env is correct (no quotes, no spaces)

### Issue: Bot connects but doesn't respond
**Solutions:**
1. Check bot has "Message Content Intent" enabled in Discord Developer Portal
2. Check bot has permission to read messages in your server
3. Ensure you're @mentioning the bot

### Issue: "sqlite3.OperationalError"
**Solution:** Ensure data/ directory exists and is writable

### Issue: "Permission denied" when creating directories
**Solution:** Run terminal as administrator or use sudo on Linux/Mac

## Next Steps

- [Configuration Guide](CONFIGURATION.md) - Detailed configuration options
- [Troubleshooting](TROUBLESHOOTING.md) - Common issues and solutions
- [User Guide](USER_GUIDE.md) - How to use Azure features

## Getting Help

If installation fails:

1. Check [Troubleshooting Guide](TROUBLESHOOTING.md)
2. Review error messages carefully
3. Check all prerequisites are met
4. Open an issue on GitHub with:
   - Your OS and Python version
   - Complete error message
   - Steps you've tried

---

**Installation should take 10-15 minutes for a new user.**
