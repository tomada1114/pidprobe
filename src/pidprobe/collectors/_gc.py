"""Generation-by-generation garbage collector statistics."""

from __future__ import annotations

from ._base import Collector

# The number of generations is read from gc itself rather than assumed to be
# three: the free-threaded build does not use the same generation layout.
_SOURCE = """
import gc

counts = list(gc.get_count())
thresholds = list(gc.get_threshold())

generations = []
for index, stats in enumerate(gc.get_stats()):
    generation = {"generation": index}
    for key, value in stats.items():
        generation[str(key)] = value
    if index < len(counts):
        generation["count"] = counts[index]
    if index < len(thresholds):
        generation["threshold"] = thresholds[index]
    generations.append(generation)

data = {
    "enabled": gc.isenabled(),
    "counts": counts,
    "thresholds": thresholds,
    "generations": generations,
    "garbage_count": len(gc.garbage),
    "freeze_count": gc.get_freeze_count(),
}
"""

GC_COLLECTOR = Collector(
    name="gc",
    source=_SOURCE,
    description="Garbage collector counts, thresholds and per-generation stats",
)
