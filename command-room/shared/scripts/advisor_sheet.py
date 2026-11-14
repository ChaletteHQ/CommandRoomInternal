#!/usr/bin/env python3
"""The advisor's sheet (cr1#1017, train 8 part 1).

WHAT IT IS
----------
How a person wants their Command Room to talk and what it should know first,
in their own words. Ruling A6: delivery customizes through both this sheet and
the per-seat word list (`words.py`). The advisor fills it at delivery with
about eight plain questions; the owner can re-run it any time. The answers are
kept verbatim (spaces tidied, nothing reworded), because later surfaces quote
them back.

One sheet per seat (ruling B3: one folder per seat), at
`_hq/config/advisor_sheet.json`. The two naming answers also set the word list,
so "what do you call one piece of work" changes the word on screen.

What the sheet is NOT: it creates no priority (ruling B2: priorities are
suggested, then confirmed), adds no person, and holds no money. It is the
advisor's notes in the owner's words; later trains read them.

Read-only except `write_sheet`. stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import words as _words  # noqa: E402

SHEET_REL = "_hq/config/advisor_sheet.json"
SCHEMA_VERSION = 1
MAX_ANSWER_LEN = 1000

#: The questions, in the order they are asked. `word` names the word-list key
#: an answer sets. The wording is a draft for the owner to edit (cr1#1017).
QUESTIONS: List[Dict[str, Any]] = [
    {"id": "name", "ask": "What should I call you?",
     "hint": "The name you go by, as you would sign a short note."},
    {"id": "business", "ask": "In a sentence, what does your business do, and for whom?",
     "hint": "The way you would say it to someone you just met."},
    {"id": "thread_word", "ask": "What do you call one piece of work you are running?",
     "hint": "A client, a deal, a job, a matter. We say thread if you have no word.",
     "word": "thread"},
    {"id": "work_stream_word", "ask": "What do you call a group of those pieces of work?",
     "hint": "A division, a practice area, a line of business. We say work stream if you have no word.",
     "word": "work_stream"},
    {"id": "people", "ask": "Who are the few people whose messages should never wait?",
     "hint": "Names and how you know them. Nobody is added to your book from this answer."},
    {"id": "priorities", "ask": "What are the two or three things that matter most this quarter?",
     "hint": "In your words. They are suggestions until you confirm them."},
    {"id": "morning", "ask": "When does your working day start, and when should the morning message reach you?",
     "hint": "A time, or a habit, like before the school run."},
    {"id": "avoid", "ask": "Any words or habits you never want to see from me?",
     "hint": "A phrase you dislike, a tone, anything that would make you stop reading."},
]
_BY_ID = {q["id"]: q for q in QUESTIONS}


def sheet_path(workspace_root) -> Path:
    return Path(workspace_root) / SHEET_REL


def load_sheet(workspace_root) -> Dict[str, Any]:
    """READ. `{found, answers: {id: {text, at, by}}, rel}`. A missing or
    corrupt file reads as not found, never raises."""
    out: Dict[str, Any] = {"found": False, "answers": {}, "rel": SHEET_REL}
    try:
        data = json.loads(sheet_path(workspace_root).read_bytes().decode("utf-8"))
    except Exception:  # noqa: BLE001 - absent or corrupt reads as none
        return out
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        return out
    kept = {k: v for k, v in answers.items()
            if k in _BY_ID and isinstance(v, dict) and isinstance(v.get("text"), str)}
    out.update(found=bool(kept), answers=kept)
    return out


def questions(workspace_root=None) -> Dict[str, Any]:
    """READ. The questions in order, each with the answer on file (if any)
    and, for a naming question, the word the seat uses now. A re-run shows
    what was said before, so the advisor changes one answer, not all."""
    sheet = load_sheet(workspace_root) if workspace_root else {"answers": {}}
    doc = _words.load_words(workspace_root) if workspace_root else None
    rows = []
    for n, q in enumerate(QUESTIONS, 1):
        row = {"n": n, "id": q["id"], "ask": q["ask"], "hint": q["hint"],
               "answer": (sheet["answers"].get(q["id"]) or {}).get("text")}
        if q.get("word"):
            row["word_now"] = _words.label(doc, q["word"]) + " / " + _words.label(doc, q["word"], many=True)
        rows.append(row)
    return {"questions": rows, "count": len(rows),
            "answered": sum(1 for r in rows if r["answer"])}


def _tidy(text) -> Optional[str]:
    if not isinstance(text, str):
        return None
    lines = [" ".join(line.split()) for line in text.strip().splitlines()]
    t = "\n".join(line for line in lines if line)
    return t or None


def write_sheet(workspace_root, answers: Dict[str, Any], *, by: str = "advisor",
                now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER. Saves the answers given, keeps every earlier answer not given
    again, and sets the word list from the two naming answers. Everything is
    checked before anything is written: an unknown question, an empty or
    over-long answer, or a word the list refuses stops the whole call with a
    line, and neither file changes. A naming answer that says there is no
    word ("none", "n/a", `words.NO_WORD`) keeps the ruled word; a sentence
    is refused, so the word on screen is always a word."""
    from atomic_write import atomic_write_text

    if not isinstance(answers, dict) or not answers:
        return {"ok": False, "reason": "nothing_to_save",
                "line": "No answer was given, so the sheet is unchanged."}
    unknown = sorted(k for k in answers if k not in _BY_ID)
    if unknown:
        return {"ok": False, "reason": "unknown_question", "ids": unknown,
                "line": "The sheet has no question " + ", ".join(repr(k) for k in unknown)
                        + ". Nothing was saved."}
    tidy: Dict[str, str] = {}
    for qid, text in answers.items():
        t = _tidy(text)
        if t is None:
            return {"ok": False, "reason": "empty_answer", "ids": [qid],
                    "line": f"The answer to \"{_BY_ID[qid]['ask']}\" had no words in it. Nothing was saved."}
        if len(t) > MAX_ANSWER_LEN:
            return {"ok": False, "reason": "too_long", "ids": [qid],
                    "line": f"The answer to \"{_BY_ID[qid]['ask']}\" is over {MAX_ANSWER_LEN} "
                            "characters; a sentence or two is enough. Nothing was saved."}
        tidy[qid] = t
    word_changes: Dict[str, Any] = {}
    for qid, t in tidy.items():
        key = _BY_ID[qid].get("word")
        if key:
            word_changes[key] = None if _words.means_no_word(t) else t
    if word_changes:
        # Check the words first, so a refused word leaves both files exactly
        # as they were.
        trial = _words.plan_words(workspace_root, word_changes)
        if not trial.get("ok"):
            return {"ok": False, "reason": trial.get("reason"), "ids": [
                qid for qid in tidy if _BY_ID[qid].get("word") in (trial.get("keys") or [])],
                "line": trial.get("line")}
    at = now_iso or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    kept = load_sheet(workspace_root)["answers"]
    for qid, t in tidy.items():
        kept[qid] = {"text": t, "at": at, "by": by}
    data = {"schema_version": SCHEMA_VERSION, "audience": "seat",
            "answers": {q["id"]: kept[q["id"]] for q in QUESTIONS if q["id"] in kept},
            "updated_at": at}
    path = sheet_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    words_out = None
    if word_changes:
        words_out = _words.set_words(workspace_root, word_changes, set_by=by, now_iso=at)
    missing = [q["id"] for q in QUESTIONS if q["id"] not in kept]
    line = f"Saved {len(tidy)} answer{'s' if len(tidy) != 1 else ''}"
    line += f"; {len(missing)} still open." if missing else "; the sheet is complete."
    if words_out and words_out.get("ok"):
        line += " " + words_out["line"]
    return {"ok": True, "rel": SHEET_REL, "saved": sorted(tidy), "missing": missing,
            "words": (words_out or {}).get("words") or _words.load_words(workspace_root)["words"],
            "line": line}
