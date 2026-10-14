"""Tests for the domains' shared helpers: row decoding and number readings."""

import pytest

from mifit.domains.base import (
    ValueProblem,
    as_float,
    as_int,
    as_positive_float,
    as_positive_int,
    decode_value,
    stalled_note,
)

ABSENT = object()
"""Stands for a ``value`` key that is not there at all."""

HUGE_INT = "1" * 5000
"""An integer literal longer than Python's 4300-digit ``int`` parsing limit, so
``json.loads`` raises ``ValueError`` on it."""

TOO_DEEP = 1_000_000
"""A nesting depth far beyond what ``json.loads`` accepts on any supported Python,
so it raises ``RecursionError`` (not a ``ValueError``)."""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (72, 72),
        (0, 0),  # a reported zero is a number; callers decide what it means
        (-5, -5),
        (72.9, 72),
        (-0.5, 0),
        (1e3, 1000),
    ],
)
def test_as_int_reads_numbers(raw, expected):
    assert as_int(raw) == expected
    assert type(as_int(raw)) is int


@pytest.mark.parametrize(
    "raw",
    [
        None,
        True,
        False,
        "72",
        [72],
        {"bpm": 72},
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
    ids=["none", "true", "false", "str", "list", "dict", "nan", "inf", "-inf"],
)
def test_as_int_rejects_everything_else(raw):
    assert as_int(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (72, 72),
        (1, 1),
        (72.9, 72),
        (96.0, 96),
        (1e3, 1000),
    ],
)
def test_as_positive_int_keeps_positive_numbers(raw, expected):
    assert as_positive_int(raw) == expected
    assert type(as_positive_int(raw)) is int


@pytest.mark.parametrize(
    "raw",
    [
        0,  # the service's "not reported"
        0.0,
        0.9,  # truncates to 0
        -5,
        -0.5,
        None,
        True,
        False,
        "72",
        [72],
        {"bpm": 72},
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
    ids=[
        "zero",
        "zero-float",
        "fraction-below-one",
        "negative",
        "negative-fraction",
        "none",
        "true",
        "false",
        "str",
        "list",
        "dict",
        "nan",
        "inf",
        "-inf",
    ],
)
def test_as_positive_int_rejects_zero_negatives_and_non_numbers(raw):
    assert as_positive_int(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (70.5, 70.5),
        (70, 70.0),
        (0, 0.0),  # a reported zero is a number; callers decide what it means
        (-1.5, -1.5),
    ],
)
def test_as_float(raw, expected):
    assert as_float(raw) == expected
    assert type(as_float(raw)) is float


@pytest.mark.parametrize(
    "raw",
    [
        None,
        True,
        False,
        "70",
        [70],
        {"kg": 70},
        float("nan"),
        float("inf"),
        float("-inf"),
        10**400,  # ``json.loads`` yields such ints; ``float()`` would raise
        -(10**400),
    ],
    ids=[
        "none",
        "true",
        "false",
        "str",
        "list",
        "dict",
        "nan",
        "inf",
        "-inf",
        "huge-int",
        "huge-negative-int",
    ],
)
def test_as_float_rejects_everything_else(raw):
    assert as_float(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (70.5, 70.5),
        (70, 70.0),
        (1e-3, 1e-3),
    ],
)
def test_as_positive_float_keeps_positive_numbers(raw, expected):
    assert as_positive_float(raw) == expected
    assert type(as_positive_float(raw)) is float


@pytest.mark.parametrize(
    "raw",
    [
        0,  # "not reported" / "not set"
        0.0,
        -0.0,
        -1,
        -1.5,
        None,
        True,
        False,
        "70",
        [70],
        {"kg": 70},
        float("nan"),
        float("inf"),
        float("-inf"),
        10**400,
    ],
    ids=[
        "zero",
        "zero-float",
        "negative-zero",
        "negative",
        "negative-fraction",
        "none",
        "true",
        "false",
        "str",
        "list",
        "dict",
        "nan",
        "inf",
        "-inf",
        "huge-int",
    ],
)
def test_as_positive_float_rejects_zero_negatives_and_non_numbers(raw):
    assert as_positive_float(raw) is None


def test_decode_value_returns_the_decoded_object():
    assert decode_value({"time": 1, "value": '{"bpm": 72, "type": null}'}) == (
        {"bpm": 72, "type": None},
        None,
    )


def test_decode_value_of_an_empty_object_is_an_empty_dict():
    assert decode_value({"value": "{}"}) == ({}, None)


@pytest.mark.parametrize(
    "value",
    [
        ABSENT,
        None,
        72,
        True,
        ["{}"],
        {"bpm": 72},  # already decoded: the wire value is a JSON string
        "",
        "not json",
        "{'bpm': 72}",
        HUGE_INT,
        '{"bpm": ' + HUGE_INT + "}",
    ],
    ids=[
        "absent",
        "none",
        "int",
        "bool",
        "list",
        "dict",
        "empty-string",
        "not-json",
        "single-quoted",
        "5000-digit-int",
        "5000-digit-int-in-object",
    ],
)
def test_decode_value_reports_a_missing_or_undecodable_value(value):
    row = {"time": 1} if value is ABSENT else {"time": 1, "value": value}

    assert decode_value(row) == (None, ValueProblem.UNREADABLE)


@pytest.mark.parametrize(
    ("opening", "closing"), [("[", "]"), ('{"a": ', "}")], ids=["arrays", "objects"]
)
def test_decode_value_of_a_too_deeply_nested_value_is_unreadable(opening, closing):
    value = opening * TOO_DEEP + "1" + closing * TOO_DEEP

    assert decode_value({"time": 1, "value": value}) == (None, ValueProblem.UNREADABLE)


@pytest.mark.parametrize(
    "row", [None, "value", ["value"], 7], ids=["none", "str", "list", "int"]
)
def test_decode_value_of_a_row_that_is_not_an_object_is_unreadable(row):
    assert decode_value(row) == (None, ValueProblem.UNREADABLE)


@pytest.mark.parametrize(
    "value",
    ["[1, 2]", "[]", '"72"', "72", "1.5", "true", "null", "NaN"],
    ids=["array", "empty-array", "string", "int", "float", "bool", "null", "nan"],
)
def test_decode_value_reports_json_that_is_not_an_object(value):
    assert decode_value({"value": value}) == (None, ValueProblem.NOT_AN_OBJECT)


def test_stalled_note_names_what_may_be_missing():
    assert stalled_note("daily step rows") == (
        "The service said more daily step rows followed but sent no usable cursor, "
        "so the read stopped early: some daily step rows in the range may be missing."
    )
