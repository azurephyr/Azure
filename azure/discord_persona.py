"""
Azure Discord Persona & Prompt Engineering

System prompts and conversation formatting optimized for Discord context.
The persona shapes how the model behaves in a server environment.
"""

from __future__ import annotations

# Import operator persona
from .operator_persona import MODERATION_EXPLAINER_PERSONA, OPERATOR_PERSONA, OWNER_PERSONA

# Backward-compatible aliases
DEFAULT_PERSONA = OPERATOR_PERSONA

# ---------------------------------------------------------------------------
# Conversation Formatter
# ---------------------------------------------------------------------------

class ConversationFormatter:
    """Formats Discord conversation history into model-ready messages."""

    def __init__(self, system_prompt: str = None, llm=None,
                 max_history_turns: int = 10, max_context_tokens: int = 3000):
        self.llm = llm
        self.system_prompt = system_prompt or DEFAULT_PERSONA
        self.max_history_turns = max_history_turns
        self.max_context_tokens = max_context_tokens

    def format(self, history: list[dict[str, str]], user_name: str,
               current_message: str, server_name: str = "Discord") -> list[dict[str, str]]:
        """
        Format conversation history into chat messages.

        Args:
            history: List of {"role": "user"|"assistant", "content": str, "name": str}
            user_name: Name of the current user
            current_message: The message they just sent
            server_name: Name of the Discord server

        Returns:
            List of {"role": "system"|"user"|"assistant", "content": str}
        """
        # Keep the voice deterministic. Generating a new persona per message
        # wastes quota and causes tone drift across conversation turns.
        system = self.system_prompt
        if server_name:
            system += f"\n\nCURRENT SERVER: {server_name}"
        system += f"\n\nCURRENT USER: {user_name}"
        system += "\n\nCRITICAL: NEVER output multiple response options. Output only ONE response."

        messages = [{"role": "system", "content": system}]

        # Add recent history (trimmed to max turns)
        recent = history[-self.max_history_turns * 2:] if len(history) > self.max_history_turns * 2 else history
        for msg in recent:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            name = msg.get("name", "User")
            # Prepend username for context (models understand this pattern)
            if role == "user" and name:
                content = f"{name} says: {content}"
            messages.append({"role": role, "content": content})

        # Add current message (no name prefix - LLM already knows from system prompt)
        messages.append({"role": "user", "content": current_message})

        return messages

    def format_for_moderation_explanation(self, decision: dict, user_name: str) -> list[dict[str, str]]:
        """Format a request for Azure to explain a moderation decision."""
        return [
            {"role": "system", "content": MODERATION_EXPLAINER_PERSONA},
            {"role": "user", "content": f"[{user_name}] Why did you take action against me?"},
            {"role": "assistant", "content": self._build_explanation(decision)},
        ]

    def _build_explanation(self, decision: dict) -> str:
        """Build a factual explanation from decision data."""
        decision.get("action", "unknown")
        reason = decision.get("reason", "No reason recorded.")
        risk = decision.get("risk_score", 0)
        confidence = decision.get("confidence", 0)

        return (
            f"I took action because: {reason}\n"
            f"Risk score: {risk:.0%} | Confidence: {confidence:.0%}"
        )

    def set_owner_mode(self):
        """Switch to owner persona (more technical, transparent)."""
        self.system_prompt = OWNER_PERSONA

    def set_moderation_explanation_mode(self):
        """Switch to moderation explainer persona."""
        self.system_prompt = MODERATION_EXPLAINER_PERSONA

    def reset(self):
        """Reset to default (operator) persona."""
        self.system_prompt = DEFAULT_PERSONA
