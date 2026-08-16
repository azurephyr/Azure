# Azure AI

**An experimental, open-source Discord operating platform.**

> **Status: VERY EARLY / EXTREMELY BETA.** Azure AI is actively being developed and is currently unstable. Features may fail, APIs may change, and some systems are not production-ready.

Azure AI (also known as Azure / The Z / Adam-1 during development) is a modular Discord platform combining Discord operations, moderation, cognitive planning, multi-provider LLM inference, local-model support, memory/RAG, recovery systems, telemetry, and a web administration layer.

## What Azure is trying to become

Azure is intended to provide a single operating layer for Discord communities rather than a collection of unrelated commands. Its architecture separates the Discord interface from intelligence and web administration so the underlying systems can be tested and evolved independently.

```text
                    Discord
                       │
                       ▼
              ┌─────────────────┐
              │  Discord Layer  │
              │ bot/ + handlers │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ Intelligence    │
              │ azure/          │
              │                 │
              │ Agent / Cognition│
              │ Moderation      │
              │ Tools           │
              │ Memory / RAG    │
              │ LLM routing     │
              │ Recovery        │
              │ Telemetry       │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ Web / Dashboard │
              │ FastAPI + WS    │
              └─────────────────┘
```

## Current systems

The current development tree contains systems for:

- Discord gateway, commands, handlers, lifecycle and background tasks
- Agent orchestration and intent routing
- Cognitive planning and reasoning components
- Rule-based and AI-assisted moderation components
- Behavioral, temporal and risk analysis
- Moderation action execution and confirmation gates
- Multi-provider LLM failover and circuit breaking
- Local GGUF model support
- Memory and RAG systems
- Autonomous recovery and self-repair experiments
- Server health and configuration systems
- Audit and telemetry infrastructure
- Discord server/channel/role/member tooling
- FastAPI APIs, WebSockets and administration pages
- Voice and vision experiments

## Development reality

Azure is **not a finished product**. The repository is intended to document and develop the project openly while the architecture is stabilized.

Some features have extensive automated tests, while other areas still require live Discord, dashboard, provider, concurrency, and failure-path validation. A passing unit test does not mean that Azure is production-ready.

The maintainer's current RC1 snapshot reports more than 2,800 unit/integration assertions across the local test tree, alongside dedicated Discord scenario and end-to-end verification suites. These figures describe the development workspace and should not be interpreted as a guarantee of production reliability.

## Development

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
pytest
```

For local-model development, see `models/README.md` when present in the development tree.

## Security

**Never commit secrets.** Discord tokens, API keys, passwords, private server data, local databases, model weights, and other sensitive artifacts must remain outside the public repository.

Use `.env.example` as the template for local configuration. The real `.env` file is intentionally ignored.

See `SECURITY.md` for reporting guidance.

## Contributing

Azure is experimental, but contributions and technical feedback are welcome. See `CONTRIBUTING.md` before opening an issue or pull request.

## Roadmap

The current development priorities include:

1. Stabilize the RC1 implementation and reconcile the public repository with the real development tree.
2. Harden Discord command synchronization and application-command error handling.
3. Expand dashboard Case Management and Reputation interfaces.
4. Improve CI coverage for Discord scenario and end-to-end verification.
5. Continue hardening moderation, recovery, provider failover, and concurrency behavior.
6. Improve documentation and make safe parts of the system easier for contributors to run.

## License

MIT. See `LICENSE`.
