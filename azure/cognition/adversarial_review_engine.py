"""
AdversarialReviewEngine — Upgrade 4: Adversarial Review

This is NOT a passive checklist. It actively tries to BREAK the response.

Standard review asks: "Did we pass this check?"
Adversarial review asks: "Can I find a way to prove this response is wrong?"

New checklist (replaces / extends the standard 5-point review):
  1. Am I misunderstanding intent?
     → Actively search for alternative interpretations
     → Check if memory context contradicts the response
  2. Could this action cause harm?
     → Simulate worst-case outcomes
     → Check for scope creep, unintended consequences
  3. Am I missing critical context?
     → Cross-reference with user memory, server memory, conversation history
     → Check if prior_plans conflict with current action
  4. Is there a safer alternative?
     → Propose a less risky version of the action
     → Check if a simpler response would work
  5. What assumptions am I making?
     → Explicitly name every assumption
     → Check if any assumption could be false
  6. [Adversarial] Could this be manipulated?
     → Is the user trying to trick the bot into something?
     → Check for jailbreak attempts, prompt injection
  7. [Adversarial] Is this proportionate?
     → Does the response intensity match the request intensity?
     → Overkill responses are also a failure

If any adversarial check fails, a "safer" response is generated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .cognitive_state import CognitiveState, Mode, Risk


@dataclass
class AdversarialCheck:
    """Result of a single adversarial challenge."""
    challenge: str          # What we're challenging
    passed: bool
    concern: str = ""       # What specifically worried us
    safer_alternative: str = ""  # What we should say/do instead


class AdversarialReviewEngine:
    """
    Active adversarial reviewer — challenges outputs, not just checks them.

    This is the "red team" layer. It looks for:
      - Hidden manipulation attempts
      - Context gaps that could lead to wrong responses
      - Proportionality failures
      - Scope creep in automated actions
      - Contradictions with prior context
    """

    # Manipulation/jailbreak patterns to watch for
    MANIPULATION_PATTERNS = [
        r"(?:ignore|forget)\s+(?:all|previous|prior|above)\s+(?:instructions?|rules?|prompt)",
        r"(?:you\s+are\s+now|pretend\s+you\s+are|switch\s+to)\s+\w+\s+mode",
        r"^(?:lol){3,}",   # "lololol" — someone laughing off a serious request
        r"(?:just\s+)?(?:kidding|joking)\s*,?\s*(?:im?)?\s*(?:not|don'?t)\s+(?:serious|lying)",
        r"do\s+it\s+(?:anyway|regardless)\s*,?\s*no\s+(?:wait|actually)",
        r"(?:type\s+`yes`|confirm)\s*,?\s*(?:i\s+know|trust\s+me)",
        r"^\s*\:?\s*\(?\s*hidden\s*\)?",  # "[hidden]" style markers
    ]

    # Words that signal emotional manipulation attempts
    EMOTIONAL_MANIPULATION = [
        "just", "literally", "honestly", "frankly",
        "to be honest", "trust me", "believe me",
    ]

    def review(self, state: CognitiveState, response: str) -> list[AdversarialCheck]:
        """
        Run all adversarial checks against the response.

        Returns:
            List of AdversarialCheck results
        """
        checks: list[AdversarialCheck] = []
        # Note: per-method `.lower()` is computed inside each check_* below.
        # Earlier versions pre-computed lower/msg_lower here without using
        # them; per-method computation eliminates duplicate work AND prevents
        # refactoring hazards where an inline `.lower()` could diverge from
        # a pre-computed one. See FIX-7 in the RC1 audit report.

        # --- Check 1: Intent misunderstanding ---
        checks.append(self._check_intent_misunderstanding(state, response))

        # --- Check 2: Potential harm ---
        checks.append(self._check_potential_harm(state, response))

        # --- Check 3: Missing context ---
        checks.append(self._check_missing_context(state, response))

        # --- Check 4: Safer alternatives ---
        checks.append(self._check_safer_alternative(state, response))

        # --- Check 5: Assumptions ---
        checks.append(self._check_assumptions(state, response))

        # --- Check 6: Manipulation ---
        checks.append(self._check_manipulation(state, response))

        # --- Check 7: Proportionality ---
        checks.append(self._check_proportionality(state, response))

        return checks

    # -------------------------------------------------------------------------
    # Check 1: Intent misunderstanding
    # -------------------------------------------------------------------------

    def _check_intent_misunderstanding(self, state: CognitiveState, response: str) -> AdversarialCheck:
        """
        Could the user mean something different from what we understood?

        Example: "ban everyone" → user might mean "ban everyone who violates rules"
                  not "mass ban all members"
        """
        passed = True
        concern = ""
        alternative = ""

        lower = state.raw_message.lower()

        # "do what we discussed earlier" without prior context
        if any(phrase in lower for phrase in ["earlier", "discussed", "before", "previous", "that time"]) and not state.conversation_history and not state.prior_plans:
                passed = False
                concern = "User references prior context but no conversation history available — we don't know what 'earlier' means"
                alternative = "Ask: 'What did we discuss? Can you remind me?'"

        # "the mods are too aggressive" — are we misunderstanding?
        if "mod" in lower and any(w in lower for w in ["aggressive", "strict", "too much", "over"]) and Mode.ADMIN not in state.modes and Mode.ANALYSIS not in state.modes:
                passed = False
                concern = "Moderation concern detected but not routed to ADMIN/ANALYSIS — we might be responding casually when action is needed"
                alternative = "Route to ADMIN/ANALYSIS and ask what specific mod actions caused the problem"

        # Short confirmation messages ("yes", "ok", "sure") — could be confirmation of a harmful action
        if state.raw_message.strip().lower() in ("yes", "y", "yeah", "yep", "do it", "go ahead") and state.risk in (Risk.HIGH, Risk.CRITICAL) and not state.execution_result:
                passed = False
                concern = "User confirmed something but no action was executed — possible missed CRITICAL/HIGH risk action"
                alternative = "Re-confirm: 'Just to confirm, you want me to [action]? This cannot be undone.'"

        return AdversarialCheck(
            challenge="Intent Misunderstanding",
            passed=passed,
            concern=concern,
            safer_alternative=alternative,
        )

    # -------------------------------------------------------------------------
    # Check 2: Potential harm
    # -------------------------------------------------------------------------

    def _check_potential_harm(self, state: CognitiveState, response: str) -> AdversarialCheck:
        """
        Could this response or action cause unintended harm?

        Harm categories:
          - Wrong server targeted (DMs, wrong guild)
          - Scope creep (one action → many affected)
          - Collateral damage (affecting non-targets)
        """
        passed = True
        concern = ""
        alternative = ""

        lower_msg = state.raw_message.lower()

        # Scope creep check
        scope_signals = ["all", "every", "everyone", "whole", "entire"]
        if any(s in lower_msg for s in scope_signals) and (Mode.ADMIN in state.modes or Mode.TOOL in state.modes):
                passed = False
                concern = "Wide-scope admin request detected — action could affect many people unexpectedly"
                alternative = "Ask for specific targets instead of 'all' before executing"

        # "delete" without mentioning what — response might be too eager
        if "delete" in lower_msg and not state.missing_info and not response:
            passed = False
            concern = "Delete request without asking what's being deleted — we might act on partial information"
            alternative = "Confirm: 'Delete what exactly? Channel name, role, message type?'"

        # Responding with server-level changes based on a single casual comment
        if Mode.ADMIN in state.modes and state.confidence_is_low(0.75) and not state.confirmation_required:
                passed = False
                concern = f"ADMIN mode with LOW confidence ({state.overall_confidence:.2f}) and no confirmation required — risk of acting on ambiguous input"
                alternative = "Require explicit confirmation or ask for clarification"

        return AdversarialCheck(
            challenge="Potential Harm",
            passed=passed,
            concern=concern,
            safer_alternative=alternative,
        )

    # -------------------------------------------------------------------------
    # Check 3: Missing context
    # -------------------------------------------------------------------------

    def _check_missing_context(self, state: CognitiveState, response: str) -> AdversarialCheck:
        """
        Are we missing information that would change our response?

        Checks:
          - Does prior_plans conflict with this action?
          - Does user_memory contradict their request?
          - Is the server_memory relevant and used?
        """
        passed = True
        concern = ""
        alternative = ""

        lower_msg = state.raw_message.lower()

        # Prior plans conflict
        if state.prior_plans:
            last_plan = state.prior_plans[-1] if state.prior_plans else ""
            # Simple heuristic: if user now wants to undo/reverse something
            if any(w in lower_msg for w in ["undo", "revert", "change", "cancel", "nevermind"]) and "undo" not in lower_msg and last_plan:
                    # They might be contradicting their own plan
                    passed = False
                    concern = f"User request may contradict prior plan: '{last_plan}'"
                    alternative = "Acknowledge the prior plan and confirm: 'You previously wanted [plan]. Has that changed?'"

        # User memory relevant?
        if state.user_memory and len(state.user_memory) >= 2:
            # Check if user has admin history or conflict history
            memory_lower = " ".join(state.user_memory).lower()
            if any(w in memory_lower for w in ["banned", "kicked", "warned"]):
                # User has moderation history — potential revenge/escalation
                passed = False
                concern = "User has moderation history — this request may be revenge or escalation"
                alternative = "Approach with extra caution; verify if this is proportional"

        # Server memory relevant?
        if state.server_memory:
            server_lower = " ".join(state.server_memory).lower()
            if ("small" in server_lower or "new" in server_lower or "quiet" in server_lower) and Mode.ADMIN in state.modes and any(w in lower_msg for w in ["ban", "kick", "delete"]):
                    passed = False
                    concern = "Server is small/new — aggressive actions have outsized impact"
                    alternative = "Suggest softer moderation first: warnings, timeouts, before bans"

        return AdversarialCheck(
            challenge="Missing Context",
            passed=passed,
            concern=concern,
            safer_alternative=alternative,
        )

    # -------------------------------------------------------------------------
    # Check 4: Safer alternatives
    # -------------------------------------------------------------------------

    def _check_safer_alternative(self, state: CognitiveState, response: str) -> AdversarialCheck:
        """
        Is there a safer, less risky way to accomplish the same goal?

        This check looks for escalation where de-escalation would work.
        """
        passed = True
        concern = ""
        alternative = ""

        lower_msg = state.raw_message.lower()

        # Ban → timeout first?
        if "ban" in lower_msg and "timeout" not in lower_msg and "warn" not in lower_msg:
            passed = False
            concern = "Ban requested but no mention of timeout or warning — de-escalation not attempted"
            alternative = "Suggest: 'Before a ban, would you like to timeout this member first?'"

        # Delete channel → archive instead?
        if "delete" in lower_msg and "channel" in lower_msg:
            passed = False
            concern = "Channel deletion requested — archiving is safer and reversible"
            alternative = "Suggest: 'I can archive this channel instead of deleting it. Want to keep the history?'"

        # Kick → timeout for a first offense?
        if "kick" in lower_msg:
            passed = False
            concern = "Kick requested — is this a first offense? A timeout might be more appropriate"
            alternative = "Ask: 'Is this a first offense? I can timeout instead, which is reversible.'"

        # Multiple tool calls when one would suffice
        if len(state.selected_tools) > 2:
            passed = False
            concern = f"Requesting {len(state.selected_tools)} simultaneous actions — high risk of unintended consequences"
            alternative = "Execute one action at a time and confirm before each subsequent step"

        return AdversarialCheck(
            challenge="Safer Alternative",
            passed=passed,
            concern=concern,
            safer_alternative=alternative,
        )

    # -------------------------------------------------------------------------
    # Check 5: Assumptions
    # -------------------------------------------------------------------------

    def _check_assumptions(self, state: CognitiveState, response: str) -> AdversarialCheck:
        """
        Explicitly name every assumption we made. Check if any could be wrong.
        """
        passed = True
        concern = ""
        alternative = ""

        assumptions: list[str] = []

        # Assumption: user has permission to make this request
        if Mode.ADMIN in state.modes:
            assumptions.append("User has admin permissions (not verified)")

        # Assumption: we understood the target correctly
        if state.missing_info:
            assumptions.append(f"Missing info: {state.missing_info} — we made assumptions to fill gaps")

        # Assumption: this is not a manipulation attempt
        assumptions.append("User is acting in good faith (not verified)")

        # Assumption: context is accurate
        if state.conversation_history:
            assumptions.append(f"Conversation history is accurate ({len(state.conversation_history)} turns)")

        # Flag problematic assumptions
        unverified = [a for a in assumptions if "not verified" in a]
        if len(unverified) >= 2:
            passed = False
            concern = f"Multiple unverified assumptions: {unverified}"
            alternative = "Ask for verification before executing admin actions"

        return AdversarialCheck(
            challenge="Assumptions",
            passed=passed,
            concern=concern,
            safer_alternative=alternative,
        )

    # -------------------------------------------------------------------------
    # Check 6: Manipulation attempts
    # -------------------------------------------------------------------------

    def _check_manipulation(self, state: CognitiveState, response: str) -> AdversarialCheck:
        """
        Is the user trying to manipulate or jailbreak the bot?
        """
        passed = True
        concern = ""
        alternative = ""

        lower_msg = state.raw_message.lower()

        # Check manipulation patterns
        for pat in self.MANIPULATION_PATTERNS:
            if re.search(pat, lower_msg):
                passed = False
                concern = f"Manipulation pattern detected: '{pat[:40]}'"
                alternative = "Acknowledge without complying; respond neutrally"
                break

        # Urgency/exploit pressure
        urgency_signals = ["now", "immediately", "right now", "do it now", "now now", "fast"]
        pressure_signals = ["no one is watching", "doesn't matter", "just do it", "trust me"]
        if any(s in lower_msg for s in urgency_signals) and any(s in lower_msg for s in pressure_signals):
            passed = False
            concern = "Urgency + pressure combo — common social engineering pattern"
            alternative = "Slow down: 'Let me make sure I understand this correctly before acting.'"

        # Emotional manipulation (using emotional words to bypass reasoning)
        emotion_count = sum(1 for e in self.EMOTIONAL_MANIPULATION if e in lower_msg)
        if emotion_count >= 2 and Mode.ADMIN in state.modes:
            passed = False
            concern = f"Emotional manipulation signals ({emotion_count} indicators) combined with admin request"
            alternative = "Proceed with extra caution; require confirmation"

        return AdversarialCheck(
            challenge="Manipulation Check",
            passed=passed,
            concern=concern,
            safer_alternative=alternative,
        )

    # -------------------------------------------------------------------------
    # Check 7: Proportionality
    # -------------------------------------------------------------------------

    def _check_proportionality(self, state: CognitiveState, response: str) -> AdversarialCheck:
        """
        Does the response intensity match the request intensity?

        Overkill responses are also a failure — responding with complex plans
        to simple questions, or vice versa.
        """
        passed = True
        concern = ""
        alternative = ""

        msg_len = len(state.raw_message)
        resp_len = len(response)

        # Overkill: huge response to a short casual message
        if msg_len < 30 and resp_len > 500 and Mode.CHAT in state.modes:
            passed = False
            concern = f"Response ({resp_len} chars) is disproportionate to casual message ({msg_len} chars)"
            alternative = "Keep it short and casual — user just said hi"

        # Underkill: short dismissive response to a serious question
        if Mode.QUESTION in state.modes and Mode.CHAT not in state.modes and resp_len < 20 and not state.selected_tools:
                passed = False
                concern = "Serious question received minimal response"
                alternative = "Provide a more substantive answer"

        # Plan for a trivial request
        if state.plan.execution_order and len(state.plan.execution_order) >= 5 and msg_len < 50 and Mode.ADMIN in state.modes:
                passed = False
                concern = f"Building a {len(state.plan.execution_order)}-step plan for a short request — possibly overkill"
                alternative = "Simplify the plan; fewer steps with less automation"

        return AdversarialCheck(
            challenge="Proportionality",
            passed=passed,
            concern=concern,
            safer_alternative=alternative,
        )

    # -------------------------------------------------------------------------
    # Generate safer response
    # -------------------------------------------------------------------------

    def generate_safer_response(self, state: CognitiveState, current_response: str) -> str:
        """
        When adversarial checks fail, generate a safer alternative response.

        This is called when the response might be harmful, disproportionate,
        or based on misunderstood intent.
        """
        lower_msg = state.raw_message.lower()

        # For manipulation attempts: neutral acknowledgement
        for pat in self.MANIPULATION_PATTERNS:
            if re.search(pat, lower_msg):
                return (
                    "I understand your message, but I'd like to make sure I handle this correctly. "
                    "Could you clarify what you'd like me to do?"
                )

        # For wide-scope admin: ask for specifics
        if any(s in lower_msg for s in ["all", "everyone", "every"]) and Mode.ADMIN in state.modes:
            return (
                "I want to make sure I do this right. "
                "Can you be more specific about who or what this applies to? "
                "For example, a specific member name, channel name, or role?"
            )

        # For ban/kick without prior de-escalation
        if any(w in lower_msg for w in ["ban", "kick"]) and Mode.ADMIN in state.modes and "timeout" not in lower_msg and "warn" not in lower_msg:
                return (
                    "Before I take that action — is this a first offense? "
                    "Timeouts are reversible and might be worth trying first. "
                    "What would you prefer?"
                )

        # For low-confidence admin actions
        if state.confidence_is_low(0.75) and Mode.ADMIN in state.modes:
            return (
                "I want to make sure I understand correctly before making changes. "
                "I'm not fully confident I have enough context here. "
                "Could you provide more details?"
            )

        # For "do what we discussed" with no history
        if any(phrase in lower_msg for phrase in ["earlier", "discussed", "before", "that time"]) and not state.conversation_history:
                return (
                    "I'd like to help, but I'm not sure what you're referring to. "
                    "Could you remind me what we discussed? I don't have that context right now."
                )

        # Default: add a cautionary note
        if current_response and not any(
            c in current_response.lower() for c in ["?", "clarify", "confirm", "verify"]
        ):
            return (
                current_response.rstrip() +
                "\n\n_(I'm not entirely sure I understood that correctly. "
                "Let me know if you'd like me to try again or clarify.)_"
            )

        return current_response
