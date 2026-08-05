"""Tests for the script pidprobe generates and injects into a target.

The generated script is exercised for real by running it in a fresh
interpreter while a channel listens, which covers everything except
``sys.remote_exec`` itself (that needs a live target, see
``test_channel_integration.py``).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from pidprobe._channel import TempFileChannel, UnixSocketChannel, open_channel
from pidprobe._deadline import Deadline
from pidprobe._envelope import parse_envelope
from pidprobe._inject import (
    NAME_PREFIX,
    ChannelKind,
    ChannelSpec,
    build_injection_script,
    write_injection_script,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pidprobe._channel import ReturnChannel
    from pidprobe._envelope import Envelope

# Placeholder path for tests that only inspect the generated source.
FAKE_CHANNEL_PATH = "/tmp/pidprobe-not-used"  # noqa: S108 -- never created
SCRIPT_TIMEOUT_SECONDS = 30.0
RECEIVE_TIMEOUT_SECONDS = 10.0

FRAME_COLLECTOR = """
frames = []
for _tid, frame in _pidprobe_target_frames().items():
    while frame is not None:
        frames.append({"file": frame.f_code.co_filename, "name": frame.f_code.co_name})
        frame = frame.f_back
payload = {"frames": frames}
"""


def _spec(channel: ReturnChannel) -> ChannelSpec:
    return ChannelSpec(
        kind=channel.kind,
        path=channel.path,
        timeout_seconds=RECEIVE_TIMEOUT_SECONDS,
    )


def _run_generated(
    collector_source: str,
    channel: ReturnChannel,
    tmp_path: Path,
) -> tuple[Envelope, subprocess.CompletedProcess[str]]:
    """Run the generated script in a fresh interpreter and read the answer."""
    script_path = write_injection_script(collector_source, _spec(channel), tmp_path)
    completed = subprocess.run(  # noqa: S603 -- generated script, trusted argv
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT_SECONDS,
        check=False,
    )
    raw = channel.receive(Deadline.start(RECEIVE_TIMEOUT_SECONDS))
    return parse_envelope(raw), completed


def test_generated_script_is_valid_python() -> None:
    source = build_injection_script(
        "payload = {'a': 1}",
        ChannelSpec(ChannelKind.UNIX_SOCKET, FAKE_CHANNEL_PATH, 5.0),
    )

    compile(source, "<generated>", "exec")


@pytest.mark.parametrize(
    "kind",
    [ChannelKind.UNIX_SOCKET, ChannelKind.TEMPFILE],
    ids=["socket", "tempfile"],
)
def test_generated_script_imports_only_the_standard_library(kind: ChannelKind) -> None:
    source = build_injection_script(
        "payload = {'a': 1}",
        ChannelSpec(kind, FAKE_CHANNEL_PATH, 5.0),
    )

    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported |= {
        node.module.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert imported
    assert imported <= set(sys.stdlib_module_names), imported


def test_generated_script_only_binds_namespaced_module_level_names() -> None:
    source = build_injection_script(
        "payload = {'a': 1}",
        ChannelSpec(ChannelKind.TEMPFILE, FAKE_CHANNEL_PATH, 5.0),
    )

    bound = {
        node.name
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef | ast.ClassDef)
    } | {
        target.id
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    assert bound == {f"{NAME_PREFIX}main"}


def test_generated_script_deletes_its_own_binding() -> None:
    source = build_injection_script(
        "payload = {}", ChannelSpec(ChannelKind.TEMPFILE, FAKE_CHANNEL_PATH, 5.0)
    )

    assert f"del {NAME_PREFIX}main" in source


def test_collector_source_with_braces_survives_rendering() -> None:
    source = build_injection_script(
        "payload = {'literal': '{not a placeholder}', 'dollar': '$spec'}",
        ChannelSpec(ChannelKind.TEMPFILE, FAKE_CHANNEL_PATH, 5.0),
    )

    assert "'{not a placeholder}'" in source
    assert "'$spec'" in source


def test_socket_round_trip_returns_the_collector_payload(tmp_path: Path) -> None:
    channel = UnixSocketChannel()
    try:
        envelope, completed = _run_generated(
            "payload = {'answer': 42}", channel, tmp_path
        )
    finally:
        channel.close()

    assert completed.returncode == 0, completed.stderr
    assert envelope == {"status": "ok", "error": None, "payload": {"answer": 42}}


def test_tempfile_round_trip_returns_the_collector_payload(tmp_path: Path) -> None:
    channel = TempFileChannel()
    try:
        envelope, completed = _run_generated(
            "payload = {'answer': 42}", channel, tmp_path
        )
    finally:
        channel.close()

    assert completed.returncode == 0, completed.stderr
    assert envelope["payload"] == {"answer": 42}


def test_raising_collector_still_answers_with_an_error_envelope(tmp_path: Path) -> None:
    channel = open_channel()
    try:
        envelope, completed = _run_generated(
            "raise RuntimeError('collector exploded')",
            channel,
            tmp_path,
        )
    finally:
        channel.close()

    assert completed.returncode == 0, completed.stderr
    assert envelope["status"] == "error"
    assert envelope["payload"] is None
    error = envelope["error"]
    assert error is not None
    assert error["type"] == "RuntimeError"
    assert error["message"] == "collector exploded"
    assert "RuntimeError: collector exploded" in error["traceback"]


def test_unserializable_payload_answers_with_an_error_envelope(tmp_path: Path) -> None:
    channel = open_channel()
    try:
        envelope, _ = _run_generated("payload = {'obj': object()}", channel, tmp_path)
    finally:
        channel.close()

    assert envelope["status"] == "error"
    error = envelope["error"]
    assert error is not None
    assert error["type"] == "TypeError"


def test_reported_frames_exclude_the_injected_script(tmp_path: Path) -> None:
    channel = open_channel()
    try:
        envelope, _ = _run_generated(FRAME_COLLECTOR, channel, tmp_path)
    finally:
        channel.close()

    payload = envelope["payload"]
    assert payload is not None
    # Standalone, *every* frame on the running thread belongs to the injected
    # script, so a correct filter leaves nothing behind. Without the filter
    # the collector would report pidprobe's own frames as target code.
    assert payload["frames"] == []


def test_write_back_failure_leaves_the_target_process_intact(tmp_path: Path) -> None:
    """A dead channel is pidprobe's problem: the target must not notice."""
    spec = ChannelSpec(ChannelKind.TEMPFILE, str(tmp_path / "gone" / "r.json"), 1.0)
    script_path = write_injection_script("payload = {'a': 1}", spec, tmp_path)

    completed = subprocess.run(  # noqa: S603 -- generated script, trusted argv
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT_SECONDS,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
