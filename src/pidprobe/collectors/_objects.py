"""Object counts grouped by type, top N by count."""

from __future__ import annotations

from ._base import Collector

# Only GC-tracked containers are visible to gc.get_objects(): atomic values
# such as int and str are not counted. That is stated in the schema and the
# docs rather than repeated in every snapshot.
_SOURCE = """
import gc
from collections import Counter

TOP_N = 50
UNKNOWN_TYPE = "<unknown>"

counts = Counter()
for obj in gc.get_objects():
    kind = type(obj)
    try:
        module = kind.__module__
        qualname = kind.__qualname__
    except Exception:
        counts[UNKNOWN_TYPE] += 1
        continue
    if module in (None, "builtins"):
        counts[qualname] += 1
    else:
        counts[str(module) + "." + qualname] += 1

data = {
    "top_n": TOP_N,
    "total_tracked": sum(counts.values()),
    "distinct_types": len(counts),
    "top": [
        {"type": label, "count": count} for label, count in counts.most_common(TOP_N)
    ],
}
"""

OBJECTS_COLLECTOR = Collector(
    name="objects",
    source=_SOURCE,
    description="Counts of GC-tracked objects grouped by type, top N by count",
)
