# Azure Configuration Guide

Complete reference for all configuration options.

## Configuration Methods

Azure supports configuration through:

1. **Environment Variables** (`.env` file) - Recommended
2. **Command-line arguments** - Not yet implemented in v1.0
3. **Config file** - Not yet implemented in v1.0

## Environment Variables Reference

### Required

#### DISCORD_TOKEN
- **Type:** String
- **Required:** Yes
- **Description:** Discord bot token from Developer Portal
- **Example:** `DISCORD_TOKEN=your-discord-token-here`
- **Security:** Never commit this to version control!

### LLM Configuration

#### AZURE_LLM_MODEL
- **Type:** String
- **Default:** `none`
- **Options:** `none`, `local`, `openai`, `anthropic`
- **Description:** Which LLM backend to use
  - `none`: Fallback mode (limited functionality)
  - `local`: Local LLM via llama.cpp
  - `openai`: Azure OpenAI or OpenAI API
  - `anthropic`: Anthropic Claude API
- **Example:** `AZURE_LLM_MODEL=local`

#### AZURE_LOCAL_LLM_PATH
- **Type:** Path
- **Default:** None
- **Description:** Path to .gguf model file for local LLM
- **Example:** `AZURE_LOCAL_LLM_PATH=models/qwen2.5-3b-instruct.gguf`
- **Note:** Only used if AZURE_LLM_MODEL=local

#### AZURE_LLM_THREADS
- **Type:** Integer
- **Default:** System default
- **Description:** Number of CPU threads for local LLM inference
- **Example:** `AZURE_LLM_THREADS=4`
- **Recommendation:** Set to number of physical cores

#### AZURE_LLM_TEMPERATURE
- **Type:** Float
- **Default:** `0.7`
- **Range:** 0.0 - 2.0
- **Description:** LLM sampling temperature
  - Lower = more deterministic
  - Higher = more creative
- **Example:** `AZURE_LLM_TEMPERATURE=0.8`

#### AZURE_LLM_MAX_TOKENS
- **Type:** Integer
- **Default:** `512`
- **Description:** Maximum tokens in LLM response
- **Example:** `AZURE_LLM_MAX_TOKENS=1024`

### Storage Paths

#### AZURE_RAG_PATH
- **Type:** Path
- **Default:** `rag_store.json`
- **Description:** Path to RAG (retrieval) knowledge base
- **Example:** `AZURE_RAG_PATH=data/rag.json`

#### AZURE_MEMORY_DB
- **Type:** Path
- **Default:** `data/memory.db`
- **Description:** SQLite database for conversation history
- **Example:** `AZURE_MEMORY_DB=/var/lib/azure/memory.db`

#### AZURE_HYBRID_RAG_DB
- **Type:** Path
- **Default:** `data/hybrid_rag.db`
- **Description:** SQLite database for hybrid RAG system
- **Example:** `AZURE_HYBRID_RAG_DB=data/rag.db`

#### AZURE_LOG_DIR
- **Type:** Path
- **Default:** `logs/cognition`
- **Description:** Directory for log files
- **Example:** `AZURE_LOG_DIR=/var/log/azure`

### Performance Tuning

#### AZURE_MEMORY_TURNS
- **Type:** Integer
- **Default:** `10`
- **Description:** Number of conversation turns to keep in memory
- **Example:** `AZURE_MEMORY_TURNS=20`
- **Impact:** Higher = more context, more memory usage

#### AZURE_RAG_MAX_DOCS
- **Type:** Integer
- **Default:** `1000`
- **Description:** Maximum documents in RAG store
- **Example:** `AZURE_RAG_MAX_DOCS=5000`

#### AZURE_RAG_K
- **Type:** Integer
- **Default:** `3`
- **Description:** Number of documents to retrieve from RAG
- **Example:** `AZURE_RAG_K=5`
- **Impact:** Higher = more context, slower retrieval

#### AZURE_DISCORD_DECISION_TIMEOUT
- **Type:** Integer (seconds)
- **Default:** `600` (10 minutes)
- **Description:** Timeout for Discord decision-making
- **Example:** `AZURE_DISCORD_DECISION_TIMEOUT=300`

#### AZURE_DISCORD_PLAN_TIMEOUT
- **Type:** Integer (seconds)
- **Default:** `600` (10 minutes)
- **Description:** Timeout for plan generation
- **Example:** `AZURE_DISCORD_PLAN_TIMEOUT=300`

### Search and Tools

#### AZURE_SEARCH_API_URL
- **Type:** URL
- **Default:** `https://en.wikipedia.org/w/api.php`
- **Description:** API endpoint for web search
- **Example:** `AZURE_SEARCH_API_URL=https://api.duckduckgo.com`

#### AZURE_SEARCH_MAX_RESULTS
- **Type:** Integer
- **Default:** `3`
- **Description:** Maximum search results to return
- **Example:** `AZURE_SEARCH_MAX_RESULTS=5`

#### AZURE_FETCH_MAX_CHARS
- **Type:** Integer
- **Default:** `3000`
- **Description:** Maximum characters to fetch from URLs
- **Example:** `AZURE_FETCH_MAX_CHARS=5000`

#### AZURE_WEB_TIMEOUT
- **Type:** Integer (seconds)
- **Default:** `15`
- **Description:** Timeout for web requests
- **Example:** `AZURE_WEB_TIMEOUT=30`

#### AZURE_SANDBOX_DIR
- **Type:** Path
- **Default:** `sandbox`
- **Description:** Directory for file operations sandbox
- **Example:** `AZURE_SANDBOX_DIR=/tmp/azure_sandbox`

## Configuration Profiles

### Development Profile

```env
DISCORD_TOKEN=your_dev_token
AZURE_LLM_MODEL=none
AZURE_MEMORY_TURNS=5
AZURE_RAG_K=2
AZURE_LOG_DIR=logs/dev
```

### Production Profile (No LLM)

```env
DISCORD_TOKEN=your_prod_token
AZURE_LLM_MODEL=none
AZURE_MEMORY_DB=/var/lib/azure/memory.db
AZURE_LOG_DIR=/var/log/azure
AZURE_MEMORY_TURNS=10
AZURE_RAG_K=3
```

### Production Profile (Local LLM)

```env
DISCORD_TOKEN=your_prod_token
AZURE_LLM_MODEL=local
AZURE_LOCAL_LLM_PATH=/models/qwen2.5-3b.gguf
AZURE_LLM_THREADS=8
AZURE_MEMORY_DB=/var/lib/azure/memory.db
AZURE_LOG_DIR=/var/log/azure
AZURE_MEMORY_TURNS=15
AZURE_RAG_K=5
AZURE_LLM_TEMPERATURE=0.7
AZURE_LLM_MAX_TOKENS=1024
```

### Production Profile (API LLM)

```env
DISCORD_TOKEN=your_prod_token
AZURE_LLM_MODEL=openai
OPENAI_API_KEY=your_api_key
AZURE_MEMORY_DB=/var/lib/azure/memory.db
AZURE_LOG_DIR=/var/log/azure
AZURE_LLM_TEMPERATURE=0.7
AZURE_LLM_MAX_TOKENS=512
```

## Validation

To verify your configuration:

```bash
# Check .env file exists
ls -la .env

# Test bot startup (Ctrl+C to stop)
python run_bot.py

# Check for configuration errors in output
```

## Security Best Practices

1. **Never commit .env to version control**
   - Add `.env` to `.gitignore`
   - Use `.env.example` for templates

2. **Protect your Discord token**
   - Regenerate if exposed
   - Use environment variables in production
   - Restrict file permissions: `chmod 600 .env`

3. **Separate dev/prod tokens**
   - Use different bots for development and production
   - Use different servers for testing

4. **API Keys**
   - Store in environment variables
   - Rotate regularly
   - Monitor usage

## Troubleshooting Configuration

### Bot doesn't start
- Check `.env` file exists in project root
- Check DISCORD_TOKEN is set and correct
- Check file has no syntax errors

### Bot starts but no functionality
- Check AZURE_LLM_MODEL is set correctly
- If using local LLM, verify model file exists at AZURE_LOCAL_LLM_PATH
- Check logs in AZURE_LOG_DIR

### Database errors
- Check data/ directory exists
- Check write permissions
- Check disk space

### Performance issues
- Reduce AZURE_MEMORY_TURNS if memory constrained
- Reduce AZURE_RAG_K if queries are slow
- Increase AZURE_LLM_THREADS if CPU has capacity

---

**Configuration should take 5 minutes once you have your Discord token.**
