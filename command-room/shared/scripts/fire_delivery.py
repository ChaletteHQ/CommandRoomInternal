#!/usr/bin/env python3
"""How a scheduled fire hands its surface over when the widget cannot show
(IDENT1 I-12 (8); rulings R-RW2-7 (a), R-RW2-9; the routines-doc constraint the
coordinator recorded 2026-09-23).

WHY THIS EXISTS. On the merged seat's real scheduled shape ("Require this
computer") the widget tool is ABSENT - not unfound, absent, before and after a
tool refresh (Probes B, C, C2). The inbox still lands, as its text form in the
task chat plus the saved page. The app's own unattended templates can ALSO hand
the page over as an Artifact - a page that opens on a phone. That is a custody
change: a published page leaves the customer's folder for hosted storage, and a
client seat never makes that change silently. So it is a per-workspace setting,
OFF by default, turned on only by the customer's own words in an interactive
chat (`publish my scheduled pages`).

AND AN UNATTENDED RUN MAY ONLY REPUBLISH. A fire may republish an EXISTING
artifact without asking, page-only - no files, no force, no capability beyond
the page. Publishing a NEW artifact from a fire asks, which blocks an
autonomous run. So the interactive `publish my scheduled pages` publishes each
registered chat's first page ONCE and stores the address here
(`workspace.fire_delivery.artifacts[<task id>]`); a fire republishes that
stored address in place. No stored address, no tool, or a republish that asks
or is refused -> no publish, ONE `fire_delivery_flag` row for the next
interactive chat, and never a question from the fire.

Readers here read; the two setters write through the workspace's canonical
locked writer and are reached only through the write door (`run_writer`).

3.10-safe, stdlib only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

SETTING_KEY = "fire_delivery"
PUBLISH_FLAG = "artifact_publish"
ARTIFACTS_KEY = "artifacts"
CHANGED_EVENT = "fire_delivery_changed"
FLAG_EVENT = "fire_delivery_flag"

#: The one line a fire adds when it republished its page.
ARTIFACT_LINE = "Open the page: {url}"

#: Why a fire that was allowed to publish did not.
FLAG_NO_URL = "no_stored_page"
FLAG_NO_TOOL = "tool_absent"
FLAG_REFUSED = "republish_refused"
#: the page could not be read back to hand the container its bytes
FLAG_NO_PAGE = "page_not_landed"

#: The keys a fire's republish call may carry, and the ones it never may.
REPUBLISH_KEYS = ("url", "file_path")
FORBIDDEN_REPUBLISH_KEYS = ("files", "capabilities", "force")

#: What the customer hears after `publish my scheduled pages` / `stop ...`.
ON_LINE = ("Your scheduled chats will now also give you a page you can open "
           "on your phone.")
OFF_LINE = "Your scheduled chats will no longer publish a page."


def _block(workspace_root) -> Dict[str, Any]:
    try:
        import connector_config

        ws = connector_config.workspace_block(workspace_root)
    except Exception:  # noqa: BLE001 - a reader never raises
        return {}
    block = ws.get(SETTING_KEY) if isinstance(ws, dict) else None
    return block if isinstance(block, dict) else {}


def artifact_publish_enabled(workspace_root) -> bool:
    """True only when the workspace says so, in so many words. DEFAULT FALSE -
    an absent, malformed or unreadable setting is OFF (custody)."""
    return _block(workspace_root).get(PUBLISH_FLAG) is True


def stored_artifact_url(workspace_root, task_id: str) -> Optional[str]:
    """The page address the interactive publish stored for this chat, or
    None."""
    arts = _block(workspace_root).get(ARTIFACTS_KEY)
    if not isinstance(arts, dict):
        return None
    url = arts.get(task_id)
    return url.strip() if isinstance(url, str) and url.strip() else None


def flag_row(task_id: str, reason: str) -> Dict[str, Any]:
    """The ONE row a fire lands (through `append_jsonl`) when it was allowed to
    publish and did not - read by the next interactive chat, never asked
    about by the fire."""
    return {"type": FLAG_EVENT, "source_skill": task_id,
            "data": {"task_id": task_id, "reason": reason}}


def republish_call(url: str, page_path: str) -> Dict[str, Any]:
    """The fire's ONE republish, page-only: the stored address and the landed
    page's bytes, and nothing else - no files, no capabilities, no force."""
    return {"tool": "Artifact", "args": {"url": url, "file_path": page_path}}


def _write_setting(workspace_root, mutate, *, holder: str) -> Dict[str, Any]:
    import connector_config

    ent = connector_config._load_full(workspace_root)
    ws = connector_config._workspace_container(ent)
    block = ws.get(SETTING_KEY)
    if not isinstance(block, dict):
        block = {}
        ws[SETTING_KEY] = block
    mutate(block)
    connector_config._write_entities(workspace_root, ent, holder)
    return dict(block)


def set_artifact_publish(workspace_root, enabled: bool, *,
                         source_skill: str = "workspace-manager") -> Dict[str, Any]:
    """THE setter for the publish setting (`publish my scheduled pages` /
    `stop publishing my scheduled pages`). Writes the flag through the
    canonical locked writer and appends ONE `fire_delivery_changed` row;
    the same value again writes nothing. Returns `{changed, enabled, line}`."""
    from receipts import require_writer_identity

    require_writer_identity(workspace_root=workspace_root)
    want = bool(enabled)
    before = artifact_publish_enabled(workspace_root)
    line = ON_LINE if want else OFF_LINE
    if before == want and _block(workspace_root).get(PUBLISH_FLAG) is want:
        return {"changed": False, "enabled": want, "line": line}

    def mutate(block):
        block[PUBLISH_FLAG] = want

    _write_setting(workspace_root, mutate, holder=source_skill)
    from event_gate import append_event

    append_event(Path(workspace_root) / "_hq" / "data" / "events.jsonl",
                 [{"type": CHANGED_EVENT, "source_skill": source_skill,
                   "data": {"artifact_publish": want, "previous": before}}],
                 holder=source_skill)
    return {"changed": True, "enabled": want, "line": line}


def record_artifact_url(workspace_root, task_id: str, url: str, *,
                        source_skill: str = "workspace-manager") -> Dict[str, Any]:
    """Store the address the INTERACTIVE publish got for one chat's page, so a
    fire can republish it in place. Idempotent."""
    from receipts import require_writer_identity

    require_writer_identity(workspace_root=workspace_root)
    task_id = str(task_id or "").strip()
    url = str(url or "").strip()
    if not task_id or not url:
        raise ValueError("a task id and a page address are both required")
    if stored_artifact_url(workspace_root, task_id) == url:
        return {"changed": False, "task_id": task_id}

    def mutate(block):
        arts = block.get(ARTIFACTS_KEY)
        if not isinstance(arts, dict):
            arts = {}
            block[ARTIFACTS_KEY] = arts
        arts[task_id] = url

    _write_setting(workspace_root, mutate, holder=source_skill)
    return {"changed": True, "task_id": task_id}


__all__ = [
    "ARTIFACT_LINE", "CHANGED_EVENT", "FLAG_EVENT", "FLAG_NO_PAGE", "FLAG_NO_TOOL",
    "FLAG_NO_URL", "FLAG_REFUSED", "FORBIDDEN_REPUBLISH_KEYS", "OFF_LINE", "ON_LINE",
    "artifact_publish_enabled", "flag_row", "record_artifact_url",
    "republish_call", "set_artifact_publish", "stored_artifact_url",
]
