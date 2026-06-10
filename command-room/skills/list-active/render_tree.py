#!/usr/bin/env python3
"""
Render the workspace org+project tree for list-active.

Pure read over:
  _hq/data/entities.json
  _hq/data/events.jsonl
  _hq/data/aliases.json

Usage:
  python render_tree.py [WORKSPACE_ROOT] [--include-archived]

If entities.json is missing, falls back to a folder scan.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _load_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso[:10]
    return dt.strftime("%b %d").replace(" 0", " ")


# ---------------------------------------------------------------------------
# Tree construction
# ---------------------------------------------------------------------------

@dataclass
class ProjectNode:
    id: str
    display_name: str
    status: str = "active"
    kind: str = "initiative"
    affiliation_id: str | None = None
    parent_thread_id: str | None = None
    last_activity: str | None = None
    aliases: list[str] = field(default_factory=list)
    children: list["ProjectNode"] = field(default_factory=list)


@dataclass
class OrgNode:
    id: str
    display_name: str
    scope: str = "operating"
    relationship_type: str | None = None
    parent_org_id: str | None = None
    is_primary_focus: bool = False
    aliases: list[str] = field(default_factory=list)
    projects: list[ProjectNode] = field(default_factory=list)
    children: list["OrgNode"] = field(default_factory=list)


def _compute_last_activity(events: list[dict]) -> dict[str, str]:
    """Return map of project_id → latest event ts, using primary_thread_id only."""
    latest: dict[str, str] = {}
    for ev in events:
        pt = ev.get("primary_thread_id")
        ts = ev.get("ts")
        if not pt or not ts:
            continue
        prev = latest.get(pt)
        if prev is None or ts > prev:
            latest[pt] = ts
    return latest


def _collect_aliases(aliases_data: dict | None) -> dict[str, list[str]]:
    """entity_id → list of alias strings."""
    out: dict[str, list[str]] = defaultdict(list)
    if not aliases_data:
        return out
    mappings = aliases_data.get("mappings", aliases_data) if isinstance(aliases_data, dict) else []
    if isinstance(mappings, dict):
        # Legacy/simple format: {"alias": "entity_id", ...}
        for alias, eid in mappings.items():
            out[eid].append(alias)
    elif isinstance(mappings, list):
        for m in mappings:
            alias = m.get("alias") or m.get("raw")
            eid = m.get("entity_id") or m.get("canonical_id") or m.get("target_id")
            if alias and eid:
                out[eid].append(alias)
    return out


def build_tree(
    entities: dict,
    events: list[dict],
    aliases_data: dict | None,
    include_archived: bool,
) -> tuple[list[OrgNode], list[ProjectNode]]:
    """Return (root_orgs, workspace_level_projects)."""
    last_activity = _compute_last_activity(events)
    alias_map = _collect_aliases(aliases_data)

    orgs_by_id: dict[str, OrgNode] = {}
    for org in entities.get("orgs", []):
        oid = org.get("id")
        if not oid:
            continue
        orgs_by_id[oid] = OrgNode(
            id=oid,
            display_name=org.get("display_name") or org.get("name") or oid,
            scope=org.get("scope", "operating"),
            relationship_type=org.get("relationship_type"),
            parent_org_id=org.get("parent_org_id"),
            is_primary_focus=bool(org.get("is_primary_focus")),
            aliases=alias_map.get(oid, []),
        )

    # Build org hierarchy
    root_orgs: list[OrgNode] = []
    for org in orgs_by_id.values():
        if org.parent_org_id and org.parent_org_id in orgs_by_id:
            orgs_by_id[org.parent_org_id].children.append(org)
        else:
            root_orgs.append(org)

    # Projects (called "threads" in schema for stability)
    projects_by_id: dict[str, ProjectNode] = {}
    for proj in entities.get("threads", entities.get("projects", [])):
        pid = proj.get("id")
        if not pid:
            continue
        status = proj.get("status", "active")
        if not include_archived and status in ("archived", "completed"):
            continue
        projects_by_id[pid] = ProjectNode(
            id=pid,
            display_name=proj.get("display_name") or proj.get("name") or pid,
            status=status,
            kind=proj.get("kind", "initiative"),
            affiliation_id=proj.get("affiliation_id"),
            parent_thread_id=proj.get("parent_thread_id"),
            last_activity=last_activity.get(pid),
            aliases=alias_map.get(pid, []),
        )

    # Nest sub-projects under parents
    top_projects: list[ProjectNode] = []
    for proj in projects_by_id.values():
        if proj.parent_thread_id and proj.parent_thread_id in projects_by_id:
            projects_by_id[proj.parent_thread_id].children.append(proj)
        else:
            top_projects.append(proj)

    # Attach top-level projects to orgs
    workspace_projects: list[ProjectNode] = []
    for proj in top_projects:
        if proj.affiliation_id and proj.affiliation_id in orgs_by_id:
            orgs_by_id[proj.affiliation_id].projects.append(proj)
        else:
            workspace_projects.append(proj)

    # Sort projects by last_activity desc within each org
    def _sort_projects(plist: list[ProjectNode]) -> list[ProjectNode]:
        return sorted(plist, key=lambda p: p.last_activity or "", reverse=True)

    def _walk(o: OrgNode) -> None:
        o.projects = _sort_projects(o.projects)
        for p in o.projects:
            p.children = _sort_projects(p.children)
        for c in o.children:
            _walk(c)

    for o in root_orgs:
        _walk(o)

    return root_orgs, _sort_projects(workspace_projects)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _alias_suffix(aliases: list[str]) -> str:
    if not aliases:
        return ""
    shown = aliases[:3]
    more = len(aliases) - len(shown)
    s = ", ".join(shown)
    if more > 0:
        s += f", +{more} more"
    return f" · aka {s}"


def _status_suffix(status: str) -> str:
    if status == "archived":
        return " [archived]"
    if status == "completed":
        return " [completed]"
    return ""


def _org_descriptor(o: OrgNode) -> str:
    parts = []
    if o.scope and o.scope != "operating":
        parts.append(o.scope)
    elif o.scope == "operating" and o.relationship_type and o.relationship_type != "self":
        parts.append(f"operating · {o.relationship_type}")
    elif o.scope == "operating":
        parts.append("operating")
    if parts and (o.scope == "operating" and o.relationship_type and o.relationship_type != "self"):
        pass  # already combined
    return "(" + " · ".join(parts) + ")" if parts else ""


def _render_project(p: ProjectNode, indent: str, is_last: bool, lines: list[str]) -> None:
    branch = "└─" if is_last else "├─"
    date = _fmt_date(p.last_activity)
    name = p.display_name
    kind_tag = ""
    if p.kind and p.kind not in ("initiative",):
        kind_tag = f" ({p.kind})"
    lines.append(
        f"{indent}{branch} {name}{kind_tag}{_alias_suffix(p.aliases)}{_status_suffix(p.status)} · last {date}"
    )
    next_indent = indent + ("   " if is_last else "│  ")
    for i, child in enumerate(p.children):
        _render_project(child, next_indent, i == len(p.children) - 1, lines)


def _render_org(o: OrgNode, lines: list[str]) -> None:
    desc = _org_descriptor(o)
    header = f"{o.display_name} {desc}".strip() + _alias_suffix(o.aliases)
    lines.append(header)
    for i, proj in enumerate(o.projects):
        _render_project(proj, "  ", i == len(o.projects) - 1, lines)
    for child_org in o.children:
        lines.append("")
        _render_org(child_org, lines)
    lines.append("")


def render(
    root_orgs: list[OrgNode],
    workspace_projects: list[ProjectNode],
) -> str:
    # Partition root orgs into sections
    primary: list[OrgNode] = []
    advisory: list[OrgNode] = []
    other: list[OrgNode] = []
    for o in root_orgs:
        if o.is_primary_focus:
            primary.append(o)
        elif o.scope == "advisory" or o.relationship_type == "advisor":
            advisory.append(o)
        else:
            other.append(o)

    lines: list[str] = []

    def _section(title: str, orgs: list[OrgNode]) -> None:
        lines.append(title)
        lines.append("")
        if not orgs:
            lines.append("(none)")
            lines.append("")
            return
        for o in orgs:
            _render_org(o, lines)

    _section("PRIMARY FOCUS", primary)
    _section("ADVISORY", advisory)
    _section("OTHER", other)

    lines.append("WORKSPACE-LEVEL")
    lines.append("")
    if workspace_projects:
        for i, p in enumerate(workspace_projects):
            _render_project(p, "  ", i == len(workspace_projects) - 1, lines)
    else:
        lines.append("(none)")
    lines.append("")

    total_projects = _count_projects(root_orgs) + len(workspace_projects)
    total_orgs = _count_orgs(root_orgs)
    lines.append(f"Total: {total_projects} projects across {total_orgs} orgs.")

    return "\n".join(lines).rstrip() + "\n"


def _count_projects(orgs: list[OrgNode]) -> int:
    n = 0
    def _walk(plist: list[ProjectNode]) -> None:
        nonlocal n
        for p in plist:
            n += 1
            _walk(p.children)
    def _walk_orgs(olist: list[OrgNode]) -> None:
        for o in olist:
            _walk(o.projects)
            _walk_orgs(o.children)
    _walk_orgs(orgs)
    return n


def _count_orgs(orgs: list[OrgNode]) -> int:
    n = 0
    def _walk(olist: list[OrgNode]) -> None:
        nonlocal n
        for o in olist:
            n += 1
            _walk(o.children)
    _walk(orgs)
    return n


# ---------------------------------------------------------------------------
# Folder fallback
# ---------------------------------------------------------------------------

SKIP_FOLDERS = {"_hq", "_archive", "_people", "_hq", ".claude"}


def folder_fallback(workspace_root: Path) -> str:
    """Render a flat project list from folder structure when entities.json is missing."""
    projects: list[tuple[str, str | None]] = []
    for entry in sorted(workspace_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name in SKIP_FOLDERS:
            continue
        notes = list(entry.glob("SESSION_NOTES_*.md"))
        if not notes:
            continue
        mod_ts = max(n.stat().st_mtime for n in notes)
        iso = datetime.fromtimestamp(mod_ts, tz=timezone.utc).isoformat()
        projects.append((entry.name, iso))

    projects.sort(key=lambda t: t[1] or "", reverse=True)

    lines = ["PROJECTS (folder scan — no entities.json found)", ""]
    if not projects:
        lines.append("(no projects found — run 'onboard me' to set up your workspace)")
    else:
        for name, iso in projects:
            lines.append(f"{name} · last {_fmt_date(iso)}")
    lines.append("")
    lines.append("Run `migrate to v2` to enable the full org tree view.")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace", nargs="?", default=".", help="Workspace root")
    ap.add_argument("--include-archived", action="store_true")
    args = ap.parse_args()

    root = Path(args.workspace).expanduser().resolve()
    if not root.exists():
        print(f"Workspace not found: {root}", file=sys.stderr)
        return 2

    entities_path = root / "_hq" / "data" / "entities.json"
    events_path = root / "_hq" / "data" / "events.jsonl"
    aliases_path = root / "_hq" / "data" / "aliases.json"

    entities = _load_json(entities_path)
    if not entities:
        print(folder_fallback(root))
        return 0

    events = _load_events(events_path)
    aliases_data = _load_json(aliases_path)

    root_orgs, workspace_projects = build_tree(
        entities, events, aliases_data, include_archived=args.include_archived
    )

    if not root_orgs and not workspace_projects:
        print('No projects found. Run "onboard me" to set up your workspace, or "new project [name]" to create one.')
        return 0

    print(render(root_orgs, workspace_projects))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
