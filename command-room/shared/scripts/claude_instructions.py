#!/usr/bin/env python3
"""The one block a client pastes into the account-level Claude instructions
(FILECARD1, cr1#611; owner rulings 2026-11-03).

WHY
---
The workspace rules file is the only carrier of the owner's routing rules, and
it has been seen to drop out of merged-seat chats (cr1#517) and to never load
in one cloud setup (DEEP_TEST_2026-09-24 finding 2). Most skills reach the chat
with no description (cr1#498), so a plain-language ask has nothing to route on.
The one layer that is always loaded, whichever folder is open and whether or
not the rules file loaded, is the account's own Claude instructions (Settings,
the box for how Claude should respond). Clients will not use projects (owner
ruling 2026-11-03), so the block goes there, and the plugin cannot write there:
it can only generate the block, show it once, save it in the folder and show it
again on request.

WHAT THE BLOCK IS
-----------------
Fences and routing only: no preferences, people, projects or persona. Nine
numbered lines (`FENCE_LINES` plus the map and the folder tiers), generated per
seat from the skills installed on that seat (custom pack skills included,
because a pack's skills are copied into `skills/` on promote). The three folder
tiers are the module's `TIER_LINES`, and the Access preamble in
`shared/WORKSPACE_ACCESS.md` carries the same three sentences, byte for byte:
one wording, two places (tests/run_claude_instructions_test.py).

THE MAP AND THE CAP
-------------------
The map is "the ask in plain words -> the skill that owns it", one line per
installed skill, built from each skill's own description (the first phrases it
says it fires on). The account instructions box has a length cap the build
records in the acceptance walk (`BOX_CAP`; None until recorded). When the full
map does not fit the cap, the map carries the daily dozen (`DAILY_DOZEN`) and
the rest route on their descriptions.

HOW IT RUNS
-----------
The generator reads the PLUGIN's own `skills/` folder, which is beside the
code on every seat (plugin code, never the workspace), so it runs in the chat's
own shell; `shown_status` is the one workspace read, a helper on the read
door. The save is `plan write` and the record is `plan append_jsonl` (one
`claude_instructions_shown` row carrying the block's hash), both through the
access layer, both rendered by the skill. The card is the file-card rule
(CONTRACT Rule 3).

    python3 shared/scripts/claude_instructions.py --plugin-root <RT> --folder-name "<name>" [--cap N] [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

#: Where the block is saved in the folder, through `plan write`.
SAVED_REL = "_hq/Claude_Instructions.txt"

#: The row that records a showing, with the block's hash in `data.sha256`.
SHOWN_EVENT = "claude_instructions_shown"

#: The length cap of the account instructions box. None until the acceptance
#: walk on cr1#611 records it on a test account; the walk's number goes here.
BOX_CAP: Optional[int] = None

#: The three folder tiers, one wording in two places: this block, and the
#: Access preamble in shared/WORKSPACE_ACCESS.md (rule 9). Ruled 2026-11-03:
#: the third tier carries no notice.
TIER_LINES: Tuple[str, str, str] = (
    "If the owner's computer is connected, work in the Command Room folder; "
    "if it is not attached, ask for it by name before doing any work.",
    "If the owner's computer is not connected, ask for the Command Room folder "
    "to be attached from OneDrive or Google Drive before doing any work.",
    "If neither is possible, answer as plain Claude and do not go through "
    "Command Room.",
)

#: The daily dozen (owner ruling 2026-11-03, cr1#611): the map when the full
#: one does not fit the box. Plain words -> the skill that owns them.
DAILY_DOZEN: Tuple[Tuple[str, str], ...] = (
    ("call prep", "call-prep"),
    ("meeting notes", "meeting-notes"),
    ("inbox", "inbox-triage"),
    ("memo", "memo-writer"),
    ("one-pager", "one-pager-composer"),
    ("research", "research"),
    ("people", "people-crm"),
    ("commitments", "commitment-triage"),
    ("end of day", "end-of-day"),
    ("weekly recap", "weekly-recap"),
    ("email", "email-writer"),
    ("decision log", "decision-log"),
)

#: Skills that never appear in the map: setup and plumbing a client is not
#: asked to say, and the chat's own apply path.
NOT_IN_MAP: frozenset = frozenset({
    "apply-choices", "command-room-onboarding", "command-room-update-bridge",
    "enable-command-room-schedules", "level-up-command-room", "session-backfill",
    "session-sweep", "workspace-ingest", "ingest-context", "scaffold-automation",
})

HEADER = "Command Room, how to work with me"

#: The fence lines, in the customer's voice, numbered in `render`. Lines 1
#: (the map) and 2 (the folder tiers) are composed; these are 3 to 9.
FENCE_LINES: Tuple[str, ...] = (
    "A document about my work is a Word, PDF, spreadsheet or deck file in my "
    "Command Room folder, never a Markdown text file and never a Claude Doc, "
    "page or deck unless I ask for one.",
    "Every file you make comes back in this chat as a clickable card, first, "
    "before the summary. A path in words is not delivery.",
    "Emails go through the email-writer skill; research goes through the "
    "research skill.",
    "Nothing is sent without my click: drafts are shown first and saved as "
    "drafts only when I say so.",
    "Widgets are built by the product and passed through unchanged; if the "
    "widget tool is loaded late, load it rather than fall back to text.",
    "Leave the Output picker off for Command Room work.",
    "If my workspace rules did not load in this chat, say so in one sentence "
    "before doing anything else.",
)

MAP_LEAD = ("Everything about my work runs through Command Room, in plain "
            "language. Who owns what:")
MAP_LEAD_DOZEN = ("Everything about my work runs through Command Room, in plain "
                  "language. The daily dozen, and the rest by what they describe:")

# --------------------------------------------------------------------------
# reading the installed skills
# --------------------------------------------------------------------------
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_DESCRIPTION_RE = re.compile(r'^description:\s*"(.*)"\s*$', re.MULTILINE)
_NAME_RE = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)
_TRIGGER_MARK = re.compile(
    r"(?:Fires FIRST on|Fires on|Triggers on|Triggers|Manual fire|Use when[^:'\"`]*?(?:says|asks)?)\s*:?",
    re.I)
_QUOTED = re.compile(
    r"(?<![A-Za-z])'((?:[^']|'(?=[a-z])){2,60}?)'(?![A-Za-z])"
    r"|\"([^\"]{2,60})\""
    r"|`([^`]{2,60})`")
_EXCLUSIONS = re.compile(r"does not fire|do not fire|doesn't fire|never fires|not for:|not on:", re.I)
_PLACEHOLDER = re.compile(r"\[[^\]]*\]")


def installed_skills(plugin_root) -> List[Dict[str, str]]:
    """Every skill under `<plugin_root>/skills/` with a name and a description,
    sorted by name. Custom pack skills are in the same folder on a seat."""
    root = Path(str(plugin_root)) / "skills"
    out: List[Dict[str, str]] = []
    if not root.is_dir():
        return out
    for p in sorted(root.glob("*/SKILL.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _FRONTMATTER_RE.match(text)
        if not fm:
            continue
        name_m = _NAME_RE.search(fm.group(1))
        desc_m = _DESCRIPTION_RE.search(fm.group(1))
        name = (name_m.group(1) if name_m else p.parent.name).strip()
        out.append({"name": name, "description": (desc_m.group(1) if desc_m else "")})
    return out


def phrases_for(description: str, limit: int = 2) -> List[str]:
    """The first plain-words phrases a skill says it fires on, from its own
    description: quoted phrases after the trigger mark, before any exclusion
    clause, placeholders kept as the customer would read them."""
    text = description or ""
    cut = _EXCLUSIONS.search(text)
    if cut:
        text = text[:cut.start()]
    mark = _TRIGGER_MARK.search(text)
    if mark:
        text = text[mark.end():]
    found: List[str] = []
    for m in _QUOTED.finditer(text):
        phrase = next(g for g in m.groups() if g)
        phrase = _PLACEHOLDER.sub(lambda x: x.group(0), phrase).strip()
        if phrase and phrase not in found and not phrase.startswith("tune "):
            found.append(phrase)
        if len(found) >= limit:
            break
    return found


def full_map(skills: Iterable[Dict[str, str]]) -> List[Tuple[str, str]]:
    """(plain words, skill) for every installed skill that belongs in the map."""
    rows: List[Tuple[str, str]] = []
    for s in skills:
        name = s.get("name", "")
        if not name or name in NOT_IN_MAP:
            continue
        phrases = phrases_for(s.get("description", ""))
        words = ", ".join(phrases) if phrases else name.replace("-", " ")
        rows.append((words, name))
    return rows


# --------------------------------------------------------------------------
# the block
# --------------------------------------------------------------------------
def _map_lines(rows: Iterable[Tuple[str, str]]) -> List[str]:
    return [f"   {words}: {skill}" for words, skill in rows]


def compose(rows: List[Tuple[str, str]], folder_name: str, *, dozen: bool) -> str:
    lines: List[str] = [HEADER, ""]
    lines.append(f"1. {MAP_LEAD_DOZEN if dozen else MAP_LEAD}")
    lines.extend(_map_lines(rows))
    tiers = " ".join(TIER_LINES)
    named = f' My Command Room folder is called "{folder_name}".' if folder_name else ""
    lines.append(f"2. {tiers}{named}")
    for i, fence in enumerate(FENCE_LINES, start=3):
        lines.append(f"{i}. {fence}")
    return "\n".join(lines) + "\n"


def render(plugin_root, folder_name: str = "", *, cap: Optional[int] = None,
           skills: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """The block for THIS seat: the full map when it fits `cap` (or when no
    cap is known), the daily dozen otherwise. Answers the text, its hash, its
    length and which map it carries."""
    found = installed_skills(plugin_root) if skills is None else list(skills)
    full = compose(full_map(found), folder_name, dozen=False)
    mode = "full"
    text = full
    if cap is not None and len(full) > int(cap):
        text = compose(list(DAILY_DOZEN), folder_name, dozen=True)
        mode = "daily_dozen"
    out = {"text": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
           "chars": len(text), "map": mode, "skills": len(found), "cap": cap,
           "fits": (cap is None or len(text) <= int(cap)), "rel": SAVED_REL}
    return out


def shown_status(workspace_root, sha256: str = "") -> Dict[str, Any]:
    """Has this seat been shown the block before, and was it this very block?
    A read helper (the read door): walks the ledger for `SHOWN_EVENT` rows."""
    try:
        # the masking reader (personal firewall): this row is workspace-level,
        # so the org scope never drops it, and nothing personal is walked raw
        from events_io import iter_events_org_scoped
    except ImportError:  # pragma: no cover - the runtime always carries it
        return {"shown_before": False, "same_block": False, "last_sha256": None, "n": 0}
    last: Optional[str] = None
    n = 0
    try:
        for row in iter_events_org_scoped(workspace_root):
            if row.get("type") == SHOWN_EVENT:
                n += 1
                last = str((row.get("data") or {}).get("sha256") or "") or last
    except (OSError, ValueError):
        pass
    return {"shown_before": n > 0, "same_block": bool(sha256) and last == sha256,
            "last_sha256": last, "n": n}


def shown_row(sha256: str, *, source_skill: str, map_mode: str, chars: int,
              folder_name: str = "") -> Dict[str, Any]:
    """The one row the skill appends through `plan append_jsonl` after showing
    the block; `id`, `seq` and `ts` are the door's."""
    return {"type": SHOWN_EVENT, "source_skill": source_skill,
            "data": {"sha256": sha256, "map": map_mode, "chars": int(chars),
                     "rel": SAVED_REL, "folder": folder_name, "actor": source_skill}}


def _main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="render the account instructions block")
    ap.add_argument("--plugin-root", required=True)
    ap.add_argument("--folder-name", default="")
    ap.add_argument("--cap", type=int, default=BOX_CAP)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    out = render(a.plugin_root, a.folder_name, cap=a.cap)
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        sys.stdout.write(out["text"])
    return 0


__all__ = [
    "BOX_CAP", "DAILY_DOZEN", "FENCE_LINES", "HEADER", "NOT_IN_MAP", "SAVED_REL",
    "SHOWN_EVENT", "TIER_LINES", "compose", "full_map", "installed_skills",
    "phrases_for", "render", "shown_row", "shown_status",
]

if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
