"""Tests for the public pidprobe API."""

from __future__ import annotations

import importlib
import importlib.metadata as importlib_metadata
from importlib.metadata import PackageNotFoundError, version

import pidprobe
from pidprobe import __all__, __version__, add


class TestAdd:
    def test_positive_numbers(self):
        assert add(1, 2) == 3

    def test_negative_numbers(self):
        assert add(-1, -2) == -3

    def test_zero(self):
        assert add(0, 0) == 0


class TestPackageMetadata:
    def test_public_exports(self):
        assert set(__all__) == {
            "COLLECTOR_ENTRY_POINT_GROUP",
            "SCHEMA_VERSION",
            "AttachError",
            "ChannelError",
            "Check",
            "CheckStatus",
            "Collector",
            "CollectorSpec",
            "Diagnosis",
            "Evaluation",
            "ProbeError",
            "ProbeTimeoutError",
            "TargetError",
            "__version__",
            "add",
            "available_collectors",
            "diagnose",
            "discover_collectors",
            "evaluate_in_target",
            "snapshot_schema",
            "take_snapshot",
        }

    def test_version_matches_installed_metadata(self):
        assert __version__ == version("pidprobe")

    def test_version_falls_back_when_package_not_installed(self, monkeypatch):
        def fake_version(_: str) -> str:
            raise PackageNotFoundError

        with monkeypatch.context() as patched:
            patched.setattr(importlib_metadata, "version", fake_version)
            reloaded = importlib.reload(pidprobe)

        assert reloaded.__version__ == "0.0.0+unknown"
        importlib.reload(pidprobe)
