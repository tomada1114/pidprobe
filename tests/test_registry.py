"""Tests for third-party collector discovery through entry points."""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import fastjsonschema
import pytest

from pidprobe import (
    COLLECTOR_ENTRY_POINT_GROUP,
    Collector,
    CollectorSpec,
    available_collectors,
    discover_collectors,
    snapshot_schema,
    take_snapshot,
)
from pidprobe import registry as registry_module
from pidprobe.cli import EXIT_OK, main
from pidprobe.collectors import BUILTIN_COLLECTORS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

DUMMY = Collector(
    name="dummy",
    source="data = {'plugged_in': True}",
    description="Test-only collector added through an entry point",
)

EXPLODING = Collector(
    name="exploding",
    source="raise ValueError('collector blew up inside the target')",
    description="Test-only collector that fails inside the target",
)

BUILTIN_NAMES = [collector.name for collector in BUILTIN_COLLECTORS]

# pidprobe publishes its own reference plugin in the collector entry point
# group, so the tests that install a real distribution -- instead of
# monkeypatching discovery -- see it alongside the test plugin, ordered after
# "dummy" by entry point name.
SHIPPED_PLUGIN_NAMES = ["sqlalchemy"]

# Written to disk and imported by the tests that install a real distribution,
# so discovery goes through importlib.metadata exactly as it does for a plugin
# a user installed with pip.
PLUGIN_MODULE = '''
"""Test-only collector plugin."""

from pidprobe import Collector

DUMMY = Collector(
    name="dummy",
    source="data = {'plugged_in': True}",
    description="Test-only collector added through an entry point",
)

EXPLODING = Collector(
    name="exploding",
    source="raise ValueError('collector blew up inside the target')",
    description="Test-only collector that fails inside the target",
)


def build_broken():
    raise RuntimeError("this plugin is broken")
'''

DIST_METADATA = "Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"
PLUGIN_NAME = "dummy_pidprobe_plugin"


def entry_points_text(**targets: str) -> str:
    """Render an ``entry_points.txt`` publishing collectors in pidprobe's group."""
    lines = [f"[{COLLECTOR_ENTRY_POINT_GROUP}]"]
    lines += [f"{name} = {PLUGIN_NAME}:{target}" for name, target in targets.items()]
    return "\n".join(lines) + "\n"


def published(name: str, loader: Callable[[], object]) -> SimpleNamespace:
    """Return a stand-in for one entry point installed in the collector group."""
    return SimpleNamespace(name=name, value=f"{PLUGIN_NAME}:{name}", load=loader)


def duck_typed(name: str, source: object, description: object = "") -> SimpleNamespace:
    """Return a collector-shaped object that is not a :class:`Collector`."""
    return SimpleNamespace(name=name, source=source, description=description)


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Return a factory that answers discovery with the given entry points."""

    def _install(*points: SimpleNamespace) -> None:
        def fake_entry_points(*, group: str) -> list[SimpleNamespace]:
            return list(points) if group == COLLECTOR_ENTRY_POINT_GROUP else []

        monkeypatch.setattr(registry_module, "entry_points", fake_entry_points)

    return _install


@pytest.fixture
def install_plugin_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str], None]]:
    """Return a factory that installs a real distribution of collectors.

    A module and a matching ``.dist-info`` directory are written into a
    temporary directory on ``sys.path``, which is all ``importlib.metadata``
    needs to see a distribution -- no pip install, and the same behaviour on
    every platform.
    """

    def _install(published_entry_points: str) -> None:
        (tmp_path / f"{PLUGIN_NAME}.py").write_text(PLUGIN_MODULE, encoding="utf-8")
        dist_info = tmp_path / f"{PLUGIN_NAME}-1.0.dist-info"
        dist_info.mkdir()
        (dist_info / "METADATA").write_text(
            DIST_METADATA.format(name=PLUGIN_NAME),
            encoding="utf-8",
        )
        (dist_info / "entry_points.txt").write_text(
            published_entry_points,
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        importlib.invalidate_caches()

    yield _install

    # monkeypatch restores sys.path, but an imported plugin would outlive it.
    sys.modules.pop(PLUGIN_NAME, None)


class TestCollectorProtocol:
    @pytest.mark.parametrize("collector", BUILTIN_COLLECTORS, ids=lambda c: c.name)
    def test_builtin_collectors_satisfy_the_published_protocol(self, collector):
        assert isinstance(collector, CollectorSpec)

    def test_a_collector_satisfies_the_protocol_statically(self):
        # The annotation is the real assertion here: `just lint` type checks
        # the tests too, so this fails CI if Collector ever drifts from it.
        spec: CollectorSpec = DUMMY

        assert (spec.name, spec.source, spec.description) == (
            DUMMY.name,
            DUMMY.source,
            DUMMY.description,
        )

    def test_the_protocol_is_structural_not_nominal(self):
        assert isinstance(duck_typed("dummy", "data = {}"), CollectorSpec)
        assert not isinstance(SimpleNamespace(name="dummy"), CollectorSpec)


@pytest.mark.usefixtures("local_target")
class TestInstalledPlugin:
    def test_installed_plugin_adds_its_section_to_a_snapshot(
        self,
        install_plugin_package,
    ):
        install_plugin_package(entry_points_text(dummy="DUMMY"))

        snapshot = take_snapshot(os.getpid())

        assert snapshot["dummy"] == {"plugged_in": True}
        assert [report["name"] for report in snapshot["meta"]["collectors"]] == [
            *BUILTIN_NAMES,
            "dummy",
            *SHIPPED_PLUGIN_NAMES,
        ]

    def test_plugin_that_fails_to_load_leaves_every_other_section_intact(
        self,
        install_plugin_package,
        caplog,
    ):
        install_plugin_package(entry_points_text(broken="build_broken", dummy="DUMMY"))

        with caplog.at_level(logging.WARNING, logger="pidprobe.registry"):
            snapshot = take_snapshot(os.getpid())

        assert "broken" not in snapshot
        assert snapshot["dummy"] == {"plugged_in": True}
        assert all(snapshot[name] is not None for name in BUILTIN_NAMES)
        assert [report["status"] for report in snapshot["meta"]["collectors"]] == [
            "ok",
        ] * (len(BUILTIN_NAMES) + 1 + len(SHIPPED_PLUGIN_NAMES))
        assert "this plugin is broken" in caplog.text

    def test_plugin_raising_inside_the_target_only_nulls_its_own_section(
        self,
        install_plugin_package,
    ):
        install_plugin_package(
            entry_points_text(dummy="DUMMY", exploding="EXPLODING"),
        )

        snapshot = take_snapshot(os.getpid())

        assert snapshot["exploding"] is None
        assert snapshot["dummy"] == {"plugged_in": True}
        assert all(snapshot[name] is not None for name in BUILTIN_NAMES)
        reports = {report["name"]: report for report in snapshot["meta"]["collectors"]}
        assert reports["exploding"]["status"] == "error"
        assert reports["exploding"]["error"]["type"] == "ValueError"
        assert reports["dummy"]["status"] == "ok"

    def test_a_plugin_section_still_validates_against_the_schema(
        self,
        install_plugin_package,
    ):
        install_plugin_package(entry_points_text(dummy="DUMMY"))

        snapshot = take_snapshot(os.getpid())

        fastjsonschema.compile(snapshot_schema())(snapshot)


class TestDiscoverCollectors:
    def test_no_entry_points_means_no_plugins(self, installed):
        installed()

        assert discover_collectors() == ()

    def test_a_published_collector_is_returned_as_is(self, installed):
        installed(published("dummy", lambda: DUMMY))

        assert discover_collectors() == (DUMMY,)

    def test_a_published_factory_is_called_for_its_collector(self, installed):
        installed(published("dummy", lambda: lambda: DUMMY))

        assert discover_collectors() == (DUMMY,)

    def test_a_collector_shaped_object_is_copied_into_a_collector(self, installed):
        installed(published("dummy", lambda: duck_typed("dummy", "data = {}", "why")))

        assert discover_collectors() == (
            Collector(name="dummy", source="data = {}", description="why"),
        )

    def test_plugins_are_ordered_by_entry_point_name(self, installed):
        alpha = Collector(name="alpha", source="data = 1", description="")
        zulu = Collector(name="zulu", source="data = 2", description="")
        installed(published("zulu", lambda: zulu), published("alpha", lambda: alpha))

        assert discover_collectors() == (alpha, zulu)

    def test_group_is_configurable(self, monkeypatch):
        def fake_entry_points(*, group: str) -> list[SimpleNamespace]:
            assert group == "other.group"
            return [published("dummy", lambda: DUMMY)]

        monkeypatch.setattr(registry_module, "entry_points", fake_entry_points)

        assert discover_collectors(group="other.group") == (DUMMY,)

    @pytest.mark.parametrize(
        "loader",
        [
            pytest.param(object, id="not-a-collector"),
            pytest.param(lambda: duck_typed("dummy", 42), id="non-string-source"),
            pytest.param(
                lambda: duck_typed("dummy", "data = {}", None),
                id="non-string-description",
            ),
            pytest.param(
                lambda: duck_typed("not an identifier", "data = 1"),
                id="non-identifier-name",
            ),
            pytest.param(lambda: duck_typed("meta", "data = {}"), id="reserved-name"),
            pytest.param(lambda: duck_typed("bad", "data = ("), id="uncompilable"),
        ],
    )
    def test_a_misbehaving_plugin_is_skipped_and_logged(
        self,
        installed,
        caplog,
        loader,
    ):
        installed(published("bad", loader), published("dummy", lambda: DUMMY))

        with caplog.at_level(logging.WARNING, logger="pidprobe.registry"):
            collectors = discover_collectors()

        assert collectors == (DUMMY,)
        assert "skipping collector plugin 'bad'" in caplog.text

    def test_a_plugin_that_cannot_be_imported_is_skipped_and_logged(
        self,
        installed,
        caplog,
    ):
        def explode() -> object:
            message = "no module named nope"
            raise ModuleNotFoundError(message)

        installed(published("bad", explode), published("dummy", lambda: DUMMY))

        with caplog.at_level(logging.WARNING, logger="pidprobe.registry"):
            collectors = discover_collectors()

        assert collectors == (DUMMY,)
        assert "no module named nope" in caplog.text

    def test_the_first_plugin_to_claim_a_name_keeps_it(self, installed, caplog):
        second = Collector(name="dummy", source="data = 'second'", description="")
        installed(published("a", lambda: DUMMY), published("b", lambda: second))

        with caplog.at_level(logging.WARNING, logger="pidprobe.registry"):
            collectors = discover_collectors()

        assert collectors == (DUMMY,)
        assert "already taken" in caplog.text


class TestAvailableCollectors:
    def test_builtins_come_first_and_plugins_follow(self, installed):
        installed(published("dummy", lambda: DUMMY))

        assert available_collectors() == (*BUILTIN_COLLECTORS, DUMMY)

    def test_a_plugin_cannot_take_over_a_builtin_section(self, installed, caplog):
        impostor = Collector(name="stacks", source="data = 'mine'", description="")
        installed(published("impostor", lambda: impostor))

        with caplog.at_level(logging.WARNING, logger="pidprobe.registry"):
            collectors = available_collectors()

        assert collectors == tuple(BUILTIN_COLLECTORS)
        assert "already taken" in caplog.text

    def test_without_plugins_only_the_builtins_run(self, installed):
        installed()

        assert available_collectors() == tuple(BUILTIN_COLLECTORS)


@pytest.mark.usefixtures("local_target")
class TestSnapWithPlugins:
    def test_explicit_collectors_still_replace_everything(self, installed):
        installed(published("dummy", lambda: DUMMY))

        snapshot = take_snapshot(os.getpid(), collectors=[EXPLODING])

        assert "dummy" not in snapshot
        assert "stacks" not in snapshot

    def test_no_mask_keeps_plugin_sections(self, installed, capsys):
        installed(published("dummy", lambda: DUMMY))

        exit_code = main(["snap", str(os.getpid()), "--no-mask"])

        snapshot = json.loads(capsys.readouterr().out)
        assert exit_code == EXIT_OK
        assert snapshot["dummy"] == {"plugged_in": True}
        assert snapshot["stacks"]["masking_enabled"] is False
