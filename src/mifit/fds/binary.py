"""The cloud file header and bounds-checked little-endian reads (spec §4.6).

A plain FDS file starts with an 8-byte header written by the service:

====== ========= ==========================================
offset type      meaning
====== ========= ==========================================
0      uint32 LE time stamp (the summary's ``time``)
4      int8      time zone in quarter hours
5      uint8     file format version
6      uint8     sport format (the summary's ``proto_type``)
7      uint8     storage mode (0 = neither encrypted nor compressed)
====== ========= ==========================================

Validity flags follow it. Their length depends on the sport format, the file type
and the version (the APK's ``FitnessDataValidity``). A combination the table does not
know has no flags here, and the file cannot be decoded.

The bytes are untrusted: every read checks the remaining length first and raises
``FdsFormatError``, whose message is a fixed fragment that never holds file bytes.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .ids import GPS, RECORD

HEADER_LEN = 8

_HEADER = struct.Struct("<IbBBB")  # time stamp, time zone, version, sport format, mode
_F32 = struct.Struct("<f")

# Validity flag length by version (APK ``FitnessDataValidity``).
_RUN_VALIDITY = {1: 2, 2: 3, 3: 3, 4: 3, 5: 5, 6: 6, 7: 6, 8: 7, 9: 7}  # format 22
_RIDE_VALIDITY = {1: 2, 2: 2, 3: 3, 4: 3, 5: 3, 6: 3, 7: 4}  # format 23
_GPS_VALIDITY = {1: 1, 2: 1, 3: 1, 4: 1, 5: 2}  # any format
_RECORD_VALIDITY = {22: _RUN_VALIDITY, 23: _RIDE_VALIDITY}


class FdsFormatError(ValueError):
    """A plain file that does not fit its layout. The message is a fixed fragment
    (it reads well inside a sentence) and never holds file bytes."""


@dataclass(frozen=True)
class FileHeader:
    timestamp: int
    timezone: int  # quarter hours
    version: int
    proto_type: int
    mode: int  # encryption/compression mode
    validity: bytes | None  # None when the table has no length for this file

    def as_raw(self) -> dict:
        """The A-layer header: the schema names, ``validityFlags`` as hex (left out
        when there are no flags)."""
        raw = {
            "timestamp": self.timestamp,
            "timezone": self.timezone,
            "version": self.version,
            "protoType": self.proto_type,
            "encryptionCompressionMode": self.mode,
        }
        if self.validity is not None:
            raw["validityFlags"] = self.validity.hex()
        return raw


def validity_len(proto: int, file_type: int, version: int) -> int | None:
    """The length of the validity flags, or ``None`` when the table has no entry."""
    if file_type == GPS:
        return _GPS_VALIDITY.get(version)
    if file_type == RECORD:
        return _RECORD_VALIDITY.get(proto, {}).get(version)
    return None


def parse_header(plain: bytes, file_type: int) -> FileHeader:
    """The header and validity flags at the start of ``plain``."""
    if len(plain) < HEADER_LEN:
        raise FdsFormatError("the file ends inside its header")
    timestamp, timezone, version, proto, mode = _HEADER.unpack_from(plain)
    length = validity_len(proto, file_type, version)
    validity = None
    if length is not None:
        if len(plain) - HEADER_LEN < length:
            raise FdsFormatError("the file ends inside its validity flags")
        validity = bytes(plain[HEADER_LEN : HEADER_LEN + length])
    return FileHeader(timestamp, timezone, version, proto, mode, validity)


class Reader:
    """Little-endian reads over ``data`` from ``offset`` on. A read that would run past
    the end raises ``FdsFormatError`` and moves nothing."""

    def __init__(self, data: bytes, offset: int = 0) -> None:
        if not 0 <= offset <= len(data):
            raise FdsFormatError("a read starts outside the file")
        self._data = data
        self._offset = offset

    @property
    def offset(self) -> int:
        return self._offset

    @property
    def remaining(self) -> int:
        return len(self._data) - self._offset

    def _take(self, size: int) -> int:
        """The start of the next ``size`` bytes, after checking they are there."""
        if not 0 <= size <= self.remaining:
            raise FdsFormatError("a read runs past the end of the file")
        start = self._offset
        self._offset += size
        return start

    def uint(self, size: int) -> int:
        """An unsigned little-endian integer of ``size`` bytes."""
        start = self._take(size)
        return int.from_bytes(self._data[start : start + size], "little")

    def f32(self) -> float:
        """A little-endian float32, as a Python float."""
        return _F32.unpack_from(self._data, self._take(4))[0]

    def skip(self, size: int) -> None:
        self._take(size)
