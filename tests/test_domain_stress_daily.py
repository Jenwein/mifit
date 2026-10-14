"""Daily stress summary domain tests. ``PagedFake`` serves a synthetic payload;
nothing here touches the network and no real health data is used.

The fixture follows the live-verified shape of the decrypted envelope: rows live
under ``result.data_list`` (with ``has_more``/``next_key`` beside them). Each row
is the OUTER object ``{sid, tag, key, time, value, update_time, watermark}``:
``time`` is the UTC midnight of the local date the row stands for (1760400000 =
2025-10-14, in any zone; notes/06) and ``value`` is a JSON string holding the
day's figures (``avg_stress``, ``max_stress``, ``min_stress`` and the
``stress_scale`` band durations). The second fixture row has no ``min_stress``.
"""

import dataclasses
import json
from datetime import timedelta, timezone
from pathlib import Path

import pytest

from mifit.domains.base import REGISTRY, stalled_note
from mifit.domains.stress_daily import fetch_stress_daily
from mifit.models.stress import StressDailySummary
from support import PagedFake

FIXTURE = Path(__file__).parent / "fixtures" / "stress_daily_sample.json"

DAY_1 = 1760400000  # 2025-10-14T00:00:00Z
DAY_2 = DAY_1 + 86400
START = DAY_1
END = DAY_2 + 86399
ENDPOINT = "data/get_aggregated_fitness_data_by_time"
UTC = timezone.utc
CST = timezone(timedelta(hours=8))
ABSENT = object()  # marks a key left out of a row, as opposed to set to None


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def payload_with_rows(*rows):
    """A fixture-shaped payload built from ``(outer_time, value)`` pairs.

    A dict ``value`` is encoded to the JSON string the wire carries; a ``str`` is
    used as the wire string itself, so a test can send a value that is not JSON.
    """
    payload = load_fixture()
    template = payload["result"]["data_list"][0]
    payload["result"]["data_list"] = [
        {
            **template,
            "time": time,
            "value": value if isinstance(value, str) else json.dumps(value),
        }
        for time, value in rows
    ]
    return payload


def fixture_values():
    return [json.loads(row["value"]) for row in load_fixture()["result"]["data_list"]]


def figures(**overrides):
    """A decoded daily value with only the three mapped figures, then overrides."""
    value = {"avg_stress": 35, "max_stress": 80, "min_stress": 12}
    value.update(overrides)
    return value


def triple(record):
    return (record.avg_stress_level, record.max_stress_level, record.min_stress_level)


def test_stress_daily_domain_is_registered():
    assert REGISTRY["stress_daily"] is fetch_stress_daily


def test_requests_daily_report_stress_for_the_window():
    transport = PagedFake(load_fixture())

    fetch_stress_daily(transport, START, END, tz=UTC)

    assert transport.calls == [
        {
            "endpoint_path": ENDPOINT,
            "data_obj": {
                "tag": "daily_report",
                "key": "stress",
                "reverse": True,
                "limit": 50,
                # (start, end] over the daily-report keys: the key of the day
                # before 2025-10-14 through the key of 2025-10-15.
                "start_time": DAY_1 - 86400,
                "end_time": DAY_2,
            },
            "list_key": "data_list",
            "max_pages": None,
        }
    ]


def test_maps_day_figures():
    result = fetch_stress_daily(PagedFake(load_fixture()), START, END, tz=UTC)

    assert result.records[0] == StressDailySummary(
        date="2025-10-14",
        epoch_s=DAY_1,
        avg_stress_level=35,
        max_stress_level=80,
        min_stress_level=12,
    )
    assert result.notes == []


def test_second_day_without_lowest_figure_keeps_the_rest():
    result = fetch_stress_daily(PagedFake(load_fixture()), START, END, tz=UTC)

    assert result.records[1] == StressDailySummary(
        date="2025-10-15",
        epoch_s=DAY_2,
        avg_stress_level=28,
        max_stress_level=66,
        min_stress_level=None,
    )


def test_epoch_is_the_local_midnight_of_the_report_date():
    # The row keyed 2025-10-14 (UTC midnight) stands for local 2025-10-14, which
    # starts at 2025-10-13T16:00Z at UTC+8.
    start = DAY_1 - 28800
    end = DAY_2 - 28800 + 86399

    result = fetch_stress_daily(PagedFake(load_fixture()), start, end, tz=CST)

    assert result.records[0].date == "2025-10-14"
    assert result.records[0].epoch_s == DAY_1 - 28800


def test_missing_or_zero_figures_become_none():
    payload = payload_with_rows(
        (DAY_1, {"avg_stress": 35, "max_stress": 80}),  # min_stress absent
        (DAY_1 + 86400, figures(min_stress=None)),
        (DAY_1 + 2 * 86400, figures(min_stress=0)),
        (DAY_1 + 3 * 86400, figures(max_stress=0)),
    )

    result = fetch_stress_daily(PagedFake(payload), START, DAY_1 + 3 * 86400, tz=UTC)

    assert [triple(r) for r in result.records] == [
        (35, 80, None),
        (35, 80, None),
        (35, 80, None),
        (35, None, 12),
    ]
    assert result.notes == []


def test_non_numeric_figures_become_none():
    payload = payload_with_rows(
        (DAY_1, figures(avg_stress="35")),
        (DAY_2, figures(avg_stress=True, max_stress=[35], min_stress={"stress": 12})),
    )

    result = fetch_stress_daily(PagedFake(payload), START, END, tz=UTC)

    assert [triple(r) for r in result.records] == [
        (None, 80, 12),
        (None, None, None),
    ]


def test_negative_and_non_finite_figures_become_none():
    payload = payload_with_rows(
        (
            DAY_1,
            figures(avg_stress=-35, max_stress=float("nan"), min_stress=float("inf")),
        ),
    )

    (record,) = fetch_stress_daily(PagedFake(payload), START, END, tz=UTC).records

    assert triple(record) == (None, None, None)


def test_float_figures_are_truncated():
    payload = payload_with_rows((DAY_1, figures(avg_stress=35.0, max_stress=79.9)))

    (record,) = fetch_stress_daily(PagedFake(payload), START, END, tz=UTC).records

    assert triple(record) == (35, 79, 12)
    assert type(record.avg_stress_level) is int


def test_row_with_every_figure_missing_still_gives_a_record():
    payload = payload_with_rows((DAY_1, {"stress_scale": {"relax": 600}}))

    result = fetch_stress_daily(PagedFake(payload), START, END, tz=UTC)

    assert result.records == [
        StressDailySummary(
            date="2025-10-14",
            epoch_s=DAY_1,
            avg_stress_level=None,
            max_stress_level=None,
            min_stress_level=None,
        )
    ]
    assert result.notes == []


def test_raw_keeps_decoded_values_untouched():
    result = fetch_stress_daily(PagedFake(load_fixture()), START, END, tz=UTC)

    # The band durations (stress_scale) stay in raw as the service sent them.
    assert result.raw == fixture_values()
    assert "stress_scale" in result.raw[0]


@pytest.mark.parametrize(
    "bad_value",
    ["not json", "[1]", None, ABSENT],
    ids=["not-json", "not-an-object", "null", "absent"],
)
def test_row_with_bad_value_is_skipped_with_note(bad_value):
    payload = payload_with_rows((DAY_1, figures()))
    row = payload["result"]["data_list"][0]
    if bad_value is ABSENT:
        del row["value"]
    else:
        row["value"] = bad_value

    result = fetch_stress_daily(PagedFake(payload), START, END, tz=UTC)

    assert result.records == []
    assert len(result.notes) == 1
    assert result.raw == []


def test_bad_value_notes_say_which_problem():
    payload = payload_with_rows((DAY_1, "not json"), (DAY_2, "[1]"))

    result = fetch_stress_daily(PagedFake(payload), START, END, tz=UTC)

    assert result.notes == [
        "Skipped a daily stress row whose value is missing or not JSON.",
        "Skipped a daily stress row whose value is not an object.",
    ]


@pytest.mark.parametrize(
    "bad_time",
    [ABSENT, None, "x", True, str(DAY_1)],
    ids=["absent", "null", "not-a-number", "bool", "numeric-string"],
)
def test_row_without_usable_day_is_skipped_with_note(bad_time):
    payload = payload_with_rows((DAY_1, figures()))
    row = payload["result"]["data_list"][0]
    if bad_time is ABSENT:
        del row["time"]
    else:
        row["time"] = bad_time

    result = fetch_stress_daily(PagedFake(payload), START, END, tz=UTC)

    assert result.records == []
    assert result.notes == ["Skipped a daily stress row without a usable day timestamp."]
    # The decoded value is kept even though the row gave no record.
    assert result.raw == [figures()]


def test_unverified_fields_stay_out_of_the_summary():
    assert {f.name for f in dataclasses.fields(StressDailySummary)} == {
        "date",
        "epoch_s",
        "avg_stress_level",
        "max_stress_level",
        "min_stress_level",
    }


def test_empty_window_yields_no_records_and_no_notes():
    payload = load_fixture()
    payload["result"]["data_list"] = []

    result = fetch_stress_daily(PagedFake(payload), START, END, tz=UTC)

    assert result.records == []
    assert result.raw == []
    assert result.notes == []


def test_stalled_read_says_so():
    payload = payload_with_rows((None, figures()), (DAY_2, figures()))

    result = fetch_stress_daily(PagedFake(payload, stalled=True), START, END, tz=UTC)

    # After the row's own note, and the rows that were read are still returned.
    assert len(result.notes) == 2
    assert result.notes[-1] == stalled_note("daily stress rows")
    assert [r.date for r in result.records] == ["2025-10-15"]
