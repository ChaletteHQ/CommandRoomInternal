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

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

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


def _pretty_date(value) -> str:
    s = str(value or "").strip()[:10]
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
            date = _pretty_date(d.get("meeting_date") or ev.get("ts"))
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


def _meeting_group(ev: dict, index: dict) -> tuple:
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
                            or ev.get("ts"))
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
                     *, group_by: str = GROUP_COUNTERPARTY) -> dict:
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
    """
    from cru_match import _commitment_field, load_needs_review

    if group_by not in GROUP_MODES:
        raise ValueError(
            f"group_by must be one of {list(GROUP_MODES)}; got {group_by!r}")

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    items = load_needs_review(str(_events_path(ws)), workspace_root=str(ws))
    people = _people_by_id(ws)
    rr_cache: dict = {}

    by_meeting = group_by == GROUP_MEETING
    index = _meeting_index(ws) if by_meeting else {}
    buckets: dict[str, list] = {}
    labels: dict[str, str] = {}
    dates: dict[str, str] = {}
    for ev in items:
        if by_meeting:
            key, label, date = _meeting_group(ev, index)
            labels.setdefault(key, label)
            if date and not dates.get(key):
                dates[key] = date
        else:
            key = _counterparty_display(ev, people, ws)
            labels.setdefault(key, key)
        buckets.setdefault(key, []).append(ev)

    def _age_key(ev) -> tuple:
        age = _age_days(ev.get("ts") or "", now_iso)
        # Oldest first; an unparseable ts sorts last rather than pretending
        # to be brand new.
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

    display_n = 0
    n_weak = 0
    groups: list[dict] = []
    for key in ordered_keys:
        rows = []
        for ev in sorted(buckets[key], key=_age_key):
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
        group = {"name": labels.get(key, key), "count": len(rows),
                 "items": rows}
        if by_meeting:
            group["group_key"] = key
            group["date"] = dates.get(key, "")
        groups.append(group)

    total = display_n
    noun = "extraction" if total == 1 else "extractions"
    if by_meeting:
        header = (f"Needs your call — {total} unconfirmed {noun} "
                  f"from {source_count_phrase(groups)}")
    else:
        header = (f"Needs your call — {total} unconfirmed {noun}, "
                  f"grouped by counterparty")
    return {
        "source_skill": SOURCE_SKILL,
        "group_by": group_by,
        "header": header,
        "total": total,
        "n_weak": n_weak,
        "groups": groups,
    }


EMPTY_TEXT = ("Nothing needs your call — every captured item has been "
              "confirmed or dropped.")


def render_text(view: dict) -> str:
    """The scannable list the skill pastes to the user, verbatim.

    Numbered across the whole queue so a range answer ("confirm 1-12, drop
    13") is unambiguous, and grouped so the user can also answer by
    counterparty ("confirm all Acme rows")."""
    if not view.get("total"):
        return EMPTY_TEXT

    lines = [view["header"], ""]
    for group in view.get("groups") or []:
        lines.append(f"{group['name']} ({group['count']})")
        for row in group["items"]:
            bits = []
            age = row.get("age_days")
            if age is not None:
                bits.append("1 day old" if age == 1 else f"{age} days old")
            if row.get("due"):
                bits.append(f"due {row['due']}")
            if row.get("source_skill"):
                bits.append(f"from {row['source_skill']}")
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
        lines.append("")
    if view.get("group_by") == GROUP_MEETING:
        lines.append("Say `confirm 1-5` to keep them, `drop 6,7` to let them "
                     "go, `not mine 8` if it was someone else's, or name a "
                     "call to answer the whole group. Nothing changes until "
                     "you say so.")
    else:
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
    a loud ValueError for the same reason parse_selection refuses to guess."""
    by_n = {row["display_n"]: row["commitment_id"]
            for g in (view.get("groups") or []) for row in g["items"]}
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

    Returns {"results": [{commitment_id, status}, ...], "n_confirmed": int,
             "n_not_pending": int, "n_held": int, "n_failed": int}.
    """
    from commitment_state import CommitmentIdError, clear_review_flags

    pending = _pending_by_id(workspace_root)
    cleared_by = _resolve_user(workspace_root)
    results: list[dict] = []
    n_confirmed = n_not_pending = n_held = n_failed = 0

    # THE fence (WATCHGATE §2.2), over the rows this queue can actually
    # answer. An id that is not in the queue is reported first and never
    # reaches the screen — it has no evidence to weigh either way.
    screenable = [cid for cid in (str(c) for c in (ids or []))
                  if cid in pending]
    screen = screen_bulk_accept(
        [{"id": cid, "weak_reason": _weak_reason(pending[cid])}
         for cid in screenable],
        individually_named=confirm_weak_ids or (),
    )
    accepted = set(screen["accept"])
    held_reason = {h["id"]: h["reason"] for h in screen["held"]}

    for cid in ids or []:
        cid = str(cid)
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
                              source_skill: str = SOURCE_SKILL) -> dict:
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
    """
    from commitment_state import CommitmentIdError, clear_review_flags
    from cru_match import load_open_commitments

    events_path = _events_path(Path(workspace_root))
    opens = load_open_commitments(events_path, workspace_root=workspace_root)
    cleared_by = _resolve_user(workspace_root)
    results: list[dict] = []
    n_cleared = n_failed = 0
    for ev in opens:
        d = ev.get("data") or {}
        if not d.get("review_reason_auto_satisfied"):
            continue
        cid = _commitment_id(ev)
        try:
            res = clear_review_flags(
                workspace_root, cid, cleared_by=cleared_by,
                source_skill=source_skill,
                note=f"review reason satisfied — {str(d.get('review_reason') or '')[:120]}",
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
               attested_ids=(), now_iso: str | None = None) -> dict:
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
    evidence = f"{DONE_ATTESTATION} ({source_skill}, {str(now_iso)[:10]})"
    stamp = {
        "completion_basis": COMPLETION_BASIS_ATTESTATION,
        "attested_at": now_iso,
        "attested_on_surface": source_skill,
    }

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

    results: list[dict] = []
    n_done = n_not_pending = n_held = n_refused = n_failed = 0
    for cid in ids or []:
        cid = str(cid)
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
                   source_skill: str = SOURCE_SKILL) -> dict:
    """`not mine` — the same closure `drop` writes, with the reason that says
    what actually happened: the capture was real, it just was not the user's.

    Reuses `drop_items` (one write path, one refusal contract) and the same
    evidence string the commitment-triage dispatch already uses for this verb.
    When the user NAMES the real owner, the caller routes to
    `commitment_state.reassign_commitment` instead — reassignment is a
    different question and this queue does not guess at it."""
    return drop_items(workspace_root, ids, resolved_by=resolved_by,
                      evidence=NOT_MINE_EVIDENCE, source_skill=source_skill)


def drop_items(workspace_root, ids, *, resolved_by: str,
               evidence: str = DROP_EVIDENCE,
               source_skill: str = SOURCE_SKILL) -> dict:
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

    Returns {"results": [...], "n_dropped": int, "n_already": int,
             "n_refused": int, "n_failed": int}.
    """
    from commitment_state import (CommitmentIdError, OpenSubitemsError,
                                  close_commitment)
    from cru_match import load_open_commitments, split_pending_review

    ws = Path(workspace_root)
    confirmed_open = {
        _commitment_id(ev)
        for ev in split_pending_review(load_open_commitments(
            str(_events_path(ws)), workspace_root=str(ws)))[0]
    }
    results: list[dict] = []
    n_dropped = n_already = n_refused = n_failed = 0
    for cid in ids or []:
        cid = str(cid)
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
            )
        except CommitmentIdError as exc:
            results.append({"commitment_id": cid, "status": "not_found",
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
        else:
            n_already += 1
    return {"results": results, "n_dropped": n_dropped,
            "n_already": n_already, "n_refused": n_refused,
            "n_failed": n_failed}


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


def _confirm_touch_map(workspace_root, targets: dict) -> dict:
    """{cid: {"confirmed": bool, "touch": <plain sentence or "">}} for the
    THE INDEPENDENT-TOUCH BAR.

    `confirmed` says the item carries a `review_flags_cleared` adjudication at
    all (nothing to reverse otherwise). `touch` names the FIRST adjudicating or
    state event appended after the LATEST one — a reassignment, a watch park, a
    wording fix, a later close by another path.

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
    """
    from commitment_state import (CommitmentIdError, _currently_closed,
                                  restore_review_flags)

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


def build_queue_data_view(view: dict, *, header: str | None = None) -> dict:
    """The meeting-grouped queue as a `render_and_persist` data view — one
    SECTION per call, rows carrying their WATCHGATE strength line and the
    one-tap verbs. Never hand-composed: this is the only place the rows are
    shaped for a widget."""
    sections = []
    for group in view.get("groups") or []:
        items = []
        for row in group.get("items") or []:
            items.append({
                "n": row["commitment_id"],       # wire id, verbatim
                "display_n": row["display_n"],   # what the row SHOWS
                "name": row["title"],
                "context_tag": _row_context_tag(row),
                "data": {"id": row["commitment_id"]},
                "actions": list(QUEUE_ROW_ACTIONS),
            })
        if items:
            sections.append({"title": f"{group['name']} ({len(items)})",
                             "count": len(items), "items": items})
    return {
        "source_skill": SOURCE_SKILL,
        "header": header or view.get("header") or "Needs your call",
        "sections": sections,
    }


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
    data_view = build_queue_data_view(view)
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

    items: list[dict] = []
    for group in shown:
        for row in group["items"]:
            items.append({
                "n": row["commitment_id"],
                "name": row["title"],
                "context_tag": _row_context_tag(
                    row, meeting_label=group["name"]),
                "data": {"id": row["commitment_id"]},
                "actions": list(QUEUE_ROW_ACTIONS),
            })
    if not items:
        return None

    item_noun = "item" if total_rows == 1 else "items"
    # RIDERS1 item 4 — the same phrase the on-demand header uses. This title
    # used to count `total_groups`, which includes the not-from-a-meeting
    # bucket, so the fold said "47 calls" beside a header saying "46" for the
    # same queue — and the bucket is not a call in either sentence.
    title = (f"{STAFF_SECTION_TITLE} ({total_rows} {item_noun}, "
             f"{source_count_phrase(groups)})")
    if len(items) < total_rows:
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
