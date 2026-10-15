"""The ``workout_detail`` domain (spec §5, §7, §12): the list read, one link request
per supported workout, the downloads, and a note for every way a file can fail.

``FdsTransport`` serves the workout list and answers each link request from a
``FakeFdsService``, which also stands in for the downloader. The world is a run
(format 22) and a ride (format 23), each with a record file and a GPS file, and a
strength session (format 8) that has no detail. Every value is synthetic.
"""

import dataclasses
import json
from types import SimpleNamespace

import pytest
from fds_fixtures import (
    ABSENT,
    EXPIRES,
    RIDE_FLAGS_LIVE,
    RUN_FLAGS_ALL,
    RUN_FLAGS_LIVE,
    SYNTH_KEY_B64,
    SYNTH_SID,
    URL_MARK,
    FakeFdsService,
    FdsTransport,
    b64url,
    f32,
    file_key,
    gps_file,
    gps_point,
    header_bytes,
    record_file,
    ride_point,
    ride_segment,
    run_point,
    run_segment,
    sport_row,
    sport_value,
)
from support import EncryptingHttp, PagedFake, synthetic_session

from mifit.domains import workout_detail as workout_detail_module
from mifit.domains import workouts as workouts_module
from mifit.domains.base import REGISTRY
from mifit.domains.workout_detail import _MAX_WORKOUTS, fetch_workout_detail
from mifit.domains.workouts import fetch_workouts, sport_rows, to_workout
from mifit.errors import ApiError, AuthError
from mifit.fds.download import FdsUnavailable
from mifit.fds.ids import GPS, RECORD, FileRef, suffix
from mifit.fds.urls import request_urls
from mifit.models.workout_detail import TrackPoint, WorkoutDetail
from mifit.transport import Transport

T = 1700007200
"""The run's report time and start."""
U = 1700003600
"""The ride's start; its report time is one second earlier, as live."""
G = 1700000000
"""The strength session's start."""
START, END = G, T + 3600

RUN = sport_value(proto=22, sport=1, time=T, start_time=T)
RIDE = sport_value(proto=23, sport=6, time=U - 1, start_time=U)
GYM = sport_value(proto=8, sport=308, time=G, start_time=G, distance=0, steps=0)

RUN_RECORD_KEY, RUN_GPS_KEY = file_key(RUN, RECORD), file_key(RUN, GPS)
RIDE_RECORD_KEY, RIDE_GPS_KEY = file_key(RIDE, RECORD), file_key(RIDE, GPS)
REFS = [
    FileRef(SYNTH_SID, T, 32, 22, RECORD),
    FileRef(SYNTH_SID, T, 32, 22, GPS),
    FileRef(SYNTH_SID, U - 1, 32, 23, RECORD),
    FileRef(SYNTH_SID, U - 1, 32, 23, GPS),
]


def run_record(ts=T, *, tz15=32, proto=22, version=8, flags=RUN_FLAGS_LIVE):
    """A run record file whose header says ``ts`` / ``tz15`` / ``proto`` /
    ``version``: one segment of two points from ``ts``."""
    segment = run_segment([run_point(), run_point()], resume=ts)
    return record_file(ts, tz15, proto, version, flags, segment)


def run_gps(*points, flags=b"\xff", version=2):
    """The run's GPS file; by default two fixes whose latitude ≠ longitude."""
    if not points:
        points = (gps_point(T, 20.0001, 10.0001), gps_point(T + 1, 20.0002, 10.0002))
    return gps_file(T, 32, 22, *points, flags=flags, version=version)


RUN_RECORD = run_record()
RUN_GPS = run_gps()
RIDE_RECORD = record_file(
    U - 1, 32, 23, 5, RIDE_FLAGS_LIVE, ride_segment([ride_point(), ride_point()], resume=U - 1)
)
RIDE_GPS = gps_file(
    U - 1, 32, 23, gps_point(U - 1, 20.0001, 10.0001), gps_point(U, 20.0003, 10.0003)
)

FILES = {
    RUN_RECORD_KEY: RUN_RECORD,
    RUN_GPS_KEY: RUN_GPS,
    RIDE_RECORD_KEY: RIDE_RECORD,
    RIDE_GPS_KEY: RIDE_GPS,
}

GYM_NOTE = "Workout 3: detail is not supported for this sport yet (format 8)."
NO_START_NOTE = "Skipped a workout without a usable start time."

# A file of each kind of trouble, for the run.
RUN_RECORD_V9 = run_record(version=9, flags=RUN_FLAGS_ALL)
RUN_RECORD_MODE_1 = header_bytes(T, 32, 8, 22, mode=1) + RUN_RECORD[8:]
RUN_RECORD_CUT = RUN_RECORD[:-5]  # ends inside the second point
RUN_GPS_INVALID = run_gps(flags=b"\x7f")  # ``time`` is marked invalid
RUN_GPS_WITH_ZERO_FIX = run_gps(gps_point(T, 20.0001, 10.0001), gps_point(T + 1, 0.0, 0.0))
WRONG_KEY = b64url(bytes(16))


def files_with(**changes):
    """``FILES`` with ``changes``: ``run_record``, ``run_gps``, ``ride_record`` or
    ``ride_gps`` set to a plain file, or to ``None`` to remove that file."""
    keys = {
        "run_record": RUN_RECORD_KEY,
        "run_gps": RUN_GPS_KEY,
        "ride_record": RIDE_RECORD_KEY,
        "ride_gps": RIDE_GPS_KEY,
    }
    files = dict(FILES)
    for name, plain in changes.items():
        if plain is None:
            del files[keys[name]]
        else:
            files[keys[name]] = plain
    return files


def detail(
    *,
    rows=None,
    files=None,
    entry_overrides=None,
    fetch_errors=None,
    link_errors=None,
    start=START,
    end=END,
    **options,
):
    """``fetch_workout_detail`` over the world (or ``rows`` / ``files``), with the
    service as the downloader. Returns ``(result, transport, service)``."""
    service = FakeFdsService(
        FILES if files is None else files,
        entry_overrides=entry_overrides,
        fetch_errors=fetch_errors,
    )
    if rows is None:
        rows = [sport_row(RUN), sport_row(RIDE), sport_row(GYM)]
    transport = FdsTransport(rows, service, link_errors=link_errors)
    result = fetch_workout_detail(transport, start, end, downloader=service, **options)
    return result, transport, service


def row_without_start():
    """A row inside the window whose summary has no usable start time."""
    value = sport_value(proto=22, sport=1, time=T - 60, start_time=T - 60)
    row = sport_row(value)
    row["value"] = json.dumps({**value, "start_time": "x"})
    return row


def assert_ride_read_in_full(result):
    ride = result.records[1]
    assert len(ride.samples) == 2
    assert len(ride.track) == 2
    assert result.raw[1]["record"]["segments"]
    assert result.raw[1]["gps"]["gpsPoints"]


# --- The list read and the link requests ----------------------------------------------


def test_workout_detail_domain_is_registered():
    assert REGISTRY["workout_detail"] is fetch_workout_detail
    assert _MAX_WORKOUTS == 10


def test_reads_the_list_like_workouts():
    _, transport, _ = detail()
    reference = PagedFake({"code": 0, "result": {"sport_records": []}})
    fetch_workouts(reference, START, END)

    assert transport.calls == reference.calls
    call = transport.calls[0]
    assert call["data_obj"]["start_time"] == START - 1
    assert call["list_key"] == "sport_records"
    assert call["max_pages"] == 20


def test_requests_links_with_the_signing_path_rule():
    _, transport, _ = detail()

    for call in transport.link_calls:
        assert call["endpoint_path"] == "service/gen_download_url"
        assert call["sign_path"] == "/service/gen_download_url"
        assert call["base_path"] == "healthapp/"
    assert transport.link_calls[0]["data_obj"] == {
        "did": SYNTH_SID,
        "items": [
            {"timestamp": T, "suffix": suffix(REFS[0])},
            {"timestamp": T, "suffix": suffix(REFS[1])},
        ],
    }


def test_one_link_request_per_supported_workout():
    _, transport, service = detail()

    assert len(transport.link_calls) == 2
    assert len(service.fetched) == 4


def test_default_downloader_is_built_when_none_is_given(monkeypatch):
    service = FakeFdsService(FILES)
    transport = FdsTransport([sport_row(RUN)], service)
    built = []

    def downloader_factory(*args, **kwargs):
        built.append((args, kwargs))
        return service

    monkeypatch.setattr(workout_detail_module, "FdsDownloader", downloader_factory)

    result = fetch_workout_detail(transport, START, END)

    assert built == [((), {})]
    assert len(result.records[0].samples) == 2


# --- Mapping ----------------------------------------------------------------------------


def test_maps_a_run_and_a_ride():
    result, _, _ = detail()

    run, ride, gym = result.records
    assert [d.workout for d in result.records] == [
        to_workout(RUN),
        to_workout(RIDE),
        to_workout(GYM),
    ]
    assert [s.steps for s in run.samples] == [3, 6]
    assert [s.pace_s_per_km for s in run.samples] == [360, 360]
    assert [s.cadence_spm for s in run.samples] == [170, 170]
    assert [s.distance_m for s in run.samples] == [2.5, 5.0]
    assert all(s.speed_mps is None for s in run.samples)
    assert [s.speed_mps for s in ride.samples] == [250 / 36, 250 / 36]
    assert all(s.steps is None for s in ride.samples)
    for workout in (run, ride):
        assert len(workout.track) == 2
        assert all(isinstance(point, TrackPoint) for point in workout.track)
        assert workout.track[0].latitude_deg == f32(10.0001)
        assert workout.track[0].longitude_deg == f32(20.0001)
    assert gym == WorkoutDetail(to_workout(GYM), None, None)

    assert [r["workout"] for r in result.raw] == [RUN, RIDE, GYM]
    seg = result.raw[0]["record"]["segments"][0]
    assert seg["dataPoints"][0]["newDistance"] == 25
    # The altitude stays in the A layer only (it differed from the app for the run).
    assert seg["initialAltitude"] == 50.0
    assert seg["dataPoints"][0]["isAltitudeRising"] == 1
    assert seg["dataPoints"][0]["altitudeChange"] == 5
    assert not hasattr(run.samples[0], "altitude_m")
    assert result.raw[0]["gps"]["gpsPoints"][0]["time"] == T
    assert result.raw[1]["record"]["header"]["version"] == 5
    assert result.raw[2] == {"workout": GYM, "record": None, "gps": None}
    assert result.notes == [GYM_NOTE]


def test_files_are_named_by_the_report_time_not_the_start():
    result, transport, _ = detail()

    ride_items = transport.link_calls[1]["data_obj"]["items"]
    assert [item["timestamp"] for item in ride_items] == [U - 1, U - 1]
    assert [item["suffix"] for item in ride_items] == [suffix(REFS[2]), suffix(REFS[3])]
    assert result.records[1].workout.start_epoch_s == U
    assert result.records[1].samples[0].epoch_s == U - 1


# --- Workouts that are not read ------------------------------------------------------


def test_unsupported_sport_sends_no_request():
    result, transport, service = detail(rows=[sport_row(GYM)])

    assert result.records == [WorkoutDetail(to_workout(GYM), None, None)]
    assert result.raw == [{"workout": GYM, "record": None, "gps": None}]
    assert transport.link_calls == []
    assert service.fetched == []
    assert result.notes == ["Workout 1: detail is not supported for this sport yet (format 8)."]


@pytest.mark.parametrize(
    ("figures", "what"),
    [
        ({"time": ABSENT}, "report time"),
        ({"time": "x"}, "report time"),
        ({"time": 2**32}, "report time"),
        ({"time": 0}, "report time"),
        ({"timezone": 200}, "time zone"),
        ({"proto_type": ABSENT}, "format number"),
    ],
    ids=["time-absent", "time-text", "time-too-large", "time-zero", "zone-200", "no-format"],
)
def test_summary_without_usable_report_time_or_zone_is_noted(figures, what):
    value = sport_value(proto=22, sport=1, start_time=T, **{"time": T, **figures})

    result, transport, service = detail(rows=[sport_row(value)])

    assert result.records == [WorkoutDetail(to_workout(value), None, None)]
    assert result.raw == [{"workout": value, "record": None, "gps": None}]
    assert transport.link_calls == []
    assert service.fetched == []
    assert result.notes == [
        f"Workout 1: its summary has no usable {what}, so its detail files cannot be named."
    ]


@pytest.mark.parametrize("sid", [None, "", 1000000006, "\ud800"], ids=repr)
def test_row_without_sid_is_noted(sid):
    # A lone surrogate cannot be hashed into a file name, so it counts as no id.
    result, transport, service = detail(rows=[sport_row(RUN, sid=sid)])

    assert result.records == [WorkoutDetail(to_workout(RUN), None, None)]
    assert transport.link_calls == []
    assert service.fetched == []
    assert result.notes == [
        "Workout 1: its row has no source id, so its detail files cannot be named."
    ]


def test_workout_without_start_gives_no_record():
    result, transport, _ = detail(rows=[row_without_start()])

    assert result.records == []
    assert result.raw == []
    assert result.notes == [NO_START_NOTE]
    assert transport.link_calls == []


def test_no_start_note_is_the_one_workouts_gives():
    rows = [row_without_start()]
    reference = fetch_workouts(PagedFake({"code": 0, "result": {"sport_records": rows}}), START, END)

    result, _, _ = detail(rows=rows)

    assert result.notes == reference.notes == [NO_START_NOTE]
    assert workout_detail_module.NO_START_NOTE is workouts_module.NO_START_NOTE


def test_track_false_requests_only_the_record_file():
    result, transport, service = detail(track=False)

    assert [call["data_obj"]["items"] for call in transport.link_calls] == [
        [{"timestamp": T, "suffix": suffix(REFS[0])}],
        [{"timestamp": U - 1, "suffix": suffix(REFS[2])}],
    ]
    assert len(service.fetched) == 2
    assert [len(d.samples or []) for d in result.records] == [2, 2, 0]
    assert all(d.track is None for d in result.records)
    assert all(r["gps"] is None for r in result.raw)
    assert not any("GPS" in note for note in result.notes)
    assert result.notes == [GYM_NOTE]


# --- Files that are not read -----------------------------------------------------------


@pytest.mark.parametrize(
    ("files", "entry_overrides"),
    [
        (files_with(run_gps=None), None),  # an entry without ``obj_key``, as live
        (FILES, {RUN_GPS_KEY: {"obj_key": ""}}),
        (FILES, {RUN_GPS_KEY: "not an entry"}),  # no entry at all
    ],
    ids=["no-key", "empty-key", "no-entry"],
)
def test_missing_file_is_not_downloaded(files, entry_overrides):
    result, _, service = detail(files=files, entry_overrides=entry_overrides)

    assert len(service.fetched) == 3  # run record, ride record, ride GPS
    run = result.records[0]
    assert run.track is None
    assert result.raw[0]["gps"] is None
    assert len(run.samples) == 2
    assert result.notes == ["Workout 1: the service has no GPS file for it.", GYM_NOTE]


def test_link_request_error_affects_only_that_workout():
    result, transport, _ = detail(link_errors={0: ApiError("Request failed with HTTP 500.")})

    assert len(transport.link_calls) == 2
    run = result.records[0]
    assert (run.samples, run.track) == (None, None)
    assert result.raw[0] == {"workout": RUN, "record": None, "gps": None}
    assert_ride_read_in_full(result)
    assert result.notes == [
        "Workout 1: could not get the download links (Request failed with HTTP 500.).",
        GYM_NOTE,
    ]
    assert "HTTP 500" in result.notes[0]


class AnswerHttp:
    """An ``http`` for a real ``Transport`` that answers every GET with ``status`` and
    the body ``text``."""

    def __init__(self, status, text):
        self._status = status
        self._text = text

    def get(self, url, params=None, headers=None, timeout=None):
        return SimpleNamespace(status_code=self._status, text=self._text)


def link_error(http):
    """The ``ApiError`` a real ``Transport`` over ``http`` raises for the link request."""
    transport = Transport(synthetic_session(), http=http, delay_s=0)
    with pytest.raises(ApiError) as caught:
        request_urls(transport, SYNTH_SID, REFS[:2])
    return caught.value


class NoFileMap:
    """A transport whose link answer succeeds but carries no file map."""

    def get_json(self, endpoint_path, data_obj=None, *, sign_path=None, base_path=None):
        return {"code": 0, "message": "ok", "result": []}


def no_file_map_error():
    with pytest.raises(ApiError) as caught:
        request_urls(NoFileMap(), SYNTH_SID, REFS[:2])
    return caught.value


@pytest.mark.parametrize(
    ("make_error", "quoted"),
    [
        (lambda: link_error(AnswerHttp(500, "")), "Request failed with HTTP 500."),
        (lambda: link_error(AnswerHttp(403, "")), "Request failed with HTTP 403."),
        (
            lambda: link_error(AnswerHttp(200, "not an encrypted body")),
            "Response body could not be decrypted or parsed as a JSON object.",
        ),
        (no_file_map_error, "The download-link answer carried no file map."),
    ],
    ids=["http-500", "http-403", "undecryptable", "no-file-map"],
)
def test_link_error_quotes_only_the_fixed_texts(make_error, quoted):
    result, _, _ = detail(link_errors={0: make_error()})

    assert result.notes[0] == f"Workout 1: could not get the download links ({quoted})."


SERVICE_TEXTS = {
    "secrets": f"device {SYNTH_SID} refused {suffix(REFS[0])}",
    "url marker": URL_MARK,
    "partial suffix": suffix(REFS[0])[:12],
    "control characters": "bad\x1b[31m\r\nrequest\x00",
    "plain text": "Service is busy, try later.",
    "lookalike": "Request failed with HTTP 500. See https://example.invalid/x",
}


@pytest.mark.parametrize("message", SERVICE_TEXTS.values(), ids=list(SERVICE_TEXTS))
def test_service_text_of_a_link_error_is_never_quoted(message):
    # The message of an ApiError can be the service's own text: a note gives its
    # business code, or only the exception's type, never the text.
    without_code, _, _ = detail(link_errors={0: ApiError(message)})
    with_code, _, _ = detail(link_errors={0: ApiError(message, code=70016)})

    assert without_code.notes[0] == "Workout 1: could not get the download links (ApiError)."
    assert with_code.notes[0] == (
        "Workout 1: could not get the download links (service code 70016)."
    )


def test_business_error_from_the_transport_gives_its_code():
    http = EncryptingHttp([{"code": 5, "message": f"refused {SYNTH_SID} {URL_MARK}"}])

    result, _, _ = detail(link_errors={0: link_error(http)})

    assert result.notes[0] == "Workout 1: could not get the download links (service code 5)."


@pytest.mark.parametrize(
    "code", [True, "5", 5.0, int(SYNTH_SID), int(SYNTH_SID + "7")], ids=repr
)
def test_link_error_code_is_quoted_only_as_a_plain_int_without_the_source_id(code):
    result, _, _ = detail(link_errors={0: ApiError("busy", code=code)})

    assert result.notes[0] == "Workout 1: could not get the download links (ApiError)."


def test_auth_error_propagates():
    with pytest.raises(AuthError):
        detail(link_errors={0: AuthError("x")})


def test_network_error_of_the_link_request_propagates():
    with pytest.raises(OSError):
        detail(link_errors={0: OSError("x")})


def test_download_failure_gives_none_and_note():
    result, _, _ = detail(fetch_errors={RUN_GPS_KEY: FdsUnavailable("HTTP 404")})

    run = result.records[0]
    assert run.track is None
    assert result.raw[0]["gps"] is None
    assert len(run.samples) == 2
    assert result.notes[0] == "Workout 1: the GPS file could not be downloaded: HTTP 404."
    assert result.notes[0].endswith("could not be downloaded: HTTP 404.")
    assert result.notes[1:] == [GYM_NOTE]


@pytest.mark.parametrize("method", ["PUT", "post"])
def test_link_that_is_not_get_is_not_fetched(method):
    result, _, service = detail(entry_overrides={RUN_RECORD_KEY: {"method": method}})

    assert len(service.fetched) == 3
    assert result.records[0].samples is None
    assert result.raw[0]["record"] is None
    assert len(result.records[0].track) == 2
    assert result.notes == [
        "Workout 1: the record file's link is not a download (GET) link, so it was not fetched.",
        GYM_NOTE,
    ]


@pytest.mark.parametrize("method", ["get", ABSENT], ids=["lower-case", "absent"])
def test_link_with_lower_case_or_no_method_is_fetched(method):
    result, _, service = detail(entry_overrides={RUN_RECORD_KEY: {"method": method}})

    assert len(service.fetched) == 4
    assert len(result.records[0].samples) == 2
    assert result.notes == [GYM_NOTE]


def test_undecryptable_file_gives_none_and_note():
    result, _, _ = detail(entry_overrides={RUN_RECORD_KEY: {"obj_key": WRONG_KEY}})

    assert result.records[0].samples is None
    assert result.raw[0]["record"] is None
    assert len(result.records[0].track) == 2
    assert result.notes == ["Workout 1: the record file could not be decrypted.", GYM_NOTE]


@pytest.mark.parametrize(
    "plain",
    [run_record(T + 5), run_record(tz15=33), run_record(proto=23)],
    ids=["time", "zone", "format"],
)
def test_header_mismatch_is_not_parsed(plain):
    result, _, _ = detail(files=files_with(run_record=plain))

    assert result.records[0].samples is None
    assert result.raw[0]["record"] is None
    assert len(result.records[0].track) == 2
    assert result.notes == [
        "Workout 1: the record file's header does not match the workout summary, "
        "so it was not read.",
        GYM_NOTE,
    ]


def test_unsupported_version_keeps_only_the_header():
    result, _, _ = detail(files=files_with(run_record=RUN_RECORD_V9))

    assert result.raw[0]["record"] == {
        "header": {
            "timestamp": T,
            "timezone": 32,
            "version": 9,
            "protoType": 22,
            "encryptionCompressionMode": 0,
            "validityFlags": RUN_FLAGS_ALL.hex(),
        }
    }
    assert result.records[0].samples is None
    assert len(result.records[0].track) == 2
    assert result.notes == [
        "Workout 1: format version 9 of the record file is not supported yet.",
        GYM_NOTE,
    ]
    assert "format version 9 of the record file" in result.notes[0]


def test_unsupported_gps_version_keeps_only_the_header():
    result, _, _ = detail(files=files_with(run_gps=run_gps(version=3)))

    assert result.raw[0]["gps"] == {
        "header": {
            "timestamp": T,
            "timezone": 32,
            "version": 3,
            "protoType": 22,
            "encryptionCompressionMode": 0,
            "validityFlags": "ff",
        }
    }
    assert result.records[0].track is None
    assert result.notes == [
        "Workout 1: format version 3 of the GPS file is not supported yet.",
        GYM_NOTE,
    ]


@pytest.mark.parametrize(
    "plain",
    [RUN_RECORD_MODE_1, header_bytes(T, 32, 8, 22, mode=1)],
    ids=["full", "header-only"],
)
def test_compressed_file_is_not_parsed(plain):
    # A file that ends right after its header is not "damaged": in storage mode 1
    # nothing after the header can be read anyway.
    result, _, _ = detail(files=files_with(run_record=plain))

    assert result.raw[0]["record"] == {
        "header": {
            "timestamp": T,
            "timezone": 32,
            "version": 8,
            "protoType": 22,
            "encryptionCompressionMode": 1,
        }
    }
    assert result.records[0].samples is None
    assert result.notes == [
        "Workout 1: the record file uses storage mode 1 (compressed or encrypted), "
        "which is not supported yet.",
        GYM_NOTE,
    ]


def test_damaged_file_gives_none_and_note():
    result, _, _ = detail(files=files_with(run_record=RUN_RECORD_CUT))

    assert result.raw[0]["record"] is None
    assert result.records[0].samples is None
    assert len(result.records[0].track) == 2
    assert result.raw[0]["gps"]["gpsPoints"]
    assert result.notes == [
        "Workout 1: the record file is damaged (a segment claims more points than the "
        "file holds), so it was not read.",
        GYM_NOTE,
    ]
    assert "damaged" in result.notes[0]


def test_file_too_short_for_its_header_is_damaged():
    result, _, _ = detail(files=files_with(ride_gps=RIDE_GPS[:5]))

    assert result.raw[1]["gps"] is None
    assert result.records[1].track is None
    assert result.notes == [
        "Workout 2: the GPS file is damaged (the file ends inside its header), "
        "so it was not read.",
        GYM_NOTE,
    ]


@pytest.mark.parametrize(
    ("changes", "note"),
    [
        (
            {"run_record": RUN_RECORD + b"\x00" * 3},
            "Workout 1: ignored 3 trailing byte(s) at the end of the record file.",
        ),
        (
            {"run_gps": RUN_GPS + b"\x00" * 5},
            "Workout 1: ignored 5 trailing byte(s) at the end of the GPS file.",
        ),
    ],
    ids=["record", "gps"],
)
def test_trailing_bytes_are_noted(changes, note):
    result, _, _ = detail(files=files_with(**changes))

    run = result.records[0]
    assert len(run.samples) == 2
    assert len(run.track) == 2
    assert result.notes == [note, GYM_NOTE]


def test_gps_without_valid_positions_has_no_track():
    result, _, _ = detail(files=files_with(run_gps=RUN_GPS_INVALID))

    gps_points = result.raw[0]["gps"]["gpsPoints"]
    assert len(gps_points) == 2
    assert all("time" not in point for point in gps_points)
    assert result.records[0].track is None
    assert len(result.records[0].samples) == 2
    assert result.notes == [
        "Workout 1: the GPS file marks its times or positions invalid, so there is no track.",
        GYM_NOTE,
    ]


def test_dropped_track_points_are_counted():
    result, _, _ = detail(files=files_with(run_gps=RUN_GPS_WITH_ZERO_FIX))

    assert result.records[0].track == [TrackPoint(T, f32(10.0001), f32(20.0001))]
    assert len(result.raw[0]["gps"]["gpsPoints"]) == 2
    assert result.notes == [
        "Workout 1: left out 1 track point(s) without a usable position.",
        GYM_NOTE,
    ]


def test_record_file_without_points_gives_empty_samples():
    empty = record_file(T, 32, 22, 8, RUN_FLAGS_LIVE)

    result, _, _ = detail(files=files_with(run_record=empty))

    assert result.records[0].samples == []
    assert result.raw[0]["record"]["segments"] == []
    assert result.notes == [GYM_NOTE]


# --- The cap -------------------------------------------------------------------------------


def test_cap_reads_only_the_newest_ten_supported_workouts():
    runs = [
        sport_value(proto=22, sport=1, time=T - k * 3600, start_time=T - k * 3600)
        for k in range(12)
    ]
    oldest = T - 12 * 3600
    gym = sport_value(proto=8, sport=308, time=oldest, start_time=oldest)
    files = {}
    for value in runs:
        files[file_key(value, RECORD)] = run_record(value["time"])
        files[file_key(value, GPS)] = gps_file(
            value["time"], 32, 22, gps_point(value["time"], 20.0001, 10.0001)
        )
    rows = [sport_row(value) for value in [*runs, gym]]

    result, transport, service = detail(rows=rows, files=files, start=oldest, end=T)

    assert len(transport.link_calls) == 10
    assert len(service.fetched) == 20
    assert len(result.records) == len(result.raw) == 13
    assert all(d.samples and d.track for d in result.records[:10])
    assert [(d.samples, d.track) for d in result.records[10:]] == [(None, None)] * 3
    cap_notes = [note for note in result.notes if note.startswith("Read the detail")]
    assert len(cap_notes) == 1
    assert "2 older" in cap_notes[0]
    assert result.notes == [
        "Workout 13: detail is not supported for this sport yet (format 8).",
        "Read the detail of the newest 10 supported workouts only; 2 older one(s) in the "
        "range have no samples or track: ask for a shorter range.",
    ]


# --- Alignment and secrets ---------------------------------------------------------------


def test_records_and_raw_are_index_aligned(monkeypatch):
    reads = []

    def spy(*args):
        read = sport_rows(*args)
        reads.append(read)
        return read

    monkeypatch.setattr(workout_detail_module, "sport_rows", spy)
    rows = [
        sport_row(RUN),
        {"sid": SYNTH_SID, "value": "{}"},  # no outer time: skipped by the list read
        row_without_start(),
        sport_row(GYM),
        sport_row(RIDE),
    ]

    result, _, _ = detail(rows=rows)

    assert len(result.records) == len(result.raw) == 3
    kept = [
        row.value
        for row in reads[0].rows
        if row.value is not None and to_workout(row.value) is not None
    ]
    assert len(kept) == 3
    for detail_record, raw, value in zip(result.records, result.raw, kept, strict=True):
        assert raw["workout"] is value
        assert detail_record.workout == to_workout(value)
    assert [d.samples is not None for d in result.records] == [True, False, True]
    # Notes number a workout by its position in ``records``.
    assert result.notes == [
        "Skipped a workout row without a usable time.",
        NO_START_NOTE,
        "Workout 2: detail is not supported for this sport yet (format 8).",
    ]


def eleven_runs():
    """Rows of eleven runs a second apart, newest first: one more than the cap."""
    return [
        sport_row(sport_value(proto=22, sport=1, time=T - k, start_time=T - k))
        for k in range(11)
    ]


def test_list_end_notes_follow_the_cap_note():
    service = FakeFdsService({})
    transport = FdsTransport(eleven_runs(), service, truncated=True, stalled=True)

    result = fetch_workout_detail(transport, T - 10, T, downloader=service, track=False)

    assert result.notes[-3].startswith("Read the detail of the newest 10")
    assert result.notes[-2].startswith("Workout list stopped at its 20-page cap")
    assert result.notes[-1].startswith("The service said more workout rows followed")


SECRETS = [
    SYNTH_SID,
    URL_MARK,
    "SYNTH-OBJ-NAME",
    SYNTH_KEY_B64,
    "fds.api.xiaomi.com",
    str(EXPIRES),
    *(suffix(ref) for ref in REFS),
]


def test_output_never_contains_link_secrets():
    result, _, _ = detail()
    assert result.records[0].samples and result.records[1].track  # a full read

    text = json.dumps(
        {
            "records": [dataclasses.asdict(r) for r in result.records],
            "raw": result.raw,
            "notes": result.notes,
        }
    )
    for secret in SECRETS:
        assert secret not in text
        assert secret not in repr(result)


FAILURES = {
    "unsupported sport": {"rows": [sport_row(GYM)]},
    "no report time": {
        "rows": [sport_row(sport_value(proto=22, sport=1, time="x", start_time=T))]
    },
    "no source id": {"rows": [sport_row(RUN, sid=None)]},
    "no start time": {"rows": [row_without_start()]},
    "link error": {"link_errors": {0: ApiError("Request failed with HTTP 500.")}},
    "link error naming secrets": {
        "link_errors": {0: ApiError(f"{SYNTH_SID} {suffix(REFS[0])} {URL_MARK}")}
    },
    "no file": {"files": files_with(run_gps=None)},
    "not GET": {"entry_overrides": {RUN_RECORD_KEY: {"method": "PUT"}}},
    "download failure": {"fetch_errors": {RUN_GPS_KEY: FdsUnavailable("HTTP 404")}},
    "undecryptable": {"entry_overrides": {RUN_RECORD_KEY: {"obj_key": WRONG_KEY}}},
    "header mismatch": {"files": files_with(run_record=run_record(T + 5))},
    "unsupported version": {"files": files_with(run_record=RUN_RECORD_V9)},
    "storage mode": {"files": files_with(run_record=RUN_RECORD_MODE_1)},
    "damaged": {"files": files_with(run_record=RUN_RECORD_CUT)},
    "trailing bytes": {"files": files_with(run_gps=RUN_GPS + b"\x00")},
    "invalid positions": {"files": files_with(run_gps=RUN_GPS_INVALID)},
    "dropped points": {"files": files_with(run_gps=RUN_GPS_WITH_ZERO_FIX)},
    "cap": {"rows": eleven_runs(), "files": {}, "start": T - 10, "end": T},
}


@pytest.mark.parametrize("options", FAILURES.values(), ids=list(FAILURES))
def test_failures_do_not_echo_secrets_in_notes(options):
    result, _, _ = detail(**options)

    assert result.notes != [GYM_NOTE]  # the failure path was taken
    for note in result.notes:
        for secret in SECRETS:
            assert secret not in note
