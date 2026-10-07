#!/usr/bin/env python3
"""
Scheduled-task fired-recency watchdog (Phase 3 Reliability, W1 — 2026-07).

Scheduled tasks fail silently at four joints and no symptom reaches the
user: registration never happened (onboarding registers zero tasks by
design), silent tasks never cleared Cowork's first-fire permission gate,
the laptop slept through the fire window, or a platform fault (folder
rename, VHD cache, connector drop) broke the task path. A customer named
the purchase-driving pain verbatim on a 2026-06 call: "I don't know if
it's lagging or if dispatch forgot." This module is the detector for all
four.

DESIGN RULES (from the owning spec + Cowork decisions):

- **Enforcement binds to substrate artifacts (audit events), never emitted
  text** — the Bug #98 lesson generalized. The watchdog READS receipts
  (`pack_run` / `sent_reconcile` / `cleanup_run` / report events); it does
  not trust narration. Scheduler metadata (`lastRunAt` from
  `list_scheduled_tasks`), when the caller provides it, is used as a
  SECONDARY signal — a fresh `lastRunAt` with a stale receipt is its own
  finding (`receipt_gap`: the task fired but wrote nothing — the
  render-without-write class the R10 transcript self-audit chases).
- **ONE clock everywhere — INCLUDING WHAT IS RENDERED — and which clock
  that is depends on the seat (TZ1, 2026-09-20).** `clock_policy` decides
  it once: on a LEGACY desktop seat it is the machine clock, exactly as R8
  settled it live on 2026-07-01 (machine=Mountain, workspace=Pacific) and
  exactly as this module has always behaved; on a MERGED/CLOUD seat it is
  the WORKSPACE zone, because "the machine" there is a container that read
  PDT talking to a sandbox VM on UTC about a cron the platform evaluates in
  UTC — three clocks, none of them the customer's, and the one that matched
  on 2026-09-19 matched by coincidence. All lateness math here reads
  `_now_local` / `_to_local_naive`, and so does every string `_human_time`
  emits, so the branch decision and the rendered sentence can never end up
  on different clocks. What has NOT changed: conversion still happens ONCE,
  at registration/change time (LATETZ, 2026-07-28 — "workspace TZ is
  presentation-only" never meant "present in workspace TZ"), a value
  arriving here is already in this seat's clock and is never hopped again,
  and `tz.py` remains for upstream CONNECTOR timestamps, a different
  problem.
- **One plain-English sentence per problem, only when something is wrong**
  (Rule 28 posture). `plain_english_lines()` is the single formatter every
  surface uses. Lines state FACTS + the one action — never a fabricated
  cause (v4.5.2 R3, superseding W3's sleep-first wording: the dogfood
  logged four invented "computer was likely asleep" narratives in one day,
  F-10/F-43/F-47; the generic common-causes education lives in
  system-health's self-serve list, framed as possibilities).
- **The watchdog itself must be un-silent-killable:** it runs inside
  surfaces that already fire (morning-brief step, cleanup Monday note) plus
  the on-demand `system health` trigger (skills/system-health) — never as
  yet another silent task.

TRUTH RULES (v4.5.2 R3 — F-43 P1a/P1b/P2c, F-40, the F-10 lie catalog):

- **Never assert fire history without a receipt.** A registered task with
  zero receipts is `never_fired` ("hasn't had its first run yet — next fire
  is [time]"), never part of "ran on their normal schedule" (F-43 P1a
  invented run history for two tasks registered that morning).
- **Late serves are read, not ignored.** `late_signals()` reads the
  `late_fire` events + `fired_via`/`late_tier` receipt fields R2 writes; a
  task whose newest fire was a catch-up carries `caught_up: True` and is
  reported AS a dated catch-up ("caught up Wednesday 12:20 AM"), never as
  "normal schedule" (F-43 P1b/P2c).
- **Internal consistency is code, not prose.** `health_verdict()` partitions
  every task into exactly ONE bucket (problem / caught-up / first-run-
  pending / on-schedule) and computes the summary counts from the
  partition — a task in any warning can never simultaneously count inside
  "everything's running" (F-43's self-contradiction).
- **Vantage before verdict (F-40).** An empty scheduler registry with a
  substrate full of registration history + run receipts means THIS CHAT
  CANNOT SEE THE SCHEDULER (cloud/remote session, or another machine) —
  `detect_registry_vantage()` returns that finding and the verdict becomes
  "I can't see your scheduler from this chat", never the false total-outage
  "nothing is registered" (whose named fix would double-register
  everything).

STATUSES per task:

  ok               — receipt within tolerance of the expected cadence.
  late             — fired before, then stopped: the last receipt misses
                     the two most recent expected fires (>=2 missed — one
                     missed fire is holiday/one-off tolerance, R5).
  never_fired      — registered, no receipt ever, registration recent
                     (< 3 weekdays): first fire simply hasn't landed yet.
  never_authorized — registered, no receipt ever, and >= 3 weekdays have
                     passed since registration: almost certainly the
                     Cowork first-fire permission gate was never cleared
                     (the silent-task ghost class W2's ritual closes).
  not_registered   — enabled in the merged schedule config but absent from
                     the registered set. Later-add tasks (not first-install)
                     are EXPECTED here and stay quiet — change-schedule R1
                     owns that render; first-install tasks missing from the
                     registered set are real breakage.
  platform_declined — the scheduler's own run record says the PLATFORM
                     refused the fire (a cap, a quota, a throttle: the
                     `global_limit` shape this PC's pre-merge store recorded
                     six times on 2026-09-08, SPEC_MERGEFIX1 F13) and no
                     receipt landed within dispatch grace of that decline.
                     Nothing on this computer is broken and nothing the
                     customer owns can fix it, so the sentence names the
                     platform's limit and the phrase that runs the work now
                     — never the machine (R-M1-6, ruled 2026-09-19).

W5 (unlocked surfaces — status): the Tue/Thu `waiting-on chase` shipped
Phase 4 (2026-07-02) as orchestrator-commitments Phase 3.8 — its gates
(Stage D kinds split, Stage E counterparty receipts) merged, and it rides
the existing commitments task (nothing new registered). Still gated: the
day-1/week-1 lifecycle one-shots (cut in v4.1.0 for registration
unreliability) — safe to reintroduce as opt-ins once this watchdog can
verify fires. Nothing registers from this module.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from event_time import event_dt, parse_ts  # noqa: E402
from schedule_config import (  # noqa: E402
    DEFAULT_SCHEDULES,
    FIRST_INSTALL_TASK_IDS,
    SILENT_TASKS,
    CronParseError,
    load_schedule_config,
    parse_cron,
    serving_task_ids,
    task_display_name,
)

# Analytical views owned by insight-generator — weekly-insights' pre-v4.5.2
# fires wrote no audit event, so the view-file mtimes remain a FALLBACK
# freshness signal forever (append-only history: old installs never gain
# receipts retroactively). v4.5.2+ fires also write a pack_run receipt.
_INSIGHT_VIEWS = (
    "TIMELINE.md",
    "RELATIONSHIPS.md",
    "COMMITMENT_AGING.md",
    "DORMANT.md",
    "THEMES.md",
)

# Receipt spec per task — DERIVED from the receipt contract
# (`shared/scripts/receipts.py`, v4.5.2 R1), the single source of truth for
# receipt types + legacy-spelling normalization. This dict keeps the
# watchdog's historical shape for its consumers; the matcher itself is
# `receipts.receipt_task_id` (all legacy spellings — cr-* prefixes,
# underscore kinds, kind-only payloads — parse forever).
# `views` marks the file-mtime fallback receipt (weekly-insights).
from receipts import (  # noqa: E402
    RECEIPT_TYPES as _RECEIPT_TYPES,
    get_late_tier as _get_late_tier,
    last_receipt_times as _last_receipt_times,
    normalize_fired_via as _normalize_fired_via,
    normalize_task_id as _normalize_task_id,
    receipt_task_id as _receipt_task_id,
)

RECEIPT_SPECS: dict[str, dict] = {
    tid: {"types": set(spec["types"]), "match": None}
    for tid, spec in _RECEIPT_TYPES.items()
}
RECEIPT_SPECS["weekly-insights"]["views"] = _INSIGHT_VIEWS

# MAINT1: the five pre-MAINT1 silent taskIds live on in RECEIPT_SPECS as JOB
# ids — check_tasks no longer reports them as tasks (they left
# DEFAULT_SCHEDULES), but check_maintenance_jobs reads their receipts per-job
# against the nominal crons in maintenance_dispatcher.MAINTENANCE_JOBS.

# The version stamp the bootloader template carries as of Phase 3 (W4).
# Registered prompts older than the stamp's introduction simply don't have
# one — reported as "unstamped", which is informational, not a failure.
_VERSION_STAMP_RE = re.compile(r"plugin-version:\s*v?([0-9][0-9A-Za-z.\-]*)")

_AUTHORIZATION_GRACE_WEEKDAYS = 3

# COPY1 (SPEC_NIGHTM1_LANES §7 item 5, 2026-09-19) — what `never_authorized`
# MEANS on a merged seat. The detector is unchanged ("registered, no receipt,
# three weekdays on"); what changed underneath it is the cause. There is no
# one-time permission click any more: a scheduled fire runs in the cloud and
# asks this computer for the workspace folder itself, and it waits on ONE
# Allow (or on the folder being trusted — gap analysis §0.7). So the sentence
# names the card that is actually open, and it never names a sidebar, a
# section, or the id of the session that ran (F16: the health line printed
# `data.machine`, a per-session id that says nothing true about a machine).
NEVER_AUTHORIZED_LINE = (
    "<Chat> is registered but has never reached your workspace — the folder "
    "request is still waiting for one Allow on your computer."
)


def never_authorized_line(display_name: str) -> str:
    """`NEVER_AUTHORIZED_LINE` with the chat's own display name in it."""
    return NEVER_AUTHORIZED_LINE.replace("<Chat>", str(display_name or "").strip()
                                         or "This chat")


def seat_backend(workspace_path) -> str:
    """`"cloud"` when this workspace's schedules live in the account-level
    trigger registry, `"legacy"` otherwise — read from the `triggers` map
    `schedule_backend.record_trigger_map` writes into workspace_config.json.

    A map with rows in it is the only positive evidence that registration
    happened the merged way; everything else (an old desktop seat, a fresh
    workspace, an unreadable config) reads legacy, which is the conservative
    answer: the legacy sentence is true on both seats, the cloud one is only
    true on a cloud seat.
    """
    if not workspace_path:
        return "legacy"
    try:
        cfg = read_workspace_config(workspace_path)
    except Exception:  # noqa: BLE001 — a sentence never fails on a config read
        return "legacy"
    triggers = cfg.get("triggers") if isinstance(cfg, dict) else None
    return "cloud" if isinstance(triggers, dict) and triggers else "legacy"

# ---------------------------------------------------------------------------
# TRUTH1 (SPEC_NIGHTM2_LANES §3; SPEC_MERGEFIX1 §4 TRUTH1, 2026-09-20) — THE
# POST-MERGE SENTENCES, EACH SPELLED ONCE.
#
# F16 is why this block exists: on a merged seat `health check` printed "I
# can't see your scheduler from this chat … For a full check, open a local
# (non-cloud) chat on the computer where Command Room is set up. (on
# claude-f352)". Three wrongs in one sentence — it named a chat that no longer
# exists on a merged build, it vouched "your tasks look alive" from a receipt
# another machine wrote, and it printed `data.machine`, which F8 established
# is a per-SESSION id that says nothing true about a machine and is a raw id
# on a customer surface. Every sentence below is a fact plus the one action a
# customer can actually take by typing it (R3), and NONE of them renders
# `data.machine` in any state — the leak fence carries the `claude-XXXX` token
# as its decoy (`surface_leak_patterns`, `sandbox_session_id`).
#
# D-8: the typed-phrase vocabulary has ONE home (`schedule_config.TYPED_PHRASES`
# / `schedule_backend.GATE_LINE`). `TRUTH_LINE` carries the spec's sentence
# VERBATIM rather than re-deriving it, and `run_truth1_test` pins every phrase
# inside it against that tuple, so the two can never drift apart without a red.

#: The `check` ids `detect_registry_vantage` can return. The first is
#: unchanged from F-40 (a legacy seat whose scheduler answered with an empty
#: list); the other two are this lane's.
VANTAGE_REGISTRY = "registry_vantage"
VANTAGE_CLOUD = "registry_vantage_cloud"
VANTAGE_UNREACHABLE = "scheduler_unreachable"

#: SPEC_MERGEFIX1 §4 TRUTH1 behaviour 2, VERBATIM. `<when>` is the only hole,
#: and the whole "— the last one ran <when>" clause drops when there is no run
#: record to name (never invent one: R3).
#:
#: HEAL1 imports this name behind `try/except ImportError` (spec §4 (c)) — the
#: plate's TRUTH line is this sentence, composed HERE, never re-typed there.
TRUTH_LINE = (
    "Your scheduled chats and background maintenance stopped running after "
    "the Claude app update — the last one ran <when>. Everything still works "
    "when you ask: say `morning briefing`, `end of day`, `weekly recap` on "
    "Fridays, `staff meeting`, and `run maintenance` once a day. Command Room "
    "will tell you when schedules are back."
)

#: The closing promise `TRUTH_LINE` ends on — the same sentence `GATE_LINE`
#: closes with, which is what makes the swap below a SUBSTITUTION and not a
#: second paragraph. Pinned against `schedule_config.gate_line()` by the suite.
GATE_PROMISE = "Command Room will tell you when schedules are back."

#: SPEC_NIGHTM2 §3 amendment (b): the second half `TRUTH_LINE` gains once the
#: schedule backend exists AND that backend's own registration gate is open.
#: While the gate is shut, the promise above stands instead — telling a
#: customer to run a setup that will refuse them is not an action, it is a
#: round trip (§0.9; COPY1's gate).
REOPEN_LINE = ("Say `set up command room schedules` to turn them back on in "
               "this version.")

#: Behaviour 3 — registry unreachable, but a SCHEDULED receipt landed
#: recently. On M's own seat that receipt came from the un-merged PC, so the
#: honest closer is the fact ("another computer"), never "open a local chat".
ANOTHER_COMPUTER_LINE = "Another computer is still running them."

#: Amendment (e) / gap analysis §5.3 — the cloud branch. The scheduler ANSWERED
#: (an empty `list_triggers`) and the workspace carries registration history:
#: the schedules are real, they live in the older desktop app, and this account
#: cannot see them. Never "open a local (non-cloud) chat" — on a merged build
#: there is no such chat to open.
CLOUD_VANTAGE_LINE = (
    "Your scheduled chats were set up in the older desktop app, so they "
    "aren't visible here."
)
CLOUD_VANTAGE_MOVE = ("Say `set up command room schedules` to move them to "
                      "this version.")

#: R-M1-6 — the platform declined the fire. The customer's computer is fine,
#: the schedule is fine, and there is nothing to press: the limit is the
#: platform's and it clears on its own. So the sentence states the fact and
#: hands over the phrase that does the work NOW.
PLATFORM_DECLINED_LINE = (
    "Your <Chat> didn't run — the Claude app reached its own usage limit and "
    "declined the run. It clears on its own; <action> if you want it now."
)

#: The words a customer types to get one chat on demand, per task id. Every
#: value is pinned against `schedule_config.TYPED_PHRASES` (one home, D-8); a
#: task with no phrase of its own gets the honest fallback rather than an
#: invented command.
TYPED_PHRASE_BY_TASK: dict[str, str] = {
    "morning-brief": "morning briefing",
    "inbox": "inbox triage",
    "end-of-day": "end of day",
    "friday-wrap": "weekly recap",
    "staff-meeting": "staff meeting",
    "maintenance": "run maintenance",
}
_NO_PHRASE_ACTION = "for it by name"


def typed_phrase(task_id: str) -> Optional[str]:
    """The back-ticked phrase that runs `task_id` on demand, or None when the
    chat has no phrase of its own. Never invent one — a command a customer
    types and Command Room does not answer is worse than no instruction."""
    phrase = TYPED_PHRASE_BY_TASK.get(str(task_id or "").strip())
    return f"`{phrase}`" if phrase else None


def typed_action(task_id: str, *, capital: bool = False) -> str:
    """The ACTION CLAUSE a sentence embeds: "say `morning briefing`", or the
    honest fallback "ask for it by name". Renderers call this — never an
    inline literal, so the six phrases have exactly one home."""
    phrase = typed_phrase(task_id)
    verb = ("Say" if capital else "say") if phrase else ("Ask" if capital else "ask")
    return f"{verb} {phrase}" if phrase else f"{verb} {_NO_PHRASE_ACTION}"


def platform_declined_line(display_name: str, task_id: str = "") -> str:
    """`PLATFORM_DECLINED_LINE` with the chat's spoken name and its action."""
    name = str(display_name or "").strip() or "scheduled chat"
    return (PLATFORM_DECLINED_LINE
            .replace("<Chat>", name)
            .replace("<action>", typed_action(task_id)))


def _gate_allowed(backend_id: str = "", *, workspace_root=None,
                  env=None) -> bool:
    """Could THIS seat register on the backend it actually uses?

    D-5's shape: on a tree without `schedule_backend` the conservative answer
    is "shut", which is what the shipped config says on a merged seat — the
    right way to be wrong (a promise that is merely withheld costs a sentence;
    a promise that cannot be kept costs the customer's trust in every other
    line this module writes).

    SCHEDREG1 (SPEC_V5330_FIXLANES §1 MUST 7, D-5): the answer comes from the
    ONE invite producer, `schedule_config.setup_invite_allowed` — on the
    cloud backend the gate must be open AND this process must be able to
    name its writer; the legacy backend is always open, as before.
    """
    try:
        from schedule_config import setup_invite_allowed
    except ImportError:
        return False
    try:
        return bool(setup_invite_allowed(
            None, workspace_root=workspace_root,
            backend_id=backend_id or "cloud", env=env))
    except Exception:  # noqa: BLE001 — a sentence never fails on a gate read
        return False


def _scheduler_reachable(tools, workspace_root=None) -> bool:
    """Is a scheduler of ANY kind reachable from this session, AND is its own
    registration gate open? One call, asked of the code (`scheduler_availability`
    is the one place that knows), never of a tool name.

    A tree without the helper answers False, which is the conservative
    direction for every caller here: it withholds an invitation rather than
    issuing one that cannot be honoured.
    """
    try:
        from schedule_config import scheduler_availability
    except ImportError:  # pragma: no cover — the helper ships with the module
        return False
    try:
        return bool(scheduler_availability(
            tools, workspace_root=workspace_root).get("available"))
    except Exception:  # noqa: BLE001 — a sentence never fails on a guard read
        return False


def _vantage_gate_allowed(backend_id=None, tools=None,
                          workspace_root=None) -> bool:
    """May the stopped sentence invite a setup? (fix round 1, REVIEW F-1.)

    The invitation is only ever true when a scheduler is REACHABLE and its own
    gate is open, so when the caller hands over this session's tool names that
    is the whole answer — `scheduler_availability` asks both halves.

    With no tool list there is still one thing this branch knows for certain:
    it IS the no-scheduler state, so the legacy desktop scheduler is not in
    this session by definition and its always-open gate cannot be the one that
    answers. `_gate_allowed(seat_backend(...))` was the original defect (the
    book replay's invitation on a seat with no scheduler); a caller-supplied
    `backend="legacy"` is the SAME claim arriving by argument instead of by
    derivation, and it is refused the same way. The only backend this version
    could ever reach from here is the cloud one.
    """
    if tools is not None:
        return _scheduler_reachable(tools, workspace_root)
    if backend_id and str(backend_id).strip().lower() != "cloud":
        return False
    return _gate_allowed("cloud", workspace_root=workspace_root)


# ---------------------------------------------------------------------------
# SCHEDREG1 (SPEC_V5330_FIXLANES §1 MUST 7) — the three watchdog sentences that
# name the setup phrase, each composed through the one invite producer
# (`schedule_config.setup_invite_line`). The invitation wording is today's,
# byte for byte; the alternative names what still works and invites nothing.
# ---------------------------------------------------------------------------

FRESH_INSTALL_INVITE = (
    "Your scheduled chats and background tasks aren't set up yet — "
    "say 'set up command room schedules' to get started."
)
FRESH_INSTALL_ALTERNATIVE = (
    "Your scheduled chats and background tasks aren't set up yet — they are "
    "set up from a Command Room chat in the Claude desktop app with your "
    "Command Room folder attached, and every chat still runs when you ask "
    "for it by name."
)
FOLDER_RENAMED_INVITE = (
    "Your workspace folder looks like it was renamed (I have it as "
    "'<stored>', but the folder is '<actual>') — "
    "say 'set up command room schedules' once and I'll re-bind everything."
)
FOLDER_RENAMED_ALTERNATIVE = (
    "Your workspace folder looks like it was renamed (I have it as "
    "'<stored>', but the folder is '<actual>') — your scheduled chats are "
    "re-bound the next time they are set up from a Command Room chat in the "
    "Claude desktop app with your Command Room folder attached."
)
MISSING_TASK_INVITE = (
    "Your <name> task is missing from the schedule — say "
    "'set up command room schedules' to restore it."
)
MISSING_TASK_ALTERNATIVE = (
    "Your <name> task is missing from the schedule — it comes back the next "
    "time your scheduled chats are set up from a Command Room chat in the "
    "Claude desktop app with your Command Room folder attached."
)


def _invite(workspace_root, invite: str, alternative: str, *, tools=None,
            env=None, backend=None) -> str:
    """`schedule_config.setup_invite_line` with this module's two wordings.
    A tree without the producer keeps today's invitation (legacy parity).
    `backend` is the seat's backend the caller already resolved (SCHEDREG1
    review B-1): without it a new workspace with no trigger map read as
    legacy and the invitation printed on a cloud seat with no account."""
    try:
        from schedule_config import setup_invite_line
    except ImportError:  # pragma: no cover - shipped beside this module
        return invite
    try:
        return setup_invite_line(tools, workspace_root=workspace_root,
                                 backend_id=backend, env=env, invite=invite,
                                 alternative=alternative)
    except Exception:  # noqa: BLE001 - a sentence never fails on a read
        return alternative


def fresh_install_line(workspace_root=None, *, tools=None, env=None,
                       backend=None) -> str:
    """The fresh-install summary line (health check), through the producer."""
    return _invite(workspace_root, FRESH_INSTALL_INVITE,
                   FRESH_INSTALL_ALTERNATIVE, tools=tools, env=env,
                   backend=backend)


def folder_renamed_line(stored: str, actual: str, workspace_root=None, *,
                        tools=None, env=None, backend=None) -> str:
    """The folder-renamed line, through the producer."""
    line = _invite(workspace_root, FOLDER_RENAMED_INVITE,
                   FOLDER_RENAMED_ALTERNATIVE, tools=tools, env=env,
                   backend=backend)
    return line.replace("<stored>", str(stored)).replace("<actual>", str(actual))


def missing_task_line(name: str, workspace_root=None, *, tools=None,
                      env=None, backend=None) -> str:
    """The missing-first-install-task line, through the producer."""
    line = _invite(workspace_root, MISSING_TASK_INVITE,
                   MISSING_TASK_ALTERNATIVE, tools=tools, env=env,
                   backend=backend)
    return line.replace("<name>", str(name))


def truth_line(when: Optional[str] = None, *, gate_allowed: bool = False) -> str:
    """The stopped sentence (SPEC_MERGEFIX1 §4 TRUTH1 behaviour 2), composed
    in ONE place so every surface that says it says the same words.

    `when` is a already-humanised time ("Tuesday 7:00 AM"); None drops the
    clause rather than inventing a date.
    """
    line = TRUTH_LINE
    if when:
        line = line.replace("<when>", when)
    else:
        line = line.replace(
            " — the last one ran <when>.", ".")
    if gate_allowed:
        line = line.replace(GATE_PROMISE, REOPEN_LINE)
    return line


_FIRE_GRACE = _dt.timedelta(minutes=90)  # dispatch jitter + long fires

# MAINTGAP1 (2026-08-27) — the maintenance dispatcher's own min-gap guard
# (`maintenance_dispatcher._check_min_gap`) makes the LATER twin of any pair
# of nominal fire instants closer than this many minutes a guaranteed,
# receipted no-op (first-fire-wins). `effective_fires` below is the
# watchdog-side mirror of that same threshold, kept as ONE literal here and
# imported by the dispatcher rather than duplicated — see that module's own
# comment on its import line.
MIN_GAP_MINUTES = 20


def _now_local(workspace_root=None) -> _dt.datetime:
    """Naive USER-local now — the clock this seat's schedules evaluate in.

    On a legacy seat that is the machine clock, byte for byte as it always
    was. On a merged/cloud seat there is no machine: `clock_policy` returns
    the WORKSPACE zone instead, because the container's PDT and the sandbox
    VM's UTC are both accidents of where the fire happened to run (TZ1,
    gap analysis §0.18). CLOCK1 is unchanged either way — the INSTANT is
    still corroborated against the workspace ledger; only the zone it is
    expressed in is decided, in one place.

    `workspace_root` is optional because most call sites here have none in
    scope; without it `clock_policy` resolves the workspace through the
    `CR_WORKSPACE` env var the access layer exports into every helper
    process. Falls back to the raw machine clock if the helpers are absent.
    """
    try:
        from clock_policy import user_local_now

        return user_local_now(workspace_root)
    except Exception:
        try:
            from trusted_now import trusted_now_local_naive

            return trusted_now_local_naive(workspace_root)
        except Exception:
            return _dt.datetime.now()


def _to_local_naive(dt: Optional[_dt.datetime]) -> Optional[_dt.datetime]:
    """Aware → naive in this seat's user-local clock (see `_now_local`)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    try:
        from clock_policy import to_user_local_naive

        return to_user_local_naive(dt)
    except Exception:
        return dt.astimezone().replace(tzinfo=None)


# TZDATE3-flagged gap, closed here (IDPOLISH1). Guarded import — survives a
# stripped install missing tz.py, same posture as brain_proposals._localize_
# date.
try:
    from tz import localize_date as _localize_date
except ImportError:  # pragma: no cover - defensive only
    def _localize_date(ts, workspace_path=None) -> str:
        return ts[:10] if isinstance(ts, str) and ts else ""


def _since_display(raw_iso: Optional[str], local_iso: Optional[str], *,
                    workspace_path=None) -> str:
    """Workspace-local "since <date>" for a watchdog sentence — DISPLAY
    ONLY, never fed back into lateness/alarm math.

    `raw_iso` is the receipt's ORIGINAL aware timestamp, from BEFORE
    `_to_local_naive` stripped its tzinfo (`_last_receipt_times`'s output,
    kept alongside the machine-local value additively — the id-never-
    removed convention `load_thread_knowledge._open_rows` also follows).
    `tz.localize_date` needs that real offset to place the calendar date
    correctly. The already machine-local `local_iso` carries NO offset —
    handing it to `tz.to_local` would have it read as UTC and shifted a
    second time, the exact LATETZ double-hop this module's header warns
    against (2026-07-28) — so this helper never does that.

    Falls back to the pre-fix machine-local date slice (`local_iso[:10]`)
    only when `raw_iso` or `workspace_path` isn't available at all — a
    caller that hasn't passed `workspace_path` keeps its old behavior
    byte-for-byte. When `workspace_path` IS given but the workspace has no
    configured tz (or ZoneInfo can't resolve it), `tz.localize_date` itself
    degrades to the raw ISO's UTC date slice rather than raising — the same
    no-configured-tz posture TZDATE1-3 already established at every other
    `localize_date` call site (see `run_tzdate3_test.py`'s "no-TZ
    workspace" section); this helper doesn't second-guess that.

    Machine-vs-workspace TZ can only ever change which CALENDAR DATE this
    sentence names — never whether a task is late, caught up, or on
    schedule; `_to_local_naive` and every comparison against it are
    untouched. See BUILD_IDPOLISH1_2026-09-02.md for the logic-vs-display
    classification of every `_to_local_naive` call site.
    """
    if workspace_path and raw_iso:
        localized = _localize_date(raw_iso, workspace_path)
        if localized:
            return localized
    return (local_iso or "")[:10]


def _iter_events(workspace_root) -> Iterable[dict]:
    try:
        import events_io

        yield from events_io.iter_events(workspace_root)
        return
    except Exception:
        pass
    # Defensive fallback — active file only, bad lines skipped.
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


def _event_matches_task(ev: dict, spec: dict) -> bool:
    """Back-compat shim — the matcher is the receipt contract's
    (`receipts.receipt_task_id`), which parses every legacy shape forever."""
    if ev.get("type") not in spec["types"]:
        return False
    tid = _receipt_task_id(ev)
    return tid is not None and spec is RECEIPT_SPECS.get(tid, spec)


def last_receipts(workspace_root, task_ids=None) -> dict[str, Optional[_dt.datetime]]:
    """Newest substrate receipt per task, machine-local naive datetimes.

    Delegates to the shared receipt reader (`receipts.last_receipt_times`,
    v4.5.2 R1 — one pass, shard-transparent, all legacy shapes). weekly-
    insights ALSO checks the analytical-view mtime fallback: pre-v4.5.2
    fires left no audit event, so the newest of {receipt, view mtime} wins.
    """
    task_ids = list(task_ids) if task_ids is not None else list(RECEIPT_SPECS)
    event_tasks = [t for t in task_ids if RECEIPT_SPECS.get(t, {}).get("types")]
    out: dict[str, Optional[_dt.datetime]] = {tid: None for tid in task_ids}
    if event_tasks:
        try:
            found = _last_receipt_times(workspace_root, event_tasks)
        except Exception:
            found = {}
        for tid, dt in found.items():
            if tid in out and dt is not None:
                out[tid] = _to_local_naive(dt)
    for tid in task_ids:
        views = RECEIPT_SPECS.get(tid, {}).get("views")
        if views:
            newest = out.get(tid)
            views_dir = Path(workspace_root) / "_hq" / "views"
            for name in views:
                p = views_dir / name
                if p.exists():
                    try:
                        mtime = _dt.datetime.fromtimestamp(p.stat().st_mtime)
                    except OSError:
                        continue
                    if newest is None or mtime > newest:
                        newest = mtime
            out[tid] = newest
    return out


def expected_fires(cron: str, now: Optional[_dt.datetime] = None, count: int = 2) -> list[_dt.datetime]:
    """The most recent `count` scheduled fire datetimes <= now, newest first.

    Machine-local math via schedule_config.parse_cron — no new dependency
    and no coupling to config writes. Walks back day-by-day (bounded), so
    weekly and monthly crons resolve without minute-stepping.
    """
    now = now or _now_local()
    minute_set, hour_set, dom_set, month_set, dow_set = parse_cron(cron)
    fires: list[_dt.datetime] = []
    day = now.date()
    for _ in range(0, 800):  # bound: > 2 years covers any sane cadence
        cron_dow = (day.weekday() + 1) % 7  # python Mon=0 → cron Sun=0
        if day.month in month_set and day.day in dom_set and cron_dow in dow_set:
            for h in sorted(hour_set, reverse=True):
                for m in sorted(minute_set, reverse=True):
                    candidate = _dt.datetime.combine(day, _dt.time(h, m))
                    if candidate <= now:
                        fires.append(candidate)
                        if len(fires) >= count:
                            return fires
        day -= _dt.timedelta(days=1)
    return fires


def expected_fires_multi(cron_spec, now: Optional[_dt.datetime] = None,
                          count: int = 2) -> list[_dt.datetime]:
    """Like `expected_fires`, but `cron_spec` may be a single 5-field cron
    string OR a tuple of them — the union of every sub-cron's expected-fire
    instants, newest `count` first.

    CAPSLOT1 (2026-08-27) — exists for a job whose true slot set cannot be
    expressed as ONE 5-field cron without an unwanted minute x hour cross
    product. `meeting-capture`'s nominal cadence is the case that motivated
    this: three hours (6/12/17) share minute :45 and a fourth (16) needs
    minute :30, and a flat cron field pairs every listed minute with every
    listed hour — there is no way to tie :30 to hour 16 alone in one
    expression (`expected_fires` above walks exactly that cross product,
    `for h in hour_set: for m in minute_set`, so this is a property of the
    field, not a bug). Its registry row instead carries a TUPLE of two
    clean, non-cross-product cron strings, and this function reads that
    shape so due-ness for that job stays exact — no extra due-ness slots
    the outer `maintenance` task's own (cross-product) registered cron
    doesn't actually need served.

    A plain string behaves identically to calling `expected_fires` directly
    (a 1-tuple has nothing to union). Duplicate instants across sub-crons
    collapse to one.
    """
    crons = (cron_spec,) if isinstance(cron_spec, str) else tuple(cron_spec)
    merged: list[_dt.datetime] = []
    for c in crons:
        merged.extend(expected_fires(c, now=now, count=count))
    merged.sort(reverse=True)
    out: list[_dt.datetime] = []
    for candidate in merged:
        if not out or out[-1] != candidate:
            out.append(candidate)
        if len(out) >= count:
            break
    return out


def effective_fires(cron: str, now: Optional[_dt.datetime] = None, count: int = 2,
                     min_gap_minutes: int = MIN_GAP_MINUTES) -> list[_dt.datetime]:
    """Like `expected_fires`, but adjacent nominal fire instants closer than
    `min_gap_minutes` apart collapse to ONE effective slot — the EARLIER of
    the pair, newest `count` first.

    MAINTGAP1 F3 (2026-08-27, off REVIEW CAPSLOT1's finding): the
    maintenance dispatcher's min-gap guard makes the LATER twin of a close
    pair (e.g. the :45 fire 15 minutes after its :30 sibling) a guaranteed,
    receipted no-op by construction — first-fire-wins. `check_tasks`'s
    lateness tolerance ("one missed fire is holiday tolerance" — missing
    BOTH of the two most recent expected fires reads `late`, R5) was tuned
    for a cadence where consecutive nominal fires are HOURS apart. Left
    reading the raw nominal cron, a task whose fires now come in 15-minute
    PAIRS turns that one-missed-fire tolerance into effectively ZERO
    tolerance for an ordinary short nap that straddles one pair (a 12:15-
    14:00 laptop close reads `late` at 14:00, where the pre-pairing cadence
    read `ok` for hours more) — the exact regression the review's F3 named.
    Evaluating lateness against EFFECTIVE slots (the earlier / ":30" server
    of each pair) restores the intended tolerance: a pair counts as ONE
    serving opportunity, never two, so a genuinely missed pair still alarms
    exactly as before and a merely-napped-through single fire does not.

    A no-op for every cron with no adjacent pair (every task but
    `maintenance` today — DEFAULT_SCHEDULES has exactly one multi-minute
    row) — collapsing never fires when there is nothing within
    `min_gap_minutes` of anything else, so this is a strict generalization
    of `expected_fires`, safe to call unconditionally.

    Reads more raw fires than requested so collapsing can never starve the
    returned count short.
    """
    now = now or _now_local()
    raw = expected_fires(cron, now=now, count=max(count * 3 + 6, count))
    ascending = list(reversed(raw))  # oldest first — mirrors the dispatcher's
    kept: list[_dt.datetime] = []    # own first-fire-wins scan direction
    gap = _dt.timedelta(minutes=min_gap_minutes)
    for candidate in ascending:
        if kept and (candidate - kept[-1]) < gap:
            continue  # within min-gap of the earlier kept slot — the later
                      # twin collapses into it, same pairing the dispatcher
                      # itself performs at fire time
        kept.append(candidate)
    kept.reverse()  # newest first, matching expected_fires's own contract
    return kept[:count]


def next_fire(cron: str, now: Optional[_dt.datetime] = None) -> Optional[_dt.datetime]:
    """The next scheduled fire datetime strictly after `now` — the forward
    mirror of expected_fires. Machine-local math, same bounded day walk.

    R3 consumer: the `never_fired` render ("hasn't had its first run yet —
    next fire is [time]") must name a real upcoming time, never invent a
    past one."""
    now = now or _now_local()
    minute_set, hour_set, dom_set, month_set, dow_set = parse_cron(cron)
    day = now.date()
    for _ in range(0, 800):  # bound: > 2 years covers any sane cadence
        cron_dow = (day.weekday() + 1) % 7  # python Mon=0 → cron Sun=0
        if day.month in month_set and day.day in dom_set and cron_dow in dow_set:
            for h in sorted(hour_set):
                for m in sorted(minute_set):
                    candidate = _dt.datetime.combine(day, _dt.time(h, m))
                    if candidate > now:
                        return candidate
        day += _dt.timedelta(days=1)
    return None


def _human_time(
    dt_naive: _dt.datetime,
    now: Optional[_dt.datetime] = None,
) -> str:
    """Presentation-only: 'Wednesday 12:20 AM' when within ~6 days of now
    (past or future), 'Jul 2, 12:20 AM' beyond that — a bare weekday would
    be ambiguous across weeks. Renders the value on THIS SEAT's user-local
    clock — the machine's on a legacy desktop seat, the workspace's on a
    merged seat (`_to_local_naive`, i.e. `clock_policy`) — which is the
    watchdog's own clock (`late_signals`) and the clock its cron is scored
    against. A naive value is already in that clock and passes through
    untouched. Never raises.

    NO WORKSPACE-TZ CONVERSION HAPPENS HERE, DELIBERATELY (LATETZ,
    2026-07-28). This is the sibling rail of `late_fire._human_time`, and it
    carried the identical defect: it attached the machine zone with
    `.astimezone()` and then re-expressed the value in the workspace zone via
    `tz.to_local()`. Every value reaching it is ALREADY machine-local naive
    (`_to_local_naive` at the `records()` boundary, then round-tripped through
    `.isoformat()`), so that second hop moved a slot into a clock it was never
    authored in — a no-op only where machine tz == workspace tz. See
    `late_fire._human_time` for the full reasoning and the governing rule
    ("conversion happens once, at registration/change time"). That rule is
    untouched by TZ1: a naive value still passes through with no hop at all.
    What changed is only which zone an AWARE value is normalized INTO — this
    seat's, decided once by `clock_policy`, instead of the host's.

    Two further bugs died with it, both from the old version keeping the
    branch decision and the rendered string on DIFFERENT clocks:
      - `abs(ref - dt_naive)` compared a naive `ref` against `dt_naive`, which
        the old line 306 left AWARE whenever a caller passed an aware value —
        a TypeError in a function documented to never raise. Not reachable
        from today's call sites (they all serialize naive values), but it was
        one aware caller away.
      - the ~6-day window was decided on the machine clock while the weekday
        was rendered on the workspace clock, so near a date boundary the two
        could disagree — "Wednesday" on a row the window had judged as far.
    """
    now = now or _now_local()
    # ONE clock for both the branch decision and the rendered string, and on a
    # merged seat that clock is the WORKSPACE's, not the container's. This is
    # the last unrouted host-clock read on the fire path: the sibling in
    # `late_fire._human_time` was re-pointed at `_to_local_naive` and this one
    # was left on `.astimezone()`, so the same aware value rendered a
    # different weekday on each rail (TZ1 fix round 1, review M-1).
    # `_to_local_naive` returns a naive value unchanged, so a naive caller —
    # which is every caller today — is byte for byte what it always was.
    dt_naive = _to_local_naive(dt_naive)
    ref = _to_local_naive(now)
    clock = dt_naive.strftime("%I:%M %p").lstrip("0")
    if abs(ref - dt_naive) < _dt.timedelta(days=6):
        return f"{dt_naive.strftime('%A')} {clock}"
    return f"{dt_naive.strftime('%b')} {dt_naive.day}, {clock}"


def late_signals(workspace_root, task_ids=None) -> dict[str, dict]:
    """One defensive substrate pass for the late-serve evidence the truth
    rules read (v4.5.2 R3 — F-43 P1b/P2c). Per task:

      receipt_dt / fired_via / late_tier — the task's NEWEST receipt with
        the run mode + lateness tier it carried (all legacy spellings
        normalized by the R1 contract).
      late_fire — the task's newest `late_fire` event (written by
        late_fire.check_lateness on note/degrade tiers):
        {dt, tier, lateness_minutes, scheduled_for}.

    Machine-local naive datetimes throughout (the watchdog's clock).
    """
    wanted = None
    if task_ids is not None:
        wanted = {_normalize_task_id(t) for t in task_ids}
    out: dict[str, dict] = {}

    def _slot(tid: str) -> dict:
        return out.setdefault(tid, {
            "receipt_dt": None, "fired_via": None, "late_tier": None,
            "late_fire": None,
        })

    for ev in _iter_events(workspace_root):
        if not isinstance(ev, dict):
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == "late_fire":
            tid = _normalize_task_id(
                data.get("taskId") or data.get("task_id") or ev.get("source_skill")
            )
            if not isinstance(tid, str) or (wanted is not None and tid not in wanted):
                continue
            dt = _to_local_naive(event_dt(ev))
            if dt is None:
                continue
            s = _slot(tid)
            prev = s["late_fire"]
            if prev is None or dt > prev["dt"]:
                lm = data.get("lateness_minutes")
                tier = data.get("tier")
                sched = data.get("scheduled_for")
                s["late_fire"] = {
                    "dt": dt,
                    "tier": tier if isinstance(tier, str) else None,
                    "lateness_minutes": lm if isinstance(lm, int) else None,
                    "scheduled_for": sched if isinstance(sched, str) else None,
                }
            continue
        tid = _receipt_task_id(ev)
        if tid is None or (wanted is not None and tid not in wanted):
            continue
        dt = _to_local_naive(event_dt(ev))
        if dt is None:
            continue
        s = _slot(tid)
        if s["receipt_dt"] is None or dt > s["receipt_dt"]:
            s["receipt_dt"] = dt
            s["fired_via"] = _normalize_fired_via(data.get("fired_via"))
            s["late_tier"] = _get_late_tier(data)
    return out


def _weekdays_since(start: _dt.datetime, now: _dt.datetime) -> int:
    days, d = 0, start.date()
    while d < now.date():
        d += _dt.timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def read_workspace_config(workspace_root) -> dict:
    p = Path(workspace_root) / "_hq" / "workspace_config.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        # FS-15 — a workspace_config.json that EXISTS but won't read makes
        # every registered task look unregistered (the probable mechanism of
        # the June workspace_config truncation). Keep the {} fallback (the
        # watchdog must not crash the brief) but record the degradation so
        # the brief / system-health surface it loudly. Alarm recording is
        # best-effort: a broken read_alarm module must not turn this
        # degraded read into a hard failure.
        try:
            from read_alarm import record_read_alarm
            record_read_alarm(p, e, reader="task_watchdog")
        except Exception:
            pass
        return {}


# ---------------------------------------------------------------------------
# R-M1-6 — the platform declined the fire (SPEC_MERGEFIX1 F13).
#
# This PC's pre-merge Cowork store recorded `maintenance` and `staff-meeting`
# skipped with `reason: global_limit` three times each on 2026-09-08. Read
# from here that is indistinguishable from a computer that slept: no receipt,
# two missed slots, `late`, and a sentence telling the customer to go and
# press something. It is a different fact with a different (empty) action, and
# saying the wrong one trains a reader to ignore the right one.
#
# The SHAPES are read defensively and by VOCABULARY, not by one key: the
# legacy store spells it `recordedSkips`, a normalised cloud record may carry
# a run list, and `schedule_backend` will eventually own the normalisation
# (named as a seam in this lane's record — it owns no decline vocabulary
# today). If it ever grows one, it is used first and this reader becomes the
# fallback; until then the fallback IS the reader.
# ---------------------------------------------------------------------------

#: A `reason` value that means "the platform said no", not "the work failed".
PLATFORM_DECLINE_REASONS: frozenset = frozenset({
    "global_limit", "usage_limit", "rate_limit", "rate_limited",
    "quota_exceeded", "over_capacity", "capacity", "throttled",
})
#: A run `status` that means the same thing.
PLATFORM_DECLINE_STATUSES: frozenset = frozenset({
    "declined", "refused", "throttled", "rate_limited", "skipped_limit",
})
#: Where a decline row can hide on a task record.
_DECLINE_LIST_KEYS = ("recordedSkips", "recorded_skips", "skips", "declines",
                      "runs", "lastRuns", "run_history", "runHistory")
_DECLINE_TS_KEYS = ("at", "ts", "timestamp", "ranAt", "runAt", "startedAt",
                    "scheduledFor", "scheduled_for")


def _decline_reason(row) -> Optional[str]:
    """The decline reason on ONE run/skip row, or None when the row is an
    ordinary run (or a failure, which `check_task_failures` already owns)."""
    if not isinstance(row, dict):
        return None
    for key in ("reason", "skipReason", "skip_reason", "declineReason"):
        value = row.get(key)
        if isinstance(value, str) and value.strip().lower() in PLATFORM_DECLINE_REASONS:
            return value.strip().lower()
    for key in ("status", "state", "outcome", "result"):
        value = row.get(key)
        if isinstance(value, str) and value.strip().lower() in PLATFORM_DECLINE_STATUSES:
            reason = row.get("reason")
            return (reason.strip().lower()
                    if isinstance(reason, str) and reason.strip()
                    else value.strip().lower())
    return None


def _decline_rows(rec) -> list[dict]:
    """Every `{reason, dt}` decline the scheduler's own record carries,
    newest last. An unreadable or absent record is simply no declines — this
    is a health read and it never raises into a surface."""
    if not isinstance(rec, dict):
        return []
    try:
        import schedule_backend as _sb

        normaliser = getattr(_sb, "normalize_run_records", None)
        if callable(normaliser):
            rec = normaliser(rec) or rec
    except Exception:  # noqa: BLE001 — the seam is optional, never required
        pass
    rows: list[dict] = []
    candidates: list = []
    for key in _DECLINE_LIST_KEYS:
        value = rec.get(key)
        if isinstance(value, list):
            candidates.extend(value)
        elif isinstance(value, dict):
            candidates.append(value)
    # The flat shape: one last-run verdict on the record itself.
    flat = {k: rec.get(k) for k in ("lastRunStatus", "lastRunReason",
                                    "status", "reason")}
    if flat.get("lastRunStatus") or flat.get("lastRunReason"):
        candidates.append({"status": flat.get("lastRunStatus"),
                           "reason": flat.get("lastRunReason"),
                           "at": rec.get("lastRunAt")})
    for row in candidates:
        reason = _decline_reason(row)
        if reason is None:
            continue
        dt = None
        for key in _DECLINE_TS_KEYS:
            dt = _to_local_naive(parse_ts(str(row.get(key) or "")))
            if dt is not None:
                break
        rows.append({"reason": reason, "dt": dt})
    rows.sort(key=lambda r: (r["dt"] is not None, r["dt"] or _dt.datetime.min))
    return rows


def platform_decline(rec, *, last_fired=None, now=None,
                     not_before=None) -> Optional[dict]:
    """The newest platform decline this task has NOT recovered from, or None.

    "Recovered" is a receipt: a decline followed (within dispatch grace) by a
    fire that wrote something is history, not a finding — exactly the posture
    `late_signals` already takes for an old `late_fire` with a normal receipt
    after it. A decline with no timestamp is read as current, which is the
    conservative direction: it says "this is happening", and the sentence it
    earns asks for nothing.

    `not_before` is the RECENCY FLOOR (fix round 1, REVIEW F-2). Recovery is
    not the only way a decline stops being the current explanation: a task
    that never ran again keeps its last decline for ever, and the sentence
    ("it clears on its own") becomes false the moment the decline stops being
    the live cause. A decline older than the floor is history too, and the
    caller's ordinary late/receipt-gap partition — which says the task has
    stopped and cannot tell why — is the true sentence for a task dead four
    months. The caller passes the second-most-recent effective slot, so the
    floor is the task's OWN cadence rather than a number typed here. A decline
    with no timestamp has no age to test and stays current.
    """
    rows = _decline_rows(rec)
    if not rows:
        return None
    newest = rows[-1]
    when = newest["dt"] or (now or _now_local())
    if last_fired is not None and last_fired >= when - _FIRE_GRACE:
        return None
    if (not_before is not None and newest["dt"] is not None
            and newest["dt"] < not_before):
        return None
    return {"reason": newest["reason"],
            "at": newest["dt"].isoformat() if newest["dt"] else None}


# ---------------------------------------------------------------------------
# HEALTH3 (Train 2b §3, F-T2-7, walk row 2 (a) and (d)): the health check
# reads the APP's last-run time, and only from THIS seat's records.
#
# On the 2026-09-27 walk the check was handed twelve records: six of this
# seat and six disabled copies another computer left behind. `check_tasks`
# keyed them by taskId with the LAST record winning, so the other computer's
# copy decided `lastRunAt`, and the surface printed no last-run time at all.
# ---------------------------------------------------------------------------

#: A value that names a time of day, not just a date ("...T17:06", "... 17:06").
_TIME_OF_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ](\d{2}):(\d{2})")
#: Fractional seconds of any length; cut or padded to microseconds, so an
#: older Python reads the same instant a newer one does.
_FRACTION_RE = re.compile(r"(:\d{2})\.(\d+)")


def app_last_run(value) -> Optional[_dt.datetime]:
    """The app's `lastRunAt` as a tz-aware instant, parsed through
    `parse_ts`, or None when the value is not an instant.

    A value with no time of day is refused. `parse_ts` reads "2026-09-25" (or
    anything it can only read the date out of) as midnight UTC, and midnight
    UTC renders as 5 PM the evening BEFORE on a Pacific seat: the walk's
    `2026-09-24T17:00` / `2026-09-25T17:00` values, the same instant for four
    tasks, matching no app `lastRunAt`. A last-run time the app did not give
    is never shown; the task reads as "not run yet" instead."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    m = _TIME_OF_DAY_RE.match(text)
    if not m:
        return None
    text = _FRACTION_RE.sub(
        lambda f: f.group(1) + "." + (f.group(2) + "000000")[:6], text, count=1)
    # Review N-7: the FULL parse decides, on every Python this ships to (an
    # offset without its colon is given one, which 3.10 needs); a value it
    # refuses is not an instant, whatever parse_ts would fall back to.
    full = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text.replace("Z", "+00:00"))
    try:
        _dt.datetime.fromisoformat(full)
    except ValueError:
        return None
    # The full parse accepted `full`, so parse_ts reads the same instant (it
    # falls back to the date alone only on a value the full parse refused).
    return parse_ts(full)


def split_seat_records(task_records, device_path=None) -> dict:
    """THIS seat's scheduler records, and the ones another computer holds.

    `device_path` is this seat's folder on the customer's computer (the
    `CR_DEVICE_WORKSPACE` the door forwards). With it, the records are split
    by `schedule_backend.seat_records` (the digest of each record's folder);
    without it, or when the split cannot run, every record is this seat's,
    which is today's behaviour byte for byte.

    Returns `{"own", "foreign", "foreign_records", "foreign_count",
    "foreign_disabled"}`: `foreign_records` is `[{"taskId", "enabled"}]` for
    each record another computer holds, `foreign_disabled` how many of them
    are switched off. `task_records=None` (no scheduler here) stays None."""
    out = {"own": task_records, "foreign": [], "foreign_records": [],
           "foreign_count": 0, "foreign_disabled": 0}
    if task_records is None or not str(device_path or "").strip():
        return out
    try:
        import schedule_backend as _sb

        split = _sb.seat_records(list(task_records),
                                 abs_path=str(device_path).strip())
    except Exception:  # noqa: BLE001 - a health read never breaks a surface
        return out
    foreign = list(split.get("foreign") or [])

    def _get(rec, key):
        return rec.get(key) if isinstance(rec, dict) else getattr(rec, key, None)

    out["own"] = list(split.get("own") or [])
    out["foreign"] = foreign
    out["foreign_records"] = [{"taskId": _get(r, "taskId"),
                               "enabled": _get(r, "enabled")} for r in foreign]
    out["foreign_count"] = len(foreign)
    out["foreign_disabled"] = sum(1 for r in foreign if _get(r, "enabled") is False)
    return out


# HEALTH3 MUST 2 (F-T2-8): the first-run window is PER REGISTRATION, not per
# workspace. On the walk three chats re-registered on 2026-09-26, on a
# workspace registered five weeks earlier with old receipts, read `late` and
# "dark" before their first slot had even come round.

#: The keys a scheduler record may carry its own creation stamp under.
_CREATED_KEYS = ("created_at", "createdAt")


def schedule_created_times(workspace_root) -> dict:
    """`{trigger_id: newest instant}` of the `schedule_created` rows that name
    a trigger (the row registration appends after each create, carrying the
    trigger id the create returned). Keyed by trigger, so a re-run that
    creates nothing moves nothing, and another computer's trigger is never
    this seat's. Never raises."""
    out: dict = {}
    try:
        for ev in _iter_events(workspace_root):
            if not isinstance(ev, dict) or ev.get("type") != "schedule_created":
                continue
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            trig = data.get("trigger_id")
            if not isinstance(trig, str) or not trig.strip():
                continue
            when = event_dt(ev)
            if when is None:
                continue
            key = trig.strip()
            if key not in out or when > out[key]:
                out[key] = when
    except Exception:  # noqa: BLE001 - a health read never breaks a surface
        return {}
    return out


def stored_row_is_this_seat(row, device_path=None) -> bool:
    """Fix round 1 (review N-4): may this seat read a stored `triggers` row
    as its own? The map lives in the synced folder and holds whichever
    computer registered last. A row that names no folder, or a seat with no
    folder to compare, cannot be judged and is read (today's behaviour); a
    row whose folder digests to another computer's never is."""
    if not isinstance(row, dict):
        return False
    here = str(device_path or "").strip()
    folders = row.get("folders")
    where = ""
    if isinstance(folders, (list, tuple)):
        for entry in folders:
            if isinstance(entry, dict):
                entry = entry.get("path") or entry.get("folder") or ""
            if str(entry or "").strip():
                where = str(entry).strip()
                break
    if not here or not where:
        return True
    try:
        from schedule_config import device_digest
    except Exception:  # noqa: BLE001 - without the digest, cannot judge
        return True
    return device_digest(where) == device_digest(here)


def task_registered_at(task_id, rec=None, *, trigger_map=None,
                       created_times=None, workspace_registered=None,
                       device_path=None):
    """The newest registration of `task_id` on THIS seat, on this seat's
    clock (naive local), from the task's own evidence: the stored trigger
    row's `registered_at` (`read_trigger_map`), the `schedule_created` row of
    this seat's trigger (the record's `trigger_id`, else the stored row's),
    and the record's own creation stamp when the app carries one; the newest
    of those. With none of them, the workspace's `registered_at`. The stored
    row is read only when `stored_row_is_this_seat(row, device_path)`."""
    row = (trigger_map or {}).get(task_id) if isinstance(trigger_map, dict) else None
    row = row if isinstance(row, dict) else {}
    if not stored_row_is_this_seat(row, device_path):
        row = {}
    rec = rec if isinstance(rec, dict) else {}
    found = [parse_ts(row.get("registered_at") or "")]
    trig = rec.get("trigger_id") or row.get("trigger_id")
    if trig:
        found.append((created_times or {}).get(str(trig).strip()))
    raw = rec.get("raw") if isinstance(rec.get("raw"), dict) else {}
    for src in (rec, raw):
        for key in _CREATED_KEYS:
            value = src.get(key)
            found.append(parse_ts(value) if isinstance(value, str) else None)
    found = [_to_local_naive(f) for f in found if f is not None]
    if found:
        return max(found)
    return workspace_registered


def registered_after_slot(registered, recent, effective, last_run=None,
                          enabled=None, declined=None) -> bool:
    """True when the task's newest registration is LATER than its
    second-most-recent expected slot and nothing has been receipted since
    that registration: the chat has not had its first chance to run, so it
    is `never_fired` whatever older receipts say.

    Fix round 1 (review N-2): never over a chat that HAS had its chance. The
    app ran it since the registration (`last_run` at or after it), the
    platform declined a run of it (`declined`), or the chat is switched off
    (`enabled is False`, it will make no first run): each keeps the status and
    the sentence it had before this window existed."""
    if registered is None or len(recent or []) < 2:
        return False
    if enabled is False or declined is not None:
        return False
    if last_run is not None and last_run >= registered:
        return False
    return registered > recent[1] and (effective is None or effective < registered)


def _record_rank(rec) -> tuple:
    """The order `pick_task_records` keeps: an enabled record over a
    switched-off one, then the later app last-run instant."""
    when = app_last_run(rec.get("lastRunAt"))
    return (rec.get("enabled") is not False,
            when.timestamp() if when is not None else float("-inf"))


def pick_task_records(task_records) -> tuple:
    """`({taskId: record}, {taskId, ...})`: ONE record per taskId, chosen by
    what the records say, never by list order (fix round 1, review N-5).
    Two records of one chat that the seat split could not tell apart (no
    folder, no prompt) resolve to the enabled one, then the one the app ran
    later. Still equal, the choice is made on the record's own content (so
    it is the same in any order) and the taskId is in the second set, a
    code fact the report carries as `duplicate_tied`."""
    grouped: dict = {}
    for rec in task_records or []:
        if isinstance(rec, dict) and rec.get("taskId"):
            grouped.setdefault(rec["taskId"], []).append(rec)
    chosen, tied = {}, set()
    for tid, recs in grouped.items():
        if len(recs) == 1:
            chosen[tid] = recs[0]
            continue
        ranked = sorted(recs, key=lambda r: (
            _record_rank(r), json.dumps(r, sort_keys=True, default=str)),
            reverse=True)
        chosen[tid] = ranked[0]
        if _record_rank(ranked[0]) == _record_rank(ranked[1]):
            tied.add(tid)
    return chosen, tied


def check_tasks(
    workspace_root,
    *,
    now: Optional[_dt.datetime] = None,
    registered_ids: Optional[set] = None,
    task_records: Optional[list] = None,
    config: Optional[dict] = None,
    device_path: Optional[str] = None,
) -> list[dict]:
    """The W1 report: one dict per enabled task.

    Args:
      device_path (HEALTH3 MUST 1): this seat's folder on the customer's
        computer. When given, only THIS seat's records feed the report
        (`split_seat_records`); another computer's copies of the same chats
        never decide `lastRunAt`. Omitted, every record is used, as before.
      registered_ids: the registered-task set. Defaults to
        workspace_config.json `registered_taskIds` (the offline-first record
        registration maintains); pass the taskIds from
        `list_scheduled_tasks` when the caller has them (they're fresher).
      task_records: optional raw records from `list_scheduled_tasks`
        (dicts with taskId / lastRunAt / enabled / prompt). Used for the
        secondary lastRunAt signal + receipt_gap detection.

    Returns dicts:
      {task, display_name, status, silent, last_fired (ISO|None),
       expected (ISO|None), next_fire (ISO|None), last_run_at (ISO|None),
       receipt_gap (bool), registered (bool), last_fired_via (str|None),
       caught_up (bool), catchup (dict|None)}

    `caught_up` (R3 — F-43 P1b/P2c): the task's NEWEST fire served its slot
    late — a `catchup` fired_via / note-or-degrade `late_tier` on the newest
    receipt, or a `late_fire` event written alongside it. Cadence-wise the
    task is not broken (it fired), but it did NOT run "on its normal
    schedule" and every render must say so, with dates (`catchup` carries
    fired_at / scheduled_for / tier / lateness_minutes).
    """
    now = now or _now_local()
    ws_config = read_workspace_config(workspace_root)
    if registered_ids is None:
        raw = ws_config.get("registered_taskIds")
        registered_ids = set(raw) if isinstance(raw, list) else set()
    if device_path:
        task_records = split_seat_records(task_records, device_path)["own"]
    records_by_id, duplicate_tied = pick_task_records(task_records)
    if config is None:
        entities = Path(workspace_root) / "_hq" / "data" / "entities.json"
        config = load_schedule_config(entities)

    enabled = {tid: spec for tid, spec in config.items() if spec.get("enabled")}
    # EOD2 — a RENAMED task is served by whichever of its ids this machine
    # actually has registered, and its fire history spans both. So every
    # question below ("is it registered?", "when did it last fire?") is asked
    # of the SERVING set, not the successor id alone. `past-meetings` left
    # DEFAULT_SCHEDULES, so it is not reported as a task of its own — that is
    # what makes a still-registered retired id structurally silent — but it
    # is precisely what proves `end-of-day` healthy on the same machine.
    # Without this, EOD2's ship day turns every fleet workspace's evening
    # chat into a "missing from the schedule" finding on a chat that fires
    # tonight.
    # `serving_task_ids`, not an inlined `(tid,) + renamed_predecessors(tid)`:
    # two spellings of one derivation is how they drift (REVIEW N-2 — the
    # registry helper was uncalled by the code that most needs it).
    serving: dict[str, tuple] = {tid: serving_task_ids(tid) for tid in enabled}
    receipt_ids = sorted({t for ids in serving.values() for t in ids})
    receipts_by_id = last_receipts(workspace_root, receipt_ids)
    receipts = {
        tid: max((d for d in (receipts_by_id.get(t) for t in ids) if d is not None),
                 default=None)
        for tid, ids in serving.items()
    }
    # IDPOLISH1 — the same reduction, over the RAW (still tz-aware) receipt
    # times, purely for the "since <date>" display helper below.
    # `_to_local_naive` is order-preserving (`astimezone()` never reorders
    # instants), so this picks the identical winning receipt id per task as
    # `receipts` above — additive, never consulted by any status/lateness
    # decision.
    try:
        raw_receipts_by_id = _last_receipt_times(workspace_root, receipt_ids)
    except Exception:
        raw_receipts_by_id = {}
    raw_receipts = {
        tid: max((d for d in (raw_receipts_by_id.get(t) for t in ids) if d is not None),
                 default=None)
        for tid, ids in serving.items()
    }
    try:
        signals = late_signals(workspace_root, receipt_ids)
    except Exception:
        signals = {}
    registered_at = parse_ts(ws_config.get("registered_at") or "")
    registered_at = _to_local_naive(registered_at)
    # HEALTH3 MUST 2: each task's own newest registration on this seat.
    trigger_map = ws_config.get("triggers")
    trigger_map = trigger_map if isinstance(trigger_map, dict) else {}
    created_times = (schedule_created_times(workspace_root)
                     if trigger_map or any(r.get("trigger_id")
                                           for r in records_by_id.values())
                     else {})

    reports = []
    for tid, spec in enabled.items():
        # The record / registration answer comes from whichever SERVING id
        # this machine has (EOD2). `served_by` is the predecessor when that
        # is what is registered — None on every workspace that has the
        # current id, which is every workspace with no rename outstanding.
        served_by = None
        rec = records_by_id.get(tid)
        for alt in serving[tid][1:]:
            if rec is None and alt in records_by_id:
                rec = records_by_id[alt]
            if alt in registered_ids or alt in records_by_id:
                served_by = served_by or alt
        is_registered = (
            tid in registered_ids or records_by_id.get(tid) is not None
            or served_by is not None
        )
        if tid in registered_ids or records_by_id.get(tid) is not None:
            served_by = None
        last_fired = receipts.get(tid)
        # HEALTH3 MUST 1: the app's own instant, never a date read as midnight.
        app_run = app_last_run((rec or {}).get("lastRunAt"))
        last_run_at = _to_local_naive(app_run)
        # Fix round 1 (review N-3): a value the app gave that cannot be read
        # is not "never ran"; the stamp line then claims nothing about a run.
        _raw_run = (rec or {}).get("lastRunAt")
        last_run_unread = bool(app_run is None and _raw_run not in (None, "")
                               and not (isinstance(_raw_run, str)
                                        and not _raw_run.strip()))
        try:
            # MAINTGAP1 F3 — EFFECTIVE slots, not raw nominal ones (see
            # effective_fires's own docstring): a strict generalization of
            # expected_fires that only changes anything for a cron carrying
            # adjacent min-gap pairs (today: `maintenance` alone).
            recent = effective_fires(spec["cron"], now=now, count=2)
        except CronParseError:
            recent = []
        expected_latest = recent[0] if recent else None
        # Receipts are the ONLY served/not-served truth (v4.5.2 R2). The
        # pre-R2 code took max(receipt, lastRunAt) here, which let a
        # stamped-but-never-executed "run" read as on-schedule forever —
        # the 2026-07-08 cleanup autopsy proved lastRunAt lands without
        # execution (F-39: 9 tasks stamped at app launch, ONE receipt).
        # lastRunAt stays a SECONDARY signal via receipt_gap below.
        effective = last_fired

        # R-M1-6 — asked BEFORE the lateness math, because a declined fire and
        # a slept-through fire look identical from the receipts alone and only
        # the run record can tell them apart. A decline the task already
        # recovered from (a receipt inside grace) returns None here and the
        # ordinary partition runs, unchanged.
        #
        # FIX ROUND 1 (REVIEW F-2) — the RECENCY FLOOR. Recovery was the only
        # bound, so a decline from three months ago with nothing since stayed
        # this task's current explanation for ever and displaced the true
        # sentence ("stopped firing, and I can't tell why"). The floor is the
        # task's own second-most-recent effective slot — the same instant the
        # lateness test below uses, so "older than the last two slots" means
        # one thing in this function — and the freshness window where no cron
        # parses.
        decline_floor = (recent[1] - _FIRE_GRACE if len(recent) >= 2
                         else now - _VANTAGE_FRESH)
        declined = platform_decline(rec, last_fired=effective, now=now,
                                    not_before=decline_floor)

        grace_start = registered_at or (now - _dt.timedelta(days=1))
        past_grace = (_weekdays_since(grace_start, now)
                      >= _AUTHORIZATION_GRACE_WEEKDAYS)
        # FIX ROUND 1 (REVIEW F-3, ruling R-TRUTH1-d taken the other way by
        # the coordinator). The first-run window is DELIBERATELY silent: a
        # chat registered yesterday with no receipt yet is `never_fired` and
        # says nothing at all. A decline arriving inside that window used to
        # jump the queue and put a sentence on a surface that is supposed to
        # be quiet, on a chat that has never run once. Inside the grace, the
        # decline speaks only where there is a prior receipt to have been
        # declined AFTER; with no receipt at all the first-run status stands
        # and the decline stays on the record as evidence.
        first_run_window = effective is None and not past_grace
        # HEALTH3 MUST 2 (F-T2-8): registered on this seat after its
        # second-most-recent slot, and nothing receipted since: first run.
        # Only the TASK's own evidence opens this window. With none, the
        # workspace's `registered_at` keeps its existing job (the grace just
        # above), unchanged: a setup re-run restamps that value, and read as
        # every task's registration it would silence every late chat.
        if registered_after_slot(
                task_registered_at(tid, rec, trigger_map=trigger_map,
                                   created_times=created_times,
                                   workspace_registered=None,
                                   device_path=device_path),
                recent, effective, last_run=last_run_at,
                enabled=(rec or {}).get("enabled"), declined=declined):
            first_run_window = True

        if not is_registered:
            status = "not_registered"
            declined = None
        elif first_run_window:
            status = "never_fired"
        elif declined is not None:
            status = "platform_declined"
        elif effective is None:
            status = "never_authorized"
        elif len(recent) >= 2 and effective < recent[1] - _FIRE_GRACE:
            # Missed BOTH of the two most recent expected fires (>=2 missed
            # = R5 threshold; one missed fire is holiday tolerance).
            status = "late"
        else:
            status = "ok"

        receipt_gap = bool(
            last_run_at is not None
            and expected_latest is not None
            and (last_fired is None or last_fired < last_run_at - _FIRE_GRACE)
            and RECEIPT_SPECS.get(tid, {}).get("types")
        )

        # Late-serve detection (R3 — F-43 P1b/P2c): does the NEWEST fire
        # evidence say this slot was served late? Signals only count when
        # they belong to the newest fire — an old late_fire with a normal
        # receipt after it is history, not a current finding. `last_fired`
        # can come from the view-mtime fallback (weekly-insights), so the
        # receipt-borne signals are gated on the receipt actually BEING the
        # newest fire (within dispatch grace).
        # Same serving-set read as the receipts above: on a machine still
        # firing the predecessor, the late/catch-up evidence for this slot is
        # filed under the OLD id. Newest receipt_dt wins.
        sig = {}
        for alt in serving[tid]:
            cand = signals.get(alt) or {}
            if not cand:
                continue
            if not sig or (
                cand.get("receipt_dt") is not None
                and (sig.get("receipt_dt") is None
                     or cand["receipt_dt"] > sig["receipt_dt"])
            ):
                sig = cand
        lf = sig.get("late_fire")
        last_fired_via = None
        caught_up = False
        catchup_info = None
        if last_fired is not None:
            receipt_is_newest = (
                sig.get("receipt_dt") is not None
                and abs(sig["receipt_dt"] - last_fired) <= _FIRE_GRACE
            )
            if receipt_is_newest:
                last_fired_via = sig.get("fired_via")
            lf_is_newest = bool(
                lf and lf.get("dt") is not None
                and abs(lf["dt"] - last_fired) <= _FIRE_GRACE
            )
            late_serve = status == "ok" and (
                (receipt_is_newest and sig.get("fired_via") == "catchup")
                or (receipt_is_newest and sig.get("late_tier") in ("note", "degrade"))
                or lf_is_newest
            )
            if late_serve:
                # W2 SCORE2 MUST 4 (CB-T2B-4 F-2): a retry that FAILED again
                # lands a `late_fire` row beside a `surface_failed` receipt;
                # that slot was not caught up. Read only when a late serve is
                # claimed, so the ordinary report pays nothing for it.
                try:
                    from receipts import iter_receipts as _iter_receipts
                    _rows = _iter_receipts(workspace_root,
                                           task_ids=list(serving[tid]))
                    _newest = _rows[-1] if _rows else {}
                except Exception:  # noqa: BLE001 - unreadable proves nothing
                    _newest = {}
                if "surface_failed" in (_newest.get("status"),
                                        _newest.get("type")):
                    late_serve = False
            if late_serve:
                caught_up = True
                catchup_info = {
                    "fired_at": last_fired.isoformat(),
                    "scheduled_for": (lf or {}).get("scheduled_for"),
                    "tier": (lf or {}).get("tier")
                            or (sig.get("late_tier") if receipt_is_newest else None),
                    "lateness_minutes": (lf or {}).get("lateness_minutes"),
                }

        try:
            upcoming = next_fire(spec["cron"], now=now)
        except CronParseError:
            upcoming = None

        reports.append({
            "task": tid,
            "display_name": task_display_name(tid),
            "status": status,
            "silent": tid in SILENT_TASKS,
            "first_install": tid in FIRST_INSTALL_TASK_IDS,
            "registered": is_registered,
            "last_fired": last_fired.isoformat() if last_fired else None,
            # IDPOLISH1 — additive, display-only (see _since_display): the
            # SAME receipt as "last_fired", still tz-aware. Never read by
            # any status/lateness comparison.
            "last_fired_raw": (raw_receipts.get(tid).isoformat()
                               if raw_receipts.get(tid) else None),
            "last_run_at": last_run_at.isoformat() if last_run_at else None,
            # HEALTH3 MUST 1: the app's own value, as the instant it names.
            "last_run_at_raw": app_run.isoformat() if app_run else None,
            "last_run_unread": last_run_unread,
            # Fix round 1 (review N-5): two records of this chat were equal on
            # everything that decides which is read. A code fact, never text.
            "duplicate_tied": tid in duplicate_tied,
            "expected": expected_latest.isoformat() if expected_latest else None,
            "next_fire": upcoming.isoformat() if upcoming else None,
            "receipt_gap": receipt_gap,
            # R-M1-6 — `{reason, at}` when the platform declined the newest
            # fire and nothing has been receipted since; None everywhere else,
            # which is every seat whose scheduler never reported a cap.
            "platform_declined": declined,
            "last_fired_via": last_fired_via,
            "caught_up": caught_up,
            "catchup": catchup_info,
            # EOD2 — the retired id this machine is actually running, when
            # that is what serves the row. None everywhere else, which is
            # every workspace with no rename outstanding. Renders that name
            # the task must say the name the customer's Scheduled list shows.
            "served_by": served_by,
        })
    return reports


def check_maintenance_jobs(workspace_root, *, now=None) -> list[dict]:
    """MAINT1 (D8) — per-JOB receipt-gap check for the jobs inside the
    `maintenance` task. Same posture as check_tasks: receipts are the only
    served/not-served truth, and one missed nominal slot is tolerance (a
    single fire can be cut short); missing BOTH of the two most recent
    nominal slots is `stale`.

    Statuses per job:
      ok    — receipt within tolerance of the job's own nominal cadence.
      never — no receipt ever (meaningful only once the maintenance task has
              been firing across the job's slots — health_verdict gates on
              that before flagging).
      stale — receipted before, then stopped: missed the two most recent
              nominal slots.

    Returns [{job, display_name, status, last_receipt (ISO|None),
    expected (ISO|None), second_expected (ISO|None)}] in registry order.
    """
    # Lazy import — maintenance_dispatcher imports this module's cron math at
    # load; importing it back at module level would be a cycle.
    from maintenance_dispatcher import MAINTENANCE_JOBS

    now = now or _now_local()
    receipts = last_receipts(workspace_root, list(MAINTENANCE_JOBS))
    # IDPOLISH1 — raw (tz-aware) twin of `receipts`, additive and display-
    # only (see _since_display); never consulted by the never/stale/ok
    # decision below.
    try:
        raw_receipts = _last_receipt_times(workspace_root, list(MAINTENANCE_JOBS))
    except Exception:
        raw_receipts = {}
    findings = []
    for job_id, spec in MAINTENANCE_JOBS.items():
        try:
            recent = expected_fires_multi(spec["nominal_cron"], now=now, count=2)
        except CronParseError:
            continue
        last = receipts.get(job_id)
        if last is None:
            status = "never"
        elif len(recent) >= 2 and last < recent[1] - _FIRE_GRACE:
            status = "stale"
        else:
            status = "ok"
        raw_last = raw_receipts.get(job_id)
        findings.append({
            "job": job_id,
            "display_name": task_display_name(job_id),
            "status": status,
            "last_receipt": last.isoformat() if last else None,
            "last_receipt_raw": raw_last.isoformat() if raw_last else None,
            "expected": recent[0].isoformat() if recent else None,
            "second_expected": recent[1].isoformat() if len(recent) >= 2 else None,
        })
    return findings


def _maintenance_job_problems(workspace_root, reports, *, now=None):
    """The job-level findings health_verdict folds in (MAINT1 D8). Job detail
    is only meaningful when the maintenance TASK itself is firing — a broken
    task already gets its own task-level line, and doubling it per job would
    be noise. So this returns [] unless the maintenance task report exists,
    is receipted, and is not itself a problem.

    A `never` job is flagged only when the task has been firing since before
    the job's second-most-recent nominal slot (the task had >= 2 chances to
    serve it and never did) — a fresh install's first week stays quiet.

    Returns (findings, lines): the stale-job findings + one plain-English
    sentence each (facts + the one action, never a cause — R3).
    """
    maint = next((r for r in reports if r["task"] == "maintenance"), None)
    if maint is None or not maint.get("last_fired"):
        return [], []
    if maint["status"] not in ("ok",) or maint.get("receipt_gap"):
        return [], []
    now = now or _now_local()
    try:
        findings = check_maintenance_jobs(workspace_root, now=now)
    except Exception:
        return [], []
    # Oldest maintenance_run receipt = how long the task has been firing.
    oldest_fire = None
    try:
        from receipts import iter_receipts as _iter_receipts

        for r in _iter_receipts(workspace_root, task_ids=["maintenance"]):
            dt_local = _to_local_naive(r["dt"]) if r["dt"] is not None else None
            if dt_local is not None and (oldest_fire is None or dt_local < oldest_fire):
                oldest_fire = dt_local
    except Exception:
        oldest_fire = None

    # T2 FIRE1 MUST 3 (FIX-7). Two classes are never "hasn't recorded any
    # work": a QUIET job, which writes no receipt on a quiet run BY DESIGN
    # (`job_counts_as_complete` already scores it complete; the health check
    # on the second computer still named three of them), and a job ruled
    # skill-class on the merged fire that the newest fire left honestly due
    # (`honestly_due_jobs`; it gets the waiting sentence, not a failure).
    try:
        from maintenance_dispatcher import QUIET_RUN_JOBS as _quiet
    except Exception:  # noqa: BLE001 - an older tree: nothing exempt
        _quiet = frozenset()
    _quiet = frozenset(_quiet) | nothing_stamped_jobs()
    _left_due = honestly_due_jobs(workspace_root)
    problems, lines = [], []
    for f in findings:
        if f["job"] in _quiet or f["job"] in _left_due:
            continue
        flag = False
        if f["status"] == "stale":
            flag = True
        elif f["status"] == "never" and f["second_expected"] and oldest_fire is not None:
            try:
                flag = oldest_fire < _dt.datetime.fromisoformat(f["second_expected"]) - _FIRE_GRACE
            except ValueError:
                flag = False
        if not flag:
            continue
        name = f["display_name"]
        since = _since_display(f.get("last_receipt_raw"), f.get("last_receipt"),
                                workspace_path=workspace_root)
        since_phrase = f" since {since}" if since else ""
        lines.append(
            f"Your Maintenance task is running, but its {name} pass hasn't "
            f"recorded any work{since_phrase} — "
            f"{typed_action('maintenance')} once, and check the result looks "
            f"right."
        )
        problems.append(f)
    return problems, lines


def nothing_stamped_jobs() -> frozenset:
    """HEALTH3 MUST 4 (walk row 2 (b), F-T2-11): the jobs the registry says
    leave NO trace on a run with nothing stamped (`dedup-apply`), which the
    health check must never call "hasn't recorded any work". Read from
    `maintenance_dispatcher.NOTHING_STAMPED_JOBS` (Train 3 FIRE3 defines it);
    the fallback names the one job for a tree that does not have it yet."""
    try:
        import maintenance_dispatcher as _md
    except Exception:  # noqa: BLE001 - without the registry, the known one
        return frozenset({"dedup-apply"})
    return frozenset(getattr(_md, "NOTHING_STAMPED_JOBS",
                             frozenset({"dedup-apply"})))


def honestly_due_jobs(workspace_root) -> frozenset:
    """The jobs ruled skill-class on the merged fire
    (`maintenance_dispatcher.MERGED_SKILL_CLASS_JOBS`) that the NEWEST
    `maintenance_run` receipt left due - in its `jobs_due` and in neither
    `jobs_completed` nor `jobs_failed` (T2 FIRE1 MUST 3, D-6). A job the fire
    listed as failed is not here: that is a failure, and it is said."""
    try:
        from maintenance_dispatcher import MERGED_SKILL_CLASS_JOBS as _ruled
        from maintenance_dispatcher import RECEIPT_EVENT_TYPE as _run_type
        from receipts import iter_receipts as _iter_receipts
    except Exception:  # noqa: BLE001
        return frozenset()
    newest, newest_dt = None, None
    try:
        for r in _iter_receipts(workspace_root, task_ids=["maintenance"]):
            raw = r.get("raw") if isinstance(r, dict) else None
            if not isinstance(raw, dict) or raw.get("type") != _run_type:
                continue
            when = r.get("dt")
            if when is None:
                continue
            if newest_dt is None or when >= newest_dt:
                newest, newest_dt = raw, when
    except Exception:  # noqa: BLE001 - a health read never breaks a surface
        return frozenset()
    data = (newest or {}).get("data")
    if not isinstance(data, dict):
        return frozenset()

    def _ids(key):
        out = set()
        for item in data.get(key) or []:
            job = item.get("job_id") if isinstance(item, dict) else item
            if isinstance(job, str):
                out.add(job)
        return out

    left = _ids("jobs_due") - _ids("jobs_completed") - _ids("jobs_failed")
    return frozenset(left & set(_ruled))


def honestly_due_line(workspace_root) -> str:
    """The ONE sentence the health check says when a skill-class job was left
    due by the merged fire: the existing waiting sentence
    (`maintenance_dispatcher.CONTAINER_REFUSAL_LINE`, D-6), never a failure.
    "" when nothing was left due."""
    if not honestly_due_jobs(workspace_root):
        return ""
    try:
        from maintenance_dispatcher import CONTAINER_REFUSAL_LINE
    except Exception:  # noqa: BLE001
        return ""
    return CONTAINER_REFUSAL_LINE


def _prep_leg_problems(workspace_root, reports):
    """SPEC BRIEFMERGE §D — the "brief ran, prep didn't" finding.

    Leg detail is only meaningful when the morning-brief TASK itself is
    firing: a task that is late or never authorized already gets its own
    task-level line, and a leg line under it would be noise about a fire that
    did not happen. So this returns ([], []) unless the morning-brief report
    exists, is receipted, and is not itself a problem — the same gate
    `_maintenance_job_problems` applies to job detail.

    Returns (findings, lines): at most ONE finding, with one plain-English
    sentence (facts + the one action, never a cause — R3).
    """
    brief = next((r for r in reports if r["task"] == "morning-brief"), None)
    if brief is None or not brief.get("last_fired"):
        return [], []
    if brief["status"] not in ("ok",) or brief.get("receipt_gap"):
        return [], []
    try:
        from prep_leg import prep_leg_finding

        finding = prep_leg_finding(workspace_root)
    except Exception:  # noqa: BLE001 — a health read never breaks a surface
        return [], []
    if not finding:
        return [], []
    return [finding], [finding["line"]]


def _brief_receipt_problems(workspace_root, reports, *, now=None):
    """SPEC BRIEFFIX1 Item C — "a brief posted without its receipt".

    Deliberately NOT gated on the morning-brief report being healthy the way
    `_prep_leg_problems` is, because the failure this catches is invisible to
    that gate: an EARLIER fire's receipt keeps the task looking on schedule
    while the NEWEST fire posted a digest and recorded nothing. That is the
    exact 2026-08-09 shape, and a gate on task health would have hidden it.

    What IS excluded is a morning-brief already sitting in the problems
    bucket — its task-level line exists and a second sentence about the same
    task is noise (the `check_task_failures` posture).

    Returns (findings, lines): at most one, fact-only.
    """
    brief = next((r for r in reports if r["task"] == "morning-brief"), None)
    if brief is None:
        return [], []
    if (brief["status"] in ("late", "never_authorized", "not_registered")
            or brief.get("receipt_gap")):
        return [], []
    # This module's `now` is MACHINE-LOCAL NAIVE (the clock cron evaluates in);
    # `orphan_brief_finding` compares against event timestamps, which are UTC.
    # Handing it a naive local instant would shift the comparison by the whole
    # UTC offset and the finding would simply never fire west of Greenwich. So
    # a naive value is dropped and the helper reads the real clock; an aware
    # one (a test freezing the instant) passes straight through.
    frozen = now if (now is not None and now.tzinfo is not None) else None
    try:
        from brief_receipt import orphan_brief_finding

        finding = orphan_brief_finding(workspace_root, now=frozen)
    except Exception:  # noqa: BLE001 — a health read never breaks a surface
        return [], []
    if not finding:
        return [], []
    return [finding], [finding["line"]]


# ---------------------------------------------------------------------------
# Hard-failure surfacing (HYG1 Item 4 — the dead-letter scheduled_task_failure)
# ---------------------------------------------------------------------------
#
# Orchestrators WRITE `scheduled_task_failure` on hard failures (dont-forget /
# upcoming-meetings / historical-backfill error contracts) but nothing ever
# READ the type — a task that fired and crashed mid-run looked healthy as
# long as its receipt landed, and the failure event was a dead letter. This
# reader closes the loop: recent failures surface in the health verdict as
# fact-only problem lines. R3's cause-fabrication ban applies verbatim —
# quote the event's own diagnostic, never speculate about why.

FAILURE_WINDOW_DAYS = 7

# The event's own diagnostic string, first non-empty of these data keys.
_FAILURE_DETAIL_KEYS = ("error", "reason", "message", "detail", "note", "summary")


def check_task_failures(workspace_root, *, now=None, reports=None,
                        exclude_tasks=None):
    """`scheduled_task_failure` events from the last FAILURE_WINDOW_DAYS,
    grouped by task (ids normalized via receipts.normalize_task_id over
    data.task_id → data.kind → source_skill), newest failure per task.

    Gating (mirrors R3's newest-fire rule): a failure OLDER than the task's
    newest successful receipt is history, not a finding — the task has
    demonstrably run clean since. A task with no receipt at all keeps its
    failure (there is nothing newer to vouch for it).

    MAINT1 attribution: dispatcher-owned silent jobs attribute to the
    failing sub-task when the event names one (its id is a MAINTENANCE_JOBS
    key), else to `maintenance`.

    `exclude_tasks`: task ids already in the verdict's problems bucket —
    their task-level line already exists; doubling it with the failure
    detail would be noise (same doctrine as the maintenance job findings).

    Returns (findings, lines): findings are
      {"task", "display_name", "ts" (ISO), "detail"} newest-first;
    lines are one fact-only sentence each — what failed, when (localized),
    the event's own diagnostic — plus the one action.
    """
    now = now or _now_local()
    exclude = set(exclude_tasks or ())
    floor = now - _dt.timedelta(days=FAILURE_WINDOW_DAYS)

    try:
        from maintenance_dispatcher import MAINTENANCE_JOBS
        _dispatcher_jobs = set(MAINTENANCE_JOBS)
    except Exception:
        _dispatcher_jobs = set()

    newest_by_task: dict[str, dict] = {}
    for ev in _iter_events(workspace_root):
        if ev.get("type") != "scheduled_task_failure":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        raw = d.get("task_id") or d.get("kind") or ev.get("source_skill") or ""
        tid = _normalize_task_id(raw) if isinstance(raw, str) else ""
        if not tid:
            continue
        # MAINT1 attribution: a dispatcher-owned sub-task keeps its own id
        # (it IS the named failing job); an unnameable dispatcher failure
        # arrives already stamped `maintenance` by the dispatcher itself.
        ts_raw = ev.get("ts") or d.get("ts") or ""
        try:
            when = _dt.datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue  # an undatable failure can't be windowed honestly
        when = _to_local_naive(when)
        if when is None or when < floor:
            continue
        detail = next(
            (str(d[k]).strip() for k in _FAILURE_DETAIL_KEYS
             if isinstance(d.get(k), str) and d.get(k).strip()),
            "no detail recorded",
        )
        prev = newest_by_task.get(tid)
        if prev is None or when > prev["_when"]:
            newest_by_task[tid] = {
                "task": tid,
                "display_name": task_display_name(tid),
                "ts": when.isoformat(),
                "detail": detail[:200],
                "_when": when,
            }

    if not newest_by_task:
        return [], []

    # Newest-successful-receipt gate: one shared read for every affected id.
    try:
        receipts_by_task = last_receipts(workspace_root, list(newest_by_task))
    except Exception:
        receipts_by_task = {}

    display_by_task = {}
    for r in reports or []:
        display_by_task[r.get("task")] = r.get("display_name")

    findings, lines = [], []
    for tid, f in sorted(newest_by_task.items(),
                         key=lambda kv: kv[1]["_when"], reverse=True):
        if tid in exclude:
            continue
        newest_receipt = receipts_by_task.get(tid)
        if newest_receipt is not None and newest_receipt > f["_when"]:
            continue  # ran clean since — history, not a finding
        name = display_by_task.get(tid) or f["display_name"]
        when_h = _human_time(f["_when"], now=now)
        lines.append(
            f"{name} hit an error mid-run at {when_h} — its own log says: "
            f"\"{f['detail']}\". Its next scheduled run will show whether it "
            "recovered, or run it now to check."
        )
        findings.append({k: v for k, v in f.items() if not k.startswith("_")})
    return findings, lines


# How fresh a run receipt must be for the vantage line to vouch that the
# tasks "look alive" — a week covers the sparsest default cadence (weekly).
_VANTAGE_FRESH = _dt.timedelta(days=7)


def _newest_second_slot(workspace_root, registered, *, now) -> Optional[_dt.datetime]:
    """The most recent SECOND-most-recent nominal slot across the registered
    tasks — the slot the MOST FREQUENT registered chat served one fire before
    last (SPEC_MERGEFIX1 §4 TRUTH1 behaviour 2: "older than the last two
    nominal slots of the most frequent registered task").

    Taking the maximum over the registered set IS "the most frequent task":
    the more often a task fires, the later its second-most-recent slot sits.
    Deriving it that way means there is no frequency metric to keep in step
    with the cron table. Returns None when nothing is registered or no cron
    parses, and the caller falls back to the freshness window.
    """
    entities = Path(workspace_root) / "_hq" / "data" / "entities.json"
    try:
        config = load_schedule_config(entities)
    except Exception:  # noqa: BLE001 — a sentence never fails on a config read
        return None
    best: Optional[_dt.datetime] = None
    for tid, spec in (config or {}).items():
        if not isinstance(spec, dict) or not spec.get("enabled"):
            continue
        if registered and tid not in registered:
            continue
        try:
            recent = effective_fires(spec.get("cron") or "", now=now, count=2)
        except CronParseError:
            continue
        if len(recent) >= 2 and (best is None or recent[1] > best):
            best = recent[1]
    return best


def detect_registry_vantage(workspace_root, task_records, *, now=None,
                            backend=None, tools=None) -> Optional[dict]:
    """What this chat can honestly say about a scheduler it cannot see.

    THREE registry states, and TRUTH1's whole point is that they never
    collapse into one another (SPEC_NIGHTM2_LANES §3 (a)):

      a list with records — the scheduler is in view; None here, normal
                            checks apply.
      `[]`                — the scheduler ANSWERED and holds nothing. That is
                            the F-40 guard's original case. On a legacy seat
                            the sentence is unchanged from today; on a CLOUD
                            seat an empty listing plus registration history
                            means the chats were set up in the older desktop
                            app and this account cannot see them (amendment
                            (e), gap analysis §5.3) — never "open a local
                            (non-cloud) chat", because on a merged build there
                            is no such chat to open.
      `None`              — there is no scheduler tool in this session at all.
                            The caller derives that from
                            `schedule_config.scheduler_availability(tools)`
                            and passes `task_records=None`; nothing here ever
                            probes a tool name (amendment (a)). The sentence
                            is the stopped one — unless a SCHEDULED receipt
                            landed recently, in which case something else is
                            still firing and the honest closer says so
                            (behaviour 3).

    `backend` is the id of the backend the caller actually selected
    ("cloud"/"legacy"). Omitted, it is derived from this workspace's own
    trigger map (`seat_backend`), whose conservative answer is "legacy" — the
    answer that keeps every un-merged seat byte-identical to today.

    `tools` is this session's tool names. It is what decides whether the
    stopped sentence may invite a setup: a scheduler has to be REACHABLE and
    its own gate open before "say `set up command room schedules`" is a true
    thing to say, and only the tool list can answer the first half. A caller
    that passes `backend="legacy"` and no tools is making a claim about a
    scheduler that is not in this session, and it is refused (fix round 1,
    REVIEW F-1).

    Returns None when the registry is visibly non-empty, or when the substrate
    shows no registration history either (a genuinely fresh install — "not set
    up yet" is then the honest verdict). Otherwise a finding dict:
      {check, schedule_created_seen, registered_recorded, newest_receipt,
       newest_scheduled_receipt, receipts_fresh, machine, backend, line}

    `machine` stays in the dict and NEVER reaches `line` (F16): the field is
    evidence for a reader of the finding, the line is a customer sentence, and
    a per-session id on a customer sentence is the leak class the v5.31.0
    attended test HOLDs on.
    """
    records = [r for r in (task_records or [])
               if isinstance(r, dict) and r.get("taskId")]
    if records:
        return None
    scheduler_answered = task_records is not None
    now = now or _now_local()
    ws_config = read_workspace_config(workspace_root)
    raw = ws_config.get("registered_taskIds")
    recorded = [t for t in raw if isinstance(t, str)] if isinstance(raw, list) else []

    seen_schedule_created = False
    newest_receipt: Optional[_dt.datetime] = None
    newest_scheduled: Optional[_dt.datetime] = None
    machine: Optional[str] = None
    for ev in _iter_events(workspace_root):
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "schedule_created":
            seen_schedule_created = True
            continue
        tid = _receipt_task_id(ev)
        if tid is None:
            continue
        dt = _to_local_naive(event_dt(ev))
        if dt is None:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if newest_receipt is None or dt > newest_receipt:
            newest_receipt = dt
            m = data.get("machine")
            machine = m.strip() if isinstance(m, str) and m.strip() else None
        # Behaviour 3 + the spec's own decoy. Only a SCHEDULED fire (a cron
        # fire, or the catch-up that serves a cron slot late) is evidence that
        # a SCHEDULER is alive. A `manual` receipt is evidence that a PERSON
        # typed the phrase — which is precisely what these sentences tell them
        # to do, so counting it would have the watchdog vouch for a scheduler
        # out of the customer's own typing. That is the shape of M's own book
        # on 2026-09-19, and it is this pin's decoy.
        via = _normalize_fired_via(data.get("fired_via"))
        if via in ("scheduled", "catchup") and (
                newest_scheduled is None or dt > newest_scheduled):
            newest_scheduled = dt

    if not seen_schedule_created and not recorded:
        return None

    backend_id = backend or seat_backend(workspace_root)
    fresh = newest_receipt is not None and (now - newest_receipt) <= _VANTAGE_FRESH
    finding = {
        "schedule_created_seen": seen_schedule_created,
        "registered_recorded": len(recorded),
        "newest_receipt": newest_receipt.isoformat() if newest_receipt else None,
        "newest_scheduled_receipt": (newest_scheduled.isoformat()
                                     if newest_scheduled else None),
        "receipts_fresh": fresh,
        # Evidence for a reader of the finding; NEVER rendered (F16).
        "machine": machine,
        "backend": backend_id,
    }

    if not scheduler_answered:
        # --- state (c): no scheduler tool in this session at all -----------
        #
        # WHOSE GATE (the defect the book replay caught, 2026-09-20). This
        # asked `_gate_allowed(seat_backend(...))`, and `seat_backend` answers
        # "legacy" for every workspace with no trigger map — which is every
        # workspace that has not registered the merged way, including M's own.
        # The legacy gate is ALWAYS open (the desktop fleet must keep working),
        # so the stopped sentence on the real book ended "say `set up command
        # room schedules` to turn them back on in this version" — an
        # invitation into a setup that cannot happen, on a seat whose whole
        # problem is that it has no scheduler. The legacy desktop scheduler is
        # not in this session BY DEFINITION of this branch, so its gate cannot
        # be the one that answers; the only backend this version could ever
        # reach is the cloud one.
        #
        # FIX ROUND 1 (REVIEW F-1). `_gate_allowed(backend or "cloud")` left
        # the same door open one argument further in: a caller passing
        # `backend="legacy"` — which is exactly what a surface does when it
        # hands the seat's own backend down alongside the records — reopened
        # the always-open legacy gate and brought the invitation back. The
        # invitation may appear ONLY when a scheduler is really reachable AND
        # its gate is open, and the one thing that knows is
        # `schedule_config.scheduler_availability(tools)` — the real seat, not
        # an id somebody passed. `tools` is this session's tool names; without
        # them the answer is the cloud gate's and a claimed `legacy` is
        # refused (`_vantage_gate_allowed`).
        gate_allowed = _vantage_gate_allowed(backend, tools, workspace_root)
        second_slot = _newest_second_slot(workspace_root, set(recorded), now=now)
        if newest_scheduled is None:
            still_firing = False
        elif second_slot is not None:
            still_firing = newest_scheduled >= second_slot - _FIRE_GRACE
        else:
            still_firing = (now - newest_scheduled) <= _VANTAGE_FRESH
        if still_firing:
            when = _human_time(newest_scheduled, now=now)
            line = (
                "I can't see your scheduler from this chat. Your scheduled "
                f"chats look alive: the most recent one ran {when}. "
                + ANOTHER_COMPUTER_LINE
            )
        else:
            when = (_human_time(newest_scheduled, now=now)
                    if newest_scheduled is not None else None)
            line = truth_line(when, gate_allowed=gate_allowed)
        finding["check"] = VANTAGE_UNREACHABLE
        finding["still_firing"] = still_firing
        finding["line"] = line
        return finding

    if backend_id == "cloud":
        # --- state (b) on a cloud seat: amendment (e) ----------------------
        # The gate asked is this seat's own backend's, which here is the cloud
        # row by construction — never the module default and never the legacy
        # row (REVIEW_COPY1 B-1's lesson, in the other direction).
        finding["check"] = VANTAGE_CLOUD
        finding["still_firing"] = False
        finding["line"] = (
            CLOUD_VANTAGE_LINE + " "
            + (CLOUD_VANTAGE_MOVE
               if _vantage_gate_allowed(backend_id, tools, workspace_root)
               else GATE_PROMISE)
        )
        return finding

    # --- state (b) on a legacy seat: F-40, unchanged but for F16 -----------
    opener = (
        "I can't see your scheduler from this chat — that usually means this "
        "is a cloud or remote session, or a different computer than the one "
        "your scheduled tasks run on."
    )
    closer = (
        "For a full check, open a local (non-cloud) chat on the computer "
        "where Command Room is set up."
    )
    if fresh:
        when = _human_time(newest_receipt, now=now)
        # F16 (c): this used to append " (on <data.machine>)". That token is a
        # per-SESSION id, not a machine name, and a raw id on a customer
        # sentence is the leak class the attended test HOLDs on. It is gone
        # from every state; the value stays on the finding for a reader.
        line = (f"{opener} Your tasks look alive: the most recent one ran "
                f"{when}. {closer}")
    elif newest_receipt is not None:
        when = _human_time(newest_receipt, now=now)
        line = (
            f"{opener} I can't tell from here whether your tasks are still "
            f"running — the most recent recorded run was {when}. {closer}"
        )
    else:
        line = (
            f"{opener} Your tasks were set up before, but there's no run "
            f"record I can read from here. {closer}"
        )
    finding["check"] = VANTAGE_REGISTRY
    finding["still_firing"] = fresh
    finding["line"] = line
    return finding


def _spoken_name(r: dict) -> str:
    """The name to SAY for a task report (EOD2).

    On a machine running a renamed predecessor, the registry's display name
    is not the name in the customer's Scheduled list. Every sentence about
    that row has to use the name they can find, or the action it recommends
    points at a row that is not there. `served_by` is None on every row of
    every workspace with no rename outstanding, so this is the identity
    function almost everywhere."""
    if r.get("served_by"):
        return task_display_name(r["served_by"])
    return r["display_name"]


def _caught_up_line(r: dict, now=None) -> str:
    """Dated catch-up render (F-43 P2c's fix): name WHEN it caught up and
    which slot it served — facts only, no cause."""
    name = _spoken_name(r)
    fired = _human_time(_dt.datetime.fromisoformat(r["last_fired"]), now=now)
    sched_iso = (r.get("catchup") or {}).get("scheduled_for")
    if sched_iso:
        try:
            sched = _human_time(_dt.datetime.fromisoformat(sched_iso), now=now)
            return (
                f"Your {name} caught up {fired} — its {sched} run didn't "
                f"happen on time. The work is done, just later than scheduled."
            )
        except ValueError:
            pass
    return (
        f"Your {name}'s most recent run was a late catch-up ({fired}), not an "
        f"on-schedule fire. The work is done, just later than scheduled."
    )


def _first_run_line(r: dict, now=None) -> str:
    """never_fired render (F-43 P1a's fix): a task with zero receipts has NO
    fire history to speak of — say so, and name the real next fire time."""
    name = _spoken_name(r)
    if r.get("next_fire"):
        try:
            nxt = _human_time(_dt.datetime.fromisoformat(r["next_fire"]), now=now)
            return (
                f"Your {name} task hasn't had its first run yet — its next "
                f"scheduled run is {nxt}."
            )
        except ValueError:
            pass
    return f"Your {name} task hasn't had its first run yet."


#: HEALTH3 MUST 1 (F-T2-7): the per-task line the health check's task table
#: shows, one per counted task on a cloud seat. The times are the APP's
#: (`lastRunAt`) and the next scheduled run; nothing here is a receipt claim.
TASK_STAMP_LINE = "<Chat>: the app last ran it <last>; next <next>."
#: ...when the app has not run the task yet.
TASK_STAMP_FIRST_LINE = "<Chat>: the app has not run it yet; first run <next>."
#: ...when the app gave a last-run value this code cannot read (review N-3):
#: nothing is claimed about the run, only the next one is named.
TASK_STAMP_NEXT_LINE = "<Chat>: next run <next>."
#: The clause each form drops when no next run can be named.
_STAMP_NEXT_CLAUSES = ("; next <next>", "; first run <next>")


def task_stamp_line(r: dict, now=None) -> str:
    """One report's task-table line: "<Name>: the app last ran it <time>;
    next <time>." or, when the app's `lastRunAt` is null, "<Name>: the app
    has not run it yet; first run <time>.". Times through `_human_time`, on
    this seat's clock. A next run that cannot be named drops its clause."""
    name = _spoken_name(r)
    last = nxt = ""
    if r.get("last_run_at"):
        try:
            last = _human_time(_dt.datetime.fromisoformat(r["last_run_at"]), now=now)
        except ValueError:
            last = ""
    if r.get("next_fire"):
        try:
            nxt = _human_time(_dt.datetime.fromisoformat(r["next_fire"]), now=now)
        except ValueError:
            nxt = ""
    if not last and r.get("last_run_unread"):
        if not nxt:
            return ""
        return TASK_STAMP_NEXT_LINE.replace("<Chat>", name).replace("<next>", nxt)
    template = TASK_STAMP_LINE if last else TASK_STAMP_FIRST_LINE
    if not nxt:
        for clause in _STAMP_NEXT_CLAUSES:
            template = template.replace(clause, "")
    return (template.replace("<Chat>", name).replace("<last>", last)
            .replace("<next>", nxt))


#: HEALTH3 MUST 3 (walk row 2 (d)): the ONE sentence naming another
#: computer's switched-off copies of this workspace's chats. They are not
#: counted anywhere; this says so once, instead of saying nothing.
FOREIGN_SWITCHED_OFF_LINE = (
    "<N> copies of your scheduled chats on another computer are switched off "
    "and not counted."
)
#: ...its singular.
FOREIGN_SWITCHED_OFF_ONE = (
    "1 copy of your scheduled chats on another computer is switched off and "
    "not counted."
)


def foreign_switched_off_line(n) -> str:
    """The info sentence for `n` switched-off copies on another computer, or
    "" when there are none."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n == 1:
        return FOREIGN_SWITCHED_OFF_ONE
    return FOREIGN_SWITCHED_OFF_LINE.replace("<N>", str(n))


def task_stamp_lines(reports, now=None) -> list:
    """`task_stamp_line` for EVERY counted task: each registered report (a
    task the app does not hold has no app time to show)."""
    lines = [task_stamp_line(r, now=now) for r in reports or []
             if r.get("registered") and r.get("status") != "not_registered"]
    return [ln for ln in lines if ln]


#: The receipt types SAFETY0 counts — EVERY type in the receipt contract's
#: registry (HYGIENE3; the 2026-09-23 walk's other computer wrote end-of-day,
#: reconcile and maintenance receipts, and a count of `pack_run` alone said
#: "1 scheduled chat"). Read from the registry, never re-spelled here.
FOREIGN_WRITER_RECEIPT_TYPES = tuple(sorted(
    set().union(*(spec.get("types") or () for spec in _RECEIPT_TYPES.values()))))


def _task_family(task_id) -> str:
    """The scheduled CHAT a receipt's task belongs to - what "N scheduled
    chats" counts. A maintenance job (`maintenance_dispatcher.MAINTENANCE_JOBS`)
    runs inside the one maintenance chat; a predecessor id is its successor's
    chat (`receipts.TASK_PREDECESSORS`: `past-meetings` is `end-of-day`);
    anything else is its own chat."""
    tid = str(task_id or "")
    try:
        from maintenance_dispatcher import MAINTENANCE_JOBS
        if tid in MAINTENANCE_JOBS:
            return "maintenance"
    except Exception:  # noqa: BLE001 — without the registry, a job is its own chat
        pass
    try:
        from receipts import TASK_PREDECESSORS
        for successor, predecessors in TASK_PREDECESSORS.items():
            if tid in predecessors and len(
                    [s for s, p in TASK_PREDECESSORS.items() if tid in p]) == 1:
                return successor
    except Exception:  # noqa: BLE001
        pass
    return tid


def foreign_writer_count(workspace_root, since=None) -> dict:
    """How much of this workspace's scheduled work another computer is still
    writing, since the scheduled writer was declared (SAFETY0, RETIRE1 R-2).

    Counts every receipt in the receipt contract's registry
    (`FOREIGN_WRITER_RECEIPT_TYPES`) newer than `since` (default: the
    declaration's own `declared_at`) whose `data.machine` is not the declared
    writer id — a `claude-XXXX` token, a hostname, another `acct-` id — or IS
    the declared id but carries a `device_digest` that differs from the
    declaration's (the same account on another computer, HYGIENE3 / D-4).
    Returns

        {"n": receipts, "writers": [sorted machine tokens], "tasks": [sorted
         task ids], "families": [sorted chat families], "declared": id|None,
         "since": str|None, "line": str}

    `line` is `schedule_config.other_writers_line(len(families))` — the
    sentence names how many CHATS (a maintenance job is the maintenance
    chat; `past-meetings` is the end-of-day chat — `_task_family`), never how
    many receipts, and never the token itself (F16: a machine field is
    evidence, not text).

    What is NOT counted, each deliberately:
      * a receipt at or before the declaration — that history predates the
        rule (the `since` filter);
      * a receipt with no `machine` at all — it cannot be attributed to any
        computer, so it cannot be said to be another one;
      * a receipt whose `fired_via` is `manual` — a person asking by hand on
        another computer is not a scheduled writer, and the sentence is about
        scheduled chats.

    No declaration → `n == 0` and no line: without a declared writer there is
    no "other" computer to name, and the un-declared fleet reads exactly as it
    does today. READ ONLY; never raises.
    """
    out = {"n": 0, "writers": [], "tasks": [], "families": [], "declared": None,
           "since": None, "line": ""}
    try:
        from schedule_config import other_writers_line, scheduled_writer

        decl = scheduled_writer(workspace_root)
    except Exception:  # noqa: BLE001
        return out
    if not decl:
        return out
    declared = decl["writer_id"]
    declared_digest = decl.get("device_digest")
    out["declared"] = declared
    floor = since if since is not None else decl.get("declared_at")
    floor_dt = parse_ts(floor) if isinstance(floor, str) else None
    if floor_dt is None:
        # No moment to count from — say nothing rather than count all of
        # history against a rule that did not exist yet.
        return out
    out["since"] = floor
    n = 0
    writers, tasks, families = set(), set(), set()
    try:
        import events_io

        rows = events_io.iter_events(workspace_root, since_ts=floor)
    except Exception:  # noqa: BLE001
        rows = _iter_events(workspace_root)
    try:
        for ev in rows:
            if not isinstance(ev, dict):
                continue
            if ev.get("type") not in FOREIGN_WRITER_RECEIPT_TYPES:
                continue
            when = event_dt(ev)
            if when is None or when <= floor_dt:
                continue
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            machine = data.get("machine")
            if not isinstance(machine, str) or not machine.strip():
                continue
            machine = machine.strip()
            if machine == declared:
                digest = data.get("device_digest")
                if not (declared_digest and isinstance(digest, str)
                        and digest and digest != declared_digest):
                    continue
            if _normalize_fired_via(data.get("fired_via")) == "manual":
                continue
            n += 1
            writers.add(machine)
            task = _receipt_task_id(ev) or str(ev.get("type"))
            tasks.add(task)
            families.add(_task_family(task))
    except Exception:  # noqa: BLE001 — an unreadable ledger never alarms
        return out
    out["n"] = n
    out["writers"] = sorted(writers)
    out["tasks"] = sorted(tasks)
    out["families"] = sorted(families)
    out["line"] = other_writers_line(len(families)) if n else ""
    return out


def _foreign_writers(workspace_root) -> dict:
    """`foreign_writer_count` for the verdict — never raises into it."""
    try:
        return foreign_writer_count(workspace_root)
    except Exception:  # noqa: BLE001
        return {"n": 0, "writers": [], "tasks": [], "families": [],
                "declared": None, "since": None, "line": ""}


def health_verdict(workspace_root, *, task_records=None, now=None,
                   backend=None, tools=None, device_path=None) -> dict:
    """R3's one entry point for every health surface (system-health, cleanup's
    weekly pass, any future daily pass). Vantage guard first, then the
    partition: every counted task lands in exactly ONE bucket, and the
    summary counts come from the partition — so a task named in any warning
    can never simultaneously count inside "everything's running" (F-43's
    self-contradiction, made structurally impossible).

    Buckets (priority order — first match wins):
      problems          — late / never_authorized / platform_declined /
                          missing first-install / receipt_gap: something
                          needs the user (or, for a decline, needs saying).
      caught_up         — newest fire served its slot late (dated info line).
      first_run_pending — registered, zero receipts, inside the grace window.
      on_schedule       — receipted within cadence, served on time.
    Later-add tasks that simply aren't registered are excluded from the
    partition entirely (change-schedule owns that render).

    Returns:
      {vantage (None|finding), reports, on_schedule, caught_up,
       first_run_pending, problems (task-id lists), summary_line,
       lines (problem sentences incl. binding), info_lines (dated catch-up +
       first-run sentences)}

    `tools` is this session's tool names, passed straight through to the
    vantage guard: it is what tells the stopped sentence whether a scheduler
    is really reachable before it invites a setup (fix round 1, REVIEW F-1).

    `device_path` (HEALTH3 MUST 1) is this seat's folder on the customer's
    computer. With it, only THIS seat's records are counted
    (`split_seat_records`); the other computer's are returned beside the
    reports as `foreign_records`. On a cloud seat the verdict also carries
    `stamp_lines`: the app's last-run time and the next run, one line per
    counted task (`task_stamp_lines`), the health check's task table; each
    counted report carries its own line as `stamp_line` too.
    """
    now = now or _now_local()
    seat = split_seat_records(task_records, device_path)
    # TRUTH1 (§3 (a)) — `task_records` is passed through UNCHANGED, never
    # `task_records or []`: `None` ("no scheduler tool in this session") and
    # `[]` ("the scheduler answered and holds nothing") are two different
    # facts with two different sentences, and collapsing them is the defect.
    vantage = detect_registry_vantage(workspace_root, task_records, now=now,
                                      backend=backend, tools=tools)
    # SAFETY0 (RETIRE1 R-2): another computer still writing scheduled chats
    # into a workspace that has declared its one writer. The sentence rides
    # the verdict itself so every surface that already renders the verdict
    # verbatim — the health check, the Monday note — says it with no new
    # step: appended to the vantage line when the scheduler cannot be seen
    # (the one sentence those surfaces print in that state), and as one more
    # problem line otherwise. No declaration → nothing changes.
    foreign = _foreign_writers(workspace_root)
    if vantage is not None:
        if foreign["line"]:
            # The count is the more exact form of "another computer is still
            # running them", so it REPLACES that closer instead of repeating
            # it; any other vantage sentence gains it at the end.
            vantage = dict(vantage)
            said = str(vantage.get("line") or "")
            if ANOTHER_COMPUTER_LINE in said:
                said = said.replace(ANOTHER_COMPUTER_LINE, foreign["line"])
            else:
                said = (said.rstrip() + " " + foreign["line"]).strip()
            vantage["line"] = said
        return {
            "vantage": vantage,
            "foreign_writers": foreign,
            "reports": [],
            "on_schedule": [], "caught_up": [],
            "first_run_pending": [], "problems": [],
            "maintenance_jobs": [],
            "legs": [],
            "brief_receipt": [],
            "task_failures": [],
            "summary_line": vantage["line"],
            "lines": [],
            "info_lines": [],
            "stamp_lines": [],
            "foreign_records": seat["foreign_records"],
        }

    reports = check_tasks(workspace_root, now=now, task_records=seat["own"])
    stamp_lines = (task_stamp_lines(reports, now=now)
                   if (backend or seat_backend(workspace_root)) == "cloud" else [])
    # ...and each counted report carries its own line as `stamp_line`, so a
    # surface that relays `reports` (the health check's helper) has the table
    # without a key of its own. Only on a cloud seat: a legacy report is
    # byte-identical to before.
    if stamp_lines:
        for r in reports:
            if r.get("registered") and r.get("status") != "not_registered":
                _line = task_stamp_line(r, now=now)
                if _line:
                    r["stamp_line"] = _line
    binding = check_workspace_binding(workspace_root)
    on_schedule, caught_up, first_run, problems = [], [], [], []
    for r in reports:
        if r["status"] == "not_registered" and not r["first_install"]:
            continue  # later-add: expected, uncounted; change-schedule renders it
        if r["status"] in ("late", "never_authorized", "not_registered",
                           "platform_declined") or r["receipt_gap"]:
            problems.append(r)
        elif r["caught_up"]:
            caught_up.append(r)
        elif r["status"] == "never_fired":
            first_run.append(r)
        else:
            on_schedule.append(r)

    lines = plain_english_lines(reports, binding=binding,
                                workspace_path=workspace_root)
    info_lines = [_caught_up_line(r, now=now) for r in caught_up]
    info_lines += [_first_run_line(r, now=now) for r in first_run]
    # T2 FIRE1 MUST 3: a skill-class job the merged fire left due is waiting,
    # not failed - one info sentence, never a problem line or a count.
    _due_line = honestly_due_line(workspace_root)
    if _due_line:
        info_lines.append(_due_line)
    # HEALTH3 MUST 3: another computer's SWITCHED-OFF copies are in no count
    # (the split above); they are named once here. Its ENABLED copies keep
    # the foreign-writer sentence below, unchanged.
    _off_line = foreign_switched_off_line(seat["foreign_disabled"])
    if _off_line:
        info_lines.append(_off_line)

    # MAINT1 (D8): per-JOB receipt gaps inside a healthy maintenance task —
    # the task fired, a job chronically wrote nothing. Job findings ride the
    # problem lines/counts but never move the task out of its bucket (the
    # task DID run on schedule; the job inside it is what needs eyes).
    job_problems, job_lines = _maintenance_job_problems(
        workspace_root, reports, now=now
    )
    lines += job_lines

    # SPEC BRIEFMERGE §D: the merged morning-brief fire runs two legs, so a
    # fire can be on schedule and still have half-failed. The combined receipt
    # records both; this reads the newest one and surfaces "brief ran, prep
    # didn't" — the sentence nothing could say while prep was a separate task
    # that could die in silence. Same posture as the job findings above: a leg
    # finding never moves the TASK out of its bucket (the brief did run), it
    # rides the problem lines and the attention count.
    leg_problems, leg_lines = _prep_leg_problems(workspace_root, reports)
    lines += leg_lines

    # SPEC BRIEFFIX1 Item C: the other half of the same fire's honesty. The
    # leg finding above says "the brief ran, the prep didn't"; this one says
    # "a brief posted and nothing recorded it" — the state where every other
    # signal here reads healthy because an older fire's receipt is still the
    # newest one. It rides the problem lines/count exactly like the leg
    # finding and never moves the task out of its bucket.
    receipt_problems, receipt_lines = _brief_receipt_problems(
        workspace_root, reports, now=now
    )
    lines += receipt_lines

    # HYG1 Item 4: recent hard failures (scheduled_task_failure) ride the
    # problem lines/counts like the job findings — a failure never moves a
    # task out of its partition bucket (its receipt may genuinely be on
    # schedule; the mid-run crash is what needs eyes). Tasks already in the
    # problems bucket are excluded — their task-level line exists.
    failure_findings, failure_lines = check_task_failures(
        workspace_root, now=now, reports=reports,
        # A failure event names the id that FAILED, which on a machine
        # running a renamed predecessor is the predecessor (EOD2). Exclude
        # both spellings, or a task already carrying a problem line collects
        # a second one under its other name.
        exclude_tasks={r["task"] for r in problems}
                      | {r["served_by"] for r in problems if r.get("served_by")},
    )
    lines += failure_lines
    if foreign["line"]:
        lines.append(foreign["line"])

    total = len(on_schedule) + len(caught_up) + len(first_run) + len(problems)
    fresh_unregistered = (
        total > 0
        and len(problems) == total
        and all(r["status"] == "not_registered" for r in problems)
    )
    if total == 0 or fresh_unregistered:
        # Nothing registered and no run history claimed for anything — a
        # genuinely fresh install. One honest line beats N identical
        # "missing from the schedule" flags; the per-task lines collapse
        # (the binding finding, if any, stays).
        summary = fresh_install_line(workspace_root, tools=tools, backend=backend)
        if fresh_unregistered:
            lines = plain_english_lines([], binding=binding,
                                        workspace_path=workspace_root)
            if foreign["line"]:
                lines.append(foreign["line"])
    elif not lines and not caught_up and not first_run:
        newest = max(
            (r for r in on_schedule if r["last_fired"]),
            key=lambda r: r["last_fired"],
            default=None,
        )
        recency = ""
        if newest:
            when = _human_time(
                _dt.datetime.fromisoformat(newest["last_fired"]), now=now
            )
            recency = f", most recently {newest['display_name']} at {when}"
        summary = (
            f"Everything's running. All {total} of your scheduled chats and "
            f"background tasks ran on their normal schedule{recency}."
        )
    else:
        parts = [
            f"{len(on_schedule)} of {total} scheduled chats and background "
            f"tasks ran on their normal schedule"
        ]
        if caught_up:
            parts.append(
                f"{len(caught_up)} caught up late" if len(caught_up) > 1
                else "1 caught up late"
            )
        if first_run:
            parts.append(
                f"{len(first_run)} are waiting on their first run" if len(first_run) > 1
                else "1 is waiting on its first run"
            )
        n_attention = (len(problems) + len(job_problems) + len(leg_problems)
                       + len(receipt_problems) + len(failure_findings)
                       + (1 if foreign["line"] else 0))
        if n_attention:
            parts.append(
                f"{n_attention} need attention" if n_attention > 1
                else "1 needs attention"
            )
        summary = "; ".join(parts) + "."

    return {
        "vantage": None,
        "reports": reports,
        "on_schedule": [r["task"] for r in on_schedule],
        "caught_up": [r["task"] for r in caught_up],
        "first_run_pending": [r["task"] for r in first_run],
        # Job-level findings count as problems (brief_watchdog_line's count,
        # the "N need attention" math) under a namespaced id so consumers can
        # tell a task from a job inside the maintenance task.
        "problems": [r["task"] for r in problems]
                    + [f"maintenance:{f['job']}" for f in job_problems]
                    + [f"leg:{f['leg']}" for f in leg_problems]
                    + [f"receipt:{f['check']}" for f in receipt_problems]
                    + [f"failure:{f['task']}" for f in failure_findings]
                    + (["writer:foreign"] if foreign["line"] else []),
        "foreign_writers": foreign,
        "maintenance_jobs": job_problems,
        "legs": leg_problems,
        "brief_receipt": receipt_problems,
        "task_failures": failure_findings,
        "summary_line": summary,
        "lines": lines,
        "info_lines": info_lines,
        "stamp_lines": stamp_lines,
        "foreign_records": seat["foreign_records"],
    }


def brief_watchdog_line(workspace_root, *, verdict=None, now=None):
    """The morning brief's LIGHT daily watchdog pass (v4.6.1 S3 — the R3
    discovery: system-health's docstring promised this line while the
    morning-brief orchestrator called nothing, so the brief inherited no
    watchdog at all).

    ONE line, receipts-only, derived SOLELY from health_verdict's
    partition — no per-task detail, no cause guessing, no second scan:

      problems > 0   → "N of your background tasks need attention — say
                        health check for the detail."
      problems == 0  → None (never pad the brief with an all-clear; the
                        brief's job is today's work, not green checkmarks)
      cloud vantage  → None (this chat can't see the scheduler; staying
                        quiet beats a false alarm — system-health owns
                        the vantage explanation when asked directly)

    Pass a precomputed `verdict` to avoid a second receipt scan when the
    caller already ran health_verdict this fire.
    """
    if verdict is None:
        verdict = health_verdict(workspace_root, now=now)
    if verdict.get("vantage") is not None:
        return None
    n = len(verdict.get("problems") or [])
    if n == 0:
        return None
    if n == 1:
        return ("1 of your background tasks needs attention — "
                "say health check for the detail.")
    return (f"{n} of your background tasks need attention — "
            "say health check for the detail.")


def check_schedule_parity(workspace_root, registered_ids=None) -> dict:
    """R2 schedule-parity check (cleanup's weekly pass). Detect + report —
    NO config writes; `schedule_config` stays a sparse override store.

    Mismatch classes:
      ghost_first_install — enabled first-install task absent from the
        registered set: real breakage, flag in the Monday note.
      ghost_later_add     — enabled later-add task not registered: EXPECTED
        (deliberately not first-install) — say nothing; R3's proposal step
        owns the nudge.
      orphan_overrides    — schedule_config entries for taskIds that exist
        in neither DEFAULT_SCHEDULES nor the registered set (e.g. legacy
        cr-* keys): flag in the Monday note. Removal would be the only
        heal and cleanup never removes — flag-only (R2 reframed; the
        original densify-heal died with old-R1).
    """
    from schedule_config import DEFAULT_SCHEDULES, load_schedule_view

    ws_config = read_workspace_config(workspace_root)
    if registered_ids is None:
        raw = ws_config.get("registered_taskIds")
        registered_ids = set(raw) if isinstance(raw, list) else set()
    registered_ids = set(registered_ids)

    entities = Path(workspace_root) / "_hq" / "data" / "entities.json"
    view = load_schedule_view(entities, registered_ids)
    ghosts_first, ghosts_later = [], []
    for tid, spec in view.items():
        if spec["enabled"] and not spec["registered"]:
            (ghosts_later if spec["later_add"] else ghosts_first).append(tid)

    # MAINT1: superseded taskIds (the five old silent tasks) are disabled by
    # migration, not removed — an override left behind for one of them is
    # expected history, never drift. Same for the `maintenance_jobs` sub-dict
    # (change-schedule's job-level pause store), which shares the
    # schedule_config namespace but is not a taskId.
    from schedule_config import RETIRED_TASKS, SUPERSEDED_BY

    superseded = {t for ids in SUPERSEDED_BY.values() for t in ids}
    # EOD2 / REVIEW F-1 — a RENAMED predecessor's override key is not an
    # orphan: `load_schedule_config` inherits it onto the successor's row, so
    # it is still doing its job whether or not the customer has switched. The
    # pre-fix code only stayed quiet about it by accident (the second clause,
    # `tid not in registered_ids`), which is exactly why F-1 was silent — and
    # that accident stops holding the moment the customer takes the rename.
    # Flagging a LIVE override as drift would send them to delete their own
    # schedule.
    renamed = {t for t, spec in RETIRED_TASKS.items() if spec.get("renamed_to")}
    orphans = []
    try:
        data = json.loads(entities.read_text(encoding="utf-8"))
        overrides = ((data.get("workspace") or {}).get("schedule_config") or {})
        for tid in overrides:
            if tid == "maintenance_jobs" or tid in superseded or tid in renamed:
                continue
            if tid not in DEFAULT_SCHEDULES and tid not in registered_ids:
                orphans.append(tid)
    except (OSError, json.JSONDecodeError):
        pass

    return {
        "ghost_first_install": sorted(ghosts_first),
        "ghost_later_add": sorted(ghosts_later),
        "orphan_overrides": sorted(orphans),
    }


def check_workspace_binding(workspace_root) -> Optional[dict]:
    """The folder-rename case (JS 07-01): the stored binding no longer
    matches reality. Returns a finding dict, or None when the binding is
    healthy / no binding is recorded yet."""
    ws_config = read_workspace_config(workspace_root)
    stored = (ws_config.get("workspace_basename") or "").strip()
    if not stored:
        return None
    actual = Path(workspace_root).name
    if stored == actual:
        return None
    return {
        "check": "workspace_binding",
        "stored_basename": stored,
        "actual_basename": actual,
        "fix": "re-run `set up command room schedules` to re-bind",
    }


def check_prompt_versions(task_records, installed_version: str) -> list[dict]:
    """W4 stale-prompt drift: compare each registered prompt's stamped
    plugin version against the installed one. Unstamped prompts predate the
    stamp — reported as informational (`stamped: False`)."""
    findings = []
    for rec in task_records or []:
        if not isinstance(rec, dict):
            continue
        tid = rec.get("taskId")
        prompt = rec.get("prompt") or ""
        m = _VERSION_STAMP_RE.search(prompt)
        if not m:
            findings.append({"task": tid, "stamped": False, "stale": False})
        elif installed_version and m.group(1) != installed_version.lstrip("v"):
            findings.append({
                "task": tid, "stamped": True, "stale": True,
                "prompt_version": m.group(1), "installed_version": installed_version,
            })
    return findings


def normalize_prompt_stamp(prompt: str) -> str:
    """BRIDGESIL1 (2026-08-27) — blind a bootloader-prompt comparison to the
    diagnostic plugin-version stamp, one direction of the same parse
    `_VERSION_STAMP_RE` already does the other way in `check_prompt_versions`
    above. Every version bump changes the stamp, so a raw hash/string compare
    of two composed bootloaders always differs across a release even when
    nothing a customer would call "content" changed — exactly the defect
    BUG_2026-08-16 and BUG_2026-08-19 filed: the bridge's W4 refresh and
    `enable-command-room-schedules` Step 1.C both hash-compared the RAW
    strings, so every plugin upgrade rewrote every registered prompt (seven
    `update_scheduled_task` calls on M's own v5.13.0 install, for a diff
    `git diff` proved was empty). Normalizing here — not deleting the stamp —
    keeps the watchdog's own read (`check_prompt_versions`, which parses the
    UNNORMALIZED registered prompt) unaffected; this is a write-gate helper
    only, used exclusively to decide whether a refresh has anything real to
    do.
    """
    return _VERSION_STAMP_RE.sub("plugin-version: <normalized>", prompt or "")


def plain_english_lines(reports, *, binding=None, include_ok: bool = False,
                        workspace_path=None, include_stamps: bool = False,
                        now=None) -> list[str]:
    """One sentence per problem — the ONLY watchdog voice any surface uses.

    Facts + the one action, never a cause the watchdog can't know (R3 —
    the pre-R3 `late` line asserted "the computer was asleep", the exact
    fabricated-narrative class F-10/F-43/F-47 catalogued; when a gap is
    unexplained, say what is known and stop). Scheduler stamps are quoted
    AS the schedule's claim ("the schedule shows..."), never as fact — F-39
    proved they land without execution. No jargon, no taskIds, no event
    names — display names + the exact next action.

    `workspace_path` (IDPOLISH1, additive/optional): when passed, the "since
    <date>" sentence below localizes to the WORKSPACE's configured tz
    instead of the machine's OS tz (see `_since_display`) — display only;
    the `late` verdict itself was already decided by `check_tasks` on the
    machine clock, upstream of this function, and stays that way regardless
    of this argument. Omitted (the default), this renders byte-identical to
    before IDPOLISH1.

    `include_stamps` (HEALTH3 MUST 1): the problem lines are followed by one
    `task_stamp_line` for EVERY counted task (`task_stamp_lines`). The verdict
    keeps the two apart (`lines` / `stamp_lines`): a stamp is a fact about
    every task, never a problem, and counted as one it would turn an all-clear
    into "N need attention".
    """
    if include_stamps:
        return (plain_english_lines(reports, binding=binding,
                                    include_ok=include_ok,
                                    workspace_path=workspace_path)
                + task_stamp_lines(reports, now=now))
    lines: list[str] = []
    backend = seat_backend(workspace_path)
    if binding:
        lines.append(folder_renamed_line(binding['stored_basename'],
                                         binding['actual_basename'],
                                         workspace_path, backend=backend))
    for r in reports:
        # EOD2 — say the name the customer's own Scheduled list shows. On a
        # machine still running the renamed predecessor, "your End of Day
        # task hasn't run" names a row they cannot find; the honest sentence
        # names the entry that is actually there.
        name = _spoken_name(r)
        stamp_phrase = ""
        if r.get("last_run_at"):
            try:
                stamp_phrase = " " + _human_time(_dt.datetime.fromisoformat(r["last_run_at"]))
            except ValueError:
                stamp_phrase = ""
        if r["status"] == "platform_declined":
            # R-M1-6. Facts + the one action, and the action is NOT a repair:
            # the limit is the platform's, it clears by itself, and the only
            # thing the customer can do is ask for the work now. Naming the
            # computer here would send them to fix something that is not
            # broken — the failure mode F16 is the sentence-level twin of.
            lines.append(platform_declined_line(name, r["task"]))
        elif r["status"] == "never_authorized":
            if backend == "cloud":
                # COPY1 — one sentence for both halves on a cloud seat, and it
                # is the same sentence either way: a fire that ran without the
                # folder recorded nothing, which is what `receipt_gap` sees
                # from here. The cause is the waiting card, not a permission
                # click that no longer exists.
                lines.append(never_authorized_line(name))
            elif r["receipt_gap"]:
                # The scheduler claims runs but the substrate has nothing —
                # never call that "waiting on permission" (F-39 class:
                # lastRunAt stamps land without execution).
                lines.append(
                    f"The schedule shows runs for your {name} task"
                    f"{f' (latest{stamp_phrase})' if stamp_phrase else ''}, but it has never "
                    f"recorded any work — open it once and check the result looks right."
                )
            else:
                lines.append(
                    f"Your {name} task has never run — it's likely still waiting on its one-time "
                    f"permission. Open it once to authorize it."
                )
        elif r["status"] == "late":
            since = _since_display(r.get("last_fired_raw"), r.get("last_fired"),
                                    workspace_path=workspace_path)
            since_phrase = f" since {since}" if since else ""
            if r["receipt_gap"]:
                lines.append(
                    f"The schedule shows your {name} task ran{stamp_phrase}, but it hasn't "
                    f"recorded any work{since_phrase} — {typed_action(r['task'])} once and "
                    f"check the result looks right."
                )
            else:
                # What is KNOWN: no record since <date>. The cause is not
                # knowable from here — never assert one (R3).
                lines.append(
                    f"Your {name} task hasn't run{since_phrase} — I can't tell from here why it "
                    f"stopped. {typed_action(r['task'], capital=True)} to catch it up."
                )
        elif r["status"] == "not_registered" and r["first_install"]:
            lines.append(missing_task_line(name, workspace_path, backend=backend))
        elif r["receipt_gap"] and r["status"] == "ok":
            lines.append(
                f"Your {name} task ran but didn't record its work — "
                f"{typed_action(r['task'])} once to check the last run looks right."
            )
        elif include_ok and r["status"] == "ok":
            lines.append(f"{name}: running on schedule.")
    return lines


__all__ = [
    "RECEIPT_SPECS",
    # HEALTH3 (Train 2b §3) - the app's time, this seat's records.
    "TASK_STAMP_FIRST_LINE",
    "TASK_STAMP_LINE",
    "TASK_STAMP_NEXT_LINE",
    "FOREIGN_SWITCHED_OFF_LINE",
    "FOREIGN_SWITCHED_OFF_ONE",
    "app_last_run",
    "foreign_switched_off_line",
    "nothing_stamped_jobs",
    "registered_after_slot",
    "schedule_created_times",
    "pick_task_records",
    "split_seat_records",
    "stored_row_is_this_seat",
    "task_registered_at",
    "task_stamp_line",
    "task_stamp_lines",
    "brief_watchdog_line",
    "check_maintenance_jobs",
    "check_tasks",
    "check_schedule_parity",
    "check_workspace_binding",
    "check_prompt_versions",
    "detect_registry_vantage",
    "effective_fires",
    "expected_fires",
    "expected_fires_multi",
    "health_verdict",
    "MIN_GAP_MINUTES",
    "last_receipts",
    "late_signals",
    "next_fire",
    "normalize_prompt_stamp",
    "plain_english_lines",
    "read_workspace_config",
    "NEVER_AUTHORIZED_LINE",
    "never_authorized_line",
    "seat_backend",
    # TRUTH1 (SPEC_NIGHTM2_LANES §3) — the post-merge sentences and the
    # states that earn them. `TRUTH_LINE` is the name HEAL1 imports.
    "ANOTHER_COMPUTER_LINE",
    # RETIRE1 (Night M3, SAFETY0) — the foreign-writer count.
    "FOREIGN_WRITER_RECEIPT_TYPES",
    "foreign_writer_count",
    "CLOUD_VANTAGE_LINE",
    "CLOUD_VANTAGE_MOVE",
    "GATE_PROMISE",
    "PLATFORM_DECLINED_LINE",
    "PLATFORM_DECLINE_REASONS",
    "PLATFORM_DECLINE_STATUSES",
    "REOPEN_LINE",
    "TRUTH_LINE",
    "TYPED_PHRASE_BY_TASK",
    "VANTAGE_CLOUD",
    "VANTAGE_REGISTRY",
    "VANTAGE_UNREACHABLE",
    "platform_decline",
    "platform_declined_line",
    "truth_line",
    "typed_action",
    "typed_phrase",
]
