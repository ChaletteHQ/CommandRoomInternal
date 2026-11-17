#!/usr/bin/env python3
"""The morning message (cr1#1017, train 8 behaviour 1).

The owner's definition: the Hub does not wait to be remembered. Each morning
Sofia sends the owner ONE short email: what the day is about and how many
things need them, with a link that opens the Hub. It is worth reading even if
never tapped, and reads on a phone as well as a desktop (plain text, short
lines).

The owner's ruling of 2026-11-13 (option A) made it the ONE exception to IDENT1
I-12 ("a silent fire never sends"): `inbox_helpers.mail_seams(...,
self_send=<plan>)` resolves send on the morning run only for a plan from
`plan_send` that the seam re-derives itself. The recipient is the owner's own
business-primary address from the account map, its server binding
`connector_asserted` (the connector named the address itself), never anyone
else and never a personal account. One send a day is pinned by
`morning_message_sent`; a day that cannot send records
`morning_message_skipped` with its reason and sends nothing.

The day's words are the Hub's own: `needs_you` and `sentence_parts` are the
Python twin of the page's `needsYou` (the fixed morning, cr1#1197) and
`sentText(sentModel(S))` (one sentence on top, cr1#1205), pinned to the page's
functions run under node by MORNMSG1, so the email and the page say the same
thing. At send time nothing has been pressed yet, so every item in the morning
set still needs the owner.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

PURPOSE = "morning_message"
SENT_TYPE = "morning_message_sent"
CLAIM_TYPE = "morning_message_claimed"
SKIPPED_TYPE = "morning_message_skipped"
SOURCE_SKILL = "morning-brief"
#: The items listed under the sentence, so the email is worth reading untapped.
ITEM_CAP = 5
LINE_CAP = 120
LOCK_STALE_S = 300
PERSONAL_ITEM = "A personal item"
NO_TITLE = "(no subject)"
#: A day of this many timed meetings is full (the page's SENT_FULL_DAY).
SENT_FULL_DAY = 4
#: Why a morning sends nothing, in the owner's words (the receipt's line).
SKIP_LINES = {
    "already_sent": "This morning's message already went out.",
    "no_hub": "There is no Hub on this computer yet, so there is nothing to link to.",
    "no_owner_address": "Your business email is not set as the one business-primary account, so nothing was sent.",
    "unverified_binding": "Your mail connector has not confirmed your business address itself, so nothing was sent.",
    "walled": "Your business mailbox is switched off for Command Room, so nothing was sent.",
    "read_only": "Your mail connector cannot send from Command Room, so nothing was sent.",
    "no_send_tool": "The mail connector's send was not available on this run, so nothing was sent.",
    "send_failed": "The send did not go through, so nothing was sent.",
    "unreadable": "Your settings could not be read, so nothing was sent.",
    "busy": "Another run was sending this morning's message, so this one sent nothing.",
    "not_today": "The message was planned for another day, so nothing was sent.",
    "not_the_owner": "The message was not addressed to your business email, so nothing was sent.",
    "not_the_plan": "The message changed after it was planned, so nothing was sent.",
    "not_the_morning_message": "That was not the morning message, so nothing was sent.",
}


# ---------------------------------------------------------------------------
# the twin of the page's words (needsYou, sentModel, sentText)
# ---------------------------------------------------------------------------

def _cap(t: Any) -> str:
    s = str("" if t is None else t).lstrip()
    return s[:1].upper() + s[1:]


def _late(r: Dict[str, Any]) -> bool:
    return r.get("counted") is True or r.get("horizon") in ("overdue", "past_due")


def _day10(v: Any) -> str:
    return str(v or "")[:10]


def needs_you(plate: Optional[Dict[str, Any]], review: Optional[Dict[str, Any]],
              inbox: Optional[Dict[str, Any]], *, day: str) -> List[Dict[str, Any]]:
    """What needs the owner this morning, in the page's order (the plate,
    then Review, then the Inbox): `[{source, id, title}]`. The page's
    `needsYou`: a commitment due today or one to follow up (never past its
    date), a Review question marked for today, every Inbox item."""
    out: List[Dict[str, Any]] = []
    if isinstance(plate, dict) and plate.get("today") == day:
        for r in plate.get("rows") or []:
            if not isinstance(r, dict) or r.get("id") is None or _late(r):
                continue
            if r.get("lane") == "chase" or (r.get("due") and _day10(r.get("due")) == plate.get("today")):
                out.append({"source": "plate", "id": "c:" + str(r["id"]), "title": r.get("title"),
                            **({"personal": True} if r.get("personal") else {})})
    if isinstance(review, dict):
        for r in review.get("rows") or []:
            if isinstance(r, dict) and r.get("today") and r.get("id") is not None:
                out.append({"source": "review", "id": "q:" + str(r["id"]), "title": r.get("title")})
    if isinstance(inbox, dict):
        for it in inbox.get("items") or []:
            if isinstance(it, dict):
                out.append({"source": "inbox", "id": "m", "title": it.get("subject") or it.get("name")})
    return out


def _parse(iso: Any) -> Optional[_dt.datetime]:
    try:
        d = _dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else None


def _local_day(iso: Any, tz) -> str:
    d = _parse(iso)
    return d.astimezone(tz).date().isoformat() if d else ""


def _fmt_time(iso: Any, tz) -> str:
    d = _parse(iso)
    if not d:
        return ""
    d = d.astimezone(tz)
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def _same_meeting(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return bool((a.get("meeting_id") and a.get("meeting_id") == b.get("meeting_id"))
                or (a.get("title") == b.get("title") and a.get("start") == b.get("start")))


def _is_block(m: Dict[str, Any]) -> bool:
    """cr1#1293: a timed event with nobody else on it (the today feed's
    `solo`) is the owner's own time, never a meeting. The page's `isBlock`."""
    return m.get("solo") is True and not m.get("all_day")


def _block_words(blocks: List[Dict[str, Any]]) -> str:
    """The page's `blockWords`: "3 hours blocked for <title>" for one block,
    "... for your own work" for several; under an hour in minutes, else hours
    to the half hour."""
    mins = 0.0
    for m in blocks:
        s, e = _parse(m.get("start")), _parse(m.get("end"))
        d = (e - s).total_seconds() / 60 if s and e else 0
        mins += d if d > 0 else 0
    mins = math.floor(mins + 0.5)
    if mins < 60:
        span = f"{mins} minute" + ("" if mins == 1 else "s")
    else:
        h = math.floor(mins / 30 + 0.5) / 2
        span = (str(int(h)) if h == int(h) else str(h)) + (" hour" if h == 1 else " hours")
    return span + " blocked for " + (_cap(blocks[0].get("title")) if len(blocks) == 1 else "your own work")


def _day_clash(today, plate, day: str, now: _dt.datetime, tz) -> Optional[Dict[str, Any]]:
    ms = [m for m in ((today or {}).get("meetings") or []) if isinstance(m, dict)
          and not m.get("all_day") and not _is_block(m) and m.get("start") and m.get("end") and _local_day(m.get("start"), tz) == day]
    ms.sort(key=lambda m: _parse(m["start"]) or now)
    ahead = [m for m in ms if (_parse(m["end"]) or now) > now]
    for i in range(len(ahead)):
        for j in range(i + 1, len(ahead)):
            if (_parse(ahead[j]["start"]) < _parse(ahead[i]["end"])) and not _same_meeting(ahead[i], ahead[j]):
                return {"kind": "overlap", "a": ahead[i], "b": ahead[j]}
    full = [m for k, m in enumerate(ms) if not any(_same_meeting(o, m) for o in ms[:k])]
    if len(full) >= SENT_FULL_DAY and ahead and isinstance(plate, dict) and plate.get("today") == day:
        due = [r for r in plate.get("rows") or [] if isinstance(r, dict) and not _late(r)
               and r.get("lane") not in ("chase", "wait") and r.get("due") and _day10(r.get("due")) == day]
        if due:
            return {"kind": "full", "row": due[0], "more": len(due) - 1, "meetings": full}
    return None


def _words(items: List[Any]) -> str:
    w = [re.sub(r"[\s.!?;:,]+$", "", str(x or "")) for x in items]
    w = [x for x in w if x]
    return " and ".join(w) if len(w) < 3 else ", ".join(w[:-1]) + ", and " + w[-1]


def _lead_words(lead: List[Dict[str, Any]]) -> str:
    """cr1#1290, the page's `leadWords`: the top ranked item due later this
    week is what the week is about, and the next ones are what today moves;
    a top item due today, past its date or undated is today's."""
    top = lead[0] or {}
    rest = [_cap((r or {}).get("title")) for r in lead[1:]]
    left = top.get("days_left")
    if isinstance(left, int) and not isinstance(left, bool) and left > 0:
        return ("This week is about " + _cap(top.get("title"))
                + (", due " + str(top["due_day"]) if top.get("due_day") else "")
                + (", and today moves " + _words(rest) if rest else ""))
    return "Today is about " + _words([_cap(top.get("title"))] + rest)


def sentence_parts(today: Optional[Dict[str, Any]], plate: Optional[Dict[str, Any]],
                   review: Optional[Dict[str, Any]], inbox: Optional[Dict[str, Any]], *,
                   day: str, now: _dt.datetime, tz) -> List[Dict[str, str]]:
    """The day as the Hub writes it: `[{part, text}]` for `about`, `need`,
    `clash` and `goal` (cr1#1294), in that order, the first capitalised. The page's
    `sentText(sentModel(S))` on a fresh morning (nothing pressed yet)."""
    if not today and not plate:
        return []
    wday = plate.get("today") if isinstance(plate, dict) and plate.get("today") else day
    about = None
    ab = (today or {}).get("about") if isinstance(today, dict) else None
    if isinstance(ab, dict) and ab.get("for_date") == wday and (ab.get("items") or []):
        about = {"said": True, "items": ab["items"]}
    if not about:
        focus = (plate.get("focus") or []) if isinstance(plate, dict) and plate.get("today") == day else []
        ms = [m for m in ((today or {}).get("meetings") or []) if isinstance(m, dict)
              and not m.get("all_day") and _local_day(m.get("start"), tz) == day]
        bs = [m for m in ms if _is_block(m)]
        ms = [m for m in ms if not _is_block(m)]
        lead = (plate.get("lead") or []) if isinstance(plate, dict) and plate.get("today") == day else []
        if lead:
            about = {"said": False, "lead": lead[:3]}
        elif focus:
            about = {"said": False, "focus": focus[:3]}
        elif ms or bs:
            about = {"said": False, "meetings": ms, "blocks": bs}
    need = None
    if isinstance(plate, dict) and plate.get("today") and plate.get("today") == day:
        need = len(needs_you(plate, review, inbox, day=day))
    clash = _day_clash(today, plate, day, now, tz)
    # cr1#1294: the confirmed priorities with nothing open, the page's `goal`
    goals = [g for g in ((plate.get("goals") or []) if isinstance(plate, dict) and plate.get("today") == day else [])
             if isinstance(g, dict)]
    out: List[Dict[str, str]] = []
    if about:
        if about.get("lead"):
            out.append({"part": "about", "text": _lead_words(about["lead"])})
        else:
            if about["said"]:
                a = _words(about["items"])
            elif about.get("focus"):
                a = _cap((about["focus"][0] or {}).get("title"))
            else:
                n = len(about["meetings"])
                w = [f"{n} meeting" + ("" if n == 1 else "s")] if n else []
                if about.get("blocks"):
                    w.append(_block_words(about["blocks"]))
                a = " and ".join(w)
            out.append({"part": "about", "text": "Today is about " + a})
    if need is not None:
        out.append({"part": "need", "text": (f"{need} thing needs you" if need == 1 else f"{need} things need you")
                    if need else "nothing needs you"})
    if clash:
        if clash["kind"] == "overlap":
            t = (_cap(clash["a"].get("title")) + " overlaps " + _cap(clash["b"].get("title"))
                 + " at " + _fmt_time(clash["b"].get("start"), tz))
        else:
            t = _cap(clash["row"].get("title")) + " is due on a day of " + str(len(clash["meetings"])) + " meetings"
        out.append({"part": "clash", "text": t})
    if goals:
        n = plate.get("n_goals")
        n = n if isinstance(n, int) and not isinstance(n, bool) and n >= len(goals) else len(goals)
        out.append({"part": "goal", "text": _cap(goals[0].get("statement")) + " has nothing open"
                    if n == 1 else f"{n} priorities have nothing open"})
    if out:
        out[0]["text"] = _cap(out[0]["text"])
    return out


# ---------------------------------------------------------------------------
# who it may go to: the owner's own business address, verified
# ---------------------------------------------------------------------------

def owner_address(workspace_root, entities: Optional[dict] = None) -> Dict[str, Any]:
    """`{ok, address, server_id, provider}` or `{ok: False, reason}`. The ONE
    business-primary account in the account map, not dialled off, with a
    binding whose address the connector itself returned
    (`connector_asserted`) on a backend whose manifest says it can send. Of
    several such bindings, the one on the declared mail server wins. READ
    ONLY."""
    import connector_config as cc
    from connector_adapters.capabilities import supports

    try:
        ent = entities if isinstance(entities, dict) else cc.load_entities(workspace_root)
    except Exception:  # noqa: BLE001
        return {"ok": False, "reason": "unreadable"}
    if not isinstance(ent, dict) or not ent:
        return {"ok": False, "reason": "unreadable"}
    prim = [a for a in cc.accounts(entities=ent) if a.get("role") == "business-primary"
            and cc._norm_addr(a.get("address"))]
    if len(prim) != 1:
        return {"ok": False, "reason": "no_owner_address"}
    acc = prim[0]
    address = cc._norm_addr(acc.get("address"))
    if not cc._dials_for(acc).get("surface"):
        return {"ok": False, "reason": "walled"}
    binds = [b for b in (acc.get("bindings") or []) if isinstance(b, dict) and b.get("server_id")
             and b.get("binding_verified") == cc.BINDING_CONNECTOR_ASSERTED]
    if not binds:
        return {"ok": False, "reason": "unverified_binding"}
    sendable = [b for b in binds if supports(b.get("provider"), "send", category="email")]
    if not sendable:
        return {"ok": False, "reason": "read_only"}
    try:
        declared = (cc.declared_backend("email", workspace_root, entities=ent) or {}).get("server_id")
    except Exception:  # noqa: BLE001
        declared = None
    b = next((x for x in sendable if declared and x["server_id"] == declared), sendable[0])
    return {"ok": True, "address": address, "server_id": str(b["server_id"]), "provider": b.get("provider")}


def _day_of(workspace_root, now: _dt.datetime) -> str:
    from day_intent import workspace_today
    return workspace_today(workspace_root, now=now).isoformat()


def day_state(workspace_root, day: str) -> Dict[str, Any]:
    """`{claim, sent}` for one workspace day: the digest this morning's plan
    claimed, and whether it went out. RAISES when the log cannot be read:
    every caller refuses then, so an unread log never sends twice."""
    from events_io import load_events_org_scoped

    out: Dict[str, Any] = {"claim": None, "sent": False}
    for ev in load_events_org_scoped(str(workspace_root), drop_personal=False)[0]:
        if not isinstance(ev, dict):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if str(d.get("day") or "") != day:
            continue
        if ev.get("type") == CLAIM_TYPE and not out["claim"]:
            out["claim"] = d.get("digest")
        elif ev.get("type") == SENT_TYPE:
            out["sent"] = True
    return out


def sent_on(workspace_root, day: str) -> bool:
    """Has the morning message gone out (or been claimed) on this day. An
    unread log answers True: it never sends on a guess."""
    try:
        st = day_state(workspace_root, day)
    except Exception:  # noqa: BLE001
        return True
    return bool(st["sent"] or st["claim"])


def digest(plan: Dict[str, Any]) -> str:
    """The seal on a plan: what it says and to whom. A plan whose recipient,
    server, day, subject or body was changed after `plan_send` no longer
    matches its claim."""
    keys = ("purpose", "day", "to", "server_id", "subject", "body")
    raw = json.dumps({k: plan.get(k) for k in keys}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_self_send(workspace_root, plan: Any, *, server_id: Optional[str] = None) -> Dict[str, Any]:
    """What `mail_seams(self_send=...)` asks before it lets send resolve on a
    silent fire. The plan is the morning message's; its recipient and server
    are the ones this code derives now from the account map (never taken
    from the plan); the resolved send tool is on that server; its day is
    today; it is the very plan `plan_send` claimed (its digest); nothing went
    out yet. `{ok, address}` or `{ok: False, reason}`. READ ONLY; never
    raises, and refuses whatever it cannot read."""
    try:
        if not isinstance(plan, dict) or plan.get("purpose") != PURPOSE or plan.get("send") is not True:
            return {"ok": False, "reason": "not_the_morning_message"}
        own = owner_address(workspace_root)
        if not own.get("ok"):
            return {"ok": False, "reason": own.get("reason")}
        if str(plan.get("to") or "").strip().lower() != own["address"] or plan.get("server_id") != own["server_id"]:
            return {"ok": False, "reason": "not_the_owner"}
        if server_id is not None and server_id != own["server_id"]:
            return {"ok": False, "reason": "not_the_owner"}
        today = _day_of(workspace_root, _dt.datetime.now(_dt.timezone.utc))
        if plan.get("day") != today:
            return {"ok": False, "reason": "not_today"}
        st = day_state(workspace_root, today)
        if st["sent"]:
            return {"ok": False, "reason": "already_sent"}
        if not st["claim"] or st["claim"] != digest(plan):
            return {"ok": False, "reason": "not_the_plan"}
        return {"ok": True, "address": own["address"]}
    except Exception:  # noqa: BLE001 - a check that cannot answer refuses
        return {"ok": False, "reason": "unreadable"}


# ---------------------------------------------------------------------------
# the message
# ---------------------------------------------------------------------------

def _clip(s: Any) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= LINE_CAP else s[:LINE_CAP - 1].rstrip() + "…"


def compose(parts: List[Dict[str, str]], items: List[Dict[str, Any]], url: str,
            brain_name: str = "") -> Dict[str, str]:
    """`{subject, body}`: the sentence first, then what needs the owner, then
    the link. Plain text and short lines, so a phone shows it as well as a
    desktop does; the subject alone tells the day's count. An item marked
    `personal` is listed only as "A personal item"."""
    need = next((p["text"] for p in parts if p["part"] == "need"), "")
    subject = "Your day: " + (need[:1].lower() + need[1:]) if need else "Your day"
    lines = []
    if parts:
        lines.append(_clip("; ".join(p["text"] for p in parts) + "."))
    if items:
        lines += ["", "Needs you:"]
        for i in items[:ITEM_CAP]:
            title = PERSONAL_ITEM if i.get("personal") else (_cap(i.get("title") or "") or NO_TITLE)
            lines.append("- " + _clip(title))
        if len(items) > ITEM_CAP:
            lines.append(f"- and {len(items) - ITEM_CAP} more")
    lines += ["", "Open the Hub: " + url]
    if brain_name:
        lines += ["", brain_name]
    return {"subject": _clip(subject), "body": "\n".join(lines)}


def _personal_ids(root: Path) -> Optional[set]:
    """The commitments on the personal lane: in the owner's whole book but
    not in the organisation's view. None when that cannot be told, and then
    every commitment title is held back."""
    try:
        from events_io import load_events_org_scoped

        def ids(drop: bool) -> set:
            return {str((e.get("data") or {}).get("id")) for e in
                    load_events_org_scoped(str(root), drop_personal=drop)[0]
                    if isinstance(e, dict) and e.get("type") == "commitment" and (e.get("data") or {}).get("id")}
        return ids(False) - ids(True)
    except Exception:  # noqa: BLE001
        return None


def _hide_personal(plate: Any, personal: Optional[set]) -> Any:
    """The plate as the email may show it: a commitment on the personal lane
    keeps its place and its count, but its title reads "a personal item",
    in the list, in the day's first thing to move and in a clash. With
    `personal` unknown (None), every title is held back."""
    if not isinstance(plate, dict):
        return plate
    out = dict(plate)
    hidden = set()
    rows = []
    for r in plate.get("rows") or []:
        if isinstance(r, dict) and (personal is None or str(r.get("id")) in personal):
            hidden.add(r.get("title"))
            r = dict(r, title=PERSONAL_ITEM.lower(), personal=True)
        rows.append(r)
    out["rows"] = rows
    out["focus"] = [dict(f, title=PERSONAL_ITEM.lower()) if isinstance(f, dict)
                    and (personal is None or f.get("title") in hidden) else f for f in plate.get("focus") or []]
    # cr1#1290: the ranked top line reads the same way
    out["lead"] = [dict(f, title=PERSONAL_ITEM.lower()) if isinstance(f, dict)
                   and (personal is None or str(f.get("id")) in personal or f.get("title") in hidden) else f
                   for f in plate.get("lead") or []]
    # cr1#1294: a priority on the personal lane (the feed marks it), or any
    # priority while the personal lane is unknown, reads as a personal item
    out["goals"] = [dict(g, statement=PERSONAL_ITEM.lower()) if isinstance(g, dict)
                    and (personal is None or g.get("personal") is not False) else g
                    for g in plate.get("goals") or []]
    return out


class _Lock:
    """One morning message at a time on this computer: an exclusive lock
    file beside the log, reclaimed when older than `LOCK_STALE_S`."""

    def __init__(self, root: Path):
        self.path = root / "_hq" / "data" / ".morning_message.lock"
        self.held = False

    def __enter__(self):
        import os
        import time
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > LOCK_STALE_S:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                return self
        return self

    def __exit__(self, *exc):
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
        return False


def hub_address(workspace_root) -> Dict[str, Any]:
    """READ. `{found, url}`: the Hub page whose `feed/inbox` the morning run
    reads before it plans the message (`cockpit_helpers.hub_pointer`'s
    address; the brief's own last line stays that helper's). Never raises."""
    try:
        import cockpit_helpers as ch
        p = ch.hub_pointer(workspace_root)
        return {"found": bool(p.get("found")), "url": p.get("url")}
    except Exception:  # noqa: BLE001
        return {"found": False, "url": None}


def plan_send(workspace_root, *, now_iso=None, meetings: Optional[List[Dict[str, Any]]] = None,
              inbox: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A WRITER. This morning's message, planned and CLAIMED: `{send: True,
    purpose, day, to, server_id, provider, subject, body, n_need}` or
    `{send: False, reason, line, day}`.

    The claim (`morning_message_claimed` with the plan's `digest`) is written
    under a lock before anything can be sent, so two runs on one computer
    never both send, and the seam sends only this exact plan. A claimed day
    is not planned again, even when its send then failed: at most one
    message a day. It builds the plate (`cockpit_helpers.feed_plate`, which
    mints display numbers). `meetings` is the calendar the run read; a
    meeting on a personal calendar never reaches the email. `inbox` is the
    Hub's `feed/inbox` document as read, or null (the count then leaves the
    Inbox out, as the page does without that feed). Nothing is sent here."""
    import cockpit_helpers as ch
    from event_gate import append_event

    root = Path(str(workspace_root))
    try:
        now = ch._now(now_iso)
        day = _day_of(root, now)
    except Exception:  # noqa: BLE001
        return {"send": False, "purpose": PURPOSE, "reason": "unreadable", "line": SKIP_LINES["unreadable"],
                "day": None}

    def skip(reason: str) -> Dict[str, Any]:
        return {"send": False, "purpose": PURPOSE, "reason": reason, "line": SKIP_LINES[reason], "day": day}

    with _Lock(root) as lock:
        if not lock.held:
            return skip("busy")
        try:
            if sent_on(root, day):
                return skip("already_sent")
            hub = ch.hub_pointer(root)
            if not hub.get("found"):
                return skip("no_hub")
            own = owner_address(root)
            if not own.get("ok"):
                return skip(own.get("reason") or "no_owner_address")
            tz = ch._workspace_tz(root)
            plate = ch.feed_plate(root, now_iso=now_iso)
            try:
                review = ch.feed_review(root, now_iso=now_iso)
            except Exception:  # noqa: BLE001 - the page without its Review feed
                review = None
            business = [m for m in meetings or [] if isinstance(m, dict)
                        and str(m.get("calendar") or "").lower() != "personal"]
            today = ch.feed_today(root, business, now_iso=now_iso)
            inbox = inbox if isinstance(inbox, dict) else None
            plate = _hide_personal(plate, _personal_ids(root))
            parts = sentence_parts(today, plate, review, inbox, day=day, now=now, tz=tz)
            items = needs_you(plate, review, inbox, day=day)                 if (isinstance(plate, dict) and plate.get("today") == day) else []
            import connector_config as cc
            try:
                brain = str(((cc.load_entities(root) or {}).get("entities") or {}).get("workspace", {})
                            .get("brain_name") or "")
            except Exception:  # noqa: BLE001
                brain = ""
            msg = compose(parts, items, hub["url"], brain)
            plan = {"send": True, "purpose": PURPOSE, "day": day, "to": own["address"],
                    "server_id": own["server_id"], "provider": own.get("provider"), "subject": msg["subject"],
                    "body": msg["body"], "n_need": len(items)}
            append_event(root / "_hq" / "data" / "events.jsonl",
                         {"type": CLAIM_TYPE, "source_skill": SOURCE_SKILL,
                          "data": {"day": day, "digest": digest(plan)}}, holder="morning_message")
            return plan
        except Exception:  # noqa: BLE001 - a plan that cannot be built sends nothing
            return skip("unreadable")


def record(workspace_root, plan: Optional[Dict[str, Any]], *, sent: bool, via: Optional[str] = None,
           reason: Optional[str] = None, now_iso=None) -> Dict[str, Any]:
    """A WRITER. One row for the morning: `morning_message_sent` `{day, to,
    via, subject, n_need}` once the claimed plan went out (refused for any
    plan that is not the day's claim, or not to the owner), else
    `morning_message_skipped` `{day, reason}`, naming no address. A second
    `sent` for the same day writes nothing."""
    from event_gate import append_event

    plan = plan if isinstance(plan, dict) else {}
    root = Path(str(workspace_root))
    day = plan.get("day")
    if not day:
        import cockpit_helpers as ch
        day = _day_of(root, ch._now(now_iso))
    if sent:
        try:
            st = day_state(root, day)
        except Exception:  # noqa: BLE001
            return {"ok": False, "written": False, "reason": "unreadable"}
        if st["sent"]:
            return {"ok": True, "written": False, "reason": "already_sent"}
        own = owner_address(root)
        if plan.get("send") is not True or not own.get("ok") \
                or str(plan.get("to") or "").strip().lower() != own["address"]:
            return {"ok": False, "written": False, "reason": "not_the_owner"}
        if not st["claim"] or st["claim"] != digest(plan):
            return {"ok": False, "written": False, "reason": "not_the_plan"}
        data = {"day": day, "to": own["address"], "via": via or "native", "subject": plan.get("subject"),
                "n_need": plan.get("n_need")}
        etype = SENT_TYPE
    else:
        why = reason or plan.get("reason") or "send_failed"
        data = {"day": day, "reason": why if why in SKIP_LINES else "send_failed"}
        etype = SKIPPED_TYPE
    append_event(root / "_hq" / "data" / "events.jsonl",
                 {"type": etype, "source_skill": SOURCE_SKILL,
                  "data": {k: v for k, v in data.items() if v not in (None, "")}},
                 holder="morning_message")
    return {"ok": True, "written": True, "type": etype}
