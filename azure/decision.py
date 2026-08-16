from dataclasses import dataclass

from .moderation.policy import ActionType, ModerationPhase, ModerationPolicy


@dataclass
class Decision:
    """A moderation decision with full reasoning."""
    action: 'ActionType'
    confidence: float
    explanation: str
    human_review: bool
    risk_profile: dict
    signals: dict
    reason: str  # short machine reason for logging

    def to_dict(self) -> dict:
        return {
            "action": self.action.name,
            "confidence": round(self.confidence, 3),
            "explanation": self.explanation,
            "human_review": self.human_review,
            "risk_profile": self.risk_profile,
            "reason": self.reason,
        }


class DecisionEngine:
    """Intelligent decision-making based on risk, confidence, and policy.

    Principle: least intrusive action that solves the problem.
    Principle: when uncertain, log and notify (don't act).
    Principle: when confident and risk is high, act fast.
    """

    def __init__(self, policy: ModerationPolicy):
        self.policy = policy

    # ------------------------------------------------------------------
    # Single-message decision
    # ------------------------------------------------------------------
    def decide(self, content_severity: float, content_confidence: float,
               content_category: str, behavioral_signals: dict,
               temporal_signals: dict, risk_profile: dict,
               phase: ModerationPhase, is_whitelisted: bool = False,
               author_name: str = "") -> Decision:
        """Make a moderation decision for a single message."""

        if is_whitelisted:
            return Decision(
                action=ActionType.NONE,
                confidence=1.0,
                explanation="User is whitelisted. No action.",
                human_review=False,
                risk_profile=risk_profile,
                signals={"content": content_category, "behavioral": behavioral_signals, "temporal": temporal_signals},
                reason="whitelisted",
            )

        total_risk = risk_profile.get("total_risk", 0.0)
        confidence = risk_profile.get("confidence", 0.0)
        user_risk = risk_profile.get("user_risk", 0.0)
        situation_risk = risk_profile.get("situation_risk", 0.0)

        action = ActionType.NONE
        human_review = False
        reason = "no_threat"
        explanation = "No significant risk detected."

        # 1. RAID / SITUATION THREAT (highest priority)
        if situation_risk > 0.85 and confidence > 0.7:
            action = ActionType.TIMEOUT
            reason = "situation_threat"
            if temporal_signals.get("is_raid", False):
                reason = "raid_detected"
            explanation = (
                f"RAID/SITUATION ALERT: {temporal_signals.get('explanation', 'high situation risk')} "
                f"Risk: {total_risk:.0%}. Acting to protect server."
            )

        # 2. HIGH RISK USER (repeat offender)
        elif user_risk > 0.75 and total_risk > 0.6:
            action = ActionType.TIMEOUT
            reason = "repeat_offender"
            explanation = (
                f"User {author_name} has elevated risk ({user_risk:.0%}) with "
                f"{behavioral_signals.get('offense_count_24h', 0)} recent offenses. "
                f"Message risk: {total_risk:.0%}."
            )

        # 3. HIGH SEVERITY CONTENT
        elif total_risk > 0.8 and confidence > 0.75:
            if content_category == "scam":
                action = ActionType.TIMEOUT
                reason = "scam_confident"
            elif content_category == "spam":
                action = ActionType.DELETE
                reason = "spam_confident"
            elif content_category == "toxicity":
                action = ActionType.DELETE
                reason = "toxicity_confident"
            else:
                action = ActionType.WARN
                reason = "high_risk_uncertain"
            explanation = (
                f"High-risk {content_category} detected. "
                f"Risk: {total_risk:.0%}, Confidence: {confidence:.0%}. "
                f"{behavioral_signals.get('explanation', '')}"
            )

        # 4. MEDIUM RISK
        elif total_risk > 0.5 and confidence > 0.5:
            if content_category == "scam":
                action = ActionType.DELETE
                reason = "scam_suspected"
            elif content_category == "spam":
                action = ActionType.DELETE
                reason = "spam_suspected"
            else:
                action = ActionType.WARN
                reason = "medium_risk"
            explanation = (
                f"Medium-risk {content_category} detected. "
                f"Risk: {total_risk:.0%}, Confidence: {confidence:.0%}."
            )
            human_review = True

        # 5. LOW RISK but suspicious behavior
        elif behavioral_signals.get("anomaly_score", 0) > 0.6:
            action = ActionType.LOG
            reason = "behavioral_anomaly"
            explanation = (
                f"Behavioral anomaly detected: {behavioral_signals.get('explanation', '')} "
                f"Risk: {total_risk:.0%}."
            )
            human_review = True

        # Phase clamping
        action = self._clamp_for_phase(action, phase, explanation)
        if phase == ModerationPhase.DRY_RUN:
            human_review = True

        # Confidence guard for destructive actions
        if action in [ActionType.BAN, ActionType.KICK, ActionType.TIMEOUT] and confidence < 0.5:
            action = ActionType.WARN
            explanation += " [Confidence too low for strong action. ActionType degraded to WARN.]"
            human_review = True

        return Decision(
            action=action,
            confidence=confidence,
            explanation=explanation,
            human_review=human_review,
            risk_profile=risk_profile,
            signals={"content": content_category, "behavioral": behavioral_signals, "temporal": temporal_signals},
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Situation (multi-message) decision
    # ------------------------------------------------------------------
    def decide_situation(self, temporal_signals: dict, risk_profile: dict,
                         phase: ModerationPhase, involved_users: list[str]) -> Decision:
        """Make a decision for a multi-message situation (raid, spam wave)."""
        raid_probability = temporal_signals.get("raid_probability", 0.0)
        is_raid = temporal_signals.get("is_raid", False)

        if not is_raid and raid_probability < 0.6:
            return Decision(
                action=ActionType.NONE,
                confidence=0.5,
                explanation="No significant situation threat.",
                human_review=False,
                risk_profile=risk_profile,
                signals={"temporal": temporal_signals},
                reason="no_situation",
            )

        if raid_probability > 0.9:
            action = ActionType.TIMEOUT
            reason = "critical_raid"
        elif raid_probability > 0.7:
            action = ActionType.DELETE
            reason = "probable_raid"
        else:
            action = ActionType.WARN
            reason = "suspected_raid"

        explanation = (
            f"SITUATION: {temporal_signals.get('explanation', 'Coordinated activity detected')}. "
            f"Raid probability: {raid_probability:.0%}. "
            f"Users involved: {len(involved_users)}. "
            f"Messages: {temporal_signals.get('matched_messages', 0)}."
        )

        action = self._clamp_for_phase(action, phase, explanation)
        if phase == ModerationPhase.DRY_RUN:
            explanation = f"[DRY RUN] Would have: {action.name}. {explanation}"

        return Decision(
            action=action,
            confidence=raid_probability,
            explanation=explanation,
            human_review=(raid_probability < 0.8),
            risk_profile=risk_profile,
            signals={"temporal": temporal_signals},
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _clamp_for_phase(self, action: 'ActionType', phase: ModerationPhase, explanation: str) -> 'ActionType':
        """Clamp action based on moderation phase."""
        if action == ActionType.NONE:
            return ActionType.NONE
        if phase == ModerationPhase.DRY_RUN:
            return ActionType.LOG

        if phase == ModerationPhase.REACTIVE_LIMITED and action in [ActionType.BAN, ActionType.KICK]:
            return ActionType.TIMEOUT

        return action
