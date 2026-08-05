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
pidprobe diff <PID> --interval SECONDS [--count N] [--pretty] [--timeout SECONDS]
```

`diff` samples one process over and over and prints only what moved between
two consecutive samples, which is what finds a leak: a growing type is
invisible in any single snapshot and tedious to spot across two full ones.

```console
$ pidprobe diff 12345 --interval 5 --count 3 | jq -c '.objects.types[0]'
{"type":"app.models.Session","before":1204,"after":3861,"delta":2657}
{"type":"app.models.Session","before":3861,"after":6498,"delta":2637}
```

Each delta is printed as one JSON object on its own line -- **JSON Lines** --
and flushed as soon as it is computed, so the stream can be piped into `jq`
or a log while it is still running. `--pretty` indents each delta instead,
which is for reading: the output is then no longer one delta per line.

`--count N` takes `N` snapshots and therefore prints `N - 1` deltas, since a
delta needs a pair; `--count 1` prints nothing. Without `--count` the command
samples until it is interrupted with Ctrl-C, which ends it cleanly with exit
code `130` and no traceback. `--interval` is measured between the *starts* of
two samples, so the time a probe itself costs is taken off the wait rather
than added to it.

!!! note

    Only the `objects`, `gc` and `fds` collectors run -- the three sections a
    delta is defined for. Stacks and [plugin](#collector-plugins) sections are
    not sampled at all, so `diff` stops the target for less time per sample
    than `snap` does, and `--no-mask` has nothing to apply to: a delta reports
    counters, never values read out of the target.

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

### Global options

```bash
pidprobe [--timeout SECONDS] [--debug] <command> ...
```

`--timeout` before the subcommand sets the hard probe budget for whichever
command follows, overriding the built-in default of 5 seconds; the same option
*after* the subcommand overrides it in turn, so the more specific one wins.
`doctor` accepts it and ignores it, because it never attaches and so has
nothing to budget.

`--debug` re-raises an unexpected error instead of summarising it, printing
the real traceback. `PIDPROBE_DEBUG=1` in the environment does the same, which
is the version you want inside a script. It only affects *unexpected* errors:
a diagnosed failure like a timeout is reported the same way either way.

### Exit codes

Every command reports its outcome with one of these, so a script can act on
what went wrong without parsing the message:

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | The probe failed for a reason with no more specific code |
| `2` | Invalid command line |
| `3` | No such process |
| `4` | Attaching to the target was refused |
| `5` | The target did not answer within the timeout |
| `6` | The injected code raised inside the target |
| `7` | `doctor` found a check that blocks attaching |
| `70` | pidprobe hit an unexpected error — a bug |
| `130` | Interrupted with Ctrl-C |
| `141` | The reader of stdout closed the pipe |

`pidprobe --help` prints the same table. The failure is explained on stderr as
a single `pidprobe: ...` line for `snap`, `eval` and `diff`; `doctor` prints
its report to stdout whatever it says, because the report *is* its output, and
only the exit code separates a clean environment from a blocked one.

!!! note

    `3` and `4` are worth telling apart: a vanished process is nothing to fix
    and may be worth retrying, while a refused attach needs an operator. `7`
    likewise means the diagnosis itself succeeded — distinct from `1`, which
    means no diagnosis could be produced.

A `snap`, `eval` or `diff` failure always names `pidprobe doctor <PID>` in its
error, whatever went wrong. A `doctor` failure does not, since that is where
you already are.

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

## Delta format

`pidprobe diff` prints a different document from `snap`: not a snapshot, but
the difference between two of them. Every number that moved is reported as a
`{"before", "after", "delta"}` object, where `delta` is `after - before`.

```json
{
  "schema_version": "1.0",
  "meta": {
    "pid": 12345,
    "from": "2026-08-05T14:51:08.863505Z",
    "to": "2026-08-05T14:51:13.867786Z",
    "interval_ms": 5004.281
  },
  "objects": {
    "top_n": 50,
    "total_tracked": { "before": 91204, "after": 93871, "delta": 2667 },
    "distinct_types": { "before": 412, "after": 413, "delta": 1 },
    "types": [
      { "type": "app.models.Session", "before": 1204, "after": 3861, "delta": 2657 },
      { "type": "dict", "before": 30112, "after": 30121, "delta": 9 },
      { "type": "tuple", "before": 18004, "after": 17998, "delta": -6 }
    ]
  },
  "gc": {
    "generations": [
      {
        "generation": 0,
        "collections": { "before": 14, "after": 19, "delta": 5 },
        "collected": { "before": 179, "after": 233, "delta": 54 },
        "uncollectable": { "before": 0, "after": 0, "delta": 0 },
        "count": { "before": 7, "after": 925, "delta": 918 }
      }
    ],
    "garbage_count": { "before": 0, "after": 0, "delta": 0 },
    "freeze_count": { "before": 0, "after": 0, "delta": 0 }
  },
  "fds": { "count": { "before": 31, "after": 31, "delta": 0 } }
}
```

| Key | Contents |
| --- | --- |
| `meta` | The pid, the capture timestamps of the two snapshots (`from`, `to`) and the milliseconds actually measured between them |
| `objects.types` | Every type whose count moved, ranked by `delta` from fastest-growing to fastest-shrinking, ties broken by type name |
| `objects.total_tracked`, `objects.distinct_types` | How the totals of the `objects` section moved |
| `gc.generations` | One row per generation both snapshots reported, paired by `generation` index, with every statistic diffed |
| `gc.garbage_count`, `gc.freeze_count` | How the two `gc` totals moved |
| `fds.count` | How many file descriptors the target gained or lost |

A type is left out of `objects.types` when its count did not move -- a delta
reports what changed, and most of the hundreds of types a process holds did
not. The `gc` and `fds` numbers are reported either way: their shape is fixed
and small, and "the collector never ran" is worth telling apart from "nothing
happened".

!!! warning

    `before` or `after` is `null` when the type was outside that snapshot's
    `top_n` ranking, which is **not** the same as having no instances. The
    unknown side is then counted as zero, so `delta` bounds the change in the
    direction it moved instead of stating it exactly.

!!! note

    A section is `null` when either snapshot lacked it, which is what a
    collector that failed inside the target leaves behind. The
    `schema_version` tracks pidprobe's output format as a whole; a delta is
    not a snapshot and is not described by `snapshot.schema.json`.

## Collector plugins

A section can come from any installed package, not only from pidprobe --
including the `sqlalchemy` section above, which pidprobe ships as an ordinary
plugin. [Writing a collector plugin](plugins.md) walks through that one as a
worked example; the contract is below. Publish an entry point in the
`pidprobe.collectors` group:

```toml
[project.entry-points."pidprobe.collectors"]
redis = "my_package.collectors:REDIS"
```

The entry point resolves either to a collector or to a zero-argument callable
returning one:

```python
from pidprobe import Collector

REDIS = Collector(
    name="redis",
    source="""
import sys

module = sys.modules.get("redis")
data = {"available": module is not None}
""",
    description="whether the target has Redis loaded",
)
```

`source` does not run in the prober. It becomes a function body inside the
*target* process and must assign a JSON-serializable value to `data`, which is
published as the top-level section named after `name` — next to `stacks`,
`objects`, `gc` and `fds`, and reported in `meta.collectors` like any built-in
one. The JSON Schema allows unknown top-level keys for exactly this reason.

!!! warning

    `source` must not `import` the library it reports on. The import would
    run inside the target and load a package that process never asked for,
    changing what you were trying to observe. Look the module up in
    `sys.modules` instead, as above, and report `"available": false` when it
    is not there. [Writing a collector plugin](plugins.md) covers the rest of
    the rules.

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
