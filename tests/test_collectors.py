"""Tests for the built-in collectors and their composition."""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pidprobe._envelope import parse_envelope
from pidprobe._inject import ChannelKind, ChannelSpec, write_injection_script
from pidprobe.collectors import (
    BUILTIN_COLLECTORS,
    FDS_COLLECTOR,
    GC_COLLECTOR,
    OBJECTS_COLLECTOR,
    STACKS_COLLECTOR,
    Collector,
    compose_collector_source,
)

from .conftest import run_collector_source

MARKER = "pidprobe-collector-marker"


def collect(*collectors: Collector) -> dict[str, Any]:
    """Run collectors in this process and return their sections."""
    payload = run_collector_source(compose_collector_source(collectors))
    sections: dict[str, Any] = payload["sections"]
    return sections


class TestCollectorDefinition:
    def test_name_must_be_an_identifier(self):
        with pytest.raises(ValueError, match=r"must be an identifier"):
            Collector(name="not a name", source="data = {}", description="")

    def test_reserved_name_is_rejected(self):
        with pytest.raises(ValueError, match=r"is reserved"):
            Collector(name="meta", source="data = {}", description="")

    @pytest.mark.parametrize("collector", BUILTIN_COLLECTORS, ids=lambda c: c.name)
    def test_builtin_collectors_are_documented(self, collector):
        assert collector.description


class TestComposeCollectorSource:
    def test_empty_collector_list_is_rejected(self):
        with pytest.raises(ValueError, match=r"at least one collector"):
            compose_collector_source([])

    def test_duplicate_names_are_rejected(self):
        duplicate = Collector(name="dupe", source="data = {}", description="")
        with pytest.raises(ValueError, match=r"must be unique"):
            compose_collector_source([duplicate, duplicate])

    def test_source_defines_one_function_per_collector(self):
        source = compose_collector_source(BUILTIN_COLLECTORS)

        for collector in BUILTIN_COLLECTORS:
            assert f"def _pidprobe_section_{collector.name}():" in source

    def test_payload_reports_every_collector_and_the_target(self):
        payload = run_collector_source(compose_collector_source(BUILTIN_COLLECTORS))

        reported = [report["name"] for report in payload["collectors"]]
        assert reported == [collector.name for collector in BUILTIN_COLLECTORS]
        assert payload["target"]["pid"] > 0
        assert payload["stop_duration_ms"] >= 0

    def test_generated_script_answers_from_a_fresh_interpreter(self, tmp_path):
        # Proves the composed source survives being embedded in the injected
        # script -- indentation, imports and JSON serialization included --
        # without needing sys.remote_exec, which most CI runners cannot use.
        spec = ChannelSpec(
            kind=ChannelKind.TEMPFILE,
            path=str(tmp_path / "result.json"),
            timeout_seconds=5.0,
        )
        script = write_injection_script(
            compose_collector_source(BUILTIN_COLLECTORS),
            spec,
            tmp_path,
        )

        completed = subprocess.run(  # noqa: S603 -- fixed, trusted argv; no shell
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 0, completed.stderr
        envelope = parse_envelope(Path(spec.path).read_text(encoding="utf-8"))
        assert envelope["status"] == "ok", envelope["error"]
        payload = envelope["payload"]
        assert payload is not None
        assert sorted(payload["sections"]) == sorted(
            collector.name for collector in BUILTIN_COLLECTORS
        )
        assert all(section is not None for section in payload["sections"].values())

    def test_failing_collector_costs_only_its_own_section(self):
        broken = Collector(
            name="broken",
            source="raise RuntimeError('collector exploded')",
            description="",
        )

        payload = run_collector_source(
            compose_collector_source([GC_COLLECTOR, broken]),
        )

        assert payload["sections"]["gc"]["enabled"] in (True, False)
        assert payload["sections"]["broken"] is None
        report = payload["collectors"][1]
        assert report["status"] == "error"
        assert report["error"]["type"] == "RuntimeError"
        assert report["error"]["message"] == "collector exploded"
        assert "collector exploded" in report["error"]["traceback"]


class TestStacksCollector:
    def test_reports_this_frame_with_its_locals(self):
        marker = MARKER

        sections = collect(STACKS_COLLECTOR)

        functions = [
            frame["function"]
            for thread in sections["stacks"]["threads"]
            for frame in thread["frames"]
        ]
        assert (
            "TestStacksCollector.test_reports_this_frame_with_its_locals" in functions
        )
        locals_by_function = {
            frame["function"]: frame["locals"]
            for thread in sections["stacks"]["threads"]
            for frame in thread["frames"]
        }
        reported = locals_by_function[
            "TestStacksCollector.test_reports_this_frame_with_its_locals"
        ]
        assert reported["marker"] == repr(marker)

    def test_main_thread_is_reported_first_with_its_name(self):
        threads = collect(STACKS_COLLECTOR)["stacks"]["threads"]

        assert threads[0]["is_main"] is True
        assert threads[0]["name"] == "MainThread"
        assert threads[0]["daemon"] is False

    def test_long_repr_values_are_truncated(self):
        long_value = "x" * 10_000  # noqa: F841 -- read back out of this frame's locals

        sections = collect(STACKS_COLLECTOR)

        reported = [
            frame["locals"]["long_value"]
            for thread in sections["stacks"]["threads"]
            for frame in thread["frames"]
            if frame["locals"] and "long_value" in frame["locals"]
        ]
        assert reported
        assert all(text.endswith("...<truncated>") for text in reported)
        assert all(len(text) < 1_000 for text in reported)

    def test_unrepresentable_locals_do_not_break_the_frame(self):
        class Exploding:
            def __repr__(self):
                message = "repr exploded"
                raise ValueError(message)

        exploding = Exploding()  # noqa: F841 -- read back out of this frame's locals

        sections = collect(STACKS_COLLECTOR)

        reported = [
            frame["locals"]["exploding"]
            for thread in sections["stacks"]["threads"]
            for frame in thread["frames"]
            if frame["locals"] and "exploding" in frame["locals"]
        ]
        assert reported == ["<unrepresentable Exploding: ValueError>"]


class TestObjectsCollector:
    def test_counts_tracked_objects_by_type(self):
        section = collect(OBJECTS_COLLECTOR)["objects"]

        assert section["total_tracked"] > 0
        assert section["distinct_types"] > 0
        assert len(section["top"]) <= section["top_n"]
        assert [entry["count"] for entry in section["top"]] == sorted(
            (entry["count"] for entry in section["top"]),
            reverse=True,
        )
        assert "dict" in {entry["type"] for entry in section["top"]}


class TestGcCollector:
    def test_reports_counts_thresholds_and_generations(self):
        section = collect(GC_COLLECTOR)["gc"]

        assert section["enabled"] is True
        assert len(section["counts"]) == len(section["thresholds"])
        assert section["generations"]
        assert [entry["generation"] for entry in section["generations"]] == list(
            range(len(section["generations"])),
        )
        assert section["garbage_count"] >= 0


class TestFdsCollector:
    def test_reports_an_open_file_with_its_path(self, tmp_path):
        path = tmp_path / "probe.txt"
        path.write_text("content", encoding="utf-8")

        with path.open(encoding="utf-8") as handle:
            section = collect(FDS_COLLECTOR)["fds"]
            expected_fd = handle.fileno()

        assert section["supported"] is True
        assert section["count"] == len(section["descriptors"])
        by_fd = {entry["fd"]: entry for entry in section["descriptors"]}
        assert by_fd[expected_fd]["kind"] == "file"
        assert by_fd[expected_fd]["size"] == len("content")

    def test_reports_an_open_socket(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            expected_port = server.getsockname()[1]

            section = collect(FDS_COLLECTOR)["fds"]
            by_fd = {entry["fd"]: entry for entry in section["descriptors"]}
            entry = by_fd[server.fileno()]

        assert entry["kind"] == "socket"
        if sys.platform.startswith("linux"):
            assert entry["socket"]["family"] == "AF_INET"
            assert entry["socket"]["laddr"] == ["127.0.0.1", expected_port]
            assert entry["socket"]["raddr"] is None
        else:
            # Only Linux exposes SO_DOMAIN, so the family cannot be detected.
            assert entry["socket"] is None

    def test_descriptors_are_sorted_by_number(self):
        descriptors = collect(FDS_COLLECTOR)["fds"]["descriptors"]

        numbers = [entry["fd"] for entry in descriptors]
        assert numbers == sorted(numbers)
