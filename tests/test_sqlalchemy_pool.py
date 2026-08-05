"""Tests for the reference SQLAlchemy connection pool collector plugin."""

from __future__ import annotations

import gc
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool, QueuePool

from pidprobe import available_collectors, discover_collectors, take_snapshot
from pidprobe.collectors import compose_collector_source
from pidprobe.plugins.sqlalchemy_pool import SQLALCHEMY_POOL_COLLECTOR

from .conftest import run_collector_source

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable, Iterator

    from sqlalchemy import Engine

# Must match tests/targets/sqlalchemy_pool.py: the target checks out more
# connections than the pool holds, so the pool is in real overflow.
POOL_SIZE = 5
MAX_OVERFLOW = 3
CHECKED_OUT = 7
EXPECTED_OVERFLOW = CHECKED_OUT - POOL_SIZE
QUEUE_POOL_TYPE = "sqlalchemy.pool.impl.QueuePool"

PLUGIN_GUIDE = Path(__file__).resolve().parents[1] / "docs" / "plugins.md"
EXAMPLE_PATTERN = re.compile(r"```python\n(.*?)```", re.DOTALL)
MAX_EXAMPLE_LINES = 30


def documented_example() -> str:
    """Return the collector the plugin guide shows as its worked example."""
    match = EXAMPLE_PATTERN.search(PLUGIN_GUIDE.read_text(encoding="utf-8"))
    assert match is not None, f"no Python example in {PLUGIN_GUIDE}"
    return match.group(1)


def section() -> dict[str, Any]:
    """Run the collector in this process and return its snapshot section."""
    payload = run_collector_source(
        compose_collector_source([SQLALCHEMY_POOL_COLLECTOR]),
    )
    reported: dict[str, Any] = payload["sections"]["sqlalchemy"]
    return reported


def pools_of(reported: dict[str, Any], type_name: str) -> list[dict[str, Any]]:
    """Return every reported pool of one pool class."""
    return [pool for pool in reported["pools"] if pool["type"] == type_name]


def queue_pool(checked_out: int, checked_in: int, overflow: int) -> dict[str, Any]:
    """Return the entry a ``QueuePool`` in that state is reported as."""
    return {
        "type": QUEUE_POOL_TYPE,
        "size": POOL_SIZE,
        "checked_out": checked_out,
        "checked_in": checked_in,
        "overflow": overflow,
    }


@pytest.fixture(autouse=True)
def _reclaim_disposed_pools() -> Iterator[None]:
    """Collect the pools a finished test left behind.

    ``Engine.dispose()`` swaps in a fresh pool rather than dropping the old
    one, and both stay reachable through reference cycles until the collector
    runs. The next test walks the whole heap, so without this it would report
    pools no test still owns.
    """
    yield
    gc.collect()


@pytest.fixture
def overflowing_engine() -> Iterator[Engine]:
    """Return an engine whose pool has more connections out than it holds."""
    engine = create_engine(
        "sqlite://",
        poolclass=QueuePool,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
    )
    connections = [engine.connect() for _ in range(CHECKED_OUT)]
    yield engine
    for connection in connections:
        connection.close()
    engine.dispose()


class TestRegistration:
    def test_the_plugin_is_published_as_an_entry_point(self):
        assert SQLALCHEMY_POOL_COLLECTOR in discover_collectors()

    def test_a_snapshot_runs_it_after_the_builtins(self):
        assert available_collectors()[-1] == SQLALCHEMY_POOL_COLLECTOR

    def test_the_collector_is_documented(self):
        assert SQLALCHEMY_POOL_COLLECTOR.description


class TestPoolReporting:
    @pytest.mark.usefixtures("overflowing_engine")
    def test_an_overflowing_pool_reports_size_checked_out_and_overflow(self):
        reported = section()

        assert reported["available"] is True
        assert queue_pool(CHECKED_OUT, 0, EXPECTED_OVERFLOW) in reported["pools"]

    def test_returning_a_connection_moves_it_back_into_the_pool(
        self,
        overflowing_engine,
    ):
        with overflowing_engine.connect() as extra:
            during = section()["pools"]
        assert extra.closed
        after = section()["pools"]

        # One more connection out means one more overflow; giving it back
        # leaves the overflow allocated but parks the connection in the queue.
        assert queue_pool(CHECKED_OUT + 1, 0, EXPECTED_OVERFLOW + 1) in during
        assert queue_pool(CHECKED_OUT, 1, EXPECTED_OVERFLOW + 1) in after

    def test_a_pool_without_queue_metrics_reports_nulls_not_zeros(self):
        engine = create_engine("sqlite://", poolclass=NullPool)
        try:
            reported = pools_of(section(), "sqlalchemy.pool.impl.NullPool")
        finally:
            engine.dispose()

        assert {
            "type": "sqlalchemy.pool.impl.NullPool",
            "size": None,
            "checked_out": None,
            "checked_in": None,
            "overflow": None,
        } in reported

    def test_a_target_without_sqlalchemy_reports_an_unavailable_section(
        self,
        monkeypatch,
    ):
        monkeypatch.delitem(sys.modules, "sqlalchemy.pool")

        reported = section()

        assert reported == {
            "available": False,
            "max_pools": reported["max_pools"],
            "pool_count": 0,
            "pools": [],
        }

    def test_more_pools_than_the_cap_are_counted_but_not_all_reported(self):
        cap = section()["max_pools"]
        engines = [
            create_engine("sqlite://", poolclass=NullPool) for _ in range(cap + 1)
        ]
        try:
            reported = section()
        finally:
            for engine in engines:
                engine.dispose()

        assert reported["pool_count"] > cap
        assert len(reported["pools"]) == cap


class TestDocumentedExample:
    """The plugin guide promises a collector short enough to read in one go."""

    def test_the_example_fits_in_thirty_lines(self):
        assert len(documented_example().splitlines()) <= MAX_EXAMPLE_LINES

    @pytest.mark.usefixtures("overflowing_engine")
    def test_the_example_collects_the_same_pool_the_plugin_does(self):
        namespace: dict[str, Any] = {}
        exec(documented_example(), namespace)  # noqa: S102 -- our own docs

        payload = run_collector_source(
            compose_collector_source([namespace["SQLALCHEMY_POOLS"]]),
        )

        reported = payload["sections"]["sqlalchemy"]
        assert reported["available"] is True
        assert {
            "type": "QueuePool",
            "size": POOL_SIZE,
            "checked_out": CHECKED_OUT,
            "overflow": EXPECTED_OVERFLOW,
        } in reported["pools"]


@pytest.mark.usefixtures("local_target", "overflowing_engine")
class TestSnapshotSection:
    def test_snap_publishes_the_pool_section_next_to_the_builtins(self):
        snapshot = take_snapshot(os.getpid())

        assert snapshot["sqlalchemy"]["available"] is True
        assert (
            queue_pool(CHECKED_OUT, 0, EXPECTED_OVERFLOW)
            in snapshot["sqlalchemy"]["pools"]
        )
        reports = {report["name"]: report for report in snapshot["meta"]["collectors"]}
        assert reports["sqlalchemy"]["status"] == "ok"


@pytest.mark.integration
def test_snapshot_of_a_live_target_reports_its_pool(
    remote_exec_supported: None,
    spawn_target: Callable[[str], subprocess.Popen[str]],
) -> None:
    proc = spawn_target("sqlalchemy_pool")

    snapshot = take_snapshot(proc.pid)

    assert snapshot["sqlalchemy"]["available"] is True
    assert pools_of(snapshot["sqlalchemy"], QUEUE_POOL_TYPE) == [
        {
            "type": QUEUE_POOL_TYPE,
            "size": POOL_SIZE,
            "checked_out": CHECKED_OUT,
            "checked_in": 0,
            "overflow": EXPECTED_OVERFLOW,
        },
    ]
