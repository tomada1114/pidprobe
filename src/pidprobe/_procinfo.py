"""Read-only facts about the running platform and about another process.

``pidprobe doctor`` has to explain an attach it deliberately never attempts,
so it needs answers about a *target* -- which interpreter it runs, who owns
it, whether it lives behind a namespace boundary -- without touching it. Every
lookup here is therefore read-only and best-effort: it returns ``None`` when
the platform cannot answer, so a check can report "could not determine"
instead of guessing.

Linux answers through procfs, macOS through ``ps``; nothing here works on
other platforms, which is why the doctor marks those checks as skipped rather
than failing them.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LINUX = "linux"
MACOS = "darwin"

PROC = Path("/proc")
"""Root of the Linux procfs, read at call time so tests can redirect it."""

MINIMUM_TARGET_VERSION = (3, 14)
"""First CPython feature release carrying the PEP 768 debugger interface."""

_PS_EXECUTABLE = "/bin/ps"
_PS_TIMEOUT_SECONDS = 2.0
_VERSION_TIMEOUT_SECONDS = 10.0
_INTERPRETER_NAME_PREFIXES = ("python", "pypy")
_VERSION_SCRIPT = "import sys;print(sys.implementation.name,*sys.version_info[:3])"
_VERSION_FIELDS = 4

NOT_INTROSPECTABLE = (
    "this platform exposes no way to inspect another process; only Linux "
    "(procfs) and macOS (ps) are supported"
)
NO_EXECUTABLE = "the operating system did not report an executable for this pid"
NOT_AN_INTERPRETER = (
    "the process does not run an executable named like a Python interpreter, "
    "so pidprobe did not run it to ask for a version"
)
UNQUERYABLE = "running the target's executable to ask for its version failed"


@dataclass(frozen=True, slots=True)
class Interpreter:
    """What could be learned about the interpreter another process runs.

    Attributes:
        executable: Path of the running binary, when the platform reports one.
        implementation: ``sys.implementation.name`` of that binary.
        version: Its ``sys.version_info[:3]``.
        reason: Why ``version`` is ``None``; ``None`` when it was determined.
    """

    executable: str | None
    implementation: str | None
    version: tuple[int, int, int] | None
    reason: str | None


def current_platform() -> str:
    """Return :data:`sys.platform` through a call, so checks stay mockable.

    Reading it here rather than comparing ``sys.platform`` inline also keeps
    the platform branches type-checkable on both operating systems: mypy
    prunes ``sys.platform == ...`` comparisons for the platform it runs on.
    """
    return sys.platform


def is_linux() -> bool:
    """Whether the prober runs on Linux."""
    return current_platform() == LINUX


def is_macos() -> bool:
    """Whether the prober runs on macOS."""
    return current_platform() == MACOS


def ptrace_scope() -> int | None:
    """Return the Yama ``ptrace_scope`` level.

    Returns:
        The configured level, or ``None`` when the knob does not exist -- a
        kernel without the Yama LSM does not restrict ptrace at all -- or
        holds something that is not a number.
    """
    raw = _read_text(PROC / "sys" / "kernel" / "yama" / "ptrace_scope")
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def process_uid(pid: int) -> int | None:
    """Return the uid owning *pid*, or ``None`` if it cannot be determined."""
    if is_linux():
        try:
            return (PROC / str(pid)).stat().st_uid
        except OSError:
            return None
    if is_macos():
        field = _ps_field(pid, "uid")
        if field is None:
            return None
        try:
            return int(field)
        except ValueError:
            return None
    return None


def pid_namespace(pid: int | str) -> str | None:
    """Return the PID namespace identifier of *pid*, or ``None``.

    Args:
        pid: A process id, or ``"self"`` for the prober's own namespace.

    Returns:
        The ``pid:[4026531836]``-style link target, or ``None`` on any
        platform or kernel that does not expose namespaces.
    """
    try:
        return str((PROC / str(pid) / "ns" / "pid").readlink())
    except OSError:
        return None


def process_environ(pid: int) -> dict[str, str] | None:
    """Return the environment *pid* was started with, or ``None``.

    Only Linux exposes another process' environment without elevated
    privileges. The snapshot is the one taken at ``exec`` time, which is
    exactly the right one for start-up flags like
    ``PYTHON_DISABLE_REMOTE_DEBUG``.
    """
    try:
        raw = (PROC / str(pid) / "environ").read_bytes()
    except OSError:
        return None
    entries: dict[str, str] = {}
    for chunk in raw.split(b"\0"):
        if not chunk:
            continue
        name, _, value = chunk.partition(b"=")
        entries[name.decode(errors="replace")] = value.decode(errors="replace")
    return entries


def process_executable(pid: int) -> str | None:
    """Return the path of the binary *pid* is running, or ``None``."""
    if is_linux():
        try:
            return str((PROC / str(pid) / "exe").readlink())
        except OSError:
            return None
    if is_macos():
        return _ps_field(pid, "comm")
    return None


def target_interpreter(pid: int) -> Interpreter:
    """Describe the interpreter *pid* runs, without attaching to it.

    The version is obtained by running the target's own executable with
    ``-c``, because a process cannot be asked for it from the outside. That
    only happens for a binary whose name looks like an interpreter, so
    pointing the doctor at an arbitrary pid never executes an arbitrary
    program.

    Returns:
        An :class:`Interpreter`; its ``reason`` explains an unknown version.
    """
    executable = process_executable(pid)
    if executable is None:
        reason = NO_EXECUTABLE if is_linux() or is_macos() else NOT_INTROSPECTABLE
        return Interpreter(None, None, None, reason=reason)
    if not _looks_like_interpreter(executable):
        return Interpreter(executable, None, None, reason=NOT_AN_INTERPRETER)
    queried = _query_interpreter(executable)
    if queried is None:
        return Interpreter(executable, None, None, reason=UNQUERYABLE)
    implementation, version = queried
    return Interpreter(executable, implementation, version, reason=None)


def _read_text(path: Path) -> str | None:
    """Read a small text file, returning ``None`` when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _looks_like_interpreter(executable: str) -> bool:
    """Whether a binary's name identifies it as a Python interpreter."""
    return Path(executable).name.lower().startswith(_INTERPRETER_NAME_PREFIXES)


def _ps_field(pid: int, field: str) -> str | None:
    """Read one ``ps`` output field for *pid*, or ``None`` if ps says nothing."""
    return _run(
        [_PS_EXECUTABLE, "-o", f"{field}=", "-p", str(pid)], _PS_TIMEOUT_SECONDS
    )


def _query_interpreter(executable: str) -> tuple[str, tuple[int, int, int]] | None:
    """Ask an interpreter binary for its implementation name and version."""
    output = _run([executable, "-c", _VERSION_SCRIPT], _VERSION_TIMEOUT_SECONDS)
    if output is None:
        return None
    fields = output.split()
    if len(fields) != _VERSION_FIELDS:
        return None
    try:
        numbers = [int(field) for field in fields[1:]]
    except ValueError:
        return None
    return fields[0], (numbers[0], numbers[1], numbers[2])


def _run(argv: list[str], timeout_seconds: float) -> str | None:
    """Run a command and return its stdout, or ``None`` if it did not succeed."""
    try:
        completed = subprocess.run(  # noqa: S603 -- argv built here; no shell
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None
