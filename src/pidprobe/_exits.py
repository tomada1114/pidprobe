"""Exit codes the ``pidprobe`` command returns, and how failures map onto them.

A probe fails for reasons a script can act on differently -- a vanished pid is
worth retrying, a refused attach needs the operator, a timeout usually means a
different tool -- so each of those gets its own code rather than a shared
``1``. The mapping lives here, next to the table that documents it, so the
codes, their meanings and the ``--help`` epilog can never drift apart.

The numbers are part of the command's contract: they may gain new members, but
an existing code keeps its meaning.
"""

from __future__ import annotations

from ._errors import (
    AttachError,
    NoSuchProcessError,
    ProbeError,
    ProbeTimeoutError,
    TargetError,
)

EXIT_OK = 0
"""The command ran and printed its output."""

EXIT_PROBE_ERROR = 1
"""A probe failed for a reason with no more specific code of its own.

:class:`~pidprobe.ChannelError` lands here, as does any future
:class:`~pidprobe.ProbeError` subclass, so an unmapped failure degrades to
"the probe failed" instead of to "pidprobe has a bug".
"""

EXIT_USAGE = 2
"""The command line itself was wrong; argparse chooses this one, not pidprobe."""

EXIT_TARGET_NOT_FOUND = 3
"""No process with that pid exists, or it exited before pidprobe attached."""

EXIT_ATTACH_FAILED = 4
"""The process exists but pidprobe was not allowed, or able, to attach to it."""

EXIT_TIMEOUT = 5
"""The target never answered within the time budget."""

EXIT_TARGET_ERROR = 6
"""The code pidprobe injected raised inside the target.

For ``eval`` this is the common case of a wrong expression -- a ``NameError``
or a ``SyntaxError`` -- which says nothing about whether attaching works.
"""

EXIT_DIAGNOSIS_FAILED = 7
"""``doctor`` ran fine and found a check that blocks attaching.

Distinct from :data:`EXIT_PROBE_ERROR` because the diagnosis itself succeeded:
a preflight script wants to tell "attaching would not work" apart from "the
diagnostics could not be produced".
"""

EXIT_INTERNAL_ERROR = 70
"""pidprobe hit an error it does not model -- a bug in pidprobe.

70 is ``EX_SOFTWARE`` from ``sysexits.h``, kept well clear of the small codes
so an internal error can never be mistaken for a diagnosed failure.
"""

EXIT_INTERRUPTED = 130
"""Ctrl-C ended the command, by the 128 + SIGINT convention.

``diff`` without ``--count`` is meant to be ended this way, so this is a
normal way to finish rather than a failure.
"""

EXIT_BROKEN_PIPE = 141
"""The reader of stdout went away, by the 128 + SIGPIPE convention.

``pidprobe snap PID | head -1`` is an ordinary thing to type, and it must not
end in a traceback.
"""

EXIT_CODE_TABLE: tuple[tuple[int, str], ...] = (
    (EXIT_OK, "success"),
    (EXIT_PROBE_ERROR, "probe failed for a reason with no more specific code"),
    (EXIT_USAGE, "invalid command line"),
    (EXIT_TARGET_NOT_FOUND, "no such process"),
    (EXIT_ATTACH_FAILED, "attaching to the target was refused"),
    (EXIT_TIMEOUT, "the target did not answer within the timeout"),
    (EXIT_TARGET_ERROR, "the injected code raised inside the target"),
    (EXIT_DIAGNOSIS_FAILED, "doctor found a check that blocks attaching"),
    (EXIT_INTERNAL_ERROR, "pidprobe hit an unexpected error (a bug)"),
    (EXIT_INTERRUPTED, "interrupted with Ctrl-C"),
    (EXIT_BROKEN_PIPE, "the reader of stdout closed the pipe"),
)
"""Every exit code with the one-line meaning ``--help`` and the docs print."""

DOCTOR_HINT = "run `pidprobe doctor {pid}` for attach diagnostics"

_CODE_COLUMN_WIDTH = 3

__all__ = [
    "DOCTOR_HINT",
    "EXIT_ATTACH_FAILED",
    "EXIT_BROKEN_PIPE",
    "EXIT_CODE_TABLE",
    "EXIT_DIAGNOSIS_FAILED",
    "EXIT_INTERNAL_ERROR",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_PROBE_ERROR",
    "EXIT_TARGET_ERROR",
    "EXIT_TARGET_NOT_FOUND",
    "EXIT_TIMEOUT",
    "EXIT_USAGE",
    "exit_code_for",
    "format_exit_codes",
]


def exit_code_for(error: ProbeError) -> int:
    """Return the exit code that reports *error*.

    Args:
        error: Failure raised by a probe.

    Returns:
        One of the ``EXIT_*`` codes; :data:`EXIT_PROBE_ERROR` for any failure
        this table does not name, so a new error type is never reported as an
        internal one.
    """
    match error:
        case NoSuchProcessError():
            return EXIT_TARGET_NOT_FOUND
        case AttachError():
            return EXIT_ATTACH_FAILED
        case ProbeTimeoutError():
            return EXIT_TIMEOUT
        case TargetError():
            return EXIT_TARGET_ERROR
        case _:
            return EXIT_PROBE_ERROR


def describe(error: ProbeError, pid: int | None) -> tuple[int, str]:
    """Render *error* as the exit code and the stderr line that report it.

    A failed probe is the moment the diagnostics are worth running, so every
    message that names a pid points at ``doctor`` -- appended here rather than
    written into each message, so no new failure mode can be added without the
    hint, and de-duplicated against the messages that already carry it.

    Args:
        error: Failure raised by a probe.
        pid: Target the failing command was pointed at, when naming it in the
            hint makes sense; ``None`` suppresses the hint.

    Returns:
        The exit code and the message to write to stderr, without a newline
        and without the program name.
    """
    code = exit_code_for(error)
    if pid is None:
        return code, str(error)
    hint = DOCTOR_HINT.format(pid=pid)
    if hint in str(error):
        return code, str(error)
    return code, f"{error}; {hint}"


def format_exit_codes() -> str:
    """Render :data:`EXIT_CODE_TABLE` as the ``--help`` epilog."""
    rows = "\n".join(
        f"  {code:>{_CODE_COLUMN_WIDTH}}  {meaning}"
        for code, meaning in EXIT_CODE_TABLE
    )
    return f"exit codes:\n{rows}\n"
