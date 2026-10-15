"""``mifit.jsonsafe``: domain results as strict JSON data, shared by the CLI and the
MCP server."""

import json

from mifit import jsonsafe
from mifit.domains.base import DomainResult
from mifit.models.weight import WeightRecord


def test_finite_replaces_only_non_finite_floats_at_any_depth():
    value = {
        "nan": float("nan"),
        "rows": [(float("inf"), {"deep": [float("-inf")]}), 2.5, 3, True, None, "x"],
    }

    cleaned, count = jsonsafe.finite(value)

    assert count == 3
    assert cleaned == {"nan": None, "rows": [[None, {"deep": [None]}], 2.5, 3, True, None, "x"]}
    assert isinstance(cleaned["rows"][0], list)  # tuples become lists
    assert cleaned["rows"][4] is None and cleaned["rows"][3] is True
    assert jsonsafe.finite([1, 2.0, {"k": "v"}]) == ([1, 2.0, {"k": "v"}], 0)
    assert value["nan"] != value["nan"]  # the input is left untouched


def test_finite_keeps_the_order_of_keys_and_items():
    value = {
        "b": [3, float("nan"), {"z": 1, "a": 2}],
        "a": (1.5, "x"),
        "c": {"y": None, "x": float("inf")},
    }

    cleaned, count = jsonsafe.finite(value)

    assert count == 2
    # ``json.dumps`` writes keys in insertion order, so this pins the order too.
    assert json.dumps(cleaned) == (
        '{"b": [3, null, {"z": 1, "a": 2}], "a": [1.5, "x"], "c": {"y": null, "x": null}}'
    )


def test_finite_handles_nesting_deeper_than_the_recursion_limit():
    depth = 5000  # json.loads accepts this deep on Python 3.14
    value = float("nan")
    for level in range(depth):
        value = [value] if level % 2 else {"k": value}

    cleaned, count = jsonsafe.finite(value)

    assert count == 1
    # Walked down in a loop: ``==`` on 5000 levels would recurse itself.
    node = cleaned
    for level in reversed(range(depth)):
        if level % 2:
            assert isinstance(node, list) and len(node) == 1
            node = node[0]
        else:
            assert isinstance(node, dict) and list(node) == ["k"]
            node = node["k"]
    assert node is None


def test_nonfinite_note_is_the_cli_text():
    assert jsonsafe.nonfinite_note(3) == (
        "Replaced 3 non-finite number(s) (NaN or Infinity) with null: "
        "strict JSON has no such values."
    )


def test_json_ready_turns_records_into_dicts_and_counts_replacements():
    result = jsonsafe.json_ready(
        DomainResult(
            records=[WeightRecord(epoch_s=1, weight_kg=float("nan"), bmi=None)],
            raw=[],
            notes=["n"],
        )
    )
    assert result.records == [{"epoch_s": 1, "weight_kg": None, "bmi": None}]
    assert result.notes == ["n", jsonsafe.nonfinite_note(1)]
