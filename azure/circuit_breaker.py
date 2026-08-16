"""Circuit breaker for LLM API calls."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger("azure.circuit_breaker")


class CircuitBreaker:
    """Prevents repeated calls to a failing service.

    States:
      CLOSED -- normal operation, calls pass through
      OPEN -- too many failures, calls are short-circuited
      HALF_OPEN -- after cooldown, one test call is allowed
    """

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 60.0):
        self._failure_count = 0
        self._failure_threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._state = "CLOSED"
        self._last_failure_time = 0.0
        self._lock = threading.RLock()  # RLock: state property calls check_cooldown which also locks
        self._half_open_test_pending = False

    @property
    def state(self) -> str:
        """Return the current circuit breaker state.

        Returns:
            One of "CLOSED", "OPEN", or "HALF_OPEN".
        """
        with self._lock:
            self._check_cooldown_locked()
            return self._state

    def _check_cooldown_locked(self) -> None:
        """Transition from OPEN to HALF_OPEN if cooldown has elapsed.

        MUST be called with self._lock held.
        """
        if self._state == "OPEN" and time.monotonic() - self._last_failure_time >= self._cooldown:
            self._state = "HALF_OPEN"

    def check_cooldown(self) -> None:
        """Transition from OPEN to HALF_OPEN if cooldown has elapsed."""
        with self._lock:
            self._check_cooldown_locked()

    def allow_request(self) -> bool:
        """Check whether a request is allowed through the circuit breaker.

        In the CLOSED state all requests are allowed.  In HALF_OPEN state
        a single test request is permitted.  In OPEN state requests are
        blocked until the cooldown expires.

        Returns:
            True if the request may proceed, False if it should be short-circuited.
        """
        with self._lock:
            if self._state == "CLOSED":
                return True
            if self._state == "HALF_OPEN":
                if self._half_open_test_pending:
                    return False
                self._half_open_test_pending = True
                return True
            # OPEN — use monotonic clock to avoid NTP-related issues
            if time.monotonic() - self._last_failure_time >= self._cooldown:
                self._state = "HALF_OPEN"
                self._half_open_test_pending = True
                return True
            return False

    def record_success(self) -> None:
        """Record a successful call and reset the circuit breaker.

        Resets the failure count and transitions back to the CLOSED state.
        """
        with self._lock:
            self._failure_count = 0
            self._state = "CLOSED"
            self._half_open_test_pending = False

    def record_failure(self) -> None:
        """Record a failed call and potentially open the circuit breaker.

        Increments the failure count.  When the count reaches the configured
        threshold the breaker transitions to the OPEN state and begins cooling
        down.  A failure during HALF_OPEN (test request) re-opens the breaker
        and restarts the cooldown timer.
        """
        with self._lock:
            self._failure_count += 1
            self._half_open_test_pending = False  # test request finished (failed)
            if self._state == "HALF_OPEN":
                # Test request failed — re-open and restart cooldown
                self._state = "OPEN"
                self._last_failure_time = time.monotonic()
                logger.warning(
                    "[circuit_breaker] HALF_OPEN test failed -- re-opening, "
                    "cooling down for %.0fs", self._cooldown,
                )
            elif self._failure_count >= self._failure_threshold and self._state != "OPEN":
                self._state = "OPEN"
                self._last_failure_time = time.monotonic()
                logger.warning(
                    "[circuit_breaker] OPEN -- %d consecutive failures, "
                    "cooling down for %.0fs",
                    self._failure_count, self._cooldown,
                )
            elif self._state != "OPEN":
                self._last_failure_time = time.monotonic()

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED state."""
        with self._lock:
            self._failure_count = 0
            self._state = "CLOSED"
            self._half_open_test_pending = False
            self._last_failure_time = 0.0

    def get_info(self) -> dict[str, Any]:
        """Return circuit breaker status for dashboards/logging."""
        with self._lock:
            return {
                "state": self._state,
                "failure_count": self._failure_count,
                "failure_threshold": self._failure_threshold,
                "cooldown_seconds": self._cooldown,
                "seconds_since_failure": round(
                    time.monotonic() - self._last_failure_time, 1
                ) if self._last_failure_time else None,
            }
