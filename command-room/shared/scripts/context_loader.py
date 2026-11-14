#!/usr/bin/env python3
"""The one loader: every skill gets its context here (cr1#1016, train 7 of
the memory and Hub programme; absorbs cr1#788 and cr1#785 items 2 to 5).

WHY THIS EXISTS
---------------
Each skill gathered its own context its own way: a few read the thread
reader, a few read the owner's standing rules, most read neither, so a rule
said once reached a handful of skills and the rest never saw it. One loader,
called the same way by every skill, is what makes a rule land everywhere.

HOW A SKILL REACHES IT
----------------------
Through the Access preamble every skill file carries byte for byte (rule 2,
`shared/WORKSPACE_ACCESS.md`): read the brief, then one `plan run_helper`
call to `context_loader:load_context` with the skill's own folder name. No
skill reads the rules folder, the rule rows on the log or the moved-out
history any other way; `tests/run_guard_one_loader_test.py` fails when one
does.

WHAT ONE CALL ANSWERS
---------------------
  * `rules`: every live rule that applies to this skill, in full, in the one
    shape (`rules_store.RULE_FIELDS`), folded fresh from the sources, so a
    rule stated earlier today is here now;
  * `index`: one line per live rule, every scope, so the answer can see what
    else is on file;
  * `business`: the business context file when the folder has one, bounded;
  * `thread`: the thread reader's payload, when the skill names a thread;
  * `craft`: when the call names a kind of task, the owner's stored method
    for it (`_hq/craft/<scope>.md`, then the firm's), cited by file;
  * `history`: when the call names a topic, the blocks of the brief's dated
    history (moved out by `claude_md_diet`) that carry its words;
  * `records_used`: the ids and files this context came from, which is what
    an answer names when it says what it rests on;
  * `how`: the three sentences every answer follows (name the rules it used
    in plain words, name the records, and say "I do not have that" when
    nothing on file answers).

It writes nothing. A source that cannot be read degrades into `degraded`
rather than failing the call: a skill always gets an answer.

Command line, from the plugin root:
    python shared/scripts/context_loader.py <workspace> <skill> [--thread ID]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import rules_store  # noqa: E402

LOADER_VERSION = 1
#: The business context file the template names (`_hq/BUSINESS_CONTEXT.md`).
BUSINESS_REL = "_hq/BUSINESS_CONTEXT.md"
BUSINESS_CHARS = 4000
#: A business file whose head says it is retired is not loaded: an owner who
#: bannered it has said not to draft from it.
BANNER_CHARS = 600
RETIRED_BANNER = re.compile(r"\b(retired|superseded|do not draft from)\b", re.I)
#: The thread reader's profile for a loader call (a frozen profile name).
THREAD_PROFILE = "catch-all"
#: The owner's stored method per kind of task (train 5): owner's file, then
#: the firm's. Loaded on demand by scope, cited by file in the premise line.
CRAFT_DIR_REL = "_hq/craft"
CRAFT_SCOPE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
CRAFT_CHARS = 12000
#: What an answer says when nothing on file answers the ask (cr1#785 item 5).
NOTHING_LINE = "I do not have that."

#: At most this many rule lines in the brief's index (the always-loaded file
#: has a budget); the rest are named by a count and one ask away.
INDEX_CAP = 25
#: The first line of the index the brief carries (`index_for_brief`).
INDEX_HEADER = ("Rules on file, one line each (the full rule loads with each "
                "skill it applies to):")

HOW: tuple = (
    "Follow every rule in `rules`. When a rule shaped the answer, say so in "
    "a few plain words of the rule itself; never print a rule's id unless "
    "asked which rules were used.",
    "Name the records the answer rests on (a person, a thread, a decision, "
    "a file, with its date) and only records that are on file.",
    "When the ask is about the owner's own records (a person, a thread, a "
    "date, a figure, a promise) and nothing on file answers it, say \""
    + NOTHING_LINE + "\" and do not guess.",
    "When a term or a figure the ask depends on is not defined on file, answer "
    "with the assumption stated in one line and offer to keep that meaning; ask "
    "first instead when it goes into something sent or signed.",
    "When the owner states a rule to hold from now on that is not about how "
    "you talk to them (that is the persona's), say it back once as one "
    "sentence with where it applies, and only on their yes store it with "
    "`plan run_writer` naming `rules_store:state_rule`.",
    "When the owner corrects an answer against a rule in `rules`, record it "
    "with `plan run_writer` naming `rules_store:record_correction` and that "
    "rule's id.",
    "When an ask needs the brief's older history (what was ruled, corrected or "
    "retired before, and when), call this loader again with `history` set to "
    "the topic's words, and name the history file the answer rests on.",
    "When the task has a stored method (a craft file), call this loader with "
    "`craft` set to its scope; an answer that applied one of its rules names "
    "that craft file in its premise line.",
)


def _ws(workspace_root) -> Path:
    return Path(str(workspace_root))


def _business(ws: Path, degraded: List[str]) -> Optional[Dict[str, Any]]:
    path = ws / BUSINESS_REL
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        degraded.append(f"business: {type(exc).__name__}")
        return None
    if RETIRED_BANNER.search(text[:BANNER_CHARS]):
        degraded.append("business: the file is marked retired, so it was not loaded")
        return None
    clipped = len(text) > BUSINESS_CHARS
    return {"rel": BUSINESS_REL, "text": text[:BUSINESS_CHARS],
            "clipped": clipped}


def _thread(ws: Path, thread: str, degraded: List[str]) -> Optional[dict]:
    try:
        from load_thread_knowledge import load_thread_knowledge
        return load_thread_knowledge(ws, thread, THREAD_PROFILE)
    except Exception as exc:  # noqa: BLE001: one source never fails the call
        degraded.append(f"thread: {type(exc).__name__}: {exc}"[:200])
        return None


def _craft(ws: Path, scope: str, degraded: List[str]) -> List[Dict[str, Any]]:
    """The owner's stored method for one kind of task (train 5, cr1#381): the
    owner's own file first, `_hq/craft/<scope>.md`, then the firm's,
    `_hq/craft/firm/<scope>.md`. Read on demand, bounded, never written."""
    name = str(scope or "").strip().lower()
    if not CRAFT_SCOPE_RE.fullmatch(name):
        degraded.append("craft: a scope is a short name of letters, digits and dashes")
        return []
    out: List[Dict[str, Any]] = []
    for rel in (f"{CRAFT_DIR_REL}/{name}.md", f"{CRAFT_DIR_REL}/firm/{name}.md"):
        path = ws / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            degraded.append(f"craft: {type(exc).__name__}")
            continue
        out.append({"rel": rel, "text": text[:CRAFT_CHARS],
                    "clipped": len(text) > CRAFT_CHARS})
    return out


def load_context(workspace_root, skill: str, *, thread: Optional[str] = None,
                 history: Optional[str] = None, craft: Optional[str] = None) -> dict:
    """The context for one skill's turn. Read-only; never raises on a
    source, only on a missing skill name. `history` reaches the dated
    history the diet moved out of the brief (`claude_md_diet`)."""
    name = str(skill or "").strip().lower()
    if not name:
        raise ValueError("load_context needs the skill's folder name.")
    ws = _ws(workspace_root)
    degraded: List[str] = []
    try:
        everything = rules_store.rules(ws, problems=degraded)
    except Exception as exc:  # noqa: BLE001
        degraded.append(f"rules: {type(exc).__name__}: {exc}"[:200])
        everything = []
    applying = [r for r in everything if rules_store.applies(r, name)]
    used: List[str] = [r["id"] for r in applying]
    business = _business(ws, degraded)
    if business:
        used.append(business["rel"])
    found_history: List[dict] = []
    n_history_files = 0
    try:
        import claude_md_diet
        n_history_files = len(claude_md_diet.history_files(ws))
        if history:
            found_history = claude_md_diet.search_history(ws, str(history))
    except Exception as exc:  # noqa: BLE001
        degraded.append(f"history: {type(exc).__name__}: {exc}"[:200])
    for h in found_history:
        if h["file"] not in used:
            used.append(h["file"])
    crafts: List[Dict[str, Any]] = _craft(ws, craft, degraded) if craft else []
    for c in crafts:
        used.append(c["rel"])
    payload = None
    if thread:
        payload = _thread(ws, str(thread), degraded)
        if payload is not None:
            used.append(str(thread))
    return {
        "loader_version": LOADER_VERSION,
        "skill": name,
        "rules": applying,
        "index": rules_store.index_lines(everything),
        "n_rules_on_file": len(everything),
        "business": business,
        "thread": payload,
        "history": found_history,
        "craft": crafts,
        "n_history_files": n_history_files,
        "records_used": used,
        "nothing_line": NOTHING_LINE,
        "how": list(HOW),
        "degraded": degraded,
    }


class SourceUnreadable(RuntimeError):
    """A rules source could not be read; the caller keeps what it had."""


def index_for_brief(workspace_root) -> List[str]:
    """The one-line index of every live rule, headed, for the brief's
    generated rules block; empty when no rule is on file. Capped at
    `INDEX_CAP` lines, with one line saying how many more there are. Raises
    `SourceUnreadable` when a source could not be read, so the brief keeps
    its last index rather than showing none."""
    problems: List[str] = []
    lines = rules_store.index_lines(rules_store.rules(_ws(workspace_root),
                                                      problems=problems))
    if problems:
        raise SourceUnreadable("; ".join(problems))
    if not lines:
        return []
    if len(lines) > INDEX_CAP:
        more = len(lines) - INDEX_CAP
        lines = lines[:INDEX_CAP] + [f"- and {more} more: say \"show my rules\"."]
    return [INDEX_HEADER] + lines


_ID_KINDS = (("rul_", "rule"), ("lrn_", "rule"), ("decision_seq_", "decision"),
             ("dec_", "decision"), ("seq:", "entry"),
             ("person_", "person"), ("project_", "thread"), ("org_", "org"))


def check_named(workspace_root, names: List[str]) -> dict:
    """Whether each record an answer names is on file (cr1#785 item 4).
    Answers `{found: [...], missing: [...], unknown: [...]}`: a name of a
    shape this cannot check, or one whose source could not be read, is
    `unknown`, never counted as found or missing. Read-only."""
    from event_seq import event_seq

    ws = _ws(workspace_root)
    problems: List[str] = []
    rule_ids = {r["id"] for r in rules_store.rules(ws, include_retired=True,
                                                   problems=problems)}
    log_ok = not problems
    events: list = []
    if log_ok:
        try:
            # Owner-tier read through the allowlisted shard reader
            # (personal firewall D4c), as rules_store does.
            from events_io import load_events_owner_scoped
            loaded, _skipped = load_events_owner_scoped(ws / "_hq" / "data")
            events = [ev for ev in loaded if isinstance(ev, dict)]
        except Exception:  # noqa: BLE001
            log_ok = False
    seqs = {event_seq(ev) for ev in events}
    # The key the decision readers use: data.id, then the top-level id, then
    # `decision_seq_<n>` only for a decision with neither (event_gate).
    dec_keys = set()
    for ev in events:
        if ev.get("type") == "decision":
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            dec_keys.add(str(data.get("id") or ev.get("id")
                             or f"decision_seq_{event_seq(ev)}"))
    entity_ids: set = set()
    entities_ok = True
    try:
        from entities_io import entities_collection, read_entities_json
        ents = read_entities_json(ws / "_hq" / "data" / "entities.json")
        for coll in ("people", "orgs", "threads"):
            entity_ids |= {str(r.get("id")) for r in entities_collection(ents, coll)
                           if isinstance(r, dict)}
    except Exception:  # noqa: BLE001
        entities_ok = False
    out: Dict[str, list] = {"found": [], "missing": [], "unknown": []}
    for raw in names or []:
        name = str(raw or "").strip()
        kind = next((k for prefix, k in _ID_KINDS if name.startswith(prefix)), None)
        readable = entities_ok if kind in ("person", "thread", "org") else log_ok
        if kind is None or not readable:
            out["unknown"].append(name)
            continue
        if kind == "rule":
            ok = name in rule_ids
        elif kind == "entry":
            ok = name[4:].isdigit() and int(name[4:]) in seqs
        else:
            ok = name in (dec_keys if kind == "decision" else entity_ids)
        out["found" if ok else "missing"].append(name)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("workspace")
    ap.add_argument("skill")
    ap.add_argument("--thread")
    args = ap.parse_args(argv)
    print(json.dumps(load_context(args.workspace, args.skill, thread=args.thread),
                     indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
