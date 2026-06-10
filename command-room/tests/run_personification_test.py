#!/usr/bin/env python3
"""Test for v3.14.2 personification helper + brain-name vocative routing.

Exercises shared/scripts/personification.py against synthetic workspaces:

  Helper (get_brain_name):
    1. Workspace with workspace.brain_name set      -> returns that name
    2. Workspace with no workspace section at all   -> "Penelope"
    3. Workspace with empty brain_name string       -> "Penelope"
    4. Workspace with whitespace-only brain_name    -> "Penelope"
    5. No entities.json at all                      -> "Penelope"
    6. Malformed entities.json                      -> "Penelope"
    7. brain_name is not a string (e.g. number)     -> "Penelope"
    8. Renamed brain (e.g. "Aria")                  -> "Aria"

  Vocative router (detect_vocative_address):
    9.  "Penelope, what's overdue?"                 -> matched, remainder
    10. "Penelope - prep me for my 2pm"             -> matched, remainder
    11. "Penelope?"                                  -> matched, empty
    12. "Hey Penelope, draft an email..."           -> matched, remainder
    13. "Penelope what's going on"                  -> matched, remainder
    14. "Penelope"                                  -> matched, empty (wake call)
    15. "Did Penelope send the brief?"              -> no match (indirect)
    16. "Penelopes work for me"                     -> no match (name is prefix)
    17. ""                                          -> no match
    18. "  Penelope, hi"                            -> matched after lstrip
    19. Custom brain_name "Aria, ..."               -> matched
    20. Case-insensitive: "penelope, ..."           -> matched
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from personification import (  # noqa: E402
    DEFAULT_BRAIN_NAME,
    detect_vocative_address,
    get_brain_name,
)


def _setup_workspace(entities: dict | None) -> Path:
    """Build a synthetic workspace; return workspace_root path."""
    tmp = Path(tempfile.mkdtemp(prefix="cr_personification_test_"))
    data_dir = tmp / "_hq" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    if entities is not None:
        (data_dir / "entities.json").write_text(
            json.dumps(entities, indent=2), encoding="utf-8"
        )
    return tmp


# --------- helper tests ---------

def test_brain_name_set_returns_value():
    root = _setup_workspace({"workspace": {"brain_name": "Penelope"}})
    assert get_brain_name(root) == "Penelope"
    print("PASS test_brain_name_set_returns_value")


def test_no_workspace_section_returns_default():
    root = _setup_workspace({"people": [], "orgs": []})
    assert get_brain_name(root) == DEFAULT_BRAIN_NAME
    print("PASS test_no_workspace_section_returns_default")


def test_empty_brain_name_returns_default():
    root = _setup_workspace({"workspace": {"brain_name": ""}})
    assert get_brain_name(root) == DEFAULT_BRAIN_NAME
    print("PASS test_empty_brain_name_returns_default")


def test_whitespace_brain_name_returns_default():
    root = _setup_workspace({"workspace": {"brain_name": "   "}})
    assert get_brain_name(root) == DEFAULT_BRAIN_NAME
    print("PASS test_whitespace_brain_name_returns_default")


def test_no_entities_file_returns_default():
    tmp = Path(tempfile.mkdtemp(prefix="cr_personification_test_empty_"))
    assert get_brain_name(tmp) == DEFAULT_BRAIN_NAME
    print("PASS test_no_entities_file_returns_default")


def test_malformed_entities_returns_default():
    root = _setup_workspace(None)
    (root / "_hq" / "data" / "entities.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    assert get_brain_name(root) == DEFAULT_BRAIN_NAME
    print("PASS test_malformed_entities_returns_default")


def test_non_string_brain_name_returns_default():
    root = _setup_workspace({"workspace": {"brain_name": 42}})
    assert get_brain_name(root) == DEFAULT_BRAIN_NAME
    print("PASS test_non_string_brain_name_returns_default")


def test_renamed_brain_returns_custom_name():
    root = _setup_workspace({"workspace": {"brain_name": "Aria"}})
    assert get_brain_name(root) == "Aria"
    print("PASS test_renamed_brain_returns_custom_name")


# --------- vocative router tests ---------

def test_vocative_comma():
    matched, remainder = detect_vocative_address(
        "Penelope, what's overdue?", "Penelope"
    )
    assert matched
    assert remainder == "what's overdue?"
    print("PASS test_vocative_comma")


def test_vocative_dash():
    matched, remainder = detect_vocative_address(
        "Penelope - prep me for my 2pm", "Penelope"
    )
    assert matched
    assert remainder == "prep me for my 2pm"
    print("PASS test_vocative_dash")


def test_vocative_question_mark():
    matched, remainder = detect_vocative_address("Penelope?", "Penelope")
    assert matched
    assert remainder == ""
    print("PASS test_vocative_question_mark")


def test_vocative_with_greeting():
    matched, remainder = detect_vocative_address(
        "Hey Penelope, draft an email to Sam", "Penelope"
    )
    assert matched
    assert remainder == "draft an email to Sam"
    print("PASS test_vocative_with_greeting")


def test_vocative_single_space():
    matched, remainder = detect_vocative_address(
        "Penelope what's going on", "Penelope"
    )
    assert matched
    assert remainder == "what's going on"
    print("PASS test_vocative_single_space")


def test_vocative_bare_wake_call():
    matched, remainder = detect_vocative_address("Penelope", "Penelope")
    assert matched
    assert remainder == ""
    print("PASS test_vocative_bare_wake_call")


def test_indirect_reference_no_match():
    matched, remainder = detect_vocative_address(
        "Did Penelope send the brief?", "Penelope"
    )
    assert not matched
    assert remainder == "Did Penelope send the brief?"
    print("PASS test_indirect_reference_no_match")


def test_name_as_prefix_no_match():
    matched, _ = detect_vocative_address("Penelopes work for me", "Penelope")
    assert not matched
    print("PASS test_name_as_prefix_no_match")


def test_empty_input_no_match():
    matched, _ = detect_vocative_address("", "Penelope")
    assert not matched
    print("PASS test_empty_input_no_match")


def test_leading_whitespace_stripped():
    matched, remainder = detect_vocative_address(
        "   Penelope, hi", "Penelope"
    )
    assert matched
    assert remainder == "hi"
    print("PASS test_leading_whitespace_stripped")


def test_custom_brain_name_matches():
    matched, remainder = detect_vocative_address(
        "Aria, what's the status on Northstar?", "Aria"
    )
    assert matched
    assert remainder == "what's the status on Northstar?"
    print("PASS test_custom_brain_name_matches")


def test_case_insensitive():
    matched, remainder = detect_vocative_address(
        "penelope, give me the brief", "Penelope"
    )
    assert matched
    assert remainder == "give me the brief"
    print("PASS test_case_insensitive")


def test_rename_then_address_works():
    """End-to-end: workspace renames brain to Aria, addressing by Aria matches."""
    root = _setup_workspace({"workspace": {"brain_name": "Aria"}})
    brain_name = get_brain_name(root)
    matched, remainder = detect_vocative_address(
        f"{brain_name}, prep me for my 3pm", brain_name
    )
    assert matched
    assert remainder == "prep me for my 3pm"
    print("PASS test_rename_then_address_works")


def main():
    # Helper tests
    test_brain_name_set_returns_value()
    test_no_workspace_section_returns_default()
    test_empty_brain_name_returns_default()
    test_whitespace_brain_name_returns_default()
    test_no_entities_file_returns_default()
    test_malformed_entities_returns_default()
    test_non_string_brain_name_returns_default()
    test_renamed_brain_returns_custom_name()
    # Vocative tests
    test_vocative_comma()
    test_vocative_dash()
    test_vocative_question_mark()
    test_vocative_with_greeting()
    test_vocative_single_space()
    test_vocative_bare_wake_call()
    test_indirect_reference_no_match()
    test_name_as_prefix_no_match()
    test_empty_input_no_match()
    test_leading_whitespace_stripped()
    test_custom_brain_name_matches()
    test_case_insensitive()
    test_rename_then_address_works()
    print()
    print("OK - all 20 personification tests passed.")


if __name__ == "__main__":
    main()
