"""Tests for the shared helper of the domains' normalizers."""

import pytest

from mifit.domains.base import as_int, as_positive_int


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
