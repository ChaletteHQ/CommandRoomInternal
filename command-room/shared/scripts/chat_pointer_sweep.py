#!/usr/bin/env python3
"""The chat pointer sweep (cr1#164): every saved chat-evidenced close still
carries its pointer back to the chat message that justified it.

A close or a proposal the chat leg writes is checked for a complete
`chat_source_ref` at the moment it is written (`chat_reconcile`'s
write-time refusal). Nothing re-checked the rows already on the book, so a
row a future writer lands without the check, or a row damaged later, would
sit in the log unauditable: a close the owner could not trace back to the
message that justified it.

This is a Maintenance JOB (`chat-pointer-sweep`), registered beside the
reconcile legs in `maintenance_dispatcher.MAINTENANCE_JOBS` and run through
the existing fire (no scheduled task of its own). It reads the whole book,
applies the same read-side predicate the writer applies
(`connector_adapters.chat.missing_pointer_reason`, through
`chat_reconcile.is_chat_evidenced` to find the rows), and lands ONE
`pack_run` receipt under its own id: `n_checked`, `n_flagged`, and the
flagged rows by `seq` and commitment id with the reason each failed. A
clean book is a silent green receipt (empty `summary`); a flagged one
carries a line the owner can read. It writes NOTHING else: a row that fails
is reported, never edited, because history is append-only and whether a
damaged row is repaired is the owner's call.

Run: python3 shared/scripts/chat_pointer_sweep.py --workspace <root> --apply
     (without --apply it is a dry run: the findings are printed, no receipt
     lands, and the job stays due, exactly like the other script jobs)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

JOB_ID = "chat-pointer-sweep"
RECEIPT_TYPE = "pack_run"

#: The row types this sweep reads, the closes and the review proposals.
SWEPT_TYPES = frozenset({"commitment_resolved", "thread_resolved",
                         "commitment_review_proposed"})

#: How many flagged rows the receipt lists in full; the count is always whole.
LISTED_CAP = 50


def _s(v) -> str:
    return str(v if v is not None else "").strip()


def sweep_pointers(workspace_root) -> Dict[str, Any]:
    """Walk the book and check every chat-evidenced close and chat review
    proposal. READ ONLY.
    Returns `{n_checked, n_flagged, flagged: [{seq, commitment_id, type,
    reason}], listed_cap}`; never raises on a bad row (a row that cannot
    be read is flagged with the reason)."""
    from chat_reconcile import is_chat_evidenced, is_chat_proposal
    from connector_adapters.chat import missing_pointer_reason
    from events_io import iter_events

    checked = 0
    flagged: List[Dict[str, Any]] = []
    for ev in iter_events(workspace_root):
        if (isinstance(ev, dict) and ev.get("type") in SWEPT_TYPES
                and not isinstance(ev.get("data"), dict)):
            # Nothing on it can say whose it was, so it is read as a finding.
            checked += 1
            flagged.append({"seq": ev.get("seq"), "commitment_id": "",
                            "type": _s(ev.get("type")),
                            "reason": "the row's data could not be read"})
            continue
        if not (is_chat_evidenced(ev) or is_chat_proposal(ev)):
            continue
        checked += 1
        try:
            reason = missing_pointer_reason(ev)
        except Exception as exc:  # noqa: BLE001 - a row that cannot be read is a finding
            reason = f"could not be checked: {type(exc).__name__}: {exc}"
        if reason:
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            flagged.append({"seq": ev.get("seq"),
                            "commitment_id": _s(data.get("commitment_id")
                                                or data.get("thread_id")),
                            "type": _s(ev.get("type")), "reason": reason})
    return {"n_checked": checked, "n_flagged": len(flagged),
            "flagged": flagged[:LISTED_CAP], "listed_cap": LISTED_CAP}


def summary_line(result: Dict[str, Any]) -> str:
    """Empty on a clean book (a silent green receipt); one plain line when
    a saved close cannot be traced back to its chat message."""
    n = int(result.get("n_flagged") or 0)
    if not n:
        return ""
    return (f"{n} close{'s' if n != 1 else ''} or proposal{'s' if n != 1 else ''} "
            f"made from chat {'have' if n != 1 else 'has'} lost the pointer back to the "
            "message that justified it; the rows are named on the receipt "
            "and nothing was changed.")


def run_chat_pointer_sweep(workspace_root, *, apply: bool = False,
                           fired_via: Optional[str] = None,
                           triggered_by: Optional[str] = None) -> Dict[str, Any]:
    """The job: sweep, then (with `apply`) land the one receipt. A dry run
    returns the findings and writes nothing, so the job stays due."""
    result = sweep_pointers(workspace_root)
    result["job_id"] = JOB_ID
    result["summary"] = summary_line(result)
    result["dry_run"] = not apply
    if not apply:
        return result
    from receipts import effective_fired_via, log_receipt, require_writer_identity

    require_writer_identity(workspace_root=workspace_root)
    via = effective_fired_via(fired_via)
    extra = {k: result[k] for k in ("n_checked", "n_flagged", "flagged",
                                    "listed_cap", "summary")}
    extra["leg"] = JOB_ID
    try:
        receipt = log_receipt(workspace_root, JOB_ID, receipt_type=RECEIPT_TYPE,
                              status="complete", fired_via=via,
                              surfaced=int(result["n_flagged"]),
                              triggered_by=triggered_by, extra_data=extra)
        result["receipt"] = receipt
    except Exception as exc:  # noqa: BLE001 - no receipt keeps the job due
        print(f"chat_pointer_sweep: receipt FAILED to land ({type(exc).__name__}: "
              f"{exc})", file=sys.stderr)
        result["receipt_failed"] = True
        result["receipt_error"] = f"{type(exc).__name__}: {exc}"
    return result


def main(argv=None) -> int:
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(
        description="Command Room chat pointer sweep (cr1#164)")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--fired-via", default=None,
                        help="the seat decides when nothing is said "
                             "(receipts.effective_fired_via)")
    parser.add_argument("--triggered-by", default=None,
                        help="the surface that asked for this run")
    args = parser.parse_args(argv)
    if getattr(args, "triggered_by", None):
        os.environ["CR_TRIGGERED_BY"] = str(args.triggered_by)
    print(_json.dumps(run_chat_pointer_sweep(
        args.workspace, apply=args.apply, fired_via=args.fired_via,
        triggered_by=args.triggered_by), indent=2, default=str))
    return 0


__all__ = ["JOB_ID", "RECEIPT_TYPE", "LISTED_CAP", "sweep_pointers",
           "summary_line", "run_chat_pointer_sweep", "main"]


if __name__ == "__main__":
    sys.exit(main())
