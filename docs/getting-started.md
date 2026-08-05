# Getting Started

## Installation

```bash
pip install pidprobe
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add pidprobe
```

## Basic Usage

Take one snapshot of a running CPython 3.14+ process:

```bash
pidprobe snap 12345 | jq .meta
pidprobe snap 12345 --pretty
```

!!! tip

    Locals whose name looks like a credential (`password`, `token`,
    `api_key`, ...) come back as `"<masked>"`. Masking is on by default and
    happens inside the target process; `--no-mask` turns it off. See
    [Secret masking](reference.md#secret-masking).

The same from Python:

```python
from pidprobe import take_snapshot

snapshot = take_snapshot(12345)
for thread in snapshot["stacks"]["threads"]:
    print(thread["name"], thread["frames"][0]["function"])
```

!!! warning

    The target process must run CPython 3.14+ with remote debugging enabled,
    and the operating system must allow attaching to it: root on macOS, a
    permissive `ptrace_scope` or `CAP_SYS_PTRACE` on Linux. Run
    [`pidprobe doctor 12345`](reference.md#command-line) to find out which of
    those applies before probing -- it checks without attaching, and every
    failing check comes with the command to confirm it and the fix.
    [Troubleshooting](troubleshooting.md) explains each one at length.

## What's Next?

- [API Reference](reference.md) — every command, option and exit code
- [Output Schema](output-schema.md) — what the JSON contains, field by field
- [Collector Plugins](plugins.md) — add a section of your own
- [Troubleshooting](troubleshooting.md) — when `doctor` says no
