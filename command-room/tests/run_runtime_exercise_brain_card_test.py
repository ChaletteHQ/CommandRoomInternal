#!/usr/bin/env python3
"""Runtime exercise — the Living Brain card end-to-end over the synthetic
workspace fixture (SPEC LB1 §6). Extends a COPY of tests/fixtures/
workspace_mini with open proposals + a narrated auto-close, then drives the
exact code paths the surfaces are instructed to call:

  brief:        select_confirm_card("morning-brief") + changes_since(...)
  apply-choices: resolve_proposal (the cr-brain dispatch)
  undo:         brain_undo.undo_batch over the narrated sent_reconcile batch

Asserting the render inputs come from the PROJECTOR (not freelance reads),
tombstones land, the ledger row lands, and undo reverses additively. Dates
relative to today; placeholder names only (the fixture's own)."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / "shared" / "scripts"))

from output_exercise_lib import copy_fixture  # noqa: E402
import brain_proposals as bp  # noqa: E402
import brain_undo as bu  # noqa: E402
import change_feed as cf  # noqa: E402
import event_gate  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)
ws = Path(copy_fixture())
events_path = ws / "_hq" / "data" / "events.jsonl"

# --- seed through the REAL gate: a commitment, its narrated auto-close, and
# --- the audit event (the reconcile-sent shipped shape) -----------------------
event_gate.append_event(events_path, [
    {"type": "commitment", "source_skill": "meeting-notes",
     "data": {"id": "cmt_lb1runtime01", "title": "send the revised draft",
              "kind": "promise"}},
    {"type": "commitment_resolved", "source_skill": "reconcile-sent",
     "data": {"commitment_id": "cmt_lb1runtime01",
              "resolved_by": "sent_reconcile",
              "evidence": "matched an outbound send", "resolution": "done"}},
    {"type": "sent_reconcile", "source_skill": "reconcile-sent",
     "data": {"task_id": "reconcile-sent", "kind": "reconcile-sent",
              "status": "complete", "fired_via": "scheduled",
              "cursor_from": _iso(NOW - timedelta(days=1)),
              "cursor_to": _iso(NOW), "sent_scanned_count": 4,
              "n_closed": 1, "n_pending": 0}},
], holder="lb1-runtime-test")

# --- seed open proposals through the REAL writer API --------------------------
r1 = bp.propose(ws, kind="deal_update", fingerprint="deal:rt:stage",
                evidence="observed stage language",
                action_tuples=[{"action": "confirm proposal"},
                               {"action": "dismiss proposal"},
                               {"action": "snooze proposal 7d"}],
                tier="confirm", detector="deal-signals",
                thread_id="project_001",
                extra={"proposal_kind": "stage", "proposed_stage": "negotiating"})
check(r1["status"] == "proposed", "runtime seed proposal landed via propose()")

# --- the brief's exact calls (default now — the instruction shape) -----------
since = _iso(NOW - timedelta(days=1))
feed = cf.changes_since(ws, since)
texts = [l["text"] for l in feed["lines"]]
check(any("Closed 1 commitment" in t and "`undo`" in t for t in texts),
      "CHANGED slot narrates the auto-close with the undo affordance")
audit_seq = feed["lines"][0]["refs"][0]
check(isinstance(audit_seq, int), "narration is traceable to the audit seq")

card = bp.select_confirm_card(ws, "morning-brief")
check(len(card["items"]) >= 1, "the card renders from the projector")
item = next(i for i in card["items"] if i["id"] == r1["proposal_id"])
check(item["action_tuples"][0]["action"] == "confirm proposal",
      "card item carries its registered one-tap verbs")
check(item["thread_id"] == "project_001",
      "target id embedded verbatim (F2 identity rule)")

# fixture integrity: every event still gate-valid (schema-evolution tripwire)
from output_exercise_lib import validate_event  # noqa: E402
for line in events_path.read_text(encoding="utf-8").splitlines():
    if line.strip():
        problems = validate_event(json.loads(line))
        check(not problems, f"fixture event stays schema-valid: {problems}")

# --- apply-choices' exact dispatch (a decline) --------------------------------
res = bp.resolve_proposal(ws, r1["proposal_id"], "declined",
                          resolved_by="person_001",
                          source_skill="apply-choices")
check(res["status"] == "resolved", "cr-brain dispatch resolves the tuple")
check(all(i["id"] != r1["proposal_id"]
          for i in bp.load_open_proposals(ws)),
      "resolved proposal leaves every surface's queue")
ledger_rows = (ws / "_hq" / "data" / "proposal_feedback.jsonl").read_text(
    encoding="utf-8").splitlines()
check(any(json.loads(x)["fingerprint"] == "deal:rt:stage" for x in ledger_rows),
      "the decision landed in the shared cooldown ledger")

# --- the narrated batch's undo --------------------------------------------------
before = events_path.read_text(encoding="utf-8").splitlines()
result = bu.undo_batch(ws, {"kind": "sent_reconcile", "seq": audit_seq},
                       undone_by="person_001", source_skill="apply-choices")
check(result["status"] == "undone" and result["n_undone"] == 1,
      "undo reverses the narrated auto-close")
after = events_path.read_text(encoding="utf-8").splitlines()
check(after[:len(before)] == before and len(after) == len(before) + 2,
      "undo appended exactly reopen + marker; nothing edited")
reopen = json.loads(after[-2])
marker = json.loads(after[-1])
check(reopen["type"] == "commitment_reopened"
      and reopen["data"]["commitment_id"] == "cmt_lb1runtime01",
      "additive commitment_reopened for the exact commitment")
check(marker["type"] == "brain_change_undone"
      and marker["data"]["reverser"] == "commitment_close",
      "brain_change_undone marker written")
feed2 = cf.changes_since(ws, since)
check(feed2["counts"]["changes_undone"] == 1,
      "the next feed narrates the undo")

print(f"OK — {PASS} checks passed")
