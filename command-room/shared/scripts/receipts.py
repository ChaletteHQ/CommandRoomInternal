#!/usr/bin/env python3
"""
THE receipt contract — one schema, one writer, one reader (v4.5.2 R1).

WHY
---

The 2026-07-07/08 dogfood (FINDINGS_M_v451) proved receipt anarchy is a
single root cause wearing five findings:

  F-10b  — health check flagged Inbox "didn't record" because its matcher
           keyed on `data.task_id` while inbox's receipt carried only
           `data.kind`.
  F-43   — the matcher half: same disease, one day later.
  F-47 P2a — ONE skill, TWO receipt shapes in ONE day: the morning scheduled
           fire wrote `{task_id: 'cr-commitments', fired_at, outcome, ...}`;
           the afternoon manual fire wrote `{kind, date, status, late_tier,
           surfaced, header_counts}`.
  F-49   — usage-report undercounted 4 of 7 task rows and missed two task
           families entirely (reconcile-sent ×6, session-sweep ×4) because
           it read only `pack_run` events and only canonical id spellings.
  F-50 P2c — the SAME skill wrote `late_tier` in the morning and
           `lateness_tier` in the evening — the field name was never
           specified anywhere, so each fire improvised it.

The drift source: the prose orchestrators hand-rolled their receipt JSON
(each file specced a different field set) instead of calling a helper. This
module is the consolidation: every writer calls `log_receipt()` (or the
back-compat `log_pack_run` wrapper that now delegates here); every reader
goes through `iter_receipts()` / `last_receipt_times()` / `count_runs()`.

CONTRACT DECISIONS (settled here, once)
---------------------------------------

- **Canonical task_id spelling** is the hyphenated registry id (the
  DEFAULT_SCHEDULES key): `commitments`, `past-meetings`, `upcoming-meetings`,
  ... — never `cr-` prefixed, never underscored. `normalize_task_id()` maps
  every spelling observed in live substrates to the canonical form.
- **The lateness field is `late_tier`.** `lateness_tier` (and bare `tier`
  on pack_runs) are legacy spellings — parsed forever, never written again.
- **`fired_via` is one of `scheduled | manual | catchup`** (R2 wires the
  detection; the field ships in the schema now). Legacy values
  (`user-trigger`, `scheduled_late_refire`) are normalized read-side.
- **Receipts carry `machine`** (hostname) — F-38: schedules are per-machine
  and readers couldn't tell two machines from a double-registration bug.
- **Readers parse ALL legacy shapes FOREVER.** events.jsonl is append-only
  history; back-compat lives read-side, never as a history rewrite.

CANONICAL RECEIPT SHAPE (what log_receipt writes)
-------------------------------------------------

    {"type": "<receipt type>", "source_skill": "<canonical task_id>",
     "ts": "<auto-stamped UTC inside the writer lock>",
     "data": {"task_id": "<canonical>", "kind": "<canonical>",
              "status": "complete", "fired_via": "scheduled|manual|catchup",
              "surfaced": <int?>, "duration_ms": <int?>,
              "late_tier": "<note|degrade>"?, "machine": "<hostname>",
              ...task-specific counts...}}

`kind` duplicates `task_id` deliberately: legacy readers key on `kind`,
and dropping it would orphan them. New readers use `task_id`.

USAGE
-----

Writer (one line from any orchestrator's final phase):

    from receipts import log_receipt
    log_receipt(WORKSPACE, "past-meetings", fired_via="scheduled",
                surfaced=3, duration_ms=elapsed_ms,
                extra_data={"meetings_processed": 3})

Reader:

    from receipts import count_runs, last_receipt_times
    counts = count_runs(WORKSPACE, since=window_start)   # usage-report
    latest = last_receipt_times(WORKSPACE)               # watchdog freshness
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from event_time import event_dt, parse_ts  # noqa: E402


# ---------------------------------------------------------------------------
# Canonical vocabulary
# ---------------------------------------------------------------------------

# The scheduled-task registry ids (DEFAULT_SCHEDULES keys) plus the on-demand
# scanners that R1 gives receipts. Hyphenated, bare (no cr- prefix) — final.
CANONICAL_TASK_IDS = frozenset({
    "morning-brief",
    "upcoming-meetings",
    "inbox",
    "commitments",   # CTS1 — retired taskId; kept forever so pre-split receipts stay readable (append-only history)
    "waiting-on",    # CTS1 Surface 1 — the re-scoped daily chat (successor of `commitments`; new receipts land here)
    "my-plate",      # CTS1 Surface 2 — the owner-me act-list chat
    # LIFECYCLE1 — the Pulse CHAT is retired (M's ruling 2026-08-02) and its
    # taskId is out of DEFAULT_SCHEDULES, but the id stays here FOREVER: three
    # receipt shapes sit on disk under it (see RECEIPT_TYPES below) and history
    # is append-only. `pulse` parses forever; it is simply never written again.
    "pulse",
    "lifecycle",     # LIFECYCLE1 — the project lifecycle pass job inside `maintenance` (the fold that replaced Pulse's Phase 4)
    "past-meetings",
    "friday-wrap",
    "cleanup",
    "reconcile-sent",
    "monthly-report",
    "weekly-insights",
    "session-sweep",
    "relationship-moves",
    "commitment-triage",
    "dormant-scan",
    "stalled-projects",
    "pipeline-tracker",  # RCPT1 — the deal-pipeline report's SKILL.md-mandated scan receipt (on-demand, like stalled-projects)
    "objectives",    # OBJ1 (draft) — the on-demand objectives readout (same shape as stalled-projects: what was surfaced + drifting_thread_ids, so drift-flag value counts read from receipts)
    "maintenance",   # MAINT1 — the single silent dispatcher task (the five silent ids above live on as its JOBS and keep their receipt vocabularies forever)
    "staff-meeting",  # LB1 R3 — the weekly Staff Meeting chat (opt-in later-add)
    "balance",        # BAL1 — the Sunday personal white-space chat (opt-in later-add, m_facing only; receipt carries counts, never personal content)
    "pipeline-digest",  # PIPE1 Part 2 — the Tuesday deal-review chat (opt-in later-add, gated >=1 open deal); its receipt keys the next digest's since-window
    "deal-signals",   # LB1 D7 — the deal-signal detector job inside `maintenance`
    "identity-reconcile",  # PID1 D7 — the Sunday identity reconciler job inside `maintenance` (also the M-fired one-time backfill)
    "monthly-scorecard",  # SPEC OUT7 — the OPT-IN monthly KPI scorecard job inside `maintenance` (never auto-fires; its pack_run receipt self-limits it to monthly once opted in)
})

# Renames where the canonical form is NOT just a cr-strip + underscore fix.
# Sources: source_skill_compat (cr-dont-forget → pulse), live-substrate
# spellings observed in the v4.5.1 dogfood, and skill-name/task-id splits.
_TASK_ALIASES = {
    "dont-forget": "pulse",
    "dont-forget-run": "pulse",
    "inbox-pulse": "pulse",           # legacy cr-inbox-pulse was the pulse forerunner
    "inbox-triage": "inbox",          # customer-facing skill name for the inbox task
    "morning-briefing": "morning-brief",
    "weekly-recap": "friday-wrap",    # the skill the friday-wrap task wraps
    "dormant-customer-scan": "dormant-scan",
    "sent-reconcile": "reconcile-sent",
    "session-sweep-run": "session-sweep",
}

# CTS1 — split-task predecessors: receipts of a RETIRED task also vouch for
# its successors' served slots (late_fire reads this). The morning after the
# commitments → waiting-on + my-plate split, the 8:30 slot WAS served — by a
# receipt written under the old id; without this bridge the first post-split
# fire would fabricate lateness for a slot that ran. Successor ids stay their
# own canonical ids (new receipts land under them); this map is read-side
# only and never rewrites history.
TASK_PREDECESSORS: dict[str, tuple] = {
    "waiting-on": ("commitments",),
    "my-plate": ("commitments",),
}

# The one lateness field name written from v4.5.2 on. Legacy spellings are
# read forever via get_late_tier() — never written again.
LATENESS_FIELD = "late_tier"
_LEGACY_LATENESS_FIELDS = ("late_tier", "lateness_tier", "tier")

FIRED_VIA = frozenset({"scheduled", "manual", "catchup"})

# Legacy / improvised fired_via values observed in live data → canonical.
_FIRED_VIA_ALIASES = {
    "user-trigger": "manual",         # log_pack_run's pre-R1 vocabulary
    "user_trigger": "manual",
    "run-now": "manual",
    "run_now": "manual",
    "scheduled_late_refire": "catchup",  # F-51's phantom re-fire spelling
    "scheduled-late-refire": "catchup",
    "catch-up": "catchup",
    "catch_up": "catchup",
}

# Receipt event types per task. `types` = every event type that counts as
# "this task fired" (the watchdog freshness signal). `count_types`, when
# present, narrows which types are RUN-COUNTED — monthly-report's fire also
# emits value_receipt_generated (2-3 per fire: month + quarter + the F-36
# dupes), so counting those as runs would fabricate fires.
RECEIPT_TYPES: dict[str, dict] = {
    "morning-brief":      {"types": frozenset({"pack_run"})},
    "upcoming-meetings":  {"types": frozenset({"pack_run"})},
    "inbox":              {"types": frozenset({"pack_run"})},
    "commitments":        {"types": frozenset({"pack_run"})},
    # CTS1 — the two split surfaces. `commitments` keeps its row above so
    # pre-split receipts read forever; the waiting-on window computation
    # takes max(last_receipt_times(ws, ["waiting-on", "commitments"])) so
    # the first post-split fire doesn't re-scan a week of mail.
    "waiting-on":         {"types": frozenset({"pack_run"})},
    "my-plate":           {"types": frozenset({"pack_run"})},
    # Pulse fires have left three shapes on disk: pack_run, pulse_run, and
    # dont_forget_run (F-49's exact miss). All three are pulse receipts.
    "pulse":              {"types": frozenset({"pack_run", "pulse_run", "dont_forget_run"})},
    # LIFECYCLE1 — the fold that replaced Pulse's Phase 4. Its own job receipt
    # (the dispatcher's due-ness rule reads it; written by
    # lifecycle_pass.run_lifecycle_pass, and ONLY on an --apply run).
    "lifecycle":          {"types": frozenset({"lifecycle_run"})},
    "past-meetings":      {"types": frozenset({"pack_run"})},
    "friday-wrap":        {"types": frozenset({"pack_run"})},
    "relationship-moves": {"types": frozenset({"pack_run"})},
    "commitment-triage":  {"types": frozenset({"pack_run"})},
    "cleanup":            {"types": frozenset({"cleanup_run", "audit_run"})},
    "reconcile-sent":     {"types": frozenset({"sent_reconcile"})},
    "monthly-report":     {"types": frozenset({"operator_report_generated", "value_receipt_generated"}),
                           "count_types": frozenset({"operator_report_generated"})},
    # weekly-insights writes a pack_run receipt from v4.5.2 (it was the one
    # task with NO substrate receipt — the watchdog fell back to view-file
    # mtimes, which can't be run-counted).
    "weekly-insights":    {"types": frozenset({"pack_run"})},
    "session-sweep":      {"types": frozenset({"session_sweep_run"})},
    "dormant-scan":       {"types": frozenset({"pack_run"})},
    # stalled-projects scan receipt (v4.5.2 C3 — closes the F-57-noted gap
    # for the project-side scan: what was surfaced, so the next scan can
    # dedup its own nags and value receipts can count the work).
    "stalled-projects":   {"types": frozenset({"pack_run"})},
    # RCPT1 — pipeline-tracker's report-fire receipt. Its SKILL.md has
    # mandated this log_receipt since PIPE1 Part 1, but the id was never
    # registered here, so every mandated call raised ValueError at runtime
    # (stalled-projects got registered in v4.5.2 C3; pipeline didn't).
    "pipeline-tracker":   {"types": frozenset({"pack_run"})},
    # objectives readout receipt (SPEC OBJ1, DRAFT — same C3 shape): what was
    # surfaced + data.drifting_thread_ids, so the next fire dedups its nags
    # and the monthly value receipt can count drift flags from receipts.
    "objectives":         {"types": frozenset({"pack_run"})},
    # MAINT1 — the dispatcher task's own per-fire audit event. The five job
    # ids above (cleanup / reconcile-sent / monthly-report / weekly-insights /
    # session-sweep) keep their own receipt types: those are the JOB success
    # signals the dispatcher's due-ness rule reads; maintenance_run only says
    # the dispatcher itself fired and what was due/completed/failed.
    "maintenance":        {"types": frozenset({"maintenance_run"})},
    # LB1 R3 — the Staff Meeting chat's per-fire receipt (scheduled or the
    # on-demand `staff meeting` fire, fired_via distinguishes).
    "staff-meeting":      {"types": frozenset({"pack_run"})},
    # BAL1 — the Sunday Balance chat's per-fire receipt (scheduled or the
    # on-demand `balance check` fire). The receipt is the standard pack_run
    # shape and carries NO personal content — surfaced count only.
    "balance":            {"types": frozenset({"pack_run"})},
    # PIPE1 Part 2 — the Tuesday Pipeline Digest chat's per-fire receipt
    # (standard pack_run; ALSO the marker the next digest's since-last-digest
    # window keys on, so it writes even on a degrade fire with surfaced=0).
    "pipeline-digest":    {"types": frozenset({"pack_run"})},
    # LB1 D7 — the deal-signal detector's job receipt (the dispatcher's
    # due-ness rule reads it; written by deal_signal_detector.run_deal_signal_job).
    "deal-signals":       {"types": frozenset({"pack_run"})},
    # PID1 D7 — the identity reconciler's per-run receipt: ALSO the D6
    # CHANGED-narration source (change_feed reads n_auto_added / n_linked)
    # and the honesty artifact (counts from what was WRITTEN, never the
    # plan). The dispatcher's due-ness rule reads it, so an M-fired backfill
    # also serves that week's Sunday slot.
    "identity-reconcile": {"types": frozenset({"identity_reconcile_run"})},
    # SPEC OUT7 — the opt-in monthly KPI scorecard job's receipt. pack_run, the
    # standard scheduled-pack shape (like deal-signals / staff-meeting): the
    # dispatcher's due-ness rule reads it so a fired scorecard self-limits to
    # monthly. Only ever written when the workspace opted the job in.
    "monthly-scorecard":  {"types": frozenset({"pack_run"})},
}

# Types that identify their task by TYPE alone (exactly one writer each).
_TYPE_IMPLIES_TASK = {
    "sent_reconcile": "reconcile-sent",
    "session_sweep_run": "session-sweep",
    "cleanup_run": "cleanup",
    "audit_run": "cleanup",
    "operator_report_generated": "monthly-report",
    "value_receipt_generated": "monthly-report",
    "pulse_run": "pulse",
    "dont_forget_run": "pulse",
    "maintenance_run": "maintenance",
    "identity_reconcile_run": "identity-reconcile",
}

ALL_RECEIPT_TYPES = frozenset().union(*(spec["types"] for spec in RECEIPT_TYPES.values()))

# Two receipts of DIFFERENT types this close together are one fire (a task
# emitting its primary receipt plus a secondary one, e.g. monthly-report's
# operator report + value receipts ~20s apart). Same-type receipts NEVER
# merge — two pack_runs 4 minutes apart are two real runs (M's back-to-back
# manual sweeps, F-08).
RUN_DEDUP_WINDOW = _dt.timedelta(minutes=15)


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------

def normalize_task_id(value):
    """Canonicalize any task-id / kind / source_skill spelling observed in
    live substrates: lowercase, `cr-` prefix stripped, underscores → hyphens,
    known renames via the alias table.

    Non-strings pass through unchanged (defensive readers feed raw data).
    Unknown-but-clean ids (e.g. `list`, `historical-backfill`) return in
    normalized form without being forced into CANONICAL_TASK_IDS — the
    caller decides whether unknowns matter.
    """
    if not isinstance(value, str) or not value.strip():
        return value
    v = value.strip().lower().replace("_", "-")
    if v.startswith("cr-"):
        v = v[3:]
    return _TASK_ALIASES.get(v, v)


def normalize_fired_via(value) -> Optional[str]:
    """Canonicalize a fired_via value; None when absent/unrecognizable.
    Unknown strings return normalized-lowercase (never dropped — the raw
    value is still evidence)."""
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip().lower()
    return _FIRED_VIA_ALIASES.get(v, v)


def get_late_tier(data) -> Optional[str]:
    """The receipt's lateness tier under any legacy spelling
    (`late_tier` → `lateness_tier` → `tier`, first present wins)."""
    if not isinstance(data, dict):
        return None
    for field in _LEGACY_LATENESS_FIELDS:
        v = data.get(field)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def receipt_task_id(ev) -> Optional[str]:
    """The canonical task a receipt event belongs to, or None when the event
    is not a receipt (wrong type) or names no known task.

    Matching order:
      1. event type implies the task (sent_reconcile → reconcile-sent, ...)
      2. identity fields, first canonical match wins:
         data.task_id → data.taskId → data.kind → source_skill →
         data.source_skill — each run through normalize_task_id, so
         `cr-commitments`, `past_meetings`, `upcoming_meetings`,
         `dont_forget`, and `morning-briefing` all land correctly.
    """
    if not isinstance(ev, dict):
        return None
    etype = ev.get("type")
    if etype not in ALL_RECEIPT_TYPES:
        return None
    implied = _TYPE_IMPLIES_TASK.get(etype)
    if implied:
        return implied
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for candidate in (
        data.get("task_id"),
        data.get("taskId"),
        data.get("kind"),
        ev.get("source_skill"),
        data.get("source_skill"),
    ):
        norm = normalize_task_id(candidate)
        if isinstance(norm, str) and norm in CANONICAL_TASK_IDS:
            return norm
    return None


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def _machine_name() -> Optional[str]:
    try:
        import platform
        name = platform.node()
        return name[:64] if name else None
    except Exception:
        return None


def log_receipt(
    workspace_root,
    task_id: str,
    *,
    receipt_type: str = "pack_run",
    status: str = "complete",
    fired_via: str = "scheduled",
    surfaced: Optional[int] = None,
    duration_ms: Optional[int] = None,
    late_tier: Optional[str] = None,
    extra_data: Optional[dict] = None,
) -> dict:
    """THE receipt writer. Every scheduled/manual task fire ends with one
    call here (directly or via the log_pack_run back-compat wrapper).

    Validates the canonical vocabulary at write time (drift is a defect the
    moment it's written, not when a reader trips on it months later), routes
    through event_gate.append_event (enum check + locked writer + UTC
    auto-stamp), and returns the event dict as appended (minus seq/ts, which
    the writer lock stamps).
    """
    canonical = normalize_task_id(task_id)
    if canonical not in CANONICAL_TASK_IDS:
        raise ValueError(
            f"unknown task_id {task_id!r} (normalized {canonical!r}); "
            f"canonical ids: {sorted(CANONICAL_TASK_IDS)}"
        )
    if receipt_type not in ALL_RECEIPT_TYPES:
        raise ValueError(
            f"{receipt_type!r} is not a registered receipt type "
            f"({sorted(ALL_RECEIPT_TYPES)})"
        )
    if receipt_type not in RECEIPT_TYPES[canonical]["types"]:
        raise ValueError(
            f"receipt type {receipt_type!r} does not belong to task "
            f"{canonical!r} (expected one of "
            f"{sorted(RECEIPT_TYPES[canonical]['types'])})"
        )
    via = normalize_fired_via(fired_via)
    if via not in FIRED_VIA:
        raise ValueError(
            f"fired_via must be one of {sorted(FIRED_VIA)}; got {fired_via!r}"
        )
    if late_tier is not None and not isinstance(late_tier, str):
        raise ValueError(f"late_tier must be a string tier name; got {late_tier!r}")
    if surfaced is not None and (not isinstance(surfaced, int) or surfaced < 0):
        raise ValueError(f"surfaced must be a non-negative int; got {surfaced!r}")
    if duration_ms is not None and (not isinstance(duration_ms, int) or duration_ms < 0):
        raise ValueError(f"duration_ms must be a non-negative int; got {duration_ms!r}")

    data: dict = {
        "task_id": canonical,
        "kind": canonical,  # legacy readers key on kind; new readers use task_id
        "status": status,
        "fired_via": via,
    }
    if surfaced is not None:
        data["surfaced"] = surfaced
    if duration_ms is not None:
        data["duration_ms"] = duration_ms
    if late_tier is not None:
        data[LATENESS_FIELD] = late_tier
    machine = _machine_name()
    if machine:
        data["machine"] = machine
    if extra_data:
        # extra_data never overrides the contract fields — task-specific
        # counts ride along; identity/vocabulary stays canonical.
        for k, v in extra_data.items():
            if k not in data:
                data[k] = v

    event = {
        "type": receipt_type,
        "source_skill": canonical,
        "data": data,
    }
    from event_gate import append_event

    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    append_event(events_path, event, holder=f"receipt:{canonical}")
    return event


# ---------------------------------------------------------------------------
# Per-brief prep receipts (v4.5.2 S1 — F-29 / F-29b)
# ---------------------------------------------------------------------------
#
# A `prep_brief` event is written ONCE PER BRIEF (not per fire — the task's
# pack_run stays the per-fire receipt) and carries the MEETING ID. It is THE
# signal the morning brief's prep-detection reads: the "no prep" flag may
# only render for a meeting with NO prep_brief receipt (F-29's fix — the
# detector and the writer finally share one signal). It is deliberately NOT
# in RECEIPT_TYPES: five briefs in one upcoming-meetings fire are five
# prep_brief events and ONE run, so counting them as task runs would
# fabricate fires (the F-49 disease in reverse).

PREP_RECEIPT_TYPE = "prep_brief"


def log_prep_receipt(
    workspace_root,
    *,
    meeting_id: str,
    slug: str,
    brief_path: str,
    generated_by: str = "upcoming-meetings",
    fired_via: str = "scheduled",
    refreshed: bool = False,
    extra_data: Optional[dict] = None,
) -> dict:
    """THE per-brief receipt writer. Both prep paths (scheduled auto-prep and
    on-demand 'prep me') call this after a successful make_brief save.

    `meeting_id` is the calendar event id — the same identity `prep_slug`
    derives the filename from, so detector and file share one key.
    `refreshed` records that an existing brief was updated in place rather
    than a new one written (the F-29b contract made auditable).
    """
    if not isinstance(meeting_id, str) or not meeting_id.strip():
        raise ValueError("meeting_id is required (the calendar event id)")
    if not isinstance(slug, str) or not slug.strip():
        raise ValueError("slug is required")
    if not isinstance(brief_path, str) or not brief_path.strip():
        raise ValueError("brief_path is required")
    via = normalize_fired_via(fired_via)
    if via not in FIRED_VIA:
        raise ValueError(
            f"fired_via must be one of {sorted(FIRED_VIA)}; got {fired_via!r}"
        )
    generator = normalize_task_id(generated_by)

    from os.path import basename

    data: dict = {
        "meeting_id": meeting_id.strip(),
        "slug": slug.strip(),
        "artifact": basename(brief_path.strip().replace("\\", "/")),
        "generated_by": generator,
        "fired_via": via,
        "refreshed": bool(refreshed),
    }
    machine = _machine_name()
    if machine:
        data["machine"] = machine
    if extra_data:
        for k, v in extra_data.items():
            if k not in data:
                data[k] = v

    event = {
        "type": PREP_RECEIPT_TYPE,
        "source_skill": generator,
        "data": data,
    }
    from event_gate import append_event

    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    append_event(events_path, event, holder=f"prep_receipt:{data['slug']}")
    return event


def prep_receipts(
    workspace_root,
    *,
    meeting_ids: Optional[Iterable[str]] = None,
    since: Optional[_dt.datetime] = None,
) -> list[dict]:
    """Every prep_brief receipt, oldest first, optionally filtered by meeting
    id / time. Returns {meeting_id, slug, artifact, dt, fired_via, refreshed,
    raw}. Same defensive read path as iter_receipts — legacy/malformed lines
    never break the reader."""
    wanted = None
    if meeting_ids is not None:
        wanted = {str(m).strip() for m in meeting_ids if str(m).strip()}
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=_dt.timezone.utc)

    out: list[dict] = []
    for ev in _iter_events(workspace_root):
        if not isinstance(ev, dict) or ev.get("type") != PREP_RECEIPT_TYPE:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        mid = str(data.get("meeting_id") or "").strip()
        if not mid:
            continue
        if wanted is not None and mid not in wanted:
            continue
        dt = event_dt(ev)
        if since is not None:
            if dt is None or dt < since:
                continue
        out.append({
            "meeting_id": mid,
            "slug": data.get("slug"),
            "artifact": data.get("artifact"),
            "dt": dt,
            "fired_via": normalize_fired_via(data.get("fired_via")),
            "refreshed": bool(data.get("refreshed")),
            "raw": ev,
        })
    out.sort(key=lambda r: (r["dt"] is not None, r["dt"] or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)))
    return out


def prep_exists_for_meeting(workspace_root, meeting_id) -> bool:
    """F-29's detector rule, as one function: does ANY prep_brief receipt
    exist for this meeting id? The morning brief may render its "no prep"
    flag ONLY when this returns False — never from folder globs or slug
    guesses (the pre-v4.5.2 detector read a different signal than the writer
    left and claimed "no prep" while the file + receipt were both on disk)."""
    if not isinstance(meeting_id, str) or not meeting_id.strip():
        return False
    return bool(prep_receipts(workspace_root, meeting_ids=[meeting_id]))


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

def _iter_events(workspace_root) -> Iterable[dict]:
    try:
        import events_io

        yield from events_io.iter_events(workspace_root)
        return
    except Exception:
        pass
    # Defensive fallback — active file only, bad lines skipped.
    import json

    path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(ev, dict):
                    yield ev
    except OSError:
        return


def iter_receipts(
    workspace_root,
    *,
    task_ids: Optional[Iterable[str]] = None,
    since: Optional[_dt.datetime] = None,
    until: Optional[_dt.datetime] = None,
) -> list[dict]:
    """Every receipt in the substrate, normalized. One pass, shard-
    transparent (events_io), all legacy shapes parsed forever.

    Returns dicts, oldest first:
      {task_id (canonical), type, dt (aware datetime|None),
       fired_via (canonical|None), late_tier (coalesced|None),
       status, raw (the original event, untouched)}

    `since`/`until` are half-open [since, until); naive bounds are taken
    as UTC. Receipts with no parseable timestamp are INCLUDED when no time
    filter is set and excluded by any time filter (they can't be placed).
    """
    wanted = None
    if task_ids is not None:
        wanted = {normalize_task_id(t) for t in task_ids}
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=_dt.timezone.utc)
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=_dt.timezone.utc)

    out: list[dict] = []
    for ev in _iter_events(workspace_root):
        tid = receipt_task_id(ev)
        if tid is None:
            continue
        if wanted is not None and tid not in wanted:
            continue
        dt = event_dt(ev)
        if since is not None or until is not None:
            if dt is None:
                continue
            if since is not None and dt < since:
                continue
            if until is not None and dt >= until:
                continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        out.append({
            "task_id": tid,
            "type": ev.get("type"),
            "dt": dt,
            "fired_via": normalize_fired_via(data.get("fired_via")),
            "late_tier": get_late_tier(data),
            "status": data.get("status") or data.get("outcome"),
            "raw": ev,
        })
    out.sort(key=lambda r: (r["dt"] is not None, r["dt"] or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)))
    return out


def last_receipt_times(
    workspace_root,
    task_ids: Optional[Iterable[str]] = None,
) -> dict[str, Optional[_dt.datetime]]:
    """Newest receipt datetime per task (aware; None = never receipted).
    The watchdog freshness signal — task_watchdog delegates here."""
    ids = [normalize_task_id(t) for t in task_ids] if task_ids is not None else list(RECEIPT_TYPES)
    out: dict[str, Optional[_dt.datetime]] = {tid: None for tid in ids}
    for r in iter_receipts(workspace_root, task_ids=ids):
        if r["dt"] is not None:
            cur = out.get(r["task_id"])
            if cur is None or r["dt"] > cur:
                out[r["task_id"]] = r["dt"]
    return out


def count_runs(
    workspace_root,
    *,
    since: Optional[_dt.datetime] = None,
    until: Optional[_dt.datetime] = None,
    task_ids: Optional[Iterable[str]] = None,
) -> dict[str, int]:
    """Run count per task — the usage-report number (F-49's acceptance).

    Counting rules (the documented contract):
      - Only a task's `count_types` (defaulting to its full `types` set)
        are run-countable — monthly-report's value_receipt_generated events
        are freshness signals, not runs (one fire writes 2-3 of them).
      - Receipts of DIFFERENT types chained within RUN_DEDUP_WINDOW are ONE
        run (a fire emitting primary + secondary receipts).
      - Receipts of the SAME type never merge — two pack_runs minutes apart
        are two real runs.
      - Timestamp-less receipts each count as one run (never dropped).

    Returns {canonical task_id: run count} for every requested task,
    zero-filled — a task with no receipts reports 0, it does not vanish
    from the table (F-49's missing-rows failure mode).
    """
    ids = [normalize_task_id(t) for t in task_ids] if task_ids is not None else list(RECEIPT_TYPES)
    counts: dict[str, int] = {tid: 0 for tid in ids}
    receipts = iter_receipts(workspace_root, task_ids=ids, since=since, until=until)

    by_task: dict[str, list[dict]] = {}
    for r in receipts:
        spec = RECEIPT_TYPES.get(r["task_id"], {})
        countable = spec.get("count_types") or spec.get("types") or frozenset()
        if r["type"] not in countable:
            continue
        by_task.setdefault(r["task_id"], []).append(r)

    for tid, rs in by_task.items():
        undated = [r for r in rs if r["dt"] is None]
        dated = sorted((r for r in rs if r["dt"] is not None), key=lambda r: r["dt"])
        runs = len(undated)
        cluster_type_counts: dict[str, int] = {}
        cluster_last: Optional[_dt.datetime] = None
        for r in dated:
            if cluster_last is not None and (r["dt"] - cluster_last) <= RUN_DEDUP_WINDOW:
                cluster_type_counts[r["type"]] = cluster_type_counts.get(r["type"], 0) + 1
            else:
                if cluster_type_counts:
                    runs += max(cluster_type_counts.values())
                cluster_type_counts = {r["type"]: 1}
            cluster_last = r["dt"]
        if cluster_type_counts:
            runs += max(cluster_type_counts.values())
        counts[tid] = runs
    return counts


__all__ = [
    "CANONICAL_TASK_IDS",
    "RECEIPT_TYPES",
    "ALL_RECEIPT_TYPES",
    "FIRED_VIA",
    "LATENESS_FIELD",
    "RUN_DEDUP_WINDOW",
    "normalize_task_id",
    "normalize_fired_via",
    "get_late_tier",
    "receipt_task_id",
    "log_receipt",
    "iter_receipts",
    "last_receipt_times",
    "count_runs",
    "PREP_RECEIPT_TYPE",
    "log_prep_receipt",
    "prep_receipts",
    "prep_exists_for_meeting",
]
