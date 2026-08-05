# pidprobe

[![CI](https://github.com/tomada1114/pidprobe/actions/workflows/ci.yml/badge.svg)](https://github.com/tomada1114/pidprobe/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/tomada1114/pidprobe/branch/main/graph/badge.svg)](https://codecov.io/gh/tomada1114/pidprobe)
[![PyPI](https://img.shields.io/pypi/v/pidprobe)](https://pypi.org/project/pidprobe/)
[![Python](https://img.shields.io/pypi/pyversions/pidprobe)](https://pypi.org/project/pidprobe/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Structured JSON snapshots of running CPython 3.14+ processes via PEP 768 - no agent, no restart, no gdb

## Quickstart

```bash
uv tool install pidprobe
# or
pip install pidprobe
```

Point it at a running CPython 3.14+ process and get one JSON document back:

```bash
pidprobe snap 12345 | jq '.stacks.threads[0].frames[0]'
pidprobe snap 12345 --pretty        # indented, for humans
```

```json
{
  "file": "/srv/app/worker.py",
  "line": 42,
  "function": "Worker._drain",
  "locals": { "self": "<Worker queued=1281>", "timeout": "5.0" }
}
```

A snapshot carries a `meta` section (both Python versions, the measured stop
duration, the pidprobe version, the output `schema_version`) plus one section
per collector: `stacks`, `objects`, `gc`, `fds` and `sqlalchemy`. The output
format is described by a JSON Schema that ships with the package.

The same thing from Python:

```python
from pidprobe import snapshot_schema, take_snapshot

snapshot = take_snapshot(12345)
print(snapshot["meta"]["stop_duration_ms"])
print(snapshot_schema()["$id"])
```

Attaching needs the target to run CPython 3.14+ with remote debugging enabled,
and the operating system to allow it (root on macOS, `ptrace_scope` or
`CAP_SYS_PTRACE` on Linux). Failures explain which of those applies.

## Why won't it attach?

`doctor` answers that before you hit it, and without attaching to anything:

```console
$ pidprobe doctor 12345
  OK      prober_remote_debug   pidprobe runs cpython 3.14.6 with remote debugging enabled
  FAIL    ptrace_scope          kernel.yama.ptrace_scope is 2
            cause: at scope 2 only a process holding CAP_SYS_PTRACE may attach to anything, ...
            confirm: cat /proc/sys/kernel/yama/ptrace_scope
            fix: run pidprobe as root or with CAP_SYS_PTRACE, or relax the knob with ...
```

Every check that fails or warns carries a cause, a command you can run to
confirm it yourself, and the concrete fix — never a bare "Permission denied".
The `PID` is optional: without one, only the checks describing your own
environment run. `--json` prints the same report for tooling, and any `snap`,
`eval` or `diff` failure points you here.

```python
from pidprobe import diagnose

for check in diagnose(12345).failures:
    print(check.name, check.fix)
```

## Evaluating an expression

When you only want one value, `eval` skips the collectors and answers with the
rendered result of a single expression:

```console
$ pidprobe eval 12345 'len(queue)'
{"pid":12345,"expression":"len(queue)","type":"int","result":"12","masking_enabled":true}
```

The expression is compiled inside the target, against a copy of its `__main__`
namespace, and the result goes through the same bounds and masking as stack
locals. Statements are refused — `cache = {}` comes back as a `SyntaxError` —
and anything the expression raises is reported on stderr with exit code `6`,
never as a hang.

```python
from pidprobe import evaluate_in_target

print(evaluate_in_target(12345, "len(queue)")["result"])
```

## Watching a leak grow

A single snapshot cannot tell you what is *growing*. `diff` samples the same
process repeatedly and prints only what moved between two consecutive samples,
one JSON line per delta, as soon as each one is ready:

```console
$ pidprobe diff 12345 --interval 5 --count 3
{"schema_version":"1.0","meta":{"pid":12345,"from":"...","to":"...","interval_ms":5004.1},"objects":{"top_n":50,"total_tracked":{"before":91204,"after":93871,"delta":2667},...,"types":[{"type":"app.models.Session","before":1204,"after":3861,"delta":2657},...]},"gc":{...},"fds":{"count":{"before":31,"after":31,"delta":0}}}
```

Types are ranked by growth, so a leak sorts itself to the top. `--count N`
takes N snapshots and therefore prints N-1 deltas; leave it out to keep
sampling until you press Ctrl-C. Only the `objects`, `gc` and `fds` collectors
run, so a delta stops the target for less time than a full `snap` would.

```python
from pidprobe import iter_snapshot_deltas

for delta in iter_snapshot_deltas(12345, interval_seconds=5, count=3):
    print(delta["objects"]["types"][:3])
```

## Exit codes

A failing probe is something a script has to react to, so the reason is in the
exit code and not only in the message:

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Probe failed, with no more specific code |
| `2` | Invalid command line |
| `3` | No such process |
| `4` | Attaching to the target was refused |
| `5` | The target did not answer within the timeout |
| `6` | The injected code raised inside the target |
| `7` | `doctor` found a check that blocks attaching |
| `70` | pidprobe hit an unexpected error — a bug |
| `130` | Interrupted with Ctrl-C |
| `141` | The reader of stdout closed the pipe |

`pidprobe --help` prints the same table. Errors arrive on stderr as a single
`pidprobe: ...` line — never a traceback — and every `snap`, `eval` or `diff`
failure points at `pidprobe doctor <PID>`. `doctor` is the exception: its
report goes to stdout whatever it says, because the report is the output.

A global `--timeout` before the subcommand overrides the 5-second default for
whichever command follows, and the same option after the subcommand overrides
that in turn:

```bash
pidprobe --timeout 30 snap 12345      # this probe gets 30 seconds
pidprobe --timeout 30 diff 12345 --interval 5 --timeout 2   # 2 wins
```

If pidprobe ever exits `70`, that is a bug in pidprobe. Re-run with `--debug`
(or `PIDPROBE_DEBUG=1`) for the traceback and please report it.

## Secret masking

Snapshots get pasted into issues and chat, so pidprobe masks credentials **by
default**. A value is replaced with `"<masked>"` when the name it is bound to
contains any of

`password`, `passwd`, `passphrase`, `pwd`, `secret`, `token`, `apikey`,
`accesskey`, `privatekey`, `credential`, `authorization`

Case and separators are ignored, so `API_KEY`, `api_key` and `apiKey` all
match. This applies to local variables in stack frames, to values stored
under a matching string key in a dictionary — `{"api_key": "<masked>"}` — and
to the expression text `pidprobe eval` is given. The
masking happens *inside the target process*, so an unmasked value never
crosses the wire, and `stacks.masking_enabled` records whether it was on.

Names that merely look similar are masked too (`token_count` is not a
credential, but it matches `token`): over-masking costs one more look,
under-masking leaks a secret. Pass `--no-mask` when you need the raw values:

```bash
pidprobe snap 12345 --no-mask
```

Every rendered value is bounded regardless of masking: at most 3 levels of
nesting, 10 elements per container, 200 characters per `repr()` and 2000
characters in total. Anything left out is shown as `...` or
`...<truncated>`, so one pathological object cannot blow up a snapshot.

## Collector plugins

Any package can add its own section to every snapshot by publishing an entry
point in the `pidprobe.collectors` group:

```toml
[project.entry-points."pidprobe.collectors"]
redis = "my_package.collectors:REDIS"
```

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

`source` runs inside the *target* process and assigns `data`, which becomes the
`redis` section of the snapshot. It must never `import` the library it reports
on — that would load it into a process that never asked for it — so it looks
in `sys.modules` instead. A plugin that fails to load, or that raises inside
the target, costs only its own section — every other collector still reports.
See the
[API Reference](https://tomada1114.github.io/pidprobe/reference/#collector-plugins)
for the full contract, and
[Writing a collector plugin](https://tomada1114.github.io/pidprobe/plugins/)
for the shipped `sqlalchemy` collector as a worked example.

## Design Philosophy

Every choice in this template has a reason. If you disagree with a decision,
you know exactly what to change and why it was there in the first place.

### Why `src/` layout?

The `src/` layout prevents accidental imports of the local package during
development and testing. It ensures that tests always run against the
*installed* version, catching packaging errors before they reach users.

### Why strict mypy + comprehensive Ruff rules?

Type errors and lint issues are cheapest to fix at write time. Strict settings
from day one mean every line of code is held to the same standard — there is
never a "legacy" codebase to clean up. LLMs generating code also benefit from
strict rules: they produce higher-quality output when constraints are clear.

### Why zero runtime dependencies?

A library template should not impose opinions about logging, HTTP clients, or
data validation. You add what you need. Starting from zero keeps the dependency
tree small and avoids conflicts with downstream users.

### Why Just over Make?

Just has cleaner syntax (no mandatory tabs), better cross-platform support, and
more readable recipe definitions. It is a task runner, not a build system —
which is exactly what a Python project needs.

### Why AGENTS.md and .claude/?

AI-assisted development is the norm, not the exception. `AGENTS.md` gives any
coding agent (Claude Code, Codex, Cursor, Gemini CLI, ...) the context it
needs to match your project's standards; `CLAUDE.md` imports it and adds
Claude Code specifics. The committed `.claude/` directory goes further than
prose: path-scoped rules load conventions only when relevant files are
touched, hooks deterministically auto-format edited files, block edits to
`uv.lock`/`.env*` and `--no-verify`/force-push commands, and run ruff + mypy
before the agent ends a turn, while a reviewed permission allowlist covers
local build/lint/test commands only — commit, push, and PR creation always
stay behind human approval.

### Why 80% coverage minimum?

80% is high enough to catch most regressions but low enough to avoid
test-for-the-sake-of-testing. Branch coverage is enabled, so conditional logic
is meaningfully tested.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for full setup instructions.

```bash
uv sync --all-groups
# Optional but recommended when working in a Git checkout
uv run pre-commit install --install-hooks
just check
```

`just install` installs pre-commit hooks automatically when the project lives in
a Git repository and skips that step for "Use this template" bootstrap copies
before Git is initialized.

For packaging verification, run `just smoke` (or `uv build && uv run python scripts/smoke_test.py`)
to install the freshly built wheel into a temporary virtual environment and
confirm the distribution imports from the wheel, not from `src/`.

## Documentation

- [Getting Started](https://tomada1114.github.io/pidprobe/getting-started/)
- [API Reference](https://tomada1114.github.io/pidprobe/reference/)

## License

[MIT](LICENSE)
