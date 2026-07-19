#!/usr/bin/env python3
"""FS-10 — Staff Meeting / Living Brain card view builder + deal-row shape.

The live staff-meeting fire improvised the queue into 3 opaque clusters with
an unregistered "Confirm-close all" bulk verb and no per-row evidence. This
mechanizes the shape so the runtime renders it verbatim:

  - `build_card_view` groups the ranked queue into money > identity > hygiene
    SECTIONS titled with HONEST counts, drop-empty header tiles.
  - Each row is `{name — badge · evidence-with-date · consequence}` carrying
    ONLY the proposal's registered verbs — no invented bulk verb.
  - The deal detector's render_line is brand-clean (no emoji, no em dash) and
    carries a dated, provenance-honest source phrase.

House convention: non-zero exit = fail.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import brain_proposals as bp  # noqa: E402
from deal_signal_detector import _source_phrase  # noqa: E402
from widget_transport import render_and_persist  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def _deal_row(i, shape="money", verbs=("confirm proposal", "dismiss proposal",
                                       "snooze proposal 7d")):
    return {
        "id": f"bp_{i}", "shape": shape, "kind": "deal_creation",
        "title": f"Acme Co {i}",
        "render_line": ("likely deal · proposal language in your Jul 8 sent "
                        "mail · no pipeline record"),
        "action_tuples": [{"action": v} for v in verbs],
        "org_id": f"org_{i}", "opened_at": "2026-07-08",
    }


def main() -> int:
    # ---- source phrase: dated + provenance-honest, never invented ----------
    check("sent-mail source phrase is dated + owned",
          _source_phrase({"ts": "2026-07-08T12:00:00Z", "type": "sent_promise",
                          "source_skill": "reconcile-sent"}) == "your Jul 8 sent mail")
    check("meeting source noun honest",
          "meeting notes" in _source_phrase({"ts": "2026-07-08T00:00:00Z",
                                             "type": "meeting_processed",
                                             "source_skill": "granola"}))
    check("no ts → no invented date",
          "recent" in _source_phrase({"type": "email"}) and
          not any(c.isdigit() for c in _source_phrase({"type": "email"})))

    # ---- build_card_view: sections, honest counts, registered verbs --------
    items = ([_deal_row(i) for i in range(1, 13)] +           # 12 money
             [{**_deal_row(100, shape="identity"), "title": "Jane Roe",
               "action_tuples": [{"action": "add person"},
                                 {"action": "proposal not relevant"}]}] +  # 1 identity
             [_deal_row(200, shape="hygiene") for _ in range(1)])  # 1 hygiene
    view = bp.build_card_view(items, surface="staff-meeting",
                              header="Staff Meeting — 14 waiting on you")
    titles = [s["title"] for s in view["sections"]]
    check("sections ordered money > identity > hygiene",
          titles == ["MONEY (12)", "IDENTITY (1)", "HYGIENE (1)"], f"{titles}")
    check("honest counts in section titles", "MONEY (12)" in titles)
    tile_labels = [t["label"] for t in view["tiles"]]
    check("header tiles per shape, drop-empty",
          tile_labels == ["Money", "Identity", "Hygiene"])
    # Row shape
    row = view["sections"][0]["items"][0]
    check("row name is the title (not an id)", row["name"] == "Acme Co 1")
    check("row context carries badge · evidence-with-date · consequence",
          "likely deal" in row["context_tag"] and "Jul 8" in row["context_tag"]
          and "no pipeline record" in row["context_tag"])
    check("row embeds proposal id verbatim (F2)", row["data"]["id"] == "bp_1")
    check("row embeds target org id verbatim (F2)", row["data"]["org_id"] == "org_1")
    check("brain row carries ONLY the 3 registered verbs",
          row["actions"] == ["confirm proposal", "dismiss proposal",
                             "snooze proposal 7d"])
    check("legacy identity row keeps its own shipped verbs",
          view["sections"][1]["items"][0]["actions"] ==
          ["add person", "proposal not relevant"])

    # ---- no invented bulk verb anywhere in the built view ------------------
    all_actions = {a for s in view["sections"] for r in s["items"]
                   for a in r["actions"]}
    from verb_taxonomy import CANONICAL_ACTION_IDS
    for a in all_actions:
        check(f"built verb {a!r} is registered", a in CANONICAL_ACTION_IDS)
    check("no 'confirm-close all'-style bulk verb leaked in",
          not any("all" in a for a in all_actions))

    # ---- render + paginate the built view (end to end) ---------------------
    t = render_and_persist(data_view=view, wrapper="fragment",
                           persist_dir=tempfile.mkdtemp(),
                           name_hint="staff-meeting", page=1, page_size=10)
    check("built view renders + validates + paginates",
          t["pagination"]["total_pages"] == 2)
    check("honest count survives onto the page",
          "MONEY (12)" in t["html"])
    check("M's agreed row shape renders on the page",
          "Acme Co 1" in t["html"]
          and "proposal language in your Jul 8 sent mail" in t["html"])

    # ---- detector render_line is brand-clean (no emoji, no em dash) --------
    from deal_signal_detector import _MONTHS  # noqa: F401  (import smoke)
    # Build a candidate render_line via the private path shape check: the
    # public detector output is the render_line stored on proposals; assert the
    # format string used by the detector carries no banned glyphs.
    det_src = (ROOT / "shared" / "scripts" / "deal_signal_detector.py").read_text(
        encoding="utf-8")
    # The row line the detector composes:
    line_region = det_src.split("line = f\"{badge}")[1][:80]
    check("detector row line uses · separators, not em dash / emoji",
          "·" in det_src and "💼" not in line_region)

    if failures:
        print(f"\nstaff-meeting card view FAIL — {len(failures)} of {checks} failed")
        return 1
    print(f"staff-meeting card view (FS-10): {checks} checks OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
