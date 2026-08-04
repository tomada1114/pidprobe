"""Canary integration test proving the PEP 768 (sys.remote_exec) round trip.

This exercises the exact pattern pidprobe relies on: the prober listens on
an AF_UNIX socket first, writes an injection script to a temp file, calls
``sys.remote_exec(pid, script_path)``, and waits for the target process to
connect back and send a JSON envelope. If this test cannot pass in CI, none
of pidprobe's core functionality can work either -- it is meant to fail
loudly and fast rather than let a broken environment surface as a confusing
failure somewhere else.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import IO

import pytest

pytestmark = pytest.mark.integration

TARGET_SCRIPT = Path(__file__).parent / "targets" / "sleeper.py"
READY_TIMEOUT_SECONDS = 10.0
ACCEPT_TIMEOUT_SECONDS = 5.0
ROUND_TRIP_BUDGET_SECONDS = 5.0
RECV_CHUNK_BYTES = 65536

# macOS caps sockaddr_un.sun_path at 104 bytes (including the terminator);
# Linux allows 108. Use the stricter limit so the same code path works on
# both platforms.
_MAX_SUN_PATH_BYTES = 104

_INJECTED_SCRIPT_TEMPLATE = """
import json
import socket
import sys
import traceback


def _collect_stacks():
    # sys._current_frames() only gives the *currently executing* frame per
    # thread. While this script runs, that is this injected module's own
    # frame -- the frame it interrupted (e.g. the target's main loop) is
    # its caller, reachable by walking f_back.
    frames = []
    for _thread_id, frame in sys._current_frames().items():
        while frame is not None:
            frames.append(
                {{"file": frame.f_code.co_filename, "function": frame.f_code.co_name}}
            )
            frame = frame.f_back
    return frames


try:
    _payload = {{"status": "ok", "payload": {{"frames": _collect_stacks()}}}}
except Exception:
    _payload = {{"status": "error", "error": traceback.format_exc()}}

_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    _sock.connect({socket_path!r})
    _sock.sendall(json.dumps(_payload).encode("utf-8"))
finally:
    _sock.close()
"""


def _injected_script(socket_path: Path) -> str:
    """Build the source injected into the target process via remote_exec.

    Must be fully self-contained (stdlib only): it runs in a separate
    interpreter and has no access to this test module's globals.
    """
    return _INJECTED_SCRIPT_TEMPLATE.format(socket_path=str(socket_path))


def _socket_path(tmp_path: Path) -> tuple[Path, Path | None]:
    """Return a usable AF_UNIX socket path under the AF_UNIX length limit.

    Falls back to a short-lived directory outside ``tmp_path`` (created via
    ``tempfile.mkdtemp``) when the pytest-provided ``tmp_path`` is too deep
    to fit a socket path under the platform limit.

    Returns:
        A tuple of ``(socket_path, fallback_dir)``. ``fallback_dir`` is not
        ``None`` when a separate directory had to be created; the caller is
        responsible for removing it.
    """
    candidate = tmp_path / "pidprobe.sock"
    if len(str(candidate).encode()) < _MAX_SUN_PATH_BYTES:
        return candidate, None
    fallback_dir = Path(tempfile.mkdtemp(prefix="pidprobe-"))
    return fallback_dir / "s.sock", fallback_dir


def _wait_for_ready(stream: IO[str], timeout: float) -> None:
    """Block until the target process prints its READY line or times out.

    Uses ``select()`` instead of a blocking ``readline()`` so a target that
    never starts (e.g. crashes before printing) fails the test quickly
    instead of hanging CI.
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


def test_remote_exec_round_trip_returns_stack_envelope(tmp_path: Path) -> None:
    if not hasattr(sys, "remote_exec"):
        pytest.skip("sys.remote_exec is not available (requires CPython 3.14+)")
    if sys.platform == "darwin" and os.geteuid() != 0:
        pytest.skip("requires root on macOS (task_for_pid)")

    socket_path, fallback_dir = _socket_path(tmp_path)
    script_path = tmp_path / "inject.py"
    script_path.write_text(_injected_script(socket_path), encoding="utf-8")

    # Make sure a build-time opt-out of remote debugging on this machine
    # can't silently leak into the target's environment.
    env = os.environ.copy()
    env.pop("PYTHON_DISABLE_REMOTE_DEBUG", None)

    proc = subprocess.Popen(  # noqa: S603 -- fixed, trusted argv; no shell
        [sys.executable, str(TARGET_SCRIPT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        assert proc.stdout is not None
        _wait_for_ready(proc.stdout, READY_TIMEOUT_SECONDS)

        server.bind(str(socket_path))
        server.listen(1)
        server.settimeout(ACCEPT_TIMEOUT_SECONDS)

        start = time.monotonic()
        sys.remote_exec(proc.pid, str(script_path))

        try:
            conn, _ = server.accept()
        except TimeoutError:
            pytest.fail(
                f"target did not connect back within {ACCEPT_TIMEOUT_SECONDS}s "
                "-- remote_exec round trip failed"
            )

        with conn:
            chunks = []
            while data := conn.recv(RECV_CHUNK_BYTES):
                chunks.append(data)
        elapsed = time.monotonic() - start

        envelope = json.loads(b"".join(chunks).decode("utf-8"))
    finally:
        server.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        if fallback_dir is not None:
            shutil.rmtree(fallback_dir, ignore_errors=True)

    assert elapsed < ROUND_TRIP_BUDGET_SECONDS, (
        f"round trip took {elapsed:.3f}s, expected under {ROUND_TRIP_BUDGET_SECONDS}s"
    )
    assert envelope["status"] == "ok", envelope
    frames = envelope["payload"]["frames"]
    assert any(Path(frame["file"]).name == "sleeper.py" for frame in frames), frames
