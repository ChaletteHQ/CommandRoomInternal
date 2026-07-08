#!/usr/bin/env python3
"""
commitment_state.py — THE commitment-state projector (Phase 2 Stage A, 2026-07).

WHY THIS EXISTS
===============
Surfaces disagreed on the one number that matters: within 24 hours of the
2026-07-01 audit, COMMITMENT_AGING said 104 open, MASTER_TRACKER said 54, and a
live cru_match replay said 105. Bug #85 (coach 4 vs brief 18) already proved
customers notice. The root cause is N aggregators: every surface re-derived
"how many commitments are open" with its own filters.

This module is the single projector for commitment state (Build Guide
2026-07-01 §4 Phase 2 Stage A). It absorbs brief_state.py (v3.14.8+), which
was already the deterministic commitment-state computer for the morning brief
and the commitments orchestrator — brief_state.py remains as a compat shim
importing from here, so every existing caller keeps working forever.

THE TWO CONTRACTS
=================
1. **One open set.** `load_open_commitments` (re-exported from cru_match,
   where the loader lives next to the shape-alias helpers it depends on) is
   the only definition of "open commitment". As of Stage A the loader also
   folds `commitment_updated` deferrals (data.new_due) into the effective
   due date — see cru_match.load_open_commitments — so a deferred item stops
   rendering overdue. Before Stage A those events were write-only: written by
   the orchestrator's `push to [date]` verb and the CRU schedule-shift path,
   read by nothing.
2. **One counting API.** `commitment_counts(workspace_root)` (I/O wrapper)
   and `count_commitments(open_commitments, ...)` (pure, same math) are the
   ONLY place open/overdue/undated/by-direction counts come from. Every
   surface that renders a commitment count — MASTER_TRACKER renderer,
   COMMITMENT_AGING, morning-brief header, coach headline, the Commitments
   (Pulse) orchestrator, value surfaces — MUST call one of them. The
   acceptance test is tests/run_commitment_state_test.py; it fails when a
   counting surface reads around this module.

`compute_brief_state` (below, promoted from brief_state.py) computes its
`counts` block through `count_commitments`, so the brief header, the coach
headline, and `commitment_counts()` are the same number by construction.

THE DROP RULES (why a "you owe" item does NOT surface under Needs Attention)
===========================================================================
Applied in priority order; first match wins and is recorded in `dropped` for
auditability:

1. calendar_action — a calendar event with the counter-party fulfills a
   scheduling commitment (delegates to cru_match Path 5). Closes the v3.14.7
   bug at the surfacing layer regardless of whether the daily resolver has run.
2. email_reply — the linked thread's latest message is FROM the user; they
   already replied, so the ball is not on them (morning-briefing Step 3c).
3. recent_activity — the linked thread had ANY activity in the last 7 days; the
   work is probably done but not formally closed, so surfacing it as overdue is
   noise (morning-briefing Step 3b 7-day stopgap).

Header counts (you owe / they owe / stuck) count ALL open commitments and are
NOT affected by the drops — the header preserves true workspace state; only the
surfaced Needs-Attention list is filtered. This matches the pre-existing
contract in morning-briefing Step 3b.

Pure functions over data the caller supplies (no connector I/O; the only file
I/O lives in the explicitly-named `commitment_counts` /
`compute_and_log_brief_state` / `latest_brief_state_event` wrappers).
"""
from __future__ import annotations

import datetime
import sys
from typing import Any, Iterable, Optional

# Reuse the canonical shape-aware readers + the calendar matcher so there is ONE
# definition of "open commitment", "owner", "due", and "calendar fulfills this".
try:
    from cru_match import (
        _commitment_field,
        _commitment_id,
        _is_pending_review,
        load_open_commitments,
        match_calendar_to_commitments,
    )
except ImportError:
    from pathlib import Path as _Path

    sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from cru_match import (
        _commitment_field,
        _commitment_id,
        _is_pending_review,
        load_open_commitments,
        match_calendar_to_commitments,
    )

RECENT_ACTIVITY_WINDOW_DAYS = 7

# Read-side kind default: commitments written before the Phase 1 gate carry no
# data.kind; they are promises (the behavior-preserving default the gate also
# stamps). Old shapes stay readable forever — never backfill kind on disk.
KIND_DEFAULT = "promise"


def _parse_date(value: Optional[str]) -> Optional[datetime.date]:
    """Best-effort parse of a due-date / timestamp string to a date. Accepts
    full ISO timestamps ("2026-05-29T08:00:00Z") and bare dates ("2026-05-29").
    Returns None if unparseable — callers treat None as "no known due date"
    (never overdue), the conservative choice.
    """
    if not value or not isinstance(value, str):
        return None
    head = value.strip()[:10]
    try:
        return datetime.date.fromisoformat(head)
    except ValueError:
        return None


def is_overdue(due_value: Optional[str], now_iso: str) -> bool:
    """True iff `due_value` parses to a date strictly before today's date.
    Unparseable / missing due → False (an undated commitment is not overdue).
    """
    due = _parse_date(due_value)
    if due is None:
        return False
    today = _parse_date(now_iso)
    if today is None:
        return False
    return due < today


def commitment_kind(ev: dict) -> str:
    """Read a commitment event's `data.kind`, defaulting missing/empty to
    `promise` (pre-Phase-1 events carry no kind; they are promises)."""
    d = ev.get("data") or {}
    kind = d.get("kind")
    return kind if isinstance(kind, str) and kind else KIND_DEFAULT


def _within_recent_window(activity_iso: Optional[str], now_iso: str,
                          *, days: int = RECENT_ACTIVITY_WINDOW_DAYS) -> bool:
    """True iff `activity_iso` is within `days` of `now_iso` (inclusive)."""
    act = _parse_date(activity_iso)
    now = _parse_date(now_iso)
    if act is None or now is None:
        return False
    return (now - act).days <= days and act <= now


# Default: a sent-reconcile cursor older than this many days is "stale".
RECONCILE_STALE_DAYS = 1


def reconcile_is_stale(sent_reconcile_cursor: Optional[str], now_iso: str,
                       *, days: int = RECONCILE_STALE_DAYS) -> bool:
    """True iff sent-mail reconciliation is behind: the cursor is absent, or it
    is more than `days` days older than now. When True, the brief MUST soften any
    commitment the user owes — they may have already completed it by a sent email
    that hasn't been reconciled yet — instead of telling them to redo it. This is
    the deterministic floor for Bug #98: even if a given run skips the actual
    reconciliation fetch, the brief still won't send the CEO to redo done work."""
    cur = _parse_date(sent_reconcile_cursor)
    now = _parse_date(now_iso)
    if cur is None or now is None:
        return True
    return (now - cur).days > days


# -----------------------------------------------------------------------------
# The one counting API (Stage A)
# -----------------------------------------------------------------------------


def count_commitments(
    open_commitments: list[dict],
    *,
    user_person_id: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict:
    """Canonical commitment counts over an already-loaded open set (pure).

    THE one place the open/overdue/undated/by-direction math lives. Every
    number a surface renders about open commitments comes from this function —
    directly, via `commitment_counts(workspace_root)`, or via
    `compute_brief_state(...)["counts"]` (which delegates here).

    Keys:
      total     — len(open_commitments). The canonical headline every surface
                  reports (Bug #85 / A85: never you_owe + they_owe, never a
                  confidence- or staleness-filtered subset).
      you_owe   — owner is the primary user (direction: user owes).
      they_owe  — owner is someone else (direction: owed to the user).
      unowned   — no resolvable owner_id (extraction gap; still open, still in
                  total — omitting these was the v3.18.4 A85 16-vs-18 split).
      stuck     — overdue per the EFFECTIVE due (the loader has already folded
                  `commitment_updated` deferrals, so a pushed item is not
                  stuck). 0 when now_iso is not supplied.
      undated   — no parseable due date at all (S2 target: < 30% of open).
      by_kind   — open count per data.kind (missing kind reads as `promise`).

    `user_person_id=None` (unresolvable primary user) degrades safely: nothing
    matches the user, so you_owe=0 and every owned commitment counts as
    they_owe — total/stuck/undated/by_kind stay exact.
    """
    you_owe = they_owe = unowned = stuck = undated = 0
    by_kind: dict[str, int] = {}
    for ev in open_commitments:
        owner = _commitment_field(ev, "owner_id")
        if owner and user_person_id and owner == user_person_id:
            you_owe += 1
        elif owner:
            they_owe += 1
        else:
            unowned += 1
        due = _commitment_field(ev, "due")
        if _parse_date(due) is None:
            undated += 1
        elif now_iso and is_overdue(due, now_iso):
            stuck += 1
        kind = commitment_kind(ev)
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        # Canonical open-commitment total == len(open_commitments). Both the
        # brief header and the coach MUST report THIS number (Bug #85 + the
        # A85 followup). you_owe + they_owe alone drops ownerless items.
        "total": len(open_commitments),
        "you_owe": you_owe,
        "they_owe": they_owe,
        "unowned": unowned,
        "stuck": stuck,
        "undated": undated,
        "by_kind": by_kind,
    }


def commitment_counts(
    workspace_root,
    *,
    user_person_id: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict:
    """THE counting API (Stage A acceptance): load the canonical open set from
    the workspace and return `count_commitments` over it.

    Resolves the primary user via primary_user.resolve_primary_user when not
    supplied, and `now` from the wall clock when not supplied (pass now_iso
    explicitly in tests). Surfaces that already hold the open list (to render
    rows) call `count_commitments(opens, ...)` instead — same math, no second
    read.
    """
    from pathlib import Path as _Path

    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    opens = load_open_commitments(events_path)
    if user_person_id is None:
        try:
            from primary_user import resolve_primary_user
            user_person_id = resolve_primary_user(workspace_root)
        except Exception:
            user_person_id = None
    if now_iso is None:
        now_iso = datetime.date.today().isoformat()
    return count_commitments(
        opens, user_person_id=user_person_id, now_iso=now_iso
    )


# -----------------------------------------------------------------------------
# Brief state (promoted from brief_state.py, v3.14.8+)
# -----------------------------------------------------------------------------


def compute_brief_state(
    *,
    open_commitments: list[dict],
    user_person_id: str,
    now_iso: str,
    threads: Optional[dict] = None,
    calendar_events: Optional[Iterable[dict]] = None,
    thread_activity: Optional[dict] = None,
    sent_reconcile_cursor: Optional[str] = None,
) -> dict:
    """Compute the deterministic commitment state for a brief / commitments fire.

    Inputs (all caller-supplied; this function does NO connector I/O):
      open_commitments: list of open commitment event dicts, exactly as returned
        by `load_open_commitments`.
      user_person_id: the primary user's person_id.
      now_iso: current time as an ISO string (caller passes it — keeps the
        function pure and testable; never call datetime.now() in here).
      threads: optional dict keyed by thread_id →
        {"latest_sender_is_user": bool, "latest_msg_ts": iso}. The caller resolves
        each linked thread's latest message once (Gmail get_thread) and records
        whether the user was the latest sender. Threads not present → no
        email_reply drop applied for that commitment.
      calendar_events: optional iterable of Path-5-shaped calendar event dicts
        ({attendee_person_ids, summary, created_ts, accepted_by, calendar_event_id}).
        Passed straight to `match_calendar_to_commitments`.
      thread_activity: optional dict keyed by thread_id → latest-activity ISO
        string (max ts of any event on that thread). Drives the 7-day stopgap.

    Returns:
      {
        "counts": count_commitments(open_commitments, ...) — the canonical
            counting API's dict verbatim (total/you_owe/they_owe/unowned/
            stuck/undated/by_kind),
        "needs_attention": [  # you-owe items that survived ALL drops
            {"commitment_id", "title", "owner_id", "thread_id", "due",
             "overdue": bool}
        ],
        "dropped": [ {"commitment_id", "reason"} ],  # reason in
            # {"calendar_action", "email_reply", "recent_activity"}
      }

    Surfacing scope: `needs_attention` contains only commitments the USER owes —
    the "ball is on you" / "reply to X" class where the recurring bug lived.
    Counter-party-owed items ("they owe") are counted in the header but resolved
    by the inbound/transcript paths, not surfaced-then-dropped here.
    """
    threads = threads or {}
    thread_activity = thread_activity or {}
    # Deterministic Bug #98 floor: is sent-mail reconciliation behind?
    reconcile_stale = reconcile_is_stale(sent_reconcile_cursor, now_iso)

    # Counts come from THE counting API — the brief header, the coach headline,
    # and commitment_counts() are the same number by construction (Stage A).
    counts = count_commitments(
        open_commitments, user_person_id=user_person_id, now_iso=now_iso
    )

    you_owe_commitments: list[dict] = [
        ev for ev in open_commitments
        if _commitment_field(ev, "owner_id") == user_person_id
    ]

    # Calendar-action drops: one batch call over all you-owe commitments.
    calendar_resolved_ids: set[str] = set()
    if calendar_events:
        cal_results = match_calendar_to_commitments(
            open_commitments=you_owe_commitments,
            user_person_id=user_person_id,
            calendar_events=calendar_events,
        )
        calendar_resolved_ids = {
            r["commitment_id"] for r in cal_results
            if r["recommendation"] == "auto_resolve"
        }

    needs_attention: list[dict] = []
    dropped: list[dict] = []

    for ev in you_owe_commitments:
        cid = _commitment_id(ev)
        thread_id = ev.get("primary_thread_id") or ""

        # Priority order: first matching drop wins.
        if cid in calendar_resolved_ids:
            dropped.append({"commitment_id": cid, "reason": "calendar_action"})
            continue
        t = threads.get(thread_id)
        if t and t.get("latest_sender_is_user"):
            dropped.append({"commitment_id": cid, "reason": "email_reply"})
            continue
        if _within_recent_window(thread_activity.get(thread_id), now_iso):
            dropped.append({"commitment_id": cid, "reason": "recent_activity"})
            continue

        due = _commitment_field(ev, "due")
        needs_attention.append({
            "commitment_id": cid,
            "title": _commitment_field(ev, "title") or "",
            "owner_id": user_person_id,
            "thread_id": thread_id,
            "due": due,
            "overdue": is_overdue(due, now_iso),
            # When True the brief MUST soften this item (you may have already sent
            # the email that closes it) rather than telling the CEO to redo it.
            "reconcile_stale": reconcile_stale,
        })

    return {
        "counts": counts,
        "needs_attention": needs_attention,
        "dropped": dropped,
        # True iff sent-mail reconciliation is behind (cursor stale/absent). The
        # brief reads this to soften you-owe items instead of telling the CEO to
        # redo work they may have already completed by an unreconciled send.
        "reconcile_stale": reconcile_stale,
    }


def compute_and_log_brief_state(workspace_root, *, source_skill="morning-briefing", **kwargs):
    """Compute the brief state AND emit a `brief_state` audit event carrying the
    CODE's real numbers (Bug #99).

    Why this exists: the brief was caught hand-rolling its counts instead of
    calling `compute_brief_state` (it matched the function's output by luck, then
    bypassed the drop rules it would get subtly wrong over time). You can't force
    a pure, cheap function call with a narration gate — but you CAN make the
    bypass DETECTABLE, the same way the `sent_reconcile` audit event made a skipped
    reconcile detectable (Bug #98-v3). The brief renders from THIS wrapper's
    return value; the wrapper emits a `brief_state` event whose counts come from
    `compute_brief_state` itself (not from anything the model typed). A brief with
    no `brief_state` event for its fire bypassed the computer — checkable in the
    verify loop, no honesty self-report required.

    `kwargs` are passed straight through to `compute_brief_state`.
    """
    state = compute_brief_state(**kwargs)
    try:
        from pathlib import Path as _Path
        from atomic_write import atomic_append_jsonl as _append
        events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        # seq/ts are auto-stamped inside the writer lock; the Phase 1 gate
        # validates the type (brief_state is registered in events.schema.json).
        # by_kind is dropped from the audit payload to keep the event small.
        _append(events_path, [{
            "type": "brief_state",
            "source_skill": source_skill,
            "data": {
                "counts": {k: v for k, v in state["counts"].items() if k != "by_kind"},
                "n_needs_attention": len(state["needs_attention"]),
                "reconcile_stale": state["reconcile_stale"],
            },
        }])
    except Exception:
        # Never let the audit write block the brief — the state is what matters.
        pass
    return state


def latest_brief_state_event(workspace_root) -> dict | None:
    """Return the most recent `brief_state` event's data dict (Bug #99 check), or
    None if the brief never logged one — i.e. it bypassed compute_brief_state."""
    import json
    from pathlib import Path as _Path
    p = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not p.exists():
        return None
    latest = None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") == "brief_state":
            latest = e
    return (latest or {}).get("data") if latest else None


# -----------------------------------------------------------------------------
# The single closure path (Phase 2 Stage B, F2)
# -----------------------------------------------------------------------------
#
# 408 closure events had ever been written to M's live substrate when the
# 2026-07-01 audit ran; only 43 matched a commitment. 291 carried no readable
# id and 74 carried orphan ids that matched nothing — dead letters, written by
# five different closers each constructing its own event. close_commitment()
# is the ONE way a commitment closes from Stage B on: it normalizes legacy id
# shapes to the canonical id, refuses loudly when nothing matches (no more
# orphan tombstones), is idempotent over the FULL resolved-id set (not
# log-resolution's last-200-lines window), never auto-resolves a
# pending_review item, and appends through the Phase 1 gate.

import re as _re

VALID_RESOLUTIONS = ("done", "dropped", "superseded")

# Legacy id spellings observed in the live substrate (F2): bare int 86,
# "86", "seq_86", "event_086", "commitment_seq_86" — all meaning "the
# commitment event at seq N".
_LEGACY_SEQ_ID_RE = _re.compile(r"^(?:commitment_seq_|event_|seq_)?0*(\d+)$")


class CommitmentIdError(ValueError):
    """A closure referenced an id that matches no commitment. Fail loud —
    writing the tombstone anyway is how 74 orphan closures happened."""


class PendingReviewError(ValueError):
    """Refused to auto-resolve a pending_review commitment. Extraction flagged
    it as uncertain; only an explicit user confirmation may close it
    (deep-audit 2026-05-29 finding #9 / F2)."""


def _closer_target_id(ev: dict) -> str:
    """The id a commitment_resolved / thread_resolved / commitment_superseded
    event closes — MUST mirror load_open_commitments' closer chain exactly, so
    close_commitment's idempotency agrees with what the loader actually treats
    as closed. (Stage C extended both chains together with the seq aliases —
    see `_closer_target_seqs` for that half of the mirror.)"""
    d = ev.get("data") or {}
    return (
        d.get("commitment_id")
        or d.get("thread_id")
        or d.get("id")
        or d.get("target_id")
        or ev.get("commitment_id")
        or ev.get("thread_id")
        or ev.get("id")
        or ""
    )


def _closer_target_seqs(ev: dict) -> list[int]:
    """The F3 amnesty half of the closer chain (Stage C): seqs a closure
    references via `data.commitment_seq` / `data.source_event_seq` — both map
    seq → the commitment event at that seq. Mirrors the loader exactly."""
    d = ev.get("data") or {}
    out: list[int] = []
    for field in ("commitment_seq", "source_event_seq"):
        v = d.get(field)
        if isinstance(v, bool):
            continue
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v.strip())
        if isinstance(v, int):
            out.append(v)
    return out


def _scan_commitment_index(events_jsonl_path) -> dict:
    """One pass over events.jsonl → the closure working set:
      by_id:  canonical id (_commitment_id) → commitment event
      by_seq: seq (int)                     → commitment event
      resolved_ids:  ids CURRENTLY closed (order-aware since Stage D: a
                     commitment_reopened AFTER the closure reopens it — S4
                     undo; a later re-close closes it again)
      resolved_seqs: commitment seqs currently closed via the F3 seq aliases
      kind_by_id:    effective-kind overrides from commitment_reclassified
    Mirrors load_open_commitments' state machine exactly.
    """
    from cru_match import load_events_defensively
    from pathlib import Path as _Path

    by_id: dict[str, dict] = {}
    by_seq: dict[int, dict] = {}
    closed_ids_at: dict[str, int] = {}
    closed_seqs_at: dict[int, int] = {}
    reopened_ids_at: dict[str, int] = {}
    reopened_seqs_at: dict[int, int] = {}
    kind_by_id: dict[str, str] = {}
    kind_by_seq: dict[int, str] = {}
    p = _Path(events_jsonl_path)
    if not p.exists():
        return {"by_id": by_id, "by_seq": by_seq,
                "closed_ids_at": closed_ids_at, "closed_seqs_at": closed_seqs_at,
                "reopened_ids_at": reopened_ids_at,
                "reopened_seqs_at": reopened_seqs_at,
                "kind_by_id": kind_by_id, "kind_by_seq": kind_by_seq}
    events, _skipped = load_events_defensively(p)
    for idx, ev in enumerate(events):
        et = ev.get("type") or ev.get("event") or ""
        d = ev.get("data") or {}
        if et == "commitment":
            by_id[_commitment_id(ev)] = ev
            seq = ev.get("seq")
            if isinstance(seq, int):
                by_seq[seq] = ev
        elif et in ("commitment_resolved", "thread_resolved", "commitment_superseded"):
            cid = _closer_target_id(ev)
            if cid:
                closed_ids_at[str(cid)] = idx
            for s in _closer_target_seqs(ev):
                closed_seqs_at[s] = idx
        elif et == "commitment_reopened":
            target = d.get("commitment_id") or d.get("target_id") or ev.get("commitment_id")
            if target:
                reopened_ids_at[str(target)] = idx
            v = d.get("commitment_seq")
            if isinstance(v, str) and v.strip().isdigit():
                v = int(v.strip())
            if isinstance(v, int) and not isinstance(v, bool):
                reopened_seqs_at[v] = idx
        elif et == "commitment_reclassified":
            new_kind = d.get("new_kind") or d.get("new_type")
            target = d.get("target_id") or d.get("commitment_id")
            if new_kind and target:
                kind_by_id[str(target)] = new_kind
            v = d.get("target_seq")
            if new_kind and isinstance(v, int) and not isinstance(v, bool):
                kind_by_seq[v] = new_kind

    return {"by_id": by_id, "by_seq": by_seq,
            "closed_ids_at": closed_ids_at, "closed_seqs_at": closed_seqs_at,
            "reopened_ids_at": reopened_ids_at,
            "reopened_seqs_at": reopened_seqs_at,
            "kind_by_id": kind_by_id, "kind_by_seq": kind_by_seq}


def _currently_closed(index: dict, cid: str, seq) -> bool:
    """CURRENT closure state of one commitment, cross-keyed over both the id
    chain and the F3 seq aliases: closed iff the latest closure (either
    keying) comes after the latest reopen (either keying) in append order.
    This is exactly load_open_commitments' per-commitment math — the two can
    never disagree."""
    seq_ok = isinstance(seq, int) and not isinstance(seq, bool)
    last_close = max(
        index["closed_ids_at"].get(cid, -1),
        index["closed_seqs_at"].get(seq, -1) if seq_ok else -1,
    )
    last_reopen = max(
        index["reopened_ids_at"].get(cid, -1),
        index["reopened_seqs_at"].get(seq, -1) if seq_ok else -1,
    )
    return last_close > last_reopen


def effective_kind(index: dict, target: dict) -> str:
    """Effective kind of a commitment given reclassification markers (latest
    marker wins over the captured data.kind; missing → promise forever)."""
    cid = _commitment_id(target)
    seq = target.get("seq")
    override = index["kind_by_id"].get(cid) or (
        index["kind_by_seq"].get(seq)
        if isinstance(seq, int) and not isinstance(seq, bool) else None
    )
    return override or commitment_kind(target)


def normalize_commitment_id(raw, index: dict) -> str:
    """Resolve any observed id spelling to the commitment's canonical id.

    Accepts: the canonical `data.id` (cmt_<ulid> or any explicit id), the
    synthesized `commitment_seq_<n>` fallback, and every legacy seq spelling —
    int `86`, `"86"`, `"seq_86"`, `"event_086"`, `"commitment_seq_86"`.
    Raises CommitmentIdError when nothing matches — an unmatched closure is a
    dead letter and MUST NOT be written.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise CommitmentIdError("empty commitment id")
    raw_s = str(raw).strip()

    # 1. Exact canonical-id match (covers cmt_<ulid>, explicit ids, and the
    #    synthesized commitment_seq_<n> form for id-less commitments).
    if raw_s in index["by_id"]:
        return raw_s

    # 2. Legacy seq spellings → the commitment event at that seq.
    m = _LEGACY_SEQ_ID_RE.match(raw_s)
    if m:
        ev = index["by_seq"].get(int(m.group(1)))
        if ev is not None:
            return _commitment_id(ev)

    raise CommitmentIdError(
        f"commitment id {raw!r} matches no commitment event (checked "
        f"{len(index['by_id'])} ids and {len(index['by_seq'])} seqs). "
        "Refusing to write an orphan tombstone — pass the commitment's "
        "data.id verbatim (widgets embed it; never re-derive or abbreviate)."
    )


def close_commitment(
    workspace_root,
    commitment_id,
    *,
    resolved_by: str,
    evidence: str,
    source_skill: str,
    resolution: str = "done",
    primary_thread_id: Optional[str] = None,
    user_confirmed: bool = False,
    extra_data: Optional[dict] = None,
) -> dict:
    """THE closure path (F2). Every closer — log-resolution, apply-choices,
    the workspace-manager catch-all, reconcile-sent, the Commitments
    orchestrator, meeting-notes / follow-up-ritual — closes through this
    function. Matching logic stays with the callers (cru_match Paths 1–5
    unchanged); only the event write lives here.

    Args:
      commitment_id: canonical data.id, or any legacy spelling (bare int,
        "86", "seq_86", "event_086", "commitment_seq_86") — normalized via
        seq lookup. Loud CommitmentIdError when nothing matches.
      resolved_by: person_id (or "sent_reconcile" for the silent task).
      evidence: ≤200-char human-readable reason (truncated here).
      resolution: done | dropped | superseded (S1's one closure vocabulary).
      primary_thread_id: defaults to the commitment event's own thread.
      user_confirmed: True ONLY for an explicit user action (✓ click, typed
        "mark done", one-click confirm). pending_review commitments refuse to
        close without it — no path may AUTO-resolve them (PendingReviewError).
      extra_data: optional additional data keys (e.g. Bug #51's
        resolved_via_wrapper_seq). Never overrides the canonical keys.

    Returns {"status": "closed", "commitment_id": <canonical>, "event": {...}}
    or {"status": "already_resolved", "commitment_id": <canonical>} (idempotent
    over the FULL resolved-id set — the last-200-lines window log-resolution
    used could re-close anything older than the tail).
    """
    if resolution not in VALID_RESOLUTIONS:
        raise ValueError(
            f"invalid resolution {resolution!r} (allowed: {VALID_RESOLUTIONS})"
        )
    from pathlib import Path as _Path
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"

    index = _scan_commitment_index(events_path)
    cid = normalize_commitment_id(commitment_id, index)
    target = index["by_id"][cid]

    # Order-aware, cross-keyed CURRENT state (Stage C seq-alias mirror +
    # Stage D reopen awareness): a closed-then-reopened commitment is open
    # again and MAY be re-closed.
    if _currently_closed(index, cid, target.get("seq")):
        return {"status": "already_resolved", "commitment_id": cid}

    if _is_pending_review(target) and not user_confirmed:
        raise PendingReviewError(
            f"commitment {cid!r} is pending_review — extraction flagged it as "
            "uncertain, so it may only close on an explicit user confirmation "
            "(pass user_confirmed=True from a user-initiated action). Surface "
            "it for review instead of auto-resolving."
        )

    data = dict(extra_data) if isinstance(extra_data, dict) else {}
    data.update({
        "commitment_id": cid,
        "resolved_by": resolved_by,
        "evidence": (evidence or "")[:200],
        "resolution": resolution,
    })
    ev = {
        "type": "commitment_resolved",
        "source_skill": source_skill,
        "primary_thread_id": (
            primary_thread_id
            if primary_thread_id is not None
            else target.get("primary_thread_id") or ""
        ),
        "data": data,
    }

    from event_gate import append_event
    append_event(events_path, [ev], holder=source_skill)
    return {"status": "closed", "commitment_id": cid, "event": ev}


def close_commitments(workspace_root, closures, *, source_skill: str) -> list[dict]:
    """Batch closure for callers that close several commitments in one run
    (reconcile-sent). Same contract as close_commitment per item; a
    CommitmentIdError or PendingReviewError on one item is recorded as
    {"status": "error", ...} and does NOT abort the rest (a bad id in a batch
    of real closes must not lose the real closes).
    """
    results: list[dict] = []
    for c in closures or []:
        try:
            results.append(close_commitment(
                workspace_root,
                c.get("commitment_id"),
                resolved_by=c.get("resolved_by") or "",
                evidence=c.get("evidence") or "",
                source_skill=source_skill,
                resolution=c.get("resolution") or "done",
                primary_thread_id=c.get("primary_thread_id"),
                user_confirmed=bool(c.get("user_confirmed")),
                extra_data=c.get("extra_data"),
            ))
        except (CommitmentIdError, PendingReviewError) as e:
            sys.stderr.write(
                f"[close_commitments] {type(e).__name__} for "
                f"{c.get('commitment_id')!r}: {e}\n"
            )
            results.append({
                "status": "error",
                "commitment_id": c.get("commitment_id"),
                "error": type(e).__name__,
                "detail": str(e),
            })
    return results


# -----------------------------------------------------------------------------
# Kind policy layer (Phase 2 Stage D — S4 undo, S5 task aging + promote)
# -----------------------------------------------------------------------------

TASK_STALE_DAYS = 30


def reopen_commitment(
    workspace_root,
    commitment_id,
    *,
    reopened_by: str,
    reason: str,
    source_skill: str,
) -> dict:
    """S4 undo: reopen a closed commitment ADDITIVELY — append a
    `commitment_reopened` event; the tombstone stays in history and the
    projector honors whichever came last. Same id normalization + loud
    no-match as close_commitment. A later re-close works normally.
    """
    from pathlib import Path as _Path
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    index = _scan_commitment_index(events_path)
    cid = normalize_commitment_id(commitment_id, index)
    target = index["by_id"][cid]
    if not _currently_closed(index, cid, target.get("seq")):
        return {"status": "already_open", "commitment_id": cid}
    ev = {
        "type": "commitment_reopened",
        "source_skill": source_skill,
        "primary_thread_id": target.get("primary_thread_id") or "",
        "data": {
            "commitment_id": cid,
            "reopened_by": reopened_by,
            "reason": (reason or "")[:200],
        },
    }
    from event_gate import append_event
    append_event(events_path, [ev], holder=source_skill)
    return {"status": "reopened", "commitment_id": cid, "event": ev}


def promote_task_to_commitment(
    workspace_root,
    commitment_id,
    *,
    source_skill: str,
    reason: str = "counterparty appeared — promoted from task",
    new_kind: str = "promise",
) -> dict:
    """S5 one-tap promote: a task gains a counterparty → it becomes a real
    commitment. The flip is a LABEL CHANGE via an additive
    `commitment_reclassified` marker — never delete/recreate (ratified
    condition, §3.1). The projector applies the override read-side; the next
    CRU pass sees it as eligible.
    """
    if new_kind not in KIND_VALUES_SAFE:
        raise ValueError(f"invalid new_kind {new_kind!r} (allowed: {sorted(KIND_VALUES_SAFE)})")
    from pathlib import Path as _Path
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    index = _scan_commitment_index(events_path)
    cid = normalize_commitment_id(commitment_id, index)
    target = index["by_id"][cid]
    if effective_kind(index, target) == new_kind:
        return {"status": "already_" + new_kind, "commitment_id": cid}
    data = {
        "target_id": cid,
        "new_kind": new_kind,
        "reason": (reason or "")[:200],
    }
    if isinstance(target.get("seq"), int):
        data["target_seq"] = target["seq"]
    ev = {
        "type": "commitment_reclassified",
        "source_skill": source_skill,
        "primary_thread_id": target.get("primary_thread_id") or "",
        "data": data,
    }
    from event_gate import append_event
    append_event(events_path, [ev], holder=source_skill)
    return {"status": "reclassified", "commitment_id": cid, "event": ev}


def stale_tasks(
    open_commitments: list[dict],
    now_iso: str,
    *,
    days: int = TASK_STALE_DAYS,
) -> list[dict]:
    """S5 task aging policy (code-enforced): open TASK-kind items captured
    ≥ `days` ago. These are swept into the Friday triage as "still on your
    plate?" — they are NEVER rendered in commitment aging and NEVER chased by
    reconcile-sent/CRU (cru_match.cru_eligible excludes task kind at the
    matcher layer). Pass the PROJECTED open set so reclassification markers
    are already applied."""
    from event_time import event_time
    now = _parse_date(now_iso)
    out: list[dict] = []
    for ev in open_commitments:
        if commitment_kind(ev) != "task":
            continue
        captured = _parse_date(event_time(ev))
        if captured is None or now is None:
            continue
        if (now - captured).days >= days:
            out.append(ev)
    return out


# Late import guard: KIND_VALUES lives in event_types (the enum home); keep a
# module-local safe alias so promote validation never hard-fails on import
# order.
try:
    from event_types import KIND_VALUES as KIND_VALUES_SAFE
except Exception:  # pragma: no cover
    KIND_VALUES_SAFE = frozenset({"promise", "task", "scheduling", "agenda"})


__all__ = [
    "RECENT_ACTIVITY_WINDOW_DAYS",
    "RECONCILE_STALE_DAYS",
    "KIND_DEFAULT",
    "TASK_STALE_DAYS",
    "VALID_RESOLUTIONS",
    "CommitmentIdError",
    "PendingReviewError",
    "effective_kind",
    "reopen_commitment",
    "promote_task_to_commitment",
    "stale_tasks",
    "commitment_kind",
    "load_open_commitments",
    "is_overdue",
    "reconcile_is_stale",
    "count_commitments",
    "commitment_counts",
    "compute_brief_state",
    "compute_and_log_brief_state",
    "latest_brief_state_event",
    "normalize_commitment_id",
    "close_commitment",
    "close_commitments",
]
