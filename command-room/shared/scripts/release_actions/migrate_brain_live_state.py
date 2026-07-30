#!/usr/bin/env python3
"""One-time migration: convert a brain file's hand-authored People section into
the generated Live State block — preserving durable content, and NEVER deleting
a hand-written person.

The safe rule (brain-substrate-drift audit, 2026-05-30): a hand People row that
does NOT match anyone in the event-derived roster is treated as durable /
manually-tracked (e.g. a framework author referenced for context, like Geoff
Woods — "his book is the framework", zero events). Such rows are RELOCATED into
a preserved "Manually tracked" list under the same heading, never dropped. The
generated block then keeps itself current on every `go [project]`.

Idempotent (re-running on an already-migrated section is a no-op), dry-run by
default. Durable content outside the People section is byte-untouched.

stdlib only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))  # shared/scripts on path

import render_thread_live_state as rtls  # noqa: E402
from thread_roster import derive_roster  # noqa: E402
from atomic_write import atomic_write_text  # noqa: E402

PEOPLE_HEADING_RE = re.compile(r"^##\s+(?:\d+\.\s*)?People\s*$", re.IGNORECASE | re.MULTILINE)
NEXT_HEADING_RE = re.compile(r"^##\s+", re.MULTILINE)


def _people_section_span(text: str):
    """(start, end) char offsets of the People section body (after the heading
    line, up to the next `## ` heading or EOF), or None."""
    m = PEOPLE_HEADING_RE.search(text)
    if not m:
        return None
    body_start = text.index("\n", m.start()) + 1 if "\n" in text[m.start():] else len(text)
    nxt = NEXT_HEADING_RE.search(text, body_start)
    body_end = nxt.start() if nxt else len(text)
    return body_start, body_end


def _hand_table_names(section: str) -> list[str]:
    """First-column names from a markdown table, excluding header/separator and
    any content already inside a LIVE-STATE block."""
    # strip any existing generated block so we don't re-capture rendered rows
    section = re.sub(r"<!--\s*LIVE-STATE:.*?/LIVE-STATE:[^>]*-->", "", section, flags=re.S)
    names = []
    for line in section.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0]
        if not first or set(first) <= set("-: ") or first.lower() in ("name",):
            continue
        names.append(first)
    return names


def migrate_brain(workspace_root, thread_id, brain_path, *, dry_run: bool = True) -> dict:
    workspace_root = Path(workspace_root)
    brain_path = Path(brain_path)
    if not brain_path.exists():
        return {"status": "no_file", "changed": False}

    text = brain_path.read_text(encoding="utf-8")
    span = _people_section_span(text)
    if span is None:
        return {"status": "no_people_section", "changed": False}
    body_start, body_end = span
    section = text[body_start:body_end]

    body, source_seq = rtls.format_live_state(workspace_root, thread_id)
    roster_names = {r["name"].lower() for r in derive_roster(workspace_root, thread_id)}

    # Candidate hand-authored people = current table rows UNION any already-
    # preserved bullets from a prior migration. Including the prior bullets is
    # what makes re-running stable (idempotent) AND keeps the never-delete
    # guarantee: a durable name relocated to a bullet on run 1 is re-recognized
    # on run 2 instead of vanishing.
    hand_names = _hand_table_names(section)
    seen, candidates = set(), []
    for n in hand_names + _preserved_names(section):
        if n.lower() not in seen:
            seen.add(n.lower())
            candidates.append(n)
    unmatched = [n for n in candidates
                 if not any(rn in n.lower() or n.lower() in rn for rn in roster_names)]

    block = (f"<!-- LIVE-STATE:people source_seq={source_seq} -->\n"
             f"{body.rstrip()}\n"
             f"<!-- /LIVE-STATE:people -->\n")
    preserved = ""
    if unmatched:
        preserved = ("\n**Manually tracked (no activity signal — durable, kept from the prior hand list):**\n"
                     + "".join(f"- {n}\n" for n in unmatched))

    new_section = "\n" + block + preserved + "\n"
    new_text = text[:body_start] + new_section + text[body_end:]
    changed = new_text != text

    result = {
        "status": "migrated" if changed else "noop",
        "changed": changed,
        "hand_names": hand_names,
        "unmatched_preserved": unmatched,
        "source_seq": source_seq,
        "dry_run": dry_run,
        "new_section_preview": new_section.strip(),
    }
    if changed and not dry_run:
        # FOLDERGUARD: a brain never digs its own project folder. The `no_file`
        # early-return above already means we only get here for a brain that
        # exists, but the flag keeps that true if this path ever changes.
        atomic_write_text(brain_path, new_text, create_parents=False)
    return result


def _preserved_names(section: str) -> list[str]:
    """Names from a prior migration's 'Manually tracked' bullet list (only the
    bullets that follow that marker, so unrelated bullets aren't captured)."""
    out = []
    in_block = False
    for line in section.splitlines():
        if "Manually tracked" in line:
            in_block = True
            continue
        if in_block:
            m = re.match(r"^-\s+(.*\S)\s*$", line.strip())
            if m:
                out.append(m.group(1))
            elif line.strip():
                break
    return out


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("usage: migrate_brain_live_state.py <workspace_root> <thread_id> <brain_path> [--apply]")
        sys.exit(2)
    ws, tid, bp = sys.argv[1], sys.argv[2], sys.argv[3]
    dry = "--apply" not in sys.argv
    r = migrate_brain(ws, tid, bp, dry_run=dry)
    print(f"status={r['status']} changed={r['changed']} dry_run={r['dry_run']}")
    print(f"hand rows: {r.get('hand_names')}")
    print(f"preserved (durable, no events): {r.get('unmatched_preserved')}")
    print("--- new People section ---")
    print(r.get("new_section_preview", ""))
