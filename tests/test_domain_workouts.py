"""Workouts domain tests. ``PagedFake`` serves a synthetic payload; nothing here
touches the network and no real health data is used.

The fixture follows the live-verified shape of the decrypted envelope: rows live
under ``result.sport_records`` (with ``has_more``/``next_key`` beside them), newest
first. Each row is the OUTER object ``{sid, key, category, time, value, zone_name,
zone_offset, update_time, watermark}``: ``time`` is the workout's start instant and
``value`` is a JSON string holding the workout's figures. The two fixture rows are
a strength session (no distance, no steps) and an outdoor run.
"""

import dataclasses
import json
from pathlib import Path

from mifit.domains.base import REGISTRY, stalled_note
from mifit.domains.workouts import fetch_workouts
from mifit.models.workouts import Workout
from support import PagedFake

FIXTURE = Path(__file__).parent / "fixtures" / "workouts_sample.json"

DAY_1 = 1760400000
START = DAY_1
END = DAY_1 + 172799
ENDPOINT = "data/get_sport_records_by_time"

ABSENT = object()
"""Stands for a key that is not there at all, as opposed to one set to ``None``."""

NOTE_NO_TIME = "Skipped a workout row without a usable time."
NOTE_NOT_JSON = "Skipped a workout row whose value is missing or not JSON."
NOTE_NOT_OBJECT = "Skipped a workout row whose value is not an object."
NOTE_NO_START = "Skipped a workout without a usable start time."


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


STRENGTH_ROW, RUN_ROW = load_fixture()["result"]["sport_records"]
STRENGTH_VALUE = json.loads(STRENGTH_ROW["value"])
RUN_VALUE = json.loads(RUN_ROW["value"])
RUN_START = RUN_VALUE["start_time"]


def row(time, value, **outer):
    """An outer row built on the fixture's run row.

    ``value`` is a dict (JSON-encoded here), a string (sent as it is) or ``None``
    (no ``value`` key). A ``time`` of ``ABSENT`` leaves the key out.
    """
    out = {k: v for k, v in RUN_ROW.items() if k not in ("time", "value")}
    out.update(outer)
    if time is not ABSENT:
        out["time"] = time
    if value is not None:
        out["value"] = value if isinstance(value, str) else json.dumps(value)
    return out


def run_value(**changes):
    """The fixture run's value with ``changes`` applied; ``ABSENT`` removes a key."""
    value = dict(RUN_VALUE)
    for key, new in changes.items():
        if new is ABSENT:
            del value[key]
        else:
            value[key] = new
    return value


def payload_with_rows(*rows):
    """A fixture-shaped payload whose ``sport_records`` are exactly ``rows``."""
    payload = load_fixture()
    payload["result"]["sport_records"] = list(rows)
    return payload


def fetch(payload, **options):
    """``fetch_workouts`` over ``PagedFake(payload, **options)`` for the test window."""
    return fetch_workouts(PagedFake(payload, **options), START, END)


def expected_run(**changes):
    """The ``Workout`` the fixture run maps to, with ``changes`` applied."""
    return dataclasses.replace(
        Workout(
            start_epoch_s=RUN_START,
            end_epoch_s=RUN_START + 3001,
            utc_offset_s=28800,
            sport="running",
            source_sport_code=1,
            duration_s=2700,
            distance_m=6000,
            active_kcal=400,
            avg_hr_bpm=150,
            max_hr_bpm=175,
            steps=7000,
        ),
        **changes,
    )


def test_workouts_domain_is_registered():
    assert REGISTRY["workouts"] is fetch_workouts


def test_requests_from_a_second_before_start():
    transport = PagedFake(load_fixture())

    fetch_workouts(transport, START, END)

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["endpoint_path"] == ENDPOINT
    # The service leaves out a workout starting exactly at start_time (notes/10),
    # so the request starts one second early and the rows are filtered afterwards.
    assert call["data_obj"] == {
        "start_time": START - 1,
        "end_time": END,
        "reverse": True,
        "limit": 50,
    }
    assert call["list_key"] == "sport_records"
    assert call["max_pages"] == 20


def test_maps_core_fields():
    result = fetch(load_fixture())

    assert result.records == [
        Workout(
            start_epoch_s=DAY_1 + 151200,
            end_epoch_s=DAY_1 + 152401,
            utc_offset_s=28800,
            sport="strength_training",
            source_sport_code=308,
            duration_s=1200,
            distance_m=None,
            active_kcal=150,
            avg_hr_bpm=110,
            max_hr_bpm=140,
            steps=None,
        ),
        Workout(
            start_epoch_s=DAY_1 + 36000,
            end_epoch_s=DAY_1 + 39001,
            utc_offset_s=28800,
            sport="running",
            source_sport_code=1,
            duration_s=2700,
            distance_m=6000,
            active_kcal=400,
            avg_hr_bpm=150,
            max_hr_bpm=175,
            steps=7000,
        ),
    ]
    assert result.notes == []


def test_maps_the_known_sport_types():
    known = {
        1: "running",
        6: "biking",
        300: "stair_climbing_machine",
        308: "strength_training",
    }
    payload = payload_with_rows(
        *(row(RUN_START, run_value(sport_type=code)) for code in known)
    )

    result = fetch(payload)

    got = [(w.source_sport_code, w.sport) for w in result.records]
    assert got == list(known.items())


def test_unmapped_sport_type_keeps_the_source_code():
    payload = payload_with_rows(
        row(RUN_START, run_value(sport_type=9)),
        row(RUN_START, run_value(sport_type=0)),
        row(RUN_START, run_value(sport_type=ABSENT)),
        row(RUN_START, run_value(sport_type="1")),
        row(RUN_START, run_value(sport_type=True)),
    )

    result = fetch(payload)

    # An unmapped number (``0`` too) keeps its source code; a missing or unusable
    # one has neither. The record is built either way.
    assert result.records == [
        expected_run(sport=None, source_sport_code=9),
        expected_run(sport=None, source_sport_code=0),
        expected_run(sport=None, source_sport_code=None),
        expected_run(sport=None, source_sport_code=None),
        expected_run(sport=None, source_sport_code=None),
    ]
    assert result.notes == []


def test_absent_or_zero_figures_become_none():
    cases = [
        (run_value(distance=ABSENT), {"distance_m": None}),
        (run_value(steps=0), {"steps": None}),
        (run_value(calories=0), {"active_kcal": None}),
        (run_value(avg_hrm=0), {"avg_hr_bpm": None}),
        (run_value(max_hrm=-1), {"max_hr_bpm": None}),
        (run_value(duration=0), {"duration_s": None}),
    ]
    payload = payload_with_rows(*(row(RUN_START, value) for value, _ in cases))

    result = fetch(payload)

    # Each record differs from the full run in that one field only.
    assert result.records == [expected_run(**changes) for _, changes in cases]


def test_non_numeric_figures_become_none():
    bad_values = ["72", True, [1], float("nan")]
    figures = (
        "end_time",
        "duration",
        "distance",
        "calories",
        "avg_hrm",
        "max_hrm",
        "steps",
        "timezone",
    )
    payload = payload_with_rows(
        *(
            row(RUN_START, run_value(**dict.fromkeys(figures, bad)))
            for bad in bad_values
        )
    )
    # NaN is written into the JSON as the bare token ``NaN``.
    assert "NaN" in payload["result"]["sport_records"][-1]["value"]

    result = fetch(payload)

    nothing_reported = expected_run(
        end_epoch_s=None,
        utc_offset_s=None,
        duration_s=None,
        distance_m=None,
        active_kcal=None,
        avg_hr_bpm=None,
        max_hr_bpm=None,
        steps=None,
    )
    assert result.records == [nothing_reported] * len(bad_values)
    assert result.notes == []


def test_utc_offset_comes_from_quarter_hours():
    cases = [(32, 28800), (-20, -18000), (0, 0), (ABSENT, None)]
    payload = payload_with_rows(
        *(row(RUN_START, run_value(timezone=zone)) for zone, _ in cases)
    )

    result = fetch(payload)

    # The start instant is the same whatever the zone: only the offset changes.
    assert [w.start_epoch_s for w in result.records] == [RUN_START] * len(cases)
    assert [w.utc_offset_s for w in result.records] == [offset for _, offset in cases]


def test_rows_on_both_bounds_are_kept():
    payload = payload_with_rows(
        row(END, run_value(time=END, start_time=END)),
        row(START, run_value(time=START, start_time=START)),
    )

    result = fetch(payload)

    assert [w.start_epoch_s for w in result.records] == [END, START]
    assert result.notes == []


def test_rows_outside_the_window_are_dropped():
    kept = run_value()
    payload = payload_with_rows(
        row(END + 1, run_value(time=END + 1, start_time=END + 1)),
        row(RUN_START, kept),
        row(START - 1, run_value(time=START - 1, start_time=START - 1)),
    )

    result = fetch(payload)

    assert result.records == [expected_run()]
    assert result.raw == [kept]
    # The one-second margin is the SDK's own doing: its rows go without a note.
    assert result.notes == []


def test_deleted_rows_are_dropped():
    gone = run_value(start_time=RUN_START + 1)
    payload = payload_with_rows(
        row(RUN_START + 1, gone, deleted=True),
        row(RUN_START, run_value(), deleted=False),
        row(RUN_START, run_value(), deleted=0),
    )

    result = fetch(payload)

    assert result.records == [expected_run(), expected_run()]
    assert gone not in result.raw
    assert len(result.raw) == 2
    assert result.notes == []


def test_row_without_usable_time_is_skipped_with_note():
    payload = payload_with_rows(
        row(ABSENT, run_value()),
        row("x", run_value()),
        row(True, run_value()),
    )

    result = fetch(payload)

    assert result.records == []
    assert result.raw == []
    assert result.notes == [NOTE_NO_TIME] * 3


def test_non_object_row_is_skipped_with_note():
    payload = payload_with_rows("not a row", None, row(RUN_START, run_value()))

    result = fetch(payload)

    assert result.records == [expected_run()]
    assert result.notes == [NOTE_NO_TIME] * 2


def test_row_with_bad_value_is_skipped_with_note():
    payload = payload_with_rows(
        row(RUN_START, "not json"),
        row(RUN_START, "[1]"),
        row(RUN_START, None),  # no ``value`` key at all
    )

    result = fetch(payload)

    assert result.records == []
    assert result.raw == []
    assert result.notes == [NOTE_NOT_JSON, NOTE_NOT_OBJECT, NOTE_NOT_JSON]


def test_workout_without_start_is_skipped_with_note():
    no_start = run_value(start_time=ABSENT)
    zero_start = run_value(start_time=0)
    payload = payload_with_rows(row(RUN_START, no_start), row(RUN_START, zero_start))

    result = fetch(payload)

    assert result.records == []
    assert result.notes == [NOTE_NO_START] * 2
    # Skipped as a record, but the decoded value is kept for the caller.
    assert result.raw == [no_start, zero_start]


def test_raw_keeps_decoded_values_untouched():
    result = fetch(load_fixture())

    assert result.raw == [STRENGTH_VALUE, RUN_VALUE]
    # Fields the workout model leaves out are all still there.
    for key in ("total_cal", "min_hrm", "proto_type", "hrm_aerobic_duration"):
        assert key in result.raw[1]


def test_capped_read_says_so():
    result = fetch(load_fixture(), truncated=True)

    assert len(result.notes) == 1
    assert "20-page cap" in result.notes[0]
    # The workouts that were read are still returned.
    assert len(result.records) == 2


def test_uncapped_read_has_no_cap_note():
    result = fetch(load_fixture(), truncated=False)

    assert result.notes == []


def test_unverified_fields_stay_out_of_workout():
    assert {f.name for f in dataclasses.fields(Workout)} == {
        "start_epoch_s",
        "end_epoch_s",
        "utc_offset_s",
        "sport",
        "source_sport_code",
        "duration_s",
        "distance_m",
        "active_kcal",
        "avg_hr_bpm",
        "max_hr_bpm",
        "steps",
    }


def test_empty_window_yields_no_records_and_no_notes():
    result = fetch(payload_with_rows())

    # No fabricated workout for a window the service returned nothing for.
    assert result.records == []
    assert result.raw == []
    assert result.notes == []


def test_stalled_read_says_so():
    payload = payload_with_rows(row(ABSENT, run_value()), row(RUN_START, run_value()))

    result = fetch(payload, stalled=True)

    # After the row's own note, and the workouts that were read are still returned.
    assert result.notes == [NOTE_NO_TIME, stalled_note("workout rows")]
    assert result.records == [expected_run()]
