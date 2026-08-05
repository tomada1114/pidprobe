"""Integration tests that attach to a real process via ``sys.remote_exec``.

These are the only tests that prove the whole chain works: channel first,
generated script second, injection third, envelope back. They need a live
target and, on macOS, root privileges, so they are skipped elsewhere.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pidprobe._channel import execute_in_target
from pidprobe._errors import ProbeTimeoutError

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable

pytestmark = pytest.mark.integration

HANG_TIMEOUT_SECONDS = 1.5

FRAME_COLLECTOR = """
frames = []
for _tid, frame in _pidprobe_target_frames().items():
    while frame is not None:
        frames.append({"file": frame.f_code.co_filename, "name": frame.f_code.co_name})
        frame = frame.f_back
payload = {"frames": frames}
"""


@pytest.mark.parametrize("allow_socket", [True, False], ids=["socket", "tempfile"])
def test_snapshot_round_trips_through_the_channel(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
    allow_socket: bool,
) -> None:
    proc = spawn_target("sleeper")

    envelope = execute_in_target(
        proc.pid,
        FRAME_COLLECTOR,
        allow_socket=allow_socket,
    )

    assert envelope["status"] == "ok", envelope
    payload = envelope["payload"]
    assert payload is not None
    files = [Path(frame["file"]).name for frame in payload["frames"]]
    assert "sleeper.py" in files, files


def test_reported_frames_exclude_pidprobes_own_injected_frames(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
) -> None:
    proc = spawn_target("sleeper")

    envelope = execute_in_target(proc.pid, FRAME_COLLECTOR)

    payload = envelope["payload"]
    assert payload is not None
    frames = payload["frames"]
    assert not any("pidprobe_inject" in frame["file"] for frame in frames), frames
    assert not any(frame["name"].startswith("_pidprobe_") for frame in frames), frames


def test_collector_failure_comes_back_as_an_error_envelope(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
) -> None:
    proc = spawn_target("sleeper")

    envelope = execute_in_target(proc.pid, "raise RuntimeError('collector exploded')")

    assert envelope["status"] == "error", envelope
    error = envelope["error"]
    assert error is not None
    assert error["type"] == "RuntimeError"
    assert error["message"] == "collector exploded"
    assert "collector exploded" in error["traceback"]


def test_blocked_target_times_out_with_an_actionable_message(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
) -> None:
    proc = spawn_target("hang")

    with pytest.raises(ProbeTimeoutError) as excinfo:
        execute_in_target(
            proc.pid,
            "payload = {}",
            timeout_seconds=HANG_TIMEOUT_SECONDS,
        )

    message = str(excinfo.value)
    assert "safe eval point" in message
    assert "py-spy" in message
    assert f"pidprobe doctor {proc.pid}" in message
