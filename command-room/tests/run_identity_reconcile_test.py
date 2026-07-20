#!/usr/bin/env python3
"""SPEC PID1 — People Identity & Auto-Add battery.

Covers the three-tier identity model end-to-end on synthetic fixtures
(placeholder names only; every date relative to a runtime `now` — G14):

  1. Clustering (D1/D3): one person = one cluster; a bare first name is
     never absorbed into a fuller name; update rows for a proposed person
     fold in.
  2. The tier rule table (D2 + §0 rulings): auto only on full name +
     observed email / 2 source families + zero collision; Bug #19 pins.
  3. Auto execution (D2): the R1 rail — auto_add_person, batch-stamped
     tombstones, ONE undo reverses (adds archive, expiries reopen), re-run
     after undo does not re-add.
  4. F-3 email attribution under the new bar.
  5. Merge-propose (D4): exact-email silent link (§0-2 YES) + the
     role-address guard; person_link rows; the existing-record dup scan
     (two-Dons pin, shared-role-email exclusion); merge is confirm-only.
  6. Annotations (D5/§0-4): nameless rows convert; zero person rows;
     email-join resolution.
  7. D8 fingerprint tombstones for seq-less proposals.
  8. §0-3 caps: spill counted, never silent.
  9. D6 narration: changes_since people_added/people_linked lines.
 10. Render (D3): adapter cluster rows w/ cluster_seqs; update-title fix;
     build_card_view count honesty (RV-4).

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


NOW = _dt.datetime.now(_dt.timezone.utc)
NOW_ISO = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")


def ts(days_ago: float) -> str:
    return (NOW - _dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pp(seq, days_ago, name, *, etype="person_proposal", role=None, org=None,
        evidence="", source_ref="", person_id=None):
    d = {"name": name, "evidence": evidence, "source_ref": source_ref,
         "pending_review": True}
    if name is None:
        d.pop("name")
    if role:
        d["inferred_role"] = role
    if org:
        d["inferred_org"] = org
    if person_id:
        d["person_id"] = person_id
    ev = {"type": etype, "ts": ts(days_ago), "source_skill": "meeting-notes",
          "data": d}
    if seq is not None:
        ev["seq"] = seq
    else:
        ev["seq"] = None  # the freelance-written seq:null shape (D8)
    return ev


def make_ws(events, people=None) -> Path:
    ws = Path(tempfile.mkdtemp())
    d = ws / "_hq" / "data"
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    (d / "entities.json").write_text(json.dumps({"entities": {
        "people": people or [], "orgs": [], "threads": []}}),
        encoding="utf-8")
    return ws


def main() -> int:
    import brain_undo
    import identity_reconcile as ir
    from brain_proposals import build_card_view, load_open_proposals
    from change_feed import changes_since
    from confirm_flow import load_open_person_proposals

    # ------------------------------------------------------------------
    print("[0] role-address constant (§0-2 guard)")
    # ------------------------------------------------------------------
    for local in ("info", "office", "admin", "support", "hello"):
        check(f"is_role_address({local}@) is True",
              ir.is_role_address(f"{local}@example.com"))
    check("a personal address is not role-shaped",
          not ir.is_role_address("quinn.alvarez@example.com"))
    check("no address is not role-shaped", not ir.is_role_address(None))

    # ------------------------------------------------------------------
    print("[1] clustering — one person = one cluster (D1/D3)")
    # ------------------------------------------------------------------
    ws = make_ws([
        _pp(1, 12, "Quinn Alvarez", org="Acme Co",
            evidence="kickoff intro", source_ref="granola:call-1"),
        _pp(2, 8, "Quinn Alvarez", role="Ops lead",
            evidence="followup thread", source_ref="mail:thread-2"),
        _pp(3, 4, "quinn  alvarez",
            evidence="mentioned again", source_ref="mail:thread-3"),
        _pp(4, 6, "Quinn", evidence="bare first name",
            source_ref="granola:call-9"),
        _pp(5, 3, "Quinn Alvarez", etype="person_update_proposal",
            role="VP Ops", evidence="promoted per the call",
            source_ref="granola:call-4"),
    ])
    rows = load_open_person_proposals(ws / "_hq" / "data" / "events.jsonl")
    view = ir.cluster_open_proposals(rows)
    check("two clusters — the bare 'Quinn' is NEVER absorbed (Bug #19)",
          len(view["clusters"]) == 2,
          str([c["key"] for c in view["clusters"]]))
    by_key = {c["key"]: c for c in view["clusters"]}
    qa = by_key.get("quinn alvarez") or {}
    check("the full-name cluster holds 3 adds + the folded update row",
          len(qa.get("add_rows", [])) == 3 and len(qa.get("update_rows", [])) == 1,
          f"{len(qa.get('add_rows', []))} adds, {len(qa.get('update_rows', []))} updates")
    check("cluster evidence merges newest-first",
          (qa.get("rows") or [{}])[0].get("seq") == 5)
    check("cluster title is the longest/most complete spelling",
          qa.get("name") == "Quinn Alvarez")
    check("no standalone updates left (it folded into the proposed person)",
          view["updates"] == [], str(view["updates"]))

    # ------------------------------------------------------------------
    print("[2] the tier rule table (D2 + §0-1 rulings)")
    # ------------------------------------------------------------------
    def tier_of(events, people=None):
        w = make_ws(events, people=people)
        rows_ = load_open_person_proposals(w / "_hq" / "data" / "events.jsonl")
        v = ir.cluster_open_proposals(rows_)
        cluster = (v["clusters"] or [None])[0]
        if cluster is None:
            return None, None
        cls = ir.classify_cluster(w, cluster)
        return cls["tier"], cls

    t, cls = tier_of([_pp(1, 5, "Rio Tanaka",
                          evidence="wrote from rio.tanaka@example.com",
                          source_ref="mail:t-1")])
    check("full name + observed email, no collision → auto", t == "auto",
          str(cls))
    t, cls = tier_of([
        _pp(1, 5, "Noa Lindgren", evidence="on the call",
            source_ref="granola:call-1"),
        _pp(2, 3, "Noa Lindgren", evidence="followed up",
            source_ref="mail:thread-9")])
    check("full name + 2 independent source families, no email → auto",
          t == "auto", str(cls))
    t, cls = tier_of([_pp(1, 5, "Ines Vidal", evidence="mentioned once",
                          source_ref="mail:thread-1")])
    check("full name + single source, no email → confirm", t == "confirm",
          str(cls))
    t, cls = tier_of([_pp(1, 5, "Dana", role="CFO", org="Acme Co",
                          evidence="from dana@example.com",
                          source_ref="mail:t")])
    check("lone first name w/ role AND org AND email → CONFIRM (Bug #19 pin)",
          t == "confirm", str(cls))
    t, cls = tier_of(
        [_pp(1, 5, "Sam Vale", evidence="from sam.vale@example.com",
             source_ref="mail:t")],
        people=[{"id": "person_001", "canonical_name": "Sam Reyes",
                 "first_seen": ts(400)[:10]}])
    check("ANY same-name collision → confirm, even with an email",
          t == "confirm", str(cls))
    t, cls = tier_of(
        [_pp(1, 5, "Bo Sample", evidence="mentioned",
             source_ref="mail:t")],
        people=[{"id": "person_001", "canonical_name": "Bo Sample",
                 "first_seen": ts(400)[:10]}])
    check("confidently on file → merge_propose", t == "merge_propose",
          str(cls))
    t, cls = tier_of([_pp(1, 5, None, evidence="second speaker unnamed",
                          source_ref="granola:call-1")])
    check("no name → the nameless row never forms a cluster", t is None)

    # ------------------------------------------------------------------
    print("[3] auto execution on the R1 rail + undo (D2)")
    # ------------------------------------------------------------------
    ws = make_ws([
        _pp(1, 12, "Quinn Alvarez",
            evidence="intro from quinn.alvarez@example.com",
            source_ref="mail:thread-1"),
        _pp(2, 8, "Quinn Alvarez", role="Ops lead",
            evidence="second mention", source_ref="granola:call-2"),
        # aged name-only single mention → expire (FS-17 kept)
        _pp(3, 45, "Bo", evidence="filler mention",
            source_ref="granola:call-3"),
    ])
    plan = ir.run_identity_reconcile(ws, apply=False, now_iso=NOW_ISO)
    check("dry-run is the default and writes nothing",
          plan["applied"] is False and
          '"person_created"' not in (ws / "_hq" / "data" /
                                     "events.jsonl").read_text(encoding="utf-8"))
    plan = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    res = plan["results"]
    check("auto cluster added once", len(res["added"]) == 1
          and res["added"][0]["name"] == "Quinn Alvarez", str(res["added"]))
    check("observed email captured", res["added"][0]["email"]
          == "quinn.alvarez@example.com")
    check("every member proposal tombstoned",
          res["added"][0]["n_proposals"] == 2)
    check("aged low-context expired", [e["name"] for e in res["expired"]]
          == ["Bo"], str(res["expired"]))
    text = (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
    check("ONE identity_reconcile_run receipt",
          text.count('"identity_reconcile_run"') == 1)
    open_after = load_open_person_proposals(ws / "_hq" / "data" / "events.jsonl")
    check("queue drained", open_after == [], str(open_after))
    # undo: adds archive, expiry reopens, add-tombstones stay resolved
    undo = brain_undo.undo_batch(
        ws, {"kind": "brain_batch", "batch_id": plan["batch_id"]},
        undone_by="person_000", source_skill="apply-choices")
    check("undo runs clean", undo["status"] == "undone"
          and undo["n_errors"] == 0, str(undo))
    ents = json.loads((ws / "_hq" / "data" / "entities.json")
                      .read_text(encoding="utf-8"))
    people = ents["entities"]["people"]
    check("undo ARCHIVES the auto-added record (never deletes)",
          people and all(p.get("status") == "archived" for p in people
                         if p.get("canonical_name") == "Quinn Alvarez"))
    reopened = {p["seq"] for p in load_open_person_proposals(
        ws / "_hq" / "data" / "events.jsonl")}
    check("undo reopens the EXPIRY tombstone only (adds stay resolved — a "
          "re-run must not re-add)", reopened == {3}, str(reopened))
    plan2 = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    check("re-run after undo does NOT re-add the archived person",
          plan2["results"]["added"] == [], str(plan2["results"]["added"]))

    # ------------------------------------------------------------------
    print("[4] F-3 attribution under the D2 bar")
    # ------------------------------------------------------------------
    ws = make_ws([
        _pp(1, 5, "Noa Lindgren", role="counsel",
            evidence="quoted thread: from sender@example.com, cc "
                     "admin@example.com — introduced their counsel",
            source_ref="mail:thread-88"),
    ])
    plan = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    check("no attributable address → NOT auto-added (attribution "
          "uncertainty is the no-auto case)",
          plan["results"]["added"] == [] and len(plan["confirm"]) == 1,
          f"added={plan['results']['added']}, confirm={len(plan['confirm'])}")

    # ------------------------------------------------------------------
    print("[5] merge-propose (D4) — exact-email link, role guard, dup scan")
    # ------------------------------------------------------------------
    people = [
        {"id": "person_001", "canonical_name": "Sam Reyes",
         "first_seen": ts(400)[:10], "emails": ["sam.reyes@example.com"]},
        {"id": "person_002", "canonical_name": "Val Okafor",
         "first_seen": ts(300)[:10], "emails": ["info@example.org"]},
    ]
    ws = make_ws([
        # exact-email on-file match → silent same_as link (§0-2 YES)
        _pp(1, 10, "Sam Reyes",
            evidence="wrote from sam.reyes@example.com",
            source_ref="mail:thread-1"),
        # on-file match but the shared address is role-shaped → row, no link
        _pp(2, 9, "Val Okafor",
            evidence="via info@example.org",
            source_ref="mail:thread-2"),
    ], people=list(people))
    plan = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    res = plan["results"]
    check("exact personal email → silent same_as link",
          len(res["linked"]) == 1 and res["linked"][0]["person_id"]
          == "person_001", str(res["linked"]))
    check("role-shaped exact email NEVER silently links (§0-2 guard)",
          all(l["name"] != "Val Okafor" for l in res["linked"]))
    check("the role-address case became a merge-propose row instead",
          res["merge_rows_proposed"] >= 1, str(res["merge_rows_proposed"]))
    text = (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
    check("person_link proposal carries the registered bp verbs",
          '"person_link"' in text and '"confirm proposal"' in text)
    check("linked cluster tombstoned same_as",
          '"same_as"' in text)
    # link tombstones are reopenable (the §0-2 'undoable via the tombstone
    # reverser' ruling)
    undo = brain_undo.undo_batch(
        ws, {"kind": "brain_batch", "batch_id": plan["batch_id"]},
        undone_by="person_000", source_skill="apply-choices")
    reopened = {p["seq"] for p in load_open_person_proposals(
        ws / "_hq" / "data" / "events.jsonl")}
    check("undo reopens the link tombstone", 1 in reopened, str(reopened))

    # existing-record duplicate scan (D4b)
    dup_people = [
        {"id": "person_010", "canonical_name": "Ari Bell",
         "first_seen": ts(300)[:10], "role": "CEO"},
        {"id": "person_011", "canonical_name": "Ari Bell",
         "first_seen": ts(200)[:10]},
        # the two-Dons pin: single-token exact names are two real people
        {"id": "person_012", "canonical_name": "Don",
         "first_seen": ts(300)[:10]},
        {"id": "person_013", "canonical_name": "Don",
         "first_seen": ts(200)[:10]},
        # shared ROLE address — never a dup suspect
        {"id": "person_014", "canonical_name": "Lee Ward",
         "first_seen": ts(300)[:10], "emails": ["office@example.net"]},
        {"id": "person_015", "canonical_name": "Kit Marsh",
         "first_seen": ts(200)[:10], "emails": ["office@example.net"]},
        # shared personal address — a real suspect
        {"id": "person_016", "canonical_name": "Nia Cole",
         "first_seen": ts(300)[:10], "emails": ["nia@example.com"]},
        {"id": "person_017", "canonical_name": "N. Cole",
         "first_seen": ts(200)[:10], "emails": ["nia@example.com"]},
    ]
    ws = make_ws([_pp(1, 2, "Zed Quill", evidence="x",
                      source_ref="mail:t")], people=dup_people)
    suspects = ir.scan_existing_duplicates(ws)
    keys = {(s["keep"]["id"], s["duplicate"]["id"]) for s in suspects}
    check("multi-token exact-name pair IS a suspect",
          ("person_010", "person_011") in keys, str(keys))
    check("single-token name pair is NEVER a suspect (two-Dons pin)",
          not any("person_012" in k or "person_013" in k for pair in keys
                  for k in pair), str(keys))
    check("shared ROLE address is never a suspect",
          not any("person_014" in pair for pair in keys), str(keys))
    check("shared personal address IS a suspect",
          any("person_016" in pair and "person_017" in pair
              for pair in [(a, b) for a, b in keys]), str(keys))
    src = (ROOT / "shared" / "scripts" / "identity_reconcile.py").read_text(
        encoding="utf-8")
    check("NO code path in the reconciler calls or imports merge_person_into "
          "(merge is a user click, forever — docstring mentions allowed)",
          "merge_person_into(" not in src
          and "import merge_person_into" not in src
          and "merge_person_into," not in src)
    from verb_taxonomy import CANONICAL_ACTION_IDS, taxonomy_row
    check("`merge person records` is a registered verb",
          "merge person records" in CANONICAL_ACTION_IDS)
    check("its event is person_merged",
          (taxonomy_row("merge person records") or {}).get("event")
          == "person_merged")
    from brain_proposals import AUTO_ALLOWED
    check("person_merge is NOT in AUTO_ALLOWED (confirm-only forever)",
          "person_merge" not in AUTO_ALLOWED)
    check("no reverser exists for a person merge",
          not brain_undo.has_reverser("person_merge"))

    # ------------------------------------------------------------------
    print("[6] annotations (D5/§0-4)")
    # ------------------------------------------------------------------
    ws = make_ws([
        _pp(1, 3, None,
            evidence="Granola did not identify the second speaker",
            source_ref="granola:call-7"),
    ])
    rows = {i["id"]: i for i in load_open_proposals(ws)
            if i["kind"] == "person"}
    check("a nameless add row NEVER renders as a person row", rows == {},
          str(sorted(rows)))
    plan = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    check("nameless row converted to an annotation",
          len(plan["results"]["annotations"]) == 1,
          str(plan["results"]["annotations"]))
    anns = ir.load_open_annotations(ws)
    check("annotation event open", len(anns) == 1
          and anns[0]["data"]["meeting_source_ref"] == "granola:call-7")
    check("count_open_annotations feeds the staff-meeting line",
          ir.count_open_annotations(ws) == 1)
    check("the converted proposal left the queue",
          load_open_person_proposals(ws / "_hq" / "data" / "events.jsonl")
          == [])
    # email-join resolution: the annotation's address now matches a record
    from meeting_capture import build_unidentified_attendee_event
    from event_gate import append_event
    ws2 = make_ws([], people=[{"id": "person_001",
                               "canonical_name": "Noah Pell",
                               "first_seen": ts(100)[:10],
                               "emails": ["noah.pell@example.com"]}])
    ev = build_unidentified_attendee_event(
        "granola:call-8", attendee_hint="speaker 2",
        attendee_email="noah.pell@example.com")
    append_event(ws2 / "_hq" / "data" / "events.jsonl", [ev], holder="test")
    plan = ir.run_identity_reconcile(ws2, apply=True, now_iso=NOW_ISO)
    check("annotation resolves when its address matches a record (Tier-1 "
          "email join)", ir.count_open_annotations(ws2) == 0,
          str(ir.load_open_annotations(ws2)))
    check("builder refuses an empty hint",
          _raises(lambda: build_unidentified_attendee_event(
              "granola:x", attendee_hint="")))

    # ------------------------------------------------------------------
    print("[7] D8 — seq-less proposals adjudicate by fingerprint")
    # ------------------------------------------------------------------
    ws = make_ws([
        _pp(None, 6, "Pia Voss", evidence="from pia.voss@example.com",
            source_ref="mail:thread-1"),
    ])
    rows = load_open_person_proposals(ws / "_hq" / "data" / "events.jsonl")
    check("seq-less row loads with a fingerprint",
          len(rows) == 1 and rows[0]["seq"] is None
          and bool(rows[0]["fingerprint"]), str(rows))
    plan = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    check("seq-less auto cluster still adds",
          [a["name"] for a in plan["results"]["added"]] == ["Pia Voss"])
    check("fingerprint tombstone excludes it from the open set",
          load_open_person_proposals(ws / "_hq" / "data" / "events.jsonl")
          == [])
    # int-seq behavior byte-identical for seq-carrying rows: an int-seq row
    # never computes a fingerprint
    ws = make_ws([_pp(9, 2, "Ada West", evidence="x", source_ref="mail:t")])
    rows = load_open_person_proposals(ws / "_hq" / "data" / "events.jsonl")
    check("int-seq rows carry NO fingerprint (activation rule)",
          rows[0]["fingerprint"] is None)

    # ------------------------------------------------------------------
    print("[8] §0-3 caps — spill narrated, never silent")
    # ------------------------------------------------------------------
    # Four auto-eligible clusters with fully DISTINCT names (a shared token
    # would trip auto_add_person's same-name gate against the earlier adds
    # in the same run — which is correct behavior, but not what this cap
    # test is exercising).
    cap_people = [("Kai Ono", "kai.ono"), ("Lex Pratt", "lex.pratt"),
                  ("Mia Sorel", "mia.sorel"), ("Tom Wilder", "tom.wilder")]
    evs = []
    for i, (nm, local) in enumerate(cap_people):
        evs.append(_pp(i + 1, 5, nm,
                       evidence=f"from {local}@example.com",
                       source_ref=f"mail:t-{i}"))
    ws = make_ws(evs)
    plan = ir.run_identity_reconcile(
        ws, apply=True, now_iso=NOW_ISO,
        caps={"auto_add": 2, "merge_propose": 10})
    check("auto cap honored", len(plan["results"]["added"]) == 2)
    check("overflow counted in the receipt, never dropped",
          plan["results"]["spilled"]["auto_add"] == 2,
          str(plan["results"]["spilled"]))
    text = (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
    check("receipt carries the spill", '"spilled"' in text)
    spilled_open = load_open_person_proposals(
        ws / "_hq" / "data" / "events.jsonl")
    check("spilled clusters stay OPEN in the queue",
          len(spilled_open) == 2, str(len(spilled_open)))

    # ------------------------------------------------------------------
    print("[9] D6 narration — changes_since reads the receipt")
    # ------------------------------------------------------------------
    ws = make_ws([
        _pp(1, 5, "Rio Tanaka", evidence="from rio.tanaka@example.com",
            source_ref="mail:t-1"),
        _pp(2, 4, "Sam Reyes", evidence="wrote from sam.reyes@example.com",
            source_ref="mail:t-2"),
    ], people=[{"id": "person_001", "canonical_name": "Sam Reyes",
                "first_seen": ts(400)[:10],
                "emails": ["sam.reyes@example.com"]}])
    ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    feed = changes_since(ws, ts(1))
    cats = {l["category"]: l for l in feed["lines"]}
    check("people_added line renders with refs",
          "people_added" in cats and cats["people_added"]["refs"]
          and "say `undo` to reverse" in cats["people_added"]["text"],
          str(cats.get("people_added")))
    check("people_linked line renders",
          "people_linked" in cats, str(sorted(cats)))
    empty = changes_since(ws, (NOW + _dt.timedelta(minutes=5))
                          .strftime("%Y-%m-%dT%H:%M:%SZ"))
    check("empty window → no identity lines (drop-empty)",
          not any(l["category"] in ("people_added", "people_linked")
                  for l in empty["lines"]))

    # ------------------------------------------------------------------
    print("[10] render (D3) — cluster rows, title fix, count honesty")
    # ------------------------------------------------------------------
    ws = make_ws([
        _pp(1, 12, "Quinn Alvarez", org="Acme Co",
            evidence="kickoff", source_ref="granola:call-1"),
        _pp(2, 8, "Quinn Alvarez", role="Ops lead",
            evidence="thread", source_ref="mail:thread-2"),
        _pp(3, 6, "Quinn", evidence="bare mention",
            source_ref="granola:call-9"),
        _pp(4, 3, None, evidence="unnamed speaker",
            source_ref="granola:call-7"),
        _pp(5, 2, "irrelevant", etype="person_update_proposal",
            person_id="person_001", evidence="new role heard",
            source_ref="granola:9c1f-uuid"),
    ], people=[{"id": "person_001", "canonical_name": "Val Okafor",
                "first_seen": ts(300)[:10]}])
    rows = {i["id"]: i for i in load_open_proposals(ws)
            if i["kind"] == "person"}
    check("one row per cluster + the standalone update row",
          len(rows) == 3, str(sorted(rows)))
    qa_row = rows.get("person:1") or {}
    check("cluster row carries data cluster_seqs",
          qa_row.get("cluster_seqs") == [1, 2], str(qa_row.get("cluster_seqs")))
    check("cluster row title is the person", qa_row.get("title")
          == "Quinn Alvarez")
    check("multi-mention render line says seen N×",
          "seen 2×" in qa_row.get("render_line", ""),
          qa_row.get("render_line"))
    check("bare-first-name cluster renders separately (its own decision)",
          "person:3" in rows)
    check("nameless row absent from the render", "person:4" not in rows)
    upd = rows.get("person:5") or {}
    check("update-row title is the record's canonical name — NEVER a raw "
          "granola uuid", upd.get("title") == "Val Okafor — update",
          str(upd.get("title")))
    person_rows = [i for i in load_open_proposals(ws)
                   if i["kind"] == "person"]
    cv = build_card_view(person_rows)
    n_rows = sum(len(s["items"]) for s in cv["sections"])
    check("build_card_view count matches visible rows (RV-4)",
          n_rows == 3 and f"— {n_rows} " in cv["header"], cv["header"])
    row_data = [it["data"] for s in cv["sections"] for it in s["items"]]
    check("card rows embed cluster_seqs verbatim (F2)",
          any(d.get("cluster_seqs") == [1, 2] for d in row_data),
          str(row_data))
    from identity_reconcile import count_person_rows
    loader_rows = load_open_person_proposals(
        ws / "_hq" / "data" / "events.jsonl", suppress_on_file=True)
    check("pointer count == rendered rows (step 10 honesty)",
          count_person_rows(loader_rows, now_iso=NOW_ISO) == 3)

    # ------------------------------------------------------------------
    print("[11] second-eyes regression pins (PID1 review)")
    # ------------------------------------------------------------------
    # F1 — a sole ROLE-shaped address is not corroboration and never lands
    # on a record: the §0-2 shared-inbox doctrine applies to the auto-ADD
    # bar too (a stored info@ would poison Tier-1 email resolution).
    ws = make_ws([_pp(1, 5, "Jan Fielder",
                      evidence="reach us via info@example.com",
                      source_ref="mail:t-1")])
    plan = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    check("role-shaped observed address does NOT auto-add (F1)",
          plan["results"]["added"] == [] and len(plan["confirm"]) == 1,
          f"added={plan['results']['added']}, confirm={len(plan['confirm'])}")
    ents_text = (ws / "_hq" / "data" / "entities.json").read_text(
        encoding="utf-8")
    check("role address never written to any record (F1)",
          "info@example.com" not in ents_text)

    # F2 — an observed address exactly matching an EXISTING record is a
    # link ROW (D4a): never a duplicate auto-add, never the §0-2 silent
    # link (name and email signals disagree), and zero error-loop churn.
    ws = make_ws([_pp(1, 5, "Jona Smythe",
                      evidence="wrote from jk@example.com",
                      source_ref="mail:t-1")],
                 people=[{"id": "person_001", "canonical_name": "Jenn Kimm",
                          "first_seen": ts(300)[:10],
                          "emails": ["jk@example.com"]}])
    plan = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    res = plan["results"]
    check("email-on-file cluster is a link ROW — never auto, never "
          "silently linked, zero errors (F2)",
          res["added"] == [] and res["linked"] == []
          and res["merge_rows_proposed"] == 1 and res["errors"] == [],
          f"added={res['added']} linked={res['linked']} "
          f"rows={res['merge_rows_proposed']} errors={res['errors']}")
    text = (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
    check("the F2 row is a person_link proposal naming the record",
          '"person_link"' in text and "Jenn Kimm" in text)

    # F3 — annotation conversion is idempotent across an undo: reopened
    # proposal + immutable annotation event must not double-count one
    # speaker on the staff-meeting line.
    ws = make_ws([_pp(1, 3, None, evidence="unnamed second speaker",
                      source_ref="granola:call-7")])
    plan = ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    brain_undo.undo_batch(ws, {"kind": "brain_batch",
                               "batch_id": plan["batch_id"]},
                          undone_by="person_000", source_skill="apply-choices")
    ir.run_identity_reconcile(ws, apply=True, now_iso=NOW_ISO)
    check("undo → re-run keeps exactly ONE annotation (F3)",
          ir.count_open_annotations(ws) == 1,
          str(ir.load_open_annotations(ws)))

    # F4 — guess/annotation markers in a captured name never auto (the
    # live queue's "<name> (or <other> alt account)" shape, placeholder-
    # ified): a captured note is not a canonical name.
    t, cls = tier_of([_pp(1, 5, "Chase Sample (or Alex alt account)",
                          evidence="from qm.sample@example.com",
                          source_ref="mail:t")])
    check("a name carrying guess markers stays CONFIRM (F4)",
          t == "confirm", str(cls))
    t, cls = tier_of([_pp(1, 5, "Quinn Alvarez",
                          evidence="from quinn.alvarez@example.com",
                          source_ref="mail:t")])
    check("a clean multi-token name still autos (F4 guard is narrow)",
          t == "auto", str(cls))

    if failures:
        print(f"\nidentity reconcile FAIL — {len(failures)} of {checks}")
        return 1
    print(f"identity reconcile battery: {checks} checks OK")
    return 0


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:
        return True


if __name__ == "__main__":
    raise SystemExit(main())
