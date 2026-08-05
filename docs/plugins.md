# Writing a collector plugin

A collector adds one top-level section to every snapshot. Any installed
package can publish one, and pidprobe's own SQLAlchemy section is written
exactly this way -- there is no special case for it in the core.

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

## Publishing it

Register the collector in your own `pyproject.toml`, in the
`pidprobe.collectors` entry point group. The entry point resolves either to a
collector or to a zero-argument callable returning one:

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
imported SQLAlchemy it reports `"available": false` and nothing else.

`pidprobe[sqlalchemy]` installs SQLAlchemy alongside pidprobe. The collector
never needs it -- it reads the *target's* SQLAlchemy, not the prober's -- so
the extra is only for environments that run the probed application and
pidprobe together.

The full implementation, including the details this page leaves out, is in
[`src/pidprobe/plugins/sqlalchemy_pool.py`](https://github.com/tomada1114/pidprobe/blob/main/src/pidprobe/plugins/sqlalchemy_pool.py).
