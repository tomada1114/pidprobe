# Troubleshooting

Attaching to a live process fails for a handful of environment-specific
reasons that all surface as the same opaque `PermissionError` or
`RuntimeError`. `pidprobe doctor` exists to tell them apart, so start there:

```bash
pidprobe doctor 12345
```

It attaches to nothing, which is why it is safe to point at a production
process. Every check it reports has a section on this page, under the same
name and in the same order, so a failing check reads straight across:

```console
  FAIL    ptrace_scope          kernel.yama.ptrace_scope is 2
```

...is [`ptrace_scope`](#ptrace_scope) below.

## Reading a doctor report

Each check comes back as one of four statuses:

| Status | Meaning |
| --- | --- |
| `OK` | The condition attaching needs is satisfied |
| `WARN` | Attaching still works, but something is degraded or unverified |
| `FAIL` | Attaching cannot work until this is fixed |
| `SKIPPED` | The check does not apply here, typically on another platform |

A `WARN` or `FAIL` always carries three things: the *cause*, a *confirm*
command you can run yourself, and the *fix*. That is enforced by the type
behind the report, so no check can ever come back as a bare "Permission
denied".

`doctor` exits `0` when nothing blocks attaching and
[`7`](reference.md#exit-codes) when at least one check failed; warnings do not
change the exit code. Without a `PID` only the first five checks run -- the
ones that describe this environment rather than a target.

### The JSON report

`--json` prints the same report for tooling:

```json
{
  "pid": 12345,
  "attachable": false,
  "checks": [
    { "name": "prober_remote_debug", "status": "ok", "summary": "..." },
    {
      "name": "ptrace_scope",
      "status": "fail",
      "summary": "kernel.yama.ptrace_scope is 2",
      "cause": "...",
      "confirm": "cat /proc/sys/kernel/yama/ptrace_scope",
      "fix": "..."
    }
  ]
}
```

`attachable` is `false` exactly when some check is `fail`. `cause`, `confirm`
and `fix` are omitted from a check that has nothing to explain, so a passing
check stays a short object. `--pretty` indents it.

## prober_remote_debug

**Can the interpreter running pidprobe inject at all?**

pidprobe injects through PEP 768, so its *own* interpreter needs
`sys.remote_exec`. This check fails in two ways: the function is missing
entirely -- a build older than CPython 3.14, or one configured
`--without-remote-debug` -- or it is present but switched off, because
`PYTHON_DISABLE_REMOTE_DEBUG` is set in pidprobe's environment. CPython then
refuses with "Remote debugging is not enabled" before it ever looks at the
target.

```bash
python3 -c "import sys; print(sys.is_remote_debug_enabled())"
env | grep PYTHON_DISABLE_REMOTE_DEBUG
```

Install pidprobe under a CPython 3.14+ build with remote debugging compiled
in, such as a python.org or uv-managed one, and unset
`PYTHON_DISABLE_REMOTE_DEBUG` before running it.

!!! note

    This is about the *prober*. The same variable in the target's environment
    is a different check, [`target_remote_debug`](#target_remote_debug), with
    a different remedy: the target has to be restarted.

## return_channel

**Can the target be given somewhere to answer?**

The injected script writes its result to a channel the prober creates first.
An `AF_UNIX` socket is preferred; when one cannot be bound, pidprobe falls
back to a temporary file it polls. The check warns on that fallback and fails
only when neither can be created, which means the probe would have nowhere to
send its answer.

```bash
python3 -c "import tempfile; print(tempfile.gettempdir())"
```

A sandbox, a filesystem without socket support, and a `TMPDIR` too long for
the 104-byte `sun_path` limit all look the same from here. Point `TMPDIR` at a
short directory both pidprobe and the target may write to and read from. The
warning is not fatal -- probing works over the file fallback, only by polling.

## collector_plugins

**Did every installed collector plugin load?**

Discovery never fails a snapshot: a plugin that cannot be imported, hands back
something that is not a collector, carries source that does not compile, or
claims a section name a built-in or an earlier plugin already took is logged
and left out. That is right for a snapshot and useless for a diagnosis, so
`doctor` listens to the `pidprobe.registry` logger and warns when anything was
skipped. `OK` lists the collectors that will run.

```bash
python3 -c "import logging; logging.basicConfig(); from pidprobe import available_collectors; available_collectors()"
```

Reinstall or uninstall the package publishing the failing
`pidprobe.collectors` entry point. A skipped plugin costs only its own
snapshot section, so every other section still comes back. See [Writing a
collector plugin](plugins.md) for what a collector has to look like.

## ptrace_scope

**Does the Linux Yama policy permit attaching?** `SKIPPED` off Linux.

PEP 768 writes into the target with `process_vm_writev`, which needs
`PTRACE_MODE_ATTACH`, and the Yama LSM decides who is granted it:

| `kernel.yama.ptrace_scope` | Status | Who may attach |
| --- | --- | --- |
| knob absent | `OK` | This kernel does not restrict attaching |
| `0` | `OK` | Anyone, within the same user |
| `1` | `WARN` | Only a direct ancestor of the target, or a process holding `CAP_SYS_PTRACE` |
| `2` | `FAIL` | Only a process holding `CAP_SYS_PTRACE` |
| `3` | `FAIL` | Nobody, and the value cannot be lowered while the machine runs |

```bash
cat /proc/sys/kernel/yama/ptrace_scope
```

At scope 1 or 2, run pidprobe as root, give its interpreter the capability
with `sudo setcap cap_sys_ptrace+ep $(readlink -f $(command -v python3))`, or
relax the knob with `sudo sysctl -w kernel.yama.ptrace_scope=0`. At scope 3
nothing can attach until the machine is rebooted with a lower value set in
`/etc/sysctl.d/`.

!!! warning

    `setcap` on a shared interpreter grants that capability to *everything*
    run through it. Prefer a dedicated interpreter, or `sudo`, on a machine
    other people use.

## task_for_pid

**Does macOS grant this user the target's task port?** `SKIPPED` off macOS.

`sys.remote_exec` must take the target's task port, and macOS hands that out
only to root or to a binary carrying the `com.apple.system-task-ports`
entitlement. Without it the attach fails with "Cannot get task port".

```bash
id -u
```

Rerun the probe under `sudo`, e.g. `sudo pidprobe snap 12345`. The entitlement
route needs a signed, entitled interpreter and is not something pidprobe can
grant itself. This check passes only when pidprobe already runs as root, so on
macOS expect to use `sudo` for every probe.

## target_process

**Does the pid exist, and may this user signal it?**

The cheapest possible question, asked with signal 0: writing into a process is
strictly more privileged than signalling it, so a refusal here settles the
matter. It fails either because nothing holds that pid -- a target that
already exited, or a pid that only means something inside a container -- or
because the process exists and belongs to someone else.

```bash
ps -p 12345
ps -o user=,pid= -p 12345
```

Find the running target with `pgrep -af python` and pass its pid, or rerun
pidprobe as the user owning the target, or as root. A vanished target is also
what `snap`, `eval` and `diff` report with [exit code
`3`](reference.md#exit-codes), separately from a refused attach, because
nothing about the machine needs fixing.

## target_owner

**Do prober and target run as the same user?** `SKIPPED` when the owner cannot
be determined.

Attaching writes into another process' memory, which the kernel permits only
within one user unless the prober is privileged. A prober running as root
passes whatever the target's owner is.

```bash
ps -o user=,uid= -p 12345; id -u
```

Rerun as that user -- `sudo -u '#1001' pidprobe snap 12345` -- or as root.

## pid_namespace

**Is there a container boundary between prober and target?** `SKIPPED` off
Linux, and when either namespace cannot be read.

The same number names different processes on the two sides of a namespace
boundary, and the debugger interface PEP 768 writes to cannot be reached
across it. A pid read from `docker top` and used on the host is the usual way
to arrive here.

```bash
readlink /proc/self/ns/pid /proc/12345/ns/pid
```

Run pidprobe inside the container -- `docker exec <container> pidprobe snap
<pid-inside>` -- or join the namespace first with `nsenter --target 12345
--pid --mount`. Both require pidprobe to be reachable there, which for a
container without it usually means the `nsenter` route.

!!! note

    On macOS and Windows this check is `SKIPPED` for a stronger reason than
    "not implemented": a container there runs inside its own Linux kernel, so
    a pid from it names nothing on the host and no amount of privilege makes
    it reachable. Probe from inside the container.

## target_python

**Which Python does the target run?** Reported as a `WARN` only when that
could not be established at all, in which case neither
[`target_python_version`](#target_python_version) nor
[`target_python_match`](#target_python_match) is reported.

A process cannot be asked for its interpreter version from the outside, so
pidprobe runs the target's *own* executable with `-c` to ask it. That only
happens when the binary's name identifies it as an interpreter (`python*`,
`pypy*`), so pointing `doctor` at an arbitrary pid never executes an arbitrary
program -- it warns here instead. The other ways to land here are a platform
that exposes no way to inspect another process (only Linux procfs and macOS
`ps` are supported), no executable reported for the pid, and an interpreter
that failed to answer.

```bash
ps -o comm= -p 12345
```

Check by hand that the target runs the same CPython feature release as
pidprobe. The warning does not block a probe: it means the two checks below
could not be run, not that they would have failed.

## target_python_version

**Is the target a CPython new enough to carry the PEP 768 interface?**

Fails when the target runs another implementation -- PyPy, GraalPy -- because
no other implementation exposes the debugger interface `sys.remote_exec`
writes to, and when it runs a CPython older than 3.14, where that interface
did not exist yet.

```bash
/usr/bin/python3 -VV
```

Restart the target on CPython 3.14 or newer, or use an out-of-process sampler
such as py-spy, which reads the target's memory instead of asking it to run
code and therefore works against older and non-CPython targets.

## target_python_match

**Do prober and target share a CPython feature release?**

`sys.remote_exec` reaches the debugger interface through offsets that are only
stable within one CPython feature release, so it refuses a target built from a
different one. 3.14 probing 3.15 fails as surely as 3.13 does; patch releases
are fine.

```bash
/usr/bin/python3 -VV; python3 -VV
```

Run pidprobe from an interpreter of the target's feature release, for example
with `uv tool install --python 3.14 pidprobe`, or install it into the target's
own environment with `/usr/bin/python3 -m pip install pidprobe`. Installing
pidprobe once per interpreter version is the normal way to live with this.

## target_remote_debug

**Was the target started with `PYTHON_DISABLE_REMOTE_DEBUG`?** `SKIPPED` off
Linux, and when the target's environment cannot be read.

CPython turns the debugger interface off when `PYTHON_DISABLE_REMOTE_DEBUG` is
present at start-up, whatever it is set to -- the empty string counts -- so
the target then has nothing for pidprobe to write to.

```bash
tr '\0' '\n' < /proc/12345/environ | grep PYTHON_DISABLE_REMOTE_DEBUG
sudo ps -wwEp 12345
```

Restart the target without the variable in its environment. It is read once at
interpreter start-up and cannot be cleared from outside, so unsetting it in
your own shell changes nothing about a process that is already running.

!!! note

    Reading another process' environment needs procfs, which is why this is
    `SKIPPED` everywhere but Linux, and why it may be skipped there too when
    the target belongs to another user. A skipped check is not a passing one:
    if the attach is refused with nothing else to explain it, check this by
    hand.

## Failures doctor cannot predict

A clean report means nothing in the environment blocks attaching. Three
failures remain possible after that, and each has its own [exit
code](reference.md#exit-codes):

| Symptom | Exit code | What it means |
| --- | --- | --- |
| `target never reached a safe eval point within 5s` | `5` | The target only runs injected code at the next bytecode boundary. A process parked in a long syscall, blocked in a C extension, or idle in a thread that never returns to Python never gets there. Raise `--timeout`, or use an out-of-process sampler such as py-spy. |
| `snapshot failed inside pid ...` | `6` | The injected script raised as a whole -- for `eval`, usually a `SyntaxError` or `NameError` in the expression, which says nothing about whether attaching works. |
| `internal error: ...` | `70` | A bug in pidprobe. Re-run with `--debug` (or `PIDPROBE_DEBUG=1`) for the traceback and [report it](https://github.com/tomada1114/pidprobe/issues). |

A single collector that raises is not in that list: it costs its own section,
which comes back as `null` with the reason in `meta.collectors`, and the
command still exits `0`. See [`meta`](output-schema.md#meta).
