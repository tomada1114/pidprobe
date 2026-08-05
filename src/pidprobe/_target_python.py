"""Checks on whether the target's interpreter can be injected into.

Even when the kernel allows the attach, ``sys.remote_exec`` still needs the
target to be a CPython that carries the PEP 768 debugger interface, built from
the same feature release as the prober, and started without the environment
variable that switches the interface off. None of that is visible from the
error the attach would raise, so the doctor establishes it beforehand.
"""

from __future__ import annotations

import sys

from ._diagnosis import Check, CheckStatus
from ._procinfo import (
    MINIMUM_TARGET_VERSION,
    NOT_INTROSPECTABLE,
    Interpreter,
    is_linux,
    process_environ,
    target_interpreter,
)

_CPYTHON = "cpython"
_DISABLE_VARIABLE = "PYTHON_DISABLE_REMOTE_DEBUG"
_FALLBACK_EXECUTABLE = "python3"


def check_target_python(pid: int) -> list[Check]:
    """Check the target's Python: new enough, and matching the prober's.

    Returns:
        Two checks -- the target's own version and the match with the prober
        -- or a single warning when the version could not be established at
        all, since neither question can then be answered.
    """
    interpreter = target_interpreter(pid)
    version = interpreter.version
    if version is None:
        return [_unknown_target_python(pid, interpreter)]
    return [
        _check_version(interpreter, version),
        _check_matches_prober(interpreter, version),
    ]


def check_target_remote_debug(pid: int) -> Check:
    """Check that the target was not started with remote debugging off."""
    read_environ = f"tr '\\0' '\\n' < /proc/{pid}/environ | grep {_DISABLE_VARIABLE}"
    if not is_linux():
        return Check(
            name="target_remote_debug",
            status=CheckStatus.SKIPPED,
            summary="another process' environment is not readable on this platform",
            confirm=f"sudo ps -wwEp {pid}",
        )
    environ = process_environ(pid)
    if environ is None:
        return Check(
            name="target_remote_debug",
            status=CheckStatus.SKIPPED,
            summary=f"could not read the environment pid {pid} was started with",
            confirm=f"sudo {read_environ}",
        )
    if _DISABLE_VARIABLE not in environ:
        return Check(
            name="target_remote_debug",
            status=CheckStatus.OK,
            summary=f"{_DISABLE_VARIABLE} is not set in pid {pid}",
        )
    return Check(
        name="target_remote_debug",
        status=CheckStatus.FAIL,
        summary=(
            f"pid {pid} was started with "
            f"{_DISABLE_VARIABLE}={environ[_DISABLE_VARIABLE]!r}"
        ),
        cause=(
            f"CPython turns the debugger interface off when {_DISABLE_VARIABLE} "
            "is present at start-up, whatever it is set to -- the empty string "
            "counts -- so the target has nothing for pidprobe to write to"
        ),
        confirm=read_environ,
        fix=(
            f"restart the target without {_DISABLE_VARIABLE} in its "
            "environment; it is read once at interpreter start-up and cannot "
            "be cleared from outside"
        ),
    )


def _unknown_target_python(pid: int, interpreter: Interpreter) -> Check:
    """Report that the target's interpreter could not be identified."""
    major, minor = sys.version_info[:2]
    return Check(
        name="target_python",
        status=CheckStatus.WARN,
        summary=f"could not determine which Python pid {pid} runs",
        cause=interpreter.reason or NOT_INTROSPECTABLE,
        confirm=f"ps -o comm= -p {pid}",
        fix=(
            f"check by hand that the target runs CPython {major}.{minor}: "
            "pidprobe needs the same feature release on both sides"
        ),
    )


def _check_version(interpreter: Interpreter, version: tuple[int, int, int]) -> Check:
    """Check that the target runs a CPython carrying the PEP 768 interface."""
    executable = interpreter.executable or _FALLBACK_EXECUTABLE
    implementation = interpreter.implementation or "an unknown implementation"
    rendered = _rendered(version)
    minimum = _rendered(MINIMUM_TARGET_VERSION)
    confirm = f"{executable} -VV"
    if implementation != _CPYTHON:
        return Check(
            name="target_python_version",
            status=CheckStatus.FAIL,
            summary=f"the target runs {implementation} {rendered}, not CPython",
            cause=(
                "PEP 768 is a CPython feature; no other implementation exposes "
                "the debugger interface sys.remote_exec writes to"
            ),
            confirm=confirm,
            fix=(
                f"probe {implementation} with a sampler built for it; pidprobe "
                "cannot attach to it"
            ),
        )
    if version[:2] < MINIMUM_TARGET_VERSION:
        return Check(
            name="target_python_version",
            status=CheckStatus.FAIL,
            summary=f"the target runs CPython {rendered}, older than {minimum}",
            cause=(
                f"the PEP 768 debugger interface first shipped in CPython "
                f"{minimum}; an older target has nothing for sys.remote_exec to "
                "write to"
            ),
            confirm=confirm,
            fix=(
                f"restart the target on CPython {minimum} or newer, or use an "
                "out-of-process sampler such as py-spy instead"
            ),
        )
    return Check(
        name="target_python_version",
        status=CheckStatus.OK,
        summary=f"the target runs CPython {rendered}",
    )


def _check_matches_prober(
    interpreter: Interpreter,
    version: tuple[int, int, int],
) -> Check:
    """Check that target and prober share a CPython feature release."""
    theirs, mine = _rendered(version[:2]), _rendered(sys.version_info[:2])
    if theirs == mine:
        return Check(
            name="target_python_match",
            status=CheckStatus.OK,
            summary=f"target and prober both run Python {mine}",
        )
    executable = interpreter.executable or _FALLBACK_EXECUTABLE
    return Check(
        name="target_python_match",
        status=CheckStatus.FAIL,
        summary=f"the target runs Python {theirs}, pidprobe runs {mine}",
        cause=(
            "sys.remote_exec reaches the debugger interface through offsets "
            "that are only stable within one CPython feature release, so it "
            "refuses a target built from a different one"
        ),
        confirm=f"{executable} -VV; {sys.executable or _FALLBACK_EXECUTABLE} -VV",
        fix=(
            f"run pidprobe from a {theirs} interpreter, for example with "
            f"`uv tool install --python {theirs} pidprobe` or "
            f"`{executable} -m pip install pidprobe`"
        ),
    )


def _rendered(version: tuple[int, ...]) -> str:
    """Join a version tuple the way an interpreter prints it."""
    return ".".join(str(part) for part in version)
