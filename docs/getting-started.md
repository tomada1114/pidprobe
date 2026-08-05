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
    permissive `ptrace_scope` or `CAP_SYS_PTRACE` on Linux. When any of that
    is missing, pidprobe explains which one it was.

## What's Next?

See the [API Reference](reference.md) for the complete API documentation.
