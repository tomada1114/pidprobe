# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `pidprobe snap <PID>`: one JSON snapshot of a running CPython 3.14+ process,
  built from the `stacks`, `objects`, `gc` and `fds` collectors. Compact
  single-line output by default, `--pretty` for humans, `--timeout` for the
  probe budget.
- `take_snapshot()` and `snapshot_schema()` in the public API, plus the
  `snapshot.schema.json` JSON Schema describing the output format
  (`schema_version` 1.0).
- `TargetError` for failures raised inside the target process.
- Safe rendering of every value a snapshot reports: bounded in nesting depth,
  element count and length, and masking values whose name looks like a
  credential (`password`, `token`, `api_key`, ...) with `"<masked>"`. Masking
  runs inside the target, is on by default, is reported as
  `stacks.masking_enabled`, and is turned off with `pidprobe snap --no-mask`.
- `pidprobe eval <PID> <EXPR>` and `evaluate_in_target()`: evaluate one
  expression inside a running process and get its bounded, credential-masking
  repr back as JSON.
- Collector plugin API: any package can add a snapshot section by publishing an
  entry point in the `pidprobe.collectors` group. `CollectorSpec` is the typed
  contract, `Collector`, `available_collectors()` and `discover_collectors()`
  round out the public API, and a plugin that fails to load or raises inside
  the target costs only its own section.
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
- `pidprobe diff <PID> --interval S [--count N]`, plus `iter_snapshot_deltas()`
  and `diff_snapshots()`: repeated snapshots reduced to what changed between
  them -- object counts per type ranked by growth, GC generation statistics
  and the open file descriptor count. Each delta is printed as one JSON line
  as soon as it is ready, `--count N` yields `N - 1` deltas, and without
  `--count` the series runs until Ctrl-C ends it with exit code `130`. Only
  the `objects`, `gc` and `fds` collectors run, so a sample costs the target
  less than a full snapshot.
- `sqlalchemy` section: the reference collector plugin reports every
  connection pool the target holds with its size, checked-out count and
  overflow, and `"available": false` when the target never imported
  SQLAlchemy.
- Documented, stable exit codes so a script can act on *what* failed without
  parsing the message: `0` success, `1` an unclassified probe failure, `2` an
  invalid command line, `3` no such process, `4` attach refused, `5` timeout,
  `6` the injected code raised inside the target, `7` `doctor` found a
  blocking check, `70` a bug in pidprobe, `130` Ctrl-C, `141` a closed stdout
  pipe. `pidprobe --help` prints the table, and README and the reference
  document it.
- `NoSuchProcessError`, the `AttachError` subclass raised when the pid does
  not exist, so "the process is gone" can be told from "attaching was
  refused" both in Python and in the exit code.
- Global `pidprobe --timeout SECONDS` before the subcommand, overriding the
  5-second default for whichever command follows; the existing per-subcommand
  `--timeout` still works and wins over it.
- `pidprobe --debug`, and `PIDPROBE_DEBUG=1`, to re-raise an unexpected error
  with its traceback instead of summarising it.

### Changed

- **Breaking:** `pidprobe doctor` now exits `7` rather than `1` when a check
  blocks attaching. `1` no longer means "the diagnosis says no"; it means the
  probe failed for a reason with no code of its own.
- **Breaking:** a failing `snap`, `eval` or `diff` no longer always exits `1`.
  Each failure category now has its own code (`3`, `4`, `5`, `6`), so a script
  testing `$? -eq 1` must be updated to test for non-zero, or for the
  category it cares about.
- An unexpected error in pidprobe itself is now reported as a
  `pidprobe: internal error: ...` line and exit code `70` instead of a raw
  traceback; `--debug` restores the traceback.
- A closed stdout pipe -- `pidprobe snap PID | head -1` -- now exits `141`
  quietly instead of ending in the interpreter's "Exception ignored" notice.
- A failing `pidprobe doctor <PID>` no longer suggests running
  `pidprobe doctor <PID>`.

### Fixed

- The README collector plugin example no longer imports the library it reports
  on into the target process, which contradicted the plugin guide, and no
  longer reuses the section name of the shipped `sqlalchemy` plugin, which
  discovery would reject as a duplicate. The same example in the API
  reference is corrected too.
- The ruff pinned by pre-commit is back in step with the one in `uv.lock`, so
  the pre-commit hook and `just lint` no longer disagree about which rules
  apply.

## [0.0.1] - 2026-08-04

### Added

- Placeholder release to reserve the package name on PyPI.

[Unreleased]: https://github.com/tomada1114/pidprobe/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/tomada1114/pidprobe/releases/tag/v0.0.1
