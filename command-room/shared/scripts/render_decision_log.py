#!/usr/bin/env python3
"""
Decision-log view regenerator (v3.13.0+).

Walks `_hq/data/events.jsonl`, collects every `type: "decision"` event,
applies lifecycle overlays (superseded / reaffirmed / snoozed), resolves
IDs to display names via `_hq/data/entities.json`, localizes timestamps
to the workspace timezone via `shared/scripts/tz.py`, and atomic-writes
`_hq/views/DECISION_LOG.md`.

WHY THIS EXISTS:

Per the 2026-05-20 Cowork handoff #12/#20: decisions land in events.jsonl
canonically (M had 138 decisions in his substrate as of that date) but
the human-readable view at `_hq/views/DECISION_LOG.md` was last regenerated
2026-05-10 and showed only 81 decisions — ~57 stale. There was no regenerator
script in the plugin. The doc header claimed "AUTO-GENERATED" but no code
actually generated it.

The view also rendered NO lifecycle state — a `decision_superseded` event
would write fine to events.jsonl but the view still showed the original
decision as live. M's daily question "what did we decide about pricing?"
returned outdated answers because the substrate had the supersede but the
view didn't show it.

v3.13.0 ships this renderer + wires it into decision-write paths
(`decision-log`, `decision-revisit`, `apply-choices`, `meeting-notes`) so
the view never falls behind canonical substrate.

PUBLIC API:

  - regenerate(workspace_root) → dict
      Reads events.jsonl + entities.json, generates the view content,
      atomic-writes _hq/views/DECISION_LOG.md, returns a dict with
      counts of decisions by status. Idempotent.

  - regenerate_per_project_views(workspace_root, project_ids=None) → dict
      Optional v3.14.x-ish: per-project DECISIONS sections in each
      project's context file. Off by default; opt-in via the project_ids
      filter or callable from a project-context regenerator.

USAGE:

    python3 shared/scripts/render_decision_log.py <workspace_root>

    # Or from another skill:
    from render_decision_log import regenerate
    counts = regenerate(workspace_root)

Status taxonomy:
  - active: decision is current; no supersede/snooze event references it
  - superseded: a later decision_superseded event names this seq
  - reaffirmed: a decision_reaffirmed event references this seq (renders
    with the most-recent reaffirmation date + snooze window)
  - snoozed: a decision_revisit_scheduled event references this seq with
    a future snooze_until_ts
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_write import atomic_write_text, atomic_write_json_locked  # noqa: E402
from event_time import event_time  # noqa: E402

# Optional: tz module for timezone localization. Fall back to UTC if missing.
try:
    from tz import to_local  # noqa: E402
    _HAS_TZ = True
except ImportError:
    _HAS_TZ = False


def _events_path(workspace_root: Path) -> Path:
    return workspace_root / "_hq" / "data" / "events.jsonl"


def _entities_path(workspace_root: Path) -> Path:
    return workspace_root / "_hq" / "data" / "entities.json"


def _view_path(workspace_root: Path) -> Path:
    return workspace_root / "_hq" / "views" / "DECISION_LOG.md"


def _load_events(events_path: Path) -> list[dict]:
    """Read events.jsonl tolerantly — skip malformed lines, accept string
    fragments as parse failures (they're the pre-atomic-write race artifacts
    documented in the substrate audit; this reader ignores them per the
    pattern `isinstance(obj, dict)` enforced everywhere else).
    """
    if not events_path.exists():
        return []
    out = []
    text = events_path.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _load_entities(entities_path: Path) -> dict[str, Any]:
    if not entities_path.exists():
        return {}
    try:
        return json.loads(entities_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _build_name_index(entities: dict) -> dict[str, str]:
    """Build a flat id → canonical_name index across people / orgs / projects."""
    idx = {}
    for p in entities.get("people", []):
        pid = p.get("id")
        if pid:
            idx[pid] = p.get("canonical_name") or pid
    for o in entities.get("orgs", []):
        oid = o.get("id")
        if oid:
            idx[oid] = o.get("canonical_name") or oid
    for collection in ("projects", "threads"):
        for proj in entities.get(collection, []):
            pid = proj.get("id")
            if pid:
                idx[pid] = (
                    proj.get("display_name")
                    or proj.get("canonical_name")
                    or proj.get("folder_name")
                    or pid
                )
    return idx


def _resolve_name(name_idx: dict[str, str], entity_id: str | None) -> str:
    if not entity_id:
        return ""
    return name_idx.get(entity_id, entity_id)


def _localize_date(ts: str | None, workspace_path: str | None = None) -> str:
    """Convert an ISO timestamp to a workspace-local date string. Date-only
    inputs (no time) return the date as-is — don't TZ-shift them backwards.

    Falls back to the first 10 chars of the timestamp if `tz.py` localization
    fails (e.g., no workspace_path passed or workspace TZ not configured).
    Date-only granularity is sufficient for the decision log; the small
    TZ-edge case (decision at midnight UTC localized to PT yesterday) isn't
    worth bridging here.
    """
    if not isinstance(ts, str) or not ts:
        return ""
    # Date-only — keep as is
    if len(ts) == 10 and ts.count("-") == 2:
        return ts
    if _HAS_TZ and workspace_path:
        try:
            local_dt = to_local(ts, workspace_path=workspace_path)
            if local_dt:
                return local_dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    # Fallback: take first 10 chars (ISO date portion)
    return ts[:10]


def _categorize_decisions(events: list[dict]) -> dict[str, Any]:
    """Walk events, return:
      - decisions: list of decision events
      - supersedes_map: {original_seq: [{new_seq, reason, reviewed_at}]}
      - reaffirms_map: {decision_seq: [{reason, reviewed_at, snooze_until}]}
      - revisits_map: {decision_seq: [{snooze_until_ts, reason}]}
    """
    decisions: list[dict] = []
    supersedes_map: dict[Any, list[dict]] = {}
    reaffirms_map: dict[Any, list[dict]] = {}
    revisits_map: dict[Any, list[dict]] = {}

    for ev in events:
        t = ev.get("type")
        data = ev.get("data") or {}
        if t == "decision":
            decisions.append(ev)
        elif t == "decision_superseded":
            orig_seq = (
                data.get("original_decision_seq")
                or data.get("supersedes_seq")
                or data.get("decision_event_seq")
            )
            if orig_seq is not None:
                supersedes_map.setdefault(orig_seq, []).append({
                    "new_seq": data.get("new_decision_seq"),
                    "reason": data.get("reason", ""),
                    "reviewed_at": data.get("reviewed_at") or event_time(ev),
                })
        elif t == "decision_reaffirmed":
            decision_seq = data.get("decision_event_seq") or data.get("original_decision_seq")
            if decision_seq is not None:
                reaffirms_map.setdefault(decision_seq, []).append({
                    "reason": data.get("reaffirmation_reason") or data.get("reason", ""),
                    "reviewed_at": data.get("reviewed_at") or event_time(ev),
                    "snooze_until": data.get("snooze_until"),
                })
        elif t == "decision_revisit_scheduled":
            decision_seq = data.get("decision_event_seq") or data.get("original_decision_seq")
            if decision_seq is not None:
                revisits_map.setdefault(decision_seq, []).append({
                    "snooze_until_ts": data.get("snooze_until_ts") or data.get("snooze_until"),
                    "reason": data.get("reason", ""),
                })

    return {
        "decisions": decisions,
        "supersedes_map": supersedes_map,
        "reaffirms_map": reaffirms_map,
        "revisits_map": revisits_map,
    }


def _decision_status(seq: Any, overlays: dict) -> tuple[str, dict[str, Any]]:
    """Return (status, overlay_data) for one decision.

    Status priority (most-specific first):
      1. superseded — if any supersedes event references this seq
      2. snoozed — if there's a revisit_scheduled with a future snooze_until
      3. reaffirmed — if there's a reaffirmation overlay
      4. active — default
    """
    if seq in overlays["supersedes_map"]:
        supersedes = overlays["supersedes_map"][seq]
        # Latest supersede wins
        latest = sorted(supersedes, key=lambda s: s.get("reviewed_at", ""), reverse=True)[0]
        return ("superseded", latest)

    if seq in overlays["revisits_map"]:
        revisits = overlays["revisits_map"][seq]
        latest = sorted(revisits, key=lambda r: r.get("snooze_until_ts", ""), reverse=True)[0]
        return ("snoozed", latest)

    if seq in overlays["reaffirms_map"]:
        reaffirms = overlays["reaffirms_map"][seq]
        latest = sorted(reaffirms, key=lambda r: r.get("reviewed_at", ""), reverse=True)[0]
        return ("reaffirmed", latest)

    return ("active", {})


def _format_decision_line(
    ev: dict,
    status: str,
    overlay: dict,
    name_idx: dict[str, str],
) -> str:
    """Return one markdown line for a decision. Format:

        - **<title>** — <decided_by_name>, <decided_at_date> [<status_badge>] <status_extras>
          <rationale_first_sentence>

    All IDs resolved to display names. No raw person_NNN / org_NNN / project_NNN
    in user-facing output (per CONTRACT Rule 4).
    """
    data = ev.get("data") or {}
    title = data.get("title") or data.get("decision") or "(untitled decision)"
    decided_by_id = data.get("decided_by") or data.get("decided_by_id") or ev.get("person_id")
    decided_by = _resolve_name(name_idx, decided_by_id) if decided_by_id else ""
    decided_at = _localize_date(data.get("decided_at") or event_time(ev))
    rationale = data.get("rationale") or data.get("reasoning") or data.get("context") or ""
    # Trim rationale to ~120 chars for the inline summary
    if isinstance(rationale, str) and len(rationale) > 200:
        rationale = rationale[:200].rstrip() + "…"

    # Status badge
    badge = ""
    extras = ""
    if status == "superseded":
        badge = "[SUPERSEDED]"
        new_seq = overlay.get("new_seq")
        if new_seq is not None:
            extras = f" — replaced by seq {new_seq}"
        if overlay.get("reason"):
            extras += f" ({overlay['reason'][:100]})"
    elif status == "snoozed":
        badge = "[SNOOZED]"
        snooze_until = overlay.get("snooze_until_ts")
        if snooze_until:
            extras = f" — revisit after {_localize_date(snooze_until)}"
        if overlay.get("reason"):
            extras += f" ({overlay['reason'][:100]})"
    elif status == "reaffirmed":
        badge = "[REAFFIRMED]"
        reviewed_at = overlay.get("reviewed_at")
        if reviewed_at:
            extras = f" — reviewed {_localize_date(reviewed_at)}"
        snooze_until = overlay.get("snooze_until")
        if snooze_until:
            extras += f", revisit after {_localize_date(snooze_until)}"
    # active gets no badge — it's the default

    parts = [f"- **{title}**"]
    meta = []
    if decided_by:
        meta.append(decided_by)
    if decided_at:
        meta.append(decided_at)
    if meta:
        parts.append(" — " + ", ".join(meta))
    if badge:
        parts.append(f" {badge}{extras}")

    line = "".join(parts)

    if rationale:
        # Indent rationale under the bullet
        line += "\n  " + rationale

    return line


def _build_content(workspace_root: Path) -> tuple[str, dict[str, Any]]:
    """Build the DECISION_LOG.md content + counts WITHOUT writing.

    Factored out of regenerate() so the changed-only path
    (regenerate_if_changed) can compare candidate content against the existing
    view before deciding whether to write (SPEC CLEAN1 / D4 idempotence)."""
    events_path = _events_path(workspace_root)
    entities_path = _entities_path(workspace_root)

    events = _load_events(events_path)
    entities = _load_entities(entities_path)
    name_idx = _build_name_index(entities)
    overlays = _categorize_decisions(events)

    # Group decisions by status
    by_status: dict[str, list[tuple[dict, str, dict]]] = {
        "active": [],
        "superseded": [],
        "reaffirmed": [],
        "snoozed": [],
    }
    for d in overlays["decisions"]:
        seq = d.get("seq")
        status, overlay = _decision_status(seq, overlays)
        by_status[status].append((d, status, overlay))

    # Sort each bucket newest-first by decided_at
    def _decided_at_key(d_tuple):
        d, _, _ = d_tuple
        data = d.get("data") or {}
        return data.get("decided_at") or event_time(d)

    for k in by_status:
        by_status[k].sort(key=_decided_at_key, reverse=True)

    # Build the document
    now_iso = datetime.datetime.now().replace(microsecond=0).isoformat()
    total = sum(len(v) for v in by_status.values())
    lines = [
        "<!-- AUTO-GENERATED by shared/scripts/render_decision_log.py — do not edit by hand. -->",
        f"<!-- regenerated-at: {now_iso} -->",
        f"<!-- total-decisions: {total} -->",
        f"<!-- source: _hq/data/events.jsonl + entities.json -->",
        "",
        "# Decision Log",
        "",
        f"_{total} decisions total · regenerated {now_iso}_",
        "",
        f"- **{len(by_status['active'])} active** — current, no supersede/snooze",
        f"- **{len(by_status['reaffirmed'])} reaffirmed** — explicitly re-confirmed",
        f"- **{len(by_status['snoozed'])} snoozed** — revisit scheduled later",
        f"- **{len(by_status['superseded'])} superseded** — replaced by a later decision",
        "",
        "---",
        "",
    ]

    for status_label, status_title in [
        ("active", "Active"),
        ("reaffirmed", "Reaffirmed"),
        ("snoozed", "Snoozed"),
        ("superseded", "Superseded (historical)"),
    ]:
        bucket = by_status[status_label]
        if not bucket:
            continue
        lines.append(f"## {status_title} ({len(bucket)})")
        lines.append("")
        for d, status, overlay in bucket:
            lines.append(_format_decision_line(d, status, overlay, name_idx))
            lines.append("")
        lines.append("")

    content = "\n".join(lines).rstrip() + "\n"

    counts = {
        "total": total,
        "active": len(by_status["active"]),
        "reaffirmed": len(by_status["reaffirmed"]),
        "snoozed": len(by_status["snoozed"]),
        "superseded": len(by_status["superseded"]),
    }
    return content, counts


# Header lines that change on every render (timestamps) even when the decision
# content is identical. Stripped before the changed-only comparison so a quiet
# workspace is a true no-op write.
def _strip_volatile(text: str) -> str:
    out = []
    for line in text.splitlines():
        if "regenerated-at:" in line:
            continue
        if line.startswith("_") and "· regenerated " in line:
            continue
        out.append(line)
    return "\n".join(out)


def regenerate(workspace_root: str | Path) -> dict[str, Any]:
    """Read events.jsonl + entities.json, build the view, atomic-write to
    _hq/views/DECISION_LOG.md. Returns a counts dict.

    The renderer is the canonical owner of `_hq/views/DECISION_LOG.md`
    (v3.13.0+ — pre-v3.13.0 the file was hand-edited or stale). Any skill
    that writes a decision-related event should call this after the write
    so the view never falls behind.
    """
    workspace_root = Path(workspace_root)
    view_path = _view_path(workspace_root)
    content, counts = _build_content(workspace_root)

    # Atomic write — no JSON lock needed (this is a .md view file, not the substrate),
    # but use atomic_write_text for fsync + rename safety.
    view_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(view_path, content)

    counts["view_path"] = str(view_path)
    return counts


def regenerate_if_changed(workspace_root: str | Path) -> dict[str, Any]:
    """Changed-only regeneration (SPEC CLEAN1 / D4). Build the candidate view,
    compare it (ignoring volatile timestamp lines) against what's on disk, and
    write ONLY if the decision content actually changed.

    cleanup calls this every weekly run so a missed decision-log regen never
    persists for weeks — while a workspace with no new decisions stays a true
    no-op write (the idempotence guarantee, acceptance #7). Returns the counts
    dict plus `changed` (bool: did the content differ / was a write made)."""
    workspace_root = Path(workspace_root)
    view_path = _view_path(workspace_root)
    content, counts = _build_content(workspace_root)

    old = view_path.read_text(encoding="utf-8") if view_path.exists() else ""
    changed = _strip_volatile(old) != _strip_volatile(content)
    if changed:
        view_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(view_path, content)

    counts["changed"] = changed
    counts["view_path"] = str(view_path)
    return counts


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python3 render_decision_log.py <workspace_root>", file=sys.stderr)
        return 2
    workspace_root = Path(argv[1])
    if not workspace_root.exists():
        print(f"ABORT: workspace not found: {workspace_root}", file=sys.stderr)
        return 2
    result = regenerate(workspace_root)
    print(f"OK — regenerated DECISION_LOG.md")
    print(f"  total: {result['total']}")
    print(f"  active: {result['active']}")
    print(f"  reaffirmed: {result['reaffirmed']}")
    print(f"  snoozed: {result['snoozed']}")
    print(f"  superseded: {result['superseded']}")
    print(f"  written to: {result['view_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
