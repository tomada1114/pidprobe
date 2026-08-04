"""Target process that continuously leaks objects into a global list.

Run standalone with ``python tests/targets/leak.py``. Prints ``READY`` to
stdout once the leak loop starts.

A fresh ``LeakedObject`` instance is appended to the module-level ``LEAKED``
list every 0.01 seconds, without bound. Intended for exercising a future
``pidprobe diff`` command against two snapshots of a growing collection.
"""

from __future__ import annotations

import time


class LeakedObject:
    """A trivial instance accumulated to simulate an unbounded memory leak."""

    def __init__(self, index: int) -> None:
        self.index = index


LEAKED: list[LeakedObject] = []


def _run() -> None:
    print("READY", flush=True)
    index = 0
    while True:
        LEAKED.append(LeakedObject(index))
        index += 1
        time.sleep(0.01)


if __name__ == "__main__":
    _run()
