"""The partial-export store (``mifit.cli.partial``): a JSON Lines file with a header
naming the export and one line per finished chunk, written and fsynced as each
chunk finishes and read back by a resumed run. Every value here is synthetic."""

import json
import os
from pathlib import Path

import pytest

from mifit.cli.partial import (
    FORMAT,
    PartialError,
    append_part,
    export_header,
    load_partial,
    partial_path,
    start_partial,
)
from mifit.domains.base import DomainResult
from support import exception_chain

HEADER = export_header("heart_rate", "2026-03-01", "2026-03-15", "week")
LABELS = ["2026-03-01..2026-03-07", "2026-03-08..2026-03-14", "2026-03-15..2026-03-15"]


def part(n):
    """A synthetic, already-plain part: nested records, raw rows, notes and non-ASCII text."""
    return DomainResult(
        records=[
            {"time": 1000 * n + i, "bpm": 60 + i, "tags": {"zone": ["a", n]}} for i in range(3)
        ],
        raw=[{"t": 1000 * n, "v": [1.5, None, "café 心率"]}],
        notes=[f"note {n}", "second note"],
    )


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "out.json.partial"
    start_partial(path, HEADER)
    return path


def reject_constant(name):
    raise ValueError(f"non-standard constant {name}")


def test_partial_path_appends_suffix():
    assert partial_path("out.json") == Path("out.json.partial")
    assert partial_path(Path("data") / "hr.json") == Path("data") / "hr.json.partial"


def test_header_holds_only_export_arguments():
    header = export_header("steps", "2026-01-01", "2026-03-15", "month")
    assert set(header) == {"mifit_partial", "domain", "from", "to", "chunk"}
    assert header == {
        "mifit_partial": FORMAT,
        "domain": "steps",
        "from": "2026-01-01",
        "to": "2026-03-15",
        "chunk": "month",
    }


def test_start_then_load_returns_no_parts(store):
    assert load_partial(store, HEADER, LABELS) == {}


def test_start_truncates_an_existing_file(store):
    append_part(store, LABELS[0], part(1))
    start_partial(store, HEADER)
    assert load_partial(store, HEADER, LABELS) == {}
    assert len(store.read_bytes().splitlines()) == 1


def test_append_then_load_round_trips_parts(store):
    append_part(store, LABELS[0], part(1))
    append_part(store, LABELS[1], part(2))
    assert load_partial(store, HEADER, LABELS) == {LABELS[0]: part(1), LABELS[1]: part(2)}


def test_a_later_line_for_the_same_label_wins(store):
    append_part(store, LABELS[0], part(1))
    append_part(store, LABELS[0], part(2))
    assert load_partial(store, HEADER, LABELS) == {LABELS[0]: part(2)}


def test_a_part_with_empty_fields_round_trips(store):
    append_part(store, LABELS[0], DomainResult())
    assert load_partial(store, HEADER, LABELS) == {LABELS[0]: DomainResult()}


def test_every_line_is_strict_json_and_lf(store):
    append_part(store, LABELS[0], part(1))
    append_part(store, LABELS[1], part(2))
    data = store.read_bytes()
    assert b"\r" not in data
    assert data.endswith(b"\n")
    assert data.isascii()  # non-ASCII text is written as JSON escapes, like the main output
    lines = data.decode("ascii").split("\n")[:-1]
    assert len(lines) == 3
    docs = [json.loads(line, parse_constant=reject_constant) for line in lines]
    assert docs[0] == HEADER
    assert list(docs[1]) == ["chunk", "records", "raw", "notes"]
    assert docs[1]["chunk"] == LABELS[0]
    # compact separators, and the escaped text reads back as itself
    assert lines[0] == json.dumps(HEADER, separators=(",", ":"))
    assert "café" not in lines[1]
    assert docs[1]["raw"][0]["v"][2] == "café 心率"


def test_a_lone_surrogate_and_other_odd_strings_round_trip(store):
    # Legal JSON from the service. Python cannot encode a lone surrogate as UTF-8, so
    # the file carries it as an escape; the label, a key and a note carry one too.
    lone = "a" + chr(0xD800) + "b"
    controls = "x" + chr(0) + chr(0x1F) + chr(0x2028) + "y"
    odd = DomainResult(
        records=[{"s": lone, "controls": controls, "emoji": chr(0x1F600)}],
        raw=[{lone: [lone, chr(0x1F600)]}],
        notes=[lone],
    )
    label = LABELS[0] + lone

    append_part(store, label, odd)

    assert store.read_bytes().isascii()
    assert load_partial(store, HEADER, [label]) == {label: odd}


def test_append_rejects_non_finite_numbers(store):
    before = store.read_bytes()
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            append_part(store, LABELS[0], DomainResult(raw=[{"x": bad}]))
    with pytest.raises(ValueError):
        append_part(store, LABELS[0], DomainResult(records=[{"deep": [[{"x": float("nan")}]]}]))
    assert store.read_bytes() == before  # a refused part writes nothing


def test_every_write_is_fsynced(tmp_path, monkeypatch):
    synced = []
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd))
    path = tmp_path / "out.json.partial"
    start_partial(path, HEADER)
    assert len(synced) == 1
    append_part(path, LABELS[0], part(1))
    append_part(path, LABELS[1], part(2))
    assert len(synced) == 3


def test_load_drops_a_torn_last_line(store):
    append_part(store, LABELS[0], part(1))
    append_part(store, LABELS[1], part(2))
    store.write_bytes(store.read_bytes()[:-5])
    assert load_partial(store, HEADER, LABELS) == {LABELS[0]: part(1)}


def test_load_drops_a_last_line_cut_inside_an_escape(store):
    append_part(store, LABELS[0], part(1))
    append_part(store, LABELS[1], part(2))
    data = store.read_bytes()
    cut = data.rindex(b"\\" + b"u5fc3") + 3  # the line ends in "\u5": half of an escape
    store.write_bytes(data[:cut])
    assert load_partial(store, HEADER, LABELS) == {LABELS[0]: part(1)}


def test_load_drops_a_last_line_that_lacks_a_field(store):
    append_part(store, LABELS[0], part(1))
    with open(store, "ab") as f:
        f.write(b'{"chunk":"' + LABELS[1].encode() + b'","records":[],"raw":[]}\n')
    assert load_partial(store, HEADER, LABELS) == {LABELS[0]: part(1)}


def test_load_repairs_a_torn_tail_so_the_next_append_is_clean(store):
    append_part(store, LABELS[0], part(1))
    append_part(store, LABELS[1], part(2))
    store.write_bytes(store.read_bytes()[:-5])

    assert load_partial(store, HEADER, LABELS) == {LABELS[0]: part(1)}
    append_part(store, LABELS[1], part(3))  # the chunk that is read again

    assert load_partial(store, HEADER, LABELS) == {LABELS[0]: part(1), LABELS[1]: part(3)}
    assert store.read_bytes().count(b"\n") == 3


def test_load_keeps_a_complete_last_line_that_lacks_its_newline(store):
    append_part(store, LABELS[0], part(1))
    append_part(store, LABELS[1], part(2))
    store.write_bytes(store.read_bytes()[:-1])  # only the "\n" is missing

    assert load_partial(store, HEADER, LABELS) == {LABELS[0]: part(1), LABELS[1]: part(2)}
    append_part(store, LABELS[2], part(3))
    assert load_partial(store, HEADER, LABELS) == {
        LABELS[0]: part(1),
        LABELS[1]: part(2),
        LABELS[2]: part(3),
    }


def test_load_does_not_touch_a_sound_file(store, monkeypatch):
    append_part(store, LABELS[0], part(1))
    before = store.read_bytes()
    synced = []
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd))
    monkeypatch.setattr(os, "truncate", lambda *args: pytest.fail("truncated a sound file"))

    load_partial(store, HEADER, LABELS)

    assert store.read_bytes() == before
    assert synced == []


def header_line():
    return json.dumps(HEADER, separators=(",", ":")).encode("ascii") + b"\n"


def assert_repaired(store, sound, parts):
    """``store`` loads to ``parts`` and now holds exactly ``sound``; one more chunk then
    appends cleanly, and the whole file loads again."""
    assert load_partial(store, HEADER, LABELS) == parts
    assert store.read_bytes() == sound
    append_part(store, LABELS[2], part(9))
    assert load_partial(store, HEADER, LABELS) == {**parts, LABELS[2]: part(9)}


def test_load_repairs_a_header_followed_only_by_a_torn_line(store):
    with open(store, "ab") as f:
        f.write(b'{"chunk":"' + LABELS[0].encode() + b'","rec')
    assert_repaired(store, header_line(), {})


def test_load_repairs_a_garbage_last_line_that_ends_with_a_newline(store):
    append_part(store, LABELS[0], part(1))
    sound = store.read_bytes()
    with open(store, "ab") as f:
        f.write(b"\xff\xfe not a line\n")
    assert_repaired(store, sound, {LABELS[0]: part(1)})


def test_load_repairs_a_zero_filled_tail(store):
    append_part(store, LABELS[0], part(1))
    sound = store.read_bytes()
    with open(store, "ab") as f:
        f.write(bytes(64))  # a file extended by the filesystem but never written
    assert_repaired(store, sound, {LABELS[0]: part(1)})


def test_load_restores_the_newline_of_a_header_that_lost_it(store):
    store.write_bytes(header_line()[:-1])
    assert_repaired(store, header_line(), {})


def test_restoring_a_lost_newline_is_fsynced(store, monkeypatch):
    append_part(store, LABELS[0], part(1))
    sound = store.read_bytes()
    store.write_bytes(sound[:-1])
    synced = []
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd))

    load_partial(store, HEADER, LABELS)

    assert store.read_bytes() == sound
    assert len(synced) == 1


def test_load_of_a_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_partial(tmp_path / "missing.json.partial", HEADER, LABELS)


def test_load_passes_on_an_error_from_the_repair(store, monkeypatch):
    append_part(store, LABELS[0], part(1))
    store.write_bytes(store.read_bytes()[:-5])

    def refuse(*args):
        raise PermissionError("read-only")

    monkeypatch.setattr(os, "truncate", refuse)
    with pytest.raises(OSError):
        load_partial(store, HEADER, LABELS)


def test_load_rejects_a_damaged_middle_line(store):
    append_part(store, LABELS[0], part(1))
    lines = store.read_bytes().split(b"\n")
    store.write_bytes(b"\n".join([lines[0], b"{not json", lines[1], b""]))
    append_part(store, LABELS[1], part(2))

    with pytest.raises(PartialError) as caught:
        load_partial(store, HEADER, LABELS)

    assert "damaged" in str(caught.value)
    assert "not json" not in str(caught.value)
    assert exception_chain(caught.value) == [caught.value]


def test_load_rejects_a_middle_line_that_lacks_a_field(store):
    append_part(store, LABELS[0], part(1))
    with open(store, "ab") as f:
        f.write(b'{"chunk":"x"}\n')
    append_part(store, LABELS[1], part(2))
    with pytest.raises(PartialError, match="damaged"):
        load_partial(store, HEADER, LABELS)


def test_load_rejects_a_non_standard_number(store):
    with open(store, "ab") as f:
        f.write(b'{"chunk":"' + LABELS[0].encode() + b'","records":[NaN],"raw":[],"notes":[]}\n')
    append_part(store, LABELS[1], part(2))
    with pytest.raises(PartialError, match="damaged"):
        load_partial(store, HEADER, LABELS)


def test_load_rejects_unknown_chunk_label(store):
    append_part(store, "2026-04-01..2026-04-07", part(1))
    with pytest.raises(PartialError) as caught:
        load_partial(store, HEADER, LABELS)
    assert "damaged" in str(caught.value)
    assert "2026-04-01" not in str(caught.value)
    assert exception_chain(caught.value) == [caught.value]


def test_load_rejects_another_exports_header(tmp_path):
    path = tmp_path / "out.json.partial"
    start_partial(path, export_header("heart_rate", "2026-03-01", "2026-03-15", "week"))

    with pytest.raises(PartialError) as caught:
        load_partial(path, export_header("heart_rate", "2026-03-01", "2026-03-20", "week"), LABELS)

    message = str(caught.value)
    assert "2026-03-15" in message
    assert "--resume" in message
    assert "heart_rate" in message and "2026-03-01" in message and "week" in message
    assert exception_chain(caught.value) == [caught.value]


def test_load_rejects_another_domains_header(tmp_path):
    path = tmp_path / "out.json.partial"
    start_partial(path, export_header("steps", "2026-03-01", "2026-03-15", "month"))
    with pytest.raises(PartialError, match="steps"):
        load_partial(path, HEADER, LABELS)


def test_a_foreign_header_does_not_echo_odd_values(tmp_path):
    path = tmp_path / "out.json.partial"
    odd = {**HEADER, "domain": "bad\x1b[31m domain\nwith lines", "to": ["x"]}
    path.write_bytes(json.dumps(odd).encode("utf-8") + b"\n")
    with pytest.raises(PartialError) as caught:
        load_partial(path, HEADER, LABELS)
    assert "\x1b" not in str(caught.value)
    assert "\n" not in str(caught.value)


def test_load_rejects_a_file_without_header(tmp_path):
    path = tmp_path / "out.json.partial"
    path.write_bytes(
        json.dumps({"chunk": LABELS[0], "records": [], "raw": [], "notes": []}).encode() + b"\n"
    )
    with pytest.raises(PartialError, match="damaged") as caught:
        load_partial(path, HEADER, LABELS)
    assert exception_chain(caught.value) == [caught.value]


def test_load_rejects_an_empty_file(tmp_path):
    path = tmp_path / "out.json.partial"
    path.write_bytes(b"")
    with pytest.raises(PartialError, match="damaged"):
        load_partial(path, HEADER, LABELS)


def test_load_rejects_a_header_that_is_not_json(tmp_path):
    path = tmp_path / "out.json.partial"
    path.write_bytes(b"{this is not json\n")
    with pytest.raises(PartialError) as caught:
        load_partial(path, HEADER, LABELS)
    assert "damaged" in str(caught.value)
    assert "this is not json" not in str(caught.value)
    assert exception_chain(caught.value) == [caught.value]


def test_load_rejects_a_torn_header(store):
    store.write_bytes(store.read_bytes()[:-5])
    with pytest.raises(PartialError, match="damaged"):
        load_partial(store, HEADER, LABELS)
