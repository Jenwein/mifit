"""The enabled FDS file layouts and their decoder (spec §4.7, §4.8, D1).

The tables are hand-written from the APK's legacy parsers:

* ``RUN_FIELDS`` / ``RUN_SEGMENT``: format 22 (outdoor run) records,
  ``OutdoorStepRecordParser``;
* ``RIDE_FIELDS`` / ``RIDE_SEGMENT``: format 23 (outdoor ride) records,
  ``OutdoorNoStepRecordParser``;
* ``GPS_FIELDS``: GPS files of either format, ``SportGpsParser``.

They hold only the rows the enabled versions use: record v8 (format 22), record v5
(format 23) and GPS v2. ``since`` / ``until`` give each row's version range, and
``until`` marks where the v1 altitude and distance rows end. A newer version (record
v9 for format 22, v6+ for format 23, GPS v3+) needs the APK's extra rows first (the
4-byte altitude and 2-byte distance of §4.7, the GPS altitude and HDOP of §4.8),
checked against a real file, before its entry is added to ``LAYOUTS``.

Names are the ``schemas.zip`` field names (D6) and values are stored, unscaled
values: ``newDistance`` is decimetres in format 22 and metres in format 23. A
float32 becomes a Python float as it is. Invalid sub-values and fields that do not
exist are left out.

Decoding is bounds-checked (``binary.Reader``), checks every segment's point count
against the bytes left before reading it, and stops at ``MAX_POINTS`` points per file.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass

from .binary import HEADER_LEN, FdsFormatError, FileHeader, Reader
from .ids import GPS, RECORD

MAX_POINTS = 1_000_000

_TOO_MANY = f"the file holds more than {MAX_POINTS} points"


@dataclass(frozen=True)
class Field4D:
    """One field of a record point (the APK's ``FourDimenDataInfo``).

    The field takes ``size`` bytes, read as a little-endian unsigned integer, and
    holds up to three sub-values. ``names`` and ``bits`` run high → middle → low; a
    ``bits`` entry is ``(start, length)``, or ``None`` for the whole value.
    """

    names: tuple[str, ...]
    size: int
    bits: tuple[tuple[int, int] | None, ...]
    since: int
    until: int | None = None


_WHOLE = (None,)
_ALTITUDE = ("isKilometer", "isAltitudeRising", "altitudeChange")
_ALTITUDE_BITS = ((7, 1), (6, 1), (0, 6))

RUN_FIELDS = (
    Field4D(("newCalories", "newSteps"), 1, ((4, 4), (0, 4)), 1),
    Field4D(("heartRate",), 1, _WHOLE, 1),
    Field4D(_ALTITUDE, 1, _ALTITUDE_BITS, 1, 8),
    Field4D(("newDistance",), 1, _WHOLE, 1, 8),
    Field4D(("stride",), 1, _WHOLE, 2),
    Field4D(
        ("newImpactForce", "newContactTime", "newFlightTime"), 4, ((26, 6), (13, 13), (0, 13)), 4
    ),
    Field4D(("contactRatio",), 1, _WHOLE, 5),
    Field4D(("cadence",), 1, _WHOLE, 5),
    Field4D(("pace",), 2, _WHOLE, 5),
    Field4D(("equalIntensityPace",), 2, _WHOLE, 6),
    Field4D(("runningPower",), 2, _WHOLE, 6),
    Field4D(("verticalStrideRatio",), 2, _WHOLE, 8),
    Field4D(("verticalAmplitude",), 2, _WHOLE, 8),
)

RIDE_FIELDS = (
    Field4D(("newCalories", "workoutcount"), 1, ((4, 4), (0, 4)), 1),
    Field4D(("heartRate",), 1, _WHOLE, 1),
    Field4D(_ALTITUDE, 1, _ALTITUDE_BITS, 1, 5),
    Field4D(("newDistance",), 1, _WHOLE, 1, 5),
    Field4D(("speed",), 2, _WHOLE, 3),
    Field4D(("cadence",), 1, _WHOLE, 3),
)

# Segment headers: (name, struct code), in byte order. ``segmentInfo`` is decoded
# into ``pauseType`` (bit 7) and ``segmentType`` (bits 6..0).
RUN_SEGMENT = (
    ("initialAltitude", "<f"),
    ("recordCount", "<I"),
    ("resumeTimestamp", "<I"),
    ("segmentInfo", "<B"),
    ("totalDistance", "<I"),
    ("totalDuration", "<I"),
    ("totalSteps", "<I"),
    ("segmentPace", "<H"),
)  # 27 bytes

RIDE_SEGMENT = (
    *RUN_SEGMENT[:4],
    ("totalDistance", "<I"),
    ("totalTime", "<I"),
    ("totalSpeed", "<H"),
)  # 23 bytes


@dataclass(frozen=True)
class GpsField:
    """One field of a GPS point (the APK's ``OneDimenDataInfo``). ``code`` is its
    struct code, or ``None`` for a field that is only a validity bit (size 0)."""

    name: str
    size: int
    code: str | None
    since: int


GPS_FIELDS = (
    GpsField("time", 4, "<I", 1),
    GpsField("longitude", 4, "<f", 1),
    GpsField("latitude", 4, "<f", 1),
    GpsField("locationHorizontalAccuracy", 4, "<f", 2),
    GpsField("locationSpeedAndGpsSource", 2, "<H", 2),
    GpsField("gpsSourceValid", 0, None, 2),
)


@dataclass(frozen=True)
class RecordLayout:
    fields: tuple[Field4D, ...]
    segment: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class GpsLayout:
    fields: tuple[GpsField, ...]


LAYOUTS: dict[tuple[int, int, int], RecordLayout | GpsLayout] = {
    (22, RECORD, 8): RecordLayout(RUN_FIELDS, RUN_SEGMENT),
    (23, RECORD, 5): RecordLayout(RIDE_FIELDS, RIDE_SEGMENT),
    (22, GPS, 2): GpsLayout(GPS_FIELDS),
    (23, GPS, 2): GpsLayout(GPS_FIELDS),
}


@dataclass(frozen=True)
class DecodedFile:
    raw: dict  # the A layer: {"header", "segments"} or {"header", "gpsPoints"}
    trailing_bytes: int  # bytes after the last segment or point, too few for another


def selected_fields(fields: Sequence[Field4D], version: int) -> list[Field4D]:
    """The fields a file of ``version`` holds, in table (byte) order."""
    return [f for f in fields if f.since <= version and (f.until is None or version <= f.until)]


def decode(plain: bytes, header: FileHeader, layout: RecordLayout | GpsLayout) -> DecodedFile:
    """The A layer of ``plain``, whose ``header`` was read by ``binary.parse_header``.

    Raises ``FdsFormatError`` when the file does not fit the layout: no or too few
    validity flags, a segment that claims more points than the bytes left, more than
    ``MAX_POINTS`` points, or a read past the end.
    """
    if header.validity is None:
        raise FdsFormatError("the header has no validity flags")
    reader = Reader(plain, HEADER_LEN + len(header.validity))
    if isinstance(layout, GpsLayout):
        points = _gps_points(reader, header.version, header.validity, layout.fields)
        raw = {"header": header.as_raw(), "gpsPoints": points}
    else:
        segments = _segments(reader, header.version, header.validity, layout)
        raw = {"header": header.as_raw(), "segments": segments}
    return DecodedFile(raw, reader.remaining)


def _read(reader: Reader, code: str) -> int | float:
    """One value of a table's struct code: ``<f`` or an unsigned integer code."""
    return reader.f32() if code == "<f" else reader.uint(struct.calcsize(code))


def _segments(reader: Reader, version: int, validity: bytes, layout: RecordLayout) -> list[dict]:
    """Segments until fewer bytes than a segment header are left
    (``SportRecordBaseParser.parseFourDimenData``)."""
    fields = selected_fields(layout.fields, version)
    if len(fields) > 2 * len(validity):
        raise FdsFormatError("the validity flags are shorter than the field table")
    # One nibble per selected field, high nibble first: 8 = exists, 4 / 2 / 1 = the
    # high / middle / low sub-value is valid. A field that does not exist takes no bytes.
    present = []
    for k, field in enumerate(fields):
        nibble = validity[k // 2] >> 4 if k % 2 == 0 else validity[k // 2] & 0x0F
        if nibble & 8:
            valid = tuple(bool(nibble & bit) for bit in (4, 2, 1)[: len(field.names)])
            present.append((field, valid))
    point_size = sum(field.size for field, _ in present)
    header_size = sum(struct.calcsize(code) for _, code in layout.segment)

    segments = []
    total = 0
    while reader.remaining >= header_size:
        segment = {}
        for name, code in layout.segment:
            value = _read(reader, code)
            if name == "segmentInfo":
                segment["pauseType"] = value >> 7
                segment["segmentType"] = value & 0x7F
            else:
                segment[name] = value
        count = segment["recordCount"]
        total += count
        if total > MAX_POINTS:
            raise FdsFormatError(_TOO_MANY)
        if count * point_size > reader.remaining:
            raise FdsFormatError("a segment claims more points than the file holds")
        segment["dataPoints"] = [_record_point(reader, present) for _ in range(count)]
        segments.append(segment)
    return segments


def _record_point(reader: Reader, present: list[tuple[Field4D, tuple[bool, ...]]]) -> dict:
    """One point: each existing field's valid sub-values (``FourDimenDataParser``)."""
    point = {}
    for field, valid in present:
        value = reader.uint(field.size)
        for name, bits, ok in zip(field.names, field.bits, valid, strict=True):
            if ok:
                point[name] = value if bits is None else (value >> bits[0]) & ((1 << bits[1]) - 1)
    return point


def _gps_points(
    reader: Reader, version: int, validity: bytes, fields: Sequence[GpsField]
) -> list[dict]:
    """Points until fewer bytes than one point are left (``SportGpsParser``)."""
    selected = [f for f in fields if f.since <= version]
    width = 8 * len(validity)
    if len(selected) > width:
        raise FdsFormatError("the validity flags are shorter than the field table")
    # One bit per selected field, high bit first. An invalid field still takes its bytes.
    flags = int.from_bytes(validity, "big")
    valid = {f.name: bool((flags >> (width - 1 - k)) & 1) for k, f in enumerate(selected)}
    point_size = sum(f.size for f in selected)
    count = reader.remaining // point_size
    if count > MAX_POINTS:
        raise FdsFormatError(_TOO_MANY)

    points = []
    for _ in range(count):
        point = {}
        for f in selected:
            if f.code is None:
                continue  # a validity bit only, no bytes
            value = _read(reader, f.code)
            if not valid[f.name]:
                continue
            if f.name == "locationSpeedAndGpsSource":
                point["locationSpeed"] = (value & 0xFFF0) >> 4  # stored, not divided by 10
                if valid.get("gpsSourceValid", False):
                    point["gpsSource"] = value & 0x0F
            else:
                point[f.name] = value
        points.append(point)
    return points
