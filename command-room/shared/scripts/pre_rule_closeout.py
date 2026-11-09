"""The one-time close-out of commitments from before the closure-target
rule (cr1#117, owner ruling 2026-11-06).

WHY. Books with history from before v5.11.0 hold closure events with no
pointer to any commitment, so an item can still show as open after it was
done and the open count is not one trustworthy number. No supervised
matching repair: the owner ruled that these are old commitments and
closing them out is acceptable.

WHAT. On every book, once: every open commitment created before the rule
(`RULE_CUTOFF`, the v5.11.0 release day) is closed in one pass through the
one closure path (`commitment_state.close_commitments`), with the reason
`pre-rule close-out`, on one batch so one `undo` reopens the whole batch.
Closure events with no target are left exactly as they are. The run is
recorded on the exit-doors receipt, so a second run does nothing, and each
close carries `pre_rule_closeout: true`, so an item the owner reopens stays
reopened even if a run stopped partway and the next one finishes it.
Commitments created on or after the rule are never touched. Created means
when the row reached the book (`_captured`), not its `ts`, which says when
the promise was made: an old message captured today is not from before the
rule.

WHAT STAYS OPEN, counted, and said on the receipt line:
  * a `pending_review` row. Extraction was unsure of it, so the writer lets
    only the owner's own answer close it. It stays in the review queue;
    unanswered, the review-expiry drain lapses it on its own clock, with its
    own line and its own undo.
  * a parent with a sub-item open. A parent whose sub-items all closed in
    this pass closes too; one whose sub-item stayed open stays open, because
    that sub-item came after the rule or is itself waiting in review.
  * a row the writer refused for any other reason, or whose capture time
    cannot be read.

A book that cannot be read in full raises, one damaged line included, so
nothing is closed or recorded and the next run tries again. A run that stops
partway says so the same way. Once the run is recorded, later damage changes
nothing.

WHERE. `run_once` is called by the exit-doors Maintenance job
(`exit_doors.run_exit_doors_job`), so it happens on the next Maintenance
run of every seat without a question; `plan` is read-only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

#: The rule: every closure names its target (v5.11.0, released 2026-08-11).
RULE_VERSION = "5.11.0"
RULE_CUTOFF = "2026-08-11T00:00:00Z"

SOURCE_SKILL = "pre-rule-closeout"
EVIDENCE = "pre-rule close-out"
BATCH_PREFIX = "prc_"
MARK_KEY = "pre_rule_closeout"


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _load(workspace_root, events=None) -> tuple:
    """`(rows, how many lines could not be read)`, or an exception when there
    is no book to read."""
    if events is not None:
        return list(events), 0
    from events_io import load_events_owner_scoped
    if not _events_path(workspace_root).is_file():
        raise FileNotFoundError(str(_events_path(workspace_root)))
    events, skipped = load_events_owner_scoped(workspace_root)
    return list(events or []), len(skipped or [])


def _recorded(events) -> bool:
    for ev in events:
        if not isinstance(ev, dict):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == "pack_run" and isinstance(d.get(MARK_KEY), dict) \
                and d[MARK_KEY].get("ran") is True:
            return True
    return False


def already_ran(workspace_root, *, events=None) -> bool:
    """Has the close-out finished on this book? True on an exit-doors receipt
    that recorded the run (a run that found nothing to close still counts).
    A stamped close alone does not say so: a run that stopped partway left
    some, and the rest of the book is still owed its pass."""
    return _recorded(_load(workspace_root, events)[0])


def _minted_at(commitment_id):
    """When the append gate minted this `cmt_<ULID>`: the clock of the write
    itself, where `ts` may be backdated. None for any other id."""
    from datetime import datetime, timezone
    from event_gate import _ULID_ALPHABET
    raw = str(commitment_id or "")
    ulid = raw[len("cmt_"):]
    if not raw.startswith("cmt_") or len(ulid) != 26 \
            or any(c not in _ULID_ALPHABET for c in ulid):
        return None
    ms = 0
    for c in ulid[:10]:
        ms = ms * 32 + _ULID_ALPHABET.index(c)
    try:
        return datetime.fromtimestamp(ms / 1000, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _first_row_after_rule(events, cutoff):
    """The `seq` of the first commitment the gate minted on or after the rule.
    The book is append-only, so everything behind it arrived after the rule,
    whatever its id or its `ts` say."""
    from cru_match import _commitment_id
    seqs = []
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == "commitment" and isinstance(ev.get("seq"), int):
            minted = _minted_at(_commitment_id(ev))
            if minted is not None and minted >= cutoff:
                seqs.append(ev["seq"])
    return min(seqs, default=None)


def _captured(ev, first_after_rule, cutoff):
    """When this row reached the book, or None when that cannot be read. A
    row with no minted id is from before the gate minted ids, or set its own:
    behind the first row after the rule it is after the rule, and otherwise
    its `ts` is the only clock it has."""
    from cru_match import _commitment_id, _parse_ts
    from event_time import event_time
    minted = _minted_at(_commitment_id(ev))
    if minted is not None:
        return minted
    seq = ev.get("seq")
    if first_after_rule is not None and isinstance(seq, int) and seq > first_after_rule:
        return cutoff
    raw = event_time(ev)
    return _parse_ts(raw) if raw else None


def _closed_before(events) -> set:
    """Every commitment a close-out already closed, open again or not."""
    return {str((ev.get("data") or {}).get("commitment_id"))
            for ev in events
            if isinstance(ev, dict) and ev.get("type") == "commitment_resolved"
            and isinstance(ev.get("data"), dict) and ev["data"].get(MARK_KEY) is True}


def plan(workspace_root, *, events=None) -> dict:
    """What the close-out WOULD close. `{"ran_before", "candidates": [...],
    "n_unreadable_time", "n_open"}`; each candidate `{commitment_id, title,
    captured}`. Read-only."""
    from cru_match import _commitment_field, _commitment_id, _parse_ts, load_open_commitments
    events, n_unread = _load(workspace_root, events)
    out = {"ran_before": _recorded(events),
           "candidates": [], "n_unreadable_time": 0, "n_open": 0}
    if n_unread and not out["ran_before"]:
        # A line nobody read may be a row, or the record of this very run.
        raise ValueError(f"{n_unread} lines of the book could not be read")
    cutoff = _parse_ts(RULE_CUTOFF)
    opens = load_open_commitments(str(_events_path(workspace_root)), events=events,
                                  workspace_root=workspace_root)
    out["n_open"] = len(opens)
    if out["ran_before"]:
        return out
    done = _closed_before(events)
    first_after_rule = _first_row_after_rule(events, cutoff)
    for ev in opens:
        when = _captured(ev, first_after_rule, cutoff)
        if when is None:
            out["n_unreadable_time"] += 1
            continue
        if when >= cutoff:
            continue
        if str(_commitment_id(ev)) in done:
            continue
        out["candidates"].append({
            "commitment_id": str(_commitment_id(ev)),
            "title": str(_commitment_field(ev, "title") or ""),
            "captured": when.isoformat(),
        })
    return out


def receipt_line(n: int, *, n_review: int = 0, n_open_child: int = 0,
                 n_other: int = 0) -> str:
    """The one line the owner reads. Plain words, no id, the way back, and
    what stayed open. `change_feed` composes the same sentence from here."""
    said = []
    if n > 0:
        said.append(f"Closed out {n} old item{'' if n == 1 else 's'} from before the "
                    f"closure rule (v{RULE_VERSION}); say `undo` to put "
                    f"{'it' if n == 1 else 'them all'} back.")
    kept = n_review + n_open_child + n_other
    if kept > 0:
        why = []
        if n_review:
            why.append(f"{n_review} waiting on your answer in review")
        if n_open_child:
            why.append(f"{n_open_child} with a sub-item still open")
        if n_other:
            why.append(f"{n_other} I could not close")
        said.append(f"{kept} old item{'' if kept == 1 else 's'} stayed open: "
                    f"{', '.join(why)}.")
    return " ".join(said)


def _mint_batch_id(now_iso=None) -> str:
    import secrets
    from datetime import datetime, timezone
    from cru_match import _parse_ts
    dt = (_parse_ts(now_iso) if now_iso else None) or datetime.now(timezone.utc)
    return f"{BATCH_PREFIX}{dt.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"


def run_once(workspace_root, *, apply: bool = True, now_iso: Optional[str] = None,
             batch_id: Optional[str] = None) -> dict:
    """The close-out, once per book. Returns `{"ran", "ran_before",
    "n_candidates", "n_closed", "n_review", "n_open_child", "n_other",
    "n_refused", "n_unreadable_time", "batch_id", "closed": [...],
    "refused": [...], "receipt_line"}`, plus `"error"` when it stopped partway
    (`ran` is then False, so the job's receipt does not record the run).
    `apply=False` plans only. Raises when any of the book cannot be read."""
    p = plan(workspace_root)
    out = {"ran": False, "ran_before": p["ran_before"],
           "n_candidates": len(p["candidates"]), "n_closed": 0, "n_review": 0,
           "n_open_child": 0, "n_other": p["n_unreadable_time"], "n_refused": 0,
           "n_unreadable_time": p["n_unreadable_time"], "batch_id": None,
           "closed": [], "refused": [], "receipt_line": ""}
    if p["ran_before"] or not apply:
        return out
    out["ran"] = True
    if p["candidates"]:
        _close_all(workspace_root, p["candidates"], out,
                   batch_id or _mint_batch_id(now_iso))
    out["receipt_line"] = receipt_line(out["n_closed"], n_review=out["n_review"],
                                       n_open_child=out["n_open_child"],
                                       n_other=out["n_other"])
    return out


def _close_all(workspace_root, candidates, out: dict, batch_id: str) -> None:
    """Each candidate through the one closure path. A parent refused for an
    open sub-item goes round again once the pass has closed something, so a
    parent closes behind its children in any order."""
    from commitment_state import close_commitments
    try:
        from commitment_policy import CLOSE_CHANGE_CLASS
    except Exception:  # noqa: BLE001
        CLOSE_CHANGE_CLASS = "commitment_close"
    from event_types import PRE_RULE_CLOSEOUT_REASON
    out["batch_id"] = batch_id
    todo = [{
        "commitment_id": c["commitment_id"],
        "resolved_by": SOURCE_SKILL,
        "evidence": EVIDENCE,
        "resolution": "dropped",
        "source_ref": f"session:{SOURCE_SKILL}:{batch_id}",
        "extra_data": {"brain_batch_id": batch_id,
                       "brain_change_class": CLOSE_CHANGE_CLASS,
                       MARK_KEY: True,
                       "resolution_reason": PRE_RULE_CLOSEOUT_REASON,
                       "rule_version": RULE_VERSION},
    } for c in candidates]
    while todo:
        closed_before = out["n_closed"]
        blocked: list = []
        for row in todo:
            try:
                r = close_commitments(workspace_root, [row], source_skill=SOURCE_SKILL)[0]
            except Exception as exc:  # noqa: BLE001 - a lock, a disk: the rest waits
                out["ran"] = False
                out["error"] = f"{type(exc).__name__}: {exc}"[:200]
                return
            why = r.get("error") or r.get("status")
            if r.get("status") == "closed":
                out["n_closed"] += 1
                out["closed"].append({"commitment_id": r.get("commitment_id")})
            elif why == "OpenSubitemsError":
                blocked.append(row)
            elif r.get("status") != "already_resolved":
                out["n_refused"] += 1
                out["n_review" if why == "PendingReviewError" else "n_other"] += 1
                out["refused"].append({"commitment_id": r.get("commitment_id"), "error": why})
        if out["n_closed"] == closed_before:
            for row in blocked:
                out["n_refused"] += 1
                out["n_open_child"] += 1
                out["refused"].append({"commitment_id": row["commitment_id"],
                                       "error": "OpenSubitemsError"})
            return
        todo = blocked


__all__ = ["RULE_VERSION", "RULE_CUTOFF", "SOURCE_SKILL", "EVIDENCE", "MARK_KEY",
           "already_ran", "plan", "run_once", "receipt_line"]
