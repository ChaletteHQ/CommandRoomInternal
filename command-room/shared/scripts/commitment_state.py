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
   orchestrators, value surfaces — MUST call one of them. The
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
import re
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
        partition_subitems,
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
        partition_subitems,
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


def _age_in_days(ts_value: Optional[str], now_iso: str):
    """Whole days between a capture's ts and now, or None when either end is
    unparseable. Date-granular on purpose: the brief's lane ranks by age band,
    and an hours-precise age would reorder the list between two fires on the
    same day for no reason a reader could see."""
    when = _parse_date(ts_value)
    today = _parse_date(now_iso)
    if when is None or today is None:
        return None
    return max(0, (today - when).days)


def commitment_kind(ev: dict) -> str:
    """Read a commitment event's `data.kind`, defaulting missing/empty to
    `promise` (pre-Phase-1 events carry no kind; they are promises)."""
    d = ev.get("data") or {}
    kind = d.get("kind")
    return kind if isinstance(kind, str) and kind else KIND_DEFAULT


def later_route(ev: dict, user_person_id) -> str:
    """t3 FB-3 (M ruling 2026-07-16): where a row's 'Later…' click lands.

    Returns "defer" when the item is the user's own (owner_id == the primary
    user — a due-date shift is meaningful, dispatch writes commitment_updated
    with data.new_due) and "snooze" for everything else (owed-to-you /
    unowned / visibility-only — shifting a date the counterparty owns would
    rewrite THEIR commitment from a view-management click; the truthful
    action is a chat_dismissal carrying data.snooze_until via the mute
    ledger: the item stays open and simply stops rendering until the date).

    Same ownership test count_commitments uses for the you-owe bucket. An
    unresolvable primary user (None) degrades to "snooze" — never mutate a
    due date on an item we can't prove is the user's own.
    """
    owner = _commitment_field(ev, "owner_id")
    if owner and user_person_id and owner == user_person_id:
        return "defer"
    return "snooze"


def parse_later_when(text, now_iso: str):
    """Deterministic slice of the 'Later…' when-input (t3 FB-3): a bare
    number of days ("5", "5 days", "5d") or an ISO date ("2026-08-01",
    full timestamps accepted). Returns the target date as an ISO date
    string, or None when the text needs the orchestrator's natural-language
    date parsing ("friday", "next week") — None is a hand-off, not an error.

    A number parses as now + N days; 0 and negatives are rejected (None) so
    a typo like "-3" falls through to the NL layer's clearer error surface.
    """
    if not text or not isinstance(text, str):
        return None
    today = _parse_date(now_iso)
    if today is None:
        return None
    s = text.strip().lower()
    m = re.fullmatch(r"(\d{1,3})\s*(?:d|day|days)?", s)
    if m:
        days = int(m.group(1))
        if days <= 0:
            return None
        return (today + datetime.timedelta(days=days)).isoformat()
    d = _parse_date(text.strip())
    if d is not None:
        return d.isoformat()
    return None


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
    movement: Optional[dict] = None,
) -> dict:
    """Canonical commitment counts over an already-loaded open set (pure).

    THE one place the open/overdue/undated/by-direction math lives. Every
    number a surface renders about open commitments comes from this function —
    directly, via `commitment_counts(workspace_root)`, or via
    `compute_brief_state(...)["counts"]` (which delegates here).

    INTAKE (2026-07-31) — pending_review items are QUEUE MEMBERS, not open
    commitments. A `data.pending_review` item is an UNCONFIRMED extraction:
    the extractor guessed, nobody has agreed it is real work, and
    cru_match._is_pending_review already bars it from auto-close and chase.
    So it counts in ONE place — `unconfirmed`, the pointer to the
    needs-your-call queue — and in no other tally here: not `total`, not the
    direction buckets, not overdue/undated/by_kind, not stuck/blocked.
    Confirming one (commitment_state.clear_review_flags /
    confirm_commitment_owner) moves it into the ordinary numbers with no
    other change; dropping one closes it with resolution=dropped. This is a
    DELIBERATE repoint of `total`: before it, an extractor could inflate the
    user's open book by guessing.

    Keys:
      total     — count of NON-pending top-level items: the open book the
                  user is actually carrying. The canonical headline every
                  surface reports (Bug #85 / A85: never you_owe + they_owe,
                  never a confidence- or staleness-filtered subset — the
                  pending exclusion is the one carve-out, and it has its own
                  visible counter).
      you_owe   — owner is the primary user (direction: user owes).
      they_owe  — owner is someone else (direction: owed to the user).
      unowned   — no resolvable owner_id (extraction gap; still open, still in
                  total — omitting these was the v3.18.4 A85 16-vs-18 split).
      overdue   — overdue per the EFFECTIVE due (the loader has already folded
                  `commitment_updated` deferrals, so a pushed item is not
                  overdue). 0 when now_iso is not supplied.
      stuck     — DEPRECATED alias of `overdue`, kept for readers written
                  before v4.5.2 R1b. The number was always overdue-by-due-date;
                  "stuck" was a false label (the printed caption promised a
                  no-movement/blocked metric the system never computed).
                  Never render the word "stuck" for THIS number; render
                  "overdue". The REAL stuck metric is headline["stuck"]
                  (v4.6.0 MC2, below) — this alias's value deliberately does
                  NOT change meaning (extend, don't repoint: pre-R1b readers
                  still get the number they always got).
      undated   — no parseable due date at all (S2 target: < 30% of open).
      by_kind   — open count per data.kind (missing kind reads as `promise`).
      headline  — THE four headline buckets + overdue (v4.5.2 R4 — the one
                  bucket export; F-47 P2b / F-56). Every surface that renders
                  bucket numbers (morning brief, the daily Commitments chat,
                  commitment-triage) renders THESE, verbatim:
                    you_owe / owed_to_you / unowned — CONFIRMED items only
                      (pending_review excluded from all three);
                    unconfirmed — pending_review items, their own line
                      (per the W4b design: unconfirmed items are not owned
                      yet — folding them into a direction is how one day
                      produced four different open counts). INTAKE: this is
                      a POINTER count into the needs-your-call queue, NOT a
                      slice of `total`;
                    needs_review — alias of `unconfirmed`, for readers that
                      name the queue rather than the flag;
                    overdue — same as top-level `overdue` (confirmed set);
                    total — same as top-level `total` (confirmed set).
                  Invariant (INTAKE): you_owe + owed_to_you + unowned
                  == total, and `unconfirmed` sits OUTSIDE that partition
                  (pre-INTAKE it was the fourth term). No surface may
                  re-derive its own buckets or fold unowned/unconfirmed into
                  a direction.
                  v4.6.0 MC2 extends the export (existing keys untouched)
                  with the REAL stuck metric — present ONLY when the caller
                  supplies `movement` (a derive_commitment_movement map) and
                  now_iso:
                    stuck   — open items with no movement for 21+ days OR
                              blocked on a named person (commitment_activity.
                              classify_commitments — THE one derivation);
                    blocked — the subset whose newest movement is an
                              unanswered outbound chase to a named person
                              (blocked ⊆ stuck).
                  When `movement` is not supplied the keys are ABSENT (not
                  0) — "not computed" must never render as "none stuck".

    `movement` — optional {commitment_id: CommitmentMovement} from
    commitment_activity.derive_commitment_movement. Pure callers derive it
    once per fire and pass it; the commitment_counts(workspace_root) wrapper
    derives it automatically.

    `user_person_id=None` (unresolvable primary user) degrades safely: nothing
    matches the user, so you_owe=0 and every owned commitment counts as
    they_owe — total/overdue/undated/by_kind stay exact.

    SUB1 D2 (M ruling 2026-07-16) — a parent with 3 open sub-items counts as
    **1**, not 4: the supplied open set is partitioned via
    cru_match.partition_subitems into TOP-LEVEL items (no live parent link in
    the same set — orphan children count here; they are real open work) and
    SUB-ITEMS, and every number above — total, the four headline buckets,
    overdue, undated, by_kind, stuck/blocked — is computed over the
    TOP-LEVEL partition only. The invariant holds over that partition. This
    is NOT a repoint of a shipped number: every existing workspace has zero
    sub-items, so every existing output is byte-identical (the MC1
    vacuous-safety argument). Counting children in `total` would punish the
    user for planning (decomposing one item jumps 12 → 15 and the headline
    lies about how many real-world promises exist — the W4c volume class).
    Two ADDITIVE headline keys when (and only when) sub-items exist:
      subitems_open                 — open sub-items in the supplied set;
      subitems_done_of_open_parents — closed children of still-open parents
                                      (from the loader's n_subitems_done
                                      stamp; 0 for raw unprojected input).
    ABSENT when the workspace has no sub-items — the MC2 "absent, never a
    guessed 0" rule.
    """
    from cru_match import partition_subitems
    top_level, sub_items = partition_subitems(open_commitments)
    you_owe = they_owe = unowned = overdue = undated = 0
    unconfirmed = 0
    by_kind: dict[str, int] = {}
    # INTAKE — a pending_review item is a QUEUE MEMBER, not an open
    # commitment. It increments `unconfirmed` (the pointer count) and is
    # skipped from every other tally: direction buckets, overdue, undated,
    # by_kind, and the stuck/blocked classification input below. An
    # extractor's guess must not inflate a number the user reads as "how
    # many promises am I carrying".
    confirmed_top: list[dict] = []
    for ev in top_level:
        if _is_pending_review(ev):
            unconfirmed += 1
            continue
        confirmed_top.append(ev)
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
            overdue += 1
        kind = commitment_kind(ev)
        by_kind[kind] = by_kind.get(kind, 0) + 1
    headline = {
        "total": len(confirmed_top),
        "you_owe": you_owe,
        "owed_to_you": they_owe,
        "unowned": unowned,
        "unconfirmed": unconfirmed,
        # Alias for readers that name the queue rather than the flag.
        "needs_review": unconfirmed,
        "overdue": overdue,
    }
    if movement is not None and now_iso:
        # v4.6.0 MC2 — the real stuck/blocked metric, from THE one derivation
        # (F-54 cross-surface-split rule: no surface computes its own).
        # SUB1: classified over top-level items only — a child never renders
        # as its own stuck row (its activity bubbles to the parent).
        # INTAKE: classified over the CONFIRMED top-level partition — an
        # unconfirmed extraction cannot be "stuck"; nobody has agreed it is
        # work yet.
        from commitment_activity import classify_commitments
        cls = classify_commitments(confirmed_top, movement, now_iso)
        headline["stuck"] = len(cls["stuck"])
        headline["blocked"] = len(cls["blocked"])
    # SUB1 D2 — the two ADDITIVE keys, present only when sub-items exist
    # (absent, never a guessed 0): open children in the supplied set + closed
    # children of still-open parents (the loader's n_subitems_done stamp).
    subitems_done = 0
    for ev in top_level:
        n = (ev.get("data") or {}).get("n_subitems_done")
        if isinstance(n, int) and not isinstance(n, bool):
            subitems_done += n
    if sub_items or subitems_done:
        headline["subitems_open"] = len(sub_items)
        headline["subitems_done_of_open_parents"] = subitems_done
    return {
        # Canonical open-commitment total == len(top_level): one real-world
        # promise per line (SUB1 D2 — sub-items are steps of a top-level
        # item, never additional promises; zero sub-items → byte-identical
        # to the pre-SUB1 len(open_commitments)). Both the brief header and
        # the coach MUST report THIS number (Bug #85 + the A85 followup).
        # you_owe + they_owe alone drops ownerless items.
        # INTAKE: pending_review items are NOT in this total — they are the
        # needs-your-call queue, counted separately as headline.unconfirmed.
        "total": len(confirmed_top),
        "you_owe": you_owe,
        "they_owe": they_owe,
        "unowned": unowned,
        "overdue": overdue,
        "stuck": overdue,  # deprecated alias — see docstring; never render as "stuck"
        "undated": undated,
        "by_kind": by_kind,
        "headline": headline,
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

    v4.6.0 MC2 — derives the movement map itself (it has the workspace), so
    the returned headline carries the real stuck/blocked numbers. Both scans
    are memoized per file state; the derivation never blocks the counts — a
    movement-scan failure degrades to a headline WITHOUT stuck/blocked
    (absent, never a guessed 0).
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
    movement = None
    try:
        from commitment_activity import derive_commitment_movement
        movement = derive_commitment_movement(events_path)
    except Exception:
        movement = None
    return count_commitments(
        opens, user_person_id=user_person_id, now_iso=now_iso, movement=movement
    )


# -----------------------------------------------------------------------------
# Meeting-linked commitments (v4.5.2 C1 — the F-44 visibility fix)
# -----------------------------------------------------------------------------
#
# F-44: the 7 items the Jul 7 sweep recovered — two of them about the NEXT
# MORNING's 9:15 — appeared nowhere in that morning's brief or chase, because
# every ranking bucket keys on the due date (overdue / due-near / aging) and
# the captures were undated with empty counterparty fields. Relevance to
# today's meetings is a ranking signal of its own: an open commitment whose
# counterparty is in the room, or whose text names someone in the room, is
# surfaced regardless of due date, kind, or thread activity.


def _name_tokens(name: Optional[str]) -> list[str]:
    """Lowercase word tokens of a display name / free text, possessives and
    punctuation stripped ("Bo's" -> "bo")."""
    if not name or not isinstance(name, str):
        return []
    out = []
    for raw in name.lower().replace("'s ", " ").split():
        tok = "".join(c for c in raw if c.isalpha())
        if tok.endswith("s") and raw.endswith("'s"):
            tok = tok[:-1]
        if tok:
            out.append(tok)
    return out


def _one_edit_apart(a: str, b: str) -> bool:
    """True iff strings are equal or one insert/delete/substitute apart —
    catches transcript-spelling drift like "Skylar" vs the resolved
    "Skyler" (the exact F-44 item) without fuzzy-matching short names."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = j = edits = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if la == lb:
            i += 1
        j += 1
    return True


def _tokens_match(a: str, b: str) -> bool:
    """Name-token equality with single-edit tolerance for tokens long enough
    that a typo/ASR variant can't collide with a different real name (>= 5
    chars both). Short names ("Bo", "Rio") must match exactly."""
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) >= 5 and len(b) >= 5 and _one_edit_apart(a, b)


def _commitment_match_text(ev: dict) -> str:
    """The text a name-mention is looked for in: title + summary +
    free-text counterparty/owner names (all shapes via _commitment_field)."""
    d = ev.get("data") or {}
    parts = [
        _commitment_field(ev, "title") or "",
        d.get("summary") or "",
        d.get("counterparty_name") or "",
        d.get("owner_external") or "",
    ]
    return " ".join(p for p in parts if p)


def match_commitments_to_meetings(
    open_commitments: list[dict],
    meetings: Iterable[dict],
    *,
    user_person_id: Optional[str] = None,
) -> list[dict]:
    """Match open commitments to today's meetings by counterparty OR
    name-mention (v4.5.2 C1 / F-44). Pure — no I/O.

    `meetings`: iterable of {"meeting_id", "title", "attendee_person_ids":
    [...], "attendee_names": [...]}. The caller builds these from today's
    calendar pull, resolving attendee emails to person_ids and expanding
    attendee display names with their alias spellings from aliases.json —
    the matcher itself does no entity resolution.

    A commitment links to a meeting when EITHER:
      - counterparty: its counterparty_id OR owner_id is an attendee
        (the primary user is ignored — they attend their own meetings), or
      - name_mention: an attendee's name appears in the commitment's
        title/summary/free-text name fields (full-name substring, or
        given-name token match with single-edit tolerance for >= 5-char
        tokens — "Skylar" in a sweep summary matches attendee "Skyler").

    Deliberately applies NO due-date, kind, pending_review, or activity
    filter: a missing due date must not make a meeting-relevant item
    invisible on the day of the meeting (F-44's failure). Consumers render
    pending_review items as needing confirmation, not as confirmed chase.

    Returns one row per (commitment, first matching meeting):
      {commitment_id, title, kind, due, owner_id, counterparty_id,
       pending_review, meeting_id, meeting_title, match, matched_name}
    """
    meeting_list = []
    for m in meetings or []:
        att_ids = {
            pid for pid in (m.get("attendee_person_ids") or [])
            if pid and pid != user_person_id
        }
        att_names = []
        for n in (m.get("attendee_names") or []):
            toks = _name_tokens(n)
            if toks:
                att_names.append((n, toks))
        meeting_list.append((m, att_ids, att_names))
    if not meeting_list:
        return []

    from commitment_parties import counterparty_ids as _cp_ids
    out: list[dict] = []
    for ev in open_commitments:
        # MC1: the FULL counterparty roster (legacy single + list) — a
        # meeting is relevant when ANY of a multi-counterparty commitment's
        # people is in the room, not just the first.
        cp_ids = _cp_ids(ev)
        owner_id = _commitment_field(ev, "owner_id")
        text = _commitment_match_text(ev).lower()
        text_tokens = _name_tokens(text)

        for m, att_ids, att_names in meeting_list:
            match = None
            matched_name = None
            cp_hit = next((c for c in cp_ids if c in att_ids), None)
            if cp_hit or (owner_id and owner_id in att_ids):
                match = "counterparty"
                matched_name = cp_hit or owner_id
            else:
                for display, toks in att_names:
                    full = " ".join(toks)
                    if full and full in " ".join(text_tokens):
                        match, matched_name = "name_mention", display
                        break
                    # Given-name token match (first token of the attendee name)
                    # against every token of the commitment text.
                    given = toks[0]
                    if any(_tokens_match(given, t) for t in text_tokens):
                        match, matched_name = "name_mention", display
                        break
            if match:
                row = {
                    "commitment_id": _commitment_id(ev),
                    "title": _commitment_field(ev, "title") or "",
                    "kind": commitment_kind(ev),
                    "due": _commitment_field(ev, "due"),
                    "owner_id": owner_id,
                    "counterparty_id": (cp_ids[0] if cp_ids else None),
                    "counterparty_ids": cp_ids,
                    "pending_review": _is_pending_review(ev),
                    "meeting_id": m.get("meeting_id"),
                    "meeting_title": m.get("title") or "",
                    "match": match,
                    "matched_name": matched_name,
                }
                # SUB1 D5 — meeting-match deliberately KEEPS seeing children
                # (F-44: a step relevant to today's meeting must surface on
                # the day of the meeting); the row carries the parent link so
                # renderers show "part of: [parent]".
                d_ev = ev.get("data") or {}
                if isinstance(d_ev.get("parent_id"), str) and d_ev.get("parent_id"):
                    row["parent_id"] = d_ev["parent_id"]
                    if d_ev.get("parent_title"):
                        row["parent_title"] = d_ev["parent_title"]
                out.append(row)
                break  # first matching meeting wins; one row per commitment
    return out


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
    todays_meetings: Optional[Iterable[dict]] = None,
    commitment_movement: Optional[dict] = None,
    workspace_root=None,
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
      todays_meetings: optional iterable of today's meetings
        ({"meeting_id", "title", "attendee_person_ids", "attendee_names"} —
        see match_commitments_to_meetings). When supplied, the returned
        state carries `meeting_linked`: open commitments relevant to today's
        meetings by counterparty or name-mention, REGARDLESS of due date,
        kind, or thread activity (v4.5.2 C1 / F-44 — an undated sweep
        capture about today's 9:15 must not be invisible on the day of the
        9:15). Both directions; the needs_attention drop rules deliberately
        do NOT apply to this list.
      commitment_movement: optional {commitment_id: CommitmentMovement} from
        commitment_activity.derive_commitment_movement (v4.6.0 MC2). When
        supplied, counts["headline"] carries the real stuck/blocked numbers.
        compute_and_log_brief_state derives it automatically from the
        workspace; direct callers of THIS pure function pass it themselves.
      workspace_root: optional workspace path, forwarded to Path 5
        (`match_calendar_to_commitments`) for F-28 — the roster reader needs
        the entity graph to see that one person written as BOTH a
        `counterparty_id` and that person's free-text name is ONE
        counterparty, not two. Without it Path 5's MC1 downgrade carries a
        parameter nothing fills (the post-review F-1 dead-rail finding). This
        function still does no I/O of its own. `None` is byte-identically
        pre-F-28.

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
        "meeting_linked": match_commitments_to_meetings(...) rows ([] when
            todays_meetings not supplied),
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
        open_commitments, user_person_id=user_person_id, now_iso=now_iso,
        movement=commitment_movement,
    )

    # SUB1 D2/D5 — needs_attention iterates TOP-LEVEL you-owe items only: a
    # parent row carries its progress; a child never renders as its own brief
    # line (orphan children partition top-level and surface normally).
    from cru_match import partition_subitems
    top_level_commitments, _sub_items = partition_subitems(open_commitments)
    you_owe_commitments: list[dict] = [
        ev for ev in top_level_commitments
        if _commitment_field(ev, "owner_id") == user_person_id
        # INTAKE — an unconfirmed extraction is a queue member, not work the
        # user owes: it never enters needs_attention and never reaches the
        # calendar matcher (which could otherwise auto-resolve a guess).
        and not _is_pending_review(ev)
    ]

    # Calendar-action drops: one batch call over all you-owe commitments.
    calendar_resolved_ids: set[str] = set()
    if calendar_events:
        cal_results = match_calendar_to_commitments(
            open_commitments=you_owe_commitments,
            user_person_id=user_person_id,
            calendar_events=calendar_events,
            # F-28 — the entity graph is what tells Path 5's MC1 downgrade that
            # one person written as an id AND that person's name is one
            # counterparty. Absent → the raw union, pre-F-28.
            workspace_root=workspace_root,
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
        row = {
            "commitment_id": cid,
            "title": _commitment_field(ev, "title") or "",
            "owner_id": user_person_id,
            "thread_id": thread_id,
            "due": due,
            "overdue": is_overdue(due, now_iso),
            # CAPTUREFLOW §D — the brief's lane ranks due-then-age, so the
            # capture's own ts has to ride the row. Additive, and deliberately
            # the RAW ts rather than a derived age: a now-relative number on a
            # substrate-shaped row is a date bomb (G14's class) the moment
            # anything goldens or caches it. `cap_needs_attention` derives the
            # age itself, from this value and its own `now_iso`.
            "captured_ts": ev.get("ts") or "",
            # When True the brief MUST soften this item (you may have already sent
            # the email that closes it) rather than telling the CEO to redo it.
            "reconcile_stale": reconcile_stale,
        }
        # SUB1 D5 — parent progress rides the row ("2 of 3 sub-items done ·
        # next: …"); keys present only when the item HAS sub-items (the MC2
        # absent-not-0 rule). Values come from the loader's stamps verbatim.
        d_ev = ev.get("data") or {}
        if isinstance(d_ev.get("n_subitems_open"), int) or isinstance(
                d_ev.get("n_subitems_done"), int):
            row["n_subitems_open"] = d_ev.get("n_subitems_open") or 0
            row["n_subitems_done"] = d_ev.get("n_subitems_done") or 0
            if d_ev.get("next_subitem_due"):
                row["next_subitem_due"] = d_ev["next_subitem_due"]
            if d_ev.get("all_subitems_resolved"):
                row["all_subitems_resolved"] = True
        needs_attention.append(row)

    # Meeting relevance is its own ranking signal (F-44): matched over the
    # FULL open set (both directions), with none of the needs_attention
    # drops — an item about a meeting happening today is surfaced even when
    # undated, task-kind, pending_review, or on a recently-active thread.
    meeting_linked = match_commitments_to_meetings(
        open_commitments, todays_meetings or [], user_person_id=user_person_id
    )

    return {
        "counts": counts,
        "needs_attention": needs_attention,
        "dropped": dropped,
        "meeting_linked": meeting_linked,
        # True iff sent-mail reconciliation is behind (cursor stale/absent). The
        # brief reads this to soften you-owe items instead of telling the CEO to
        # redo work they may have already completed by an unreconciled send.
        "reconcile_stale": reconcile_stale,
    }


# ---------------------------------------------------------------------------
# CAPTUREFLOW §D — the morning brief's needs-attention lane, capped
# ---------------------------------------------------------------------------
#
# The lane was unbounded: 72 rows on the audited workspace, in a surface whose
# whole job is to be readable before coffee. `compute_brief_state` still
# returns the FULL list — the header counts stay unfiltered (the :299 doctrine:
# a cap is a render bound, never a silence, and a count that quietly shrinks is
# its own dishonesty) — and this is the render bound the brief applies on top.

BRIEF_ATTENTION_CAP = 5          # M's ruling, CAPTUREFLOW §0.5 #4
BRIEF_ROTATION_AGE_DAYS = 14     # nothing older than this can stay unseen


def attention_age_days(row: dict, now_iso: str):
    """A lane row's age in whole days, from its `captured_ts`. None when the
    row carries no parseable ts — an unknown age sorts last rather than
    pretending to be brand new, and never rotates."""
    return _age_in_days(row.get("captured_ts"), now_iso)


def _attention_rank(row: dict) -> tuple:
    """Due-then-age. Dated items lead, earliest due first; undated items
    follow, oldest capture first; an unknown capture time sorts last inside
    its band. Pure in the row alone — no clock — so the ORDER of a lane is
    stable across a day and two fires cannot disagree about it."""
    due = _parse_date(row.get("due"))
    captured = _parse_date(row.get("captured_ts"))
    return (
        0 if due is not None else 1,
        due.toordinal() if due is not None else 0,
        0 if captured is not None else 1,
        captured.toordinal() if captured is not None else 0,
        str(row.get("commitment_id") or ""),
    )


def cap_needs_attention(rows, *, cap: int = BRIEF_ATTENTION_CAP,
                        now_iso: str = "",
                        rotation_age_days: int = BRIEF_ROTATION_AGE_DAYS
                        ) -> dict:
    """Rank the needs-attention lane and bound it at `cap`.

    Returns `{"shown": [...], "n_total": int, "n_more": int,
              "more_line": str, "rotated_in": str|None}`.

    THE ROTATION RULE — no item can be suppressed forever. Ranking alone would
    park a low-priority row below the fold permanently: it never ages into the
    top five because five other things always outrank it, and a lane that
    silently never shows a real promise is the same defect as no lane at all.
    So whenever the overflow contains items older than `rotation_age_days`, ONE
    of them takes the last slot, chosen round-robin by the DATE of the fire
    (`now_iso`'s ordinal modulo the aged set). Deterministic — the same day
    yields the same choice, so a re-fire is not a reshuffle — and every aged
    item comes up within `len(aged)` days.

    No new state: the rotation is a pure function of the day and the current
    overflow, which is what keeps it honest across machines (a per-machine
    "last shown" ledger would rotate differently on M's laptop than on his
    desktop). Pure — no I/O."""
    ranked = sorted(list(rows or []), key=_attention_rank)
    cap = max(0, int(cap))
    n_total = len(ranked)
    if n_total <= cap:
        return {"shown": ranked, "n_total": n_total, "n_more": 0,
                "more_line": "", "rotated_in": None}

    shown = ranked[:cap]
    overflow = ranked[cap:]
    rotated_in = None
    if cap > 0:
        aged = []
        for r in overflow:
            age = attention_age_days(r, now_iso)
            if isinstance(age, int) and age >= int(rotation_age_days):
                aged.append(r)
        if aged:
            today = _parse_date(now_iso)
            pick = aged[(today.toordinal() if today else 0) % len(aged)]
            shown = shown[:cap - 1] + [pick]
            overflow = [r for r in ranked[cap:] if r is not pick]
            overflow.insert(0, ranked[cap - 1])
            rotated_in = str(pick.get("commitment_id") or "")

    n_more = n_total - len(shown)
    noun = "one" if n_more == 1 else "more"
    return {
        "shown": shown,
        "n_total": n_total,
        "n_more": n_more,
        # 'commitment triage' is the registered trigger; a bare "triage" routes
        # to inbox-triage, which is a different surface entirely.
        "more_line": (f"…and {n_more} {noun} — say `commitment triage` for "
                      f"the full list."),
        "rotated_in": rotated_in,
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

    v4.6.0 MC2 — derives the commitment movement map from the workspace when
    the caller didn't pass one, so the brief's headline carries the real
    stuck/blocked numbers with no extra orchestrator step. A derivation
    failure degrades to a headline without the keys — never a guessed 0,
    never a blocked brief.
    """
    if "commitment_movement" not in kwargs:
        try:
            from pathlib import Path as _Path
            from commitment_activity import derive_commitment_movement
            kwargs["commitment_movement"] = derive_commitment_movement(
                _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
            )
        except Exception:
            pass
    # F-28 — this wrapper HOLDS the workspace, so Path 5's roster fix reaches
    # the brief/commitments fires rather than sitting behind a parameter nothing
    # fills. `setdefault`, so an explicit caller value still wins.
    kwargs.setdefault("workspace_root", workspace_root)
    state = compute_brief_state(**kwargs)
    try:
        from pathlib import Path as _Path
        from atomic_write import atomic_append_jsonl as _append
        events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        # seq/ts are auto-stamped inside the writer lock; the Phase 1 gate
        # validates the type (brief_state is registered in events.schema.json).
        # by_kind is dropped from the audit payload to keep the event small;
        # "stuck" is dropped because it is a deprecated alias of "overdue"
        # (v4.5.2 R1b) — new events never carry the false label.
        _append(events_path, [{
            "type": "brief_state",
            "source_skill": source_skill,
            "data": {
                "counts": {k: v for k, v in state["counts"].items()
                           if k not in ("by_kind", "stuck")},
                "n_needs_attention": len(state["needs_attention"]),
                "n_meeting_linked": len(state.get("meeting_linked") or []),
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
    from cru_match import load_events_defensively
    from pathlib import Path as _Path
    p = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not p.exists():
        return None
    latest = None
    # EVGUARD — was a hand-rolled loop whose `except Exception` wrapped only
    # the parse, so a bare-string line reached `e.get()` and raised
    # AttributeError out of the function (Sub-bug #14b, second half). The
    # canonical loader skips both malformed shapes; since_ts=None = full history.
    events, _skipped = load_events_defensively(p, since_ts=None)
    for e in events:
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
# commitment event at seq N". Canonical definition lives in event_types
# (v4.5.2 R1c — the write gate rejects explicit commitment ids that collide
# with this namespace, so read-side resolution and write-side validation
# can never drift apart); the local fallback keeps import order safe.
try:
    from event_types import LEGACY_SEQ_ID_RE as _LEGACY_SEQ_ID_RE
except Exception:  # pragma: no cover
    _LEGACY_SEQ_ID_RE = _re.compile(r"^(?:commitment_seq_|event_|seq_)?0*(\d+)$")


class CommitmentIdError(ValueError):
    """A closure referenced an id that matches no commitment. Fail loud —
    writing the tombstone anyway is how 74 orphan closures happened."""


class PendingReviewError(ValueError):
    """Refused to auto-resolve a pending_review commitment. Extraction flagged
    it as uncertain; only an explicit user confirmation may close it
    (deep-audit 2026-05-29 finding #9 / F2)."""


class OpenSubitemsError(ValueError):
    """Refused to close a parent commitment that still has open sub-items
    (SUB1 D3). A silent close would orphan the children; the caller must
    either close/drop the children first or pass close_subitems=True from an
    explicit user confirmation ("this also closes its N open sub-items")."""


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
      superseded_onto: superseded cid → survivor cid, from non-split
                     `commitment_superseded` events (SUB1 D3b: children of a
                     merged-away parent belong to the SURVIVOR read-side; the
                     writer honors the same re-point when it looks for a
                     parent's open children)
      children_of:   raw on-disk data.parent_id → [child commitment events]
                     (SUB1 — resolve through superseded_onto at query time
                     via `_live_children`)
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
    superseded_onto: dict[str, str] = {}
    children_of: dict[str, list] = {}
    p = _Path(events_jsonl_path)
    if not p.exists():
        return {"by_id": by_id, "by_seq": by_seq,
                "closed_ids_at": closed_ids_at, "closed_seqs_at": closed_seqs_at,
                "reopened_ids_at": reopened_ids_at,
                "reopened_seqs_at": reopened_seqs_at,
                "kind_by_id": kind_by_id, "kind_by_seq": kind_by_seq,
                "superseded_onto": superseded_onto, "children_of": children_of}
    events, _skipped = load_events_defensively(p)
    for idx, ev in enumerate(events):
        et = ev.get("type") or ev.get("event") or ""
        d = ev.get("data") or {}
        if et == "commitment":
            by_id[_commitment_id(ev)] = ev
            seq = ev.get("seq")
            if isinstance(seq, int):
                by_seq[seq] = ev
            pid = d.get("parent_id")
            if isinstance(pid, str) and pid.strip():
                children_of.setdefault(pid.strip(), []).append(ev)
        elif et in ("commitment_resolved", "thread_resolved", "commitment_superseded"):
            cid = _closer_target_id(ev)
            if cid:
                closed_ids_at[str(cid)] = idx
            for s in _closer_target_seqs(ev):
                closed_seqs_at[s] = idx
            # SUB1 D3b — merge re-point map (mirrors the loader's merged_onto
            # fold): a non-split supersession transfers the closed parent's
            # children to the survivor read-side. Split closers stay skipped.
            if et == "commitment_superseded" and not d.get("split_into"):
                survivor = d.get("superseded_by") or d.get("survivor_id")
                if cid and survivor:
                    superseded_onto[str(cid)] = str(survivor)
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
            "kind_by_id": kind_by_id, "kind_by_seq": kind_by_seq,
            "superseded_onto": superseded_onto, "children_of": children_of}


def _resolve_survivor(superseded_onto: dict, cid: str) -> str:
    """Follow the merge re-point chain (superseded → survivor) to its live
    end, cycle-safe. Identity when the id was never superseded (SUB1 D3b)."""
    seen: set = set()
    while cid in superseded_onto and cid not in seen:
        seen.add(cid)
        cid = superseded_onto[cid]
    return cid


def _live_children(index: dict, parent_cid: str) -> list[dict]:
    """OPEN child commitments whose EFFECTIVE parent is `parent_cid` (SUB1):
    on-disk data.parent_id resolved through the merge re-point chain, closure
    state per the same order-aware math the loader uses. Orphan children of a
    CLOSED parent still resolve here — the caller decides what that means."""
    out: list[dict] = []
    for raw_pid, kids in index["children_of"].items():
        if _resolve_survivor(index["superseded_onto"], raw_pid) != parent_cid:
            continue
        for ch in kids:
            ch_cid = _commitment_id(ch)
            status = _commitment_field(ch, "status") or "open"
            if status not in ("open", "overdue"):
                continue
            if not _currently_closed(index, ch_cid, ch.get("seq")):
                out.append(ch)
    return out


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
    close_subitems: bool = False,
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
      close_subitems: SUB1 D3 — closing a parent with OPEN sub-items raises
        OpenSubitemsError unless this is True (from a one-line user confirm:
        "this also closes its N open sub-items"). The cascade then closes
        the children FIRST (each an ordinary per-child closure, evidence
        "parent closed", inheriting the caller's user_confirmed and
        resolution), then the parent — crash-safe ordering: a failure
        mid-cascade leaves closed children + an OPEN parent (recoverable),
        never a closed parent with silently-orphaned open children. A
        pending_review child blocks an unconfirmed cascade exactly as it
        blocks a direct close (the error names WHICH child, F-59). The
        whole sequence runs inside the one writer-lock span. Programmatic
        closers (reconcile-sent, CRU auto_resolve) NEVER pass True — they
        downgrade to a propose (cru_match.parent_blocks_auto_resolve).

    Returns {"status": "closed", "commitment_id": <canonical>, "event": {...}}
    or {"status": "already_resolved", "commitment_id": <canonical>} (idempotent
    over the FULL resolved-id set — the last-200-lines window log-resolution
    used could re-close anything older than the tail). Callers MUST honor
    already_resolved as a NO-OP: acknowledge it honestly ("that one was
    already closed") and never fall back to a hand-built tombstone append —
    83 duplicate resolve-on-top-of-resolve tombstones in the live history
    came from blind re-closes (v4.5.2 R1c).

    Concurrency (v4.5.2 R1c): the scan -> state-check -> append sequence runs
    INSIDE the events writer lock. Before this, two concurrent orchestrators
    closing the same id could both scan, both see "open", and both append —
    the read-then-write race that mints duplicate tombstones no matter how
    honest each caller is. The lock is reentrant per thread, so the nested
    acquire inside append_event/atomic_append_jsonl just increments depth.
    """
    if resolution not in VALID_RESOLUTIONS:
        raise ValueError(
            f"invalid resolution {resolution!r} (allowed: {VALID_RESOLUTIONS})"
        )
    from pathlib import Path as _Path
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"

    with events_writer_lock(events_path, holder=f"close_commitment:{source_skill}"):
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

        # SUB1 D3 — no silent cascade, no silently-orphaned children.
        open_kids = _live_children(index, cid)
        closed_subitem_ids: list[str] = []
        if open_kids:
            if not close_subitems:
                titles = ", ".join(
                    repr(_commitment_field(k, "title") or _commitment_id(k))
                    for k in open_kids[:3]
                )
                raise OpenSubitemsError(
                    f"commitment {cid!r} has {len(open_kids)} open "
                    f"sub-item(s) ({titles}{'…' if len(open_kids) > 3 else ''}) "
                    "— closing the parent also closes them. Confirm with the "
                    "user, then pass close_subitems=True; or close/drop the "
                    "sub-items individually first."
                )
            # Pre-check EVERY child before writing anything (atomic refuse):
            # a pending_review child blocks an unconfirmed cascade, and the
            # ack must say WHICH child blocked and why (F-59).
            if not user_confirmed:
                for k in open_kids:
                    if _is_pending_review(k):
                        raise PendingReviewError(
                            f"sub-item {_commitment_id(k)!r} "
                            f"({(_commitment_field(k, 'title') or '')!r}) of "
                            f"commitment {cid!r} is pending_review — the "
                            "cascade may only close it on an explicit user "
                            "confirmation (pass user_confirmed=True). Nothing "
                            "was closed."
                        )
            # Children FIRST (crash-safe: a mid-cascade failure leaves closed
            # children + an open parent — recoverable — never a closed parent
            # with open children), then the parent, all in this lock span.
            from event_gate import append_event as _append_ev
            child_closers: list[dict] = []
            for k in open_kids:
                k_cid = _commitment_id(k)
                closed_subitem_ids.append(k_cid)
                child_closers.append({
                    "type": "commitment_resolved",
                    "source_skill": source_skill,
                    "primary_thread_id": k.get("primary_thread_id") or "",
                    "data": {
                        "commitment_id": k_cid,
                        "resolved_by": resolved_by,
                        "evidence": "parent closed",
                        "resolution": resolution,
                    },
                })
            _append_ev(events_path, child_closers, holder=source_skill)

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
    result = {"status": "closed", "commitment_id": cid, "event": ev}
    if closed_subitem_ids:
        # SUB1 D3 — the cascade's child ids, in close order: the dispatch
        # layer caches these alongside the parent id so a batch undo reopens
        # the whole family (reopen is per-item; see reopen_commitment).
        result["closed_subitems"] = closed_subitem_ids
    return result


def supersede_commitment(
    workspace_root,
    survivor_id,
    superseded_id,
    *,
    merged_by: str,
    source_skill: str,
    evidence: str = "",
    user_confirmed: bool = False,
    auto_merge: bool = False,
    auto_merge_evidence: Optional[dict] = None,
    brain_batch_id: Optional[str] = None,
    brain_change_class: Optional[str] = None,
) -> dict:
    """THE merge writer (v4.6.0 C4): two open items are the same real-world
    commitment → close the duplicate with a `commitment_superseded` event that
    names the survivor and unions the provenance. Restoration, not invention —
    the loader has honored `commitment_superseded` as a closer since v3.14.5
    (people-crm Gate 2 contract) with no writer emitting it, and the live
    history carries `resolution: "duplicate"` ×14 + `superseded_by_seq` from
    the era when supersession existed. This is the event the confirm flow's
    Merge verb (W4b) lands on; until W4b ships the surface is the chat phrase
    documented in commitment-triage's SKILL.md ("merge those two" / "same
    commitment").

    Semantics:
      - The SUPERSEDED item closes (the projector already treats
        `commitment_superseded` as a closer via the standard id + seq-alias
        chains — data.commitment_id / data.commitment_seq reference it).
      - The SURVIVOR stays exactly as it is on disk; the event carries
        `data.superseded_by` (survivor's canonical id) +
        `data.merged_source_refs` (union of both sides' source_refs), and
        `load_open_commitments` folds that provenance onto the survivor's
        in-memory copy (`data.merged_source_refs` / `data.merged_from`) —
        history is never rewritten.
      - Same guard set as close_commitment (one closure doctrine): id
        normalization over legacy spellings, loud CommitmentIdError on no
        match, idempotent when the superseded item is already closed,
        pending_review floor (merging IS the adjudication of a suspected
        duplicate, so it requires an explicit user action —
        user_confirmed=True), scan→append inside the writer lock (R1c).
      - survivor == superseded (after normalization) is a hard error, and a
        CLOSED survivor is allowed — the duplicate of an already-done thing
        is itself done; the closure is still correct.
      - SUB1 D3b: the superseded item's SUB-ITEMS transfer to the survivor
        READ-SIDE — the loader (and the writer index) re-point `parent_id`
        through the supersession chain on in-memory copies only; no history
        rewrite, no per-child event. This is why a merge needs no cascade
        guard: the children stay open, just under the survivor.

    AUTOAPPLY §4c — the pending-review floor's carve-out. `auto_merge=True`
    satisfies the floor in place of `user_confirmed`, and REQUIRES
    `auto_merge_evidence` (the gate's predicate + score + batch stamps) so
    it can never be used as a bare override. The doctrine: the floor
    ("merging IS the adjudication") governs FLAGGED SUSPECTS — a
    human-ambiguity queue. `commitment_dedup.auto_merge_eligible` fires only
    where there is no ambiguity left to adjudicate (owner ids equal,
    counterparty ids overlapping, near-verbatim title, different writers),
    so there is no human decision being taken away. The evidence lands on
    the event, which is also what makes the merge undoable: the stamps carry
    `brain_batch_id` + `brain_change_class` for `brain_undo`.

    SWEEPBACK — `brain_batch_id` / `brain_change_class` for a USER-CONFIRMED
    merge. Before this, the undo stamps could only ride the `auto_merge_evidence`
    dict, which is read ONLY when `auto_merge=True` — so a merge the user
    approved was unreachable by `brain_undo.undo_batch`, while a merge nobody
    approved was reversible. The backlog sweep merges only on an explicit
    approval and must still land in its one `swb_` batch, so the two stamps are
    now first-class parameters. They travel TOGETHER (one without the other is a
    ValueError, same rule as `people_writer` / `org_writer`): a `brain_batch_id`
    with no `brain_change_class` is a row `_changes_for_brain_batch` silently
    skips, which is an undo handle that looks present and reverses nothing. The
    class must be one `brain_undo.REVERSERS` knows — pass `"commitment_merge"`,
    whose reverser reopens the absorbed item and puts the pair back on the flag
    tier. Both None (the default) = pre-SWEEPBACK behavior, byte-identical.
    """
    if (brain_batch_id is None) != (brain_change_class is None):
        raise ValueError(
            "brain_batch_id and brain_change_class travel together — a batch id "
            "with no change class is a row brain_undo skips, i.e. an undo handle "
            "that looks present and reverses nothing"
        )
    from pathlib import Path as _Path
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"

    with events_writer_lock(events_path, holder=f"supersede_commitment:{source_skill}"):
        index = _scan_commitment_index(events_path)
        survivor_cid = normalize_commitment_id(survivor_id, index)
        superseded_cid = normalize_commitment_id(superseded_id, index)
        if survivor_cid == superseded_cid:
            raise ValueError(
                f"survivor and superseded resolve to the same commitment "
                f"({survivor_cid!r}) — a merge needs two distinct items"
            )
        survivor = index["by_id"][survivor_cid]
        superseded = index["by_id"][superseded_cid]

        if _currently_closed(index, superseded_cid, superseded.get("seq")):
            return {
                "status": "already_resolved",
                "commitment_id": superseded_cid,
                "survivor_id": survivor_cid,
            }

        if auto_merge and not (isinstance(auto_merge_evidence, dict)
                               and auto_merge_evidence.get("auto_predicate")):
            raise ValueError(
                "auto_merge=True requires auto_merge_evidence carrying an "
                "auto_predicate — the §4c carve-out records WHY it did not "
                "ask, or it is a bare override of the pending-review floor"
            )
        if _is_pending_review(superseded) and not (user_confirmed or auto_merge):
            raise PendingReviewError(
                f"commitment {superseded_cid!r} is pending_review — a merge "
                "adjudicates a suspected duplicate, so it may only happen on "
                "an explicit user confirmation (pass user_confirmed=True "
                "from a user-initiated action such as the Merge verb or the "
                "'merge those two' chat phrase), or through the AUTOAPPLY "
                "§4c gate (auto_merge=True + auto_merge_evidence)."
            )

        # Provenance union — survivor's ref first, then the absorbed one(s).
        refs: list[str] = []
        for ev in (survivor, superseded):
            ref = (ev.get("data") or {}).get("source_ref") or ev.get("source_ref")
            if isinstance(ref, str) and ref.strip() and ref not in refs:
                refs.append(ref)

        data: dict = {
            "commitment_id": superseded_cid,
            "superseded_by": survivor_cid,
            "resolved_by": merged_by,
            "resolution": "duplicate",
            "evidence": (evidence or f"merged into {survivor_cid}")[:200],
            "merged_source_refs": refs,
        }
        # Seq aliases on BOTH sides: commitment_seq feeds the F3 closer chain
        # for the superseded item; survivor_seq is diagnostic (mirrors the
        # historic superseded_by_seq breadcrumb).
        if isinstance(superseded.get("seq"), int):
            data["commitment_seq"] = superseded["seq"]
        if isinstance(survivor.get("seq"), int):
            data["survivor_seq"] = survivor["seq"]
        if auto_merge:
            # §7 — the audit trail rides the event, not a vanished chat:
            # WHICH clause fired, at what score, in which undoable batch.
            data["auto_merge"] = True
            for k, v in (auto_merge_evidence or {}).items():
                if v not in (None, ""):
                    data[k] = v
        if brain_batch_id is not None:
            # SWEEPBACK — the undo handle for a merge the USER approved. Set
            # AFTER the auto_merge fold so an explicit batch id wins over one
            # that arrived inside `auto_merge_evidence`; the two paths are
            # mutually exclusive in practice (nothing auto-merges inside a
            # sweep), and "the explicit argument wins" is the only rule that
            # cannot surprise a caller.
            data["brain_batch_id"] = brain_batch_id
            data["brain_change_class"] = brain_change_class

        ev = {
            "type": "commitment_superseded",
            "source_skill": source_skill,
            "primary_thread_id": (
                superseded.get("primary_thread_id")
                or survivor.get("primary_thread_id")
                or ""
            ),
            "data": data,
        }
        from event_gate import append_event
        append_event(events_path, [ev], holder=source_skill)
    return {
        "status": "superseded",
        "commitment_id": superseded_cid,
        "survivor_id": survivor_cid,
        "event": ev,
    }


def edit_commitment_wording(
    workspace_root,
    commitment_id,
    *,
    edited_by: str,
    source_skill: str,
    new_title: Optional[str] = None,
    new_summary: Optional[str] = None,
    reason: str = "wording fix",
) -> dict:
    """THE wording-fix writer (v4.6.0 S4 — the `fix wording` verb).

    A mis-extracted summary/title was uncorrectable before S4: the loader
    ignored non-due `commitment_updated` events, so nothing the user said
    changed what surfaces rendered. This appends a `commitment_updated`
    carrying `data.new_title` / `data.new_summary`; the projector folds the
    latest of each field into the projected item (newest wins per field) and
    the original wording stays in history — append-only, never rewritten.

    Same guard set as the other lifecycle writers: id normalization over
    legacy spellings, loud CommitmentIdError on no match, scan→append inside
    the writer lock (R1c). Editing a CLOSED item is allowed — the fold only
    affects the open projection, and correcting history's wording for future
    search/prep is legitimate.
    """
    new_title = (new_title or "").strip() or None
    new_summary = (new_summary or "").strip() or None
    if not (new_title or new_summary):
        raise ValueError(
            "edit_commitment_wording needs new_title and/or new_summary — "
            "an empty wording fix changes nothing"
        )
    from pathlib import Path as _Path
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_writer_lock(events_path, holder=f"edit_wording:{source_skill}"):
        index = _scan_commitment_index(events_path)
        cid = normalize_commitment_id(commitment_id, index)
        target = index["by_id"][cid]
        data: dict = {
            "commitment_id": cid,
            "edited_by": edited_by,
            "reason": (reason or "")[:200],
        }
        if isinstance(target.get("seq"), int):
            data["commitment_seq"] = target["seq"]
        if new_title:
            data["new_title"] = new_title[:300]
        if new_summary:
            data["new_summary"] = new_summary[:500]
        ev = {
            "type": "commitment_updated",
            "source_skill": source_skill,
            "primary_thread_id": target.get("primary_thread_id") or "",
            "data": data,
        }
        from event_gate import append_event
        append_event(events_path, [ev], holder=source_skill)
    return {"status": "updated", "commitment_id": cid, "event": ev}


def reassign_commitment(
    workspace_root,
    commitment_id,
    *,
    reassigned_by: str,
    source_skill: str,
    new_owner_id: Optional[str] = None,
    new_counterparty_id: Optional[str] = None,
    new_owner_name: Optional[str] = None,
    new_counterparty_name: Optional[str] = None,
    reason: str = "",
    confirmed: bool = False,
) -> dict:
    """THE reassignment writer (v4.6.0 S4). Today "not mine" DISCARDS a
    cross-attendee capture; this ROUTES it instead — the item leaves the
    user's you-owe bucket and lands on the new owner (or gains a new
    counterparty) with full history preserved.

    `confirmed` marks an explicit user action naming the person — the W4b
    Theirs→[name] confirm verb and the "that's actually [name]'s" chat phrase
    both pass True. Anything programmatic or inferred passes False, and the
    projector then stamps `pending_review` on the projected item: it counts
    in the unconfirmed bucket and NEVER enters chase until confirmed (the
    W4b guardrail — no auto-email on a guessed owner).

    Guards: id normalization over legacy spellings, loud CommitmentIdError on
    no match, refuses a CLOSED item ({"status": "not_open"} — reopen it
    first; reassigning a tombstone routes nothing), at least one of
    new_owner_id / new_counterparty_id required, scan→append inside the
    writer lock (R1c).
    """
    if not (new_owner_id or new_counterparty_id):
        raise ValueError(
            "reassign_commitment needs new_owner_id and/or "
            "new_counterparty_id — a reassignment must route the item "
            "somewhere (to DROP an item use close_commitment with "
            "resolution='dropped')"
        )
    from pathlib import Path as _Path
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_writer_lock(events_path, holder=f"reassign_commitment:{source_skill}"):
        index = _scan_commitment_index(events_path)
        cid = normalize_commitment_id(commitment_id, index)
        target = index["by_id"][cid]
        if _currently_closed(index, cid, target.get("seq")):
            return {"status": "not_open", "commitment_id": cid}
        data: dict = {
            "commitment_id": cid,
            "reassigned_by": reassigned_by,
            "reason": (reason or "")[:200],
            "confirmed": bool(confirmed),
        }
        if isinstance(target.get("seq"), int):
            data["commitment_seq"] = target["seq"]
        if new_owner_id:
            data["new_owner_id"] = new_owner_id
            if new_owner_name:
                data["new_owner_name"] = new_owner_name
        if new_counterparty_id:
            data["new_counterparty_id"] = new_counterparty_id
            if new_counterparty_name:
                data["new_counterparty_name"] = new_counterparty_name
        ev = {
            "type": "commitment_reassigned",
            "source_skill": source_skill,
            "primary_thread_id": target.get("primary_thread_id") or "",
            "data": data,
        }
        from event_gate import append_event
        append_event(events_path, [ev], holder=source_skill)
    return {"status": "reassigned", "commitment_id": cid, "event": ev}


def confirm_commitment_owner(
    workspace_root,
    commitment_id,
    *,
    owner_id: str,
    confirmed_by: str,
    source_skill: str,
    owner_name: Optional[str] = None,
    reason: str = "user confirmed ownership",
) -> dict:
    """THE Mine writer (v4.6.1 W4b confirm flow). An unconfirmed capture —
    pending_review, unowned, or an inferred owner — is claimed by the user
    ("Mine"): ownership folds to `owner_id` and the pending_review flag
    clears, so the item leaves the unconfirmed bucket and joins the
    confirmed you-owe direction.

    Appends a `commitment_updated` carrying `data.owner_confirmed: true` +
    `data.new_owner_id`; the projector folds it (append-order-aware against
    reassignments — a later unconfirmed reassign re-stamps pending_review).
    Distinct from reassign_commitment on purpose: Mine CLAIMS, Theirs ROUTES
    — the event vocabulary keeps the two adjudications distinguishable in
    history.

    Guards: id normalization over legacy spellings, loud CommitmentIdError
    on no match, refuses a CLOSED item ({"status": "not_open"}), scan→append
    inside the writer lock (R1c). No pending_review floor — the explicit
    click IS the adjudication.
    """
    if not owner_id:
        raise ValueError("confirm_commitment_owner needs an owner_id — "
                         "'Mine' claims the item for a specific person")
    from pathlib import Path as _Path
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_writer_lock(events_path, holder=f"confirm_owner:{source_skill}"):
        index = _scan_commitment_index(events_path)
        cid = normalize_commitment_id(commitment_id, index)
        target = index["by_id"][cid]
        if _currently_closed(index, cid, target.get("seq")):
            return {"status": "not_open", "commitment_id": cid}
        data: dict = {
            "commitment_id": cid,
            "new_owner_id": owner_id,
            "owner_confirmed": True,
            "pending_review": False,
            "confirmed_by": confirmed_by,
            "reason": (reason or "")[:200],
        }
        if owner_name:
            data["new_owner_name"] = owner_name
        if isinstance(target.get("seq"), int):
            data["commitment_seq"] = target["seq"]
        ev = {
            "type": "commitment_updated",
            "source_skill": source_skill,
            "primary_thread_id": target.get("primary_thread_id") or "",
            "data": data,
        }
        from event_gate import append_event
        append_event(events_path, [ev], holder=source_skill)
    return {"status": "confirmed", "commitment_id": cid, "event": ev}


def clear_review_flags(
    workspace_root,
    commitment_id,
    *,
    cleared_by: str,
    source_skill: str,
    note: str = "confirmed distinct",
) -> dict:
    """THE Keep-both writer (v4.6.1 W4b / C4). A suspected duplicate the
    user adjudicates as a real, separate item: appends a `commitment_updated`
    carrying `data.review_flags_cleared: true`; the projector clears
    `pending_review`, `review_reason`, AND the C4 `suspected_duplicate_of` /
    `suspected_duplicate_score` flags read-side — both items stay open, the
    capture event is never rewritten.

    Same guard set as confirm_commitment_owner: id normalization, loud
    CommitmentIdError on no match, refuses a CLOSED item ({"status":
    "not_open"}), scan→append inside the writer lock (R1c).
    """
    from pathlib import Path as _Path
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_writer_lock(events_path, holder=f"clear_review:{source_skill}"):
        index = _scan_commitment_index(events_path)
        cid = normalize_commitment_id(commitment_id, index)
        target = index["by_id"][cid]
        if _currently_closed(index, cid, target.get("seq")):
            return {"status": "not_open", "commitment_id": cid}
        data: dict = {
            "commitment_id": cid,
            "review_flags_cleared": True,
            "pending_review": False,
            "cleared_by": cleared_by,
            "note": (note or "")[:200],
        }
        if isinstance(target.get("seq"), int):
            data["commitment_seq"] = target["seq"]
        ev = {
            "type": "commitment_updated",
            "source_skill": source_skill,
            "primary_thread_id": target.get("primary_thread_id") or "",
            "data": data,
        }
        from event_gate import append_event
        append_event(events_path, [ev], holder=source_skill)
    return {"status": "cleared", "commitment_id": cid, "event": ev}


def flag_duplicate_for_review(
    workspace_root,
    commitment_id,
    *,
    suspected_duplicate_of: str,
    reason: str,
    flagged_by: str,
    source_skill: str,
    score=None,
) -> dict:
    """THE flag-tier fallback writer (AUTOAPPLY §4c, review F-1/F-3/F-6) —
    the additive MIRROR of `clear_review_flags`.

    WHY IT EXISTS. The §4c auto-merge gate is evaluated at capture and
    APPLIED a fire later (the D1 split), so the decision can go stale in
    between: the survivor gets closed, the stamp ages past the window, or a
    human reverses the merge. Every one of those cases must land the pair
    back on the FLAG TIER — a visible question — never a silent skip and
    never a silent drop, because C4's whole guarantee is that a duplicate
    always becomes a question. The capture event is on disk and append-only,
    so `data.pending_review` cannot be edited onto it; this appends a
    `commitment_updated` carrying `data.review_flags_set: true` and the
    projector folds `pending_review` / `review_reason` /
    `suspected_duplicate_of` / `suspected_duplicate_score` onto the item's
    in-memory copy — exactly the shape `flag_suspected_duplicates` writes at
    capture, reached through an additive event instead of a rewrite.

    The flag is also self-fencing: `_is_pending_review` is a bar on BOTH
    sides of the auto-merge gate, so a flagged pair structurally cannot be
    auto-merged again while the flag stands. Clearing it is the user's call
    (`clear_review_flags`, the Keep-both verb).

    Same guard set as `clear_review_flags`: id normalization over legacy
    spellings, loud CommitmentIdError on no match, refuses a CLOSED item
    ({"status": "not_open"} — a closed item has no question left to ask),
    scan→append inside the writer lock (R1c). NOT idempotent by itself: the
    caller short-circuits on the PROJECTED `pending_review` before calling,
    which is the only reading that sees a previously folded flag.
    """
    from pathlib import Path as _Path
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_writer_lock(events_path, holder=f"flag_review:{source_skill}"):
        index = _scan_commitment_index(events_path)
        cid = normalize_commitment_id(commitment_id, index)
        target = index["by_id"][cid]
        if _currently_closed(index, cid, target.get("seq")):
            return {"status": "not_open", "commitment_id": cid}
        data: dict = {
            "commitment_id": cid,
            "review_flags_set": True,
            "pending_review": True,
            "review_reason": (reason or "looks like a duplicate")[:200],
            "suspected_duplicate_of": str(suspected_duplicate_of),
            "flagged_by": flagged_by,
        }
        if score is not None:
            data["suspected_duplicate_score"] = score
        if isinstance(target.get("seq"), int):
            data["commitment_seq"] = target["seq"]
        ev = {
            "type": "commitment_updated",
            "source_skill": source_skill,
            "primary_thread_id": target.get("primary_thread_id") or "",
            "data": data,
        }
        from event_gate import append_event
        append_event(events_path, [ev], holder=source_skill)
    return {"status": "flagged", "commitment_id": cid,
            "suspected_duplicate_of": str(suspected_duplicate_of), "event": ev}


# UNCONFIRM1 — the reason an un-confirmed item carries when neither the caller
# nor the capture event can say why it was ever a question. Honest about what
# it knows: the user reversed their own answer, so the row is a question again.
RESTORE_DEFAULT_REVIEW_REASON = "you un-confirmed this — it needs your call again"


def restore_review_flags(
    workspace_root,
    commitment_id,
    *,
    restored_by: str,
    source_skill: str,
    review_reason: Optional[str] = None,
    note: str = "un-confirmed at the user's request",
) -> dict:
    """THE un-confirm writer (UNCONFIRM1) — the additive mirror of
    `clear_review_flags` for a confirm the USER reversed.

    WHY IT EXISTS. `clear_review_flags` is what the needs-your-call queue's
    `confirm` writes, and until now the only additive writer that reversed
    that fold was `flag_duplicate_for_review` — which REQUIRES a
    `suspected_duplicate_of` and is named, documented and schema-described
    for duplicate PAIRS ("system-written, not user-driven",
    COMMITMENT_SCHEMA.md). Reversing a confirm through it meant passing an
    empty target: benign in the projection (the fold stamps the duplicate
    flag only `if entry.get("duplicate_of")`, and `""` is falsy) and
    permanently wrong on disk, where the event carries
    `suspected_duplicate_of: ""` written by a duplicate-named writer and
    nothing anywhere says a person un-confirmed anything. That residue is
    what this writer removes. `flag_duplicate_for_review` keeps the duplicate
    case and is unchanged.

    Appends ONE `commitment_updated` carrying the EXISTING fold key
    `data.review_flags_set: true` — so there is ZERO reader change: the
    projector sorts adjudications by append index (`cru_match` — latest
    wins), and a `review_flags_set` appended after a `review_flags_cleared`
    puts the item back in `load_needs_review` carrying its reason. It also
    carries `data.review_flags_restored: true`, which no reader consumes: it
    is provenance, so a history reader can tell an un-confirm from a
    duplicate flag without inferring it from an absent key. And it takes NO
    duplicate target from the caller — there is no such parameter, and
    refusing to accept one is the point of the writer.

    THE DUPLICATE LINK IS RESTORED, NOT DISCARDED (amendment 2026-08-03,
    review finding MF-3). The first draft omitted `suspected_duplicate_of`
    flatly, and that was too broad: `load_needs_review` includes items flagged
    as suspected duplicates AT CAPTURE, so this queue's `confirm` runs on
    them, and the confirm fold NULLS the field
    (`cru_match` — `patch["suspected_duplicate_of"] = None` on `clear_flags`)
    while the flagset branch only re-stamps it `if entry.get("duplicate_of")`.
    A flat omission therefore destroyed a real duplicate link permanently, and
    visibly: the confirm-flow row picks its verb set with
    `bool(suspected_duplicate_of)`, so the un-confirmed row rendered a context
    tag still saying "looks like a duplicate" with the Merge verb GONE. An
    undo must return the item to the state it was in before the gesture. So
    the writer re-carries `suspected_duplicate_of` AND
    `suspected_duplicate_score` FROM THE TARGET CAPTURE EVENT, read inside
    the lock — the same substrate-derived mechanism it uses for
    `review_reason`. When the capture carries no link, the key is OMITTED
    ENTIRELY: never `""`, never `None`, never any other falsy placeholder,
    because a falsy value is exactly the off-label residue this writer exists
    to remove and it reads as a duplicate pair that never existed.

    `review_reason` resolution: the caller's value when given; otherwise the
    reason off the TARGET CAPTURE EVENT already in hand inside the lock, so
    nothing has to be cached between the confirm and the undo and a stale
    cache cannot invent a reason; otherwise
    `RESTORE_DEFAULT_REVIEW_REASON`.

    Same guard set as `clear_review_flags` / `flag_duplicate_for_review`: id
    normalization over legacy spellings, loud CommitmentIdError on no match,
    refuses a CLOSED item ({"status": "not_open"} — the caller reopens
    first; see needs_review_queue.undo_done_items), scan→append inside the
    writer lock (R1c).
    """
    from pathlib import Path as _Path
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_writer_lock(events_path,
                            holder=f"restore_review:{source_skill}"):
        index = _scan_commitment_index(events_path)
        cid = normalize_commitment_id(commitment_id, index)
        target = index["by_id"][cid]
        if _currently_closed(index, cid, target.get("seq")):
            return {"status": "not_open", "commitment_id": cid}
        reason = review_reason
        if reason is None or not str(reason).strip():
            # Substrate-derived: the capture's own reason, read inside the
            # lock off the event this call already resolved.
            reason = _commitment_field(target, "review_reason") or ""
        reason = str(reason).strip() or RESTORE_DEFAULT_REVIEW_REASON
        data: dict = {
            "commitment_id": cid,
            "review_flags_set": True,
            "review_flags_restored": True,
            "pending_review": True,
            "review_reason": reason[:200],
            "unconfirmed_by": restored_by,
            "note": (note or "")[:200],
        }
        # MF-3: restore the capture's OWN duplicate link, or write no key at
        # all. Substrate-derived and never caller-supplied; a falsy value is
        # never written, because the flagset fold re-stamps only a truthy one
        # and a falsy key on disk IS the residue this writer replaces.
        dup_of = _commitment_field(target, "suspected_duplicate_of")
        if isinstance(dup_of, str) and dup_of.strip():
            data["suspected_duplicate_of"] = dup_of.strip()
            dup_score = _commitment_field(target, "suspected_duplicate_score")
            if dup_score is not None:
                data["suspected_duplicate_score"] = dup_score
        if isinstance(target.get("seq"), int):
            data["commitment_seq"] = target["seq"]
        ev = {
            "type": "commitment_updated",
            "source_skill": source_skill,
            "primary_thread_id": target.get("primary_thread_id") or "",
            "data": data,
        }
        from event_gate import append_event
        append_event(events_path, [ev], holder=source_skill)
    return {"status": "restored", "commitment_id": cid, "event": ev}


def mark_partial_received(
    workspace_root,
    commitment_id,
    *,
    received_by: str,
    source_skill: str,
    counterparty_id: Optional[str] = None,
    counterparty_name: Optional[str] = None,
    evidence: str = "",
) -> dict:
    """THE per-person receipt writer (v4.6.0 MC1). A multi-counterparty
    commitment ("send the deck to the board") is fulfilled one counterparty
    at a time; this records that ONE counterparty delivered WITHOUT closing
    the item. Appends a `commitment_partial_received` carrying
    `data.received_counterparty_id` (or free-text `received_counterparty_name`
    when the person has no record); the projector accumulates
    `data.received_from` / `data.received_from_names` and stamps
    `data.all_counterparties_received` when the whole roster is in — the
    PROPOSE-closure signal (this writer NEVER closes; the user closes when
    ready).

    Returns {"status": "received", "commitment_id": <canonical>,
             "propose_closure": bool, "outstanding": [{"id","name"}, ...],
             "event": {...}} — `propose_closure` is True iff this receipt
    completed the roster (surfaces render "everyone's received — close it?").
    A CLOSED item returns {"status": "not_open", ...}; an already-recorded
    receipt for the same counterparty is harmless (the loader unions), so no
    idempotency short-circuit is needed.

    Guards: at least one of counterparty_id / counterparty_name required
    (WHICH recipient delivered?), id normalization over legacy spellings,
    loud CommitmentIdError on no match, scan→append inside the writer lock
    (R1c). No pending_review floor — a receipt is informational, not a
    closure; it never removes the item from the open set.
    """
    if not (counterparty_id or counterparty_name):
        raise ValueError(
            "mark_partial_received needs a counterparty_id or "
            "counterparty_name — a receipt records WHICH recipient of a "
            "multi-counterparty commitment delivered"
        )
    from pathlib import Path as _Path
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_writer_lock(events_path, holder=f"mark_received:{source_skill}"):
        index = _scan_commitment_index(events_path)
        cid = normalize_commitment_id(commitment_id, index)
        target = index["by_id"][cid]
        if _currently_closed(index, cid, target.get("seq")):
            return {"status": "not_open", "commitment_id": cid}
        data: dict = {
            "commitment_id": cid,
            "received_by": received_by,
        }
        if counterparty_id:
            data["received_counterparty_id"] = counterparty_id
        if counterparty_name and not counterparty_id:
            data["received_counterparty_name"] = counterparty_name
        if isinstance(target.get("seq"), int):
            data["commitment_seq"] = target["seq"]
        if evidence:
            data["evidence"] = (evidence or "")[:200]
        ev = {
            "type": "commitment_partial_received",
            "source_skill": source_skill,
            "primary_thread_id": target.get("primary_thread_id") or "",
            "data": data,
        }
        from event_gate import append_event
        append_event(events_path, [ev], holder=source_skill)

    # Recompute the roster state from the fresh projection (append invalidated
    # the loader cache) so the caller can PROPOSE closure the moment the last
    # counterparty is in — never auto-closing here.
    from commitment_parties import (
        all_counterparties_received as _all_rcv,
        outstanding_counterparties as _outstanding,
    )
    projected = None
    for c in load_open_commitments(events_path):
        if _commitment_id(c) == cid:
            projected = c
            break
    # F-28: pass the workspace so the roster reader can collapse one person
    # written as an id AND that person's name into ONE counterparty — otherwise
    # the last real receipt never proposes closure and the phantom leg is
    # reported as still outstanding to the caller that renders the chase.
    propose_closure = bool(projected is not None
                           and _all_rcv(projected, workspace_root=workspace_root))
    outstanding = (_outstanding(projected, workspace_root=workspace_root)
                   if projected is not None else [])
    return {
        "status": "received",
        "commitment_id": cid,
        "propose_closure": propose_closure,
        "outstanding": outstanding,
        "event": ev,
    }


def _mint_child_commitments(
    index: dict,
    parent: dict,
    children: list[dict],
    *,
    source_skill: str,
    provenance_fields: dict,
) -> tuple[list[dict], list[str]]:
    """The shared child-minting loop (SUB1 § 3): given an open parent and a
    list of child dicts, mint Stage-D-complete `commitment` events — fresh
    cmt_<ulid> ids, explicit kind, owner/counterparty/source_ref/
    primary_thread_id inherited from the parent unless the child overrides
    them — with `provenance_fields` stamped on every child right after
    `status` (split passes split_from/source_event_seq; add_subitems passes
    parent_id/parent_seq). Returns (child_events, child_ids); the caller
    appends them (children FIRST — crash-safe ordering) inside its own
    writer-lock span. Factored out of split_commitment byte-identically."""
    from event_gate import new_commitment_id
    pdata = parent.get("data") or {}
    parent_kind = effective_kind(index, parent)
    parent_owner = _commitment_field(parent, "owner_id")
    parent_cp = _commitment_field(parent, "counterparty_id")
    parent_ref = pdata.get("source_ref") or parent.get("source_ref")

    child_events: list[dict] = []
    child_ids: list[str] = []
    for ch in children:
        child_id = new_commitment_id()
        child_ids.append(child_id)
        cdata: dict = {
            "id": child_id,
            "title": ch["title"].strip()[:300],
            "kind": ch.get("kind") or parent_kind,
            "status": "open",
        }
        cdata.update(provenance_fields)
        due = ch.get("due")
        if due:
            cdata["due"] = due
        owner = ch.get("owner_id") or parent_owner
        if owner:
            cdata["owner_id"] = owner
        cp = ch.get("counterparty_id") or parent_cp
        if cp:
            cdata["counterparty_id"] = cp
        cp_name = ch.get("counterparty_name") or pdata.get("counterparty_name")
        if cp_name:
            cdata["counterparty_name"] = cp_name
        if isinstance(parent_ref, str) and parent_ref.strip():
            cdata["source_ref"] = parent_ref
        child_events.append({
            "type": "commitment",
            "source_skill": source_skill,
            "primary_thread_id": parent.get("primary_thread_id") or "",
            "data": cdata,
        })
    return child_events, child_ids


def split_commitment(
    workspace_root,
    commitment_id,
    children,
    *,
    split_by: str,
    source_skill: str,
    user_confirmed: bool = False,
) -> dict:
    """THE split writer (v4.6.0 S4 — M decision 2026-07-09: extraction
    pre-split stays the doctrine; this is the MANUAL correction path for the
    capture that landed as one atomic item but is really N).

    "split that into A / B / C" takes one open commitment → N new commitment
    events, each Stage-D complete (minted cmt_<ulid> id, explicit kind,
    inherited owner/counterparty/source_ref unless the child overrides them,
    `data.source_event_seq` → the original + `data.split_from` provenance),
    then closes the original via `commitment_superseded` with
    `data.split_into` listing the child ids and evidence "split into …".

    Ordering is crash-safe: children land FIRST, the closer second — a
    failure between the two leaves the parent open next to its children
    (recoverable with a merge/close), never a closed parent with no
    children. The whole sequence runs inside the writer lock (R1c). The
    capture-time dedup hook never flags a child against the commitment it
    names as its source (split provenance guard in commitment_dedup).

    Guards mirror supersede_commitment: id normalization, loud
    CommitmentIdError, idempotent when the original is already closed
    ({"status": "already_resolved"}), pending_review floor (splitting
    adjudicates the item — an explicit user action is required for flagged
    items), >= 2 children each with a non-empty title.

    `children`: list of dicts — {"title" (required), "due"?, "owner_id"?,
    "counterparty_id"?, "counterparty_name"?, "kind"?}. Missing kind/owner/
    counterparty inherit the parent's effective values.
    """
    children = list(children or [])
    if len(children) < 2:
        raise ValueError(
            "split_commitment needs at least 2 children — splitting into one "
            "item is a wording fix (edit_commitment_wording), not a split"
        )
    for i, ch in enumerate(children):
        if not isinstance(ch, dict) or not (ch.get("title") or "").strip():
            raise ValueError(f"split child {i} has no title — every child "
                             "must be a Stage-D-complete commitment")
    from pathlib import Path as _Path
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_writer_lock(events_path, holder=f"split_commitment:{source_skill}"):
        index = _scan_commitment_index(events_path)
        cid = normalize_commitment_id(commitment_id, index)
        parent = index["by_id"][cid]
        if _currently_closed(index, cid, parent.get("seq")):
            return {"status": "already_resolved", "commitment_id": cid}
        if _is_pending_review(parent) and not user_confirmed:
            raise PendingReviewError(
                f"commitment {cid!r} is pending_review — a split adjudicates "
                "the item, so it may only happen on an explicit user action "
                "(pass user_confirmed=True from the split verb / chat phrase)."
            )
        # SUB1 D4 — a decomposed item being "really N unrelated peers" is
        # incoherent: the user already said the parts belong to ONE
        # deliverable. Refuse; the fix is closing/dropping the children first,
        # or `add subitems` for more decomposition.
        live_kids = _live_children(index, cid)
        if live_kids:
            raise ValueError(
                f"commitment {cid!r} has {len(live_kids)} open sub-item(s) — "
                "a split closes the parent, but sub-items say the parts "
                "belong to ONE deliverable. Close or drop the sub-items "
                "first, or use `add subitems` to decompose further."
            )

        from event_gate import append_event
        provenance: dict = {"split_from": cid}
        if isinstance(parent.get("seq"), int):
            provenance["source_event_seq"] = parent["seq"]
        child_events, child_ids = _mint_child_commitments(
            index, parent, children,
            source_skill=source_skill, provenance_fields=provenance,
        )
        # Children FIRST (crash-safe: a parent is never closed without its
        # parts on disk), then the split closer referencing the child ids.
        append_event(events_path, child_events, holder=source_skill)

        closer_data: dict = {
            "commitment_id": cid,
            "superseded_by": child_ids[0],
            "split_into": child_ids,
            "resolved_by": split_by,
            "resolution": "superseded",
            "evidence": f"split into {len(child_ids)} items: "
                        f"{', '.join(child_ids)}"[:200],
        }
        if isinstance(parent.get("seq"), int):
            closer_data["commitment_seq"] = parent["seq"]
        closer = {
            "type": "commitment_superseded",
            "source_skill": source_skill,
            "primary_thread_id": parent.get("primary_thread_id") or "",
            "data": closer_data,
        }
        append_event(events_path, [closer], holder=source_skill)
    return {
        "status": "split",
        "commitment_id": cid,
        "children": child_ids,
        "event": closer,
    }


# SUB1 D-8 (M ruling 2026-07-16): hard cap on open children per parent — a
# 13-step item is a project, not a commitment; the cap also bounds the
# family-atomic widget page. Loud writer error above it, never a silent trim.
MAX_SUBITEMS_PER_PARENT = 12


def add_subitems(
    workspace_root,
    commitment_id,
    children,
    *,
    added_by: str,
    source_skill: str,
    user_confirmed: bool = False,
) -> dict:
    """THE sub-item writer (SUB1 — M ruling 2026-07-16, all 8 recommendations).

    Decomposition with the parent's SURVIVAL: "break that into A / B / C"
    takes one OPEN commitment → N new child `commitment` events, each
    Stage-D complete (minted cmt_<ulid> id, explicit kind, inherited owner/
    counterparty/source_ref unless the child overrides them) and carrying
    `data.parent_id` (the parent's canonical id VERBATIM) + `data.parent_seq`
    (F3-style seq alias). The parent stays open as the commitment of record —
    this is the sibling of split_commitment (extraction of peers with the
    parent's DEATH), sharing its child-minting loop but writing no closer.

    Children are real commitments: close/defer/drop/undo and the widget wire
    format work on them with zero special-casing. Creation is USER-INITIATED
    only — extraction/sweeps never mint hierarchies (the "extraction
    pre-splits into peers" doctrine, M 2026-07-09/16); no skill may call this
    from a scan path.

    Guards (SUB1 § 5): id normalization over legacy spellings, loud
    CommitmentIdError on no match, refuses a CLOSED parent ({"status":
    "not_open"} — decomposing a tombstone plans nothing), pending_review
    floor (decomposing IS adjudicating the item — requires
    user_confirmed=True from an explicit action, same as split), ONE level
    deep (a parent that is itself a live sub-item refuses — no grandchildren
    in v1), ≥ 1 titled child (unlike split's ≥ 2: one named step is a
    legitimate decomposition), and the D-8 cap — at most
    MAX_SUBITEMS_PER_PARENT (12) OPEN children after the add. Whole sequence
    inside the writer lock (R1c).

    `children`: list of dicts — {"title" (required), "due"?, "owner_id"?,
    "counterparty_id"?, "counterparty_name"?, "kind"?}. Same shape and
    input parsing as split_commitment's (`split into [items]`'s newline/
    semicolon/" / " rule, reused verbatim by the dispatch layer).
    """
    children = list(children or [])
    if len(children) < 1:
        raise ValueError(
            "add_subitems needs at least 1 child — an empty decomposition "
            "changes nothing"
        )
    for i, ch in enumerate(children):
        if not isinstance(ch, dict) or not (ch.get("title") or "").strip():
            raise ValueError(f"sub-item {i} has no title — every child "
                             "must be a Stage-D-complete commitment")
    from pathlib import Path as _Path
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_writer_lock(events_path, holder=f"add_subitems:{source_skill}"):
        index = _scan_commitment_index(events_path)
        cid = normalize_commitment_id(commitment_id, index)
        parent = index["by_id"][cid]
        if _currently_closed(index, cid, parent.get("seq")):
            return {"status": "not_open", "commitment_id": cid}
        if _is_pending_review(parent) and not user_confirmed:
            raise PendingReviewError(
                f"commitment {cid!r} is pending_review — decomposing "
                "adjudicates the item, so it may only happen on an explicit "
                "user action (pass user_confirmed=True from the add-subitems "
                "verb / chat phrase)."
            )
        # One level deep (SUB1 § 3): no grandchildren in v1 — keeps every
        # fold O(1) and the family widget renderable. "Live" = the parent's
        # own parent_id resolves to a real commitment event (merge re-point
        # honored); a dangling link degrades to top-level, same as the loader.
        own_pid = (parent.get("data") or {}).get("parent_id")
        if isinstance(own_pid, str) and own_pid.strip():
            eff_pid = _resolve_survivor(index["superseded_onto"], own_pid.strip())
            if eff_pid in index["by_id"]:
                raise ValueError(
                    f"commitment {cid!r} is itself a sub-item of {eff_pid!r} "
                    "— sub-items nest one level deep (no grandchildren in "
                    "v1). Add the steps to the top-level parent instead."
                )
        existing_open = _live_children(index, cid)
        if len(existing_open) + len(children) > MAX_SUBITEMS_PER_PARENT:
            raise ValueError(
                f"commitment {cid!r} would carry "
                f"{len(existing_open) + len(children)} open sub-items "
                f"(cap: {MAX_SUBITEMS_PER_PARENT}, currently open: "
                f"{len(existing_open)}) — a {MAX_SUBITEMS_PER_PARENT + 1}+-"
                "step item is a project, not a commitment. Close finished "
                "steps first or track the plan outside the commitment."
            )

        from event_gate import append_event
        provenance: dict = {"parent_id": cid}
        if isinstance(parent.get("seq"), int):
            provenance["parent_seq"] = parent["seq"]
        child_events, child_ids = _mint_child_commitments(
            index, parent, children,
            source_skill=source_skill, provenance_fields=provenance,
        )
        # No closer — the parent STAYS OPEN (the whole point of SUB1).
        append_event(events_path, child_events, holder=source_skill)
    return {
        "status": "subitems_added",
        "commitment_id": cid,
        "children": child_ids,
        "added_by": added_by,
    }


def close_commitments(workspace_root, closures, *, source_skill: str) -> list[dict]:
    """Batch closure for callers that close several commitments in one run
    (reconcile-sent). Same contract as close_commitment per item; a
    CommitmentIdError, PendingReviewError, or OpenSubitemsError on one item
    is recorded as {"status": "error", ...} and does NOT abort the rest (a
    bad id in a batch of real closes must not lose the real closes).

    SUB1 D3 — this batch path NEVER cascades (no close_subitems passthrough,
    deliberately): programmatic closers propose, they don't cascade. The
    matchers already downgrade a parent-with-open-children auto_resolve to
    pending_review (cru_match.parent_blocks_auto_resolve); an
    OpenSubitemsError here is the loud defensive floor, not the design path.
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
        except (CommitmentIdError, PendingReviewError, OpenSubitemsError) as e:
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
    no-match as close_commitment, and the same scan->append lock span
    (v4.5.2 R1c). A later re-close works normally.

    SUB1 D3 — reopening a cascade-closed PARENT reopens the PARENT ONLY;
    each child has its own tombstone and is reopened individually. The
    triage batch-undo already caches every closed id in the batch (the
    cascade's `closed_subitems` return joins that cache), so an undone
    cascade round-trips with zero new undo code.
    """
    from pathlib import Path as _Path
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_writer_lock(events_path, holder=f"reopen_commitment:{source_skill}"):
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
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_writer_lock(events_path, holder=f"promote_task:{source_skill}"):
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


def create_personal_task(workspace_root, *, title, owner_id, source_ref=None,
                         source_event_seq=None, source_skill="apply-choices",
                         now_iso=None) -> dict:
    """CTS1FIX 'Add to My Plate' — mint a fresh owner-me task commitment.
    Writes a `commitment` event with kind=task, owner_id=<me>, status open, which
    surface_split's `personal` partition renders on My Plate. Provenance links back
    to the originating row. Atomic append via the event gate; no in-place mutation.
    Returns status "created" (in the apply-audit OK vocabulary — FS-18a: a missing
    status would audit this write as an error)."""
    from pathlib import Path as _Path
    from capture_gate import gate_commitment_data
    from event_gate import append_event
    data = {
        "title": (title or "").strip(),
        "kind": "task",
        "owner_id": owner_id,
        "status": "open",
        "no_due": True,
        "source": "my_plate_capture",
    }
    if source_ref:
        data["source_ref"] = source_ref
    if source_event_seq is not None:
        data["source_event_seq"] = source_event_seq
    gate_commitment_data(data, subject="add to my plate")
    ev = {"type": "commitment", "source_skill": source_skill,
          "person_ids": [owner_id] if owner_id else [], "data": data}
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    append_event(events_path, [ev], holder=source_skill)
    return {"ok": True, "status": "created", "commitment": ev}


def stale_tasks(
    open_commitments: list[dict],
    now_iso: str,
    *,
    days: int = TASK_STALE_DAYS,
    movement: Optional[dict] = None,
) -> list[dict]:
    """S5 task aging policy (code-enforced): open TASK-kind items with no
    activity for ≥ `days`. These are swept into the Friday triage as "still
    on your plate?" — they are NEVER rendered in commitment aging and NEVER
    chased by reconcile-sent/CRU (cru_match.cru_eligible excludes task kind
    at the matcher layer). Pass the PROJECTED open set so reclassification
    markers are already applied.

    v4.6.0 MC2 — pass `movement` (derive_commitment_movement's map) and the
    age keys on days since last MOVEMENT, the same derivation the stuck
    metric uses (capture ts is the movement floor, so a never-touched task
    ages exactly as before; a task updated last week is NOT "still on your
    plate?" noise). Without `movement`, falls back to capture-ts age
    (pre-MC2 behavior).

    SUB1 D6 — a task-kind CHILD ages with its PARENT's movement: the anchor
    is the newer of the child's own movement and the parent's (whose map
    entry already includes bubbled-up child activity), so a step of an
    actively-moving parent never draws a "still on your plate?" nudge."""
    from event_time import event_time
    now = _parse_date(now_iso)
    out: list[dict] = []
    for ev in open_commitments:
        if commitment_kind(ev) != "task":
            continue
        anchor = None
        if movement is not None:
            m = movement.get(_commitment_id(ev))
            if m is not None:
                anchor = m.ts.date()
            pid = (ev.get("data") or {}).get("parent_id")
            if isinstance(pid, str) and pid:
                pm = movement.get(pid)
                if pm is not None and (anchor is None or pm.ts.date() > anchor):
                    anchor = pm.ts.date()
        if anchor is None:
            anchor = _parse_date(event_time(ev))
        if anchor is None or now is None:
            continue
        if (now - anchor).days >= days:
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
    "MAX_SUBITEMS_PER_PARENT",
    "CommitmentIdError",
    "PendingReviewError",
    "OpenSubitemsError",
    "effective_kind",
    "partition_subitems",
    "add_subitems",
    "supersede_commitment",
    "edit_commitment_wording",
    "reassign_commitment",
    "confirm_commitment_owner",
    "clear_review_flags",
    "flag_duplicate_for_review",
    "restore_review_flags",
    "RESTORE_DEFAULT_REVIEW_REASON",
    "mark_partial_received",
    "split_commitment",
    "reopen_commitment",
    "promote_task_to_commitment",
    "create_personal_task",
    "stale_tasks",
    "commitment_kind",
    "load_open_commitments",
    "is_overdue",
    "reconcile_is_stale",
    "count_commitments",
    "commitment_counts",
    "match_commitments_to_meetings",
    "compute_brief_state",
    "compute_and_log_brief_state",
    "cap_needs_attention",
    "attention_age_days",
    "BRIEF_ATTENTION_CAP",
    "BRIEF_ROTATION_AGE_DAYS",
    "latest_brief_state_event",
    "normalize_commitment_id",
    "close_commitment",
    "close_commitments",
]
