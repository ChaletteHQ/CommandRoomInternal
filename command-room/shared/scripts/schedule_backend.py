"""Schedule backend seam — planner and normaliser, never a caller (BOOT3).

Command Room used to talk to ONE scheduler: the old desktop app's scheduled-task
tools, five-field cron in MACHINE-local time, one registry per box. The merged
Claude app has a different one: `mcp__claude-code-remote__*` triggers, cron in
UTC with an hourly minimum, one registry per ACCOUNT, and a fire that runs in a
fresh cloud session. Un-merged seats still run the old one. This module is the
seam between the two. The old scheduler's tool ids are NEVER spelled here —
`legacy_tools.LEGACY_TOOL_MAP` is their one home and this module inverts it
(guard G67).

Two rules shape everything here:

1. **Nothing in this file calls a tool.** Python cannot invoke an MCP tool, and
   the DOORS1 fence (`tests/run_schedview1_test.py`, `tests/run_doors1_test.py`)
   makes that explicit: nothing under `shared/scripts/` may reach a live
   scheduled task. So the module produces `CallPlan` objects that a SKILL
   executes, and `normalize`s raw readback into the task-record dict every
   reader already consumes. The MERGED app's tool names appear here as STRING
   CONSTANTS, which is what GUARD1's widened token list allows; the OLD app's
   are not spelled here at all — every one of them is derived from
   `legacy_tools.LEGACY_TOOL_MAP`, so there is exactly one home for them.
2. **Dual backend** (gap analysis §0.23). The backend is chosen from the tool
   list the model can actually see, never from an environment marker — the same
   lesson `task_watchdog.detect_registry_vantage` already teaches. Where the
   legacy shape is visible, behaviour is byte-for-byte today's.

Registration is GATED OFF in the shipped config (`shared/config/schedule_backend.json`,
gap analysis §0.9): nothing registers on any seat until the v3 bootloader and the
workspace access layer ship on the same cut. While the gate is blocked, planning a
CREATE or a cron CHANGE returns a `Refusal` carrying one customer sentence; listing,
verifying, firing an existing trigger and deleting Command Room's own probes stay
live.

3.10-safe by contract: this module may be read inside the sandbox VM (Python
3.10.12), so no `datetime.UTC`, no `StrEnum`, no `tomllib`, no `typing.Self`.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol, Tuple, Union
from zoneinfo import ZoneInfo

try:  # pragma: no cover - exercised by the by-file readers, not a branch of its own
    from tool_discovery import DiscoveryResult, ToolDescriptor
except ImportError:  # pragma: no cover - direct-path import when run as a script
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tool_discovery import DiscoveryResult, ToolDescriptor  # type: ignore


def _schedule_config():
    """`schedule_config`, imported LAZILY and on purpose.

    COPY1's `schedule_config.scheduler_availability` calls into this module, so a
    module-level import here would close the circle and break both files on the
    merged tree. Every use of `parse_cron` / `task_display_name` is inside a
    function anyway.
    """
    import schedule_config  # noqa: WPS433 - see the docstring

    return schedule_config

# GUARD1's `legacy_tools.py` is the ONE legal home of the old tool strings, and
# it is keyed the other way round: old tool id -> (seam module, seam verb). The
# legacy backend needs the inverse, so it DERIVES it — the old app's tool ids
# are never spelled in this file, in code, in a comment or in a docstring, which
# is what guard G67 asks of every module but the shim. Two copies of a tool name
# is also how one of them goes stale.
_LEGACY_VERB_BY_PLAN = {
    "plan_list": "list",
    "plan_create": "create",
    "plan_update": "update",
    "plan_delete": "delete",
    "plan_fire": "fire",
}


def _legacy_shim():
    """`legacy_tools`, imported by path if the plain import cannot see it.

    The shim ships beside this file. A caller that put only its own directory
    on `sys.path` still gets it, because the alternative — a literal fallback
    copy of the old tool ids — is the second home this module exists to avoid.
    """
    try:
        import legacy_tools  # noqa: WPS433 - see the docstring
    except ImportError:  # pragma: no cover - direct-path import as a script
        import sys as _sys

        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        import legacy_tools  # type: ignore # noqa: WPS433
    return legacy_tools


def _derive_legacy_tool_map() -> dict:
    shim = _legacy_shim()
    out = {verb: None for verb in _LEGACY_VERB_BY_PLAN.values()}
    for tool_id, (seam, operation) in shim.LEGACY_TOOL_MAP.items():
        if seam != shim.SEAM_SCHEDULE or tool_id.endswith("__"):
            continue  # family prefixes are not tool ids
        verb = _LEGACY_VERB_BY_PLAN.get(operation)
        if verb and out.get(verb) is None:
            out[verb] = tool_id
    # The legacy API never had a delete; the shim lists one for recognition
    # only, and planning one has always been a refusal.
    out["delete"] = None
    return out


def _derive_legacy_prefix() -> str:
    """The old scheduler's family prefix, read off the shim's own bare key."""
    shim = _legacy_shim()
    for tool_id, (seam, _operation) in shim.LEGACY_TOOL_MAP.items():
        if seam == shim.SEAM_SCHEDULE and tool_id.endswith("__"):
            return tool_id
    raise RuntimeError(
        "legacy_tools names no family prefix for the old scheduler — the shim "
        "is the only home for that string and this module will not invent one"
    )


LEGACY_TOOL_MAP = _derive_legacy_tool_map()


# ---------------------------------------------------------------------------
# Tool names. The MERGED app's are string constants here (docstring rule 1);
# the OLD app's are derived from the shim and never spelled.
# ---------------------------------------------------------------------------

LEGACY_TOOL_PREFIX = _derive_legacy_prefix()
CLOUD_TOOL_PREFIX = "mcp__claude-code-remote__"

CLOUD_TOOL_MAP = {
    "list": CLOUD_TOOL_PREFIX + "list_triggers",
    "create": CLOUD_TOOL_PREFIX + "create_trigger",
    "update": CLOUD_TOOL_PREFIX + "update_trigger",
    "delete": CLOUD_TOOL_PREFIX + "delete_trigger",
    "fire": CLOUD_TOOL_PREFIX + "fire_trigger",
}


# ---------------------------------------------------------------------------
# The sentences (SPEC_NIGHTM1_LANES §7.3 — verbatim; COPY1 pins them too, D-8)
# ---------------------------------------------------------------------------

GATE_LINE = (
    "Scheduled chats can't be set up in this version of the Claude app yet. "
    "Everything still works when you ask: say `morning briefing`, `inbox triage`, "
    "`end of day`, `staff meeting`, `weekly recap` on Fridays, and `run maintenance` "
    "once a day. Command Room will tell you when schedules are back."
)

NO_SCHEDULER_LINE = (
    "I can't see a scheduler from this chat, so nothing can be scheduled from here. "
    "Everything still works when you ask: say `morning briefing`, `inbox triage`, "
    "`end of day`, `staff meeting`, `weekly recap` on Fridays, and `run maintenance` "
    "once a day."
)

CHANGE_REFUSED_LINE = (
    "Your schedule can't be changed in this version of the Claude app yet — each chat "
    "still runs whenever you ask for it by name."
)

ROW_NOT_RUNNING = "not running in this version of the Claude app"

FOLDER_WAITING_LINE = (
    "This chat needs a one-time OK on your computer: a card is open there for about "
    "two hours; press Allow once and every later run will find your workspace."
)

RUNTIME_MISSING_LINE = (
    "Command Room's runtime is not installed in your workspace yet, so this chat did "
    "nothing. Open a new chat and say `what's new in command room`."
)

def runtime_matches_plugin(discover, expect):
    """Does the runtime installed in the workspace belong to the plugin THIS
    FIRE resolved? Returns `(keep_going, reason)`.

    The comparison is against the resolved plugin (`workspace_access.expect`),
    never against the registered prompt's `plugin-version` stamp. The stamp is
    diagnostic and is deliberately never rewritten on a version-only bump
    (`schedule_refresh.prompts_equivalent` normalises it out, BRIDGESIL1 §0.2),
    so comparing against it meant the first patch release after registration
    told every fire of every chat "runtime not installed" and stopped it — and
    `what's new in command room` could not clear it, because that flow installs
    the runtime and never rewrites a registered prompt (merged-tree review H-1).

    `expect` unavailable is NOT a reason to stop: discovery already found and
    probed the runtime, and the smoke helper proves it works. A fire stopped by
    a self-check the customer cannot clear is the failure this guards against.
    """
    discover = discover or {}
    if not discover.get("runtime_present"):
        return False, "runtime_missing"
    if not isinstance(expect, dict):
        return True, "expect_unavailable"
    wanted = expect.get("manifest_sha")
    if not wanted:
        return True, "expect_unavailable"
    found = discover.get("manifest_sha")
    if not found:
        return False, "manifest_unknown"
    if found != wanted:
        return False, "manifest_mismatch"
    return True, "ok"


# The five ABORT paragraphs the bootloader template carries VERBATIM (D-4: COPY1
# supplies the wording, BOOT3 pastes it into the one file it owns). `<TASK_ID>` is
# substituted at registration.
ABORT_PLUGIN_NOT_FOUND = (
    "⚠️ Command Room's scheduled chat `<TASK_ID>` could not find the Command "
    "Room plugin in this run, so it did nothing. Open a new chat and say `set up command "
    "room schedules`; this chat will work next time once the plugin is reachable."
)

ABORT_ORCHESTRATOR_NOT_FOUND = (
    "⚠️ Command Room's scheduled chat `<TASK_ID>` found the plugin but not its "
    "instructions for this chat — usually an update still settling. Open a new chat, say "
    "`what's new in command room`, then `set up command room schedules`."
)

ABORT_WORKSPACE_NOT_FOUND = (
    "⚠️ Command Room's scheduled chat `<TASK_ID>` could not reach your workspace "
    "folder from this run, so it did nothing. Open a new chat with your Command Room folder "
    "attached and say `set up command room schedules` to re-bind."
)

ABORT_ENV_UNKNOWN = (
    "⚠️ Command Room's scheduled chat `<TASK_ID>` could not tell which environment "
    "it is running in, so it did nothing. Open a new chat and say `health check`."
)

#: Every ABORT paragraph the bootloader template must carry, keyed by the name the
#: template's comment uses. `tests/run_boot3_test.py` walks this map against the file.
ABORT_TEXTS = {
    "ABORT_PLUGIN_NOT_FOUND": ABORT_PLUGIN_NOT_FOUND,
    "ABORT_ORCHESTRATOR_NOT_FOUND": ABORT_ORCHESTRATOR_NOT_FOUND,
    "ABORT_WORKSPACE_NOT_FOUND": ABORT_WORKSPACE_NOT_FOUND,
    "ABORT_ENV_UNKNOWN": ABORT_ENV_UNKNOWN,
}


# ---------------------------------------------------------------------------
# The three shapes (gap analysis §5.1)
# ---------------------------------------------------------------------------


@dataclass
class TaskRecord:
    """One scheduled task, in the dict shape every reader already consumes.

    `taskId`, `enabled`, `lastRunAt`, `cronExpression`, `prompt`, `description`
    and `nextRunAt` are the legacy keys — `task_watchdog.check_tasks`,
    `schedule_refresh.prompt_body_drift`, `schedule_proposals` and the skills'
    readback steps all key off them, so the cloud backend normalises INTO this
    shape rather than asking every reader to learn a second one.
    `cronExpression` is ALWAYS workspace-local, even on the cloud backend, where
    the registered cron is UTC and the local value is recovered with the offset
    stored at registration.

    The lower block is what the cloud backend knows in addition; on the legacy
    backend it is the conservative empty answer.
    """

    taskId: str
    enabled: Optional[bool] = None
    lastRunAt: Optional[str] = None
    cronExpression: Optional[str] = None
    prompt: Optional[str] = None
    description: Optional[str] = None
    nextRunAt: Optional[str] = None
    backend: str = "legacy"
    trigger_id: Optional[str] = None
    name: Optional[str] = None
    folders: list = field(default_factory=list)
    cron_utc: Optional[str] = None
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        """The legacy five-plus-two keys, for a caller that wants the plain dict.

        Readers written before the seam take a dict; handing them this keeps the
        normaliser's output drop-in.
        """
        return {
            "taskId": self.taskId,
            "enabled": self.enabled,
            "lastRunAt": self.lastRunAt,
            "cronExpression": self.cronExpression,
            "prompt": self.prompt,
            "description": self.description,
            "nextRunAt": self.nextRunAt,
        }


@dataclass
class CallPlan:
    """One tool call for a SKILL to execute, plus the receipts it then owes.

    `after` is a list of `(helper_name, kwargs)` pairs — the receipt writes the
    site is obliged to make (`log_schedule_config_change` for an enabled change,
    `log_schedule_refreshed` for a cron or prompt refresh). Carrying them on the
    plan is what makes G33's proximity rule hold mechanically instead of by
    prose discipline.
    """

    tool: str
    args: dict
    task_id: Optional[str] = None
    after: list = field(default_factory=list)
    note: str = ""


@dataclass
class Refusal:
    """No call. One customer sentence, and a machine-readable reason code."""

    line: str
    reason_code: str
    task_id: Optional[str] = None


#: What a planner returns: either a plan to execute, or a sentence to say.
Planned = Union[CallPlan, Refusal]


@dataclass
class BackendDiscovery(DiscoveryResult):
    """`DiscoveryResult` plus the one fact backend selection adds.

    `legacy_visible` is True when BOTH schedulers are on the tool list — the
    merged app wins, but the seat is mid-migration and the skills say so.
    Subclassing rather than widening `DiscoveryResult` keeps `tool_discovery.py`
    (DISC1's file) untouched.
    """

    legacy_visible: bool = False
    backend_id: Optional[str] = None


class ScheduleBackend(Protocol):
    """What a backend must be able to do. Neither implementation calls a tool."""

    id: str
    tool_prefix: str

    def plan_list(self) -> CallPlan:
        ...

    def normalize(
        self,
        raw_records: Iterable[Any],
        *,
        trigger_map: Optional[dict] = None,
        workspace_basename: str = "",
        tz_name: str = "",
    ) -> list:
        ...

    def plan_create(self, task_id: str, **kwargs: Any) -> Planned:
        ...

    def plan_update(self, task_id: str, **kwargs: Any) -> Planned:
        ...

    def plan_delete(self, task_id: str) -> Planned:
        ...

    def plan_fire(self, task_id: str, *, reason: str = "") -> Planned:
        ...

    def verify(self, records: Iterable[Any], **kwargs: Any) -> dict:
        ...

    def registration_gate(self) -> dict:
        ...


# ---------------------------------------------------------------------------
# The registration gate (gap analysis §0.9, §6; spec §6 item 1)
# ---------------------------------------------------------------------------

CONFIG_REL = "shared/config/schedule_backend.json"


def _config_path() -> Path:
    """Where the shipped gate config lives, relative to this file.

    `shared/scripts/schedule_backend.py` → `shared/config/schedule_backend.json`.
    `CR_SCHEDULE_BACKEND_CONFIG` overrides it for tests ONLY — a temp copy with
    the gate flipped is how the open-gate path is exercised without ever shipping
    an open gate.
    """
    override = os.environ.get("CR_SCHEDULE_BACKEND_CONFIG")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "config" / "schedule_backend.json"


def load_backend_config(path: Optional[Union[str, Path]] = None) -> dict:
    """Read the shipped config. A missing or unreadable file reads as BLOCKED.

    Fail-closed on purpose: a registration that happens because a config file was
    deleted is exactly the failure the gate exists to prevent.
    """
    target = Path(path) if path is not None else _config_path()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def registration_gate(
    backend_id: str = "cloud",
    *,
    path: Optional[Union[str, Path]] = None,
) -> dict:
    """May this backend register a NEW scheduled chat right now?

    Returns `{"allowed", "line", "reason_code", "blocked_since", "reason",
    "unblock_evidence"}`. The legacy backend is always allowed — the fleet on
    desktop Cowork keeps working exactly as it does today. The cloud backend is
    blocked by the shipped config until the v3 bootloader and the workspace
    access layer are on one cut (gap analysis §0.9).
    """
    if backend_id == "legacy":
        return {
            "allowed": True,
            "line": "",
            "reason_code": None,
            "blocked_since": None,
            "reason": "",
            "unblock_evidence": "",
        }

    cfg = load_backend_config(path)
    section = cfg.get(backend_id)
    if not isinstance(section, dict):
        section = {}
    blocked = section.get("registration", "blocked") != "allowed"
    return {
        "allowed": not blocked,
        "line": GATE_LINE if blocked else "",
        "reason_code": "registration_blocked" if blocked else None,
        "blocked_since": section.get("blocked_since"),
        "reason": section.get("reason", ""),
        "unblock_evidence": section.get("unblock_evidence", ""),
    }


# ---------------------------------------------------------------------------
# UTC and DST (gap analysis §5.2; spec §6 item 4)
# ---------------------------------------------------------------------------
#
# The CANONICAL cron stays workspace-local: `DEFAULT_SCHEDULES`, the customer's
# overrides, `SHIPPED_CRON_HISTORY` and every `cron_to_english` label carry what
# the customer MEANS. The UTC string is a derived registration artifact, stored
# beside the trigger id. These two functions are the only place the derivation
# happens, and they take the timezone as an ARGUMENT — never the host clock. The
# container's clock matched the workspace's by coincidence on the seat this was
# written on, which is exactly the "agreement is not correctness" trap
# `shared/RELIABILITY.md` names.

_FULL_DOM = frozenset(range(1, 32))
_FULL_DOW = frozenset(range(0, 7))

CRON_CROSS_PRODUCT_REFUSAL = (
    "That time can't be written as a single schedule in this timezone — set it as "
    "two chats instead."
)
CRON_DOM_MIDNIGHT_REFUSAL = (
    "That day-of-the-month time lands on the previous day once it's converted, "
    "which a monthly schedule can't express — pick a time earlier in the day."
)
CRON_SPLIT_REFUSAL = (
    "That time crosses midnight on some days once it's converted, so it can't run "
    "as one chat — set it as two chats."
)
CRON_UNPARSEABLE_REFUSAL = "I couldn't read that schedule, so nothing was changed."
CRON_MIN_GAP_REFUSAL = (
    "Scheduled chats have to be at least an hour apart in this version of the "
    "Claude app — space them out and I'll set them up."
)

#: The merged app's scheduler will not take fires closer together than this.
CLOUD_MIN_GAP_MINUTES = 60


def _render_field(values, lo: int, hi: int) -> str:
    """Render a cron field from a set of ints: `*`, a range, or a comma list.

    A contiguous run of three or more collapses to `a-b` — that is how the
    shipped defaults are spelled (`1-5`, `2-6`), and a customer who opens the
    Tasks UI should see what they set, not `1,2,3,4,5`.
    """
    vals = sorted(set(values))
    if not vals:
        return "*"
    if set(vals) == set(range(lo, hi + 1)):
        return "*"
    runs = []
    start = prev = vals[0]
    for v in vals[1:]:
        if v == prev + 1:
            prev = v
            continue
        runs.append((start, prev))
        start = prev = v
    runs.append((start, prev))
    parts = []
    for a, b in runs:
        if b - a >= 2:
            parts.append(f"{a}-{b}")
        else:
            parts.extend(str(x) for x in range(a, b + 1))
    return ",".join(parts)


def _shift_pairs(pairs, minutes_delta: int):
    """Move every (hour, minute) fire by `minutes_delta`; report the day shift.

    Returns `[(hour, minute, day_shift), …]` where `day_shift ∈ {-1, 0, +1}`.
    """
    out = []
    for hour, minute in pairs:
        total = hour * 60 + minute + minutes_delta
        shift, rem = divmod(total, 24 * 60)
        out.append((rem // 60, rem % 60, shift))
    return out


def _as_cross_product(shifted):
    """Turn `[(hour, minute, …)]` back into (minute_set, hour_set) — or None.

    A cron field pair is a CROSS PRODUCT: `30,45 6,12` fires four times. Whole-
    hour offsets preserve that product, but a zone at :30 or :45 can turn four
    fires into a set of pairs no single cron expresses. Rather than silently
    registering a schedule that fires at times nobody asked for, the caller
    refuses when this returns None.
    """
    pairs = {(h, m) for h, m, *_ in shifted}
    hours = {h for h, _ in pairs}
    minutes = {m for _, m in pairs}
    if len(pairs) != len(hours) * len(minutes):
        return None
    return minutes, hours


def _convert_cron(
    cron: str,
    tz_name: str,
    *,
    on_date,
    to_utc: bool,
    utc_offset_minutes: Optional[int] = None,
    task_id: Optional[str] = None,
):
    """The shared core of both conversions. Returns `list[str]` or a `Refusal`."""
    sc = _schedule_config()
    try:
        minute_set, hour_set, dom_set, month_set, dow_set = sc.parse_cron(cron)
    except Exception:  # CronParseError and anything else a malformed string raises
        return Refusal(
            line=CRON_UNPARSEABLE_REFUSAL, reason_code="cron_unparseable", task_id=task_id
        )

    if utc_offset_minutes is None:
        # The offset IN EFFECT on `on_date` in the workspace's zone — read from
        # the zone database, never from `datetime.now().astimezone()`.
        try:
            zone = ZoneInfo(tz_name)
        except Exception:
            return Refusal(
                line=CRON_UNPARSEABLE_REFUSAL,
                reason_code="unknown_timezone",
                task_id=task_id,
            )
        probe = _dt.datetime(on_date.year, on_date.month, on_date.day, 12, 0, tzinfo=zone)
        offset = probe.utcoffset() or _dt.timedelta(0)
        utc_offset_minutes = int(offset.total_seconds() // 60)

    delta = -utc_offset_minutes if to_utc else utc_offset_minutes
    shifted = _shift_pairs(
        [(h, m) for h in sorted(hour_set) for m in sorted(minute_set)], delta
    )

    dom_restricted = set(dom_set) != set(_FULL_DOM)
    dow_restricted = set(dow_set) != set(_FULL_DOW)
    shifts = {s for _, _, s in shifted}

    if shifts != {0}:
        if dom_restricted:
            return Refusal(
                line=CRON_DOM_MIDNIGHT_REFUSAL,
                reason_code="dom_crosses_midnight",
                task_id=task_id,
            )
        if dow_restricted and len(shifts) > 1:
            # Option B (gap analysis §5.2): refuse, one sentence. No shipped
            # default splits; fanning out to N triggers would multiply every
            # update and verify path for a case nobody has asked for.
            return Refusal(
                line=CRON_SPLIT_REFUSAL, reason_code="cron_splits_utc_day", task_id=task_id
            )

    product = _as_cross_product(shifted)
    if product is None:
        return Refusal(
            line=CRON_CROSS_PRODUCT_REFUSAL,
            reason_code="cron_not_expressible",
            task_id=task_id,
        )
    minutes, hours = product

    if dow_restricted and shifts != {0}:
        shift = next(iter(shifts))
        dow_out = {(d + shift) % 7 for d in dow_set}
    else:
        dow_out = set(dow_set)

    return [
        " ".join(
            [
                _render_field(minutes, 0, 59),
                _render_field(hours, 0, 23),
                _render_field(dom_set, 1, 31),
                _render_field(month_set, 1, 12),
                _render_field(dow_out, 0, 6),
            ]
        )
    ]


def local_cron_to_utc(
    cron_local: str,
    tz_name: str,
    *,
    on_date,
    task_id: Optional[str] = None,
) -> Union[list, Refusal]:
    """Workspace-local cron → the UTC cron the merged app registers.

    `on_date` fixes which side of a DST transition the offset is read on: the
    same 7 AM brief is `0 14 * * 1-5` in summer and `0 15 * * 1-5` in winter, and
    nothing but the date decides which. Returns a one-element list (the seam
    never fans out — see the split refusal) or a `Refusal` with one sentence.
    """
    return _convert_cron(cron_local, tz_name, on_date=on_date, to_utc=True, task_id=task_id)


def utc_cron_to_local(
    cron_utc: str,
    tz_name: str,
    *,
    utc_offset_minutes: int,
    task_id: Optional[str] = None,
) -> Union[str, Refusal]:
    """The inverse, using the offset STORED at registration.

    Used by `normalize`, so every reader keeps seeing the local cron it has
    always read. A stored offset that no longer matches the zone's current
    offset is exactly the DST-drift detector the `schedule-realign` job reads.
    """
    result = _convert_cron(
        cron_utc,
        tz_name,
        on_date=_dt.date(2026, 1, 1),  # unused: the offset is supplied
        to_utc=False,
        utc_offset_minutes=utc_offset_minutes,
        task_id=task_id,
    )
    if isinstance(result, Refusal):
        return result
    return result[0]


def current_utc_offset_minutes(tz_name: str, *, on_date) -> Optional[int]:
    """The zone's UTC offset on `on_date`, in minutes. None when unknown."""
    try:
        zone = ZoneInfo(tz_name)
    except Exception:
        return None
    probe = _dt.datetime(on_date.year, on_date.month, on_date.day, 12, 0, tzinfo=zone)
    offset = probe.utcoffset() or _dt.timedelta(0)
    return int(offset.total_seconds() // 60)


def min_gap_minutes(cron: str) -> Optional[int]:
    """The tightest gap, in minutes, between two fires of this cron in one day.

    The merged app will not take fires closer than an hour apart, so the seam
    checks locally and refuses with a plain sentence rather than letting the
    call fail somewhere the customer cannot see. `None` when the cron cannot be
    read, or when it fires only once (no gap to measure).
    """
    sc = _schedule_config()
    try:
        minute_set, hour_set, _dom, _month, _dow = sc.parse_cron(cron)
    except Exception:
        return None
    times = sorted({h * 60 + m for h in hour_set for m in minute_set})
    if len(times) < 2:
        return None
    gaps = [b - a for a, b in zip(times, times[1:])]
    gaps.append(times[0] + 24 * 60 - times[-1])
    return min(gaps)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class LegacyBackend:
    """Desktop Cowork, v5.29.0 fleet. Byte-for-byte today's behaviour.

    `normalize` is the identity: the legacy listing already hands back the five
    keys every reader wants, and `cronExpression` is already machine-local.
    Plans emit the old tool names with the old argument names.
    """

    id = "legacy"
    tool_prefix = LEGACY_TOOL_PREFIX

    def plan_list(self) -> CallPlan:
        return CallPlan(
            tool=LEGACY_TOOL_MAP["list"],
            args={},
            note="legacy listing — the records already carry the reader keys",
        )

    def normalize(
        self,
        raw_records: Iterable[Any],
        *,
        trigger_map: Optional[dict] = None,
        workspace_basename: str = "",
        tz_name: str = "",
    ) -> list:
        """Identity. The legacy records ARE the reader shape."""
        return list(raw_records)

    def registration_gate(self) -> dict:
        return registration_gate("legacy")

    def plan_create(
        self,
        task_id: str,
        *,
        prompt: str,
        cron_local: Optional[str] = None,
        description: str = "",
        notify: bool = True,
        fire_at: Optional[str] = None,
        recurrence: Optional[str] = None,
        **_ignored: Any,
    ) -> Planned:
        """Today's arguments, today's names. Nothing about this path changes."""
        args: dict = {
            "taskId": task_id,
            "prompt": prompt,
            "description": description,
            "notifyOnCompletion": bool(notify),
        }
        if cron_local:
            args["cronExpression"] = cron_local
        if fire_at:
            args["fireAt"] = fire_at
        if recurrence:
            args["recurrence"] = recurrence
        return CallPlan(
            tool=LEGACY_TOOL_MAP["create"],
            args=args,
            task_id=task_id,
            after=[
                (
                    "event:schedule_created",
                    # `cron_utc` is None on this backend on purpose: the legacy
                    # scheduler evaluates cron in the MACHINE's own time, so
                    # there is no world-time value to record and claiming one
                    # would be a number no reader could trust.
                    {"taskId": task_id, "cron": cron_local, "cron_utc": None,
                     "backend": self.id},
                )
            ],
            note="legacy registration — byte-for-byte today's behaviour",
        )

    def plan_update(
        self,
        task_id: str,
        *,
        prompt: Optional[str] = None,
        cron_local: Optional[str] = None,
        enabled: Optional[bool] = None,
        **_ignored: Any,
    ) -> Planned:
        args: dict = {"taskId": task_id}
        after = []
        if cron_local is not None:
            args["cronExpression"] = cron_local
            after.append(
                (
                    "schedule_refresh.log_schedule_refreshed",
                    {"task_id": task_id, "field": "cron", "to_value": cron_local},
                )
            )
        if enabled is not None:
            args["enabled"] = bool(enabled)
            after.append(
                (
                    "schedule_config.log_schedule_config_change",
                    {"task_id": task_id, "enabled": bool(enabled)},
                )
            )
        if prompt is not None:
            args["prompt"] = prompt
            after.append(
                (
                    "schedule_refresh.log_schedule_refreshed",
                    {"task_id": task_id, "field": "prompt"},
                )
            )
        return CallPlan(
            tool=LEGACY_TOOL_MAP["update"],
            args=args,
            task_id=task_id,
            after=after,
            note="legacy update — byte-for-byte today's behaviour",
        )

    def plan_delete(self, task_id: str, **_ignored: Any) -> Planned:
        """There is no delete on the legacy API. Disable is the only removal."""
        return Refusal(
            line=DELETE_REFUSED_LINE, reason_code="delete_refused", task_id=task_id
        )

    def plan_fire(self, task_id: str, *, reason: str = "", **_ignored: Any) -> Planned:
        return CallPlan(
            tool=LEGACY_TOOL_MAP["fire"],
            args={"taskId": task_id},
            task_id=task_id,
            note=reason or "manual re-run",
        )

    def verify(self, records: Iterable[Any], *, workspace_root=None,
               plugin_version: str = "", **_ignored: Any) -> dict:
        """The legacy path has no trigger map and no UTC cron to drift."""
        rows = []
        for rec in records or []:
            row = rec.as_dict() if isinstance(rec, TaskRecord) else dict(rec)
            rows.append(
                {
                    "task_id": row.get("taskId"),
                    "trigger_id": None,
                    "enabled": row.get("enabled"),
                    "cron_local": row.get("cronExpression"),
                    "cron_utc": None,
                    "cron_drift": False,
                    "tz_drift": False,
                    "folders_bound": False,
                    "last_run": row.get("lastRunAt"),
                    "prompt_status": _prompt_status(
                        row.get("taskId"), row.get("prompt"), workspace_root,
                        plugin_version
                    ),
                }
            )
        return {"backend": self.id, "rows": rows}


# ---------------------------------------------------------------------------
# Trigger identity (gap analysis §5.3; spec §6 item 6)
# ---------------------------------------------------------------------------

#: The one machine-parseable line both prompt templates carry. It is the
#: AUTHORITATIVE identity: a name can be edited in the Tasks UI and a stored map
#: can be lost with a workspace file, but the prompt is what the fire itself runs.
HEADER_LINE_FORMAT = "cr-task: {task_id} · cr-workspace: {basename} · plugin-version: {version}"

import re as _re  # noqa: E402 - kept beside the pattern it serves

HEADER_LINE_RE = _re.compile(
    r"cr-task:\s*(?P<task_id>[A-Za-z0-9_\-]+)\s*·\s*"
    r"cr-workspace:\s*(?P<basename>[^·\n]+?)\s*·\s*"
    r"plugin-version:\s*(?P<version>[^\s·\n]+)"
)

#: Command Room's own one-shots and probes — the ONLY triggers the seam will
#: plan a delete for (gap analysis §0.20). Anything a customer can see is
#: disabled, never deleted: disable keeps the audit trail and keeps `brain_undo`'s
#: reversers working, and deleting a customer-visible chat is the same posture
#: violation as registering one without asking.
CR_ONE_SHOT_TASK_IDS = frozenset(
    {"cr-day1-checkin", "cr-week1-followup", "cr-m1-backfill"}
)
CR_PROBE_PREFIX = "cr-probe-"

DELETE_REFUSED_LINE = (
    "I don't delete a scheduled chat you can see — I turn it off instead, so it "
    "can be turned back on and the history stays."
)


def trigger_name(task_id: str, workspace_basename: str) -> str:
    """`<Display name> - Command Room (<workspace basename>)`.

    Reuses the description format the registration skill's display-name check
    already verifies. The basename suffix disambiguates an account with more
    than one Command Room workspace — a case the old machine-level design never
    had, because its registry was per box.
    """
    sc = _schedule_config()
    return f"{sc.task_display_name(task_id)} - Command Room ({workspace_basename})"


def header_line(task_id: str, workspace_basename: str, plugin_version: str) -> str:
    return HEADER_LINE_FORMAT.format(
        task_id=task_id, basename=workspace_basename, version=plugin_version
    )


def parse_header_line(prompt: Optional[str]) -> Optional[dict]:
    """Read the identity line out of a prompt body. None when it is not there."""
    if not prompt:
        return None
    match = HEADER_LINE_RE.search(prompt)
    if not match:
        return None
    return {
        "task_id": match.group("task_id"),
        "basename": match.group("basename").strip(),
        "plugin_version": match.group("version"),
    }


def is_deletable(task_id: Optional[str]) -> bool:
    """Only Command Room's own one-shots and probes may be deleted."""
    if not task_id:
        return False
    return task_id in CR_ONE_SHOT_TASK_IDS or task_id.startswith(CR_PROBE_PREFIX)


def record_trigger_map(workspace_root, task_id: str, row: dict,
                       *, merge: bool = False) -> dict:
    """Write one `triggers` row into `_hq/workspace_config.json`.

    The stored map is the PRIMARY identity lookup and works offline — it is read
    before the prompt header and before the name prefix. It sits beside
    `registered_taskIds`, which every existing reader keeps using unchanged.

    `merge=True` updates the fields given and leaves the rest of the row alone,
    which is what a cron change and the daylight-saving re-projection want: they
    move two or three values and must not drop the folders or the trigger id a
    registration wrote.
    """
    from atomic_write import atomic_write_json  # lazy: keeps VM import cost down

    path = Path(workspace_root) / "_hq" / "workspace_config.json"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            config = json.load(fh)
        if not isinstance(config, dict):
            config = {}
    except (OSError, ValueError):
        config = {}
    triggers = config.get("triggers")
    if not isinstance(triggers, dict):
        triggers = {}
    if merge and isinstance(triggers.get(task_id), dict):
        merged = dict(triggers[task_id])
        merged.update(row)
        triggers[task_id] = merged
    else:
        triggers[task_id] = dict(row)
    config["triggers"] = triggers
    atomic_write_json(path, config)
    return triggers


#: The env key the access layer forwards with this seat's folder path on the
#: customer's computer (`workspace_access.FORWARD_ENV_KEYS`).
DEVICE_WORKSPACE_ENV = "CR_DEVICE_WORKSPACE"


def record_trigger_row(workspace_root, task_id: str, row: dict,
                       *, merge: bool = False) -> dict:
    """`record_trigger_map` as the WRITE DOOR runs it (SCHEDREG1 MUST 4).

    The door fences every path-shaped argument to the workspace, so the one
    value in a trigger row that is a path on the customer's computer -
    `folders` - cannot travel as an argument. It travels as what it is: this
    seat's `CR_DEVICE_WORKSPACE`, which the container exports and the door
    forwards on the line. A row handed in without `folders` gets
    `[CR_DEVICE_WORKSPACE]` when that is set; everything else is
    `record_trigger_map`, unchanged.
    """
    row = dict(row or {})
    if "folders" not in row:
        device = str(os.environ.get(DEVICE_WORKSPACE_ENV, "")).strip()
        if device:
            row["folders"] = [device]
    return record_trigger_map(workspace_root, task_id, row, merge=merge)


#: The door form a registering SKILL renders for each `record_trigger_map`
#: entry in a plan's `after` list.
TRIGGER_ROW_WRITER = "schedule_backend:record_trigger_row"


def trigger_row_payload(after_kwargs: dict, *, trigger_id: Optional[str] = None,
                        workspace_root: str = "<WS>") -> dict:
    """The `plan run_writer --json` payload for one `record_trigger_map`
    after-entry: the row with the create's trigger id filled in and WITHOUT
    `folders` (the door fills it from this seat's forwarded folder)."""
    row = {k: v for k, v in dict(after_kwargs.get("row") or {}).items()
           if k != "folders"}
    if trigger_id is not None:
        row["trigger_id"] = trigger_id
    return {"name": TRIGGER_ROW_WRITER,
            "args": {"workspace_root": workspace_root,
                     "task_id": after_kwargs.get("task_id"),
                     "row": row, "merge": bool(after_kwargs.get("merge"))}}


# ---------------------------------------------------------------------------
# SCHEDFAST1 (ChaletteHQ/cr1#101) - ONE door write per created chat
# ---------------------------------------------------------------------------
#
# A create's `after` list has two entries - the trigger row and the
# `schedule_created` event - and on the merged seat each was its own door
# call (a device round trip, sometimes a permission card). `record_registration`
# lands both in one call: the row exactly as `record_trigger_row` writes it,
# then the event through the same gate `append_jsonl` uses. The `after` list
# itself is unchanged, so every reader of the plan still sees both entries;
# `registration_payload` is what folds them into the one call.

#: The event type the second `after` entry names.
SCHEDULE_CREATED_EVENT = "schedule_created"

#: The door form a registering SKILL renders ONCE per created chat.
REGISTRATION_WRITER = "schedule_backend:record_registration"


def record_registration(workspace_root, task_id: str, row: dict,
                        event: Optional[dict] = None, *,
                        merge: bool = False,
                        source_skill: str = "enable-command-room-schedules") -> dict:
    """The trigger row AND the `schedule_created` receipt, one door call.

    The row lands first, through `record_trigger_row` unchanged (the folder
    comes from this seat's forwarded `CR_DEVICE_WORKSPACE`, never an
    argument). The event is then appended through the canonical gate with
    the same shape the skill used to paste through `append_jsonl`:
    `{type: schedule_created, source_skill, data: <event>}`, seq and ts
    stamped by the writer. An `event` of None appends nothing (a cron change
    carries a row and no receipt). Returns `{row, event, event_written}`;
    a failed append never undoes the row - the registration happened, and
    `event_written: False` is the honest answer (RELIABILITY.md)."""
    written = record_trigger_row(workspace_root, task_id, row, merge=merge)
    out = {"row": written, "event": None, "event_written": False}
    if not isinstance(event, dict) or not event:
        return out
    data = dict(event)
    data.setdefault("taskId", task_id)
    source = str(source_skill or "").strip() or "enable-command-room-schedules"
    record = {"type": SCHEDULE_CREATED_EVENT, "source_skill": source,
              "data": data}
    try:
        import sys as _sys
        _here = str(Path(__file__).resolve().parent)
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        from event_gate import append_event

        events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        stamped = append_event(events_path, record, holder=f"schedule:{source}")
        out["event"] = stamped[0] if stamped else record
        out["event_written"] = True
    except Exception:  # noqa: BLE001 - the receipt never blocks the row
        out["event"] = record
    return out


def registration_payload(after, *, trigger_id: Optional[str] = None,
                         workspace_root: str = "<WS>",
                         source_skill: str = "enable-command-room-schedules") -> dict:
    """The ONE `plan run_writer --json` payload for a create plan's whole
    `after` list: the `record_trigger_map` entry's row (via
    `trigger_row_payload`, so no `folders` and the trigger id filled) and the
    `event:schedule_created` entry's data with the same id filled, for
    `record_registration`. An `after` with no event entry (a cron change)
    yields `event: None`; one with no row entry raises, because a chat with
    no stored row is one no later read can name."""
    row_kwargs = None
    event = None
    for name, kwargs in list(after or []):
        if name == "schedule_backend.record_trigger_map" and row_kwargs is None:
            row_kwargs = dict(kwargs or {})
        elif name == f"event:{SCHEDULE_CREATED_EVENT}" and event is None:
            event = dict(kwargs or {})
    if row_kwargs is None:
        raise ValueError("after list carries no record_trigger_map entry")
    row_payload = trigger_row_payload(row_kwargs, trigger_id=trigger_id,
                                      workspace_root=workspace_root)
    if event is not None and trigger_id is not None:
        event["trigger_id"] = trigger_id
    return {"name": REGISTRATION_WRITER,
            "args": {**row_payload["args"], "event": event,
                     "source_skill": source_skill}}


def read_trigger_map(workspace_root) -> dict:
    """The stored `triggers` map, or `{}` when there is none."""
    path = Path(workspace_root) / "_hq" / "workspace_config.json"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError):
        return {}
    triggers = config.get("triggers") if isinstance(config, dict) else None
    return triggers if isinstance(triggers, dict) else {}


def _resolve_trigger_map(trigger_map: Optional[dict], workspace_root) -> dict:
    """The map a reader should use: the one handed in, else the stored one.

    Every reader in this module goes through here, so "where the triggers map
    lives" is one sentence in one place. A reader given neither gets `{}` and
    falls through to its other identity lookups, exactly as it always did.
    """
    if trigger_map is not None:
        return trigger_map
    if workspace_root is None:
        return {}
    return read_trigger_map(workspace_root)


def _prompt_status(task_id, prompt, workspace_root, plugin_version) -> str:
    """What `verify` should say about ONE registered prompt.

    `None` means the listing carried no prompt text at all — "unknown, cannot
    judge", never drift. Anything else is handed to the ONE refresh decision
    (`schedule_refresh.plan_prompt_refresh`), so `verify` reports exactly what
    a refresh would do — `current`, `rewrite`, `refuse` — rather than the flat
    "readable" it used to, which could never show a customer a drifted prompt.
    Without a workspace to compose against there is nothing to compare, and the
    honest answer is "unknown" again.
    """
    if not prompt:
        return "unknown"
    if not workspace_root:
        return "unknown"
    try:
        import schedule_refresh  # noqa: WPS433 - lazy: VM import cost

        return str(
            schedule_refresh.plan_prompt_refresh(
                task_id,
                registered_prompt=prompt,
                workspace_root=workspace_root,
                plugin_version=plugin_version,
            )["action"]
        )
    except Exception:  # pragma: no cover - a verify never raises on one row
        return "unknown"


def trigger_map_row(
    task_id: str,
    *,
    trigger_id: Optional[str],
    backend: str,
    name: str,
    cron_local: Optional[str],
    cron_utc: Optional[str],
    tz: str,
    utc_offset_minutes: Optional[int],
    folders: Optional[list] = None,
    registered_at: Optional[str] = None,
) -> dict:
    """One `triggers` row, in the shape every reader here expects (§5.3).

    Built in ONE place so the registering skill, the change flow and the
    daylight-saving job cannot each invent their own key names — a map whose
    rows disagree is a map `normalize` silently ignores.
    """
    return {
        "trigger_id": trigger_id,
        "backend": backend,
        "name": name,
        "cron_local": cron_local,
        "cron_utc": cron_utc,
        "tz": tz,
        "utc_offset_minutes": utc_offset_minutes,
        "folders": list(folders or []),
        "registered_at": registered_at,
    }


class CloudTriggersBackend:
    """The merged Claude app: account-level triggers, UTC cron, cloud fires.

    Every plan carries `requires_local_device=True` (the workspace is on the
    customer's computer, not in the cloud), `folders=[<absolute path>]` and
    `permission_mode="auto"` — a fire nobody attends stops dead at the first
    approval card under any other mode.
    """

    id = "cloud"
    tool_prefix = CLOUD_TOOL_PREFIX

    def __init__(self, *, config_path: Optional[Union[str, Path]] = None) -> None:
        self._config_path = config_path

    # -- reads -------------------------------------------------------------

    def plan_list(self) -> CallPlan:
        return CallPlan(
            tool=CLOUD_TOOL_MAP["list"],
            args={},
            note="account-level listing — normalize() before any reader sees it",
        )

    def registration_gate(self) -> dict:
        return registration_gate("cloud", path=self._config_path)

    # -- writes ------------------------------------------------------------

    def plan_create(
        self,
        task_id: str,
        *,
        prompt: str,
        cron_local: Optional[str] = None,
        tz_name: str = "",
        enabled: bool = True,
        notify: bool = True,
        workspace_basename: str = "",
        folders: Optional[list] = None,
        on_date=None,
        run_once_at: Optional[str] = None,
    ) -> Planned:
        gate = self.registration_gate()
        if not gate["allowed"]:
            return Refusal(
                line=gate["line"], reason_code=gate["reason_code"], task_id=task_id
            )

        args = {
            "name": trigger_name(task_id, workspace_basename),
            "prompt": prompt,
            "requires_local_device": True,
            "folders": list(folders or []),
            "permission_mode": "auto",
            "notifications": {"push": bool(notify), "email": False},
        }

        if run_once_at:
            # A one-shot (the old `recurrence: "once"` + `fireAt`) registers as
            # a single future instant, not a cron.
            args["run_once_at"] = run_once_at
            cron_utc = None
        else:
            gap = min_gap_minutes(cron_local or "")
            if gap is not None and gap < CLOUD_MIN_GAP_MINUTES:
                return Refusal(
                    line=CRON_MIN_GAP_REFUSAL,
                    reason_code="cron_below_min_gap",
                    task_id=task_id,
                )
            converted = local_cron_to_utc(
                cron_local or "",
                tz_name,
                on_date=on_date or _dt.date.today(),
                task_id=task_id,
            )
            if isinstance(converted, Refusal):
                return converted
            cron_utc = converted[0]
            args["cron_expression"] = cron_utc

        return CallPlan(
            tool=CLOUD_TOOL_MAP["create"],
            args=args,
            task_id=task_id,
            after=[
                # The `triggers` map is the PRIMARY identity lookup and the only
                # offline one: without this row `normalize` cannot say which
                # chat a trigger is, cannot convert its UTC cron back to the
                # customer's time, and the daylight-saving job has nothing to
                # iterate. The skill fills `trigger_id` from the create's return
                # and executes this call.
                (
                    "schedule_backend.record_trigger_map",
                    {
                        "task_id": task_id,
                        "row": trigger_map_row(
                            task_id,
                            trigger_id=None,  # filled from the create's return
                            backend=self.id,
                            name=args["name"],
                            cron_local=cron_local,
                            cron_utc=cron_utc,
                            tz=tz_name,
                            utc_offset_minutes=current_utc_offset_minutes(
                                tz_name, on_date=on_date or _dt.date.today()
                            ),
                            folders=args["folders"],
                        ),
                    },
                ),
                # `schedule_created` is an EVENT the registering skill appends
                # through the gate, not a helper call — so the `after` entry
                # names the event and carries its additive data. The three new
                # fields are additive: every existing reader is unchanged.
                (
                    "event:schedule_created",
                    {
                        "taskId": task_id,
                        "cron": cron_local,
                        "trigger_id": None,  # filled from the create's return
                        "cron_utc": cron_utc,
                        "backend": self.id,
                    },
                ),
            ],
            note=("register this chat, then record its trigger row and write "
                  "its schedule_created receipt"),
        )

    def plan_update(
        self,
        task_id: str,
        *,
        trigger_id: Optional[str] = None,
        prompt: Optional[str] = None,
        cron_local: Optional[str] = None,
        enabled: Optional[bool] = None,
        tz_name: str = "",
        on_date=None,
    ) -> Planned:
        if cron_local is not None:
            gate = self.registration_gate()
            if not gate["allowed"]:
                return Refusal(
                    line=gate["line"], reason_code=gate["reason_code"], task_id=task_id
                )

        args: dict = {"trigger_id": trigger_id}
        after = []
        cron_utc = None

        if cron_local is not None:
            gap = min_gap_minutes(cron_local)
            if gap is not None and gap < CLOUD_MIN_GAP_MINUTES:
                return Refusal(
                    line=CRON_MIN_GAP_REFUSAL,
                    reason_code="cron_below_min_gap",
                    task_id=task_id,
                )
            converted = local_cron_to_utc(
                cron_local, tz_name, on_date=on_date or _dt.date.today(), task_id=task_id
            )
            if isinstance(converted, Refusal):
                return converted
            cron_utc = converted[0]
            args["cron_expression"] = cron_utc
            after.append(
                (
                    "schedule_refresh.log_schedule_refreshed",
                    {"task_id": task_id, "field": "cron", "to_value": cron_local},
                )
            )
            # The stored row carries the offset the UTC cron was projected on.
            # A cron change that does not re-stamp it leaves every later read
            # converting back with the wrong offset, and leaves the
            # daylight-saving job re-projecting the same chat every night.
            after.append(
                (
                    "schedule_backend.record_trigger_map",
                    {
                        "task_id": task_id,
                        "row": {
                            "trigger_id": trigger_id,
                            "cron_local": cron_local,
                            "cron_utc": cron_utc,
                            "tz": tz_name,
                            "utc_offset_minutes": current_utc_offset_minutes(
                                tz_name, on_date=on_date or _dt.date.today()
                            ),
                        },
                        "merge": True,
                    },
                )
            )
        if enabled is not None:
            args["enabled"] = bool(enabled)
            after.append(
                (
                    "schedule_config.log_schedule_config_change",
                    {"task_id": task_id, "enabled": bool(enabled)},
                )
            )
        if prompt is not None:
            args["prompt"] = prompt
            after.append(
                (
                    "schedule_refresh.log_schedule_refreshed",
                    {"task_id": task_id, "field": "prompt"},
                )
            )

        return CallPlan(
            tool=CLOUD_TOOL_MAP["update"],
            args=args,
            task_id=task_id,
            after=after,
            note="update this trigger, then write the receipt the change owes",
        )

    def plan_delete(self, task_id: str, *, trigger_id: Optional[str] = None) -> Planned:
        if not is_deletable(task_id):
            return Refusal(
                line=DELETE_REFUSED_LINE, reason_code="delete_refused", task_id=task_id
            )
        return CallPlan(
            tool=CLOUD_TOOL_MAP["delete"],
            args={"trigger_id": trigger_id},
            task_id=task_id,
            note="one of Command Room's own one-shots or probes — safe to remove",
        )

    def plan_fire(
        self,
        task_id: str,
        *,
        trigger_id: Optional[str] = None,
        reason: str = "",
        now_iso: Optional[str] = None,
    ) -> Planned:
        """Fire an existing trigger by hand.

        The `text` is the first run-mode discriminator Command Room has ever
        had: the old runtime could not tell a hand-pressed run from a scheduled
        one, so "when uncertain → manual" was the only safe posture. A fire
        that carries this sentence is provably manual.
        """
        stamp = now_iso or _dt.datetime.now(_dt.timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        return CallPlan(
            tool=CLOUD_TOOL_MAP["fire"],
            args={
                "trigger_id": trigger_id,
                "text": f"Command Room manual re-run — {stamp}",
            },
            task_id=task_id,
            note=reason or "manual re-run",
        )

    # -- normalisation -----------------------------------------------------

    def normalize(
        self,
        raw_records: Iterable[Any],
        *,
        trigger_map: Optional[dict] = None,
        workspace_root=None,
        workspace_basename: str = "",
        tz_name: str = "",
    ) -> list:
        """Account-level trigger rows → the task-record dicts readers consume.

        Pass `workspace_root` and the stored `triggers` map is READ from it —
        a caller that has a workspace never has to know where the map lives, and
        the map's writer (`record_trigger_map`) and its readers cannot drift
        apart into two spellings of one file. An explicit `trigger_map` still
        wins, which is what the fixtures use.

        Identity is looked up three ways, in this order: the stored map (works
        offline), the prompt header line (authoritative when the listing carries
        the prompt), then the name prefix (last resort — a name can be edited).
        The UTC cron is converted BACK to workspace-local with the offset stored
        at registration, so the schedule-history classifier and the live-cron
        render keep reading local values exactly as they always have.

        `prompt` is `None` when the listing carries no prompt text. That is
        "unknown, cannot judge" — never drift. The readers learn the difference
        rather than reporting every task stale forever.
        """
        trigger_map = _resolve_trigger_map(trigger_map, workspace_root)
        by_trigger_id = {
            str(row.get("trigger_id")): task_id
            for task_id, row in trigger_map.items()
            if isinstance(row, dict) and row.get("trigger_id")
        }
        out = []
        for raw in raw_records or []:
            if not isinstance(raw, dict):
                continue
            trigger_id = raw.get("id") or raw.get("trigger_id")
            name = raw.get("name")
            prompt = raw.get("prompt")

            task_id = by_trigger_id.get(str(trigger_id))
            if not task_id:
                parsed = parse_header_line(prompt)
                if parsed:
                    task_id = parsed["task_id"]
            if not task_id:
                task_id = self._task_id_from_name(name)
            if not task_id:
                # Not a Command Room trigger. Foreign triggers are never listed
                # and never touched.
                continue

            stored = trigger_map.get(task_id) or {}
            offset = stored.get("utc_offset_minutes")
            zone = stored.get("tz") or tz_name
            cron_utc = raw.get("cron_expression")
            cron_local = None
            if cron_utc and offset is not None and zone:
                converted = utc_cron_to_local(
                    cron_utc, zone, utc_offset_minutes=int(offset), task_id=task_id
                )
                cron_local = converted if isinstance(converted, str) else None

            folders = []
            derived = raw.get("derived_state")
            if isinstance(derived, dict) and isinstance(derived.get("folders"), list):
                folders = list(derived["folders"])
            elif isinstance(raw.get("folders"), list):
                folders = list(raw["folders"])

            out.append(
                TaskRecord(
                    taskId=task_id,
                    enabled=raw.get("enabled"),
                    lastRunAt=_iso_or_none(raw.get("last_run")),
                    cronExpression=cron_local,
                    prompt=prompt if prompt else None,
                    description=name,
                    nextRunAt=_iso_or_none(raw.get("next_run_at")),
                    backend=self.id,
                    trigger_id=str(trigger_id) if trigger_id else None,
                    name=name,
                    folders=folders,
                    cron_utc=cron_utc,
                    raw=raw,
                )
            )
        return out

    @staticmethod
    def _task_id_from_name(name: Optional[str]) -> Optional[str]:
        """Last-resort identity: match the display name a CR trigger carries."""
        if not name or " - Command Room" not in name:
            return None
        sc = _schedule_config()
        display = name.split(" - Command Room", 1)[0].strip()
        for task_id, known in sc.DISPLAY_NAMES.items():
            if known == display:
                return task_id
        return None

    # -- verification ------------------------------------------------------

    def verify(
        self,
        records: Iterable[Any],
        *,
        trigger_map: Optional[dict] = None,
        workspace_root=None,
        schedule_config_rows: Optional[dict] = None,
        tz_name: str = "",
        plugin_version: str = "",
        on_date=None,
    ) -> dict:
        """What the registered triggers say versus what the workspace expects.

        `workspace_root` is where the stored `triggers` map is read from when no
        map is handed in, so a verifying surface needs one argument rather than
        a file path of its own.
        """
        trigger_map = _resolve_trigger_map(trigger_map, workspace_root)
        schedule_config_rows = schedule_config_rows or {}
        on_date = on_date or _dt.date.today()
        rows = []
        for rec in records or []:
            task_id = rec.taskId if isinstance(rec, TaskRecord) else rec.get("taskId")
            stored = trigger_map.get(task_id) or {}
            expected = (schedule_config_rows.get(task_id) or {}).get("cron")
            cron_local = (
                rec.cronExpression
                if isinstance(rec, TaskRecord)
                else rec.get("cronExpression")
            )
            prompt = rec.prompt if isinstance(rec, TaskRecord) else rec.get("prompt")
            zone = stored.get("tz") or tz_name
            stored_offset = stored.get("utc_offset_minutes")
            now_offset = current_utc_offset_minutes(zone, on_date=on_date) if zone else None
            folders = rec.folders if isinstance(rec, TaskRecord) else rec.get("folders")
            rows.append(
                {
                    "task_id": task_id,
                    "trigger_id": (
                        rec.trigger_id if isinstance(rec, TaskRecord) else rec.get("trigger_id")
                    ),
                    "enabled": rec.enabled if isinstance(rec, TaskRecord) else rec.get("enabled"),
                    "cron_local": cron_local,
                    "cron_utc": rec.cron_utc if isinstance(rec, TaskRecord) else rec.get("cron_utc"),
                    "cron_drift": bool(expected) and cron_local != expected,
                    "tz_drift": (
                        stored_offset is not None
                        and now_offset is not None
                        and int(stored_offset) != int(now_offset)
                    ),
                    "folders_bound": bool(folders),
                    "last_run": (
                        rec.lastRunAt if isinstance(rec, TaskRecord) else rec.get("lastRunAt")
                    ),
                    "prompt_status": _prompt_status(
                        task_id, prompt, workspace_root, plugin_version
                    ),
                }
            )
        return {"backend": self.id, "rows": rows}


# ---------------------------------------------------------------------------
# The `schedule-realign` quiet job (gap analysis §5.2 option 2; spec §6 item 5)
# ---------------------------------------------------------------------------
#
# A cron registered in UTC is a FIXED instant of the day. The customer's 7 AM is
# not: it moves by an hour twice a year. Converting once at registration means
# the morning brief quietly fires at 6 AM all winter, which is the posture the
# product has today and the reason this job exists.
#
# The job compares the offset stored on each trigger with the offset the zone is
# actually on, and when they differ it plans ONE update per Command Room trigger
# that re-projects the same local time onto the new UTC hour. The local cron does
# not change — the customer did not move anything — so the job writes
# `schedule_refreshed(field="cron_utc")` and NEVER `schedule_config_changed`.
# `cron_utc` is deliberately not one of `schedule_refresh.SEMANTIC_FIELDS`, so
# the morning brief never announces a no-op the customer did not ask about.
#
# The job PLANS. The maintenance fire executes the plans, the same way every
# other plan in this module is executed by the surface that owns the call.
#
# Fallback, documented and not built: if a cron-only update on a device-bound
# trigger turns out to need a click on the customer's computer, the answer is a
# shadow slot — register both candidate UTC hours and have the bootloader exit
# when the workspace wall clock is not the slot. It costs one extra fire a day
# per chat and needs the lateness math to learn the shadow slot, which is why it
# is the fallback rather than the plan.

REALIGN_JOB_ID = "schedule-realign"
REALIGN_NOMINAL_CRON = "0 0 * * *"


def plan_realign(
    trigger_map: Optional[dict] = None,
    *,
    workspace_root=None,
    on_date=None,
    backend: Optional[Any] = None,
) -> dict:
    """Plan the UTC re-projection for every Command Room trigger that drifted.

    Returns `{"drifted": [...], "plans": [...], "receipts": [...]}`. A workspace
    whose stored offsets all still match the zone returns empty lists and the
    job says nothing — it is a quiet job, and a quiet job with nothing to do
    writes no receipt at all.

    The job iterates the STORED map: pass `workspace_root` and it reads it, which
    is how the maintenance fire calls it. An explicit `trigger_map` still wins.
    """
    trigger_map = _resolve_trigger_map(trigger_map, workspace_root)
    on_date = on_date or _dt.date.today()
    backend = backend or CloudTriggersBackend()
    drifted = []
    plans = []
    receipts = []

    for task_id, row in sorted(trigger_map.items()):
        if not isinstance(row, dict):
            continue
        if row.get("backend") not in (None, "cloud"):
            continue
        zone = row.get("tz")
        stored = row.get("utc_offset_minutes")
        cron_local = row.get("cron_local")
        if not zone or stored is None or not cron_local:
            continue
        now_offset = current_utc_offset_minutes(zone, on_date=on_date)
        if now_offset is None or int(now_offset) == int(stored):
            continue

        converted = local_cron_to_utc(cron_local, zone, on_date=on_date, task_id=task_id)
        if isinstance(converted, Refusal):
            # A cron that cannot be expressed on the new offset is left alone
            # and reported — never rewritten into something the customer did
            # not ask for.
            drifted.append(
                {
                    "task_id": task_id,
                    "stored_offset": int(stored),
                    "current_offset": int(now_offset),
                    "refusal": converted,
                }
            )
            continue

        plan = backend.plan_update(
            task_id,
            trigger_id=row.get("trigger_id"),
            cron_local=cron_local,
            tz_name=zone,
            on_date=on_date,
        )
        if isinstance(plan, Refusal):
            drifted.append(
                {
                    "task_id": task_id,
                    "stored_offset": int(stored),
                    "current_offset": int(now_offset),
                    "refusal": plan,
                }
            )
            continue

        drifted.append(
            {
                "task_id": task_id,
                "stored_offset": int(stored),
                "current_offset": int(now_offset),
                "cron_utc_old": row.get("cron_utc"),
                "cron_utc_new": converted[0],
            }
        )
        plans.append(plan)
        receipts.append(
            (
                "schedule_refresh.log_schedule_refreshed",
                {
                    "task_id": task_id,
                    "field": "cron_utc",
                    "old_value": row.get("cron_utc"),
                    "new_value": converted[0],
                },
            )
        )
        # Re-stamp the stored row, or tomorrow's run sees the same drift and
        # re-projects the same chat for the rest of the season.
        receipts.append(
            (
                "schedule_backend.record_trigger_map",
                {
                    "task_id": task_id,
                    "row": {
                        "cron_utc": converted[0],
                        "utc_offset_minutes": int(now_offset),
                    },
                    "merge": True,
                },
            )
        )

    return {"drifted": drifted, "plans": plans, "receipts": receipts}


#: The keys a `last_run` / `next_run_at` OBJECT may carry its instant under.
#: BRIDGE2 (Train 2 §4 MUST 4, FIX-6, F-OFF-9): the merged app's `last_run`
#: object carries `fired_at` (seen on the merged-seat walk, 2026-09-26); without
#: it every cloud task's `lastRunAt` read None and health/verify never saw
#: the app's last-run time.
ISO_OBJECT_KEYS = ("iso", "timestamp", "at", "value", "fired_at")


def _iso_or_none(value) -> Optional[str]:
    """`last_run` / `next_run_at` arrive as a string or as an object. Take both."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        for key in ISO_OBJECT_KEYS:
            got = value.get(key)
            if isinstance(got, str) and got:
                return got
            if isinstance(got, (int, float)) and not isinstance(got, bool):
                return _iso_or_none(got)
        return None
    if isinstance(value, (int, float)):
        return (
            _dt.datetime.fromtimestamp(value, _dt.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    return str(value)


# ---------------------------------------------------------------------------
# SCHEDREG1 (SPEC_V5330_FIXLANES §1 MUST 4/6/8) — registration, planned whole.
#
# `enable-command-room-schedules` registers by executing what these functions
# return; the SKILL narrates them and is never their only home. One call plans
# every chat for THIS seat: the availability guard first (a seat that cannot
# bake a writer pair on the cloud backend is refused, nothing planned), then
# per task the ONE composer (`schedule_refresh.compose_bootloader_body` for a
# chat, `schedule_config.compose_silent_task_prompt` for a silent task), then
# create / update / current. A body equal to the composed one is `current` and
# costs no call, which is what makes a second run plan nothing at all.
# ---------------------------------------------------------------------------

#: The chats' notification posture: a chat pushes; a silent task takes its own
#: registry flag (`SILENT_TASKS[...]["notify"]`).
CHAT_NOTIFY = True


def _registered_by_task(registered) -> dict:
    """`{task_id: record}` out of normalized records (TaskRecord or dict)."""
    out = {}
    for rec in registered or []:
        if isinstance(rec, TaskRecord):
            out[rec.taskId] = rec
        elif isinstance(rec, dict) and rec.get("taskId"):
            out[str(rec["taskId"])] = rec
    return out


def _rec_get(rec, key):
    if isinstance(rec, TaskRecord):
        return getattr(rec, key, None)
    return rec.get(key) if isinstance(rec, dict) else None


def compose_registration_body(task_id: str, *, basename: str,
                              plugin_version: str, abs_path: str,
                              writer_pair=None, discover_block=None,
                              plugin_root=None) -> str:
    """The body THIS seat registers for `task_id` - chat or silent - through
    the one composer each kind has. Raises KeyError for an id with neither."""
    sc = _schedule_config()
    import schedule_refresh as _sr  # noqa: WPS433 - lazy

    wid, wder = (writer_pair or (None, None))
    if task_id in sc.SILENT_TASKS:
        # T2 CB-4 (REVIEW_T2_FIRE1 N-1, R-WALK-4): the registered silent body carries
        # its one folder request for THIS seat's path; without the path the composer
        # keeps today's text (FIRE1 (1e)).
        return sc.compose_silent_task_prompt(
            task_id, basename, writer_id=wid, writer_derivation=wder,
            workspace_path=(str(abs_path or "").strip() or None))
    return _sr.compose_bootloader_body(
        task_id, workspace_basename=basename, plugin_version=plugin_version,
        plugin_root=plugin_root, abs_path=abs_path,
        discover_block=discover_block, writer_id=wid,
        writer_derivation=wder)


def plan_registration(tools=None, *, workspace_root, abs_path: Optional[str] = None,
                      task_ids=None, registered=None, plugin_version: str = "",
                      tz_name: str = "", writer_pair=None,
                      crons: Optional[dict] = None,
                      discover_block: Optional[str] = None, on_date=None,
                      plugin_root=None, env=None,
                      trigger_map: Optional[dict] = None,
                      declaration: Optional[dict] = None,
                      uncomposed_current: bool = False) -> dict:
    """Plan the whole registration for THIS seat (MUST 4). Pure: no call.

    Returns `{"ok", "reason", "line", "backend", "pair", "creates",
    "updates", "current", "refused"}`:
      * the availability guard refuses (no scheduler, a shut gate, or - on
        the cloud backend - no bakeable writer pair) -> `ok: False` with its
        ONE sentence and nothing planned;
      * otherwise per task: no registered record -> a `plan_create`; a record
        whose prompt is None -> skipped (unknown, never rewritten); a record
        for ANOTHER workspace folder -> refused with its sentence; a record
        whose body `prompts_equivalent` the composed one -> `current` (no
        call); else -> a `plan_update(prompt=...)` carrying the trigger id
        (from the record, else the stored trigger map).
    `abs_path` is the workspace's path on the customer's computer (the
    connected folder whose name is the workspace's); `writer_pair` defaults
    to `schedule_config.registration_pair(abs_path)` - this process's own,
    never the synced file (D-1, R-RW3-3). `crons` overrides the shipped
    `DEFAULT_SCHEDULES` cron per task (the customer's overrides).
    `trigger_map` is the stored map as the door read it
    (`run_helper schedule_backend:read_trigger_map` on a merged seat, where
    the workspace is not in this process); omitted, it is read from
    `workspace_root` when that is readable here.

    BRIDGE2 (Train 2 §4 MUST 1): `abs_path` omitted is this seat's
    `CR_DEVICE_WORKSPACE` - the path the door forwards on every rendered
    line, never an argument the path fence would have to judge.

    BRIDGE2 (Train 2 §4 MUST 2, FIX-4, F-OFF-7, R-WALK-3): records are
    matched to THIS seat by the digest of their folder's path on the
    customer's computer (`schedule_config.device_digest`, the HYGIENE3 D-4
    digest), never by the folder's name alone. A record bound to another
    computer is FOREIGN: it is never planned, updated or rewritten here.
    When a foreign set is the BOUND set - on the computer that holds the
    declaration, or (no declaration with a digest) any enabled foreign
    record - nothing is planned: `ok: False`, `reason: bound_elsewhere`,
    and ONE sentence naming the computer that holds it
    (`bound_elsewhere_line`). `declaration` is the workspace's declaration
    as the caller read it; omitted, it is read from `workspace_root` when
    that is readable here.

    BRIDGE3 (Train 2b §5 MUST 1, F-T2-5): `uncomposed_current` is the
    bridge's mode. A chat whose body cannot be composed in this process
    (`composer_reachable` False: a runtime staged beside the data ships no
    `skills/`, so no bootloader template) is never composed: a registered one
    is `current` and listed in `not_compared`, an unregistered one plans
    nothing. Off (every other caller), the plan is today's, byte for byte.
    """
    sc = _schedule_config()
    import schedule_refresh as _sr  # noqa: WPS433 - lazy

    env_map = dict(os.environ) if env is None else dict(env)
    if not str(abs_path or "").strip():
        abs_path = (str(env_map.get(sc.DEVICE_WORKSPACE_ENV, "") or "").strip()
                    or None)
    avail = sc.scheduler_availability(tools, workspace_root=workspace_root,
                                      abs_path=abs_path,
                                      writer_pair=writer_pair, env=env)
    out = {"ok": False, "reason": avail.get("reason"),
           "line": avail.get("line") or "", "backend": None, "pair": None,
           "creates": [], "updates": [], "current": [], "refused": []}
    if not avail.get("available"):
        return out
    backend, _found = select_backend(sc._tool_names(tools))
    if backend is None:  # pragma: no cover - availability said yes
        return out
    pair = sc.registration_pair(abs_path or workspace_root,
                                writer_pair=writer_pair, env=env)
    if backend.id == "cloud" and pair is None:  # pragma: no cover - guarded
        out["reason"] = sc.REASON_NO_ACCOUNT
        out["line"] = sc.no_account_line()
        return out
    path = str(abs_path or "").strip()
    basename = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    # SCHEDREG1 review B-3 (v5.33.0 merge-fix): the folder a registration is
    # bound to is THIS workspace's, by name - never a path from another
    # machine or another folder. The skill's prose already picks the matching
    # `connectedFolders` entry; the code refuses anything else outright.
    if path and workspace_root and not folder_is_this_workspace(path, workspace_root):
        out["reason"] = REASON_FOREIGN_FOLDER
        out["line"] = FOREIGN_FOLDER_LINE
        return out
    seat = seat_records(registered, abs_path=path,
                        declaration=(declaration if declaration is not None
                                     else _read_declaration(workspace_root)))
    out["foreign"] = seat["foreign_ids"]
    if seat["bound_elsewhere"]:
        out["reason"] = REASON_BOUND_ELSEWHERE
        out["line"] = bound_elsewhere_line(
            seat["holder"], tools, workspace_root=workspace_root,
            abs_path=path, env=env)
        return out
    out.update(ok=True, reason=None, line="", backend=backend.id, pair=pair)
    on_date = on_date or _dt.date.today()
    by_task = _registered_by_task(seat["own"])
    stored = _resolve_trigger_map(trigger_map, workspace_root or None)
    if uncomposed_current:
        out["not_compared"] = []
    for task_id in task_ids or []:
        rec = by_task.get(task_id)
        body = None
        if not uncomposed_current or composer_reachable(task_id, plugin_root):
            body = compose_registration_body(
                task_id, basename=basename, plugin_version=plugin_version,
                abs_path=path, writer_pair=pair, discover_block=discover_block,
                plugin_root=plugin_root)
        if rec is None and body is None:
            continue  # BRIDGE3: nothing to compare and nothing the bridge creates
        if rec is None:
            cron = ((crons or {}).get(task_id)
                    or (sc.DEFAULT_SCHEDULES.get(task_id) or {}).get("cron"))
            notify = (bool(sc.SILENT_TASKS[task_id].get("notify"))
                      if task_id in sc.SILENT_TASKS else CHAT_NOTIFY)
            plan = backend.plan_create(
                task_id, prompt=body, cron_local=cron, tz_name=tz_name,
                notify=notify, workspace_basename=basename, folders=[path],
                on_date=on_date)
            if isinstance(plan, Refusal):
                out["refused"].append({"task_id": task_id, "line": plan.line,
                                       "reason_code": plan.reason_code})
            else:
                out["creates"].append(plan)
            continue
        registered_prompt = _rec_get(rec, "prompt")
        if registered_prompt is None:
            continue  # the listing carried no text: unknown, never rewritten
        if workspace_root:
            guard = _sr.refresh_workspace_guard(task_id, registered_prompt,
                                                workspace_root,
                                                plugin_root=plugin_root)
            if not guard["ok"]:
                out["refused"].append({"task_id": task_id,
                                       "line": guard["line"],
                                       "reason_code": "cross_folder"})
                continue
        if body is None:
            # BRIDGE3: registered, and its body cannot be composed here, so it
            # is current and said to be uncompared; never a raise, never a
            # rewrite of a body nobody could read against.
            out["current"].append(task_id)
            out["not_compared"].append(task_id)
            continue
        if _sr.prompts_equivalent(body, registered_prompt):
            out["current"].append(task_id)
            continue
        trigger_id = (_rec_get(rec, "trigger_id")
                      or (stored.get(task_id) or {}).get("trigger_id"))
        plan = backend.plan_update(task_id, trigger_id=trigger_id,
                                   prompt=body)
        if isinstance(plan, Refusal):
            out["refused"].append({"task_id": task_id, "line": plan.line,
                                   "reason_code": plan.reason_code})
        else:
            out["updates"].append(plan)
    return out


#: SCHEDREG1 review B-3 - a registration bound to a folder that is not this
#: workspace's is refused, in one sentence a customer can act on.
REASON_FOREIGN_FOLDER = "foreign_folder"
FOREIGN_FOLDER_LINE = (
    "Your scheduled chats are set up for the Command Room folder attached to "
    "this chat, and the folder named here is a different one, so nothing was "
    "registered."
)


#: BRIDGE2 (Train 2 §4 MUST 2, R-WALK-3) - registering from a second
#: computer while another computer holds the bound set is refused in ONE
#: sentence naming that computer, then the invite producer's sentence (D-5).
REASON_BOUND_ELSEWHERE = "bound_elsewhere"
BOUND_ELSEWHERE_LINE = (
    "Your scheduled chats run on {name}, so none were set up here."
)
BOUND_ELSEWHERE_NAMELESS = (
    "Your scheduled chats run on another computer, so none were set up here."
)
BOUND_ELSEWHERE_INVITE = (
    "To run them from this computer instead, delete them there first, then "
    "say `set up command room schedules` here."
)
BOUND_ELSEWHERE_ALTERNATIVE = (
    "To run them from this computer instead, delete them there first; they "
    "are then set up from a Command Room chat in the Claude desktop app with "
    "this computer's folder attached."
)


def bound_elsewhere_line(holder=None, tools=None, *, workspace_root=None,
                         abs_path=None, env=None) -> str:
    """The refusal: the holding computer by name when its name is a plain one
    (`schedule_config._clean_device_name`, the rule `declared_line` uses)
    and the sentence validates with the fire families on, the nameless form
    otherwise; then the second sentence from the ONE invite producer
    (`schedule_config.setup_invite_line`, D-5)."""
    sc = _schedule_config()
    first = BOUND_ELSEWHERE_NAMELESS
    name = sc._clean_device_name(holder)
    if name:
        named = BOUND_ELSEWHERE_LINE.format(name=name)
        try:
            from chat_output_validator import validate_chat_output
            if validate_chat_output(named, fired_via="scheduled").ok:
                first = named
        except Exception:  # noqa: BLE001 - the nameless form is always safe
            first = BOUND_ELSEWHERE_NAMELESS
    second = sc.setup_invite_line(
        tools, workspace_root=workspace_root, abs_path=abs_path, env=env,
        invite=BOUND_ELSEWHERE_INVITE, alternative=BOUND_ELSEWHERE_ALTERNATIVE)
    return first + " " + second


def record_device_path(rec) -> Optional[str]:
    """The folder a registered record is bound to, on the customer's
    computer: its `folders[0]` (the listing's `derived_state.folders` or a
    plain `folders`), else the path its own body asks for (the bootloader's
    folder request). None when the record names neither."""
    folders = _rec_get(rec, "folders")
    raw = _rec_get(rec, "raw") if isinstance(rec, TaskRecord) else rec
    if not folders and isinstance(raw, dict):
        derived = raw.get("derived_state")
        if isinstance(derived, dict) and isinstance(derived.get("folders"), list):
            folders = derived["folders"]
    if isinstance(folders, (list, tuple)):
        for entry in folders:
            if isinstance(entry, dict):
                entry = entry.get("path") or entry.get("folder") or ""
            text = str(entry or "").strip()
            if text:
                return text
    prompt = _rec_get(rec, "prompt")
    if isinstance(prompt, str) and prompt:
        import schedule_refresh as _sr  # noqa: WPS433 - lazy
        baked = _sr.baked_abs_path(prompt)
        if baked and "<" not in baked:
            return baked
    return None


def _read_declaration(workspace_root) -> Optional[dict]:
    """The workspace's declaration when readable in this process, else None
    (a merged seat's container cannot see the workspace; it hands the
    declaration in, or the refusal is nameless)."""
    if not workspace_root:
        return None
    try:
        return _schedule_config().scheduled_writer(workspace_root)
    except Exception:  # noqa: BLE001 - a reader never raises into a plan
        return None


def seat_records(registered, *, abs_path, declaration=None) -> dict:
    """Split registered records into THIS seat's and another computer's, by
    the digest of each record's folder path against this seat's
    (`abs_path`). A record with no path, or a seat with none, cannot be
    judged and stays this seat's (today's behaviour). Returns
    `{"own", "foreign", "foreign_ids", "bound_elsewhere", "holder"}`:
    `bound_elsewhere` when a foreign record sits on the computer the
    declaration names (its `device_digest`), or - with no digest declared -
    when any foreign record is enabled; `holder` is that computer's name
    when the declaration knows it (as the holder, or as the computer the
    declaration was taken from)."""
    sc = _schedule_config()
    here = sc.device_digest(abs_path) if abs_path else None
    decl = declaration if isinstance(declaration, dict) else {}
    decl_digest = decl.get("device_digest")
    prev_digest = decl.get("previous_device_digest")
    own, foreign = [], []
    for rec in registered or []:
        where = record_device_path(rec) if here else None
        digest = sc.device_digest(where) if where else None
        if here and digest and digest != here:
            foreign.append((rec, digest))
        else:
            own.append(rec)
    bound, holder = False, None
    for rec, digest in foreign:
        if decl_digest:
            if digest == decl_digest:
                bound, holder = True, decl.get("device")
                break
        elif _rec_get(rec, "enabled") is not False:
            bound = True
            if prev_digest and digest == prev_digest:
                holder = decl.get("previous_device")
    ids = sorted({str(_rec_get(r, "taskId")) for r, _d in foreign
                  if _rec_get(r, "taskId")})
    return {"own": own, "foreign": [r for r, _d in foreign],
            "foreign_ids": ids, "bound_elsewhere": bound, "holder": holder}


def folder_is_this_workspace(abs_path, workspace_root) -> bool:
    """True when `abs_path` names THIS workspace's folder (same basename as
    `workspace_root`, on whichever machine). With no `workspace_root` to
    compare against nothing can be checked, and the answer is False - a
    registration never binds a folder it cannot vouch for."""
    path = str(abs_path or "").strip().replace("\\", "/").rstrip("/")
    root = str(workspace_root or "").strip().replace("\\", "/").rstrip("/")
    if not path or not root:
        return False
    return path.rsplit("/", 1)[-1] == root.rsplit("/", 1)[-1]


def device_folder_for(workspace_root, connected_folders) -> Optional[str]:
    """The entry of `get_device_info.connectedFolders` that IS this workspace
    (by basename), or None when none or more than one matches. The seat's own
    folder, never a path from another machine (R-RW3-3)."""
    root = str(workspace_root or "").strip().replace("\\", "/").rstrip("/")
    want = root.rsplit("/", 1)[-1] if root else ""
    hits = []
    for entry in connected_folders or []:
        if isinstance(entry, dict):
            entry = entry.get("path") or entry.get("folder") or ""
        text = str(entry or "").strip()
        if text and want and text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] == want:
            hits.append(text)
    return hits[0] if len(hits) == 1 else None


def bridge_plan(tools, *, workspace_root, abs_path: Optional[str] = None,
                registered=None, plugin_version: str, tz_name: str,
                writer_pair=None, discover_block: Optional[str] = None,
                on_date=None, plugin_root=None, env=None,
                trigger_map: Optional[dict] = None) -> dict:
    """The update bridge's schedule phase (MUST 8, coordinator decision D-8).

    It REFRESHES only chats already in the stored trigger map - it never
    first-registers. An empty map plans nothing and returns
    `first_registration_needed: True` with the ONE sentence the invite
    producer composes (`schedule_config.setup_invite_line`) - the phrase only
    where this seat could act on it. Otherwise it is `plan_registration` over
    the mapped ids, with every CREATE dropped (a mapped chat missing from the
    listing is the customer's to re-add, never the bridge's).
    """
    sc = _schedule_config()
    stored = _resolve_trigger_map(trigger_map, workspace_root or None)
    mapped = sorted(k for k, v in stored.items() if isinstance(v, dict))
    if not mapped:
        return {"ok": True, "first_registration_needed": True,
                "line": sc.setup_invite_line(
                    tools, workspace_root=workspace_root, abs_path=abs_path,
                    env=env, invite=FIRST_REGISTRATION_INVITE,
                    alternative=FIRST_REGISTRATION_ALTERNATIVE),
                "creates": [], "updates": [], "current": [], "refused": []}
    # BRIDGE2 (Train 2 §4 MUST 1): only a mapped chat the listing carries is
    # composed. A create is dropped on this path anyway, and composing needs
    # the bootloader template, which a runtime staged beside the data does
    # not carry (it ships no `skills/`) - so a mapped chat with no record
    # costs nothing and never fails the plan.
    handed = registered is not None
    if handed and not isinstance(registered, (list, tuple)):
        # REVIEW_T2B_BRIDGE3 N-6: a listing that is not a list (a scalar, a
        # placeholder string) is handed and names nothing; never a raise.
        registered = []
    listed = set(_registered_by_task(registered))
    # LOWS2 row 9 (REVIEW_T2B_BRIDGE3 N-11): the plan compares the SAME
    # record `bridge_named` names - the one `_seat_placed` kept for this seat
    # (the enabled one when an id is listed twice) - never the last record
    # per id, so a named chat is never `compared: false` for a copy. Every
    # other record stays (a foreign seat's records still reach the
    # bound-elsewhere question).
    placed = registered
    if handed:
        kept = _seat_placed(registered, _seat_path(abs_path, env),
                            workspace_root)[0]
        placed = [r for r in registered
                  if str(_rec_get(r, "taskId") or "") not in kept
                  or r is kept[str(_rec_get(r, "taskId"))]]
    planned = plan_registration(
        tools, workspace_root=workspace_root, abs_path=abs_path,
        task_ids=[t for t in mapped if t in listed], registered=placed,
        plugin_version=plugin_version, tz_name=tz_name,
        writer_pair=writer_pair, discover_block=discover_block,
        on_date=on_date, plugin_root=plugin_root, env=env,
        trigger_map=stored, uncomposed_current=handed)
    planned["first_registration_needed"] = False
    planned["creates"] = []
    if handed:
        # BRIDGE3 (Train 2b §5 MUST 1, F-T2-5): the chats this seat holds,
        # named from the listing, and the one line that says so.
        planned["named"] = (bridge_named(registered, mapped, planned,
                                         workspace_root=workspace_root,
                                         abs_path=abs_path, env=env)
                            if planned.get("ok") else [])
        planned["chats_line"] = bridge_chats_line(planned["named"])
        planned["unplaced"] = (
            [x for x in _seat_placed(registered, _seat_path(abs_path, env),
                                     workspace_root)[1] if x in mapped]
            if planned.get("ok") else [])
        if planned.get("not_compared"):
            planned["note"] = BODIES_NOT_COMPARED_NOTE
    return planned


#: BRIDGE3 (Train 2b §5 MUST 1) - a code string the plan carries when a
#: chat's body could not be compared in this process. Never spoken.
BODIES_NOT_COMPARED_NOTE = "bodies not compared here"


def composer_reachable(task_id: str, plugin_root=None) -> bool:
    """Can THIS process compose `task_id`'s registered body? A silent task
    composes from the registry alone (always True); a chat needs the
    bootloader template and the orchestrator map under `plugin_root` (else
    the plugin this module belongs to), which a runtime staged beside the
    data does not ship (it carries no `skills/`)."""
    sc = _schedule_config()
    if task_id in sc.SILENT_TASKS:
        return True
    import schedule_refresh as _sr  # noqa: WPS433 - lazy
    root = Path(plugin_root) if plugin_root else _sr._PLUGIN_ROOT
    return ((root / _sr.BOOTLOADER_TEMPLATE_RELPATH).is_file()
            and (root / _sr.ORCHESTRATOR_MAP_RELPATH).is_file())


def _seat_placed(registered, path, workspace_root=None):
    """`({task_id: record}, unplaced_ids)` for THIS seat's records. With the
    seat's path known, a record is this seat's only when its own folder
    (`record_device_path`) has this seat's digest: a record that names no
    folder cannot be placed on a computer and is counted in `unplaced`,
    never named (REVIEW_T2B_BRIDGE3 N-4). With no seat path nothing can be
    judged and `seat_records`' own set stands. When an id appears twice,
    the enabled record wins."""
    sc = _schedule_config()
    records = [r for r in (registered or []) if isinstance(r, (dict, TaskRecord))]
    unplaced = set()
    if path:
        here = sc.device_digest(path)
        own = []
        for rec in records:
            where = record_device_path(rec)
            if not where:
                if _rec_get(rec, "taskId"):
                    unplaced.add(str(_rec_get(rec, "taskId")))
                continue
            if sc.device_digest(where) == here:
                own.append(rec)
    else:
        own = seat_records(records, abs_path=path,
                           declaration=_read_declaration(workspace_root))["own"]
    by_task = {}
    for rec in own:
        tid = _rec_get(rec, "taskId")
        if not tid:
            continue
        tid = str(tid)
        held = by_task.get(tid)
        if held is None or (_rec_get(held, "enabled") is False
                            and _rec_get(rec, "enabled") is not False):
            by_task[tid] = rec
    return by_task, sorted(unplaced - set(by_task))


def _seat_path(abs_path, env) -> str:
    sc = _schedule_config()
    env_map = dict(os.environ) if env is None else dict(env)
    return (str(abs_path or "").strip()
            or str(env_map.get(sc.DEVICE_WORKSPACE_ENV, "") or "").strip())


def bridge_named(registered, mapped, planned, *, workspace_root=None,
                 abs_path=None, env=None) -> list:
    """`[{task_id, name, listed_name, enabled, compared}]` for every mapped
    chat the listing holds on THIS seat (`_seat_placed`, the seat path from
    `abs_path` or the forwarded `CR_DEVICE_WORKSPACE`), in
    `DEFAULT_SCHEDULES` order. The listing decides WHICH chats are named;
    the registry decides what they are called: `name` is always
    `task_display_name` (a registered chat's listed name is
    `trigger_name`'s "<Display name> - Command Room (<folder>)", or
    whatever the customer renamed it to; it is kept as `listed_name`, never
    spoken; REVIEW_T2B_BRIDGE3 N-1). `compared` is False for a chat whose
    body was not composed here (`planned["not_compared"]`)."""
    sc = _schedule_config()
    by_task, _unplaced = _seat_placed(registered, _seat_path(abs_path, env),
                                      workspace_root)
    skipped = set(planned.get("not_compared") or [])
    judged = set(planned.get("current") or []) | {
        getattr(p, "task_id", None) for p in planned.get("updates") or []}
    order = list(sc.DEFAULT_SCHEDULES)
    ids = sorted((t for t in mapped if t in by_task),
                 key=lambda t: (order.index(t) if t in order else len(order), t))
    named = []
    for task_id in ids:
        rec = by_task[task_id]
        named.append({"task_id": task_id,
                      "name": sc.task_display_name(task_id),
                      "listed_name": str(_rec_get(rec, "name") or "").strip(),
                      "enabled": _rec_get(rec, "enabled") is not False,
                      "compared": task_id in judged and task_id not in skipped})
    return named


#: BRIDGE3 (Train 2b §5 MUST 1, F-T2-5) - the bridge's one line naming the
#: chats this seat holds. Composed only by `bridge_chats_line`.
BRIDGE_CHATS_LINE = "Your {n} scheduled chats are set up: {names}."
BRIDGE_CHAT_LINE_ONE = "Your scheduled chat is set up: {name}."


def _join_names(names: list) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def bridge_chats_line(named) -> str:
    """ONE sentence naming the chats in `named` (the plan's `named`): the
    count and the names from the listing, the singular form for one, "" for
    none. Nothing about comparisons, ever."""
    names = [str((row or {}).get("name") or "").strip()
             for row in (named or []) if isinstance(row, dict)]
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return BRIDGE_CHAT_LINE_ONE.format(name=names[0])
    return BRIDGE_CHATS_LINE.format(n=len(names), names=_join_names(names))


#: The bridge's one sentence when nothing is registered the merged way yet
#: (MUST 8) - composed through the invite producer, never printed raw.
FIRST_REGISTRATION_INVITE = (
    "Your scheduled chats aren't set up on this version yet. Say `set up "
    "command room schedules` once and they will be."
)
FIRST_REGISTRATION_ALTERNATIVE = (
    "Your scheduled chats aren't set up on this version yet; they are set up "
    "from a Command Room chat in the Claude desktop app with your Command "
    "Room folder attached, and every chat still runs when you ask for it by "
    "name."
)

#: MUST 7's constant for the update bridge's BLOCKED message (the bridge file
#: is HYGIENE3's; the swap is named in the lane record's Seams). Invitation
#: and alternative, decided by `schedule_config.setup_invite_line`.
BRIDGE_SCHEDULES_INVITE = FIRST_REGISTRATION_INVITE
BRIDGE_SCHEDULES_ALTERNATIVE = FIRST_REGISTRATION_ALTERNATIVE


def bridge_schedules_line(tools=None, *, workspace_root=None, abs_path=None,
                          env=None) -> str:
    """The bridge's schedules sentence through the one producer (MUST 7)."""
    return _schedule_config().setup_invite_line(
        tools, workspace_root=workspace_root, abs_path=abs_path, env=env,
        invite=BRIDGE_SCHEDULES_INVITE,
        alternative=BRIDGE_SCHEDULES_ALTERNATIVE)


def scheduled_chat_rows(raw_records, *, workspace_root=None, tz_name: str = "",
                        registered_ids=None, task_ids=None,
                        backend=None, trigger_map: Optional[dict] = None,
                        device_path=None) -> list:
    """`show my scheduled chats` (MUST 6): the registry, read back.

    `raw_records` is what `plan_list()` returned. They are `normalize`d
    against the stored trigger map (`trigger_map`, else read from
    `workspace_root`), and one row is returned per task in `task_ids`
    (default: `DEFAULT_SCHEDULES`, in its order): `{"task_id", "name",
    "time", "enabled", "registered"}`. `time` is the customer's LOCAL time in
    words (`cron_to_english` of the local cron the normaliser recovered). A
    task with no registered trigger reads "not running yet" and never
    carries a time (F15: a stored cadence is not a running chat).
    `registered_ids` (e.g. the workspace's `registered_taskIds`) is accepted
    for callers that have it; only the listing decides `registered`.

    `device_path` (ROUTE3, walk row 5): this seat's folder on the customer's
    computer (`CR_DEVICE_WORKSPACE`). When given, only THIS seat's records
    feed the rows (`seat_records`), so another computer's switched-off
    copies can never decide a row. With `device_path` None the answer is
    exactly today's.
    """
    sc = _schedule_config()
    backend = backend or CloudTriggersBackend()
    records = backend.normalize(raw_records or [], trigger_map=trigger_map,
                                workspace_root=workspace_root, tz_name=tz_name)
    records = this_seat_chat_records(records, device_path)
    by_task = _registered_by_task(records)
    ids = list(task_ids) if task_ids is not None else list(sc.DEFAULT_SCHEDULES)
    rows = []
    for task_id in ids:
        rec = by_task.get(task_id)
        name = sc.task_display_name(task_id)
        if rec is None:
            rows.append({"task_id": task_id, "name": name,
                         "time": NOT_RUNNING_YET, "enabled": None,
                         "registered": False})
            continue
        cron = _rec_get(rec, "cronExpression")
        try:
            when = sc.cron_to_english(cron) if cron else ""
        except Exception:  # noqa: BLE001 - a listing never raises on one row
            when = ""
        rows.append({"task_id": task_id, "name": name, "time": when,
                     "enabled": _rec_get(rec, "enabled"),
                     "registered": True})
    return rows


#: What a not-yet-registered row says instead of a time (MUST 6).
NOT_RUNNING_YET = "not running yet"


def this_seat_chat_records(records, device_path=None) -> list:
    """ROUTE3 (walk row 5): the normalized records `show my scheduled chats`
    may read. With a `device_path`, another computer's records (a folder
    whose digest differs from this seat's) are dropped through
    `seat_records`; with none, every record is kept, as before."""
    if not device_path:
        return list(records or [])
    return list(seat_records(records, abs_path=device_path)["own"])


#: ROUTE3 fix round 1 (REVIEW_T2B_ROUTE3 N-1): the whole reply of
#: `show my scheduled chats` on the merged scheduler when this chat does not
#: know which computer's folder it is on. Listing without that would let
#: another computer's copies decide the rows (walk row 5), so nothing is
#: listed.
NO_DEVICE_FOLDER_LINE = ("I could not tell which computer this chat is on, so I did not "
                         "list your scheduled chats; open a chat with your Command Room "
                         "folder attached and ask again.")


def this_seat_chat_listing(raw_records, *, device_path, trigger_map: Optional[dict] = None,
                           tz_name: str = "", workspace_root=None) -> dict:
    """`show my scheduled chats` on the merged scheduler, fail-loud (N-1).

    With a non-blank `device_path` the answer is `{"ok": True, "rows",
    "lines"}`, exactly `scheduled_chat_lines(scheduled_chat_rows(...,
    device_path=device_path))`. With an empty, blank or missing one it is
    `{"ok": False, "rows": [], "lines": [NO_DEVICE_FOLDER_LINE]}`: it never
    falls back to the unfiltered records. `scheduled_chat_rows` itself keeps
    `device_path=None` as today's read for its other callers."""
    device = str(device_path or "").strip()
    if not device:
        return {"ok": False, "rows": [], "lines": [NO_DEVICE_FOLDER_LINE]}
    rows = scheduled_chat_rows(raw_records, workspace_root=workspace_root, tz_name=tz_name,
                               trigger_map=trigger_map, device_path=device)
    return {"ok": True, "rows": rows, "lines": scheduled_chat_lines(rows)}


def scheduled_chat_lines(rows) -> list:
    """One customer line per row of `scheduled_chat_rows`."""
    lines = []
    for row in rows or []:
        if not row.get("registered"):
            lines.append(f"{row['name']} — {NOT_RUNNING_YET}")
        elif row.get("enabled") is False:
            lines.append(f"{row['name']} — {row['time']} (paused)")
        else:
            lines.append(f"{row['name']} — {row['time']}")
    return lines


def registration_summary_lines(task_ids, *, crons: Optional[dict] = None) -> list:
    """SHOULD 10: the install summary names each registered chat with its
    LOCAL time, from `DEFAULT_SCHEDULES` (or the customer's override) - never
    a typed count or a typed time (G7)."""
    sc = _schedule_config()
    lines = []
    for task_id in task_ids or []:
        cron = ((crons or {}).get(task_id)
                or (sc.DEFAULT_SCHEDULES.get(task_id) or {}).get("cron"))
        try:
            when = sc.cron_to_english(cron) if cron else ""
        except Exception:  # noqa: BLE001
            when = ""
        lines.append(f"{sc.task_display_name(task_id)} — {when}".rstrip(" —"))
    return lines


# ---------------------------------------------------------------------------
# Backend selection (spec §6 item 1)
# ---------------------------------------------------------------------------


def _tool_ids(tools: Iterable[Any]) -> list:
    """Accept `ToolDescriptor`s, bare tool-id strings, or `{"name": <id>}`
    records; return the ids of the tools that are LOADED.

    A deferred stub — a record or object carrying `deferred` truthy, a name the
    host shows but has not loaded — is NOT a tool here (SCHEDDISCOVER1 R-SD-4):
    it is skipped, exactly as `schedule_config._tool_names` skips it for the
    availability guard, so the guard and this selector give one answer on one
    list. A plain record is read by its `name` (or `tool`), the same keys the
    guard reads.
    """
    ids = []
    for t in tools or []:
        if isinstance(t, str):
            ids.append(t)
        elif isinstance(t, dict):
            if t.get("deferred"):
                continue
            tid = t.get("name") or t.get("tool")
            if isinstance(tid, str) and tid:
                ids.append(tid)
        else:
            if getattr(t, "deferred", False):
                continue
            tid = getattr(t, "tool_id", None)
            if tid:
                ids.append(tid)
    return ids


def select_backend(tools: Iterable[Any]) -> Tuple[Optional[ScheduleBackend], BackendDiscovery]:
    """Pick the backend from what the model can SEE, never from an env marker.

    `create_trigger` present → cloud. `create_scheduled_task` present → legacy.
    Both → cloud, with `legacy_visible=True` (the seat is mid-migration).
    Neither → `(None, …)` carrying `NO_SCHEDULER_LINE` as the reason, which is
    the honest sentence the skills say.
    """
    ids = _tool_ids(tools)
    cloud_seen = CLOUD_TOOL_MAP["create"] in ids
    legacy_seen = LEGACY_TOOL_MAP["create"] in ids

    if cloud_seen:
        return (
            CloudTriggersBackend(),
            BackendDiscovery(
                tool_id=CLOUD_TOOL_MAP["create"],
                candidates_considered=len(ids),
                legacy_visible=legacy_seen,
                backend_id="cloud",
                reason="",
            ),
        )
    if legacy_seen:
        return (
            LegacyBackend(),
            BackendDiscovery(
                tool_id=LEGACY_TOOL_MAP["create"],
                candidates_considered=len(ids),
                legacy_visible=True,
                backend_id="legacy",
                reason="",
            ),
        )
    return (
        None,
        BackendDiscovery(
            tool_id=None,
            candidates_considered=len(ids),
            legacy_visible=False,
            backend_id=None,
            reason=NO_SCHEDULER_LINE,
        ),
    )



# ---------------------------------------------------------------------------
# SCHEDDISCOVER1 (SPEC_NIGHTM3_LANES §6) — discover the scheduler before looking
# for it, say which kind was found, and say WHY when there is none.
#
# `select_backend` decides from the tool list the model can SEE. On engine
# 2.1.258+ an MCP tool arrives as a deferred stub that must be LOADED through
# the host's tool search before it is on that list, so a chat that looks first
# and loads never concludes "no scheduler" on a seat that has one. The four
# scheduling skills now carry ONE rendered step that loads first; the step's
# text is DERIVED here from the two tool maps, written into the skills by
# `scripts/dev/propagate_scheduler_discovery.py`, and pinned by guard G75.
# ---------------------------------------------------------------------------

#: The sentinels around every propagated copy of the step (HTML comments, so
#: they never render and never sit inside the fence).
DISCOVERY_BEGIN = "<!-- SCHEDULER DISCOVERY >>> -->"
DISCOVERY_END = "<!-- <<< SCHEDULER DISCOVERY -->"

#: The fence language. NOT a shell or python fence on purpose: the step is
#: instruction text, and guard G69's census / G73's shell scan must never read
#: it as code (spec S-1 pass line (c)).
DISCOVERY_FENCE_LANG = "text"

#: The most lines the rendered block may take, fences included (spec S-1).
DISCOVERY_MAX_LINES = 25

#: The key that marks a tool-list entry as a deferred stub — a name the host
#: shows but has not loaded. A mapping or an object carrying it truthy is a
#: stub; `select_backend` never selects on one (it reads ids, not mappings).
DEFERRED_STUB_KEY = "deferred"


def _server(prefix: str) -> str:
    """A tool family's SERVER name: the part between `mcp__` and the last `__`."""
    if prefix.startswith("mcp__"):
        prefix = prefix[len("mcp__"):]
    return prefix.rstrip("_")


def _family_query(prefix: str, *tool_ids: str) -> str:
    """`+<server> <op words>` — the host tool search's keyword form.

    `+<server>` REQUIRES the server in a tool's name; the words after it rank
    the matches. The op words are the tool ids' own verbs, split on `_` and
    de-duplicated in order, so a renamed tool changes the query by script.

    WHY NOT `select:<id>,<id>`. Two standing fences read the instruction layer
    as text. Guard G67 forbids spelling an old scheduler id, or a bare old op
    name, anywhere in it — with no waiver on a line that instructs. And the
    COPY1 source-order pins read `create_trigger` / `scheduler_availability`
    positions inside the bridge's Phase 4.7, where this block sits ahead of
    the real guard. A block that spelled either would have re-anchored those
    pins silently. The keyword form names neither, for both backends alike,
    and loads the whole family (the update / fire tools a later plan calls
    are then loaded too).
    """
    words = []
    for tid in tool_ids:
        if not tid:
            continue
        op = tid[len(prefix):] if tid.startswith(prefix) else tid.rsplit("__", 1)[-1]
        for word in op.split("_"):
            if word and word not in words:
                words.append(word)
    return "+" + _server(prefix) + " " + " ".join(words)


def discovery_queries() -> tuple:
    """The exact tool-search queries the step tells the model to run.

    `(cloud, legacy)` — each scheduler's family by server name, ranked by its
    create and list verbs. Rendered from `CLOUD_TOOL_MAP` / `LEGACY_TOOL_MAP`
    and the two prefixes at CALL time, so a renamed tool changes the step's
    text on the next propagation.
    """
    cloud = _family_query(CLOUD_TOOL_PREFIX, CLOUD_TOOL_MAP["create"],
                          CLOUD_TOOL_MAP["list"])
    legacy = _family_query(LEGACY_TOOL_PREFIX, LEGACY_TOOL_MAP["create"],
                           LEGACY_TOOL_MAP["list"])
    return (cloud, legacy)


#: The two queries as shipped (the spec's `DISCOVERY_QUERIES`).
DISCOVERY_QUERIES = discovery_queries()

#: How many matches to ask the tool search for, so each whole family loads.
DISCOVERY_MAX_RESULTS = 10


def discovery_step() -> str:
    """The scheduler discovery step — ONE fenced block, verbatim in four skills.

    Load the scheduler tools by the host's own mechanism BEFORE looking; hand
    ONLY the loaded names to the availability guard and `select_backend`; then
    say the ONE opening line `opening_lines` returns, through the rendered call
    form below — the declaration when the guard said yes, the cause line when
    there is no scheduler, nothing where the guard refused. A tool that was
    still waiting is reloaded once before the answer is final (R-SD-4).
    `shared/SCHEDULER_DISCOVERY.md` and every propagated copy are this
    function's output; nothing types it.
    """
    cloud_q, legacy_q = discovery_queries()
    no_folder = CAUSE_LINES[CAUSE_CLOUD_SESSION_NO_FOLDER]
    lines = [
        "```" + DISCOVERY_FENCE_LANG,
        "SCHEDULER DISCOVERY (SCHEDDISCOVER1) - rendered by schedule_backend.discovery_step(); never edit by hand.",
        "A scheduler tool you cannot see yet may only be waiting to be loaded. Load first, look second, then decide.",
        "1. LOAD FIRST, every time. Claude Code and the Claude app: run the host's tool search once per query,",
        "   exactly as written, asking for up to " + str(DISCOVERY_MAX_RESULTS) + " results each:",
        "       " + cloud_q,
        "       " + legacy_q,
        "   The older desktop app, which has no tool search: read its full connector tool listing, end to end.",
        "2. LOOK SECOND. Hand ONLY the names that loaded, as plain strings, to this skill's availability guard",
        "   and to schedule_backend.select_backend below. A scheduler name still waiting to be loaded is not a",
        "   scheduler: it goes only into step 3's TOOLS, as {\"name\": <id>, \"" + DEFERRED_STUB_KEY + "\": true}.",
        "3. THE OPENING LINE. Render this one call with `workspace_access.py plan run_helper --json '<it>'` and",
        "   paste what it prints (the older app, or Code on this computer: `workspace_access.py run_helper` here):",
        "     " + opening_call_json(),
        "   TOOLS = the loaded names and any waiting one; AVAIL = the guard's answer, as it came back;",
        "   FOLDER = is a workspace folder attached to this chat (true/false); RELOADED = false; ERROR = null,",
        "   or the scheduler's own error text when one of its calls failed (then call again). Say each line of",
        "   `result` verbatim, once, as the first thing this skill says about schedules, before any listing or",
        "   registration; it replaces the no-scheduler sentence. An empty result adds nothing: the guard's own",
        "   refusal stands alone, and a step that says nothing about schedules still says nothing. Add nothing",
        "   of your own about where the chats run or why.",
        "   Refused because this chat has no workspace folder: say \"" + no_folder + "\" instead.",
        "4. If that line said the tools are loading: refresh the host's connector tools once, run step 1",
        "   again, then steps 2 and 3 with RELOADED = true. The answer after that reload is final.",
        "```",
    ]
    return "\n".join(lines)


# --- the one rendered call: the opening line ---------------------------------

#: The helper the step renders a call to, spelled as `run_helper` spells it.
#: It is on `workspace_access.RUN_HELPER_ALLOWLIST` (read-only: it computes one
#: sentence and writes nothing).
OPENING_LINES_HELPER = "schedule_backend:opening_lines"

#: The call's arguments, in order, and the placeholder the step spells for each.
#: `env_mode` is quoted because it is a string the chat fills from `CR_ENV`;
#: the others are JSON values (a list, the guard's object, true/false, null).
#: A pin binds these names to `opening_lines`' own signature.
OPENING_CALL_ARGS = (
    ("tools", "<TOOLS>"),
    ("env_mode", "\"<CR_ENV>\""),
    ("availability", "<AVAIL>"),
    ("folder_attached", "<FOLDER>"),
    ("reloaded", "<RELOADED>"),
    ("error_text", "<ERROR>"),
)


def opening_call_json() -> str:
    """The `run_helper` payload the step renders, placeholders unfilled."""
    args = ",".join("\"" + k + "\":" + v for k, v in OPENING_CALL_ARGS)
    return "{\"name\":\"" + OPENING_LINES_HELPER + "\",\"args\":{" + args + "}}"


# --- S-2: the local-vs-cloud declaration ------------------------------------

#: R-SD-2 (H-2): every chat Command Room registers on the cloud backend
#: requires this computer (`plan_create` sets `requires_local_device: True`,
#: R-M3-9) — the chat is KEPT in the cloud and RUNS in the customer's folder
#: here, so it fires only while this computer is on.
DECLARATION_CLOUD = (
    "Your scheduled chats are kept in the cloud under your account and run in "
    "your folder on this computer, so they fire only while it is on."
)
DECLARATION_LEGACY = (
    "Your scheduled chats run on this computer, so they fire only while it is "
    "on and the app is open."
)
#: R-SD-3 (M-3): the clause names what is true and promises nothing — no
#: surface names the older set yet, so no surface is offered for it. It is
#: joined to the cloud sentence with a semicolon.
DECLARATION_LEGACY_ALSO_VISIBLE = (
    "an older set on this computer is still visible."
)


def backend_declaration_line(discovery, env_mode: str = "unknown") -> str:
    """ONE sentence, in the customer's words: where their scheduled chats run.

    Keyed on what `select_backend` FOUND, never on `env_mode` — the same rule
    `select_backend` itself keeps (a backend is chosen from the visible tool
    list, never from an environment marker). `env_mode` is taken so the four
    skills hand this helper and `no_scheduler_cause` the same pair; it changes
    no sentence. Cloud → the cloud sentence (joined to the older-set clause by
    a semicolon when the old scheduler is ALSO visible); legacy → the
    this-computer sentence; none → `""` (the cause line speaks instead).

    WHETHER to say it is not this function's question: `opening_lines` says it
    only when the availability guard answered `available: True` (H-1).
    """
    backend_id = getattr(discovery, "backend_id", None)
    if backend_id is None and isinstance(discovery, dict):
        backend_id = discovery.get("backend_id")
    if backend_id == "cloud":
        also = getattr(discovery, "legacy_visible", None)
        if also is None and isinstance(discovery, dict):
            also = discovery.get("legacy_visible")
        if also:
            return (DECLARATION_CLOUD[:-1] if DECLARATION_CLOUD.endswith(".")
                    else DECLARATION_CLOUD) + "; " + DECLARATION_LEGACY_ALSO_VISIBLE
        return DECLARATION_CLOUD
    if backend_id == "legacy":
        return DECLARATION_LEGACY
    return ""


# --- S-3: the plain-English cause line when there is no scheduler -----------

CAUSE_STUBS_NOT_LOADED = "stubs_not_loaded"
CAUSE_NOT_INITIALIZED = "not_initialized"
CAUSE_CLOUD_SESSION_NO_FOLDER = "cloud_session_no_folder"
CAUSE_FEATURE_OFF = "feature_off"
CAUSE_NONE_VISIBLE = "none_visible"

#: The causes in PRECEDENCE order — the first that holds wins.
CAUSE_ORDER = (
    CAUSE_STUBS_NOT_LOADED,
    CAUSE_NOT_INITIALIZED,
    CAUSE_CLOUD_SESSION_NO_FOLDER,
    CAUSE_FEATURE_OFF,
    CAUSE_NONE_VISIBLE,
)

#: The app's own words for two of the causes (memory 2026-09-08: read from the
#: desktop app bundle). Matched case-insensitively inside the error text.
NOT_INITIALIZED_MARKERS = ("not initialized", "scheduled tasks are not initialized")
FEATURE_OFF_MARKERS = ("feature is disabled",)

#: The seats where tools arrive as deferred stubs and must be loaded first.
_STUB_LOADING_MODES = ("claude_code_local", "merged_cloud")

CAUSE_LINES = {
    # R-SD-4 (M-1): the sentence promises exactly what the step then does —
    # step 4 refreshes the host's connector tools once and looks again.
    CAUSE_STUBS_NOT_LOADED: (
        "I could not see your scheduler's tools yet; loading them now."
    ),
    CAUSE_NOT_INITIALIZED: (
        "Scheduling is not switched on in this app yet; sign in to the app once "
        "and try again."
    ),
    CAUSE_CLOUD_SESSION_NO_FOLDER: (
        "This chat has no workspace folder attached, so it cannot see your "
        "scheduled chats; open a chat with your Command Room folder attached."
    ),
    CAUSE_FEATURE_OFF: (
        "Scheduled chats are turned off in this app's settings, so nothing can be "
        "scheduled from here; every chat still runs when you ask for it by name."
    ),
    # Byte-equal to the shipped sentence: no reader of it moves (spec S-3 (a)).
    CAUSE_NONE_VISIBLE: NO_SCHEDULER_LINE,
}


def _is_deferred_stub(entry) -> bool:
    """True when a tool-list entry is a name the host has not loaded yet."""
    if isinstance(entry, dict):
        return bool(entry.get(DEFERRED_STUB_KEY))
    if isinstance(entry, str):
        return False
    return bool(getattr(entry, DEFERRED_STUB_KEY, False))


def _scheduler_visible(tools) -> bool:
    """Is either scheduler's create tool on the LOADED list?"""
    ids = _tool_ids(tools)
    return CLOUD_TOOL_MAP["create"] in ids or LEGACY_TOOL_MAP["create"] in ids


def no_scheduler_cause(tools, *, env_mode: str, signals: Optional[dict] = None,
                       error_text: Optional[str] = None) -> dict:
    """`{"cause", "line"}` — why this chat has no scheduler, in one sentence.

    Precedence (the first that holds wins):
      1. `stubs_not_loaded` — the list carries a deferred-stub entry, OR no
         scheduler is on it while `env_mode` is a seat that serves deferred
         stubs and the discovery step has not run (`signals["discovery_ran"]`)
         — and the step has NOT already reloaded once (`signals["reloaded"]`):
         after the one reload the answer is final, so a name still waiting
         falls through to the causes below rather than promising another load;
      2. `not_initialized` — the scheduler's own error says it is not
         initialized;
      3. `cloud_session_no_folder` — a merged cloud session with no workspace
         folder attached (`signals["folder_attached"] is False`);
      4. `feature_off` — the error says the feature is disabled;
      5. `none_visible` — `NO_SCHEDULER_LINE`, unchanged.
    Pure: reads its arguments, calls nothing, writes nothing.
    """
    signals = signals if isinstance(signals, dict) else {}
    entries = list(tools or [])
    err = (error_text or "").lower() if isinstance(error_text, str) else ""

    cause = CAUSE_NONE_VISIBLE
    if not signals.get("reloaded") and (
        any(_is_deferred_stub(e) for e in entries) or (
            env_mode in _STUB_LOADING_MODES
            and not signals.get("discovery_ran")
            and not _scheduler_visible(entries))):
        cause = CAUSE_STUBS_NOT_LOADED
    elif any(m in err for m in NOT_INITIALIZED_MARKERS):
        cause = CAUSE_NOT_INITIALIZED
    elif env_mode == "merged_cloud" and signals.get("folder_attached") is False:
        cause = CAUSE_CLOUD_SESSION_NO_FOLDER
    elif any(m in err for m in FEATURE_OFF_MARKERS):
        cause = CAUSE_FEATURE_OFF
    return {"cause": cause, "line": CAUSE_LINES[cause]}


# --- M-2: the opening line, one rendered site --------------------------------

#: The availability guard's two refusal reasons, spelled as
#: `schedule_config.REASON_NO_SCHEDULER` / `REASON_REGISTRATION_BLOCKED` spell
#: them (this module does not import that one; a pin holds them equal).
AVAILABILITY_NO_SCHEDULER = "scheduler_unavailable"
AVAILABILITY_BLOCKED = "registration_blocked"


def opening_lines(tools, env_mode: str = "unknown", *, availability,
                  folder_attached: Optional[bool] = None, reloaded: bool = False,
                  error_text: Optional[str] = None) -> list:
    """The ONE line a scheduling skill opens with — as a list of 0 or 1 lines.

    The rendered site of the discovery step (review M-2): every carrier pastes
    ONE call to this helper and says what it returns, verbatim. Three answers:

      * the guard answered `available: True` → the declaration of where the
        chats run (`backend_declaration_line` over what `select_backend` finds
        on the LOADED names) — or, when the scheduler's own call failed and
        `error_text` carries its words, the cause line for that error instead;
      * the guard answered `available: False` because there is NO scheduler
        (`scheduler_unavailable`) → the cause line (`no_scheduler_cause`), which
        replaces the no-scheduler sentence;
      * anything else — the guard refused a scheduler it DID find
        (`registration_blocked`, M's seat), or no guard answer at all → `[]`.
        The guard's own refusal sentence stands alone there (H-1; COPY1: "the
        refusal sentence is the only thing this skill may say on that seat").

    `tools` is step 2's list: the loaded names, plus any scheduler name still
    waiting as `{"name": <id>, "deferred": True}` — a waiting name is never a
    scheduler (R-SD-4) and only ever feeds the "not loaded" cause.
    `discovery_ran` is True by construction: the call form lives inside the
    step, after its load. `reloaded` is True on the step's one reload, after
    which the answer is final. An empty string is never emitted.
    Pure: reads its arguments, calls nothing, writes nothing.
    """
    avail = availability if isinstance(availability, dict) else {}
    entries = list(tools or [])
    signals = {"discovery_ran": True, "folder_attached": folder_attached,
               "reloaded": bool(reloaded)}
    err = error_text if isinstance(error_text, str) and error_text.strip() else None

    if avail.get("available") is True:
        if err is not None:
            loaded = [e for e in entries if not _is_deferred_stub(e)]
            line = no_scheduler_cause(loaded, env_mode=env_mode, signals=signals,
                                      error_text=err)["line"]
        else:
            _backend, found = select_backend(entries)
            line = backend_declaration_line(found, env_mode)
    elif avail.get("available") is False and avail.get("reason") == AVAILABILITY_NO_SCHEDULER:
        line = no_scheduler_cause(entries, env_mode=env_mode, signals=signals,
                                  error_text=err)["line"]
    else:
        line = ""
    return [line] if isinstance(line, str) and line.strip() else []


__all__ = [
    "BODIES_NOT_COMPARED_NOTE",
    "BRIDGE_CHATS_LINE",
    "BRIDGE_CHAT_LINE_ONE",
    "bridge_chats_line",
    "bridge_named",
    "composer_reachable",
    "BRIDGE_SCHEDULES_ALTERNATIVE",
    "BRIDGE_SCHEDULES_INVITE",
    "FIRST_REGISTRATION_ALTERNATIVE",
    "FIRST_REGISTRATION_INVITE",
    "NOT_RUNNING_YET",
    "bridge_plan",
    "bridge_schedules_line",
    "record_trigger_row",
    "trigger_row_payload",
    "TRIGGER_ROW_WRITER",
    "record_registration",
    "registration_payload",
    "REGISTRATION_WRITER",
    "SCHEDULE_CREATED_EVENT",
    "compose_registration_body",
    "plan_registration",
    "registration_summary_lines",
    "scheduled_chat_lines",
    "scheduled_chat_rows",
    "CLOUD_MIN_GAP_MINUTES",
    "CR_ONE_SHOT_TASK_IDS",
    "CR_PROBE_PREFIX",
    "CRON_DOM_MIDNIGHT_REFUSAL",
    "CRON_MIN_GAP_REFUSAL",
    "CRON_SPLIT_REFUSAL",
    "DELETE_REFUSED_LINE",
    "HEADER_LINE_FORMAT",
    "HEADER_LINE_RE",
    "Planned",
    "current_utc_offset_minutes",
    "header_line",
    "is_deletable",
    "local_cron_to_utc",
    "min_gap_minutes",
    "parse_header_line",
    "read_trigger_map",
    "record_trigger_map",
    "trigger_map_row",
    "trigger_name",
    "utc_cron_to_local",
    "ABORT_ENV_UNKNOWN",
    "ABORT_ORCHESTRATOR_NOT_FOUND",
    "ABORT_PLUGIN_NOT_FOUND",
    "ABORT_TEXTS",
    "ABORT_WORKSPACE_NOT_FOUND",
    "BackendDiscovery",
    "CHANGE_REFUSED_LINE",
    "CLOUD_TOOL_MAP",
    "CLOUD_TOOL_PREFIX",
    "CONFIG_REL",
    "CallPlan",
    "CloudTriggersBackend",
    "FOLDER_WAITING_LINE",
    "GATE_LINE",
    "LEGACY_TOOL_MAP",
    "LEGACY_TOOL_PREFIX",
    "LegacyBackend",
    "NO_SCHEDULER_LINE",
    "ROW_NOT_RUNNING",
    "RUNTIME_MISSING_LINE",
    "Refusal",
    "ScheduleBackend",
    "TaskRecord",
    "load_backend_config",
    "plan_realign",
    "registration_gate",
    "runtime_matches_plugin",
    "select_backend",
    "CAUSE_CLOUD_SESSION_NO_FOLDER",
    "CAUSE_FEATURE_OFF",
    "CAUSE_LINES",
    "CAUSE_NONE_VISIBLE",
    "CAUSE_NOT_INITIALIZED",
    "CAUSE_ORDER",
    "CAUSE_STUBS_NOT_LOADED",
    "DECLARATION_CLOUD",
    "DECLARATION_LEGACY",
    "DECLARATION_LEGACY_ALSO_VISIBLE",
    "DEFERRED_STUB_KEY",
    "DISCOVERY_BEGIN",
    "DISCOVERY_END",
    "DISCOVERY_FENCE_LANG",
    "DISCOVERY_MAX_LINES",
    "DISCOVERY_MAX_RESULTS",
    "DISCOVERY_QUERIES",
    "backend_declaration_line",
    "discovery_queries",
    "discovery_step",
    "no_scheduler_cause",
    "AVAILABILITY_BLOCKED",
    "AVAILABILITY_NO_SCHEDULER",
    "OPENING_CALL_ARGS",
    "OPENING_LINES_HELPER",
    "opening_call_json",
    "opening_lines",
]
