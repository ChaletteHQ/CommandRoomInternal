#!/usr/bin/env python3
"""SPEC CTS1 acceptance test — the commitment/task surface split.

Pins the rulings (2026-07-16): the FIVE-way partition invariant over the §11
fixture (orphaned promise / delegated task / scheduling row / unowned row /
pending_review row), the SUB1 interaction (a parent with open children counts
ONCE; children on NEITHER surface), the effective-kind classifier (Option B —
post-reclassify fold, never raw counterparty presence), the §2.4 filter trap
(a missing owner never leaks into Waiting On), headline parity with
count_commitments (the surfaces re-group the one bucket export, never
re-count), the §5 warn-level gate check (NEW writes only, never rejects), the
§8 backfill's minimal-writes plan, and the CTS1 registration wiring
(schedule_config / receipts / orchestrator map / migration table / the Phase
3.8 rename / the instruction-layer references — G13).
"""
from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

import migrate_cts1_backfill as backfill  # noqa: E402
import surface_split as ss  # noqa: E402
from commitment_state import count_commitments  # noqa: E402
from cru_match import load_open_commitments  # noqa: E402
from event_gate import gate_events  # noqa: E402

USER = "person_user"
OTHER = "person_bob"
TODAY = datetime.date.today()  # fixtures date-relative (G14) — never a hardcoded future date
NOW = TODAY.isoformat()

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")
        if detail:
            print(f"      {detail}")


def commitment(seq, cid, title, *, kind=None, owner=USER, person_ids=None,
               ts="2026-06-20T10:00:00Z", **extra):
    data = {"id": cid, "title": title, "status": "open"}
    if owner is not None:
        data["owner_id"] = owner
    if kind:
        data["kind"] = kind
    data.update(extra)
    ev = {"seq": seq, "ts": ts, "type": "commitment",
          "source_skill": "meeting-notes", "primary_thread_id": f"t{seq}",
          "data": data}
    if person_ids is not None:
        ev["person_ids"] = person_ids
    return ev


def make_workspace(events):
    ws = tempfile.mkdtemp()
    data_dir = Path(ws) / "_hq" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "entities.json").write_text(json.dumps({
        "workspace": {"user_person_id": USER},
        "people": [{"id": USER, "canonical_name": "Test User"},
                   {"id": OTHER, "canonical_name": "Bob Sample"}],
    }), encoding="utf-8")
    with open(data_dir / "events.jsonl", "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return ws


def main():
    print("=== CTS1 — surface split: five-way partition / SUB1 / gate / backfill / wiring ===\n")

    # ------------------------------------------------------------------
    print("[1] The §11 fixture — five-way partition + invariant")
    # ------------------------------------------------------------------
    fixture = [
        # the five §11-required rows:
        commitment(1, "cmt_ORPHAN", "send the revised proposal over",
                   kind="promise", owner=USER, person_ids=[USER]),      # orphaned promise
        commitment(2, "cmt_DELEG", "pull the vendor quotes",
                   kind="task", owner=OTHER, person_ids=[OTHER]),       # delegated task
        commitment(3, "cmt_SCHED", "block prep time before the offsite",
                   kind="scheduling", owner=USER, person_ids=[USER]),   # scheduling, no counterparty
        commitment(4, "cmt_UNOWNED", "circulate the summary",
                   kind="promise", owner=None),                          # unowned
        commitment(5, "cmt_PENDING", "confirm the venue hold",
                   kind="promise", owner=USER, pending_review=True),     # pending_review
        # plus one of each headline surface for coverage:
        commitment(6, "cmt_WAIT", "send back the signed agreement",
                   kind="promise", owner=OTHER, person_ids=[OTHER, USER]),
        commitment(7, "cmt_TASK", "refresh the pipeline sheet",
                   kind="task", owner=USER),
        commitment(8, "cmt_PROMISED", "send Bob the pricing deck",
                   kind="promise", owner=USER, counterparty_id=OTHER,
                   person_ids=[USER, OTHER]),
    ]
    part = ss.partition_surfaces(fixture, USER)
    ids = {name: {(e.get("data") or {}).get("id") for e in part[name]}
           for name in ss.SURFACES}
    check("orphaned promise stays PROMISED (never demoted by counterparty absence)",
          "cmt_ORPHAN" in ids["promised"], f"{ids}")
    check("delegated task (owner != me, kind task) lands in WAITING ON",
          "cmt_DELEG" in ids["waiting_on"])
    check("counterparty-less scheduling row lands in PERSONAL",
          "cmt_SCHED" in ids["personal"])
    check("ownerless row is UNOWNED — the §2.4 filter trap (None != user is truthy)",
          "cmt_UNOWNED" in ids["unowned"] and "cmt_UNOWNED" not in ids["waiting_on"])
    check("pending_review row is UNCONFIRMED regardless of owner",
          "cmt_PENDING" in ids["unconfirmed"])
    check("owner != me promise lands in WAITING ON",
          "cmt_WAIT" in ids["waiting_on"])
    check("owner-me task lands in PERSONAL; linked promise in PROMISED",
          "cmt_TASK" in ids["personal"] and "cmt_PROMISED" in ids["promised"])
    check("FIVE-WAY INVARIANT: buckets sum to total",
          ss.check_partition_invariant(part) and part["total"] == len(fixture))

    # Headline parity — the surfaces re-group count_commitments, never re-count.
    counts = count_commitments(fixture, user_person_id=USER, now_iso=NOW)
    h = counts["headline"]
    check("waiting_on == headline owed_to_you",
          len(part["waiting_on"]) == h["owed_to_you"], f"{len(part['waiting_on'])} vs {h}")
    check("promised + personal == headline you_owe",
          len(part["promised"]) + len(part["personal"]) == h["you_owe"])
    check("unowned / unconfirmed match the headline",
          len(part["unowned"]) == h["unowned"]
          and len(part["unconfirmed"]) == h["unconfirmed"])

    # ------------------------------------------------------------------
    print("\n[2] SUB1 interaction — parent counts once, children on NEITHER surface")
    # ------------------------------------------------------------------
    ws = make_workspace([
        commitment(1, "cmt_PARENT", "assemble the board pack",
                   kind="promise", owner=USER, counterparty_id=OTHER),
        commitment(2, "cmt_CHILD1", "collect the KPI slides",
                   kind="promise", owner=USER, parent_id="cmt_PARENT"),
        commitment(3, "cmt_CHILD2", "draft the cover memo",
                   kind="promise", owner=USER, parent_id="cmt_PARENT"),
        commitment(4, "cmt_LONE", "review my reading backlog",
                   kind="task", owner=USER),
    ])
    opens = load_open_commitments(Path(ws) / "_hq" / "data" / "events.jsonl")
    part2 = ss.partition_surfaces(opens, USER)
    all_surface_ids = set()
    for name in ss.SURFACES:
        all_surface_ids |= {(e.get("data") or {}).get("id") for e in part2[name]}
    check("parent with open children appears ONCE (in promised)",
          sum(1 for e in part2["promised"]
              if (e.get("data") or {}).get("id") == "cmt_PARENT") == 1)
    check("children excluded from BOTH surfaces (and the tail buckets)",
          "cmt_CHILD1" not in all_surface_ids and "cmt_CHILD2" not in all_surface_ids,
          f"{all_surface_ids}")
    check("partition total is TOP-LEVEL only (2, not 4) and matches headline total",
          part2["total"] == 2 == count_commitments(
              opens, user_person_id=USER, now_iso=NOW)["headline"]["total"],
          f"total={part2['total']} sub_items={part2['sub_items']}")
    check("excluded children are visible as the sub_items diagnostic",
          part2["sub_items"] == 2)

    # ------------------------------------------------------------------
    print("\n[3] Classifier = EFFECTIVE kind (post-reclassify fold, Option B)")
    # ------------------------------------------------------------------
    ws3 = make_workspace([
        commitment(1, "cmt_FLIP", "organize the vendor files",
                   kind="promise", owner=USER),
        {"seq": 2, "ts": "2026-06-21T10:00:00Z", "type": "commitment_reclassified",
         "source_skill": "commitment-triage",
         "data": {"target_id": "cmt_FLIP", "target_seq": 1, "new_kind": "task"}},
    ])
    opens3 = load_open_commitments(Path(ws3) / "_hq" / "data" / "events.jsonl")
    part3 = ss.partition_surfaces(opens3, USER)
    check("reclassified promise → task classifies PERSONAL via the fold",
          [(e.get("data") or {}).get("id") for e in part3["personal"]] == ["cmt_FLIP"]
          and not part3["promised"])

    # ------------------------------------------------------------------
    print("\n[4] counterparty_unresolved — the §8.2 projection-side tag")
    # ------------------------------------------------------------------
    orphan = fixture[0]
    linked = fixture[7]
    task = fixture[6]
    deleg = fixture[1]
    check("orphaned promise is tagged", ss.counterparty_unresolved(orphan, USER))
    check("linked promise / own task / delegated item are NOT tagged",
          not ss.counterparty_unresolved(linked, USER)
          and not ss.counterparty_unresolved(task, USER)
          and not ss.counterparty_unresolved(deleg, USER))
    check("counterparty test goes through the MC1 roster (list-only shape counts)",
          not ss.counterparty_unresolved(
              commitment(9, "cmt_MC", "send the deck to the board",
                         kind="promise", owner=USER,
                         counterparty_ids=[OTHER, "person_carol"]), USER))
    # Review fix (2026-07-18): the tag is defined THROUGH classify_surface —
    # it may NEVER mark a row the partition itself puts outside Promised.
    # Live data had 2 counterparty-less scheduling rows (→ PERSONAL) that the
    # pre-fix predicate tagged, which would have inflated the Friday-triage
    # "N promises have no person attached" batch with non-promises.
    sched_personal = fixture[2]   # cmt_SCHED — scheduling, no counterparty → PERSONAL
    pending = fixture[4]          # cmt_PENDING — unconfirmed, never tagged
    check("tag never disagrees with the partition (PERSONAL scheduling + pending rows untagged)",
          not ss.counterparty_unresolved(sched_personal, USER)
          and not ss.counterparty_unresolved(pending, USER))
    check("tagged set == Promised rows without a counterparty signal, over the whole fixture",
          all((ss.counterparty_unresolved(ev, USER)
               == (ss.classify_surface(ev, USER) == ss.SURFACE_PROMISED
                   and not ss.has_counterparty_signal(ev, USER)))
              for ev in fixture))

    # ------------------------------------------------------------------
    print("\n[5] §5 gate check — warn-level, NEW writes only, never rejects")
    # ------------------------------------------------------------------
    def gate_stderr(ev):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            out = gate_events([ev], strict_enum=True, holder="cts1-test")
        return out, buf.getvalue()

    out, err = gate_stderr({"type": "commitment", "source_skill": "t",
                            "data": {"title": "pull the vendor quotes",
                                     "kind": "task", "owner_id": USER,
                                     "counterparty_id": OTHER}})
    check("task + counterparty WARNS and still writes",
          len(out) == 1 and "CTS1 §5 warn" in err and "kind=task" in err, err)
    out, err = gate_stderr({"type": "commitment", "source_skill": "t",
                            "data": {"title": "send the recap",
                                     "kind": "promise", "owner_id": USER}})
    check("promise with no counterparty signal WARNS and still writes",
          len(out) == 1 and "CTS1 §5 warn" in err and "kind=promise" in err, err)
    out, err = gate_stderr({"type": "commitment", "source_skill": "t",
                            "data": {"title": "send the recap",
                                     "kind": "promise", "owner_id": USER,
                                     "pending_review": True}})
    check("promise + pending_review does NOT warn", "CTS1" not in err, err)
    out, err = gate_stderr({"type": "commitment", "source_skill": "t",
                            "data": {"title": "send the recap",
                                     "kind": "promise", "owner_id": USER,
                                     "counterparty_name": "Bob Sample"}})
    check("promise with an unresolved counterparty NAME does not warn (roster union)",
          "CTS1" not in err, err)
    out, err = gate_stderr({"type": "commitment", "source_skill": "t",
                            "data": {"title": "clean my desk", "kind": "task",
                                     "owner_id": USER}})
    check("clean task does not warn", "CTS1" not in err, err)

    # ------------------------------------------------------------------
    print("\n[6] §8 backfill — minimal writes, no pending_review flood")
    # ------------------------------------------------------------------
    ws6 = make_workspace([
        commitment(1, "cmt_ORPHAN", "send the revised proposal over",
                   kind="promise", owner=USER),                       # explicit → stays, no write
        commitment(2, "cmt_LEGTASK", "I need to organize the vendor files",
                   owner=USER),                                        # legacy no-kind + task language → marker
        commitment(3, "cmt_LEGAMB", "positioning narrative for the fund",
                   owner=USER),                                        # legacy ambiguous → batch, no write
        commitment(4, "cmt_THEIRS", "send back the signed agreement",
                   kind="promise", owner=OTHER),                       # waiting on — no write
        commitment(5, "cmt_CLEANTASK", "refresh the pipeline sheet",
                   kind="task", owner=USER),                           # clean task — no write
        commitment(6, "cmt_BADTASK", "walk the site with the inspector",
                   kind="task", owner=USER, counterparty_id=OTHER),    # task w/ counterparty → flag only
        commitment(7, "cmt_SCHEDME", "block prep time before the offsite",
                   kind="scheduling", owner=USER),                     # explicit scheduling, no cp → PERSONAL, no write, NOT an orphan
    ])
    plan = backfill.analyze(ws6)
    check("only the task-language legacy row plans a marker",
          [r["target_id"] for r in plan["to_task"]] == ["cmt_LEGTASK"], f"{plan['to_task']}")
    check("explicit orphaned promise is report-only (stays Promised); "
          "counterparty-less scheduling is NOT an orphan (it projects Personal)",
          [r["target_id"] for r in plan["orphan_promises"]] == ["cmt_ORPHAN"])
    check("ambiguous legacy row routes to the triage batch — zero pending_review writes",
          [r["target_id"] for r in plan["batch_review"]] == ["cmt_LEGAMB"])
    check("task carrying a counterparty is flagged, never silently flipped",
          [r["target_id"] for r in plan["task_with_counterparty"]] == ["cmt_BADTASK"])
    applied = backfill.apply_markers(ws6, plan)
    check("apply writes exactly the planned markers + a snapshot",
          applied["markers_written"] == 1 and Path(applied["backup"]).exists())
    opens6 = load_open_commitments(Path(ws6) / "_hq" / "data" / "events.jsonl")
    part6 = ss.partition_surfaces(opens6, USER)
    ids6 = {(e.get("data") or {}).get("id") for e in part6["personal"]}
    check("after apply, the reclassified row projects PERSONAL; re-run plans nothing",
          "cmt_LEGTASK" in ids6 and backfill.analyze(ws6)["to_task"] == [])

    # ------------------------------------------------------------------
    print("\n[7] Registration wiring — schedule_config / receipts / map / prose")
    # ------------------------------------------------------------------
    from schedule_config import DEFAULT_SCHEDULES, DISPLAY_NAMES, FIRST_INSTALL_TASK_IDS, cron_to_english
    import receipts as receipts_mod
    check("waiting-on + my-plate in DEFAULT_SCHEDULES; commitments retired",
          "waiting-on" in DEFAULT_SCHEDULES and "my-plate" in DEFAULT_SCHEDULES
          and "commitments" not in DEFAULT_SCHEDULES)
    check("neither new task is first-install (later-add posture)",
          not ({"waiting-on", "my-plate"} & FIRST_INSTALL_TASK_IDS))
    check("labels are cron_to_english-lockstep",
          all(DEFAULT_SCHEDULES[t]["label"] == cron_to_english(DEFAULT_SCHEDULES[t]["cron"])
              for t in ("waiting-on", "my-plate")),
          {t: (DEFAULT_SCHEDULES[t]["label"], cron_to_english(DEFAULT_SCHEDULES[t]["cron"]))
           for t in ("waiting-on", "my-plate")})
    check("display names present (commitments kept for legacy renders)",
          DISPLAY_NAMES.get("waiting-on") == "Waiting On"
          and DISPLAY_NAMES.get("my-plate") == "My Plate"
          and "commitments" in DISPLAY_NAMES)
    check("receipts registry carries both new ids AND keeps commitments readable",
          {"waiting-on", "my-plate", "commitments"} <= receipts_mod.CANONICAL_TASK_IDS
          and receipts_mod.RECEIPT_TYPES["waiting-on"]["types"] == frozenset({"pack_run"})
          and receipts_mod.RECEIPT_TYPES["my-plate"]["types"] == frozenset({"pack_run"}))

    ref_dir = Path(PLUGIN_ROOT) / "skills" / "enable-command-room-schedules" / "references"
    omap = {k: v for k, v in json.loads(
        (ref_dir / "orchestrator-map.json").read_text(encoding="utf-8")).items()
        if not k.startswith("_")}
    check("orchestrator map: waiting-on → orchestrator-commitments.md, my-plate → orchestrator-my-plate.md, commitments gone",
          omap.get("waiting-on") == "orchestrator-commitments.md"
          and omap.get("my-plate") == "orchestrator-my-plate.md"
          and "commitments" not in omap)
    check("every mapped orchestrator file exists with the contract marker",
          all((ref_dir / f).exists()
              and "OUTPUT CONTRACT (v2.13.0+ — MANDATORY)"
              in (ref_dir / f).read_text(encoding="utf-8")[:1500]
              for f in omap.values()))

    def read(rel):
        return (Path(PLUGIN_ROOT) / rel).read_text(encoding="utf-8")

    reg = read("skills/enable-command-room-schedules/SKILL.md")
    check("Phase 1 migration table carries the commitments → waiting-on + my-plate row",
          "`waiting-on` + `my-plate`" in reg and "CTS1 §10.3" in reg)
    wo = read("skills/enable-command-room-schedules/references/orchestrator-commitments.md")
    check("Phase 3.8 renamed — no section may be titled WAITING ON inside the Waiting On chat",
          "NUDGED — NO REPLY" in wo and "⏳ WAITING ON" not in wo)
    check("both orchestrators reference surface_split (G13 — code invisible to prose is dead code)",
          "surface_split" in wo
          and "surface_split" in read(
              "skills/enable-command-room-schedules/references/orchestrator-my-plate.md"))
    check("quick-task lane retired in prose (workspace-manager writes a kind: task event)",
          '"kind": "task"' in read("skills/workspace-manager/SKILL.md")
          and "Completed Quick Tasks" not in read(
              "skills/workspace-manager/references/workspace-detail.md")
          and "Completed Quick Tasks" not in read(
              "skills/command-room-onboarding/references/templates.md"))
    check("triage carries the §8.2(b) counterparty-unresolved batch",
          "counterparty_unresolved" in read("skills/commitment-triage/SKILL.md"))
    check("schema doc carries the Owed/Task split + the five-way invariant",
          "SPEC CTS1" in read("shared/COMMITMENT_SCHEMA.md")
          and "waiting_on + promised + personal + unowned + unconfirmed == total"
          in read("shared/COMMITMENT_SCHEMA.md"))

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
