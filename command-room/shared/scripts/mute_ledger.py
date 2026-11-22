#!/usr/bin/env python3
"""
Mute ledger — the read/write layer for chat_dismissal liveness (v4.6.0 S4).

WHY THIS EXISTS
===============
Every surface mutes with a `chat_dismissal` (Snooze 1d/3d/7d/14d, Not
relevant 60d), but until S4 a mute was a ONE-WAY DOOR: nothing listed the
live mutes, nothing could clear one before its TTL expired, and the triage
batch-undo reopened closed items while leaving their dismissals in place
(the F-20 P3a asymmetry — "undo" didn't undo the mutes). This module is the
fix:

  READ  — `live_mutes()` renders every ACTIVE chat_dismissal with its
          remaining TTL (the `show muted` / `show snoozed` ledger, surfaced
          by show-my-list). `active_dismissal_target_ids()` is the canonical
          liveness filter for orchestrators that suppress rows on an active
          dismissal — it honors BOTH expiry and clears.
  WRITE — `clear_dismissal()` / `clear_dismissals()` append a
          `chat_dismissal_cleared` event (the Unmute verb + the batch-undo
          mute reversal). Append-only: the dismissal stays in history; the
          clear wins because it comes later.

LIVENESS RULE (one definition — mirrors the pre-S4 orchestrator prose, plus
the clear):a dismissal is ACTIVE iff
  1. its TTL has not passed: `data.snooze_until` in the future when present,
     else within DEFAULT_TTL_HOURS of the event time (the legacy 24h
     default), AND
  2. no later `chat_dismissal_cleared` references it — by `data.dismissal_seq`
     (preferred, exact), or by a matching `data.target_id` / `data.fingerprint`
     written AFTER the dismissal (identity clears cover legacy dismissals
     whose seq the widget didn't carry).

Learned suppressions (`_hq/data/surface-preferences.json`) are a DIFFERENT
layer — durable preferences, not timed mutes — and are out of this ledger's
scope, as are the permanent `never track this` rules in
`_hq/config/commitment-rules.md`.

Pure helpers take events + a now timestamp and do no I/O; the writers go
through `event_gate.append_event` inside the events writer lock (R1c
doctrine). stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import re as _re
from pathlib import Path
from typing import Iterable, List, Optional

try:
    from event_time import event_time
    from surface_preferences import normalize_dismissal
except ImportError:  # direct-path import (tests, bash one-liners)
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from event_time import event_time
    from surface_preferences import normalize_dismissal

# Legacy default: a dismissal with no data.snooze_until expires 24h after the
# event time (the pre-v3.5.0 contract every orchestrator's filter documents).
DEFAULT_TTL_HOURS = 24

#: cr1#1364: what `clear_dismissal(..., as_undo=True)` answers when
#: a newer hide holds the same row; nothing is written.
STATUS_NEWER_HIDE = "newer_hide"
#: cr1#1366: ...and when the item's date or wait has changed since the hide
#: (`hide_undo.changed_since_hide`); nothing is written.
STATUS_NEWER_CHANGE = "newer_change"


def _clock_now(workspace_root=None):
    """CLOCK1 - the corroborated UTC instant this module stamps from.

    Swaps the CLOCK SOURCE only: every window, cutoff, threshold and output
    format around it is unchanged. A machine clock that has not synced used to
    write its own wrong reading straight into the permanent record; this reads
    the same clock, cross-checked against the newest timestamp the workspace
    already holds. Falls back to the raw machine clock if the helper is
    unavailable, so a stamp can never fail for want of corroboration.

    `workspace_root` is threaded in wherever the calling function already
    has one, because a helper that has to GUESS which workspace it is in
    guesses wrong exactly when it matters: a fire's early phases run in
    their own subprocesses, before anything has registered a root.
    """
    try:
        from trusted_now import trusted_now_utc

        return trusted_now_utc(workspace_root)
    except Exception:
        import datetime as _clock_dt

        return _clock_dt.datetime.now(_clock_dt.timezone.utc)


def _parse_dt(value) -> Optional[_dt.datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def dismissal_expiry(ev: dict) -> Optional[_dt.datetime]:
    """When this chat_dismissal stops suppressing: data.snooze_until when
    present (honors the duration the user actually picked — v3.5.0 contract),
    else event time + 24h (legacy default). None when the event carries no
    parseable time at all (treat as expired — never suppress forever on a
    malformed event)."""
    d = ev.get("data") or {}
    su = _parse_dt(d.get("snooze_until"))
    if su is not None:
        return su
    ts = _parse_dt(event_time(ev))
    if ts is None:
        return None
    return ts + _dt.timedelta(hours=DEFAULT_TTL_HOURS)


def _clear_index(events: Iterable[dict]) -> dict:
    """One pass over events → what has been cleared:
      seqs:        {dismissal_seq, ...} exact-seq clears
      by_target:   target_id -> last clear position (file order); a clear
                   marked `seq_only` (an undo's, cr1#1364) is not indexed here
      by_fp:       fingerprint -> last clear position
    Positions let identity clears apply only to dismissals written BEFORE
    the clear (a re-mute after an unmute stays live)."""
    seqs: set = set()
    by_target: dict = {}
    by_fp: dict = {}
    for idx, ev in enumerate(events):
        if ev.get("type") != "chat_dismissal_cleared":
            continue
        d = ev.get("data") or {}
        ds = d.get("dismissal_seq")
        if isinstance(ds, str) and ds.strip().isdigit():
            ds = int(ds.strip())
        if isinstance(ds, int) and not isinstance(ds, bool):
            seqs.add(ds)
        # cr1#1364: an undo's clear names its own dismissal only; its target
        # and fingerprint are kept for the readers that attribute it.
        if d.get("seq_only") is True and isinstance(ds, int) and not isinstance(ds, bool):
            continue
        if d.get("target_id") not in (None, ""):
            by_target[str(d["target_id"])] = idx
        if d.get("fingerprint") not in (None, ""):
            by_fp[str(d["fingerprint"])] = idx
    return {"seqs": seqs, "by_target": by_target, "by_fp": by_fp}


def _is_cleared(ev: dict, position: int, cleared: dict) -> bool:
    """True iff a later chat_dismissal_cleared references this dismissal
    (exact seq, or a matching target_id/fingerprint clear that comes after
    it in append order)."""
    seq = ev.get("seq")
    if isinstance(seq, int) and seq in cleared["seqs"]:
        return True
    d = ev.get("data") or {}
    tgt = d.get("target_id")
    if tgt not in (None, "") and cleared["by_target"].get(str(tgt), -1) > position:
        return True
    fp = d.get("fingerprint")
    if fp not in (None, "") and cleared["by_fp"].get(str(fp), -1) > position:
        return True
    return False


def live_mutes(events: Iterable[dict], now_iso: str) -> List[dict]:
    """Every ACTIVE chat_dismissal (unexpired AND uncleared), oldest first —
    the `show muted` ledger's data. Pure.

    Returns one row per live dismissal:
      {seq, ts, source_skill, surface, item_class, entity_id, target_id,
       fingerprint, reason, note, expires (ISO), remaining_days (float ≥ 0),
       ttl_label ("3 days left" / "expires in an hour")}
    """
    events = list(events)
    now = _parse_dt(now_iso)
    if now is None:
        return []
    cleared = _clear_index(events)
    out: List[dict] = []
    for idx, ev in enumerate(events):
        if ev.get("type") != "chat_dismissal":
            continue
        expires = dismissal_expiry(ev)
        if expires is None or expires <= now:
            continue
        if _is_cleared(ev, idx, cleared):
            continue
        d = ev.get("data") or {}
        norm = normalize_dismissal(ev) or {}
        remaining = (expires - now).total_seconds() / 86400.0
        out.append({
            "seq": ev.get("seq"),
            "ts": event_time(ev),
            "source_skill": ev.get("source_skill") or "",
            "surface": norm.get("surface") or "",
            "item_class": norm.get("item_class") or "",
            "entity_id": norm.get("entity_id"),
            "target_id": d.get("target_id"),
            "fingerprint": d.get("fingerprint"),
            "reason": d.get("reason") or "",
            "note": d.get("note") or d.get("summary") or "",
            "expires": expires.isoformat(),
            "remaining_days": round(remaining, 2),
            "ttl_label": remaining_ttl_label(remaining),
        })
    return out


def remaining_ttl_label(remaining_days: float) -> str:
    """Plain-English remaining-TTL for a ledger row — every mute states its
    duration (the F-59 rule extended to the ledger)."""
    if remaining_days >= 1.5:
        return f"{int(round(remaining_days))} days left"
    if remaining_days >= 0.75:
        return "1 day left"
    hours = max(1, int(round(remaining_days * 24)))
    return "expires in an hour" if hours == 1 else f"expires in {hours} hours"


def active_dismissal_target_ids(events: Iterable[dict], now_iso: str) -> set:
    """target_ids with a live (unexpired, uncleared) chat_dismissal — THE
    liveness filter for surfaces that suppress rows on an active dismissal
    (show-my-list's render filter, the orchestrator prose checks). Pure."""
    return {
        row["target_id"] for row in live_mutes(events, now_iso)
        if row.get("target_id") not in (None, "")
    }


# cr1#1292: the spellings a mute's target has used for a commitment: its
# canonical id, or its `commitment` event's seq, bare or prefixed (the set
# `commitment_state.normalize_commitment_id` accepts).
_SEQ_SPELLING = _re.compile(r"^(?:commitment_seq_|event_|seq_)?0*(\d+)$")


def item_snoozes(events: Iterable[dict], now_iso: str, *,
                 include_due_moves: bool = True) -> dict:
    """{commitment id: when its live snooze ends, ISO in UTC} (cr1#1292).
    Pure.

    THE answer to "is this item snoozed, and until when" for every surface:
    the open list, the plate (the Hub, the brief, End of Day), the brief's
    numbered list and the chat views. A snooze lives on the item: any live
    mute (`live_mutes`: unexpired, uncleared), from any surface and for any
    reason (a Snooze, a Later on a row owed to the owner, a hold), hides the
    commitment it names. The target is read in every spelling a writer has
    used: the id exactly as written, or the seq of the `commitment` event,
    bare or with a `seq_`, `event_` or `commitment_seq_` prefix. A seq is
    resolved only when the event at that seq is a commitment, so a mute on
    another kind of row (a proposal, a person, a message) names no
    commitment; its own id stays a key that matches no commitment row.
    Several live mutes on one item: the latest end wins.

    The owner's own item (owner ruling of 2026-11-17 on cr1#1292): a Later
    on it moves its due date (`commitment_state.apply_later`'s defer leg)
    and hides it until that date. The hide is the `hidden_until` stamp on
    that date change, read only while that change is the item's NEWEST due
    change, so any later date change (a new Later, the undo through
    `restore_due`) ends it or replaces it. `include_due_moves=False` reads
    the mute ledger alone."""
    events = list(events)
    try:
        from cru_match import _commitment_id
    except ImportError:  # pragma: no cover - direct-path import
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from cru_match import _commitment_id
    ids: set = set()
    by_seq: dict = {}
    for ev in events:
        if (ev.get("type") or ev.get("event")) != "commitment":
            continue
        cid = str(_commitment_id(ev))
        ids.add(cid)
        seq = ev.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            by_seq[seq] = cid
    out: dict = {}
    for m in live_mutes(events, now_iso):
        raw = m.get("target_id")
        if raw in (None, "") or isinstance(raw, bool):
            continue
        key = str(raw).strip()
        # An exact target is kept as written, as every reader did before
        # (an observed line, a step, any row keyed by its own id); a seq
        # spelling is resolved to the commitment at that seq.
        cid = key
        if key not in ids:
            hit = _SEQ_SPELLING.match(key)
            if hit and int(hit.group(1)) in by_seq:
                cid = by_seq[int(hit.group(1))]
        end = _parse_dt(m.get("expires"))
        if cid is None or end is None:
            continue
        prior = _parse_dt(out.get(cid))
        if prior is None or end > prior:
            out[cid] = end.astimezone(_dt.timezone.utc).isoformat()
    if include_due_moves:
        now = _parse_dt(now_iso)
        newest: dict = {}
        for ev in events:
            if (ev.get("type") or ev.get("event")) != "commitment_updated":
                continue
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            target = d.get("commitment_id") or d.get("target_id") or ev.get("commitment_id")
            # A due change, read as the loader folds one (cru_match): a new
            # due in any of its spellings, or the sanctioned clear.
            if not target or not (d.get("new_due") or d.get("due") or d.get("due_date")
                                  or d.get("due_cleared") is True):
                continue
            # Keyed exactly as the loader keys a due change (`str(target)`),
            # so the hide and the due date it belongs to always agree.
            newest[str(target)] = d.get("hidden_until")
        for cid, until in newest.items():
            end = _parse_dt(until)
            if end is None or now is None or end <= now:
                continue
            prior = _parse_dt(out.get(cid))
            if prior is None or end > prior:
                out[cid] = end.astimezone(_dt.timezone.utc).isoformat()
    return out


def drop_snoozed(opens: Iterable[dict], snoozed) -> tuple:
    """`(kept, hidden)` (cr1#1292). Pure. The open commitments with every
    snoozed item taken out, and the ones taken out. `snoozed` is
    `item_snoozes`' answer, or any collection of commitment ids. A step of a
    snoozed item goes with it, so it never stands alone as a top-level row
    while its parent is hidden."""
    opens = list(opens or [])
    hidden = {str(k) for k in (snoozed or ())}
    if not hidden or not opens:
        return opens, []
    from cru_match import _commitment_id
    parent_of = {}
    for ev in opens:
        pid = (ev.get("data") or {}).get("parent_id")
        if isinstance(pid, str) and pid.strip():
            parent_of[str(_commitment_id(ev))] = pid.strip()
    changed = True
    while changed:
        changed = False
        for cid, pid in parent_of.items():
            if cid not in hidden and pid in hidden:
                hidden.add(cid)
                changed = True
    kept = [ev for ev in opens if str(_commitment_id(ev)) not in hidden]
    gone = [ev for ev in opens if str(_commitment_id(ev)) in hidden]
    return kept, gone


def workspace_item_snoozes(workspace_root, now_iso: str, *,
                           include_due_moves: bool = True) -> dict:
    """`item_snoozes` over the owner's own ledger (the owner-scoped read
    every owner surface uses). Read only; {} when the ledger cannot be
    read, so a surface never fails for want of it."""
    try:
        from events_io import load_events_owner_scoped
        return item_snoozes(load_events_owner_scoped(str(workspace_root))[0], now_iso,
                            include_due_moves=include_due_moves)
    except Exception:  # noqa: BLE001 - no ledger read: nothing is known snoozed
        return {}


def workspace_moved_ids(workspace_root, now_iso: str, events: Optional[list] = None) -> set:
    """The commitments hidden now ONLY because a Later moved their date
    (cr1#1292): hidden from the lists until that date, but placed on its
    day by a calendar of days (the Hub's Week board). An item also under a
    live mute stays hidden. Read only; empty when the ledger cannot be read.
    cr1#1541: `events`, the owner-scoped ledger a caller already read
    (`events_io.load_events_owner_scoped`), saves reading it a second time."""
    if events is None:
        try:
            from events_io import load_events_owner_scoped
            events = load_events_owner_scoped(str(workspace_root))[0]
        except Exception:  # noqa: BLE001 - no ledger read: nothing is known moved
            return set()
    every = item_snoozes(events, now_iso)
    muted = item_snoozes(events, now_iso, include_due_moves=False)
    return {str(k) for k in every if k not in muted}


class DismissalNotFoundError(ValueError):
    """An unmute referenced a seq that is no chat_dismissal. Fail loud —
    writing an unanchored clear would silently unmute nothing (the same
    dead-letter doctrine as CommitmentIdError)."""


# FB-19 — how long a held item stays parked before it re-enters the queue on
# its own. A hold is a PROMISE TO ASK AGAIN, not a delete: 14 days is long
# enough that "I'm thinking about it" is respected, short enough that a
# forgotten decision comes back rather than dying silently.
HOLD_TTL_DAYS = 14

# The reason string that marks a chat_dismissal as a HOLD rather than a plain
# snooze. Same ledger, same liveness rule, same expiry mechanics — the reason
# is what lets a surface say "you parked this" instead of "you snoozed this",
# and what `show muted` reads to render the two families distinctly.
HOLD_REASON = "held"


def is_hold(ev: dict) -> bool:
    """True iff this chat_dismissal was written as a hold (FB-19). Pure."""
    if ev.get("type") != "chat_dismissal":
        return False
    return ((ev.get("data") or {}).get("reason") or "") == HOLD_REASON


def hold_item(
    workspace_root,
    target_id: str,
    *,
    source_skill: str,
    surface: str,
    fingerprint: str = "",
    item_class: str = "item",
    ttl_days: int = HOLD_TTL_DAYS,
    now_iso: Optional[str] = None,
) -> dict:
    """THE hold writer (FB-19 — M's ruling 2026-07-16).

    Parks one queue item because the user explicitly said so IN CHAT ("hold
    those two", "park that for now", "I'll think about it"). The item stops
    re-rendering on every surface that honors the mute ledger until either
    the user answers it (any surface calls `clear_dismissal` — a hold never
    outlives its own question) or `ttl_days` elapses and it comes back on its
    own.

    WHY THIS EXISTS: two rows parked in chat on 2026-07-16 re-rendered
    verbatim on the next fire, because "I'll think about it" had nowhere to
    live — the ledger only understood clicked snoozes. A surface that re-asks
    a question you just answered reads as not listening, and it is the
    fastest way to teach someone to stop reading the card. A hold is the
    durable record of "I heard you."

    Mechanically this IS a `chat_dismissal` (no new event type, no new
    reader): `data.reason = "held"` distinguishes it, `data.snooze_until`
    carries the expiry, and every existing liveness path — `live_mutes`,
    `active_dismissal_target_ids`, `brain_proposals.load_open_proposals` —
    honors it for free. That also means the pointer count and the staff
    meeting agree about held items by construction: the count comes from the
    same filtered projector the card renders.

    Idempotent: holding an already-held (live) item re-writes nothing and
    returns {"status": "already_held"} — a user repeating themselves must not
    silently extend the clock behind their back.
    """
    if not target_id or not str(target_id).strip():
        raise ValueError(
            "hold_item needs the queue item's target_id — an unanchored hold "
            "would suppress nothing and lie about it")
    target_id = str(target_id).strip()
    if not isinstance(ttl_days, int) or isinstance(ttl_days, bool) or ttl_days <= 0:
        raise ValueError(f"ttl_days must be a positive int; got {ttl_days!r}")

    from atomic_write import events_write_section as events_writer_lock  # LEASE3: flock, then lease
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    now_iso = now_iso or _clock_now(workspace_root).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = _parse_dt(now_iso) or _clock_now(workspace_root)

    with events_writer_lock(events_path, holder=f"hold_item:{source_skill}"):
        events = _load_events(events_path)
        if target_id in active_dismissal_target_ids(events, now_iso):
            return {"status": "already_held", "target_id": target_id}
        data = {
            "target_id": target_id,
            "surface": surface,
            "item_class": item_class,
            "reason": HOLD_REASON,
            "snooze_until": (now + _dt.timedelta(days=ttl_days)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
            "via": "hold",
        }
        if fingerprint:
            data["fingerprint"] = fingerprint
        ev = {
            "type": "chat_dismissal",
            "source_skill": source_skill,
            "primary_thread_id": "",
            "data": data,
        }
        from event_gate import append_event
        append_event(events_path, [ev], holder=source_skill)
    return {"status": "held", "target_id": target_id,
            "hold_expires": data["snooze_until"], "event": ev}


def _item_keys(events: list, raw) -> set:
    """Every spelling that names the same row as the target `raw`: the value
    itself and, when it names a commitment, its id and its seq spellings
    (the set `item_snoozes` reads). Pure."""
    key = str(raw).strip()
    keys = {key}
    try:
        from cru_match import _commitment_id
    except ImportError:  # pragma: no cover - direct-path import
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from cru_match import _commitment_id
    hit = _SEQ_SPELLING.match(key)
    for ev in events:
        if (ev.get("type") or ev.get("event")) != "commitment":
            continue
        seq = ev.get("seq")
        cid = str(_commitment_id(ev))
        if cid == key or (hit and isinstance(seq, int) and seq == int(hit.group(1))):
            keys.add(cid)
            if isinstance(seq, int) and not isinstance(seq, bool):
                keys.update({str(seq), f"seq_{seq}", f"event_{seq}", f"commitment_seq_{seq}"})
            break
    return keys


def newer_hide_seq(events: Iterable[dict], dismissal_seq) -> Optional[int]:
    """cr1#1364: the seq of a NEWER hide on the row the `chat_dismissal` at
    `dismissal_seq` hides, or None. Pure.

    A newer hide is a later `chat_dismissal` (a Snooze, a Later on a row owed
    to the owner, a hold) on the same row, in any spelling of its target or
    by the same fingerprint, that no clear has taken out. Expired or not, it
    is a newer word, judged by seq the way cr1#1344 judges a newer date. An
    undo of the older hide must not run under it: the clear it writes is
    keyed by target as well as by seq (`_is_cleared`), so it would take the
    newer hide out too."""
    events = list(events)
    try:
        want = int(dismissal_seq)
    except (TypeError, ValueError):
        return None
    own = next((ev for ev in events if ev.get("type") == "chat_dismissal"
                and ev.get("seq") == want), None)
    if own is None:
        return None
    d = own.get("data") or {}
    keys = _item_keys(events, d.get("target_id")) if d.get("target_id") not in (None, "") else set()
    fp = d.get("fingerprint")
    cleared = _clear_index(events)
    newest = None
    for idx, ev in enumerate(events):
        if ev.get("type") != "chat_dismissal":
            continue
        sq = ev.get("seq")
        if not isinstance(sq, int) or isinstance(sq, bool) or sq <= want:
            continue
        x = ev.get("data") or {}
        same = (x.get("target_id") not in (None, "") and str(x["target_id"]).strip() in keys) or (
            fp not in (None, "") and x.get("fingerprint") == fp)
        if same and not _is_cleared(ev, idx, cleared):
            newest = sq
    return newest


def _load_events(events_jsonl_path) -> list:
    try:
        from cru_match import load_events_defensively
    except ImportError:  # pragma: no cover
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from cru_match import load_events_defensively
    p = Path(events_jsonl_path)
    if not p.exists():
        return []
    events, _skipped = load_events_defensively(p)
    return events


def clear_dismissal(
    workspace_root,
    dismissal_seq,
    *,
    cleared_by: str,
    source_skill: str,
    reason: str = "unmuted",
    via: str = "unmute",
    as_undo: bool = False,
) -> dict:
    """THE unmute writer. Appends a `chat_dismissal_cleared` referencing the
    dismissal by exact seq (plus its target_id/fingerprint when the dismissal
    carries them, so identity-keyed readers agree). Idempotent: an already
    inactive dismissal (expired or previously cleared) returns
    {"status": "already_inactive"} and writes NOTHING. Loud
    DismissalNotFoundError when the seq matches no chat_dismissal. The
    scan→append sequence runs inside the events writer lock (R1c).

    `as_undo=True` is an undo's door (cr1#1364), and it changes two things.
    When a newer hide holds the same row (`newer_hide_seq`), it answers
    {"status": STATUS_NEWER_HIDE} and writes NOTHING. And the clear it writes
    is marked `seq_only`, so it takes out this one dismissal and no other:
    an identity clear (by target or fingerprint) would also lift an OLDER
    hide on the row, and undoing the newest of two Laters would bring the
    row back early. The Unmute verb leaves it off. On that door a hide on
    an item whose date or wait has changed since (cr1#1366,
    `hide_undo.changed_since_hide`) answers {"status": STATUS_NEWER_CHANGE}
    and writes NOTHING too: the undo would not put the item back as it was.
    """
    if isinstance(dismissal_seq, str) and dismissal_seq.strip().isdigit():
        dismissal_seq = int(dismissal_seq.strip())
    if not isinstance(dismissal_seq, int) or isinstance(dismissal_seq, bool):
        raise DismissalNotFoundError(
            f"dismissal_seq {dismissal_seq!r} is not an event seq — pass the "
            "chat_dismissal event's seq (the ledger row carries it verbatim)"
        )
    from atomic_write import events_write_section as events_writer_lock  # LEASE3: flock, then lease
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_writer_lock(events_path, holder=f"clear_dismissal:{source_skill}"):
        events = _load_events(events_path)
        target = None
        position = -1
        for idx, ev in enumerate(events):
            if ev.get("seq") == dismissal_seq and ev.get("type") == "chat_dismissal":
                target, position = ev, idx
                break
        if target is None:
            raise DismissalNotFoundError(
                f"seq {dismissal_seq} matches no chat_dismissal event — "
                "refusing to write an unanchored clear. Re-run `show muted` "
                "for the current ledger."
            )
        if as_undo:
            newer = newer_hide_seq(events, dismissal_seq)
            if newer is not None:
                return {"status": STATUS_NEWER_HIDE, "dismissal_seq": dismissal_seq,
                        "newer_seq": newer, "target_id": (target.get("data") or {}).get("target_id")}
        now = _clock_now(workspace_root)
        expires = dismissal_expiry(target)
        if expires is None or expires <= now:
            return {"status": "already_inactive", "dismissal_seq": dismissal_seq,
                    "detail": "expired"}
        if _is_cleared(target, position, _clear_index(events)):
            return {"status": "already_inactive", "dismissal_seq": dismissal_seq,
                    "detail": "cleared"}
        if as_undo:
            # cr1#1366: the undo promises the item reads as before the hide;
            # a newer date or wait on it since means it would not.
            from hide_undo import changed_since_hide
            if changed_since_hide(events, dismissal_seq,
                                  now.isoformat().replace("+00:00", "Z")):
                return {"status": STATUS_NEWER_CHANGE, "dismissal_seq": dismissal_seq,
                        "target_id": (target.get("data") or {}).get("target_id")}
        d = target.get("data") or {}
        data: dict = {
            "dismissal_seq": dismissal_seq,
            "cleared_by": cleared_by,
            "reason": (reason or "")[:200],
            "via": via,
        }
        if d.get("target_id") not in (None, ""):
            data["target_id"] = d["target_id"]
        if d.get("fingerprint") not in (None, ""):
            data["fingerprint"] = d["fingerprint"]
        if as_undo:
            data["seq_only"] = True   # cr1#1364: this dismissal and no other
        ev = {
            "type": "chat_dismissal_cleared",
            "source_skill": source_skill,
            "primary_thread_id": target.get("primary_thread_id") or "",
            "data": data,
        }
        from event_gate import append_event
        append_event(events_path, [ev], holder=source_skill)
    return {"status": "cleared", "dismissal_seq": dismissal_seq, "event": ev}


def clear_dismissals(
    workspace_root,
    dismissal_seqs,
    *,
    cleared_by: str,
    source_skill: str,
    reason: str = "batch undo",
    via: str = "batch_undo",
    as_undo: bool = False,
) -> List[dict]:
    """Batch unmute — the triage batch-undo's mute reversal (F-20 P3a: undo
    now also clears the batch's chat_dismissals). Same contract as
    clear_dismissal per seq; one bad seq is recorded as an error and does NOT
    abort the rest."""
    results: List[dict] = []
    for seq in dismissal_seqs or []:
        try:
            results.append(clear_dismissal(
                workspace_root, seq,
                cleared_by=cleared_by, source_skill=source_skill,
                reason=reason, via=via, as_undo=as_undo,
            ))
        except DismissalNotFoundError as e:
            results.append({
                "status": "error", "dismissal_seq": seq,
                "error": type(e).__name__, "detail": str(e),
            })
    return results


__all__ = [
    "DEFAULT_TTL_HOURS",
    "DismissalNotFoundError",
    "dismissal_expiry",
    "live_mutes",
    "remaining_ttl_label",
    "active_dismissal_target_ids",
    "workspace_moved_ids",
    "clear_dismissal",
    "clear_dismissals",
    "newer_hide_seq",
    "STATUS_NEWER_HIDE",
    "STATUS_NEWER_CHANGE",
]
