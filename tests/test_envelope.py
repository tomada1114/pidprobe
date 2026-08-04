"""Tests for validating the envelope a target sends back."""

from __future__ import annotations

import json

import pytest

from pidprobe._envelope import parse_envelope
from pidprobe._errors import ChannelError


def test_parse_envelope_accepts_a_successful_payload() -> None:
    raw = json.dumps({"status": "ok", "error": None, "payload": {"frames": []}})

    envelope = parse_envelope(raw)

    assert envelope["status"] == "ok"
    assert envelope["error"] is None
    assert envelope["payload"] == {"frames": []}


def test_parse_envelope_normalizes_error_details() -> None:
    raw = json.dumps(
        {
            "status": "error",
            "error": {"type": "ValueError", "message": "boom", "traceback": "..."},
            "payload": None,
        },
    )

    envelope = parse_envelope(raw)

    assert envelope["status"] == "error"
    assert envelope["error"] == {
        "type": "ValueError",
        "message": "boom",
        "traceback": "...",
    }


def test_parse_envelope_fills_missing_error_members() -> None:
    raw = json.dumps({"status": "error", "error": {}, "payload": None})

    error = parse_envelope(raw)["error"]

    assert error == {"type": "UnknownError", "message": "", "traceback": ""}


@pytest.mark.parametrize(
    ("raw", "expected_message"),
    [
        pytest.param("not json", "malformed JSON", id="not-json"),
        pytest.param("[1, 2]", "expected a JSON object", id="not-an-object"),
        pytest.param("{}", "unknown status", id="missing-status"),
        pytest.param('{"status": "fine"}', "unknown status", id="bad-status"),
        pytest.param(
            '{"status": "error", "error": null}',
            "no error details",
            id="error-without-details",
        ),
        pytest.param(
            '{"status": "ok", "error": "boom"}',
            "must be an object or null",
            id="error-not-an-object",
        ),
        pytest.param(
            '{"status": "ok", "payload": [1]}',
            "must be an object or null",
            id="payload-not-an-object",
        ),
    ],
)
def test_parse_envelope_rejects_invalid_input(raw: str, expected_message: str) -> None:
    with pytest.raises(ChannelError, match=expected_message):
        parse_envelope(raw)
