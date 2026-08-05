# pidprobe

[![CI](https://github.com/tomada1114/pidprobe/actions/workflows/ci.yml/badge.svg)](https://github.com/tomada1114/pidprobe/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/tomada1114/pidprobe/branch/main/graph/badge.svg)](https://codecov.io/gh/tomada1114/pidprobe)
[![PyPI](https://img.shields.io/pypi/v/pidprobe)](https://pypi.org/project/pidprobe/)
[![Python](https://img.shields.io/pypi/pyversions/pidprobe)](https://pypi.org/project/pidprobe/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

![A hung FastAPI request, then pidprobe snap piped into jq, naming the two deadlocked threads and the connection pool they stranded](docs/assets/demo.gif)

**Ask a running CPython 3.14+ process what it is stuck on and get one JSON
document back — no agent, no restart, no gdb.**

## Quickstart

```bash
uv tool install pidprobe    # or: pip install pidprobe
pidprobe snap 12345 | jq '.stacks.threads[0].frames[0]'
```

```json
{
  "file": "/srv/app/worker.py",
  "line": 42,
  "function": "Worker._drain",
  "locals": { "self": "<Worker queued=1281>", "timeout": "5.0" }
}
```

## What it cannot do

pidprobe attaches through [PEP 768](https://peps.python.org/pep-0768/):
`sys.remote_exec` hands the target a script, and the target runs it *itself*,
at its next safe eval point. Everything below follows from that, and most of
it is the opposite trade-off from [py-spy](https://github.com/benfred/py-spy),
which reads the interpreter's memory from the outside and never asks it to run
anything.

| | pidprobe | py-spy |
| --- | --- | --- |
| Target blocked in a C extension or a native syscall | **no answer**: the target never reaches a safe eval point, so the probe times out and exits `5` | unaffected — it never asks the interpreter to run anything |
| Python versions | CPython **3.14+ only**, and pidprobe must run on the target's own `major.minor` | CPython 2.3–2.7 and 3.3–3.14 |
| Cost to the target | the target stops for the duration of the injected script (`meta.stop_duration_ms` reports it) | pauses the target while sampling, or not at all with `--nonblocking` |
| Where the CPU time goes | not its job: no sampling, no flame graphs | that is the whole point of it |
| Native (C) frames | never — it only sees what the interpreter can see | `--native`, on supported platforms |
| Evaluate an expression inside the target | `pidprobe eval 12345 'len(queue)'` | no — it does not execute code in the target |
| Object counts per type, GC stats, open fds | every snapshot carries them | not collected |
| What is *growing* | `pidprobe diff` subtracts repeated samples and ranks types by growth | not collected |
| Library state, e.g. SQLAlchemy pool checkouts | [collector plugins](https://tomada1114.github.io/pidprobe/plugins/) add their own snapshot section | not collected |
| Locals per frame | rendered with `repr()` inside the target, bounded, credentials masked by default | `py-spy dump --locals` |
| Output | one JSON document with a [versioned schema](https://tomada1114.github.io/pidprobe/output-schema/) | text, or `py-spy dump --json` |
| Permission to attach | root on macOS, ptrace on Linux | root on macOS, ptrace on Linux |

The two answer different questions and are worth having side by side: py-spy
tells you where a process spends its time, pidprobe tells you what one is
holding right now.

## Constraints

- **CPython 3.14+ on both sides, matching `major.minor`.** `sys.remote_exec`
  reaches the debugger interface through offsets that are only stable within
  one feature release, so 3.14 probing 3.15 fails as surely as 3.13 does.
  Patch releases are fine. Installing pidprobe once per interpreter version —
  `uv tool install --python 3.14 pidprobe` — is the normal way to live with
  this.
- **Remote debugging has to be compiled in and switched on.** A build
  configured `--without-remote-debug` has no `sys.remote_exec`, and
  `PYTHON_DISABLE_REMOTE_DEBUG` in the target's environment at start-up turns
  the interface off for the life of that process — it cannot be cleared from
  outside.
- **The OS has to allow attaching.** Root on macOS (`task_for_pid`), a
  permissive `kernel.yama.ptrace_scope` or `CAP_SYS_PTRACE` on Linux, plus the
  same uid and the same PID namespace as the target.
- **The target has to reach a safe eval point** within the timeout (5 seconds
  by default, `--timeout` to change it). A process parked in a long syscall,
  blocked inside a C extension, or idle in a thread that never returns to
  Python never gets there. That is exit code `5`, and it is the case where an
  out-of-process sampler such as [py-spy](https://github.com/benfred/py-spy)
  is the right tool instead.

`pidprobe doctor` covers all of that in eleven checks without attaching to
anything, and every failing check carries a cause, a command that confirms it
and the fix:

```console
$ pidprobe doctor 12345
  OK      prober_remote_debug   pidprobe runs cpython 3.14.6 with remote debugging enabled
  FAIL    ptrace_scope          kernel.yama.ptrace_scope is 2
            cause: at scope 2 only a process holding CAP_SYS_PTRACE may attach to anything, ...
            confirm: cat /proc/sys/kernel/yama/ptrace_scope
            fix: run pidprobe as root or with CAP_SYS_PTRACE, or relax the knob with ...
```

Every `snap`, `eval` and `diff` failure points back here.
[Troubleshooting](https://tomada1114.github.io/pidprobe/troubleshooting/) has a
section per check.

## The commands

| Command | What it gives you |
| --- | --- |
| `pidprobe snap PID` | one snapshot: thread stacks with locals, object counts per type, GC statistics, open file descriptors, and a section per installed [collector plugin](https://tomada1114.github.io/pidprobe/plugins/) |
| `pidprobe diff PID --interval 5` | one JSON line per delta between consecutive snapshots, types ranked by growth, so a leak sorts itself to the top |
| `pidprobe eval PID 'len(queue)'` | one expression, compiled and evaluated inside the target against a copy of its `__main__` namespace |
| `pidprobe doctor [PID]` | whether attaching would work, and what to do about it if not |

`--pretty` indents the JSON, `--no-mask` turns off credential masking on the
commands that render values, a global `--timeout` before the subcommand sets
the budget for whichever probe follows, and `--debug` (or `PIDPROBE_DEBUG=1`)
re-raises an unexpected error instead of summarising it. Every failure category
has its own exit code so a script can react to *what* went wrong; the table is
printed by `pidprobe --help` and documented in the
[API Reference](https://tomada1114.github.io/pidprobe/reference/#exit-codes).

Credentials are masked **by default**, inside the target, so an unmasked value
never crosses the wire: a value bound to a name containing `password`, `token`,
`api_key` and friends comes back as `"<masked>"`. Every rendered value is
bounded in nesting, element count and length regardless, so one pathological
object cannot blow up a snapshot. See
[Secret masking](https://tomada1114.github.io/pidprobe/reference/#secret-masking).

## From Python

The command line is a thin wrapper over the same functions:

```python
from pidprobe import diagnose, evaluate_in_target, iter_snapshot_deltas, take_snapshot

snapshot = take_snapshot(12345)
print(snapshot["meta"]["stop_duration_ms"])
print(evaluate_in_target(12345, "len(queue)")["result"])

for check in diagnose(12345).failures:
    print(check.name, check.fix)

for delta in iter_snapshot_deltas(12345, interval_seconds=5, count=3):
    print(delta["objects"]["types"][:3])
```

## Try it without breaking your own app

`demo/` is a FastAPI + SQLAlchemy application that deadlocks and leaks on
request, in a Docker image that already has pidprobe installed beside it —
which is what the GIF above is recording:

```bash
docker build -t pidprobe-demo -f demo/Dockerfile .
docker run --rm -d --name pidprobe-demo -p 8000:8000 --cap-add=SYS_PTRACE pidprobe-demo

curl -s -X POST localhost:8000/deadlock              # arm the lock-order inversion
curl -s --max-time 5 localhost:8000/reports/daily    # this never comes back

docker exec pidprobe-demo pidprobe snap 1 \
    | jq -c '.stacks.threads[] | select(.frames[0].file | endswith("app.py"))
             | {thread: .name, stuck_at: "\(.frames[0].function):\(.frames[0].line)"}'
```

```json
{"thread":"ledger-writer","stuck_at":"_ledger_writer:96"}
{"thread":"AnyIO worker thread","stuck_at":"read_report:180"}
```

The [demo walkthrough](https://tomada1114.github.io/pidprobe/demo/) takes it
from there through the leak and `diff`, `doctor` and the SQLAlchemy pool
section.

## Documentation

- [Getting Started](https://tomada1114.github.io/pidprobe/getting-started/) — install and first probe
- [Demo](https://tomada1114.github.io/pidprobe/demo/) — the broken app above, walked through end to end
- [API Reference](https://tomada1114.github.io/pidprobe/reference/) — every command, option, exit code and public function
- [Output Schema](https://tomada1114.github.io/pidprobe/output-schema/) — every field of the JSON pidprobe prints
- [Collector Plugins](https://tomada1114.github.io/pidprobe/plugins/) — add your own snapshot section
- [Troubleshooting](https://tomada1114.github.io/pidprobe/troubleshooting/) — one section per `pidprobe doctor` check

## Contributing

pidprobe has zero runtime dependencies and intends to keep it that way. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the full setup;
[`just check`](justfile) runs format, lint, type check and tests.

```bash
uv sync --all-groups
uv run pre-commit install --install-hooks
just check
```

## License

[MIT](LICENSE)
