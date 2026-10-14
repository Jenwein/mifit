"""FDS file names: which file of a workout the service is asked for (spec §4.2).

The name of a file is a ``suffix`` built from the workout's summary: a six-byte key
(time, time zone, sport format and file type) and a hash of the source id. The
service answers with a mapping keyed by ``server_key``.

A ``FileRef`` holds the source id (``sid``) only to hash it into the name; it is
never output (not in ``repr``, not in an error message).
"""

from __future__ import annotations

import base64
import hashlib
import struct
from dataclasses import dataclass, field

RECORD = 0
GPS = 2

_INVALID = "Invalid FDS file reference."


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class FileRef:
    sid: str = field(repr=False)  # the outer row's sid; hashed into the name, never output
    timestamp: int  # value.time in seconds (NOT start_time)
    tz15: int  # value.timezone, quarter hours
    proto: int  # value.proto_type
    file_type: int  # RECORD or GPS

    def __post_init__(self) -> None:
        valid = (
            isinstance(self.sid, str)
            and bool(self.sid)
            and _is_int(self.timestamp)
            and 0 <= self.timestamp < 2**32  # the app's millisecond branch is out of scope
            and _is_int(self.tz15)
            and -128 <= self.tz15 <= 127
            and _is_int(self.proto)
            and 0 <= self.proto <= 31
            and _is_int(self.file_type)
            and self.file_type in (RECORD, GPS)
        )
        if valid:
            try:
                self.sid.encode("utf-8")
            except UnicodeEncodeError:
                valid = False  # a lone surrogate; the error text would quote the sid
        if not valid:
            raise ValueError(_INVALID)


def _b64url_nopad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def key_bytes(ref: FileRef) -> bytes:
    """Time (uint32 LE), time zone (int8) and one byte of sport format and file type."""
    flags = ((1 << 7) + (ref.proto << 2) + ref.file_type) & 0xFF
    return struct.pack("<Ib", ref.timestamp, ref.tz15) + bytes([flags])


def suffix(ref: FileRef) -> str:
    """``base64url(key_bytes)`` and ``base64url(sha1(sid))``, both unpadded, joined by ``_``."""
    digest = hashlib.sha1(ref.sid.encode("utf-8"), usedforsecurity=False).digest()
    return f"{_b64url_nopad(key_bytes(ref))}_{_b64url_nopad(digest)}"


def server_key(ref: FileRef) -> str:
    """The key under which the service's answer holds this file."""
    return f"{suffix(ref)}_{ref.timestamp}"
