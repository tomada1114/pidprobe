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

## [0.0.1] - 2026-08-04

### Added

- Placeholder release to reserve the package name on PyPI.

[Unreleased]: https://github.com/tomada1114/pidprobe/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/tomada1114/pidprobe/releases/tag/v0.0.1
