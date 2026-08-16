"""
AI Moderation Data Models - Moderation Schema v1 (FROZEN)

This module defines the STABLE moderation schema that all Azure components depend on.

SCHEMA VERSION: v1.0.0
FROZEN DATE: July 9, 2026
STATUS: Stable - changes require architectural review

This schema separates:
1. ANALYSIS (what the message IS) - from LLM
2. POLICY (what we should DO) - from PolicyEngine
3. ACTION (how to enforce) - from ActionExecutor

Key design principles:
1. NO contradictory fields (is_toxic + context_safe = impossible)
2. NO string parsing in decision logic (type-safe enums only)
3. NO policy decisions from LLM (analysis only)
4. Strict validation prevents invalid states
5. Schema stability is a first-class engineering goal

Breaking changes to this schema require:
- Documented rationale
- Migration path for existing code
- Approval from architecture review
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

# ============================================================================
# ANALYSIS LAYER: What IS the message?
# ============================================================================

class MessageType(StrEnum):
    """
    Semantic classification of message.

    A message IS exactly one type. No contradictions possible.

    Safe types:
        CONVERSATION: Normal chat
        WARNING: "Don't be an idiot and click that"
        INSTRUCTION: "Make sure to read the rules"
        QUESTION: "What rank are you?"
        JOKE: Sarcasm, humor
        QUOTE: "He said 'you're dumb'" (quoting someone)
        SELF_REFERENCE: "I'm such an idiot" (about self)

    Violation types:
        HARASSMENT: Targeted attack on individual
        HATE_SPEECH: Discrimination based on identity
        THREAT: Violence or harm threat
        SPAM: Promotional/repetitive content
        SCAM: Phishing, fake giveaways
        RAID: Coordinated attack pattern
    """
    # Safe message types
    CONVERSATION = "conversation"
    WARNING = "warning"
    INSTRUCTION = "instruction"
    QUESTION = "question"
    JOKE = "joke"
    QUOTE = "quote"
    SELF_REFERENCE = "self_reference"

    # Violation types
    HARASSMENT = "harassment"
    HATE_SPEECH = "hate_speech"
    THREAT = "threat"
    SPAM = "spam"
    SCAM = "scam"
    RAID = "raid"

    # Unknown/error
    UNKNOWN = "unknown"


class Target(StrEnum):
    """
    Who is the message directed at?

    Critical for distinguishing:
    - "Don't be an idiot" (NOBODY) vs "You're an idiot" (USER)
    - "I'm an idiot" (SELF) vs "You're an idiot" (USER)
    """
    NOBODY = "nobody"          # General statement, no specific target
    SELF = "self"              # Speaker referring to themselves
    USER = "user"              # Specific user mentioned
    ROLE = "role"              # A group/role (@Moderators)
    EVERYONE = "everyone"      # @everyone, @here
    EXTERNAL = "external"      # Outside link/service/entity


class Intent(StrEnum):
    """
    Why was the message sent? What is the speaker trying to do?

    Same words, different intent:
    - "Don't be an idiot" + HELPFUL = Warning
    - "You're an idiot" + HARMFUL = Harassment
    """
    HELPFUL = "helpful"        # Trying to help others
    NEUTRAL = "neutral"        # Just chatting
    HARMFUL = "harmful"        # Trying to hurt/attack
    PROMOTIONAL = "promotional"  # Trying to advertise
    DECEPTIVE = "deceptive"    # Trying to scam/phish
    UNKNOWN = "unknown"


class Intensity(StrEnum):
    """
    How intense is the content?

    Replaces vague "toxicity_score" with clear semantic levels.
    """
    MILD = "mild"              # Slightly harsh language
    MODERATE = "moderate"      # Clearly harsh but not extreme
    SEVERE = "severe"          # Very harsh, personal attacks
    EXTREME = "extreme"        # Extremely violent, hateful


class Specificity(StrEnum):
    """
    How specific/targeted is the message?

    "You're dumb" (TARGETED) is worse than "people are dumb" (VAGUE)

    NOTE: Under evaluation - may overlap with Target enum.
    Will be reviewed after PolicyEngine implementation to determine
    if it provides independent value in policy decisions.
    """
    VAGUE = "vague"            # General statement
    SPECIFIC = "specific"      # Mentions specific person/group
    TARGETED = "targeted"      # Directly attacks specific individual


class ConfidenceLevel(StrEnum):
    """
    LLM's confidence in analysis.

    Clearer than raw 0.0-1.0 scores.
    """
    VERY_LOW = "very_low"      # 0.0-0.3
    LOW = "low"                # 0.3-0.5
    MEDIUM = "medium"          # 0.5-0.7
    HIGH = "high"              # 0.7-0.9
    VERY_HIGH = "very_high"    # 0.9-1.0


@dataclass
class LinguisticMarkers:
    """
    What linguistic features does the message contain?

    Pure linguistic analysis, no judgment.
    """
    contains_profanity: bool = False
    profanity_words: list[str] = field(default_factory=list)

    contains_slurs: bool = False
    slur_words: list[str] = field(default_factory=list)

    contains_threats: bool = False
    threat_phrases: list[str] = field(default_factory=list)

    contains_sexual_content: bool = False

    contains_urls: bool = False
    urls_found: list[str] = field(default_factory=list)

    # Structural markers
    is_question: bool = False
    is_imperative: bool = False  # Command form
    contains_negation: bool = False  # "don't", "not"
    is_all_caps: bool = False


@dataclass
class MitigatingFactors:
    """
    Factors that mitigate the severity of the primary classification.

    These are NOT contradictions with MessageType - they are contextual
    factors that REDUCE severity of what would otherwise be a violation.

    Example: MessageType.HARASSMENT + is_joke=True
    Meaning: "Technically contains harassment language, but joking context
             mitigates severity"

    PolicyEngine uses these to adjust responses:
    - Strict server: Ignore mitigating factors
    - Lenient server: Reduce punishment if mitigating factors present
    """
    is_sarcasm: bool = False
    is_joke: bool = False
    is_educational: bool = False
    is_quoting_someone: bool = False
    is_gaming_slang: bool = False

    # Pattern markers (from spam detection)
    is_repetitive: bool = False
    similarity_to_recent: float = 0.0  # 0.0-1.0


@dataclass
class MessageAnalysis:
    """
    Complete analysis of what a message IS.

    NO policy decisions (warn/ban/timeout).
    NO contradictions (validated in __post_init__).
    ONLY facts about the message.

    The LLM returns this. The PolicyEngine decides what to do with it.

    VALIDATION: Strict checks prevent logically impossible states.
    """
    # Core semantic classification
    message_type: MessageType
    target: Target
    intent: Intent

    # Intensity and specificity
    intensity: Intensity
    specificity: Specificity  # NOTE: Under evaluation for redundancy

    # Confidence
    confidence: ConfidenceLevel
    confidence_score: float  # 0.0-1.0 raw score

    # Linguistic analysis
    linguistic: LinguisticMarkers

    # Mitigating factors (NOT contradictions!)
    mitigating: MitigatingFactors

    # Evidence (structured, not prose)
    reasoning: str  # Brief explanation for debugging
    key_phrases: list[str] = field(default_factory=list)  # Actual phrases analyzed

    # Metadata
    analyzed_at: float = field(default_factory=time.time)
    analyzer: str = "unknown"  # Which AI (toxicity/spam/scam/raid)

    # Error handling
    analysis_error: bool = False
    error_reason: str = ""

    def __post_init__(self):
        """
        Strict validation to prevent logically contradictory states.

        This catches LLM errors immediately rather than allowing
        invalid data to propagate through the system.
        """
        # Validate confidence_score range
        if not 0.0 <= self.confidence_score <= 1.0:
            raise ValueError(
                f"confidence_score must be 0.0-1.0, got {self.confidence_score}"
            )

        # Validate confidence matches confidence_score
        expected_confidence = confidence_from_score(self.confidence_score)
        if self.confidence != expected_confidence:
            # Auto-correct instead of failing - LLM might use different buckets
            self.confidence = expected_confidence

        # Safe message types should NOT have harmful intent
        safe_types = {
            MessageType.CONVERSATION,
            MessageType.WARNING,
            MessageType.INSTRUCTION,
            MessageType.QUESTION,
            MessageType.QUOTE,
            MessageType.SELF_REFERENCE
        }

        if self.message_type in safe_types and self.intent == Intent.HARMFUL:
            raise ValueError(
                f"Contradiction: {self.message_type} cannot have Intent.HARMFUL. "
                f"If message is harmful, classify as HARASSMENT/THREAT/etc."
            )

        # Violation types should have harmful/promotional/deceptive intent
        # UNLESS mitigated (joke, sarcasm, educational)
        violation_types = {
            MessageType.HARASSMENT,
            MessageType.HATE_SPEECH,
            MessageType.THREAT
        }

        if self.message_type in violation_types and self.intent not in [Intent.HARMFUL, Intent.NEUTRAL, Intent.UNKNOWN] and not (
            self.mitigating.is_joke or
            self.mitigating.is_sarcasm or
            self.mitigating.is_educational or
            self.mitigating.is_quoting_someone
        ):
            raise ValueError(
                f"Contradiction: {self.message_type} with Intent.{self.intent} "
                f"but no mitigating factors. Should be Intent.HARMFUL."
            )

        # SPAM/SCAM should have appropriate intent
        if self.message_type == MessageType.SPAM and self.intent not in [Intent.PROMOTIONAL, Intent.HARMFUL, Intent.UNKNOWN]:
                raise ValueError(
                    f"Contradiction: SPAM should have PROMOTIONAL or HARMFUL intent, "
                    f"got Intent.{self.intent}"
                )

        if self.message_type == MessageType.SCAM and self.intent != Intent.DECEPTIVE:
            raise ValueError(
                f"Contradiction: SCAM should have Intent.DECEPTIVE, "
                f"got Intent.{self.intent}"
            )

        # JOKE as message type should have neutral/helpful intent
        if self.message_type == MessageType.JOKE and self.intent == Intent.HARMFUL and not self.mitigating.is_joke:
                raise ValueError(
                    "Contradiction: MessageType.JOKE with Intent.HARMFUL "
                    "but mitigating.is_joke=False. Classify as HARASSMENT if harmful."
                )

        # Validate mitigating.similarity_to_recent range
        if not 0.0 <= self.mitigating.similarity_to_recent <= 1.0:
            raise ValueError(
                f"mitigating.similarity_to_recent must be 0.0-1.0, "
                f"got {self.mitigating.similarity_to_recent}"
            )

        # If analysis_error=True, confidence should be low
        if self.analysis_error and self.confidence_score > 0.5:
            raise ValueError(
                f"Contradiction: analysis_error=True but confidence_score={self.confidence_score}. "
                f"Errors should have low confidence."
            )

    def is_safe(self) -> bool:
        """Check if message is classified as safe (not a violation)."""
        return is_safe_message_type(self.message_type)

    def is_violation(self) -> bool:
        """Check if message is classified as a violation."""
        return is_violation_message_type(self.message_type)


# ============================================================================
# SPAM-SPECIFIC ANALYSIS
# ============================================================================

@dataclass
class SpamPatterns:
    """Spam-specific pattern detection."""
    is_bot_like: bool = False
    is_promotional: bool = False
    message_count: int = 1
    messages_per_second: float = 0.0
    burst_detected: bool = False

    # Pattern similarity
    identical_messages: int = 0
    similar_messages: int = 0
    message_similarity: float = 0.0  # 0.0-1.0


@dataclass
class SpamAnalysis:
    """
    Spam-specific analysis.

    Extends MessageAnalysis with spam-specific metrics.
    """
    # Base analysis
    message_analysis: MessageAnalysis

    # Spam-specific
    patterns: SpamPatterns

    # Is this excitement vs spam?
    is_excitement: bool = False  # Excited gamer vs spam bot


# ============================================================================
# SCAM-SPECIFIC ANALYSIS
# ============================================================================

@dataclass
class URLAnalysis:
    """URL-specific analysis for scam detection."""
    urls_found: list[str] = field(default_factory=list)
    suspicious_domains: list[str] = field(default_factory=list)

    typosquatting_detected: bool = False
    typosquat_targets: list[str] = field(default_factory=list)  # Mimics which domain?

    url_shorteners_used: bool = False
    shortener_urls: list[str] = field(default_factory=list)


@dataclass
class ScamMarkers:
    """Scam-specific behavioral markers."""
    fake_urgency: bool = False  # "HURRY! LIMITED TIME!"
    promises_rewards: bool = False  # "Free Nitro!"
    requests_credentials: bool = False  # "Login to claim"
    impersonates_official: bool = False  # "Discord Staff"
    too_good_to_be_true: bool = False


@dataclass
class ScamAnalysis:
    """
    Scam-specific analysis.

    Extends MessageAnalysis with scam-specific checks.
    """
    # Base analysis
    message_analysis: MessageAnalysis

    # URL analysis
    url_analysis: URLAnalysis

    # Scam markers
    scam_markers: ScamMarkers

    # Danger assessment
    danger_level: Intensity  # Reuse Intensity enum


# ============================================================================
# RAID-SPECIFIC ANALYSIS
# ============================================================================

@dataclass
class JoinEvent:
    """Single user join event for raid detection."""
    user_id: str
    username: str
    account_age_days: int
    has_avatar: bool
    is_verified: bool
    join_timestamp: float


@dataclass
class RaidStatistics:
    """Statistical analysis of join pattern."""
    join_count: int
    timeframe_minutes: float
    join_rate_per_hour: float

    # Statistical metrics
    join_rate_zscore: float  # Standard deviations from baseline
    is_anomalous: bool

    # Account analysis
    avg_account_age_days: float
    new_account_rate: float  # % of accounts < 7 days old
    no_avatar_rate: float  # % without avatars
    unverified_rate: float


@dataclass
class RaidPatterns:
    """Raid-specific pattern detection."""
    coordinated_behavior: bool = False
    similar_usernames: bool = False
    similar_account_ages: bool = False
    bot_like_accounts: bool = False

    username_pattern: str | None = None  # e.g., "user####"


@dataclass
class RaidAnalysis:
    """
    Raid-specific analysis.

    Different from message analysis - analyzes join patterns, not messages.
    """
    # Pattern classification
    is_raid: bool
    raid_type: MessageType  # Will be RAID or UNKNOWN

    # Confidence
    confidence: ConfidenceLevel
    confidence_score: float

    # Statistical analysis
    statistics: RaidStatistics

    # Pattern analysis
    patterns: RaidPatterns

    # Severity
    severity: Intensity

    # Evidence
    reasoning: str
    evidence: list[str] = field(default_factory=list)

    # Metadata
    analyzed_at: float = field(default_factory=time.time)


# ============================================================================
# POLICY LAYER: What should we DO?
# ============================================================================

class PolicyAction(StrEnum):
    """
    What action should be taken?

    Decided by PolicyEngine, NOT by LLM.
    """
    ALLOW = "allow"            # No action needed
    DELETE = "delete"          # Delete message only
    WARN = "warn"              # Warn user
    TIMEOUT = "timeout"        # Temporary timeout
    KICK = "kick"              # Kick from server
    BAN = "ban"                # Permanent ban

    # Special actions
    ALERT_MODS = "alert_mods"  # Alert moderators
    REVIEW = "review"          # Flag for manual review
    LOCKDOWN = "lockdown"      # Server lockdown (raids)


class PolicyReason(StrEnum):
    """
    Why was this decision made?

    Structured enum, not free-form string.
    """
    # Safe
    SAFE_CONTENT = "safe_content"
    HELPFUL_CONTENT = "helpful_content"
    NORMAL_CONVERSATION = "normal_conversation"

    # Violations
    HARASSMENT = "harassment"
    SEVERE_HARASSMENT = "severe_harassment"
    HATE_SPEECH = "hate_speech"
    THREAT_OF_VIOLENCE = "threat_of_violence"

    SPAM = "spam"
    BOT_SPAM = "bot_spam"

    SCAM = "scam"
    PHISHING = "phishing"

    RAID = "raid"
    BOT_RAID = "bot_raid"

    # Errors
    ANALYSIS_ERROR = "analysis_error"
    INSUFFICIENT_CONFIDENCE = "insufficient_confidence"


@dataclass
class PolicyDecision:
    """
    Policy decision based on analysis + server rules.

    Completely separate from LLM analysis.
    Generated by PolicyEngine component.
    """
    # Decision
    action: PolicyAction
    reason: PolicyReason

    # Which policy made this decision
    policy_applied: str  # e.g., "strict_harassment_policy_v2"
    rule_triggered: str  # e.g., "severe_harassment_rule"

    # Action details
    duration_seconds: int | None = None  # For timeouts
    delete_message: bool = False
    alert_moderators: bool = False

    # User-facing message
    action_message: str = ""

    # Reversibility
    reversible: bool = True  # Can moderator override?
    requires_review: bool = False  # Flag for mod review?

    # Confidence in decision
    confidence: float = 1.0  # PolicyEngine's confidence in applying this rule

    # Metadata
    decided_at: float = field(default_factory=time.time)
    server_id: str | None = None

    # Audit trail
    analysis_summary: str = ""  # Brief summary of what analysis found


# ============================================================================
# COMBINED RESULT
# ============================================================================

@dataclass
class ModerationResult:
    """
    Complete moderation result combining analysis + policy decision.

    This is what gets returned to Discord bot for execution.
    """
    # Analysis (what IS the message)
    analysis: MessageAnalysis | None = None
    spam_analysis: SpamAnalysis | None = None
    scam_analysis: ScamAnalysis | None = None
    raid_analysis: RaidAnalysis | None = None

    # Policy decision (what to DO)
    decision: PolicyDecision | None = None

    # Performance
    processing_time_ms: float = 0.0
    checks_run: list[str] = field(default_factory=list)

    # Errors
    errors: list[str] = field(default_factory=list)

    # Metadata
    analyzed_at: float = field(default_factory=time.time)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def confidence_from_score(score: float) -> ConfidenceLevel:
    """Convert 0.0-1.0 score to ConfidenceLevel enum."""
    if score >= 0.9:
        return ConfidenceLevel.VERY_HIGH
    elif score >= 0.7:
        return ConfidenceLevel.HIGH
    elif score >= 0.5:
        return ConfidenceLevel.MEDIUM
    elif score >= 0.3:
        return ConfidenceLevel.LOW
    else:
        return ConfidenceLevel.VERY_LOW


def is_safe_message_type(message_type: MessageType) -> bool:
    """Check if message type is safe (not a violation)."""
    safe_types = {
        MessageType.CONVERSATION,
        MessageType.WARNING,
        MessageType.INSTRUCTION,
        MessageType.QUESTION,
        MessageType.JOKE,
        MessageType.QUOTE,
        MessageType.SELF_REFERENCE
    }
    return message_type in safe_types


def is_violation_message_type(message_type: MessageType) -> bool:
    """Check if message type is a violation."""
    return not is_safe_message_type(message_type) and message_type != MessageType.UNKNOWN


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

def example_usage():
    """Example showing the new data models in action."""

    # Example 1: "Don't be an idiot and click that link"
    # OLD MODEL (contradictory):
    #   is_toxic=True, context_safe=True  ← What does this mean???
    #
    # NEW MODEL (clear):
    analysis = MessageAnalysis(
        message_type=MessageType.WARNING,  # It IS a warning
        target=Target.NOBODY,              # Not directed at anyone
        intent=Intent.HELPFUL,             # Trying to help
        intensity=Intensity.MILD,          # Mild language
        specificity=Specificity.VAGUE,     # General warning
        confidence=ConfidenceLevel.HIGH,
        confidence_score=0.85,
        linguistic=LinguisticMarkers(
            contains_profanity=True,
            profanity_words=["idiot"],
            contains_negation=True,
            is_imperative=True
        ),
        mitigating=MitigatingFactors(
            is_sarcasm=False,
            is_joke=False
        ),
        reasoning="Warning message using strong language to emphasize danger",
        key_phrases=["Don't be", "click that link"],
        analyzer="toxicity_ai"
    )

    # Validation happens automatically
    print("Example 1: 'Don't be an idiot and click that link'")
    print(f"  Message Type: {analysis.message_type}")
    print(f"  Target: {analysis.target}")
    print(f"  Intent: {analysis.intent}")
    print(f"  Contains profanity: {analysis.linguistic.contains_profanity}")
    print(f"  Is safe: {analysis.is_safe()}")
    print("  ✅ No contradictions, validation passed!")
    print()

    # Example 2: "You're an idiot"
    # OLD MODEL:
    #   is_toxic=True, context_safe=False, score=6.0
    #
    # NEW MODEL (clearer):
    analysis2 = MessageAnalysis(
        message_type=MessageType.HARASSMENT,  # It IS harassment
        target=Target.USER,                   # Directed at specific user
        intent=Intent.HARMFUL,                # Trying to hurt
        intensity=Intensity.MODERATE,         # Moderately harsh
        specificity=Specificity.TARGETED,     # Directly targets user
        confidence=ConfidenceLevel.VERY_HIGH,
        confidence_score=0.92,
        linguistic=LinguisticMarkers(
            contains_profanity=True,
            profanity_words=["idiot"]
        ),
        mitigating=MitigatingFactors(),
        reasoning="Direct insult targeting specific user",
        key_phrases=["You're an idiot"],
        analyzer="toxicity_ai"
    )

    print("Example 2: 'You're an idiot'")
    print(f"  Message Type: {analysis2.message_type}")
    print(f"  Target: {analysis2.target}")
    print(f"  Intent: {analysis2.intent}")
    print(f"  Intensity: {analysis2.intensity}")
    print(f"  Is violation: {analysis2.is_violation()}")
    print("  ✅ Clear semantic meaning!")
    print()

    # Example 3: Test contradiction detection
    print("Example 3: Testing validation (should raise error)...")
    try:
        # This should FAIL: WARNING cannot have HARMFUL intent
        MessageAnalysis(
            message_type=MessageType.WARNING,
            target=Target.NOBODY,
            intent=Intent.HARMFUL,  # ← CONTRADICTION!
            intensity=Intensity.MILD,
            specificity=Specificity.VAGUE,
            confidence=ConfidenceLevel.HIGH,
            confidence_score=0.85,
            linguistic=LinguisticMarkers(),
            mitigating=MitigatingFactors(),
            reasoning="Test",
            analyzer="test"
        )
        print("  ❌ ERROR: Validation should have caught contradiction!")
    except ValueError as e:
        print(f"  ✅ Validation caught contradiction: {str(e)[:80]}...")

    print()
    print("Schema validation working correctly!")


if __name__ == "__main__":
    example_usage()
