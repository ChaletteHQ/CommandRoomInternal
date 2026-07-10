#!/usr/bin/env python3
"""SPEC A8 — call-prep output regression exercise (runtime tier).

Simulates "prep me for the call with Rio" mechanically: resolve the attendee, pull
their open commitments via the real readers, build the inverted call-prep sections,
render via make_brief(brief_kind="call_prep") at the canonical get_brief_path. call-prep
writes NO skill-specific event (passive-capture only), so this asserts render structure +
golden, not an event side-effect.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import output_exercise_lib as lib  # noqa: E402

DATE = "2026-06-01"


def main() -> int:
    from brief_writer import make_brief
    from brief_path import get_brief_path
    from cru_match import load_open_commitments, event_references_person

    ok, fail, section, finish = lib.make_recorder()
    ws = lib.copy_fixture()
    events_path = ws / "_hq" / "data" / "events.jsonl"

    section("derivation — Rio's open commitments")
    opens = load_open_commitments(str(events_path))
    rio_opens = [c for c in opens if event_references_person(c, "person_002")]
    titles = [c.get("data", {}).get("title") for c in rio_opens]
    # Rio (person_002) owns c1 "Draft the sourcing spec" (open) — assert it surfaces.
    if any("sourcing spec" in (t or "").lower() for t in titles):
        ok("Rio's open commitment surfaced via event_references_person", str(titles))
    else:
        fail("Rio's open commitment surfaced", str(titles))

    section("render (real make_brief, canonical path)")
    brief_path = get_brief_path(str(ws), "call_prep", "rio-sample", DATE)
    Path(brief_path).parent.mkdir(parents=True, exist_ok=True)
    sections = [
        {"heading": "Suggested outcome",
         "body": "Confirm the sourcing spec lands this week and align on the pricing sheet for Northstar."},
        {"heading": "Open with them",
         "bullets": ["Ask where the sourcing spec stands — it was due in April.",
                     "Surface the Northstar pricing-sheet dependency."]},
        {"heading": "Open items & blockers",
         "bullets": [f"{(c.get('data', {}).get('title') or 'item')} — owner Rio Sample"
                     for c in rio_opens] or ["Nothing open with Rio right now."]},
        {"heading": "Decisions already on the record",
         "bullets": ["Set introductory pricing at $40/seat — Apr 16",
                     "Move pricing to annual-only billing — May 1"]},
    ]
    try:
        make_brief(brief_path, brief_kind="call_prep",
                   title="Call prep — Rio Sample",
                   subtitle=f"1:1 · {DATE} · Acme Co",
                   exec_header={"verdict": "Walk out with the renewal date locked."},  # OUT2 §4 flip
                   sections=sections, contract="report")
        ok("make_brief rendered the call-prep brief")
    except Exception as e:
        fail("make_brief rendered the call-prep brief", f"{type(e).__name__}: {e}")
        return finish("call_prep_exercise")

    p = Path(brief_path)
    if p.name.startswith("Call_Prep_") and "_hq" in str(p) and "meetings" in str(p):
        ok("canonical _hq/meetings/Call_Prep_ path", p.name)
    else:
        fail("canonical path", str(p))
    if p.exists() and p.stat().st_size > 5000:
        ok("call-prep .docx exists > 5KB", f"{p.stat().st_size}B")
    else:
        fail("call-prep .docx exists > 5KB")

    section("extracted structure + golden")
    text = lib.extract_docx_text(p)
    for h in ("CALL PREP", "Suggested outcome", "Open with them", "Open items & blockers"):
        ok(f"contains: {h}") if h in text else fail(f"contains: {h}")
    if "sourcing spec" in text.lower():
        ok("Rio's open commitment title appears in the brief")
    else:
        fail("commitment title in brief")
    ph = lib.assert_no_placeholders(text)
    ok("no placeholders") if not ph else fail("no placeholders", str(ph))
    matched, diff = lib.compare_golden("call_prep", text)
    ok("extracted text matches golden") if matched else fail("golden match", diff[:600])

    return finish("call_prep_exercise")


if __name__ == "__main__":
    raise SystemExit(main())
