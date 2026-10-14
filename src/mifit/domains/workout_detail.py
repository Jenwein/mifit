"""Workout detail domain: each workout's per-second samples and GPS track, read from
its FDS files (spec §5) and mapped onto neutral records (spec §8).

``fetch_workout_detail`` reads the files. ``to_samples`` turns a decoded record file
into one ``WorkoutSample`` per recorded second, and ``to_track`` turns a decoded GPS
file into ``TrackPoint``s. Their input is the A layer of ``mifit.fds.layouts``:
stored, unscaled values under the ``schemas.zip`` field names (spec §9). This module
is the only place where those names meet the neutral ones, and the only place values
are scaled (spec §8).

The read (spec §5), for each workout of the list, newest first:

1. ``sport_rows`` reads the list exactly as ``workouts`` does: the same window rule,
   page cap and notes. It keeps each row's source id (``sid``) in memory to name the
   files; the id is never output.
2. A summary without a usable start time gives no record, with the ``workouts``
   note. Every other workout gives a ``WorkoutDetail`` (its ``workout`` is the
   ``workouts`` summary) and, at the same index, a ``raw`` entry
   ``{"workout": <the decoded summary>, "record": ..., "gps": ...}``.
3. Only formats 22 (outdoor run) and 23 (outdoor ride) have a detail here. Naming
   the files also takes a usable report time (the summary's ``time``, not its
   ``start_time``), a time zone and the row's source id. A workout without them
   gets a note and no request.
4. One signed ``gen_download_url`` request asks for the record file and, unless
   ``track=False``, the GPS file. An ``ApiError`` gives a note and the next workout
   is read; ``AuthError`` and ``OSError`` propagate.
5. Each file the service has (an entry with a key, under a GET link) is downloaded
   with ``downloader``, a client separate from the session, then decrypted. Its
   header must match the summary (time, time zone, format). A version without a
   layout, or a storage mode other than 0, keeps only the header in ``raw``.
   Otherwise the file is decoded into ``raw`` and mapped onto ``samples`` or
   ``track``; a file that does not fit its layout is left out whole, never half
   read.

Requests: one list read (at most 20 pages, as in ``workouts``), then for each
supported workout one link request and at most two downloads; a file the service
does not have is not downloaded. A call reads the detail of at most
``_MAX_WORKOUTS`` (10) supported workouts, the newest. The older ones still get a
record, with ``samples`` and ``track`` ``None``, and one note counts them.
Unsupported workouts send no request and do not count.

``samples`` and ``track`` are ``None`` when the file was not read (a note says why,
except for a track left out with ``track=False``), and ``[]`` when it was read and
held no points. In ``raw``, ``"record"`` and ``"gps"`` are ``None`` when the file
was not read, ``{"header": ...}`` when only its header was, and the decoded file
otherwise.

Notes name a workout by its 1-based position in ``records`` ("Workout 2: ..."). A
chunked read (``mifit.chunks``) counts within each chunk, and its merged notes carry
the chunk's label, so there "Workout 2" is the second workout of that chunk. A note
never holds a link, a key, a source id or a file name. The errors it quotes are fixed
fragments (an HTTP status, an exception's type, a host name, what part of a file is
damaged), except the link request's ``ApiError``, whose message is quoted unless it
holds the source id or a file name; then only its type is.

Nothing is cached and nothing is written to disk: each call downloads afresh.

The mapping rules (spec §8; each one checked against the workout's summary, §4.9):

* ``epoch_s`` is the segment's ``resumeTimestamp`` plus the point's index in it: the
  file holds one point per second, in true Unix seconds. The app's display shift is
  never applied (D11).
* ``heart_rate_bpm`` is ``heartRate``; ``0`` is the device's "no reading", so it
  becomes ``None``.
* ``distance_m`` is the running sum of ``newDistance`` over the whole workout,
  divided by the format's distance divisor (``_DISTANCE_DIVISOR``): format 22 stores
  decimetres, format 23 metres. A point without it gives ``None`` and adds nothing.
* ``steps`` is the running sum of ``newSteps``, format 22 only; format 23 records no
  steps.
* ``cadence_spm`` (``cadence``) and ``pace_s_per_km`` (``pace``) come from format 22
  only, and ``0`` becomes ``None``.
* ``speed_mps`` is ``speed`` (0.1 km/h) divided by 36, format 23 only. ``0`` is a
  real standstill and stays ``0.0``.
* ``altitude_m`` starts each segment at its ``initialAltitude``. In each second that
  has both ``isAltitudeRising`` and ``altitudeChange``, it moves by
  ``altitudeChange / 10`` metres, up or down as ``isAltitudeRising`` says, and that
  second's altitude is the new value. A second without both has no altitude and
  leaves the running value alone. A segment whose ``initialAltitude`` is not finite
  has no altitude at all. This is the curve the app charts
  (``SportRecordBaseParser``); for the run its extremes are 2-3 m off the summary's,
  which the live check settles (spec §16 Q4).
* A track point is the GPS ``time`` with the ``latitude`` and ``longitude`` float32
  values as they are, unrounded (the file stores longitude first). When any point
  lacks one of the three, the file marks them invalid and the app drops the whole
  track, so the track is ``None`` (D23). A point whose position is not finite,
  outside ±90 / ±180, or exactly (0, 0) is left out and counted.

Running sums are kept in stored units and divided once, so no rounding error builds up
over a long workout.

These stay in ``raw`` only, because their meaning is unverified (design §2):

* per-second energy (``newCalories``): the run's sum is 0.3% off the summary;
* ``stride``, ``verticalStrideRatio`` and ``verticalAmplitude``: their averages are
  only about 1% off the summary's;
* the impact-force triple (``newImpactForce``, ``newContactTime``,
  ``newFlightTime``): not checked against anything;
* ``contactRatio``, ``equalIntensityPace`` and ``runningPower``: marked invalid in the
  checked files;
* ``workoutcount`` and the ride's ``cadence``: marked invalid in the checked files
  (the ride had no cadence sensor, D20);
* ``isKilometer``: it matches the summary, but the cumulative distance already gives
  the kilometre marks;
* the raw altitude bits: ``altitude_m`` is built from them;
* the segment fields (``segmentType``, ``pauseType`` and the segment totals): their
  meaning is unverified (spec §16 Q3); a pause already shows as a gap in ``epoch_s``;
* the GPS accuracy (``locationHorizontalAccuracy``): it looks like metres but is
  unverified;
* the GPS ``locationSpeed`` and ``gpsSource``: the speed is 0.87-0.95 of the speed
  the track gives, and its unit is unknown (D19).
"""

from __future__ import annotations

import math

from mifit.domains.base import DomainResult, as_float, as_int, register
from mifit.domains.workouts import SportRow, sport_rows, to_workout
from mifit.errors import ApiError
from mifit.fds.binary import FdsFormatError, parse_header
from mifit.fds.cipher import FdsDecryptError, decrypt_body
from mifit.fds.download import FdsDownloader, FdsUnavailable
from mifit.fds.ids import GPS, RECORD, FileRef, server_key, suffix
from mifit.fds.layouts import LAYOUTS, decode
from mifit.fds.urls import FdsEntry, request_urls
from mifit.models.workout_detail import TrackPoint, WorkoutDetail, WorkoutSample
from mifit.transport import Transport

_MAX_WORKOUTS = 10
"""The most supported workouts one call reads the detail of (D13): each costs up to
three requests."""

_NO_START = "Skipped a workout without a usable start time."
"""The note ``workouts`` gives for a summary without a usable start time."""

_KIND = {RECORD: "record", GPS: "GPS"}
"""How a note names each file."""
_RAW_KEY = {RECORD: "record", GPS: "gps"}
"""Where a workout's ``raw`` entry holds each file."""

_RUN = 22
_RIDE = 23

_DISTANCE_DIVISOR = {22: 10, 23: 1}
"""Stored ``newDistance`` units per metre, by format (the APK's
``getDistanceRecordTimes``). Its keys are the formats this module maps."""

_SPEED_UNITS_PER_MPS = 36
"""``speed`` is stored in 0.1 km/h; 1 m/s is 3.6 km/h, so 36 stored units."""

_POSITION = ("time", "longitude", "latitude")


@register("workout_detail")
def fetch_workout_detail(
    t: Transport,
    start_epoch_s: int,
    end_epoch_s: int,
    *,
    downloader: FdsDownloader | None = None,
    track: bool = True,
) -> DomainResult:
    """Fetch the per-second samples and the GPS track of the workouts that start
    between two Unix-second timestamps, both inclusive, newest first.

    The workouts are the ones ``fetch_workouts`` gives for the same window, each as
    a ``WorkoutDetail``; ``raw`` holds ``{"workout", "record", "gps"}`` for each, at
    the same index. ``downloader`` fetches the files (a new ``FdsDownloader()`` when
    ``None``). ``track=False`` asks for no GPS file. See the module docstring for
    the steps, the request budget, the 10-workout cap and the notes.
    """
    if downloader is None:
        downloader = FdsDownloader()
    read = sport_rows(t, start_epoch_s, end_epoch_s)

    result = DomainResult()
    read_count = 0
    over_cap = 0
    for row in read.rows:
        if row.value is None:
            result.notes.append(row.note)
            continue
        workout = to_workout(row.value)
        if workout is None:
            result.notes.append(_NO_START)
            continue
        # The record first, then the files fill it in: records[i] and raw[i] are the
        # same workout whatever happens to its files.
        detail = WorkoutDetail(workout, None, None)
        raw = {"workout": row.value, "record": None, "gps": None}
        result.records.append(detail)
        result.raw.append(raw)
        n = len(result.records)

        refs, problem = _file_refs(row, n, track)
        if problem is not None:
            result.notes.append(problem)
            continue
        if read_count == _MAX_WORKOUTS:
            over_cap += 1
            continue
        read_count += 1
        _read_files(t, downloader, refs, n, detail, raw, result.notes)

    if over_cap:
        result.notes.append(
            f"Read the detail of the newest {_MAX_WORKOUTS} supported workouts only; "
            f"{over_cap} older one(s) in the range have no samples or track: "
            "ask for a shorter range."
        )
    result.notes.extend(read.end_notes)
    return result


def _file_refs(row: SportRow, n: int, track: bool) -> tuple[list[FileRef], str | None]:
    """Workout ``n``'s files to ask for (the record, then the GPS file when
    ``track``) and ``None``; or ``[]`` and the note saying why they cannot be named.
    The checks run in this order, before any request."""
    value = row.value
    proto = as_int(value.get("proto_type"))
    if proto is None:
        return [], _unnamed(n, "format number")
    if proto not in _DISTANCE_DIVISOR:
        return [], f"Workout {n}: detail is not supported for this sport yet (format {proto})."
    report_time = as_int(value.get("time"))
    if report_time is None or not 0 < report_time < 2**32:
        return [], _unnamed(n, "report time")
    tz15 = as_int(value.get("timezone"))
    if tz15 is None or not -128 <= tz15 <= 127:
        return [], _unnamed(n, "time zone")
    no_source_id = f"Workout {n}: its row has no source id, so its detail files cannot be named."
    if row.sid is None:
        return [], no_source_id
    file_types = (RECORD, GPS) if track else (RECORD,)
    try:
        refs = [FileRef(row.sid, report_time, tz15, proto, ft) for ft in file_types]
    except ValueError:
        # The checks above leave only an id that cannot be encoded (a lone surrogate).
        return [], no_source_id
    return refs, None


def _unnamed(n: int, what: str) -> str:
    return f"Workout {n}: its summary has no usable {what}, so its detail files cannot be named."


def _read_files(
    t: Transport,
    downloader: FdsDownloader,
    refs: list[FileRef],
    n: int,
    detail: WorkoutDetail,
    raw: dict,
    notes: list[str],
) -> None:
    """Ask for the links of workout ``n``'s files, then read each file into its
    ``raw`` entry and onto ``detail``."""
    try:
        entries = request_urls(t, refs[0].sid, refs)
    except ApiError as exc:
        notes.append(f"Workout {n}: could not get the download links ({_error_text(exc, refs)}).")
        return
    for ref in refs:
        kind = _KIND[ref.file_type]
        plain = _plain_file(downloader, entries.get(server_key(ref)), n, kind, notes)
        if plain is None:
            continue
        file_raw, decoded = _decode_file(plain, ref, n, kind, notes)
        raw[_RAW_KEY[ref.file_type]] = file_raw
        if not decoded:
            continue
        if ref.file_type == RECORD:
            detail.samples = to_samples(file_raw, ref.proto)
            continue
        points, dropped = to_track(file_raw)
        detail.track = points
        if points is None:
            notes.append(
                f"Workout {n}: the GPS file marks its times or positions invalid, "
                "so there is no track."
            )
        elif dropped:
            notes.append(
                f"Workout {n}: left out {dropped} track point(s) without a usable position."
            )


def _error_text(exc: ApiError, refs: list[FileRef]) -> str:
    """``exc``'s message for a note, or only its type when the message (which can be
    the service's own text) holds the source id or a file name."""
    text = str(exc)
    secrets = [refs[0].sid, *(suffix(ref) for ref in refs)]
    if any(secret in text for secret in secrets):
        return type(exc).__name__
    return text


def _plain_file(
    downloader: FdsDownloader, entry: FdsEntry | None, n: int, kind: str, notes: list[str]
) -> bytes | None:
    """The plain file behind ``entry``, or ``None`` and a note. A file without a key
    (the service does not have it) or under a link that is not a GET is not
    downloaded."""
    if entry is None or entry.key_b64 is None:
        notes.append(f"Workout {n}: the service has no {kind} file for it.")
        return None
    if entry.method is not None and entry.method.upper() != "GET":
        notes.append(
            f"Workout {n}: the {kind} file's link is not a download (GET) link, "
            "so it was not fetched."
        )
        return None
    try:
        body = downloader.fetch(entry.url)
    except FdsUnavailable as exc:
        notes.append(f"Workout {n}: the {kind} file could not be downloaded: {exc}.")
        return None
    try:
        return decrypt_body(body, entry.key_b64)
    except FdsDecryptError:
        notes.append(f"Workout {n}: the {kind} file could not be decrypted.")
        return None


def _decode_file(
    plain: bytes, ref: FileRef, n: int, kind: str, notes: list[str]
) -> tuple[dict | None, bool]:
    """The A layer of one plain file, and whether it was decoded in full.

    ``(None, False)`` when the file was not read and ``({"header": ...}, False)``
    when only its header was, each with a note; ``(decoded, True)`` otherwise, with
    a note when bytes were left over.
    """
    try:
        header = parse_header(plain, ref.file_type)
    except FdsFormatError as exc:
        notes.append(_damaged(n, kind, exc))
        return None, False
    summary = (ref.timestamp, ref.tz15, ref.proto)
    if (header.timestamp, header.timezone, header.proto_type) != summary:
        notes.append(
            f"Workout {n}: the {kind} file's header does not match the workout summary, "
            "so it was not read."
        )
        return None, False
    if header.mode != 0:
        notes.append(
            f"Workout {n}: the {kind} file uses storage mode {header.mode} "
            "(compressed or encrypted), which is not supported yet."
        )
        return {"header": header.as_raw()}, False
    layout = LAYOUTS.get((ref.proto, ref.file_type, header.version))
    if layout is None or header.validity is None:
        notes.append(
            f"Workout {n}: format version {header.version} of the {kind} file "
            "is not supported yet."
        )
        return {"header": header.as_raw()}, False
    try:
        decoded = decode(plain, header, layout)
    except FdsFormatError as exc:
        notes.append(_damaged(n, kind, exc))
        return None, False
    if decoded.trailing_bytes > 0:
        notes.append(
            f"Workout {n}: ignored {decoded.trailing_bytes} trailing byte(s) at the end "
            f"of the {kind} file."
        )
    return decoded.raw, True


def _damaged(n: int, kind: str, exc: FdsFormatError) -> str:
    return f"Workout {n}: the {kind} file is damaged ({exc}), so it was not read."


def to_samples(record_raw: dict, proto: int) -> list[WorkoutSample]:
    """One ``WorkoutSample`` per point of a decoded record file of format ``proto``,
    in file order (see the module docstring for the rules).

    ``proto`` must be one of the formats in ``_DISTANCE_DIVISOR`` (``KeyError``
    otherwise). A file without points gives ``[]``.
    """
    divisor = _DISTANCE_DIVISOR[proto]
    run = proto == _RUN
    ride = proto == _RIDE
    samples = []
    distance = 0  # stored units since the start of the workout
    steps = 0
    for segment in record_raw["segments"]:
        start = segment["resumeTimestamp"]
        initial_altitude = as_float(segment.get("initialAltitude"))
        climb = 0  # tenths of a metre since the segment start
        for i, point in enumerate(segment["dataPoints"]):
            distance_m = None
            if "newDistance" in point:
                distance += point["newDistance"]
                distance_m = distance / divisor

            steps_now = None
            if run and "newSteps" in point:
                steps += point["newSteps"]
                steps_now = steps

            altitude_m = None
            rising = point.get("isAltitudeRising")
            change = point.get("altitudeChange")
            if initial_altitude is not None and rising is not None and change is not None:
                climb += change if rising else -change
                altitude_m = initial_altitude + climb / 10

            speed_mps = None
            if ride and "speed" in point:
                speed_mps = point["speed"] / _SPEED_UNITS_PER_MPS

            samples.append(
                WorkoutSample(
                    epoch_s=start + i,
                    heart_rate_bpm=point.get("heartRate") or None,
                    distance_m=distance_m,
                    steps=steps_now,
                    cadence_spm=(point.get("cadence") or None) if run else None,
                    pace_s_per_km=(point.get("pace") or None) if run else None,
                    speed_mps=speed_mps,
                    altitude_m=altitude_m,
                )
            )
    return samples


def to_track(gps_raw: dict) -> tuple[list[TrackPoint] | None, int]:
    """The track of a decoded GPS file and the number of points it left out.

    The track is ``None`` (and the count ``0``) when the file marks its times or
    positions invalid; ``[]`` when the file holds no points.
    """
    track = []
    dropped = 0
    for point in gps_raw["gpsPoints"]:
        if not all(name in point for name in _POSITION):
            return None, 0
        latitude = point["latitude"]
        longitude = point["longitude"]
        if _usable(latitude, longitude):
            track.append(TrackPoint(point["time"], latitude, longitude))
        else:
            dropped += 1
    return track, dropped


def _usable(latitude: float, longitude: float) -> bool:
    """Whether a fix is a usable position: finite, within ±90 / ±180, and not exactly
    (0, 0)."""
    return (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and abs(latitude) <= 90
        and abs(longitude) <= 180
        and (latitude, longitude) != (0.0, 0.0)
    )
