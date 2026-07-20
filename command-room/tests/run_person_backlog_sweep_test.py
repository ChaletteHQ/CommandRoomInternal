#!/usr/bin/env python3
"""T2.2 scope 3 — person-adapter enrichment (FS-17) + backlog sweep
(FS-11b-extended), on fixtures only (the live run is an orchestrator-session
action with M's go).

Covers:
  1. Adapter enrichment: person rows carry the NAME as title, a dated
     source-ref render_line, and the three REGISTERED verbs — no more
     "person:NNNN. Needs confirming" with no verbs (RV-4).
  2. TTL: aged LOW-CONTEXT (name-only) proposals leave the queue after
     PERSON_LOW_CONTEXT_STALE_DAYS; rich-context and young ones stay.
  3. Sweep dry-run default: plans, writes NOTHING.
  4. Sweep --apply under the PID1-DELEGATED classifier (D7 — plan_sweep
     delegates to identity_reconcile.plan_reconcile; the add bar is the R1
     corroboration doctrine, no longer "name + prose-inferred role/org"):
     only clusters with an OBSERVED address (or ≥2 source families) add;
     full-name single-source rows stay open as confirm; an on-file name
     routes to the merge-propose lane (kept open by this shell); aged
     low-context → expire tombstone; young low-context → left open. ONE
     audit event.
  5. Undo: the whole pass reverses via brain_undo (adds archive — never
     delete; expiries reopen via person_proposal_reopened, honored by the
     confirm_flow reader).

House convention: non-zero exit = fail.
"""
from __future__ import annotations

import datetime as _dt
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
        failures.append(label)
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def _fixture_ws() -> Path:
    ws = Path(tempfile.mkdtemp())
    d = ws / "_hq" / "data"
    d.mkdir(parents=True)
    now = _dt.datetime.now(_dt.timezone.utc)

    def ts(days):
        return (now - _dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    evs = [
        # rich context WITH an observed address in the captured text
        {"seq": 1, "type": "person_proposal", "ts": ts(10),
         "source_skill": "meeting-notes",
         "data": {"name": "Quinn Alvarez", "inferred_role": "Ops lead",
                  "inferred_org": "Acme Co",
                  "evidence": "intro from quinn@example.com in the kickoff thread",
                  "source_ref": "mail:thread-123"}},
        # rich context, no address anywhere in the source
        {"seq": 2, "type": "person_proposal", "ts": ts(50),
         "source_skill": "meeting-notes",
         "data": {"name": "Rio Tanaka", "inferred_org": "Sample Hardware",
                  "evidence": "mentioned as their buyer",
                  "source_ref": "granola:call-9"}},
        # low-context, AGED (past the 30d window)
        {"seq": 3, "type": "person_proposal", "ts": ts(45),
         "source_skill": "meeting-notes",
         "data": {"name": "Bo", "evidence": "transcript filler mention",
                  "source_ref": "granola:call-2"}},
        # low-context, YOUNG (inside the window)
        {"seq": 4, "type": "person_proposal", "ts": ts(5),
         "source_skill": "meeting-notes",
         "data": {"name": "Dana", "evidence": "came up once",
                  "source_ref": "mail:thread-7"}},
        # rich context but same-name collision with an existing record
        {"seq": 5, "type": "person_proposal", "ts": ts(20),
         "source_skill": "meeting-notes",
         "data": {"name": "Sam Sample", "inferred_role": "CEO",
                  "evidence": "the founder", "source_ref": "mail:t"}},
        # rich context but SNOOZED by M (review F-2) — the mute must hold
        {"seq": 6, "type": "person_proposal", "ts": ts(60),
         "source_skill": "meeting-notes",
         "data": {"name": "Val Okafor", "inferred_org": "Brightline Paper",
                  "evidence": "their new ops contact",
                  "source_ref": "mail:thread-42"}},
        {"seq": 7, "type": "chat_dismissal", "ts": ts(1),
         "source_skill": "apply-choices",
         "data": {"target_id": "person:6",
                  "snooze_until": (now + _dt.timedelta(days=6)).strftime(
                      "%Y-%m-%dT%H:%M:%SZ")}},
        # rich context, MULTIPLE addresses in quoted-thread evidence, none
        # matching the person's name (review F-3) — add WITHOUT an email
        {"seq": 8, "type": "person_proposal", "ts": ts(12),
         "source_skill": "meeting-notes",
         "data": {"name": "Noa Lindgren", "inferred_role": "counsel",
                  "evidence": "quoted thread: from sender@example.com, "
                              "cc admin@example.com — introduced their counsel",
                  "source_ref": "mail:thread-88"}},
        # rich context, multiple addresses but ONE token-matches the name
        # (review F-3 acceptance shape)
        {"seq": 9, "type": "person_proposal", "ts": ts(12),
         "source_skill": "meeting-notes",
         "data": {"name": "Ines Vidal", "inferred_org": "Harbor Deck",
                  "evidence": "thread from sender@example.com; she wrote "
                              "from ines.vidal@example.com",
                  "source_ref": "mail:thread-90"}},
    ]
    (d / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in evs) + "\n", encoding="utf-8")
    (d / "entities.json").write_text(json.dumps({"entities": {
        "people": [{"id": "person_001", "canonical_name": "Sam Sample",
                    "is_primary_user": True}],
        "orgs": [], "threads": []}}), encoding="utf-8")
    return ws


def main() -> int:
    from brain_proposals import load_open_proposals
    from person_backlog_sweep import run_sweep

    ws = _fixture_ws()

    # ---- 1+2. Adapter enrichment + TTL --------------------------------------
    rows = {i["id"]: i for i in load_open_proposals(ws) if i["kind"] == "person"}
    check("aged low-context proposal left the queue (TTL)",
          "person:3" not in rows, f"got {sorted(rows)}")
    check("young low-context proposal still queued", "person:4" in rows)
    check("rich-context proposals queued",
          "person:1" in rows and "person:2" in rows)
    q = rows.get("person:1") or {}
    check("row title is the person's name", q.get("title") == "Quinn Alvarez")
    check("row render_line carries role/org badge",
          "Ops lead at Acme Co" in q.get("render_line", ""))
    check("row render_line carries a dated source phrase",
          "surfaced in" in q.get("render_line", "")
          and "no contact record yet" in q.get("render_line", ""))
    verbs = [t["action"] for t in q.get("action_tuples", [])]
    check("row carries the three REGISTERED verbs",
          verbs == ["add person", "proposal not relevant", "snooze proposal 7d"],
          f"got {verbs}")
    from verb_taxonomy import CANONICAL_ACTION_IDS
    check("every adapter verb is a registered action id",
          all(v in CANONICAL_ACTION_IDS for v in verbs))
    # date honesty: a ts-less proposal renders NO date
    from brain_proposals import _person_render_line
    dateless = _person_render_line({"name": "X", "inferred_org": "Acme Co",
                                    "captured_ts": "", "source_ref": "mail:t",
                                    "evidence": ""})
    check("render line never invents a date", " on " not in dateless, dateless)

    # ---- 3. Dry-run default writes nothing ----------------------------------
    events_path = ws / "_hq" / "data" / "events.jsonl"
    before = events_path.read_text(encoding="utf-8")
    plan = run_sweep(ws, apply=False)
    check("dry-run is the default posture", plan["applied"] is False)
    check("dry-run wrote nothing",
          events_path.read_text(encoding="utf-8") == before)
    # PID1 D2 bar: ONLY the observed-email clusters plan as adds — Quinn
    # Alvarez (address in her own thread) and Ines Vidal (name-matching
    # local part among several). Rio Tanaka / Noa Lindgren are full names
    # with a single source and no attributable address → confirm, kept open.
    check("dry-run plans 2 adds (observed-email clusters only — D2)",
          sorted(e["proposal"]["name"] for e in plan["add"])
          == ["Ines Vidal", "Quinn Alvarez"],
          str([e["proposal"]["name"] for e in plan["add"]]))
    check("dry-run plans 1 expiry", len(plan["expire"]) == 1)
    check("dry-run keeps 5 open (2 confirm-tier full names + young mention "
          "+ on-file cluster + snoozed row)",
          len(plan["keep_open"]) == 5,
          str([e["proposal"]["name"] for e in plan["keep_open"]]))
    # Review F-2: the snoozed proposal routes to keep_open with the mute
    # named — the sweep NEVER adjudicates a row M snoozed.
    kept = {e["proposal"]["name"]: e["why"] for e in plan["keep_open"]}
    check("snoozed row held with a snooze rationale (F-2)",
          "Val Okafor" in kept and "snoozed" in kept["Val Okafor"].lower(),
          str(kept))
    # PID1 D4a: an on-file name is the merge-propose lane, not an add and
    # not a silent drop — this shell keeps it open with the tier named.
    check("on-file cluster held for link/merge (D4a)",
          "Sam Sample" in kept and "existing record" in kept["Sam Sample"],
          str(kept.get("Sam Sample")))

    # ---- 4. Apply ------------------------------------------------------------
    plan = run_sweep(ws, apply=True)
    res = plan["results"]
    added = {a["name"]: a for a in res["added"]}
    check("observed-email clusters auto-added (single-source full names, "
          "on-file cluster, and snooze all held — D2)",
          set(added) == {"Quinn Alvarez", "Ines Vidal"}, f"got {set(added)}")
    # Review F-3: a name-matching local part among several addresses keeps
    # THAT address; Noa Lindgren's quoted-thread addresses match nothing →
    # no attributable email → the cluster stays CONFIRM (not added at all
    # under the PID1 bar — attribution uncertainty is the no-auto case).
    check("multi-address evidence with a name-matching local part keeps "
          "THAT address (F-3)",
          added.get("Ines Vidal", {}).get("email") == "ines.vidal@example.com",
          str(added.get("Ines Vidal")))
    check("no attributable address → NOT auto-added (F-3 under the D2 bar)",
          "Noa Lindgren" not in added and "Rio Tanaka" not in added)
    check("snoozed row neither added nor expired (F-2)",
          "Val Okafor" not in added
          and all(e["name"] != "Val Okafor" for e in res["expired"]))
    check("observed email captured with provenance",
          added.get("Quinn Alvarez", {}).get("email") == "quinn@example.com")
    check("on-file cluster never reaches auto_add_person (held upstream — "
          "no needs_confirm churn)",
          res["needs_confirm"] == [], str(res["needs_confirm"]))
    check("aged low-context row expired", [e["name"] for e in res["expired"]] == ["Bo"])
    check("zero errors", res["errors"] == [], str(res["errors"]))
    text = events_path.read_text(encoding="utf-8")
    check("ONE person_backlog_swept audit event",
          text.count('"person_backlog_swept"') == 1)
    rows2 = {i["id"]: i for i in load_open_proposals(ws) if i["kind"] == "person"}
    # FS-19: the added rows left the queue via their tombstones; person:4
    # (young low-context "Dana") is genuinely unresolved and remains. person:5
    # ("Sam Sample") is now ALSO gone — not by the sweep (which still holds it
    # for a same-name confirm, checked above) but by the FS-19 queue filter:
    # it is a full-name exact match to the existing primary-user record, so
    # the "add person" row is suppressed as already-on-file (the every-week
    # resurfacing this fix kills). The sweep's needs_confirm proposal stays
    # open in the substrate; it simply never renders.
    check("added rows left the queue; the confirm-tier and young rows remain",
          sorted(rows2) == ["person:2", "person:4", "person:8"],
          f"got {sorted(rows2)}")
    ents = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    people = ents.get("entities", ents).get("people", [])
    by_name = {p["canonical_name"]: p for p in people}
    check("person records created", "Quinn Alvarez" in by_name and "Ines Vidal" in by_name)

    # ---- 5. Undo -------------------------------------------------------------
    import brain_undo
    res_u = brain_undo.undo_batch(
        ws, {"kind": "brain_batch", "batch_id": plan["batch_id"]},
        undone_by="person_001", source_skill="apply-choices")
    check("undo reverses the whole batch cleanly",
          res_u["status"] == "undone" and res_u["n_errors"] == 0,
          f"{res_u['status']}, {res_u['n_errors']} errors")
    check("undo count = 2 adds + 1 expiry", res_u["n_undone"] == 3)
    ents = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    people = ents.get("entities", ents).get("people", [])
    by_name = {p["canonical_name"]: p for p in people}
    check("undo ARCHIVES the added people (never deletes)",
          all(by_name.get(n, {}).get("status") == "archived"
              for n in ("Quinn Alvarez", "Ines Vidal")))
    # The expiry reopen is honored by the reader (the queue adapter still
    # hides it behind the low-context TTL — check the reader directly).
    from confirm_flow import load_open_person_proposals
    open_seqs = {p["seq"] for p in load_open_person_proposals(events_path)}
    check("undo reopened the expired proposal (reader honors the reopen)",
          3 in open_seqs, f"got {sorted(open_seqs)}")

    if failures:
        print(f"\nperson backlog sweep FAIL — {len(failures)} of {checks}")
        return 1
    print(f"person adapter enrichment + backlog sweep: {checks} checks OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
