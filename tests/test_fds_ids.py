"""FDS file names: the key bytes, the ``suffix`` and the server key (spec §4.2).

The vectors were computed with Python from the rule in the spec; the 1234567890
rows differ in sport format (22, 23), time zone (positive, negative) and file type.
"""

import pytest

from mifit.fds.ids import GPS, RECORD, FileRef, key_bytes, server_key, suffix


def test_key_bytes_vector():
    ref = FileRef("1234567890", 1700000000, 32, 22, RECORD)

    assert key_bytes(ref) == bytes.fromhex("00f1536520d8")


@pytest.mark.parametrize(
    ("sid", "timestamp", "tz15", "proto", "file_type", "expected"),
    [
        ("1234567890", 1700000000, 32, 22, RECORD, "APFTZSDY_AbMHrLpPVPVar8M7sGu79sqAPpo"),
        ("1234567890", 1700000000, 32, 22, GPS, "APFTZSDa_AbMHrLpPVPVar8M7sGu79sqAPpo"),
        ("1234567890", 1700000000, -20, 23, RECORD, "APFTZezc_AbMHrLpPVPVar8M7sGu79sqAPpo"),
        ("1234567890", 1700000000, -20, 23, GPS, "APFTZeze_AbMHrLpPVPVar8M7sGu79sqAPpo"),
        # The URL-safe alphabet (``_`` and ``-``) and the missing padding.
        ("1000000006", 1700000003, 32, 22, RECORD, "A_FTZSDY_cp-gLXCjBkAd5lnqRh1s3r_Zyd4"),
    ],
)
def test_suffix_vectors(sid, timestamp, tz15, proto, file_type, expected):
    assert suffix(FileRef(sid, timestamp, tz15, proto, file_type)) == expected


def test_server_key_appends_the_timestamp():
    ref = FileRef("1234567890", 1700000000, 32, 22, RECORD)

    assert server_key(ref) == "APFTZSDY_AbMHrLpPVPVar8M7sGu79sqAPpo_1700000000"


@pytest.mark.parametrize(
    "changes",
    [
        {"timestamp": 2**32},
        {"timestamp": 1700000000000},
        {"timestamp": -1},
        {"timestamp": 1.5},
        {"tz15": 128},
        {"tz15": -129},
        {"tz15": True},
        {"proto": 32},
        {"proto": -1},
        {"file_type": 1},
        {"file_type": 3},
        {"sid": ""},
        {"sid": None},
        {"sid": "\ud800"},
    ],
    ids=repr,
)
def test_out_of_range_refs_are_refused(changes):
    fields = {
        "sid": "1234567890",
        "timestamp": 1700000000,
        "tz15": 32,
        "proto": 22,
        "file_type": RECORD,
    }
    fields.update(changes)

    with pytest.raises(ValueError) as caught:
        FileRef(**fields)

    assert "1234567890" not in str(caught.value)


def test_repr_hides_the_source_id():
    ref = FileRef("1234567890", 1700000000, 32, 22, RECORD)

    assert "1234567890" not in repr(ref)
