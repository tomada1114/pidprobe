"""Per-thread call stacks with file, line, function and local variables."""

from __future__ import annotations

from ._base import Collector

_SOURCE = '''
import threading

MAX_FRAME_DEPTH = 128
MAX_REPR_CHARS = 200
TRUNCATION_MARKER = "...<truncated>"


def describe(value):
    """Bounded repr of a local variable.

    repr() runs the target's own __repr__, which may raise or be huge, so
    both outcomes are contained here.
    """
    try:
        text = repr(value)
    except Exception as exc:
        return "<unrepresentable " + type(value).__name__ + ": " + type(exc).__name__ + ">"
    if len(text) > MAX_REPR_CHARS:
        return text[:MAX_REPR_CHARS] + TRUNCATION_MARKER
    return text


def frame_locals(frame):
    try:
        items = dict(frame.f_locals)
    except Exception:
        return None
    return {str(name): describe(value) for name, value in items.items()}


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
    "threads": threads,
}
'''

STACKS_COLLECTOR = Collector(
    name="stacks",
    source=_SOURCE,
    description="Per-thread call stacks with file, line, function and locals",
)
