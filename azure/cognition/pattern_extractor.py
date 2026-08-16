"""
PatternExtractor — Upgrade 6: Reflection Pattern Analysis

Analyzes stored reflections to find recurring patterns and generate
adaptive insights. Called periodically to learn from the reflection store.

Examples of patterns it finds:
  - "For messages with 'restructure' + 'server', heuristic underestimates complexity"
  - "For ADMIN mode + 'delete', risk is usually HIGH"
  - "Tool 'create_channel' fails when user doesn't specify type"

Usage:
    extractor = PatternExtractor(reflection_memory)
    insights = extractor.analyze()
    # insights is a list of human-readable strings + structured data
"""

from __future__ import annotations

from collections import Counter

from .reflection_memory import Reflection, ReflectionMemory


class PatternInsight:
    """A single extracted pattern insight."""
    def __init__(self, pattern_type: str, description: str, confidence: float,
                 affected_messages: int, recommendation: str):
        self.pattern_type = pattern_type
        self.description = description
        self.confidence = confidence
        self.affected_messages = affected_messages
        self.recommendation = recommendation

    def to_dict(self) -> dict:
        return {
            "pattern_type": self.pattern_type,
            "description": self.description,
            "confidence": self.confidence,
            "affected_messages": self.affected_messages,
            "recommendation": self.recommendation,
        }

    def __str__(self) -> str:
        return f"[{self.pattern_type}] {self.description} (conf={self.confidence:.2f}, n={self.affected_messages})"


class PatternExtractor:
    """
    Analyzes reflection memory to find recurring patterns.

    Called periodically (e.g., every 10 messages or on startup) to update
    the system's understanding of its own failure modes.
    """

    # Minimum reflections needed for a reliable pattern
    MIN_SAMPLE_SIZE = 3
    # Minimum confidence for a pattern to be actionable
    MIN_CONFIDENCE = 0.6

    def __init__(self, memory: ReflectionMemory | None = None):
        self.memory = memory if memory is not None else ReflectionMemory()

    def analyze(self) -> list[PatternInsight]:
        """
        Run full pattern analysis on all stored reflections.

        Returns:
            List of PatternInsight objects with actionable recommendations.
        """
        reflections = self.memory.get_all()
        if len(reflections) < self.MIN_SAMPLE_SIZE:
            return []

        insights = []
        insights.extend(self._analyze_intent_patterns(reflections))
        insights.extend(self._analyze_tool_patterns(reflections))
        insights.extend(self._analyze_risk_patterns(reflections))
        insights.extend(self._analyze_complexity_patterns(reflections))
        insights.extend(self._analyze_success_patterns(reflections))

        # Sort by confidence descending
        insights.sort(key=lambda i: i.confidence, reverse=True)
        return insights

    def get_adaptive_thresholds(self) -> dict:
        """
        Suggest adjusted thresholds based on pattern analysis.

        Returns:
            dict with suggested threshold changes.
        """
        reflections = self.memory.get_all()
        suggestions = {}

        # Count confidence miscalibrations
        confidence_refs = [r for r in reflections if r.category == "confidence_miscalibration"]
        if len(confidence_refs) >= self.MIN_SAMPLE_SIZE:
            overconfident = sum(1 for r in confidence_refs if r.context.get("heuristic_confidence", 0.5) > 0.85)
            underconfident = sum(1 for r in confidence_refs if r.context.get("heuristic_confidence", 0.5) < 0.60)
            total = len(confidence_refs)

            if overconfident / total > 0.5:
                suggestions["semantic_threshold"] = 0.70  # Lower threshold (more Qwen)
            elif underconfident / total > 0.5:
                suggestions["semantic_threshold"] = 0.80  # Higher threshold (less Qwen)

        # Count risky outputs for ADMIN mode
        risky_admin = [r for r in reflections
                       if r.category == "risky_output" and "ADMIN" in r.context.get("modes", [])]
        if len(risky_admin) >= self.MIN_SAMPLE_SIZE:
            suggestions["admin_confirmation_required"] = True

        return suggestions

    # -----------------------------------------------------------------------
    # Individual pattern analyzers
    # -----------------------------------------------------------------------

    def _analyze_intent_patterns(self, reflections: list[Reflection]) -> list[PatternInsight]:
        """Find patterns in intent misclassifications."""
        insights = []
        intent_refs = [r for r in reflections if r.category == "intent_misclassification"]

        if len(intent_refs) < self.MIN_SAMPLE_SIZE:
            return insights

        # Find common keywords in misclassified messages
        words = []
        for r in intent_refs:
            words.extend(w for w in r.message_pattern.lower().split() if len(w) > 4)

        if not words:
            return insights

        word_counts = Counter(words)
        common = word_counts.most_common(3)
        if common and common[0][1] >= 2:
            keywords = ", ".join(f"'{w}'" for w, c in common[:2])
            confidence = min(0.95, common[0][1] / len(intent_refs))
            insights.append(PatternInsight(
                pattern_type="intent_misclassification",
                description=f"Messages containing {keywords} often lead to intent misclassification",
                confidence=confidence,
                affected_messages=len(intent_refs),
                recommendation=f"For messages with {keywords}, use Qwen reasoning more aggressively",
            ))

        return insights

    def _analyze_tool_patterns(self, reflections: list[Reflection]) -> list[PatternInsight]:
        """Find patterns in tool mismatches."""
        insights = []
        tool_refs = [r for r in reflections if r.category == "tool_mismatch"]

        if len(tool_refs) < self.MIN_SAMPLE_SIZE:
            return insights

        # Find commonly failing tools
        tools = []
        for r in tool_refs:
            tools.extend(r.context.get("selected_tools", []))

        if not tools:
            return insights

        tool_counts = Counter(tools)
        common = tool_counts.most_common(2)
        if common and common[0][1] >= 2:
            tool_name = common[0][0]
            confidence = min(0.95, common[0][1] / len(tool_refs))
            insights.append(PatternInsight(
                pattern_type="tool_mismatch",
                description=f"Tool '{tool_name}' frequently mismatched ({common[0][1]} times)",
                confidence=confidence,
                affected_messages=len(tool_refs),
                recommendation=f"For '{tool_name}', validate arguments more carefully before calling",
            ))

        return insights

    def _analyze_risk_patterns(self, reflections: list[Reflection]) -> list[PatternInsight]:
        """Find patterns in risky outputs."""
        insights = []
        risky_refs = [r for r in reflections if r.category == "risky_output"]

        if len(risky_refs) < self.MIN_SAMPLE_SIZE:
            return insights

        # Find risk level patterns
        risk_levels = Counter(r.context.get("risk", "LOW") for r in risky_refs)
        high_risk = risk_levels.get("HIGH", 0) + risk_levels.get("CRITICAL", 0)
        total = len(risky_refs)

        if high_risk / total > 0.5:
            confidence = min(0.95, high_risk / total)
            insights.append(PatternInsight(
                pattern_type="risk_pattern",
                description=f"{high_risk}/{total} risky outputs involve HIGH or CRITICAL risk actions",
                confidence=confidence,
                affected_messages=len(risky_refs),
                recommendation="Require explicit confirmation for all HIGH/CRITICAL risk actions",
            ))

        return insights

    def _analyze_complexity_patterns(self, reflections: list[Reflection]) -> list[PatternInsight]:
        """Find patterns in complexity underestimation."""
        insights = []
        # Look at all reflections where complexity was HIGH or EXTREME but something went wrong
        complex_refs = [r for r in reflections
                        if r.context.get("complexity") in ("HIGH", "EXTREME")]

        if len(complex_refs) < self.MIN_SAMPLE_SIZE:
            return insights

        # Check if complexity was underestimated
        failed_complex = [r for r in complex_refs if r.category in ("plan_failure", "tool_mismatch")]
        if len(failed_complex) >= 2:
            confidence = min(0.95, len(failed_complex) / len(complex_refs))
            insights.append(PatternInsight(
                pattern_type="complexity_underestimation",
                description=f"High-complexity tasks fail {len(failed_complex)}/{len(complex_refs)} times",
                confidence=confidence,
                affected_messages=len(complex_refs),
                recommendation="Always use Qwen reasoning for HIGH/EXTREME complexity tasks",
            ))

        return insights

    def _analyze_success_patterns(self, reflections: list[Reflection]) -> list[PatternInsight]:
        """Find patterns in successful approaches."""
        insights = []
        success_refs = [r for r in reflections if r.category == "success_pattern"]

        if len(success_refs) < self.MIN_SAMPLE_SIZE:
            return insights

        # Find common tools in successful patterns
        tools = []
        for r in success_refs:
            tools.extend(r.context.get("tools", []))

        if not tools:
            return insights

        tool_counts = Counter(tools)
        common = tool_counts.most_common(2)
        if common and common[0][1] >= 2:
            tool_name = common[0][0]
            confidence = min(0.95, common[0][1] / len(success_refs))
            insights.append(PatternInsight(
                pattern_type="success_pattern",
                description=f"Tool '{tool_name}' works well in complex tasks ({common[0][1]} successes)",
                confidence=confidence,
                affected_messages=len(success_refs),
                recommendation=f"Prioritize '{tool_name}' for similar complex requests",
            ))

        return insights

    def get_summary(self) -> str:
        """Generate a human-readable summary of current patterns."""
        insights = self.analyze()
        if not insights:
            return "No reliable patterns detected yet. Need more data."

        lines = [f"Pattern Analysis: {len(insights)} insights from {len(self.memory)} reflections"]
        for i in insights[:5]:
            lines.append(f"  • {i}")
        return "\n".join(lines)
