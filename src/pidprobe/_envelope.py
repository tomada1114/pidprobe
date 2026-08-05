"""Wire format exchanged between the target process and the prober.

The injected script answers exactly once with a JSON object shaped like
``{"status": "ok"|"error", "error": {...}|null, "payload": {...}|null}``.
Everything crossing the return channel is untrusted input produced by another
interpreter, so it is validated here before the rest of pidprobe sees it.
"""

from __future__ import annotations

import json
from typing import Any, Literal, TypedDict

from ._errors import ChannelError, TargetError

_VALID_STATUSES = frozenset({"ok", "error"})
_UNKNOWN_ERROR_TYPE = "UnknownError"


class ErrorInfo(TypedDict):
    """Structured description of a failure that happened inside the target."""

    type: str
    message: str
    traceback: str


class Envelope(TypedDict):
    """Single answer to a probe.

    Attributes:
        status: ``"ok"`` when the collector produced a payload, ``"error"``
            when it raised inside the target.
        error: Failure details when ``status`` is ``"error"``.
        payload: Collector output when ``status`` is ``"ok"``.
    """

    status: Literal["ok", "error"]
    error: ErrorInfo | None
    payload: dict[str, Any] | None


def _parse_error_info(status: object, raw_error: object) -> ErrorInfo | None:
    """Normalize the ``error`` member of an envelope.

    Args:
        status: Already validated envelope status.
        raw_error: Value the target sent under ``"error"``.

    Returns:
        The normalized error details, or ``None`` for a successful envelope.

    Raises:
        ChannelError: If the value does not match the contract.
    """
    if raw_error is None:
        if status == "error":
            message = "envelope has status 'error' but no error details"
            raise ChannelError(message)
        return None
    if not isinstance(raw_error, dict):
        message = f"envelope 'error' must be an object or null, got {raw_error!r}"
        raise ChannelError(message)
    return ErrorInfo(
        type=str(raw_error.get("type", _UNKNOWN_ERROR_TYPE)),
        message=str(raw_error.get("message", "")),
        traceback=str(raw_error.get("traceback", "")),
    )


def parse_envelope(raw: str) -> Envelope:
    """Validate the JSON text a target sent and return it as an envelope.

    Args:
        raw: Raw JSON text received over the channel.

    Returns:
        The validated envelope.

    Raises:
        ChannelError: If the text is not JSON or does not match the
            ``{status, error, payload}`` contract.
    """
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        message = f"target sent malformed JSON: {exc}"
        raise ChannelError(message) from exc
    if not isinstance(decoded, dict):
        message = f"expected a JSON object envelope, got {type(decoded).__name__}"
        raise ChannelError(message)
    status = decoded.get("status")
    if status not in _VALID_STATUSES:
        message = f"envelope has an unknown status: {status!r}"
        raise ChannelError(message)
    error = _parse_error_info(status, decoded.get("error"))
    payload = decoded.get("payload")
    if payload is not None and not isinstance(payload, dict):
        message = f"envelope 'payload' must be an object or null, got {payload!r}"
        raise ChannelError(message)
    return Envelope(status=status, error=error, payload=payload)


def payload_of(
    pid: int, envelope: Envelope, *, action: str = "snapshot"
) -> dict[str, Any]:
    """Return the payload of a successful envelope.

    Args:
        pid: Process the envelope came from.
        envelope: Answer already validated by :func:`parse_envelope`.
        action: What the injected script was asked to do; it names the
            failure in the raised message.

    Returns:
        The payload the target produced.

    Raises:
        TargetError: If the target reported a failure.
        ChannelError: If the target reported success without a payload.
    """
    if envelope["status"] == "error":
        error: ErrorInfo | None = envelope["error"]
        if error is None:  # pragma: no cover -- parse_envelope rejects this
            message = f"target reported an error without details for pid {pid}"
            raise ChannelError(message)
        raise TargetError(pid, error, action=action)
    payload = envelope["payload"]
    if payload is None:
        message = f"target reported success without a payload for pid {pid}"
        raise ChannelError(message)
    return payload
