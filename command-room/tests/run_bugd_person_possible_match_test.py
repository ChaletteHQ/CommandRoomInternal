#!/usr/bin/env python3
"""BUG C/D — daily person rows offer a possible-match candidate + a populated
`same as [existing]` verb instead of the false "no contact record yet".

Shared root cause (the report): the DAILY render adapter
`brain_proposals._adapt_person_proposals` asked the resolver a confident
yes/no ("on file?") and, on a NO, rendered "no contact record yet" with only an
"Add person" verb — even when a plausible existing record clearly existed. The
weekly reconcile rail already computes the candidate (identity_reconcile.
classify_cluster / people_writer.list_same_name_people); the fix wires that
resolution into the daily adapter WITHOUT making the confident FS-19 drop
greedier (that would regress Bug #19 — auto-hiding a genuinely-new person).

Fixtures mirror the real substrate shape (real-data fixture gotcha); dates are
relative to today (no hardcoded future dates); placeholder names only.

Coverage:
  1. Variants the confident/punctuation-blind/alias-blind suppression let
     through — "natan"→Nathan (fuzzy), "Chase Beach (or Evan alt account)"→Evan
     (embedded token + annotation markers), "Bo"→Bo Sample (first-name
     token) — each renders "possible match: X — same person?" + a POPULATED
     "same as [existing]" verb, and NOT "no contact record yet".
  2. Bug #19 preserved — a genuinely-new full name still renders "no contact
     record yet" + "Add person" and NO "same as" verb.
  3. The candidate reaches the wire (build_card_view data.match_person_id) so
     the populated verb dispatches without a re-type.
  4. The confident FS-19 drop is untouched — a full-name match is still
     suppressed entirely (never rendered as either "new" OR "possible match").
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


# ---------------------------------------------------------------------------
# The three on-file records the report names, plus proposal variants that each
# fail a DIFFERENT leg of the confident suppression.
# ---------------------------------------------------------------------------
PEOPLE = [
    {"id": "person_nathan", "canonical_name": "Nathan", "status": "active"},
    {"id": "person_evan", "canonical_name": "Evan Cohen", "status": "active"},
    {"id": "person_bo", "canonical_name": "Bo Sample",
     "status": "active"},
]
ws = _ws([dict(p) for p in PEOPLE])
fresh = NOW - timedelta(days=2)
_raw_append(ws, [
    # typo the token/exact check can't reach — needs fuzzy/phonetic
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "meeting-notes",
     "data": {"pending_review": True, "inferred_name": "natan",
              "source": "named again on a later call"}},
    # annotation markers + an embedded existing first name ("Evan")
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "inbox-triage",
     "data": {"pending_review": True,
              "proposed_name": "Chase Beach (or Evan alt account)",
              "source": "quoted in a triaged thread"}},
    # lone first name overlapping a multi-token record
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "meeting-notes",
     "data": {"pending_review": True, "inferred_name": "Bo",
              "source": "first-named on a call"}},
    # genuinely-new full name — NO plausible record (Bug #19 pin)
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "inbox-triage",
     "data": {"pending_review": True, "proposed_name": "Priya Anand",
              "source": "brand-new sender"}},
])

by_name = {i["name"]: i for i in _person_rows(ws)}
check(set(by_name) == {"natan", "Chase Beach (or Evan alt account)", "Bo",
                       "Priya Anand"},
      f"all four add rows survive suppression and render: {sorted(by_name)}")

# --- 1) each variant resolves to its candidate ------------------------------
EXPECT = {
    "natan": ("Nathan", "person_nathan"),
    "Chase Beach (or Evan alt account)": ("Evan Cohen", "person_evan"),
    "Bo": ("Bo Sample", "person_bo"),
}
for pname, (cand, cid) in EXPECT.items():
    row = by_name[pname]
    check(row.get("match_name") == cand,
          f"{pname!r} resolves to candidate {cand!r}: {row.get('match_name')!r}")
    check(row.get("match_person_id") == cid,
          f"{pname!r} carries the candidate id {cid!r} verbatim")
    check(f"possible match: {cand} — same person?" in row["render_line"],
          f"{pname!r} renders the possible-match hint: {row['render_line']!r}")
    check("no contact record yet" not in row["render_line"],
          f"{pname!r} DROPS the false 'no contact record yet'")
    check("same as [existing]" in _actions(row),
          f"{pname!r} carries a populated 'same as [existing]' verb")
    check("add person" in _actions(row),
          f"{pname!r} STILL offers Add person (a wrong match stays the human's "
          "call — Bug #19)")
    # the populated verb carries the candidate so apply-choices needs no re-type
    same = next(t for t in row["action_tuples"]
                if t.get("action") == "same as [existing]")
    check(same.get("value") == cand and same.get("person_id") == cid,
          f"{pname!r} same-as verb is POPULATED with {cand!r}/{cid!r}: {same!r}")

# --- 2) Bug #19: a genuinely-new name is unchanged --------------------------
new = by_name["Priya Anand"]
check(new.get("match_name") is None and new.get("match_person_id") is None,
      "genuinely-new name resolves to no candidate")
check(new["render_line"].endswith("no contact record yet"),
      f"new name keeps 'no contact record yet': {new['render_line']!r}")
check("possible match" not in new["render_line"],
      "new name shows NO possible-match hint")
check("same as [existing]" not in _actions(new),
      "new name offers NO 'same as' verb")
check("add person" in _actions(new),
      "new name still offers Add person")

# --- 3) the candidate reaches the wire (build_card_view) ---------------------
card = bp.build_card_view(bp.rank_proposals(_person_rows(ws)))
wire = {r["name"]: r for s in card["sections"] for r in s["items"]}
check(wire["natan"]["data"].get("match_person_id") == "person_nathan",
      "build_card_view embeds match_person_id on the wire row (populated "
      "dispatch, no re-type)")
check(wire["natan"]["data"].get("match_name") == "Nathan",
      "build_card_view embeds match_name on the wire row")
check("match_person_id" not in wire["Priya Anand"]["data"],
      "a no-match row carries NO match id on the wire")
check("same as [existing]" in wire["natan"]["actions"],
      "the populated 'same as' verb is present in the wire row's action ids")

# --- 4) the confident FS-19 drop is untouched (no regression) ---------------
# A full-name exact match must STILL be suppressed entirely — never rendered as
# "new" and never as a "possible match" row either.
ws2 = _ws([dict(p) for p in PEOPLE])
_raw_append(ws2, [
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "meeting-notes",
     "data": {"pending_review": True, "inferred_name": "Evan Cohen",
              "source": "named again on a later call"}},
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "inbox-triage",
     "data": {"pending_review": True, "proposed_name": "Priya Anand",
              "source": "brand-new sender"}},
])
names2 = {i["name"] for i in _person_rows(ws2)}
check("Evan Cohen" not in names2,
      f"FS-19 confident full-name match is still dropped entirely: {names2}")
check("Priya Anand" in names2,
      "the genuinely-new row still surfaces alongside the confident drop")

print(f"OK — {PASS} checks passed")
