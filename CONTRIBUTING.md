# Contributing to Azure AI

Thank you for helping improve Azure AI. The project is **extremely beta and experimental**, so contributions that improve correctness, safety, tests, documentation, and operational reliability are especially valuable.

## Before you start

Please:

1. Read the [README](README.md).
2. Read [SECURITY.md](SECURITY.md) before reporting security issues.
3. Search existing issues and pull requests before opening a new one.
4. Never include tokens, API keys, passwords, private Discord data, or other secrets in issues, commits, or pull requests.

## Development setup

### Requirements

- Python 3.11+
- Git
- A virtual environment
- A Discord bot and test server only if you are working on live Discord behavior

Create and activate a virtual environment:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Install runtime and web dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-web.txt
```

Install development tooling:

```bash
python -m pip install -r requirements-dev.txt
```

Create a local configuration from the safe template:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Keep `.env` local and never commit it.

## Development workflow

Create a focused branch from `main`:

```bash
git switch main
git pull --ff-only
git switch -c feature/short-description
```

Keep changes focused. Avoid mixing unrelated refactors with bug fixes or features.

## Required checks

Before opening a pull request, run the checks relevant to your change.

### Compilation

```bash
python -m compileall azure bot web scripts
```

### Tests

```bash
pytest
```

For a focused change, also run its targeted tests first:

```bash
pytest tests/test_<area>.py
```

### Dependency consistency

```bash
python -m pip check
```

### Linting

```bash
ruff check .
```

### Formatting

```bash
ruff format --check .
```

If formatting is required and you intend to apply it:

```bash
ruff format .
```

Do not use a formatter to rewrite unrelated files in a focused pull request.

## Testing principles

New behavior should have tests. Prefer:

- Unit tests for isolated logic
- Integration tests for subsystem interactions
- Regression tests for fixed bugs
- Adversarial/edge-case tests for security-sensitive behavior
- Simulation tests when live Discord or provider access is inappropriate

Changes involving authorization, moderation, tool execution, memory isolation, recovery, or external providers should receive extra scrutiny.

## Pull requests

A good pull request should explain:

- What changed
- Why it changed
- How it was tested
- Any limitations or unverified behavior
- Any configuration or migration impact

Keep the title concise and action-oriented, for example:

```text
fix: prevent cross-server memory leakage
```

Do not claim that a feature is production-ready unless it has actually received the required live validation.

## Coding standards

Python code should follow the project configuration in `pyproject.toml`.

General expectations:

- Use type hints for public interfaces and important internal boundaries.
- Prefer small, testable functions over large orchestration blocks.
- Keep authorization and safety decisions explicit.
- Avoid hidden global state where practical.
- Preserve async behavior and cancellation semantics.
- Add docstrings to public classes/functions when the behavior is non-obvious.
- Use `snake_case` for functions/variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants.
- Keep security-sensitive behavior easy to audit.

## Documentation

Update documentation when behavior, configuration, APIs, setup, or operational expectations change.

In particular, update:

- `README.md` for user-facing changes
- `docs/CONFIGURATION.md` for configuration changes
- `docs/TROUBLESHOOTING.md` for operational fixes
- `docs/MODEL_SETUP.md` for model/backend changes
- `CHANGELOG.md` for notable user-facing changes

## Security-sensitive changes

Do not test destructive Discord actions against communities you do not control.

For changes involving tools, permissions, moderation, recovery, authentication, data isolation, or command execution, include explicit tests for denied/unauthorized paths as well as successful paths.

If you discover a vulnerability, follow [SECURITY.md](SECURITY.md) rather than opening a public issue.

## Review checklist

Before requesting review, confirm:

- [ ] Tests pass for the changed area.
- [ ] New behavior has regression coverage where appropriate.
- [ ] No secrets or private runtime data are included.
- [ ] Documentation is updated where necessary.
- [ ] The change does not silently weaken authorization or safety gates.
- [ ] The PR description states what was actually verified.
- [ ] Unverified live behavior is clearly identified.

Thank you for contributing to Azure AI.
