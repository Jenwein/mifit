"""Day-semantics helpers shared by the per-day domains. Pure arithmetic: no network.

Background (notes/06): a daily report's ``time`` is the UTC midnight of the *local*
date it stands for, whatever the user's zone, and the service returns the rows with
``start_time < time <= end_time``. Days the caller asks for are local calendar days.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from mifit.domains.days import (
    local_day_span,
    local_midnight,
    parse_day,
    report_day,
    report_window,
)

DAY_1 = 1760400000  # 2025-10-14T00:00:00Z
DAY_2 = DAY_1 + 86400
UTC = timezone.utc
CST = timezone(timedelta(hours=8))
EST = timezone(timedelta(hours=-5))


def test_local_day_span_east_of_utc():
    # Local 2025-10-15 00:00 .. 2025-10-16 23:59:59 at UTC+8.
    start = DAY_1 + 16 * 3600
    end = DAY_2 + 16 * 3600 + 86399

    assert local_day_span(start, end, CST) == (date(2025, 10, 15), date(2025, 10, 16))


def test_local_day_span_west_of_utc():
    # Local 2025-10-14 00:00 .. 2025-10-14 23:59:59 at UTC-5.
    start = DAY_1 + 5 * 3600
    end = start + 86399

    assert local_day_span(start, end, EST) == (date(2025, 10, 14), date(2025, 10, 14))


def test_local_day_span_defaults_to_the_machines_zone():
    expected = datetime.fromtimestamp(DAY_1).date()

    assert local_day_span(DAY_1, DAY_1) == (expected, expected)


def test_local_midnight_in_a_fixed_zone():
    assert local_midnight(date(2025, 10, 15), CST) == DAY_1 + 16 * 3600
    assert local_midnight(date(2025, 10, 14), EST) == DAY_1 + 5 * 3600
    assert local_midnight(date(2025, 10, 14), UTC) == DAY_1


def test_local_midnight_defaults_to_the_machines_zone():
    expected = int(datetime.combine(date(2025, 10, 14), time()).timestamp())

    assert local_midnight(date(2025, 10, 14)) == expected


def test_local_midnight_follows_dst():
    try:
        new_york = ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:
        pytest.skip("IANA timezone database not available")

    assert local_midnight(date(2025, 11, 2), new_york) == 1762056000  # 04:00Z, EDT
    assert local_midnight(date(2025, 11, 3), new_york) == 1762146000  # 05:00Z, EST


def test_report_window_selects_first_through_last_day():
    # (start, end]: the day before ``first`` is excluded, ``last`` is included.
    assert report_window(date(2025, 10, 14), date(2025, 10, 15)) == (DAY_1 - 86400, DAY_2)


def test_report_window_for_a_single_day():
    assert report_window(date(2025, 10, 14), date(2025, 10, 14)) == (DAY_1 - 86400, DAY_1)


def test_report_day_is_the_utc_date_of_the_key():
    assert report_day(DAY_1) == date(2025, 10, 14)
    assert report_day(DAY_2) == date(2025, 10, 15)


def test_parse_day_reads_an_iso_date():
    assert parse_day("2026-02-28") == date(2026, 2, 28)
    assert parse_day("2028-02-29") == date(2028, 2, 29)


@pytest.mark.parametrize(
    "bad", ["2026-1-1", "20260101", "2026-W01-1", " 2026-01-01", "2026-01-01T00:00", ""]
)
def test_parse_day_refuses_another_shape(bad):
    # ``date.fromisoformat`` alone would take some of these (``20260101``, week dates).
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_day(bad)


def test_parse_day_refuses_a_day_that_does_not_exist_and_names_the_argument():
    with pytest.raises(ValueError, match="calendar date") as excinfo:
        parse_day("2026-02-30", "start")

    assert str(excinfo.value) == "start '2026-02-30' is not a calendar date"
    with pytest.raises(ValueError, match="^end must look like"):
        parse_day("2026-2-3", "end")
