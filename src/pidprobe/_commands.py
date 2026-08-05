"""What each ``pidprobe`` subcommand does once its arguments are parsed.

The command-line surface itself -- the parsers, their help text and the
dispatch -- lives in :mod:`pidprobe.cli`, the only module of the package that
imports this one. Keeping the two apart means "how the tool is described" and
"what the tool does" can each stay readable; every handler here has the same
shape, taking the parsed namespace and returning a process exit code.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

from ._diagnosis import as_document, render_text
from ._diff import iter_snapshot_deltas
from ._doctor import diagnose
from ._eval import evaluate_in_target
from ._snapshot import take_snapshot
from .collectors._stacks import build_stacks_collector
from .registry import available_collectors

if TYPE_CHECKING:
    import argparse
    from collections.abc import Mapping

    from .collectors import Collector

EXIT_OK = 0
"""Exit code for a command that ran and printed its output."""

EXIT_PROBE_ERROR = 1
"""Exit code for a probe that failed: attach, timeout, channel or target.

``doctor`` reuses it for a diagnosis that found a blocking check, so "a probe
of this pid would not work" is one exit code however it was discovered.
"""

EXIT_INTERRUPTED = 130
"""Exit code for a command stopped with Ctrl-C, by the 128 + SIGINT convention.

``diff`` without ``--count`` is meant to be ended this way, so an interrupt is
a normal way to finish rather than a failure, and it is distinguished from
:data:`EXIT_PROBE_ERROR` for a shell that wants to tell the two apart.
"""

_PRETTY_INDENT = 2
_COMPACT_SEPARATORS = (",", ":")


def write_json(document: Mapping[str, Any], *, is_pretty: bool) -> None:
    """Write a JSON document to stdout, compact and flushed.

    The flush is what makes a streaming command usable: without it a delta
    would sit in the block buffer of a pipe until the next one filled it.
    """
    if is_pretty:
        text = json.dumps(document, indent=_PRETTY_INDENT)
    else:
        text = json.dumps(document, separators=_COMPACT_SEPARATORS)
    sys.stdout.write(f"{text}\n")
    sys.stdout.flush()


def _snap_collectors(*, is_masked: bool) -> tuple[Collector, ...] | None:
    """Return the collectors ``snap`` runs, or ``None`` for the defaults.

    Masking is baked into the stacks collector's generated source, so turning
    it off means swapping that one collector for an unmasked build and leaving
    every other collector -- discovered plugins included -- exactly as it is.
    """
    if is_masked:
        return None
    unmasked = build_stacks_collector(is_masked=False)
    return tuple(
        unmasked if collector.name == unmasked.name else collector
        for collector in available_collectors()
    )


def run_snap(args: argparse.Namespace) -> int:
    """Take one snapshot and print it."""
    snapshot = take_snapshot(
        args.pid,
        timeout_seconds=args.timeout,
        collectors=_snap_collectors(is_masked=args.is_masked),
    )
    write_json(snapshot, is_pretty=args.pretty)
    return EXIT_OK


def run_eval(args: argparse.Namespace) -> int:
    """Evaluate one expression in the target and print the result."""
    evaluation = evaluate_in_target(
        args.pid,
        args.expression,
        timeout_seconds=args.timeout,
        is_masked=args.is_masked,
    )
    write_json(evaluation, is_pretty=args.pretty)
    return EXIT_OK


def run_diff(args: argparse.Namespace) -> int:
    """Sample the target repeatedly and print each delta as it is computed.

    Printing per delta rather than at the end is the point of the command: a
    series that runs until it is interrupted has no end to print at, and a
    leak is something you want to watch grow.
    """
    deltas = iter_snapshot_deltas(
        args.pid,
        interval_seconds=args.interval,
        count=args.count,
        timeout_seconds=args.timeout,
    )
    for delta in deltas:
        write_json(delta, is_pretty=args.pretty)
    return EXIT_OK


def run_doctor(args: argparse.Namespace) -> int:
    """Run the attach preflight checks and print the report."""
    diagnosis = diagnose(args.pid)
    if args.as_json:
        write_json(as_document(diagnosis), is_pretty=args.pretty)
    else:
        sys.stdout.write(render_text(diagnosis))
    return EXIT_OK if diagnosis.is_attachable else EXIT_PROBE_ERROR
