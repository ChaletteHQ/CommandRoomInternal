#!/usr/bin/env python3
"""Tests for entity_signal_detector (SPEC HIST1 Part 2, step 11) — the
prose lane is confirm-ONLY (D3), the structured lane auto-applies only
S2-eligible categories with batch stamps, cooldown/cap/dedup honored, and
the change feed narrates auto-noted facts with a standing undo (FB-20).

NOTE on the spec §6 sketch: it predates the S2 revision — per S2 and
acceptance #10 a structured ROLE fact yields a CONFIRM proposal (identity-
adjacent), never an auto fact; that is what this suite pins.

Fixtures mirror real substrate shapes (legacy person missing role, threads
under both keys); dates relative to today (G14); placeholder names only."""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import brain_proposals as bp  # noqa: E402
import brain_undo as bu  # noqa: E402
import change_feed  # noqa: E402
import entity_signal_detector as esd  # noqa: E402

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
        {"id": "person_001", "canonical_name": "Sam Sample",
         "status": "active", "role": "Analyst",
         "primary_org_id": "org_001", "first_seen": "2026-01-05"},
        {"id": "person_002", "canonical_name": "Mira Sample",
         "status": "active", "first_seen": "2026-02-01"},  # legacy: no role
    ], "orgs": [
        {"id": "org_001", "canonical_name": "Acme Co", "status": "active",
         "relationship_type": "client", "first_seen": "2026-01-05"},
        {"id": "org_002", "canonical_name": "Globex Co", "status": "active",
         "relationship_type": "network", "first_seen": "2026-02-01"},
    ], "threads": [], "engagements": []}
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


T_RECENT = NOW - timedelta(days=3)
T_STALE = NOW - timedelta(days=esd.TEXT_WINDOW_DAYS + 10)

# --- 1. Prose lane: promotion → CONFIRM proposal, never auto (D3) -----------
ws = _ws()
_raw_append(ws, [
    {"ts": _iso(T_RECENT), "type": "meeting", "source_skill": "meeting-notes",
     "person_ids": ["person_002"],
     "data": {"title": "Weekly sync",
              "summary": "Sounds like Mira got promoted to CFO this month"}},
])
cands = esd.detect_entity_signals(ws)
role_cands = [c for c in cands if c.get("person_id") == "person_002"]
check(len(role_cands) == 1, f"one signal for the promoted person: {len(role_cands)}")
c0 = role_cands[0]
check(c0["kind"] == "person_update" and c0["proposal_kind"] == "role_change",
      "confident title extraction → person_update role_change")
check(c0["proposed_role"].lower() == "cfo", f"extracted role: {c0['proposed_role']}")

res = esd.run_entity_signal_scan(ws)
check(res["n_proposed"] == 1, f"scan proposes it: {res}")
props = _events(ws, "brain_proposal")
check(len(props) == 1 and props[0]["data"]["tier"] == "confirm",
      "prose promotion rides tier=confirm — NEVER auto")
check(_events(ws, "person_role_changed") == [] and
      not any(p.get("role") == "CFO"
              for p in json.loads((ws / "_hq" / "data" / "entities.json")
                                  .read_text(encoding="utf-8"))["people"]),
      "nothing mutates the record without a confirm")

# Re-run: open-fingerprint dedup, no duplicate proposal.
res2 = esd.run_entity_signal_scan(ws)
check(res2["n_proposed"] == 0 and res2["n_suppressed"] >= 1,
      "re-run suppresses via open-proposal dedup")

# Decline → 60d ledger cooldown honored on the next run.
pid = props[0]["data"]["proposal_id"]
bp.resolve_proposal(ws, pid, "declined", resolved_by="person_001",
                    source_skill="test")
res3 = esd.run_entity_signal_scan(ws)
check(res3["n_proposed"] == 0 and res3["n_suppressed"] >= 1,
      "declined fingerprint stays suppressed (shared ledger cooldown)")

# --- 2. Prose lane: company-move → org_change proposal, never silent --------
ws = _ws()
_raw_append(ws, [
    {"ts": _iso(T_RECENT), "type": "interaction", "source_skill": "cr-inbox",
     "person_ids": ["person_001"], "org_ids": ["org_002"],
     "data": {"summary": "Sam mentioned he joined Globex Co last week"}},
])
cands = esd.detect_entity_signals(ws)
moves = [c for c in cands if c.get("proposal_kind") == "org_change"]
check(len(moves) == 1 and moves[0]["to_org_id"] == "org_002",
      "company-move language + co-referenced tracked org → org_change")
esd.run_entity_signal_scan(ws)
props = _events(ws, "brain_proposal")
check(any(p["data"].get("to_org_id") == "org_002" and
          p["data"]["tier"] == "confirm" for p in props),
      "move proposal is confirm-tier and carries to_org_id")
ent_after = json.loads((ws / "_hq" / "data" / "entities.json")
                       .read_text(encoding="utf-8"))
check(next(p for p in ent_after["people"]
           if p["id"] == "person_001")["primary_org_id"] == "org_001",
      "primary_org_id untouched — a move is never a silent field write")

# Move language whose only org is the person's CURRENT org → no signal.
ws2 = _ws()
_raw_append(ws2, [
    {"ts": _iso(T_RECENT), "type": "interaction", "source_skill": "cr-inbox",
     "person_ids": ["person_001"], "org_ids": ["org_001"],
     "data": {"summary": "Sam joined the Acme Co planning call"}},
])
check([c for c in esd.detect_entity_signals(ws2)
       if c.get("proposal_kind") == "org_change"] == [],
      "no org_change when the referenced org IS the current org")

# --- 3. Prose lane: org news → company_news fact proposal (confirm) ---------
ws = _ws()
_raw_append(ws, [
    {"ts": _iso(T_RECENT), "type": "note", "source_skill": "intel-intake",
     "org_ids": ["org_001"],
     "data": {"summary": "Acme Co raised a Series A this quarter"}},
    # Stale event outside the window — must NOT fire (window enforced).
    {"ts": _iso(T_STALE), "type": "note", "source_skill": "intel-intake",
     "org_ids": ["org_002"],
     "data": {"summary": "Globex Co raised a Series A long ago"}},
])
cands = esd.detect_entity_signals(ws)
news = [c for c in cands if c.get("org_id")]
check(len(news) == 1 and news[0]["org_id"] == "org_001",
      "org news inside the window fires; stale event does not")
check(news[0]["kind"] == "entity_fact"
      and news[0]["category"] == "company_news",
      "org news is an entity_fact candidate with category=company_news")
esd.run_entity_signal_scan(ws)
props = _events(ws, "brain_proposal")
check(all(p["data"]["tier"] == "confirm" for p in props),
      "company_news rides confirm (identity-adjacent, S2)")
check(_events(ws, "org_fact_observed") == [],
      "no fact is written without a confirm (prose lane)")

# --- 4. Self-referential events never echo ----------------------------------
before = len(esd.detect_entity_signals(ws))
_raw_append(ws, [
    {"ts": _iso(NOW), "type": "person_fact_observed",
     "source_skill": "people-crm", "data": {
         "person_id": "person_001", "fact": "Was promoted to CFO",
         "source_ref": "chat:user-statement", "summary": "promoted to CFO"}},
])
check(len(esd.detect_entity_signals(ws)) == before,
      "a recorded fact is not re-detected as a fresh signal (echo guard)")

# --- 5. Structured lane: S2 routing + stamps + dedup + cap ------------------
ws = _ws()
out = esd.apply_structured_facts(ws, [
    {"target_id": "person_001", "fact": "Prefers Signal over email",
     "category": "preference", "source_ref": "sig:msg_100"},
    {"target_id": "org_001", "fact": "Main line is the Denver office",
     "category": "contact", "source_ref": "sig:msg_101"},
    # identity-adjacent structured fact → CONFIRM (S2 / acceptance #10)
    {"target_id": "person_001", "fact": "Signature reads Chief of Staff",
     "category": "role", "source_ref": "sig:msg_102"},
    # uncategorized → confirm (never guessed into an auto category)
    {"target_id": "person_001", "fact": "Mentioned a new assistant",
     "source_ref": "mail:msg_103"},
])
check(out["n_auto_applied"] == 2, f"two S2-eligible facts auto-applied: {out}")
check(out["n_proposed"] == 2,
      "role + uncategorized facts DEMOTE to confirm proposals, never dropped")
pfacts = _events(ws, "person_fact_observed")
check(len(pfacts) == 1 and pfacts[0]["data"]["category"] == "preference",
      "only the preference fact wrote to the person")
check(pfacts[0]["data"]["brain_batch_id"] == out["batch_id"],
      "auto fact carries the batch stamp")
check("say `undo` to reverse" in out["undo_line"],
      "undo narration line returned for the surface")

# Dedup: the same structured fact re-observed → skipped, not duplicated.
out2 = esd.apply_structured_facts(ws, [
    {"target_id": "person_001", "fact": "Prefers Signal over email",
     "category": "preference", "source_ref": "sig:msg_200"},
])
check(out2["n_auto_applied"] == 0 and out2["n_skipped_dup"] == 1,
      "re-observed fact dedups against the un-retracted set")

# Undo the batch, re-apply → the retracted fact may be noted again.
bu.undo_batch(ws, {"kind": "brain_batch", "batch_id": out["batch_id"]},
              undone_by="person_001", source_skill="test")
out3 = esd.apply_structured_facts(ws, [
    {"target_id": "person_001", "fact": "Prefers Signal over email",
     "category": "preference", "source_ref": "sig:msg_300"},
])
check(out3["n_auto_applied"] == 1,
      "a retracted fact no longer blocks re-observation")

# Cap: an over-cap S2-eligible flood demotes the overflow to confirm.
ws = _ws()
flood = [{"target_id": "person_001", "fact": f"Preference item {i}",
          "category": "preference", "source_ref": f"sig:m{i}"}
         for i in range(esd.AUTO_FACT_CAP + 3)]
out = esd.apply_structured_facts(ws, flood)
check(out["n_auto_applied"] == esd.AUTO_FACT_CAP,
      f"auto lane capped at {esd.AUTO_FACT_CAP}")
check(out["n_proposed"] == 3,
      "overflow demotes to confirm proposals — narrated, never silent")

# Malformed rows are loud-per-item, contained-per-batch.
out = esd.apply_structured_facts(ws, [
    {"target_id": "person_001", "fact": "No source"},
    {"target_id": "person_404", "fact": "Ghost", "category": "preference",
     "source_ref": "sig:x"},
])
check(out["n_errors"] == 2 and out["n_auto_applied"] == 0,
      "missing source_ref and unknown target are per-item errors")

# --- 6. Change feed narrates auto facts with standing undo (FB-20) ----------
ws = _ws()
since = _iso(NOW - timedelta(hours=1))
esd.apply_structured_facts(ws, [
    {"target_id": "person_001", "fact": "Prefers Signal",
     "category": "preference", "source_ref": "sig:1"},
    {"target_id": "org_001", "fact": "Front desk moved to suite 400",
     "category": "contact", "source_ref": "cal:2"},
])
# An EXPLICIT user fact in the same window — must NOT count (user's own act).
from people_writer import record_person_fact  # noqa: E402
record_person_fact(ws, "person_001", "Likes morning meetings",
                   "chat:user-statement", category="preference",
                   source_skill="people-crm")
feed = change_feed.changes_since(ws, since)
noted = [l for l in feed["lines"] if l["category"] == "facts_noted"]
check(len(noted) == 1, "one facts_noted line in the feed")
check("Noted 2 facts from your connected sources" in noted[0]["text"]
      and "say `undo` to reverse" in noted[0]["text"],
      f"narration counts ONLY auto facts, undo standing: {noted[0]['text']}")
check(len(noted[0]["refs"]) == 2, "feed line refs the two fact event seqs")

# --- 7. Prose-scan per-run proposal cap -------------------------------------
ws = _ws()
rows = []
for i in range(esd.MAX_PROPOSALS_PER_RUN + 2):
    rows.append({"ts": _iso(T_RECENT), "type": "note",
                 "source_skill": "intel-intake", "org_ids": ["org_001"],
                 "data": {"summary": f"Acme Co launched product line {i}"}})
_raw_append(ws, rows)
res = esd.run_entity_signal_scan(ws)
check(res["n_proposed"] <= esd.MAX_PROPOSALS_PER_RUN,
      f"prose lane capped per run: {res}")

print(f"OK — {PASS} checks passed")
