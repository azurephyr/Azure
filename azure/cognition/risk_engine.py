"""
RiskEngine — Phase 6 of the cognitive pipeline.

Classifies action risk: LOW / MEDIUM / HIGH / CRITICAL
and determines whether user confirmation is required.

CRITICAL actions (require explicit confirmation):
  - bans, mass moderation
  - deletions of channels/roles
  - permission rewrites (especially @everyone)
  - irreversible state changes
"""

from __future__ import annotations

import re

from .cognitive_state import Mode, Risk

RISK_ORDER = {Risk.LOW: 0, Risk.MEDIUM: 1, Risk.HIGH: 2, Risk.CRITICAL: 3}


# ---------------------------------------------------------------------------
# Risk signatures
# ---------------------------------------------------------------------------

# CRITICAL risk patterns — these MUST be confirmed by the user
CRITICAL_PATTERNS = [
    # Bans
    (r"(?:ban|boot)\s+(?:everyone|all\s+(?:the\s+)?members?|here)", Risk.CRITICAL),
    (r"(?:mass|bulk)\s+ban", Risk.CRITICAL),
    # Deletion
    (r"delete\s+(?:all\s+)?(?:channels?|roles?|categories?)", Risk.CRITICAL),
    (r"(?:delete|purge|clear)\s+(?:the\s+)?(?:(?!this)[^\s]+\s+)?(entire|whole|all)", Risk.CRITICAL),
    # Permission rewrites
    (r"(?:set|give|grant)\s+@everyone\s+(?:admin|administrator|manage)", Risk.CRITICAL),
    (r"(?:remove|strip)\s+(?:all\s+)?permissions?\s+from\s+(?:everyone|all)", Risk.CRITICAL),
    # Dangerous automation
    (r"(?:auto|automatically)\s+(?:ban|kick|delete|remove)\s+(?:everyone|all)", Risk.CRITICAL),
    # Data loss
    (r"(?:reset|wipe|nuke|destroy|ruin)\s+(?:the\s+)?(?:server|guild|everything)", Risk.CRITICAL),
    # Owner-level actions
    (r"(?:transfer|give)\s+(?:ownership|owner)", Risk.CRITICAL),
    # Cross-server
    (r"(?:all\s+servers?|every\s+server)", Risk.CRITICAL),
]

# HIGH risk patterns — escalated but not catastrophic
HIGH_PATTERNS = [
    # Individual destructive actions
    (r"(?:ban|kick)\s+(?:<@\!?\d+>|[@\w]+)", Risk.HIGH),   # ban/kick a specific user
    (r"timeout\s+(?:<@\!?\d+>|[@\w]+)", Risk.HIGH),
    (r"delete\s+(?:channel|role)\s+(?!template)", Risk.HIGH),
    (r"remove\s+(?:everyone|all)\s+from", Risk.HIGH),
    # Role manipulation
    (r"(?:give|assign|set)\s+(?:admin|administrator)", Risk.HIGH),
    (r"(?:create|add)\s+(?:admin|administrator)\s+(?:role|permission)", Risk.HIGH),
    # Bulk operations
    (r"(?:mass|bulk|batch)\s+(?:kick|delete|moderat)", Risk.HIGH),
    # Automation of moderation
    (r"(?:auto|automatically)\s+(?:kick|ban|timeout|warn)", Risk.HIGH),
    # Widget / integration changes
    (r"(?:disable|turn\s+off)\s+(?:widget|invites?|widget)", Risk.HIGH),
]

# MEDIUM risk patterns — noticeable but reversible
MEDIUM_PATTERNS = [
    (r"(?:create|add|make)\s+(?:channel|role|category)", Risk.MEDIUM),
    (r"(?:edit|update|change)\s+(?:channel|role|category)\s+", Risk.MEDIUM),
    (r"(?:set|change)\s+(?:nickname|status|topic)", Risk.MEDIUM),
    (r"(?:create|delete)\s+invite", Risk.MEDIUM),
    (r"(?:mute|deafen)\s+", Risk.MEDIUM),
    (r"warn\s+(?:<@\!?\d+>|[\w]+)", Risk.MEDIUM),
    (r"(?:save|load)\s+template", Risk.MEDIUM),
]


class RiskEngine:
    """
    Classifies the risk level of an action.

    Risk is assessed based on:
      - Destructiveness / irreversibility
      - Scope (individual vs. mass)
      - Permission level required
      - Speed of execution
      - Scope of consequences
    """

    def classify(
        self,
        message: str,
        modes: list[Mode],
        params: dict | None = None,
        requires_llm: bool = True,
        _return_confidence: bool = False,
    ) -> tuple[Risk, list[str], bool, str] | tuple[Risk, list[str], bool, str, float]:
        """
        Classify risk and determine confirmation requirements.

        Args:
            message: Raw user message
            modes: Active modes from ModeClassifier
            params: Extracted parameters
            requires_llm: Whether this will trigger an LLM call

        Returns:
            (risk_level, risk_flags, confirmation_required, confirmation_message)
        """
        raw = message.strip()
        lower = raw.lower()
        params = params or {}
        flags: list[str] = []
        confirmation_required = False
        confirmation_message = ""

        risk = Risk.LOW

        # === MODE-BASED RISK ===
        if Mode.ADMIN in modes:
            # Admin actions start at at least MEDIUM
            risk = max(risk, Risk.MEDIUM, key=lambda r: RISK_ORDER.get(r, 0))

        if Mode.AUTOMATION in modes:
            flags.append("automation")
            risk = max(risk, Risk.MEDIUM, key=lambda r: RISK_ORDER.get(r, 0))

        # === PATTERN MATCHING ===
        # Check CRITICAL patterns first (highest)
        for pat, _pat_risk in CRITICAL_PATTERNS:
            if re.search(pat, lower):
                risk = max(risk, Risk.CRITICAL, key=lambda r: RISK_ORDER.get(r, 0))
                flags.append(f"critical_pattern: {pat[:40]}")
                break

        for pat, pat_risk in HIGH_PATTERNS:
            if re.search(pat, lower):
                risk = max(risk, Risk.HIGH, key=lambda r: RISK_ORDER.get(r, 0))
                if pat_risk == Risk.HIGH:
                    flags.append(f"high_risk: {pat[:40]}")

        for pat, _pat_risk in MEDIUM_PATTERNS:
            if re.search(pat, lower):
                risk = max(risk, Risk.MEDIUM, key=lambda r: RISK_ORDER.get(r, 0))

        # === PARAMETER-BASED FLAGS ===
        # Specific user targets → HIGH
        if params.get("member_id") or params.get("member"):
            flags.append("specific_user_target")
            risk = max(risk, Risk.HIGH, key=lambda r: RISK_ORDER.get(r, 0))

        # Multiple targets → HIGH
        names = params.get("names", [])
        if isinstance(names, list) and len(names) >= 3:
            flags.append(f"multiple_targets: {len(names)}")
            risk = max(risk, Risk.HIGH, key=lambda r: RISK_ORDER.get(r, 0))

        # === SCOPE FLAGS ===
        scope_signals = ["all", "everyone", "every", "whole", "entire"]
        if any(s in lower for s in scope_signals):
            flags.append("wide_scope")
            risk = max(risk, Risk.MEDIUM, key=lambda r: RISK_ORDER.get(r, 0))

        # === REVERSIBILITY FLAGS ===
        irreversible = ["delete", "ban", "kick", "purge", "reset", "wipe", "nuke"]
        reversible   = ["create", "add", "make", "set", "assign"]

        has_irrevocable = any(w in lower for w in irreversible)
        has_revocable   = any(w in lower for w in reversible)

        if has_irrevocable and not has_revocable:
            flags.append("irreversible_action")
            risk = max(risk, Risk.MEDIUM, key=lambda r: RISK_ORDER.get(r, 0))
        if has_irrevocable and any(w in lower for w in ["all", "every", "everyone"]):
            flags.append("widespread_irreversible")
            risk = max(risk, Risk.HIGH, key=lambda r: RISK_ORDER.get(r, 0))

        # === CONFIRMATION REQUIREMENT ===
        if risk == Risk.CRITICAL:
            confirmation_required = True
            confirmation_message = self._build_critical_confirmation(lower)
        elif risk == Risk.HIGH and (has_irrevocable or Mode.ADMIN in modes):
            # High risk + destructive → confirm
            confirmation_required = True
            confirmation_message = self._build_high_risk_confirmation(lower)

        # Confidence: CRITICAL/HIGH pattern matched = very confident, LOW with no flags = confident
        if risk == Risk.CRITICAL:
            confidence = min(0.99, 0.90 + len(flags) * 0.02)
        elif risk == Risk.HIGH:
            confidence = min(0.95, 0.80 + len(flags) * 0.03)
        elif risk == Risk.MEDIUM:
            confidence = min(0.90, 0.70 + len(flags) * 0.05)
        else:
            confidence = 0.85  # LOW risk is confident when no danger signals

        if _return_confidence:
            return risk, flags, confirmation_required, confirmation_message, confidence
        return risk, flags, confirmation_required, confirmation_message

    def _build_critical_confirmation(self, lower: str) -> str:
        """Build a confirmation message for CRITICAL actions."""
        if "ban" in lower or "kick" in lower:
            return (
                "⚠️ **This action may affect multiple members.** "
                "Please confirm: type `yes` to proceed or `no` to cancel."
            )
        if "delete" in lower:
            return (
                "⚠️ **Deletion is permanent and cannot be undone.** "
                "Type `yes` to confirm or `no` to cancel."
            )
        if "permission" in lower or "admin" in lower:
            return (
                "⚠️ **This modifies server-wide permissions.** "
                "Type `yes` to confirm or `no` to cancel."
            )
        return (
            "⚠️ **This is a high-risk action.** "
            "Please type `yes` to confirm or `no` to cancel."
        )

    def _build_high_risk_confirmation(self, lower: str) -> str:
        """Build a confirmation message for HIGH risk actions."""
        if "ban" in lower or "kick" in lower:
            return (
                "⚠️ **You're about to take action against a member.** "
                "Confirm with `yes`, or `no` to cancel."
            )
        if "role" in lower and "admin" in lower:
            return (
                "⚠️ **Admin roles affect server security.** "
                "Confirm with `yes`, or `no` to cancel."
            )
        if "template" in lower:
            return (
                "⚠️ **This will save or load a server template.** "
                "Confirm with `yes`, or `no` to cancel."
            )
        return (
            "⚠️ **This action makes server changes.** "
            "Type `yes` to confirm or `no` to cancel."
        )

    def requires_confirmation(self, risk: Risk) -> bool:
        """Does this risk level always require confirmation?"""
        return risk == Risk.CRITICAL
