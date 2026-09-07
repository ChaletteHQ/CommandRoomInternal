#!/usr/bin/env python3
"""stamp_light_preset — the QUIET1 D1 one-shot (M ruling 7, 2026-09-03):
every client workspace starts `light`, EXISTING ones included.

WHY
---
`commitment-policy.preset` shipped in v5.28.0 with a reader and no writer
but onboarding (which existing workspaces never re-run), so every seat
already on the product would have stayed `engaged` — fifteen questions a
week and no plate cap — while new seats started `light`. M ruled the other
way: existing clients start `light` too. This action is how that ruling
reaches a workspace that is already running.

WHAT IT DOES
------------
If NO valid preset is stored, writes `light` through `quiet.stamp_preset`
(the typed `skill_config_writer.save_skill_config` writer), stamped as ONE
`brain_batch` (`qpr_…`, class `commitment_preset`) so a bare `undo` lists
it and `brain_undo.REVERSERS["commitment_preset"]` clears the key again —
the workspace goes back to exactly what it had (nothing stored). Idempotent:
a workspace with ANY valid stored preset is a no-op (`ran=False`, no write,
no receipt) — which is also how the operator's own workspace keeps
`engaged`: it is stamped EXPLICITLY beforehand (`python quiet.py <ws>
--stamp engaged --apply`), never detected by hostname or any machine fact.
If the bridge ran first anyway, `undo` restores and the stamp follows.

Ships as a manifest ACTION (`action: auto_apply`, detector
`release_detectors.always.always_applies`); the coordinator writes the
manifest item at the cut — this module carries no manifest. The
`notice_template` may promise `undo` because the reverser exists (same
commit).

Signature per references/RELEASE_MANIFEST.md "Action contract":
    fn(events_jsonl_path, workspace_root, detector_context) -> dict
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import quiet  # noqa: E402
from commitment_policy import PRESET_CONFIG_KEY, PRESET_SKILL_KEY, PRESETS  # noqa: E402

SOURCE_SKILL = "command-room-update-bridge"
TRIGGERED_BY = "release_action:stamp_light_preset"
ORIGIN = "release_default"
PRESET = quiet.CLIENT_DEFAULT_PRESET


def plan(workspace_root) -> dict:
    """Pure read: {status: "already_set" | "would_write" | "not_a_workspace",
    stored_preset, preset}."""
    ws = Path(workspace_root)
    if not (ws / "_hq" / "data").is_dir():
        return {"status": "not_a_workspace", "stored_preset": None, "preset": PRESET}
    try:
        from skill_config_writer import load_skill_config
        stored = load_skill_config(ws, PRESET_SKILL_KEY) or {}
        cfg = stored.get("config") if isinstance(stored, dict) else None
        val = (cfg or {}).get(PRESET_CONFIG_KEY) if isinstance(cfg, dict) else None
        val = str(val or "").strip().lower()
    except Exception:
        val = ""
    if val in PRESETS:
        return {"status": "already_set", "stored_preset": val, "preset": PRESET}
    return {"status": "would_write", "stored_preset": val or None, "preset": PRESET}


def stamp_light_preset(events_jsonl_path, workspace_root, detector_context) -> dict:
    """The manifest action. Idempotent; additive (one config file + one
    batch-stamped config event); reversible by name (`commitment_preset`)."""
    ws = Path(workspace_root)
    p = plan(ws)
    ctx = {"preset": PRESET, "stored_preset": p.get("stored_preset"),
           "preset_status": p["status"]}
    if p["status"] != "would_write":
        return {"success": True, "ran": False, "context": ctx}
    try:
        res = quiet.stamp_preset(ws, PRESET, origin=ORIGIN, triggered_by=TRIGGERED_BY)
    except Exception as e:
        return {"success": False, "ran": False, "context": ctx, "error": str(e)}
    ctx["batch_id"] = res.get("batch_id")
    ctx["budget_per_week"] = quiet.QUESTION_BUDGET[PRESET]
    return {"success": True, "ran": bool(res.get("ran")), "context": ctx}


def main(argv=None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="write the preset (default: plan only)")
    args = ap.parse_args(argv)
    ws = Path(args.workspace)
    if not args.apply:
        print(json.dumps(plan(ws)))
        return 0
    out = stamp_light_preset(ws / "_hq" / "data" / "events.jsonl", ws, {})
    print(json.dumps(out))
    return 0 if out.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
