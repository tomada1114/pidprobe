"""Assembly of one snapshot document from one probe of a live process.

The target answers with the raw material -- a section per collector, a status
report per collector and its own interpreter details -- and this module turns
that into the documented output format: ``schema_version``, ``meta`` and one
top-level key per collector.
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ._channel import DEFAULT_TIMEOUT_SECONDS, execute_in_target
from ._errors import ChannelError, TargetError
from ._schema import SCHEMA_VERSION
from .collectors import BUILTIN_COLLECTORS, compose_collector_source

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ._envelope import Envelope, ErrorInfo
    from .collectors import Collector

_MILLISECONDS = 1000.0
_DURATION_DIGITS = 3
_UTC_SUFFIX = "+00:00"

# Any: a snapshot is a JSON document whose sections are collector-defined.
Snapshot = dict[str, Any]


def take_snapshot(
    pid: int,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    collectors: Iterable[Collector] | None = None,
    allow_socket: bool = True,
) -> Snapshot:
    """Collect one snapshot from a running CPython 3.14+ process.

    Args:
        pid: Target process id.
        timeout_seconds: Hard budget covering injection and read-back.
        collectors: Collectors to run; defaults to
            :data:`~pidprobe.collectors.BUILTIN_COLLECTORS`.
        allow_socket: Set to ``False`` to force the tempfile return channel.

    Returns:
        The snapshot document, ready to be serialized to JSON.

    Raises:
        AttachError: If the target refuses the injection.
        ProbeTimeoutError: If the target never reaches a safe evaluation
            point within the budget.
        ChannelError: If the answer does not match the envelope contract.
        TargetError: If the injected code failed as a whole inside the target.
    """
    chosen = BUILTIN_COLLECTORS if collectors is None else tuple(collectors)
    source = compose_collector_source(chosen)
    started = time.perf_counter()
    envelope = execute_in_target(
        pid,
        source,
        timeout_seconds=timeout_seconds,
        allow_socket=allow_socket,
    )
    elapsed_ms = (time.perf_counter() - started) * _MILLISECONDS
    return _document(pid, _payload(pid, envelope), elapsed_ms)


def _payload(pid: int, envelope: Envelope) -> dict[str, Any]:
    """Return the payload of a successful envelope.

    Raises:
        TargetError: If the target reported a failure.
        ChannelError: If the target reported success without a payload.
    """
    if envelope["status"] == "error":
        error: ErrorInfo | None = envelope["error"]
        if error is None:  # pragma: no cover -- parse_envelope rejects this
            message = f"target reported an error without details for pid {pid}"
            raise ChannelError(message)
        raise TargetError(pid, error)
    payload = envelope["payload"]
    if payload is None:
        message = f"target reported success without a payload for pid {pid}"
        raise ChannelError(message)
    return payload


def _interpreter() -> dict[str, Any]:
    """Describe the interpreter pidprobe itself runs on."""
    return {
        "python_version": sys.version.split()[0],
        "implementation": sys.implementation.name,
        "platform": sys.platform,
        "executable": sys.executable,
    }


def _pidprobe_version() -> str:
    """Return the installed pidprobe version."""
    # Imported here because the package __init__ imports this module.
    from . import __version__  # noqa: PLC0415

    return __version__


def _document(pid: int, payload: dict[str, Any], elapsed_ms: float) -> Snapshot:
    """Turn a target payload into the documented snapshot format.

    Args:
        pid: Target process id.
        payload: Raw payload the injected code produced.
        elapsed_ms: Wall-clock duration of the probe, measured by the prober.

    Returns:
        The snapshot document; collector sections sit at the top level, so a
        section can never overwrite ``meta`` or ``schema_version``.
    """
    stop_duration_ms = payload.get("stop_duration_ms")
    document: Snapshot = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "pid": pid,
            "captured_at": datetime.now(UTC).isoformat().replace(_UTC_SUFFIX, "Z"),
            "pidprobe_version": _pidprobe_version(),
            "prober": _interpreter(),
            "target": payload.get("target") or {},
            "stop_duration_ms": _rounded(stop_duration_ms),
            "elapsed_ms": _rounded(elapsed_ms),
            "collectors": [
                {**report, "duration_ms": _rounded(report.get("duration_ms"))}
                for report in payload.get("collectors") or []
            ],
        },
    }
    for name, section in (payload.get("sections") or {}).items():
        document[str(name)] = section
    return document


def _rounded(value: object) -> float | None:
    """Round a duration reported in milliseconds, keeping ``None`` as is."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), _DURATION_DIGITS)
    return None
