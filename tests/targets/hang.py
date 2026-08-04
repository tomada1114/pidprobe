"""Target process that blocks the main thread inside a single long sleep.

Run standalone with ``python tests/targets/hang.py``. Prints ``READY`` to
stdout before blocking.

``sys.remote_exec`` only runs injected code at the next bytecode boundary.
A single ``time.sleep(3600)`` call blocks the interpreter inside one
bytecode instruction for the whole duration of the sleep, so this process
never reaches a safe execution point until it wakes up (or is killed).
Used to exercise pidprobe's timeout path.
"""

from __future__ import annotations

import time


def _run() -> None:
    print("READY", flush=True)
    time.sleep(3600)


if __name__ == "__main__":
    _run()
