# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-05

First release with a working probe. 0.0.1 reserved the name and shipped no
code, so everything below is new; nothing in it replaces released behaviour.

### Added

- `pidprobe snap <PID>`: one JSON snapshot of a running CPython 3.14+ process,
  built from the `stacks`, `objects`, `gc` and `fds` collectors. Compact
  single-line output by default, `--pretty` for humans, `--timeout` for the
  probe budget.
- `take_snapshot()` and `snapshot_schema()` in the public API, plus the
  `snapshot.schema.json` JSON Schema describing the output format
  (`schema_version` 1.0).
- `pidprobe eval <PID> <EXPR>` and `evaluate_in_target()`: evaluate one
  expression inside a running process and get its bounded, credential-masking
  repr back as JSON.
- `pidprobe diff <PID> --interval S [--count N]`, plus `iter_snapshot_deltas()`
  and `diff_snapshots()`: repeated snapshots reduced to what changed between
  them -- object counts per type ranked by growth, GC generation statistics
  and the open file descriptor count. Each delta is printed as one JSON line
  as soon as it is ready, `--count N` yields `N - 1` deltas, and without
  `--count` the series runs until Ctrl-C ends it with exit code `130`. Only
  the `objects`, `gc` and `fds` collectors run, so a sample costs the target
  less than a full snapshot.
- `pidprobe doctor [PID]` and `diagnose()`: attach diagnostics that never
  attach. Eleven checks cover the prober's own remote-debug support, the
  return channel, collector plugin health, the Linux Yama `ptrace_scope`
  policy, macOS `task_for_pid` access, process existence and ownership, PID
  namespace boundaries, the target's CPython version and its match with the
  prober's, and `PYTHON_DISABLE_REMOTE_DEBUG` in the target's environment.
  Every failing or warning check carries a cause, a command to confirm it and
  a fix -- enforced by `Check` itself, which refuses to be built without them.
  Text by default, `--json` for tooling, exit code `7` when a check blocks
  attaching. Every `snap`, `eval` and `diff` failure names
  `pidprobe doctor <PID>`.
- Safe rendering of every value a snapshot reports: bounded in nesting depth,
  element count and length, and masking values whose name looks like a
  credential (`password`, `token`, `api_key`, ...) with `"<masked>"`. Masking
  runs inside the target, is on by default, is reported as
  `stacks.masking_enabled`, and is turned off with `pidprobe snap --no-mask`.
- Collector plugin API: any package can add a snapshot section by publishing an
  entry point in the `pidprobe.collectors` group. `CollectorSpec` is the typed
  contract, `Collector`, `available_collectors()` and `discover_collectors()`
  round out the public API, and a plugin that fails to load or raises inside
  the target costs only its own section.
- `sqlalchemy` section: the reference collector plugin reports every
  connection pool the target holds with its size, checked-out count and
  overflow, and `"available": false` when the target never imported
  SQLAlchemy.
- Documented, stable exit codes so a script can act on *what* failed without
  parsing the message: `0` success, `1` an unclassified probe failure, `2` an
  invalid command line, `3` no such process, `4` attach refused, `5` timeout,
  `6` the injected code raised inside the target, `7` `doctor` found a
  blocking check, `70` a bug in pidprobe, `130` Ctrl-C, `141` a closed stdout
  pipe. `pidprobe --help` prints the table and the API reference documents it.
  An unexpected error inside pidprobe is summarised as a
  `pidprobe: internal error: ...` line rather than a raw traceback, and a
  closed stdout pipe -- `pidprobe snap PID | head -1` -- ends quietly instead
  of in the interpreter's "Exception ignored" notice.
- `TargetError` for failures raised inside the target process, and
  `NoSuchProcessError`, the `AttachError` subclass raised when the pid does
  not exist, so "the process is gone" can be told from "attaching was
  refused" both in Python and in the exit code.
- Global `pidprobe --timeout SECONDS` before the subcommand, overriding the
  5-second default for whichever command follows; the existing per-subcommand
  `--timeout` still works and wins over it.
- `pidprobe --debug`, and `PIDPROBE_DEBUG=1`, to re-raise an unexpected error
  with its traceback instead of summarising it.
- Documentation: a README that opens with the demo GIF and leads with what
  pidprobe *cannot* do -- an honest comparison against py-spy and the
  constraints that follow from PEP 768 -- over a documentation site with
  *Getting Started*, *Demo*, *API Reference*, *Output Schema* (the
  `{status, error, payload}` envelope and every field of every snapshot
  section), *Collector Plugins* (the collector contract, how to see what
  discovery found, and how to run a collector without a target) and
  *Troubleshooting* (one section per `pidprobe doctor` check, under the
  check's own name and in the order the report prints them). A test asserts
  docs and code agree on the check names, their order, the exit code table
  and the subcommands.
- Demo environment: `demo/` holds a FastAPI + SQLAlchemy application that is
  broken on purpose -- a lock-order deadlock that hangs an HTTP request and
  strands two pooled connections, and an unbounded cache that leaks -- plus a
  Dockerfile that installs it next to pidprobe, so the same-`major.minor`
  CPython requirement holds inside one container. The *Demo* documentation
  page walks the whole thing through `snap`, `diff`, `doctor` and `eval`, and
  `scripts/record_demo.sh` drives the deadlock half non-interactively for
  recording and for CI. The demo's dependencies stay out of `pyproject.toml`:
  pidprobe still has no runtime dependencies.
- `docs/assets/demo.gif`, recorded from `scripts/record_demo.sh` against the
  demo image: a hung request, then `pidprobe snap | jq` naming the two
  deadlocked threads and the connection pool they stranded.

## [0.0.1] - 2026-08-04

### Added

- Placeholder release to reserve the package name on PyPI.

[Unreleased]: https://github.com/tomada1114/pidprobe/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tomada1114/pidprobe/releases/tag/v0.1.0
[0.0.1]: https://pypi.org/project/pidprobe/0.0.1/
