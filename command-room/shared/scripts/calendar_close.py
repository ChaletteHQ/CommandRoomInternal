#!/usr/bin/env python3
"""POLICY1-B DD-7 / D7 — the CALENDAR CLOSER for `scheduling` rows.

M's ruling 2 (2026-09-03): scheduling and agenda rows book silently and
are finished later by the calendar closer. This is that closer, as a
maintenance JOB (`calendar-close`, daily, after `review-expiry`).

THE PREDICATE (pure — `commitment_policy.calendar_matches`): an open,
CONFIRMED `kind: scheduling` row closes `done` when a `meeting` event
exists whose time (`data.start_ts`, else `ts`) lies inside
(capture ts, capture ts + CALENDAR_WINDOW_DAYS] AND at least one non-user
person id on the row (`person_ids` / `counterparty_id(s)`) appears in the
meeting's `person_ids`. Never `data.status` (set on 3 of 428 meetings on
the operator's book). Evidence names the meeting by date, party count and
seq — no names in evidence. Pending rows never close here.

THE OBSERVED TIER IS READ TOO (REVIEW_ATTRIB1B F-10): ATTRIB1-B's aside
rule diverts an UNDATED scheduling row to the observed tier, so a closer
that read the book alone would never see the rows ruling 2 was about. An
observed scheduling row that matches is promoted then closed `done`
through the writer's machine door (`confirmed_by: "calendar"`, never the
human `user_confirmed` flag) with the same evidence — counted separately
on the receipt, and the receipt says plainly that it closed an
UNCONFIRMED guess. Its undo is its OWN reverser
(`commitment_close_from_observed`): the promoted row is NOT reopened (that
would manufacture a pending question that never existed); a
`promotion_reversed` marker returns the observed row to the tier exactly
as it was, and the promoted close stays as history. An undone observed
close is a standing answer on that meeting.

CONFIRM-FIRST, PER ROW (fix F-5 — the SWEEPSCHED1 pattern counted FIRES,
so three empty fires spent the probation and a row could close the first
time it was ever seen): every (row, meeting) pair is OFFERED on
CALENDAR_CLOSE_CONFIRM_FIRST_RUNS proposing receipts before it closes; the
counter is the job's own receipts (`data.offered`, one entry per pair per
fire). A fire may offer some pairs and close others. Closes land on ONE run
batch with a GROUP per meeting (DD-5),
`brain_change_class: commitment_close`, so `undo <group>` reopens one
meeting's closes and `undo <run>` the fire. Every close is narrated ONCE by the CHANGED feed
(`change_feed.changes_since`, the `closed_from_calendar` line, with its
`undo`) and once on the job's own receipt line.

Every full-history read goes through `events_io`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

JOB_ID = "calendar-close"
RECEIPT_TYPE = "pack_run"
SOURCE_SKILL = "calendar-close"
BATCH_PREFIX = "cal_"
CALENDAR_WINDOW_DAYS = 45
CALENDAR_CLOSE_CONFIRM_FIRST_RUNS = 3
MODE_PROPOSED = "proposed"
MODE_APPLIED = "applied"


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _load_all(workspace_root) -> list:
    from events_io import load_events_owner_scoped
    events, _skipped = load_events_owner_scoped(workspace_root)
    return events


def prior_runs(workspace_root) -> int:
    """This job's own proposing / applying receipts (kept for the record and
    the receipt; probation itself is PER ROW — see `offers_so_far`).
    Unreadable → 0."""
    try:
        from receipts import iter_receipts
        seen = 0
        for r in iter_receipts(workspace_root, task_ids=[JOB_ID]):
            raw = r.get("raw") if isinstance(r, dict) else None
            data = raw.get("data") if isinstance(raw, dict) else None
            if isinstance(data, dict) and data.get("mode") in (MODE_PROPOSED, MODE_APPLIED):
                seen += 1
        return seen
    except Exception:
        return 0


def offers_so_far(workspace_root) -> dict:
    """F-5 — {(commitment_id, meeting_seq): n} — how many proposing receipts
    have carried each pair under `data.offered`. Unreadable → {} (every pair
    still on probation, the safe direction)."""
    out: dict = {}
    try:
        from receipts import iter_receipts
        for r in iter_receipts(workspace_root, task_ids=[JOB_ID]):
            raw = r.get("raw") if isinstance(r, dict) else None
            data = raw.get("data") if isinstance(raw, dict) else None
            for o in ((data or {}).get("offered") or []) if isinstance(data, dict) else []:
                if isinstance(o, dict) and o.get("commitment_id"):
                    key = (str(o["commitment_id"]), o.get("meeting_seq"))
                    out[key] = out.get(key, 0) + 1
    except Exception:
        return {}
    return out


def plan(workspace_root, *, now_iso=None) -> dict:
    """The candidates, no writes: {book: [...], observed: [...], n_scheduling_open,
    n_scheduling_observed}. Each candidate: {commitment_id, title, meeting_seq,
    meeting_ts, n_parties, primary_thread_id, tier}."""
    import commitment_policy as policy
    from cru_match import _is_pending_review, load_open_commitments
    from primary_user import resolve_primary_user
    events = _load_all(workspace_root)
    ep = _events_path(workspace_root)
    uid = resolve_primary_user(workspace_root) or ""
    opens = [r for r in load_open_commitments(str(ep), events=events, workspace_root=workspace_root)
             if not _is_pending_review(r)]
    meetings = [e for e in events if isinstance(e, dict) and e.get("type") == "meeting"]
    sched_book = [r for r in opens if str((r.get("data") or {}).get("kind") or "") == "scheduling"]
    book = policy.calendar_matches(sched_book, meetings, user_id=uid, now_iso=now_iso,
                                   window_days=CALENDAR_WINDOW_DAYS)
    for b in book:
        b["tier"] = "book"
    observed_rows: list = []
    try:
        from capture_gate import live_observed
        _now = policy._parse_ts(now_iso) if now_iso else None
        observed_rows = [r for r in live_observed(workspace_root, now=_now)
                         if str((r.get("data") or {}).get("kind") or "") == "scheduling"]
    except Exception:
        observed_rows = []
    observed = policy.calendar_matches(observed_rows, meetings, user_id=uid, now_iso=now_iso,
                                       window_days=CALENDAR_WINDOW_DAYS)
    for o in observed:
        o["tier"] = "observed"
    # AN UNDONE CALENDAR CLOSE IS A STANDING ANSWER (the `undone_promotions`
    # doctrine): a row the user put back after this closer closed it on a
    # meeting is never closed again on THAT meeting. Read off the ledger —
    # a calendar close reversed by a later reopen.
    undone = _undone_pairs(events)
    book = [c for c in book if (c["commitment_id"], c["meeting_seq"]) not in undone]
    observed = [c for c in observed if (c["commitment_id"], c["meeting_seq"]) not in undone]
    return {"book": book, "observed": observed,
            "n_scheduling_open": len(sched_book),
            "n_scheduling_observed": len(observed_rows)}


def _undone_pairs(events) -> set:
    """{(commitment_id, meeting_seq)} for every calendar close a later reopen
    reversed. An observed-tier close is keyed on the OBSERVED id too."""
    try:
        from closure_index import closer_target_id, reversed_closer_positions
        reversed_at = reversed_closer_positions(events)
    except Exception:
        return set()
    out = set()
    for pos, ev in enumerate(events):
        d = ev.get("data") or {}
        # the observed-side undo writes no reopen; its marker IS the answer
        if (ev.get("type") == "commitment_updated" and d.get("promotion_reversed") is True
                and d.get("observed_id")):
            out.add((str(d.get("observed_id")), d.get("meeting_seq")))
            continue
        if pos not in reversed_at or ev.get("type") != "commitment_resolved":
            continue
        if d.get("calendar_close") is not True:
            continue
        seq = d.get("meeting_seq")
        out.add((closer_target_id(ev), seq))
        if d.get("observed_id"):
            out.add((str(d.get("observed_id")), seq))
    return out


def _evidence(c: dict) -> str:
    day = str(c.get("meeting_ts") or "")[:10]
    n = int(c.get("n_parties") or 0)
    return f"meeting on {day} with {n} {'party' if n == 1 else 'parties'} (seq {c.get('meeting_seq')})"


def _close_book_row(workspace_root, c: dict, *, batch_id: str, now_iso=None) -> dict:
    import commitment_policy as policy
    from commitment_state import (AmbiguousTargetError, CommitmentIdError,
                                  OpenSubitemsError, PendingReviewError,
                                  close_commitment)
    extra = {"brain_change_class": policy.CLOSE_CHANGE_CLASS,
             "calendar_close": True, "meeting_seq": c.get("meeting_seq")}
    extra.update(policy.group_stamps(batch_id, f"meeting:{c.get('meeting_seq')}"))
    try:
        res = close_commitment(
            workspace_root, c["commitment_id"], resolved_by=SOURCE_SKILL,
            evidence=_evidence(c), source_skill=SOURCE_SKILL,
            source_ref=c.get("meeting_ref") or None, extra_data=extra,
            mint_now_iso=now_iso)
        return {"commitment_id": c["commitment_id"], "status": res.get("status")}
    except (CommitmentIdError, PendingReviewError, OpenSubitemsError,
            AmbiguousTargetError, ValueError) as exc:
        return {"commitment_id": c["commitment_id"], "status": "refused",
                "error": type(exc).__name__}


OBSERVED_CLOSE_CHANGE_CLASS = "commitment_close_from_observed"


def _close_observed_row(workspace_root, c: dict, *, batch_id: str, now_iso=None) -> dict:
    """Promote then close `done` THROUGH THE MACHINE DOOR (`confirmed_by:
    calendar` — F-1: never `user_confirmed=True`, the human flag). The close
    carries its own change class so `undo` runs the observed-side reverser."""
    import commitment_policy as policy
    from commitment_state import (AmbiguousTargetError, CommitmentIdError,
                                  OpenSubitemsError, PendingReviewError,
                                  close_commitment)
    from needs_review_queue import _promote_for_dispatch
    try:
        from event_types import AUTO_CLOSE_CONFIRMED_BY_CALENDAR as _DOOR
    except Exception:  # pragma: no cover
        _DOOR = "calendar"
    obs_ev = c.get("row")
    ref, _already, err = _promote_for_dispatch(workspace_root, obs_ev, source_skill=SOURCE_SKILL)
    if err:
        return {"commitment_id": c["commitment_id"], "status": "refused",
                "error": str(err.get("status") or "promote_failed")}
    extra = {"brain_change_class": OBSERVED_CLOSE_CHANGE_CLASS,
             "calendar_close": True, "meeting_seq": c.get("meeting_seq"),
             "from_observed": True, "observed_id": c["commitment_id"]}
    extra.update(policy.group_stamps(batch_id, f"meeting:{c.get('meeting_seq')}"))
    try:
        res = close_commitment(
            workspace_root, ref, resolved_by=SOURCE_SKILL, evidence=_evidence(c),
            source_skill=SOURCE_SKILL,
            source_ref=c.get("meeting_ref") or f"meeting:{c.get('meeting_seq')}",
            confirmed_by=_DOOR, extra_data=extra, mint_now_iso=now_iso)
        return {"commitment_id": ref, "observed_id": c["commitment_id"],
                "status": res.get("status")}
    except (CommitmentIdError, PendingReviewError, OpenSubitemsError,
            AmbiguousTargetError, ValueError) as exc:
        return {"commitment_id": ref, "status": "refused", "error": type(exc).__name__}


def reverse_observed_close(workspace_root, commitment_id, *, observed_id, meeting_seq=None,
                           undone_by: str, source_skill: str) -> dict:
    """THE observed-side reverser (F-1). Does NOT reopen the promoted row —
    that would put a pending question in the queue that never existed
    before the close. Writes ONE `commitment_updated` on the promoted row
    carrying `promotion_reversed: true` + `observed_id` (+ `meeting_seq`),
    which `capture_gate._promoted_ids` reads: the observed row is live on
    its tier again, exactly as it was; the promoted close stays as history
    and the row stays closed. A missing observed id refuses."""
    if not observed_id:
        raise ValueError("reverse_observed_close needs the observed_id the close "
                         "carried — without it nothing can be put back on the tier")
    from pathlib import Path as _Path
    from event_gate import append_event
    ep = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    # RV-1 (b) — idempotent: a marker for this (promoted row, observed id)
    # already on the ledger means the guess is already back on its tier.
    # Answer `already_reversed` (the F-9 accounting: counted apart, no
    # second marker, no brain_change_undone).
    for ev0 in _load_all(workspace_root):
        d0 = ev0.get("data") if isinstance(ev0.get("data"), dict) else {}
        if (ev0.get("type") == "commitment_updated" and d0.get("promotion_reversed") is True
                and str(d0.get("commitment_id") or "") == str(commitment_id)
                and str(d0.get("observed_id") or "") == str(observed_id)):
            return {"status": "already_reversed", "commitment_id": str(commitment_id),
                    "observed_id": str(observed_id)}  # RV-1: no second marker
    ev = {"type": "commitment_updated", "source_skill": source_skill,
          "primary_thread_id": "",
          "data": {"commitment_id": str(commitment_id), "promotion_reversed": True,
                   "observed_id": str(observed_id), "meeting_seq": meeting_seq,
                   "reversed_by": undone_by,
                   "reason": "brain undo — the observed guess goes back to its tier"}}
    append_event(ep, [ev], holder=source_skill)
    return {"status": "restored_to_observed", "commitment_id": str(commitment_id),
            "observed_id": str(observed_id), "event": ev}


def offer_line(n_book: int, n_observed: int, n_prior: int = 0, *, min_remaining=None,
               closes_enabled: bool = True) -> str:
    """The offer for the pairs still on probation. `min_remaining` is how
    many more offers the NEAREST pair needs before it closes (F-5: per row).
    CUT-A: with closing on evidence OFF the tail says what actually gates
    the close — M's word — never a fire count that will not close it."""
    n = n_book + n_observed
    if not n:
        return ""
    remaining = int(min_remaining if min_remaining is not None
                    else CALENDAR_CLOSE_CONFIRM_FIRST_RUNS - 1 - int(n_prior or 0))
    tail = (f"after {remaining} more fires" if remaining >= 2
            else "after one more fire" if remaining == 1 else "from the next fire")
    if closes_enabled is not True:
        tail = "once you say `turn on closing on evidence`"
    return (f"{n} scheduling item{'' if n == 1 else 's'} look{'s' if n == 1 else ''} booked — "
            f"a meeting with the other side landed after {'it was' if n == 1 else 'they were'} "
            f"captured. I will close {'it' if n == 1 else 'them'} on my own {tail}; "
            f"say `my plate` to see {'it' if n == 1 else 'them'}.")


def receipt_line(out: dict) -> str:
    n = int(out.get("n_closed") or 0)
    if not n:
        return ""
    n_obs = int(out.get("n_closed_from_observed") or 0)
    line = (f"{n} scheduling item{'' if n == 1 else 's'} closed — the meeting "
            f"{'it' if n == 1 else 'each'} was about happened, with the other side in the "
            f"room; say `undo` to reopen {'it' if n == 1 else 'any of them'}.")
    if n_obs:
        # F-1 — say plainly that a guess nobody confirmed was closed: it was
        # never on the plate, and `undo` puts it back on the set-aside tier,
        # not on the plate.
        one = n_obs == 1
        line += (f" {n_obs} of them {'was a guess' if one else 'were guesses'} you had never "
                 f"confirmed (set aside, not on your plate); the same `undo` puts "
                 f"{'it' if one else 'them'} back where {'it was' if one else 'they were'}.")
    return line


def _log_receipt(workspace_root, out: dict, *, fired_via: str) -> None:
    try:
        from receipts import log_receipt
        log_receipt(
            workspace_root, JOB_ID, receipt_type=RECEIPT_TYPE, fired_via=fired_via,
            surfaced=int(out.get("n_planned") or 0) if out.get("mode") == MODE_PROPOSED
            else int(out.get("n_closed") or 0),
            extra_data={
                "mode": out.get("mode"),
                "n_planned": out.get("n_planned"), "n_closed": out.get("n_closed"),
                "n_closed_from_observed": out.get("n_closed_from_observed"),
                "n_refused": out.get("n_refused"),
                "n_scheduling_open": out.get("n_scheduling_open"),
                "n_scheduling_observed": out.get("n_scheduling_observed"),
                "batch_id": out.get("batch_id"),
                "receipt_line": out.get("receipt_line"),
                "yield": out.get("yield"),
                # F-5 — the per-row probation counter: every pair this fire
                # OFFERED (not closed). `offers_so_far` reads these back.
                "offered": out.get("offered") or [],
                "n_offered": len(out.get("offered") or []),
                # CUT-A — the switch's state on this fire and the pairs it
                # held back from a close (offered again instead).
                "closes_enabled": out.get("closes_enabled"),
                "n_close_withheld": int(out.get("n_close_withheld") or 0),
            })
    except Exception:
        pass


def run_calendar_close_job(workspace_root, *, apply: bool = False, now_iso=None,
                           fired_via: str = "scheduled", batch_id=None) -> dict:
    """The job. `apply=False` plans only (no receipt). Confirm-first PER
    ROW (F-5): a (row, meeting) pair closes only after it has been offered on
    CALENDAR_CLOSE_CONFIRM_FIRST_RUNS proposing receipts; until then the
    fire offers it (the offer line names it, the receipt records it under
    `offered`). Closes land on one run batch with a group per meeting. The
    receipt carries the honest YIELD: matched / open scheduling rows, on the
    book and on the observed tier."""
    import commitment_policy as policy
    p = plan(workspace_root, now_iso=now_iso)
    n_book, n_obs = len(p["book"]), len(p["observed"])
    n_prior = prior_runs(workspace_root)
    offers = offers_so_far(workspace_root)
    out = {"ran": True, "applied": False, "mode": None, "n_prior_runs": n_prior,
           "n_planned": n_book + n_obs, "n_planned_book": n_book,
           "n_planned_observed": n_obs, "n_closed": 0,
           "n_closed_from_observed": 0, "n_refused": 0, "batch_id": None,
           "closed": [], "refused": [], "offered": [], "receipt_line": "",
           "n_scheduling_open": p["n_scheduling_open"],
           "n_scheduling_observed": p["n_scheduling_observed"],
           "yield": {"book": f"{n_book}/{p['n_scheduling_open']}",
                     "observed": f"{n_obs}/{p['n_scheduling_observed']}"}}
    if not apply:
        return out
    # F-5 — split every pair by ITS OWN offer count
    ready, to_offer = [], []
    for tier in ("book", "observed"):
        for c in p[tier]:
            n_off = offers.get((str(c["commitment_id"]), c.get("meeting_seq")), 0)
            (ready if n_off >= CALENDAR_CLOSE_CONFIRM_FIRST_RUNS else to_offer).append((tier, c, n_off))
    # CUT-A (ruling R-A(ii), coordinator's call) — the calendar closer sits
    # behind the SAME switch as the transcript pass: while OFF it keeps
    # offering and never promotes a pair to a close. Read ONCE per fire.
    from commitment_policy_pass import _closes_enabled
    closes_on = _closes_enabled(workspace_root)
    out["closes_enabled"] = closes_on
    out["n_close_withheld"] = 0
    if ready and not closes_on:  # CUT-A switch: the calendar closer never closes while OFF
        out["n_close_withheld"] = len(ready)
        to_offer.extend(ready)
        ready = []
    for tier, c, n_off in to_offer:
        out["offered"].append({"commitment_id": c["commitment_id"], "meeting_seq": c.get("meeting_seq"),
                               "tier": tier, "n_offers_before": n_off})
    if ready:
        batch_id = batch_id or (BATCH_PREFIX + policy.mint_fire_batch_id(now_iso).split("_", 1)[1])
        out["batch_id"] = batch_id
        for tier, c, _n in ready:
            r = (_close_book_row if tier == "book" else _close_observed_row)(
                workspace_root, c, batch_id=batch_id, now_iso=now_iso)
            if r.get("status") == "closed":
                out["n_closed"] += 1
                if tier == "observed":
                    out["n_closed_from_observed"] += 1
                out["closed"].append(r)
            else:
                out["n_refused"] += 1
                out["refused"].append(r)
    out["applied"] = out["n_closed"] > 0
    out["mode"] = MODE_APPLIED if out["n_closed"] else MODE_PROPOSED
    lines = []
    if out["n_closed"]:
        lines.append(receipt_line(out))
    if to_offer:
        nearest = min(CALENDAR_CLOSE_CONFIRM_FIRST_RUNS - 1 - n for _t, _c, n in to_offer)
        lines.append(offer_line(sum(1 for t, _c, _n in to_offer if t == "book"),
                                sum(1 for t, _c, _n in to_offer if t == "observed"),
                                min_remaining=max(0, nearest),
                                closes_enabled=closes_on))
    out["receipt_line"] = " ".join(x for x in lines if x)
    _log_receipt(workspace_root, out, fired_via=fired_via)
    return out


def main(argv: Optional[list] = None) -> int:
    import argparse
    import json as _json
    parser = argparse.ArgumentParser(description="Command Room calendar-close job (POLICY1-B DD-7)")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--now", default=None)
    parser.add_argument("--fired-via", default="scheduled")
    args = parser.parse_args(argv)
    print(_json.dumps(run_calendar_close_job(args.workspace, apply=args.apply, now_iso=args.now,
                                             fired_via=args.fired_via), indent=2, default=str))
    return 0


__all__ = ["JOB_ID", "RECEIPT_TYPE", "SOURCE_SKILL", "CALENDAR_WINDOW_DAYS",
           "CALENDAR_CLOSE_CONFIRM_FIRST_RUNS", "OBSERVED_CLOSE_CHANGE_CLASS",
           "plan", "prior_runs", "offers_so_far", "run_calendar_close_job", "offer_line",
           "receipt_line", "reverse_observed_close"]

if __name__ == "__main__":
    sys.exit(main())
