# Repository Maintenance

This document describes the maintenance baseline for the public Azure AI repository.

## Quality gates

A healthy contribution should preserve:

1. Passing automated tests.
2. Successful Python compilation.
3. Dependency consistency (`pip check`).
4. No committed secrets or runtime data.
5. Explicit authorization and safety boundaries around privileged operations.
6. Documentation that matches the behavior actually verified.

## Release discipline

Azure AI is experimental. Do not label a commit, release, or feature production-ready without the corresponding live validation.

Historical RC1 documents are engineering records. Current status should be determined from the latest verification results, not historical reports.

## Secrets and runtime state

Keep credentials and runtime state outside Git:

- `.env`
- API keys and bot tokens
- databases and SQLite sidecars
- logs and message histories
- local model weights
- caches and generated runtime reports

Use `.env.example` for documented configuration names and safe placeholders.

## Changes to safety-sensitive systems

Changes affecting authentication, Discord permissions, moderation, tool execution, memory/RAG isolation, recovery, or command execution require both success-path and denial-path tests.

## Current known limitations

The project remains experimental. Live Discord behavior, real provider failure paths, production dashboard behavior, privileged-tool sandboxing, and complete recovery rollback remain areas for continued validation.
