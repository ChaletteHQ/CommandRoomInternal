#!/usr/bin/env python3
"""GRANOLA1 — meeting discovery, attendance classification, meeting-level
dedup, and the non-attendee lane running in SHADOW MODE.

WHAT THIS IS FOR
----------------
Team meetings happen without the user — a teammate's client call, a shared
workspace session — and everything decided or promised in them is invisible to
the brain. The existing past-meetings pass is tuned for meetings the user
ATTENDED, where party-only scoping and book routing make sense; pointing it at
other people's meetings unchanged would put other people's homework on the
user's book at scale (the exact defect class CAPTUREFLOW closed).

So this module adds three things to the EXISTING past-meetings fire — no new
scheduled task, no new surface:

  §A   `classify_attendance` — attended when the backend's involvement flags
       say the user captured the note OR is listed as a participant;
       non_attendee otherwise. **Uncertain is non_attendee** (the conservative
       direction: nothing auto-enters the book from that lane, and participant
       metadata is incomplete by the backend's own admission).
  §A2  `dedup_meetings` — dedup on the underlying MEETING, not the document.
       One real meeting can produce several notes (the user's own, plus a
       teammate's note of the same meeting shared into a team space), each with
       its own document id, so document-id dedup alone double-captures every
       shared meeting. Key: the calendar event id when the backend exposes one,
       else normalized title + start within a tolerance window + participant
       overlap. A recurring series must NOT dedup across occurrences.
  §B   `shadow_route_meeting` / `run_shadow_pass` — the non-attendee lane, with
       routing inverted by provenance (observed / review / close-proposal, and
       NEVER the book) — running behind `shadow_fence`, which withholds every
       candidate event so the lane measures itself and writes nothing.

SHADOW MODE IS THE WHOLE SCOPE OF THIS BUILD.
---------------------------------------------
`SHADOW_MODE` below is the dial. While it is on, `shadow_fence` returns an
empty list for every candidate and the lane's one append callsite therefore
appends nothing: the lane produces a per-run REPORT (counts by classification
and by would-be tier) and zero substrate events. Turning it off is a SEPARATE,
later decision gated on that report and on the capture-load re-measure — it is
NOT this build's scope and must not be flipped as a side effect of any other
change.

CONNECTOR SEAM
--------------
Nothing here names a transcript-backend tool. The caller hands in meeting
records it already fetched from the DECLARED transcript backend
(`tool_discovery.discover_transcript_tool`), and `normalize_meeting` tolerates
the field spellings backends actually use. `SOURCE_REF_PREFIX` is the
substrate's existing ref NAMESPACE for transcript captures (the same
`granola:<id>` refs every meeting event on disk already carries), not a tool
name, and callers can override it per workspace.

TIMEZONES
---------
Every timestamp this module RENDERS goes through `tz.to_local()` — upstream
meeting times are never trusted as already-local (workspace convention). The
comparisons it does internally are instant-vs-instant in UTC, which is
timezone-independent by construction.

House convention: construction/measurement only. The one write callsite is
`_emit`, and while shadow mode is on it has nothing to write.
"""
from __future__ import annotations
try:
    from text_clip import clip  # noqa: E402
except ImportError:  # pragma: no cover — direct-path fallback
    import sys as _sys_tc
    from pathlib import Path as _Path_tc
    _sys_tc.path.insert(0, str(_Path_tc(__file__).resolve().parent))
    from text_clip import clip  # noqa: E402

import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:  # package-style import
    from capture_gate import (
        CaptureGateError,
        build_observed_event,
        carries_due_or_money,
        observed_from_commitment_event,
        workspace_capture_context,
        _name_matches,
    )
except ImportError:  # pragma: no cover — direct-path import (tests, one-liners)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from capture_gate import (  # noqa: F401
        CaptureGateError,
        build_observed_event,
        carries_due_or_money,
        observed_from_commitment_event,
        workspace_capture_context,
        _name_matches,
    )


# ---------------------------------------------------------------------------
# §A — attendance classification
# ---------------------------------------------------------------------------

CLASS_ATTENDED = "attended"
CLASS_NON_ATTENDEE = "non_attendee"

REASON_CAPTURED_BY_USER = "the user captured the note"
REASON_LISTED_PARTICIPANT = "the user is listed as a participant"
REASON_NO_INVOLVEMENT = "neither involvement flag is set"
REASON_UNPROVEN = "attendance could not be proven from the metadata"

# The substrate's existing ref namespace for transcript captures. NOT a tool
# name — every meeting event already on disk carries `granola:<id>` refs and
# the dedup index has to match them. Override per workspace if the declared
# transcript backend writes a different namespace.
SOURCE_REF_PREFIX = "granola"

_DOC_ID_KEYS = ("doc_id", "document_id", "note_id", "meeting_id", "id")
_CAL_ID_KEYS = ("calendar_event_id", "calendar_id", "gcal_event_id",
                "event_id", "ical_uid")
_TITLE_KEYS = ("title", "name", "subject", "meeting_title")
_START_KEYS = ("start", "start_time", "started_at", "starts_at", "start_ts",
               "scheduled_start", "meeting_start")
_PARTICIPANT_KEYS = ("participants", "attendees", "people", "invitees",
                     "participant_emails")
_CAPTURED_KEYS = ("captured_by_me", "captured_by_user", "is_note_owner")
_PARTICIPANT_FLAG_KEYS = ("listed_as_participant", "is_participant",
                          "user_is_participant")
_NOTE_OWNER_KEYS = ("note_owner", "owner", "captured_by", "creator",
                    "owner_name")

_WS_RE = re.compile(r"\s+")
_NONWORD_RE = re.compile(r"[^\w\s]+")


def _first(raw: dict, keys) -> Any:
    for k in keys:
        if k in raw and raw[k] not in (None, ""):
            return raw[k]
    return None


def _tri_state(raw: dict, keys) -> Optional[bool]:
    """True / False / None — None means the backend did not say, which is a
    DIFFERENT thing from saying no and is what `certain` reports on."""
    for k in keys:
        if k in raw and raw[k] is not None:
            return bool(raw[k])
    return None


def _participant_token(value) -> str:
    if isinstance(value, dict):
        value = (value.get("email") or value.get("id")
                 or value.get("name") or value.get("display_name") or "")
    return str(value or "").strip().lower()


def normalize_title(value) -> str:
    """Lowercased, punctuation-stripped, whitespace-collapsed title — the
    fuzzy key's text half."""
    text = _NONWORD_RE.sub(" ", str(value or "").lower())
    return _WS_RE.sub(" ", text).strip()


def normalize_meeting(raw, *, ref_prefix: str = SOURCE_REF_PREFIX) -> dict:
    """One backend meeting record → the shape this module reads.

    Backend-agnostic by field-name tolerance: whatever the declared transcript
    backend calls its id / title / start / participants, the caller can hand
    the record straight through. An already-normalized record passes through
    unchanged (idempotent)."""
    if isinstance(raw, dict) and raw.get("_normalized") is True:
        return raw
    raw = dict(raw or {})
    doc_id = str(_first(raw, _DOC_ID_KEYS) or "").strip()
    participants = _first(raw, _PARTICIPANT_KEYS) or []
    if isinstance(participants, (str, bytes)):
        participants = [participants]
    tokens = tuple(sorted({t for t in (_participant_token(p)
                                       for p in participants) if t}))
    source_ref = str(raw.get("source_ref") or "").strip()
    if not source_ref and doc_id:
        source_ref = f"{ref_prefix}:{doc_id}"
    return {
        "_normalized": True,
        "doc_id": doc_id,
        "source_ref": source_ref,
        "calendar_event_id": str(_first(raw, _CAL_ID_KEYS) or "").strip(),
        "title": str(_first(raw, _TITLE_KEYS) or "").strip(),
        "title_key": normalize_title(_first(raw, _TITLE_KEYS)),
        "start": _first(raw, _START_KEYS),
        "participants": tokens,
        "captured_by_me": _tri_state(raw, _CAPTURED_KEYS),
        "listed_as_participant": _tri_state(raw, _PARTICIPANT_FLAG_KEYS),
        "note_owner": str(_first(raw, _NOTE_OWNER_KEYS) or "").strip(),
        "person_ids": tuple(p for p in (raw.get("person_ids") or ()) if p),
    }


def classify_attendance(meeting) -> dict:
    """`{"classification", "reason", "certain"}` for ONE meeting.

    attended  = `captured_by_me` OR `listed_as_participant` (the OR is the
                point: a teammate can capture a meeting the user attended, and
                the user can capture one he skipped — neither flag alone is
                attendance).
    non_attendee = anything else, INCLUDING "the backend did not say".

    `certain` is False whenever the verdict rests on missing metadata rather
    than on a flag the backend actually set. It never changes the routing —
    uncertain is non_attendee on purpose — it makes the posture measurable.

    NOTE (M clarification): how the note ARRIVED is not evidence. A meeting
    shared into a team space is classified by its involvement flags exactly
    like any other; many shared notes are of meetings the user did attend."""
    m = normalize_meeting(meeting)
    captured = m.get("captured_by_me")
    participant = m.get("listed_as_participant")
    if captured is True:
        return {"classification": CLASS_ATTENDED,
                "reason": REASON_CAPTURED_BY_USER, "certain": True}
    if participant is True:
        return {"classification": CLASS_ATTENDED,
                "reason": REASON_LISTED_PARTICIPANT, "certain": True}
    if captured is None and participant is None:
        return {"classification": CLASS_NON_ATTENDEE,
                "reason": REASON_UNPROVEN, "certain": False}
    if captured is None or participant is None:
        # One flag said no, the other said nothing — still unproven.
        return {"classification": CLASS_NON_ATTENDEE,
                "reason": REASON_UNPROVEN, "certain": False}
    return {"classification": CLASS_NON_ATTENDEE,
            "reason": REASON_NO_INVOLVEMENT, "certain": True}


# ---------------------------------------------------------------------------
# §A2 — meeting-level dedup
# ---------------------------------------------------------------------------

# How far apart two notes of the SAME meeting may claim to start. Small on
# purpose: it is the only thing separating "two notes of one meeting" from
# "two occurrences of a recurring series", and a series repeats at the same
# clock time on a different DAY, which is far outside any sane tolerance.
DEDUP_TOLERANCE_MIN = 30

BASIS_CALENDAR_ID = "calendar event id"
BASIS_FUZZY = "title + start within tolerance + participant overlap"
BASIS_FUZZY_NO_PARTICIPANTS = "title + start within tolerance (no participant metadata on one side)"

ACTION_PROCESS = "process"
ACTION_SKIP_DUPLICATE = "skip_duplicate"
ACTION_SKIP_PROCESSED = "skip_processed"


def _parse_instant(value) -> Optional[_dt.datetime]:
    """Any upstream time → an aware UTC datetime, or None. Naive strings are
    read as UTC (the transcript-backend convention `tz.to_local` documents)."""
    if value in (None, ""):
        return None
    if isinstance(value, _dt.datetime):
        dt = value
    else:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = _dt.datetime.fromisoformat(s)
        except ValueError:
            try:
                dt = _dt.datetime.fromisoformat(s[:19])
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def same_meeting(a, b, *, tolerance_minutes: int = DEDUP_TOLERANCE_MIN) -> dict:
    """Are these two records notes of the SAME real-world meeting?

    Returns `{"same": bool, "basis": str}`.

    Order of authority:
      1. Both sides carry a calendar event id → that answers it, both ways. Two
         DIFFERENT calendar ids are two different meetings even if everything
         else matches (this is what keeps a recurring series apart when the
         backend exposes ids).
      2. Otherwise the fuzzy key: identical normalized title AND starts within
         `tolerance_minutes` AND — when both sides list participants — at least
         one participant in common.

    A record with no start time never fuzzy-matches: a key that cannot place
    the meeting in time cannot tell one occurrence from the next."""
    a = normalize_meeting(a)
    b = normalize_meeting(b)
    if a.get("doc_id") and a.get("doc_id") == b.get("doc_id"):
        return {"same": True, "basis": "same document id"}
    cal_a, cal_b = a.get("calendar_event_id"), b.get("calendar_event_id")
    if cal_a and cal_b:
        return {"same": cal_a == cal_b, "basis": BASIS_CALENDAR_ID}
    if not a.get("title_key") or a["title_key"] != b.get("title_key"):
        return {"same": False, "basis": BASIS_FUZZY}
    start_a, start_b = _parse_instant(a.get("start")), _parse_instant(b.get("start"))
    if start_a is None or start_b is None:
        return {"same": False, "basis": BASIS_FUZZY}
    if abs((start_a - start_b).total_seconds()) > tolerance_minutes * 60:
        # Recurring series, or same title on another day: NOT the same meeting.
        return {"same": False, "basis": BASIS_FUZZY}
    pa, pb = set(a.get("participants") or ()), set(b.get("participants") or ())
    if pa and pb:
        return {"same": bool(pa & pb), "basis": BASIS_FUZZY}
    return {"same": True, "basis": BASIS_FUZZY_NO_PARTICIPANTS}


LEDGER_SHAPE_MEETINGS_LIST = "meetings-list"
LEDGER_SHAPE_BARE_LIST = "bare-list"
LEDGER_SHAPE_DOC_KEYED = "doc-id-keyed"
LEDGER_SHAPE_UNKNOWN = "unknown"

# The field names a processed-meetings row is recognized by. Used ONLY to tell
# a dict-of-rows apart from a dict that merely happens to nest dicts — never to
# require any particular field on a row.
_LEDGER_ROW_HINT_KEYS = frozenset(
    _TITLE_KEYS + _DOC_ID_KEYS + _START_KEYS
    + ("processedAtMs", "processed_at", "processed_at_ms", "summary",
       "files", "projects", "commitmentCount")
)


def _looks_like_ledger_row(value) -> bool:
    return isinstance(value, dict) and bool(
        _LEDGER_ROW_HINT_KEYS & set(value.keys()))


def _ledger_rows(raw) -> tuple:
    """`(rows, shape)` for whatever a processed-meetings ledger turned out to
    be. Three real shapes are tolerated; anything else degrades to no rows.

      1. `{"meetings": [row, ...]}` — the documented envelope.
      2. `[row, ...]` — a bare list.
      3. `{"<document id>": {row}, ...}` — **the shape M's live workspace
         actually carries** (8 rows, written 2026-06-14, fields
         `title / processedAtMs / summary / files / projects /
         commitmentCount`). The document id is the KEY, not a field, so the id
         has to be lifted onto the row or `normalize_meeting` produces an empty
         `source_ref` and the row is dropped by `_add`.

    Shape 3 deliberately contributes NO start time. `processedAtMs` is when the
    row was PROCESSED, not when the meeting began — the same distinction the
    events half draws for `meeting_processed` — so it must never seed the fuzzy
    key. These rows dedup by document id / source_ref only, which is exactly
    what a processed-ledger row is good for."""
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)], LEDGER_SHAPE_BARE_LIST
    if isinstance(raw, dict):
        envelope = raw.get("meetings")
        if isinstance(envelope, list):
            return ([r for r in envelope if isinstance(r, dict)],
                    LEDGER_SHAPE_MEETINGS_LIST)
        rows = []
        for key, value in raw.items():
            if not _looks_like_ledger_row(value):
                continue
            row = dict(value)
            if not row.get("doc_id"):
                row["doc_id"] = str(key)
            rows.append(row)
        if rows:
            return rows, LEDGER_SHAPE_DOC_KEYED
    return [], LEDGER_SHAPE_UNKNOWN


def processed_index(workspace_root=None, *, events_path=None,
                    ledger_path=None) -> List[dict]:
    """The already-processed meetings, as normalized records.

    TWO sources, both READ-ONLY:

      * `events.jsonl` — the canonical processed-meetings ledger in practice.
        `meeting` / `meeting_processed` / `meeting_skipped` events are what the
        past-meetings fire already dedups against, so they are what this reads.
      * `_hq/data/processed-meetings.json` when the workspace carries one — a
        live-state sibling under the workspace hygiene rules. It is read and
        MERGED; it is never rewritten, moved or truncated here (multi-machine
        clobber gotcha: a task that rewrites a synced live-state file wholesale
        loses whatever the other machine wrote between reads). All three shapes
        `_ledger_rows` documents are accepted, including the dict-keyed-by-
        document-id shape the live workspace carries; an unrecognized shape
        contributes nothing and says so on stderr.

    Never raises: an unreadable source contributes nothing rather than taking
    the fire down."""
    root = Path(workspace_root) if workspace_root else None
    if events_path is None and root is not None:
        events_path = root / "_hq" / "data" / "events.jsonl"
    if ledger_path is None and root is not None:
        ledger_path = root / "_hq" / "data" / "processed-meetings.json"

    out: List[dict] = []
    seen: set = set()

    def _add(rec: dict) -> None:
        ref = rec.get("source_ref") or rec.get("doc_id")
        if not ref or ref in seen:
            return
        seen.add(ref)
        out.append(rec)

    try:
        from events_io import iter_events
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from events_io import iter_events

    if events_path and Path(events_path).exists():
        try:
            for ev in iter_events(str(events_path)):
                if not isinstance(ev, dict):
                    continue
                etype = ev.get("type")
                if etype not in ("meeting", "meeting_processed",
                                 "meeting_skipped"):
                    continue
                data = ev.get("data") or {}
                ref = str(data.get("source_ref") or data.get("meeting_id")
                          or "").strip()
                if not ref:
                    continue
                # `meeting` events stamp `ts` = the meeting's START; the status
                # events stamp processing time, which is NOT a meeting start
                # and must never seed the fuzzy key.
                start = (data.get("start") or data.get("meeting_date")
                         or (ev.get("ts") if etype == "meeting" else None))
                # BUG-8244: fold every email variant the substrate has carried
                # (attendee_emails, attendees, invitees) — a replay that reads
                # one spelling routes attended meetings to the non-attendee
                # lane when the writer used another.
                try:
                    from event_refs import attendee_emails_of
                    emails = sorted(attendee_emails_of(ev))
                except Exception:
                    emails = data.get("attendee_emails") or []
                _add(normalize_meeting({
                    "source_ref": ref,
                    "doc_id": ref.split(":", 1)[-1],
                    "calendar_event_id": data.get("calendar_event_id") or "",
                    "title": data.get("title") or "",
                    "start": start,
                    "participants": emails,
                    "person_ids": ev.get("person_ids") or [],
                }))
        except Exception:
            pass

    if ledger_path and Path(ledger_path).exists():
        try:
            import json as _json
            raw = _json.loads(Path(ledger_path).read_text(encoding="utf-8"))
            rows, shape = _ledger_rows(raw)
            if shape == LEDGER_SHAPE_UNKNOWN:
                # Degrade to empty, but never silently: a ledger that stops
                # contributing is a dedup source that quietly went to zero, and
                # a two-source merge where only one source contributes is the
                # sweep-methodology gotcha in its exact form.
                sys.stderr.write(
                    "[meeting_discovery] processed-meetings ledger at "
                    f"{Path(ledger_path).name}: unrecognized shape "
                    f"({type(raw).__name__}); contributing 0 rows\n")
            for row in rows:
                _add(normalize_meeting(row))
        except Exception:
            pass

    return out


def dedup_meetings(meetings, *, processed=(),
                   tolerance_minutes: int = DEDUP_TOLERANCE_MIN) -> List[dict]:
    """One decision per input meeting, in input order.

    `{"meeting", "action", "duplicate_of", "basis", "classification",
      "classification_reason", "certain", "note_owner"}`

    Primary selection, per §A2: an already-processed version always wins (it is
    the one that carries the work). Within a batch the ATTENDED document wins,
    so a teammate's shared note of a meeting the user attended is the duplicate
    and the user's own note stays primary; ties break on the earlier start and
    then the document id, so the verdict is stable across runs."""
    normalized = [normalize_meeting(m) for m in meetings or []]
    verdicts = {id(m): classify_attendance(m) for m in normalized}
    prior = [normalize_meeting(p) for p in processed or []]

    order = sorted(
        range(len(normalized)),
        key=lambda i: (
            0 if verdicts[id(normalized[i])]["classification"] == CLASS_ATTENDED else 1,
            str(_parse_instant(normalized[i].get("start")) or ""),
            normalized[i].get("doc_id") or "",
        ),
    )

    decisions: Dict[int, dict] = {}
    accepted: List[dict] = []
    for i in order:
        m = normalized[i]
        verdict = verdicts[id(m)]
        base = {
            "meeting": m,
            "source_ref": m.get("source_ref"),
            "classification": verdict["classification"],
            "classification_reason": verdict["reason"],
            "certain": verdict["certain"],
            "note_owner": m.get("note_owner"),
            "duplicate_of": "",
            "basis": "",
        }
        hit = None
        for p in prior:
            r = same_meeting(m, p, tolerance_minutes=tolerance_minutes)
            if r["same"]:
                hit = (p, r["basis"], ACTION_SKIP_PROCESSED)
                break
        if hit is None:
            for p in accepted:
                r = same_meeting(m, p, tolerance_minutes=tolerance_minutes)
                if r["same"]:
                    hit = (p, r["basis"], ACTION_SKIP_DUPLICATE)
                    break
        if hit is None:
            base["action"] = ACTION_PROCESS
            accepted.append(m)
        else:
            primary, basis, action = hit
            base["action"] = action
            base["duplicate_of"] = primary.get("source_ref") or primary.get("doc_id")
            base["basis"] = basis
        decisions[i] = base

    return [decisions[i] for i in range(len(normalized))]


# ---------------------------------------------------------------------------
# §B — the non-attendee lane, in shadow
# ---------------------------------------------------------------------------

# THE SHADOW DIAL. While True, `shadow_fence` withholds every candidate event
# and the lane's single append callsite has nothing to append: the lane
# measures itself and writes NOTHING to the substrate.
#
# ⛔ Flipping this to False is the WRITE-ENABLING decision, which SPEC_GRANOLA1
# scopes OUT of this build — it is gated on the shadow report this module
# produces plus the capture-load re-measure. It is not a knob to flip while
# fixing something else, and it is deliberately not readable from config or
# the environment: a lane that can switch itself on is not a shadow run.
SHADOW_MODE = True

TIER_OBSERVED = "observed"
TIER_REVIEW = "review"
TIER_CLOSE_PROPOSAL = "close_proposal"
TIER_SKIPPED = "skipped"
REPORT_TIERS = (TIER_OBSERVED, TIER_REVIEW, TIER_CLOSE_PROPOSAL, TIER_SKIPPED)

ABSENT_OWNER_REASON = ("named in a meeting the user did not attend — never "
                       "auto-confirmed, the user was not there to agree")
AMBIGUOUS_ATTRIBUTION_REASON = ("the speaker this was attributed to is "
                                "ambiguous in a meeting the user did not attend")
DATED_NONATTENDEE_REASON = ("carries a due date or money and came from a "
                            "meeting the user did not attend")
OBSERVED_NONATTENDEE_REASON = "from a meeting the user did not attend"
# WATCHGATE strength class for anything this lane proposes: a secondhand
# account of the user's own work is weaker evidence than the user's own
# transcript, whatever words it uses. Never STRONG from here.
NONATTENDEE_STRENGTH = "weak"
NONATTENDEE_STRENGTH_REASON = ("this came from a meeting you were not in — "
                               "secondhand")


class ShadowLaneError(RuntimeError):
    """The lane was asked to do something outside its scope."""


def shadow_mode_enabled() -> bool:
    """Is the non-attendee lane in shadow? (The one reader of SHADOW_MODE.)"""
    return bool(SHADOW_MODE)


def shadow_fence(candidates, *, report: Optional[dict] = None) -> List[dict]:
    """⛔ THE SHADOW GUARD — the only thing standing between this lane and the
    substrate.

    In shadow mode it returns an EMPTY list for any candidate set, counting
    what it withheld onto the report. The lane's append callsite appends
    exactly what this returns, so the guard's removal is what the zero-writes
    pin is testing: with the guard neutered the same fixture appends real
    events and the pin goes red (proof-by-removal, per the trigger-test rule).
    """
    cands = [c for c in (candidates or []) if c]
    if not shadow_mode_enabled():  # pragma: no cover — write-enabling is out of scope
        return cands
    if report is not None:
        report["writes_suppressed"] = report.get("writes_suppressed", 0) + len(cands)
    return []


def _emit(events_path, candidates, *, report, holder="past-meetings.nonattendee") -> int:
    """The lane's ONE write callsite. It appends whatever `shadow_fence` hands
    back — which, in shadow mode, is nothing at all."""
    emit = shadow_fence(candidates, report=report)
    if not emit:
        return 0
    try:  # pragma: no cover — unreachable while shadow mode is on
        from event_gate import append_event
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from event_gate import append_event
    append_event(str(events_path), emit, holder=holder)  # pragma: no cover
    return len(emit)  # pragma: no cover


def user_is_party(data: dict, capture_context: dict,
                  *, workspace_root=None) -> bool:
    """Is the (absent) user named as a party on this capture? Owner or any
    counterparty, by resolved id or by a free-text name that matches the
    user's own names — the same name rule `capture_gate` applies, imported
    rather than re-derived so the two can never disagree.

    `workspace_root` is threaded into the roster readers (F-28): one person
    written as BOTH a resolved id and that person's free-text name is ONE
    counterparty, and the reader can only tell that from the entity graph.
    This decides whether an item goes to REVIEW as an absent-owner row or
    silently observed, so a phantom second counterparty changes the answer."""
    data = data or {}
    ctx = capture_context or {}
    user_id = ctx.get("user_id")
    names = ctx.get("user_names") or ()
    try:
        from commitment_parties import counterparty_ids, counterparty_names
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from commitment_parties import counterparty_ids, counterparty_names
    ids = {i for i in ([data.get("owner_id")]
                       + list(counterparty_ids(data))) if i}
    if user_id and user_id in ids:
        return True
    free = [data.get("owner_external")] + list(
        counterparty_names(data, workspace_root=workspace_root))
    return any(_name_matches(n, names) for n in free if n)


def _attribution_uncertain(data: dict) -> bool:
    """The speaker-attribution guard's own flags. Merged/mis-labelled speaker
    attribution is a known transcript failure, and an item whose owner is
    unresolved must reach the queue rather than go observed-silent."""
    data = data or {}
    return bool(data.get("attribution_ambiguous") or data.get("attribution_unknown"))


def _to_review(ev: dict, *, reason: str, absent_owner: bool = False,
               meeting_ref: str = "", note_owner: str = "") -> dict:
    out = dict(ev)
    data = dict(out.get("data") or {})
    data["pending_review"] = True
    data["review_reason"] = reason
    data["nonattendee_lane"] = True
    if absent_owner:
        data["absent_owner"] = True
    if meeting_ref:
        data["meeting_source_ref"] = meeting_ref
    if note_owner:
        data["note_owner"] = note_owner
    out["data"] = data
    return out


def _mark_lane(ev: dict, *, meeting_ref: str = "", note_owner: str = "") -> dict:
    out = dict(ev)
    data = dict(out.get("data") or {})
    data["nonattendee_lane"] = True
    if meeting_ref:
        data["meeting_source_ref"] = meeting_ref
    if note_owner:
        data["note_owner"] = note_owner
    out["data"] = data
    return out


def close_proposal_attendees(attendee_person_ids, user_id) -> List[str]:
    """The owner gate for this lane's closure matching, WIDENED on purpose.

    `cru_match` only scores commitments whose owner sat in the room — right for
    an attended meeting and structurally wrong here, because the whole B3 case
    is a teammate saying the ABSENT user's deliverable went out. So the user is
    added to the attendee set for matching only, and every result that comes
    back is demoted to a proposal (never a close) to pay for it."""
    ids = [a for a in (attendee_person_ids or ()) if a]
    if user_id and user_id not in ids:
        ids.append(user_id)
    return ids


def close_proposal_strength(evidence: str = "") -> dict:
    """WATCHGATE strength class for a closure proposal from this lane. Always
    WEAK: a secondhand account of the user's own work cannot be strong
    evidence, whatever completion words it happens to use."""
    return {"strength": NONATTENDEE_STRENGTH,
            "reason": NONATTENDEE_STRENGTH_REASON,
            "evidence": clip(evidence)}


def shadow_route_meeting(*, meeting, items=(), transcript_text=None,
                         workspace_root, events_path=None,
                         open_commitments=(), attendee_person_ids=(),
                         org_id=None, org_name=None, primary_thread_id=None,
                         meeting_date=None, fire_start=None,
                         capture_context=None, next_seq_start: int = 0,
                         source_skill: str = "past-meetings") -> dict:
    """Run ONE non-attendee meeting through the lane, in shadow.

    Every capture goes through `meeting_capture.route_meeting_captures` — the
    SAME admission path the attended legs use, so the capture floor, the
    cross-meeting fusion guardrail and party-only relevance all run here at
    THIS callsite rather than only inside a helper nobody calls. What differs
    is the routing AFTER admission, inverted by provenance:

      * book-tier captures NEVER stay on the book. The user named in absentia
        → review + `absent_owner`; anything dated/money → review (the caution
        rail says a dated item always surfaces, and from this lane surfacing
        means the queue); everything else → observed.
      * fusion refusals and below-floor rows keep the review disposition the
        shared helper already gave them.
      * completion language about the user's EXISTING open commitments →
        close PROPOSALS with a transcript pointer, at the weak strength class.

    Returns the per-meeting report entry. Writes nothing while shadow mode is
    on — `_emit` is the only write callsite and `shadow_fence` empties it."""
    m = normalize_meeting(meeting)
    verdict = classify_attendance(m)
    if verdict["classification"] != CLASS_NON_ATTENDEE:
        raise ShadowLaneError(
            "the non-attendee lane was handed an attended meeting — the "
            "attended pipeline owns those, unchanged")

    try:
        from meeting_capture import route_meeting_captures
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from meeting_capture import route_meeting_captures

    ctx = capture_context or workspace_capture_context(workspace_root)
    ref = m.get("source_ref")
    note_owner = m.get("note_owner") or ""

    entry: dict = {
        "source_ref": ref,
        "classification": verdict["classification"],
        "classification_reason": verdict["reason"],
        "certain": verdict["certain"],
        "note_owner": note_owner,
        "would_be": {t: 0 for t in REPORT_TIERS},
        "absent_owner": 0,
        "floor_gated": 0,
        "fusion_unverified": 0,
        "dated_held": 0,
        "writes_suppressed": 0,
        "events_written": 0,
        "close_proposals": [],
    }

    routed = route_meeting_captures(
        items,
        workspace_root=workspace_root,
        source_ref=ref,
        transcript_text=transcript_text,
        meeting_date=meeting_date,
        org_id=org_id,
        org_name=org_name,
        primary_thread_id=primary_thread_id,
        source_skill=source_skill,
    )

    candidates: List[dict] = []

    for ev in routed["observed"]:
        candidates.append(_mark_lane(ev, meeting_ref=ref, note_owner=note_owner))
        entry["would_be"][TIER_OBSERVED] += 1

    for ev in routed["review"]:
        data = ev.get("data") or {}
        if data.get("floor_gated"):
            entry["floor_gated"] += 1
        if data.get("fusion_unverified"):
            entry["fusion_unverified"] += 1
        candidates.append(_mark_lane(ev, meeting_ref=ref, note_owner=note_owner))
        entry["would_be"][TIER_REVIEW] += 1

    for ev in routed["book"]:
        data = ev.get("data") or {}
        if user_is_party(data, ctx, workspace_root=workspace_root):
            candidates.append(_to_review(ev, reason=ABSENT_OWNER_REASON,
                                         absent_owner=True, meeting_ref=ref,
                                         note_owner=note_owner))
            entry["would_be"][TIER_REVIEW] += 1
            entry["absent_owner"] += 1
            continue
        if _attribution_uncertain(data):
            candidates.append(_to_review(ev, reason=AMBIGUOUS_ATTRIBUTION_REASON,
                                         meeting_ref=ref, note_owner=note_owner))
            entry["would_be"][TIER_REVIEW] += 1
            continue
        if carries_due_or_money(data):
            # The caution rail is intact — a dated item still always surfaces.
            # It just surfaces in the QUEUE from this lane, never on the book.
            candidates.append(_to_review(ev, reason=DATED_NONATTENDEE_REASON,
                                         meeting_ref=ref, note_owner=note_owner))
            entry["would_be"][TIER_REVIEW] += 1
            entry["dated_held"] += 1
            continue
        try:
            obs = observed_from_commitment_event(
                ev, reason=OBSERVED_NONATTENDEE_REASON)
        except Exception:  # pragma: no cover — construction only
            entry["would_be"][TIER_SKIPPED] += 1
            continue
        candidates.append(_mark_lane(obs, meeting_ref=ref, note_owner=note_owner))
        entry["would_be"][TIER_OBSERVED] += 1

    entry["would_be"][TIER_SKIPPED] += len(routed["skipped"])

    # ---- B3: closure PROPOSALS, never closures -----------------------------
    proposals, diag = _close_proposals(
        open_commitments=open_commitments,
        attendee_person_ids=attendee_person_ids,
        transcript_text=transcript_text,
        workspace_root=workspace_root,
        meeting=m,
        capture_context=ctx,
        fire_start=fire_start,
        next_seq_start=next_seq_start,
        source_skill=source_skill,
    )
    for prop in proposals:
        candidates.append(prop["event"])
        entry["would_be"][TIER_CLOSE_PROPOSAL] += 1
        entry["close_proposals"].append({
            "commitment_id": prop["commitment_id"],
            "proposed_resolution": prop["proposed_resolution"],
            "strength": prop["strength"],
        })
    entry["stale_evidence_skipped"] = int(diag.get("stale_evidence_dropped", 0))

    entry["events_written"] = _emit(
        events_path or _events_path_for(workspace_root), candidates, report=entry)
    return entry


def _events_path_for(workspace_root) -> str:
    return str(Path(workspace_root) / "_hq" / "data" / "events.jsonl")


def _close_proposals(*, open_commitments, attendee_person_ids, transcript_text,
                     workspace_root, meeting, capture_context, fire_start,
                     next_seq_start, source_skill):
    """Completion language about the user's OPEN commitments → propose-close
    rows, with the transcript pointer on `data.source_ref` (the pointer-less
    close hole must not reopen on a new lane) and the weak strength class.

    NOTHING here closes anything: the matcher's `auto_resolve` verdict is
    demoted to a PROPOSAL, because a teammate's account of the user's own
    deliverable is weaker provenance than the user's own transcript."""
    diag: dict = {}
    if not open_commitments or not transcript_text:
        return [], diag
    try:
        from cru_match import (build_pending_review_event,
                               match_transcript_to_commitments)
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from cru_match import (build_pending_review_event,
                               match_transcript_to_commitments)

    ref = meeting.get("source_ref")
    start = meeting.get("start")
    results = match_transcript_to_commitments(
        open_commitments=list(open_commitments),
        attendee_person_ids=close_proposal_attendees(
            attendee_person_ids, (capture_context or {}).get("user_id")),
        transcript_text=transcript_text,
        workspace_root=workspace_root,
        transcript_source_ref=ref,
        exclude_captured_since=fire_start,
        transcript_ts=start,
        diagnostics=diag,
    )
    out: List[dict] = []
    seq = int(next_seq_start or 0)
    for r in results:
        rec = r.get("recommendation")
        if rec not in ("auto_resolve", "pending_review", "supersede"):
            continue
        resolution = "supersede" if rec == "supersede" else "auto_resolve"
        strength = close_proposal_strength()
        evidence = ("A meeting the user did not attend "
                    f"({'completion language' if r.get('has_completion_signal') else 'title match'})"
                    f" — {NONATTENDEE_STRENGTH_REASON}")
        ev = build_pending_review_event(
            commitment_id=r["commitment_id"],
            primary_thread_id=r.get("primary_thread_id") or "",
            source_skill=source_skill,
            proposed_resolution=resolution,
            score=r.get("score") or 0.0,
            evidence=evidence,
            next_seq=seq,
            title=r.get("title") or "",
            has_completion_signal=r.get("has_completion_signal"),
            evidence_ts=str(start) if start else None,
        )
        data = dict(ev.get("data") or {})
        # The transcript pointer, on the key every closure reader already
        # looks at — a proposal whose evidence cannot be traced back to a
        # source is the pointer-less-close hole, restated on a weaker lane.
        data["source_ref"] = ref
        data["nonattendee_lane"] = True
        data["evidence_strength"] = strength["strength"]
        data["strength_reason"] = strength["reason"]
        data["auto_close_blocked"] = True
        ev["data"] = data
        seq += 1
        out.append({
            "event": ev,
            "commitment_id": r["commitment_id"],
            "proposed_resolution": resolution,
            "strength": strength["strength"],
        })
    return out, diag


# ---------------------------------------------------------------------------
# The per-run shadow report
# ---------------------------------------------------------------------------

DEFAULT_MEETING_CAP = 5


def _local_iso(value, workspace_root=None) -> str:
    """Render an instant in the workspace timezone — never the upstream's own
    claim about what time it is (`tz.to_local` is the one converter)."""
    if value in (None, ""):
        return ""
    try:
        from tz import to_local
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            from tz import to_local
        except ImportError:
            return str(value)
    try:
        localized = to_local(value, workspace_path=workspace_root)
    except Exception:
        # A workspace with no resolvable timezone must not cost the report.
        return str(value)
    return localized.isoformat() if localized else ""


def empty_report() -> dict:
    return {
        "shadow": True,
        "counts": {"discovered": 0, "attended": 0, "non_attendee": 0,
                   "duplicate": 0, "uncertain": 0, "already_processed": 0},
        "would_be": {t: 0 for t in REPORT_TIERS},
        "absent_owner": 0,
        "floor_gated": 0,
        "fusion_unverified": 0,
        "dated_held": 0,
        "stale_evidence_skipped": 0,
        "writes_suppressed": 0,
        "events_written": 0,
        "cap": {"limit": DEFAULT_MEETING_CAP, "processed": 0, "overflow": 0,
                "oldest_unprocessed": ""},
        "meetings": [],
    }


def run_shadow_pass(payloads, *, workspace_root, events_path=None,
                    processed=None, cap: int = DEFAULT_MEETING_CAP,
                    tolerance_minutes: int = DEDUP_TOLERANCE_MIN,
                    open_commitments=(), fire_start=None,
                    source_skill: str = "past-meetings") -> dict:
    """Discovery → classification → dedup → the shadow lane, for one fire.

    `payloads` is one entry per discovered meeting:
      `{"meeting": <backend record>, "items": [...extracted captures...],
        "transcript_text": "...", "attendee_person_ids": [...],
        "org_id"/"org_name"/"primary_thread_id"/"meeting_date": optional}`

    ATTENDED meetings are reported and otherwise UNTOUCHED — the existing
    pipeline owns them and its behavior is byte-identical to before this
    module existed. Duplicates are receipted with `duplicate_of` and never
    reprocessed. Only non-attendee, non-duplicate meetings enter the lane, at
    most `cap` per run, oldest first, with the overflow logged rather than
    silently truncated.

    The return is the SHADOW REPORT: counts by classification and by would-be
    tier, plus per-meeting rows carrying refs and counts only — never a title,
    which is the same discipline the meeting receipts already keep."""
    report = empty_report()
    report["cap"]["limit"] = cap
    report["generated_at"] = _local_iso(
        _dt.datetime.now(_dt.timezone.utc), workspace_root)

    payloads = [dict(p or {}) for p in payloads or []]
    if processed is None:
        processed = processed_index(workspace_root, events_path=events_path)
    decisions = dedup_meetings([p.get("meeting") for p in payloads],
                               processed=processed,
                               tolerance_minutes=tolerance_minutes)
    report["counts"]["discovered"] = len(decisions)

    lane_queue: List[tuple] = []
    for payload, decision in zip(payloads, decisions):
        m = decision["meeting"]
        row = {
            "source_ref": decision["source_ref"],
            "classification": decision["classification"],
            "classification_reason": decision["classification_reason"],
            "certain": decision["certain"],
            "note_owner": decision["note_owner"],
            "action": decision["action"],
            "duplicate_of": decision["duplicate_of"],
            "dedup_basis": decision["basis"],
            "start_local": _local_iso(m.get("start"), workspace_root),
        }
        if not decision["certain"]:
            report["counts"]["uncertain"] += 1
        if decision["classification"] == CLASS_ATTENDED:
            report["counts"]["attended"] += 1
        else:
            report["counts"]["non_attendee"] += 1
        if decision["action"] == ACTION_SKIP_PROCESSED:
            report["counts"]["already_processed"] += 1
            report["meetings"].append(row)
            continue
        if decision["action"] == ACTION_SKIP_DUPLICATE:
            report["counts"]["duplicate"] += 1
            report["meetings"].append(row)
            continue
        if decision["classification"] == CLASS_ATTENDED:
            row["lane"] = "attended-pipeline"
            report["meetings"].append(row)
            continue
        lane_queue.append((payload, m, row))

    # Oldest first — the backlog drains in order across runs, and the cap
    # overflow is REPORTED rather than quietly dropped.
    lane_queue.sort(key=lambda t: (str(_parse_instant(t[1].get("start")) or ""),
                                   t[1].get("doc_id") or ""))
    run_now = lane_queue[:cap] if cap and cap > 0 else lane_queue
    overflow = lane_queue[len(run_now):]
    report["cap"]["processed"] = len(run_now)
    report["cap"]["overflow"] = len(overflow)
    if overflow:
        report["cap"]["oldest_unprocessed"] = _local_iso(
            overflow[0][1].get("start"), workspace_root)
        for _payload, _m, row in overflow:
            row["lane"] = "deferred-cap"
            report["meetings"].append(row)

    ctx = workspace_capture_context(workspace_root)
    for payload, m, row in run_now:
        entry = shadow_route_meeting(
            meeting=m,
            items=payload.get("items") or (),
            transcript_text=payload.get("transcript_text"),
            workspace_root=workspace_root,
            events_path=events_path,
            open_commitments=open_commitments,
            attendee_person_ids=payload.get("attendee_person_ids") or (),
            org_id=payload.get("org_id"),
            org_name=payload.get("org_name"),
            primary_thread_id=payload.get("primary_thread_id"),
            meeting_date=payload.get("meeting_date"),
            fire_start=fire_start,
            capture_context=ctx,
            source_skill=source_skill,
        )
        row.update({k: entry[k] for k in
                    ("would_be", "absent_owner", "floor_gated",
                     "fusion_unverified", "dated_held", "writes_suppressed",
                     "events_written", "close_proposals")})
        row["lane"] = "non-attendee-shadow"
        for tier in REPORT_TIERS:
            report["would_be"][tier] += entry["would_be"][tier]
        for key in ("absent_owner", "floor_gated", "fusion_unverified",
                    "dated_held", "writes_suppressed", "events_written",
                    "stale_evidence_skipped"):
            report[key] += entry.get(key, 0)
        report["meetings"].append(row)

    return report


def render_shadow_report(report: dict) -> str:
    """The report as one short block for the fire's log. COUNTS ONLY — no
    titles, no names, same discipline as the meeting receipts."""
    r = report or empty_report()
    c, w = r.get("counts", {}), r.get("would_be", {})
    lines = [
        "Non-attendee lane — SHADOW (no substrate writes)",
        (f"  discovered={c.get('discovered', 0)} attended={c.get('attended', 0)} "
         f"non_attendee={c.get('non_attendee', 0)} duplicate={c.get('duplicate', 0)} "
         f"already_processed={c.get('already_processed', 0)} "
         f"uncertain={c.get('uncertain', 0)}"),
        (f"  would-be: observed={w.get(TIER_OBSERVED, 0)} "
         f"review={w.get(TIER_REVIEW, 0)} "
         f"close_proposal={w.get(TIER_CLOSE_PROPOSAL, 0)} "
         f"skipped={w.get(TIER_SKIPPED, 0)}"),
        (f"  absent_owner={r.get('absent_owner', 0)} "
         f"floor_gated={r.get('floor_gated', 0)} "
         f"fusion_unverified={r.get('fusion_unverified', 0)} "
         f"dated_held={r.get('dated_held', 0)}"),
        (f"  writes_suppressed={r.get('writes_suppressed', 0)} "
         f"events_written={r.get('events_written', 0)} "
         f"cap_overflow={r.get('cap', {}).get('overflow', 0)}"),
    ]
    return "\n".join(lines)


__all__ = [
    # §A
    "CLASS_ATTENDED",
    "CLASS_NON_ATTENDEE",
    "REASON_CAPTURED_BY_USER",
    "REASON_LISTED_PARTICIPANT",
    "REASON_NO_INVOLVEMENT",
    "REASON_UNPROVEN",
    "SOURCE_REF_PREFIX",
    "normalize_meeting",
    "normalize_title",
    "classify_attendance",
    # §A2
    "DEDUP_TOLERANCE_MIN",
    "ACTION_PROCESS",
    "ACTION_SKIP_DUPLICATE",
    "ACTION_SKIP_PROCESSED",
    "same_meeting",
    "LEDGER_SHAPE_MEETINGS_LIST",
    "LEDGER_SHAPE_BARE_LIST",
    "LEDGER_SHAPE_DOC_KEYED",
    "LEDGER_SHAPE_UNKNOWN",
    "processed_index",
    "dedup_meetings",
    # §B
    "SHADOW_MODE",
    "shadow_mode_enabled",
    "shadow_fence",
    "ShadowLaneError",
    "TIER_OBSERVED",
    "TIER_REVIEW",
    "TIER_CLOSE_PROPOSAL",
    "TIER_SKIPPED",
    "REPORT_TIERS",
    "ABSENT_OWNER_REASON",
    "AMBIGUOUS_ATTRIBUTION_REASON",
    "DATED_NONATTENDEE_REASON",
    "OBSERVED_NONATTENDEE_REASON",
    "NONATTENDEE_STRENGTH",
    "NONATTENDEE_STRENGTH_REASON",
    "user_is_party",
    "close_proposal_attendees",
    "close_proposal_strength",
    "shadow_route_meeting",
    "run_shadow_pass",
    "empty_report",
    "render_shadow_report",
    "DEFAULT_MEETING_CAP",
]
