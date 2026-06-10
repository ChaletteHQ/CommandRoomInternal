"""v3.18.9 rerender_brains action (auto_apply) — surfaced 2026-06-01 (Bugs #87/#97).

Re-renders every ACTIVE project's Live-State (People) block under the current
render logic, so a customer who UPDATES immediately gets the v3.18.9 corrections
(the #87 umbrella-bleed fix drops org-less vendor/demo contacts from the proposed
list) instead of waiting for the next Sunday `cleanup` fire to refresh them.

It defers entirely to `render_thread_live_state.render_live_state`, whose #97
dirty-check re-renders a block ONLY when a newer event exists OR the block's
recorded `logic_v` differs from the current `LIVE_STATE_LOGIC_VERSION`. So:
  - stale-logic blocks re-render (picking up the new proposed-set logic + stamp);
  - already-current blocks are a cheap no-op ("unchanged");
  - the action is idempotent — a second run re-renders nothing, returns ran=False.

Safety (auto_apply contract): additive + reversible + no-data-loss. render_block
rewrites ONLY the marked Live-State region and preserves every byte of durable,
hand-owned content outside the markers. No deletes, no overwrite-without-marker.

Signature per references/RELEASE_MANIFEST.md "Action contract":
    fn(events_jsonl_path, workspace_root, detector_context) -> dict
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _active_threads(ent: dict) -> list[dict]:
    threads = ent.get("threads") or ent.get("projects") or []
    return [t for t in threads if t.get("status") != "archived"]


def rerender_brains(events_jsonl_path, workspace_root, detector_context) -> dict:
    """Re-render active brains' Live-State blocks under the current logic version."""
    try:
        import json
        import render_thread_live_state as rtls
    except Exception as e:
        return {"success": False, "ran": False, "context": {}, "error": str(e)}

    workspace_root = Path(workspace_root)
    entities_path = workspace_root / "_hq" / "data" / "entities.json"
    if not entities_path.exists():
        return {"success": True, "ran": False, "context": {"n_rerendered": 0}}

    try:
        raw = json.loads(entities_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"success": False, "ran": False, "context": {}, "error": str(e)}
    ent = raw["entities"] if isinstance(raw.get("entities"), dict) else raw

    rerendered = 0
    examined = 0
    for t in _active_threads(ent):
        tid = t.get("id")
        if not tid:
            continue
        try:
            brain = rtls.default_brain_path(workspace_root, tid)
        except Exception:
            brain = None
        if not brain or not Path(brain).exists():
            continue
        examined += 1
        try:
            # Non-force: the #97 dirty-check decides. A stale-logic or newer-event
            # block re-renders; a current one returns status "unchanged".
            r = rtls.render_live_state(workspace_root, tid)
        except Exception:
            continue
        if r.get("rendered"):
            rerendered += 1

    return {
        "success": True,
        "ran": rerendered > 0,
        "context": {"n_rerendered": rerendered, "n_examined": examined},
    }


__all__ = ["rerender_brains"]


if __name__ == "__main__":
    import json
    ws = sys.argv[1] if len(sys.argv) > 1 else "."
    ev = str(Path(ws) / "_hq" / "data" / "events.jsonl")
    print(json.dumps(rerender_brains(ev, ws, {})))
