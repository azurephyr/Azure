# Azure AI — Development Status

> Snapshot date: August 2026
>
> **Azure AI is extremely beta and actively under development.** The project currently contains experimental systems that can fail in real Discord environments. This document intentionally distinguishes development evidence from production guarantees.

## Source of truth

The maintainer's local Azure AI workspace is currently the authoritative implementation. The public repository is being synchronized with that workspace incrementally.

The local development archive contains the real `azure/`, `bot/`, `web/`, `tests/`, configuration, scripts, and documentation trees. It also contains a local Git history whose latest recorded commit is the RC1 release-package commit `45e5939697f67eab12231112a46f7ce8589127fc`.

## Current shape

The development tree contains approximately 256 Python source files under `azure/`, `bot/`, `web/`, and `tests/`, plus supporting configuration, documentation, scripts, templates, and certification tooling.

The architecture is divided into:

1. **Discord layer (`bot/`)** — gateway, handlers, commands, lifecycle, background work, views and runtime configuration.
2. **Intelligence layer (`azure/`)** — agent orchestration, cognition, moderation, tools, memory/RAG, model routing, recovery, telemetry and integrations.
3. **Web layer (`web/`)** — FastAPI services, authentication, APIs, WebSockets and administration UI.

## Reported validation

The local development documentation/history reports:

- 2,800+ unit/integration assertions in the broader test tree.
- 15/15 Discord scenario simulations passing in the reported verification state.
- 88/88 cross-server moderation feature checks passing in the reported verification state.
- 183 Python files passing a syntax check in the July development snapshot.
- Hardcore stress, tool integration, server-building and moderation suites passing in the July 13 anchored summary.

These are **development/test results from the supplied workspace history**, not a claim that Azure is production-ready.

## Current local verification in this handoff

A clean Python `compileall` pass was performed against the supplied archive's `azure/`, `bot/`, `web/`, and `tests/` trees.

A full pytest run could not start in this environment because the installed runtime does not currently contain the `discord` Python package required by `tests/conftest.py`. No pytest result is being claimed from that failed startup.

## Known development priorities

- Synchronize application/slash commands during startup.
- Add a global application-command error handler.
- Continue dashboard Case Management and Reputation work.
- Put Discord scenario and E2E verification into CI.
- Continue live integration testing and concurrency/failover validation.

## Safety and repository hygiene

The local development archive contains a real `.env` file. It must **never** be copied into the public repository.

Also keep local databases, model weights, caches, logs containing private data, private server data, and generated runtime artifacts out of the public repository.

The public project should use `.env.example` and documented placeholders instead.

## Beta policy

Until the project is substantially stabilized:

- Do not describe Azure as production-ready.
- Do not claim that every feature in the local tree works.
- Do not convert historical test results into guarantees.
- Do not publish secrets or private operational data.
- Prefer reproducible tests and explicit limitations over impressive but unverifiable claims.
