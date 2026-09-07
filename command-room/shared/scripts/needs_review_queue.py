#!/usr/bin/env python3
"""The needs-your-call queue — one place for UNCONFIRMED EXTRACTIONS.

WHY THIS EXISTS
A commitment carrying `data.pending_review` is a guess: the extractor read a
transcript or an email, thought it saw a promise, and flagged that it was not
sure. `cru_match._is_pending_review` has always barred those from auto-close
and chase — but they still counted in the headline open total and still
rendered as rows wherever open commitments render. So a bad week of
extraction inflated the number the CEO reads as "how many promises am I
carrying", and the only way to fix a wrong guess was to hunt the row down
inside a triage list of everything.

INTAKE splits the two ideas apart. An unconfirmed extraction is a QUEUE
MEMBER, not an open commitment: it counts in exactly one number
(`count_commitments(...)["headline"]["unconfirmed"]`, a pointer) and lives in
exactly one list — this one. Confirming one makes it an ordinary commitment
with no other change. Dropping one closes it with `resolution="dropped"`.
Neither path ever rewrites or deletes an event: the substrate is append-only,
and the original capture stays in history exactly as it was written.

OBSERVED1 (2026-08-24) — the queue also reads the OBSERVED TIER. A
`commitment_observed` row is the relevance gate's set-aside: kept on file,
feeding prep, deliberately not open. For six weeks those rows were readable
(prep cited them as live work) while NO confirm queue listed them — no
confirm path, no drop path, a row influencing output that no human could
answer. The view now carries live observed rows (unexpired, unpromoted) as a
separate trailing section (`observed_groups` / `n_observed`, drop-empty), and
the two verbs below dispatch an `obs_` id through the tier's one defined
transition: `capture_gate.promote_observed` first (a REAL commitment with
`promoted_from` + `pending_review`), then the standard writer — confirm
clears the review flags, drop closes it dropped. The observed WRITE path is
untouched, and `total` keeps its shipped meaning (the count
`headline.unconfirmed` points at); `selection_numbers(view, spec)` is the
one selection parse — `all` answers the queue's own rows only, explicit
numbers and ranges reach both tiers (`addressable_total(view)` is the full
bound it validates against).

WHAT THIS MODULE IS
  build_queue_view  — PURE READ. The queue, grouped by counterparty, each row
                      numbered so the user can answer in ranges.
  render_text       — that view as the scannable text the skill pastes back.
  confirm_items     — clear the review flags (commitment_state.clear_review_flags).
  done_items        — DONE1 `already done`: confirm THEN close with
                      resolution="done", on the user's own attestation. Rides
                      the same bulk-accept fence plus a stricter gesture bar —
                      every id must be individually named.
  drop_items        — close with resolution="dropped" (commitment_state.close_commitment).
  undo_confirm_items / undo_done_items
                    — UNCONFIRM1: put a confirmed (or Done'd) item back in the
                      queue carrying its ORIGINAL reason, through
                      commitment_state.restore_review_flags. Additive; the
                      confirm and the closure stay in history.
  parse_selection   — "1,3,5-9" / "all" -> display numbers.

CLI (one command, mirroring surface_drivers' dispatch):

    python3 shared/scripts/needs_review_queue.py view <WORKSPACE> [--now ISO]

stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# TZDATE3 — canonical date localizer (tz.py, hoisted TZDATE2). Guarded: a
# stripped install missing tz.py keeps the pre-TZDATE3 UTC-slice behavior
# exactly (matches the render_*.py precedent).
try:
    from tz import localize_date as _localize_date  # noqa: E402
except ImportError:
    def _localize_date(ts: str | None, workspace_path: str | None = None) -> str:
        return ts[:10] if isinstance(ts, str) and ts else ""

NO_COUNTERPARTY = "(no counterparty)"
SOURCE_SKILL = "needs-your-call"
DROP_EVIDENCE = "dropped from needs-your-call queue"
# `not mine` reuses the drop write with the reason the commitment-triage
# dispatch already uses for the same verb — one vocabulary, two surfaces.
NOT_MINE_EVIDENCE = "not the user's item (cross-attendee capture)"

# CAPTUREFLOW §B — the queue's second grouping. Meeting captures are what
# fills this queue, and one call's worth of them is ONE decision the user can
# make in one pass; grouped by counterparty they were scattered across the
# list. Counterparty grouping stays (it is what a chase-shaped answer wants);
# the meeting grouping is what the surface renders by default now.
GROUP_COUNTERPARTY = "counterparty"
GROUP_MEETING = "meeting"
GROUP_MODES = (GROUP_MEETING, GROUP_COUNTERPARTY)
NOT_FROM_A_MEETING = "(not from a meeting)"

# HELDREVIEW1 §DD-1 — the queue's ROW SCOPE. A second selector beside
# `group_by`, and deliberately the same shape: it decides WHICH rows are in
# the view and nothing else. The numbering contract, the fields on every row,
# the grouping and the render are identical either way.
#
#   "all"         every unconfirmed extraction — the shipped queue. DEFAULT,
#                 and byte-identical to before this parameter existed: the
#                 filter below is a no-op on this value and the returned dict
#                 gains no key. That is pinned, both ways, in
#                 `tests/run_heldreview1_test.py`.
#   "would_hold"  ONLY the rows `held_tier.is_floor_gated` calls true — the
#                 candidate list the held tier would route out of sight if the
#                 flip were on. It is a READING scope: the operator looks at
#                 what would have been hidden before deciding whether hiding
#                 it is acceptable. It resolves nothing and offers no verb.
#
# The scope reads `held_tier.is_floor_gated` rather than re-testing the marker
# here, so "what would be held" can never come to mean two different things on
# the routing side and the review side — the same single-definition rule the
# weakness vocabulary already follows through `watch_gate`.
SCOPE_ALL = "all"
SCOPE_WOULD_HOLD = "would_hold"
SCOPE_MODES = (SCOPE_ALL, SCOPE_WOULD_HOLD)

# The row verbs on the grouped surface (§B): one tap each. `not mine` is the
# W4b reassign-or-drop verb, not a third idea.
#
# DONE1 — `already done` sits beside `confirm` because it IS confirm-plus (the
# capture was real AND it was fulfilled); the two closures stay on the right.
# THIS LIST IS THE ONLY DEFINITION of what either queue surface renders: the
# on-demand widget (`build_queue_data_view`) and the staff-meeting fold
# (`staff_meeting_group_section`) both read it, so a verb can never appear on
# one surface and not the other. Its wire ids are validated against
# `verb_taxonomy` at render time by `chat_output_renderer`, so a verb added
# here with no taxonomy row fails the render rather than shipping a dead
# button.
QUEUE_ROW_ACTIONS = ["confirm", "already done", "drop", "not mine"]

# DONE1 — the wire id of the Done verb, named once so tests and dispatch prose
# can key on the constant rather than re-typing the token.
DONE_ACTION = "already done"

# DONE1 — the evidence a Done rests on, and the ONLY thing it may ever rest on.
# NOT a match, NOT a sent-mail hit, NOT a score: the user's own attestation,
# stamped with when and where they said it. Fabricating match-shaped evidence
# for a closure the system did not observe is the exact lie this verb exists to
# avoid — a Drop was already lying about WHAT happened; a fake evidence line
# would lie about HOW WE KNOW.
DONE_ATTESTATION = "you said at review it was already done"
DONE_CONFIRM_NOTE = ("confirmed from the needs-your-call queue — you said it "
                     "was already done")
# The machine-readable half of the same honesty, carried on the closure's
# extra_data. Nothing in this build consumes it; it exists so no future reader
# (value receipts, recaps, the backlog sweep) can mistake an attested
# completion for an evidence-backed one.
COMPLETION_BASIS_ATTESTATION = "user_attestation"

# UNCONFIRM1 — the note the un-confirm writers stamp, in the user's words.
UNCONFIRM_NOTE = "you un-confirmed this from the needs-your-call queue"
UNDO_DONE_REASON = "you undid an 'already done' — the item is open again"

# CAPTUREFLOW §C — the staff-meeting fold's volume guard. Whole groups only,
# oldest call first, and the section can never dominate the page: at most
# STAFF_GROUP_CAP calls and at most STAFF_ROW_CAP rows, whichever binds first.
# A single call carrying more than the row cap is still shown WHOLE (a split
# group is a worse lie than a long one) and is then the only group on the
# section. Oldest-first is the rotation rule: nothing can be suppressed
# forever, because answering the front of the queue is what advances it.
STAFF_SECTION_TITLE = "FROM YOUR MEETINGS"
STAFF_GROUP_CAP = 3
STAFF_ROW_CAP = 8

# BULKGUARD — the render marker + hold reason for rows whose capture carries
# nothing a person could weigh. A batch apply once closed six commitments in
# two seconds off proposals whose entire evidence was a title match — all six
# wrong. This queue's batch verbs are that accept-in-bulk path again, so the
# same class is gated here: a row with no evidence text, or whose evidence is
# a title match rather than source text, is WEAK — rendered as such, and never
# confirmed by `all`, a group phrase, or a range. Only its own number, typed
# alone, confirms it (see confirm_items / individually_named).
#
# WATCHGATE moved BOTH halves of that rule into `watch_gate`, unchanged in
# behavior: the weakness VOCABULARY (`commitment_weak_reason`) and the
# accept FENCE (`screen_bulk_accept`). This queue and the proposal queue now
# call the same two functions, so "weak" can never come to mean two different
# things on two surfaces — which, given they are the same incident class
# arriving by two roads, is the only version of this fence worth having.
from watch_gate import commitment_weak_reason as _watch_weak_reason
from watch_gate import screen_bulk_accept


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _age_days(ts: str, now_iso: str) -> int | None:
    from event_time import parse_ts

    a, b = parse_ts(ts), parse_ts(now_iso)
    if a is None or b is None:
        return None
    return max(0, int((b - a).total_seconds() // 86400))


def _people_by_id(ws: Path) -> dict:
    """id -> person record from entities.json. Defensive: a missing or corrupt
    file yields an empty map and every row falls back to its free-text
    counterparty name (never a crash, never a fabricated name)."""
    try:
        raw = (ws / "_hq" / "data" / "entities.json").read_text("utf-8")
        people = (json.loads(raw) or {}).get("people") or []
    except Exception:
        return {}
    out: dict = {}
    for p in people:
        if isinstance(p, dict) and p.get("id"):
            out[p["id"]] = p
    return out


def _counterparty_display(ev: dict, people_by_id: dict, ws: Path) -> str:
    """The group label for one row: the counterparty's display name.

    Resolution order — resolved id through the entity graph, then the
    capture's free-text name, then the honest placeholder. F-28: the roster
    readers are threaded with `workspace_root`, so one person written as BOTH
    an id and that person's name is ONE counterparty, not two groups.
    """
    from commitment_parties import (primary_counterparty_id,
                                    primary_counterparty_name)

    cp_id = primary_counterparty_id(ev)
    if cp_id:
        rec = people_by_id.get(cp_id) or {}
        name = (rec.get("name") or rec.get("canonical_name") or "").strip()
        if name:
            return name
        return cp_id
    name = primary_counterparty_name(ev, workspace_root=str(ws))
    if isinstance(name, str) and name.strip():
        return name.strip()
    return NO_COUNTERPARTY


def _commitment_id(ev: dict) -> str:
    from cru_match import _commitment_id as _cid

    return _cid(ev)


def _evidence_text(ev: dict) -> str:
    """The capture's own extraction evidence — the source-text quote the
    extractor saved when it guessed. Empty string when none was recorded."""
    val = (ev.get("data") or {}).get("evidence")
    return val.strip() if isinstance(val, str) else ""


def _weak_reason(ev: dict) -> str:
    """Why this row is too weak for a bulk confirm — or "" when it is not.

    WEAK means the user has nothing real to weigh: either the capture
    recorded no evidence at all (a bare extractor guess), or the recorded
    evidence is a title match — the commitment's own words echoed back, not
    source text (the exact evidence string behind the six-wrong-closes
    incident).

    One line, because the rule lives in `watch_gate` now and both accept
    surfaces read it from there."""
    return _watch_weak_reason(ev)


def _stamped_fields(ev: dict) -> dict:
    """The producer's own strength stamp off a capture event, or {}.

    RIDERS (c). One reader (`watch_gate.stamped_strength_fields`) shared with
    the proposal adapter, so the two proposal surfaces cannot grow two ideas
    of what a stamped row says. Defensive: any failure yields no stamp, which
    is the pre-rider render."""
    try:
        from watch_gate import stamped_strength_fields

        return stamped_strength_fields((ev or {}).get("data"))
    except Exception:
        return {}


def _review_reason(ws: Path, ev: dict, cache: dict) -> str:
    """The row's `review_reason` in the copy the user should read.

    Routed through the RRF1 render-time overlay so a clause that went stale
    ("X has no person record") does not tell the CEO to add a contact they
    already added. Defensive: any failure falls back to the stored text —
    the stored value is a gating input and is never rewritten either way."""
    reason = (ev.get("data") or {}).get("review_reason") or ""
    if not reason:
        return ""
    try:
        from surface_drivers import _display_review_reason

        return _display_review_reason(ws, reason, cache)
    except Exception:
        return reason


# ---------------------------------------------------------------------------
# Meeting grouping (CAPTUREFLOW §B) — which call did this row come from?
# ---------------------------------------------------------------------------

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Transcript backends whose ref prefix identifies a meeting even when no
# `meeting` event was ever written for it (a leg that wrote captures and died
# before its receipt). Kept in step with meeting_capture._norm_ref_keys.
_TRANSCRIPT_PREFIXES = ("granola", "fireflies", "otter", "zoom", "teams")


def _ref_keys(ref) -> set:
    """Normalized membership keys for a source ref — `granola:<id>` and the
    bare `<id>` both index the same meeting (the F-50 drift both spellings of
    which are live in real substrate)."""
    from meeting_capture import _norm_ref_keys

    return _norm_ref_keys(ref)


def _pretty_date(value, workspace_path=None) -> str:
    """"Aug 3" from an ISO ts. TZDATE3 — localizes via `tz.localize_date`
    first when `workspace_path` is passed, so an evening-local meeting
    doesn't read as tomorrow's UTC date; `workspace_path=None` (the
    default) keeps the pre-TZDATE3 raw-UTC-slice behavior."""
    raw = str(value or "").strip()
    s = _localize_date(raw, workspace_path)[:10] if workspace_path and raw else raw[:10]
    try:
        y, m, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
        return f"{_MONTHS[m - 1]} {d}"
    except (ValueError, IndexError):
        return ""


def _meeting_index(ws: Path) -> dict:
    """ref-key -> {"title", "date"} for every meeting on record.

    Reads through `events_io.iter_events` — the canonical shard-aware
    iterator — never a hand-rolled join (the id-scheme trap: meeting refs
    carry both the prefixed and bare spellings, and the reader has to match
    either). Defensive: any failure yields an empty index and every row falls
    back to its own `meeting_date`, so a broken log degrades to un-labelled
    groups rather than a crash."""
    index: dict = {}
    try:
        from events_io import iter_events
    except Exception:  # pragma: no cover
        return index
    try:
        for ev in iter_events(ws):
            if ev.get("type") not in ("meeting", "meeting_processed"):
                continue
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            keys = _ref_keys(d.get("source_ref")) | _ref_keys(d.get("meeting_id"))
            if not keys:
                continue
            title = str(d.get("title") or "").strip()
            date = _pretty_date(d.get("meeting_date") or ev.get("ts"), ws)
            for k in keys:
                slot = index.setdefault(k, {"title": "", "date": ""})
                # A `meeting` event carries the real title; `meeting_processed`
                # may not. First non-empty value wins for each field.
                if title and not slot["title"]:
                    slot["title"] = title
                if date and not slot["date"]:
                    slot["date"] = date
    except Exception:  # pragma: no cover
        return {}
    return index


def _meeting_group(ev: dict, index: dict, workspace_path=None) -> tuple:
    """(group_key, display_label, sort_date) for one queue row.

    A row is FROM A MEETING when its source ref resolves in the meeting index
    or carries a transcript backend's prefix. Everything else — sent mail,
    Slack, a session sweep — lands in one honest bucket that sorts last. Wire
    ids never render as row text (RV-5), so an unlabelled meeting reads as
    "a call on Jun 2", never as its ref."""
    from cru_match import _commitment_field

    ref = str(_commitment_field(ev, "source_ref") or "").strip()
    keys = _ref_keys(ref)
    hit = None
    for k in keys:
        if k in index:
            hit = index[k]
            break
    prefix = ref.partition(":")[0].lower() if ":" in ref else ""
    if hit is None and prefix not in _TRANSCRIPT_PREFIXES:
        return (NOT_FROM_A_MEETING, NOT_FROM_A_MEETING, "")

    row_date = _pretty_date(_commitment_field(ev, "meeting_date")
                            or ev.get("ts"), workspace_path)
    title = (hit or {}).get("title") or ""
    date = (hit or {}).get("date") or row_date
    key = sorted(keys)[0] if keys else NOT_FROM_A_MEETING
    if title and date:
        label = f"{title} — {date}"
    elif title:
        label = title
    elif date:
        label = f"a call on {date}"
    else:
        label = "an earlier call"
    return (key, label, date)


NOT_FROM_A_MEETING_PHRASE = "items not from a meeting"


def source_count_phrase(groups) -> str:
    """"46 calls", or "46 calls plus items not from a meeting" — THE sentence
    both surfaces count with (SPEC RIDERS1 item 4).

    The on-demand header and the staff-meeting fold's title were computed
    separately and disagreed: the header counted only the meeting groups ("46
    calls") while the fold counted every group including the not-from-a-meeting
    bucket ("47 calls"). Same queue, same page, two numbers — and the second one
    was also wrong on its own terms, because that bucket is not a call.

    So the arithmetic is stated ONCE. The full total is always honest: every
    group is accounted for, calls are counted as calls, and the bucket is NAMED
    rather than folded into a number it does not belong in. There is exactly one
    such bucket by construction, which is why it is named and not counted.
    """
    rows = list(groups or [])
    n_calls = sum(1 for g in rows
                  if (g or {}).get("group_key") not in (None,
                                                        NOT_FROM_A_MEETING))
    has_other = any((g or {}).get("group_key") == NOT_FROM_A_MEETING
                    for g in rows)
    noun = "call" if n_calls == 1 else "calls"
    phrase = f"{n_calls} {noun}"
    if has_other:
        phrase += f" plus {NOT_FROM_A_MEETING_PHRASE}"
    return phrase


# ---------------------------------------------------------------------------
# The view (pure read)
# ---------------------------------------------------------------------------

def build_queue_view(workspace_root, now_iso: str | None = None,
                     *, group_by: str = GROUP_COUNTERPARTY,
                     scope: str = SCOPE_ALL) -> dict:
    """The needs-your-call queue, grouped by counterparty. PURE READ.

    Returns:
      {
        "header": "Needs your call — N unconfirmed extractions, grouped by
                   counterparty",
        "total": N,
        "groups": [{"name": str, "count": int, "items": [row, ...]}, ...],
      }

    Each row: `display_n` (a stable 1..N index across the WHOLE list, so the
    user can say "confirm 1-12, drop 13" without counting inside groups),
    `commitment_id` (the canonical data.id verbatim — the identity contract),
    `title`, `age_days`, `review_reason`, `source_skill`, `due`, plus the
    BULKGUARD pair: `evidence` (the capture's source-text quote, "" when none
    was recorded) and `weak_reason` ("" for a row with real evidence; the
    one-line reason it cannot be confirmed in bulk otherwise). The view also
    carries `n_weak`, the count of weak rows, for the render footer.

    Groups are ordered oldest-item-first (the counterparty who has been
    waiting longest leads), with `(no counterparty)` always last — it is a
    bucket, not a person. Inside a group, rows are oldest first.

    `group_by` (CAPTUREFLOW §B) selects the grouping and nothing else — the
    rows, the numbering contract and every field on them are identical:

      "counterparty"  the shipped grouping (default here for back-compat with
                      every existing caller).
      "meeting"       one group per SOURCE CALL, oldest call first, with
                      `(not from a meeting)` last. This is what the surface
                      renders now: a call's worth of captures is one decision
                      the user makes in one pass. Each group also carries
                      `group_key` (the meeting's normalized ref) so a group
                      answer can be resolved without matching on display text.

    `scope` (HELDREVIEW1 DD-1) selects WHICH ROWS are in the view and nothing
    else — same numbering contract, same fields, same grouping, same render:

      "all"         every unconfirmed extraction. DEFAULT, and the output is
                    byte-identical to before this parameter existed — the
                    filter is a no-op and the returned dict gains no key.
      "would_hold"  only the below-floor captures (`held_tier.is_floor_gated`)
                    — what the held tier WOULD route out of sight. The header
                    says so, and says which weeks the rows span, because a
                    review of "what would be hidden" that does not say over
                    what period is a number without a denominator.
    """
    from cru_match import _commitment_field, load_needs_review

    if group_by not in GROUP_MODES:
        raise ValueError(
            f"group_by must be one of {list(GROUP_MODES)}; got {group_by!r}")
    if scope not in SCOPE_MODES:
        raise ValueError(
            f"scope must be one of {list(SCOPE_MODES)}; got {scope!r}")

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    items = load_needs_review(str(_events_path(ws)), workspace_root=str(ws))
    if scope == SCOPE_WOULD_HOLD:
        # ONE filter, at ONE place, keyed on the routing side's own predicate.
        from held_tier import is_floor_gated
        items = [ev for ev in items if is_floor_gated(ev)]
    people = _people_by_id(ws)
    rr_cache: dict = {}

    by_meeting = group_by == GROUP_MEETING
    index = _meeting_index(ws) if by_meeting else {}
    ordered = _bucket_and_order(items, by_meeting=by_meeting, people=people,
                                index=index, ws=ws, now_iso=now_iso)

    display_n = 0
    n_weak = 0
    groups: list[dict] = []
    for key, label, date, bucket_rows in ordered:
        rows = []
        for ev in bucket_rows:
            display_n += 1
            weak = _weak_reason(ev)
            if weak:
                n_weak += 1
            rows.append({
                "display_n": display_n,
                "commitment_id": _commitment_id(ev),
                "title": (_commitment_field(ev, "title")
                          or (ev.get("data") or {}).get("summary")
                          or "(untitled)"),
                "age_days": _age_days(ev.get("ts") or "", now_iso),
                "review_reason": _review_reason(ws, ev, rr_cache),
                "source_skill": ev.get("source_skill") or "",
                "due": _commitment_field(ev, "due") or None,
                "evidence": _evidence_text(ev),
                "weak_reason": weak,
            })
            # RIDERS (c) — the PRODUCER'S own strength stamp, carried onto the
            # row so the shared renderer below can SAY it. Only when the writer
            # set one: a row with no stamp gains no keys and renders exactly as
            # before. Deliberately NOT folded into `weak_reason` — that field
            # is what `confirm_items` screens a bulk answer with, and this
            # rider is a rendering change, not a screening one.
            rows[-1].update(_stamped_fields(ev))
        group = {"name": label, "count": len(rows), "items": rows}
        if by_meeting:
            group["group_key"] = key
            group["date"] = date
        groups.append(group)

    total = display_n
    noun = "extraction" if total == 1 else "extractions"

    # CLUSTER1 — render-level clustering, DEFAULT-ON (SPEC §0-1). One line
    # per real-world item: rows the clusterer joins (shared content tokens +
    # shared counterparty read off the roster + temporal adjacency, precision
    # over recall) render as ONE cluster line — survivor title + "+N folded"
    # — with the folded rows one tap away, read-only, numbers kept so a typed
    # answer still reaches them individually. A cluster is a DISPLAY fact:
    # nothing here writes, and the view keeps EVERY row (`ids_for_selection`,
    # `ids_for_group` and the numbering contract are untouched) — the RENDER
    # paths are what fold. Drop-empty: with no clusters the view gains no key
    # and is byte-identical to before this existed (golden-pinned). Both
    # scopes cluster — the `would_hold` reading chair shows the same one-line
    # shape, display-only by its own DD-4 fence (held_review renders it).
    clusters = []
    if total:
        from commitment_cluster import render_clusters
        clusters = render_clusters(items, workspace_root=str(ws),
                                   now_iso=now_iso)
    n_folded = sum(c["n_folded"] for c in clusters)
    info = total - n_folded
    info_noun = "item" if info == 1 else "items"

    if scope == SCOPE_WOULD_HOLD:
        header = would_hold_header(total, week_span_phrase(items, ws),
                                   n_lines=info if clusters else None)
    elif by_meeting:
        if clusters:
            # SPEC §0-4 — the headline count is the INFORMATION count
            # (clusters, not fragments); the true row count stays in the
            # same sentence, one level down.
            header = (f"Needs your call — {info} {info_noun} to answer "
                      f"({total} unconfirmed {noun}) "
                      f"from {source_count_phrase(groups)}")
        else:
            header = (f"Needs your call — {total} unconfirmed {noun} "
                      f"from {source_count_phrase(groups)}")
    else:
        if clusters:
            header = (f"Needs your call — {info} {info_noun} to answer "
                      f"({total} unconfirmed {noun}), "
                      f"grouped by counterparty")
        else:
            header = (f"Needs your call — {total} unconfirmed {noun}, "
                      f"grouped by counterparty")
    out = {
        "source_skill": SOURCE_SKILL,
        "group_by": group_by,
        "header": header,
        # REVSCHED1 §3-1 — the drain's offer line, or "". Deliberately its OWN
        # field rather than appended to `header`: the header is a counted
        # sentence about this queue and both surfaces that count with it
        # compare it (RIDERS1 item 4), so folding an offer into it would put a
        # second number in the sentence the two surfaces reconcile. Rendered
        # directly UNDER the header by `render_text` / carried onto the data
        # view, which is where the user reads it.
        "offer": _review_drain_offer(workspace_root, now_iso),
        "total": total,
        "n_weak": n_weak,
        "groups": groups,
    }
    # The scope MARKER is carried only by a non-default scope, on purpose: the
    # default view's key set is what every existing caller and every existing
    # surface test reads, and "the default is byte-identical to today" is an
    # acceptance criterion of this build, not a nicety. A reader that wants to
    # know the scope of a default view already knows it — it asked for it.
    if scope != SCOPE_ALL:
        out["scope"] = scope

    # CLUSTER1 — annotate the rows (additive keys only; absent entirely when
    # nothing clusters, which is the byte-identical contract).
    if clusters:
        _overlay_view_clusters(out, clusters)

    # OBSERVED1 — the set-aside tier, rendered as its OWN trailing section.
    # The tier was readable (prep cited its rows as live work) while no
    # confirm queue listed it, so a row could influence output forever
    # without a human ever being able to answer it. Live rows only
    # (unexpired, unpromoted — `capture_gate.live_observed`), numbered
    # CONTINUING after the queue so a typed answer addresses them, and
    # DELIBERATELY not folded into `total` / the groups: `total` is the
    # header's counted sentence ("N unconfirmed extractions") and the number
    # `headline.unconfirmed` points at — observed rows are a different tier
    # with their own count (`n_observed`), and inflating the reconciled
    # number would trade one coverage lie for another. Drop-empty: with no
    # live observed rows the returned dict gains no key and is byte-identical
    # to before this section existed. The `would_hold` scope never carries
    # it — that scope is a reading of the HELD-tier candidate list, and
    # mixing tiers into it would put a second population under its header's
    # denominator.
    if scope == SCOPE_ALL:
        observed_groups, n_observed = _observed_view_groups(
            ws, now_iso=now_iso, group_by=group_by, people=people,
            index=index, start_n=total)
        if n_observed:
            out["observed_groups"] = observed_groups
            out["n_observed"] = n_observed
    return out


def _bucket_and_order(events, *, by_meeting: bool, people: dict, index: dict,
                      ws: Path, now_iso: str) -> list:
    """CAPTUREFLOW §B's grouping and ordering, ONE copy (REVIEW S1) — the
    queue's own rows and the observed section both read it, so the two
    contiguously-numbered sections of one render cannot drift onto two
    ordering rules. Buckets by meeting or counterparty, orders buckets
    oldest-first with the catch-all bucket last (label tiebreak), orders rows
    inside a bucket oldest-first (unparseable ts last rather than pretending
    to be brand new). Returns `[(key, label, date, sorted_events), ...]`."""
    buckets: dict[str, list] = {}
    labels: dict[str, str] = {}
    dates: dict[str, str] = {}
    for ev in events:
        if by_meeting:
            key, label, date = _meeting_group(ev, index, ws)
            labels.setdefault(key, label)
            if date and not dates.get(key):
                dates[key] = date
        else:
            key = _counterparty_display(ev, people, ws)
            labels.setdefault(key, key)
        buckets.setdefault(key, []).append(ev)

    def _age_key(ev) -> tuple:
        age = _age_days(ev.get("ts") or "", now_iso)
        return (0 if age is not None else 1, -(age or 0), _commitment_id(ev))

    bucket_last = NOT_FROM_A_MEETING if by_meeting else NO_COUNTERPARTY
    ordered_keys = sorted(
        buckets,
        key=lambda k: (
            1 if k == bucket_last else 0,
            -max((_age_days(e.get("ts") or "", now_iso) or 0)
                 for e in buckets[k]),
            labels.get(k, k).lower(),
        ),
    )
    return [(k, labels.get(k, k), dates.get(k, ""),
             sorted(buckets[k], key=_age_key)) for k in ordered_keys]


def _overlay_view_clusters(out: dict, clusters: list) -> None:
    """CLUSTER1 — stamp the cluster DISPLAY facts onto a built view, in place.

    Additive only, and only when clusters exist: the SURVIVOR row gains
    `cluster` ({cluster_id, n_folded, folded_ids, folded: [{display_n,
    commitment_id, title, group} ...]}), each FOLDED row gains
    `folded_into: <survivor_id>`, and the view gains `n_clusters` /
    `n_folded` / `n_lines` (the information count — SPEC §0-4: the count the
    CEO sees is clusters, not fragments, with the true row count reachable).
    EVERY row stays in `groups` with its number: folding is a job for the
    render paths, so `ids_for_selection` / `ids_for_group` and every typed
    answer keep working on individual rows (SPEC §0-2 — answering rows
    individually leaves history untouched, and stays possible)."""
    from commitment_cluster import cluster_index

    by_survivor, by_folded = cluster_index(clusters)
    row_of: dict = {}
    group_of: dict = {}
    for group in out.get("groups") or []:
        for row in group.get("items") or []:
            row_of[row["commitment_id"]] = row
            group_of[row["commitment_id"]] = group.get("name") or ""
    for c in clusters:
        srow = row_of.get(c["survivor_id"])
        if srow is None:  # pragma: no cover — members come from these rows
            continue
        folded_meta = []
        for fid in c["folded_ids"]:
            frow = row_of.get(fid)
            if frow is None:  # pragma: no cover
                continue
            frow["folded_into"] = c["survivor_id"]
            folded_meta.append({
                "display_n": frow.get("display_n"),
                "commitment_id": fid,
                "title": frow.get("title") or "(untitled)",
                "group": group_of.get(fid, ""),
            })
        if not folded_meta:  # pragma: no cover
            continue
        srow["cluster"] = {
            "cluster_id": c["cluster_id"],
            "n_folded": len(folded_meta),
            "folded_ids": [f["commitment_id"] for f in folded_meta],
            "folded": folded_meta,
        }
    n_folded = sum(len(r.get("cluster", {}).get("folded_ids") or [])
                   for r in row_of.values() if r.get("cluster"))
    if not n_folded:  # pragma: no cover — defensive: nothing actually folded
        return
    out["n_clusters"] = sum(1 for r in row_of.values() if r.get("cluster"))
    out["n_folded"] = n_folded
    out["n_lines"] = int(out.get("total") or 0) - n_folded


def _observed_view_groups(ws: Path, *, now_iso: str, group_by: str,
                          people: dict, index: dict,
                          start_n: int) -> tuple[list, int]:
    """The live observed rows, grouped and numbered like the queue's own rows
    (same grouping mode, same ordering rule — `_bucket_and_order`, the one
    copy), continuing the display numbering from `start_n`. Returns
    `(groups, n_rows)`; `([], 0)` when the tier is empty or unreadable — the
    queue must render without this section before it renders without its own
    rows."""
    from cru_match import _commitment_field

    try:
        from capture_gate import live_observed
        rows_src = live_observed(ws, now=_aware_now(now_iso))
    except Exception:
        return [], 0
    if not rows_src:
        return [], 0

    by_meeting = group_by == GROUP_MEETING
    ordered = _bucket_and_order(rows_src, by_meeting=by_meeting,
                                people=people, index=index, ws=ws,
                                now_iso=now_iso)

    display_n = start_n
    groups: list[dict] = []
    for key, label, date, bucket_rows in ordered:
        rows = []
        for ev in bucket_rows:
            display_n += 1
            d = ev.get("data") or {}
            rows.append({
                "display_n": display_n,
                "commitment_id": _commitment_id(ev),
                "title": (_commitment_field(ev, "title")
                          or d.get("summary") or "(untitled)"),
                "age_days": _age_days(ev.get("ts") or "", now_iso),
                # Not a review_reason clause (no RRF1 overlay applies): the
                # gate's own record of why this was kept without opening.
                "review_reason": ("set aside at capture — "
                                  f"{d.get('observed_reason') or 'other'}"),
                "source_skill": ev.get("source_skill") or "",
                "due": None,   # the caution rail refuses dated items observed
                "evidence": _evidence_text(ev),
                "weak_reason": _weak_reason(ev),
                "observed": True,
            })
        group = {"name": label, "count": len(rows), "items": rows,
                 "observed": True}
        if by_meeting:
            group["group_key"] = key
            group["date"] = date
        groups.append(group)
    return groups, display_n - start_n


def _aware_now(now_iso: str):
    """`now_iso` as an aware datetime for `live_observed`, or None (its own
    clock) when the string will not parse. One parser (REVIEW S2): the
    canonical `event_time.parse_ts`, which `_age_days` and `watch_gate._parse`
    already read through — a private fourth ISO parser here is the drift
    class `event_time` exists to end."""
    try:
        from event_time import parse_ts

        return parse_ts(str(now_iso))
    except Exception:
        return None


def addressable_total(view: dict) -> int:
    """The bound to hand `parse_selection` for THIS view: every display
    number a typed answer may name — the queue's own rows plus the set-aside
    section (OBSERVED1). `view["total"]` keeps its shipped meaning (the
    header's counted sentence, the number `headline.unconfirmed` points at)
    and cannot double as the selection bound now the view can carry a second
    numbered section."""
    return int(view.get("total") or 0) + int(view.get("n_observed") or 0)


def selection_numbers(view: dict, spec) -> list[int]:
    """THE view-aware selection parse (REVIEW OBSERVED1 C2) — the one call
    the skill makes for a typed answer.

    `all` answers the QUEUE'S OWN rows only: the header's counted sentence
    ("N unconfirmed extractions") is the question the user is answering, and
    a one-word sweep must never mint or dismiss the set-aside tier — the
    tier's own doctrine says promotion stays an explicit gesture, and every
    bulk drop is a dismissal-tuning signal the gate learns from. Explicit
    NUMBERS and RANGES reach both tiers (validated against
    `addressable_total`): a typed number is the user reading THAT row, which
    is exactly the bar the tier's verbs want.
    """
    text = str(spec or "").strip().lower()
    if text == "all":
        total = int(view.get("total") or 0)
        if not total and view.get("n_observed"):
            raise ValueError(
                "`all` answers the tracked queue only, and it is empty — "
                "the set-aside rows answer by their own numbers (or one "
                "tap), never in one word")
        return parse_selection("all", total)
    return parse_selection(spec, addressable_total(view))


# HELDREVIEW1 DD-2 — the review header. Counts FIRST (the number is the whole
# question M is answering), then the period the rows span, then the promise
# that each row says why. The wording is M's own framing of the decision:
# these are the ones it would stop asking about.
WOULD_HOLD_EMPTY = ("Nothing would be held — every capture on file cleared "
                    "the floor.")


def would_hold_header(total: int, window: str = "", *,
                      n_lines=None) -> str:
    """The scoped view's header: the count, the window, the promise.

    `n_lines` (CLUSTER1) is the information count when the view clustered —
    the headline says clusters, the true capture count stays in the same
    sentence. None (the default, and every un-clustered render) is
    byte-identical to before the parameter existed."""
    if not total:
        return WOULD_HOLD_EMPTY
    noun = "capture" if total == 1 else "captures"
    if n_lines is not None and n_lines != total:
        line_noun = "item" if n_lines == 1 else "items"
        head = (f"{n_lines} {line_noun} ({total} {noun}) — this is what I'd "
                f"stop asking you about. Each row says why it was weak.")
    else:
        head = (f"{total} {noun} — this is what I'd stop asking you about. "
                f"Each row says why it was weak.")
    return f"{head} {window}" if window else head


def week_span_phrase(events, workspace_path=None) -> str:
    """"From the weeks of Aug 3, Aug 10 and Aug 17." — or "".

    The window is DERIVED from the rows in hand rather than imposed on them:
    a review of what would be hidden that silently drops part of what would be
    hidden is the one thing this surface must not do. Weeks are ISO weeks
    named by their Monday, oldest first.

    TZDATE3 — each row's ts localizes via `tz.localize_date` first when
    `workspace_path` is passed, so a late-evening-local capture is grouped
    (and named) under its local ISO week rather than the next UTC day's.
    `workspace_path=None` (the default) keeps the pre-TZDATE3 raw-UTC-slice
    behavior."""
    mondays = set()
    for ev in events or []:
        raw = str((ev or {}).get("ts") or "")
        ts = (_localize_date(raw, workspace_path)[:10]
              if workspace_path and raw else raw[:10])
        try:
            day = _dt.date.fromisoformat(ts)
        except Exception:
            continue
        mondays.add(day - _dt.timedelta(days=day.weekday()))
    if not mondays:
        return ""
    labels = [f"{_MONTHS[d.month - 1]} {d.day}" for d in sorted(mondays)]
    if len(labels) == 1:
        return f"From the week of {labels[0]}."
    joined = ", ".join(labels[:-1]) + f" and {labels[-1]}"
    return f"From the weeks of {joined}."


def _review_drain_offer(workspace_root, now_iso) -> str:
    """The one-line bulk-drain offer for this queue, or "" (REVSCHED1 §3-1).

    Thin by design: the bar, the wording and the derivation all live in
    `commitment_backlog_sweep.review_offer`, so the digest and this queue
    cannot drift into offering two different things. Lazy import — the sweep
    module is a heavy read-side module and this queue is on the daily path.

    Never raises and never refuses: an offer line that can break the queue it
    decorates is worse than no offer line.
    """
    try:
        from commitment_backlog_sweep import review_offer
        offer = review_offer(workspace_root, now_iso=now_iso)
    except Exception:
        return ""
    return (offer or {}).get("line") or ""


EMPTY_TEXT = ("Nothing needs your call — every captured item has been "
              "confirmed or dropped.")


def _observed_only_lead(n: int) -> str:
    """The lead line when the queue's own rows are empty and only set-aside
    rows remain — ONE wording for the text render and the widget header
    (REVIEW OBSERVED1 R3: the widget was still saying '0 unconfirmed
    extractions from 0 calls' above a populated section)."""
    noun_is = "item is" if n == 1 else "items are"
    return (f"Nothing needs your call on tracked items — but {n} "
            f"set-aside {noun_is} on file from your calls:")


def render_text(view: dict) -> str:
    """The scannable list the skill pastes to the user, verbatim.

    Numbered across the whole queue so a range answer ("confirm 1-12, drop
    13") is unambiguous, and grouped so the user can also answer by
    counterparty ("confirm all Acme rows")."""
    if not view.get("total") and not view.get("n_observed"):
        return EMPTY_TEXT

    if not view.get("total"):
        # OBSERVED1 — only set-aside rows on file. The counted header is a
        # sentence about the queue's own rows; with zero of those it would
        # read "0 unconfirmed extractions from 0 calls", so the section
        # leads instead. Same wording on the widget path (REVIEW R3).
        lines = [_observed_only_lead(view.get("n_observed") or 0)]
    else:
        lines = [view["header"]]
    # PERSONLOOP1 — the offer sits in the HEADER position because it explains
    # the list below it: these names are why the queue is this long.
    offer = str(view.get("person_candidate_offer") or "").strip()
    if offer:
        lines.append(offer)
    # REVSCHED1 §3-1 — directly under the header, before the rows, because a
    # reader who has decided to work the list row by row has already stopped
    # reading by the time a footer arrives. Absent entirely below the bar.
    if view.get("offer"):
        lines.append(view["offer"])
    lines.append("")
    for group in view.get("groups") or []:
        # CLUSTER1 — one line per real-world item: a folded row renders
        # under its cluster's surviving line (read-only, number kept), never
        # as a line of its own. A group whose rows ALL folded elsewhere is
        # skipped whole — its rows are on the page, under their survivors.
        shown_rows = [r for r in group["items"] if not r.get("folded_into")]
        if not shown_rows:
            continue
        lines.append(f"{group['name']} ({group['count']})")
        for row in shown_rows:
            bits = []
            age = row.get("age_days")
            if age is not None:
                bits.append("1 day old" if age == 1 else f"{age} days old")
            if row.get("due"):
                bits.append(f"due {row['due']}")
            if row.get("source_skill"):
                bits.append(f"from {row['source_skill']}")
            cluster = row.get("cluster")
            if cluster:
                bits.append(f"+{cluster['n_folded']} folded — the same "
                            f"real-world item")
            tail = (" — " + " · ".join(bits)) if bits else ""
            lines.append(f"  {row['display_n']}. {row['title']}{tail}")
            if row.get("review_reason"):
                lines.append(f"       why it's here: {row['review_reason']}")
            # BULKGUARD — every row shows what it rests on, before any accept.
            if row.get("weak_reason"):
                lines.append(f"       evidence: NONE that holds up — "
                             f"{row['weak_reason']}. Bulk answers skip this "
                             f"row; say `confirm {row['display_n']}` on its "
                             f"own to keep it.")
            else:
                evd = row.get("evidence") or ""
                if len(evd) > 110:
                    evd = evd[:107] + "..."
                lines.append(f"       evidence: \"{evd}\"")
            if cluster:
                # The folded rows, read-only, numbers kept — reachable one
                # level down, individually answerable by their own numbers.
                from commitment_cluster import folded_line
                lines.append("       folded in (read-only — these read as "
                             "the same item):")
                for f in cluster["folded"]:
                    lines.append("         + " + folded_line(
                        f.get("display_n"), f.get("title") or "(untitled)",
                        f.get("group") or ""))
        lines.append("")
    # OBSERVED1 — the set-aside section, after the queue's own rows and
    # under its own labelled banner: same row format, same numbering scheme
    # (continuing), so a typed answer addresses either tier the same way.
    if view.get("n_observed"):
        from capture_gate import OBSERVED_SECTION_TITLE
        lines.append(f"{OBSERVED_SECTION_TITLE} ({view['n_observed']})")
        for group in view.get("observed_groups") or []:
            lines.append(f"{group['name']} ({group['count']})")
            for row in group["items"]:
                bits = []
                age = row.get("age_days")
                if age is not None:
                    bits.append("1 day old" if age == 1
                                else f"{age} days old")
                if row.get("source_skill"):
                    bits.append(f"from {row['source_skill']}")
                tail = (" — " + " · ".join(bits)) if bits else ""
                lines.append(f"  {row['display_n']}. {row['title']}{tail}")
                if row.get("review_reason"):
                    lines.append(f"       why it's here: "
                                 f"{row['review_reason']}")
                if row.get("weak_reason"):
                    lines.append(f"       evidence: NONE that holds up — "
                                 f"{row['weak_reason']}. Bulk answers skip "
                                 f"this row; say `confirm "
                                 f"{row['display_n']}` on its own to track "
                                 f"it.")
                else:
                    evd = row.get("evidence") or ""
                    if len(evd) > 110:
                        evd = evd[:107] + "..."
                    lines.append(f"       evidence: \"{evd}\"")
        lines.append("")
        lines.append("These were heard between other people, so I kept them "
                     "without tracking. `confirm N` starts tracking one as "
                     "an ordinary open item; `drop N` lets it go. Either "
                     "way the original stays in history.")
    # REVIEW OBSERVED1 R5 — the group-answer invitation renders only when
    # the queue's own rows exist: group phrases deliberately answer no
    # set-aside row (`ids_for_group`), so in the observed-only case the
    # footer would invite the one gesture that cannot address anything on
    # screen. The observed section's own hint above already covers its rows.
    if view.get("total") and view.get("group_by") == GROUP_MEETING:
        lines.append("Say `confirm 1-5` to keep them, `drop 6,7` to let them "
                     "go, `not mine 8` if it was someone else's, or name a "
                     "call to answer the whole group. Nothing changes until "
                     "you say so.")
    elif view.get("total"):
        lines.append("Say `confirm 1-5` to keep them, `drop 6,7` to let them "
                     "go, or name a group (`confirm all Acme`). Nothing "
                     "changes until you say so.")
    n_weak = view.get("n_weak") or 0
    if n_weak:
        noun = "row has" if n_weak == 1 else "rows have"
        lines.append(f"{n_weak} {noun} nothing behind them but the "
                     f"extractor's guess — `confirm all`, group confirms and "
                     f"ranges will hold those; confirm each by its own "
                     f"number, or drop them.")
    # CLUSTER1 — the one-tap offer, only when something clustered. A cluster
    # is a display fact until this gesture; ignoring it, expanding it, or
    # answering rows one at a time changes nothing.
    if view.get("n_folded"):
        n_c = view.get("n_clusters") or 0
        covered = (view.get("n_folded") or 0) + n_c
        noun_c = "line covers" if n_c == 1 else "lines cover"
        lines.append(f"{n_c} {noun_c} {covered} rows that read as the same "
                     f"real-world item — say `keep as one N` (the line's "
                     f"number) to fold them for good; that writes through "
                     f"the ordinary merge path and one `undo` splits them "
                     f"back out. Leaving them alone changes nothing.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Selection parsing
# ---------------------------------------------------------------------------

def parse_selection(spec, total: int) -> list[int]:
    """Turn a selection phrase into display numbers: "1,3,5-9" -> [1,3,5..9],
    "all" -> [1..total]. Whitespace and repeated separators are tolerated;
    the result is sorted and de-duplicated.

    Raises ValueError — loudly, naming the offending token — on anything it
    cannot read, including a number outside 1..total. Guessing what the user
    meant here would confirm or drop the wrong promise. Group selections
    ("all Acme") are resolved by the skill against the view's group names,
    not here.
    """
    text = str(spec or "").strip().lower()
    if not text:
        raise ValueError("no selection given — say something like "
                         "`confirm 1-5` or `confirm all`")
    if total <= 0:
        raise ValueError("the queue is empty — nothing to select")
    if text == "all":
        return list(range(1, total + 1))

    out: set[int] = set()
    for token in (t.strip() for t in text.replace(" and ", ",").split(",")):
        if not token:
            continue
        if "-" in token:
            lo_s, _, hi_s = token.partition("-")
            lo_s, hi_s = lo_s.strip(), hi_s.strip()
            if not (lo_s.isdigit() and hi_s.isdigit()):
                raise ValueError(f"could not read {token!r} as a range of "
                                 f"row numbers")
            lo, hi = int(lo_s), int(hi_s)
            if lo > hi:
                lo, hi = hi, lo
            for n in range(lo, hi + 1):
                _check_range(n, total)
                out.add(n)
        elif token.isdigit():
            n = int(token)
            _check_range(n, total)
            out.add(n)
        else:
            raise ValueError(
                f"could not read {token!r} as a row number. Use numbers and "
                f"ranges (`1,3,5-9`) or `all`; a group name like "
                f"`all Acme` is resolved against the list, not here.")
    return sorted(out)


def _check_range(n: int, total: int) -> None:
    if not 1 <= n <= total:
        raise ValueError(f"row {n} is not on the list — it runs 1 to {total}")


def individually_named(spec) -> set:
    """BULKGUARD — the display numbers the user typed as STANDALONE tokens
    ("confirm 3, 17" names 3 and 17), as opposed to swept in by `all`, a
    range, or a group phrase. These are the only rows whose weak-evidence
    hold may be overridden: sweeping a bare guess into the open book takes a
    human reading THAT row and naming THAT number. "confirm 1-40" names
    nothing individually; "confirm 1-40, 44" names 44. Non-numeric input
    (including group phrases) names nothing — never raises."""
    out: set = set()
    text = str(spec or "").strip().lower()
    for token in (t.strip() for t in text.replace(" and ", ",").split(",")):
        if token.isdigit():
            out.add(int(token))
    return out


def ids_for_group(view: dict, group) -> list[str]:
    """Every commitment id in ONE group, in list order.

    `group` matches a group's `group_key` (exact) or its display `name`
    (case-insensitive). Ambiguity is a loud ValueError for the same reason
    `parse_selection` refuses to guess: two calls with the same title on the
    same day are two different decisions."""
    want = str(group or "").strip()
    if not want:
        raise ValueError("name a group — say the call, or use row numbers")
    hits = [g for g in (view.get("groups") or [])
            if g.get("group_key") == want]
    if not hits:
        hits = [g for g in (view.get("groups") or [])
                if str(g.get("name") or "").strip().lower() == want.lower()]
    if not hits:
        hits = [g for g in (view.get("groups") or [])
                if want.lower() in str(g.get("name") or "").lower()]
    if not hits:
        raise ValueError(f"no group on the list matches {group!r}")
    if len(hits) > 1:
        names = ", ".join(str(g.get("name")) for g in hits)
        raise ValueError(f"{group!r} matches more than one group ({names}) — "
                         f"say which one")
    return [row["commitment_id"] for row in hits[0]["items"]]


def ids_for_selection(view: dict, numbers) -> list[str]:
    """Display numbers -> commitment ids, in list order. Unknown numbers are
    a loud ValueError for the same reason parse_selection refuses to guess.
    OBSERVED1: the set-aside section's numbers resolve too (to `obs_` ids —
    the write wrappers dispatch those through the observed transition);
    `parse_selection` needs `addressable_total(view)` as its bound for those
    numbers to survive the range check."""
    by_n = {row["display_n"]: row["commitment_id"]
            for g in ((view.get("groups") or [])
                      + (view.get("observed_groups") or []))
            for row in g["items"]}
    out = []
    for n in numbers:
        if n not in by_n:
            raise ValueError(f"row {n} is not on the list")
        out.append(by_n[n])
    return out


# ---------------------------------------------------------------------------
# The two writes
# ---------------------------------------------------------------------------

def _resolve_user(workspace_root) -> str:
    try:
        from primary_user import resolve_primary_user

        return resolve_primary_user(workspace_root) or ""
    except Exception:
        return ""


def _pending_by_id(workspace_root) -> dict:
    from cru_match import load_needs_review

    ws = Path(workspace_root)
    return {_commitment_id(ev): ev
            for ev in load_needs_review(str(_events_path(ws)),
                                        workspace_root=str(ws))}


# ---------------------------------------------------------------------------
# OBSERVED1 — the observed transition, dispatched from the same two verbs.
#
# An observed row's ONE defined transition is `capture_gate.promote_observed`
# (there has never been a `commitment_confirmed` type — zero lifetime across
# the first workspace measured, and it is registered nowhere): promotion
# appends a REAL `commitment` carrying `pending_review` + `promoted_from`,
# which puts the item ON the standard closure path. The queue's verbs then do
# exactly what they already do — confirm clears the review flags (one gesture,
# an ordinary open commitment), drop closes it as dropped (the standard
# tombstone, which also feeds the capture gate's dismissal tuning — dropping
# observed noise SHOULD teach the gate). Both are appends; the observed event
# is never rewritten, and once promoted it stops surfacing everywhere by the
# tier's own permanent-promotion rule.
# ---------------------------------------------------------------------------


def _observed_by_id(workspace_root) -> dict:
    """Every observed row, keyed by EVERY id spelling a surface can render
    for it (latest event per row, live or not): `data.id` when the row
    carries one, and the `_commitment_id` fallback (`commitment_seq_<seq>`)
    either way — REVIEW OBSERVED1 R1: a legacy row with no `data.id` renders
    under the fallback spelling, and a map keyed on `data.id` alone made
    that row unanswerable from the very surfaces built to answer it.
    Membership here is what routes an id through the observed transition;
    expiry/already-promoted are enforced by `promote_observed` itself, so a
    stale id gets the tier's own plain refusal rather than a generic
    not-found."""
    try:
        from capture_gate import OBSERVED_TYPE, _iter_ws_events
    except Exception:
        return {}
    out: dict = {}
    for ev in _iter_ws_events(workspace_root):
        if ev.get("type") != OBSERVED_TYPE:
            continue
        oid = str((ev.get("data") or {}).get("id") or "")
        if oid:
            out[oid] = ev
        fallback = _commitment_id(ev)
        if fallback and not fallback.endswith("_?"):
            out[fallback] = ev
    return out


def _observed_promote_ref(ev: dict) -> str:
    """The spelling `promote_observed` resolves THIS row by — `data.id`, or
    the bare seq for a legacy id-less row (the same `or` its own
    `promoted_from` stamp uses)."""
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    oid = str(d.get("id") or "")
    if oid:
        return oid
    return str(ev.get("seq")) if ev.get("seq") is not None else ""


def _promoted_commitment_ref(workspace_root, promote_ref: str) -> str:
    """The id spelling of the commitment a promotion minted for this observed
    row (`data.promoted_from == promote_ref`) — its `data.id` when stamped,
    else the `commitment_seq_<seq>` legacy spelling `normalize_commitment_id`
    already accepts. "" when no promotion exists. The fresh-promote path
    never needs this scan (promote_observed returns the stamped copy); this
    is the `already`-promoted fallback."""
    try:
        from capture_gate import _iter_ws_events
    except Exception:
        return ""
    hit = None
    for ev in _iter_ws_events(workspace_root):
        if (ev.get("type") == "commitment"
                and str((ev.get("data") or {}).get("promoted_from") or "")
                == str(promote_ref)):
            hit = ev
    if hit is None:
        return ""
    return _commitment_id(hit)


def _promote_for_dispatch(workspace_root, obs_ev: dict, *,
                          source_skill: str) -> tuple:
    """Shared first half of both observed verbs: promote (or find the prior
    promotion) and return `(ref, already, error_result)` where exactly one of
    `ref` / `error_result` is set. The fresh-promote ref comes from the
    STAMPED copy promote_observed returns — no rescan; the `already` branch
    falls back to the promoted_from scan."""
    from capture_gate import promote_observed

    promote_ref = _observed_promote_ref(obs_ev)
    obs_key = _commitment_id(obs_ev)
    r = promote_observed(workspace_root, promote_ref, corroborated_by="user",
                         source_skill=source_skill)
    if not r.get("ok"):
        return "", False, {"commitment_id": obs_key, "status": "failed",
                           "detail": r.get("reason") or ""}
    if r.get("already"):
        ref = _promoted_commitment_ref(workspace_root, promote_ref)
    else:
        ref = _commitment_id(r.get("commitment") or {})
        if not ref or ref.endswith("_?"):
            ref = _promoted_commitment_ref(workspace_root, promote_ref)
    if not ref:
        return "", False, {"commitment_id": obs_key, "status": "failed",
                           "detail": "promotion left no commitment to act on"}
    return ref, bool(r.get("already")), None


# The one-line honesty owed when the second append fails after the first
# landed: the row has ALREADY left the set-aside tier (promotion is
# permanent), so it now sits in the queue proper as an unconfirmed row.
_OBSERVED_HALF_LANDED = (" — the item now sits in the needs-your-call queue "
                         "as an unconfirmed row; answer it there, or repeat "
                         "this verb to finish")


def _confirm_observed_row(workspace_root, obs_ev: dict, *, cleared_by: str,
                          source_skill: str, gesture_iso: str,
                          pending: dict) -> dict:
    """Confirm ONE observed row: promote, then clear the promoted row's
    review flags — one gesture, two appends, an ordinary open commitment.
    Statuses: `confirmed` / `not_pending` (already adjudicated) / `failed`
    (the tier refused — expired, titleless — with its own plain reason)."""
    from commitment_state import CommitmentIdError, clear_review_flags

    obs_key = _commitment_id(obs_ev)
    ref, already, err = _promote_for_dispatch(workspace_root, obs_ev,
                                              source_skill=source_skill)
    if err:
        return err
    if already and ref not in pending:
        # Promoted earlier AND no longer awaiting review — the question was
        # already answered (confirmed, or closed another way). Honest no-op.
        return {"commitment_id": obs_key, "status": "not_pending",
                "promoted_id": ref}
    try:
        res = clear_review_flags(
            workspace_root, ref, cleared_by=cleared_by,
            source_skill=source_skill,
            note=("confirmed from the needs-your-call queue — a set-aside "
                  "item you told me to track"),
            mint_now_iso=gesture_iso,
        )
    except CommitmentIdError as exc:
        return {"commitment_id": obs_key, "status": "failed",
                "detail": str(exc) + _OBSERVED_HALF_LANDED}
    status = res.get("status")
    if status == "cleared":
        return {"commitment_id": obs_key, "status": "confirmed",
                "promoted_id": res.get("commitment_id", ref)}
    return {"commitment_id": obs_key, "status": "not_pending",
            "promoted_id": res.get("commitment_id", ref)}


def _drop_observed_row(workspace_root, obs_ev: dict, *, resolved_by: str,
                       evidence: str, source_skill: str, source_ref,
                       mint_iso: str, confirmed_open: set) -> dict:
    """Drop ONE observed row: promote, then close the promoted row as
    dropped — the standard tombstone, appended, nothing rewritten. Statuses
    are the closure path's OWN vocabulary (FS-18: one status, one meaning,
    everywhere): `closed` / `already_resolved` / `confirmed_open` (promoted
    earlier and since confirmed — the queue never closes confirmed work) /
    `has_subitems` (the same per-row refusal the queue path reports) /
    `failed`."""
    from commitment_state import (AmbiguousTargetError, CommitmentIdError,
                                  OpenSubitemsError, close_commitment)

    obs_key = _commitment_id(obs_ev)
    ref, _already, err = _promote_for_dispatch(workspace_root, obs_ev,
                                               source_skill=source_skill)
    if err:
        return err
    if ref in confirmed_open:
        return {"commitment_id": obs_key, "status": "confirmed_open",
                "promoted_id": ref,
                "detail": "you confirmed this one earlier — it is an open "
                          "commitment now; use the ordinary close path"}
    try:
        res = close_commitment(
            workspace_root, ref, resolved_by=resolved_by,
            evidence=evidence, source_skill=source_skill,
            resolution="dropped", user_confirmed=True,
            source_ref=source_ref, mint_now_iso=mint_iso,
        )
    except CommitmentIdError as exc:
        return {"commitment_id": obs_key, "status": "failed",
                "detail": str(exc) + _OBSERVED_HALF_LANDED}
    except AmbiguousTargetError as exc:
        # REVIEW_PR57 F-6 — CLOSEID2 retrofitted this catch onto every other
        # closure callsite; this drop path merged alongside it and was the
        # one without it. Low reachability (promotion-minted refs are
        # unambiguous), but an uncaught raise here would abort the batch
        # after this row's promotion already landed — same refuse-the-row
        # contract as the queue path 700 lines below.
        return {"commitment_id": obs_key, "status": "failed",
                "detail": str(exc) + _OBSERVED_HALF_LANDED}
    except OpenSubitemsError as exc:
        # Same per-row refusal the queue path reports 20 lines below — an
        # uncaught raise here would abort the rest of the batch after this
        # row's promotion already landed.
        return {"commitment_id": obs_key, "status": "has_subitems",
                "promoted_id": ref,
                "detail": str(exc) + _OBSERVED_HALF_LANDED}
    status = res.get("status")
    if status == "closed":
        return {"commitment_id": obs_key, "status": "closed",
                "promoted_id": res.get("commitment_id", ref)}
    return {"commitment_id": obs_key, "status": "already_resolved",
            "promoted_id": res.get("commitment_id", ref)}


def confirm_items(workspace_root, ids, *, source_skill: str = SOURCE_SKILL,
                  confirm_weak_ids=()) -> dict:
    """Confirm unconfirmed extractions: they become ordinary open commitments.

    One `commitment_updated` per id through `commitment_state.clear_review_flags`
    (`data.review_flags_cleared: true`), which the loader folds to clear
    `pending_review` read-side. The capture event is never rewritten.

    BULKGUARD — a WEAK row (no evidence text, or title-match-only evidence;
    see `_weak_reason`) is HELD, not confirmed, unless its id is in
    `confirm_weak_ids`. The caller may put an id there ONLY when the user
    typed that row's number as a standalone token (`individually_named`) —
    `all`, group phrases and ranges never qualify. This is the write-layer
    backstop for the incident class: six commitments closed in two seconds off
    title-match evidence the matcher had refused the day before. A held row is
    REPORTED (`held_weak_evidence`, with the reason), never written.

    WATCHGATE §2.2 — the accept/hold split itself is now
    `watch_gate.screen_bulk_accept`, THE shared fence, exercised from here and
    from the proposal queue's batch apply. The decision is identical to the
    inline loop it replaces; what changed is that there is only one of it.

    IDEMPOTENT-SAFE: an id that is no longer pending — already confirmed,
    already closed, or never in the queue — is REPORTED (`not_pending` /
    `not_open`), never raised and never written. Re-running the same
    selection is a no-op with an honest ack, not a second event.

    OBSERVED1 — an `obs_` id (a live set-aside row) rides the same fence and
    the same counters, through `_confirm_observed_row`: promote, then clear
    the promoted row's flags — one gesture, an ordinary open commitment.
    Idempotent by the tier's own rules (a second confirm is `not_pending`;
    an expired row is `failed` with the tier's plain reason). The result row
    carries `promoted_id` so the ack can point at what now exists.

    Returns {"results": [{commitment_id, status}, ...], "n_confirmed": int,
             "n_not_pending": int, "n_held": int, "n_failed": int}.
    """
    from commitment_state import CommitmentIdError, clear_review_flags

    pending = _pending_by_id(workspace_root)
    # OBSERVED1 — the set-aside rows this queue now also answers. Routed by
    # id membership, never by prefix; a pending id always wins the route (the
    # two id schemes cannot collide, but the order states the priority).
    # REVIEW E1 — the observed index is a second full-log scan, so it is
    # built ONLY when some id is not already a queue member; the common
    # all-pending selection pays nothing new.
    observed: dict = {}
    if any(str(c) not in pending for c in (ids or [])):
        observed = _observed_by_id(workspace_root)
    cleared_by = _resolve_user(workspace_root)
    # PROVMINT1 — this call IS one gesture, so its minted receipt is read from
    # the clock ONCE and handed to every write below. Minting per row would
    # hand the coverage metric N distinct pointers for one decision.
    _gesture_iso = _now_iso()
    results: list[dict] = []
    n_confirmed = n_not_pending = n_held = n_failed = 0

    # THE fence (WATCHGATE §2.2), over the rows this queue can actually
    # answer. An id that is not in the queue is reported first and never
    # reaches the screen — it has no evidence to weigh either way.
    # OBSERVED1: set-aside rows ride the SAME fence — confirming one MINTS an
    # open commitment, so an evidence-less observed row is exactly the class
    # the bulk guard exists for.
    screenable = [cid for cid in (str(c) for c in (ids or []))
                  if cid in pending or cid in observed]
    screen = screen_bulk_accept(
        [{"id": cid,
          "weak_reason": _weak_reason(pending.get(cid) or observed[cid])}
         for cid in screenable],
        individually_named=confirm_weak_ids or (),
    )
    accepted = set(screen["accept"])
    held_reason = {h["id"]: h["reason"] for h in screen["held"]}

    for cid in ids or []:
        cid = str(cid)
        if cid not in pending and cid in observed:
            if cid not in accepted:
                results.append({"commitment_id": cid,
                                "status": "held_weak_evidence",
                                "detail": held_reason.get(cid, "")})
                n_held += 1
                continue
            res = _confirm_observed_row(
                workspace_root, observed[cid], cleared_by=cleared_by,
                source_skill=source_skill, gesture_iso=_gesture_iso,
                pending=pending)
            results.append(res)
            if res["status"] == "confirmed":
                n_confirmed += 1
            elif res["status"] == "not_pending":
                n_not_pending += 1
            else:
                n_failed += 1
            continue
        if cid not in pending:
            results.append({"commitment_id": cid, "status": "not_pending"})
            n_not_pending += 1
            continue
        if cid not in accepted:
            results.append({"commitment_id": cid,
                            "status": "held_weak_evidence",
                            "detail": held_reason.get(cid, "")})
            n_held += 1
            continue
        try:
            res = clear_review_flags(
                workspace_root, cid, cleared_by=cleared_by,
                source_skill=source_skill,
                note="confirmed from the needs-your-call queue",
                # PROVMINT1 — one gesture, one receipt: the clock is read once
                # for the whole batch above, so N confirms in one answer carry
                # ONE pointer rather than N a second apart.
                mint_now_iso=_gesture_iso,
            )
        except CommitmentIdError as exc:
            results.append({"commitment_id": cid, "status": "not_found",
                            "detail": str(exc)})
            n_failed += 1
            continue
        status = res.get("status")
        results.append({"commitment_id": res.get("commitment_id", cid),
                        "status": status})
        if status == "cleared":
            n_confirmed += 1
        else:
            n_not_pending += 1
    return {"results": results, "n_confirmed": n_confirmed,
            "n_not_pending": n_not_pending, "n_held": n_held,
            "n_failed": n_failed}


def confirm_satisfied_reasons(workspace_root, *,
                              source_skill: str = SOURCE_SKILL,
                              brain_batch_id: Optional[str] = None,
                              brain_change_class: Optional[str] = None,
                              stamp_only_ids=None) -> dict:
    """The reason-scoped batch verb (BUG-8330 item 4).

    The old batch surface was reason-BLIND and per-id only: nothing could
    say "clear every item whose reason is answered". This scans the
    projection for items the read-side fold marked
    `review_reason_auto_satisfied` (a SOLE review_reason clause whose
    mechanical check no longer holds — e.g. "counterparty 'X' has no person
    record" where X resolves to a contact today) and formalizes each as an
    ordinary `clear_review_flags` event — the ONE write path, so history
    shows an explicit adjudication instead of a state perpetually re-derived
    at read time. The capture event is never rewritten.

    No weak-evidence screen applies: nothing here accepts extraction
    evidence in bulk — the reason the item was held is gone, and the fold
    has ALREADY released it for gating; this only makes that durable.

    Returns {"results": [...], "n_cleared": int, "n_failed": int}.

    ATTENDEE1 — `brain_batch_id` / `brain_change_class` are passed straight
    through to `clear_review_flags`, both None by default so the manual verb
    writes a byte-identical event. An AUTOMATIC caller supplies them so the
    clears it caused land in the same undo batch as the record that released
    them.

    `stamp_only_ids` SCOPES THAT BATCH, and it closes review F-3. This
    function is workspace-WIDE: it formalizes every auto-satisfied row,
    including rows released long ago by unrelated conditions that simply never
    got written down. Formalizing them is right and stays. But stamping them
    put them in the undo batch too, so a user who took back ONE added contact
    got every incidental row reopened with it — the receipt said "2 cleared"
    while `undo` reversed 3. Pass the ids the caller can actually attribute and
    only those carry the stamp; the rest are still cleared, just not claimed.
    None (the default) stamps everything, which is the pre-F-3 behaviour.
    """
    from commitment_state import CommitmentIdError, clear_review_flags
    from cru_match import load_open_commitments

    events_path = _events_path(Path(workspace_root))
    opens = load_open_commitments(events_path, workspace_root=workspace_root)
    cleared_by = _resolve_user(workspace_root)
    # PROVMINT1 — this call IS one gesture, so its minted receipt is read from
    # the clock ONCE and handed to every write below. Minting per row would
    # hand the coverage metric N distinct pointers for one decision.
    _gesture_iso = _now_iso()
    results: list[dict] = []
    n_cleared = n_failed = 0
    for ev in opens:
        d = ev.get("data") or {}
        if not d.get("review_reason_auto_satisfied"):
            continue
        cid = _commitment_id(ev)
        _stamp = (brain_batch_id is not None
                  and (stamp_only_ids is None
                       or str(cid) in {str(x) for x in stamp_only_ids}))
        try:
            res = clear_review_flags(
                workspace_root, cid, cleared_by=cleared_by,
                source_skill=source_skill,
                note=f"review reason satisfied — {str(d.get('review_reason') or '')[:120]}",
                mint_now_iso=_gesture_iso,   # PROVMINT1 — one pass, one receipt
                # F-3: only rows the caller can attribute join the batch.
                brain_batch_id=brain_batch_id if _stamp else None,
                brain_change_class=brain_change_class if _stamp else None,
            )
        except CommitmentIdError as exc:
            results.append({"commitment_id": cid, "status": "not_found",
                            "detail": str(exc)})
            n_failed += 1
            continue
        results.append({"commitment_id": res.get("commitment_id", cid),
                        "status": res.get("status")})
        if res.get("status") == "cleared":
            n_cleared += 1
        else:
            n_failed += 1
    return {"results": results, "n_cleared": n_cleared, "n_failed": n_failed}


def confirm_group(workspace_root, view: dict, group, *,
                  source_skill: str = SOURCE_SKILL) -> dict:
    """`confirm group` — confirm one whole meeting group, STRONG rows only.

    Naming a call is a bulk gesture: it names no row individually, so
    `confirm_weak_ids` is EMPTY and the shared fence
    (`watch_gate.screen_bulk_accept`, reached through `confirm_items`) holds
    every weak row exactly as `confirm all` and a range do. This function adds
    no policy of its own — it resolves the group to ids and calls the one
    write path. Never widen `confirm_weak_ids` from here; that override
    belongs to the user typing a single number."""
    return confirm_items(workspace_root, ids_for_group(view, group),
                         source_skill=source_skill, confirm_weak_ids=())


# The dispatcher that answers a queue row on a SURFACE's behalf. It is not a
# surface itself (nothing renders as `apply-choices`), which is why it cannot
# be derived from the verb's `surfaces` tuple and is named here instead.
DISPATCH_SKILL = "apply-choices"


def allowed_done_surfaces() -> frozenset:
    """Where an `already done` may be attested — DERIVED from the verb's own
    `verb_taxonomy` row, so the allow-list cannot drift from the table that
    says where the verb renders, plus the one dispatcher.

    Fail-CLOSED: if the taxonomy cannot be read the set narrows to the two
    names this module owns, rather than widening to anything."""
    surfaces: set = set()
    try:
        from verb_taxonomy import taxonomy_row

        surfaces = set((taxonomy_row(DONE_ACTION) or {}).get("surfaces") or ())
    except Exception:  # pragma: no cover — narrow, never widen
        surfaces = set()
    return frozenset(surfaces | {SOURCE_SKILL, DISPATCH_SKILL})


def _checked_done_surface(source_skill) -> str:
    """SF-3. `source_skill` is interpolated into the Done's evidence line, so
    an unvalidated one defeats §2.3's honesty rule with ZERO code change:
    `source_skill="matched an outbound send, score 0.94, thread ..."` writes
    exactly the fabricated match evidence this verb refuses to fabricate."""
    allowed = allowed_done_surfaces()
    if isinstance(source_skill, str) and source_skill in allowed:
        return source_skill
    raise ValueError(
        f"done_items got source_skill={source_skill!r}, which is not a surface "
        f"this verb renders on. It goes into the closure's evidence sentence "
        f"verbatim, so it may only be one of {sorted(allowed)} — the verb's "
        f"own taxonomy surfaces plus the dispatcher. Never pass free text.")


def _checked_now_iso(now_iso) -> str:
    """SF-2. `now_iso` lands verbatim in `attested_at` and is sliced into the
    evidence line. A non-string (a dict, a list, a number) wrote a malformed
    event AND interpolated itself into the sentence — the STAFFCUT round-1
    class, where a fix turned a silent drop into malformed substrate writes."""
    if now_iso is None:
        return _now_iso()
    if not isinstance(now_iso, str) or not now_iso.strip():
        raise ValueError(
            f"done_items got now_iso={now_iso!r} ({type(now_iso).__name__}); "
            "it is stamped on the closure as `attested_at` and read into the "
            "evidence sentence, so it must be an ISO timestamp string. Pass "
            "None to use the clock.")
    try:
        from event_time import parse_ts

        if parse_ts(now_iso) is None:
            raise ValueError("unparseable")
    except ValueError:
        raise ValueError(
            f"done_items could not read now_iso={now_iso!r} as a timestamp. "
            "An attestation records WHEN the user said it; an unreadable "
            "stamp is worse than no stamp.")
    except Exception:  # pragma: no cover — a missing helper must not widen
        raise ValueError(
            f"done_items could not validate now_iso={now_iso!r}")
    return now_iso


def done_items(workspace_root, ids, *, resolved_by: str,
               source_skill: str = SOURCE_SKILL,
               attested_ids=(), now_iso: str | None = None,
               source_ref=None) -> dict:
    """`already done` — the user attests they already did this one (DONE1).

    THE PROBLEM THIS FIXES. Until now the queue's only answers were confirm
    (it's real, carry it) and drop / not mine (close it as let-go). A CEO who
    keeps a promise off-mail — said it in a hallway, sent it from their phone,
    handed it over in person — had no honest answer, and `drop` was the one
    that felt closest. It is not close: `capture_gate._DISMISS_RESOLUTIONS`
    counts a `dropped` / `not_mine` closure as a DISMISSAL SIGNAL for that
    counterparty's org, and at enough of them the capture-tuning miner
    proposes an observed-only override for the whole org. So answering `drop`
    on kept promises does not merely undercount completions — it accumulates
    evidence for suppressing that counterparty's captures entirely. A Done
    writes `resolution="done"`, which is in no dismissal set, so the class
    stops accruing.

    TWO WRITES, IN THIS ORDER, per accepted id:
      a. `commitment_state.clear_review_flags` — the capture was real;
      b. `commitment_state.close_commitment(resolution="done",
         user_confirmed=True)` — and it was fulfilled.

    Both claims are true and the substrate should carry both, which also makes
    Done exactly "confirm, then close": it rides the confirm fence unmodified,
    and every `resolution="done"` closure in history stays uniform (confirmed
    before closing — no reader has to special-case a closed-but-never-confirmed
    item). The order is the RECOVERABLE one: if (b) fails after (a), the item
    is a confirmed OPEN commitment the user can close by ordinary means and the
    per-item result says so (`confirmed_not_closed`). The reverse order is not
    recoverable — `clear_review_flags` refuses a closed item, so a failure
    would strand a closed item that was never confirmed.

    THREE REFUSALS, in this order, and each is reported per item — never
    written, never silently downgraded to a confirm:

      1. `not_pending` — the id is not a current queue member (idempotent-safe,
         exactly as `confirm_items` is; re-running the same Done is a no-op
         with an honest ack, not a second tombstone).
      2. `held_weak_evidence` — THE shared fence
         (`watch_gate.screen_bulk_accept`), unchanged and unforked, over the
         same `{"id", "weak_reason"}` rows `confirm_items` builds.
      3. `not_individually_named` — the DONE1 gesture bar, caller-side and
         STRICTER than confirm: EVERY id must appear in `attested_ids`.
         `already done 7` and `already done 7, 9` work; `already done all`,
         `already done 1-40` and `already done the vendor call` are refused.
         A confirm asserts a fact about the workspace's hearing; an
         attestation asserts a fact about the user's own conduct, and the
         rubber-stamp failure the fence exists for (six commitments closed in
         two seconds on title-match evidence) is strictly worse when the claim
         is "I did these." A range is exactly that gesture's shape. Narrowing
         is reversible; un-shipping a bulk attestation is not.

    This bar narrows an ALREADY-SCREENED set caller-side. It changes nothing
    about `screen_bulk_accept`'s contract or its result for any other caller,
    and there is deliberately no `done_group` twin — naming a call names no row
    individually (`ids_for_group`), so a group Done could never populate
    `attested_ids` anyway.

    THE EVIDENCE IS THE ATTESTATION AND NOTHING ELSE (`DONE_ATTESTATION`) —
    who said it, and when and where. No match, no score, no synthesized
    sent-mail line. The closure also carries an additive `extra_data` stamp
    (`completion_basis: "user_attestation"`, `attested_at`,
    `attested_on_surface`) so no future reader can mistake an attested
    completion for an evidence-backed one; `close_commitment` never lets
    extra_data override its canonical keys.

    THE THREE INPUTS THAT REACH THE SUBSTRATE ARE VALIDATED FIRST, LOUDLY,
    BEFORE ANY WRITE (review SF-2/3/4). Every one of them lands in an event or
    in the evidence sentence, so an unchecked value is not a caller bug — it is
    a malformed or dishonest substrate write, the STAFFCUT round-1 class:

      `source_skill` must be a surface this verb actually renders on (derived
        from its own `verb_taxonomy` row) or the dispatcher that answers on a
        surface's behalf. It is interpolated into the evidence line, so an
        unchecked value defeats the honesty rule above with no code change at
        all — `source_skill="matched an outbound send, score 0.94"` would write
        exactly the fabricated match evidence this verb refuses to fabricate.
      `now_iso` must be a parseable timestamp string. It lands verbatim in
        `attested_at` and is sliced into the evidence line; a dict wrote
        `"attested_at": {"a": 1}` and interpolated it into the sentence.
      `resolved_by` must be non-empty. An attestation that says "you said at
        review" with nobody attributed is not an attestation. (Only here —
        `drop_items` has the same gap and it is pre-existing and out of scope;
        widening it belongs to its own change.)

    A bad value raises `ValueError` and NOTHING is written for ANY id in the
    call — the refusal is atomic, because a half-written batch on a malformed
    input is worse than the refusal.

    `OpenSubitemsError` / `CommitmentIdError` are handled exactly as
    `drop_items` handles them — reported per item with the writer's own
    message, never swallowed, never auto-cascaded.

    PROV1 — a Done is a HUMAN close with no message behind it, which is
    precisely the case §3.2 says must never be blocked: its pointer is the
    surface receipt (`session:<surface>:<now>`), derived from the same two
    checked values the evidence sentence is built from, so the pointer cannot
    disagree with the prose. A caller holding a truer receipt id passes
    `source_ref` and it wins.

    Returns {"results": [...], "n_done": int, "n_not_pending": int,
             "n_held": int, "n_refused": int, "n_failed": int}.
    """
    from commitment_state import (CommitmentIdError, OpenSubitemsError,
                                  clear_review_flags, close_commitment)
    from writer_lock import events_writer_lock

    source_skill = _checked_done_surface(source_skill)
    now_iso = _checked_now_iso(now_iso)
    if not isinstance(resolved_by, str) or not resolved_by.strip():
        raise ValueError(
            "done_items needs a resolved_by — an 'already done' is an "
            "attestation, and an attestation with nobody attributed is not "
            "one. Pass the primary user's person id (primary_user."
            "resolve_primary_user); if the workspace has no primary user on "
            "file, say so instead of closing the item.")
    pending = _pending_by_id(workspace_root)
    named = {str(x) for x in (attested_ids or ())}
    # TZDATE3 — localized via `tz.localize_date` so an attestation made
    # late-evening-local doesn't stamp tomorrow's UTC date into the record.
    evidence = f"{DONE_ATTESTATION} ({source_skill}, {_localize_date(str(now_iso), workspace_root)})"
    stamp = {
        "completion_basis": COMPLETION_BASIS_ATTESTATION,
        "attested_at": now_iso,
        "attested_on_surface": source_skill,
    }
    # PROV1 — the surface receipt for a human attestation. PROVMINT1 moved the
    # MINT into `commitment_state` (one helper, one home — this file used to
    # spell the string itself, and a shape spelled in three places is three
    # shapes waiting to drift). What stays here is the thing only this caller
    # knows: the gesture's own instant, already checked above, so the receipt
    # can never name a surface or a time the evidence sentence does not — and
    # so BOTH writes of this one gesture carry ONE receipt.

    # THE fence, over the rows this queue can actually answer — the same call
    # `confirm_items` makes, with the same row shape and the same override
    # vocabulary. An id that is not in the queue never reaches the screen.
    screenable = [cid for cid in (str(c) for c in (ids or []))
                  if cid in pending]
    screen = screen_bulk_accept(
        [{"id": cid, "weak_reason": _weak_reason(pending[cid])}
         for cid in screenable],
        individually_named=named,
    )
    # Deliberately NOT named `accepted`: `confirm_items` uses that name, and
    # the WATCHGATE M1b mutation pin anchors on ITS line
    # (`if cid not in accepted:`) and refuses to run when the anchor matches
    # more than once. A second identically-worded callsite would silently
    # disarm that pin — a mutation that cannot be applied is not a fence.
    # DONE1's own callsite is pinned by run_done1_mutation_test.py's D1.
    fence_accepted = set(screen["accept"])
    held_reason = {h["id"]: h["reason"] for h in screen["held"]}

    # REVIEW OBSERVED1 C4 — a rendered SET ASIDE row resolves to an id this
    # verb cannot honor (nothing was tracked, so there is nothing whose
    # completion can be attested). Refusing it BY NAME beats the generic
    # `not_pending`, which Step 3's ack reads as "already settled" — telling
    # the user a row the render just printed does not exist. Lazy (E1): the
    # index is a full-log scan and is built only when some id misses the
    # queue.
    observed: dict = {}
    if any(str(c) not in pending for c in (ids or [])):
        observed = _observed_by_id(workspace_root)

    results: list[dict] = []
    n_done = n_not_pending = n_held = n_refused = n_failed = 0
    for cid in ids or []:
        cid = str(cid)
        if cid not in pending and cid in observed:
            results.append({
                "commitment_id": cid,
                "status": "refused",
                "detail": "a set-aside item — heard between other people, "
                          "never tracked, so there is nothing to attest as "
                          "done. `confirm` starts tracking it; `drop` lets "
                          "it go.",
            })
            n_refused += 1
            continue
        if cid not in pending:
            results.append({"commitment_id": cid, "status": "not_pending"})
            n_not_pending += 1
            continue
        if cid not in fence_accepted:
            results.append({"commitment_id": cid,
                            "status": "held_weak_evidence",
                            "detail": held_reason.get(cid, "")})
            n_held += 1
            continue
        if cid not in named:
            results.append({
                "commitment_id": cid,
                "status": "not_individually_named",
                "detail": "say it's done one row at a time — name that row's "
                          "own number. `all`, a range and a call name nothing "
                          "individually, and this answer says you did the "
                          "work.",
            })
            n_refused += 1
            continue
        # SF-8 — ONE outer lock span over the confirm+close PAIR. Both writers
        # take the same reentrant lock internally, so this costs a depth
        # increment and closes the window where another writer could land
        # between (a) and (b) and see a confirmed-but-unclosed item that was
        # mid-gesture. The per-item failure contract is unchanged: the `with`
        # unwinds on either leg's exception and (a) stays on disk, which is the
        # recoverable half.
        with events_writer_lock(_events_path(Path(workspace_root)),
                                holder=f"done_items:{source_skill}"):
            try:
                confirmed = clear_review_flags(
                    workspace_root, cid, cleared_by=resolved_by,
                    source_skill=source_skill, note=DONE_CONFIRM_NOTE,
                    # PROVMINT1 §0-2 — the confirm leg of a Done is half of one
                    # gesture and owes the same pointer the close leg carries.
                    # This write is the one that produced the walk's six
                    # unmarked `commitment_updated` events.
                    source_ref=source_ref, mint_now_iso=now_iso,
                )
            except CommitmentIdError as exc:
                results.append({"commitment_id": cid, "status": "not_found",
                                "detail": str(exc)})
                n_failed += 1
                continue
            if confirmed.get("status") != "cleared":
                # Not open any more between the read and the write — report the
                # writer's own verdict rather than closing something blind.
                results.append(
                    {"commitment_id": confirmed.get("commitment_id", cid),
                     "status": confirmed.get("status")})
                n_not_pending += 1
                continue
            try:
                res = close_commitment(
                    workspace_root, cid, resolved_by=resolved_by,
                    evidence=evidence, source_skill=source_skill,
                    resolution="done", user_confirmed=True,
                    extra_data=dict(stamp),
                    source_ref=source_ref, mint_now_iso=now_iso,
                )
            except (CommitmentIdError, OpenSubitemsError) as exc:
                # (a) landed, (b) did not: the item is a confirmed OPEN
                # commitment and stays one. Nothing is lost, nothing half-closed.
                results.append({"commitment_id": cid,
                                "status": "confirmed_not_closed",
                                "detail": str(exc)})
                n_failed += 1
                continue
            except Exception as exc:  # pragma: no cover — same recoverable shape
                results.append({"commitment_id": cid,
                                "status": "confirmed_not_closed",
                                "detail": f"{type(exc).__name__}: {exc}"})
                n_failed += 1
                continue
        status = res.get("status")
        results.append({"commitment_id": res.get("commitment_id", cid),
                        "status": "done" if status == "closed" else status})
        if status == "closed":
            n_done += 1
        else:
            n_not_pending += 1
    return {"results": results, "n_done": n_done,
            "n_not_pending": n_not_pending, "n_held": n_held,
            "n_refused": n_refused, "n_failed": n_failed}


def not_mine_items(workspace_root, ids, *, resolved_by: str,
                   source_skill: str = SOURCE_SKILL, source_ref=None) -> dict:
    """`not mine` — the same closure `drop` writes, with the reason that says
    what actually happened: the capture was real, it just was not the user's.

    Reuses `drop_items` (one write path, one refusal contract) and the same
    evidence string the commitment-triage dispatch already uses for this verb.
    When the user NAMES the real owner, the caller routes to
    `commitment_state.reassign_commitment` instead — reassignment is a
    different question and this queue does not guess at it."""
    return drop_items(workspace_root, ids, resolved_by=resolved_by,
                      evidence=NOT_MINE_EVIDENCE, source_skill=source_skill,
                      source_ref=source_ref)


def drop_items(workspace_root, ids, *, resolved_by: str,
               evidence: str = DROP_EVIDENCE,
               source_skill: str = SOURCE_SKILL,
               source_ref=None) -> dict:
    """Drop unconfirmed extractions: closed with `resolution="dropped"`.

    Through `commitment_state.close_commitment` — THE closure path — with
    `user_confirmed=True`, because the user naming a row IS the explicit
    confirmation a pending_review item requires. NOTHING IS DELETED: a
    `commitment_resolved` event is appended and the original capture stays in
    history, readable forever.

    `already_resolved` is honored as a NO-OP ack (never a hand-built second
    tombstone — that is where the 83 duplicate resolve-on-resolve rows in the
    live history came from).

    QUEUE MEMBERS ONLY (BULKGUARD) — an id that is currently an OPEN,
    CONFIRMED commitment is REFUSED (`confirmed_open`), never closed. This
    writer serves the needs-your-call queue; closing confirmed work belongs
    to `log-resolution` / `close_commitment` callers with their own
    contracts. Ids that are already closed still flow through, so a re-drop
    keeps its honest `already_resolved` no-op ack.

    PROV1 — a drop is a human decision on a surface, so its pointer is that
    surface's receipt (`session:<surface>:<now>`), same posture and the same
    minted SHAPE as `done_items`. The time component is load-bearing, not
    decoration: without it every drop the surface ever performs carries one
    identical string, which resolves to nothing and still counts as
    `with_pointer` in `closure_index.pointer_coverage` — i.e. a constant
    inflates the very metric PROV1 exists to produce. Minted ONCE per call, so
    one gesture over many ids reads as one act (again as `done_items` does).
    A caller holding a truer receipt id passes `source_ref` and it wins.

    OBSERVED1 — an `obs_` id (a set-aside row) routes through
    `_drop_observed_row`: promote, then close the promoted row as dropped —
    the standard tombstone, so the drop also feeds the capture gate's
    dismissal tuning exactly as a queue drop does. A set-aside row promoted
    earlier and CONFIRMED since refuses (`confirmed_open`) like any other
    confirmed open item.

    Returns {"results": [...], "n_dropped": int, "n_already": int,
             "n_refused": int, "n_failed": int}.
    """
    from commitment_state import (AmbiguousTargetError, CommitmentIdError,
                                  OpenSubitemsError, close_commitment)
    from cru_match import load_open_commitments, split_pending_review

    ws = Path(workspace_root)
    # PROV1 — the instant of THIS drop gesture, read from the clock ONCE here so
    # the receipt names when the decision was made instead of being one string
    # every drop shares forever, and so one gesture over many ids reads as one
    # act. Same resolution as `done_items` (whole seconds), so two gestures
    # inside one second still collide — parity with the Done path, deliberately,
    # not an oversight. PROVMINT1: the receipt STRING is minted by
    # `commitment_state` (one helper, one home); what belongs here is the clock
    # read, which is the only part this surface knows.
    drop_mint_iso = _now_iso()
    confirmed, pending_rows = split_pending_review(load_open_commitments(
        str(_events_path(ws)), workspace_root=str(ws)))
    confirmed_open = {_commitment_id(ev) for ev in confirmed}
    pending_by_id = {_commitment_id(ev): ev for ev in pending_rows}
    pending_ids = set(pending_by_id)
    # OBSERVED1 — the set-aside rows this queue now also answers (same
    # routing rule as confirm_items: id membership; the id schemes cannot
    # collide). REVIEW E1 — the observed index is a second full-log scan, so
    # it is built only when some id is neither a queue member nor a
    # confirmed open item (i.e. could be a set-aside row or a closed id).
    observed: dict = {}
    if any(str(c) not in confirmed_open and str(c) not in pending_ids
           for c in (ids or [])):
        observed = _observed_by_id(workspace_root)
    results: list[dict] = []
    n_dropped = n_already = n_refused = n_failed = 0
    for cid in ids or []:
        cid = str(cid)
        if cid in observed:
            res = _drop_observed_row(
                workspace_root, observed[cid], resolved_by=resolved_by,
                evidence=evidence, source_skill=source_skill,
                source_ref=source_ref, mint_iso=drop_mint_iso,
                confirmed_open=confirmed_open)
            results.append(res)
            if res["status"] == "closed":
                n_dropped += 1
            elif res["status"] == "already_resolved":
                n_already += 1
            elif res["status"] == "confirmed_open":
                n_refused += 1
            else:
                n_failed += 1
            continue
        if cid in confirmed_open:
            results.append({
                "commitment_id": cid, "status": "confirmed_open",
                "detail": "an open confirmed commitment — this queue never "
                          "closes those; use the ordinary close path",
            })
            n_refused += 1
            continue
        try:
            res = close_commitment(
                workspace_root, cid, resolved_by=resolved_by,
                evidence=evidence, source_skill=source_skill,
                resolution="dropped", user_confirmed=True,
                source_ref=source_ref, mint_now_iso=drop_mint_iso,
            )
        except CommitmentIdError as exc:
            results.append({"commitment_id": cid, "status": "not_found",
                            "detail": str(exc)})
            n_failed += 1
            continue
        except AmbiguousTargetError as exc:
            # CLOSEID2 — a session-lane surface reaching this drop path has no
            # per-row door to state; the refusal is one row's, never the run's
            # (the same refuse-the-row contract close_commitments keeps).
            results.append({"commitment_id": cid, "status": "refused",
                            "detail": str(exc)})
            n_failed += 1
            continue
        except OpenSubitemsError as exc:
            # A parent with open children needs its own one-line confirm —
            # the queue never cascades silently.
            results.append({"commitment_id": cid, "status": "has_subitems",
                            "detail": str(exc)})
            n_failed += 1
            continue
        status = res.get("status")
        results.append({"commitment_id": res.get("commitment_id", cid),
                        "status": status})
        if status == "closed":
            n_dropped += 1
            # ATTRIB1-B D12 — a drop / not-mine on a MEETING capture (a row
            # that carries a typed basis) teaches the extractor one line.
            # Never fatal: the close already landed.
            _attribution_hint_on_drop(workspace_root, pending_by_id.get(cid),
                                      evidence)
        else:
            n_already += 1
    return {"results": results, "n_dropped": n_dropped,
            "n_already": n_already, "n_refused": n_refused,
            "n_failed": n_failed}


def _attribution_hint_on_drop(workspace_root, ev, evidence) -> bool:
    """D12 — one hint line for a dropped / not-mine meeting capture."""
    if not isinstance(ev, dict):
        return False
    d = ev.get("data") or {}
    attr = d.get("attribution") if isinstance(d.get("attribution"), dict) else None
    if not attr:
        return False
    try:
        from extraction_hints import append_attribution_hint
        return append_attribution_hint(
            workspace_root,
            verdict="not_mine" if evidence == NOT_MINE_EVIDENCE else "dropped",
            title=str(d.get("title") or ""),
            transcript_class=str(attr.get("transcript_class") or ""),
            kind=str(d.get("kind") or ""))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# UNCONFIRM1 — the two reversals
# ---------------------------------------------------------------------------
#
# A confirm and a Done are USER GESTURES, and a user gesture the user can't
# take back is a trap. Before this, the only additive writer that reversed a
# confirm was `flag_duplicate_for_review` — a duplicate-pair writer — so the
# live undo on 2026-08-03 went off-label through it with an EMPTY duplicate
# target: benign in the projection, permanently wrong on disk. These two
# wrappers route to the purpose-built writer instead, and they are the queue's
# own mirror of `confirm_items` / `done_items`: same per-item reporting, same
# refusal-not-exception contract, nothing ever deleted.

# WHAT THIS LIST IS DERIVED FROM (review SF-6 — the audit, not the inventory).
#
# The rule: a registered event type belongs here iff it can be appended AFTER a
# commitment's capture, targets that commitment through the closer/adjudication
# id chain, and either CHANGES what the projector reports for the item or
# RECORDS A USER DECISION about it. Anything matching that rule is a later
# decision, and an undo that steps over a later decision is not an undo.
#
# The audit is checkable rather than assertable: `_NON_TOUCH_TYPES` records
# every commitment-/thread-shaped registered type that was CONSIDERED and
# excluded, with the reason, and run_done1_test.py [9] asserts the two sets
# together cover the whole registered scope — so a newly registered type of
# that shape fails the suite instead of silently becoming invisible to the bar.
_TOUCH_TYPES = frozenset({
    "commitment_updated",            # due / wording / owner / review / watch
    "commitment_reassigned",         # routed to someone else
    "commitment_resolved",           # closed
    "commitment_reopened",           # reopened
    "commitment_superseded",         # merged away
    "commitment_reclassified",       # became a different kind of item
    "commitment_partial_received",   # a counterparty delivered
    "thread_resolved",               # the v2.7.13 batch-close path IS a closer
    "commitment_review_dismissed",   # the user skipped the review row (the
                                     # commitment stays open — still a decision)
    "chat_dismissal",                # muted / snoozed by target_id
})

# CONSIDERED AND EXCLUDED — with the reason each is not a touch. Keys are
# registered event types in the commitment/thread scope; the suite pins that
# `_TOUCH_TYPES | _NON_TOUCH_TYPES` covers that whole scope.
_NON_TOUCH_TYPES = {
    "commitment": "the capture itself — it precedes every confirm, and a "
                  "second capture is a different item",
    "commitment_observed": "the observed tier is a parallel record, not this "
                           "item's state",
    "commitment_noise_proposal": "a proposal about capture tuning; adjudicates "
                                 "nothing on this item",
    "commitment_review_proposed": "a proposal — the QUESTION, not an answer",
    "commitment_to_discuss": "mints a NEW list item pointing back at this one; "
                             "the projector reports nothing different here",
    "thread_created": "thread lifecycle, not a commitment adjudication",
    "thread_updated": "thread lifecycle, not a commitment adjudication",
    "thread_repaired": "a substrate repair on a thread record",
    "thread_resurrected": "thread lifecycle, not a commitment adjudication",
    # SPEC THREADANN1 — a derived subject label on a thread; it references
    # no commitment id/seq and adjudicates nothing about a needs-your-call
    # item.
    "thread_annotation": "a derived subject-cluster label on a thread, not "
                         "a commitment adjudication",
    # A confirmed thread split's summary receipt; the actual per-event moves
    # are `reclassification` events (already outside this scope — not
    # thread-/commitment-prefixed), which touch a THREAD's ownership, never
    # a commitment's confirm/review state.
    "thread_split_executed": "a thread-split receipt naming child threads, "
                             "not a commitment adjudication",
}


def _event_targets(ev: dict, cid: str, seq) -> bool:
    """Does this event reference THAT commitment?

    MIRRORS `commitment_state._closer_target_id` / `_closer_target_seqs` leg
    for leg, because that pair is what the loader treats as "closes this item"
    — a scan that sees fewer spellings than the closers write is a bar with
    holes in it, and the holes are invisible (a missed touch reads exactly like
    no touch). Three families:

      * the four `data` legs, in the closer chain's own order;
      * the three TOP-LEVEL legs — `ev["commitment_id"]`, `ev["thread_id"]`,
        `ev["id"]` — which the closer chain checks and the first draft of this
        function did not, so a touch spelled only at top level was missed;
      * the F3 seq aliases, plus every LEGACY ID SPELLING
        (`86`, `"86"`, `"seq_86"`, `"event_086"`, `"commitment_seq_86"`) that
        `normalize_commitment_id` resolves — a legacy closure names its target
        that way and would otherwise compare unequal to the canonical id.
    """
    try:
        from commitment_state import _LEGACY_SEQ_ID_RE
    except Exception:  # pragma: no cover — never widen on an import failure
        _LEGACY_SEQ_ID_RE = None

    def _hit(value) -> bool:
        if isinstance(value, bool) or value is None:
            return False
        if isinstance(value, int):
            # A bare int target is a seq alias (`data.commitment_id: 86`).
            return seq is not None and value == seq
        if not isinstance(value, str):
            return False
        v = value.strip()
        if not v:
            return False
        if v == cid:
            return True
        if seq is None or _LEGACY_SEQ_ID_RE is None:
            return False
        m = _LEGACY_SEQ_ID_RE.match(v)
        return bool(m) and int(m.group(1)) == seq

    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for key in ("commitment_id", "thread_id", "id", "target_id"):
        if _hit(d.get(key)):
            return True
    for key in ("commitment_id", "thread_id", "id"):
        if _hit(ev.get(key)):
            return True
    if seq is None:
        return False
    for key in ("commitment_seq", "source_event_seq", "target_seq"):
        sv = d.get(key)
        if isinstance(sv, bool):
            continue
        if isinstance(sv, str) and sv.strip().isdigit():
            sv = int(sv.strip())
        if isinstance(sv, int) and sv == seq:
            return True
    return False


def _touch_phrase(ev: dict) -> str:
    """What happened to this item after the confirm, in plain words. Never an
    event type name, never a field name — the refusal is read by the person
    who just said `undo`."""
    t = ev.get("type")
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    if t == "commitment_reassigned":
        return "it was reassigned to someone else after you confirmed it"
    if t == "commitment_resolved":
        return "it was closed after you confirmed it"
    if t == "commitment_reopened":
        return "it was reopened after you confirmed it"
    if t == "commitment_superseded":
        return "it was merged into another item after you confirmed it"
    if t == "commitment_reclassified":
        return "it was changed to a different kind of item after you confirmed it"
    if t == "commitment_partial_received":
        return "a delivery was recorded against it after you confirmed it"
    if t == "thread_resolved":
        return "it was closed from another surface after you confirmed it"
    if t == "commitment_review_dismissed":
        return "you skipped its review row after you confirmed it"
    if t == "chat_dismissal":
        return "it was muted or snoozed after you confirmed it"
    if d.get("review_flags_set"):
        return "it was flagged for review again after you confirmed it"
    if d.get("watch_set"):
        return "it was parked on watch after you confirmed it"
    if d.get("watch_cleared"):
        return "it was taken off watch after you confirmed it"
    if d.get("new_title") or d.get("new_summary"):
        return "its wording was corrected after you confirmed it"
    if d.get("new_due") or d.get("due") or d.get("due_date"):
        return "its due date was changed after you confirmed it"
    if d.get("owner_confirmed"):
        return "its owner was claimed after you confirmed it"
    return "it was changed after you confirmed it"


def _is_system_question(event_type: str, data: dict) -> bool:
    """True for an event that is only the SYSTEM SPEAKING, never a decision:
    a `commitment_updated` that is the SPEC OVERDUE1 overdue-ask mark
    (`asked_set` / `asked_cleared`), or — REVIEW_MERGED_v5280 F-2 — a
    `commitment_review_dismissed` whose reason is in
    `NON_DISMISSAL_RESOLUTION_REASONS` (the review-expiry chip leg
    withdrawing its own evidence line; the dangling drain's tombstone).

    The independent-touch bar exists so an undo cannot silently step over
    somebody ELSE'S later decision. That reasoning is about a decision. An
    overdue ask is not a decision and is not a touch: nobody edited the item,
    nobody re-routed it, nobody adjudicated it — the evening surface asked a
    question and is still waiting for the answer. Counting it would refuse the
    user's undo of THEIR OWN confirm because the system spoke while they
    slept, which is the wrong direction for a bar whose whole purpose is to
    protect the user's intent (REVIEW OVERDUE1 F-1, coordinator's ruling).

    The path this closes is one confirm plus one night, not an exotic race: a
    capture arrives unconfirmed with a due date already days past; the user
    confirms it, which is precisely what makes it eligible for the evening
    block; that night's fire asks about it; the next morning `undo confirm`
    was refused as `touched_since_confirm`.

    DELIBERATELY NARROW, and it does NOT touch the watch precedent. A watch
    park is a person parking an item — a decision, and it stays a touch. So
    does an ask that rides along with any real change: this returns False the
    moment the same event carries a substantive key, because then the item
    really did move and the mark is incidental to it. `_TOUCH_TYPES` is
    unchanged (removing `commitment_updated` from it would blind the bar to
    every genuine edit), so `_NON_TOUCH_TYPES` and the coverage pin over the
    two sets are unaffected — this is a payload-level exemption inside the
    one function that reads the bar, exactly where the scope belongs.
    """
    d = data if isinstance(data, dict) else {}
    if event_type == "commitment_review_dismissed":
        # REVIEW_MERGED_v5280 F-2 — a SYSTEM dismissal is not a decision
        # either: the review-expiry job withdrawing its own chip
        # (`policy_retracted`) and the dangling drain's terminal tombstone
        # (`target_never_created`) both carry a reason inside
        # `NON_DISMISSAL_RESOLUTION_REASONS`, and refusing the customer's
        # undo of their OWN confirm with "you skipped its review row" over a
        # row the machine withdrew is the wrong direction for this bar. A
        # human's Skip carries no such reason and stays a touch.
        try:
            from event_types import is_non_dismissal_closure
        except Exception:  # pragma: no cover — never widen on an import failure
            return False
        return is_non_dismissal_closure(d)
    if event_type != "commitment_updated":
        return False
    if not (d.get("asked_set") or d.get("asked_cleared")):
        return False
    from commitment_activity import SUBSTANTIVE_UPDATE_KEYS
    return not any(d.get(k) not in (None, "", False)
                   for k in SUBSTANTIVE_UPDATE_KEYS)


def _confirm_touch_map(workspace_root, targets: dict) -> dict:
    """{cid: {"confirmed": bool, "touch": <plain sentence or "">}} for the
    THE INDEPENDENT-TOUCH BAR.

    `confirmed` says the item carries a `review_flags_cleared` adjudication at
    all (nothing to reverse otherwise). `touch` names the FIRST adjudicating or
    state event appended after the LATEST one — a reassignment, a watch park, a
    wording fix, a later close by another path.

    ONE payload-level exemption (SPEC OVERDUE1 F-1): a pure overdue-ask mark is
    skipped by `_is_system_question` before any target matching. The bar is
    about somebody else's later DECISION; the system asking a question is not
    one, and refusing the user's own undo because the evening fire spoke
    overnight is the wrong direction. Read that helper for the full reasoning
    and for why the watch precedent is untouched.

    Reads through the shard-aware iterator, defensively: a broken log yields an
    empty map, and an empty map REFUSES every id (`not_confirmed`) rather than
    waving one through — an undo that cannot see the history must not write.
    """
    out = {cid: {"confirmed": False, "touch": ""} for cid in targets}
    anchor = {cid: -1 for cid in targets}
    later: dict = {cid: [] for cid in targets}
    try:
        from events_io import iter_events

        for idx, ev in enumerate(iter_events(Path(workspace_root))):
            if not isinstance(ev, dict):
                continue
            t = ev.get("type")
            if t not in _TOUCH_TYPES:
                continue
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if _is_system_question(t, d):
                continue
            for cid, seq in targets.items():
                if not _event_targets(ev, cid, seq):
                    continue
                if t == "commitment_updated" and d.get("review_flags_cleared"):
                    anchor[cid] = idx
                    out[cid]["confirmed"] = True
                else:
                    later[cid].append((idx, ev))
    except Exception:  # pragma: no cover — a broken log refuses, never writes
        return {cid: {"confirmed": False, "touch": ""} for cid in targets}
    for cid in targets:
        if anchor[cid] < 0:
            continue
        for idx, ev in later[cid]:
            if idx > anchor[cid]:
                out[cid]["touch"] = _touch_phrase(ev)
                break
    return out


# The three types the loader honors as CLOSERS (COMMITMENT_SCHEMA: "THE
# closure path" + the v2.7.13 thread_resolved path + C4 supersession).
_CLOSER_TYPES = ("commitment_resolved", "thread_resolved",
                 "commitment_superseded")


def _latest_closure_map(workspace_root, targets: dict) -> dict:
    """{cid: <the LAST closing event that named it>, or None}.

    MF-2's input: `undo_done_items` may only reverse a closure IT wrote, and
    the only thing on disk that says so is the `completion_basis` stamp
    `done_items` puts on its `commitment_resolved`. Latest-wins mirrors the
    projector, so a Done that was reopened and then dropped reads as a drop.

    Defensive like `_confirm_touch_map`: a broken log yields an empty map, and
    an empty map REFUSES every id rather than reopening something blind."""
    out: dict = {cid: None for cid in targets}
    try:
        from events_io import iter_events

        for ev in iter_events(Path(workspace_root)):
            if not isinstance(ev, dict) or ev.get("type") not in _CLOSER_TYPES:
                continue
            for cid, seq in targets.items():
                if _event_targets(ev, cid, seq):
                    out[cid] = ev
    except Exception:  # pragma: no cover — a broken log refuses, never writes
        return {cid: None for cid in targets}
    return out


def _resolve_targets(workspace_root, ids) -> tuple:
    """(index, {raw_id: canonical_id}, {raw_id: error}) — normalize the caller's
    ids through the SAME resolver the writers use, so a legacy spelling
    (`seq_86`, `event_086`) reverses exactly what it confirmed."""
    from commitment_state import (CommitmentIdError, _scan_commitment_index,
                                  normalize_commitment_id)

    index = _scan_commitment_index(
        Path(workspace_root) / "_hq" / "data" / "events.jsonl")
    canon: dict = {}
    errs: dict = {}
    for raw in ids or []:
        raw = str(raw)
        try:
            canon[raw] = normalize_commitment_id(raw, index)
        except CommitmentIdError as exc:
            errs[raw] = str(exc)
    return index, canon, errs


def undo_confirm_items(workspace_root, ids, *, restored_by: str,
                       source_skill: str = SOURCE_SKILL) -> dict:
    """Un-confirm: the item returns to this queue carrying its ORIGINAL reason.

    One `commitment_state.restore_review_flags` per id — the purpose-built
    writer, NOT `flag_duplicate_for_review` with an empty target. The confirm
    stays in history; the un-confirm is appended beside it.

    REPORTED, never written:
      `not_found`          — the id matches no commitment.
      `not_open`           — the item is closed (a Done? use `undo_done_items`).
      `already_unconfirmed`— it is already back in the queue; idempotent-safe.
      `not_confirmed`      — it was never confirmed from here, so there is
                             nothing to reverse.
      `touched_since_confirm` — somebody made a later decision about this item
                             and the refusal NAMES it. An undo that silently
                             steps over another decision is not an undo.

    Returns {"results": [...], "n_restored", "n_already", "n_refused",
             "n_failed"}.

    OBSERVED1 (REVIEW C3) — a cached `obs_` id reverses through the
    commitment its confirm minted: the id is translated to the promoted
    ref before resolution, so the ack's "say undo" promise holds for a
    set-aside confirm too. The undone item returns to the QUEUE as an
    unconfirmed row (promotion is permanent — the tier defines no
    un-promote), carrying the promotion's own review reason.
    """
    from commitment_state import (CommitmentIdError, _currently_closed,
                                  restore_review_flags)

    ids = [str(i) for i in (ids or [])]
    if ids:
        # An undo is a rare, user-typed gesture — the observed index's one
        # extra scan is acceptable here where it was not on the hot verbs.
        observed = _observed_by_id(workspace_root)
        ids = [(_promoted_commitment_ref(
                    workspace_root, _observed_promote_ref(observed[i])) or i)
               if i in observed else i
               for i in ids]

    index, canon, errs = _resolve_targets(workspace_root, ids)
    pending = set(_pending_by_id(workspace_root))
    targets: dict = {}
    for cid in canon.values():
        seq = (index["by_id"].get(cid) or {}).get("seq")
        targets[cid] = seq if isinstance(seq, int) and not isinstance(
            seq, bool) else None
    touch = _confirm_touch_map(workspace_root, targets)

    results: list[dict] = []
    n_restored = n_already = n_refused = n_failed = 0
    for raw in ids or []:
        raw = str(raw)
        if raw in errs:
            results.append({"commitment_id": raw, "status": "not_found",
                            "detail": errs[raw]})
            n_failed += 1
            continue
        cid = canon[raw]
        target = index["by_id"][cid]
        if _currently_closed(index, cid, target.get("seq")):
            results.append({
                "commitment_id": cid, "status": "not_open",
                "detail": "that one is closed — undoing an 'already done' "
                          "reopens it first",
            })
            n_refused += 1
            continue
        if cid in pending:
            results.append({"commitment_id": cid,
                            "status": "already_unconfirmed"})
            n_already += 1
            continue
        state = touch.get(cid) or {}
        if not state.get("confirmed"):
            results.append({
                "commitment_id": cid, "status": "not_confirmed",
                "detail": "nothing to take back — this one was never "
                          "confirmed from the queue",
            })
            n_refused += 1
            continue
        if state.get("touch"):
            results.append({"commitment_id": cid,
                            "status": "touched_since_confirm",
                            "detail": state["touch"]})
            n_refused += 1
            continue
        try:
            res = restore_review_flags(
                workspace_root, cid, restored_by=restored_by,
                source_skill=source_skill, note=UNCONFIRM_NOTE,
            )
        except CommitmentIdError as exc:  # pragma: no cover — resolved above
            results.append({"commitment_id": cid, "status": "not_found",
                            "detail": str(exc)})
            n_failed += 1
            continue
        status = res.get("status")
        results.append({"commitment_id": res.get("commitment_id", cid),
                        "status": status})
        if status == "restored":
            n_restored += 1
        else:
            n_refused += 1
    return {"results": results, "n_restored": n_restored,
            "n_already": n_already, "n_refused": n_refused,
            "n_failed": n_failed}


def undo_done_items(workspace_root, ids, *, restored_by: str,
                    source_skill: str = SOURCE_SKILL) -> dict:
    """Undo an `already done`: the item reopens AND returns to the queue
    unconfirmed — never a closed corpse, and never an open CONFIRMED item.

    TWO WRITES, AND THE ORDER IS FORCED:
      a. `commitment_state.reopen_commitment` — both review-flag writers refuse
         a closed item, so the reopen must land first;
      b. `commitment_state.restore_review_flags` — because a bare reopen leaves
         the Done's own `review_flags_cleared` standing as the latest
         adjudication, which yields an OPEN, CONFIRMED item: not the queue
         member the user had before they tapped. That is the closed-corpse
         blind spot's twin, and it is why this is a two-step.

    IT ONLY REVERSES CLOSURES IT OWNS (review MF-2). The latest closure on the
    item must carry `data.completion_basis == "user_attestation"` — the stamp
    `done_items` writes for exactly this purpose. Without that gate this
    function reopened ANYTHING closed: a `drop`, a reconcile-sent close on
    HIGH sent-mail evidence, a `mark done` from another surface. Reversing a
    decision the user never made here, on the strength of an id they typed, is
    the same class of over-reach the touch bar exists to prevent. A closure
    that is not an attested Done is REPORTED `not_a_done` and nothing is
    written.

    IDEMPOTENT (review SF-5), symmetrically with `undo_confirm_items`: an item
    that is already OPEN **and** back in the queue is `already_undone` — a
    no-op ack, no second `commitment_reopened`, no second un-confirm marker.
    (Redundant markers are the 83-duplicate-tombstone class.) An item that is
    open but still CONFIRMED — reopened by another path — is NOT idempotent-
    skipped: it still needs the re-flag, which is the spec's ratified
    `already_open` case.

    A reopen that FAILS aborts that id and reports — the item stays closed and
    the history stays clean.

    Both the Done's confirm and its closure stay in history. Returns
    {"results": [...], "n_undone", "n_already", "n_refused", "n_failed"}.
    """
    from commitment_state import (_currently_closed, reopen_commitment,
                                  restore_review_flags)
    from writer_lock import events_writer_lock

    index, canon, errs = _resolve_targets(workspace_root, ids)
    pending = set(_pending_by_id(workspace_root))
    targets: dict = {}
    for cid in canon.values():
        seq = (index["by_id"].get(cid) or {}).get("seq")
        targets[cid] = seq if isinstance(seq, int) and not isinstance(
            seq, bool) else None
    closures = _latest_closure_map(workspace_root, targets)

    # PROVMINT1 — this call IS one gesture, so its minted receipt is read from
    # the clock ONCE and handed to every write below. Minting per row would
    # hand the coverage metric N distinct pointers for one decision.
    _gesture_iso = _now_iso()
    results: list[dict] = []
    n_undone = n_already = n_refused = n_failed = 0
    for raw in ids or []:
        raw = str(raw)
        if raw in errs:
            results.append({"commitment_id": raw, "status": "not_found",
                            "detail": errs[raw]})
            n_failed += 1
            continue
        cid = canon[raw]
        target = index["by_id"][cid]
        is_closed = _currently_closed(index, cid, target.get("seq"))
        if not is_closed and cid in pending:
            # SF-5 — already in the state this call produces.
            results.append({"commitment_id": cid, "status": "already_undone"})
            n_already += 1
            continue
        closure = closures.get(cid)
        basis = ((closure or {}).get("data") or {}).get("completion_basis")
        if basis != COMPLETION_BASIS_ATTESTATION:
            results.append({
                "commitment_id": cid, "status": "not_a_done",
                "detail": "that one wasn't closed by an 'already done' — this "
                          "undo only reverses what you attested to here. A "
                          "drop or an ordinary close is reopened its own way.",
            })
            n_refused += 1
            continue
        # SF-8 — ONE outer lock span over the reopen+un-confirm PAIR, so no
        # other writer can observe the reopened-but-still-confirmed midpoint.
        with events_writer_lock(_events_path(Path(workspace_root)),
                                holder=f"undo_done:{source_skill}"):
            try:
                reopened = reopen_commitment(
                    workspace_root, cid, reopened_by=restored_by,
                    reason=UNDO_DONE_REASON, source_skill=source_skill,
                    mint_now_iso=_gesture_iso,   # PROVMINT1 — one undo, one receipt
                )
            except Exception as exc:
                # Includes CommitmentIdError. The item stays CLOSED and nothing
                # else is written for it — a half-undo is worse than none.
                results.append({"commitment_id": cid, "status": "not_reopened",
                                "detail": f"{type(exc).__name__}: {exc}"})
                n_failed += 1
                continue
            try:
                restored = restore_review_flags(
                    workspace_root, cid, restored_by=restored_by,
                    source_skill=source_skill, note=UNCONFIRM_NOTE,
                )
            except Exception as exc:  # pragma: no cover — reopened above
                results.append({"commitment_id": cid,
                                "status": "reopened_only",
                                "detail": f"{type(exc).__name__}: {exc}"})
                n_failed += 1
                continue
        if restored.get("status") != "restored":
            results.append({"commitment_id": cid, "status": "reopened_only",
                            "detail": str(restored.get("status"))})
            n_failed += 1
            continue
        results.append({"commitment_id": cid, "status": "undone",
                        "reopen": reopened.get("status")})
        n_undone += 1
    return {"results": results, "n_undone": n_undone, "n_already": n_already,
            "n_refused": n_refused, "n_failed": n_failed}


# ---------------------------------------------------------------------------
# The SHARED per-meeting renderer (CAPTUREFLOW §B + §C)
# ---------------------------------------------------------------------------
#
# ONE grouping implementation, ONE row shape, TWO consumers: the on-demand
# needs-your-call widget and the staff-meeting fold's section. Both are built
# from `build_queue_view(..., group_by="meeting")` and both dispatch their
# answers through `confirm_items` / `drop_items` — the same shared fence, one
# write path, never a per-surface fork (the fence-tests-the-helper gotcha: a
# second renderer would be a second idea of what a row says).


def _row_context_tag(row: dict, *, meeting_label: str = "") -> str:
    """The one context line under a row's name: how old, when due, what the row
    RESTS ON (WATCHGATE §2.1 `strength_line`, never a score), and WHY IT IS
    HERE.

    The last clause is not decoration. Re-verify 2026-08-01: both widget
    surfaces — the on-demand queue's `build_queue_data_view` and the
    staff-meeting fold — built this tag WITHOUT the row's `review_reason`,
    while `render_text` printed it and two shipped skill texts asserted it
    ("its `FLOOR_*` reason printed on the row"; "it is printed under every
    row"). Since M's ruling routes below-floor captures here, that gap had a
    sharp edge: a `floor_gated` row rendered as
    `The record says: "we should circle back on that at some point"` — the
    hedge that GOT it gated, quoted back as the thing the row rests on, with
    nothing saying the admission gate had refused it. The strength line
    answers "is there source text?"; only the reason answers "why am I being
    asked?". Appended LAST so the group label stays the tag's head, which is
    what the surface tests key on."""
    from watch_gate import stamped_strength_note, strength_line

    bits = []
    if meeting_label:
        bits.append(meeting_label)
    age = row.get("age_days")
    if age is not None:
        bits.append("1 day old" if age == 1 else f"{age} days old")
    if row.get("due"):
        bits.append(f"due {row['due']}")
    bits.append(strength_line(row.get("weak_reason") or "",
                              evidence=row.get("evidence") or ""))
    # RIDERS (c) — the PRODUCER'S strength claim, if it made one. The
    # `strength_line` above answers "is there source text?" from the evidence
    # TEXT; a producer that knows the row came from a meeting the user was not
    # in knows something the text cannot say, and until this line nothing on
    # either surface showed it. "" for every unstamped row, so their tag is
    # byte-identical to before.
    stamp_note = stamped_strength_note(row)
    if stamp_note:
        bits.append(stamp_note)
    reason = str(row.get("review_reason") or "").strip()
    if reason:
        # Same wording as the text render, so one row reads the same in both.
        bits.append(f"why it's here: {reason}")
    return " · ".join(b for b in bits if b)


def person_candidate_offer(workspace_root, *, now_iso: str | None = None):
    """PERSONLOOP1 §0-3 — the queue's HEADER OFFER: the recurring names this
    queue is jammed behind, and the one-tap answers that unjam it.

    Returns `(section_or_None, header_line)`. Both are drop-empty: a
    workspace with no recurring unresolved name gets byte-identical output to
    before this landed.

    THIS IS A DELIBERATE REVERSAL of the stance recorded a few lines above in
    `_review_reason` — that the queue should not tell the CEO to add a
    contact. That stance was right about a SINGLE row: one unresolved name on
    one capture is noise, and asking about it is nagging. It was wrong about
    the aggregate, and the aggregate is what filled this queue: the same
    handful of names failed resolution meeting after meeting, and each
    failure minted more rows that nothing could ever drain. Recurrence plus
    propose-only is the taste guard — the question is only ever asked about a
    name the graph has now missed at least twice, and it is only ever a
    question.

    Any failure degrades to no offer rather than a broken queue: the queue's
    own job does not depend on this."""
    try:
        from person_candidates import (candidate_section, derive_candidates,
                                       header_offer)

        cands = derive_candidates(workspace_root, now_iso=now_iso)
        if not cands:
            return None, ""
        return candidate_section(workspace_root, candidates=cands), \
            header_offer(cands)
    except Exception as exc:  # pragma: no cover — the queue must still render
        sys.stderr.write(f"[needs_review_queue] candidate offer skipped: "
                         f"{exc}\n")
        return None, ""


def build_queue_data_view(view: dict, *, header: str | None = None,
                          candidate_section: dict | None = None) -> dict:
    """The meeting-grouped queue as a `render_and_persist` data view — one
    SECTION per call, rows carrying their WATCHGATE strength line and the
    one-tap verbs. Never hand-composed: this is the only place the rows are
    shaped for a widget.

    `candidate_section` (PERSONLOOP1) is the person-candidate offer, built by
    `person_candidate_offer`. It leads the page when present — the names in
    it are why the rest of the page is as long as it is — and is absent
    entirely otherwise."""
    sections = []
    if candidate_section and (candidate_section.get("items") or []):
        # HYGIENE9 (d2) — a candidate row's `n` is its WIRE id (`pcand:<hex>`,
        # what apply-choices dispatches on) and the shared renderer prints
        # `display_n` — falling back to `n` when a row has none. Candidate
        # rows arrive without one (the one row shape serves three surfaces;
        # the Staff Meeting card stamps its own), so on this surface the
        # wire id rendered as the visible row number: `pcand:53504c35d5f8.`
        # led the held queue through the whole v5.27.0 supervised test (A2,
        # B1.2, C). The visible label is `P1`, `P2`, … — lettered so it can
        # never collide with the commitment rows' `1..N`, which `confirm N`
        # resolves by number. The wire id itself is untouched.
        items = []
        for i, it in enumerate(candidate_section.get("items") or [], start=1):
            row = dict(it)
            row.setdefault("display_n", f"P{i}")
            items.append(row)
        sections.append({**candidate_section, "items": items})
    for group in view.get("groups") or []:
        items = []
        for row in group.get("items") or []:
            # CLUSTER1 — a folded row never becomes its own widget row: it
            # rides its survivor's read-only expand below, number kept.
            if row.get("folded_into"):
                continue
            item = {
                "n": row["commitment_id"],       # wire id, verbatim
                "display_n": row["display_n"],   # what the row SHOWS
                "name": row["title"],
                "context_tag": _row_context_tag(row),
                "data": {"id": row["commitment_id"]},
                "actions": list(QUEUE_ROW_ACTIONS),
            }
            cluster = row.get("cluster")
            if cluster:
                # ONE line per real-world item: the survivor carries the
                # fold count, the read-only expand, the widget-embedded ids
                # (the CLOSEID2 "id" door — dispatch reads data.folded_ids
                # verbatim, never resolves ids itself), and the one tap.
                from commitment_cluster import CLUSTER_ACTION, folded_line
                item["context_tag"] += (f" · +{cluster['n_folded']} folded "
                                        f"— the same real-world item")
                item["folded_rows"] = [
                    folded_line(f.get("display_n"),
                                f.get("title") or "(untitled)",
                                f.get("group") or "")
                    for f in cluster["folded"]]
                item["data"]["folded_ids"] = list(cluster["folded_ids"])
                item["actions"] = item["actions"] + [CLUSTER_ACTION]
            items.append(item)
        if items:
            # `count` alone — the shared renderer appends "(N)" to any titled
            # section carrying one, so a count baked into the title here
            # rendered twice: "A CALL ON JUN 2 (3) (3)". The renderer owns
            # that chrome; this producer only says what the number is (the
            # EXCH1 held-review rider's fix, applied to the site it was
            # copied from — REVIEW_PR57 F-1 sweep).
            sections.append({"title": group["name"],
                             "count": len(items), "items": items})
    # OBSERVED1 — the set-aside tier, ONE trailing section under the tier's
    # own banner (grouping-by-call matters for the queue's group answers,
    # which deliberately do not reach this tier — see `ids_for_group` — so
    # the widget keeps the tier visually one thing). Rows carry the tier's
    # OWN verb list: confirm/drop, defined once in `capture_gate` beside the
    # tier itself.
    if view.get("n_observed"):
        from capture_gate import OBSERVED_ROW_ACTIONS, OBSERVED_SECTION_TITLE
        obs_items = []
        for group in view.get("observed_groups") or []:
            for row in group.get("items") or []:
                obs_items.append({
                    "n": row["commitment_id"],
                    "display_n": row["display_n"],
                    "name": row["title"],
                    "context_tag": _row_context_tag(
                        row, meeting_label=group.get("name") or ""),
                    "data": {"id": row["commitment_id"]},
                    "actions": list(OBSERVED_ROW_ACTIONS),
                })
        if obs_items:
            # REVIEW_PR57 F-1 — `count` alone. This title baked "(N)" into a
            # string both shared renderers append "({count})" to, so the
            # section header rendered "SET ASIDE — … (2) (2)". Producer-side
            # fix, the e44a283c pattern: the renderer owns the count chrome.
            sections.append({
                "title": OBSERVED_SECTION_TITLE,
                "count": len(obs_items),
                "items": obs_items,
            })
    # REVIEW OBSERVED1 R3 — an observed-only view's counted header would
    # read "0 unconfirmed extractions from 0 calls" above a populated
    # section; the widget takes the same zero-lead the text render takes.
    default_header = view.get("header") or "Needs your call"
    if not view.get("total") and view.get("n_observed"):
        default_header = _observed_only_lead(view.get("n_observed") or 0)
    out = {
        "source_skill": SOURCE_SKILL,
        "header": header or default_header,
        "sections": sections,
    }
    # REVSCHED1 §3-1 — carried through so the widget path shows the same offer
    # the text path does, under the renderer's OWN optional field name
    # (`sub_header`, the line the card renderers already draw directly under
    # the header) rather than a key of this module's invention. Set only when
    # there is one: an empty value would put a blank sub-header band in every
    # render below the bar.
    if view.get("offer"):
        out["sub_header"] = view["offer"]
    return out


def paginate_groups(data_view: dict, *, page: int = 1,
                    max_rows: int | None = None) -> dict:
    """Slice a grouped data view into ONE page of WHOLE groups.

    A meeting group is one decision; splitting it across a page boundary asks
    half a question. So pages are packed by group: groups are added until the
    next one would exceed `max_rows`, and a single group LARGER than the
    budget gets a page to itself rather than being cut (a split group is a
    worse lie than a long page — the byte-fit inside the transport is the
    backstop, and it reports itself when it bites).

    Returns a shallow copy carrying only that page's sections, plus
    `group_pagination`: {page, total_pages, has_more, total_items,
    total_groups, groups_on_page, rows_on_page}."""
    if max_rows is None:
        from chat_output_renderer import DEFAULT_PAGE_SIZE
        max_rows = DEFAULT_PAGE_SIZE
    max_rows = max(1, int(max_rows))

    sections = list(data_view.get("sections") or [])
    pages: list[list] = []
    current: list = []
    n_rows = 0
    for sec in sections:
        count = len(sec.get("items") or [])
        if current and n_rows + count > max_rows:
            pages.append(current)
            current, n_rows = [], 0
        current.append(sec)
        n_rows += count
    if current:
        pages.append(current)
    if not pages:
        pages = [[]]

    total_pages = len(pages)
    requested = int(page)
    page = max(1, min(requested, total_pages))
    out = dict(data_view)
    out["sections"] = pages[page - 1]
    pagination = {
        "page": page,
        "total_pages": total_pages,
        "has_more": page < total_pages,
        "total_items": sum(len(s.get("items") or []) for s in sections),
        "total_groups": len(sections),
        "groups_on_page": len(pages[page - 1]),
        "rows_on_page": sum(len(s.get("items") or [])
                            for s in pages[page - 1]),
    }
    if requested != page:
        pagination["clamped"] = True
        pagination["requested_page"] = requested
    out["group_pagination"] = pagination
    return out


def render_queue_page(workspace_root, *, page: int = 1,
                      persist_dir=None, now_iso: str | None = None,
                      max_rows: int | None = None) -> dict:
    """Build the meeting-grouped queue and render ONE page through the
    canonical transport. The skill's single call.

    Group-aware paging happens FIRST (whole groups only), and the resulting
    page is then handed to `widget_transport.render_and_persist` with an
    EXPLICIT `page` + `page_size` — an unpaginated call skips the 40KB
    byte-budget fit, which is how an over-budget widget reaches the relay.
    `page_size` is the page's own row count, so the transport re-slices
    nothing; if its byte fit shrinks the page anyway (one enormous call), the
    returned pagination carries `group_split_by_budget` and the caller says so
    rather than presenting a cut group as a whole one.

    Returns the transport dict plus `group_pagination` and `view`."""
    from widget_transport import render_and_persist

    ws = Path(workspace_root)
    view = build_queue_view(ws, now_iso=now_iso, group_by=GROUP_MEETING)
    # PERSONLOOP1 — the header offer. Derived here (the widget path) rather
    # than inside `build_queue_view`, so every existing caller of the pure
    # read keeps its cost and its output unchanged.
    offer_section, offer_line = person_candidate_offer(ws, now_iso=now_iso)
    if offer_line:
        view["person_candidate_offer"] = offer_line
    data_view = build_queue_data_view(view,
                                      candidate_section=offer_section)
    page_view = paginate_groups(data_view, page=page, max_rows=max_rows)
    gp = page_view.pop("group_pagination")
    rows = max(1, gp["rows_on_page"])
    transport = render_and_persist(
        data_view=page_view, wrapper="fragment",
        persist_dir=str(persist_dir or (ws / "_hq" / ".system" / "widgets")),
        page=1, page_size=rows)
    fitted = (transport.get("pagination") or {}).get("total_pages") or 1
    if fitted > 1:
        gp = dict(gp)
        gp["group_split_by_budget"] = True
    transport["group_pagination"] = gp
    transport["view"] = view
    return transport


def staff_meeting_group_section(workspace_root, *, now_iso: str | None = None,
                                group_cap: int = STAFF_GROUP_CAP,
                                row_cap: int = STAFF_ROW_CAP,
                                view: dict | None = None) -> dict | None:
    """CAPTUREFLOW §C — the staff-meeting fold: ONE section rendering the same
    per-meeting groups as the on-demand queue, from the same builder.

    Returns a `build_card_view(extra_sections=[...])`-shaped section, or None
    when there is nothing to fold in (drop-empty, all the way up — an empty
    frame is never data).

    THE VOLUME GUARD. Whole calls only, OLDEST CALL FIRST, at most
    `group_cap` calls and `row_cap` rows — whichever binds first — and the
    honest full totals stay in the title with a pointer to the on-demand
    queue for the remainder. Oldest-first IS the rotation rule: the front of
    the queue is what the staff meeting shows, so no call can be suppressed
    forever; answering the ones on the page is what advances it. The one
    deliberate exception: a single call carrying more rows than the cap is
    shown WHOLE and is then the only group on the section — a split group
    asks half a question.

    The rows carry the SAME verbs as the on-demand queue — `QUEUE_ROW_ACTIONS`,
    the one list both render sites read — and are answered through the SAME
    writers: `confirm_items` / `done_items` (both through
    `watch_gate.screen_bulk_accept`, the one shared fence) / `drop_items` /
    `not_mine_items`, and reversed through the same `undo_confirm_items` /
    `undo_done_items`. One write path, never a per-surface fork."""
    ws = Path(workspace_root)
    view = view if view is not None else build_queue_view(
        ws, now_iso=now_iso, group_by=GROUP_MEETING)
    groups = [g for g in (view.get("groups") or []) if g.get("items")]
    if not groups:
        return None

    total_rows = view.get("total") or sum(len(g["items"]) for g in groups)

    shown: list[dict] = []
    n_rows = 0
    for group in groups[:max(1, int(group_cap))]:
        count = len(group["items"])
        if shown and n_rows + count > max(1, int(row_cap)):
            break
        shown.append(group)
        n_rows += count

    # CLUSTER1 — fold on this surface too (same view annotations, same
    # rules), but ONLY when the cluster's surviving line is itself on the
    # page: hiding a row behind a survivor the volume guard cut would hide
    # it behind nothing.
    shown_cids = {row["commitment_id"] for g in shown for row in g["items"]}
    n_folded_here = 0
    items: list[dict] = []
    for group in shown:
        for row in group["items"]:
            if row.get("folded_into") and row["folded_into"] in shown_cids:
                n_folded_here += 1
                continue
            item = {
                "n": row["commitment_id"],
                "name": row["title"],
                "context_tag": _row_context_tag(
                    row, meeting_label=group["name"]),
                "data": {"id": row["commitment_id"]},
                "actions": list(QUEUE_ROW_ACTIONS),
            }
            cluster = row.get("cluster")
            if cluster:
                from commitment_cluster import CLUSTER_ACTION, folded_line
                item["context_tag"] += (f" · +{cluster['n_folded']} folded "
                                        f"— the same real-world item")
                item["folded_rows"] = [
                    folded_line(f.get("display_n"),
                                f.get("title") or "(untitled)",
                                f.get("group") or "")
                    for f in cluster["folded"]]
                item["data"]["folded_ids"] = list(cluster["folded_ids"])
                item["actions"] = item["actions"] + [CLUSTER_ACTION]
            items.append(item)
    if not items:
        return None

    # SPEC CLUSTER1 §0-4 — the fold's headline number is the INFORMATION
    # count when the view clustered; the true row count stays in the title.
    # Byte-identical when nothing clustered.
    total_info = total_rows - int(view.get("n_folded") or 0)
    item_noun = "item" if total_rows == 1 else "items"
    # RIDERS1 item 4 — the same phrase the on-demand header uses. This title
    # used to count `total_groups`, which includes the not-from-a-meeting
    # bucket, so the fold said "47 calls" beside a header saying "46" for the
    # same queue — and the bucket is not a call in either sentence.
    if view.get("n_folded"):
        info_noun = "item" if total_info == 1 else "items"
        title = (f"{STAFF_SECTION_TITLE} ({total_info} {info_noun} covering "
                 f"{total_rows} captures, {source_count_phrase(groups)})")
    else:
        title = (f"{STAFF_SECTION_TITLE} ({total_rows} {item_noun}, "
                 f"{source_count_phrase(groups)})")
    if len(items) < total_info:
        title += (f" — showing {len(items)}; say `needs your call` for the "
                  f"rest")
    return {"title": title, "count": len(items), "items": items}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    # Windows pipes default to cp1252 and the output carries middots.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["view", "view-json"])
    ap.add_argument("workspace")
    ap.add_argument("--now", default=None, help="ISO now override (tests)")
    # CAPTUREFLOW §B: the surface renders grouped by SOURCE CALL. Counterparty
    # grouping stays reachable — it is what a chase-shaped answer wants — but
    # it is no longer what the queue shows first.
    ap.add_argument("--group-by", default=GROUP_MEETING, choices=list(GROUP_MODES))
    args = ap.parse_args(argv)

    view = build_queue_view(args.workspace, now_iso=args.now,
                            group_by=args.group_by)
    if args.command == "view-json":
        print(json.dumps(view, ensure_ascii=False))
    else:
        print(render_text(view))
    return 0


__all__ = [
    "build_queue_view",
    "render_text",
    "parse_selection",
    "individually_named",
    "ids_for_selection",
    "ids_for_group",
    "confirm_items",
    "confirm_group",
    "confirm_satisfied_reasons",
    "done_items",
    "drop_items",
    "not_mine_items",
    "undo_confirm_items",
    "undo_done_items",
    "build_queue_data_view",
    "person_candidate_offer",
    "paginate_groups",
    "render_queue_page",
    "staff_meeting_group_section",
    "source_count_phrase",
    "NOT_FROM_A_MEETING_PHRASE",
    "GROUP_MEETING",
    "GROUP_COUNTERPARTY",
    "GROUP_MODES",
    "QUEUE_ROW_ACTIONS",
    "DONE_ACTION",
    "DONE_ATTESTATION",
    "DONE_CONFIRM_NOTE",
    "COMPLETION_BASIS_ATTESTATION",
    "UNCONFIRM_NOTE",
    "UNDO_DONE_REASON",
    "STAFF_SECTION_TITLE",
    "STAFF_GROUP_CAP",
    "STAFF_ROW_CAP",
    "NO_COUNTERPARTY",
    "NOT_FROM_A_MEETING",
    "NOT_MINE_EVIDENCE",
    "EMPTY_TEXT",
]


if __name__ == "__main__":
    raise SystemExit(main())
