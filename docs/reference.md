# API Reference

## Command line

```bash
pidprobe snap <PID> [--pretty] [--timeout SECONDS] [--no-mask]
```

`snap` injects the built-in collectors into a running CPython 3.14+ process
and prints one JSON snapshot. The default output is a single compact line so
it can be piped straight into `jq`; `--pretty` indents it instead.
`--timeout` (default: 5 seconds) is a hard budget for the whole probe.
`--no-mask` turns off secret masking, which is otherwise on.

### Secret masking

Values bound to a credential-like name -- `password`, `passwd`, `passphrase`,
`pwd`, `secret`, `token`, `apikey`, `accesskey`, `privatekey`, `credential` or
`authorization`, matched with case and separators ignored -- are replaced with
`"<masked>"`. Local variables and values under a matching string key in a
dictionary are both covered, and `stacks.masking_enabled` records whether
masking was on.

!!! warning

    Masking happens inside the target process, so a masked value never
    crosses the return channel. `--no-mask` removes that guarantee: raw
    credentials then land in the snapshot, and in whatever you pipe it into.

Every value is rendered within fixed bounds -- 3 levels of nesting, 10
elements per container, 200 characters per `repr()` and 2000 characters in
total -- with what was left out marked as `...` or `...<truncated>`.

The command exits `0` on success, `1` when the probe fails (the reason is
printed to stderr), and `2` on invalid arguments.

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

## Python API

::: pidprobe
