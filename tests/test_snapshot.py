"""Tests for snapshot assembly and the published JSON Schema."""

from __future__ import annotations

import os
import re
import sys
from typing import Any

import fastjsonschema
import pytest

from pidprobe import SCHEMA_VERSION, ChannelError, TargetError, snapshot_schema
from pidprobe import _snapshot as snapshot_module
from pidprobe._envelope import Envelope, ErrorInfo
from pidprobe._snapshot import take_snapshot
from pidprobe.collectors import BUILTIN_COLLECTORS, Collector


@pytest.fixture
def validate_snapshot():
    """Return a validator compiled from the packaged JSON Schema."""
    return fastjsonschema.compile(snapshot_schema())


@pytest.fixture
def canned_target(monkeypatch):
    """Return a factory that makes the channel answer with a fixed envelope."""

    def _install(envelope: Envelope) -> None:
        def fake_execute(pid: int, source: str, **kwargs: Any) -> Envelope:
            return envelope

        monkeypatch.setattr(snapshot_module, "execute_in_target", fake_execute)

    return _install


@pytest.mark.usefixtures("local_target")
class TestTakeSnapshot:
    def test_snapshot_validates_against_the_published_schema(self, validate_snapshot):
        snapshot = take_snapshot(os.getpid())

        validate_snapshot(snapshot)

    def test_snapshot_has_one_section_per_collector(self):
        snapshot = take_snapshot(os.getpid())

        for collector in BUILTIN_COLLECTORS:
            assert snapshot[collector.name] is not None
        assert [report["name"] for report in snapshot["meta"]["collectors"]] == [
            collector.name for collector in BUILTIN_COLLECTORS
        ]
        assert all(
            report["status"] == "ok" for report in snapshot["meta"]["collectors"]
        )

    def test_meta_describes_both_interpreters_and_the_durations(self):
        snapshot = take_snapshot(os.getpid())

        meta = snapshot["meta"]
        assert snapshot["schema_version"] == SCHEMA_VERSION
        assert meta["pid"] == os.getpid()
        assert meta["prober"]["python_version"] == sys.version.split()[0]
        assert meta["target"]["python_version"] == sys.version.split()[0]
        assert meta["target"]["pid"] == os.getpid()
        assert meta["pidprobe_version"]
        assert meta["stop_duration_ms"] >= 0
        assert meta["elapsed_ms"] >= meta["stop_duration_ms"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z", meta["captured_at"])

    def test_channel_arguments_are_passed_through(self, local_target):
        take_snapshot(4321, timeout_seconds=2.5, allow_socket=False)

        assert local_target["pid"] == 4321
        assert local_target["timeout_seconds"] == 2.5
        assert local_target["allow_socket"] is False

    def test_selected_collectors_replace_the_built_in_ones(self):
        custom = Collector(name="custom", source="data = {'ok': True}", description="")

        snapshot = take_snapshot(os.getpid(), collectors=[custom])

        assert snapshot["custom"] == {"ok": True}
        assert "stacks" not in snapshot

    def test_failing_collector_leaves_a_null_section_and_a_reason(self):
        broken = Collector(
            name="broken",
            source="raise KeyError('nope')",
            description="",
        )

        snapshot = take_snapshot(os.getpid(), collectors=[broken])

        assert snapshot["broken"] is None
        report = snapshot["meta"]["collectors"][0]
        assert report["status"] == "error"
        assert report["error"]["type"] == "KeyError"


class TestTargetFailures:
    def test_target_side_failure_raises_target_error(self, canned_target):
        canned_target(
            Envelope(
                status="error",
                error=ErrorInfo(
                    type="TypeError",
                    message="not serializable",
                    traceback="Traceback...",
                ),
                payload=None,
            ),
        )

        with pytest.raises(TargetError, match=r"TypeError: not serializable") as info:
            take_snapshot(4321)

        assert info.value.pid == 4321
        assert info.value.error["type"] == "TypeError"

    def test_missing_payload_raises_channel_error(self, canned_target):
        canned_target(Envelope(status="ok", error=None, payload=None))

        with pytest.raises(ChannelError, match=r"without a payload"):
            take_snapshot(4321)

    def test_unusable_durations_are_reported_as_null(self, canned_target):
        canned_target(
            Envelope(
                status="ok",
                error=None,
                payload={"sections": {}, "collectors": [], "stop_duration_ms": None},
            ),
        )

        snapshot = take_snapshot(4321)

        assert snapshot["meta"]["stop_duration_ms"] is None
        assert snapshot["meta"]["target"] == {}


class TestSnapshotSchema:
    def test_schema_pins_the_version_the_package_reports(self):
        schema = snapshot_schema()

        assert schema["properties"]["schema_version"]["const"] == SCHEMA_VERSION

    def test_each_call_returns_an_independent_copy(self):
        first = snapshot_schema()
        first["properties"].clear()

        assert snapshot_schema()["properties"]

    def test_snapshot_missing_a_section_is_rejected(self, validate_snapshot):
        with pytest.raises(fastjsonschema.JsonSchemaException):
            validate_snapshot({"schema_version": SCHEMA_VERSION, "meta": {}})

    @pytest.mark.usefixtures("local_target")
    def test_snapshot_with_a_wrong_schema_version_is_rejected(self, validate_snapshot):
        snapshot = take_snapshot(os.getpid())
        snapshot["schema_version"] = "0.9"

        with pytest.raises(fastjsonschema.JsonSchemaException):
            validate_snapshot(snapshot)
