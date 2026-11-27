#!/usr/bin/env python3
"""The Hub's Urgent list (cr1#1786): immediate incoming communication from the connected apps, ranked.

Owner ruling 125k (2026-11-24): Urgent is someone needing something, asking
for something, or needing an answer as soon as possible, read from what came
in (mail now; Slack when cr1#1767 adds it; calendar invites waiting on the
owner). It is never built from late commitments: late is not urgent.

The first cut of the ranking, from #1786, is four fixed signals on a live
incoming item:

  request   a direct question or request to the owner (the latest message
            asks something);
  deadline  a deadline stated in the message that falls today or tomorrow
            (or "as soon as possible", "end of day" and the like);
  starred   the sender is a person starred on the People tab;
  invite    a calendar invite still waiting on the owner's answer.

An item with none of them is not urgent, and a copy (the owner on Cc
only) carries none: it is addressed to someone else. Noise (the inbox read's own
`noise` kind, a noise class, a suppressed sibling, a thread whose newest
message is the owner's, a run verdict that it needs nothing) never ranks,
whatever its words say, and nobody off limits or tied to the personal side
is ever listed.

Waiting on others (owner ruling 130f, 2026-11-24, replacing 125m's
empty-only rule): up to three things other people owe the owner that are
late or due today, each marked for a chase draft (the draft itself is
email-writer's, at the press), ALWAYS show under Urgent, under the urgent
rows on a busy day as well as on an empty one. They are their own list
(`chase`), never ranked among the rows. When nothing urgent came in, one
line leads (ruling 125m): "Nothing urgent" with the free time until the
next meeting. When there is nothing either way, the answer says so
(`open_todo`), so the page opens To do in the space. It never repeats Focus
(what the owner owes), the next meeting itself (Calendar) or People
follow-ups.

`rank_urgent` is the ranking on plain inputs and reads nothing;
`urgent_view` asks the workspace for the stars, the owner's own addresses,
the people not to name and the open owed-to-you items, then ranks. Both are
READS: nothing here writes, fetches or sends.

Wired (cr1#1786): `cockpit_helpers.feed_urgent` publishes `feed/urgent` at
every Update from the inbox part's store (`hub_urgent_store`, ruling 127f),
whose saved items carry what `signals` reads in place of the text; the page
draws it in `#urgent` and lays its own live lines over it (136c).
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

#: The most rows the page shows before "Show N more" (128o).
URGENT_CAP = 5
#: Feed-shape audit (U2): the feed carries the ranked rows up to this many, so
#: the page's "Show N more" folds rows it holds; past it, `n_more` counts them.
URGENT_FEED_CAP = 20
#: The most waiting-on-others rows under Urgent (ruling 130f).
CHASE_CAP = 3
#: Review round 1 of #1819: the feed carries this many owed rows, so the page
#: can take out what Focus holds before it shows `CHASE_CAP` of them.
CHASE_SEND_CAP = 8
#: The first cut's signals and their weight, strongest first. A row's score is
#: the sum of its signals; ties go to the one that has waited longest.
SIGNAL_POINTS = {"deadline": 40, "request": 30, "invite": 25, "starred": 20}
SIGNAL_ORDER = ("deadline", "request", "invite", "starred")
#: Plain words for each signal, joined into the row's `why`.
SIGNAL_WORDS = {
    "deadline": "a deadline {when}",
    "request": "asks you directly",
    "invite": "an invite waiting on your answer",
    "starred": "from someone you starred",
}
#: The version of the ranking, so a later widening can be told apart.
RANKING = "cr1#1786 first cut"

NOTHING_URGENT_FREE_LINE = "Nothing urgent. {free} free before your next meeting."
NOTHING_URGENT_CLEAR_LINE = "Nothing urgent, and no more meetings today."
#: Review round 1 of #1819: an Urgent held because the off-limits list could
#: not be read never says "Nothing urgent".
URGENT_HELD_LINE = "Urgent names nobody just now: the off-limits list could not be loaded. The next Update tries again."

#: Invite answers that still wait on the owner. Anything else (accepted,
#: declined, tentative) has been answered.
INVITE_PENDING = frozenset({"", "needsaction", "needs_action", "pending", "none", "noresponse",
                            "no_response", "notresponded", "not_responded"})

#: Review round 1 of #1819: a question is a sentence of at least three words
#: that ends in a question mark, outside a link and outside quoted text; a
#: bare "?" (a link's query, "Re: ?", a quoted line) asks nothing.
_QUESTION_SENTENCE_RE = re.compile(r"(?:^|[.!?\n])\s*(?:[^\s.!?\n]+\s+){2,}[^\s.!?\n]+\s*\?")
_LINK_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def asks(text: str) -> bool:
    """Whether `text` asks the owner for something: a real question or a
    request. Feed-shape audit (U6): the one rule, which the store keeps on
    each item (`hub_urgent_store.project`) and the live path reads."""
    return bool(asks_question(text) or _REQUEST_RE.search(str(text or "")))


def asks_question(text: str) -> bool:
    """Whether `text` holds a real question (review round 1 of #1819)."""
    lines = [ln for ln in str(text or "").splitlines() if not ln.lstrip().startswith(">")]
    return bool(_QUESTION_SENTENCE_RE.search(_LINK_RE.sub(" ", "\n".join(lines))))
_REQUEST_RE = re.compile(
    r"\b(?:can|could|would|will) you\b|\bplease (?!see\b|find\b|note\b|ignore\b|disregard\b)|\blet me know\b|\bneed (?:you|your)\b"
    r"|\bare you (?:able|free|available)\b|\bdo you (?:have|know|want|mind)\b"
    r"|\bwhen can\b|\bany update\b|\bsign(?:ed)? off\b|\bapprove\b|\bconfirm\b",
    re.IGNORECASE)
#: Words that ask for an answer now, with no date: always within a day.
_NOW_RE = re.compile(
    r"\basap\b|\bas soon as (?:possible|you can)\b|\burgent(?:ly)?\b|\bright away\b"
    r"|\bimmediately\b|\b(?:by |before )?(?:the )?end of (?:the )?day\b|\beod\b|\bcob\b"
    r"|\bclose of business\b|\bwithin the hour\b|\bin the next (?:hour|few hours)\b",
    re.IGNORECASE)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
#: A deadline cue followed by when: "by tomorrow", "due Friday", "before Nov 25",
#: "deadline is 11/25", "no later than today".
_CUE = r"(?:\bby|\bbefore|\bdue(?: on| by)?|\bdeadline(?: is| of)?:?|\bno later than|\buntil)"
_WHEN = (r"(?P<when>today|tonight|this (?:morning|afternoon|evening)|tomorrow(?: morning| afternoon)?"
         r"|(?:this |next )?(?:" + "|".join(_WEEKDAYS) + r")"
         r"|(?:" + "|".join(_MONTHS) + r")[a-z]*\.? \d{1,2}(?:st|nd|rd|th)?"
         r"|\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2})")
_DEADLINE_RE = re.compile(_CUE + r"\s+" + _WHEN + r"\b", re.IGNORECASE)


def _parse_ts(value: Any) -> Optional[_dt.datetime]:
    if not value:
        return None
    if isinstance(value, _dt.datetime):
        ts = value
    else:
        try:
            ts = _dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return ts if ts.tzinfo else ts.replace(tzinfo=_dt.timezone.utc)


def _addr(value: Any) -> str:
    """One address, lower case, from `Name <a@b>` or a bare address."""
    text = str(value or "").strip()
    m = re.search(r"<([^>]+)>", text)
    return (m.group(1) if m else text).strip().lower()


def _addrs(value: Any) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        parts: Iterable[Any] = value
    else:
        parts = re.split(r"[,;]", str(value or ""))
    return [a for a in (_addr(p) for p in parts) if "@" in a]


def deadline_date(text: str, sent_on: _dt.date) -> Optional[_dt.date]:
    """The day a deadline stated in `text` falls on, read against the day the
    message was sent; None when the text states none. "As soon as possible",
    "end of day" and the like fall on the day it was sent."""
    text = str(text or "")
    found: List[_dt.date] = []
    if _NOW_RE.search(text):
        found.append(sent_on)
    for m in _DEADLINE_RE.finditer(text):
        when = m.group("when").lower()
        day: Optional[_dt.date] = None
        if when.startswith(("today", "tonight", "this morning", "this afternoon", "this evening")):
            day = sent_on
        elif when.startswith("tomorrow"):
            day = sent_on + _dt.timedelta(days=1)
        elif any(w in when for w in _WEEKDAYS):
            target = next(i for i, w in enumerate(_WEEKDAYS) if w in when)
            ahead = (target - sent_on.weekday()) % 7
            if when.startswith("next "):
                ahead = ahead + 7 if ahead == 0 else ahead
            day = sent_on + _dt.timedelta(days=ahead)
        elif re.match(r"\d{4}-\d{2}-\d{2}$", when):
            try:
                day = _dt.date.fromisoformat(when)
            except ValueError:
                day = None
        elif "/" in when:
            bits = [int(b) for b in when.split("/")]
            year = bits[2] if len(bits) == 3 else sent_on.year
            year = year + 2000 if year < 100 else year
            try:
                day = _dt.date(year, bits[0], bits[1])
            except ValueError:
                day = None
            if day and len(bits) == 2 and day < sent_on - _dt.timedelta(days=180):
                day = day.replace(year=day.year + 1)
        else:
            mon = _MONTHS.index(when[:3])
            num = int(re.search(r"\d{1,2}", when).group(0))
            try:
                day = _dt.date(sent_on.year, mon + 1, num)
            except ValueError:
                day = None
            if day and day < sent_on - _dt.timedelta(days=180):
                day = day.replace(year=day.year + 1)
        if day is not None:
            found.append(day)
    return min(found) if found else None


def _stamp(when: _dt.datetime) -> str:
    """The Hub feeds' own stamp (`cockpit_helpers._iso`): UTC, whole seconds,
    `Z`. The page compares stamps as text, so a local offset would sort
    wrong against a press's `applied_at`."""
    return when.astimezone(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _waited_words(minutes: int) -> str:
    if minutes < 60:
        n = max(1, minutes)
        return f"{n} minute{'s' if n != 1 else ''}"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = hours // 24
    return f"{days} days"


def is_noise(item: Dict[str, Any], own: Iterable[str] = ()) -> bool:
    """True for an item the inbox read already set aside or that needs
    nothing from the owner: noise, a suppressed sibling, the owner's own
    newest message, or a run verdict of `needs: false`."""
    if str(item.get("kind") or "") == "noise" or item.get("noise_class"):
        return True
    if item.get("suppressed") or item.get("from_me"):
        return True
    if item.get("needs") is False:
        return True
    mine = {_addr(a) for a in own or ()}
    sender = _addr(item.get("sender_email") or item.get("sender"))
    return bool(sender and sender in mine)


def signals(item: Dict[str, Any], *, own: Iterable[str] = (), starred_ids: Iterable[str] = (),
            starred_addresses: Iterable[str] = (), today: _dt.date,
            tzinfo: _dt.tzinfo = _dt.timezone.utc, now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """The first cut's signals on one live item: `{names, deadline}`, the
    signal names in `SIGNAL_ORDER` and the deadline's day when one counts.

    A saved item (`hub_urgent_store`, ruling 127f) keeps no text: it carries
    what the text said instead, `asks` (a question or request), `deadline_on`
    (the stated deadline's day, ISO, or None) and `copy` (the owner on Cc
    only). When those fields are present they are used, so a saved item ranks
    exactly as the live one did, and the deadline window is read against
    today again."""
    mine = {_addr(a) for a in own or ()}
    kind = str(item.get("kind") or "")
    names: List[str] = []
    text = " ".join(str(item.get(k) or "") for k in ("subject", "body"))
    ts = _parse_ts(item.get("ts") or item.get("message_ts"))
    sent_on = ts.astimezone(tzinfo).date() if ts else today
    if "deadline_on" in item:
        try:
            due = _dt.date.fromisoformat(str(item.get("deadline_on") or "")[:10]) if item.get("deadline_on") else None
        except ValueError:
            due = None
    else:
        due = deadline_date(text, sent_on)
    deadline = due if (due is not None and today <= due <= today + _dt.timedelta(days=1)) else None
    if deadline is not None:
        names.append("deadline")
    if kind != "calendar_invite":
        to = _addrs(item.get("to"))
        cc = _addrs(item.get("cc"))
        # A copy (the owner on Cc and not on To) is addressed to someone
        # else, so it carries no signal at all. Anything else the inbox read
        # brought in (the owner on To, or on neither line: a Bcc, an alias)
        # counts as addressed to the owner; with no own addresses to tell the
        # owner by, nothing reads as a copy.
        copy = (item.get("copy") is True) if "copy" in item else (bool(mine & set(cc)) and not (mine & set(to)))
        if copy:
            return {"names": [], "deadline": None}
        asks_ = (item.get("asks") is True) if "asks" in item else asks(text)
        if asks_:
            names.append("request")
    else:
        answer = re.sub(r"[\s-]", "", str(item.get("response") or item.get("rsvp") or "")).lower()
        start = _parse_ts(item.get("event_start"))
        over = bool(start and now and start <= now)
        if answer in INVITE_PENDING and not over:
            names.append("invite")
    pid = str(item.get("sender_person_id") or "")
    sender = _addr(item.get("sender_email") or item.get("sender"))
    if (pid and pid in set(starred_ids or ())) or (sender and sender in {_addr(a) for a in starred_addresses or ()}):
        names.append("starred")
    return {"names": [s for s in SIGNAL_ORDER if s in names], "deadline": deadline}


def _why(names: List[str], deadline: Optional[_dt.date], today: _dt.date) -> str:
    when = "today" if deadline == today else "tomorrow"
    words = [SIGNAL_WORDS[s].format(when=when) for s in names]
    text = ", ".join(words)
    return text[:1].upper() + text[1:] + "."


def _owed_rows(owed: Iterable[Dict[str, Any]], *, owner_id: str, today: _dt.date,
               hidden_ids: Iterable[str] = (), names_unread: bool = False,
               title_screen: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Up to `CHASE_CAP` things other people owe the owner that are late or
    due today, latest first. A row the owner owes (Focus's) never shows.

    cr1#1862 (decision 147q): a row whose title names someone hidden is
    dropped, by the drafts' own free-text screen (`followup_draft.
    names_hidden`, in its `title` mode), before the cap, so the cap fills
    with rows that can be shown. `title_screen` None (who is hidden cannot be
    read) drops every row: no title can be checked."""
    if title_screen is None:
        return []
    import followup_draft as fd

    hidden = {str(h) for h in hidden_ids or ()}
    out = []
    for r in owed or []:
        if not isinstance(r, dict):
            continue
        who = str(r.get("owner_id") or "")
        if not who or who == owner_id or who in hidden:
            continue
        try:
            due = _dt.date.fromisoformat(str(r.get("due") or "")[:10])
        except ValueError:
            continue
        if due > today:
            continue
        title = str(r.get("title") or "")[:200]
        # the review of e2567453d: the full title and the cut it shows (a cut can end on a name)
        if fd.title_hidden(r.get("title"), title_screen) or fd.title_hidden(title, title_screen):
            continue
        out.append({"id": r.get("id"), "title": title,
                    "who": "" if names_unread else str(r.get("owner_name") or "")[:120],
                    "owner_id": who, "due": due.isoformat(), "days_late": (today - due).days,
                    # review round 1 of #1819: the plate drafts a chase for a late item only,
                    # so a row due today offers no Chase that would dead-end
                    "late": due < today, "chase_draft": due < today})
    out.sort(key=lambda x: (-x["days_late"], x["title"], str(x["id"])))
    return out[:CHASE_SEND_CAP]


def rank_urgent(items: Optional[List[Dict[str, Any]]], *, now: _dt.datetime,
                tzinfo: _dt.tzinfo = _dt.timezone.utc, own: Iterable[str] = (),
                starred_ids: Iterable[str] = (), starred_addresses: Iterable[str] = (),
                hidden_ids: Iterable[str] = (), owner_id: str = "",
                owed_to_you: Optional[List[Dict[str, Any]]] = None,
                names_unread: bool = False,
                title_screen: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """READ. The Urgent list from the inbox leg's live items. Answers
    `{generated_at, rows, n_rows, n_more, empty, line, chase, open_todo, ranking}`.
    `title_screen` (cr1#1862) is the drafts' free-text screen
    (`followup_draft.hidden_words`) for the `chase` titles; None when who is
    hidden cannot be read, which lists no `chase` row and adds
    `chase_held: true`.
    `generated_at` is the stamp every Hub feed carries and the page's
    reconcile reads (`_stamp`: UTC, whole seconds, `Z`), so it compares as
    text against a press's `applied_at` the way the page compares it.

    `rows` are the urgent items, strongest first, each
    `{id, thread_id, message_id, kind, source, who, sender, what, signals,
    why, deadline, waited_minutes, waited, action, url}`; `action` is the one
    button (`reply`, `accept` or `open_draft`). `chase` holds up to three
    late or due-today things others owe the owner, each with `chase_draft:
    True`, shown under the rows whether or not any row qualified (ruling
    130f). When no row qualifies, `empty` is True and `line` is the "Nothing
    urgent" line; `open_todo` is True when `chase` is empty too. Late
    commitments never become rows: `owed_to_you` feeds `chase` only."""
    now = now if now.tzinfo else now.replace(tzinfo=_dt.timezone.utc)
    today = now.astimezone(tzinfo).date()
    hidden = {str(h) for h in hidden_ids or ()}
    stars = {str(s) for s in starred_ids or ()} - ({owner_id} if owner_id else set())
    ranked = []
    for item in items or []:
        if not isinstance(item, dict) or is_noise(item, own):
            continue
        if str(item.get("sender_person_id") or "") in hidden:
            continue
        sig = signals(item, own=own, starred_ids=stars, starred_addresses=starred_addresses,
                      today=today, tzinfo=tzinfo, now=now)
        if not sig["names"]:
            continue
        ts = _parse_ts(item.get("ts") or item.get("message_ts"))
        waited = max(0, int((now - ts).total_seconds() // 60)) if ts else 0
        kind = "calendar_invite" if str(item.get("kind") or "") == "calendar_invite" else "email_reply"
        if kind == "calendar_invite":
            action = "accept"
        elif item.get("draft") or item.get("has_draft") is True:
            # review round 1 of #1819: the store keeps `has_draft`, a live item `draft`
            action = "open_draft"
        else:
            action = "reply"
        tid = str(item.get("thread_id") or item.get("message_id") or "")
        ranked.append({
            "id": f"urgent:{tid}" if tid else None,
            "thread_id": str(item.get("thread_id") or "") or None,
            "message_id": str(item.get("message_id") or "") or None,
            "kind": kind,
            "source": "calendar" if kind == "calendar_invite" else str(item.get("source") or "mail"),
            "who": str(item.get("sender_name") or _addr(item.get("sender_email") or item.get("sender")) or "")[:120],
            "sender": _addr(item.get("sender_email") or item.get("sender")) or None,
            "what": str(item.get("subject") or "")[:200],
            "signals": sig["names"],
            "why": _why(sig["names"], sig["deadline"], today),
            "deadline": sig["deadline"].isoformat() if sig["deadline"] else None,
            "waited_minutes": waited,
            "waited": _waited_words(waited),
            "action": action,
            "url": str(item.get("url") or "") or None,
            "_score": sum(SIGNAL_POINTS[s] for s in sig["names"]),
        })
    ranked.sort(key=lambda r: (-r["_score"], -r["waited_minutes"], str(r["thread_id"] or "")))
    for r in ranked:
        r.pop("_score")
    rows = ranked[:URGENT_FEED_CAP]
    out: Dict[str, Any] = {"generated_at": _stamp(now), "rows": rows, "n_rows": len(rows),
                           "n_more": len(ranked) - len(rows), "empty": not rows, "line": None,
                           "chase": [], "open_todo": False, "ranking": RANKING}
    # Ruling 130f: waiting on others shows on a busy day as well as an empty one.
    out["chase"] = _owed_rows(owed_to_you or [], owner_id=owner_id, today=today,
                              hidden_ids=hidden, names_unread=names_unread, title_screen=title_screen)
    # cr1#1862 (add-only, on that path only): who is hidden could not be read, so no
    # waiting-on-others row could be checked and none is listed
    if title_screen is None:
        out["chase_held"] = True
    if rows:
        return out
    # Feed-shape audit (U4): the free time before the next meeting is the
    # page's to say (`uxFree`), against the live calendar and its own clock,
    # with `lines.free`; a feed built an hour ago cannot know it.
    out["line"] = NOTHING_URGENT_CLEAR_LINE
    out["open_todo"] = not out["chase"]
    return out


def owed_to_you(workspace_root, *, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """READ. The open items other people owe the owner, from the ledger's
    open set, each `{id, title, owner_id, owner_name, due}`; empty when no
    owner resolves or the ledger cannot be read."""
    root = Path(str(workspace_root))
    owner = owner_id if owner_id is not None else _owner(root)
    if not owner:
        return []
    try:
        from cru_match import _commitment_field, _commitment_id, load_open_commitments
        from surface_split import SURFACE_WAITING_ON, classify_surface

        ep = root / "_hq" / "data" / "events.jsonl"
        opens = load_open_commitments(ep) if ep.exists() else []
    except Exception:  # noqa: BLE001 - an unread ledger owes nothing
        return []
    names = _names(root)
    out = []
    for ev in opens:
        try:
            if classify_surface(ev, owner) != SURFACE_WAITING_ON:
                continue
            who = str(_commitment_field(ev, "owner_id") or "")
            out.append({"id": str(_commitment_id(ev) or ""), "title": str(_commitment_field(ev, "title") or ""),
                        "owner_id": who, "owner_name": names.get(who, ""),
                        "due": str(_commitment_field(ev, "due") or "")[:10]})
        except Exception:  # noqa: BLE001 - one bad record leaves that row off
            continue
    return out


def _owner(root: Path) -> str:
    try:
        from primary_user import resolve_primary_user

        return str(resolve_primary_user(str(root)) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _names(root: Path) -> Dict[str, str]:
    try:
        from entities_io import entities_collection, read_entities_json

        ent = read_entities_json(root / "_hq" / "data" / "entities.json")
    except Exception:  # noqa: BLE001
        return {}
    return {str(p["id"]): str(p.get("display_name") or p.get("canonical_name") or p.get("name") or "")
            for p in entities_collection(ent, "people") if isinstance(p, dict) and p.get("id")}


def urgent_view(workspace_root, items: Optional[List[Dict[str, Any]]] = None, *,
                owed: Optional[List[Dict[str, Any]]] = None,
                now_iso: Optional[str] = None) -> Dict[str, Any]:
    """READ. `rank_urgent` with the workspace's own stars (the People tab),
    the owner's own addresses, the people never named on the Hub
    (`hub_work.hidden_people`, failing closed) and, unless `owed` is given,
    the open owed-to-you items (for `chase`, busy day or empty). With no
    items it answers the empty case."""
    root = Path(str(workspace_root))
    now = _parse_ts(now_iso) if now_iso else None
    now = now or _dt.datetime.now(_dt.timezone.utc)
    try:
        from tz import load_workspace_tz

        tzinfo = load_workspace_tz(str(root)) or _dt.timezone.utc
    except Exception:  # noqa: BLE001
        tzinfo = _dt.timezone.utc
    owner = _owner(root)
    try:
        from people_view import load_stars, starred_ids

        stars = starred_ids(load_stars(root))
    except Exception:  # noqa: BLE001 - no stars read, none counted
        stars = []
    try:
        import inbox_helpers

        own = inbox_helpers.own_addresses(str(root))
    except Exception:  # noqa: BLE001
        own = set()
    try:
        import hub_work

        hp = hub_work.hidden_people(root)
    except Exception:  # noqa: BLE001 - fail closed: nobody is named
        hp = {"unread": True, "ids": frozenset()}
    # cr1#1862: the drafts' own screen for the waiting-on-others titles; None when it
    # cannot be read (then no row is listed)
    screen = None
    if not hp.get("unread"):
        try:
            import followup_draft

            screen = followup_draft.hidden_words(root)
        except Exception:  # noqa: BLE001 - fail closed
            screen = None
    return rank_urgent(items, now=now, tzinfo=tzinfo, own=own, starred_ids=stars,
                       hidden_ids=hp.get("ids") or (), owner_id=owner,
                       owed_to_you=owed if owed is not None else owed_to_you(root, owner_id=owner),
                       names_unread=bool(hp.get("unread")), title_screen=screen)
