import logging

from .cognitive_state import CognitiveState

logger = logging.getLogger("azure.cognition.clarification_agent")

class ClarificationAgent:
    """
    Upgrade 10: Uncertainty to Clarification
    Intercepts low-confidence thoughts before execution or response generation,
    and asks the user a targeted clarifying question instead of hallucinating.
    """
    def __init__(self, llm=None):
        self.llm = llm

    def should_clarify(self, state: CognitiveState) -> bool:
        """Determine if clarification is needed."""
        if state.tool_decision.value == "CLARIFICATION":
            return True
        if state.overall_confidence < 0.65:
            return True
        return bool(state.missing_info and len(state.missing_info) > 0)
        return False

    def generate_clarification(self, state: CognitiveState) -> str:
        """Generate a smart clarifying question."""
        if not self.llm:
            return "I'm not quite sure I understand. Could you clarify what you mean?"

        prompt = (
            "You are Azure, an AI operator. You received a request but lack sufficient context or confidence to act.\n\n"
            f"User request: '{state.raw_message}'\n"
            f"Missing info identified: {state.missing_info}\n"
            f"Ambiguities identified: {state.ambiguities}\n\n"
            "Ask the user ONE direct, casual, and specific question to get the information you need. "
            "Do NOT explain why you are asking, just ask the question. Do NOT prefix with your name."
        )

        messages = [
            {"role": "system", "content": "You are a sharp, direct AI operator asking for clarification."},
            {"role": "user", "content": prompt}
        ]

        try:
            response = self.llm.chat(messages, max_tokens=100, temperature=0.5)
            return response.strip()
        except Exception as e:
            logger.error(f"[ClarificationAgent] Failed: {e}")
            return "I need a bit more info to do that safely. Could you clarify?"
