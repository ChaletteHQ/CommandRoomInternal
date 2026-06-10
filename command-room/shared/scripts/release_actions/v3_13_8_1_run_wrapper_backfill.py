"""v3.13.8.1 wrapper-backfill auto-apply action.

Wraps `source_event_seq_backfill.run_backfill_if_needed`. Converts the previously
instruct_user manifest item ("type `run wrapper backfill`") into a silent
auto-apply.

Safety: run_backfill_if_needed is idempotent (short-circuits on already_run via
_already_ran check) and conservative — only high-confidence matches get linked;
ambiguous wrappers stay flagged as needs_review (no false links). No data loss.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def auto_run_wrapper_backfill(
    events_jsonl_path,
    workspace_root,
    detector_context: dict,
) -> dict:
    """Auto-apply: link legacy wrappers to their source events where confident."""
    try:
        from source_event_seq_backfill import run_backfill_if_needed
    except ImportError as e:
        return {
            "success": False,
            "ran": False,
            "context": {},
            "error": f"source_event_seq_backfill import failed: {e}",
            "fallback_prompt": None,
        }

    try:
        summary = run_backfill_if_needed(workspace_root=Path(workspace_root))
    except Exception as e:
        return {
            "success": False,
            "ran": False,
            "context": {},
            "error": f"run_backfill_if_needed raised: {e}",
            "fallback_prompt": None,
        }

    if not summary.get("ran"):
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
            "wrappers_examined": summary["wrappers_examined"],
            "wrappers_linked": summary["wrappers_linked"],
            "wrappers_marked_needs_review": summary["wrappers_marked_needs_review"],
        },
        "error": None,
        "fallback_prompt": None,
    }


__all__ = ["auto_run_wrapper_backfill"]
