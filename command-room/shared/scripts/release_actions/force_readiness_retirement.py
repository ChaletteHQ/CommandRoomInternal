#!/usr/bin/env python3
"""force_readiness_retirement — the retired chats actually stop firing.

WHY
---
`readiness_retirement_v1` has been in the update bridge's migration table
since TASKRET1 (2026-08-17) and has NEVER had a manifest item forcing it, so
on a seat whose update path short-circuits it simply does not run. The
attended test watched the result: Commitment Triage (Fridays 15:00) and
Pipeline Digest (Tuesdays 08:00) were still firing on the operator's seat
weeks after both were readiness-retired — the scratch workspace flagged it
independently (Step 0b), and B2.9 scored it.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
It is the PLAN half, as a manifest item, so the retirement rides the release
instead of depending on which branch of the bridge a fire happens to take.
`schedule_proposals.plan_readiness_retirements` is the one planner and this
does not re-implement a line of it.

It is NOT the pause. Nothing under `shared/scripts/` may reach a live
scheduler — that is a shipped battery guard, and `plan_readiness_retirements`
names the scheduler tool in prose for exactly that reason. The pause is
`schedule_backend.plan_update(taskId, enabled=False)`, which only the
bridge can put in front of a live scheduler, and the substrate record
(`schedule_proposals.log_readiness_retirement`) is written AFTER that call
succeeds, never before: an event claiming a task is off while it still fires
is worse than no event.

So the contract between this module and the bridge is one field:
`context["pause_task_ids"]` — the ids the bridge must switch off on this
update, in order, before it narrates. `command-room-update-bridge/SKILL.md`
Step 4.8 carries the instruction.

THE READBACK IS AN INPUT, NOT A GUESS. `detector_context` carries
`registered_ids` and/or `task_records` (the raw `list_scheduled_tasks`
records) from the bridge's own FS-16 readback. With no readback in the
context this action plans NOTHING and says so (`status:
"no_readback"`) rather than assuming a seat has the chats registered — a
migration that acts on an assumption about live schedule state is the class of
act that produced the 48 closes this train exists to undo.

IT STILL LEAVES A TRACE WHEN IT COULD NOT LOOK (SCHEDVIEW1 5.3). On the
operator's seat this item was handed neither `registered_ids` nor
`task_records`, planned nothing, and wrote nothing — indistinguishable in the
ledger from an item that never ran, which is the class the marker rider was
built to end. Three outcomes are now three receipts with three sentences: a
run that looked and acted, a run that looked and found nothing registered, and
a run that could not look. The third is a NOTE rather than the marker role, so
the item stays armed for the next update that carries a readback.

TWO STEPS, AND THE MARKER BELONGS TO THE SECOND (fix round 1, reviewer F4).
The first update ASKS: it hands the bridge `pause_task_ids` and writes a
`pause_pending` note, and the item stays ARMED, because at the moment this
function returns the pause has not happened and may never happen (a model that
skips Step 4.8c, a scheduler call that errors, a fire that is interrupted).
The second update READS THE PAUSE BACK — `pauses_confirmed` looks for the
`schedule_config_changed` row the bridge writes only after its call succeeded —
and only then writes the one-shot marker. The first cut marked the item done in
the same call that asked for the pause, which disarmed it permanently on every
seat where the pause did not land: the retired chats would go on firing with
the ledger saying they had been switched off, which is the B2.9 failure this
item exists to end, reproduced by the item meant to end it.

IDEMPOTENT on its own marker, and on the planner's `enabled` fence underneath.

Signature per references/RELEASE_MANIFEST.md "Action contract":
    fn(events_jsonl_path, workspace_root, detector_context) -> dict
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import schedule_config as sc  # noqa: E402
import schedule_proposals as sp  # noqa: E402
from event_time import parse_ts as _parse_ts  # noqa: E402


def _instant(value):
    """The INSTANT a stamp names, through the tree's own read-side parser.

    FIX ROUND 2, N-1. `pauses_confirmed`'s date gate used to compare the two
    stamps as TEXT, and this book carries three spellings of the same moment:
    on the 18,392-row scratch copy the `ts` field is `+00:00` on 10,681 rows,
    `Z` on 6,040 and an explicit NEGATIVE offset on 1,355 (1,288 of them
    `-07:00`, the operator's own zone), plus 96 naive. A pause written five
    minutes AFTER the ask but stamped `-07:00` sorts lexically BEFORE a UTC
    ask, so the row was refused and the item asked again on every update,
    forever — the "appends a fresh note on every update" symptom 5.3 exists
    to end, re-entered through 5.3's own door. Three writers already reach
    `schedule_config_changed` and one of them is a customer-facing skill.

    `event_time.parse_ts` is the tree's one read-side ISO parser (Phase 1
    Foundation / R7) and no new parser is written here. THE NAIVE CONVENTION
    IS THE TREE'S, NOT THIS MODULE'S: a stamp with no offset is read as UTC,
    which is what `parse_ts` does, what `tz.to_local` documents for the same
    input, and what every other full-history reader on this substrate
    assumes. Unreadable or absent → None, and the caller refuses — the
    direction that keeps the item armed.
    """
    return _parse_ts(str(value or "").strip())

ITEM_ID = "v5310_readiness_retirement"
ACTION_MODULE = "release_actions.force_readiness_retirement"
ACTION_FUNCTION = "force_readiness_retirement"

SOURCE_SKILL = "command-room-update-bridge"

RECEIPT_TYPE = "plugin_update_remediation"
RECEIPT_ROLE = "marker"
#: The row a run writes when it has ASKED for the pause but the pause has not
#: landed yet. It is deliberately NOT the marker role and NOT an accepted
#: `already_ran` outcome: it is a record that the attempt happened, and the
#: item stays armed behind it.
RECEIPT_ROLE_PENDING = "note"
RECEIPT_OUTCOME_APPLIED = "applied"
RECEIPT_OUTCOME_NOOP = "nothing_registered"
RECEIPT_OUTCOME_PENDING = "pause_pending"
#: SCHEDVIEW1 5.3 — the outcome for a run that COULD NOT LOOK.
#:
#: On the operator's own seat (attended test v5.31.0, Step 0 b) this item was
#: never handed a readback at all: it planned nothing, wrote nothing, and left
#: NO trace, which reads exactly like an item that never ran. Three states
#: have to be distinguishable afterwards — a run that looked and acted, a run
#: that looked and found nothing, and a run that could not look — so the third
#: gets its own word and its own marker instead of silence. It is written as a
#: NOTE, never the marker role: an item that could not look is not finished,
#: and `already_ran` deliberately does not accept it.
RECEIPT_OUTCOME_NO_READBACK = "no_readback"

#: The one sentence each outcome writes onto its own receipt, so a reader of
#: the ledger never has to infer which of the three happened.
OUTCOME_TEXT = {
    RECEIPT_OUTCOME_APPLIED: "the pause landed and was read back",
    RECEIPT_OUTCOME_NOOP: "nothing registered — nothing to switch off",
    RECEIPT_OUTCOME_PENDING: "asked for the pause; waiting to read it back",
    RECEIPT_OUTCOME_NO_READBACK: ("no schedule readback available — nothing "
                                  "planned"),
}

#: The two the record names (B2.9). NOT a hard-coded target list — the planner
#: decides, off `schedule_config.readiness_retired_task_ids()` — but the two
#: are named here so a check can prove the registry still classes them the way
#: M's ruling did, and say which ones by name if it ever stops.
NAMED_BY_THE_RECORD = ("commitment-triage", "pipeline-digest")

MANIFEST_ITEM = {
    "id": ITEM_ID,
    "detector_module": "release_detectors.always",
    "detector_function": "always_applies",
    "action": "auto_apply",
    "action_module": ACTION_MODULE,
    "action_function": ACTION_FUNCTION,
    "notice_template": (
        "Switched off {n_paused} scheduled chat{plural} that had already been "
        "retired — nothing you had asked for is affected."
    ),
    "fallback_prompt_template": (
        "Two retired chats may still be firing on this workspace; switching "
        "them off is pending and will retry on the next update."
    ),
}


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def already_ran(workspace_root) -> bool:
    """Has this seat already had this item? Its own marker is the answer."""
    try:
        from events_io import load_events_owner_scoped
        events, _skipped = load_events_owner_scoped(workspace_root)
    except Exception:  # noqa: BLE001
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
        # FIX ROUND 1 (reviewer F4). `RECEIPT_OUTCOME_PENDING` is absent from
        # this tuple ON PURPOSE: a run that asked the bridge to pause and has
        # no proof the pause happened must leave the item ARMED. The old code
        # wrote the marker in the same call that returned `pause_task_ids`, so
        # a bridge that skipped Step 4.8c, errored, or was interrupted
        # disarmed the item forever and the two retired chats kept firing —
        # the exact B2.9 failure this item exists to end.
        if d.get("outcome") in (RECEIPT_OUTCOME_APPLIED, RECEIPT_OUTCOME_NOOP):
            return True
    return False


def asked_at(workspace_root):
    """The instant this item last ASKED the bridge to pause — the `ts` of its
    newest `pause_pending` note — or None if it has never asked on this seat.

    Fix round 1, F-4: this is the clock `pauses_confirmed` measures against.
    """
    try:
        from events_io import load_events_owner_scoped
        events, _skipped = load_events_owner_scoped(workspace_root)
    except Exception:  # noqa: BLE001
        return None
    newest = None
    newest_at = None
    for ev in events:
        if (ev.get("type") or ev.get("event")) != RECEIPT_TYPE:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if d.get("item_id") != ITEM_ID:
            continue
        if d.get("outcome") != RECEIPT_OUTCOME_PENDING:
            continue
        ts = str(ev.get("ts") or ev.get("timestamp") or "")
        # Fix round 2, N-1: newest by INSTANT, not by text -- the same three
        # spellings reach this side too. The raw string is what is returned
        # (it is a stamp, and callers print it); only the ordering is parsed.
        inst = _instant(ts)
        if inst is not None and (newest_at is None or inst > newest_at):
            newest, newest_at = ts, inst
    return newest


def pauses_confirmed(workspace_root, task_ids, *, after=None) -> list:
    """Which of `task_ids` the SUBSTRATE says were actually switched off
    IN ANSWER TO THIS ITEM'S ASK.

    The proof is `schedule_proposals.log_readiness_retirement`'s own row — a
    `schedule_config_changed` carrying `enabled: false` for the id — which the
    bridge writes only AFTER the scheduler's own update tool succeeded. That
    ordering is stated at both ends (the wrapper's docstring, the bridge's
    Step 4.8c), so the row is the readback: no row, no confirmed pause.

    A PAUSE OLDER THAN THE ASK IS NOT AN ANSWER TO IT (fix round 1, F-4).
    `after` is this item's own `pause_pending` note (see `asked_at`), and a
    row dated before it does not count. Without that gate the check answered
    a question nobody asked: a seat carrying a pause record from weeks ago —
    for a chat that has since been switched back ON, which the readback says
    plainly — read as `pause_confirmed`, wrote "the pause landed and was read
    back", and disarmed the item forever with both chats still firing. That
    is the B2.9 failure this item exists to end, reached through the other
    door. With no ask on file nothing can be an answer to it, so nothing
    confirms and the item asks; asking twice costs one no-op scheduler call
    and one appended row, while disarming wrongly costs the customer a chat
    that never stops firing.

    Any pause PATH's row still counts — the migration's, or a customer who
    switched the chat off by hand — as long as it lands after the ask. What
    is refused is the DATE, never the author.

    THE DATE IS AN INSTANT, NOT A STRING (fix round 2, N-1). Both sides go
    through `event_time.parse_ts` before they are compared, so a pause
    stamped at the workspace's own offset (`-07:00`, 1,288 rows on the
    scratch copy) is read as the moment it names rather than sorted as text;
    a naive stamp is read as UTC, the tree's own read-side convention. An
    undated or unparseable row is still refused. See `_instant`."""
    wanted = {str(t) for t in (task_ids or []) if str(t or "").strip()}
    if not wanted:
        return []
    try:
        from events_io import load_events_owner_scoped
        events, _skipped = load_events_owner_scoped(workspace_root)
    except Exception:  # noqa: BLE001
        return []
    seen: set = set()
    after_at = _instant(after) if after else None
    for ev in events:
        if (ev.get("type") or ev.get("event")) != sc.SCHEDULE_CONFIG_CHANGED:
            continue
        if after:
            ts = str(ev.get("ts") or ev.get("timestamp") or "")
            # BOTH SIDES ARE PARSED BEFORE THEY ARE COMPARED (fix round 2,
            # N-1) -- see `_instant`. A row stamped at the workspace's own
            # offset names the same instant as a UTC one and must be read
            # that way; comparing the text refused it and the item asked
            # forever. A row with no readable instant -- absent, blank or
            # garbage -- cannot be shown to post-date the ask and is
            # refused, which fails SAFE in the direction that keeps the item
            # armed. If the ASK itself is unreadable, nothing can be shown
            # to post-date it and every row is refused, same direction.
            row_at = _instant(ts)
            if row_at is None or after_at is None or row_at < after_at:
                continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        rows = d.get("changes")
        if not isinstance(rows, list):
            rows = [d]
        for row in rows:
            if not isinstance(row, dict):
                continue
            tid = str(row.get("task_id") or row.get("taskId") or "")
            if tid in wanted and row.get("enabled") is False:
                seen.add(tid)
    return sorted(seen)


def plan(workspace_root, *, registered_ids=None, task_records=None) -> dict:
    """Pure read. `{status, entries, pause_task_ids, summary}` where status is
    `already_marked` | `no_readback` | `nothing_registered` | `pause_confirmed`
    | `would_pause`.

    `pause_confirmed` (fix round 1, reviewer F4) is the second half of the
    two-step: the planner still names these ids (a seat whose readback carries
    no `enabled` flag cannot tell us they are off) but the substrate carries
    the bridge's own pause record for every one of them, so the work is done
    and THIS is the update that may mark the item.

    `entries` and the one-line `summary` are the registry's own — the summary
    carries each chat's comes-back clause, and this module never paraphrases
    it (that clause is what makes acting without asking legitimate).
    """
    if already_ran(workspace_root):
        return {"status": "already_marked", "entries": [],
                "pause_task_ids": [], "summary": ""}
    if registered_ids is None and task_records is None:
        return {"status": "no_readback", "entries": [],
                "pause_task_ids": [], "summary": ""}
    entries = sp.plan_readiness_retirements(registered_ids=registered_ids,
                                            task_records=task_records) or []
    ids = [e["task"] for e in entries if e.get("task")]
    if not ids:
        return {"status": "nothing_registered", "entries": [],
                "pause_task_ids": [], "summary": ""}
    asked = asked_at(workspace_root)
    confirmed = (set(pauses_confirmed(workspace_root, ids, after=asked))
                 if asked else set())
    if confirmed >= set(ids):
        return {"status": "pause_confirmed", "entries": entries,
                "pause_task_ids": [], "confirmed": sorted(confirmed),
                "summary": ""}
    todo = [i for i in ids if i not in confirmed]
    return {"status": "would_pause", "entries": entries,
            "pause_task_ids": todo, "confirmed": sorted(confirmed),
            "summary": sc.readiness_retirement_summary(todo)}


def _write_marker(workspace_root, context: dict, *, outcome: str,
                  manifest_version=None) -> bool:
    from event_gate import append_event
    ids = list(context.get("pause_task_ids") or [])
    if outcome in (RECEIPT_OUTCOME_PENDING, RECEIPT_OUTCOME_NO_READBACK):
        # Neither one is finished work, so neither takes the marker role —
        # `already_ran` accepts only APPLIED and NOOP, and that is what keeps
        # the item armed for an update that CAN look.
        role = RECEIPT_ROLE_PENDING
    else:
        role = RECEIPT_ROLE
        ids = list(context.get("confirmed") or ids)
    data = {
        "item_id": ITEM_ID,
        "action": "auto_apply",
        "outcome": outcome,
        "outcome_text": OUTCOME_TEXT.get(outcome, outcome),
        "receipt_role": role,
        "source": ACTION_MODULE,
        "migration_id": sp.READINESS_RETIREMENT_MIGRATION_ID,
        "task_ids": ids,
        "counts": {"n_paused": len(ids)},
    }
    if manifest_version:
        data["manifest_version"] = manifest_version
    try:
        append_event(_events_path(workspace_root),
                     [{"type": RECEIPT_TYPE, "source_skill": SOURCE_SKILL,
                       "data": data}], holder=SOURCE_SKILL)
        return True
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[force_readiness_retirement] marker failed: {exc}\n")
        return False


def force_readiness_retirement(events_jsonl_path, workspace_root,
                               detector_context) -> dict:
    """The `auto_apply` action contract.

    `ran=True` only when there is something for the bridge to switch off; the
    bridge then makes the scheduler calls named in `context["pause_task_ids"]`
    and logs each one through `schedule_proposals.log_readiness_retirement`
    after its call succeeds.
    """
    ctx: dict = {"pause_task_ids": [], "n_paused": 0, "plural": "s",
                 "receipt_written": False}
    try:
        dc = detector_context if isinstance(detector_context, dict) else {}
        p = plan(workspace_root,
                 registered_ids=dc.get("registered_ids"),
                 task_records=dc.get("task_records"))
        ctx["status"] = p["status"]
        ctx["summary"] = p["summary"]
        ctx["entries"] = p["entries"]
        ctx["pause_task_ids"] = p["pause_task_ids"]
        ctx["confirmed"] = p.get("confirmed") or []
        ctx["n_paused"] = len(p["pause_task_ids"])
        ctx["plural"] = "" if ctx["n_paused"] == 1 else "s"
        if p["status"] == "already_marked":
            return {"success": True, "ran": False, "context": ctx,
                    "error": None, "fallback_prompt": None}
        if p["status"] == "no_readback":
            # SCHEDVIEW1 5.3 — IT STILL LEAVES A TRACE.
            #
            # This branch used to return having written nothing at all, which
            # is how the item ran on the operator's seat and left the ledger
            # unable to tell "could not look" from "never ran" (Step 0 b).
            # It writes an honest NOTE saying it had no readback, so
            # `applied_remediation_ids` carries the item and the bridge's
            # `receipt_written` read is true — and, because a note is not the
            # marker role, `already_ran` still returns False and the item is
            # re-armable on the next update that carries a readback.
            ctx["outcome"] = RECEIPT_OUTCOME_NO_READBACK
            ctx["outcome_text"] = OUTCOME_TEXT[RECEIPT_OUTCOME_NO_READBACK]
            ctx["receipt_written"] = _write_marker(
                workspace_root, ctx, outcome=RECEIPT_OUTCOME_NO_READBACK,
                manifest_version=dc.get("manifest_version"))
            return {"success": True, "ran": False, "context": ctx,
                    "error": None, "fallback_prompt": None}
        if p["status"] == "would_pause":
            # FIX ROUND 1 (reviewer F4) — THE PAUSE HAS NOT HAPPENED YET.
            # This call is what HANDS the bridge `pause_task_ids`; the
            # scheduler calls come after it returns. Marking the item done
            # here is a claim about the future, and when that future did not
            # arrive the item was disarmed forever with the chats still
            # firing. So this writes an honest receipt saying what it asked
            # for, and the item stays armed: the NEXT update reads the
            # bridge's own pause records back and marks it then.
            ctx["outcome"] = RECEIPT_OUTCOME_PENDING
            ctx["outcome_text"] = OUTCOME_TEXT[RECEIPT_OUTCOME_PENDING]
            ctx["receipt_written"] = _write_marker(
                workspace_root, ctx, outcome=RECEIPT_OUTCOME_PENDING,
                manifest_version=dc.get("manifest_version"))
            return {"success": True, "ran": True, "context": ctx,
                    "error": None, "fallback_prompt": None}
        outcome = (RECEIPT_OUTCOME_APPLIED if p["status"] == "pause_confirmed"
                   else RECEIPT_OUTCOME_NOOP)
        ctx["outcome"] = outcome
        ctx["outcome_text"] = OUTCOME_TEXT[outcome]
        ctx["receipt_written"] = _write_marker(
            workspace_root, ctx, outcome=outcome,
            manifest_version=dc.get("manifest_version"))
        # `ran` is "did the customer's seat change on THIS update" and the
        # answer is no in both branches left here: the pause either already
        # landed (and was narrated when it was asked for) or there was
        # nothing registered to pause.
        return {"success": True, "ran": False,
                "context": ctx, "error": None, "fallback_prompt": None}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "ran": False, "context": ctx,
                "error": f"{type(exc).__name__}: {exc}",
                "fallback_prompt": MANIFEST_ITEM["fallback_prompt_template"]}


def main(argv=None) -> int:
    import argparse
    import json
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--registered", default="",
                    help="comma-separated registered task ids (the readback)")
    args = ap.parse_args(argv)
    ids = [x.strip() for x in args.registered.split(",") if x.strip()]
    print(json.dumps(plan(Path(args.workspace),
                          registered_ids=ids or None), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
