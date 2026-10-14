"""Decrypt an FDS download body (spec §4.5, D22).

The body is a base64url ciphertext, usually inside a JSON string literal. The key
(the service's ``obj_key``) is base64url too. The cipher is AES-CBC with a fixed IV
and PKCS#7 padding.

Decoding is strict: only the base64url alphabet, no inner whitespace, at most two
trailing ``=``. The app's Android decoder skips any other character; this one does
not, so a body that is not a ciphertext fails instead of being half-read.

Every failure raises ``FdsDecryptError`` with one fixed message, from outside the
``except`` block that caught it: the message quotes neither body nor key, and the
error carries no ``__cause__`` or ``__context__`` that could.
"""

from __future__ import annotations

import base64
import json
import re

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

IV = b"1234567887654321"

_KEY_LENGTHS = (16, 24, 32)
_BASE64URL = re.compile(r"[A-Za-z0-9_-]*={0,2}")
_FAILED = "The FDS body could not be decrypted."


class FdsDecryptError(ValueError):
    """A body or key that does not decrypt. Its message never quotes either."""


def _b64url_decode(text: str) -> bytes:
    """Strict base64url: the alphabet, at most two trailing ``=``, padding optional."""
    if _BASE64URL.fullmatch(text) is None:
        raise ValueError
    core = text.rstrip("=")
    if len(core) % 4 == 1:
        raise ValueError
    return base64.urlsafe_b64decode(core + "=" * (-len(core) % 4))


def _decrypt(body: bytes, key_b64: str) -> bytes:
    """The plain file, or ``ValueError`` (whatever its message) for any failure."""
    text = body.decode("ascii").strip()
    if text.startswith('"'):
        text = json.loads(text)  # from a leading quote it is a str, or it raises
    ciphertext = _b64url_decode(text)
    if not ciphertext or len(ciphertext) % 16:
        raise ValueError
    key = _b64url_decode(key_b64)
    if len(key) not in _KEY_LENGTHS:
        raise ValueError
    decryptor = Cipher(algorithms.AES(key), modes.CBC(IV)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def decrypt_body(body: bytes, key_b64: str) -> bytes:
    """The plain file inside a download ``body``, decrypted with the base64url ``key_b64``.

    Raises ``FdsDecryptError`` for a body or key that is malformed, a ciphertext of
    the wrong length, or padding that does not check out (a wrong key mostly shows
    as the latter).
    """
    try:
        return _decrypt(body, key_b64)
    except ValueError:
        pass
    raise FdsDecryptError(_FAILED)
