#!/usr/bin/env python3
"""The per-seat word list (cr1#1017, train 8 part 1).

WHAT IT IS
----------
The words a person sees for the pieces of their work. The book calls a piece
of work a "project" in its ids, fields and code; nobody should see that word
on screen. By the 2026-11-09 vocabulary a piece of work is a "thread" and a
parent thread is a "work stream" (ruling A3). A client may use their own words
instead ("matter" and "practice area", say), and ruling A6 says delivery
customizes through this list beside the advisor's sheet (`advisor_sheet.py`).

One list per seat (ruling B3: one folder per seat), at `_hq/config/words.json`.
A seat with no file reads the ruled defaults, so nothing changes for it.

HOW A SURFACE USES IT, WITHOUT REWRITING A SKILL
------------------------------------------------
* `load_words(ws)` once per render, then
* `label(doc, "thread", many=True, title=True)` wherever a label names a piece
  of work (a tab, a heading, a search box), and
* `relabel(text, doc)` over display text a skill already wrote: every whole
  word "project" or "projects" becomes the seat's thread word, and every
  "work stream" or "work streams" the seat's work-stream word, case kept.

`relabel` never touches an id, a field name, a path or an address
(`project_001`, `project_id`, `Projects/Acme`, `.../project`), and it never
rewrites free-text "thread": on the Inbox that word means a mail thread, so a
label that means a piece of work asks `label` for it instead.

Read-only except `set_words`. stdlib only.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

WORDS_REL = "_hq/config/words.json"
SCHEMA_VERSION = 1
MAX_WORD_LEN = 40

#: The keys a seat may set, each with the ruled default and what it means.
#: A key not here is refused by name, never guessed.
DEFAULTS: Dict[str, Dict[str, str]] = {
    "thread": {"one": "thread", "many": "threads",
               "means": "one piece of work (the book calls it a project)",
               "plain": "one piece of work"},
    "work_stream": {"one": "work stream", "many": "work streams",
                    "means": "a parent thread that groups others (ruling A3)",
                    "plain": "a group of pieces of work"},
}

#: What `relabel` replaces in display text, per key: (singular, plural).
_REPLACES = {
    "thread": ("project", "projects"),
    "work_stream": ("work stream", "work streams"),
}

#: A word is replaced only where it stands alone: not inside an id, a path,
#: an address, a tag or a "re:" subject (`project_001`, `a/project`,
#: `project@example.com`, `#projects`, `re:project`).
_EDGE_L = r"(?<![\w/\\.\-@#:+=&?])"
_EDGE_R = r"(?![\w/\\\-@#+=&]|[.:]\w)"
MAX_WORD_WORDS = 3
#: Answers that mean "I have no word, keep yours".
NO_WORD = frozenset({"none", "no", "nope", "skip", "pass", "n/a", "na", "-",
                     "nothing", "no word", "not really", "same", "same as yours"})


def means_no_word(text) -> bool:
    """True for an answer that says the person has no word of their own."""
    if not isinstance(text, str):
        return False
    t = re.sub(r"[.!,;]+$", "", " ".join(text.split()).lower())
    return t in NO_WORD


def _squash(word: str) -> str:
    """How two spellings are compared: case, spaces and hyphens ignored,
    exactly as `relabel` matches them (work stream, work-stream, workstream)."""
    return re.sub(r"[\s\-]+", "", word.lower())


def words_path(workspace_root) -> Path:
    return Path(workspace_root) / WORDS_REL


def _plural(one: str) -> str:
    if re.search(r"(s|x|z|ch|sh)$", one, re.IGNORECASE):
        return one + "es"
    if re.search(r"[^aeiou]y$", one, re.IGNORECASE):
        return one[:-1] + "ies"
    return one + "s"


def _defaults() -> Dict[str, Dict[str, str]]:
    return {k: {"one": v["one"], "many": v["many"]} for k, v in DEFAULTS.items()}


def load_words(workspace_root) -> Dict[str, Any]:
    """READ. `{words: {key: {one, many}}, source, set: [keys the seat set],
    rel}`. A missing or corrupt file, or a bad entry in it, reads as the
    ruled default for that key: losing the list costs the client's words for
    one render, never a failed one."""
    words = _defaults()
    seat_set = []
    try:
        data = json.loads(words_path(workspace_root).read_bytes().decode("utf-8"))
    except Exception:  # noqa: BLE001 - absent or corrupt reads as defaults
        data = None
    stored = data.get("words") if isinstance(data, dict) else None
    if isinstance(stored, dict):
        for key, val in stored.items():
            if key not in DEFAULTS or not isinstance(val, dict):
                continue
            one, many = val.get("one"), val.get("many")
            if _clean(one) is None or _clean(many) is None:
                continue
            words[key] = {"one": one.strip(), "many": many.strip()}
            seat_set.append(key)
    return {"words": words, "source": "seat" if seat_set else "default",
            "set": sorted(seat_set), "rel": WORDS_REL}


def _words_of(doc_or_words) -> Dict[str, Dict[str, str]]:
    if isinstance(doc_or_words, dict) and isinstance(doc_or_words.get("words"), dict):
        return doc_or_words["words"]
    if isinstance(doc_or_words, dict):
        return doc_or_words
    return _defaults()


def _case_like(src: str, word: str) -> str:
    """The seat's word in the case of the text it replaces. A word stored
    capitalized ("Matter") still reads lower case mid-sentence; an acronym
    ("RFP") keeps its capitals."""
    if word[:1].isupper() and not word[1:2].isupper():
        word = word[:1].lower() + word[1:]
    if src.isupper() and len(src) > 1:
        return word.upper()
    if src[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


def label(doc_or_words, key: str, *, many: bool = False, title: bool = False) -> str:
    """The seat's word for `key`. `title=True` capitalizes the first letter
    only ("Work streams"), the Hub's own label case. An unknown key raises
    KeyError: a label naming a word that does not exist is a code defect."""
    if key not in DEFAULTS:
        raise KeyError(f"no word named {key!r}; the list has {sorted(DEFAULTS)}")
    entry = _words_of(doc_or_words).get(key) or DEFAULTS[key]
    word = entry["many" if many else "one"]
    return word[:1].upper() + word[1:] if title else word


def relabel(text, doc_or_words=None):
    """Display text in the seat's words. Non-strings come back unchanged.
    Plurals are matched before singulars so "projects" never reads as
    "threads" with a stray "s"."""
    if not isinstance(text, str) or not text:
        return text
    words = _words_of(doc_or_words)
    # One pass over one pattern, so a word just written is never rewritten
    # again by a later rule (a seat whose thread word is "workstream").
    alts, dsts = [], []
    for key, (one_src, many_src) in _REPLACES.items():
        entry = words.get(key) or DEFAULTS[key]
        for src, dst in ((many_src, entry["many"]), (one_src, entry["one"])):
            alts.append("(" + re.escape(src).replace(r"\ ", r"[ \-]?") + ")")
            dsts.append(dst)
    pat = re.compile(_EDGE_L + "(?:" + "|".join(alts) + ")" + _EDGE_R, re.IGNORECASE)

    def _swap(m):
        i = next(n for n in range(len(dsts)) if m.group(n + 1) is not None)
        return _case_like(m.group(0), dsts[i])

    return pat.sub(_swap, text)


def _clean(word) -> Optional[str]:
    """A word the list will hold, or None. Plain words only: one line, no
    markup, no sentence, not a number, at most MAX_WORD_WORDS words and
    MAX_WORD_LEN characters."""
    if not isinstance(word, str):
        return None
    w = " ".join(word.split())
    if not w or len(w) > MAX_WORD_LEN or not re.search(r"[A-Za-z]", w):
        return None
    if re.search(r"[<>{}\[\]`|\\/.!?,;:@#]", w) or len(w.split()) > MAX_WORD_WORDS:
        return None
    return w


def _parse(value) -> Optional[Dict[str, str]]:
    """A key's new value: `{"one", "many"}`, a "one / many" string, or one
    word (its plural made by the usual rule). None for a bad value."""
    if isinstance(value, dict):
        one = _clean(value.get("one"))
        many = _clean(value.get("many")) if value.get("many") else (one and _plural(one))
    elif isinstance(value, str) and "/" in value:
        parts = [p for p in value.split("/")]
        if len(parts) != 2:
            return None
        one, many = _clean(parts[0]), _clean(parts[1])
        if one and many and not _squash(many).startswith(_squash(one)[:3]):
            return None  # "ja / b" is not a word and its plural
    else:
        one = _clean(re.sub(r"^(a|an|the)\s+", "", value, flags=re.IGNORECASE)
                     if isinstance(value, str) else value)
        many = one and _plural(one)
    if not one or not many:
        return None
    return {"one": one, "many": many}


def plan_words(workspace_root, changes: Dict[str, Any]) -> Dict[str, Any]:
    """READ. What `set_words` would store, checked, writing nothing:
    `{ok: True, stored, final}` or the refusal `set_words` would answer.
    The advisor's sheet checks its naming answers here before it saves."""
    if not isinstance(changes, dict) or not changes:
        return {"ok": False, "reason": "nothing_to_set",
                "line": "No word was given, so nothing changed."}
    unknown = sorted(k for k in changes if k not in DEFAULTS)
    if unknown:
        return {"ok": False, "reason": "unknown_key", "keys": unknown,
                "line": "That is not a word the list keeps; it keeps the word for one "
                        "piece of work and the word for a group of them. Nothing changed."}
    current = load_words(workspace_root)
    stored = {k: dict(current["words"][k]) for k in current["set"]}
    for key, value in changes.items():
        if value is None or means_no_word(value):
            stored.pop(key, None)
            continue
        parsed = _parse(value)
        if parsed is None:
            shown = " ".join(str(value).split())[:60]
            return {"ok": False, "reason": "bad_word", "keys": [key],
                    "line": f'"{shown}" cannot be the word for {DEFAULTS[key]["plain"]}: '
                            f"give the word itself, up to {MAX_WORD_WORDS} words. Nothing changed."}
        stored[key] = parsed
    final = _defaults()
    final.update(stored)
    # The ruled phrases relabel reads count as taken by their own key, so a
    # thread word of "workstream" cannot pass for the work-stream phrase.
    seen: Dict[str, str] = {_squash(src): key for key, pair in _REPLACES.items()
                            for src in pair}
    for key, entry in final.items():
        for form in (entry["one"], entry["many"]):
            sq = _squash(form)
            if sq in seen and seen[sq] != key:
                return {"ok": False, "reason": "same_word", "keys": sorted({key, seen[sq]}),
                        "line": f'"{form}" would name both {DEFAULTS[seen[sq]]["plain"]} and '
                                f'{DEFAULTS[key]["plain"]}, so nobody could tell them apart. '
                                "Nothing changed."}
            seen[sq] = key
    return {"ok": True, "stored": stored, "final": final}


def set_words(workspace_root, changes: Dict[str, Any], *,
              set_by: str = "owner", now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER. Sets the seat's words. `changes` maps a key to its new word
    (see `_parse`) or to None, which puts the key back to the ruled default.
    Every change is checked first (`plan_words`): an unknown key, a bad word,
    or two keys left with the same word refuse the whole call, nothing
    written, with a line naming why. Atomic, so a crash never leaves half a
    list."""
    from atomic_write import atomic_write_text
    import datetime as _dt

    plan = plan_words(workspace_root, changes)
    if not plan.get("ok"):
        return plan
    stored, final = plan["stored"], plan["final"]
    at = now_iso or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    data = {"schema_version": SCHEMA_VERSION, "words": stored,
            "updated_at": at, "updated_by": set_by}
    path = words_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    changed = sorted(changes)
    shown = "; ".join(f"{DEFAULTS[k]['plain']}: {final[k]['one']} / {final[k]['many']}"
                      for k in changed)
    return {"ok": True, "rel": WORDS_REL, "changed": changed, "words": final,
            "line": "Saved. On screen now, " + shown + "."}
