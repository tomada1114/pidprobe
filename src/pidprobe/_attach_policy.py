"""Checks on whether the operating system lets one process reach another.

Every check here answers the same question from a different angle: before any
Python-level concern, does the kernel permit this prober to write into that
target? Linux answers with the Yama LSM and PID namespaces, macOS with the
task-port policy, and both with plain process ownership. Nothing here
attaches; each check reads policy and reports it.
"""

from __future__ import annotations

import os

from ._diagnosis import Check, CheckStatus
from ._procinfo import is_linux, is_macos, pid_namespace, process_uid, ptrace_scope

_UNRESTRICTED_SCOPE = 0
_LOCKED_SCOPE = 3
_PTRACE_CONFIRM = "cat /proc/sys/kernel/yama/ptrace_scope"
_PTRACE_ADVICE: dict[int, tuple[CheckStatus, str, str]] = {
    1: (
        CheckStatus.WARN,
        "PEP 768 writes into the target with process_vm_writev, which needs "
        "PTRACE_MODE_ATTACH; at scope 1 only a direct ancestor of the target, "
        "or a process holding CAP_SYS_PTRACE, is granted it",
        "run pidprobe as root, give its interpreter the capability with "
        "`sudo setcap cap_sys_ptrace+ep $(readlink -f $(command -v python3))`, "
        "or relax the knob with `sudo sysctl -w kernel.yama.ptrace_scope=0`",
    ),
    2: (
        CheckStatus.FAIL,
        "at scope 2 only a process holding CAP_SYS_PTRACE may attach to "
        "anything, so the PTRACE_MODE_ATTACH check PEP 768 needs is refused",
        "run pidprobe as root or with CAP_SYS_PTRACE, or relax the knob with "
        "`sudo sysctl -w kernel.yama.ptrace_scope=0`",
    ),
    3: (
        CheckStatus.FAIL,
        "at scope 3 attaching is disabled for every process, and the value "
        "cannot be lowered again while the machine is running",
        "set `kernel.yama.ptrace_scope` to 0 or 1 in /etc/sysctl.d/ and "
        "reboot; nothing can attach until then",
    ),
}


def check_ptrace_scope() -> Check:
    """Check the Yama LSM policy that governs attaching on Linux."""
    if not is_linux():
        return Check(
            name="ptrace_scope",
            status=CheckStatus.SKIPPED,
            summary="the Yama ptrace_scope policy only exists on Linux",
        )
    scope = ptrace_scope()
    if scope is None:
        return Check(
            name="ptrace_scope",
            status=CheckStatus.OK,
            summary="no Yama ptrace_scope knob: this kernel does not restrict attaching",
        )
    if scope <= _UNRESTRICTED_SCOPE:
        return Check(
            name="ptrace_scope",
            status=CheckStatus.OK,
            summary=f"kernel.yama.ptrace_scope is {scope}: attaching is unrestricted",
        )
    status, cause, fix = _PTRACE_ADVICE.get(scope, _PTRACE_ADVICE[_LOCKED_SCOPE])
    return Check(
        name="ptrace_scope",
        status=status,
        summary=f"kernel.yama.ptrace_scope is {scope}",
        cause=cause,
        confirm=_PTRACE_CONFIRM,
        fix=fix,
    )


def check_task_for_pid() -> Check:
    """Check the macOS task-port policy that governs attaching."""
    if not is_macos():
        return Check(
            name="task_for_pid",
            status=CheckStatus.SKIPPED,
            summary="task_for_pid access is a macOS restriction",
        )
    euid = os.geteuid()
    if euid == 0:
        return Check(
            name="task_for_pid",
            status=CheckStatus.OK,
            summary="pidprobe runs as root, which macOS grants task_for_pid",
        )
    return Check(
        name="task_for_pid",
        status=CheckStatus.FAIL,
        summary=f"pidprobe runs as uid {euid}, which macOS denies task_for_pid",
        cause=(
            "sys.remote_exec must take the target's task port, and macOS hands "
            "that out only to root or to a binary carrying the "
            "com.apple.system-task-ports entitlement; without it the attach "
            "fails with 'Cannot get task port'"
        ),
        confirm="id -u",
        fix=(
            "rerun the probe under sudo, e.g. `sudo pidprobe snap <PID>`; the "
            "entitlement route needs a signed, entitled interpreter and is not "
            "something pidprobe can grant itself"
        ),
    )


def check_target_process(pid: int) -> Check:
    """Check that the pid names a process this user can reach at all."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return Check(
            name="target_process",
            status=CheckStatus.FAIL,
            summary=f"no process with pid {pid} exists",
            cause=(
                "the pid is not in use; a target that already exited and a pid "
                "that only means something inside a container both look like this"
            ),
            confirm=f"ps -p {pid}",
            fix="find the running target with `pgrep -af python` and pass its pid",
        )
    except OSError as exc:
        return Check(
            name="target_process",
            status=CheckStatus.FAIL,
            summary=f"pid {pid} exists but this user may not signal it",
            cause=(
                f"a bare signal 0 was refused ({type(exc).__name__}: {exc}), and "
                "writing into the process is strictly more privileged than that"
            ),
            confirm=f"ps -o user=,pid= -p {pid}",
            fix="rerun pidprobe as the user owning the target, or as root",
        )
    return Check(
        name="target_process",
        status=CheckStatus.OK,
        summary=f"pid {pid} is running and this user may signal it",
    )


def check_target_owner(pid: int) -> Check:
    """Check that prober and target run as the same user."""
    uid = process_uid(pid)
    euid = os.geteuid()
    if uid is None:
        return Check(
            name="target_owner",
            status=CheckStatus.SKIPPED,
            summary=f"could not determine which user owns pid {pid}",
            confirm=f"ps -o user=,uid= -p {pid}",
        )
    if uid == euid:
        return Check(
            name="target_owner",
            status=CheckStatus.OK,
            summary=f"pid {pid} and pidprobe both run as uid {uid}",
        )
    if euid == 0:
        return Check(
            name="target_owner",
            status=CheckStatus.OK,
            summary=f"pid {pid} runs as uid {uid}; pidprobe runs as root",
        )
    return Check(
        name="target_owner",
        status=CheckStatus.FAIL,
        summary=f"pid {pid} runs as uid {uid}, pidprobe as uid {euid}",
        cause=(
            "attaching writes into another process' memory, which the kernel "
            "permits only within one user unless the prober is privileged"
        ),
        confirm=f"ps -o user=,uid= -p {pid}; id -u",
        fix=f"rerun as that user (`sudo -u '#{uid}' pidprobe snap {pid}`) or as root",
    )


def check_pid_namespace(pid: int) -> Check:
    """Check that no container boundary sits between prober and target."""
    if not is_linux():
        return Check(
            name="pid_namespace",
            status=CheckStatus.SKIPPED,
            summary=(
                "PID namespaces are a Linux concept; a container on this host "
                "runs its own kernel and cannot be reached by pid at all"
            ),
        )
    confirm = f"readlink /proc/self/ns/pid /proc/{pid}/ns/pid"
    own, theirs = pid_namespace("self"), pid_namespace(pid)
    if own is None or theirs is None:
        return Check(
            name="pid_namespace",
            status=CheckStatus.SKIPPED,
            summary=f"could not read the PID namespace of pidprobe or of pid {pid}",
            confirm=confirm,
        )
    if own == theirs:
        return Check(
            name="pid_namespace",
            status=CheckStatus.OK,
            summary=f"pidprobe and pid {pid} share PID namespace {own}",
        )
    return Check(
        name="pid_namespace",
        status=CheckStatus.FAIL,
        summary=f"pid {pid} is in PID namespace {theirs}, pidprobe in {own}",
        cause=(
            "the same number names different processes on the two sides of a "
            "namespace boundary, and the debugger interface PEP 768 writes to "
            "cannot be reached across it"
        ),
        confirm=confirm,
        fix=(
            "run pidprobe inside the container (`docker exec <container> "
            f"pidprobe snap <pid-inside>`) or join first with `nsenter --target "
            f"{pid} --pid --mount`"
        ),
    )
