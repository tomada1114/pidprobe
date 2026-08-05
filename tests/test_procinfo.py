"""Tests for the read-only process and platform introspection layer."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from pidprobe import _procinfo
from pidprobe._procinfo import (
    NO_EXECUTABLE,
    NOT_AN_INTERPRETER,
    NOT_INTROSPECTABLE,
    UNQUERYABLE,
    pid_namespace,
    process_environ,
    process_uid,
    ptrace_scope,
    target_interpreter,
)

FAKE_PID = 4321
NAMESPACE = "pid:[4026531836]"


@pytest.fixture
def fake_proc(monkeypatch, tmp_path):
    """Redirect procfs reads at a directory the test owns."""
    monkeypatch.setattr(_procinfo, "PROC", tmp_path)
    return tmp_path


@pytest.fixture
def on_linux(monkeypatch):
    """Make the platform-dependent lookups take their Linux branch."""
    monkeypatch.setattr(sys, "platform", "linux")


@pytest.fixture
def on_macos(monkeypatch):
    """Make the platform-dependent lookups take their macOS branch."""
    monkeypatch.setattr(sys, "platform", "darwin")


def write_namespace(root: Path, pid: str, target: str) -> None:
    """Create a ``/proc/<pid>/ns/pid`` symlink pointing at *target*."""
    directory = root / pid / "ns"
    directory.mkdir(parents=True)
    (directory / "pid").symlink_to(target)


class TestPtraceScope:
    @pytest.mark.parametrize("raw", ["0", "1\n", " 2 "])
    def test_a_numeric_knob_is_read(self, fake_proc, raw):
        path = fake_proc / "sys" / "kernel" / "yama" / "ptrace_scope"
        path.parent.mkdir(parents=True)
        path.write_text(raw)

        assert ptrace_scope() == int(raw)

    @pytest.mark.usefixtures("fake_proc")
    def test_a_missing_knob_reads_as_no_restriction(self):
        assert ptrace_scope() is None

    def test_a_non_numeric_knob_reads_as_unknown(self, fake_proc):
        path = fake_proc / "sys" / "kernel" / "yama" / "ptrace_scope"
        path.parent.mkdir(parents=True)
        path.write_text("enabled")

        assert ptrace_scope() is None


class TestProcessUid:
    @pytest.mark.usefixtures("on_linux")
    def test_linux_reads_the_owner_of_the_proc_entry(self, fake_proc):
        (fake_proc / str(FAKE_PID)).mkdir()

        assert process_uid(FAKE_PID) == os.getuid()

    @pytest.mark.usefixtures("on_linux", "fake_proc")
    def test_an_unknown_pid_has_no_owner(self):
        assert process_uid(FAKE_PID) is None

    @pytest.mark.skipif(not Path("/bin/ps").exists(), reason="needs /bin/ps")
    @pytest.mark.usefixtures("on_macos")
    def test_macos_reads_the_owner_from_ps(self):
        assert process_uid(os.getpid()) == os.getuid()

    def test_an_unsupported_platform_reports_nothing(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")

        assert process_uid(os.getpid()) is None


class TestPidNamespace:
    def test_a_namespace_link_is_read(self, fake_proc):
        write_namespace(fake_proc, str(FAKE_PID), NAMESPACE)

        assert pid_namespace(FAKE_PID) == NAMESPACE

    @pytest.mark.usefixtures("fake_proc")
    def test_a_missing_link_reports_nothing(self):
        assert pid_namespace("self") is None


class TestProcessEnviron:
    def test_the_null_separated_environment_is_parsed(self, fake_proc):
        directory = fake_proc / str(FAKE_PID)
        directory.mkdir()
        (directory / "environ").write_bytes(b"PATH=/bin\0LANG=C.UTF-8\0")

        assert process_environ(FAKE_PID) == {"PATH": "/bin", "LANG": "C.UTF-8"}

    @pytest.mark.usefixtures("fake_proc")
    def test_an_unreadable_environment_reports_nothing(self):
        assert process_environ(FAKE_PID) is None


class TestTargetInterpreter:
    def test_this_process_is_identified_on_the_real_platform(self):
        interpreter = target_interpreter(os.getpid())

        assert interpreter.reason is None
        assert interpreter.implementation == sys.implementation.name
        assert interpreter.version == tuple(sys.version_info[:3])

    def test_a_process_without_an_executable_is_not_identified(self, monkeypatch):
        monkeypatch.setattr(_procinfo, "process_executable", lambda _pid: None)

        assert target_interpreter(FAKE_PID).reason == NO_EXECUTABLE

    def test_an_unsupported_platform_is_named_as_the_reason(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")

        assert target_interpreter(FAKE_PID).reason == NOT_INTROSPECTABLE

    def test_a_non_interpreter_binary_is_never_executed(self, monkeypatch):
        monkeypatch.setattr(_procinfo, "process_executable", lambda _pid: "/bin/sleep")

        interpreter = target_interpreter(FAKE_PID)

        assert interpreter.reason == NOT_AN_INTERPRETER
        assert interpreter.version is None

    def test_an_interpreter_that_cannot_be_run_is_reported(self, monkeypatch, tmp_path):
        missing = tmp_path / "python3.14"
        monkeypatch.setattr(_procinfo, "process_executable", lambda _pid: str(missing))

        assert target_interpreter(FAKE_PID).reason == UNQUERYABLE

    def test_unexpected_version_output_is_reported(self, monkeypatch, tmp_path):
        liar = tmp_path / "python-liar"
        liar.write_text("#!/bin/sh\necho not a version\n")
        liar.chmod(0o755)
        monkeypatch.setattr(_procinfo, "process_executable", lambda _pid: str(liar))

        assert target_interpreter(FAKE_PID).reason == UNQUERYABLE
