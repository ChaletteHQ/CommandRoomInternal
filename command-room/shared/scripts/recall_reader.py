#!/usr/bin/env python3
"""cr1#1556: one read call for a plain question about the record.

    answer_pack(workspace_root, ask) -> dict

The owner's question goes in as typed. What comes back is everything an answer
needs, worked out in code: who and what the question names, their current
record, the newest entries tagged with them, open promises with their counts,
decisions and whether each still stands, a search of the log on the question's
own words, and `enough`, a plain flag for whether that covers the question.
The caller writes the answer from the pack and nothing else.

WHY ONE CALL. The readers underneath already exist (`entity_resolve`, the open
list, the decision list, `log_search`), and on 2026-11-21 they were named by
none or one of sixty skills. Tested the same day on a full-size synthetic
workspace: reading the folder with no guidance gave three overdue counts in
three runs, the readers gave one; and one call did in about a quarter of the
tool calls what the same readers did as separate steps, without a step being
left out. Results are on cr1#1016 and cr1#777.

RULES THIS FILE EXISTS TO HOLD, each pinned by `tests/run_recall_reader_test.py`:

  * One number per question. Every count carries a label that says what it
    counts, and `lead` states the number the question asked for in a plain
    sentence. A prototype offered "44 open" (everything involving a person)
    ahead of the 12 that person owns, and the answer took the first.
  * Every date is the owner's date: the workspace's time zone, never a slice of
    the stamp. The prototype sliced a stamp taken just after midnight universal
    time and answered a day late.
  * The personal wall holds for everything in the pack. The log is read once,
    through the org-scoped reader; promises are the open list's rows, kept only
    where that read saw them, and a promise's title, owner and due date come
    from that read too. When rows are held back the pack says how many, so a
    count that differs from the Hub's is explained and never silent.
  * `enough` is honest both ways. It is false when a name is not on file, when
    a name matches more than one record, when the field asked for is empty, or
    when the record never mentions what the question is about. A capitalised
    word that is not a name ("What's", a month) never causes a decline while
    the pack holds an answer.

READ ONLY. Nothing here writes, stages or makes a temporary file. Called with
the workspace alone it answers ok with an empty pack.

WHAT IT DOES NOT DO. It does not read connectors (mail, calendar and meeting
notes stay with the model), it does not search by meaning (cr1#1009 ruling A7:
cards plus keyword search), and it does not write a receipt of its own.
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

READER_VERSION = 1
NOTHING_LINE = "I do not have that."

#: What the caller does with the pack. Carried in the pack so the rule travels
#: with the data and is not a line in a skill file.
HOW: Tuple[str, ...] = (
    "Answer only from this pack. Do not add a fact that is not in it.",
    "Give the numbers and dates exactly as `lead` and the labelled counts state them.",
    "When `enough` is false, the answer is the `say` sentence.",
    "Name the records the answer rests on (the person, the work stream, the entry's date).",
    "An entry marked `no_longer_current`, and a decision that does not stand, is history: "
    "say what is current first.",
)

#: Words that start a question or a request, never a name.
_ASK_WORDS = frozenset(
    "what when who whom whose which where why how is are was were am do does did has have had "
    "can could should would will shall may might give show tell list send draft write on in at "
    "for from with about and or the a an my our their his her its this that these those any all "
    "please also so if i me we update status remind summarize summarise find get check".split())

#: Capitalised words that are dates, not names.
_CALENDAR = frozenset(
    "january february march april may june july august september october november december "
    "jan feb mar apr jun jul aug sep sept oct nov dec monday tuesday wednesday thursday friday "
    "saturday sunday q1 q2 q3 q4".split())

#: Words that carry no topic. The register's own field words are here too: a
#: question about a role or an email is answered from the card, not the log.
_STOP = frozenset(
    "a an the of on in at to for from by with about and or but is are was were be been being am "
    "do does did done doing what when who whom whose which where why how many much my me i we our "
    "us you your it its this that these those still now right most recently recent last next any "
    "there here have has had should could would will can use stand stands standing decide decided "
    "decision decisions commitment commitments item items open overdue owe owed owes role title "
    "mobile phone number email address key contact active work stream streams hear heard come "
    "comes due day days seven counting today people person other others give me tell show please "
    "record records number numbers one two three new old all every not no yes company status "
    "owner owns project projects anything something everything know mention mentioned mentions "
    "say said says talk talked spoke spoken reply replied his her their time since been "
    "going long ago task tasks whether".split())

#: The medium a thing was said in is not what it was about.
_MEDIUM = frozenset("call calls email emails mail meeting meetings note notes message messages "
                    "chat thread conversation".split())

_COMMIT_RX = re.compile(
    r"\bopen (commitments?|items?|promises?|tasks?|loops?)\b|still open|\boverdue\b|\bowe[ds]?\b|"
    r"commitments?\b|\bpromise[ds]?\b|\bdue\b|on my plate|record numbers?", re.I)
_DECIDE_RX = re.compile(r"\bdecid|\bdecision|\bagreed?\b|still stand|does this stand", re.I)
_CONTACT_RX = re.compile(r"\bhear(d)?\b|\btalk(ed)?\b|\bspoke|\bspoken\b|\brepl(y|ied)\b|"
                         r"last (time|email|call|meeting|message)", re.I)
_STREAM_RX = re.compile(r"\bactive\b|\bpaused\b|on hold|work streams?|\bprojects?\b", re.I)
_WINDOW_RX = re.compile(r"next (seven|7) days|this week|com(e|es|ing) due", re.I)
_ABOUT_RX = re.compile(
    r"\babout\s+(.+?)(?:\s+(?:on|in|at|during|from|with|when|before|after)\b|[?.!]|$)", re.I)
_REQUEST_RX = re.compile(r"(please\s+)?(send|draft|write|email|reply|schedule|book|create|make|"
                         r"log|add|set|move|close|mark)\b", re.I)
_FIELDS = (("mobile", "phones"), ("phone", "phones"), ("email", "emails"))

_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_QUOTED_RX = re.compile(r"[\"“]([^\"”]{4,})[\"”]")
_TOKEN_RX = re.compile(r"[a-z0-9][a-z0-9'-]*")
_WORD_RX = re.compile(r"[A-Za-z][A-Za-z'-]+")
_NAME_TOKEN_RX = re.compile(r"[A-Za-z][A-Za-z0-9'&.-]*|[—–-]")
_JOINERS = ("—", "–", "-", "&")

#: Changes to a person or a work stream. They carry the current fact (a new
#: title, a new address, a status), so they are entries even where the search
#: index does not treat them as substance.
_FACT_TYPES = frozenset({"person_fact_observed", "person_role_changed", "person_record_update",
                         "person_updated", "thread_updated", "status_change", "org_updated",
                         "deal_stage_changed", "engagement_updated"})


# ---------------------------------------------------------------------------
# The register
# ---------------------------------------------------------------------------

def _register(ws: Path) -> Dict[str, List[dict]]:
    empty: Dict[str, List[dict]] = {"people": [], "threads": [], "orgs": []}
    try:
        from entities_io import entities_collection, read_entities_json
        data = read_entities_json(ws / "_hq" / "data" / "entities.json")
        if not isinstance(data, dict):
            return empty
        return {name: [r for r in entities_collection(data, name)
                       if isinstance(r, dict) and r.get("id")]
                for name in ("people", "threads", "orgs")}
    except Exception:
        return empty


def _name(rec: Optional[dict]) -> str:
    if not isinstance(rec, dict):
        return ""
    return str(rec.get("canonical_name") or rec.get("display_name") or rec.get("id") or "")


def _strings(value: Any) -> List[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, (list, tuple)):
        return [v.strip() for v in value if isinstance(v, str) and v.strip()]
    return []


def _person_card(rec: dict, by_id: Dict[str, dict]) -> dict:
    org = by_id.get(str(rec.get("org_id") or ""))
    return {"kind": "person", "id": rec.get("id"), "name": _name(rec),
            "also_known_as": _strings(rec.get("aliases")) + _strings(rec.get("nicknames")),
            "role": rec.get("role") or None, "company": _name(org) or None,
            "emails": _strings(rec.get("emails")) or _strings(rec.get("email")),
            "phones": _strings(rec.get("phones")) or _strings(rec.get("phone")),
            "status": rec.get("status") or None}


def _stream_card(rec: dict, by_id: Dict[str, dict]) -> dict:
    org = by_id.get(str(rec.get("org_id") or rec.get("affiliation_id") or ""))
    contact = by_id.get(str(rec.get("key_contact_id") or ""))
    owner = by_id.get(str(rec.get("owner_person_id") or ""))
    return {"kind": "work stream", "id": rec.get("id"), "name": _name(rec),
            "also_known_as": _strings(rec.get("aliases")),
            "status": rec.get("status") or None, "company": _name(org) or None,
            "key_contact": _name(contact) or None, "owner": _name(owner) or None,
            "last_activity_on_register": rec.get("last_activity") or None}


def _company_card(rec: dict, reg: Dict[str, List[dict]]) -> dict:
    oid = str(rec.get("id") or "")
    people = [_name(p) for p in reg["people"] if str(p.get("org_id") or "") == oid]
    streams = [_name(t) for t in reg["threads"]
               if str(t.get("org_id") or t.get("affiliation_id") or "") == oid]
    return {"kind": "company", "id": rec.get("id"), "name": _name(rec),
            "also_known_as": _strings(rec.get("aliases")),
            "relationship": rec.get("relationship_type") or None,
            "people_on_file": people[:12], "work_streams": streams[:12]}


# ---------------------------------------------------------------------------
# Who and what the question names
# ---------------------------------------------------------------------------

def _spans(ask: str) -> List[Tuple[str, bool]]:
    """Runs of capitalised words that could be a name, each with whether it
    opens a sentence (a lone opening word is the question's own first word far
    more often than a name)."""
    text = _QUOTED_RX.sub(" ", ask)
    out: List[Tuple[str, bool]] = []
    for sentence in re.split(r"(?<=[.?!])\s+", text):
        cur: List[str] = []
        opening = False
        first = True
        for tok in _NAME_TOKEN_RX.findall(sentence):
            bare = re.sub(r"'s$", "", tok)
            joiner = tok in _JOINERS
            cap = bare[:1].isupper()
            asking = bare.lower() in _ASK_WORDS or bare.lower() in _CALENDAR
            if (cap and not (not cur and asking)) or (joiner and cur):
                if not cur:
                    opening = first
                cur.append(bare if not joiner else tok)
            else:
                if cur:
                    out.append((" ".join(cur), opening))
                cur = []
            first = False
        if cur:
            out.append((" ".join(cur), opening))
    cleaned: List[Tuple[str, bool]] = []
    for span, opening in out:
        parts = span.split()
        while parts and parts[-1] in _JOINERS:
            parts.pop()
        while parts and parts[0] in _JOINERS:
            parts.pop(0)
        if parts and any(p not in _JOINERS for p in parts):
            cleaned.append((" ".join(parts), opening))
    return cleaned


def _named_in(ask: str, reg: Dict[str, List[dict]]) -> Tuple[Dict[str, List[dict]], List[str]]:
    """Records whose own full name or alias (two words or more) appears word
    for word in the question, longest first so a work stream's full name wins
    over a part of it. Returns the records and the stretches of text they took."""
    low = " " + re.sub(r"\s+", " ", ask.lower()) + " "
    taken: List[Tuple[int, int]] = []
    found: Dict[str, List[dict]] = {"people": [], "threads": [], "orgs": []}
    candidates: List[Tuple[int, str, str, dict]] = []
    for coll in ("threads", "orgs", "people"):
        for rec in reg[coll]:
            for nm in [_name(rec)] + _strings(rec.get("aliases")):
                nm = " ".join(nm.split())
                if len(nm) >= 4 and " " in nm:
                    candidates.append((len(nm), coll, nm.lower(), rec))
    for _, coll, nm, rec in sorted(candidates, key=lambda c: -c[0]):
        at = low.find(nm)
        if at < 0:
            continue
        end = at + len(nm)
        if low[at - 1].isalnum() or (end < len(low) and low[end].isalnum()):
            continue
        if any(a < end and at < b for a, b in taken):
            continue
        taken.append((at, end))
        if rec not in found[coll]:
            found[coll].append(rec)
    return found, [low[a:b] for a, b in taken]


def _single_names(reg: Dict[str, List[dict]]) -> Dict[str, List[str]]:
    """One-word names a question may use in lower case: a person's first name
    and any one-word alias, mapped to the ids that carry it."""
    out: Dict[str, List[str]] = {}
    for coll in ("people", "threads", "orgs"):
        for rec in reg[coll]:
            words = [a for a in _strings(rec.get("aliases")) + _strings(rec.get("nicknames"))
                     if " " not in a]
            if coll == "people" and " " in _name(rec):
                words.append(_name(rec).split()[0])
            for w in words:
                key = w.lower()
                if len(key) >= 3 and key not in _STOP and key not in _ASK_WORDS:
                    ids = out.setdefault(key, [])
                    if str(rec["id"]) not in ids:
                        ids.append(str(rec["id"]))
    return out


def _resolve(ws: Path, span: str) -> List[dict]:
    try:
        from entity_resolve import resolve_all
        res = resolve_all(ws, span)
    except Exception:
        return []
    out = []
    for r in res:
        rec = getattr(r, "record", None)
        if isinstance(rec, dict) and rec.get("id"):
            out.append({"record": rec, "confidence": float(getattr(r, "confidence", 0) or 0),
                        "via": str(getattr(r, "matched_via", "") or "")})
    return out


# ---------------------------------------------------------------------------
# One pass over the log
# ---------------------------------------------------------------------------

class _OwnerDay:
    """The owner's date for a stamp. The workspace's zone is resolved once;
    `tz.localize_date` would re-read the register for every row."""

    def __init__(self, ws: Path):
        self.ws = ws
        try:
            from tz import load_workspace_tz
            self.zone = load_workspace_tz(workspace_path=ws)
        except Exception:
            self.zone = None

    def __call__(self, ts: Any) -> str:
        if not isinstance(ts, str) or not ts:
            return ""
        if len(ts) == 10 and ts.count("-") == 2:
            return ts  # a value that never carried a time is never shifted
        if self.zone is not None:
            try:
                d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                return d.astimezone(self.zone).date().isoformat()
            except ValueError:
                pass
        from tz import localize_date
        return localize_date(ts, workspace_path=self.ws)


def _is_substance(ev_type: str) -> bool:
    if ev_type in _FACT_TYPES:
        return True
    try:
        from log_search import EXTRA_TYPES
        if ev_type in EXTRA_TYPES:
            return True
    except Exception:
        pass
    try:
        from substance_events import is_substance_type
        return bool(is_substance_type(ev_type))
    except Exception:
        return False


def _text(ev: dict) -> str:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for key in ("summary", "title", "what", "decision", "fact", "text", "subject", "note"):
        v = d.get(key)
        if isinstance(v, str) and v.strip():
            return " ".join(v.split())
    return ""


def _as_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _walk_log(ws: Path, people: List[str], threads: List[str], want_words: Iterable[str],
              limit: int, mentions: Dict[str, List[str]], today: date) -> dict:
    """One read of the log, through the org-scoped reader. Gives: the newest
    entries per named id (tagged with it, or naming it in their own words),
    the last contact per person, every promise the read saw (title, owner and
    dates), and which of the question's words the record holds."""
    from cru_match import _commitment_field, _commitment_id
    from event_refs import event_ts, persons_of, threads_of
    from event_seq import event_seq
    from events_io import load_events_org_scoped
    from substance_events import event_type_of

    events, _skipped = load_events_org_scoped(ws)
    day_of = _OwnerDay(ws)
    pset, tset = set(people), set(threads)
    wanted = {w.lower() for w in want_words if w}
    present: set = set()
    mention_rx = {i: re.compile(r"(?<![a-z0-9])(?:" + "|".join(
        re.escape(n.lower()) for n in ns if len(n) >= 4) + r")(?![a-z0-9])")
        for i, ns in mentions.items() if any(len(n) >= 4 for n in ns)}
    substance: Dict[str, bool] = {}
    promises: Dict[str, dict] = {}
    rows: Dict[str, List[dict]] = {i: [] for i in people + threads}
    contact: Dict[str, Dict[str, Optional[dict]]] = {
        p: {"last_inbound_from_them": None, "last_outbound_to_them": None, "last_meeting": None}
        for p in people}

    def ago(day: str) -> Optional[int]:
        d0 = _as_date(day)
        return (today - d0).days if d0 else None

    for ev in events:
        if not isinstance(ev, dict):
            continue
        t = event_type_of(ev)
        if not isinstance(t, str):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        text = _text(ev)
        low_text = text.lower()
        if text and wanted - present:
            present |= {w for w in wanted - present if w in low_text}
        if t == "commitment":
            cid = str(_commitment_id(ev))
            day0 = day_of(str(event_ts(ev) or ""))
            keep = promises.get(cid) or {"logged": day0, "logged_days_ago": ago(day0)}
            keep.update({"title": _commitment_field(ev, "title") or keep.get("title") or "",
                         "owner_id": _commitment_field(ev, "owner_id") or keep.get("owner_id") or "",
                         "due": _commitment_field(ev, "due") or keep.get("due") or "",
                         "created_on": d.get("created_on") or keep.get("created_on"),
                         "promised_on": d.get("promised_on") or keep.get("promised_on"),
                         "due_as_said": d.get("due_text") or keep.get("due_as_said")})
            promises[cid] = keep
        if not (pset or tset):
            continue
        ev_people = persons_of(ev) & pset
        ev_threads = threads_of(ev) & tset
        if text:
            for i, rx in mention_rx.items():
                if rx.search(low_text):
                    (ev_people if i in pset else ev_threads).add(i)
        if not (ev_people or ev_threads):
            continue
        day = day_of(str(event_ts(ev) or ""))
        seq = event_seq(ev)
        for p in ev_people:
            slot = None
            if t == "interaction":
                slot = {"inbound": "last_inbound_from_them",
                        "outbound": "last_outbound_to_them"}.get(str(d.get("direction") or ""))
            elif t in ("meeting", "meeting_processed"):
                slot = "last_meeting"
            if slot:
                cur = contact[p][slot]
                if cur is None or (day, _seq_key(seq)) >= (cur["date"], _seq_key(cur["seq"])):
                    contact[p][slot] = {"date": day, "days_ago": ago(day), "seq": seq,
                                        "what": text[:160] or None}
        if t not in substance:
            substance[t] = _is_substance(t)
        if not substance[t] or not text:
            continue
        row = {"seq": seq, "date": day, "days_ago": ago(day), "type": t, "text": text[:260]}
        for key in ("id", "due", "due_text", "status", "promised_on", "created_on", "topic"):
            if d.get(key):
                row[key] = d[key]
        for i in list(ev_people) + list(ev_threads):
            rows[i].append(row)
    newest = {}
    for i, lst in rows.items():
        lst.sort(key=lambda r: (r["date"], _seq_key(r["seq"])), reverse=True)
        kept = [dict(r) for r in lst[:limit]]
        for older in kept[1:]:
            older["no_longer_current"] = "an older entry; a newer one above may replace it"
        newest[i] = {"newest_first": kept, "shown": len(kept), "on_file": len(lst)}
    return {"recent": newest, "contact": contact, "present": present, "promises": promises}


# ---------------------------------------------------------------------------
# Promises
# ---------------------------------------------------------------------------

def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _open_rows(ws: Path, now_iso: Optional[str], visible: Dict[str, dict]) -> dict:
    """The open list's rows the org-scoped read saw, with THE overdue rule's
    verdict on each. The list is read once per call."""
    import open_list
    verdict = open_list.verdict(ws, now_iso)  # the one count; never recomputed here
    counts = verdict["counts"]
    late_ids = {str(i) for i in verdict["counted_ids"]}
    now = datetime.fromisoformat(now_iso) if now_iso else None
    doc, from_log = open_list.read_list(ws, now)
    every = [r for r in (doc.get("rows") or []) if isinstance(r, dict)]
    rows = [r for r in every if str(r.get("id")) in visible]
    when = now or datetime.now(timezone.utc)

    def shown(r: dict) -> bool:
        if r.get("observed"):
            return False
        try:
            return not open_list.is_snoozed(r, when)
        except TypeError:
            return True

    return {"rows": rows, "late_ids": late_ids & {str(r.get("id")) for r in rows},
            "shown": [r for r in rows if shown(r)], "held_back": len(every) - len(rows),
            "today": date.fromisoformat(str(counts["today"])), "counted_from_log": bool(from_log)}


def _promises(opened: dict, names: Dict[str, str], visible: Dict[str, dict], low: str,
              people: List[str], thread: Optional[str], limit: int) -> dict:
    rows, late_ids, today = opened["rows"], opened["late_ids"], opened["today"]

    def late(r: dict) -> bool:
        return str(r.get("id")) in late_ids

    def slim(r: dict) -> dict:
        seen = visible.get(str(r.get("id"))) or {}
        direction = {"you_owe": "you owe this", "owed_to_you": "owed to you"}.get(
            str(r.get("direction") or ""), "direction not set")
        return {"id": r.get("id"), "title": seen.get("title") or r.get("title"),
                "direction": direction,
                "owner": names.get(str(r.get("owner_id") or "")) or None,
                "other_party": [names.get(str(c)) or str(c) for c in (r.get("counterparty_ids") or [])],
                "due": r.get("due") or None, "confirmed": bool(r.get("confirmed")),
                "overdue": late(r),
                "work_stream": names.get(str(r.get("thread_id") or "")) or None}

    late_rows = [r for r in rows if late(r)]
    confirmed_shown = [r for r in opened["shown"] if r.get("confirmed")]
    in_window = [r for r in rows if _as_date(r.get("due")) is not None
                 and today <= _as_date(r.get("due")) <= today + timedelta(days=7)]
    you_owe_late = sum(1 for r in late_rows if r.get("direction") == "you_owe")
    owed_late = sum(1 for r in late_rows if r.get("direction") == "owed_to_you")
    out: Dict[str, Any] = {"today": today.isoformat(),
                           "counted_from_log": opened["counted_from_log"]}
    out["whole_workspace"] = {
        "overdue (confirmed, past due, not snoozed or waiting)": len(late_rows),
        "overdue that you owe": you_owe_late,
        "overdue that others owe you": owed_late,
        "open and confirmed": len(confirmed_shown),
        "waiting for you to confirm": sum(1 for r in opened["shown"] if not r.get("confirmed")),
        "due in the next seven days, counting today": len(in_window),
        "of those due in the next seven days, confirmed": sum(1 for r in in_window if r.get("confirmed")),
    }
    lead: List[str] = []
    asks_overdue = "overdue" in low
    chosen: List[dict] = []
    for person in people[:3]:
        who = names.get(person) or person
        owned = [r for r in rows if str(r.get("owner_id") or "") == person]
        you_owe = [r for r in rows if r.get("direction") == "you_owe"
                   and person in [str(c) for c in (r.get("counterparty_ids") or [])]]
        if thread:
            owned = [r for r in owned if str(r.get("thread_id") or "") == thread]
            you_owe = [r for r in you_owe if str(r.get("thread_id") or "") == thread]
        chosen += [r for r in owned + you_owe if r not in chosen]
        out.setdefault("for_people", []).append({
            "name": who,
            f"open commitments {who} owns": len(owned),
            f"of those {who} owns, confirmed": sum(1 for r in owned if r.get("confirmed")),
            f"of those {who} owns, overdue": sum(1 for r in owned if late(r)),
            f"open commitments you owe {who}": len(you_owe),
            f"of those you owe {who}, confirmed": sum(1 for r in you_owe if r.get("confirmed")),
            f"of those you owe {who}, overdue": sum(1 for r in you_owe if late(r)),
        })
        conf = sum(1 for r in owned if r.get("confirmed"))
        where = f" on {names.get(thread)}" if thread else ""
        if asks_overdue:
            lead.append(f"{who} owns {_plural(sum(1 for r in owned if late(r)), 'overdue commitment')}"
                        f"{where}, of {len(owned)} open.")
            lead.append(f"You owe {who} {_plural(sum(1 for r in you_owe if late(r)), 'overdue commitment')}"
                        f"{where}, of {len(you_owe)} open.")
        else:
            lead.append(f"{who} owns {_plural(len(owned), 'open commitment')}{where} "
                        f"({conf} confirmed, {len(owned) - conf} waiting to be confirmed).")
            lead.append(f"You owe {who} {_plural(len(you_owe), 'open commitment')}{where} "
                        f"({sum(1 for r in you_owe if r.get('confirmed'))} confirmed).")
    if thread and not people:
        what = names.get(thread) or thread
        on = [r for r in rows if str(r.get("thread_id") or "") == thread]
        chosen = on
        conf = sum(1 for r in on if r.get("confirmed"))
        out["for_work_stream"] = {"name": what, f"open items on {what}": len(on),
                                  f"of those on {what}, confirmed": conf,
                                  f"of those on {what}, overdue": sum(1 for r in on if late(r))}
        lead.append(f"{what} has {_plural(len(on), 'open item')} ({conf} confirmed, "
                    f"{len(on) - conf} waiting to be confirmed); "
                    f"{sum(1 for r in on if late(r))} overdue.")
    if not (people or thread):
        lead.append(f"{_plural(len(late_rows), 'commitment is', 'commitments are')} overdue: "
                    f"{you_owe_late} you owe, {owed_late} owed to you.")
        if _WINDOW_RX.search(low):
            lead.append(f"{_plural(len(in_window), 'commitment comes', 'commitments come')} due in "
                        f"the next seven days, counting today "
                        f"({sum(1 for r in in_window if r.get('confirmed'))} confirmed).")
            chosen = in_window
        else:
            chosen = late_rows or rows
            undated = [r for r in rows if _as_date(r.get("due")) is None and r.get("confirmed")]
            if undated:
                out["open_with_no_due_date_on_file"] = {
                    "note": "never counted as overdue; a promise made for a relative day "
                            "(for example tomorrow) may be late",
                    "how_many": len(undated), "items": [slim(r) for r in undated[:10]]}
    if opened["held_back"]:
        # said last: the answer comes first, the reason it may differ from the Hub after
        out["personal_items_left_out"] = opened["held_back"]
        lead.append(f"{_plural(opened['held_back'], 'personal item is', 'personal items are')} "
                    f"left out of these counts.")
    chosen = sorted(chosen, key=lambda r: str(r.get("due") or "9999"))
    out["items"] = [slim(r) for r in chosen[:limit]]
    out["items_shown"] = len(out["items"])
    out["items_matching"] = len(chosen)
    return {"evidence": out, "lead": lead}


def _title_tokens(text: str) -> set:
    return set(_TOKEN_RX.findall(str(text or "").lower()))


def _promise_lookup(words: List[str], names: Dict[str, str], visible: Dict[str, dict],
                    open_by_id: Dict[str, dict]) -> List[dict]:
    """Promises whose title holds the question's words, open or not. Matched
    against the titles the org-scoped read saw, so nothing behind the wall is
    matched or shown. Every word of a try must be in the title; a single word
    is never tried, because one word matches anything."""
    words = [w.lower() for w in words]
    tries = [words] if len(words) >= 2 else []
    tries += [words[i:i + 2] for i in range(len(words) - 1)]
    out: List[dict] = []
    for q in tries:
        need = set(t for w in q for t in _TOKEN_RX.findall(w))
        if len(need) < 2:
            continue
        for cid, seen in visible.items():
            if need <= _title_tokens(seen.get("title")) and not any(o["id"] == cid for o in out):
                row = {"id": cid, "title": seen.get("title"),
                       "owner": names.get(str(seen.get("owner_id") or "")) or None,
                       "due": (open_by_id.get(cid) or {}).get("due") or seen.get("due") or None,
                       "state": "open" if cid in open_by_id else "not open (closed, done or parked)"}
                for k in ("logged", "logged_days_ago", "created_on", "promised_on", "due_as_said"):
                    if seen.get(k) is not None:
                        row[k] = seen[k]
                out.append(row)
        if out:
            break
    return out[:5]


def _email_lookup(ask: str, reg: Dict[str, List[dict]]) -> List[dict]:
    out = []
    for addr in dict.fromkeys(a.lower() for a in _EMAIL_RX.findall(ask)):
        who = []
        for rec in reg["people"]:
            held = [e.lower() for e in _strings(rec.get("emails")) + _strings(rec.get("email"))
                    + _strings(rec.get("shared_emails")) + _strings(rec.get("aliases"))]
            if addr in held:
                who.append(_name(rec))
        out.append({"address": addr, "people_on_file_with_it": who})
    return out


def _brief_lines(ws: Path, name_words: List[str], topic: List[str], limit: int = 6) -> List[str]:
    """Lines of the workspace's standing instructions that name what the
    question names, or hold its words. The brief is meant to be in view in
    every chat; cr1#1550 found it often is not, so the lines that bear on the
    question travel in the pack."""
    try:
        text = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    names_low = [n.lower() for n in name_words if len(n) >= 4]
    topic_low = {w.lower() for w in topic if len(w) > 3}
    out: List[str] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*0123456789. ").strip()
        if not line or raw.lstrip().startswith("#") or len(line) > 400:
            continue
        low = line.lower()
        toks = set(_TOKEN_RX.findall(low))
        if any(n in low for n in names_low) or len(topic_low & toks) >= 2 \
                or any(len(w) >= 7 for w in topic_low & toks):
            out.append(line)
            if len(out) >= limit:
                break
    return out


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

def _decision_row(r: dict) -> dict:
    return {"id": r.get("id"), "what": r.get("what"), "when": r.get("when"),
            "stands": bool(r.get("stands")), "status": r.get("status"),
            "work_stream": (r.get("thread") or {}).get("name") if isinstance(r.get("thread"), dict) else None,
            "who": (r.get("who") or {}).get("text") if isinstance(r.get("who"), dict) else None}


def _seq_key(value: Any) -> int:
    """A sortable position for a seq: a bad or missing one sorts first."""
    from event_seq import coerce_seq
    n = coerce_seq(value)
    return -1 if n is None else n


def _order(r: dict) -> Tuple[str, int]:
    return (str(r.get("when") or ""), _seq_key(r.get("seq")))


def _decisions(ws: Path, ask: str, topic_words: List[str], thread: Optional[str],
               limit: int) -> dict:
    import decision_list
    lead: List[str] = []
    quoted = _QUOTED_RX.findall(ask)
    if quoted:
        want = " ".join(quoted[0].split()).lower()
        rows = [r for r in (decision_list.decision_list(ws, thread=thread).get("rows") or [])
                if isinstance(r, dict)]
        exact = sorted([r for r in rows
                        if " ".join(str(r.get("what") or "").split()).lower() == want],
                       key=_order, reverse=True)
        out: Dict[str, Any] = {"asked_about": quoted[0],
                               "matches": [_decision_row(r) for r in exact[:limit]]}
        if exact:
            r = exact[0]
            tid = (r.get("thread") or {}).get("id") if isinstance(r.get("thread"), dict) else None
            newer = sorted([x for x in rows if x is not r and tid
                            and isinstance(x.get("thread"), dict) and x["thread"].get("id") == tid
                            and _order(x) > _order(r)], key=_order, reverse=True)
            if r.get("stands"):
                lead.append(f"The decision list shows this as standing, logged {r.get('when')}.")
            else:
                lead.append(f"This decision no longer stands (status: {r.get('status')}), "
                            f"logged {r.get('when')}.")
            if newer:
                out["newer_decisions_on_the_same_work_stream"] = [_decision_row(x) for x in newer[:3]]
                lead.append(f"A newer decision on the same work stream, {newer[0].get('when')}: "
                            f"{newer[0].get('what')}. Check whether it replaces the one asked about.")
        return {"evidence": out, "lead": lead, "found": bool(exact)}
    if topic_words:
        ans = decision_list.what_did_we_decide(ws, " ".join(topic_words), thread=thread)
        cur = [r for r in (ans.get("current") or []) if isinstance(r, dict)]
        out = {"topic": " ".join(topic_words), "matches": [_decision_row(r) for r in cur[:limit]],
               "matching": len(cur)}
        return {"evidence": out, "lead": lead, "found": bool(cur)}
    rows = sorted([r for r in (decision_list.decision_list(ws, thread=thread).get("rows") or [])
                   if isinstance(r, dict)], key=_order, reverse=True)
    out = {"newest_first": [_decision_row(r) for r in rows[:limit]], "on_file": len(rows)}
    standing = [r for r in rows if r.get("stands")]
    if standing and thread:
        r = standing[0]
        lead.append(f"The most recent standing decision here: {r.get('what')} ({r.get('when')}).")
    return {"evidence": out, "lead": lead, "found": bool(rows)}


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------

def answer_pack(workspace_root, ask: str = "", now_iso: Optional[str] = None,
                limit: int = 20) -> Dict[str, Any]:
    """The pack for one question. Read only; never raises on a source."""
    ws = Path(str(workspace_root))
    ask = " ".join(str(ask or "").split())[:2000]
    pack: Dict[str, Any] = {"ok": True, "reader_version": READER_VERSION, "ask": ask,
                            "found": {}, "lead": [], "evidence": {}, "enough": False,
                            "say": None, "how": list(HOW), "degraded": []}
    if not ask:
        pack["reason"] = "no question"
        return pack
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        limit = 20
    low = ask.lower()
    reg = _register(ws)
    by_id = {str(r["id"]): r for coll in reg.values() for r in coll}
    names = {i: _name(r) for i, r in by_id.items()}
    kind_of = {str(r["id"]): coll for coll, rs in reg.items() for r in rs}

    # 1. who and what the question names
    named, taken = _named_in(ask, reg)
    ids: List[str] = [str(r["id"]) for coll in ("people", "threads", "orgs") for r in named[coll]]
    ambiguous: List[dict] = []
    near_miss: List[dict] = []
    unknown: List[str] = []   # capitalised, not on the register; a name, or a topic
    spans = _spans(ask)
    for span, opening in spans:
        if any(span.lower() in t for t in taken):
            continue
        cands = _resolve(ws, span)
        if not cands:
            if not (opening and " " not in span):  # a lone opening word is the question's own
                unknown.append(span)
            continue
        top = max(c["confidence"] for c in cands)
        best = [c for c in cands if c["confidence"] >= top - 0.001]
        if " " in span and not any(c["via"].startswith("exact")
                                   or (c["via"] == "fuzzy" and c["confidence"] >= 0.9)
                                   for c in best):
            # a full name that only half matches is someone else
            near_miss.append({"asked": span,
                              "nearest_on_file": [_name(c["record"]) for c in cands[:3]]})
            continue
        got = list(dict.fromkeys(str(c["record"]["id"]) for c in best))
        if len(got) > 1:
            ambiguous.append({"asked": span, "could_be": [names.get(i) or i for i in got],
                              "records": [_person_card(by_id[i], by_id) if kind_of.get(i) == "people"
                                          else {"id": i, "name": names.get(i)} for i in got[:6]
                                          if i in by_id]})
            continue
        ids.append(got[0])
    # a name typed in lower case ("what does rio owe me")
    single = _single_names(reg)
    spanned = {w.lower() for span, _ in spans for w in span.split()}
    for tok in dict.fromkeys(_TOKEN_RX.findall(low)):
        key = re.sub(r"'s$", "", tok)
        if key in single and key not in spanned and not any(key in t for t in taken):
            got = [i for i in single[key] if i not in ids]
            if len(single[key]) == 1 and got:
                ids.append(got[0])
            elif len(single[key]) > 1 and not any(i in ids for i in single[key]):
                ambiguous.append({"asked": key, "could_be": [names.get(i) or i for i in single[key]],
                                  "records": [{"id": i, "name": names.get(i)} for i in single[key][:6]]})
    ids = [i for i in dict.fromkeys(ids) if i in by_id]
    people = [i for i in ids if kind_of.get(i) == "people"]
    threads = [i for i in ids if kind_of.get(i) == "threads"]
    orgs = [i for i in ids if kind_of.get(i) == "orgs"]
    pack["found"] = {
        "people": [_person_card(by_id[p], by_id) for p in people],
        "work_streams": [_stream_card(by_id[t], by_id) for t in threads],
        "companies": [_company_card(by_id[o], reg) for o in orgs],
    }
    if ambiguous:
        pack["found"]["more_than_one_match"] = ambiguous
    if near_miss:
        pack["found"]["not_on_file_but_near"] = near_miss
    person = people[0] if people else None
    thread = threads[0] if threads else None
    if orgs and not threads:
        # a company's work streams stand in for it when no work stream was named
        threads_for_log = [str(t["id"]) for t in reg["threads"]
                           if str(t.get("org_id") or t.get("affiliation_id") or "") in orgs][:4]
    else:
        threads_for_log = threads

    # 2. the words the question is about
    name_words = set(_TOKEN_RX.findall(" ".join(names.get(i, "") for i in ids).lower()))
    for i in ids:
        for a in _strings(by_id[i].get("aliases")):
            name_words |= set(_TOKEN_RX.findall(a.lower()))
    plain = _EMAIL_RX.sub(" ", _QUOTED_RX.sub(" ", ask))
    topic = [w for w in _WORD_RX.findall(plain)
             if w.lower() not in _STOP and w.lower() not in _ASK_WORDS
             and w.lower() not in _CALENDAR and w.lower() not in name_words
             and re.sub(r"'s$", "", w.lower()) not in name_words]
    content = [w for w in dict.fromkeys(w.lower() for w in topic)
               if len(w) > 3 and w not in _MEDIUM]

    want_commit = bool(_COMMIT_RX.search(low))
    want_decide = bool(_DECIDE_RX.search(low))
    want_contact = bool(_CONTACT_RX.search(low))
    want_streams = bool(_STREAM_RX.search(low))
    want_fields = [(word, key) for word, key in _FIELDS if re.search(rf"\b{word}\b", low)]
    nothing_asked = not (want_commit or want_decide or want_contact or want_streams or want_fields)

    ev = pack["evidence"]
    lead: List[str] = pack["lead"]

    # 3. one read of the log, and one of the open list
    try:
        import open_list
        today = date.fromisoformat(str(open_list.headline_counts(ws, now_iso)["today"]))
    except Exception:
        today = date.today()
    pack["today"] = today.isoformat()
    unknown_words = [w.lower() for span in unknown for w in span.split() if w not in _JOINERS]
    mention = {i: [names.get(i, "")] + _strings(by_id[i].get("aliases"))
               for i in people + threads_for_log}
    try:
        walked = _walk_log(ws, people, threads_for_log, content + unknown_words, limit, mention, today)
    except Exception as exc:  # a source that cannot be read is said, never raised
        walked = {"recent": {}, "contact": {}, "present": set(content + unknown_words),
                  "promises": {}}
        pack["degraded"].append(f"log: {type(exc).__name__}")
    visible: Dict[str, dict] = walked["promises"]
    opened: Optional[dict] = None
    if visible:
        try:
            opened = _open_rows(ws, now_iso, visible)
        except Exception as exc:
            pack["degraded"].append(f"open list: {type(exc).__name__}")
    for i in people + threads_for_log:
        if walked["recent"].get(i, {}).get("shown"):
            ev.setdefault("recent_entries", {})[names.get(i) or i] = walked["recent"][i]
    for p in people[:3]:
        if not (want_contact or nothing_asked):
            break
        c = dict(walked["contact"].get(p) or {})
        on_register = _strings(by_id[p].get("last_interaction"))
        c["last_interaction_on_register"] = on_register[0][:10] if on_register else None
        ev.setdefault("last_contact", []).append({"with": names.get(p), **c})
        if want_contact:
            heard = c.get("last_inbound_from_them")
            reg_day = c["last_interaction_on_register"]
            who = names.get(p)
            if heard and (not reg_day or heard["date"] >= reg_day):
                lead.append(f"Last heard from {who}: {heard['date']}"
                            + (f" ({heard['what']})." if heard.get("what") else "."))
            elif reg_day:
                lead.append(f"The register's last interaction with {who} is {reg_day}. "
                            + (f"The newest inbound message in the log is {heard['date']}."
                               if heard else f"The log holds no inbound message from {who}."))

    # 4. promises, counted by the open list
    if opened and (want_commit or (nothing_asked and (people or thread))):
        got = _promises(opened, names, visible, low, people, thread, limit)
        ev["commitments"] = got["evidence"]
        if want_commit:
            lead.extend(got["lead"])

    # 4b. a promise asked for by its title, open or not
    words_for_title = [w for w in topic if len(w) > 2][:6]
    if words_for_title and visible and opened:
        open_by_id = {str(r.get("id")): r for r in opened["rows"]}
        found_promises = _promise_lookup(words_for_title, names, visible, open_by_id)
        if found_promises:
            ev["promises_matching_the_words"] = found_promises
            if len(found_promises) == 1:
                f = found_promises[0]
                lead.append(f"{f['title']} ({f['id']}) is {f['state']}"
                            + (f"; owner {f['owner']}" if f.get("owner") else "")
                            + (f"; due {f['due']}" if f.get("due") else "") + ".")

    # 4c. an email address asked about
    emails = _email_lookup(ask, reg)
    if emails:
        ev["email_addresses"] = emails
        for e in emails:
            if e["people_on_file_with_it"]:
                lead.append(f"{e['address']} is on file for {', '.join(e['people_on_file_with_it'])}.")

    # 4d. the standing instructions that bear on the question
    brief = _brief_lines(ws, [names.get(i, "") for i in ids]
                         + [a for i in ids for a in _strings(by_id[i].get("aliases"))], topic)
    if brief:
        ev["standing_instructions"] = brief

    # 5. decisions, with what still stands
    decided_found = True
    if want_decide:
        try:
            got = _decisions(ws, ask, [w for w in topic if len(w) > 2][:6], thread, limit)
            ev["decisions"] = got["evidence"]
            lead.extend(got["lead"])
            decided_found = got["found"]
        except Exception as exc:
            pack["degraded"].append(f"decision list: {type(exc).__name__}")

    # 6. work streams by status
    if want_streams:
        by_status: Dict[str, int] = {}
        for t in reg["threads"]:
            s = str(t.get("status") or "not set")
            by_status[s] = by_status.get(s, 0) + 1
        ev["work_streams_by_status"] = by_status
        if thread:
            lead.append(f"{names.get(thread)} is {by_id[thread].get('status') or 'not set'}.")
        elif not people:
            lead.append(f"{_plural(by_status.get('active', 0), 'work stream is', 'work streams are')} active.")

    # 7. the log on the question's own words
    words = [w for w in topic if len(w) > 2][:6]
    if words:
        try:
            from log_search import search_log
            hits = search_log(ws, " ".join(words), thread=thread, person=person, limit=8)
            if hits.get("total"):
                ev["log_search"] = {
                    "words": " ".join(words), "entries_holding_any_of_the_words": hits.get("total"),
                    "hits": [{"seq": h.get("seq"), "id": h.get("id"), "date": h.get("date"),
                              "type": h.get("type"), "text": h.get("title") or h.get("snippet")}
                             for h in (hits.get("hits") or [])]}
        except Exception as exc:
            pack["degraded"].append(f"log search: {type(exc).__name__}")

    # 8. is it enough
    reasons: List[str] = []
    present = set(walked.get("present") or [])
    brief_text = " ".join(brief).lower()
    cards_text = " ".join(str(v).lower()
                          for card in pack["found"]["people"] + pack["found"]["work_streams"]
                          for v in card.values() if isinstance(v, (str, list)))

    def in_record(word: str) -> bool:
        return word in present or word in brief_text or word in cards_text

    for nm in near_miss:
        reasons.append(f"No one named {nm['asked']} is on file. The nearest on file: "
                       f"{', '.join(nm['nearest_on_file'])}.")
    for amb in ambiguous:
        reasons.append(f"{amb['asked']} matches more than one record: "
                       f"{', '.join(amb['could_be'])}. Ask which one.")
    never: List[str] = []
    for span in unknown:
        parts = [w.lower() for w in span.split() if w not in _JOINERS]
        if all(in_record(w) for w in parts):
            continue  # a capitalised topic the record does hold, not a missing name
        if len(parts) >= 2:
            reasons.append(f"No person, company or work stream named {span} is on file, "
                           f"and the record never mentions it.")
        else:
            never.append(span)
    if len(people) == 1:
        for word, key in want_fields:
            if not pack["found"]["people"][0].get(key):
                reasons.append(f"No {word} is on file for {pack['found']['people'][0]['name']}.")
    for e in emails:
        if not e["people_on_file_with_it"]:
            reasons.append(f"No one on file uses {e['address']}.")
    about = _ABOUT_RX.search(plain)
    if about:
        about_words = [w.lower() for w in _WORD_RX.findall(about.group(1)) if w.lower() in content]
        if about_words and not any(in_record(w) for w in about_words) \
                and not ev.get("promises_matching_the_words"):
            reasons.append("The record never mentions " + " ".join(about_words[:4]) + ".")
    if want_decide and not decided_found and not reasons:
        reasons.append("No decision on file matches this.")
    # a lone capitalised word the record never holds: decline only when the pack
    # has nothing else to answer from
    if never and not reasons and not lead and not ids:
        reasons.append("The record never mentions " + ", ".join(dict.fromkeys(never)) + ".")
    elif never:
        pack["found"]["words_not_in_the_record"] = list(dict.fromkeys(never))
    # what the question is about has to be in the record, unless a count, a
    # field on a card or a standing instruction already answers it
    is_request = bool(_REQUEST_RX.match(low))  # "send ...", "draft ...": the pack is context
    if not reasons and not lead and content and not want_fields and not is_request:
        held = [w for w in content if in_record(w)]
        if len(held) * 2 <= len(content):
            missing = [w for w in content if w not in held][:4]
            reasons.append("The record never mentions " + ", ".join(missing) + ".")
    if not reasons and not (ids or ev):
        reasons.append("Nothing on file matches this question.")
    pack["enough"] = not reasons
    if reasons:
        pack["say"] = NOTHING_LINE + " " + " ".join(dict.fromkeys(reasons))
    return pack


def main(argv=None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("workspace_root")
    ap.add_argument("ask", nargs="?", default="")
    ap.add_argument("--now")
    args = ap.parse_args(argv)
    print(json.dumps(answer_pack(args.workspace_root, args.ask, now_iso=args.now), indent=1,
                     default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
