#!/usr/bin/env python3
"""write_user_pointer — the D8 one-shot (SPEC PLATE1, 2026-09-03): write the
primary-user pointer the workspace never had a writer for.

WHY
---
`primary_user.resolve_primary_user` climbs a ladder — the explicit pointer
(`workspace.user_id`, canonical; the two legacy spellings in
`primary_user.POINTER_KEYS` tolerated), then the primary-user flag, then a
first-name match. Nothing in the product ever WROTE rung one (intake
BUG_2026-08-18_no-writer-for-user-pointer), so every workspace resolved by
inference on every read, and any workspace where the inference failed
rendered "you owe 0 / owed to you N" without saying so. PLATE1 makes the
counting API REFUSE without a user; this action makes sure the pointer is
there before that refusal can bite a customer who updates.

WHAT IT DOES
------------
Resolves the user through the SAME ladder (never a second one), and if the
resolution came from a lower rung, writes it to rung one through
`workspace_settings.set_workspace_settings` — the typed writer that also
emits the `workspace_setting_changed` receipt. Idempotent: a workspace whose
pointer already resolves to the same person is a no-op (`ran=False`, no
write, no receipt); an unresolvable workspace is a no-op that says so in
its context (the bridge surfaces nothing — the plate's own refusal line does
the asking).

THE KEY. The spec text names the LEGACY pointer spelling; the resolver and
`entities.schema.json` designate `user_id` as the canonical one and treat
the older spelling as deprecated (USERKEY1/2). Writing a legacy key when the
canonical exists would be a one-vocabulary violation, so this action writes
`user_id` (`primary_user.CANONICAL_POINTER_KEY`, never spelled here). Recorded as a §2 drift in the BUILD record;
`POINTER_KEY` is the one place to flip if M rules otherwise.

Ships as a manifest ACTION (`action: auto_apply`, detector
`release_detectors.always.always_applies`) — the coordinator writes the
v5.27.0 manifest item; this module carries no manifest.

Signature per references/RELEASE_MANIFEST.md "Action contract":
    fn(events_jsonl_path, workspace_root, detector_context) -> dict
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from primary_user import (  # noqa: E402
    CANONICAL_POINTER_KEY, POINTER_KEYS, _workspace_settings,
    resolve_primary_user_from_entities,
)

# The key this action writes. Canonical per primary_user / the entities
# schema. See the module docstring before changing it.
POINTER_KEY = CANONICAL_POINTER_KEY
SOURCE_SKILL = "command-room-update-bridge"
TRIGGERED_BY = "release_action:write_user_pointer"


def _load_entities(workspace_root: Path) -> dict | None:
    import json
    p = workspace_root / "_hq" / "data" / "entities.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def plan(workspace_root) -> dict:
    """Pure read: what the action WOULD do.

    Returns {status: "already_set" | "would_write" | "unresolvable" |
             "no_entities", person_id, current_pointer, key}."""
    ws = Path(workspace_root)
    ent = _load_entities(ws)
    if ent is None:
        return {"status": "no_entities", "person_id": None,
                "current_pointer": None, "key": POINTER_KEY}
    settings = _workspace_settings(ent)
    current = None
    for key in POINTER_KEYS:
        if settings.get(key):
            current = settings.get(key)
            break
    resolved = resolve_primary_user_from_entities(ent)
    if not resolved:
        return {"status": "unresolvable", "person_id": None,
                "current_pointer": current, "key": POINTER_KEY}
    if settings.get(POINTER_KEY) == resolved:
        return {"status": "already_set", "person_id": resolved,
                "current_pointer": current, "key": POINTER_KEY}
    return {"status": "would_write", "person_id": resolved,
            "current_pointer": current, "key": POINTER_KEY}


def write_user_pointer(events_jsonl_path, workspace_root, detector_context) -> dict:
    """The manifest action. Idempotent; additive (one settings key + one
    receipt event); reversible (the receipt carries old_value)."""
    ws = Path(workspace_root)
    try:
        p = plan(ws)
    except Exception as e:  # pragma: no cover — a broken entities file
        return {"success": False, "ran": False, "context": {}, "error": str(e)}
    ctx = {"person_id": p.get("person_id"), "pointer_key": POINTER_KEY,
           "pointer_status": p["status"]}
    if p["status"] != "would_write":
        return {"success": True, "ran": False, "context": ctx}
    try:
        from workspace_settings import set_workspace_settings
        res = set_workspace_settings(
            ws, {POINTER_KEY: p["person_id"]},
            source_skill=SOURCE_SKILL, triggered_by=TRIGGERED_BY)
    except Exception as e:
        return {"success": False, "ran": False, "context": ctx, "error": str(e)}
    ctx["events_emitted"] = int(res.get("events_emitted") or 0)
    ctx["changed"] = sorted((res.get("changed") or {}).keys())
    return {"success": True, "ran": POINTER_KEY in (res.get("changed") or {}),
            "context": ctx}


def main(argv=None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="write the pointer (default: plan only)")
    args = ap.parse_args(argv)
    ws = Path(args.workspace)
    if not args.apply:
        print(json.dumps(plan(ws)))
        return 0
    out = write_user_pointer(ws / "_hq" / "data" / "events.jsonl", ws, {})
    print(json.dumps(out))
    return 0 if out.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
