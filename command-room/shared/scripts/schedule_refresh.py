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
import re
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


# ---------------------------------------------------------------------------
# CUT-PLATE (2026-09-06) — THE ONE SENTENCE FOR A STALE REGISTERED PROMPT.
#
# What the registered prompt IS: a thin bootloader that `cat`s the plugin's
# orchestrator file at fire time (scheduled-task-bootloader.md, Step 3), so
# a plugin update reaches every scheduled chat WITHOUT a re-register — the
# content the fire runs is always the installed plugin's. What can go stale
# is the bootloader's own body (its discovery snippet, its diagnostic
# version stamp): Step 1.C of `set up command room schedules` refreshes it
# in place (`update_scheduled_task(taskId, prompt=…)` — no re-register), and
# `update command room` runs that same step on the full-update path.
#
# The v5.28.0 attended test (Step 0) found all seven bootloaders still
# stamped v5.20.0 after two updates: the Code-session run could not see
# Cowork's scheduler store, and the earlier runs had not applied the refresh
# either. When a registered BODY still differs from the composed one AFTER
# an update's Step 1.C readback, the customer needs exactly one sentence
# naming the exact phrase to type — not a per-task list, not a diagnosis,
# and never twice in one run. The three surfaces that can see the drift
# (the update bridge, the health check, the Monday cleanup note) all say
# THIS sentence, so it cannot drift into three.
#
# What the sentence is KEYED TO (CUT-PLATE fix round 1, REVIEW F-1): a real
# body drift — `prompt_body_drift`, the same `prompts_equivalent` compare
# Step 1.C writes on — never the diagnostic version stamp. Step 1.C
# normalizes the stamp away (BRIDGESIL1 Ruling §0.2: a stamp-only diff
# writes NOTHING), so on any release where the bootloader body is unchanged
# a stamp-keyed notice would print on every `update command room`, every
# `health check` and every Monday note forever, and the phrase it names
# could never clear it. `task_watchdog.check_prompt_versions` (the stamp
# read) stays informational.
# ---------------------------------------------------------------------------
STALE_PROMPT_NOTICE = (
    "Your scheduled chats are still running the setup from an older Command "
    "Room. Type `set up command room schedules` once and they'll be brought "
    "current — nothing else changes."
)

#: SCHEDREG1 (SPEC_V5330_FIXLANES §1 MUST 7, D-5): the same fact on a seat
#: where registration is closed — no phrase to type, what still works named.
STALE_PROMPT_ALTERNATIVE = (
    "Your scheduled chats are still running the setup from an older Command "
    "Room; they are brought current the next time they are set up from a "
    "Command Room chat in the Claude desktop app with your Command Room "
    "folder attached, and every chat keeps running meanwhile."
)

_PLUGIN_ROOT = _HERE.parent.parent
BOOTLOADER_TEMPLATE_RELPATH = (
    "skills/enable-command-room-schedules/references/scheduled-task-bootloader.md"
)
ORCHESTRATOR_MAP_RELPATH = (
    "skills/enable-command-room-schedules/references/orchestrator-map.json"
)
BOOTLOADER_BODY_MARKER = (
    "## The bootloader template (everything below this heading is the "
    "registered prompt body)"
)
# The workspace basename Step 1.B bakes into every bootloader (template
# Step 1: `WORKSPACE="$SESSION_DIR/mnt/<WORKSPACE_BASENAME>"`).
_BAKED_BASENAME_RE = re.compile(r'WORKSPACE="\$SESSION_DIR/mnt/([^"/\\]+)"')


def bootloader_template_body(plugin_root=None) -> str:
    """The registered prompt body of the shipped template — everything
    below the canonical marker line, exactly as `enable-command-room-
    schedules` Step 1.B splits it (same marker, same lstrip)."""
    root = Path(plugin_root) if plugin_root else _PLUGIN_ROOT
    full = (root / BOOTLOADER_TEMPLATE_RELPATH).read_text(encoding="utf-8")
    if BOOTLOADER_BODY_MARKER not in full:
        raise ValueError("bootloader template missing its canonical marker line")
    return full.split(BOOTLOADER_BODY_MARKER, 1)[1].lstrip("\n").lstrip()


def orchestrator_filename(task_id: str, plugin_root=None) -> Optional[str]:
    """`orchestrator-map.json` lookup; None for an id that has no chat
    orchestrator (a silent task, or an id this plugin does not know)."""
    root = Path(plugin_root) if plugin_root else _PLUGIN_ROOT
    try:
        omap = json.loads((root / ORCHESTRATOR_MAP_RELPATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    val = omap.get(task_id) if isinstance(omap, dict) else None
    return val if isinstance(val, str) and val else None


#: SCHEDREG1 (SPEC_V5330_FIXLANES §1 MUST 3, D-1) — every placeholder the
#: shipped bootloader template carries. The set is FIXED (the template's owner
#: neither adds nor removes one; `run_schedreg1_test` pins it against the
#: file). The first four are the legacy composition's; the merged composition
#: substitutes all seven and bakes (or removes) the writer pair.
LEGACY_PLACEHOLDERS = ("<TASK_ID>", "<ORCHESTRATOR_FILENAME>",
                       "<WORKSPACE_BASENAME>", "<PLUGIN_VERSION>")
MERGED_PLACEHOLDERS = LEGACY_PLACEHOLDERS + (
    "<WORKSPACE_ABSOLUTE_PATH>", "<CHAT_DISPLAY_NAME>", "<DISCOVER_BLOCK>")
PAIR_PLACEHOLDERS = ("<WRITER_ID>", "<WRITER_DERIVATION>")
BOOTLOADER_PLACEHOLDERS = MERGED_PLACEHOLDERS + PAIR_PLACEHOLDERS


def default_discover_block() -> str:
    """`workspace_access.DISCOVER_BLOCK`, exactly as the bootloader carries it
    (the template indents its first line; the block is substituted as is)."""
    import workspace_access as _wa  # noqa: WPS433 - lazy: import cost

    return _wa.DISCOVER_BLOCK


def substitute_bootloader(body: str, *, task_id: str, orchestrator: str,
                          basename: str, version: str, abs_path: str,
                          display_name: str, discover_block: str) -> str:
    """The seven substitutions, in the order the dev renderer always made
    them. No checks here: `compose_bootloader_body` is the checked door."""
    for placeholder, value in (
        ("<TASK_ID>", task_id),
        ("<ORCHESTRATOR_FILENAME>", orchestrator),
        ("<WORKSPACE_BASENAME>", basename),
        ("<PLUGIN_VERSION>", version),
        ("<WORKSPACE_ABSOLUTE_PATH>", abs_path),
        ("<CHAT_DISPLAY_NAME>", display_name),
        ("<DISCOVER_BLOCK>", discover_block),
    ):
        body = body.replace(placeholder, value)
    return body


def compose_bootloader_body(task_id: str, *, workspace_basename: str,
                            plugin_version: str, plugin_root=None,
                            orchestrator_filename_override: Optional[str] = None,
                            abs_path: Optional[str] = None,
                            display_name: Optional[str] = None,
                            discover_block: Optional[str] = None,
                            writer_id: Optional[str] = None,
                            writer_derivation: Optional[str] = None) -> str:
    """THE composer (SCHEDREG1 MUST 3, D-1): registration, the drift compare,
    the refresh and the dev renderer all compose through here.

    With NONE of the merged inputs (`abs_path`, `display_name`,
    `discover_block`, `writer_id`, `writer_derivation`) it is Step 1.B's
    historical composition, byte for byte: the four substitutions over the
    shipped template body. Raises on an unknown task id, a path-shaped
    basename or one of those four left unsubstituted.

    With ANY merged input it is the merged composition: all seven
    substitutions (`abs_path` required; `display_name` defaults to
    `schedule_config.task_display_name`; `discover_block` to
    `workspace_access.DISCOVER_BLOCK`), then the writer pair baked by
    `writer_identity.bake_pair` - both or neither; a half or malformed pair
    raises `ValueError(BAD_PAIR_LINE)`; no pair removes the pair's lines - and
    it asserts ZERO placeholders remain.
    """
    basename = (workspace_basename or "").strip()
    if not basename or "/" in basename or "\\" in basename:
        raise ValueError(
            f"workspace_basename must be a bare folder basename, got {basename!r}")
    fname = orchestrator_filename_override or orchestrator_filename(task_id, plugin_root)
    if not fname:
        raise KeyError(f"{task_id}: no orchestrator in orchestrator-map.json")
    merged = any(v is not None for v in (abs_path, display_name, discover_block,
                                         writer_id, writer_derivation))
    if merged:
        path = str(abs_path or "").strip()
        if not path:
            raise ValueError(
                f"{task_id}: the merged composition needs the workspace's "
                "absolute path on the customer's computer")
        if display_name is None:
            import schedule_config as _sc  # noqa: WPS433 - lazy

            display_name = _sc.task_display_name(task_id)
        if discover_block is None:
            discover_block = default_discover_block()
        import writer_identity as _wi  # noqa: WPS433 - lazy

        body = substitute_bootloader(
            bootloader_template_body(plugin_root), task_id=task_id,
            orchestrator=fname, basename=basename,
            version=str(plugin_version or ""), abs_path=path,
            display_name=str(display_name), discover_block=str(discover_block))
        body = _wi.bake_pair(body, writer_id, writer_derivation)
        left = [ph for ph in BOOTLOADER_PLACEHOLDERS if ph in body]
        if left:
            raise ValueError(f"{task_id}: unsubstituted {left} after compose")
        return body
    body = (bootloader_template_body(plugin_root)
            .replace("<TASK_ID>", task_id)
            .replace("<ORCHESTRATOR_FILENAME>", fname)
            .replace("<WORKSPACE_BASENAME>", basename)
            .replace("<PLUGIN_VERSION>", str(plugin_version or "")))
    for ph in ("<TASK_ID>", "<ORCHESTRATOR_FILENAME>", "<WORKSPACE_BASENAME>", "<PLUGIN_VERSION>"):
        if ph in body:
            raise ValueError(f"{task_id}: unsubstituted {ph} after compose")
    return body


_BAKED_PATH_RE = re.compile(r'paths=\["([^"]+)"\]')
_BAKED_ID_RE = re.compile(r"CR_WRITER_ID=(acct-[0-9a-f]{12})\b")
_BAKED_DIGEST_RE = re.compile(r"CR_WRITER_DERIVATION=([0-9a-f]{64})\b")


def baked_abs_path(registered_prompt: str) -> Optional[str]:
    """The absolute path a registered bootloader asks for its folder by, read
    off its own folder request; None when the body carries none."""
    m = _BAKED_PATH_RE.search(registered_prompt or "")
    return m.group(1) if m else None


def baked_pair(registered_prompt: str):
    """The writer pair a registered prompt carries, read off its own lines;
    None when it carries none (or only half of one)."""
    text = registered_prompt or ""
    mi = _BAKED_ID_RE.search(text)
    md = _BAKED_DIGEST_RE.search(text)
    return (mi.group(1), md.group(1)) if (mi and md) else None


def seat_pair(workspace, registered_prompt: str = "", *, env=None):
    """The pair THIS seat judges a registered prompt against (MUST 5).

    `schedule_config.silent_prompt_pair` - the one source registration bakes
    from (a process with an account derives; a helper child uses the pair the
    door forwarded). A seat with NO pair at all DECLINES to judge the pair
    (reader F-2/F-3): it composes with the pair the registered prompt already
    carries, so the content is still compared and no pair-less rewrite is ever
    proposed from a seat that could not bake one. `workspace` None (no root)
    declines the same way - never a derivation from a bare name (F-3)."""
    import schedule_config as _sc  # noqa: WPS433 - lazy

    if workspace:
        pair = _sc.silent_prompt_pair(workspace, env)
        if pair:
            return pair
    return baked_pair(registered_prompt)


def seat_compose_inputs(task_id: str, registered_prompt: str, workspace_root,
                        *, abs_path: Optional[str] = None, env=None) -> dict:
    """The merged inputs a compare composes a CHAT with, for this seat.

    `abs_path`: the caller's, else the trigger map's `folders[0]` for this
    task (the path the registration recorded), else the path the registered
    body itself asks for (the compare then declines to judge the path - a
    hand-made or legacy registration has no recorded path to hold it to).
    The pair: `seat_pair`. Display name and discover block are the shipped
    ones. Returns `{"abs_path", "writer_id", "writer_derivation"}`;
    `abs_path` None means nothing could name a path (the body predates the
    merged template - the caller treats that as drift)."""
    path = str(abs_path or "").strip() or None
    if path is None and workspace_root is not None:
        try:
            import schedule_backend as _sb  # noqa: WPS433 - lazy

            row = _sb.read_trigger_map(workspace_root).get(task_id) or {}
            folders = row.get("folders") if isinstance(row, dict) else None
            if isinstance(folders, list) and folders and str(folders[0]).strip():
                path = str(folders[0]).strip()
        except Exception:  # noqa: BLE001 - a compare never raises on a read
            path = None
    if path is None:
        path = baked_abs_path(registered_prompt)
    pair = seat_pair(path if workspace_root is not None else None,
                     registered_prompt, env=env)
    return {"abs_path": path,
            "writer_id": pair[0] if pair else None,
            "writer_derivation": pair[1] if pair else None}


def compose_for_seat(task_id: str, registered_prompt: str, *, basename: str,
                     plugin_version: str, workspace_root, plugin_root=None,
                     abs_path: Optional[str] = None, env=None) -> str:
    """A CHAT's body composed the way THIS seat would register it (MUST 5).
    Raises ValueError when no path can be named (the caller: drift)."""
    inputs = seat_compose_inputs(task_id, registered_prompt, workspace_root,
                                 abs_path=abs_path, env=env)
    if not inputs["abs_path"]:
        raise ValueError(f"{task_id}: no workspace path to compose against")
    return compose_bootloader_body(
        task_id, workspace_basename=basename, plugin_version=plugin_version,
        plugin_root=plugin_root, abs_path=inputs["abs_path"],
        writer_id=inputs["writer_id"],
        writer_derivation=inputs["writer_derivation"])


def baked_basename(registered_prompt: str) -> Optional[str]:
    """The workspace basename a registered bootloader was bound to, read off
    its own Step 1 line; None for a legacy prompt that predates baking."""
    m = _BAKED_BASENAME_RE.search(registered_prompt or "")
    return m.group(1) if m else None


def silent_baked_basename(task_id: str, registered_prompt: str):
    """The workspace basename a registered SILENT prompt was composed for,
    read off the prompt itself; None when it cannot be read.

    THE PATTERN IS DERIVED FROM THE TEMPLATE, never hand-typed: it is built
    from `schedule_config.SILENT_TASKS[task_id]["prompt"]` by escaping the
    text either side of the `{BASENAME}` placeholder. A hand-written regex
    here would be a second copy of the template and would drift from it the
    first time the prompt's opening sentence was reworded — which is exactly
    the class of silent staleness this whole item is about.
    """
    import schedule_config as _sc

    spec = _sc.SILENT_TASKS.get(task_id)
    if not isinstance(spec, dict):
        return None
    tpl = str(spec.get("prompt") or "")
    if "{BASENAME}" not in tpl:
        return None
    head, tail = tpl.split("{BASENAME}", 1)
    # Anchor on a short, stable window either side of the placeholder rather
    # than the whole multi-KB body: the point is to read the basename out of
    # a prompt that may be STALE everywhere else, and a full-body anchor
    # would only match a prompt that needed no refresh at all.
    pat = (re.escape(head[-60:]) + r"(?P<basename>[^\r\n]+?)"
           + re.escape(tail[:20]))
    m = re.search(pat, registered_prompt or "")
    return m.group("basename").strip() if m else None


def prompt_body_drift(task_records, *, plugin_version: str,
                      workspace_basename: Optional[str] = None,
                      workspace_root=None,
                      plugin_root=None) -> list[str]:
    """Task ids whose REGISTERED bootloader body differs from the one this
    plugin composes today, after the stamp is normalized out of both sides —
    exactly the set Step 1.C rewrites (`prompts_equivalent` False). Read
    this off a readback taken AFTER the refresh and it is the set the
    refresh did not reach.

    A stamp-only difference is never drift. Each body is composed against the
    basename the registered prompt itself bakes in, so a moved workspace is
    not mistaken for a stale body (`task_watchdog` owns the binding check);
    `workspace_basename` is the fallback for a prompt that bakes none.

    NOTHING IS SKIPPED IN SILENCE (fix round 1, F-1). A record whose prompt
    yields no basename and has no fallback is reported as DRIFT, not passed
    over: a skipped task is indistinguishable from a current one in the
    returned list, and the caller's next sentence is "all current". Pass
    `workspace_basename` whenever the session knows its own folder — then a
    legacy prompt is composed against it and judged on its content like any
    other, instead of being judged on the fact that it is legacy.

    A CROSS-FOLDER RECORD GETS ONE ANSWER TOO (fix round 2, N-2). Pass
    `workspace_root` and a record the cross-folder guard REFUSES is reported
    as drift, always — never silently current. Without it this function had
    no way to see the folder question at all, so a task registered to
    ANOTHER workspace was judged on its content and answered `not drift`
    when that other folder's body happened to be current, while
    `plan_prompt_refresh` answered `refuse` for the same record: two readers,
    two answers, which is the 2026-09-16 failure this whole item is about.
    Drift is the safe direction and the honest one — this session cannot
    certify a prompt it is not allowed to judge or to rewrite, so the
    caller's "all N current" line must not be sayable over it. The per-row
    verdict is still `plan_prompt_refresh`'s, and it still says `refuse` and
    prints its one sentence; nothing is rewritten from here.

    So, with `workspace_root` supplied: this function and
    `plan_prompt_refresh` return the same verdict for the same record —
    `rewrite` and `refuse` are drift, `current` and `unknown` are not — and
    the lane suite pins that over a matrix of shapes.

    IT COVERS THE SILENT TASKS TOO (SCHEDVIEW1 5.4). This function used to
    open with `if not tid or not orchestrator_filename(tid, plugin_root):
    continue`, which skipped every task with no chat orchestrator — and
    `maintenance`, the silent background dispatcher, is exactly such a task
    and has no row in `orchestrator-map.json`. So the one compare that
    answers "is every registered prompt current?" never looked at the one
    prompt that runs the whole maintenance fire. On 2026-09-16 the seat
    answered "every prompt is current — same 208 lines" while its registered
    Maintenance prompt still typed a command that had been deleted from the
    product (attended test v5.31.0, Step 0 e). One compare, two shapes of
    task: a chat task is composed by `compose_bootloader_body`, a silent task
    by `schedule_config.compose_silent_task_prompt`, and BOTH go through the
    same `prompts_equivalent` normalisation, so a stamp-only difference is
    still not drift on either side.
    """
    import schedule_config as _sc

    drift: list[str] = []
    for rec in task_records or []:
        if not isinstance(rec, dict):
            continue
        tid = rec.get("taskId")
        registered = rec.get("prompt")
        if not tid:
            continue
        if registered is None:
            # BOOT3 (2026-09-19) — THE LISTING CARRIED NO PROMPT TEXT AT ALL.
            #
            # That is "unknown, cannot judge", and it is NOT drift. The merged
            # app's trigger listing may return a trigger with no `prompt` field;
            # the old desktop listing returned a path rather than the body for
            # the same reason. Falling into the F-1 branch below would report
            # every registered chat stale forever on those seats and send the
            # refresher rewriting prompts it has never read.
            #
            # The distinction is `None` versus `""`: a MISSING prompt is
            # unknown, an EMPTY registered body is a real, readable, empty body
            # and stays drift exactly as it was.
            continue
        registered = registered or ""
        if workspace_root is not None:
            # FIX ROUND 2, N-2 — the cross-folder guard runs FIRST here for
            # the same reason it runs first in `plan_prompt_refresh`: a
            # prompt that is not this workspace's is not this session's to
            # judge stale. Refused means "cannot be certified current from
            # here", and that is DRIFT, whatever the other folder's body
            # says.
            if not refresh_workspace_guard(
                    tid, registered, workspace_root,
                    plugin_root=plugin_root)["ok"]:
                drift.append(tid)
                continue
        is_chat = bool(orchestrator_filename(tid, plugin_root))
        if not is_chat and tid not in _sc.SILENT_TASKS:
            # An id this plugin does not know at all — neither a chat nor a
            # registered silent task. Nothing to compose it against.
            continue
        if is_chat:
            basename = (baked_basename(registered)
                        or (workspace_basename or "").strip())
        else:
            basename = (silent_baked_basename(tid, registered)
                        or (workspace_basename or "").strip())
        if not basename:
            # FIX ROUND 1, F-1 — A PROMPT WITH NO READABLE BASENAME IS DRIFT,
            # NEVER A SILENT SKIP.
            #
            # This line used to `continue`, and a skipped task reads as a
            # current one: the caller sees an empty drift list and says "all
            # current" over a prompt nobody looked at. That is the same shape
            # as the defect item 5.4 exists to close, one door further in,
            # and the reviewer reached it through the snippet this lane
            # itself added to the schedules skill.
            #
            # DRIFT is the honest answer, not a hedge. Every basename reader
            # here is derived from the CURRENT template, so a body that is
            # genuinely current always parses: a bootloader bakes its
            # workspace into the Step 1 line, and a silent prompt names it in
            # the anchor sentence. A registered body that cannot yield one is
            # therefore either pre-baking or worded the way an older template
            # worded it — and in both cases it cannot equal what this plugin
            # composes today. Refreshing it is the right act, and it is the
            # act `plan_prompt_refresh` already returns for the same record
            # (`rewrite`), which is what makes the two readers agree.
            drift.append(tid)
            continue
        try:
            if is_chat:
                # SCHEDREG1 MUST 5 (D-1): the chat is composed the way THIS
                # seat registers it - seven substitutions and the pair - so a
                # correctly rendered chat is current, and one rendered for
                # another path or pair is drift, by name.
                composed = compose_for_seat(
                    tid, registered, basename=basename,
                    plugin_version=plugin_version,
                    workspace_root=workspace_root, plugin_root=plugin_root)
            else:
                composed = _compose_silent(tid, basename, workspace_root,
                                           registered)
        except KeyError:
            continue
        except ValueError:
            # A CHAT nothing could name a path for: the body predates the
            # merged template, so it cannot equal what this plugin composes
            # today (the F-1 posture - drift, never a silent skip). A silent
            # task's ValueError (a malformed basename) is skipped, as before.
            if is_chat:
                drift.append(tid)
            continue
        if not prompts_equivalent(composed, registered):
            drift.append(tid)
    return drift


def _compose_silent(task_id: str, basename: str, workspace,
                    registered_prompt: str = "") -> str:
    """A silent task's prompt composed the way REGISTRATION composes it on
    this seat - with the writer pair from `schedule_config.silent_prompt_pair`,
    the one source Step 1.D bakes from (MF-26, merge-fix 2).

    Without the pair here, every maintenance prompt registered with a baked
    pair would read as drifted against a pair-less recompose, and a refresh
    would rewrite it WITHOUT the identity the fire needs to land its receipt.
    With it, a prompt registered under this seat's pair is current, and one
    registered under any other pair is drift - which is the honest answer:
    this seat would register it differently."""
    import schedule_config as _sc

    # SCHEDREG1 MUST 5 / reader F-3: `seat_pair` - with no workspace root, or
    # on a seat with no pair of its own, the compare DECLINES to derive and
    # judges the content under the pair the registered prompt carries.
    pair = seat_pair(workspace, registered_prompt) or (None, None)
    # T2 CB-4 (REVIEW_T2_FIRE1 N-1): the path registration composed with - the trigger
    # map's `folders[0]` for this task, else the path the body asks for - so a body
    # registered WITH its folder request reads as current, never as drift against a
    # path-less recompose. No path anywhere: today's text, judged as before.
    # With no workspace root (the MF-26 no-root legs, a helper child on the
    # device) the trigger map is out of reach and the path is the one the body
    # itself asks for (`baked_abs_path`); `seat_compose_inputs` does both.
    try:
        path = seat_compose_inputs(task_id, registered_prompt, workspace).get("abs_path")
    except Exception:  # noqa: BLE001 - a compare never raises on a read
        path = None
    return _sc.compose_silent_task_prompt(
        task_id, basename, writer_id=pair[0], writer_derivation=pair[1],
        workspace_path=(str(path or "").strip() or None))


# ---------------------------------------------------------------------------
# SCHEDVIEW1 5.4 — a refresh writes its receipt where the refresh happened
# ---------------------------------------------------------------------------

#: The one sentence a cross-folder refresh says instead of acting. Scheduled
#: tasks are MACHINE-level: one registry serves every workspace on the box, so
#: a session mounted on folder A can see — and rewrite — the task that belongs
#: to folder B. On 2026-09-15 that happened: a session on a scratch copy
#: refreshed the live seat's Maintenance prompt, and the receipt went nowhere,
#: because the writer was pointed at the copy and the copy was not the
#: workspace that changed (attended test v5.31.0, Step 0 e — "done from the
#: copy, which refused to write into the copy").
CROSS_FOLDER_REFUSAL = (
    "That scheduled task belongs to a different workspace folder, so I have "
    "left it alone. Open that workspace and say `set up command room "
    "schedules` there, and the change will be recorded where it happened."
)

#: SCHEDREG1 (SPEC_V5330_FIXLANES §1 MUST 7, D-5): the same refusal on a seat
#: where registration is closed - no phrase to type.
CROSS_FOLDER_ALTERNATIVE = (
    "That scheduled task belongs to a different workspace folder, so I have "
    "left it alone; it is changed from a Command Room chat in the Claude "
    "desktop app with that folder attached, where the change is recorded."
)


def cross_folder_line(workspace_root=None) -> str:
    """The cross-folder refusal through the one invite producer."""
    try:
        from schedule_config import setup_invite_line
    except ImportError:  # pragma: no cover - shipped beside this module
        return CROSS_FOLDER_REFUSAL
    try:
        return setup_invite_line(None, workspace_root=workspace_root,
                                 invite=CROSS_FOLDER_REFUSAL,
                                 alternative=CROSS_FOLDER_ALTERNATIVE)
    except Exception:  # noqa: BLE001 - a sentence never fails on a read
        return CROSS_FOLDER_ALTERNATIVE


def refresh_workspace_guard(task_id: str, registered_prompt: str,
                            workspace_root, *, plugin_root=None) -> dict:
    """Does this registered task belong to the workspace this session is
    mounted on? `{"ok", "registered_basename", "session_basename", "line"}`.

    M's default (SPEC_FIXTRAIN_v5310 ruling 3): a cross-folder refresh is
    REFUSED rather than performed with the receipt written somewhere else. A
    scheduled task is machine-level and a session editing another workspace's
    schedule with no receipt anywhere is the condition the finding is about;
    refusing is the only outcome that leaves both workspaces honest, and the
    sentence tells the reader exactly where to go to get it done.

    `ok` is True — do the refresh — when the two basenames agree, and also
    when the registered prompt bakes no basename at all (a legacy prompt
    names no workspace, so there is no other workspace to be wrong about).
    """
    import schedule_config as _sc

    session = Path(workspace_root).name.strip()
    if task_id in _sc.SILENT_TASKS:
        registered = silent_baked_basename(task_id, registered_prompt)
    else:
        registered = baked_basename(registered_prompt)
    registered = (registered or "").strip()
    ok = (not registered) or registered == session
    return {"ok": ok, "registered_basename": registered or None,
            "session_basename": session,
            "line": "" if ok else cross_folder_line(workspace_root)}


def plan_prompt_refresh(task_id: str, *, registered_prompt: str,
                        workspace_root, plugin_version: str,
                        plugin_root=None) -> dict:
    """THE one decision for one registered prompt (SCHEDVIEW1 5.4):
    `{"action", "task_id", "composed", "basename", "line", "guard"}` where
    `action` is `rewrite` | `current` | `refuse` | `unknown`.

    It is one function because the 2026-09-16 failure was two readers giving
    two answers about one task twenty minutes apart — the seat said "every
    prompt is current" and the scratch said "genuinely stale, not a stamp
    diff" — and neither was reading the other's rule. Chat tasks and silent
    tasks compose differently and are compared identically; the cross-folder
    guard runs FIRST, because a prompt that is not this workspace's is not
    this session's to judge stale.

    `unknown` is an id this plugin cannot compose a prompt for at all. It is
    never drift and never a refusal — there is nothing to compare.
    """
    import schedule_config as _sc

    if registered_prompt is None:
        # BOOT3 (2026-09-19) — no prompt text came back from the listing, so
        # there is nothing to compare. `unknown` is the existing answer for
        # exactly this shape: never drift, never a refusal. See the twin branch
        # in `prompt_body_drift`.
        return {"action": "unknown", "task_id": task_id, "composed": None,
                "basename": None, "line": "", "guard": None}

    guard = refresh_workspace_guard(task_id, registered_prompt,
                                    workspace_root, plugin_root=plugin_root)
    if not guard["ok"]:
        return {"action": "refuse", "task_id": task_id, "composed": None,
                "basename": guard["registered_basename"],
                "line": guard["line"], "guard": guard}
    is_chat = bool(orchestrator_filename(task_id, plugin_root))
    if not is_chat and task_id not in _sc.SILENT_TASKS:
        return {"action": "unknown", "task_id": task_id, "composed": None,
                "basename": None, "line": "", "guard": guard}
    basename = (guard["registered_basename"] or guard["session_basename"])
    try:
        composed = (compose_for_seat(
            task_id, registered_prompt, basename=basename,
            plugin_version=plugin_version, workspace_root=workspace_root,
            plugin_root=plugin_root)
            if is_chat else
            _compose_silent(task_id, basename, workspace_root,
                            registered_prompt))
    except KeyError:
        return {"action": "unknown", "task_id": task_id, "composed": None,
                "basename": basename, "line": "", "guard": guard}
    except ValueError:
        if is_chat:
            # No path could be named for this chat (a pre-merged body): the
            # same answer `prompt_body_drift` gives it - it needs rewriting,
            # and the registering seat composes the body (MUST 4).
            return {"action": "rewrite", "task_id": task_id, "composed": None,
                    "basename": basename, "line": "", "guard": guard}
        return {"action": "unknown", "task_id": task_id, "composed": None,
                "basename": basename, "line": "", "guard": guard}
    current = prompts_equivalent(composed, registered_prompt)
    return {"action": "current" if current else "rewrite",
            "task_id": task_id, "composed": composed, "basename": basename,
            "line": "", "guard": guard}


def stale_prompt_notice(drift, findings=None, *, tools=None,
                        workspace_root=None, env=None) -> str:
    """The ONE line to say when `prompt_body_drift` is non-empty after the
    readback — a registered body the refresh did not reach; `""` when every
    body is current, whatever the stamps say. `findings` (the
    `task_watchdog.check_prompt_versions` stamp read) is accepted for the
    record and never decides: passing it AS the drift raises, so a caller
    cannot key the sentence to the stamp by accident (REVIEW F-1 — that was
    the repeating nag). Always the same sentence, whatever the count: one
    sentence per run, never per task."""
    if any(isinstance(d, dict) for d in (drift or [])):
        raise TypeError(
            "stale_prompt_notice decides on prompt_body_drift (task ids), "
            "never on check_prompt_versions findings — the stamp is informational")
    if not drift:
        return ""
    # SCHEDREG1 MUST 7 (D-5): the invitation only where this seat could act
    # on it — `schedule_config.setup_invite_line` is the one producer.
    try:
        from schedule_config import setup_invite_line
    except ImportError:  # pragma: no cover - shipped beside this module
        return STALE_PROMPT_NOTICE
    try:
        return setup_invite_line(tools, workspace_root=workspace_root,
                                 env=env, invite=STALE_PROMPT_NOTICE,
                                 alternative=STALE_PROMPT_ALTERNATIVE)
    except Exception:  # noqa: BLE001 - a sentence never fails on a read
        return STALE_PROMPT_ALTERNATIVE


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
    "STALE_PROMPT_NOTICE",
    "BOOTLOADER_TEMPLATE_RELPATH",
    "ORCHESTRATOR_MAP_RELPATH",
    "BOOTLOADER_BODY_MARKER",
    "bootloader_template_body",
    "orchestrator_filename",
    "compose_bootloader_body",
    "baked_basename",
    "prompt_body_drift",
    "stale_prompt_notice",
    "plan_schedule_refresh",
    "apply_schedule_refresh",
    "log_schedule_refreshed",
    "load_announce_ledger",
    "announce_lines",
]
