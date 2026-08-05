# Output schema

`pidprobe snap <PID>` prints one JSON document per run. This page describes
that document field by field, and the `{status, error, payload}` envelope the
target answers with underneath it.

Everything here is versioned by `schema_version`, and the same shape is
published as a JSON Schema inside the installed package, so downstream tooling
can validate a snapshot instead of guessing at it.

## Where each document is described

| Document | Printed by | Described in |
| --- | --- | --- |
| Snapshot | `pidprobe snap` | this page |
| Delta | `pidprobe diff` | [Delta format](reference.md#delta-format) |
| Evaluation | `pidprobe eval` | [Command line](reference.md#command-line) |
| Diagnosis | `pidprobe doctor --json` | [The JSON report](troubleshooting.md#the-json-report) |

A delta carries the same `schema_version` string, because that version tracks
pidprobe's output format as a whole, but only the snapshot is described by
`snapshot.schema.json`. The evaluation and the diagnosis carry no version of
their own.

## The envelope

A probe is one round trip: pidprobe injects a script, the script runs inside
the target and answers exactly once on the return channel. That answer is
always the same three-key object, whatever the script was asked to do:

```json
{ "status": "ok", "error": null, "payload": { "...": "..." } }
```

| Key | Contents |
| --- | --- |
| `status` | `"ok"` when the script produced a payload, `"error"` when it raised |
| `error` | `null` on success; otherwise `{"type", "message", "traceback"}` as strings, all three required |
| `payload` | The script's result on success, `null` on failure |

The envelope is the trust boundary. It is produced by another interpreter that
pidprobe does not control, so it is validated before anything else sees it: a
malformed answer -- not JSON, not an object, an unknown `status`, an `error`
status with no details, or a success with no payload -- raises `ChannelError`
and ends the command with [exit code `1`](reference.md#exit-codes). An
`"error"` envelope raises `TargetError` and exits `6`.

!!! note

    You never see an envelope in normal output: the prober unwraps it and
    prints the payload, reshaped into the documents above. It is worth knowing
    about when you write a collector, because it is what an exception inside
    the target turns into -- and, for a snapshot, why one raising collector
    costs its own section rather than the whole answer.

For a snapshot the payload is the raw material, and the prober -- not the
target -- assembles the document from it:

| Payload key | Becomes |
| --- | --- |
| `sections` | One top-level key per collector, named after the collector |
| `collectors` | `meta.collectors` |
| `target` | `meta.target` |
| `stop_duration_ms` | `meta.stop_duration_ms` |

## Snapshot document

```json
{
  "schema_version": "1.0",
  "meta": { "...": "..." },
  "stacks": { "...": "..." },
  "objects": { "...": "..." },
  "gc": { "...": "..." },
  "fds": { "...": "..." },
  "sqlalchemy": { "...": "..." }
}
```

| Key | Type | Contents |
| --- | --- | --- |
| `schema_version` | string | Version of this output format, currently `"1.0"` |
| `meta` | object | Everything about the probe itself; never `null` |
| `stacks`, `objects`, `gc`, `fds` | object or `null` | The built-in sections, always present as keys |
| *anything else* | object or `null` | One section per installed [collector plugin](plugins.md), named after it |

A section is `null` exactly when its collector raised inside the target; the
reason is then in `meta.collectors`. The four built-in keys are always
present, so a consumer can read `snapshot["gc"]` without a `get()` and only
has to handle `null`.

!!! note

    `schema_version` is bumped in its minor part for additive changes -- a new
    field, a new section -- and in its major part for anything that would break
    a reader. Unknown top-level keys are allowed by the schema, because that is
    how plugins add sections, so a validator must not reject them.

### `meta`

| Key | Type | Contents |
| --- | --- | --- |
| `pid` | integer | Process pidprobe was pointed at |
| `captured_at` | string | UTC timestamp in ISO 8601 form, e.g. `2026-08-05T14:51:08.863505Z`, taken by the prober |
| `pidprobe_version` | string | Version of pidprobe that produced the document |
| `prober` | object | Interpreter pidprobe itself runs on |
| `target` | object | Interpreter the target runs, as the target reports itself |
| `stop_duration_ms` | number | Time the target spent running the collectors, measured inside the target |
| `elapsed_ms` | number | Wall-clock time of the whole probe, measured by the prober |
| `collectors` | array | One report per collector that ran |

`prober` and `target` share one shape: `python_version`, `implementation`,
`platform` and `executable`, all strings. `target` additionally carries the
`pid` the target sees for itself.

`stop_duration_ms` is the number to watch in production: it is how long the
target was busy answering, and therefore what the probe cost the application.
`elapsed_ms` is larger by the injection and read-back the prober did around
it. Both are rounded to three decimals, and either is `null` if the target
reported something that is not a number.

Each entry of `collectors` is:

| Key | Type | Contents |
| --- | --- | --- |
| `name` | string | Section the collector fills |
| `status` | string | `"ok"` or `"error"` |
| `duration_ms` | number | How long that one collector took inside the target |
| `error` | object or `null` | `{"type", "message", "traceback"}` when it raised |

### `stacks`

Per-thread call stacks, main thread first and then by thread id.

| Key | Type | Contents |
| --- | --- | --- |
| `thread_count` | integer | Number of threads reported |
| `max_frame_depth` | integer | Frames kept per thread before truncating; currently 128 |
| `masking_enabled` | boolean | Whether credential-like locals were replaced with `"<masked>"` inside the target |
| `threads` | array | One entry per thread |

A thread is:

| Key | Type | Contents |
| --- | --- | --- |
| `thread_id` | integer | The thread's identifier in the target |
| `name` | string or `null` | Thread name; `null` when it could not be read |
| `daemon` | boolean or `null` | Daemon flag; `null` on the same terms |
| `is_main` | boolean | Whether this is the target's main thread |
| `frames` | array | Frames, **innermost first** |
| `frames_truncated` | boolean | Whether `max_frame_depth` cut the stack short |

A frame is:

| Key | Type | Contents |
| --- | --- | --- |
| `file` | string | `co_filename` of the running code |
| `line` | integer | Line currently executing |
| `function` | string | Qualified name, so a method reads as `Class.method` |
| `locals` | object or `null` | Variable name to rendered value, both strings; `null` when the frame refused to expose them |

Every value in `locals` is a *string*, never the value itself: it is rendered
inside the target by the [safe-repr
rules](reference.md#secret-masking) -- 3 levels of nesting, 10 elements per
container, 200 characters per `repr()`, 2000 in total, with `...` or
`...<truncated>` marking what was left out.

!!! warning

    `name` and `daemon` are `null` when the thread table could not be read.
    The collector deliberately reads it without taking the lock that protects
    it, because the injected script may have interrupted the very thread that
    holds it, and blocking there would deadlock the target. A missing thread
    name is the price.

### `objects`

| Key | Type | Contents |
| --- | --- | --- |
| `top_n` | integer | How many types the ranking keeps; currently 50 |
| `total_tracked` | integer | Total GC-tracked objects counted |
| `distinct_types` | integer | How many distinct types were seen, including those outside the ranking |
| `top` | array | `{"type", "count"}` entries, most numerous first |

`type` is the fully qualified name -- `app.models.Session` -- except for
builtins, which appear bare (`dict`, `list`), and objects whose type refused to
name itself, counted together as `<unknown>`.

!!! warning

    Only containers the garbage collector tracks are visible here. Atomic
    values such as `int`, `str` and `bytes` are not counted, so
    `total_tracked` is not "objects in the process" and a leak of strings
    shows up as growth in whatever holds them.

### `gc`

| Key | Type | Contents |
| --- | --- | --- |
| `enabled` | boolean | Whether the cyclic collector is switched on |
| `counts` | array of integers | `gc.get_count()`: allocations outstanding per generation |
| `thresholds` | array of integers | `gc.get_threshold()` |
| `generations` | array | One entry per generation |
| `garbage_count` | integer | Length of `gc.garbage`, i.e. uncollectable objects kept for inspection |
| `freeze_count` | integer | `gc.get_freeze_count()` |

A generation entry carries `generation` (its index) plus whatever
`gc.get_stats()` reports for it -- today `collections`, `collected` and
`uncollectable` -- and the matching `count` and `threshold`. Only `generation`
is guaranteed: the number of generations and the statistics themselves come
from the target's own interpreter, which is why they are read rather than
assumed. The free-threaded build does not use the same layout.

### `fds`

| Key | Type | Contents |
| --- | --- | --- |
| `supported` | boolean | `false` when the platform exposes no readable fd directory |
| `source` | string or `null` | Directory read: `/proc/self/fd` on Linux, `/dev/fd` elsewhere |
| `count` | integer | Number of descriptors reported |
| `descriptors` | array | One entry per descriptor, ordered by `fd` |

A descriptor is:

| Key | Type | Contents |
| --- | --- | --- |
| `fd` | integer | Descriptor number |
| `kind` | string | One of `socket`, `fifo`, `file`, `directory`, `char`, `block`, `symlink`, `unknown` |
| `target` | string or `null` | What the descriptor points at, when it can be resolved |
| `inode` | integer | Inode from `fstat` |
| `is_tty` | boolean | Whether it is a terminal |
| `size` | integer | Present for `kind` `file` only |
| `socket` | object or `null` | Present for `kind` `socket` only |

Socket details are `{"family", "type", "laddr", "raddr"}`, where the addresses
are whatever that family uses -- a `[host, port]` pair for IP, a path for
`AF_UNIX`, `null` for an unconnected peer.

!!! note

    `socket` is `null` outside Linux, and on Linux when the target has a
    default socket timeout set. The family is read from `SO_DOMAIN`, which
    only Linux provides, and wrapping a descriptor while a default timeout is
    in force would switch the target's own open file description to
    non-blocking -- observing the process must not change it. The descriptor
    itself is still listed.

## Validating a snapshot

The schema ships inside the package, so nothing has to be fetched at runtime:

```python
import json
import subprocess

from jsonschema import validate

from pidprobe import snapshot_schema

snapshot = json.loads(subprocess.run(
    ["pidprobe", "snap", "12345"], capture_output=True, text=True, check=True
).stdout)

validate(instance=snapshot, schema=snapshot_schema())
```

`snapshot_schema()` parses the file afresh on every call, so the returned
dictionary can be modified -- bundled into a larger schema, say -- without
affecting anyone else. `jsonschema` is not a pidprobe dependency; any
2020-12-capable validator works.

The schema is also readable straight from the repository as
[`src/pidprobe/snapshot.schema.json`](https://github.com/tomada1114/pidprobe/blob/main/src/pidprobe/snapshot.schema.json),
and `SCHEMA_VERSION` in the public API is the same string every snapshot
carries.
