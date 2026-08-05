"""Target process holding a SQLAlchemy pool with more connections than it has.

Run standalone with ``python tests/targets/sqlalchemy_pool.py``. Prints
``READY`` to stdout once every connection is checked out.

``CHECKED_OUT`` exceeds ``POOL_SIZE``, so the pool is forced into overflow and
``Pool.overflow()`` reports a positive number rather than the negative one it
starts at. SQLite in memory keeps this dependency-free; the pool bookkeeping
the collector reads is the same for any database.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool

if TYPE_CHECKING:
    from sqlalchemy import Connection

POOL_SIZE = 5
MAX_OVERFLOW = 3
CHECKED_OUT = 7

ENGINE = create_engine(
    "sqlite://",
    poolclass=QueuePool,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
)
CHECKED_OUT_CONNECTIONS: list[Connection] = []


def _run() -> None:
    CHECKED_OUT_CONNECTIONS.extend(ENGINE.connect() for _ in range(CHECKED_OUT))
    print("READY", flush=True)
    while True:
        time.sleep(0.01)


if __name__ == "__main__":
    _run()
