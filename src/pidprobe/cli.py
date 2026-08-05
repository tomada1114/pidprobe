"""Command-line interface for pidprobe.

Every subcommand is registered in :func:`build_parser` and dispatched through
the ``handler`` default it sets, so adding one means adding a parser and a
handler function -- nothing in :func:`main` changes.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any

from ._channel import DEFAULT_TIMEOUT_SECONDS
from ._errors import ProbeError
from ._eval import evaluate_in_target
from ._snapshot import take_snapshot
from .collectors._stacks import build_stacks_collector
from .registry import available_collectors

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from .collectors import Collector


EXIT_OK = 0
"""Exit code for a snapshot that was collected and printed."""

EXIT_PROBE_ERROR = 1
"""Exit code for a probe that failed: attach, timeout, channel or target."""

_PRETTY_INDENT = 2
_COMPACT_SEPARATORS = (",", ":")
_PROGRAM_NAME = "pidprobe"


def _positive_int(value: str) -> int:
    """Parse a strictly positive integer argument.

    Raises:
        ArgumentTypeError: If the value is not a positive integer.
    """
    try:
        number = int(value)
    except ValueError:
        message = f"expected an integer, got {value!r}"
        raise argparse.ArgumentTypeError(message) from None
    if number <= 0:
        message = f"expected a positive integer, got {number}"
        raise argparse.ArgumentTypeError(message)
    return number


def _positive_float(value: str) -> float:
    """Parse a strictly positive float argument.

    Raises:
        ArgumentTypeError: If the value is not a positive number.
    """
    try:
        number = float(value)
    except ValueError:
        message = f"expected a number, got {value!r}"
        raise argparse.ArgumentTypeError(message) from None
    if number <= 0:
        message = f"expected a positive number, got {number}"
        raise argparse.ArgumentTypeError(message)
    return number


def _write_json(document: Mapping[str, Any], *, is_pretty: bool) -> None:
    """Write a JSON document to stdout, compact by default."""
    if is_pretty:
        text = json.dumps(document, indent=_PRETTY_INDENT)
    else:
        text = json.dumps(document, separators=_COMPACT_SEPARATORS)
    sys.stdout.write(f"{text}\n")


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


def _run_snap(args: argparse.Namespace) -> int:
    """Take one snapshot and print it."""
    snapshot = take_snapshot(
        args.pid,
        timeout_seconds=args.timeout,
        collectors=_snap_collectors(is_masked=args.is_masked),
    )
    _write_json(snapshot, is_pretty=args.pretty)
    return EXIT_OK


def _run_eval(args: argparse.Namespace) -> int:
    """Evaluate one expression in the target and print the result."""
    evaluation = evaluate_in_target(
        args.pid,
        args.expression,
        timeout_seconds=args.timeout,
        is_masked=args.is_masked,
    )
    _write_json(evaluation, is_pretty=args.pretty)
    return EXIT_OK


def _add_common_options(parser: argparse.ArgumentParser, *, mask_help: str) -> None:
    """Add the options every probing subcommand shares.

    Args:
        parser: Subcommand parser to extend.
        mask_help: What ``--no-mask`` does in this subcommand, phrased as the
            first clause of its help text.
    """
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="indent the JSON instead of printing it on a single line",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=DEFAULT_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=(
            f"hard budget for the whole probe (default: {DEFAULT_TIMEOUT_SECONDS:g})"
        ),
    )
    parser.add_argument(
        "--no-mask",
        dest="is_masked",
        action="store_false",
        help=(
            f"{mask_help}; masking is on by default and happens inside the "
            "target process"
        ),
    )


def _add_snap_parser(subcommands: argparse._SubParsersAction[Any]) -> None:
    """Register the ``snap`` subcommand."""
    snap = subcommands.add_parser(
        "snap",
        help="collect one snapshot of a running process",
        description=(
            "Inject the built-in collectors, plus any installed collector "
            "plugin, into a running process and print one JSON snapshot of "
            "its threads, objects, GC state and open file descriptors."
        ),
    )
    snap.add_argument("pid", type=_positive_int, help="process id of the target")
    _add_common_options(
        snap,
        mask_help="show credential-like locals instead of masking them",
    )
    snap.set_defaults(handler=_run_snap)


def _add_eval_parser(subcommands: argparse._SubParsersAction[Any]) -> None:
    """Register the ``eval`` subcommand."""
    evaluate = subcommands.add_parser(
        "eval",
        help="evaluate one expression inside a running process",
        description=(
            "Evaluate a Python expression against a copy of the target's "
            "__main__ namespace and print its bounded, credential-masking "
            "repr as JSON. Statements are rejected: an evaluation reads the "
            "target rather than rebinding its names."
        ),
    )
    evaluate.add_argument("pid", type=_positive_int, help="process id of the target")
    evaluate.add_argument(
        "expression",
        metavar="EXPR",
        help="Python expression to evaluate inside the target",
    )
    _add_common_options(
        evaluate,
        mask_help="show a credential-like result instead of masking it",
    )
    evaluate.set_defaults(handler=_run_eval)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the ``pidprobe`` command.

    Returns:
        A parser whose subcommands each set a ``handler`` default taking the
        parsed namespace and returning a process exit code.
    """
    from . import __version__  # noqa: PLC0415 -- avoids a package import cycle

    parser = argparse.ArgumentParser(
        prog=_PROGRAM_NAME,
        description=(
            "Structured JSON snapshots of running CPython 3.14+ processes via PEP 768."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{_PROGRAM_NAME} {__version__}",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    _add_snap_parser(subcommands)
    _add_eval_parser(subcommands)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``pidprobe`` command-line interface.

    Args:
        argv: Argument list to parse; defaults to :data:`sys.argv`.

    Returns:
        The process exit code. Probe failures print their explanation to
        stderr and return :data:`EXIT_PROBE_ERROR`.
    """
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except ProbeError as exc:
        sys.stderr.write(f"{_PROGRAM_NAME}: {exc}\n")
        return EXIT_PROBE_ERROR
