# Azure AI

**An experimental agentic Discord platform for community operations, moderation, memory, and tool use.**

> **Status: EXTREMELY BETA / EXPERIMENTAL**
>
> Azure AI is not production-ready. The project is actively being validated, and live Discord, external-provider, dashboard, privileged-tool, and recovery scenarios still require additional real-world testing.

## What is Azure AI?

Azure AI is a modular Discord AI system built around an agent, configurable LLM providers, moderation, cognition, memory/RAG, tools, telemetry, and a web dashboard.

The repository is intentionally broad and experimental. Features are independently gated where possible so individual subsystems can be developed and tested without pretending that the entire platform is production-hardened.

### Core areas

- **Agent orchestration** — coordinates conversation, tools, memory, moderation, and model providers.
- **LLM routing and failover** — supports API and local-model paths with routing, retries, and failure handling.
- **Cognition** — intent analysis, planning, risk assessment, review, reflection, and tool-decision components.
- **Moderation** — policy-driven moderation with phase gates, confirmation flows, case handling, and AI-assisted analysis.
- **Memory and RAG** — conversation memory, server knowledge, retrieval, and contextual adaptation.
- **Discord tools** — server, member, role, channel, planning, and other guarded operations.
- **Telemetry and audit** — runtime status, execution tracking, audit records, and dashboard integration.
- **Web dashboard** — experimental FastAPI APIs, authentication, settings, moderation, logs, and WebSocket telemetry.
- **Recovery and self-repair** — experimental recovery paths for diagnosing and responding to subsystem failures.

## Current verification

The current development tree has been locally reported as verified with:

- **3,049 pytest tests passing**
- **3,049 tests passing in a clean isolated environment**
- **5/5 certification suites passing**
- **10,000 stress validations with 0 failures**
- **Python compilation passing**
- **Clean dependency installation and `pip check` passing**
- **Targeted Ruff checks passing**
- **Full-codebase Ruff still has approximately 357 findings to resolve**

These results are development verification, not a guarantee of production reliability.

### What is not yet fully verified

- Live Discord login, reconnect behavior, and command synchronization
- Real provider/API behavior and provider failover under live network failures
- Production dashboard deployment and concurrent multi-user behavior
- Full privileged-tool sandbox validation
- Complete recovery rollback and active-work shutdown behavior
- A clean production deployment with real external services

## Requirements

- **Python 3.11+**
- Git
- A Discord application/bot for live Discord testing
- Provider credentials if using cloud inference
- A compatible local model if using local inference

The project can also be exercised through its automated test and simulation suites without connecting a live Discord account.

## Quick start

### 1. Clone

```bash
git clone https://github.com/azurephyr/Azure.git
cd Azure
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it on Windows:

```powershell
.venv\Scripts\Activate.ps1
```

Or on Linux/macOS:

```bash
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

For the experimental web dashboard:

```bash
python -m pip install -r requirements-web.txt
```

For development and testing:

```bash
python -m pip install -r requirements-dev.txt
```

### 4. Configure the environment

Copy the safe template:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Then configure only the credentials and settings required for the features you intend to use. **Never commit `.env`.**

### 5. Run Azure

```bash
python run_bot.py
```

Live Discord operation requires a valid bot token and the appropriate Discord application intents/permissions.

## Local models

Azure includes experimental local-LLM support. Local inference depends on the selected backend and model format; the repository does **not** ship model weights.

See [`docs/MODEL_SETUP.md`](docs/MODEL_SETUP.md) for the current setup guidance.

## Testing

Install development dependencies and run:

```bash
pytest
```

Useful checks:

```bash
python -m compileall azure bot web scripts
python -m pip check
ruff check .
ruff format --check .
```

The certification and simulation suites live under [`tests/`](tests/).

## Documentation

- [`docs/INSTALLATION.md`](docs/INSTALLATION.md) — installation and environment setup
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) — configuration reference
- [`docs/MODEL_SETUP.md`](docs/MODEL_SETUP.md) — local model setup
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) — troubleshooting
- [`docs/LIVE_STAGING_CERTIFICATION.md`](docs/LIVE_STAGING_CERTIFICATION.md) — guarded live validation
- [`docs/AGRE_GUIDE.md`](docs/AGRE_GUIDE.md) — recovery/self-repair guidance
- [`docs/RC1_KNOWN_LIMITATIONS.md`](docs/RC1_KNOWN_LIMITATIONS.md) — known limitations

Historical RC1 documents are retained for engineering history and should not be interpreted as a current production-readiness certification.

## Security

Please read [`SECURITY.md`](SECURITY.md) before reporting a security issue. Never publish bot tokens, API keys, passwords, private Discord data, or other secrets in issues or pull requests.

## Contributing

Contributions, bug reports, tests, and documentation improvements are welcome. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a pull request.

## License

Azure AI is released under the MIT License. See [`LICENSE`](LICENSE).
