#!/usr/bin/env python3
"""SPEC OBJ2 §1B — the objective_link detector (org-scoped, config_drift
pattern).

Covers: (a) a stamped provisional event targeting an open objective thread →
exactly ONE proposal with the right kind / surface hint / natural-key
fingerprint; (b) an already-bound link (the event's primary thread is in the
objective's activity binding) → no proposal; (c) a closed/archived objective →
no proposal; (d) an auto-band classification (>= the auto-attach floor) → no
proposal — the detector only mechanizes the PROVISIONAL lane; (e) a second
detector run mints no duplicates (idempotence via propose()'s duplicate/no-op
statuses); (f) org-scope: a masked-account event that would otherwise qualify
never proposes and none of its fields leak into any payload (fixture built the
way the personal-firewall suite builds masked fixtures); (g) a link the CEO
adjudicated as CONFIRMED (applied) never re-lists — the one dedup propose()
does not own; (h) the cleanup dispatcher registers the detector (the same
instruction-layer pin run_lb2_remainder_test asserts for config_drift).

Fixtures mirror real substrate shapes; all dates computed relative to today
(G14); placeholder names only (Acme Pilot, lighthouse objective)."""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "shared" / "scripts"))
import brain_proposals as bp  # noqa: E402
import objective_link_detector as old  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)
LEAK_TOKEN = "Mole-Payload-Token"
FP1 = "objective_link:project_901:cmt_01OBJ2TESTTARGET1"


def _ws():
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {"version": 1, "people": [], "orgs": [], "engagements": [],
           "threads": [
        # the open objective (activity-bound to the Acme Pilot thread)
        {"id": "project_901", "kind": "objective", "status": "active",
         "canonical_name": "Land three lighthouse clients",
         "objective": {"statement": "Land three lighthouse clients",
                       "binding": {"type": "activity",
                                   "entity_ids": ["project_101"]},
                       "opened_at": (NOW - timedelta(days=40)).strftime("%Y-%m-%d")}},
        # a closed (archived) objective — never a link target
        {"id": "project_902", "kind": "objective", "status": "archived",
         "canonical_name": "Old push",
         "objective": {"statement": "Old push",
                       "binding": {"type": "self", "cadence_days": 7},
                       "opened_at": (NOW - timedelta(days=200)).strftime("%Y-%m-%d"),
                       "outcome": "archived",
                       "closed_at": (NOW - timedelta(days=90)).strftime("%Y-%m-%d")}},
        # the objective's bound activity thread
        {"id": "project_101", "kind": "project", "status": "active",
         "canonical_name": "Acme Pilot"},
    ]}
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    evs = [
        # (a) provisional classification onto the OPEN objective → PROPOSES
        {"seq": 1, "ts": _iso(NOW - timedelta(days=5)), "type": "commitment",
         "source_skill": "meeting-notes", "primary_thread_id": "project_901",
         "classification_confidence": 0.55,
         "data": {"id": "cmt_01OBJ2TESTTARGET1",
                  "title": "Draft the lighthouse pitch", "status": "open",
                  "origin": "user_stated"}},
        # (b) signal already on the BOUND thread (primary = binding entity) →
        # already linked, no attribution needed → no proposal
        {"seq": 2, "ts": _iso(NOW - timedelta(days=4)), "type": "commitment",
         "source_skill": "meeting-notes", "primary_thread_id": "project_101",
         "related_thread_ids": ["project_901"],
         "classification_confidence": 0.60,
         "data": {"id": "cmt_01OBJ2TESTBOUND02",
                  "title": "Send Acme the pilot recap", "status": "open",
                  "origin": "user_stated"}},
        # (c) provisional stamp onto the CLOSED objective → no proposal
        {"seq": 3, "ts": _iso(NOW - timedelta(days=3)), "type": "commitment",
         "source_skill": "meeting-notes", "primary_thread_id": "project_902",
         "classification_confidence": 0.50,
         "data": {"id": "cmt_01OBJ2TESTCLOSED3",
                  "title": "Revive the old push", "status": "open",
                  "origin": "user_stated"}},
        # (d) AUTO-band classification (>= floor, attribution asserted) →
        # not provisional → no proposal
        {"seq": 4, "ts": _iso(NOW - timedelta(days=2)), "type": "commitment",
         "source_skill": "meeting-notes", "primary_thread_id": "project_901",
         "classification_confidence": 0.92,
         "data": {"id": "cmt_01OBJ2TESTAUTO004",
                  "title": "Book the lighthouse demo", "status": "open",
                  "origin": "user_stated"}},
        # (f) the mask event + a masked-account event that would otherwise
        # qualify (provisional, open objective) — built the way the
        # personal-firewall suite builds its masked fixture
        {"seq": 5, "ts": _iso(NOW - timedelta(days=2)),
         "type": "account_scope_masked", "source_skill": "workspace-manager",
         "data": {"masked_account_id": "acct_reclassified",
                  "address": "personal@example.com"}},
        {"seq": 6, "ts": _iso(NOW - timedelta(days=1)), "type": "interaction",
         "source_skill": "inbox-triage", "primary_thread_id": "project_901",
         "classification_confidence": 0.50,
         "data": {"summary": f"{LEAK_TOKEN} private thread",
                  "provenance": {"provider": "gmail", "native_id": "m9",
                                 "account_id": "acct_reclassified"}}},
    ]
    (data / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in evs) + "\n", encoding="utf-8")
    return d


def _open_link_rows(ws):
    return [i for i in bp.load_open_proposals(ws, "staff-meeting")
            if i["kind"] == "objective_link"]


def _bp_events(ws):
    lines = (ws / "_hq" / "data" / "events.jsonl").read_text(
        encoding="utf-8").splitlines()
    out = []
    for line in lines:
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("type") == "brain_proposal":
            out.append(ev)
    return out


# ===========================================================================
# §1B.a — one provisional event → exactly ONE proposal, correct
#          kind / hint / fingerprint; bound, closed, and auto-band
#          fixtures propose NOTHING
# ===========================================================================
print("[a] detect + propose")
ws = _ws()
cands = old.detect_objective_links(ws)
check(len(cands) == 1 and cands[0]["fingerprint"] == FP1,
      f"exactly ONE candidate, natural-key fingerprint: {cands}")
out = old.run_objective_link_detector(ws)
check(out == {"candidates": 1, "proposed": 1, "suppressed": 0},
      f"first run proposes once: {out}")
rows = _open_link_rows(ws)
check(len(rows) == 1, f"exactly ONE open objective_link proposal: {rows}")
check(rows[0]["fingerprint"] == FP1,
      f"row carries the objective_link:<obj>:<target> fingerprint: "
      f"{rows[0]['fingerprint']}")
check(rows[0]["surface_hint"] == "staff-meeting",
      "row carries its staff-meeting surface hint")
check(rows[0]["shape"] == "objective",
      f"row resolves shape 'objective': {rows[0]['shape']}")
bp_evs = _bp_events(ws)
check(len(bp_evs) == 1,
      f"bound/closed/auto/masked fixtures proposed NOTHING: {len(bp_evs)}")
d = bp_evs[0]["data"]
check(d.get("objective_id") == "project_901"
      and d.get("target_id") == "cmt_01OBJ2TESTTARGET1",
      f"payload carries the adjudication target ids verbatim: {d}")
check(d.get("source_event_seq") == 1,
      f"payload carries the source event's seq — the dismiss pair's "
      f"supersedes_seq rides the payload, never re-derived at dispatch "
      f"(OBJ2 §2): {d}")

# ===========================================================================
# §1B.b — idempotence: a second run mints no duplicates
# ===========================================================================
print("[b] idempotence")
out2 = old.run_objective_link_detector(ws)
check(out2["proposed"] == 0 and out2["suppressed"] == 1,
      f"second run is all duplicate/no-op statuses: {out2}")
check(len(_open_link_rows(ws)) == 1 and len(_bp_events(ws)) == 1,
      "still exactly one proposal after the second run")

# ===========================================================================
# §1B.c — org-scope: the masked event neither proposes nor leaks
# ===========================================================================
print("[c] org-scope firewall")
raw = (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
check(raw.count(LEAK_TOKEN) == 1,
      "masked event's fields never reach a proposal payload (token appears "
      "only in its own fixture row)")
check(all(LEAK_TOKEN not in json.dumps(ev) for ev in _bp_events(ws)),
      "no brain_proposal payload carries the masked field")
check(all("seq6" not in i["fingerprint"] for i in _open_link_rows(ws)),
      "no proposal fingerprints the masked item")

# ===========================================================================
# §1B.d — a CONFIRMED (applied) adjudication never re-lists: detect-level
#          skip, not just propose()'s open-row dedup
# ===========================================================================
print("[d] confirmed never re-lists")
bp.resolve_proposal(ws, _open_link_rows(ws)[0]["id"], "applied",
                    resolved_by="person_m", source_skill="apply-choices")
check(_open_link_rows(ws) == [], "applied row leaves the open queue")
check(old.detect_objective_links(ws) == [],
      "confirmed fingerprint is skipped at DETECT (applied/edited never "
      "enter the ledger cooldown, so the detector must own this skip)")
out3 = old.run_objective_link_detector(ws)
check(out3 == {"candidates": 0, "proposed": 0, "suppressed": 0},
      f"post-confirm run proposes nothing: {out3}")

# ===========================================================================
# §1B.e — invocation: the cleanup dispatcher registers the detector (the
#          run_lb2_remainder_test pin, mirrored)
# ===========================================================================
print("[e] dispatcher registration")
cleanup_md = (REPO / "skills" / "cleanup" / "SKILL.md").read_text(
    encoding="utf-8")
check("run_objective_link_detector" in cleanup_md
      and "objective_link_detector" in cleanup_md,
      "cleanup SKILL.md wires the objective_link detector")

print(f"OK — {PASS} checks passed")
