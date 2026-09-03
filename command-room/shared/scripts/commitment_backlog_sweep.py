#!/usr/bin/env python3
"""On-demand commitment backlog sweep — evidence backfill + age-out + merge
proposals (SPEC_SWEEPBACK, 2026-07-30).

WHY THIS EXISTS
===============
The v5.6.0 mail matchers are forward-only. Both reconcile cursors are monotonic,
so evidence sitting in OLD mail is never re-read, and the update bridge does
nothing retroactive. The result is months of open-commitment bloat with three
distinct populations underneath it:

  1. items whose delivery / reply evidence ALREADY EXISTS in historical mail and
     is closeable the moment somebody looks — which nothing does;
  2. items structurally unreachable by mail (no counterparty id AND no name;
     measured 40 of 254 owned and 97 of 351 waiting-side on the reference
     substrate);
  3. genuinely open work.

This module is the one-command sweep for (1), plus the two judgment surfaces that
serve (2) and the meeting-sourced pile: age-out and duplicate-merge.

WHY A WIDE HISTORICAL SCAN IS SAFE ONLY NOW
===========================================
This shape — "read months of old mail and close things" — is exactly BUG-3719:
*a wide catch-up closes the promise its own message opened.* It depends on
guards that did not all exist before this train:

  * per-backend dedup keys + the identity predicate (MAILSEAM), so a `gmail:`-era
    ref and a Superhuman-era ref are compared as artifacts, not strings;
  * the two-layer circularity fence on both mail rails (RECONFENCE + REPLYCLOSE);
  * the direction hard stop (the user's own message closes nothing on inbound);
  * Bug #102's loud abort on an unresolved primary user;
  * **EVORDER layer 3** — the ordering guard a historical sweep depends on more
    than any live rail does. In a months-deep scan MOST candidate mail predates
    MOST open commitments, so without `send_ts` / `inbound_ts` on every scoring
    call this module would be an F-11 mass-production machine.

HOW IT AVOIDS FORKING ANY OF THAT
=================================
It never scores anything itself. Every candidate goes through the RAILS' OWN
driver functions — `reconcile_sent` and `reconcile_inbound`, imported, the same
objects the daily fires call. Those drivers pass every fence parameter,
including each message's own `ts` as `send_ts` / `inbound_ts`, and they return
the rails' own verdicts. This module's job is only:

  * choose WHICH commitments and WHICH messages go in (the window, the cap, the
    reachability filter),
  * narrow the auto tier below the rails' own bar (`cru_match.closes_on_evidence`,
    imported — the title path never auto-applies over 180 days of mail), and
  * write ONE audit event and ONE undoable batch.

THREE THINGS THIS MODULE DELIBERATELY DOES NOT DO
=================================================
* **It never touches a reconcile cursor.** The pure drivers read and write none;
  only the `*_and_receipt` wrappers do, and this module does not call them. That
  is a structural property, not a promise — `run_commitment_backlog_sweep_test`
  hashes `entities.json` around a full sweep.
* **It never re-scores meeting transcripts.** Mail evidence cannot reach
  meeting-sourced items (295 of 683 commitment events, and all 31 live waiting-on
  items, are `granola:`-sourced), and transcript re-scoring is a separate build.
  Those items are served by the age-out and merge buckets instead, and the digest
  says so rather than implying reach it lacks.
* **It never forks a similarity metric.** Duplicate grouping calls
  `commitment_dedup.score_suspected_duplicate` — the shipped scorer, with its
  window widened. Its known title-echo bias (a side with no counterparty fields
  falls back to TITLE tokens for the counterparty comparison) is why the sweep
  only groups CORROBORATED pairs; see `duplicate_groups`.

stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# THE AUTO BAR — imported from the rails' own module, never restated here. See
# `cru_match.closes_on_evidence` for why it lives there and what a copy would
# cost. `run_commitment_backlog_sweep_test` pins the object identity.
from cru_match import (  # noqa: E402
    AUTO_CLOSE_EVIDENCE_BASES,
    closes_on_evidence,
    cru_eligible,
    load_open_commitments,
    split_pending_review,
)
# THE SCORERS — the rails' own drivers, imported. This module calls no matcher
# directly, which is what makes "the sweep cannot invoke a matcher without its
# EVORDER kwarg" a structural fact: the drivers always pass each message's `ts`.
from reconcile_sent_commitments import reconcile_sent  # noqa: E402
from reconcile_inbound_commitments import reconcile_inbound  # noqa: E402
from connector_adapters.provenance import resolve_mail_provider  # noqa: E402
from event_time import event_time, parse_ts  # noqa: E402
# REVAMN1 §0-3 — the two review-tier closure reasons. They live in
# `event_types` (the shared vocabulary home) rather than here, because the two
# suppression learners that must IGNORE them cannot import this module: it
# pulls in both reconcile rails. One spelling, three readers.
from event_types import (  # noqa: E402
    INGEST_KILL_REASON,
    REVIEW_EXPIRY_REASON,
)


SOURCE_SKILL = "commitment-backlog-sweep"

# The historical window, in days. Wide by design — the whole point is mail the
# forward-only rails will never look at again. Configurable per run.
DEFAULT_WINDOW_DAYS = 180

# "No evidence AND no activity for N days" → an age-out candidate. Read from
# skill_config (`load_skill_config(ws, "commitment-backlog-sweep")["age_out_days"]`)
# and overridable in the command ("sweep my backlog, age out at 90 days").
#
# SWEEPSCHED1 §0 D1 — 45 → 30, and the PHRASE PATH INHERITS IT. The 2026-08-22
# funnel census measured the confirmed book against three bars: at 45 days
# nothing at all qualified, at 21 days a third of the book did, at 30 days a
# workable tenth did. A bar that clears nothing is a bar nobody notices; a bar
# that clears a third of work somebody agreed to is a bar nobody trusts. One
# number serves both doors on purpose — the weekly job and `commitment amnesty`
# must never be able to disagree about what "quiet" means, or the offer a user
# reads and the clear a job performs are answering different questions.
# A workspace that wants its own bar still sets `age_out_days` and wins.
DEFAULT_AGE_OUT_DAYS = 30

# Volume cap per run — the CATCHUP1 batching precedent. A 400-item backlog must
# not produce a 400-row widget or an unbounded connector bill, and a run that
# stops must say where it stopped so the next one resumes rather than restarts.
DEFAULT_ITEM_CAP = 60

# The duplicate scorer's window, widened for the sweep. Capture-time dedup uses
# 14 days because the real cross-writer pairs land hours-to-days apart; the twins
# this sweep exists to find were opened MONTHS apart ("things come up in
# different ways at different times"), so the window has to be the sweep's.
DEFAULT_DUP_WINDOW_DAYS = 180

# The undo batch stamps. Both classes are already registered in
# `brain_undo.REVERSERS`, so this build adds no reverser and no batch kind.
BATCH_PREFIX = "swb_"
CLOSE_CHANGE_CLASS = "commitment_close"
MERGE_CHANGE_CLASS = "commitment_merge"

# Random bytes salting each batch id (`swb_<UTC>-<2*N hex>`). Named rather than
# inlined so the width is one fact the shipped code and its test read from the
# same place — the re-verify's F-9 was a test asserting a distinctness property
# the then-current width could not actually deliver. See `_mint_batch_id`.
BATCH_SALT_BYTES = 4

# `commitment_state.VALID_RESOLUTIONS` is exactly ("done", "dropped",
# "superseded") — S1's one closure vocabulary, and this module does not get to
# widen it. So the three sweep outcomes map on as:
#
#   evidence close -> "done"       (it really was delivered; evidence attached)
#   age-out        -> "dropped"    + data.resolution_reason = "aged_out"
#   merge          -> handled by `supersede_commitment`, which writes its OWN
#                     `resolution: "duplicate"` on a `commitment_superseded`
#                     event — a distinct kind that already exists.
#
# WHY AGE-OUT IS "dropped" AND NOT "done": nobody delivered anything. "done"
# would put a false completion in the history that every count and every recap
# reads back as work finished. "superseded" is the merge word and means another
# item carries this one. `dropped` is the honest one, and `resolution_reason`
# distinguishes a sweep age-out from a user's own "drop this" — a reader that
# wants only the deliberate drops can filter on it, and `undo` reopens either.
AGE_OUT_RESOLUTION = "dropped"
AGE_OUT_REASON = "aged_out"
EVIDENCE_RESOLUTION = "done"

# The audit event type. Registered in the ONE enum home
# (`shared/data-schemas/events.schema.json`) with writer + named consumer in
# `shared/EVENT_TYPES.md`.
AUDIT_EVENT_TYPE = "backlog_sweep"


# ---------------------------------------------------------------------------
# The window — structural, never a prose floor (F-12)
# ---------------------------------------------------------------------------

def _now_dt(now_iso=None) -> _dt.datetime:
    if now_iso:
        parsed = parse_ts(now_iso)
        if parsed is not None:
            return parsed
    return _dt.datetime.now(_dt.timezone.utc)


def window_start(now_iso=None, days: Optional[int] = None) -> str:
    """The ISO-8601 UTC instant the sweep's window opens at."""
    days = DEFAULT_WINDOW_DAYS if days is None else int(days)
    start = _now_dt(now_iso) - _dt.timedelta(days=max(1, days))
    return start.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


# Providers whose search tool exposes a REAL structured date parameter, and the
# name of that parameter. Superhuman's `list_threads` takes `start_date`; the
# MAILSEAM adapter compiles `after` into the natural-language phrase "on or after
# <value>" for that provider, which is a floor asked for IN PROSE and never
# enforced (dogfood F-12: three separate runs used out-of-window mail as closure
# evidence, one of them four hours outside its own window).
#
# So a sweep asks BOTH ways: the structured knob where one exists, AND the
# compiled intent for the search tool. Neither is trusted — `messages_in_window`
# post-filters whatever comes back. Belt, braces, and a tape measure.
_STRUCTURED_DATE_PARAM = {
    "superhuman": "start_date",
}


def structured_window_params(provider, window_start_iso) -> dict:
    """The provider's own structured date parameter for this window, or `{}`.

    `{}` is not "no floor" — it means this provider exposes no structured knob, so
    the compiled intent below is the only thing that can be ASKED, and the
    post-filter is the only thing that ENFORCES. Callers pass this dict straight
    into the connector's list/search tool alongside the compiled query.
    """
    param = _STRUCTURED_DATE_PARAM.get((provider or "").lower())
    if not param:
        return {}
    return {param: (window_start_iso or "")[:10]}


def window_intent(window_start_iso, *, direction: str) -> dict:
    """The MAILSEAM intent dict for one leg of the sweep's fetch.

    Compiled per provider by `connector_adapters.mail.compile_search`. `after` is
    the seam's own date verb — this module does not spell a provider's operator.
    """
    if direction not in ("sent", "inbound"):
        raise ValueError(f"direction must be 'sent' or 'inbound', got {direction!r}")
    intent = {"after": (window_start_iso or "")[:10], "not_draft": True}
    intent["in_sent" if direction == "sent" else "in_inbox"] = True
    return intent


def messages_in_window(messages, *, window_start_iso, now_iso=None) -> tuple:
    """(kept, counters) — THE post-filter. Nothing reaches a matcher without it.

    Returns the messages provably inside `[window_start, now]`, plus counters for
    the receipt. Three drop classes, and the split matters:

      * `n_out_of_window` — the connector returned mail outside the floor it was
        given. Measured on a real substrate, repeatedly (F-12). Not an error, not
        rare: the honest assumption is that a prose floor is a suggestion.
      * `n_no_usable_ts` — a message with no `ts`, or one that will not parse.
        These are DROPPED, and that is the sweep's own tightening of the rails'
        rule. On a live rail an absent `send_ts` leaves EVORDER layer 3 inert,
        which is right there: the window is a day wide and closing nothing at all
        because a provider omits send times would be worse than the risk. Across
        180 days it is the opposite — an un-orderable message is precisely the
        F-11 machine, so the sweep refuses to score it and says how many it
        refused.
      * `n_future` — a stamp after `now`. A clock artifact, and an item whose
        capture cannot be ordered against it either way.

    A caller CANNOT get the un-filtered list to a matcher by accident, because the
    scan below never passes `messages` anywhere else.
    """
    start = parse_ts(window_start_iso)
    end = _now_dt(now_iso)
    counters = {"n_returned": 0, "n_out_of_window": 0, "n_no_usable_ts": 0,
                "n_future": 0, "n_in_window": 0}
    kept: list = []
    for msg in messages or []:
        counters["n_returned"] += 1
        if not isinstance(msg, dict):
            counters["n_no_usable_ts"] += 1
            continue
        ts = parse_ts(msg.get("ts"))
        if ts is None:
            counters["n_no_usable_ts"] += 1
            continue
        if start is not None and ts < start:
            counters["n_out_of_window"] += 1
            continue
        if ts > end:
            counters["n_future"] += 1
            continue
        counters["n_in_window"] += 1
        kept.append(msg)
    return kept, counters


# ---------------------------------------------------------------------------
# Reachability — population 2, counted and named rather than quietly excluded
# ---------------------------------------------------------------------------

def _cid(ev) -> str:
    from cru_match import _commitment_id
    return _commitment_id(ev)


def _title(ev) -> str:
    from cru_match import _commitment_field
    return _commitment_field(ev, "title") or ""


def _source_kind(ev) -> str:
    """'granola' / 'gmail' / 'superhuman' / 'chat' / '' — the provider prefix of
    whatever this item was captured from. Used only to explain the coverage
    block, never to gate a close."""
    from cru_match import _commitment_field
    ref = _commitment_field(ev, "source_ref") or ""
    if isinstance(ref, str) and ":" in ref:
        return ref.split(":", 1)[0].strip().lower()
    return ""


def mail_reachable(ev, *, workspace_root=None) -> bool:
    """True when mail evidence could reach this item at all.

    Two halves, both required, both measured on the reference substrate:

      * SOMEBODY to match against — a resolved counterparty id or a free-text
        counterparty name. Items with neither are population 2: 40 of 254 owned
        and 97 of 351 waiting-side. No message can ever be attributed to them,
        and a receipt that folded them into a silent zero would be lying by
        omission.
      * a MAIL-shaped anchor — a `source_ref` or `thread_ref` from a mail
        provider, OR a counterparty id (which the recipient/sender gate can match
        on its own). A `granola:`-only item with no counterparty is out.

    `workspace_root` is threaded because `counterparty_names` needs the entity
    graph to know that one person written as an id AND that person's name is ONE
    counterparty (F-28). Here it only ever narrows a COUNT that is compared to
    zero, so the answer is the same either way — but a roster read on a path that
    decides what gets scored is exactly the class G21 pins, and "correct either
    way" is not a reason to leave a workspace on the floor.
    """
    from commitment_parties import counterparty_ids, counterparty_names
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    ids = counterparty_ids(d)
    names = counterparty_names(d, workspace_root=workspace_root)
    if not ids and not names:
        return False
    from cru_match import commitment_source_refs, commitment_thread_refs
    if ids:
        return True
    refs = commitment_source_refs(ev) | commitment_thread_refs(ev)
    return any(isinstance(r, str) and r.split(":", 1)[0].lower()
               in ("gmail", "superhuman", "outlook") for r in refs)


def coverage_block(opens, *, workspace_root=None) -> dict:
    """The honest coverage numbers for the digest (v5.6.0 receipt discipline).

    `n_thread_anchored` is here because the spec requires the digest to state the
    ANCHORED population it actually found. On the reference substrate that number
    was 2 of 682 commitment events, and 0 of the waiting-on ones — so the
    reply-evidence auto tier fires rarely and the sweep's inbound value is
    proposals, not closures. Printing the real count is the difference between a
    quiet rail and a rail that explains itself.
    """
    from cru_match import commitment_thread_refs
    eligible = cru_eligible(opens or [])
    out = {
        "n_open": len(opens or []),
        "n_eligible": len(eligible),
        "n_mail_reachable": 0,
        "n_unreachable_no_counterparty": 0,
        "n_unreachable_no_mail_anchor": 0,
        "n_thread_anchored": 0,
        "n_meeting_sourced": 0,
    }
    from commitment_parties import counterparty_ids, counterparty_names
    for ev in eligible:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if commitment_thread_refs(ev):
            out["n_thread_anchored"] += 1
        if _source_kind(ev) == "granola":
            out["n_meeting_sourced"] += 1
        if mail_reachable(ev, workspace_root=workspace_root):
            out["n_mail_reachable"] += 1
        elif not (counterparty_ids(d)
                  or counterparty_names(d, workspace_root=workspace_root)):
            out["n_unreachable_no_counterparty"] += 1
        else:
            out["n_unreachable_no_mail_anchor"] += 1
    return out


# ---------------------------------------------------------------------------
# Age-out
# ---------------------------------------------------------------------------

def last_activity_map(events_path, *, movement_types=None) -> dict:
    """{commitment id: UTC-aware datetime of its newest movement}.

    Derived through `commitment_activity.derive_commitment_movement` — THE
    movement baseline every other surface passes, never a second derivation. Its
    map is seeded with each commitment's own capture ts, so an item that has never
    moved has its capture time as its last activity, which is exactly the age-out
    question.

    REVSCHED1 §0-3 — `movement_types` narrows WHICH event types count as
    movement, and it exists for exactly one caller: the `include_reopened`
    door on the review-tier expiry, which has to be able to ask "how long
    had this been quiet BEFORE somebody un-did it". Default None means
    `commitment_activity.MOVEMENT_EVENT_TYPES`, i.e. every existing caller
    (`age_out_candidates`, `ingest_kill_candidates`, the default expiry) is
    byte-for-byte unchanged. The narrowing is passed THROUGH to the one
    derivation rather than post-filtered here: the capture-ts floor and the
    sub-item / merge re-pointing all live inside that function, and a second
    filter on this side would silently lose them.
    """
    try:
        from commitment_activity import derive_commitment_movement
        return {cid: mv.ts for cid, mv
                in derive_commitment_movement(
                    events_path, movement_types=movement_types).items()}
    except Exception:
        return {}


# REVSCHED1 §0-3 — the movement set the `include_reopened` door measures
# against: everything EXCEPT the reopen. Derived from the one canonical set by
# subtraction, never restated, so a movement type added to
# `commitment_activity.MOVEMENT_EVENT_TYPES` tomorrow is honoured by the door
# on the same day. Computed lazily (module import order) via
# `_movement_types_without_reopen`.
REOPEN_MOVEMENT_TYPE = "commitment_reopened"


def _movement_types_without_reopen() -> frozenset:
    """`MOVEMENT_EVENT_TYPES` minus the reopen. THE door's measuring stick.

    Subtraction, not a hand-written list: the door's promise is "reopen-only
    movement is ignored, EVERY other movement type still shields", and a
    restated list would keep that promise only for the types someone
    remembered on the day they wrote it.
    """
    from commitment_activity import MOVEMENT_EVENT_TYPES
    return frozenset(MOVEMENT_EVENT_TYPES) - {REOPEN_MOVEMENT_TYPE}


def age_out_candidates(opens, *, events_path, now_iso=None,
                       age_out_days: Optional[int] = None,
                       exclude_ids: Optional[Iterable[str]] = None) -> list:
    """Items with NO evidence found this run and no activity for N days.

    `exclude_ids` is the set the evidence scan touched at all — anything with an
    auto-close or a proposal against it is NOT stale, it is answered, and asking
    "still real?" about an item the same digest is proposing to close would be the
    kind of double-surfacing the receipt discipline exists to stop.

    An item whose activity cannot be dated is NOT a candidate. Age-out is an
    argument from silence, and silence you cannot measure is not evidence.
    """
    days = DEFAULT_AGE_OUT_DAYS if age_out_days is None else int(age_out_days)
    now = _now_dt(now_iso)
    cutoff = now - _dt.timedelta(days=max(1, days))
    activity = last_activity_map(events_path)
    skip = {str(x) for x in (exclude_ids or [])}
    out: list = []
    for ev in cru_eligible(opens or []):
        cid = _cid(ev)
        if not cid or cid in skip:
            continue
        seen = activity.get(cid)
        if seen is None:
            continue
        if seen > cutoff:
            continue
        out.append({
            "commitment_id": cid,
            "title": _title(ev),
            "primary_thread_id": ev.get("primary_thread_id") or "",
            "last_activity": seen.isoformat().replace("+00:00", "Z"),
            "days_quiet": int((now - seen).total_seconds() // 86400),
            "source_kind": _source_kind(ev),
        })
    out.sort(key=lambda r: r["days_quiet"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Duplicate grouping — the SHIPPED scorer, never a fork
# ---------------------------------------------------------------------------

def _party_keys(ev, *, workspace_root=None) -> set:
    """The comparable identity of a commitment's counterparties.

    `counterparties()` is the canonical DISJOINT roster reader — with a workspace
    it collapses one person written as both a resolved id and that person's name
    into ONE entry (F-28), which is the whole reason the workspace is threaded
    here: without it the same person could read as two entries on one side and one
    on the other, and two spellings of one roster would look like two rosters.

    An id compares as an id; a name that resolved to nothing compares as its
    lower-cased text, because a free-text name is all the identity that item has.
    """
    from commitment_parties import counterparties
    out: set = set()
    for party in counterparties(ev, workspace_root=workspace_root):
        pid = party.get("id")
        if pid:
            out.add(("id", str(pid)))
            continue
        name = (party.get("name") or "").strip().lower()
        if name:
            out.add(("name", name))
    return out


def _shares_a_counterparty(a, b, *, workspace_root=None) -> bool:
    """The spec's second grouping conjunct — same counterparty, from the roster.

    Both sides must name SOMEBODY, and the somebodies must overlap. Two items with
    no counterparty at all are not "the same counterparty"; they are two items
    nobody attached a person to, which is exactly the population the dedup
    scorer's title-token fallback would otherwise hand over as a merge proposal.
    """
    ka = _party_keys(a, workspace_root=workspace_root)
    kb = _party_keys(b, workspace_root=workspace_root)
    return bool(ka and kb and (ka & kb))


def duplicate_groups(opens, *, workspace_root=None, now_iso=None,
                     window_days: Optional[int] = None) -> list:
    """Groups of open items that look like the SAME real-world commitment.

    Uses `commitment_dedup.score_suspected_duplicate` — the shipped scorer — with
    its `window_days` widened to the sweep's. Nothing here re-derives a similarity
    metric: two metrics for one question is how two surfaces end up disagreeing
    about whether a pair is a duplicate.

    THE GROUPING FLOOR IS "SAME COUNTERPARTY", AND THAT IS THE TITLE-ECHO
    ANSWER. The scorer has a documented bias: when a side carries no counterparty
    fields at all, its person gate falls back to that side's TITLE tokens, so two
    items about the same DELIVERABLE pass the person gate with no evidence at all
    that they involve the same person. And its `corroborated` flag is
    `owner_corroborated OR cp_corroborated` — on a self-owed backlog the owner is
    the user on BOTH sides, so `corroborated` is True for free and carries no
    information about WHO. The scorer already does what it can about this on its
    own terms (title bar 0.7 corroborated / 0.85 not) and its own stated reason for
    a 14-day window is precisely that "a same-title item from months ago is far
    more likely a recurring real ask than a duplicate capture" — which is the
    window this sweep has to open to 180 days to do its job at all.

    So the sweep adds the spec's own second conjunct — SAME COUNTERPARTY — read
    off the roster rather than inferred from words. `counterparties()` returns the
    workspace-resolved DISJOINT roster (one person once, even when written as both
    an id and that person's name), so the comparison is an identity overlap, not a
    similarity score, and no metric is forked. Two items with no counterparty at
    all therefore never group, which is exactly the population the title-echo bias
    would otherwise hand over.

    That trades recall for precision in the one direction that matters: a missed
    twin stays on the list and costs a line of clutter, while a wrong merge folds
    two different promises into one and needs an undo to notice.

    Groups are PROPOSED. Nothing here merges anything.
    """
    from commitment_dedup import (
        DUP_WINDOW_DAYS, _person_name_index, score_suspected_duplicate,
    )
    window = (DEFAULT_DUP_WINDOW_DAYS if window_days is None else int(window_days))
    if window < DUP_WINDOW_DAYS:
        window = DUP_WINDOW_DAYS
    now = _now_dt(now_iso)
    name_index = _person_name_index(workspace_root) if workspace_root else {}
    items = [ev for ev in cru_eligible(opens or []) if _cid(ev)]
    # Oldest first — the survivor of a merge is the OLDEST item, so a
    # deterministic age order makes the proposal reproducible run to run.
    items.sort(key=lambda ev: (parse_ts(event_time(ev))
                               or _dt.datetime.max.replace(
                                   tzinfo=_dt.timezone.utc), _cid(ev)))

    parent: dict[str, str] = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    scores: dict[tuple, float] = {}
    for i, older in enumerate(items):
        parent.setdefault(_cid(older), _cid(older))
        for newer in items[i + 1:]:
            parent.setdefault(_cid(newer), _cid(newer))
            hit = score_suspected_duplicate(
                (newer.get("data") or {}), older,
                name_index=name_index, now_dt=now, window_days=window,
                # INGESTDUP1 review N-1 — the workspace_root this function
                # already holds must reach the scorer, for two reasons that are
                # the same reason. (a) D1's owner resolution is what makes a
                # pair written as an id on one side and that person's NAME on
                # the other decidable, and this digest is SPEC INGESTDUP1 §1's
                # named "merge verb for the stock": without it the one pair the
                # fix exists for stays ungrouped here. (b) Withholding it is a
                # measured RECALL LOSS, not merely a missed improvement — the
                # pre-fix asymmetry let a legacy-name owner read as NO owner (a
                # vacuous pass), while the symmetric raw compare correctly sees
                # two different strings and vetoes; only the resolution rescues
                # it. Reviewer measured this exact shape at 1 group before the
                # D1 change and 0 after, on byte-identical input.
                workspace_root=workspace_root)
            if not hit or not hit.get("corroborated"):
                continue
            if not _shares_a_counterparty(older, newer,
                                          workspace_root=workspace_root):
                continue
            scores[(_cid(older), _cid(newer))] = hit["score"]
            union(_cid(older), _cid(newer))

    by_root: dict[str, list] = {}
    for ev in items:
        by_root.setdefault(find(_cid(ev)), []).append(ev)
    groups: list = []
    for root, members in by_root.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda ev: (parse_ts(event_time(ev))
                                     or _dt.datetime.max.replace(
                                         tzinfo=_dt.timezone.utc), _cid(ev)))
        survivor, absorbed = members[0], members[1:]
        groups.append({
            "survivor_id": _cid(survivor),
            "survivor_title": _title(survivor),
            "survivor_captured": event_time(survivor) or "",
            "absorbed": [{"commitment_id": _cid(m), "title": _title(m),
                          "captured": event_time(m) or "",
                          "score": scores.get((_cid(survivor), _cid(m)))}
                         for m in absorbed],
        })
    groups.sort(key=lambda g: g["survivor_captured"])
    return groups


# ---------------------------------------------------------------------------
# Phase A — the scan
# ---------------------------------------------------------------------------

def _mint_batch_id(now_iso=None) -> str:
    """A batch id unique to THIS run, not merely to this second.

    Review F-5. The `inr_` / `rcc_` precedent stamps `<prefix><UTC to the
    second>`, which is enough for a rail that fires on a schedule. This one is
    human-triggered: a scan, a look, an Apply and a second scan can all land
    inside one second, and two runs sharing a batch id means ONE `undo` reverses
    BOTH — including the run the user was happy with. The `swb_` batch IS the
    undo contract, so it cannot be almost-unique.

    An 8-hex suffix from `secrets` (not `random`, so nothing here depends on a
    seeded global some other module may have touched) turns a same-second
    collision from a certainty into a roughly 1-in-4-billion-per-pair accident.
    The shape stays `swb_<UTC>-<8 hex>`: still sortable by time, still readable
    aloud, and still matched by everything that looks for the `swb_` prefix.

    WHY 8 AND NOT 4 (re-verify F-9). The first cut used 4 hex — 65,536 values —
    which is plenty for the real hazard (two human-triggered runs in one second)
    but is a birthday problem the moment anything asks "are N mints distinct?":
    over 200 draws from 65,536 the chance of at least one collision is **26%**.
    The test written to pin this property was therefore red about one run in
    four on a clean tree, and a gate that fails a quarter of the time trains
    people to re-run the battery instead of reading it — which is worse than the
    defect it was pinning. Widening the SHIPPED salt fixes both halves at once:
    the same 200-draw check now collides about 5 times in a million, and the
    real uniqueness guarantee gets four extra bytes of headroom for free. The
    width is a property of the id, so a test asserting it is asserting the
    contract, not a coincidence.
    """
    import secrets
    stamp = _now_dt(now_iso).astimezone(
        _dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{BATCH_PREFIX}{stamp}-{secrets.token_hex(BATCH_SALT_BYTES)}"


def _evidence_row(proposal, *, leg: str) -> dict:
    return {
        "commitment_id": proposal.get("commitment_id"),
        "title": proposal.get("title") or "",
        "primary_thread_id": proposal.get("primary_thread_id") or "",
        "score": proposal.get("score"),
        "close_basis": proposal.get("close_basis") or "",
        "evidence": proposal.get("evidence") or "",
        "message_id": proposal.get("message_id") or "",
        "ts": proposal.get("ts") or "",
        "owner_id": proposal.get("owner_id") or "",
        "leg": leg,
    }


def last_scan(workspace_root) -> Optional[dict]:
    """The newest `backlog_sweep` audit event's `data`, or None.

    The resume point lives on the receipt, the same way `catchup.last_successful_point`
    reads its window off the task's own audit trail rather than a side file. A
    caller resumes with `resume_after=last_scan(ws)["resume_after"]`.
    """
    from cru_match import load_events_defensively
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not events_path.exists():
        return None
    events, _skipped = load_events_defensively(events_path, since_ts=None)
    latest = None
    for e in events:
        if e.get("type") == AUDIT_EVENT_TYPE:
            latest = e
    return (latest.get("data") or {}) if latest else None


def scan(
    workspace_root,
    *,
    user_person_id,
    sent_messages=None,
    inbound_messages=None,
    now_iso=None,
    window_days: Optional[int] = None,
    age_out_days: Optional[int] = None,
    item_cap: Optional[int] = None,
    dup_window_days: Optional[int] = None,
    provider=None,
    dry_run: bool = False,
    resume_after=None,
    fetch_blocked=None,
    source_skill: str = SOURCE_SKILL,
) -> dict:
    """Phase A — scan the backlog, apply the auto tier, write ONE audit event.

    The caller's only job before this: fetch historical mail through the MAILSEAM
    seam over the window this module hands it (`window_intent` +
    `structured_window_params`) and resolve each message's people, exactly as the
    daily rails' callers do. Everything else happens here.

    `dry_run=True` writes NOTHING except this scan's own `backlog_sweep` audit
    event. No closure, no archive, no proposal row. The audit is still written
    because a scan that leaves no trace is indistinguishable from a scan that
    never happened — the same reasoning as the blocked-run receipt on both mail
    rails — and its `dry_run: true` field is what tells a reader which it was.

    In DEFAULT mode (M's ruling 2026-07-29) the auto tier applies immediately:
    exactly the closes the v5.6.0 rails would have made in real time, narrowed to
    the two evidence bases, in one `swb_` batch that `undo` lists and reverses.
    The three manual buckets — proposed, merge candidates, age-out — write nothing
    until Apply.

    RAISES `PrimaryUserUnresolvedError` when `user_person_id` is falsy. Inherited
    from both rails, and it matters more here: the user id is the owner gate on the
    sent leg AND the direction check on the inbound leg, so with no user every
    basis is inert and this would write a clean audit claiming an empty backlog.
    """
    from reconcile_sent_commitments import PrimaryUserUnresolvedError
    if not user_person_id:
        msg = (
            "backlog sweep ABORTED: the primary user is unresolved "
            "(resolve_primary_user returned None/empty). The sent leg's owner "
            "gate and the inbound leg's direction check both compare against "
            "the user, so every basis would be inert and this run would report "
            "an empty backlog. No audit event written. Fix: pass the WORKSPACE "
            "ROOT (not _hq) to resolve_primary_user, or set "
            "workspace.user_id in entities.json (Bug #102)."
        )
        print(msg, file=sys.stderr)
        raise PrimaryUserUnresolvedError(msg)

    provider = resolve_mail_provider(workspace_root, provider)
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    cap = DEFAULT_ITEM_CAP if item_cap is None else max(1, int(item_cap))
    start_iso = window_start(now_iso, window_days)
    batch_id = _mint_batch_id(now_iso)

    # R4 / F-28 — the projection is threaded, so the roster reads below and the
    # MC1 all-received stamp on these rows agree about who the counterparties are.
    opens_raw = load_open_commitments(str(events_path),
                                      workspace_root=workspace_root)
    # INTAKE — unconfirmed extractions (`data.pending_review`) are queue
    # members, not open commitments. They are excluded from EVERYTHING
    # downstream (coverage, eligibility, age-out, duplicate grouping): the
    # sweep must never chase, age out, or merge something nobody has agreed
    # is real work. Their count rides the coverage block so the digest can
    # say where they went instead of silently shrinking the total.
    opens_all, needs_review = split_pending_review(opens_raw)
    coverage = coverage_block(opens_all, workspace_root=workspace_root)
    coverage["n_needs_review"] = len(needs_review)

    # The scan ORDER is oldest capture first — burn down rot from the bottom, and
    # make the cap resumable: `resume_after` is a capture ts, so the next run
    # starts where this one stopped instead of re-reading the same head.
    def _key(ev):
        return (parse_ts(event_time(ev))
                or _dt.datetime.max.replace(tzinfo=_dt.timezone.utc), _cid(ev))

    eligible = sorted(cru_eligible(opens_all), key=_key)
    resume_dt = parse_ts(resume_after) if resume_after else None
    if resume_dt is not None:
        eligible = [ev for ev in eligible
                    if (parse_ts(event_time(ev)) or resume_dt) > resume_dt]

    reachable = [ev for ev in eligible
                 if mail_reachable(ev, workspace_root=workspace_root)]
    scanned = reachable[:cap]
    has_more = len(reachable) > cap
    next_resume = (event_time(scanned[-1]) or "") if scanned else (resume_after or "")

    blocked = str(fetch_blocked or "").strip()
    sent_kept, sent_counts = messages_in_window(
        [] if blocked else sent_messages,
        window_start_iso=start_iso, now_iso=now_iso)
    inbound_kept, inbound_counts = messages_in_window(
        [] if blocked else inbound_messages,
        window_start_iso=start_iso, now_iso=now_iso)

    # --- the two legs, scored by the RAILS' OWN drivers -------------------
    # Nothing below calls a matcher. `reconcile_sent` / `reconcile_inbound` pass
    # every fence parameter these rows need, including each message's own `ts` as
    # `send_ts` / `inbound_ts` (EVORDER layer 3) — which is why the sweep cannot
    # score an un-orderable message even by mistake, and why the post-filter
    # above drops messages with no usable `ts` rather than letting the guard go
    # inert across 180 days of mail.
    sent_res = reconcile_sent(
        scanned, sent_kept, user_person_id=user_person_id, provider=provider,
        # NOT passed: `exclude_captured_since`. Layer 2 fences against the start
        # of THIS FIRE, and every commitment in a backlog sweep predates the fire
        # by construction — so it would exclude nothing while reading as if it
        # fenced something. Layer 1 (each message's own ref) and layer 3 (the
        # ordering guard) are the two that do the work here, and the drivers pass
        # both internally.
        workspace_root=workspace_root,
    ) if sent_kept else {"auto_close": [], "pending": [], "partial": [],
                         "signal_fields": {"n_stale_evidence_skipped": 0,
                                           "n_fetched": 0}}
    inbound_res = reconcile_inbound(
        scanned, inbound_kept, user_person_id=user_person_id, provider=provider,
        workspace_root=workspace_root,
    ) if inbound_kept else {"auto_close": [], "pending": [], "partial": [],
                            "updated": [],
                            "signal_fields": {"n_stale_evidence_skipped": 0,
                                              "n_fetched": 0}}

    # --- the auto/proposed split, on the IMPORTED bar ---------------------
    auto: list = []
    proposed: list = []
    for leg, res in (("sent", sent_res), ("inbound", inbound_res)):
        for p in res.get("auto_close") or []:
            row = _evidence_row(p, leg=leg)
            # `closes_on_evidence` is cru_match's object, imported. A row the
            # RAILS graded auto_resolve on the TITLE path (including FS-11's
            # unambiguous-moderate promotion) lands in PROPOSED regardless of
            # score — over 180 days a subject that echoes the deliverable's name
            # scores 0.750 by itself (F-19).
            (auto if closes_on_evidence(p) else proposed).append(row)
        for p in res.get("pending") or []:
            proposed.append(_evidence_row(p, leg=leg))

    auto_ids = {r["commitment_id"] for r in auto}
    proposed = [r for r in proposed if r["commitment_id"] not in auto_ids]
    touched = auto_ids | {r["commitment_id"] for r in proposed}

    aged = age_out_candidates(opens_all, events_path=events_path,
                              now_iso=now_iso, age_out_days=age_out_days,
                              exclude_ids=touched)
    merges = duplicate_groups(opens_all, workspace_root=workspace_root,
                             now_iso=now_iso, window_days=dup_window_days)

    stale_skipped = (
        int((sent_res.get("signal_fields") or {}).get("n_stale_evidence_skipped", 0))
        + int((inbound_res.get("signal_fields") or {})
              .get("n_stale_evidence_skipped", 0)))

    # --- the auto tier applies (unless dry-run) ---------------------------
    applied: list = []
    n_closed = 0
    if auto and not dry_run:
        applied = _close_rows(workspace_root, auto, batch_id=batch_id,
                              source_skill=source_skill,
                              resolution=EVIDENCE_RESOLUTION)
        closed_ok = {r["commitment_id"] for r in applied
                     if r.get("status") in ("closed", "already_resolved")}
        n_closed = sum(1 for r in applied if r.get("status") == "closed")
        # A row the closer refused (a pending_review item, a parent with open
        # sub-items, an id that no longer resolves) is not an auto-close — it
        # becomes a proposal, which is what the refusal MEANS.
        for row in auto:
            if row["commitment_id"] not in closed_ok:
                proposed.append(dict(row, refused=True))
        auto = [r for r in auto if r["commitment_id"] in closed_ok]

    receipt = {
        "ran": True,
        "batch_id": batch_id,
        "dry_run": bool(dry_run),
        "blocked": bool(blocked) or None,
        "blocked_reason": blocked or None,
        "window_start": start_iso,
        "window_days": (DEFAULT_WINDOW_DAYS if window_days is None
                        else int(window_days)),
        "age_out_days": (DEFAULT_AGE_OUT_DAYS if age_out_days is None
                         else int(age_out_days)),
        "mail_provider": provider,
        "item_cap": cap,
        "n_open_total": coverage["n_open"],
        # INTAKE — unconfirmed extractions excluded from n_open_total above.
        # Named here so the digest can account for them instead of leaving
        # the reader to wonder why the open count shrank.
        "n_needs_review": coverage["n_needs_review"],
        "n_scanned": len(scanned),
        "n_reachable_total": len(reachable),
        "has_more": has_more,
        "resume_after": next_resume,
        "auto_closed": auto,
        "n_auto_closed": len(auto),
        "n_closure_events": n_closed,
        "proposed": proposed,
        "n_proposed": len(proposed),
        "merge_candidates": merges,
        "n_merge_groups": len(merges),
        "age_out": aged,
        "n_age_out": len(aged),
        "coverage": coverage,
        "signal_fields": {
            "sent": sent_counts,
            "inbound": inbound_counts,
            "n_stale_evidence_skipped": stale_skipped,
            "n_sent_scored": (sent_res.get("signal_fields") or {}).get("n_fetched", 0),
            "n_inbound_scored": (inbound_res.get("signal_fields") or {}).get(
                "n_scored", (inbound_res.get("signal_fields") or {}).get("n_fetched", 0)),
            # The two ways the inbound leg goes quiet with nothing wrong in the
            # matcher, both forwarded from the driver's own counters rather than
            # re-derived. `n_from_user_skipped` is the DIRECTION stop counting
            # itself: the user's own message in an inbound batch (a thread they
            # replied to last, a Sent row that leaked into an inbox fetch) is
            # refused, and this is the only place that refusal is visible from
            # the outside. Without it, "the direction stop worked" and "the
            # direction stop is gone and the message quietly scored nothing"
            # produce the identical receipt — which is exactly what review F-2(b)
            # found, and why the pin now reads this number.
            "n_from_user_skipped": (inbound_res.get("signal_fields") or {}).get(
                "n_from_user_skipped", 0),
            "n_sender_unresolved": (inbound_res.get("signal_fields") or {}).get(
                "n_sender_unresolved", 0),
        },
    }
    receipt["summary"] = summarize(receipt)
    _write_audit(workspace_root, events_path, receipt, source_skill=source_skill)
    return receipt


def _close_rows(workspace_root, rows, *, batch_id, source_skill, resolution,
                extra=None, user_confirmed: bool = False) -> list:
    """Close a list of rows through `close_commitments` — THE single closure path.

    Every closure carries its evidence and the two undo stamps, so `undo` lists
    and reverses a sweep run through the already-registered `commitment_close`
    reverser. No new reverser, no new batch kind, no hand-rolled append.

    PROV1 — a sweep close has no message behind it; it is the USER's decision
    inside one sweep run, so its pointer is that run's receipt
    (`session:<batch_id>`, the same batch id the undo handle is keyed on).
    Per-row pointers would be a lie here: every row in the batch was closed by
    the same act.

    REVAMN1 — `user_confirmed` defaults to False and STAYS False for every
    confirmed-tier caller, unchanged. It is True on exactly one path: the
    review-tier verbs, whose whole population is `pending_review` rows that
    `close_commitment` refuses to close without it. That refusal is the floor
    "no path may AUTO-resolve an unconfirmed extraction", and the review verbs
    clear it the same way `needs_review_queue.drop_items` does — by being
    reached only from a preview-and-confirm ritual the user answered out loud.
    Passing it here rather than defaulting it True module-wide is deliberate:
    an amnesty over the CONFIRMED pile must keep hitting that floor, which is
    what stops a smuggled review row closing inside an amnesty batch.
    """
    from commitment_state import close_commitments
    closures = []
    for row in rows:
        data = {"brain_batch_id": batch_id,
                "brain_change_class": CLOSE_CHANGE_CLASS,
                "backlog_sweep": True}
        if row.get("close_basis"):
            data["close_basis"] = row["close_basis"]
        if extra:
            data.update(extra)
        closures.append({
            "commitment_id": row["commitment_id"],
            # On the sent leg the user's own send is the fulfillment, so the
            # closer is the owner; on the inbound leg the counterparty delivered
            # and `owner_id` on the row IS that person. Both come off the rails'
            # own proposal, never guessed here.
            "resolved_by": row.get("owner_id") or "",
            "evidence": row.get("evidence") or "",
            "primary_thread_id": row.get("primary_thread_id") or "",
            "resolution": resolution,
            "extra_data": data,
            "source_ref": f"session:{batch_id}",
            "user_confirmed": bool(user_confirmed),
        })
    return [dict(r, commitment_id=str(r.get("commitment_id")))
            for r in close_commitments(workspace_root, closures,
                                       source_skill=source_skill)]


def _write_audit(workspace_root, events_path, receipt, *, source_skill) -> None:
    """ONE `backlog_sweep` audit event per run — dry-run included.

    Written through `atomic_append_jsonl`, which allocates seq inside the
    writer lock (BUG-8330 item 7 — no hand-stamped seq). The slim lists
    carry ids and titles only; the digest is rendered from the return value,
    not from this row.
    """
    from atomic_write import atomic_append_jsonl as _append
    from cru_match import _now_iso as _audit_ts

    def _slim(items, key="commitment_id"):
        return [{key: i.get(key), "title": (i.get("title") or "")[:120]}
                for i in items]

    event = {
        "ts": _audit_ts(),
        "type": AUDIT_EVENT_TYPE,
        "source_skill": source_skill,
        "data": {
            "kind": "commitment-backlog-sweep",
            "status": "blocked" if receipt.get("blocked") else "complete",
            "blocked_reason": receipt.get("blocked_reason"),
            "dry_run": receipt["dry_run"],
            "batch_id": receipt["batch_id"],
            "window_start": receipt["window_start"],
            "window_days": receipt["window_days"],
            "age_out_days": receipt["age_out_days"],
            "mail_provider": receipt["mail_provider"],
            "item_cap": receipt["item_cap"],
            "n_open_total": receipt["n_open_total"],
            "n_needs_review": receipt["n_needs_review"],
            "n_scanned": receipt["n_scanned"],
            "n_reachable_total": receipt["n_reachable_total"],
            "has_more": receipt["has_more"],
            "resume_after": receipt["resume_after"],
            "n_auto_closed": receipt["n_auto_closed"],
            "n_proposed": receipt["n_proposed"],
            "n_merge_groups": receipt["n_merge_groups"],
            "n_age_out": receipt["n_age_out"],
            "auto_closed": _slim(receipt["auto_closed"]),
            "coverage": receipt["coverage"],
            "signal_fields": receipt["signal_fields"],
        },
    }
    try:
        # SCHED1 — the shared stamp helper, so the machine token and its
        # not-persisted flag land the same way here as on every other receipt.
        from receipts import machine_fields
        event["data"].update(machine_fields())
    except Exception:
        pass
    _append(events_path, [event])


def validate_sweep_ran(workspace_root, *, since_ts=None) -> dict:
    """Read the log back and confirm a REAL sweep ran (the ungameable half).

    A narrated "swept your backlog" with no `backlog_sweep` audit event returns
    ok=False. A blocked run is refused with its reason, same as both mail rails.
    """
    d = last_scan(workspace_root)
    if d is None:
        return {"ok": False, "ran": False,
                "reason": "no backlog_sweep audit event — the sweep did not "
                          "actually run"}
    if d.get("status") == "blocked":
        return {"ok": False, "ran": False,
                "reason": ("the historical mail read did not happen — "
                           + (d.get("blocked_reason") or "recorded as blocked")),
                "batch_id": d.get("batch_id")}
    if since_ts is not None:
        seen = parse_ts(d.get("window_start"))
        want = parse_ts(since_ts)
        if seen is None or want is None:
            return {"ok": False, "ran": True,
                    "reason": f"unreadable window on the latest audit "
                              f"({d.get('window_start')!r})"}
    return {"ok": True, "ran": True, "dry_run": bool(d.get("dry_run")),
            "batch_id": d.get("batch_id"),
            "n_auto_closed": d.get("n_auto_closed"),
            "n_proposed": d.get("n_proposed"),
            "has_more": d.get("has_more"),
            "resume_after": d.get("resume_after"),
            "coverage": d.get("coverage")}


# ---------------------------------------------------------------------------
# The digest
# ---------------------------------------------------------------------------

def summarize(receipt) -> str:
    """The plain-English line the digest header and the chat ack both use.

    Rule 4: no event-type names, no field names, no jargon. The stale-evidence
    count gets a full sentence of its own because a large number there is the
    fence WORKING, and a reader who does not know that will read it as breakage.

    THE BLOCKED BRANCH REPORTS WHAT IT STILL COMPUTED (review F-4). A blocked run
    cannot read mail, so the evidence buckets are genuinely empty — but the
    duplicate and gone-quiet buckets come off the event log and need no mail at
    all, so the digest legitimately renders them. Saying "nothing was changed" and
    then rendering two lists reads as a contradiction: the user sees work under a
    headline claiming none happened. So the blocked headline separates WRITTEN
    (nothing) from COMPUTED (whatever the log could answer), which is the same
    distinction the blocked receipt on both mail rails draws.
    """
    bits: list[str] = []
    if receipt.get("blocked"):
        line = ("I could not read your mail history: "
                + (receipt.get("blocked_reason") or "the connector was not "
                   "available")
                + ". Nothing was closed and nothing was changed.")
        still: list[str] = []
        if receipt["n_merge_groups"]:
            still.append(f"{receipt['n_merge_groups']} look like the same thing "
                         f"written twice")
        if receipt["n_age_out"]:
            still.append(f"{receipt['n_age_out']} have gone quiet for "
                         f"{receipt['age_out_days']}+ days")
        if still:
            line += (" I could still go through the list itself, though: "
                     + "; ".join(still)
                     + " — those are below, and answering them does not need "
                       "your mail.")
        return line
    n_auto = receipt["n_auto_closed"]
    if receipt["dry_run"]:
        bits.append(f"{n_auto} would close on evidence I found"
                    if n_auto else "nothing is clear enough to close on its own")
    else:
        bits.append(f"closed {n_auto} where the evidence was already in your mail"
                    if n_auto else
                    "nothing had evidence clear enough to close on its own")
    if receipt["n_proposed"]:
        bits.append(f"{receipt['n_proposed']} look handled but need your yes")
    if receipt["n_merge_groups"]:
        bits.append(f"{receipt['n_merge_groups']} look like the same thing "
                    f"written twice")
    if receipt["n_age_out"]:
        bits.append(f"{receipt['n_age_out']} have gone quiet for "
                    f"{receipt['age_out_days']}+ days")
    line = "Backlog sweep: " + "; ".join(bits) + "."
    stale = receipt["signal_fields"]["n_stale_evidence_skipped"]
    if stale:
        line += (f" I ignored {stale} older message"
                 f"{'s' if stale != 1 else ''} that arrived BEFORE the promise "
                 f"it would have closed — that is the safety check doing its job, "
                 f"not a problem.")
    cov = receipt["coverage"]
    unreachable = (cov["n_unreachable_no_counterparty"]
                   + cov["n_unreachable_no_mail_anchor"])
    if unreachable:
        line += (f" {unreachable} of your open items have nobody or no email "
                 f"trail attached, so mail can never settle them — the "
                 f"still-real? and duplicate lists are what serve those.")
    if receipt["has_more"]:
        line += (f" I stopped after {receipt['n_scanned']} of "
                 f"{receipt['n_reachable_total']} to keep this readable — say "
                 f"the same thing again to pick up where I left off.")
    return line


_CLUSTERABLE_BUCKETS = frozenset({"proposed", "age_out"})
_BUCKET_TITLES = {
    "proposed": "Looks handled — your call",
    "age_out": "Gone quiet — still real?",
}


def _cluster_digest_sections(sections: list, workspace_root) -> None:
    """CLUSTER1 — fold the digest's per-row lists, in place, per bucket.

    Render-level only, through `commitment_cluster.render_clusters` (the one
    clusterer — nothing here re-derives a similarity signal). Never crosses
    a bucket; the survivor keeps the section's own verbs and gains the
    `keep as one` tap with `data.folded_ids` embedded (the CLOSEID2 "id"
    door). Section titles restate the headline as the information count with
    the true row count in the same sentence (SPEC §0-4). Defensive: any
    failure leaves the sections exactly as built."""
    try:
        from cru_match import load_open_commitments
        from commitment_cluster import (CLUSTER_ACTION, folded_line,
                                        render_clusters)
        from pathlib import Path as _Path

        events_path = (_Path(workspace_root) / "_hq" / "data"
                       / "events.jsonl")
        opens = load_open_commitments(str(events_path),
                                      workspace_root=str(workspace_root))
        by_cid = {_cid(ev): ev for ev in opens}
        for sec in sections:
            rows = sec.get("items") or []
            buckets = {(r.get("data") or {}).get("bucket") for r in rows}
            if not (rows and buckets <= _CLUSTERABLE_BUCKETS):
                continue
            row_of = {}
            for r in rows:
                cid = str((r.get("data") or {}).get("id") or "")
                if cid and cid in by_cid:
                    row_of[cid] = r
            if len(row_of) < 2:
                continue
            clusters = render_clusters([by_cid[c] for c in row_of],
                                       workspace_root=str(workspace_root))
            clusters = [c for c in clusters
                        if all(m in row_of for m in c["member_ids"])]
            if not clusters:
                continue
            folded_away = set()
            for c in clusters:
                srow = row_of[c["survivor_id"]]
                srow["context_tag"] = (str(srow.get("context_tag") or "")
                                       + f" · +{c['n_folded']} folded — the "
                                         f"same real-world item")
                srow["folded_rows"] = [
                    folded_line(row_of[fid].get("n"),
                                row_of[fid].get("name") or "(untitled)")
                    for fid in c["folded_ids"]]
                srow["data"]["folded_ids"] = list(c["folded_ids"])
                srow["actions"] = list(srow.get("actions") or []) \
                    + [CLUSTER_ACTION]
                folded_away |= set(c["folded_ids"])
            if not folded_away:
                continue
            sec["items"] = [
                r for r in rows
                if str((r.get("data") or {}).get("id") or "")
                not in folded_away]
            info = len(sec["items"])
            bucket = next(iter(buckets))
            base = _BUCKET_TITLES.get(bucket)
            if base:
                sec["title"] = (f"{base} ({info} "
                                f"{'item' if info == 1 else 'items'} "
                                f"covering {len(rows)} rows)")
    except Exception as exc:  # pragma: no cover — the digest must render
        import sys as _sys
        _sys.stderr.write(f"[commitment_backlog_sweep] digest clustering "
                          f"skipped: {exc}\n")


def digest_view(receipt, *, page: Optional[int] = None,
                workspace_root=None) -> dict:
    """The data view for `widget_transport.render_and_persist`.

    Four sections in the spec's order, plus the coverage block. Every action verb
    is in `chat_output_renderer.CANONICAL_ACTIONS`; the auto-closed section
    carries NO actions at all — those already happened, and the honest affordance
    for them is the batch id plus one word (`undo`), not a button that re-decides
    a decision already made.

    `workspace_root` (CLUSTER1) turns on render-level clustering of the
    "Looks handled" and "Gone quiet" lists: rows the clusterer joins (the
    shipped duplicate scorer + roster counterparty conjunct + temporal
    adjacency, precision over recall) render as ONE line — survivor +
    "+N folded" + the read-only expand + the `keep as one` tap, ids
    widget-embedded. Clustering never crosses a bucket (a "looks handled"
    row and a "gone quiet" row are two different questions), never touches
    the merge or auto-closed sections (the merge section already IS one line
    per real-world item, and the auto-closed rows already happened), and
    never edits the flat `items` export — the true row list stays reachable.
    Omitted (the default), or with nothing clustering, the view is
    byte-identical to before this parameter existed.
    """
    n = 0
    sections: list = []
    all_items: list = []

    def _row(title, context, actions, data):
        nonlocal n
        n += 1
        row = {
            "n": str(n),
            # The renderer's own item shape: `icon` + `name` + `context_tag`.
            # Deliberately NOT a `type` key — `self_commitment` is the icon's
            # NAME in the validator's icon map, not an event type, and putting it
            # on a row makes the source-of-truth scanner read this module as a
            # writer of an unregistered event type. It was right to complain: a
            # module that writes events must not also carry strings that look
            # like event types it does not register.
            "icon": "⚙",
            "name": title or "(untitled)",
            "context_tag": context,
            "actions": list(actions),
            "src": "backlog-sweep",
            "data": data,
        }
        all_items.append(row)
        return row

    closed = [_row(r["title"], f"already closed — {r['evidence']}", [],
                   {"id": r["commitment_id"], "bucket": "auto_closed"})
              for r in receipt["auto_closed"]]
    if closed:
        sections.append({
            "title": f"Closed on evidence ({len(closed)}) — "
                     f"say undo to reverse the whole run",
            "items": closed})

    proposed = [_row(r["title"],
                     (f"looks handled — {r['evidence']}" if r.get("evidence")
                      else "looks handled"),
                     ["mark done", "still valid", "skip"],
                     {"id": r["commitment_id"], "bucket": "proposed"})
                for r in receipt["proposed"]]
    if proposed:
        sections.append({"title": f"Looks handled — your call ({len(proposed)})",
                         "items": proposed})

    merges = [_row(g["survivor_title"],
                   "also on your list as: "
                   + "; ".join(a["title"] or "(untitled)" for a in g["absorbed"]),
                   ["merge", "keep both"],
                   {"id": g["survivor_id"], "bucket": "merge",
                    "absorbed_ids": [a["commitment_id"] for a in g["absorbed"]]})
              for g in receipt["merge_candidates"]]
    if merges:
        sections.append({"title": f"The same thing, written twice ({len(merges)})",
                         "items": merges})

    aged = [_row(r["title"], f"no movement in {r['days_quiet']} days",
                 ["drop", "still valid"],
                 {"id": r["commitment_id"], "bucket": "age_out"})
            for r in receipt["age_out"]]
    if aged:
        sections.append({
            # ONE decision for the batch, not one question per row — the spec's
            # "batch still real?". The batch verbs are the renderer's own: `drop`
            # and `still valid` ride each row, and Snooze-rest arms `skip` across
            # every un-armed row in one click.
            "title": f"Gone quiet — still real? ({len(aged)})", "items": aged})

    # CLUSTER1 — fold the two per-row lists (see the docstring). Sections
    # only; `all_items` stays the true, complete export.
    if workspace_root is not None:
        _cluster_digest_sections(sections, workspace_root)

    # SWEEPRENDER (F-1) — the view speaks the RENDERER'S vocabulary, not this
    # module's private one. `title` / `headline` / `footer` were three names
    # `render_chat_output_widget` never looks up, and an unknown key renders as
    # nothing with no error to notice: every run that found anything dropped the
    # title, the summary (stale-evidence sentence and cap disclosure included)
    # and the entire coverage block. The honesty layer survived only on the
    # all-clear branch, which happens to read `footer` — so it rendered only
    # when there was nothing to be honest about.
    batch = bool(all_items)
    view = {
        "widget_mode": "all_batch_widget" if batch else "all_clear_summary",
        "surface": "backlog-sweep",
        "header": "Commitment backlog sweep",
        "sub_header": receipt["summary"],
        "sections": sections,
        # NOT rendered in either of this view's modes — only `onboarding_setup`
        # reads a top-level `items`. It is the flat row list in bucket order,
        # kept as a deliberate export for the tests that pin the four-bucket
        # ordering and the auto-closed-rows-carry-no-buttons contract. It holds
        # no prose, so unlike the F-1 keys nothing a reader was owed can go
        # missing through it. Do not add user-facing text here.
        "items": all_items,
    }
    # The coverage block goes under the key THIS mode actually renders — the
    # batch widget reads `quick_read`, the all-clear branch reads `footer`.
    # Emitting both would put an ignored key back into the view, which is the
    # defect itself rather than a belt-and-braces version of the fix.
    view["quick_read" if batch else "footer"] = _coverage_prose(receipt)
    if page is not None:
        # Caller echo only. The page the READER sees comes from `pagination`,
        # which `widget_transport.render_and_persist` computes and stamps; the
        # renderer reads that block and never this key.
        view["page"] = page
    return view


def _coverage_prose(receipt) -> str:
    """The coverage block, in words — the v5.6.0 receipt discipline, verbatim in
    spirit: say what was read, what could not be reached, and why."""
    cov = receipt["coverage"]
    sent = receipt["signal_fields"]["sent"]
    inbound = receipt["signal_fields"]["inbound"]
    lines = [
        f"Looked back {receipt['window_days']} days. "
        f"Read {sent['n_in_window']} message(s) you sent and "
        f"{inbound['n_in_window']} you received inside that window; "
        f"set aside {sent['n_out_of_window'] + inbound['n_out_of_window']} "
        f"that came back outside it and "
        f"{sent['n_no_usable_ts'] + inbound['n_no_usable_ts']} with no usable "
        f"date.",
        f"Checked {receipt['n_scanned']} of your "
        f"{cov['n_eligible']} open items — {cov['n_mail_reachable']} of them can "
        f"be settled by mail at all.",
    ]
    if cov["n_unreachable_no_counterparty"]:
        lines.append(
            f"{cov['n_unreachable_no_counterparty']} have no person attached, so "
            f"no message can ever be matched to them.")
    if cov["n_unreachable_no_mail_anchor"]:
        lines.append(
            f"{cov['n_unreachable_no_mail_anchor']} name a person but have no "
            f"email trail to search.")
    lines.append(
        f"{cov['n_thread_anchored']} of your open items are anchored to an email "
        f"conversation — that anchor is what lets someone's reply close an item "
        f"on its own, so with {cov['n_thread_anchored']} of them the reply side "
        f"of this sweep mostly proposes rather than closes.")
    if cov["n_meeting_sourced"]:
        lines.append(
            f"{cov['n_meeting_sourced']} came from meeting notes. Mail cannot "
            f"settle those, so the still-real? and duplicate lists above are what "
            f"serve most of that pile.")
    # INTAKE — say where the excluded ones went. Drop-empty at zero.
    n_nr = cov.get("n_needs_review") or 0
    if n_nr:
        lines.append(
            f"{n_nr} unconfirmed excluded — they live in the needs-your-call "
            f"queue until you confirm or drop them, so nothing here chased, "
            f"aged out, or merged them.")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# Phase B — apply
# ---------------------------------------------------------------------------

def apply_decisions(workspace_root, decisions, *, user_person_id, batch_id=None,
                    source_skill: str = SOURCE_SKILL, now_iso=None) -> dict:
    """Phase B — carry out the user's choices from the digest. ONE batch per run.

    `decisions` is the apply-choices payload, already parsed:
        [{"commitment_id": str, "bucket": "proposed"|"age_out"|"merge",
          "action": str, "evidence": str|None,
          "absorbed_ids": [str]}]   # merge only

    Every write lands through a canonical writer:
      * `proposed` + `mark done`  -> `close_commitments` (THE closure path),
        resolution `done`, the row's own evidence carried onto the event;
      * `age_out` + `drop`        -> `close_commitments`, resolution `dropped`
        plus `resolution_reason: "aged_out"`. Nothing is deleted; the item is
        closed, reversibly, and the reason distinguishes it from a user's own drop;
      * `review_expiry` + `expire` and `ingest_kill` + `kill` (REVAMN1)
        -> `close_commitments`, resolution `dropped` plus the verb's own
        `resolution_reason` (`review_expired` / `ingest_killed`) and
        `user_confirmed=True`. These two are the ONLY buckets that reach the
        UNCONFIRMED-EXTRACTION tier, and the reason is not caller-supplied: it
        is looked up from the bucket, so a row cannot arrive wearing a stamp of
        its own choosing;
      * `merge`                   -> `supersede_commitment(user_confirmed=True)`
        per absorbed item, folding into the OLDEST item as the survivor. That
        writer already unions both sides' provenance into `merged_source_refs`,
        which the loader folds onto the survivor's projected copy — so the
        survivor inherits the circularity fence's protection for every absorbed
        source, not just its own.
      * `still valid` / `keep both` / `skip` -> nothing is written. A decision to
        leave something alone is not an event.

    A merge is NEVER automatic: `user_confirmed=True` is passed only because this
    function is reached from an explicit Apply, and `auto_merge` is never used
    here at all.
    """
    from commitment_state import (
        CommitmentIdError, OpenSubitemsError, PendingReviewError,
        supersede_commitment,
    )
    batch_id = batch_id or _mint_batch_id(now_iso)
    closes: list = []
    drops: list = []
    merges: list = []
    skipped: list = []
    # REVAMN1 — the review-tier rows, kept per BUCKET so each carries its own
    # reason stamp. Two verbs, two piles, one closure path.
    review: dict = {b: [] for b in REVIEW_TIER_BUCKETS}
    for d in decisions or []:
        action = str(d.get("action") or "").strip().lower()
        bucket = str(d.get("bucket") or "").strip().lower()
        if action in ("still valid", "keep both", "skip"):
            skipped.append({"commitment_id": d.get("commitment_id"),
                            "action": action})
            continue
        if bucket == "proposed" and action == "mark done":
            closes.append(d)
        elif bucket == "age_out" and action == "drop":
            drops.append(d)
        elif bucket == "merge" and action == "merge":
            merges.append(d)
        elif (bucket in REVIEW_TIER_BUCKETS
                and action == REVIEW_TIER_BUCKETS[bucket]["action"]):
            review[bucket].append(d)
        else:
            skipped.append({"commitment_id": d.get("commitment_id"),
                            "action": action, "why": "not a sweep verb"})

    results: list = []
    if closes:
        results += _close_rows(
            workspace_root,
            [{"commitment_id": c.get("commitment_id"),
              "title": c.get("title") or "",
              "owner_id": c.get("owner_id") or user_person_id,
              "primary_thread_id": c.get("primary_thread_id") or "",
              "close_basis": c.get("close_basis") or "",
              "evidence": c.get("evidence")
                          or "you confirmed this from the backlog sweep"}
             for c in closes],
            batch_id=batch_id, source_skill=source_skill,
            resolution=EVIDENCE_RESOLUTION,
            extra={"user_confirmed_from": "backlog-sweep"})
    if drops:
        results += _close_rows(
            workspace_root,
            [{"commitment_id": c.get("commitment_id"),
              "title": c.get("title") or "",
              "owner_id": c.get("owner_id") or user_person_id,
              "primary_thread_id": c.get("primary_thread_id") or "",
              "evidence": c.get("evidence")
                          or "no movement for months — you cleared it in the "
                             "backlog sweep"}
             for c in drops],
            batch_id=batch_id, source_skill=source_skill,
            resolution=AGE_OUT_RESOLUTION,
            extra={"resolution_reason": AGE_OUT_REASON,
                   "user_confirmed_from": "backlog-sweep"})
    # REVAMN1 — the review tier. Same closure path, same batch, same undo; the
    # two differences are the reason stamp (looked up from the bucket, never
    # read off the row) and `user_confirmed=True`, which is what lets an
    # unconfirmed extraction close at all.
    for bucket, rows in review.items():
        if not rows:
            continue
        spec = REVIEW_TIER_BUCKETS[bucket]
        results += _close_rows(
            workspace_root,
            [{"commitment_id": c.get("commitment_id"),
              "title": c.get("title") or "",
              "owner_id": c.get("owner_id") or user_person_id,
              "primary_thread_id": c.get("primary_thread_id") or "",
              "evidence": c.get("evidence") or spec["evidence"]}
             for c in rows],
            batch_id=batch_id, source_skill=source_skill,
            resolution=REVIEW_TIER_RESOLUTION,
            extra={"resolution_reason": spec["reason"],
                   "user_confirmed_from": "backlog-sweep"},
            user_confirmed=True)

    merged: list = []
    for group in merges:
        survivor = group.get("commitment_id")
        for absorbed in group.get("absorbed_ids") or []:
            try:
                out = supersede_commitment(
                    workspace_root, survivor, absorbed,
                    merged_by=user_person_id, source_skill=source_skill,
                    evidence="same commitment, written twice — you merged them "
                             "in the backlog sweep",
                    user_confirmed=True,
                    brain_batch_id=batch_id,
                    brain_change_class=MERGE_CHANGE_CLASS,
                    # PROV1 — the merge DECISION's pointer is this sweep run's
                    # receipt (the absorbed items keep their own capture refs
                    # in `merged_source_refs`).
                    source_ref=f"session:{batch_id}",
                )
                merged.append({"survivor_id": survivor,
                               "commitment_id": absorbed,
                               "status": out.get("status")})
            except (CommitmentIdError, PendingReviewError, OpenSubitemsError,
                    ValueError) as exc:
                sys.stderr.write(
                    f"[backlog-sweep] merge {absorbed!r} -> {survivor!r} "
                    f"refused: {type(exc).__name__}: {exc}\n")
                merged.append({"survivor_id": survivor,
                               "commitment_id": absorbed,
                               "status": "error",
                               "error": type(exc).__name__})

    n_closed = sum(1 for r in results if r.get("status") == "closed")
    n_merged = sum(1 for m in merged if m.get("status") == "superseded")
    return {
        "ran": True,
        "batch_id": batch_id,
        "n_closed": n_closed,
        "n_merged": n_merged,
        "n_skipped": len(skipped),
        "closed": results,
        "merged": merged,
        "skipped": skipped,
        "summary": _apply_summary(n_closed, n_merged, len(skipped)),
    }


def _amnesty_summary(n_planned, n_applied, threshold_days) -> str:
    """The plain-English ack for a bulk clear. Rule 4: no field names, no jargon.

    DRIFT IS SAID OUT LOUD, never folded into the total. `n_planned` is what the
    re-derivation lined up a moment before the write; `n_applied` is what the
    closure path actually closed. They differ when something in the pile was
    settled between the two — another surface closing it, a second window, the
    same person answering an email. Reporting only the applied count would make a
    partial run read as a whole one, and the whole point of re-deriving is that
    the pile is allowed to move under us.
    """
    n_applied = int(n_applied)
    n_planned = int(n_planned)
    line = (f"Cleared {n_applied} item{'' if n_applied == 1 else 's'} that had "
            f"gone quiet for {threshold_days}+ days.")
    drift = n_planned - n_applied
    if drift > 0:
        line += (f" {drift} of the {n_planned} I lined up had already been "
                 f"settled by the time I got to them, so "
                 f"{'that one' if drift == 1 else 'those'} stayed as "
                 f"{'it was' if drift == 1 else 'they were'}.")
    return line + " Say `undo` if that was wrong — it reopens the whole batch."


def _apply_summary(n_closed, n_merged, n_skipped) -> str:
    bits = []
    if n_closed:
        bits.append(f"cleared {n_closed}")
    if n_merged:
        bits.append(f"merged {n_merged} into the item they duplicate")
    if n_skipped:
        bits.append(f"left {n_skipped} alone")
    if not bits:
        return "Nothing to do — nothing was changed."
    return ("Done: " + ", ".join(bits)
            + ". Say `undo` if any of that was wrong.")


# ---------------------------------------------------------------------------
# REFINT1 — the dangling review-proposal drain (writes, so it lives ABOVE the
# AMNESTY heading: everything below it is fenced to write only through
# apply_decisions — run_commitment_backlog_sweep_test §5)
# ---------------------------------------------------------------------------

def dangling_review_drain(workspace_root, *, now_iso=None,
                          apply: bool = False,
                          source_skill: str = "cleanup") -> dict:
    """REFINT1 layer 2 — drain review proposals whose commitment was never
    created.

    The write gate now refuses new ones (event_gate 4d), but the class left
    residue on live substrates: rows that exist, carry a live question
    (`proposed_resolution: auto_resolve`, scores 0.43–0.5 on the observed
    three), and are reachable by NO surface — the review tier and amnesty
    derive from `commitment` events, the queue adapter reads a 7-day window
    and drops title-less rows, so a dangling proposal appears in no count
    and no list while "nothing has sat unanswered" reads literally true.

    Silent permanent invisibility is the bug, so the exit is neither silent
    nor a deletion: each orphaned QUESTION is terminally closed with a
    `commitment_review_dismissed` (built by the CANONICAL builder — the same
    writer shape the queue's own Skip uses) carrying the lapse reason under
    the SHARED key (`RESOLUTION_REASON_KEY: DANGLING_TARGET_REASON`) so
    `is_non_dismissal_closure` readers — confidence calibration above all —
    can tell a system drain from the CEO's "not relevant". The report lines
    carry every row for a human note (cleanup's Monday note surfaces them
    once). A dismissal is the right tombstone — it claims no work exists,
    which is exactly the truth here — and it is deliberately outside the
    gate's reference wall.

    Termination is ORDER-AWARE and reads the whole closer family, exactly as
    the queue's own loader does (`commitment_review_dismissed` plus
    `closure_index.CLOSER_TYPES`, targets via `closer_target_id`): a cid's
    question is open when its newest proposal came AFTER its newest terminal
    event, so a tombstone written today cannot hide a fresh orphan replayed
    tomorrow, and a question already ended by a merge or a thread resolve is
    never re-tombstoned. Resolution honors the full chain + seq aliases
    (`resolve_closure_target`) — a proposal whose id string is garbage but
    whose `commitment_seq` names a real commitment is a LIVE question, never
    a drain candidate. Scans the FULL log: the 7-day read window is one of
    the ways these rows went invisible, and the drain must not inherit it.

    Safety rails: `apply=True` runs the whole scan→append inside the events
    writer lock (the R1c discipline every sibling writer keeps), and refuses
    outright when the log has unparseable lines — a commitment whose
    creation row is corrupt would read exactly like one never created, and a
    tombstone is not the remedy for corruption (the heal pass is). Proposals
    carrying no readable target at all are counted and reported
    (`n_unaddressable`), never tombstoned — a tombstone needs a cid to
    terminate anything.

    Returns {"rows", "n", "applied", "lines", "n_unaddressable",
    "n_skipped_lines", "refused"}. `apply=False` (default) is a dry run; a
    second applied run finds nothing (the tombstone terminates).
    """
    from cru_match import (build_commitment_review_dismissed_event,
                           load_events_defensively)
    from closure_index import (CLOSER_TYPES, build_commitment_universe,
                               closer_target_id, resolve_closure_target)
    from event_types import DANGLING_TARGET_REASON, RESOLUTION_REASON_KEY

    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    now = _now_dt(now_iso)

    def _compute():
        events, skipped = load_events_defensively(str(events_path))
        by_id, by_seq = build_commitment_universe(events)

        terminal_at: dict = {}
        groups: dict = {}
        n_unaddressable = 0
        for i, ev in enumerate(events):
            et = ev.get("type") or ev.get("event")
            if et == "commitment_review_proposed":
                cid = closer_target_id(ev)
                if not cid:
                    n_unaddressable += 1
                    continue
                g = groups.setdefault(cid, {"evs": [], "last_idx": -1})
                g["evs"].append(ev)
                g["last_idx"] = i
            elif et == "commitment_review_dismissed" or et in CLOSER_TYPES:
                cid = closer_target_id(ev)
                if cid:
                    terminal_at[cid] = i

        rows = []
        for cid, g in groups.items():
            if terminal_at.get(cid, -1) > g["last_idx"]:
                continue  # the question was ended AFTER its newest asking
            if any(resolve_closure_target(ev, by_id, by_seq) is not None
                   for ev in g["evs"]):
                continue  # a live, answerable question — not ours
            stamps = [parse_ts(event_time(ev)) for ev in g["evs"]]
            oldest = min((s for s in stamps if s is not None), default=None)
            age = (int((now - oldest).total_seconds() // 86400)
                   if oldest is not None else None)
            title = ""
            for ev in g["evs"]:
                title = str((ev.get("data") or {}).get("title") or "").strip()
                if title:
                    break
            rows.append({
                "commitment_id": cid,
                "title": title,
                "proposal_seqs": sorted(ev.get("seq") for ev in g["evs"]
                                        if isinstance(ev.get("seq"), int)),
                "age_days": age,
            })
        rows.sort(key=lambda r: (-(r["age_days"] if r["age_days"] is not None
                                   else 10 ** 6), r["commitment_id"]))
        return rows, n_unaddressable, len(skipped)

    def _lines(rows, n_unaddressable):
        lines = [
            (f"Closed as unresolvable: {r['title'] or r['commitment_id']!r}"
             + (f" (proposed {r['age_days']}d ago)"
                if r["age_days"] is not None else "")
             + " — its work item was never created")
            for r in rows
        ]
        if n_unaddressable:
            lines.append(
                f"{n_unaddressable} review proposal(s) name no target at all "
                "— nothing to terminate, flagged for the heal pass")
        return lines

    def _tombstones(rows):
        batch = []
        for r in rows:
            ev = build_commitment_review_dismissed_event(
                commitment_id=r["commitment_id"], primary_thread_id="",
                source_skill=source_skill, next_seq=None)
            ev.pop("seq", None)
            ev["data"].update({
                RESOLUTION_REASON_KEY: DANGLING_TARGET_REASON,
                "dangling_proposal_seqs": r["proposal_seqs"],
                "title": r["title"],
            })
            batch.append(ev)
        return batch

    if not apply:
        rows, n_unaddressable, n_skipped = _compute()
        return {"rows": rows, "n": len(rows), "applied": False,
                "lines": _lines(rows, n_unaddressable),
                "n_unaddressable": n_unaddressable,
                "n_skipped_lines": n_skipped, "refused": None}

    from event_gate import append_event
    from writer_lock import events_writer_lock
    with events_writer_lock(events_path,
                            holder=f"dangling_review_drain:{source_skill}"):
        rows, n_unaddressable, n_skipped = _compute()
        if n_skipped:
            return {"rows": rows, "n": len(rows), "applied": False,
                    "lines": _lines(rows, n_unaddressable) + [
                        f"NOT applied: the log has {n_skipped} unparseable "
                        "line(s) — a corrupt creation row would read as "
                        "never-created, and a tombstone is not the remedy "
                        "for corruption. Run the heal pass first."],
                    "n_unaddressable": n_unaddressable,
                    "n_skipped_lines": n_skipped,
                    "refused": "unparseable_lines"}
        if rows:
            append_event(events_path, _tombstones(rows), holder=source_skill)
    return {"rows": rows, "n": len(rows), "applied": bool(rows),
            "lines": _lines(rows, n_unaddressable),
            "n_unaddressable": n_unaddressable,
            "n_skipped_lines": n_skipped, "refused": None}


# ---------------------------------------------------------------------------
# AMNESTY — the quiet pile cleared in ONE confirm (SPEC_AMNESTY1, 2026-08-16)
# ---------------------------------------------------------------------------
#
# WHY THIS IS NOT "REPLAY THE DIGEST". The `backlog_sweep` audit event does not
# persist the age-out rows — `_write_audit`'s `_slim` runs over `auto_closed`
# and nothing else — so a later-turn amnesty CANNOT be composed from
# `last_scan()`. That is not a gap to patch; it is the right shape. The quiet
# pile is a QUESTION ABOUT THE PRESENT ("what has had no movement for N days?"),
# and `age_out_candidates` answers it from `events.jsonl` alone: no mail read,
# no connector, no cursor. So amnesty re-derives, every time, and an item that
# stopped being quiet between the offer and the yes simply is not in the answer.
#
# WHY THE FENCE LIVES HERE AND NOT IN SKILL PROSE. The caller-side-fence lesson:
# a rail whose only guard is a sentence in a SKILL.md is a rail with no guard,
# because prose is advice and a module is a contract. `apply_amnesty` therefore
# takes NO row list at all — there is no parameter through which a caller could
# hand it a `proposed` row, a `merge` group, or a stale pile from ten minutes
# ago. It re-derives, stamps the bucket and the verb from module constants, and
# refuses anything that arrives wearing a different one.

# The distinguishable stamp on a bulk clear (§0-4). Zero new event types and
# zero new writers: the close events are already told apart by
# `resolution_reason: "aged_out"` PLUS this source_skill, and `undo` reverses
# them through the reverser that is already registered.
AMNESTY_SOURCE_SKILL = f"{SOURCE_SKILL}:amnesty"

# The ONLY bucket and verb amnesty may compose. Constants, not caller input —
# see `_amnesty_decisions`.
AMNESTY_BUCKET = "age_out"
AMNESTY_ACTION = "drop"

# The hard floor, in days (§0-3). Below this the phrase stops meaning "clear the
# rot" and starts meaning "close this week's work", which is a different act and
# nobody asks for it in one confirm. Refused with an honest line, no writes.
AMNESTY_MIN_DAYS = 7

# How many rows the prose preview names before it starts counting. A confirm the
# user cannot read is a confirm they cannot give.
AMNESTY_PREVIEW_ROWS = 8

# The digest offers the bulk verb once the quiet pile is big enough that working
# it row by row is the thing nobody will do (§0-1). The number lives here so the
# skill text and any future surface read it from ONE place.
AMNESTY_OFFER_AT = 10

# ACCEPT-ALL (§0-5) — the post-digest bulk verb for the "looks handled" pile.
# `ACCEPT_SOURCE_KEY` is THE fence: the receipt carries four lists and exactly
# one of them is composable here. Point it anywhere else and a quiet item closes
# as `done` — a false completion, which is the specific harm this key prevents.
ACCEPT_SOURCE_KEY = "proposed"
ACCEPT_BUCKET = "proposed"
ACCEPT_ACTION = "mark done"


def _configured_age_out_days(workspace_root) -> Optional[int]:
    """The workspace's own `age_out_days`, or None — the SAME config key the
    sweep reads (§0-3), unwrapped the way every other reader unwraps it.

    `load_skill_config` returns the whole envelope (`schema_version`,
    `configured_at`, `skill_name`, `config`), so the value lives one level down;
    reading the envelope directly is the mistake that makes a configured
    workspace silently fall back to the default. Tolerates the flat shape too,
    and never raises — an unreadable config is "not configured", not an abort.
    """
    try:
        from skill_config_writer import load_skill_config
        saved = load_skill_config(workspace_root, SOURCE_SKILL)
    except Exception:
        return None
    if not isinstance(saved, dict):
        return None
    cfg = saved.get("config") if isinstance(saved.get("config"), dict) else saved
    try:
        return int((cfg or {}).get("age_out_days"))
    except (TypeError, ValueError):
        return None


def _amnesty_preview(rows, threshold_days) -> str:
    """The short prose list under the confirm — oldest first, titles and days."""
    if not rows:
        return f"Nothing has gone quiet for {threshold_days}+ days."
    head = list(rows)[:AMNESTY_PREVIEW_ROWS]
    lines = [f"- {(r.get('title') or '(untitled)')} — quiet "
             f"{r.get('days_quiet')} days" for r in head]
    rest = len(rows) - len(head)
    if rest > 0:
        lines.append(f"- ...and {rest} more")
    return "\n".join(lines)


def _amnesty_confirm(n, threshold_days) -> str:
    """THE confirm line (§0-2) — prose, not a widget button.

    Count, threshold, reversibility and the undo handle all ride ONE sentence,
    because a bulk verb the user says yes to from memory is a bulk verb whose
    consequences were never on screen. A widget button would need a new verb in
    the renderer's action taxonomy mid-cycle; a section-level bulk-arm control in
    the digest is a follow-up, not this build.
    """
    if not n:
        return (f"Nothing has gone quiet for {threshold_days}+ days — there is "
                f"nothing to clear.")
    return (f"Drop all {n} item{'' if n == 1 else 's'} quiet for "
            f"{threshold_days}+ days? They close reversibly as aged-out — "
            f"nothing is deleted — and one `undo` reopens all {n}.")


def amnesty_plan(workspace_root, *, older_than_days=None, now_iso=None) -> dict:
    """The quiet pile, derived LIVE. Writes nothing, reads no mail.

    Same opens-loading and eligibility chain `scan()` uses, in the same order:
    `load_open_commitments` (workspace threaded, F-28) -> `split_pending_review`
    -> `age_out_candidates` (which applies `cru_eligible` itself, and drops any
    item whose activity cannot be dated — age-out is an argument from silence,
    and silence you cannot measure is not evidence).

    THE SPLIT IS THE FENCE, AND IT NOW CUTS BOTH WAYS (REVAMN1). There are two
    piles under `load_open_commitments` and two bulk verbs, one per pile, and
    neither verb may ever reach the other's rows:

      * CONFIRMED work — this function's pile. `amnesty` clears the quiet half
        of it. An unconfirmed extraction is not open work and is never in here;
        it was nobody's promise until somebody said so.
      * UNCONFIRMED EXTRACTIONS (`data.pending_review`) — the needs-your-call
        queue's pile, and the review tier's. `review expiry` lapses the ones
        nobody answered inside the window and `ingest kill` clears one bad
        ingest's cluster, both under their own stamps, both closing as an
        expiry rather than as the CEO's own Drop. See the REVAMN1 section.

    Until REVAMN1 the second pile had no bulk verb at all and this docstring
    said so ("must never be cleared in bulk by anyone"). What has changed is
    that the review tier now has ITS OWN verb, not that this one reaches it:
    `amnesty` still cannot touch a `pending_review` row, and the disjointness
    is proved from both ends — by REMOVING each fence in turn — in
    `tests/run_revamn1_review_amnesty_test.py`.

    Threshold resolution, in order: the caller's `older_than_days` ("amnesty
    everything past 90 days"), else the workspace's configured `age_out_days`,
    else `DEFAULT_AGE_OUT_DAYS`. Below `AMNESTY_MIN_DAYS` the plan is REFUSED
    (`ok: False`) carrying the honest line — refusing in the PLAN is what makes
    the floor real for `apply_amnesty` too, which re-derives through here.

    Returns `{ok, refused, reason, threshold_days, rows, n, preview, confirm}`.
    `rows` are `age_out_candidates` rows verbatim, oldest first.
    """
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if older_than_days is None:
        threshold = _configured_age_out_days(workspace_root)
        if threshold is None:
            threshold = DEFAULT_AGE_OUT_DAYS
    else:
        threshold = int(older_than_days)

    if threshold < AMNESTY_MIN_DAYS:
        reason = (
            f"I will not clear things in bulk at {threshold} days — the floor is "
            f"{AMNESTY_MIN_DAYS}. Under a week is this week's work, not a "
            f"backlog, and one confirm is the wrong shape for it. Nothing was "
            f"changed. Say a longer window and I will show you the pile.")
        return {"ok": False, "refused": "threshold_below_floor",
                "reason": reason, "threshold_days": threshold,
                "rows": [], "n": 0, "preview": reason, "confirm": reason}

    opens_raw = load_open_commitments(str(events_path),
                                      workspace_root=workspace_root)
    opens, needs_review = split_pending_review(opens_raw)
    rows = age_out_candidates(opens, events_path=events_path, now_iso=now_iso,
                              age_out_days=threshold)
    return {
        "ok": True,
        "refused": None,
        "reason": "",
        "threshold_days": threshold,
        "rows": rows,
        "n": len(rows),
        "n_needs_review": len(needs_review),
        "preview": _amnesty_preview(rows, threshold),
        "confirm": _amnesty_confirm(len(rows), threshold),
    }


def _amnesty_decisions(rows) -> list:
    """Plan rows -> the `apply_decisions` payload. THE quiet-pile fence.

    Two things happen here and both are load-bearing:

      * every composed row is stamped `AMNESTY_BUCKET` / `AMNESTY_ACTION` from
        the module constants, so amnesty cannot compose a `mark done` or a
        `merge` even if some future caller wanted one;
      * a row that ARRIVES already wearing a different bucket or verb is
        DROPPED, not restamped. Restamping would be the bug: it would take a
        "looks handled" row — which needs mail evidence and a per-row yes — and
        launder it into the bulk drop the user actually confirmed. Refusing is
        the only honest answer, because the row is not what this plan is about.

    Removing the bucket/verb comparison below is the mutation the suite runs: a
    smuggled `proposed` row then closes inside an amnesty batch.
    """
    out: list = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        bucket = str(r.get("bucket") or AMNESTY_BUCKET).strip().lower()
        action = str(r.get("action") or AMNESTY_ACTION).strip().lower()
        if bucket != AMNESTY_BUCKET or action != AMNESTY_ACTION:
            continue
        cid = str(r.get("commitment_id") or "").strip()
        if not cid:
            continue
        out.append({
            "commitment_id": cid,
            "bucket": AMNESTY_BUCKET,
            "action": AMNESTY_ACTION,
            # The item's own thread rides along so the close lands on it rather
            # than on the closer's default. Nothing else from the plan row is
            # carried: the evidence line for an age-out is not the row's, it is
            # the ACT's, and `apply_decisions` writes that one.
            "primary_thread_id": r.get("primary_thread_id") or "",
        })
    return out


def apply_amnesty(workspace_root, *, user_person_id, older_than_days=None,
                  batch_id=None, now_iso=None) -> dict:
    """Clear the quiet pile in ONE undoable batch. Re-derives; trusts no caller.

    There is deliberately no `rows` / `plan` / `decisions` parameter. TOCTOU: the
    pile is derived at offer time and confirmed a turn later, and in between an
    item can be closed by any other surface. A caller-passed list would re-close
    it (a duplicate tombstone) or, worse, close something the user never saw
    because the list was stale in the other direction. Re-deriving means an item
    that stopped being quiet silently falls out — which is the correct behaviour
    and needs no special case anywhere.

    Every write goes through `apply_decisions` unchanged — same closure path,
    same `dropped` + `aged_out` stamps, same reversibility — under
    `AMNESTY_SOURCE_SKILL` so a bulk clear is distinguishable from the per-row
    drop, and inside ONE `swb_` batch so one `undo` covers all N.

    Refuses, writing NOTHING, in exactly two cases: the threshold is under the
    floor, and the plan is empty. Both return an honest line rather than a clean
    zero — "cleared 0" and "there was nothing to clear" are different facts.

    Returns the `apply_decisions` receipt plus `n_planned` / `n_applied` /
    `n_drifted`, so a partial run reports the gap instead of hiding it.
    """
    plan = amnesty_plan(workspace_root, older_than_days=older_than_days,
                        now_iso=now_iso)
    threshold = plan.get("threshold_days")
    if not plan.get("ok"):
        return {"ran": False, "applied": False,
                "refused": plan.get("refused"), "reason": plan.get("reason"),
                "threshold_days": threshold, "batch_id": batch_id,
                "n_planned": 0, "n_applied": 0, "n_drifted": 0, "n_closed": 0,
                "closed": [], "merged": [], "skipped": [],
                "summary": plan.get("reason")}

    decisions = _amnesty_decisions(plan.get("rows"))
    n_planned = len(decisions)
    if not n_planned:
        reason = (f"Nothing has gone quiet for {threshold}+ days, so there is "
                  f"nothing to clear. Nothing was changed.")
        return {"ran": False, "applied": False, "refused": "empty_plan",
                "reason": reason, "threshold_days": threshold,
                "batch_id": batch_id,
                "n_planned": 0, "n_applied": 0, "n_drifted": 0, "n_closed": 0,
                "closed": [], "merged": [], "skipped": [], "summary": reason}

    out = dict(apply_decisions(workspace_root, decisions,
                               user_person_id=user_person_id,
                               batch_id=batch_id,
                               source_skill=AMNESTY_SOURCE_SKILL,
                               now_iso=now_iso))
    n_applied = int(out.get("n_closed") or 0)
    out.update({
        "applied": True,
        "refused": None,
        "reason": "",
        "threshold_days": threshold,
        "n_planned": n_planned,
        "n_applied": n_applied,
        "n_drifted": n_planned - n_applied,
        "summary": _amnesty_summary(n_planned, n_applied, threshold),
    })
    return out


def accept_handled_decisions(receipt) -> list:
    """The "looks handled" pile -> `mark done` rows, each with its OWN evidence.

    POST-DIGEST ONLY, and that is a property of the data, not a preference. The
    proposed rows are built from MAIL, and mail evidence is persisted nowhere:
    `_write_audit` slims `auto_closed` alone, so nothing on disk can rebuild this
    pile. It exists while a receipt is in hand and not one turn longer — a fresh
    sweep re-creates it. That is why this function takes a RECEIPT and not a
    workspace path: there is no derivation to re-run.

    THE FENCE is `ACCEPT_SOURCE_KEY`. A sweep receipt carries four lists, and
    exactly one of them may be composed here. `age_out` rows are quiet, not
    delivered — closing one as `done` writes a completion that never happened,
    and every count and recap downstream reads it back as work finished.
    `merge_candidates` are a judgment call about two rows being one, which no
    bulk verb gets to make. `auto_closed` is already closed. So: `proposed`,
    nothing else, and a row arriving with a foreign bucket or verb is refused
    rather than restamped.

    Returns `[]` on an empty or missing pile — the caller says so and applies
    nothing, which is different from applying an empty list and reporting a
    successful run of zero. The caller hands the result to `apply_decisions`
    with the DIGEST'S OWN `batch_id`, so one `undo` covers the run's auto-closes,
    its accept-alls and its amnesty drops alike. `already_resolved` rows no-op
    honestly, per the existing apply contract.
    """
    rows = receipt.get(ACCEPT_SOURCE_KEY) if isinstance(receipt, dict) else None
    out: list = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        bucket = str(r.get("bucket") or ACCEPT_BUCKET).strip().lower()
        action = str(r.get("action") or ACCEPT_ACTION).strip().lower()
        if bucket != ACCEPT_BUCKET or action != ACCEPT_ACTION:
            continue
        cid = str(r.get("commitment_id") or "").strip()
        if not cid:
            continue
        out.append({
            "commitment_id": cid,
            "bucket": ACCEPT_BUCKET,
            "action": ACCEPT_ACTION,
            # THE ROW'S OWN evidence — never a shared line. Each of these closes
            # on a different message, and one sentence stamped across all of them
            # would be a plausible-looking lie on all but the first.
            "evidence": r.get("evidence") or "",
            "title": r.get("title") or "",
            "owner_id": r.get("owner_id") or "",
            "primary_thread_id": r.get("primary_thread_id") or "",
            "close_basis": r.get("close_basis") or "",
        })
    return out


# ---------------------------------------------------------------------------
# REVAMN1 — the REVIEW TIER: aging + two bulk verbs for the unconfirmed pile
# ---------------------------------------------------------------------------
#
# WHAT THE TIER IS (§0-1, ruled). `data.pending_review` — the whole flag, one
# definition, the same one `cru_match.split_pending_review` draws and the
# needs-your-call queue renders. Not a narrower "eligible" subset: a second
# definition of the same pile is how two surfaces come to disagree about one
# number, and this pile is already the biggest number in the workspace.
#
# WHY IT NEEDS VERBS AT ALL. An unconfirmed extraction is a GUESS. It costs
# nothing to write and it is barred from auto-close and chase, so nothing in
# the system ever retires one — the pile only grows, and a pile nobody can
# answer is a pile nobody opens. The queue's per-row verbs are correct and
# unchanged; they are simply not a plan for hundreds of rows.
#
# THE TWO VERBS, AND WHY THEY ARE NOT ONE:
#
#   review expiry   time. A guess nobody answered inside the window has
#                   lapsed. Nothing is asserted about whether it was real.
#   ingest kill     provenance. One ingest went wrong and wrote a cluster of
#                   junk; the user names THAT ingest and the cluster goes.
#                   Safer than age, because it targets a defect rather than a
#                   date, which is why it carries no threshold and no floor.
#
# THREE THINGS BOTH VERBS ARE NOT:
#
#   * NOT a Drop. A Drop is the CEO looking at one row and letting it go, and
#     two learners read it as a per-counterparty suppression signal. A lapse
#     carries no such judgment, so neither verb feeds them (§0-3 — the reason
#     set lives in `event_types`, and `run_revamn1_review_amnesty_test` pins
#     that an expiry proposes no suppression where a Drop would).
#   * NOT a delete, and NOT a bar on re-capture. The capture stays in history,
#     one `undo` reopens the batch, and a later re-mention is FRESH EVIDENCE
#     that gets captured normally (§0-3).
#   * NOT the fix for the inflow. Expiry drains the STOCK. What stops the pile
#     refilling is the capture side deciding better which extractions to
#     commit and which to surface — a different build. The skill says both, in
#     those words, because a client told only the first half will clear the
#     pile once and conclude the product does not work.
#
# THE SHAPE IS AMNESTY1's, DELIBERATELY: plan -> verbatim prose confirm ->
# apply that re-derives and takes no row list. Every write still goes through
# `apply_decisions`; this section adds no writer, no event type and no
# reverser, and its closes ride the same `swb_` batch family so one `undo`
# covers a review run exactly as it covers an amnesty.

# The stamp that tells a review-tier bulk clear from everything else. ONE
# source_skill for the verb family — the per-verb distinction is the
# `resolution_reason`, which is on every row, so a reader never has to join two
# fields to answer "what closed this".
REVIEW_SOURCE_SKILL = f"{SOURCE_SKILL}:review-amnesty"

# WHY `dropped` AND NOT `done`. Same reasoning as the age-out above, and
# sharper here: nobody ever agreed this item was real, so `done` would claim a
# completion of something that may never have been a promise. `superseded` is
# the merge word. `dropped` plus the reason is the honest pair, and
# `VALID_RESOLUTIONS` is not this module's to widen.
REVIEW_TIER_RESOLUTION = "dropped"

# §0-2 / UNCONFEXP1 — the nag window, in days: how long an unconfirmed
# extraction gets to ask before it lapses on its own. THE OPERATOR RULING
# (M, 2026-08-30, verbatim intent): "They should close out. They should hide
# and close out, not act like that. It can nag for like a day or two." That
# ruling SUPERSEDES both the REVAMN1 14-day default and the QUEUE1 ruling
# (2026-08-12) that the unconfirmed drain stays M-driven/escalate-only —
# measured on the live workspace, the escalate-and-nag posture produced
# accumulation (~156 unconfirmed, ~110 escalated, oldest 34 days), not drain.
#
# SEMANTICS (reviewer-visible default): the clock starts at CAPTURE — the
# row's own ts seeds `last_activity_map` — and resets only on REAL movement
# on the item (re-wording, re-dating, re-owning, an adjudication, an undo).
# Being RENDERED on a surface is deliberately NOT movement: a nag that
# extends its own life by being seen never expires, which is the exact
# pathology this window ends.
#
# Overridable per run ("expire the review pile past 30 days") and
# configurable per workspace — see `_configured_review_expiry_days`.
UNCONFIRMED_NAG_DAYS = 2

# The pre-UNCONFEXP1 spelling, kept because the plan/offer/receipt plumbing
# and their suites read it. ONE number, two names for it — never assign these
# independently.
REVIEW_EXPIRY_DAYS = UNCONFIRMED_NAG_DAYS

# §1 — the hard floor, in days. Under ONE day the phrase stops meaning
# "clear what has gone stale" and starts meaning "clear what I heard this
# morning", which is a different act. Refused with an honest line, no writes.
# UNCONFEXP1 lowered it from 3: the ruled default window is 2 days, and a
# floor sitting ABOVE the product's own default would refuse the product's
# own policy at every scheduled fire.
REVIEW_EXPIRY_MIN_DAYS = 1

# How many rows the preview names PER GROUP before it starts counting. Per
# group and not per pile, deliberately (§0-5): the whole point of the split is
# that the user sees their own promises and other people's separately, and a
# global cap would hide one group behind the other.
REVIEW_PREVIEW_ROWS = 8

# REVSCHED1 §3-1 — the bar at which a surface OFFERS the drain, mirroring
# `AMNESTY_OFFER_AT` for the confirmed tier. Below it the pile is workable row
# by row and an offer is noise; at or above it, working it row by row is the
# thing nobody does.
#
# WHY THIS CONSTANT EXISTS AT ALL. REVAMN1 shipped the drain reachable ONLY by
# saying `review amnesty` — no offer line anywhere. Measured on a real
# workspace over 17 days: of 224 unconfirmed captures, 3 were answered by
# review and 163 were still open. A drain nobody is reminded of is the landfill
# one level up, and the number lives HERE so the digest, the queue header and
# any future surface read it from one place.
REVIEW_OFFER_AT = 10

# The buckets and verbs, and the reason each one stamps. THE TABLE IS THE
# FENCE: `apply_decisions` looks the reason up from the bucket, so a row can
# never carry a stamp of its own choosing, and a bucket with no row here is
# not a review verb at all.
REVIEW_EXPIRY_BUCKET = "review_expiry"
REVIEW_EXPIRY_ACTION = "expire"
INGEST_KILL_BUCKET = "ingest_kill"
INGEST_KILL_ACTION = "kill"

REVIEW_TIER_BUCKETS = {
    REVIEW_EXPIRY_BUCKET: {
        "action": REVIEW_EXPIRY_ACTION,
        "reason": REVIEW_EXPIRY_REASON,
        "evidence": "nobody answered this unconfirmed capture inside the "
                    "review window — you let the pile lapse",
    },
    INGEST_KILL_BUCKET: {
        "action": INGEST_KILL_ACTION,
        "reason": INGEST_KILL_REASON,
        "evidence": "you cleared everything this ingest captured",
    },
}


def _configured_review_expiry_days(workspace_root) -> Optional[int]:
    """The workspace's own `review_expiry_days`, or None (§0-6).

    Same envelope-unwrapping as `_configured_age_out_days`, and the same
    never-raise posture: an unreadable config is "not configured", not an
    abort. A separate key from `age_out_days` on purpose — the two piles age
    at different speeds and one number for both would silently move whichever
    was tuned second.
    """
    try:
        from skill_config_writer import load_skill_config
        saved = load_skill_config(workspace_root, SOURCE_SKILL)
    except Exception:
        return None
    if not isinstance(saved, dict):
        return None
    cfg = saved.get("config") if isinstance(saved.get("config"), dict) else saved
    try:
        return int((cfg or {}).get("review_expiry_days"))
    except (TypeError, ValueError):
        return None


def _resolve_review_threshold(workspace_root, older_than_days) -> int:
    """The expiry window this run uses, in ONE place.

    Order: the number in the phrase, else the workspace's configured
    `review_expiry_days`, else `REVIEW_EXPIRY_DAYS`. Extracted because
    `review_offer` needs the same answer as `review_expiry_plan` and a second
    copy of a three-way resolution order is the classic place for the two to
    silently disagree — a workspace configured to 30 days would get an offer
    line counting a 14-day window.

    Does NOT apply the floor: the floor is a REFUSAL with prose attached, and
    that belongs to the plan. This function answers "what window", never
    "is that window allowed".
    """
    if older_than_days is not None:
        return int(older_than_days)
    configured = _configured_review_expiry_days(workspace_root)
    return REVIEW_EXPIRY_DAYS if configured is None else configured


def review_tier(workspace_root) -> list:
    """THE review tier: every open `data.pending_review` row, as events.

    One definition, one caller-visible function, so the plan builders, the
    apply-side membership fence and any future surface cannot drift apart
    (§0-1). Deliberately NOT narrowed by `cru_eligible`: that filter answers
    "can a mail matcher reach this", which is the wrong question for a pile
    nobody has agreed is real work yet, and applying it here would leave
    task-kind and sub-item guesses in a queue with no way out.
    """
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    opens_raw = load_open_commitments(str(events_path),
                                      workspace_root=workspace_root)
    return split_pending_review(opens_raw)[1]


def _owner_id(ev) -> str:
    from cru_match import _commitment_field
    return str(_commitment_field(ev, "owner_id") or "")


def _source_ref(ev) -> str:
    from cru_match import _commitment_field
    return str(_commitment_field(ev, "source_ref") or "").strip()


def _review_row(ev, *, user_person_id, seen=None, now=None) -> dict:
    """One plan row. `seen` is the item's newest movement, or None."""
    owner = _owner_id(ev)
    days = None
    if seen is not None and now is not None:
        days = int((now - seen).total_seconds() // 86400)
    return {
        "commitment_id": _cid(ev),
        "title": _title(ev),
        "primary_thread_id": ev.get("primary_thread_id") or "",
        "owner_id": owner,
        # §0-5 — the pre-sort. `mine` is TRUE only when the record says the
        # user owns it; an unattributed capture is not claimed for them, which
        # is why the surface says "not yours" rather than "theirs".
        "mine": bool(owner) and owner == str(user_person_id or ""),
        "source_ref": _source_ref(ev),
        "source_kind": _source_kind(ev),
        "last_activity": (seen.isoformat().replace("+00:00", "Z")
                          if seen is not None else ""),
        "days_quiet": days,
    }


def review_expiry_candidates(rows, *, events_path, user_person_id,
                             now_iso=None,
                             older_than_days: Optional[int] = None,
                             include_reopened: bool = False) -> list:
    """Review-tier rows with no movement for N days, oldest first.

    Movement comes from `last_activity_map` — the SAME baseline the
    confirmed-tier age-out measures, seeded with each item's own capture ts,
    so a guess that has never moved is measured from when it was written.

    An item whose activity cannot be dated is NOT a candidate, exactly as it
    is not one for the confirmed pile: an expiry is an argument from silence,
    and silence you cannot measure is not evidence.

    REVSCHED1 §0-3 — `include_reopened` is THE DOOR, and it is OFF by
    default, which is the whole design. `commitment_reopened` is movement
    (`commitment_activity.MOVEMENT_EVENT_TYPES`), so an `undo` of a previous
    expiry resets every reopened row's quiet clock — correct for a deliberate
    undo (REVAMN1: "the identical phrase will not re-lapse what the user just
    put back") and measured as a real cost on 2026-08-19, when one operator
    undo held 106 rows out of the bulk verb for a fresh window.

    With the flag ON the quiet clock is measured against every movement type
    EXCEPT the reopen, so:

      * a row whose ONLY movement since capture is a reopen is measured from
        whatever it last actually did, and can lapse again;
      * a row that was reopened AND THEN genuinely touched — re-worded,
        re-dated, re-owned, chased, adjudicated — is STILL SHIELDED by that
        touch, because that type is still in the set.

    That second half is what makes the flag a door and not a bulldozer, and
    it is why the set is computed by subtraction rather than written out.
    Never the default, never inferred: the caller has to have been told what
    it reaches, which is what the confirm sentence is for.
    """
    days = REVIEW_EXPIRY_DAYS if older_than_days is None else int(older_than_days)
    now = _now_dt(now_iso)
    cutoff = now - _dt.timedelta(days=max(1, days))
    activity = last_activity_map(
        events_path,
        movement_types=(_movement_types_without_reopen()
                        if include_reopened else None))
    out: list = []
    for ev in rows or []:
        cid = _cid(ev)
        if not cid:
            continue
        seen = activity.get(cid)
        if seen is None or seen > cutoff:
            continue
        out.append(_review_row(ev, user_person_id=user_person_id,
                               seen=seen, now=now))
    out.sort(key=lambda r: (-(r["days_quiet"] or 0), r["commitment_id"]))
    return out


def ingest_kill_candidates(rows, *, source_ref, events_path, user_person_id,
                           now_iso=None) -> list:
    """Review-tier rows captured from ONE ingest, newest movement first.

    Membership is the normalized ref key set (`granola:<id>` and the bare
    `<id>` are one meeting — the spelling drift is live in real substrate), so
    the user can name the ingest the way their own surface showed it.

    NO age threshold and no undated exclusion, unlike the expiry: this verb
    argues from PROVENANCE, not from silence. A cluster written by a bad
    ingest an hour ago is exactly the case it exists for, and a row whose
    movement cannot be dated is still unambiguously from that ingest.
    """
    # THE PUBLIC name for that derivation, deliberately: `meeting_ref_keys` is
    # exported (EODFIX1 §2-4) precisely so a second module can ask "is this the
    # same meeting" and get the SAME answer, and its own docstring says a
    # private name would have guaranteed a second copy. Reaching past it to
    # `_norm_ref_keys` would take that guarantee back for no gain.
    from meeting_capture import meeting_ref_keys
    wanted = meeting_ref_keys(source_ref)
    if not wanted:
        return []
    now = _now_dt(now_iso)
    activity = last_activity_map(events_path)
    out: list = []
    for ev in rows or []:
        cid = _cid(ev)
        if not cid or not (meeting_ref_keys(_source_ref(ev)) & wanted):
            continue
        out.append(_review_row(ev, user_person_id=user_person_id,
                               seen=activity.get(cid), now=now))
    out.sort(key=lambda r: (-(r["days_quiet"] or 0), r["commitment_id"]))
    return out


def _owner_split(rows) -> tuple:
    """(mine, not-mine) — §0-5's pre-sort, applied once so every caller of a
    plan sees the same two halves in the same order."""
    mine = [r for r in rows or [] if r.get("mine")]
    theirs = [r for r in rows or [] if not r.get("mine")]
    return mine, theirs


def _review_group_lines(rows, label) -> list:
    if not rows:
        return []
    head = list(rows)[:REVIEW_PREVIEW_ROWS]
    lines = [f"{label} ({len(rows)}):"]
    for r in head:
        days = r.get("days_quiet")
        age = f" — quiet {days} days" if isinstance(days, int) else ""
        lines.append(f"- {(r.get('title') or '(untitled)')}{age}")
    rest = len(rows) - len(head)
    if rest > 0:
        lines.append(f"- ...and {rest} more")
    return lines


def _review_preview(rows, *, empty_line) -> str:
    """The pile as prose, YOURS first and NOT YOURS second (§0-5).

    The split is on screen rather than folded into a total because roughly
    half of a real queue page is other people's promises, captured because
    they were said in the user's meeting. Someone deciding whether to clear
    hundreds of rows needs to see which half is which BEFORE they say yes;
    a single number hides exactly the fact that changes the answer.
    """
    if not rows:
        return empty_line
    mine, theirs = _owner_split(rows)
    lines = _review_group_lines(mine, "Yours")
    other = _review_group_lines(theirs, "Not yours")
    if lines and other:
        lines.append("")
    return "\n".join(lines + other)


def _split_clause(n, n_mine) -> str:
    """" (4 yours, 7 other people's.)" — the §0-5 split inside the confirm.

    Omitted when the pile is all one side: "0 other people's" is noise, and a
    parenthetical that restates the count the sentence just gave makes a long
    sentence longer without making it truer.
    """
    theirs = int(n) - int(n_mine)
    if not n_mine or not theirs:
        return ""
    return f" ({n_mine} yours, {theirs} other people's.)"


def _review_expiry_confirm(n, threshold_days, n_mine,
                           include_reopened: bool = False) -> str:
    """THE expiry confirm line — prose, and it carries the whole consequence.

    Count, the split, the bar, reversibility, the undo handle, AND the fact
    that clearing does not stop a re-mention being captured again (§0-3): a
    user who thinks an expiry means "never hear about this again" is being
    asked to say yes to something the system will not do.

    REVSCHED1 §0-3 — with the door open the sentence MUST say that it reaches
    rows the user personally put back, because that is the one thing this run
    does that the ordinary phrase does not, and it is the only warning the
    user gets. Saying it in the confirm rather than the ack is deliberate: an
    ack explains a write that already happened.
    """
    if not n:
        return (f"Nothing in the unconfirmed pile has sat unanswered for "
                f"{threshold_days}+ days — there is nothing to clear.")
    reopened_clause = (
        " This one INCLUDES rows you previously put back with `undo` — it "
        "measures how long they were quiet before you un-did them, so "
        "un-doing a clear no longer holds them out. Anything you have "
        "actually touched since is still left alone."
        if include_reopened else "")
    return (f"Clear all {n} unconfirmed capture{'' if n == 1 else 's'} nobody "
            f"has answered in {threshold_days}+ days?"
            f"{_split_clause(n, n_mine)}{reopened_clause} They close "
            f"reversibly — nothing is "
            f"deleted, and if any of them comes up again I will capture it "
            f"fresh — and one `undo` puts all {n} back in the queue.")


def _ingest_kill_confirm(n, label, n_mine) -> str:
    if not n:
        return (f"Nothing unconfirmed is left from {label} — there is nothing "
                f"to clear.")
    return (f"Clear all {n} unconfirmed capture{'' if n == 1 else 's'} from "
            f"{label}?{_split_clause(n, n_mine)} They close reversibly — "
            f"nothing is deleted, and anything real from that conversation "
            f"will be captured again the next time it comes up — and one "
            f"`undo` puts all {n} back in the queue.")


def _drift_causes(results) -> dict:
    """Why each lined-up row did not close, counted by cause (REVIEW_REVAMN1
    F-2). Read off the closure path's OWN per-row results — never guessed.

    `close_commitments` reports each row as `closed`, `already_resolved`, or
    `{"status": "error", "error": "<ExceptionName>"}`. Three causes matter to
    a person reading an ack, and they are three different facts:

      * `answered`  — someone settled it between the offer and the yes. The
        only cause the ack used to name, and the reason F-2 was a lie: it was
        asserted for every drift, whatever actually happened.
      * `held`      — `OpenSubitemsError`. The row owns open sub-items, and
        closing the parent would close them too, so the closure path refuses.
        The user is not told this anywhere else, and it is not a race: it will
        happen again on every run until the sub-items are dealt with, so an
        ack that calls it "already answered" sends them back to a queue where
        nothing looks wrong.
      * `refused`   — any other refusal. Named as a refusal WITHOUT a story,
        because inventing one is how F-2 happened in the first place.
    """
    out = {"answered": 0, "held": 0, "refused": 0}
    for r in results or []:
        if not isinstance(r, dict):
            continue
        status = str(r.get("status") or "")
        if status == "closed":
            continue
        if status == "already_resolved":
            out["answered"] += 1
        elif str(r.get("error") or "") == "OpenSubitemsError":
            out["held"] += 1
        else:
            out["refused"] += 1
    return out


def _review_summary(n_planned, n_applied, *, verb, results=None) -> str:
    """The plain-English ack. Drift is said out loud AND attributed.

    `n_planned` is what the re-derivation lined up, `n_applied` is what
    actually closed, and reporting only the second would make a partial run
    read as a whole one.

    F-2 (REVIEW_REVAMN1): this used to render every gap as "had already been
    answered", which is a claim about the user's own queue and is false for the
    one drift case this tier can produce that the confirmed tier cannot — an
    unconfirmed PARENT owning an open sub-item, where the closure path refuses
    and nothing is written. Telling someone their row was answered when it was
    refused sends them looking for an answer that is not there. Causes now come
    off the writer's own results.

    The undo offer is likewise conditional: `undo` on an empty batch reverses
    nothing, and offering it is a second small lie in the same sentence.
    """
    n_applied, n_planned = int(n_applied), int(n_planned)
    causes = _drift_causes(results)
    unattributed = max(0, (n_planned - n_applied) - sum(causes.values()))
    if unattributed:
        # Never silently absorb a gap the results cannot explain — an
        # unexplained row is a refusal we have no story for, which is exactly
        # what `refused` says.
        causes["refused"] += unattributed

    if n_applied:
        line = (f"Cleared {n_applied} unconfirmed capture"
                f"{'' if n_applied == 1 else 's'} {verb}.")
    else:
        line = "Nothing was cleared."

    def _n(k):
        return f"{causes[k]} of the {n_planned} I lined up"

    if causes["answered"]:
        one = causes["answered"] == 1
        line += (f" {_n('answered')} had already been answered by the time I "
                 f"got to them, so {'that one' if one else 'those'} stayed as "
                 f"{'it was' if one else 'they were'}.")
    if causes["held"]:
        one = causes["held"] == 1
        line += (f" {_n('held')} still {'has' if one else 'have'} smaller "
                 f"pieces open underneath {'it' if one else 'them'} — clearing "
                 f"the top one would have closed that work too, so I left "
                 f"{'it' if one else 'them'} alone. Deal with the smaller "
                 f"pieces first and this will go through.")
    if causes["refused"]:
        one = causes["refused"] == 1
        line += (f" {_n('refused')} refused to close and I have no reason to "
                 f"give you for {'it' if one else 'them'}, so nothing about "
                 f"{'it' if one else 'them'} changed.")

    if n_applied:
        line += " Say `undo` if that was wrong — it reopens the whole batch."
    return line


def _no_user_refusal(kind) -> dict:
    """Refuse, writing nothing, when the primary user is unresolved.

    §0-5 makes the owner split part of the contract, and "yours vs not yours"
    computed against nobody is not a smaller answer — it is a wrong one that
    would label the user's own promises as other people's on the exact screen
    where they decide to clear hundreds of rows. Same posture as `scan()`'s
    abort, returned as a refusal because every function in this family reports
    refusals in its return value rather than by raising.
    """
    reason = (
        "I could not work out whose workspace this is, so I cannot tell your "
        "own captures from other people's — and that split is the whole point "
        "of this screen. Nothing was changed. Fix: set the workspace's primary "
        "user, then ask again.")
    print(f"[backlog-sweep] {kind} ABORTED: primary user unresolved",
          file=sys.stderr)
    return {"ok": False, "refused": "primary_user_unresolved", "reason": reason,
            "rows": [], "n": 0, "n_mine": 0, "n_not_mine": 0,
            "preview": reason, "confirm": reason}


def review_expiry_plan(workspace_root, *, user_person_id,
                       older_than_days=None, now_iso=None,
                       include_reopened: bool = False) -> dict:
    """The lapsed half of the unconfirmed pile, derived LIVE. Writes nothing.

    Threshold resolution, in order: the caller's `older_than_days` ("expire
    the review pile past 30 days"), else the workspace's configured
    `review_expiry_days`, else `REVIEW_EXPIRY_DAYS`. Below
    `REVIEW_EXPIRY_MIN_DAYS` the plan is REFUSED carrying the honest line —
    refusing in the PLAN is what makes the floor real for
    `apply_review_expiry` too, which re-derives through here.

    Returns `{ok, refused, reason, threshold_days, include_reopened, rows, n,
    n_mine, n_not_mine, n_review_total, n_shielded_by_reopen, preview,
    confirm}`. `rows` are oldest first; `preview` is pre-sorted
    yours-then-not-yours (§0-5).

    REVSCHED1 §0-3 — `include_reopened` opens the door (default OFF; see
    `review_expiry_candidates`). `n_shielded_by_reopen` is the honest
    counterpart on a CLOSED-door plan: how many rows the door WOULD have
    reached, i.e. how many are being held out by a reopen and nothing else.
    Reported rather than acted on, because that is the number that makes the
    door's existence legible — and it is derived by running the same
    candidate function with the flag on and diffing, never guessed. On an
    OPEN-door plan it is 0 by construction: nothing is being shielded.
    """
    if not user_person_id:
        out = _no_user_refusal("review expiry")
        out["threshold_days"] = older_than_days
        out["n_review_total"] = 0
        out["include_reopened"] = bool(include_reopened)
        out["n_shielded_by_reopen"] = 0
        return out
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    threshold = _resolve_review_threshold(workspace_root, older_than_days)

    if threshold < REVIEW_EXPIRY_MIN_DAYS:
        reason = (
            f"I will not clear the unconfirmed pile in bulk at {threshold} "
            f"day{'' if threshold == 1 else 's'} — the floor is "
            f"{REVIEW_EXPIRY_MIN_DAYS}. Anything younger than that is what I "
            f"heard this week, and one confirm is the wrong shape for it. "
            f"Nothing was changed. Say a longer window and I will show you "
            f"the pile.")
        return {"ok": False, "refused": "threshold_below_floor",
                "reason": reason, "threshold_days": threshold,
                "include_reopened": bool(include_reopened),
                "rows": [], "n": 0, "n_mine": 0, "n_not_mine": 0,
                "n_review_total": 0, "n_shielded_by_reopen": 0,
                "preview": reason, "confirm": reason}

    tier = review_tier(workspace_root)
    rows = review_expiry_candidates(tier, events_path=events_path,
                                    user_person_id=user_person_id,
                                    now_iso=now_iso, older_than_days=threshold,
                                    include_reopened=include_reopened)
    mine, theirs = _owner_split(rows)
    # §0-3 honesty counterpart. Only on a CLOSED-door plan: with the door open
    # nothing is shielded, so re-deriving would be work spent computing zero.
    n_shielded = 0
    if not include_reopened:
        wide = review_expiry_candidates(
            tier, events_path=events_path, user_person_id=user_person_id,
            now_iso=now_iso, older_than_days=threshold,
            include_reopened=True)
        here = {r["commitment_id"] for r in rows}
        n_shielded = sum(1 for r in wide if r["commitment_id"] not in here)
    return {
        "ok": True,
        "refused": None,
        "reason": "",
        "threshold_days": threshold,
        "include_reopened": bool(include_reopened),
        "rows": rows,
        "n": len(rows),
        "n_mine": len(mine),
        "n_not_mine": len(theirs),
        # How big the whole tier is, so a surface can say "N of M" instead of
        # implying the window found everything there is.
        "n_review_total": len(tier),
        "n_shielded_by_reopen": n_shielded,
        "preview": _review_preview(
            rows,
            empty_line=(f"Nothing in the unconfirmed pile has sat unanswered "
                        f"for {threshold}+ days.")),
        "confirm": _review_expiry_confirm(len(rows), threshold, len(mine),
                                          include_reopened),
    }


def ingest_kill_plan(workspace_root, source_ref, *, user_person_id,
                     now_iso=None) -> dict:
    """Everything the review tier still holds from ONE ingest. Writes nothing.

    `source_ref` is the meeting / ingest id the user names, in either live
    spelling. A blank one is REFUSED rather than treated as "match nothing":
    a verb whose target failed to resolve must say so, because the alternative
    is a confident "nothing to clear" about a pile that is sitting right
    there.
    """
    if not user_person_id:
        out = _no_user_refusal("ingest kill")
        out["source_ref"] = source_ref
        out["n_review_total"] = 0
        return out
    label = str(source_ref or "").strip()
    if not label:
        reason = ("I need to know WHICH conversation or import to clear — say "
                  "the meeting or the ingest and I will show you what it left "
                  "behind. Nothing was changed.")
        return {"ok": False, "refused": "no_source_ref", "reason": reason,
                "source_ref": "", "rows": [], "n": 0, "n_mine": 0,
                "n_not_mine": 0, "n_review_total": 0,
                "preview": reason, "confirm": reason}

    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    tier = review_tier(workspace_root)
    rows = ingest_kill_candidates(tier, source_ref=label,
                                  events_path=events_path,
                                  user_person_id=user_person_id,
                                  now_iso=now_iso)
    mine, theirs = _owner_split(rows)
    return {
        "ok": True,
        "refused": None,
        "reason": "",
        "source_ref": label,
        "rows": rows,
        "n": len(rows),
        "n_mine": len(mine),
        "n_not_mine": len(theirs),
        "n_review_total": len(tier),
        "preview": _review_preview(
            rows,
            empty_line=f"Nothing unconfirmed is left from {label}."),
        "confirm": _ingest_kill_confirm(len(rows), label, len(mine)),
    }


def _review_decisions(rows, *, bucket, pending_ids) -> list:
    """Plan rows -> the `apply_decisions` payload. THE review-tier fence.

    THREE things happen here, and each one refuses rather than repairs:

      * every composed row is stamped from `REVIEW_TIER_BUCKETS`, so a review
        verb cannot compose a `mark done`, a `merge` or an `age_out` drop even
        if a caller wanted one;
      * a row that ARRIVES wearing a different bucket or verb is DROPPED, not
        restamped — restamping would launder a row from another pile into the
        bulk clear the user actually confirmed;
      * **a row whose id is not in `pending_ids` is DROPPED.** This is the
        disjointness fence, and it is the one that matters most, because the
        review path passes `user_confirmed=True` — the floor that stops every
        other bulk verb reaching an unconfirmed extraction is deliberately
        DOWN here, so nothing but this membership test stands between a
        smuggled CONFIRMED commitment and a silent expiry. `pending_ids` is
        derived from the substrate by the apply function itself, never handed
        in by a caller and never read off the plan.

    Removing the membership test is the mutation the suite runs: a confirmed,
    real commitment then closes inside a review-expiry batch.
    """
    spec = REVIEW_TIER_BUCKETS.get(bucket)
    if spec is None:
        return []
    live = {str(x) for x in (pending_ids or ())}
    out: list = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        row_bucket = str(r.get("bucket") or bucket).strip().lower()
        row_action = str(r.get("action") or spec["action"]).strip().lower()
        if row_bucket != bucket or row_action != spec["action"]:
            continue
        cid = str(r.get("commitment_id") or "").strip()
        if not cid or cid not in live:
            continue
        out.append({
            "commitment_id": cid,
            "bucket": bucket,
            "action": spec["action"],
            "primary_thread_id": r.get("primary_thread_id") or "",
        })
    return out


def _apply_review(workspace_root, plan, *, bucket, user_person_id, batch_id,
                  now_iso, empty_reason, verb) -> dict:
    """The shared apply half of both review verbs. Re-derives; trusts nothing.

    The plan arrives already re-derived by the caller (which is why neither
    public apply takes a row list). What happens HERE is the second,
    independent derivation: the live `pending_review` id set, read from the
    substrate a moment before the write, which is what `_review_decisions`
    fences against. Two derivations rather than one because the first is
    reachable by monkeypatch and the second is not — and because between the
    offer and the yes a row can be confirmed by any other surface, at which
    point it must fall out rather than be expired behind the user's back.
    """
    threshold = plan.get("threshold_days")
    # REVSCHED1 §3-4 — the receipt trio travels on EVERY return shape, so a
    # reader never has to tell "0 held back" from "this shape does not report
    # held-back at all". `n_shielded_by_reopen` is the plan's own count (the
    # plan derived it; this function does not re-derive and does not invent
    # one when the plan carried none).
    shielded = int(plan.get("n_shielded_by_reopen") or 0)
    door = bool(plan.get("include_reopened"))
    if not plan.get("ok"):
        return {"ran": False, "applied": False, "refused": plan.get("refused"),
                "reason": plan.get("reason"), "threshold_days": threshold,
                "source_ref": plan.get("source_ref"), "batch_id": batch_id,
                "include_reopened": door,
                "n_planned": 0, "n_applied": 0, "n_drifted": 0, "n_closed": 0,
                "n_shielded_by_reopen": shielded, "n_held_back": 0,
                "drift_causes": {"answered": 0, "held": 0, "refused": 0},
                "closed": [], "merged": [], "skipped": [],
                "summary": plan.get("reason")}

    pending_ids = {_cid(ev) for ev in review_tier(workspace_root)}
    decisions = _review_decisions(plan.get("rows"), bucket=bucket,
                                  pending_ids=pending_ids)
    n_planned = len(decisions)
    if not n_planned:
        return {"ran": False, "applied": False, "refused": "empty_plan",
                "reason": empty_reason, "threshold_days": threshold,
                "source_ref": plan.get("source_ref"), "batch_id": batch_id,
                "include_reopened": door,
                "n_planned": 0, "n_applied": 0, "n_drifted": 0, "n_closed": 0,
                "n_shielded_by_reopen": shielded, "n_held_back": 0,
                "drift_causes": {"answered": 0, "held": 0, "refused": 0},
                "closed": [], "merged": [], "skipped": [],
                "summary": empty_reason}

    out = dict(apply_decisions(workspace_root, decisions,
                               user_person_id=user_person_id,
                               batch_id=batch_id,
                               source_skill=REVIEW_SOURCE_SKILL,
                               now_iso=now_iso))
    n_applied = int(out.get("n_closed") or 0)
    # F-2 pattern — every cause on the receipt comes off the writer's OWN
    # per-row results. Nothing here asserts a cause the writer did not return:
    # `_drift_causes` folds an unexplained gap into `refused`, which is a
    # statement that we have no reason, not a reason.
    causes = _drift_causes(out.get("closed"))
    unattributed = max(0, (n_planned - n_applied) - sum(causes.values()))
    if unattributed:
        causes["refused"] += unattributed
    out.update({
        "applied": True,
        "refused": None,
        "reason": "",
        "threshold_days": threshold,
        "source_ref": plan.get("source_ref"),
        "include_reopened": door,
        "n_planned": n_planned,
        "n_applied": n_applied,
        "n_drifted": n_planned - n_applied,
        "n_shielded_by_reopen": shielded,
        "n_held_back": causes["held"],
        "drift_causes": causes,
        # F-2 — the ack is built from the writer's OWN per-row results, so the
        # cause it names is the cause that happened.
        "summary": _review_summary(n_planned, n_applied, verb=verb,
                                   results=out.get("closed")),
    })
    return out


def apply_review_expiry(workspace_root, *, user_person_id,
                        older_than_days=None, batch_id=None,
                        now_iso=None, include_reopened: bool = False) -> dict:
    """Lapse the unanswered half of the unconfirmed pile in ONE undoable batch.

    There is deliberately no `rows` / `plan` / `decisions` parameter, for the
    same TOCTOU reason `apply_amnesty` has none: the pile is derived at offer
    time and confirmed a turn later, and in between a row can be confirmed,
    dropped or answered on the queue. Re-deriving means it silently falls out,
    which is the correct behaviour and needs no special case anywhere.

    Refuses, writing NOTHING, in three cases: an unresolved primary user, a
    threshold under the floor, and an empty pile. Each returns an honest line
    rather than a clean zero.

    REVSCHED1 §0-3 — `include_reopened` rides through to the plan and is OFF
    by default. It is a keyword here for the same reason there is no `rows`
    parameter: the apply re-derives, so the flag has to be re-stated on the
    apply call and cannot be smuggled in on a plan object from a turn ago.
    """
    plan = review_expiry_plan(workspace_root, user_person_id=user_person_id,
                              older_than_days=older_than_days, now_iso=now_iso,
                              include_reopened=include_reopened)
    threshold = plan.get("threshold_days")
    return _apply_review(
        workspace_root, plan, bucket=REVIEW_EXPIRY_BUCKET,
        user_person_id=user_person_id, batch_id=batch_id, now_iso=now_iso,
        empty_reason=(f"Nothing in the unconfirmed pile has sat unanswered "
                      f"for {threshold}+ days, so there is nothing to clear. "
                      f"Nothing was changed."),
        verb=f"nobody had answered in {threshold}+ days")


def apply_ingest_kill(workspace_root, *, user_person_id, source_ref,
                      batch_id=None, now_iso=None) -> dict:
    """Clear one ingest's unconfirmed cluster in ONE undoable batch.

    Same no-row-list contract and the same re-derivation as the expiry. The
    ONLY caller input is the ingest id — a name for a defect, not a list of
    rows — so the widest thing this verb can ever reach is one ingest's
    review-tier output.
    """
    plan = ingest_kill_plan(workspace_root, source_ref,
                            user_person_id=user_person_id, now_iso=now_iso)
    label = plan.get("source_ref") or source_ref
    return _apply_review(
        workspace_root, plan, bucket=INGEST_KILL_BUCKET,
        user_person_id=user_person_id, batch_id=batch_id, now_iso=now_iso,
        empty_reason=(f"Nothing unconfirmed is left from {label}, so there is "
                      f"nothing to clear. Nothing was changed."),
        verb=f"from {label}")


# ---------------------------------------------------------------------------
# REVSCHED1 §3-1 — the offer, and §3-2 — the schedule
# ---------------------------------------------------------------------------

def review_offer(workspace_root, *, user_person_id=None, now_iso=None,
                 offer_at: Optional[int] = None) -> Optional[dict]:
    """ONE line offering the bulk drain, or None. PURE READ, never refuses.

    The mirror of the digest's `AMNESTY_OFFER_AT` line for the review tier
    (§3-1). Returns `{n, threshold_days, n_review_total, n_shielded_by_reopen,
    line}` when the lapsed count clears `REVIEW_OFFER_AT`, else None.

    THREE deliberate differences from a plan call:

      * it NEVER refuses. An offer is not a verb. A workspace whose primary
        user cannot be resolved gets no offer line — silence, not an error
        sentence pasted under a queue the user came to read.
      * it resolves the primary user itself when the caller has none, because
        both callers (the backlog digest and the needs-your-call queue view)
        are surfaces that do not otherwise need one.
      * every failure returns None. A broken offer line must never take down
        the surface it was going to decorate.

    The DOOR is deliberately not advertised here, and its count is not even
    computed: the offered phrase is the ordinary one, and an offer line is the
    wrong place to teach a verb that reaches rows the user personally put back.

    ONE derivation, deliberately. This runs on the daily needs-your-call path,
    and `review_expiry_plan` does the candidate pass TWICE on a closed-door
    plan (once for the rows, once to count what the door would have reached)
    plus builds a preview and a confirm sentence — all of it wasted here.
    So this calls `review_expiry_candidates` directly and takes the window
    from the SAME resolver the plan uses, which is why that resolver is its
    own function: two copies of the three-way window order would eventually
    have the offer line counting 14 days on a workspace configured to 30.
    The floor is applied here too, because a workspace configured below it
    gets no offer rather than an offer for a window the verb would refuse.
    """
    try:
        bar = REVIEW_OFFER_AT if offer_at is None else int(offer_at)
        uid = user_person_id
        if not uid:
            from primary_user import resolve_primary_user
            uid = resolve_primary_user(workspace_root)
        if not uid:
            return None
        days = _resolve_review_threshold(workspace_root, None)
        if days < REVIEW_EXPIRY_MIN_DAYS:
            return None
        events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        tier = review_tier(workspace_root)
        rows = review_expiry_candidates(tier, events_path=events_path,
                                        user_person_id=uid, now_iso=now_iso,
                                        older_than_days=days)
        n = len(rows)
        if n < bar:
            return None
        return {
            "n": n,
            "threshold_days": days,
            "n_review_total": len(tier),
            "line": (f"{n} unconfirmed capture{'' if n == 1 else 's'} "
                     f"nobody has answered in {days}+ days — say "
                     f"`review amnesty` and the whole lapsed pile goes in one "
                     f"confirm, reversibly."),
        }
    except Exception:
        return None


# The scheduled drain's own receipt id + type. Registered in
# `receipts.RECEIPT_TYPES` / `CANONICAL_TASK_IDS`; read by
# `maintenance_dispatcher.due_jobs` (dueness) and
# `task_watchdog.check_maintenance_jobs` (lateness, machine-local).
REVIEW_EXPIRY_JOB_ID = "review-expiry"
REVIEW_EXPIRY_RECEIPT_TYPE = "pack_run"


def run_review_expiry_job(workspace_root, *, apply: bool = False,
                          now_iso=None, fired_via: str = "scheduled",
                          batch_id=None) -> dict:
    """REVSCHED1 §3-2 — the scheduled drain, as a maintenance JOB.

    §0-2 RULED: auto-run. Plan internally, apply when the plan is
    non-empty, leave ONE receipt either way, and let the standing `undo` be
    the safety. A recurring confirm nobody answers recreates the exact
    pathology the measurement above records (3 of 224 answered), so the
    confirm is not the design here — reversibility is.

    UNCONFEXP1 (M's 2026-08-30 ruling — see UNCONFIRMED_NAG_DAYS): the drain
    now fires DAILY at the day's first maintenance slot with a 2-day default
    window, so an unconfirmed extraction nags for its window and then lapses
    the next morning — it no longer waits for a Sunday. The cadence lives on
    the job's `nominal_cron` in `maintenance_dispatcher.MAINTENANCE_JOBS`;
    nothing about the registered task or its prompt changed.

    WHY A JOB AND NOT A TASK, and this is the load-bearing part:

      * registration stays the writer of record. The `maintenance` task is
        ALREADY registered and already past Cowork's one-time Run-Now
        permission gate on every machine, so this adds ZERO scheduled tasks
        anywhere and needs no re-registration, no new prose registration
        block, and no client action.
      * it cannot inherit a retired task's disabled state. The 2026-08-19
        end-of-day incident came from `load_schedule_config`'s renamed-
        predecessor override inheritance, which only ever reads
        `DEFAULT_SCHEDULES` keys. A job id is not one: there is no
        DEFAULT_SCHEDULES row, no `RETIRED_TASKS` row, no `SUPERSEDED_BY`
        entry, and `renamed_predecessors()` returns nothing for it — so the
        carry-over seam is structurally out of reach rather than guarded
        against. Pinned by the suite.

    NEVER TOUCHES THE CONFIRMED TIER. It goes through `apply_review_expiry`,
    whose `_review_decisions` membership fence derives the live
    `pending_review` id set from the substrate and DROPS anything else. This
    function adds no second write path and no second derivation.

    `apply=False` is the dry run (plan + receipt-shaped return, no receipt
    written, nothing closed). The registered prompt passes `--apply`, exactly
    as `identity-reconcile` and `lifecycle` do, and the flag mattering is the
    point: a dry run that wrote a receipt would go permanently un-due.

    Returns `{ran, applied, n_planned, n_applied, n_shielded_by_reopen,
    n_held_back, threshold_days, batch_id, refused, reason, receipt_line,
    summary}`.
    """
    from primary_user import resolve_primary_user

    # REVIEW N-4 — VALIDATE THE RECEIPT VOCABULARY BEFORE ANY WRITE.
    #
    # `log_receipt` validates `fired_via` at write time and RAISES on an
    # unknown value, and the receipt call sits AFTER the closes and inside a
    # swallow-and-log guard (correct on its own terms: once rows are closed, a
    # receipt failure must not lose that fact). Composed, the two produced the
    # worst possible outcome — an unrecognised value (`"Run Now"`, say: not one
    # of the two aliases the normalizer knows) closed the whole lapsed pile,
    # the receipt raised, the guard printed to stderr, and the job stayed
    # PERMANENTLY DUE. Every subsequent fire re-derived and found nothing,
    # forever, with no receipt to explain why.
    #
    # So the check moves to the FRONT, where a refusal still means "nothing
    # happened": no closes, no receipt, job stays due, and the next fire with a
    # correct value works normally. Same posture as the other four refusals —
    # returned in the result, never raised.
    via = _normalize_fired_via_or_none(fired_via)
    if via is None:
        return {"ran": False, "applied": False,
                "refused": "unknown_fired_via",
                "reason": (f"{fired_via!r} is not a fire provenance I can "
                           f"record, so I did not touch the unconfirmed pile. "
                           f"Nothing was changed."),
                "n_planned": 0, "n_applied": 0, "n_shielded_by_reopen": 0,
                "n_held_back": 0, "threshold_days": None, "batch_id": None,
                "summary": "", "receipt_line": ""}
    fired_via = via

    uid = resolve_primary_user(workspace_root)
    if not uid:
        out = {"ran": False, "applied": False, "refused":
               "primary_user_unresolved",
               "reason": ("I could not work out whose workspace this is, so "
                          "the unconfirmed pile was left alone."),
               "n_planned": 0, "n_applied": 0, "n_shielded_by_reopen": 0,
               "n_held_back": 0, "threshold_days": None, "batch_id": None,
               "summary": "", "receipt_line": ""}
        if apply:
            _log_review_expiry_receipt(workspace_root, out,
                                       fired_via=fired_via)
        return out

    if not apply:
        plan = review_expiry_plan(workspace_root, user_person_id=uid,
                                  now_iso=now_iso)
        return {
            "ran": False, "applied": False,
            "refused": plan.get("refused"), "reason": plan.get("reason"),
            "n_planned": int(plan.get("n") or 0), "n_applied": 0,
            "n_shielded_by_reopen": int(plan.get("n_shielded_by_reopen") or 0),
            "n_held_back": 0,
            "threshold_days": plan.get("threshold_days"), "batch_id": None,
            "summary": plan.get("confirm") or plan.get("reason") or "",
            "receipt_line": "",
        }

    out = dict(apply_review_expiry(workspace_root, user_person_id=uid,
                                   batch_id=batch_id, now_iso=now_iso))
    out["receipt_line"] = _review_expiry_receipt_line(out)
    _log_review_expiry_receipt(workspace_root, out, fired_via=fired_via)
    return out


def _normalize_fired_via_or_none(value) -> Optional[str]:
    """The canonical `fired_via`, or None when `log_receipt` would reject it.

    Asks `receipts` both questions rather than re-stating either: the
    normalizer folds the known aliases (`run-now` -> `manual` and friends) and
    `FIRED_VIA` is the accepted set. A second copy of that vocabulary here
    would diverge the day a value is added, and the whole point of the check is
    to agree EXACTLY with the writer that would otherwise raise.

    Unreadable `receipts` module -> None, i.e. refuse. A fire that cannot even
    establish whether its receipt would be writable must not close rows.
    """
    try:
        from receipts import FIRED_VIA, normalize_fired_via
    except Exception:
        return None
    canonical = normalize_fired_via(value)
    return canonical if canonical in FIRED_VIA else None


def _review_expiry_receipt_line(out) -> str:
    """The ONE line the next staff meeting / end-of-day reads out (§0-2).

    Empty string when nothing was applied: a silent no-op is the contract, and
    a receipt line saying "cleared 0" is a line the CEO has to read to learn
    nothing happened.

    Every number in it comes off the apply's own return — `n_applied` from the
    writer, `n_held_back` from `_drift_causes`. It never offers `undo` on an
    empty batch (the same second-small-lie `_review_summary` avoids).
    """
    n = int((out or {}).get("n_applied") or 0)
    if not n:
        return ""
    days = (out or {}).get("threshold_days")
    line = (f"{n} unconfirmed capture{'' if n == 1 else 's'} lapsed after "
            f"{days} quiet days — say `undo` to put them back, `needs your "
            f"call` to see what remains.")
    held = int((out or {}).get("n_held_back") or 0)
    if held:
        line += (f" {held} stayed put: {'it owns' if held == 1 else 'they own'}"
                 f" smaller pieces that are still open.")
    return line


def _log_review_expiry_receipt(workspace_root, out, *, fired_via) -> None:
    """ONE receipt per fire, empty plan included (§3-2's silent no-op).

    The receipt is the job's dueness signal, so it is written on a no-op too —
    a job that only receipts when it finds work is a job that re-derives the
    whole pile at every one of the task's three daily slots forever.

    Counts come from the apply's return, never from the plan: §3-4 — the
    receipt must never assert a cause the writer did not return. `drift_causes`
    rides in whole so a reader can see the attribution rather than a total.
    """
    try:
        from receipts import log_receipt
        causes = (out or {}).get("drift_causes") or {}
        log_receipt(
            workspace_root, REVIEW_EXPIRY_JOB_ID,
            receipt_type=REVIEW_EXPIRY_RECEIPT_TYPE,
            fired_via=fired_via,
            surfaced=int((out or {}).get("n_applied") or 0),
            extra_data={
                "n_planned": int((out or {}).get("n_planned") or 0),
                "n_applied": int((out or {}).get("n_applied") or 0),
                "n_shielded_by_reopen":
                    int((out or {}).get("n_shielded_by_reopen") or 0),
                "n_held_back": int((out or {}).get("n_held_back") or 0),
                "n_answered": int(causes.get("answered") or 0),
                "n_refused": int(causes.get("refused") or 0),
                "threshold_days": (out or {}).get("threshold_days"),
                "batch_id": (out or {}).get("batch_id"),
                "refused": (out or {}).get("refused"),
                "receipt_line": (out or {}).get("receipt_line") or "",
            },
        )
    except Exception as exc:  # loud, never fatal — the closes already landed
        print(f"[backlog-sweep] review-expiry receipt FAILED: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)



# ---------------------------------------------------------------------------
# SWEEPSCHED1 — the CONFIRMED tier ages out on a SCHEDULE, not on a phrase
# ---------------------------------------------------------------------------
#
# WHAT WAS MISSING (the 2026-08-22 funnel census). The amnesty above exists,
# works, and is reversible — and it is wired to nothing. It runs only when
# somebody types `commitment amnesty`, which is the same shape of problem the
# review tier had until REVSCHED1 gave it a weekly job: a drain nobody
# remembers is a drain that never runs, and the confirmed pile is then the one
# lane that only ever grows.
#
# WHY IT IS A JOB AND NOT A TASK — the whole of `run_review_expiry_job`'s
# reasoning applies here unchanged and is not restated: it rides the already
# authorized `maintenance` task, so it registers zero scheduled tasks on any
# machine, and a job id is not a `DEFAULT_SCHEDULES` key, so it cannot inherit
# a retired predecessor's `enabled: false` through the carry-over seam that
# silently disabled the end-of-day task on 2026-08-19.
#
# WHERE IT DIFFERS FROM THE REVIEW DRAIN, and this is the only difference:
# CONFIRM-FIRST. The review tier drains guesses nobody ever agreed to, so a
# weekly confirm nobody answers was the wrong safety there and reversibility
# was the right one. This tier closes work somebody DID agree to, and nobody
# has ever watched this job act. So the first `AGE_OUT_CONFIRM_FIRST_RUNS`
# fires SHOW THEIR HAND — they compute the same plan, close nothing, and write
# the offer as their receipt line. From the fire after that it applies
# unattended, reversibly, exactly as the review drain does.
#
# THE COUNTER LIVES ON THE RECEIPTS AND NOWHERE ELSE (DD-2). A config flag
# would be a flag nobody flips, i.e. a job that never applies; a counter in
# config would be a second piece of state that can disagree with the ledger.
# The job's own `pack_run` receipts already record every fire it has made, so
# it counts itself — the same "the receipt is the signal" doctrine the
# dispatcher's dueness rule already runs on.

# The job's receipt id + type. Registered in `receipts.CANONICAL_TASK_IDS` /
# `receipts.RECEIPT_TYPES`; read by `maintenance_dispatcher.dispatch_plan`
# (dueness) and `task_watchdog.check_maintenance_jobs` (lateness,
# machine-local).
AGE_OUT_JOB_ID = "age-out"
AGE_OUT_RECEIPT_TYPE = "pack_run"

# How many fires propose before any fire applies (D2, RULED). Three is not a
# rounded-up two: it is the number of Sundays a CEO who ignores one and skims
# the next still gets a third look at before anything closes on its own.
AGE_OUT_CONFIRM_FIRST_RUNS = 3

# The two modes a fire can record. They are the ONLY values the counter counts,
# which is what keeps a refusal — a fire that never got as far as a plan — from
# burning one of the three looks the user is owed.
AGE_OUT_MODE_PROPOSED = "proposed"
AGE_OUT_MODE_APPLIED = "applied"


def _age_out_prior_runs(workspace_root) -> int:
    """How many times this job has already proposed or applied.

    Counts THIS JOB'S OWN receipts and nothing else — never config, never a
    stored counter. `iter_receipts` is shard-transparent and parses every
    legacy shape, so the count survives a substrate that has been sharded or
    migrated under it.

    A receipt with no `mode` does not count. Three shapes arrive that way and
    all must be excluded for the same reason: a `primary_user_unresolved`
    refusal (the fire never reached a plan), a REFUSED plan
    (`threshold_below_floor` — the plan came back with nothing to offer and an
    empty line), and any receipt written under this id by a future sibling.
    None of them showed the user anything, so none may consume one of the
    three looks.

    An unreadable substrate returns 0, which reads as "still on probation" —
    the safe direction. The failure in the other direction is a job that
    applies unattended because it could not read its own history, which is
    exactly what confirm-first exists to prevent.
    """
    try:
        from receipts import iter_receipts

        seen = 0
        for r in iter_receipts(workspace_root, task_ids=[AGE_OUT_JOB_ID]):
            raw = r.get("raw") if isinstance(r, dict) else None
            data = raw.get("data") if isinstance(raw, dict) else None
            mode = data.get("mode") if isinstance(data, dict) else None
            if mode in (AGE_OUT_MODE_PROPOSED, AGE_OUT_MODE_APPLIED):
                seen += 1
        return seen
    except Exception:
        return 0


def _age_out_offer_line(n, threshold_days, n_prior) -> str:
    """The line a PROPOSING fire leaves — an offer, not a report (DD-2).

    Empty when the plan is empty, for the same reason
    `_review_expiry_receipt_line` is: a line saying "nothing to clear" is a
    line the CEO has to read in order to learn that nothing happened.

    The tail COUNTS DOWN rather than repeating "two more Sundays" at every
    offer. The spec's sentence is the FIRST offer's sentence; saying it again
    on the third would be a plausible-looking lie about how much warning is
    left, and this module refuses those elsewhere for the same reason
    (`_review_expiry_receipt_line` never offers `undo` on an empty batch).
    """
    n = int(n or 0)
    if not n:
        return ""
    remaining = AGE_OUT_CONFIRM_FIRST_RUNS - 1 - int(n_prior or 0)
    if remaining >= 2:
        tail = f"after {remaining} more Sundays"
    elif remaining == 1:
        tail = "after one more Sunday"
    else:
        tail = "from next Sunday"
    subject = "item has" if n == 1 else "items have"
    obj = "it" if n == 1 else "them"
    return (f"{n} agreed {subject} been silent {threshold_days}+ days. Say "
            f"`commitment amnesty` to let {obj} go in one reversible batch, or "
            f"nothing and I will start doing it on my own {tail}.")


def _age_out_receipt_line(out) -> str:
    """The ONE line an APPLYING fire leaves for the next day-close to read out.

    Same contract as `_review_expiry_receipt_line`, same empty-on-zero rule,
    and every number off the writer's own return rather than off the plan — a
    receipt must never assert a count the writer did not produce.
    """
    n = int((out or {}).get("n_applied") or 0)
    if not n:
        return ""
    days = (out or {}).get("threshold_days")
    them = "it" if n == 1 else "them"
    return (f"{n} silent agreed item{'' if n == 1 else 's'} aged out after "
            f"{days} quiet days — say `undo` to put {them} back, `my plate` "
            f"for what remains.")


def run_age_out_job(workspace_root, *, apply: bool = False, now_iso=None,
                    fired_via: str = "scheduled", batch_id=None) -> dict:
    """SWEEPSCHED1 DD-1/DD-2 — the confirmed-tier drain, as a maintenance JOB.

    THE ORDER OF OPERATIONS IS `run_review_expiry_job`'S, DELIBERATELY, and the
    reasoning behind each step is recorded there rather than restated here:

      1. validate `fired_via` FIRST. `log_receipt` raises on a value it does not
         know, and the receipt call sits after the closes inside a
         swallow-and-log guard — composed, the two once closed a whole pile and
         then lost the receipt to the exception, leaving the job permanently
         due. A refusal at the front means nothing happened at all;
      2. `resolve_primary_user`, never a guess. Unresolved refuses, and on an
         apply it STILL receipts, or the job is permanently due;
      3. `apply=False` is the dry run: plan only, no receipt. The flag
         mattering is what stops a flagless fire from silently satisfying the
         dispatcher's dueness rule forever;
      4. on an apply, the plan goes through `apply_amnesty`, which this build
         does not touch. The phrase path stays byte-identical; all this wrapper
         adds is the user resolution `apply_amnesty` has always demanded of its
         caller — the single thing that kept it from running unattended.

    AND ONE STEP THAT IS THIS JOB'S ALONE: between 3 and 4, the confirm-first
    gate. Under `AGE_OUT_CONFIRM_FIRST_RUNS` prior proposing/applying fires the
    job computes the plan, CLOSES NOTHING, and writes the offer as its receipt
    line. It is a real fire either way — it receipts, so its slot is served and
    it does not re-derive at every one of the task's three daily slots.

    It reaches the CONFIRMED pile and nothing else, and adds no second
    derivation to make that true: `amnesty_plan` -> `_amnesty_decisions` ->
    `apply_decisions` is the one path, `split_pending_review` inside the plan is
    its fence, and an unconfirmed extraction never enters.

    Returns `{ran, applied, mode, n_prior_runs, n_planned, n_applied,
    n_drifted, threshold_days, batch_id, refused, reason, preview,
    receipt_line, summary}`.
    """
    from primary_user import resolve_primary_user

    def _blank(**over) -> dict:
        base = {"ran": False, "applied": False, "mode": None,
                "n_prior_runs": 0, "n_planned": 0, "n_applied": 0,
                "n_drifted": 0, "threshold_days": None, "batch_id": None,
                "refused": None, "reason": "", "preview": "",
                "receipt_line": "", "summary": ""}
        base.update(over)
        return base

    via = _normalize_fired_via_or_none(fired_via)
    if via is None:
        return _blank(
            refused="unknown_fired_via",
            reason=(f"{fired_via!r} is not a fire provenance I can record, so "
                    f"I did not touch the agreed pile. Nothing was changed."))
    fired_via = via

    uid = resolve_primary_user(workspace_root)
    if not uid:
        out = _blank(
            refused="primary_user_unresolved",
            reason=("I could not work out whose workspace this is, so the "
                    "agreed pile was left alone."))
        if apply:
            _log_age_out_receipt(workspace_root, out, fired_via=fired_via)
        return out

    if not apply:
        plan = amnesty_plan(workspace_root, now_iso=now_iso)
        return _blank(
            n_prior_runs=_age_out_prior_runs(workspace_root),
            n_planned=int(plan.get("n") or 0),
            threshold_days=plan.get("threshold_days"),
            refused=plan.get("refused"),
            reason=plan.get("reason") or "",
            preview=plan.get("preview") or "",
            summary=plan.get("confirm") or plan.get("reason") or "")

    n_prior = _age_out_prior_runs(workspace_root)
    if n_prior < AGE_OUT_CONFIRM_FIRST_RUNS:
        plan = amnesty_plan(workspace_root, now_iso=now_iso)
        n = int(plan.get("n") or 0)
        line = _age_out_offer_line(n, plan.get("threshold_days"), n_prior)
        out = _blank(
            ran=bool(n),
            # A REFUSED plan put NOTHING in front of anybody, so it records no
            # mode and burns none of the three looks — the same shape the
            # unresolved-user refusal above already uses, and the same reason
            # `_age_out_prior_runs` gives for excluding it. `amnesty_plan`
            # refuses exactly one way here (`threshold_below_floor`, a
            # workspace configured under the seven-day floor), and a workspace
            # can sit in that state for weeks; without this the three Sundays
            # of warning are spent in silence and the fourth closes the pile
            # having never once shown its hand. An EMPTY plan is NOT a refusal
            # and still counts — there was nothing to offer, and probation that
            # can never end is a job that can never act.
            mode=None if plan.get("refused") else AGE_OUT_MODE_PROPOSED,
            n_prior_runs=n_prior,
            n_planned=n,
            threshold_days=plan.get("threshold_days"),
            refused=plan.get("refused"),
            reason=plan.get("reason") or "",
            preview=plan.get("preview") or "",
            receipt_line=line,
            summary=line or plan.get("reason") or "")
        _log_age_out_receipt(workspace_root, out, fired_via=fired_via)
        return out

    out = dict(apply_amnesty(workspace_root, user_person_id=uid,
                             batch_id=batch_id, now_iso=now_iso))
    out["mode"] = AGE_OUT_MODE_APPLIED
    out["n_prior_runs"] = n_prior
    out["preview"] = out.get("preview") or ""
    out["receipt_line"] = _age_out_receipt_line(out)
    _log_age_out_receipt(workspace_root, out, fired_via=fired_via)
    return out


def _log_age_out_receipt(workspace_root, out, *, fired_via) -> None:
    """ONE receipt per fire — proposed, applied, empty and refused alike.

    The receipt is the job's dueness signal AND the confirm-first counter, so
    it is written on a no-op too. A job that only receipts when it finds work
    re-derives the whole pile at every one of the task's three daily slots
    forever; a job that only receipted when it ACTED would additionally never
    leave probation, because the counter it reads is this very ledger.

    `surfaced` is what the fire PUT IN FRONT OF THE USER: what closed on an
    applying fire, what was offered on a proposing one. A proposing fire
    reporting 0 there while its line named 26 items would put the usage report
    at odds with the sentence the CEO actually read.
    """
    try:
        from receipts import log_receipt
        mode = (out or {}).get("mode")
        n_planned = int((out or {}).get("n_planned") or 0)
        n_applied = int((out or {}).get("n_applied") or 0)
        surfaced = n_planned if mode == AGE_OUT_MODE_PROPOSED else n_applied
        log_receipt(
            workspace_root, AGE_OUT_JOB_ID,
            receipt_type=AGE_OUT_RECEIPT_TYPE,
            fired_via=fired_via,
            surfaced=surfaced,
            extra_data={
                "mode": mode,
                "n_prior_runs": int((out or {}).get("n_prior_runs") or 0),
                "n_planned": n_planned,
                "n_applied": n_applied,
                "n_drifted": int((out or {}).get("n_drifted") or 0),
                "threshold_days": (out or {}).get("threshold_days"),
                "batch_id": (out or {}).get("batch_id"),
                "refused": (out or {}).get("refused"),
                "preview": (out or {}).get("preview") or "",
                "receipt_line": (out or {}).get("receipt_line") or "",
            },
        )
    except Exception as exc:  # loud, never fatal — the closes already landed
        print(f"[backlog-sweep] age-out receipt FAILED: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)

__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "DEFAULT_AGE_OUT_DAYS",
    "DEFAULT_ITEM_CAP",
    "DEFAULT_DUP_WINDOW_DAYS",
    "AUTO_CLOSE_EVIDENCE_BASES",
    "AGE_OUT_RESOLUTION",
    "AGE_OUT_REASON",
    "AMNESTY_SOURCE_SKILL",
    "AMNESTY_BUCKET",
    "AMNESTY_ACTION",
    "AMNESTY_MIN_DAYS",
    "AMNESTY_OFFER_AT",
    "AMNESTY_PREVIEW_ROWS",
    "ACCEPT_SOURCE_KEY",
    "ACCEPT_BUCKET",
    "ACCEPT_ACTION",
    "REVIEW_SOURCE_SKILL",
    "REVIEW_TIER_RESOLUTION",
    "UNCONFIRMED_NAG_DAYS",
    "REVIEW_EXPIRY_DAYS",
    "REVIEW_EXPIRY_MIN_DAYS",
    "REVIEW_PREVIEW_ROWS",
    "REVIEW_OFFER_AT",
    "REVIEW_EXPIRY_JOB_ID",
    "REVIEW_EXPIRY_RECEIPT_TYPE",
    "REOPEN_MOVEMENT_TYPE",
    "REVIEW_EXPIRY_BUCKET",
    "REVIEW_EXPIRY_ACTION",
    "REVIEW_EXPIRY_REASON",
    "INGEST_KILL_BUCKET",
    "INGEST_KILL_ACTION",
    "INGEST_KILL_REASON",
    "REVIEW_TIER_BUCKETS",
    "review_tier",
    "review_expiry_candidates",
    "ingest_kill_candidates",
    "review_expiry_plan",
    "apply_review_expiry",
    "ingest_kill_plan",
    "apply_ingest_kill",
    "AUDIT_EVENT_TYPE",
    "BATCH_PREFIX",
    "BATCH_SALT_BYTES",
    "CLOSE_CHANGE_CLASS",
    "MERGE_CHANGE_CLASS",
    "closes_on_evidence",
    "window_start",
    "window_intent",
    "structured_window_params",
    "messages_in_window",
    "mail_reachable",
    "coverage_block",
    "last_activity_map",
    "age_out_candidates",
    "duplicate_groups",
    "scan",
    "apply_decisions",
    "amnesty_plan",
    "apply_amnesty",
    "accept_handled_decisions",
    "digest_view",
    "summarize",
    "last_scan",
    "validate_sweep_ran",
    "review_offer",
    "run_review_expiry_job",
    "AGE_OUT_JOB_ID",
    "AGE_OUT_RECEIPT_TYPE",
    "AGE_OUT_CONFIRM_FIRST_RUNS",
    "AGE_OUT_MODE_PROPOSED",
    "AGE_OUT_MODE_APPLIED",
    "run_age_out_job",
]


def main(argv: Optional[list] = None) -> int:
    """CLI for the scheduled legs of this module (REVSCHED1 §3-2, SWEEPSCHED1).

    `python3 commitment_backlog_sweep.py review-expiry --workspace <root>
    [--apply]` — the unconfirmed pile.
    `python3 commitment_backlog_sweep.py age-out --workspace <root> [--apply]`
    — the confirmed pile.

    Without `--apply` either leg plans and writes nothing — including NO
    receipt, so a dry run leaves the job due. Same contract as
    `lifecycle_pass.py` and `identity_reconcile.py`, deliberately: the flag
    mattering is what stops a dry run from silently satisfying the dispatcher's
    dueness rule forever.

    The two legs are separate subcommands and never one with a switch: they
    close different piles under different stamps at different bars, and a typo
    in a shell string that silently selected the other pile is exactly the
    class of accident a scheduled command line must not be able to have.
    """
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(
        description="Command Room commitment-backlog-sweep scheduled legs")
    sub = parser.add_subparsers(dest="leg", required=True)

    def _leg(name, help_text):
        pr = sub.add_parser(name, help=help_text)
        pr.add_argument("--workspace", required=True,
                        help="absolute path to the workspace root")
        pr.add_argument("--apply", action="store_true",
                        help="execute the plan (without it: dry run, no writes)")
        pr.add_argument("--now", default=None,
                        help="frozen ISO instant (testing/simulation)")
        pr.add_argument("--fired-via", default="scheduled",
                        help="scheduled | manual (receipt provenance)")
        return pr

    _leg(REVIEW_EXPIRY_JOB_ID, "the weekly unconfirmed-pile drain")
    _leg(AGE_OUT_JOB_ID, "the weekly confirmed-pile drain (confirm-first)")

    args = parser.parse_args(argv)
    runner = (run_age_out_job if args.leg == AGE_OUT_JOB_ID
              else run_review_expiry_job)
    result = runner(args.workspace, apply=args.apply, now_iso=args.now,
                    fired_via=args.fired_via)
    print(_json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
