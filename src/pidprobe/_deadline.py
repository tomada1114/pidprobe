"""Single hard time budget shared by every step of a probe.

Attaching to a live process has several places where it can wait forever: the
target may never reach a safe evaluation point, may connect and then stall, or
may never write its result file. All of them consult the same
:class:`Deadline`, so one probe can never exceed the budget the caller asked
for, and the resulting error always names that budget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ._errors import ProbeTimeoutError


@dataclass(frozen=True, slots=True)
class Deadline:
    """Absolute point in time at which a probe gives up.

    Attributes:
        budget_seconds: Total budget, kept for error messages.
        expires_at: :func:`time.monotonic` value at which the budget is spent.
        pid: Target process id, when known, for error messages.
    """

    budget_seconds: float
    expires_at: float
    pid: int | None = None

    @classmethod
    def start(cls, budget_seconds: float, pid: int | None = None) -> Deadline:
        """Start a budget that expires *budget_seconds* from now.

        Args:
            budget_seconds: Length of the budget.
            pid: Target process id, when known.

        Returns:
            A fresh deadline.
        """
        return cls(budget_seconds, time.monotonic() + budget_seconds, pid)

    @property
    def remaining(self) -> float:
        """Seconds left before the budget expires; negative once spent."""
        return self.expires_at - time.monotonic()

    @property
    def is_expired(self) -> bool:
        """Whether the budget is spent."""
        return self.remaining <= 0

    def timeout_error(self) -> ProbeTimeoutError:
        """Return the error to raise when this deadline expires."""
        return ProbeTimeoutError(self.budget_seconds, self.pid)
