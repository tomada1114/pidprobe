"""Public package interface for pidprobe."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from ._errors import (
    AttachError,
    ChannelError,
    ProbeError,
    ProbeTimeoutError,
    TargetError,
)
from ._eval import Evaluation, evaluate_in_target
from ._schema import SCHEMA_VERSION, snapshot_schema
from ._snapshot import take_snapshot
from .collectors import Collector
from .core import add
from .registry import (
    COLLECTOR_ENTRY_POINT_GROUP,
    CollectorSpec,
    available_collectors,
    discover_collectors,
)

try:
    __version__ = version("pidprobe")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "COLLECTOR_ENTRY_POINT_GROUP",
    "SCHEMA_VERSION",
    "AttachError",
    "ChannelError",
    "Collector",
    "CollectorSpec",
    "Evaluation",
    "ProbeError",
    "ProbeTimeoutError",
    "TargetError",
    "__version__",
    "add",
    "available_collectors",
    "discover_collectors",
    "evaluate_in_target",
    "snapshot_schema",
    "take_snapshot",
]
