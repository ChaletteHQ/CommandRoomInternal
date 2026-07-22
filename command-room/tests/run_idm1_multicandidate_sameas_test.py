#!/usr/bin/env python3
"""IDM1 — 2+ same-name candidates must DISAMBIGUATE, never rank-and-pick.

The gap (widget review batch 2026-07-21, follow-up 1): when a daily person
proposal's name matched 2+ people already on file, `_resolve_person_match`
truncated to ONE candidate (`max_candidates=1` on the token tier;
first-hit-wins on the fuzzy tier) and pre-filled its person_id into the
`same as [existing]` verb — one tap then linked the row to whichever
same-name person the resolver ranked first: a silent wrong-person merge.

The fix (D1/D2): detect the collision before capping. 2+ candidates → the
hint goes count-honest ("possible match: N people named X on file — same as
one of them?") and the `same as [existing]` verb ships UNPOPULATED (no value,
no person_id) so the handler's input="required" path forces the CEO to name
which record. Exactly one candidate → the pre-IDM1 row, byte-identical.

Fixtures mirror the real substrate shape (real-data fixture gotcha); dates are
relative to today (no hardcoded future dates); placeholder names only.

Coverage:
  1. Two same-name people on file ("Sam" → Sam Sample / Sam Stone, token
     tier) → count-honest hint, `same as [existing]` present but with NO
     value and NO person_id, no match_name/match_person_id on the row.
  2. D2 — the fuzzy fallback tier gets the same fence: a typo that
     fuzzy-resolves to 2 distinct on-file records ("natan" → two Nathans)
     is a collision, not first-hit-wins.
  3. Regression pin — exactly one candidate → the row is byte-identical to
     the pre-IDM1 output (full action_tuples literal pinned, populated verb,
     named hint, wire ids).
  4. Miss — a genuinely-new name → row unchanged (three-verb, "no contact
     record yet").
  5. Bug #19 pin — `add person` is offered on ALL of the above rows.
  6. Wire (build_card_view) — a collision row carries NO match_person_id /
     match_name in data; the unpopulated verb is still in its action ids.
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "scripts"))
import brain_proposals as bp  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)


def _ws(people):
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {"version": 1, "people": people, "orgs": [], "threads": [],
           "engagements": []}
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


def _person_rows(ws):
    return [i for i in bp.load_open_proposals(ws)
            if i["source_family"] == "person"]


def _actions(item):
    return [t.get("action") for t in item.get("action_tuples") or []]


def _same_as_tuple(item):
    return next((t for t in (item.get("action_tuples") or [])
                 if t.get("action") == "same as [existing]"), None)


# ---------------------------------------------------------------------------
# One workspace, three lanes: a token-tier collision (two Sams), a fuzzy-tier
# collision (two Nathans, reached only via the typo "natan"), a single match
# (Bo → Bo Sample), and a genuinely-new name.
# ---------------------------------------------------------------------------
PEOPLE = [
    {"id": "person_sam_sample", "canonical_name": "Sam Sample",
     "status": "active"},
    {"id": "person_sam_stone", "canonical_name": "Sam Stone",
     "status": "active"},
    {"id": "person_nathan_a", "canonical_name": "Nathan", "status": "active"},
    {"id": "person_nathan_b", "canonical_name": "Nathan", "status": "active"},
    {"id": "person_bo", "canonical_name": "Bo Sample", "status": "active"},
]
ws = _ws([dict(p) for p in PEOPLE])
fresh = NOW - timedelta(days=2)
_raw_append(ws, [
    # first name colliding with TWO multi-token records (token tier, D1)
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "meeting-notes",
     "data": {"pending_review": True, "inferred_name": "Sam",
              "source": "first-named on a call"}},
    # typo reaching TWO distinct records only via fuzzy/phonetic (D2)
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "inbox-triage",
     "data": {"pending_review": True, "proposed_name": "natan",
              "source": "named again on a later call"}},
    # single-candidate control — the pre-IDM1 populated row, byte-identical
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "meeting-notes",
     "data": {"pending_review": True, "inferred_name": "Bo",
              "source": "first-named on a call"}},
    # genuinely-new full name — NO plausible record
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "inbox-triage",
     "data": {"pending_review": True, "proposed_name": "Priya Anand",
              "source": "brand-new sender"}},
])

by_name = {i["name"]: i for i in _person_rows(ws)}
check(set(by_name) == {"Sam", "natan", "Bo", "Priya Anand"},
      f"all four add rows survive suppression and render: {sorted(by_name)}")

# --- 1) token-tier collision (D1): count-honest hint, unpopulated verb ------
sam = by_name["Sam"]
check("possible match: 2 people named Sam on file — same as one of them?"
      in sam["render_line"],
      f"'Sam' renders the count-honest collision hint: {sam['render_line']!r}")
check("no contact record yet" not in sam["render_line"],
      "'Sam' drops the false 'no contact record yet'")
check("possible match: Sam Sample" not in sam["render_line"]
      and "possible match: Sam Stone" not in sam["render_line"],
      "'Sam' names NEITHER candidate in the hint (no rank-and-pick)")
sam_verb = _same_as_tuple(sam)
check(sam_verb is not None,
      "'Sam' still offers the 'same as [existing]' verb on collision")
check("person_id" not in sam_verb and "value" not in sam_verb,
      f"'Sam' same-as verb is UNPOPULATED — no person_id, no value: "
      f"{sam_verb!r}")
check(sam.get("match_person_id") is None and sam.get("match_name") is None,
      "'Sam' row carries NO match_person_id / match_name (no silent winner)")

# --- 2) fuzzy-tier collision (D2): same fence, not first-hit-wins -----------
natan = by_name["natan"]
check("possible match: 2 people named natan on file — same as one of them?"
      in natan["render_line"],
      f"'natan' fuzzy tier is collision-fenced too: {natan['render_line']!r}")
natan_verb = _same_as_tuple(natan)
check(natan_verb is not None and "person_id" not in natan_verb
      and "value" not in natan_verb,
      f"'natan' same-as verb is UNPOPULATED: {natan_verb!r}")
check(natan.get("match_person_id") is None and natan.get("match_name") is None,
      "'natan' row carries NO match id — the fuzzy tier no longer picks the "
      "first hit")

# --- 3) single candidate: byte-identical pre-IDM1 row (regression pin) ------
bo = by_name["Bo"]
check(bo["action_tuples"] == [
    {"action": "add person"},
    {"action": "same as [existing]", "value": "Bo Sample",
     "person_id": "person_bo"},
    {"action": "proposal not relevant"},
    {"action": "snooze proposal 7d"},
], f"single-candidate action_tuples are byte-identical to pre-IDM1: "
   f"{bo['action_tuples']!r}")
check("possible match: Bo Sample — same person?" in bo["render_line"],
      f"single candidate keeps the NAMED hint: {bo['render_line']!r}")
check(bo.get("match_person_id") == "person_bo"
      and bo.get("match_name") == "Bo Sample",
      "single candidate still carries the wire ids verbatim")

# --- 4) miss: a genuinely-new name is unchanged ------------------------------
new = by_name["Priya Anand"]
check(new["action_tuples"] == [
    {"action": "add person"},
    {"action": "proposal not relevant"},
    {"action": "snooze proposal 7d"},
], f"no-match row keeps the unchanged three-verb set: "
   f"{new['action_tuples']!r}")
check(new["render_line"].endswith("no contact record yet"),
      f"new name keeps 'no contact record yet': {new['render_line']!r}")
check(new.get("match_person_id") is None and new.get("match_name") is None,
      "new name resolves to no candidate")

# --- 5) Bug #19 pin: Add person offered on every row -------------------------
for pname in ("Sam", "natan", "Bo", "Priya Anand"):
    check("add person" in _actions(by_name[pname]),
          f"{pname!r} still offers Add person (Bug #19)")

# --- 6) the wire (build_card_view): no winner leaks onto a collision row -----
card = bp.build_card_view(bp.rank_proposals(_person_rows(ws)))
wire = {r["name"]: r for s in card["sections"] for r in s["items"]}
check("match_person_id" not in wire["Sam"]["data"]
      and "match_name" not in wire["Sam"]["data"],
      "collision row carries NO match id/name on the wire")
check("same as [existing]" in wire["Sam"]["actions"],
      "the unpopulated 'same as' verb is present in the collision row's "
      "wire action ids")
check(wire["Bo"]["data"].get("match_person_id") == "person_bo",
      "single-candidate wire row still embeds match_person_id (populated "
      "dispatch, no re-type)")

print(f"OK — {PASS} checks passed")
