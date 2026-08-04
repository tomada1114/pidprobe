"""Target process with a thread blocked waiting on a held lock.

Run standalone with ``python tests/targets/lock.py``. Prints ``READY`` to
stdout once both threads are set up.

Thread A acquires ``LOCK`` and then sleeps forever while still holding it.
Thread B blocks trying to acquire that same lock. The main thread loops on
short sleeps. Intended for verifying that pidprobe's stack collector can
observe a thread that is blocked on a lock acquisition.
"""

from __future__ import annotations

import threading
import time

LOCK = threading.Lock()


def _hold_lock() -> None:
    with LOCK:
        while True:
            time.sleep(0.05)


def _wait_for_lock() -> None:
    with LOCK:
        pass


def _run() -> None:
    holder = threading.Thread(target=_hold_lock, daemon=True)
    holder.start()
    while not LOCK.locked():
        time.sleep(0.01)

    waiter = threading.Thread(target=_wait_for_lock, daemon=True)
    waiter.start()

    print("READY", flush=True)
    while True:
        time.sleep(0.05)


if __name__ == "__main__":
    _run()
