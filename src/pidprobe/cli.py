"""Command-line interface for pidprobe.

Every subcommand is registered in :func:`build_parser` and dispatched through
the ``handler`` default it sets, so adding one means adding a parser here and
a handler in :mod:`pidprobe._commands` -- nothing in :func:`main` changes.
"""

from __future__ import annotations

import argparse
import math
import sys
from typing import TYPE_CHECKING, Any

from ._channel import DEFAULT_TIMEOUT_SECONDS
from ._commands import (
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_PROBE_ERROR,
    run_diff,
    run_doctor,
    run_eval,
    run_snap,
)
from ._errors import ProbeError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_DOCTOR_HINT = "run `pidprobe doctor {pid}` for attach diagnostics"

_PROGRAM_NAME = "pidprobe"

__all__ = [
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_PROBE_ERROR",
    "build_parser",
    "main",
]


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
    """Parse a strictly positive, finite float argument.

    Infinity and NaN are rejected rather than passed on: ``nan`` compares
    false against every bound, so it would slip through as a duration that
    never elapses and never times out.

    Raises:
        ArgumentTypeError: If the value is not a positive, finite number.
    """
    try:
        number = float(value)
    except ValueError:
        message = f"expected a number, got {value!r}"
        raise argparse.ArgumentTypeError(message) from None
    if not math.isfinite(number) or number <= 0:
        message = f"expected a positive number, got {number}"
        raise argparse.ArgumentTypeError(message)
    return number


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    """Add the options every probing subcommand shares."""
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


def _add_mask_option(parser: argparse.ArgumentParser, *, mask_help: str) -> None:
    """Add ``--no-mask`` to a subcommand that reports values from the target.

    Args:
        parser: Subcommand parser to extend.
        mask_help: What ``--no-mask`` does in this subcommand, phrased as the
            first clause of its help text.
    """
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
    _add_common_options(snap)
    _add_mask_option(
        snap,
        mask_help="show credential-like locals instead of masking them",
    )
    snap.set_defaults(handler=run_snap)


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
    _add_common_options(evaluate)
    _add_mask_option(
        evaluate,
        mask_help="show a credential-like result instead of masking it",
    )
    evaluate.set_defaults(handler=run_eval)


def _add_diff_parser(subcommands: argparse._SubParsersAction[Any]) -> None:
    """Register the ``diff`` subcommand."""
    diff = subcommands.add_parser(
        "diff",
        help="report what changed between repeated snapshots of a process",
        description=(
            "Sample a running process every --interval seconds and print only "
            "what moved between two consecutive samples: object counts per "
            "type, ranked by growth, garbage collector statistics and the "
            "number of open file descriptors. Each delta is printed as one "
            "JSON line as soon as it is ready, so a leak can be watched "
            "growing. Only the objects, gc and fds collectors run."
        ),
    )
    diff.add_argument("pid", type=_positive_int, help="process id of the target")
    diff.add_argument(
        "--interval",
        type=_positive_float,
        required=True,
        metavar="SECONDS",
        help="seconds between the starts of two consecutive snapshots",
    )
    diff.add_argument(
        "--count",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "number of snapshots to take, which yields N-1 deltas; omit to "
            "keep sampling until interrupted with Ctrl-C"
        ),
    )
    _add_common_options(diff)
    diff.set_defaults(handler=run_diff)


def _add_doctor_parser(subcommands: argparse._SubParsersAction[Any]) -> None:
    """Register the ``doctor`` subcommand."""
    doctor = subcommands.add_parser(
        "doctor",
        help="explain whether attaching to a process would work",
        description=(
            "Run the attach preflight checks and report each one with its "
            "cause, a command that confirms it and the fix. Nothing is "
            "injected, so this is safe to run against a production process. "
            "Without a PID only the checks describing this environment run."
        ),
    )
    doctor.add_argument(
        "pid",
        nargs="?",
        type=_positive_int,
        default=None,
        help="process id to diagnose; omit to check only this environment",
    )
    doctor.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="print the report as JSON instead of text",
    )
    doctor.add_argument(
        "--pretty",
        action="store_true",
        help="indent the JSON; only meaningful together with --json",
    )
    doctor.set_defaults(handler=run_doctor)


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
    _add_diff_parser(subcommands)
    _add_doctor_parser(subcommands)
    return parser


def _explain(error: ProbeError, pid: int | None) -> str:
    """Render a probe failure, always pointing at ``doctor`` for the details.

    A failed probe is the moment the diagnostics are worth running, so every
    error path names them -- appended here rather than in each message, so no
    new failure mode can be added without the hint.
    """
    if pid is None:
        return str(error)
    hint = _DOCTOR_HINT.format(pid=pid)
    if hint in str(error):
        return str(error)
    return f"{error}; {hint}"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``pidprobe`` command-line interface.

    Args:
        argv: Argument list to parse; defaults to :data:`sys.argv`.

    Returns:
        The process exit code. Probe failures print their explanation to
        stderr and return :data:`EXIT_PROBE_ERROR`; Ctrl-C ends the command
        without a traceback and returns :data:`EXIT_INTERRUPTED`.
    """
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except KeyboardInterrupt:
        sys.stderr.write(f"{_PROGRAM_NAME}: interrupted\n")
        return EXIT_INTERRUPTED
    except ProbeError as exc:
        sys.stderr.write(
            f"{_PROGRAM_NAME}: {_explain(exc, getattr(args, 'pid', None))}\n"
        )
        return EXIT_PROBE_ERROR
