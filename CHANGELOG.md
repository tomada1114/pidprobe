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

## [0.0.1] - 2026-08-04

### Added

- Placeholder release to reserve the package name on PyPI.

[Unreleased]: https://github.com/tomada1114/pidprobe/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/tomada1114/pidprobe/releases/tag/v0.0.1
