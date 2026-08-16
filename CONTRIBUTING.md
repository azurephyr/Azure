# Contributing to Azure Discord Bot

Thank you for your interest in contributing to Azure! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How Can I Contribute?](#how-can-i-contribute)
- [Development Setup](#development-setup)
- [Pull Request Process](#pull-request-process)
- [Coding Standards](#coding-standards)
- [Testing Guidelines](#testing-guidelines)

---

## Code of Conduct

### Our Pledge

We are committed to providing a welcoming and inclusive experience for everyone. We expect all contributors to:

- Use welcoming and inclusive language
- Be respectful of differing viewpoints and experiences
- Gracefully accept constructive criticism
- Focus on what is best for the community
- Show empathy towards other community members

### Unacceptable Behavior

- Harassment, trolling, or derogatory comments
- Publishing others' private information
- Any conduct that could reasonably be considered inappropriate

---

## How Can I Contribute?

### Reporting Bugs

Before creating a bug report, please:

1. **Search existing issues** to avoid duplicates
2. **Check the troubleshooting guide** (docs/TROUBLESHOOTING.md)
3. **Verify you're on the latest version**

When creating a bug report, include:

- **Clear title** describing the issue
- **Steps to reproduce** the behavior
- **Expected vs actual behavior**
- **Environment details** (OS, Python version, etc.)
- **Error messages** (full text, not screenshots if possible)
- **Relevant configuration** (remove tokens!)

### Suggesting Features

Feature suggestions are welcome! Please:

1. **Search existing feature requests** first
2. **Explain the use case** - why is this feature valuable?
3. **Describe the solution** you'd like
4. **Consider alternatives** - are there other ways to solve this?
5. **Be open to discussion** - features may evolve

### Contributing Code

We welcome pull requests for:

- Bug fixes
- Feature implementations
- Documentation improvements
- Test coverage improvements
- Performance optimizations

---

## Development Setup

### Prerequisites

- Python 3.11 or higher
- Git
- Discord bot token (for testing)
- Virtual environment tool (venv, conda, etc.)

### Setup Steps

```bash
# 1. Fork and clone
git clone https://github.com/YOUR_USERNAME/azure-discord-bot.git
cd azure-discord-bot

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows

# 3. Install dependencies
pip install -r requirements.txt
pip install -r requirements-web.txt

# 4. Install development tools (optional)
pip install pytest pytest-asyncio pytest-cov black ruff mypy

# 5. Configure for development
cp .env.example .env
# Edit .env with your test bot token

# 5. Run tests
pytest

# 6. Create a branch
git checkout -b feature/your-feature-name
```

---

## Pull Request Process

### Before Submitting

1. **Test your changes**
   ```bash
   pytest
   ```

2. **Format your code**
   ```bash
   black azure/
   ```

3. **Check for issues**
   ```bash
   flake8 azure/
   ```

4. **Update documentation** if needed
   - Update README.md for user-facing changes
   - Update docstrings for API changes
   - Update CHANGELOG.md (unreleased section)

5. **Commit with clear messages**
   ```
   Add spam detection fallback for empty messages
   
   - Check for None/empty message content before processing
   - Add tests for edge cases
   - Update docs/TROUBLESHOOTING.md with new edge case
   ```

### Submitting

1. **Push to your fork**
   ```bash
   git push origin feature/your-feature-name
   ```

2. **Open a Pull Request** on GitHub

3. **Fill out the PR template**
   - What does this PR do?
   - Why is this change needed?
   - How has it been tested?
   - Related issues?

4. **Respond to review feedback**
   - Be open to suggestions
   - Make requested changes
   - Ask questions if unclear

### After Submission

- Automated tests will run (GitHub Actions)
- Maintainers will review your PR
- You may be asked to make changes
- Once approved, maintainers will merge

---

## Coding Standards

### Python Style

We follow **PEP 8** with some modifications:

- Line length: 100 characters (not 79)
- Use **type hints** for function signatures
- Use **docstrings** for public functions/classes
- Prefer **explicit over implicit**

### Example

```python
from typing import Optional

def classify_message(
    content: str,
    user_id: str,
    threshold: float = 0.75
) -> Optional[str]:
    """
    Classify a message as spam, toxic, or clean.
    
    Args:
        content: The message text to classify
        user_id: Discord user ID for context
        threshold: Confidence threshold (0.0-1.0)
    
    Returns:
        Classification label or None if below threshold
    
    Raises:
        ValueError: If threshold is out of range
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"Threshold must be 0.0-1.0, got {threshold}")
    
    # Implementation...
    return "clean"
```

### Code Organization

- **One class per file** (unless closely related)
- **Logical file names** (e.g., `spam_detector.py`, not `utils.py`)
- **Group related functions** into modules
- **Avoid circular imports**

### Naming Conventions

- **Variables/functions**: `snake_case`
- **Classes**: `PascalCase`
- **Constants**: `UPPER_SNAKE_CASE`
- **Private methods**: `_leading_underscore`

---

## Testing Guidelines

### Test Requirements

All new code should include tests:

- **Unit tests** for individual functions
- **Integration tests** for system interactions
- **Edge case tests** for error conditions

### Writing Tests

```python
import pytest
from azure.ai_moderation.spam_ai import SpamAI

def test_spam_detection_obvious_spam():
    """Test that obvious spam is detected"""
    detector = SpamAI()
    result = detector.classify("BUY NOW! CLICK HERE! LIMITED OFFER!")
    
    assert result.is_spam is True
    assert result.confidence > 0.8

def test_spam_detection_normal_message():
    """Test that normal messages are not flagged"""
    detector = SpamAI()
    result = detector.classify("Hey, how's it going?")
    
    assert result.is_spam is False

@pytest.mark.parametrize("message,expected", [
    ("", False),  # Empty message
    (None, False),  # None message
    ("a" * 10000, True),  # Very long message
])
def test_spam_detection_edge_cases(message, expected):
    """Test edge cases"""
    detector = SpamAI()
    result = detector.classify(message)
    assert result.is_spam == expected
```

### Running Tests

```bash
# All tests
pytest

# Specific file
pytest tests/test_spam_ai.py

# Specific test
pytest tests/test_spam_ai.py::test_spam_detection_obvious_spam

# With coverage
pytest --cov=azure --cov-report=html

# Watch mode (re-run on changes)
pytest-watch
```

---

## Documentation

### Docstrings

Use **Google-style docstrings**:

```python
def process_message(content: str, context: dict) -> dict:
    """
    Process a message through the cognitive pipeline.
    
    Args:
        content: The message text to process
        context: Dictionary containing conversation context
    
    Returns:
        Dictionary with processing results including:
            - response: Generated response text
            - confidence: Confidence score (0.0-1.0)
            - reasoning: Explanation of decision
    
    Raises:
        ValueError: If content is empty
        LLMError: If LLM processing fails
    
    Example:
        >>> result = process_message("Hello", {"user_id": "123"})
        >>> print(result["response"])
        'Hi! How can I help you today?'
    """
```

### README Updates

When adding user-facing features:

1. Update main README.md
2. Update relevant docs/ files
3. Add examples if helpful
4. Update .env.example if adding config

---

## Questions?

- **GitHub Discussions** for questions
- **GitHub Issues** for bugs/features
- **Discord Server** (if available)

---

## Recognition

Contributors are recognized in:

- CHANGELOG.md (for significant contributions)
- GitHub contributors page
- Release notes (for major features)

Thank you for contributing to Azure!
