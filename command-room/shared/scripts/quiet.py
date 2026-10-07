#!/usr/bin/env python3
"""QUIET1 — correct when ignored (SPEC_QUIET1, built to M's rulings of 2026-09-03).

WHY THIS MODULE EXISTS
----------------------
Most seats on a firm-wide rollout will never open a triage widget. Today
silence makes the product LOUDER (91 items proposed three or more times on
the operator's own book; the held queue grows while nobody answers), and
every state waits on a click to stay true. This module is the one home for
the four things that make a seat that reads a five-line brief and does
nothing else stay CORRECT:

  * the PRESET ladder (D1) — `engaged` / `light` / `quiet`, read through ONE
    helper (`effective_preset`) that combines the configured posture with
    the silence step-down;
  * the INTERACTION GAUGE (D2) — a leg inside the daily `binding-gauge` job
    (never a job of its own) that measures how often a person answers and
    writes the verdict into the gauge artifact;
  * the STEP-DOWN (D3) — fourteen silent days step the effective preset DOWN
    one level; it NEVER steps up on its own; the next answer restores the
    configured preset; the brief narrates it exactly once;
  * the QUESTION BUDGET (D4) — five a week under `light`, across every
    asker, ranked by consequence; below the cut the default applies.

Plus the small readers the surfaces need: the plate cap (D5), the wrap's
"decided for you" / "still waiting" sections (D6), the return window (D7),
and the receipt counters (D9).

RULINGS ENCODED (M, 2026-09-03 — do not re-decide here)
  * Every CLIENT workspace starts `light`, existing ones included — the
    manifest `auto_apply` (`release_actions.stamp_light_preset`) writes it
    wherever no preset is stored, receipted and undoable by name. The
    operator's own workspace is stamped `engaged` EXPLICITLY (a command),
    never by hostname or any machine fact.
  * NO new question classes. The budget RANKS and CUTS questions the
    product already asks; it never invents one. POLICY1 chips are evidence
    on a row, not questions, and are named in `NOT_QUESTIONS` so no asker
    can count them.
  * Unanswered means the default applies after FOUR days —
    `DEFAULT_WINDOW_DAYS` is `commitment_policy.PROPOSAL_TTL_DAYS`, one
    number, never a second spelling.
  * No decisions block in the day-close (M's hold) — pinned in the guard
    suite against `end_of_day.RENDER_ORDER` / `COMPUTED_ONLY`.
  * Reversibility is the safety net: the preset stamp is a `brain_batch`
    with a registered reverser; the step-down is a MEASUREMENT (nothing on
    the plate moves) and restores itself on the next answer.

WHAT AN "ANSWER" IS
-------------------
A user gesture the ledger records as the person's own act: an Apply on a
widget (`apply_choices_applied`), a mute / snooze (`chat_dismissal`), a
"today is about" (`day_intent`), a closure the user confirmed
(`commitment_resolved` with `data.user_confirmed is True`), a bare `undo`
(`brain_change_undone`), a chat-typed verb recognised by its actor field
(`commitment_updated {pushed_by}` / `{question_by: user_verb}`,
`commitment_reassigned {reassigned_by}` — REVIEW_QUIET1 F-5), the person's
own preset stamp (an onboarding pick, `ask me more/less`, the operator's
command — any `commitment_preset` stamp whose origin is not the release
stamp, F-1) and the by-user restore `ask me more` writes on a stepped seat.
NOT an answer: anything a scheduled fire wrote, the bridge's release stamp,
a transcript-driven close (those carry a person id as `resolved_by` even
when no person tapped — the v5.27.0 attended test B4.4), a receipt, a
widget render. An OPENED SURFACE is a `pack_run` receipt fired
`manual` (the person typed the phrase) or a held-review render.

Read-only over the ledger except the three named writers: `stamp_preset`
(through `skill_config_writer.save_skill_config`), `write_posture_event`
and `submit_questions` (both through `event_gate.append_event`). stdlib +
sibling shared/scripts modules only. Names in docstrings and tests are the
Sample/Stone placeholder roster.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import commitment_policy as _policy  # noqa: E402
from commitment_policy import (  # noqa: E402
    PRESET_CONFIG_KEY, PRESET_ENGAGED, PRESET_LIGHT, PRESET_QUIET,
    PRESET_SKILL_KEY, PRESETS, PROPOSAL_TTL_DAYS,
)

# ---------------------------------------------------------------------------
# The ladder and the numbers (D1 / D3 / D4 / D5)
# ---------------------------------------------------------------------------

#: Left to right = asks less. `step_down` moves one place right and stops.
PRESET_LADDER = (PRESET_ENGAGED, PRESET_LIGHT, PRESET_QUIET)

#: M ruling 7 (2026-09-03): every client workspace starts `light`, existing
#: ones included. This is the value the manifest `auto_apply` STAMPS; the
#: shipped `commitment_policy.DEFAULT_PRESET` (engaged) stays the read-side
#: fallback for an unstored / malformed key, so a typo never silences every
#: question and nothing changes until the receipted stamp lands.
CLIENT_DEFAULT_PRESET = PRESET_LIGHT
#: The operator's own workspace — stamped explicitly, never detected.
OPERATOR_PRESET = PRESET_ENGAGED

#: D3 — silent days before the effective preset steps down one level.
STEP_DOWN_DAYS = 14
#: D2 — the interaction gauge's window.
INTERACTION_WINDOW_DAYS = 30
#: M ruling 5 — THE unanswered window, everywhere it appears. One home.
DEFAULT_WINDOW_DAYS = PROPOSAL_TTL_DAYS

#: D4 — questions per workspace per rolling week, by effective preset.
QUESTION_BUDGET = {PRESET_ENGAGED: 15, PRESET_LIGHT: 5, PRESET_QUIET: 2}
BUDGET_WINDOW_DAYS = 7

#: D5 — DO IT + CHASE rows the plate renders; beyond it the oldest of the
#: Later + No date rows park (CUT-D, 2026-09-06: overdue / this-week never).
PLATE_CAP = {PRESET_ENGAGED: None, PRESET_LIGHT: 40, PRESET_QUIET: 25}

#: D7 — the return summary opens the brief once the person has been away
#: this many days; at most this many lines, never a row list.
RETURN_SUMMARY_MIN_DAYS = 2
RETURN_SUMMARY_MAX_LINES = 3

# ---------------------------------------------------------------------------
# The askers (DD-2) — the ONLY sources a question may be submitted from
# ---------------------------------------------------------------------------

ASKER_MEETING_CARD = "meeting_card"     # ATTRIB1 card questions (attribution_doors)
ASKER_OVERDUE = "overdue_ask"           # OVERDUE1 morning asks (end_of_day.apply_overdue_ask)
ASKER_AGE_OUT = "age_out_offer"         # the age-out job's propose-first offer line
# FOLD1A (SPEC_FLOW1 Lane H) — End of Day's own questions submit through THIS
# door too, so they draw down the SAME weekly budget every other asker does
# rather than adding a separate allowance on top (M's ruling: "at most two
# of those questions from the same budget"). Candidates are the same
# needs_review_queue rows the Staff Meeting's "FROM YOUR MEETINGS" section
# reads (`eod_question_budget.eod_confirm_candidates`); this asker only
# governs WHICH of them the evening may ask about, never a new question
# shape. See `eod_question_budget.py`'s module docstring for the honest
# caveat: the Staff Meeting's own section is not YET wired through this same
# door (FOLD1B), so today this asker's only effect is a per-EVENING cap —
# it becomes "not on top of" the Staff Meeting for real the day that section
# submits here too.
ASKER_EOD_CONFIRM = "eod_confirm"
# SPEC SURFACES2_11c Lane 3 item 3 — the self-scored coaching question, on a
# seat that opened the coaching door. A SIXTH source is not a sixth allowance:
# it draws on this same weekly budget, and the evening's own cap counts the
# confirms FIRST — a confirm is an act waiting on the reader, a coaching
# question is not, so the confirms take the slots and the coach takes what is
# left of the two (ruling R-9). An observed seat never submits here at all.
ASKER_EOD_COACH = "eod_coach"
ASKERS = (ASKER_MEETING_CARD, ASKER_OVERDUE, ASKER_AGE_OUT, ASKER_EOD_CONFIRM,
          ASKER_EOD_COACH)
#: M ruling: a POLICY1 chip is evidence on the row, not a question. It is
#: named here so `submit_questions` refuses it by name, and so the pin that
#: keeps it out of the budget has a citation rather than a size check.
NOT_QUESTIONS = ("policy_chip",)

#: Consequence rank (D4): named counterparty + date > named counterparty >
#: date > neither. Lower sorts first.
CONSEQUENCE_ORDER = ("counterparty_and_date", "counterparty", "date", "neither")

# ---------------------------------------------------------------------------
# Ledger vocabulary this module writes / reads
# ---------------------------------------------------------------------------

#: The posture receipt — written by the gauge leg (step_down / restore) and
#: by the brief's narration marker (narrated). One type, three actions.
POSTURE_EVENT_TYPE = "interaction_posture"
POSTURE_STEP_DOWN = "step_down"
POSTURE_RESTORE = "restore"
POSTURE_NARRATED = "narrated"
#: REVIEW_CUTD R2 — the onboarding stamp found a posture already stored and
#: left it alone (an `engaged` seat re-running onboarding, or the bridge's
#: `light` landing first). Written ONCE per seat; a receipt of a skip, not a
#: move (`from_preset == to_preset`); carries the Q5 answer on `triggered_by`.
POSTURE_ONBOARDING_KEPT = "onboarding_kept"
POSTURE_ACTIONS = (POSTURE_STEP_DOWN, POSTURE_RESTORE, POSTURE_NARRATED,
                   POSTURE_ONBOARDING_KEPT)

#: The budget receipt — one per submission that asked or deferred anything.
BUDGET_EVENT_TYPE = "question_budget_spent"

#: The preset stamp's undo class (registered in `brain_undo.REVERSERS`) and
#: its batch prefix (listed by a bare `undo`).
PRESET_CHANGE_CLASS = "commitment_preset"
STAMP_BATCH_PREFIX = "qpr_"
SOURCE_SKILL = "quiet"

#: The gauge artifact's block (a compatible extension of binding_gauge.json —
#: `load_thread_knowledge._load_gauge` passes unknown keys through).
GAUGE_BLOCK_KEY = "interaction"

ANSWER_EVENT_TYPES = frozenset({
    "apply_choices_applied", "chat_dismissal", "day_intent",
})
#: REVIEW_QUIET1 F-5 — `chat_action` was a pre-registry FOSSIL the gate
#: refuses, so a chat-typed verb outside apply-choices never counted. The
#: user-verb writers are recognised by the ACTOR FIELD they stamp instead:
#: `commitment_updated {pushed_by}` (push to a date), `commitment_updated
#: {question_by: user_verb}` (not mine), `commitment_reassigned
#: {reassigned_by}`. All three are written only by the person's own verb.
USER_VERB_QUESTION_BY = "user_verb"     # commitment_state.QUESTION_BY_USER_VERB
#: REVIEW_QUIET1 F-1 — the person's own preset stamp (onboarding pick,
#: `ask me more/less`, the operator's command) is an answer; the bridge's
#: release stamp is a machine write and is not.
RELEASE_STAMP_ORIGIN = "release_default"
CONFIG_EVENT_TYPES = frozenset({"skill_first_run_configured", "skill_reconfigured"})
OPENED_SURFACE_TYPES = frozenset({"held_review_rendered"})

#: The one narration line (D3). Advertises only phrases that route.
STEP_DOWN_NARRATION = ("I've stopped asking — the next answer you give "
                       "brings the questions back, or say `ask me more`.")


class QuietInputError(ValueError):
    """A preset / asker / candidate outside the vocabulary."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts) -> Optional[datetime]:
    from event_time import parse_ts
    return parse_ts(ts)


def _iso(dt: Optional[datetime]) -> str:
    return dt.isoformat() if dt else ""


def _load_events(workspace_root) -> list:
    """Owner-tier read through `events_io` (the allowlisted shard reader);
    never a raw read of events.jsonl from this module."""
    import events_io
    events, _skipped = events_io.load_events_owner_scoped(workspace_root)
    return events


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _data(ev) -> dict:
    d = ev.get("data") if isinstance(ev, dict) else None
    return d if isinstance(d, dict) else {}


def _ts(ev) -> str:
    from event_time import event_time
    return event_time(ev)


def _check_preset(preset) -> str:
    p = str(preset or "").strip().lower()
    if p not in PRESETS:
        raise QuietInputError(f"preset {preset!r} is not one of {PRESETS}")
    return p


# ---------------------------------------------------------------------------
# The ladder (D1 / D3)
# ---------------------------------------------------------------------------

def step_down(preset: str) -> str:
    """One level quieter; `quiet` stays `quiet`. Never a step up."""
    p = _check_preset(preset)
    i = PRESET_LADDER.index(p)
    return PRESET_LADDER[min(i + 1, len(PRESET_LADDER) - 1)]


def is_quieter_or_equal(a: str, b: str) -> bool:
    """True when preset `a` asks no more than preset `b`."""
    return PRESET_LADDER.index(_check_preset(a)) >= PRESET_LADDER.index(_check_preset(b))


def configured_preset(workspace_root) -> str:
    """The STORED posture (`commitment-policy.preset`), with the shipped
    fallback — `commitment_policy.effective_preset` is the one reader of
    the key; this is a named alias so the two meanings never blur."""
    return _policy.effective_preset(workspace_root)


def stored_preset(workspace_root) -> Optional[str]:
    """The STORED posture with NO fallback: the valid `commitment-policy
    .preset` value on file, or None when nothing (or nothing valid) is
    stored. The read `release_actions.stamp_light_preset.plan` and the
    onboarding stamp (R2) share — "is a posture stored?" is a different
    question from "what posture applies?" (`configured_preset`)."""
    try:
        from skill_config_writer import load_skill_config
        stored = load_skill_config(workspace_root, PRESET_SKILL_KEY) or {}
        cfg = stored.get("config") if isinstance(stored, dict) else None
        val = (cfg or {}).get(PRESET_CONFIG_KEY) if isinstance(cfg, dict) else None
        val = str(val or "").strip().lower()
    except Exception:  # noqa: BLE001 — an unreadable config is "nothing stored"
        val = ""
    return val if val in PRESETS else None


def _gauge_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "binding_gauge.json"


def _read_gauge_block(workspace_root) -> dict:
    try:
        doc = json.loads(_gauge_path(workspace_root).read_text(encoding="utf-8"))
    except Exception:
        return {}
    blk = doc.get(GAUGE_BLOCK_KEY) if isinstance(doc, dict) else None
    return blk if isinstance(blk, dict) else {}


def effective_preset(workspace_root=None) -> str:
    """THE preset every asker, cap and budget reads (DD-1): the configured
    posture, stepped down one level when the gauge leg measured the silence
    (D3), and NEVER more engaged than what is configured — a stale or
    hand-edited artifact cannot raise the posture, only the person can, by
    answering (the leg restores) or by changing the stored key."""
    if workspace_root is None:
        return _policy.DEFAULT_PRESET
    configured = configured_preset(workspace_root)
    blk = _read_gauge_block(workspace_root)
    measured = str(blk.get("effective_preset") or "").strip().lower()
    if (blk.get("configured_preset") == configured and measured in PRESETS
            and is_quieter_or_equal(measured, configured)
            and measured in (configured, step_down(configured))):
        return measured
    return configured


# ---------------------------------------------------------------------------
# The interaction gauge (D2) and the step-down (D3)
# ---------------------------------------------------------------------------

def is_user_answer(ev) -> bool:
    """A gesture the ledger records as the PERSON's own act (module doc)."""
    t = ev.get("type") if isinstance(ev, dict) else None
    if t in ANSWER_EVENT_TYPES:
        return True
    d = _data(ev)
    if t == "commitment_resolved":
        return d.get("user_confirmed") is True
    if t == "brain_change_undone":
        return True
    if t == "commitment_updated":
        # F-5 — a chat-typed `push to [date]` or `not mine`
        return bool(d.get("pushed_by")) or d.get("question_by") == USER_VERB_QUESTION_BY
    if t == "commitment_reassigned":
        return bool(d.get("reassigned_by"))
    if t in CONFIG_EVENT_TYPES:
        # F-1 — the person's own preset stamp, never the release stamp
        return (d.get("brain_change_class") == PRESET_CHANGE_CLASS
                and d.get("origin") != RELEASE_STAMP_ORIGIN)
    if t == POSTURE_EVENT_TYPE:
        # F-1 — `ask me more` on a stepped seat restores on the spot; that
        # restore is the person's gesture and resets the silence clock.
        return d.get("action") == POSTURE_RESTORE and d.get("by_user") is True
    return False


def is_opened_surface(ev) -> bool:
    """The person typed a surface's phrase (a `manual` receipt) or opened
    the held review."""
    t = ev.get("type") if isinstance(ev, dict) else None
    if t in OPENED_SURFACE_TYPES:
        return True
    if t == "pack_run":
        try:
            from receipts import normalize_fired_via
            return normalize_fired_via(_data(ev).get("fired_via")) == "manual"
        except Exception:
            return str(_data(ev).get("fired_via") or "").lower() == "manual"
    return False


def _answer_key(ev) -> str:
    """One `undo` gesture reverses a whole batch — count the batch once."""
    if ev.get("type") == "brain_change_undone":
        return f"undo:{_data(ev).get('batch_ref') or _ts(ev)}"
    return f"{ev.get('type')}:{ev.get('seq')}"


def interaction_block(events: Iterable[dict], *, configured: str,
                      now_iso: Optional[str] = None,
                      previous: Optional[dict] = None) -> tuple[dict, Optional[dict]]:
    """PURE (D2 + D3): the gauge artifact's `interaction` block and the
    posture transition it implies, from the events and the previous block.

    Block: {configured_preset, effective_preset, interaction_30d,
            last_answer_ts, last_touch_ts, silence_origin_ts,
            stepped_down_at, stepped_down_from}. Days-since are derived at
            read time, so a quiet day does not churn the artifact.

    Transition: None, or {action: step_down|restore, from, to, days_silent,
    configured} — exactly when the effective preset moves relative to the
    PREVIOUS block for the SAME configured posture. A change of the stored
    key is the person's own act and is never a transition here.

    The origin of a silence is the last answer; for a seat that never
    answered, the newest preset stamp (REVIEW_QUIET1 F-2 — the clock starts
    when the posture was set: an existing client stamped `light` today gets
    fourteen days of `light` before anything steps, instead of `quiet` the
    same afternoon), else the previous block's origin, else the workspace's
    first event. No events at all → nothing is measured and the configured
    preset stands."""
    configured = _check_preset(configured)
    now = _parse(now_iso) or datetime.now(timezone.utc)
    window_start = now - timedelta(days=INTERACTION_WINDOW_DAYS)
    last_answer = None
    last_touch = None
    first_ts = None
    last_stamp = None
    seen: set = set()
    n_30d = 0
    for ev in events or []:
        dt = _parse(_ts(ev))
        if dt is None or dt > now:
            continue
        if first_ts is None or dt < first_ts:
            first_ts = dt
        if (ev.get("type") in CONFIG_EVENT_TYPES
                and _data(ev).get("brain_change_class") == PRESET_CHANGE_CLASS):
            if last_stamp is None or dt > last_stamp:
                last_stamp = dt
        if is_user_answer(ev):
            if last_answer is None or dt > last_answer:
                last_answer = dt
            if dt >= window_start:
                k = _answer_key(ev)
                if k not in seen:
                    seen.add(k)
                    n_30d += 1
        if is_user_answer(ev) or is_opened_surface(ev):
            if last_touch is None or dt > last_touch:
                last_touch = dt
    prev_origin = _parse((previous or {}).get("silence_origin_ts")) if isinstance(previous, dict) else None
    marks = [x for x in (last_answer, last_stamp) if x is not None]
    origin = max(marks) if marks else (prev_origin or first_ts)
    days_silent = (now - origin).days if origin else None
    stepped = days_silent is not None and days_silent >= STEP_DOWN_DAYS
    effective = step_down(configured) if stepped else configured
    # The fence, by construction and asserted: never more engaged than
    # configured, never more than one level quieter.
    assert effective in (configured, step_down(configured))

    prev = previous if isinstance(previous, dict) else {}
    same_posture = prev.get("configured_preset") == configured
    prev_eff = str(prev.get("effective_preset") or "").strip().lower()
    prev_stepped = same_posture and prev_eff in PRESETS and prev_eff != configured
    stepped_down_at = ""
    stepped_down_from = ""
    transition = None
    if effective != configured:
        stepped_down_from = configured
        if prev_stepped and prev_eff == effective:
            stepped_down_at = str(prev.get("stepped_down_at") or "") or _iso(now)
        else:
            stepped_down_at = _iso(now)
            transition = {"action": POSTURE_STEP_DOWN, "from": configured,
                          "to": effective, "days_silent": days_silent,
                          "configured": configured}
    elif prev_stepped:
        transition = {"action": POSTURE_RESTORE, "from": prev_eff,
                      "to": configured, "days_silent": days_silent,
                      "configured": configured}
    block = {
        "configured_preset": configured,
        "effective_preset": effective,
        "interaction_30d": n_30d,
        "last_answer_ts": _iso(last_answer),
        "last_touch_ts": _iso(last_touch),
        "silence_origin_ts": _iso(origin),
        "stepped_down_at": stepped_down_at,
        "stepped_down_from": stepped_down_from,
    }
    return block, transition


def days_since_last_answer(workspace_root, now_iso: Optional[str] = None,
                           *, block: Optional[dict] = None) -> Optional[int]:
    """Derived from the artifact block (or one handed in)."""
    blk = block if isinstance(block, dict) else _read_gauge_block(workspace_root)
    origin = _parse(blk.get("silence_origin_ts"))
    if origin is None:
        return None
    now = _parse(now_iso) or datetime.now(timezone.utc)
    return (now - origin).days


def gauge_leg(workspace_root, events: Iterable[dict], *, now_iso: Optional[str] = None,
              previous_doc: Optional[dict] = None) -> tuple[dict, Optional[dict]]:
    """The D2 leg as `binding_gauge` calls it: the block for THIS workspace's
    configured posture plus the transition (if any). Pure apart from the
    config read; the caller writes the artifact and the receipt."""
    prev = (previous_doc or {}).get(GAUGE_BLOCK_KEY) if isinstance(previous_doc, dict) else None
    return interaction_block(events, configured=configured_preset(workspace_root),
                             now_iso=now_iso, previous=prev)


def write_posture_event(workspace_root, transition: dict, *,
                        now_iso: Optional[str] = None, source_skill: str = SOURCE_SKILL,
                        narrated_seq: Optional[int] = None,
                        extra: Optional[dict] = None) -> dict:
    """The receipt of a posture move (step_down / restore) or of its one
    narration (narrated → `data.narrates_seq` points at the step_down row).
    Through the gate; returns the appended event."""
    from event_gate import append_event
    action = transition.get("action")
    if action not in POSTURE_ACTIONS:
        raise QuietInputError(f"posture action {action!r} is not one of {POSTURE_ACTIONS}")
    data = {"action": action,
            "from_preset": transition.get("from"),
            "to_preset": transition.get("to"),
            "configured_preset": transition.get("configured"),
            "days_silent": transition.get("days_silent")}
    if action == POSTURE_NARRATED:
        data["narrates_seq"] = narrated_seq
    if isinstance(extra, dict):
        data.update(extra)
    ev = {"type": POSTURE_EVENT_TYPE, "source_skill": source_skill, "data": data}
    if now_iso:
        ev["ts"] = now_iso
    out = append_event(_events_path(workspace_root), ev, holder="quiet")
    return out[0] if out else ev


def step_down_narration(workspace_root, *, now_iso: Optional[str] = None,
                        mark: bool = True, events: Optional[list] = None) -> str:
    """D3 — the ONE line the brief says when the product stopped asking.

    Returns the line the FIRST time it is asked for after a step_down that
    is still in force, and `""` every time after — the `narrated` marker
    (written here when `mark=True`) points at the step_down's seq, so a
    re-run, a second brief, or a second workspace-manager turn cannot say
    it twice. A restore, or a newer step_down, starts a fresh count."""
    evs = events if events is not None else _load_events(workspace_root)
    from event_seq import event_seq
    last_step = None
    narrated: set = set()
    for ev in evs:
        if ev.get("type") != POSTURE_EVENT_TYPE:
            continue
        d = _data(ev)
        if d.get("action") == POSTURE_STEP_DOWN:
            last_step = ev
        elif d.get("action") == POSTURE_RESTORE:
            last_step = None
        elif d.get("action") == POSTURE_NARRATED and d.get("narrates_seq") is not None:
            narrated.add(int(d["narrates_seq"]))
    if last_step is None:
        return ""
    seq = event_seq(last_step)
    if seq is None or seq in narrated:
        return ""
    if mark:
        write_posture_event(workspace_root, {"action": POSTURE_NARRATED,
                                             "from": _data(last_step).get("from_preset"),
                                             "to": _data(last_step).get("to_preset"),
                                             "configured": _data(last_step).get("configured_preset"),
                                             "days_silent": _data(last_step).get("days_silent")},
                            now_iso=now_iso, narrated_seq=seq)
    return STEP_DOWN_NARRATION


# ---------------------------------------------------------------------------
# The question budget (D4 / DD-2)
# ---------------------------------------------------------------------------

def consequence_of(candidate: dict) -> str:
    """The D4 rank of one candidate from its two facts."""
    cp = bool(candidate.get("has_counterparty"))
    dt = bool(candidate.get("has_date"))
    if cp and dt:
        return "counterparty_and_date"
    if cp:
        return "counterparty"
    if dt:
        return "date"
    return "neither"


def rank_questions(candidates: Iterable[dict]) -> list:
    """Pure: the candidates sorted by consequence (D4), then nearest date,
    then oldest capture, then id — each returned row gains `consequence`
    and `rank` (1-based). Input rows are not mutated."""
    rows = []
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        row = dict(c)
        row["consequence"] = consequence_of(row)
        rows.append(row)

    def _key(r):
        return (CONSEQUENCE_ORDER.index(r["consequence"]),
                str(r.get("due") or "9999-99-99"),
                str(r.get("ts") or "9999"),
                str(r.get("commitment_id") or r.get("id") or ""))
    rows.sort(key=_key)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def _asked_this_window(events: Iterable[dict], now: datetime) -> dict:
    """{(asker, id): ts} of every question asked inside the rolling week."""
    start = now - timedelta(days=BUDGET_WINDOW_DAYS)
    out: dict = {}
    for ev in events or []:
        if ev.get("type") != BUDGET_EVENT_TYPE:
            continue
        dt = _parse(_ts(ev))
        if dt is None or dt < start or dt > now:
            continue
        d = _data(ev)
        asker = d.get("asker")
        for cid in d.get("asked_ids") or []:
            out[(asker, str(cid))] = _ts(ev)
    return out


def _last_deferred(events: Iterable[dict], asker: str, now: datetime) -> set:
    """The deferred set on this asker's NEWEST budget receipt inside the
    rolling week (F-6), or an empty set."""
    start = now - timedelta(days=BUDGET_WINDOW_DAYS)
    best = None
    best_dt = None
    for ev in events or []:
        if ev.get("type") != BUDGET_EVENT_TYPE or _data(ev).get("asker") != asker:
            continue
        dt = _parse(_ts(ev))
        if dt is None or dt < start or dt > now:
            continue
        if best_dt is None or dt >= best_dt:
            best, best_dt = ev, dt
    return {str(x) for x in (_data(best).get("deferred_ids") or [])} if best else set()


def question_budget(workspace_root, now_iso: Optional[str] = None, *,
                    events: Optional[list] = None,
                    preset: Optional[str] = None) -> dict:
    """{"limit", "used", "remaining", "preset", "window_days"} for the
    rolling week ending now. `used` counts DISTINCT (asker, id) pairs
    asked in the window, so re-rendering a question you already asked
    spends nothing."""
    now = _parse(now_iso) or datetime.now(timezone.utc)
    evs = events if events is not None else _load_events(workspace_root)
    p = _check_preset(preset or effective_preset(workspace_root))
    limit = int(QUESTION_BUDGET[p])
    used = len(_asked_this_window(evs, now))
    return {"limit": limit, "used": used, "remaining": max(0, limit - used),
            "preset": p, "window_days": BUDGET_WINDOW_DAYS}


#: The floor TTL1's own ceiling carries, kept verbatim rather than re-sized
#: here: a ceiling of zero is indistinguishable from the surface being
#: switched off, and this number bounds the WHOLE Staff Meeting page (the
#: queue lane and every appended section), not just its questions.
STAFF_CEILING_FLOOR = 1


def staff_meeting_question_ceiling(workspace_root=None, *, now_iso=None,
                                   events: Optional[list] = None,
                                   preset: Optional[str] = None,
                                   page_bound: Optional[int] = None) -> int:
    """THE CEILING for one Staff Meeting page — what is LEFT of this week's
    question budget, never more than the shipped page bound.

    FOLD1-A fix round 2, REVIEW_FOLD1A R-1. TTL1 (SPEC_FLOW1 Lane G) published
    this ceiling reading `QUESTION_BUDGET[preset]` — the LIMIT. That was right
    while nothing else drew on the budget: the Staff Meeting was the only
    surface asking, so its limit and its remainder were the same number. FOLD1-A
    put a second asker on the same budget (the day-close's <= 2 pre-picked
    confirms, `eod_question_budget`), and a ceiling that reads the limit stops
    describing what is left: on a `light` seat the evening spends 2 and the
    Staff Meeting page still bounds itself at 5, so the week can show SEVEN
    questions on a five-question budget. M's ruling is that the evening's two
    are SPENT from the weekly five, never on top of them — which is a claim
    about BOTH surfaces, and it is enforceable only where the remainder is read.

    AT THE MERGE: `question_ttl.staff_meeting_question_ceiling` (TTL1's copy,
    which merges immediately before this lane) YIELDS TO THIS ONE — its body
    becomes a one-line delegation here. The name, the signature and the
    fallback are TTL1's, unchanged, so every caller it already has
    (`surface_drivers.build_staff_meeting_view`, `run_pagesnap_test`,
    `run_staffcut_test`, `run_ttl1_test`, G62 section 1) keeps working
    untouched; only the number inside changes, from the limit to the
    remainder.

    THE FLOOR IS TTL1's AND IT STAYS (`STAFF_CEILING_FLOOR`). A spent week
    therefore still shows one row rather than none — the honest residual, and
    it is recorded rather than hidden: worst case on a `light` seat is 5
    evening asks + 1 Staff Meeting row = 6, not 2 + 5 = 7. Driving it to zero
    would empty the WHOLE page, because this ceiling bounds the queue lane and
    every appended section too (G62 fence 11), and an empty Staff Meeting is a
    different act from a quiet one.

    ONE HALF IS STILL OWED, AND FOLD1B OWES IT. The Staff Meeting reads this
    ceiling as a page bound and does not call `submit_questions`, so it never
    DEBITS the budget — this makes the evening's spend shrink the Staff
    Meeting's page, and not the other way round. Wiring the Staff Meeting's own
    rows through the shared door is FOLD1B's scope, named in
    `eod_question_budget.py`'s docstring; until it lands the arithmetic is
    enforced on one side only and this record says so.

    THE FALLBACKS ARE TTL1's, IN BOTH DIRECTIONS, DELIBERATELY. A workspace
    whose PRESET cannot be resolved falls back to the shipped page bound, and
    a workspace whose LEDGER cannot be read falls back to that preset's LIMIT
    — which is exactly the number TTL1's body returned. So every call that
    cannot reach a real book (`staff_meeting_question_ceiling(None)`, which
    `run_pagesnap_test` and `run_staffcut_test` both make) answers what it
    always answered, and only a call that CAN read the week's spend sees the
    remainder. A ceiling that cannot read must not silence a surface.
    """
    if page_bound is None:
        try:
            from proposal_digests import STAFF_PAGE_ROW_CAP
            page_bound = int(STAFF_PAGE_ROW_CAP)
        except Exception:  # pragma: no cover — a ceiling that cannot read
            return STAFF_CEILING_FLOOR
    try:
        p = _check_preset(preset or effective_preset(workspace_root))
        remaining = int(QUESTION_BUDGET[p])
    except Exception:  # pragma: no cover — no preset: the shipped page bound
        return int(page_bound)
    try:
        remaining = int(question_budget(workspace_root, now_iso, events=events,
                                        preset=p)["remaining"])
    except Exception:
        pass  # no readable ledger: the LIMIT, exactly as TTL1's body returned
    return max(STAFF_CEILING_FLOOR, min(int(page_bound), remaining))


def submit_questions(workspace_root, asker: str, candidates: Iterable[dict], *,
                     now_iso: Optional[str] = None, apply: bool = True,
                     events: Optional[list] = None,
                     preset: Optional[str] = None) -> dict:
    """DD-2 — the one door every asker submits through.

    Candidates: {"commitment_id" (or "id"), "has_counterparty", "has_date",
    "due"?, "ts"?, ...anything the asker needs back}. Returns
    {"render": [rows to ask, ranked], "deferred": [rows cut by the budget],
     "already_asked": [rows asked earlier this week — render free],
     "budget": <question_budget before this submission>, "n_spent"}.

    Below the cut takes the default: a deferred row is simply not asked —
    each asker's own default machinery (the card's lapse, the overdue
    rest, the age-out apply) does what it would have done unanswered.
    `apply=True` writes ONE `question_budget_spent` receipt when anything
    was asked or deferred (drop-empty). A candidate whose `kind` is in
    `NOT_QUESTIONS` raises — a chip is not a question (M ruling)."""
    if asker not in ASKERS:
        raise QuietInputError(f"asker {asker!r} is not one of {ASKERS}")
    now = _parse(now_iso) or datetime.now(timezone.utc)
    evs = events if events is not None else _load_events(workspace_root)
    ranked = rank_questions(candidates)
    for r in ranked:
        if str(r.get("kind") or "") in NOT_QUESTIONS:
            raise QuietInputError(
                f"{r.get('kind')} is not a question and never spends the budget "
                f"(M ruling 2026-09-03: a POLICY1 chip is evidence on the row)")
    budget = question_budget(workspace_root, _iso(now), events=evs, preset=preset)
    asked_before = _asked_this_window(evs, now)
    render, deferred, free = [], [], []
    remaining = budget["remaining"]
    seen: set = set()
    for r in ranked:
        cid = str(r.get("commitment_id") or r.get("id") or "")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        if (asker, cid) in asked_before:
            free.append(r)
            render.append(r)
            continue
        if remaining > 0:
            remaining -= 1
            render.append(r)
        else:
            deferred.append(r)
    spent = [str(r.get("commitment_id") or r.get("id")) for r in render if r not in free]
    out = {"render": render, "deferred": deferred, "already_asked": free,
           "budget": budget, "n_spent": len(spent)}
    deferred_ids = [str(r.get("commitment_id") or r.get("id")) for r in deferred]
    # REVIEW_QUIET1 F-6 — a spent week re-deferring the same rows every
    # morning is not news: write the receipt only when something was newly
    # asked, or when the deferred set differs from this asker's last receipt
    # in the window (so "N took their default" still counts each row once).
    repeat = (not spent and deferred_ids
              and set(deferred_ids) == _last_deferred(evs, asker, now))
    if apply and (spent or deferred) and not repeat:
        from event_gate import append_event
        ev = {"type": BUDGET_EVENT_TYPE, "source_skill": SOURCE_SKILL,
              "data": {"asker": asker, "preset": budget["preset"],
                       "limit": budget["limit"], "used_before": budget["used"],
                       "n_submitted": len(ranked), "n_asked": len(spent),
                       "n_deferred": len(deferred),
                       "asked_ids": spent,
                       "deferred_ids": deferred_ids}}
        if now_iso:
            ev["ts"] = _iso(now)
        append_event(_events_path(workspace_root), ev, holder="quiet")
    return out


# ---------------------------------------------------------------------------
# The plate cap (D5)
# ---------------------------------------------------------------------------

def plate_cap(preset: str) -> Optional[int]:
    """DO IT + CHASE rows the plate renders under `preset`; None = no cap."""
    return PLATE_CAP[_check_preset(preset)]


# ---------------------------------------------------------------------------
# The return window (D7) and the wrap / receipt readers (D6 / D9)
# ---------------------------------------------------------------------------

def window_since_last_touch(workspace_root, now_iso: Optional[str] = None, *,
                            events: Optional[list] = None) -> dict:
    """{"since_ts", "days", "kind"} — the last answer or opened surface
    (`kind`: answer | opened | none). With no touch ever, `since_ts` is the
    first event's ts and `kind` is `none`."""
    now = _parse(now_iso) or datetime.now(timezone.utc)
    evs = events if events is not None else _load_events(workspace_root)
    last = None
    kind = "none"
    first = None
    for ev in evs:
        dt = _parse(_ts(ev))
        if dt is None or dt > now:
            continue
        if first is None or dt < first:
            first = dt
        if is_user_answer(ev):
            if last is None or dt >= last:
                last, kind = dt, "answer"
        elif is_opened_surface(ev):
            if last is None or dt > last:
                last, kind = dt, "opened"
    origin = last or first
    return {"since_ts": _iso(origin), "days": (now - origin).days if origin else None,
            "kind": kind}


def brief_window(workspace_root, default_since_ts: str,
                 now_iso: Optional[str] = None, *,
                 events: Optional[list] = None) -> tuple[str, Optional[dict]]:
    """D7 — the CHANGED window the brief should use: the default (since the
    last brief) while the person is around; from the last touch once they
    have been away `RETURN_SUMMARY_MIN_DAYS` or more. Returns
    (since_ts, return_meta|None) where meta = {"days", "since_ts", "kind"}."""
    w = window_since_last_touch(workspace_root, now_iso, events=events)
    default_dt = _parse(default_since_ts)
    touch_dt = _parse(w.get("since_ts"))
    days = w.get("days")
    # A workspace nobody has EVER touched is not "away" — reading the brief
    # leaves no trace, so without one recorded touch the claim "while you
    # were out" would be made every morning forever. The default window
    # stands until the first answer or opened surface.
    if (w.get("kind") != "none" and touch_dt is not None and days is not None
            and days >= RETURN_SUMMARY_MIN_DAYS
            and (default_dt is None or touch_dt < default_dt)):
        return w["since_ts"], {"days": days, "since_ts": w["since_ts"], "kind": w["kind"]}
    return default_since_ts, None


PARK_HINT_VALUE = "parked"   # plate_view.STATUS_HINT_PARKED — the DD-6 row hint


def _parked_in_window(events: Iterable[dict], since: Optional[datetime],
                      now: datetime) -> tuple[int, list]:
    """REVIEW_QUIET1 F-3 — rows PARKED inside (since, now]: the written
    `commitment_updated {status_hint: parked}` hint (POLICY1 DD-6's row
    shape, the one `plate_view._assign` already reads). Counted from the
    ledger because `change_feed` has no `parked` category on this branch.
    At merge POLICY1-B adds its `commitment_park` hint event — read that
    name here too (keep both). Returns (count, refs)."""
    n = 0
    refs = []
    for ev in events or []:
        if ev.get("type") != "commitment_updated":
            continue
        d = _data(ev)
        if str(d.get("status_hint") or "").strip().lower() != PARK_HINT_VALUE:
            continue
        dt = _parse(_ts(ev))
        if dt is None or dt > now or (since is not None and dt <= since):
            continue
        n += 1
        refs.append(ev.get("seq"))
    return n, refs


def _decided_counts(counts: dict, n_parked: int = 0) -> dict:
    """The three numbers the return summary and the wrap agree on: two from
    a `change_feed.changes_since` counts dict, `parked` from the written
    park hints (`_parked_in_window`)."""
    c = counts or {}
    closed = int(c.get("closed_from_meetings") or 0) + int(c.get("closed_from_sent") or 0)
    lapsed = int(c.get("unconfirmed_expired") or 0)
    return {"closed_on_evidence": closed, "lapsed": lapsed, "parked": int(n_parked or 0)}


def _open_questions(workspace_root, events: Iterable[dict], since: datetime,
                    now: datetime) -> tuple[int, int]:
    """(asked and still open, deferred) inside [since, now] — from the
    budget receipts joined against the live open set."""
    from cru_match import _commitment_id, load_open_commitments
    evs = list(events or [])
    open_ids = {_commitment_id(r) for r in
                load_open_commitments(_events_path(workspace_root), events=evs)}
    asked: set = set()
    deferred: set = set()
    for ev in evs:
        if ev.get("type") != BUDGET_EVENT_TYPE:
            continue
        dt = _parse(_ts(ev))
        if dt is None or dt <= since or dt > now:
            continue
        d = _data(ev)
        asked.update(str(x) for x in (d.get("asked_ids") or []))
        deferred.update(str(x) for x in (d.get("deferred_ids") or []))
    return len(asked & open_ids), len(deferred & open_ids)


def return_summary(workspace_root, now_iso: Optional[str] = None, *,
                   events: Optional[list] = None) -> Optional[dict]:
    """D7 — "While you were out (19 days): 14 closed on evidence, 9 parked,
    3 need you." plus at most three feed lines. None while the person is
    around (away < RETURN_SUMMARY_MIN_DAYS). Never a row list."""
    from change_feed import changes_since
    now = _parse(now_iso) or datetime.now(timezone.utc)
    evs = events if events is not None else _load_events(workspace_root)
    w = window_since_last_touch(workspace_root, _iso(now), events=evs)
    days = w.get("days")
    if (w.get("kind") == "none" or days is None or days < RETURN_SUMMARY_MIN_DAYS
            or not w.get("since_ts")):
        return None
    feed = changes_since(workspace_root, w["since_ts"], now_iso=_iso(now),
                         max_lines=RETURN_SUMMARY_MAX_LINES)
    n_parked, _ = _parked_in_window(evs, _parse(w["since_ts"]), now)
    counts = _decided_counts(feed.get("counts"), n_parked)
    need_you, _deferred = _open_questions(workspace_root, evs, _parse(w["since_ts"]), now)
    counts["need_you"] = need_you
    parts = [f"{counts['closed_on_evidence']} closed on evidence"]
    if counts["parked"]:
        parts.append(f"{counts['parked']} parked")
    if counts["lapsed"]:
        parts.append(f"{counts['lapsed']} lapsed unanswered")
    parts.append(f"{need_you} need you")
    header = f"While you were out ({days} days): " + ", ".join(parts) + "."
    lines = [l.get("text", "") for l in (feed.get("lines") or []) if l.get("text")]
    return {"header": header, "lines": lines[:RETURN_SUMMARY_MAX_LINES],
            "counts": counts, "days": days, "since_ts": w["since_ts"]}


#: The feed categories the "Decided for you" block owns (weekly-recap 8b
#: prints the rest) and the batch classes those lines already narrate.
DECIDED_FEED_CATEGORIES = frozenset({
    "closed_from_meetings", "closed_from_sent", "unconfirmed_expired",
    "proposals_retracted", "orgs_promoted",
    # MF-11c-1 — a job's aged-out pass was decided for the reader; the
    # customer's own amnesty (`cleared_amnesty`) was not, and stays out.
    "let_go_backlog",
})
#: WRAPSTAFF1 4.3 — how many NAMED acts the wrap lists before it points
#: at the rest. The cap lives here, so the skill has no number of its own.
#:
#: FIVE, by M's ruling of 2026-09-17 (fix round 2), and it is deliberately
#: NOT `plate_view.WRAP_PARKED_CAP`. Fix round 1 tied the two together as an
#: identity; the ruling untied them, because the two blocks are not the same
#: shape of list. The Parked block lists rows the reader may still act on and
#: ten of them earn their space; this block lists acts already taken, which
#: are evidence for the counts above them rather than work — five name enough
#: for the counts to be checkable and keep the wrap short. On M's own week
#: this is the difference between 26 lines and 21 in one block (base was 14).
DECIDED_NAMED_ACT_CAP = 5

#: The one count line for the acts beyond the cap. No phrase: `show
#: decided` does not route, and a surface never prints a door that is
#: not there.
DECIDED_MORE_LINE = "- …and {n} more decided for you."

#: WRAPSTAFF1 fix round 3 (review N-1) — how wide an act's ledger seq is
#: zero-padded before the cap ranks on it. `plate_view.importance_key` ranks
#: age by complementing the string digit for digit (`_neg_ts`), which orders
#: numbers only when they are the same width: unpadded, seq 9 would outrank
#: seq 15955. Twelve digits is a trillion acts — wider than any book grows —
#: and a seq that somehow outgrew it pads to itself rather than raising.
ACT_SORT_SEQ_WIDTH = 12
FEED_NARRATED_CLASSES = frozenset({
    "commitment_close", "commitment_merge", "org_promotion", "person_link",
    "person_org_creation_structured_fact", "entity_fact_structured",
    "chat_dismissal",
    # TTL1 fix round 1 (reviewer F-4) — CITED WIDENING. A question-expiry
    # run already has a line of its own in this block
    # (`question_ttl.decided_for_you_lines`, added below), so the generic
    # batch label printed a SECOND line for the identical `qex_` batch and
    # the wrap offered to reverse one run twice. This set exists for exactly
    # that: a batch line is printed only for a class no line here already
    # narrates. The engine's own sentence is the plainer of the two ("let go
    # a question nobody answered (×5)" beside "Answered 5 questions nobody
    # had got to"), and it carries the counts and the way back.
    "brain_proposal_expiry",
})


def _act_sort_ts(act: dict) -> str:
    """WHERE THIS ACT REALLY SITS IN TIME, as a string `importance_key` can
    rank (review N-1, fix round 3).

    `act["date"]` is DISPLAY WORDS, not a timestamp: `change_feed._act_date`
    returns "Sep 9", "Sep 15", "date not on record". Ranked, that is
    month-alphabetical, and inside one month a one-digit day sorts ahead of
    every two-digit one — so on M's own week the five acts the wrap named
    were the one with money in it plus the four OLDEST of seven days, and
    nothing from the last day of the window survived the cap.

    The ledger `seq` IS append order, so a zero-padded seq gives newest-first
    exactly, through the same `_neg_ts` complement every other cap on this
    tree ranks age by. An act whose ref cannot be read pads to all zeros and
    therefore sorts LAST: an act whose place we cannot tell is not the act to
    promote into five named lines.
    """
    return str(act.get("ref") or "").zfill(ACT_SORT_SEQ_WIDTH)


def _named_act_lines(workspace_root, since_iso: str, now_iso: str) -> list:
    """The NAMED acts the wrap lists under "Decided for you", capped and
    ranked (review F-3 / the coordinator's ruling, 2026-09-17).

    THE ORDERING IS THE PARKED BLOCK'S; THE CAP IS THIS BLOCK'S OWN. The row
    acts are ranked by `plate_view.importance_key` — the same function the
    Parked list ranks by — so the same idea of "important" decides what a
    reader sees in both blocks of one post. Producer order is not importance:
    it was giving the reader whichever acts the ledger happened to hand back
    first. The COUNT is `DECIDED_NAMED_ACT_CAP` = five, M's ruling of
    2026-09-17, and it is not the Parked cap: see the constant for why the
    fix-round-1 identity was untied. "Newest" is the act's LEDGER SEQ, never
    the date the line prints (`_act_sort_ts`, review N-1).

    A BATCH ACT IS NEVER CUT AND ALWAYS LEADS. It carries its own count and
    stands for a pile the reader can see no other way; on the 09-15 window
    there were 92 acts and a flat cap had already dropped the 88 once.

    TITLES PRINT AS THE BOARD PRINTS THEM. `plate_view.row_title` is the
    board's own preparation, reused rather than re-implemented — one
    cleaner, so the same row cannot read two ways on two surfaces. Fix round
    3 (review N-2) made that true rather than decorative: the producer's own
    fallback (`change_feed._act_title`) now ends in the SAME helper, so a row
    with no title on record reads `(untitled — needs repair)` here and on the
    board, instead of one sentence on the wrap and another on the plate. The
    call below is what keeps the wrap honest about whatever the producer
    hands it — it does not trust its input to be clean.

    Read-only; never raises into the wrap.
    """
    try:
        from change_feed import (BATCH_ACT_DOORS, NAMED_ACT_LINE,
                                 decided_for_you)
        from plate_view import importance_key, row_title, says_money
    except Exception:  # pragma: no cover — the wrap must survive
        return []
    try:
        named = decided_for_you(workspace_root, since_iso, now_iso=now_iso)
        acts = list(named.get("acts") or [])
        doors = set(BATCH_ACT_DOORS or ())
        batch_acts = [a for a in acts if a.get("category") in doors]
        row_acts = [a for a in acts if a.get("category") not in doors]

        def _rank(a: dict) -> tuple:
            # An act is not a plate row: it has no horizon and no
            # counterparty, so those two terms are constant and the ranking
            # falls to money-first, then newest, then the act's own ref —
            # which is exactly `importance_key`'s tail on such a row. Shaped
            # rather than re-implemented so the two blocks cannot drift.
            #
            # THE AGE TERM IS THE SEQ, NEVER THE PRINTED DATE (review N-1):
            # `a["date"]` is the words the line shows a reader, and ranking
            # on those is month-alphabetical. `_act_sort_ts` says why.
            # This call is DEFENSIVE ONLY and carries no pin: its one reader
            # is `says_money`, whose regex gives the same answer cleaned or
            # raw, so no input can tell the two apart (review M-2). It stays
            # so the ranking and the rendering below cannot drift apart.
            title = row_title(a.get("title"))
            return importance_key({"horizon": None, "client": False,
                                   "money": says_money(title),
                                   "ts": _act_sort_ts(a),
                                   "id": str(a.get("ref") or "")})

        row_acts.sort(key=_rank)
        kept = batch_acts + row_acts[:DECIDED_NAMED_ACT_CAP]
        lines = []
        for a in kept:
            lines.append("- " + NAMED_ACT_LINE.format(
                title=row_title(a.get("title")),
                door=a.get("door") or "",
                date=a.get("date") or ""))
        n_more = max(0, len(row_acts) - DECIDED_NAMED_ACT_CAP)
        if n_more:
            # ONE count line for the rest, and no phrase attached: `show
            # decided` does not route on this tree, and a surface never
            # prints a phrase the router cannot answer (the `show waiting`
            # dead end, SURFACEFIX1 5.5). Give it a route and the pointer
            # belongs here.
            lines.append(DECIDED_MORE_LINE.format(n=n_more))
        return lines
    except Exception:  # pragma: no cover — the wrap must survive
        return []


def wrap_sections(workspace_root, since_iso: str, now_iso: Optional[str] = None, *,
                  events: Optional[list] = None) -> dict:
    """D6 — the Friday wrap's two sections, composed here so the skill
    renders them VERBATIM (the `plate_view.wrap_cut` posture).

    decided_for_you: what the brain settled in the window — one line per
      feed category (each carrying its own `undo` phrase, as the feed
      wrote it) and one line per undoable batch the window minted
      (`brain_undo.recent_auto_batches`, labelled), so a group is one
      `undo` away. POLICY1-B's per-group lines add beside these at merge.
    still_waiting: questions asked in the window and still open, and how
      many the budget deferred to their defaults.
    Both drop-empty: `text` is "" when there is nothing to say."""
    from change_feed import changes_since
    now = _parse(now_iso) or datetime.now(timezone.utc)
    evs = events if events is not None else _load_events(workspace_root)
    feed = changes_since(workspace_root, since_iso, now_iso=_iso(now))
    n_parked, park_refs = _parked_in_window(evs, _parse(since_iso), now)
    counts = _decided_counts(feed.get("counts"), n_parked)
    lines = [l.get("text", "") for l in (feed.get("lines") or [])
             if l.get("text") and l.get("category") in DECIDED_FEED_CATEGORIES]
    if n_parked:
        lines.append(f"Parked {n_parked} {'item' if n_parked == 1 else 'items'} "
                     f"with the reason on the row — they rest under Parked on your plate.")
    batches = []
    try:
        from brain_undo import recent_auto_batches
        since_dt = _parse(since_iso)
        for b in recent_auto_batches(workspace_root, days=3660, now_iso=_iso(now)):
            bdt = _parse(b.get("ts"))
            if since_dt is not None and bdt is not None and bdt <= since_dt:
                continue
            b = dict(b)
            b["classes"] = _batch_classes(workspace_root, b.get("batch_ref") or {}, evs)
            batches.append(b)
    except Exception:
        batches = []
    # REVIEW_QUIET1 F-4 — ONE line per act. The feed already narrates the
    # classes in FEED_NARRATED_CLASSES (each feed line carries its undo), so
    # a batch line is printed only for a class the feed has no line for
    # (today: the preset stamp). This function is the single owner of the
    # block; weekly-recap 8b prints the feed's OTHER categories.
    #
    # TTL1 fix round 1 (reviewer F-4) — AND ONE LINE PER RUN. A question
    # expiry writes THREE change classes under one `qex_` batch (the card
    # default's reassign and confirm, and the let-go's tombstone), so the
    # class test alone still printed a generic batch label beside the
    # engine's own sentence and the wrap offered to reverse the same run
    # twice. The batch PREFIX answers it exactly: every `qex_` run is
    # already reported, once, by `decided_for_you_lines` below.
    _expiry_prefix = "qex_"
    try:
        from question_ttl import EXPIRY_BATCH_PREFIX as _expiry_prefix
    except Exception:  # pragma: no cover — the wrap must survive
        pass
    batch_lines = [f"- {b['label']} — say `undo` and pick it from the list to reverse"
                   for b in batches
                   if not (set(b.get("classes") or []) & FEED_NARRATED_CLASSES)
                   and not str((b.get("batch_ref") or {}).get("batch_id") or "")
                   .startswith(_expiry_prefix)
                   ][:RETURN_SUMMARY_MAX_LINES * 2]
    # TTL1 (SPEC_FLOW1 Lane G) — the questions that answered themselves.
    #
    # An expiry is the clearest possible case of "decided for you": nobody
    # answered, so the product took the default. It belongs in THIS block and
    # nowhere else, it is one line per run rather than one per question, and
    # each line carries the way back. The engine's own reader does the
    # already-reversed fold (a run a later `undo` took back is not news),
    # exactly as ATTRIB2 made `change_feed` do for the lines above.
    try:
        from question_ttl import decided_for_you_lines
        expiry_lines = decided_for_you_lines(workspace_root, since_iso,
                                             now_iso=_iso(now), events=evs)
    except Exception:  # pragma: no cover — the wrap must survive
        expiry_lines = []
    batch_lines = batch_lines + [f"- {t}" for t in expiry_lines]
    # WRAPSTAFF1 4.3 — ONE PRODUCER FOR "DECIDED FOR YOU", IN CODE.
    #
    # The named form (`change_feed.decided_for_you` — `title — door, date`
    # per act, plus one line per BATCH act off the job's own receipt) used to
    # be run by `weekly-recap/SKILL.md` as a SECOND python block beside this
    # helper's return. On 09-15 the four-day door retracted 88 superseded
    # suggestions in one quiet batch (receipt 17592, `n_retracted 88`) and
    # the wrap said nothing about it (ATTENDED_TEST_v5.31.0 B2.7): the
    # machinery was complete and correct, and a producer a skill has to
    # REMEMBER to run is a producer that does not run.
    #
    # So this helper composes the whole block, batch acts included, and the
    # skill renders this one return verbatim. The feed counts above stay
    # where they are — they are the picture — and these are the evidence.
    #
    # The "Withdrew N unanswered questions" feed line is a DIFFERENT act (the
    # chip rail's per-row dismissals) and may stand beside the batch line;
    # the two never count the same rows, and `run_wrapstaff1_test` [3] pins
    # that. `question_ttl.decided_for_you_lines` renders no retraction
    # sentence at all (MF-6), so the batch line cannot double either.
    # A BATCH ACT IS NEVER CUT BY THE CAP, and it leads.
    #
    # The cap is applied HERE rather than by passing `max_acts`, because the
    # producer appends the batch acts after the row acts: on the 09-15 window
    # there were 92 named acts, the ten-line cap took the first ten, and the
    # 88 fell off the page again — the same silence, through a different
    # door. A batch act carries its own count and stands for a pile nobody
    # can see any other way, so it is the line the reader most needs; the row
    # acts are the evidence behind the counts above and take their chances
    # with the cap, as they always have.
    named_lines = _named_act_lines(workspace_root, since_iso, _iso(now))
    decided_text = ""
    if lines or batch_lines or named_lines:
        groups = [g for g in ("\n".join(f"- {t}" for t in lines),
                              "\n".join(batch_lines),
                              "\n".join(named_lines)) if g]
        decided_text = ("## Decided for you this week\n"
                        + "\n".join(groups)).rstrip()
    n_open, n_deferred = _open_questions(workspace_root, evs, _parse(since_iso) or now, now)
    waiting = ""
    if n_open or n_deferred:
        bits = []
        if n_open:
            bits.append(f"{n_open} {'question' if n_open == 1 else 'questions'} "
                        f"still waiting on you")
        if n_deferred:
            bits.append(f"{n_deferred} took {'its' if n_deferred == 1 else 'their'} "
                        f"default rather than ask")
        waiting = "Still waiting: " + "; ".join(bits) + "."
    return {"decided_for_you": {"text": decided_text, "lines": lines,
                                "named_lines": named_lines,
                                "batches": batches, "counts": counts},
            "still_waiting": {"text": waiting, "n_open_questions": n_open,
                              "n_deferred": n_deferred}}


def _batch_classes(workspace_root, batch_ref: dict, events: Iterable[dict]) -> set:
    """The change classes one listed batch carries (the listing drops them)."""
    if batch_ref.get("kind") == "sent_reconcile":
        return {"commitment_close"}
    bid = batch_ref.get("batch_id")
    out: set = set()
    for ev in events or []:
        d = _data(ev)
        if bid and d.get("brain_batch_id") == bid and d.get("brain_change_class"):
            out.add(str(d["brain_change_class"]))
    return out


def receipt_counters(events: Iterable[dict], start: datetime, end: datetime) -> dict:
    """D9 — {"closed_untouched", "questions_asked"} over [start, end):
    closures the brain wrote on its own (a transcript close through the
    `match` door, a sent-mail close, a lapse, an age-out — anything with a
    `brain_batch_id` or the sent rail's `resolved_by`) and the questions
    the budget receipts say were asked."""
    closed = 0
    asked = 0
    # value_receipt hands NAIVE-UTC bounds; the ledger parses tz-aware.
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    for ev in events or []:
        dt = _parse(_ts(ev))
        if dt is None or dt < start or dt >= end:
            continue
        t = ev.get("type")
        d = _data(ev)
        if t == "commitment_resolved":
            if d.get("user_confirmed") is True:
                continue
            if d.get("brain_batch_id") or d.get("resolved_by") == "sent_reconcile":
                closed += 1
        elif t == BUDGET_EVENT_TYPE:
            asked += int(d.get("n_asked") or 0)
    return {"closed_untouched": closed, "questions_asked": asked}


# ---------------------------------------------------------------------------
# The preset writers: the stamp (D1 auto_apply) and `ask me more/less`
# ---------------------------------------------------------------------------

def mint_stamp_batch_id(now_iso: Optional[str] = None) -> str:
    import secrets
    dt = _parse(now_iso) or datetime.now(timezone.utc)
    return f"{STAMP_BATCH_PREFIX}{dt.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"


def stamp_preset(workspace_root, preset: str, *, origin: str,
                 triggered_by: str, now_iso: Optional[str] = None) -> dict:
    """Write `commitment-policy.preset` through the typed writer, stamped as
    ONE `brain_batch` (`qpr_…`, class `commitment_preset`) so a bare `undo`
    lists it and the registered reverser puts the previous value back
    exactly (or clears the key when there was none). Idempotent: the same
    value already stored is a no-op with no receipt.

    Returns {ran, preset, prev_preset, batch_id}."""
    from skill_config_writer import load_skill_config, save_skill_config
    p = _check_preset(preset)
    stored = load_skill_config(workspace_root, PRESET_SKILL_KEY) or {}
    prev_cfg = stored.get("config") if isinstance(stored, dict) else None
    prev_cfg = prev_cfg if isinstance(prev_cfg, dict) else None
    prev = str((prev_cfg or {}).get(PRESET_CONFIG_KEY) or "").strip().lower()
    if prev == p:
        return {"ran": False, "preset": p, "prev_preset": prev, "batch_id": None}
    batch_id = mint_stamp_batch_id(now_iso)
    # CUT-A — the store carries a second key now (the transcript
    # closing-on-evidence switch); a preset stamp moves the preset and
    # NOTHING else. `prev_config` on the batch is still the whole dict, so
    # the reverser puts the whole dict back exactly.
    cfg = dict(prev_cfg or {})
    cfg[PRESET_CONFIG_KEY] = p
    save_skill_config(
        workspace_root, PRESET_SKILL_KEY, cfg,
        is_reconfigure=bool(prev_cfg), origin=origin,
        event_extra={"brain_batch_id": batch_id,
                     "brain_change_class": PRESET_CHANGE_CLASS,
                     "skill_name": PRESET_SKILL_KEY,
                     "prev_config_present": prev_cfg is not None,
                     "prev_config": prev_cfg,
                     "triggered_by": triggered_by},
        event_ts=_iso(_parse(now_iso)) if now_iso else None)
    return {"ran": True, "preset": p, "prev_preset": prev or None, "batch_id": batch_id}


def restore_effective(workspace_root, *, now_iso: Optional[str] = None,
                      triggered_by: str = "ask me more") -> Optional[dict]:
    """REVIEW_QUIET1 F-1 — the person said `ask me more` on a seat the
    silence had stepped down: put the EFFECTIVE preset back to the stored
    one on the spot (not at the next daily leg). Writes ONE `restore`
    posture receipt stamped `by_user: true` (an answer — it resets the
    silence clock, so the leg does not step the seat down again tomorrow
    and the narration is not repeated) and rewrites the gauge artifact's
    interaction block. Returns the receipt data, or None when the seat was
    not stepped."""
    configured = configured_preset(workspace_root)
    effective = effective_preset(workspace_root)
    if effective == configured:
        return None
    now = _parse(now_iso) or datetime.now(timezone.utc)
    ev = write_posture_event(workspace_root, {"action": POSTURE_RESTORE,
                                              "from": effective, "to": configured,
                                              "configured": configured, "days_silent": 0},
                             now_iso=_iso(now), extra={"by_user": True,
                                                       "triggered_by": triggered_by})
    gp = _gauge_path(workspace_root)
    try:
        doc = json.loads(gp.read_text(encoding="utf-8"))
        blk = dict(doc.get(GAUGE_BLOCK_KEY) or {})
        blk.update({"configured_preset": configured, "effective_preset": configured,
                    "stepped_down_at": "", "stepped_down_from": "",
                    "last_answer_ts": _iso(now), "last_touch_ts": _iso(now),
                    "silence_origin_ts": _iso(now)})
        doc[GAUGE_BLOCK_KEY] = blk
        from atomic_write import atomic_write_json
        atomic_write_json(gp, doc)
    except Exception:  # noqa: BLE001 — the receipt is the record; the leg repairs the artifact
        pass
    return {"from": effective, "to": configured, "seq": ev.get("seq")}


def raise_preset(workspace_root, *, now_iso: Optional[str] = None,
                 triggered_by: str = "ask me more") -> dict:
    """`ask me more` — one level MORE engaged than what the person is
    EXPERIENCING. On a seat the silence stepped down, that is the restore
    (F-1): the effective preset returns to the stored one now, `ran=True,
    restored=True`, and the stored key does not move. On a seat that is
    not stepped, the stored key moves one level up (the person's own act;
    the only way up) and stops at the top."""
    cur = configured_preset(workspace_root)
    restored = restore_effective(workspace_root, now_iso=now_iso, triggered_by=triggered_by)
    if restored is not None:
        return {"ran": True, "restored": True, "preset": cur, "prev_preset": cur,
                "from": restored["from"], "batch_id": None}
    i = PRESET_LADDER.index(cur)
    target = PRESET_LADDER[max(i - 1, 0)]
    out = stamp_preset(workspace_root, target, origin="tune",
                       triggered_by=triggered_by, now_iso=now_iso)
    out["from"] = cur
    out["restored"] = False
    return out


def lower_preset(workspace_root, *, now_iso: Optional[str] = None,
                 triggered_by: str = "ask me less") -> dict:
    """`ask me less` — one level quieter than the configured posture."""
    cur = configured_preset(workspace_root)
    out = stamp_preset(workspace_root, step_down(cur), origin="tune",
                       triggered_by=triggered_by, now_iso=now_iso)
    out["from"] = cur
    return out


# ---------------------------------------------------------------------------
# Onboarding Q5 (CUT-D, M ruling 2026-09-06): every seat STARTS `light`,
# whatever the setup answer. "Daily" never means `engaged`. The answer is
# kept on the receipt's `triggered_by` line (so it is not lost) and changes
# nothing else; the only doors UP are `ask me more` (`raise_preset`) and the
# operator command (`quiet.py <ws> --stamp engaged --apply`).
# REVIEW_CUTD R2: "starts" is about FRESH seats. A seat with a posture
# already stored — `engaged` (the operator's own, or a seat that said `ask
# me more`) re-running onboarding, or the update bridge's `light` landing
# first — is left exactly as it is; the skip is receipted ONCE per seat
# (`interaction_posture` / `onboarding_kept`, the answer on the line) so
# the answer is never lost and nothing has to be undone. Pinned in
# `run_quiet1_test` [12]; proven by removal in G49 F15 / F16.
# ---------------------------------------------------------------------------
ONBOARDING_ANSWERS = ("daily", "few_times_a_week", "rarely")
ONBOARDING_PRESET = PRESET_LIGHT
ONBOARDING_ORIGIN = "m1_batch"


def onboarding_preset(answer: Optional[str]) -> str:
    """The preset an onboarding Q5 answer stamps: `light` for every answer,
    known or not (an older widget, an unclicked question)."""
    return ONBOARDING_PRESET


def _onboarding_kept_receipts(workspace_root) -> list:
    return [ev for ev in _load_events(workspace_root)
            if ev.get("type") == POSTURE_EVENT_TYPE
            and _data(ev).get("action") == POSTURE_ONBOARDING_KEPT]


def stamp_onboarding_preset(workspace_root, answer: Optional[str], *,
                            now_iso: Optional[str] = None) -> dict:
    """Stamp the onboarding posture on a FRESH seat through `stamp_preset`
    (origin `m1_batch`, batch-stamped, undoable). On a seat with a posture
    already stored (R2) stamp NOTHING — `ran False, skipped True, preset =
    the stored one` — and receipt the skip once (`onboarding_kept`, the
    answer on `triggered_by`); a second call on the same seat writes no
    second receipt. Returns the stamp's dict plus `answer` (the normalised
    Q5 answer, or `unanswered`) and `skipped`."""
    a = str(answer or "").strip().lower()
    a = a if a in ONBOARDING_ANSWERS else "unanswered"
    stored = stored_preset(workspace_root)
    if stored is not None:
        if not _onboarding_kept_receipts(workspace_root):
            write_posture_event(workspace_root,
                                {"action": POSTURE_ONBOARDING_KEPT, "from": stored,
                                 "to": stored, "configured": stored, "days_silent": None},
                                now_iso=now_iso,
                                extra={"triggered_by": f"onboarding Q5: {a}", "answer": a})
        return {"ran": False, "skipped": True, "preset": stored, "prev_preset": stored,
                "batch_id": None, "answer": a}
    out = stamp_preset(workspace_root, onboarding_preset(a), origin=ONBOARDING_ORIGIN,
                       triggered_by=f"onboarding Q5: {a}", now_iso=now_iso)
    out["skipped"] = False
    out["answer"] = a
    return out


# ---------------------------------------------------------------------------
# WRAP2 (SPEC_SURFACES2_11c Lane 4) — the wrap reports the week against the
# word, and NEVER ASKS
# ---------------------------------------------------------------------------
#
# M's design rule of 2026-09-06 says the brief and the wrap never ask; the
# Staff Meeting is where questions live and End of Day may carry two. The
# brief got its fence in SURFACEFIX1 (`surface_drivers.assert_brief_never_asks`)
# and End of Day got its budget. The WRAP had neither, and it asked twice on
# every fire: the objectives self-report ("20 seconds: how do these stand?")
# and the "What now" ASK block. Both are rehomed — the self-reports to the
# on-demand `objectives` skill (ruling R-6), the ask block to up to three
# STATEMENTS each carrying the phrase that acts on it.
#
# On top of that the wrap is where the week is REPORTED against the word.
# `coaching_doors.surface_deltas` has promised a seat that opens the door "an
# accountability report against what you committed to last Friday, and where
# {behaviour} stands in evidence from the week" since night 11a, and nothing
# on main produced either sentence: `earned_offer_due` / `record_earned_offer`
# / `accept_earned_offer` had no caller outside prose, and `claims.py` — the
# module that exists so a rendered number resolves to the ledger — had no
# production caller at all (11a N-13 / N-17). The five readers below are
# those callers.
#
# WHAT THIS SECTION MAY NOT DO
#   * ask anything, anywhere, on any seat (`assert_wrap_never_asks`);
#   * render a number that does not resolve (`claims.assert_surface_resolved`);
#   * score the reader (`eod_synthesis.assert_no_score` — a self-report's
#     number is the CUSTOMER'S own and is quoted back, never graded);
#   * compare the reader to anybody (`claims.comparison_tokens_in`, inside
#     the surface scan);
#   * COACH an OBSERVED seat. The behaviour read is the coaching one and it
#     is SHAPE-GATED: an observed seat gets nothing there, not a shorter
#     version and not a teaser, and the shape's safe direction is silence.
#     The other four blocks are UNGATED by ruling (REVIEW_WRAP2 F5, the
#     reviewer's ruling and the coordinator's default, 2026-09-15): next
#     week's three and What now are the plate's own rows; the accountability
#     report is the wrap reading its OWN word back; the bigger picture is a
#     count over a window that asks nothing and grades nobody, over arcs End
#     of Day already rendered to that seat nightly; and the earned door is
#     the door UP, which gating behind the door would seal shut. If M would
#     rather any of them waited for the door, it is one `coaching_shape`
#     check each in `wrap_coaching_blocks`.

#: The surface key every fence and every claim in this section is keyed to.
WRAP_SURFACE = "weekly-wrap"


class WrapAsksError(RuntimeError):
    """The wrap asked the reader a question. M's design rule of 2026-09-06:
    the brief and the wrap never ask; the Staff Meeting is where questions
    live, and End of Day may carry two."""


def wrap_interrogatives(text: str) -> list:
    """Every sentence in a composed wrap that puts a question to the reader.

    `surface_drivers.interrogatives_in` is CALLED, never copied: its opener
    roster and its four trailing-clause shapes ("…or drop?", "did I get that
    right?", "whose is this?", "yes or no?") were derived from the sentences
    the attended test actually recorded, and two copies of that roster is two
    fences that drift apart. The import is function-local because
    `surface_drivers` imports THIS module."""
    from surface_drivers import interrogatives_in

    return interrogatives_in(text or "")


def assert_wrap_never_asks(text: str, *, relayed: str = "",
                           spans: Optional[Iterable[str]] = None,
                           where: str = WRAP_SURFACE) -> None:
    """THE WRAP NEVER ASKS — over the whole composed post, and the document.

    Three halves, and the third is the one the brief does not need:

    The `?`-LINE RULE. A line the WRAP ITSELF composed may not carry a
    question mark AT ALL. The opener roster below is the shape a question
    usually takes, and it caught §9's "Which commitment is closest to
    overdue?" — but it walked straight past §8c's "20 seconds: how do these
    stand?", because the sentence opens on a numeral and the interrogative
    is a clause inside it. That ask went out on every wrap for months. On a
    surface with no reply path at all, the honest rule is the blunt one: no
    question mark in the product's own words.

    THE EXEMPTION IS SPAN-AWARE, AND THAT IS THE WHOLE OF IT (REVIEW_WRAP2
    F1/F2, 2026-09-15). The customer's own words reach this surface two
    ways: relayed WHOLE (the plate cut, byte-exact) and COMPOSED AROUND —
    the accountability report and next week's three build a sentence around
    a row title the customer wrote. The first cut exempted whole LINES only,
    and only from the `?` rule, so `wrap_coaching_blocks` raised on a
    customer title ending in a question mark and took the whole Friday wrap
    down. Four records on M's book carry such a title — and NONE of them is
    an open plate row, so the crash was real without being imminent. The
    earlier wording here implied the latter and is corrected in fix round 2.

    So: every declared span is BLANKED out of each line, in place, and BOTH
    rules then read what is left — the product's own words and nothing else.
    `relayed` declares whole lines (pass `plate["text"]`; each line of it is
    a span) and `spans` declares the fragments a block was composed around
    (the `user_spans` every block already carries for the document gate).
    Declare nothing and every line in `text` is treated as the product's
    own, which is the right default for a block this module composed itself.
    A question the WRAP composed around an exempt span still raises, because
    blanking removes the span and leaves the ask standing on its own.

    The blanking is `docx_leak_scanner.blank_user_spans`, CALLED — the same
    helper the document gate uses to decide whose words a span is, so the
    two gates cannot disagree about that. It runs line by line, so a span is
    never matched across a line break and the line a raise names is the line
    the reader would have seen.

    Two more halves, the same two the brief's fence has:

    The INTERROGATIVE half — a question PUT TO THE READER, anywhere in the
    wrap. That is what §8c's "20 seconds: how do these stand?" and §9's ASK
    block both were, and no fence on main could see either of them.

    The PROPOSAL half — `plate_view.scan_no_pending_question`, CALLED (its
    shapes live in `plate_view.py`, which owns them and which this lane does
    not re-spell). A wrap that tells the reader a proposal is waiting on them
    has asked without using a question mark.

    THE PROPOSAL HALF READS THE BLANKED TEXT TOO (REVIEW_WRAP2 N-1,
    2026-09-15). The first cut of the span fix handed `visible` to the two
    rules above and the RAW text to this one, so a customer's own row title
    reading like a pending proposal — "Proposal for Stone Co - waiting on
    your call?", "Stone Co proposal still pending", "Sample Co redline -
    awaiting your answer" — raised `PlateAskError` out of the composer and
    took the whole Friday wrap down. That is the F1 crash class, one fence
    over: `plate_view` blanks only its OWN markers, which this surface never
    sets, so a declaration made here was invisible to it. All three rules now
    read the same span-blanked body. A proposal claim the WRAP composed still
    raises, because blanking removes the customer's span and leaves the
    product's own sentence standing on its own.

    Run it over the FINAL text of the chat turn and over the document's
    joined section text, never over one block at a time: the defect both
    recorded asks belong to is one block composing a question no other block
    knows about."""
    from docx_leak_scanner import blank_user_spans

    declared = [l.strip() for l in (relayed or "").split("\n") if l.strip()]
    declared += [str(s).strip() for s in (spans or []) if str(s or "").strip()]
    lines = (text or "").splitlines()
    visible = [blank_user_spans(l, declared) if declared else l for l in lines]

    asks = wrap_interrogatives("\n".join(visible))
    if not asks:
        asks = [lines[i].strip() for i, l in enumerate(visible)
                if l.strip() and "?" in l]
    if asks:
        raise WrapAsksError(
            f"{where}: the wrap asks the reader a question ({asks[:3]!r}). "
            f"M's design rule of 2026-09-06: the brief and the wrap never "
            f"ask — the Staff Meeting is where questions live, and End of "
            f"Day may carry two. State it, or give it a phrase to say. (A "
            f"line relayed byte-exact from the plate cut is the customer's "
            f"own words — hand the cut in as `relayed`, and a block's `user_spans` in as `spans`.)")
    from plate_view import scan_no_pending_question

    scan_no_pending_question("\n".join(visible))


# ---------------------------------------------------------------------------
# 4.2 item 2 — next week's three, STATED
# ---------------------------------------------------------------------------

#: Three. The same cap `day_intent.MAX_ITEMS` puts on a day, for the same
#: reason: a list of eight is a to-do list and the reader already has one.
NEXT_WEEK_CAP = 3

NEXT_WEEK_HEADING = "Next week"
NEXT_WEEK_LEAD = "Next week:"

#: The phrase that OUTRANKS the derived three — stated beats learned, the
#: standing rule. The phrase is registered in `workspace-manager/SKILL.md`
#: (COACH2's file; recorded as this lane's seam) and routes to
#: `write_next_week_intent` below. This reader works whether or not that line
#: has landed: no phrase simply means no stated record for next Monday, and
#: the derived three stand.
NEXT_WEEK_PHRASE = "next week is about"

#: The receipt key next Friday's accountability report reads back. One
#: spelling, one home.
NEXT_WEEK_RECEIPT_KEY = "next_week_ids"

#: The task whose `pack_run` receipt carries it.
WRAP_TASK_ID = "friday-wrap"


def next_week_monday(workspace_root, now_iso: Optional[str] = None) -> str:
    """The Monday that opens NEXT week, workspace-local `YYYY-MM-DD`.

    Through `day_intent.workspace_today`, never `date.today()`: the wrap
    fires at 4pm Friday local, and a UTC-derived "next Monday" is a different
    day for most of the world's evening. From any day of this week this is
    the following Monday; from a Monday it is the Monday after — the week the
    reader is looking forward into, never the one they are standing in."""
    import day_intent as di

    today = di.workspace_today(
        workspace_root, now=_parse(now_iso) or datetime.now(timezone.utc))
    return (today + timedelta(days=7 - today.weekday())).isoformat()


def _as_date(value):
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


#: Plain day words. A bare ISO date reads as a wire value on a prose surface.
_WEEKDAY_WORDS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTH_WORDS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _short_date(value) -> str:
    """"Sep 5" — composed by hand rather than through `%-d`, which is not a
    format code Windows understands (the plugin runs on both)."""
    d = _as_date(value)
    if d is None:
        dt = _parse(value)
        d = dt.date() if dt else None
    return f"{_MONTH_WORDS[d.month - 1]} {d.day}" if d else ""


def _next_week_phrase(due, monday) -> str:
    """"(Tue)" for a row due inside next week, "(overdue)" for one already
    past, "" otherwise. The reader is told WHEN in the one word that means
    something to them."""
    d = _as_date(due)
    if d is None:
        return ""
    if d < monday:
        return " (overdue)"
    if d < monday + timedelta(days=7):
        return f" ({_WEEKDAY_WORDS[d.weekday()]})"
    return ""


def next_week_three(workspace_root, now_iso: Optional[str] = None, *,
                    view: Optional[dict] = None,
                    rows: Optional[list] = None) -> dict:
    """The three things next week is about, as STATEMENTS (4.2 item 2).

    Two sources, and the reader's own word wins:

      STATED — a `day_intent` record for next Monday, written through the
        `next week is about X` phrase (origin `manual`: the same writer,
        undo handle and provenance the day's own intent goes through).
        `load_day_intent` refuses a `proposed` draft by default, so a guess
        can never arrive here dressed as the reader's word.

      DERIVED — the highest-importance open rows due by the end of next week,
        ranked by `plate_view.importance_key`: the SAME order the plate, the
        widget cap and the wrap's Parked list already use, so overdue comes
        first and the three named here are the three the plate would have put
        at the top. A row with no due date is not next week's business and is
        not named.

    Returns `{"text", "lines", "ids", "rows", "stated", "source", "n",
    "claims"}`. `text` is `""` when there is nothing to name — an empty week
    says nothing rather than saying it is empty.

    NEVER an interrogative, never a widget, never a tap: one line the reader
    can read on a phone and forget."""
    import claims as cl

    monday = _as_date(next_week_monday(workspace_root, now_iso))
    window = f"the week of {monday.isoformat()}"
    import day_intent as di

    record = di.load_day_intent(workspace_root, monday.isoformat())
    if record and record.get("stated") and record.get("items"):
        items = list(record["items"])[:NEXT_WEEK_CAP]
        named = [str(it.get("text") or "").strip() for it in items]
        named = [n for n in named if n]
        ids = [str(it.get("commitment_id") or "").strip() for it in items]
        ids = [i for i in ids if i]
        source, spans = "stated", list(named)
    else:
        if rows is None:
            if view is None:
                from plate_view import build_plate

                view = build_plate(workspace_root, now_iso=now_iso)
            rows = list(view.get("rows") or []) if view.get("ok") else []
        from plate_view import importance_key

        horizon_end = monday + timedelta(days=7)
        candidates = [r for r in rows
                      if _as_date(r.get("due")) is not None
                      and _as_date(r.get("due")) < horizon_end]
        picked = sorted(candidates, key=importance_key)[:NEXT_WEEK_CAP]
        named, ids, spans = [], [], []
        for r in picked:
            title = str(r.get("title") or "").strip()
            if not title:
                continue
            named.append(title + _next_week_phrase(r.get("due"), monday))
            ids.append(str(r.get("id") or ""))
            spans.append(title)
        rows = picked
        source = "derived"

    if not named:
        return {"text": "", "lines": [], "ids": [], "rows": [],
                "stated": source == "stated", "source": source, "n": 0,
                "claims": [], "user_spans": []}
    line = f"{NEXT_WEEK_LEAD} " + " · ".join(named) + "."
    # An OBSERVATION, not a fact: the sentence states how many rows this week
    # put in front of next week, over a named window. It carries a claim at
    # all because a title can hold a numeral, and a figure on this surface
    # with nothing behind it is the class `claims` exists to refuse.
    manifest = [cl.make_claim(line, count=len(named), window=window)]
    return {"text": line, "lines": [line], "ids": ids, "rows": list(rows or []),
            "stated": source == "stated", "source": source, "n": len(named),
            "claims": manifest, "user_spans": spans}


#: How far back "last Friday" may reach. A weekly surface that skipped a
#: fire has nothing to report against; a ten-day window covers one late fire
#: and refuses a month-old list (M-5).
PRIOR_WRAP_MAX_AGE_DAYS = 10


def wrap_receipt_extra(three: Optional[dict]) -> dict:
    """The `extra_data` the wrap's ONE `pack_run` receipt carries for this
    section — `{"next_week_ids": [...]}` (4.2 item 2, the ordinal contract's
    shape).

    THE WRAP WRITES ONE RECEIPT. The orchestrator's Phase 5 `log_receipt`
    call is that receipt, and this dict merges into its `extra_data`; a
    second receipt of our own would split one fire's record in two and the
    served-slot reader would have to guess which half was the fire.

    This is the LOAD-BEARING half of item 3. `accountability_report` reads
    exactly this key back off the prior week's receipt, so a wrap that does
    not carry it leaves next Friday nothing to report against — which is
    precisely the state main was in: a promise of accountability with no
    record anywhere of what had been promised."""
    ids = [str(i).strip() for i in ((three or {}).get("ids") or [])
           if str(i or "").strip()]
    return {NEXT_WEEK_RECEIPT_KEY: ids} if ids else {}


def write_next_week_intent(workspace_root, phrase_or_items, *,
                           now_iso: Optional[str] = None,
                           source_ref: Any = None,
                           source_skill: str = "workspace-manager") -> dict:
    """`next week is about X` — the reader editing next week's three BY WORD.

    Writes a `day_intent` for next Monday through
    `day_intent.write_day_intent(origin="manual")`: the SAME writer, the same
    undo handle and the same provenance the day's own intent goes through
    (BK1's path). No second store, no second spelling, and bare `undo`
    reverses it exactly as it reverses "tomorrow is about X".

    `phrase_or_items` takes either the reader's raw sentence (the phrase is
    stripped when it leads) or an already-split list; `normalize_items` owns
    the splitting and the three-item cap from there."""
    import day_intent as di

    value = phrase_or_items
    if isinstance(value, str):
        text = value.strip()
        if text.lower().startswith(NEXT_WEEK_PHRASE):
            text = text[len(NEXT_WEEK_PHRASE):].strip(" :,-—")
        value = text
    return di.write_day_intent(
        workspace_root, value, origin="manual",
        for_date=next_week_monday(workspace_root, now_iso),
        source_ref=source_ref, source_skill=source_skill)


def last_next_week_ids(workspace_root, *,
                       before_iso: Optional[str] = None) -> dict:
    """What LAST Friday's wrap said next week was about.

    The most recent `friday-wrap` `pack_run` receipt carrying
    `next_week_ids`, strictly before `before_iso` when given — this Friday's
    own receipt must never be read as its own history, the same defensive
    exclusion `eod_coach.read_prior_packs` takes with `before_for_date`.

    Returns `{"found", "ids", "at", "reason"}`. `found` False means there is
    nothing to report against, which is the honest state of the FIRST wrap
    and is what makes item 3's section absent rather than empty.

    AND "LAST FRIDAY" MEANS LAST FRIDAY (REVIEW_NIGHT11C M-5, 2026-09-15).
    This took the newest receipt carrying the key AT ANY AGE, so a skipped
    or failed wrap left a month-old list under a heading that says last
    Friday. Anything older than `PRIOR_WRAP_MAX_AGE_DAYS` is not read, and
    `reason` says `stale_prior_receipt` when that is why nothing came back —
    the section goes absent, which is the honest answer."""
    from receipts import iter_receipts

    cutoff = _parse(before_iso)
    floor = (cutoff or datetime.now(timezone.utc)) - timedelta(
        days=PRIOR_WRAP_MAX_AGE_DAYS)
    best = None
    stale = False
    for rec in iter_receipts(workspace_root, task_ids=[WRAP_TASK_ID]):
        ids = _data(rec.get("raw") or {}).get(NEXT_WEEK_RECEIPT_KEY)
        if not isinstance(ids, list) or not ids:
            continue
        dt = rec.get("dt")
        if cutoff is not None and dt is not None and dt >= cutoff:
            continue
        if dt is not None and dt < floor:
            stale = True
            continue
        best = rec  # iter_receipts is oldest-first, so the last one wins
    if best is None:
        return {"found": False, "ids": [], "at": "",
                "reason": "stale_prior_receipt" if stale else ""}
    data = _data(best.get("raw") or {})
    return {"found": True,
            "ids": [str(i) for i in data.get(NEXT_WEEK_RECEIPT_KEY) or []],
            "at": _iso(best.get("dt")), "reason": ""}


# ---------------------------------------------------------------------------
# 4.2 item 3 — the accountability report
# ---------------------------------------------------------------------------

ACCOUNTABILITY_HEADING = "Against last Friday"

#: Every row on the report is one of these, and each carries the ledger seq
#: that proves it. There is no sixth outcome and no "probably".
OUTCOME_CLOSED_OWN_WORD = "closed_own_word"
OUTCOME_CLOSED_RECORD = "closed_record"
OUTCOME_SLIPPED = "slipped"
OUTCOME_LET_GO = "let_go"
OUTCOME_OPEN = "open"

#: The silence rail's let-go as `change_feed` classifies it: a `dropped`
#: resolution, never a `done` — nothing says it was finished, only that
#: nobody touched it for two months.
_RESOLUTION_DROPPED = "dropped"
_CONFIRMED_OWN_WORD = "own_word"

#: The one line the section becomes when a prior receipt exists and not one
#: row moved. Deliberately carries NO numeral: a count here would need its
#: own claim and would say nothing the sentence does not already say.
ACCOUNTABILITY_UNCHANGED = "Everything on last Friday's list is where it was."


def _commitment_history(events: Iterable[dict], wanted: set) -> dict:
    """Every event naming one of `wanted`, in ledger order, keyed by id.

    ONE pass over the ledger for the whole report rather than one pass per
    row: three full-history walks to answer three questions is the shape that
    makes a surface slow on a real book."""
    out: dict = {cid: [] for cid in wanted}
    for ev in events or []:
        data = _data(ev)
        cid = str(data.get("id") or data.get("commitment_id") or "").strip()
        if cid in out:
            out[cid].append(ev)
    return out


def _outcome_of(history: list) -> dict:
    """The row's outcome and THE LEDGER SEQ THAT PROVES IT.

    Newest-wins over the row's own history. A row with no event at all
    resolves to nothing and is NOT RENDERED — a line with no ref is exactly
    the unbacked sentence `claims` exists to refuse, and dropping it here is
    cheaper than refusing the whole section downstream."""
    seq_open = None
    title = ""
    for ev in history or []:
        data = _data(ev)
        if data.get("title") and not title:
            title = str(data["title"]).strip()
        if ev.get("type") == "commitment" and seq_open is None:
            seq_open = ev.get("seq")
    for ev in reversed(history or []):
        data = _data(ev)
        etype = ev.get("type")
        if etype == "commitment_resolved":
            if str(data.get("resolution") or "done") == _RESOLUTION_DROPPED:
                return {"outcome": OUTCOME_LET_GO, "seq": ev.get("seq"),
                        "when": _ts(ev), "title": title, "detail": ""}
            own = str(data.get("confirmed_by") or "") == _CONFIRMED_OWN_WORD
            return {"outcome": (OUTCOME_CLOSED_OWN_WORD if own
                                else OUTCOME_CLOSED_RECORD),
                    "seq": ev.get("seq"), "when": _ts(ev), "title": title,
                    "detail": ""}
        if etype == "commitment_updated" and data.get("new_due"):
            return {"outcome": OUTCOME_SLIPPED, "seq": ev.get("seq"),
                    "when": _ts(ev), "title": title,
                    "detail": _short_date(data["new_due"])}
    if seq_open is None:
        return {}
    return {"outcome": OUTCOME_OPEN, "seq": seq_open, "when": "",
            "title": title, "detail": ""}


def accountability_line(row: dict) -> str:
    """One row's sentence. Facts only, the customer's own words for the
    title, and never a grade: the outcome is what the ledger says happened,
    not a verdict on the person it happened to."""
    title = row["title"]
    outcome = row["outcome"]
    when = _short_date(row.get("when"))
    tail = f", {when}" if when else ""
    if outcome == OUTCOME_CLOSED_OWN_WORD:
        return f"{title} — closed, your own word{tail}."
    if outcome == OUTCOME_CLOSED_RECORD:
        return f"{title} — closed on the record{tail}."
    if outcome == OUTCOME_SLIPPED:
        return f"{title} — moved to {row['detail']}."
    if outcome == OUTCOME_LET_GO:
        return (f"{title} — let go through the silence door{tail}; say "
                f"`undo` to put it back on the resting list.")
    return f"{title} — still open."


def accountability_report(workspace_root, now_iso: Optional[str] = None, *,
                          events: Optional[list] = None,
                          titles: Optional[dict] = None) -> dict:
    """THE WEEK AGAINST THE WORD (4.2 item 3) — and `claims.py`'s first
    production caller (11a N-17, which found it had none).

    Reads LAST Friday's `next_week_ids` off the prior wrap's receipt and
    reports each of them: closed by the reader's own word, closed on the
    record, moved to a new date, let go through the silence door, or still
    open. Every line is a `claims.FACT` carrying the ledger seq that proves
    it — the sentence and its evidence are built in the same breath, which is
    the whole of "code at the write".

    ABSENT ON THE FIRST WRAP. No prior receipt means there is nothing to
    report against, and a heading over nothing is worse than no heading:
    `rendered` is False and `text` is `""`.

    ONE LINE when a prior receipt exists and not one row moved.

    Returns `{"rendered", "text", "heading", "lines", "claims", "ids",
    "user_spans", "reason"}`."""
    import claims as cl

    prior = last_next_week_ids(workspace_root, before_iso=now_iso)
    if not prior["found"]:
        return {"rendered": False, "text": "", "heading": "", "lines": [],
                "claims": [], "ids": [], "user_spans": [],
                "reason": "no_prior_receipt"}

    evs = events if events is not None else _load_events(workspace_root)
    wanted = {str(i) for i in prior["ids"] if str(i or "").strip()}
    history = _commitment_history(evs, wanted)

    outcomes: list = []
    for cid in prior["ids"]:
        row = _outcome_of(history.get(str(cid)) or [])
        # NO REF, NO LINE. The fence: take the seq away and `claims` refuses
        # the section rather than letting an unbacked sentence through.
        if not row or row.get("seq") in (None, ""):
            continue
        row["title"] = (row.get("title")
                        or str((titles or {}).get(cid) or "").strip())
        if not row["title"]:
            continue
        outcomes.append(row)

    if not outcomes:
        return {"rendered": False, "text": "", "heading": "", "lines": [],
                "claims": [], "ids": list(prior["ids"]), "user_spans": [],
                "reason": "nothing_resolvable"}

    if all(r["outcome"] == OUTCOME_OPEN for r in outcomes):
        lines = [ACCOUNTABILITY_UNCHANGED]
        manifest = [cl.make_claim(ACCOUNTABILITY_UNCHANGED,
                                  refs=[r["seq"] for r in outcomes])]
    else:
        lines, manifest = [], []
        for row in outcomes:
            line = accountability_line(row)
            lines.append(line)
            manifest.append(cl.make_claim(line, refs=[row["seq"]]))

    text = "\n".join([f"## {ACCOUNTABILITY_HEADING}"] + lines)
    return {"rendered": True, "text": text, "heading": ACCOUNTABILITY_HEADING,
            "lines": lines, "claims": manifest, "ids": list(prior["ids"]),
            "user_spans": [r["title"] for r in outcomes], "reason": ""}


# ---------------------------------------------------------------------------
# 4.2 item 4 — the behaviour in evidence, NAMED SHAPE ONLY
# ---------------------------------------------------------------------------

#: The routes a close can leave by that are NOT the reader's own act: the
#: silence door let it go, and a lapse is nobody's act at all. `fact`,
#: `own_word` and `tap` are the reader (M-4).
RAIL_ROUTES = ("silence", "lapse")

BEHAVIOUR_HEADING = "In evidence this week"

#: EOD2's event type for a self-scored answer. Carried as a literal rather
#: than imported so this reader works on a tree where that lane has not
#: landed: an absent event type simply means no answers, which renders the
#: shorter of the two sentences. Recorded as a seam.
COACHING_ANSWER_EVENT = "coaching_answer"

#: Words too common to carry a behaviour's meaning. A token match on "the"
#: would make every close in the week "evidence", which is the opposite of
#: evidence.
_BEHAVIOUR_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "more",
    "less", "being", "been", "have", "having", "when", "what", "your",
    "about", "than", "them", "they", "will", "would", "should", "could",
    "over", "under", "make", "making", "take", "taking", "give", "giving",
})

#: A token has to be at least this long to count. Shorter fragments match
#: inside unrelated words and turn a count into noise.
_BEHAVIOUR_TOKEN_MIN = 4

#: The endings a stated behaviour and a row title disagree about. A seat says
#: "delegating earlier" and the rows say "Delegate the scope note",
#: "delegated to Stone's team" — the same act in three tenses. Trimming these
#: is the whole of the stemming here: no dictionary, no library, and a trim
#: that would leave a fragment shorter than the floor is not taken.
_BEHAVIOUR_SUFFIXES = ("ingly", "ing", "edly", "ed", "es", "s", "er", "ly")


def _behaviour_stem(word: str) -> str:
    for suffix in _BEHAVIOUR_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _BEHAVIOUR_TOKEN_MIN:
            return word[:-len(suffix)]
    return word


def behaviour_tokens(behaviour: str) -> list:
    """The words of a stated behaviour that can carry a match, STEMMED.

    Empty means the behaviour cannot be counted and NOTHING renders — a read
    with no instrument is not a read, and "be with them more" is a sentence
    every close in the week would match on if this returned its stopwords."""
    out: list = []
    for raw in re.split(r"[^A-Za-z0-9']+", str(behaviour or "").lower()):
        word = raw.strip("'")
        if len(word) < _BEHAVIOUR_TOKEN_MIN or word in _BEHAVIOUR_STOPWORDS:
            continue
        stem = _behaviour_stem(word)
        if stem not in out:
            out.append(stem)
    return out


def _numbers_phrase(values: list) -> str:
    """"6 and 8" / "6, 8 and 9" — a LIST, never a mean and never a trend
    word. The reader's numbers are quoted back as the numbers they gave,
    because an average of two self-reports is a grade wearing arithmetic."""
    vals = [str(v) for v in values]
    if len(vals) == 1:
        return vals[0]
    return ", ".join(vals[:-1]) + " and " + vals[-1]


def behaviour_in_evidence(workspace_root, since_iso: str,
                          now_iso: Optional[str] = None, *,
                          events: Optional[list] = None) -> dict:
    """Where the behaviour the seat NAMED showed up this week (4.2 item 4).

    THE SHAPE IS THE GATE. An observed seat renders NOTHING — not a shorter
    version, not a teaser. `coaching_doors.coaching_shape` fails to
    `observed` on every error, so the safe direction is silence, which is the
    one thing this product has to get right about coaching somebody who never
    opened a door.

    On a NAMED or COACHED seat: the closes in the window whose own text names
    the behaviour, counted; and the self-scores the reader gave, as a LIST.
    Both are `claims.OBSERVATION` — a projection over a window, carrying the
    count and the window it was taken over.

    NEVER A SCORE OF THE PERSON. `eod_synthesis.assert_no_score` runs over
    the sentence before it leaves this function. A self-report's number is
    the CUSTOMER'S OWN, quoted back; the product never grades, never averages
    and never says whether the number is a good one.

    NEVER A COMPARISON, and never a trend word — no "up from", no "better
    than last week". Counts and the window, and the reader draws the line."""
    import coaching_doors as cd
    import eod_synthesis as syn
    import flow_measure as _fm

    empty = {"rendered": False, "text": "", "heading": "", "lines": [],
             "claims": [], "behaviour": "", "n_closes": 0, "scores": []}

    # THE SHAPE GATE (the fence: remove this and the observed control reds).
    if cd.coaching_shape(workspace_root) == cd.SHAPE_OBSERVED:
        return empty

    behaviour = str((cd.relationship(workspace_root) or {}).get("behaviour")
                    or "").strip()
    tokens = behaviour_tokens(behaviour)
    if not behaviour or not tokens:
        return empty

    since = _parse(since_iso)
    now = _parse(now_iso) or datetime.now(timezone.utc)
    evs = events if events is not None else _load_events(workspace_root)

    # THE REVERSAL FOLD IS THE CLOSURE CHAIN'S, ASKED BY POSITION
    # (REVIEW re-verification F-2, 2026-09-15). Round 1 wrote
    # `if data.get("reversed_at")`, a per-event FIELD — and nothing in this
    # tree writes that key. Every other module that folds reversals
    # (`change_feed`, `end_of_day`, `exit_doors`, `calendar_close`) reads
    # `reversed_at` as a set of APPEND POSITIONS from `closure_index`, so
    # the name collided with the tree's own vocabulary while meaning
    # something else and the fold caught nothing: on M's book the chain
    # marks 187 of 1,567 resolutions reversed and NONE carries the field.
    # This is the same call `change_feed.changes_since` makes, over the same
    # list, which is what the comment below used to claim without doing.
    try:
        from closure_index import reversed_closer_positions as _reversed
        reversed_at = _reversed(evs or [], until=now)
    except Exception:  # pragma: no cover — never widen a count on a failure
        reversed_at = set()

    n_closes = 0
    scores: list = []
    for pos, ev in enumerate(evs or []):
        dt = _parse(_ts(ev))
        if dt is None or (since is not None and dt < since) or dt > now:
            continue
        data = _data(ev)
        if ev.get("type") == "commitment_resolved":
            # M-4 (REVIEW_NIGHT11C, 2026-09-15) — A SENTENCE ABOUT WHAT
            # THE READER DID COUNTS ONLY WHAT THE READER DID. This walked
            # every `commitment_resolved` with no reversal fold and no route
            # filter, so a close an undo had reversed and a let-go the
            # silence rail wrote were both "where the behaviour showed up".
            # `change_feed.changes_since` folds both; this asks the SAME
            # fold, by position, over the same list.
            #
            # DIVERGENCE, STATED: the review prescribed
            # `value_receipt.COMPLETION_ROUTES` ("fact", "own_word"). That
            # pair is the right one for a COMPLETION measure and the wrong
            # one here — it drops `tap`, which is the reader pressing the
            # button, and it reds this lane's own reviewed fixture 3 -> 0.
            # What this sentence must exclude is the RAILS, so the rails are
            # what is named.
            if pos in reversed_at:
                continue
            if _fm.route_of(ev) in RAIL_ROUTES:
                continue
            haystack = " ".join(
                str(data.get(k) or "") for k in
                ("title", "evidence", "evidence_text", "resolution_note")
            ).lower()
            if any(t in haystack for t in tokens):
                n_closes += 1
        elif ev.get("type") == COACHING_ANSWER_EVENT:
            value = data.get("score", data.get("answer"))
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            scores.append(int(value) if float(value).is_integer() else value)

    if not n_closes and not scores:
        return empty

    window = f"{since_iso}..{_iso(now)}"
    closes = f"{n_closes} close" + ("" if n_closes == 1 else "s")
    if n_closes and scores:
        line = (f"This week {behaviour} showed up in {closes}; you "
                f"scored it {_numbers_phrase(scores)}.")
    elif n_closes:
        line = f"This week {behaviour} showed up in {closes}."
    else:
        line = f"This week you scored {behaviour} {_numbers_phrase(scores)}."

    # The score fence, over the composed sentence, before it leaves here.
    syn.assert_no_score(line, where="wrap.behaviour_in_evidence")

    import claims as cl

    manifest = [cl.make_claim(line, count=(n_closes or len(scores)),
                              window=window)]
    text = "\n".join([f"## {BEHAVIOUR_HEADING}", line])
    return {"rendered": True, "text": text, "heading": BEHAVIOUR_HEADING,
            "lines": [line], "claims": manifest, "behaviour": behaviour,
            "n_closes": n_closes, "scores": scores}


# ---------------------------------------------------------------------------
# 4.2 item 5 — the bigger picture, at most three sentences
# ---------------------------------------------------------------------------

BIGGER_PICTURE_HEADING = "The bigger picture"

#: Three. A cap on a set that is usually empty, never a target to fill.
BIGGER_PICTURE_CAP = 3

MOVEMENT_MOVED = "moved"
MOVEMENT_UNMOVED = "unmoved"


def _arc_template_parts(template: str) -> tuple:
    """`("The day went into ", " — ")` from
    `"The day went into {arc} — {what}."` — the literal head before the arc
    label and the literal that ends it.

    DERIVED FROM THE PRODUCING CONSTANT, never re-spelled here: the arc
    sentences are composed in `eod_synthesis` from these templates, and a
    second copy of their wording in this module is a copy that goes stale the
    next time somebody rewords a sentence, silently."""
    head, rest = template.split("{arc}", 1)
    return head, rest.split("{", 1)[0]


def _label_from(text: str, template: str) -> str:
    head, tail = _arc_template_parts(template)
    if not tail or not text.startswith(head):
        return ""
    rest = text[len(head):]
    return rest.split(tail, 1)[0].strip() if tail in rest else ""


def arc_movement_of(sentence: dict) -> tuple:
    """`(label, movement)` for a persisted `what_it_meant` sentence, or
    `("", "")`.

    The MOVEMENT is read off which template composed the sentence, not off
    the sentence's `kind`: a moved sentence carries the ARC's kind
    (`objective` / `workstream` / `org`) rather than the word "moved", so
    `kind` cannot answer this question and a lane that assumed it could would
    count every arc as still.

    The LABEL is in the sentence because the templates put it there; the arc
    REF is not usable on a prose surface (it is `thread:project_042` and the
    like — exactly what the leak scan refuses), so an arc this function
    cannot NAME is an arc the wrap does not talk about. That refusal is this
    block's leak fence."""
    import eod_synthesis as syn
    import plate_view as pv

    text = str((sentence or {}).get("text") or "").strip()
    if not text:
        return "", ""
    pairs = (
        (syn.T_ARC_UNMOVED_DATE, MOVEMENT_UNMOVED),
        (syn.T_ARC_UNMOVED_DUE, MOVEMENT_UNMOVED),
        (syn.T_ARC_UNMOVED_MEETING, MOVEMENT_UNMOVED),
        (syn.T_ARC_UNMOVED_PERSON, MOVEMENT_UNMOVED),
        (syn.T_ARC_UNMOVED_BLOCKER, MOVEMENT_UNMOVED),
        (syn.T_ARC_MOVED_FIRST, MOVEMENT_MOVED),
        (syn.T_ARC_MOVED_ALSO, MOVEMENT_MOVED),
        (syn.T_ARC_WEIGHT_ROWS, MOVEMENT_MOVED),
    )
    for template, movement in pairs:
        label = _label_from(text, template)
        if label and not pv.id_shaped_name(label):
            return label, movement
    return "", ""


def bigger_picture(workspace_root, *, now_iso: Optional[str] = None,
                   packs: Optional[list] = None,
                   window: Optional[str] = None) -> dict:
    """WHAT THE WEEK ADDED UP TO, in at most three sentences (4.2 item 5).

    Composed from the arcs the End-of-Day packs already counted — the arc
    that moved on the most evenings, and the arc that came up and never moved
    at all. Nothing is re-derived from raw rows: every evening's read was
    already fenced and grounded when it was written, and a second derivation
    is a second answer to a question already answered.

    THE FLOOR IS THE POINT. `claims.FLOOR_INSTANCES` is three and no sentence
    here renders below it: two evenings is not a pattern, and a reading
    minted out of two data points is the exact defect INSIGHT-RULE1 exists to
    stop. Each sentence is an OBSERVATION — the count it states and the
    window it was taken over — never a READING, because a count of evenings
    is something the record shows rather than something this module
    concluded."""
    import claims as cl
    import eod_coach as ec

    if packs is None:
        packs = ec.read_prior_packs(workspace_root, limit=ec.MAX_PRIOR_PACKS)
    packs = list(packs or [])
    n_packs = len(packs)

    moved: dict = {}
    unmoved: dict = {}
    for pack in packs:
        block = pack.get("what_it_meant") if isinstance(pack, dict) else None
        if not isinstance(block, dict):
            continue
        seen: dict = {}
        for s in (list(block.get("sentences") or [])
                  + list(block.get("unmoved") or [])):
            if not isinstance(s, dict):
                continue
            label, movement = arc_movement_of(s)
            if not label:
                continue
            # An arc that moved on an evening MOVED on it, whatever else the
            # same evening said about it.
            if seen.get(label) != MOVEMENT_MOVED:
                seen[label] = movement
        for label, movement in seen.items():
            bag = moved if movement == MOVEMENT_MOVED else unmoved
            bag[label] = bag.get(label, 0) + 1

    lines: list = []
    manifest: list = []
    win = window or f"the last {n_packs} evenings"

    for label, n in sorted(moved.items(), key=lambda kv: (-kv[1], kv[0])):
        if n < cl.FLOOR_INSTANCES:
            break
        line = (f"{label} moved on {n} of the last {n_packs} evenings — "
                f"that is where the week's weight went.")
        lines.append(line)
        manifest.append(cl.make_claim(line, count=n, window=win))
        break

    for label, n in sorted(((l, c) for l, c in unmoved.items()
                            if not moved.get(l)),
                           key=lambda kv: (-kv[1], kv[0])):
        if n < cl.FLOOR_INSTANCES:
            break
        line = f"{label} came up on {n} evenings and did not move once."
        lines.append(line)
        manifest.append(cl.make_claim(line, count=n, window=win))
        break

    lines, manifest = lines[:BIGGER_PICTURE_CAP], manifest[:BIGGER_PICTURE_CAP]
    if not lines:
        return {"rendered": False, "text": "", "heading": "", "lines": [],
                "claims": [], "n_packs": n_packs}
    return {"rendered": True, "text": "\n".join(
                [f"## {BIGGER_PICTURE_HEADING}"] + lines),
            "heading": BIGGER_PICTURE_HEADING, "lines": lines,
            "claims": manifest, "n_packs": n_packs}


# ---------------------------------------------------------------------------
# 4.2 item 6 — the earned door, offered ONCE, as a statement with a door
# ---------------------------------------------------------------------------

EARNED_OFFER_HEADING = "Worth working on"

#: The offer. Not a question, not a widget, not a tap — a STATEMENT with a
#: phrase, and the phrase is the door. The second clause is the ruling in the
#: reader's own terms: silence means never again, so saying nothing costs
#: them nothing and they are told so at the moment they might have worried.
#:
#: THE UNIT IS EVENINGS, AND THE SENTENCE SAYS SO (REVIEW_WRAP2 F4,
#: 2026-09-15). `n` counts END-OF-DAY packs — one per evening — and the
#: claim this line is built with carries `window="the last N evenings"`. The
#: spec's draft said "three Fridays running" (wrong: they are nightly, and
#: the count can exceed the floor) and the first cut said "of your recent
#: closes" (wrong the other way: a close is a row leaving the plate, which
#: is not what was counted). A customer-facing sentence and its own evidence
#: manifest may not disagree about the unit.
EARNED_OFFER_TEMPLATE = (
    "{label} has come up on {n} of your recent evenings. Say "
    "`coach me on {label}` and I will work it with you; say nothing and I "
    "will not raise it again.")

#: The answer recorded when the offer HAS BEEN POSTED — before any reply
#: exists, because the whole point of the row is that it exists whether or not
#: one ever comes. It is written after the post, not at compose: see
#: `wrap_record_offer` (REVIEW_NIGHT11C H-1, 2026-09-15).
EARNED_OFFER_ANSWER = "offered"


def earned_offer(workspace_root, *, now_iso: Optional[str] = None,
                 packs: Optional[list] = None, record: bool = True) -> dict:
    """THE EARNED DOOR, raised once, ever (4.2 item 6) — and the first
    production caller of `coaching_doors.record_earned_offer` (11a N-13).

    A pattern the End-of-Day coach kept on `EARNED_DOOR_FLOOR` (3) or more of
    the packs on disk earns ONE line on ONE wrap. `earned_offer_due` is the
    gate and it answers False forever once a key has been offered — accepted,
    declined, or met with silence. `accept_earned_offer` is the other half of
    the door and belongs to the `coach me on X` phrase (COACH2's); this lane
    writes the row that makes that phrase mean something.

    `record=False` composes the line WITHOUT writing. A render that shows the
    line and never writes the row offers the same pattern again next Friday,
    which is the one thing the door's own rule forbids — so SOMETHING has to
    write it, but not this function on the wrap's path: `wrap_coaching_blocks`
    composes with `record=False` and `wrap_record_offer` writes the row after
    the post, once the fences and the leak gate have let the line out
    (REVIEW_NIGHT11C H-1, 2026-09-15). `record=True` remains for a caller
    that has already shown the line and has nothing left that can refuse it."""
    import claims as cl
    import coaching_doors as cd
    import eod_coach as ec
    import plate_view as pv

    empty = {"rendered": False, "text": "", "heading": "", "lines": [],
             "claims": [], "pattern_key": "", "label": "", "n": 0}

    if packs is None:
        packs = ec.read_prior_packs(workspace_root, limit=ec.MAX_PRIOR_PACKS)
    packs = list(packs or [])

    counts: dict = {}
    labels: dict = {}
    for pack in packs:
        coach = pack.get("coach") if isinstance(pack, dict) else None
        kept = (coach or {}).get("patterns") if isinstance(coach, dict) else None
        if isinstance(kept, dict):
            kept = kept.get("kept")
        for cand in list(kept or []):
            if not isinstance(cand, dict):
                continue
            key = str(cand.get("identity") or "").strip()
            label = str(cand.get("label") or "").strip()
            if not key or not label:
                continue
            counts[key] = counts.get(key, 0) + 1
            labels.setdefault(key, label)

    for key, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        label = labels.get(key, "")
        # A label that reads as a wire id never reaches a prose surface, and
        # a pattern this wrap cannot NAME is not one it raises.
        if not label or pv.id_shaped_name(label):
            continue
        if not cd.earned_offer_due(workspace_root, key, n):
            continue
        line = EARNED_OFFER_TEMPLATE.format(label=label, n=n)
        if record:
            cd.record_earned_offer(workspace_root, key,
                                   answer=EARNED_OFFER_ANSWER)
        manifest = [cl.make_claim(line, count=n,
                                  window=f"the last {len(packs)} evenings")]
        return {"rendered": True, "text": "\n".join(
                    [f"## {EARNED_OFFER_HEADING}", line]),
                "heading": EARNED_OFFER_HEADING, "lines": [line],
                "claims": manifest, "pattern_key": key, "label": label,
                "n": n}
    return empty


# ---------------------------------------------------------------------------
# 4.2 items 1 and 7 — one composer for the chat AND the document
# ---------------------------------------------------------------------------

#: §9's replacement. Up to three STATEMENTS, each carrying the phrase that
#: acts on it. The ASK block is gone: "which commitment is closest to
#: overdue" was a question the reader could not answer without going
#: somewhere else, and it was one of the two asks the 09-11 wrap carried.
WHAT_NOW_HEADING = "What now"
WHAT_NOW_CAP = 3


def what_now_statements(items: Optional[Iterable[dict]] = None) -> dict:
    """§9 as statements (4.2 item 1). Each item is `{"text", "phrase"}` and
    renders as "<text> — say `<phrase>`". No `asks` kwarg, no tap, no
    question: the reader is told what is worth doing and handed the words to
    do it with.

    The cap stays three — the design rule's complaint was never the number,
    it was that they were asks."""
    lines = []
    for it in list(items or [])[:WHAT_NOW_CAP]:
        text = str((it or {}).get("text") or "").strip().rstrip(".")
        phrase = str((it or {}).get("phrase") or "").strip()
        if not text:
            continue
        lines.append(f"{text} — say `{phrase}`." if phrase else f"{text}.")
    if not lines:
        return {"rendered": False, "text": "", "heading": "", "lines": []}
    return {"rendered": True, "heading": WHAT_NOW_HEADING, "lines": lines,
            "text": "\n".join([f"## {WHAT_NOW_HEADING}"] + lines)}


def _wrap_block_text(block: dict) -> str:
    head = str(block.get("heading") or "").strip()
    lines = [str(l) for l in (block.get("lines") or [])]
    return "\n".join(([f"## {head}"] if head else []) + lines)


def wrap_docx_sections(blocks: Iterable[dict]) -> list:
    """The same blocks as `brief_writer` sections (4.2 item 7).

    BULLETS, NEVER A BODY — WRAPSAVE1's finding, inherited whole:
    `voice_tell_detector` runs its structural scan over section `body`
    paragraphs only, and the em dash these lines use as a column separator is
    a FAIL-severity finding in prose. One line in, one bullet out, so the
    words are byte-identical to the chat's and the two surfaces are the same
    document.

    `user_spans` travels WITH the section, exactly as `wrap_docx_section`
    carries it for the plate cut: the accountability lines are built around
    titles the customer's counterparties wrote, and one banned marketing word
    inside one of those refuses the whole weekly `.docx` when the document
    gate has no way to tell whose words they are."""
    out = []
    for b in blocks or []:
        lines = [str(l) for l in (b.get("lines") or [])]
        heading = str(b.get("heading") or "").strip()
        if not lines or not heading:
            continue
        out.append({"heading": heading, "bullets": lines,
                    "user_spans": [str(s) for s in (b.get("user_spans") or [])
                                   if str(s or "").strip()]})
    return out


def wrap_coaching_blocks(workspace_root, since_iso: str,
                         now_iso: Optional[str] = None, *,
                         events: Optional[list] = None,
                         packs: Optional[list] = None,
                         view: Optional[dict] = None,
                         what_now: Optional[Iterable[dict]] = None,
                         record: bool = True) -> dict:
    """EVERY WRAP2 SECTION, composed ONCE, for both surfaces (4.2 item 7).

    The chat body and the `.docx` carry THE SAME WORDS because they come off
    the same list. `blocks` is `[{"key", "heading", "lines", "user_spans"}]`,
    `text` joins them for the post, and `docx_sections` hands each one to
    `brief_writer` in the `{"heading", "bullets", "user_spans"}` shape
    WRAPSAVE1 established.

    EVERY BLOCK IS BEST-EFFORT; EVERY FENCE IS NOT (REVIEW_NIGHT11C H-4).
    A block that raises is ABSENT and its key lands in `failed_blocks` —
    BRIEF2's contract for the brief's own reads, which the wrap's five
    blocks did not have. The fences below still raise: a wrap missing a
    section is a shorter wrap, a wrap carrying a question is not posted.

    Every gate this lane owns runs HERE, over the composed whole:
      * `assert_wrap_never_asks` — no interrogative anywhere, chat or docx;
      * `eod_synthesis.assert_no_score` — no grade, on any seat;
      * `claims.assert_surface_resolved` — every figure and every outcome
        sentence in the lines THIS MODULE composed resolves to a claim.
        The scan runs over the LINES, not the headings: `claims` splits
        sentences on terminal punctuation, and a heading with no full stop
        would otherwise be glued to the line under it and never match the
        claim text that line was built with. `what_now`'s lines are excluded
        from the manifest because their words are the caller's, not this
        module's — they carry no product-derived figure, and the two fences
        that DO cover them (the ask fence and the score fence) run over the
        whole text including them.

    ONE block is door-gated, not four (REVIEW_WRAP2 F5, 2026-09-15). An
    OBSERVED seat gets no BEHAVIOUR read — that block is coaching and the
    coaching shape is its switch. It still gets next week's three, What now,
    the accountability report, the bigger picture and the earned door, and
    that is the ruling, not an oversight: the first three are the wrap
    reading the reader's own rows and its own word back, the bigger picture
    is a count over a window that asks nothing and grades nobody, and the
    earned door is how a seat asks for coaching in the first place."""
    import claims as cl
    import eod_synthesis as syn

    blocks: list = []
    manifest: list = []
    resolved_lines: list = []

    def _add(key, heading, lines, spans=(), claims=()):
        if not lines:
            return
        blocks.append({"key": key, "heading": heading, "lines": list(lines),
                       "user_spans": list(spans)})
        manifest.extend(list(claims))
        resolved_lines.extend(list(lines))

    #: EVERY BLOCK IS BEST-EFFORT — A MISSING BLOCK, NEVER A DEAD WRAP
    #: (REVIEW_NIGHT11C H-4, 2026-09-15). BRIEF2 states this contract for
    #: the brief's own reads ("line absent, never a failed surface",
    #: `surface_drivers.brief_day_intent_line`); the wrap's five blocks did
    #: not have it, and the coaching train gave them a new way to die — next
    #: week's three asks `day_intent.workspace_today`, which RAISES on a
    #: workspace whose `entities.json` carries no `user_timezone`. One
    #: unreadable store took the whole Friday post down.
    #:
    #: The FENCES below are NOT best-effort and never will be: a wrap that
    #: cannot compose a block still posts without it, and a wrap that
    #: composes a question does not post at all.
    def _block(key, fn, default):
        try:
            return fn()
        except Exception:  # noqa: BLE001 — block absent, never a dead wrap
            failed.append(key)
            return default

    failed: list = []
    empty_section = {"rendered": False, "text": "", "heading": "",
                     "lines": [], "claims": [], "user_spans": []}

    three = _block("next_week",
                   lambda: next_week_three(workspace_root, now_iso, view=view),
                   dict(empty_section, ids=[], rows=[], stated=False,
                        source="", n=0))
    _add("next_week", NEXT_WEEK_HEADING, three["lines"],
         three.get("user_spans") or [], three.get("claims") or [])

    report = _block("accountability",
                    lambda: accountability_report(workspace_root, now_iso,
                                                  events=events),
                    dict(empty_section))
    if report["rendered"]:
        _add("accountability", report["heading"], report["lines"],
             report["user_spans"], report["claims"])

    evidence = _block("behaviour",
                      lambda: behaviour_in_evidence(workspace_root, since_iso,
                                                    now_iso, events=events),
                      dict(empty_section))
    if evidence["rendered"]:
        _add("behaviour", evidence["heading"], evidence["lines"], (),
             evidence["claims"])

    picture = _block("bigger_picture",
                     lambda: bigger_picture(workspace_root, now_iso=now_iso,
                                            packs=packs),
                     dict(empty_section))
    if picture["rendered"]:
        _add("bigger_picture", picture["heading"], picture["lines"], (),
             picture["claims"])

    # THE DOOR IS NOT CLOSED HERE (REVIEW_NIGHT11C H-1, 2026-09-15).
    # `record_earned_offer` used to run inside `earned_offer`, at compose —
    # BEFORE the three fences below and before the skill posts anything. A
    # wrap that raised on a fence, or a post the leak gate refused, had
    # already written `{key: offered}`, and `earned_offer_due` answers False
    # forever after that: the reader never saw the line and never will. So
    # the composer only ever COMPOSES, and hands the key back for the
    # orchestrator to commit after the post — see `wrap_record_offer`.
    offer = _block("earned_offer",
                   lambda: earned_offer(workspace_root, now_iso=now_iso,
                                        packs=packs, record=False),
                   dict(empty_section, pattern_key="", label="", n=0))
    if offer["rendered"]:
        _add("earned_offer", offer["heading"], offer["lines"], (),
             offer["claims"])

    now_block = what_now_statements(what_now)
    if now_block["rendered"]:
        blocks.append({"key": "what_now", "heading": WHAT_NOW_HEADING,
                       "lines": list(now_block["lines"]), "user_spans": []})

    text = "\n\n".join(_wrap_block_text(b) for b in blocks)
    spans = [str(sp) for b in blocks for sp in (b.get('user_spans') or [])
             if str(sp or '').strip()]
    # A FENCE RAISE HERE IS A DEAD WRAP TOO (F-3, 2026-09-15), and the same
    # receipt says so. The blocks degrade; the fences do not, and telling
    # those two apart is exactly what the watchdog needs to be able to do.
    try:
        assert_wrap_never_asks(text, spans=spans)
        syn.assert_no_score(text, where=WRAP_SURFACE)
        cl.assert_surface_resolved(WRAP_SURFACE, "\n".join(resolved_lines),
                                   manifest)
    except Exception as exc:
        wrap_failed(workspace_root, exc)
        raise
    return {"blocks": blocks, "text": text, "claims": manifest,
            "next_week": three, "accountability": report,
            "behaviour": evidence, "picture": picture, "offer": offer,
            # The once-ever key this fire OFFERED, for `wrap_record_offer` to
            # write after the post. `record=False` is a preview and hands
            # back no key at all, so a preview can never close the door.
            "offer_pattern_key": (str(offer.get("pattern_key") or "")
                                  if record else ""),
            # The blocks that could not be composed (H-4). The wrap still
            # posts, one section short, and names them here so the
            # orchestrator can say so in the post. A dark BLOCK writes no
            # `surface_failed` receipt (the wrap fired); only a wrap that
            # dies whole — a fence refusal in `wrap_post` or here — writes
            # one, through `wrap_failed` (MTR fix round 2, F-3).
            "failed_blocks": list(failed),
            "receipt_extra": wrap_receipt_extra(three),
            "docx_sections": wrap_docx_sections(blocks)}


class WrapRelayError(RuntimeError):
    """The post dropped or reworded a line of the plate cut (CUT-PLATE).

    The cut is relayed byte-exact; a post that summarises it is the v5.28.0
    failure the relay check was built for."""


def wrap_failed(workspace_root, exc: BaseException) -> Optional[dict]:
    """Record that the Friday wrap could not render — the wrap's own half of
    the failure path (re-verification F-3, 2026-09-15).

    H-4 registered the vocabulary (`SURFACE_FAILED_WRAP`, the receipt type on
    `friday-wrap`, the plain sentence) and wired no writer, so the watchdog
    still read a dead wrap as a job that never fired. With the five blocks
    now best-effort, the remaining way for the wrap to die is a FENCE — and a
    fence raise wrote nothing. This is the call the day-close and the brief
    already make, on the surface that did not have it.

    Never raises: a wrap that failed must not fail again on the way to saying
    so, which is the whole reason `log_surface_failed` exists."""
    try:
        import surface_drivers as _sd
        return _sd.log_surface_failed(workspace_root, _sd.SURFACE_FAILED_WRAP,
                                      exc)
    except Exception:  # pragma: no cover — the raise below is what matters
        return None


def wrap_post(text: str, *, relayed: str = "",
              spans: Optional[Iterable[str]] = None,
              workspace_root=None) -> str:
    """THE ONE DOOR EVERY WRAP POST GOES THROUGH — and it returns the text.

    THE FENCES WERE PROSE-INSTRUCTED (REVIEW_NIGHT11C H-6, 2026-09-15).
    `wrap_coaching_blocks` fences the blocks IT composed, which is most of
    the week-against-the-word section and none of the rest of the post:
    §8c's `recap_rows` lines, the plate cut, the Decided-for-you block and
    everything the model writes around them reached the chat fenced only if
    the model remembered to run a snippet. "The fence is in code, and this
    section is inside it" was true of one section and false of the post.

    So the skill renders THIS function's RETURN VALUE. Three fences, over
    the FINAL text of the chat turn, in the order a reader would want them:

      * `assert_wrap_never_asks` — no question mark in the product's own
        words (`relayed` is the plate cut, `spans` every `user_spans` entry
        the composer handed back; neither is optional and they are not the
        same thing);
      * `eod_synthesis.assert_no_score` — no grade, on any seat;
      * `plate_view.wrap_relay_check` — the plate cut relayed byte-exact,
        which the skill used to run as a separate step and could skip.

    Every one of them RAISES. Nothing here degrades: a wrap missing a
    section still posts (`wrap_coaching_blocks`' own best-effort blocks), a
    wrap carrying a question or a grade does not post at all."""
    import eod_synthesis as syn
    import plate_view as pv

    body = str(text or "")
    # AND IT STAMPS NOTHING (RE-VERIFY_LEAK4 P-1, 2026-09-18). Fix round 2
    # put a `_register_composed(relayed)` here so the Friday wrap could
    # relay its plate cut through `surface_composers.post`. That vouched
    # for whatever text this door was HANDED, before this door's own
    # fences ran, so one call from anywhere waved any paragraph through
    # afterwards. The stamp moved to `plate_view.wrap_cut` -- the
    # function that composed the cut in the first place. A
    # `wrap_post(relayed=X)` call no longer says anything about X to
    # anyone.
    # AND A RAISE HERE IS A DEAD WRAP, SO IT LEAVES A RECEIPT (F-3). Given a
    # workspace root this writes ONE `surface_failed` receipt and re-raises;
    # the raise is never swallowed. Without a root it behaves exactly as
    # before, for a caller that has no workspace (a preview, a test probe).
    try:
        assert_wrap_never_asks(body, relayed=relayed or "", spans=spans)
        syn.assert_no_score(body, where=WRAP_SURFACE)
        if relayed:
            missing = pv.wrap_relay_check(body, relayed)
            if missing:
                raise WrapRelayError(
                    f"{WRAP_SURFACE}: the plate cut is relayed byte-exact and "
                    f"{len(missing)} of its lines are not in the post "
                    f"(CUT-PLATE, 2026-09-06). First missing: {missing[0]!r}")
    except Exception as exc:
        if workspace_root is not None:
            wrap_failed(workspace_root, exc)
        raise
    return body


def wrap_record_offer(workspace_root, wrap2: Optional[dict], *,
                      answer: str = EARNED_OFFER_ANSWER) -> dict:
    """CLOSE THE EARNED DOOR — after the post, never before (H-1).

    The other half of `wrap_coaching_blocks`. It writes the once-ever offer
    row for the key that fire actually composed, and it belongs in the same
    place `next_week_ids` is written: the orchestrator's Phase 5, after the
    chat turn is out. Everything between composing and posting can still
    refuse — the ask fence, the score fence, the claims fence, the relay
    check, the leak gate — and a door closed before those is a door the
    reader never got to walk through.

    No key (no offer this week, or a preview) is a no-op, not an error."""
    import coaching_doors as cd

    key = str((wrap2 or {}).get("offer_pattern_key") or "").strip()
    if not key:
        return {"recorded": False, "pattern_key": ""}
    cd.record_earned_offer(workspace_root, key, answer=answer)
    return {"recorded": True, "pattern_key": key}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="QUIET1 — read or stamp a workspace's interaction posture")
    ap.add_argument("workspace")
    ap.add_argument("--stamp", choices=PRESETS, help="write this preset (receipted, undoable)")
    ap.add_argument("--apply", action="store_true", help="with --stamp: actually write")
    args = ap.parse_args(argv)
    ws = Path(args.workspace)
    if args.stamp:
        if not args.apply:
            print(json.dumps({"would_stamp": args.stamp, "configured": configured_preset(ws)}))
            return 0
        print(json.dumps(stamp_preset(ws, args.stamp, origin="operator_stamp",
                                      triggered_by="quiet.py --stamp")))
        return 0
    print(json.dumps({"configured": configured_preset(ws),
                      "effective": effective_preset(ws),
                      "gauge": _read_gauge_block(ws),
                      "budget": question_budget(ws)}, indent=2))
    return 0


__all__ = [
    "PRESET_LADDER", "CLIENT_DEFAULT_PRESET", "OPERATOR_PRESET",
    "STEP_DOWN_DAYS", "INTERACTION_WINDOW_DAYS", "DEFAULT_WINDOW_DAYS",
    "QUESTION_BUDGET", "BUDGET_WINDOW_DAYS", "PLATE_CAP",
    "ONBOARDING_ANSWERS", "ONBOARDING_PRESET", "ONBOARDING_ORIGIN",
    "onboarding_preset", "stamp_onboarding_preset", "stored_preset",
    "POSTURE_ONBOARDING_KEPT",
    "RETURN_SUMMARY_MIN_DAYS", "RETURN_SUMMARY_MAX_LINES",
    "ASKERS", "ASKER_MEETING_CARD", "ASKER_OVERDUE", "ASKER_AGE_OUT",
    "ASKER_EOD_CONFIRM", "ASKER_EOD_COACH",
    "NOT_QUESTIONS", "CONSEQUENCE_ORDER",
    "POSTURE_EVENT_TYPE", "POSTURE_STEP_DOWN", "POSTURE_RESTORE", "POSTURE_NARRATED",
    "BUDGET_EVENT_TYPE", "PRESET_CHANGE_CLASS", "STAMP_BATCH_PREFIX",
    "GAUGE_BLOCK_KEY", "ANSWER_EVENT_TYPES", "STEP_DOWN_NARRATION",
    "QuietInputError",
    "step_down", "is_quieter_or_equal", "configured_preset", "effective_preset",
    "is_user_answer", "is_opened_surface", "interaction_block",
    "days_since_last_answer", "gauge_leg", "write_posture_event",
    "step_down_narration",
    "consequence_of", "rank_questions", "question_budget", "submit_questions",
    "staff_meeting_question_ceiling", "STAFF_CEILING_FLOOR",
    "plate_cap",
    "window_since_last_touch", "brief_window", "return_summary",
    "wrap_sections", "receipt_counters",
    "mint_stamp_batch_id", "stamp_preset", "raise_preset", "lower_preset",
    "restore_effective", "USER_VERB_QUESTION_BY", "RELEASE_STAMP_ORIGIN",
    "CONFIG_EVENT_TYPES", "DECIDED_FEED_CATEGORIES", "FEED_NARRATED_CLASSES",
    "DECIDED_NAMED_ACT_CAP", "DECIDED_MORE_LINE",
    # WRAP2 (SPEC_SURFACES2_11c Lane 4) — the wrap never asks, and the
    # week is reported against the word.
    "WRAP_SURFACE", "WrapAsksError", "wrap_interrogatives",
    "assert_wrap_never_asks",
    "NEXT_WEEK_CAP", "NEXT_WEEK_HEADING", "NEXT_WEEK_LEAD",
    "NEXT_WEEK_PHRASE", "NEXT_WEEK_RECEIPT_KEY", "WRAP_TASK_ID",
    "next_week_monday", "next_week_three", "wrap_receipt_extra",
    "write_next_week_intent", "last_next_week_ids",
    "PRIOR_WRAP_MAX_AGE_DAYS", "RAIL_ROUTES",
    "ACCOUNTABILITY_HEADING", "ACCOUNTABILITY_UNCHANGED",
    "OUTCOME_CLOSED_OWN_WORD", "OUTCOME_CLOSED_RECORD",
    "OUTCOME_SLIPPED", "OUTCOME_LET_GO", "OUTCOME_OPEN",
    "accountability_line", "accountability_report",
    "BEHAVIOUR_HEADING", "COACHING_ANSWER_EVENT", "behaviour_tokens",
    "behaviour_in_evidence",
    "BIGGER_PICTURE_HEADING", "BIGGER_PICTURE_CAP", "MOVEMENT_MOVED",
    "MOVEMENT_UNMOVED", "arc_movement_of", "bigger_picture",
    "EARNED_OFFER_HEADING", "EARNED_OFFER_TEMPLATE",
    "EARNED_OFFER_ANSWER", "earned_offer",
    "WHAT_NOW_HEADING", "WHAT_NOW_CAP", "what_now_statements",
    "wrap_docx_sections", "wrap_coaching_blocks", "wrap_record_offer",
    "wrap_post", "wrap_failed", "WrapRelayError",
]


if __name__ == "__main__":
    sys.exit(main())
