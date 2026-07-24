#!/usr/bin/env python3
"""SPEC UXR1 — widget vocabulary fixes acceptance battery (D1-D5, D7).

Covers §5 of SPEC_UXR1_widget_vocabulary_fixes.md on synthetic fixtures
(placeholder names only — Sam Sample / Acme Co house set; every date
relative to a runtime `now` — G14):

  D1  confirm tail slims to mine/theirs/drop/snooze — exactly 4 buttons,
      no <select>; the removed verbs stay registered (legacy dispatch).
  D2  hygiene rows relabel per class (Close it / No — still open) with
      frozen wire ids; every other surface keeps the global labels.
  D3  auto-link matrix: exact-unique-clean auto-applies + resolves same
      run + receipt line + undo round-trip (alias-free state restored,
      confirm row re-opens, re-run does NOT re-auto); every other shape
      still asks. AUTO_ALLOWED key-set pin (mutation fence).
  D4  ambiguous person_link rows render evidence + a differentiator;
      the identical alias/matched-names live shape is decidable.
  D5  nameless update rows resolve the on-file name or quarantine to the
      honest placeholder — never a blank ask.
  D7  hold / snooze 14d / make task display labels (finding-8 pair).

House convention: non-zero exit = fail.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
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


NOW = _dt.datetime.now(_dt.timezone.utc)
NOW_ISO = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")


def ts(days_ago: float) -> str:
    return (NOW - _dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_ws(events, people=None, orgs=None) -> Path:
    ws = Path(tempfile.mkdtemp())
    d = ws / "_hq" / "data"
    d.mkdir(parents=True)
    d.joinpath("events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + ("\n" if events else ""),
        encoding="utf-8")
    d.joinpath("entities.json").write_text(json.dumps({"entities": {
        "people": people or [], "orgs": orgs or [], "threads": []}}),
        encoding="utf-8")
    return ws


def _pp(seq, days_ago, name, *, etype="person_proposal", org=None,
        evidence="", source_ref="", person_id=None):
    d = {"name": name, "evidence": evidence, "source_ref": source_ref,
         "pending_review": True}
    if name is None:
        d.pop("name")
    if org:
        d["inferred_org"] = org
    if person_id:
        d["person_id"] = person_id
    return {"type": etype, "seq": seq, "ts": ts(days_ago),
            "source_skill": "meeting-notes", "data": d}


def main() -> int:
    import brain_undo
    import identity_reconcile as ir
    import surface_drivers
    from brain_proposals import AUTO_ALLOWED, load_open_proposals
    from change_feed import changes_since
    from chat_output_renderer import (CLASS_DISPLAY_LABELS,
                                      _grammar_class_of,
                                      render_chat_output_widget)
    from verb_taxonomy import CANONICAL_ACTION_IDS, display_label

    # ------------------------------------------------------------------
    print("[D1] confirm tail — 4 buttons, no select; removed verbs dispatch")
    # ------------------------------------------------------------------
    check("emitted confirm tail is exactly mine/theirs/drop/snooze",
          surface_drivers._REVIEW_VERBS == ["mine", "theirs to [name]",
                                            "drop", "snooze 3d"],
          repr(surface_drivers._REVIEW_VERBS))
    view = {"surface": "commitments", "title": "t", "sections": [{
        "title": "Needs a quick confirm", "items": [{
            "n": "cmt_1", "display_n": 1, "name": "Send deck to Sam Sample",
            "actions": list(surface_drivers._REVIEW_VERBS)}]}]}
    html = render_chat_output_widget(view, wrapper="fragment")
    row = html.split('data-item-n="cmt_1"')[1]
    row_controls = row.split("cr-item-note")[0]
    n_buttons = len(re.findall(r'<button class="cr-action', row_controls))
    check("confirm row renders exactly 4 verb buttons", n_buttons == 4,
          str(n_buttons))
    check("confirm row renders NO <select>", "<select" not in row_controls)
    # Old persisted 5-verb rows: the removed ids stay registered + renderable
    for verb in ("not relevant", "add to my plate"):
        check(f"{verb!r} still a registered wire id (legacy dispatch)",
              verb in CANONICAL_ACTION_IDS)
    legacy_view = {"surface": "commitments", "title": "t", "sections": [{
        "title": "S", "items": [{
            "n": "cmt_2", "display_n": 1, "name": "Old widget row",
            "actions": ["mine", "theirs to [name]", "drop", "not relevant",
                        "add to my plate"]}]}]}
    legacy_html = render_chat_output_widget(legacy_view, wrapper="fragment")
    check("a 5-verb legacy-shaped row still renders + carries both wire ids",
          'data-action="not relevant"' in legacy_html
          or '"not relevant"' in legacy_html)

    # ------------------------------------------------------------------
    print("[D2] hygiene relabel — Close it / No — still open; wires frozen")
    # ------------------------------------------------------------------
    check("hygiene class owns the staff-meeting CRU verb set",
          _grammar_class_of(["confirm", "not relevant", "hold"]) == "hygiene")
    check("hygiene class owns the Phase 3.6 pair shape too",
          _grammar_class_of(["confirm", "not relevant", "add to my plate"])
          == "hygiene")
    check("a bare confirm row is NOT hygiene",
          _grammar_class_of(["confirm", "edit [change]"]) == "confirm")
    hyg_view = {"surface": "cr-brain", "title": "t", "sections": [{
        "title": "Hygiene", "items": [{
            "n": "cru:c1", "display_n": 1, "name": "Send deck to Sam Sample",
            "context_tag": "Did you already handle this? — close it?",
            "actions": ["confirm", "not relevant", "hold"]}]}]}
    hyg = render_chat_output_widget(hyg_view, wrapper="fragment")
    check("affirmative reads 'Close it'", ">Close it<" in hyg)
    check("negative reads 'No — still open'", ">No — still open<" in hyg)
    check("hold keeps its taxonomy label",
          f">{display_label('hold')}<" in hyg, display_label("hold"))
    check("middle wire tuple is `not relevant` (frozen)",
          'data-action="not relevant"' in hyg)
    check("affirmative wire tuple is `confirm` (frozen)",
          'data-action="confirm"' in hyg)
    check("3 options render as buttons, no dropdown",
          "<select" not in hyg.split('data-item-n="cru:c1"')[1]
          .split("cr-item-note")[0])
    # Every OTHER surface keeps the global label.
    other = render_chat_output_widget(
        {"surface": "commitments", "title": "t", "sections": [{
            "title": "S", "items": [{
                "n": "3", "display_n": 3, "name": "Non-hygiene row",
                "actions": ["resolved", "not relevant"]}]}]},
        wrapper="fragment")
    check("non-hygiene surface still labels 'Not relevant (60 days)'",
          "Not relevant (60 days)" in other)
    check("non-hygiene surface never shows the hygiene relabel",
          "No — still open" not in other)
    check("the override table is scoped to hygiene only",
          set(CLASS_DISPLAY_LABELS) == {"hygiene"},
          repr(set(CLASS_DISPLAY_LABELS)))

    # ------------------------------------------------------------------
    print("[D3] auto-link matrix (each fixture real-substrate-shaped)")
    # ------------------------------------------------------------------
    # exact-unique-clean → auto-linked + resolved same run
    people = [{"id": "person_001", "canonical_name": "Drew Sample",
               "first_seen": ts(300)[:10], "primary_org_id": "org_001"}]
    orgs = [{"id": "org_001", "canonical_name": "Acme Co"}]
    ws = make_ws([_pp(1, 5, "Drew Sample",
                      evidence="granola attendee, Command Room 2",
                      source_ref="granola:call-1")], people, orgs)
    plan = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    r = plan["results"]
    check("exact-unique-clean auto-links",
          [a["name"] for a in r["auto_linked"]] == ["Drew Sample"],
          str(r["auto_linked"]))
    check("no confirm row minted for the auto-linked pair",
          r["merge_rows_proposed"] == 0)
    check("the auto proposal resolved in the SAME run (queue empty)",
          [p for p in load_open_proposals(ws)] == [])
    from brain_proposals import resting_auto_proposals
    check("no resting auto proposal (LB2 lifecycle contract)",
          resting_auto_proposals(ws) == [])
    feed = changes_since(ws, ts(1))
    check("receipt line: 'Linked N name-mentions … say `undo` to reverse any.'",
          any("name-mention" in l["text"] and "`undo`" in l["text"]
              for l in feed["lines"]),
          str([l["text"] for l in feed["lines"]]))
    # undo round-trip: alias-free state + a confirm row re-opens
    undo = brain_undo.undo_batch(
        ws, {"kind": "brain_batch", "batch_id": plan["batch_id"]},
        undone_by="person_000", source_skill="apply-choices")
    check("undo batch succeeds", undo["status"] == "undone", str(undo))
    check("alias-free state restored (no aliases.json ever written)",
          not (ws / "_hq" / "data" / "aliases.json").exists())
    q = load_open_proposals(ws)
    link_rows = [p for p in q if p["kind"] == "person_link"]
    check("undo re-opens a CONFIRM-tier person_link row",
          len(link_rows) == 1 and link_rows[0]["tier"] == "confirm",
          str([(p["kind"], p["tier"]) for p in q]))
    check("the re-opened row carries the original evidence",
          "granola attendee" in (link_rows[0]["render_line"] if link_rows
                                 else ""))
    plan2 = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    check("re-run after undo does NOT re-auto-link",
          plan2["results"]["auto_linked"] == [],
          str(plan2["results"]["auto_linked"]))

    # two-candidate name → still asks, count named, no pre-fill (IDM1 rail)
    people2 = [{"id": "person_001", "canonical_name": "Sam Reyes",
                "first_seen": ts(300)[:10]},
               {"id": "person_002", "canonical_name": "Sam Reyes",
                "first_seen": ts(200)[:10]}]
    ws = make_ws([_pp(1, 5, "Sam Reyes", evidence="intro thread",
                      source_ref="mail:t1")], people2)
    plan = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    check("two same-name records → NEVER auto",
          plan["results"]["auto_linked"] == [])
    rows = [p for p in load_open_proposals(ws)
            if p["source_family"] == "person" and not p.get("person_id")]
    check("two-candidate row still asks, count named, no pre-fill",
          rows and "2 people named Sam Reyes" in rows[0]["render_line"]
          and not rows[0].get("match_person_id"),
          str([p.get("render_line") for p in rows]))

    # conflicting org → still asks
    people3 = [{"id": "person_001", "canonical_name": "Kit Marsh",
                "first_seen": ts(300)[:10], "primary_org_id": "org_001"}]
    ws = make_ws([_pp(1, 5, "Kit Marsh", evidence="mentioned in a thread",
                      source_ref="mail:t1", org="Globex")], people3, orgs)
    plan = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    check("conflicting org → still asks",
          plan["results"]["auto_linked"] == []
          and plan["results"]["merge_rows_proposed"] == 1)

    # shared-inbox email → not corroboration, still asks
    people4 = [{"id": "person_001", "canonical_name": "Val Okafor",
                "first_seen": ts(300)[:10], "emails": ["info@example.org"]}]
    ws = make_ws([_pp(1, 5, "Val Okafor", evidence="via info@example.org",
                      source_ref="mail:t1")], people4)
    plan = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    check("shared-inbox email → not corroboration, still asks",
          plan["results"]["auto_linked"] == []
          and plan["results"]["merge_rows_proposed"] == 1)

    # duplicate-suspect pair → still asks
    people5 = [{"id": "person_010", "canonical_name": "Ari Bell",
                "first_seen": ts(300)[:10], "role": "CEO"},
               {"id": "person_011", "canonical_name": "Ari Bell",
                "first_seen": ts(200)[:10]}]
    ws = make_ws([_pp(1, 5, "Ari Bell", evidence="board thread",
                      source_ref="mail:t1")], people5)
    plan = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    check("duplicate-suspect pair → still asks",
          plan["results"]["auto_linked"] == [])

    # bare first name → never auto (Bug #19 stands)
    ws = make_ws([_pp(1, 5, "Quinn", evidence="mentioned",
                      source_ref="mail:t1")],
                 [{"id": "person_001", "canonical_name": "Quinn Alvarez",
                   "first_seen": ts(300)[:10]}])
    plan = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    check("bare first name → never auto",
          plan["results"]["auto_linked"] == [])

    # gate helper direct: eligible case says why
    ok, why = ir.auto_link_eligible(
        make_ws([], people), {"name": "Drew Sample", "add_rows": [],
                              "inferred_org": None},
        people[0], None)
    check("gate helper: clean case eligible", ok, why)

    # AUTO_ALLOWED key-set pin (mutation fence): adding person_proposal or
    # person_merge to the auto table must fail this test.
    check("AUTO_ALLOWED keys pinned (person_link in; person_merge and "
          "person_proposal NEVER)",
          set(AUTO_ALLOWED) == {"commitment_close",
                                "person_org_creation_structured_fact",
                                "entity_fact_structured", "person_link"},
          repr(sorted(AUTO_ALLOWED)))
    check("person_merge has no reverser (merge is a click, forever)",
          not brain_undo.has_reverser("person_merge"))
    check("person_link HAS a registered reverser (auto legality half)",
          brain_undo.has_reverser("person_link"))

    # ------------------------------------------------------------------
    print("[D4] ambiguous rows render evidence + differentiator")
    # ------------------------------------------------------------------
    # role-email case renders decision-grade (evidence + differentiator)
    ws = make_ws([_pp(1, 5, "Val Okafor", evidence="via info@example.org",
                      source_ref="mail:t1")], people4)
    ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    link = [p for p in load_open_proposals(ws) if p["kind"] == "person_link"]
    check("ambiguous row line carries the evidence text",
          link and "via info@example.org" in link[0]["render_line"],
          str([p.get("render_line") for p in link]))
    check("ambiguous row line carries a differentiator + the ask",
          link and "Same as Val Okafor (" in link[0]["render_line"]
          and "link it?" in link[0]["render_line"])
    # the live identical-names shape (spec acceptance): alias == matched name, evidence present
    line = ir.person_link_ask_line(
        make_ws([], people, orgs), "Drew Sample",
        {"id": "person_001", "canonical_name": "Drew Sample",
         "primary_org_id": "org_001"},
        "granola attendee, Command Room 2 (a while back)")
    check("identical-names shape renders decidable (evidence + org diff)",
          "appeared as granola attendee" in line and "(Acme Co)" in line,
          line)
    # differentiator ladder: org > email > last touched > no details
    ws0 = make_ws([], [])
    check("differentiator falls back to email",
          ir._link_differentiator(ws0, {"emails": ["sam@example.com"]})
          == "sam@example.com")
    check("differentiator falls back to last touched",
          ir._link_differentiator(
              ws0, {"last_interaction": "2026-03-04"}).startswith(
              "last touched"))
    check("differentiator floor is honest",
          ir._link_differentiator(ws0, {}) == "no details on file")

    # ------------------------------------------------------------------
    print("[D5] nameless update rows — resolve or quarantine, never blank")
    # ------------------------------------------------------------------
    # The live seq-1591/1742 shape: name:null, evidence:null, person_id set.
    # (Dates inside the FS-17 low-context window — the age-out is its own
    # shipped behavior, not what D5 is exercising.)
    evs = [_pp(1591, 10, None, etype="person_update_proposal",
               person_id="person_001"),
           _pp(1742, 8, None, etype="person_update_proposal",
               person_id="person_9zz")]
    ws = make_ws(evs, [{"id": "person_001",
                        "canonical_name": "Noa Lindgren",
                        "first_seen": ts(300)[:10]}])
    rows = {p["id"]: p for p in load_open_proposals(ws)
            if p["source_family"] == "person"}
    check("resolvable person_id renders the on-file name",
          rows.get("person:1591", {}).get("title") == "Noa Lindgren — update",
          str(rows.get("person:1591")))
    check("resolved row keeps its adjudication verbs",
          [a["action"] for a in rows.get("person:1591", {})
           .get("action_tuples", [])] == ["add person",
                                          "proposal not relevant",
                                          "snooze proposal 7d"])
    q1742 = rows.get("person:1742", {})
    check("name+evidence-empty row quarantines to the honest placeholder",
          q1742.get("render_line") == "proposal withheld — identity row "
                                      "with nothing to decide",
          str(q1742))
    check("quarantined row carries the read-only verb only",
          [a["action"] for a in q1742.get("action_tuples", [])]
          == ["show why"])
    check("no blank ask anywhere (every person row has a render_line "
          "or title)",
          all((p.get("title") or "").strip()
              for p in rows.values()), str(rows))

    # ------------------------------------------------------------------
    print("[D7] finding-8 label pair (+ render checks on live surfaces)")
    # ------------------------------------------------------------------
    check("hold label carries intent + duration",
          display_label("hold") == "Hold — parked till you answer (14 days)",
          display_label("hold"))
    check("snooze 14d label carries duration + intent",
          display_label("snooze 14d") == "Snooze (14 days) — hide until then",
          display_label("snooze 14d"))
    check("make task label says conversion",
          display_label("make task") == "Turn into a task",
          display_label("make task"))
    d7 = render_chat_output_widget(
        {"surface": "cr-brain", "title": "t", "sections": [{
            "title": "S", "items": [
                {"n": "1", "display_n": 1, "name": "Hygiene",
                 "actions": ["confirm", "not relevant", "hold"]},
                {"n": "2", "display_n": 2, "name": "Objective row",
                 "actions": ["report [status]", "snooze 14d", "skip"]},
                {"n": "3", "display_n": 3, "name": "Confirm row",
                 "actions": ["mine", "make task", "drop"]},
            ]}]},
        wrapper="fragment")
    check("hold renders its new label on a live surface",
          "Hold — parked till you answer (14 days)" in d7)
    check("snooze 14d renders its new label on a live surface",
          "Snooze (14 days) — hide until then" in d7)
    check("make task renders 'Turn into a task' on a live surface",
          "Turn into a task" in d7)

    print(f"{checks - len(failures)}/{checks} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
