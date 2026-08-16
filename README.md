# Azure AI — Autonomous Agentic Discord System

> **Development Status: EXTREMELY BETA / EXPERIMENTAL**  
> Azure AI is an experimental autonomous agentic framework for Discord. It is **NOT** production-ready. Live Discord, cloud provider, and web dashboard integrations remain under active testing and validation.

Azure AI is an advanced, autonomous AI agent architecture designed for Discord community management, cognitive tool use, contextual memory, and intelligent server workflows. It can run completely locally and offline using open-source models via `llama.cpp`, or connect to cloud LLM providers (OpenAI, Anthropic, Google Gemini, Groq, Mistral, OpenRouter, and NaraRouter).

---

## 🧪 Current Verification & Quality Metrics

The codebase has undergone extensive automated testing and stress testing:

- **3,049 pytest tests passing**
- **3,049 tests passing in clean isolated test environments**
- **5 / 5 certification suites passing**
- **10,000 stress validations executed with 0 failures**
- **Clean Python compilation passing**
- **Clean dependency installation & `pip check` passing**
- **Targeted Ruff linting passing** (approximately 357 pre-existing full-codebase Ruff findings remain in progress)

> ⚠️ **Notice**: Live Discord API connections, external provider uptime, and real-time dashboard socket validations are still limited and subject to ongoing validation.

---

## 🚀 Key Architectural Capabilities

- **100% Local Inference Support:** Run local GGUF models completely offline via `llama.cpp`.
- **Cognitive Reasoning Pipeline:** 10-phase modular reasoning pipeline including intent decomposition, risk assessment, tool tier routing, adversarial review, and structured reflection.
- **Hybrid Memory & RAG:** Contextual memory storage with SQLite backends, server knowledge indexing, and user interaction adaptation.
- **Autonomous & Ghost Moderation:** Granular moderation workflows with dry-run, reactive, and stealth modes, case management, and scam source tracing.
- **Cross-Server Reputation Network:** Opt-in federation for tracking bad actors and spam rings.
- **Web Dashboard & Health Telemetry:** FastAPI and WebSocket-powered administration panel with JWT authentication and real-time telemetry streaming.

---

## ⚡ Quick Start

### 1. Prerequisites
- Python 3.10+
- A Discord Bot Token ([Discord Developer Portal](https://discord.com/developers/applications))

### 2. Installation
Clone the repository and install dependencies:

```bash
git clone https://github.com/azurephyr/Azure.git
cd Azure
pip install -r requirements.txt
```

*(Optional: for Web Dashboard dependencies)*
```bash
pip install -r requirements-web.txt
```

### 3. Configuration
Copy the template configuration:

```bash
cp .env.example .env
```

Edit `.env` to configure your tokens and settings:
```env
AZURE_DISCORD_TOKEN=your_bot_token_here
```

### 4. Choose an Intelligence Engine

#### Option A: Cloud API (Fastest Setup)
Add your preferred provider key in `.env`:
```env
AZURE_LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-key-here
```
*(Supported providers: `openai`, `anthropic`, `google`, `groq`, `mistral`, `openrouter`, `nararouter`)*

#### Option B: Local Model (Offline & Private)
1. Install `llama-cpp-python`:
   ```bash
   pip install llama-cpp-python --prefer-binary
   ```
2. Download a GGUF model (e.g. Qwen2.5-7B-Instruct or Qwen2.5-3B-Instruct) into `models/`.
3. Set `AZURE_MODEL_PATH` in `.env`:
   ```env
   AZURE_MODEL_PATH=models/qwen2.5-7b-instruct-q4_k_m.gguf
   AZURE_N_THREADS=4
   ```

### 5. Running the Bot
```bash
python run_bot.py
```

---

## 📚 Documentation

Detailed documentation is available in the [`docs/`](docs/) directory:

- [Installation Guide](docs/INSTALLATION.md)
- [Configuration Reference](docs/CONFIGURATION.md)
- [Model Setup Guide](docs/MODEL_SETUP.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [AGRE Implementation](docs/AGRE_GUIDE.md)
- [RC1 Verification Report](docs/RC1_VERIFICATION_REPORT.md)
- [RC1 Known Limitations](docs/RC1_KNOWN_LIMITATIONS.md)

---

## 🤝 Contributing

Contributions are welcome! Please review [CONTRIBUTING.md](CONTRIBUTING.md) for development workflows, testing instructions, and coding standards.

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
