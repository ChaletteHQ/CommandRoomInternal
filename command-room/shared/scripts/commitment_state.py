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
try:
    from text_clip import clip  # noqa: E402
except ImportError:  # pragma: no cover — direct-path fallback
    import sys as _sys_tc
    from pathlib import Path as _Path_tc
    _sys_tc.path.insert(0, str(_Path_tc(__file__).resolve().parent))
    from text_clip import clip  # noqa: E402

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

# PROV1 — the close-family source pointer. Every closer canonicalizes through
# Layer A4 (connector_adapters.provenance) before append, so `data.source_ref`
# is a resolvable key and never a raw spelling a reader has to re-normalize.
try:
    from connector_adapters.provenance import (  # noqa: E402
        PROVENANCE_MISSING_KEY,
        REF_GRAIN_KEY,
        REF_GRAIN_SURFACE_MINTED,
        SOURCE_REF_KEY,
        SourceRefError,
        close_provenance_fields,
        dedup_key_of,
    )
except ImportError:  # pragma: no cover — direct-path fallback
    from pathlib import Path as _Path_pv

    sys.path.insert(0, str(_Path_pv(__file__).resolve().parent))
    from connector_adapters.provenance import (  # noqa: E402
        PROVENANCE_MISSING_KEY,
        REF_GRAIN_KEY,
        REF_GRAIN_SURFACE_MINTED,
        SOURCE_REF_KEY,
        SourceRefError,
        close_provenance_fields,
        dedup_key_of,
    )


def _now_iso() -> str:
    """UTC to the second — the resolution every minted receipt in this codebase
    already uses (`needs_review_queue._now_iso`, `watch_gate._now_iso`). Kept as
    a module-level name so a test can drive the clock at one seam instead of
    monkeypatching `datetime`."""
    return datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mint_surface_ref(source_skill, now_iso=None) -> str:
    """THE minted floor pointer: `session:<surface>:<instant>` (SPEC PROVMINT1
    §2-1). ONE helper, ONE home — every writer that needs this shape calls
    here, and no module outside this one spells it locally.

    WHY IT IS REAL PROVENANCE, not a placebo. It names WHO closed the item
    (the surface the gesture arrived on) and WHEN (to the second). That is a
    smaller grain than `gmail:<message-id>` — it points at the act rather than
    at an artifact — but it is a true fact about the close, and it is the only
    fact available when a human types "mark done" in a chat. The grain marker
    (`_minted_pointer_fields`) is what stops a reader mistaking it for the
    bigger thing.

    THE TIME COMPONENT IS LOAD-BEARING, not decoration. Without it every close
    a surface ever performs carries one identical string, which resolves to
    nothing and still counts as `with_pointer` in
    `closure_index.pointer_coverage` — a constant that inflates the very metric
    the provenance work exists to produce (review F-2, and the EODFIX1 per-day
    `pack_run` constant, both of that class).

    `now_iso`: the caller's own gesture instant. Passed by every caller that
    performs ONE act over MANY ids, so the batch reads as one act rather than
    N; omitted only when the writer genuinely is the gesture.
    """
    skill = str(source_skill or "").strip() or "unknown-surface"
    return f"session:{skill}:{now_iso or _now_iso()}"


def _minted_pointer_fields(source_skill, now_iso=None) -> dict:
    """The minted ref PLUS its grain marker — the two halves are one shape and
    are never written apart. A minted pointer with no marker is exactly the
    silent coverage inflation §0-1 refuses; a marker with no pointer is a
    field naming nothing."""
    fields = close_provenance_fields(_mint_surface_ref(source_skill, now_iso))
    fields[REF_GRAIN_KEY] = REF_GRAIN_SURFACE_MINTED
    return fields


def _close_pointer_fields(source_ref=None, extra_data=None, *,
                          default_provider=None, mint_for=None, now_iso=None) -> dict:
    """The pointer fragment for ONE close-family write (PROV1 §3.2, PROVMINT1
    §2-1).

    Resolution order — CALLER FIRST, THEN THE FLOOR. This is the same
    prefer-the-better-ref-then-mint shape `watch_gate.confirm_review_rows`
    already used at its own call site; it is generalized here rather than
    forked, so there is one answer to "what pointer does this write carry":

      1. the explicit `source_ref` argument;
      2. a pointer the caller threaded through `extra_data` (the chat-reconcile
         leg has carried one there since CHATSCAN1 — it must not be re-marked
         just because the parameter is newer than the caller);
      3. `mint_for` → the minted surface receipt + its grain marker;
      4. nothing (no `mint_for`) → the honest `provenance_missing` marker.

    A writer that passes `mint_for` can never reach shape 4 — that is the whole
    inversion: prose ASKS for the better pointer, and the module catches what
    prose misses, instead of the pointer existing only where a caller
    remembered it.

    Raises SourceRefError BEFORE the writer lock is taken, so a malformed
    pointer refuses without touching the file.
    """
    ref = source_ref
    if ref is None and isinstance(extra_data, dict):
        ref = extra_data.get(SOURCE_REF_KEY)
    if mint_for and (ref is None or (isinstance(ref, str) and not ref.strip())):
        return _minted_pointer_fields(mint_for, now_iso)
    return close_provenance_fields(ref, default_provider=default_provider)


def _title_snapshot(commitment_event) -> dict:
    """`{"title": <the commitment's title>}`, or `{}` (SPEC EODFIX1 §2-2).

    A tombstone that names only an id is a tombstone every downstream reader
    has to join back to the log to render. Every other win writer stamps the
    name of the thing it moved; this one never did, which is why the End of
    Day's wins block was structurally blind to closes (`_WIN_SPECS`).

    SNAPSHOT, not a pointer: it records what the commitment was CALLED at the
    moment it closed. A later retitle does not rewrite history, and a reader
    that wants the current title still has the id.

    Absent or empty title → no key at all. An empty string would satisfy the
    presence checks downstream and render as a nameless row, which is the
    defect wearing the fix's clothes.
    """
    title = _commitment_field(commitment_event, "title")
    if isinstance(title, str) and title.strip():
        return {"title": clip(title.strip())}
    return {}


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


def overdue_days(due_value: Optional[str], now_iso: str):
    """How many whole days past its due date an item is, or None when it is
    not overdue at all (SPEC OVERDUE1 DD-3).

    Deliberately the SAME parse and the SAME "strictly before today" boundary
    `is_overdue` uses, and deliberately in this module rather than in the
    surface that needs it: two derivations of "how late is this" is how a row
    renders `overdue: True` beside "0 days overdue". `None` for a missing,
    unparseable, or not-yet-due date — the caller's threshold comparison then
    fails closed (an item with no known age never crosses a fatigue bar).
    """
    due = _parse_date(due_value)
    if due is None:
        return None
    today = _parse_date(now_iso)
    if today is None:
        return None
    delta = (today - due).days
    return delta if delta > 0 else None


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


LATER_SNOOZE_VIA = "later"


def _later_when(when_iso: str) -> tuple:
    """`(calendar_day, utc_stamp)` for one Later… — the SAME input read the two
    ways its two legs need, from ONE parse.

      calendar_day  the date the user NAMED, in the offset their input carried
                    ("2026-07-22"). This is what `data.new_due` gets: a due
                    date is a calendar day, not an instant.
      utc_stamp     the same moment normalized to UTC in the exact shape the
                    mute ledger writes its own `snooze_until`
                    (`%Y-%m-%dT%H:%M:%SZ`), so one ledger holds one format.
                    This is what `data.snooze_until` gets: a mute expiring IS
                    an instant.

    THE TWO ARE NOT INTERCHANGEABLE, and treating them as one was a real bug
    (review N-2). The defer leg used to take `utc_stamp[:10]`, so
    `2026-07-22T20:00:00-07:00` — an evening push in the fleet's most common
    timezone, and a shape the orchestrator's natural-language date parse
    genuinely produces — became `03:00Z the NEXT day`, and the due date landed
    a day after the one the user named. The snooze leg's UTC reading is
    correct and unchanged: hiding a row until 20:00 local IS 03:00Z, and the
    row re-surfaces exactly when they meant.

    A bare date ("2026-07-22" — what `parse_later_when` returns) and a naive
    datetime are both read as UTC, so both legs agree on them; that day at
    00:00Z is what "hidden until Friday" means — the row is back ON Friday,
    not after it. TIME-CARRYING INPUT IS AN ACCEPTED SHAPE, deliberately: see
    `apply_later`'s contract note.
    """
    raw = str(when_iso or "").strip()
    try:
        dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError(
            "apply_later needs an ISO date/timestamp for when_iso — run "
            "commitment_state.parse_later_when (or the orchestrator's "
            "natural-language date parse) FIRST; a push with no date moves "
            f"nothing and would still be receipted. Got: {when_iso!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    # The calendar day comes off the value AS GIVEN — before any conversion.
    calendar_day = dt.date().isoformat()
    utc_stamp = dt.astimezone(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    return calendar_day, utc_stamp


def apply_later(
    workspace_root,
    commitment_id,
    *,
    when_iso: str,
    actor_id: str,
    source_skill: str,
    reason: str = "pushed to a later date",
    surface: str = "",
) -> dict:
    """THE `push to [date]` writer (APPLYAUDIT1 part 3).

    `later_route` decides WHERE a Later… click lands; until now nothing
    performed the landing. Every surface that offers the verb — commitment
    triage, My Plate, the End of Day — read the route and then hand-appended
    its own event from prose. A hand-append returns no writer result, so the
    Apply receipt had no `status` to derive an outcome from and booked every
    single `push to [date]` as an error (7 of the defect register's 93). The
    dispatch was also two subtly different hand-shapes in three places.

    One call now does both legs and says which one happened:

      {"status": "deferred", ...}  the item is the user's OWN, so its due date
        moves: a `commitment_updated` carrying `data.new_due`, which
        `cru_match.load_open_commitments` folds read-side (history is never
        rewritten). Classified OK.
      {"status": "snoozed", ...}   the item is owed TO the user (or unowned),
        so the date the counterparty owns is NOT touched: a `chat_dismissal`
        carrying `data.target_id` + `data.snooze_until`, the mute-ledger
        fields. The commitment stays OPEN and simply stops rendering until
        then. Classified OK — the write landed; the item is dealt with.
      {"status": "not_open", ...}  the commitment is closed. Deferring a
        tombstone moves nothing, so nothing is written (the same refusal
        `reassign_commitment` gives, and already never-optimistic).

    `when_iso` is REQUIRED and must already be RESOLVED — this writer does no
    date parsing, deliberately: `parse_later_when` owns the deterministic
    slice and the orchestrator owns natural language, and a writer that
    quietly accepted "sometime soon" would write a mute that never expires.
    Raises ValueError on anything unparseable, BEFORE the lock.

    RESOLVED MEANS ISO, NOT NECESSARILY A BARE DATE. A full offset-carrying
    timestamp is an accepted shape — the orchestrator's natural-language parse
    produces one ("tomorrow evening"), and the dispatch table routes that parse
    straight into this argument. The two legs then read it differently, and
    that split is the fix for review N-2:
      * the DUE DATE is the calendar day the user NAMED, taken in the offset
        their own input carried. Reading it off a UTC-normalized stamp instead
        moved every evening push west of UTC forward a day.
      * the MUTE EXPIRY is an instant, stored UTC like every other
        `snooze_until` in the ledger.
    `_later_when` returns both from one parse; neither leg re-derives the
    other's value.

    NOT IDEMPOTENT, and that is the design (review N-6). The snooze leg copies
    `mute_ledger.hold_item`'s event shape but does NOT route through it, so
    hold_item's no-extend guard — "a user repeating themselves must not
    silently extend the clock" — does not cover this path: pushing the same
    row twice writes two events and the later date wins. That is right for a
    verb whose whole content is a date the user chose, and wrong for a
    weak-evidence hold, which is why the two stay separate writers. Do not
    assume the ledger's guard applies here.

    Same guard set as its sibling lifecycle writers: id normalization over
    legacy spellings, loud CommitmentIdError on no match, scan→append inside
    the writer lock (R1c).
    """
    calendar_day, utc_stamp = _later_when(when_iso)
    from pathlib import Path as _Path
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    with events_writer_lock(events_path, holder=f"apply_later:{source_skill}"):
        index = _scan_commitment_index(events_path)
        cid = normalize_commitment_id(commitment_id, index)
        target = index["by_id"][cid]
        if _currently_closed(index, cid, target.get("seq")):
            return {"status": "not_open", "commitment_id": cid}
        route = later_route(target, actor_id)
        if route == "defer":
            data: dict = {
                "commitment_id": cid,
                # N-2: the day the user NAMED, in their own offset — never
                # a slice off the UTC stamp (that is the day-shift bug).
                "new_due": calendar_day,
                "pushed_by": actor_id,
                "reason": (reason or "")[:200],
            }
            if isinstance(target.get("seq"), int):
                data["commitment_seq"] = target["seq"]
            ev = {
                "type": "commitment_updated",
                "source_skill": source_skill,
                "primary_thread_id": target.get("primary_thread_id") or "",
                "data": data,
            }
        else:
            data = {
                "target_id": cid,
                "snooze_until": utc_stamp,
                "reason": (reason or "")[:200],
                "via": LATER_SNOOZE_VIA,
            }
            if surface:
                data["surface"] = surface
            # NO Loop-2 suppression identity here, and that is deliberate.
            # apply-choices Step 3f stamps `item_class` + `entity_id` +
            # `fingerprint` on the dismissals it writes so a repeated "no"
            # becomes learnable. A Later… is not a "no": the user picked a
            # DATE, and mining it as a suppression preference would teach a
            # rule they never stated. Worse, the fingerprint would be
            # class-wide — a commitment id is not one of the entity prefixes
            # `surface_preferences.normalize_dismissal` recognises, so three
            # deferrals of three DIFFERENT items would read as one repeated
            # refusal of a whole class. Leaving the identity off makes that
            # normalizer skip the row by construction, which is the honest
            # outcome. Suppression itself is unaffected: `live_mutes` and
            # `active_dismissal_target_ids` key on target_id + snooze_until.
            ev = {
                # `primary_thread_id` stays EMPTY here, exactly as
                # `mute_ledger.hold_item` writes it: a mute is view management,
                # and stamping the commitment's thread on it would make hiding
                # a row read as activity ON that project to every recency
                # surface that folds the envelope. The commitment id lives in
                # `data.target_id`, which is what the ledger keys on.
                "type": "chat_dismissal",
                "source_skill": source_skill,
                "primary_thread_id": "",
                "data": data,
            }
        from event_gate import append_event
        append_event(events_path, [ev], holder=source_skill)
    return {"status": "deferred" if route == "defer" else "snoozed",
            "commitment_id": cid, "route": route,
            "when": calendar_day if route == "defer" else utc_stamp,
            "event": ev}


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

# The four headline buckets, as ids. Spelled once so no surface invents its
# own spelling of a bucket name (F-47 P2b / F-56: four different open counts
# in one day came from each surface folding buckets its own way).
BUCKET_YOU_OWE = "you_owe"
BUCKET_OWED_TO_YOU = "owed_to_you"
BUCKET_UNOWNED = "unowned"
BUCKET_UNCONFIRMED = "unconfirmed"
HEADLINE_BUCKETS = (BUCKET_YOU_OWE, BUCKET_OWED_TO_YOU, BUCKET_UNOWNED,
                    BUCKET_UNCONFIRMED)


def bucket_of(commitment_event: dict,
              user_person_id: Optional[str]) -> str:
    """THE headline-bucket membership predicate for ONE top-level commitment.

    `count_commitments` counts by calling this; any surface that has to place
    a row in a bucket (SPEC_BOARD1's board tabs and pinned strips) calls the
    SAME function rather than re-deriving ownership. That is the whole point:
    a bucket count and a bucket membership that disagree is the F-56 defect
    class, and it can only be prevented structurally — one predicate, two
    readers.

    Returns one of `HEADLINE_BUCKETS`:

      unconfirmed  — pending_review (INTAKE 2026-07-31: a queue member, not
                     an open commitment; it counts in that ONE tile and
                     nowhere else, so it is checked FIRST)
      you_owe      — a resolvable owner that IS the primary user
      owed_to_you  — a resolvable owner that is someone else
      unowned      — no resolvable owner_id (extraction gap; still open)

    `user_person_id=None` (unresolvable primary user) degrades exactly as
    `count_commitments` documents: nothing matches the user, so every owned
    item reads as owed_to_you.

    Expects a TOP-LEVEL item (callers partition sub-items out first via
    `cru_match.partition_subitems`) — a sub-item is a step of a promise, not a
    promise, and never carries a bucket of its own.
    """
    if _is_pending_review(commitment_event):
        return BUCKET_UNCONFIRMED
    owner = _commitment_field(commitment_event, "owner_id")
    if owner and user_person_id and owner == user_person_id:
        return BUCKET_YOU_OWE
    if owner:
        return BUCKET_OWED_TO_YOU
    return BUCKET_UNOWNED


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
    _bucket = bucket_of  # THE membership predicate (see its docstring)
    # INTAKE — a pending_review item is a QUEUE MEMBER, not an open
    # commitment. It increments `unconfirmed` (the pointer count) and is
    # skipped from every other tally: direction buckets, overdue, undated,
    # by_kind, and the stuck/blocked classification input below. An
    # extractor's guess must not inflate a number the user reads as "how
    # many promises am I carrying".
    confirmed_top: list[dict] = []
    for ev in top_level:
        bucket = _bucket(ev, user_person_id)
        if bucket == BUCKET_UNCONFIRMED:
            unconfirmed += 1
            continue
        confirmed_top.append(ev)
        if bucket == BUCKET_YOU_OWE:
            you_owe += 1
        elif bucket == BUCKET_OWED_TO_YOU:
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


# ---------------------------------------------------------------------------
# WALKFIX1 Item E — THE unconfirmed vocabulary
# ---------------------------------------------------------------------------

# The renderer prose for a row whose title is missing entirely. Spelled once
# and shared by the widget and the board so the two surfaces cannot describe
# the same broken row differently. It says REPAIR because that is the action:
# a title-less row is a damaged record, not a row the user forgot to name.
UNTITLED_PLACEHOLDER = "(untitled — needs repair)"

# The escalation block's own name. The header now carries a reconciliation
# sentence after it, so the NAME is spelled here and the lane key below is what
# any consumer should identify the block by — a rendered sentence is a label,
# never an identifier.
UNCONFIRMED_SECTION_LABEL = "Unconfirmed"

# The stable, non-rendered key on the escalation section. Three shipped suites
# used to find that block by matching its title string; the moment the title
# gained a reconciliation they all broke, which is the point — a surface's
# rendered text is allowed to change and its identity is not.
UNCONFIRMED_LANE = "unconfirmed"


def unconfirmed_slices(*, queue_total, shown: int, escalated: int) -> dict:
    """THE derivation behind every unconfirmed number this product renders.

    Three surfaces show three different unconfirmed numbers, and on the
    2026-08-10 walk they read 198 / 110 / 89 with nothing on any of them
    saying which slice of what each one was. All three are correct; none of
    them was legible. The fix is vocabulary, not arithmetic — but the
    vocabulary only stays true if the three labels come from ONE computation,
    so this function owns all three.

    Inputs, all derived by the caller from the SAME bucketing pass:
      queue_total  the whole pending-review queue (the headline tile's value)
      shown        rows the escalation strip/section actually renders
      escalated    how many of those rows are in the unconfirmed BUCKET

    `shown - escalated` is the crossing set: rows the escalation selector
    pinned that the bucket predicate does not call unconfirmed — the ownerless
    escalation is the live case, and it is a verified non-bug, which is
    exactly why the label has to name it instead of quietly absorbing it.

    Returns the numbers plus the three rendered labels. `None`/unreadable
    `queue_total`, or a total that cannot be reconciled against what is shown,
    degrades every label to its plain form: a reconciliation of one number is
    noise and an invented total is worse than none.
    """
    try:
        shown = int(shown)
        escalated = int(escalated)
    except (TypeError, ValueError):
        shown, escalated = 0, 0
    if isinstance(queue_total, bool) or not isinstance(queue_total, int):
        queue_total = None
    crossing = max(0, shown - escalated)
    remainder = (queue_total - escalated) if queue_total is not None else None
    # Nothing left to reconcile — no remainder AND no crossing row — is the
    # degrade case, not a sentence: "5 rows — 5 escalated of 5" restates one
    # number three times. A negative remainder means the inputs disagree, and
    # an invented total is worse than none.
    degraded = (queue_total is None or remainder is None
                or remainder < 0 or (remainder == 0 and crossing == 0))

    out = {
        "queue_total": queue_total,
        "shown": shown,
        "escalated": escalated,
        "crossing": crossing,
        "remainder": remainder,
        "degraded": degraded,
    }
    if degraded:
        out["section_title"] = UNCONFIRMED_SECTION_LABEL
        out["board_heading_tail"] = f"({shown})"
        out["quick_read"] = None
        return out

    # "Unconfirmed — 110 rows, 109 escalated of 198, plus 1 unowned"
    #
    # The section KEEPS ITS NAME. The spec's draft header dropped the noun
    # ("110 rows — 109 escalated of 198, plus 1 unowned"), and on a surface
    # whose whole job here is legibility a header with no subject is a step
    # backwards: the section header is the only place the widget says what the
    # block IS. Same shape as the board strip's heading, so the two surfaces
    # read as one product.
    parts = [f"{shown} rows", f"{escalated} escalated of {queue_total}"]
    if crossing:
        parts.append(f"plus {crossing} unowned")
    out["section_title"] = f"{UNCONFIRMED_SECTION_LABEL} — " + ", ".join(parts)
    # The board strip pins only rows the strip itself renders, so its heading
    # is the same sentence without the crossing term.
    out["board_heading_tail"] = (
        f"{escalated} escalated of {queue_total} unconfirmed · "
        f"say `needs your call` for the rest") if remainder else f"({shown})"
    noun = ("extraction waiting" if remainder == 1 else "extractions waiting")
    out["quick_read"] = (
        f"{remainder} unconfirmed {noun} (not yet escalated; "
        f"{queue_total} total) — say `needs your call` to clear them."
        if remainder else None)
    return out


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
        function still does no I/O of its own — it forwards the path to
        callees that do. `None` is byte-identically pre-F-28.
        CLUSTCOUNT1 (2026-08-26) extends the forward: the same path also
        reaches `commitment_cluster.render_clusters`, so `counts["headline"]`
        gains `information_count` / `information_line` ("9 items, 41 rows")
        whenever the confirmed open set actually clusters. `None` here is
        also byte-identically pre-CLUSTCOUNT1 on this axis.

    Returns:
      {
        "counts": count_commitments(open_commitments, ...) — the canonical
            counting API's dict verbatim (total/you_owe/they_owe/unowned/
            stuck/undated/by_kind), with `counts["headline"]` additionally
            carrying `information_count` / `information_line` when
            CLUSTCOUNT1's clustering pass (above) found something to fold —
            absent otherwise, never a guessed pair of equal numbers,
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

    # CLUSTCOUNT1 (REVIEW_PR62 F-3 / DEV-1) — the bookend counters learn the
    # information count. CLUSTER1 made every QUEUE surface state one line per
    # real-world item; the two bookends (end-of-day, morning brief) could not
    # follow it in that build because `count_commitments` is a pure reader
    # over an already-loaded list, with no workspace in hand to run the
    # clusterer. THIS function already holds `workspace_root` (F-28, for
    # Path 5's roster read) — the SAME clusterer the queues consult
    # (`commitment_cluster.render_clusters` / `information_count`) runs here,
    # over the SAME confirmed top-level set `count_commitments` just counted:
    # `top_level_commitments` (above) minus pending-review, via
    # `_is_pending_review` — the one predicate `bucket_of` itself gates on,
    # reused rather than re-derived, so this set is byte-for-byte
    # `count_commitments`'s own `confirmed_top` (headline["total"] counts it).
    #
    # Additive and drop-empty, the CLUSTER1 contract one level up: the keys
    # land on `counts["headline"]` ONLY when `workspace_root` is supplied AND
    # something actually clusters. A workspace with no clusters — or a caller
    # that still hands over no `workspace_root` — renders BYTE-IDENTICAL to
    # pre-CLUSTCOUNT1 output; `render_clusters` is the same defensive wrapper
    # every queue calls, so a clustering failure degrades to nothing here too,
    # never to a broken bookend.
    if workspace_root is not None:
        try:
            from commitment_cluster import information_count, render_clusters

            confirmed_top = [ev for ev in top_level_commitments
                             if not _is_pending_review(ev)]
            clusters = render_clusters(confirmed_top,
                                       workspace_root=workspace_root,
                                       now_iso=now_iso)
            if clusters:
                n_rows = len(confirmed_top)
                n_items = information_count(n_rows, clusters)
                noun_items = "item" if n_items == 1 else "items"
                noun_rows = "row" if n_rows == 1 else "rows"
                counts["headline"]["information_count"] = n_items
                counts["headline"]["information_line"] = (
                    f"{n_items} {noun_items}, {n_rows} {noun_rows}")
        except Exception as exc:  # pragma: no cover — a bookend must render
            import sys as _sys
            _sys.stderr.write(
                f"[commitment_state] bookend clustering skipped: {exc}\n")

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
        # SPEC OVERDUE1 DD-3 — the overdue-ask mark rides the lane row, so the
        # End of Day's slipped block can stay a PURE function of the rows it is
        # handed (it does no I/O and must not start). Present only when the
        # item actually carries one, the same absent-not-empty rule the SUB1
        # stamps above follow: a row with no key has never been asked about.
        # The value is the projection's own fold verbatim — nothing here
        # re-derives it, and nothing here decides what it means.
        asked_mark = asked_mark_of(ev)
        if asked_mark is not None:
            row["asked"] = dict(asked_mark)
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


def compute_and_log_brief_state(workspace_root, *, source_skill="morning-briefing",
                                fired_via="manual", **kwargs):
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

    BRIEFFIX1 Item C (2026-08-09, second-eyes F1) — the event now records WHICH
    KIND OF FIRE wrote it. It is emitted on BOTH paths (the scheduled
    orchestrator's driver and the on-demand "brief me"), and before this it
    carried no discriminator at all: no `fired_via`, no mode, and the same
    `source_skill` either way. Anything reading it therefore could not tell a
    hand-run brief from a scheduled one — which is how the receipt-ordering
    check came to read every manual brief as the defect it was built to catch.

    The default is `manual` on purpose, per RECEIPT_CONTRACT § Run-mode
    detection: an unlabelled fire is treated as the interactive one, because
    mislabelling a manual costs a missing note while mislabelling a scheduled
    fabricates history (F-47 P1a). Callers that KNOW pass the value.
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

    def _normalize_fired_via(value):
        """The canonical spelling, or `manual` when the value means nothing.
        Import-tolerant: the audit write must never be what breaks a brief."""
        try:
            from receipts import FIRED_VIA, normalize_fired_via
            via = normalize_fired_via(value)
            return via if via in FIRED_VIA else "manual"
        except Exception:  # noqa: BLE001
            return value if value in ("scheduled", "manual", "catchup") else "manual"

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
                # Which kind of fire computed this (BRIEFFIX1 Item C / F1).
                # Normalized through the receipt vocabulary so `catchup`,
                # `user-trigger` and the rest land in one spelling.
                "fired_via": _normalize_fired_via(fired_via),
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

# CLOSEID1 — how a closer established WHICH commitment it is closing. Not a
# confidence score and not a match type: `id` and `number` both mean the target
# arrived already resolved (a widget's embedded `data.id`; a row on a receipt
# whose positions this fire rendered itself), `title` means a human's words
# were turned into an id by matching, which is the one door the wrong-close
# came through. `session` (CLOSEID2) means the session resolved the id ITSELF
# — its own scan of the substrate, its own hand-built picker — and that door
# does not exist: the writer refuses it unconditionally, because a
# session-resolved id never inherits the pre-confirmed status of a
# surface-resolved one.
RESOLVED_BY_MATCH_VALUES = ("id", "number", "title", "session")
MATCH_KEY = "resolved_by_match"

# CLOSEID2 — the SESSION LANE: skills whose closes arrive from an ad-hoc chat
# turn, where the "surface" is the session itself. In this lane an unstated
# resolved_by_match cannot mean "the surface embedded the id" — there is no
# surface but the model — so the writer refuses silence instead of letting it
# inherit the id-keyed door (the 2026-08-24 Skip closed three live items
# through exactly that silence). Membership is deliberately the catch-all
# alone: every other closer's ids arrive from queue rows, numbered receipts,
# watch entries, or widget payloads (sweep, SPEC_CLOSEID2 §3), and adding a
# skill here is a statement that its closes have no surface anchor either.
SESSION_RESOLVED_SOURCES = frozenset({"workspace-manager"})
# DD-3 — how many candidates the ONE ambiguity row lists. Past three, a "which
# of these did you mean" list stops being an answer and becomes the list the
# user was already looking at.
MAX_AMBIGUOUS_CANDIDATES = 3

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


class AmbiguousTargetError(ValueError):
    """Refused to close a commitment that was picked by NAME rather than by id
    (CLOSEID1). The 2026-08-22 End of Day fire closed one item against evidence
    that belonged to another, reopened it, and closed the right one — three
    ledger events in one fire — because a first-hit name match was allowed to
    close. Identity comes from the id the surface already resolved, or from
    exactly one unambiguous open match that the user confirmed; everything
    weaker PROPOSES (`propose_ambiguous_close`) and writes nothing to the
    commitment.

    Carries `candidates` — the open items the name matched — so the caller can
    say WHICH ones it saw instead of apologising in the abstract.
    """

    def __init__(self, message: str, candidates: Optional[list] = None,
                 query: str = ""):
        super().__init__(message)
        self.candidates = list(candidates or [])
        self.query = query or ""


def _closer_target_id(ev: dict) -> str:
    """The id a commitment_resolved / thread_resolved / commitment_superseded
    event closes. Since BUG-8330 item 1 this IS load_open_commitments' chain —
    both sides import closure_index, so the mirror holds by construction
    instead of by comment."""
    from closure_index import closer_target_id
    return closer_target_id(ev)


def _closer_target_seqs(ev: dict) -> list[int]:
    """The F3 amnesty half of the closer chain (Stage C): seqs a closure
    references via `data.commitment_seq` / `data.source_event_seq`. Shared
    fold — see `_closer_target_id`."""
    from closure_index import closer_target_seqs
    return closer_target_seqs(ev)


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
    from closure_index import ClosureIndex
    from pathlib import Path as _Path

    by_id: dict[str, dict] = {}
    by_seq: dict[int, dict] = {}
    # BUG-8330 item 1: closure/reopen state folds through the SHARED
    # closure_index (the same object load_open_commitments builds), so the
    # write-side pre-flight can never drift from the read-side projection.
    # The four *_at keys stay in the returned dict for shape compat — they
    # are the ClosureIndex's own maps.
    closure = ClosureIndex()
    kind_by_id: dict[str, str] = {}
    kind_by_seq: dict[int, str] = {}
    superseded_onto: dict[str, str] = {}
    children_of: dict[str, list] = {}

    def _index_dict() -> dict:
        return {"by_id": by_id, "by_seq": by_seq, "closure": closure,
                "closed_ids_at": closure.closed_ids_at,
                "closed_seqs_at": closure.closed_seqs_at,
                "reopened_ids_at": closure.reopened_ids_at,
                "reopened_seqs_at": closure.reopened_seqs_at,
                "kind_by_id": kind_by_id, "kind_by_seq": kind_by_seq,
                "superseded_onto": superseded_onto, "children_of": children_of}

    p = _Path(events_jsonl_path)
    if not p.exists():
        return _index_dict()
    events, _skipped = load_events_defensively(p)
    for idx, ev in enumerate(events):
        et = ev.get("type") or ev.get("event") or ""
        d = ev.get("data") or {}
        closure.fold(idx, ev)
        if et == "commitment":
            by_id[_commitment_id(ev)] = ev
            seq = ev.get("seq")
            if isinstance(seq, int):
                by_seq[seq] = ev
            pid = d.get("parent_id")
            if isinstance(pid, str) and pid.strip():
                children_of.setdefault(pid.strip(), []).append(ev)
        elif et == "commitment_superseded" and not d.get("split_into"):
            # SUB1 D3b — merge re-point map (mirrors the loader's merged_onto
            # fold): a non-split supersession transfers the closed parent's
            # children to the survivor read-side. Split closers stay skipped.
            cid = _closer_target_id(ev)
            survivor = d.get("superseded_by") or d.get("survivor_id")
            if cid and survivor:
                superseded_onto[str(cid)] = str(survivor)
        elif et == "commitment_reclassified":
            new_kind = d.get("new_kind") or d.get("new_type")
            target = d.get("target_id") or d.get("commitment_id")
            if new_kind and target:
                kind_by_id[str(target)] = new_kind
            v = d.get("target_seq")
            if new_kind and isinstance(v, int) and not isinstance(v, bool):
                kind_by_seq[v] = new_kind

    return _index_dict()


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
    This IS load_open_commitments' per-commitment math — both delegate to
    the shared closure_index fold (BUG-8330 item 1), so the two can never
    disagree."""
    return index["closure"].is_closed(cid, seq)


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


_TITLE_PUNCT_RE = _re.compile(r"[^0-9a-z]+")


def _title_key(value) -> str:
    """Normalized comparison key for a commitment title: casefolded, every
    non-alphanumeric run collapsed to one space, ends trimmed. Deliberately
    NOT a fuzzy matcher — this key decides whether two strings are the SAME
    title, and every loosening of it widens the wrong-close blast radius."""
    return _TITLE_PUNCT_RE.sub(" ", str(value or "").casefold()).strip()


def _title_tokens(value) -> list[str]:
    key = _title_key(value)
    return [t for t in key.split(" ") if t]


def _title_candidate(ev: dict) -> dict:
    return {
        "id": _commitment_id(ev),
        "title": _commitment_field(ev, "title") or "",
        "owner_id": _commitment_field(ev, "owner_id") or "",
        "due": _commitment_field(ev, "due") or "",
    }


def resolve_commitment_by_title(index, text, *, open_only: bool = True) -> dict:
    """Resolve a user's WORDS to a commitment — or refuse, with the candidates.

    Returns `{"ok": bool, "id": str|None, "candidates": [{id, title, owner_id,
    due}], "query": str, "stage": "exact"|"token"|None}`.

    CLOSEID1 DD-1. This is the resolver every name-keyed close must go through,
    and its contract is the whole point: **it never returns a first hit.**
    - exact-normalized title match first (`_title_key`); if that stage hits at
      all, it is the ONLY stage consulted — a weaker match can never outvote an
      exact one;
    - otherwise a token match: every token of the query must appear in the
      title's tokens (so a bare first name matches every item that names that
      person, which is exactly the ambiguity we want SEEN);
    - exactly one hit -> `ok: True` with its id;
    - two or more -> `ok: False` and every candidate, because "the first one"
      is how the 2026-08-22 fire closed the wrong promise;
    - zero -> `ok: False`, empty candidates.

    `index` is the `_scan_commitment_index` working set, or a workspace root
    (str/Path) to scan — prose callers hold a workspace, not an index, and a
    resolver they cannot call from the surface that needs it is a resolver that
    gets re-implemented inline.

    `open_only=True` (default) considers only commitments that are currently
    open: a closed item is not something a name may re-close, and leaving them
    in would let old history manufacture ambiguity forever.
    """
    if not isinstance(index, dict):
        from pathlib import Path as _P
        index = _scan_commitment_index(_P(index) / "_hq" / "data" / "events.jsonl")

    query = str(text or "").strip()
    out = {"ok": False, "id": None, "candidates": [], "query": query,
           "stage": None}
    q_key = _title_key(query)
    q_tokens = _title_tokens(query)
    if not q_key or not q_tokens:
        return out

    pool: list[dict] = []
    for cid, ev in index["by_id"].items():
        if open_only:
            status = _commitment_field(ev, "status") or "open"
            if status not in ("open", "overdue"):
                continue
            if _currently_closed(index, cid, ev.get("seq")):
                continue
        pool.append(ev)

    exact = [ev for ev in pool
             if _title_key(_commitment_field(ev, "title")) == q_key]
    if exact:
        hits, stage = exact, "exact"
    else:
        hits, stage = ([ev for ev in pool
                        if set(q_tokens) <= set(_title_tokens(
                            _commitment_field(ev, "title")))],
                       "token")
    if not hits:
        return out

    out["stage"] = stage
    out["candidates"] = [_title_candidate(ev) for ev in hits]
    if len(hits) == 1:
        out["ok"] = True
        out["id"] = _commitment_id(hits[0])
    return out


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
    resolved_by_match: Optional[str] = None,
    title_candidates: Optional[int] = None,
    title_query: Optional[str] = None,
    source_ref=None,
    mint_now_iso=None,
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
      resolved_by_match: CLOSEID1/CLOSEID2 — HOW the caller picked this
        target. `"id"` (the surface embedded it), `"number"` (a row on a
        receipt whose positions this fire itself rendered), `"title"` (the
        user's words), `"session"` (the session resolved the id itself), or
        None = unstated, the pre-CLOSEID1 default that every programmatic
        closer still writes byte-identically. Two values are policed by
        REFUSING: `"title"` closes only when the user confirmed it AND the
        name resolved to exactly one open item; `"session"` NEVER closes —
        a session-resolved id takes the one-question path
        (`propose_ambiguous_close`) or re-enters through the user's words
        (`resolve_commitment_by_title` + the title door). And in the session
        lane (`SESSION_RESOLVED_SOURCES` — the chat catch-all, where the
        "surface" is the session itself) None refuses too: silence no longer
        inherits the id-keyed door there, the caller must state which door
        it came through. Every refusal raises AmbiguousTargetError with the
        candidates and writes nothing. When stated, the value is stamped on
        `data.resolved_by_match` so the ledger records how identity was
        established, not just that something closed.
      title_candidates: how many open items the caller's name match hit. Only
        read when `resolved_by_match="title"`; anything but 1 refuses.
      title_query: the user's actual WORDS, when the caller has them. This is
        the difference between a chokepoint and an honor system: given the
        query, this writer re-derives the candidate set itself through
        `resolve_commitment_by_title` and refuses on what IT finds, so a caller
        that asserts "one match" while its own first-hit logic looked past a
        second one is refused anyway. That caller-asserted count is exactly the
        shape of the prose contract that failed on 2026-08-22. Optional, and
        strictly stricter: omit it and only `title_candidates` is consulted.
      source_ref: PROV1 — the pointer back to the thing that justified this
        close, canonicalized through Layer A4 before append
        (`gmail:<message-id>`, `granola:<meeting-id>`, either Slack spelling,
        `session:<receipt-id>` for a human/chat close). A close is NEVER
        blocked for want of one — and since PROVMINT1 it is never UNSOURCED
        either: pass nothing and this writer MINTS `session:<source_skill>:
        <now>` and stamps `ref_grain: "surface_minted"` beside it, so the
        event points at the act that closed it (who, and when) and says
        out loud that that is all it points at. `provenance_missing` is
        unreachable from this writer. Pass a real artifact pointer whenever
        you hold one: the mint is the FLOOR, never the ceiling, and a
        caller-passed ref wins with no grain marker.
        A MALFORMED pointer is
        refused loudly (SourceRefError) before the writer lock is taken —
        garbage in `data.source_ref` is worse than no pointer, because every
        reader downstream treats that key as resolvable evidence. The cascade
        children inherit the parent's pointer: the parent's evidence is what
        justified closing them.
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
      mint_now_iso: PROVMINT1 — the GESTURE's instant, for the minted fallback
        only (the event's own `ts` still comes from the append gate; this never
        touches it). A caller performing ONE act over MANY ids reads the clock
        once and passes it to every close, so the batch carries one receipt
        instead of N — one gesture, one act. Omit it and the writer reads the
        clock itself, which is right when the write IS the gesture. Ignored
        entirely when a real pointer is supplied.

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
    if (resolved_by_match is not None
            and resolved_by_match not in RESOLVED_BY_MATCH_VALUES):
        raise ValueError(
            f"invalid resolved_by_match {resolved_by_match!r} "
            f"(allowed: {RESOLVED_BY_MATCH_VALUES}, or None when unstated)"
        )
    # PROV1 — resolve the pointer BEFORE the lock. A malformed ref refuses with
    # nothing written and no lock held; a missing one is MINTED here (PROVMINT1),
    # once, for the parent and every cascade child — so a cascade reads as one
    # act with one receipt rather than N receipts one second apart.
    pointer_fields = _close_pointer_fields(source_ref, extra_data,
                                           mint_for=source_skill,
                                           now_iso=mint_now_iso)
    from pathlib import Path as _Path
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"

    with events_writer_lock(events_path, holder=f"close_commitment:{source_skill}"):
        index = _scan_commitment_index(events_path)
        cid = normalize_commitment_id(commitment_id, index)
        target = index["by_id"][cid]

        # CLOSEID1 DD-2 — a target picked by NAME does not get to close itself.
        # This sits BEFORE the already-closed check on purpose: a caller that
        # guessed at the target should be told it guessed, not handed a cheerful
        # "already done" about an item it never identified.
        if resolved_by_match == "title":
            # F-3 — an empty-but-PRESENT query is not "no query". `""` and
            # `"   "` are falsy, and letting them fall through to the
            # caller-asserted count would hand the door straight back to the
            # honor system this guard exists to replace — a caller whose match
            # produced nothing would look identical to one that never matched.
            # Present-and-blank is its own refusal; only None means "not
            # supplied".
            if title_query is not None and not str(title_query).strip():
                raise AmbiguousTargetError(
                    f"refusing to close {cid!r}: resolved_by_match='title' "
                    "with an EMPTY query. A blank string is not the absence of "
                    "a name match, it is a name match that resolved nothing — "
                    "pass the user's actual words, or do not claim a title "
                    "resolution.",
                    candidates=[], query="")
            derived = (resolve_commitment_by_title(index, title_query)
                       if title_query else None)
            candidates = list(derived["candidates"]) if derived else []
            n_hits = (len(candidates) if derived is not None
                      else title_candidates)
            named = f" for {title_query!r}" if title_query else ""
            if not user_confirmed:
                raise AmbiguousTargetError(
                    f"refusing to close {cid!r}{named}: the target was resolved "
                    "from a NAME and nobody confirmed it. A name match may "
                    "propose (propose_ambiguous_close) — only an explicit user "
                    "confirmation may close one.",
                    candidates=candidates, query=title_query or "")
            if n_hits is None:
                raise AmbiguousTargetError(
                    f"refusing to close {cid!r}{named}: resolved_by_match="
                    "'title' but the caller reported no candidate count. Pass "
                    "title_candidates (and title_query when you have the "
                    "user's words) from resolve_commitment_by_title — an "
                    "unmeasured name match is the first-hit close by another "
                    "name.",
                    candidates=candidates, query=title_query or "")
            if int(n_hits) != 1:
                raise AmbiguousTargetError(
                    f"refusing to close {cid!r}{named}: the name matched "
                    f"{int(n_hits)} open items, not one. Nothing was written — "
                    "propose them instead (propose_ambiguous_close) and let "
                    "the user say which they meant.",
                    candidates=candidates, query=title_query or "")
            if derived is not None and derived.get("id") != cid:
                raise AmbiguousTargetError(
                    f"refusing to close {cid!r}{named}: the caller's own words "
                    f"resolve to {derived.get('id')!r}, not to the id it asked "
                    "to close. The target and the evidence disagree — this is "
                    "the 2026-08-22 wrong-close shape exactly.",
                    candidates=candidates, query=title_query or "")

        # Order-aware, cross-keyed CURRENT state (Stage C seq-alias mirror +
        # Stage D reopen awareness): a closed-then-reopened commitment is open
        # again and MAY be re-closed.
        if _currently_closed(index, cid, target.get("seq")):
            return {"status": "already_resolved", "commitment_id": cid}

        # CLOSEID2 — the rule, named: a close whose ids were resolved by the
        # SESSION rather than by a rendered surface requires the same
        # one-question path a name match does. The 2026-08-24 chat turn
        # scanned the substrate itself, rendered its own picker, and Skip —
        # the decline-to-answer control — closed all three candidates,
        # because an unstated resolved_by_match inherited the id-keyed door
        # by default. Two clauses close that — and they sit AFTER the
        # already-closed check, unlike the title guard above: a name-picked
        # close is a GUESS about identity and must be told so, but a bare or
        # session-labeled id is a missing/failed label on a target the caller
        # named exactly, and the documented already_resolved no-op ("that one
        # was already closed") stays reachable for the session lane's
        # legitimate double-tap retries. Nothing is written on either path.
        if resolved_by_match == "session":
            raise AmbiguousTargetError(
                f"refusing to close {cid!r}: the session resolved this id "
                "itself, and a session-resolved id never inherits the "
                "pre-confirmed status of a surface-resolved one (CLOSEID2). "
                "Route the user's words through resolve_commitment_by_title "
                "and close on its singular confirmed answer, or ask the one "
                "question (propose_ambiguous_close, passing "
                "resolved_by_match='session' so the row says what it is). A "
                "decline on that question — Skip, dismiss, timeout, empty "
                "submit — is NO ACTION, never a close.",
                candidates=[], query="")
        if resolved_by_match is None and source_skill in SESSION_RESOLVED_SOURCES:
            raise AmbiguousTargetError(
                f"refusing to close {cid!r}: {source_skill!r} is the session "
                "lane, where an unstated resolved_by_match means the model "
                "picked the target itself (CLOSEID2) — silence does not "
                "inherit surface trust here. State the door ONLY IF IT IS "
                "TRUE: resolved_by_match='id' (a widget embedded this id), "
                "'number' (the user said a row number from a receipt this "
                "session rendered), or 'title' with title_query=<the user's "
                "words>. An id you resolved yourself has no door — declare "
                "'session' or take the one-question path "
                "(propose_ambiguous_close) instead; never claim a surface "
                "that did not render.",
                candidates=[], query="")

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
                        # EODFIX1 — the title SNAPSHOT (see the parent's note).
                        **_title_snapshot(k),
                        # PROV1 — a cascade child is closed on the PARENT's
                        # evidence, so it carries the parent's pointer (or the
                        # parent's missing-marker). A child tombstone with no
                        # provenance at all would be the least auditable close
                        # in the system: nobody typed it and nothing cites it.
                        **pointer_fields,
                    },
                })
            _append_ev(events_path, child_closers, holder=source_skill)

        data = dict(extra_data) if isinstance(extra_data, dict) else {}
        data.update({
            "commitment_id": cid,
            "resolved_by": resolved_by,
            "evidence": clip(evidence),
            "resolution": resolution,
            # EODFIX1 — the title SNAPSHOT. Additive, never required: every
            # other win writer stamps the name of the thing it moved, and this
            # one did not, so the End of Day's wins block could only ever see
            # a close by joining back to the commitment. The join stays (it is
            # the half that covers the closes already on disk and any writer
            # that is not this function); the snapshot is what makes a close
            # written from here legible without one.
            **_title_snapshot(target),
        })
        # CLOSEID1 — HOW identity was established, recorded beside the close.
        # Popped first for the same reason the PROV1 pointer is: this is a
        # statement THIS writer makes about how it was called, so a caller must
        # not be able to smuggle one in through extra_data and make a
        # name-picked close read as an id-keyed one. Omitted entirely when the
        # caller did not state it, so every existing closer writes the
        # byte-identical event it wrote before.
        data.pop(MATCH_KEY, None)
        if resolved_by_match is not None:
            data[MATCH_KEY] = resolved_by_match
        # PROV1 last: the CANONICAL pointer wins over whatever spelling arrived
        # through extra_data, and the marker can never sit next to a real ref.
        # PROVMINT1: the GRAIN marker is popped with them — it is a statement
        # this writer makes about its OWN pointer, so a caller must not be able
        # to hand one in beside a real ref and make an artifact pointer read as
        # a mint (or the reverse).
        data.pop(PROVENANCE_MISSING_KEY, None)
        data.pop(SOURCE_REF_KEY, None)
        data.pop(REF_GRAIN_KEY, None)
        data.update(pointer_fields)
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


def ambiguous_close_sentence(query: str, candidates: list) -> str:
    """The one sentence a refused name-close says out loud (CLOSEID1 DD-3).

    Named and returned rather than described in prose because the refusal is
    the FEATURE: a fire that silently proposes instead of closing looks, to the
    CEO, exactly like a fire that did nothing.
    """
    n = len(candidates or [])
    q = str(query or "").strip()
    if n == 0:
        return (f"Nothing open matches '{q}', so I did not close anything. "
                "Tell me which item you mean and I will close that one.")
    word = {1: "One", 2: "Two", 3: "Three"}.get(n, str(n))
    plural = "item" if n == 1 else "items"
    where = "it" if n == 1 else ("both" if n == 2 else "all of them")
    return (f"{word} open {plural} match '{q}' — I have put "
            f"{where} in needs your call rather than guess.")


AMBIGUITY_EVIDENCE_SHAPE = (
    "a title match on '{query}' — {n} open items matched it, so this is a "
    "question about which one, not a close"
)


def propose_ambiguous_close(
    workspace_root,
    *,
    query: str,
    source_skill: str,
    evidence: str = "",
    candidates: Optional[list] = None,
    proposed_resolution: str = "auto_resolve",
    evidence_ts: Optional[str] = None,
    source_ref=None,
    max_candidates: int = MAX_AMBIGUOUS_CANDIDATES,
    resolved_by_match: str = "title",
) -> dict:
    """Where a refused name-close LANDS (CLOSEID1 DD-3, reshaped by review).

    **ONE question, not N.** An ambiguity is a single either/or — "two open
    items match what you said; which did you mean?" — carried on ONE
    `commitment_review_proposed` row that names every candidate. The first
    build wrote one row per candidate, which is three things wrong at once: it
    is net-new INFLOW into a queue already measured as an inflow problem (436
    open, 25/day in against 8/day out); it presents an either/or as N
    independent yes/no questions with the SAME evidence attached and nothing on
    screen saying only one can be true; and a `confirm all` over that pile
    closes BOTH candidates, which is the wrong close returning one hop later
    through the bulk path. One row makes the ask count 1, so the trade is
    honestly "one question replaces one wrong close plus a reopen plus a
    re-close".

    **The row cannot be swept.** Its evidence states, in the words the fence
    reads, that it is a title match — so `watch_gate.weakness_reason` returns
    `TITLE_MATCH_REASON` and `screen_bulk_accept` HOLDS it in every bulk
    gesture. `has_completion_signal=False` holds it a second, independent way.
    Neither is decoration: this row IS a title match, and nothing assessed
    completion for any single candidate. The only way past the fence is a human
    naming that row's own number, which is `screen_bulk_accept`'s
    `individually_named` escape hatch working exactly as designed.

    **`data.commitment_id` is an identity ANCHOR, never a close target.** The
    queue keys rows by commitment id, so the row needs one; the ambiguity does
    not belong to any single commitment. Readers that mean to act on this row
    read `ambiguous_candidates` — the full [{id, title}] set — and ask the
    user. A reader that closes the anchor because it is the anchor has
    reintroduced the first-hit close.

    It touches NOTHING on the commitments: no tombstone, no marker, no reopen.

    Returns `{"status", "query", "candidates", "anchor_id", "proposed",
    "truncated", "rows", "sentence", "event"}`.
    """
    from pathlib import Path as _Path
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    index = _scan_commitment_index(events_path)
    if candidates is None:
        candidates = resolve_commitment_by_title(index, query)["candidates"]
    candidates = list(candidates or [])

    # An either/or needs two sides. A cap of 1 would truncate a real
    # ambiguity down to its FIRST hit, and a one-candidate row renders as
    # "close this one?" against the anchor — the first-hit close this
    # whole build exists to kill, resurfaced as a proposal. Any positive
    # cap floors at 2; 0 still means "propose nothing".
    cap = max(2, int(max_candidates)) if int(max_candidates) > 0 else 0
    listed = candidates[:cap]
    truncated = max(0, len(candidates) - len(listed))

    out = {
        "status": "nothing_to_propose",
        "query": str(query or ""),
        "candidates": candidates,
        "anchor_id": None,
        "proposed": [],
        "truncated": truncated,
        "rows": 0,
        "sentence": ambiguous_close_sentence(query, candidates),
        "event": None,
    }
    carried = [
        {"id": str(c.get("id") or "").strip(),
         "title": str(c.get("title")
                      or _commitment_field(index["by_id"].get(
                          str(c.get("id") or "").strip()) or {}, "title") or "")}
        for c in listed if str(c.get("id") or "").strip()
    ]
    if not carried:
        return out

    anchor = carried[0]["id"]
    target = index["by_id"].get(anchor) or {}

    try:
        from cru_match import build_pending_review_event
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(_Path(__file__).resolve().parent))
        from cru_match import build_pending_review_event

    # The evidence the FENCE reads. `weakness_reason` looks for the title-match
    # marker in this text and nothing else, so the row's own honest description
    # of itself is what holds it — not a flag some renderer might ignore.
    fence_evidence = AMBIGUITY_EVIDENCE_SHAPE.format(
        query=str(query or "").strip(), n=len(carried))
    if evidence:
        fence_evidence = f"{fence_evidence}. What was said: {clip(evidence)}"

    ev = build_pending_review_event(
        commitment_id=anchor,
        primary_thread_id=target.get("primary_thread_id") or "",
        source_skill=source_skill,
        proposed_resolution=proposed_resolution,
        # No scored rail produced this — a string hit more than one promise.
        # Claiming a score would let an accept surface read a number no
        # matcher computed.
        score=0.0,
        evidence=fence_evidence,
        next_seq=None,
        title=carried[0]["title"],
        # The caller assessed completion for the PHRASE, never for a single
        # candidate. False is the honest answer and it holds the row a second
        # way, independent of the evidence text.
        has_completion_signal=False,
        evidence_ts=evidence_ts,
    )
    data = dict(ev.get("data") or {})
    # CLOSEID2 — the row says how its CANDIDATES were resolved. "title" (the
    # default, and every pre-CLOSEID2 caller's truth): a human's words were
    # matched. "session": the session's own scan produced them — the one
    # place that value ever reaches disk, so an audit can tell a
    # model-resolved ambiguity from a human one. The bulk fence is untouched
    # either way: `weakness_reason` reads the EVIDENCE TEXT, not this key.
    if resolved_by_match not in ("title", "session"):
        raise ValueError(
            f"invalid resolved_by_match {resolved_by_match!r} for an "
            "ambiguity row (allowed: 'title', 'session') — 'id' and 'number' "
            "are surface doors, and a surface-resolved id is never ambiguous")
    data[MATCH_KEY] = resolved_by_match
    data["ambiguous_query"] = clip(str(query or ""))
    # The LIST, not a count: a reader that must ask "which did you mean" needs
    # the ids and the titles. A bare count told nobody anything, which is why
    # it had no reader.
    data["ambiguous_candidates"] = carried
    data["ambiguous_truncated"] = truncated
    data["auto_close_blocked"] = True
    if source_ref is not None:
        data[SOURCE_REF_KEY] = source_ref
    ev["data"] = data

    from event_gate import append_event
    append_event(events_path, [ev], holder=source_skill)

    out.update({
        "status": "proposed",
        "anchor_id": anchor,
        "proposed": [c["id"] for c in carried],
        "rows": 1,
        "event": ev,
    })
    return out


def resolve_thread(
    workspace_root,
    target_id,
    *,
    source_skill: str,
    kind: str = "unknown",
    source_artifact: Optional[str] = None,
    resolved_by: Optional[str] = None,
    primary_thread_id: Optional[str] = None,
    extra_data: Optional[dict] = None,
    source_ref=None,
) -> dict:
    """THE `thread_resolved` writer (PROV1).

    `thread_resolved` is the third member of the closer family
    (`closure_index.CLOSER_TYPES`) and was the only one with no writer in
    code: log-resolution hand-composed the JSON in prose, so the one closer
    that most often fires from a raw dashboard click was also the one nothing
    could stamp a pointer onto. This function is that writer — same envelope
    the skill has always emitted (`data.id` / `data.kind` /
    `data.source_artifact`), now with the close-family pointer contract.

    Deliberately NOT a commitment closer: a commitment closes through
    `close_commitment`, which normalizes legacy id spellings, refuses orphan
    tombstones, and honors the pending_review floor. This writer covers the
    non-commitment kinds (meeting, inbox, priority) and the back-compat
    `thread_resolved` twin log-resolution still appends after a canonical
    close. The append gate keeps the wall up either way: a `thread_resolved`
    whose target CLAIMS the commitment namespace and resolves to nothing is
    refused at append time (`event_gate._check_closure_resolves`).

    `source_ref`: the pointer that justified the resolve — the dashboard
    click's session/receipt id (`session:<id>`), or the message / meeting the
    artifact was built from. Absent → `provenance_missing: true`, never a
    blocked write; malformed → SourceRefError before anything is appended.

    Returns {"status": "resolved", "target_id": <str>, "event": {...}}.
    """
    tid = str(target_id or "").strip()
    if not tid:
        raise ValueError(
            "resolve_thread needs a target id — a thread_resolved event that "
            "names nothing closes nothing and is a dead letter by construction"
        )
    pointer_fields = _close_pointer_fields(source_ref, extra_data)

    from pathlib import Path as _Path
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"

    data = dict(extra_data) if isinstance(extra_data, dict) else {}
    data.update({
        "id": tid,
        "kind": (kind or "unknown").strip() or "unknown",
        "source_artifact": source_artifact,
    })
    if resolved_by:
        data["resolved_by"] = resolved_by
    data.pop(PROVENANCE_MISSING_KEY, None)
    data.pop(SOURCE_REF_KEY, None)
    data.pop(REF_GRAIN_KEY, None)
    data.update(pointer_fields)

    ev = {
        "type": "thread_resolved",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id or "",
        "data": data,
    }
    from event_gate import append_event
    append_event(events_path, [ev], holder=source_skill)
    return {"status": "resolved", "target_id": tid, "event": ev}


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
    source_ref=None,
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

    PROV1 — `source_ref` is the pointer to what justified THIS merge (the
    sweep receipt / session id for a user-confirmed merge, the gate's batch for
    an auto-merge), canonicalized through Layer A4 and stamped on the event;
    nothing passed lands `provenance_missing: true`. It is a different fact
    from `data.merged_source_refs`, which is the union of the two ABSORBED
    items' own capture provenance and is left exactly as history wrote it —
    conflating the two would rewrite the merged items' capture story into a
    claim about the merge decision.
    """
    if (brain_batch_id is None) != (brain_change_class is None):
        raise ValueError(
            "brain_batch_id and brain_change_class travel together — a batch id "
            "with no change class is a row brain_undo skips, i.e. an undo handle "
            "that looks present and reverses nothing"
        )
    # PROV1 — refuse a malformed pointer before the lock (same posture as
    # close_commitment); an absent one becomes the marker.
    pointer_fields = _close_pointer_fields(source_ref)
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
        # PROV2: two case-variant spellings of ONE artifact are one provenance
        # entry, so the union dedups on the DERIVED identity while storing each
        # pointer's own bytes — the stored ref stays resolvable, the list stays
        # honest about how many distinct sources are folded in.
        refs: list[str] = []
        ref_keys: set = set()
        for ev in (survivor, superseded):
            ref = (ev.get("data") or {}).get("source_ref") or ev.get("source_ref")
            if not (isinstance(ref, str) and ref.strip()):
                continue
            key = dedup_key_of(ref)
            if key in ref_keys:
                continue
            ref_keys.add(key)
            refs.append(ref)

        data: dict = {
            "commitment_id": superseded_cid,
            "superseded_by": survivor_cid,
            "resolved_by": merged_by,
            "resolution": "duplicate",
            "evidence": clip(evidence or f"merged into {survivor_cid}"),
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

        # PROV1 LAST — the pointer for the MERGE DECISION, alongside (never
        # instead of) the absorbed items' own capture refs in
        # `merged_source_refs`. Stamped after the auto_merge fold so an
        # evidence dict carrying its own `source_ref` spelling cannot land an
        # un-canonicalized value in the field readers treat as resolvable.
        # PROVMINT1: this writer never mints, so it never stamps a grain — and
        # popping the key means an evidence dict cannot stamp one on its behalf.
        data.pop(PROVENANCE_MISSING_KEY, None)
        data.pop(SOURCE_REF_KEY, None)
        data.pop(REF_GRAIN_KEY, None)
        data.update(pointer_fields)

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
    source_ref=None,
    mint_now_iso=None,
    brain_batch_id: Optional[str] = None,
    brain_change_class: Optional[str] = None,
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

    PROVMINT1 §0-2 — `source_ref` / `mint_now_iso`. This writer STRUCTURALLY
    could not carry a pointer before, which is the whole finding behind the
    walk's six unmarked `commitment_updated` events: they were not a caller
    forgetting, they were a signature with nowhere to put one. This write
    asserts an EXTERNAL fact ("these two really are different things"), so it
    belongs to the pointer contract exactly as the close does — caller ref
    wins, and nothing supplied mints `session:<source_skill>:<now>` with
    `ref_grain: "surface_minted"`. Same semantics as `close_commitment`; see
    its docstring for why the mint's time component is load-bearing.

    ATTENDEE1 — `brain_batch_id` / `brain_change_class` are OPTIONAL and both
    default None, so every shipped caller writes a byte-identical event. They
    exist because a clear written by an AUTOMATIC gesture has to be reversible
    in the SAME batch as whatever caused it: ATTENDEE1's evidence path creates
    a person and drains the rows that person released, and one `undo` must take
    back both halves or the receipt is lying about what it did. The stamped
    class is `commitment_confirm`, whose registered reverser routes through
    `needs_review_queue.undo_confirm_items` -> `restore_review_flags` — the
    additive mirror of this very event, so the reversal path is the shipped one
    and nothing here forks a second way to un-clear a row. They travel together
    or raise, per the house rule (see `supersede_commitment`).
    """
    if (brain_batch_id is None) != (brain_change_class is None):
        raise ValueError(
            "brain_batch_id and brain_change_class travel together — a batch "
            "id with no change class is a clear `undo_batch` will list and "
            "then refuse to reverse")
    pointer_fields = _close_pointer_fields(source_ref, mint_for=source_skill,
                                           now_iso=mint_now_iso)
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
        data.update(pointer_fields)
        if isinstance(target.get("seq"), int):
            data["commitment_seq"] = target["seq"]
        if brain_batch_id is not None:
            data["brain_batch_id"] = brain_batch_id
            data["brain_change_class"] = brain_change_class
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


# ---------------------------------------------------------------------------
# THE OVERDUE ASK (SPEC OVERDUE1 DD-1) — asked once, then it rests
# ---------------------------------------------------------------------------
#
# M's ruling R-3, on an item that had rendered identically in the evening block
# every night for two weeks: "I would do it for 3-4 days." So after the
# threshold the block asks ONCE — done, new date, or drop — and then stops
# repeating the row until the question is answered.
#
# The mark that remembers the asking is modelled on `watch_gate.park_in_watch`,
# key for key: ONE additive `commitment_updated`, idempotent, id-validated, the
# capture never rewritten, the item never leaving the open set or changing
# status. Two properties are load-bearing and neither is decoration:
#
#   * IT IS NOT MOVEMENT. `commitment_activity._is_bookkeeping_update` excludes
#     it, so asking about a quiet item does not reset the 21-day clock that
#     measures how quiet it has been. Without that exclusion the fatigue rule
#     would launder every stale row into a fresh one on the night it noticed.
#
#   * IT CARRIES NO THREAD. Unlike `close_commitment` / `clear_review_flags`,
#     this writer does NOT stamp `primary_thread_id` from its target — exactly
#     as `park_in_watch` does not. A surface deriving thread activity with
#     `thread_activity.ALL_TYPES` counts every event that names a thread, so a
#     thread-stamped mark would make a silent thread look touched on the night
#     the system talked to itself about it.
#
# `due_at_ask` is what makes the mark answerable rather than permanent: it
# records WHICH deadline was asked about. Re-date the item and the mark no
# longer describes the item's due date, so it stops suppressing anything and
# the clock re-arms from the new date. That is the whole of "a new due date
# re-arms it", and it needs no second event to happen.

ASKED_SURFACE_DEFAULT = "end-of-day"


def asked_mark_of(commitment_event: dict):
    """The live overdue-ask mark on a PROJECTED commitment, or None.

    Reads the read-side fold `cru_match.load_open_commitments` stamps
    (`data.asked`), never the raw event stream — same posture as every other
    projection read in this module."""
    d = (commitment_event or {}).get("data")
    if not isinstance(d, dict):
        return None
    asked = d.get("asked")
    return asked if isinstance(asked, dict) else None


def asked_commitment_marks(workspace_root, events_path=None) -> dict:
    """{commitment id: the live `asked` mark} across the open set.

    The idempotency source for `mark_asked`, mirroring
    `watch_gate.watched_commitment_ids`. Defensive in the same direction:
    any failure yields an EMPTY map, which fails toward asking a second time
    rather than silently suppressing a row nobody ever answered. A duplicate
    question is a redundancy; a row that rests without ever having been asked
    is a disappearance, and those are not the same mistake.
    """
    try:
        from pathlib import Path as _P
        path = events_path or str(_P(workspace_root) / "_hq" / "data" / "events.jsonl")
        rows = load_open_commitments(path, workspace_root=str(workspace_root))
        out = {}
        for ev in rows:
            mark = asked_mark_of(ev)
            if mark is not None:
                out[_commitment_id(ev)] = mark
        return out
    except Exception:  # noqa: BLE001
        return {}


def mark_asked(
    workspace_root,
    commitment_id,
    *,
    due_at_ask,
    source_skill: str,
    surface: str = ASKED_SURFACE_DEFAULT,
    now_iso: Optional[str] = None,
    note: str = "",
    force: bool = False,
    known_asked: Optional[dict] = None,
) -> dict:
    """Record that a surface has ASKED about an overdue item (OVERDUE1 DD-1).

    Appends ONE `commitment_updated` carrying
    `data = {commitment_id, asked_set: True, asked: {surface, asked_at,
    due_at_ask}}`. Nothing else about the item changes: it stays `status:
    "open"`, stays in every count, stays on `my plate` and in the brief's
    needs-attention lane. Only the nightly slipped block reads the mark.

    IDEMPOTENT, and idempotent on the QUESTION rather than on the item: a live
    mark for the SAME `due_at_ask` is a no-op (`{"status": "already_asked"}`)
    and appends nothing, because asking twice about one deadline is one
    question. A mark whose `due_at_ask` differs from the one being written is
    stale — the user re-dated the item — so this writes a fresh mark and the
    fatigue clock re-arms. `force=True` re-asks deliberately; `known_asked`
    lets a batch caller pass the map it already projected instead of
    re-projecting per row.

    REFUSES AN EMPTY ID and refuses a CLOSED item, the two floors every
    id-bearing writer in this module holds. A mark on nothing is a permanent
    line in an append-only log that no projection can ever attach to anything
    (the `park_in_watch` empty-id incident), and a mark on a closed item is a
    question about work that is already finished. It also REFUSES AN EMPTY
    `due_at_ask` unless `force=True` — see the guard's own comment: a mark
    with nothing to go stale against rests its row forever.

    The idempotency projection runs OUTSIDE the writer lock; the reasoning,
    and the TOCTOU it accepts in exchange, are in the comment above the read.

    Returns {"status": "asked"|"already_asked"|"not_open", "commitment_id": …,
             "event": {...}} — `event` only on an actual write.
    """
    cid_raw = str(commitment_id or "").strip()
    if not cid_raw:
        raise CommitmentIdError(
            "mark_asked got an empty commitment id — an overdue ask has to be "
            "about something. The caller's row arrived with no id at the top "
            "level (a rendered pack row keeps its id under `commitment_id`).")
    due_key = str(due_at_ask or "").strip()
    if not due_key and not force:
        # REVIEW OVERDUE1 F-7. `due_at_ask` is what lets a mark go STALE: the
        # row's due date moves, the comparison stops matching, the clock
        # re-arms. An empty one can never stop matching an undated row, so the
        # row would rest forever with `clear_asked` as its only way out. The
        # shipped path cannot reach this (an undated row has no days-overdue
        # and is never asked), which is exactly why a second caller could.
        raise ValueError(
            "mark_asked needs the due date the question was about — a mark "
            "with no `due_at_ask` can never go stale, so the item would rest "
            "until something explicitly un-asks it. Pass the row's effective "
            "due, or force=True if a permanent mark is genuinely intended.")
    from pathlib import Path as _Path
    from writer_lock import events_writer_lock
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    # THE PROJECTION HAPPENS OUTSIDE THE LOCK (REVIEW OVERDUE1 F-5). No other
    # writer in this module reads a whole workspace projection while holding
    # the writer lock — they scan an index and append — and this repo has
    # already lost five mutation suites to writer-lock starvation at exactly
    # the 900s cap. A nightly writer that holds the lock across a
    # workspace-sized read is how that comes back. The cost is the same TOCTOU
    # every other member of this family accepts (`park_in_watch` projects
    # entirely outside any lock): a mark landing between this read and the
    # append yields one duplicate `asked_set`, which the fold resolves
    # latest-wins and which costs nothing but a line of history. Batch callers
    # pass `known_asked` and skip this read entirely.
    live = None
    if not force:
        live = (known_asked if known_asked is not None
                else asked_commitment_marks(workspace_root, events_path))
    with events_writer_lock(events_path, holder=f"mark_asked:{source_skill}"):
        index = _scan_commitment_index(events_path)
        cid = normalize_commitment_id(cid_raw, index)
        target = index["by_id"][cid]
        if _currently_closed(index, cid, target.get("seq")):
            return {"status": "not_open", "commitment_id": cid}
        if live is not None:
            # Keyed on the CANONICAL id, which is what the projection uses —
            # so a caller arriving with a legacy spelling still matches its
            # own prior mark.
            prior = live.get(cid)
            if isinstance(prior, dict) and str(
                    prior.get("due_at_ask") or "").strip() == due_key:
                return {"status": "already_asked", "commitment_id": cid}
        data: dict = {
            "commitment_id": cid,
            "asked_set": True,
            "asked": {
                "surface": surface,
                "asked_at": now_iso or _now_iso(),
                # The deadline THIS question was about. Empty string rather
                # than absent for an undated item, so the comparison above has
                # one shape to reason about.
                "due_at_ask": due_key,
            },
        }
        if note:
            data["note"] = note[:200]
        if isinstance(target.get("seq"), int):
            data["commitment_seq"] = target["seq"]
        # NO `primary_thread_id` — see the section note above.
        ev = {
            "type": "commitment_updated",
            "source_skill": source_skill,
            "data": data,
        }
        from event_gate import append_event
        append_event(events_path, [ev], holder=source_skill)
    return {"status": "asked", "commitment_id": cid, "event": ev}


def clear_asked(workspace_root, commitment_id, *, source_skill: str,
                note: str = "") -> dict:
    """Drop the overdue-ask mark WITHOUT resolving the item — the mirror of
    `mark_asked` and the twin of `watch_gate.clear_watch`.

    One `commitment_updated` carrying `data.asked_cleared: true`; the item
    stays exactly as open as it was and returns to the nightly block on the
    next fire. Ordinary answers do not need this: a `push to [date]`, a
    re-wording, a re-owner or a close all clear the mark through the read-side
    fold with no event of their own (DD-2). This exists for the case with no
    such write behind it — an operator or a repair pass un-asking a question.
    """
    cid = str(commitment_id or "").strip()
    if not cid:
        raise CommitmentIdError(
            "clear_asked got an empty commitment id — there is nothing to "
            "un-ask.")
    from pathlib import Path as _Path
    from event_gate import append_event
    events_path = _Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    data = {"commitment_id": cid, "asked_cleared": True}
    if note:
        data["note"] = note[:200]
    ev = {"type": "commitment_updated", "source_skill": source_skill,
          "data": data}
    append_event(events_path, [ev], holder=source_skill)
    return {"status": "cleared", "commitment_id": cid, "event": ev}


def mark_partial_received(
    workspace_root,
    commitment_id,
    *,
    received_by: str,
    source_skill: str,
    counterparty_id: Optional[str] = None,
    counterparty_name: Optional[str] = None,
    evidence: str = "",
    source_ref=None,
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

    PROV1 — a receipt ASSERTS AN EXTERNAL FACT ("they delivered"), which is
    exactly the update class §3.1 puts under the pointer contract even though
    the event never closes anything: it is the evidence a later close will
    stand on (`all_counterparties_received` is the PROPOSE-closure signal), so
    a receipt nobody can trace is a close nobody can trace one hop later.
    Same rule as the closers: absent → `provenance_missing: true`, malformed →
    SourceRefError before the lock.
    """
    if not (counterparty_id or counterparty_name):
        raise ValueError(
            "mark_partial_received needs a counterparty_id or "
            "counterparty_name — a receipt records WHICH recipient of a "
            "multi-counterparty commitment delivered"
        )
    pointer_fields = _close_pointer_fields(source_ref)
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
            data["evidence"] = clip(evidence)
        data.update(pointer_fields)
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
    source_ref=None,
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

    PROV1 — the split CLOSER is a `commitment_superseded`, so it carries the
    close-family pointer contract: `source_ref` names what justified the split
    (the session/receipt of the verb that asked for it), and nothing passed
    lands `provenance_missing: true`. This is a distinct fact from the
    CHILDREN's inherited capture `source_ref`, which keeps pointing at the
    artifact the original promise was captured from.
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
    pointer_fields = _close_pointer_fields(source_ref)
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
        closer_data.update(pointer_fields)
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
    CommitmentIdError, PendingReviewError, OpenSubitemsError, SourceRefError,
    or AmbiguousTargetError (CLOSEID2 — this batch shape has no per-row door
    to state, so a session-lane call refuses every row rather than the run)
    on one item is recorded as {"status": "error", ...} and does NOT abort the
    rest (a bad id — or a bad POINTER — in a batch of real closes must not lose
    the real closes; PROV1 refuses the one row, never the run).

    Each row may carry `source_ref` (PROV1): the pointer to the artifact that
    justified THAT row's close. A row without one falls to the module floor and
    is MINTED (PROVMINT1) — with ONE receipt minted for the whole batch, read
    from the clock once here, because a batch IS one gesture: N receipts a
    second apart would read as N separate acts and hand the coverage metric N
    distinct pointers for one decision. (The per-row pointer is still per-row:
    a row that carries its own artifact key wins over the batch receipt.)

    SUB1 D3 — this batch path NEVER cascades (no close_subitems passthrough,
    deliberately): programmatic closers propose, they don't cascade. The
    matchers already downgrade a parent-with-open-children auto_resolve to
    pending_review (cru_match.parent_blocks_auto_resolve); an
    OpenSubitemsError here is the loud defensive floor, not the design path.
    """
    results: list[dict] = []
    # PROVMINT1 — the batch's own instant, read ONCE. See the docstring: one
    # gesture, one receipt.
    batch_mint_iso = _now_iso()
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
                # PROV1 — per-row pointer. A batch is N independent closes and
                # each cites its OWN message; one pointer for the batch would
                # be a plausible-looking lie on N-1 of them.
                source_ref=c.get("source_ref"),
                mint_now_iso=batch_mint_iso,
            ))
        except (CommitmentIdError, PendingReviewError, OpenSubitemsError,
                SourceRefError, AmbiguousTargetError) as e:
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
    source_ref=None,
    mint_now_iso=None,
) -> dict:
    """S4 undo: reopen a closed commitment ADDITIVELY — append a
    `commitment_reopened` event; the tombstone stays in history and the
    projector honors whichever came last. Same id normalization + loud
    no-match as close_commitment, and the same scan->append lock span
    (v4.5.2 R1c). A later re-close works normally.

    PROVMINT1 §0-2 — `source_ref` / `mint_now_iso`. A reopen REVERSES a close
    that carries a pointer, so the reversal owed one too and had no parameter
    to take it. Caller ref wins (an undo fired from a surface with a receipt
    should pass that receipt); nothing supplied mints
    `session:<source_skill>:<now>` with `ref_grain: "surface_minted"`.

    SUB1 D3 — reopening a cascade-closed PARENT reopens the PARENT ONLY;
    each child has its own tombstone and is reopened individually. The
    triage batch-undo already caches every closed id in the batch (the
    cascade's `closed_subitems` return joins that cache), so an undone
    cascade round-trips with zero new undo code.
    """
    pointer_fields = _close_pointer_fields(source_ref, mint_for=source_skill,
                                           now_iso=mint_now_iso)
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
                **pointer_fields,
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
    status would audit this write as an error).

    PROVENANCE (FLOOR2 C2, intake BUG_2026-08-06_myplate-synthetic-granola-
    sourceref): this is a USER-INITIATED capture, and it says so in its own
    scheme. A caller's originating ref is kept when it is one — that is the
    provenance — but a synthetic `granola:<not-a-uuid>` ref is replaced by
    `user:my_plate` rather than written as if a connector had produced it. The
    live row `granola:past-meetings-2026-08-04` is what this closes: it made a
    hand-typed task look like a meeting capture to every reader that resolves
    the scheme, `account_scope_gate` included. `data.origin` is stamped for the
    same reason — the scope wall should read the discriminator, not sniff a
    prefix."""
    from pathlib import Path as _Path
    from capture_gate import gate_commitment_data, user_initiated_source_ref
    from event_gate import append_event
    # REVIEW TITLEMINT1 R-1 — the same non-empty-title refusal every OTHER
    # commitment writer already carries (`inbound_capture`, `sent_capture`,
    # `slack_capture`, `meeting_capture`, and `capture_gate`'s observed-item
    # promoter). This one never had it, and `gate_commitment_data` — the block
    # they all share — does not check the title, so this was the one door in
    # the tree through which a TITLELESS commitment could reach the substrate.
    # That shape is what makes TITLEMINT1's refuse-at-the-writer posture
    # dangerous rather than merely strict: the non-title close bases
    # (`REPLY_BASIS`, `DELIVERY_BASIS`) grade a titleless item into the
    # `pending_review` band at score 0.0 by design, and the reconcile rails
    # then hand that empty title to `build_pending_review_event`, which now
    # raises — uncaught, inside a nightly loop. Refusing at capture is what
    # keeps that population empty (measured: 0 titleless of 842 commitments).
    title = (title or "").strip()
    if not title:
        raise ValueError("a My Plate task needs a non-empty title")
    data = {
        "title": title,
        "kind": "task",
        "owner_id": owner_id,
        "status": "open",
        "no_due": True,
        "source": "my_plate_capture",
        "origin": "user_stated",
    }
    data["source_ref"] = user_initiated_source_ref(source_ref)
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
    "AmbiguousTargetError",
    "RESOLVED_BY_MATCH_VALUES",
    "SESSION_RESOLVED_SOURCES",
    "MAX_AMBIGUOUS_CANDIDATES",
    "AMBIGUITY_EVIDENCE_SHAPE",
    "resolve_commitment_by_title",
    "propose_ambiguous_close",
    "ambiguous_close_sentence",
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
    "ASKED_SURFACE_DEFAULT",
    "asked_mark_of",
    "asked_commitment_marks",
    "mark_asked",
    "clear_asked",
    "split_commitment",
    "reopen_commitment",
    "promote_task_to_commitment",
    "create_personal_task",
    "stale_tasks",
    "commitment_kind",
    "load_open_commitments",
    "is_overdue",
    "overdue_days",
    "reconcile_is_stale",
    "HEADLINE_BUCKETS",
    "BUCKET_YOU_OWE",
    "BUCKET_OWED_TO_YOU",
    "BUCKET_UNOWNED",
    "BUCKET_UNCONFIRMED",
    "bucket_of",
    "unconfirmed_slices",
    "UNTITLED_PLACEHOLDER",
    "UNCONFIRMED_SECTION_LABEL",
    "UNCONFIRMED_LANE",
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
    "later_route",
    "parse_later_when",
    "apply_later",
    "LATER_SNOOZE_VIA",
]
