"""Review and criticism phases — critic agent review, adversarial review, and fast-path review processing."""

import logging
import time

from ..cognitive_state import CognitiveState, PhaseLog

logger = logging.getLogger(__name__)


class ReviewPhaseMixin:
    """Mixin for review phases: critic review, adversarial review, semantic research."""

    def _phase_critic_review(
        self,
        state: CognitiveState,
        critique,
        run_critic: bool,
        t_critic: float,
    ) -> None:
        """Process critic review results."""
        if run_critic and critique is not None:
            state.phases.append(PhaseLog(
                phase="CRITIC",
                duration_ms=(time.perf_counter() - t_critic) * 1000,
                result=f"{'PASSED' if critique.passed else 'ISSUES: ' + ', '.join(critique.concerns[:2])}",
                confidence=critique.confidence,
            ))
            if not critique.passed or critique.concerns:
                if critique.requires_override and critique.safer_response and critique.confidence >= 0.7:
                    state.response = critique.safer_response
                    state.review_notes = f"[CRITIC OVERRIDE] {critique.overall_assessment}"
                else:
                    if critique.concerns:
                        state.review_notes = " | ".join(critique.concerns[:2])
                        state.response = self.critic.generate_response(state, critique, state.response)
                state.review_passed = False
            else:
                state.review_passed = True
        elif run_critic and critique is None:
            state.phases.append(PhaseLog(
                phase="CRITIC",
                duration_ms=(time.perf_counter() - t_critic) * 1000,
                result="failed (parallel error \u2014 fallback to no review)",
            ))
            state.review_passed = True
        else:
            state.phases.append(PhaseLog(
                phase="CRITIC",
                duration_ms=(time.perf_counter() - t_critic) * 1000,
                result="skipped (LOW/MEDIUM complexity + LOW/MEDIUM risk)",
            ))
            state.review_passed = True

    def _phase_review_process(
        self,
        state: CognitiveState,
        response: str,
        adversarial_review: bool,
    ) -> str:
        """Run review and adversarial review on a response (fast premium path)."""
        t_review = time.perf_counter()
        review_passed, review_results, review_notes = self.review.review(state, response)
        state.review_passes = [r.passed for r in review_results]
        state.review_issues = [r.issue for r in review_results if not r.passed]
        state.review_notes = review_notes
        state.review_passed = review_passed

        if not review_passed:
            response = self._apply_corrections(state, review_results)
            state.response = response

        if adversarial_review and response:
            adversarial_results = self.adversarial_review.review(state, response)
            state.review_passes.extend([r.passed for r in adversarial_results])
            state.review_issues.extend([r.concern for r in adversarial_results if not r.passed])
            if not all(r.passed for r in adversarial_results):
                cautious = self.adversarial_review.generate_safer_response(state, response)
                if cautious != response:
                    response = cautious
                    state.review_notes += "\n[ADVERSARIAL CORRECTION APPLIED]"
                state.review_passed = False

        state.response_final = True
        state.phases.append(PhaseLog(
            phase="REVIEW",
            duration_ms=(time.perf_counter() - t_review) * 1000,
            result=f"{'PASSED' if review_passed else 'ISSUES: ' + ', '.join(state.review_issues[:3])}",
        ))
        return response
