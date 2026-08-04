"""Shared test fixtures."""

from __future__ import annotations

import os
import select
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

TARGETS_DIR = Path(__file__).parent / "targets"
READY_TIMEOUT_SECONDS = 10.0
KILL_TIMEOUT_SECONDS = 5.0


def _wait_for_ready(stream: IO[str], timeout: float) -> None:
    """Block until the target prints its READY line, or fail the test.

    Uses select() instead of a blocking readline() so a target that never
    starts fails fast instead of hanging CI.
    """
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            pytest.fail(f"target process did not print READY within {timeout}s")
        ready, _, _ = select.select([stream], [], [], remaining)
        if not ready:
            continue
        line = stream.readline()
        if line == "":
            pytest.fail("target process exited before printing READY")
        if line.strip() == "READY":
            return


@pytest.fixture
def spawn_target() -> Iterator[Callable[[str], subprocess.Popen[str]]]:
    """Return a factory that starts a target from ``tests/targets``.

    The factory blocks until the target printed READY, so tests never race
    against interpreter start-up, and every spawned process is killed during
    teardown even when the test fails.
    """
    processes: list[subprocess.Popen[str]] = []

    def _spawn(name: str) -> subprocess.Popen[str]:
        # A build-time opt-out on the developer machine must not leak into
        # the target, or sys.remote_exec would be refused for the wrong
        # reason.
        env = os.environ.copy()
        env.pop("PYTHON_DISABLE_REMOTE_DEBUG", None)
        proc = subprocess.Popen(  # noqa: S603 -- fixed, trusted argv; no shell
            [sys.executable, str(TARGETS_DIR / f"{name}.py")],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        processes.append(proc)
        assert proc.stdout is not None
        _wait_for_ready(proc.stdout, READY_TIMEOUT_SECONDS)
        return proc

    yield _spawn

    for proc in processes:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=KILL_TIMEOUT_SECONDS)


@pytest.fixture
def remote_exec_supported() -> None:
    """Skip the test unless this machine can actually inject code."""
    if not hasattr(sys, "remote_exec"):
        pytest.skip("sys.remote_exec is not available (requires CPython 3.14+)")
    if sys.platform == "darwin" and os.geteuid() != 0:
        pytest.skip("requires root on macOS (task_for_pid)")
