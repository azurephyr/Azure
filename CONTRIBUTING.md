# Contributing to Azure

Thanks for helping build Azure.

## Before opening a PR

1. Read the README and existing documentation.
2. Keep changes focused and explain the motivation.
3. Add or update tests for behavior changes.
4. Never commit secrets, Discord tokens, API keys, or private user data.
5. Keep core logic independent from Discord and external AI providers where practical.

## Development

```bash
python -m venv .venv
pip install -e ".[dev]"
pytest
```

## Pull requests

A useful PR should explain what changed, why it changed, how it was tested, and any compatibility impact. Security-sensitive changes should be clearly identified in the PR description.
