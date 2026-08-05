"""Integration tests that diff repeated snapshots of a real leaking target.

These attach to a live child process with ``sys.remote_exec``, so they need
CPython 3.14+ and, on macOS, root -- the same skip rules as the other
integration tests. The ``leak`` target appends a fresh ``LeakedObject`` to a
module-level list every 10 ms, which is the pattern ``diff`` exists to
surface: invisible in any single snapshot, obvious in the delta between two.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import pytest

from pidprobe import iter_snapshot_deltas
from pidprobe.cli import EXIT_OK, main

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable

pytestmark = pytest.mark.integration

LEAKED_TYPE = "__main__.LeakedObject"
"""How the target's leaking class is labelled, it being run as a script."""

TOP_RANKS = 5
"""How far down the ranking the leaking type still counts as "at the top".

The leak normally ranks first, but ``dict`` and ``tuple`` also move by a few
dozen per sample because the probe compiles and runs a script inside the
target. Allowing a few rows keeps a slow CI machine from failing the test
over that fixed overhead rather than over the ranking being wrong.
"""

INTERVAL_SECONDS = 1.0
"""Long enough that ~100 leaked objects outweigh what one probe itself costs.

Every probe compiles and runs a script inside the target, which moves the
built-in counters by a few dozen no matter how short the interval is. That
noise is per sample rather than per second, so the leak only stands out once
the interval is long enough to accumulate more than it.
"""

PROBE_BUDGET_SECONDS = 3.0
"""Slack for the probes themselves on top of the intervals they are paced by."""


def test_the_leaking_type_tops_the_object_count_ranking(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
) -> None:
    proc = spawn_target("leak")

    deltas = list(
        iter_snapshot_deltas(proc.pid, interval_seconds=INTERVAL_SECONDS, count=3),
    )

    # Three snapshots, two deltas: a delta needs a pair.
    assert len(deltas) == 2
    for delta in deltas:
        ranking = delta["objects"]["types"][:TOP_RANKS]
        leaked = [entry for entry in ranking if entry["type"] == LEAKED_TYPE]
        assert leaked, ranking
        assert leaked[0]["delta"] > 0
        assert delta["objects"]["total_tracked"]["delta"] > 0


def test_the_interval_paces_the_samples(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
) -> None:
    proc = spawn_target("leak")

    started = time.perf_counter()
    deltas = list(
        iter_snapshot_deltas(proc.pid, interval_seconds=INTERVAL_SECONDS, count=3),
    )
    elapsed_seconds = time.perf_counter() - started

    # Three samples one interval apart cannot finish sooner than two
    # intervals, so a command that ignored --interval would fail here.
    paced_seconds = 2 * INTERVAL_SECONDS
    assert paced_seconds <= elapsed_seconds < paced_seconds + PROBE_BUDGET_SECONDS
    # Measured between the two capture timestamps, so a probe that took its
    # time shows up here rather than being assumed away.
    assert deltas[0]["meta"]["interval_ms"] > 0
    assert deltas[0]["meta"]["pid"] == proc.pid


def test_a_delta_reports_only_the_countable_sections(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
) -> None:
    proc = spawn_target("leak")

    delta = next(iter_snapshot_deltas(proc.pid, interval_seconds=0.05, count=2))

    # No stacks and no plugin sections: a delta is defined for counters only.
    assert set(delta) == {"schema_version", "meta", "objects", "gc", "fds"}
    assert delta["fds"]["count"]["delta"] == 0
    # Every generation the target reports is diffed, however many it has.
    generations = delta["gc"]["generations"]
    assert generations
    assert all("collections" in row for row in generations)


def test_cli_prints_one_json_line_per_delta(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    proc = spawn_target("leak")

    exit_code = main(
        ["diff", str(proc.pid), "--interval", str(INTERVAL_SECONDS), "--count", "3"],
    )

    out = capsys.readouterr().out
    assert exit_code == EXIT_OK
    printed: list[dict[str, Any]] = [json.loads(line) for line in out.splitlines()]
    assert len(printed) == 2
    assert all(delta["meta"]["pid"] == proc.pid for delta in printed)
    assert all(
        LEAKED_TYPE
        in [entry["type"] for entry in delta["objects"]["types"][:TOP_RANKS]]
        for delta in printed
    )
