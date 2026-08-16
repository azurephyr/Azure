"""
AI Moderation Engine - Schema v1 Compatible

Main orchestration engine that coordinates all AI moderation systems with:
- Parallel execution (async gather)
- PolicyEngine integration for decisions
- Schema v1 throughout (MessageAnalysis, PolicyDecision)
- Comprehensive metrics and monitoring

MIGRATION NOTE: Integrated with PolicyEngine v1.0.
- Uses MessageAnalysis from toxicity/spam/scam/raid AI
- Uses PolicyEngine.decide() for all decisions
- NO legacy logic (_analyze_context_safety, _toxicity_decision removed)
- Preserves behavioral compatibility for safe contexts
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .models import (
    Intensity,
    JoinEvent,
    MessageAnalysis,
    PolicyAction,
    PolicyDecision,
    RaidAnalysis,
    ScamAnalysis,
    SpamAnalysis,
)
from .policy_engine import (
    MODERATE_POLICY,
    PolicyEngine,
    ServerConfig,
)
from .raid_ai import RaidAI
from .scam_ai import ScamAI
from .spam_ai import SpamAI
from .toxicity_ai import ToxicityAI

logger = logging.getLogger("azure.ai_moderation.engine")


@dataclass
class ModerationResult:
    """Complete moderation analysis result with PolicyEngine decision."""
    # PolicyEngine decision
    policy_decision: PolicyDecision | None = None

    # Individual analyses (Optional because we run them in parallel)
    toxicity: MessageAnalysis | None = None
    spam: SpamAnalysis | None = None
    scam: ScamAnalysis | None = None
    raid: RaidAnalysis | None = None

    # Combined reasoning
    primary_violation: str = "none"  # Which AI detected the issue
    reasoning: str = ""
    evidence: list[str] = field(default_factory=list)

    # Flags
    flagged_for_review: bool = False
    analysis_errors: list[str] = field(default_factory=list)

    # Metadata
    analyzed_at: float = field(default_factory=time.time)
    processing_time_ms: float = 0.0
    checks_run: list[str] = field(default_factory=list)

    # Convenience properties for backward compatibility
    @property
    def decision(self) -> str:
        """Get action as string (backward compat)."""
        if self.policy_decision:
            return self.policy_decision.action.value
        return "safe"

    @property
    def confidence(self) -> float:
        """Get confidence score (backward compat)."""
        if self.policy_decision:
            return self.policy_decision.confidence
        return 1.0

    @property
    def delete_message(self) -> bool:
        """Should message be deleted?"""
        if self.policy_decision:
            return self.policy_decision.delete_message
        return False

    @property
    def alert_moderators(self) -> bool:
        """Should moderators be alerted?"""
        if self.policy_decision:
            return self.policy_decision.alert_moderators
        return False

    @property
    def action_duration(self) -> int | None:
        """Get action duration (for timeouts)."""
        if self.policy_decision:
            return self.policy_decision.duration_seconds
        return None

    @property
    def action_message(self) -> str:
        """Get user-facing message."""
        if self.policy_decision:
            return self.policy_decision.action_message
        return ""


class AIModerationEngine:
    """
    Main AI moderation engine with PolicyEngine integration.

    Coordinates all AI detection systems with:
    - Parallel async execution
    - PolicyEngine for decisions
    - Schema v1 throughout
    - Comprehensive monitoring
    """

    def __init__(
        self,
        llm,
        server_config: ServerConfig | None = None,
        enable_toxicity: bool = True,
        enable_spam: bool = True,
        enable_scam: bool = True,
        enable_raid: bool = True
    ):
        """
        Initialize AI moderation engine.

        Args:
            llm: Language model instance
            server_config: Server configuration with policy (uses MODERATE if None)
            enable_toxicity: Enable toxicity detection
            enable_spam: Enable spam detection
            enable_scam: Enable scam detection
            enable_raid: Enable raid detection
        """
        self.llm = llm

        # Default to moderate policy if no config provided
        if server_config is None:
            server_config = ServerConfig(
                server_id="default",
                policy=MODERATE_POLICY,
                confidence_threshold=0.7
            )
        self.server_config = server_config

        # Initialize PolicyEngine
        self.policy_engine = PolicyEngine()

        # Initialize AI systems
        self.toxicity_ai = ToxicityAI(llm) if enable_toxicity else None
        self.spam_ai = SpamAI(llm) if enable_spam else None
        self.scam_ai = ScamAI(llm) if enable_scam else None
        self.raid_ai = RaidAI(llm) if enable_raid else None

        # Metrics
        self._metrics = {
            'total_analyses': 0,
            'decisions_by_action': {},
            'avg_processing_time_ms': 0.0,
            'total_processing_time_ms': 0.0
        }

        logger.info(
            f"[AIModerationEngine] Initialized with policy={server_config.policy.name}, "
            f"toxicity={enable_toxicity}, spam={enable_spam}, "
            f"scam={enable_scam}, raid={enable_raid}"
        )

    async def analyze_message(
        self,
        message: str,
        user_name: str = "User",
        context: list[str] | None = None,
        user_history: str | None = None,
        user_trust_score: float = 0.5,
        check_toxicity: bool = True,
        check_scam: bool = True
    ) -> ModerationResult:
        """
        Analyze a single message for violations.

        Runs toxicity and scam checks IN PARALLEL for speed,
        then uses PolicyEngine to make decision.

        Args:
            message: Message content
            user_name: Username
            context: Conversation context
            user_history: User's behavior history
            user_trust_score: 0.0-1.0, higher = more trusted
            check_toxicity: Run toxicity check
            check_scam: Run scam check

        Returns:
            ModerationResult with PolicyDecision
        """
        start_time = time.time()
        logger.info(f"[AIModerationEngine] Analyzing message from {user_name}")

        result = ModerationResult()

        # Run checks IN PARALLEL (not sequential!)
        tasks = []

        if check_toxicity and self.toxicity_ai:
            result.checks_run.append("toxicity")
            tasks.append(self._check_toxicity(message, user_name, context, user_history))

        if check_scam and self.scam_ai:
            result.checks_run.append("scam")
            tasks.append(self._check_scam(message, user_name, user_trust_score))

        # Gather results (parallel execution!)
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for check_result in results:
                if isinstance(check_result, Exception):
                    error_msg = f"Check failed: {str(check_result)}"
                    logger.error(f"[AIModerationEngine] {error_msg}")
                    result.analysis_errors.append(error_msg)
                elif isinstance(check_result, MessageAnalysis):
                    result.toxicity = check_result
                elif isinstance(check_result, ScamAnalysis):
                    result.scam = check_result

        # Use PolicyEngine to make decision
        result = self._make_policy_decision(result)

        # Calculate processing time
        result.processing_time_ms = (time.time() - start_time) * 1000

        # Update metrics
        self._update_metrics(result)

        logger.info(
            f"[AIModerationEngine] Decision: {result.decision}, "
            f"Confidence: {result.confidence:.2f}, "
            f"Time: {result.processing_time_ms:.1f}ms, "
            f"Checks: {', '.join(result.checks_run)}"
        )

        return result

    async def analyze_spam_burst(
        self,
        messages: list[str],
        timeframe_seconds: float,
        user_name: str = "User",
        user_trust_score: float = 0.5
    ) -> ModerationResult:
        """
        Analyze multiple rapid messages for spam.

        Args:
            messages: List of messages
            timeframe_seconds: Time window
            user_name: Username
            user_trust_score: 0.0-1.0, higher = more trusted

        Returns:
            ModerationResult with PolicyDecision
        """
        start_time = time.time()
        logger.info(
            f"[AIModerationEngine] Analyzing spam burst: "
            f"{len(messages)} messages in {timeframe_seconds:.1f}s from {user_name}"
        )

        result = ModerationResult(checks_run=["spam"])

        if self.spam_ai:
            try:
                spam = await self.spam_ai.analyze_burst(
                    messages=messages,
                    timeframe_seconds=timeframe_seconds,
                    user_name=user_name,
                    user_trust_score=user_trust_score
                )
                result.spam = spam

                # Use PolicyEngine for decision
                result = self._make_policy_decision(result)

            except Exception as e:
                error_msg = f"Spam analysis failed: {str(e)}"
                logger.error(f"[AIModerationEngine] {error_msg}")
                result.analysis_errors.append(error_msg)

        result.processing_time_ms = (time.time() - start_time) * 1000
        self._update_metrics(result)

        return result

    async def analyze_raid(
        self,
        join_events: list[JoinEvent],
        timeframe_minutes: float
    ) -> ModerationResult:
        """
        Analyze join pattern for raid.

        Args:
            join_events: List of join events
            timeframe_minutes: Time window in minutes

        Returns:
            ModerationResult with PolicyDecision
        """
        start_time = time.time()
        logger.info(f"[AIModerationEngine] Analyzing raid: {len(join_events)} joins")

        result = ModerationResult(checks_run=["raid"])

        if self.raid_ai:
            try:
                raid = await self.raid_ai.analyze_joins(
                    join_events=join_events,
                    timeframe_minutes=timeframe_minutes
                )
                result.raid = raid

                # For raids, we alert mods but don't make user-level decisions
                # Raids require server-wide action
                if raid.is_raid:
                    result.primary_violation = "raid"
                    result.reasoning = raid.reasoning
                    result.evidence = raid.evidence
                    result.flagged_for_review = True
                    # Create a simple decision for raid alerts
                    result.policy_decision = PolicyDecision(
                        action=PolicyAction.ALERT_MODS,
                        reason="raid",
                        policy_applied=self.server_config.policy.name,
                        rule_triggered="raid_detection",
                        alert_moderators=True,
                        action_message=f"⚠️ RAID DETECTED: {len(join_events)} suspicious joins"
                    )

            except Exception as e:
                error_msg = f"Raid analysis failed: {str(e)}"
                logger.error(f"[AIModerationEngine] {error_msg}")
                result.analysis_errors.append(error_msg)

        result.processing_time_ms = (time.time() - start_time) * 1000
        self._update_metrics(result)

        return result

    # Helper methods for parallel checks

    async def _check_toxicity(
        self,
        message: str,
        user_name: str,
        context: list[str] | None,
        user_history: str | None
    ) -> MessageAnalysis:
        """Run toxicity check (async) - returns MessageAnalysis."""
        return await self.toxicity_ai.analyze_message(
            message=message,
            context=context,
            user_history=user_history,
            user_name=user_name
        )

    async def _check_scam(
        self,
        message: str,
        user_name: str,
        user_trust_score: float
    ) -> ScamAnalysis:
        """Run scam check (async) - returns ScamAnalysis."""
        return await self.scam_ai.analyze_message(
            message=message,
            user_name=user_name,
            user_trust_score=user_trust_score
        )

    # PolicyEngine integration

    def _make_policy_decision(self, result: ModerationResult) -> ModerationResult:
        """
        Use PolicyEngine to make decision based on analyses.

        Selects most severe analysis and passes to PolicyEngine.
        """
        # Find the most relevant analysis to pass to PolicyEngine.
        #
        # We must NOT blindly prioritize scam: a message can have a benign
        # scam analysis (not a violation) alongside a genuine toxicity
        # violation. Picking scam first would then discard the toxicity
        # result and let harassment through. Instead, among the analyses that
        # are actually violations, pick the most severe (by intensity, then
        # confidence). Fall back to a fixed order only if none is a violation.
        candidates = []
        if result.scam and result.scam.message_analysis:
            candidates.append(("scam", result.scam.message_analysis))
        if result.toxicity:
            candidates.append(("toxicity", result.toxicity))
        if result.spam and result.spam.message_analysis:
            candidates.append(("spam", result.spam.message_analysis))

        analysis_to_decide = None
        if candidates:
            _INTENSITY_RANK = {
                Intensity.MILD: 1,
                Intensity.MODERATE: 2,
                Intensity.SEVERE: 3,
                Intensity.EXTREME: 4,
            }

            def _severity_key(item):
                _name, analysis = item
                is_viol = bool(getattr(analysis, "is_violation", lambda: False)())
                intensity = getattr(analysis, "intensity", None)
                intensity_rank = _INTENSITY_RANK.get(intensity, 0)
                conf = float(getattr(analysis, "confidence_score", 0.0) or 0.0)
                return (is_viol, intensity_rank, conf)

            # Prefer a real violation; among violations, the most severe.
            best_name, best_analysis = max(candidates, key=_severity_key)
            best_is_viol = bool(
                getattr(best_analysis, "is_violation", lambda: False)()
            )
            if best_is_viol:
                analysis_to_decide = best_analysis
                result.primary_violation = best_name
            else:
                # No analysis is a violation; keep prior scam>toxicity>spam
                # ordering so PolicyEngine still gets something coherent.
                analysis_to_decide = candidates[0][1]
                result.primary_violation = candidates[0][0]

        # If we have analysis, use PolicyEngine
        if analysis_to_decide:
            try:
                policy_decision = self.policy_engine.decide(
                    analysis=analysis_to_decide,
                    server_config=self.server_config
                )
                result.policy_decision = policy_decision
                result.reasoning = analysis_to_decide.reasoning
                result.evidence = analysis_to_decide.key_phrases

                # Set flags based on decision
                if policy_decision.action == PolicyAction.REVIEW:
                    result.flagged_for_review = True

                logger.debug(
                    f"[PolicyEngine] Decision: {policy_decision.action.value}, "
                    f"Rule: {policy_decision.rule_triggered}, "
                    f"Confidence: {policy_decision.confidence:.2f}"
                )

            except Exception as e:
                logger.error(f"[PolicyEngine] Decision failed: {e}")
                # Fail-safe: flag for review
                result.policy_decision = PolicyDecision(
                    action=PolicyAction.REVIEW,
                    reason="analysis_error",
                    policy_applied="error_fallback",
                    rule_triggered="policy_engine_error",
                    requires_review=True,
                    action_message=f"Policy decision failed: {str(e)}"
                )
                result.flagged_for_review = True

        return result

    def _update_metrics(self, result: ModerationResult):
        """Update engine metrics."""
        self._metrics['total_analyses'] += 1
        self._metrics['total_processing_time_ms'] += result.processing_time_ms
        self._metrics['avg_processing_time_ms'] = (
            self._metrics['total_processing_time_ms'] / self._metrics['total_analyses']
        )

        decision_key = result.decision  # Now a string from property
        self._metrics['decisions_by_action'][decision_key] = (
            self._metrics['decisions_by_action'].get(decision_key, 0) + 1
        )

    def get_metrics(self) -> dict[str, Any]:
        """Get comprehensive metrics."""
        component_metrics = {}

        if self.toxicity_ai:
            component_metrics['toxicity'] = self.toxicity_ai.get_stats()
        if self.spam_ai:
            component_metrics['spam'] = self.spam_ai.get_stats()
        if self.scam_ai:
            component_metrics['scam'] = self.scam_ai.get_stats()
        if self.raid_ai:
            component_metrics['raid'] = self.raid_ai.get_stats()

        return {
            "engine": self._metrics,
            "components": component_metrics
        }

    def clear_caches(self):
        """Clear all component caches."""
        if self.toxicity_ai:
            self.toxicity_ai.clear_cache()
        if self.spam_ai:
            self.spam_ai.clear_cache()
        if self.scam_ai:
            self.scam_ai.clear_cache()
        if self.raid_ai:
            self.raid_ai.clear_cache()

        logger.info("[AIModerationEngine] All caches cleared")


# Example usage
async def example_usage():
    """Example of using AI moderation engine with PolicyEngine."""
    from azure.local_llm import LocalLLM

    # Initialize with server config
    server_config = ServerConfig(
        server_id="example-server",
        policy=MODERATE_POLICY,  # Or STRICT_POLICY, LENIENT_POLICY
        confidence_threshold=0.7
    )

    llm = LocalLLM("models/qwen2.5-7b-instruct.gguf")
    engine = AIModerationEngine(llm, server_config=server_config)

    # Example 1: Analyze message (parallel toxicity + scam checks)
    result = await engine.analyze_message(
        message="You're such an idiot, you never know anything",
        user_name="BadUser",
        context=["Previous helpful discussion"],
        user_history="2 previous warnings"
    )

    print("Example 1 (Harassment):")
    print(f"  Decision: {result.decision}")
    print(f"  Confidence: {result.confidence:.2f}")
    print(f"  Delete message: {result.delete_message}")
    print(f"  Action: {result.action_message}")
    print(f"  Processing time: {result.processing_time_ms:.1f}ms")
    print(f"  Checks run: {', '.join(result.checks_run)}")
    if result.policy_decision:
        print(f"  Rule triggered: {result.policy_decision.rule_triggered}")
        print(f"  Policy applied: {result.policy_decision.policy_applied}")
    print()

    # Example 2: Helpful warning (should be ALLOWED by PolicyEngine)
    result = await engine.analyze_message(
        message="Don't be an idiot and click that phishing link!",
        user_name="HelpfulUser",
        context=["User1: Check out this link!", "User2: That looks sus"]
    )

    print("Example 2 (Helpful warning):")
    print(f"  Decision: {result.decision}")
    print(f"  Message Type: {result.toxicity.message_type if result.toxicity else 'N/A'}")
    print(f"  Intent: {result.toxicity.intent if result.toxicity else 'N/A'}")
    print(f"  Action: {result.action_message or 'None - message allowed'}")
    print()

    # Example 3: Analyze spam burst
    result = await engine.analyze_spam_burst(
        messages=[
            "JOIN MY SERVER!!!",
            "discord.gg/spam",
            "FREE NITRO!!!",
            "JOIN NOW!!!"
        ],
        timeframe_seconds=5.0,
        user_name="Spammer"
    )

    print("Example 3 (Spam burst):")
    print(f"  Decision: {result.decision}")
    print(f"  Message Type: {result.spam.message_analysis.message_type if result.spam else 'N/A'}")
    print(f"  Action: {result.action_message}")
    print()

    # Get metrics
    metrics = engine.get_metrics()
    print("Metrics:")
    print(f"  Total analyses: {metrics['engine']['total_analyses']}")
    print(f"  Avg processing time: {metrics['engine']['avg_processing_time_ms']:.1f}ms")
    print(f"  Decisions: {metrics['engine']['decisions_by_action']}")


if __name__ == "__main__":
    asyncio.run(example_usage())
