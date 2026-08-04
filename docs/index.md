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

```python
from pidprobe import add

result = add(1, 2)
print(result)  # 3
```

## Next Steps

- [Getting Started](getting-started.md) — setup and first steps
- [API Reference](reference.md) — full API documentation
- [Contributing](contributing.md) — how to contribute
