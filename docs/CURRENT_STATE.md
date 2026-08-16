# Azure AI RC1 — Current State

> This document records the current local RC1 state supplied by the maintainer on August 2026. The local project is the source of truth until its source tree is imported into this repository.

## Identity

Azure AI (also called The Z / Azure Platform / Adam-1) is a multi-layer autonomous Discord operating platform. It combines Discord operations, moderation, cognitive planning, multi-provider LLM inference, local GGUF inference, RAG, memory, recovery, telemetry, and a FastAPI/WebSocket administration dashboard.

## Architecture

### Layer 1 — Discord

`bot/` owns the Discord gateway, event handlers, commands, lifecycle, background loops, rate limiting, interactive views, and runtime configuration.

### Layer 2 — Intelligence

`azure/` owns the AzureAgent orchestrator, intent routing, cognitive pipeline, moderation, risk analysis, tools, memory/RAG, model routing, resilience, telemetry, and integrations.

### Layer 3 — Web

`web/` contains the FastAPI dashboard, JWT/bcrypt authentication, REST APIs, WebSockets, moderation/configuration APIs, and the BotDataBridge.

## Message pipeline

1. Discord message arrives.
2. Attention and chat-mode checks run.
3. Input security validation runs.
4. Rate limits and cooldowns are checked.
5. Moderation scanner, behavioral analysis, temporal analysis, and risk scoring run.
6. Moderation phase clamps the available action set; destructive actions may enter human confirmation.
7. Intent classification selects chat, plan, tool, moderation, or health-check routing.
8. Direct chat uses the AzureAgent and provider failover chain.
9. Complex plans use tool-chain planning, adversarial review, confirmation, and execution.
10. Telemetry and interaction history are persisted.

## Implemented feature families

- Scam DM tracing
- Cross-server reputation
- Unified moderation cases
- Configuration export/import with secret redaction
- Ghost/invisible moderation
- Dead-chat revival
- Multi-provider LLM failover and circuit breaker
- 10-phase cognitive pipeline
- Human-in-the-loop destructive-action confirmation
- Local and hybrid RAG
- FastAPI/WebSocket dashboard
- Autonomous recovery engine
- Server health and self-repair systems
- Discord server architecture/templates
- Voice and vision systems

## Moderation safety model

The moderation system uses three escalation phases:

- `dry_run`: classify, score, and log only
- `reactive_limited`: limited non-destructive actions
- `reactive_full`: full action set

Risk combines content severity/confidence with behavioral and temporal/situational factors. Destructive actions are confirmation-gated under the configured confirmation policy.

## Current validation

- 38 test files in the local RC1 tree
- 2,800+ unit/integration assertions reported passing
- 15/15 Discord scenario simulations passing
- End-to-end master verification passing
- 88/88 cross-server moderation feature tests passing

## Critical invariants

Do not remove the SQLite `_execute_with_retry` protection. Do not replace the reputation database `RLock` with a normal `Lock`. Do not remove `base_content_risk` from message risk calculation. Never hardcode credentials or secrets.

## Immediate engineering tasks

1. Synchronize slash commands with `bot.tree.sync()` during startup/ready lifecycle.
2. Add a global `on_app_command_error` handler for graceful slash-command failures.
3. Add dashboard pages for Case Management and Reputation.
4. Add CI coverage for the Discord scenario and E2E verification runners.

## Important boundary

This repository initially contained a small public foundation created before the full RC1 source tree was available. The local RC1 source tree remains authoritative. Future source imports should preserve the architecture and invariants above rather than replacing the real implementation with a simplified rewrite.
