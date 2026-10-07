#!/usr/bin/env python3
"""
OUTGATE1 (SPEC_SURFACES2 §10, 2026-09-08) — the writer-path gate reports
itself on the maintenance run.

WHY THIS EXISTS
----------------
BUG_2026-09-07_turn-hook-silent-and-gates-disagree: the per-turn `Stop` hook
(`gate2_turn_sweep.py`, wired via `hooks/hooks.json`) stopped emitting
entirely on 2026-08-24 and nothing noticed for two weeks, while it was
running it disagreed with the save-time writer gate scanning the SAME
artifact class (`docx` passed 74% and never recorded a fail; `turn_hook`
failed 94% of what it scanned), and 185 `docx` / 4 `premium_html` `gate_ran`
events in the live census carried no `result` field at all — a defect a
reader silently tolerated. M ruled 2026-09-07: Cowork never runs plugin
hooks, so the hook is retired outright (see `hooks/` removal, this same
lane) and the honest instrument left standing is the SAVE-TIME writer gate
(`brief_gates.emit_gate_ran_audit` for `docx`/`premium_html`,
`turn_backstop._emit_gate_ran_chat_email` for `chat_email`) — this module is
that instrument's own self-report, so a gate that stops recording is
detectable exactly the way `task_watchdog` already makes a dead SCHEDULED
TASK detectable (closes `BUG_2026-08-04_no-alarm-when-scheduled-surfaces-stop`
for the WRITER-GATE class of surface, distinct from that spec's scheduled-task
class).

REUSED SIGNAL, NEW SURFACE. Every field this module reads already exists on
the `gate_ran` event every writer chokepoint emits today
(`run_gate_ran_shape_test.py` pins the unified shape: every emitter carries
BOTH `result` and `artifact`). This module adds no new instrumentation to any
writer — it censuses what they already write and turns that census into two
plain findings, exactly as `task_alarm` turns `task_watchdog`'s existing
report into plain lines.

THE TWO FINDINGS
----------------
1. **A `gate_ran` event with no `result`.** Per the unified shape, every
   compliant emitter always sets `result` to "pass" or "fail" — a resultless
   row is either legacy debt (pre-shape-unification) or a new emitter that
   skipped the contract. Either way `defect_lines` names it: "no result
   recorded" IS a defect in the gate, not a comment on what it scanned.
2. **A gated writer surface gone dark.** `docx` / `premium_html` /
   `chat_email` are the surfaces a real writer chokepoint feeds today
   (`GATED_WRITER_SURFACES`) — `turn_hook` is deliberately
   EXCLUDED: it is retired, not merely idle, and reporting it dark would be
   reporting the intended state as a defect. A surface that has fired before
   and has gone quiet for `DARK_WINDOW_HOURS` (48h, the spec's own number) is
   dark. A surface that has NEVER fired is not alarmable — there is nothing to
   compare against, the same posture `task_alarm`'s `never_authorized` vs
   `late` split takes for scheduled tasks.

WHERE THIS RENDERS — MAINTENANCE ONLY, NEVER THE BRIEF. Exactly the HEALTH1
posture: `report_lines()` is consumed from `cleanup/SKILL.md`'s weekly
maintenance pass (Phase 3d-quinquies) alongside `task_alarm.dark_surface_lines`
and `substrate_health.substrate_alarm_lines` — never from
`surface_drivers.build_morning_brief_pack` or `build_end_of_day_pack`.
`tests/run_guard_writer_gate_not_on_brief_test.py` is the structural fence
for that boundary — including, as of FIX ROUND 1 (F-1), a POSITIVE half:
`cleanup/SKILL.md`'s shipped 3d-quinquies fence is proven to actually import
and call `report_lines` and fold it into the printed `writer_gate` key, not
merely proven absent from the brief. The fixture with a dark surface used
there and in `run_writer_gate_report_test.py` is the same fixture, so "one
maintenance-run line, zero brief lines, one maintenance line" is provable
from a single planted condition.

WINDOWED TALLIES, UNWINDOWED LAST-SEEN. `census_gate_ran`'s pass/fail/
no_result counts are windowed (`since_ts`, the weekly cadence's own 7 days by
default) so a workspace does not re-report the SAME historical debt every
Monday forever — only new occurrences since the last settled week. The
per-surface `last_ts` is always computed from the FULL history regardless of
the window: staleness math needs the true last fire, and a windowed
last-seen would make a surface that fired 3 days ago and nothing since read
as "never fired" the moment the window rolled past it.

FIX ROUND 2 (review finding F-8, HIGH, 2026-09-08) — `branded_pdf` is NOT in
this registry, and the fix round that added it is reverted. Round 1 added it
on a finding that misread `emit_gate_ran_audit`'s signature: `surface` is
that function's FIFTH parameter with a default of `"docx"`, and
`brief_writer.py`'s call site passes only FOUR arguments, so every
brief_writer render emits `surface="docx"` whatever the `brief_kind`.
Nothing in this tree can emit `surface="branded_pdf"`, so the dark-surface
line the registry entry rendered could never be cleared by any amount of
correct behaviour — a permanent, unresolvable line on the one weekly note
this module feeds. A registry entry with no emitter is now impossible to
add again: `tests/run_guard_writer_gate_not_on_brief_test.py` section [4]
reads the emitters straight out of the AST of `shared/scripts/*.py` and reds
on any member of this tuple that nothing can produce.

The `branded_pdf` events are real, and the finding underneath the bad fix is
real too — recorded here so it is not lost with the revert. A gate DID emit
that surface: 67 `gate_ran` events, `source_skill: brief_writer`,
`brief_kind: branded_pdf`, all `result: "pass"`, running
2026-07-29T04:21:26Z through 2026-08-31T19:45:48Z and then stopping. The
capability regressed: with the call site as it stands, that surface name can
never be written again. That is a genuinely dark writer surface — exactly
the class this module exists to detect — but repairing the emitter is a
separate build, and this lane must not ship an alarm whose subject it cannot
fix. When a branded-PDF chokepoint is built, its surface joins this tuple in
the SAME commit as its emitter, which is what the rule below already says
and what section [4] now enforces.

The round-1 review also asked a genuine either-way question: should a GATED
surface that has NEVER fired at all stay permanently silent (today's
posture), or should "gated, configured, and never once fired" become its own
reportable condition — mirroring `task_alarm`'s `never_authorized` class,
which DOES alarm on a scheduled task that was never authorized?

RULING: never-fired STAYS permanently silent. `task_alarm`'s
`never_authorized` alarms safely because every scheduled task carries a
`registered_days_ago` anchor (§`task_alarm.py` — "the workspace's
registration timestamp") — the module knows exactly how long a task has had
the chance to fire and can withhold the alarm until a fresh registration has
had a fair window. A gated WRITER surface has no equivalent TIME anchor: a
customer who has simply never produced a `chat_email` draft, or never had a
premium HTML brief rendered, has done nothing wrong and may never trigger
that surface in the surface's entire lifetime — that is a normal, permanent
usage pattern, not a transient "just configured, give it a minute" state.
Making never-fired reportable without a registration anchor to gate it would
alarm every workspace that simply doesn't use one of the three gated
document kinds — a far larger false-alarm surface than task_alarm's single
one-time Cowork-permission-gate case, and exactly the "lights up on day one"
failure this ruling exists to avoid. Building a bespoke registration-time
system for writer surfaces (which do not have one today) is a real feature,
out of scope for a MED, non-blocking fix. `dark_writer_lines` therefore keeps
its `if not entry.get("last_ts"): continue` guard unchanged; the pin in
`run_writer_gate_report_test.py` section [3] now also proves this holds even
across a LONG-lived workspace (60+ days of unrelated activity) — the
permanence of the posture, not merely an untested edge case.

WHAT THAT RULING DOES NOT SAY (round-2 review, F-12). It says there is no
TIME anchor, and that is true. There IS a DEMAND anchor already sitting in
the substrate this module reads — draft-class events (`draft_created`,
`email_drafted` and their siblings) are the evidence that a customer is
actually producing the kind of artifact a gated surface covers. "Gated
surface with zero `gate_ran` events AND N+ events of its own demand
evidence" separates "never used" (stay silent — the correct concern above)
from "used every week and never once gated" (broken from birth, and today
nothing anywhere says so). That is the follow-up, not this lane; the ruling
above stands, but it closes no door it has measured.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from event_time import event_dt, parse_ts  # noqa: E402

# The surfaces a real writer chokepoint feeds today (brief_gates for
# docx/premium_html, turn_backstop for chat_email). `turn_hook` is NOT here —
# OUTGATE1 retires it; a retired surface going quiet is the intended outcome,
# never a defect. Extend this set in the SAME commit that adds a new gated
# writer chokepoint (mirrors task_alarm's registry-follows-the-writer
# stance) — enforced, not advised: `run_guard_writer_gate_not_on_brief_test.py`
# section [4] reds on any member here that no emitter in `shared/scripts/`
# can actually produce. FIX ROUND 2 (review finding F-8, HIGH, 2026-09-08):
# `branded_pdf` REMOVED — round 1 added it on a misread signature and it has
# no emitter anywhere in this codebase, so its line could never clear. The
# regression that made it dark is recorded in the module docstring above.
GATED_WRITER_SURFACES = ("docx", "premium_html", "chat_email")

# The spec's own number (SPEC_SURFACES2 §10): a gated surface quiet this long
# is reported, never inferred sooner (machines sleep; a few hours of quiet is
# normal noise the way `task_alarm`'s "late" class is for scheduled tasks).
DARK_WINDOW_HOURS = 48

# Default tally window for `writer_gate_report` — the weekly maintenance
# cadence's own span, so a defect is reported the week it happens and not
# re-reported as fresh news every fire thereafter.
DEFAULT_WINDOW_DAYS = 7


def _now_utc(workspace_root=None) -> _dt.datetime:
    try:
        from trusted_now import trusted_now

        return trusted_now(workspace_root)
    except Exception:  # noqa: BLE001
        return _dt.datetime.now(_dt.timezone.utc)


def census_gate_ran(workspace_root, *, since_ts: Optional[float] = None) -> dict:
    """Every `gate_ran` event, aggregated per surface.

    Returns `{surface: {"pass": int, "fail": int, "no_result": int,
    "last_ts": iso str | None}}`. `last_ts` is the most recent event for that
    surface across the WHOLE history, `since_ts` unapplied — see module
    docstring. The pass/fail/no_result tallies count only events at or after
    `since_ts` (all history when `since_ts` is None). A surface is keyed by
    whatever string `data.surface` carries; an event with no surface name (or
    a non-string one) is folded under `"unknown"` rather than dropped, so a
    future emitter that forgets to name its surface still shows up somewhere
    instead of vanishing from the census.

    Reads through `events_io.load_all` (LEDGERFENCE1 — every full-history
    read goes through the shard-transparent reader); tolerates a missing or
    corrupt events file by returning `{}`, never raises."""
    try:
        from events_io import load_all
    except Exception:  # noqa: BLE001
        return {}

    census: dict[str, dict] = {}
    try:
        events = load_all(workspace_root)
    except Exception:  # noqa: BLE001
        return {}

    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != "gate_ran":
            continue
        data = ev.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        surface = data.get("surface")
        if not isinstance(surface, str) or not surface.strip():
            surface = "unknown"

        entry = census.setdefault(
            surface,
            {"pass": 0, "fail": 0, "no_result": 0, "last_ts": None, "_last_dt": None},
        )

        dt = event_dt(ev)
        if dt is not None and (entry["_last_dt"] is None or dt > entry["_last_dt"]):
            entry["_last_dt"] = dt
            entry["last_ts"] = dt.isoformat()

        if since_ts is not None and dt is not None and dt.timestamp() < since_ts:
            continue

        result = data.get("result")
        if result == "pass":
            entry["pass"] += 1
        elif result == "fail":
            entry["fail"] += 1
        else:
            # THE DEFECT CONDITION — see `defect_lines`. Removing this branch
            # (folding an unrecognized result into "pass" or dropping it) is
            # the acceptance's first named fence: a resultless gate_ran would
            # then pass silently. Kept as its own branch, never merged into
            # the else of a two-way if, so a mutation that removes the
            # `no_result` bucket entirely is a one-line diff to point at.
            entry["no_result"] += 1

    for entry in census.values():
        entry.pop("_last_dt", None)
    return census


def defect_lines(census: dict) -> list[str]:
    """One plain sentence per surface that recorded at least one resultless
    `gate_ran` THIS window. Empty census / all-clean census -> `[]`, never a
    padded all-clear (house convention)."""
    lines: list[str] = []
    for surface in sorted(census):
        n = (census.get(surface) or {}).get("no_result", 0)
        if not n:
            continue
        word = "time" if n == 1 else "times"
        lines.append(
            f"The {surface} output-quality gate ran {n} {word} this week "
            f"without recording a pass or fail — that's a defect in the gate "
            f"itself, not a comment on what it scanned. Worth mentioning to "
            f"whoever set up your Command Room."
        )
    return lines


def dark_writer_lines(
    census: dict, *, now: Optional[_dt.datetime] = None,
    window_hours: float = DARK_WINDOW_HOURS,
) -> list[str]:
    """One plain sentence per GATED_WRITER_SURFACES member that has fired
    before (a `last_ts` on record) but has been quiet `window_hours` or
    longer. A surface with no `last_ts` at all has never fired and is not
    alarmable — nothing to compare against (mirrors `task_alarm`'s
    never_authorized/late split for scheduled tasks). `turn_hook` is not in
    `GATED_WRITER_SURFACES` on purpose — it is retired, and a retired
    surface's silence is the intended state, never a finding."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    lines: list[str] = []
    for surface in GATED_WRITER_SURFACES:
        entry = census.get(surface)
        if not entry or not entry.get("last_ts"):
            continue  # never fired -- not this module's concern
        last_dt = parse_ts(entry["last_ts"])
        if last_dt is None:
            continue
        age_hours = (now - last_dt).total_seconds() / 3600.0
        if age_hours < window_hours:
            continue
        days = max(int(age_hours // 24), 1)
        word = "day" if days == 1 else "days"
        lines.append(
            f"The {surface} output-quality gate has not recorded anything in "
            f"{days} {word} — it may have gone quiet instead of clean; worth "
            f"a look from whoever set up your Command Room."
        )
    return lines


def writer_gate_report(
    workspace_root, *, now: Optional[_dt.datetime] = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict:
    """The one call the maintenance pass makes: `{"census": {...},
    "defects": [...], "dark": [...]}`. `census` carries the full per-surface
    tallies (for anyone that wants the numbers); `defects` + `dark` are the
    plain-English lines `report_lines` folds into the Monday note."""
    now = now or _now_utc(workspace_root)
    since_ts = (now - _dt.timedelta(days=window_days)).timestamp()
    census = census_gate_ran(workspace_root, since_ts=since_ts)
    return {
        "census": census,
        "defects": defect_lines(census),
        "dark": dark_writer_lines(census, now=now),
    }


def report_lines(
    workspace_root, *, now: Optional[_dt.datetime] = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[str]:
    """Convenience for a caller that only wants the rendered lines, worst
    first (dark surfaces — the gate is silent — before defects — the gate
    ran but forgot to say pass/fail). `[]` on a clean report."""
    rep = writer_gate_report(workspace_root, now=now, window_days=window_days)
    return list(rep["dark"]) + list(rep["defects"])


__all__ = [
    "GATED_WRITER_SURFACES",
    "DARK_WINDOW_HOURS",
    "DEFAULT_WINDOW_DAYS",
    "census_gate_ran",
    "defect_lines",
    "dark_writer_lines",
    "writer_gate_report",
    "report_lines",
]
