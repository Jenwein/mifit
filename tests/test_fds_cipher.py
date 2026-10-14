"""FDS body decryption (spec §4.5, D22): a JSON string or bare base64url, strictly
decoded, then AES-CBC with the fixed IV and PKCS#7 padding."""

import re
from pathlib import Path

import pytest
from fds_fixtures import (
    SYNTH_KEY_B64,
    b64url,
    encrypt_body,
    encrypt_plain,
)
from support import exception_chain

from mifit.fds.cipher import FdsDecryptError, decrypt_body

SRC = Path(__file__).resolve().parents[1] / "src" / "mifit"


@pytest.mark.parametrize("quoted", [True, False], ids=["quoted", "bare"])
@pytest.mark.parametrize("length", [0, 15, 16, 17, 1000])
def test_round_trip(length, quoted):
    plain = bytes(i % 251 for i in range(length))

    body = encrypt_body(plain, quoted=quoted)

    assert decrypt_body(body, SYNTH_KEY_B64) == plain


def test_surrounding_whitespace_is_stripped():
    body = b" \r\n" + encrypt_body(b"hello") + b"\n"

    assert decrypt_body(body, SYNTH_KEY_B64) == b"hello"


@pytest.mark.parametrize("key_length", [24, 32])
def test_longer_aes_keys(key_length):
    key = bytes(range(key_length))

    assert decrypt_body(encrypt_body(b"payload", key), b64url(key)) == b"payload"


def test_key_with_padding_is_accepted():
    body = encrypt_body(b"payload")

    assert decrypt_body(body, SYNTH_KEY_B64 + "==") == b"payload"


def test_iv_is_the_fixed_one():
    plain = bytes(range(48))
    body = b'"' + b64url(encrypt_plain(plain, iv=b"0" * 16)).encode("ascii") + b'"'

    decrypted = decrypt_body(body, SYNTH_KEY_B64)

    assert decrypted[16:] == plain[16:]
    assert decrypted[:16] != plain[:16]


def _quoted(raw: bytes) -> bytes:
    return b'"' + b64url(raw).encode("ascii") + b'"'


_GOOD_BODY = encrypt_body(b"x" * 16)
_GOOD_TEXT = _GOOD_BODY.decode("ascii").strip('"')

REJECTIONS = {
    "key-15-bytes": (_GOOD_BODY, b64url(bytes(15))),
    "key-bad-char": (_GOOD_BODY, "+" + SYNTH_KEY_B64[1:]),
    "key-inner-space": (_GOOD_BODY, SYNTH_KEY_B64[:8] + " " + SYNTH_KEY_B64[8:]),
    "key-too-much-padding": (_GOOD_BODY, SYNTH_KEY_B64 + "==="),
    "key-17-bytes": (_GOOD_BODY, b64url(bytes(17))),
    "key-impossible-length": (_GOOD_BODY, SYNTH_KEY_B64 + "AAA"),
    "key-empty": (_GOOD_BODY, ""),
    "body-json-number": (b"123", SYNTH_KEY_B64),
    "body-json-object": (b'{"a": 1}', SYNTH_KEY_B64),
    "body-json-list": (b'["' + _GOOD_TEXT.encode("ascii") + b'"]', SYNTH_KEY_B64),
    "body-not-ascii": (b"\xff", SYNTH_KEY_B64),
    "body-bad-char": (b'"' + _GOOD_TEXT[:8].encode("ascii") + b'+AAA"', SYNTH_KEY_B64),
    "body-inner-space": (
        b'"' + _GOOD_TEXT[:8].encode("ascii") + b" " + _GOOD_TEXT[8:].encode("ascii") + b'"',
        SYNTH_KEY_B64,
    ),
    "body-trailing-garbage": (_GOOD_BODY + b"x", SYNTH_KEY_B64),
    "ciphertext-not-block-multiple": (_quoted(bytes(20)), SYNTH_KEY_B64),
    "empty-ciphertext": (b'""', SYNTH_KEY_B64),
    "empty-body": (b"", SYNTH_KEY_B64),
    "bad-padding": (_quoted(encrypt_plain(b"\x00" * 16, pad=False)), SYNTH_KEY_B64),
}


@pytest.mark.parametrize(("body", "key"), REJECTIONS.values(), ids=REJECTIONS.keys())
def test_rejections(body, key):
    with pytest.raises(FdsDecryptError):
        decrypt_body(body, key)


# The next two tests build input that the app's lax Android decoder reads right, so
# a decoder that is less strict than ours would decrypt it. Each has a control that
# decrypts, so the refusal is down to the characters alone.


def test_the_standard_alphabet_is_refused():
    key = bytes([0xFB, 0xFF] * 8)  # base64url "-__7__v_...": has both '-' and '_'
    key_b64 = b64url(key)
    plain = bytes(range(10))
    body = encrypt_body(plain, key)
    assert "-" in key_b64 + body.decode() and "_" in key_b64 + body.decode()
    assert decrypt_body(body, key_b64) == plain

    def standard(text):
        return text.replace("-", "+").replace("_", "/")

    with pytest.raises(FdsDecryptError):
        decrypt_body(standard(body.decode()).encode(), key_b64)
    with pytest.raises(FdsDecryptError):
        decrypt_body(body, standard(key_b64))


def test_whitespace_between_characters_is_refused():
    # 64 body characters and 32 key characters need no padding, so four inserted
    # spaces keep the length a multiple of four.
    key = bytes(range(24))
    plain = bytes(range(32))
    text = b64url(encrypt_plain(plain, key))
    key_b64 = b64url(key)
    assert (len(text), len(key_b64)) == (64, 32)
    assert decrypt_body(b'"' + text.encode() + b'"', key_b64) == plain

    spaced = text[:10] + "    " + text[10:]
    with pytest.raises(FdsDecryptError):
        decrypt_body(b'"' + spaced.encode() + b'"', key_b64)
    with pytest.raises(FdsDecryptError):
        decrypt_body(b'"' + text.encode() + b'"', key_b64[:5] + "    " + key_b64[5:])


def test_errors_quote_nothing_and_have_no_chain():
    with pytest.raises(FdsDecryptError) as caught:
        decrypt_body(b'"SYNTH-BODY-MARK', "SYNTH-KEY-MARK+")

    exc = caught.value
    assert "SYNTH-BODY-MARK" not in str(exc) + repr(exc)
    assert "SYNTH-KEY-MARK" not in str(exc) + repr(exc)
    assert exception_chain(exc) == [exc]


def test_a_failure_inside_the_cipher_has_no_chain_either():
    with pytest.raises(FdsDecryptError) as caught:
        decrypt_body(_quoted(encrypt_plain(b"\x00" * 16, pad=False)), SYNTH_KEY_B64)

    assert exception_chain(caught.value) == [caught.value]


def test_the_error_is_a_value_error():
    assert issubclass(FdsDecryptError, ValueError)


def test_cryptography_is_imported_only_by_the_cipher():
    pattern = re.compile(r"^\s*(from|import)\s+cryptography", re.M)

    importers = sorted(
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    )

    assert importers == ["fds/cipher.py"]
