#!/usr/bin/env python3
"""CTS1FIX Step B — the "Add to My Plate" reroute (spec §6 items 4-5).

The Waiting On widget's old `add to my list` button only flagged an item for
later discussion (`commitment_to_discuss`) — it never created a task, so the
label over-promised. Ruled fix (D1/D2): a NEW `add to my plate` action mints a
real owner-me task via `commitment_state.create_personal_task`, which
surface_split's `personal` partition renders on My Plate. This suite pins:

1. The writer: one gated `commitment` event (kind=task, owner-me, open), the
   return carries status "created" (apply-audit OK vocabulary — FS-18a), and
   the audit builder derives outcome "ok" / n_errors 0 from it.
2. The partition: the written event classifies to the personal (My Plate)
   surface for the owning user.
3. The taxonomy + verb swap: `add to my plate` row exists with the exact
   display label; Waiting On driver clusters carry the new verb and NOT the
   old one (ruled D5: Waiting On only).
4. The renderer accepts the new verb (its registry derives from the taxonomy)
   and emits the "Add to My Plate" label.

House convention: non-zero exit = fail. Placeholder names only.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

import surface_drivers  # noqa: E402
import surface_split  # noqa: E402
import verb_taxonomy  # noqa: E402
from apply_audit import build_apply_choices_applied_event  # noqa: E402
from chat_output_renderer import (  # noqa: E402
    CanonicalActionError,
    render_chat_output_widget,
)
from commitment_state import create_personal_task  # noqa: E402

USER = "person:user"

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")
        if detail:
            print(f"      {detail}")


def main():
    print("=== CTS1FIX Step B — 'Add to My Plate' reroute ===\n")

    # ------------------------------------------------------------------
    print("[1] create_personal_task — gated write, status 'created', audit ok")
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        data_dir = ws / "_hq" / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "entities.json").write_text(json.dumps({
            "people": [{"id": USER, "name": "Sam Sample", "primary_user": True}],
        }), encoding="utf-8")

        ret = create_personal_task(
            ws,
            title="File the expense report",
            owner_id=USER,
            source_ref="email:msg-123",
            source_event_seq=7,
        )

        events_path = data_dir / "events.jsonl"
        lines = [ln for ln in
                 events_path.read_text(encoding="utf-8").splitlines() if ln]
        check("exactly one event appended", len(lines) == 1,
              f"got {len(lines)}")
        ev = json.loads(lines[0]) if lines else {}
        d = ev.get("data") or {}
        check("event type is commitment", ev.get("type") == "commitment",
              repr(ev.get("type")))
        check("data.kind == task", d.get("kind") == "task", repr(d.get("kind")))
        check("data.owner_id is the user", d.get("owner_id") == USER,
              repr(d.get("owner_id")))
        check("data.status == open", d.get("status") == "open",
              repr(d.get("status")))
        check("data.title carried", d.get("title") == "File the expense report",
              repr(d.get("title")))
        check("return ok is True", ret.get("ok") is True, repr(ret.get("ok")))
        check("return status == created", ret.get("status") == "created",
              repr(ret.get("status")))

        audit = build_apply_choices_applied_event(
            source="commitments",
            actions=[{"n": 1, "action": "add to my plate",
                      "handler_result": ret}],
        )
        rows = audit["data"]["actions"]
        check("audit outcome is ok", rows and rows[0]["outcome"] == "ok",
              repr(rows))
        check("audit n_errors == 0", audit["data"]["n_errors"] == 0,
              repr(audit["data"]["n_errors"]))

        # ------------------------------------------------------------------
        print("\n[2] surface_split — the written event lands on My Plate")
        # ------------------------------------------------------------------
        surface = surface_split.classify_surface(ev, USER)
        check("classifies to the personal (My Plate) surface",
              surface == surface_split.SURFACE_PERSONAL, repr(surface))

    # ------------------------------------------------------------------
    print("\n[3] taxonomy row + Waiting On verb swap (ruled D5)")
    # ------------------------------------------------------------------
    row = verb_taxonomy.taxonomy_row("add to my plate")
    check("taxonomy row exists", row is not None)
    if row is not None:
        check("display label exactly 'Add to My Plate'",
              row["verb"] == "Add to My Plate", repr(row["verb"]))
        check("event is commitment", row["event"] == "commitment",
              repr(row["event"]))
    check("'add to my list' absent from _DELEGATED_VERBS",
          "add to my list" not in surface_drivers._DELEGATED_VERBS,
          repr(surface_drivers._DELEGATED_VERBS))
    check("'add to my list' absent from _REVIEW_VERBS",
          "add to my list" not in surface_drivers._REVIEW_VERBS,
          repr(surface_drivers._REVIEW_VERBS))
    check("'add to my plate' present in _DELEGATED_VERBS",
          "add to my plate" in surface_drivers._DELEGATED_VERBS,
          repr(surface_drivers._DELEGATED_VERBS))
    check("'add to my plate' present in _REVIEW_VERBS",
          "add to my plate" in surface_drivers._REVIEW_VERBS,
          repr(surface_drivers._REVIEW_VERBS))

    # ------------------------------------------------------------------
    print("\n[4] renderer accepts the new verb, emits the display label")
    # ------------------------------------------------------------------
    view = {"surface": "commitments", "title": "t", "sections": [{"title": "S",
        "items": [{"n": "1", "name": "Row",
                   "actions": ["mark received", "add to my plate"]}]}]}
    try:
        html = render_chat_output_widget(view, wrapper="fragment")
    except CanonicalActionError as exc:
        html = None
        check("renderer accepts 'add to my plate'", False, str(exc))
    if html is not None:
        check("renderer accepts 'add to my plate'", True)
        check("emitted label reads 'Add to My Plate'",
              "Add to My Plate" in html)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
