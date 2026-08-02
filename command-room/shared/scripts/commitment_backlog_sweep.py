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


SOURCE_SKILL = "commitment-backlog-sweep"

# The historical window, in days. Wide by design — the whole point is mail the
# forward-only rails will never look at again. Configurable per run.
DEFAULT_WINDOW_DAYS = 180

# "No evidence AND no activity for N days" → an age-out candidate. Read from
# skill_config (`load_skill_config(ws, "commitment-backlog-sweep")["age_out_days"]`)
# and overridable in the command ("sweep my backlog, age out at 90 days").
DEFAULT_AGE_OUT_DAYS = 45

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

def last_activity_map(events_path) -> dict:
    """{commitment id: UTC-aware datetime of its newest movement}.

    Derived through `commitment_activity.derive_commitment_movement` — THE
    movement baseline every other surface passes, never a second derivation. Its
    map is seeded with each commitment's own capture ts, so an item that has never
    moved has its capture time as its last activity, which is exactly the age-out
    question.
    """
    try:
        from commitment_activity import derive_commitment_movement
        return {cid: mv.ts for cid, mv
                in derive_commitment_movement(events_path).items()}
    except Exception:
        return {}


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
                name_index=name_index, now_dt=now, window_days=window)
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
            "workspace.user_person_id in entities.json (Bug #102)."
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
                extra=None) -> list:
    """Close a list of rows through `close_commitments` — THE single closure path.

    Every closure carries its evidence and the two undo stamps, so `undo` lists
    and reverses a sweep run through the already-registered `commitment_close`
    reverser. No new reverser, no new batch kind, no hand-rolled append.
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
        })
    return [dict(r, commitment_id=str(r.get("commitment_id")))
            for r in close_commitments(workspace_root, closures,
                                       source_skill=source_skill)]


def _write_audit(workspace_root, events_path, receipt, *, source_skill) -> None:
    """ONE `backlog_sweep` audit event per run — dry-run included.

    Written through the same `next_seq` + `atomic_append_jsonl` pair both mail
    rails' audits use. The slim lists carry ids and titles only; the digest is
    rendered from the return value, not from this row.
    """
    from atomic_write import atomic_append_jsonl as _append
    from cru_match import _now_iso as _audit_ts
    from next_seq import next_seq as _next_seq

    def _slim(items, key="commitment_id"):
        return [{key: i.get(key), "title": (i.get("title") or "")[:120]}
                for i in items]

    event = {
        "seq": _next_seq(str(events_path)),
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
        from receipts import _machine_name
        machine = _machine_name()
        if machine:
            event["data"]["machine"] = machine
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


def digest_view(receipt, *, page: Optional[int] = None) -> dict:
    """The data view for `widget_transport.render_and_persist`.

    Four sections in the spec's order, plus the coverage block. Every action verb
    is in `chat_output_renderer.CANONICAL_ACTIONS`; the auto-closed section
    carries NO actions at all — those already happened, and the honest affordance
    for them is the batch id plus one word (`undo`), not a button that re-decides
    a decision already made.
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


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "DEFAULT_AGE_OUT_DAYS",
    "DEFAULT_ITEM_CAP",
    "DEFAULT_DUP_WINDOW_DAYS",
    "AUTO_CLOSE_EVIDENCE_BASES",
    "AGE_OUT_RESOLUTION",
    "AGE_OUT_REASON",
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
    "digest_view",
    "summarize",
    "last_scan",
    "validate_sweep_ran",
]
