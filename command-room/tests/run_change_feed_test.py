#!/usr/bin/env python3
"""Tests for change_feed — the Living Brain narration READER (SPEC LB1 D6).
Hand-computed vectors over real-shape audit events. The feed only reads:
every line must be traceable (refs) to its audit event; drop-empty
throughout; dates relative to today. Placeholder names only."""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import change_feed as cf  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)
SINCE = NOW - timedelta(days=1)


def _ws(rows):
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    lines = []
    for i, r in enumerate(rows, 1):
        r.setdefault("seq", i)
        lines.append(json.dumps(r))
    (data / "events.jsonl").write_text(
        "".join(l + "\n" for l in lines), encoding="utf-8")
    return d


# --- empty window → empty lines (drop-empty) ---------------------------------
ws = _ws([])
feed = cf.changes_since(ws, _iso(SINCE))
check(feed["lines"] == [], "empty substrate → no lines")

# --- full aggregation vector --------------------------------------------------
in_win = _iso(NOW - timedelta(hours=6))
out_win = _iso(NOW - timedelta(days=3))
rows = [
    # sent_reconcile inside the window (real receipt shape)
    {"ts": in_win, "type": "sent_reconcile", "source_skill": "reconcile-sent",
     "data": {"task_id": "reconcile-sent", "kind": "reconcile-sent",
              "status": "complete", "fired_via": "scheduled",
              "cursor_from": out_win, "cursor_to": in_win,
              "sent_scanned_count": 9, "n_closed": 3, "n_pending": 1,
              "n_opened": 1}},
    # one OUTSIDE the window — must not count
    {"ts": out_win, "type": "sent_reconcile", "source_skill": "reconcile-sent",
     "data": {"task_id": "reconcile-sent", "status": "complete",
              "cursor_from": out_win, "cursor_to": out_win,
              "sent_scanned_count": 4, "n_closed": 2, "n_pending": 0}},
    # a zero-closure run — drop-empty (no line, no ref)
    {"ts": in_win, "type": "sent_reconcile", "source_skill": "reconcile-sent",
     "data": {"task_id": "reconcile-sent", "status": "complete",
              "cursor_from": in_win, "cursor_to": in_win,
              "sent_scanned_count": 0, "n_closed": 0, "n_pending": 0}},
    {"ts": in_win, "type": "session_sweep_run", "source_skill": "session-sweep",
     "data": {"sessions_scanned": 4, "events_recovered": 2}},
    {"ts": in_win, "type": "cleanup_run", "source_skill": "cleanup",
     "data": {"status": "complete"}},
    {"ts": in_win, "type": "maintenance_run", "source_skill": "maintenance",
     "data": {"fired_at_slot": "17:45", "jobs_due": ["cleanup"],
              "jobs_completed": ["cleanup"], "jobs_failed": [],
              "skipped_disabled": []}},
    {"ts": in_win, "type": "brain_proposal", "source_skill": "deal-signals",
     "data": {"proposal_id": "bp_aaa", "kind": "deal_update",
              "fingerprint": "f1", "tier": "confirm", "detector": "deal-signals"}},
    {"ts": in_win, "type": "brain_proposal_resolved", "source_skill": "apply-choices",
     "data": {"proposal_id": "bp_bbb", "user_action": "applied"}},
    {"ts": in_win, "type": "brain_proposal_resolved", "source_skill": "apply-choices",
     "data": {"proposal_id": "bp_ccc", "user_action": "declined"}},
    {"ts": in_win, "type": "brain_proposal_expired", "source_skill": "cleanup",
     "data": {"proposal_id": "bp_ddd"}},
    {"ts": in_win, "type": "brain_change_undone", "source_skill": "apply-choices",
     "data": {"change_ref": "seq:2", "reverser": "commitment_close"}},
]
ws = _ws(rows)
feed = cf.changes_since(ws, _iso(SINCE))
c = feed["counts"]
check(c["closed_from_sent"] == 3, "in-window closures counted, stale run excluded")
check(c["opened_from_sent"] == 1, "opened-from-sent counted")
check(c["swept"] == 2, "sweep recoveries counted")
check(c["cleanup_runs"] == 1 and c["maintenance_jobs"] == 1,
      "housekeeping counted")
check(c["new_proposals"] == 1 and c["proposals_resolved"] == 1
      and c["proposals_declined"] == 1 and c["proposals_expired"] == 1
      and c["changes_undone"] == 1, "proposal lifecycle counted")

texts = [l["text"] for l in feed["lines"]]
check(any("Closed 3 commitments" in t and "`undo`" in t for t in texts),
      "closed line carries count + undo affordance")
check(any("Started tracking 1 new promise" in t for t in texts),
      "opened line singular form")
check(any("Recovered 2 items" in t for t in texts), "sweep line")
check(texts[0].startswith("Closed 3"),
      "substance ranks first (closures before housekeeping)")
check(texts[-1] in ("Ran the weekly cleanup pass.",
                    "Completed 1 background maintenance job on schedule."),
      "housekeeping ranks last")

# traceability: every line's refs point at real audit event seqs of its category
by_cat = {l["category"]: l for l in feed["lines"]}
closed_refs = by_cat["closed_from_sent"]["refs"]
check(len(closed_refs) == 1 and closed_refs[0] == 1,
      "closed line refs the exact sent_reconcile audit seq")
for l in feed["lines"]:
    check(l["refs"], f"every line traceable: {l['category']} carries refs")

# max_lines cap
feed2 = cf.changes_since(ws, _iso(SINCE), max_lines=3)
check(len(feed2["lines"]) == 3, "max_lines caps the render")

# malformed events never raise
ws2 = _ws([{"ts": "not-a-date", "type": "sent_reconcile", "data": "junk"},
           {"ts": in_win, "type": "sent_reconcile",
            "data": {"n_closed": "three"}}])
feed3 = cf.changes_since(ws2, _iso(SINCE))
check(feed3["lines"] == [], "malformed audit events tolerated, no lines")

print(f"OK — {PASS} checks passed")
