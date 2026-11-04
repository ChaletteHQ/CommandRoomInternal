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

  2b. Fold a personal layer's `reports_to` into `reports_to_id` (cr1#132,
     owner ruling 2026-10-26). `reports_to_id` holds a person id. A
     `reports_to` value that is an id on file is taken as it is; any other
     value is resolved through people_writer.find_existing_person, which only
     answers on an unambiguous match. Then:
     - `reports_to_id` empty and the value resolves: the id moves across and
       the move is listed in the report.
     - `reports_to_id` already names the same person: `reports_to` goes.
     - `reports_to_id` names someone else, or the value does not resolve:
       `reports_to_id` is kept as it is, the old value moves into `notes` as
       one line ("Reports to (from an older field, not matched): ..."),
       appended after any existing notes and never twice (notes that are not
       text are first rewritten as their JSON text, nothing removed), the rest of the
       record is migrated, and the report lists it under "needs a look",
       instead of failing.
     `is_user` and `pronouns` are schema fields since cr1#132 and are kept.

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
    MultipleCandidatesError,
    PERSON_ID_RE,
    REQUIRED_PERSON_FIELDS,
    find_existing_person,
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


def _resolve_reports_to(value, workspace_root: Path | None,
                        known_ids: set[str]) -> str | None:
    """The person id a `reports_to` value names, or None when it names nobody
    for certain. An id on file is taken as it is. Anything else goes through
    find_existing_person, which answers only on an unambiguous match (an exact
    email, or a unique full canonical name) and refuses a one-word or
    alias-only hit; an ambiguous answer is treated as unresolved."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if PERSON_ID_RE.match(text):
        return text if text in known_ids else None
    if workspace_root is None:
        return None
    try:
        if "@" in text:
            hit = find_existing_person(workspace_root, email=text)
        else:
            hit = find_existing_person(workspace_root, name=text)
    except MultipleCandidatesError:
        return None
    if isinstance(hit, dict) and hit.get("id") in known_ids:
        return hit["id"]
    return None


#: The line a conflicting or unresolved `reports_to` leaves in the record's
#: `notes`. The value must never leave the live record: the backups folder
#: keeps only three copies and the update bridge does not relay this
#: script's report to the owner (review finding on cr1#132).
REPORTS_TO_NOTE_PREFIX = "Reports to (from an older field, not matched): "


def _keep_in_notes(record: dict, value) -> str:
    """Append the unmatched `reports_to` value to `notes` as one plain line,
    after any existing notes, and only if that exact line is not there yet,
    so a re-run never appends it twice. Notes that are not text (the
    validator does not type-check them) are rewritten as their JSON text
    first, so nothing is lost and the record ends valid (`notes` is
    string | null). Returns the phrase the report uses for where the value
    went, saying exactly what happened."""
    shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    line = REPORTS_TO_NOTE_PREFIX + shown.strip()
    notes = record.get("notes")
    if notes is None or (isinstance(notes, str) and not notes.strip()):
        record["notes"] = line
        return "kept the old value in notes"
    if isinstance(notes, str):
        if line in notes.splitlines():
            return "kept the old value in notes, where it already was"
        record["notes"] = notes.rstrip("\n") + "\n" + line
        return "kept the old value in notes, after the existing notes"
    record["notes"] = json.dumps(notes, ensure_ascii=False) + "\n" + line
    return ("kept the old value in notes; the existing notes were not text, "
            "so they were rewritten as text first, with nothing removed")


def _fold_reports_to(record: dict, workspace_root: Path | None,
                     known_ids: set[str]) -> tuple[list[str], list[str]]:
    """Fold a personal layer's `reports_to` into `reports_to_id`, in place
    (cr1#132, owner ruling 2026-10-26). Returns (changes, notices): changes
    go in the per-record report; notices are the conflicts and unresolved
    values the owner should look at. A conflicting or unresolved value is
    kept on the record as a line in `notes`. Never raises on the data."""
    if "reports_to" not in record:
        return [], []
    value = record.pop("reports_to")
    current = record.get("reports_to_id")
    if value is None or (isinstance(value, str) and not value.strip()):
        return ["dropped empty 'reports_to'"], []
    resolved = _resolve_reports_to(value, workspace_root, known_ids)
    if not current:
        if resolved:
            record["reports_to_id"] = resolved
            return [f"moved 'reports_to' {value!r} into reports_to_id = {resolved}"], []
        kept = _keep_in_notes(record, value)
        return (
            [f"moved 'reports_to' {value!r} out of its old field: it does not "
             f"name one person on file; {kept}"],
            [f"unresolved: 'reports_to' was {value!r}, which does not name one "
             f"person on file, so reports_to_id was left empty and {kept}. "
             "Set reports_to_id by hand if you know who this person reports to."],
        )
    if resolved == current:
        return [f"dropped 'reports_to' {value!r}: same person as reports_to_id = {current}"], []
    kept = _keep_in_notes(record, value)
    return (
        [f"moved 'reports_to' {value!r} out of its old field: kept "
         f"reports_to_id = {current}; {kept}"],
        [f"conflict: 'reports_to' was {value!r} but reports_to_id is {current}. "
         f"Kept reports_to_id = {current} and {kept}. "
         "Change reports_to_id by hand if 'reports_to' was right."],
    )


def _migrate_record(p: dict, first_seen_index: dict[str, str], *,
                    workspace_root: Path | None = None,
                    known_ids: set[str] | None = None,
                    notices: list[str] | None = None) -> tuple[dict, list[str]]:
    """Return (migrated_record, list_of_changes_applied).

    Changes are described in plain English for the report. Conflicts and
    unresolved values from the `reports_to` fold are appended to `notices`
    when a list is passed.
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

    # cr1#132: a personal layer's `reports_to` folds into `reports_to_id`.
    fold_changes, fold_notices = _fold_reports_to(
        cleaned, workspace_root, known_ids or set())
    changes.extend(fold_changes)
    if notices is not None:
        notices.extend(f"{cleaned.get('id', '?')}: {n}" for n in fold_notices)

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
    notices: list[str] = []
    known_ids = {p.get("id") for p in people
                 if isinstance(p, dict) and isinstance(p.get("id"), str)}
    for p in people:
        if not isinstance(p, dict):
            print(f"WARN: person record is not a dict, skipping: {type(p).__name__}")
            migrated_people.append(p)
            continue
        new_record, changes = _migrate_record(
            p, first_seen_index, workspace_root=workspace_root,
            known_ids=known_ids, notices=notices)
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

    # cr1#132: who-reports-to-whom values the fold could not settle. Reported,
    # never a reason to stop: the records are otherwise migrated.
    if notices:
        print(f"Needs a look, who someone reports to ({len(notices)}):")
        for n in notices:
            print(f"  - {n}")
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
