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
from ._schema import SCHEMA_VERSION, snapshot_schema
from ._snapshot import take_snapshot
from .core import add

try:
    __version__ = version("pidprobe")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "SCHEMA_VERSION",
    "AttachError",
    "ChannelError",
    "ProbeError",
    "ProbeTimeoutError",
    "TargetError",
    "__version__",
    "add",
    "snapshot_schema",
    "take_snapshot",
]
