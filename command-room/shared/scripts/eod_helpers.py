#!/usr/bin/env python3
"""End of Day — the named entry points, for the workspace access layer.

WHY THIS MODULE EXISTS (ORCH2, Night M3). `orchestrator-past-meetings.md`
(the End of Day chat, under both of its task ids) and `end-of-day/SKILL.md`
carried seventeen python blocks that opened the customer's workspace
in-process: the lateness check, the catch-up window and the backlog sweep,
the meeting dedup, the brief path, the capture fence, the commitment and
decision passes over a transcript, the own-word closes, the prep grading, the
capture admission gate, the shadow lane, the render set, the renderer
preflight, the per-meeting write counts, the page render and the brief's
shape. On a merged seat each ran where the files are not. Each is now ONE
plan line naming a function here — or, where the block wrote something that
is not a ledger row, naming a writer on `RUN_WRITER_ALLOWLIST`.

TWO KINDS OF FUNCTION, KEPT APART BY THE TWO DOORS.

  * READ / COMPUTE (on `RUN_HELPER_ALLOWLIST`, past the transitive
    write-scan): everything but the five writers below. A row a delegated writer
    would append comes back in `rows` / `pending_rows` for the caller's
    `plan append_jsonl`.
  * WRITE (on `RUN_WRITER_ALLOWLIST` ONLY — never on the read list, which
    guard G74 enforces): `apply_cru_pass` and `run_shadow_lane`. Each is the
    old block's own write half, moved whole, because its writer takes
    arguments no JSON envelope can carry — a mutable `set` the pass adds to,
    a budget `dict` it counts into, an open set computed beside the data —
    and because the old block's ORDER is part of its contract (the walk is
    recorded only after the pass's own appends landed). No logic is new in
    either; each calls the writers the block called, in the block's order.
    MIGRATE3-EOD (Train 2b) added three more, each on the write list only:
    `run_end_of_day_pack` (Phase C's pack, which also lands the pack the
    receipt reads back), `answer_eod_questions` and `score_coach_answer`
    (the SKILL's answer handlers). Fix round 2 added `stage_fire_input`,
    `run_cru_pass` and `record_fire_stopped`. EODHARD3 (Train 2b) added
    `reconcile_sent_staged` and `reconcile_chat_staged` (the close leg over
    a batch staged raw and cut to its named fields in code) and
    `clear_fire_staging` (the fire's staged files removed).

3.10-safe: this module ships in the runtime manifest.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from morning_brief_helpers import _jsonable, answers, base_cwd, lateness_verdict

#: The task id the old lateness block passed — the file serves both
#: `end-of-day` and its predecessor `past-meetings`, and `late_fire`
#: resolves either to the one schedule (EOD2).
TASK_ID = "past-meetings"

#: The surface the brief-shape settings and the page name.
SURFACE = "end-of-day"

WIDGET_DIR_REL = "_hq/.system/widgets"


def _events_path(workspace_root: str) -> str:
    return str(Path(workspace_root) / "_hq" / "data" / "events.jsonl")


def _instant(value: Optional[str]):
    if not value:
        return None
    try:
        dt = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt


# ---------------------------------------------------------------------------
# Phase 2.9, Phase 3, Phase 3.5 — the reads that decide what the fire owes
# ---------------------------------------------------------------------------

@answers
def lateness(workspace_root: str, *, fired_via: str = "manual",
             env_date: str = "", now: Optional[Any] = None,
             task_id: str = TASK_ID) -> Dict[str, Any]:
    """The End of Day's lateness verdict (Phase 2.9), every row the check
    writes handed back in `pending_rows`."""
    return lateness_verdict(workspace_root, task_id, fired_via=fired_via,
                            env_date=env_date, now=now)


@answers
def catchup_window(workspace_root: str, *, task_id: str = TASK_ID,
                   floor_hours: int = 24, cap_days: int = 30) -> Dict[str, Any]:
    """Phase 3's window — `catchup.catchup_window(ws, 'past-meetings',
    floor_hours=24, cap_days=30)`, the span since the last SUCCESSFUL run."""
    from catchup import catchup_window as _window

    return _jsonable(_window(workspace_root, task_id, floor_hours=floor_hours,
                             cap_days=cap_days))


@answers
def unprocessed_backlog(workspace_root: str, *, limit: int = 5,
                        now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Phase 3's catch-up sweep — `meeting_discovery.unprocessed_backlog(ws,
    limit=5)`: every meeting with a record and no receipt, oldest first."""
    from meeting_discovery import unprocessed_backlog as _backlog

    kwargs: Dict[str, Any] = {"limit": int(limit)}
    at = _instant(now_iso)
    if at is not None:
        kwargs["now"] = at
    return _jsonable(_backlog(workspace_root, **kwargs))


@answers
def dedup_meetings(workspace_root: str,
                   records: Optional[List[Dict[str, Any]]] = None,
                   records_rel: Optional[str] = None) -> Any:
    """Phase 3.5 — `dedup_meetings(<the raw records>, processed=
    processed_index(ws, include_bare_meetings=False))`, projected to the
    seven fields the fire routes on. `include_bare_meetings=False` is
    load-bearing: a record is not a receipt."""
    from meeting_discovery import dedup_meetings as _dedup
    from meeting_discovery import processed_index

    if records_rel:  # FIX ROUND 2: the day's records, staged beside the data
        records = _read_staged(workspace_root, records_rel, "meeting_records")
    prior = processed_index(workspace_root, include_bare_meetings=False)
    keys = ("source_ref", "classification", "classification_reason",
            "certain", "action", "duplicate_of", "note_owner")
    return _jsonable([{k: d[k] for k in keys}
                      for d in _dedup(list(records or []), processed=prior)])


# ---------------------------------------------------------------------------
# Phase 4 — per meeting
# ---------------------------------------------------------------------------

@answers
def brief_path(workspace_root: str, *, slug: str = "",
               date: str = "") -> Dict[str, Any]:
    """Step 7's path — `brief_path.get_brief_path(ws, 'past_meeting', slug,
    date)`, its `computer://` url and whether it is session-scoped. The
    meetings folder itself is made by `brief_path:ensure_brief_directory`
    through the WRITE door first; this reads nothing it would create."""
    from brief_path import (get_brief_artifact_url, get_brief_path,
                            is_session_scoped_path)

    path = get_brief_path(workspace_root, "past_meeting", slug, date)
    try:
        rel = Path(path).resolve().relative_to(
            Path(workspace_root).resolve()).as_posix()
    except ValueError:
        rel = None
    return {"brief_path": path, "rel": rel,
            "brief_url": get_brief_artifact_url(path),
            "session_scoped": bool(is_session_scoped_path(path))}


@answers
def capture_fence(workspace_root: str, *, capture_leg_start: str = "",
                  n_captured_full_depth: int = 0,
                  now_iso: Optional[str] = None) -> Dict[str, Any]:
    """CAPFENCE1 — `capture_fence_elapsed_ms` and `capture_fence_should_defer`
    against `CAPTURE_FENCE_MS`, between meetings."""
    from end_of_day import (CAPTURE_FENCE_MS, capture_fence_elapsed_ms,
                            capture_fence_should_defer)

    at = _instant(now_iso)
    elapsed = capture_fence_elapsed_ms(capture_leg_start, now=at)
    return {"elapsed_ms": elapsed, "fence_ms": CAPTURE_FENCE_MS,
            "defer": capture_fence_should_defer(elapsed,
                                                int(n_captured_full_depth))}


@answers
def cru_walk(workspace_root: str, *, source_ref: str = "",
             meeting_ts: str = "", fire_start: str = "",
             attendee_person_ids: Optional[List[str]] = None,
             transcript_text: str = "") -> Dict[str, Any]:
    """Phase 4.6's READ half — the walk-ledger check, then the matcher.

    `eod_incremental.already_walked` first: a transcript whose walk already
    completed answers `walked` and nothing else (the pass is skipped). Else
    `cru_match.match_transcript_to_commitments` over the open set, with the
    §6 fence (`exclude_captured_since=fire_start`), EVORDER layer 3 (THIS
    meeting's own start) and F-28's roster. The answer carries what the
    write half needs: `results`, `already_proposed`, the fire's batch id and
    the stale-evidence count before the walk.
    """
    from commitment_policy import mint_fire_batch_id
    from cru_match import (load_open_commitments,
                           match_transcript_to_commitments,
                           open_review_proposal_ids)
    from eod_incremental import already_walked

    events_path = _events_path(workspace_root)
    walked = already_walked(workspace_root, evidence_ref=source_ref,
                            evidence_ts=meeting_ts)
    if walked is not None:
        return {"walked": _jsonable(walked), "skip": True}
    cru_diag: Dict[str, Any] = {}
    results = match_transcript_to_commitments(
        open_commitments=load_open_commitments(events_path),
        attendee_person_ids=list(attendee_person_ids or []),
        transcript_text=transcript_text,
        transcript_source_ref=source_ref,
        exclude_captured_since=fire_start or None,
        transcript_ts=meeting_ts or None,
        diagnostics=cru_diag,
        workspace_root=workspace_root)
    return _jsonable({
        "walked": None, "skip": False, "results": results,
        "already_proposed": sorted(open_review_proposal_ids(events_path)),
        "batch_id": mint_fire_batch_id(fire_start or None),
        "stale_before": cru_diag.get("stale_evidence_dropped", 0),
        "diagnostics": cru_diag,
    })


def apply_cru_pass(workspace_root: str, *, results: Optional[List[Any]] = None,
                   meeting_ref: str = "", transcript_ts: str = "",
                   already_proposed: Optional[List[str]] = None,
                   batch_id: Optional[str] = None,
                   n_stale: int = 0,
                   review_budget: Optional[Dict[str, Any]] = None
                   ) -> Dict[str, Any]:
    """Phase 4.6's WRITE half — a WRITER, on the write list only.

    The old block's two writes, in its order and no other:
    `commitment_policy_pass.apply_transcript_results` (closes, updates,
    proposals, each through its own writer) with a real `set` and a real
    budget `dict` — which is why this cannot be the writer called directly
    through the door — and then, only after those appends landed,
    `eod_incremental.record_walk` for this transcript. Answers the pass's
    counts, the budget it counted into, and the walk record.
    """
    from commitment_policy_pass import apply_transcript_results
    from eod_incremental import record_walk

    proposed = set(already_proposed or [])
    # ONE budget for the whole fire (TITLEMINT1): the caller hands back the
    # budget the previous transcript's pass returned, `{}` for the first. A
    # fresh dict per transcript would make the 25-per-fire cap per-meeting.
    budget: Dict[str, Any] = dict(review_budget or {})
    rows = list(results or [])
    counts = apply_transcript_results(
        workspace_root, rows, meeting_ref=meeting_ref,
        transcript_ts=transcript_ts or None, already_proposed=proposed,
        review_budget=budget, batch_id=batch_id)
    walk = record_walk(workspace_root, evidence_ref=meeting_ref,
                       evidence_ts=transcript_ts, n_stale=int(n_stale or 0),
                       n_results=len(rows))
    # `already_proposed` comes back as the pass left it: the set the pass
    # adds to is the fire's one set, and the next transcript's walk seeds
    # from the same disk these appends just reached.
    return _jsonable({"counts": counts, "review_budget": budget,
                      "walk": walk, "already_proposed": sorted(proposed),
                      "line": cru_diagnostic_line(counts, int(n_stale or 0),
                                                  budget)})


#: The counts the CRU diagnostic line prints, in the old block's order, each
#: with the label the line has always used (Phase 5 and the receipt keys are
#: read off these labels — "the last number on that stdout line").
_CRU_LINE_FIELDS = (("resolved", "n_closed"),
                    ("confirmed_closed", "n_confirm_closed"),
                    ("withheld", "n_close_withheld"),
                    ("closes_enabled", "closes_enabled"),
                    ("updated", "n_updated"),
                    ("chips", "n_proposed"),
                    ("rescored", "n_rescored"),
                    ("silent_pending", "n_silent_pending"),
                    ("silent_out_of_band", "n_silent_out_of_band"),
                    ("silent_dup", "n_silent_dup"),
                    ("close_refused", "n_close_refused"))


def cru_diagnostic_line(counts: Dict[str, Any], n_stale: int,
                        review_budget: Dict[str, Any]) -> str:
    """The old block's own stdout line, byte for byte: the pass's counts,
    then EVORDER layer 3's refusals, then TITLEMINT1's suppressed count."""
    parts = [f"{label}={(counts or {}).get(key)}"
             for label, key in _CRU_LINE_FIELDS]
    parts.append(f"stale_evidence_skipped={n_stale}")
    parts.append("proposals_suppressed="
                 f"{(review_budget or {}).get('proposals_suppressed', 0)}")
    return "CRU past-meetings: " + " ".join(parts)


@answers
def decision_rows(workspace_root: str, *,
                  transcripts: Optional[List[Dict[str, Any]]] = None,
                  transcripts_rel: Optional[str] = None,
                  fire_start: str = "") -> Dict[str, Any]:
    """Phase 4.6.b — the decision pass, composed and NOT written.

    `decision_match.match_transcript_to_decisions` over the open decisions
    for every newly-processed transcript, with WALKFIX1 Item A's same-fire
    fence and CAPTUREONCE1 2.3's this-fire fence (both arms); each
    recommendation built by the module's own builders, the evidence word
    read by identity (POLICY1-B), and the batch filtered by
    `new_supersede_proposals`. The rows come back for ONE `plan append_jsonl`.
    """
    from decision_match import (build_decision_resolved_event,
                                build_decision_supersede_proposal_event,
                                load_open_decisions,
                                match_transcript_to_decisions,
                                new_supersede_proposals)

    if transcripts_rel:  # FIX ROUND 2: the fire's transcripts, staged
        transcripts = _read_staged(workspace_root, transcripts_rel,
                                   "decision_transcripts")
    events_path = _events_path(workspace_root)
    opens = load_open_decisions(events_path)
    items = list(transcripts or [])
    processed_this_fire = [t.get("source_ref") for t in items]
    to_append: List[Dict[str, Any]] = []
    for transcript in items:
        results = match_transcript_to_decisions(
            open_decisions=opens,
            attendee_person_ids=transcript.get("attendee_person_ids") or [],
            transcript_text=transcript.get("text") or "",
            exclude_captured_since=fire_start or None,
            source_ref=transcript.get("source_ref"),
            processed_this_fire=processed_this_fire,
            workspace_root=workspace_root)
        for r in results:
            rec = r["recommendation"]
            sig = r.get("has_completion_signal")
            word = ("reversal language" if r.get("has_reversal_signal") is True
                    else "completion language" if sig is True
                    else "title match" if sig is False
                    else "completion not assessed")
            evidence = f"Past meeting transcript ({word})"
            if rec == "decision_resolved":
                to_append.append(build_decision_resolved_event(
                    decision_id=r["decision_id"],
                    primary_thread_id=r["primary_thread_id"],
                    source_skill="past-meetings", evidence=evidence,
                    next_seq=None))
            elif rec == "decision_supersede_proposed":
                to_append.append(build_decision_supersede_proposal_event(
                    decision_id=r["decision_id"],
                    primary_thread_id=r["primary_thread_id"],
                    source_skill="past-meetings", evidence=evidence,
                    next_seq=None, score=r.get("score"),
                    title=r.get("title", ""),
                    source_ref=transcript.get("source_ref")))
    filtered = new_supersede_proposals(to_append, events_jsonl_path=events_path)
    rows = list(filtered["events"])
    return _jsonable({
        "rows": rows,
        "n_resolved": sum(1 for e in rows if e.get("type") == "decision_resolved"),
        "n_supersede_proposed": sum(1 for e in rows if e.get("type")
                                    == "decision_supersede_proposed"),
        "n_duplicate_pairs": filtered["n_duplicate_pairs"]})


@answers
def prep_feedback(workspace_root: str, *, meeting_id: str = "",
                  meeting_type: str = "",
                  predicted_sections: Optional[Dict[str, Any]] = None,
                  transcript_topics: Optional[List[str]] = None,
                  person_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Phase 6 Loop 3 — `prep_grading.grade_brief` and the ONE
    `prep_feedback` row `build_prep_feedback_event` builds, composed and NOT
    written."""
    from prep_grading import build_prep_feedback_event, grade_brief

    grade = grade_brief(dict(predicted_sections or {}),
                        list(transcript_topics or []))
    row = build_prep_feedback_event(meeting_id=meeting_id,
                                    meeting_type=meeting_type, grade=grade,
                                    person_ids=list(person_ids or []))
    return _jsonable({"grade": grade, "rows": [row]})


def run_shadow_lane(workspace_root: str, *,
                    payloads: Optional[List[Dict[str, Any]]] = None,
                    payloads_rel: Optional[str] = None,
                    fire_start: str = "") -> Dict[str, Any]:
    """Phase 4.8's non-attendee lane — a WRITER, on the write list only.

    The lane is a shadow ("no substrate writes" is its own contract), but the
    code it routes through can create a person record, which is why it runs
    through the write door and not the read door. The old block's call, as
    it was: `meeting_discovery.run_shadow_pass` over the discovered meetings,
    with the open set read beside the data, then `render_shadow_report`.
    """
    from cru_match import load_open_commitments
    from meeting_discovery import render_shadow_report, run_shadow_pass

    if payloads_rel:  # FIX ROUND 2: the discovered meetings, staged
        payloads = _read_staged(workspace_root, payloads_rel,
                                "shadow_payloads")
    events_path = _events_path(workspace_root)
    report = run_shadow_pass(list(payloads or []),
                             workspace_root=workspace_root,
                             events_path=events_path,
                             open_commitments=load_open_commitments(events_path),
                             fire_start=fire_start or None)
    return _jsonable({"text": render_shadow_report(report),
                      "counts": report.get("counts"),
                      "would_be": report.get("would_be")})


# ---------------------------------------------------------------------------
# Phase 5 / 6 — the render set, the preflight, the counts, the page
# ---------------------------------------------------------------------------

@answers
def render_set(workspace_root: str, *,
               decisions: Optional[List[Dict[str, Any]]] = None,
               coverage: Optional[Dict[str, Any]] = None,
               now_iso: Optional[str] = None) -> Dict[str, Any]:
    """MEETCOUNT1 — the ONE producer. `eod_incremental.prior_briefed_refs`
    (the prior-capture set), `end_of_day.meeting_render_set` over the Phase
    3.5 rows with their mapped status, and the coverage strip's meetings
    line reconciled from it (`reconcile_meetings_line`)."""
    from end_of_day import meeting_render_set, reconcile_meetings_line
    from eod_incremental import prior_briefed_refs

    kwargs: Dict[str, Any] = {}
    at = _instant(now_iso)
    if at is not None:
        kwargs["now"] = at
    prior = {r["source_ref"]: r
             for r in prior_briefed_refs(workspace_root, **kwargs)}
    rset = meeting_render_set(list(decisions or []))
    return _jsonable({"prior": prior, "render_set": rset,
                      "coverage": reconcile_meetings_line(dict(coverage or {}),
                                                          rset)})


@answers
def renderer_preflight(workspace_root: str) -> Dict[str, Any]:
    """Phase 6 step 1 — the renderer and the brief-path imports, proven in the
    runtime beside the data: `{ok, missing}`."""
    missing = []
    for module, names in (
        ("widget_transport", ("render_and_persist",)),
        ("chat_output_renderer", ("validate_chat_output", "CANONICAL_ACTIONS",
                                  "CanonicalActionError", "LeakDetectedError",
                                  "WrapperContractError")),
        ("brief_path", ("get_brief_path", "get_brief_artifact_url")),
    ):
        try:
            loaded = __import__(module)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{module} ({type(exc).__name__}: {exc})")
            continue
        for name in names:
            if not hasattr(loaded, name):
                missing.append(f"{module}.{name}")
    return {"ok": not missing, "missing": missing}


@answers
def meeting_write_counts(workspace_root: str,
                         source_refs: Optional[List[str]] = None) -> Dict[str, Any]:
    """`{source_ref: counts}` — `meeting_capture.count_meeting_writes` per
    meeting: the events actually on disk, never the extraction intent."""
    from meeting_capture import count_meeting_writes

    return _jsonable({str(ref): count_meeting_writes(workspace_root, str(ref))
                      for ref in (source_refs or [])})


@answers
def render_eod_page(workspace_root: str, data_view: Dict[str, Any], *,
                    wrapper: str = "fragment",
                    name_hint: str = SURFACE) -> Dict[str, Any]:
    """`{html, page_rel, bytes_len, pending_rows}` — the End of Day widget,
    rendered where the data is, by the same renderer and the same
    wrapper-contract validator `widget_transport.render_and_persist` runs,
    with nothing persisted: the caller lands the page with `plan write`."""
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


# ---------------------------------------------------------------------------
# Phase 5 — THE receipt, composed by its own writer and NOT written
# ---------------------------------------------------------------------------

#: The three legs this file times across its own process boundaries, keyed
#: as the caller hands them in, mapped to the phase names the ledger knows.
_LEG_PHASES = (("close", "PHASE_CLOSE"), ("capture", "PHASE_CAPTURE"),
               ("post", "PHASE_POST"))


@answers
def plan_eod_receipt(workspace_root: str, *,
                     pack: Optional[Dict[str, Any]] = None,
                     pack_rel: Optional[str] = None,
                     pack_updates: Optional[Dict[str, Any]] = None,
                     fired_via: Optional[str] = None,
                     duration_ms: Optional[int] = None,
                     late_tier: Optional[str] = None,
                     capture_leg: Optional[Dict[str, Any]] = None,
                     leg_ms: Optional[Dict[str, Any]] = None,
                     extra_data: Optional[Dict[str, Any]] = None
                     ) -> Dict[str, Any]:
    """`{rows}` — the day-close's ONE `pack_run`, by `end_of_day.
    log_end_of_day_receipt` with its append held.

    Phase 5's two blocks, as they were: a `PhaseLedger` seeded from the
    pack's own `phase_timings` (`merge_snapshot`, so the fourteen pack-build
    phases are not discarded) with the three legs this file timed folded in
    by `record_leg` — a leg with no number is skipped, never zeroed (Ruling
    4) — and then THE writer, `phase_ledger=led`. The row that comes back is
    the one it writes (same `past-meetings` task id, same keys, `confirm_ids`
    derived from the pack); the caller lands it with ONE `plan append_jsonl`.
    If the ledger cannot be built the receipt is still owed, and is written
    without it — the orchestrator's own best-effort rule.

    MIGRATE3-EOD fix round 1 (review N-1): on a fire the pack comes as
    `pack_rel`, the file `run_end_of_day_pack` landed, read here beside the
    data, with `pack_updates` (only the keys in `PACK_UPDATE_KEYS`) set on it
    by name; a pack handed inline is still taken (the legacy leg and its
    golden), but a real day's pack is too large to cross the door that way.
    """
    import end_of_day as eod
    from inbox_helpers import _captured_appends

    if pack_rel:
        pack = _read_pack(workspace_root, pack_rel)
    if pack is None:
        raise ValueError("no pack: pass the pack writer's pack_rel")
    unknown = sorted(set(pack_updates or {}) - set(PACK_UPDATE_KEYS))
    if unknown:
        raise ValueError("pack_updates carries keys Phase 5 does not set: "
                         + ", ".join(unknown))
    pack = dict(pack or {})
    pack.update(dict(pack_updates or {}))

    led = None
    try:
        led = eod.PhaseLedger()
        led.merge_snapshot((pack or {}).get("phase_timings"))
        for key, phase in _LEG_PHASES:
            ms = (leg_ms or {}).get(key)
            if ms is not None:
                led.record_leg(getattr(eod, phase), ms)
    except Exception:  # noqa: BLE001 — instrumentation never costs the receipt
        led = None
    kwargs: Dict[str, Any] = {"duration_ms": duration_ms,
                              "late_tier": late_tier,
                              "capture_leg": dict(capture_leg or {}) or None,
                              "extra_data": dict(extra_data or {})}
    if fired_via:
        kwargs["fired_via"] = fired_via
    if led is not None:
        kwargs["phase_ledger"] = led
    with _captured_appends() as captured:
        eod.log_end_of_day_receipt(workspace_root, dict(pack or {}), **kwargs)
    return {"rows": _jsonable(list(captured))}


# ---------------------------------------------------------------------------
# MIGRATE3-EOD (Train 2b, F-T2-15): the Phase C pack and the answer handlers
# ---------------------------------------------------------------------------

def _is_refusal(exc: BaseException) -> bool:
    """A writer that cannot name who it writes for raises an exception that
    carries `cr_refusal_reason`; the door turns it into `ok:false` with its
    one sentence, so it must pass through untouched (MF-27's rule)."""
    return bool(getattr(exc, "cr_refusal_reason", None))


def run_end_of_day_pack(workspace_root: str, *, mode: str = "scheduled",
                        close_result: Optional[Dict[str, Any]] = None,
                        calendar_events: Optional[List[Any]] = None,
                        calendar_available: Optional[bool] = None,
                        connector_gaps: Optional[List[Any]] = None,
                        lateness: Optional[Dict[str, Any]] = None,
                        now_iso: Optional[str] = None,
                        calendar_rel: Optional[str] = None) -> Dict[str, Any]:
    """Phase C on every seat: `{ok, pack, pack_rel}`, or `{ok: false, line}`.

    EODHARD3 MUST 1: on a fire the calendar comes as `calendar_rel`, the raw
    events the fire staged (`stage_fire_input`, kind `calendar_events`), read
    here beside the data; every event, staged or handed in, is cut to
    `CALENDAR_KEEP_FIELDS` in code before the builder sees it.

    A WRITER, on `RUN_WRITER_ALLOWLIST` only: the SAME builder the
    `surface_drivers.py end-of-day` CLI wraps, `build_end_of_day_pack`, with
    the CLI's four JSON inputs as named arguments (a fire hands them in one
    `args_file`). It writes where the data is, exactly as the CLI did: the
    pack's audit copy under `_hq/.system/briefs/`, and whatever the builder
    appends on its way. It also lands a copy of the shaped pack under
    `PACK_DIR_REL` and answers its workspace-relative path as `pack_rel`: the
    Phase 5 receipt reads the pack from there, because a real day's pack is
    too large for the door's argument fence (fix round 1, review N-1).

    `calendar_available` follows the CLI: given `calendar_events` it is True,
    omitted it is False, unless the caller says otherwise.

    THE FIRE FAILS IN ONE SENTENCE (SPEC SURFACEFIX1 5.3), the CLI branch's
    own floor moved here: any exception out of the builder writes the
    `surface_failed` receipt (`surface_drivers.log_surface_failed`) and answers
    `ok: false` with `surface_drivers.SURFACE_FAILED_LINES`'s End of Day
    sentence, and nothing else: no class name, no message, no path.
    """
    import json as _json

    import surface_drivers as sd

    try:
        if calendar_rel:  # EODHARD3 MUST 1: the raw events, by path
            calendar_events = _read_raw(workspace_root, calendar_rel,
                                        "calendar_events")
        elif calendar_events is not None:
            calendar_events = trim_to_fields(calendar_events,
                                             CALENDAR_KEEP_FIELDS)
        if calendar_available is None:
            calendar_available = calendar_events is not None
        pack = sd.build_end_of_day_pack(
            workspace_root, mode=mode, now_iso=now_iso,
            close_result=close_result, calendar_events=calendar_events,
            calendar_available=bool(calendar_available),
            connector_gaps=connector_gaps, lateness=lateness)
        # the CLI's own serialisation (`default=str`), so the answer that
        # crosses the door is the line the CLI printed, parsed
        shaped = _json.loads(_json.dumps(pack, ensure_ascii=False,
                                         default=str))
        pack_rel = _land_pack(workspace_root, shaped)
    except Exception as exc:  # noqa: BLE001 - one sentence, never a trace
        if _is_refusal(exc):
            raise
        sd.log_surface_failed(workspace_root, sd.SURFACE_FAILED_EOD, exc,
                              mode=mode, now_iso=now_iso)
        _clear_staging(workspace_root)  # review N-6: no raw mail left staged
        return {"ok": False,
                "line": sd.SURFACE_FAILED_LINES[sd.SURFACE_FAILED_EOD]}
    return {"ok": True, "pack": shaped, "pack_rel": pack_rel}


#: Where the pack writer lands the pack the Phase 5 receipt reads back
#: (MIGRATE3-EOD fix round 1, review N-1). The door's argument fence refuses
#: a payload that walks past the door's argument cap
#: (`workspace_access.ARG_WALK_CAP`); a real day's pack walked 2,138 to 3,511,
#: past the cap of the time, and the by-path shape stays the product
#: (D-T2B-7), so the pack never crosses the door as an argument: the receipt
#: is composed from this file, read beside the data.
#: FIX ROUND 2 (R-1): the fire's own folder, which no reader globs (the
#: coach's `PACK_GLOB` reads `_hq/.system/briefs/end-of-day-pack-*.json`),
#: and ONE file per workspace-local day, overwritten by a second fire.
PACK_DIR_REL = "_hq/.system/eod-fire"
PACK_FILE_PREFIX = "day-close-pack-"

#: The four pack keys Phase 5 sets after Phase D, by name, and nothing else:
#: the coverage line reconciled by `render_set`, the batch-cap and capture
#: fence markers, and the render decision.
PACK_UPDATE_KEYS = ("coverage", "window_incomplete_before",
                    "n_time_fence_deferred", "coverage_disclosed")


_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _land_pack(workspace_root: str, shaped: Dict[str, Any]) -> str:
    """Write the shaped pack under `PACK_DIR_REL` and answer its
    workspace-relative path.

    LOWS2 row 2 (BATTFIX1 S-1 / N-2): the fire's folder, resolved, must sit
    inside the workspace, as `stage_fire_input` and `_clear_staging` ask; a
    folder replaced by a link out of the workspace raises here, before the
    write, and `run_end_of_day_pack` answers its one sentence."""
    import json as _json

    from atomic_write import atomic_write_text

    if not _inside_workspace(workspace_root, Path(workspace_root) / PACK_DIR_REL):
        raise ValueError("the End of Day's fire folder is not inside the "
                         "workspace; the pack is not landed")
    day = str(shaped.get("for_date") or "")[:10]
    if not _DAY_RE.match(day):
        day = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    rel = f"{PACK_DIR_REL}/{PACK_FILE_PREFIX}{day}.json"
    atomic_write_text(Path(workspace_root) / rel,
                      _json.dumps(shaped, ensure_ascii=False))
    return rel


def _read_pack(workspace_root: str, pack_rel: str) -> Dict[str, Any]:
    """The pack `run_end_of_day_pack` landed, read beside the data. Only a
    file the pack writer names (`PACK_DIR_REL`, `PACK_FILE_PREFIX`, `.json`)
    inside this workspace is read; anything else raises."""
    import json as _json

    root = Path(workspace_root).resolve()
    target = (root / str(pack_rel)).resolve()
    folder = (root / PACK_DIR_REL).resolve()
    if (target.parent != folder or not target.name.startswith(PACK_FILE_PREFIX)
            or target.suffix != ".json"):
        raise ValueError("pack_rel is not a pack the End of Day pack writer "
                         "landed")
    loaded = _json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("the landed pack is not an object")
    return loaded


@answers
def resolve_choice(workspace_root: str, n: Any = 0, *,
                   action: str = "yes") -> Dict[str, Any]:
    """`yes [n]` / `no [n]` / `skip [n]` against this fire's own receipt:
    `end_of_day.resolve_choice(ws, n, action=...)`, read where the data is.
    Nothing is written; a refusal comes back in `refusal`, said verbatim."""
    from end_of_day import resolve_choice as _resolve

    return _jsonable(_resolve(workspace_root, n, action=action))


def answer_eod_questions(workspace_root: str, *,
                         answers: Optional[List[Dict[str, Any]]] = None,
                         answered_by: str = "customer",
                         individually_named: Optional[List[str]] = None,
                         source_ref: Optional[str] = None) -> Dict[str, Any]:
    """The evening's answers, landed: a WRITER, on the write list only.

    `eod_question_budget.apply_eod_answers` (the queue's own writers
    underneath, ONE batch, ONE `eod_question_answered` receipt, one `undo`)
    with the rows `resolve_choice` returned ok for, then `answer_ack` on its
    result: the answer carries the writer's own dict plus `ack`, the one
    sentence the fire posts back."""
    from eod_question_budget import answer_ack, apply_eod_answers

    res = apply_eod_answers(workspace_root, list(answers or []),
                            answered_by=answered_by,
                            individually_named=list(individually_named or []),
                            source_ref=source_ref)
    out = dict(_jsonable(res) or {})
    out["ack"] = answer_ack(res)
    return out


def score_coach_answer(workspace_root: str, *, n: Any = 0, score: Any = None,
                       answered_by: str = "customer") -> Dict[str, Any]:
    """`score [n] [1-10]`, recorded: a WRITER, on the write list only.

    `end_of_day.resolve_coach_score` (positional against this fire's receipt;
    it refuses a confirm row and a number outside one to ten, writing
    nothing) then `eod_question_budget.coach_answer_ack` on its result: the
    writer's own dict plus `ack`."""
    from end_of_day import resolve_coach_score
    from eod_question_budget import coach_answer_ack

    res = resolve_coach_score(workspace_root, n, score,
                              answered_by=answered_by)
    out = dict(_jsonable(res) or {})
    out["ack"] = coach_answer_ack(res)
    return out


#: EODTAP2 (wave 2, MIGRATE3-EOD N-8): the row taps on the End of Day that
#: close or move a commitment, spelled as `end_of_day.ROUTES` spells them.
#: `draft` writes nothing (email-writer's hand-off) and `confirm` is the
#: unified confirm path, so neither is handled by `close_from_eod`.
EOD_TAP_ACTIONS = ("mark done", "resolved", "drop", "push to [date]")

#: The one sentence each outcome posts back. Pinned here, typed once,
#: validated with the fire families on (`fired_via="scheduled"`); counts and
#: words only, never an id. `{day}` is `apply_later`'s own `moved_phrase`
#: (CUT-C item 5: the ack relays that string and never composes a weekday).
EOD_TAP_DONE_LINE = "Marked that one done."
EOD_TAP_DROPPED_LINE = "Let that one go. It is off your plate."
EOD_TAP_ALREADY_CLOSED_LINE = ("That one was already closed, so nothing "
                               "changed.")
EOD_TAP_DEFERRED_LINE = "Moved its due date to {day}."
EOD_TAP_SNOOZED_LINE = ("It stays open, since the date is theirs, and "
                        "comes back on {day}.")
EOD_TAP_NEEDS_DATE_LINE = ("That push needs a date, as a number of days or "
                           "as a year, month and day. Nothing moved.")
EOD_TAP_NOT_HANDLED_LINE = ("That answer is not one I can apply from this "
                            "list, so nothing changed.")
EOD_TAP_SUBITEMS_LINE = ("That one still has open steps under it, so it "
                         "stays open. Close those first.")
EOD_TAP_GONE_LINE = ("That item is no longer on the record, so nothing "
                     "changed.")
#: EODTAP2 fix round 1 (review N-1): the caller's `source_ref` is a
#: cross-check, never an override. When it is not the pointer the resolver
#: answers for that row now, the list moved (or the pointer was composed),
#: so nothing is written.
EOD_TAP_STALE_ROW_LINE = ("That list has changed since you saw it, so "
                          "nothing changed. Open the newest one and tap "
                          "again.")
#: EODTAP2 fix round 1 (review N-2): a tap with no person behind it. A close
#: would credit nobody, and a push on the customer's own item would take the
#: mute leg instead of moving the date, so nothing is written.
EOD_TAP_NO_ACTOR_LINE = ("I could not tell who made that tap, so nothing "
                         "changed. Tap it again.")


def close_from_eod(workspace_root: str, *, n: Any = 0, action: str = "",
                   resolved_by: str = "", source_ref: Optional[str] = None,
                   when: Optional[str] = None,
                   now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A tapped `mark done` / `resolved` / `drop` / `push to [date]` on the
    End of Day's row list, landed: a WRITER, on the write list only
    (EODTAP2, MIGRATE3-EOD N-8).

    Resolves the number through `end_of_day.resolve_choice` (the ONE
    resolver: positional against this fire's own receipt, refusing a stale
    or absent map, a number off the list and a verb on the wrong block) and,
    on a refusal, answers the resolver's own dict with its `refusal` as the
    `ack`, having written nothing. On `ok` it runs the ONE handler the
    apply-choices prose names for that verb, with the prose's arguments:

      * `mark done` / `resolved`: `commitment_state.close_commitment` with
        `resolution="done"`, `user_confirmed=True`,
        `source_skill="apply-choices"` and the `source_ref`;
      * `drop`: the same close with `resolution="dropped"`;
      * `push to [date]`: `commitment_state.parse_later_when(when, now_iso,
        workspace_path=...)` then ONE `commitment_state.apply_later(...,
        surface="end-of-day")`, which picks the leg by ownership (own item:
        `deferred`; owed to you: `snoozed`).

    The close always carries the pointer the resolver answers for that row
    (`session:<receipt id>:row<N>`). `source_ref` is optional and only a
    cross-check: when the caller hands one that is not that pointer (a
    flattened, composed or malformed value, or the list moved between the
    read and the tap), the answer is `stale_row` and nothing is written. The answer is the handler's own dict plus `ok` and
    `ack`. Two domain refusals of the closer are answered, not raised (the
    id no longer resolves; open sub-items): anything else raises, which the
    door reports.
    """
    verb = str(action or "").strip().lower()
    if verb not in EOD_TAP_ACTIONS:
        return {"ok": False, "status": "not_handled", "action": action,
                "n": n, "ack": EOD_TAP_NOT_HANDLED_LINE}
    # Review N-2: a missing or blank actor is refused before anything reads
    # or writes (the default keeps a form without the key off the traceback
    # path; the check keeps a blank one off the wrong push leg).
    if not isinstance(resolved_by, str) or not resolved_by.strip():
        return {"ok": False, "status": "no_actor", "action": action, "n": n,
                "ack": EOD_TAP_NO_ACTOR_LINE}
    resolved_by = resolved_by.strip()

    import commitment_state as cs
    from end_of_day import resolve_choice as _resolve

    target = dict(_jsonable(_resolve(workspace_root, n, action=verb)) or {})
    if not target.get("ok"):
        target["ack"] = target.get("refusal")
        return target
    cid = target.get("id")
    # Review N-1: the resolver's pointer is the only one written. A caller's
    # value may confirm it and nothing else; a flattened, composed or
    # malformed one is refused here, before any writer sees it.
    # Compared as identities (guard G30), never raw.
    from connector_adapters.provenance import dedup_key_of

    ref = target.get("source_ref")
    if source_ref and dedup_key_of(source_ref) != dedup_key_of(ref):
        return {"ok": False, "status": "stale_row", "commitment_id": cid,
                "n": n, "ack": EOD_TAP_STALE_ROW_LINE}
    try:
        if verb == "push to [date]":
            stamp = now_iso or _dt.datetime.now(_dt.timezone.utc).isoformat()
            day = (cs.parse_later_when(when, stamp,
                                       workspace_path=workspace_root)
                   if isinstance(when, str) else None)
            if not day:
                return {"ok": False, "status": cs.LATER_MISSING_STATUS,
                        "commitment_id": cid, "n": n,
                        "ack": EOD_TAP_NEEDS_DATE_LINE}
            res = cs.apply_later(workspace_root, cid, when_iso=day,
                                 actor_id=resolved_by,
                                 source_skill="apply-choices",
                                 surface="end-of-day")
            status = res.get("status")
            ack = {"deferred": EOD_TAP_DEFERRED_LINE,
                   "snoozed": EOD_TAP_SNOOZED_LINE,
                   "not_open": EOD_TAP_ALREADY_CLOSED_LINE}.get(
                       status, EOD_TAP_ALREADY_CLOSED_LINE)
        else:
            dropped = verb == "drop"
            res = cs.close_commitment(
                workspace_root, cid, resolved_by=resolved_by,
                evidence=("dropped from the End of Day" if dropped
                          else "marked done from the End of Day"),
                source_skill="apply-choices",
                resolution="dropped" if dropped else "done",
                user_confirmed=True, resolved_by_match="number",
                source_ref=ref)
            status = res.get("status")
            ack = (EOD_TAP_ALREADY_CLOSED_LINE
                   if status == "already_resolved"
                   else EOD_TAP_DROPPED_LINE if dropped
                   else EOD_TAP_DONE_LINE)
    except cs.OpenSubitemsError:
        return {"ok": False, "status": "open_subitems", "commitment_id": cid,
                "n": n, "ack": EOD_TAP_SUBITEMS_LINE}
    except cs.CommitmentIdError:
        return {"ok": False, "status": "unknown_id", "commitment_id": cid,
                "n": n, "ack": EOD_TAP_GONE_LINE}
    out = dict(_jsonable(res) or {})
    out["ok"] = status in ("closed", "already_resolved", "deferred",
                           "snoozed", "not_open")
    out["ack"] = (ack.format(day=res.get("moved_phrase") or day)
                  if verb == "push to [date]" else ack)
    return out


# ---------------------------------------------------------------------------
# FIX ROUND 2 (review R-2, R-6, the general rule): nothing that scales with
# the day crosses the door as an argument. The door's argument fence walks
# every key and value of `args` and of an `args_file` and refuses past the
# door's argument cap (`workspace_access.ARG_WALK_CAP`; CAP1 raised it as
# headroom, D-T2B-7), and a command line has its own byte
# limit. So what the fire fetched is STAGED: the session hands it as bytes
# under a `*_base64` key (the door's content carrier, whose value is not
# walked; `deliverables:export_claude_doc` is the precedent), the writer
# lands it in the fire's own folder, one file per kind, overwritten each
# fire, and the step that needs it reads it by `*_rel` beside the data.
# What is computed beside the data never crosses at all: the CRU pass runs
# its walk and its write in ONE writer.
# ---------------------------------------------------------------------------

#: EODHARD3 MUST 1: the three inputs the fire fetches from a connector and
#: stages AS THE CONNECTOR RETURNED THEM. The chat no longer trims anything:
#: the writer that reads each one keeps only that input's named fields
#: (`KEEP_FIELDS`), in code, beside the data.
RAW_INPUTS = ("sent_messages", "chat_messages", "calendar_events")

#: The kinds a fire stages, each ONE file in `PACK_DIR_REL`, and the only
#: kinds `stage_fire_input` lands.
STAGED_INPUTS = ("meeting_records", "decision_transcripts",
                 "shadow_payloads") + RAW_INPUTS

#: What ONE calendar event carries into the pack writer: the five fields
#: `end_of_day.compute_tomorrow` reads, and nothing else (review R-3).
CALENDAR_EVENT_FIELDS = ("id", "title", "start", "time_label", "prep_exists")

#: EODHARD3 MUST 1: the fields the pack writer KEEPS of a staged raw calendar
#: event: `CALENDAR_EVENT_FIELDS` plus the two spellings `compute_tomorrow`
#: also reads (`meeting_id` for `id`, `summary` for `title`), so a raw event
#: from the connector builds the same `tomorrow` block the trimmed one did.
CALENDAR_KEEP_FIELDS = ("id", "meeting_id", "title", "summary", "start",
                        "time_label", "prep_exists")

#: ...of a staged raw Sent message: the mail plan's `message_fields`
#: (`reconcile_sent_commitments.SENT_MESSAGE_FIELDS`, pinned equal).
SENT_KEEP_FIELDS = ("message_id", "ts", "thread_id", "has_attachment",
                    "recipient_person_ids", "recipient_names",
                    "recipient_emails", "subject", "body")

#: ...of a staged raw chat message: the chat plan's `message_fields`
#: (`chat_reconcile.CHAT_MESSAGE_FIELDS`, pinned equal).
CHAT_KEEP_FIELDS = ("chat_or_channel_id", "message_id", "ts", "user_id",
                    "user_name", "text", "thread_ts", "permalink")

#: One kept-field list per raw input, by staged kind.
KEEP_FIELDS = {"sent_messages": SENT_KEEP_FIELDS,
               "chat_messages": CHAT_KEEP_FIELDS,
               "calendar_events": CALENDAR_KEEP_FIELDS}

#: EODHARD3 MUST 2: the most one staged input may hold, in decoded bytes.
#: The bytes cross in the caller's `args_file`, which no command line
#: carries, and are never walked by the argument fence, so the limit left is
#: the door's own 150 s budget. Measured through the door on the build PC
#: (the lane record's table): 2,000 raw Sent messages (8.9 MB) staged and
#: reconciled in under one second, 7,000 (31.3 MB, just under this ceiling)
#: in under two, so a slower mount keeps far inside the budget. Over it the
#: stage lands nothing and answers the line below.
STAGE_MAX_BYTES = 32 * 1024 * 1024

#: The one sentence a stage over `STAGE_MAX_BYTES` answers (and the fire
#: posts). No path, no number, no mechanism.
STAGE_TOO_LARGE_LINE = ("End of Day stopped because today's mail, chat or "
                        "calendar was too large to read in one pass.")


#: EODHARD3 fix round 1 (review N-1): the one sentence a stage that carries
#: no batch answers (and the fire posts): no bytes at all, or a raw input
#: with no statement of how many items the connector returned, or a count
#: that is not the list's. A read that never happened must never pass as a
#: day that was read and found empty (MAILSEAM item 8).
STAGE_UNREAD_LINE = ("End of Day stopped because today's mail, chat or "
                     "calendar did not reach it, so nothing was checked.")

#: EODHARD3 fix round 1 (review N-2): per raw input, the field a row must
#: keep after the cut for the batch to count as read.
IDENTITY_FIELDS = {"sent_messages": "message_id",
                   "chat_messages": "message_id",
                   "calendar_events": "start"}


#: WAVE 2 MAINTHARD2 MUST 1 (D-W2-3, EODHARD3 N-8): the fires that stage,
#: each in its OWN folder, so two fires never share one folder or one set of
#: names. A closed set: `fire` is looked up here and never used as a path, so
#: no caller can name a folder. One staging primitive, three folders.
STAGE_DIRS = {"end-of-day": PACK_DIR_REL,
              "maintenance": "_hq/.system/maintenance-fire",
              "morning-brief": "_hq/.system/morning-brief-fire"}

#: The kinds each fire stages, and the only kinds `stage_fire_input` lands
#: in that fire's folder: the End of Day every kind; the maintenance fire
#: its two reconcile batches; the Morning Brief its chat.
FIRE_KINDS = {"end-of-day": STAGED_INPUTS,
              "maintenance": ("sent_messages", "chat_messages"),
              "morning-brief": ("chat_messages",)}

#: The maintenance fire's own words for a stage it refused (MAINTHARD2
#: MUST 2): the job is left due for the next run, never retried by hand.
MAINT_STAGE_TOO_LARGE_LINE = (
    "The background check of sent mail and chat was left for the next run "
    "because the messages were more than one pass can read.")
MAINT_STAGE_UNREAD_LINE = (
    "The background check of sent mail and chat was left for the next run "
    "because the messages did not arrive, so none were checked.")

#: The Morning Brief's own words for a chat stage it refused (MAINTHARD2
#: MUST 3).
BRIEF_STAGE_TOO_LARGE_LINE = (
    "The morning brief left chat out "
    "because the messages were more than one pass can read.")
BRIEF_STAGE_UNREAD_LINE = (
    "The morning brief left chat out "
    "because the messages did not arrive, so none were checked.")

#: Per fire, the one sentence each refusal answers: the fire's own words.
STAGE_LINES = {
    "end-of-day": {"too_large": STAGE_TOO_LARGE_LINE,
                   "unread": STAGE_UNREAD_LINE},
    "maintenance": {"too_large": MAINT_STAGE_TOO_LARGE_LINE,
                    "unread": MAINT_STAGE_UNREAD_LINE},
    "morning-brief": {"too_large": BRIEF_STAGE_TOO_LARGE_LINE,
                      "unread": BRIEF_STAGE_UNREAD_LINE},
}


def _stage_dir(fire: Any) -> str:
    """The staging folder of `fire`, from the closed set. Anything else (an
    empty string, a path, a value that is not a string) raises before a
    single file is touched."""
    if not isinstance(fire, str) or fire not in STAGE_DIRS:
        raise ValueError("fire is not one of the fires that stage: "
                         + ", ".join(STAGE_DIRS))
    return STAGE_DIRS[fire]


def _unread(reason: str, fire: str = "end-of-day") -> Dict[str, Any]:
    return {"ok": False, "error": reason,
            "line": STAGE_LINES[fire]["unread"]}


def _inside_workspace(workspace_root: str, path: Path) -> bool:
    """Review N-7: the staging folder, resolved, sits under the resolved
    workspace (a link planted out of it is never followed)."""
    try:
        path.resolve().relative_to(Path(workspace_root).resolve())
    except (OSError, ValueError):
        return False
    return True


def _own_folder(workspace_root: str, fire: str) -> bool:
    """Fix round 1 (REVIEW_W2_MAINTHARD2 N-3): `fire`'s staging folder is its
    own real folder: resolved, it is exactly the workspace's resolved root
    joined with `STAGE_DIRS[fire]`. A folder that is a link or a junction
    anywhere on its path, out of the workspace (review N-7) or INTO another
    fire's folder, resolves elsewhere and is never followed."""
    rel = _stage_dir(fire)
    try:
        root = Path(workspace_root).resolve()
        return (Path(workspace_root) / rel).resolve() == root.joinpath(
            *rel.split("/"))
    except (OSError, ValueError):
        return False


def trim_to_fields(rows: Optional[List[Any]],
                   fields: tuple) -> List[Dict[str, Any]]:
    """EODHARD3 MUST 1: each object cut to `fields`, in code. A field the
    object does not carry stays absent (never invented); an element that is
    not an object is dropped. Writes nothing."""
    return [{k: row[k] for k in fields if k in row}
            for row in (rows or []) if isinstance(row, dict)]


def stage_fire_input(workspace_root: str, *, kind: str = "",
                     content_base64: str = "",
                     n_returned: Optional[int] = None,
                     fire: str = "end-of-day") -> Dict[str, Any]:
    """Land one staged input of THIS fire: a WRITER, on the write list only.

    `content_base64` is the JSON list the fire fetched, as base64 bytes;
    `kind` is one of the fire's `FIRE_KINDS`. The list lands at
    `STAGE_DIRS[fire]/<kind>.json` (overwriting the previous fire's) and
    the answer is `{rel, n}`; the read that needs it takes `rel`.

    MAINTHARD2 MUST 1 (D-W2-3): `fire` names WHICH fire stages, from the
    closed set `STAGE_DIRS` (the End of Day by default, byte for byte as
    before); any other value raises before anything is touched. A refusal
    answers that fire's own sentence (`STAGE_LINES`).

    Fix round 1 (review N-1): no bytes lands nothing and answers the unread
    sentence; for a raw input (`RAW_INPUTS`) the caller also states
    `n_returned`, how many items the connector returned, and a list of any
    other length answers the same. A true empty day is `[]` with
    `n_returned: 0`, and lands."""
    import base64 as _b64
    import json as _json

    from atomic_write import atomic_write_text

    folder = _stage_dir(fire)
    kinds = FIRE_KINDS[fire]
    if kind not in kinds:
        raise ValueError("kind is not one of the inputs a fire stages: "
                         + ", ".join(kinds))
    lines = STAGE_LINES[fire]
    text = str(content_base64 or "")
    # review N-9: the ceiling is asked of the length before anything decodes
    early = len(text) // 4 * 3 - text[-2:].count("=")
    if early > STAGE_MAX_BYTES:
        return {"ok": False, "error": "too_large", "line": lines["too_large"],
                "bytes": early, "limit": STAGE_MAX_BYTES}
    raw = _b64.b64decode(text, validate=True)  # review N-1: no silent drops
    if len(raw) > STAGE_MAX_BYTES:  # EODHARD3 MUST 2: the one real limit
        return {"ok": False, "error": "too_large", "line": lines["too_large"],
                "bytes": len(raw), "limit": STAGE_MAX_BYTES}
    if not raw:  # review N-1: no batch is not an empty batch
        return _unread("no_content", fire)
    counted = kind in RAW_INPUTS or n_returned is not None
    if counted and (not isinstance(n_returned, int)
                    or isinstance(n_returned, bool) or n_returned < 0):
        return _unread("no_count", fire)
    loaded = _json.loads(raw.decode("utf-8"))
    if not isinstance(loaded, list):
        raise ValueError("a staged input is a JSON list")
    if counted and len(loaded) != n_returned:
        return _unread("count_mismatch", fire)
    rel = f"{folder}/{kind}.json"
    target = Path(workspace_root) / rel
    if (not _inside_workspace(workspace_root, target.parent)  # review N-7
            or not _own_folder(workspace_root, fire)):  # W2 review N-3
        raise ValueError("the staging folder is not this fire's own folder "
                         "inside the workspace")
    atomic_write_text(target, _json.dumps(loaded, ensure_ascii=False))
    return {"ok": True, "rel": rel, "n": len(loaded), "bytes": len(raw)}


def _read_staged(workspace_root: str, rel: str, name: str,
                 fire: str = "end-of-day") -> List[Any]:
    """A list `stage_fire_input` landed under `name` in `fire`'s folder,
    read beside the data. Only that one file is read (another fire's file of
    the same name included); anything else raises."""
    import json as _json

    folder = _stage_dir(fire)
    root = Path(workspace_root).resolve()
    target = (root / str(rel)).resolve()
    if (target != (root / folder / f"{name}.json").resolve()
            or not _own_folder(workspace_root, fire)):  # W2 review N-3
        raise ValueError(f"not the staged {name} file")
    loaded = _json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        raise ValueError("the staged input is not a list")
    return loaded


def _read_raw(workspace_root: str, rel: str, kind: str,
              fire: str = "end-of-day") -> List[Dict[str, Any]]:
    """EODHARD3 MUST 1: a raw input `stage_fire_input` landed under `kind`
    in `fire`'s folder, read beside the data and cut to `KEEP_FIELDS[kind]`
    in code.

    Fix round 1 (review N-2): a batch that keeps nothing is never read as a
    day. A non-empty staged list with an element that is not an object, or
    in which no row keeps `IDENTITY_FIELDS[kind]` after the cut, raises.
    The test is that ANY row keeps the identity field, not every row: one
    odd message must not stop the fire (EODHARD3 review F-2)."""
    staged = _read_staged(workspace_root, rel, kind, fire)
    kept = trim_to_fields(staged, KEEP_FIELDS[kind])
    if staged and (len(kept) != len(staged) or not any(
            IDENTITY_FIELDS[kind] in row for row in kept)):
        raise ValueError(f"the staged {kind} keep no "
                         f"{IDENTITY_FIELDS[kind]} after the cut")
    return kept


def reconcile_sent_staged(workspace_root: str, *,
                          sent_rel: Optional[str] = None,
                          user_person_id: Optional[str] = None,
                          source_skill: str = "past-meetings",
                          fired_via: Optional[str] = None,
                          provider: Optional[str] = None,
                          exclude_captured_since: Optional[str] = None,
                          fetch_blocked: Optional[str] = None,
                          fire: str = "end-of-day"
                          ) -> Dict[str, Any]:
    """Phase A's mail leg, by path: a WRITER, on the write list only
    (EODHARD3 MUST 1 and 2).

    The Sent batch was staged raw (`stage_fire_input`, kind `sent_messages`);
    this reads it beside the data, keeps `SENT_KEEP_FIELDS` of each message
    in code, and runs `reconcile_sent_commitments.reconcile_and_receipt` ONCE
    over the whole batch, a first run's 30 days included. So the fire writes
    the one `sent_reconcile` audit row and moves the cursor once, exactly as
    a small batch does. No `sent_rel` (a blocked fetch) is the empty batch.
    Answers the reconcile writer's own receipt, unchanged.

    MAINTHARD2 MUST 1: `fire` names the folder the batch was staged in (the
    maintenance fire passes `maintenance` and `source_skill`
    `reconcile-sent`); a value outside `STAGE_DIRS` raises first."""
    from reconcile_sent_commitments import reconcile_and_receipt

    _stage_dir(fire)
    messages = (_read_raw(workspace_root, sent_rel, "sent_messages", fire)
                if sent_rel else [])
    return _jsonable(reconcile_and_receipt(
        workspace_root, messages, user_person_id=user_person_id,
        source_skill=source_skill, fired_via=fired_via, provider=provider,
        exclude_captured_since=exclude_captured_since,
        fetch_blocked=fetch_blocked))


def reconcile_chat_staged(workspace_root: str, *,
                          chat_rel: Optional[str] = None,
                          user_person_id: Optional[str] = None,
                          source_skill: str = "past-meetings",
                          fired_via: Optional[str] = None,
                          exclude_captured_since: Optional[str] = None,
                          fetch_blocked: Optional[str] = None,
                          provider: Optional[str] = None,
                          scan_plan: Optional[Dict[str, Any]] = None,
                          user_chat_ids: Optional[List[str]] = None,
                          user_names: Optional[List[str]] = None,
                          fire: str = "end-of-day"
                          ) -> Dict[str, Any]:
    """Phase A's chat leg, by path: a WRITER, on the write list only
    (EODHARD3 MUST 1 and 2). The chat batch staged raw (kind
    `chat_messages`), read beside the data, cut to `CHAT_KEEP_FIELDS` in
    code, and handed to `chat_reconcile.reconcile_chat_and_receipt` ONCE:
    one `chat_reconcile` audit row per fire. Answers that writer's receipt,
    unchanged.

    MAINTHARD2 MUST 1 and 2: `fire` names the folder the batch was staged
    in (a value outside `STAGE_DIRS` raises first), and the chat plan's
    `provider` and `scan_plan` are handed on by name (the maintenance fire's
    leg carries both, as its inline form did; None is the writer's own
    default, so the End of Day's call is unchanged).

    Fix round 1 (REVIEW_W2_MAINTHARD2 N-2 (a)): `user_chat_ids` and
    `user_names`, the user's own ids and names on the chat backend, are
    handed on by name too, as the inline writer took them; without them the
    leg can never tell the user's own messages from anyone else's. None is
    the writer's own default (an empty tuple)."""
    from chat_reconcile import reconcile_chat_and_receipt

    _stage_dir(fire)
    messages = (_read_raw(workspace_root, chat_rel, "chat_messages", fire)
                if chat_rel else [])
    return _jsonable(reconcile_chat_and_receipt(
        workspace_root, messages, user_person_id=user_person_id,
        provider=provider, scan_plan=scan_plan,
        user_chat_ids=tuple(user_chat_ids or ()),
        user_names=tuple(user_names or ()),
        source_skill=source_skill, fired_via=fired_via,
        exclude_captured_since=exclude_captured_since,
        fetch_blocked=fetch_blocked))


def _staged_names(fire: str = "end-of-day") -> frozenset:
    return frozenset(f"{kind}.json" for kind in FIRE_KINDS[fire])


def _clear_staging(workspace_root: str,
                   fire: str = "end-of-day") -> Dict[str, Any]:
    """Remove every file `fire`'s writers stage under `STAGE_DIRS[fire]`: one
    file per kind that fire stages (`FIRE_KINDS`) and, for the End of Day
    only, the day-close pack copies. Nothing else in the folder is touched,
    and no other fire's folder is entered (MAINTHARD2 MUST 1, D-W2-3). A
    mount that refuses the delete gets the file emptied instead (the
    transcript text is gone either way) and says so; a file that can be
    neither is named in `left`."""
    import os

    from atomic_write import atomic_write_text

    folder = Path(workspace_root) / _stage_dir(fire)
    out: Dict[str, List[str]] = {"removed": [], "emptied": [], "left": []}
    if (not folder.is_dir() or not _inside_workspace(workspace_root, folder)
            or not _own_folder(workspace_root, fire)):
        # review N-7: a link out of the workspace is never followed; W2
        # review N-3: nor a link into another fire's folder
        return out
    names = _staged_names(fire)
    packs = fire == "end-of-day"
    for path in sorted(folder.iterdir()):
        ours = path.name in names or (packs
                                      and path.name.startswith(PACK_FILE_PREFIX)
                                      and path.suffix == ".json")
        if not ours or not path.is_file():
            continue
        try:
            os.unlink(str(path))
            out["removed"].append(path.name)
            continue
        except OSError:
            pass
        try:
            atomic_write_text(path, "")
            out["emptied"].append(path.name)
        except OSError:
            out["left"].append(path.name)
    return out


def clear_fire_staging(workspace_root: str,
                       fire: str = "end-of-day") -> Dict[str, Any]:
    """EODHARD3 MUST 3: the fire's staged files, removed: a WRITER, on the
    write list only. The fire runs it at the start of Phase A, before it
    stages anything (so a fire that stopped early is cleaned by the next),
    and again once its receipt has landed, so a day's transcripts and raw
    mail never rest in the synced folder between fires. Answers
    `{removed, emptied, left}` by file name.

    MAINTHARD2 MUST 1: `fire` clears only that fire's own folder; the End of
    Day's (the default) is untouched by a maintenance clear, and the other
    way round."""
    return _clear_staging(workspace_root, fire)


def run_cru_pass(workspace_root: str, *, source_ref: str = "",
                 meeting_ts: str = "", fire_start: str = "",
                 attendee_person_ids: Optional[List[str]] = None,
                 transcript_text: str = "",
                 review_budget: Optional[Dict[str, Any]] = None
                 ) -> Dict[str, Any]:
    """Phase 4.6 in ONE call: a WRITER, on the write list only (R-2).

    The walk (`cru_walk`'s body: the walk ledger, then the matcher over the
    open set) and the write (`apply_cru_pass`: the pass's writers, then the
    walk record) run in the same process beside the data, so the matcher's
    results, 128 to 166 rows on a real day, never cross the door. The
    transcript travels in the caller's `args_file` (one string, never
    walked item by item, and never on a command line). `skip: true` is the
    walk ledger honoring the transcript; nothing is written then. The
    stale count is THIS transcript's own EVORDER refusals."""
    walked = cru_walk(workspace_root, source_ref=source_ref,
                      meeting_ts=meeting_ts, fire_start=fire_start,
                      attendee_person_ids=attendee_person_ids,
                      transcript_text=transcript_text)
    if walked.get("error"):
        raise RuntimeError(walked["error"])
    if walked.get("skip"):
        return {"skip": True, "walked": walked.get("walked")}
    stale = int((walked.get("diagnostics") or {})
                .get("stale_evidence_dropped", 0) or 0)
    out = apply_cru_pass(workspace_root, results=walked.get("results"),
                         meeting_ref=source_ref, transcript_ts=meeting_ts,
                         already_proposed=walked.get("already_proposed"),
                         batch_id=walked.get("batch_id"), n_stale=stale,
                         review_budget=review_budget)
    out = dict(out)
    out.update({"skip": False, "n_results": len(walked.get("results") or []),
                "stale_evidence_dropped": stale})
    return out


class DoorStepRefused(Exception):
    """The class name the `surface_failed` receipt records when a fire
    stopped because a door answer was `ok: false` mid-chain."""


def record_fire_stopped(workspace_root: str, *, step: str = "",
                        mode: str = "scheduled") -> Dict[str, Any]:
    """The fire stopped mid-chain: a WRITER, on the write list only.

    Writes the End of Day's `surface_failed` receipt (the same one a pack
    that could not build writes, `surface_drivers.log_surface_failed`) so a
    partial day is never silent, and answers the one sentence to post,
    `surface_drivers.SURFACE_FAILED_LINES`'s End of Day line. `step` names
    the form that was refused, for the chat's own reading only."""
    import surface_drivers as sd

    receipt = sd.log_surface_failed(workspace_root, sd.SURFACE_FAILED_EOD,
                                    DoorStepRefused(str(step or "")),
                                    mode=mode)
    # EODHARD3 MUST 3: a stopped fire leaves no staged transcript behind
    cleared = _clear_staging(workspace_root)
    return {"line": sd.SURFACE_FAILED_LINES[sd.SURFACE_FAILED_EOD],
            "receipt": bool(receipt), "step": str(step or ""),
            "cleared": cleared}


@answers
def render_for_fire(workspace_root: str, view: Dict[str, Any], *,
                    surface: str = SURFACE) -> Dict[str, Any]:
    """The day-close's shape — `brief_settings.render_for_fire(ws, view,
    surface="end-of-day")`: the SAME ten settings the brief reads, settings
    first and the free-text notes beneath them."""
    from brief_settings import render_for_fire as _render_for_fire

    return _jsonable(_render_for_fire(workspace_root, dict(view or {}),
                                      surface=surface))
