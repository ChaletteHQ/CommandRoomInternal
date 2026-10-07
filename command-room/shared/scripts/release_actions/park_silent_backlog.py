#!/usr/bin/env python3
"""CLEANUP1 (SPEC_FLOW1 Lane E) — the update itself rests the dead backlog.

WHY THIS EXISTS
---------------
Twenty-one seats are carrying a plate nobody can read. On the operator's
book on 2026-09-07: 317 open rows, 238 with no date at all, 108 older than
thirty days, and an automatic way out of about fifteen a month. EXIT1 builds
the silence rule so the backlog drains from that day forward — but "from that
day forward" leaves every existing seat exactly as overwhelming as it is
today, for weeks. This action runs the SAME rule ONCE, at update time, over
the open set the workspace already has, so the plate shrinks on update day.

THE SAME RULE MEANS THE SAME CODE (fix round 1, F-2)
----------------------------------------------------
The silence predicate lives in ONE place and it is EXIT1's:
`exit_doors.silence_candidates`, with EXIT1's constants and EXIT1's doors.
This module owns no second definition of "quiet" — `_candidates` below is a
thin adapter that asks EXIT1's predicate for the whole silent pool and then
applies the two doors that are this action's own and nobody else's: the BAR
(how many days is long enough for a one-shot that arrives unasked) and the
UNCONFIRMED rows (a guess the workspace has not had confirmed is not open in
any customer number, and parking it would hide it from the one surface built
to review it). EXIT1 merges first; this lane adopts.

WHAT IT DOES, AND WHAT IT WILL NEVER DO
---------------------------------------
Every open row nobody has touched for sixty days, with no due date on it,
PARKS. Parked is not closed and not deleted: the row stays open, keeps its
words, renders under Parked with its reason on it, comes back by itself the
moment either side touches it, and `undo` puts the whole batch back in one
word. Anything OVERDUE or DUE THIS WEEK is held back by name and never
parked, whatever the silence says (DESIGN_RULE §4 — importance first; the
door is EXIT1's, and its held-back rows arrive here as `n_important`).

It never closes a row, never drops one, never deletes anything, and never
asks a question. One act, one receipt, one batch, one undo (CONTRACT Rule 36).

"NOBODY HAS TOUCHED IT" — THE LIMIT, STATED
-------------------------------------------
The movement baseline every staleness surface passes through
(`commitment_activity.derive_commitment_movement`) records what the LEDGER
holds: our own edits, re-dates, adjudications, reopens and sent chases. An
inbound reply that nobody turned into an event is NOT on it. So "nobody has
touched it" is honest about our side and optimistic about theirs, and the
fences that make that safe are the ones above: nothing overdue and nothing
due this week is ever touched, nothing is ever ended, and the whole run is
one word away from being reversed. EXIT1's module states the same limit
about the same baseline; closing it (counting inbound mail, a calendar
acceptance, a meeting with them) is one fix in that one baseline, and it is
named as the open seam in BUILD_CLEANUP1 rather than patched here into a
second answer.

M's ruling R-3 (2026-09-07) closed the near half of it: the product's OWN
unsent draft is not somebody touching a row, and `draft_created` left
`commitment_activity.MOVEMENT_EVENT_TYPES` for every staleness surface at
once.

ONCE, AND ONLY ONCE — PER BAR (M's ruling R-2)
----------------------------------------------
Three locks, deliberately:
  * per row — a resting row is not parked twice (`park_commitments` refuses
    `already_parked`), so a second run of the same bar parks ZERO;
  * per workspace — the action's own `plugin_update_remediation` receipt is
    the MARKER (`data.receipt_role`). A seat that has had its one-shot never
    gets a second, so a customer who says `undo` KEEPS the undo: the next
    update does not quietly re-park what they just put back;
  * per BAR — the marker is read for the bar being asked about
    (`data.days`). If the bar is ever moved by a decision, the new bar
    re-arms the one-shot exactly once on every seat, including the seats
    where the first pass was a silent no-op; an `undo` at a given bar still
    stands at that bar forever.

THE SWITCH
----------
EXIT1's `exit.silence_age_out`, read with EXIT1's reader
(`commitment_policy.flow_switch_enabled`), default ON, failing to ON. ONE
off-word turns off the ongoing rule AND this one-shot, because they are one
rule; the key is on the sanctioned writer's allowlist, so the word a customer
says actually writes.

WIRING (pinned; the release cut pastes the stanza from BUILD_CLEANUP1)
---------------------------------------------------------------------
  id               v5300_park_silent_backlog
  action           auto_apply
  action_module    release_actions.park_silent_backlog
  action_function  park_silent_backlog
  detector         release_detectors.always.always_applies
No manifest FILE ships on this branch (the cut writes `shared/releases/
v5.30.0.json`); `MANIFEST_ITEM` below is the exact stanza and the suite pins
that the names in it resolve to the callable the bridge will import.

Signature per references/RELEASE_MANIFEST.md "Action contract":
    fn(events_jsonl_path, workspace_root, detector_context) -> dict
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

#: The manifest item id — the per-workspace "already had its one-shot" marker.
ITEM_ID = "v5300_park_silent_backlog"
#: The two names the release cut must not mis-wire.
ACTION_MODULE = "release_actions.park_silent_backlog"
ACTION_FUNCTION = "park_silent_backlog"

#: WHO acted. The bridge is the actor, and `parked_by` is spelled IDENTICALLY
#: to `source_skill` on purpose: that is the machine reading in every actor
#: resolver we have (ATTRIB2's `event_types.resolve_actor` returns
#: `actor_kind: machine` for an actor that IS the event's source skill), so no
#: surface can ever narrate this park as something the customer did. It is
#: never a person id.
SOURCE_SKILL = "command-room-update-bridge"
PARKED_BY = SOURCE_SKILL

#: THE BAR. Sixty days, ruled in SPEC_FLOW1 Lane E, named ONCE here.
#:
#: The replay says plainly what sixty buys on the operator's book today: ZERO.
#: His oldest untouched open row has been quiet fifty-six days — the book's
#: commitment history effectively starts in August — so the bar clears nothing
#: at all, while forty-five clears thirty-two and thirty clears seventy-eight
#: (CLEANUP1_REPLAY_2026-09-07.md §3). The bar stays at the ruled sixty, and
#: M's ruling R-1 gives the reason the numbers do not: EXIT1 lets go at sixty
#: and SKIPS rows that are already resting, so a one-shot at forty-five would
#: permanently shield exactly those thirty-two rows from the ongoing rule's
#: let-go — the tidy-up would block the drain it was built to accelerate. At
#: sixty the two bars are the same number. Moving it is a decision, not a
#: build, and it is one number in one place when the decision is made: a
#: caller (or a future detector) may pass `park_days` in the detector context,
#: and the marker is per-bar, so a new bar re-arms the one-shot once.
PARK_DAYS = 60
#: `clp_<UTC to the second>-<8 hex>` — the sweep's `swb_` shape and its
#: reasoning (a batch id that is almost-unique is not an undo contract).
BATCH_PREFIX = "clp_"
BATCH_SALT_BYTES = 4

RECEIPT_TYPE = "plugin_update_remediation"
#: `data.receipt_role` on the ONE receipt this action writes. The update
#: bridge writes its own `plugin_update_remediation` audit row for every item
#: that surfaces (SKILL.md Step 4.8c), so "which of these is the one-shot
#: marker" has to be answerable by a field and not by the type alone. Step
#: 4.8c is amended on this branch to skip its own row when the action already
#: wrote one, so ONE receipt lands; the role keeps the marker unambiguous on
#: any seat where an older bridge wrote both.
RECEIPT_ROLE = "marker"
RECEIPT_OUTCOME_APPLIED = "applied"
#: DOORS1 1.5 (the Step 0 note). The outcome a run that parked NOTHING writes.
#:
#: THE FINDING. On the operator's seat the v5.30.0 one-shot parked zero rows,
#: which was the TRUE answer (`exit-doors` the same evening rested 31 at 45
#: days and let go 0 at 60) — and it wrote nothing at all, so the ledger
#: carried no marker, no skip, no receipt and no fallback. Truthful silence
#: that is indistinguishable from "never ran" is not traceability, and
#: `applied_remediation_ids` could not carry the item.
#:
#: A zero run now leaves EXACTLY ONE receipt, under this outcome, and says
#: NOTHING to the customer (`ran=False`, so the bridge surfaces no notice).
#:
#: WHAT THIS SUPERSEDES, SAID OUT LOUD. `already_ran` used to require
#: `outcome == applied` at this bar, on the reasoning that "a receipt from a
#: run that parked nothing must not silence the item forever" (CLEANUP1 fix
#: round 1, F-5/F-10). It does not silence anything that matters any more:
#: EXIT1's ongoing 45/60 door ships and drains continuously, so a row that
#: becomes quiet AFTER update day is the ongoing rule's, never this
#: one-shot's, and re-arming the one-shot would only re-derive the same pile
#: to find what EXIT1 already rested. The marker counts as the one-shot having
#: happened; a bar CHANGE still re-arms it exactly once, which is the fence
#: that was actually load-bearing.
RECEIPT_OUTCOME_NOTHING = "nothing_to_park"

#: The words for a number of days, so the sentence the customer reads and the
#: bar it describes can never disagree (fix round 1, F-4). A bar with no
#: phrase falls back to plain days — always true, never wrong.
_PERIOD_WORDS = {
    7: "a week", 14: "two weeks", 21: "three weeks", 30: "a month",
    45: "six weeks", 60: "two months", 75: "ten weeks", 90: "three months",
}


def period_words(days) -> str:
    """The words for the bar: "two months" for 60, "six weeks" for 45, and a
    plain "30 days" for a bar with no phrase. Composed FROM the number the
    rule actually used — never a second literal standing beside it."""
    n = int(days)
    return _PERIOD_WORDS.get(n, f"{n} days")


def park_reason(days) -> str:
    """The reason line this park writes. The stem stays `no movement ...` —
    what the owed-to-you leg's un-park test matches on
    (`park_reason.startswith("no movement")`), and what EXIT1's own
    `SILENCE_PARK_REASON` uses — so a row parked by silence and then touched
    by either side comes back on its own. The undo half is said out loud
    because this park arrives unasked in the middle of an update rather than
    out of a weekly drain the customer already knows about."""
    return f"no movement {int(days)} days — put back with undo"


#: The stanza the release cut pastes into `shared/releases/v5.30.0.json`.
#: It lives here as well as in the BUILD record so the suite can scan the
#: sentence the customer reads with the shipped jargon gate BEFORE the cut.
#: ONE sentence (M's ruling R-4), and the period comes from the bar (F-4).
MANIFEST_ITEM = {
    "id": ITEM_ID,
    "detector_module": "release_detectors.always",
    "detector_function": "always_applies",
    "action": "auto_apply",
    "action_module": ACTION_MODULE,
    "action_function": ACTION_FUNCTION,
    "notice_template": (
        "Parked {n_parked} {items_word} nobody had touched in {period_words} "
        "— say undo to put them all back."
    ),
    "fallback_prompt_template": (
        "A tidy-up of your plate (resting the items nobody has touched in "
        "{period_words}) is pending; it will retry on the next update."
    ),
}


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _mint_batch_id(now_iso=None) -> str:
    import secrets
    if now_iso:
        from event_time import parse_ts
        when = parse_ts(now_iso) or datetime.now(timezone.utc)
    else:
        when = datetime.now(timezone.utc)
    stamp = when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{BATCH_PREFIX}{stamp}-{secrets.token_hex(BATCH_SALT_BYTES)}"


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def already_ran(workspace_root, *, days=None) -> bool:
    """Has this seat already had its one-shot AT THIS BAR? The action's own
    receipt is the marker, read through `events_io` like every other
    full-history read.

    Three things must hold, and each closes a hole a bare type-and-id match
    left open (fix round 1, F-5 and F-10, and M's ruling R-2):
      * it is OUR receipt — the bridge's own audit row for a surfaced item
        carries the same type and the same `item_id` and knows nothing about
        what was parked;
      * it says the act APPLIED — a receipt from a run that parked nothing
        must not silence the item forever;
      * it is the SAME BAR — a decision that moves the bar re-arms the
        one-shot exactly once, and an `undo` at the old bar still stands.

    A read that fails answers False — a missed marker costs a second pass
    that parks nothing new (every row it would touch is already resting),
    while a marker invented out of an error would silence the whole item."""
    bar = PARK_DAYS if days is None else int(days)
    try:
        from events_io import load_events_owner_scoped
        events, _skipped = load_events_owner_scoped(workspace_root)
    except Exception:
        return False
    for ev in events:
        if (ev.get("type") or ev.get("event")) != RECEIPT_TYPE:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if d.get("item_id") != ITEM_ID:
            continue
        if not (d.get("receipt_role") == RECEIPT_ROLE
                or d.get("source") == ACTION_MODULE):
            continue
        if d.get("outcome") not in (RECEIPT_OUTCOME_APPLIED,
                                    RECEIPT_OUTCOME_NOTHING):
            continue
        if _as_int(d.get("days")) != bar:
            continue
        return True
    return False


def _candidates(workspace_root, *, days, now_iso=None) -> dict:
    """The rows this one-shot would park — EXIT1's silence predicate, plus
    the two doors that belong to this action alone. NOTHING is written.

    `exit_doors.silence_candidates` is asked for the WHOLE silent pool
    (`park_days=0`, and its two lists put back together) so that the bar is
    applied here, once, to numbers EXIT1 computed. The doors EXIT1 owns —
    already resting, unmeasurable silence, a due date of any kind, and
    anything overdue or due inside the next seven days — are unconditional
    over there and are not re-implemented, re-argued or re-counted here.

    The two doors that are this action's own:
      BAR              `days` — the one-shot's bar, M's ruled sixty;
      UNCONFIRMED      rows the workspace has not had confirmed are dropped.
                       They are not open in any customer number, they have
                       their own two-day lapse, and parking a guess would
                       hide it from the one surface built to review it
                       (`show me what you'd hide`).

    Live SUB-ITEMS never reach the pool (EXIT1 partitions them out — the
    parent is the row of record). Self-owed TASK rows are deliberately IN:
    197 of the operator's 317 open rows carry no counterparty at all, and
    they are the entire reason this rule exists.

    The counts, honestly: `n_open`, `n_already_parked`, `n_undatable`,
    `n_important` and `n_dated` are EXIT1's own door counters, taken over the
    whole open set with the unconfirmed rows still in it; `n_pending_review`
    is how many rows this action then dropped, and `n_recent` how many
    confirmed, undated, measurable rows were quieter than the bar.

    Returns {days, now_iso, rows, n_open, n_pending_review, n_subitems,
    n_already_parked, n_undatable, n_recent, n_important, n_dated,
    n_candidates}; `rows` are EXIT1's row shape, quietest first."""
    from commitment_backlog_sweep import _cid, split_pending_review
    from cru_match import load_open_commitments, partition_subitems
    from events_io import load_events_owner_scoped
    from exit_doors import silence_candidates

    ws = Path(workspace_root)
    bar = int(days)
    events, _skipped = load_events_owner_scoped(ws)
    opens = load_open_commitments(str(_events_path(ws)), events=events,
                                  workspace_root=ws)
    _confirmed, pending = split_pending_review(opens)
    _top, subs = partition_subitems(opens)
    pending_ids = {_cid(ev) for ev in pending}

    found = silence_candidates(ws, now_iso=now_iso, events=events, park_days=0)
    pool = [r for r in (list(found["park"]) + list(found["let_go"]))
            if r.get("commitment_id") not in pending_ids]

    out = {"days": bar, "now_iso": now_iso, "rows": [],
           "n_open": found.get("n_open", 0),
           "n_pending_review": len(pending), "n_subitems": len(subs),
           "n_already_parked": found.get("n_parked_already", 0),
           "n_undatable": found.get("n_undateable", 0),
           "n_recent": 0,
           "n_important": found.get("n_protected", 0),
           "n_dated": max(0, found.get("n_dated", 0)
                          - found.get("n_protected", 0)),
           "n_candidates": 0}
    for row in pool:
        if int(row.get("days_quiet") or 0) < bar:
            out["n_recent"] += 1
            continue
        out["rows"].append(dict(row))
    out["rows"].sort(key=lambda r: (-int(r.get("days_quiet") or 0),
                                    r.get("commitment_id") or ""))
    out["n_candidates"] = len(out["rows"])
    return out


def plan(workspace_root, *, now_iso=None, park_days=None) -> dict:
    """Pure read — nothing is written. Statuses: `not_a_workspace`,
    `switched_off`, `already_ran`, `nothing_to_park`, `would_park`."""
    ws = Path(workspace_root)
    days = PARK_DAYS if park_days is None else int(park_days)
    base = {"status": "not_a_workspace", "rows": [], "days": days,
            "n_open": 0, "n_candidates": 0, "n_important": 0, "n_dated": 0,
            "n_recent": 0, "n_already_parked": 0, "n_undatable": 0,
            "n_pending_review": 0, "n_subitems": 0}
    if not (ws / "_hq" / "data").is_dir():
        return base
    from commitment_policy import EXIT_SILENCE_AGE_OUT_KEY, flow_switch_enabled
    if not flow_switch_enabled(ws, EXIT_SILENCE_AGE_OUT_KEY):
        return {**base, "status": "switched_off"}
    if already_ran(ws, days=days):
        return {**base, "status": "already_ran"}
    found = _candidates(ws, days=days, now_iso=now_iso)
    out = {**base, **found}
    out["status"] = "would_park" if found["rows"] else "nothing_to_park"
    return out


def apply_plan(workspace_root, planned: dict, *, batch_id=None,
               now_iso=None) -> dict:
    """Park every planned row in ONE batch, under ONE lock, through the
    sanctioned writer. Returns {n_parked, n_refused, batch_id, parked}."""
    from commitment_state import park_commitments

    batch_id = batch_id or _mint_batch_id(now_iso)
    reason = park_reason(planned.get("days") or PARK_DAYS)
    rows = [{"commitment_id": r["commitment_id"],
             "reason": reason,
             "brain_batch_id": batch_id,
             "extra_data": {"lane": "silence_age_out",
                            "days_quiet": r.get("days_quiet"),
                            "release_item_id": ITEM_ID}}
            for r in planned.get("rows") or []]
    if not rows:
        return {"n_parked": 0, "n_refused": 0, "batch_id": batch_id, "parked": []}
    results = park_commitments(workspace_root, rows, parked_by=PARKED_BY,
                               source_skill=SOURCE_SKILL)
    parked = [r["commitment_id"] for r in results if r.get("status") == "parked"]
    return {"n_parked": len(parked),
            "n_refused": len(results) - len(parked),
            "batch_id": batch_id, "parked": parked}


def _write_receipt(workspace_root, context: dict, *, manifest_version=None,
                   outcome: str = RECEIPT_OUTCOME_APPLIED) -> bool:
    """ONE `plugin_update_remediation` — the act's receipt AND the marker that
    makes it a one-shot at this bar. Written through the gated appender (it
    stamps `seq` and `ts` inside the writer lock), never by hand: CONTRACT
    Rule 31. `receipt_role` says which row is the marker, and the bridge's
    Step 4.8c reads `receipt_written` off this action's context and writes no
    second row for this item."""
    from event_gate import append_event
    data = {"item_id": ITEM_ID, "action": "auto_apply",
            "outcome": outcome, "receipt_role": RECEIPT_ROLE,
            "source": ACTION_MODULE, "days": context.get("days"),
            "counts": {k: context.get(k) for k in
                       ("n_open", "n_parked", "n_candidates", "n_important",
                        "n_dated", "n_recent", "n_already_parked",
                        "n_undatable", "n_pending_review")},
            "brain_batch_id": context.get("batch_id"),
            "undo": "say `undo` — one batch, every row back where it was"}
    if manifest_version:
        data["manifest_version"] = manifest_version
    try:
        append_event(_events_path(workspace_root),
                     [{"type": RECEIPT_TYPE, "source_skill": SOURCE_SKILL,
                       "data": data}], holder=SOURCE_SKILL)
        return True
    except Exception as exc:  # noqa: BLE001 — loud, never fatal: parks landed
        sys.stderr.write(f"[park_silent_backlog] receipt failed: {exc}\n")
        return False


def park_silent_backlog(events_jsonl_path, workspace_root,
                        detector_context) -> dict:
    """The `auto_apply` action contract (release_actions/__init__.py).

    `success=True ran=False` on every non-act (not a workspace, switched off,
    already had its one-shot, nothing quiet enough) so the bridge surfaces
    NOTHING — the customer never reads "we already did this"."""
    ctx = {"days": PARK_DAYS, "period_words": period_words(PARK_DAYS)}
    try:
        dc = detector_context if isinstance(detector_context, dict) else {}
        now_iso = dc.get("now_iso")
        planned = plan(workspace_root, now_iso=now_iso,
                       park_days=dc.get("park_days"))
        for k in ("status", "n_open", "n_candidates", "n_important", "n_dated",
                  "n_recent", "n_already_parked", "n_undatable",
                  "n_pending_review", "n_subitems", "days"):
            ctx[k] = planned.get(k)
        ctx["period_words"] = period_words(planned.get("days") or PARK_DAYS)
        ctx["n_parked"] = 0
        ctx["items_word"] = "items"
        if planned["status"] != "would_park":
            # DOORS1 1.5 — a zero-RESULT run leaves its marker, and ONLY a
            # zero-result one. `already_ran` short-circuits inside `plan`, so
            # this is reachable once per bar and the marker can never be
            # written twice. The other three statuses are deliberately NOT
            # marked: `not_a_workspace` has no ledger to mark, `switched_off`
            # is the customer's own "don't do this" and a marker would silence
            # the item for good if they switch it back on, and `already_ran`
            # is the marker already being there.
            if planned["status"] == "nothing_to_park":
                ctx["receipt_written"] = _write_receipt(
                    workspace_root, ctx,
                    manifest_version=dc.get("manifest_version"),
                    outcome=RECEIPT_OUTCOME_NOTHING)
            return {"success": True, "ran": False, "context": ctx,
                    "error": None, "fallback_prompt": None}
        applied = apply_plan(workspace_root, planned, now_iso=now_iso)
        ctx["n_parked"] = applied["n_parked"]
        ctx["n_refused"] = applied["n_refused"]
        ctx["batch_id"] = applied["batch_id"]
        ctx["items_word"] = "item" if applied["n_parked"] == 1 else "items"
        if not applied["n_parked"]:
            # Every planned row was taken by something else between the plan
            # and the write. Nothing happened; say nothing — but leave the
            # marker, for the same reason the zero-plan branch above does.
            ctx["receipt_written"] = _write_receipt(
                workspace_root, ctx,
                manifest_version=dc.get("manifest_version"),
                outcome=RECEIPT_OUTCOME_NOTHING)
            return {"success": True, "ran": False, "context": ctx,
                    "error": None, "fallback_prompt": None}
        ctx["receipt_written"] = _write_receipt(
            workspace_root, ctx, manifest_version=dc.get("manifest_version"))
        return {"success": True, "ran": True, "context": ctx,
                "error": None, "fallback_prompt": None}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "ran": False, "context": ctx,
                "error": f"{type(exc).__name__}: {exc}",
                "fallback_prompt":
                    MANIFEST_ITEM["fallback_prompt_template"].format(
                        period_words=ctx.get("period_words",
                                             period_words(PARK_DAYS)))}


def main(argv=None) -> int:
    import argparse
    import json
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--now", default=None, help="ISO instant to plan against")
    ap.add_argument("--days", type=int, default=None, help="the bar in days")
    ap.add_argument("--apply", action="store_true",
                    help="park the planned rows (default: plan only)")
    args = ap.parse_args(argv)
    ws = Path(args.workspace)
    planned = plan(ws, now_iso=args.now, park_days=args.days)
    print(json.dumps({k: v for k, v in planned.items() if k != "rows"},
                     indent=1))
    if not args.apply:
        print("MODE: DRY-RUN — nothing written. Re-run with --apply to park.")
        return 0
    dc = {}
    if args.now:
        dc["now_iso"] = args.now
    if args.days is not None:
        dc["park_days"] = args.days
    out = park_silent_backlog(_events_path(ws), ws, dc)
    print(json.dumps(out, indent=1))
    return 0 if out.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
