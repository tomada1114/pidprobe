"""Integration tests that evaluate expressions in real target processes.

These attach to a live child process with ``sys.remote_exec``, so they need
CPython 3.14+ and, on macOS, root -- the same skip rules as the other
integration tests. The ``sleeper`` target carries the module-level state the
expressions resolve: ``INVENTORY`` and a credential-like ``api_key``.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import pytest

from pidprobe import TargetError, evaluate_in_target
from pidprobe._saferepr import MASK_PLACEHOLDER
from pidprobe.cli import EXIT_OK, EXIT_PROBE_ERROR, main

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable

pytestmark = pytest.mark.integration

FAILURE_BUDGET_SECONDS = 4.0
TIMEOUT_SECONDS = 5.0


def test_expression_returns_the_safe_repr_of_its_value(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
) -> None:
    proc = spawn_target("sleeper")

    evaluation = evaluate_in_target(proc.pid, "sorted(INVENTORY)")

    assert evaluation == {
        "pid": proc.pid,
        "expression": "sorted(INVENTORY)",
        "type": "list",
        "result": "['gadgets', 'widgets']",
        "masking_enabled": True,
    }


def test_failing_expression_reports_the_target_error_instead_of_timing_out(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
) -> None:
    proc = spawn_target("sleeper")

    started = time.perf_counter()
    with pytest.raises(TargetError, match=r"evaluation failed inside pid") as info:
        evaluate_in_target(proc.pid, "no_such_name", timeout_seconds=TIMEOUT_SECONDS)
    elapsed_seconds = time.perf_counter() - started

    # A timeout would have taken the whole budget and raised ProbeTimeoutError.
    assert elapsed_seconds < FAILURE_BUDGET_SECONDS
    assert info.value.error["type"] == "NameError"
    assert "no_such_name" in info.value.error["message"]


def test_statement_is_refused_by_the_target_compiler(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
) -> None:
    proc = spawn_target("sleeper")

    with pytest.raises(TargetError) as info:
        evaluate_in_target(proc.pid, "api_key = 'leaked'")

    assert info.value.error["type"] == "SyntaxError"
    assert evaluate_in_target(proc.pid, "api_key", is_masked=False)["result"] == repr(
        "not-a-real-secret",
    )


def test_credential_like_expression_is_masked_by_default(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
) -> None:
    proc = spawn_target("sleeper")

    masked = evaluate_in_target(proc.pid, "api_key")
    unmasked = evaluate_in_target(proc.pid, "api_key", is_masked=False)

    assert masked["result"] == MASK_PLACEHOLDER
    assert masked["masking_enabled"] is True
    assert unmasked["result"] == repr("not-a-real-secret")
    assert unmasked["masking_enabled"] is False


def test_cli_prints_one_json_line_for_a_live_target(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    proc = spawn_target("sleeper")

    exit_code = main(["eval", str(proc.pid), "len(INVENTORY)"])

    out = capsys.readouterr().out
    assert exit_code == EXIT_OK
    assert out.count("\n") == 1
    assert json.loads(out) == {
        "pid": proc.pid,
        "expression": "len(INVENTORY)",
        "type": "int",
        "result": "2",
        "masking_enabled": True,
    }


def test_cli_explains_a_failing_expression_on_stderr(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    proc = spawn_target("sleeper")

    exit_code = main(["eval", str(proc.pid), "1 / 0"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_PROBE_ERROR
    assert captured.out == ""
    assert "ZeroDivisionError" in captured.err
