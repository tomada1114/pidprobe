"""Tests for the return channel and the probe orchestration around it.

``sys.remote_exec`` is replaced by a stand-in that runs the generated script
in a fresh interpreter, so the orchestration is covered on every platform;
the real injection is exercised in ``test_channel_integration.py``.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pidprobe import _channel
from pidprobe._channel import (
    TempFileChannel,
    UnixSocketChannel,
    execute_in_target,
    open_channel,
)
from pidprobe._deadline import Deadline
from pidprobe._errors import AttachError, ChannelError, ProbeTimeoutError
from pidprobe._inject import ChannelKind

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

MAX_SUN_PATH_BYTES = 104
FAKE_PID = 4242
SHORT_TIMEOUT_SECONDS = 0.2
SCRIPT_TIMEOUT_SECONDS = 30.0


@pytest.fixture
def socket_channel() -> Iterator[UnixSocketChannel]:
    channel = UnixSocketChannel()
    yield channel
    channel.close()


@pytest.fixture
def file_channel() -> Iterator[TempFileChannel]:
    channel = TempFileChannel()
    yield channel
    channel.close()


def _connect(channel: UnixSocketChannel) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(channel.path)
    return client


def _publish(channel: TempFileChannel, content: str) -> None:
    """Write a result the way the injected script does: write then rename."""
    staged = Path(f"{channel.path}.tmp")
    staged.write_text(content, encoding="utf-8")
    os.replace(staged, channel.path)  # noqa: PTH105 -- atomic rename is the point


def test_socket_channel_path_fits_the_platform_limit(
    socket_channel: UnixSocketChannel,
) -> None:
    assert len(socket_channel.path.encode()) < MAX_SUN_PATH_BYTES
    assert socket_channel.kind is ChannelKind.UNIX_SOCKET
    assert Path(socket_channel.path).exists()


def test_socket_channel_receives_what_the_target_sent(
    socket_channel: UnixSocketChannel,
) -> None:
    with _connect(socket_channel) as client:
        client.sendall(b'{"status": "ok"}')

    assert socket_channel.receive(Deadline.start(5.0)) == '{"status": "ok"}'


def test_socket_channel_times_out_when_nobody_connects(
    socket_channel: UnixSocketChannel,
) -> None:
    with pytest.raises(ProbeTimeoutError, match="safe eval point"):
        socket_channel.receive(Deadline.start(SHORT_TIMEOUT_SECONDS))


def test_socket_channel_rejects_an_empty_answer(
    socket_channel: UnixSocketChannel,
) -> None:
    _connect(socket_channel).close()

    with pytest.raises(ChannelError, match="sent nothing"):
        socket_channel.receive(Deadline.start(5.0))


def test_socket_channel_close_removes_its_directory() -> None:
    channel = UnixSocketChannel()
    directory = Path(channel.path).parent

    channel.close()

    assert not directory.exists()


def test_file_channel_receives_a_renamed_result(file_channel: TempFileChannel) -> None:
    _publish(file_channel, '{"status": "ok"}')

    assert file_channel.receive(Deadline.start(5.0)) == '{"status": "ok"}'
    assert file_channel.kind is ChannelKind.TEMPFILE


def test_file_channel_ignores_a_half_written_result(
    file_channel: TempFileChannel,
) -> None:
    Path(f"{file_channel.path}.tmp").write_text("{partial", encoding="utf-8")

    with pytest.raises(ProbeTimeoutError):
        file_channel.receive(Deadline.start(SHORT_TIMEOUT_SECONDS))


def test_file_channel_times_out_when_no_result_appears(
    file_channel: TempFileChannel,
) -> None:
    with pytest.raises(ProbeTimeoutError, match="py-spy"):
        file_channel.receive(Deadline.start(SHORT_TIMEOUT_SECONDS))


def test_file_channel_close_removes_its_directory() -> None:
    channel = TempFileChannel()
    directory = Path(channel.path).parent

    channel.close()

    assert not directory.exists()


def test_open_channel_prefers_the_socket() -> None:
    channel = open_channel()
    try:
        assert channel.kind is ChannelKind.UNIX_SOCKET
    finally:
        channel.close()


def test_open_channel_can_skip_the_socket() -> None:
    channel = open_channel(allow_socket=False)
    try:
        assert channel.kind is ChannelKind.TEMPFILE
    finally:
        channel.close()


def test_open_channel_falls_back_when_no_short_socket_path_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No temp directory can produce a path this short, which is what happens
    # for real on macOS once the temp directory is deep enough.
    monkeypatch.setattr(_channel, "_MAX_SUN_PATH_BYTES", 4)

    channel = open_channel()
    try:
        assert channel.kind is ChannelKind.TEMPFILE
    finally:
        channel.close()


def test_open_channel_falls_back_when_the_socket_cannot_be_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _refuse(*_args: object, **_kwargs: object) -> socket.socket:
        message = "sandbox forbids sockets"
        raise OSError(message)

    monkeypatch.setattr(socket, "socket", _refuse)

    channel = open_channel()
    try:
        assert channel.kind is ChannelKind.TEMPFILE
    finally:
        channel.close()


def _fake_remote_exec(seen: list[Path]) -> Callable[[int, str], None]:
    """Stand in for sys.remote_exec by running the script out of process."""

    def _run(pid: int, script: str) -> None:
        seen.append(Path(script))
        subprocess.run(  # noqa: S603 -- generated script, trusted argv
            [sys.executable, script],
            capture_output=True,
            check=False,
            timeout=SCRIPT_TIMEOUT_SECONDS,
        )

    return _run


@pytest.mark.parametrize("allow_socket", [True, False], ids=["socket", "tempfile"])
def test_execute_in_target_returns_the_collector_payload(
    monkeypatch: pytest.MonkeyPatch,
    allow_socket: bool,
) -> None:
    monkeypatch.setattr(sys, "remote_exec", _fake_remote_exec([]))

    envelope = execute_in_target(
        FAKE_PID,
        "payload = {'answer': 42}",
        allow_socket=allow_socket,
    )

    assert envelope == {"status": "ok", "error": None, "payload": {"answer": 42}}


def test_execute_in_target_reports_collector_failures_as_an_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "remote_exec", _fake_remote_exec([]))

    envelope = execute_in_target(FAKE_PID, "raise ValueError('nope')")

    assert envelope["status"] == "error"
    error = envelope["error"]
    assert error is not None
    assert error["type"] == "ValueError"
    assert error["message"] == "nope"


def test_execute_in_target_removes_the_generated_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts: list[Path] = []
    monkeypatch.setattr(sys, "remote_exec", _fake_remote_exec(scripts))

    execute_in_target(FAKE_PID, "payload = {}")

    assert scripts
    assert not scripts[0].exists()
    assert not scripts[0].parent.exists()


def test_execute_in_target_times_out_when_the_target_never_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "remote_exec", lambda *_args: None)

    with pytest.raises(ProbeTimeoutError) as excinfo:
        execute_in_target(
            FAKE_PID,
            "payload = {}",
            timeout_seconds=SHORT_TIMEOUT_SECONDS,
        )

    assert excinfo.value.pid == FAKE_PID
    assert f"pidprobe doctor {FAKE_PID}" in str(excinfo.value)


def test_execute_in_target_wraps_attach_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _deny(*_args: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(sys, "remote_exec", _deny)

    with pytest.raises(AttachError, match="task_for_pid") as excinfo:
        execute_in_target(FAKE_PID, "payload = {}")

    assert isinstance(excinfo.value.__cause__, PermissionError)
