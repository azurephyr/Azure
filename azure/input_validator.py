"""
Input Validation and Sanitization Layer for Azure

Protects against common injection attacks and malicious inputs:
- SQL injection
- Command injection
- Path traversal
- XXE (XML External Entity)
- Prompt injection
- XSS (Cross-Site Scripting) in Discord embeds
- Suspicious patterns and anomalies

All user inputs should pass through this layer before processing.
"""

from __future__ import annotations

import contextlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import unquote_plus

logger = logging.getLogger(__name__)


class ThreatLevel(StrEnum):
    """Severity level of detected threat.

    Order is intentional: higher rank = more severe. Do not use built-in
    `max()` on members — str Enum compares lexicographically and would
    rank SUSPICIOUS > CRITICAL (bug: CRITICAL downgraded to SUSPICIOUS).
    """
    SAFE = "SAFE"
    SUSPICIOUS = "SUSPICIOUS"
    DANGEROUS = "DANGEROUS"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _THREAT_RANK[self]

    def elevate(self, other: ThreatLevel) -> ThreatLevel:
        """Return the more severe of self and other."""
        return other if other.rank > self.rank else self


_THREAT_RANK = {
    ThreatLevel.SAFE: 0,
    ThreatLevel.SUSPICIOUS: 1,
    ThreatLevel.DANGEROUS: 2,
    ThreatLevel.CRITICAL: 3,
}


@dataclass
class ValidationResult:
    """Result of input validation."""
    is_valid: bool
    threat_level: ThreatLevel
    sanitized_input: str
    violations: list[str]
    blocked_patterns: list[str]

    @property
    def is_safe(self) -> bool:
        """Returns True when input is allowed through.

        SAFE and SUSPICIOUS inputs both return True because SUSPICIOUS
        inputs may be legitimate (e.g. "how do I hack my own router?").
        Use `is_blocked` instead to catch SUSPICIOUS inputs that have
        concrete violations - those should be blocked at the boundary.
        """
        return self.threat_level in (ThreatLevel.SAFE, ThreatLevel.SUSPICIOUS)

    @property
    def is_blocked(self) -> bool:
        """Hard-block decision for security gates.

        Any DANGEROUS/CRITICAL threat level, OR a SUSPICIOUS input with
        detected violations, blocks the input. This matches the documented
        behavior on InputValidator: "if not result.is_safe: ... block".
        """
        if self.threat_level in (ThreatLevel.DANGEROUS, ThreatLevel.CRITICAL):
            return True
        return bool(self.violations)


class InputValidator:
    """
    Validates and sanitizes user inputs against common attack vectors.

    Usage:
        validator = InputValidator()
        result = validator.validate(user_message)

        if not result.is_safe:
            logger.warning(f"Blocked malicious input: {result.violations}")
            return "Your input contains suspicious patterns."

        # Use sanitized input
        process(result.sanitized_input)
    """

    # SQL injection patterns
    SQL_INJECTION_PATTERNS = [
        r"(?i)(union\s+select|select\s+.*\s+from|drop\s+table|delete\s+from|insert\s+into)",
        # Comment-obfuscated DDL (e.g. DROP/**/TABLE) after whitespace normalization
        r"(?i)(drop\s+table|delete\s+from|insert\s+into|union\s+select)",
        r"(?i)(exec\s*\(|execute\s+immediate|call\s+procedure)",
        r"--\s*$",  # SQL comment at end
        r"(?i)(or\s+1\s*=\s*1|and\s+1\s*=\s*1)",
        r"(?i)(or\s+'?1'?\s*=\s*'?1'|and\s+'?1'?\s*=\s*'?1')",  # tautology variants
        r"(?i)\b1\s+or\s+1\s*=\s*1\b",
        r"';--",  # Comment-based injection
    ]

    # Zero-width / bidi control chars used to evade regex gates
    _ZW_RE = re.compile(
        r"[\u200b\u200c\u200d\u2060\ufeff\u202a-\u202e\u2066-\u2069]"
    )
    # SQL block comments used for keyword obfuscation
    _SQL_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

    # Command injection patterns
    # Prefer operator+command or path-anchored forms to avoid FP on chat like
    # "use bash to explain" / "please curl up" / "rm -rf is dangerous".
    _SHELL_CMDS = (
        r"rm|wget|curl|nc|netcat|bash|sh|chmod|chown|shutdown|ls|cat|uname|"
        r"printenv|env|id|whoami|python|perl|php|powershell|cmd"
    )
    COMMAND_INJECTION_PATTERNS = [
        r"(?i)(\$\(|\`|<\(|>\()",  # Subshell operators
        r"(?i)(;\s*(?:rm|wget|curl|nc|netcat|bash|sh|chmod|chown|shutdown|printenv|env|ls|cat|uname)\b)",
        r"(?i)(&&\s*(?:rm|wget|curl|nc|netcat|bash|sh|chmod|chown|ls|cat|uname|printenv|env)\b)",
        r"(?i)(\|\s*(?:nc|netcat|bash|sh|grep)\b)",
        r"(?i)\brm\s+-rf\s+[/\\~.]",
        r"(?i)\b(?:wget|curl)\s+https?://",
        r"(?i)\b(?:nc|netcat)\s+\S+\s+\d+",
        r"(?i)(/bin/(?:ba)?sh\b)",
        r"(?i)(\beval\s*\(|\bexec\s*\(|\bsystem\s*\(|\bpassthru\s*\()",
    ]

    # Path traversal patterns (strict - only match actual file system attacks)
    PATH_TRAVERSAL_PATTERNS = [
        r"\.\./.*\.(php|asp|jsp|exe|sh|bat)",  # Directory traversal with dangerous file
        r"(?:^|/)\.\.(?:/|\\)",  # Actual traversal at word boundaries only
        r"(?i)/etc/(?:passwd|shadow|hosts)",  # Unix system files
        r"(?i)/windows/system32",  # Windows system files
        r"(?i)/root/\.ssh",
        r"(?i)(?:^|[\\/])(?:Users|home)[\\/].*(?:auth|secret|id_rsa|\.env)",
        r"(?i)/usr/local/etc/.*\.(?:toml|conf|key)",
    ]

    # XXE patterns
    XXE_PATTERNS = [
        r"(?i)<!DOCTYPE",
        r"(?i)<!ENTITY",
        r"(?i)SYSTEM\s+['\"]",
    ]

    # Prompt injection patterns (AI-specific)
    PROMPT_INJECTION_PATTERNS = [
        r"(?i)(ignore\s+(previous|all|above)|disregard|forget)",
        r"(?i)(new\s+instructions?|system\s+(prompt|message))",
        r"(?i)(you\s+are\s+now|act\s+as\s+if|pretend\s+to\s+be)",
        r"(?i)(reveal\s+(your|the)\s+(prompt|instructions|system))",
        r"(?i)(\[SYSTEM\]|\[INST\]|\[/INST\]|<\|im_start\|>|<\|im_end\|>)",
    ]

    # XSS patterns (for Discord embeds)
    XSS_PATTERNS = [
        r"<script[^>]*>",
        r"javascript:",
        r"on\w+\s*=",  # Event handlers (onclick, onerror, etc.)
        r"<iframe",
        r"<(?:object|embed|form|style)\b",
        r"\bformaction\s*=",
    ]

    # Extra prompt-injection paraphrases (jailbreak / roleplay / secret exfil)
    PROMPT_INJECTION_EXTRA = [
        r"(?i)\bjailbreak\b",
        r"(?i)\bdeveloper\s+mode\b",
        r"(?i)\bout\s+of\s+character\b",
        r"(?i)(do\s+not\s+follow\s+the\s+rules|drop\s+the\s+safety)",
        r"(?i)((?:reveal|show|print|output|tell\s+me).{0,40}(?:prompt|instructions|api\s*keys?|configuration|secrets?|environment\s+variables|configured))",
        r"(?i)(system\s*role|starting\s+prompt|previous\s+\d+\s+instructions)",
        r"(?i)(i\s+am\s+the\s+developer|as\s+the\s+developer)",
        r"(?i)ignore\s+the\s+developer",
        r"(?i)(very\s+first\s+lines|azure\s+really\s+says|rogue\s+agi)",
        r"(?i)system\s*:\s*.{0,40}override",
    ]

    # Suspicious repetition patterns (DDoS/spam)
    REPETITION_THRESHOLD = 5  # Same character repeated N times

    # Maximum input length
    MAX_INPUT_LENGTH = 4000  # Discord's message limit is 2000, but allow some buffer

    def __init__(self, strict_mode: bool = False):
        """
        Args:
            strict_mode: If True, be more aggressive with blocking
        """
        self.strict_mode = strict_mode
        self._validation_count = 0
        self._blocked_count = 0

    def validate(self, user_input: str, input_type: str = "message") -> ValidationResult:
        """
        Validate and sanitize user input.

        Args:
            user_input: The raw user input to validate
            input_type: Type of input ("message", "command", "parameter")

        Returns:
            ValidationResult with sanitized input and threat assessment
        """
        self._validation_count += 1
        violations = []
        blocked_patterns = []
        threat_level = ThreatLevel.SAFE

        # Type gate: non-strings must not be treated as safe empty input.
        # Callers sometimes pass None / dict / int; accepting them as SAFE
        # silently skips every security pattern.
        if user_input is None:
            return ValidationResult(
                is_valid=True,
                threat_level=ThreatLevel.SAFE,
                sanitized_input="",
                violations=[],
                blocked_patterns=[],
            )
        if not isinstance(user_input, str):
            return ValidationResult(
                is_valid=False,
                threat_level=ThreatLevel.DANGEROUS,
                sanitized_input="",
                violations=[f"Non-string input rejected (type={type(user_input).__name__})"],
                blocked_patterns=[],
            )
        if not user_input:
            return ValidationResult(
                is_valid=True,
                threat_level=ThreatLevel.SAFE,
                sanitized_input="",
                violations=[],
                blocked_patterns=[],
            )

        # Length check (pre-normalization)
        if len(user_input) > self.MAX_INPUT_LENGTH:
            violations.append(
                f"Input exceeds maximum length ({len(user_input)} > {self.MAX_INPUT_LENGTH})"
            )
            threat_level = ThreatLevel.SUSPICIOUS
            user_input = user_input[: self.MAX_INPUT_LENGTH]

        # Canonical form for pattern matching only. Preserves original for
        # sanitization output except control-char stripping in _sanitize.
        scan_input = self._normalize_for_scan(user_input)

        # Check for SQL injection
        for pattern in self.SQL_INJECTION_PATTERNS:
            if re.search(pattern, scan_input):
                violations.append("SQL injection pattern detected")
                blocked_patterns.append(pattern)
                threat_level = threat_level.elevate(ThreatLevel.CRITICAL)

        # Check for command injection
        for pattern in self.COMMAND_INJECTION_PATTERNS:
            if re.search(pattern, scan_input):
                violations.append("Command injection pattern detected")
                blocked_patterns.append(pattern)
                threat_level = threat_level.elevate(ThreatLevel.CRITICAL)

        # Check for path traversal
        for pattern in self.PATH_TRAVERSAL_PATTERNS:
            if re.search(pattern, scan_input):
                violations.append("Path traversal pattern detected")
                blocked_patterns.append(pattern)
                threat_level = threat_level.elevate(ThreatLevel.DANGEROUS)

        # Check for XXE
        for pattern in self.XXE_PATTERNS:
            if re.search(pattern, scan_input):
                violations.append("XXE pattern detected")
                blocked_patterns.append(pattern)
                threat_level = threat_level.elevate(ThreatLevel.DANGEROUS)

        # Check for prompt injection (AI-specific threat)
        # Use digit-leet form so "Ign0re" matches without breaking SQL scanners.
        prompt_scan = self._normalize_for_prompt_scan(user_input)
        for pattern in self.PROMPT_INJECTION_PATTERNS + self.PROMPT_INJECTION_EXTRA:
            if re.search(pattern, prompt_scan):
                violations.append("Prompt injection pattern detected")
                blocked_patterns.append(pattern)
                if self.strict_mode:
                    threat_level = threat_level.elevate(ThreatLevel.DANGEROUS)
                else:
                    # Prompt injection is less critical than code injection
                    threat_level = threat_level.elevate(ThreatLevel.SUSPICIOUS)

        # Check for XSS (in Discord embeds)
        for pattern in self.XSS_PATTERNS:
            if re.search(pattern, scan_input, re.IGNORECASE):
                violations.append("XSS pattern detected")
                blocked_patterns.append(pattern)
                threat_level = threat_level.elevate(ThreatLevel.DANGEROUS)

        # Check for suspicious repetition (spam/DDoS)
        if self._check_repetition(scan_input):
            violations.append("Suspicious repetition pattern detected")
            threat_level = threat_level.elevate(ThreatLevel.SUSPICIOUS)

        # Check for null bytes (can break parsers)
        if "\x00" in user_input:
            violations.append("Null byte detected")
            threat_level = ThreatLevel.DANGEROUS
            user_input = user_input.replace("\x00", "")

        # Sanitize the input
        sanitized = self._sanitize(user_input)

        # Determine if valid
        is_valid = threat_level in (ThreatLevel.SAFE, ThreatLevel.SUSPICIOUS)

        if not is_valid:
            self._blocked_count += 1
            logger.warning(
                f"[security] Blocked malicious input (threat={threat_level.value}): "
                f"violations={violations}, patterns={len(blocked_patterns)}"
            )

        return ValidationResult(
            is_valid=is_valid,
            threat_level=threat_level,
            sanitized_input=sanitized,
            violations=violations,
            blocked_patterns=blocked_patterns,
        )

    # Cyrillic / symbol lookalikes (safe for all scanners — no digit remaps)
    _HOMOGLYPH_MAP = str.maketrans({
        "@": "a",
        "\u0456": "i",  # Cyrillic small і
        "\u0406": "i",  # Cyrillic capital І
        "\u043e": "o",  # Cyrillic о
        "\u041e": "o",
        "\u0430": "a",  # Cyrillic а
        "\u0410": "a",
        "\u0435": "e",  # Cyrillic е
        "\u0415": "e",
        "\u0440": "p",  # Cyrillic р
        "\u0441": "c",  # Cyrillic с
    })
    # Digit leetspeak only for prompt-injection scan (must NOT run on SQL
    # scanners — "1 OR 1=1" would become "i OR i=i" and evade detection).
    _LEET_DIGIT_MAP = str.maketrans({
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
    })

    def _normalize_for_scan(self, text: str) -> str:
        """Canonicalize text before pattern matching.

        - NFKC collapses compatibility homoglyphs
        - Strip zero-width / bidi controls used to split keywords
        - One-pass URL-decode (Union percent-20 Select)
        - Strip SQL block comments (DROP/**/TABLE)
        - Map Cyrillic lookalikes to Latin (not digits - see prompt path)
        """
        normalized = unicodedata.normalize("NFKC", text)
        normalized = self._ZW_RE.sub("", normalized)
        with contextlib.suppress(Exception):
            normalized = unquote_plus(normalized)
        normalized = self._SQL_COMMENT_RE.sub(" ", normalized)
        normalized = normalized.translate(self._HOMOGLYPH_MAP)
        # Collapse runs of whitespace so "ignore  previous" still matches
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized

    # Digits embedded in words only (Ign0re → Ignore), not free-standing numbers
    _LEET_IN_WORD_RE = re.compile(r"(?<=[A-Za-z])[01345](?=[A-Za-z])")

    def _normalize_for_prompt_scan(self, text: str) -> str:
        """Prompt-injection scan form: base normalize + in-word digit leet."""
        base = self._normalize_for_scan(text)

        def _sub(m: re.Match) -> str:
            return m.group(0).translate(self._LEET_DIGIT_MAP)

        return self._LEET_IN_WORD_RE.sub(_sub, base)

    def _check_repetition(self, text: str) -> bool:
        """Check for suspicious character repetition."""
        if len(text) < 10:
            return False

        # Check for same character repeated many times
        for char in set(text):
            count = text.count(char)
            if count > len(text) * 0.5 and count > 20:
                return True

        # Check for repeated patterns
        if len(text) > 50:
            # Simple pattern detection: check if first 10 chars repeat
            pattern = text[:10]
            if text.count(pattern) > 3:
                return True

        return False

    def _sanitize(self, text: str) -> str:
        """
        Sanitize input by removing dangerous characters.

        This is a light sanitization - we don't want to destroy legitimate
        input, but we do want to remove clearly dangerous stuff.
        """
        # Remove null bytes
        text = text.replace('\x00', '')

        # Remove other control characters (except newline, tab, carriage return)
        text = ''.join(char for char in text if ord(char) >= 32 or char in '\n\t\r')

        # Discord markdown sanitization (optional - only if strict)
        if self.strict_mode:
            # Escape Discord markdown to prevent abuse
            text = text.replace('`', '\\`')
            text = text.replace('*', '\\*')
            text = text.replace('_', '\\_')

        return text

    def stats(self) -> dict:
        """Get validation statistics."""
        block_rate = self._blocked_count / self._validation_count if self._validation_count > 0 else 0.0
        return {
            "total_validations": self._validation_count,
            "blocked_inputs": self._blocked_count,
            "block_rate": block_rate,
            "strict_mode": self.strict_mode,
        }


# Global validator instance
_global_validator: InputValidator | None = None


def get_validator(strict_mode: bool = False) -> InputValidator:
    """Get or create the global validator instance."""
    global _global_validator
    if _global_validator is None:
        _global_validator = InputValidator(strict_mode=strict_mode)
    return _global_validator


def validate_input(user_input: str, input_type: str = "message") -> ValidationResult:
    """
    Convenience function to validate input using the global validator.

    Usage:
        result = validate_input(message.content)
        if not result.is_safe:
            await message.reply("Your input contains suspicious patterns.")
            return
    """
    validator = get_validator()
    return validator.validate(user_input, input_type)
