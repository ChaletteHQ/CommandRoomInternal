"""surface_text — the grouped-list TEXT form of a widget surface, for a run with no widget tool (S-13, night 2; PARALLEL-B lane A, 2026-09-24).

The merged app's scheduled fire has no `show_widget` (spec §0.1 fact 1). The
inbox chain answered that shape first (`inbox_helpers.render_inbox_text` +
`plan_delivery`, ruling R-RW2-7 (a)); My Plate and Staff Meeting stopped with
one sentence instead — "door-complete, no text fallback" — and were declared
UNMIGRATED in the census. This module is the text half for those two: the
SAME rows the widget shows (the surface's own data view, row numbers
included), as a grouped list, the saved-at line LAST in the code-span form
`deliverables.saved_to_line` composes, one push sentence naming counts, and
the delivery plan the run makes its calls from. Every composed line passes
`chat_output_validator.validate_chat_output` with the fire families on.

Nothing here writes. The drivers (`plate_helpers.run_my_plate_surface`,
`staff_meeting_helpers.run_staff_meeting_surface`) call it after
`surface_drivers.run_surface` has landed the page and the receipt.

3.10-safe (the sandbox VM).
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# The tool names the inbox delivery planner keys on — ONE spelling, theirs.
from inbox_helpers import (ARTIFACT_TOOL, CONTAINER_PAGE_DIR, SEND_FILE_TOOL,
                           SEND_MESSAGE_TOOL, WIDGET_TOOL, _landed_page, widget_tool_name,
                           _tool_names, text_row_line, widget_absent_line)

__all__ = [
    "render_my_plate_text", "render_staff_meeting_text", "plan_text_delivery",
    "push_sentence", "validated", "saved_at_line", "PUSH_LINES", "DUE_GROUPS",
]

#: The due buckets a My Plate section is split into, in order. The widget's
#: rows carry their due date in `data.due` (ISO) or in the context tag
#: ("due May 25"); a row with neither is "No date".
DUE_GROUPS = (("overdue", "Overdue"), ("this_week", "This week"),
              ("later", "Later"), ("no_date", "No date"))

#: The ONE push sentence per surface: counts only, no name, no path, no tool.
PUSH_LINES = {
    "my-plate": "My Plate: {promised} promised, {personal} personal; the list is in your My Plate chat.",
    "staff-meeting": "Staff Meeting: {to_confirm} to confirm, {overdue} overdue; the list is in your Staff Meeting chat.",
}


def _fire_mode() -> Optional[str]:
    via = str(os.environ.get("CR_FIRED_VIA", "") or "").strip().lower()
    return via if via in ("scheduled", "catchup") else None


def validated(line: str, *, content: bool = False, fired_via: Optional[str] = None) -> str:
    """A composed line, or a raise naming the family that refused it. A line
    carrying the customer's own words (a row's name) is `content`: it may end
    in a question mark and is never the fire asking one."""
    from chat_output_validator import validate_chat_output

    mode = None if content else (fired_via or _fire_mode())
    result = validate_chat_output(line, fired_via=mode)
    if not getattr(result, "ok", True):
        raise ValueError("a composed surface line did not pass the gate: "
                         + "; ".join(str(v) for v in result.violations))
    return line


def saved_at_line(page_rel: str, device_root: Optional[str]) -> str:
    """The message's LAST line. With the customer's folder path forwarded
    (`device_root` / `CR_DEVICE_WORKSPACE`) it is `deliverables.saved_to_line`'s
    code-span form (I-3); without it, the words form the inbox uses."""
    import deliverables as dl

    if device_root:
        return validated(dl.saved_to_line(dl.device_join(device_root, page_rel)))
    return widget_absent_line(page_rel, None)


# ---------------------------------------------------------------------------
# rows -> lines
# ---------------------------------------------------------------------------

_DUE_TAG_RE = re.compile(r"\bdue\s+([A-Z][a-z]{2})\s+(\d{1,2})\b")
_MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def _item_due(item: Dict[str, Any], today: _dt.date) -> Optional[_dt.date]:
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    raw = str(data.get("due") or item.get("due") or "").strip()
    if raw:
        try:
            return _dt.date.fromisoformat(raw[:10])
        except ValueError:
            pass
    m = _DUE_TAG_RE.search(str(item.get("context_tag") or ""))
    if m:
        month = _MONTHS.get(m.group(1))
        if month:
            year = today.year
            try:
                cand = _dt.date(year, month, int(m.group(2)))
            except ValueError:
                return None
            # a due month more than half a year ahead of today was last year's
            if (cand - today).days > 183:
                cand = _dt.date(year - 1, month, int(m.group(2)))
            return cand
    return None


def _due_group(item: Dict[str, Any], today: _dt.date) -> str:
    due = _item_due(item, today)
    if due is None:
        return "no_date"
    delta = (due - today).days
    if delta < 0:
        return "overdue"
    if delta <= 7:
        return "this_week"
    return "later"


def _row_line(item: Dict[str, Any], fired_via: Optional[str]) -> str:
    """One row in the inbox's shared form (`inbox_helpers.TEXT_ROW_FORM`, a
    bold number Markdown cannot renumber - ROUTE2, review B N-2), carrying
    the widget's own number (`display_n`, the action key)."""
    n = item.get("display_n") if item.get("display_n") is not None else item.get("n")
    return validated(text_row_line(n, item), content=True, fired_via=fired_via)


def _today(now_iso: Optional[str]) -> _dt.date:
    if now_iso:
        try:
            return _dt.date.fromisoformat(str(now_iso)[:10])
        except ValueError:
            pass
    return _dt.date.today()


def render_my_plate_text(data_view: Dict[str, Any], *, now_iso: Optional[str] = None,
                         fired_via: Optional[str] = None) -> Dict[str, Any]:
    """My Plate as a grouped list: the view's header, then each of its
    sections (PROMISED, PERSONAL — the widget's own groups) with the rows
    split by due bucket (Overdue / This week / Later / No date), every row
    carrying the SAME row number the widget's action grammar uses. Returns
    `{text, counts, groups}`; the saved-at line is NOT here (the delivery
    planner appends it last)."""
    today = _today(now_iso)
    lines: List[str] = []
    header = str(data_view.get("header") or "").strip()
    if header:
        lines.append(validated(header, fired_via=fired_via))
    counts: Dict[str, int] = {"promised": 0, "personal": 0, "rows": 0}
    groups: Dict[str, List[Any]] = {}
    for section in data_view.get("sections") or []:
        items = list(section.get("items") or [])
        if not items:
            continue
        title = str(section.get("title") or "").strip()
        key = "personal" if "PERSONAL" in title.upper() else "promised"
        counts[key] += len(items)
        counts["rows"] += len(items)
        lines.append("")
        lines.append(validated(f"**{title}**", content=True, fired_via=fired_via))
        buckets: Dict[str, List[Dict[str, Any]]] = {k: [] for k, _ in DUE_GROUPS}
        for item in items:
            buckets[_due_group(item, today)].append(item)
        for bucket, label in DUE_GROUPS:
            rows = buckets[bucket]
            if not rows:
                continue
            lines.append(validated(f"{label}:", fired_via=fired_via))
            for item in rows:
                lines.append(_row_line(item, fired_via))
                groups.setdefault(bucket, []).append(item.get("display_n", item.get("n")))
    return {"text": "\n".join(lines).strip() + "\n", "counts": counts, "groups": groups}


def _confirm_queue_prefixes() -> tuple:
    """The section titles whose rows are "to confirm": the queue's own shape
    sections (`brain_proposals._SHAPE_SECTION_LABEL`, "MONEY (N)" ...) and
    the meeting fold (`needs_review_queue.STAFF_SECTION_TITLE`). The appended
    asks - OVERDUE, HELD, STILL OPEN?, the candidates, SET ASIDE, THIS WEEK'S
    MOVES, the change feed - render but are NOT counted, so the push sentence
    never overstates (REVIEW_PARALLEL_B_2026-09-26 N-3)."""
    from brain_proposals import _SHAPE_SECTION_LABEL
    from needs_review_queue import STAFF_SECTION_TITLE
    return tuple(str(t).upper() for t in list(_SHAPE_SECTION_LABEL.values()) + [STAFF_SECTION_TITLE])


def render_staff_meeting_text(data_view: Dict[str, Any], *, now_iso: Optional[str] = None,
                              fired_via: Optional[str] = None) -> Dict[str, Any]:
    """Staff Meeting as a grouped list: the header, the tiles as one counts
    line, then every section in the view's own order (the confirm queue
    first, then the change feed, then this week's moves — the order the
    builder emits), rows numbered as the widget numbers them."""
    lines: List[str] = []
    header = str(data_view.get("header") or "").strip()
    if header:
        lines.append(validated(header, fired_via=fired_via))
    tiles = data_view.get("tiles")
    if isinstance(tiles, list) and tiles:
        bits = []
        for t in tiles:
            if isinstance(t, dict) and t.get("label") is not None:
                bits.append(f"{t.get('value', '')} {t.get('label')}".strip())
        if bits:
            lines.append(validated(" · ".join(bits), content=True, fired_via=fired_via))
    counts: Dict[str, int] = {"to_confirm": 0, "overdue": 0, "rows": 0, "sections": 0}
    groups: Dict[str, List[Any]] = {}
    confirm_prefixes = _confirm_queue_prefixes()
    for section in data_view.get("sections") or []:
        items = list(section.get("items") or [])
        if not items:
            continue
        title = str(section.get("title") or "").strip()
        counts["sections"] += 1
        counts["rows"] += len(items)
        up = title.upper()
        if "OVERDUE" in up:
            counts["overdue"] += len(items)
        elif up.startswith(confirm_prefixes):
            counts["to_confirm"] += len(items)
        lines.append("")
        lines.append(validated(f"**{title}**", content=True, fired_via=fired_via))
        for item in items:
            lines.append(_row_line(item, fired_via))
            groups.setdefault(title, []).append(item.get("display_n", item.get("n")))
    return {"text": "\n".join(lines).strip() + "\n", "counts": counts, "groups": groups}


# ---------------------------------------------------------------------------
# delivery
# ---------------------------------------------------------------------------

def push_sentence(task_id: str, counts: Dict[str, int], *, fired_via: Optional[str] = None) -> str:
    """The ONE push sentence for the surface, counts only, validated."""
    template = PUSH_LINES.get(task_id)
    if template is None:
        raise ValueError(f"no push line for {task_id!r}")
    safe = {k: int(v or 0) for k, v in (counts or {}).items()}
    return validated(template.format_map(_Counts(safe)), fired_via=fired_via)


class _Counts(dict):
    def __missing__(self, key):  # a count the surface did not compute reads as 0
        return 0


def plan_text_delivery(workspace_root: str, *, tools, page_rel: str, text: str,
                       task_id: str, push: str,
                       device_root: Optional[str] = None,
                       fired_via: Optional[str] = None) -> Dict[str, Any]:
    """How THIS run hands the surface over, decided from the tools it has —
    the same plan shape `inbox_helpers.plan_delivery` answers, for a surface
    whose text this module composed.

    `show_widget` present -> the widget branch (the driver's html goes to
    `show_widget`, as today). ABSENT -> the text branch: the message is the
    grouped list with the saved-at line LAST, delivered through
    `SendUserMessage` (or as the chat turn when that is absent too), the
    landed page copied into the container for `SendUserFile` when present,
    the page republish only when the workspace enables it (`fire_delivery`),
    and the push is the counts sentence. Nothing here sends or writes: it
    answers the calls to make and the flags for the receipt."""
    import fire_delivery as fd

    if device_root is None:
        device_root = str(os.environ.get("CR_DEVICE_WORKSPACE", "") or "").strip() or None
    names = _tool_names(tools)
    out: Dict[str, Any] = {"calls": [], "rows": [], "lines": []}
    widget_tool = widget_tool_name(tools)
    if widget_tool:
        out.update({"branch": "widget", "push": push,
                    "receipt_flags": {"widget_rendered": True, "widget_posted": True,
                                      "text_fallback": False, "push_planned": True}})
        out["calls"].append({"tool": widget_tool})
        return out
    closing = saved_at_line(page_rel, device_root)
    body_lines = [text.rstrip("\n"), "", closing] if text else [closing]
    flags = {"widget_rendered": True, "widget_posted": False, "text_fallback": True,
             "push_planned": True}
    page_path: Optional[str] = page_rel
    stage = None
    if device_root:
        import hashlib

        landed = _landed_page(workspace_root, page_rel)
        page_path = None
        if landed is not None:
            page_path = CONTAINER_PAGE_DIR + "/" + Path(str(page_rel)).name
            stage = {"tool": "Write", "file_path": page_path, "content": landed,
                     "sha256": hashlib.sha256(landed.encode("utf-8")).hexdigest()}
    if fd.artifact_publish_enabled(workspace_root):
        url = fd.stored_artifact_url(workspace_root, task_id)
        if page_path is None:
            out["rows"].append(fd.flag_row(task_id, fd.FLAG_NO_PAGE))
        elif ARTIFACT_TOOL in names and url:
            out["calls"].append(fd.republish_call(url, page_path))
            body_lines.insert(len(body_lines) - 1,
                              validated(fd.ARTIFACT_LINE.format(url=url), fired_via=fired_via))
            flags["artifact_url"] = url
            out["on_refused_row"] = fd.flag_row(task_id, fd.FLAG_REFUSED)
        else:
            out["rows"].append(fd.flag_row(
                task_id, fd.FLAG_NO_URL if ARTIFACT_TOOL in names else fd.FLAG_NO_TOOL))
    message = "\n".join(body_lines).strip() + "\n"
    if SEND_MESSAGE_TOOL in names:
        out["calls"].insert(0, {"tool": SEND_MESSAGE_TOOL, "text": message})
    else:
        out["calls"].insert(0, {"tool": "chat_turn", "text": message})
    if SEND_FILE_TOOL in names and page_path is not None:
        out["calls"].append({"tool": SEND_FILE_TOOL, "path": page_path})
    if stage is not None and any(c["tool"] in (SEND_FILE_TOOL, ARTIFACT_TOOL) for c in out["calls"]):
        out["calls"].insert(1, stage)
    out.update({"branch": "text", "message": message, "push": push,
                "receipt_flags": flags, "saved_at_line": closing})
    return out
