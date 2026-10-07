#!/usr/bin/env python3
"""connector_display_name_repin_v1 — move a workspace's declared connectors
onto the merged app's connection ids, without asking (DISC1; gap analysis §6.7,
ruling §0.29).

WHY
---
Every seat that declared a backend before the merge pinned it to a connection
id the old runtime minted — eight hex characters that rotate on reconnect. In
the merged app the same connector appears under its DISPLAY NAME
(`Superhuman_Mail`, `Slack`, `Granola`, `Google_Drive`), so the declared id is
simply absent from the tool list at fire time. Discovery correctly reads that
as drift (R13) and, on a SILENT fire, R13 says skip the leg and flag it. So
without this migration the first scheduled fire after the merge runs every mail
leg dark — no error, no output, a briefing that says less happened than did —
until the customer happens to open a chat and confirm a re-pair.

Most seats are worse off still: they never declared anything. Undeclared,
Superhuman's send and reply resolve to nothing by design (the blind vocabulary
cannot safely carry `send_draft`), so a merged seat that never declared can
never send.

WHAT IT DOES
------------
Per category (the capability manifest's own list — email, calendar, chat):

  * DECLARED, and the declared connection is VISIBLE → nothing. The common
    case and the idempotency gate: this is also what a second run sees.
  * DECLARED, connection ABSENT → re-pin, but only when EXACTLY ONE visible
    connection fingerprints as the declared provider AND its id is not
    UUID-shaped (a rotating id is not a stable answer, so re-pinning onto one
    would only have to be done again). `triggered_by: display_name_repin`.
  * UNDECLARED → declare, but only when EXACTLY ONE visible connection
    fingerprints for the category. `triggered_by: auto_single_candidate`, plus
    one plain-English nudge line in the update summary.
  * ZERO or TWO+ candidates → NO WRITE, one line in the report. Two mail
    connectors and nobody said which is the business one is exactly the
    question a machine must not answer for the customer.

Identity comes from the capability manifest's fingerprints — the same data
discovery and `detect_backend_drift` read — never from the display name's
spelling. A connector renamed to something confusing is still identified by
which operations it exposes.

Additive and reversible: the writes are one connectors row per category, plus
the audit events. The old connection id is kept on the moved account binding as
`retired_server_id`, never dropped.

Signature per references/RELEASE_MANIFEST.md "Action contract":
    fn(events_jsonl_path, workspace_root, detector_context) -> dict

`detector_context["tools"]` is the fire-time tool list (ids, or objects /
dicts carrying `tool_id`). NO tool list means no candidates, which means no
writes — a sweep over empty input must never print a clean verdict.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import connector_config  # noqa: E402
import tool_discovery  # noqa: E402
from connector_adapters import capabilities  # noqa: E402

SOURCE_SKILL = "command-room-update-bridge"
MIGRATION_ID = "connector_display_name_repin_v1"

TRIGGERED_BY_REPIN = "display_name_repin"
TRIGGERED_BY_DECLARE = "auto_single_candidate"

# Plain English for the category names, for the one nudge line. Never the
# internal key.
_CATEGORY_WORDS = {
    "email": "email",
    "calendar": "calendar",
    "chat": "team chat",
}


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _tool_ids(tools) -> List[str]:
    """Tool ids out of whatever the bridge handed in — strings, dicts, or the
    `ToolDescriptor`-shaped objects discovery uses."""
    out: List[str] = []
    for t in tools or []:
        if isinstance(t, str):
            tid = t
        elif isinstance(t, dict):
            tid = t.get("tool_id") or t.get("id") or ""
        else:
            tid = getattr(t, "tool_id", "") or ""
        tid = str(tid).strip()
        if tid:
            out.append(tid)
    return out


def _categories() -> List[str]:
    """The categories the capability manifest knows. Read, never hard-coded —
    a category added to the manifest is migrated for free."""
    try:
        cats = capabilities.load_manifest().get("categories")
    except Exception:
        cats = None
    if isinstance(cats, list) and cats:
        return [str(c) for c in cats if isinstance(c, str)]
    return ["email", "calendar", "chat"]


def _label_for(provider: Optional[str]) -> Optional[str]:
    try:
        row = capabilities.provider_row(provider) or {}
    except Exception:
        return None
    label = row.get("label")
    return label if isinstance(label, str) and label.strip() else None


def _visible_servers(tool_ids: List[str]) -> set:
    out = set()
    for tid in tool_ids:
        sid = connector_config.server_id_of(tid)
        if sid:
            out.add(sid)
    return out


def plan(workspace_root, tools, entities: Optional[dict] = None) -> Dict[str, Any]:
    """Pure read. Returns
    {"status": ..., "actions": [ {category, kind, server_id, provider, label,
    previous_server_id, triggered_by} ], "report": [str], "n_tools": int}.

    `kind` is `"repin"` or `"declare"`. Everything not acted on leaves a report
    line and nothing else."""
    tool_ids = _tool_ids(tools)
    out: Dict[str, Any] = {"status": "nothing_to_do", "actions": [],
                           "report": [], "n_tools": len(tool_ids)}
    if not tool_ids:
        out["status"] = "no_tools"
        out["report"].append("No tool list was available, so nothing was changed.")
        return out
    try:
        declared_map = connector_config.declared_backends(workspace_root, entities)
    except Exception:
        declared_map = {}
    visible = _visible_servers(tool_ids)
    descriptors = [tool_discovery.ToolDescriptor(t) for t in tool_ids]

    for category in _categories():
        try:
            fingerprints = tool_discovery._fingerprint_platforms(descriptors, category)
        except Exception:
            fingerprints = {}
        row = declared_map.get(category)
        row = row if isinstance(row, dict) and row.get("server_id") else None

        if row:
            sid = row.get("server_id")
            # IDENT1 I-1 (R-RW2-1): a declaration covers every id the ledger
            # RECORDED for the category (the scheduled sandbox's UUID beside
            # the interactive display name). Any of them visible = not
            # drifted: no offer, no write - which is what makes the walk's
            # operator warning ("do not accept the re-pin") moot.
            try:
                sids = (connector_config.declared_backend_ids(category, workspace_root, entities)
                        if workspace_root is not None else [sid]) or [sid]
            except Exception:  # noqa: BLE001
                sids = [sid]
            if any(x in visible for x in sids):
                out["report"].append(
                    f"{category}: already pointing at a connection this session can see.")
                continue
            provider = (row.get("provider") or "").strip().lower() or None
            candidates = sorted(s for s, p in fingerprints.items()
                                if provider and p == provider)
            if len(candidates) != 1:
                out["report"].append(
                    f"{category}: the declared connection is not in this session and "
                    f"{len(candidates)} visible connections match it — left alone.")
                continue
            new_sid = candidates[0]
            if connector_config.is_uuid_shaped_server_id(new_sid):
                out["report"].append(
                    f"{category}: the only match is another id that rotates — left alone.")
                continue
            out["actions"].append({
                "category": category, "kind": "repin", "server_id": new_sid,
                "provider": provider, "label": _label_for(provider),
                "previous_server_id": sid, "triggered_by": TRIGGERED_BY_REPIN,
            })
            continue

        candidates = sorted(fingerprints)
        if len(candidates) != 1:
            out["report"].append(
                f"{category}: nothing declared and {len(candidates)} candidates — "
                "left for the customer to say.")
            continue
        new_sid = candidates[0]
        provider = fingerprints[new_sid]
        out["actions"].append({
            "category": category, "kind": "declare", "server_id": new_sid,
            "provider": provider, "label": _label_for(provider),
            "previous_server_id": None, "triggered_by": TRIGGERED_BY_DECLARE,
        })

    if out["actions"]:
        out["status"] = "would_write"
    return out


def nudge_line(actions: List[Dict[str, Any]]) -> str:
    """The ONE line the update summary carries, or "" when there is nothing to
    say. Names products and categories in the customer's words — never a
    connection id, never a category key."""
    declared = [a for a in actions if a.get("kind") == "declare"]
    if not declared:
        return ""
    parts = []
    for a in declared:
        # The manifest's labels carry a parenthetical for the engineers reading
        # them ("Slack (native)"); the customer gets the product's name.
        label = (a.get("label") or "your connector").split(" (")[0].strip()
        word = _CATEGORY_WORDS.get(a.get("category") or "", a.get("category") or "")
        parts.append(f"{word} through {label}")
    if len(parts) == 1:
        body = parts[0]
    else:
        body = ", ".join(parts[:-1]) + " and " + parts[-1]
    return ("Now reading " + body +
            " — say \"what accounts do I have\" if you'd rather point somewhere else.")


def apply_plan(events_jsonl_path, workspace_root, planned: Dict[str, Any]) -> Dict[str, Any]:
    """Write the planned rows + one audit event pair per category."""
    from event_gate import append_event

    written: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    for a in planned.get("actions") or []:
        connector_config.set_declared_backend(
            workspace_root, a["category"], a["server_id"],
            provider=a.get("provider"), label=a.get("label"),
            holder=SOURCE_SKILL)
        data = {"category": a["category"], "server_id": a["server_id"],
                "provider": a.get("provider"), "label": a.get("label"),
                "triggered_by": a["triggered_by"], "migration_id": MIGRATION_ID}
        if a.get("previous_server_id"):
            data["previous_server_id"] = a["previous_server_id"]
        rows.append({"type": "connector_backend_changed",
                     "source_skill": SOURCE_SKILL, "data": data})
        rows.append({"type": "connector_detected", "source_skill": SOURCE_SKILL,
                     "data": {"server_id": a["server_id"],
                              "provider": a.get("provider"),
                              "fingerprint_matched": True,
                              "triggered_by": a["triggered_by"]}})
        written.append(a)
    if rows:
        append_event(events_jsonl_path or _events_path(workspace_root), rows,
                     holder=SOURCE_SKILL)
    return {"written": written, "events": len(rows)}


def connector_display_name_repin_v1(events_jsonl_path, workspace_root,
                                    detector_context) -> dict:
    """The manifest action. Idempotent: a second run sees every declared
    connection present in the tool list and writes nothing."""
    ctx: Dict[str, Any] = {"migration_id": MIGRATION_ID}
    try:
        tools = (detector_context or {}).get("tools")
        planned = plan(workspace_root, tools)
        ctx.update({"status": planned["status"], "report": planned["report"],
                    "n_tools": planned["n_tools"],
                    "n_repinned": 0, "n_declared": 0, "nudge_line": ""})
        if planned["status"] != "would_write":
            return {"success": True, "ran": False, "context": ctx,
                    "error": None, "fallback_prompt": None}
        applied = apply_plan(events_jsonl_path, workspace_root, planned)
        ctx["n_repinned"] = sum(1 for a in applied["written"] if a["kind"] == "repin")
        ctx["n_declared"] = sum(1 for a in applied["written"] if a["kind"] == "declare")
        ctx["categories"] = [a["category"] for a in applied["written"]]
        ctx["nudge_line"] = nudge_line(applied["written"])
        return {"success": True, "ran": bool(applied["written"]), "context": ctx,
                "error": None, "fallback_prompt": None}
    except Exception as exc:  # noqa: BLE001 — never fatal to an update
        return {"success": False, "ran": False, "context": ctx,
                "error": f"{type(exc).__name__}: {exc}",
                "fallback_prompt": ("Your connections need pointing at the right "
                                    "place; it will retry on the next update.")}


def main(argv=None) -> int:
    import argparse
    import json
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--tool", action="append", default=[],
                    help="a visible tool id; repeat (no live call is ever made)")
    ap.add_argument("--apply", action="store_true",
                    help="write the rows (default: plan only)")
    args = ap.parse_args(argv)
    ws = Path(args.workspace)
    if not args.apply:
        print(json.dumps(plan(ws, args.tool), indent=2))
        return 0
    out = connector_display_name_repin_v1(_events_path(ws), ws, {"tools": args.tool})
    print(json.dumps(out, indent=2))
    return 0 if out.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
