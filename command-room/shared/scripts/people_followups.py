#!/usr/bin/env python3
"""people_followups.py: the Hub's People follow-ups list and the priorities'
states (cr1#1789, v6.12 slice 6). `build` is the `feed/pfollow` document,
built at every Update (`cockpit_helpers.feed_pfollow`).

WHAT IT RANKS (owner rulings 125f and 125j, 2026-11-24)
Follow-ups are the pending ones the owner should do, plus the important
people the owner has not followed up with in a while:

  draft    a meeting's follow-up is drafted and not sent: one row per kept
           draft still waiting (`followup_draft.feed_followups`, which has
           already checked the wall). Button: Open draft (the draft card
           carries the two-press Send).
  process  a meeting ended and was not written up: a past meeting whose
           wrap-up reads `unwritten` (`hub_meeting.wrap_state`). Button:
           Process.
  quiet    an important person last in touch long enough ago that the
           book's one dormancy rule flags it (`dormancy.normalize_score`
           with no baseline: 14 days or more). Button: Draft, or Book once
           the gap reaches BOOK_AFTER_DAYS (dormancy's top tier).

A meeting coming up with no prep is not a follow-up (not ruled in).

IMPORTANCE (only signals already in the book; each row says which one)
  starred       the owner starred the person (`people_view` star store).
  relationship  the person's record says how they are known with a key
                word (`KEY_RELATIONSHIP_WORDS`, read from the record's
                relationship, category and related fields through
                `people_view._how_known`).
  open_items    something confirmed is open between the owner and them
                (`n_open` on their People row).
  pending       a draft or an unprocessed meeting is itself something to do;
                it ranks at the open-items tier unless someone on it ranks
                higher.
A quiet person with none of the first three is not listed: the ruling is
"important people", so time quiet alone never puts anyone here.

ORDER: importance tier, then time quiet (longest first), then the name.
The first TOP_N rows are `shown`; the rest go in `more` (Show more). Each
person appears once: someone on a pending row is not listed again as quiet.

PRIORITIES: `priority_state` turns a priority's recorded last action (the
`last_action` `_feed_priorities` already builds) into moving, quiet this
week or no signal.

stdlib only. Reads only; writes nothing.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

#: The rows shown before "Show more" (ruling 125f/125j, the design's three).
TOP_N = 3
#: A quiet person's button turns from Draft to Book at this many days: the
#: dormancy rule's top tier (`dormancy.normalize_score`, 60 days and over).
BOOK_AFTER_DAYS = 60
#: Words in how a person is known that make them a key relationship.
KEY_RELATIONSHIP_WORDS = ("client", "customer", "partner", "investor", "board", "advisor", "adviser")
#: A priority with a recorded action this many days back or fewer is moving.
MOVING_DAYS = 7

TIERS = {"starred": 0, "relationship": 1, "open_items": 2, "pending": 2}
SIGNAL_WORDS = {"starred": "starred", "relationship": "key relationship ({word})",
                "open_items": "open items between you", "pending": "waiting on you"}
BUTTONS = {"draft": {"verb": "open_draft", "label": "Open draft"},
           "process": {"verb": "process", "label": "Process"},
           "quiet_draft": {"verb": "draft", "label": "Draft"},
           "quiet_book": {"verb": "book", "label": "Book"},
           "open_prep": {"verb": "open_prep", "label": "Open prep"}}
#: The rows `feed/pfollow` carries (review round 1 of #1820): the document
#: stays far under the size limit on any book; `n` says how many there are.
ROWS_CAP = 50
#: A key relationship's Open prep looks this far ahead (the issue's "in the next day").
PREP_AHEAD_HOURS = 24
STATE_WORDS = {"moving": "moving", "quiet": "quiet this week", "no_signal": "no signal"}
UNREAD_LINE = "Your follow-ups could not be listed just now: who is off limits could not be read. The next refresh tries again."


def _date(value: Any) -> Optional[_dt.date]:
    s = str(value or "").strip()
    if len(s) < 10:
        return None
    try:
        return _dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _moment(value: Any) -> Optional[_dt.datetime]:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def _local_day(value: Any, tzinfo) -> Optional[_dt.date]:
    """The workspace's own calendar day of a timestamp (a bare date is that
    day); with no zone given, the timestamp's own day as written."""
    at = _moment(value) if len(str(value or "").strip()) > 10 else None
    if at is not None and tzinfo is not None:
        return at.astimezone(tzinfo).date()
    return _date(value)


def _today(now_iso: Optional[str], tzinfo) -> _dt.date:
    """The workspace's today at `now_iso` (#1795 review: never the UTC day)."""
    now = _moment(now_iso) or _dt.datetime.now(_dt.timezone.utc)
    return now.astimezone(tzinfo).date() if tzinfo is not None else now.date()


def _quiet_flag(days: int) -> bool:
    """The book's one dormancy rule decides "a while" (no baseline)."""
    try:
        from dormancy import normalize_score
    except Exception:  # pragma: no cover
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from dormancy import normalize_score  # type: ignore
    return normalize_score(float(days), None) is not None


def importance(person: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The strongest importance signal on a People row, `{signal, tier,
    why}`, or None when the book gives none. Pure."""
    if person.get("starred"):
        return {"signal": "starred", "tier": TIERS["starred"], "why": SIGNAL_WORDS["starred"]}
    words = set("".join(ch if ch.isalnum() else " " for ch in str(person.get("how_known") or "").lower()).split())
    for word in KEY_RELATIONSHIP_WORDS:
        if word in words or word + "s" in words:
            return {"signal": "relationship", "tier": TIERS["relationship"],
                    "why": SIGNAL_WORDS["relationship"].format(word=word)}
    try:
        n_open = int(person.get("n_open") or 0)
    except (TypeError, ValueError):
        n_open = 0
    if n_open > 0:
        return {"signal": "open_items", "tier": TIERS["open_items"], "why": SIGNAL_WORDS["open_items"]}
    return None


def _pending_importance(ids: Iterable[str], by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    best = {"signal": "pending", "tier": TIERS["pending"], "why": SIGNAL_WORDS["pending"]}
    for pid in ids:
        p = by_id.get(str(pid))
        imp = importance(p) if p else None
        if imp and imp["tier"] < best["tier"]:
            best = dict(imp, why=f"{imp['why']}: {p.get('name') or ''}".rstrip(": "))
    return best


def rank(people: List[Dict[str, Any]], drafts: List[Dict[str, Any]], past: List[Dict[str, Any]],
         *, now_iso: str, tzinfo=None) -> Dict[str, Any]:
    """PURE. The follow-ups list from rows the book already builds:
    `people` (People rows, `people_view.people`, fenced), `drafts`
    (`feed_followups`' waiting drafts, fenced), `past` (the today feed's
    ended meetings with their `wrap`). Days are counted on the workspace's
    calendar (`tzinfo`, the workspace's zone; UTC when not given). Returns
    `{rows, shown, more, n, n_more}`; each row `{kind, title, people:
    [ids], quiet_days, signal, tier, why, button}`."""
    now = _moment(now_iso) or _dt.datetime.now(_dt.timezone.utc)
    today = _today(now_iso, tzinfo)
    by_id = {str(p.get("id")): p for p in people if isinstance(p, dict) and p.get("id")}
    rows: List[Dict[str, Any]] = []
    taken: set = set()

    # cr1#1789 (127h): `feed/followups` is one row per draft. Each person is
    # listed once, at their newest unsent draft; a draft keeps only the people
    # no newer draft took, shown once under the one who ranks highest, with
    # "+N" (`n_others`) for the rest. Newest first by `created_at`, then the
    # feed's own order (newest first) for drafts that carry none.
    on_draft: set = set()
    order = sorted(enumerate(d for d in drafts or [] if isinstance(d, dict)),
                   key=lambda t: (str(t[1].get("created_at") or ""), -t[0]), reverse=True)
    for _i, d in order:
        to = [t for t in d.get("to") or [] if isinstance(t, dict) and t.get("id")]
        names = {str(t.get("id")): str(t.get("name") or "") for t in to}
        ids = [pid for pid in dict.fromkeys(str(t.get("id")) for t in to) if pid not in on_draft]
        if not ids:
            continue
        on_draft |= set(ids)
        ids.sort(key=lambda pid: ((importance(by_id[pid]) or {"tier": TIERS["pending"]})["tier"] if pid in by_id
                                  else TIERS["pending"], (names.get(pid) or "").lower(), pid))
        day = _local_day(d.get("start"), tzinfo)
        imp = _pending_importance(ids, by_id)
        taken |= set(ids)
        did = str(d.get("draft_id") or "")
        wkey = f"{d.get('meeting_id') or ''}|{d.get('start') or ''}"
        rows.append({"kind": "draft", "id": "draft:" + (did or wkey), "title": str(d.get("title") or "")[:200],
                     "meeting_id": d.get("meeting_id") or "", "draft_id": did, "ref": str(d.get("ref") or ""),
                     "start": str(d.get("start") or ""),
                     "meeting_key": str(d.get("meeting_key") or ""), "wrap_key": wkey,
                     "name": (by_id.get(ids[0]) or {}).get("name") or names.get(ids[0]) or "",
                     "people": ids, "n_others": len(ids) - 1, "quiet_days": (today - day).days if day else 0,
                     **imp, "button": dict(BUTTONS["draft"])})

    for m in past or []:
        if not isinstance(m, dict):
            continue
        wrap = m.get("wrap") if isinstance(m.get("wrap"), dict) else {}
        if wrap.get("state") != "unwritten":
            continue
        end = _moment(m.get("end"))
        if end is None or end > now or m.get("solo") or m.get("calendar") == "personal":
            continue
        ids = [str(x) for x in m.get("people") or [] if str(x).strip()]
        imp = _pending_importance(ids, by_id)
        taken |= set(ids)
        wkey = f"{m.get('meeting_id') or ''}|{m.get('start') or ''}"
        rows.append({"kind": "process", "id": "process:" + wkey, "title": str(m.get("title") or "")[:200],
                     "meeting_id": m.get("meeting_id") or "", "wrap_key": wkey,
                     "meeting_key": str(m.get("meeting_key") or ""),
                     "people": ids, "quiet_days": max(0, (today - _local_day(m.get("end"), tzinfo)).days),
                     **imp, "button": dict(BUTTONS["process"])})

    for pid, p in by_id.items():
        if pid in taken:
            continue
        imp = importance(p)
        if imp is None:
            continue
        last = _date(p.get("last_contact"))
        if last is None or not p.get("email"):
            continue
        days = (today - last).days
        if not _quiet_flag(days):
            continue
        button = BUTTONS["quiet_book"] if days >= BOOK_AFTER_DAYS else BUTTONS["quiet_draft"]
        rows.append({"kind": "quiet", "id": "quiet:" + pid, "title": str(p.get("name") or "")[:200],
                     "name": str(p.get("name") or "")[:200], "people": [pid],
                     "quiet_days": days, **imp, "button": dict(button)})

    rows.sort(key=lambda r: (r["tier"], -int(r["quiet_days"] or 0), r["title"].lower()))
    return {"rows": rows, "shown": rows[:TOP_N], "more": rows[TOP_N:], "n": len(rows),
            "n_more": max(0, len(rows) - TOP_N)}


def _name_key(value: Any) -> str:
    return " ".join("".join(ch if ch.isalnum() else " " for ch in str(value or "").casefold()).split())


def join_attendees(names: Iterable[Any], people: List[Dict[str, Any]]) -> List[str]:
    """PURE (open item 1 of #1795). A meeting row names its other people by
    name only (`people`, from the calendar read). Each name is matched to a
    row of the fenced People feed by its whole name, case and punctuation
    aside, and only when exactly one person on file has that name. Someone
    off limits or personal is not in that feed, so is never matched."""
    by_name: Dict[str, List[str]] = {}
    for p in people or []:
        if isinstance(p, dict) and p.get("id"):
            by_name.setdefault(_name_key(p.get("name")), []).append(str(p["id"]))
    out: List[str] = []
    for n in names or []:
        hit = by_name.get(_name_key(n)) or []
        if _name_key(n) and len(hit) == 1 and hit[0] not in out:
            out.append(hit[0])
    return out


def key_relationships(people: List[Dict[str, Any]], rows: List[Dict[str, Any]],
                      upcoming: List[Dict[str, Any]], *, now_iso: str) -> List[Dict[str, Any]]:
    """PURE. The starred people (key relationships), in the People feed's
    order: `{id, name, last_contact, n_open}`, and a `button` only when there
    is something to do with them: Open prep when a meeting with them in the
    next PREP_AHEAD_HOURS has its prep (`meet_key`, the page's own key for
    the meeting), else the button of their follow-up row (`row_id`)."""
    now = _moment(now_iso) or _dt.datetime.now(_dt.timezone.utc)
    horizon = now + _dt.timedelta(hours=PREP_AHEAD_HOURS)
    out: List[Dict[str, Any]] = []
    for p in people or []:
        if not (isinstance(p, dict) and p.get("id") and p.get("starred")):
            continue
        pid = str(p["id"])
        row: Dict[str, Any] = {"id": pid, "name": str(p.get("name") or "")[:200],
                               "last_contact": p.get("last_contact") or None, "n_open": p.get("n_open") or 0}
        for m in upcoming or []:
            start = _moment(m.get("start"))
            if not m.get("prep") or start is None or not (now <= start <= horizon):
                continue
            if pid in join_attendees(m.get("people") or [], people):
                row["button"] = dict(BUTTONS["open_prep"])
                row["meet_key"] = f"{m.get('start') or ''}|{m.get('title') or ''}"
                break
        if "button" not in row:
            mine = next((r for r in rows if pid in (r.get("people") or [])), None)
            if mine:
                row["button"] = dict(mine["button"])
                row["row_id"] = mine["id"]
        out.append(row)
    return out


def importance_map(people: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """PURE. `{person id: {tier, why}}` for every person in the fenced People
    feed the book calls important (`importance`): the page ranks the
    meetings it joins from its own Today feed by these, never by a second
    copy of the rule. Ids only, no names (the page has them already)."""
    out: Dict[str, Dict[str, Any]] = {}
    for p in people or []:
        if isinstance(p, dict) and p.get("id"):
            imp = importance(p)
            if imp:
                out[str(p["id"])] = {"tier": imp["tier"], "why": imp["why"]}
    return out


def page_rules() -> Dict[str, Any]:
    """The rule's numbers the page needs for what it joins itself: a pending
    row with nobody important on it (`pending`), how far ahead Open prep
    looks and how many rows show before Show more."""
    return {"pending": {"tier": TIERS["pending"], "why": SIGNAL_WORDS["pending"]},
            "prep_ahead_hours": PREP_AHEAD_HOURS, "top_n": TOP_N}


def bar_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """PURE. The People bar's one line: how many need action (a draft or a
    meeting to process) and how many are going quiet."""
    return {"need_action": sum(1 for r in rows if r.get("kind") in ("draft", "process")),
            "going_quiet": sum(1 for r in rows if r.get("kind") == "quiet")}


def build(workspace_root, *, now_iso: Optional[str] = None,
          people_feed: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """READ. The `feed/pfollow` document (cr1#1789): the follow-ups list (the
    drafts not sent and the important people gone quiet), the key
    relationships (`key`) and the bar's counts (`counts`), from every person
    on file, fenced (`people_feed` in tests), and the waiting follow-up
    drafts. It reads no Today feed (feed-shape audit of #1820): the meetings
    to process and Open prep are joined on the page from its own Today feed,
    ranked by `importance` (each important person's tier and words, from
    this same rule), so the order rule lives here once. `rank` still takes
    ended meetings when a caller gives them; this never does. `ok: False`
    and nothing listed when the wall or the drafts cannot be read."""
    import cockpit_helpers as ch
    import followup_draft as fd
    import people_view

    import open_list as ol

    now = ch._iso(ch._now(now_iso))
    root = Path(str(workspace_root))
    # #1795 review: the doc says when it was built, the field the page's
    # reconcile reads, on every answer.
    empty = {"ok": False, "generated_at": now, "rows": [], "n": 0, "n_held": 0,
             "key": [], "counts": {"need_action": 0, "going_quiet": 0}, "importance": {}, "line": UNREAD_LINE}
    if isinstance(people_feed, dict):
        feed = people_feed
    else:
        # review round 1 of #1820: every person on file, fenced, not the People
        # tab's first PEOPLE_CAP (most recent first): the quietest sort last
        hp = ch._hidden_people(workspace_root)
        if hp.get("unread"):
            return empty
        feed = ch._hide_people_in_feed(people_view.people(workspace_root, now_iso=now, hidden_ids=set(hp.get("ids") or ()),
                                                          cap=None), hp)
    if (feed.get("not_shown") or {}).get("unread") or "fence" in (feed.get("errors") or []):
        return empty
    if fd.hidden_words(root) is None:
        return empty
    drafts = fd.feed_followups(workspace_root, now_iso=now)
    if not drafts.get("ok"):
        return empty
    people = [p for p in feed.get("people") or [] if isinstance(p, dict)]
    # #1795 review: days on the workspace's own calendar, the resolver the
    # plate and the People tab share (`open_list._workspace_tz`)
    out = rank(people, drafts.get("drafts") or [], [], now_iso=now, tzinfo=ol._workspace_tz(root))
    rows = out["rows"]
    # the page joins names against the People tab's rows only, so the map stops where that list does
    return {"ok": True, "generated_at": now, "rows": rows[:ROWS_CAP], "n": len(rows),
            "n_held": int(drafts.get("n_held") or 0), "importance": importance_map(people[:people_view.PEOPLE_CAP]),
            **page_rules(), "key": key_relationships(people, rows, [], now_iso=now), "counts": bar_counts(rows)}


def priority_state(last_action: Optional[Dict[str, Any]], today: _dt.date) -> str:
    """PURE. moving / quiet / no_signal from a priority's `last_action`
    (`cockpit_helpers._priority_last_action`): no recorded action is no
    signal; one in the last MOVING_DAYS days is moving; older is quiet."""
    at = _date((last_action or {}).get("at"))
    if at is None:
        return "no_signal"
    return "moving" if (today - at).days < MOVING_DAYS else "quiet"


def priority_states(priorities: List[Dict[str, Any]], *, now_iso: str, tzinfo=None) -> Dict[str, Any]:
    """PURE. Each confirmed priority (`_feed_priorities`' `confirmed`) with
    its `state` and the words, plus the counts for the closed line; today is
    the workspace's (`tzinfo`; UTC when not given)."""
    today = _today(now_iso, tzinfo)
    rows = []
    counts = {k: 0 for k in STATE_WORDS}
    for p in priorities or []:
        if not isinstance(p, dict):
            continue
        st = priority_state(p.get("last_action") if isinstance(p.get("last_action"), dict) else None, today)
        counts[st] += 1
        rows.append({"id": str(p.get("id") or ""), "state": st, "words": STATE_WORDS[st]})
    return {"rows": rows, "counts": counts}


__all__ = ["TOP_N", "ROWS_CAP", "BOOK_AFTER_DAYS", "KEY_RELATIONSHIP_WORDS", "MOVING_DAYS", "PREP_AHEAD_HOURS", "importance",
           "rank", "join_attendees", "key_relationships", "importance_map", "bar_counts", "build", "priority_state", "priority_states"]
