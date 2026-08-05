"""Happy-path target process: a tight sleep loop with known state.

Run standalone with ``python tests/targets/sleeper.py``. Prints ``READY`` to
stdout once the main loop starts, so a test harness can synchronize before
attaching to this process via ``sys.remote_exec``.

The sleep interval is short (as opposed to a single long sleep) so that
``sys.remote_exec`` -- which only runs injected code at the next bytecode
boundary -- gets frequent opportunities to run without a long wait.
"""

from __future__ import annotations

import time

# Module-level state, so `pidprobe eval` has something to resolve in this
# target's __main__ namespace. The credential-like name is what the masking
# tests evaluate; the value is fake.
INVENTORY = {"widgets": 3, "gadgets": 7}
api_key = "not-a-real-secret"


def _run() -> None:
    marker = "pidprobe-sleeper"
    state: dict[str, object] = {"label": marker, "tick": 0}
    tick = 0
    print("READY", flush=True)
    while True:
        tick += 1
        state["tick"] = tick
        time.sleep(0.05)


if __name__ == "__main__":
    _run()
