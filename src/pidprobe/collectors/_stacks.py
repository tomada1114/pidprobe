"""Per-thread call stacks with file, line, function and local variables."""

from __future__ import annotations

from .._saferepr import injected_source
from ._base import Collector

_SAFEREPR_PLACEHOLDER = "__PIDPROBE_SAFEREPR__"
_MASKING_PLACEHOLDER = "__PIDPROBE_IS_MASKED__"

_DESCRIPTION = "Per-thread call stacks with file, line, function and locals"

# Locals are rendered by the safe-repr rules, which are spliced in verbatim so
# the target applies exactly the bounds and masking this package documents.
_SOURCE = """
import threading

__PIDPROBE_SAFEREPR__

MAX_FRAME_DEPTH = 128
IS_MASKED = __PIDPROBE_IS_MASKED__


def frame_locals(frame):
    try:
        items = dict(frame.f_locals)
    except Exception:
        return None
    return {
        str(name): safe_repr_named(str(name), value, is_masked=IS_MASKED)
        for name, value in items.items()
    }


def walk(top):
    frames = []
    frame = top
    truncated = False
    while frame is not None:
        if len(frames) >= MAX_FRAME_DEPTH:
            truncated = True
            break
        code = frame.f_code
        frames.append(
            {
                "file": code.co_filename,
                "line": frame.f_lineno,
                "function": code.co_qualname,
                "locals": frame_locals(frame),
            }
        )
        frame = frame.f_back
    return frames, truncated


# threading._active is read without holding _active_limbo_lock on purpose:
# the injected script runs at an arbitrary bytecode boundary, so the thread
# it interrupted may already hold that lock, and acquiring it here would
# deadlock the target. A missing thread name is the lesser evil.
descriptors = {}
try:
    for ident, thread in list(threading._active.items()):
        descriptors[ident] = (thread.name, thread.daemon)
except Exception:
    descriptors = {}

main_ident = threading.main_thread().ident

threads = []
for thread_id, top_frame in _pidprobe_target_frames().items():
    frames, truncated = walk(top_frame)
    name, daemon = descriptors.get(thread_id, (None, None))
    threads.append(
        {
            "thread_id": thread_id,
            "name": name,
            "daemon": daemon,
            "is_main": thread_id == main_ident,
            "frames": frames,
            "frames_truncated": truncated,
        }
    )
threads.sort(key=lambda entry: (not entry["is_main"], entry["thread_id"]))

data = {
    "thread_count": len(threads),
    "max_frame_depth": MAX_FRAME_DEPTH,
    "masking_enabled": IS_MASKED,
    "threads": threads,
}
"""


def build_stacks_collector(*, is_masked: bool = True) -> Collector:
    """Build the stacks collector with or without secret masking.

    Args:
        is_masked: Whether locals whose name looks like a credential are
            replaced with ``<masked>`` inside the target, before the value
            ever crosses the return channel.

    Returns:
        A collector carrying its own copy of the safe-repr rules, so masking
        is decided when the script is generated and cannot be changed by the
        target.
    """
    source = _SOURCE.replace(_SAFEREPR_PLACEHOLDER, injected_source()).replace(
        _MASKING_PLACEHOLDER,
        repr(is_masked),
    )
    return Collector(name="stacks", source=source, description=_DESCRIPTION)


STACKS_COLLECTOR = build_stacks_collector()
"""Stacks collector as every snapshot runs it: with masking enabled."""
