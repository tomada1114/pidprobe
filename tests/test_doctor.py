"""Tests for ``pidprobe doctor``: the checks, the verdict and the report."""

from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING

import pytest

from pidprobe import _attach_policy, _doctor, _procinfo, _target_python
from pidprobe._attach_policy import (
    check_pid_namespace,
    check_ptrace_scope,
    check_target_owner,
    check_target_process,
    check_task_for_pid,
)
from pidprobe._diagnosis import Check, CheckStatus, Diagnosis, as_document, render_text
from pidprobe._doctor import diagnose
from pidprobe._procinfo import Interpreter
from pidprobe._target_python import check_target_python, check_target_remote_debug
from pidprobe.collectors import BUILTIN_COLLECTORS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

FAKE_PID = 4321
NAMESPACE = "pid:[4026531836]"
OTHER_NAMESPACE = "pid:[4026532000]"
ENVIRONMENT_CHECKS = (
    "prober_remote_debug",
    "return_channel",
    "collector_plugins",
    "ptrace_scope",
    "task_for_pid",
)


@pytest.fixture
def fake_proc(monkeypatch, tmp_path):
    """Redirect procfs reads at a directory the test owns."""
    monkeypatch.setattr(_procinfo, "PROC", tmp_path)
    return tmp_path


@pytest.fixture
def on_linux(monkeypatch):
    """Make the platform-dependent checks take their Linux branch."""
    monkeypatch.setattr(sys, "platform", "linux")


@pytest.fixture
def on_macos(monkeypatch):
    """Make the platform-dependent checks take their macOS branch."""
    monkeypatch.setattr(sys, "platform", "darwin")


def make_check(status: CheckStatus = CheckStatus.OK, **overrides: str) -> Check:
    """Build a check, filling in whatever the status requires."""
    is_explained = status in (CheckStatus.WARN, CheckStatus.FAIL)
    fields = {
        "name": "example",
        "summary": "s",
        "cause": "c" if is_explained else "",
        "confirm": "run this" if is_explained else "",
        "fix": "do that" if is_explained else "",
    }
    return Check(status=status, **(fields | overrides))


def write_ptrace_scope(root: Path, value: str) -> None:
    """Populate the fake procfs with a Yama ptrace_scope knob."""
    path = root / "sys" / "kernel" / "yama" / "ptrace_scope"
    path.parent.mkdir(parents=True)
    path.write_text(value)


def write_environ(root: Path, pid: int, raw: bytes) -> None:
    """Populate the fake procfs with a target's initial environment."""
    directory = root / str(pid)
    directory.mkdir(parents=True)
    (directory / "environ").write_bytes(raw)


def write_namespace(root: Path, pid: str, target: str) -> None:
    """Populate the fake procfs with a PID namespace link."""
    directory = root / pid / "ns"
    directory.mkdir(parents=True)
    (directory / "pid").symlink_to(target)


class TestCheckContract:
    @pytest.mark.parametrize(
        "status",
        [
            pytest.param(CheckStatus.WARN, id="warn"),
            pytest.param(CheckStatus.FAIL, id="fail"),
        ],
    )
    @pytest.mark.parametrize(
        "missing",
        [
            pytest.param("cause", id="no-cause"),
            pytest.param("confirm", id="no-confirm"),
            pytest.param("fix", id="no-fix"),
        ],
    )
    def test_a_reported_problem_without_all_three_explanations_is_rejected(
        self,
        status,
        missing,
    ):
        with pytest.raises(ValueError, match="must carry a cause"):
            make_check(status, **{missing: ""})

    def test_a_passing_check_needs_no_explanation(self):
        assert make_check().details() is not None

    def test_details_are_reported_in_a_fixed_order(self):
        labels = [label for label, _ in make_check(CheckStatus.FAIL).details()]

        assert labels == ["cause", "confirm", "fix"]


class TestProberRemoteDebug:
    def test_an_interpreter_without_remote_exec_fails(self, monkeypatch):
        monkeypatch.delattr(sys, "remote_exec", raising=False)

        check = _named(diagnose(), "prober_remote_debug")

        assert check.status is CheckStatus.FAIL
        assert "sys.remote_exec" in check.summary
        assert "--without-remote-debug" in check.cause

    def test_disabled_remote_debugging_fails_with_the_variable_named(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(sys, "is_remote_debug_enabled", lambda: False)

        check = _named(diagnose(), "prober_remote_debug")

        assert check.status is CheckStatus.FAIL
        assert "PYTHON_DISABLE_REMOTE_DEBUG" in check.cause
        assert "PYTHON_DISABLE_REMOTE_DEBUG" in check.fix

    def test_this_test_run_can_inject(self):
        assert _named(diagnose(), "prober_remote_debug").status is CheckStatus.OK


class TestReturnChannel:
    def test_a_bindable_socket_passes(self):
        check = _named(diagnose(), "return_channel")

        assert check.status is CheckStatus.OK
        assert check.summary.endswith(".sock")

    def test_an_unbindable_socket_warns_about_the_file_fallback(self, monkeypatch):
        monkeypatch.setattr(_doctor, "UnixSocketChannel", _raising_channel)

        check = _named(diagnose(), "return_channel")

        assert check.status is CheckStatus.WARN
        assert "sun_path" in check.cause
        assert "TMPDIR" in check.fix

    def test_no_usable_channel_at_all_fails(self, monkeypatch):
        monkeypatch.setattr(_doctor, "UnixSocketChannel", _raising_channel)
        monkeypatch.setattr(_doctor, "TempFileChannel", _raising_channel)

        check = _named(diagnose(), "return_channel")

        assert check.status is CheckStatus.FAIL
        assert "nowhere to send its result" in check.cause


def _raising_channel():
    """Stand in for a channel constructor that cannot open anything."""
    message = "no channel here"
    raise OSError(message)


class TestCollectorPlugins:
    def test_healthy_discovery_lists_what_will_run(self):
        check = _named(diagnose(), "collector_plugins")

        assert check.status is CheckStatus.OK
        assert "stacks" in check.summary

    def test_a_skipped_plugin_is_surfaced_with_the_reason_discovery_logged(
        self,
        monkeypatch,
    ):
        def noisy_discovery():
            logging.getLogger("pidprobe.registry").warning(
                "skipping collector plugin 'broken': it could not be loaded",
            )
            return BUILTIN_COLLECTORS

        monkeypatch.setattr(_doctor, "available_collectors", noisy_discovery)

        check = _named(diagnose(), "collector_plugins")

        assert check.status is CheckStatus.WARN
        assert "broken" in check.cause
        assert "own snapshot section" in check.fix

    def test_the_registry_logger_is_left_as_it_was_found(self):
        logger = logging.getLogger("pidprobe.registry")
        before = (logger.level, logger.propagate, list(logger.handlers))

        diagnose()

        assert (logger.level, logger.propagate, list(logger.handlers)) == before


class TestPtraceScope:
    @pytest.mark.parametrize(
        ("value", "status"),
        [
            pytest.param("0", CheckStatus.OK, id="unrestricted"),
            pytest.param("1", CheckStatus.WARN, id="descendants-only"),
            pytest.param("2", CheckStatus.FAIL, id="admin-only"),
            pytest.param("3", CheckStatus.FAIL, id="no-attach"),
            pytest.param("9", CheckStatus.FAIL, id="unknown-level"),
        ],
    )
    @pytest.mark.usefixtures("on_linux")
    def test_each_yama_level_is_judged_and_explained(self, fake_proc, value, status):
        write_ptrace_scope(fake_proc, value)

        check = check_ptrace_scope()

        assert check.status is status
        if status is not CheckStatus.OK:
            assert check.confirm == "cat /proc/sys/kernel/yama/ptrace_scope"
            assert "ptrace_scope" in check.fix

    @pytest.mark.usefixtures("on_linux", "fake_proc")
    def test_a_kernel_without_yama_does_not_restrict_attaching(self):
        assert check_ptrace_scope().status is CheckStatus.OK

    @pytest.mark.usefixtures("on_macos")
    def test_the_check_is_skipped_off_linux(self):
        check = check_ptrace_scope()

        assert check.status is CheckStatus.SKIPPED
        assert "Linux" in check.summary


class TestTaskForPid:
    @pytest.mark.usefixtures("on_macos")
    def test_a_non_root_prober_fails_on_macos(self, monkeypatch):
        monkeypatch.setattr(os, "geteuid", lambda: 501)

        check = check_task_for_pid()

        assert check.status is CheckStatus.FAIL
        assert "task port" in check.cause
        assert "sudo" in check.fix

    @pytest.mark.usefixtures("on_macos")
    def test_root_may_take_the_task_port(self, monkeypatch):
        monkeypatch.setattr(os, "geteuid", lambda: 0)

        assert check_task_for_pid().status is CheckStatus.OK

    @pytest.mark.usefixtures("on_linux")
    def test_the_check_is_skipped_off_macos(self):
        assert check_task_for_pid().status is CheckStatus.SKIPPED


class TestTargetProcess:
    def test_a_process_this_user_owns_passes(self):
        assert check_target_process(os.getpid()).status is CheckStatus.OK

    def test_a_pid_nobody_uses_fails_with_a_way_to_find_the_real_one(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(os, "kill", _raiser(ProcessLookupError))

        check = check_target_process(FAKE_PID)

        assert check.status is CheckStatus.FAIL
        assert "pgrep" in check.fix

    def test_an_unsignalable_process_fails_with_more_than_permission_denied(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(os, "kill", _raiser(PermissionError))

        check = check_target_process(FAKE_PID)

        assert check.status is CheckStatus.FAIL
        assert list(check.details()) == [
            ("cause", check.cause),
            ("confirm", check.confirm),
            ("fix", check.fix),
        ]
        assert check.cause != "Permission denied"


def _raiser(error: type[BaseException]) -> Callable[..., None]:
    """Build a stand-in for ``os.kill`` that always raises *error*."""

    def _raise(*_args: object) -> None:
        message = "denied"
        raise error(message)

    return _raise


class TestTargetOwner:
    def test_a_shared_uid_passes(self, monkeypatch):
        monkeypatch.setattr(_attach_policy, "process_uid", lambda _pid: 501)
        monkeypatch.setattr(os, "geteuid", lambda: 501)

        assert check_target_owner(FAKE_PID).status is CheckStatus.OK

    def test_root_may_attach_across_users(self, monkeypatch):
        monkeypatch.setattr(_attach_policy, "process_uid", lambda _pid: 501)
        monkeypatch.setattr(os, "geteuid", lambda: 0)

        assert check_target_owner(FAKE_PID).status is CheckStatus.OK

    def test_a_foreign_owner_fails_with_the_two_uids_named(self, monkeypatch):
        monkeypatch.setattr(_attach_policy, "process_uid", lambda _pid: 0)
        monkeypatch.setattr(os, "geteuid", lambda: 501)

        check = check_target_owner(FAKE_PID)

        assert check.status is CheckStatus.FAIL
        assert "uid 0" in check.summary
        assert "uid 501" in check.summary
        assert "sudo -u '#0'" in check.fix

    def test_an_unknown_owner_is_skipped_with_a_way_to_look_it_up(self, monkeypatch):
        monkeypatch.setattr(_attach_policy, "process_uid", lambda _pid: None)

        check = check_target_owner(FAKE_PID)

        assert check.status is CheckStatus.SKIPPED
        assert check.confirm.startswith("ps ")


class TestPidNamespace:
    @pytest.mark.usefixtures("on_linux")
    def test_a_shared_namespace_passes(self, fake_proc):
        write_namespace(fake_proc, "self", NAMESPACE)
        write_namespace(fake_proc, str(FAKE_PID), NAMESPACE)

        assert check_pid_namespace(FAKE_PID).status is CheckStatus.OK

    @pytest.mark.usefixtures("on_linux")
    def test_a_container_boundary_fails_with_a_way_across_it(self, fake_proc):
        write_namespace(fake_proc, "self", NAMESPACE)
        write_namespace(fake_proc, str(FAKE_PID), OTHER_NAMESPACE)

        check = check_pid_namespace(FAKE_PID)

        assert check.status is CheckStatus.FAIL
        assert "nsenter" in check.fix
        assert "docker exec" in check.fix

    @pytest.mark.usefixtures("on_linux", "fake_proc")
    def test_unreadable_namespaces_are_skipped(self):
        assert check_pid_namespace(FAKE_PID).status is CheckStatus.SKIPPED

    @pytest.mark.usefixtures("on_macos")
    def test_the_check_is_skipped_off_linux(self):
        assert check_pid_namespace(FAKE_PID).status is CheckStatus.SKIPPED


class TestTargetPython:
    def test_a_matching_cpython_passes_both_checks(self, monkeypatch):
        _fake_interpreter(monkeypatch, version=_this_version())

        checks = check_target_python(FAKE_PID)

        assert [check.name for check in checks] == [
            "target_python_version",
            "target_python_match",
        ]
        assert all(check.status is CheckStatus.OK for check in checks)

    def test_a_target_older_than_3_14_fails_with_the_minimum_named(self, monkeypatch):
        _fake_interpreter(monkeypatch, version=(3, 13, 2))

        version, _ = check_target_python(FAKE_PID)

        assert version.status is CheckStatus.FAIL
        assert "3.14" in version.cause
        assert "py-spy" in version.fix

    def test_another_implementation_fails(self, monkeypatch):
        _fake_interpreter(monkeypatch, implementation="pypy", version=(3, 14, 0))

        version, _ = check_target_python(FAKE_PID)

        assert version.status is CheckStatus.FAIL
        assert "pypy" in version.summary

    def test_a_different_feature_release_fails_the_match_check_only(self, monkeypatch):
        major, minor = sys.version_info[:2]
        _fake_interpreter(monkeypatch, version=(major, minor + 1, 0))

        version, match = check_target_python(FAKE_PID)

        assert version.status is CheckStatus.OK
        assert match.status is CheckStatus.FAIL
        assert f"{major}.{minor + 1}" in match.fix

    def test_an_unidentifiable_interpreter_warns_once(self, monkeypatch):
        _fake_interpreter(monkeypatch, version=None, reason="nothing to read")

        checks = check_target_python(FAKE_PID)

        assert [check.name for check in checks] == ["target_python"]
        assert checks[0].status is CheckStatus.WARN
        assert checks[0].cause == "nothing to read"


def _fake_interpreter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    version: tuple[int, int, int] | None,
    implementation: str = "cpython",
    reason: str | None = None,
) -> None:
    """Make the target look like a chosen interpreter."""
    interpreter = Interpreter(
        executable="/usr/bin/python3",
        implementation=implementation,
        version=version,
        reason=reason,
    )
    monkeypatch.setattr(_target_python, "target_interpreter", lambda _pid: interpreter)


class TestTargetRemoteDebug:
    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param(b"PYTHON_DISABLE_REMOTE_DEBUG=1\0", id="set-to-one"),
            pytest.param(b"PYTHON_DISABLE_REMOTE_DEBUG=\0", id="set-to-empty"),
        ],
    )
    @pytest.mark.usefixtures("on_linux")
    def test_the_variable_in_the_targets_environment_fails(self, fake_proc, raw):
        write_environ(fake_proc, FAKE_PID, raw)

        check = check_target_remote_debug(FAKE_PID)

        assert check.status is CheckStatus.FAIL
        assert "the empty string counts" in check.cause
        assert "restart the target" in check.fix

    @pytest.mark.usefixtures("on_linux")
    def test_an_untouched_environment_passes(self, fake_proc):
        write_environ(fake_proc, FAKE_PID, b"PATH=/bin\0")

        assert check_target_remote_debug(FAKE_PID).status is CheckStatus.OK

    @pytest.mark.usefixtures("on_linux", "fake_proc")
    def test_an_unreadable_environment_is_skipped_with_a_command_to_try(self):
        check = check_target_remote_debug(FAKE_PID)

        assert check.status is CheckStatus.SKIPPED
        assert "sudo" in check.confirm

    @pytest.mark.usefixtures("on_macos")
    def test_the_check_is_skipped_off_linux(self):
        assert check_target_remote_debug(FAKE_PID).status is CheckStatus.SKIPPED


class TestDiagnose:
    def test_without_a_pid_only_the_environment_is_examined(self):
        diagnosis = diagnose()

        assert diagnosis.pid is None
        assert tuple(check.name for check in diagnosis.checks) == ENVIRONMENT_CHECKS

    def test_a_pid_adds_the_target_checks(self):
        diagnosis = diagnose(os.getpid())

        names = [check.name for check in diagnosis.checks]
        assert names[: len(ENVIRONMENT_CHECKS)] == list(ENVIRONMENT_CHECKS)
        assert "target_process" in names
        assert "target_remote_debug" in names

    def test_every_problem_it_reports_is_actionable(self):
        diagnosis = diagnose(os.getpid())

        reported = diagnosis.failures + diagnosis.warnings
        assert all(check.cause and check.confirm and check.fix for check in reported)

    def test_a_failing_check_makes_the_target_unattachable(self):
        diagnosis = Diagnosis(pid=FAKE_PID, checks=(make_check(CheckStatus.FAIL),))

        assert diagnosis.is_attachable is False
        assert len(diagnosis.failures) == 1

    def test_a_warning_alone_leaves_the_target_attachable(self):
        diagnosis = Diagnosis(pid=FAKE_PID, checks=(make_check(CheckStatus.WARN),))

        assert diagnosis.is_attachable is True
        assert len(diagnosis.warnings) == 1


class TestReport:
    def test_text_spells_out_cause_confirm_and_fix_for_a_failure(self):
        diagnosis = Diagnosis(pid=FAKE_PID, checks=(make_check(CheckStatus.FAIL),))

        text = render_text(diagnosis)

        assert "cause: c" in text
        assert "confirm: run this" in text
        assert "fix: do that" in text
        assert f"1 check failed; attaching to pid {FAKE_PID} will not work" in text

    def test_text_without_a_pid_says_so(self):
        text = render_text(Diagnosis(pid=None, checks=(make_check(),)))

        assert "no pid given" in text
        assert "nothing blocks attaching to a target" in text

    def test_text_counts_failures_and_warnings(self):
        diagnosis = Diagnosis(
            pid=FAKE_PID,
            checks=(
                make_check(CheckStatus.FAIL, name="one"),
                make_check(CheckStatus.FAIL, name="two"),
                make_check(CheckStatus.WARN, name="three"),
            ),
        )

        assert "2 checks failed" in render_text(diagnosis)
        assert "(1 warning)" in render_text(diagnosis)

    def test_json_omits_empty_explanations_and_keeps_the_verdict(self):
        diagnosis = Diagnosis(
            pid=FAKE_PID,
            checks=(make_check(name="fine"), make_check(CheckStatus.FAIL, name="bad")),
        )

        document = as_document(diagnosis)

        assert document["pid"] == FAKE_PID
        assert document["attachable"] is False
        assert document["checks"][0] == {
            "name": "fine",
            "status": "ok",
            "summary": "s",
        }
        assert document["checks"][1]["fix"] == "do that"


def _this_version() -> tuple[int, int, int]:
    """Return the running interpreter's version as a fixed-width tuple."""
    return (sys.version_info[0], sys.version_info[1], sys.version_info[2])


def _named(diagnosis: Diagnosis, name: str) -> Check:
    """Return the one check with *name*, failing the test if it is missing."""
    for check in diagnosis.checks:
        if check.name == name:
            return check
    pytest.fail(f"no check named {name!r} in {[c.name for c in diagnosis.checks]}")
