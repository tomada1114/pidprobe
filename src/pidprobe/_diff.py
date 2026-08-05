"""Deltas between repeated snapshots of one live process.

A delta answers a different question from a snapshot: not "what does this
process hold" but "what is growing". A leak reads as the same type gaining
objects sample after sample, which no single snapshot shows and which is
tedious to spot by eyeballing two full ones.

Only the three counting collectors run -- ``objects``, ``gc`` and ``fds`` --
because those are the sections a delta is defined for. Sampling therefore
stops the target for less time than a snapshot would, which is what makes
repeating it affordable, and no plugin section can turn up in a delta.

The reported interval comes from the two capture timestamps rather than from
the requested spacing, so it measures what happened instead of what was asked
for.
"""

from __future__ import annotations

from datetime import datetime
from time import monotonic, sleep
from typing import TYPE_CHECKING, Any

from ._channel import DEFAULT_TIMEOUT_SECONDS
from ._schema import SCHEMA_VERSION
from ._snapshot import take_snapshot
from .collectors import FDS_COLLECTOR, GC_COLLECTOR, OBJECTS_COLLECTOR

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from .collectors import Collector

DIFF_COLLECTORS: tuple[Collector, ...] = (
    OBJECTS_COLLECTOR,
    GC_COLLECTOR,
    FDS_COLLECTOR,
)
"""The only collectors a delta is defined for, in output order."""

# Any: a delta mirrors the collector-defined sections it is computed from.
SnapshotDelta = dict[str, Any]

_MILLISECONDS = 1000.0
_DURATION_DIGITS = 3

# The generation index labels the row and the threshold is configuration, so
# neither is a statistic that can meaningfully move between two samples.
_GC_UNCHANGING_KEYS = frozenset({"generation", "threshold"})


def diff_snapshots(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> SnapshotDelta:
    """Report what changed between two snapshots of the same process.

    Args:
        before: The earlier snapshot.
        after: A later snapshot of the same process.

    Returns:
        The delta document: ``meta`` plus an ``objects``, ``gc`` and ``fds``
        section. A section is ``None`` when either snapshot lacks it, which is
        what a collector that failed in the target looks like.

    Raises:
        ValueError: If the snapshots describe different processes, which would
            make every number in the delta meaningless.
    """
    before_meta = _meta_of(before)
    after_meta = _meta_of(after)
    pid = after_meta.get("pid")
    if before_meta.get("pid") != pid:
        message = (
            "cannot diff snapshots of different processes: "
            f"{before_meta.get('pid')!r} and {pid!r}"
        )
        raise ValueError(message)
    captured_from = before_meta.get("captured_at")
    captured_to = after_meta.get("captured_at")
    return {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "pid": pid,
            "from": captured_from,
            "to": captured_to,
            "interval_ms": _interval_ms(captured_from, captured_to),
        },
        "objects": _objects_delta(before.get("objects"), after.get("objects")),
        "gc": _gc_delta(before.get("gc"), after.get("gc")),
        "fds": _fds_delta(before.get("fds"), after.get("fds")),
    }


def iter_snapshot_deltas(
    pid: int,
    *,
    interval_seconds: float,
    count: int | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Iterator[SnapshotDelta]:
    """Sample a running process repeatedly, yielding each delta as it is ready.

    Args:
        pid: Target process id.
        interval_seconds: Spacing between the *starts* of two samples. The
            time a sample itself costs is subtracted from the wait, so a slow
            probe does not push the whole series later and later.
        count: How many snapshots to take. ``N`` snapshots produce ``N - 1``
            deltas, so ``count=1`` yields nothing at all and ``count=0`` does
            not touch the target. ``None`` keeps sampling until the caller
            stops consuming the iterator.
        timeout_seconds: Hard budget for each individual probe.

    Yields:
        One delta per pair of consecutive snapshots, as soon as the later
        snapshot of that pair has been taken.

    Raises:
        ProbeError: Whatever :func:`~pidprobe.take_snapshot` raises for a
            sample. A failed sample ends the series instead of being skipped:
            a target that stopped answering will not start again on its own,
            and silently widening the interval would misreport every delta
            that followed.
    """
    started = monotonic()
    previous: Mapping[str, Any] | None = None
    taken = 0
    while count is None or taken < count:
        # Scheduled from one origin rather than from the previous sample, so
        # the probes themselves cannot make the series drift.
        _wait_until(started + taken * interval_seconds)
        current = _sample(pid, timeout_seconds)
        taken += 1
        if previous is not None:
            yield diff_snapshots(previous, current)
        previous = current


def _sample(pid: int, timeout_seconds: float) -> Mapping[str, Any]:
    """Take one snapshot restricted to the sections a delta is defined for."""
    return take_snapshot(
        pid,
        timeout_seconds=timeout_seconds,
        collectors=DIFF_COLLECTORS,
    )


def _wait_until(deadline: float) -> None:
    """Sleep until a monotonic deadline, returning at once if it has passed."""
    remaining = deadline - monotonic()
    if remaining > 0:
        sleep(remaining)


def _meta_of(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a snapshot's ``meta`` section, or an empty one."""
    meta = snapshot.get("meta")
    return meta if isinstance(meta, dict) else {}


def _interval_ms(captured_from: object, captured_to: object) -> float | None:
    """Return the wall-clock milliseconds between two capture timestamps."""
    if not isinstance(captured_from, str) or not isinstance(captured_to, str):
        return None
    try:
        started = datetime.fromisoformat(captured_from)
        ended = datetime.fromisoformat(captured_to)
    except ValueError:
        return None
    elapsed = (ended - started).total_seconds() * _MILLISECONDS
    return round(elapsed, _DURATION_DIGITS)


def _objects_delta(before: object, after: object) -> dict[str, Any] | None:
    """Diff the ``objects`` section: the totals and the per-type ranking."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    was, now = before.get, after.get
    return {
        "top_n": _as_int(now("top_n")),
        "total_tracked": _change(was("total_tracked"), now("total_tracked")),
        "distinct_types": _change(was("distinct_types"), now("distinct_types")),
        "types": _type_deltas(_counts_by_type(before), _counts_by_type(after)),
    }


def _counts_by_type(section: Mapping[str, Any]) -> dict[str, int]:
    """Read one snapshot's per-type counts, ignoring rows that are malformed."""
    counts: dict[str, int] = {}
    for entry in section.get("top") or ():
        if not isinstance(entry, dict):
            continue
        label = entry.get("type")
        count = _as_int(entry.get("count"))
        if isinstance(label, str) and count is not None:
            counts[label] = count
    return counts


def _type_deltas(
    before: Mapping[str, int],
    after: Mapping[str, int],
) -> list[dict[str, Any]]:
    """Rank the types whose count moved, largest growth first.

    Types that did not move are left out; a stable process holds hundreds of
    them. A type missing from one side was outside that snapshot's ``top_n``
    ranking, which is not the same as having no instances: that side is
    reported as ``None`` and counted as zero, so the delta bounds the real
    change rather than stating it.
    """
    ranked: list[dict[str, Any]] = []
    for label in before.keys() | after.keys():
        was = before.get(label)
        now = after.get(label)
        delta = (now or 0) - (was or 0)
        if delta:
            ranked.append(
                {"type": label, "before": was, "after": now, "delta": delta},
            )
    ranked.sort(key=lambda entry: (-entry["delta"], entry["type"]))
    return ranked


def _gc_delta(before: object, after: object) -> dict[str, Any] | None:
    """Diff the ``gc`` section: per-generation statistics and the totals."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    was, now = before.get, after.get
    return {
        "generations": _generation_deltas(was("generations"), now("generations")),
        "garbage_count": _change(was("garbage_count"), now("garbage_count")),
        "freeze_count": _change(was("freeze_count"), now("freeze_count")),
    }


def _generation_deltas(before: object, after: object) -> list[dict[str, Any]]:
    """Diff every statistic of every generation both snapshots reported.

    Generations are paired by their reported index rather than by position,
    because the free-threaded build does not use the same generation layout
    and a build change between two samples must not shift the rows.
    """
    previous_by_index = _generations_by_index(before)
    rows: list[dict[str, Any]] = []
    for entry in after if isinstance(after, list) else ():
        if not isinstance(entry, dict):
            continue
        index = _as_int(entry.get("generation"))
        previous = previous_by_index.get(index) if index is not None else None
        if previous is None:
            continue
        row: dict[str, Any] = {"generation": index}
        for key, value in entry.items():
            if key in _GC_UNCHANGING_KEYS:
                continue
            change = _change(previous.get(key), value)
            if change is not None:
                row[str(key)] = change
        rows.append(row)
    return rows


def _generations_by_index(section: object) -> dict[int, Mapping[str, Any]]:
    """Index the generation rows of one snapshot by their generation number."""
    indexed: dict[int, Mapping[str, Any]] = {}
    for entry in section if isinstance(section, list) else ():
        if not isinstance(entry, dict):
            continue
        index = _as_int(entry.get("generation"))
        if index is not None:
            indexed[index] = entry
    return indexed


def _fds_delta(before: object, after: object) -> dict[str, Any] | None:
    """Diff the ``fds`` section: how many descriptors the target holds open."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    return {"count": _change(before.get("count"), after.get("count"))}


def _change(before: object, after: object) -> dict[str, int] | None:
    """Report one integer that moved, or ``None`` when either side is missing."""
    was = _as_int(before)
    now = _as_int(after)
    if was is None or now is None:
        return None
    return {"before": was, "after": now, "delta": now - was}


def _as_int(value: object) -> int | None:
    """Return a counter the target reported, rejecting bools and non-integers."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
