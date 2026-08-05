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
      by_target:   target_id -> last clear position (file order)
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

    from writer_lock import events_writer_lock
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
) -> dict:
    """THE unmute writer. Appends a `chat_dismissal_cleared` referencing the
    dismissal by exact seq (plus its target_id/fingerprint when the dismissal
    carries them, so identity-keyed readers agree). Idempotent: an already
    inactive dismissal (expired or previously cleared) returns
    {"status": "already_inactive"} and writes NOTHING. Loud
    DismissalNotFoundError when the seq matches no chat_dismissal. The
    scan→append sequence runs inside the events writer lock (R1c).
    """
    if isinstance(dismissal_seq, str) and dismissal_seq.strip().isdigit():
        dismissal_seq = int(dismissal_seq.strip())
    if not isinstance(dismissal_seq, int) or isinstance(dismissal_seq, bool):
        raise DismissalNotFoundError(
            f"dismissal_seq {dismissal_seq!r} is not an event seq — pass the "
            "chat_dismissal event's seq (the ledger row carries it verbatim)"
        )
    from writer_lock import events_writer_lock
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
        now = _clock_now(workspace_root)
        expires = dismissal_expiry(target)
        if expires is None or expires <= now:
            return {"status": "already_inactive", "dismissal_seq": dismissal_seq,
                    "detail": "expired"}
        if _is_cleared(target, position, _clear_index(events)):
            return {"status": "already_inactive", "dismissal_seq": dismissal_seq,
                    "detail": "cleared"}
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
                reason=reason, via=via,
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
    "clear_dismissal",
    "clear_dismissals",
]
