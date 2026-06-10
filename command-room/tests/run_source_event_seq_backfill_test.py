#!/usr/bin/env python3
"""
Test for v3.13.8.1 source_event_seq backfill migration (Bug #71).

Verifies:
  1. Legacy commitment_to_discuss wrappers (no data.source_event_seq) get
     scanned for matching source commitments.
  2. High-confidence text-similarity match + thread anchoring backfills
     source_event_seq with high_confidence marker.
  3. Low-confidence wrappers get needs_review marker (no false-positive
     link).
  4. Idempotency: a second run is a no-op.
  5. wrapper_source_seq_backfill event is written with v3.13.8.1 marker.
  6. Wrappers that already have source_event_seq are NOT touched (regression
     check — must not break v3.13.8 go-forward wrappers).
  7. A clean workspace with no legacy wrappers produces a no_op marker
     event so future runs don't rescan.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from source_event_seq_backfill import (  # noqa: E402
    RECOVERY_VERSION,
    run_backfill_if_needed,
)


def _setup_workspace(events: list[dict]) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="cr_backfill_test_"))
    data_dir = tmp / "_hq" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    events_path = data_dir / "events.jsonl"
    events_path.write_text(
        "".join(json.dumps(e) + "\n" for e in events),
        encoding="utf-8",
    )
    return tmp


def _read_events(workspace_root: Path) -> list[dict]:
    path = workspace_root / "_hq" / "data" / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------- Test 1: high-confidence match ----------

def test_high_confidence_match_backfills_link():
    """Source commitment + legacy wrapper with very similar text + same
    thread → should backfill source_event_seq with high_confidence marker."""
    events = [
        # The source commitment (the underlying work item)
        {
            "seq": 100,
            "ts": "2026-05-15T10:00:00Z",
            "type": "commitment_logged",
            "source_skill": "meeting-notes",
            "primary_thread_id": "project_015",
            "data": {
                "commitment_id": 1779999999900000000,
                "text": "Investigate Granola MCP auth failure when authenticated via Microsoft",
                "owner_person_id": "person_001",
            },
        },
        # The legacy wrapper (no data.source_event_seq)
        {
            "seq": 200,
            "ts": "2026-05-20T15:30:00Z",
            "type": "commitment_to_discuss",
            "source_skill": "apply-choices",
            "primary_thread_id": "project_015",
            "data": {
                "summary": "Granola MCP-via-Microsoft auth blocker",
                "via": "show_my_list_add",
            },
        },
    ]

    workspace = _setup_workspace(events)
    summary = run_backfill_if_needed(workspace)

    assert summary["ran"] is True, f"expected ran=True, got {summary}"
    assert summary["wrappers_linked"] == 1, f"expected 1 link, got {summary}"
    assert summary["wrappers_marked_needs_review"] == 0

    written = _read_events(workspace)
    wrapper = next(e for e in written if e.get("type") == "commitment_to_discuss")
    assert wrapper["data"]["source_event_seq"] == 100
    assert wrapper["data"]["source_event_seq_match"] == "high_confidence"
    assert wrapper["data"]["source_event_seq_backfilled_by"] == RECOVERY_VERSION

    # Marker event
    marker = next(e for e in written if e.get("type") == "wrapper_source_seq_backfill")
    assert marker["data"]["wrappers_linked"] == 1
    assert marker["data"]["recovery_version"] == RECOVERY_VERSION
    print("PASS test_high_confidence_match_backfills_link")


# ---------- Test 2: low-confidence stays unlinked + gets needs_review marker ----------

def test_low_confidence_marked_needs_review():
    """Source commitment + legacy wrapper with NO text overlap → no link;
    wrapper gets needs_review marker so future scans skip it."""
    events = [
        {
            "seq": 100,
            "ts": "2026-05-15T10:00:00Z",
            "type": "commitment_logged",
            "source_skill": "meeting-notes",
            "primary_thread_id": "project_015",
            "data": {
                "commitment_id": 1779999999900000000,
                "text": "Ship next-quarter pricing memo to the board",
            },
        },
        {
            "seq": 200,
            "ts": "2026-05-20T15:30:00Z",
            "type": "commitment_to_discuss",
            "source_skill": "apply-choices",
            "primary_thread_id": "project_999",  # different thread
            "data": {
                "summary": "Investigate audio playback distortion bug",  # no overlap
            },
        },
    ]

    workspace = _setup_workspace(events)
    summary = run_backfill_if_needed(workspace)

    assert summary["ran"] is True
    assert summary["wrappers_linked"] == 0
    assert summary["wrappers_marked_needs_review"] == 1

    written = _read_events(workspace)
    wrapper = next(e for e in written if e.get("type") == "commitment_to_discuss")
    # Wrapper should NOT have a false-positive link
    assert wrapper["data"].get("source_event_seq") is None
    assert wrapper["data"]["source_event_seq_match"] == "needs_review"
    assert wrapper["data"]["source_event_seq_backfill_attempted"] == RECOVERY_VERSION
    print("PASS test_low_confidence_marked_needs_review")


# ---------- Test 3: idempotency ----------

def test_idempotency_second_run_is_noop():
    """A second run on the same workspace must short-circuit."""
    events = [
        {
            "seq": 100,
            "ts": "2026-05-15T10:00:00Z",
            "type": "commitment_logged",
            "primary_thread_id": "project_015",
            "data": {"text": "Investigate Granola auth issue"},
        },
        {
            "seq": 200,
            "ts": "2026-05-20T15:30:00Z",
            "type": "commitment_to_discuss",
            "primary_thread_id": "project_015",
            "data": {"summary": "Granola auth investigation"},
        },
    ]

    workspace = _setup_workspace(events)
    run_backfill_if_needed(workspace)  # first run
    second = run_backfill_if_needed(workspace)  # second run

    assert second["ran"] is False
    assert second["skipped_reason"] == "already_run"
    print("PASS test_idempotency_second_run_is_noop")


# ---------- Test 4: go-forward wrappers (already have source_event_seq) untouched ----------

def test_go_forward_wrapper_not_touched():
    """v3.13.8 wrappers that already carry data.source_event_seq must not be
    rewritten by the backfill. Regression check."""
    events = [
        {
            "seq": 100,
            "ts": "2026-05-15T10:00:00Z",
            "type": "commitment_logged",
            "primary_thread_id": "project_015",
            "data": {"text": "Original work item text"},
        },
        # Already-linked go-forward wrapper (v3.13.8 native)
        {
            "seq": 200,
            "ts": "2026-05-25T12:00:00Z",
            "type": "commitment_to_discuss",
            "primary_thread_id": "project_015",
            "data": {
                "summary": "Already linked wrapper",
                "source_event_seq": 100,  # already set
                "via": "show_my_list_add",
            },
        },
    ]

    workspace = _setup_workspace(events)
    summary = run_backfill_if_needed(workspace)

    # Wrapper was not a candidate; no link should have been written
    assert summary["wrappers_linked"] == 0
    assert summary["wrappers_marked_needs_review"] == 0

    written = _read_events(workspace)
    wrapper = next(e for e in written if e.get("type") == "commitment_to_discuss")
    # Field is unchanged
    assert wrapper["data"]["source_event_seq"] == 100
    # Should NOT have backfilled_by marker
    assert "source_event_seq_backfilled_by" not in wrapper["data"]
    print("PASS test_go_forward_wrapper_not_touched")


# ---------- Test 5: needs_review wrappers not re-scanned on next run ----------

def test_needs_review_wrappers_skipped_on_rerun():
    """If a previous run already marked a wrapper as needs_review, the next
    full migration should leave it alone — the UI flow takes over from there."""
    events = [
        {
            "seq": 100,
            "ts": "2026-05-15T10:00:00Z",
            "type": "commitment_logged",
            "data": {"text": "Some completely different work"},
        },
        # Already-attempted wrapper (needs_review)
        {
            "seq": 200,
            "ts": "2026-05-20T15:30:00Z",
            "type": "commitment_to_discuss",
            "data": {
                "summary": "Granola investigation",
                "source_event_seq_match": "needs_review",
                "source_event_seq_backfill_attempted": "v3.13.8.0",  # prior version
            },
        },
    ]

    workspace = _setup_workspace(events)
    summary = run_backfill_if_needed(workspace)

    # Wrapper was already attempted → not re-examined
    assert summary["wrappers_examined"] == 0
    # And the no_op marker event should be written
    assert summary["ran"] is False
    assert summary["skipped_reason"] == "no_legacy_wrappers"
    print("PASS test_needs_review_wrappers_skipped_on_rerun")


# ---------- Test 6: clean workspace (no legacy wrappers) writes no_op marker ----------

def test_clean_workspace_writes_noop_marker():
    """Workspace with no legacy wrappers should still write a marker event
    so future runs see the migration completed (no re-scan every update)."""
    events = [
        {
            "seq": 100,
            "ts": "2026-05-15T10:00:00Z",
            "type": "commitment_logged",
            "data": {"text": "Just a commitment, no wrappers exist"},
        },
    ]

    workspace = _setup_workspace(events)
    summary = run_backfill_if_needed(workspace)

    assert summary["ran"] is False
    assert summary["skipped_reason"] == "no_legacy_wrappers"

    written = _read_events(workspace)
    marker = next(
        (e for e in written if e.get("type") == "wrapper_source_seq_backfill"),
        None,
    )
    assert marker is not None, "no_op marker event must be written"
    assert marker["data"].get("no_op") is True
    assert marker["data"]["recovery_version"] == RECOVERY_VERSION

    # Second run should now short-circuit on idempotency
    second = run_backfill_if_needed(workspace)
    assert second["ran"] is False
    assert second["skipped_reason"] == "already_run"
    print("PASS test_clean_workspace_writes_noop_marker")


# ---------- Test 7: no events file at all ----------

def test_missing_events_file_safe():
    """Workspace where _hq/data/events.jsonl doesn't exist yet should
    short-circuit gracefully, no error."""
    tmp = Path(tempfile.mkdtemp(prefix="cr_backfill_test_missing_"))
    (tmp / "_hq" / "data").mkdir(parents=True, exist_ok=True)
    summary = run_backfill_if_needed(tmp)
    assert summary["ran"] is False
    assert summary["skipped_reason"] == "no_events_file"
    print("PASS test_missing_events_file_safe")


# ---------- Test 8: canonical "commitment" source type is recognized (Bug #2 regression) ----------

def test_canonical_commitment_type_is_a_candidate_source():
    """REGRESSION for the v3.14.5 backfill no-op bug.

    The real writer emits source commitments as type `commitment` (plain), but
    COMMITMENT_TYPES historically only listed `commitment_logged` /
    `commitment_pending_review` / `commitment_captured`. Every other test in this
    file uses `commitment_logged`, so they all passed while production found ZERO
    candidate sources in every real workspace and the backfill did nothing.

    This test pins the fix: a source event of type `commitment` MUST be eligible
    as a backfill source so a legacy wrapper can link to it. If someone removes
    `commitment` from COMMITMENT_TYPES, this fails.
    """
    events = [
        {
            "seq": 100,
            "ts": "2026-05-15T10:00:00Z",
            "type": "commitment",  # <-- the REAL production type, not commitment_logged
            "source_skill": "meeting-notes",
            "primary_thread_id": "project_015",
            "data": {
                "commitment_id": 1779999999900000000,
                "text": "Investigate Granola MCP auth failure when authenticated via Microsoft",
                "owner_person_id": "person_001",
            },
        },
        {
            "seq": 200,
            "ts": "2026-05-20T15:30:00Z",
            "type": "commitment_to_discuss",
            "source_skill": "apply-choices",
            "primary_thread_id": "project_015",
            "data": {
                "summary": "Granola MCP-via-Microsoft auth blocker",
                "via": "show_my_list_add",
            },
        },
    ]

    workspace = _setup_workspace(events)
    summary = run_backfill_if_needed(workspace)

    assert summary["ran"] is True, f"expected ran=True, got {summary}"
    assert summary["wrappers_linked"] == 1, (
        f"plain `commitment` source must be a candidate (Bug #2); got {summary}"
    )

    written = _read_events(workspace)
    wrapper = next(e for e in written if e.get("type") == "commitment_to_discuss")
    assert wrapper["data"]["source_event_seq"] == 100
    assert wrapper["data"]["source_event_seq_match"] == "high_confidence"
    print("PASS test_canonical_commitment_type_is_a_candidate_source")


# ---------- Test 9: Bug #80 — malformed lines quarantined, not silently dropped ----------

def test_bug80_malformed_lines_quarantined_not_dropped():
    """Bug #80 (2026-05-31): the backfill rewrites events.jsonl from the
    defensively-loaded events only, so before the fix any malformed line was
    silently dropped on rewrite — no quarantine, no corruption_recovery event,
    bypassing every recovery contract. After the fix the backfill routes
    malformed lines through recover_corruption (recurring) first, so they are
    preserved in quarantine + audited before the rewrite."""
    workspace = _setup_workspace([
        {"seq": 100, "ts": "2026-05-30T10:00:00Z", "type": "commitment",
         "source_skill": "meeting-notes", "data": {"title": "real work item"}},
    ])
    events_path = workspace / "_hq" / "data" / "events.jsonl"
    # Inject a malformed line (the Bug #68 torn-write / keys-only class).
    with open(events_path, "a", encoding="utf-8") as f:
        f.write("this is a torn half-written line, not valid json\n")

    run_backfill_if_needed(workspace)

    raw = events_path.read_text(encoding="utf-8")
    written = _read_events(workspace)

    # 1. The malformed content is no longer a raw line in events.jsonl...
    assert "torn half-written line" not in raw, (
        "Bug #80: malformed line still in events.jsonl — heal did not run"
    )
    # 2. ...but it was preserved in quarantine, not silently dropped.
    quarantine_dir = workspace / "_hq" / ".system" / "quarantine"
    q_files = list(quarantine_dir.glob("*")) if quarantine_dir.exists() else []
    assert q_files, "Bug #80: no quarantine file — malformed line was silently dropped"
    q_content = "".join(p.read_text(encoding="utf-8") for p in q_files)
    assert "torn half-written line" in q_content, (
        "Bug #80: malformed line not preserved in quarantine"
    )
    # 3. A corruption_recovery event documents the salvage (audit trail).
    assert any(e.get("type") == "corruption_recovery" for e in written), (
        "Bug #80: no corruption_recovery event — recovery contract bypassed"
    )
    # 4. The real event survived.
    assert any(e.get("seq") == 100 for e in written), "real event was lost in the heal"
    print("PASS test_bug80_malformed_lines_quarantined_not_dropped")


def main():
    test_high_confidence_match_backfills_link()
    test_low_confidence_marked_needs_review()
    test_idempotency_second_run_is_noop()
    test_go_forward_wrapper_not_touched()
    test_needs_review_wrappers_skipped_on_rerun()
    test_clean_workspace_writes_noop_marker()
    test_missing_events_file_safe()
    test_canonical_commitment_type_is_a_candidate_source()
    test_bug80_malformed_lines_quarantined_not_dropped()
    print()
    print("OK — all 9 source_event_seq backfill tests passed.")


if __name__ == "__main__":
    main()
