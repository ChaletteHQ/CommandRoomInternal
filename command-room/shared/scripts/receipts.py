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
from typing import Iterable, List, Optional

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
    "review-expiry",  # REVSCHED1 — the weekly unconfirmed-pile drain job inside `maintenance` (never a task of its own; see maintenance_dispatcher.MAINTENANCE_JOBS)
    "age-out",       # SWEEPSCHED1 — the weekly CONFIRMED-pile drain job inside `maintenance` (never a task of its own; sibling of review-expiry, different pile, different bar)
    "calendar-close",  # POLICY1-B DD-7 — the daily calendar closer for scheduling rows inside `maintenance` (never a task of its own; confirm-first for three fires)
    # EOD2 — the 5 PM chat's taskId was RENAMED to `end-of-day`, but the
    # RECEIPT id deliberately did NOT move: `end_of_day.TASK_ID` still writes
    # `past-meetings`, so the day-close series is ONE continuous history
    # across the rename instead of two half-series either side of whenever a
    # given customer took the offer. `past-meetings` therefore stays the
    # WRITTEN id; `end-of-day` is registered below so the watchdog can ask
    # about the successor by name and TASK_PREDECESSORS bridges the read.
    "past-meetings",
    "end-of-day",
    "friday-wrap",
    "cleanup",
    "reconcile-sent",
    # CHATSCAN1 — the chat closure leg. A SIBLING job inside the same
    # `maintenance` task as `reconcile-sent`, not a new scheduled task: M's
    # ruling is that closing from chat is wired into the maintenance cadence
    # exactly like closing from mail, so it needs its own job id, its own
    # receipt and its own due-ness — and NO registration of its own.
    "reconcile-chat",
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
    "balance",        # BAL1 — the personal white-space surface (m_facing only; receipt carries counts, never personal content). TASKRET1 READINESS-retired the Sunday CHAT; this row stays forever so pre-retirement receipts keep parsing, and the on-demand `balance check` fire still writes it.
    "pipeline-digest",  # PIPE1 Part 2 — the Tuesday deal-review chat. TASKRET1 READINESS-retired it; the row stays forever so pre-retirement receipts keep parsing (and its since-window marker stays readable), but nothing writes it any more — the on-demand report is `pipeline-tracker` above.
    "deal-signals",   # LB1 D7 — the deal-signal detector job inside `maintenance`
    "identity-reconcile",  # PID1 D7 — the Sunday identity reconciler job inside `maintenance` (also the M-fired one-time backfill)
    "meeting-capture",  # EODSPEED1 — the incremental capture pass job inside `maintenance` (never a task of its own; its pack_run is the dispatcher's dueness signal and catchup_window's resume point — NEVER written under past-meetings, which would arm skip_render against the real close)
    "monthly-scorecard",  # SPEC OUT7 — the OPT-IN monthly KPI scorecard job inside `maintenance` (never auto-fires; its pack_run receipt self-limits it to monthly once opted in)
    "binding-gauge",  # GAUGEJOB1 — the daily binding-gauge refresh job inside `maintenance` (never a task of its own; its pack_run is the dueness signal, written on CHANGE runs only — a quiet run leaves no trace, see binding_gauge.run_gauge_refresh_job's QUIET-RUN SEMANTICS)
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
    # EOD2 — the RENAME direction. Stronger than the CTS1 split above: the
    # successor is the same chat under a new id, reading the same orchestrator
    # file, and the fire still WRITES `past-meetings`. So every `end-of-day`
    # read resolves through here, on both sides of the rename and forever —
    # a workspace that took the offer must not read as never-fired, and one
    # that never takes it must not read as never-registered.
    "end-of-day": ("past-meetings",),
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
    # EOD2 — the successor id. Same type, and in practice the receipts it
    # reads are written under `past-meetings` (TASK_PREDECESSORS bridges
    # them): the row exists so the watchdog / usage-report can ASK about
    # `end-of-day` by name without a KeyError, and so a receipt ever written
    # under the new id parses rather than being dropped as unknown.
    "end-of-day":         {"types": frozenset({"pack_run"})},
    "friday-wrap":        {"types": frozenset({"pack_run"})},
    "relationship-moves": {"types": frozenset({"pack_run"})},
    # TASKRET1 READINESS-retired the Friday CHAT; this row stays forever so
    # pre-retirement receipts keep parsing. The on-demand `triage my
    # commitments` fire is unchanged and does not write a pack_run (only the
    # scheduled fire ever did).
    "commitment-triage":  {"types": frozenset({"pack_run"})},
    "cleanup":            {"types": frozenset({"cleanup_run", "audit_run"})},
    "reconcile-sent":     {"types": frozenset({"sent_reconcile"})},
    # CHATSCAN1 — the chat leg's own receipt. Deliberately a DIFFERENT type
    # from `sent_reconcile`: the two legs run in the same fire, and
    # `validate_reconcile_ran` reads "the latest sent_reconcile event". Had
    # the chat leg written that type, a chat run would have satisfied the mail
    # leg's validator and a mail leg that never fired would have read as
    # healthy — the two would have vouched for each other. Same shape, same
    # cadence, separate proof.
    "reconcile-chat":     {"types": frozenset({"chat_reconcile"})},
    # SPEC EODSPEED1 — the incremental capture pass's job receipt (the
    # `meeting-capture` maintenance job). `pack_run`, the standard
    # scheduled-job shape: the dispatcher's due-ness rule reads it and
    # `catchup_window("meeting-capture", ...)` resumes from its window
    # fields. Deliberately its OWN task id, never `past-meetings`: a
    # pack_run on the day-close series would arm skip_render against the
    # real 5 PM close and split the series EOD2 keeps whole.
    "meeting-capture":    {"types": frozenset({"pack_run"})},
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
    # REVSCHED1 §3-2 — the weekly unconfirmed-pile drain job's receipt.
    # `pack_run`, the standard scheduled-job shape (like deal-signals): the
    # dispatcher's dueness rule reads it, so it self-limits to weekly. Written
    # on an EMPTY plan too — the no-op is silent to the CEO but never silent to
    # the ledger, because a job that only receipts when it finds work
    # re-derives the whole pile at every one of the task's three daily slots
    # forever. Its extra_data carries the §3-4 honesty trio (n_applied /
    # n_shielded_by_reopen / n_held_back), every number off the writer's own
    # per-row results.
    "review-expiry":      {"types": frozenset({"pack_run"})},
    # SWEEPSCHED1 — the weekly confirmed-pile drain job's receipt. `pack_run`,
    # the same scheduled-job shape its review-tier sibling uses, so the
    # dispatcher's dueness rule self-limits it to weekly. Written on an EMPTY
    # plan and on a PROPOSING fire too, and the second one is load-bearing
    # twice over: it is the dueness signal, and `data.mode` on these receipts
    # IS the confirm-first counter — the job counts its own prior fires here
    # rather than in config, so there is no second piece of state to disagree
    # with the ledger. `mode` is "proposed" (showed its hand, closed nothing)
    # or "applied"; a refusal that never reached a plan carries neither and is
    # therefore not counted.
    "age-out":            {"types": frozenset({"pack_run"})},
    # POLICY1-B DD-7 — the calendar closer's receipt, the same scheduled-job
    # shape; written on a proposing fire and an applying fire alike (the
    # `mode` on it IS the confirm-first counter, like age-out's).
    "calendar-close":     {"types": frozenset({"pack_run"})},
    # GAUGEJOB1 — the daily binding-gauge refresh job's receipt. `pack_run`,
    # the standard scheduled-job shape: the dispatcher's dueness rule reads it
    # and self-limits the refresh to daily on any day with substrate
    # movement. Written on CHANGE runs ONLY, and BEFORE the artifact — a
    # deliberate departure from review-expiry's receipt-every-fire rule: the
    # receipt is itself a ledger event, so a quiet-run receipt (or one
    # written after the artifact) would advance the events high-water mark
    # past the artifact's own stamp and flag every reader stale over the
    # job's bookkeeping forever. A quiet run leaves no trace and simply
    # re-runs at the next fire (~2s, the accepted trade — see
    # binding_gauge's QUIET-RUN SEMANTICS note).
    "binding-gauge":      {"types": frozenset({"pack_run"})},
    # SPEC OUT7 — the opt-in monthly KPI scorecard job's receipt. pack_run, the
    # standard scheduled-pack shape (like deal-signals / staff-meeting): the
    # dispatcher's due-ness rule reads it so a fired scorecard self-limits to
    # monthly. Only ever written when the workspace opted the job in.
    "monthly-scorecard":  {"types": frozenset({"pack_run"})},
}

# Types that identify their task by TYPE alone (exactly one writer each).
_TYPE_IMPLIES_TASK = {
    "sent_reconcile": "reconcile-sent",
    "chat_reconcile": "reconcile-chat",
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
    """The machine token for this computer (SPEC SCHED1 §0-3), or None.

    THE FINDING (2026-08-17). This was `platform.node()`, and rule 4 of
    `RECEIPT_CONTRACT.md` says the field exists so "readers can't tell two
    machines from a double-registration bug". Inside Cowork's sandbox
    `platform.node()` returns the SAME string on every physical machine — 840
    receipts written from two different computers all carry one value — so the
    field answered the question it exists for with a constant. Swapping it for
    another sandbox-visible name fixes nothing: they are all properties of the
    sandbox, not of the computer under it.

    `machine_identity` resolves a machine-local marker instead. Kept as the ONE
    chokepoint rather than editing each stamp site to import the new module: a
    prose-free single point of change is how the `fired_via` vocabulary stopped
    drifting, and every existing caller migrates without touching its file.
    Falls back to the pre-SCHED1 behaviour if the module is unavailable.
    """
    try:
        from machine_identity import machine_id

        return machine_id()
    except Exception:
        try:
            import platform
            name = platform.node()
            return name[:64] if name else None
        except Exception:
            return None


def machine_fields() -> dict:
    """`{"machine": <token>}` for a receipt's `data`, `{}` when unknowable.

    THE PUBLIC stamp helper — every writer outside this module uses it rather
    than reaching for the private `_machine_name`, so the fallback flag lands
    uniformly instead of at whichever site remembered it.

    Carries `machine_id_fallback: true` ONLY when the token could not be
    persisted to the machine-local marker (a read-only or absent home), which
    means "this token is this run's best guess, not a durable identity".
    Present only when true, so the ordinary receipt's shape is unchanged.
    """
    try:
        from machine_identity import FALLBACK_FIELD, machine_identity

        ident = machine_identity()
        token = ident.get("machine")
        if not token:
            return {}
        out = {"machine": token}
        if ident.get("fallback"):
            out[FALLBACK_FIELD] = True
        return out
    except Exception:  # noqa: BLE001 — identity never blocks a receipt
        name = _machine_name()
        return {"machine": name} if name else {}


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
    now=None,
) -> dict:
    """THE receipt writer. Every scheduled/manual task fire ends with one
    call here (directly or via the log_pack_run back-compat wrapper).

    Validates the canonical vocabulary at write time (drift is a defect the
    moment it's written, not when a reader trips on it months later), routes
    through event_gate.append_event (enum check + locked writer + UTC
    auto-stamp), and returns the event dict as appended (minus seq/ts, which
    the writer lock stamps).

    `now` (SPEC FLAKEFIX2) is the instant the SLOT PROVENANCE below is measured
    against — machine-local naive, the clock cron evaluates in, or an ISO
    string this function parses for you. Omitted, provenance reads the real
    clock exactly as it always has and no caller sees any difference.

    IT EXISTS BECAUSE `slot_provenance` STAMPS A MINUTE-GRANULAR WALL-CLOCK
    READING (`slot_delta_minutes`) INTO EVERY RECEIPT'S `data`. Two receipts
    written back to back therefore carry different payloads whenever the pair
    straddles a wall-clock MINUTE — and a different `scheduled_for` whenever it
    straddles the task's own daily slot. A fixture comparing two payloads for
    equality must pin the instant here rather than race it; `slot_provenance`
    has always taken a `now`, this threads it the one hop that was missing.
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
    data.update(machine_fields())
    if extra_data:
        # extra_data never overrides the contract fields — task-specific
        # counts ride along; identity/vocabulary stays canonical.
        for k, v in extra_data.items():
            if k not in data:
                data[k] = v

    # WALKFIX1 Item H — where this fire landed relative to its own slot, so the
    # ledger explains itself. Stamped HERE rather than asked of each
    # orchestrator: a prose mandate across a dozen orchestrators is presumed
    # skipped (Bug #98 class), and every scheduled fire already ends at this
    # one call. Import is local — `late_fire` imports this module.
    # A caller that computed the fields itself keeps its own values.
    try:
        from late_fire import slot_provenance

        at = now
        if isinstance(at, str):
            at = _dt.datetime.fromisoformat(at.replace("Z", "+00:00"))
            if at.tzinfo is not None:
                at = at.astimezone().replace(tzinfo=None)
        for k, v in slot_provenance(workspace_root, canonical,
                                    now=at, fired_via=via).items():
            data.setdefault(k, v)
    except Exception:  # noqa: BLE001 — provenance never blocks a receipt
        pass

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
# WALKFIX1 Item J — receipt errors reach the chat, in one line
# ---------------------------------------------------------------------------

def receipt_errors_notice(receipt) -> Optional[str]:
    """ONE chat line when a fire's own receipt carries `data.errors`, else None.

    THE FINDING (2026-08-10). A past-meetings fire wrote an `errors[]` entry
    naming a real correctness defect in its own run, and the chat presented a
    clean two-brief recap with no mention of it. The operator learned about it
    only by reading the ledger. The product's own closing-silently-is-worse
    principle says a system that noticed something and said nothing has spent
    trust it did not have to.

    WHAT THIS DELIBERATELY IS NOT: an error dump, an alarm, or a phase-internals
    tour. One sentence, a count, and a pointer. The CEO is not being asked to
    do anything; they are being told the run is not claiming to be perfect.

    VOCABULARY NOTE — the wording differs from the spec's draft in one word,
    because the draft's own spelling is forbidden output. `seq N` is an
    "event seq leak" in the renderer's Gate 2 pattern table and `(seq N)` is a
    `telemetry_narration` violation in `chat_output_validator`; a line that
    trips the product's own gates cannot ship as product output. "entry" says
    the same thing to a reader and passes both, and the suite pins that it
    does rather than asserting it.

    Returns None for an empty / absent / malformed `errors` — a notice about
    nothing is worse than silence.
    """
    if not isinstance(receipt, dict):
        return None
    data = receipt.get("data") if isinstance(receipt.get("data"), dict) else {}
    errors = data.get("errors")
    if not isinstance(errors, list) or not errors:
        return None
    n = len(errors)
    noun = "correction" if n == 1 else "corrections"
    seq = receipt.get("seq")
    where = (f" (entry {seq})" if isinstance(seq, int)
             and not isinstance(seq, bool) else "")
    return f"{n} internal {noun} noted — details in the run receipt{where}."


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
    meeting_start: Optional[str] = None,
    attendee_person_ids: Optional[List[str]] = None,
    extra_data: Optional[dict] = None,
) -> dict:
    """THE per-brief receipt writer. Both prep paths (scheduled auto-prep and
    on-demand 'prep me') call this after a successful make_brief save.

    `meeting_id` is the calendar event id — the same identity `prep_slug`
    derives the filename from, so detector and file share one key.
    `refreshed` records that an existing brief was updated in place rather
    than a new one written (the F-29b contract made auditable).

    `meeting_start` is the INSTANCE discriminator (BRIEFFIX1 Item B, added
    2026-08-09 after the second-eyes review). A calendar id is stable across a
    recurring series, so the id alone cannot say WHICH standup a document was
    written for — and a reuse rule keyed on the id plus a clock window handed
    the CEO yesterday's brief for today's meeting, reproduced at 14h and 23.9h.
    The start time is the one field that differs between two instances, so it
    is what the reuse test compares.

    Optional, and its absence is meaningful rather than tolerated: every
    pre-BRIEFFIX1 receipt lacks it (history is append-only and nothing here
    rewrites it), and `prep_leg` treats a receipt with no `meeting_start` as
    unprovable and REGENERATES. Callers that have the value must pass it; the
    cost of omitting it is a duplicate document, never a stale one.

    `attendee_person_ids` (SPEC THREADBIND1 §0 ruling 3 — "prep_brief binds
    going forward"). Optional evidence for `thread_resolve.resolve_thread_binding`:
    a caller that already resolved the meeting's attendees for the brief's
    own content can pass them here at no extra lookup cost. Bound: the
    receipt's `data` gains `thread_id` next to `meeting_id`, plus
    `thread_basis`. Below the floor with an askable candidate set: ONE
    capped queue row fires through the standing adjudication queue,
    exactly as it would for any other canonical writer — the receipt itself
    still lands (fail-open; a resolver misfire never blocks the receipt).
    Omitting `attendee_person_ids` still lets the MEETING'S OWN prior
    binding resolve (evidence order step 1 needs only `meeting_id`, which
    this function always has) — the attendee fallback is the one extra
    resolution the caller's evidence unlocks.
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
    if isinstance(meeting_start, str) and meeting_start.strip():
        data["meeting_start"] = meeting_start.strip()
    data.update(machine_fields())
    if extra_data:
        for k, v in extra_data.items():
            if k not in data:
                data[k] = v

    # SPEC THREADBIND1 §0 ruling 3 — "prep_brief binds going forward". Only
    # when the caller (or extra_data) did not already supply one — an
    # explicit id always wins (evidence order step 0) and this never
    # overwrites it. Fail-open: a resolver misfire must never block the
    # brief's own receipt from landing (the floor is never below today).
    if not data.get("thread_id"):
        try:
            from thread_resolve import (
                BIND_CONFIDENCE_FLOOR,
                PROPOSABLE_BASES,
                propose_binding_row,
                resolve_thread_binding,
            )

            evidence = {"meeting_id": data["meeting_id"],
                       "attendee_person_ids": list(attendee_person_ids or [])}
            result = resolve_thread_binding(evidence, workspace_root=workspace_root)
            if result["thread_id"] and result["confidence"] >= BIND_CONFIDENCE_FLOOR:
                data["thread_id"] = result["thread_id"]
                data["thread_basis"] = result["basis"]
            elif result["basis"] in PROPOSABLE_BASES:
                propose_binding_row(workspace_root, evidence, result,
                                    detector="prep-brief-thread-resolve",
                                    title=data["slug"])
        except Exception:
            pass

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


def receipt_surface(receipt) -> Optional[str]:
    """The SURFACE a receipt served, when it names one and the name is a task
    this module knows (SPEC SURFCOUNT1). None otherwise.

    Some fires deliberately wear a task id that is not the surface they
    serve. The day-close is the shipped case: `end_of_day.TASK_ID` is the
    legacy `past-meetings` BY DESIGN — a rename that re-pointed the
    registration would have moved every customer's evening chat — and the
    fire stamps `data.surface: "end-of-day"` as the discriminator. So the
    receipt names both, and which of the two a reader should use depends on
    the question: "when did this REGISTERED task last fire" wants the task
    id; "how many times did this SURFACE run" wants the surface.

    Gated on `RECEIPT_TYPES` membership on purpose. An unrecognised surface
    string minting a bucket of its own would give the usage report a row with
    no `count_types` to judge against and no display name to render, and would
    silently move that fire out of the row it does belong in. Falling back to
    the task id keeps the run counted somewhere real — the conservative
    direction, and the same posture `normalize_task_id` takes with an
    unknown-but-clean id.
    """
    raw = receipt.get("raw") if isinstance(receipt, dict) else None
    data = raw.get("data") if isinstance(raw, dict) else None
    surface = data.get("surface") if isinstance(data, dict) else None
    if not isinstance(surface, str) or not surface.strip():
        return None
    tid = normalize_task_id(surface)
    return tid if tid in RECEIPT_TYPES else None


def run_bucket(receipt) -> Optional[str]:
    """WHICH ROW a receipt's run belongs in: its surface when it names a known
    one, else its task id (SPEC SURFCOUNT1).

    Exactly one bucket per receipt, which is what makes double-counting
    structurally impossible rather than a property somebody has to remember.
    Every reader that TALLIES or COMPARES fires across surfaces goes through
    here; readers asking about a single registered task's freshness
    (`last_receipt_times`, the dispatcher's due-ness rule) correctly keep
    using the task id, because their question really is about the task.
    """
    if not isinstance(receipt, dict):
        return None
    return receipt_surface(receipt) or receipt.get("task_id")


def count_runs(
    workspace_root,
    *,
    since: Optional[_dt.datetime] = None,
    until: Optional[_dt.datetime] = None,
    task_ids: Optional[Iterable[str]] = None,
) -> dict[str, int]:
    """Run count per task — the usage-report number (F-49's acceptance).

    Counting rules (the documented contract):
      - A receipt is counted under the SURFACE it served when it names one
        (`run_bucket`), else under its task id. The day-close fires under the
        legacy `past-meetings` id with `data.surface: "end-of-day"`; before
        SURFCOUNT1 those fires tallied under Past Meetings and End of Day
        rendered "ran 0x" the morning after it fired, which teaches a reader
        to distrust either the schedule or the report. The
        discriminate-on-surface rule was already written for the audit
        readers; this is the reporting readers applying it.
      - Only a task's `count_types` (defaulting to its full `types` set)
        are run-countable — monthly-report's value_receipt_generated events
        are freshness signals, not runs (one fire writes 2-3 of them). Judged
        against the BUCKET's spec, since the bucket is the row the run lands
        in.
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
    wanted = set(ids)
    # NO task filter on the fetch, and that is load-bearing rather than
    # sloppy: a receipt destined for the `end-of-day` bucket is written under
    # `past-meetings`, so filtering by the requested ids up front would drop
    # it before anything could read its surface — a caller asking only about
    # the day-close would get the same zero it gets today. The scan is the
    # same one pass either way (`iter_receipts` walks the events once
    # regardless); only the in-memory filter moves, to AFTER bucketing.
    receipts = iter_receipts(workspace_root, since=since, until=until)

    by_task: dict[str, list[dict]] = {}
    for r in receipts:
        bucket = run_bucket(r)
        if bucket not in wanted:
            continue
        spec = RECEIPT_TYPES.get(bucket, {})
        countable = spec.get("count_types") or spec.get("types") or frozenset()
        if r["type"] not in countable:
            continue
        by_task.setdefault(bucket, []).append(r)

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
    "receipt_surface",
    "run_bucket",
    "count_runs",
    "PREP_RECEIPT_TYPE",
    "log_prep_receipt",
    "prep_receipts",
    "prep_exists_for_meeting",
]
