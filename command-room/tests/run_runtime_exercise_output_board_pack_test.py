#!/usr/bin/env python3
"""SPEC A8 — board-pack output regression exercise (runtime tier).

The board pack is the purest substrate roll-up. This aggregates the fixture's
2026-05-01..2026-06-01 window into section_counts {wins, concerns, decisions_logged,
asks}, renders via make_brief(brief_kind="board_pack"), and appends board_pack_assembled
with section_counts asserted equal to the independently-computed fixture truth.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import output_exercise_lib as lib  # noqa: E402

NOW = "2026-06-01T12:00:00+00:00"
PERIOD_START = "2026-05-01T00:00:00+00:00"
PERIOD_END = "2026-06-01T00:00:00+00:00"


def _dt(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _in_period(ev):
    t = _dt(ev.get("ts"))
    return t is not None and _dt(PERIOD_START) <= t < _dt(PERIOD_END)


def main() -> int:
    from brief_writer import make_brief
    from cru_match import load_events_defensively, load_open_commitments, _commitment_field, _parse_ts
    from next_seq import next_seq
    from atomic_write import atomic_append_jsonl

    ok, fail, section, finish = lib.make_recorder()
    ws = lib.copy_fixture()
    events_path = ws / "_hq" / "data" / "events.jsonl"
    events, _ = load_events_defensively(str(events_path))

    section("derivation — period aggregation (independent fixture truth)")
    decisions_logged = sum(1 for e in events if e.get("type") == "decision" and _in_period(e))
    wins = sum(1 for e in events if e.get("type") == "commitment_resolved" and _in_period(e))
    opens = load_open_commitments(str(events_path))
    now_dt = _dt(NOW)
    concerns = 0
    asks = 0
    for c in opens:
        due = _parse_ts(_commitment_field(c, "due"))
        if due is not None and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due is not None and due < now_dt:
            concerns += 1
        if _commitment_field(c, "owner_id") == "person_001":
            asks += 1
    counts = {"wins": wins, "concerns": concerns, "decisions_logged": decisions_logged, "asks": asks}
    if counts == {"wins": 2, "concerns": 3, "decisions_logged": 3, "asks": 2}:
        ok("section_counts computed from the period", json.dumps(counts))
    else:
        fail("section_counts computation", json.dumps(counts))

    section("render (real make_brief)")
    out = ws / "_hq" / "board-packs" / "BoardPack_2026-06-01.docx"
    out.parent.mkdir(parents=True, exist_ok=True)
    sections = [
        {"heading": "Executive summary",
         "body": "Pricing direction is set and the renewal motion is underway. Three "
                 "decisions logged this period; two commitments closed."},
        {"heading": "Decisions logged", "bullets": [
            "Move to annual-only billing — May 1",
            "Lock the pricing tiers before the renewal — May 12",
            "Sunset the legacy importer by Q4 — May 22"]},
        {"heading": "Wins", "bullets": ["Confirmed the vendor list.", "Prepped the rollout plan."]},
        {"heading": "Concerns", "bullets": [
            "Three open commitments are past due.",
            "Northstar is evaluating a competitor for the renewal."]},
        {"heading": "Asks", "bullets": ["Approve the pricing page copy.", "Confirm the Northstar pricing sheet."]},
    ]
    try:
        make_brief(str(out), brief_kind="board_pack",
                   title="Board pack — Acme Co", subtitle="Reporting period: May 2026",
                   sections=sections, contract="report")
        ok("make_brief rendered the board pack")
    except Exception as e:
        fail("make_brief rendered the board pack", f"{type(e).__name__}: {e}")
        return finish("board_pack_exercise")
    ok("board pack .docx > 5KB") if out.exists() and out.stat().st_size > 5000 else fail("board pack > 5KB")

    section("extracted structure + golden")
    text = lib.extract_docx_text(out)
    for h in ("BOARD PACK", "Executive summary", "Decisions logged", "Wins", "Concerns", "Asks"):
        ok(f"contains: {h}") if h in text else fail(f"contains: {h}")
    ph = lib.assert_no_placeholders(text)
    ok("no placeholders") if not ph else fail("no placeholders", str(ph))
    matched, diff = lib.compare_golden("board_pack", text)
    ok("extracted text matches golden") if matched else fail("golden match", diff[:600])

    section("substrate side-effect (board_pack_assembled)")
    seq = next_seq(str(events_path))
    ev = {"seq": seq, "ts": NOW, "type": "board_pack_assembled", "source_skill": "board-pack-assembler",
          "primary_thread_id": "project_001",
          "data": {"board_meeting_ts": NOW, "reporting_period_start_ts": PERIOD_START,
                   "reporting_period_end_ts": PERIOD_END,
                   "artifact_path": "_hq/board-packs/BoardPack_2026-06-01.docx",
                   "section_counts": counts}}
    atomic_append_jsonl(events_path, [ev])
    appended = json.loads(events_path.read_text(encoding="utf-8").splitlines()[-1])
    v = lib.validate_event(appended)
    ok("board_pack_assembled validates") if not v else fail("event valid", str(v))
    if appended["data"]["section_counts"] == counts:
        ok("event section_counts == independently-computed fixture truth")
    else:
        fail("event section_counts mismatch", json.dumps(appended["data"]["section_counts"]))

    return finish("board_pack_exercise")


if __name__ == "__main__":
    raise SystemExit(main())
