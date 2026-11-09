#!/usr/bin/env python3
"""
The propose-only kinds (cr1#668, lane 6 of cr1#609): a declared
`documents`, `deals` or `contracts` connector is read on every Maintenance
run for people and projects, and for closes that it only ever PROPOSES.

THE RULING (owner, cr1#609, 2026-11-03): every declared connector both
opens and closes, subject to its dials, and `documents`, `deals` and
`contracts` propose closes until a capture rule for them is ruled. So:

  * nothing here opens a commitment (no capture rule exists yet, and the
    kinds' `open_commitments` dial ships off);
  * a match against an open commitment, through the EXISTING matchers at
    their own thresholds (`reconcile_sent` for the owner's own artifact,
    `reconcile_inbound` for somebody else's), is written as a close
    PROPOSAL with the artifact as its source pointer, whatever the
    connector's `close_mode` says and whatever grade the matcher gave it.
    This job never closes outright;
  * every item read on an included container (a folder, a pipeline) is
    also people and project signal under the `update_people` and
    `update_projects` dials: the receipt names the people and the
    containers it saw, and says why when a dial turned a kind away. What it
    saw goes to the identity and lifecycle paths with the item as the source
    pointer (`tasks_leg.hand_off_seen`, cr1#738), for the daily people and
    project jobs to judge by their own window rule. Under a non-empty include
    list, an item with no container name or id is not read (cr1#759).

One registry row, `connector-proposals`, a close leg (after the capture
legs, cr1#663). It calls `connector_reads.for_job` for each kind before it
reads (cr1#667, guard G84). Its receipt is a `pack_run` under its own id.

Pure-ish: no network I/O. The fire's connector step reads the items and
hands dicts in.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

JOB_ID = "connector-proposals"
KINDS = ("documents", "deals", "contracts")

#: The fields one staged item carries.
ITEM_FIELDS = ("category", "item_id", "title", "text", "ts", "by_owner",
               "container_id", "container_name", "people_names",
               "person_ids", "url")


def _s(v) -> str:
    return str(v if v is not None else "").strip()


def _list(v) -> List[str]:
    if isinstance(v, (list, tuple, set)):
        return [_s(x) for x in v if _s(x)]
    return [_s(v)] if _s(v) else []


def _effective_fired_via(explicit):
    try:
        from receipts import effective_fired_via
    except Exception:  # noqa: BLE001
        return explicit if explicit is not None else "scheduled"
    try:
        return effective_fired_via(explicit)
    except Exception:  # noqa: BLE001
        return explicit if explicit is not None else "scheduled"


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def records(workspace_root, purpose: str, env=None) -> List[Dict[str, Any]]:
    """Every declared connector of the three kinds, with its skip for
    `purpose`: the one reader, called before any source is read."""
    from connector_reads import for_job
    out: List[Dict[str, Any]] = []
    for kind in KINDS:
        try:
            out.extend(for_job(workspace_root, kind, purpose=purpose, env=env))
        except Exception:  # noqa: BLE001 - a reader never raises
            continue
    return out


def _window(workspace_root, now=None) -> dict:
    from catchup import maintenance_window
    local_now = None
    if isinstance(now, _dt.datetime):
        local_now = now.astimezone().replace(tzinfo=None) if now.tzinfo else now
    return maintenance_window(workspace_root, JOB_ID, now=local_now)


def plan_connector_proposals(workspace_root, now=None) -> dict:
    """The connector-proposals job PLANNED beside the data - READ ONLY.
    One entry per declared documents / deals / contracts connector with its
    close skip, its people and project skips and its lists, bounded by the
    job's window. Writes nothing."""
    window = _window(workspace_root, now)
    try:
        from primary_user import resolve_primary_user
        user = resolve_primary_user(workspace_root)
    except Exception:  # noqa: BLE001
        user = None
    people = {r["category"]: r.get("skip_reason") for r in records(workspace_root, "people")}
    projects = {r["category"]: r.get("skip_reason") for r in records(workspace_root, "projects")}
    connectors = []
    for rec in records(workspace_root, "close"):
        cat = rec.get("category")
        connectors.append({
            "category": cat, "kind": rec.get("kind"),
            "provider": rec.get("provider"), "label": rec.get("label"),
            "include": list(rec.get("include") or []),
            "exclude": list(rec.get("exclude") or []),
            "skip_reason": rec.get("skip_reason"),
            "skip_kind": rec.get("skip_kind"),
            "people_skip_reason": people.get(cat),
            "projects_skip_reason": projects.get(cat),
        })
    readable = [c for c in connectors
                if c.get("skip_kind") != "reachability"
                and (not c["skip_reason"] or not (c["people_skip_reason"]
                                                  and c["projects_skip_reason"]))]
    from catchup import MAINTENANCE_WINDOW_FIELD
    return {
        "job_id": JOB_ID,
        "kinds": list(KINDS),
        "connectors": connectors,
        "connector_queries": [c["label"] or c["category"] for c in readable],
        "after": window.get("since_aware"),
        MAINTENANCE_WINDOW_FIELD: window,
        "user_person_id": user,
        "item_fields": list(ITEM_FIELDS),
        "fire_start": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "ready": bool(user) or not connectors,
        "blocked_reason": (None if (user or not connectors) else
                           "the workspace's own person is not recorded"),
    }


def build_ref(item: dict, rec: dict) -> Optional[Dict[str, Any]]:
    """The structured pointer for an item, or None when it cannot be built
    (no provider or no item id): a proposal with no pointer is refused."""
    provider = _s(item.get("provider") or rec.get("provider")).lower()
    item_id = _s(item.get("item_id") or item.get("id"))
    if not provider or not item_id:
        return None
    ref = {"provider": provider, "kind": rec.get("kind"), "item_id": item_id,
           "container_id": _s(item.get("container_id")),
           "container_name": _s(item.get("container_name"))}
    if _s(item.get("url")):
        ref["url"] = _s(item.get("url"))
    return ref


def _ref_string(ref: dict) -> str:
    return f"{ref['provider']}:{ref['item_id']}"


def _record_for(item: dict, recs: List[dict]) -> Optional[dict]:
    want = _s(item.get("category") or item.get("connector"))
    if want:
        for r in recs:
            if r.get("category") == want:
                return r
        return None
    return recs[0] if len(recs) == 1 else None


def _log_receipt(workspace_root, receipt: dict, *, fired_via) -> None:
    from receipts import log_receipt
    keep = {k: v for k, v in receipt.items() if v is not None}
    try:
        log_receipt(workspace_root, JOB_ID, receipt_type="pack_run",
                    status=receipt.get("status") or "complete",
                    fired_via=fired_via,
                    surfaced=int(receipt.get("n_proposed") or 0),
                    extra_data=keep)
    except Exception as exc:  # noqa: BLE001
        print(f"connector_proposals: receipt FAILED to land ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        receipt["receipt_failed"] = True
        receipt["receipt_error"] = f"{type(exc).__name__}: {exc}"


def propose_from_connectors_and_receipt(workspace_root, items, *, user_person_id,
                                        owner_names=(), maintenance_window=None,
                                        fired_via=None, fetch_blocked=None,
                                        connectors_read=None,
                                        exclude_captured_since=None,
                                        source_skill: str = JOB_ID, now=None) -> dict:
    """Run the job end to end over the items the fire read and return the
    receipt it wrote. Never closes: every match is a proposal.

    `connectors_read` (cr1#743) is the categories whose read answered, an
    empty answer included; `fetch_blocked` is why no read answered. An empty
    `items` alone proves no read (`connector_reads.read_outcome`)."""
    from receipts import require_writer_identity
    require_writer_identity(workspace_root=workspace_root)
    fired_via = _effective_fired_via(fired_via)
    window = maintenance_window or _window(workspace_root, now)
    from catchup import MAINTENANCE_WINDOW_FIELD, WINDOW_INCOMPLETE_FIELD
    from tasks_leg import hold_marker

    close_recs = records(workspace_root, "close")
    if not close_recs:
        # cr1#740: a skip reads nothing, so its receipt carries the window
        # start as its marker and the next run reads the same window.
        receipt = {"ran": False, "status": "skipped", "leg": JOB_ID,
                   "skip_reason": ("no documents, deals or contracts connector "
                                   "is declared for this workspace"),
                   MAINTENANCE_WINDOW_FIELD: window,
                   WINDOW_INCOMPLETE_FIELD: hold_marker(window), "summary": ""}
        _log_receipt(workspace_root, receipt, fired_via=fired_via)
        return receipt
    if not user_person_id:
        raise RuntimeError(
            "connector-proposals ABORTED: the primary user is unresolved. "
            "Nothing was read or written (the Bug #102 posture).")
    from connector_reads import allowed, read_outcome
    from tasks_leg import note_unread, read_fields
    seen = set()
    for raw in items or []:
        rec = _record_for(raw, close_recs) if isinstance(raw, dict) else None
        if rec is not None:
            seen.add(rec.get("category"))
    blocked, unread, unsure = read_outcome(workspace_root, JOB_ID, close_recs, seen=seen,
                                           connectors_read=connectors_read,
                                           fetch_blocked=fetch_blocked)
    if blocked:
        said = "confirmed as read" if unsure else "read"
        receipt = {"ran": False, "status": "blocked", "leg": JOB_ID,
                   "blocked_reason": blocked, MAINTENANCE_WINDOW_FIELD: window,
                   WINDOW_INCOMPLETE_FIELD: hold_marker(window),
                   "summary": f"Documents, deals and contracts were not {said}: "
                              f"{blocked}. The same window is read next time."}
        receipt.update(read_fields(unread, unsure))
        _log_receipt(workspace_root, receipt, fired_via=fired_via)
        return receipt

    people_skip = {r["category"]: r.get("skip_reason") for r in records(workspace_root, "people")}
    projects_skip = {r["category"]: r.get("skip_reason") for r in records(workspace_root, "projects")}
    own = {_s(n).lower() for n in owner_names or () if _s(n)}
    counts = {"n_scanned": 0, "n_excluded": 0, "n_unattributed": 0,
              "n_dialed_off": 0, "n_refused_no_pointer": 0}
    people: set = set()
    projects: set = set()
    seen_people: List[dict] = []
    seen_projects: List[dict] = []
    n_no_pointer = 0
    lanes: Dict[tuple, List[tuple]] = {}
    for raw in items or []:
        counts["n_scanned"] += 1
        if not isinstance(raw, dict):
            counts["n_unattributed"] += 1
            continue
        rec = _record_for(raw, close_recs)
        if rec is None:
            counts["n_unattributed"] += 1
            continue
        container = [x for x in (_s(raw.get("container_id")),
                                 _s(raw.get("container_name"))) if x]
        if not allowed(rec, *container, must_match=True):
            counts["n_excluded"] += 1
            continue
        cat = rec.get("category")
        ref = build_ref(raw, rec)
        pointer = ({"ref": ref, "kind": rec.get("kind"), "source_ref": _ref_string(ref)}
                   if ref else None)
        wanted = False
        if cat in people_skip and not people_skip.get(cat):
            for nm in _list(raw.get("people_names")):
                if nm.lower() not in own:
                    people.add(nm)
                    wanted = True
                    if pointer:
                        seen_people.append(dict(pointer, name=nm))
        if cat in projects_skip and not projects_skip.get(cat) and container:
            projects.add(_s(raw.get("container_name")) or container[0])
            wanted = True
            if pointer:
                seen_projects.append(dict(pointer, name=_s(raw.get("container_name")),
                                          ts=_s(raw.get("ts"))))
        if wanted and pointer is None:
            n_no_pointer += 1
        if rec.get("skip_reason"):
            counts["n_dialed_off"] += 1
            continue
        if ref is None:
            counts["n_refused_no_pointer"] += 1
            continue
        lane = "out" if raw.get("by_owner") is True else "in"
        lanes.setdefault((ref["provider"], lane), []).append((raw, ref))

    from cru_match import (build_pending_review_event, load_open_commitments,
                           load_open_review_proposals, shared_content_words)
    from event_gate import append_event
    from reconcile_inbound_commitments import reconcile_inbound
    from reconcile_sent_commitments import reconcile_sent

    from cru_match import _commitment_id
    from tasks_leg import hand_off_seen, note_waiting, party_names, signal_skips

    events_path = _events_path(workspace_root)
    opens = load_open_commitments(str(events_path), workspace_root=workspace_root) if lanes else []
    by_cid = {str(_commitment_id(ev)): ev for ev in opens if isinstance(ev, dict)}
    best: Dict[str, tuple] = {}
    for (provider, lane), pairs in lanes.items():
        msgs = []
        by_mid = {}
        for raw, ref in pairs:
            m = {"message_id": ref["item_id"], "ts": _s(raw.get("ts")),
                 "thread_id": None, "has_attachment": False,
                 "recipient_person_ids": _list(raw.get("person_ids")),
                 "recipient_names": _list(raw.get("people_names")),
                 "subject": _s(raw.get("title")) or None,
                 "body": _s(raw.get("text"))}
            msgs.append(m)
            by_mid[ref["item_id"]] = (raw, ref, m)
        if lane == "out":
            res = reconcile_sent(opens, msgs, user_person_id=user_person_id,
                                 provider=provider, exclude_captured_since=exclude_captured_since,
                                 workspace_root=workspace_root, from_mail=False)
        else:
            res = reconcile_inbound(opens, msgs, user_person_id=user_person_id,
                                    provider=provider, exclude_captured_since=exclude_captured_since,
                                    workspace_root=workspace_root)
        for row in list(res.get("auto_close") or []) + list(res.get("pending") or []):
            mid = _s(row.get("message_id"))
            hit = by_mid.get(mid)
            cid = _s(row.get("commitment_id"))
            if hit is None or not cid:
                continue
            raw, ref, m = hit
            names = party_names(workspace_root, by_cid.get(cid),
                                user_person_id=user_person_id,
                                extra=list(owner_names or [])
                                + list(m.get("recipient_names") or []))
            if not shared_content_words(f"{m.get('subject') or ''} {m.get('body') or ''}",
                                        row.get("title"), names=names):
                continue
            prev = best.get(cid)
            if prev is None or (row.get("score") or 0) > (prev[0].get("score") or 0):
                best[cid] = (row, ref)

    proposed: List[dict] = []
    if best:
        already = {(p.get("data") or {}).get("commitment_id")
                   for p in load_open_review_proposals(str(events_path))}
        rows_out = []
        for cid, (row, ref) in best.items():
            if cid in already:
                continue
            ev = build_pending_review_event(
                commitment_id=cid,
                primary_thread_id=row.get("primary_thread_id") or "",
                source_skill=source_skill,
                proposed_resolution="auto_resolve",
                score=row.get("score") or 0,
                evidence=row.get("evidence") or f"matched an item in {ref.get('kind')}",
                next_seq=None,
                title=row.get("title") or "",
                evidence_ts=row.get("ts") or None,
                source_ref=_ref_string(ref),
            )
            ev["data"]["connector_source_ref"] = dict(ref)
            rows_out.append(ev)
            proposed.append({"commitment_id": cid, "source_ref": _ref_string(ref)})
        if rows_out:
            append_event(events_path, rows_out, holder=source_skill)

    said = signal_skips({
        r["category"]: {"people": people_skip.get(r["category"]),
                        "projects": projects_skip.get(r["category"])}
        for r in close_recs})
    receipt = {"ran": True, "status": "complete", "leg": JOB_ID,
               MAINTENANCE_WINDOW_FIELD: window, "proposed": proposed,
               "n_proposed": len(proposed), "n_closed": 0,
               "people_seen": sorted(people), "projects_seen": sorted(projects),
               "n_people_seen": len(people), "n_projects_seen": len(projects),
               "connector_skips": {r["category"]: r["skip_reason"]
                                   for r in close_recs if r.get("skip_reason")}}
    receipt.update(counts)
    receipt["n_signal_no_pointer"] = n_no_pointer
    if said:
        receipt["signal_skips"] = said
    receipt.update(hand_off_seen(workspace_root, seen_people, seen_projects,
                                 source_skill=source_skill))
    receipt["summary"] = (f"{len(proposed)} possible close"
                          f"{'s' if len(proposed) != 1 else ''} to confirm from "
                          "your documents, deals and contracts." if proposed else "")
    note_waiting(receipt)
    note_unread(workspace_root, receipt, unread, unsure)
    _log_receipt(workspace_root, receipt, fired_via=fired_via)
    return receipt


__all__ = ["JOB_ID", "KINDS", "ITEM_FIELDS", "records",
           "plan_connector_proposals", "build_ref",
           "propose_from_connectors_and_receipt"]
