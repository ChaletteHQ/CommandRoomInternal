#!/usr/bin/env python3
"""
migrate_style_claude_md.py — one-shot migration of a freelanced CLAUDE.md
style rule into the chat_persona store (SPEC STYLEROUTE1 §3).

THE DEFECT THIS SCRIPT REPAIRS
===============================
STYLEROUTE1's origin incident (2026-08-26): a live session heard "Never open
with pleasantries" as a standing style rule, found no frontmatter trigger for
the style verbs (they lived body-only — see workspace-manager/SKILL.md's
Routing corpus), and freelanced the line straight into workspace CLAUDE.md's
"Working style" section instead of the chat_persona store. Zero
`style_changed` events were written; the store was never touched. The
frontmatter routing fix closes the door going forward (new feedback routes
correctly); THIS script is the one-time repair for whatever a workspace
already carries from before that fix landed.

⚠️  DRY-RUN BY DEFAULT (same contract as migrate_commitment_kinds.py and
backfill_substrate.py). The store write is real work on real state — it runs
supervised, under an explicit --apply, never silently.

WHAT IT DOES
============
1. Scans <workspace_root>/CLAUDE.md for a "Working style" (or "Session
   Rules") section and, inside it, a bulleted rule shaped like a bolded
   standing directive: `- **Never <clause> (set YYYY-MM-DD).** ...` (the date
   parenthetical is optional — a freelanced rule may or may not carry one).
   Only lines that read as a `never_line`-shaped rule are candidates; this is
   deliberately narrow (a heuristic false negative costs a rule that stays
   put and gets a manual look; a false positive would silently harvest
   unrelated prose into the persona store).
2. For each candidate, checks `chat_persona.validate_chat_persona` — a
   candidate that fails the D5 bounds fence (too long, or reads as an attempt
   to disable output machinery) is reported as `needs_manual_review`, never
   forced through.
3. Idempotency (second run = no-op): a candidate whose exact text was already
   migrated by THIS script (a `style_changed` event with
   `source_skill == MIGRATION_SOURCE_SKILL` and a `to` matching the
   candidate) is skipped — the plan reports it `already_migrated`, and
   `--apply` writes nothing for it.
4. `--apply` (supervised): for each fresh candidate, merges the rule into the
   current chat_persona as `never_line`, writes it via
   `skill_config_writer.save_skill_config(..., origin="asked")`, and appends
   ONE `style_changed` event (origin `asked`) through
   `event_gate.append_event`, built by `chat_persona.build_style_changed_event`
   — the same writer contract every confirmed style change uses (D4/D7:
   nothing here is a new unconfirmed-write carve-out).
5. Removal of the CLAUDE.md line is PROPOSED ONLY — this script never edits
   CLAUDE.md, under --apply or otherwise. The report names the exact line(s)
   to remove and the reason (now duplicated in the store); a human (or a
   supervised follow-up skill turn) makes that edit, if any, deliberately.

UNDO
====
The written `style_changed` event carries `changes: [{"knob": "never_line",
"from": <prior value>, "to": <candidate>}]` — exactly the shape
workspace-manager's standard style undo lane already reverses on a bare
"undo" (see SKILL.md § "Style — how [BrainName] talks to you"). No new undo
machinery; this write is undo-able by construction because it goes through
the same builder every other confirmed style change does.

USAGE
=====
    python3 shared/scripts/migrate_style_claude_md.py <workspace_root>            # dry-run
    python3 shared/scripts/migrate_style_claude_md.py <workspace_root> --apply    # write
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import chat_persona  # noqa: E402
import skill_config_writer  # noqa: E402
from event_gate import append_event  # noqa: E402

MIGRATION_SOURCE_SKILL = "migrate_style_claude_md"

# Section headings this script will look inside. Case-insensitive, any
# heading level (## or ###) — freelanced rules land under whichever
# hand-authored section the session was editing at the time.
_SECTION_HEADING_RE = re.compile(
    r"^#{1,6}\s*(Working style|Session Rules)\b.*$", re.IGNORECASE | re.MULTILINE)
_ANY_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)

# A bolded "Never ..." bullet, optionally carrying a "(set YYYY-MM-DD)"
# freshness stamp before the closing bold marker — the shape M's own
# freelanced rules take ("- **Never open with pleasantries (set
# 2026-08-26).** No 'Great question,' ...").
_RULE_BULLET_RE = re.compile(
    r"^-\s*\*\*(?P<rule>Never\s[^*]+?)\*\*", re.MULTILINE)
_DATE_STAMP_RE = re.compile(r"\s*\(set\s+\d{4}-\d{2}-\d{2}\)\.?\s*$", re.IGNORECASE)


def _claude_md_path(workspace_root) -> Path:
    return Path(workspace_root) / "CLAUDE.md"


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _clean_candidate(raw: str) -> str:
    """Strip the trailing date stamp and punctuation, collapse whitespace."""
    s = re.sub(r"\s+", " ", raw).strip()
    s = _DATE_STAMP_RE.sub("", s)
    s = s.rstrip(". ").strip()
    return s


def find_candidates(workspace_root) -> list[dict[str, Any]]:
    """Pure read — scan CLAUDE.md for freelanced style-rule bullets.

    Returns a list of {"line_no", "raw_line", "raw_match", "candidate",
    "section"}. Never raises on a missing/unreadable CLAUDE.md — returns [].
    """
    path = _claude_md_path(workspace_root)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    out: list[dict[str, Any]] = []
    for sec_m in _SECTION_HEADING_RE.finditer(text):
        sec_start = sec_m.end()
        nxt = _ANY_HEADING_RE.search(text, sec_start)
        sec_end = nxt.start() if nxt else len(text)
        section_body = text[sec_start:sec_end]
        section_name = sec_m.group(1)
        for bm in _RULE_BULLET_RE.finditer(section_body):
            raw_rule = bm.group("rule")
            candidate = _clean_candidate(raw_rule)
            if not candidate:
                continue
            abs_start = sec_start + bm.start()
            line_no = text.count("\n", 0, abs_start) + 1
            line_start = text.rfind("\n", 0, abs_start) + 1
            line_end = text.find("\n", abs_start)
            if line_end == -1:
                line_end = len(text)
            out.append({
                "line_no": line_no,
                "raw_line": text[line_start:line_end].strip(),
                "candidate": candidate,
                "section": section_name,
            })
    return out


def _already_migrated(workspace_root, candidate: str) -> bool:
    """True when THIS script already wrote a style_changed event carrying
    this exact candidate as a never_line 'to' value. Read-only."""
    events_path = _events_path(workspace_root)
    if not events_path.exists():
        return False
    try:
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            import json
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("source_skill") != MIGRATION_SOURCE_SKILL:
                continue
            if ev.get("type") != "style_changed":
                continue
            data = ev.get("data") or {}
            for c in data.get("changes") or []:
                if (isinstance(c, dict) and c.get("knob") == "never_line"
                        and c.get("to") == candidate):
                    return True
    except OSError:
        return False
    return False


def analyze(workspace_root) -> dict[str, Any]:
    """Pure analysis — reads the workspace, writes nothing.

    Returns {"candidates": [...], "to_migrate": [...], "already_migrated":
    [...], "needs_manual_review": [...]}. Each entry in the sub-lists is one
    of the dicts from find_candidates(), tagged with its disposition.
    """
    candidates = find_candidates(workspace_root)
    to_migrate: list[dict] = []
    already: list[dict] = []
    needs_review: list[dict] = []
    for c in candidates:
        problems = chat_persona.validate_chat_persona({"never_line": c["candidate"]})
        if problems:
            needs_review.append({**c, "problems": problems})
            continue
        if _already_migrated(workspace_root, c["candidate"]):
            already.append(c)
            continue
        to_migrate.append(c)
    return {
        "candidates": candidates,
        "to_migrate": to_migrate,
        "already_migrated": already,
        "needs_manual_review": needs_review,
    }


def apply_migration(workspace_root, plan: dict[str, Any]) -> dict[str, Any]:
    """Write each fresh candidate through the canonical style writer.
    NEVER edits CLAUDE.md — line removal is proposed only (see plan['candidates']
    for line/section pointers; the caller/report surfaces them)."""
    written: list[dict] = []
    for c in plan["to_migrate"]:
        candidate = c["candidate"]
        current = chat_persona.get_chat_persona(workspace_root)
        prior = current.get("never_line", "")
        new_persona = dict(current)
        new_persona["never_line"] = candidate
        skill_config_writer.save_skill_config(
            workspace_root, "chat_persona", new_persona, origin="asked")
        ev = chat_persona.build_style_changed_event(
            layer="chat_persona", origin="asked",
            changes=[{"knob": "never_line", "from": prior, "to": candidate}],
            source_skill=MIGRATION_SOURCE_SKILL,
            evidence=(f"migrated from a freelanced CLAUDE.md rule "
                      f"(section {c['section']!r}, line {c['line_no']})"))
        [stamped] = append_event(str(_events_path(workspace_root)), ev,
                                  holder=MIGRATION_SOURCE_SKILL)
        written.append({
            "candidate": candidate,
            "line_no": c["line_no"],
            "event_seq": stamped.get("seq"),
            "undo_hint": ("reversible via the standard style undo lane — a "
                          "bare 'undo' after this turn reverses this "
                          "style_changed event (workspace-manager)"),
        })
    return {"written": written}


def render_report(plan: dict[str, Any], *, applied: dict | None = None) -> str:
    lines = ["=== style CLAUDE.md migration (STYLEROUTE1) ==="]
    lines.append(f"candidate rule bullets found: {len(plan['candidates'])}")
    if not plan["candidates"]:
        lines.append("nothing to migrate — no freelanced style rule found.")
    for c in plan["to_migrate"]:
        lines.append(f"  [migrate] line {c['line_no']} ({c['section']}): "
                      f"{c['candidate']!r}")
    for c in plan["already_migrated"]:
        lines.append(f"  [already migrated] line {c['line_no']}: "
                      f"{c['candidate']!r}")
    for c in plan["needs_manual_review"]:
        lines.append(f"  [needs manual review] line {c['line_no']}: "
                      f"{c['candidate']!r} — {c['problems']}")
    if applied is None:
        lines.append("MODE: DRY-RUN — nothing written. Re-run with --apply "
                      "(supervised) to write.")
    else:
        lines.append(f"MODE: APPLIED — {len(applied['written'])} "
                      f"style_changed event(s) written.")
        for w in applied["written"]:
            lines.append(f"  wrote never_line={w['candidate']!r} "
                          f"(event seq {w['event_seq']}) — {w['undo_hint']}")
    if plan["to_migrate"] or applied:
        lines.append(
            "PROPOSED (never auto-applied): remove the migrated line(s) "
            "from CLAUDE.md by hand — this script does not edit CLAUDE.md.")
    return "\n".join(lines)


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    workspace_root = argv[1]
    do_apply = "--apply" in argv[2:]
    if not _claude_md_path(workspace_root).exists():
        print(f"no CLAUDE.md under {workspace_root!r}", file=sys.stderr)
        return 2
    plan = analyze(workspace_root)
    applied = apply_migration(workspace_root, plan) if do_apply else None
    print(render_report(plan, applied=applied))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
