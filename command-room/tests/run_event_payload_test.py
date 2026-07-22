#!/usr/bin/env python3
"""SPEC EVT1 — per-type event payload validation tests."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import event_payload_check as epc  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


# Valid representative payloads per covered type (alias shapes included).
VALID = [
    {"type": "commitment", "data": {"title": "Send the deck", "due": "2026-06-01T00:00:00Z", "status": "open"}},
    {"type": "commitment", "data": {"title": "Send the deck", "due_date": "2026-06-01T00:00:00Z", "state": "open"}},  # alias shape (Sam 2026-05-17 class)
    {"type": "commitment_resolved", "data": {"commitment_id": "c1"}},
    {"type": "commitment_resolved", "data": {"target_id": "c7"}},  # closer alias
    {"type": "commitment_superseded", "data": {"commitment_id": "c1", "superseded_by": "c2", "resolution": "duplicate", "merged_source_refs": ["gmail:g1"]}},  # C4 merge closer
    {"type": "decision", "data": {"decision": "Picked vendor A"}},
    {"type": "decision", "data": {"summary": "Picked vendor A"}},  # alias
    {"type": "meeting", "data": {"title": "Acme sync"}},
    {"type": "meeting_processed", "data": {"meeting_id": "m1", "summary": "covered pricing"}},
    {"type": "interaction", "data": {"source_ref": "gmail:abc", "direction": "inbound"}},
    {"type": "email_drafted", "data": {"recipient": "a@example.com", "draft_event_seq": 5}},
    {"type": "email_sent", "data": {"recipient": "a@example.com", "gmail_thread_id": "t1", "draft_event_seq": 5}},
    {"type": "pack_run", "data": {"task_id": "morning-brief"}},
    {"type": "pattern_break_detected", "data": {"person_id": "person_4", "eligible_event_count": 12}},
    {"type": "reminder", "data": {"id": "rem_1", "summary": "renew filing", "remind_from": "2026-07-10", "personal": True, "origin": "user_explicit"}},
    {"type": "reminder_updated", "data": {"reminder_id": "rem_1", "action": "push", "remind_from": "2026-07-20", "origin": "user_explicit"}},  # DATE_GUARD_OK: payload contract shape only; no clock comparison
    {"type": "reminder_cleared", "data": {"reminder_id": "rem_1", "origin": "user_explicit"}},
    {"type": "commitment_observed", "data": {"title": "Stacy to send Rick the report", "source_ref": "granola:m9", "tier": "observed", "observed_reason": "between other people", "id": "obs_abc123def456"}},
    {"type": "visual_gate", "data": {"doc": "_hq/meetings/CallPrep_2026-07-10.docx", "rendered": True, "findings": ["empty or placeholder tile"], "fixed": True}},
    {"type": "visual_gate", "data": {"doc": "_hq/meetings/CallPrep_2026-07-10.docx", "rendered": False, "findings": [], "fixed": False, "skipped_reason": "no renderer on this machine"}},  # skipped-path shape
    {"type": "deal_created", "data": {"thread_id": "project_017", "org_id": "org_acme", "stage": "lead", "value": 40000}},  # PIPE1
    {"type": "deal_created", "data": {"thread_id": "project_018", "org_id": "org_acme", "stage": "qualified", "adopted": True}},  # PIPE1 adoption shape (no value — never estimated)
    {"type": "deal_updated", "data": {"thread_id": "project_017", "expected_close": "2026-08-15"}},  # DATE_GUARD_OK: payload contract shape only; no clock comparison
    {"type": "deal_stage_changed", "data": {"thread_id": "project_017", "from_stage": "lead", "to_stage": "proposal_sent"}},
    {"type": "deal_won", "data": {"thread_id": "project_017", "org_id": "org_acme", "value": 40000, "converted_prospect": True}},
    {"type": "deal_lost", "data": {"thread_id": "project_017", "org_id": "org_acme", "loss_reason": "price", "loss_note": "budget cut"}},
    {"type": "deal_update_proposed", "data": {"thread_id": "project_017", "proposal_kind": "stage", "proposed_stage": "proposal_sent", "evidence": "sent the proposal over"}},  # Part 2, registered up front
    {"type": "deal_update_dismissed", "data": {"thread_id": "project_017", "fingerprint": "abc123"}},  # Part 2, registered up front
    {"type": "chart_render", "data": {"catalog_id": "pipeline_by_org", "kind": "bar", "org_id": "org_acme", "artifact": "_hq/deliverables/Chart_2026-07-19.html", "refused": False}},  # OUT3B rendered  # DATE_GUARD_OK: deliverable filename in a payload-shape fixture; never compared to any clock
    {"type": "chart_render", "data": {"catalog_id": "value_trend", "kind": "line", "refused": True, "reason": "only 1 month of data — a trend needs 2+ points"}},  # OUT3B refusal (D4)
    {"type": "some_unconstrained_type", "data": {"anything": True}},  # no schema -> pass
]


def test_valid_payloads_pass():
    allok = True
    for ev in VALID:
        v = epc.check_payload(ev)
        if v:
            allok = False
            print("   unexpected violation:", ev["type"], v)
    check("all representative + alias payloads pass", allok)


def test_missing_required_fails_with_named_key():
    v = epc.check_payload({"type": "email_sent", "data": {"topic": "hi"}})  # no recipient
    check("missing required names the key", any("recipient" in x for x in v))
    v2 = epc.check_payload({"type": "pack_run", "data": {}})
    check("pack_run missing task_id flagged", any("task_id" in x for x in v2))
    v3 = epc.check_payload({"type": "commitment_resolved", "data": {"nope": 1}})
    check("commitment_resolved missing closer-id flagged", any("one of" in x for x in v3))


def test_type_mismatch_flagged():
    v = epc.check_payload({"type": "email_drafted", "data": {"recipient": "a@example.com", "draft_event_seq": "five"}})
    check("wrong-typed property flagged", any("draft_event_seq" in x for x in v))


def test_coverage_of_load_bearing_types():
    types = set(epc.covered_types())
    # 10 original load-bearing types + the Phase 2 Stage D commitment-family
    # additions (commitment_reclassified / commitment_reopened) + the v4.5.2
    # S1 per-brief prep receipt (prep_brief) + the v4.6.0 W4a reminder lane
    # (reminder / reminder_updated / reminder_cleared) + the v4.6.0 C4 merge
    # closer (commitment_superseded) + the v4.6.0 S4 lifecycle verbs
    # (commitment_updated wording/due shapes, commitment_reassigned,
    # chat_dismissal_cleared) + the v4.6.1 W4b proposal tombstone
    # (person_proposal_resolved) + the v4.6.1 W4c observed tier
    # (commitment_observed) + the OUT2 §3 render-then-critique audit
    # (visual_gate) + the PIPE1 deal lane (deal_created / deal_updated /
    # deal_stage_changed / deal_won / deal_lost, written only by
    # deal_state.py, plus the two Part 2 detector types registered up
    # front) + the LB1 Living Brain lane (brain_proposal /
    # brain_proposal_resolved / brain_proposal_expired / brain_change_undone,
    # written only by brain_proposals.py / brain_undo.py / the cleanup expiry
    # sweep) + the HIST1 entity-history lane (person_role_changed /
    # person_org_changed auto-emitted by people_writer.update_person,
    # person_fact_observed / org_fact_observed via the record_*_fact writers,
    # entity_fact_retracted registered up front — its Part 2 brain_undo
    # reverser is the writer) + the PID1 people-identity lane
    # (unidentified_attendee_observed via meeting_capture.
    # build_unidentified_attendee_event; identity_reconcile_run — the
    # reconciler's per-pass receipt, written only via receipts.log_receipt)
    # + the OBJ1 objective lane (objective_created / objective_updated /
    # objective_review / objective_report / objective_completed /
    # objective_archived, written only by objective_state.py)
    # - each registered per EVENT_TYPES.md with named consumers.
    expected = {"commitment", "commitment_resolved", "decision", "meeting", "meeting_processed",
                "interaction", "email_drafted", "email_sent", "pack_run", "pattern_break_detected",
                "commitment_reclassified", "commitment_reopened", "prep_brief",
                "reminder", "reminder_updated", "reminder_cleared", "commitment_superseded",
                "commitment_updated", "commitment_reassigned", "chat_dismissal_cleared",
                "person_proposal_resolved", "commitment_observed", "visual_gate",
                "deal_created", "deal_updated", "deal_stage_changed",
                "deal_won", "deal_lost", "deal_update_proposed",
                "deal_update_dismissed",
                "brain_proposal", "brain_proposal_resolved",
                "brain_proposal_expired", "brain_change_undone",
                "person_role_changed", "person_org_changed",
                "person_fact_observed", "org_fact_observed",
                "entity_fact_retracted",
                "unidentified_attendee_observed", "identity_reconcile_run",
                # SPEC OUT3B (2026-07-19) — the on-demand chart lane, written
                # only by the chart-on-demand skill (catalog_id / kind / refused).
                "chart_render",
                # SPEC OBJ1 (DRAFT) — the objective lane, written only by objective_state.py.
                "objective_created", "objective_updated", "objective_review",
                "objective_report", "objective_completed",
                "objective_archived"}
    check("exactly the 48 load-bearing types are covered", types == expected)


def test_warn_only_hook_never_blocks():
    from atomic_write import atomic_append_jsonl
    ws = Path(tempfile.mkdtemp(prefix="evt1_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    ep = ws / "_hq" / "data" / "events.jsonl"
    # An invalid payload (email_sent with no recipient) must STILL append.
    atomic_append_jsonl(ep, [{"type": "email_sent", "data": {"topic": "no recipient here"}}])
    lines = [l for l in ep.read_text(encoding="utf-8").splitlines() if l.strip()]
    check("invalid payload still appended (warn-only, never blocks)", len(lines) == 1)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_valid_payloads_pass()
    test_missing_required_fails_with_named_key()
    test_type_mismatch_flagged()
    test_coverage_of_load_bearing_types()
    test_warn_only_hook_never_blocks()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL event_payload tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
