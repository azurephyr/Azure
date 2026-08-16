"""
SchemaValidator — Upgrade 5.5: Strict Qwen Output Validation

Validates LLM JSON outputs against expected schemas.
Provides:
  - JSON schema validation (fields, types, enums)
  - Parse failure detection
  - Retry logic (up to N attempts with temperature backoff)
  - Heuristic fallback on total failure

Usage:
    validator = SchemaValidator(llm)
    result = validator.call_with_retry(
        messages=[...],
        schema=REASONER_SCHEMA,
        max_attempts=2,
        fallback_fn=lambda: ReasonerAnalysis(),
    )
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Schemas for Reasoner and Critic outputs
# ---------------------------------------------------------------------------

REASONER_SCHEMA = {
    "type": "object",
    "required": ["true_intent", "modes", "complexity", "risk", "confidence"],
    "properties": {
        "true_intent": {"type": "string", "minLength": 1},
        "hidden_goals": {"type": "array", "items": {"type": "string"}},
        "desired_outcome": {"type": "string"},
        "ambiguities": {"type": "array", "items": {"type": "string"}},
        "missing_info": {"type": "array", "items": {"type": "string"}},
        "scratchpad": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "modes": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "complexity": {"enum": ["LOW", "MEDIUM", "HIGH", "EXTREME"]},
        "thinking_depth": {"enum": ["FAST", "NORMAL", "DEEP", "MAXIMUM"]},
        "risk": {"enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
        "requires_confirmation": {"type": "boolean"},
        "confirmation_message": {"type": "string"},
        "tool_decision": {"enum": ["DIRECT", "SINGLE_TOOL", "MULTIPLE_TOOLS", "CLARIFICATION", "SKIP", "REJECT"]},
        "selected_tools": {"type": "array", "items": {"type": "string"}},
        "tool_args": {"type": "object"},
        "needs_plan": {"type": "boolean"},
        "plan_description": {"type": "string"},
        "response": {"type": "string"},
        "response_tone": {"type": "string"},
        "response_length": {"type": "string"},
        "reasoning_chain": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
}

CRITIC_SCHEMA = {
    "type": "object",
    "required": ["passed", "overall_assessment", "confidence"],
    "properties": {
        "passed": {"type": "boolean"},
        "overall_assessment": {"type": "string", "minLength": 1},
        "intent_challenge": {"type": "string"},
        "harm_assessment": {"type": "string"},
        "context_gaps": {"type": "string"},
        "safer_alternative": {"type": "string"},
        "assumptions_found": {"type": "array", "items": {"type": "string"}},
        "manipulation_detected": {"type": "boolean"},
        "proportionality": {"type": "string"},
        "concerns": {"type": "array", "items": {"type": "string"}},
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "requires_override": {"type": "boolean"},
        "safer_response": {"type": "string"},
        "reasoning_chain": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
}


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """Result of schema validation."""
    valid: bool
    errors: list[str]
    data: dict | None = None


# ---------------------------------------------------------------------------
# SchemaValidator class
# ---------------------------------------------------------------------------

class SchemaValidator:
    """
    Validates LLM JSON outputs against schemas with retry and fallback.
    """

    MAX_RETRIES = 2  # Total attempts = 1 + MAX_RETRIES
    DEFAULT_TEMPERATURE = 0.3
    RETRY_TEMPERATURE = 0.1  # Lower temp on retry for more structured output

    def __init__(self, llm=None):
        self.llm = llm

    def validate_json(self, raw: str, schema: dict) -> ValidationResult:
        """
        Validate a raw JSON string against a schema.

        Returns ValidationResult with parsed data if valid, or errors if invalid.
        """
        errors = []
        data = None

        # Step 1: Extract JSON from raw text
        extracted = self._extract_json(raw)
        if extracted is None:
            return ValidationResult(valid=False, errors=["No valid JSON object found in LLM output"], data=None)

        # Step 2: Parse JSON
        try:
            data = json.loads(extracted)
        except json.JSONDecodeError as e:
            errors.append(f"JSON parse error: {e}")
            return ValidationResult(valid=False, errors=errors, data=None)

        # Step 3: Validate required fields
        required = schema.get("required", [])
        missing = [f for f in required if f not in data]
        if missing:
            errors.append(f"Missing required fields: {missing}")

        # Step 4: Validate field types and enums
        properties = schema.get("properties", {})
        for field_name, field_spec in properties.items():
            if field_name not in data:
                continue  # Skip missing optional fields
            value = data[field_name]

            # Type check
            expected_type = field_spec.get("type")
            if expected_type and not self._check_type(value, expected_type):
                errors.append(f"Field '{field_name}' has wrong type: expected {expected_type}, got {type(value).__name__}")

            # Enum check
            enum_values = field_spec.get("enum")
            if enum_values and value not in enum_values:
                errors.append(f"Field '{field_name}' has invalid value: '{value}' not in {enum_values}")

            # String min length
            min_len = field_spec.get("minLength")
            if min_len and isinstance(value, str) and len(value) < min_len:
                errors.append(f"Field '{field_name}' too short: {len(value)} < {min_len}")

            # Number range
            if isinstance(value, (int, float)):
                minimum = field_spec.get("minimum")
                maximum = field_spec.get("maximum")
                if minimum is not None and value < minimum:
                    errors.append(f"Field '{field_name}' below minimum: {value} < {minimum}")
                if maximum is not None and value > maximum:
                    errors.append(f"Field '{field_name}' above maximum: {value} > {maximum}")

        valid = len(errors) == 0
        return ValidationResult(valid=valid, errors=errors, data=data if valid else None)

    def call_with_retry(
        self,
        messages: list[dict],
        schema: dict,
        max_tokens: int = 300,
        fallback_fn: Callable = None,
        max_attempts: int = None,
    ) -> tuple[dict | None, list[str]]:
        """
        Call LLM with validation, retry on failure, and fallback.

        Returns:
            (parsed_data, error_log)
        """
        max_attempts = max_attempts or (self.MAX_RETRIES + 1)
        error_log = []

        for attempt in range(max_attempts):
            temperature = self.DEFAULT_TEMPERATURE if attempt == 0 else self.RETRY_TEMPERATURE

            try:
                raw = self.llm.chat(messages, max_tokens=max_tokens, temperature=temperature)
            except Exception as e:
                error_log.append(f"Attempt {attempt + 1}: LLM error: {e}")
                continue

            result = self.validate_json(raw, schema)
            if result.valid:
                return result.data, error_log

            error_log.append(f"Attempt {attempt + 1}: Schema validation failed: {result.errors}")
            # On failure, add a system reminder for the next attempt (copy messages to avoid mutating caller's list)
            if attempt < max_attempts - 1:
                messages = list(messages) + [{
                    "role": "system",
                    "content": f"Your previous response was invalid: {result.errors}. Please fix the errors and respond with ONLY valid JSON."
                }]

        # All attempts failed — use fallback
        if fallback_fn:
            error_log.append(f"All {max_attempts} attempts failed. Using heuristic fallback.")
            return fallback_fn(), error_log

        return None, error_log

    @staticmethod
    def _extract_json(raw: str) -> str | None:
        """Extract the first valid JSON object from raw text."""
        raw = raw.strip()

        # Try markdown code block first
        code_block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
        if code_block:
            return code_block.group(1)

        # Try stack-based brace matching for all { positions
        start = -1
        while True:
            start = raw.find("{", start + 1)
            if start == -1:
                break
            depth = 0
            for i in range(start, len(raw)):
                if raw[i] == '{':
                    depth += 1
                elif raw[i] == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = raw[start:i + 1]
                        try:
                            json.loads(candidate)  # Validate
                            return candidate
                        except json.JSONDecodeError:
                            break

        return None

    @staticmethod
    def _check_type(value: Any, expected_type: str) -> bool:
        """Check if a value matches an expected JSON schema type."""
        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        expected = type_map.get(expected_type)
        if expected is None:
            return True
        if isinstance(expected, tuple):
            return isinstance(value, expected)
        return isinstance(value, expected)
