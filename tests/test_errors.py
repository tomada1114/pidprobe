"""Tests for the typed exceptions and the hints they carry."""

from __future__ import annotations

import pytest

from pidprobe._errors import (
    AttachError,
    ChannelError,
    NoSuchProcessError,
    ProbeError,
    ProbeTimeoutError,
)


@pytest.mark.parametrize(
    ("cause", "expected_hint"),
    [
        pytest.param(PermissionError(1, "denied"), "task_for_pid", id="permission"),
        pytest.param(ProcessLookupError(3, "gone"), "no such process", id="gone"),
        pytest.param(RuntimeError("nope"), "PYTHON_DISABLE_REMOTE_DEBUG", id="runtime"),
        pytest.param(ValueError("bad pid"), "PYTHON_DISABLE_REMOTE_DEBUG", id="value"),
        pytest.param(OSError(5, "io"), "sys.remote_exec failed", id="other-oserror"),
    ],
)
def test_attach_error_from_cause_explains_the_likely_remedy(
    cause: BaseException,
    expected_hint: str,
) -> None:
    error = AttachError.from_cause(4242, cause)

    assert error.pid == 4242
    assert expected_hint in str(error)
    assert "cannot attach to pid 4242" in str(error)
    assert type(cause).__name__ in str(error)


def test_a_vanished_target_gets_its_own_error_type() -> None:
    error = AttachError.from_cause(4242, ProcessLookupError(3, "gone"))

    # Its own type, because it is its own outcome: nothing needs fixing, the
    # process is simply not there, and the CLI reports it with its own code.
    assert isinstance(error, NoSuchProcessError)
    assert error.pid == 4242


@pytest.mark.parametrize(
    "cause",
    [
        pytest.param(PermissionError(1, "denied"), id="permission"),
        pytest.param(RuntimeError("nope"), id="runtime"),
        pytest.param(OSError(5, "io"), id="other-oserror"),
    ],
)
def test_a_refused_attach_is_not_a_vanished_target(cause: BaseException) -> None:
    error = AttachError.from_cause(4242, cause)

    assert isinstance(error, AttachError)
    assert not isinstance(error, NoSuchProcessError)


def test_attach_error_without_cause_keeps_the_hint() -> None:
    error = AttachError(7, "custom hint")

    assert str(error) == "cannot attach to pid 7: custom hint"


def test_probe_timeout_error_message_names_budget_and_workaround() -> None:
    error = ProbeTimeoutError(2.5, pid=99)

    message = str(error)
    assert "within 2.5s" in message
    assert "safe eval point" in message
    assert "py-spy" in message
    assert "pidprobe doctor 99" in message
    assert error.timeout_seconds == 2.5
    assert error.pid == 99


def test_probe_timeout_error_without_pid_uses_placeholder() -> None:
    assert "pidprobe doctor <pid>" in str(ProbeTimeoutError(5.0))


@pytest.mark.parametrize(
    "error",
    [
        AttachError(1, "hint"),
        ProbeTimeoutError(1.0),
        ChannelError("broken"),
    ],
)
def test_every_error_derives_from_probe_error(error: ProbeError) -> None:
    assert isinstance(error, ProbeError)
