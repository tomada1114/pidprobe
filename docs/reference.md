# API Reference

## Command line

```bash
pidprobe snap <PID> [--pretty] [--timeout SECONDS] [--no-mask]
```

`snap` injects the built-in collectors -- plus every installed
[collector plugin](#collector-plugins) -- into a running CPython 3.14+ process
and prints one JSON snapshot. The default output is a single compact line so
it can be piped straight into `jq`; `--pretty` indents it instead.
`--timeout` (default: 5 seconds) is a hard budget for the whole probe.
`--no-mask` turns off secret masking, which is otherwise on.

```bash
pidprobe eval <PID> <EXPR> [--pretty] [--timeout SECONDS] [--no-mask]
```

`eval` evaluates one Python expression inside a running process and prints its
rendered value, without collecting a snapshot:

```console
$ pidprobe eval 12345 'len(queue)'
{"pid":12345,"expression":"len(queue)","type":"int","result":"12","masking_enabled":true}
```

The expression is compiled *in the target* against a copy of its `__main__`
namespace, and the result is rendered by the same rules as stack locals -- the
bounds and the masking below both apply, and `result` is therefore always a
string. `type` names the result's type and survives masking.

!!! note

    Statements are not expressions: `pidprobe eval 12345 'cache = {}'` comes
    back as a `SyntaxError` rather than rebinding anything. Evaluating a
    *call* can still have side effects, because the target runs it — the same
    call you would make in a debugger.

Anything the expression raises -- a `SyntaxError` from compiling it, a
`NameError`, or an exception from the expression itself -- comes back as the
failure it is, never as a timeout: the target answers with the exception
instead of going quiet.

### Secret masking

Values bound to a credential-like name -- `password`, `passwd`, `passphrase`,
`pwd`, `secret`, `token`, `apikey`, `accesskey`, `privatekey`, `credential` or
`authorization`, matched with case and separators ignored -- are replaced with
`"<masked>"`. Local variables and values under a matching string key in a
dictionary are both covered, and `stacks.masking_enabled` records whether
masking was on. `eval` matches the same patterns against the expression text,
so `pidprobe eval 12345 api_key` is masked and reports
`"masking_enabled": true`.

!!! warning

    Masking happens inside the target process, so a masked value never
    crosses the return channel. `--no-mask` removes that guarantee: raw
    credentials then land in the output, and in whatever you pipe it into.

Every value is rendered within fixed bounds -- 3 levels of nesting, 10
elements per container, 200 characters per `repr()` and 2000 characters in
total -- with what was left out marked as `...` or `...<truncated>`.

```bash
pidprobe doctor [PID] [--json] [--pretty]
```

`doctor` explains whether attaching would work, without attaching: it never
injects anything, so it is safe to point at a production process. Given a
`PID` it also examines that process; without one it reports only on this
environment, which is what you want before there is a target to name.

```console
$ pidprobe doctor 12345
pidprobe doctor: checking this environment against pid 12345

  OK      prober_remote_debug   pidprobe runs cpython 3.14.6 with remote debugging enabled
  OK      return_channel        an AF_UNIX return channel binds at /tmp/pidprobe-3f9a1c2e/s.sock
  OK      collector_plugins     5 collectors will run: stacks, objects, gc, fds, sqlalchemy
  FAIL    ptrace_scope          kernel.yama.ptrace_scope is 2
            cause: at scope 2 only a process holding CAP_SYS_PTRACE may attach to anything, ...
            confirm: cat /proc/sys/kernel/yama/ptrace_scope
            fix: run pidprobe as root or with CAP_SYS_PTRACE, or relax the knob with ...
  ...

1 check failed; attaching to pid 12345 will not work
```

Every check that fails or warns carries all four of the things you need: which
check it was, the *cause*, a *confirm* command you can run yourself, and the
*fix*. The type rejects a check built without them, so no diagnosis can come
back as a bare "Permission denied".
`--json` prints the same report as `{"pid", "attachable", "checks"}` for
tooling.

| Check | What it answers |
| --- | --- |
| `prober_remote_debug` | Can this interpreter call `sys.remote_exec` at all? |
| `return_channel` | Can a channel be created for the target to answer on? |
| `collector_plugins` | Did every installed collector plugin load? |
| `ptrace_scope` | Does the Linux Yama policy permit attaching? |
| `task_for_pid` | Does macOS grant this user the target's task port? |
| `target_process` | Does the pid exist and may this user signal it? |
| `target_owner` | Do prober and target run as the same user? |
| `pid_namespace` | Is there a container boundary between them? |
| `target_python_version` | Is the target CPython 3.14+? |
| `target_python_match` | Do both sides share a CPython feature release? |
| `target_remote_debug` | Was the target started with `PYTHON_DISABLE_REMOTE_DEBUG`? |

!!! note

    A check that cannot be answered here is reported as `SKIPPED` rather than
    guessed at: `ptrace_scope` on macOS, `task_for_pid` on Linux, and
    everything that reads another process' environment or namespace off
    Linux. Skipped checks still print the command that would answer them.

!!! warning

    Establishing the target's Python version means running the target's own
    executable with `-c` -- a process cannot be asked for its version from
    the outside. That only happens when the binary's name identifies it as an
    interpreter (`python*`, `pypy*`), so pointing `doctor` at an arbitrary pid
    never executes an arbitrary program; it reports `target_python` as a
    warning instead.

All three commands exit `0` on success, `1` when the probe fails or `doctor`
found a blocking check (the reason is printed to stderr for `snap` and `eval`,
to stdout for `doctor`), and `2` on invalid arguments. A `snap` or `eval`
failure always names `pidprobe doctor <PID>` in its error, whatever went
wrong.

## Snapshot format

Every snapshot is a JSON object with a `schema_version`, a `meta` section and
one section per collector, named after that collector:

| Key | Contents |
| --- | --- |
| `meta` | Target and prober Python versions, pidprobe version, capture timestamp, measured stop duration inside the target, total elapsed time, and one status report per collector |
| `stacks` | Per-thread call stacks: file, line, function and a bounded, credential-masking repr of every local variable, innermost frame first |
| `objects` | Counts of GC-tracked objects grouped by type, top 50 by count |
| `gc` | Garbage collector state: counts, thresholds and per-generation statistics |
| `fds` | Open file descriptors, each with its kind, target and (on Linux) socket addresses |
| `sqlalchemy` | Connection pools the target holds, with size, checked-out count and overflow; `"available": false` when the target never imported SQLAlchemy |

!!! note

    A collector that fails costs only its own section: that section becomes
    `null` and the reason is reported in `meta.collectors`. The rest of the
    snapshot still comes back.

The format is described by a JSON Schema shipped inside the package, so
downstream tooling can validate the output with any JSON Schema validator:

```python
from pidprobe import snapshot_schema

schema = snapshot_schema()
```

!!! warning

    `objects` only counts containers the garbage collector tracks. Atomic
    values such as `int` and `str` are invisible to it and are not counted.

## Collector plugins

A section can come from any installed package, not only from pidprobe --
including the `sqlalchemy` section above, which pidprobe ships as an ordinary
plugin. [Writing a collector plugin](plugins.md) walks through that one as a
worked example; the contract is below. Publish an entry point in the
`pidprobe.collectors` group:

```toml
[project.entry-points."pidprobe.collectors"]
sqlalchemy = "pidprobe_sqlalchemy:COLLECTOR"
```

The entry point resolves either to a collector or to a zero-argument callable
returning one:

```python
from pidprobe import Collector

COLLECTOR = Collector(
    name="sqlalchemy",
    source="""
import sqlalchemy

data = {"version": sqlalchemy.__version__}
""",
    description="SQLAlchemy engine and pool state",
)
```

`source` does not run in the prober. It becomes a function body inside the
*target* process and must assign a JSON-serializable value to `data`, which is
published as the top-level section named after `name` — next to `stacks`,
`objects`, `gc` and `fds`, and reported in `meta.collectors` like any built-in
one. The JSON Schema allows unknown top-level keys for exactly this reason.

`Collector` is a convenience, not a requirement: `CollectorSpec` is the typed
protocol discovery accepts, so any object carrying `name`, `source` and
`description` strings qualifies. What a snapshot would run is available
without probing anything:

```python
from pidprobe import available_collectors, discover_collectors

print([collector.name for collector in available_collectors()])
print([collector.name for collector in discover_collectors()])
```

!!! note

    Discovery never fails a snapshot. A plugin that cannot be imported, hands
    back something that is not a collector, carries source that does not
    compile, or claims a name a built-in or an earlier plugin already took is
    logged on the `pidprobe.registry` logger and left out; every other section
    still comes back. A plugin that raises *inside the target* costs only its
    own section, exactly like a built-in.

## Python API

::: pidprobe
