"""How each ``pidprobe`` subcommand is spelled on the command line.

This module owns the arguments, their validation and their help text; what a
subcommand then does lives in :mod:`pidprobe._commands`, and the entry point
that ties both to a process exit code lives in :mod:`pidprobe.cli`. Adding a
subcommand therefore means one parser here and one handler there.
"""

from __future__ import annotations

import argparse
import math
from typing import TYPE_CHECKING, Any

from ._channel import DEFAULT_TIMEOUT_SECONDS
from ._commands import run_diff, run_doctor, run_eval, run_snap

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["add_subcommands", "positive_float"]


def positive_int(value: str) -> int:
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


def positive_float(value: str) -> float:
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
    """Add the options every probing subcommand shares.

    ``--timeout`` starts at ``None`` rather than at the default budget so it
    can be told apart from "not given": the same option exists globally, and
    a default written here would silently erase a value given before the
    subcommand.
    """
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="indent the JSON instead of printing it on a single line",
    )
    parser.add_argument(
        "--timeout",
        type=positive_float,
        default=None,
        metavar="SECONDS",
        help=(
            "hard budget for the whole probe, overriding a global --timeout "
            f"(default: {DEFAULT_TIMEOUT_SECONDS:g})"
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
            "Inject the built-in stacks, objects, gc and fds collectors, plus "
            "every installed collector plugin, into a running process and "
            "print one JSON snapshot. Each plugin adds its own top-level "
            "section; `pidprobe doctor` lists the collectors that will run."
        ),
    )
    snap.add_argument("pid", type=positive_int, help="process id of the target")
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
    evaluate.add_argument("pid", type=positive_int, help="process id of the target")
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
    diff.add_argument("pid", type=positive_int, help="process id of the target")
    diff.add_argument(
        "--interval",
        type=positive_float,
        required=True,
        metavar="SECONDS",
        help="seconds between the starts of two consecutive snapshots",
    )
    diff.add_argument(
        "--count",
        type=positive_int,
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
            "injected, so this is safe to run against a production process, "
            "and no time budget applies. Without a PID only the checks "
            "describing this environment run."
        ),
    )
    doctor.add_argument(
        "pid",
        nargs="?",
        type=positive_int,
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


_SUBCOMMANDS: tuple[Callable[[argparse._SubParsersAction[Any]], None], ...] = (
    _add_snap_parser,
    _add_eval_parser,
    _add_diff_parser,
    _add_doctor_parser,
)


def add_subcommands(parser: argparse.ArgumentParser) -> None:
    """Register every subcommand on *parser*, each with its own handler."""
    subcommands = parser.add_subparsers(dest="command", required=True)
    for register in _SUBCOMMANDS:
        register(subcommands)
