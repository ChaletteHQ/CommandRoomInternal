#!/usr/bin/env python3
"""SPEC OBJ2 §1A — objective_link kind + "objective" shape on the brain rail.

Covers: (a) propose() an objective_link → one open proposal, shape
"objective", ranked after hygiene; (b) same fingerprint again → the rail's
duplicate no-op; (c) dismiss then re-propose → the standard 60d ledger
cooldown suppresses (no kind-specific machinery); (d) build_card_view
renders the objective section LAST and the header count EQUALS the rendered
rows — pinned for EVERY registered shape so the count-but-drop bug cannot
recur; (e) the surface_hint filter that keeps config_drift off the daily
card (LB2) holds identically for objective_link.

Fixtures mirror real substrate shapes; all dates computed relative to today
(G14); placeholder names only (Sam Sample, Acme Co, obj_1/cmt_1)."""
import json
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "shared" / "scripts"))
import brain_proposals as bp  # noqa: E402
from proposal_ledger import active_cooldowns  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)
FP = "objective_link:obj_1:cmt_1"
DETECTOR = "objective-link"
BP_TUPLES = [{"action": "confirm proposal"},
             {"action": "dismiss proposal"},
             {"action": "snooze proposal 7d"}]


def _ws():
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {"version": 1, "people": [], "orgs": [
        {"id": "org_acme", "canonical_name": "Acme Co",
         "display_name": "Acme Co", "relationship_type": "prospect"},
    ], "threads": [], "engagements": []}
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    (data / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _propose_objective(ws):
    return bp.propose(
        ws,
        kind="objective_link",
        tier="confirm",
        fingerprint=FP,
        detector=DETECTOR,
        evidence="cmt_1 looks like a move on obj_1",
        action_tuples=list(BP_TUPLES),
        render_line="Sam Sample's commitment looks like a move on obj_1 — "
                    "link it?",
        extra={
            "surface_hint": "staff-meeting",
            "objective_id": "obj_1",
            "target_id": "cmt_1",
            "title": "obj_1 — proposed link",
        },
    )


# ===========================================================================
# §1A.a — propose → ONE open proposal; shape "objective"; ranks after hygiene
# ===========================================================================
print("[a] propose objective_link")
check(bp.kind_shape("objective_link") == "objective",
      "kind_shape registers objective_link → objective")
check(bp._SHAPE_RANK.get("objective") == 3
      and bp._SHAPE_RANK["objective"] > bp._SHAPE_RANK["hygiene"],
      "objective ranks AFTER hygiene in _SHAPE_RANK")
check(bp.MIGRATED_KINDS.get("objective_link") == "objective_link:",
      "fingerprint-convention prefix registered in MIGRATED_KINDS")

ws = _ws()
r = _propose_objective(ws)
check(r["status"] == "proposed", f"first propose emits: {r}")
rows = [i for i in bp.load_open_proposals(ws, "staff-meeting")
        if i["kind"] == "objective_link"]
check(len(rows) == 1, f"exactly ONE open objective_link proposal: {rows}")
check(rows[0]["shape"] == "objective",
      f"projected row resolves shape 'objective': {rows[0]['shape']}")
check(rows[0]["surface_hint"] == "staff-meeting",
      "row carries its staff-meeting surface hint")
ranked = bp.rank_proposals([
    dict(rows[0], opened_at=_iso(NOW - timedelta(days=30))),
    {"id": "bp_hyg", "shape": "hygiene", "kind": "entity_fact",
     "opened_at": _iso(NOW)},
])
check([i["shape"] for i in ranked] == ["hygiene", "objective"],
      "an OLDER objective still ranks after hygiene (shape before age)")

# convention guard: a fingerprint off the natural-key form is refused loudly
try:
    bp.propose(ws, kind="objective_link", tier="confirm",
               fingerprint="obj_1:cmt_1", detector=DETECTOR,
               evidence="x", action_tuples=list(BP_TUPLES))
    check(False, "off-convention fingerprint must raise")
except bp.BrainProposalError:
    check(True, "off-convention fingerprint refused (natural-key form)")

# ===========================================================================
# §1A.b — same fingerprint again → duplicate no-op
# ===========================================================================
print("[b] duplicate fingerprint")
r2 = _propose_objective(ws)
check(r2["status"] == "duplicate_open",
      f"open row dedups the second propose: {r2}")
check(len([i for i in bp.load_open_proposals(ws, "staff-meeting")
           if i["kind"] == "objective_link"]) == 1,
      "still exactly one open proposal after the duplicate")

# ===========================================================================
# §1A.c — dismiss, re-propose → the standard ledger cooldown suppresses
# ===========================================================================
print("[c] decline cooldown")
bp.resolve_proposal(ws, rows[0]["id"], "declined",
                    resolved_by="person_m", source_skill="apply-choices")
check([i for i in bp.load_open_proposals(ws, "staff-meeting")
       if i["kind"] == "objective_link"] == [],
      "declined row leaves the open queue")
r3 = _propose_objective(ws)
check(r3["status"] == "suppressed_cooldown",
      f"re-propose after decline is suppressed: {r3}")
check(FP in active_cooldowns(ws, DETECTOR, now_iso=_iso(NOW)),
      "the fingerprint's cooldown rides the shared ledger (nothing "
      "kind-specific)")

# ===========================================================================
# §1A.d — card view: objective section renders, LAST, and the header count
#         equals the rendered rows for EVERY registered shape (RV-4 pin)
# ===========================================================================
print("[d] card view honesty")
ws = _ws()
for kind, fp in (("deal_creation", "deal:acme"),
                 ("person", "person:sam-sample"),
                 ("entity_fact", "fact:acme:hq")):
    r = bp.propose(ws, kind=kind, tier="confirm", fingerprint=fp,
                   detector="fixture", evidence="fixture evidence",
                   action_tuples=list(BP_TUPLES),
                   extra={"title": "Acme Co"})
    check(r["status"] == "proposed", f"{kind} fixture emits: {r}")
r = _propose_objective(ws)
check(r["status"] == "proposed", f"objective fixture emits: {r}")
view = bp.build_card_view(
    bp.rank_proposals(bp.load_open_proposals(ws, "staff-meeting")))
titles = [s["title"] for s in view["sections"]]
check(len(titles) == 4 and titles[-1].startswith("OBJECTIVES ("),
      f"objective section renders, ordered LAST: {titles}")
check(any(t["label"] == "Objectives" and t["value"] == 1
          for t in view["tiles"]),
      f"Objectives tile counts its row: {view['tiles']}")
n_rows = sum(len(s["items"]) for s in view["sections"])
m = re.search(r"— (\d+) ", view["header"])
check(m is not None and int(m.group(1)) == n_rows == 4,
      f"header count EQUALS rendered rows: {view['header']} vs {n_rows}")
# the structural pin: EVERY shape in _SHAPE_RANK must render — a shape
# registered for ranking but missing from build_card_view's section/tile
# tuples would be counted in the header and silently dropped from the rows
# (the empirically-confirmed pre-OBJ2 bug). This fails the moment a fifth
# shape is added without wiring the card.
all_shapes = [{"id": f"bp_{s}", "shape": s, "kind": "x",
               "action_tuples": list(BP_TUPLES)}
              for s in sorted(bp._SHAPE_RANK, key=bp._SHAPE_RANK.get)]
cv = bp.build_card_view(all_shapes)
n_all = sum(len(s["items"]) for s in cv["sections"])
check(n_all == len(bp._SHAPE_RANK),
      f"every registered shape renders its row (count-but-drop pin): "
      f"{n_all}/{len(bp._SHAPE_RANK)}")
m = re.search(r"— (\d+) ", cv["header"])
check(m is not None and int(m.group(1)) == n_all,
      f"header honest over ALL registered shapes: {cv['header']}")
check(bp._SHAPE_NAME_FALLBACK.get("objective") == "Objective proposal",
      "objective fallback row name registered")

# ===========================================================================
# §1A.e — surface gating: the LB2 surface_hint filter holds for
#         objective_link (staff meeting only, never the daily card)
# ===========================================================================
print("[e] surface gating")
ws = _ws()
r = _propose_objective(ws)
check(r["status"] == "proposed", f"gating fixture emits: {r}")
card = bp.select_confirm_card(ws, "morning-brief")
check(all(i["kind"] != "objective_link" for i in card["items"]),
      "objective_link never enters the daily card (staff meeting only)")
check(len([i for i in bp.load_open_proposals(ws, "staff-meeting")
           if i["kind"] == "objective_link"]) == 1,
      "the staff meeting still sees the hinted row")

print(f"OK — {PASS} checks passed")
