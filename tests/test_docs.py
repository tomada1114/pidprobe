"""Tests that keep the documentation and the shipped code from drifting apart.

The pages these tests read describe surfaces the code owns -- the checks
``pidprobe doctor`` reports, the exit codes, the subcommands -- and a rename in
the code would otherwise leave the docs quietly wrong. Nothing here checks
prose; each test pins one enumeration that exists in both places.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

from pidprobe import _attach_policy, _doctor, _target_python
from pidprobe._doctor import diagnose
from pidprobe._exits import EXIT_CODE_TABLE
from pidprobe.cli import build_parser

DOCS = Path(__file__).resolve().parents[1] / "docs"
TROUBLESHOOTING = DOCS / "troubleshooting.md"
REFERENCE = DOCS / "reference.md"

CHECK_MODULES = (_doctor, _attach_policy, _target_python)
"""Every module that builds a :class:`~pidprobe._diagnosis.Check`."""

MINIMUM_CHECKS = 11
"""Guard against an extraction that silently stops finding anything."""

_SECTION_PATTERN = re.compile(r"^## (.+)$", re.MULTILINE)
_CHECK_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_EXIT_ROW_PATTERN = re.compile(r"^\| `(\d+)` \| (.+?) \|$", re.MULTILINE)
_COMMAND_PATTERN = re.compile(r"^pidprobe (\w+)", re.MULTILINE)
_SUBCOMMAND_LIST_PATTERN = re.compile(r"\{([a-z]+(?:,[a-z]+)+)\}")


def doctor_check_names() -> set[str]:
    """Return every check name ``doctor`` can report, read from the source.

    Running the diagnosis would only reveal the checks this platform reaches,
    so the names are collected statically instead: every ``Check(...)`` built
    in the doctor's own modules, whichever branch produced it.
    """
    names: set[str] = set()
    for module in CHECK_MODULES:
        assert module.__file__ is not None
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "Check":
                continue
            names.update(
                str(keyword.value.value)
                for keyword in node.keywords
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant)
            )
    return names


def documented_checks() -> list[str]:
    """Return the check names troubleshooting.md has a section for, in order."""
    headings = _SECTION_PATTERN.findall(TROUBLESHOOTING.read_text(encoding="utf-8"))
    return [heading for heading in headings if _CHECK_NAME_PATTERN.match(heading)]


def test_doctor_check_names_are_extractable() -> None:
    """The static extraction still finds the checks it is meant to compare."""
    names = doctor_check_names()

    assert len(names) >= MINIMUM_CHECKS
    assert "prober_remote_debug" in names


def test_troubleshooting_documents_every_doctor_check() -> None:
    """Every check has a section, and every check section names a real check."""
    documented = documented_checks()

    assert len(documented) == len(set(documented)), "a check is documented twice"
    assert set(documented) == doctor_check_names()


def test_troubleshooting_sections_follow_the_doctor_report_order() -> None:
    """The sections run in the order a report prints its checks.

    Diagnosing this very process is what makes the order real rather than
    asserted: it is the order a reader sees, on this platform, today.
    """
    documented = documented_checks()
    reported = [check.name for check in diagnose(os.getpid()).checks]

    positions = [documented.index(name) for name in reported]
    assert positions == sorted(positions), (
        f"doctor reports {reported}, which is not the order of {documented}"
    )


def test_reference_exit_code_table_matches_the_shipped_table() -> None:
    """The documented exit codes are the ones ``--help`` prints, word for word."""
    rows = _EXIT_ROW_PATTERN.findall(REFERENCE.read_text(encoding="utf-8"))

    assert [(int(code), meaning) for code, meaning in rows] == list(EXIT_CODE_TABLE)


def test_reference_documents_every_subcommand() -> None:
    """The documented subcommands are exactly the ones the parser offers."""
    match = _SUBCOMMAND_LIST_PATTERN.search(build_parser().format_help())
    assert match is not None, "the parser no longer lists its subcommands"
    documented = set(_COMMAND_PATTERN.findall(REFERENCE.read_text(encoding="utf-8")))

    assert set(match.group(1).split(",")) == documented
