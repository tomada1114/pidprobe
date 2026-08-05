"""Tests for the pidprobe command-line interface."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest

from pidprobe import __version__, available_collectors
from pidprobe import _commands as commands_module
from pidprobe._diagnosis import Check, CheckStatus, Diagnosis
from pidprobe._errors import (
    AttachError,
    ChannelError,
    NoSuchProcessError,
    ProbeTimeoutError,
    TargetError,
)
from pidprobe._exits import EXIT_CODE_TABLE
from pidprobe.cli import (
    EXIT_ATTACH_FAILED,
    EXIT_BROKEN_PIPE,
    EXIT_DIAGNOSIS_FAILED,
    EXIT_INTERNAL_ERROR,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_PROBE_ERROR,
    EXIT_TARGET_ERROR,
    EXIT_TARGET_NOT_FOUND,
    EXIT_TIMEOUT,
    EXIT_USAGE,
    build_parser,
    main,
)
from pidprobe.collectors import STACKS_COLLECTOR
from pidprobe.collectors._stacks import build_stacks_collector

if TYPE_CHECKING:
    from pathlib import Path

SNAPSHOT = {"schema_version": "1.0", "meta": {"pid": 4321}, "gc": {"enabled": True}}
EVALUATION = {
    "pid": 4321,
    "expression": "1 + 1",
    "type": "int",
    "result": "2",
    "masking_enabled": True,
}


@pytest.fixture
def fake_snapshot(monkeypatch):
    """Make ``snap`` return a fixed document and record its arguments."""
    calls: dict[str, Any] = {}

    def fake_take_snapshot(pid: int, **kwargs: Any) -> dict[str, Any]:
        calls.update(pid=pid, **kwargs)
        return SNAPSHOT

    monkeypatch.setattr(commands_module, "take_snapshot", fake_take_snapshot)
    return calls


@pytest.fixture
def failing_snapshot(monkeypatch):
    """Make ``snap`` fail the way an unreachable target does."""

    def fake_take_snapshot(pid: int, **kwargs: Any) -> dict[str, Any]:
        raise ProbeTimeoutError(5.0, pid)

    monkeypatch.setattr(commands_module, "take_snapshot", fake_take_snapshot)


class TestSnapOutput:
    @pytest.mark.usefixtures("fake_snapshot")
    def test_default_output_is_a_single_compact_json_line(self, capsys):
        exit_code = main(["snap", "4321"])

        out = capsys.readouterr().out
        assert exit_code == EXIT_OK
        assert out.count("\n") == 1
        assert out.rstrip("\n") == json.dumps(SNAPSHOT, separators=(",", ":"))
        assert json.loads(out) == SNAPSHOT

    @pytest.mark.usefixtures("fake_snapshot")
    def test_pretty_output_is_indented_json(self, capsys):
        exit_code = main(["snap", "4321", "--pretty"])

        out = capsys.readouterr().out
        assert exit_code == EXIT_OK
        assert out.count("\n") > 1
        assert '\n  "meta": {' in out
        assert json.loads(out) == SNAPSHOT

    def test_timeout_defaults_and_overrides_reach_the_snapshot(self, fake_snapshot):
        main(["snap", "4321"])
        assert fake_snapshot["timeout_seconds"] == pytest.approx(5.0)

        main(["snap", "4321", "--timeout", "0.5"])
        assert fake_snapshot["pid"] == 4321
        assert fake_snapshot["timeout_seconds"] == pytest.approx(0.5)


class TestSnapMasking:
    def test_masking_is_on_by_default(self, fake_snapshot):
        main(["snap", "4321"])

        # None lets take_snapshot pick the built-in collectors, whose stacks
        # collector masks credentials.
        assert fake_snapshot["collectors"] is None

    def test_no_mask_swaps_in_an_unmasked_stacks_collector(self, fake_snapshot):
        main(["snap", "4321", "--no-mask"])

        collectors = fake_snapshot["collectors"]
        # Everything a default snapshot would run, plugins included -- only
        # the stacks collector is swapped.
        assert [collector.name for collector in collectors] == [
            collector.name for collector in available_collectors()
        ]
        assert STACKS_COLLECTOR not in collectors
        by_name = {collector.name: collector for collector in collectors}
        assert by_name["stacks"] == build_stacks_collector(is_masked=False)

    def test_parser_defaults_to_masking(self):
        args = build_parser().parse_args(["snap", "4321"])

        assert args.is_masked is True


@pytest.fixture
def fake_evaluation(monkeypatch):
    """Make ``eval`` return a fixed document and record its arguments."""
    calls: dict[str, Any] = {}

    def fake_evaluate_in_target(
        pid: int,
        expression: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        calls.update(pid=pid, expression=expression, **kwargs)
        return EVALUATION

    monkeypatch.setattr(commands_module, "evaluate_in_target", fake_evaluate_in_target)
    return calls


class TestEval:
    @pytest.mark.usefixtures("fake_evaluation")
    def test_default_output_is_a_single_compact_json_line(self, capsys):
        exit_code = main(["eval", "4321", "1 + 1"])

        out = capsys.readouterr().out
        assert exit_code == EXIT_OK
        assert out.count("\n") == 1
        assert json.loads(out) == EVALUATION

    @pytest.mark.usefixtures("fake_evaluation")
    def test_pretty_output_is_indented_json(self, capsys):
        main(["eval", "4321", "1 + 1", "--pretty"])

        out = capsys.readouterr().out
        assert out.count("\n") > 1
        assert json.loads(out) == EVALUATION

    def test_expression_timeout_and_masking_reach_the_evaluation(
        self,
        fake_evaluation,
    ):
        main(["eval", "4321", "api_key", "--timeout", "0.5", "--no-mask"])

        assert fake_evaluation["pid"] == 4321
        assert fake_evaluation["expression"] == "api_key"
        assert fake_evaluation["timeout_seconds"] == pytest.approx(0.5)
        assert fake_evaluation["is_masked"] is False

    def test_masking_is_on_by_default(self, fake_evaluation):
        main(["eval", "4321", "api_key"])

        assert fake_evaluation["is_masked"] is True
        assert fake_evaluation["timeout_seconds"] == pytest.approx(5.0)

    def test_probe_failure_is_explained_on_stderr(self, monkeypatch, capsys):
        def failing(pid: int, expression: str, **kwargs: Any) -> dict[str, Any]:
            raise ProbeTimeoutError(5.0, pid)

        monkeypatch.setattr(commands_module, "evaluate_in_target", failing)

        exit_code = main(["eval", "4321", "1 + 1"])

        captured = capsys.readouterr()
        assert exit_code == EXIT_TIMEOUT
        assert captured.out == ""
        assert captured.err.startswith("pidprobe: ")


DELTAS = [
    {
        "schema_version": "1.0",
        "meta": {"pid": 4321, "interval_ms": 1000.0},
        "objects": {"types": [{"type": "Leak", "before": 1, "after": 9, "delta": 8}]},
    },
    {
        "schema_version": "1.0",
        "meta": {"pid": 4321, "interval_ms": 1000.0},
        "objects": {"types": []},
    },
]


@pytest.fixture
def fake_deltas(monkeypatch):
    """Make ``diff`` yield fixed deltas and record how it was asked to sample."""
    calls: dict[str, Any] = {}

    def fake_iter_snapshot_deltas(pid: int, **kwargs: Any) -> Any:
        calls.update(pid=pid, **kwargs)
        yield from DELTAS

    monkeypatch.setattr(
        commands_module,
        "iter_snapshot_deltas",
        fake_iter_snapshot_deltas,
    )
    return calls


class TestDiff:
    @pytest.mark.usefixtures("fake_deltas")
    def test_every_delta_is_printed_as_its_own_json_line(self, capsys):
        exit_code = main(["diff", "4321", "--interval", "0.01", "--count", "3"])

        out = capsys.readouterr().out
        assert exit_code == EXIT_OK
        # JSON Lines: one delta per line, so the stream can be consumed as it
        # arrives instead of only after the last sample.
        assert [json.loads(line) for line in out.splitlines()] == DELTAS

    @pytest.mark.usefixtures("fake_deltas")
    def test_pretty_output_indents_each_delta(self, capsys):
        main(["diff", "4321", "--interval", "0.01", "--pretty"])

        out = capsys.readouterr().out
        assert '\n  "meta": {' in out
        assert out.count('"schema_version"') == len(DELTAS)

    def test_interval_count_and_timeout_reach_the_sampler(self, fake_deltas):
        main(["diff", "4321", "--interval", "2.5", "--count", "7", "--timeout", "0.5"])

        assert fake_deltas["pid"] == 4321
        assert fake_deltas["interval_seconds"] == pytest.approx(2.5)
        assert fake_deltas["count"] == 7
        assert fake_deltas["timeout_seconds"] == pytest.approx(0.5)

    def test_without_a_count_the_sampler_is_left_unbounded(self, fake_deltas):
        main(["diff", "4321", "--interval", "0.01"])

        assert fake_deltas["count"] is None
        assert fake_deltas["timeout_seconds"] == pytest.approx(5.0)

    def test_ctrl_c_keeps_the_deltas_already_printed_and_exits_cleanly(
        self,
        monkeypatch,
        capsys,
    ):
        def interrupted(pid: int, **kwargs: Any) -> Any:
            yield DELTAS[0]
            raise KeyboardInterrupt

        monkeypatch.setattr(commands_module, "iter_snapshot_deltas", interrupted)

        exit_code = main(["diff", "4321", "--interval", "0.01"])

        captured = capsys.readouterr()
        assert exit_code == EXIT_INTERRUPTED
        assert json.loads(captured.out) == DELTAS[0]
        assert captured.err == "pidprobe: interrupted\n"

    def test_a_probe_failing_mid_series_points_at_doctor(self, monkeypatch, capsys):
        def failing(pid: int, **kwargs: Any) -> Any:
            yield DELTAS[0]
            raise ProbeTimeoutError(5.0, pid)

        monkeypatch.setattr(commands_module, "iter_snapshot_deltas", failing)

        exit_code = main(["diff", "4321", "--interval", "0.01"])

        captured = capsys.readouterr()
        assert exit_code == EXIT_TIMEOUT
        # A target that stops answering ends the series; what it already said
        # stays on stdout and the reason goes to stderr.
        assert json.loads(captured.out) == DELTAS[0]
        assert "pidprobe doctor 4321" in captured.err

    def test_diff_is_dispatched_through_its_own_handler(self):
        parser = build_parser()
        diff_args = parser.parse_args(["diff", "4321", "--interval", "1"])

        assert diff_args.handler is not parser.parse_args(["snap", "1"]).handler
        assert diff_args.count is None
        assert diff_args.pretty is False


HEALTHY = Diagnosis(
    pid=4321,
    checks=(Check(name="return_channel", status=CheckStatus.OK, summary="fine"),),
)
BLOCKED = Diagnosis(
    pid=4321,
    checks=(
        Check(
            name="task_for_pid",
            status=CheckStatus.FAIL,
            summary="not root",
            cause="macOS denies task_for_pid",
            confirm="id -u",
            fix="rerun under sudo",
        ),
    ),
)


@pytest.fixture
def fake_diagnosis(monkeypatch):
    """Make ``doctor`` report a fixed diagnosis and record the pid it got."""
    calls: dict[str, Any] = {"result": HEALTHY}

    def fake_diagnose(pid: int | None) -> Diagnosis:
        calls["pid"] = pid
        result: Diagnosis = calls["result"]
        return result

    monkeypatch.setattr(commands_module, "diagnose", fake_diagnose)
    return calls


class TestDoctor:
    def test_a_clean_environment_exits_zero_and_prints_a_report(
        self,
        fake_diagnosis,
        capsys,
    ):
        exit_code = main(["doctor", "4321"])

        out = capsys.readouterr().out
        assert exit_code == EXIT_OK
        assert fake_diagnosis["pid"] == 4321
        assert "return_channel" in out

    def test_a_blocking_check_exits_with_the_diagnosis_code(
        self,
        fake_diagnosis,
        capsys,
    ):
        fake_diagnosis["result"] = BLOCKED

        exit_code = main(["doctor", "4321"])

        out = capsys.readouterr().out
        # Its own code: the diagnosis itself succeeded, and a preflight script
        # wants that apart from "the diagnosis could not be produced".
        assert exit_code == EXIT_DIAGNOSIS_FAILED
        assert "cause: macOS denies task_for_pid" in out
        assert "confirm: id -u" in out
        assert "fix: rerun under sudo" in out

    def test_the_pid_is_optional(self, fake_diagnosis):
        exit_code = main(["doctor"])

        assert exit_code == EXIT_OK
        assert fake_diagnosis["pid"] is None

    def test_json_output_is_a_single_compact_line(self, fake_diagnosis, capsys):
        fake_diagnosis["result"] = BLOCKED

        main(["doctor", "4321", "--json"])

        out = capsys.readouterr().out
        assert out.count("\n") == 1
        assert json.loads(out)["attachable"] is False

    @pytest.mark.usefixtures("fake_diagnosis")
    def test_pretty_json_is_indented(self, capsys):
        main(["doctor", "4321", "--json", "--pretty"])

        out = capsys.readouterr().out
        assert out.count("\n") > 1
        assert json.loads(out)["pid"] == 4321

    def test_doctor_is_dispatched_through_its_own_handler(self):
        parser = build_parser()
        doctor_args = parser.parse_args(["doctor"])

        assert doctor_args.handler is not parser.parse_args(["snap", "1"]).handler
        assert doctor_args.pid is None
        assert doctor_args.as_json is False


def _raise_from_every_probe(monkeypatch: Any, error: BaseException) -> None:
    """Make whichever probe the command under test runs fail with *error*."""

    def failing(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(commands_module, "take_snapshot", failing)
    monkeypatch.setattr(commands_module, "evaluate_in_target", failing)
    monkeypatch.setattr(commands_module, "iter_snapshot_deltas", failing)


PROBING_ARGV = [
    pytest.param(["snap", "4321"], id="snap"),
    pytest.param(["eval", "4321", "1 + 1"], id="eval"),
    pytest.param(["diff", "4321", "--interval", "0.01"], id="diff"),
]


class TestErrorHandling:
    @pytest.mark.usefixtures("failing_snapshot")
    def test_probe_failure_is_explained_on_stderr(self, capsys):
        exit_code = main(["snap", "4321"])

        captured = capsys.readouterr()
        assert exit_code == EXIT_TIMEOUT
        assert captured.out == ""
        assert captured.err.startswith("pidprobe: ")
        assert "pidprobe doctor 4321" in captured.err

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(AttachError(4321, "permission denied"), id="attach"),
            pytest.param(ChannelError("target sent nothing"), id="channel"),
            pytest.param(ProbeTimeoutError(5.0, 4321), id="timeout"),
            pytest.param(
                TargetError(
                    4321, {"type": "TypeError", "message": "nope", "traceback": ""}
                ),
                id="target",
            ),
        ],
    )
    @pytest.mark.parametrize("argv", PROBING_ARGV)
    def test_every_failure_points_at_doctor(self, monkeypatch, capsys, error, argv):
        _raise_from_every_probe(monkeypatch, error)

        main(argv)

        stderr = capsys.readouterr().err
        # Exactly once: the timeout message already carries the hint, and a
        # second copy appended here would read as two different suggestions.
        assert stderr.count("pidprobe doctor 4321") == 1

    def test_a_failure_without_a_pid_cannot_suggest_one(self, monkeypatch, capsys):
        def failing(_pid: int | None) -> Diagnosis:
            message = "no channel"
            raise ChannelError(message)

        monkeypatch.setattr(commands_module, "diagnose", failing)

        exit_code = main(["doctor"])

        stderr = capsys.readouterr().err
        assert exit_code == EXIT_PROBE_ERROR
        assert stderr == "pidprobe: no channel\n"

    def test_a_failing_doctor_does_not_suggest_running_doctor(
        self,
        monkeypatch,
        capsys,
    ):
        def failing(_pid: int | None) -> Diagnosis:
            message = "no channel"
            raise ChannelError(message)

        monkeypatch.setattr(commands_module, "diagnose", failing)

        exit_code = main(["doctor", "4321"])

        stderr = capsys.readouterr().err
        assert exit_code == EXIT_PROBE_ERROR
        assert "pidprobe doctor" not in stderr

    @pytest.mark.parametrize(
        "argv",
        [
            pytest.param(["snap", "0"], id="zero-pid"),
            pytest.param(["snap", "-1"], id="negative-pid"),
            pytest.param(["snap", "abc"], id="non-numeric-pid"),
            pytest.param(["snap", "4321", "--timeout", "0"], id="zero-timeout"),
            pytest.param(
                ["snap", "4321", "--timeout", "nope"], id="non-numeric-timeout"
            ),
            pytest.param(["snap"], id="missing-pid"),
            pytest.param(["eval", "4321"], id="missing-expression"),
            pytest.param(["eval", "0", "1 + 1"], id="eval-zero-pid"),
            pytest.param(["diff", "4321"], id="missing-interval"),
            pytest.param(["diff", "4321", "--interval", "0"], id="zero-interval"),
            # nan compares false against every bound, so an unguarded parser
            # would take it and then never wait between samples.
            pytest.param(["diff", "4321", "--interval", "nan"], id="nan-interval"),
            pytest.param(["diff", "4321", "--interval", "inf"], id="inf-interval"),
            pytest.param(
                ["snap", "4321", "--timeout", "nan"],
                id="nan-timeout",
            ),
            pytest.param(
                ["diff", "4321", "--interval", "1", "--count", "0"],
                id="zero-count",
            ),
            pytest.param([], id="missing-subcommand"),
            pytest.param(["nosuchcommand"], id="unknown-subcommand"),
        ],
    )
    def test_invalid_arguments_are_rejected_by_the_parser(self, argv, capsys):
        with pytest.raises(SystemExit) as info:
            main(argv)

        assert info.value.code == EXIT_USAGE
        assert capsys.readouterr().err


class TestExitCodes:
    """Every failure category a script is meant to be able to tell apart."""

    @pytest.mark.parametrize(
        ("error", "expected_code"),
        [
            pytest.param(
                AttachError.from_cause(4321, ProcessLookupError(3, "gone")),
                EXIT_TARGET_NOT_FOUND,
                id="target-not-found",
            ),
            pytest.param(
                AttachError(4321, "permission denied"),
                EXIT_ATTACH_FAILED,
                id="attach-failed",
            ),
            pytest.param(ProbeTimeoutError(5.0, 4321), EXIT_TIMEOUT, id="timeout"),
            pytest.param(
                TargetError(
                    4321,
                    {"type": "NameError", "message": "no queue", "traceback": ""},
                ),
                EXIT_TARGET_ERROR,
                id="target-error",
            ),
            pytest.param(
                ChannelError("target sent nothing"),
                EXIT_PROBE_ERROR,
                id="unclassified-probe-error",
            ),
        ],
    )
    @pytest.mark.parametrize("argv", PROBING_ARGV)
    def test_each_failure_category_has_its_own_code(
        self,
        monkeypatch,
        capsys,
        error,
        expected_code,
        argv,
    ):
        _raise_from_every_probe(monkeypatch, error)

        exit_code = main(argv)

        assert exit_code == expected_code
        assert capsys.readouterr().err.startswith("pidprobe: ")

    def test_a_vanished_target_is_not_reported_as_a_refused_attach(self, monkeypatch):
        # The two share a message shape and used to share an exit code, which
        # is exactly the distinction a retrying script needs.
        _raise_from_every_probe(monkeypatch, NoSuchProcessError(4321, "gone"))

        assert main(["snap", "4321"]) == EXIT_TARGET_NOT_FOUND

    def test_an_unexpected_error_is_summarised_not_traced(self, monkeypatch, capsys):
        def exploding(*_args: Any, **_kwargs: Any) -> Any:
            message = "cannot diff snapshots of different processes"
            raise ValueError(message)

        monkeypatch.setattr(commands_module, "take_snapshot", exploding)

        exit_code = main(["snap", "4321"])

        stderr = capsys.readouterr().err
        assert exit_code == EXIT_INTERNAL_ERROR
        assert "internal error: ValueError: cannot diff snapshots" in stderr
        assert "Traceback" not in stderr
        assert "--debug" in stderr

    @pytest.mark.parametrize(
        "argv",
        [
            pytest.param(["--debug", "snap", "4321"], id="flag"),
            pytest.param(["snap", "4321"], id="env-var"),
        ],
    )
    def test_debug_lets_the_real_traceback_escape(self, monkeypatch, argv):
        def exploding(*_args: Any, **_kwargs: Any) -> Any:
            message = "boom"
            raise ValueError(message)

        monkeypatch.setattr(commands_module, "take_snapshot", exploding)
        if "--debug" not in argv:
            monkeypatch.setenv("PIDPROBE_DEBUG", "1")

        with pytest.raises(ValueError, match="boom"):
            main(argv)

    def test_debug_env_var_set_to_zero_is_off(self, monkeypatch, capsys):
        def exploding(*_args: Any, **_kwargs: Any) -> Any:
            message = "boom"
            raise ValueError(message)

        monkeypatch.setattr(commands_module, "take_snapshot", exploding)
        monkeypatch.setenv("PIDPROBE_DEBUG", "0")

        assert main(["snap", "4321"]) == EXIT_INTERNAL_ERROR
        assert "internal error" in capsys.readouterr().err

    @pytest.mark.parametrize("argv", PROBING_ARGV)
    def test_ctrl_c_is_not_a_failure(self, monkeypatch, argv):
        _raise_from_every_probe(monkeypatch, KeyboardInterrupt())

        assert main(argv) == EXIT_INTERRUPTED

    def test_a_closed_pipe_ends_quietly(self, monkeypatch, capsys):
        # `pidprobe snap PID | head -1` is ordinary; it must not end in the
        # interpreter's own "Exception ignored" noise.
        _raise_from_every_probe(monkeypatch, BrokenPipeError())

        exit_code = main(["snap", "4321"])

        assert exit_code == EXIT_BROKEN_PIPE
        assert capsys.readouterr().err == ""

    @pytest.mark.usefixtures("fake_diagnosis")
    def test_a_reader_leaving_after_the_output_is_written_ends_quietly(
        self,
        monkeypatch,
        capsys,
    ):
        # doctor writes its report without flushing, so the pipe only breaks
        # when the buffer is emptied -- which the CLI does itself rather than
        # leaving to interpreter shutdown, where it would be unreportable.
        class ClosedPipe(io.StringIO):
            def flush(self) -> None:
                raise BrokenPipeError

        monkeypatch.setattr(sys, "stdout", ClosedPipe())

        exit_code = main(["doctor", "4321"])

        assert exit_code == EXIT_BROKEN_PIPE
        assert capsys.readouterr().err == ""

    @pytest.mark.usefixtures("fake_diagnosis")
    def test_a_broken_stdout_is_redirected_so_shutdown_stays_quiet(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Reporting the broken pipe is only half of it: the descriptor has to
        # stop pointing at the dead reader, or the interpreter's own flush on
        # the way out raises again where nothing can catch it.
        path = tmp_path / "stdout"
        with path.open("w") as handle:

            class ClosedPipe:
                def write(self, text: str) -> int:
                    return handle.write(text)

                def flush(self) -> None:
                    raise BrokenPipeError

                def fileno(self) -> int:
                    return handle.fileno()

            monkeypatch.setattr(sys, "stdout", ClosedPipe())

            exit_code = main(["doctor", "4321"])

            # Now backed by the void, so the final flush cannot fail.
            handle.write("written after the reader left")

        assert exit_code == EXIT_BROKEN_PIPE
        assert capsys.readouterr().err == ""

    def test_every_documented_code_is_unique(self):
        codes = [code for code, _ in EXIT_CODE_TABLE]

        assert len(set(codes)) == len(codes)


class TestGlobalTimeout:
    def test_a_global_timeout_reaches_every_probing_subcommand(self, fake_snapshot):
        main(["--timeout", "2.5", "snap", "4321"])

        assert fake_snapshot["timeout_seconds"] == pytest.approx(2.5)

    def test_the_subcommand_timeout_wins_over_the_global_one(self, fake_snapshot):
        # More specific wins: the option nearer the command it applies to.
        main(["--timeout", "2.5", "snap", "4321", "--timeout", "0.5"])

        assert fake_snapshot["timeout_seconds"] == pytest.approx(0.5)

    def test_without_either_the_built_in_budget_applies(self, fake_snapshot):
        main(["snap", "4321"])

        assert fake_snapshot["timeout_seconds"] == pytest.approx(5.0)

    def test_a_global_timeout_reaches_the_sampler(self, fake_deltas):
        main(["--timeout", "2.5", "diff", "4321", "--interval", "0.01"])

        assert fake_deltas["timeout_seconds"] == pytest.approx(2.5)

    def test_a_global_timeout_reaches_the_evaluation(self, fake_evaluation):
        main(["--timeout", "2.5", "eval", "4321", "1 + 1"])

        assert fake_evaluation["timeout_seconds"] == pytest.approx(2.5)

    @pytest.mark.parametrize(
        "argv",
        [
            pytest.param(["--timeout", "0", "snap", "4321"], id="zero"),
            pytest.param(["--timeout", "nan", "snap", "4321"], id="nan"),
            pytest.param(["--timeout", "inf", "snap", "4321"], id="inf"),
            pytest.param(["--timeout", "nope", "snap", "4321"], id="non-numeric"),
        ],
    )
    def test_an_invalid_global_timeout_is_rejected(self, argv):
        with pytest.raises(SystemExit) as info:
            main(argv)

        assert info.value.code == EXIT_USAGE

    @pytest.mark.usefixtures("fake_diagnosis")
    def test_doctor_accepts_a_global_timeout_without_using_it(self):
        # doctor never attaches, so there is nothing to budget; accepting the
        # option keeps `pidprobe --timeout N <anything>` from failing.
        assert main(["--timeout", "2.5", "doctor", "4321"]) == EXIT_OK


class TestHelp:
    def _help(self, capsys, argv):
        with pytest.raises(SystemExit):
            main([*argv, "--help"])
        return capsys.readouterr().out

    @pytest.mark.parametrize("command", ["snap", "eval", "diff", "doctor"])
    def test_top_level_help_lists_every_subcommand(self, capsys, command):
        assert command in self._help(capsys, [])

    @pytest.mark.parametrize("option", ["--version", "--timeout", "--debug", "--help"])
    def test_top_level_help_documents_every_global_option(self, capsys, option):
        assert option in self._help(capsys, [])

    @pytest.mark.parametrize(("code", "meaning"), EXIT_CODE_TABLE)
    def test_top_level_help_documents_every_exit_code(self, capsys, code, meaning):
        out = self._help(capsys, [])

        assert f"{code}" in out
        assert meaning in out

    def test_snap_help_names_the_plugin_mechanism(self, capsys):
        # A plugin silently adding a section to every snapshot is exactly the
        # kind of thing --help has to say out loud.
        assert "plugin" in self._help(capsys, ["snap"])

    @pytest.mark.parametrize(
        ("command", "arguments"),
        [
            pytest.param("snap", ["pid", "--pretty", "--timeout", "--no-mask"]),
            pytest.param(
                "eval",
                ["pid", "EXPR", "--pretty", "--timeout", "--no-mask"],
            ),
            pytest.param(
                "diff",
                ["pid", "--interval", "--count", "--pretty", "--timeout"],
            ),
            pytest.param("doctor", ["pid", "--json", "--pretty"]),
        ],
    )
    def test_every_subcommand_documents_every_argument_it_takes(
        self,
        capsys,
        command,
        arguments,
    ):
        out = self._help(capsys, [command])

        assert [argument for argument in arguments if argument not in out] == []


class TestEntryPoint:
    def test_module_entry_point_runs_the_cli(self):
        completed = subprocess.run(
            [sys.executable, "-m", "pidprobe", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == f"pidprobe {__version__}"


class TestParser:
    def test_version_flag_reports_the_package_version(self, capsys):
        with pytest.raises(SystemExit) as info:
            main(["--version"])

        assert info.value.code == 0
        assert capsys.readouterr().out.strip() == f"pidprobe {__version__}"

    def test_snap_is_dispatched_through_a_handler(self):
        args = build_parser().parse_args(["snap", "4321"])

        assert callable(args.handler)
        assert args.pid == 4321
        assert args.pretty is False

    def test_eval_is_dispatched_through_its_own_handler(self):
        parser = build_parser()
        snap_args = parser.parse_args(["snap", "4321"])
        eval_args = parser.parse_args(["eval", "4321", "len(cache)"])

        assert callable(eval_args.handler)
        assert eval_args.handler is not snap_args.handler
        assert eval_args.expression == "len(cache)"
        assert eval_args.is_masked is True
