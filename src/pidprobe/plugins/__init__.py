"""Collector plugins that ship with pidprobe as worked examples.

These are ordinary plugins: they are published in the ``pidprobe.collectors``
entry point group and go through :func:`pidprobe.registry.discover_collectors`
exactly like a plugin from any other package, with no special-casing in the
core. Nothing here is imported by :mod:`pidprobe` itself, so a plugin module
may depend on whatever it likes without weighing down the package.
"""

from __future__ import annotations

from .sqlalchemy_pool import SQLALCHEMY_POOL_COLLECTOR

__all__ = ["SQLALCHEMY_POOL_COLLECTOR"]
