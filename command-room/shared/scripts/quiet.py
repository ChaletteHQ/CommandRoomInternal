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
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

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
ASKERS = (ASKER_MEETING_CARD, ASKER_OVERDUE, ASKER_AGE_OUT)
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
})
FEED_NARRATED_CLASSES = frozenset({
    "commitment_close", "commitment_merge", "org_promotion", "person_link",
    "person_org_creation_structured_fact", "entity_fact_structured",
    "chat_dismissal",
})


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
    batch_lines = [f"- {b['label']} — say `undo` and pick it from the list to reverse"
                   for b in batches
                   if not (set(b.get("classes") or []) & FEED_NARRATED_CLASSES)
                   ][:RETURN_SUMMARY_MAX_LINES * 2]
    decided_text = ""
    if lines or batch_lines:
        decided_text = ("## Decided for you this week\n"
                        + "\n".join(f"- {t}" for t in lines)
                        + ("\n" if lines and batch_lines else "")
                        + "\n".join(batch_lines)).rstrip()
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
    "plate_cap",
    "window_since_last_touch", "brief_window", "return_summary",
    "wrap_sections", "receipt_counters",
    "mint_stamp_batch_id", "stamp_preset", "raise_preset", "lower_preset",
    "restore_effective", "USER_VERB_QUESTION_BY", "RELEASE_STAMP_ORIGIN",
    "CONFIG_EVENT_TYPES", "DECIDED_FEED_CATEGORIES", "FEED_NARRATED_CLASSES",
]


if __name__ == "__main__":
    sys.exit(main())
