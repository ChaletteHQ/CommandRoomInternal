#!/usr/bin/env python3
"""MLK1 — retire "Add to my list" / the discuss list (M ruling 2026-07-21).

Covers the spec's five pins:
(a) NO source emits the verb — no `"actions": [...]` string-array literal in
    skills/ offers it, no driver verb set in shared/scripts carries it;
(b) old persisted-widget dispatch still works — the wire id stays registered
    (canonical, never a DEPRECATED_ALIAS: an old click must keep its ORIGINAL
    meaning, `commitment_to_discuss`, not someone else's), a legacy-markup
    tuple parses, the renderer still accepts the verb from an in-flight
    widget, and the display label is banned on NEW renders;
(c) orphan-note re-route, both branches — resolves → ONE `note` event on the
    target person/thread; unresolvable → declined with the honest line and
    NOTHING written;
(d) show-my-list is a drain-only fossil reader — the fossil render carries
    drain verbs only (Done / Drop) + the retirement line, and the mute
    ledger is untouched;
(e) the Pulse this-week header drops the "captured to list" segment.

Real-substrate-shaped fixtures; placeholder names only (Sam Sample / Quinn
Sample); dates computed relative to today (G14).
"""
from __future__ import annotations

import datetime
import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "shared" / "scripts"))

import verb_taxonomy as vt  # noqa: E402
from chat_output_renderer import (  # noqa: E402
    CANONICAL_ACTIONS,
    is_canonical_action,
    render_chat_output_widget,
)
from orphan_note import DECLINE_LINE, reroute_orphan_note  # noqa: E402

PASS = 0


def check(cond, msg):
    global PASS
    assert cond, "FAIL: " + msg
    PASS += 1


def _iso(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.datetime.now(datetime.timezone.utc)


# ===========================================================================
# (a) emission source pin — nothing offers the verb anymore
# ===========================================================================
print("[a] no source emits the verb")

# G4-style scan: every "actions": [...] string-array literal in skills/.
ACTIONS_RE = re.compile(r'"actions"\s*:\s*\[([^\]]*)\]')
STR_RE = re.compile(r'(?:f?")([^"]+)"')


def _normalize(verb: str) -> str:
    v = verb.strip()
    v = re.sub(r"^\{[^}]*\}\s*", "", v)          # f-string index prefix
    v = re.sub(r"^(?:fr)?\d+[a-z]?\s+", "", v)   # item-number prefix
    return v.strip()


emitters = []
for p in sorted((REPO / "skills").rglob("*")):
    if p.suffix not in (".md", ".html") or not p.is_file():
        continue
    text = p.read_text(encoding="utf-8")
    for m in ACTIONS_RE.finditer(text):
        for s in STR_RE.findall(m.group(1)):
            if _normalize(s) == "add to my list":
                emitters.append(p.relative_to(REPO).as_posix())
check(not emitters,
      f"no skills/ actions-array offers the verb; found in: {emitters}")

# Driver verb sets (the CTS1FIX pin, extended to every module-level set).
import surface_drivers as sd  # noqa: E402

for name in dir(sd):
    if name.endswith("_VERBS"):
        verbs = getattr(sd, name)
        check("add to my list" not in verbs,
              f"surface_drivers.{name} does not carry the verb")

# The bulleted pill examples in the orchestrator reply-handling sections
# (`- \`N add to my list\`` handler docs) are gone too.
PILL_RE = re.compile(r"[`▸]\s*(?:\[?[N\dA-Za-z/]+\]?\s+)?add to my list")
offenders = []
for p in sorted((REPO / "skills").rglob("*.md")):
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if PILL_RE.search(line) and "retired" not in line.lower() \
                and "MLK1" not in line:
            offenders.append(f"{p.relative_to(REPO).as_posix()}:{i}")
check(not offenders,
      f"no orchestrator pill/handler prose offers the verb: {offenders}")

# The Pulse fallback copy no longer tells the user to use the retired verb.
dontforget = (REPO / "skills" / "enable-command-room-schedules" / "references"
              / "orchestrator-dont-forget.md").read_text(encoding="utf-8")
check("`add to my list` to triage" not in dontforget,
      "the (no role tracked yet) fallback stopped advertising the verb")

# meeting-notes agenda items no longer capture to the list.
meeting_notes = (REPO / "skills" / "meeting-notes" / "SKILL.md").read_text(
    encoding="utf-8")
check("the existing `commitment_to_discuss` type (unchanged)" not in meeting_notes,
      "meeting-notes no longer instructs the agenda-item list write")
check("NOT captured (MLK1" in meeting_notes,
      "meeting-notes carries the below-the-floor agenda rule")

# ===========================================================================
# (b) old-widget dispatch back-compat — load-bearing, never re-aliased
# ===========================================================================
print("[b] persisted-widget dispatch back-compat")

check("add to my list" in vt.CANONICAL_ACTION_IDS,
      "wire id stays registered (old widgets must dispatch)")
check("add to my list" not in vt.DEPRECATED_ALIASES,
      "NEVER aliased — an old click keeps its original meaning")
row = vt.taxonomy_row("add to my list")
check(row is not None and row["event"] == "commitment_to_discuss",
      "the id still maps to its ORIGINAL write (commitment_to_discuss)")
check("retired" in (row["notes"] or "").lower(),
      "taxonomy row is marked retired in its notes")
check("add to my list" in CANONICAL_ACTIONS and
      is_canonical_action("add to my list"),
      "renderer still accepts the verb from an in-flight widget")

# Legacy-markup tuple parse: the exact choice shapes an old widget's JS emits
# (explicit click, and the orphan-note carrier with context).
ALLOWED_KEYS = {"n", "action", "sub", "input", "src", "context"}
legacy_tuples = [
    {"n": 3, "action": "add to my list", "src": "commitments"},
    {"n": "1a", "action": "add to my list",
     "context": "circle back on the pilot", "src": "past-meetings"},
]
for tup in legacy_tuples:
    check(set(tup) <= ALLOWED_KEYS and is_canonical_action(tup["action"]),
          f"legacy tuple parses + validates: {tup}")
check(_normalize("7a add to my list") == "add to my list",
      "item-prefixed legacy wire form normalizes to the registered id")

# A legacy persisted widget still RENDERS + validates (acceptance ≠ emission).
legacy_view = {
    "widget_mode": "all_batch_widget",
    "source_skill": "commitments",
    "header": "Waiting on 1 thing",
    "sections": [{"title": None, "count": None, "items": [{
        "n": 1, "icon": None, "name": "Sam Sample",
        "context_tag": "committed 16 days ago",
        "actions": ["1 mark received", "1 not relevant", "1 add to my list"],
    }]}],
}
html = render_chat_output_widget(legacy_view, wrapper="fragment")
check(isinstance(html, str) and len(html) > 500,
      "legacy-markup fixture renders without raising")

# Display label banned on NEW renders.
check("Add to my list" in vt.LEGACY_DISPLAY_LABELS,
      "display label joined LEGACY_DISPLAY_LABELS")
check(vt.DISPLAY_LABELS.get("add to my list") == "Add to my list",
      "the id's own label is unchanged (old widgets keep their wording)")

# The orphan-note carrier keeps the legacy wire id — one shape for old and
# new widgets (re-route happens at dispatch, never a second wire id).
renderer_src = (REPO / "shared" / "scripts" / "chat_output_renderer.py"
                ).read_text(encoding="utf-8")
check("action: 'add to my list', context: f.value.trim()" in renderer_src,
      "crApplyAll orphan-note carrier still emits the legacy wire id")

# Instruction layer: apply-choices routes the carrier through the helper and
# keeps the context-less fossil write.
apply_md = (REPO / "skills" / "apply-choices" / "SKILL.md").read_text(
    encoding="utf-8")
check("reroute_orphan_note" in apply_md,
      "apply-choices names the re-route helper (instruction layer, G13 class)")
check("never aliased to a different action" in apply_md,
      "apply-choices pins the never-alias rule")
check("acceptable fossil trickle" in apply_md,
      "the context-less stale-widget click keeps its fossil write")

# ===========================================================================
# (c) orphan-note re-route — both branches, real-shaped fixtures
# ===========================================================================
print("[c] orphan-note re-route")


def _mk_workspace(events):
    ws = Path(tempfile.mkdtemp(prefix="mlk1_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "entities.json").write_text(json.dumps({
        "version": 1,
        "people": [
            {"id": "person_1", "canonical_name": "Sam Sample"},
            {"id": "person_2", "canonical_name": "Quinn Sample"},
        ],
        "orgs": [],
        "threads": [{"id": "thread_acme", "display_name": "Acme Co pilot"}],
        "engagements": [],
    }), encoding="utf-8")
    with open(ws / "_hq" / "data" / "events.jsonl", "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return ws


def _events(ws):
    out = []
    with open(ws / "_hq" / "data" / "events.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


# Branch 1a: source event resolves to a PERSON (counterparty_id).
src_commitment = {
    "seq": 12, "type": "commitment",
    "ts": _iso(NOW - datetime.timedelta(days=9)),
    "source_skill": "meeting-notes",
    "data": {"id": "c_012", "kind": "promise",
             "title": "Send Sam the pricing options",
             "counterparty_id": "person_1", "owner_id": "person_2"},
}
ws = _mk_workspace([src_commitment])
res = reroute_orphan_note(ws, "he mentioned budget pressure", 12)
check(res["outcome"] == "noted" and res["target_kind"] == "person"
      and res["target_id"] == "person_1",
      f"person branch: noted on the resolved counterparty, got {res}")
evs = _events(ws)
check(len(evs) == 2 and evs[-1]["type"] == "note",
      "person branch: exactly ONE note event appended")
note = evs[-1]
check(note["data"]["summary"] == "he mentioned budget pressure"
      and note["data"]["via"] == "orphan_note_capture"
      and note["data"]["person_id"] == "person_1"
      and note["data"]["source_event_seq"] == 12,
      f"person branch: note shape carries summary/via/person/seq, got {note}")
check("commitment_to_discuss" not in {e["type"] for e in evs},
      "person branch: NO list item was written — the list is retired")

# Branch 1b: no person, but the envelope resolves to a THREAD.
src_thread_ev = {
    "seq": 30, "type": "interaction",
    "ts": _iso(NOW - datetime.timedelta(days=2)),
    "primary_thread_id": "thread_acme",
    "data": {"summary": "pilot kickoff sync"},
}
ws2 = _mk_workspace([src_thread_ev])
res2 = reroute_orphan_note(ws2, "pilot scope may grow", 30)
check(res2["outcome"] == "noted" and res2["target_kind"] == "thread"
      and res2["target_id"] == "thread_acme",
      f"thread branch: noted on the resolved thread, got {res2}")
note2 = _events(ws2)[-1]
check(note2["type"] == "note"
      and note2.get("primary_thread_id") == "thread_acme",
      "thread branch: the note rides the thread envelope")

# Branch 2: NOTHING resolves → declined, nothing written.
src_orphan = {
    "seq": 44, "type": "commitment",
    "ts": _iso(NOW - datetime.timedelta(days=1)),
    "data": {"id": "c_044", "kind": "promise",
             "title": "This is the company from the call from last week."},
}
ws3 = _mk_workspace([src_orphan])
before = _events(ws3)
res3 = reroute_orphan_note(ws3, "mira stone", 44)  # a name NOT on file — stays unresolvable
check(res3["outcome"] == "declined" and res3["line"] == DECLINE_LINE,
      f"unresolvable branch: declined with the honest line, got {res3}")
check(_events(ws3) == before,
      "unresolvable branch: NOTHING was written")

# Degenerate shapes decline too (never crash, never write).
res4 = reroute_orphan_note(ws3, "", 44)
res5 = reroute_orphan_note(ws3, "dangling", 999)
check(res4["outcome"] == "declined" and res5["outcome"] == "declined"
      and _events(ws3) == before,
      "empty text / missing source event both decline with nothing written")

# ===========================================================================
# (d) show-my-list — drain-only fossil render + mute ledger untouched
# ===========================================================================
print("[d] fossil reader + mute ledger")

# Real-shaped commitment_to_discuss fixture (M's live shape: summary + via).
fossil_view = {
    "widget_mode": "all_batch_widget",
    "source_skill": "show-my-list",
    "header": "Your list — 1 to discuss · oldest 9 days",
    "sub_header": "This list is retired — new items go to My Plate or a contact note.",
    "sections": [{"title": None, "count": None, "items": [{
        "n": 1, "icon": "💬", "name": "Sam Sample",
        "context_tag": "next time you talk to Sam Sample (1)",
        "body_lines": ["- circle back on the pilot"],
        "actions": ["1 resolved", "1 drop"],
    }]}],
}
fossil_html = render_chat_output_widget(fossil_view, wrapper="fragment")
check("This list is retired — new items go to My Plate or a contact note." in fossil_html,
      "fossil render carries the retirement line")
check("Add to my list" not in fossil_html,
      "the banned label never renders on the fossil view")
check("resolved" in fossil_html and "drop" in fossil_html,
      "drain verbs (resolved/drop) are wired on the fossil rows")

# SKILL.md instruction pins.
sml = (REPO / "skills" / "show-my-list" / "SKILL.md").read_text(encoding="utf-8")
check('"actions": [f"{i} resolved", f"{i} drop"]' in sml,
      "SKILL prescribes the drain-only verb pair")
check("This list is retired — new items go to My Plate or a contact note." in sml,
      "SKILL prescribes the retirement line verbatim")
check("Your list is empty. This list is retired" in sml,
      "the drained empty state stands alone (no capture advertisement)")
check('"show muted" / "show snoozed"' in sml or "`show muted` / `show snoozed`" in sml,
      "mute-ledger mode survives in the SKILL")
check("live_mutes" in sml and "unmute" in sml,
      "mute-ledger render + Unmute dispatch prose untouched")

# Mute ledger behavior untouched: a live dismissal still surfaces with TTL.
from mute_ledger import live_mutes  # noqa: E402

dismissal = {
    "seq": 7, "type": "chat_dismissal",
    "ts": _iso(NOW - datetime.timedelta(days=1)),
    "data": {"target_id": "c_012", "reason": "snoozed",
             "snooze_until": _iso(NOW + datetime.timedelta(days=2)),
             "surface": "commitments"},
}
rows = live_mutes([dismissal], _iso(NOW))
check(len(rows) == 1 and rows[0].get("ttl_label"),
      "mute ledger still lists a live snooze with its remaining-time label")

# The quick-commands artifact swapped the retired card for the ledger.
qc = (REPO / "skills" / "enable-quick-commands" / "references"
      / "quick-commands-artifact.html").read_text(encoding="utf-8")
check('data-prompt="show muted"' in qc,
      "quick-commands artifact carries the `show muted` card")
check('data-prompt="show my list"' not in qc,
      "quick-commands artifact dropped the retired-list card")

# Call-prep's Parked to Discuss stays as a drain-only reader (D4): the
# reader function is intact and the SKILL says drain-only, not deleted.
import prep_pipeline  # noqa: E402

bullets = prep_pipeline.discuss_later_bullets(
    [{"type": "commitment_to_discuss",
      "data": {"person_id": "person_1", "summary": "pricing follow-up"}}],
    attendee_person_ids=["person_1"], attendee_names=["Sam Sample"])
check(bullets == ["pricing follow-up"],
      "call-prep's Parked-to-Discuss reader still drains open items")
callprep = (REPO / "skills" / "call-prep" / "SKILL.md").read_text(encoding="utf-8")
check("drain-only" in callprep and "Parked to Discuss" in callprep,
      "call-prep pins the block as drain-only (not deleted)")

# ===========================================================================
# (e) Pulse header drops the captured-to-list segment
# ===========================================================================
print("[e] pulse header segment")

segment_hits = [
    ln for ln in dontforget.splitlines()
    if "captured to list" in ln.lower() and "MLK1" not in ln
]
check(not segment_hits,
      f"no live captured-to-list segment remains: {segment_hits}")
check("This week: 7 resolved · 3 pending review · 2 going quiet." in dontforget,
      "the example header carries exactly three segments")
check("commitment_to_discuss` events in window" not in dontforget,
      "the header no longer counts commitment_to_discuss events")

print(f"OK — {PASS} checks passed")
