#!/usr/bin/env python3
"""The Waiting On chain's named entry points, for the workspace access layer.

WHY THIS MODULE EXISTS (ORCH2C, Night M3 lane 6). `orchestrator-commitments.md`
- the scheduled Waiting On chat, and the morning's one bulk reconciliation
pass - did its work in inline `python3 -c` bodies that opened the customer's
ledger in the same process. On a legacy seat the helpers and the files share a
filesystem, so that works. On a merged seat the helpers run in a cloud
container and the files are on the customer's own computer, so every one of
those bodies opens nothing and the chain stops. The access layer's answer is
two doors: `run_helper` (read and compute, one NAMED function, called where the
data is) and `run_writer` (the writer family, with its own list and an identity
precondition). This module is the Waiting On chain's half of both.

THE RULE, BY DOOR.

  * Every function here EXCEPT the two writers below is a READ: it is on
    `RUN_HELPER_ALLOWLIST`, it passes the seven-frame transitive write-scan,
    and it writes nothing - not through a gated writer either. A row it needs
    written is COMPOSED and handed back (`rows`, `pending_rows`, the receipt
    `row`) for the caller's `plan append_jsonl`; a page is handed back as
    `html` + `page_rel` for the caller's `plan write`.
  * `apply_cru_writes` and `run_waiting_on_surface` are the two WRITERS. They
    are on `RUN_WRITER_ALLOWLIST` and never on the helper list (guard G74),
    so the door asks who is writing before either runs.
    `apply_cru_writes` performs exactly the closes and per-person receipts a
    CRU plan names, through the single closure path (`commitment_state.
    close_commitment` / `mark_partial_received`), in the plan's order, with
    the same refusal handling the inline bodies had - nothing else.
    `run_waiting_on_surface` is the one-command driver itself
    (`surface_drivers.run_surface`), because the view it builds mints the
    plate's display numbers under the writer lock - a write no read door may
    reach - and because the driver's render and its receipt are one call by
    design (FB-7).

COMPOSING IS NOT HAND-ROLLING. Every row is built by the module that owns its
shape - `cru_match.build_pending_review_event`, `inbox_helpers.plan_fire_receipt`
(which is `receipts`' own parts) - and `tests/run_orch2c_test.py` compares each
composed row, each page and each planned write against what the BASE commit's
inline bodies produced for the same inputs on the same fixture (the golden
captured at 7862214c before this module existed).

Every function takes `workspace_root` first, returns JSON-serialisable data,
and is callable with `workspace_root` alone (the allow-list control drives
every listed name that way). 3.10-safe: this module ships in the runtime
manifest and runs on the sandbox VM's interpreter.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

#: The scheduler/receipt task id this chain fires under (CTS1). Events keep
#: `source_skill='commitments'` - the dispatch family - on purpose.
TASK_ID = "waiting-on"
SOURCE_SKILL = "commitments"
SURFACE = "waiting-on"
WIDGET_DIR_REL = "_hq/.system/widgets"

#: FRP1's two knobs for the commitment family (orchestrator-commitments.md
#: § First-Run Personalization). One place, so the read and the first-fire
#: save cannot disagree.
CONFIG_DEFAULTS = {"group_by": "person", "chase_tone": "friendly"}

#: The two operations a CRU plan may name. Anything else in a `writes` list is
#: refused by the writer and recorded, never interpreted.
WRITE_OPS = ("close", "partial")

#: The sentence the text form ends with when the widget tool is absent from
#: the session (R-RW2-7 (a); SPEC_NIGHTM3 I-12, applied to this chain by
#: §4b) - the inbox's sentence, naming the folder in words rather than by a
#: path (R-DELIV1-2). A caller holding the saved page's own sentence passes it
#: as `saved_line` instead.
WIDGET_ABSENT_LINE = ("The live view could not be shown in this chat, so here "
                      "is the list; the full page is saved in your Command "
                      "Room folder.")


#: A connector or harness tool id (`mcp__<server>__<tool>`). Never on a
#: customer line.
_TOOL_ID_RE = re.compile(r"mcp__[A-Za-z0-9_-]+__")


def _events_path(workspace_root: str) -> str:
    return str(Path(workspace_root) / "_hq" / "data" / "events.jsonl")


#: The plugin root this module ships in (`shared/scripts/..`/..). A render on
#: a seat whose files are local runs FROM here, as the base CLI always did.
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]


class _base_cwd:
    """Run a render from the plugin root on every seat that is NOT a VM seat
    (R-ORCH2C-1, REVIEW_ORCH2C HIGH-1).

    WHY. The chat-email backstop (`turn_backstop._resolve_workspace_root`)
    finds the workspace from the process's working directory. The base CLI
    ran from the plugin root, found nothing and wrote no `gate_ran` row; the
    door runs every verb with the workspace as its working directory, so the
    same render would land three audit rows per fire on a legacy seat - and
    the maintenance note would then call a weekday-only chat "quiet for 3
    days" after every weekend. Legacy seats stay byte-for-byte the base: the
    render runs from where the CLI ran. On a VM seat (`CR_HOST_MODE=vm`) the
    working directory is left alone and the audit lands, as it does for every
    migrated chain there.
    """

    def __enter__(self):
        self.prev = None
        if os.environ.get("CR_HOST_MODE") != "vm":
            self.prev = os.getcwd()
            os.chdir(str(_PLUGIN_ROOT))
        return self

    def __exit__(self, *exc):
        if self.prev is not None:
            os.chdir(self.prev)
        return False


# ---------------------------------------------------------------------------
# Step 0 - the FOLD1A gate
# ---------------------------------------------------------------------------

def fold_gate(workspace_root: str, *, task_id: str = TASK_ID) -> Dict[str, Any]:
    """`{folded, line}` - is this chat folded into the morning brief today?

    The check is PER FIRE from the workspace's own state (the fold is
    reversible), so it has to run where the workspace is. `line` is
    `schedule_config.folded_fire_line`, never retyped.
    """
    from schedule_config import fold_is_active, folded_fire_line

    try:
        folded = bool(fold_is_active(workspace_root, task_id))
    except Exception:  # noqa: BLE001 - an unreadable config is a LIVE chat,
        # which is the direction the fold's own module takes
        folded = False
    return {"folded": folded,
            "line": folded_fire_line(task_id) if folded else ""}


# ---------------------------------------------------------------------------
# Phase 2.9 - the clock and the lateness tier
# ---------------------------------------------------------------------------

def lateness(workspace_root: str, *, task_id: str = TASK_ID,
             fired_via: str = "manual", env_date: str = "",
             now: Optional[Any] = None) -> Dict[str, Any]:
    """This fire's lateness verdict, computed and NOT recorded.

    The inbox chain's verb, for this task id: `check_lateness(emit=False)`
    with the clock record and the note/degrade telemetry row CAPTURED and
    handed back in `pending_rows` for the caller's `plan append_jsonl`.
    """
    import inbox_helpers

    return inbox_helpers.lateness(workspace_root, task_id=task_id,
                                  fired_via=fired_via, env_date=env_date,
                                  now=now)


# ---------------------------------------------------------------------------
# Phase 2 - setup reads
# ---------------------------------------------------------------------------

def setup(workspace_root: str, *,
          tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Phase 2's reads in one call: who the user is, the family's two knobs
    (and whether this is the first fire), and the mail and calendar seams
    resolved off the tools this session actually has.

    One call rather than four because each is a device round trip on a
    merged seat. Never raises: an unresolved user comes back as `""` with the
    reason beside it, and the CRU phases decide what that means for them.
    """
    import inbox_helpers
    from skill_config_writer import get_config, is_configured

    out: Dict[str, Any] = {}
    try:
        from primary_user import resolve_primary_user
        out["primary_user"] = resolve_primary_user(workspace_root)
    except Exception as exc:  # noqa: BLE001
        out["primary_user"] = ""
        out["primary_user_error"] = f"{type(exc).__name__}: {exc}"
    try:
        out["config"] = dict(get_config(workspace_root, SOURCE_SKILL,
                                        dict(CONFIG_DEFAULTS)))
        out["configured"] = bool(is_configured(workspace_root, SOURCE_SKILL))
    except Exception as exc:  # noqa: BLE001
        out["config"] = dict(CONFIG_DEFAULTS)
        out["configured"] = True   # never offer the first-run row on a guess
        out["config_error"] = f"{type(exc).__name__}: {exc}"
    try:
        out["seams"] = inbox_helpers.mail_seams(workspace_root,
                                                tools=list(tools or []))
    except Exception as exc:  # noqa: BLE001
        out["seams"] = {}
        out["seams_error"] = f"{type(exc).__name__}: {exc}"
    return out


def resolve_people(workspace_root: str, *,
                   emails: Optional[List[str]] = None) -> Dict[str, Any]:
    """`{by_email: {email: person_id}, unresolved: [...]}` - the lookup the
    CRU phases need before they can score a message.

    The instructions say "resolve each address via entities.json /
    aliases.json"; on a merged seat those files are on the customer's
    computer, so the lookup runs there. Case-insensitive on the address, and
    an address that matches nobody is listed rather than guessed.
    """
    from entities_io import entities_collection

    index: Dict[str, str] = {}
    error = ""
    try:
        path = Path(workspace_root) / "_hq" / "data" / "entities.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for person in entities_collection(data, "people"):
            email = str((person or {}).get("email") or "").strip().lower()
            pid = str((person or {}).get("id") or "")
            if email and pid:
                index.setdefault(email, pid)
    except Exception as exc:  # noqa: BLE001 - an unreadable roster resolves
        # nobody; the phases count unresolvable senders, they never crash
        error = f"{type(exc).__name__}: {exc}"
    by_email: Dict[str, str] = {}
    unresolved: List[str] = []
    for raw in emails or []:
        key = str(raw or "").strip().lower()
        if not key:
            continue
        if key in index:
            by_email[key] = index[key]
        else:
            unresolved.append(key)
    out: Dict[str, Any] = {"by_email": by_email, "unresolved": unresolved}
    if error:
        out["error"] = error
    return out


# ---------------------------------------------------------------------------
# Phase 2.5 - the window
# ---------------------------------------------------------------------------

def cru_window(workspace_root: str, *, now_iso: Optional[str] = None,
               days: int = 7) -> Dict[str, Any]:
    """`{window_start, last_fire, open_count, open_ids}` - how far back this
    fire's mail and calendar reads go.

    `max(last Waiting On fire, now - days)`, reading BOTH task ids (CTS1: the
    first post-split fire must see the last pre-split `commitments` fire)
    through the shared receipt reader, which parses every legacy receipt shape.
    """
    from cru_match import load_open_commitments
    from receipts import last_receipt_times

    opens = load_open_commitments(_events_path(workspace_root))
    times = last_receipt_times(workspace_root, [TASK_ID, "commitments"])
    last = max((t for t in times.values() if t), default=None)
    now = None
    if now_iso:
        try:
            now = _dt.datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
        except ValueError:
            now = None
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    floor = now - _dt.timedelta(days=int(days))
    start = floor
    if last is not None:
        last_aware = last if last.tzinfo else last.replace(tzinfo=_dt.timezone.utc)
        start = max(last_aware, floor)
    return {"window_start": start.isoformat(),
            "last_fire": last.isoformat() if last is not None else None,
            "open_count": len(opens),
            "open_ids": sorted(str((o.get("data") or {}).get("id")
                                   or o.get("id") or "") for o in opens)}


# ---------------------------------------------------------------------------
# Phases 2.5 / 2.7 - the CRU passes, planned
# ---------------------------------------------------------------------------

def plan_sent_cru(workspace_root: str, *,
                  sends: Optional[List[Dict[str, Any]]] = None,
                  user_person_id: str = "",
                  provider: Optional[str] = None,
                  fire_start: Optional[str] = None,
                  source_skill: str = SOURCE_SKILL) -> Dict[str, Any]:
    """Phase 2.5 - `{writes, rows, counters}` for the outbound sends since
    the last fire. Nothing is closed and nothing is written here.

    The matching is `cru_match.match_send_to_commitments` over the SAME open
    set the inline body loaded, with the same three fence layers (the send's
    own artifact key, `fire_start`, the send's own `ts`). What it would have
    done in-process it now PLANS: `writes` - the closes and the per-person
    receipts, in the order the body made them - for `run_writer
    commitments_helpers:apply_cru_writes`; `rows` - the review proposals,
    built by their own builder - for `plan append_jsonl` AFTER the writes,
    which is where the body appended them.
    """
    from connector_adapters.provenance import (primary_artifact_key,
                                               resolve_mail_provider)
    from cru_match import (build_pending_review_event, load_open_commitments,
                           match_send_to_commitments)

    writes: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    counters = {"n_sends": 0, "n_close": 0, "n_partial": 0, "n_pending": 0}
    sends = list(sends or [])
    if not sends:
        return {"writes": writes, "rows": rows, "counters": counters}
    opens = load_open_commitments(_events_path(workspace_root))
    resolved_provider = resolve_mail_provider(workspace_root, provider)
    for send in sends:
        counters["n_sends"] += 1
        key = primary_artifact_key(resolved_provider,
                                   str(send.get("message_id") or ""))
        results = match_send_to_commitments(
            open_commitments=opens,
            sender_person_id=user_person_id,
            recipient_person_ids=list(send.get("recipient_person_ids") or []),
            subject=send.get("subject"),
            body=send.get("body"),
            workspace_root=workspace_root,
            send_source_ref=key,
            exclude_captured_since=fire_start,
            send_ts=send.get("ts"),
        )
        for r in results:
            evidence = (f"Sent via native mail client at {send.get('ts')} — "
                        f"Subject: {send.get('subject')}")
            rec = r.get("recommendation")
            if rec == "auto_resolve":
                writes.append({"op": "close",
                               "commitment_id": r["commitment_id"],
                               "resolved_by": user_person_id,
                               "evidence": evidence,
                               "source_skill": source_skill,
                               "source_ref": key})
                counters["n_close"] += 1
            elif rec == "partial_received":
                for cp in r.get("matched_counterparty_ids") or []:
                    writes.append({"op": "partial",
                                   "commitment_id": r["commitment_id"],
                                   "received_by": user_person_id,
                                   "source_skill": source_skill,
                                   "counterparty_id": cp,
                                   "evidence": evidence,
                                   # PROV1 - the same send is this person's
                                   # receipt's pointer
                                   "source_ref": key})
                    counters["n_partial"] += 1
            elif rec == "pending_review":
                rows.append(build_pending_review_event(
                    commitment_id=r["commitment_id"],
                    primary_thread_id=r["primary_thread_id"],
                    source_skill=source_skill,
                    proposed_resolution="auto_resolve",
                    score=r["score"],
                    evidence=evidence,
                    next_seq=None,
                    title=r["title"],
                    has_completion_signal=r.get("has_completion_signal"),
                    evidence_ts=send.get("ts"),
                ))
                counters["n_pending"] += 1
    return {"writes": writes, "rows": rows, "counters": counters}


def plan_calendar_cru(workspace_root: str, *,
                      calendar_events: Optional[List[Dict[str, Any]]] = None,
                      user_person_id: str = "",
                      source_skill: str = SOURCE_SKILL) -> Dict[str, Any]:
    """Phase 2.7 - `{writes, rows, results, counters}` for the calendar events
    created or updated since the last fire. The calendar twin of
    `plan_sent_cru`: `cru_match.match_calendar_to_commitments` with the
    workspace passed (F-28 - the roster reader resolves a free-text
    counterparty against the entity graph READ-ONLY; nothing here or in the
    writer touches `entities.json`), planned instead of performed.

    A proposal's `evidence_ts` is the matched event's own `created_ts`.
    """
    from cru_match import (build_pending_review_event, load_open_commitments,
                           match_calendar_to_commitments)

    writes: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    counters = {"n_events": 0, "n_close": 0, "n_partial": 0, "n_pending": 0}
    events = [dict(e) for e in (calendar_events or [])]
    if not events:
        return {"writes": writes, "rows": rows, "results": [],
                "counters": counters}
    counters["n_events"] = len(events)
    created_by_id = {str(e.get("calendar_event_id") or ""): e.get("created_ts")
                     for e in events}
    opens = load_open_commitments(_events_path(workspace_root))
    results = match_calendar_to_commitments(
        open_commitments=opens, user_person_id=user_person_id,
        calendar_events=events, workspace_root=workspace_root)
    for r in results:
        rec = r.get("recommendation")
        if rec == "auto_resolve":
            cal = r.get("calendar_event_id")
            writes.append({"op": "close",
                           "commitment_id": r["commitment_id"],
                           "resolved_by": r.get("owner_id") or "",
                           "evidence": r.get("evidence") or "",
                           "source_skill": source_skill,
                           "source_ref": f"gcal:{cal}" if cal else None})
            counters["n_close"] += 1
        elif rec == "partial_received":
            cal = r.get("calendar_event_id")
            for cp in r.get("matched_counterparty_ids") or []:
                writes.append({"op": "partial",
                               "commitment_id": r["commitment_id"],
                               "received_by": r.get("owner_id") or "",
                               "source_skill": source_skill,
                               "counterparty_id": cp,
                               "evidence": r.get("evidence") or "",
                               "source_ref": f"gcal:{cal}" if cal else None})
                counters["n_partial"] += 1
        elif rec == "pending_review":
            rows.append(build_pending_review_event(
                commitment_id=r["commitment_id"],
                primary_thread_id=r["primary_thread_id"],
                source_skill=source_skill,
                proposed_resolution="auto_resolve",
                score=r["score"],
                evidence=r.get("evidence") or "",
                next_seq=None,
                title=r["title"],
                has_completion_signal=r.get("has_completion_signal"),
                evidence_ts=(created_by_id.get(str(r.get("calendar_event_id") or ""))
                             or r.get("created_ts")),
            ))
            counters["n_pending"] += 1
    return {"writes": writes, "rows": rows, "results": results,
            "counters": counters}


def apply_cru_writes(workspace_root: str, *,
                     writes: Optional[List[Dict[str, Any]]] = None,
                     source_skill: str = SOURCE_SKILL) -> Dict[str, Any]:
    """THE WRITER (on `RUN_WRITER_ALLOWLIST` only). Perform a CRU plan's
    `writes`, in order, through the single closure path.

    `close` -> `commitment_state.close_commitment` (the pointer the plan
    carries is forwarded as `source_ref`, PROVMINT1); `partial` ->
    `commitment_state.mark_partial_received` (MC1: a multi-counterparty item
    records ONE person's receipt, never a whole close), carrying the same
    pointer the plan built (PROV1). A refused close
    (`CommitmentIdError`, `PendingReviewError`) or a refused receipt
    (`CommitmentIdError`) is recorded and the next write runs - exactly the
    inline bodies' handling, so a stale id in a batch of real closes never
    loses the real closes. An op this writer does not know is recorded as
    refused and nothing is done for it.

    Returns `{n_resolved, n_partial, n_refused, results}`.
    """
    from commitment_state import (CommitmentIdError, PendingReviewError,
                                  close_commitment, mark_partial_received)

    results: List[Dict[str, Any]] = []
    n_resolved = n_partial = n_refused = 0
    for raw in writes or []:
        w = dict(raw or {})
        op = w.get("op")
        cid = w.get("commitment_id")
        if op == "close":
            try:
                res = close_commitment(
                    workspace_root, cid,
                    resolved_by=str(w.get("resolved_by") or ""),
                    evidence=str(w.get("evidence") or ""),
                    source_skill=str(w.get("source_skill") or source_skill),
                    source_ref=w.get("source_ref"),
                )
                status = (res or {}).get("status")
                if status == "closed":
                    n_resolved += 1
                results.append({"op": op, "commitment_id": cid,
                                "status": status})
            except (CommitmentIdError, PendingReviewError) as exc:
                n_refused += 1
                results.append({"op": op, "commitment_id": cid,
                                "status": "skipped",
                                "reason": type(exc).__name__})
        elif op == "partial":
            try:
                mark_partial_received(
                    workspace_root, cid,
                    received_by=str(w.get("received_by") or ""),
                    source_skill=str(w.get("source_skill") or source_skill),
                    counterparty_id=w.get("counterparty_id"),
                    evidence=str(w.get("evidence") or ""),
                    source_ref=w.get("source_ref"),
                )
                n_partial += 1
                results.append({"op": op, "commitment_id": cid,
                                "counterparty_id": w.get("counterparty_id"),
                                "status": "recorded"})
            except CommitmentIdError as exc:
                n_refused += 1
                results.append({"op": op, "commitment_id": cid,
                                "status": "skipped",
                                "reason": type(exc).__name__})
        else:
            n_refused += 1
            results.append({"op": op, "commitment_id": cid,
                            "status": "refused", "reason": "unknown_op"})
    return {"n_resolved": n_resolved, "n_partial": n_partial,
            "n_refused": n_refused, "results": results}


# ---------------------------------------------------------------------------
# Phase 2.6 - the inbound pass's self-check
# ---------------------------------------------------------------------------

def validate_inbound_ran(workspace_root: str, *,
                         since_ts: Optional[str] = None) -> Dict[str, Any]:
    """`reconcile_inbound_commitments.validate_inbound_reconcile_ran` - did a
    REAL inbound pass land its audit row at or after `since_ts`?"""
    from reconcile_inbound_commitments import validate_inbound_reconcile_ran

    return dict(validate_inbound_reconcile_ran(workspace_root,
                                               since_ts=since_ts))


# ---------------------------------------------------------------------------
# Phase 3 - the surface-preference filter
# ---------------------------------------------------------------------------

def filter_chase_rows(workspace_root: str, *,
                      items: Optional[List[Dict[str, Any]]] = None,
                      surface: str = SOURCE_SKILL,
                      item_class: str = "chase") -> Dict[str, Any]:
    """`{surfaced, n_suppressed}` - the chase rows the CEO has NOT taught the
    system to stop offering (insight-generator Pass 14). Each item carries
    `counterparty_person_id`; a missing store suppresses nothing. This hides a
    PROMPT only - the commitment stays open and still counts."""
    from surface_preferences import is_suppressed, load_surface_preferences

    prefs = load_surface_preferences(workspace_root)
    kept = [dict(c) for c in (items or [])
            if not is_suppressed(prefs, surface, item_class=item_class,
                                 entity_id=c.get("counterparty_person_id"))]
    return {"surfaced": kept, "n_suppressed": len(items or []) - len(kept)}


#: Phase 3's caps and windows (orchestrator-commitments.md), in one place so
#: the prose and the code cannot drift: five rows per date bucket (both
#: reachability sub-buckets together), seven per fire across the meeting
#: bucket and the three date buckets (the meeting bucket takes at most three
#: of them first), "due soon" = the next three days, the 60/30-day aging cap.
CHASE_BUCKETS = ("overdue", "due_near", "aging_undated")
BUCKET_CAP = 5
TOTAL_CAP = 7
MEETING_CAP = 3
DUE_NEAR_DAYS = 3
AGING_CAP_DAYS = 60
AGING_QUIET_DAYS = 30
#: Phase 3.6 - review proposals per fire; Phase 3.8 - the nudged tail.
REVIEW_CAP = 3
NUDGED_CAP = 5
NUDGED_MIN_WEEKDAYS = 3
#: Tuesday and Thursday (Monday = 0) - the only fires that build Phase 3.8.
NUDGED_WEEKDAYS = (1, 3)
#: Phase 5 - the repeat-chase bump looks back this far.
REPEAT_CHASE_DAYS = 14
#: the two fields a `sent_reconcile` row must carry to count as a touch of a
#: person (Phase 3.8 only; REVIEW_ORCH2C LOW-6)
SENT_RECONCILE_PERSON_KEYS = ("counterparty_id", "counterparty_name")
#: Phase 5's three tiers, in escalation order.
TIERS = ("friendly", "firmer", "status check")
#: Phase 3 - a commitment tied to a project in any other status stays off
#: the daily chat (reachable through `show more`).
LIVE_PROJECT_STATUSES = ("active", "paused", "blocked")

_WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday",
                  "saturday", "sunday")


def _weekday_index(weekday: Any, today: "_dt.date") -> int:
    """0-6 (Monday = 0) from an int, a day name (`"Tue"`, `"tuesday"`), or
    - when nothing usable is given - the workspace's own today."""
    if isinstance(weekday, bool):
        weekday = None
    if isinstance(weekday, int) and 0 <= weekday <= 6:
        return weekday
    if isinstance(weekday, str) and weekday.strip():
        key = weekday.strip().lower()[:3]
        for index, name in enumerate(_WEEKDAY_NAMES):
            if name.startswith(key):
                return index
    return today.weekday()


def _day(value: Any, workspace_root: str) -> Optional["_dt.date"]:
    """A workspace-local calendar day for a due date or a timestamp, or None
    when it does not parse (DATE1: `tz.localize_date`, never a UTC slice)."""
    from tz import localize_date

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _dt.date.fromisoformat(localize_date(value.strip(),
                                                    workspace_root)[:10])
    except ValueError:
        return None


def _weekdays_between(start: "_dt.date", end: "_dt.date") -> int:
    """Weekdays after `start` up to and including `end` (0 when not after)."""
    n = 0
    cur = start
    while cur < end:
        cur += _dt.timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


def chase_candidates(workspace_root: str, *,
                     now_iso: Optional[str] = None,
                     user_person_id: str = "",
                     weekday: Any = None,
                     meeting_today_ids: Optional[List[str]] = None,
                     held_thread_ids: Optional[List[str]] = None
                     ) -> Dict[str, Any]:
    """Phases 3, 3.6, 3.8, 4.5 and 5 - the rows this chat chases, built where
    the ledger is. A READ (REVIEW_ORCH2C HIGH-2).

    WHY. These phases were prose reads ("Read events.jsonl. Apply base
    filter...") that the model did in its own process. On a merged seat the
    ledger is on the customer's computer, so the model had nothing to build
    the chase rows from and the page rendered its count over an empty body.
    This composes the EXISTING readers - the driver's own open set (the
    canonical loader, its duplicate fold and confidence floor), the surface
    partition, the dismissal ledger, the chase policy, the counterparty
    roster, the review-proposal reader, the movement map - and returns the
    candidates; the model drafts each chase (email-writer, Phase 7), passes
    the rows through `filter_chase_rows`, and hands them to the driver as
    `chase_rows`.

    `{today, weekday, user_person_id, rows, counts, review, nudged_no_reply,
    nudged_ran}`:

      * `rows` - Phase 3's WAITING ON set after every base filter (owner
        present and not the user, not a delegated task, no live dismissal,
        the user asked for it, its project is live, not already in today's
        meeting bucket), bucketed `overdue` / `due_near` / `aging_undated`,
        oldest first, capped (five per bucket, seven per fire less the
        meeting bucket's share). Each row carries the reachability split
        (`reachable` - a record with an email - or why not), the Phase 5
        tier with the 14-day repeat bump, the learned chase window, whether
        the escalation suggests a call, and the Phase 4.5 roster
        (`outstanding` - one nudge per entry - or `all_received`, which is a
        close PROPOSAL, never a close).
      * `review` - Phase 3.6, the open review proposals, oldest first, cap 3.
      * `nudged_no_reply` - Phase 3.8, on Tuesday and Thursday only: items
        already chased, quiet for three weekdays or more, not in `rows`;
        oldest outbound first, cap 5; `reads_disagree` when the inbound
        pass held this item's thread (MAILTRUST1 - never "nothing back").

    The requester rule: a named requester must be the user; with none, the
    item's source meeting (`data.source_event_seq`) must have had the user in
    it - and an item that names no source meeting at all is kept (its capture
    is in the user's own book, and dropping it would leave a count with no
    row, the defect this helper exists to close).
    """
    import surface_drivers as sd
    from chase_policy import get_chase_window, load_chase_policy
    from commitment_activity import (derive_commitment_movement,
                                     event_commitment_refs)
    from commitment_parties import (all_counterparties_received,
                                    outstanding_counterparties)
    from cru_match import (_commitment_field, load_events_defensively,
                           load_open_commitments, load_open_review_proposals)
    from entities_io import entities_collection
    from mute_ledger import active_dismissal_target_ids
    from people_writer import get_person_emails
    from source_skill_compat import normalize_source_skill
    from surface_split import (SURFACE_WAITING_ON, effective_kind_of,
                               partition_surfaces)

    ws = Path(os.path.abspath(str(workspace_root)))
    events_path = _events_path(str(ws))
    now_iso = now_iso or sd._now_iso()
    anchor, _instant = sd._hoisted_anchor_and_instant(ws, now_iso)
    try:
        today = _dt.date.fromisoformat(str(anchor)[:10])
    except ValueError:
        today = _dt.datetime.now(_dt.timezone.utc).date()
    try:
        now = _dt.datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
    except ValueError:
        now = _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    wd = _weekday_index(weekday, today)
    user = str(user_person_id or "")
    if not user:
        try:
            from primary_user import resolve_primary_user
            user = str(resolve_primary_user(str(ws)) or "")
        except Exception:  # noqa: BLE001 - an unresolved user matches nobody
            user = ""

    # --- the reads, each through its own reader ---------------------------
    opens = load_open_commitments(events_path, workspace_root=str(ws))
    opens, _n_folded = sd._fold_dups(opens)
    shown, _n_floor = sd._apply_confidence_floor(ws, opens)
    part = partition_surfaces(shown, user or None)
    waiting = [ev for ev in part[SURFACE_WAITING_ON]
               if effective_kind_of(ev) != "task"]
    events, _skipped = load_events_defensively(events_path, since_ts=None)
    dismissed = {str(t) for t in active_dismissal_target_ids(events, now_iso)}
    movement = derive_commitment_movement(events_path)
    policy = load_chase_policy(str(ws))
    people: Dict[str, Dict[str, Any]] = {}
    orgs: Dict[str, Dict[str, Any]] = {}
    threads: Dict[str, Dict[str, Any]] = {}
    try:
        ent = json.loads((ws / "_hq" / "data" / "entities.json").read_text(
            encoding="utf-8"))
        for name, into in (("people", people), ("orgs", orgs),
                           ("threads", threads)):
            for rec in entities_collection(ent, name):
                if isinstance(rec, dict) and rec.get("id"):
                    into[str(rec["id"])] = rec
    except Exception:  # noqa: BLE001 - no roster: nobody is reachable, and
        pass           # every item still renders (the unreachable shape)

    by_seq: Dict[int, Dict[str, Any]] = {}
    commitment_by_seq: Dict[int, str] = {}
    for ev in events:
        seq = ev.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            by_seq[seq] = ev
            if (ev.get("type") or ev.get("event")) == "commitment":
                d = ev.get("data") or {}
                commitment_by_seq[seq] = str(d.get("id")
                                             or f"commitment_seq_{seq}")

    # Phase 5 - every chase this chat sent, by commitment id. Phase 3.8 ALSO
    # reads a sent-reconcile outbound that names the person (a PERSON touch)
    touches: Dict[str, List[str]] = {}
    person_touches: Dict[str, List[str]] = {}
    for ev in events:
        et = ev.get("type") or ev.get("event")
        ts = str(ev.get("ts") or "")
        if not ts:
            continue
        if (et == "outreach_sent"
                and normalize_source_skill(ev.get("source_skill"))
                == SOURCE_SKILL):
            ids, seqs = event_commitment_refs(ev)
            ids = list(ids) + [commitment_by_seq[q] for q in seqs
                               if q in commitment_by_seq]
            for cid in dict.fromkeys(ids):
                touches.setdefault(str(cid), []).append(ts)
        elif et == "sent_reconcile":
            # REVIEW_ORCH2C LOW-6. A person touch needs the row to NAME its
            # counterparty in these two fields; no shipped writer puts them
            # on this event today (both write per-run audit fields only), so
            # a row without them is not a touch at all - never guessed from
            # any other key. And a person touch only QUALIFIES an item for
            # Phase 3.8's nudged tail: Phase 5's repeat bump and the call
            # suggestion count this chat's own chases of the SAME item, so a
            # note to a person never raises the tone on an item nobody chased.
            d = ev.get("data") or {}
            named = [str(d.get(key)).strip().lower()
                     for key in SENT_RECONCILE_PERSON_KEYS
                     if isinstance(d.get(key), str) and d.get(key).strip()]
            if not named:
                continue
            for val in dict.fromkeys(named):
                person_touches.setdefault(val, []).append(ts)

    def _cid(ev: Dict[str, Any]) -> str:
        d = ev.get("data") or {}
        return str(d.get("id") or f"commitment_seq_{ev.get('seq')}")

    def _requested_by_user(ev: Dict[str, Any]) -> str:
        """'' when the item fails the requester rule, else the basis."""
        req = _commitment_field(ev, "requester_id")
        if req:
            return "requester" if user and str(req) == user else ""
        sseq = (ev.get("data") or {}).get("source_event_seq")
        if isinstance(sseq, str) and sseq.strip().isdigit():
            sseq = int(sseq.strip())
        source = by_seq.get(sseq) if isinstance(sseq, int) else None
        if source is None:
            return "no_source_meeting"
        present = set(source.get("person_ids") or []) | set(
            (source.get("data") or {}).get("person_ids") or [])
        return "attended" if user and user in present else ""

    def _live_project(ev: Dict[str, Any]) -> bool:
        d = ev.get("data") or {}
        tid = str(d.get("primary_thread_id") or ev.get("primary_thread_id")
                  or "")
        rec = threads.get(tid)
        if not rec:
            return True   # an unresolvable thread says nothing is dormant
        status = str(rec.get("status") or "active").strip().lower()
        return status in LIVE_PROJECT_STATUSES

    def _recent(stamp: str) -> bool:
        day = _day(stamp, str(ws))
        return day is not None and (today - day).days <= REPEAT_CHASE_DAYS

    exclude = {str(x) for x in (meeting_today_ids or [])}
    held = {str(x) for x in (held_thread_ids or [])}
    counts: Dict[str, Any] = {
        "waiting_on": len(waiting), "not_requested": 0, "dormant_project": 0,
        "dismissed": 0, "in_meeting_bucket": 0, "not_due_yet": 0,
        "aging_capped": 0}
    eligible: List[Dict[str, Any]] = []
    for ev in waiting:
        cid = _cid(ev)
        d = ev.get("data") or {}
        if cid in dismissed:
            counts["dismissed"] += 1
            continue
        basis = _requested_by_user(ev)
        if not basis:
            counts["not_requested"] += 1
            continue
        if not _live_project(ev):
            counts["dormant_project"] += 1
            continue
        owner = str(_commitment_field(ev, "owner_id") or "")
        rec = people.get(owner) or {}
        emails = list(get_person_emails(rec)) if rec else []
        external = str(d.get("owner_external") or ev.get("owner_external")
                       or "").strip()
        if not emails and "@" in external:
            emails = [external]
        name = str(rec.get("canonical_name") or rec.get("name") or external
                   or owner).strip()
        org = orgs.get(str(rec.get("org_id") or "")) or {}
        relationship = str(org.get("relationship_type") or "")
        chase_days, escalate = get_chase_window(policy, relationship)
        age = sd._age_days(str(ev.get("ts") or ""), now_iso)
        due_raw = _commitment_field(ev, "due")
        due_day = _day(str(due_raw), str(ws)) if due_raw else None
        bucket = ""
        if due_day is not None:
            if due_day < today:
                bucket = "overdue"
            elif (due_day - today).days <= DUE_NEAR_DAYS:
                bucket = "due_near"
        elif age is not None and age >= int(chase_days):
            bucket = "aging_undated"
            last = getattr(movement.get(cid), "ts", None)
            if (age >= AGING_CAP_DAYS and last is not None
                    and (now - last).days >= AGING_QUIET_DAYS):
                counts["aging_capped"] += 1
                bucket = "capped"
        chased = sorted(set(touches.get(cid, [])))
        touched = set(chased)
        for key in (owner.lower(), name.lower()):
            if key:
                touched |= set(person_touches.get(key, []))
        touched_sorted = sorted(touched)
        tier_i = 0 if age is None or age <= 7 else (1 if age <= 30 else 2)
        bumped = any(_recent(t) for t in chased) and tier_i < len(TIERS) - 1
        if bumped:
            tier_i += 1
        row: Dict[str, Any] = {
            "commitment_id": cid,
            "title": str(d.get("title") or d.get("summary") or "(untitled)"),
            "bucket": bucket if bucket in CHASE_BUCKETS else "",
            "due": str(due_raw) if due_raw else None,
            "age_days": age,
            "owner_id": owner or None,
            "owner_name": name or None,
            "owner_external": external or None,
            "counterparty_person_id": owner or None,
            "owner_email": emails[0] if emails else None,
            "reachable": bool(emails),
            "unreachable_reason": (None if emails else
                                   ("no_email_on_file" if rec
                                    else "no_contact_record")),
            "requester_basis": basis,
            "relationship_type": relationship or None,
            "chase_days": int(chase_days),
            "tier": TIERS[tier_i],
            "tier_bumped": bumped,
            "n_chases": len(chased),
            # Phase 3.8's qualifier: this chat's chase OR a person touch
            "last_chased": touched_sorted[-1] if touched_sorted else None,
            "suggest_call": len(chased) >= int(escalate),
            "outstanding": outstanding_counterparties(
                ev, workspace_root=str(ws)),
            "all_received": bool(all_counterparties_received(
                ev, workspace_root=str(ws))),
            "thread_id": d.get("thread_id") or None,
        }
        if cid in exclude:
            counts["in_meeting_bucket"] += 1
            row["bucket"] = "meeting_today"
        elif not row["bucket"] and bucket != "capped":
            counts["not_due_yet"] += 1
        eligible.append(row)

    def _oldest_first(r: Dict[str, Any]):
        age = r["age_days"] if isinstance(r["age_days"], int) else -1
        return (-age, r["commitment_id"])

    budget = TOTAL_CAP - min(MEETING_CAP, len(exclude))
    rows: List[Dict[str, Any]] = []
    by_bucket: Dict[str, int] = {}
    capped = 0
    for bucket in CHASE_BUCKETS:
        members = sorted((r for r in eligible if r["bucket"] == bucket),
                         key=_oldest_first)
        by_bucket[bucket] = len(members)
        taken = 0
        for r in members:
            if taken < BUCKET_CAP and len(rows) < budget:
                rows.append(r)
                taken += 1
            else:
                capped += 1
    counts["by_bucket"] = by_bucket
    counts["capped"] = capped
    counts["rows"] = len(rows)

    # --- Phase 3.6 - the review proposals ---------------------------------
    titles = {_cid(ev): str((ev.get("data") or {}).get("title") or "")
              for ev in opens}
    review: List[Dict[str, Any]] = []
    for prop in reversed(load_open_review_proposals(events_path,
                                                    now_iso=now_iso)):
        pd = prop.get("data") or {}
        pcid = str(pd.get("commitment_id") or "")
        if not pcid or pcid in dismissed:
            continue
        review.append({"commitment_id": pcid,
                       "title": titles.get(pcid) or str(pd.get("title") or ""),
                       "evidence": str(pd.get("evidence") or ""),
                       "proposal_seq": prop.get("seq")})
        if len(review) >= REVIEW_CAP:
            break

    # --- Phase 3.8 - the nudged tail, Tuesday and Thursday only -----------
    nudged: List[Dict[str, Any]] = []
    nudged_ran = wd in NUDGED_WEEKDAYS
    if nudged_ran:
        shown_ids = {r["commitment_id"] for r in rows} | exclude
        for r in eligible:
            if r["commitment_id"] in shown_ids or not r["last_chased"]:
                continue
            last = _day(r["last_chased"], str(ws))
            if last is None:
                continue
            quiet = _weekdays_between(last, today)
            if quiet < NUDGED_MIN_WEEKDAYS:
                continue
            item = dict(r)
            item["bucket"] = "nudged_no_reply"
            item["weekdays_quiet"] = quiet
            item["reads_disagree"] = bool(r.get("thread_id")
                                          and str(r["thread_id"]) in held)
            if item["tier"] == TIERS[0]:   # a re-nudge is never friendly
                item["tier"] = TIERS[1]
            nudged.append(item)
        nudged.sort(key=lambda r: (str(r["last_chased"]), r["commitment_id"]))
        nudged = nudged[:NUDGED_CAP]

    return {"today": today.isoformat(), "weekday": _WEEKDAY_NAMES[wd],
            "user_person_id": user, "rows": rows, "counts": counts,
            "review": review, "nudged_no_reply": nudged,
            "nudged_ran": nudged_ran}


def meeting_today_rows(workspace_root: str, *,
                       todays_meetings: Optional[List[Dict[str, Any]]] = None,
                       user_person_id: str = "") -> Dict[str, Any]:
    """`{rows}` - the MEETING TODAY bucket's matches (v4.5.2 C1 / F-44):
    `commitment_state.match_commitments_to_meetings` over the projected open
    set, loaded with the workspace (F-28) the way Phase 3 loads it.

    The instruction used to hand the matcher an `opens` it had loaded in the
    same process; on a merged seat that set is on the customer's computer, so
    the match runs there and the rows come back. Every match is returned;
    which of them render HERE (owner present and not the user) is the
    partition's call, exactly as before.
    """
    from commitment_state import match_commitments_to_meetings
    from cru_match import load_open_commitments

    meetings = [dict(m) for m in (todays_meetings or [])]
    if not meetings:
        return {"rows": []}
    opens = load_open_commitments(_events_path(workspace_root),
                                  workspace_root=workspace_root)
    return {"rows": match_commitments_to_meetings(
        opens, meetings, user_person_id=user_person_id or None)}


# ---------------------------------------------------------------------------
# Phase 9 - the render
# ---------------------------------------------------------------------------

def renderer_preflight(workspace_root: str) -> Dict[str, Any]:
    """`{ok, missing}` - the renderer and its validators import where the data
    is. Step 1 of Phase 9; `ok: false` aborts the fire before any post."""
    import inbox_helpers

    return inbox_helpers.renderer_preflight(workspace_root)


def _surfaced(view: Dict[str, Any]) -> int:
    return sum(len(sec.get("items") or []) for sec in view.get("sections") or [])


def _render_one(view: Dict[str, Any], *, wrapper: str) -> Dict[str, Any]:
    """`widget_transport.render_and_persist`'s render half for an UNPAGED
    view, WITHOUT the persist: the same renderer and the same wrapper-contract
    validator, and the same over-budget flag. The persist is the caller's
    `plan write`. Pinned byte-for-byte against the transport by the suite."""
    from chat_output_renderer import (render_chat_output_widget,
                                      validate_rendered_widget)
    from widget_transport import WIDGET_PAGE_BYTE_BUDGET

    html = render_chat_output_widget(view, wrapper=wrapper)
    validate_rendered_widget(html, surface=view.get("surface"))
    out: Dict[str, Any] = {"html": html}
    if len(html) > WIDGET_PAGE_BYTE_BUDGET:
        out["over_budget"] = True
    return out


def _page_rel(name_hint: str) -> str:
    """Where the audit page goes - the transport's own spelling for an
    unpaged render (`<hint>_<utc stamp>.html` under the widgets folder)."""
    from widget_transport import _safe_filename

    stamp = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    return f"{WIDGET_DIR_REL}/{_safe_filename(name_hint)}_{stamp}.html"


def render_waiting_on_page(workspace_root: str, *,
                           data_view: Optional[Dict[str, Any]] = None,
                           wrapper: str = "fragment",
                           name_hint: str = SURFACE) -> Dict[str, Any]:
    """A HAND-SHAPED Waiting On view, rendered where the data is and persisted
    by nobody here: `{html, page_rel, bytes_len, wrapper, surfaced,
    pending_rows}`.

    This is the manual path's transport call - the all-clear view, and the
    meeting-relevance / nudged-no-reply sections the one-command driver does
    not build - minus its persist: the same renderer, the same
    wrapper-contract validator, `page_rel` naming where the audit page goes
    for the caller's `plan write`, and any gate row the render emitted handed
    back in `pending_rows`. Unpaged, exactly as that call was.

    The driver's own page is NOT rendered here. `build_waiting_on_view` mints
    the plate's display numbers under the writer lock (PLATENUM1 - a real
    write, found by the seven-frame scan), so that page goes through the write
    door as `run_waiting_on_surface`. With no `data_view` this answers
    `{refused: "no_view"}` and renders nothing.
    """
    import inbox_helpers

    if not isinstance(data_view, dict) or not data_view:
        return {"refused": "no_view", "html": "", "pending_rows": []}
    with inbox_helpers._captured_appends() as captured, _base_cwd():
        rendered = _render_one(dict(data_view), wrapper=wrapper)
    html = rendered["html"]
    out = {"html": html, "page_rel": _page_rel(name_hint),
           "bytes_len": len(html), "wrapper": wrapper,
           "surfaced": _surfaced(data_view), "pending_rows": list(captured)}
    if rendered.get("over_budget"):
        out["over_budget"] = True
    return out


def run_waiting_on_surface(workspace_root: str, *, page: int = 1,
                           chase_rows: Optional[List[Dict[str, Any]]] = None,
                           fired_via: Optional[str] = None,
                           rerun_of: Optional[str] = None,
                           page_size: Optional[int] = None,
                           now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER (on `RUN_WRITER_ALLOWLIST` only) - the one-command driver,
    through the write door: `surface_drivers.run_surface("waiting-on", ...)`,
    the function `surface_drivers.py waiting-on --fired-via ...` has always
    called.

    It writes four things, all through their own writers, in one call: the
    plate's display numbers (PLATENUM1, under the writer lock), the frozen
    page-set pages 2+ slice (PAGESNAP), the audit page, and - on page 1 with
    `fired_via` - the surface's ONE `pack_run` receipt, deduped exactly as
    the driver dedups it (FB-7: the render and the receipt cannot be split).
    Through the door, the receipt is stamped with the writer the door
    forwarded, so a merged seat's fire is logged under the customer's id.

    The answer is the transport the model relays: `html` (to `show_widget`
    verbatim), `pagination`, `receipt`, `page_rel` - the audit page named
    workspace-relative, never by the mount path it landed at. A stale mount
    is refused before anything renders and comes back as `{refused:
    "mount_stale", lines}` - the driver's own syncing sentences - never as a
    crash. `now_iso` is the CLI's `--now`: a fire passes nothing.
    """
    import surface_drivers as sd

    # absolute BEFORE the fence moves the working directory: a relative
    # root must name the same folder it named when the door called us
    ws = Path(os.path.abspath(str(workspace_root)))
    try:
        with _base_cwd():
            transport = sd.run_surface(
                SURFACE, str(ws), page=1 if page is None else int(page),
                page_size=page_size, now_iso=now_iso, chase_rows=chase_rows,
                fired_via=fired_via, rerun_of=rerun_of or None)
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
    return out


def render_waiting_on_text(workspace_root: str, *,
                           data_view: Optional[Dict[str, Any]] = None,
                           saved_line: str = "") -> Dict[str, Any]:
    """The Waiting On list as TEXT, for the session that has no widget tool
    (R-RW2-7 (a); SPEC_NIGHTM3 I-12's rule, applied to this chain by §4b).

    `{text, groups, lines}` - one heading per non-empty section, in the
    view's order; one line per row carrying the SAME row number the widget's
    action grammar uses (`3 send` means the same row in both surfaces); the
    draft's To and Subject under a drafted row, as a preview only - nothing
    is queued or sent from a fire. The last line is the widget-absent
    sentence (or `saved_line` when the caller has the saved page's own
    sentence). Every line passes `validate_chat_output`; a line that does not
    is dropped and counted, never posted.

    With no `data_view`, the view is the page-set the one-command driver just
    froze for this surface (`page_snapshot.load_pageset`) - the same rows,
    in the same order, with the same numbers the page carries.
    """
    from chat_output_renderer import validate_chat_output

    view = dict(data_view or {})
    if not view:
        from page_snapshot import load_pageset
        try:
            frozen, _meta = load_pageset(workspace_root, SURFACE)
        except Exception:  # noqa: BLE001 - no page-set is an empty list
            frozen = None
        view = dict(frozen or {})
    lines: List[str] = []
    groups: Dict[str, List[str]] = {}
    dropped = 0

    def keep(line: str) -> bool:
        nonlocal dropped
        # A tool id on a customer line is the Probe B leak (the fire named
        # the missing widget tool to the customer). The output gate learns
        # the pattern in IDENT1 I-12; this surface does not wait for it.
        if _TOOL_ID_RE.search(line):
            dropped += 1
            return False
        try:
            validate_chat_output(line, surface=view.get("surface"))
        except Exception:  # noqa: BLE001 - a line that fails the gate is
            dropped += 1   # never posted
            return False
        return True

    header = str(view.get("header") or "").strip()
    if header and keep(header):
        lines.append(header)
    for sec in view.get("sections") or []:
        items = list(sec.get("items") or [])
        if not items:
            continue
        title = str(sec.get("title") or "").strip() or "Waiting On"
        block: List[str] = []
        for it in items:
            # the renderer's own rule for the visible number
            # (`chat_output_renderer`: `item.get("display_n", n)`)
            n = it.get("display_n", it.get("n"))
            who = str(it.get("name") or "").strip()
            what = str(it.get("subject") or it.get("title") or "").strip()
            why = str(it.get("context_tag") or "").strip()
            lead = f"{n}. " if n is not None else "- "
            line = lead + " — ".join(p for p in (who, what) if p)
            if why:
                line += f" ({why})"
            if keep(line):
                block.append(line)
            meta = dict((str(k), str(v)) for k, v in (it.get("metadata") or [])
                        if isinstance(k, str))
            if meta.get("To") and meta.get("Subject"):
                preview = (f"> **To:** {meta['To']}  ·  "
                           f"**Subject:** {meta['Subject']}")
                if keep(preview):
                    block.append(preview)
        if block and keep(title):
            groups[title] = block
            lines.append("")
            lines.append(title)
            lines.extend(block)
    closing = str(saved_line or "").strip() or WIDGET_ABSENT_LINE
    if keep(closing):
        lines.append("")
        lines.append(closing)
    return {"text": "\n".join(lines).strip() + "\n", "groups": groups,
            "lines": lines, "n_dropped": dropped}


# ---------------------------------------------------------------------------
# Phase 8 - the one receipt
# ---------------------------------------------------------------------------

def plan_commitments_receipt(workspace_root: str, *,
                             fired_via: Optional[str] = None,
                             surfaced: Optional[int] = None,
                             rerun_of: Optional[str] = None,
                             duration_ms: Optional[int] = None,
                             late_tier: Optional[str] = None,
                             telemetry: Optional[Dict[str, Any]] = None,
                             extra_data: Optional[Dict[str, Any]] = None
                             ) -> Dict[str, Any]:
    """`{row}` - the fire's ONE `pack_run` receipt on the paths the
    one-command driver does not receipt (Phase 8's `log_receipt` call: the
    degrade tier, which renders nothing, and the manual hand-shaped render),
    composed exactly as `receipts.log_receipt` composes it and NOT written.
    The caller appends `row` through `plan append_jsonl`, once.

    `rerun_of` rides `extra_data` under `late_fire.RERUN_OF_FIELD` on a
    `rerun` tier ONLY (SPEC RERUNFAN1); omit it on every other tier. The row
    is `inbox_helpers.plan_fire_receipt` - `receipts`' own parts, the composer
    ORCH1 pinned against `log_receipt` - for this chain's task id.
    """
    import inbox_helpers

    extra: Dict[str, Any] = dict(extra_data or {})
    if rerun_of:
        from late_fire import RERUN_OF_FIELD
        extra[RERUN_OF_FIELD] = rerun_of
    row = inbox_helpers.plan_fire_receipt(
        workspace_root, task_id=TASK_ID, fired_via=fired_via,
        surfaced=surfaced, duration_ms=duration_ms, late_tier=late_tier,
        telemetry=telemetry, extra_data=extra or None)
    # `log_receipt` derives `n_errors` from `errors` only when the caller
    # carried an `n_errors` of its own (receipts.py, FIX3 F3-11); the inbox
    # composer derives it whenever `errors` is a list. This chain's receipt is
    # the WRITER's row, so the derived count stays only where the writer
    # would have put it (the golden pins it).
    if "n_errors" not in extra:
        row.get("data", {}).pop("n_errors", None)
    return {"row": row}


__all__ = [
    "CONFIG_DEFAULTS", "SOURCE_SKILL", "TASK_ID", "WIDGET_ABSENT_LINE",
    "WRITE_OPS", "apply_cru_writes", "chase_candidates", "cru_window",
    "filter_chase_rows",
    "fold_gate", "lateness", "meeting_today_rows", "plan_calendar_cru", "plan_commitments_receipt",
    "plan_sent_cru", "render_waiting_on_page", "render_waiting_on_text",
    "renderer_preflight", "resolve_people", "run_waiting_on_surface", "setup",
    "validate_inbound_ran",
]
