"""The result ``pidprobe doctor`` produces, and how it is written out.

A diagnosis is a list of named checks rather than a pass/fail flag, because
the command exists to replace a bare ``PermissionError`` with something
actionable. That contract is enforced by the type: a check that failed or
warned cannot be built without a cause, a command the user can run to confirm
it, and a fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

_LABEL_WIDTH = 8
_NAME_WIDTH = 22
_DETAIL_INDENT = " " * 12


class CheckStatus(StrEnum):
    """Outcome of one diagnostic check.

    Attributes:
        OK: The condition attaching needs is satisfied.
        WARN: Attaching still works, but something is degraded or unverified.
        FAIL: Attaching cannot work until this is fixed.
        SKIPPED: The check does not apply here, typically on another platform.
    """

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"


_EXPLAINED = (CheckStatus.WARN, CheckStatus.FAIL)


@dataclass(frozen=True, slots=True)
class Check:
    """One diagnostic check and everything needed to act on it.

    Attributes:
        name: Stable identifier of the check, used as the JSON key.
        status: What the check concluded.
        summary: One line naming what was actually observed.
        cause: Why that blocks or degrades attaching.
        confirm: A shell command the user can run to see it independently.
        fix: The concrete change that resolves it.

    Raises:
        ValueError: If a warning or failure is built without a cause, a
            confirmation command and a fix -- the guarantee that no output
            path is ever a bare "Permission denied".
    """

    name: str
    status: CheckStatus
    summary: str
    cause: str = ""
    confirm: str = ""
    fix: str = ""

    def __post_init__(self) -> None:
        """Reject an unactionable warning or failure."""
        if self.status in _EXPLAINED and not all((self.cause, self.confirm, self.fix)):
            message = (
                f"check {self.name!r} is {self.status.value} and must carry a "
                f"cause, a confirmation command and a fix"
            )
            raise ValueError(message)

    def details(self) -> Iterator[tuple[str, str]]:
        """Yield the populated ``(label, text)`` pairs, in reporting order."""
        for label, text in (
            ("cause", self.cause),
            ("confirm", self.confirm),
            ("fix", self.fix),
        ):
            if text:
                yield label, text


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """Every check ``pidprobe doctor`` ran, and what they add up to.

    Attributes:
        pid: Target the target-specific checks ran against, or ``None`` when
            only the prober's own environment was examined.
        checks: The checks, in the order they were run.
    """

    pid: int | None
    checks: tuple[Check, ...]

    @property
    def failures(self) -> tuple[Check, ...]:
        """The checks that block attaching."""
        return self._with_status(CheckStatus.FAIL)

    @property
    def warnings(self) -> tuple[Check, ...]:
        """The checks that flagged something without blocking attaching."""
        return self._with_status(CheckStatus.WARN)

    @property
    def is_attachable(self) -> bool:
        """Whether nothing found would stop pidprobe from attaching."""
        return not self.failures

    def _with_status(self, status: CheckStatus) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.status is status)


def as_document(diagnosis: Diagnosis) -> dict[str, Any]:
    """Render a diagnosis as the JSON document ``--json`` prints.

    Returns:
        A mapping with ``pid``, an ``attachable`` verdict and one entry per
        check; empty explanation fields are omitted so a passing check stays
        a single short object.
    """
    return {
        "pid": diagnosis.pid,
        "attachable": diagnosis.is_attachable,
        "checks": [
            {
                "name": check.name,
                "status": check.status.value,
                "summary": check.summary,
                **dict(check.details()),
            }
            for check in diagnosis.checks
        ],
    }


def render_text(diagnosis: Diagnosis) -> str:
    """Render a diagnosis as the human-readable report ``doctor`` prints."""
    lines = [_headline(diagnosis), ""]
    for check in diagnosis.checks:
        status = check.status.value.upper()
        lines.append(
            f"  {status:<{_LABEL_WIDTH}}{check.name:<{_NAME_WIDTH}}{check.summary}"
        )
        lines.extend(
            f"{_DETAIL_INDENT}{label}: {text}" for label, text in check.details()
        )
    lines.extend(("", _verdict(diagnosis)))
    return "\n".join(lines) + "\n"


def _headline(diagnosis: Diagnosis) -> str:
    """Name what was examined."""
    if diagnosis.pid is None:
        return "pidprobe doctor: checking this environment (no pid given)"
    return f"pidprobe doctor: checking this environment against pid {diagnosis.pid}"


def _verdict(diagnosis: Diagnosis) -> str:
    """Summarize the outcome in one line."""
    target = "a target" if diagnosis.pid is None else f"pid {diagnosis.pid}"
    warnings = len(diagnosis.warnings)
    suffix = f" ({warnings} warning{'s' if warnings != 1 else ''})" if warnings else ""
    failures = len(diagnosis.failures)
    if not failures:
        return f"nothing blocks attaching to {target}{suffix}"
    checks = "check" if failures == 1 else "checks"
    return f"{failures} {checks} failed; attaching to {target} will not work{suffix}"
