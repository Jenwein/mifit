"""Signing and encryption primitives for the Mi Fitness cloud API.

Pure standard library. The algorithm is fixed (verified against an independent
Java SunJCE ARCFOUR oracle and a separate reference implementation); do not alter
it without re-verifying against those vectors:

* ``signed_nonce``  = b64( SHA256( b64decode(ssecurity) + b64decode(nonce) ) )
* RC4 uses the signed nonce (decoded, 32 bytes) as key, and discards the first
  1024 keystream bytes before use; cipher state is continuous within one cipher.
* signatures are b64( SHA1( "&".join([METHOD, path, *sorted "k=v", signed_nonce]) ) ).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import time
from urllib.parse import urlparse

_RC4_DISCARD = 1024
_MINUTE_MS = 60_000


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value)


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


class _Rc4:
    """RC4 keystream with the Mi Fitness 1024-byte discard applied at construction."""

    def __init__(self, key: bytes) -> None:
        if not key:
            raise ValueError("RC4 key must not be empty.")
        state = list(range(256))
        j = 0
        for i in range(256):
            j = (j + state[i] + key[i % len(key)]) & 0xFF
            state[i], state[j] = state[j], state[i]
        self._state = state
        self._i = 0
        self._j = 0
        self.apply(bytes(_RC4_DISCARD))

    def apply(self, data: bytes) -> bytes:
        state = self._state
        i, j = self._i, self._j
        out = bytearray(len(data))
        for index, value in enumerate(data):
            i = (i + 1) & 0xFF
            j = (j + state[i]) & 0xFF
            state[i], state[j] = state[j], state[i]
            out[index] = value ^ state[(state[i] + state[j]) & 0xFF]
        self._i, self._j = i, j
        return bytes(out)


def rc4_crypt(key: bytes, data: bytes) -> bytes:
    """RC4-encrypt (or decrypt) ``data`` with a fresh cipher, discarding 1024 bytes first."""
    return _Rc4(key).apply(data)


def generate_nonce(time_diff_ms: int = 0) -> str:
    """Return a 12-byte nonce: 8 random bytes + big-endian int32 minutes-since-epoch."""
    now_ms = int(time.time() * 1000) + time_diff_ms
    payload = os.urandom(8) + struct.pack(">i", now_ms // _MINUTE_MS)
    return _b64encode(payload)


def signed_nonce(ssecurity: str, nonce: str) -> str:
    """b64( SHA256( b64decode(ssecurity) + b64decode(nonce) ) )."""
    digest = hashlib.sha256(_b64decode(ssecurity) + _b64decode(nonce)).digest()
    return _b64encode(digest)


def build_signature(
    method: str, path: str, params: dict[str, str], signed_nonce_value: str
) -> str:
    """b64( SHA1( METHOD & url-path & sorted k=v ... & signed_nonce ) )."""
    parts = [method.upper(), urlparse(path).path]
    parts.extend(f"{key}={params[key]}" for key in sorted(params))
    parts.append(signed_nonce_value)
    return _b64encode(hashlib.sha1("&".join(parts).encode("utf-8")).digest())


def encrypt_query_params(
    *,
    method: str,
    path: str,
    params: dict[str, str],
    nonce: str,
    ssecurity: str,
    signature_path: str | None = None,
) -> dict[str, str]:
    """Sign and RC4-encrypt request parameters.

    Empty keys/values are dropped. ``rc4_hash__`` (a signature over the plaintext
    params) is added, every value (including it) is encrypted in sorted-key order
    with one continuous cipher, then ``signature`` (over the encrypted params) and
    ``_nonce`` are appended.
    """
    signed_nonce_value = signed_nonce(ssecurity, nonce)
    sign_path = signature_path or path

    plain = {key: value for key, value in params.items() if key and value}
    plain["rc4_hash__"] = build_signature(method, sign_path, plain, signed_nonce_value)

    cipher = _Rc4(_b64decode(signed_nonce_value))
    encrypted = {
        key: _b64encode(cipher.apply(value.encode("utf-8")))
        for key, value in sorted(plain.items())
    }
    encrypted["signature"] = build_signature(method, sign_path, encrypted, signed_nonce_value)
    encrypted["_nonce"] = nonce
    return encrypted


def decrypt_response_payload(body: str, nonce: str, ssecurity: str) -> dict:
    """Decrypt a response body (b64 -> RC4 -> UTF-8 -> JSON object)."""
    key = _b64decode(signed_nonce(ssecurity, nonce))
    payload = json.loads(rc4_crypt(key, _b64decode(body)).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Mi Fitness response payload was not a JSON object.")
    return payload
