"""Integration tests that snapshot real target processes.

These attach to a live child process with ``sys.remote_exec``, so they need
CPython 3.14+ and, on macOS, root. They cover the three target patterns a
snapshot has to survive: a healthy sleeper, a thread blocked on a lock, and a
thread blocked in a socket accept.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fastjsonschema
import pytest

from pidprobe import available_collectors, snapshot_schema, take_snapshot
from pidprobe.cli import EXIT_OK, main

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable

pytestmark = pytest.mark.integration

HAPPY_PATH_BUDGET_SECONDS = 1.0
TARGET_NAMES = ["sleeper", "lock", "socket_accept"]


@pytest.fixture
def validate_snapshot():
    """Return a validator compiled from the packaged JSON Schema."""
    return fastjsonschema.compile(snapshot_schema())


def functions_of(snapshot: dict[str, Any]) -> set[str]:
    """Return every function name reported by the stacks collector."""
    return {
        frame["function"]
        for thread in snapshot["stacks"]["threads"]
        for frame in thread["frames"]
    }


@pytest.mark.parametrize("target_name", TARGET_NAMES)
def test_snapshot_of_a_live_target_matches_the_schema(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
    validate_snapshot: Callable[[dict[str, Any]], object],
    target_name: str,
) -> None:
    proc = spawn_target(target_name)

    snapshot = take_snapshot(proc.pid)

    validate_snapshot(snapshot)
    assert snapshot["meta"]["pid"] == proc.pid
    assert snapshot["meta"]["target"]["pid"] == proc.pid
    assert [report["name"] for report in snapshot["meta"]["collectors"]] == [
        collector.name for collector in available_collectors()
    ]
    assert all(report["status"] == "ok" for report in snapshot["meta"]["collectors"])


def test_healthy_target_snapshot_completes_within_a_second(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
) -> None:
    proc = spawn_target("sleeper")

    started = time.perf_counter()
    snapshot = take_snapshot(proc.pid)
    elapsed_seconds = time.perf_counter() - started

    assert elapsed_seconds < HAPPY_PATH_BUDGET_SECONDS, snapshot["meta"]
    assert snapshot["meta"]["stop_duration_ms"] <= snapshot["meta"]["elapsed_ms"]


def test_sleeper_stack_reports_the_running_loop_with_its_locals(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
) -> None:
    proc = spawn_target("sleeper")

    snapshot = take_snapshot(proc.pid)

    main_thread = snapshot["stacks"]["threads"][0]
    assert main_thread["is_main"] is True
    loop_frames = [
        frame
        for frame in main_thread["frames"]
        if Path(frame["file"]).name == "sleeper.py" and frame["function"] == "_run"
    ]
    assert loop_frames, main_thread["frames"]
    assert loop_frames[0]["locals"]["marker"] == repr("pidprobe-sleeper")


def test_lock_target_reports_both_the_holder_and_the_waiter(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
) -> None:
    proc = spawn_target("lock")

    snapshot = take_snapshot(proc.pid)

    functions = functions_of(snapshot)
    assert "_hold_lock" in functions, functions
    assert "_wait_for_lock" in functions, functions
    assert snapshot["stacks"]["thread_count"] >= 3


def test_socket_target_reports_the_blocked_thread_and_its_listener(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
) -> None:
    proc = spawn_target("socket_accept")

    snapshot = take_snapshot(proc.pid)

    assert "_accept_forever" in functions_of(snapshot)
    sockets = [
        entry for entry in snapshot["fds"]["descriptors"] if entry["kind"] == "socket"
    ]
    assert sockets, snapshot["fds"]["descriptors"]


def test_cli_prints_one_json_line_for_a_live_target(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    proc = spawn_target("sleeper")

    exit_code = main(["snap", str(proc.pid)])

    out = capsys.readouterr().out
    assert exit_code == EXIT_OK
    assert out.count("\n") == 1
    assert json.loads(out)["meta"]["pid"] == proc.pid
