#!/usr/bin/env python3
"""connector_jobs_draft_v1: draft a connector job block on each declared row
(cr1#1877 slice 0; spec §6).

WHAT IT DOES
------------
For each declared row of a job category (email, calendar, meetings, documents,
chat, personal_calendar) that carries no `job` block, writes one
`{state: draft, origin: migration}` block in the row's OWN category (a row is
never moved), through `connector_config.draft_from_seat`. The page's
`config/live_sources` states, passed in under `live_sources` exactly as
`live_sources_check` takes them, become the job's approval, stamped with the
Hub address under `hub_address` (an empty one never matches a page).

A migrated draft keeps working and never stops by itself (owner ruling 151a):
the routing reader answers it set, and its account, role and identity checks
only report. A row with no block reads the same way, so a seat that has not
run this yet, and every client seat until its own setup call (151c), behaves
exactly as before.

IDEMPOTENT: a row that already has a block is left alone, so a second run
writes nothing. `apply_once: false` in the bridge's list costs nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

MIGRATION_ID = "connector_jobs_draft_v1"


def connector_jobs_draft_v1(events_jsonl_path, workspace_root,
                            detector_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The action: draft the job blocks once, or do nothing and say why."""
    ctx = dict(detector_context or {})
    try:
        from receipts import require_writer_identity

        require_writer_identity(workspace_root=workspace_root)
    except ImportError:
        pass
    import json
    import connector_config as cc

    book = Path(str(workspace_root)) / "_hq" / "data" / "entities.json"
    try:
        if book.exists():
            json.loads(book.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - an unreadable book is no answer
        return {"success": False, "ran": False, "context": {"migration_id": MIGRATION_ID},
                "error": "entities_unreadable", "fallback_prompt": None}
    try:
        out = cc.draft_from_seat(workspace_root, live_sources=ctx.get("live_sources"),
                                 hub_address=ctx.get("hub_address"))
    except Exception as exc:  # noqa: BLE001 - a failed draft leaves the seat as it was
        return {"success": False, "ran": False, "context": {"migration_id": MIGRATION_ID},
                "error": type(exc).__name__, "fallback_prompt": None}
    drafted = out.get("drafted") or []
    line = ("Your connectors are carried over as drafts; confirm them on the Hub when you can."
            if drafted else "")
    return {"success": True, "ran": bool(drafted),
            "context": {"migration_id": MIGRATION_ID, "drafted": drafted, "kept": out.get("kept") or [],
                        "summary_line": line},
            "error": None, "fallback_prompt": None}
