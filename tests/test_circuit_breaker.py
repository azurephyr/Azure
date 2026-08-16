"""Tests for the CircuitBreaker (azure/circuit_breaker.py)."""

import time

from azure.circuit_breaker import CircuitBreaker


def test_closed_allows_requests():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.1)
    assert cb.state == "CLOSED"
    assert cb.allow_request() is True


def test_failures_increment_count():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.1)
    cb.record_failure()
    cb.record_failure()
    info = cb.get_info()
    assert info["failure_count"] == 2
    assert info["state"] == "CLOSED"


def test_threshold_triggers_open():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.1)
    for _ in range(3):
        cb.record_failure()
    assert cb.state == "OPEN"


def test_open_blocks_requests():
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=1.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "OPEN"
    assert cb.allow_request() is False


def test_cooldown_transitions_to_half_open():
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.02)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "OPEN"

    time.sleep(0.05)
    assert cb.state == "HALF_OPEN"


def test_half_open_allows_one_request():
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.02)
    cb.record_failure()
    cb.record_failure()

    time.sleep(0.05)
    assert cb.allow_request() is True


def test_success_resets_to_closed():
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.02)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "OPEN"

    time.sleep(0.05)
    cb.allow_request()  # transitions to HALF_OPEN
    cb.record_success()

    assert cb.state == "CLOSED"
    assert cb.get_info()["failure_count"] == 0


def test_failure_in_half_open_reopens():
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.02)
    cb.record_failure()
    cb.record_failure()

    time.sleep(0.05)
    cb.allow_request()  # HALF_OPEN
    cb.record_failure()  # failed again

    assert cb.state == "OPEN"


def test_half_open_failure_restarts_cooldown():
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.1)
    cb.record_failure()
    time.sleep(0.12)
    assert cb.allow_request() is True
    cb.record_failure()

    assert cb.allow_request() is False


def test_state_property_does_not_deadlock():
    cb = CircuitBreaker()
    assert cb.state == "CLOSED"


def test_get_info():
    cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
    info = cb.get_info()
    assert info["state"] == "CLOSED"
    assert info["failure_count"] == 0
    assert info["failure_threshold"] == 5
    assert info["cooldown_seconds"] == 60
