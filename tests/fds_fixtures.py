"""Synthetic FDS data for the tests (imported as ``from fds_fixtures import ...``;
pytest puts ``tests/`` on ``sys.path``). Everything here is made up.

This module encrypts with ``cryptography`` directly and never imports
``mifit.fds.cipher``, so the cipher tests check the decryptor against an
independent encryptor.
"""

import base64

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
