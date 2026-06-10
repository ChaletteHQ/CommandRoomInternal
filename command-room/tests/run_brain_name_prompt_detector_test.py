#!/usr/bin/env python3
"""
Test for v3.13.8.2 brain_name_prompt detector (Bug #72).

Verifies:
  1. Empty workspace (no entities.json, empty events.jsonl) → applies=False
     (fresh-install path; M1 onboarding handles capture, not the bridge).
  2. Workspace with brain_name set in entities.json → applies=False (idempotent).
  3. Workspace with brain_name_captured event in events.jsonl → applies=False.
  4. Workspace with brain_name_declined event in events.jsonl → applies=False.
  5. Upgrade-customer workspace (entities.json exists, has events, NO brain_name
     anywhere, NO capture event, NO decline event) → applies=True, with
     default_name in context for the prompt.
  6. Malformed entities.json → still checks events.jsonl as fallback.
  7. Existing entities.json with no workspace section at all → applies=True
     (M's actual scenario as of session-close).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from release_detectors.v3_13_8_2_brain_name_prompt import (  # noqa: E402
    needs_brain_name_prompt,
)


def _setup(entities: dict | None = None, events: list[dict] | None = None) -> Path:
    """Build a synthetic workspace; return events.jsonl path."""
    tmp = Path(tempfile.mkdtemp(prefix="cr_brain_name_test_"))
    data_dir = tmp / "_hq" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    if entities is not None:
        (data_dir / "entities.json").write_text(
            json.dumps(entities, indent=2), encoding="utf-8"
        )
    events_path = data_dir / "events.jsonl"
    if events is not None:
        events_path.write_text(
            "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
        )
    else:
        events_path.write_text("", encoding="utf-8")
    return events_path


# ---------- Test 1: fresh empty workspace (no events.jsonl yet) ----------

def test_no_events_file_applies_false():
    tmp = Path(tempfile.mkdtemp(prefix="cr_brain_name_test_fresh_"))
    # Intentionally don't create the data dir at all
    events_path = tmp / "_hq" / "data" / "events.jsonl"
    result = needs_brain_name_prompt(events_path)
    assert result == {"applies": False}, f"expected applies=False, got {result}"
    print("PASS test_no_events_file_applies_false")


# ---------- Test 2: brain_name set in entities.json → no prompt ----------

def test_brain_name_already_set_applies_false():
    events_path = _setup(
        entities={"workspace": {"brain_name": "Penelope"}},
        events=[],
    )
    result = needs_brain_name_prompt(events_path)
    assert result["applies"] is False
    print("PASS test_brain_name_already_set_applies_false")


# ---------- Test 3: brain_name_captured event exists → no prompt ----------

def test_brain_name_captured_event_applies_false():
    events_path = _setup(
        entities={"workspace": {}},  # no brain_name here…
        events=[
            {
                "type": "brain_name_captured",
                "ts": "2026-05-25T10:00:00Z",
                "data": {"brain_name": "Penelope"},
            },
        ],
    )
    result = needs_brain_name_prompt(events_path)
    assert result["applies"] is False
    print("PASS test_brain_name_captured_event_applies_false")


# ---------- Test 4: brain_name_declined event exists → no prompt ----------

def test_brain_name_declined_event_applies_false():
    events_path = _setup(
        entities={"workspace": {}},
        events=[
            {
                "type": "brain_name_declined",
                "ts": "2026-05-25T10:00:00Z",
                "data": {"via": "manual_trigger"},
            },
        ],
    )
    result = needs_brain_name_prompt(events_path)
    assert result["applies"] is False
    print("PASS test_brain_name_declined_event_applies_false")


# ---------- Test 5: upgrade-customer workspace with no brain_name → prompt fires ----------

def test_upgrade_customer_applies_true():
    """M's actual scenario as of v3.13.8 verification session close."""
    events_path = _setup(
        entities={
            "schema_version": "1.0",
            "people": [{"id": "person_001", "canonical_name": "Owner"}],
            # No workspace.brain_name anywhere
        },
        events=[
            # Plenty of normal activity, just no brain_name event
            {"type": "noop", "ts": "2026-05-15T10:00:00Z", "data": {}},
            {"type": "commitment_logged", "ts": "2026-05-16T10:00:00Z", "data": {}},
        ],
    )
    result = needs_brain_name_prompt(events_path)
    assert result["applies"] is True, f"expected applies=True, got {result}"
    assert "context" in result
    assert result["context"]["default_name"] == "Penelope"
    print("PASS test_upgrade_customer_applies_true")


# ---------- Test 6: malformed entities.json → falls through to events.jsonl check ----------

def test_malformed_entities_json_falls_through():
    """Detector must not crash on malformed entities.json — falls through to
    the events.jsonl check (which is the authoritative idempotency surface)."""
    events_path = _setup(events=[])
    # Overwrite entities.json with malformed content
    entities_path = events_path.parent / "entities.json"
    entities_path.write_text("{not valid json", encoding="utf-8")

    result = needs_brain_name_prompt(events_path)
    # Should still work — events.jsonl is empty (no capture/decline events),
    # so detector returns applies=True
    assert result["applies"] is True
    print("PASS test_malformed_entities_json_falls_through")


# ---------- Test 7: entities.json with no workspace section at all ----------

def test_entities_with_no_workspace_section():
    """M's actual scenario — entities.json exists with people + orgs but
    no workspace section at all."""
    events_path = _setup(
        entities={
            "schema_version": "1.0",
            "people": [{"id": "person_001"}],
            "orgs": [{"id": "org_001"}],
        },
        events=[],
    )
    result = needs_brain_name_prompt(events_path)
    assert result["applies"] is True
    print("PASS test_entities_with_no_workspace_section")


def main():
    test_no_events_file_applies_false()
    test_brain_name_already_set_applies_false()
    test_brain_name_captured_event_applies_false()
    test_brain_name_declined_event_applies_false()
    test_upgrade_customer_applies_true()
    test_malformed_entities_json_falls_through()
    test_entities_with_no_workspace_section()
    print()
    print("OK — all 7 brain_name_prompt detector tests passed.")


if __name__ == "__main__":
    main()
