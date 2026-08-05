"""Discovery of collectors published by third-party packages.

Any package can add a section to every snapshot by publishing an entry point
in the ``pidprobe.collectors`` group::

    [project.entry-points."pidprobe.collectors"]
    sqlalchemy = "pidprobe_sqlalchemy:COLLECTOR"

The entry point resolves either to a collector or to a zero-argument callable
returning one, so a plugin may hand over a module-level constant or build its
collector only when it is asked for.

Discovery never raises. A plugin that fails to import, hands back something
that is not a collector, carries source that does not compile, or claims a
section name that is already taken is logged and left out, because one broken
plugin must not cost the snapshot every other section. That same isolation
continues inside the target, where
the collector driver turns a raising collector into a ``null`` section and a
reason in ``meta.collectors``.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .collectors import BUILTIN_COLLECTORS, Collector, compose_collector_source

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from importlib.metadata import EntryPoint

COLLECTOR_ENTRY_POINT_GROUP = "pidprobe.collectors"
"""Entry point group a package publishes its collectors in."""

_logger = logging.getLogger(__name__)


@runtime_checkable
class CollectorSpec(Protocol):
    """The contract a collector fulfils, built-in or third-party.

    A collector does not run in the prober: its :attr:`source` is composed
    into the script pidprobe injects into the target process, which is why a
    plugin contributes source text rather than a function to call.
    """

    @property
    def name(self) -> str:
        """Snapshot key this collector fills; must be a Python identifier."""

    @property
    def source(self) -> str:
        """Python source run as a function body inside the target process.

        It must assign a JSON-serializable value to the name ``data``, and may
        call ``_pidprobe_target_frames()`` to reach the target's stack frames.
        """

    @property
    def description(self) -> str:
        """One-line summary of the section, for documentation and diagnostics."""


def discover_collectors(
    *,
    group: str = COLLECTOR_ENTRY_POINT_GROUP,
) -> tuple[Collector, ...]:
    """Load every collector published in an entry point group.

    Args:
        group: Entry point group to read; defaults to
            :data:`COLLECTOR_ENTRY_POINT_GROUP`.

    Returns:
        The collectors that loaded cleanly, ordered by entry point name so the
        result does not depend on installation order. Anything that failed to
        load or repeated a name already claimed is logged and left out.
    """
    published = sorted(entry_points(group=group), key=lambda point: point.name)
    loaded = [
        collector for point in published if (collector := _load(point)) is not None
    ]
    return tuple(_accepted(loaded, set()))


def available_collectors(
    *,
    group: str = COLLECTOR_ENTRY_POINT_GROUP,
) -> tuple[Collector, ...]:
    """Return the collectors a snapshot runs when it is given none.

    Args:
        group: Entry point group to read; defaults to
            :data:`COLLECTOR_ENTRY_POINT_GROUP`.

    Returns:
        :data:`~pidprobe.collectors.BUILTIN_COLLECTORS` in their documented
        order, followed by the discovered plugins. A plugin cannot take over a
        built-in section: one that claims a built-in name is logged and
        skipped, so ``stacks`` always means what this package documents.
    """
    builtin = tuple(BUILTIN_COLLECTORS)
    taken = {collector.name for collector in builtin}
    return builtin + tuple(_accepted(discover_collectors(group=group), taken))


def _load(entry_point: EntryPoint) -> Collector | None:
    """Return the collector an entry point publishes, or ``None`` if it cannot."""
    try:
        collector = _as_collector(entry_point.load())
    except Exception:
        _logger.exception(
            "skipping collector plugin %r (%s): it could not be loaded",
            entry_point.name,
            entry_point.value,
        )
        return None
    return collector


def _as_collector(published: object) -> Collector:
    """Turn what an entry point resolved to into a collector.

    Args:
        published: The loaded entry point: a collector, or a zero-argument
            callable returning one.

    Returns:
        A :class:`~pidprobe.collectors.Collector` copied from the published
        object, so the rest of the run works with a validated value object
        instead of whatever shape the plugin handed over.

    Raises:
        TypeError: If the result does not carry the three string attributes of
            :class:`CollectorSpec`.
        ValueError: If the name cannot be used as a snapshot key.
        SyntaxError: If the source cannot be composed into the injected script.
    """
    resolved = published() if callable(published) else published
    collector = Collector(
        name=_string_attribute(resolved, "name"),
        source=_string_attribute(resolved, "source"),
        description=_string_attribute(resolved, "description"),
    )
    # Source that does not compile would be a SyntaxError in the script as a
    # whole, which costs every section rather than this one; compiling the
    # composition here turns that into one skipped plugin.
    compile(compose_collector_source([collector]), "<pidprobe-collector>", "exec")
    return collector


def _string_attribute(candidate: object, attribute: str) -> str:
    """Read one string attribute of the collector contract.

    Raises:
        TypeError: If the attribute is missing or is not a string.
    """
    value = getattr(candidate, attribute, None)
    if not isinstance(value, str):
        message = (
            f"a collector must carry a string {attribute!r}, "
            f"which {type(candidate).__name__} does not"
        )
        raise TypeError(message)
    return value


def _accepted(collectors: Iterable[Collector], taken: set[str]) -> Iterator[Collector]:
    """Yield the collectors whose section name is still free.

    Args:
        collectors: Candidates, in the order they should be offered.
        taken: Names already claimed; extended with every accepted name.
    """
    for collector in collectors:
        if collector.name in taken:
            _logger.warning(
                "skipping collector plugin %r: that section name is already taken",
                collector.name,
            )
            continue
        taken.add(collector.name)
        yield collector


__all__ = [
    "COLLECTOR_ENTRY_POINT_GROUP",
    "CollectorSpec",
    "available_collectors",
    "discover_collectors",
]
