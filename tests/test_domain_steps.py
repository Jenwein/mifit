"""Steps domain tests. ``PagedFake`` serves a synthetic payload; nothing
here touches the network and no real health data is used.

The fixture follows the live-verified shape of the decrypted envelope: rows live
under ``result.data_list`` (with ``has_more``/``next_key`` beside them). Each row
is the OUTER object ``{sid, tag, key, time, value, update_time, watermark}``:
``time`` is the UTC midnight of the local date the row stands for (1760400000 =
2025-10-14, in any zone; notes/06) and
``value`` is a JSON string holding only ``{steps, distance, calories}`` -- the
decoded value has no ``time`` of its own. See also mi-health-lab
``docs/cloud-read-06.md``.
"""

import dataclasses
import json
from datetime import timedelta, timezone
from pathlib import Path

from mifit.domains.base import REGISTRY, stalled_note
from mifit.domains.steps import fetch_steps
from mifit.models.steps import StepRecord
from support import PagedFake, payload_with_rows_from

FIXTURE = Path(__file__).parent / "fixtures" / "steps_daily_sample.json"

DAY_1 = 1760400000  # 2025-10-14T00:00:00Z
DAY_2 = DAY_1 + 86400
START = DAY_1
END = DAY_2 + 86399
ENDPOINT = "data/get_aggregated_fitness_data_by_time"
UTC = timezone.utc
CST = timezone(timedelta(hours=8))
EST = timezone(timedelta(hours=-5))


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


payload_with_rows = payload_with_rows_from(load_fixture()["result"]["data_list"][0])


def test_parses_steps_record():
    transport = PagedFake(load_fixture())

    result = fetch_steps(transport, START, END, tz=UTC)

    assert result.records == [
        StepRecord(
            date="2025-10-14",
            steps=8000,
            distance_m=5200,
            step_calories=210,
            epoch_s=1760400000,
        )
    ]
    # The A-faithful side: the untouched, twice-decoded value dict.
    assert result.raw == [{"steps": 8000, "distance": 5200, "calories": 210}]
    assert result.notes == []


def test_timestamp_comes_from_the_outer_entry():
    # The decoded value carries no ``time``; the day is the outer entry's.
    payload = payload_with_rows((DAY_2, {"steps": 1}))

    result = fetch_steps(PagedFake(payload), START, END, tz=UTC)

    assert [(r.epoch_s, r.date) for r in result.records] == [(DAY_2, "2025-10-15")]


def test_requests_daily_report_steps_for_the_window():
    transport = PagedFake(load_fixture())

    fetch_steps(transport, START, END, tz=UTC)

    assert transport.calls == [
        {
            "endpoint_path": ENDPOINT,
            "data_obj": {
                "tag": "daily_report",
                "key": "steps",
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


def test_window_east_of_utc_selects_the_local_days():
    # Local 2025-10-15 00:00 .. 2025-10-16 23:59:59 at UTC+8 asks for the reports
    # keyed 2025-10-15 and 2025-10-16.
    transport = PagedFake(load_fixture())
    start = DAY_1 + 16 * 3600
    end = DAY_2 + 16 * 3600 + 86399

    fetch_steps(transport, start, end, tz=CST)

    sent = transport.calls[0]["data_obj"]
    assert (sent["start_time"], sent["end_time"]) == (DAY_1, DAY_2 + 86400)


def test_window_west_of_utc_selects_the_local_day():
    # Regression: local 2025-10-14 at UTC-5 is 05:00Z..04:59:59Z next day. Sent as
    # is, that window would skip the 2025-10-14 report (keyed 00:00Z) and pick up
    # 2025-10-15's instead.
    transport = PagedFake(load_fixture())
    start = DAY_1 + 5 * 3600

    fetch_steps(transport, start, start + 86399, tz=EST)

    sent = transport.calls[0]["data_obj"]
    assert (sent["start_time"], sent["end_time"]) == (DAY_1 - 86400, DAY_1)


def test_epoch_s_is_the_local_midnight_of_the_date():
    # The row keyed 2025-10-15 (UTC midnight) stands for local 2025-10-15, which
    # starts at 2025-10-14T16:00Z at UTC+8.
    payload = payload_with_rows((DAY_2, {"steps": 1}))

    result = fetch_steps(PagedFake(payload), START, END, tz=CST)

    assert [(r.date, r.epoch_s) for r in result.records] == [
        ("2025-10-15", DAY_1 + 16 * 3600)
    ]


def test_empty_day_yields_no_records():
    payload = load_fixture()
    payload["result"]["data_list"] = []

    result = fetch_steps(PagedFake(payload), START, END, tz=UTC)

    # No fabricated zero for a day the service returned nothing for.
    assert result.records == []
    assert result.raw == []


def test_null_fields_stay_none():
    payload = payload_with_rows(
        (DAY_1, {"steps": 100}),  # distance/calories absent
        (DAY_2, {"steps": 200, "distance": None, "calories": None}),
    )

    result = fetch_steps(PagedFake(payload), START, END, tz=UTC)

    assert [r.steps for r in result.records] == [100, 200]
    for record in result.records:
        assert record.distance_m is None
        assert record.step_calories is None


def test_explicit_zero_steps_is_kept():
    payload = payload_with_rows((DAY_1, {"steps": 0, "distance": 0}))

    result = fetch_steps(PagedFake(payload), START, END, tz=UTC)

    # A reported zero is data; only an absent value must not become zero.
    assert [(r.steps, r.distance_m, r.step_calories) for r in result.records] == [
        (0, 0, None)
    ]


def test_entry_without_steps_is_skipped_not_zeroed():
    payload = payload_with_rows(
        (DAY_1, {"distance": 5200}),  # no steps
        (DAY_2, {"steps": 200}),
    )

    result = fetch_steps(PagedFake(payload), START, END, tz=UTC)

    assert [r.steps for r in result.records] == [200]
    assert len(result.notes) == 1
    # The untouched value of the skipped row is still preserved in ``raw``.
    assert result.raw == [{"distance": 5200}, {"steps": 200}]


def test_entry_without_a_usable_outer_time_is_skipped():
    payload = payload_with_rows((DAY_1, {"steps": 100}), (DAY_2, {"steps": 200}))
    rows = payload["result"]["data_list"]
    del rows[0]["time"]
    rows[1]["time"] = None

    result = fetch_steps(PagedFake(payload), START, END, tz=UTC)

    assert result.records == []
    assert len(result.notes) == 2
    assert result.raw == [{"steps": 100}, {"steps": 200}]


def test_walking_calories_land_in_step_calories_not_calories():
    # The step report's own ``calories`` (walking calories) is exposed under the
    # explicit name ``step_calories`` so it is not mistaken for the app's calorie
    # ring (that figure comes from the consumption domain).
    payload = payload_with_rows((DAY_1, {"steps": 8000, "calories": 210}))

    (record,) = fetch_steps(PagedFake(payload), START, END, tz=UTC).records

    assert record.step_calories == 210
    assert "step_calories" in {f.name for f in dataclasses.fields(StepRecord)}
    assert "calories" not in {f.name for f in dataclasses.fields(StepRecord)}
    assert not hasattr(record, "calories")


def test_steps_domain_is_registered():
    assert REGISTRY["steps"] is fetch_steps


def test_stalled_read_says_so():
    payload = payload_with_rows((DAY_1, {"distance": 5200}), (DAY_2, {"steps": 200}))

    result = fetch_steps(PagedFake(payload, stalled=True), START, END, tz=UTC)

    # After the row's own note, and the rows that were read are still returned.
    assert len(result.notes) == 2
    assert result.notes[-1] == stalled_note("daily step rows")
    assert [r.steps for r in result.records] == [200]
