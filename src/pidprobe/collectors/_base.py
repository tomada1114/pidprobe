"""Definition of a collector and composition of the injected collector source.

A collector is a snippet of Python source that runs *inside* the target
process. :func:`compose_collector_source` wraps every snippet in its own
function and appends a driver that runs them one by one, so a collector that
raises costs its own section only -- the rest of the snapshot still comes
back. The result is the ``collector_source`` contract of
:func:`pidprobe._inject.build_injection_script`: it assigns ``payload``.

Because each snippet becomes a function body, every name it binds is local to
that function and cannot leak into the target's namespace, which is why the
snippets themselves need no ``_pidprobe_`` prefixes. The driver's own names do
carry the prefix, as does the helper ``_pidprobe_target_frames()`` that
snippets may call.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

RESERVED_SECTION_NAMES = frozenset({"meta", "schema_version"})
"""Snapshot keys a collector may not claim; see :mod:`pidprobe._snapshot`."""

SECTION_FUNCTION_PREFIX = "_pidprobe_section_"
"""Prefix of the generated per-collector function inside the target."""

_SPECS_PLACEHOLDER = "__PIDPROBE_SPECS__"
_BODY_INDENT = " " * 4
_MILLISECONDS_LITERAL = "1000.0"


@dataclass(frozen=True, slots=True)
class Collector:
    """One named section of a snapshot, collected inside the target process.

    Attributes:
        name: Snapshot key this collector fills. Must be a Python identifier
            so it can name the generated function, and must not shadow a
            reserved snapshot key.
        source: Python source run as a function body inside the target. It
            must assign a JSON-serializable value to the name ``data`` and may
            call ``_pidprobe_target_frames()``.
        description: One-line summary used by documentation and ``--help``.
    """

    name: str
    source: str
    description: str

    def __post_init__(self) -> None:
        """Reject names that cannot be used as a snapshot key.

        Raises:
            ValueError: If the name is not an identifier or is reserved.
        """
        if not self.name.isidentifier():
            message = f"collector name must be an identifier, got {self.name!r}"
            raise ValueError(message)
        if self.name in RESERVED_SECTION_NAMES:
            message = f"collector name {self.name!r} is reserved by the snapshot format"
            raise ValueError(message)


# Kept out of the collector snippets so every collector is timed and isolated
# the same way. `_pidprobe_sys` and `_pidprobe_traceback` are bound by the
# enclosing injected script and reachable here as closure variables.
_DRIVER_SOURCE = f"""
import os as _pidprobe_osmod
import time as _pidprobe_time

_pidprobe_specs = ({_SPECS_PLACEHOLDER})
_pidprobe_started = _pidprobe_time.perf_counter()
_pidprobe_sections = {{}}
_pidprobe_reports = []

for _pidprobe_name, _pidprobe_section in _pidprobe_specs:
    _pidprobe_begin = _pidprobe_time.perf_counter()
    try:
        _pidprobe_sections[_pidprobe_name] = _pidprobe_section()
        _pidprobe_report = {{"name": _pidprobe_name, "status": "ok", "error": None}}
    except Exception:
        _pidprobe_sections[_pidprobe_name] = None
        _pidprobe_failure = _pidprobe_sys.exc_info()[1]
        _pidprobe_report = {{
            "name": _pidprobe_name,
            "status": "error",
            "error": {{
                "type": type(_pidprobe_failure).__name__,
                "message": str(_pidprobe_failure),
                "traceback": _pidprobe_traceback.format_exc(),
            }},
        }}
    _pidprobe_report["duration_ms"] = (
        _pidprobe_time.perf_counter() - _pidprobe_begin
    ) * {_MILLISECONDS_LITERAL}
    _pidprobe_reports.append(_pidprobe_report)

payload = {{
    "sections": _pidprobe_sections,
    "collectors": _pidprobe_reports,
    "target": {{
        "pid": _pidprobe_osmod.getpid(),
        "python_version": _pidprobe_sys.version.split()[0],
        "implementation": _pidprobe_sys.implementation.name,
        "platform": _pidprobe_sys.platform,
        "executable": _pidprobe_sys.executable,
    }},
    "stop_duration_ms": (
        _pidprobe_time.perf_counter() - _pidprobe_started
    ) * {_MILLISECONDS_LITERAL},
}}
"""


def _section_function(collector: Collector) -> str:
    """Wrap one collector snippet in the function the driver calls."""
    body = textwrap.dedent(collector.source).strip("\n")
    return "\n".join(
        (
            f"def {SECTION_FUNCTION_PREFIX}{collector.name}():",
            textwrap.indent(body, _BODY_INDENT),
            f"{_BODY_INDENT}return data",
            "",
        ),
    )


def compose_collector_source(collectors: Iterable[Collector]) -> str:
    """Render the collector source injected into the target process.

    Args:
        collectors: Collectors to run, in output order.

    Returns:
        Python source that assigns ``payload``: the collected sections, a
        per-collector status report, the target's own interpreter details and
        the time the target spent building all of it.

    Raises:
        ValueError: If no collector is given, or two share a name.
    """
    ordered = tuple(collectors)
    if not ordered:
        message = "at least one collector is required"
        raise ValueError(message)
    names = [collector.name for collector in ordered]
    if len(set(names)) != len(names):
        message = f"collector names must be unique, got {names}"
        raise ValueError(message)
    specs = "".join(
        f'("{name}", {SECTION_FUNCTION_PREFIX}{name}), ' for name in names
    ).rstrip()
    parts = [_section_function(collector) for collector in ordered]
    parts.append(_DRIVER_SOURCE.replace(_SPECS_PLACEHOLDER, specs))
    return "\n".join(parts)
