"""The close-or-propose dial, read the same way by every job that closes
(cr1#756), and the one proposal writer those jobs share.

WHY. Each declared connector carries a `close_mode` of `close` or `propose`
(`connector_config`, the cr1#667 dials). The inbound-mail and tasks legs
read it; sent-mail reconcile, chat reconcile, calendar close and the exit
doors never did, so a connector the owner set to `propose` was stored and
shown back while its job still closed outright.

TWO THINGS, both small:

  * `close_mode_for(workspace_root, category)`: `{"declared", "mode",
    "skip_reason"}`. `declared` False when no connector of that category is
    on file (the job behaves as it always has); `skip_reason` set when its
    close dial is off (the job closes nothing and proposes nothing, and
    says why); `mode` is `close` or `propose` (None when undeclared).
  * `propose_closes(workspace_root, rows, ...)`: the matched closes a
    `propose` dial sends to the confirm list instead, each written through
    THE proposal builder (`cru_match.build_pending_review_event`). Asked once
    per (commitment, evidence pointer), ever, so a dismissed proposal does not
    return on the next fire; a commitment with a proposal already open is left
    alone. Every proposal is stamped `auto_close_blocked`: it waits for the
    owner, so the timeout withdraws it and never closes it. One row the gate
    refuses is counted and does not take the others with it.
  * `asked_evidence(workspace_root)`: the pairs already asked, for the two
    rails that write their proposals themselves (sent mail, chat).

Never raises into a job: an unreadable dial reads as undeclared.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

MODE_CLOSE = "close"
MODE_PROPOSE = "propose"
MODES = (MODE_CLOSE, MODE_PROPOSE)

#: The reason a close demoted by a `propose` dial carries on the confirm list.
PROPOSE_REASON = "this connector is set to ask before closing"
#: The stamp that keeps the timeout from closing a proposal nobody answered
#: (`commitment_policy.chip_ttl_action` retracts on it). The human confirm
#: does not read it.
BLOCKED_FIELD = "auto_close_blocked"


def close_mode_for(workspace_root, category: str) -> dict:
    """The declared connector's close dial for `category` (`email`, `chat`,
    `calendar`, `meetings`, `deals`...), read through the one connector
    reader (`connector_reads.for_category`)."""
    out = {"declared": False, "mode": None, "skip_reason": None}
    if not workspace_root or not category:
        return out
    try:
        from connector_reads import for_category
        rec = for_category(workspace_root, str(category), purpose="close")
    except Exception:  # noqa: BLE001 - an unreadable dial reads as undeclared
        return out
    if not isinstance(rec, dict):
        return out
    out["declared"] = True
    mode = str(((rec.get("dials") or {}).get("close_mode")) or "").strip().lower()
    out["mode"] = mode if mode in MODES else MODE_CLOSE
    if rec.get("skip_reason"):
        out["skip_reason"] = str(rec["skip_reason"])
    return out


def is_propose(dial: Optional[dict]) -> bool:
    return bool(dial) and dial.get("mode") == MODE_PROPOSE


def asked_evidence(workspace_root, *, dismissed_only: bool = False) -> set:
    """`{(commitment_id, source_ref)}` a proposal has ever been written for,
    however it ended (answered, withdrawn or still open), off the ledger's
    one proposal fold. `dismissed_only`: just the pairs the owner said no to;
    a proposal another pass left open, or the timeout withdrew, is not an
    answer. Unreadable reads as empty."""
    try:
        from commitment_policy import fold_proposals
        from events_io import load_events_owner_scoped
        events, _skipped = load_events_owner_scoped(workspace_root)
        ledger = fold_proposals(events)
        if dismissed_only:
            return {(r["commitment_id"], r["source_ref"]) for r in ledger["by_seq"].values()
                    if r["source_ref"] and r["dismissed_by"] is not None and not r["retracted"]}
        return {key for key in ledger["by_key"] if key[1]}
    except Exception:  # noqa: BLE001 - a read that fails asks again, it never stops a job
        return set()


def propose_closes(workspace_root, rows, *, source_skill: str,
                   reason: str = PROPOSE_REASON, holder: Optional[str] = None,
                   extra: Optional[dict] = None) -> dict:
    """Write one `commitment_review_proposed` per row, skipping a commitment
    that already carries an open proposal and a (commitment, source_ref) that
    was asked before. Each row: `commitment_id`, and optionally `title`,
    `primary_thread_id`, `score`, `evidence`, `evidence_ts`, `source_ref`,
    `has_completion_signal`. Rows are written one at a time, so a row the
    builder or the gate refuses costs only itself. Returns `{"n_proposed",
    "proposed": [{commitment_id, source_ref}], "n_already", "n_refused",
    "refused": [{commitment_id, error}]}`."""
    out = {"n_proposed": 0, "proposed": [], "n_already": 0,
           "n_refused": 0, "refused": []}
    rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("commitment_id")]
    if not rows:
        return out
    from cru_match import build_pending_review_event, load_open_review_proposals
    from event_gate import append_event
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    already = {(p.get("data") or {}).get("commitment_id")
               for p in load_open_review_proposals(str(events_path))}
    asked = asked_evidence(workspace_root)
    for row in rows:
        cid = str(row["commitment_id"])
        ref = str(row.get("source_ref") or "")
        if cid in already or (ref and (cid, ref) in asked):
            out["n_already"] += 1
            continue
        try:
            ev = build_pending_review_event(
                commitment_id=cid,
                primary_thread_id=str(row.get("primary_thread_id") or ""),
                source_skill=source_skill,
                proposed_resolution="auto_resolve",
                score=float(row.get("score") or 0.0),
                evidence=str(row.get("evidence") or "matched the record"),
                next_seq=None,
                title=str(row.get("title") or ""),
                evidence_ts=row.get("evidence_ts") or None,
                has_completion_signal=row.get("has_completion_signal"),
                source_ref=ref or None,
            )
            ev["data"]["close_mode_reason"] = reason
            ev["data"][BLOCKED_FIELD] = True
            if extra:
                ev["data"].update(extra)
            append_event(events_path, [ev], holder=holder or source_skill)
        except Exception as exc:  # noqa: BLE001 - one refused row never stops the batch
            out["n_refused"] += 1
            out["refused"].append({"commitment_id": cid, "error": type(exc).__name__})
            continue
        already.add(cid)
        out["n_proposed"] += 1
        out["proposed"].append({"commitment_id": cid, "source_ref": ref or None})
    return out


__all__ = ["BLOCKED_FIELD", "MODE_CLOSE", "MODE_PROPOSE", "MODES", "PROPOSE_REASON",
           "asked_evidence", "close_mode_for", "is_propose", "propose_closes"]
