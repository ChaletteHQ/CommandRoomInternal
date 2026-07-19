#!/usr/bin/env python3
"""The writer-side account-scope wall (R2/R3) — the load-bearing privacy test.

Proves ACCOUNT_SCOPE §4 end-to-end through the REAL append path
(atomic_append_jsonl): a personal-tagged account's mail provably cannot enter
events.jsonl, while empty-map / unknown-account / user-stated events pass
unchanged. This is Phase-6 gate #4 evidence, run every release.

Real-shaped fixtures: account records mirror the compound ACCOUNT_SCOPE §1 shape;
provenance mirrors the structured {connector, provider, native_id, account_id}
form and the legacy gmail:<id> string.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import account_scope_gate as asg  # noqa: E402
import connector_config as cc  # noqa: E402
from atomic_write import atomic_append_jsonl  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _ws(populated=True):
    ws = Path(tempfile.mkdtemp(prefix="asg_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    ent = {"version": 1, "entities": {"people": [], "workspace": {}}}
    if populated:
        ent["entities"]["workspace"]["accounts"] = [
            {"address": "owner@example.com", "account_id": "acct_biz",
             "role": "business-primary", "scope": {"surface": "on", "write_to_business": "on"}},
            {"address": "me@personal.example.com", "account_id": "acct_pers",
             "role": "personal", "scope": {"surface": "on", "write_to_business": "off"}},
            {"address": "me.side@mixed.example.com", "account_id": "acct_mixed",
             "role": "mixed",
             "overrides": {"senders": {
                 "vip@client.example.com": {"write_to_business": "on"}}}},
        ]
    (ws / "_hq" / "data" / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    return ws


def _events(ws):
    p = ws / "_hq" / "data" / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---- module-level enforce_scope (unit) ----------------------------------------

def test_empty_map_noop():
    ws = _ws(populated=False)
    ev = {"type": "interaction", "data": {"provenance": {"provider": "gmail", "native_id": "m1", "account_id": "acct_pers"}}}
    out = asg.enforce_scope([ev], workspace_root=ws)
    check("empty map: no-op (event passes)", out == [ev])


def test_personal_out_of_scope_rejected():
    ws = _ws()
    ev = {"type": "interaction", "data": {"summary": "personal note",
          "provenance": {"provider": "gmail", "native_id": "m1", "account_id": "acct_pers"}}}
    raised = False
    try:
        asg.enforce_scope([ev], workspace_root=ws)
    except asg.AccountScopeError:
        raised = True
    check("personal interaction REJECTED at the wall", raised)


def test_business_in_scope_passes():
    ws = _ws()
    ev = {"type": "interaction", "data": {"provenance": {"provider": "superhuman", "native_id": "t1", "account_id": "acct_biz"}}}
    out = asg.enforce_scope([ev], workspace_root=ws)
    check("business interaction passes the wall", out == [ev])


def test_provenance_required_absent_rejected():
    # R2 fail-open fix: a provenance-required family with NO provenance rejects
    # (an LLM dropping source_ref can't silently bypass the wall).
    ws = _ws()
    ev = {"type": "interaction", "data": {"summary": "no provenance"}}
    raised = False
    try:
        asg.enforce_scope([ev], workspace_root=ws)
    except asg.AccountScopeError:
        raised = True
    check("R2: provenance-required family w/ no provenance REJECTED", raised)


def test_user_stated_commitment_exempt():
    # A user-typed commitment (no connector source_ref) is provenance-OPTIONAL.
    ws = _ws()
    ev = {"type": "commitment", "data": {"title": "call the vet", "kind": "task"}}
    out = asg.enforce_scope([ev], workspace_root=ws)
    check("user-stated commitment is exempt (passes)", out == [ev])


def test_decision_note_exempt():
    ws = _ws()
    evs = [{"type": "decision", "data": {"summary": "go with plan B"}},
           {"type": "note", "data": {"summary": "misc"}}]
    out = asg.enforce_scope(evs, workspace_root=ws)
    check("decisions/notes exempt (provenance-optional)", out == evs)


def test_connector_commitment_out_of_scope_rejected():
    ws = _ws()
    ev = {"type": "commitment", "data": {"title": "reply to spouse", "kind": "promise",
          "provenance": {"provider": "gmail", "native_id": "m2", "account_id": "acct_pers"}}}
    raised = False
    try:
        asg.enforce_scope([ev], workspace_root=ws)
    except asg.AccountScopeError:
        raised = True
    check("connector-derived commitment from personal acct REJECTED", raised)


def test_unresolvable_account_passes():
    # Legacy row: gmail:<id> string, no account_id, no address -> in scope.
    ws = _ws()
    ev = {"type": "interaction", "data": {"source_ref": "gmail:legacy1"}}
    out = asg.enforce_scope([ev], workspace_root=ws)
    check("legacy unresolvable-account row passes (back-compat)", out == ev if False else out == [ev])


# ---- origin discriminator (review fixes 5/6) ----------------------------------

def test_manual_meeting_passes_on_classified_workspace():
    # Fix 6: a provenance-less manual / end-session meeting log must write fine
    # on a workspace with a populated account map.
    ws = _ws()
    ev = {"type": "meeting", "data": {"title": "weekly sync", "summary": "manual log"}}
    out = asg.enforce_scope([ev], workspace_root=ws)
    check("fix6: manual provenance-less meeting passes on classified workspace", out == [ev])


def test_connector_meeting_absent_provenance_rejected():
    # A meeting STAMPED connector-origin with no provenance is the R2 hole -> strict.
    ws = _ws()
    ev = {"type": "meeting", "data": {"title": "synced event", "origin": "connector"}}
    raised = False
    try:
        asg.enforce_scope([ev], workspace_root=ws)
    except asg.AccountScopeError:
        raised = True
    check("fix6: connector-origin meeting w/o provenance REJECTED (strict)", raised)


def test_connector_meeting_out_of_scope_rejected():
    ws = _ws()
    ev = {"type": "meeting", "data": {"title": "personal appt",
          "provenance": {"provider": "gcal", "native_id": "e1", "account_id": "acct_pers"}}}
    raised = False
    try:
        asg.enforce_scope([ev], workspace_root=ws)
    except asg.AccountScopeError:
        raised = True
    check("connector meeting from personal acct REJECTED (scope)", raised)


def test_connector_origin_commitment_absent_provenance_rejected():
    # Fix 5: origin=="connector" makes commitments STRICT — dropping source_ref
    # can no longer bypass the wall for stamped producers.
    ws = _ws()
    ev = {"type": "commitment", "data": {"title": "send the deck", "kind": "promise",
                                          "origin": "connector"}}
    raised = False
    try:
        asg.enforce_scope([ev], workspace_root=ws)
    except asg.AccountScopeError:
        raised = True
    check("fix5: connector-origin commitment w/o provenance REJECTED (strict)", raised)


def test_user_stated_origin_commitment_exempt():
    # Fix 5: an explicit user_stated origin is exempt even if a provider-ish
    # source_ref is present (the user pasted a link, say).
    ws = _ws()
    ev = {"type": "commitment", "data": {"title": "call the vet", "kind": "task",
                                          "origin": "user_stated",
                                          "source_ref": "gmail:pasted1"}}
    out = asg.enforce_scope([ev], workspace_root=ws)
    check("fix5: user_stated-origin commitment exempt (passes)", out == [ev])


def test_absent_origin_connector_commitment_legacy_sniff():
    # Fix 5 staging: absent origin + connector provenance = today's scope_only
    # sniff (rejected when out-of-scope) — producers lag, no hard reject on
    # absent provenance.
    ws = _ws()
    no_prov = {"type": "commitment", "data": {"title": "x", "kind": "task"}}
    out = asg.enforce_scope([no_prov], workspace_root=ws)
    check("fix5 staging: absent-origin commitment w/o provenance still passes", out == [no_prov])


# ---- promote-queue + business-by-association (R8, Part 2B) --------------------

def test_mixed_unknown_sender_rejected():
    # A mixed account files by association ONLY — an unknown-sender interaction
    # stays walled (it routes to the promote-queue instead).
    ws = _ws()
    ev = {"type": "interaction", "data": {"summary": "cold inbound",
          "from": "stranger@nowhere.example.com",
          "provenance": {"provider": "gmail", "native_id": "mx1", "account_id": "acct_mixed"}}}
    raised = False
    try:
        asg.enforce_scope([ev], workspace_root=ws)
    except asg.AccountScopeError:
        raised = True
    check("R8: mixed-account unknown-sender interaction REJECTED (walled)", raised)


def test_mixed_association_passes():
    # ...but an event referencing a resolved entity is business-by-association.
    ws = _ws()
    ev = {"type": "interaction", "person_ids": ["person_007"],
          "data": {"summary": "known contact",
          "provenance": {"provider": "gmail", "native_id": "mx2", "account_id": "acct_mixed"}}}
    out = asg.enforce_scope([ev], workspace_root=ws)
    check("R8: mixed-account known-entity interaction passes (by association)", out == [ev])


def test_sender_override_passes():
    # A per-sender write_to_business:on override puts that sender in scope.
    ws = _ws()
    ev = {"type": "interaction", "data": {"summary": "vip mail",
          "from": "vip@client.example.com",
          "provenance": {"provider": "gmail", "native_id": "mx3", "account_id": "acct_mixed"}}}
    out = asg.enforce_scope([ev], workspace_root=ws)
    check("R8: per-sender override (write on) passes the wall", out == [ev])


def test_sender_override_setter_roundtrip():
    ws = _ws()
    cc.set_sender_scope_override(ws, "me@personal.example.com",
                                 "school@district.example.com",
                                 write_to_business=True, reason="kids' school")
    ev = {"type": "interaction", "data": {"summary": "school mail",
          "from": "school@district.example.com",
          "provenance": {"provider": "gmail", "native_id": "p77", "account_id": "acct_pers"}}}
    out = asg.enforce_scope([ev], workspace_root=ws)
    check("R8/2C: setter-written sender override honored by the wall", out == [ev])
    # A different personal sender is still walled.
    ev2 = {"type": "interaction", "data": {"summary": "other personal",
           "from": "aunt@family.example.com",
           "provenance": {"provider": "gmail", "native_id": "p78", "account_id": "acct_pers"}}}
    raised = False
    try:
        asg.enforce_scope([ev2], workspace_root=ws)
    except asg.AccountScopeError:
        raised = True
    check("R8/2C: non-overridden personal sender still walled", raised)


def test_promote_queue_proposal_exempt():
    # The propose-then-confirm surface must be writable for out-of-scope
    # accounts — that's what it reviews (ACCOUNT_SCOPE §8).
    ws = _ws()
    ev = {"type": "person_proposal", "data": {
        "name": "New Contact", "email": "stranger@nowhere.example.com",
        "promote_queue": True, "origin": "connector",
        "provenance": {"provider": "gmail", "native_id": "mx9", "account_id": "acct_mixed"}}}
    out = asg.enforce_scope([ev], workspace_root=ws)
    check("R8: promote-queue proposal exempt (writable for walled accounts)", out == [ev])
    # A NON-promote-queue enrichment event with out-of-scope provenance still rejects.
    ev2 = {"type": "person_proposal", "data": {
        "name": "Aunt", "provenance": {"provider": "gmail", "native_id": "p9",
                                       "account_id": "acct_pers"}}}
    raised = False
    try:
        asg.enforce_scope([ev2], workspace_root=ws)
    except asg.AccountScopeError:
        raised = True
    check("R8: non-promote-queue enrichment from personal acct still REJECTED", raised)


# ---- end-to-end through the REAL append path (gate #4) ------------------------

def test_e2e_personal_cannot_enter_events_jsonl():
    ws = _ws()
    ep = ws / "_hq" / "data" / "events.jsonl"
    # A business interaction lands.
    atomic_append_jsonl(ep, [{"type": "interaction", "data": {"summary": "biz",
        "provenance": {"provider": "superhuman", "native_id": "t9", "account_id": "acct_biz"}}}])
    check("business interaction written to events.jsonl", len(_events(ws)) == 1)
    # A personal interaction is REJECTED by the append path itself.
    raised = False
    try:
        atomic_append_jsonl(ep, [{"type": "interaction", "data": {"summary": "personal",
            "provenance": {"provider": "gmail", "native_id": "p9", "account_id": "acct_pers"}}}])
    except Exception as e:
        raised = "scope" in str(e).lower() or e.__class__.__name__ == "AccountScopeError"
    check("gate #4: personal interaction REJECTED by the real append path", raised)
    check("gate #4: personal event provably absent from events.jsonl", len(_events(ws)) == 1)


def test_e2e_empty_map_zero_change():
    # Gate #7: empty account map = zero behavior change through the real path.
    ws = _ws(populated=False)
    ep = ws / "_hq" / "data" / "events.jsonl"
    atomic_append_jsonl(ep, [{"type": "interaction", "data": {"summary": "anything",
        "provenance": {"provider": "gmail", "native_id": "x", "account_id": "acct_whatever"}}}])
    check("gate #7: empty-map interaction writes normally", len(_events(ws)) == 1)


def test_e2e_entities_absent_wall_noop():
    # Never-brick (review should-fix): entities.json ABSENT -> the wall no-ops
    # and writes proceed through the real append path.
    ws = Path(tempfile.mkdtemp(prefix="asg_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    ep = ws / "_hq" / "data" / "events.jsonl"
    atomic_append_jsonl(ep, [{"type": "interaction", "data": {"summary": "x",
        "provenance": {"provider": "gmail", "native_id": "a1", "account_id": "acct_pers"}}}])
    check("never-brick: entities.json ABSENT -> wall no-ops, write proceeds",
          len(_events(ws)) == 1)


def test_e2e_entities_corrupt_wall_noop():
    # Never-brick (review should-fix): entities.json UNPARSEABLE -> the wall
    # no-ops and writes proceed (a broken map must never brick the substrate).
    ws = Path(tempfile.mkdtemp(prefix="asg_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "entities.json").write_text("{not json!!", encoding="utf-8")
    ep = ws / "_hq" / "data" / "events.jsonl"
    atomic_append_jsonl(ep, [{"type": "interaction", "data": {"summary": "x",
        "provenance": {"provider": "gmail", "native_id": "a2", "account_id": "acct_pers"}}}])
    check("never-brick: entities.json CORRUPT -> wall no-ops, write proceeds",
          len(_events(ws)) == 1)


# ---- reader-honor for scope masks (R5, Part 2D — resolves review fix 2) -------

def test_masked_history_invisible_and_restorable():
    import json as _j
    from cru_match import load_open_commitments
    ws = _ws()
    ep = ws / "_hq" / "data" / "events.jsonl"
    # History: one business commitment + one commitment attributed to the
    # (then-business) account that later gets reclassified personal.
    rows = [
        {"seq": 1, "type": "commitment", "data": {
            "id": "c_biz", "status": "open", "kind": "task", "title": "ship the deck",
            "provenance": {"provider": "superhuman", "native_id": "b1",
                           "account_id": "acct_biz"}}},
        {"seq": 2, "type": "commitment", "data": {
            "id": "c_pers", "status": "open", "kind": "task", "title": "family thing",
            "provenance": {"provider": "gmail", "native_id": "p1",
                           "account_id": "acct_pers"}}},
    ]
    ep.write_text("".join(_j.dumps(r) + "\n" for r in rows), encoding="utf-8")
    ids = {(c.get("data") or {}).get("id") for c in load_open_commitments(str(ep))}
    check("R5 baseline: both commitments visible before any mask",
          ids == {"c_biz", "c_pers"})
    # business→personal flip appends the IN-PLACE mask (rows never move).
    with ep.open("a", encoding="utf-8") as f:
        f.write(_j.dumps({"seq": 3, "type": "account_scope_masked", "data": {
            "address": "me@personal.example.com", "masked_account_id": "acct_pers",
            "reason": "reclassified personal"}}) + "\n")
    ids = {(c.get("data") or {}).get("id") for c in load_open_commitments(str(ep))}
    check("R5: masked account's historical rows invisible to the projector",
          ids == {"c_biz"})
    check("R5: rows were NOT physically moved (still on disk)",
          sum(1 for l in ep.read_text(encoding="utf-8").splitlines() if l.strip()) == 3)
    # restore un-hides.
    with ep.open("a", encoding="utf-8") as f:
        f.write(_j.dumps({"seq": 4, "type": "account_scope_restored", "data": {
            "address": "me@personal.example.com", "masked_account_id": "acct_pers",
            "reason": "restored"}}) + "\n")
    ids = {(c.get("data") or {}).get("id") for c in load_open_commitments(str(ep))}
    check("R5: account_scope_restored un-hides the history",
          ids == {"c_biz", "c_pers"})


def test_mask_matches_address_derived_rows():
    # A mask must also catch rows carrying only data.account_address (no
    # explicit account_id) — both spellings reduce via derive_account_id.
    import account_scope_gate as g
    evs = [
        {"type": "interaction", "data": {
            "summary": "x", "account_address": "Me@Personal.example.com"}},
        {"type": "interaction", "data": {"summary": "y"}},
        {"type": "account_scope_masked", "data": {
            "address": "me@personal.example.com"}},
    ]
    out = g.filter_masked_events(evs)
    kept = [e for e in out if e.get("type") == "interaction"]
    check("R5: address-derived row masked (case-insensitive), unattributed row kept",
          len(kept) == 1 and kept[0]["data"]["summary"] == "y")


def test_live_masks_never_brick():
    import account_scope_gate as g
    check("R5 never-brick: live_masks on a bare temp dir -> empty set",
          g.live_masks(Path(tempfile.mkdtemp(prefix="asg_"))) == frozenset())
    check("R5 never-brick: filter with junk rows returns them unfiltered",
          g.filter_masked_events([{"type": "interaction"}, "junk", None])
          == [{"type": "interaction"}, "junk", None])


# ---- the CRM record wall (review fix 7) ---------------------------------------

def test_record_wall_personal_provenance_rejected():
    ws = _ws()
    import people_writer as pw
    raised = False
    try:
        pw.create_person(ws, canonical_name="Pat Example",
                         provenance={"provider": "gmail", "native_id": "m7",
                                     "account_id": "acct_pers"},
                         source_skill="test")
    except asg.AccountScopeError:
        raised = True
    check("fix7: personal-provenance create_person REJECTED before write", raised)
    ent = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    check("fix7: rejected person absent from entities.json",
          len(ent["entities"]["people"]) == 0)


def test_record_wall_account_address_rejected():
    ws = _ws()
    import people_writer as pw
    raised = False
    try:
        pw.create_person(ws, canonical_name="Sam Example",
                         account_address="me@personal.example.com",
                         source_skill="test")
    except asg.AccountScopeError:
        raised = True
    check("fix7: personal account_address create_person REJECTED", raised)


def test_record_wall_manual_create_passes():
    ws = _ws()
    import people_writer as pw
    rec = pw.create_person(ws, canonical_name="Manny Example", source_skill="test")
    check("fix7: manual create_person (no provenance) passes on classified workspace",
          rec.get("id", "").startswith("person_"))


def test_record_wall_business_provenance_passes():
    ws = _ws()
    import people_writer as pw
    rec = pw.create_person(ws, canonical_name="Bea Example",
                           provenance={"provider": "superhuman", "native_id": "t3",
                                       "account_id": "acct_biz"},
                           source_skill="test")
    check("fix7: business-provenance create_person passes", bool(rec.get("id")))


def test_record_wall_corrupt_map_passes():
    # Never-brick: corrupt entities.json -> record wall no-ops. (create_person
    # itself will fail to load a corrupt entities.json downstream, so exercise
    # the wall function directly.)
    ws = Path(tempfile.mkdtemp(prefix="asg_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "entities.json").write_text("{not json!!", encoding="utf-8")
    try:
        asg.enforce_record_scope(ws, provenance={"provider": "gmail",
                                                 "native_id": "m9",
                                                 "account_id": "acct_pers"},
                                 holder="test")
        ok = True
    except Exception:
        ok = False
    check("fix7 never-brick: corrupt map -> record wall no-ops", ok)


def test_record_wall_org_writer_wired():
    ws = _ws()
    import org_writer as ow
    raised = False
    try:
        ow.create_org(ws, canonical_name="Family Trust",
                      provenance={"provider": "gmail", "native_id": "m8",
                                  "account_id": "acct_pers"},
                      source_skill="test")
    except asg.AccountScopeError:
        raised = True
    check("fix7: personal-provenance create_org REJECTED", raised)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for fn in [
        test_empty_map_noop, test_personal_out_of_scope_rejected, test_business_in_scope_passes,
        test_provenance_required_absent_rejected, test_user_stated_commitment_exempt,
        test_decision_note_exempt, test_connector_commitment_out_of_scope_rejected,
        test_unresolvable_account_passes,
        test_manual_meeting_passes_on_classified_workspace,
        test_connector_meeting_absent_provenance_rejected,
        test_connector_meeting_out_of_scope_rejected,
        test_connector_origin_commitment_absent_provenance_rejected,
        test_user_stated_origin_commitment_exempt,
        test_absent_origin_connector_commitment_legacy_sniff,
        test_mixed_unknown_sender_rejected, test_mixed_association_passes,
        test_sender_override_passes, test_sender_override_setter_roundtrip,
        test_promote_queue_proposal_exempt,
        test_e2e_personal_cannot_enter_events_jsonl,
        test_e2e_empty_map_zero_change,
        test_e2e_entities_absent_wall_noop, test_e2e_entities_corrupt_wall_noop,
        test_masked_history_invisible_and_restorable,
        test_mask_matches_address_derived_rows, test_live_masks_never_brick,
        test_record_wall_personal_provenance_rejected,
        test_record_wall_account_address_rejected,
        test_record_wall_manual_create_passes,
        test_record_wall_business_provenance_passes,
        test_record_wall_corrupt_map_passes,
        test_record_wall_org_writer_wired,
    ]:
        fn()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL account_scope_gate tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
