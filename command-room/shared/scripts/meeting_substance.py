#!/usr/bin/env python3
"""Meeting substance: what was said in each meeting, and feedback as its own entry (cr1#1014).

WHY THIS EXISTS
---------------
A processed meeting left promises and decisions in the log, and a one-line
`data.summary` on its `meeting` row. What was actually said (the topics, who
said what, the reasoning) was nowhere the model could ask. Train 4 of the
memory programme adds it, beside the document shelf (`doc_shelf`):

  * `meeting_summary` - one row per processed meeting (and one per teammate's
    note on it): a summary of what was said, by topic, with the meeting's
    `source_ref`.
  * `feedback_captured` - one row per piece of feedback someone gave in a
    meeting (a request, a complaint, praise), in their words, with the
    meeting's `source_ref`.

THE RULINGS IT FOLLOWS (owner, 2026-11-10; recorded on cr1#1009)
  * C3: a summary is for DISPLAY. Every row carries the meeting's
    `source_ref`, and an ask that needs the exact words goes back to the
    transcript with it (the transcript-search skill, on demand). The rows
    are never edited and never stand in for the transcript.
  * A7: finding a summary is keyword matching over its words, its topics and
    the meeting's title. Nothing is embedded.

ONE WRITER, TWO READERS
  * `record_meeting_substance` appends through the one append path
    (`event_gate.append_event`). It is on `RUN_WRITER_ALLOWLIST`. A second
    summary for the same meeting from the same notes is not written again;
    a teammate's note on the same meeting is its own row.
  * `meeting_summaries` and `feedback_entries` read the log and write
    nothing. Both are on the read door's list.
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

SUMMARY_TYPE = "meeting_summary"
FEEDBACK_TYPE = "feedback_captured"
HOLDER = "meeting_substance"

#: Where the notes a summary was made from came from.
NOTES_FROM = ("transcript", "owner_notes", "teammate_notes")
#: What kind of feedback a row holds.
FEEDBACK_KINDS = ("request", "complaint", "praise", "other")

SUMMARY_MAX_WORDS = 400
TOPICS_MAX = 12
TOPIC_SAID_MAX_WORDS = 60
FEEDBACK_SAID_MAX_WORDS = 80

_WORD = re.compile(r"[a-z0-9][a-z0-9'\-]*")
_STOP = frozenset({
    "a", "an", "at", "by", "do", "go", "if", "in", "is", "it", "me", "my",
    "no", "of", "on", "or", "so", "to", "up", "us", "we", "be", "as", "am",
    "the", "and", "for", "with", "that", "this", "what", "does", "did", "say",
    "said", "says", "about", "our", "your", "from", "into", "are", "was",
    "were", "has", "have", "which", "who", "how", "when", "where", "there",
    "their", "them", "meeting", "meetings", "call", "calls", "tell", "any",
    "all", "its", "not", "they", "you", "anything", "something", "last",
})


class SubstanceError(ValueError):
    """A summary or feedback item that cannot be written, with the reason."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _words(text: str) -> List[str]:
    return (text or "").split()


def _clip_words(text: str, n: int) -> Tuple[str, bool]:
    words = _words(text)
    if len(words) <= n:
        return " ".join(words), False
    return " ".join(words[:n]) + "…", True


def _tokens(text: str) -> List[str]:
    return [t for t in _WORD.findall((text or "").lower())
            if len(t) >= 2 and t not in _STOP]


def _norm(text: str) -> str:
    return " ".join(_WORD.findall((text or "").lower()))


def _str_list(values: Optional[Iterable[Any]]) -> List[str]:
    out: List[str] = []
    for v in values or []:
        s = str(v or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _ref_key(source_ref: Any) -> str:
    """One meeting, one key: `granola:x`, `x` and two casings of one id are
    the same meeting. Built on `meeting_capture.meeting_ref_keys`, the one
    derivation the capture path's already-processed check uses."""
    raw = str(source_ref or "").strip()
    try:
        from meeting_capture import meeting_ref_keys
        keys = meeting_ref_keys(raw)
    except Exception:
        keys = {raw.lower()}
    return min(keys, key=lambda k: (len(k), k)) if keys else ""


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


# ---------------------------------------------------------------------------
# Builders (construction only; `record_meeting_substance` appends)
# ---------------------------------------------------------------------------

def build_meeting_summary_event(*, source_ref: str, summary: str,
                                title: str = "", meeting_ts: Optional[str] = None,
                                topics: Optional[List[Dict[str, Any]]] = None,
                                thread_id: Optional[str] = None,
                                related_thread_ids: Optional[Iterable[str]] = None,
                                person_ids: Optional[Iterable[str]] = None,
                                notes_from: str = "transcript",
                                notes_author: Optional[str] = None,
                                source_skill: str = "meeting-notes") -> dict:
    """One `meeting_summary` row. Raises `SubstanceError` on a row that
    would say nothing or point nowhere."""
    ref = str(source_ref or "").strip()
    if not ref:
        raise SubstanceError("a summary needs the meeting's source_ref, so it can lead back to the transcript")
    text, clipped = _clip_words(summary, SUMMARY_MAX_WORDS)
    if len(_words(text)) < 5:
        raise SubstanceError("a summary needs at least one sentence about what was said")
    nf = str(notes_from or "transcript").strip()
    if nf not in NOTES_FROM:
        raise SubstanceError(f"notes_from must be one of {', '.join(NOTES_FROM)}")
    author = str(notes_author or "").strip()
    if nf == "teammate_notes" and not author:
        raise SubstanceError("a teammate's note needs its author")
    clean_topics = []
    for t in (topics or [])[:TOPICS_MAX]:
        if not isinstance(t, dict):
            continue
        name = " ".join(str(t.get("topic") or "").split())
        said, _ = _clip_words(str(t.get("said") or ""), TOPIC_SAID_MAX_WORDS)
        if name and said:
            clean_topics.append({"topic": name[:120], "said": said})
    data: Dict[str, Any] = {
        "source_ref": ref,
        "title": " ".join(str(title or "").split())[:200],
        "summary": text,
        "topics": clean_topics,
        "notes_from": nf,
        "display_only": True,
    }
    if clipped:
        data["clipped"] = True
    if meeting_ts:
        data["meeting_ts"] = str(meeting_ts).strip()
    if author:
        data["notes_author"] = author
    ev: Dict[str, Any] = {"type": SUMMARY_TYPE, "source_skill": source_skill,
                          "person_ids": _str_list(person_ids), "data": data}
    if thread_id:
        ev["primary_thread_id"] = str(thread_id).strip()
    related = [t for t in _str_list(related_thread_ids) if t != ev.get("primary_thread_id")]
    if related:
        ev["related_thread_ids"] = related
    return ev


def build_feedback_event(*, source_ref: str, said: str, about: str = "",
                         kind: str = "other", from_person_id: Optional[str] = None,
                         from_name: Optional[str] = None, title: str = "",
                         meeting_ts: Optional[str] = None,
                         thread_id: Optional[str] = None,
                         related_thread_ids: Optional[Iterable[str]] = None,
                         source_skill: str = "meeting-notes") -> dict:
    """One `feedback_captured` row: what someone said, in their words."""
    ref = str(source_ref or "").strip()
    if not ref:
        raise SubstanceError("feedback needs the meeting's source_ref")
    text, clipped = _clip_words(said, FEEDBACK_SAID_MAX_WORDS)
    if len(_words(text)) < 3:
        raise SubstanceError("feedback needs the words that were said")
    k = str(kind or "other").strip().lower()
    if k not in FEEDBACK_KINDS:
        raise SubstanceError(f"kind must be one of {', '.join(FEEDBACK_KINDS)}")
    pid = str(from_person_id or "").strip()
    name = " ".join(str(from_name or "").split())
    if not pid and not name:
        raise SubstanceError("feedback needs who gave it")
    data: Dict[str, Any] = {"source_ref": ref, "said": text, "about": " ".join(
        str(about or "").split())[:160], "kind": k,
        "title": " ".join(str(title or "").split())[:200]}
    if clipped:
        data["clipped"] = True
    if pid:
        data["from_person_id"] = pid
    if name:
        data["from_name"] = name
    if meeting_ts:
        data["meeting_ts"] = str(meeting_ts).strip()
    ev: Dict[str, Any] = {"type": FEEDBACK_TYPE, "source_skill": source_skill,
                          "person_ids": [pid] if pid.startswith("person_") else [],
                          "data": data}
    if thread_id:
        ev["primary_thread_id"] = str(thread_id).strip()
    related = [t for t in _str_list(related_thread_ids) if t != ev.get("primary_thread_id")]
    if related:
        ev["related_thread_ids"] = related
    return ev


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------

def _existing_keys(workspace_root) -> Tuple[set, set]:
    # Every row, the owner's personal lane included, so a personal meeting's
    # summary is never written twice either.
    from events_io import iter_events_org_scoped
    summaries, feedback = set(), set()
    for ev in iter_events_org_scoped(workspace_root, drop_personal=False):
        t = ev.get("type")
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if t == SUMMARY_TYPE:
            summaries.add((_ref_key(d.get("source_ref")), d.get("notes_from"),
                           (d.get("notes_author") or "").lower()))
        elif t == FEEDBACK_TYPE:
            feedback.add((_ref_key(d.get("source_ref")), _norm(d.get("said") or "")))
    return summaries, feedback


def record_meeting_substance(workspace_root, summary: Optional[dict] = None,
                             feedback: Optional[List[dict]] = None,
                             source_skill: str = "meeting-notes") -> Dict[str, Any]:
    """Append one meeting's summary and its feedback items.

    `summary` takes the keyword arguments of `build_meeting_summary_event`;
    each `feedback` item those of `build_feedback_event` (the meeting's
    `source_ref`, `title`, `meeting_ts` and `thread_id` are taken from the
    summary when an item leaves them out). Nothing is written when any item
    is refused, so a meeting never ends up half recorded. A summary or a
    feedback item already on file for this meeting is skipped, not doubled.
    """
    summary = dict(summary or {})
    items = [dict(f) for f in (feedback or []) if isinstance(f, dict)]
    if not summary and not items:
        return {"ok": False, "reason": "nothing to record"}
    shared = {k: summary.get(k) for k in ("source_ref", "title", "meeting_ts", "thread_id",
                                          "related_thread_ids")}
    to_write: List[dict] = []
    try:
        if summary:
            to_write.append(build_meeting_summary_event(source_skill=source_skill, **summary))
        for f in items:
            for k, v in shared.items():
                if v and not f.get(k):
                    f[k] = v
            to_write.append(build_feedback_event(source_skill=source_skill, **f))
    except (SubstanceError, TypeError) as exc:
        return {"ok": False, "reason": str(exc)}

    have_summaries, have_feedback = _existing_keys(workspace_root)
    fresh: List[dict] = []
    skipped: List[str] = []
    # A meeting whose summary from these same notes is already on file was
    # processed before: its feedback was recorded then, and a re-run's
    # paraphrase of the same words must not land as new feedback.
    reprocessed = False
    for ev in to_write:
        d = ev["data"]
        if ev["type"] == SUMMARY_TYPE:
            key = (_ref_key(d["source_ref"]), d["notes_from"], (d.get("notes_author") or "").lower())
            if key in have_summaries:
                skipped.append("summary already on file")
                reprocessed = True
                continue
            have_summaries.add(key)
        else:
            if reprocessed:
                skipped.append("feedback already recorded with this meeting")
                continue
            key = (_ref_key(d["source_ref"]), _norm(d["said"]))
            if key in have_feedback:
                skipped.append("feedback already on file")
                continue
            have_feedback.add(key)
        fresh.append(ev)
    written: List[dict] = []
    if fresh:
        from event_gate import append_event
        written = append_event(_events_path(workspace_root), fresh, holder=HOLDER)
    return {
        "ok": True,
        "summary_seq": next((e.get("seq") for e in written if e.get("type") == SUMMARY_TYPE), None),
        "feedback_seqs": [e.get("seq") for e in written if e.get("type") == FEEDBACK_TYPE],
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# The readers
# ---------------------------------------------------------------------------

def _entities(workspace_root) -> dict:
    try:
        from entities_io import read_entities_json
        data = read_entities_json(Path(workspace_root) / "_hq" / "data" / "entities.json")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _ids_named(entities: dict, collection: str, want: Optional[str],
               names_out: Optional[set] = None) -> Optional[set]:
    """Ids whose id, name or alias is `want` (case folded); None for no filter.

    `names_out`, when given, collects every name and alias of the matched
    records, so a row that names a person in words (a teammate's note, who
    gave feedback) matches as well as one that carries their id."""
    if not want:
        return None
    w = str(want).strip().lower()
    out = {w}
    if names_out is not None:
        names_out.add(w)
    try:
        from entities_io import entities_collection
        rows = entities_collection(entities, collection) if entities else []
    except Exception:
        rows = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        names = [str(r.get("canonical_name") or ""), str(r.get("display_name") or "")]
        names += [a for a in (r.get("aliases") or []) if isinstance(a, str)]
        folded = {n.strip().lower() for n in names if n}
        if w in folded or w == str(r.get("id") or "").lower():
            out.add(str(r.get("id") or "").lower())
            if names_out is not None:
                names_out.update(folded)
    return out


def _date_of(ev: dict) -> str:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    from event_refs import event_ts
    return str(d.get("meeting_ts") or event_ts(ev) or "")[:10]


def _in_window(date: str, since: Optional[str], until: Optional[str]) -> bool:
    if since and date and date < str(since)[:10]:
        return False
    if until and date and date > str(until)[:10]:
        return False
    return True


def _matches(ev: dict, thread_ids: Optional[set], person_ids: Optional[set],
             person_names: set) -> bool:
    from event_refs import threads_of, persons_of
    if thread_ids is not None and not ({t.lower() for t in threads_of(ev)} & thread_ids):
        return False
    if person_ids is not None:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        ids = {p.lower() for p in persons_of(ev)}
        if d.get("from_person_id"):
            ids.add(str(d["from_person_id"]).lower())
        named = bool(person_names & {str(d.get("from_name") or "").strip().lower(),
                                     str(d.get("notes_author") or "").strip().lower()} - {""})
        if not (ids & person_ids) and not named:
            return False
    return True


def _rank(row: Tuple[int, str, dict]) -> Tuple[int, str, int]:
    """Keyword score, then date, then the entry's number (an entry with no
    readable number sorts last, never as a number it is not)."""
    seq = row[2].get("seq")
    return (row[0], row[1], seq if isinstance(seq, int) else -1)


def _score(text: str, terms: List[str]) -> int:
    toks = _tokens(text)
    return sum(1 for t in terms if t in toks)


def meeting_summaries(workspace_root, query: Optional[str] = None,
                      thread: Optional[str] = None, person: Optional[str] = None,
                      since: Optional[str] = None, until: Optional[str] = None,
                      limit: int = 10) -> Dict[str, Any]:
    """Stored summaries of what was said, newest first, or ranked by keywords.

    Read only. `thread` and `person` take an id, a name or an alias; `since`
    and `until` are dates. Each row carries its `source_ref`: the summary is
    for display, and the exact words come from the transcript (C3). The
    feedback given in that meeting rides on its row.
    """
    from events_io import iter_events_org_scoped
    ents = _entities(workspace_root)
    tids = _ids_named(ents, "threads", thread)
    pnames: set = set()
    pids = _ids_named(ents, "people", person, pnames)
    terms = _tokens(query or "")
    if (query or "").strip() and not terms:
        return {"ok": True, "total": 0, "rows": [], "reason": "no words to search for",
                "recall": "for the exact words, open the transcript by its source_ref"}
    rows: List[Tuple[int, str, dict]] = []
    feedback_by_ref: Dict[str, List[dict]] = {}
    for ev in iter_events_org_scoped(workspace_root):
        t = ev.get("type")
        if t not in (SUMMARY_TYPE, FEEDBACK_TYPE):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if t == FEEDBACK_TYPE:
            feedback_by_ref.setdefault(_ref_key(d.get("source_ref")), []).append(
                {"seq": event_seq(ev), "said": d.get("said"), "about": d.get("about"),
                 "kind": d.get("kind"), "from": d.get("from_name") or d.get("from_person_id")})
            continue
        date = _date_of(ev)
        if not _in_window(date, since, until):
            continue
        if not _matches(ev, tids, pids, pnames):
            continue
        score = 0
        if terms:
            hay = " ".join([d.get("title") or "", d.get("summary") or ""]
                           + [f"{x.get('topic')} {x.get('said')}" for x in d.get("topics") or []])
            score = _score(hay, terms)
            if not score:
                continue
        rows.append((score, date, {
            "seq": event_seq(ev), "date": date, "title": d.get("title"),
            "summary": d.get("summary"), "topics": d.get("topics") or [],
            "source_ref": d.get("source_ref"), "notes_from": d.get("notes_from"),
            "notes_author": d.get("notes_author"),
            "thread": ev.get("primary_thread_id"), "display_only": True,
            "clipped": bool(d.get("clipped")),
        }))
    rows.sort(key=_rank, reverse=True)
    cap = max(1, min(int(limit or 10), 50))
    out = []
    for _, _, row in rows[:cap]:
        row["feedback"] = feedback_by_ref.get(_ref_key(row["source_ref"]), [])
        out.append(row)
    return {"ok": True, "total": len(rows), "rows": out,
            "recall": "for the exact words, open the transcript by its source_ref"}


def feedback_entries(workspace_root, query: Optional[str] = None,
                     thread: Optional[str] = None, person: Optional[str] = None,
                     kind: Optional[str] = None, since: Optional[str] = None,
                     until: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    """Feedback rows, newest first, or ranked by keywords. Read only."""
    from events_io import iter_events_org_scoped
    ents = _entities(workspace_root)
    tids = _ids_named(ents, "threads", thread)
    pnames: set = set()
    pids = _ids_named(ents, "people", person, pnames)
    terms = _tokens(query or "")
    if (query or "").strip() and not terms:
        return {"ok": True, "total": 0, "rows": [], "reason": "no words to search for"}
    want_kind = (kind or "").strip().lower() or None
    rows: List[Tuple[int, str, dict]] = []
    for ev in iter_events_org_scoped(workspace_root):
        if ev.get("type") != FEEDBACK_TYPE:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if want_kind and d.get("kind") != want_kind:
            continue
        date = _date_of(ev)
        if not _in_window(date, since, until) or not _matches(ev, tids, pids, pnames):
            continue
        score = 0
        if terms:
            score = _score(" ".join([d.get("said") or "", d.get("about") or "",
                                     d.get("title") or ""]), terms)
            if not score:
                continue
        rows.append((score, date, {
            "seq": event_seq(ev), "date": date, "said": d.get("said"),
            "about": d.get("about"), "kind": d.get("kind"),
            "from": d.get("from_name") or d.get("from_person_id"),
            "from_person_id": d.get("from_person_id"),
            "title": d.get("title"), "source_ref": d.get("source_ref"),
            "thread": ev.get("primary_thread_id"), "clipped": bool(d.get("clipped")),
        }))
    rows.sort(key=_rank, reverse=True)
    cap = max(1, min(int(limit or 50), 200))
    return {"ok": True, "total": len(rows), "rows": [r for _, _, r in rows[:cap]]}
