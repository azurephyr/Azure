"""
Azure Chain-of-Thought Visualization

Generates Discord embeds showing the real-time reasoning process.
Displays each cognitive step with confidence scores and decision rationale.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class ThinkingStep:
    """A single step in the thinking process."""
    phase: str
    description: str
    confidence: float
    duration_ms: int = 0
    status: str = "pending"  # pending, running, complete, error
    details: str = ""


class ThinkingVisualizer:
    """
    Real-time thinking process visualization for Discord.

    Usage:
        viz = ThinkingVisualizer()
        viz.start_phase("UNDERSTAND", "Analyzing user intent")
        viz.complete_phase("UNDERSTAND", confidence=0.92, details="Intent: coding question")
        embed = viz.build_embed()
    """

    PHASE_ORDER = [
        "UNDERSTAND", "ANALYZE", "CLASSIFY", "COMPLEXITY",
        "THINKING_DEPTH", "RISK", "TOOL_DECISION", "PLAN", "EXECUTE", "REVIEW"
    ]

    PHASE_ICONS = {
        "UNDERSTAND": "🧠",
        "ANALYZE": "🔍",
        "CLASSIFY": "🏷️",
        "COMPLEXITY": "📊",
        "THINKING_DEPTH": "🤔",
        "RISK": "⚠️",
        "TOOL_DECISION": "🔧",
        "PLAN": "📋",
        "EXECUTE": "⚡",
        "REVIEW": "✅",
    }

    def __init__(self):
        self.steps: dict[str, ThinkingStep] = {}
        self.current_phase: str | None = None
        self.start_time: float = 0.0

    def start(self):
        """Start the thinking process."""
        self.start_time = time.time()
        for phase in self.PHASE_ORDER:
            self.steps[phase] = ThinkingStep(
                phase=phase,
                description=self._phase_description(phase),
                confidence=0.0,
                status="pending",
            )

    def start_phase(self, phase: str, description: str = ""):
        """Mark a phase as running."""
        self.current_phase = phase
        if phase in self.steps:
            self.steps[phase].status = "running"
            if description:
                self.steps[phase].description = description

    def complete_phase(self, phase: str, confidence: float = 0.0, details: str = "", duration_ms: int = 0):
        """Mark a phase as complete."""
        if phase in self.steps:
            self.steps[phase].status = "complete"
            self.steps[phase].confidence = confidence
            self.steps[phase].details = details
            self.steps[phase].duration_ms = duration_ms

    def error_phase(self, phase: str, error: str):
        """Mark a phase as errored."""
        if phase in self.steps:
            self.steps[phase].status = "error"
            self.steps[phase].details = error

    def build_embed(self) -> dict:
        """Build a Discord embed showing the thinking process."""
        lines = []
        for phase in self.PHASE_ORDER:
            step = self.steps.get(phase)
            if not step:
                continue
            icon = self.PHASE_ICONS.get(phase, "•")
            if step.status == "pending":
                lines.append(f"⬜ {icon} **{phase}** — *Pending*")
            elif step.status == "running":
                lines.append(f"🔄 {icon} **{phase}** — {step.description}")
            elif step.status == "complete":
                conf_bar = self._confidence_bar(step.confidence)
                lines.append(f"✅ {icon} **{phase}** — {step.details or step.description} {conf_bar}")
            elif step.status == "error":
                lines.append(f"❌ {icon} **{phase}** — {step.details}")

        total_time = int((time.time() - self.start_time) * 1000) if self.start_time else 0

        return {
            "title": "🧠 Cognitive Process",
            "description": "\n".join(lines) or "Thinking...",
            "color": 0x3498db,
            "footer": {"text": f"Total: {total_time}ms"},
        }

    def build_text(self) -> str:
        """Build a plain text representation of the thinking process."""
        lines = []
        for phase in self.PHASE_ORDER:
            step = self.steps.get(phase)
            if not step or step.status == "pending":
                continue
            icon = self.PHASE_ICONS.get(phase, "•")
            if step.status == "complete":
                lines.append(f"{icon} {phase}: {step.details or step.description} ({step.confidence:.0%})")
            elif step.status == "running":
                lines.append(f"{icon} {phase}: ...")
            elif step.status == "error":
                lines.append(f"{icon} {phase}: Error — {step.details}")
        return "\n".join(lines)

    def _phase_description(self, phase: str) -> str:
        """Default description for each phase."""
        descriptions = {
            "UNDERSTAND": "Extracting user intent",
            "ANALYZE": "Analyzing context and constraints",
            "CLASSIFY": "Classifying query mode",
            "COMPLEXITY": "Assessing complexity",
            "THINKING_DEPTH": "Determining thinking depth",
            "RISK": "Evaluating risk",
            "TOOL_DECISION": "Selecting tools",
            "PLAN": "Creating execution plan",
            "EXECUTE": "Executing tools",
            "REVIEW": "Reviewing and responding",
        }
        return descriptions.get(phase, phase)

    def _confidence_bar(self, confidence: float) -> str:
        """Generate a visual confidence bar."""
        filled = int(confidence * 5)
        bar = "█" * filled + "░" * (5 - filled)
        return f"[{bar}] {confidence:.0%}"
