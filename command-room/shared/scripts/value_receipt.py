"""Value receipt — deterministic monthly/quarterly ROI receipt (SPEC C1).

WHY THIS EXISTS
---------------
The CEO wants a monthly "value receipt" they can read in chat and forward as a
.docx: how many commitments got captured and resolved on time, how many drafts
and briefs were produced, how many meetings were processed, how many quiet
relationships got resurfaced, how many decisions were logged — plus a
CONSERVATIVE-labeled hours-saved estimate. A quarterly roll-up variant (3-month
window + a per-month breakdown table) is tuned for the justify-the-spend
audience the CEO forwards to a board or CFO.

The numbers are COMPUTED IN CODE, never by the LLM. operator-report counts every
section by hand in SKILL.md prose; the same failure mode (the model gets the
arithmetic wrong, or quietly invents a number) is exactly what the Bug #98/#99
enforcement model exists to kill: a printed claim is not a checkable artifact, an
EVENT is. So `compute_value_receipt` does the math, appends a
`value_receipt_generated` audit event carrying the CODE's numbers, and returns a
receipt the skill renders verbatim. `validate_receipt_ran` reads the event back —
a hand-rolled bypass (numbers in prose, no event) is detectable. This mirrors
`reconcile_sent_commitments.reconcile_and_receipt` / `validate_reconcile_ran`.

PRIVACY
-------
Every number comes from the customer's OWN `events.jsonl`. The receipt carries
counts + hours ONLY — zero names, topics, or email content — so the forwardable
.docx passes `docx_leak_scanner`. The audit event never leaves the customer's
own activity log. See skills/value-receipt/SKILL.md "Positioning".

PURE vs ORCHESTRATOR
--------------------
  - `compute_metrics(events, window_start, window_end)` is pure: it takes a
    pre-loaded event list + window bounds and returns the metric dict, the
    conservative hours estimate, and the per-month breakdown. No I/O, no clock.
  - `compute_value_receipt(workspace_root, ...)` is the orchestrator: it loads
    events defensively, calls compute_metrics, appends the audit event, and
    returns the full receipt (metrics + ready-to-render sections + chat summary).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from cru_match import (  # noqa: E402
    load_events_defensively,
    _commitment_field,
    _commitment_id,
    _now_iso,
)
from event_time import event_dt, event_time  # noqa: E402

try:
    from source_skill_compat import normalize_source_skill  # noqa: E402
except ImportError:  # pragma: no cover - back-compat helper always ships
    def normalize_source_skill(value):
        return value

try:
    from receipts import receipt_task_id  # noqa: E402 — v4.5.2 R1 shared reader
except ImportError:  # pragma: no cover - contract module always ships
    def receipt_task_id(ev):
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        return normalize_source_skill(data.get("task_id"))


# ---------------------------------------------------------------------------
# Conservative time rubric — SINGLE SOURCE OF TRUTH (SPEC C1 D2).
# ---------------------------------------------------------------------------
# Values copied verbatim from operator-report/SKILL.md Section 4's rubric table.
# Keeping ONE constant means a customer who challenges the estimate is a one-line
# tunable, and a drifted rubric in a forwarded document (two tables disagreeing)
# can never happen. operator-report points at this constant as canonical.
# Minutes "absorbed" per unit of work — deliberately low; the "Conservative"
# label is the defense, and under-claiming a strong month beats over-claiming a
# thin one.
CONSERVATIVE_MINUTES_PER_UNIT = {
    "commitment_captured": 8,     # would have been re-asked / forgotten
    "meeting_processed": 12,      # turned into a structured brief
    "morning_briefing": 15,       # delivered
    "prep_brief": 20,             # pre-meeting prep brief
    "email_triaged": 2,           # into Reply Now / Decision / FYI / Discard
    "email_drafted": 8,           # in voice (user reviews + sends or edits lightly)
    "decision_logged": 10,        # avoids re-debate next time the topic surfaces
    "aging_followup": 5,          # surfaced
    "cleanup": 30,                # weekly self-maintenance
    "cold_relationship_flag": 25, # one nudge prevents a quarter of stale silence
}


# Commitments "captured" are those extracted from a meeting/email rather than
# user-created. Mirrors operator-report Section 1 (normalized through
# source_skill_compat so pre-rename `cr-*` history on migrated workspaces still
# counts). events.jsonl is append-only — we never rewrite it; we read tolerantly.
CAPTURE_SKILLS = frozenset(
    {"meeting-notes", "inbox-triage", "follow-up-ritual", "scan-for-commitments"}
)

# pack_run task ids that represent a delivered briefing artifact.
MORNING_BRIEF_TASK_IDS = frozenset({"morning-brief"})
PREP_BRIEF_TASK_IDS = frozenset({"upcoming-meetings"})

# "Other deliverables" rolled into one documents-produced line (SPEC C1 D4).
DOCUMENT_EVENT_TYPES = frozenset(
    {"memo_drafted", "one_pager_drafted", "contract_reviewed", "board_pack_assembled"}
)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _parse_dt(value):
    """Parse an ISO-8601 timestamp (or a bare date) to a naive-UTC datetime for
    half-open window comparison. Tolerates `...Z`, `...+00:00`, and naive shapes
    (the auto-stamp emits +00:00, the build_* helpers emit Z, some legacy events
    are naive). Returns None on junk."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        v = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            try:
                dt = datetime.fromisoformat(v[:10])
            except ValueError:
                return None
    else:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _in_window(ts, start_dt, end_dt) -> bool:
    """Half-open membership: start <= ts < end. An event exactly at start is
    included; one exactly at end is excluded (a calendar month is
    [first-of-month, first-of-next-month))."""
    dt = _parse_dt(ts)
    if dt is None:
        return False
    return start_dt <= dt < end_dt


def _month_floor(dt: datetime) -> datetime:
    return datetime(dt.year, dt.month, 1)


def _next_month(dt: datetime) -> datetime:
    if dt.month == 12:
        return datetime(dt.year + 1, 1, 1)
    return datetime(dt.year, dt.month + 1, 1)


_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _month_label(dt: datetime) -> str:
    return f"{_MONTH_NAMES[dt.month - 1]} {dt.year}"


def window_label(start_dt: datetime, end_dt: datetime) -> str:
    """Readable window string for titles/subtitles. A clean single calendar
    month renders as 'May 2026'; anything else as a 'Mon D – Mon D, YYYY' range
    over the inclusive last day."""
    last = end_dt  # end is exclusive; describe the inclusive span
    if start_dt == _month_floor(start_dt) and end_dt == _next_month(start_dt):
        return _month_label(start_dt)
    # Inclusive end date for human reading.
    from datetime import timedelta
    inclusive_end = end_dt - timedelta(days=1)
    return (
        f"{_MONTH_NAMES[start_dt.month - 1]} {start_dt.day}, {start_dt.year} – "
        f"{_MONTH_NAMES[inclusive_end.month - 1]} {inclusive_end.day}, {inclusive_end.year}"
    )


def _closer_id(ev: dict) -> str:
    """Commitment id a commitment_resolved / thread_resolved event closes.
    Mirrors cru_match.load_open_commitments' fallback chain."""
    d = ev.get("data") or {}
    return (
        d.get("commitment_id")
        or d.get("thread_id")
        or d.get("id")
        or d.get("target_id")
        or ev.get("commitment_id")
        or ev.get("thread_id")
        or ev.get("id")
        or ""
    )


# ---------------------------------------------------------------------------
# Pure compute
# ---------------------------------------------------------------------------

# Each tuple maps a metric key to the rubric key whose conservative minutes it
# absorbs. Metrics NOT in this list (drafts_sent, documents_produced,
# resolved_on_time, briefs_delivered) are reported but do NOT add hours:
#   - drafts_sent: the work was the DRAFT (already counted via drafts_produced);
#     a legacy send with no draft counts once in "sent", never synthesizes a
#     phantom draft (avoids double counting).
#   - documents_produced / resolved_on_time: real value, but the operator-report
#     rubric has no per-unit minutes for them — inventing one would drift from
#     the canonical table, so they stay display-only (conservative under-claim).
#   - briefs_delivered is the morning+prep total for the display line; hours come
#     from the two sub-counts so the rubric split (15 vs 20 min) is preserved.
_HOURS_CONTRIB = (
    ("commitments_captured", "commitment_captured"),
    ("meetings_processed", "meeting_processed"),
    ("morning_briefings", "morning_briefing"),
    ("prep_briefs", "prep_brief"),
    ("drafts_produced", "email_drafted"),
    ("decisions_logged", "decision_logged"),
    ("dormant_resurfaced", "cold_relationship_flag"),
)


def _hours_from_metrics(metrics: dict) -> float:
    """Conservative hours absorbed = sum(count x rubric minutes) / 60, 2 dp."""
    minutes = sum(
        metrics.get(metric_key, 0) * CONSERVATIVE_MINUTES_PER_UNIT[rubric_key]
        for metric_key, rubric_key in _HOURS_CONTRIB
    )
    return round(minutes / 60.0, 2)


def _window_metrics(events, start_dt: datetime, end_dt: datetime, commitments_by_id: dict) -> dict:
    """Compute the raw metric counts for a single half-open window. `events` is
    the full list (so resolved-on-time can join against commitments outside the
    window); `commitments_by_id` is the precomputed id -> commitment-event map."""
    commitments_captured = 0
    resolved_on_time = 0
    drafts_produced = 0
    drafts_sent = 0
    meetings_processed = 0
    decisions_logged = 0
    dormant_resurfaced = 0
    documents_produced = 0
    prep_briefs = 0
    # Briefings: a pack_run is canonical; a `briefing` event counts only when no
    # same-day morning-brief pack_run exists (one fire can emit both — SPEC C1
    # Risks). Dedupe morning briefings by calendar date.
    morning_pack_dates = set()
    briefing_dates = set()

    for ev in events:
        ts = event_time(ev)
        if not _in_window(ts, start_dt, end_dt):
            continue
        et = ev.get("type")
        if et == "commitment":
            if normalize_source_skill(ev.get("source_skill")) in CAPTURE_SKILLS:
                commitments_captured += 1
        elif et == "commitment_resolved":
            commitment = commitments_by_id.get(_closer_id(ev))
            if commitment is not None:
                due = _parse_dt(_commitment_field(commitment, "due"))
                resolved = _parse_dt(ts)
                if due is not None and resolved is not None and resolved <= due:
                    resolved_on_time += 1
        elif et == "email_drafted":
            drafts_produced += 1
        elif et == "email_sent":
            drafts_sent += 1
        elif et == "meeting_processed":
            meetings_processed += 1
        elif et == "decision" or et == "decision_memo_drafted":
            decisions_logged += 1
        elif et == "pattern_break_detected" or et == "thread_resurrected":
            dormant_resurfaced += 1
        elif et in DOCUMENT_EVENT_TYPES:
            documents_produced += 1
        elif et == "pack_run":
            # v4.5.2 R1 — match through the receipt contract, not data.task_id
            # alone: kind-only receipts (F-10b's live shape) and legacy cr-*/
            # underscore spellings (F-49's miss class) all resolve.
            task_id = receipt_task_id(ev)
            if task_id in MORNING_BRIEF_TASK_IDS:
                d = _parse_dt(ts)
                if d is not None:
                    morning_pack_dates.add(d.date())
            elif task_id in PREP_BRIEF_TASK_IDS:
                prep_briefs += 1
        elif et == "briefing":
            d = _parse_dt(ts)
            if d is not None:
                briefing_dates.add(d.date())

    # Count a standalone briefing only on days with no morning-brief pack_run.
    extra_briefings = len(briefing_dates - morning_pack_dates)
    morning_briefings = len(morning_pack_dates) + extra_briefings

    metrics = {
        "commitments_captured": commitments_captured,
        "resolved_on_time": resolved_on_time,
        "drafts_produced": drafts_produced,
        "drafts_sent": drafts_sent,
        "morning_briefings": morning_briefings,
        "prep_briefs": prep_briefs,
        "briefs_delivered": morning_briefings + prep_briefs,
        "meetings_processed": meetings_processed,
        "dormant_resurfaced": dormant_resurfaced,
        "decisions_logged": decisions_logged,
        "documents_produced": documents_produced,
    }
    return metrics


def compute_metrics(events, window_start, window_end) -> dict:
    """Pure metric computation over `events` for the half-open window
    [window_start, window_end). `window_start`/`window_end` may be ISO strings
    or datetimes.

    Returns:
      {
        "window": "<start-iso>..<end-iso>",
        "metrics": {<the SPEC C1 D4 metric set>},
        "hours_estimate": float,        # conservative, 2 dp
        "per_month": [                  # one row per calendar month in window
          {"month": "2026-05", "label": "May 2026", "hours_estimate": ...,
           **per-month metrics}
        ],
      }
    """
    start_dt = _parse_dt(window_start)
    end_dt = _parse_dt(window_end)
    if start_dt is None or end_dt is None:
        raise ValueError(
            f"compute_metrics needs parseable window bounds; got "
            f"{window_start!r}..{window_end!r}"
        )
    if end_dt <= start_dt:
        raise ValueError(
            f"window_end ({window_end!r}) must be after window_start "
            f"({window_start!r})"
        )

    events = events or []
    commitments_by_id = {}
    for ev in events:
        if ev.get("type") == "commitment":
            commitments_by_id.setdefault(_commitment_id(ev), ev)

    metrics = _window_metrics(events, start_dt, end_dt, commitments_by_id)
    hours_estimate = _hours_from_metrics(metrics)

    # Per-month breakdown — contiguous, non-overlapping buckets clipped to the
    # window, so each event lands in exactly one bucket and the rows sum to the
    # totals (the quarterly roll-up's per-month table + its summation guarantee).
    per_month = []
    cursor = _month_floor(start_dt)
    while cursor < end_dt:
        bucket_start = max(cursor, start_dt)
        bucket_end = min(_next_month(cursor), end_dt)
        bucket_metrics = _window_metrics(events, bucket_start, bucket_end, commitments_by_id)
        row = {
            "month": f"{cursor.year:04d}-{cursor.month:02d}",
            "label": _month_label(cursor),
            "hours_estimate": _hours_from_metrics(bucket_metrics),
        }
        row.update(bucket_metrics)
        per_month.append(row)
        cursor = _next_month(cursor)

    return {
        "window": f"{start_dt.isoformat()}..{end_dt.isoformat()}",
        "metrics": metrics,
        "hours_estimate": hours_estimate,
        "per_month": per_month,
    }


# ---------------------------------------------------------------------------
# Render helpers — code builds the strings so the numbers are NEVER LLM math.
# ---------------------------------------------------------------------------

def _plural(n: int, singular: str, plural: str = None) -> str:
    return singular if n == 1 else (plural or singular + "s")


# The exact disclaimer sentence from operator-report/SKILL.md Section 4 — used
# verbatim on every value-receipt surface so the estimate is always labeled
# Conservative.
CONSERVATIVE_DISCLAIMER = (
    "Conservative — assumes you would have done each of these tasks yourself at "
    "average speed. The real lift is usually higher because half of these would "
    "have just dropped."
)


def _count_bullets(metrics: dict) -> list:
    """Plain-English count lines, counts only — zero names/topics so the
    forwardable .docx passes the leak scanner. Omit zero lines (omit-don't-pad);
    a thin month shows thin numbers, never a padded one."""
    lines = []
    m = metrics

    def add(n, text):
        if n > 0:
            lines.append(text)

    add(m["commitments_captured"],
        f"{m['commitments_captured']} {_plural(m['commitments_captured'], 'commitment')} "
        f"captured that weren't tracked anywhere else")
    add(m["resolved_on_time"],
        f"{m['resolved_on_time']} {_plural(m['resolved_on_time'], 'commitment')} "
        f"resolved on time")
    add(m["meetings_processed"],
        f"{m['meetings_processed']} {_plural(m['meetings_processed'], 'meeting')} "
        f"turned into a structured brief")
    add(m["morning_briefings"],
        f"{m['morning_briefings']} morning {_plural(m['morning_briefings'], 'briefing')} delivered")
    add(m["prep_briefs"],
        f"{m['prep_briefs']} pre-meeting prep {_plural(m['prep_briefs'], 'brief')}")
    add(m["drafts_produced"],
        f"{m['drafts_produced']} {_plural(m['drafts_produced'], 'draft')} written in your voice")
    add(m["decisions_logged"],
        f"{m['decisions_logged']} {_plural(m['decisions_logged'], 'decision')} logged")
    add(m["dormant_resurfaced"],
        f"{m['dormant_resurfaced']} quiet {_plural(m['dormant_resurfaced'], 'relationship')} resurfaced")
    add(m["documents_produced"],
        f"{m['documents_produced']} {_plural(m['documents_produced'], 'document')} produced")

    if not lines:
        lines.append("No recorded activity in this window yet.")
    return lines


def _hours_body(hours_estimate: float) -> str:
    """The hours block, with the literal word 'Conservative' adjacent to the
    figure (SPEC C1 acceptance #7)."""
    return (
        f"~{hours_estimate:g} hours of operational overhead absorbed.\n\n"
        f"{CONSERVATIVE_DISCLAIMER}"
    )


# Metric keys that roll up into the "Actions handled" tile — every unit of work
# Command Room actually did this window. briefs_delivered already folds
# morning_briefings + prep_briefs, so those are NOT re-added (no double count).
_ACTIONS_HANDLED_KEYS = (
    "commitments_captured",
    "meetings_processed",
    "briefs_delivered",
    "drafts_produced",
    "decisions_logged",
    "documents_produced",
    "dormant_resurfaced",
)


def build_receipt_tiles(metrics: dict, hours_estimate: float) -> list:
    """The stat-tile band for the value receipt (SPEC OUT1 §4): Actions handled ·
    Hours returned (conservative) · Threads advanced. Values come straight from
    the computed receipt — this NEVER re-derives math the skill would then
    render in prose (F-60: substrate-derived only).

    Drop rule follows this module's own omit-don't-pad convention (mirrors
    `_count_bullets`, which skips zero lines): a zero tile is omitted so a thin
    window shows a thin band, never a wall of zeros. Returns [] when nothing was
    handled — the caller then omits the band entirely (`_count_bullets` already
    prints the honest 'No recorded activity' line)."""
    m = metrics
    actions_handled = sum(int(m.get(k, 0)) for k in _ACTIONS_HANDLED_KEYS)
    # A "thread advanced" = an outbound draft written or a quiet relationship
    # resurfaced — both move a conversation forward.
    threads_advanced = int(m.get("drafts_produced", 0)) + int(m.get("dormant_resurfaced", 0))

    tiles = []
    if actions_handled > 0:
        tiles.append({"label": "Actions handled", "value": str(actions_handled)})
    if hours_estimate and hours_estimate > 0:
        tiles.append({"label": "Hours returned (conservative)", "value": f"~{hours_estimate:g}"})
    if threads_advanced > 0:
        tiles.append({"label": "Threads advanced", "value": str(threads_advanced)})
    return tiles


def _build_sections(metrics: dict, hours_estimate: float, per_month: list, rollup: str) -> list:
    """A ready-to-pass make_brief sections list. Code builds every number into
    the strings, so the skill renders — it never recomputes in prose."""
    sections = []
    # SPEC OUT1 §4 — tile band above the counts. Substrate-derived, drop-empty.
    tiles = build_receipt_tiles(metrics, hours_estimate)
    if tiles:
        sections.append({"heading": "At a glance", "tiles": tiles})
    sections += [
        {"heading": "What Command Room handled", "bullets": _count_bullets(metrics)},
        {"heading": "Time absorbed", "body": _hours_body(hours_estimate)},
    ]
    # Quarterly roll-up: a per-month breakdown table (counts + hours per month).
    if rollup == "quarter" and len(per_month) > 1:
        headers = ["Month", "Commitments", "Meetings", "Briefs", "Drafts", "Decisions", "Hours"]
        rows = []
        for r in per_month:
            rows.append([
                r["label"],
                str(r["commitments_captured"]),
                str(r["meetings_processed"]),
                str(r["briefs_delivered"]),
                str(r["drafts_produced"]),
                str(r["decisions_logged"]),
                f"~{r['hours_estimate']:g}",
            ])
        sections.append({
            "heading": "Month by month",
            "table": {"rows": rows, "headers": headers},
        })
    return sections


def _build_summary(metrics: dict, hours_estimate: float, label: str) -> str:
    """The verbatim chat line the skill pastes. Contains 'Conservative' adjacent
    to the hours figure (acceptance #7). Honest on an empty window."""
    m = metrics
    if not any(m[k] for k in m):
        return (
            f"{label}: no recorded activity in this window yet. As Command Room "
            f"logs more of your work, this receipt fills in."
        )
    parts = []
    if m["commitments_captured"]:
        parts.append(f"captured {m['commitments_captured']} "
                     f"{_plural(m['commitments_captured'], 'commitment')}")
    if m["meetings_processed"]:
        parts.append(f"processed {m['meetings_processed']} "
                     f"{_plural(m['meetings_processed'], 'meeting')}")
    if m["briefs_delivered"]:
        parts.append(f"delivered {m['briefs_delivered']} "
                     f"{_plural(m['briefs_delivered'], 'brief')}")
    if m["drafts_produced"]:
        parts.append(f"wrote {m['drafts_produced']} "
                     f"{_plural(m['drafts_produced'], 'draft')}")
    if m["decisions_logged"]:
        parts.append(f"logged {m['decisions_logged']} "
                     f"{_plural(m['decisions_logged'], 'decision')}")
    lead = ", ".join(parts) if parts else "kept the operating layer running"
    return (
        f"{label}: {lead} — about {hours_estimate:g} hours of operational "
        f"overhead absorbed (Conservative: assumes you'd have done each yourself "
        f"at average speed)."
    )


# ---------------------------------------------------------------------------
# Orchestrator + audit event (SPEC C1 D1) — copies reconcile_and_receipt.
# ---------------------------------------------------------------------------

# Idempotency guard (v4.5.2 R4 / F-09+F-36): the writer double-emitted
# byte-identical month receipts three separate times in the dogfood (Jul 1
# ~22s apart, Jul 7 17s apart, Jul 8 10s apart — the skill re-emits on some
# second pass). A value_receipt_generated event for the SAME window + rollup
# with IDENTICAL numbers inside this interval is the same logical receipt:
# skip the append and note the skip on the returned receipt. If the numbers
# CHANGED (events landed between the two calls), the new snapshot is real and
# still writes — the guard only kills verbatim duplicates.
IDEMPOTENCY_GUARD = timedelta(minutes=10)


def _find_recent_duplicate(events, window_str, rollup, metrics, hours_estimate):
    """The most recent value_receipt_generated event that duplicates the
    receipt about to be written (same window, rollup, metrics, and hours),
    or None. Caller checks its age against IDEMPOTENCY_GUARD."""
    latest = None
    for ev in events:
        if ev.get("type") != "value_receipt_generated":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if (
            d.get("window") == window_str
            and d.get("rollup") == rollup
            and d.get("metrics") == metrics
            and d.get("hours_estimate") == hours_estimate
        ):
            latest = ev  # append-ordered — keep the last one seen
    return latest


def compute_value_receipt(
    workspace_root,
    window_start,
    window_end,
    *,
    rollup="month",
    source_skill="value-receipt",
    output_path=None,
):
    """Compute the value receipt end-to-end and return a tamper-proof receipt.

    Reads events.jsonl defensively, computes every metric IN CODE, appends ONE
    `value_receipt_generated` audit event carrying the code's numbers, and
    returns the receipt the skill renders. A skill that hand-rolls the numbers
    leaves no event — detectable via `validate_receipt_ran` (the Bug #98/#99
    enforcement model: an event is checkable, a printed sentence is not).

    Receipts are point-in-time and window-pure: re-running appends a SECOND event
    (correct — each is a snapshot) and never mutates prior events; there is no
    cursor state to go stale. EXCEPTION (v4.5.2 R4 / F-36): a re-run inside
    IDEMPOTENCY_GUARD whose window, rollup, AND numbers are identical to the
    latest prior receipt skips the append — the returned receipt notes the
    skip in `duplicate_guard` and the prior event remains the receipt of
    record (validate_receipt_ran still passes for the window).

    Args:
      workspace_root: absolute workspace root (the dir containing _hq/).
      window_start, window_end: ISO strings or datetimes; half-open
        [start, end) (a calendar month is [1st, 1st-of-next)).
      rollup: "month" (single window) or "quarter" (adds the per-month table).
      source_skill: tag for the audit event.
      output_path: optional path of the .docx the skill will write (the skill
        writes the doc AFTER calling this, so it's usually None here).

    Returns a receipt:
      {
        "ran": True,
        "window": "<start-iso>..<end-iso>",
        "rollup": str,
        "metrics": {...},               # the SPEC C1 D4 metric set
        "hours_estimate": float,
        "per_month": [ {...} ],
        "sections": [ ... ],            # ready to pass to make_brief
        "summary": str,                 # verbatim chat line
        "skipped_lines": int,           # malformed events.jsonl lines tolerated
        "duplicate_guard": {            # v4.5.2 R4 idempotency note
            "skipped": bool,            # True = duplicate write suppressed
            "prior_receipt_ts": str?,   # ts of the receipt of record when skipped
        },
      }
    """
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    events, skipped = load_events_defensively(events_path)

    computed = compute_metrics(events, window_start, window_end)
    metrics = computed["metrics"]
    hours_estimate = computed["hours_estimate"]
    per_month = computed["per_month"]
    window_str = computed["window"]

    start_dt = _parse_dt(window_start)
    end_dt = _parse_dt(window_end)
    label = window_label(start_dt, end_dt)

    sections = _build_sections(metrics, hours_estimate, per_month, rollup)
    summary = _build_summary(metrics, hours_estimate, label)

    # Idempotency guard (v4.5.2 R4 / F-09+F-36): if the latest prior receipt
    # for this window+rollup carries IDENTICAL numbers and is younger than
    # IDEMPOTENCY_GUARD, this call is the double-emit bug — skip the append
    # and note it. The prior event stays the receipt of record.
    duplicate = _find_recent_duplicate(
        events, window_str, rollup, metrics, hours_estimate
    )
    if duplicate is not None:
        prior_dt = event_dt(duplicate)
        now_dt = datetime.now(timezone.utc)
        if prior_dt is not None and (now_dt - prior_dt) <= IDEMPOTENCY_GUARD:
            return {
                "ran": True,
                "window": window_str,
                "rollup": rollup,
                "metrics": metrics,
                "hours_estimate": hours_estimate,
                "per_month": per_month,
                "sections": sections,
                "summary": summary,
                "skipped_lines": len(skipped),
                "duplicate_guard": {
                    "skipped": True,
                    "prior_receipt_ts": event_time(duplicate),
                },
            }

    # ALWAYS append a value_receipt_generated AUDIT event carrying the CODE's
    # numbers — even on an empty window. Enforcement points at THIS event
    # (metrics + hours_estimate), not a printed sentence: a hand-rolled receipt
    # with no event fails validate_receipt_ran. The payload numbers are
    # byte-equal to the returned receipt (acceptance #3).
    from next_seq import next_seq
    from atomic_write import atomic_append_jsonl
    audit_event = {
        "seq": next_seq(str(events_path)),
        "ts": _now_iso(),
        "type": "value_receipt_generated",
        "source_skill": source_skill,
        "data": {
            "window": window_str,
            "rollup": rollup,
            "metrics": metrics,
            "hours_estimate": hours_estimate,
            "output_path": output_path,
        },
    }
    atomic_append_jsonl(events_path, [audit_event])

    return {
        "ran": True,
        "window": window_str,
        "rollup": rollup,
        "metrics": metrics,
        "hours_estimate": hours_estimate,
        "per_month": per_month,
        "sections": sections,
        "summary": summary,
        "skipped_lines": len(skipped),
        "duplicate_guard": {"skipped": False},
    }


def validate_receipt_ran(workspace_root, *, window=None) -> dict:
    """Read events.jsonl back and confirm a REAL value receipt was generated
    (SPEC C1 D1). The ungameable check: a printed receipt with no
    `value_receipt_generated` event returns ok=False.

    Looks at the LATEST `value_receipt_generated` event. If `window` is given,
    that event's `data.window` must match (so a stale prior receipt can't pass
    for the requested window).

    Returns {ok, ran, reason?, window, rollup, hours_estimate, metrics}.
    """
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not events_path.exists():
        return {"ok": False, "ran": False, "reason": "no activity log yet"}
    events, _skipped = load_events_defensively(events_path)
    latest = None
    for ev in events:
        if ev.get("type") == "value_receipt_generated":
            latest = ev  # append-ordered -> keep the last one seen
    if latest is None:
        return {
            "ok": False, "ran": False,
            "reason": "no value_receipt_generated event — the receipt was not actually computed",
        }
    d = latest.get("data") or {}
    if window is not None and d.get("window") != window:
        return {
            "ok": False, "ran": True,
            "reason": f"latest receipt is for a different window "
                      f"(window={d.get('window')!r} != expected {window!r})",
            "window": d.get("window"),
        }
    return {
        "ok": True, "ran": True,
        "window": d.get("window"),
        "rollup": d.get("rollup"),
        "hours_estimate": d.get("hours_estimate"),
        "metrics": d.get("metrics"),
    }


__all__ = [
    "CONSERVATIVE_MINUTES_PER_UNIT",
    "CONSERVATIVE_DISCLAIMER",
    "IDEMPOTENCY_GUARD",
    "compute_metrics",
    "compute_value_receipt",
    "validate_receipt_ran",
    "window_label",
    "build_receipt_tiles",
]


if __name__ == "__main__":
    # Convenience CLI: read a JSON payload {workspace_root, window_start,
    # window_end, rollup?} from the file path in argv[1], compute the receipt,
    # and print it as JSON. Lets a skill shell in without inlining the logic.
    import json

    if len(sys.argv) < 2:
        print("usage: value_receipt.py <payload.json>", file=sys.stderr)
        raise SystemExit(2)
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = compute_value_receipt(
        payload["workspace_root"],
        payload["window_start"],
        payload["window_end"],
        rollup=payload.get("rollup", "month"),
        source_skill=payload.get("source_skill", "value-receipt"),
        output_path=payload.get("output_path"),
    )
    print(json.dumps(out, indent=2))
