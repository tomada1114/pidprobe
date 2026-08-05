# pidprobe

Structured JSON snapshots of running CPython 3.14+ processes via PEP 768 - no agent, no restart, no gdb

## Installation

=== "pip"

    ```bash
    pip install pidprobe
    ```

=== "uv"

    ```bash
    uv add pidprobe
    ```

## Quick Example

```bash
pidprobe snap 12345 | jq '.stacks.threads[0].frames[0]'
```

```python
from pidprobe import take_snapshot

snapshot = take_snapshot(12345)
print(snapshot["meta"]["stop_duration_ms"])
```

## Next Steps

- [Getting Started](getting-started.md) — setup and first steps
- [API Reference](reference.md) — the commands, their options and the Python API
- [Output Schema](output-schema.md) — every field of the JSON pidprobe prints
- [Collector Plugins](plugins.md) — add your own snapshot section
- [Troubleshooting](troubleshooting.md) — one section per `pidprobe doctor` check
- [Contributing](contributing.md) — how to contribute
