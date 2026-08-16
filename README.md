# Azure

**Open-source Discord operations, moderation, and agent framework.**

Azure is being built as a modular control layer for Discord communities. The goal is to keep the core deterministic and testable while allowing Discord adapters, moderation engines, dashboards, and optional AI providers to plug into the same event-driven core.

> Status: early public development. APIs may change.

## Design goals

- **Core-first:** business logic lives in Azure Core rather than inside a Discord client.
- **Auditable:** important state changes produce structured audit events.
- **Safe by default:** permissions are explicit and destructive operations require authorization.
- **Provider-neutral:** AI/LLM integrations are optional adapters, not a hard dependency.
- **Testable:** moderation and policy decisions can run without Discord or network access.
- **Extensible:** Discord, dashboard, API, and future clients should share one source of truth.

## Architecture

```text
Discord adapter ─┐
Dashboard/API ───┼──> Azure Core ──> Policy Engine
Future clients ──┘         │              │
                           ├──────────────┘
                           ├── Event Bus
                           ├── Audit Log
                           └── Provider adapters (optional)
```

Azure Core owns domain decisions. Clients translate external input into core events and render the results; they should not duplicate business rules.

## Current foundation

- Typed configuration and environment loading
- Event model and in-process event bus
- Structured audit records
- Deterministic moderation rule engine
- Permission/policy primitives
- Discord adapter boundary
- Pytest test suite
- CI for supported Python versions

## Development

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Roadmap

1. Harden the core event and policy APIs.
2. Build the Discord gateway/command adapter.
3. Add persistent audit storage.
4. Add moderation pipelines for spam, scams, raids, and toxicity.
5. Add a secure dashboard/API on top of Azure Core.
6. Add optional AI provider adapters with explicit policy gates.
7. Build contributor tooling, documentation, and production deployment guides.

## Security

Do not put Discord tokens, API keys, or other secrets in source control. See `SECURITY.md` for reporting guidance.

## License

MIT. See `LICENSE`.