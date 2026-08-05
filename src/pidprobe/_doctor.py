"""The preflight run behind ``pidprobe doctor``.

Attaching to a live process fails for a handful of environment-specific
reasons that all surface as the same opaque ``PermissionError`` or
``RuntimeError``. Each check isolates one of them and answers it *before*
anything is injected: nothing here attaches to the target, so the report is
safe to run against a production process.

This module owns :func:`diagnose` and the checks that describe the prober
itself -- whether its interpreter can inject at all, whether the target would
have somewhere to answer, and whether every installed collector plugin
loaded. The checks about the operating system's attach policy live in
:mod:`pidprobe._attach_policy` and the ones about the target's interpreter in
:mod:`pidprobe._target_python`.
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING

from ._attach_policy import (
    check_pid_namespace,
    check_ptrace_scope,
    check_target_owner,
    check_target_process,
    check_task_for_pid,
)
from ._channel import TempFileChannel, UnixSocketChannel
from ._diagnosis import Check, CheckStatus, Diagnosis
from ._target_python import check_target_python, check_target_remote_debug
from .registry import available_collectors

if TYPE_CHECKING:
    from collections.abc import Iterator

_DISABLE_VARIABLE = "PYTHON_DISABLE_REMOTE_DEBUG"
_REGISTRY_LOGGER = "pidprobe.registry"


def diagnose(pid: int | None = None) -> Diagnosis:
    """Run the attach preflight checks and report what they found.

    Args:
        pid: Target to examine. Omit it to run only the checks that describe
            the prober's own environment, which is the useful thing to do
            before there is a process to point at.

    Returns:
        The :class:`~pidprobe._diagnosis.Diagnosis`; it is ``is_attachable``
        when nothing found would stop an injection.
    """
    checks = [
        _check_prober_remote_debug(),
        _check_return_channel(),
        _check_collector_plugins(),
        check_ptrace_scope(),
        check_task_for_pid(),
    ]
    if pid is not None:
        checks += [
            check_target_process(pid),
            check_target_owner(pid),
            check_pid_namespace(pid),
            *check_target_python(pid),
            check_target_remote_debug(pid),
        ]
    return Diagnosis(pid=pid, checks=tuple(checks))


def _check_prober_remote_debug() -> Check:
    """Check that the interpreter running pidprobe can inject at all."""
    executable = sys.executable or "python3"
    confirm = f'{executable} -c "import sys; print(sys.is_remote_debug_enabled())"'
    if getattr(sys, "remote_exec", None) is None:
        return Check(
            name="prober_remote_debug",
            status=CheckStatus.FAIL,
            summary="this interpreter has no sys.remote_exec",
            cause=(
                "pidprobe injects through PEP 768, which this build does not "
                "provide: it predates CPython 3.14 or was configured "
                "--without-remote-debug"
            ),
            confirm=confirm,
            fix=(
                "install pidprobe under a CPython 3.14+ build with remote "
                "debugging compiled in, such as a python.org or uv-managed one"
            ),
        )
    if not sys.is_remote_debug_enabled():
        return Check(
            name="prober_remote_debug",
            status=CheckStatus.FAIL,
            summary=f"remote debugging is switched off in {executable}",
            cause=(
                f"{_DISABLE_VARIABLE} is set in pidprobe's own environment, and "
                "CPython then refuses sys.remote_exec with 'Remote debugging "
                "is not enabled' before it ever looks at the target"
            ),
            confirm=f"env | grep {_DISABLE_VARIABLE}",
            fix=f"unset {_DISABLE_VARIABLE} before running pidprobe",
        )
    return Check(
        name="prober_remote_debug",
        status=CheckStatus.OK,
        summary=(
            f"pidprobe runs {sys.implementation.name} "
            f"{sys.version.split()[0]} with remote debugging enabled"
        ),
    )


def _check_return_channel() -> Check:
    """Check that the target would have somewhere to write its answer."""
    try:
        channel = UnixSocketChannel()
    except OSError as exc:
        return _tempfile_channel_check(f"{type(exc).__name__}: {exc}")
    path = channel.path
    channel.close()
    return Check(
        name="return_channel",
        status=CheckStatus.OK,
        summary=f"an AF_UNIX return channel binds at {path}",
    )


def _tempfile_channel_check(socket_error: str) -> Check:
    """Report on the fallback channel after no socket could be bound."""
    try:
        channel = TempFileChannel()
    except OSError as exc:
        return Check(
            name="return_channel",
            status=CheckStatus.FAIL,
            summary="no return channel can be created here",
            cause=(
                f"binding an AF_UNIX socket failed ({socket_error}) and so did "
                f"creating a temporary directory ({type(exc).__name__}: {exc}); "
                "the injected script would have nowhere to send its result"
            ),
            confirm='python3 -c "import tempfile; print(tempfile.mkdtemp())"',
            fix=(
                "point TMPDIR at a directory both pidprobe and the target may "
                "write to and read from"
            ),
        )
    path = channel.path
    channel.close()
    return Check(
        name="return_channel",
        status=CheckStatus.WARN,
        summary=f"falling back to the temporary-file return channel at {path}",
        cause=(
            f"no AF_UNIX socket could be bound ({socket_error}); a sandbox, a "
            "filesystem without socket support, or a TMPDIR too long for the "
            "104-byte sun_path limit all look like this"
        ),
        confirm='python3 -c "import tempfile; print(tempfile.gettempdir())"',
        fix=(
            "probing still works over the file fallback, only by polling; set "
            "TMPDIR to a short, writable path to get the socket back"
        ),
    )


def _check_collector_plugins() -> Check:
    """Check that every installed collector plugin actually loaded."""
    with _captured_registry_warnings() as messages:
        collectors = available_collectors()
    names = ", ".join(collector.name for collector in collectors)
    if not messages:
        return Check(
            name="collector_plugins",
            status=CheckStatus.OK,
            summary=f"{len(collectors)} collectors will run: {names}",
        )
    return Check(
        name="collector_plugins",
        status=CheckStatus.WARN,
        summary=(
            f"{len(messages)} collector plugin(s) were skipped; "
            f"{len(collectors)} will run: {names}"
        ),
        cause="; ".join(messages),
        confirm=(
            'python3 -c "import logging; logging.basicConfig(); '
            'from pidprobe import available_collectors; available_collectors()"'
        ),
        fix=(
            "reinstall or uninstall the package publishing the failing "
            "pidprobe.collectors entry point; a skipped plugin costs only its "
            "own snapshot section, so every other section still comes back"
        ),
    )


@contextmanager
def _captured_registry_warnings() -> Iterator[list[str]]:
    """Collect what plugin discovery logs, instead of letting it print.

    Discovery reports a broken plugin on the ``pidprobe.registry`` logger and
    carries on, which is right for a snapshot and useless for a diagnosis --
    so the doctor listens in and turns those records into a check.
    """
    logger = logging.getLogger(_REGISTRY_LOGGER)
    messages: list[str] = []
    handler = _RecordingHandler(messages)
    level, propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.propagate = False
    if logger.getEffectiveLevel() > logging.WARNING:
        logger.setLevel(logging.WARNING)
    try:
        yield messages
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
        logger.propagate = propagate


class _RecordingHandler(logging.Handler):
    """Logging handler that appends formatted messages to a caller's list."""

    def __init__(self, messages: list[str]) -> None:
        """Record every warning or worse into *messages*."""
        super().__init__(level=logging.WARNING)
        self._messages = messages

    def emit(self, record: logging.LogRecord) -> None:
        """Append one record's message."""
        self._messages.append(record.getMessage())
