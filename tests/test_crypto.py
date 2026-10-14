"""Tests for the Mi Fitness signing / encryption primitives (``mifit.crypto``).

Golden vectors (all synthetic -- no real credentials or health data):

* JCE vectors: produced by Java 17 SunJCE ARCFOUR in the sibling research repo
  ``mi-health-lab`` (``evidence/protocol-jce-vectors-08.tsv``) and cross-checked
  by ``evidence/tools/verify_protocol_08.py``. Inputs there are
  ``ssecurity = b64(bytes(range(16)))`` and ``nonce = b64(bytes(range(16, 28)))``.
  This is an oracle independent of any Python implementation.
* Reference vectors: produced by running the independent in-repo reference
  ``reference/mi-fitness-sync/src_mi_fitness_sync_activity_crypto.py`` on the fixed
  synthetic inputs ``SS_REF`` / ``NONCE_REF`` below.
"""

import base64
import hashlib
import json
import struct

import pytest

from mifit import crypto
from mifit.crypto import (
    build_signature,
    decrypt_response_payload,
    encrypt_query_params,
    generate_nonce,
    rc4_crypt,
    signed_nonce,
)


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


# --- JCE-sourced synthetic vectors -------------------------------------------------
SS_JCE = b64(bytes(range(16)))
NONCE_JCE = b64(bytes(range(16, 28)))
SIGNED_NONCE_JCE = "3Cf46O4tCKK8y7Lb1sjgf/uhlBAfw0WMNN7VX3LAlxo="
JCE_PLAINTEXTS = [
    "{}",
    '{"key":"heart_rate","start_time":1,"end_time":2}',
    '{"note":"测试 UTF-8 & + /"}',
]
# (path, plaintext index, encrypted data, encrypted rc4_hash__, signature)
JCE_VECTORS = [
    (
        "/app/v1/data/get_fitness_data_by_time",
        0,
        "JIk=",
        "uj7OPdQ5cto0Yd9yLM0Km196V70rWnzxGSWnbQ==",
        "gZqrYajW49IewkFgVNCLrkdHeww=",
    ),
    (
        "/app/v1/data/get_fitness_data_by_time",
        1,
        "JNbpDfhqoS9Q8TRB73kM6RC6LmQwlBRTP/x+foM9crV+W+RaET9RAo0FHKJtKHo0",
        "xbc6kZEzkKLoaJ6cbsaNn1xsSD4gjh+5Kzd5Bg==",
        "kXIza1GUipdZ4CCRF2ZrEWeDREc=",
    ),
    (
        "/app/v1/data/get_fitness_data_by_time",
        2,
        "JNbsB/UtuTcacuC4c4nrqDGLSmUqx0YSZqgOKJc=",
        "f2/afEWdQVslYmWBBTqAPCN+Cvi6IK64Sd3KuA==",
        "kxEnN9zSh55APDfudFRYTWJh5GI=",
    ),
    (
        "/service/gen_download_url",
        0,
        "JIk=",
        "0VHHPcxmD+cmYc8fVcsR60cZcI4PdHvnb36rbQ==",
        "OVTzCDIxlhRmEOkdREeUdwXWH5g=",
    ),
    (
        "/service/gen_download_url",
        1,
        "JNbpDfhqoS9Q8TRB73kM6RC6LmQwlBRTP/x+foM9crV+W+RaET9RAo0FHKJtKHo0",
        "wechtvVBkbC3cJuAcZa5sEgPXyF0o3GaHEt1Bg==",
        "0O3ai9OgPjq/Y92lIKER9p1bOUY=",
    ),
    (
        "/service/gen_download_url",
        2,
        "JNbsB/UtuTcacuC4c4nrqDGLSmUqx0YSZqgOKJc=",
        "InrNAFOiTC0hTCijBhihHCURccH/CPfzbt2nuA==",
        "yhqSFfVnp/ki0JeR8/FWxN7A8+E=",
    ),
]

# --- reference-implementation-sourced synthetic vectors ----------------------------
SS_REF = b64(b"0123456789abcdef0123456789abcdef")
NONCE_REF = b64(b"\x00" * 12)
SIGNED_NONCE_REF = "msM0Gsa1tYZwJUU8Bu0wZZEXz9v+NMbYEDzJlN+WSnc="
PATH_REF = "/app/v1/data/get_fitness_data_by_time"
MSG_REF = b'{"key":"steps","start_time":1,"end_time":2}'
RC4_REF_HEX = (
    "2e157a182fdc7737ecba4924b5cf197bbb4cebd9234b060bc40b61df3cca9320"
    "ec9e8fb3b8d3619a25d339"
)
SIGNATURE_REF = "TzhG31uu/YtfYD99nazi6PaAxnc="
ENCRYPTED_REF = {
    "_nonce": NONCE_REF,
    "data": "LhV6GC/cdzfsukkktc8Ze7tM69kjSwYLxAth3zzKkyDsno+zuNNhmiXTOQ==",
    "rc4_hash__": "raAnUDjtsiF4TdkYZSWhXfXSfNw3D0vW8Lq3DQ==",
    "signature": "ia5lpFuRcw4FmKEUoFqstrHsCAc=",
}
RESPONSE_PAYLOAD_REF = '{"code":0,"result":{"ok":true}}'
RESPONSE_BODY_REF = "LhVyEjKbby+v4g4mo55ANbwasNB1exlAkxoxkGibzA=="


def _rc4_key(ssecurity: str, nonce: str) -> bytes:
    return base64.b64decode(signed_nonce(ssecurity, nonce))


# --- generate_nonce ----------------------------------------------------------------
def test_nonce_decodes_to_12_bytes():
    assert len(base64.b64decode(generate_nonce(0))) == 12


def test_nonce_default_argument_is_zero_offset():
    assert len(base64.b64decode(generate_nonce())) == 12


def test_nonce_embeds_big_endian_minute_counter(monkeypatch):
    monkeypatch.setattr(crypto.time, "time", lambda: 1_700_000_000.0)
    base = 1_700_000_000_000 // 60000
    raw = base64.b64decode(generate_nonce(0))
    assert struct.unpack(">i", raw[8:])[0] == base
    shifted = base64.b64decode(generate_nonce(120_000))
    assert struct.unpack(">i", shifted[8:])[0] == base + 2
    behind = base64.b64decode(generate_nonce(-60_000))
    assert struct.unpack(">i", behind[8:])[0] == base - 1


def test_nonce_random_prefix_differs_between_calls():
    prefixes = {base64.b64decode(generate_nonce(0))[:8] for _ in range(8)}
    assert len(prefixes) > 1


# --- signed_nonce ------------------------------------------------------------------
def test_signed_nonce_is_b64_of_32_bytes():
    value = signed_nonce(b64(b"s" * 16), b64(b"n" * 12))
    assert len(base64.b64decode(value)) == 32


def test_signed_nonce_is_sha256_of_decoded_concatenation():
    expected = b64(hashlib.sha256(bytes(range(16)) + bytes(range(16, 28))).digest())
    assert signed_nonce(SS_JCE, NONCE_JCE) == expected
    assert signed_nonce(SS_JCE, NONCE_JCE) == SIGNED_NONCE_JCE  # JCE-sourced pin
    assert signed_nonce(SS_REF, NONCE_REF) == SIGNED_NONCE_REF


# --- rc4_crypt ---------------------------------------------------------------------
def test_rc4_known_answer():
    # Reference-implementation vector (32-byte key, 1024-byte discard built in).
    key = base64.b64decode(SIGNED_NONCE_REF)
    assert rc4_crypt(key, MSG_REF).hex() == RC4_REF_HEX


def test_rc4_known_answer_jce_independent():
    # Java SunJCE ARCFOUR vector: ciphertext of plaintext #1 under the derived key.
    key = base64.b64decode(SIGNED_NONCE_JCE)
    cipher = base64.b64decode(JCE_VECTORS[1][2])
    assert rc4_crypt(key, JCE_PLAINTEXTS[1].encode("utf-8")) == cipher


def test_rc4_is_symmetric_and_length_preserving():
    key = bytes(range(32))
    data = bytes(range(256)) * 3
    encrypted = rc4_crypt(key, data)
    assert len(encrypted) == len(data)
    assert encrypted != data
    assert rc4_crypt(key, encrypted) == data


def test_rc4_empty_data_returns_empty():
    assert rc4_crypt(bytes(32), b"") == b""


def test_rc4_state_is_continuous_within_a_call_and_fresh_per_call():
    key = bytes(range(32))
    whole = rc4_crypt(key, b"\x00" * 64)
    # Fresh cipher every call: same input -> same output.
    assert rc4_crypt(key, b"\x00" * 64) == whole
    # A single call's stream is a prefix of a longer call's stream.
    assert rc4_crypt(key, b"\x00" * 32) == whole[:32]


def test_rc4_rejects_empty_key():
    with pytest.raises(ValueError):
        rc4_crypt(b"", b"data")


# --- build_signature ---------------------------------------------------------------
def test_signature_is_stable_b64():
    args = ("GET", PATH_REF, {"data": MSG_REF.decode()}, SIGNED_NONCE_REF)
    first = build_signature(*args)
    assert first == build_signature(*args)
    assert len(first) == 28
    assert len(base64.b64decode(first)) == 20  # SHA-1 digest
    assert first == SIGNATURE_REF  # reference-sourced pin


def test_signature_formula_sorted_params_and_uppercased_method():
    expected_input = "&".join(["POST", "/p", "a=1", "b=2", "SN"])
    expected = b64(hashlib.sha1(expected_input.encode("utf-8")).digest())
    assert build_signature("post", "/p", {"b": "2", "a": "1"}, "SN") == expected


def test_signature_uses_only_url_path_component():
    plain = build_signature("GET", "/app/v1/x", {"a": "1"}, "SN")
    full = build_signature("GET", "https://host.example/app/v1/x?q=9#f", {"a": "1"}, "SN")
    assert full == plain


# --- encrypt_query_params ----------------------------------------------------------
def test_encrypt_query_params_matches_reference_vector():
    encrypted = encrypt_query_params(
        method="GET",
        path=PATH_REF,
        params={"data": MSG_REF.decode()},
        nonce=NONCE_REF,
        ssecurity=SS_REF,
    )
    assert encrypted == ENCRYPTED_REF


@pytest.mark.parametrize("path,index,data,rc4_hash,signature", JCE_VECTORS)
def test_encrypt_query_params_matches_jce_vectors(path, index, data, rc4_hash, signature):
    encrypted = encrypt_query_params(
        method="GET",
        path=path,
        params={"data": JCE_PLAINTEXTS[index]},
        nonce=NONCE_JCE,
        ssecurity=SS_JCE,
    )
    assert encrypted == {
        "data": data,
        "rc4_hash__": rc4_hash,
        "signature": signature,
        "_nonce": NONCE_JCE,
    }


def test_encrypt_then_decrypt_roundtrip():
    plaintext = MSG_REF.decode()
    encrypted = encrypt_query_params(
        method="GET",
        path=PATH_REF,
        params={"data": plaintext},
        nonce=NONCE_REF,
        ssecurity=SS_REF,
    )
    assert "signature" in encrypted
    assert encrypted["_nonce"] == NONCE_REF
    assert encrypted["data"] != plaintext

    # Values are encrypted with ONE continuous cipher over the sorted keys
    # (data, then rc4_hash__), so decrypt both ciphertexts as one stream.
    key = _rc4_key(SS_REF, NONCE_REF)
    data_ct = base64.b64decode(encrypted["data"])
    hash_ct = base64.b64decode(encrypted["rc4_hash__"])
    assert rc4_crypt(key, data_ct).decode("utf-8") == plaintext  # data comes first
    stream = rc4_crypt(key, data_ct + hash_ct)
    assert stream[: len(data_ct)].decode("utf-8") == plaintext
    # rc4_hash__ is the signature over the *plaintext* params.
    expected_hash = build_signature(
        "GET", PATH_REF, {"data": plaintext}, signed_nonce(SS_REF, NONCE_REF)
    )
    assert stream[len(data_ct):].decode("utf-8") == expected_hash


def test_encrypt_query_params_signature_covers_encrypted_params():
    encrypted = encrypt_query_params(
        method="GET", path=PATH_REF, params={"data": "{}"}, nonce=NONCE_JCE, ssecurity=SS_JCE
    )
    signed_over = {k: v for k, v in encrypted.items() if k not in ("signature", "_nonce")}
    assert encrypted["signature"] == build_signature(
        "GET", PATH_REF, signed_over, SIGNED_NONCE_JCE
    )


def test_encrypt_query_params_filters_empty_keys_and_values():
    clean = encrypt_query_params(
        method="GET", path=PATH_REF, params={"data": "{}"}, nonce=NONCE_JCE, ssecurity=SS_JCE
    )
    noisy = encrypt_query_params(
        method="GET",
        path=PATH_REF,
        params={"data": "{}", "blank": "", "": "orphan"},
        nonce=NONCE_JCE,
        ssecurity=SS_JCE,
    )
    assert noisy == clean
    assert set(noisy) == {"data", "rc4_hash__", "signature", "_nonce"}


def test_encrypt_query_params_signature_path_override():
    default = encrypt_query_params(
        method="GET", path=PATH_REF, params={"data": "{}"}, nonce=NONCE_JCE, ssecurity=SS_JCE
    )
    overridden = encrypt_query_params(
        method="GET",
        path="/ignored",
        params={"data": "{}"},
        nonce=NONCE_JCE,
        ssecurity=SS_JCE,
        signature_path=PATH_REF,
    )
    assert overridden == default


def test_encrypt_query_params_does_not_mutate_input():
    params = {"data": "{}", "blank": ""}
    encrypt_query_params(
        method="GET", path=PATH_REF, params=params, nonce=NONCE_JCE, ssecurity=SS_JCE
    )
    assert params == {"data": "{}", "blank": ""}


# --- decrypt_response_payload ------------------------------------------------------
def test_decrypt_response_payload_matches_reference_vector():
    decrypted = decrypt_response_payload(RESPONSE_BODY_REF, NONCE_REF, SS_REF)
    assert decrypted == json.loads(RESPONSE_PAYLOAD_REF)


@pytest.mark.parametrize("path,index,data,rc4_hash,signature", JCE_VECTORS[:3])
def test_decrypt_response_payload_matches_jce_vectors(path, index, data, rc4_hash, signature):
    # The JCE harness also verified that a fresh cipher over the ciphertext recovers
    # the plaintext ("response_state_reset" in verify_protocol_08.py).
    decrypted = decrypt_response_payload(data, NONCE_JCE, SS_JCE)
    assert decrypted == json.loads(JCE_PLAINTEXTS[index])


def test_decrypt_response_payload_rejects_non_object_json():
    body = b64(rc4_crypt(_rc4_key(SS_REF, NONCE_REF), b"[1, 2, 3]"))
    with pytest.raises(ValueError):
        decrypt_response_payload(body, NONCE_REF, SS_REF)
