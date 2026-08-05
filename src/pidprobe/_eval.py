"""Evaluation of a single expression inside a live target process.

Unlike a snapshot, which composes the collector suite and isolates every
section from the others, an evaluation injects exactly one expression and has
nothing to fall back on: whatever the expression raises -- a ``SyntaxError``
from compiling it, a ``NameError`` from the target's namespace, or an
exception the expression itself provokes -- becomes the ``{status: "error"}``
envelope the injected script always sends. The prober turns that into a
:class:`~pidprobe._errors.TargetError`, so a failing expression costs an
error message rather than the return channel.

The result crosses the channel as text, not as data: it is rendered by the
same safe-repr rules the stacks collector applies, inside the target, so the
credential masking and the size bounds hold here exactly as they do for stack
locals.
"""

from __future__ import annotations

from typing import TypedDict

from ._channel import DEFAULT_TIMEOUT_SECONDS, execute_in_target
from ._envelope import payload_of
from ._saferepr import injected_source

_SAFEREPR_PLACEHOLDER = "__PIDPROBE_SAFEREPR__"
_EXPRESSION_PLACEHOLDER = "__PIDPROBE_EXPRESSION__"
_MASKING_PLACEHOLDER = "__PIDPROBE_IS_MASKED__"

_ACTION = "evaluation"

# The expression is masked by its own text: `api_key` reads as a credential
# name exactly like the local variable would, and the same greedy matching
# covers `os.environ["API_KEY"]`.
_SOURCE = """
__PIDPROBE_SAFEREPR__

EXPRESSION = __PIDPROBE_EXPRESSION__
IS_MASKED = __PIDPROBE_IS_MASKED__

# A *copy* of the target's __main__ namespace: the expression reads whatever
# the target's main module holds, while eval()'s own __builtins__ insertion
# and any walrus assignment land in the copy instead of the target's module
# dict. Copying can fail because other threads keep running while the target
# is stopped, and an empty namespace still evaluates literals.
try:
    namespace = dict(vars(_pidprobe_sys.modules["__main__"]))
except Exception:
    namespace = {}

value = eval(compile(EXPRESSION, "<pidprobe-eval>", "eval"), namespace)

payload = {
    "type": type(value).__name__,
    "result": safe_repr_named(EXPRESSION, value, is_masked=IS_MASKED),
}
"""


class Evaluation(TypedDict):
    """Result of evaluating one expression inside a target process.

    Attributes:
        pid: Process the expression was evaluated in.
        expression: Expression as it was handed to pidprobe.
        type: Name of the result's type, which survives masking.
        result: Safe-repr of the result, or ``"<masked>"`` when the
            expression reads as a credential name and masking is on.
        masking_enabled: Whether credential masking was applied.
    """

    pid: int
    expression: str
    type: str
    result: str
    masking_enabled: bool


def build_eval_source(expression: str, *, is_masked: bool = True) -> str:
    """Render the source that evaluates *expression* inside the target.

    Args:
        expression: Python expression to evaluate in the target's ``__main__``
            namespace. It is embedded as a string literal, so it is compiled
            in the target rather than spliced into the injected script.
        is_masked: Whether a result whose expression reads as a credential
            name is replaced with ``<masked>`` before it leaves the target.

    Returns:
        Source fulfilling the ``collector_source`` contract of
        :func:`pidprobe._inject.build_injection_script`: it assigns
        ``payload``.
    """
    # The expression goes in last: it is the only untrusted text here, and
    # substituting it after the others means it cannot spell a placeholder.
    return (
        _SOURCE.replace(_SAFEREPR_PLACEHOLDER, injected_source())
        .replace(_MASKING_PLACEHOLDER, repr(is_masked))
        .replace(_EXPRESSION_PLACEHOLDER, repr(expression))
    )


def evaluate_in_target(
    pid: int,
    expression: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    is_masked: bool = True,
    allow_socket: bool = True,
) -> Evaluation:
    """Evaluate one expression inside a running CPython 3.14+ process.

    Args:
        pid: Target process id.
        expression: Python expression evaluated against a copy of the
            target's ``__main__`` namespace. Statements are rejected by the
            target's own compiler, so an evaluation cannot rebind a name the
            target holds.
        timeout_seconds: Hard budget covering injection and read-back.
        is_masked: Set to ``False`` to receive credential-like results as
            they are; masking is otherwise applied inside the target.
        allow_socket: Set to ``False`` to force the tempfile return channel.

    Returns:
        The evaluation document, ready to be serialized to JSON.

    Raises:
        AttachError: If the target refuses the injection.
        ProbeTimeoutError: If the target never reaches a safe evaluation
            point within the budget.
        ChannelError: If the answer does not match the envelope contract.
        TargetError: If compiling or evaluating the expression raised inside
            the target.
    """
    envelope = execute_in_target(
        pid,
        build_eval_source(expression, is_masked=is_masked),
        timeout_seconds=timeout_seconds,
        allow_socket=allow_socket,
    )
    payload = payload_of(pid, envelope, action=_ACTION)
    return Evaluation(
        pid=pid,
        expression=expression,
        # Only the two rendered strings come from the target; everything else
        # is what the prober asked for and needs no trusting back.
        type=str(payload.get("type", "")),
        result=str(payload.get("result", "")),
        masking_enabled=is_masked,
    )
