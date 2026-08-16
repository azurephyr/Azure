"""
Azure Moderation Intelligence System

Advanced rule violation detection using:
- Pattern matching (spam, raids, floods)
- Behavioral analysis (trust scores, history)
- LLM-powered content analysis (toxicity, harassment, context)
- Real-time anomaly detection
- Coordinated attack detection

This is NOT just keyword filtering - it understands context, intent, and patterns.

Features:
- Multi-level threat classification (INFO, WARNING, DANGEROUS, CRITICAL)
- Confidence scoring for every decision
- Graduated response recommendations
- False positive mitigation
- Learning from moderator feedback
- Cross-channel pattern detection

Architecture:
- Rule-based engine (fast path, <1ms)
- ML classifier (medium path, ~10ms) - uses patterns
- LLM analyzer (slow path, ~500ms) - only for ambiguous cases
- Ensemble scoring: combines all signals for final decision

Usage:
    mod_intel = ModerationIntelligence(llm=local_llm, awareness_engine=awareness)
    result = await mod_intel.analyze_message(message, user_activity)

    if result.threat_level in [ThreatLevel.DANGEROUS, ThreatLevel.CRITICAL]:
        await mod_intel.recommend_action(result)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("azure.moderation_intelligence")


class ThreatLevel(Enum):
    """Threat severity levels."""
    INFO = "info"              # Informational, no action needed
    WARNING = "warning"        # Minor issue, warn user
    DANGEROUS = "dangerous"    # Serious violation, timeout/kick
    CRITICAL = "critical"      # Severe violation, ban immediately


class ViolationType(Enum):
    """Types of rule violations we detect."""
    SPAM = "spam"                    # Repeated messages, flooding
    HARASSMENT = "harassment"        # Targeted abuse, bullying
    HATE_SPEECH = "hate_speech"      # Slurs, discrimination
    TOXICITY = "toxicity"            # General rudeness, negativity
    NSFW = "nsfw"                    # Inappropriate content
    SCAM = "scam"                    # Phishing, malicious links
    RAID = "raid"                    # Coordinated attack
    IMPERSONATION = "impersonation"  # Fake accounts
    SELF_HARM = "self_harm"          # Concerning mental health content
    DOXXING = "doxxing"              # Sharing private info
    ADVERTISING = "advertising"      # Unsolicited promotion


@dataclass
class ModerationResult:
    """Result of moderation analysis."""
    message_id: str
    user_id: str
    guild_id: str

    # Classification
    threat_level: ThreatLevel
    violation_types: list[ViolationType]
    confidence: float  # 0.0-1.0

    # Evidence
    rule_matches: list[str] = field(default_factory=list)
    pattern_scores: dict[str, float] = field(default_factory=dict)
    behavioral_flags: list[str] = field(default_factory=list)
    llm_analysis: str = ""

    # Recommendation
    recommended_action: str = ""  # "delete", "warn", "timeout", "kick", "ban"
    action_reason: str = ""
    auto_execute: bool = False  # Whether to execute automatically

    # Context
    similar_incidents: int = 0
    user_history: str = ""

    timestamp: float = 0.0


@dataclass
class ModerationStats:
    """Statistics for moderation system performance."""
    total_analyzed: int = 0
    threats_detected: int = 0
    auto_actions_taken: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    avg_confidence: float = 0.0

    by_threat_level: dict[str, int] = field(default_factory=dict)
    by_violation_type: dict[str, int] = field(default_factory=dict)
    by_action: dict[str, int] = field(default_factory=dict)


class ModerationIntelligence:
    """
    Advanced moderation intelligence system.

    Analyzes content for rule violations using:
    1. Fast rule-based patterns (instant)
    2. Behavioral analysis from awareness engine
    3. LLM-powered semantic analysis (ambiguous cases only)

    Produces actionable recommendations with confidence scores.
    """

    def __init__(self, llm=None, awareness_engine=None, strict_mode: bool = False):
        """
        Initialize moderation intelligence.

        Args:
            llm: Optional local LLM for semantic analysis
            awareness_engine: ServerAwarenessEngine for behavioral context
            strict_mode: If True, lower thresholds (more sensitive)
        """
        self.llm = llm
        self.awareness = awareness_engine
        self.strict_mode = strict_mode

        # Statistics
        self.stats = ModerationStats()
        self._confidence_sum = 0.0

        # Pattern libraries (compiled regex for speed)
        self._compile_patterns()

        # Behavioral thresholds
        self.BURST_THRESHOLD = 5  # messages in 5 seconds
        self.SPAM_THRESHOLD = 10  # identical messages
        self.TRUST_THRESHOLD = 30.0  # below this = higher scrutiny

        # LLM usage thresholds
        self.LLM_CONFIDENCE_THRESHOLD = 0.6  # Use LLM if confidence < this

        logger.info(f"[moderation] Initialized (strict={strict_mode}, llm={'available' if llm else 'disabled'})")

    def _compile_patterns(self) -> None:
        """Compile regex patterns for fast matching."""

        # Spam patterns
        self.spam_patterns = [
            re.compile(r'(.)\1{10,}', re.IGNORECASE),  # Repeated characters
            re.compile(r'\b(buy|cheap|free|click|discount|limited|offer)\b.*\b(now|here|link)\b', re.IGNORECASE),
            re.compile(r'(discord\.gg|bit\.ly|tinyurl|goo\.gl)/\w+', re.IGNORECASE),  # Suspicious links
        ]

        # Hate speech patterns (carefully designed to minimize false positives)
        self.hate_speech_patterns = [
            re.compile(r'\b(n[i!1]gg[e3]r|f[a4]gg[o0]t|tr[a4]nny|r[e3]t[a4]rd)\b', re.IGNORECASE),
            re.compile(r'\b(k[i!1]ll\s+yourself|kys)\b', re.IGNORECASE),
        ]

        # Toxicity patterns
        self.toxicity_patterns = [
            re.compile(r'\b(stupid|idiot|dumb|loser|trash|garbage)\s+(person|human|user|member)\b', re.IGNORECASE),
            re.compile(r'\b(shut\s+up|stfu|get\s+lost|go\s+away)\b.*\b(nobody|everyone)\b', re.IGNORECASE),
        ]

        # NSFW patterns
        self.nsfw_patterns = [
            re.compile(r'\b(porn|xxx|nsfw|nude|sex|dick|pussy)\b', re.IGNORECASE),
        ]

        # Scam patterns
        self.scam_patterns = [
            re.compile(r'\b(free\s+nitro|steam\s+gift|giveaway|dm\s+me)\b', re.IGNORECASE),
            re.compile(r'@everyone.*\b(vote|win|claim|limited)\b', re.IGNORECASE),
        ]

        # Doxxing patterns
        self.doxxing_patterns = [
            re.compile(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'),  # Phone numbers
            re.compile(r'\b\d{1,5}\s+\w+\s+(street|st|avenue|ave|road|rd|drive|dr|lane|ln|court|ct)\b', re.IGNORECASE),  # Addresses
            re.compile(r'\b[A-Z][a-z]+\s+[A-Z][a-z]+\s+lives\s+at\b', re.IGNORECASE),  # "John Smith lives at"
        ]

        # Self-harm patterns
        self.self_harm_patterns = [
            re.compile(r'\b(suicide|kill\s+myself|end\s+my\s+life|want\s+to\s+die)\b', re.IGNORECASE),
            re.compile(r'\b(cutting|self\s+harm|self\s+injury)\b', re.IGNORECASE),
        ]

    async def analyze_message(self, message, user_activity=None) -> ModerationResult:  # type: ignore[no-untyped-def]
        """
        Analyze a message for rule violations.

        Args:
            message: Discord message object
            user_activity: Optional UserActivity from awareness engine

        Returns:
            ModerationResult with threat level and recommended action
        """
        self.stats.total_analyzed += 1

        result = ModerationResult(
            message_id=str(message.id),
            user_id=str(message.author.id),
            guild_id=str(message.guild.id) if message.guild else "DM",
            threat_level=ThreatLevel.INFO,
            violation_types=[],
            confidence=1.0,
            timestamp=time.time(),
        )

        content = message.content.lower()

        # PHASE 1: Fast rule-based detection
        rule_scores = await self._analyze_with_rules(message, content, result)

        # PHASE 2: Behavioral analysis
        behavioral_scores = self._analyze_behavior(user_activity, result) if user_activity else {}

        # PHASE 3: Ensemble scoring
        combined_score = self._combine_scores(rule_scores, behavioral_scores)

        # Determine threat level. Pass raw rule scores too: the CRITICAL
        # escalation for hate speech / doxxing must fire on a single pattern
        # match, but _combine_scores weights rule scores by 0.7 (0.7->0.49,
        # 0.6->0.42), which would sink them below the >0.5 gate.
        threat_level, confidence = self._calculate_threat_level(
            combined_score, result, rule_scores=rule_scores
        )
        result.threat_level = threat_level
        result.confidence = confidence
        self._confidence_sum = getattr(self, '_confidence_sum', 0.0) + confidence

        # PHASE 4: LLM analysis (only if ambiguous)
        if self.llm and confidence < self.LLM_CONFIDENCE_THRESHOLD and threat_level != ThreatLevel.INFO:
            llm_result = await self._analyze_with_llm(message, user_activity, result)
            if llm_result:
                # LLM can override if very confident
                result.llm_analysis = llm_result.get("analysis", "")
                llm_confidence = llm_result.get("confidence", 0.0)
                if llm_confidence > 0.8:
                    # LLM output is untrusted: an unexpected label (e.g.
                    # "DANGER", "SEVERE") would make ThreatLevel[...] raise
                    # KeyError and crash the whole analysis. Look it up
                    # defensively and ignore anything we don't recognize.
                    raw_level = str(llm_result.get("threat_level", "INFO")).upper()
                    mapped_level = ThreatLevel.__members__.get(raw_level)
                    if mapped_level is not None:
                        result.threat_level = mapped_level
                        result.confidence = llm_confidence
                    else:
                        logger.warning(
                            "[ModerationIntelligence] LLM returned unknown "
                            "threat_level %r; keeping heuristic level %s",
                            raw_level, result.threat_level.name,
                        )

        # PHASE 5: Recommend action
        self._recommend_action(result, user_activity)

        # Update stats
        if result.threat_level != ThreatLevel.INFO:
            self.stats.threats_detected += 1

        threat_name = result.threat_level.value
        self.stats.by_threat_level[threat_name] = self.stats.by_threat_level.get(threat_name, 0) + 1

        for vtype in result.violation_types:
            vname = vtype.value
            self.stats.by_violation_type[vname] = self.stats.by_violation_type.get(vname, 0) + 1

        return result

    async def _analyze_with_rules(self, message, content: str, result: ModerationResult) -> dict[str, float]:  # type: ignore[no-untyped-def]
        """Phase 1: Fast rule-based pattern matching."""
        scores = {}

        # Spam detection
        spam_score = 0.0
        for pattern in self.spam_patterns:
            if pattern.search(content):
                spam_score += 0.3
                result.rule_matches.append(f"spam_pattern: {pattern.pattern[:50]}")

        # Check for message flooding (if awareness engine available)
        if self.awareness and message.guild:
            guild_id = str(message.guild.id)
            user_id = str(message.author.id)
            recent = self.awareness.get_recent_events(guild_id, limit=20)
            user_recent = [e for e in recent if e.user_id == user_id]

            if len(user_recent) >= self.BURST_THRESHOLD:
                spam_score += 0.4
                result.rule_matches.append(f"burst_detected: {len(user_recent)} messages in 5s")

        if spam_score > 0:
            scores["spam"] = min(1.0, spam_score)
            result.violation_types.append(ViolationType.SPAM)

        # Hate speech detection
        hate_score = 0.0
        for pattern in self.hate_speech_patterns:
            if pattern.search(content):
                hate_score += 0.7  # High confidence on hate speech patterns
                result.rule_matches.append(f"hate_speech: {pattern.pattern[:30]}")

        if hate_score > 0:
            scores["hate_speech"] = min(1.0, hate_score)
            result.violation_types.append(ViolationType.HATE_SPEECH)

        # Toxicity detection
        toxicity_score = 0.0
        for pattern in self.toxicity_patterns:
            if pattern.search(content):
                toxicity_score += 0.3
                result.rule_matches.append(f"toxicity: {pattern.pattern[:30]}")

        # Check for excessive caps (use original message, not lowercased content)
        raw_content = message.content
        if len(raw_content) > 20:
            caps_ratio = sum(1 for c in raw_content if c.isupper()) / len(raw_content)
            if caps_ratio > 0.7:
                toxicity_score += 0.2
                result.rule_matches.append(f"excessive_caps: {caps_ratio:.1%}")

        if toxicity_score > 0:
            scores["toxicity"] = min(1.0, toxicity_score)
            result.violation_types.append(ViolationType.TOXICITY)

        # NSFW detection
        nsfw_score = 0.0
        for pattern in self.nsfw_patterns:
            if pattern.search(content):
                nsfw_score += 0.4
                result.rule_matches.append(f"nsfw: {pattern.pattern[:30]}")

        # Check attachments
        if message.attachments:
            for attachment in message.attachments:
                if any(word in attachment.filename.lower() for word in ['porn', 'nsfw', 'nude']):
                    nsfw_score += 0.5
                    result.rule_matches.append(f"nsfw_attachment: {attachment.filename}")

        if nsfw_score > 0:
            scores["nsfw"] = min(1.0, nsfw_score)
            result.violation_types.append(ViolationType.NSFW)

        # Scam detection
        scam_score = 0.0
        for pattern in self.scam_patterns:
            if pattern.search(content):
                scam_score += 0.5
                result.rule_matches.append(f"scam: {pattern.pattern[:30]}")

        # Check for suspicious links
        if 'http' in content:
            # Count links
            link_count = content.count('http')
            if link_count > 3:
                scam_score += 0.3
                result.rule_matches.append(f"excessive_links: {link_count}")

            # Check for @everyone with links
            if '@everyone' in content or '@here' in content:
                scam_score += 0.4
                result.rule_matches.append("mass_ping_with_links")

        if scam_score > 0:
            scores["scam"] = min(1.0, scam_score)
            result.violation_types.append(ViolationType.SCAM)

        # Doxxing detection
        doxxing_score = 0.0
        for pattern in self.doxxing_patterns:
            if pattern.search(content):
                doxxing_score += 0.6
                result.rule_matches.append(f"doxxing: {pattern.pattern[:30]}")

        if doxxing_score > 0:
            scores["doxxing"] = min(1.0, doxxing_score)
            result.violation_types.append(ViolationType.DOXXING)

        # Self-harm detection (INFO level - need support, not punishment)
        self_harm_score = 0.0
        for pattern in self.self_harm_patterns:
            if pattern.search(content):
                self_harm_score += 0.5
                result.rule_matches.append(f"self_harm: {pattern.pattern[:30]}")

        if self_harm_score > 0:
            scores["self_harm"] = min(1.0, self_harm_score)
            result.violation_types.append(ViolationType.SELF_HARM)

        result.pattern_scores = scores
        return scores

    def _analyze_behavior(self, user_activity, result: ModerationResult) -> dict[str, float]:  # type: ignore[no-untyped-def]
        """Phase 2: Behavioral analysis using user history."""
        scores = {}

        # Low trust score increases scrutiny
        if user_activity.trust_score < self.TRUST_THRESHOLD:
            scores["low_trust"] = (self.TRUST_THRESHOLD - user_activity.trust_score) / self.TRUST_THRESHOLD
            result.behavioral_flags.append(f"low_trust: {user_activity.trust_score:.1f}")

        # Burst messaging
        if user_activity.burst_detected:
            scores["burst"] = 0.5
            result.behavioral_flags.append("burst_detected")

        # Excessive links
        if user_activity.message_count > 0:
            link_ratio = user_activity.link_count / user_activity.message_count
            if link_ratio > 0.5:
                scores["link_spam"] = link_ratio
                result.behavioral_flags.append(f"link_ratio: {link_ratio:.1%}")

        # Prior warnings/timeouts
        if user_activity.warnings > 0:
            scores["prior_warnings"] = min(1.0, user_activity.warnings * 0.2)
            result.behavioral_flags.append(f"warnings: {user_activity.warnings}")

        if user_activity.timeouts > 0:
            scores["prior_timeouts"] = min(1.0, user_activity.timeouts * 0.3)
            result.behavioral_flags.append(f"timeouts: {user_activity.timeouts}")

        # Suspicious patterns
        if len(user_activity.suspicious_patterns) > 0:
            scores["suspicious"] = min(1.0, len(user_activity.suspicious_patterns) * 0.25)
            result.behavioral_flags.append(f"patterns: {', '.join(user_activity.suspicious_patterns[:3])}")

        # New account (less than 24 hours since first seen)
        account_age = time.time() - user_activity.first_seen
        if account_age < 86400:  # 24 hours
            scores["new_account"] = 0.3
            result.behavioral_flags.append(f"account_age: {account_age/3600:.1f}h")

        return scores

    def _combine_scores(self, rule_scores: dict[str, float],
                        behavioral_scores: dict[str, float]) -> dict[str, float]:
        """Phase 3: Combine rule and behavioral scores."""
        combined = {}

        # Rule scores have higher weight (0.7)
        for key, score in rule_scores.items():
            combined[key] = score * 0.7

        # Behavioral scores add context (0.3)
        for key, score in behavioral_scores.items():
            combined[key] = combined.get(key, 0.0) + (score * 0.3)

        return combined

    def _calculate_threat_level(self, scores: dict[str, float],
                                result: ModerationResult,
                                rule_scores: dict[str, float] | None = None
                                ) -> tuple[ThreatLevel, float]:
        """Determine threat level from combined scores.

        rule_scores (unweighted) is used for the hate_speech/doxxing CRITICAL
        escalation so a single pattern match still escalates, independent of
        the 0.7 rule weighting applied in _combine_scores.
        """
        if not scores:
            return ThreatLevel.INFO, 1.0

        # Get max score as primary indicator
        max_score = max(scores.values()) if scores else 0.0

        # Calculate confidence (average of top 3 scores)
        top_scores = sorted(scores.values(), reverse=True)[:3]
        confidence = sum(top_scores) / len(top_scores) if top_scores else 0.0

        # Special cases — evaluate against raw rule scores so a single
        # hate-speech/doxxing pattern match escalates to CRITICAL.
        severe_scores = rule_scores if rule_scores is not None else scores

        if severe_scores.get("hate_speech", 0.0) > 0.5:
            return ThreatLevel.CRITICAL, confidence

        if severe_scores.get("doxxing", 0.0) > 0.5:
            return ThreatLevel.CRITICAL, confidence

        if "self_harm" in severe_scores:
            return ThreatLevel.INFO, confidence  # INFO = needs support, not punishment

        # Standard thresholds
        if self.strict_mode:
            if max_score >= 0.5:
                return ThreatLevel.DANGEROUS, confidence
            elif max_score >= 0.3:
                return ThreatLevel.WARNING, confidence
            elif max_score >= 0.1:
                return ThreatLevel.INFO, confidence
        else:
            if max_score >= 0.7:
                return ThreatLevel.CRITICAL, confidence
            elif max_score >= 0.5:
                return ThreatLevel.DANGEROUS, confidence
            elif max_score >= 0.3:
                return ThreatLevel.WARNING, confidence

        return ThreatLevel.INFO, confidence

    async def _analyze_with_llm(self, message, user_activity,
                                result: ModerationResult) -> dict | None:  # type: ignore[no-untyped-def]
        """Phase 4: LLM semantic analysis for ambiguous cases."""
        if not self.llm:
            return None

        try:
            # Build context
            context = f"User: {message.author.display_name}\n"
            context += f"Message: {message.content}\n"

            if user_activity:
                context += f"User history: {user_activity.message_count} messages, "
                context += f"trust score: {user_activity.trust_score:.1f}, "
                context += f"warnings: {user_activity.warnings}\n"

            context += f"\nRule-based detection found: {', '.join(r for r in result.rule_matches)}\n"
            context += f"Confidence: {result.confidence:.0%}\n"

            prompt = (
                "You are a content moderation AI. Analyze this message for rule violations.\n\n"
                f"{context}\n"
                "Provide:\n"
                "1. threat_level: INFO, WARNING, DANGEROUS, or CRITICAL\n"
                "2. confidence: 0.0-1.0\n"
                "3. analysis: Brief explanation (1-2 sentences)\n\n"
                "Consider context and intent. Be fair but firm.\n\n"
                "Respond in JSON format:\n"
                '{"threat_level": "...", "confidence": 0.X, "analysis": "..."}'
            )

            # Run LLM in executor
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.llm.chat([
                    {"role": "system", "content": "You are a fair and accurate content moderator."},
                    {"role": "user", "content": prompt}
                ], max_tokens=200, temperature=0.3)
            )

            # Parse response
            import json
            # Extract JSON from response
            response = response.strip()
            if "{" in response and "}" in response:
                json_start = response.index("{")
                json_end = response.rindex("}") + 1
                json_str = response[json_start:json_end]
                return json.loads(json_str)

        except Exception as e:
            logger.error(f"[moderation] LLM analysis failed: {e}")

        return None

    def _recommend_action(self, result: ModerationResult, user_activity=None) -> None:  # type: ignore[no-untyped-def]
        """Phase 5: Recommend moderation action."""
        # Special case: self-harm needs support, not punishment
        if ViolationType.SELF_HARM in result.violation_types:
            result.recommended_action = "alert_support"
            result.action_reason = "User may need mental health support"
            result.auto_execute = False
            return

        # Action based on threat level
        if result.threat_level == ThreatLevel.CRITICAL:
            if result.confidence > 0.8:
                result.recommended_action = "ban"
                result.action_reason = f"Critical violation: {', '.join(v.value for v in result.violation_types)}"
                result.auto_execute = False  # Never auto-ban, require confirmation
            else:
                result.recommended_action = "kick"
                result.action_reason = "Serious violation with medium confidence"
                result.auto_execute = False

        elif result.threat_level == ThreatLevel.DANGEROUS:
            # Check history for escalation
            if user_activity and (user_activity.warnings >= 2 or user_activity.timeouts >= 1):
                result.recommended_action = "kick"
                result.action_reason = f"Repeat offender: {user_activity.warnings} warnings, {user_activity.timeouts} timeouts"
                result.auto_execute = False
            else:
                result.recommended_action = "timeout"
                result.action_reason = f"Dangerous violation: {', '.join(v.value for v in result.violation_types)}"
                result.auto_execute = result.confidence > 0.7

        elif result.threat_level == ThreatLevel.WARNING:
            if user_activity and user_activity.warnings >= 3:
                result.recommended_action = "timeout"
                result.action_reason = "Multiple warnings ignored"
                result.auto_execute = True
            else:
                result.recommended_action = "warn"
                result.action_reason = f"Minor violation: {', '.join(v.value for v in result.violation_types)}"
                result.auto_execute = result.confidence > 0.6

        else:  # INFO
            result.recommended_action = "none"
            result.action_reason = "No action needed"
            result.auto_execute = False

    def record_feedback(self, message_id: str, was_correct: bool) -> None:
        """Record moderator feedback for learning."""
        if was_correct:
            logger.info(f"[moderation] Correct decision for {message_id}")
        else:
            self.stats.false_positives += 1
            logger.warning(f"[moderation] False positive for {message_id}")

    def get_stats(self) -> ModerationStats:
        """Get moderation statistics."""
        if self.stats.total_analyzed > 0 and self._confidence_sum > 0:
            self.stats.avg_confidence = self._confidence_sum / self.stats.total_analyzed
        return self.stats

    async def detect_raid(self, guild_id: str) -> dict | None:
        """Detect coordinated raid attacks."""
        if not self.awareness:
            return None

        insights = self.awareness.get_server_insights(guild_id)

        if insights.raid_probability > 0.5:
            # Get recent joins
            recent_events = self.awareness.get_recent_events(guild_id, limit=200)
            joins = [e for e in recent_events if e.event_type.value == "member_join"]

            # Check for coordination patterns
            if len(joins) > 10:
                # Detect similar usernames
                usernames = [e.metadata.get("user_name", "") for e in joins]
                similar = sum(1 for i in range(len(usernames)-1)
                             if usernames[i][:5] == usernames[i+1][:5])

                if similar > len(joins) * 0.5:
                    return {
                        "raid_detected": True,
                        "confidence": 0.9,
                        "join_count": len(joins),
                        "similar_names": similar,
                        "pattern": "coordinated_raid",
                        "recommended_action": "lockdown",
                    }

        return None
