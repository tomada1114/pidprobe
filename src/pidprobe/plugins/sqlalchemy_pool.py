"""Reference collector plugin: SQLAlchemy connection pools.

This is the worked example of a pidprobe plugin, and a useful section in its
own right: it answers "is the pool exhausted right now?" for a process that is
already running, without adding instrumentation to it.

Like every collector, :data:`SQLALCHEMY_POOL_COLLECTOR` carries *source text*
rather than a function. That source runs inside the target process, so this
module -- and pidprobe itself -- never imports SQLAlchemy. The source does not
import it either: it looks ``sqlalchemy.pool`` up in the target's
``sys.modules`` and reports ``available: false`` when it is not there, because
importing a package into a process that never asked for it would change the
target rather than observe it.
"""

from __future__ import annotations

from pidprobe.collectors import Collector

# Pools are found by walking the target's own heap: a Pool is reachable from
# its Engine, but the Engine may be held in any application-specific place,
# and gc is the one index of live objects that needs no such knowledge.
#
# `size`, `checkedout`, `checkedin` and `overflow` are QueuePool's public
# accessors; pools without a queue (NullPool, StaticPool, ...) implement none
# of them and report null rather than a made-up zero. `overflow` is
# SQLAlchemy's own signed counter -- it starts at minus the pool size and only
# turns positive once connections are handed out beyond it, which is exactly
# what Pool.status() prints.
_SOURCE = '''
import gc
import sys

MAX_POOLS = 50
POOL_MODULE = "sqlalchemy.pool"
METRICS = {
    "size": "size",
    "checked_out": "checkedout",
    "checked_in": "checkedin",
    "overflow": "overflow",
}


def measure(pool, accessor):
    """Return what a pool accessor reports, or None if it has none to give."""
    method = getattr(pool, accessor, None)
    if method is None:
        return None
    try:
        value = method()
    except Exception:
        # A pool mid-checkout can raise on any of these; one unreadable
        # metric must not cost the whole section.
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def describe(pool):
    kind = type(pool)
    entry = {"type": kind.__module__ + "." + kind.__qualname__}
    for key, accessor in METRICS.items():
        entry[key] = measure(pool, accessor)
    return entry


base = getattr(sys.modules.get(POOL_MODULE), "Pool", None)
if not isinstance(base, type):
    data = {"available": False, "max_pools": MAX_POOLS, "pool_count": 0, "pools": []}
else:
    entries = [
        describe(obj) for obj in gc.get_objects() if issubclass(type(obj), base)
    ]
    # Two pools of the same class are told apart by their numbers only, so
    # sorting on those is what makes the section stable across snapshots.
    entries.sort(
        key=lambda entry: (
            entry["type"],
            entry["size"] is None,
            entry["size"] or 0,
            entry["checked_out"] or 0,
        ),
    )
    data = {
        "available": True,
        "max_pools": MAX_POOLS,
        "pool_count": len(entries),
        "pools": entries[:MAX_POOLS],
    }
'''

SQLALCHEMY_POOL_COLLECTOR = Collector(
    name="sqlalchemy",
    source=_SOURCE,
    description="SQLAlchemy connection pools: size, checked-out count and overflow",
)
