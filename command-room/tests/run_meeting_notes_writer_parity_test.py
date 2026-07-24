#!/usr/bin/env python3
"""
Meeting-notes writer parity (v4.5.2 C2 — F-46 P1/P2a/P2b, F-50 P2a).

Exercises shared/scripts/meeting_capture.py against a real-shape workspace
copy. The fixture events below MIRROR the exact events meeting-notes wrote on
2026-07-08 (FINDINGS_M_v451 F-46, live seq 3588-3594: one `meeting` with a
tz-offset start time + six Stage-D commitments, names swapped) — the state in
which the chat claimed "Decisions logged (3)" while the substrate held zero
decision events. Regresses:

  - the F-46 repro: after the real writes, the claim audit reads decision=0,
    so a "3 decisions logged" claim is refuted from disk (verify_claims);
  - decision / person_proposal / meeting_processed builders produce events
    that pass the strict append gate + payload schema (parity with the
    past-meetings writer contract);
  - person_proposal carries pending_review unconditionally; low-confidence
    decisions get pending_review forced on (safety inversion);
  - meeting_processed is the already-processed marker, matched across the
    bare-id vs granola:-prefixed source_ref drift observed live (F-50 window);
  - the F-50 P2a repro: claiming one more decision than is on disk fails the
    claim audit;
  - SKILL.md structural guards: decision writes are no longer gated to deep
    mode, and the new mandatory contract blocks exist.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from output_exercise_lib import copy_fixture  # noqa: E402
import meeting_capture as mc  # noqa: E402
from event_gate import append_event, EventGateError  # noqa: E402
from event_payload_check import check_payload  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


# The F-46 meeting, real field set (live seq 3588; names/ids swapped to the
# fixture roster, uuid regenerated). Note the tz-OFFSET meeting-start ts and
# `due: ""` on undated items — both are the real shapes, keep them.
F46_REF = "granola:a64c4b14-0000-4dee-b192-cc156153e845"

F46_MEETING = {
    "type": "meeting",
    "ts": "2026-07-08T08:00:00-07:00",
    "source_skill": "meeting-notes",
    "primary_thread_id": "project_001",
    "related_thread_ids": [],
    "cross_ref_reason": None,
    "classification_confidence": 0.95,
    "person_ids": ["person_001", "person_002", "person_005"],
    "data": {
        "title": "State of department reporting — dashboard setup and weekly analysis with Lee",
        "source_ref": F46_REF,
        "duration_min": 60,
        "brief_path": "_hq/meetings/Past_Meeting_lee-sod-dashboard-setup_2026-07-08.docx",
        "attendees_external": ["Lee"],
    },
}


def _f46_commitment(kind: str, owner: str, title: str, due: str, person_ids: list) -> dict:
    return {
        "type": "commitment",
        "source_skill": "meeting-notes",
        "primary_thread_id": "project_001",
        "related_thread_ids": [],
        "classification_confidence": 0.95,
        "person_ids": person_ids,
        "data": {
            "kind": kind,
            "owner_id": owner,
            "title": title,
            "due": due,
            "status": "open",
            "summary": title,
            "source_ref": F46_REF,
        },
    }


F46_COMMITMENTS = [
    _f46_commitment("promise", "person_002",
                    "set up dedicated shared folder for weekly SOD submissions",
                    "2026-07-20", ["person_002", "person_001"]),  # DATE_GUARD_OK: writer-parity pass-through data; status is fixture-set, not derived
    _f46_commitment("task", "person_002",
                    "refine SOD dashboard and summary format", "", ["person_002"]),
    _f46_commitment("task", "person_002",
                    "record all 1:1s and use voice prep as a standing habit", "", ["person_002"]),
    _f46_commitment("task", "person_002",
                    "upload culture-index profiles for direct reports", "", ["person_002"]),
    _f46_commitment("promise", "person_001",
                    "wire weekly scheduled task to pull the SOD from the shared folder",
                    "", ["person_001", "person_002"]),
    _f46_commitment("scheduling", "person_001",
                    "next working session — Tue Jul 21, 8:00 AM PT",
                    "2026-07-21", ["person_001", "person_002"]),  # DATE_GUARD_OK: writer-parity pass-through data; status is fixture-set, not derived
]


def main() -> int:
    ws = copy_fixture()
    events_path = ws / "_hq" / "data" / "events.jsonl"

    # --- Replay the F-46 disk state: meeting + 6 commitments, zero decisions.
    append_event(events_path, [F46_MEETING] + F46_COMMITMENTS,
                 holder="parity-test.f46-replay")

    counts = mc.count_meeting_writes(ws, F46_REF)
    check("F-46 replay: meeting event on disk", counts.get("meeting") == 1,
          f"counts={counts}")
    check("F-46 replay: 6 commitments on disk", counts.get("commitment") == 6,
          f"counts={counts}")
    check("F-46 replay: ZERO decision events on disk", counts.get("decision") == 0,
          f"counts={counts}")

    # The exact F-46 lie: chat said "Decisions logged (3)". The claim audit
    # must refute it from disk.
    audit = mc.verify_claims(ws, F46_REF, {"decision": 3, "commitment": 6})
    check("F-46 claim 'Decisions logged (3)' fails the audit", not audit["ok"],
          json.dumps(audit))
    check("F-46 audit names the decision mismatch",
          any(m["type"] == "decision" and m["claimed"] == 3 and m["on_disk"] == 0
              for m in audit["mismatches"]),
          json.dumps(audit["mismatches"]))

    # No receipt yet either (F-46 P2a) — already_processed must be False.
    check("F-46 replay: no meeting_processed receipt yet",
          not mc.already_processed(ws, F46_REF))

    # --- Close the gap: write the 3 decisions the run actually extracted.
    decisions = [
        mc.build_decision_event(
            s,
            source_ref=F46_REF,
            primary_thread_id="project_001",
            person_ids=["person_001", "person_002"],
            project_id="project_001",
            evidence=f"transcript: {s}",
            source_event_seq=F46_MEETING.get("seq"),
            confidence=0.9,
        )
        for s in (
            "weekly SOD report lands in the shared folder Mondays; dashboard generates Tuesdays",
            "dashboard weighting locked by department, formatting tuned via natural language",
            "all 1:1s recorded as a standing habit",
        )
    ]
    for d in decisions:
        check(f"decision builder passes payload schema ({d['data']['summary'][:30]}…)",
              check_payload(d) == [], str(check_payload(d)))
    try:
        append_event(events_path, decisions, holder="parity-test.decisions")
        gate_ok = True
    except EventGateError as e:
        gate_ok = False
        print(f"        gate error: {e}")
    check("decision events pass the strict append gate", gate_ok)

    counts = mc.count_meeting_writes(ws, F46_REF)
    check("3 decision events now on disk", counts.get("decision") == 3,
          f"counts={counts}")
    audit = mc.verify_claims(ws, F46_REF, {"decision": 3, "commitment": 6})
    check("honest claim now passes the audit", audit["ok"], json.dumps(audit))

    # F-50 P2a: claiming one more than written must fail.
    audit7 = mc.verify_claims(ws, F46_REF, {"decision": 4})
    check("F-50 repro: over-claim (4 vs 3) fails the audit", not audit7["ok"],
          json.dumps(audit7))

    # --- Safety inversion: low confidence forces pending_review on decisions.
    low = mc.build_decision_event(
        "ambiguous decider decision", source_ref=F46_REF, confidence=0.5)
    check("decision below 0.75 confidence forced pending_review",
          low["data"].get("pending_review") is True and low["data"]["committed"] is False,
          json.dumps(low["data"]))
    high = mc.build_decision_event(
        "clear decision", source_ref=F46_REF, confidence=0.9)
    check("decision at high confidence stays committed",
          "pending_review" not in high["data"] and high["data"]["committed"] is True,
          json.dumps(high["data"]))

    # --- person_proposal (F-46 P2b): four new names surfaced chat-only live;
    # here they land as pending-review events.
    proposals = [
        mc.build_person_proposal_event(
            n,
            source_ref=F46_REF,
            primary_thread_id="project_001",
            inferred_role=r,
            evidence=f"transcript mention: {n}",
            review_reason="New person mentioned; no entity record yet.",
            confidence=0.7,
        )
        for n, r in (
            ("Casey North", "marketing coordinator"),
            ("Casey Fowler", None),
            ("Jordan Vale", "IT lead"),
            ("Rick", None),
        )
    ]
    check("every person_proposal carries pending_review unconditionally",
          all(p["data"]["pending_review"] is True for p in proposals))
    check("person_proposal carries confidence",
          all(p["data"]["confidence"] == 0.7 for p in proposals))
    try:
        append_event(events_path, proposals, holder="parity-test.proposals")
        gate_ok = True
    except EventGateError as e:
        gate_ok = False
        print(f"        gate error: {e}")
    check("person_proposal events pass the strict append gate", gate_ok)

    # --- WG1-B D-B3 writer-side org gate: a "person" name that resolves to a
    # tracked org comes back as an org_proposal; a real person is untouched;
    # omitting workspace_root keeps every legacy call byte-identical.
    ent_path = ws / "_hq" / "data" / "entities.json"
    ent = json.loads(ent_path.read_text(encoding="utf-8"))
    holder = ent.get("entities") if isinstance(ent.get("entities"), dict) \
        else ent
    holder.setdefault("orgs", []).append(
        {"id": "org_900", "canonical_name": "Vertex Range (AcademyCo)"})
    ent_path.write_text(json.dumps(ent), encoding="utf-8")
    org_shaped = mc.build_person_proposal_event(
        "Vertex Range (AcademyCo)", source_ref=F46_REF,
        evidence="Named repeatedly as a partnership channel.",
        workspace_root=ws)
    check("D-B3: an on-file org name is refused as a person — org_proposal "
          "returned", org_shaped["type"] == "org_proposal",
          json.dumps(org_shaped))
    check("D-B3: the org-path event keeps the signal + source_ref",
          org_shaped["data"]["name"] == "Vertex Range (AcademyCo)"
          and org_shaped["data"]["source_ref"] == F46_REF
          and org_shaped["data"]["pending_review"] is True
          and "partnership channel" in org_shaped["data"]["signal"],
          json.dumps(org_shaped["data"]))
    person_shaped = mc.build_person_proposal_event(
        "Casey North", source_ref=F46_REF, workspace_root=ws)
    check("D-B3: a non-org name stays a person_proposal with the gate armed",
          person_shaped["type"] == "person_proposal")
    legacy = mc.build_person_proposal_event(
        "Vertex Range (AcademyCo)", source_ref=F46_REF)
    check("D-B3: omitting workspace_root is byte-identical legacy behavior",
          legacy["type"] == "person_proposal")

    # --- meeting_processed receipt (F-46 P2a) + already-processed detector.
    receipt = mc.build_meeting_processed_event(
        "a64c4b14-0000-4dee-b192-cc156153e845",
        source_ref=F46_REF,
        primary_thread_id="project_001",
        extracted_count=13,           # 6 commitments + 3 decisions + 4 proposals
        pending_review_count=4,
        brief_path="_hq/meetings/Past_Meeting_lee-sod-dashboard-setup_2026-07-08.docx",
    )
    check("meeting_processed builder passes payload schema",
          check_payload(receipt) == [], str(check_payload(receipt)))
    append_event(events_path, [receipt], holder="parity-test.receipt")
    check("already_processed flips True after the receipt",
          mc.already_processed(ws, F46_REF))

    # Live drift (F-50 window): meeting_id written BARE, source_ref prefixed.
    # The detector must match either spelling.
    check("already_processed matches the bare meeting id",
          mc.already_processed(ws, "a64c4b14-0000-4dee-b192-cc156153e845"))
    bare_receipt = mc.build_meeting_processed_event(
        "8817ba95-0000-4538-b6ee-26044a3aabe6")  # source_ref auto-derived
    check("bare-id builder derives the granola: source_ref",
          bare_receipt["data"]["source_ref"] == "granola:8817ba95-0000-4538-b6ee-26044a3aabe6")
    append_event(events_path, [bare_receipt], holder="parity-test.receipt2")
    check("prefixed lookup matches a bare-id receipt",
          mc.already_processed(ws, "granola:8817ba95-0000-4538-b6ee-26044a3aabe6"))

    # Full parity snapshot: the write set now matches what past-meetings
    # leaves for one processed meeting.
    counts = mc.count_meeting_writes(ws, F46_REF)
    check("parity write-set complete (meeting/receipt/decisions/commitments/proposals)",
          counts.get("meeting") == 1 and counts.get("meeting_processed") == 1
          and counts.get("decision") == 3 and counts.get("commitment") == 6
          and counts.get("person_proposal") == 4,
          f"counts={counts}")

    # --- Fixture integrity: still parses, seq strictly monotonic.
    seqs = []
    parse_ok = True
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            parse_ok = False
            break
        if isinstance(ev.get("seq"), int):
            seqs.append(ev["seq"])
    check("events.jsonl still parses after all writes", parse_ok)
    check("seq stays strictly monotonic", seqs == sorted(seqs) and len(set(seqs)) == len(seqs))

    # --- SKILL.md structural guards (the prose IS the runtime for this skill).
    skill = (ROOT / "skills" / "meeting-notes" / "SKILL.md").read_text(encoding="utf-8")
    check("SKILL.md no longer lists the decision log as skipped in light mode",
          "**Skipped in light mode:** Decision log" not in skill)
    check("SKILL.md Step 5b is mandatory in both modes",
          "Log Decisions (MANDATORY in both modes" in skill)
    check("SKILL.md carries the person_proposal step (5f)",
          "Step 5f: Person Proposals" in skill)
    check("SKILL.md carries the meeting_processed receipt step (9a2)",
          "meeting_processed` receipt" in skill and "Step 9a2" in skill)
    check("SKILL.md carries the claim-audit step (9a3)",
          "Step 9a3" in skill and "count_meeting_writes" in skill)
    check("SKILL.md carries the pending_review safety inversion",
          "pending_review is default-on for low-confidence attribution" in skill)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
