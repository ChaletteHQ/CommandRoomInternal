#!/usr/bin/env python3
"""
BRIDGESIL1 — the update bridge refreshes schedules silently.

Origin: M's live-walk intake (S5, 2026-08-25/26) — update-bridge schedule
confirms are friction, one-word-diff prompts are unnecessary. Kin intake:
BUG_2026-08-16 (stamp-only refresh) and BUG_2026-08-19 (W4 refresh rewrites
prompts on a stamp-only diff) are both instances of the same root defect this
module fixes structurally: a refresh path that treated "the raw text
differs" as "something changed" instead of asking what actually changed and
whether the customer owns it.

RULINGS (§0, binding, kept verbatim from the spec):

  1. Silent refresh applies ONLY to non-semantic diffs: cron/label/prompt
     text where the change is core-authored and the client never customized
     that field. Registration never re-anchors a live task's cron the client
     customized — the schedule_config override ALWAYS wins.
  2. Stamp-only diffs write NOTHING (the 08-16/08-19 class): if the only
     diff is a version stamp or regenerated boilerplate, the refresh is a
     no-op — no rewrite, no prompt, no receipt churn.
  3. Semantic core changes (new slot, changed cadence) apply silently BUT
     are narrated after the fact, one line on the next morning brief, never
     a pre-confirmation. Announce, don't ask.
  4. Everything receipted (`schedule_refreshed`: task, field, old->new,
     origin=bridge) and undoable via the existing schedule_config override
     path — a customer who dislikes what the bridge did says `change my
     schedule` and it moves, exactly like any other cron edit.

THREE FUNCTIONS, THREE RULINGS:

  `plan_schedule_refresh` classifies ONE task/field diff into a closed
  vocabulary — noop / silent_apply / preserve — and is Ruling 1's
  customization test in code, TWO signals, both required for a silent apply
  (spec "the work" item 3): (a) no explicit override in
  `workspace.schedule_config`, and (b) the live value appears in the
  shipped-default TABLE (`schedule_config.SHIPPED_CRON_HISTORY` — every cron
  core ever shipped for the task), so the value being replaced is provably
  core-authored. Never a diff of the live value against the just-shipped new
  default: diffing against "current core" would misclassify every genuine
  core cadence change as "customized" and freeze it forever — the exact
  freeze this spec exists to thaw — while treating any divergence as
  uncustomized would silently destroy an out-of-band edit (a cron moved in
  the Cowork UI writes no override; only the table's silence protects it).
  `confirm_required`
  is hardcoded False: there is no fourth outcome, and Ruling 1's "drop the
  confirm prompt from this path" is the fence `apply_schedule_refresh` gates
  on below.

  `apply_schedule_refresh` executes a `silent_apply` plan and writes the
  ONE receipt Ruling 4 requires via `log_schedule_refreshed`. `noop` and
  `preserve` write nothing — no rewrite, no prompt, no receipt churn
  (Ruling 2 / Ruling 1). The confirm-required gate is the acceptance's
  no-prompt mutation target: reintroduce a live condition on that key (the
  BUG_2026-08-16/08-19 regression shape — a refresh path that asks before
  applying) and the apply silently stops applying, which the pin catches as
  a missing write/receipt rather than a literal dialog, because this module
  has no dialog layer to remove.

  `prompts_equivalent` is Ruling 2's concrete fix for the two named bugs:
  two composed bootloader bodies compare equal for refresh purposes once
  the diagnostic plugin-version stamp is normalized out of both sides via
  `task_watchdog.normalize_prompt_stamp` — a version-only bump is therefore
  a genuine no-op, not a content diff.

ANNOUNCE LEDGER (Ruling 3, item 2 of "the work"). `announce_lines` imitates
TASKALARM1's shape exactly (`task_alarm.py` — same render-once ledger
posture, same cap-with-tail-line rendering, same best-effort atomic write):
keyed by the `schedule_refreshed` event's own `seq` (additive, immutable —
the natural per-change identity; TASKALARM1 keys by a recomputed "dark
window" anchor because its underlying fact has no event of its own, this
spec's underlying fact IS an event). Only `cron` / `label` rows announce —
a `prompt`-field refresh is plumbing a customer never asked about and Ruling
3's example line ("your background capture now also runs at 4:30 PM") is a
cadence sentence, not a text-diff sentence.

TWO CUSTOMIZATION SIGNALS, NO HEURISTICS. The override store this module
reads (`workspace.schedule_config`) is the same one `change-schedule` writes
and the same one the rest of the plugin already treats as "the customer
touched this" (see `schedule_config.py`'s own docstring: "a SPARSE override
store — an entry means the operator customized this; missing means
default"). But the override store is deliberately sparse, and NOT every
customization writes one — a cadence edited in the Cowork UI, or kept by
declining a bridge migration (`staff_meeting_cadence_mwf_v1`'s decline path
writes NOTHING by design), leaves no entry. The shipped-default table
(`schedule_config.SHIPPED_CRON_HISTORY`) closes that gap from the other
side: a live value core never shipped cannot be core-authored, whatever the
override store says, and is preserved. Silent apply requires BOTH — no
override AND a live value core itself shipped.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

SCHEDULE_REFRESHED = "schedule_refreshed"

# Where the render-once announce ledger lives — `.system` is the workspace's
# diagnostic sidecar home (mirrors ALARM_LEDGER_RELPATH in task_alarm.py).
ANNOUNCE_LEDGER_RELPATH = "_hq/.system/schedule_refresh_announce_ledger.json"
ANNOUNCE_LEDGER_WINDOW_DAYS = 90
ANNOUNCE_CAP = 3

# The closed decision vocabulary. Never a fourth value.
DECISION_NOOP = "noop"
DECISION_SILENT_APPLY = "silent_apply"
DECISION_PRESERVE = "preserve"

# Fields eligible for the semantic (cron/label) classify+apply pass. `prompt`
# is handled separately by `prompts_equivalent` — it has no customization
# surface (no product path lets a customer hand-edit bootloader text), so it
# never reaches `preserve`.
SEMANTIC_FIELDS = ("cron", "label")


def _now_local() -> _dt.datetime:
    try:
        from trusted_now import trusted_now_local_naive

        return trusted_now_local_naive()
    except Exception:  # noqa: BLE001
        return _dt.datetime.now()


# ---------------------------------------------------------------------------
# Ruling §0.2 — stamp-only diffs write NOTHING
# ---------------------------------------------------------------------------

def prompts_equivalent(composed: str, registered: str) -> bool:
    """Two bootloader bodies are equivalent for refresh purposes once the
    diagnostic plugin-version stamp is normalized out of both sides. THE fix
    for BUG_2026-08-16 / BUG_2026-08-19: the stamp made every version bump
    look like a content change, so every plugin upgrade rewrote every
    registered prompt for zero behavioral gain (proof on file: `git diff` on
    the actually-changed release was empty for the pinned bootloader
    template; the seven rewrites traced entirely to the stamp).
    """
    from task_watchdog import normalize_prompt_stamp

    return normalize_prompt_stamp(composed or "") == normalize_prompt_stamp(registered or "")


def prompt_fingerprint(text: str) -> str:
    """A short, stable stand-in for a multi-KB bootloader body in a
    `schedule_refreshed` receipt. Ruling §0.4 says "everything receipted",
    but events.jsonl is additive-forever substrate — writing the FULL
    composed bootloader as `old`/`new` on every prompt refresh would bloat
    every workspace's event log with duplicate copies of a file that already
    lives on disk. The receipt exists to prove a change happened and when,
    not to be a second copy of the template."""
    import hashlib

    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Ruling §0.1 / §0.3 — classify ONE task/field diff
# ---------------------------------------------------------------------------

def plan_schedule_refresh(task_id: str, field: str, *, live_value,
                          new_default, has_override: bool,
                          shipped_defaults=None) -> dict:
    """Classify a single task/field diff.

    `live_value` — what is actually registered right now (the live Cowork
    task's cron/label, or the currently-registered prompt text).
    `new_default` — what core ships as of THIS run (`DEFAULT_SCHEDULES[...]`
    for cron/label, or the freshly-composed bootloader body for prompt).
    `has_override` — whether `workspace.schedule_config[task_id]` carries an
    explicit value for this field (Ruling 1's customization test; always
    False for `field="prompt"`, which has no override surface).
    `shipped_defaults` — the shipped-default TABLE row for this task/field
    (spec "the work" item 3: `schedule_config.SHIPPED_CRON_HISTORY[task_id]`
    for cron): every value core has EVER shipped as the default. When given,
    a divergent live value classifies `silent_apply` ONLY if it appears in
    the table — i.e. the live value is one core itself shipped, so the
    change is provably core-authored (Ruling §0.1). A live value in neither
    the table nor the override store was put there by something other than
    core (an out-of-band Cowork-UI edit, a hand-fix) and is `preserve` —
    silent refresh never guesses about provenance. Pass None ONLY for a
    field with no shipped-default table and no customization surface
    (`prompt`), where any surviving diff is core-authored by construction.

    Returns `{task_id, field, decision, old, new, confirm_required}`.
    `confirm_required` is hardcoded False — see the module docstring.
    """
    if live_value == new_default:
        decision = DECISION_NOOP
    elif has_override:
        decision = DECISION_PRESERVE
    elif shipped_defaults is not None and live_value not in tuple(shipped_defaults):
        # The shipped-default table says core never shipped this value, and
        # no override explains it either: someone other than core set it
        # (out-of-band edit — the Cowork UI, a hand-fix). Not core-authored,
        # so never silently re-anchored (Ruling §0.1). Undo path unchanged:
        # `change my schedule` still moves it any time.
        decision = DECISION_PRESERVE
    else:
        decision = DECISION_SILENT_APPLY
    return {
        "task_id": task_id,
        "field": field,
        "decision": decision,
        "old": live_value,
        "new": new_default,
        # RULING 1 FENCE — there is no fourth outcome. Reintroducing a live
        # condition here (instead of this literal) is the exact regression
        # class BUG_2026-08-16 / BUG_2026-08-19 were filed against; the
        # no-prompt acceptance pin mutates this line to prove it still
        # matters.
        "confirm_required": False,
    }


def apply_schedule_refresh(workspace_root, plan: dict, *, source_skill: str) -> dict:
    """Execute ONE classified plan. `noop` / `preserve` write nothing and
    return `{"applied": False, "event": None}` untouched — Ruling 2 / Ruling
    1, no rewrite, no prompt, no receipt churn. `silent_apply` writes the
    ONE receipt Ruling 4 requires and returns it.

    The caller is responsible for the actual `update_scheduled_task` /
    prompt-write call on `applied: True` — this function's job is the
    classification gate and the receipt, not the Cowork API call, so a
    caller composing multiple field-plans for one task can batch them into
    a single `update_scheduled_task` (cron/label/prompt together) while
    still receipting per field.
    """
    if plan.get("decision") != DECISION_SILENT_APPLY:
        return {"applied": False, "event": None}
    if plan.get("confirm_required"):
        # Unreachable under the current fence (see plan_schedule_refresh) —
        # this guard is what the no-prompt mutation actually trips: mutate
        # `confirm_required` to a live condition and a genuinely-uncustomized
        # semantic change stops applying, which the acceptance pin reads as
        # "the silent-apply fixture no longer wrote its receipt."
        return {"applied": False, "event": None, "blocked_by": "confirm_required"}
    event = log_schedule_refreshed(
        workspace_root, plan["task_id"], plan["field"], plan["old"], plan["new"],
        source_skill=source_skill,
    )
    return {"applied": True, "event": event}


# ---------------------------------------------------------------------------
# Ruling §0.4 — everything receipted
# ---------------------------------------------------------------------------

def log_schedule_refreshed(workspace_root, task_id: str, field: str,
                           old_value, new_value, *, source_skill: str) -> Optional[dict]:
    """THE writer for `schedule_refreshed`. One event per silently-applied
    field. Best-effort by design (RELIABILITY.md) — a registration that
    succeeded must not be reported as failed because its audit event could
    not be appended; returns None on any failure instead of raising.
    """
    event = {
        "type": SCHEDULE_REFRESHED,
        "source_skill": str(source_skill or "").strip() or "command-room-update-bridge",
        "data": {
            "task_id": task_id,
            "field": field,
            "old": old_value,
            "new": new_value,
            "origin": "bridge",
        },
    }
    try:
        from event_gate import append_event

        events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        appended = append_event(
            events_path, event, holder=f"schedule-refresh:{event['source_skill']}"
        )
        return appended[0] if appended else None
    except Exception:  # noqa: BLE001 — the audit event never blocks the apply
        return None


# ---------------------------------------------------------------------------
# Ruling §0.3, "the work" item 2 — the morning-brief announce ledger
# ---------------------------------------------------------------------------

def _ledger_path(workspace_root) -> Path:
    return Path(workspace_root) / ANNOUNCE_LEDGER_RELPATH


def load_announce_ledger(workspace_root) -> dict:
    """The ledger, `{seq_str: entry}` — `{}` on any failure. A corrupt or
    missing ledger degrades to "nothing has announced yet", which re-
    announces: the safe direction (an extra line beats a silently-
    suppressed one), same posture as `task_alarm.load_alarm_ledger`."""
    try:
        raw = _ledger_path(workspace_root).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _already_announced(ledger: dict, key: str) -> bool:
    """THE HONOR FENCE — named separately (mirrors `task_alarm.
    _already_alarmed`) so the mutation suite can remove the call site whole
    and prove the double-announce pin goes red (the acceptance's second
    named mutation, "drop the announce ledger")."""
    return key in ledger


def _record_announced(workspace_root, keys: list[str], *, now: _dt.datetime) -> None:
    if not keys:
        return
    ledger = load_announce_ledger(workspace_root)
    for k in keys:
        ledger[k] = {"announced_at": now.isoformat()}
    horizon = now - _dt.timedelta(days=ANNOUNCE_LEDGER_WINDOW_DAYS)
    pruned = {}
    for k, entry in ledger.items():
        if not isinstance(entry, dict):
            continue
        try:
            announced_at = _dt.datetime.fromisoformat(str(entry.get("announced_at", "")))
        except ValueError:
            continue
        if announced_at < horizon:
            continue
        pruned[k] = entry
    try:
        from atomic_write import atomic_write_json

        path = _ledger_path(workspace_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, pruned)
    except Exception:  # noqa: BLE001
        pass


def _read_pending_refresh_events(workspace_root) -> list[dict]:
    """Every `schedule_refreshed` event on a SEMANTIC field (cron/label —
    `prompt` refreshes are plumbing, never announced per Ruling 3). Reads
    events.jsonl directly — additive-only substrate, cheap full scan mirrors
    task_alarm's own posture (no separate index for a low-volume type)."""
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    out: list[dict] = []
    try:
        with open(events_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict) or ev.get("type") != SCHEDULE_REFRESHED:
                    continue
                data = ev.get("data") or {}
                if data.get("field") not in SEMANTIC_FIELDS:
                    continue
                if ev.get("seq") is None:
                    continue
                out.append(ev)
    except (OSError, FileNotFoundError):
        return []
    out.sort(key=lambda e: e.get("seq", 0))
    return out


def _announce_line(ev: dict) -> str:
    from schedule_config import task_display_name, cron_to_english

    data = ev.get("data") or {}
    name = task_display_name(data.get("task_id", "")) or data.get("task_id", "this")
    field = data.get("field")
    new_value = data.get("new")
    if field == "cron":
        try:
            phrase = cron_to_english(new_value)
        except Exception:  # noqa: BLE001
            phrase = str(new_value)
        return (
            f"Your {name} task now runs {phrase} — a Command Room update moved it; "
            f"say 'change my schedule' if you'd rather move it back."
        )
    return (
        f"Your {name} task's schedule changed to \"{new_value}\" — a Command Room "
        f"update; say 'change my schedule' to adjust."
    )


def announce_lines(workspace_root, *, cap: int = ANNOUNCE_CAP,
                   record: bool = True, now: Optional[_dt.datetime] = None) -> list[str]:
    """SPEC BRIDGESIL1 item 2: one announce line per silent-applied semantic
    change since last brief, capped, render-once (Ruling 3 — "announce,
    don't ask", TASKALARM1's ledger shape). Every pending row is recorded as
    announced (not just the `cap` that render — same posture as
    `task_alarm.dark_surfaces`: the ledger tracks what has been SEEN, the cap
    only bounds what a single render shows), so a burst of changes never
    replays on the next brief just because it overflowed the cap once.
    """
    events = _read_pending_refresh_events(workspace_root)
    if not events:
        return []
    ledger = load_announce_ledger(workspace_root) if record else {}
    pending = [
        (str(ev["seq"]), ev) for ev in events
        if not (record and _already_announced(ledger, str(ev["seq"])))
    ]
    if not pending:
        return []
    lines = [_announce_line(ev) for _, ev in pending[:cap]]
    rest = len(pending) - cap
    if rest > 0:
        word = "change" if rest == 1 else "changes"
        lines.append(
            f"...and {rest} more schedule {word} — say 'change my schedule' for details."
        )
    if record:
        _record_announced(workspace_root, [k for k, _ in pending], now=now or _now_local())
    return lines


__all__ = [
    "SCHEDULE_REFRESHED",
    "ANNOUNCE_LEDGER_RELPATH",
    "DECISION_NOOP",
    "DECISION_SILENT_APPLY",
    "DECISION_PRESERVE",
    "SEMANTIC_FIELDS",
    "prompts_equivalent",
    "plan_schedule_refresh",
    "apply_schedule_refresh",
    "log_schedule_refreshed",
    "load_announce_ledger",
    "announce_lines",
]
