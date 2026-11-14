#!/usr/bin/env python3
"""Keyword search over the log itself (cr1#1014, train 4, slice 3).

WHY THIS EXISTS
---------------
An ask like "when did we last talk about the warehouse lease?" or "what did
we decide about hosting?" needs the log, not only the cards built from it.
The readers that existed answered for one thread, one person or the open
list; nothing searched the record itself by its words.

THE RULINGS IT FOLLOWS (owner, 2026-11-10; recorded on cr1#1009)
  * A7: no vector search. A match is a keyword match over an entry's own
    words (its title, summary and the other text fields named in
    `TEXT_KEYS`). Nothing is embedded.
  * C3: the log is the source. Every hit carries the entry's number (`seq`)
    and, for a promise or a decision, the id train 1 (#1011) writes
    (`cmt_...`, `dec_...`), its date and where it came from, so an answer can
    be checked against the entry itself.

WHAT IT SEARCHES
  The record of the work: every entry `substance_events` calls substance
  (promises, decisions, meetings, notes, mail and the rest), plus the
  meeting summaries and feedback that slice 2 adds. The system's own
  bookkeeping (receipts, gate runs, connector reads) is left out. It reads
  through the org-scoped reader, so the personal lane stays out of answers.

It writes nothing. `search_log` is on the read door's list.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from event_seq import event_seq  # noqa: E402

#: The fields whose words are searched, at the top of `data`.
TEXT_KEYS = ("title", "summary", "description", "text", "note", "notes",
             "said", "about", "subject", "rationale", "context", "evidence",
             "reason", "decision", "outcome", "topic", "name", "change_summary")
#: Entries that are not substance by `substance_events` but are searched.
EXTRA_TYPES = frozenset({"meeting_summary", "feedback_captured"})
#: Where a promise's or a decision's id sits on its own row and on the rows
#: that follow it (a close, an update, a supersede), in reading order.
_ID_KEYS = {"commitment": ("commitment_id", "id"), "decision": ("decision_id", "id")}

SNIPPET_CHARS = 220
MAX_LIMIT = 100

#: A word is a run of letters and digits: "lease-renewal" and "lease's" hold
#: the word "lease", the same way a query's words are split.
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset({
    "a", "an", "at", "by", "do", "go", "if", "in", "is", "it", "me", "my",
    "no", "of", "on", "or", "so", "to", "up", "us", "we", "be", "as", "am",
    "the", "and", "for", "with", "that", "this", "what", "does", "did", "say",
    "said", "says", "about", "our", "your", "from", "into", "are", "was",
    "were", "has", "have", "which", "who", "how", "when", "where", "there",
    "their", "them", "they", "you", "any", "all", "its", "not", "last",
    "find", "search", "log", "record", "anything", "something", "ever",
})


def _tokens(text: str) -> List[str]:
    return [t for t in _WORD.findall((text or "").lower())
            if len(t) >= 2 and t not in _STOP]


def _strings(v: Any) -> List[str]:
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    if isinstance(v, list):
        return [x.strip() for x in v if isinstance(x, str) and x.strip()]
    return []


def _text_of(ev: dict) -> str:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    parts: List[str] = []
    for k in TEXT_KEYS:
        parts.extend(_strings(d.get(k)))
    topics = d.get("topics")
    for t in topics if isinstance(topics, list) else []:
        if isinstance(t, dict):
            parts.append(f"{t.get('topic') or ''} {t.get('said') or ''}".strip())
    return "\n".join(p for p in parts if p)


def _id_of(ev: dict, ev_type: str) -> Optional[str]:
    """The promise's or decision's id this row is about, if any.

    A promise or a decision row carries its own `data.id`. A row that closes
    a promise names its target the way `closure_index.closer_target_id` reads
    it, and one that resolves or supersedes a decision the way
    `event_types.decision_resolve_targets` / `decision_supersede_targets`
    read it: the same chains the readers close on, never a copy of them."""
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    if ev_type in ("commitment", "decision"):
        v = d.get("id") or ev.get("id")
        return v.strip() if isinstance(v, str) and v.strip() else None
    if ev_type.startswith("commitment"):
        # The closers' chain reads `data.thread_id` early, which on any
        # other commitment row is the thread: only a closer walks it.
        from closure_index import CLOSER_TYPES, closer_target_id
        if ev_type in CLOSER_TYPES:
            v = closer_target_id(ev)
            if v:
                return v
    elif ev_type.startswith("decision"):
        from event_types import decision_resolve_targets, decision_supersede_targets
        for ids, _seqs in (decision_resolve_targets(ev), decision_supersede_targets(ev)):
            for v in ids:
                if isinstance(v, str) and v.strip():
                    return v.strip()
    else:
        return None
    for k in _ID_KEYS["commitment" if ev_type.startswith("commitment") else "decision"]:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _searchable(ev_type: Optional[str]) -> bool:
    if ev_type in EXTRA_TYPES:
        return True
    from substance_events import is_substance_type
    return is_substance_type(ev_type)


def _entities(workspace_root) -> dict:
    try:
        from entities_io import read_entities_json
        data = read_entities_json(Path(workspace_root) / "_hq" / "data" / "entities.json")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _records(entities: dict, collection: str) -> List[dict]:
    try:
        from entities_io import entities_collection
        return [r for r in entities_collection(entities, collection) if isinstance(r, dict)]
    except Exception:
        return []


def _match_ids(entities: dict, collection: str, want: Optional[str]) -> Tuple[Optional[set], bool]:
    """(ids, matched): the ids whose id, name, alias or (for a person) first
    name is `want`, case folded, and whether any record matched. (None, True)
    for no filter. The raw word stays in the set, so an id written on a row
    but missing from the register still filters."""
    if not want:
        return None, True
    w = str(want).strip().lower()
    out = {w}
    matched = False
    for r in _records(entities, collection):
        names = [str(r.get("canonical_name") or ""), str(r.get("display_name") or "")]
        names += [a for a in (r.get("aliases") or []) if isinstance(a, str)]
        folded = {n.strip().lower() for n in names if n.strip()}
        first = {n.split()[0] for n in folded if collection == "people" and " " in n}
        if w in folded or w in first or w == str(r.get("id") or "").lower():
            out.add(str(r.get("id") or "").lower())
            matched = True
    return out, matched


def _names(entities: dict, collection: str) -> Dict[str, str]:
    return {str(r.get("id")): str(r.get("canonical_name") or r.get("display_name") or "")
            for r in _records(entities, collection) if r.get("id")}


def _snippet(text: str, terms: List[str]) -> str:
    flat = " ".join(text.split())
    low = flat.lower()
    hit = min((i for i in (re.search(r"\b" + re.escape(t), low) for t in terms) if i),
              key=lambda m: m.start(), default=None)
    if hit is None or len(flat) <= SNIPPET_CHARS:
        return flat[:SNIPPET_CHARS] + ("…" if len(flat) > SNIPPET_CHARS else "")
    start = max(0, hit.start() - SNIPPET_CHARS // 3)
    end = min(len(flat), start + SNIPPET_CHARS)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


def search_log(workspace_root, query: str, thread: Optional[str] = None,
               person: Optional[str] = None, since: Optional[str] = None,
               until: Optional[str] = None, types: Optional[Iterable[str]] = None,
               limit: int = 25) -> Dict[str, Any]:
    """Entries of the log whose own words hold the query's words.

    Read only. Ranked by how many of the query's words an entry holds (a
    phrase match counts most), then newest first. `thread` and `person` take
    an id, a name or an alias; `since` and `until` are dates; `types` limits
    the entry types. Each hit carries its `seq`, its `id` when it is a
    promise or a decision, its date, its type, a snippet of its own words and
    its source.
    """
    terms = _tokens(query or "")
    if not terms:
        return {"ok": True, "total": 0, "hits": [],
                "reason": "no words to search for" if (query or "").strip() else "no query"}
    from events_io import iter_events_org_scoped
    from event_refs import threads_of, persons_of, event_ts
    from tz import localize_date
    ents = _entities(workspace_root)
    tids, t_ok = _match_ids(ents, "threads", thread)
    pids, p_ok = _match_ids(ents, "people", person)
    thread_names = _names(ents, "threads")
    want_types = {str(t).strip() for t in types} if types else None
    unmatched = [label for label, ok in (("thread", t_ok), ("person", p_ok)) if not ok]
    phrase = " ".join(terms)
    scored: List[Tuple[int, str, int, dict]] = []
    for ev in iter_events_org_scoped(workspace_root):
        if not isinstance(ev, dict):
            continue
        from substance_events import event_type_of
        t = event_type_of(ev)  # `type`, or an older row's `event`
        if not isinstance(t, str):
            continue
        if want_types is not None and t not in want_types:
            continue
        if not _searchable(t):
            continue
        text = _text_of(ev)
        if not text:
            continue
        toks_list = _tokens(text)
        toks = set(toks_list)
        score = sum(1 for term in terms if term in toks)
        if not score:
            continue
        if len(terms) > 1 and phrase in " ".join(toks_list):
            score += len(terms)
        date = localize_date(str(event_ts(ev) or ""), workspace_path=workspace_root)
        if (since or until) and not date:
            continue
        if since and date < str(since)[:10]:
            continue
        if until and date > str(until)[:10]:
            continue
        threads = sorted(threads_of(ev))
        if tids is not None and not ({x.lower() for x in threads} & tids):
            continue
        if pids is not None and not ({p.lower() for p in persons_of(ev)} & pids):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        seq = event_seq(ev)
        hit = {
            "seq": seq,
            "id": _id_of(ev, t),
            "type": t,
            "date": date,
            "title": " ".join(str(d.get("title") or d.get("summary") or d.get("subject") or "").split())[:160],
            "snippet": _snippet(text, terms),
            "threads": [{"id": x, "name": thread_names.get(x)} for x in threads],
            "source": {"skill": ev.get("source_skill"),
                       "ref": d.get("source_ref") or ev.get("source_ref")},
        }
        scored.append((score, date, seq if isinstance(seq, int) else -1, hit))
    scored.sort(key=lambda s: (s[0], s[1], s[2]), reverse=True)
    cap = max(1, min(int(limit or 25), MAX_LIMIT))
    out: Dict[str, Any] = {"ok": True, "total": len(scored), "hits": [s[3] for s in scored[:cap]]}
    if unmatched:
        # A named thread or person that matches no record filters everything
        # out; say so, so "no hits" is never read as "never came up".
        out["unmatched_filters"] = unmatched
    return out
