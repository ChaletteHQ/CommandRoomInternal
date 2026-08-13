#!/usr/bin/env python3
"""BUG-8244 client remediation — bind historical unbound meeting events.

THE GAP THIS REPAIRS. Until BUG-8244, no writer populated the meeting
event's person binding, so client workspaces hold months of meeting events
with `person_ids: []`/absent and no attendee fields — invisible to
relationship cadence, dormancy, person history, commitment closing, and the
rest of the 19-reader blast radius. The reader normalizer
(`event_refs.meeting_person_ids`) repairs rows that carry ANY binding data;
rows that carry NONE need new data, which is what this script derives.

RESOLUTION SOURCES, in order, per unbound `meeting` event:
  1. attendee EMAILS already on the row (any variant) → person_ids via
     `event_refs.email_person_index`. (Rows like these are already readable
     through the normalizer; binding them here just materializes it.)
  2. the meeting TITLE → person display names (canonical_name + aliases +
     nicknames via `people_writer.get_person_display_names`). Conservative
     by construction: a name token binds ONLY when it matches exactly one
     person — an ambiguous "Sam 1:1" with two Sams binds nobody and is
     reported. This is the same repair the reporting client ran by hand
     across several dozen events, which corrected their cadence numbers
     immediately.

WRITE MECHANISM — in-place enrichment under the migration doctrine
(archive-never-delete): `apply_backfill` first copies every touched shard to
`_hq/data/_backups/events_pre_bug8244_<UTC-date>[.shard].jsonl`, then
rewrites ONLY the planned rows, filling ONLY an empty/absent top-level
`person_ids` and stamping `data.binding_backfilled: true` +
`data.binding_source`. seq, ts, every other byte of every other row —
untouched, byte-for-byte. This is NOT a general precedent for editing
history: supersede-append is wrong here because no reader dedups meeting
events newest-wins by source_ref, so a corrective duplicate double-counts
in every windowed computation (and the idempotency contract forbids a
second `meeting` event per source_ref outright).

CONCURRENCY — the whole read-modify-write is held under
`writer_lock.events_writer_lock`, the SAME OS byte-range lock every gated
append takes, and the plan is re-derived inside it. This is not belt-and-
braces: a rewrite is a truncating write, so an append landing between the
read and the write is silently DESTROYED (the write puts back the file as
it was before the append). A client runs this at promote, which is exactly
when `maintenance` / `morning-brief` / `past-meetings` are firing. Same
defect class as BUG_2026-08-10_substrate-append-on-stale-mount-write-lost.
The rewrite itself goes through `atomic_write.atomic_write_text` (temp
sibling + fsync + os.replace), so a crash or a full disk mid-write cannot
truncate the canonical ledger — the `_backups/` copy makes that
recoverable, the atomic replace makes it impossible.

IDEMPOTENT AND HAND-BACKFILL SAFE: bound rows are skipped (a workspace the
client already repaired by hand — the reporting client did — plans zero
rows), and re-running after apply plans zero rows.

Run (from the plugin root, workspace path required):
    PYTHONUTF8=1 python shared/scripts/backfill_meeting_binding.py <workspace_root>          # dry-run report
    PYTHONUTF8=1 python shared/scripts/backfill_meeting_binding.py <workspace_root> --apply  # archive + write
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

try:
    from event_refs import attendee_emails_of, email_person_index, meeting_person_ids
except ImportError:  # direct-path import (tests, bash one-liners)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from event_refs import attendee_emails_of, email_person_index, meeting_person_ids

try:
    from atomic_write import atomic_write_text
    from writer_lock import events_writer_lock
except ImportError:  # direct-path import (tests, bash one-liners)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from atomic_write import atomic_write_text
    from writer_lock import events_writer_lock

_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ']+")


def _load_entities(workspace_root) -> dict:
    p = Path(workspace_root) / "_hq" / "data" / "entities.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _people(entities: dict) -> list:
    try:
        from entities_io import entities_collection
        return entities_collection(entities, "people")
    except Exception:
        return entities.get("people") or []


def name_person_index(entities: dict) -> Dict[str, Optional[str]]:
    """{lowered display-name token/full-name: person_id | None}. None marks an
    AMBIGUOUS name (shared by 2+ people) — a lookup hitting None must not
    bind. Both full display names ("sam stone") and single-token first names
    ("sam") are indexed; ambiguity is tracked per key."""
    idx: Dict[str, Optional[str]] = {}

    def _put(key: str, pid: str) -> None:
        key = key.strip().lower()
        if not key:
            return
        if key in idx and idx[key] != pid:
            idx[key] = None  # shared by 2+ people → ambiguous, never binds
        else:
            idx.setdefault(key, pid)

    try:
        from people_writer import get_person_display_names
        getter = get_person_display_names
    except Exception:
        def getter(p):
            out = []
            if isinstance(p.get("canonical_name"), str):
                out.append(p["canonical_name"])
            for k in ("aliases", "nicknames"):
                out.extend(x for x in (p.get(k) or []) if isinstance(x, str))
            return out

    for p in _people(entities):
        pid = p.get("id")
        if not (isinstance(pid, str) and pid.startswith("person_")):
            continue
        for name in getter(p):
            name = str(name or "").strip()
            if not name:
                continue
            _put(name, pid)
            first = name.split()[0]
            if len(first) >= 3:  # "Jo" is a coin-flip; 3+ chars only
                _put(first, pid)
    return idx


def resolve_title(title: str, name_idx: Dict[str, Optional[str]]) -> List[str]:
    """person_ids a meeting title unambiguously names. Full-name phrases are
    tried first (2- and 3-token windows), then single tokens. Ambiguous keys
    (None in the index) never bind."""
    out: List[str] = []
    tokens = [t.lower() for t in _WORD_RE.findall(title or "")]
    claimed: set = set()
    for size in (3, 2):
        for i in range(len(tokens) - size + 1):
            if any(j in claimed for j in range(i, i + size)):
                continue
            pid = name_idx.get(" ".join(tokens[i:i + size]))
            if pid:
                if pid not in out:
                    out.append(pid)
                claimed.update(range(i, i + size))
    for i, tok in enumerate(tokens):
        if i in claimed:
            continue
        pid = name_idx.get(tok)
        if pid and pid not in out:
            out.append(pid)
    return out


def _is_unbound(ev: dict) -> bool:
    """Unbound for PLANNING purposes = no resolved person ids in any variant.
    A row carrying only emails/external names still plans: several readers
    call the normalizer without an email index, so materializing the ids is
    what makes ALL of them whole (the audit's looser `bound` definition is a
    different question — "is there anything to repair FROM")."""
    if ev.get("type") != "meeting":
        return False
    return not meeting_person_ids(ev)


def _shard_files(workspace_root) -> List[Path]:
    main = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    try:
        from events_io import shard_paths
        return [p for p in shard_paths(main) if p.exists()]
    except Exception:
        return [main] if main.exists() else []


def plan_backfill(workspace_root) -> dict:
    """Dry plan. Returns {rows: [{file, line, seq, source_ref, title,
    person_ids, binding_source}], unresolved: [{seq, title, reason}],
    scanned, unbound}. Never writes."""
    entities = _load_entities(workspace_root)
    email_idx = email_person_index(entities)
    name_idx = name_person_index(entities)
    rows: List[dict] = []
    unresolved: List[dict] = []
    scanned = unbound = 0
    for path in _shard_files(workspace_root):
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines()):
            s = line.strip()
            if not s:
                continue
            try:
                ev = json.loads(s)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict) or ev.get("type") != "meeting":
                continue
            scanned += 1
            if not _is_unbound(ev):
                continue
            unbound += 1
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            emails = attendee_emails_of(ev)
            pids = sorted({email_idx[e] for e in emails if email_idx.get(e)})
            source = "emails"
            if not pids:
                pids = resolve_title(str(d.get("title") or ""), name_idx)
                source = "title"
            if pids:
                rows.append({
                    "file": path.name, "line": lineno, "seq": ev.get("seq"),
                    "source_ref": d.get("source_ref") or "",
                    "title": d.get("title") or "",
                    "person_ids": pids, "binding_source": source,
                })
            else:
                unresolved.append({
                    "seq": ev.get("seq"), "title": d.get("title") or "",
                    "reason": ("title names nobody unambiguously"
                               if d.get("title") else "no title, no emails"),
                })
    return {"rows": rows, "unresolved": unresolved,
            "scanned": scanned, "unbound": unbound}


def apply_backfill(workspace_root, plan: Optional[dict] = None) -> dict:
    """Archive touched shards, then fill the planned rows in place. Only an
    empty/absent top-level `person_ids` is written, plus the
    `data.binding_backfilled` / `data.binding_source` stamps — nothing else
    on the row, nothing at all on any other row. Returns the plan +
    {applied, backups}.

    Held under the events writer lock for the WHOLE read-modify-write, and
    the plan is re-derived inside it (see the module docstring). A
    caller-supplied `plan` is advisory only — it is what the CLI already
    printed, never what gets written; line indices computed outside the lock
    can be invalidated by a concurrent append or a shard rotation before the
    write lands. Raises TimeoutError rather than writing unlocked if another
    writer holds the lock past the timeout.
    """
    if not _shard_files(workspace_root):
        # Nothing to rewrite — and acquiring the lock would CREATE
        # `<root>/_hq/data/.writer.lock`, fabricating a substrate tree at a
        # mistyped path. That phantom-tree failure mode is this very bug's
        # own (the first bug_received write landed in a path Python resolved
        # into a tree that did not exist, and never reached the ledger);
        # taking the lock must not add a second way to do it.
        return {"rows": [], "unresolved": [], "scanned": 0, "unbound": 0,
                "applied": 0, "backups": []}
    with events_writer_lock(workspace_root, holder="backfill_meeting_binding"):
        return _apply_locked(workspace_root)


def _apply_locked(workspace_root) -> dict:
    """The critical section of `apply_backfill`. Callers MUST already hold
    `writer_lock.events_writer_lock` — this function truncates and rewrites
    events.jsonl, so running it unlocked silently destroys any append that
    lands between its read and its write."""
    plan = plan_backfill(workspace_root)
    by_file: Dict[str, Dict[int, dict]] = {}
    for row in plan["rows"]:
        by_file.setdefault(row["file"], {})[row["line"]] = row
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    backups: List[str] = []
    applied = 0
    data_dir = Path(workspace_root) / "_hq" / "data"
    backup_dir = data_dir / "_backups"
    for path in _shard_files(workspace_root):
        rows = by_file.get(path.name)
        if not rows:
            continue
        backup_dir.mkdir(parents=True, exist_ok=True)
        suffix = "" if path.name == "events.jsonl" else f".{path.stem}"
        bak = backup_dir / f"events_pre_bug8244_{stamp}{suffix}.jsonl"
        if not bak.exists():  # first apply of the day wins; never clobber
            shutil.copy2(path, bak)
        backups.append(str(bak))
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for lineno, row in rows.items():
            try:
                ev = json.loads(lines[lineno])
            except (IndexError, json.JSONDecodeError):
                continue
            if not _is_unbound(ev):  # re-check: idempotent under re-runs
                continue
            if ev.get("person_ids"):
                continue  # never touch a non-empty binding
            ev["person_ids"] = row["person_ids"]
            d = ev.setdefault("data", {})
            d["binding_backfilled"] = True
            d["binding_source"] = row["binding_source"]
            lines[lineno] = json.dumps(ev, ensure_ascii=False)
            applied += 1
        atomic_write_text(path, "\n".join(lines) + "\n", encoding="utf-8")
    plan["applied"] = applied
    plan["backups"] = backups
    return plan


def main(argv: List[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print("usage: backfill_meeting_binding.py <workspace_root> [--apply]")
        return 2
    ws = args[0]
    apply = "--apply" in argv
    plan = plan_backfill(ws)
    print(f"meeting events scanned: {plan['scanned']}  "
          f"unbound: {plan['unbound']}  "
          f"resolvable: {len(plan['rows'])}  "
          f"unresolved: {len(plan['unresolved'])}")
    for row in plan["rows"]:
        print(f"  seq {row['seq']}: \"{row['title']}\" -> "
              f"{','.join(row['person_ids'])} ({row['binding_source']})")
    for u in plan["unresolved"]:
        print(f"  seq {u['seq']}: UNRESOLVED — {u['reason']} (\"{u['title']}\")")
    if not apply:
        print("dry-run only — re-run with --apply to archive + write")
        return 0
    result = apply_backfill(ws, plan)
    print(f"applied: {result['applied']}  backups: {result['backups']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
