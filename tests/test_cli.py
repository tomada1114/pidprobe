"""Tests for the pidprobe command-line interface."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import pytest

from pidprobe import __version__
from pidprobe import cli as cli_module
from pidprobe._errors import ProbeTimeoutError
from pidprobe.cli import EXIT_OK, EXIT_PROBE_ERROR, build_parser, main
from pidprobe.collectors import BUILTIN_COLLECTORS, STACKS_COLLECTOR
from pidprobe.collectors._stacks import build_stacks_collector

SNAPSHOT = {"schema_version": "1.0", "meta": {"pid": 4321}, "gc": {"enabled": True}}


@pytest.fixture
def fake_snapshot(monkeypatch):
    """Make ``snap`` return a fixed document and record its arguments."""
    calls: dict[str, Any] = {}

    def fake_take_snapshot(pid: int, **kwargs: Any) -> dict[str, Any]:
        calls.update(pid=pid, **kwargs)
        return SNAPSHOT

    monkeypatch.setattr(cli_module, "take_snapshot", fake_take_snapshot)
    return calls


@pytest.fixture
def failing_snapshot(monkeypatch):
    """Make ``snap`` fail the way an unreachable target does."""

    def fake_take_snapshot(pid: int, **kwargs: Any) -> dict[str, Any]:
        raise ProbeTimeoutError(5.0, pid)

    monkeypatch.setattr(cli_module, "take_snapshot", fake_take_snapshot)


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
        assert [collector.name for collector in collectors] == [
            collector.name for collector in BUILTIN_COLLECTORS
        ]
        assert STACKS_COLLECTOR not in collectors
        by_name = {collector.name: collector for collector in collectors}
        assert by_name["stacks"] == build_stacks_collector(is_masked=False)

    def test_parser_defaults_to_masking(self):
        args = build_parser().parse_args(["snap", "4321"])

        assert args.is_masked is True


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
