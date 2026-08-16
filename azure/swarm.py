"""
Azure Multi-Agent Swarm System

Specialized agents that collaborate and debate before responding.
- CodeAgent: coding, debugging, technical explanations
- CreativeAgent: creative writing, brainstorming, art prompts
- ResearchAgent: web search, fact-checking, summaries
- SocialAgent: social interactions, jokes, casual chat
- ModeratorAgent: moderation, conflict resolution

The SwarmCoordinator routes messages to appropriate agents and manages consensus.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("azure.swarm")


@dataclass
class SwarmResponse:
    """Response from a single agent in the swarm."""
    agent_name: str
    text: str
    confidence: float
    specialty: str


@dataclass
class SwarmConsensus:
    """Final consensus from the swarm."""
    text: str
    confidence: float
    contributing_agents: list[str] = field(default_factory=list)
    debate_notes: list[str] = field(default_factory=list)


class BaseSwarmAgent:
    """Base class for swarm agents."""

    def __init__(self, name: str, specialty: str, llm=None):
        self.name = name
        self.specialty = specialty
        self.llm = llm
        self._invocations = 0

    def can_handle(self, message: str) -> float:
        """Return confidence (0-1) that this agent can handle the message."""
        return 0.0

    def respond(self, message: str, context: dict = None) -> SwarmResponse:
        """Generate a response."""
        self._invocations += 1
        return SwarmResponse(
            agent_name=self.name,
            text="",
            confidence=0.0,
            specialty=self.specialty,
        )


class CodeAgent(BaseSwarmAgent):
    """Handles coding, debugging, and technical questions."""

    KEYWORDS = ["code", "python", "javascript", "bug", "error", "debug", "fix", "function", "class", "api", "database", "sql", "git", "deploy", "docker"]

    def __init__(self, llm=None):
        super().__init__("CodeAgent", "coding", llm)

    def can_handle(self, message: str) -> float:
        msg = message.lower()
        matches = sum(1 for k in self.KEYWORDS if k in msg)
        return min(1.0, matches * 0.15)

    def respond(self, message: str, context: dict = None) -> SwarmResponse:
        confidence = self.can_handle(message)
        if self.llm and confidence > 0.3:
            try:
                prompt = f"You are an expert programmer. Answer concisely with code examples where helpful.\n\nUser: {message}"
                raw = self.llm.chat([{"role": "user", "content": prompt}], max_tokens=400, temperature=0.3)
                return SwarmResponse(self.name, raw.strip(), confidence, self.specialty)
            except Exception as e:
                logger.warning("CodeAgent respond error: %s", e)
        return SwarmResponse(self.name, "", confidence, self.specialty)


class CreativeAgent(BaseSwarmAgent):
    """Handles creative writing, brainstorming, and art."""

    KEYWORDS = ["write", "story", "poem", "creative", "brainstorm", "idea", "design", "art", "draw", "imagine", "fantasy", "fiction"]

    def __init__(self, llm=None):
        super().__init__("CreativeAgent", "creative", llm)

    def can_handle(self, message: str) -> float:
        msg = message.lower()
        matches = sum(1 for k in self.KEYWORDS if k in msg)
        return min(1.0, matches * 0.15)

    def respond(self, message: str, context: dict = None) -> SwarmResponse:
        confidence = self.can_handle(message)
        if self.llm and confidence > 0.3:
            try:
                prompt = f"You are a creative writer and artist. Be imaginative and inspiring.\n\nUser: {message}"
                raw = self.llm.chat([{"role": "user", "content": prompt}], max_tokens=400, temperature=0.9)
                return SwarmResponse(self.name, raw.strip(), confidence, self.specialty)
            except Exception as e:
                logger.warning("CreativeAgent respond error: %s", e)
        return SwarmResponse(self.name, "", confidence, self.specialty)


class ResearchAgent(BaseSwarmAgent):
    """Handles research, facts, and information gathering."""

    KEYWORDS = ["search", "find", "what is", "who is", "when did", "how does", "why is", "explain", "research", "learn about", "tell me about"]

    def __init__(self, llm=None):
        super().__init__("ResearchAgent", "research", llm)

    def can_handle(self, message: str) -> float:
        msg = message.lower()
        if "?" in msg:
            return 0.6
        matches = sum(1 for k in self.KEYWORDS if k in msg)
        return min(1.0, matches * 0.15 + 0.2)

    def respond(self, message: str, context: dict = None) -> SwarmResponse:
        confidence = self.can_handle(message)
        if self.llm and confidence > 0.3:
            try:
                prompt = f"You are a research expert. Provide accurate, well-sourced information.\n\nUser: {message}"
                raw = self.llm.chat([{"role": "user", "content": prompt}], max_tokens=400, temperature=0.4)
                return SwarmResponse(self.name, raw.strip(), confidence, self.specialty)
            except Exception as e:
                logger.warning("ResearchAgent respond error: %s", e)
        return SwarmResponse(self.name, "", confidence, self.specialty)


class SocialAgent(BaseSwarmAgent):
    """Handles social interactions, jokes, and casual chat."""

    KEYWORDS = ["joke", "funny", "lol", "hi", "hello", "hey", "how are you", "friend", "chat", "talk", "bored", "happy", "sad"]

    def __init__(self, llm=None):
        super().__init__("SocialAgent", "social", llm)

    def can_handle(self, message: str) -> float:
        msg = message.lower()
        if len(msg) < 30 and "?" not in msg:
            return 0.8
        matches = sum(1 for k in self.KEYWORDS if k in msg)
        return min(1.0, matches * 0.15 + 0.3)

    def respond(self, message: str, context: dict = None) -> SwarmResponse:
        confidence = self.can_handle(message)
        if self.llm and confidence > 0.3:
            try:
                prompt = f"You are a friendly, witty conversationalist. Be warm and engaging.\n\nUser: {message}"
                raw = self.llm.chat([{"role": "user", "content": prompt}], max_tokens=200, temperature=0.8)
                return SwarmResponse(self.name, raw.strip(), confidence, self.specialty)
            except Exception as e:
                logger.warning("SocialAgent respond error: %s", e)
        return SwarmResponse(self.name, "", confidence, self.specialty)


class ModeratorAgent(BaseSwarmAgent):
    """Handles moderation, conflict resolution, and rule enforcement."""

    KEYWORDS = ["rule", "moderate", "ban", "kick", "warn", "report", "toxic", "spam", "inappropriate", "violation"]

    def __init__(self, llm=None):
        super().__init__("ModeratorAgent", "moderation", llm)

    def can_handle(self, message: str) -> float:
        msg = message.lower()
        matches = sum(1 for k in self.KEYWORDS if k in msg)
        return min(1.0, matches * 0.2)

    def respond(self, message: str, context: dict = None) -> SwarmResponse:
        confidence = self.can_handle(message)
        if self.llm and confidence > 0.3:
            try:
                prompt = f"You are a fair and calm moderator. Enforce rules while being respectful.\n\nUser: {message}"
                raw = self.llm.chat([{"role": "user", "content": prompt}], max_tokens=300, temperature=0.4)
                return SwarmResponse(self.name, raw.strip(), confidence, self.specialty)
            except Exception as e:
                logger.warning("ModeratorAgent respond error: %s", e)
        return SwarmResponse(self.name, "", confidence, self.specialty)


class SwarmCoordinator:
    """
    Coordinates multiple specialized agents.

    Usage:
        swarm = SwarmCoordinator(llm)
        consensus = swarm.process("How do I fix this Python bug?")
    """

    def __init__(self, llm=None):
        self.llm = llm
        self.agents: list[BaseSwarmAgent] = [
            CodeAgent(llm),
            CreativeAgent(llm),
            ResearchAgent(llm),
            SocialAgent(llm),
            ModeratorAgent(llm),
        ]
        self._history: list[dict] = []

    def process(self, message: str, context: dict = None) -> SwarmConsensus:
        """Process a message through the swarm and return consensus."""
        context = context or {}

        # 1. Route to all agents
        responses = []
        for agent in self.agents:
            try:
                resp = agent.respond(message, context)
                if resp.confidence > 0.2 and resp.text:
                    responses.append(resp)
            except Exception as e:
                logger.warning("Swarm agent %s error: %s", agent.name, e)

        if not responses:
            return SwarmConsensus(
                text="I'm not sure how to help with that. Could you rephrase?",
                confidence=0.0,
                contributing_agents=[],
            )

        # 2. Sort by confidence
        responses.sort(key=lambda r: r.confidence, reverse=True)

        # 3. If top agent is confident enough, use it directly
        if responses[0].confidence >= 0.8:
            return SwarmConsensus(
                text=responses[0].text,
                confidence=responses[0].confidence,
                contributing_agents=[responses[0].agent_name],
            )

        # 4. Otherwise, debate and synthesize
        return self._debate_and_synthesize(responses, message)

    def _debate_and_synthesize(self, responses: list[SwarmResponse], original_message: str) -> SwarmConsensus:
        """Have agents debate and synthesize a consensus response."""
        top_agents = responses[:3]

        # Build debate prompt
        debate_lines = [f"Specialist responses to: '{original_message}'"]
        for r in top_agents:
            debate_lines.append(f"\n[{r.agent_name} - confidence {r.confidence:.0%}]:")
            debate_lines.append(r.text[:300])

        debate_prompt = "\n".join(debate_lines) + "\n\nSynthesize the best answer from these specialists, combining their insights. Be concise and accurate."

        if self.llm:
            try:
                raw = self.llm.chat([{"role": "user", "content": debate_prompt}], max_tokens=400, temperature=0.5)
                consensus_text = raw.strip()
            except Exception:
                consensus_text = top_agents[0].text
        else:
            consensus_text = top_agents[0].text

        avg_confidence = sum(r.confidence for r in top_agents) / len(top_agents)

        return SwarmConsensus(
            text=consensus_text,
            confidence=avg_confidence,
            contributing_agents=[r.agent_name for r in top_agents],
            debate_notes=[f"{r.agent_name}: confidence {r.confidence:.0%}" for r in top_agents],
        )

    def get_stats(self) -> dict[str, Any]:
        """Return swarm statistics."""
        return {
            "agents": {a.name: {"invocations": a._invocations, "specialty": a.specialty} for a in self.agents},
        }
