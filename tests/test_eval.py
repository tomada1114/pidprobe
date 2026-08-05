"""Tests for evaluating one expression inside a target process.

The generated source is exercised without ``sys.remote_exec`` in two ways: run
in this process (which is what the target does with it), and run as the full
injected script in a fresh interpreter while a channel listens, which is the
only way to observe the envelope a failing expression produces. Attaching to a
real process is covered by ``test_eval_integration.py``.
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest

from pidprobe import ChannelError, Evaluation, TargetError, evaluate_in_target
from pidprobe import _eval as eval_module
from pidprobe._channel import open_channel
from pidprobe._deadline import Deadline
from pidprobe._envelope import Envelope, ErrorInfo, parse_envelope
from pidprobe._eval import build_eval_source
from pidprobe._inject import ChannelSpec, write_injection_script
from pidprobe._saferepr import MASK_PLACEHOLDER, TRUNCATION_MARKER

from .conftest import run_collector_source

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pidprobe._channel import ReturnChannel

SCRIPT_TIMEOUT_SECONDS = 30.0
RECEIVE_TIMEOUT_SECONDS = 10.0
PID = 4321


@pytest.fixture
def target_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give this process the ``__main__`` names the expressions resolve.

    The generated source reads the target's ``__main__`` namespace, which is
    pytest's own module while these tests run.
    """
    main_module = sys.modules["__main__"]
    monkeypatch.setattr(main_module, "inventory", {"widgets": 3}, raising=False)
    monkeypatch.setattr(main_module, "api_key", "not-a-real-secret", raising=False)
    monkeypatch.setattr(
        main_module,
        "config",
        {"api_key": "not-a-real-secret", "host": "db"},
        raising=False,
    )


def evaluated(expression: str, *, is_masked: bool = True) -> dict[str, Any]:
    """Run the generated source here and return the payload it assigns."""
    return run_collector_source(build_eval_source(expression, is_masked=is_masked))


@pytest.mark.usefixtures("target_globals")
class TestGeneratedSource:
    def test_expression_is_evaluated_in_the_target_namespace(self):
        assert evaluated("inventory") == {"type": "dict", "result": "{'widgets': 3}"}

    def test_literal_expression_needs_no_target_state(self):
        assert evaluated("1 + 1") == {"type": "int", "result": "2"}

    def test_credential_like_expression_is_masked_by_default(self):
        assert evaluated("api_key") == {"type": "str", "result": MASK_PLACEHOLDER}

    def test_no_mask_returns_the_credential_as_it_is(self):
        result = evaluated("api_key", is_masked=False)

        assert result == {"type": "str", "result": "'not-a-real-secret'"}

    def test_secret_key_inside_a_result_is_masked_too(self):
        result = evaluated("config")

        assert result["result"] == f"{{'api_key': {MASK_PLACEHOLDER}, 'host': 'db'}}"

    def test_oversized_result_is_truncated(self):
        result = evaluated("'x' * 10000")

        assert result["result"].endswith(TRUNCATION_MARKER)

    def test_expression_spanning_lines_is_embedded_as_a_literal(self):
        assert evaluated("(1 +\n2)") == {"type": "int", "result": "3"}

    def test_expression_cannot_spell_a_template_placeholder(self):
        source = build_eval_source("'__PIDPROBE_IS_MASKED__'")

        assert "IS_MASKED = True" in source
        assert run_collector_source(source)["result"] == repr(
            "__PIDPROBE_IS_MASKED__",
        )

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            pytest.param("nope_missing", NameError, id="unknown-name"),
            pytest.param("1 / 0", ZeroDivisionError, id="raising-expression"),
            pytest.param("inventory = {}", SyntaxError, id="statement-not-expression"),
            pytest.param("(((", SyntaxError, id="malformed-expression"),
        ],
    )
    def test_failing_expression_raises_inside_the_target(self, expression, expected):
        with pytest.raises(expected):
            evaluated(expression)


def _run_generated(expression: str, channel: ReturnChannel, tmp_path: Path) -> Envelope:
    """Run the full injected script in a fresh interpreter and read its answer."""
    spec = ChannelSpec(
        kind=channel.kind,
        path=channel.path,
        timeout_seconds=RECEIVE_TIMEOUT_SECONDS,
    )
    script_path = write_injection_script(build_eval_source(expression), spec, tmp_path)
    completed = subprocess.run(  # noqa: S603 -- generated script, trusted argv
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT_SECONDS,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return parse_envelope(channel.receive(Deadline.start(RECEIVE_TIMEOUT_SECONDS)))


class TestInjectedScript:
    def test_successful_expression_answers_with_an_ok_envelope(self, tmp_path):
        channel = open_channel()
        try:
            envelope = _run_generated("2 ** 10", channel, tmp_path)
        finally:
            channel.close()

        assert envelope["status"] == "ok"
        assert envelope["payload"] == {"type": "int", "result": "1024"}

    def test_raising_expression_answers_with_an_error_envelope(self, tmp_path):
        channel = open_channel()
        try:
            envelope = _run_generated("1 / 0", channel, tmp_path)
        finally:
            channel.close()

        assert envelope["status"] == "error"
        assert envelope["payload"] is None
        error = envelope["error"]
        assert error is not None
        assert error["type"] == "ZeroDivisionError"
        assert "ZeroDivisionError" in error["traceback"]

    def test_statement_answers_with_a_syntax_error_envelope(self, tmp_path):
        channel = open_channel()
        try:
            envelope = _run_generated("leaked = 1", channel, tmp_path)
        finally:
            channel.close()

        assert envelope["status"] == "error"
        error = envelope["error"]
        assert error is not None
        assert error["type"] == "SyntaxError"


@pytest.fixture
def canned_target(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Envelope], dict[str, Any]]:
    """Return a factory making the channel answer with a fixed envelope."""

    def _install(envelope: Envelope) -> dict[str, Any]:
        calls: dict[str, Any] = {}

        def fake_execute(pid: int, source: str, **kwargs: Any) -> Envelope:
            calls.update(pid=pid, source=source, **kwargs)
            return envelope

        monkeypatch.setattr(eval_module, "execute_in_target", fake_execute)
        return calls

    return _install


class TestEvaluateInTarget:
    def test_document_reports_what_was_asked_and_what_came_back(self, canned_target):
        canned_target(
            Envelope(
                status="ok",
                error=None,
                payload={"type": "int", "result": "42"},
            ),
        )

        evaluation = evaluate_in_target(PID, "answer")

        assert evaluation == Evaluation(
            pid=PID,
            expression="answer",
            type="int",
            result="42",
            masking_enabled=True,
        )

    def test_channel_arguments_are_passed_through(self, canned_target):
        calls = canned_target(
            Envelope(status="ok", error=None, payload={"type": "int", "result": "1"}),
        )

        evaluate_in_target(PID, "1", timeout_seconds=2.5, allow_socket=False)

        assert calls["pid"] == PID
        assert calls["timeout_seconds"] == pytest.approx(2.5)
        assert calls["allow_socket"] is False

    def test_masking_choice_is_recorded_and_reaches_the_target(self, canned_target):
        calls = canned_target(
            Envelope(status="ok", error=None, payload={"type": "str", "result": "'a'"}),
        )

        evaluation = evaluate_in_target(PID, "api_key", is_masked=False)

        assert evaluation["masking_enabled"] is False
        assert "IS_MASKED = False" in calls["source"]

    def test_target_side_failure_raises_a_target_error_naming_the_evaluation(
        self,
        canned_target,
    ):
        canned_target(
            Envelope(
                status="error",
                error=ErrorInfo(
                    type="NameError",
                    message="name 'nope' is not defined",
                    traceback="Traceback...",
                ),
                payload=None,
            ),
        )

        with pytest.raises(TargetError, match=r"evaluation failed inside pid") as info:
            evaluate_in_target(PID, "nope")

        assert info.value.action == "evaluation"
        assert info.value.error["type"] == "NameError"

    def test_missing_payload_raises_channel_error(self, canned_target):
        canned_target(Envelope(status="ok", error=None, payload=None))

        with pytest.raises(ChannelError, match=r"without a payload"):
            evaluate_in_target(PID, "1")
