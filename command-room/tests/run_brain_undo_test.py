#!/usr/bin/env python3
"""Tests for brain_undo — the Living Brain reverser registry + batch undo
(SPEC LB1 D5, R1). All undo must be ADDITIVE (reversing events appended
through the class's single writer; history never edited). Fixtures mirror
real substrate shapes; dates relative to today; placeholder names only."""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import brain_undo as bu  # noqa: E402
import brain_proposals as bp  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)


def _ws():
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {"version": 1, "people": [
        {"id": "person_001", "canonical_name": "Sam Sample", "status": "active",
         "first_seen": "2026-01-05"},
    ], "orgs": [], "threads": [], "engagements": []}
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    (data / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _raw_append(ws, rows):
    path = ws / "_hq" / "data" / "events.jsonl"
    existing = path.read_text(encoding="utf-8")
    seq = existing.count("\n")
    lines = []
    for r in rows:
        seq += 1
        r.setdefault("seq", seq)
        lines.append(json.dumps(r))
    path.write_text(existing + "".join(l + "\n" for l in lines),
                    encoding="utf-8")


def _events(ws, etype=None):
    out = []
    for line in (ws / "_hq" / "data" / "events.jsonl").read_text(
            encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if etype is None or ev.get("type") == etype:
            out.append(ev)
    return out


# --- registry shape + the D2 legality hook ----------------------------------
check(bu.has_reverser("commitment_close"), "commitment_close reverser registered")
check(bu.has_reverser("chat_dismissal"), "chat_dismissal reverser registered")
check(bu.has_reverser("person_org_creation_structured_fact"),
      "R1 archive reverser registered")
check(not bu.has_reverser("entity_merge"),
      "merges have NO reverser (they stay confirm forever)")
for cls, entry in bu.REVERSERS.items():
    check(callable(entry.get("reverse")), f"{cls} reverser is callable")
    check(bool(entry.get("reverses_via")), f"{cls} names its reversing event")
# Every AUTO_ALLOWED class must have a reverser (D2 both halves agree)
for cls in bp.AUTO_ALLOWED:
    check(bu.has_reverser(cls), f"AUTO_ALLOWED class {cls} has a reverser")

# --- sent_reconcile batch resolution + undo (the shipped precedent) ----------
ws = _ws()
t0 = NOW - timedelta(hours=6)
_raw_append(ws, [
    # a prior reconcile run (its closures must NOT be in this batch)
    {"ts": _iso(t0 - timedelta(days=1)), "type": "commitment_resolved",
     "source_skill": "reconcile-sent",
     "data": {"commitment_id": "cmt_OLD000000001", "resolved_by": "sent_reconcile",
              "evidence": "matched an outbound send", "resolution": "done"}},
    {"ts": _iso(t0 - timedelta(days=1)), "type": "sent_reconcile",
     "source_skill": "reconcile-sent",
     "data": {"task_id": "reconcile-sent", "kind": "reconcile-sent",
              "status": "complete", "fired_via": "scheduled",
              "cursor_from": _iso(t0 - timedelta(days=2)),
              "cursor_to": _iso(t0 - timedelta(days=1)),
              "sent_scanned_count": 3, "n_closed": 1, "n_pending": 0}},
    # THIS run's closures (real shapes: one id-keyed, one via close path)
    {"ts": _iso(t0), "type": "commitment_resolved",
     "source_skill": "reconcile-sent",
     "data": {"commitment_id": "cmt_01AAAAAAAAAA", "resolved_by": "sent_reconcile",
              "evidence": "matched an outbound send", "resolution": "done"}},
    {"ts": _iso(t0), "type": "commitment_resolved",
     "source_skill": "reconcile-sent",
     "data": {"commitment_id": "cmt_01BBBBBBBBBB", "resolved_by": "sent_reconcile",
              "evidence": "matched an outbound send", "resolution": "done"}},
    # an unrelated manual close in the same window — NOT part of the batch
    {"ts": _iso(t0), "type": "commitment_resolved", "source_skill": "commitments",
     "data": {"commitment_id": "cmt_01CCCCCCCCCC", "resolved_by": "person_001",
              "evidence": "done in triage", "resolution": "done"}},
    {"ts": _iso(t0), "type": "sent_reconcile", "source_skill": "reconcile-sent",
     "data": {"task_id": "reconcile-sent", "kind": "reconcile-sent",
              "status": "complete", "fired_via": "scheduled",
              "cursor_from": _iso(t0 - timedelta(days=1)), "cursor_to": _iso(t0),
              "sent_scanned_count": 5, "n_closed": 2, "n_pending": 0}},
])
audit_seq = _events(ws, "sent_reconcile")[-1]["seq"]
changes = bu.resolve_batch(ws, {"kind": "sent_reconcile", "seq": audit_seq})
ids = sorted(c["commitment_id"] for c in changes)
check(ids == ["cmt_01AAAAAAAAAA", "cmt_01BBBBBBBBBB"],
      f"batch = exactly this run's sent_reconcile closures: {ids}")
check(all(c["change_class"] == "commitment_close" for c in changes),
      "sent_reconcile changes carry the commitment_close class")

try:
    bu.resolve_batch(ws, {"kind": "sent_reconcile", "seq": 99999})
    check(False, "unknown audit seq must raise")
except bu.BrainUndoError:
    check(True, "unresolvable sent_reconcile ref raises loudly")
try:
    bu.resolve_batch(ws, {"kind": "mystery", "batch_id": "x"})
    check(False, "unknown batch kind must raise")
except bu.BrainUndoError:
    check(True, "unknown batch kind raises loudly")

# The batch's commitments must exist as open-able commitments for the real
# reopen path — seed the matching commitment events (real shape).
_raw_append(ws, [])  # no-op keeps seq math obvious
before_lines = (ws / "_hq" / "data" / "events.jsonl").read_text(
    encoding="utf-8").splitlines()
_seed = [
    {"ts": _iso(t0 - timedelta(days=3)), "type": "commitment",
     "source_skill": "meeting-notes",
     "data": {"id": "cmt_01AAAAAAAAAA", "title": "send Sam the draft",
              "kind": "promise", "owner_id": "person_001"}},
    {"ts": _iso(t0 - timedelta(days=3)), "type": "commitment",
     "source_skill": "meeting-notes",
     "data": {"id": "cmt_01BBBBBBBBBB", "title": "send Quinn the numbers",
              "kind": "promise", "owner_id": "person_001"}},
]
_raw_append(ws, _seed)

result = bu.undo_batch(ws, {"kind": "sent_reconcile", "seq": audit_seq},
                       undone_by="person_001", source_skill="apply-choices")
check(result["status"] == "undone" and result["n_undone"] == 2,
      f"undo_batch reverses both closures: {result['status']}/{result['n_undone']}")
reopened = _events(ws, "commitment_reopened")
check(len(reopened) == 2, "two ADDITIVE commitment_reopened events")
markers = _events(ws, "brain_change_undone")
check(len(markers) == 2, "one brain_change_undone marker per reversal")
for m in markers:
    check(m["data"]["reverser"] == "commitment_close"
          and m["data"]["change_ref"].startswith("seq:"),
          "marker carries reverser + traceable change_ref")
# additive-only: every pre-undo line is byte-identical
after_lines = (ws / "_hq" / "data" / "events.jsonl").read_text(
    encoding="utf-8").splitlines()
check(after_lines[:len(before_lines)] == before_lines
      and len(after_lines) > len(before_lines),
      "undo appended only — no prior event edited or deleted")

# --- brain_batch resolution (LB2-ready: stamped change events) ---------------
ws = _ws()
_raw_append(ws, [
    {"ts": _iso(NOW - timedelta(hours=2)), "type": "person_created",
     "source_skill": "lb2-detector",
     "data": {"person_id": "person_001", "canonical_name": "Sam Sample",
              "brain_batch_id": "batch_abc", "brain_change_class":
              "person_org_creation_structured_fact"}},
])
changes = bu.resolve_batch(ws, {"kind": "brain_batch", "batch_id": "batch_abc"})
check(len(changes) == 1
      and changes[0]["change_class"] == "person_org_creation_structured_fact"
      and changes[0]["person_id"] == "person_001",
      "brain_batch resolution reads the stamped change events")
result = bu.undo_batch(ws, {"kind": "brain_batch", "batch_id": "batch_abc"},
                       undone_by="person_001", source_skill="apply-choices")
check(result["n_undone"] == 1, "R1 archive reverser runs")
upd = _events(ws, "person_updated")
check(len(upd) == 1, "archive flip is an ADDITIVE person_updated event")
ent = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
people = ent["people"] if isinstance(ent.get("people"), list) else \
    ent.get("entities", {}).get("people", [])
sam = next(p for p in people if p["id"] == "person_001")
check(sam.get("status") == "archived",
      "auto-created person is archived, never deleted")
check(len(_events(ws, "brain_change_undone")) == 1, "marker written")

# --- containment: an unknown class never aborts the batch --------------------
ws = _ws()
_raw_append(ws, [
    {"ts": _iso(NOW - timedelta(hours=1)), "type": "note",
     "source_skill": "x",
     "data": {"brain_batch_id": "b2", "brain_change_class": "no_such_class"}},
    {"ts": _iso(NOW - timedelta(hours=1)), "type": "person_created",
     "source_skill": "lb2-detector",
     "data": {"person_id": "person_001", "brain_batch_id": "b2",
              "brain_change_class": "person_org_creation_structured_fact"}},
])
result = bu.undo_batch(ws, {"kind": "brain_batch", "batch_id": "b2"},
                       undone_by="person_001", source_skill="apply-choices")
check(result["status"] == "partial" and result["n_undone"] == 1
      and result["n_errors"] == 1,
      "per-item failure is contained; the batch never aborts")

print(f"OK — {PASS} checks passed")
