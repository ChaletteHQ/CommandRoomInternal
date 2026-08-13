#!/usr/bin/env python3
"""
The morning brief's receipt/post ordering, in code (SPEC BRIEFFIX1 Item C).

WHY
---

On 2026-08-09 a manual morning-brief fire posted the full digest and wrote
ONE thing: the `brief_state` audit event the driver emits. No `pack_run`
receipt, no prep receipt, no telemetry. The digest was real, the numbers in it
were real, and the substrate had no record that any of it happened.

Two harms, and they are different harms:

  1. **Nothing said so.** The watchdog reads receipts; a fire with no receipt
     looks exactly like a fire that never ran, and the last fire that DID
     receipt was hours earlier, so every health surface reported fine. The
     brief that posted without its receipt is invisible by construction.

  2. **`mark done [n]` silently aims at the wrong item.** That affordance
     resolves `[n]` against `needs_attention_ids` on the newest morning-brief
     `pack_run`. When the newest brief POSTED without a receipt, the newest
     recorded map belongs to an OLDER brief with a different numbering — so a
     one-tap close lands on whatever item used to be at that position. A wrong
     close is worse than a refused one: it is silent, and it closes real work.

WHAT THIS MODULE IS AND IS NOT
------------------------------

It is NOT a way to force the receipt to be written. The receipt is written by
an agent following prose, and no code can make a phase run. What code CAN do
is make the omission loud and the hazard fenced, which is the whole design:

  * `orphan_brief_finding` — the VALIDATOR. The newest `brief_state` with no
    morning-brief `pack_run` after it is a named red line on the health read,
    never a silence. (The ordering was flipped in the same build so the
    receipt is written BEFORE the post: an agent dying mid-fire then leaves a
    receipt and no post — which the degrade tier already blesses — instead of
    the inverse, which is the Bug #98 class.)

  * `needs_attention_map` / `resolve_mark_done` — the FENCE. When the newest
    map is older than the newest brief, one-tap closes are REFUSED in plain
    English. This kills the wrong-close hazard on its own, independently of
    whether anyone ever reads the validator's line.

Both are read-only. Neither writes, neither raises on a malformed substrate,
and both answer "I don't know" as an explicit shape rather than a guess — a
health read must never break a fire, and a fence that crashes is a fence that
gets removed.

Stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


BRIEF_TASK_ID = "morning-brief"
BRIEF_STATE_EVENT = "brief_state"
RECEIPT_EVENT = "pack_run"

# The check id, named so the red line can be pointed at.
CHECK_BRIEF_WITHOUT_RECEIPT = "brief-posted-without-receipt"

# The run modes whose finding should say "press Run Now on the scheduled
# task". A catch-up IS a scheduled fire arriving late, so it belongs here;
# everything else — `manual`, and an absent value on any pre-BRIEFFIX1 row —
# takes the hand-run wording, matching the writer's own default (RV-4).
SCHEDULED_WORDING = frozenset({"scheduled", "catchup"})

# How long after a `brief_state` the receipt may still be in flight. A fire
# composes a digest between the driver call and the receipt, and flagging a
# run that is still happening would train the reader to ignore the finding.
# Generous on purpose: this detects an ABSENT receipt, not a slow one.
RECEIPT_WINDOW_MINUTES = 20

# Clock tolerance in the other direction. `brief_state` and the receipt are
# stamped by two different writers inside one fire; a receipt whose stamp
# lands a few seconds before the state event is the same fire, not an earlier
# one.
#
# TEN seconds, not ninety (review RV-5). The old value was a generous skew
# allowance, and because `same_fire_receipt` uses it as the FIRE-IDENTITY
# window it also meant two fires less than 90s apart were indistinguishable:
# a scheduled fire's receipt was matched to a hand-run brief 60s later that
# recorded nothing, so the finding stayed quiet and `mark done [n]` handed
# back the SCHEDULED brief's ids. That is exactly the wrong-close Item C
# exists to prevent, inside a 90-second slot.
#
# Ten is still far more than the real skew: both events are stamped in UTC by
# the same writer lock, seconds apart, in one turn — the drift being tolerated
# is between two writes in one process, not between two clocks.
_ORDER_TOLERANCE = _dt.timedelta(seconds=10)

# The plain-English line. Facts plus the one action, no cause invented, no
# internal vocabulary (R3) — "brief_state" and "pack_run" are our words, not
# the CEO's.
ORPHAN_LINE = (
    "Your Morning Brief posted without recording that it ran, so its numbers "
    "can't be used for one-tap closes right now — open the Morning Brief in "
    "the Scheduled section and press Run Now once."
)

# The same fact, said to someone who ran the brief by hand (BRIEFFIX1 F6).
# "Press Run Now on the scheduled task" is the wrong instruction there: the
# scheduled task is not what fell over, and following it would re-run a
# different fire. Saying it again is the action, because saying it again is
# what re-computes the numbering and records it.
ORPHAN_LINE_MANUAL = (
    "The last Morning Brief you ran by hand didn't record its numbered list, "
    "so closing an item by its number isn't safe right now — say "
    "'morning briefing' once and the numbers will line up again."
)

STALE_MAP_REFUSAL = (
    "I can't close that one from this brief. The most recent Morning Brief "
    "didn't record its numbered list, so the numbers you're looking at and "
    "the numbers I have don't line up — closing by number now could close the "
    "wrong thing. Run the Morning Brief once and the numbers will match, or "
    "tell me what to close by name and I'll do it directly."
)

NO_MAP_REFUSAL = (
    "I don't have a numbered list from a Morning Brief to match that against "
    "yet. Run the Morning Brief once, or tell me what to close by name."
)


def _aware(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def _newest_brief_state(workspace_root):
    """The newest `brief_state` audit event as `(dt, fired_via)`, or
    `(None, None)`.

    Reads through the defensive loader for the same reason
    `latest_brief_state_event` does: a bare-string line in events.jsonl once
    reached `.get()` and raised out of a health read (Sub-bug #14b).

    `fired_via` is present since BRIEFFIX1 Item C / F1 and absent on every
    older row. It is NOT used to excuse a path — a manual brief that posts
    numbered actions with no receipt is the exact defect this module exists
    for, and the live 2026-08-09 incident WAS a manual fire. It is used to
    word the finding correctly, because "your scheduled brief lost its
    receipt" and "the brief you just ran by hand recorded nothing" need
    different next steps.
    """
    try:
        from cru_match import load_events_defensively
        from event_time import event_dt
    except Exception:  # noqa: BLE001 — a health read never breaks a surface
        return None, None
    path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not path.exists():
        return None, None
    try:
        events, _skipped = load_events_defensively(path, since_ts=None)
    except Exception:  # noqa: BLE001
        return None, None
    newest, via = None, None
    for ev in events:
        if ev.get("type") != BRIEF_STATE_EVENT:
            continue
        dt = _aware(event_dt(ev))
        if dt is not None and (newest is None or dt > newest):
            newest = dt
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            raw_via = data.get("fired_via")
            via = raw_via if isinstance(raw_via, str) and raw_via else None
    return newest, via


def same_fire_receipt(state_dt, receipts):
    """THE same-fire test: the morning-brief `pack_run` belonging to the fire
    that wrote `state_dt`, or None.

    A receipt belongs to that fire when it lands at or after the state event
    (the receipt is written later in the same turn) within clock tolerance.
    Path-agnostic by design (BRIEFFIX1 F1): a scheduled fire and a hand-run
    one both compute a state and both owe a receipt, so the test asks whether
    THIS fire recorded itself — never which kind of fire it was.

    Newest-first, so the answer is about the newest brief rather than about
    any receipt that happens to exist somewhere in history.
    """
    if state_dt is None:
        return None
    best = None
    for r in receipts or []:
        dt = _aware(r.get("dt"))
        if dt is None or dt < state_dt - _ORDER_TOLERANCE:
            continue
        if best is None or dt < _aware(best.get("dt")):
            best = r
    return best


def _brief_receipts(workspace_root) -> list:
    """Every morning-brief `pack_run`, oldest first. `[]` on any read
    failure — an unreadable substrate is not a claim that none exist, and the
    callers below treat it as "unknown" rather than "missing"."""
    try:
        from receipts import iter_receipts

        rows = iter_receipts(workspace_root, task_ids=[BRIEF_TASK_ID])
    except Exception:  # noqa: BLE001
        return []
    return [r for r in (rows or []) if r.get("type") == RECEIPT_EVENT]


def orphan_brief_finding(workspace_root, *, now=None) -> Optional[dict]:
    """THE validator: a brief that posted with no receipt behind it.

    Fires when the newest `brief_state` event has NO morning-brief `pack_run`
    at or after it (within clock tolerance) and enough time has passed that a
    receipt still in flight is not the explanation.

    Returns `{check, line, brief_state, last_receipt}` or None. Deliberately
    silent in three cases, each because the finding would be a lie:
      * no `brief_state` at all — nothing has claimed to compute a brief;
      * the newest `brief_state` is younger than the window — the fire may be
        mid-flight, and the next health read catches it if it is not;
      * a receipt exists at or after it — the fire recorded itself.
    """
    now = _aware(now) or _dt.datetime.now(_dt.timezone.utc)
    state_dt, via = _newest_brief_state(workspace_root)
    if state_dt is None:
        return None
    if now - state_dt < _dt.timedelta(minutes=RECEIPT_WINDOW_MINUTES):
        return None
    receipts = _brief_receipts(workspace_root)
    if same_fire_receipt(state_dt, receipts) is not None:
        return None
    newest_receipt = None
    for r in receipts:
        dt = _aware(r.get("dt"))
        if dt is not None and (newest_receipt is None or dt > newest_receipt):
            newest_receipt = dt
    return {
        "check": CHECK_BRIEF_WITHOUT_RECEIPT,
        # An UNLABELLED row takes the writer's own default, `manual` (review
        # RV-4). Every row written before this branch has no `fired_via`, and
        # sending those to the scheduled wording told the CEO to press Run Now
        # on a scheduled task in the one window — immediately post-upgrade —
        # where a hand-run brief is the likeliest cause. Reader and writer must
        # answer "unknown" the same way or the default is decorative.
        "line": ORPHAN_LINE if via in SCHEDULED_WORDING else ORPHAN_LINE_MANUAL,
        "fired_via": via,
        "brief_state": state_dt.isoformat(),
        "last_receipt": newest_receipt.isoformat() if newest_receipt else None,
    }


def needs_attention_map(workspace_root, *, now=None) -> dict:
    """The numbered Needs Attention list `mark done [n]` may resolve against,
    or an explicit refusal.

    Returns `{"ok": bool, "ids": [...], "refusal": str|None, "receipt": iso|
    None, "brief_state": iso|None}`.

    `ok` is False in exactly two situations, and the refusals differ because
    the CEO's next move differs:
      * no receipt has ever carried `needs_attention_ids` → NO_MAP_REFUSAL;
      * the newest receipt carrying one is OLDER than the newest
        `brief_state` → STALE_MAP_REFUSAL. This is the live hazard: a brief
        posted, its numbering is on screen, and the newest recorded numbering
        belongs to a different brief. Positions do not survive between briefs.

    PATH-AGNOSTIC (BRIEFFIX1 F1). The test is "did the newest brief record its
    own numbering", never "was the newest brief a scheduled one". Both paths
    write the state and both owe the receipt, and the incident that produced
    this module was a HAND-RUN brief posting numbered actions with nothing
    recorded — so exempting manual fires would have exempted the actual bug.
    What makes this safe rather than punitive is the other half of the fix:
    the on-demand path now writes the receipt too, so a manual brief resolves
    `mark done [n]` correctly instead of being refused.

    `now` is accepted only so callers and tests share one clock; the map's
    verdict does not depend on it.
    """
    state_dt, _via = _newest_brief_state(workspace_root)
    receipt_dt = None
    ids: list = []
    for r in _brief_receipts(workspace_root):
        raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        if "needs_attention_ids" not in data:
            continue
        dt = _aware(r.get("dt"))
        if dt is None:
            continue
        if receipt_dt is None or dt >= receipt_dt:
            receipt_dt = dt
            found = data.get("needs_attention_ids")
            ids = list(found) if isinstance(found, list) else []
    base = {
        "ok": False,
        "ids": [],
        "refusal": None,
        "receipt": receipt_dt.isoformat() if receipt_dt else None,
        "brief_state": state_dt.isoformat() if state_dt else None,
    }
    if receipt_dt is None:
        base["refusal"] = NO_MAP_REFUSAL
        return base
    if state_dt is not None and receipt_dt < state_dt - _ORDER_TOLERANCE:
        base["refusal"] = STALE_MAP_REFUSAL
        return base
    base["ok"] = True
    base["ids"] = ids
    return base


def resolve_mark_done(workspace_root, n, *, now=None) -> dict:
    """`mark done [n]` → the commitment id to close, or a refusal.

    Returns `{"ok", "id", "refusal", "n"}`. THE fence for the morning-brief
    source in apply-choices: every close by number goes through here, so the
    stale-map check cannot be forgotten at one call site. An out-of-range `n`
    is refused with its own sentence rather than clamped — guessing which item
    the CEO meant is the wrong-close hazard by another route.
    """
    out = {"ok": False, "id": None, "refusal": None, "n": n}
    mapping = needs_attention_map(workspace_root, now=now)
    if not mapping["ok"]:
        out["refusal"] = mapping["refusal"]
        return out
    try:
        index = int(n)
    except (TypeError, ValueError):
        out["refusal"] = (
            "I couldn't read that item number — say `mark done 2` with the "
            "number from the list.")
        return out
    ids = mapping["ids"]
    if index < 1 or index > len(ids):
        total = len(ids)
        out["refusal"] = (
            f"There's no item {index} on this brief — it listed {total} "
            f"{'item' if total == 1 else 'items'}. Say the number from the "
            f"list, or tell me what to close by name.")
        return out
    out["ok"] = True
    out["id"] = ids[index - 1]
    return out


__all__ = [
    "BRIEF_TASK_ID",
    "BRIEF_STATE_EVENT",
    "RECEIPT_EVENT",
    "CHECK_BRIEF_WITHOUT_RECEIPT",
    "RECEIPT_WINDOW_MINUTES",
    "ORPHAN_LINE",
    "ORPHAN_LINE_MANUAL",
    "STALE_MAP_REFUSAL",
    "NO_MAP_REFUSAL",
    "same_fire_receipt",
    "needs_attention_map",
    "orphan_brief_finding",
    "resolve_mark_done",
]
