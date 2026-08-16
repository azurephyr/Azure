# Azure Troubleshooting Guide

Common issues and solutions for Azure Discord Bot.

## Startup Issues

### Bot Won't Start - "ModuleNotFoundError"

**Symptoms:**
```
ModuleNotFoundError: No module named 'discord'
```

**Cause:** Dependencies not installed

**Solution:**
```bash
pip install -r requirements.txt
```

### Bot Won't Start - "Improper token"

**Symptoms:**
```
discord.errors.LoginFailure: Improper token has been passed
```

**Causes:**
1. Wrong token in .env file
2. Token has extra spaces or quotes
3. Token was regenerated in Discord portal

**Solutions:**
1. Verify AZURE_DISCORD_TOKEN in .env is correct
2. Remove any quotes around the token
3. Regenerate token in Discord Developer Portal if needed

### Bot Won't Start - "dotenv not found"

**Symptoms:**
```
ModuleNotFoundError: No module named 'dotenv'
```

**Solution:**
```bash
pip install python-dotenv
```

### Bot Won't Start - Permission Errors

**Symptoms:**
```
PermissionError: [Errno 13] Permission denied: 'data/memory.db'
```

**Solutions:**
```bash
# Create directories with proper permissions
mkdir -p data logs/cognition
chmod 755 data logs

# On Windows, run PowerShell as Administrator
```

## Runtime Issues

### Bot Online But Doesn't Respond

**Symptoms:**
- Bot shows as online (green)
- Doesn't respond to @mentions
- No errors in terminal

**Causes & Solutions:**

1. **Missing Message Content Intent**
   - Go to Discord Developer Portal
   - Select your application → Bot
   - Enable "Message Content Intent"
   - Save changes
   - Restart bot

2. **Missing Permissions**
   - Check bot has "Read Messages" permission
   - Check bot has "Send Messages" permission
   - Check bot can see the channel

3. **Wrong Chat Mode**
   - Check AZURE_CHAT_MODE in .env
   - Set to `anyone` for testing
   - Restart bot

### Bot Responds Slowly

**Symptoms:**
- Bot takes 5-10+ seconds to respond
- Terminal shows no errors

**Causes & Solutions:**

1. **No LLM Configured (Fallback Mode)**
   - Expected behavior without LLM
   - Configure local LLM or API for faster responses

2. **LLM Model Too Large**
   - Use smaller model (3B instead of 7B)
   - Increase AZURE_N_THREADS

3. **Cognitive Pipeline Overhead**
   - Set AZURE_COGNITIVE_MODE=0 to disable
   - Reduces features but improves speed

### Database Errors

**Symptoms:**
```
sqlite3.OperationalError: unable to open database file
```

**Solutions:**
```bash
# Ensure data directory exists
mkdir -p data

# Check permissions
chmod 755 data

# Check disk space
df -h

# Try different path
# In .env: AZURE_MEMORY_BACKEND=memory
```

### Memory Issues / Bot Crashes

**Symptoms:**
- Bot crashes after running for hours
- "MemoryError" or "Out of memory"

**Solutions:**

1. **Reduce Memory Usage**
   ```env
   # In .env
   AZURE_MEMORY_BACKEND=memory  # Don't persist
   AZURE_COGNITIVE_MODE=0       # Disable heavy reasoning
   ```

2. **Monitor Resource Usage**
   ```bash
   # Check memory usage
   top  # Linux/Mac
   # Task Manager on Windows
   ```

3. **Restart Regularly**
   - Set up cron job or scheduled task to restart daily

## Configuration Issues

### Environment Variables Not Loading

**Symptoms:**
- Bot ignores .env file
- Uses default values

**Solutions:**

1. **Verify .env location**
   ```bash
   ls -la .env  # Should be in project root
   ```

2. **Check .env syntax**
   - No quotes around values (usually)
   - No spaces around =
   - Correct: `AZURE_DISCORD_TOKEN=abc123`
   - Wrong: `AZURE_DISCORD_TOKEN = "abc123"`

3. **Install python-dotenv**
   ```bash
   pip install python-dotenv
   ```

### Local LLM Not Loading

**Symptoms:**
```
[agent] WARNING: No LLM available
```

**Causes & Solutions:**

1. **Model File Not Found**
   - Check AZURE_MODEL_PATH points to actual file
   - Download model if missing
   - Verify file permissions

2. **Model Format Unsupported**
   - Only .gguf format supported
   - Convert PyTorch/SafeTensors to GGUF if needed

3. **Out of Memory**
   - Use quantized model (Q4, Q5)
   - Reduce model size (use 3B instead of 7B)

## Discord Integration Issues

### Bot Kicks Itself / Takes Wrong Actions

**Symptoms:**
- Bot performs actions on itself
- Deletes own messages

**Solution:**
```env
# In .env
AZURE_MODERATION_PHASE=dry_run  # Safe mode
AZURE_CONFIRMATION_MODE=all     # Ask before any action
```

### Bot Spams Messages

**Symptoms:**
- Bot sends multiple responses
- Creates message loops

**Causes:**
- Multiple bot instances running
- Bot responding to its own messages

**Solutions:**
1. Stop all instances: `pkill -f run_bot.py`
2. Start only one instance
3. Check logs for duplicate connections

## Performance Optimization

### Reduce Startup Time

```env
AZURE_COGNITIVE_MODE=0  # Skip pipeline init
AZURE_MEMORY_BACKEND=memory  # Skip DB init
```

### Reduce Response Time

```env
AZURE_N_THREADS=8  # More threads for LLM
AZURE_COGNITIVE_MODE=0  # Disable reasoning
```

### Reduce Memory Usage

```env
AZURE_MEMORY_BACKEND=memory  # No DB persistence
AZURE_COGNITIVE_MODE=0  # Disable heavy processing
```

## Diagnostic Commands

### Check Python Version
```bash
python --version
# Should be 3.11 or higher
```

### Check Dependencies
```bash
pip list | grep discord
# Should show discord.py
```

### Test Database Connection
```bash
python -c "from azure.database import DatabaseManager; db = DatabaseManager('test.db'); print('✓ DB works')"
```

### Test Agent Import
```bash
python -c "from azure.agent import AzureAgent; print('✓ Agent imports')"
```

### Check Logs
```bash
# Check latest logs
tail -f logs/cognition/*.log

# On Windows
Get-Content logs\cognition\*.log -Tail 50
```

## Getting More Help

If issue persists:

1. **Enable Debug Logging**
   ```env
   AZURE_LOG_LEVEL=DEBUG
   ```

2. **Check Logs**
   - Look in `logs/cognition/`
   - Check terminal output for errors

3. **Gather Information**
   - Python version: `python --version`
   - OS: Windows/Linux/Mac version
   - Error message (full text)
   - Steps to reproduce

4. **Ask for Help**
   - GitHub Issues: Include all info above
   - Discord Support: Share logs (remove tokens!)

## Common Error Messages Explained

| Error | Meaning | Solution |
|-------|---------|----------|
| `Improper token` | Wrong Discord token | Check .env file |
| `ModuleNotFoundError` | Missing dependency | Run `pip install -r requirements.txt` |
| `PermissionError` | File access denied | Check directory permissions |
| `ConnectionRefusedError` | Can't reach Discord | Check internet connection |
| `sqlite3.OperationalError` | Database issue | Check data/ directory exists |
| `MemoryError` | Out of RAM | Reduce model size or disable features |

## Still Stuck?

1. Re-read [Installation Guide](INSTALLATION.md)
2. Check [Configuration Guide](CONFIGURATION.md)
3. Try fresh installation in new directory
4. Ask in Discord support server
5. Open GitHub issue with details

---

**Most issues are solved by:**
1. Reinstalling dependencies
2. Checking .env configuration
3. Enabling Message Content Intent in Discord portal
