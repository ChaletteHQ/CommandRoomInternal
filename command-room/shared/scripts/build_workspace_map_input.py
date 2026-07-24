#!/usr/bin/env python3
"""
Project workspace data (entities.json / events.jsonl / aliases.json) into the
JSON shape the Workspace Map artifact template expects, then emit a single
input.json that render_artifact.py can substitute into the template.

This script encapsulates ALL projection logic. The model now just runs:

    python build_workspace_map_input.py --workspace-root <path> --output input.json
    python render_artifact.py --template <path> --input input.json --output rendered.html

Output JSON keys (all pre-formatted as strings for the renderer):
    CEO_DISPLAY_NAME    HTML-safe first name
    LAST_BUILT          ISO-friendly snapshot timestamp string
    ORGS_JSON           JSON-encoded array of orgs (with optional children sub-orgs)
    PROJECTS_JSON       JSON-encoded array of projects
    PEOPLE_JSON         JSON-encoded array of people
    THREADS_JSON        JSON-encoded array of open commitment threads (derived from events.jsonl)
    COMMITMENTS_JSON    JSON-encoded array of all open commitments (for the v2.7.13 Commitments tab)
    OWES_BY_ORG_JSON    (v2.14.11+) JSON-encoded {org_id: {youOwe, theyOwe}} aggregating
                        commitments across all projects under each org. Used by
                        Workspace Map to render commitment counts on org cards.

Schema handling (v2.7.13+): the script accepts BOTH known entities.json schemas
in production:

  schema A — fresh-onboarding (Sam-class workspace):
    org.name, org.description
    person.name, person.is_user
    entities.projects[]                           (top-level array)
    project.name, project.stage, project.last_touched, project.next_action

  schema B — v2.x-ingested (M-class workspace, migrated from v1.x):
    org.canonical_name, org.notes
    person.canonical_name, person.is_primary_user, person.last_interaction
    entities.threads[]                            (top-level array)
    thread.canonical_name, thread.status, thread.last_activity, thread.notes

The defensive readers below try schema A first, then fall back to schema B.
Field names that don't appear in either schema are tolerated (returned as
empty/default), so future schema additions don't break the projector.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from build_dcc_input import _format_last_built  # noqa: E402
from cru_match import _commitment_field, _commitment_confidence  # noqa: E402

# ----- defensive field readers (handle both schemas A and B) -----


def _name(obj: dict[str, Any]) -> str:
    """Display name. Tries schema-A 'name' first, then schema-B 'canonical_name', falls back to id."""
    return obj.get("name") or obj.get("canonical_name") or obj.get("id", "")


def _is_primary_user(person: dict[str, Any]) -> bool:
    """Schema A uses 'is_user', schema B uses 'is_primary_user'."""
    return bool(person.get("is_user") or person.get("is_primary_user"))


def _projects_array(entities: dict[str, Any]) -> list[dict[str, Any]]:
    """Top-level projects array: schema A uses 'projects', schema B uses 'threads'."""
    return entities.get("projects") or entities.get("threads") or []


def _org_note(org: dict[str, Any]) -> str:
    """Schema A uses 'description', schema B uses 'notes'."""
    return org.get("description") or org.get("notes") or ""


def _project_status(project: dict[str, Any]) -> str:
    """Schema A uses 'stage' (Capitalized), schema B uses 'status' (lowercase). Always returned lowercase."""
    raw = project.get("stage") or project.get("status") or "active"
    return str(raw).lower()


def _project_last_when(project: dict[str, Any],
                       activity: dict | None = None) -> str | None:
    """Last-touched display source (HYG1 Item 3 — the C3 fossil-reader
    retirement): OBSERVED recency first — the newest event on the thread via
    thread_activity.derive_thread_activity (the caller derives the map ONCE
    per build and passes it) — then the stored record stamp
    (last_touched / last_activity) as the ZERO-EVENT FLOOR only (the
    DATA_CONTRACT carve-out: ingest legitimately stamps it for threads with
    no event history)."""
    if activity is not None:
        act = activity.get(project.get("id"))
        if act is not None:
            return act.ts.isoformat()
    return project.get("last_touched") or project.get("last_activity")


def _project_next(project: dict[str, Any]) -> str:
    """
    Schema A has explicit 'next_action'. Schema B doesn't, so we derive from the
    first sentence of 'notes' as a best-effort.
    """
    if project.get("next_action"):
        return str(project["next_action"]).strip()
    notes = project.get("notes") or ""
    if notes:
        first = str(notes).split(".")[0].strip()
        if len(first) > 140:
            first = first[:137] + "..."
        return first
    return ""


def _project_org_id(project: dict[str, Any]) -> str:
    """Schema-tolerant: schema A and B both use org_id; schema B may use affiliation_id as fallback."""
    return project.get("org_id") or project.get("affiliation_id") or ""


def _person_org_id(person: dict[str, Any]) -> str:
    """Schema-tolerant person->org affiliation. Canonical writers emit
    primary_org_id / affiliation_ids[]; older/flat records use org_id
    (deprecated). Reading org_id alone left the Orgs Map org column blank for
    every canonically-written person (deep-audit 2026-05-29, finding #5)."""
    aff = person.get("affiliation_ids")
    return (
        person.get("primary_org_id")
        or person.get("org_id")
        or (aff[0] if isinstance(aff, list) and aff else "")
        or ""
    )


def _project_key_contact(project: dict[str, Any]) -> str | None:
    """Schema A doesn't currently expose key_contact_id; schema B does. Tolerate both."""
    return project.get("key_contact_id") or project.get("primary_contact_id")


def _person_last_interaction(person: dict[str, Any]) -> str | None:
    """Schema B has 'last_interaction'; schema A may not (returns None)."""
    return person.get("last_interaction")


# ----- helpers -----


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _humanize_age(when: str | None, now: datetime) -> tuple[str, int]:
    """Return ('3d', 72) style tuple. Empty string + 0 if input is missing/unparseable."""
    if not when:
        return ("", 0)
    s = str(when).strip()
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except ValueError:
        return ("", 0)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    delta = now - dt
    hours = max(0, int(delta.total_seconds() // 3600))
    if hours < 1:
        return ("just now", 0)
    if hours < 24:
        return (f"{hours}h", hours)
    days = hours // 24
    if days < 14:
        return (f"{days}d", hours)
    weeks = days // 7
    if weeks < 8:
        return (f"{weeks}w", hours)
    months = days // 30
    return (f"{months}mo", hours)


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# ----- commitment aggregation (v2.7.13: matches actual events.jsonl schema) -----


def _aggregate_commitments(
    events: list[dict[str, Any]],
    user_id: str,
    people_by_id: dict[str, dict[str, Any]],
    projects_by_id: dict[str, dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """
    Walk events.jsonl. Open commitments have type='commitment' and status in ('open','overdue').
    Closing events have type='commitment_resolved' or 'thread_resolved' and reference the commitment id.

    Canonical commitment schema (v2.7.15+):
      {
        "seq": <int>, "ts": "<iso>", "type": "commitment", "source_skill": "<skill>",
        "primary_thread_id": "project_NNN",
        "person_ids": ["person_NNN", ...],
        "data": {
          "owner_id": "person_NNN",          // who owes the deliverable; user_id if "I owe"
          "title": "<short verb-phrase>",
          "due": "<iso date>" | "",
          "status": "open" | "overdue",
          "source_event_seq": <seq of meeting/interaction>,
          "source_ref": "<granola:meeting_id | gmail:msg_id | ...>"
        }
      }

    Legacy flat schema (pre-v2.7.15) is also accepted: top-level project_id / owner /
    title / due / status / timestamp. Both shapes coexist; this aggregator reads
    canonical first, falls back to flat.

    Returns a dict with:
        owes_by_project   : {project_id: {"youOwe": int, "theyOwe": int}}
        owes_by_person    : {person_id: int}      (count of commitments where owner=person_id)
        threads_records   : list of thread records for THREADS_JSON (commitment per thread)
        commitments       : list of all open commitments (richer shape, for COMMITMENTS_JSON tab)
    """
    # First, find all commitments and which ones got resolved.
    open_commitments: list[dict[str, Any]] = []
    resolved_ids: set[str] = set()

    for ev in events:
        et = ev.get("type") or ev.get("event") or ""
        d = ev.get("data") or {}
        if et == "commitment_resolved" or et == "thread_resolved":
            cid = (
                d.get("commitment_id")
                or d.get("thread_id")
                or d.get("id")
                or ev.get("commitment_id")
                or ev.get("thread_id")
                or ev.get("id")
            )
            if cid:
                resolved_ids.add(cid)
        elif et == "commitment":
            # Use the shared shape-aware reader (v3.4.5+) so all 5 known
            # commitment-event shapes are covered. Pre-v3.4.5 this used a
            # narrower local handler that missed the owner_person_id-variant
            # (data.state instead of data.status) — ~42% of M's commitments
            # in his 2026-05-17 audit, plus 7 of Sam's "owed to him" items
            # from the 2026-04-30 working session.
            status = _commitment_field(ev, "status") or "open"
            # Pending-review commitments (shape #5) carry data.pending_review=True
            # and are routed via the Pulse CRU-review surface, not the main
            # commitments list. Filter them out here.
            d = ev.get("data") or {}
            if d.get("pending_review"):
                continue
            if status in ("open", "overdue"):
                open_commitments.append(ev)

    def _cid(ev: dict[str, Any]) -> str:
        d = ev.get("data") or {}
        return d.get("id") or ev.get("id") or f"commitment_seq_{ev.get('seq', '?')}"

    open_commitments = [c for c in open_commitments if _cid(c) not in resolved_ids]

    owes_by_project: dict[str, dict[str, int]] = {}
    owes_by_person: dict[str, int] = {}
    threads_records: list[dict[str, Any]] = []
    commitments_out: list[dict[str, Any]] = []

    for c in open_commitments:
        d = c.get("data") or {}
        pid = (
            c.get("primary_thread_id")
            or d.get("project_id")
            or c.get("project_id")
            or d.get("thread_id")
            or c.get("thread_id")
            or ""
        )
        # All commitment field reads go through _commitment_field (v3.4.5+).
        owner = _commitment_field(c, "owner_id") or ""
        status_val = _commitment_field(c, "status") or "open"
        is_overdue = status_val == "overdue"
        title = (_commitment_field(c, "title") or "").strip()
        due = _commitment_field(c, "due") or ""
        from event_time import event_time
        ts = event_time(c) or d.get("timestamp") or c.get("created_at") or ""

        # Aggregate counts on the owning project
        if pid:
            bucket = owes_by_project.setdefault(pid, {"youOwe": 0, "theyOwe": 0})
            if owner == user_id or not owner:
                bucket["youOwe"] += 1
            else:
                bucket["theyOwe"] += 1

        # Per-person count + thread record (only when owner is not the user)
        if owner and owner != user_id:
            owes_by_person[owner] = owes_by_person.get(owner, 0) + 1
            age_str, age_h = _humanize_age(ts, now)
            # v4.5.2 R1b: "stuck" here is a legacy artifact-template token
            # meaning overdue-by-due-date (the artifact CSS/JS key on it).
            # Any VISIBLE label rendered from it says "overdue" (see
            # orgs-map-artifact renderStuckLine) — never surface "stuck".
            state = "stuck" if is_overdue else "theyowe"
            tone = "stuck" if is_overdue else None
            preview = title + (f" · due {due}" if due else "")
            threads_records.append(
                {
                    "id": _cid(c),
                    "who": owner,
                    "subj": title,
                    "state": state,
                    "age": age_str,
                    "tone": tone,
                    "preview": preview,
                }
            )

        # Rich commitment record for the v2.7.13 Commitments tab
        owner_name = _name(people_by_id[owner]) if owner in people_by_id else ""
        project_name = _name(projects_by_id[pid]) if pid in projects_by_id else ""
        age_str, age_h = _humanize_age(ts, now)
        commitments_out.append(
            {
                "id": _cid(c),
                "title": title or "(untitled commitment)",
                "owner": owner,
                "ownerName": owner_name,
                "project": pid,
                "projectName": project_name,
                "due": due,
                "status": status_val,
                "direction": "youowe" if (owner == user_id or not owner) else "theyowe",
                "tone": "stuck" if is_overdue else None,
                "age": age_str,
                "ageH": age_h,
                "ts": ts,
            }
        )

    # Sort commitments: stuck (overdue) first by age desc, then theyowe by age desc, then youowe
    def _commitment_sort_key(c: dict[str, Any]) -> tuple[int, int]:
        if c["tone"] == "stuck":
            bucket = 0
        elif c["direction"] == "theyowe":
            bucket = 1
        else:
            bucket = 2
        return (bucket, -c["ageH"])

    commitments_out.sort(key=_commitment_sort_key)

    return {
        "owes_by_project": owes_by_project,
        "owes_by_person": owes_by_person,
        "threads_records": threads_records,
        "commitments": commitments_out,
    }


# ----- top-level projections -----


def _project_orgs(entities: dict[str, Any]) -> list[dict[str, Any]]:
    """Top-level orgs (parent_org_id is null) with sub-orgs nested under `children`.

    Also attaches `engagements` (non-ownership edges from entities.engagements[])
    as a sibling of `children` on each shaped org. Engaged orgs are NOT removed
    from the top level — they remain independently navigable (graph, not tree).
    Inactive engagements (is_active: false) are filtered out.
    """
    all_orgs = entities.get("orgs", [])
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for org in all_orgs:
        by_parent.setdefault(org.get("parent_org_id"), []).append(org)

    org_name_by_id = {o["id"]: _name(o) for o in all_orgs}

    engagements_by_from: dict[str, list[dict[str, Any]]] = {}
    for eng in entities.get("engagements", []) or []:
        if eng.get("is_active") is False:
            continue
        from_id = eng.get("from_org_id")
        if not from_id:
            continue
        engagements_by_from.setdefault(from_id, []).append(eng)

    def shape(o: dict[str, Any]) -> dict[str, Any]:
        tags: list[str] = []
        scope = o.get("scope")
        rel = o.get("relationship_type")
        if scope:
            tags.append(scope)
        if rel and rel != scope:
            tags.append(rel)
        if o.get("is_primary_focus"):
            tags.append("primary")
        # v2.10.3 tier — drives renderer visual treatment
        tier = o.get("tier")
        if not tier:
            # Back-compat for orgs without explicit tier set:
            # primary_focus → primary; client/partner/advisor/board/portfolio → secondary;
            # vendor/prospect/service_provider → external; archived → passive; else → secondary
            status = (o.get("status") or "active").lower()
            if status == "archived":
                tier = "passive"
            elif o.get("is_primary_focus"):
                tier = "primary"
            elif rel in {"vendor", "prospect", "service_provider"}:
                tier = "external"
            else:
                tier = "secondary"
        shaped: dict[str, Any] = {
            "id": o["id"],
            "name": _name(o),
            "tags": tags,
            "tier": tier,
            "relationship_type": rel or "",
            "note": _org_note(o),
        }
        engs = engagements_by_from.get(o["id"], [])
        if engs:
            shaped["engagements"] = [
                {
                    "id": e.get("id", ""),
                    "to_org_id": e.get("to_org_id", "") or "",
                    "name": org_name_by_id.get(e.get("to_org_id", ""), e.get("to_org_id", "")),
                    "kind": e.get("kind", "") or "",
                    "label": e.get("label", "") or "",
                    "note": e.get("notes", "") or "",
                }
                for e in engs
            ]
        return shaped

    top_level = by_parent.get(None, [])
    top_level.sort(
        key=lambda o: (
            0 if o.get("is_primary_focus") else 1,
            _name(o).lower(),
        )
    )

    result: list[dict[str, Any]] = []
    for o in top_level:
        shaped = shape(o)
        children = by_parent.get(o["id"], [])
        if children:
            children.sort(key=lambda c: _name(c).lower())
            shaped["children"] = [shape(c) for c in children]
        result.append(shaped)
    return result


def _project_projects(
    entities: dict[str, Any],
    owes_by_project: dict[str, dict[str, int]],
    now: datetime,
    activity: dict | None = None,
) -> list[dict[str, Any]]:
    """Project entities → template-expected project shape. Drops archived."""
    out: list[dict[str, Any]] = []
    for p in _projects_array(entities):
        status = _project_status(p)
        if status == "archived":
            continue
        last_str, last_h = _humanize_age(_project_last_when(p, activity), now)
        owes = owes_by_project.get(p["id"], {"youOwe": 0, "theyOwe": 0})
        out.append(
            {
                "id": p["id"],
                "name": _name(p),
                "org": _project_org_id(p),
                "status": status,
                "last": last_str,
                "lastH": last_h,
                "next": _project_next(p),
                "youOwe": owes["youOwe"],
                "theyOwe": owes["theyOwe"],
            }
        )
    out.sort(key=lambda p: (p["lastH"] if p["lastH"] else 1_000_000))
    return out


def _project_people(
    entities: dict[str, Any],
    threads_records: list[dict[str, Any]],
    owes_by_person: dict[str, int],
    now: datetime,
) -> list[dict[str, Any]]:
    """People → template shape. Filters out the primary user."""
    threads_by_person: dict[str, int] = {}
    for t in threads_records:
        threads_by_person[t["who"]] = threads_by_person.get(t["who"], 0) + 1
    # owes_by_person is already commitment count; merge with threads if both exist
    merged = {p: max(threads_by_person.get(p, 0), owes_by_person.get(p, 0)) for p in set(threads_by_person) | set(owes_by_person)}

    out: list[dict[str, Any]] = []
    for p in entities.get("people", []):
        if _is_primary_user(p):
            continue
        age_str, age_h = _humanize_age(_person_last_interaction(p), now)
        tone: str | None = None
        if age_h >= 30 * 24:
            tone = "stuck"
        elif age_h >= 14 * 24:
            tone = "warn"
        person_threads = [t for t in threads_records if t["who"] == p["id"]]
        if any(t["state"] == "stuck" for t in person_threads):
            tone = "stuck"
        out.append(
            {
                "id": p["id"],
                "name": _name(p),
                "role": p.get("role", "") or "",
                "org": _person_org_id(p),
                "threads": merged.get(p["id"], 0),
                "age": age_str,
                "tone": tone,
            }
        )
    out.sort(
        key=lambda p: (
            -p["threads"],
            0 if p["tone"] == "stuck" else 1 if p["tone"] == "warn" else 2,
            p["name"].lower(),
        )
    )
    return out


def _ceo_display_name(entities: dict[str, Any]) -> str:
    for p in entities.get("people", []):
        if _is_primary_user(p):
            name = _name(p).strip()
            if name:
                return name.split()[0]
    return ""


def _user_id(entities: dict[str, Any]) -> str:
    for p in entities.get("people", []):
        if _is_primary_user(p):
            return p.get("id", "")
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Workspace Map input JSON.")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--now",
        type=str,
        default=None,
        help="Override 'now' for deterministic testing (ISO 8601).",
    )
    args = parser.parse_args()

    data_dir = args.workspace_root / "_hq" / "data"
    entities_path = data_dir / "entities.json"
    events_path = data_dir / "events.jsonl"

    if not entities_path.exists():
        print(f"ERROR: entities.json not found at {entities_path}", file=sys.stderr)
        return 2

    data = _read_json(entities_path)
    # v2.14.17: entities.schema.json canonical shape nests under `entities`;
    # tolerate older flat shape by falling back to top-level keys.
    entities = data["entities"] if isinstance(data.get("entities"), dict) else data

    events: list[dict[str, Any]] = []
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if args.now:
        now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(tz=timezone.utc)

    user_id = _user_id(entities)
    people_by_id = {p["id"]: p for p in entities.get("people", [])}
    projects_by_id = {p["id"]: p for p in _projects_array(entities)}

    aggregates = _aggregate_commitments(events, user_id, people_by_id, projects_by_id, now)

    # HYG1 Item 3: last-touched display comes from OBSERVED activity, derived
    # ONCE per build; the stored record stamp is the zero-event floor only.
    try:
        from thread_activity import derive_thread_activity
        activity = derive_thread_activity(
            args.workspace_root, honor_reclassifications=True)  # RECL1
    except Exception:
        activity = {}

    orgs = _project_orgs(entities)
    projects = _project_projects(entities, aggregates["owes_by_project"], now,
                                 activity=activity)
    people = _project_people(entities, aggregates["threads_records"], aggregates["owes_by_person"], now)
    threads = aggregates["threads_records"]
    commitments = aggregates["commitments"]

    # v2.14.11+ — aggregate commitments per org across all its projects.
    # Walk projects, sum each project's youOwe + theyOwe into its parent org.
    owes_by_org: dict[str, dict[str, int]] = {}
    for proj in projects:
        org_id = proj.get("org") or ""
        if not org_id:
            continue
        bucket = owes_by_org.setdefault(org_id, {"youOwe": 0, "theyOwe": 0})
        bucket["youOwe"] += proj.get("youOwe", 0)
        bucket["theyOwe"] += proj.get("theyOwe", 0)

    output_values = {
        "CEO_DISPLAY_NAME": _html_escape(_ceo_display_name(entities)),
        "LAST_BUILT": _format_last_built(now, args.workspace_root),  # v3.18.5: was missing workspace_root (v3.11.1 sig change drift → TypeError, renderer crashed)
        "ORGS_JSON": json.dumps(orgs, ensure_ascii=False),
        "PROJECTS_JSON": json.dumps(projects, ensure_ascii=False),
        "PEOPLE_JSON": json.dumps(people, ensure_ascii=False),
        "THREADS_JSON": json.dumps(threads, ensure_ascii=False),
        "COMMITMENTS_JSON": json.dumps(commitments, ensure_ascii=False),
        "OWES_BY_ORG_JSON": json.dumps(owes_by_org, ensure_ascii=False),
    }

    payload = json.dumps(output_values, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
