#!/usr/bin/env python3
"""
One-time backfill: attach historical unattributed people to their orgs.

Run after the v3.13.0 person-record migration. Walks every person record with
no `primary_org_id` and no `affiliation_ids`, gathers their work-domain emails
+ any historical `org_hint` from past `person_pending_review` events, and
calls `attribute_person_to_org` to attach them.

Behavior matches apply-choices Step 3b — auto-attach on strong signal,
silently skip on no signal. Free-mail domains (gmail.com etc.) never trigger.

DRY-RUN by default. Pass `--apply` to actually write.

In M's workspace as of the 2026-05-20 audit: 34 of 83 people are unattributed.
Per #21 handoff: 31 of 80 were unattributed. The difference is the 3 new
records added during today's session (person_081 - person_083). Expected
attach rate: ~half (the rest don't have work-domain emails or org_hint
signal, so they'll stay unattached).

Usage:

    python3 shared/scripts/backfill_org_attribution_v3_13_0.py <workspace_root> [--apply]

Arguments:
  workspace_root  Path to the workspace (the directory that contains _hq/).
  --apply         Actually write. Default is DRY-RUN — reports what would change.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from people_writer import get_person_emails  # noqa: E402
from org_writer import (  # noqa: E402
    attribute_person_to_org,
    _extract_domain,
    _is_work_domain,
)


def _load_entities(workspace_root: Path) -> dict:
    return json.loads((workspace_root / "_hq" / "data" / "entities.json").read_text("utf-8"))


def _index_org_hints_by_person(events_path: Path) -> dict[str, list[str]]:
    """Walk events.jsonl and collect org_hint strings per person.

    `person_pending_review` events from cr-upcoming-meetings / cr-past-meetings
    carry `data.org_hint` AND a `data.email` (or `data.name`). The chain from
    those to the eventual created person_id is via apply-choices, which writes
    a `person_added` event with both the org_hint AND the new person_id.

    For backfill simplicity, we collect by EMAIL (since person_pending_review
    keys on email) and then match emails to created people. Fallback: collect
    by canonical_name if email isn't present.
    """
    hints_by_email: dict[str, list[str]] = {}
    hints_by_name: dict[str, list[str]] = {}
    if not events_path.exists():
        return {}

    for line in events_path.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") not in ("person_pending_review", "person_proposal", "person_added"):
            continue
        data = ev.get("data") or {}
        if not isinstance(data, dict):
            continue
        hint = data.get("org_hint")
        if not isinstance(hint, str) or not hint.strip():
            continue
        email = data.get("email")
        if isinstance(email, str) and email.strip():
            hints_by_email.setdefault(email.strip().lower(), []).append(hint)
        name = data.get("name") or data.get("canonical_name")
        if isinstance(name, str) and name.strip():
            hints_by_name.setdefault(name.strip().lower(), []).append(hint)

    # Combine into a single dict — we'll look up by both keys at attribution time
    return {"by_email": hints_by_email, "by_name": hints_by_name}


def _find_hint_for_person(person: dict, hints_index: dict) -> str | None:
    """Find the most-recent org_hint that referenced this person."""
    by_email = hints_index.get("by_email", {}) if isinstance(hints_index, dict) else {}
    by_name = hints_index.get("by_name", {}) if isinstance(hints_index, dict) else {}

    for e in get_person_emails(person):
        candidates = by_email.get(e.strip().lower())
        if candidates:
            return candidates[-1]  # most recent

    canon = (person.get("canonical_name") or "").strip().lower()
    if canon:
        candidates = by_name.get(canon)
        if candidates:
            return candidates[-1]
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    workspace_root = Path(argv[1]).resolve()
    apply_mode = "--apply" in argv[2:]

    entities = _load_entities(workspace_root)
    people = entities.get("people", [])
    events_path = workspace_root / "_hq" / "data" / "events.jsonl"

    print(f"Workspace: {workspace_root}")
    print(f"Mode:      {'APPLY' if apply_mode else 'DRY-RUN'}")
    print()

    print("Indexing historical org_hint events ...")
    hints_index = _index_org_hints_by_person(events_path)
    n_email_hints = sum(len(v) for v in hints_index.get("by_email", {}).values())
    n_name_hints = sum(len(v) for v in hints_index.get("by_name", {}).values())
    print(f"  found {n_email_hints} org_hint(s) keyed by email, {n_name_hints} by name")
    print()

    # Identify unattributed people
    unattributed = []
    for p in people:
        if not isinstance(p, dict):
            continue
        if p.get("primary_org_id"):
            continue
        if p.get("affiliation_ids"):
            continue
        if p.get("org_id"):  # legacy field — counts as attributed
            continue
        unattributed.append(p)

    print(f"Unattributed people: {len(unattributed)} of {len(people)}")
    print()

    if not unattributed:
        print("Nothing to backfill.")
        return 0

    # Walk each unattributed person and attempt attribution
    attached_count = 0
    skipped_count = 0
    for p in unattributed:
        pid = p.get("id")
        name = p.get("canonical_name", "(no name)")

        # Gather work-domains from emails
        work_domains = []
        for e in get_person_emails(p):
            domain = _extract_domain(e)
            if domain and _is_work_domain(domain):
                work_domains.append(domain)

        # Find a historical org_hint for this person
        hint = _find_hint_for_person(p, hints_index)

        if not work_domains and not hint:
            print(f"  SKIP {pid} ({name}) — no work-domain email, no hint")
            skipped_count += 1
            continue

        if not apply_mode:
            # Bug #100 — run the SAME matcher apply uses, just without writing, so
            # the preview can't over-promise. Previously this branch counted anyone
            # with a work-domain email; apply only attaches on an actual org match,
            # so the dry-run claimed 11 and apply wrote 0. dry_run=True returns the
            # identical (org_record_or_None, reason) a real apply would.
            try:
                org_record, reason = attribute_person_to_org(
                    workspace_root, pid,
                    work_domains=work_domains, org_hint=hint,
                    source_skill="backfill_org_attribution_v3_13_0",
                    dry_run=True,
                )
            except Exception as exc:
                print(f"  SKIP {pid} ({name}) — preview error: {exc}")
                skipped_count += 1
                continue
            if org_record is None:
                print(f"  SKIP {pid} ({name}) — {reason}")
                skipped_count += 1
            else:
                print(f"  WOULD attribute {pid} ({name}) — {reason}")
                attached_count += 1
            continue

        # Apply mode — call the helper
        try:
            org_record, reason = attribute_person_to_org(
                workspace_root,
                pid,
                work_domains=work_domains,
                org_hint=hint,
                source_skill="backfill_org_attribution_v3_13_0",
            )
        except Exception as exc:
            print(f"  ERROR {pid} ({name}): {exc}")
            skipped_count += 1
            continue

        if org_record is None:
            print(f"  SKIP {pid} ({name}) — {reason}")
            skipped_count += 1
        else:
            print(f"  ATTACHED {pid} ({name}) → {org_record.get('canonical_name')} ({reason})")
            attached_count += 1

    print()
    print(f"Result: {attached_count} {'would be ' if not apply_mode else ''}attributed, {skipped_count} skipped")
    if not apply_mode:
        print("\nRun with --apply to actually write.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
