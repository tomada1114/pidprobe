"""Tests for the pidprobe command-line interface."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import pytest

from pidprobe import __version__, available_collectors
from pidprobe import _commands as commands_module
from pidprobe._diagnosis import Check, CheckStatus, Diagnosis
from pidprobe._errors import AttachError, ChannelError, ProbeTimeoutError
from pidprobe.cli import (
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_PROBE_ERROR,
    build_parser,
    main,
)
from pidprobe.collectors import STACKS_COLLECTOR
from pidprobe.collectors._stacks import build_stacks_collector

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
        assert exit_code == EXIT_PROBE_ERROR
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
        assert exit_code == EXIT_PROBE_ERROR
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

    def test_a_blocking_check_exits_with_the_probe_error_code(
        self,
        fake_diagnosis,
        capsys,
    ):
        fake_diagnosis["result"] = BLOCKED

        exit_code = main(["doctor", "4321"])

        out = capsys.readouterr().out
        assert exit_code == EXIT_PROBE_ERROR
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


class TestErrorHandling:
    @pytest.mark.usefixtures("failing_snapshot")
    def test_probe_failure_is_explained_on_stderr(self, capsys):
        exit_code = main(["snap", "4321"])

        captured = capsys.readouterr()
        assert exit_code == EXIT_PROBE_ERROR
        assert captured.out == ""
        assert captured.err.startswith("pidprobe: ")
        assert "pidprobe doctor 4321" in captured.err

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(AttachError(4321, "permission denied"), id="attach"),
            pytest.param(ChannelError("target sent nothing"), id="channel"),
            pytest.param(ProbeTimeoutError(5.0, 4321), id="timeout"),
        ],
    )
    @pytest.mark.parametrize(
        "argv",
        [
            pytest.param(["snap", "4321"], id="snap"),
            pytest.param(["eval", "4321", "1 + 1"], id="eval"),
        ],
    )
    def test_every_failure_points_at_doctor(self, monkeypatch, capsys, error, argv):
        def failing(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise error

        monkeypatch.setattr(commands_module, "take_snapshot", failing)
        monkeypatch.setattr(commands_module, "evaluate_in_target", failing)

        exit_code = main(argv)

        stderr = capsys.readouterr().err
        assert exit_code == EXIT_PROBE_ERROR
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

        assert info.value.code == 2
        assert capsys.readouterr().err


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
