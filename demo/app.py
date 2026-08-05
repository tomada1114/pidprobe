"""Intentionally broken FastAPI application, used to demonstrate pidprobe.

Two failures are wired in on purpose, each reachable over HTTP:

* a **lock-order deadlock** between a background ledger writer and the report
  endpoint. Once armed, ``GET /reports/{name}`` never returns, and the two
  stuck threads hold a SQLAlchemy connection each that the pool never gets
  back;
* a **memory leak** in a background thread that keeps every report it ever
  built, so :class:`LeakedRecord` counts climb for as long as it runs.

Nothing here imports or cooperates with pidprobe. That is the point: the
process is started the way any application is, and pidprobe learns all of
this from the outside.

``dict[str, Any]`` is used for every response body because FastAPI turns the
return annotation into a response model, and these handlers return plain
JSON-ish documents rather than a fixed schema.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

DATABASE_URL = "sqlite:////app/demo.db"
POOL_SIZE = 5
MAX_OVERFLOW = 2
WIDGET_COUNT = 20
LEAK_INTERVAL_SECONDS = 0.02
ARM_TIMEOUT_SECONDS = 5.0

# A file-backed SQLite URL gives QueuePool, which is the pool that actually
# reports size/checkedout/overflow -- the numbers pidprobe's SQLAlchemy
# collector reads out of the running process.
engine = create_engine(
    DATABASE_URL,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    connect_args={"check_same_thread": False},
)

_INVENTORY_LOCK = threading.Lock()
_LEDGER_LOCK = threading.Lock()
_LEDGER_ARMED = threading.Event()
_INVENTORY_TAKEN = threading.Event()
_LEAK_STOP = threading.Event()


@dataclass(slots=True)
class LeakedRecord:
    """One built report that the cache was supposed to evict, and never does."""

    index: int
    rows: list[tuple[int, str]]


@dataclass(slots=True)
class DemoState:
    """Everything the endpoints mutate, in one place instead of in globals."""

    deadlock_thread: threading.Thread | None = None
    leak_thread: threading.Thread | None = None
    widget_rows: list[tuple[int, str]] = field(default_factory=list)
    leaked: list[LeakedRecord] = field(default_factory=list)


STATE = DemoState()


def _ledger_writer() -> None:
    """Take the ledger lock, then block forever on the inventory lock.

    This is one half of a lock-order inversion: it holds LEDGER and wants
    INVENTORY, while :func:`read_report` holds INVENTORY and wants LEDGER.
    The waits on the two events make the interleaving deterministic, so the
    demo deadlocks every time instead of most of the time.

    The connection is checked out *before* the locks so that the deadlock
    also strands a pool connection, which is what makes the SQLAlchemy
    section of a snapshot interesting.
    """
    with engine.connect() as connection:
        connection.execute(text("SELECT COUNT(*) FROM widgets"))
        with _LEDGER_LOCK:
            _LEDGER_ARMED.set()
            _INVENTORY_TAKEN.wait()
            with _INVENTORY_LOCK:
                pass


def _leak_worker() -> None:
    """Append a report to a cache that is never bounded and never evicted."""
    index = 0
    while not _LEAK_STOP.is_set():
        STATE.leaked.append(LeakedRecord(index, list(STATE.widget_rows)))
        index += 1
        _LEAK_STOP.wait(LEAK_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Recreate the demo table and cache its rows before serving traffic."""
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS widgets"))
        connection.execute(
            text("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"),
        )
        connection.execute(
            text("INSERT INTO widgets (id, name) VALUES (:id, :name)"),
            [
                {"id": number, "name": f"widget-{number:03d}"}
                for number in range(1, WIDGET_COUNT + 1)
            ],
        )
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT id, name FROM widgets")).all()
    STATE.widget_rows = [(int(row[0]), str(row[1])) for row in rows]
    yield


app = FastAPI(
    title="pidprobe demo",
    description="A FastAPI application that deadlocks and leaks on request.",
    lifespan=lifespan,
)


@app.get("/")
def read_index() -> dict[str, Any]:
    """List what this application can be asked to break."""
    return {
        "service": "pidprobe demo",
        "endpoints": {
            "GET /health": "liveness",
            "GET /widgets": "a query that works",
            "GET /reports/{name}": "works, until the deadlock is armed",
            "POST /deadlock": "arm the lock-order deadlock",
            "POST /leak/start": "start leaking report objects",
            "POST /leak/stop": "stop leaking (already-leaked objects stay)",
            "GET /status": "what is currently broken",
        },
    }


@app.get("/health")
def read_health() -> dict[str, Any]:
    """Report liveness. Stays healthy even while the app is deadlocked."""
    return {"status": "ok"}


@app.get("/widgets")
def read_widgets() -> dict[str, Any]:
    """Run a normal query against the pool, touching neither lock."""
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT id, name FROM widgets")).all()
    return {"count": len(rows), "widgets": [row[1] for row in rows[:5]]}


@app.get("/reports/{name}")
def read_report(name: str) -> dict[str, Any]:
    """Build a report -- and hang forever once ``POST /deadlock`` has run.

    Before arming, both locks are free and this returns normally. After
    arming, it takes INVENTORY, hands the ledger writer its cue, and then
    waits for LEDGER, which the ledger writer will never release.
    """
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT id, name FROM widgets")).all()
        with _INVENTORY_LOCK:
            _INVENTORY_TAKEN.set()
            with _LEDGER_LOCK:
                return {"report": name, "widgets": len(rows)}


@app.post("/deadlock")
def arm_deadlock() -> dict[str, Any]:
    """Start the ledger writer that holds one half of the lock pair.

    Returns once the writer is holding LEDGER, so the very next request to
    ``GET /reports/{name}`` is guaranteed to deadlock rather than merely
    likely to.
    """
    thread = STATE.deadlock_thread
    if thread is not None and thread.is_alive():
        return {"armed": True, "thread": thread.name, "detail": "already armed"}
    _LEDGER_ARMED.clear()
    _INVENTORY_TAKEN.clear()
    thread = threading.Thread(target=_ledger_writer, name="ledger-writer", daemon=True)
    STATE.deadlock_thread = thread
    thread.start()
    _LEDGER_ARMED.wait(ARM_TIMEOUT_SECONDS)
    return {
        "armed": _LEDGER_ARMED.is_set(),
        "thread": thread.name,
        "detail": "GET /reports/{name} will now block forever",
    }


@app.post("/leak/start")
def start_leak() -> dict[str, Any]:
    """Start the background thread that grows the unbounded report cache."""
    thread = STATE.leak_thread
    if thread is not None and thread.is_alive():
        return {"leaking": True, "thread": thread.name, "detail": "already running"}
    _LEAK_STOP.clear()
    thread = threading.Thread(target=_leak_worker, name="report-cache", daemon=True)
    STATE.leak_thread = thread
    thread.start()
    return {
        "leaking": True,
        "thread": thread.name,
        "interval_seconds": LEAK_INTERVAL_SECONDS,
    }


@app.post("/leak/stop")
def stop_leak() -> dict[str, Any]:
    """Stop growing the cache. What already leaked is deliberately kept."""
    _LEAK_STOP.set()
    return {"leaking": False, "leaked_records": len(STATE.leaked)}


@app.get("/status")
def read_status() -> dict[str, Any]:
    """Report what is currently broken, for comparison with a snapshot."""
    leak_thread = STATE.leak_thread
    return {
        "deadlock_armed": _LEDGER_ARMED.is_set(),
        "leaking": leak_thread is not None and leak_thread.is_alive(),
        "leaked_records": len(STATE.leaked),
        "pool": engine.pool.status(),
    }
