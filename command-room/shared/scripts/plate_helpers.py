#!/usr/bin/env python3
"""What's on my plate — the named entry points, for the workspace access layer.

WHY THIS MODULE EXISTS (ORCH2, Night M3). `show-my-list/SKILL.md` and
`orchestrator-my-plate.md` carried five python blocks that opened the
customer's workspace in-process: the retired discuss list's open-item read,
its page render, its fire-marker, the mute ledger read, and the My Plate
chat's lateness check. On a merged seat those blocks ran in a container that
holds no workspace. Each is now ONE `plan run_helper` line naming a function
here, answered with JSON by the process that holds the data.

THE RULE EVERY FUNCTION HERE OBEYS (workspace_access review B-1, R-M2-6): it
reads or computes and writes nothing. A row a delegated writer would append
comes back in `pending_rows` / `rows` for the caller's `plan append_jsonl`
(`morning_brief_helpers`' capture, the same two doors held); the rendered
page comes back in the envelope for `plan write` and `show_widget`.

The My Plate CHAT's own page and receipt are the one-command driver, run
through the WRITE door (`run_my_plate_surface` below). Its Step 0 fold gate
(`fold_gate`) and its degrade-tier receipt (`plan_plate_receipt`) are read
helpers here since ORCH2 fix pass 1: in the empty cloud session the old
in-process gate always answered "not folded", and the old in-process receipt
landed in a folder that is not the workspace.

3.10-safe: this module ships in the runtime manifest.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from morning_brief_helpers import _jsonable, answers, base_cwd, lateness_verdict

#: The scheduled task the lateness verb serves.
TASK_ID = "my-plate"

#: The skill whose fossil list and mute ledger live here.
LIST_SKILL = "show-my-list"

WIDGET_DIR_REL = "_hq/.system/widgets"


def _now_iso(now_iso: Optional[str]) -> str:
    return (now_iso or "").strip() or _dt.datetime.now(_dt.timezone.utc).isoformat()


def _events(workspace_root: str) -> List[Dict[str, Any]]:
    """The ledger for an OWNER surface — `events_io.load_events_owner_scoped`,
    the defensive loader behind the shard reader (malformed lines skipped,
    never a crash on one bad row; the owner sees every row). The raw read
    stays inside the allowlisted reader, never in a new module (the READER1
    firewall re-route)."""
    from events_io import load_events_owner_scoped

    events, _skipped = load_events_owner_scoped(workspace_root)
    return list(events)


@answers
def fold_gate(workspace_root: str, *, task_id: str = TASK_ID) -> Dict[str, Any]:
    """`{folded, line}` — is this chat folded into the morning brief today?
    (Step 0, FOLD1A.)

    The check is PER FIRE from the workspace's own state (the fold is
    reversible), so it has to run where the workspace is: in a session that
    holds no workspace `fold_is_active` answers False, and a folded seat would
    get the full plate instead of its one line. `line` is
    `schedule_config.folded_fire_line`, never retyped. ORCH2C's
    `commitments_helpers.fold_gate` shape, for this chat.
    """
    from schedule_config import fold_is_active, folded_fire_line

    try:
        folded = bool(fold_is_active(workspace_root, task_id))
    except Exception:  # noqa: BLE001 - an unreadable config is a LIVE chat,
        # which is the direction the fold's own module takes
        folded = False
    return {"folded": folded,
            "line": folded_fire_line(task_id) if folded else ""}


@answers
def lateness(workspace_root: str, *, fired_via: str = "manual",
             env_date: str = "", now: Optional[Any] = None) -> Dict[str, Any]:
    """The My Plate chat's lateness verdict (Phase 2.9) with every row the
    check writes handed back in `pending_rows`."""
    return lateness_verdict(workspace_root, TASK_ID, fired_via=fired_via,
                            env_date=env_date, now=now)


@answers
def discuss_list(workspace_root: str, *,
                 now_iso: Optional[str] = None) -> Dict[str, Any]:
    """`{open_items, open_count}` — the retired discuss list, drain-only.

    Exactly the old block's reading: every `commitment_to_discuss` event not
    closed by a `commitment_resolved` (canonical, v3.11.4+: `data.commitment_id`
    is the discuss event's seq) or the legacy `thread_resolved` shape, and not
    muted by a LIVE `chat_dismissal` — the mute ledger's own liveness rule
    (`mute_ledger.active_dismissal_target_ids`, v4.6.0 S4: the TTL the
    dismissal was written with, and any later unmute).
    """
    from mute_ledger import active_dismissal_target_ids

    events = _events(workspace_root)
    discuss_items = [e for e in events if e.get("type") == "commitment_to_discuss"]
    resolved_seqs = set()
    for e in events:
        et = e.get("type")
        d = e.get("data") or {}
        if et == "commitment_resolved":
            cid = d.get("commitment_id") or d.get("id") or d.get("target_id")
            if cid is not None:
                resolved_seqs.add(cid)
        elif et == "thread_resolved":
            tid = d.get("target_id") or d.get("id") or d.get("thread_id")
            if tid is not None:
                resolved_seqs.add(tid)
    dismissed_seqs = active_dismissal_target_ids(events, _now_iso(now_iso))
    open_items = [e for e in discuss_items
                  if e.get("seq") not in resolved_seqs
                  and e.get("seq") not in dismissed_seqs]
    return {"open_items": _jsonable(open_items), "open_count": len(open_items)}


@answers
def live_mutes(workspace_root: str, *,
               now_iso: Optional[str] = None) -> Dict[str, Any]:
    """`{mutes}` — every live mute, oldest first: `mute_ledger.live_mutes`
    over the owner's ledger (`_events` — the owner-scoped defensive read)."""
    from mute_ledger import live_mutes as _live_mutes

    return {"mutes": _jsonable(_live_mutes(_events(workspace_root),
                                           _now_iso(now_iso)))}


@answers
def render_list_page(workspace_root: str, data_view: Dict[str, Any], *,
                     wrapper: str = "fragment",
                     name_hint: str = LIST_SKILL) -> Dict[str, Any]:
    """`{html, page_rel, bytes_len, pending_rows}` — the list's widget,
    rendered where the data is.

    The same renderer and the same wrapper-contract validator
    `widget_transport.render_and_persist` runs, with nothing persisted: the
    caller lands the page at `page_rel` with `plan write` and hands `html` to
    `show_widget`. The render's own audit row, if it writes one, is held and
    handed back in `pending_rows`.
    """
    from chat_output_renderer import (render_chat_output_widget,
                                      validate_rendered_widget)
    from inbox_helpers import _captured_appends
    from widget_transport import _safe_filename

    with _captured_appends() as captured, base_cwd():
        html = render_chat_output_widget(dict(data_view or {}), wrapper=wrapper)
        validate_rendered_widget(html, surface=(data_view or {}).get("surface"))
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    page_rel = f"{WIDGET_DIR_REL}/{_safe_filename(name_hint or 'widget')}_{stamp}.html"
    return {"html": html, "page_rel": page_rel, "bytes_len": len(html),
            "wrapper": wrapper, "pending_rows": _jsonable(list(captured))}


@answers
def plan_list_marker(workspace_root: str, *, surfaced: int = 0,
                     duration_ms: int = 0, kind: str = "list",
                     fired_via: Optional[str] = "manual") -> Dict[str, Any]:
    """`{rows}` — the list's fire-marker, the `pack_run` apply-choices reads
    to tell which surface a click came from. `log_pack_run.log_pack_run` runs
    here with its append held; the row it writes comes back for ONE
    `plan append_jsonl`."""
    from inbox_helpers import _captured_appends
    from log_pack_run import log_pack_run

    with _captured_appends() as captured:
        log_pack_run(workspace_root=workspace_root, kind=kind,
                     surfaced=int(surfaced), duration_ms=int(duration_ms),
                     source_skill=LIST_SKILL, fired_via=fired_via)
    return {"rows": _jsonable(list(captured))}


@answers
def plan_plate_receipt(workspace_root: str, *,
                       fired_via: Optional[str] = None,
                       surfaced: int = 0,
                       duration_ms: Optional[int] = None,
                       late_tier: Optional[str] = None,
                       extra_data: Optional[Dict[str, Any]] = None
                       ) -> Dict[str, Any]:
    """`{rows}` — the My Plate chat's `pack_run` on the ONE path that writes
    it outside the driver: the `degrade` tier (Phase 8), where the surface is
    not rendered and the Phase 9 driver never runs. `receipts.log_receipt(
    workspace_root, "my-plate", ...)` runs here with its append held; ONE
    `plan append_jsonl` lands the row it writes. Every normal fire's receipt
    is written INSIDE the driver call (FB-7) and never here."""
    from inbox_helpers import _captured_appends
    from receipts import log_receipt

    kwargs: Dict[str, Any] = {"surfaced": int(surfaced),
                              "extra_data": dict(extra_data or {}) or None}
    if fired_via:
        kwargs["fired_via"] = fired_via
    if duration_ms is not None:
        kwargs["duration_ms"] = int(duration_ms)
    if late_tier:
        kwargs["late_tier"] = late_tier
    with _captured_appends() as captured:
        log_receipt(workspace_root, TASK_ID, **kwargs)
    return {"rows": _jsonable(list(captured))}


# ---------------------------------------------------------------------------
# Phase 9 — the one-command driver, through the WRITE door
# ---------------------------------------------------------------------------

def run_my_plate_surface(workspace_root: str, *, page: int = 1,
                         status_rows: Optional[List[Dict[str, Any]]] = None,
                         personal_cap: Optional[int] = None,
                         fired_via: Optional[str] = None,
                         rerun_of: Optional[str] = None,
                         page_size: Optional[int] = None,
                         now_iso: Optional[str] = None,
                         tools: Optional[List[Any]] = None,
                         device_root: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER (on `RUN_WRITER_ALLOWLIST` only) — the My Plate one-command
    driver, `surface_drivers.run_surface("my-plate", ...)`, the function
    `surface_drivers.py my-plate --fired-via ...` has always called.

    It is a writer because the view it builds mints the plate's display
    numbers under the writer lock (PLATENUM1), freezes the page-set
    (PAGESNAP), persists the audit page and — on page 1 with `fired_via` —
    writes the surface's ONE `pack_run` receipt in the same call (FB-7: the
    render and the receipt cannot be split). Through the door the receipt is
    stamped with the writer the door forwarded. The same shape ORCH2C gave
    the Waiting On driver.

    The answer is the transport the model relays: `html` (to `show_widget`
    verbatim), `pagination`, `receipt`, and `page_rel` — the audit page named
    workspace-relative. A stale mount comes back as `{refused: "mount_stale",
    lines}` — the driver's own sentences — never as a crash.

    `tools` (S-13, PARALLEL-B lane A, 2026-09-24): the run's tool list
    (`{name}` each, or bare names). When handed in, the answer ALSO carries
    the delivery: with `show_widget` present, `widget_posted: true` and the
    one push sentence; with it ABSENT (the merged app's scheduled shape), the
    grouped-list `text` — the same rows the widget shows, by group then
    Overdue / This week / Later / No date, row numbers kept — ending in the
    saved-at line (the page's path on the customer's computer, from
    `device_root` / `CR_DEVICE_WORKSPACE`), the `calls` to make in order, the
    `push` sentence naming counts, `page_pc_path`, `text_fallback: true` /
    `widget_posted: false`; the receipt written inside this call carries the
    same flags plus the landed page's sha. A call WITHOUT `tools` is the
    legacy caller: byte-identical answer, page and receipt.
    """
    from pathlib import Path

    import surface_drivers as sd

    ws = Path(workspace_root)
    kwargs: Dict[str, Any] = {"page": 1 if page is None else int(page),
                              "page_size": page_size, "now_iso": now_iso,
                              "status_rows": status_rows,
                              "fired_via": fired_via,
                              "rerun_of": rerun_of or None}
    if personal_cap is not None:
        kwargs["personal_cap"] = int(personal_cap)
    if tools is not None:
        from inbox_helpers import widget_tool_name
        _widget = widget_tool_name(tools) is not None
        kwargs["extra_receipt_data"] = {"receipt_flags": {
            "widget_rendered": True, "widget_posted": _widget,
            "text_fallback": not _widget, "push_planned": True}}
    try:
        with base_cwd():
            transport = sd.run_surface(TASK_ID, str(ws), **kwargs)
    except sd.MountStaleError as stale:
        return {"refused": "mount_stale",
                "lines": [str(x) for x in (getattr(stale, "lines", None)
                                           or [str(stale)])]}
    out: Dict[str, Any] = {"html": transport.get("html") or ""}
    path = transport.get("path")
    if path:
        try:
            out["page_rel"] = Path(str(path)).resolve().relative_to(
                ws.resolve()).as_posix()
        except (OSError, ValueError):
            out["page_rel"] = Path(str(path)).name
    out["bytes_len"] = len(out["html"])
    for key in ("pagination", "receipt", "maintenance_line", "over_budget",
                "text"):
        if key in transport:
            out[key] = transport[key]

    # S-13 (PARALLEL-B lane A, 2026-09-24): the text form for a run whose
    # tool list has NO widget tool — the merged app's scheduled shape. Only
    # when the orchestrator handed `tools` in; a call without it is a legacy
    # caller and its answer, page and receipt are byte-identical to before.
    if tools is not None:
        import surface_text as st

        page_rel = out.get("page_rel") or ""
        # The transport persists the fragment behind a UTF-8 BOM (Bug #40);
        # the landed bytes minus that mark must BE the html the answer carries,
        # and the receipt's `page_sha256` is the sha of the landed bytes.
        landed = st._landed_page(str(ws), page_rel) if page_rel else None
        out["page_verified"] = bool(landed is not None
                                    and landed.lstrip("\ufeff") == out["html"])
        out["page_sha256"] = (__import__("hashlib").sha256(landed.encode("utf-8")).hexdigest()
                              if landed is not None else None)
        out["page_bytes"] = len(landed.encode("utf-8")) if landed is not None else None
        view = transport.get("view") if isinstance(transport.get("view"), dict) else {}
        composed = st.render_my_plate_text(view, now_iso=now_iso, fired_via=fired_via)
        push = st.push_sentence(TASK_ID, composed["counts"], fired_via=fired_via)
        plan = st.plan_text_delivery(str(ws), tools=tools, page_rel=page_rel,
                                     text=composed["text"], task_id=TASK_ID,
                                     push=push, device_root=device_root,
                                     fired_via=fired_via)
        out["counts"] = composed["counts"]
        out["push"] = push
        out["receipt_flags"] = plan["receipt_flags"]
        out["widget_posted"] = bool(plan["receipt_flags"].get("widget_posted"))
        out["text_fallback"] = bool(plan["receipt_flags"].get("text_fallback"))
        out["calls"] = plan["calls"]
        out["rows"] = plan.get("rows") or []
        if plan["branch"] == "text":
            out["text"] = plan["message"]
            out["list_text"] = composed["text"]
            out["saved_at_line"] = plan["saved_at_line"]
            root = device_root or str(__import__("os").environ.get("CR_DEVICE_WORKSPACE", "") or "").strip()
            if root and page_rel:
                import deliverables as dl
                out["page_pc_path"] = dl.opener_spelling(dl.device_join(root, page_rel))
            else:
                out["page_pc_path"] = None
    return _jsonable(out)
