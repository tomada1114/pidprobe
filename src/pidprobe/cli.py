"""Command-line entry point for pidprobe.

:func:`main` is the only place that turns a failure into an exit code and a
line on stderr, and it dispatches through the ``handler`` default each
subcommand sets, so adding one means a parser in :mod:`pidprobe._parsers` and
a handler in :mod:`pidprobe._commands` -- nothing here changes.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from typing import TYPE_CHECKING

from ._channel import DEFAULT_TIMEOUT_SECONDS
from ._errors import ProbeError
from ._exits import (
    EXIT_ATTACH_FAILED,
    EXIT_BROKEN_PIPE,
    EXIT_DIAGNOSIS_FAILED,
    EXIT_INTERNAL_ERROR,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_PROBE_ERROR,
    EXIT_TARGET_ERROR,
    EXIT_TARGET_NOT_FOUND,
    EXIT_TIMEOUT,
    EXIT_USAGE,
    describe,
    format_exit_codes,
)
from ._parsers import add_subcommands, positive_float

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_PROGRAM_NAME = "pidprobe"

_DEBUG_ENV_VAR = "PIDPROBE_DEBUG"

_HINTING_COMMANDS = frozenset({"snap", "eval", "diff"})
"""Subcommands whose failures are worth answering with ``doctor``.

``doctor`` is left out: telling someone whose ``doctor`` run failed to run
``doctor`` is not advice.
"""

_BUG_REPORT_HINT = (
    "this is a bug in pidprobe; re-run with --debug (or PIDPROBE_DEBUG=1) for "
    "the traceback and report it at https://github.com/tomada1114/pidprobe/issues"
)

__all__ = [
    "EXIT_ATTACH_FAILED",
    "EXIT_BROKEN_PIPE",
    "EXIT_DIAGNOSIS_FAILED",
    "EXIT_INTERNAL_ERROR",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_PROBE_ERROR",
    "EXIT_TARGET_ERROR",
    "EXIT_TARGET_NOT_FOUND",
    "EXIT_TIMEOUT",
    "EXIT_USAGE",
    "build_parser",
    "main",
]


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
        epilog=format_exit_codes(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{_PROGRAM_NAME} {__version__}",
    )
    parser.add_argument(
        "--timeout",
        dest="global_timeout",
        type=positive_float,
        default=None,
        metavar="SECONDS",
        help=(
            "hard budget for the whole probe, overriding the default of "
            f"{DEFAULT_TIMEOUT_SECONDS:g}; the same option after the "
            "subcommand wins over this one, and doctor ignores both because "
            "it never attaches"
        ),
    )
    parser.add_argument(
        "--debug",
        dest="is_debug",
        action="store_true",
        help=(
            "re-raise an unexpected error instead of summarising it, so the "
            f"traceback is printed; {_DEBUG_ENV_VAR}=1 does the same"
        ),
    )
    add_subcommands(parser)
    return parser


def _resolve_timeout(args: argparse.Namespace) -> None:
    """Fold the global and per-subcommand ``--timeout`` into ``args.timeout``.

    Both parsers write into the namespace, and the subcommand's default would
    otherwise erase whatever was given before it, so both start at ``None``
    and the more specific one is chosen here.
    """
    timeout = getattr(args, "timeout", None)
    if timeout is None:
        timeout = args.global_timeout
    args.timeout = DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout


def _is_debug(args: argparse.Namespace) -> bool:
    """Whether an unexpected error should surface as its own traceback."""
    return bool(args.is_debug) or os.environ.get(_DEBUG_ENV_VAR, "") not in {"", "0"}


def _abandon_stdout() -> None:
    """Point stdout at the void after its reader went away.

    Without this the interpreter flushes the buffer again on the way out and
    prints its own ``BrokenPipeError`` after pidprobe has already handled it.
    """
    with contextlib.suppress(OSError, ValueError):
        # The real descriptor is looked up first: when stdout is not backed by
        # one there is nothing to redirect, and opening devnull would have
        # leaked it.
        target = sys.stdout.fileno()
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, target)
        finally:
            os.close(devnull)


def _fail(message: str) -> None:
    """Write one prefixed failure line to stderr."""
    sys.stderr.write(f"{_PROGRAM_NAME}: {message}\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``pidprobe`` command-line interface.

    Every expected failure is reported as a single ``pidprobe: ...`` line on
    stderr and one of the documented exit codes; a traceback only ever
    escapes under ``--debug``.

    Args:
        argv: Argument list to parse; defaults to :data:`sys.argv`.

    Returns:
        The process exit code; see :mod:`pidprobe._exits` for the table.
    """
    args = build_parser().parse_args(argv)
    _resolve_timeout(args)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        code = handler(args)
        # Flushed here rather than left to interpreter shutdown, so a reader
        # that went away is reported by the handler below instead of by the
        # interpreter's own "Exception ignored" notice after main returned.
        sys.stdout.flush()
    except KeyboardInterrupt:
        _fail("interrupted")
        return EXIT_INTERRUPTED
    except BrokenPipeError:
        _abandon_stdout()
        return EXIT_BROKEN_PIPE
    except ProbeError as exc:
        pid = getattr(args, "pid", None) if args.command in _HINTING_COMMANDS else None
        code, message = describe(exc, pid)
        _fail(message)
        return code
    except Exception as exc:
        if _is_debug(args):
            raise
        _fail(f"internal error: {type(exc).__name__}: {exc}")
        _fail(_BUG_REPORT_HINT)
        return EXIT_INTERNAL_ERROR
    return code
