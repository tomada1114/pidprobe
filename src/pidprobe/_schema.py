"""Access to the JSON Schema that describes a snapshot document.

The schema ships with the package so downstream consumers can validate
pidprobe output with the validator of their choice instead of guessing the
shape of the JSON.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

SCHEMA_VERSION = "1.0"
"""Version of the snapshot output format; also embedded in every snapshot."""

SCHEMA_FILENAME = "snapshot.schema.json"
"""Name of the schema file inside the installed package."""


def snapshot_schema() -> dict[str, Any]:
    """Return the JSON Schema for snapshot documents.

    Returns:
        A freshly parsed schema, so callers may modify it (to bundle it into
        a larger schema, for instance) without affecting anyone else.
    """
    text = resources.files(__package__).joinpath(SCHEMA_FILENAME).read_text("utf-8")
    # Any: a JSON Schema is an arbitrarily nested JSON document.
    schema: dict[str, Any] = json.loads(text)
    return schema
