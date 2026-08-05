"""Bounded, secret-masking rendering of values read out of a live process.

Every value pidprobe shows belongs to somebody else's process, so rendering it
has to survive whatever that process holds: a ``__repr__`` that raises, a list
with a million elements, a structure that nests into itself, a container
another thread is mutating right now, or a credential sitting in a local
variable. :func:`safe_repr` is therefore bounded in depth, element count and
length, and :func:`safe_repr_named` replaces values whose *name* looks like a
credential with :data:`MASK_PLACEHOLDER` unless masking is turned off.

Rendering happens inside the target process, embedded in the injected script.
Rather than keeping a second copy of these rules as a source string,
:func:`injected_source` returns this module's own source below the injection
marker. Everything below that marker is written to be valid both as part of
this (linted, type-checked) module and as an indented block inside the
injected script, which is why it imports nothing and refers to nothing outside
itself.
"""

from __future__ import annotations

import inspect
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_INJECTION_MARKER = "# --- injected below ---"


def injected_source() -> str:
    """Return the part of this module that runs inside the target process.

    Returns:
        Python source with no imports and no package-relative references, so
        it can be embedded verbatim -- and indented -- into a collector.

    Raises:
        RuntimeError: If this module lost its injection marker.
    """
    source = inspect.getsource(sys.modules[__name__])
    # rpartition, because the marker's own definition above is an earlier
    # occurrence of the same text.
    _, marker, body = source.rpartition(_INJECTION_MARKER)
    if not marker:  # pragma: no cover -- the marker is part of this file
        message = "the safe-repr injection marker is missing from _saferepr.py"
        raise RuntimeError(message)
    return body.strip("\n")


# --- injected below ---

MAX_DEPTH = 3
"""Container levels rendered before the nesting below them is elided."""

MAX_ITEMS = 10
"""Elements rendered per container before the rest is elided."""

MAX_STRING_CHARS = 200
"""Characters kept from a single ``repr()`` before it is truncated."""

MAX_TOTAL_CHARS = 2000
"""Characters kept from one fully rendered value, however deeply nested."""

MASK_PLACEHOLDER = "<masked>"
"""Stand-in for a value whose name looks like a credential."""

TRUNCATION_MARKER = "...<truncated>"
"""Suffix marking output that a length bound cut off."""

ELISION = "..."
"""Stand-in for the elements or nesting levels that were left out."""

SECRET_NAME_PATTERNS = (
    "password",
    "passwd",
    "passphrase",
    "pwd",
    "secret",
    "token",
    "apikey",
    "accesskey",
    "privatekey",
    "credential",
    "authorization",
)
"""Substrings that mark a name as holding a credential.

They are matched against the name with case and separators removed, so
``API_KEY``, ``api_key`` and ``apiKey`` all match ``apikey``. The list is
deliberately greedy: over-masking costs one debugging round trip, while
under-masking leaks a credential into a snapshot that ends up pasted into an
issue tracker.
"""


def is_secret_name(name: str) -> bool:
    """Return whether *name* looks like it holds a credential."""
    normalized = "".join(char for char in name.lower() if char.isalnum())
    return any(pattern in normalized for pattern in SECRET_NAME_PATTERNS)


def safe_repr(value: object, *, is_masked: bool = True) -> str:
    """Render *value* within the depth, element count and length bounds.

    Args:
        value: Any object, including one whose own ``__repr__`` raises.
        is_masked: Whether mapping values stored under a secret-like key are
            replaced with :data:`MASK_PLACEHOLDER`.

    Returns:
        A rendering of at most :data:`MAX_TOTAL_CHARS` characters that never
        raises, whatever *value* does.
    """
    return _bounded(_render(value, MAX_DEPTH, is_masked), MAX_TOTAL_CHARS)


def safe_repr_named(name: str, value: object, *, is_masked: bool = True) -> str:
    """Render a named value, masking it when the name looks like a credential.

    Args:
        name: Name the value is bound to, such as a local variable name.
        value: Value bound to *name*.
        is_masked: Set to ``False`` to render credentials as they are.

    Returns:
        :data:`MASK_PLACEHOLDER` when masking applies, otherwise the same
        rendering :func:`safe_repr` produces.
    """
    if is_masked and is_secret_name(name):
        return MASK_PLACEHOLDER
    return safe_repr(value, is_masked=is_masked)


def _render(value: object, depth: int, is_masked: bool) -> str:
    """Render one value, recursing at most *depth* container levels deep."""
    if isinstance(value, dict):
        return _render_mapping(value, depth, is_masked)
    if isinstance(value, list):
        return _render_items(value, depth, is_masked, ("[", "]"))
    if isinstance(value, tuple):
        return _render_items(value, depth, is_masked, ("(", ")"))
    if isinstance(value, frozenset):
        return _render_items(value, depth, is_masked, ("frozenset({", "})"))
    if isinstance(value, set):
        return _render_items(value, depth, is_masked, ("{", "}"))
    return _bounded_repr(value)


def _render_items(
    value: Iterable[object],
    depth: int,
    is_masked: bool,
    style: tuple[str, str],
) -> str:
    """Render a sequence or set within the element count and depth bounds."""
    opening, closing = style
    if depth <= 0:
        return opening + ELISION + closing
    try:
        items, has_more = _take(value, MAX_ITEMS)
    except Exception as exc:
        # Another thread keeps running while the target is stopped, so it can
        # mutate this container mid-iteration.
        return _failure(value, exc)
    if not items and not has_more:
        return _bounded_repr(value)
    parts = [_render(item, depth - 1, is_masked) for item in items]
    if has_more:
        parts.append(ELISION)
    body = ", ".join(parts)
    if isinstance(value, tuple) and len(parts) == 1 and not has_more:
        body += ","
    return opening + body + closing


def _render_mapping(value: dict[object, object], depth: int, is_masked: bool) -> str:
    """Render a mapping, masking values stored under secret-like keys."""
    if depth <= 0:
        return "{" + ELISION + "}"
    try:
        items, has_more = _take(value.items(), MAX_ITEMS)
    except Exception as exc:
        return _failure(value, exc)
    if not items and not has_more:
        return _bounded_repr(value)
    parts = [_render_pair(key, item, depth, is_masked) for key, item in items]
    if has_more:
        parts.append(ELISION)
    return "{" + ", ".join(parts) + "}"


def _render_pair(key: object, item: object, depth: int, is_masked: bool) -> str:
    """Render one mapping entry, masking the value under a secret-like key."""
    rendered_key = _render(key, depth - 1, is_masked)
    if is_masked and isinstance(key, str) and is_secret_name(key):
        return rendered_key + ": " + MASK_PLACEHOLDER
    return rendered_key + ": " + _render(item, depth - 1, is_masked)


def _take[T](iterable: Iterable[T], limit: int) -> tuple[list[T], bool]:
    """Return up to *limit* items and whether the iterable held more.

    Iterating in place rather than materializing the container keeps a
    million-element list from being copied inside the target process.
    """
    items: list[T] = []
    for item in iterable:
        if len(items) >= limit:
            return items, True
        items.append(item)
    return items, False


def _bounded_repr(value: object) -> str:
    """Return ``repr(value)``, truncated to the single-value length bound."""
    try:
        text = repr(value)
    except Exception as exc:
        return _failure(value, exc)
    return _bounded(text, MAX_STRING_CHARS)


def _bounded(text: str, limit: int) -> str:
    """Truncate *text* to *limit* characters, marking that it was cut."""
    if len(text) > limit:
        return text[:limit] + TRUNCATION_MARKER
    return text


def _failure(value: object, exc: BaseException) -> str:
    """Describe a value that could not be rendered at all."""
    return "<unrepresentable " + type(value).__name__ + ": " + type(exc).__name__ + ">"
