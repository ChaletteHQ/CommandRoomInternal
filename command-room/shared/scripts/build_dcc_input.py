#!/usr/bin/env python3
"""
Compose Daily Command Center input.json from pre-projected snapshot data.

Architecture (v2.7.11+): the skill is responsible for fetching MCP data
(Granola, Gmail, etc.) and projecting each source into the template's expected
shape. This script accepts those pre-projected JSON files and composes the
final input.json that render_artifact.py substitutes into the template. Any
projection logic the model would have done inline now happens in the skill,
intermediate JSON is passed through here, and the final HTML is byte-
deterministic.

Inputs (all optional — missing = empty array / null):
    --workspace-root <path>        Required. Reads entities.json for CEO name + connector status.
    --matters <json>               Path to JSON file: array of matter objects.
    --inbox <json>                 Path to JSON file: array of inbox item objects.
    --meetings <json>              Path to JSON file: array of meeting objects.
    --calendar-tool <id>           MCP tool ID for live calendar refresh (e.g.
                                   "mcp__abc__list_events"). null if unavailable.
    --connected-sources <list>     Comma-separated source ids that are connected
                                   (gmail,slack,docs,drive,calendar,asana). Drives
                                   the inbox filter chips.
    --output <path>                Output JSON path (default: stdout).

Output keys (all pre-formatted strings, ready for renderer substitution):
    CEO_DISPLAY_NAME
    LAST_BUILT
    MATTERS_JSON
    INBOX_JSON
    MEETINGS_JSON
    CALENDAR_TOOL_NAME
    CONNECTED_SOURCES
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from tz import to_local, TZResolutionError  # noqa: E402


def _format_last_built(now: datetime, workspace_root: Path) -> str:
    """Render LAST_BUILT in workspace TZ per tz.py.

    v3.11.1: thread workspace_root through tz so the script doesn't depend on
    walk-up resolution (which never worked from the plugin clone). If TZ can't
    be resolved, render UTC with an explicit tag so the miss is visible rather
    than silently wrong.
    """
    try:
        local = to_local(now, workspace_path=workspace_root)
    except TZResolutionError as exc:
        print(f"⚠️ tz.py: {exc} — rendering LAST_BUILT in UTC.", file=sys.stderr)
        local = None
    if local is None:
        return now.strftime("%Y-%m-%d %H:%M UTC")
    return local.strftime("%Y-%m-%d %H:%M %Z")


def _read_json_or_default(path: Path | None, default: Any) -> Any:
    if not path:
        return default
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _ceo_first_name(entities_path: Path) -> str:
    if not entities_path.exists():
        return ""
    try:
        data = json.loads(entities_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    # v2.14.17: entities.schema.json canonical shape nests under `entities`;
    # tolerate older flat shape by falling back to top-level keys.
    entities = data["entities"] if isinstance(data.get("entities"), dict) else data
    for p in entities.get("people", []):
        # v2.7.13: handle both schemas
        if p.get("is_user") or p.get("is_primary_user"):
            name = (p.get("name") or p.get("canonical_name") or "").strip()
            if name:
                return name.split()[0]
    return ""


# ----- v2.7.13: native fallback projection for "What matters" -----
# If the skill orchestrator passes --matters, that wins. If not, the script
# projects a reasonable 5-item snapshot from entities.json + events.jsonl so
# the artifact never ships empty.


def _humanize_age_simple(when: str | None, now: datetime) -> tuple[str, int]:
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


def _project_matters_fallback(
    workspace_root: Path, now: datetime
) -> list[dict[str, Any]]:
    """
    Best-effort 5-item "What matters" projection from workspace data only (no MCP).
    Heuristic: surface stuck commitments first, then aging open commitments,
    then stale-but-active projects. Cap at 5. Returns [] if data is sparse.
    """
    entities_path = workspace_root / "_hq" / "data" / "entities.json"
    events_path = workspace_root / "_hq" / "data" / "events.jsonl"
    if not entities_path.exists():
        return []
    try:
        data = json.loads(entities_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
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

    # Identify the user
    user_id = ""
    for p in entities.get("people", []):
        if p.get("is_user") or p.get("is_primary_user"):
            user_id = p.get("id", "")
            break

    # Build lookup tables
    people_by_id = {p.get("id"): p for p in entities.get("people", [])}
    projects_by_id = {p.get("id"): p for p in (entities.get("projects") or entities.get("threads") or [])}

    def _name_of(rec: dict[str, Any] | None) -> str:
        if not rec:
            return ""
        return rec.get("name") or rec.get("canonical_name") or rec.get("id", "")

    # Field readers handle BOTH canonical (v2.7.15+) and legacy flat shapes.
    # Canonical: data.owner_id / data.title / data.due / data.status, primary_thread_id top-level.
    # Legacy:    owner / title / due / status / project_id all top-level.
    def _c_data(c: dict[str, Any]) -> dict[str, Any]:
        return c.get("data") or {}

    def _c_owner(c: dict[str, Any]) -> str:
        d = _c_data(c)
        return d.get("owner_id") or d.get("owner") or c.get("owner") or c.get("owner_id") or ""

    def _c_project(c: dict[str, Any]) -> str:
        d = _c_data(c)
        return c.get("primary_thread_id") or d.get("project_id") or c.get("project_id") or d.get("thread_id") or c.get("thread_id") or ""

    def _c_title(c: dict[str, Any]) -> str:
        d = _c_data(c)
        return (d.get("title") or c.get("title") or "").strip()

    def _c_due(c: dict[str, Any]) -> str:
        d = _c_data(c)
        return d.get("due") or c.get("due") or ""

    def _c_status(c: dict[str, Any]) -> str:
        d = _c_data(c)
        return d.get("status") or c.get("status") or "open"

    def _c_ts(c: dict[str, Any]) -> str:
        d = _c_data(c)
        from event_time import event_time
        return event_time(c) or d.get("timestamp") or c.get("created_at") or ""

    def _c_id(c: dict[str, Any]) -> str:
        d = _c_data(c)
        return d.get("id") or c.get("id") or f"commitment_seq_{c.get('seq', '?')}"

    # Find open commitments and resolved IDs
    resolved_ids: set[str] = set()
    open_commits: list[dict[str, Any]] = []
    for ev in events:
        et = ev.get("type") or ev.get("event") or ""
        d = ev.get("data") or {}
        if et in ("commitment_resolved", "thread_resolved"):
            cid = (
                d.get("commitment_id") or d.get("thread_id") or d.get("id")
                or ev.get("commitment_id") or ev.get("thread_id") or ev.get("id")
            )
            if cid:
                resolved_ids.add(cid)
        elif et == "commitment":
            if _c_status(ev) in ("open", "overdue"):
                open_commits.append(ev)
    open_commits = [c for c in open_commits if _c_id(c) not in resolved_ids]

    matters: list[dict[str, Any]] = []
    seq = 1

    # Layer 1: overdue commitments first, by oldest. v4.5.2 R1b: the template
    # token `type: "stuck"` is a legacy STYLING key the artifact CSS keys on —
    # kept for template compat. Every user-VISIBLE string says "overdue";
    # never render the word "stuck" for an overdue-by-due-date item.
    overdue_commits = [c for c in open_commits if _c_status(c) == "overdue"]
    overdue_commits.sort(key=lambda c: _c_ts(c))
    for c in overdue_commits[:3]:
        owner = _c_owner(c)
        proj_id = _c_project(c)
        proj_name = _name_of(projects_by_id.get(proj_id)) or proj_id
        owner_name = _name_of(people_by_id.get(owner)) or "owner"
        title = _c_title(c) or "(untitled commitment)"
        due = _c_due(c)
        age_str, _ = _humanize_age_simple(_c_ts(c), now)
        is_user_owed = owner == user_id or not owner
        action_verb = "wrap up" if is_user_owed else "nudge " + owner_name
        matters.append({
            "id": f"matter_fallback_{seq}",
            "source": "signal",
            "type": "stuck",  # legacy styling token (see Layer 1 note); display text says "overdue"
            "aged": (age_str + " overdue") if age_str else "overdue",
            "headline": title,
            "evidence": f"Commitment is overdue{(' · due ' + due) if due else ''}{(' · owner: ' + owner_name) if owner_name and not is_user_owed else ''}.",
            "project": proj_name,
            "action": ("go " + proj_name + ", then " if proj_name else "") + action_verb + " on: " + title,
        })
        seq += 1

    # Layer 2: aging open commitments (open status, > 21d old)
    aging = [c for c in open_commits if _c_status(c) == "open"]
    aging.sort(key=lambda c: _c_ts(c))
    for c in aging:
        if len(matters) >= 5:
            break
        age_str, age_h = _humanize_age_simple(_c_ts(c), now)
        if age_h < 21 * 24:
            continue
        owner = _c_owner(c)
        proj_id = _c_project(c)
        proj_name = _name_of(projects_by_id.get(proj_id)) or proj_id
        owner_name = _name_of(people_by_id.get(owner)) or ""
        title = _c_title(c) or "(untitled commitment)"
        is_user_owed = owner == user_id or not owner
        type_ = "priority" if is_user_owed else "stuck"  # styling token only; visible text says "open"
        action_verb = "work on" if is_user_owed else "nudge " + (owner_name or "owner")
        matters.append({
            "id": f"matter_fallback_{seq}",
            "source": "signal",
            "type": type_,
            "aged": age_str + " open",
            "headline": title,
            "evidence": f"Commitment open since {age_str}{(' · ' + owner_name) if owner_name and not is_user_owed else ''}.",
            "project": proj_name,
            "action": ("go " + proj_name + ", then " if proj_name else "") + action_verb + " on: " + title,
        })
        seq += 1

    # Layer 3: stale active projects (last touch > 21d, status=active).
    # HYG1 Item 3: staleness derives from OBSERVED events (thread_activity),
    # never the deprecated last_activity stamp — the fossil made genuinely
    # active projects read stale (F-54 class). The record stamp remains the
    # zero-event floor only.
    if len(matters) < 5:
        try:
            from thread_activity import derive_thread_activity
            _activity = derive_thread_activity(
                workspace_root, honor_reclassifications=True)  # RECL1
        except Exception:
            _activity = {}
        projects = entities.get("projects") or entities.get("threads") or []
        stale_projects = []
        for p in projects:
            stage = (p.get("stage") or p.get("status") or "active").lower()
            if stage != "active":
                continue
            act = _activity.get(p.get("id"))
            last = (act.ts.isoformat() if act is not None
                    else p.get("last_touched") or p.get("last_activity"))
            age_str, age_h = _humanize_age_simple(last, now)
            if age_h >= 21 * 24:
                stale_projects.append((p, age_str, age_h))
        stale_projects.sort(key=lambda x: -x[2])
        for p, age_str, _ in stale_projects:
            if len(matters) >= 5:
                break
            proj_name = _name_of(p)
            next_action = p.get("next_action") or (p.get("notes") or "").split(".")[0]
            matters.append({
                "id": f"matter_fallback_{seq}",
                "source": "signal",
                "type": "anomaly",
                "aged": age_str + " quiet",
                "headline": f"{proj_name} — {age_str} since last activity, status still active",
                "evidence": (next_action or "Project marked active but no recent activity logged.").strip(),
                "project": proj_name,
                "action": f"go {proj_name}, then give me a status update",
            })
            seq += 1

    return matters[:5]


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Compose Daily Command Center input JSON.")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--matters", type=Path, default=None)
    parser.add_argument("--inbox", type=Path, default=None)
    parser.add_argument("--meetings", type=Path, default=None)
    parser.add_argument(
        "--calendar-tool",
        type=str,
        default=None,
        help="MCP tool ID for live calendar refresh (full prefixed name, e.g. mcp__<uuid>__list_events). Omit or 'null' to disable live calendar refresh.",
    )
    parser.add_argument(
        "--granola-tool",
        type=str,
        default=None,
        help="MCP tool ID for live Granola meetings refresh (full prefixed name, e.g. mcp__<uuid>__list_meetings). Omit or 'null' to disable live meetings refresh — Meetings tab stays snapshot.",
    )
    parser.add_argument(
        "--gmail-tool",
        type=str,
        default=None,
        help="MCP tool ID for live Gmail inbox refresh (full prefixed name, e.g. mcp__<uuid>__search_threads). Omit or 'null' to disable live Gmail refresh — Gmail items in Inbox tab stay snapshot.",
    )
    parser.add_argument(
        "--connected-sources",
        type=str,
        default="",
        help="Comma-separated connector ids (gmail,slack,docs,drive,calendar,asana).",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--now",
        type=str,
        default=None,
        help="Override 'now' for deterministic testing (ISO 8601).",
    )
    args = parser.parse_args()

    entities_path = args.workspace_root / "_hq" / "data" / "entities.json"
    ceo_name = _ceo_first_name(entities_path)

    if args.now:
        now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(tz=timezone.utc)

    # v2.7.13: if --matters is not provided, fall back to native projection
    # from entities.json + events.jsonl. Inbox + meetings stay empty unless
    # the skill orchestrator pre-projected them via MCP probes — those tabs
    # populate live via callMcpTool on artifact open (refreshAll on init).
    if args.matters is None:
        matters = _project_matters_fallback(args.workspace_root, now)
    else:
        matters = _read_json_or_default(args.matters, [])

    inbox = _read_json_or_default(args.inbox, [])
    meetings = _read_json_or_default(args.meetings, [])

    if not isinstance(matters, list):
        matters = []
    if not isinstance(inbox, list):
        inbox = []
    if not isinstance(meetings, list):
        meetings = []

    def _tool_literal(value: str | None) -> str:
        if not value or value.lower() == "null":
            return "null"
        return json.dumps(value)

    cal_tool_value = _tool_literal(args.calendar_tool)
    granola_tool_value = _tool_literal(args.granola_tool)
    gmail_tool_value = _tool_literal(args.gmail_tool)

    connected_list = [s.strip() for s in args.connected_sources.split(",") if s.strip()]

    output_values = {
        "CEO_DISPLAY_NAME": _html_escape(ceo_name),
        "LAST_BUILT": _format_last_built(now, args.workspace_root),
        "MATTERS_JSON": json.dumps(matters, ensure_ascii=False),
        "INBOX_JSON": json.dumps(inbox, ensure_ascii=False),
        "MEETINGS_JSON": json.dumps(meetings, ensure_ascii=False),
        "CALENDAR_TOOL_NAME": cal_tool_value,
        "GRANOLA_TOOL_NAME": granola_tool_value,
        "GMAIL_TOOL_NAME": gmail_tool_value,
        "CONNECTED_SOURCES": json.dumps(connected_list),
    }

    payload = json.dumps(output_values, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
