"""Typed exceptions raised by pidprobe.

Every failure mode surfaces as a subclass of :class:`ProbeError` carrying a
message that explains what to do next: attaching to a live process fails for
a handful of well-known, environment-specific reasons, and a bare
``PermissionError`` or ``TimeoutError`` tells the user nothing about which of
them applies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._envelope import ErrorInfo

_TIMEOUT_MESSAGE = (
    "target never reached a safe eval point within {seconds:g}s; it may be "
    "blocked inside a C extension or a long syscall; try an out-of-process "
    "sampler like py-spy, and run `pidprobe doctor {pid}` for attach "
    "diagnostics"
)

_PERMISSION_HINT = (
    "the operating system refused access to the process; on macOS "
    "sys.remote_exec needs root (task_for_pid), so retry with sudo; on Linux "
    "check /proc/sys/kernel/yama/ptrace_scope or grant CAP_SYS_PTRACE"
)
_NO_SUCH_PROCESS_HINT = "no such process; it may have exited already"
_REFUSED_HINT = (
    "the interpreter refused the injection; the target may run a different "
    "Python build than pidprobe, may be older than 3.14, or may have remote "
    "debugging disabled via PYTHON_DISABLE_REMOTE_DEBUG=1 or a "
    "--without-remote-debug build"
)
_GENERIC_HINT = "sys.remote_exec failed"


class ProbeError(Exception):
    """Base class for every error raised by pidprobe."""


class AttachError(ProbeError):
    """Raised when pidprobe cannot inject code into the target process.

    Wraps the low-level failure from :func:`sys.remote_exec` and adds a hint
    about the most likely cause, since the underlying exceptions are
    indistinguishable without knowing the platform.
    """

    def __init__(self, pid: int, hint: str, cause: BaseException | None = None) -> None:
        """Build the error.

        Args:
            pid: Process id pidprobe tried to attach to.
            hint: Human-readable explanation of the likely cause.
            cause: Original exception raised by :func:`sys.remote_exec`.
        """
        detail = f" ({type(cause).__name__}: {cause})" if cause is not None else ""
        super().__init__(f"cannot attach to pid {pid}: {hint}{detail}")
        self.pid = pid
        self.hint = hint

    @classmethod
    def from_cause(cls, pid: int, cause: BaseException) -> AttachError:
        """Create an :class:`AttachError` with a cause-specific hint.

        Args:
            pid: Process id pidprobe tried to attach to.
            cause: Exception raised by :func:`sys.remote_exec`.

        Returns:
            An error whose message names the most likely remedy.
        """
        match cause:
            case PermissionError():
                hint = _PERMISSION_HINT
            case ProcessLookupError():
                hint = _NO_SUCH_PROCESS_HINT
            case RuntimeError() | ValueError():
                hint = _REFUSED_HINT
            case _:
                hint = _GENERIC_HINT
        return cls(pid, hint, cause)


class ProbeTimeoutError(ProbeError):
    """Raised when the target does not answer within the time budget.

    A target only runs injected code at the next bytecode boundary, so a
    process parked in a long syscall or inside a C extension never answers.
    """

    def __init__(self, timeout_seconds: float, pid: int | None = None) -> None:
        """Build the error.

        Args:
            timeout_seconds: Budget that elapsed without an answer.
            pid: Process id of the target, when known.
        """
        super().__init__(
            _TIMEOUT_MESSAGE.format(
                seconds=timeout_seconds,
                pid="<pid>" if pid is None else pid,
            ),
        )
        self.timeout_seconds = timeout_seconds
        self.pid = pid


class ChannelError(ProbeError):
    """Raised when the return channel cannot be set up or returns garbage."""


class TargetError(ProbeError):
    """Raised when the injected code failed as a whole inside the target.

    A single failing collector is reported per section instead, so this error
    means the injected script itself could not produce a payload -- for
    example because the collected data would not serialize.
    """

    def __init__(self, pid: int, error: ErrorInfo) -> None:
        """Build the error.

        Args:
            pid: Process id the snapshot was taken from.
            error: Failure details reported by the target.
        """
        super().__init__(
            f"snapshot failed inside pid {pid}: {error['type']}: {error['message']}",
        )
        self.pid = pid
        self.error = error
