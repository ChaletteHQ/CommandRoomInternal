"""v3.13.8 substrate-corruption auto-apply action.

Wraps `recover_corruption.run_recovery_if_needed`. Converts the previously
instruct_user manifest item ("type `run recovery`") into a silent auto-apply
so non-technical customers don't have to type a phrase to make their substrate
sane again.

Safety: recover_corruption.run_recovery_if_needed is idempotent (short-circuits
on already_run via _already_ran check) and preserves original data in
events_quarantine_*.jsonl sidecar. No data loss.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def auto_quarantine_malformed(
    events_jsonl_path,
    workspace_root,
    detector_context: dict,
) -> dict:
    """Auto-apply: quarantine malformed events.jsonl lines."""
    try:
        from recover_corruption import run_recovery_if_needed
    except ImportError as e:
        return {
            "success": False,
            "ran": False,
            "context": {},
            "error": f"recover_corruption import failed: {e}",
            "fallback_prompt": None,
        }

    try:
        summary = run_recovery_if_needed(
            workspace_root=Path(workspace_root),
            source_skill="update-bridge",
        )
    except Exception as e:
        return {
            "success": False,
            "ran": False,
            "context": {},
            "error": f"run_recovery_if_needed raised: {e}",
            "fallback_prompt": None,
        }

    if not summary.get("ran"):
        # Already-recovered or nothing-to-recover. No surface.
        return {
            "success": True,
            "ran": False,
            "context": {},
            "error": None,
            "fallback_prompt": None,
        }

    return {
        "success": True,
        "ran": True,
        "context": {
            "quarantined_count": summary["quarantined_count"],
            "date_range_label": summary["date_range_label"] or "recent",
        },
        "error": None,
        "fallback_prompt": None,
    }


__all__ = ["auto_quarantine_malformed"]
