# Security Policy

Azure AI is **extremely beta and experimental**. Security reports are especially important because the project can interact with Discord permissions, moderation actions, external providers, stored user/server data, tools, and recovery mechanisms.

## Supported versions

The `main` branch is the primary development target. There are currently no separate long-term-support releases.

Because the project is experimental, security fixes may require configuration changes or architectural changes as the system evolves.

## Reporting a vulnerability

**Please do not disclose security vulnerabilities in public issues or pull requests.**

If GitHub Security Advisories are enabled for this repository, use the repository's private vulnerability reporting flow.

If private vulnerability reporting is unavailable, contact the repository maintainer privately through the GitHub profile associated with `azurephyr/Azure` and ask for a private security-reporting channel.

When reporting a vulnerability, include:

- A concise description of the issue
- The affected component or file, if known
- Reproduction steps or a minimal proof of concept
- The security impact
- Any suggested mitigation
- The affected version/commit

Never include live credentials, Discord bot tokens, API keys, passwords, private user data, guild data, or other secrets in the report.

## High-priority areas

Please report issues involving:

- Authentication or authorization bypasses
- Discord permission escalation
- Unsafe or unintended tool execution
- Arbitrary code or command execution
- Secret or credential exposure
- Cross-user or cross-server data leakage
- RAG or memory isolation failures
- Unsafe moderation actions
- WebSocket or dashboard authorization failures
- Recovery/self-repair actions escaping intended safety boundaries
- Path traversal or unsafe file access

## Disclosure

Please allow maintainers reasonable time to investigate and prepare a fix before publicly disclosing a vulnerability.

Do not use a security report to disclose credentials or private Discord information. If a credential has been exposed, revoke or rotate it immediately through the relevant provider and then report the exposure privately.
