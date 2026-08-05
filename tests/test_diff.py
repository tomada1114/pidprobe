"""Tests for snapshot deltas and repeated sampling."""

from __future__ import annotations

import os
from itertools import pairwise
from typing import Any

import pytest

from pidprobe import (
    ChannelError,
    diff_snapshots,
    iter_snapshot_deltas,
    take_snapshot,
)
from pidprobe import _diff as diff_module
from pidprobe._diff import DIFF_COLLECTORS

PID = 4321
FIRST_CAPTURE = "2026-08-05T12:00:00.000000Z"
SECOND_CAPTURE = "2026-08-05T12:00:02.500000Z"
PROBE_SECONDS = 2.0
"""What one faked probe costs on the fake clock, so waits can be checked."""


def make_snapshot(
    counts: dict[str, int],
    *,
    pid: int = PID,
    captured_at: str = FIRST_CAPTURE,
    fd_count: int = 5,
) -> dict[str, Any]:
    """Build a snapshot shaped exactly like the objects, gc and fds sections."""
    return {
        "schema_version": "1.0",
        "meta": {"pid": pid, "captured_at": captured_at},
        "objects": {
            "top_n": 50,
            "total_tracked": sum(counts.values()),
            "distinct_types": len(counts),
            "top": [{"type": name, "count": n} for name, n in counts.items()],
        },
        "gc": {
            "enabled": True,
            "generations": [
                {
                    "generation": 0,
                    "collections": 1,
                    "collected": 3,
                    "uncollectable": 0,
                    "count": 7,
                    "threshold": 700,
                },
            ],
            "garbage_count": 0,
            "freeze_count": 0,
        },
        "fds": {"supported": True, "count": fd_count, "descriptors": []},
    }


def types_of(delta: dict[str, Any]) -> list[str]:
    """Return the ranked type names of an objects delta."""
    return [entry["type"] for entry in delta["objects"]["types"]]


def _gaps(moments: list[float]) -> list[float]:
    """Return the spacing between consecutive moments on the fake clock."""
    return [round(later - earlier, 6) for earlier, later in pairwise(moments)]


class TestObjectDeltas:
    def test_types_are_ranked_by_growth_with_the_fastest_grower_first(self):
        before = make_snapshot({"Leak": 100, "Shrinking": 40, "dict": 8})
        after = make_snapshot(
            {"Leak": 900, "Shrinking": 10, "dict": 9},
            captured_at=SECOND_CAPTURE,
        )

        delta = diff_snapshots(before, after)

        assert types_of(delta) == ["Leak", "dict", "Shrinking"]
        assert delta["objects"]["types"][0] == {
            "type": "Leak",
            "before": 100,
            "after": 900,
            "delta": 800,
        }
        assert delta["objects"]["types"][-1]["delta"] == -30

    def test_types_whose_count_did_not_move_are_left_out(self):
        before = make_snapshot({"Leak": 100, "steady.Type": 42})
        after = make_snapshot({"Leak": 101, "steady.Type": 42})

        delta = diff_snapshots(before, after)

        assert types_of(delta) == ["Leak"]

    def test_types_sharing_a_delta_are_ordered_by_name(self):
        before = make_snapshot({"beta": 1, "alpha": 1})
        after = make_snapshot({"beta": 3, "alpha": 3})

        assert types_of(diff_snapshots(before, after)) == ["alpha", "beta"]

    def test_a_type_outside_the_other_ranking_is_reported_as_unknown(self):
        before = make_snapshot({"known": 5})
        after = make_snapshot({"known": 5, "newcomer": 70})

        entries = diff_snapshots(before, after)["objects"]["types"]

        # None is not zero: the type may simply have been below the top_n cut
        # in the earlier snapshot, so the delta is a bound, not a measurement.
        assert entries == [
            {"type": "newcomer", "before": None, "after": 70, "delta": 70},
        ]

    def test_totals_report_both_sides_and_the_difference(self):
        before = make_snapshot({"a": 10, "b": 5})
        after = make_snapshot({"a": 30, "b": 5, "c": 1})

        objects = diff_snapshots(before, after)["objects"]

        assert objects["total_tracked"] == {"before": 15, "after": 36, "delta": 21}
        assert objects["distinct_types"] == {"before": 2, "after": 3, "delta": 1}
        assert objects["top_n"] == 50

    def test_malformed_rows_are_ignored_rather_than_failing_the_delta(self):
        before = make_snapshot({"a": 1})
        after = make_snapshot({"a": 4})
        after["objects"]["top"] += [{"type": "b"}, {"count": 3}, "junk", {"type": 1}]

        assert types_of(diff_snapshots(before, after)) == ["a"]

    def test_a_missing_counter_leaves_that_total_unknown(self):
        before = make_snapshot({"a": 1})
        after = make_snapshot({"a": 4})
        del after["objects"]["total_tracked"]
        before["objects"]["distinct_types"] = "many"

        objects = diff_snapshots(before, after)["objects"]

        assert objects["total_tracked"] is None
        assert objects["distinct_types"] is None


class TestGcAndFdDeltas:
    def test_every_generation_statistic_is_diffed(self):
        before = make_snapshot({"a": 1})
        after = make_snapshot({"a": 1})
        after["gc"]["generations"][0]["collections"] = 9

        generations = diff_snapshots(before, after)["gc"]["generations"]

        assert generations == [
            {
                "generation": 0,
                "collections": {"before": 1, "after": 9, "delta": 8},
                "collected": {"before": 3, "after": 3, "delta": 0},
                "uncollectable": {"before": 0, "after": 0, "delta": 0},
                "count": {"before": 7, "after": 7, "delta": 0},
            },
        ]

    def test_the_threshold_is_configuration_and_is_not_diffed(self):
        before = make_snapshot({"a": 1})
        after = make_snapshot({"a": 1})

        assert "threshold" not in diff_snapshots(before, after)["gc"]["generations"][0]

    def test_generations_are_paired_by_index_not_by_position(self):
        before = make_snapshot({"a": 1})
        before["gc"]["generations"] = [
            {"generation": 1, "collections": 4},
            {"generation": 0, "collections": 1},
        ]
        after = make_snapshot({"a": 1})
        after["gc"]["generations"] = [{"generation": 1, "collections": 6}]

        generations = diff_snapshots(before, after)["gc"]["generations"]

        assert generations == [
            {"generation": 1, "collections": {"before": 4, "after": 6, "delta": 2}},
        ]

    def test_a_generation_only_one_snapshot_knows_is_skipped(self):
        before = make_snapshot({"a": 1})
        after = make_snapshot({"a": 1})
        after["gc"]["generations"] += [{"generation": 2, "collections": 1}, "junk"]

        generations = diff_snapshots(before, after)["gc"]["generations"]

        assert [row["generation"] for row in generations] == [0]

    def test_malformed_generation_rows_and_statistics_are_ignored(self):
        before = make_snapshot({"a": 1})
        before["gc"]["generations"] = [
            "junk",
            {"collections": 2},
            {"generation": 0, "collections": 1, "collected": 3},
        ]
        after = make_snapshot({"a": 1})
        after["gc"]["generations"] = [
            {"generation": 0, "collections": 5, "collected": "many"},
        ]

        generations = diff_snapshots(before, after)["gc"]["generations"]

        # A row with no usable index and a statistic that is not a number
        # both drop out, rather than costing the whole delta.
        assert generations == [
            {"generation": 0, "collections": {"before": 1, "after": 5, "delta": 4}},
        ]

    def test_garbage_and_freeze_counts_are_diffed(self):
        before = make_snapshot({"a": 1})
        after = make_snapshot({"a": 1})
        after["gc"]["garbage_count"] = 12

        gc_delta = diff_snapshots(before, after)["gc"]

        assert gc_delta["garbage_count"] == {"before": 0, "after": 12, "delta": 12}
        assert gc_delta["freeze_count"] == {"before": 0, "after": 0, "delta": 0}

    def test_the_fd_count_delta_is_reported(self):
        before = make_snapshot({"a": 1}, fd_count=5)
        after = make_snapshot({"a": 1}, fd_count=41)

        assert diff_snapshots(before, after)["fds"] == {
            "count": {"before": 5, "after": 41, "delta": 36},
        }


class TestDeltaDocument:
    def test_meta_carries_the_pid_and_both_capture_times(self):
        before = make_snapshot({"a": 1})
        after = make_snapshot({"a": 1}, captured_at=SECOND_CAPTURE)

        meta = diff_snapshots(before, after)["meta"]

        assert meta["pid"] == PID
        assert meta["from"] == FIRST_CAPTURE
        assert meta["to"] == SECOND_CAPTURE
        # Measured from the timestamps, not from the interval that was asked for.
        assert meta["interval_ms"] == pytest.approx(2500.0)

    def test_the_document_is_versioned_like_a_snapshot(self):
        delta = diff_snapshots(make_snapshot({"a": 1}), make_snapshot({"a": 1}))

        assert delta["schema_version"] == "1.0"

    @pytest.mark.parametrize(
        "captured_at",
        [
            pytest.param("not a timestamp", id="unparsable"),
            pytest.param(None, id="missing"),
        ],
    )
    def test_an_unusable_timestamp_leaves_the_interval_unknown(self, captured_at):
        before = make_snapshot({"a": 1})
        after = make_snapshot({"a": 1})
        after["meta"]["captured_at"] = captured_at

        meta = diff_snapshots(before, after)["meta"]

        assert meta["interval_ms"] is None
        assert meta["to"] == captured_at

    @pytest.mark.parametrize("section", ["objects", "gc", "fds"])
    def test_a_section_a_failed_collector_left_behind_becomes_none(self, section):
        before = make_snapshot({"a": 1})
        after = make_snapshot({"a": 1})
        after[section] = None

        assert diff_snapshots(before, after)[section] is None

    def test_a_snapshot_without_meta_still_diffs(self):
        before = make_snapshot({"a": 1})
        after = make_snapshot({"a": 4})
        del before["meta"]
        del after["meta"]

        delta = diff_snapshots(before, after)

        assert delta["meta"] == {
            "pid": None,
            "from": None,
            "to": None,
            "interval_ms": None,
        }
        assert types_of(delta) == ["a"]

    def test_diffing_snapshots_of_different_processes_is_refused(self):
        before = make_snapshot({"a": 1}, pid=1)
        after = make_snapshot({"a": 1}, pid=2)

        with pytest.raises(ValueError, match=r"different processes: 1 and 2"):
            diff_snapshots(before, after)


@pytest.fixture
def fake_sampling(monkeypatch):
    """Sample a canned, leaking target on a fake clock.

    Both the clock and the sleep are faked, so the cadence can be asserted
    exactly and the tests never spend the intervals they ask for.
    """
    recorded: dict[str, Any] = {
        "probes": [],
        "starts": [],
        "waits": [],
        "leaked": 0,
        "now": 0.0,
    }

    def fake_take_snapshot(pid: int, **kwargs: Any) -> dict[str, Any]:
        recorded["probes"].append({"pid": pid, **kwargs})
        recorded["starts"].append(recorded["now"])
        recorded["leaked"] += 10
        recorded["now"] += PROBE_SECONDS
        return make_snapshot({"Leak": recorded["leaked"], "dict": 3}, pid=pid)

    def fake_sleep(seconds: float) -> None:
        recorded["waits"].append(seconds)
        recorded["now"] += seconds

    monkeypatch.setattr(diff_module, "take_snapshot", fake_take_snapshot)
    monkeypatch.setattr(diff_module, "sleep", fake_sleep)
    monkeypatch.setattr(diff_module, "monotonic", lambda: recorded["now"])
    return recorded


class TestIterSnapshotDeltas:
    def test_n_snapshots_yield_n_minus_one_deltas(self, fake_sampling):
        deltas = list(iter_snapshot_deltas(PID, interval_seconds=0.01, count=4))

        assert len(fake_sampling["probes"]) == 4
        assert len(deltas) == 3
        assert [types_of(delta) for delta in deltas] == [["Leak"]] * 3

    def test_a_single_snapshot_has_nothing_to_compare_with(self, fake_sampling):
        deltas = list(iter_snapshot_deltas(PID, interval_seconds=0.01, count=1))

        assert deltas == []
        assert len(fake_sampling["probes"]) == 1
        assert fake_sampling["waits"] == []

    @pytest.mark.parametrize("count", [0, -1])
    def test_asking_for_no_snapshots_never_touches_the_target(
        self,
        fake_sampling,
        count,
    ):
        deltas = list(iter_snapshot_deltas(PID, interval_seconds=0.01, count=count))

        # Stopping a live process is a side effect; nobody asked for one.
        assert deltas == []
        assert fake_sampling["probes"] == []

    @pytest.mark.usefixtures("fake_sampling")
    def test_consecutive_snapshots_are_compared_pairwise(self):
        deltas = list(iter_snapshot_deltas(PID, interval_seconds=0.01, count=3))

        # 10 -> 20 -> 30: every delta is one step, never measured from the start.
        assert [delta["objects"]["types"][0]["delta"] for delta in deltas] == [10, 10]
        assert deltas[1]["objects"]["types"][0]["before"] == 20

    def test_only_the_counting_collectors_run(self, fake_sampling):
        list(iter_snapshot_deltas(PID, interval_seconds=0.01, count=2))

        for probe in fake_sampling["probes"]:
            assert probe["collectors"] == DIFF_COLLECTORS
        assert [collector.name for collector in DIFF_COLLECTORS] == [
            "objects",
            "gc",
            "fds",
        ]

    def test_the_timeout_applies_to_every_probe(self, fake_sampling):
        list(
            iter_snapshot_deltas(PID, interval_seconds=0.01, count=3, timeout_seconds=2)
        )

        assert [probe["timeout_seconds"] for probe in fake_sampling["probes"]] == [
            2
        ] * 3

    def test_samples_start_one_interval_apart_whatever_a_probe_costs(
        self,
        fake_sampling,
    ):
        list(iter_snapshot_deltas(PID, interval_seconds=30.0, count=3))

        # Start to start, not end to start: each 2s probe comes out of the
        # following wait instead of pushing the rest of the series later.
        assert _gaps(fake_sampling["starts"]) == [30.0, 30.0]

    def test_a_probe_slower_than_the_interval_samples_without_waiting(
        self,
        fake_sampling,
    ):
        list(iter_snapshot_deltas(PID, interval_seconds=0.5, count=3))

        # A deadline that has already passed must not sleep a negative time;
        # the series then runs as fast as the probes allow.
        assert fake_sampling["waits"] == []
        assert _gaps(fake_sampling["starts"]) == [PROBE_SECONDS, PROBE_SECONDS]

    def test_without_a_count_it_samples_until_it_is_interrupted(
        self,
        fake_sampling,
        monkeypatch,
    ):
        def interrupt_after_one_wait(seconds: float) -> None:
            fake_sampling["waits"].append(seconds)
            fake_sampling["now"] += seconds
            if len(fake_sampling["waits"]) > 1:
                raise KeyboardInterrupt

        monkeypatch.setattr(diff_module, "sleep", interrupt_after_one_wait)
        deltas = iter_snapshot_deltas(PID, interval_seconds=30.0)

        # An unbounded series keeps producing until something stops it, and a
        # Ctrl-C reaches the caller rather than the generator swallowing it.
        assert next(deltas)["objects"]["types"][0]["delta"] == 10
        with pytest.raises(KeyboardInterrupt):
            next(deltas)

    def test_a_failing_probe_ends_the_series(self, monkeypatch):
        probes: list[int] = []

        def failing(pid: int, **kwargs: Any) -> dict[str, Any]:
            probes.append(pid)
            message = "target went away"
            raise ChannelError(message)

        monkeypatch.setattr(diff_module, "take_snapshot", failing)

        with pytest.raises(ChannelError, match=r"target went away"):
            list(iter_snapshot_deltas(PID, interval_seconds=0.01, count=3))

        assert probes == [PID]


class LeakedByTheTest:
    """Instances of this type are what the ranking is expected to surface."""


@pytest.mark.usefixtures("local_target")
def test_a_real_leak_tops_the_ranking_of_real_collector_output():
    before = take_snapshot(os.getpid(), collectors=DIFF_COLLECTORS)
    hoard = [LeakedByTheTest() for _ in range(500)]
    after = take_snapshot(os.getpid(), collectors=DIFF_COLLECTORS)

    delta = diff_snapshots(before, after)

    top = delta["objects"]["types"][0]
    assert top["type"].endswith("LeakedByTheTest"), delta["objects"]["types"][:5]
    assert top["delta"] >= len(hoard)
    assert delta["fds"]["count"]["delta"] == 0
