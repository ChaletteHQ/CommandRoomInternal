#!/usr/bin/env python3
"""
v4.6.1 W4b regression suite — the unconfirmed-commitment confirm flow.

Guards the design contracts from the W4b spec (FINDINGS F-13 P2b / F-56 —
persisting owner misattributions; F-46 P2b — stranded person proposals):

  1. Confirm-section selector: the daily window runs up to the 7-day
     escalation pin (v4.6.1 S3 — widened from 24h so items aged 1-7 days
     no longer fall between the daily section and the pin); the three
     amber classes (pending_review / unowned / suspected_duplicate) are
     IN; confirmed items and pin-age items are OUT.
  2. Every confirm verb's event round-trips through the loader:
     Mine (owner=user, pending_review cleared), Theirs→[name] (S4
     commitment_reassigned confirmed=true), Keep both (duplicate flags
     cleared), Merge (duplicate closes into the survivor), Make task /
     Drop / Promote — and the ordering rule (a later unconfirmed
     reassignment re-stamps pending_review over an earlier Mine).
  3. Same-as writes the alias (aliases.json mapping + person record) and
     FUTURE resolution uses it (entity_resolve); the tombstone retires the
     proposal so it stops re-surfacing.
  4. Escalation: 7d+ unconfirmed pins; 30d+ additionally proposes Drop;
     confirmed old items do neither.
  5. The morning-brief pointer line renders ONLY when the confirm set is
     non-empty.

Fixtures mirror REAL substrate shapes (realdata-fixture gotcha): the open
set includes canonical (data.*), flat-new (top-level fields), and
owner_person_id-variant commitments — the selector must not silently drop
shape variants.

Run via: python3 tests/run_confirm_flow_test.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from confirm_flow import (  # noqa: E402
    build_person_proposal_resolved_event,
    confirm_pointer_line,
    load_open_person_proposals,
    select_confirm_items,
    select_promotion_proposals,
    select_unconfirmed_escalation,
    unconfirmed_classes,
)
from cru_match import (  # noqa: E402
    _commitment_field,
    _is_pending_review,
    load_open_commitments,
)
from commitment_state import (  # noqa: E402
    clear_review_flags,
    close_commitment,
    confirm_commitment_owner,
    count_commitments,
    promote_task_to_commitment,
    reassign_commitment,
    supersede_commitment,
)

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}{(' — ' + detail) if detail else ''}")


NOW = dt.datetime(2026, 7, 9, 12, 0, 0, tzinfo=dt.timezone.utc)
NOW_ISO = NOW.isoformat().replace("+00:00", "Z")


def iso(hours_ago: float = 0.0, days_ago: float = 0.0) -> str:
    t = NOW - dt.timedelta(hours=hours_ago, days=days_ago)
    return t.isoformat().replace("+00:00", "Z")


def make_workspace(events: list[dict]) -> Path:
    ws = Path(tempfile.mkdtemp(prefix="w4b_confirm_"))
    d = ws / "_hq" / "data"
    d.mkdir(parents=True)
    with open(d / "events.jsonl", "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    (d / "entities.json").write_text(json.dumps({
        "version": 1,
        "people": [
            {"id": "person_001", "canonical_name": "Mira Sample",
             "first_seen": "2026-01-01"},
            {"id": "person_009", "canonical_name": "Dustin Stone",
             "first_seen": "2026-01-01"},
        ],
    }), encoding="utf-8")
    return ws


def events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


# Real-shape fixture set. USER = user_1.
FIXTURE = [
    # canonical shape, pending_review, captured 3h ago — IN (pending_review)
    {"seq": 1, "ts": iso(hours_ago=3), "type": "commitment", "source_skill": "meeting-notes",
     "data": {"id": "cmt_pending", "title": "send Jason the book", "status": "open",
              "owner_id": "user_1", "pending_review": True,
              "review_reason": "owner inferred from transcript"}},
    # flat-new shape (top-level fields), NO owner, 5h ago — IN (unowned)
    {"seq": 2, "ts": iso(hours_ago=5), "type": "commitment", "source_skill": "session-sweep",
     "title": "circulate the positioning brief", "status": "open",
     "data": {"id": "cmt_unowned", "evidence": "sweep recovery"}},
    # canonical, C4 duplicate flag, 2h ago — IN (suspected_duplicate)
    {"seq": 3, "ts": iso(hours_ago=2), "type": "commitment", "source_skill": "scan-for-commitments",
     "data": {"id": "cmt_dup", "title": "send the deck to Michele", "status": "open",
              "owner_id": "user_1", "pending_review": True,
              "suspected_duplicate_of": "cmt_survivor", "suspected_duplicate_score": 0.86,
              "review_reason": "looks like a duplicate of an open item: send deck"}},
    # the duplicate's survivor — confirmed, 30h ago — OUT (confirmed)
    {"seq": 4, "ts": iso(hours_ago=30), "type": "commitment", "source_skill": "meeting-notes",
     "data": {"id": "cmt_survivor", "title": "send deck", "status": "open",
              "owner_id": "user_1", "source_ref": "granola:abc",
              "counterparty_id": "person_001"}},
    # owner_person_id-variant shape, confirmed, 1h ago — OUT (no amber class)
    {"seq": 5, "ts": iso(hours_ago=1), "type": "commitment", "source_skill": "past-meetings",
     "data": {"id": "cmt_variant", "title": "book the venue", "state": "open",
              "owner_person_id": "user_1", "due_date": "2026-07-15"}},  # DATE_GUARD_OK: widget payload shape; suite clock is pinned via NOW
    # pending_review, captured 26h ago — IN since S3 widened the window to the
    # 7-day pin (this row was the W4b gap: old enough to leave the 24h section,
    # too young for the pin — it surfaced nowhere)
    {"seq": 6, "ts": iso(hours_ago=26), "type": "commitment", "source_skill": "scan-for-commitments",
     "data": {"id": "cmt_yesterday", "title": "chase the invoice", "status": "open",
              "pending_review": True}},
    # unconfirmed for 12 days — escalation pin, not the daily section
    {"seq": 7, "ts": iso(days_ago=12), "type": "commitment", "source_skill": "session-sweep",
     "data": {"id": "cmt_12d", "title": "old unowned capture", "status": "open"}},
    # unconfirmed for 45 days — pin + propose_drop
    {"seq": 8, "ts": iso(days_ago=45), "type": "commitment", "source_skill": "scan-for-commitments",
     "data": {"id": "cmt_45d", "title": "ancient pending capture", "status": "open",
              "pending_review": True}},
    # CONFIRMED and 45 days old — neither pin nor drop
    {"seq": 9, "ts": iso(days_ago=45), "type": "commitment", "source_skill": "meeting-notes",
     "data": {"id": "cmt_45d_ok", "title": "long-running confirmed item", "status": "open",
              "owner_id": "user_1"}},
    # task that gained a counterparty — promotion proposal
    {"seq": 10, "ts": iso(days_ago=2), "type": "commitment", "source_skill": "meeting-notes",
     "data": {"id": "cmt_task_cp", "title": "draft the pricing note", "status": "open",
              "kind": "task", "owner_id": "user_1",
              "counterparty_id": "person_009", "counterparty_name": "Dustin Stone"}},
    # task with NO counterparty — never proposed
    {"seq": 11, "ts": iso(days_ago=2), "type": "commitment", "source_skill": "meeting-notes",
     "data": {"id": "cmt_task_bare", "title": "tidy the tracker", "status": "open",
              "kind": "task", "owner_id": "user_1"}},
    # person proposals: one open, one adjudicated
    {"seq": 12, "ts": iso(days_ago=9), "type": "person_proposal", "source_skill": "meeting-notes",
     "data": {"name": "Myra Samples", "pending_review": True,
              "source_ref": "granola:xyz", "inferred_org": "Acme Co"}},
    {"seq": 13, "ts": iso(days_ago=9), "type": "person_proposal", "source_skill": "meeting-notes",
     "data": {"name": "Rick Passerby", "pending_review": True, "source_ref": "granola:xyz"}},
    {"seq": 14, "ts": iso(days_ago=8), "type": "person_proposal_resolved",
     "source_skill": "commitments",
     "data": {"proposal_seq": 13, "resolution": "not_relevant"}},
    # pending_review, captured 6 days ago — still the daily section's job (< pin)
    {"seq": 15, "ts": iso(days_ago=6), "type": "commitment", "source_skill": "session-sweep",
     "data": {"id": "cmt_6d", "title": "review the vendor quote", "status": "open",
              "pending_review": True}},
]


def main() -> int:
    # ------------------------------------------------------------------
    print("[1] confirm-section selector — window tiles with the 7d pin, three amber classes in, confirmed out")
    # ------------------------------------------------------------------
    ws = make_workspace(FIXTURE)
    ep = events_path(ws)
    opens = load_open_commitments(ep)
    rows = select_confirm_items(opens, NOW_ISO)
    ids = {r["commitment_id"] for r in rows}
    check("pending_review capture (canonical shape) is IN", "cmt_pending" in ids)
    check("unowned capture (flat-new shape) is IN", "cmt_unowned" in ids)
    check("suspected-duplicate capture is IN", "cmt_dup" in ids)
    check("confirmed recent capture (owner_person_id shape) is OUT",
          "cmt_variant" not in ids)
    check("confirmed 30h-old capture is OUT", "cmt_survivor" not in ids)
    check("26h-old pending capture is IN (the W4b gap: 1-7d items surfaced nowhere)",
          "cmt_yesterday" in ids)
    check("6d-old pending capture is IN (daily section owns everything under the pin)",
          "cmt_6d" in ids)
    check("12d unconfirmed is OUT of the daily section (the pin owns it)",
          "cmt_12d" not in ids)
    check("selector returns exactly the five amber rows", len(rows) == 5,
          str(sorted(ids)))
    by_id = {r["commitment_id"]: r for r in rows}
    check("classes name pending_review",
          by_id["cmt_pending"]["classes"] == ["pending_review"])
    check("classes name unowned", "unowned" in by_id["cmt_unowned"]["classes"])
    check("duplicate row carries the flagged target + score",
          by_id["cmt_dup"]["suspected_duplicate_of"] == "cmt_survivor"
          and by_id["cmt_dup"]["suspected_duplicate_score"] == 0.86)
    check("rows sort oldest first",
          [r["commitment_id"] for r in rows]
          == ["cmt_6d", "cmt_yesterday", "cmt_unowned", "cmt_pending", "cmt_dup"])
    # dismissed_ids quiets a snoozed row
    rows_muted = select_confirm_items(opens, NOW_ISO, dismissed_ids={"cmt_dup"})
    check("a live-muted row stays quiet for its TTL",
          "cmt_dup" not in {r["commitment_id"] for r in rows_muted})

    # ------------------------------------------------------------------
    print("[2] verb round-trips through the loader")
    # ------------------------------------------------------------------
    # Mine: owner=user, pending_review cleared, unconfirmed bucket shrinks
    before = count_commitments(opens, user_person_id="user_1", now_iso=NOW_ISO)
    r = confirm_commitment_owner(ws, "cmt_pending", owner_id="user_1",
                                 confirmed_by="user_1", source_skill="commitments")
    check("Mine returns confirmed via commitment_updated",
          r["status"] == "confirmed" and r["event"]["type"] == "commitment_updated")
    opens = load_open_commitments(ep)
    by = {e["data"].get("id"): e for e in opens if e.get("data", {}).get("id")}
    check("Mine: projected owner is the user",
          _commitment_field(by["cmt_pending"], "owner_id") == "user_1")
    check("Mine: pending_review cleared on the projection",
          not _is_pending_review(by["cmt_pending"]))
    after = count_commitments(opens, user_person_id="user_1", now_iso=NOW_ISO)
    check("Mine: headline unconfirmed decremented",
          after["headline"]["unconfirmed"] == before["headline"]["unconfirmed"] - 1,
          f"{before['headline']['unconfirmed']} -> {after['headline']['unconfirmed']}")
    check("Mine: headline you_owe incremented",
          after["headline"]["you_owe"] == before["headline"]["you_owe"] + 1)

    # Theirs → [name]: S4 reassign with confirmed=True
    r = reassign_commitment(ws, "cmt_unowned", reassigned_by="user_1",
                            source_skill="commitments", new_owner_id="person_009",
                            new_owner_name="Dustin Stone",
                            reason="confirmed: theirs", confirmed=True)
    check("Theirs returns reassigned via commitment_reassigned",
          r["status"] == "reassigned" and r["event"]["type"] == "commitment_reassigned"
          and r["event"]["data"]["confirmed"] is True)
    opens = load_open_commitments(ep)
    by = {e["data"].get("id"): e for e in opens if e.get("data", {}).get("id")}
    check("Theirs: projected owner is the named person",
          _commitment_field(by["cmt_unowned"], "owner_id") == "person_009")
    check("Theirs: confirmed routing is NOT pending_review",
          not _is_pending_review(by["cmt_unowned"]))
    check("Theirs: item left the confirm section",
          "cmt_unowned" not in {x["commitment_id"]
                                for x in select_confirm_items(opens, NOW_ISO)})

    # Ordering: a later UNCONFIRMED reassignment re-stamps pending_review over Mine
    reassign_commitment(ws, "cmt_pending", reassigned_by="sweep",
                        source_skill="session-sweep", new_owner_id="person_001",
                        confirmed=False)
    opens = load_open_commitments(ep)
    by = {e["data"].get("id"): e for e in opens if e.get("data", {}).get("id")}
    check("later unconfirmed reassign re-stamps pending_review over Mine",
          _is_pending_review(by["cmt_pending"]))
    confirm_commitment_owner(ws, "cmt_pending", owner_id="user_1",
                             confirmed_by="user_1", source_skill="commitments")
    opens = load_open_commitments(ep)
    by = {e["data"].get("id"): e for e in opens if e.get("data", {}).get("id")}
    check("a later Mine wins back (latest adjudication decides)",
          not _is_pending_review(by["cmt_pending"])
          and _commitment_field(by["cmt_pending"], "owner_id") == "user_1")

    # Keep both: duplicate flags clear, both stay open
    r = clear_review_flags(ws, "cmt_dup", cleared_by="user_1",
                           source_skill="commitments")
    check("Keep both returns cleared", r["status"] == "cleared")
    opens = load_open_commitments(ep)
    by = {e["data"].get("id"): e for e in opens if e.get("data", {}).get("id")}
    check("Keep both: duplicate flag cleared on the projection",
          not by["cmt_dup"]["data"].get("suspected_duplicate_of")
          and not _is_pending_review(by["cmt_dup"]))
    check("Keep both: both items still open",
          "cmt_dup" in by and "cmt_survivor" in by)

    # Merge (fresh duplicate): the suspect closes into the survivor w/ provenance
    dup2 = {"type": "commitment", "source_skill": "scan-for-commitments",
            "data": {"id": "cmt_dup2", "title": "send deck (email capture)",
                     "status": "open", "owner_id": "user_1", "kind": "promise",
                     "source_ref": "gmail:thread9", "pending_review": True,
                     "suspected_duplicate_of": "cmt_survivor",
                     "suspected_duplicate_score": 0.9}}
    from atomic_write import atomic_append_jsonl
    atomic_append_jsonl(ep, [dup2])
    r = supersede_commitment(ws, "cmt_survivor", "cmt_dup2", merged_by="user_1",
                             source_skill="commitments",
                             evidence="user merged from the confirm section",
                             user_confirmed=True)
    check("Merge returns superseded", r["status"] == "superseded")
    opens = load_open_commitments(ep)
    by = {e["data"].get("id"): e for e in opens if e.get("data", {}).get("id")}
    check("Merge: the duplicate closed", "cmt_dup2" not in by)
    check("Merge: survivor carries the absorbed source",
          "gmail:thread9" in (by["cmt_survivor"]["data"].get("merged_source_refs") or []))

    # Make task / Drop
    r = promote_task_to_commitment(ws, "cmt_dup", new_kind="task",
                                   source_skill="commitments",
                                   reason="user triaged to task")
    check("Make task reclassifies", r["status"] == "reclassified")
    r = close_commitment(ws, "cmt_yesterday", resolved_by="user_1",
                         evidence="confirm section: dropped",
                         source_skill="commitments", resolution="dropped",
                         user_confirmed=True)
    check("Drop closes a pending_review row with user_confirmed",
          r["status"] == "closed")
    opens = load_open_commitments(ep)
    by = {e["data"].get("id"): e for e in opens if e.get("data", {}).get("id")}
    check("Drop: item left the open set", "cmt_yesterday" not in by)

    # Promotion proposals: task+counterparty proposed; promote adjudicates
    promos = select_promotion_proposals(opens)
    promo_ids = {p["commitment_id"] for p in promos}
    check("task with a resolvable counterparty is proposed",
          "cmt_task_cp" in promo_ids)
    check("task without a counterparty is NOT proposed",
          "cmt_task_bare" not in promo_ids)
    check("Make-task'd duplicate (no counterparty) is NOT proposed",
          "cmt_dup" not in promo_ids)
    promote_task_to_commitment(ws, "cmt_task_cp", new_kind="promise",
                               source_skill="commitments",
                               reason="counterparty appeared — user promoted")
    opens = load_open_commitments(ep)
    check("promote adjudicates: the proposal drops out",
          "cmt_task_cp" not in
          {p["commitment_id"] for p in select_promotion_proposals(opens)})

    # ------------------------------------------------------------------
    print("[3] Same-as — alias written, future resolution uses it, tombstone retires the proposal")
    # ------------------------------------------------------------------
    props = load_open_person_proposals(ep)
    names = {p["name"] for p in props}
    check("open proposal re-surfaces regardless of age (9 days)",
          "Myra Samples" in names)
    check("tombstoned proposal (not_relevant) is retired",
          "Rick Passerby" not in names)
    proposal = next(p for p in props if p["name"] == "Myra Samples")

    from people_writer import add_person_alias
    r = add_person_alias(ws, "person_001", "Myra Samples",
                         source_skill="commitments")
    check("add_person_alias writes mapping + person record",
          r["status"] == "added" and r["mapping_written"] and r["record_written"])
    aliases_doc = json.loads((ws / "_hq" / "data" / "aliases.json")
                             .read_text(encoding="utf-8"))
    check("aliases.json carries the raw → canonical mapping",
          {"raw": "Myra Samples", "canonical_id": "person_001"}
          in aliases_doc["mappings"]["people"])
    from entity_resolve import resolve
    res = resolve(ws, "Myra Samples")
    check("future resolution: the raw spelling resolves to the person",
          res is not None and res.record["id"] == "person_001")
    check("re-adding the same alias is a no-op",
          add_person_alias(ws, "person_001", "myra samples")["status"] == "exists")
    try:
        add_person_alias(ws, "person_009", "Myra Samples")
        check("an alias mapped to a DIFFERENT person refuses to re-point", False)
    except ValueError:
        check("an alias mapped to a DIFFERENT person refuses to re-point", True)

    # tombstone: same_as retires the proposal
    from event_gate import append_event
    tomb = build_person_proposal_resolved_event(
        proposal["seq"], resolution="same_as", source_skill="commitments",
        person_id="person_001", alias="Myra Samples")
    append_event(ep, [tomb], holder="commitments")
    check("same_as tombstone retires the proposal",
          "Myra Samples" not in
          {p["name"] for p in load_open_person_proposals(ep)})
    try:
        build_person_proposal_resolved_event(1, resolution="maybe",
                                             source_skill="x")
        check("invalid resolution rejected", False)
    except ValueError:
        check("invalid resolution rejected", True)

    # ------------------------------------------------------------------
    print("[4] escalation — 7d pin, 30d propose-drop, confirmed items exempt")
    # ------------------------------------------------------------------
    ws2 = make_workspace(FIXTURE)
    opens2 = load_open_commitments(events_path(ws2))
    esc = select_unconfirmed_escalation(opens2, NOW_ISO)
    pin_ids = {r["commitment_id"] for r in esc["pin"]}
    drop_ids = {r["commitment_id"] for r in esc["propose_drop"]}
    check("12d unconfirmed pins", "cmt_12d" in pin_ids)
    check("45d unconfirmed pins AND proposes drop",
          "cmt_45d" in pin_ids and "cmt_45d" in drop_ids)
    check("12d pin does NOT propose drop", "cmt_12d" not in drop_ids)
    check("45d CONFIRMED item is exempt from both",
          "cmt_45d_ok" not in pin_ids and "cmt_45d_ok" not in drop_ids)
    check("fresh unconfirmed captures do not pin",
          "cmt_pending" not in pin_ids and "cmt_unowned" not in pin_ids)
    check("pin rows carry days_unconfirmed",
          all(isinstance(r.get("days_unconfirmed"), int) for r in esc["pin"]))
    check("pin sorts oldest first",
          [r["commitment_id"] for r in esc["pin"]] == ["cmt_45d", "cmt_12d"])

    # ------------------------------------------------------------------
    print("[5] pointer line — renders only when non-empty")
    # ------------------------------------------------------------------
    check("empty confirm set → no line", confirm_pointer_line(0) is None)
    check("negative/garbage → no line", confirm_pointer_line(-3) is None)
    one = confirm_pointer_line(1)
    many = confirm_pointer_line(3)
    check("singular reads naturally",
          one == "1 item needs a 10-second confirm — it's in your Commitments chat.")
    check("plural carries the count",
          many == "3 items need a 10-second confirm — they're in your Commitments chat.")

    # ------------------------------------------------------------------
    print("[6] FS-19 — suppress_on_file drops already-a-contact add rows; "
          "sweep default keeps them; first-name/new/update untouched")
    # ------------------------------------------------------------------
    # entities carry person_001 "Mira Sample" + person_009 "Dustin Stone".
    ws6 = make_workspace([
        # full-name exact match to person_001 → confident (Tier 2)
        {"seq": 1, "ts": iso(days_ago=2), "type": "person_proposal",
         "source_skill": "meeting-notes",
         "data": {"inferred_name": "Mira Sample", "role": "advisor",
                  "source": "named again on a later call"}},
        # lone first name overlapping "Mira Sample" → ambiguous (Tier 3)
        {"seq": 2, "ts": iso(days_ago=2), "type": "person_proposal",
         "source_skill": "meeting-notes",
         "data": {"inferred_name": "Mira", "role": "vendor",
                  "source": "a different Mira on a new thread"}},
        # full name NOT on file → no match
        {"seq": 3, "ts": iso(days_ago=2), "type": "person_proposal",
         "source_skill": "inbox-triage",
         "data": {"proposed_name": "Dana Newperson", "source": "new sender"}},
        # update-type referencing an existing person → never suppressed
        {"seq": 4, "ts": iso(days_ago=2), "type": "person_update_proposal",
         "source_skill": "people-crm",
         "data": {"person_id": "person_001",
                  "proposed_delta": {"role": "director"},
                  "note": "title change spotted in a signature",
                  "source_ref": "mail:t9"}},
    ])
    ep6 = events_path(ws6)
    default_names = {p["name"] for p in load_open_person_proposals(ep6)}
    supp = load_open_person_proposals(ep6, suppress_on_file=True)
    supp_names = {p["name"] for p in supp}
    check("default read (sweep posture) keeps the on-file full name",
          "Mira Sample" in default_names)
    check("suppress_on_file drops the on-file full name",
          "Mira Sample" not in supp_names, repr(supp_names))
    check("suppress_on_file keeps a lone first name (Tier-3 ambiguity)",
          "Mira" in supp_names)
    check("suppress_on_file keeps a genuinely-new full name",
          "Dana Newperson" in supp_names)
    check("suppress_on_file never drops an update-type proposal",
          any(p["type"] == "person_update_proposal" for p in supp))
    from confirm_flow import person_name_on_file
    check("person_name_on_file: full name → True",
          person_name_on_file(ws6, "Mira Sample") is True)
    check("person_name_on_file: lone first name → False (Bug #19)",
          person_name_on_file(ws6, "Mira") is False)
    check("person_name_on_file: unknown name → False",
          person_name_on_file(ws6, "Dana Newperson") is False)
    check("person_name_on_file: no workspace → False (fail-open)",
          person_name_on_file(None, "Mira Sample") is False)

    # ------------------------------------------------------------------
    print("[D8] PID1 — seq-less proposals adjudicate by content fingerprint")
    # ------------------------------------------------------------------
    from confirm_flow import compute_proposal_fingerprint

    seqless_ts = iso(days_ago=3.0)
    ws7 = make_workspace([
        {"seq": 1, "type": "person_proposal", "ts": iso(days_ago=1.0),
         "source_skill": "meeting-notes",
         "data": {"name": "Ada West", "evidence": "x", "source_ref": "mail:t"}},
        # the freelance-written seq:null shape — unadjudicatable pre-D8
        {"seq": None, "type": "person_proposal", "ts": seqless_ts,
         "source_skill": "freelance",
         "data": {"name": "Pia Voss", "evidence": "y",
                  "source_ref": "mail:t2"}},
    ])
    ep7 = events_path(ws7)
    rows7 = {p["name"]: p for p in load_open_person_proposals(ep7)}
    check("int-seq row carries NO fingerprint (activation rule)",
          rows7["Ada West"]["fingerprint"] is None)
    fp = rows7["Pia Voss"]["fingerprint"]
    check("seq-less row carries the computed fingerprint",
          fp == compute_proposal_fingerprint("person_proposal", "Pia Voss",
                                            seqless_ts), repr(fp))
    check("fingerprint is stable + normalized (whitespace/case)",
          compute_proposal_fingerprint("person_proposal", "  pia   VOSS ",
                                       seqless_ts) == fp)
    # builder: seq path unchanged; fingerprint path activates on None only
    tomb = build_person_proposal_resolved_event(
        None, resolution="not_relevant", source_skill="test",
        proposal_fingerprint=fp)
    check("builder emits data.proposal_fingerprint for a seq-less row",
          tomb["data"]["proposal_fingerprint"] == fp
          and "proposal_seq" not in tomb["data"])
    try:
        build_person_proposal_resolved_event(
            None, resolution="not_relevant", source_skill="test")
        check("builder refuses None seq with no fingerprint", False)
    except ValueError:
        check("builder refuses None seq with no fingerprint", True)
    import json as _json
    with open(ep7, "a", encoding="utf-8") as f:
        f.write(_json.dumps({"seq": 3, "ts": iso(), **{k: tomb[k] for k in
                            ("type", "source_skill", "data")}}) + "\n")
    names_after = {p["name"] for p in load_open_person_proposals(ep7)}
    check("fingerprint tombstone excludes the seq-less row",
          names_after == {"Ada West"}, repr(names_after))
    check("int-seq row untouched by the fingerprint fold",
          "Ada West" in names_after)
    with open(ep7, "a", encoding="utf-8") as f:
        f.write(_json.dumps({"seq": 4, "ts": iso(),
                             "type": "person_proposal_reopened",
                             "source_skill": "brain_undo",
                             "data": {"proposal_fingerprint": fp}}) + "\n")
    names_after = {p["name"] for p in load_open_person_proposals(ep7)}
    check("a fingerprint reopen lifts the tombstone (last writer wins)",
          "Pia Voss" in names_after, repr(names_after))

    # ------------------------------------------------------------------
    # WG1-B D-B3 — the loader is shape-BLIND by design: an org-shaped
    # person_proposal (the TDX-Arena row class) passes through UNCHANGED;
    # the org-shape gate lives adapter-side (brain_proposals), and
    # suppress_on_file semantics are untouched by it.
    ws2 = Path(tempfile.mkdtemp())
    (ws2 / "_hq" / "data").mkdir(parents=True)
    (ws2 / "_hq" / "data" / "entities.json").write_text(
        json.dumps({"version": 1, "people": [], "orgs": [
            {"id": "org_101", "canonical_name": "Vertex Range (AcademyCo)"},
        ]}), encoding="utf-8")
    ev_path = ws2 / "_hq" / "data" / "events.jsonl"
    now2 = dt.datetime.now(dt.timezone.utc)
    ev_path.write_text(json.dumps({
        "seq": 1, "ts": (now2 - dt.timedelta(days=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "type": "person_proposal", "source_skill": "past-meetings",
        "data": {"name": "Vertex Range (AcademyCo)", "pending_review": True,
                 "source_ref": "granola:00000000-mirror-tdx"}}) + "\n",
        encoding="utf-8")
    rows_plain = load_open_person_proposals(ev_path)
    check("D-B3: loader passes an org-shaped row through unchanged",
          len(rows_plain) == 1
          and rows_plain[0]["name"] == "Vertex Range (AcademyCo)"
          and rows_plain[0]["inferred_role"] is None,
          repr(rows_plain))
    rows_sup = load_open_person_proposals(ev_path, suppress_on_file=True)
    check("D-B3: suppress_on_file is untouched by the org gate "
          "(an org name is not a person on file)",
          len(rows_sup) == 1, repr(rows_sup))

    # ------------------------------------------------------------------
    print(f"\n=== Summary: {PASS} passed, {FAIL} failed ===")
    if FAIL:
        return 1
    print("OK — W4b confirm-flow battery ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
