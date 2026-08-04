"""Tests for the shared probe time budget."""

from __future__ import annotations

from pidprobe._deadline import Deadline
from pidprobe._errors import ProbeTimeoutError


def test_fresh_deadline_has_time_left() -> None:
    deadline = Deadline.start(5.0, pid=11)

    assert not deadline.is_expired
    assert 0 < deadline.remaining <= 5.0


def test_zero_budget_is_immediately_expired() -> None:
    deadline = Deadline.start(0.0)

    assert deadline.is_expired
    assert deadline.remaining <= 0


def test_timeout_error_reports_the_original_budget_and_pid() -> None:
    error = Deadline.start(1.5, pid=42).timeout_error()

    assert isinstance(error, ProbeTimeoutError)
    assert error.timeout_seconds == 1.5
    assert error.pid == 42
