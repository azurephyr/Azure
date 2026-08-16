"""
PolicyEngine - Pure Function Policy Decision System

Converts MessageAnalysis → PolicyDecision using data-driven rules.

DESIGN: PolicyEngine Design v1.0 (FROZEN)
VERSION: 1.0.0
STATUS: Production Implementation

RESPONSIBILITIES:
- Match analysis against policy rules
- Apply safety overrides (SCAM/THREAT always dangerous)
- Handle edge cases (errors, low confidence, no match)
- Provide audit trail

NOT RESPONSIBLE FOR:
- Message analysis (LLMs do this)
- User history tracking (ModerationEngine does this)
- Action execution (ActionExecutor does this)
- Side effects (pure function)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .models import (
    Intensity,
    Intent,
    MessageAnalysis,
    MessageType,
    PolicyAction,
    PolicyDecision,
    PolicyReason,
    Specificity,
    Target,
)

logger = logging.getLogger("azure.ai_moderation.policy_engine")


# ============================================================================
# RULE STRUCTURE
# ============================================================================

@dataclass
class Condition:
    """
    Conditions that must match for rule to trigger.

    All specified conditions must match (AND logic).
    None/null conditions are ignored (match anything).
    """
    # Core semantic conditions
    message_type: MessageType | None = None
    target: Target | None = None
    intent: Intent | None = None
    intensity: Intensity | None = None
    specificity: Specificity | None = None
    min_confidence: float = 0.7

    # Linguistic conditions
    has_profanity: bool | None = None
    has_slurs: bool | None = None
    has_threats: bool | None = None

    # Mitigating conditions
    is_joke: bool | None = None
    is_educational: bool | None = None

    def matches(self, analysis: MessageAnalysis) -> bool:
        """Check if analysis matches ALL specified conditions."""
        # Confidence check
        if analysis.confidence_score < self.min_confidence:
            return False

        # Core semantic checks
        if self.message_type and analysis.message_type != self.message_type:
            return False
        if self.target and analysis.target != self.target:
            return False
        if self.intent and analysis.intent != self.intent:
            return False
        if self.intensity and analysis.intensity != self.intensity:
            return False
        if self.specificity and analysis.specificity != self.specificity:
            return False

        # Linguistic checks
        if self.has_profanity is not None and analysis.linguistic.contains_profanity != self.has_profanity:
            return False
        if self.has_slurs is not None and analysis.linguistic.contains_slurs != self.has_slurs:
            return False
        if self.has_threats is not None and analysis.linguistic.contains_threats != self.has_threats:
            return False

        # Mitigating checks
        if self.is_joke is not None and analysis.mitigating.is_joke != self.is_joke:
            return False
        return not (self.is_educational is not None and analysis.mitigating.is_educational != self.is_educational)

    def specificity_count(self) -> int:
        """Count how many conditions are specified (for most-specific matching)."""
        count = 0
        if self.message_type:
            count += 1
        if self.target:
            count += 1
        if self.intent:
            count += 1
        if self.intensity:
            count += 1
        if self.specificity:
            count += 1
        if self.has_profanity is not None:
            count += 1
        if self.has_slurs is not None:
            count += 1
        if self.has_threats is not None:
            count += 1
        if self.is_joke is not None:
            count += 1
        if self.is_educational is not None:
            count += 1
        return count


@dataclass
class Rule:
    """A single policy rule."""
    name: str
    condition: Condition
    action: PolicyAction
    reason: PolicyReason
    priority: int = 100
    duration_seconds: int | None = None
    delete_message: bool = False
    action_message: str = ""

    def matches(self, analysis: MessageAnalysis) -> bool:
        """Check if rule matches analysis."""
        return self.condition.matches(analysis)

    def get_specificity(self) -> int:
        """Get specificity for sorting (higher = more specific)."""
        return self.condition.specificity_count()


@dataclass
class Policy:
    """A collection of rules defining server policy."""
    name: str
    description: str
    rules: list[Rule] = field(default_factory=list)


# ============================================================================
# SERVER CONFIGURATION
# ============================================================================

@dataclass
class ServerConfig:
    """Server-specific policy configuration."""
    server_id: str
    policy: Policy
    confidence_threshold: float = 0.7

    # Feature flags
    allow_profanity_in_warnings: bool = False
    require_manual_review_for_bans: bool = True


# ============================================================================
# POLICY ENGINE
# ============================================================================

class PolicyEngine:
    """
    Pure function policy engine.

    Converts MessageAnalysis → PolicyDecision using rules.
    No side effects, no state, no LLM calls.
    """

    def __init__(self):
        """Initialize policy engine."""
        self._rules_by_type: dict[MessageType, list[Rule]] = {}

    def decide(
        self,
        analysis: MessageAnalysis,
        server_config: ServerConfig
    ) -> PolicyDecision:
        """
        Make policy decision based on analysis and server config.

        Args:
            analysis: Message analysis from AI
            server_config: Server-specific configuration

        Returns:
            PolicyDecision with action, reason, and audit trail
        """
        logger.debug(
            f"[PolicyEngine] Deciding for {analysis.message_type} "
            f"(confidence={analysis.confidence_score:.2f})"
        )

        # Edge case 1: Analysis error
        if analysis.analysis_error:
            logger.warning(f"[PolicyEngine] Analysis error: {analysis.error_reason}")
            return self._handle_analysis_error(analysis)

        # Edge case 2: Low confidence
        if analysis.confidence_score < server_config.confidence_threshold:
            logger.info(
                f"[PolicyEngine] Low confidence {analysis.confidence_score:.2f} "
                f"< {server_config.confidence_threshold}"
            )
            return self._handle_low_confidence(analysis, server_config)

        # Safety override: SCAM and THREAT always dangerous
        if analysis.message_type in [MessageType.SCAM, MessageType.THREAT]:
            logger.warning(f"[PolicyEngine] Safety override for {analysis.message_type}")
            return self._apply_safety_override(analysis)

        # Find best matching rule
        best_rule = self._find_best_rule(analysis, server_config.policy)

        # Edge case 3: No rule match
        if not best_rule:
            logger.warning(f"[PolicyEngine] No rule match for {analysis.message_type}")
            return self._handle_no_match(analysis, server_config)

        # Apply rule
        logger.info(f"[PolicyEngine] Applying rule: {best_rule.name}")
        return self._apply_rule(best_rule, analysis, server_config)

    def _find_best_rule(
        self,
        analysis: MessageAnalysis,
        policy: Policy
    ) -> Rule | None:
        """
        Find the most specific matching rule.

        Algorithm:
        1. Filter to matching rules
        2. Sort by specificity (descending)
        3. Break ties with priority
        4. Return first (most specific)
        """
        # Filter to matching rules
        matching = [rule for rule in policy.rules if rule.matches(analysis)]

        if not matching:
            return None

        # Sort by specificity (desc), then priority (desc)
        matching.sort(
            key=lambda r: (r.get_specificity(), r.priority),
            reverse=True
        )

        return matching[0]

    def _apply_rule(
        self,
        rule: Rule,
        analysis: MessageAnalysis,
        server_config: ServerConfig
    ) -> PolicyDecision:
        """Apply a rule to create PolicyDecision."""
        # Check if ban requires manual review
        requires_review = False
        if rule.action == PolicyAction.BAN and server_config.require_manual_review_for_bans:
            requires_review = True

        # Build decision
        return PolicyDecision(
            action=rule.action,
            reason=rule.reason,
            policy_applied=f"{server_config.policy.name}_policy_v1",
            rule_triggered=rule.name,
            duration_seconds=rule.duration_seconds,
            delete_message=rule.delete_message,
            alert_moderators=False,
            action_message=rule.action_message or self._default_action_message(rule.action),
            reversible=True,
            requires_review=requires_review,
            confidence=analysis.confidence_score,
            analysis_summary=f"{analysis.message_type.value}: {analysis.reasoning[:100]}"
        )

    def _apply_safety_override(self, analysis: MessageAnalysis) -> PolicyDecision:
        """Apply safety override for SCAM/THREAT (always ban)."""
        if analysis.message_type == MessageType.SCAM:
            reason = PolicyReason.SCAM
            msg_type = "scam"
        else:  # THREAT
            reason = PolicyReason.THREAT_OF_VIOLENCE
            msg_type = "threat"

        return PolicyDecision(
            action=PolicyAction.BAN,
            reason=reason,
            policy_applied="safety_override",
            rule_triggered=f"{msg_type}_safety",
            duration_seconds=None,
            delete_message=True,
            alert_moderators=True,
            action_message=f"{msg_type.capitalize()} detected - user banned for safety",
            reversible=False,
            requires_review=False,
            confidence=analysis.confidence_score,
            analysis_summary=f"{analysis.message_type.value}: {analysis.reasoning[:100]}"
        )

    def _handle_analysis_error(self, analysis: MessageAnalysis) -> PolicyDecision:
        """Handle analysis error - flag for review."""
        return PolicyDecision(
            action=PolicyAction.REVIEW,
            reason=PolicyReason.ANALYSIS_ERROR,
            policy_applied="error_handler",
            rule_triggered="analysis_error",
            duration_seconds=None,
            delete_message=False,
            alert_moderators=False,
            action_message="Analysis failed, flagged for review",
            reversible=True,
            requires_review=True,
            confidence=0.0,
            analysis_summary=f"Error: {analysis.error_reason}"
        )

    def _handle_low_confidence(
        self,
        analysis: MessageAnalysis,
        server_config: ServerConfig
    ) -> PolicyDecision:
        """Handle low confidence - flag for review."""
        return PolicyDecision(
            action=PolicyAction.REVIEW,
            reason=PolicyReason.INSUFFICIENT_CONFIDENCE,
            policy_applied="confidence_filter",
            rule_triggered="low_confidence",
            duration_seconds=None,
            delete_message=False,
            alert_moderators=False,
            action_message=f"Low confidence ({analysis.confidence_score:.2f})",
            reversible=True,
            requires_review=True,
            confidence=analysis.confidence_score,
            analysis_summary=f"{analysis.message_type.value}: confidence too low"
        )

    def _handle_no_match(
        self,
        analysis: MessageAnalysis,
        server_config: ServerConfig
    ) -> PolicyDecision:
        """Handle no matching rule - flag for review."""
        return PolicyDecision(
            action=PolicyAction.REVIEW,
            reason=PolicyReason.INSUFFICIENT_CONFIDENCE,
            policy_applied=f"{server_config.policy.name}_policy_v1",
            rule_triggered="default_no_match",
            duration_seconds=None,
            delete_message=False,
            alert_moderators=False,
            action_message="No matching rule, needs manual review",
            reversible=True,
            requires_review=True,
            confidence=analysis.confidence_score,
            analysis_summary=f"{analysis.message_type.value}: no rule match"
        )

    def _default_action_message(self, action: PolicyAction) -> str:
        """Get default user-facing message for action."""
        messages = {
            PolicyAction.ALLOW: "",
            PolicyAction.WARN: "You have received an official warning for violating server rules.",
            PolicyAction.TIMEOUT: "You have been timed out for violating server rules.",
            PolicyAction.KICK: "You have been kicked from the server for violating rules.",
            PolicyAction.BAN: "You have been banned from the server for violating rules.",
            PolicyAction.ALERT_MODS: "",
            PolicyAction.REVIEW: "Your message has been flagged for moderator review.",
        }
        return messages.get(action, "")


# ============================================================================
# POLICY DEFINITIONS (STRICT, MODERATE, LENIENT)
# ============================================================================

# STRICT POLICY: Zero tolerance
STRICT_POLICY = Policy(
    name="strict",
    description="Zero tolerance for violations",
    rules=[
        # SCAM: Always ban (safety override handles this, but included for completeness)
        Rule(
            name="scam_always_ban",
            condition=Condition(message_type=MessageType.SCAM),
            action=PolicyAction.BAN,
            reason=PolicyReason.SCAM,
            delete_message=True,
            priority=1000
        ),

        # THREAT: Always ban
        Rule(
            name="threat_always_ban",
            condition=Condition(message_type=MessageType.THREAT),
            action=PolicyAction.BAN,
            reason=PolicyReason.THREAT_OF_VIOLENCE,
            delete_message=True,
            priority=1000
        ),

        # HATE_SPEECH: Always ban
        Rule(
            name="hate_speech_ban",
            condition=Condition(message_type=MessageType.HATE_SPEECH),
            action=PolicyAction.BAN,
            reason=PolicyReason.HATE_SPEECH,
            delete_message=True,
            priority=1000
        ),

        # HARASSMENT: Severe → Ban, Moderate → Timeout, Mild → Warn
        Rule(
            name="harassment_extreme",
            condition=Condition(
                message_type=MessageType.HARASSMENT,
                intensity=Intensity.EXTREME
            ),
            action=PolicyAction.BAN,
            reason=PolicyReason.SEVERE_HARASSMENT,
            delete_message=True,
            priority=250
        ),

        Rule(
            name="harassment_severe",
            condition=Condition(
                message_type=MessageType.HARASSMENT,
                intensity=Intensity.SEVERE
            ),
            action=PolicyAction.BAN,
            reason=PolicyReason.SEVERE_HARASSMENT,
            delete_message=True,
            priority=200
        ),

        Rule(
            name="harassment_moderate",
            condition=Condition(
                message_type=MessageType.HARASSMENT,
                intensity=Intensity.MODERATE
            ),
            action=PolicyAction.TIMEOUT,
            reason=PolicyReason.HARASSMENT,
            duration_seconds=3600,  # 1 hour
            delete_message=True,
            priority=150
        ),

        Rule(
            name="harassment_mild",
            condition=Condition(
                message_type=MessageType.HARASSMENT,
                intensity=Intensity.MILD
            ),
            action=PolicyAction.WARN,
            reason=PolicyReason.HARASSMENT,
            delete_message=True,
            priority=100
        ),

        # SPAM: Timeout
        Rule(
            name="spam_timeout",
            condition=Condition(message_type=MessageType.SPAM),
            action=PolicyAction.TIMEOUT,
            reason=PolicyReason.SPAM,
            duration_seconds=3600,  # 1 hour
            delete_message=True,
            priority=100
        ),

        # RAID: Alert mods
        Rule(
            name="raid_alert",
            condition=Condition(message_type=MessageType.RAID),
            action=PolicyAction.ALERT_MODS,
            reason=PolicyReason.RAID,
            delete_message=False,
            priority=1000
        ),

        # SAFE: Allow all safe message types
        Rule(
            name="conversation_allow",
            condition=Condition(message_type=MessageType.CONVERSATION),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="warning_allow",
            condition=Condition(message_type=MessageType.WARNING),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.HELPFUL_CONTENT,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="instruction_allow",
            condition=Condition(message_type=MessageType.INSTRUCTION),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.HELPFUL_CONTENT,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="question_allow",
            condition=Condition(message_type=MessageType.QUESTION),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="joke_allow",
            condition=Condition(message_type=MessageType.JOKE),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="quote_allow",
            condition=Condition(message_type=MessageType.QUOTE),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="self_reference_allow",
            condition=Condition(message_type=MessageType.SELF_REFERENCE),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=50
        ),
    ]
)


# MODERATE POLICY: Balanced approach
MODERATE_POLICY = Policy(
    name="moderate",
    description="Balanced approach, warnings before timeouts",
    rules=[
        # SCAM/THREAT/HATE: Still always ban (safety)
        Rule(
            name="scam_always_ban",
            condition=Condition(message_type=MessageType.SCAM),
            action=PolicyAction.BAN,
            reason=PolicyReason.SCAM,
            delete_message=True,
            priority=1000
        ),

        Rule(
            name="threat_always_ban",
            condition=Condition(message_type=MessageType.THREAT),
            action=PolicyAction.BAN,
            reason=PolicyReason.THREAT_OF_VIOLENCE,
            delete_message=True,
            priority=1000
        ),

        Rule(
            name="hate_speech_ban",
            condition=Condition(message_type=MessageType.HATE_SPEECH),
            action=PolicyAction.BAN,
            reason=PolicyReason.HATE_SPEECH,
            delete_message=True,
            priority=1000
        ),

        # HARASSMENT: More lenient (timeout instead of ban for severe)
        Rule(
            name="harassment_extreme_ban",
            condition=Condition(
                message_type=MessageType.HARASSMENT,
                intensity=Intensity.EXTREME
            ),
            action=PolicyAction.BAN,
            reason=PolicyReason.SEVERE_HARASSMENT,
            delete_message=True,
            priority=250
        ),

        Rule(
            name="harassment_severe_timeout",
            condition=Condition(
                message_type=MessageType.HARASSMENT,
                intensity=Intensity.SEVERE
            ),
            action=PolicyAction.TIMEOUT,  # Timeout instead of ban
            reason=PolicyReason.SEVERE_HARASSMENT,
            duration_seconds=86400,  # 24 hours
            delete_message=True,
            priority=200
        ),

        Rule(
            name="harassment_moderate_warn",
            condition=Condition(
                message_type=MessageType.HARASSMENT,
                intensity=Intensity.MODERATE
            ),
            action=PolicyAction.WARN,  # Warn instead of timeout
            reason=PolicyReason.HARASSMENT,
            delete_message=True,
            priority=150
        ),

        Rule(
            name="harassment_mild_allow",
            condition=Condition(
                message_type=MessageType.HARASSMENT,
                intensity=Intensity.MILD
            ),
            action=PolicyAction.ALLOW,  # Allow mild
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=100
        ),

        # SPAM: Warn first
        Rule(
            name="spam_warn_first",
            condition=Condition(message_type=MessageType.SPAM),
            action=PolicyAction.WARN,
            reason=PolicyReason.SPAM,
            delete_message=True,
            priority=100
        ),

        # RAID: Alert mods
        Rule(
            name="raid_alert",
            condition=Condition(message_type=MessageType.RAID),
            action=PolicyAction.ALERT_MODS,
            reason=PolicyReason.RAID,
            delete_message=False,
            priority=1000
        ),

        # SAFE: Allow all safe message types (same as strict)
        Rule(
            name="conversation_allow",
            condition=Condition(message_type=MessageType.CONVERSATION),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="warning_allow",
            condition=Condition(message_type=MessageType.WARNING),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.HELPFUL_CONTENT,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="instruction_allow",
            condition=Condition(message_type=MessageType.INSTRUCTION),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.HELPFUL_CONTENT,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="question_allow",
            condition=Condition(message_type=MessageType.QUESTION),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="joke_allow",
            condition=Condition(message_type=MessageType.JOKE),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="quote_allow",
            condition=Condition(message_type=MessageType.QUOTE),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="self_reference_allow",
            condition=Condition(message_type=MessageType.SELF_REFERENCE),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=50
        ),
    ]
)


# LENIENT POLICY: Gaming community, trash talk OK
LENIENT_POLICY = Policy(
    name="lenient",
    description="Gaming community, trash talk OK",
    rules=[
        # SCAM/THREAT: Still always ban (safety)
        Rule(
            name="scam_always_ban",
            condition=Condition(message_type=MessageType.SCAM),
            action=PolicyAction.BAN,
            reason=PolicyReason.SCAM,
            delete_message=True,
            priority=1000
        ),

        Rule(
            name="threat_always_ban",
            condition=Condition(message_type=MessageType.THREAT),
            action=PolicyAction.BAN,
            reason=PolicyReason.THREAT_OF_VIOLENCE,
            delete_message=True,
            priority=1000
        ),

        # HATE_SPEECH: Timeout first
        Rule(
            name="hate_speech_timeout",
            condition=Condition(message_type=MessageType.HATE_SPEECH),
            action=PolicyAction.TIMEOUT,
            reason=PolicyReason.HATE_SPEECH,
            duration_seconds=86400,  # 24 hours
            delete_message=True,
            priority=900
        ),

        # HARASSMENT: Very lenient (allow if mitigated, warn for moderate)
        Rule(
            name="harassment_extreme_timeout",
            condition=Condition(
                message_type=MessageType.HARASSMENT,
                intensity=Intensity.EXTREME
            ),
            action=PolicyAction.TIMEOUT,
            reason=PolicyReason.SEVERE_HARASSMENT,
            duration_seconds=86400,  # 24 hours
            delete_message=True,
            priority=250
        ),

        Rule(
            name="harassment_severe_warn",
            condition=Condition(
                message_type=MessageType.HARASSMENT,
                intensity=Intensity.SEVERE
            ),
            action=PolicyAction.WARN,
            reason=PolicyReason.HARASSMENT,
            delete_message=False,
            priority=200
        ),

        # Allow harassment if it's a joke
        Rule(
            name="harassment_mitigated_allow",
            condition=Condition(
                message_type=MessageType.HARASSMENT,
                is_joke=True
            ),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=300  # More specific, overrides general harassment rules
        ),

        # Allow moderate harassment
        Rule(
            name="harassment_moderate_allow",
            condition=Condition(
                message_type=MessageType.HARASSMENT,
                intensity=Intensity.MODERATE
            ),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=150
        ),

        Rule(
            name="harassment_mild_allow",
            condition=Condition(
                message_type=MessageType.HARASSMENT,
                intensity=Intensity.MILD
            ),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=100
        ),

        # SPAM: Allow (community self-regulates)
        Rule(
            name="spam_allow",
            condition=Condition(message_type=MessageType.SPAM),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=100
        ),

        # RAID: Alert mods
        Rule(
            name="raid_alert",
            condition=Condition(message_type=MessageType.RAID),
            action=PolicyAction.ALERT_MODS,
            reason=PolicyReason.RAID,
            delete_message=False,
            priority=1000
        ),

        # Profanity in warnings is OK
        Rule(
            name="profanity_in_warnings_ok",
            condition=Condition(
                message_type=MessageType.WARNING,
                has_profanity=True
            ),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.HELPFUL_CONTENT,
            delete_message=False,
            priority=150  # More specific than general warning rule
        ),

        # SAFE: Allow all safe message types (same as other policies)
        Rule(
            name="conversation_allow",
            condition=Condition(message_type=MessageType.CONVERSATION),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="warning_allow",
            condition=Condition(message_type=MessageType.WARNING),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.HELPFUL_CONTENT,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="instruction_allow",
            condition=Condition(message_type=MessageType.INSTRUCTION),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.HELPFUL_CONTENT,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="question_allow",
            condition=Condition(message_type=MessageType.QUESTION),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="joke_allow",
            condition=Condition(message_type=MessageType.JOKE),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="quote_allow",
            condition=Condition(message_type=MessageType.QUOTE),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=50
        ),

        Rule(
            name="self_reference_allow",
            condition=Condition(message_type=MessageType.SELF_REFERENCE),
            action=PolicyAction.ALLOW,
            reason=PolicyReason.NORMAL_CONVERSATION,
            delete_message=False,
            priority=50
        ),
    ]
)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_policy_by_name(name: str) -> Policy:
    """Get policy by name."""
    policies = {
        "strict": STRICT_POLICY,
        "moderate": MODERATE_POLICY,
        "lenient": LENIENT_POLICY,
    }

    policy = policies.get(name.lower())
    if not policy:
        raise ValueError(f"Unknown policy: {name}. Valid: {list(policies.keys())}")

    return policy


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

def example_usage():
    """Example of how to use PolicyEngine."""
    from .models import (
        ConfidenceLevel,
        Intensity,
        Intent,
        LinguisticMarkers,
        MessageAnalysis,
        MessageType,
        MitigatingFactors,
        Specificity,
        Target,
    )

    # Initialize engine
    engine = PolicyEngine()

    # Example 1: Helpful warning (should ALLOW)
    analysis1 = MessageAnalysis(
        message_type=MessageType.WARNING,
        target=Target.NOBODY,
        intent=Intent.HELPFUL,
        intensity=Intensity.MILD,
        specificity=Specificity.VAGUE,
        confidence=ConfidenceLevel.HIGH,
        confidence_score=0.85,
        linguistic=LinguisticMarkers(contains_profanity=True, profanity_words=["idiot"]),
        mitigating=MitigatingFactors(),
        reasoning="Warning message with strong language",
        key_phrases=["Don't be"],
        analyzer="toxicity_ai"
    )

    config_strict = ServerConfig(
        server_id="test-server",
        policy=STRICT_POLICY,
        confidence_threshold=0.7
    )

    decision1 = engine.decide(analysis1, config_strict)
    print("Example 1 (Warning - Strict Policy):")
    print(f"  Action: {decision1.action}")
    print(f"  Reason: {decision1.reason}")
    print(f"  Rule: {decision1.rule_triggered}")
    print(f"  Delete: {decision1.delete_message}")
    print()

    # Example 2: Harassment (should vary by policy)
    analysis2 = MessageAnalysis(
        message_type=MessageType.HARASSMENT,
        target=Target.USER,
        intent=Intent.HARMFUL,
        intensity=Intensity.MODERATE,
        specificity=Specificity.TARGETED,
        confidence=ConfidenceLevel.VERY_HIGH,
        confidence_score=0.92,
        linguistic=LinguisticMarkers(contains_profanity=True, profanity_words=["idiot"]),
        mitigating=MitigatingFactors(),
        reasoning="Direct insult targeting user",
        key_phrases=["You're an idiot"],
        analyzer="toxicity_ai"
    )

    # Strict: TIMEOUT
    decision2_strict = engine.decide(analysis2, config_strict)
    print("Example 2 (Harassment Moderate - Strict Policy):")
    print(f"  Action: {decision2_strict.action}")
    print(f"  Reason: {decision2_strict.reason}")
    print(f"  Duration: {decision2_strict.duration_seconds}s")
    print()

    # Moderate: WARN
    config_moderate = ServerConfig(
        server_id="test-server",
        policy=MODERATE_POLICY,
        confidence_threshold=0.7
    )

    decision2_moderate = engine.decide(analysis2, config_moderate)
    print("Example 2 (Harassment Moderate - Moderate Policy):")
    print(f"  Action: {decision2_moderate.action}")
    print(f"  Reason: {decision2_moderate.reason}")
    print()

    # Lenient: ALLOW
    config_lenient = ServerConfig(
        server_id="test-server",
        policy=LENIENT_POLICY,
        confidence_threshold=0.7
    )

    decision2_lenient = engine.decide(analysis2, config_lenient)
    print("Example 2 (Harassment Moderate - Lenient Policy):")
    print(f"  Action: {decision2_lenient.action}")
    print(f"  Reason: {decision2_lenient.reason}")
    print()

    # Example 3: SCAM (should always BAN regardless of policy)
    analysis3 = MessageAnalysis(
        message_type=MessageType.SCAM,
        target=Target.EVERYONE,
        intent=Intent.DECEPTIVE,
        intensity=Intensity.SEVERE,
        specificity=Specificity.VAGUE,
        confidence=ConfidenceLevel.VERY_HIGH,
        confidence_score=0.95,
        linguistic=LinguisticMarkers(contains_urls=True),
        mitigating=MitigatingFactors(),
        reasoning="Phishing link detected",
        key_phrases=["Free Nitro"],
        analyzer="scam_ai"
    )

    decision3 = engine.decide(analysis3, config_lenient)
    print("Example 3 (SCAM - Lenient Policy - Safety Override):")
    print(f"  Action: {decision3.action}")
    print(f"  Reason: {decision3.reason}")
    print(f"  Policy Applied: {decision3.policy_applied}")
    print(f"  Alert Mods: {decision3.alert_moderators}")
    print()


if __name__ == "__main__":
    example_usage()
