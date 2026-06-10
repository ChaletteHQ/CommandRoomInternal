"""v3.18.9 brains_stale_logic detector — surfaced 2026-06-01 (Bugs #87/#97).

Returns applies=True when any ACTIVE project's brain carries a generated
Live-State (People) block built under an OLDER render-logic version than the
current `render_thread_live_state.LIVE_STATE_LOGIC_VERSION`.

WHY THIS EXISTS
The v3.18.9 #87 re-fix changed which people the People block proposes (org-less
vendor/demo contacts are now dropped), and #97 added a `logic_v` stamp to the
block marker so a logic change can reach a QUIET brain whose events haven't moved.
But the propagation only happens when something re-renders the brain — the Sunday
`cleanup` does it weekly. This detector lets the UPDATE path push the same refresh
immediately, so a customer who updates sees their project people-lists corrected
on update instead of waiting for the next cleanup fire.

Stale = a brain that HAS a Live-State block whose recorded `logic_v` differs from
the current version (None for pre-stamp blocks). A brain with no block at all is
NOT counted here — that's an un-migrated brain handled by the go/cleanup render
path, not a logic-version refresh.

Wiring (v3.18.10): consumed by command-room-update-bridge Phase 4.8 as the
detector for the `v3_18_9_rerender_brains` auto_apply action. Idempotent — once
the action re-renders, every block stamps the current `logic_v` and the detector
returns applies=False.
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


def brains_stale_logic(events_jsonl_path) -> dict:
    """Detector for stale-render-logic brains (Bugs #87/#97).

    Returns {"applies": bool, "context": {"n_stale": int}}.
    """
    events_path = Path(events_jsonl_path)
    # workspace_root = .../_hq/data/events.jsonl -> up 3
    workspace_root = events_path.parent.parent.parent
    entities_path = workspace_root / "_hq" / "data" / "entities.json"
    if not entities_path.exists():
        return {"applies": False, "context": {"n_stale": 0}}

    try:
        import json
        import render_thread_live_state as rtls
        import render_brain_block
    except Exception:
        return {"applies": False, "context": {"n_stale": 0}}

    try:
        raw = json.loads(entities_path.read_text(encoding="utf-8"))
    except Exception:
        return {"applies": False, "context": {"n_stale": 0}}
    ent = raw["entities"] if isinstance(raw.get("entities"), dict) else raw

    current_v = getattr(rtls, "LIVE_STATE_LOGIC_VERSION", None)
    if current_v is None:
        return {"applies": False, "context": {"n_stale": 0}}

    n_stale = 0
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
        meta = render_brain_block.read_block_meta(brain, rtls.BLOCK_ID)
        if meta is None:
            continue  # no generated block yet — not a logic-version refresh case
        if meta.get("logic_v") != current_v:
            n_stale += 1

    return {"applies": n_stale > 0, "context": {"n_stale": n_stale}}


__all__ = ["brains_stale_logic"]


if __name__ == "__main__":
    import json
    p = sys.argv[1] if len(sys.argv) > 1 else "."
    print(json.dumps(brains_stale_logic(p)))
