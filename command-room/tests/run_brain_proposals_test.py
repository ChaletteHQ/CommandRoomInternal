#!/usr/bin/env python3
"""Tests for brain_proposals — the Living Brain writer API + projector
(SPEC LB1 D1–D3, D10 + M rulings R1/R2). Mirrors run_deal_state_test.py
conventions. Fixtures mirror real substrate shapes (real-data fixture
gotcha); all dates computed relative to today (no hardcoded future dates).
Placeholder org/person names only."""
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


def _ws():
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {"version": 1, "people": [], "orgs": [
        {"id": "org_acme", "canonical_name": "Acme Co",
         "relationship_type": "prospect"},
    ], "threads": [], "engagements": []}
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    (data / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _raw_append(ws, rows):
    """Backdated fixture lines, real substrate shape (seq/ts present, exactly
    as they sit on disk) — the gate stamps live writes; fixtures that need
    old timestamps are written as the file would actually look."""
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


CONFIRM_TUPLES = [{"action": "confirm proposal"}, {"action": "dismiss proposal"}]


# --- propose(): tier legality (D2) -----------------------------------------
ws = _ws()
try:
    bp.propose(ws, kind="x", fingerprint="f", evidence="e",
               action_tuples=CONFIRM_TUPLES, tier="auto", detector="d",
               change_class="not_a_class")
    check(False, "auto tier with unlisted class must raise")
except bp.BrainProposalError:
    check(True, "auto tier outside AUTO_ALLOWED raises")

try:
    bp.propose(ws, kind="x", fingerprint="f", evidence="e",
               action_tuples=CONFIRM_TUPLES, tier="maybe", detector="d")
    check(False, "unknown tier must raise")
except bp.BrainProposalError:
    check(True, "unknown tier raises")

# R1 — the structured-fact identity class IS auto-legal (policy row landed
# ahead of its LB2 detector) because its archive reverser is registered.
r = bp.propose(ws, kind="person_org_creation_structured_fact",
               fingerprint="r1:sam", evidence="structured connector fact",
               action_tuples=CONFIRM_TUPLES, tier="auto",
               detector="lb2-placeholder",
               change_class="person_org_creation_structured_fact")
check(r["status"] == "proposed", "R1 auto class proposes (reverser registered)")

# --- propose(): D10 no-consumer rejection -----------------------------------
try:
    bp.propose(ws, kind="x", fingerprint="f2", evidence="e",
               action_tuples=[], tier="confirm", detector="d")
    check(False, "empty action_tuples must raise")
except bp.BrainProposalError:
    check(True, "no-consumer (empty tuples) rejected at source")
try:
    bp.propose(ws, kind="x", fingerprint="f2", evidence="e",
               action_tuples=[{"action": "not a real verb"}],
               tier="confirm", detector="d")
    check(False, "unregistered verb must raise")
except bp.BrainProposalError:
    check(True, "unregistered verb rejected at source (D10)")

# --- propose(): emit + dedup + alongside event -------------------------------
ws = _ws()
r = bp.propose(ws, kind="deal_update", fingerprint="deal:t1:stage",
               evidence="observed stage language",
               action_tuples=CONFIRM_TUPLES, tier="confirm",
               detector="deal-signals", thread_id="project_001",
               extra={"proposal_kind": "stage", "proposed_stage": "negotiating"})
check(r["status"] == "proposed" and r["proposal_id"].startswith("bp_"),
      "confirm proposal emits with bp_ id")
check(len(_events(ws, "brain_proposal")) == 1, "one brain_proposal event")
legacy = _events(ws, "deal_update_proposed")
check(len(legacy) == 1 and legacy[0]["data"]["thread_id"] == "project_001",
      "deal_update kind writes the reserved PIPE1 type alongside")
check(legacy[0]["data"]["proposed_stage"] == "negotiating",
      "alongside event carries the proposed stage")

r2 = bp.propose(ws, kind="deal_update", fingerprint="deal:t1:stage",
                evidence="again", action_tuples=CONFIRM_TUPLES,
                tier="confirm", detector="deal-signals")
check(r2["status"] == "duplicate_open", "open fingerprint dedups")
check(len(_events(ws, "brain_proposal")) == 1, "dedup wrote nothing")

# deal_creation (no thread yet) must NOT write the legacy type (its payload
# contract requires a thread_id)
r3 = bp.propose(ws, kind="deal_creation", fingerprint="deal:org_acme:creation",
                evidence="deal-shaped activity", action_tuples=CONFIRM_TUPLES,
                tier="confirm", detector="deal-signals", org_id="org_acme")
check(r3["status"] == "proposed", "creation proposal emits")
check(len(_events(ws, "deal_update_proposed")) == 1,
      "creation proposal writes NO legacy deal_update_proposed")

# --- projector: load + rank + shapes ----------------------------------------
items = bp.load_open_proposals(ws)
check(len(items) == 2, "projector returns both open proposals")
by_kind = {i["kind"]: i for i in items}
check(by_kind["deal_update"]["shape"] == "money", "deal_update ranks money-shaped")
check(by_kind["deal_creation"]["expires_at"] != "", "TTL expiry computed")

# ranking: money > identity > hygiene, then age
ranked = bp.rank_proposals([
    {"id": "a", "shape": "hygiene", "opened_at": _iso(NOW - timedelta(days=9))},
    {"id": "b", "shape": "money", "opened_at": _iso(NOW - timedelta(days=1))},
    {"id": "c", "shape": "identity", "opened_at": _iso(NOW - timedelta(days=5))},
    {"id": "d", "shape": "money", "opened_at": _iso(NOW - timedelta(days=3))},
])
check([i["id"] for i in ranked] == ["d", "b", "c", "a"],
      "rank: money > identity > hygiene, then oldest first within shape")

# --- card: cap + per-detector limit + overflow line (D3/D10) -----------------
ws = _ws()
for n in range(4):
    bp.propose(ws, kind="deal_update", fingerprint=f"deal:t{n}:stage",
               evidence=f"e{n}", action_tuples=CONFIRM_TUPLES, tier="confirm",
               detector="deal-signals", thread_id=f"project_{n:03d}")
for n in range(3):
    bp.propose(ws, kind="hygiene_thing", fingerprint=f"h{n}",
               evidence=f"h{n}", action_tuples=CONFIRM_TUPLES,
               tier="confirm", detector="other-detector")
card = bp.select_confirm_card(ws, "morning-brief")
check(len(card["items"]) <= bp.DAILY_CONFIRM_CAP, "card respects the cap of 5")
per_det = {}
for i in card["items"]:
    per_det[i["detector"]] = per_det.get(i["detector"], 0) + 1
check(max(per_det.values()) <= bp.MAX_SLOTS_PER_DETECTOR,
      "max 2 slots per detector per render")
check(card["overflow_count"] == card["total_open"] - len(card["items"]),
      "overflow count is total minus rendered")
check("staff meeting" in card["overflow_line"],
      "overflow line teaches the staff meeting phrase")

# --- R2 shown-markers: cross-surface same-day dedup --------------------------
ids_on_brief = {i["id"] for i in card["items"]}
coach_card = bp.select_confirm_card(ws, "coach")
check(not (ids_on_brief & {i["id"] for i in coach_card["items"]}),
      "R2: items shown on the brief today don't re-show on coach")
brief_again = bp.select_confirm_card(ws, "morning-brief")
check(ids_on_brief & {i["id"] for i in brief_again["items"]},
      "R2: the same surface re-rendering the same day still sees its items")
full = bp.load_open_proposals(ws, "staff-meeting")
check(len(full) == 7, "staff-meeting full-set exemption sees everything (R2)")
explicit = bp.load_open_proposals(ws, "system-health")
check(len(explicit) == 7, "system-health explicit ask sees everything (R2)")

# --- resolve: tombstone + shared ledger + cooldown ----------------------------
ws = _ws()
r = bp.propose(ws, kind="deal_update", fingerprint="deal:t9:stage",
               evidence="observed", action_tuples=CONFIRM_TUPLES,
               tier="confirm", detector="deal-signals", thread_id="project_009")
res = bp.resolve_proposal(ws, r["proposal_id"], "declined",
                          resolved_by="person_001", source_skill="apply-choices")
check(res["status"] == "resolved", "resolve writes")
check(len(_events(ws, "brain_proposal_resolved")) == 1, "tombstone appended")
check(len(_events(ws, "deal_update_dismissed")) == 1,
      "declined deal-kind writes the PIPE1 dismissal alongside")
check(bp.load_open_proposals(ws) == [], "tombstoned proposal leaves the queue")
res2 = bp.resolve_proposal(ws, r["proposal_id"], "declined",
                           resolved_by="person_001", source_skill="apply-choices")
check(res2["status"] == "already_resolved", "resolve is idempotent")
ledger = ws / "_hq" / "data" / "proposal_feedback.jsonl"
row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
check(row["pass"] == "deal-signals" and row["user_action"] == "declined"
      and row["fingerprint"] == "deal:t9:stage",
      "shared ledger row carries pass=detector + fingerprint")
r4 = bp.propose(ws, kind="deal_update", fingerprint="deal:t9:stage",
                evidence="signal persists", action_tuples=CONFIRM_TUPLES,
                tier="confirm", detector="deal-signals")
check(r4["status"] == "suppressed_cooldown",
      "declined fingerprint suppressed at source for 60 days")

try:
    bp.resolve_proposal(ws, "person:12", "applied",
                        resolved_by="p", source_skill="apply-choices")
    check(False, "legacy-family ids must be refused")
except bp.BrainProposalError:
    check(True, "resolve refuses legacy-family ids (adapters are read-only)")

# --- TTL expiry: silent sweep + card health -----------------------------------
ws = _ws()
opened = NOW - timedelta(days=20)   # past the 14d default TTL
_raw_append(ws, [{
    "ts": _iso(opened), "type": "brain_proposal", "source_skill": "deal-signals",
    "data": {"proposal_id": "bp_stale0000001", "kind": "deal_update",
             "fingerprint": "deal:old:stage", "tier": "confirm",
             "evidence": "old signal", "action_tuples": CONFIRM_TUPLES,
             "ttl_days": 14, "detector": "deal-signals"},
}])
bp.propose(ws, kind="deal_update", fingerprint="deal:fresh:stage",
           evidence="fresh", action_tuples=CONFIRM_TUPLES,
           tier="confirm", detector="deal-signals", thread_id="project_010")
check(len(bp.load_open_proposals(ws)) == 1,
      "TTL-past proposal excluded from render even before the sweep")
swept = bp.expire_stale(ws)
check(swept["n_expired"] == 1 and swept["expired"] == ["bp_stale0000001"],
      "expiry sweep tombstones exactly the stale one")
check(len(_events(ws, "brain_proposal_expired")) == 1, "expiry event written")
check(bp.expire_stale(ws)["n_expired"] == 0, "sweep is idempotent")
health = bp.card_health_counts(ws)
# LB2: `resting_auto` counts open auto-tier rows (the lifecycle-contract
# violation detector's health mirror) — 0 on any healthy workspace.
check(health == {"open": 1, "expired_in_window": 1, "resting_auto": 0},
      "card health counts open + recent expiries")
# expiry is NOT a cooldown — the fingerprint may re-propose
r5 = bp.propose(ws, kind="deal_update", fingerprint="deal:old:stage",
                evidence="signal persists", action_tuples=CONFIRM_TUPLES,
                tier="confirm", detector="deal-signals")
check(r5["status"] == "proposed", "an expired fingerprint may re-propose")

# --- legacy adapters: real-shape fixtures normalized into ONE queue ----------
ws = _ws()
old = NOW - timedelta(days=2)
_raw_append(ws, [
    # commitment-review family (reconcile-sent MEDIUM band, real shape).
    # The OPEN COMMITMENT the review is about (FB-19): real substrate always
    # has one — the reconciler only proposes a review against a commitment it
    # matched, and `load_open_review_proposals` drops reviews whose commitment
    # closed. This fixture used to omit it, which no adapter noticed until the
    # row had to state an ask; a review with no commitment behind it can name
    # nothing and, if confirmed, would dispatch a close against an id that
    # does not exist.
    {"ts": _iso(old), "type": "commitment", "source_skill": "meeting-notes",
     "data": {"id": "cmt_01ABCDEF", "title": "Send the Q3 draft",
              "owner_id": "person:001", "kind": "promise"}},
    {"ts": _iso(old), "type": "commitment_review_proposed",
     "source_skill": "reconcile-sent", "primary_thread_id": "",
     "data": {"commitment_id": "cmt_01ABCDEF", "proposed_resolution": "auto_resolve",
              "match_score": 0.42,
              "evidence": 'matched your sent message "Q3 draft" (Jul 8)'}},
    # person family (confirm_flow shape)
    {"ts": _iso(old), "type": "person_proposal", "source_skill": "meeting-notes",
     "data": {"name": "Quinn Sample", "evidence": "mentioned in a meeting",
              "review_reason": "unknown person"}},
    # org family (prose-written)
    {"ts": _iso(old), "type": "org_proposal", "source_skill": "pulse",
     "data": {"name": "Northwind", "evidence": "domain northwind.example seen"}},
    # dont-forget dormancy family
    {"ts": _iso(old), "type": "dont_forget_dormant_proposal",
     "source_skill": "pulse",
     "data": {"thread_id": "project_042", "reason": "31 days quiet"}},
    # a DECLINED org proposal must not surface
    {"ts": _iso(old), "type": "org_proposal", "source_skill": "pulse",
     "data": {"name": "Declined Org", "evidence": "x"}},
    {"ts": _iso(old), "type": "org_proposal_declined", "source_skill": "pulse",
     "data": {"name": "Declined Org"}},
    # a snoozed dormancy proposal must not surface
    {"ts": _iso(old), "type": "dont_forget_dormant_proposal",
     "source_skill": "pulse", "data": {"thread_id": "project_043"}},
    {"ts": _iso(old), "type": "dont_forget_snooze", "source_skill": "pulse",
     "data": {"thread_id": "project_043"}},
])
queue = bp.load_open_proposals(ws)
families = sorted(i["source_family"] for i in queue)
check(families == ["commitment_review", "dont_forget", "org", "person"],
      f"adapters normalize all four legacy families, tombstones honored: {families}")
for i in queue:
    for key in ("id", "kind", "shape", "tier", "fingerprint", "evidence",
                "opened_at", "detector", "source_family", "action_tuples"):
        check(key in i, f"normalized item carries {key} ({i['source_family']})")
cru = next(i for i in queue if i["source_family"] == "commitment_review")
check(cru["commitment_id"] == "cmt_01ABCDEF",
      "commitment-review adapter embeds the id verbatim")
# FB-19: the row must be answerable — named, asked, and actionable.
check(cru.get("title") == "Send the Q3 draft",
      "commitment-review row is NAMED (never the bare 'Housekeeping' fallback)")
check(cru["render_line"].endswith("?"),
      "commitment-review row states its ask as a question")
check([t["action"] for t in cru["action_tuples"]]
      == ["confirm", "not relevant", "hold"],
      f"commitment-review row carries its verbs: {cru['action_tuples']}")

# FB-19 drop-empty: an ORPHAN review (no commitment behind it → no title →
# no honest ask, and a confirm would dispatch a close against a missing id)
# must not render at all.
ws_orphan = _ws()
_raw_append(ws_orphan, [
    {"ts": _iso(old), "type": "commitment_review_proposed",
     "source_skill": "reconcile-sent", "primary_thread_id": "",
     "data": {"commitment_id": "cmt_NOSUCH", "proposed_resolution": "auto_resolve",
              "match_score": 0.42, "evidence": "matched an outbound send"}},
])
check(bp.load_open_proposals(ws_orphan) == [],
      "an un-askable orphan review renders no row (FB-19 drop-empty)")
org_row = next(i for i in queue if i["source_family"] == "org")
check(org_row.get("title") == "Northwind",
      "org adapter titles the row with the org name (FB-8)")
org_card = bp.build_card_view(bp.rank_proposals([org_row]))
check(org_card["sections"][0]["items"][0]["name"] == "Northwind",
      "org proposal renders its name on the card, not the shape fallback")


# --- FB-8: LEGACY person-proposal field spellings → the row NAME lands -------
# The 2026-07-16 live fire: every staff-meeting identity row rendered
# NAMELESS because the reader coalesced only `name` while the legacy events
# (older skill versions) carry `proposed_name` / `inferred_name` /
# `display_name`. Fixtures below mirror the REAL substrate key layouts
# observed on the live events.jsonl (keys + nesting exact; placeholder
# values — real-data fixture gotcha).
ws = _ws()
old = NOW - timedelta(days=3)
aged = NOW - timedelta(days=bp.PERSON_LOW_CONTEXT_STALE_DAYS + 10)
_raw_append(ws, [
    # real layout: promote-queue shape — name under `proposed_name`
    {"ts": _iso(old), "type": "person_proposal", "source_skill": "inbox-triage",
     "data": {"pending_review": True, "proposed_email": "riley@example.com",
              "proposed_name": "Riley Placeholder", "proposed_org_id": "org_x1",
              "source": "unknown sender in a triaged thread"}},
    # real layout: meeting-writer shape — name under `inferred_name`, role
    # under bare `role`
    {"ts": _iso(old), "type": "person_proposal", "source_skill": "meeting-notes",
     "data": {"confidence": 0.8, "inferred_name": "Morgan Sample",
              "pending_review": True, "primary_org_id": "org_x2",
              "role": "operations lead",
              "source": "introduced on a recorded call"}},
    # real layout: signal shape — `proposed_name` + `proposed_role` +
    # source_refs LIST; AGED past the low-context window (role present ⇒
    # rich context ⇒ must still surface)
    {"ts": _iso(aged), "type": "person_proposal", "source_skill": "pulse",
     "data": {"pending_review": True, "primary_org_id": "org_x3",
              "proposed_name": "Casey Example", "proposed_role": "counsel",
              "signal": "named as the counsel reviewing the draft terms",
              "source_refs": ["mail:thread_0001"]}},
    # real layout: update-proposal shape — NO name field at all (person_id +
    # delta); the row must carry a source snippet, NEVER a bare placeholder
    {"ts": _iso(old), "type": "person_update_proposal",
     "source_skill": "people-crm",
     "data": {"confidence": 0.7,
              "note": "proposed title change spotted in a signature block",
              "person_id": "person_777",
              "proposed_delta": {"role": "director"},
              "source_ref": "mail:thread_0002"}},
])
prows = [i for i in bp.load_open_proposals(ws)
         if i["source_family"] == "person"]
by_title = {i["title"]: i for i in prows}
check(len(prows) == 4, f"all four legacy-shaped person rows surface: {len(prows)}")
check("Riley Placeholder" in by_title,
      "proposed_name (promote-queue layout) lands as the row title")
check("Morgan Sample" in by_title,
      "inferred_name (meeting-writer layout) lands as the row title")
check(by_title["Morgan Sample"]["inferred_role"] == "operations lead",
      "bare `role` spelling coalesces into inferred_role")
check("looks like operations lead"
      in by_title["Morgan Sample"]["render_line"],
      "coalesced role drives the badge, not 'mentioned by name only'")
check("Casey Example" in by_title,
      "aged rich-context legacy row still surfaces (role ⇒ not low-context)")
nameless = [i for i in prows if not (i.get("name") or "").strip()]
check(len(nameless) == 1 and nameless[0]["title"].startswith(
        "proposed title change spotted"),
      "no-name-field update proposal falls back to a source snippet")
check(all(i["title"] for i in prows),
      "FB-8 contract: every person row carries a non-empty title")
view = bp.build_card_view(bp.rank_proposals(prows))
row_names = [r["name"] for s in view["sections"] for r in s["items"]]
check(sorted(row_names[:3] + row_names[3:])
      == sorted(by_title.keys()),
      f"build_card_view rows carry the names verbatim: {row_names}")
check("Needs confirming" not in row_names and "(unknown)" not in row_names,
      "no card row renders the nameless shape-label fallback")


# --- lb1 review fixes: F1 snooze/decline gate, F2 staleness window, F5 auto off card
ws = _ws()
# F1a: a snoozed brain proposal is retired for the dismissal TTL
r = bp.propose(ws, kind="deal_update", fingerprint="deal:snooze:stage",
               evidence="sig", action_tuples=CONFIRM_TUPLES,
               tier="confirm", detector="deal-signals")
pid = r["proposal_id"]
_raw_append(ws, [{"ts": _iso(NOW), "type": "chat_dismissal",
                  "source_skill": "apply-choices",
                  "data": {"target_id": pid, "ttl_days": 7,
                           "reason": "snoozed from the brain card"}}])
ids = [i["id"] for i in bp.load_open_proposals(ws)]
check(pid not in ids, "F1: snoozed brain proposal is retired (dismissal gate)")
# F1b: a project-row `not relevant` dismissal retires the project item
ws = _ws()
_raw_append(ws, [
    {"ts": _iso(NOW - timedelta(days=2)), "type": "project_proposal",
     "source_skill": "meeting-notes",
     "data": {"project_name": "Acme Rollout", "evidence": "mentioned twice"}},
])
q = bp.load_open_proposals(ws)
proj = next(i for i in q if i["source_family"] == "project")
_raw_append(ws, [{"ts": _iso(NOW), "type": "chat_dismissal",
                  "source_skill": "apply-choices",
                  "data": {"target_id": proj["id"], "ttl_days": 60,
                           "reason": "not relevant"}}])
ids = [i["id"] for i in bp.load_open_proposals(ws)]
check(proj["id"] not in ids, "F1: declined project proposal is retired")
# F2: an org proposal older than the staleness window never surfaces
ws = _ws()
_raw_append(ws, [
    {"ts": _iso(NOW - timedelta(days=bp.ORG_PROJECT_STALE_DAYS + 5)),
     "type": "org_proposal", "source_skill": "pulse",
     "data": {"name": "Zombie Org", "evidence": "ancient prose"}},
    {"ts": _iso(NOW - timedelta(days=2)), "type": "org_proposal",
     "source_skill": "pulse", "data": {"name": "Fresh Org", "evidence": "new"}},
])
names = [i.get("name") for i in bp.load_open_proposals(ws)
         if i["source_family"] == "org"]
check(names == ["Fresh Org"],
      f"F2: org/project adapter enforces the {bp.ORG_PROJECT_STALE_DAYS}d window: {names}")
# F5: an auto-tier proposal never enters the confirm card (feed's job)
ws = _ws()
bp.propose(ws, kind="commitment_close", fingerprint="cc:auto:1",
           evidence="high-band sent match", action_tuples=CONFIRM_TUPLES,
           tier="auto", change_class="commitment_close",
           detector="reconcile-sent")
bp.propose(ws, kind="deal_update", fingerprint="deal:cf:stage",
           evidence="sig", action_tuples=CONFIRM_TUPLES,
           tier="confirm", detector="deal-signals")
card = bp.select_confirm_card(ws, "morning-brief")
tiers = [i.get("tier") for i in card["items"]]
check("auto" not in tiers and len(card["items"]) == 1,
      f"F5: auto tier excluded from the card: {tiers}")
# LB2 parity flip: the projector DEFAULT now excludes autos too (the FB-20
# mandate — no adjudication surface may render or count one); diagnostics
# reach them via the explicit include_auto=True escape.
check(all(i.get("tier") != "auto" for i in bp.load_open_proposals(ws)),
      "LB2: auto proposal excluded from the projector default")
check(any(i.get("tier") == "auto"
          for i in bp.load_open_proposals(ws, include_auto=True)),
      "LB2: include_auto=True still reaches the auto row (diagnostic escape)")

# --- FS-19: already-a-contact suppression (confident matches only) ---------
# The person queue's missing symmetry with the org adapter. An "add person"
# whose name confidently resolves to an existing contact must NOT re-surface
# (the every-week resurfacing M reported); a lone first-name match stays a
# human call (Bug #19 discipline — never auto-merge a different same-first).
ws = _ws()
# Existing contacts on file: a full-name record and a first-name-shared one.
ent = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
ent["people"] = [
    {"id": "person_ross", "canonical_name": "Ross Placeholder",
     "emails": ["ross@example.com"], "status": "active"},
    {"id": "person_kev", "canonical_name": "Kevin Sample", "status": "active"},
]
(ws / "_hq" / "data" / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
fresh = NOW - timedelta(days=2)
_raw_append(ws, [
    # full-name match to person_ross → confident (Tier 2) → SUPPRESSED
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "meeting-notes",
     "data": {"pending_review": True, "inferred_name": "Ross Placeholder",
              "role": "advisor", "source": "named again on a later call"}},
    # lone first name overlapping Kevin Sample → ambiguous (Tier 3) → KEPT
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "meeting-notes",
     "data": {"pending_review": True, "inferred_name": "Kevin",
              "role": "vendor", "source": "a different Kevin on a new thread"}},
    # full name NOT on file → no match → KEPT
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "inbox-triage",
     "data": {"pending_review": True, "proposed_name": "Dana Newperson",
              "source": "new sender"}},
    # update-type referencing an existing person → existence is its premise,
    # NEVER suppressed (proposes a change to someone you already have)
    {"ts": _iso(fresh), "type": "person_update_proposal", "source_skill": "people-crm",
     "data": {"person_id": "person_ross", "proposed_delta": {"role": "director"},
              "note": "title change spotted in a signature",
              "source_ref": "mail:thread_9001"}},
])
# UXR1 D5: update-type rows now RESOLVE their display name from the record
# (person_ross's update row legitimately carries "Ross Placeholder"), so the
# FS-19 suppression pin scopes to ADD rows (person_id is None on those).
pnames = [i.get("name") for i in bp.load_open_proposals(ws)
          if i["source_family"] == "person" and not i.get("person_id")]
check("Ross Placeholder" not in pnames,
      f"FS-19: full-name match to an existing contact is suppressed: {pnames}")
check("Kevin" in pnames,
      f"FS-19: lone first-name overlap stays a human call (not suppressed): {pnames}")
check("Dana Newperson" in pnames,
      f"FS-19: a genuinely-new full name still surfaces: {pnames}")
check(any(i.get("person_id") == "person_ross" and i["source_family"] == "person"
          for i in bp.load_open_proposals(ws)),
      "FS-19: a person_update_proposal on an existing person is NOT suppressed")
# The helper itself, exercised directly on the three add-type shapes.
check(bp.person_proposal_already_on_file(
    ws, {"type": "person_proposal", "name": "Ross Placeholder"}) is True,
    "FS-19 helper: confident full-name match → True")
check(bp.person_proposal_already_on_file(
    ws, {"type": "person_proposal", "name": "Kevin"}) is False,
    "FS-19 helper: ambiguous first-name → False (fail-open on Tier 3)")
check(bp.person_proposal_already_on_file(
    ws, {"type": "person_update_proposal", "name": "Ross Placeholder"}) is False,
    "FS-19 helper: update-type is never suppressed")


# =============================================================================
# WG1-B — row quality (D-B1 resolve-or-strip / D-B2 evidence snippet /
# D-B3 org-shape gate). Fixtures mirror the real substrate shapes: the
# seq-3774 payload class (person_proposal with role + evidence + an id-shaped
# inferred_org) and the TDX-Arena row class (org-named person_proposal, no
# role, no evidence). Placeholder names only; dates relative to today (G14).
# =============================================================================
import re as _re  # noqa: E402

ws = Path(tempfile.mkdtemp())
_data = ws / "_hq" / "data"
_data.mkdir(parents=True)
(_data / "entities.json").write_text(json.dumps({
    "version": 1, "people": [],
    "orgs": [
        {"id": "org_045", "canonical_name": "Bluewater Placeholder",
         "relationship_type": "client"},
        {"id": "org_101", "canonical_name": "Vertex Range (AcademyCo)",
         "relationship_type": "prospect"},
    ],
    "threads": [],
}), encoding="utf-8")
(_data / "events.jsonl").write_text("", encoding="utf-8")

fresh = NOW - timedelta(days=2)
LONG_EVIDENCE = ("Onboarding session for a new client at 4k per month; "
                 "instance branded internally. Org record exists but no "
                 "person record yet, and the transcript names her twice "
                 "as the decision maker on the rollout.")
_raw_append(ws, [
    # seq-3774 class — hyphen id variant (the live sighting class).
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "past-meetings",
     "data": {"name": "Lia Sample",
              "inferred_role": "New client",
              "inferred_org": "org-045",
              "confidence": 0.75, "pending_review": True,
              "source_ref": "granola:00000000-mirror-3774",
              "evidence": LONG_EVIDENCE,
              "review_reason": "New paying client with no person record."}},
    # unresolvable id variant — must STRIP, never render.
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "past-meetings",
     "data": {"name": "Miko Placeholder",
              "inferred_role": "advisor",
              "inferred_org": "org_777",
              "pending_review": True,
              "source_ref": "granola:00000000-mirror-b",
              "evidence": "Advisor intro on the platform call."}},
    # D-B2 negative control — no evidence: line stays byte-identical.
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "meeting-notes",
     "data": {"name": "Garrick Placeholder",
              "pending_review": True,
              "source_ref": ""}},
    # TDX-Arena class 1 — org-named, no role/evidence, org already ON FILE.
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "past-meetings",
     "data": {"name": "Vertex Range (AcademyCo)", "pending_review": True,
              "source_ref": "granola:00000000-mirror-tdx"}},
    # TDX-Arena class 2 — org-named, no role/evidence, matching an OPEN
    # org_proposal (the org rail renders it; the person row re-routes away).
    {"ts": _iso(fresh), "type": "org_proposal", "source_skill": "past-meetings",
     "data": {"name": "Harbor Deck (StudioCo)",
              "signal": "External training platform, named repeatedly.",
              "source_ref": "granola:00000000-mirror-tdx2",
              "pending_review": True}},
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "past-meetings",
     "data": {"name": "Harbor Deck (StudioCo)", "pending_review": True,
              "source_ref": "granola:00000000-mirror-tdx2"}},
    # TDX-Arena class 3 — org-marker shape, matches NOTHING → quarantine.
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "past-meetings",
     "data": {"name": "Quantum Placeholder LLC", "pending_review": True,
              "source_ref": "granola:00000000-mirror-tdx3"}},
])

items = bp.load_open_proposals(ws)
person_items = {i.get("name"): i for i in items
                if i["source_family"] == "person"}

# --- D-B1: id-shaped inferred_org resolves to the canonical name ------------
lia = person_items.get("Lia Sample")
check(lia is not None, "seq-3774-class row renders as a person row")
check("Bluewater Placeholder" in lia["render_line"],
      f"D-B1: hyphen org id resolves to canonical name: {lia['render_line']}")
check(_re.search(r"org[-_]\d+", lia["render_line"]) is None,
      f"D-B1: raw id absent from the rendered line: {lia['render_line']}")
check(lia["inferred_org"] == "Bluewater Placeholder",
      "D-B1: the emitted item carries the RESOLVED org, never the id")

# --- D-B1: unresolvable id STRIPS (role-only badge, no org clause) ----------
miko = person_items.get("Miko Placeholder")
check(miko is not None, "unresolvable-id row still renders as a person row")
check(miko["render_line"].startswith("looks like advisor ·"),
      f"D-B1: unresolvable id strips to role-only badge: {miko['render_line']}")
check(_re.search(r"org[-_]\d+", miko["render_line"]) is None,
      "D-B1: unresolvable raw id never renders")
check(miko["inferred_org"] == "",
      "D-B1: unresolvable id stripped from the emitted item")

# --- D-B1: the stored payload is UNTOUCHED (no event rewrites) --------------
stored = [e for e in _events(ws, "person_proposal")
          if e["data"].get("name") == "Lia Sample"][0]
check(stored["data"]["inferred_org"] == "org-045",
      "D-B1: resolve-or-strip is render-side only — payload untouched")

# --- D-B2: evidence renders as a quoted, word-boundary-truncated snippet ----
check("“" in lia["render_line"] and "”" in lia["render_line"],
      f"D-B2: evidence renders as a quoted snippet: {lia['render_line']}")
snippet = lia["render_line"].split("“")[1].split("”")[0]
check(snippet.endswith("…"), f"D-B2: long evidence truncates with ellipsis: {snippet!r}")
check(len(snippet) <= 145, f"D-B2: snippet capped near 140 chars ({len(snippet)})")
body = snippet[:-1]
check(LONG_EVIDENCE.startswith(body.rstrip()) or body.rstrip() in LONG_EVIDENCE,
      "D-B2: snippet is the evidence VERBATIM (word-boundary cut, no rewrite)")
check(" " + body.rstrip().split(" ")[-1] + " " in LONG_EVIDENCE + " ",
      f"D-B2: truncation lands on a word boundary: …{body[-20:]!r}")

# --- D-B2: evidence-less row keeps today's generic line byte-identical ------
garrick = person_items.get("Garrick Placeholder")
check(garrick is not None, "name-only row still renders (negative control)")
check(_re.fullmatch(
      r"mentioned by name only · surfaced in a captured note"
      r"( on [A-Z][a-z]{2} \d{1,2})? · no contact record yet",
      garrick["render_line"]) is not None,
      f"D-B2: empty evidence → generic line unchanged (no snippet segment): "
      f"{garrick['render_line']}")

# --- D-B3: org-on-file person row drops (actioned symmetry) -----------------
check("Vertex Range (AcademyCo)" not in person_items,
      "D-B3: org-named row (org on file) never renders as a person row")

# --- D-B3: open org_proposal match re-routes to the org rail ----------------
check("Harbor Deck (StudioCo)" not in person_items,
      "D-B3: org-proposal-named row leaves the person queue")
org_rows = [i for i in items if i["source_family"] == "org"]
check(any(i.get("name") == "Harbor Deck (StudioCo)" for i in org_rows),
      "D-B3: the org rail renders the same subject as a proper org row")
check(sum(1 for i in items
          if (i.get("name") or "") == "Harbor Deck (StudioCo)") == 1,
      "D-B3: the re-route never double-renders the subject")

# --- D-B3: unroutable org-marker shape quarantines (honest placeholder) -----
quar = [i for i in items if i.get("title") == "1 row withheld"]
check(len(quar) == 1, f"D-B3: exactly one quarantine placeholder: {len(quar)}")
check("org-shaped payload in the person queue" in quar[0]["render_line"],
      "D-B3: placeholder names the defect class")
check("Quantum" not in quar[0]["render_line"]
      and "Quantum" not in quar[0]["title"],
      "D-B3: placeholder never echoes the payload content")
check([t["action"] for t in quar[0]["action_tuples"]] == ["show why"],
      "D-B3: placeholder carries only the read-only show-why verb")
check(quar[0]["id"].startswith("person:"),
      "D-B3: placeholder keeps the real wire id for show-why dispatch")

# --- D-B3: the Laura shape (role+evidence, org-matching name) is NEVER gated
# (the live org_045 record shares the person's name — person signal wins) ----
_raw_append(ws, [
    {"ts": _iso(fresh), "type": "person_proposal", "source_skill": "past-meetings",
     "data": {"name": "Bluewater Placeholder",
              "inferred_role": "founder", "pending_review": True,
              "source_ref": "granola:00000000-mirror-c",
              "evidence": "Named as the founder on the kickoff call."}},
])
names_now = [i.get("name") for i in bp.load_open_proposals(ws)
             if i["source_family"] == "person"]
check("Bluewater Placeholder" in names_now,
      "D-B3: a role+evidence row is never gated, whatever its name matches")

# --- acceptance #6: raw id absent from the FULL rendered page ---------------
from widget_transport import render_and_persist  # noqa: E402
view = bp.build_card_view(bp.rank_proposals(bp.load_open_proposals(ws)),
                          surface="staff-meeting")
transport = render_and_persist(data_view=view, wrapper="fragment",
                               persist_dir=tempfile.mkdtemp(),
                               name_hint="staff-meeting", page=1,
                               page_size=20)
visible = _re.sub(r'(data-[a-z-]+="[^"]*")|(href="[^"]*")', "",
                  transport["html"])
check(_re.search(r"org[-_]\d+", visible) is None,
      "D-B1 acceptance: no org id anywhere in the rendered page's visible text")

print(f"OK — {PASS} checks passed")
