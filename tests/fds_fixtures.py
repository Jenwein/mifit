"""Synthetic FDS data for the tests (imported as ``from fds_fixtures import ...``;
pytest puts ``tests/`` on ``sys.path``). Everything here is made up.

This module encrypts with ``cryptography`` directly and never imports
``mifit.fds.cipher``, so the cipher tests check the decryptor against an
independent encryptor.

Likewise, the file builders pack every byte by hand from the spec's tables
(§4.6 header, §4.7 record segments and points, §4.8 GPS points) and never import
``mifit.fds.layouts``: a wrong entry in the decoder's tables then fails a test
instead of round-tripping silently.
"""

import base64
import struct

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SYNTH_SID = "1000000006"
SYNTH_KEY = bytes(range(16))


def b64url(raw: bytes) -> str:
    """Base64url without padding."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


SYNTH_KEY_B64 = b64url(SYNTH_KEY)


def encrypt_plain(
    plain: bytes,
    key: bytes = SYNTH_KEY,
    *,
    iv: bytes = b"1234567887654321",
    pad: bool = True,
) -> bytes:
    """AES-CBC of ``plain`` under ``key`` and ``iv``: the raw ciphertext.

    With ``pad=False`` the plaintext is encrypted as it is (it must be a multiple of
    16 bytes), so a test can build a ciphertext whose padding is invalid.
    """
    if pad:
        padder = padding.PKCS7(128).padder()
        plain = padder.update(plain) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(plain) + encryptor.finalize()


def encrypt_body(plain: bytes, key: bytes = SYNTH_KEY, *, quoted: bool = True) -> bytes:
    """A download body: the ciphertext as base64url, inside double quotes (a JSON
    string literal) or, with ``quoted=False``, as bare text."""
    text = b64url(encrypt_plain(plain, key)).encode("ascii")
    return b'"' + text + b'"' if quoted else text


# --- Plain files (spec §4.6–§4.8) ------------------------------------------------

# Validity flags. A record file gives each selected field one nibble, high nibble
# first: 8 = exists, then 4 / 2 / 1 = the high / middle / low sub-value is valid.
# A GPS file gives each field one bit, high bit first.
RUN_FLAGS_ALL = bytes.fromhex("ffffffffffffff")
# Same shape as the live run file: impact force and flight time, contact ratio,
# equal-intensity pace and running power are invalid (every field still exists).
RUN_FLAGS_LIVE = bytes.fromhex("fffffabffbbfff")
RIDE_FLAGS_ALL = bytes.fromhex("ffffff")
# Same shape as the live ride file: ``workoutcount`` and cadence are invalid.
RIDE_FLAGS_LIVE = bytes.fromhex("dffffb")
# Cadence does not exist (its nibble lacks bit 8), so it takes no bytes.
RIDE_FLAGS_NO_CADENCE = bytes.fromhex("dffff3")

_RUN_POINT_DEFAULTS = {
    "calories": 1,
    "steps": 3,
    "heart_rate": 120,
    "kilometer": 0,
    "rising": 1,
    "altitude_change": 5,
    "distance_dm": 25,
    "stride": 80,
    "impact": 10,
    "contact": 250,
    "flight": 100,
    "contact_ratio": 40,
    "cadence": 170,
    "pace": 360,
    "equal_pace": 365,
    "power": 200,
    "vertical_ratio": 85,
    "vertical_amplitude": 80,
}

_RIDE_POINT_DEFAULTS = {
    "calories": 2,
    "workoutcount": 0,
    "heart_rate": 110,
    "kilometer": 0,
    "rising": 0,
    "altitude_change": 3,
    "distance_m": 5,
    "speed": 250,
    "cadence": 0,
}


def f32(x: float) -> float:
    """``x`` as a float32 reads back."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def _merge(defaults: dict, fields: dict) -> dict:
    unknown = set(fields) - set(defaults)
    if unknown:
        raise TypeError(f"unknown point field(s): {sorted(unknown)}")
    return {**defaults, **fields}


def _bits(value: int, width: int) -> int:
    """``value`` checked to fit ``width`` bits, so a fixture never spills into a neighbour."""
    if not 0 <= value < 1 << width:
        raise ValueError(f"{value} does not fit in {width} bits")
    return value


def _altitude_byte(v: dict) -> int:
    """``isKilometer`` [7], ``isAltitudeRising`` [6], ``altitudeChange`` [5..0]."""
    kilometer, rising = _bits(v["kilometer"], 1), _bits(v["rising"], 1)
    return kilometer << 7 | rising << 6 | _bits(v["altitude_change"], 6)


def header_bytes(ts: int, tz15: int, version: int, proto: int, mode: int = 0) -> bytes:
    """The 8-byte cloud header: time (u32), time zone (i8), version, format, mode (u8)."""
    return struct.pack("<IbBBB", ts, tz15, version, proto, mode)


def run_point(**fields) -> bytes:
    """One second of a format-22 v8 record with all 13 fields present: 21 bytes."""
    v = _merge(_RUN_POINT_DEFAULTS, fields)
    return struct.pack(
        "<BBBBBIBBHHHHH",
        _bits(v["calories"], 4) << 4 | _bits(v["steps"], 4),
        v["heart_rate"],
        _altitude_byte(v),
        v["distance_dm"],
        v["stride"],
        _bits(v["impact"], 6) << 26 | _bits(v["contact"], 13) << 13 | _bits(v["flight"], 13),
        v["contact_ratio"],
        v["cadence"],
        v["pace"],
        v["equal_pace"],
        v["power"],
        v["vertical_ratio"],
        v["vertical_amplitude"],
    )


def ride_point(*, with_cadence: bool = True, **fields) -> bytes:
    """One second of a format-23 v5 record: 7 bytes, or 6 without the cadence byte."""
    v = _merge(_RIDE_POINT_DEFAULTS, fields)
    packed = struct.pack(
        "<BBBBH",
        _bits(v["calories"], 4) << 4 | _bits(v["workoutcount"], 4),
        v["heart_rate"],
        _altitude_byte(v),
        v["distance_m"],
        v["speed"],
    )
    return packed + struct.pack("<B", v["cadence"]) if with_cadence else packed


def _segment_info(pause_type: int, segment_type: int) -> int:
    return _bits(pause_type, 1) << 7 | _bits(segment_type, 7)


def run_segment(
    points,
    *,
    resume,
    initial_altitude=50.0,
    pause_type=1,
    segment_type=4,
    total_distance=0,
    total_duration=None,
    total_steps=0,
    segment_pace=0,
    record_count=None,
) -> bytes:
    """A format-22 segment: the 27-byte header, then its points."""
    count = len(points) if record_count is None else record_count
    duration = count if total_duration is None else total_duration
    header = struct.pack(
        "<fIIBIIIH",
        initial_altitude,
        count,
        resume,
        _segment_info(pause_type, segment_type),
        total_distance,
        duration,
        total_steps,
        segment_pace,
    )
    return header + b"".join(points)


def ride_segment(
    points,
    *,
    resume,
    initial_altitude=50.0,
    pause_type=1,
    segment_type=0,
    total_distance=0,
    total_time=None,
    total_speed=0,
    record_count=None,
) -> bytes:
    """A format-23 segment: the 23-byte header, then its points."""
    count = len(points) if record_count is None else record_count
    duration = count if total_time is None else total_time
    header = struct.pack(
        "<fIIBIIH",
        initial_altitude,
        count,
        resume,
        _segment_info(pause_type, segment_type),
        total_distance,
        duration,
        total_speed,
    )
    return header + b"".join(points)


def gps_point(
    time: int, lon: float, lat: float, *, accuracy: float = 4.5, speed: int = 30, source: int = 0
) -> bytes:
    """One GPS v2 fix, 18 bytes: time, longitude (first), latitude, accuracy, then
    speed (bits 15..4) and source (bits 3..0) in one u16."""
    return struct.pack("<IfffH", time, lon, lat, accuracy, _bits(speed, 12) << 4 | _bits(source, 4))


def record_file(ts, tz15, proto, version, flags, *segments) -> bytes:
    """A plain record file: header, validity flags, segments."""
    return header_bytes(ts, tz15, version, proto) + flags + b"".join(segments)


def gps_file(ts, tz15, proto, *points, flags=b"\xff", version=2) -> bytes:
    """A plain GPS file: header, validity flags, points."""
    return header_bytes(ts, tz15, version, proto) + flags + b"".join(points)
