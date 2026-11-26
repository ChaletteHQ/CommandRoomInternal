#!/usr/bin/env python3
"""The meeting page's follow-up plan: who gets which email (cr1#1787, Hub
v6.12 slice 4, backend groundwork).

Before a meeting is processed the owner can change who gets which follow-up:
one email to several people, a person who was not on the call (ruling 125e),
a person taken off, and the kind of email each one is. Those choices are
saved on the page as `meetplan` presses and written here at the next Update
(`cockpit_helpers.apply_run`), the way every other press is. The automatic
write-up reads this store before it drafts (`followup_draft.record_draft`,
`followup_draft.drafts_wanted`), and so does the follow-ups feed.

One small store, written only here, under its own lock:

  `_hq/data/hub-meetplan.json`
    `meetings`  one row per meeting with a plan, `{key, meeting_id, at,
                emails, removed}`:
                `key` the calendar key the page's rows carry (32 hex, cr1#1758),
                `meeting_id` the meeting's id as the page's row has it,
                `start` the occurrence's start (UTC, to the minute). A press
                names the id and the start, always (review 2 of #1814,
                must-fix 5); the key when the row has one.
                `emails` one row per chosen email, `{email_id, to, kind,
                instruction, at, choice_id}`: `to` the person ids it goes to,
                one or several, attendees or not; a person is on one email
                of a meeting at most.
                `removed` one row per person taken off, `{person_id, at,
                choice_id}`; a later add of that person by the owner stamps
                `back_at` and `back_by` (history stays additive).
    `history`   one row per press that changed a plan, `{choice_id, key,
                meeting_id, before, after}`: the meeting's plan before and
                after, so its Undo restores exactly what it changed.
    `applied`   the ids of the presses that wrote here, newest last, so a
                press a stale page sends again is not applied twice.
    `lost`      one row per plan file set aside unread, `{file, at}`: the
                plans in it may have taken someone off, so a meeting that
                could have been planned before then gets no automatic
                draft until the owner chooses again (`lookup`).

Which plan is a meeting's (review 2 of #1814, the new must-fix): `lookup`
answers the one plan that is this occurrence's exactly (the same calendar
key, or the same id and start), "none" only when no plan could be this
meeting's at all, and "unsure" whenever one could be and none is exactly:
a plan with the meeting's id and another start (a moved meeting, a start a
minute off), a plan with no zone on its start, a plan at the same minute
under another id (the notes and the calendar can name a meeting by
different ids), two plans that both match, or a lost plan file. A reader
holds the meeting's drafting on "unsure" and says so; it never reads it as
"no plan".

The rules, each pinned by HUBMEETPLAN1:

  * A person removed never comes back on their own. No draft is written to
    them, from the automatic run or any other path, and a draft already
    written to them is held off the Hub (`followup_draft`). Only the owner's
    own later Add (or the Undo of the removal) brings them back.
  * Someone off limits or on the personal side is never put in a plan, by
    any press; the store refuses it and names nobody in its answer.
  * Only a person on file can be put in a plan (a person id); the page adds
    new people through People first.
  * The store never writes the book. The plate and the people rows take no
    new field (ruling 111, #1768, #1751's size guard).

stdlib only. Never sends. Synthetic sample data only in tests.
"""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from atomic_write import acquire_write_lock, atomic_write_json, release_write_lock
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from atomic_write import acquire_write_lock, atomic_write_json, release_write_lock  # type: ignore

PLAN_REL = Path("_hq") / "data" / "hub-meetplan.json"
STORE_VERSION = 1
VERBS = ("add", "remove", "kind", "undo")
#: The kinds of email a row can be (the meeting page's menu). `pick` leaves
#: the choice to the background run; `own` carries the owner's instruction.
KINDS = ("actions", "summary", "thanks", "next_steps", "pick", "own")
KIND_WORDS = {"actions": "Follow-up with action items", "summary": "Summary", "thanks": "Quick thank-you",
              "next_steps": "Next steps and dates", "pick": "Let Command Room pick", "own": "Write your own"}
DEFAULT_KIND = "pick"
APPLIED_CAP = 2000
HISTORY_CAP = 1000
MEETINGS_CAP = 500
EMAILS_CAP = 20
TO_CAP = 20
INSTRUCTION_CAP = 600
ID_CAP = 80
MEETING_ID_CAP = 200
LOST_CAP = 20
#: How far ahead the page shows a meeting a plan can be made for (the Week
#: view's seven days, `CALENDAR_LIVE_AHEAD_DAYS`) and a day's margin: a
#: meeting that starts within this of a lost plan file could have had a
#: plan in it.
LOST_HORIZON_DAYS = 8
KEY_RE = re.compile(r"^[0-9a-f]{32}$")
EMAIL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

ADDED_LINE = "Added to the follow-up."
ADDED_ALREADY_LINE = "That person is already on that email; nothing was written."
BACK_LINE = "Added back to the follow-up."
REMOVED_LINE = "Taken off the follow-up. No email is drafted for them from this meeting."
REMOVED_ALREADY_LINE = "That person is already off the follow-up; nothing was written."
KIND_LINE = "The kind of email is changed: {words}."
KIND_ALREADY_LINE = "That email is already that kind; nothing was written."
UNKNOWN_KIND_LINE = "That is not a kind of email the page offers; nothing was written."
OWN_EMPTY_LINE = "Write your own needs a few words saying what the email should say; nothing was written."
NO_EMAIL_LINE = "That email is not in this meeting's follow-up any more; nothing was written."
NO_MEETING_LINE = "That press does not say which meeting, and when, so nothing was written."
SET_ASIDE_LINE = ("Your follow-up plans could not be read, so the file was set aside (kept) and a new one "
                  "started. Meetings in the next week get no automatic follow-up until you choose again.")
TAKEN_BACK_LINE = "Taken back before it was written; nothing was written."
NO_PERSON_LINE = "That press names nobody on file; nothing was written."
HIDDEN_LINE = "That person cannot get a follow-up from here; nothing was written."
NAMED_LINE = "Those words name someone who cannot get a follow-up from here; nothing was written."
WALL_UNREAD_LINE = "Who may get a follow-up could not be checked just now, so nothing was written. The next update tries again."
STORE_UNREADABLE_LINE = "The follow-up plan could not be read, so nothing was written. The next update tries again."
UNKNOWN_VERB_LINE = "The follow-up plan does not take that press ({verb}); nothing was written."
REPLAYED_LINE = "That press was already applied; nothing was written."
UNDONE_LINE = "Put back as it was."
UNDO_NOTHING_LINE = "That press has nothing left to take back (the plan was changed since); nothing was written."
UNDO_NO_PRESS_LINE = "That Undo names no press; nothing was written."
TOO_MANY_LINE = "That follow-up already has as many emails or people as it can take; nothing was written."


def _now(now_iso: Optional[str] = None) -> _dt.datetime:
    if now_iso:
        try:
            dt = _dt.datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            pass
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pid(value: Any) -> str:
    return str(value or "").strip()[:ID_CAP]


def norm_key(value: Any) -> str:
    """The calendar key, lower-cased, or "" when it is not one."""
    k = str(value or "").strip().lower()
    return k if KEY_RE.match(k) else ""


def norm_start(value: Any) -> str:
    """A meeting's start as a plan names its occurrence (review of #1814,
    must-fix 2): the instant to the minute, in UTC (`YYYY-MM-DDTHH:MMZ`), so
    the page's and the automatic run's ways of writing the same time meet;
    "" when it is not a time with a zone."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        dt = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        return ""
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def norm_meeting_id(value: Any) -> str:
    return str(value or "").strip()[:MEETING_ID_CAP]


# ---------------------------------------------------------------- the store

def plan_path(workspace_root) -> Path:
    return Path(str(workspace_root)) / PLAN_REL


def _empty() -> Dict[str, Any]:
    return {"version": STORE_VERSION, "meetings": [], "history": [], "applied": [], "lost": []}


def _load_for_write(workspace_root) -> Dict[str, Any]:
    path = plan_path(workspace_root)
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"plan unreadable: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise ValueError("plan unreadable: not an object")
    data.setdefault("version", STORE_VERSION)
    # A missing field is empty; a field in another shape is a store this
    # module did not write: refused and left as it is, never reset.
    for key in ("meetings", "history", "applied", "lost"):
        if key not in data:
            data[key] = []
        elif not isinstance(data[key], list):
            raise ValueError(f"plan unreadable: {key} is not a list")
    for m in data["meetings"]:
        if not isinstance(m, dict) or not isinstance(m.get("emails", []), list) \
                or not isinstance(m.get("removed", []), list):
            raise ValueError("plan unreadable: a meeting is not a plan")
    return data


def load_store(workspace_root) -> Tuple[Dict[str, Any], bool]:
    """(the store, unread). A missing store is an empty one; an unreadable
    one is an empty one with `unread` True, so a reader can fail closed."""
    try:
        return _load_for_write(workspace_root), False
    except ValueError:
        return _empty(), True


def _matches(rec: Dict[str, Any], key: str, meeting_id: str, start: str) -> bool:
    """A plan is one occurrence's (review of #1814, must-fix 2): its calendar
    key is the press's key, or its meeting id AND its start are the press's.
    A meeting id alone never matches, so a recurring meeting's plan never
    governs another of its occurrences; a key that differs is another
    occurrence, whatever the id says."""
    rk, rm, rs = norm_key(rec.get("key")), norm_meeting_id(rec.get("meeting_id")), norm_start(rec.get("start"))
    if key and rk:
        return rk == key
    return bool(meeting_id and start) and rm == meeting_id and rs == start


def find(store: Dict[str, Any], key: Any = "", meeting_id: Any = "", start: Any = "") -> Optional[Dict[str, Any]]:
    """The occurrence's plan in `store`, or None. Pure."""
    k, mid, st = norm_key(key), norm_meeting_id(meeting_id), norm_start(start)
    if not k and not (mid and st):
        return None
    for rec in store.get("meetings") or []:
        if isinstance(rec, dict) and not _ref_id(rec.get("ref")) and _matches(rec, k, mid, st):
            return rec
    return None


def _minute(value: Any) -> Optional[_dt.datetime]:
    st = norm_start(value)
    return _dt.datetime.strptime(st, "%Y-%m-%dT%H:%MZ").replace(tzinfo=_dt.timezone.utc) if st else None


#: Rule R2 of #1814 (notes/spec_1814_rule_R2.md, the coordinator's spec). A plan
#: within this many hours of the write-up's own start (absolute hours) is a
#: candidate for it.
CANDIDATE_HOURS = 48
#: The bound event's window: from this many minutes before the event's start to
#: its end. The write-up's own start must fall inside it for its binding to be
#: confirmed (the notes' span is not used).
WINDOW_EARLY_MINUTES = 15
#: An event whose end the read did not give: this long.
DEFAULT_EVENT_MINUTES = 30
#: The series key of a one-off meeting. A plan with the field absent is a legacy
#: plan, whose series is unknown (fail closed).
SERIES_NONE = "none"
REF_REFUSED_LINE = ("That press does not match a meeting write-up on your book (or names another meeting), "
                    "so nothing was written.")
ENDED_LINE = "That meeting has ended. Make its follow-up choices on its own page in the Hub; nothing was written."
#: Rule R2, sixth review of #1814 (MUST 1): the meeting is written up and its write-up has its own choices
OWN_PLAN_LINE = ("That meeting is written up and has its own follow-up choices. Make changes on its own page "
                 "in the Hub; nothing was written.")


def norm_series(value: Any) -> str:
    """A plan's or a binding's series key: a calendar key, or SERIES_NONE; "" when not given. Pure."""
    v = str(value or "").strip().lower()
    return SERIES_NONE if v == SERIES_NONE else norm_key(v)


def norm_ref(value: Any) -> str:
    """A write-up's pointer as a plan stores it (review 5 of #1814): "" for none. Pure."""
    v = str(value or "").strip()
    return v[:400] if v else ""


def _ref_id(value: Any) -> str:
    """A pointer by its identity (`hub_meeting._ref_key`), so two spellings of one meet. Pure."""
    v = norm_ref(value)
    if not v:
        return ""
    try:
        import hub_meeting

        return hub_meeting._ref_key(v) or v
    except Exception:  # noqa: BLE001
        return v


def _window(binding: Dict[str, Any]) -> Optional[Tuple[_dt.datetime, _dt.datetime]]:
    """The bound event's window, from WINDOW_EARLY_MINUTES before its start to its end. Pure."""
    st = _minute(binding.get("start"))
    if st is None:
        return None
    end = _minute(binding.get("end"))
    if end is None or end <= st:
        end = st + _dt.timedelta(minutes=DEFAULT_EVENT_MINUTES)
    return st - _dt.timedelta(minutes=WINDOW_EARLY_MINUTES), end


def confirmed(binding: Any, start: Any) -> Optional[Dict[str, Any]]:
    """Rule R2: the write-up's binding, kept only when its own start falls in
    the bound event's window (its end exclusive, as `meeting_binding.bind`);
    None otherwise (treated as no binding). A binding with no series field
    has an unknown series ("" here), which `related` treats as shared. Pure."""
    if not isinstance(binding, dict) or not str(binding.get("event_id") or "").strip():
        return None
    w, s = _window(binding), _minute(start)
    if w is None or s is None or not (w[0] <= s < w[1]):
        return None
    return {"key": norm_key(binding.get("key")), "event_id": norm_meeting_id(binding.get("event_id")),
            "start": norm_start(binding.get("start")), "end": norm_start(binding.get("end")),
            "series": norm_series(binding.get("series")), "window": w}


def candidates(store: Dict[str, Any], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Rule R2's candidates for one write-up (`ctx`: ref, start, ids,
    binding): every plan within CANDIDATE_HOURS of its start (every plan when
    it has none), every plan carrying its ref, and every plan sharing its
    confirmed binding's key or id, or its own ids. Pure."""
    s, me, b = _minute(ctx.get("start")), _ref_id(ctx.get("ref")), ctx.get("binding")
    ids = {norm_meeting_id(i) for i in ctx.get("ids") or () if norm_meeting_id(i)}
    out = []
    for rec in store.get("meetings") or []:
        if not isinstance(rec, dict):
            continue
        rs = _minute(rec.get("start"))
        rk, rm = norm_key(rec.get("key")), norm_meeting_id(rec.get("meeting_id"))
        if s is None or (rs is not None and abs((rs - s).total_seconds()) <= CANDIDATE_HOURS * 3600) \
                or (me and _ref_id(rec.get("ref")) == me) \
                or (b and ((rk and rk == b["key"]) or (rm and rm == b["event_id"]))) or (rm and rm in ids):
            out.append(rec)
    return out


def related(rec: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
    """Rule R2: a candidate is related to a write-up with a confirmed binding
    when its start falls in the bound event's window (whatever its key or id
    is spelled like); or it shares the binding's key, id or series (a series
    of SERIES_NONE shares with nothing; a binding whose series is unknown,
    because the calendar read gave none, shares with every series: fail
    closed); or it has no key and no id; or its series is absent (a legacy
    plan: fail closed). The window's end is exclusive, so the next
    back-to-back session's plan is not related by its start.
    Sixth review of #1814 (MUST 4): a plan carrying another write-up's ref is
    related only by these tests, never by its ref alone, so a choice on
    another meeting's page does not hold this one; two write-ups of one
    meeting are still related through the window or the key. Pure."""
    b = ctx["binding"]
    rs = _minute(rec.get("start"))
    rk, rm = norm_key(rec.get("key")), norm_meeting_id(rec.get("meeting_id"))
    if rs is not None and b["window"][0] <= rs < b["window"][1]:
        return True
    if (rk and rk == b["key"]) or (rm and rm == b["event_id"]):
        return True
    series = norm_series(rec.get("series"))
    if series and series != SERIES_NONE and (series == b["series"] or not b["series"]):
        return True
    if not rk and not rm:
        return True
    return "series" not in rec


def resolve(store: Dict[str, Any], ctx: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str, str]:
    """Rule R2 (notes/spec_1814_rule_R2.md): (the plan, state, rule) for one
    write-up. State "exact" (its plan), "none" (default drafting) or
    "unsure" (held). Rule: "ref", "none", "bound-unrelated", "bound-one" or
    "held", the words the run's receipt counts.
      1. A plan carries the write-up's ref: use it; nothing else is consulted.
      2. No candidates: default drafting.
      3. A confirmed binding: no related candidate, default drafting; exactly
         one related candidate, matching the binding's key or id and naming
         no other write-up, use it; anything else holds.
      4. No confirmed binding: any candidate holds.
    Pure."""
    me = _ref_id(ctx.get("ref"))
    recs = [r for r in store.get("meetings") or [] if isinstance(r, dict)]
    mine = [r for r in recs if me and _ref_id(r.get("ref")) == me]
    if len(mine) == 1:
        return mine[0], "exact", "ref"
    if mine:
        return None, "unsure", "held"
    cands = candidates(store, ctx)
    if not cands:
        return (None, "unsure", "held") if _lost_near(store, ctx) else (None, "none", "none")
    b = ctx.get("binding")
    if not b:
        return None, "unsure", "held"
    rel = [r for r in cands if related(r, ctx)]
    if not rel:
        return (None, "unsure", "held") if _lost_near(store, ctx) else (None, "none", "bound-unrelated")
    one = _bound_one(rel, b)
    if one is not None:
        return one, "exact", "bound-one"
    return None, "unsure", "held"


def _bound_one(rel: List[Dict[str, Any]], b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Rule 3's plan: the one related candidate, when there is exactly one,
    it carries no write-up's ref and it matches the binding's key or id;
    else None. Pure."""
    if len(rel) != 1:
        return None
    one = rel[0]
    if not _ref_id(one.get("ref")) and (
            (norm_key(one.get("key")) and norm_key(one.get("key")) == b["key"])
            or (norm_meeting_id(one.get("meeting_id")) and norm_meeting_id(one.get("meeting_id")) == b["event_id"])):
        return one
    return None


def _lost_near(store: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
    """Review 2 of #1814, kept under rule R2: an unreadable plan file was set
    aside, so the plans in it are lost; a write-up starting within
    LOST_HORIZON_DAYS of that (or with no start) is held, never drafted by
    default. Pure."""
    s = _minute(ctx.get("start"))
    for row in store.get("lost") or []:
        at = _now(row.get("at")) if isinstance(row, dict) and row.get("at") else None
        if s is None or at is None or s <= at + _dt.timedelta(days=LOST_HORIZON_DAYS):
            return True
    return False


def seed(store: Dict[str, Any], ctx: Dict[str, Any], now: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Rule R2: what a write-up's own plan starts with when the owner's first
    press on its page makes it: every person removed in a related candidate
    (with a confirmed binding) or in any candidate (without one), each with
    the plan it came from; and the chosen emails of the plan rule 3 would use
    (`_bound_one`: exactly one related candidate, with no ref, matching the
    binding's key or id), each marked `seeded_from` like the removals. Without
    a confirmed binding no email is copied (sixth review of #1814, MUST 3: a
    nearby plan's emails are another meeting's, recipients and own words
    included). Pure."""
    cands = candidates(store, ctx)
    b = ctx.get("binding")
    if b:
        cands = [r for r in cands if related(r, ctx)]
    removed, seen = [], set()
    for rec in cands:
        for r in rec.get("removed") or []:
            if not isinstance(r, dict) or r.get("back_at"):
                continue
            pid = _pid(r.get("person_id"))
            if pid and pid not in seen:
                seen.add(pid)
                removed.append({"person_id": pid, "at": now, "choice_id": "", "seeded_from": {
                    "meeting_id": norm_meeting_id(rec.get("meeting_id")), "key": norm_key(rec.get("key")),
                    "start": norm_start(rec.get("start"))}})
    one = _bound_one(cands, b) if b else None
    mails = copy.deepcopy([e for e in one.get("emails") or [] if isinstance(e, dict)]) if one is not None else []
    for e in mails:
        e["to"] = [p for p in e.get("to") or [] if _pid(p) not in seen]
        e["seeded_from"] = {"meeting_id": norm_meeting_id(one.get("meeting_id")), "key": norm_key(one.get("key")),
                            "start": norm_start(one.get("start"))}
    return removed, [e for e in mails if e.get("to")]


def plan_lookup(workspace_root, *, meeting_key: Any = "", meeting_id: Any = "", start: Any = "",
                also_ids: Any = (), ref: Any = "") -> Tuple[Dict[str, Any], str]:
    """READ. (the plan, state) as stored: a write-up's own plan by its `ref`,
    else the Today plan for the occurrence (`find`); state "exact" or
    "none", "unread" when the store cannot be read. Which plan governs a
    write-up's drafting is `resolve`'s (rule R2), never this."""
    store, unread = load_store(workspace_root)
    if unread:
        return {}, "unread"
    me = _ref_id(ref)
    rec = next((r for r in store.get("meetings") or [] if isinstance(r, dict) and _ref_id(r.get("ref")) == me),
               None) if me else find(store, meeting_key, meeting_id, start)
    return (copy.deepcopy(rec) if rec else {}), ("exact" if rec else "none")


def plan_for(workspace_root, *, meeting_key: Any = "", meeting_id: Any = "", start: Any = "",
             also_ids: Any = (), ref: Any = "") -> Optional[Dict[str, Any]]:
    """READ. The stored plan (`plan_lookup`), `{}` when there is none, None
    when the store cannot be read."""
    plan, state = plan_lookup(workspace_root, meeting_key=meeting_key, meeting_id=meeting_id, start=start,
                              also_ids=also_ids, ref=ref)
    return None if state == "unread" else plan


def _pressed(pressed_at: Any, now: _dt.datetime) -> _dt.datetime:
    """When a press was made, for the ended test (sixth review of #1814, MUST
    5): its own `created_at`, used only when it parses, carries a zone and is
    not after the Update's time; else the Update's time. Pure."""
    raw = str(pressed_at or "").strip()
    if raw:
        try:
            dt = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            dt = None
        if dt is not None and dt.tzinfo is not None and dt <= now:
            return dt
    return now


def _press_window(st: str, end: Any) -> Optional[Tuple[_dt.datetime, _dt.datetime]]:
    """A Today press's occurrence window, as `_window` reads a binding: from
    WINDOW_EARLY_MINUTES before its start to its end (exclusive). Pure."""
    return _window({"start": st, "end": end})


def _has_own_plan(workspace_root, key: str, mid: str, st: str, end: Any) -> bool:
    """READ. Sixth review of #1814 (MUST 1): whether the occurrence a press
    without a ref names already has a write-up with its own plan (a plan
    carrying that write-up's ref). The write-up is found by its own start
    inside the press's window, or through its binding's key or id when that
    write-up is near the press (`followup_draft.writeup_refs_for`, seventh
    check of #1814, MUST B). An unreadable store answers False here: the
    press goes on to `_write`, which sets the file aside and starts a new one
    (the set-aside line says so), and an unreadable book raises, so the press
    waits."""
    store, unread = load_store(workspace_root)
    if unread:
        return False
    planned = {_ref_id(r.get("ref")) for r in store.get("meetings") or [] if isinstance(r, dict) and _ref_id(r.get("ref"))}
    if not planned:
        return False
    import followup_draft as fd

    return bool(planned & fd.writeup_refs_for(workspace_root, key=key, meeting_id=mid, start=st,
                                              window=_press_window(st, end)))


def _gate(workspace_root, *, ref: Any, key: str, mid: str, st: str, end: Any,
          now: _dt.datetime, pressed_at: Any = None) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Rule R2's press gate: (refusal or None, the write-up's context or None).
    A press with a ref is accepted only when the ref's write-up is on the book
    and the press's meeting id is its notes' id, or its confirmed binding's key
    or id. A press without one, for a meeting that had ended when it was
    pressed (`_pressed`; its end, else its start), is refused, pointing to the
    meeting's page; so is one for an occurrence whose write-up already has its
    own plan (`_has_own_plan`; a meeting that ended early), whatever the
    calendar's end says. Not every shape is caught: a meeting whose own notes
    started well before its calendar start (more than 60 minutes with an
    unconfirmed binding, more than 15 with none) is not matched, so a Today
    press can land and then be ignored by rule 1 (a stated limit, decision
    147i, spec_1814_rule_R2.md)."""
    if norm_ref(ref):
        import followup_draft as fd

        ctx = fd.writeup_ctx(workspace_root, ref)
        if ctx is None:
            return {"outcome": "refused", "line": REF_REFUSED_LINE}, None
        b = ctx.get("binding") or {}
        if not (mid and (mid == norm_meeting_id(ctx.get("tail")) or (b and (mid == b.get("event_id") or (key and key == b.get("key")))))):
            return {"outcome": "refused", "line": REF_REFUSED_LINE}, None
        return None, ctx
    ended = _minute(end) or _minute(st)
    if ended is not None and ended <= _pressed(pressed_at, now):
        return {"outcome": "refused", "line": ENDED_LINE}, None
    if _has_own_plan(workspace_root, key, mid, st, end):
        return {"outcome": "refused", "line": OWN_PLAN_LINE}, None
    return None, None


def emails(plan: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The plan's chosen emails, each `{email_id, to, kind, instruction}`. Pure."""
    out = []
    for e in (plan or {}).get("emails") or []:
        if isinstance(e, dict) and EMAIL_ID_RE.match(str(e.get("email_id") or "")):
            to = [_pid(p) for p in e.get("to") or [] if _pid(p)]
            if to:
                out.append({"email_id": str(e["email_id"]), "to": to,
                            "kind": e.get("kind") if e.get("kind") in KINDS else DEFAULT_KIND,
                            "instruction": str(e.get("instruction") or "")[:INSTRUCTION_CAP]})
    return out


def email_of(plan: Optional[Dict[str, Any]], email_id: Any) -> Optional[Dict[str, Any]]:
    """The plan's email of that id, or None. Pure."""
    for e in emails(plan):
        if e["email_id"] == str(email_id or ""):
            return e
    return None


def removed_ids(plan: Optional[Dict[str, Any]]) -> set:
    """The people taken off the meeting's follow-up and not brought back. Pure."""
    return {_pid(r.get("person_id")) for r in (plan or {}).get("removed") or []
            if isinstance(r, dict) and _pid(r.get("person_id")) and not r.get("back_at")}


def _write(workspace_root, mutate, choice_id: Optional[str] = None,
           now_iso: Optional[str] = None) -> Dict[str, Any]:
    """The one write: under the store's lock, read, change, write whole. A
    press whose id is already in `applied` is not applied again."""
    path = plan_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_write_lock(path, holder="hub-meetplan")
    try:
        aside = ""
        try:
            store = _load_for_write(workspace_root)
        except ValueError:
            # review of #1814 (should-fix): an unreadable plan is a repair, not a
            # dead end. It is kept beside the store, never deleted, and the
            # owner's press starts a new one. Until a press comes, drafting stays
            # refused (the plan cannot be checked).
            aside = set_aside(workspace_root)
            if not aside:
                return {"outcome": "waiting", "line": STORE_UNREADABLE_LINE}
            # review 2 of #1814: the plans in it are lost, so no meeting they
            # could have been for is drafted until the owner chooses again
            store = _empty()
            store["lost"] = [{"file": aside, "at": _iso(_now(now_iso))}]
        if choice_id and choice_id in store["applied"]:
            return {"outcome": "already", "line": REPLAYED_LINE}
        result, changed = mutate(store)
        if aside and not changed:
            atomic_write_json(path, store)   # the lost marker lands even when the press writes nothing
        if changed:
            if choice_id:
                store["applied"] = (store["applied"] + [choice_id])[-APPLIED_CAP:]
            store["meetings"] = store["meetings"][-MEETINGS_CAP:]
            store["history"] = store["history"][-HISTORY_CAP:]
            store["lost"] = store["lost"][-LOST_CAP:]
            atomic_write_json(path, store)
        if aside:
            # review 2 of #1814: the owner is told, in the press's own answer
            result = dict(result, set_aside=aside, line=SET_ASIDE_LINE + " " + str(result.get("line") or ""))
        return result
    finally:
        release_write_lock(lock)


def set_aside(workspace_root) -> str:
    """WRITE (called under the store's lock). Move an unreadable plan file
    beside itself as `hub-meetplan.unreadable-<stamp>.json`, kept for the
    owner; answers its name, or "" when it could not be moved."""
    path = plan_path(workspace_root)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    dest = path.with_name(f"hub-meetplan.unreadable-{stamp}.json")
    try:
        path.replace(dest)
    except OSError:
        return ""
    return dest.name


def _change(store: Dict[str, Any], key: str, meeting_id: str, start: str, choice_id: Optional[str], now: str, edit,
            ref: str = "", ctx: Optional[Dict[str, Any]] = None, series: str = "", end: str = ""):
    """Run `edit(rec)` on the meeting's plan (made when there is none),
    recording the plan before and after for the press's Undo. `edit`
    answers (result, changed). Rule R2 of #1814: a press with a write-up's
    `ref` edits only that write-up's own plan (found by the ref, never by
    the press's key), made on its first press and seeded from the plans
    that could be its own (`seed`, over `ctx`); a press without one edits
    the Today plan, never a write-up's. A plan's start never moves."""
    me = _ref_id(ref)
    if me:
        rec = next((r for r in store.get("meetings") or [] if isinstance(r, dict) and _ref_id(r.get("ref")) == me), None)
    else:
        rec = find(store, key, meeting_id, start)
    before = copy.deepcopy(rec) if rec else None
    fresh = rec is None
    if fresh:
        rec = {"key": key, "meeting_id": meeting_id, "start": start, "at": now, "emails": [], "removed": []}
        if series:
            rec["series"] = series
        if end:
            rec["end"] = end
        if me:
            rec["ref"] = norm_ref(ref)
            rec["removed"], rec["emails"] = seed(store, ctx or {"ref": ref}, now)
    else:
        if key and not norm_key(rec.get("key")):
            rec["key"] = key
        if meeting_id and not norm_meeting_id(rec.get("meeting_id")):
            rec["meeting_id"] = meeting_id
        if series and "series" not in rec:
            rec["series"] = series
        if end and not rec.get("end"):
            rec["end"] = end
    result, changed = edit(rec)
    if not changed and fresh and me and result.get("outcome") != "refused" and (rec["removed"] or rec["emails"]):
        # the seeded plan is the write-up's own from now on, even when the press itself was a no-op; a
        # refused press writes nothing, seed included (sixth review of #1814: it answers "nothing was written")
        changed = True
    if not changed:
        return result, False
    rec["at"] = now
    if fresh:
        store["meetings"].append(rec)
    # review 3 of #1814: when the press changed the plan, add-only, so a hold made before it is known
    # to be superseded even when an Undo took the plan away whole
    store["history"].append({"choice_id": choice_id or "", "key": rec.get("key") or "",
                             "meeting_id": rec.get("meeting_id") or "", "start": rec.get("start") or "",
                             "at": now, "before": before, "after": copy.deepcopy(rec)})
    return result, True


# ---------------------------------------------------------------- the wall (read only)

def _wall(workspace_root) -> Optional[Dict[str, Any]]:
    """`{hidden, people, words}`: who may not be written to (off limits or a
    personal tie), the people on file by id, and every way a hidden person is
    written. None when any of it cannot be read (nothing is written then)."""
    import followup_draft as fd

    root = Path(str(workspace_root))
    hidden = fd._hidden(root)
    words = fd.hidden_words(root)
    if hidden is None or words is None:
        return None
    try:
        from entities_io import entities_collection, read_entities_json

        people = entities_collection(read_entities_json(root / "_hq" / "data" / "entities.json"), "people")
    except Exception:  # noqa: BLE001
        return None
    ids = {str(p.get("id")) for p in people if isinstance(p, dict) and p.get("id")}
    return {"hidden": hidden, "people": ids, "words": words, "owner": fd._owner(root)}


def _person_ok(wall: Dict[str, Any], pid: str) -> Optional[str]:
    """The refusal line for putting `pid` in a plan, or None when allowed."""
    if not pid or pid not in wall["people"] or pid == wall.get("owner"):
        return NO_PERSON_LINE
    if pid in wall["hidden"]:
        return HIDDEN_LINE
    return None


# ---------------------------------------------------------------- the presses

def _new_email_id(choice_id: Optional[str], now: str, rec: Dict[str, Any]) -> str:
    have = {str(e.get("email_id")) for e in rec.get("emails") or [] if isinstance(e, dict)}
    seed = str(choice_id or now)
    n = 0
    while True:
        eid = "e_" + hashlib.sha256(f"{seed}|{n}".encode("utf-8")).hexdigest()[:12]
        if eid not in have:
            return eid
        n += 1


def add(workspace_root, *, person_id: Any, meeting_key: Any = "", start: Any = "", meeting_id: Any = "", email_id: Any = "",
        kind: Any = "", choice_id: Optional[str] = None, now_iso: Optional[str] = None, ref: Any = "",
        series: Any = "", end: Any = "", pressed_at: Any = None) -> Dict[str, Any]:
    """WRITE. Put a person on one of the meeting's emails: the one named, or
    a new one. A person on another email of the meeting moves (one row per
    person). A person the owner removed comes back: this is the owner's own
    word. Someone not on the meeting is allowed (ruling 125e); someone off
    limits or with a personal tie never is."""
    key, mid, st, pid = norm_key(meeting_key), norm_meeting_id(meeting_id), norm_start(start), _pid(person_id)
    if not mid or not st:
        return {"outcome": "refused", "line": NO_MEETING_LINE}
    eid = str(email_id or "").strip()
    if eid and not EMAIL_ID_RE.match(eid):
        return {"outcome": "refused", "line": NO_EMAIL_LINE}
    k = str(kind or "").strip().lower()
    if k and k not in KINDS:
        return {"outcome": "refused", "line": UNKNOWN_KIND_LINE}
    if k == "own":
        return {"outcome": "refused", "line": OWN_EMPTY_LINE}
    wall = _wall(workspace_root)
    if wall is None:
        return {"outcome": "waiting", "line": WALL_UNREAD_LINE}
    bad = _person_ok(wall, pid)
    if bad:
        return {"outcome": "refused", "line": bad}
    refusal, ctx = _gate(workspace_root, ref=ref, key=key, mid=mid, st=st, end=end, now=_now(now_iso),
                         pressed_at=pressed_at)
    if refusal:
        return refusal
    now = _iso(_now(now_iso))

    def edit(rec):
        target = None
        for e in rec["emails"]:
            if isinstance(e, dict) and str(e.get("email_id")) == eid and eid:
                target = e
        back = False
        for r in rec["removed"]:
            if isinstance(r, dict) and _pid(r.get("person_id")) == pid and not r.get("back_at"):
                r["back_at"], r["back_by"] = now, choice_id or ""
                back = True
        if target is not None and pid in target.get("to", []) and not back:
            return {"outcome": "already", "line": ADDED_ALREADY_LINE}, False
        if target is None and len(rec["emails"]) >= EMAILS_CAP:
            return {"outcome": "refused", "line": TOO_MANY_LINE}, False
        if target is not None and pid not in target.get("to", []) and len(target.get("to", [])) >= TO_CAP:
            return {"outcome": "refused", "line": TOO_MANY_LINE}, False
        for e in rec["emails"]:
            if isinstance(e, dict) and e is not target:
                e["to"] = [p for p in e.get("to") or [] if _pid(p) != pid]
        rec["emails"] = [e for e in rec["emails"] if isinstance(e, dict) and e.get("to") or e is target]
        if target is None:
            target = {"email_id": eid or _new_email_id(choice_id, now, rec), "to": [],
                      "kind": k or DEFAULT_KIND, "instruction": "", "at": now, "choice_id": choice_id or ""}
            rec["emails"].append(target)
        if pid not in target["to"]:
            target["to"].append(pid)
        return {"outcome": "applied", "line": BACK_LINE if back else ADDED_LINE,
                "email_id": target["email_id"]}, True

    return _write(workspace_root, lambda store: _change(store, key, mid, st, choice_id, now, edit, ref, ctx, norm_series(series),
                                                       norm_start(end)), choice_id, now)


def remove(workspace_root, *, person_id: Any, meeting_key: Any = "", start: Any = "", meeting_id: Any = "",
           choice_id: Optional[str] = None, now_iso: Optional[str] = None, ref: Any = "",
           series: Any = "", end: Any = "", pressed_at: Any = None) -> Dict[str, Any]:
    """WRITE. Take a person off the meeting's follow-up: off every email of
    it, and recorded as removed, so no path drafts to them again until the
    owner adds them back. An attendee on no chosen email (the automatic
    run's default) can be removed too."""
    key, mid, st, pid = norm_key(meeting_key), norm_meeting_id(meeting_id), norm_start(start), _pid(person_id)
    if not mid or not st:
        return {"outcome": "refused", "line": NO_MEETING_LINE}
    if not pid:
        return {"outcome": "refused", "line": NO_PERSON_LINE}
    refusal, ctx = _gate(workspace_root, ref=ref, key=key, mid=mid, st=st, end=end, now=_now(now_iso),
                         pressed_at=pressed_at)
    if refusal:
        return refusal
    now = _iso(_now(now_iso))

    def edit(rec):
        live = any(isinstance(r, dict) and _pid(r.get("person_id")) == pid and not r.get("back_at")
                   for r in rec["removed"])
        on = any(isinstance(e, dict) and pid in (e.get("to") or []) for e in rec["emails"])
        if live and not on:
            return {"outcome": "already", "line": REMOVED_ALREADY_LINE}, False
        for e in rec["emails"]:
            if isinstance(e, dict):
                e["to"] = [p for p in e.get("to") or [] if _pid(p) != pid]
        rec["emails"] = [e for e in rec["emails"] if isinstance(e, dict) and e.get("to")]
        if not live:
            rec["removed"].append({"person_id": pid, "at": now, "choice_id": choice_id or ""})
        return {"outcome": "applied", "line": REMOVED_LINE}, True

    return _write(workspace_root, lambda store: _change(store, key, mid, st, choice_id, now, edit, ref, ctx, norm_series(series),
                                                       norm_start(end)), choice_id, now)


def set_kind(workspace_root, *, email_id: Any, kind: Any, instruction: Any = "", meeting_key: Any = "", start: Any = "",
             meeting_id: Any = "", choice_id: Optional[str] = None, now_iso: Optional[str] = None,
             ref: Any = "", series: Any = "", end: Any = "", pressed_at: Any = None) -> Dict[str, Any]:
    """WRITE. The kind of one chosen email; `own` carries the owner's own
    instruction, which may name nobody hidden."""
    key, mid, st = norm_key(meeting_key), norm_meeting_id(meeting_id), norm_start(start)
    if not mid or not st:
        return {"outcome": "refused", "line": NO_MEETING_LINE}
    k = str(kind or "").strip().lower()
    if k not in KINDS:
        return {"outcome": "refused", "line": UNKNOWN_KIND_LINE}
    words = " ".join(str(instruction or "").split())[:INSTRUCTION_CAP] if k == "own" else ""
    if k == "own" and not words:
        return {"outcome": "refused", "line": OWN_EMPTY_LINE}
    if words:
        import followup_draft as fd

        wall = _wall(workspace_root)
        if wall is None:
            return {"outcome": "waiting", "line": WALL_UNREAD_LINE}
        if fd.names_hidden([words], wall["words"]):
            return {"outcome": "refused", "line": NAMED_LINE}
    eid = str(email_id or "").strip()
    refusal, ctx = _gate(workspace_root, ref=ref, key=key, mid=mid, st=st, end=end, now=_now(now_iso),
                         pressed_at=pressed_at)
    if refusal:
        return refusal
    now = _iso(_now(now_iso))

    def edit(rec):
        target = next((e for e in rec["emails"] if isinstance(e, dict) and str(e.get("email_id")) == eid), None)
        if target is None:
            return {"outcome": "refused", "line": NO_EMAIL_LINE}, False
        if target.get("kind") == k and str(target.get("instruction") or "") == words:
            return {"outcome": "already", "line": KIND_ALREADY_LINE}, False
        target["kind"], target["instruction"] = k, words
        return {"outcome": "applied", "line": KIND_LINE.format(words=KIND_WORDS[k])}, True

    return _write(workspace_root, lambda store: _change(store, key, mid, st, choice_id, now, edit, ref, ctx, norm_series(series),
                                                       norm_start(end)), choice_id, now)


def undo(workspace_root, back_of: Any, *, choice_id: Optional[str] = None,
         now_iso: Optional[str] = None) -> Dict[str, Any]:
    """WRITE. Take back exactly the press `back_of`: the meeting's plan goes
    back to what it was before it, only while nothing changed it since (a
    later press is not undone with it)."""
    bid = str(back_of or "").strip()
    if not bid:
        return {"outcome": "refused", "line": UNDO_NO_PRESS_LINE}
    now = _iso(_now(now_iso))

    def mutate(store):
        rows = [h for h in store["history"] if isinstance(h, dict) and str(h.get("choice_id") or "") == bid]
        if not rows:
            return {"outcome": "refused", "line": UNDO_NOTHING_LINE}, False
        h = rows[-1]
        after = h.get("after") if isinstance(h.get("after"), dict) else {}
        # rule R2 of #1814: a write-up's own plan is found by its ref (`find` reads Today plans only)
        me = _ref_id(after.get("ref"))
        rec = next((r for r in store["meetings"] if isinstance(r, dict) and _ref_id(r.get("ref")) == me), None)             if me else find(store, after.get("key"), after.get("meeting_id"), after.get("start"))
        if rec is None or {k: v for k, v in rec.items() if k != "at"} != {k: v for k, v in after.items() if k != "at"}:
            return {"outcome": "refused", "line": UNDO_NOTHING_LINE}, False
        before = h.get("before")
        idx = store["meetings"].index(rec)
        if isinstance(before, dict):
            store["meetings"][idx] = copy.deepcopy(before)
        else:
            store["meetings"].pop(idx)
        store["history"].append({"choice_id": choice_id or "", "key": after.get("key") or "",
                                 "meeting_id": after.get("meeting_id") or "", "start": after.get("start") or "",
                                 "before": copy.deepcopy(rec),
                                 "after": copy.deepcopy(before) if isinstance(before, dict) else None,
                                 "undid": bid, "at": now})
        return {"outcome": "applied", "line": UNDONE_LINE}, True

    return _write(workspace_root, mutate, choice_id, now_iso)


def sequence(choices: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """The presses in the order they were pressed (`created_at`), strictly,
    an Undo included (review of #1814, must-fix 3: Remove, Undo, Remove ends
    with the person off, as pressed). Ties keep the page's order."""
    return sorted((c for c in choices or [] if isinstance(c, dict)), key=lambda c: str(c.get("created_at") or ""))


def apply_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None, *,
                  now_iso: Optional[str] = None) -> Dict[str, Any]:
    """`{ok, results: [{id, outcome, line}]}` for the plan's presses, each
    `{id, action, meeting_key, meeting_id, person_id, email_id, kind,
    instruction, back_of, created_at}` (`cockpit_helpers.plan_apply`'s
    `meetplan` list). One bad row never stops the rest."""
    results = []
    waited: Dict[str, int] = {}   # press id -> its place in results, for a press that waited
    for c in sequence(choices):
        verb = str(c.get("action") or "").strip().lower()
        cid = str(c.get("id") or "").strip() or None
        where = {"meeting_key": c.get("meeting_key"), "meeting_id": c.get("meeting_id"), "start": c.get("start"),
                 "ref": c.get("ref") or "", "series": c.get("series") or "", "end": c.get("end") or "",
                 # sixth review of #1814 (MUST 5): "ended" is judged at the press's own time
                 "pressed_at": c.get("created_at") or ""}
        try:
            if verb == "add":
                res = add(workspace_root, person_id=c.get("person_id"), email_id=c.get("email_id"),
                          kind=c.get("kind"), choice_id=cid, now_iso=now_iso, **where)
            elif verb == "remove":
                res = remove(workspace_root, person_id=c.get("person_id"), choice_id=cid, now_iso=now_iso, **where)
            elif verb == "kind":
                res = set_kind(workspace_root, email_id=c.get("email_id"), kind=c.get("kind"),
                               instruction=c.get("instruction"), choice_id=cid, now_iso=now_iso, **where)
            elif verb == "undo" and str(c.get("back_of") or "").strip() in waited:
                # review 2 of #1814: the press it takes back waited (nothing was
                # written), so both end here: the waiting press is never
                # retried and landed after its Undo
                at = waited.pop(str(c.get("back_of")).strip())
                results[at] = dict(results[at], outcome="already", line=TAKEN_BACK_LINE)
                res = {"outcome": "applied", "line": UNDONE_LINE}
            elif verb == "undo":
                res = undo(workspace_root, c.get("back_of"), choice_id=cid, now_iso=now_iso)
            else:
                res = {"outcome": "refused", "line": UNKNOWN_VERB_LINE.format(verb=verb or "none")}
        except Exception as exc:  # noqa: BLE001 - one bad row never stops the rest
            res = {"outcome": "waiting", "line": STORE_UNREADABLE_LINE, "error": f"{type(exc).__name__}: {exc}"[:240]}
        res = dict(res)
        res["id"] = c.get("id")
        if res.get("outcome") == "waiting" and cid:
            waited[cid] = len(results)
        results.append(res)
    return {"ok": True, "results": results}


__all__ = ["PLAN_REL", "VERBS", "KINDS", "KIND_WORDS", "DEFAULT_KIND", "plan_path", "load_store", "find",
           "plan_for", "plan_lookup", "resolve", "candidates", "related", "seed", "confirmed", "emails", "email_of", "removed_ids", "add", "remove", "set_kind", "undo", "sequence",
           "apply_choices", "norm_key", "norm_meeting_id", "norm_start", "set_aside"]
