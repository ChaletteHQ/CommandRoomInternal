#!/usr/bin/env python3
"""The staff meeting and the health check — named entry points, for the
workspace access layer.

WHY THIS MODULE EXISTS (ORCH2, Night M3). `system-health/SKILL.md` and
`orchestrator-staff-meeting.md` carried five python blocks that opened the
customer's workspace in-process: the health verdict, the self-report's
substrate lines, the change feed and queue read, the staff meeting's window
read, and its lateness check. On a merged seat those blocks ran in a
container with no workspace. Each is now ONE `plan run_helper` line naming a
function here.

THE RULE EVERY FUNCTION HERE OBEYS (workspace_access review B-1, R-M2-6): it
reads or computes and writes nothing. Three places where the old block
called a function that CAN write are read here through the pure half the
function itself delegates to, never through a copy of its logic:

  * `task_alarm.dark_surfaces(..., record=False)` IS
    `_strip_private(classify_dark_surfaces(...))` — its own docstring says the
    two are identical when `record` is False — and the helper calls exactly
    that, so the render-once ledger writer is not reachable from this door.
  * `seq_health.detect_and_mark(ws)` with no `apply` IS `_detect(ws)` behind
    its phantom-path refusal; `detect_and_mark_gaps(ws)` IS `_detect_gaps(ws)`
    behind the same. The marker-writing branch is the weekly cleanup's.
  * `substrate_health.substrate_alarm_lines` SWEEPS resolved alerts on its
    way through (a file removal), so it is not a read and is not here: the
    health check asks it through the WRITE door (`plan run_writer`), and on a
    seat whose writer cannot be named the alarm lines are the one part of the
    self-report that does not render.

The staff meeting's widget and its receipt are the one-command driver, run
through the WRITE door since ORCH2 fix pass 1 (`run_staff_meeting_surface`,
below — the CLI leg `surface_drivers.py staff-meeting` ran in the cloud
session's own shell and drew an EMPTY card there, a false all-clear). Its
unnamed-speaker count line is a read here (`unnamed_speakers`). The one
receipt this surface writes in prose — the degrade branch, and the health
check's empty-queue line — is composed here (`plan_staff_receipt`).

3.10-safe: this module ships in the runtime manifest.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from morning_brief_helpers import _jsonable, answers, base_cwd, lateness_verdict

#: The scheduled task the lateness verb and the receipt serve.
TASK_ID = "staff-meeting"


def _installed_version() -> str:
    """The installed plugin's version, read where the old block read it (the
    plugin's own manifest beside this module), else the runtime version the
    access layer stamps on every helper process."""
    import os

    manifest = (Path(__file__).resolve().parent.parent.parent
                / ".claude-plugin" / "plugin.json")
    try:
        version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
        if version:
            return str(version)
    except (OSError, ValueError):
        pass
    return str(os.environ.get("CR_RUNTIME_VERSION") or "")


def _parse_now(now_iso: Optional[str]):
    import datetime as _dt

    if not now_iso:
        return None
    try:
        return _dt.datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
    except ValueError:
        return None


@answers
def lateness(workspace_root: str, *, fired_via: str = "manual",
             env_date: str = "", now: Optional[Any] = None) -> Dict[str, Any]:
    """The Staff Meeting chat's lateness verdict (Phase 2.9), every row the
    check writes handed back in `pending_rows`."""
    return lateness_verdict(workspace_root, TASK_ID, fired_via=fired_via,
                            env_date=env_date, now=now)


@answers
def staff_window(workspace_root: str, *,
                 now_iso: Optional[str] = None) -> Dict[str, Any]:
    """`{window, feed, n_open, queue}` — the staff meeting's two halves.

    The window is ONE read, in code (WRAPSTAFF1 4.4): the marker and the
    words for it both come from `surface_drivers.staff_meeting_window`; the
    feed is `change_feed.changes_since` from its `since_ts`; the queue is
    `brain_proposals.rank_proposals(load_open_proposals(ws, "staff-meeting"))`.
    """
    from brain_proposals import load_open_proposals, rank_proposals
    from change_feed import changes_since
    from surface_drivers import staff_meeting_window

    window = staff_meeting_window(workspace_root, now_iso=now_iso or None)
    feed = changes_since(workspace_root, window["since_ts"])
    queue = rank_proposals(load_open_proposals(workspace_root, TASK_ID))
    return _jsonable({"window": window, "feed": feed, "n_open": len(queue),
                      "queue": queue})


@answers
def unnamed_speakers(workspace_root: str) -> Dict[str, Any]:
    """`{n}` — the staff meeting's ONE unnamed-speaker count line (PID1 §0-4):
    `identity_reconcile.count_open_annotations(ws)`, a separate read from the
    queue load, asked where the workspace is. `n == 0` renders nothing."""
    from identity_reconcile import count_open_annotations

    return {"n": int(count_open_annotations(workspace_root))}


@answers
def self_report(workspace_root: str, *, since_iso: str = "",
                surface: str = "system-health") -> Dict[str, Any]:
    """`{feed, queue}` — 'what did you change' / 'what's waiting on me': the
    change feed's lines since `since_iso` and the ranked open queue as the
    health check sees it (`load_open_proposals(ws, "system-health")`)."""
    from brain_proposals import load_open_proposals, rank_proposals
    from change_feed import changes_since

    feed = changes_since(workspace_root, since_iso)
    queue = rank_proposals(load_open_proposals(workspace_root, surface))
    return _jsonable({"feed": feed["lines"], "queue": queue})


@answers
def health_report(workspace_root: str, *,
                  task_records: Optional[List[Dict[str, Any]]] = None,
                  backend: Optional[str] = None,
                  now_iso: Optional[str] = None,
                  records_known: bool = True) -> Dict[str, Any]:
    """The health check's verdict (Step 2) — one answer.

    `task_records` is the NORMALISED scheduler listing, or null when Step 1
    said no scheduler is available. JSON cannot tell "no scheduler" from "an
    empty one" once a caller drops the key, so `records_known: false` is the
    explicit spelling of the former; `None` and `[]` stay two different
    registry states exactly as `health_verdict` reads them.
    """
    import schedule_refresh as sr
    import task_alarm as ta
    import task_watchdog as tw

    import os

    records = task_records if records_known else None
    now = _parse_now(now_iso)
    # HEALTH3 MUST 1 (F-T2-7): this seat's folder, forwarded by the door. With
    # it, the verdict, the drift read and the dark table all see THIS seat's
    # records only; another computer's copies are named beside, never counted.
    device_path = str(os.environ.get("CR_DEVICE_WORKSPACE", "")).strip() or None
    own = tw.split_seat_records(records, device_path)["own"]
    verdict = tw.health_verdict(workspace_root, task_records=records,
                                backend=backend, now=now,
                                device_path=device_path)
    installed = _installed_version()
    stamps = tw.check_prompt_versions(own, installed)
    drift = sr.prompt_body_drift(own, plugin_version=installed)
    dark = ta._strip_private(ta.classify_dark_surfaces(
        workspace_root, now=now or ta._now_local(), task_records=own))
    return _jsonable({
        "vantage": verdict["vantage"],
        "summary_line": verdict["summary_line"],
        "lines": verdict["lines"],
        "info_lines": verdict["info_lines"],
        "reports": verdict["reports"],
        "stale_prompts": drift,
        "stale_stamps": [f for f in stamps if f.get("stale")],
        "dark_surfaces": dark,
    })


@answers
def health_substrate(workspace_root: str) -> Dict[str, Any]:
    """Step 3b's self-report lines, every READ half of them.

    `integ` (`org_writer.count_failing_orgs`), `n_waiting`
    (`load_open_proposals(ws, "system-health")`), `git_lint`
    (`substrate_health.check_git_in_drive`), the duplicate-seq count and the
    missing-entry REPORT (`seq_gap`) — `seq_health`'s read-only halves, the
    same ones `detect_and_mark` / `detect_and_mark_gaps` return without
    `apply`. The missing-entry SENTENCES are composed from `seq_gap` by
    `seq_health` alone, on the caller's side (pure over the report). The
    substrate ALARM lines are not here: they sweep resolved alerts as they
    run, so they come through `plan run_writer
    substrate_health:substrate_alarm_lines`.
    """
    import seq_health
    from brain_proposals import load_open_proposals
    from org_writer import count_failing_orgs
    from substrate_health import check_git_in_drive

    events = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if events.exists():
        seq_rep = seq_health._detect(workspace_root)
        seq_gap = seq_health._detect_gaps(workspace_root)
    else:
        refused = f"no events.jsonl under {str(workspace_root)!r}"
        seq_rep = {"n_duplicate_seqs": 0, "n_new": 0, "new": [],
                   "marked": False, "refused": refused}
        seq_gap = seq_health._empty_gap_report(refused=refused)
    return _jsonable({
        "integ": count_failing_orgs(workspace_root),
        "n_waiting": len(load_open_proposals(workspace_root, "system-health")),
        "git_lint": check_git_in_drive(workspace_root),
        "n_new_duplicate_seqs": seq_rep["n_new"],
        # The missing-entry REPORT, not its sentences: `seq_health` is the
        # one module that composes those (LEDGERFENCE1), and it does so over
        # this report wherever the caller runs — it reads no workspace.
        "seq_gap": seq_gap,
    })


@answers
def plan_staff_receipt(workspace_root: str, *,
                       fired_via: Optional[str] = None,
                       surfaced: int = 0,
                       extra_data: Optional[Dict[str, Any]] = None
                       ) -> Dict[str, Any]:
    """`{rows}` — the staff meeting's `pack_run` on the two paths that write
    it in prose: the degrade branch (the driver never ran) and the health
    check's empty queue. `receipts.log_receipt(workspace_root,
    "staff-meeting", ...)` runs here with its append held; ONE
    `plan append_jsonl` lands the row it writes."""
    from inbox_helpers import _captured_appends
    from receipts import log_receipt

    kwargs: Dict[str, Any] = {"surfaced": int(surfaced),
                              "extra_data": dict(extra_data or {}) or None}
    if fired_via:
        kwargs["fired_via"] = fired_via
    with _captured_appends() as captured:
        log_receipt(workspace_root, TASK_ID, **kwargs)
    return {"rows": _jsonable(list(captured))}


# ---------------------------------------------------------------------------
# Phase 5 — the one-command driver, through the WRITE door
# ---------------------------------------------------------------------------

def run_staff_meeting_surface(workspace_root: str, *, page: int = 1,
                              fired_via: Optional[str] = None,
                              rerun_of: Optional[str] = None,
                              page_size: Optional[int] = None,
                              now_iso: Optional[str] = None,
                              tools: Optional[List[Any]] = None,
                              device_root: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER (on `RUN_WRITER_ALLOWLIST` only) — the Staff Meeting
    one-command driver, `surface_drivers.run_surface("staff-meeting", ...)`,
    the function `surface_drivers.py staff-meeting --fired-via ...` has always
    called (ORCH2 fix pass 1, review F-1).

    It is a writer because, on page 1, the call runs the watch-expiry pass,
    freezes the page-set (PAGESNAP), persists the audit page, marks the
    rehomed overdue question asked, and — with `fired_via` — writes the
    surface's ONE `pack_run` receipt in the same call (FB-7: the render and
    the receipt cannot be split). Through the door the receipt is stamped
    with the writer the door forwarded. The same shape as My Plate's
    `run_my_plate_surface` and ORCH2C's Waiting On driver.

    The answer is the transport the model relays: `html` (to `show_widget`
    verbatim), `pagination`, `receipt`, `maintenance_line` when there is one,
    and `page_rel` — the audit page named workspace-relative. A stale mount
    comes back as `{refused: "mount_stale", lines}` — the driver's own
    sentences — never as a crash.
    """
    import surface_drivers as sd

    ws = Path(workspace_root)
    kwargs: Dict[str, Any] = {"page": 1 if page is None else int(page),
                              "page_size": page_size, "now_iso": now_iso,
                              "fired_via": fired_via,
                              "rerun_of": rerun_of or None}
    if tools is not None:
        # S-13 (PARALLEL-B lane A): see plate_helpers.run_my_plate_surface —
        # the same text form for a run with no widget tool; the confirm queue
        # with row numbers, then the change feed, then this week's moves.
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
        composed = st.render_staff_meeting_text(view, now_iso=now_iso, fired_via=fired_via)
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
