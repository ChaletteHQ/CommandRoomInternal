#!/usr/bin/env python3
"""The always-loaded diet: the brief cut to about 1,500 words, with its dated
history moved out to a place the loader can still reach (cr1#1016, train 7;
owner ruling A5 of 2026-11-10; cr1#788 item 1).

WHY THIS EXISTS
---------------
The brief (`CLAUDE.md` at the workspace root) loads before the first message
of every chat. On a long-running seat it grew to 14,000 words and more, most
of it dated history: what was corrected on which day, what was retired and
why. Every word of it is paid for on every turn, and the rules that must hold
are buried in it.

WHAT THE CUT DOES
-----------------
`plan` reads the brief, block by block (a paragraph, or a list item with its
continuation lines), outside the generated `LIVE-STATE` blocks, code fences
and tables, and sorts each block:

  * KEEP: headings, tables, short lines, the binding `IMPORTANT:` rules the
    template carries (fewer than ten), and any prose that is neither dated
    history nor a long rule.
  * HISTORY: a block that carries a date and is not a rule, or that opens as
    history ("History:", "Retired", "Corrected", "Restored", ...). Moved, word
    for word, to the history file.
  * RULE: a hand-written rule (rule language, `claude_md_guard`) that is long
    or dated. It becomes a standing rule in the rules folder (its first
    sentence is the rule, the rest is why, scope everywhere), so its one line
    stays in view through the brief's rules index and the whole rule loads in
    every skill; its full text is also kept word for word in the history file.

Nothing is dropped: every block that leaves the brief is in the history file
verbatim, the whole brief before the cut is kept beside it, and the plan's
own check (`claude_md_guard.report`) must find every removed line there.

WHERE THE HISTORY GOES, AND HOW IT IS REACHED
---------------------------------------------
`_hq/rules/history/Brief_history_<date>.md` (the moved blocks, under their
headings) and `Brief_before_diet_<date>.md` (the whole brief as it was). The
one loader reaches them on demand: `context_loader.load_context(...,
history="<words>")` answers the moved blocks that carry those words. The file
names say neither ruling nor decision, so the one-time pull of past rulings
never reads every dated line in them as a ruling.

ONLY AFTER THE DECISION LIST (ruling A5)
----------------------------------------
The cut is refused while any ruling in the brief is not yet on the decision
list or waiting there to be confirmed (`past_rulings`): the owner first says
"pull in my past rulings", so no ruling leaves the brief before it reaches the
list.

Writes, only in `apply`: the two history files, the standing rules (through
`rules_store.state_rule`) and the brief, then the brief's rules index.

Command line, from the plugin root:
    python shared/scripts/claude_md_diet.py <workspace> measure
    python shared/scripts/claude_md_diet.py <workspace> plan [--json]
    python shared/scripts/claude_md_diet.py <workspace> apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

BRIEF = "CLAUDE.md"
INSTRUCTIONS_REL = "_hq/Claude_Instructions.txt"
HISTORY_DIR_REL = "_hq/rules/history"
HISTORY_PREFIX = "Brief_history_"
BEFORE_PREFIX = "Brief_before_diet_"
#: The always-loaded target (ruling A5: about 1,500 words).
TARGET_WORDS = 1500
#: A rule longer than this, or dated, leaves the brief for the rules folder.
RULE_KEEP_WORDS = 60
#: A block this short stays, dated or not.
SHORT_WORDS = 25
RULE_CHARS = 300
#: A moved rule's first sentence is at least this long, or the block stays.
MIN_RULE_CHARS = 20
WHY_CHARS = 600

_ISO = re.compile(r"(?<![\w/.-])20\d\d-\d\d-\d\d(?![\w/-])")
_MONTH = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+\d{1,2}\b")
_HISTORY_CUE = re.compile(
    r"^\W*(?:\(?history\b|retired\b|corrected\b|restored\b|previously\b|"
    r"superseded\b|address change\b|amended\b)", re.I)
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+\S")
_BULLET = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+")
_FENCE = re.compile(r"^\s*(```|~~~)")
_LIVE_START = re.compile(r"<!--\s*LIVE-STATE:[\w-]+\b[^>]*-->")
_LIVE_END = re.compile(r"<!--\s*/LIVE-STATE:[\w-]+\s*-->")


def _ws(workspace_root) -> Path:
    return Path(str(workspace_root))


def words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _read(p: Path) -> str:
    """The file exactly as it is, line endings included, so the brief is
    written back in its own line endings and the kept copy is exact."""
    try:
        with p.open(encoding="utf-8", newline="") as fh:
            return fh.read()
    except OSError:
        return ""


def measure(workspace_root) -> dict:
    """Words loaded before the first message: the brief, and the saved copy of
    the account instructions block when the folder has one."""
    ws = _ws(workspace_root)
    brief = words(_read(ws / BRIEF))
    instr = words(_read(ws / INSTRUCTIONS_REL))
    return {"brief_words": brief, "instructions_words": instr,
            "total_words": brief + instr, "target_words": TARGET_WORDS}


# ---------------------------------------------------------------------------
# Reading the brief into blocks
# ---------------------------------------------------------------------------

def blocks(text: str) -> List[dict]:
    """The brief as consecutive blocks, each `{kind, lines, heading}`; `kind`
    is `fixed` (generated block, fence, table, heading, blank) or `body`."""
    out: List[dict] = []
    lines = text.splitlines(keepends=True)
    heading = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if _LIVE_START.search(line):
            j = i
            while j < len(lines) and not _LIVE_END.search(lines[j]):
                j += 1
            out.append({"kind": "fixed", "lines": lines[i:j + 1], "heading": heading})
            i = j + 1
            continue
        if _FENCE.match(line):
            mark = _FENCE.match(line).group(1)
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith(mark):
                j += 1
            out.append({"kind": "fixed", "lines": lines[i:j + 1], "heading": heading})
            i = j + 1
            continue
        if _HEADING.match(line):
            heading = line.strip()
            out.append({"kind": "heading", "lines": [line], "heading": heading})
            i += 1
            continue
        if not line.strip() or line.lstrip().startswith("|") or line.lstrip().startswith("<!--"):
            out.append({"kind": "fixed", "lines": [line], "heading": heading})
            i += 1
            continue
        j = i + 1
        bullet = _BULLET.match(line)
        while j < len(lines):
            nxt = lines[j]
            if (not nxt.strip() or _HEADING.match(nxt) or _FENCE.match(nxt)
                    or _LIVE_START.search(nxt) or nxt.lstrip().startswith("|")):
                break
            nb = _BULLET.match(nxt)
            if nb and (not bullet or len(nb.group(1)) <= len(bullet.group(1))):
                break
            if bullet and not nb and not nxt.startswith((" ", "\t")):
                break
            j += 1
        out.append({"kind": "body", "lines": lines[i:j], "heading": heading})
        i = j
    return out


def _dated(text: str) -> bool:
    return bool(_ISO.search(text) or _MONTH.search(text))


def classify(block: dict) -> str:
    """`keep`, `history` or `rule` for one body block."""
    import claude_md_guard
    text = "".join(block["lines"])
    flat = " ".join(text.split())
    stripped = _BULLET.sub("", flat, count=1)
    if "IMPORTANT:" in flat:
        return "keep"
    n = words(flat)
    if n <= SHORT_WORDS:
        return "keep"
    rule = claude_md_guard.is_rule_language(flat)
    if rule:
        # A rule is never moved to history alone, even one that opens like
        # history ("Retired ..., never ..."): it stays, or it becomes a
        # standing rule that loads in every skill.
        if n > RULE_KEEP_WORDS or _dated(flat):
            return "rule" if len(_first_sentence(text)[0]) >= MIN_RULE_CHARS else "keep"
        return "keep"
    if _HISTORY_CUE.match(stripped.replace("**", "").replace("*", "")) or _dated(flat):
        return "history"
    return "keep"


def _first_sentence(text: str) -> tuple:
    """(the rule, why): the first sentence, ended by a full stop, question or
    exclamation mark followed by a capital, never inside "e.g." or "U.S.",
    and never shorter than `MIN_RULE_CHARS`."""
    flat = " ".join(_BULLET.sub("", text, count=1).replace("**", "").split())
    head = flat
    for m in re.finditer(r"[.!?](?=\s+[\"'(]?[A-Z])", flat):
        cand = flat[:m.end()]
        if len(cand) >= MIN_RULE_CHARS and not re.search(
                r"\b(?:e\.g|i\.e|etc|vs|Dr|Mr|Mrs|Ms|St|No|U\.S|a\.m|p\.m)\.$", cand):
            head = cand
            break
    rest = flat[len(head):].strip()
    if len(head) > RULE_CHARS:
        head, rest = head[:RULE_CHARS - 1].rstrip() + "…", flat
    return head, rest[:WHY_CHARS]


def _drop_empty_headings(parts: List[dict]) -> List[dict]:
    """A heading whose whole section moved out goes with it."""
    out: List[dict] = []
    for idx, part in enumerate(parts):
        if part["kind"] == "heading":
            level = len(_HEADING.match(part["lines"][0]).group(1))
            has_body = False
            for later in parts[idx + 1:]:
                if later["kind"] == "heading":
                    if len(_HEADING.match(later["lines"][0]).group(1)) <= level:
                        break
                    continue
                if "".join(later["lines"]).strip():
                    has_body = True
                    break
            if not has_body and part.get("emptied"):
                continue
        out.append(part)
    return out


def _tidy(text: str) -> str:
    """Runs of blank lines left by the moves collapse to one, outside code
    fences only."""
    out: List[str] = []
    fence = None
    blanks = 0
    for line in text.splitlines(keepends=True):
        m = _FENCE.match(line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
        if fence is None and not line.strip():
            blanks += 1
            if blanks > 1:
                continue
        else:
            blanks = 0
        out.append(line)
    return "".join(out)


def _unique_rel(ws: Path, stem: str) -> str:
    """`stem.md`, or `stem_2.md`, `stem_3.md` ...: a kept file is never
    overwritten, so a second cut on the same day keeps the first one's copy."""
    rel = stem + ".md"
    n = 2
    while (ws / rel).exists():
        rel = f"{stem}_{n}.md"
        n += 1
    return rel


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

def _rulings_waiting(ws: Path) -> List[str]:
    """Rulings in the brief that the decision list does not hold yet: not
    pulled in, or pulled in and not yet confirmed (ruling A5: the list must
    hold them before the brief is cut). A check that cannot run blocks."""
    try:
        import past_rulings
        res = past_rulings.preview_past_rulings(ws)
        if not res.get("ok"):
            return [f"(could not check: {res.get('reason') or 'unknown'})"]
        out = []
        for row in res.get("rows") or []:
            data = row.get("data") if isinstance(row.get("data"), dict) else row
            if data.get("source_file") == BRIEF or data.get("status") == "error":
                out.append(str(data.get("ruling_text") or data.get("title") or ""))
        # Pulled in and still unanswered, whichever day it is due on the tab.
        events = past_rulings._events(ws)
        closed: set = set()
        for ev in events:
            pid = (ev.get("data") or {}).get("proposal_id")
            if ev.get("type") in ("brain_proposal_resolved", "brain_proposal_expired"):
                closed.add(pid)
            elif ev.get("type") == "brain_proposal_reopened":
                closed.discard(pid)
        for ev in events:
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if (ev.get("type") == "brain_proposal" and data.get("kind") == past_rulings.KIND
                    and data.get("source_file") == BRIEF
                    and data.get("proposal_id") not in closed):
                out.append(str(data.get("ruling_text") or data.get("title") or ""))
        return out
    except Exception as exc:  # noqa: BLE001: unknown is not "none waiting"
        return [f"(could not check: {type(exc).__name__})"]


def plan(workspace_root, *, today: Optional[str] = None) -> dict:
    """What the cut would do, without writing. Answers `{ok, reason?, line,
    before, after, moves, rules, history_text, after_text, guard}`."""
    import claude_md_guard
    ws = _ws(workspace_root)
    path = ws / BRIEF
    if not path.is_file():
        return {"ok": False, "reason": "no_brief", "line": "There is no brief in this folder."}
    before_text = _read(path)
    day = today or date.today().isoformat()
    parts = blocks(before_text)
    kept: List[dict] = []
    moves: List[dict] = []
    rules: List[dict] = []
    for part in parts:
        if part["kind"] != "body":
            kept.append(dict(part))
            continue
        verdict = classify(part)
        if verdict == "keep":
            kept.append(dict(part))
            continue
        text = "".join(part["lines"])
        moves.append({"as": verdict, "heading": part["heading"], "text": text})
        if verdict == "rule":
            rule, why = _first_sentence(text)
            rules.append({"rule": rule, "why": why, "heading": part["heading"]})
        for k in reversed(range(len(kept))):
            if kept[k]["kind"] == "heading":
                kept[k]["emptied"] = True
                break
    after_text = _tidy("".join("".join(p["lines"]) for p in _drop_empty_headings(kept)))
    history_rel = _unique_rel(ws, f"{HISTORY_DIR_REL}/{HISTORY_PREFIX}{day}")
    before_rel = _unique_rel(ws, f"{HISTORY_DIR_REL}/{BEFORE_PREFIX}{day}")
    history_text = render_history(moves, day=day, before_rel=before_rel)
    guard = claude_md_guard.report(before_text, after_text)
    lost = [ln for ln in guard["removed"] if ln.strip() not in history_text]
    out = {
        "ok": True, "day": day,
        "before": measure(ws),
        "after": {"brief_words": words(after_text)},
        "moves": moves, "rules": rules,
        "n_history": sum(1 for m in moves if m["as"] == "history"),
        "n_rules": len(rules),
        "history_rel": history_rel, "before_rel": before_rel,
        "history_text": history_text, "after_text": after_text,
        "guard": {"removed": len(guard["removed"]), "lost": lost},
    }
    if lost:
        out.update(ok=False, reason="would_lose_lines",
                   line="Not cut: some lines would leave the brief without "
                        "reaching the history file.")
        return out
    if not moves:
        out.update(ok=False, reason="nothing_to_move",
                   line=f"Nothing to move: the brief is "
                        f"{out['before']['brief_words']} words and carries no "
                        f"dated history or long rules.")
        return out
    waiting = _rulings_waiting(ws)
    if waiting:
        out.update(ok=False, reason="rulings_not_on_list", waiting=waiting[:10],
                   line="Not cut yet: the brief holds rulings the decision list "
                        "does not hold yet. Say \"pull in my past rulings\" and "
                        "confirm or dismiss each on the Review tab first, so "
                        "none is lost.")
        return out
    after_words = out["after"]["brief_words"]
    out["line"] = (f"The brief would go from {out['before']['brief_words']} to "
                   f"{after_words} words: {out['n_history']} dated history "
                   f"blocks and {out['n_rules']} long rules move out, every word "
                   f"kept in the history file"
                   + ("." if after_words <= TARGET_WORDS * 1.2 else
                      f"; it would still be over the {TARGET_WORDS}-word target, "
                      f"so the rest is yours to trim."))
    return out


def render_history(moves: List[dict], *, day: str, before_rel: str) -> str:
    """The history file: every moved block, word for word, under its
    heading, in the brief's own order."""
    lines = [f"# Brief history, moved out of the brief on {day}", "",
             "Every block below left the always-loaded brief word for word. "
             "Long rules among them are also standing rules in the rules "
             f"folder. The whole brief as it was is in `{before_rel}`.", ""]
    last = None
    for m in moves:
        if m["heading"] != last:
            head = m["heading"] if (m["heading"] or "").startswith("## ") else (
                "## " + (m["heading"] or "(top of the brief)").lstrip("# ").strip())
            lines += ["", head, ""]
            last = m["heading"]
        lines += [m["text"].rstrip("\r\n"), ""]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# The cut
# ---------------------------------------------------------------------------

def apply_diet(workspace_root, *, today: Optional[str] = None,
               source_skill: str = "command-room-update-bridge") -> dict:
    """Make the cut the plan describes. Refuses exactly when the plan does.
    Order: the history files first, then the standing rules, then the brief,
    then the brief's rules index, so a failure part way never leaves a block
    out of the brief that is not on file."""
    from atomic_write import atomic_write_text
    import rules_store

    ws = _ws(workspace_root)
    p = plan(ws, today=today)
    if not p["ok"]:
        return {k: p[k] for k in ("ok", "reason", "line") if k in p}
    before_text = _read(ws / BRIEF)
    atomic_write_text(ws / p["before_rel"], before_text)
    atomic_write_text(ws / p["history_rel"], p["history_text"])
    stated = []
    for r in p["rules"]:
        res = rules_store.state_rule(ws, r["rule"], why=r["why"],
                                     origin=rules_store.ORIGIN_STANDING,
                                     moved_from=p["history_rel"],
                                     source_skill=source_skill)
        stated.append(res["rule_id"])
    atomic_write_text(ws / BRIEF, p["after_text"])
    try:
        import render_claude_md
        index = render_claude_md.regenerate_rules_block(ws)
    except Exception as exc:  # noqa: BLE001: the index follows on the next render
        index = {"rules": f"failed: {type(exc).__name__}"}
    after = measure(ws)
    return {"ok": True, "before_words": p["before"]["brief_words"],
            "after_words": after["brief_words"], "history_rel": p["history_rel"],
            "before_rel": p["before_rel"], "rules": stated, "index": index,
            "line": (f"Your brief went from {p['before']['brief_words']} to "
                     f"{after['brief_words']} words. The dated history is kept "
                     f"word for word in your history file, and the long rules "
                     f"are standing rules that load in every skill.")}


# ---------------------------------------------------------------------------
# Reaching the history on demand (read by the one loader)
# ---------------------------------------------------------------------------

def history_files(workspace_root) -> List[Path]:
    d = _ws(workspace_root) / HISTORY_DIR_REL
    if not d.is_dir():
        return []
    return sorted(d.glob(HISTORY_PREFIX + "*.md"))


def search_history(workspace_root, query: str, *, limit: int = 8) -> List[dict]:
    """The moved-out blocks that carry every word of `query`, newest file
    first: `{file, heading, text}`. Read-only."""
    want = [w for w in re.findall(r"[a-z0-9]+", str(query or "").lower()) if len(w) > 2]
    if not want:
        return []
    ws = _ws(workspace_root)
    out: List[dict] = []
    for f in reversed(history_files(ws)):
        heading = ""
        for part in blocks(_read(f)):
            if part["kind"] == "heading":
                heading = part["lines"][0].strip()
                continue
            text = "".join(part["lines"]).strip()
            have = set(re.findall(r"[a-z0-9]+", text.lower()))
            if text and heading.startswith("## ") and all(w in have for w in want):
                out.append({"file": f.relative_to(ws).as_posix(),
                            "heading": heading, "text": text})
                if len(out) >= limit:
                    return out
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("workspace")
    ap.add_argument("cmd", choices=("measure", "plan", "apply"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "measure":
        print(json.dumps(measure(args.workspace)))
    elif args.cmd == "plan":
        res = plan(args.workspace)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(res["line"])
    else:
        print(json.dumps(apply_diet(args.workspace), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
