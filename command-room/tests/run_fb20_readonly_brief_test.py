#!/usr/bin/env python3
"""FB-20 + FB-19 — the read-only brief, the money carve-out, and the staff
meeting as the sole adjudication surface (M's rulings, 2026-07-16).

WHAT THIS PINS
--------------
FB-20 — "the morning brief should just be a morning brief":
  [1] money prose — deal proposals surface as ONE sentence each, propose-only
      (no verbs), drop-empty, capped without lying
  [2] pointer honesty — the count the brief promises IS what the staff
      meeting renders (same projector, same filters). An over-promising
      pointer is its own dishonesty.
FB-19 — the card must earn the trip now that it is the only door:
  [3] hygiene rows state their ask and carry verbs, or do not render
  [4] held items are suppressed until answered or 14d expiry
  [5] consequence floor — nothing consequential → no card at all
FB-20 part 2 — cadence:
  [6] staff-meeting defaults Mon/Wed/Fri for NEW installs; existing
      registrations are PROPOSED the change, never silently rewritten

Fixtures mirror the real substrate shapes the live 2026-07-16 fire produced:
the bare "Housekeeping — matched your sent message X" review row (title
dropped by the writer) and the two rows parked in chat that re-rendered
anyway. Synthetic names only (Northwind / Sam Sample / Q3 draft).

G14: every fixture timestamp is computed relative to today.

House convention: non-zero exit = fail.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def _iso(days_ago: float) -> str:
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _future_iso(days_ahead: float) -> str:
    return (dt.datetime.now(dt.timezone.utc)
            + dt.timedelta(days=days_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _deal(i: int, kind: str = "deal_creation", name: str = None) -> dict:
    return {
        "seq": 100 + i, "ts": _iso(2 + i), "type": "brain_proposal",
        "source_skill": "cr-brain",
        "data": {
            "proposal_id": f"bp_{i}", "kind": kind, "detector": "deal_signal",
            "tier": "confirm", "fingerprint": f"fp_{i}",
            "title": name or f"Northwind {i}",
            "render_line": "likely deal · proposal language in sent mail",
            "action_tuples": [{"action": "confirm proposal"},
                              {"action": "dismiss proposal"}],
            "org_id": f"org_{i}",
        },
    }


def make_ws(events: list[dict]) -> Path:
    ws = Path(tempfile.mkdtemp(prefix="fb20_"))
    dd = ws / "_hq" / "data"
    dd.mkdir(parents=True, exist_ok=True)
    with open(dd / "events.jsonl", "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    (dd / "entities.json").write_text(json.dumps({
        "persons": [{"id": "person:001", "canonical_name": "Sam Sample",
                     "is_primary_user": True}],
        "orgs": [], "threads": [],
    }), encoding="utf-8")
    return ws


# The live 2026-07-16 shape: a review proposal whose writer dropped the title.
def _review(cid: str, subject: str, day: str, *, with_title: str = None,
            score: float = 0.42) -> dict:
    data = {
        "commitment_id": cid, "proposed_resolution": "auto_resolve",
        "match_score": score,
        "evidence": f'matched your sent message "{subject}" ({day})',
        "ttl_days": 14,
    }
    if with_title is not None:
        data["title"] = with_title
    return {"seq": 200 + len(cid), "ts": _iso(1),
            "type": "commitment_review_proposed",
            "source_skill": "reconcile-sent", "primary_thread_id": "",
            "data": data}


def _commitment(cid: str, title: str) -> dict:
    return {"seq": 300 + len(cid), "ts": _iso(20), "type": "commitment",
            "source_skill": "meeting-notes",
            "data": {"id": cid, "title": title, "owner_id": "person:001",
                     "kind": "promise"}}


def main() -> int:
    from brain_proposals import (build_card_view, load_open_proposals,
                                 money_prose_lines, rank_proposals)
    from surface_drivers import MONEY_PROSE_CAP, build_morning_brief_pack

    # ---- [1] money prose ---------------------------------------------------
    print("[1] money carve-out — prose, propose-only, drop-empty")

    ws = make_ws([_commitment("cmt_a", "Send the Q3 draft"),
                  _deal(1), _deal(2)])
    pack = build_morning_brief_pack(ws, mode="scheduled")
    lines = pack["money_lines"]
    check("a deal proposal produces exactly one sentence", len(lines) == 2,
          f"got {lines}")
    check("the sentence names the org",
          all("Northwind" in l for l in lines), str(lines))
    check("the sentence routes to the staff meeting",
          all("staff meeting" in l for l in lines), str(lines))
    check("the sentence carries NO verbs / no buttons",
          not any(w in " ".join(lines).lower()
                  for w in ("[confirm", "click", "tap ", "button")),
          "money is propose-only in the brief — the confirm is a chat phrase "
          "at the staff meeting")

    # deal_update gets its own sentence, not the creation copy
    ws_u = make_ws([_deal(1, kind="deal_update", name="Northwind")])
    ul = build_morning_brief_pack(ws_u, mode="scheduled")["money_lines"]
    check("deal_update gets its own copy",
          len(ul) == 1 and "deal moved" in ul[0], str(ul))

    # drop-empty: no money → no lines, and NEVER an all-clear pad
    ws_e = make_ws([_commitment("cmt_a", "Send the Q3 draft")])
    pe = build_morning_brief_pack(ws_e, mode="scheduled")
    check("no money open → money_lines is empty (never an all-clear line)",
          pe["money_lines"] == [], str(pe["money_lines"]))

    # an unnamed money proposal is skipped rather than rendered nameless
    bad = _deal(9)
    bad["data"]["title"] = ""
    check("a money item with no name renders no sentence",
          money_prose_lines([{"kind": "deal_creation", "title": "",
                              "opened_at": _iso(1)}]) == [],
          "'Command Room thinks  is a live deal' is worse than silence")

    # cap bounds the render, never the truth
    ws_many = make_ws([_deal(i) for i in range(1, MONEY_PROSE_CAP + 3)])
    pm = build_morning_brief_pack(ws_many, mode="scheduled")
    check("money prose is capped", len(pm["money_lines"]) == MONEY_PROSE_CAP,
          f"got {len(pm['money_lines'])}")
    check("capped money items are still COUNTED in the pointer",
          pm["queue_pointer"]["count"] == MONEY_PROSE_CAP + 2,
          f"count={pm['queue_pointer']['count']} — a cap that hid items from "
          "the count would make the pointer lie")

    # ---- [2] pointer honesty ------------------------------------------------
    print("[2] pointer count == what the staff meeting renders")

    ws_p = make_ws([_commitment("cmt_a", "Send the Q3 draft"),
                    _review("cmt_a", "Q3 draft", "Jul 8",
                            with_title="Send the Q3 draft"),
                    _deal(1), _deal(2)])
    pp = build_morning_brief_pack(ws_p, mode="scheduled")
    staff_queue = rank_proposals(load_open_proposals(ws_p, "staff-meeting"))
    check("pointer count equals the staff-meeting queue length",
          pp["queue_pointer"]["count"] == len(staff_queue),
          f"pointer={pp['queue_pointer']['count']} staff={len(staff_queue)} — "
          "the pointer promises what the staff meeting will show")
    check("pointer line states the number and the phrase",
          str(len(staff_queue)) in pp["queue_pointer"]["line"]
          and "staff meeting" in pp["queue_pointer"]["line"],
          pp["queue_pointer"]["line"])

    check("empty queue → empty pointer line (drop-empty, never '0 things')",
          pe["queue_pointer"]["count"] == 0
          and pe["queue_pointer"]["line"] == "",
          repr(pe["queue_pointer"]["line"]))

    # grammar: 1 thing NEEDS, 2 things NEED
    ws_one = make_ws([_deal(1)])
    p1 = build_morning_brief_pack(ws_one, mode="scheduled")
    check("singular reads naturally",
          "1 thing needs your eyes" in p1["queue_pointer"]["line"],
          p1["queue_pointer"]["line"])
    check("plural reads naturally",
          "2 things need your eyes" in pp["queue_pointer"]["line"]
          if pp["queue_pointer"]["count"] == 2 else True,
          pp["queue_pointer"]["line"])

    # ---- [3] hygiene row: ask + verbs, or no row ----------------------------
    print("[3] hygiene review rows state the ask and carry verbs")

    # the LIVE defect shape: writer dropped the title, commitment exists
    ws_h = make_ws([_commitment("cmt_a", "Send the Q3 draft"),
                    _review("cmt_a", "Q3 draft", "Jul 8")])
    q = rank_proposals(load_open_proposals(ws_h, "staff-meeting"))
    check("the legacy no-title row still renders (title recovered from the "
          "commitment projection)", len(q) == 1, f"got {[i['id'] for i in q]}")
    if q:
        row = q[0]
        check("row is NAMED (never the bare 'Housekeeping' shape label)",
              row.get("title") == "Send the Q3 draft", str(row.get("title")))
        check("row ASKS a question", row["render_line"].endswith("?")
              and "?" in row["render_line"], row["render_line"])
        check("row's ask names the evidence (subject + date)",
              "Q3 draft" in row["render_line"]
              and "Jul 8" in row["render_line"], row["render_line"])
        check("row carries verbs", len(row["action_tuples"]) >= 2,
              str(row["action_tuples"]))
        acts = [t["action"] for t in row["action_tuples"]]
        check("row offers a way to say yes", "confirm" in acts, str(acts))
        check("row offers a way to say no", "not relevant" in acts, str(acts))
        check("row offers a way to park it (FB-19)", "hold" in acts, str(acts))
        # HONESTY: this row proposes a close; it must not claim one happened
        check("row does NOT claim a close that never happened",
              "i closed" not in row["render_line"].lower(),
              "the 0.30-0.55 band PROPOSES a close — anything confident "
              "enough to close already closed silently and never carded. "
              "'I closed X — right?' would report an action that did not "
              "occur (and offering to undo it would compound it).")

    # un-askable row (no title anywhere) must NOT render
    ws_g = make_ws([_review("cmt_ghost", "something", "Jul 9")])
    qg = load_open_proposals(ws_g, "staff-meeting")
    check("a row that cannot state its ask does not render at all",
          qg == [], f"got {[i['id'] for i in qg]} — the live 'Housekeeping — "
                    "matched your sent message X' shrug is banned by test")

    # the verb label must state its own duration (F-59)
    from verb_taxonomy import display_label, mute_ttl_days
    check("the hold verb's label states its duration",
          "14" in display_label("hold"), display_label("hold"))
    check("the hold verb's TTL is 14 days", mute_ttl_days("hold") == 14)

    # ---- [4] held suppression + expiry --------------------------------------
    print("[4] held items stay held until answered or expired")

    from mute_ledger import (HOLD_REASON, HOLD_TTL_DAYS, clear_dismissal,
                            hold_item, is_hold)

    ws_x = make_ws([_commitment("cmt_a", "Send the Q3 draft"),
                    _review("cmt_a", "Q3 draft", "Jul 8",
                            with_title="Send the Q3 draft"),
                    _deal(1)])
    before = {i["id"] for i in load_open_proposals(ws_x, "staff-meeting")}
    check("both rows present before the hold",
          {"cru:cmt_a", "bp_1"} <= before, str(before))

    r = hold_item(ws_x, "cru:cmt_a", source_skill="apply-choices",
                  surface="staff-meeting")
    check("hold writes", r["status"] == "held", str(r))
    after = {i["id"] for i in load_open_proposals(ws_x, "staff-meeting")}
    check("held row is SUPPRESSED", "cru:cmt_a" not in after, str(after))
    check("the un-held row is untouched", "bp_1" in after, str(after))

    check("re-holding is idempotent (never silently extends the clock)",
          hold_item(ws_x, "cru:cmt_a", source_skill="apply-choices",
                    surface="staff-meeting")["status"] == "already_held")

    # the brief's pointer must agree — held items are neither shown nor counted
    px = build_morning_brief_pack(ws_x, mode="scheduled")
    check("the brief's pointer count excludes held items too",
          px["queue_pointer"]["count"] == len(after),
          f"pointer={px['queue_pointer']['count']} staff={len(after)} — the "
          "pointer and the card must agree about holds by construction")

    # expiry: it comes BACK on its own — a hold is not a delete
    late = _future_iso(HOLD_TTL_DAYS + 1)
    back = {i["id"] for i in load_open_proposals(ws_x, "staff-meeting",
                                                 now_iso=late)}
    check(f"the hold expires after {HOLD_TTL_DAYS}d and the row returns",
          "cru:cmt_a" in back, str(back))
    early = _future_iso(HOLD_TTL_DAYS - 2)
    still = {i["id"] for i in load_open_proposals(ws_x, "staff-meeting",
                                                 now_iso=early)}
    check("the hold still holds one day before expiry",
          "cru:cmt_a" not in still, str(still))

    # a hold is distinguishable from a plain snooze
    evs = [json.loads(l) for l in
           (ws_x / "_hq" / "data" / "events.jsonl").read_text(
               encoding="utf-8").splitlines() if l.strip()]
    holds = [e for e in evs if is_hold(e)]
    check("the hold is a chat_dismissal (no new event type)",
          len(holds) == 1 and holds[0]["type"] == "chat_dismissal",
          str([e.get("type") for e in evs]))
    check("the hold is tagged with its reason",
          holds and holds[0]["data"]["reason"] == HOLD_REASON)
    check("the hold carries an explicit expiry the ledger honors",
          holds and holds[0]["data"].get("snooze_until"),
          "a bare TTL silently falls back to the 24h legacy default")

    # answering it clears the hold — a hold never outlives its question
    seq = holds[0].get("seq")
    cleared = clear_dismissal(ws_x, seq, cleared_by="person:001",
                              source_skill="apply-choices",
                              reason="answered")
    check("answering clears the hold early", cleared["status"] == "cleared",
          str(cleared))
    check("the row is back the moment it is answered",
          "cru:cmt_a" in {i["id"] for i in
                          load_open_proposals(ws_x, "staff-meeting")})

    check("an unanchored hold is refused loudly",
          _raises(lambda: hold_item(ws_x, "", source_skill="x",
                                    surface="staff-meeting")))

    # ---- [5] consequence floor ---------------------------------------------
    print("[5] consequence floor — nothing consequential → no card")

    ws_f = make_ws([_review("cmt_ghost", "something", "Jul 9")])
    qf = rank_proposals(load_open_proposals(ws_f, "staff-meeting"))
    view = build_card_view(qf, surface="staff-meeting")
    check("a queue of only un-askable rows produces NO sections",
          view["sections"] == [], str(view["sections"]))
    check("...and no tiles (an empty frame is never data)",
          view["tiles"] == [], str(view["tiles"]))
    pf = build_morning_brief_pack(ws_f, mode="scheduled")
    check("...and the brief says nothing about the queue either",
          pf["queue_pointer"]["line"] == "" and pf["money_lines"] == [])

    # a card with real content still renders every section it earns
    ws_c = make_ws([_commitment("cmt_a", "Send the Q3 draft"),
                    _review("cmt_a", "Q3 draft", "Jul 8",
                            with_title="Send the Q3 draft"),
                    _deal(1)])
    vc = build_card_view(rank_proposals(
        load_open_proposals(ws_c, "staff-meeting")), surface="staff-meeting")
    titles = [s["title"] for s in vc["sections"]]
    check("money + hygiene both section when both are real",
          any(t.startswith("MONEY") for t in titles)
          and any(t.startswith("HYGIENE") for t in titles), str(titles))
    check("section counts are honest",
          all(f"({len(s['items'])})" in s["title"] for s in vc["sections"]),
          str(titles))

    # ---- [6] cadence default + migration adjudication ----------------------
    print("[6] staff-meeting cadence — Mon/Wed/Fri default, PROPOSED to "
          "existing installs")

    from schedule_config import (DEFAULT_SCHEDULES, cron_to_english,
                                 load_schedule_config, parse_cron)

    sm = DEFAULT_SCHEDULES["staff-meeting"]
    check("default cron is Mon/Wed/Fri 9am", sm["cron"] == "0 9 * * 1,3,5",
          sm["cron"])
    _m, _h, _dom, _mon, dow = parse_cron(sm["cron"])
    check("cron parses to exactly Mon/Wed/Fri", sorted(dow) == [1, 3, 5],
          str(sorted(dow)))
    check("cron parses to exactly 9:00", sorted(_h) == [9] and sorted(_m) == [0])
    # the invariant every other default holds
    check("stored label == cron_to_english(cron) (the convention every other "
          "DEFAULT_SCHEDULES row holds — a divergent label renders one time "
          "in the config view and another in the live-cron view)",
          sm["label"] == cron_to_english(sm["cron"]),
          f"{sm['label']!r} != {cron_to_english(sm['cron'])!r}")
    for tid, row in DEFAULT_SCHEDULES.items():
        check(f"label/cron lockstep holds for {tid}",
              row["label"] == cron_to_english(row["cron"]),
              f"{row['label']!r} != {cron_to_english(row['cron'])!r}")

    # an EXISTING workspace's custom cron is never overwritten by the default
    ws_s = make_ws([])
    ents = ws_s / "_hq" / "data" / "entities.json"
    data = json.loads(ents.read_text(encoding="utf-8"))
    data["workspace"] = {"schedule_config": {
        "staff-meeting": {"cron": "0 9 * * 1", "label": "9 AM Mondays",
                          "enabled": True}}}
    ents.write_text(json.dumps(data), encoding="utf-8")
    merged = load_schedule_config(ents)
    check("an existing Mon-only registration KEEPS its cron (the default "
          "never silently rewrites a customer's schedule)",
          merged["staff-meeting"]["cron"] == "0 9 * * 1",
          merged["staff-meeting"]["cron"])

    # the bridge migration: propose-and-confirm, durably adjudicated
    from migration_adjudication import adjudication_status, is_suppressed

    MIG = "staff_meeting_cadence_mwf_v1"
    ws_m = make_ws([])
    st = adjudication_status(ws_m, [MIG])
    check("un-asked → unadjudicated (the bridge may propose)",
          st[MIG]["status"] == "unadjudicated"
          and not st[MIG]["suppressed"], str(st))

    ws_d = make_ws([{"seq": 1, "ts": _iso(1),
                     "type": "workspace_migration_skipped",
                     "source_skill": "command-room-update-bridge",
                     "data": {"migration_id": MIG,
                              "reason": "user_declined_cadence_change"}}])
    check("a DECLINED cadence change is durably suppressed — the bridge never "
          "re-asks (the FB-5 re-propose-forever class; re-asking about "
          "someone's calendar reads as nagging)",
          is_suppressed(ws_d, MIG), str(adjudication_status(ws_d, [MIG])))
    check("`redo workspace migrations` can still opt back in",
          not is_suppressed(ws_d, MIG, honor_skips=False))

    ws_a = make_ws([{"seq": 1, "ts": _iso(1),
                     "type": "workspace_migration_applied",
                     "source_skill": "command-room-update-bridge",
                     "data": {"migration_id": MIG}}])
    check("an APPLIED cadence change is not re-proposed",
          is_suppressed(ws_a, MIG))

    # the migration's instruction layer carries the doctrine
    bridge = (ROOT / "skills" / "command-room-update-bridge"
              / "SKILL.md").read_text(encoding="utf-8")
    check("the migration is registered", MIG in bridge)
    check("it is a QUESTION, never a silent append",
          'id: "staff_meeting_cadence_mwf_v1"' in bridge
          and bridge.split('id: "staff_meeting_cadence_mwf_v1"', 1)[1]
                    .split("}", 1)[0].count('type: "calibration_question"') == 1,
          "a schedule is the customer's — propose-and-confirm only")
    seg = bridge.split(f"### Migration: `{MIG}`", 1)[-1].split("### ", 1)[0]
    check("the migration honors the change-schedule no-fire doctrine",
          "NEVER FIRES THE TASK" in seg.upper(),
          "a cadence change must not conjure a catch-up staff meeting (F-51)")
    check("the migration refuses to overwrite a custom cron",
          "custom cron" in seg and "skip silently" in seg)
    check("the migration routes cron mutation to change-schedule",
          "change-schedule" in seg)
    check("the silent pre-check skip writes NO adjudication event",
          "no event at all" in seg,
          "a skip event for a question never asked would durably suppress it")

    # G13 — the helpers must be reachable from the instruction layer
    apply_md = (ROOT / "skills" / "apply-choices" / "SKILL.md").read_text(
        encoding="utf-8")
    check("apply-choices names the hold writer (G13)",
          "mute_ledger.hold_item" in apply_md or "hold_item" in apply_md)
    check("apply-choices carries the in-chat park path (the live failure was "
          "a CHAT park, not a click)",
          "park" in apply_md.lower() and "hold those two" in apply_md.lower())

    print(f"\n{checks - len(failures)}/{checks} checks OK")
    if failures:
        print(f"{len(failures)} FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("run_fb20_readonly_brief_test: all checks passed")
    return 0


def _raises(fn) -> bool:
    try:
        fn()
    except Exception:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
