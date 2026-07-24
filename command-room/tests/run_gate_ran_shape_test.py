#!/usr/bin/env python3
"""FB-plumbing item 1 — the UNIFIED gate_ran receipt shape.

Two gate_ran emitters had drifted apart: the deliverable_sweep shape carried a
`result` (pass/fail) but no artifact name, and the brief_writer shape carried an
`artifact` basename but no result. A reader joining gate_ran events couldn't
count on either field. This pins the merged contract: EVERY gate_ran receipt
carries BOTH

  - `result`  — "pass" | "fail"
  - `artifact` — a list of file basenames (empty list for a surface that saves
                 no file, e.g. the chat-email backstop)

across all three emitters (brief_writer / deliverable_sweep / turn_backstop), so
a future emitter-agnostic reader can rely on the shape without special-casing.

G14: no fixture timestamps here (the emitters self-stamp). Placeholder names
only. House convention: non-zero exit = fail.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

# G14 — compute the fixture date at runtime (never a hardcoded ISO literal, even
# in an artifact filename): the deliverable names are date-stamped, but the date
# is only cosmetic here, so any stable value works.
_DATE = dt.date.today().isoformat()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import brief_gates  # noqa: E402
import deliverable_sweep  # noqa: E402
import turn_backstop  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))
    else:
        print(f"  ok   {label}")


def _ws() -> Path:
    d = Path(tempfile.mkdtemp(prefix="gate_ran_shape_"))
    (d / "_hq" / "data").mkdir(parents=True)
    (d / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _gate_events(ws: Path) -> list[dict]:
    out = []
    for line in (ws / "_hq" / "data" / "events.jsonl").read_text(
        encoding="utf-8"
    ).splitlines():
        line = line.strip()
        if not line:
            continue
        ev = json.loads(line)
        if ev.get("type") == "gate_ran":
            out.append(ev)
    return out


def _assert_unified(label: str, data: dict) -> None:
    """The shared invariant every emitter must satisfy."""
    check(f"{label}: carries a result", data.get("result") in ("pass", "fail"),
          repr(data.get("result")))
    check(f"{label}: carries an artifact LIST",
          isinstance(data.get("artifact"), list), repr(data.get("artifact")))
    check(f"{label}: still carries surface + gates",
          isinstance(data.get("surface"), str)
          and isinstance(data.get("gates"), list), repr(data))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # --- brief_writer emitter (was: artifact, no result) --------------------
    ws = _ws()
    board_pack = f"Acme_Board_Pack_{_DATE}.docx"
    brief_gates.emit_gate_ran_audit(
        "board_pack", ["contract", "voice", "leak"],
        f"/x/y/{board_pack}", str(ws), surface="docx")
    evs = _gate_events(ws)
    check("brief_writer emitted exactly one gate_ran", len(evs) == 1, repr(evs))
    d = evs[-1]["data"]
    _assert_unified("brief_writer", d)
    check("brief_writer result is pass (reached emit = gates ran + saved)",
          d.get("result") == "pass", repr(d))
    check("brief_writer artifact names the saved file",
          d.get("artifact") == [board_pack], repr(d))

    # --- deliverable_sweep emitter (was: result, no artifact) ---------------
    ws2 = _ws()
    one_pager = f"Client_One_Pager_{_DATE}.docx"
    memo = f"Client_Memo_{_DATE}.docx"
    sweep_result = {
        "scanned": 2,
        "flagged": [
            {"path": f"/abs/{one_pager}",
             "has_violation": True, "leaks": [{"match": "synergy"}]},
            {"path": f"/abs/{memo}", "has_violation": False},
        ],
        "violation_count": 1, "warn_count": 0, "error_count": 0,
    }
    deliverable_sweep._emit_sweep_event(ws2, sweep_result, source="cleanup_sweep")
    d2 = _gate_events(ws2)[-1]["data"]
    _assert_unified("deliverable_sweep", d2)
    check("deliverable_sweep result reflects the violation (fail)",
          d2.get("result") == "fail", repr(d2))
    check("deliverable_sweep artifact lists the flagged basenames",
          d2.get("artifact") == [one_pager, memo], repr(d2))

    # --- turn_backstop emitter (chat surface — no file artifact) ------------
    ws3 = _ws()
    turn_backstop._emit_gate_ran_chat_email(str(ws3), fail_count=0, item_count=3)
    d3 = _gate_events(ws3)[-1]["data"]
    _assert_unified("turn_backstop", d3)
    check("turn_backstop artifact is an empty list (chat saves no file)",
          d3.get("artifact") == [], repr(d3))

    print()
    if failures:
        print(f"FAIL — {len(failures)}/{checks} gate_ran shape checks failed")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print(f"OK — all {checks} gate_ran shape checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
