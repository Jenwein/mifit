"""The cloud file header (spec §4.6) and the three enabled layouts: run record v8
and ride record v5 (§4.7), GPS v2 (§4.8).

Every file here is packed by ``fds_fixtures`` straight from the spec's tables,
without ``mifit.fds.layouts``, so a wrong table entry fails instead of
round-tripping.
"""

import struct
import time

import pytest
from fds_fixtures import (
    RIDE_FLAGS_LIVE,
    RIDE_FLAGS_NO_CADENCE,
    RUN_FLAGS_ALL,
    RUN_FLAGS_LIVE,
    f32,
    gps_file,
    gps_point,
    header_bytes,
    record_file,
    ride_point,
    ride_segment,
    run_point,
    run_segment,
)

from mifit.fds import layouts
from mifit.fds.binary import FdsFormatError, FileHeader, Reader, parse_header, validity_len
from mifit.fds.ids import GPS, RECORD
from mifit.fds.layouts import LAYOUTS, RIDE_FIELDS, RUN_FIELDS, decode, selected_fields

TS = 1700000000
TZ = -20

# The A names and stored values of the default ``run_point()`` / ``ride_point()``.
RUN_DEFAULTS = {
    "newCalories": 1,
    "newSteps": 3,
    "heartRate": 120,
    "isKilometer": 0,
    "isAltitudeRising": 1,
    "altitudeChange": 5,
    "newDistance": 25,
    "stride": 80,
    "newImpactForce": 10,
    "newContactTime": 250,
    "newFlightTime": 100,
    "contactRatio": 40,
    "cadence": 170,
    "pace": 360,
    "equalIntensityPace": 365,
    "runningPower": 200,
    "verticalStrideRatio": 85,
    "verticalAmplitude": 80,
}
RIDE_LIVE_DEFAULTS = {
    "newCalories": 2,
    "heartRate": 110,
    "isKilometer": 0,
    "isAltitudeRising": 0,
    "altitudeChange": 3,
    "newDistance": 5,
    "speed": 250,
}


def decode_file(plain, file_type=RECORD):
    header = parse_header(plain, file_type)
    return decode(plain, header, LAYOUTS[(header.proto_type, file_type, header.version)])


def run_file(*segments, flags=RUN_FLAGS_ALL):
    return record_file(TS, TZ, 22, 8, flags, *segments)


# --- Header -------------------------------------------------------------------------


def test_parse_header_reads_the_cloud_header():
    plain = record_file(1700000000, -20, 22, 8, RUN_FLAGS_LIVE)

    header = parse_header(plain, RECORD)

    assert header == FileHeader(1700000000, -20, 8, 22, 0, RUN_FLAGS_LIVE)
    raw = header.as_raw()
    assert raw["validityFlags"] == "fffffabffbbfff"
    assert list(raw.items()) == [
        ("timestamp", 1700000000),
        ("timezone", -20),
        ("version", 8),
        ("protoType", 22),
        ("encryptionCompressionMode", 0),
        ("validityFlags", "fffffabffbbfff"),
    ]


@pytest.mark.parametrize(
    ("proto", "file_type", "version", "expected"),
    [
        (22, RECORD, 1, 2),
        (22, RECORD, 4, 3),
        (22, RECORD, 5, 5),
        (22, RECORD, 7, 6),
        (22, RECORD, 8, 7),
        (22, RECORD, 9, 7),
        (22, RECORD, 10, None),
        (23, RECORD, 2, 2),
        (23, RECORD, 6, 3),
        (23, RECORD, 7, 4),
        (23, RECORD, 8, None),
        (23, GPS, 4, 1),
        (23, GPS, 5, 2),
        (23, GPS, 6, None),
        (8, RECORD, 1, None),
    ],
)
def test_validity_lengths(proto, file_type, version, expected):
    assert validity_len(proto, file_type, version) == expected


def test_short_files_are_refused():
    with pytest.raises(FdsFormatError, match="ends inside its header"):
        parse_header(run_file()[:7], RECORD)
    with pytest.raises(FdsFormatError, match="ends inside its validity flags"):
        parse_header(header_bytes(TS, TZ, 8, 22), RECORD)


def test_unknown_validity_gives_no_flags():
    header = parse_header(header_bytes(TS, 32, 1, 8) + b"\xff\xff", RECORD)

    assert header.validity is None
    assert "validityFlags" not in header.as_raw()


# --- Tables -------------------------------------------------------------------------


def test_run_v8_fields_in_byte_order():
    assert [f.names for f in selected_fields(RUN_FIELDS, 8)] == [
        ("newCalories", "newSteps"),
        ("heartRate",),
        ("isKilometer", "isAltitudeRising", "altitudeChange"),
        ("newDistance",),
        ("stride",),
        ("newImpactForce", "newContactTime", "newFlightTime"),
        ("contactRatio",),
        ("cadence",),
        ("pace",),
        ("equalIntensityPace",),
        ("runningPower",),
        ("verticalStrideRatio",),
        ("verticalAmplitude",),
    ]


def test_ride_v5_fields_in_byte_order():
    assert [f.names for f in selected_fields(RIDE_FIELDS, 5)] == [
        ("newCalories", "workoutcount"),
        ("heartRate",),
        ("isKilometer", "isAltitudeRising", "altitudeChange"),
        ("newDistance",),
        ("speed",),
        ("cadence",),
    ]


def test_enabled_layouts():
    assert set(LAYOUTS) == {(22, 0, 8), (23, 0, 5), (22, 2, 2), (23, 2, 2)}


# --- Record, format 22 v8 -----------------------------------------------------------


def test_run_v8_all_valid_point():
    decoded = decode_file(run_file(run_segment([run_point()], resume=TS)))

    [segment] = decoded.raw["segments"]
    assert segment["dataPoints"] == [RUN_DEFAULTS]
    assert decoded.trailing_bytes == 0


def test_run_bit_fields_at_their_limits():
    point = run_point(
        calories=15, steps=15, kilometer=1, altitude_change=63, impact=63, contact=8191, flight=8191
    )

    decoded = decode_file(run_file(run_segment([point], resume=TS)))

    assert decoded.raw["segments"][0]["dataPoints"] == [
        RUN_DEFAULTS
        | {
            "newCalories": 15,
            "newSteps": 15,
            "isKilometer": 1,
            "altitudeChange": 63,
            "newImpactForce": 63,
            "newContactTime": 8191,
            "newFlightTime": 8191,
        }
    ]


def test_run_v8_live_shape_flags():
    plain = run_file(
        run_segment([run_point(), run_point(heart_rate=121)], resume=TS), flags=RUN_FLAGS_LIVE
    )

    decoded = decode_file(plain)

    points = decoded.raw["segments"][0]["dataPoints"]
    assert len(points) == 2
    dropped = {
        "newImpactForce",
        "newFlightTime",
        "contactRatio",
        "equalIntensityPace",
        "runningPower",
    }
    for point in points:
        assert not dropped & point.keys()
        assert point["newContactTime"] == 250
    kept = {k: v for k, v in RUN_DEFAULTS.items() if k not in dropped}
    assert points == [kept, kept | {"heartRate": 121}]
    assert len(plain) == 8 + 7 + 27 + 2 * 21
    assert decoded.trailing_bytes == 0


# --- Record, format 23 v5 -----------------------------------------------------------


def ride_points(with_cadence=True):
    return [
        ride_point(with_cadence=with_cadence),
        ride_point(with_cadence=with_cadence, workoutcount=7, cadence=90, heart_rate=111),
    ]


def test_ride_v5_live_shape_flags():
    plain = record_file(TS, TZ, 23, 5, RIDE_FLAGS_LIVE, ride_segment(ride_points(), resume=TS))

    decoded = decode_file(plain)

    assert len(plain) == 8 + 3 + 23 + 2 * 7
    assert decoded.raw["segments"][0]["dataPoints"] == [
        RIDE_LIVE_DEFAULTS,
        RIDE_LIVE_DEFAULTS | {"heartRate": 111},
    ]
    assert decoded.trailing_bytes == 0


def test_field_without_exists_takes_no_bytes():
    live = record_file(TS, TZ, 23, 5, RIDE_FLAGS_LIVE, ride_segment(ride_points(), resume=TS))
    segment = ride_segment(ride_points(with_cadence=False), resume=TS)
    no_cadence = record_file(TS, TZ, 23, 5, RIDE_FLAGS_NO_CADENCE, segment)

    decoded = decode_file(no_cadence)

    assert len(no_cadence) == 8 + 3 + 23 + 2 * 6
    assert decoded.raw["segments"] == decode_file(live).raw["segments"]
    assert decoded.trailing_bytes == 0


# --- Segments -----------------------------------------------------------------------


def test_segments_and_pause_info():
    first = run_segment(
        [run_point(), run_point()],
        resume=TS,
        initial_altitude=12.25,
        pause_type=0,
        segment_type=3,
        total_distance=5,
        total_steps=6,
        segment_pace=400,
    )
    second = run_segment([run_point(heart_rate=130)], resume=TS + 100)  # info 0x84

    segments = decode_file(run_file(first, second)).raw["segments"]

    keys = [
        "initialAltitude",
        "recordCount",
        "resumeTimestamp",
        "pauseType",
        "segmentType",
        "totalDistance",
        "totalDuration",
        "totalSteps",
        "segmentPace",
        "dataPoints",
    ]
    assert [list(s) for s in segments] == [keys, keys]
    assert {k: v for k, v in segments[0].items() if k != "dataPoints"} == {
        "initialAltitude": 12.25,
        "recordCount": 2,
        "resumeTimestamp": TS,
        "pauseType": 0,
        "segmentType": 3,
        "totalDistance": 5,
        "totalDuration": 2,
        "totalSteps": 6,
        "segmentPace": 400,
    }
    assert segments[1]["resumeTimestamp"] == TS + 100
    assert (segments[1]["pauseType"], segments[1]["segmentType"]) == (1, 4)
    assert isinstance(segments[1]["initialAltitude"], float)
    assert segments[1]["initialAltitude"] == 50.0
    assert [len(s["dataPoints"]) for s in segments] == [2, 1]
    assert segments[1]["dataPoints"] == [RUN_DEFAULTS | {"heartRate": 130}]


def test_ride_segment_header():
    plain = record_file(
        TS,
        TZ,
        23,
        5,
        RIDE_FLAGS_LIVE,
        ride_segment(ride_points(), resume=TS, total_distance=10, total_speed=255),
    )

    [segment] = decode_file(plain).raw["segments"]

    assert {k: v for k, v in segment.items() if k != "dataPoints"} == {
        "initialAltitude": 50.0,
        "recordCount": 2,
        "resumeTimestamp": TS,
        "pauseType": 1,
        "segmentType": 0,
        "totalDistance": 10,
        "totalTime": 2,
        "totalSpeed": 255,
    }


def test_record_count_beyond_remaining_bytes_is_refused():
    plain = run_file(run_segment([run_point(), run_point()], resume=TS, record_count=3))

    with pytest.raises(FdsFormatError, match="more points than the file holds"):
        decode_file(plain)


def test_file_cut_inside_a_point_is_refused():
    plain = run_file(run_segment([run_point(), run_point()], resume=TS))

    with pytest.raises(FdsFormatError):
        decode_file(plain[:-5])


def test_total_points_are_capped():
    plain = run_file(
        run_segment([], resume=TS, record_count=2**32 - 1), flags=bytes.fromhex("77777777777777")
    )

    started = time.perf_counter()
    with pytest.raises(FdsFormatError, match="more than 1000000 points"):
        decode_file(plain)
    assert time.perf_counter() - started < 0.5


def test_point_cap_counts_every_segment(monkeypatch):
    monkeypatch.setattr(layouts, "MAX_POINTS", 3)
    one, two = [run_point()], [run_point(), run_point()]

    three = decode_file(run_file(run_segment(one, resume=TS), run_segment(two, resume=TS + 9)))
    assert [len(s["dataPoints"]) for s in three.raw["segments"]] == [1, 2]
    with pytest.raises(FdsFormatError, match="points"):
        decode_file(run_file(run_segment(two, resume=TS), run_segment(two, resume=TS + 9)))


def test_short_tail_is_trailing():
    plain = run_file(
        run_segment([run_point()], resume=TS),
        run_segment([run_point(heart_rate=99)], resume=TS + 50),
    )

    decoded = decode_file(plain + bytes(5))

    assert decoded.trailing_bytes == 5
    assert decoded.raw == decode_file(plain).raw


def test_flags_shorter_than_the_field_table_are_refused():
    plain = run_file(run_segment([run_point()], resume=TS))
    header = FileHeader(TS, TZ, 8, 22, 0, RUN_FLAGS_ALL[:6])

    with pytest.raises(FdsFormatError, match="validity flags are shorter than the field table"):
        decode(plain, header, LAYOUTS[(22, RECORD, 8)])


def test_record_raw_shape():
    plain = run_file(run_segment([run_point()], resume=TS))

    decoded = decode_file(plain)

    assert list(decoded.raw) == ["header", "segments"]
    assert decoded.raw["header"] == parse_header(plain, RECORD).as_raw()


# --- GPS v2 -------------------------------------------------------------------------


def gps_points(count):
    return [
        gps_point(TS + i, 20.0001 + i / 1e4, 10.0001 + i / 1e4, speed=30, source=2)
        for i in range(count)
    ]


@pytest.mark.parametrize("proto", [22, 23])
def test_gps_v2_points(proto):
    plain = gps_file(TS, TZ, proto, *gps_points(3))

    decoded = decode_file(plain, GPS)

    assert list(decoded.raw) == ["header", "gpsPoints"]
    assert decoded.raw["header"] == parse_header(plain, GPS).as_raw()
    assert decoded.raw["gpsPoints"] == [
        {
            "time": TS + i,
            "longitude": f32(20.0001 + i / 1e4),
            "latitude": f32(10.0001 + i / 1e4),
            "locationHorizontalAccuracy": f32(4.5),
            "locationSpeed": 30,
            "gpsSource": 2,
        }
        for i in range(3)
    ]
    assert all(p["longitude"] != p["latitude"] for p in decoded.raw["gpsPoints"])
    assert decoded.trailing_bytes == 0


def test_gps_invalid_field_still_takes_bytes():
    decoded = decode_file(gps_file(TS, TZ, 22, *gps_points(2), flags=b"\xec"), GPS)

    assert decoded.raw["gpsPoints"] == [
        {
            "time": TS + i,
            "longitude": f32(20.0001 + i / 1e4),
            "latitude": f32(10.0001 + i / 1e4),
            "locationSpeed": 30,
            "gpsSource": 2,
        }
        for i in range(2)
    ]


def test_gps_source_needs_its_valid_bit():
    decoded = decode_file(gps_file(TS, TZ, 22, *gps_points(2), flags=b"\xf8"), GPS)

    for point in decoded.raw["gpsPoints"]:
        assert point["locationSpeed"] == 30
        assert "gpsSource" not in point


def test_gps_speed_and_source_bits():
    plain = gps_file(TS, TZ, 22, gps_point(TS, 20.5, 10.5, speed=4095, source=15))

    [point] = decode_file(plain, GPS).raw["gpsPoints"]

    assert (point["locationSpeed"], point["gpsSource"]) == (4095, 15)


def test_gps_tail_is_trailing():
    decoded = decode_file(gps_file(TS, TZ, 22, *gps_points(2)) + bytes(5), GPS)

    assert len(decoded.raw["gpsPoints"]) == 2
    assert decoded.trailing_bytes == 5


def test_gps_points_are_capped(monkeypatch):
    monkeypatch.setattr(layouts, "MAX_POINTS", 2)

    with pytest.raises(FdsFormatError):
        decode_file(gps_file(TS, TZ, 22, *gps_points(3)), GPS)


# --- Reader -------------------------------------------------------------------------


def test_reader_refuses_reads_past_the_end():
    reader = Reader(b"\x00\x01\x02\x03\x04" + struct.pack("<f", 1.5), 1)

    assert (reader.offset, reader.remaining) == (1, 8)
    assert reader.uint(2) == 0x0201
    reader.skip(2)
    assert reader.f32() == 1.5
    assert (reader.offset, reader.remaining) == (9, 0)
    for read in (lambda: reader.uint(1), reader.f32, lambda: reader.skip(1)):
        with pytest.raises(FdsFormatError):
            read()
    assert reader.offset == 9

    short = Reader(b"\x01\x02\x03", 0)
    with pytest.raises(FdsFormatError):
        short.f32()
    with pytest.raises(FdsFormatError):
        short.uint(4)
    assert short.offset == 0
    with pytest.raises(FdsFormatError):
        Reader(b"\x01", 2)
