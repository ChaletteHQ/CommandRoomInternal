#!/usr/bin/env python3
"""WG1-B D-B4 — the deterministic scheduled-moves adapter
(`relationship_moves.moves_rows_from_candidates`) + the `build_card_view`
extra-sections `n`-assert.

Big-test row 10a: scheduled staff-meeting fires DROPPED the "This week's
moves" section because nothing converted bare compute_relationship_moves
candidates ({person_id, score, components}) into renderer-ready rows.

Covers:
  - bare candidates -> full n-bearing rows (wire id, resolved name,
    substrate-derived why-now tag, canonical connector-free verbs)
  - the rows render through build_staff_meeting_view + render_and_persist
    end to end without a raise (the exact scheduled-fire path)
  - zero candidates -> zero rows (the omit --moves-json branch is honest)
  - an unresolvable person_id is SKIPPED with a stderr note — a raw id
    never renders (D-B1's principle)
  - build_card_view fails LOUD (section named) on an extra-sections item
    missing `n`

Fixtures mirror real substrate shapes; dates relative to today (G14).
House convention: non-zero exit = fail.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import brain_proposals as bp  # noqa: E402
import relationship_moves as rm  # noqa: E402
from widget_transport import render_and_persist  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ws() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="mv_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    ent = {"version": 1, "people": [
        {"id": "person_101", "canonical_name": "Rio Placeholder"},
        {"id": "person_102", "canonical_name": "Dana Placeholder"},
    ], "orgs": [], "threads": []}
    (ws / "_hq" / "data" / "entities.json").write_text(
        json.dumps(ent), encoding="utf-8")
    # A real-shaped dormancy_signal for person_101 (gap/baseline days are what
    # the honest why-now line is derived from).
    sig = {"seq": 1, "ts": _iso(1), "type": "dormancy_signal",
           "source_skill": "pulse",
           "data": {"entity_id": "person_101", "entity_type": "person",
                    "score": 2.0, "gap_days": 56.0, "baseline_days": 14.0}}
    (ws / "_hq" / "data" / "events.jsonl").write_text(
        json.dumps(sig) + "\n", encoding="utf-8")
    return ws


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ws = _ws()
    candidates = [
        {"person_id": "person_101", "score": 0.5,
         "components": {"dormancy": 0.5, "thread_context": 0.0,
                        "commitment_overdue": 0.3}},
        {"person_id": "person_102", "score": 0.2,
         "components": {"dormancy": 0.4, "thread_context": 0.0,
                        "commitment_overdue": 0.0}},
    ]

    # ---- bare candidates -> full n-bearing rows -----------------------------
    rows = rm.moves_rows_from_candidates(candidates, ws)
    check("one row per resolvable candidate", len(rows) == 2, f"{rows}")
    r = rows[0]
    check("wire id is move:<person_id>", r["n"] == "move:person_101")
    check("name is the RESOLVED canonical name, never an id",
          r["name"] == "Rio Placeholder")
    check("row embeds person_id verbatim",
          r["data"]["person_id"] == "person_101")
    check("row data names its family",
          r["data"]["kind"] == "relationship_move")
    check("verbs are the canonical connector-free set",
          r["actions"] == ["nudge", "snooze 3d", "not relevant"])
    check("why-now tag carries the signal's own gap days",
          "56d since last touch" in r["context_tag"], r["context_tag"])
    check("why-now tag carries the signal's own cadence",
          "14d cadence" in r["context_tag"], r["context_tag"])
    check("overdue component named when in play",
          "overdue commitment" in r["context_tag"], r["context_tag"])
    check("no-signal candidate degrades honestly (no fabricated days)",
          not any(ch.isdigit() for ch in rows[1]["context_tag"]),
          rows[1]["context_tag"])
    # A raw person id must never appear in any renderable text field.
    for row in rows:
        check(f"no raw id in visible text ({row['n']})",
              "person_10" not in row["name"]
              and "person_10" not in row["context_tag"])

    # ---- unresolvable person_id: skipped loudly, never rendered ------------
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rows_bad = rm.moves_rows_from_candidates(
            [{"person_id": "person_999", "score": 0.9,
              "components": {"dormancy": 0.9}}], ws)
    check("unresolvable person_id produces NO row", rows_bad == [])
    check("the skip is loud (stderr names the adapter)",
          "moves adapter" in err.getvalue(), err.getvalue())

    # ---- zero candidates -> zero rows (omit-when-zero stays honest) --------
    check("zero candidates -> zero rows",
          rm.moves_rows_from_candidates([], ws) == [])

    # ---- end to end: the scheduled-fire path renders without a raise -------
    import surface_drivers as sd
    view = sd.build_staff_meeting_view(ws, moves_rows=rows)
    move_secs = [s for s in view["sections"]
                 if s.get("title") == "THIS WEEK'S MOVES"]
    check("moves section present with the adapter's rows",
          len(move_secs) == 1 and len(move_secs[0]["items"]) == 2)
    t = render_and_persist(data_view=view, wrapper="fragment",
                           persist_dir=tempfile.mkdtemp(),
                           name_hint="staff-meeting", page=1, page_size=10)
    check("scheduled-path view renders + validates end to end",
          "THIS WEEK" in t["html"] and "S MOVES" in t["html"])
    check("resolved name renders on the page", "Rio Placeholder" in t["html"])
    check("raw person id never reaches visible page text",
          "person_101" not in
          __import__("re").sub(r'(data-[a-z-]+="[^"]*")|(href="[^"]*")', "",
                               t["html"]))

    # ---- zero moves: section honestly absent --------------------------------
    view0 = sd.build_staff_meeting_view(ws, moves_rows=None)
    check("no moves -> no moves section",
          not any(s.get("title") == "THIS WEEK'S MOVES"
                  for s in view0["sections"]))

    # ---- build_card_view n-assert: fail loud, section named ----------------
    try:
        bp.build_card_view([], extra_sections=[
            {"title": "THIS WEEK'S MOVES",
             "items": [{"name": "Rio Placeholder", "actions": ["nudge"]}]}])
        check("missing n raises at build-view time", False)
    except ValueError as exc:
        check("missing n raises at build-view time", True)
        check("the raise names the section",
              "THIS WEEK'S MOVES" in str(exc), str(exc))
    check("well-formed extra rows still pass the assert",
          bp.build_card_view([], extra_sections=[
              {"title": "THIS WEEK'S MOVES", "items": list(rows)}])
          ["sections"][0]["items"][0]["n"] == "move:person_101")

    print()
    if failures:
        print(f"{len(failures)} FAILED of {checks}")
        return 1
    print(f"ALL moves-adapter tests PASSED ({checks} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
