#!/usr/bin/env python3
"""
One-time person-record migration for v3.13.0 Option B schema evolution.

Run once per workspace to align legacy person records with the v3.13.0 schema.
Idempotent: re-running on a migrated workspace is a no-op.

What it does (in order, per record):

  1. Apply _normalize_legacy_keys (people_writer.py) to:
     - Combine first_name + last_name into canonical_name (if missing)
     - Rename first_contact → first_seen, last_contact → last_interaction
     - Drop forbidden provenance keys (inferred_from, pending_review,
       created_at, created_by, is_primary_user, enriched_*, low_signal,
       confidence, first_seen_source, normalized_name, related_people,
       relationship_type, tier, company)

  2. Backfill missing first_seen:
     - Scan events.jsonl for the earliest event referencing this person
       (via top-level person_id field, or data.owner_id matching this
       record's id)
     - Use the event's timestamp as first_seen (date portion only)
     - If no event found, fall back to the workspace baseline date
       (FALLBACK_FIRST_SEEN constant — default "2026-04-01", before any
       Command Room substrate writes in M's workspace)

  3. Re-validate every record via _validate_person.
     Fail loudly if any record still doesn't pass after migration.

  4. Atomic-write the result via atomic_write_json.
     A backup is written to _hq/data/_backups/ with a pre-migration timestamp.

Usage:

    python3 shared/scripts/migrate_persons_v3_13_0.py <workspace_root> [--dry-run]

Arguments:
  workspace_root  Path to the workspace (the directory that contains _hq/).
  --dry-run       Don't write — just report what WOULD change.

Exit code:
  0 = success (or dry-run clean)
  1 = at least one record still fails validation after migration
  2 = bad invocation / I/O failure
"""
from __future__ import annotations

import datetime
import json
import shutil
import sys
from pathlib import Path

# Bring people_writer's helpers in scope
sys.path.insert(0, str(Path(__file__).resolve().parent))
from event_time import event_time  # noqa: E402
from people_writer import (  # noqa: E402
    _normalize_legacy_keys,
    _validate_person,
    ALLOWED_PERSON_FIELDS,
    REQUIRED_PERSON_FIELDS,
)
from atomic_write import atomic_write_json  # noqa: E402

# Workspace baseline — earliest reasonable first_seen if no event references
# the person. April 1 2026 is before any of M's events.jsonl entries.
FALLBACK_FIRST_SEEN = "2026-04-01"


def _entities_path(workspace_root: Path) -> Path:
    return workspace_root / "_hq" / "data" / "entities.json"


def _events_path(workspace_root: Path) -> Path:
    return workspace_root / "_hq" / "data" / "events.jsonl"


def _backups_dir(workspace_root: Path) -> Path:
    return workspace_root / "_hq" / "data" / "_backups"


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _now_compact() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H%M")


def _build_first_seen_index(events_path: Path) -> dict[str, str]:
    """Walk events.jsonl once and return {person_id: earliest_date_iso}.

    Tolerant of malformed lines (parse failures skipped). Looks for person_id
    references in:
      - top-level `person_id` field
      - `data.owner_id` (commitment events)
      - `data.person_id` (proposals + similar)
      - `data.assignee_id`, `data.recipient_id` (extensions)
    """
    earliest: dict[str, str] = {}
    if not events_path.exists():
        return earliest

    try:
        text = events_path.read_text(encoding="utf-8")
    except OSError:
        return earliest

    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue

        # Collect any person_id reference
        candidates: list[str] = []
        for key in ("person_id", "owner_id", "assignee_id", "recipient_id"):
            v = ev.get(key)
            if isinstance(v, str) and v.startswith("person_"):
                candidates.append(v)
        data_block = ev.get("data") or {}
        if isinstance(data_block, dict):
            for key in ("person_id", "owner_id", "assignee_id", "recipient_id"):
                v = data_block.get(key)
                if isinstance(v, str) and v.startswith("person_"):
                    candidates.append(v)

        if not candidates:
            continue

        # Pull a date from the event (canonical priority: ts → timestamp → date).
        ts = event_time(ev)
        if not isinstance(ts, str) or not ts:
            continue
        date_part = ts[:10]  # ISO date portion
        # Validate it's a real date
        try:
            datetime.date.fromisoformat(date_part)
        except ValueError:
            continue

        for pid in candidates:
            prev = earliest.get(pid)
            if prev is None or date_part < prev:
                earliest[pid] = date_part

    return earliest


def _migrate_record(p: dict, first_seen_index: dict[str, str]) -> tuple[dict, list[str]]:
    """Return (migrated_record, list_of_changes_applied).

    Changes are described in plain English for the report.
    """
    changes: list[str] = []
    original_keys = set(p.keys())

    cleaned = _normalize_legacy_keys(p)

    # What did the normalize step do?
    removed = original_keys - set(cleaned.keys())
    added = set(cleaned.keys()) - original_keys
    for k in sorted(removed):
        # If it was renamed (in LEGACY_KEY_RENAMES), the new key will be in added
        # — but the report should still mention the rename source-side.
        changes.append(f"dropped/renamed legacy key {k!r}")
    for k in sorted(added):
        if k not in original_keys:
            changes.append(f"added canonical key {k!r} (from legacy field)")

    # Backfill first_seen if missing
    if "first_seen" not in cleaned:
        pid = cleaned.get("id", "")
        inferred = first_seen_index.get(pid)
        if inferred:
            cleaned["first_seen"] = inferred
            changes.append(f"backfilled first_seen = {inferred} (from earliest event)")
        else:
            cleaned["first_seen"] = FALLBACK_FIRST_SEEN
            changes.append(
                f"backfilled first_seen = {FALLBACK_FIRST_SEEN} (workspace baseline — no event references found)"
            )

    return cleaned, changes


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    workspace_root = Path(argv[1]).resolve()
    dry_run = "--dry-run" in argv[2:]

    entities_path = _entities_path(workspace_root)
    events_path = _events_path(workspace_root)
    backups = _backups_dir(workspace_root)

    if not entities_path.exists():
        print(f"ABORT: entities.json not found at {entities_path}", file=sys.stderr)
        return 2

    print(f"Workspace: {workspace_root}")
    print(f"Entities:  {entities_path}")
    print(f"Events:    {events_path} ({'exists' if events_path.exists() else 'missing'})")
    print(f"Mode:      {'DRY-RUN' if dry_run else 'WRITE'}")
    print()

    try:
        data = json.loads(entities_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ABORT: entities.json fails to parse: {e}", file=sys.stderr)
        return 2

    people = data.get("people", [])
    if not isinstance(people, list):
        print("ABORT: entities.json `people` key is not a list", file=sys.stderr)
        return 2

    print(f"Loading first-seen index from events.jsonl ...")
    first_seen_index = _build_first_seen_index(events_path)
    print(f"  found event-references for {len(first_seen_index)} people")
    print()

    # Migrate each record
    migrated_people: list[dict] = []
    changes_by_pid: dict[str, list[str]] = {}
    for p in people:
        if not isinstance(p, dict):
            print(f"WARN: person record is not a dict, skipping: {type(p).__name__}")
            migrated_people.append(p)
            continue
        new_record, changes = _migrate_record(p, first_seen_index)
        migrated_people.append(new_record)
        if changes:
            changes_by_pid[new_record.get("id", "?")] = changes

    # Re-validate every record. Track failures.
    failures: list[tuple[str, str]] = []
    for p in migrated_people:
        if not isinstance(p, dict):
            continue
        try:
            _validate_person(p)
        except ValueError as e:
            failures.append((p.get("id", "?"), str(e)))

    # Report
    print(f"Migrated {len(changes_by_pid)} of {len(people)} records:")
    for pid, changes in changes_by_pid.items():
        print(f"  {pid}")
        for c in changes:
            print(f"    - {c}")
    print()

    if failures:
        # v3.13.6+ — friendly abort (per CONTRACT Rule 4, "FAIL" is forbidden
        # in user-visible output). The script aborts cleanly with no write
        # so the user can fix the offending records and re-run.
        print(f"Some records can't be migrated automatically — they're missing")
        print(f"fields the new schema needs ({len(failures)} record(s)):")
        for pid, err in failures[:10]:
            first_lines = err.split("\n")[:5]
            print(f"  - {pid}: {first_lines[0]}")
            for line in first_lines[1:]:
                print(f"      {line}")
            print(f"      → Add the missing fields to this person record in")
            print(f"        _hq/data/entities.json, then re-run the migration.")
        if len(failures) > 10:
            print(f"  ... and {len(failures) - 10} more")
        print()
        print(f"ABORT — no changes written. Fix the records listed above, then re-run.")
        return 1

    print(f"OK — all {len(migrated_people)} records validate")
    print()

    if dry_run:
        print("Dry-run mode — no files written")
        return 0

    # v3.13.6+ — short-circuit if zero records changed. Pre-v3.13.6 wrote
    # a backup + bumped version on every run; repeated Update clicks
    # accumulated backup-file clutter for no real change.
    if not changes_by_pid:
        print("Nothing to migrate — every record is already at the v3.13.0 shape.")
        print("No changes written.")
        return 0

    # Write a timestamped backup
    backups.mkdir(parents=True, exist_ok=True)
    backup_path = backups / f"entities.json.{_now_compact()}.pre-v3.13.0-migrate.bak"
    shutil.copy2(entities_path, backup_path)
    print(f"Backup written: {backup_path}")

    # Atomic-write the migrated data
    data["people"] = migrated_people
    data["last_updated"] = datetime.datetime.now().replace(microsecond=0).isoformat()
    data["last_writer"] = "migrate_persons_v3_13_0"
    # bump version so concurrent writers see this
    data["version"] = int(data.get("version", 0)) + 1
    atomic_write_json(entities_path, data)
    print(f"Migrated entities.json written. New version: {data['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
