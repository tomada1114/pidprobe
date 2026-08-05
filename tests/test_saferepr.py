"""Tests for bounded, secret-masking rendering of arbitrary values."""

from __future__ import annotations

import re
import textwrap
from typing import Any

import pytest

from pidprobe._saferepr import (
    ELISION,
    MASK_PLACEHOLDER,
    MAX_ITEMS,
    MAX_STRING_CHARS,
    MAX_TOTAL_CHARS,
    SECRET_NAME_PATTERNS,
    TRUNCATION_MARKER,
    injected_source,
    is_secret_name,
    safe_repr,
    safe_repr_named,
)

RAW_VALUE = "hunter2"


class Exploding:
    """Stands in for a live object whose own repr fails."""

    def __repr__(self) -> str:
        message = "repr exploded"
        raise ValueError(message)


class Mutating(dict[str, int]):
    """Stands in for a mapping another thread mutates mid-iteration."""

    def items(self):
        message = "dictionary changed size during iteration"
        raise RuntimeError(message)


class Unstable(list[int]):
    """Stands in for a sequence another thread mutates mid-iteration."""

    def __iter__(self):
        message = "list changed size during iteration"
        raise RuntimeError(message)


class TestFaithfulRendering:
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(None, id="none"),
            pytest.param(True, id="bool"),
            pytest.param(42, id="int"),
            pytest.param(1.5, id="float"),
            pytest.param("text", id="str"),
            pytest.param(b"bytes", id="bytes"),
            pytest.param([], id="empty-list"),
            pytest.param({}, id="empty-dict"),
            pytest.param(set(), id="empty-set"),
            pytest.param(frozenset(), id="empty-frozenset"),
            pytest.param((), id="empty-tuple"),
            pytest.param((1,), id="one-tuple"),
            pytest.param([1, "two", None], id="list"),
            pytest.param({"a": 1, "b": [2]}, id="dict"),
            pytest.param({"only"}, id="set"),
            pytest.param(frozenset({"only"}), id="frozenset"),
        ],
    )
    def test_small_values_render_exactly_like_repr(self, value):
        assert safe_repr(value) == repr(value)


class TestBounds:
    def test_nesting_within_the_depth_limit_is_rendered(self):
        assert safe_repr([[["deep"]]]) == "[[['deep']]]"

    def test_nesting_beyond_the_depth_limit_is_elided(self):
        assert safe_repr([[[["deep"]]]]) == "[[[[...]]]]"
        assert safe_repr({"a": {"b": {"c": {"d": RAW_VALUE}}}}) == (
            "{'a': {'b': {'c': {...}}}}"
        )

    def test_self_referencing_value_terminates(self):
        looping: list[Any] = []
        looping.append(looping)

        assert safe_repr(looping) == "[[[[...]]]]"

    def test_elements_beyond_the_count_limit_are_elided(self):
        rendered = safe_repr(list(range(MAX_ITEMS + 5)))

        assert rendered.startswith("[0, 1, 2,")
        assert rendered.endswith(f", {ELISION}]")
        assert str(MAX_ITEMS) not in rendered

    def test_mapping_entries_beyond_the_count_limit_are_elided(self):
        rendered = safe_repr({str(index): index for index in range(MAX_ITEMS + 5)})

        assert rendered.count(": ") == MAX_ITEMS
        assert rendered.endswith(f", {ELISION}}}")

    def test_long_string_is_truncated_to_the_string_limit(self):
        rendered = safe_repr("x" * 10_000)

        assert rendered.endswith(TRUNCATION_MARKER)
        assert len(rendered) == MAX_STRING_CHARS + len(TRUNCATION_MARKER)

    def test_long_object_repr_is_truncated_to_the_string_limit(self):
        rendered = safe_repr(range(10**200, 10**200 + 1))

        assert rendered.endswith(TRUNCATION_MARKER)
        assert len(rendered) == MAX_STRING_CHARS + len(TRUNCATION_MARKER)

    def test_total_length_is_bounded_across_nested_values(self):
        wide = [["y" * 500] * MAX_ITEMS] * MAX_ITEMS

        rendered = safe_repr(wide)

        assert rendered.endswith(TRUNCATION_MARKER)
        assert len(rendered) == MAX_TOTAL_CHARS + len(TRUNCATION_MARKER)

    def test_failing_repr_is_reported_instead_of_raising(self):
        assert safe_repr(Exploding()) == "<unrepresentable Exploding: ValueError>"

    def test_failing_repr_inside_a_container_costs_only_that_element(self):
        assert safe_repr([1, Exploding()]) == (
            "[1, <unrepresentable Exploding: ValueError>]"
        )

    def test_mapping_that_fails_to_iterate_is_reported(self):
        assert safe_repr(Mutating(a=1)) == "<unrepresentable Mutating: RuntimeError>"

    def test_sequence_that_fails_to_iterate_is_reported(self):
        assert safe_repr(Unstable([1])) == "<unrepresentable Unstable: RuntimeError>"


class TestMasking:
    @pytest.mark.parametrize("pattern", SECRET_NAME_PATTERNS)
    def test_documented_pattern_is_masked_by_default(self, pattern):
        assert safe_repr_named(pattern, RAW_VALUE) == MASK_PLACEHOLDER

    @pytest.mark.parametrize("pattern", SECRET_NAME_PATTERNS)
    def test_documented_pattern_is_masked_inside_a_longer_name(self, pattern):
        assert safe_repr_named(f"user_{pattern}_2", RAW_VALUE) == MASK_PLACEHOLDER

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("api_key", id="snake-case"),
            pytest.param("apiKey", id="camel-case"),
            pytest.param("API-KEY", id="upper-kebab-case"),
            pytest.param("API_KEY", id="upper-snake-case"),
        ],
    )
    def test_case_and_separators_do_not_defeat_masking(self, name):
        assert safe_repr_named(name, RAW_VALUE) == MASK_PLACEHOLDER

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("username", id="username"),
            pytest.param("author", id="author"),
            pytest.param("session", id="session"),
            pytest.param("retry_count", id="retry-count"),
            pytest.param("", id="empty"),
        ],
    )
    def test_ordinary_names_are_left_alone(self, name):
        assert safe_repr_named(name, RAW_VALUE) == repr(RAW_VALUE)
        assert is_secret_name(name) is False

    def test_mapping_value_under_a_secret_key_is_masked(self):
        rendered = safe_repr({"api_key": RAW_VALUE, "retries": 3})

        assert rendered == f"{{'api_key': {MASK_PLACEHOLDER}, 'retries': 3}}"

    def test_nested_mapping_value_under_a_secret_key_is_masked(self):
        rendered = safe_repr({"config": {"token": RAW_VALUE}})

        assert rendered == f"{{'config': {{'token': {MASK_PLACEHOLDER}}}}}"

    def test_masking_off_restores_the_named_value(self):
        assert safe_repr_named("password", RAW_VALUE, is_masked=False) == repr(
            RAW_VALUE,
        )

    def test_masking_off_restores_values_under_secret_mapping_keys(self):
        value = {"api_key": RAW_VALUE}

        assert safe_repr(value, is_masked=False) == repr(value)

    def test_masking_off_still_bounds_the_value(self):
        rendered = safe_repr_named("password", "x" * 10_000, is_masked=False)

        assert rendered.endswith(TRUNCATION_MARKER)
        assert len(rendered) == MAX_STRING_CHARS + len(TRUNCATION_MARKER)


class TestInjectedSource:
    def test_source_is_self_contained(self):
        source = injected_source()

        assert not re.search(r"(?m)^\s*(import |from .* import )", source)
        assert "injected_source" not in source

    def test_source_compiles_indented_inside_a_function(self):
        # The collector source becomes a function body in the injected script.
        body = textwrap.indent(injected_source(), " " * 4)

        compile(f"def _section():\n{body}\n", "<injected>", "exec")

    def test_injected_copy_renders_like_the_imported_one(self):
        namespace: dict[str, Any] = {}
        exec(injected_source(), namespace)  # noqa: S102 -- pidprobe's own source

        value = {"api_key": RAW_VALUE, "items": list(range(MAX_ITEMS + 5))}
        assert namespace["safe_repr"](value) == safe_repr(value)
        assert namespace["safe_repr_named"]("password", RAW_VALUE) == (MASK_PLACEHOLDER)
