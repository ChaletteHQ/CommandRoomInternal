#!/usr/bin/env python3
"""
Meeting-prep v2 regression battery (v4.5.2 S1 — FINDINGS_M_v451 F-60 /
F-29 / F-29b).

Fixtures mirror the REAL dogfood shapes (per the cr-realdata-fixture
gotcha), the finding each check reproduces is cited inline:

  F-29  — the morning brief claimed "no prep brief" for the 9:15 Mira
          call while the prep file + fire receipt were both on disk. The fix
          is the per-brief `prep_brief` receipt: the flag may only render
          when NO receipt exists for that meeting id.
  F-29b — 'prep me' minted a second, differently-slugged brief
          (`acme-bo-barrow-session` vs `robert-barrow`) for ONE
          meeting. The fix is prep_slug (identity = the meeting id) +
          resolve_prep_brief_path refresh-in-place.
  F-60  — visual-layer drop rule: a tile with no data is DROPPED, never
          rendered as an empty frame — enforced at build (prep_pipeline)
          AND at the render chokepoint (brief_writer).
  F-44  — carried into prep: undated, name-mention-matched commitments stay
          visible in the OWED table ("no date set", never hidden).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import prep_pipeline as P  # noqa: E402
import receipts as R  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f"  FAIL {label}  {detail}")


def make_workspace(base: Path) -> Path:
    (base / "_hq" / "data").mkdir(parents=True, exist_ok=True)
    (base / "_hq" / "meetings").mkdir(parents=True, exist_ok=True)
    return base


# ---------------------------------------------------------------------------
print("== F-29b — slug is a pure function of the meeting id ==")
# The real duplicate: one Sam Sample meeting, two attendee-name phrasings.
MEETING_ID = "6kq3jv7p8a_20260708T150000Z"  # Google Calendar event-id shape
s1 = P.prep_slug(MEETING_ID, "Acme Freight — Sam Sample session")
s2 = P.prep_slug(MEETING_ID, "Sam — Acme weekly sync")
s3 = P.prep_slug(MEETING_ID)
check("same meeting id -> same identity suffix (title phrasing 1 vs 2)",
      s1.split("-")[-1] == s2.split("-")[-1], f"{s1} vs {s2}")
check("titleless slug is the bare hash", s3 == s1.split("-")[-1], s3)
check("different meeting id -> different identity",
      P.prep_slug("other_event_id").split("-")[-1] != s3)

with tempfile.TemporaryDirectory() as td:
    ws = make_workspace(Path(td))
    r1 = P.resolve_prep_brief_path(ws, MEETING_ID,
                                   title="Acme Freight — Sam Sample session",
                                   date_iso="2026-07-08")
    check("first resolve is a fresh path", r1["refresh"] is False)
    Path(r1["path"]).write_bytes(b"docx-bytes")
    # Re-prep with the OTHER phrasing AND a different date (the F-29b repro).
    r2 = P.resolve_prep_brief_path(ws, MEETING_ID,
                                   title="Sam — Acme weekly sync",
                                   date_iso="2026-07-09")
    check("re-prep resolves to the SAME file (refresh-in-place)",
          r2["path"] == r1["path"], f"{r2['path']} vs {r1['path']}")
    check("re-prep is flagged refresh", r2["refresh"] is True)
    briefs = [n for n in os.listdir(ws / "_hq" / "meetings")
              if n.startswith("Call_Prep_")]
    check("no sibling file exists for the same meeting", len(briefs) == 1,
          str(briefs))
    check("unrelated meeting resolves to a different file",
          P.resolve_prep_brief_path(ws, "other_event_id", title="x",
                                    date_iso="2026-07-09")["path"] != r1["path"])

# ---------------------------------------------------------------------------
print("== F-29 — the no-prep flag reads prep_brief receipts, only receipts ==")
with tempfile.TemporaryDirectory() as td:
    ws = make_workspace(Path(td))
    # The real scenario: the 6:35 AM upcoming-meetings fire generated
    # Call_Prep_mira...docx + its receipt; the 7:14 AM brief said "no prep".
    res = P.resolve_prep_brief_path(ws, "mira_q4_20260708",
                                    title="Mira - Pat - Quinn",
                                    date_iso="2026-07-08")
    Path(res["path"]).write_bytes(b"docx-bytes")
    R.log_prep_receipt(ws, meeting_id="mira_q4_20260708", slug=res["slug"],
                       brief_path=res["path"], generated_by="upcoming-meetings",
                       fired_via="scheduled", refreshed=False)
    check("prep receipt exists -> the no-prep flag MUST NOT render",
          R.prep_exists_for_meeting(ws, "mira_q4_20260708") is True)
    check("no receipt -> flag may render",
          R.prep_exists_for_meeting(ws, "some_unprepped_meeting") is False)
    check("empty/None meeting id never claims prep exists",
          R.prep_exists_for_meeting(ws, "") is False
          and R.prep_exists_for_meeting(ws, None) is False)

    rs = R.prep_receipts(ws)
    check("receipt carries the meeting id + artifact",
          len(rs) == 1 and rs[0]["meeting_id"] == "mira_q4_20260708"
          and rs[0]["artifact"].startswith("Call_Prep_"))
    check("receipt fired_via normalized", rs[0]["fired_via"] == "scheduled")

    # prep_brief receipts are NOT task runs (five briefs in one fire = one
    # pack_run) — counting them would fabricate fires, the F-49 disease.
    counts = R.count_runs(ws)
    check("prep_brief receipts never count as task runs",
          all(v == 0 for v in counts.values()), str(counts))

    # A second brief in the same fire, then a manual refresh — 3 receipts,
    # still zero run counts, latest refresh visible.
    res2 = P.resolve_prep_brief_path(ws, "bo_sod_20260708",
                                     title="Bo Sample SOD",
                                     date_iso="2026-07-08")
    Path(res2["path"]).write_bytes(b"docx-bytes")
    R.log_prep_receipt(ws, meeting_id="bo_sod_20260708", slug=res2["slug"],
                       brief_path=res2["path"], generated_by="upcoming-meetings",
                       fired_via="scheduled")
    R.log_prep_receipt(ws, meeting_id="mira_q4_20260708", slug=res["slug"],
                       brief_path=res["path"], generated_by="call-prep",
                       fired_via="manual", refreshed=True)
    check("per-meeting filter returns only that meeting's receipts",
          len(R.prep_receipts(ws, meeting_ids=["mira_q4_20260708"])) == 2)
    check("refresh receipt records refreshed=True",
          R.prep_receipts(ws, meeting_ids=["mira_q4_20260708"])[-1]["refreshed"] is True)
    check("run counts still unpolluted after 3 prep receipts",
          all(v == 0 for v in R.count_runs(ws).values()))

# Bad-input guards on the writer.
with tempfile.TemporaryDirectory() as td:
    ws = make_workspace(Path(td))
    for label, kwargs in [
        ("missing meeting_id rejected", dict(meeting_id="", slug="s", brief_path="p")),
        ("missing slug rejected", dict(meeting_id="m", slug=" ", brief_path="p")),
        ("bad fired_via rejected", dict(meeting_id="m", slug="s", brief_path="p",
                                        fired_via="cron-ish")),
    ]:
        try:
            R.log_prep_receipt(ws, **kwargs)
            check(label, False, "no ValueError raised")
        except ValueError:
            check(label, True)

# ---------------------------------------------------------------------------
print("== Visual layer — tile drop rule (build side + render chokepoint) ==")
tiles = P.build_prep_tiles(days_since_last_touch=12, you_owe=2,
                           oldest_owed_days=47, touch_number=5)
check("unknown datum (they_owe=None) -> that tile dropped",
      all(t["label"] != "Owed to you" for t in tiles), json.dumps(tiles))
check("known data render", any(t["label"] == "You owe" for t in tiles))
check("oldest age rides the owed tile",
      any("oldest 47d" in t["value"] for t in tiles))
check("nothing known -> empty band (caller omits the section)",
      P.build_prep_tiles() == [])
check("zero is data, not absence",
      any(t["value"] == "0" for t in P.build_prep_tiles(you_owe=0)))
check("widget counters come from the SAME tiles",
      P.tiles_to_counters(tiles) == tiles and P.tiles_to_counters(tiles) is not tiles)

tl = P.build_relationship_timeline(
    [{"date": "Jun 2", "label": "Kickoff call"},
     {"date": "Jun 18", "label": "Pricing email"}])
check("timeline appends the current-meeting marker",
      tl[-1]["current"] is True and len(tl) == 3)
check("current-meeting-only timeline -> dropped (no history = empty frame)",
      P.build_relationship_timeline(
          [{"date": "Jul 8", "label": "Q4 check-in", "current": True}]) == [])
check("one historic touch + current marker IS signal -> renders as 2 points",
      len(P.build_relationship_timeline([{"date": "Jun 2", "label": "Kickoff"}])) == 2)
check("undated/unlabeled points dropped, not rendered",
      P.build_relationship_timeline(
          [{"date": "", "label": "x"}, {"date": "Jul 1", "label": ""}]) == [])

# Render chokepoint: brief_writer REFUSES an empty tile / one-point strip.
from brief_writer import make_brief  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    out = str(Path(td) / "x.docx")
    for label, sections in [
        ("brief_writer refuses an empty-value tile",
         [{"heading": "At a Glance", "tiles": [{"label": "You owe", "value": " "}]},
          {"heading": "Meeting Details", "body": "x"}]),
        ("brief_writer refuses a one-point timeline",
         [{"heading": "Relationship Timeline",
           "timeline": [{"date": "Jun 2", "label": "Kickoff"}]},
          {"heading": "Meeting Details", "body": "x"}]),
    ]:
        try:
            make_brief(out, brief_kind="call_prep", title="t", subtitle="s",
                       sections=sections, contract="off", voice_gate="off")
            check(label, False, "no ValueError raised")
        except ValueError:
            check(label, True)

# ---------------------------------------------------------------------------
print("== OWED table — F-44's undated name-mention items stay visible ==")
# The real shapes: the sweep-recovered, undated, pending_review Mira item
# plus a dated overdue item and one owed TO the user.
matched = [
    {"commitment_id": "cmt_01", "title": "soft-sell the video-testimonial idea to Meera",
     "kind": "task", "due": None, "owner_id": "person_001", "counterparty_id": None,
     "pending_review": True, "meeting_id": "mira_q4", "match": "name_mention"},
    {"commitment_id": "cmt_02", "title": "send positioning briefs",
     "kind": "promise", "due": "2026-07-08", "owner_id": "person_001",
     "counterparty_id": "person_093", "pending_review": False,
     "meeting_id": "mira_q4", "match": "counterparty"},
    {"commitment_id": "cmt_03", "title": "Mira to send the Q4 leader roster",
     "kind": "promise", "due": None, "owner_id": "person_093",
     "counterparty_id": "person_001", "pending_review": False,
     "meeting_id": "mira_q4", "match": "counterparty"},
]
tab = P.build_owed_table(matched, user_person_id="person_001", now_date="2026-07-09")
flat = json.dumps(tab)
check("two-column headers", tab["headers"] == ["You owe", "Owed to you"])
check("undated item visible with 'no date set'", "no date set" in flat)
check("pending_review renders as confirm-ask, not settled fact",
      "needs a quick confirm" in flat)
check("overdue phrasing on the dated item", "overdue (was due 2026-07-08)" in flat)
you_col = [r[0] for r in tab["rows"] if r[0]]
they_col = [r[1] for r in tab["rows"] if r[1]]
check("direction split by owner", len(you_col) == 2 and len(they_col) == 1,
      flat)
check("nothing owed -> None (section dropped, never an empty table)",
      P.build_owed_table([], user_person_id="person_001") is None)

discuss = P.discuss_later_bullets(
    [{"type": "commitment_to_discuss",
      "data": {"person_id": "person_093", "title": "Denver expansion timing"}},
     {"type": "commitment_to_discuss",
      "data": {"person": "Parker Vale", "title": "Northstar invoice split"}},
     {"type": "note", "data": {"title": "not a discuss item"}}],
    attendee_person_ids=["person_093"],
    attendee_names=["Mira Chen"])
check("discuss-later filtered to these attendees",
      discuss == ["Denver expansion timing"], str(discuss))

# ---------------------------------------------------------------------------
print("== Five-block assembly — sourced lines + section order ==")
check("unsourced line detected",
      P.unsourced_lines(["Push on cutover (email, Jul 7)", "Generic filler"])
      == ["Generic filler"])
check("all source families accepted",
      P.unsourced_lines([
          "a (email, Jul 7)", "b (meeting, Jun 30)", "c (commitment, May 22)",
          "d (decision log)", "e (transcript, Jul 2)", "f (calendar, Jul 8)",
          "g (sweep, Jul 7)", "h (session notes, Jun 12)"]) == [])
check("decorative parens do not pass as a source",
      P.unsourced_lines(["push hard (important!)"]) != [])

try:
    P.assemble_prep_sections(walk_out_with="", meeting_details="x")
    check("missing walk-out-with rejected", False, "no raise")
except P.PrepContractError as e:
    check("missing walk-out-with rejected", "walk_out_with" in str(e.violations))

try:
    P.assemble_prep_sections(walk_out_with="w", meeting_details="x",
                             talking_points=["no source here"])
    check("unsourced talking point rejected", False, "no raise")
except P.PrepContractError as e:
    check("unsourced talking point rejected",
          any("Talking Points" in v for v in e.violations))

out = P.assemble_prep_sections(
    walk_out_with="Q4 pricing confirmed",
    meeting_details="9:15 AM · Mira Chen · Peak Leaders",
    changed_lines=["Quinn sent two deliverables overnight (email, Jul 8)"],
    decide_lines=["Testimonial format: video vs written"],
    owed_table=tab,
    discuss_bullets=discuss,
    talking_points=["Soft-sell the video testimonial (sweep, Jul 7)"],
    questions=["Is the Jul 21 session still on? (calendar, Jul 8)"],
    tiles=tiles, timeline=tl,
    supporting_sections=[{"heading": "Where We Left Off",
                          "body": "Four sentences. Of real. Context here. Truly."}],
)
headings = [s["heading"] for s in out["sections"]]
check("verdict carries the walk-out prefix",
      out["exec_header"]["verdict"] == "Walk out with: Q4 pricing confirmed")
check("exec CHANGED lead comes from block 2",
      "overnight" in out["exec_header"]["changed"])
check("canonical order holds",
      headings == ["At a Glance", "Meeting Details", "Relationship Timeline",
                   "Where We Left Off", "Changed Since Last Touch",
                   "Decisions Needed", "Owed — Both Directions",
                   "Parked to Discuss", "Talking Points", "Questions to Ask"],
      str(headings))

empty = P.assemble_prep_sections(walk_out_with="w", meeting_details="x")
check("sparse workspace -> only Meeting Details + honest nothing-forms",
      [s["heading"] for s in empty["sections"]] == ["Meeting Details"]
      and empty["exec_header"]["changed"] == "Nothing new since last touch."
      and empty["exec_header"]["decide"] == "Nothing — execution call.")

# ---------------------------------------------------------------------------
print()
print(f"prep-pipeline battery: {PASS} passed, {FAIL} failed")
if FAILURES:
    print("FAILED:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
