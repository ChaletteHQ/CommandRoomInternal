#!/usr/bin/env python3
"""
session_narrative — the sweep's narrative leg (SPEC SESSSTORY1, 2026-08-27).

WHAT THIS CLOSES
-----------------
Facts already write live or get caught by the nightly session-sweep
(session_sweep.py). The NARRATIVE — the human-readable session-notes block —
used to write only inside the "end session" ritual, which most sessions never
run. This module lets the sweep COMPOSE that block overnight instead, from
material the sweep already has in hand:

  - that session's `session_chapter` markers (session_chapter.py) — one-line,
    already-curated pointers a session wrote at its own natural boundaries;
  - that session's already-promoted event counts (what session_sweep.py's
    other legs recovered THIS run for the same session_id).

It NEVER re-reads a raw transcript itself (that judgment call, when a session
wrote no chapters, belongs to the session-sweep SKILL at runtime — this module
only ever receives already-composed lines, never transcript text) and it NEVER
puts transcript content anywhere durable: the composed .md block is built from
one-line chapter text plus structural counts, and the substrate marker this
module writes is counts-only (SESSSTORY1 scope fence).

TWO WRITERS, ONE MARKER, ONE DEDUP.
------------------------------------
Both the nightly sweep (this module, `origin="swept"`) and the workspace-
manager "end session" ritual (`origin="ritual"`) may compose a session's
notes block. Whichever gets there first wins — the other calls
`already_composed()` first and skips (Ruling §0.3: "the workspace-manager
end-session flow checks whether the sweep already composed this session's
block ... and skips ... rather than duplicating"). The dedup handle is a
single `note` event per session — `data.recovered_kind: "session_narrative"`,
`data.source_ref: narrative_source_ref(session_id)` — indexed through the
SAME `.source_refs.idx` sidecar every other capture uses (no second dedup
mechanism; no new event type — `note` is the existing family, per
EVENT_TYPES.md's "no parallel swept variants" rule).

NEVER CREATES A NOTES FILE (Ruling §0.4). `compose_and_append` appends to an
EXISTING `SESSION_NOTES*.md` inside the resolved project folder only. No
file, no folder, no thread resolvable → nothing is written except (on a real
compose) the marker event; a session with nowhere to land its narrative is a
silent, receipted no-op, exactly like an empty sweep window.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from entities_io import entities_collection  # noqa: E402
from event_gate import append_event  # noqa: E402
import source_ref_index  # noqa: E402

# The origin values EVENT_TYPES.md's Session chapter lane documents. A reader
# tells ritual-written from sweep-written by this string alone — both in the
# substrate marker's data.origin AND in the composed heading text below.
ORIGIN_SWEPT = "swept"
ORIGIN_RITUAL = "ritual"
_VALID_ORIGINS = frozenset({ORIGIN_SWEPT, ORIGIN_RITUAL})

_MARKER_KIND = "session_narrative"

# H2 heading line: any level-2 markdown heading. Used only to find the FIRST
# existing dated entry so a new block can be inserted before it (the
# "most recent first" convention Step 2 of "end session" already uses).
_H2_LINE_RE = re.compile(r"^##[ \t]+\S")

# Recovered-item type -> human plural, in a fixed display order (structure
# only — never the item's own summary text; SESSSTORY1 scope fence).
_COUNT_LABELS = (
    ("commitment", "commitment"),
    ("decision", "decision"),
    ("interaction", "interaction"),
    ("note", "note"),
)


def narrative_source_ref(session_id: str) -> str:
    """Dedup handle for ONE session's composed narrative — deliberately a
    DIFFERENT string than session_sweep.session_source_ref's plain
    `session:{id}` (which is shared across every per-item capture of that
    session and is explicitly NOT a valid dedup key on its own). The
    `:narrative` suffix keys the whole-session composition as its own,
    single-shot unit."""
    sid = (session_id or "").strip()
    if not sid:
        raise ValueError("session_id is required")
    return f"session:{sid}:narrative"


def already_composed(workspace_root, session_id: str) -> bool:
    """True when EITHER writer (sweep or ritual) already composed this
    session's notes block. Self-heals via source_ref_index's own rebuild-on-
    first-check, same as every other dedup consumer in this codebase."""
    return source_ref_index.check(
        workspace_root, source_ref=narrative_source_ref(session_id)
    )


def mark_composed(
    workspace_root,
    session_id: str,
    *,
    origin: str,
    source_skill: str,
    thread_id: Optional[str] = None,
) -> List[dict]:
    """Append the one counts-only marker `note` event for this session's
    composed narrative. Callers write this AFTER a successful compose (or
    call it standalone from the "end session" reconcile check — see
    workspace-manager SKILL.md). Never contains the composed text itself,
    only bookkeeping (SESSSTORY1 scope fence: never transcript content in a
    receipt-shaped record) — deliberately no `notes_path` field either:
    `already_composed`/dedup only need `source_ref`, and `resolve_notes_path`
    is cheap to re-derive on demand, so a field with no reader stays out
    (G29 discipline)."""
    if origin not in _VALID_ORIGINS:
        raise ValueError(f"origin must be one of {sorted(_VALID_ORIGINS)}, got {origin!r}")
    data: Dict[str, Any] = {
        "recovered_kind": _MARKER_KIND,
        "origin": origin,
        "session_id": session_id,
        "source_ref": narrative_source_ref(session_id),
        "summary": f"Session notes composed ({origin}) for session {session_id}",
    }
    event: Dict[str, Any] = {"type": "note", "source_skill": source_skill, "data": data}
    if thread_id:
        event["primary_thread_id"] = thread_id
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    return append_event(events_path, event, holder=source_skill)


def _load_entities(workspace_root) -> dict:
    path = Path(workspace_root) / "_hq" / "data" / "entities.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_notes_path(workspace_root, thread_id: Optional[str]) -> Optional[Path]:
    """The EXISTING `SESSION_NOTES*.md` file for `thread_id`'s project folder,
    or None when the thread, its folder, or a notes file inside it does not
    resolve. NEVER creates anything — a None return means "events only" per
    Ruling §0.4; the caller must not fall back to scaffolding one (that is
    cleanup.backfill_session_notes's job, and it is deliberately not called
    from this module).

    Folder resolution goes through `thread_writer.resolve_folder_name` — the
    SAME explicit-then-guess resolver every other writer in this codebase
    uses (an explicit `folder_name` is checked against disk verbatim; a
    missing one is guessed from `canonical_name` against real directories).
    This module never re-derives that logic — a second, slightly different
    resolver is exactly the kind of drift EVENT_TYPES.md's "one writer, one
    reader" doctrine exists to prevent."""
    if not thread_id:
        return None
    data = _load_entities(workspace_root)
    if not data:
        return None
    threads = entities_collection(data, "threads")
    thread = next((t for t in threads if isinstance(t, dict) and t.get("id") == thread_id), None)
    if thread is None:
        return None
    try:
        from thread_writer import resolve_folder_name
    except ImportError:
        sys.path.insert(0, str(_HERE))
        from thread_writer import resolve_folder_name
    folder_rel = resolve_folder_name(
        workspace_root,
        thread.get("canonical_name") or "",
        thread.get("folder_name"),
    )
    if not folder_rel:
        return None
    folder = Path(workspace_root) / folder_rel
    if not folder.is_dir():
        return None
    candidates = []
    for p in sorted(folder.glob("SESSION_NOTES*.md")):
        name = p.name.upper()
        if "_ARCHIVE" in name or name.endswith("_INDEX.MD") or "TEMPLATE" in name:
            continue
        if p.is_file():
            candidates.append(p)
    return candidates[0] if candidates else None


def _clean_line(text: str) -> str:
    """Collapse a piece of already-curated one-line material into a safe
    bullet: no embedded newlines, no leading markdown furniture that would be
    misread as a heading/table row by the rollover reshaper
    (cleanup_actions.py's H2-block parser)."""
    s = " ".join((text or "").split())
    s = s.lstrip("#|").strip()
    return s


def compose_block(
    session_id: str,
    *,
    for_date: str,
    chapter_lines: Optional[Iterable[str]] = None,
    recovered_counts: Optional[Dict[str, int]] = None,
    origin: str = ORIGIN_SWEPT,
) -> Optional[str]:
    """Build the markdown block text, or None when there is nothing to say
    (no chapters and no recovered items — a genuinely quiet session).

    Heading carries the origin marker as PLAIN TEXT (`— origin: swept`) —
    readable both by a human scanning the file and by grep/tests — and stays
    inside `_ISO_LEAD_RE`'s date-prefix contract so cleanup's Rule 1 rollover
    keeps parsing the entry correctly (verified against cleanup_actions.py's
    `_entry_date`)."""
    if origin not in _VALID_ORIGINS:
        raise ValueError(f"origin must be one of {sorted(_VALID_ORIGINS)}, got {origin!r}")

    lines = [_clean_line(t) for t in (chapter_lines or []) if _clean_line(t)]

    counts = recovered_counts or {}
    count_bits = [
        f"{counts[key]} {label}{'s' if counts[key] != 1 else ''}"
        for key, label in _COUNT_LABELS
        if counts.get(key)
    ]
    if count_bits:
        lines.append("Captured: " + ", ".join(count_bits) + ".")

    if not lines:
        return None

    heading = f"## {for_date} — origin: {origin}"
    body = "\n".join(f"- {ln}" for ln in lines)
    return f"{heading}\n{body}\n"


def _insert_entry(existing_text: str, block_text: str) -> str:
    """Insert `block_text` as the FIRST H2 entry (most-recent-first, matching
    end-session Step 2) — right after the file's preamble and before the
    current first dated entry, or at end-of-file when there is no H2 yet.
    Never touches a byte outside the insertion point (existing entries are
    carried forward verbatim, cumulative-rule per workspace-detail.md)."""
    lines = existing_text.splitlines(keepends=True)
    insert_at = len(lines)
    for i, ln in enumerate(lines):
        if _H2_LINE_RE.match(ln):
            insert_at = i
            break
    prefix = "".join(lines[:insert_at])
    suffix = "".join(lines[insert_at:])
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n"
    new_block = block_text if block_text.endswith("\n") else block_text + "\n"
    if suffix:
        new_block += "\n"
    return prefix + new_block + suffix


def compose_and_append(
    workspace_root,
    *,
    session_id: str,
    thread_id: Optional[str],
    for_date: str,
    chapter_lines: Optional[Iterable[str]] = None,
    recovered_counts: Optional[Dict[str, int]] = None,
    source_skill: str = "session-sweep",
    origin: str = ORIGIN_SWEPT,
) -> Dict[str, Any]:
    """Compose + append ONE session's narrative block, then land its marker
    event — or explain, via `reason`, exactly which fence stopped it.

    Returns {"composed": bool, "reason": str|None, "notes_path": str|None}.
    `composed` is True only when a block was ACTUALLY WRITTEN to a notes
    file — the receipt's `n_narratives_composed` counts this field, not
    "a session got as far as this function" (Acceptance fixture parity:
    "notes file appended in-format, receipt n=1").
    """
    if already_composed(workspace_root, session_id):
        return {"composed": False, "reason": "already_composed", "notes_path": None}

    block = compose_block(
        session_id,
        for_date=for_date,
        chapter_lines=chapter_lines,
        recovered_counts=recovered_counts,
        origin=origin,
    )
    if block is None:
        return {"composed": False, "reason": "nothing_to_compose", "notes_path": None}

    notes_path = resolve_notes_path(workspace_root, thread_id)
    if notes_path is None:
        # Ruling §0.4 — events only, no file created. No marker either: a
        # future run (once a notes file exists) should still get a chance to
        # compose the entry it never got to write.
        return {"composed": False, "reason": "no_notes_file", "notes_path": None}

    try:
        from atomic_write import atomic_write_text
    except ImportError:
        sys.path.insert(0, str(_HERE))
        from atomic_write import atomic_write_text

    existing = notes_path.read_text(encoding="utf-8")
    new_text = _insert_entry(existing, block)
    # create_parents=False (FOLDERGUARD): this is the CEO's project folder,
    # never ours to fabricate — and it already exists, since notes_path came
    # from resolve_notes_path finding a real file inside it.
    atomic_write_text(notes_path, new_text, create_parents=False)

    rel_path = str(notes_path.relative_to(Path(workspace_root))).replace("\\", "/")
    mark_composed(
        workspace_root,
        session_id,
        origin=origin,
        source_skill=source_skill,
        thread_id=thread_id,
    )
    return {"composed": True, "reason": None, "notes_path": rel_path}


__all__ = [
    "ORIGIN_SWEPT",
    "ORIGIN_RITUAL",
    "narrative_source_ref",
    "already_composed",
    "mark_composed",
    "resolve_notes_path",
    "compose_block",
    "compose_and_append",
]
