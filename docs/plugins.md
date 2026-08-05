# Writing a collector plugin

A collector adds one top-level section to every snapshot. Any installed
package can publish one, and pidprobe's own SQLAlchemy section is written
exactly this way -- there is no special case for it in the core. What the
section then looks like in the output is described in [Output
schema](output-schema.md).

## The one thing to understand first

A collector is **not** a function pidprobe calls. It is a piece of *source
text* that pidprobe composes into the script it injects into the target
process, so it runs inside the process you are probing:

* it may only use the standard library and whatever the **target** already
  imported -- your plugin's own dependencies are not there;
* it must assign a JSON-serializable value to the name `data`;
* it must not import into the target. Importing a package the target never
  asked for changes the process instead of observing it, so look modules up
  in `sys.modules` and report their absence rather than pulling them in.

## A complete collector in 30 lines

This is the SQLAlchemy pool collector, reduced to its essentials. It reports
every connection pool the target holds, with the numbers that answer "is the
pool exhausted right now?":

```python
from pidprobe import Collector

SQLALCHEMY_POOLS = Collector(
    name="sqlalchemy",
    source="""
import gc
import sys

pool_class = getattr(sys.modules.get("sqlalchemy.pool"), "Pool", None)
if not isinstance(pool_class, type):
    data = {"available": False, "pools": []}
else:
    data = {
        "available": True,
        "pools": [
            {
                "type": type(pool).__qualname__,
                "size": pool.size(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow(),
            }
            for pool in gc.get_objects()
            if isinstance(pool, pool_class) and hasattr(pool, "size")
        ],
    }
""",
    description="SQLAlchemy connection pools",
)
```

`gc.get_objects()` is the trick that makes this work without cooperation from
the application: a `Pool` is reachable from its `Engine`, but the `Engine`
lives wherever the application put it, and the garbage collector is the one
index of live objects that needs no such knowledge.

!!! note

    `hasattr(pool, "size")` skips the pool classes that have no queue behind
    them -- `NullPool` and `StaticPool` implement none of these accessors.
    The shipped collector reports those pools with `null` metrics instead, so
    a pool is never silently missing from the section.

## The collector contract

`Collector` is a frozen value object with exactly three string fields, and
each one has rules:

| Field | Rules |
| --- | --- |
| `name` | The snapshot key this collector fills. Must be a Python identifier, because it also names the generated function inside the target, and may not be `meta` or `schema_version`, which the snapshot format owns. A name a built-in or an earlier plugin already took is refused at discovery, so `stacks` always means what pidprobe documents. |
| `source` | Python source run as a *function body* inside the target. It must assign a JSON-serializable value to `data`. It may call `_pidprobe_target_frames()`, which returns `{thread_id: frame}` for the target's threads -- the one thing the injected script provides that the standard library does not. |
| `description` | One line, for documentation and diagnostics. |

Because each snippet becomes a function body, every name it binds is local to
that function: a collector cannot leak a name into the target's namespace, and
two collectors cannot collide over one. Nothing is shared between them either,
so a helper has to be defined in the snippet that uses it.

`Collector` is a convenience, not a requirement. `CollectorSpec` is the typed
protocol discovery accepts, so any object carrying `name`, `source` and
`description` strings qualifies -- discovery copies the three values into a
`Collector` of its own and validates them there.

!!! warning

    `source` runs in a process that is *stopped* while it runs, so its cost is
    the target's latency. Keep it to reading state. Anything that blocks --
    acquiring a lock the target may already hold, I/O, a network call -- risks
    deadlocking the process you were trying to observe, since the injected
    script may have interrupted the very thread holding what you want.

## Publishing it

Register the collector in your own `pyproject.toml`, in the
`pidprobe.collectors` entry point group -- the value of
`COLLECTOR_ENTRY_POINT_GROUP` in the public API. The entry point resolves
either to a collector or to a zero-argument callable returning one, so a
plugin may hand over a module-level constant or build its collector only when
it is asked for:

```toml
[project.entry-points."pidprobe.collectors"]
sqlalchemy = "my_package.collectors:SQLALCHEMY_POOLS"
```

Once the package is installed, `pidprobe snap <PID>` runs the collector after
the built-in ones and publishes its `data` as the `sqlalchemy` section:

```console
$ pidprobe snap 12345 --pretty | jq .sqlalchemy
{
  "available": true,
  "max_pools": 50,
  "pool_count": 1,
  "pools": [
    {
      "type": "sqlalchemy.pool.impl.QueuePool",
      "size": 5,
      "checked_out": 7,
      "checked_in": 0,
      "overflow": 2
    }
  ]
}
```

!!! warning

    `overflow` is SQLAlchemy's own signed counter, the one `Pool.status()`
    prints. It starts at minus the pool size and only turns positive once
    connections are handed out beyond the pool, so a *negative* overflow means
    the pool has never been full.

## Checking that it loaded

A plugin that cannot be loaded does not fail the snapshot -- one broken plugin
must not cost every other section -- so it is logged on the `pidprobe.registry`
logger and left out, and an entry point published under the wrong group is
never noticed at all. Two ways to see what pidprobe found, neither of which
touches a target:

```python
from pidprobe import available_collectors, discover_collectors

print([collector.name for collector in available_collectors()])
print([collector.name for collector in discover_collectors()])
```

`discover_collectors()` returns only the plugins, ordered by entry point name;
`available_collectors()` returns the built-ins in their output order followed
by those plugins, which is exactly what a snapshot runs.

```console
$ pidprobe doctor
  OK      collector_plugins     5 collectors will run: stacks, objects, gc, fds, sqlalchemy
```

`doctor` reports the same list, and turns what discovery logged into a warning
naming the plugin it skipped -- see
[`collector_plugins`](troubleshooting.md#collector_plugins). It is the quicker
answer to "why is my section not in the output?".

## Trying it without a target

A collector is source text, so it can be run in *this* process before it is
ever injected into another one. That is how pidprobe tests its own collectors,
and it is the fastest way to iterate on yours:

```python
import sys
import traceback

from pidprobe.collectors import compose_collector_source
from my_package.collectors import REDIS

namespace = {"_pidprobe_sys": sys, "_pidprobe_traceback": traceback}
exec(compose_collector_source([REDIS]), namespace)  # noqa: S102

payload = namespace["payload"]
print(payload["sections"]["redis"])
print(payload["collectors"])
```

`compose_collector_source()` builds what the injected script would run: the
per-collector functions plus the driver that times them and catches what they
raise. The two `_pidprobe_` names are what the injected script would otherwise
bind; add `"_pidprobe_target_frames"` to the namespace as well if your
collector walks stacks. `payload["collectors"]` carries the same per-collector
report a snapshot puts in `meta.collectors`, so a collector that raised shows
up there with its traceback instead of taking the process down.

## What the plugin API guarantees

| Failure | Cost |
| --- | --- |
| The plugin cannot be imported, or hands back something that is not a collector | The plugin is skipped and logged on the `pidprobe.registry` logger |
| Its `source` does not compile | Same -- it is compiled during discovery so it cannot break the injected script |
| It claims a name a built-in or an earlier plugin already took | Same |
| It raises *inside the target* | Its section becomes `null` and the reason lands in `meta.collectors` |

In every case the rest of the snapshot still comes back. A plugin can slow a
snapshot down -- `duration_ms` in `meta.collectors` is per collector, so it
will show up there -- but it cannot cost another collector its section.

## The shipped reference plugin

pidprobe registers the SQLAlchemy collector itself, so the `sqlalchemy`
section is in every snapshot with no extra install. In a target that never
imported SQLAlchemy it reports `"available": false` and an empty `pools` list,
which is the answer, not a failure.

`pidprobe[sqlalchemy]` installs SQLAlchemy alongside pidprobe. The collector
never needs it -- it reads the *target's* SQLAlchemy, not the prober's -- so
the extra is only for environments that run the probed application and
pidprobe together.

The full implementation, including the details this page leaves out, is in
[`src/pidprobe/plugins/sqlalchemy_pool.py`](https://github.com/tomada1114/pidprobe/blob/main/src/pidprobe/plugins/sqlalchemy_pool.py).
