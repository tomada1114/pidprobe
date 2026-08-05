"""Built-in collectors and the registration point for the snapshot sections.

:data:`BUILTIN_COLLECTORS` is the single ordered list of everything
``pidprobe snap`` collects; adding a section means adding a
:class:`~pidprobe.collectors._base.Collector` here.
"""

from __future__ import annotations

from ._base import (
    RESERVED_SECTION_NAMES,
    Collector,
    compose_collector_source,
)
from ._fds import FDS_COLLECTOR
from ._gc import GC_COLLECTOR
from ._objects import OBJECTS_COLLECTOR
from ._stacks import STACKS_COLLECTOR

BUILTIN_COLLECTORS: tuple[Collector, ...] = (
    STACKS_COLLECTOR,
    OBJECTS_COLLECTOR,
    GC_COLLECTOR,
    FDS_COLLECTOR,
)
"""Collectors every snapshot runs, in output order."""

__all__ = [
    "BUILTIN_COLLECTORS",
    "FDS_COLLECTOR",
    "GC_COLLECTOR",
    "OBJECTS_COLLECTOR",
    "RESERVED_SECTION_NAMES",
    "STACKS_COLLECTOR",
    "Collector",
    "compose_collector_source",
]
