"""
Which clock is the customer's clock — the ONE resolver every scheduling,
lateness, receipt and diagnostic stamp reads (SPEC_NIGHTM2 §6, TZ1).

WHY THIS MODULE EXISTS
----------------------
R8 (`references/HOW_COMMAND_ROOM_WORKS.md`, settled 2026-07-01) says cron,
lateness and fired-recency math run on MACHINE-local time. That rule was
written for a world with a machine: one desktop, one clock, the customer
sitting in front of it. On a merged cloud seat there is no such machine.
A scheduled fire is a container whose clock read PDT, talking to a sandbox
VM whose clock is UTC, running a cron the platform evaluates in UTC, for a
customer who may be in any of the three zones or none of them. The container
matched M's own workspace on 2026-09-19 by coincidence — the exact
"agreement is not correctness" trap `shared/RELIABILITY.md` names.

So the question "which clock" now has two answers, and this module is the
only place that picks between them:

  * **Cloud / merged seats** — the WORKSPACE zone
    (`entities.json` `workspace.user_timezone`, the same value the access
    layer exports as `TZ` into every helper process —
    `workspace_access._helper_child_env`, which is the one producer of that
    variable in this tree and is why a helper running in the sandbox VM,
    beside a workspace it can open, still lands on the customer's clock).
    Chosen when the environment reports `host_clock_trust == "untrusted"`,
    or when this workspace's schedules live in the account-level trigger
    registry (a non-empty `triggers` map — the positive evidence that
    registration happened the merged way).
  * **Legacy seats** — today's machine-local clock, byte for byte
    (`datetime.now().astimezone()`), because on a desktop Cowork seat the
    machine clock IS the clock cron evaluates in and R8 still holds.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not correct WHICH INSTANT it is — that is `trusted_now`'s job, and
this module calls it so the CLOCK1 corroboration survives. It only decides
WHICH ZONE an instant is expressed in. It never converts a value that is
already naive: a naive datetime reaching `to_user_local_naive` is assumed to
be in the seat's user-local clock already, which is the invariant
`late_fire` and `task_watchdog` maintain end to end.

DIAGNOSTIC STAMPS are a separate, simpler rule: a lock sidecar or a stale-
lock payload is read by a human and by `_read_lock_payload().timestamp()`,
possibly from a different process in a different zone. Those never carry a
naive local wall clock — `diagnostic_stamp()` is UTC-aware, always, on every
seat. `diagnostic_stamp` deliberately imports nothing from this tree so the
lowest-level writers can call it without an import cycle.

3.10-safe: `zoneinfo` only, no `datetime.UTC`, no `StrEnum`.
"""

from __future__ import annotations

import datetime as _dt
import os
from typing import Any, Optional

#: The two answers to "which clock". Returned by `clock_source()` and safe to
#: put on a receipt or in a diagnostic line.
CLOCK_WORKSPACE_ZONE = "workspace_zone"
CLOCK_MACHINE_LOCAL = "machine_local"

#: A future hook, read but NOT PRODUCED BY ANYTHING IN THIS TREE (TZ1 fix
#: round 1, review M-2 — the first draft's docstring claimed the access layer
#: exported it, and it does not: `workspace_access._helper_child_env` exports
#: `CR_WORKSPACE`, `PYTHONDONTWRITEBYTECODE`, `TZ`, `CR_RUNTIME_VERSION`,
#: `CR_HOST_MODE` and `CR_ENV`). Kept because a zone name is the one thing a
#: container can hand a helper that `TZ` also has to mean to the C library,
#: and a separate name costs nothing until someone needs it. Tried FIRST so
#: that, if a producer ever appears, it wins — but `TZ` below is what is
#: actually read today, and the pin says so.
WORKSPACE_TZ_ENV = "CR_WORKSPACE_TZ"

#: The plain POSIX `TZ` the access layer really does export
#: (`workspace_access._helper_child_env`, from the resolved workspace's own
#: `user_timezone`). THIS is the producer behind "the container's case": a
#: helper in the sandbox VM that cannot open `entities.json` still gets the
#: zone by name here. Python honours `TZ` for `astimezone()` on POSIX only,
#: never on Windows, so it is read by NAME rather than trusted to move the
#: host clock.
POSIX_TZ_ENV = "TZ"

#: The one helper this module asks the ACCESS LAYER to run on the machine
#: that holds the workspace, when the workspace is not on this filesystem.
#: Spelled once here so the skill body, this suite and
#: `workspace_access.RUN_HELPER_ALLOWLIST` cannot drift apart.
HELPER_READ_TRIGGER_MAP = "schedule_backend:read_trigger_map"


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

def _env_map(env=None) -> dict:
    try:
        return dict(os.environ) if env is None else dict(env)
    except (TypeError, ValueError):
        return {}


def _env_says_untrusted(env=None) -> bool:
    """True when the access layer itself declared the host clock untrusted.

    `CR_CLOCK_TRUST` is exported by `env_detect --shell`, which only runs
    inside a resolved environment — so an explicit `untrusted` here is a
    POSITIVE reading, not an absence of evidence.
    """
    environ = _env_map(env)
    return str(environ.get("CR_CLOCK_TRUST") or "").strip() == "untrusted"


def _detected_untrusted(env=None) -> bool:
    """True when `env_detect` POSITIVELY reads a merged/cloud environment.

    AN `unknown` MODE IS NOT CLOUD. `env_detect` derives `host_clock_trust`
    from `local_workspace_fs`, which is false for `unknown` as well as for
    `merged_cloud` — so a bare process with no signals at all (every test
    runner on a build box, and any stripped install) reports `untrusted`
    while being nothing of the kind. Taking that as evidence would move the
    clock on seats nobody has diagnosed, which is the opposite of R8's
    conservatism and of ruling §0.34 ("readers proceed, writers refuse" —
    never "guess"). Positive evidence only: a decided, non-`unknown` mode
    whose filesystem is not local.
    """
    environ = _env_map(env)
    try:
        import env_detect

        report = env_detect.detect(env=environ)
    except Exception:  # noqa: BLE001 — a clock never fails on a detector
        return False
    if report.mode == "unknown":
        return False
    return report.host_clock_trust == "untrusted"


def _resolve_workspace(workspace_root=None, env=None):
    """The workspace a caller means: the one it passed, else `CR_WORKSPACE`.

    Most clock reads on the fire path have no workspace in scope — a
    `_to_local_naive(value)` deep inside the watchdog is two frames from
    anything that knows where the substrate is. The access layer exports
    `CR_WORKSPACE` into every helper process precisely so those callers can
    still resolve it, and `tz.py` has read it that way since CLOCK1. Reading
    it here is what makes "the seat's clock" the same answer at EVERY call
    site instead of only the ones that happened to thread a path through.
    """
    if workspace_root:
        return workspace_root
    environ = _env_map(env)
    value = str(environ.get("CR_WORKSPACE") or "").strip()
    return value or None


def _backend_is_cloud(workspace_root=None, env=None) -> bool:
    """True when this workspace's schedules live in the trigger registry.

    Reads the stored `triggers` map the same way `task_watchdog.seat_backend`
    does, but through `schedule_backend` so this module never imports a
    surface. An unreadable config reads legacy — the conservative answer.
    """
    workspace_root = _resolve_workspace(workspace_root, env)
    if not workspace_root:
        return False
    try:
        import schedule_backend

        rows = schedule_backend.read_trigger_map(workspace_root)
        return bool(isinstance(rows, dict) and rows)
    except Exception:  # noqa: BLE001
        return False


def trigger_map_reading(workspace_root=None, env=None) -> dict:
    """`{"ok": bool, "rows": dict, "via": str|None, "reason": str|None}` —
    the trigger map AND whether this process actually got to read it.

    WHY THE PAIR AND NOT JUST THE MAP (TZ1 fix round 1, review M-6).
    `schedule_backend.read_trigger_map` answers `{}` for two different
    worlds and says nothing about which: a legacy desktop seat that never
    registered anything, and a merged container whose workspace is on
    ANOTHER MACHINE and cannot be opened at all. `clock_source` may take
    `{}` as "legacy" and be right either way — the machine clock is the
    conservative answer and a wrong clock is the harm it is avoiding. A
    CUSTOMER SENTENCE may not. "Your scheduled chats keep firing at their
    current clock times" is true on the first seat and false on the second,
    and a reader has no way to tell they were told the wrong one.

    So: read it in process when the workspace is on this filesystem (the
    legacy seat, byte for byte what the lane shipped — no new subprocess, no
    access layer, nothing to go wrong), and only when it is NOT reachable
    ask the access layer to run the same helper on the machine that holds
    the folder (`run_helper`, allow-list entry
    `schedule_backend:read_trigger_map`). If neither works, say `ok: False`
    and let the surface say so out loud.

    `ok: True` with empty `rows` is a real answer: this seat has no
    registrations. `ok: False` is the absence of an answer, never a seat
    shape. Nothing here raises and nothing here writes.

    A THIRD SHAPE THE FIRST ROUND COLLAPSED (MF-M2-17). A config file that is
    PRESENT and will not parse used to come back `ok: True, rows: {}`, which
    is the "never registered anything" answer, which the surface says as
    "your scheduled chats keep firing at their current clock times" — a
    sentence about a file nobody read. `schedule_backend.read_trigger_map`
    swallows the parse error by design (a reader wants a map, not an
    exception), so the distinction has to be drawn HERE, where the caller is
    asking whether it got to read at all. An unreadable config is `ok: False`
    with `reason: config_unreadable`, and the surface says it could not read
    the schedule from here.

    THE `via: "access_layer"` SUCCESS BRANCH IS RESERVED (MF-M2-18, the night
    M2 coordinator default). `run_helper` runs the helper on THIS host, so on
    every host shape the fleet has today that branch is only reached after
    the in-process read already failed — and where the in-process read fails,
    the helper reads the same unreachable folder. It is KEPT, with its
    allow-list row, against the host shape where the layer runs a helper
    somewhere else; M3 decides whether that shape arrives or the branch goes.
    Nothing reads `via` to make a decision, so a reserved branch costs a
    reader nothing, and the row itself is clean under the transitive
    write-scan.
    """
    root = _resolve_workspace(workspace_root, env)
    if not root:
        return {"ok": False, "rows": {}, "via": None,
                "reason": "no_workspace_on_this_host"}
    reachable = False
    try:
        from pathlib import Path as _Path

        base = _Path(root)
        config = base / "_hq" / "workspace_config.json"
        if config.exists():
            # MF-M2-17: present is not the same as readable.
            import json as _json

            try:
                parsed = _json.loads(config.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                return {"ok": False, "rows": {}, "via": None,
                        "reason": "config_unreadable"}
            if not isinstance(parsed, dict):
                return {"ok": False, "rows": {}, "via": None,
                        "reason": "config_unreadable"}
            reachable = True
        elif base.is_dir():
            # The folder is here and the config is not: a seat that has
            # simply never registered. An answer, not a failure.
            return {"ok": True, "rows": {}, "via": "in_process",
                    "reason": None}
    except OSError:
        reachable = False
    if reachable:
        try:
            import schedule_backend

            rows = schedule_backend.read_trigger_map(root)
            if isinstance(rows, dict):
                return {"ok": True, "rows": dict(rows), "via": "in_process",
                        "reason": None}
        except Exception:  # noqa: BLE001 — fall through to the layer
            pass
    try:
        import workspace_access

        envelope = workspace_access.run_helper(
            HELPER_READ_TRIGGER_MAP, {"workspace_root": str(root)},
            env=None if env is None else dict(env))
    except Exception:  # noqa: BLE001 — no layer on this seat
        return {"ok": False, "rows": {}, "via": None,
                "reason": "access_layer_unavailable"}
    if isinstance(envelope, dict) and envelope.get("ok"):
        rows = envelope.get("result")
        return {"ok": True, "rows": dict(rows) if isinstance(rows, dict) else {},
                "via": "access_layer", "reason": None}
    reason = "helper_failed"
    if isinstance(envelope, dict) and envelope.get("reason"):
        reason = str(envelope["reason"])
    return {"ok": False, "rows": {}, "via": None, "reason": reason}


def clock_source(workspace_root=None, env=None) -> str:
    """`"workspace_zone"` on a cloud/merged seat, `"machine_local"` on legacy.

    Any ONE piece of positive evidence is enough: the layer's own
    `CR_CLOCK_TRUST=untrusted`, a decided merged/cloud environment, or a
    workspace whose schedules live in the account-level trigger registry. A
    legacy desktop seat has none of the three and keeps R8 byte for byte —
    and so does an `unknown` seat, which is an absence of evidence, not
    evidence of the cloud.
    """
    if _env_says_untrusted(env):
        return CLOCK_WORKSPACE_ZONE
    if _detected_untrusted(env):
        return CLOCK_WORKSPACE_ZONE
    if _backend_is_cloud(workspace_root, env):
        return CLOCK_WORKSPACE_ZONE
    return CLOCK_MACHINE_LOCAL


# ---------------------------------------------------------------------------
# The zone
# ---------------------------------------------------------------------------

def _zone_from_name(name) -> Optional[_dt.tzinfo]:
    text = str(name or "").strip()
    if not text:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(text)
    except Exception:  # noqa: BLE001 — an unresolvable name is not a crash
        return None


#: Every spelling of "the workspace is on UTC ON PURPOSE". A configured name
#: in this set is an answer, not a fallback, so the degrade below leaves it
#: alone even on a machine with no tz database at all.
UTC_NAMES = ("UTC", "Etc/UTC", "Etc/Universal", "Universal", "Zulu",
             "Etc/Zulu", "Etc/GMT", "GMT", "Etc/GMT+0", "Etc/GMT-0", "GMT0")


def _is_utc_name(name) -> bool:
    return str(name or "").strip().upper() in tuple(n.upper() for n in UTC_NAMES)


def _is_utc_zone(zone) -> bool:
    """True for a tzinfo that IS UTC, however it was constructed.

    `ZoneInfo("UTC")` carries `key`; `datetime.timezone.utc` — what
    `tz._utc_fallback` returns on a machine with no tz database — carries
    none, and prints as `UTC`. Both are the fallback's answer.
    """
    if zone is None:
        return False
    return _is_utc_name(getattr(zone, "key", None) or str(zone))


def _configured_zone_name(workspace_root=None) -> str:
    """The zone NAME `entities.json` carries, or `""` when it carries none.

    Read here rather than inferred from what `tz.load_workspace_tz` RETURNS,
    because that function answers UTC to two different questions — "this
    workspace is on UTC" and "the name in entities.json will not resolve on
    this machine" (its tz-database branch) — and only one of those is a
    resolved clock (review F-2). tz.py's own workspace resolution is reused so
    the two readers can never disagree about WHICH entities.json is the
    workspace's; its merge rule (top-level block wins, TZFIX v5.9.4) is
    mirrored for the same reason.
    """
    try:
        import json
        import tz as _tz

        root = _tz._resolve_workspace_path(workspace_root)
        if root is None:
            return ""
        with (root / _tz._ENTITIES_REL).open("r", encoding="utf-8") as handle:
            entities = json.load(handle)
    except Exception:  # noqa: BLE001 — unreadable is not configured
        return ""
    if not isinstance(entities, dict):
        return ""
    inner = entities.get("entities")
    inner_ws = inner.get("workspace") if isinstance(inner, dict) else None
    top_ws = entities.get("workspace")
    merged = {**(inner_ws if isinstance(inner_ws, dict) else {}),
              **(top_ws if isinstance(top_ws, dict) else {})}
    return str(merged.get("user_timezone") or "").strip()


def workspace_zone(workspace_root=None, env=None) -> Optional[_dt.tzinfo]:
    """The customer's own zone, or None when it cannot be resolved.

    `entities.json` first (`tz.load_workspace_tz`, which already honours the
    `CR_WORKSPACE` env var when no root is passed — the way a helper process
    inside the sandbox VM finds the workspace), then the zone NAME in the
    environment — `CR_WORKSPACE_TZ` if anything ever sets it, then `TZ`,
    which the access layer really does export — then nothing. None means
    "no opinion": the caller falls back to the host zone rather than guessing.

    A MISTYPED zone name is "cannot be resolved", not "UTC" (R-TZ1-2, review
    F-2). `tz.load_workspace_tz` ends its retry on an unresolvable name by
    falling back to UTC — the right call for ITS callers, whose job is to
    render a date rather than to say which clock ran — and that UTC arrived
    here looking exactly like a resolved zone, so `clock_fields` reported
    `{"clock": "workspace_zone", "zone": "UTC"}` for a zone nobody
    configured, and every receipt and all three lock stamps carried it. The
    configured NAME decides instead: tz.py is still asked first, so its retry
    still covers a first-construction transient, but a UTC answer to a
    non-UTC name that will not resolve here is read as the degrade it is and
    returns None — which is what makes `clock_fields` say `host_clock`.
    tz.py itself is untouched: every legacy caller of it gets the same bytes.
    """
    try:
        import tz as _tz

        zone = _tz.load_workspace_tz(workspace_path=workspace_root)
        if zone is not None:
            name = _configured_zone_name(workspace_root)
            if (name and not _is_utc_name(name) and _is_utc_zone(zone)
                    and _zone_from_name(name) is None):
                return None
            return zone
    except Exception:  # noqa: BLE001 — TZResolutionError and anything else
        pass
    environ = _env_map(env)
    for key in (WORKSPACE_TZ_ENV, POSIX_TZ_ENV):
        zone = _zone_from_name(environ.get(key))
        if zone is not None:
            return zone
    return None


def user_local_zone(workspace_root=None, env=None) -> Optional[_dt.tzinfo]:
    """The zone every user-facing clock read is expressed in on THIS seat.

    `None` means "the host zone" — which is what a legacy seat wants and what
    every caller already did before this module existed. A cloud seat whose
    workspace zone cannot be resolved also degrades to None: a wrong-zone
    answer is bad, a crash in the middle of a fire is worse, and the degrade
    is the same one `tz.local_header_date` has always taken.

    THE DEGRADE IS DETECTABLE, DELIBERATELY. `clock_source()` still says
    `workspace_zone` while this returns `None`, and that pair — "this seat
    should be on the workspace clock and I cannot find out what it is" — is
    the one state a surface may want to say out loud (ruling §0.34's banner
    class). Nothing in this module narrates it; it is left legible so the
    surface that owns the sentence can.
    """
    if clock_source(workspace_root, env) == CLOCK_MACHINE_LOCAL:
        return None
    return workspace_zone(workspace_root, env)


# ---------------------------------------------------------------------------
# The clock reads
# ---------------------------------------------------------------------------

def user_local_now(workspace_root=None, env=None, *, env_date=None) -> _dt.datetime:
    """Naive "now" in the seat's user-local clock. The drop-in for
    `datetime.now()` in every scheduling, lateness and slot computation.

    The INSTANT still comes through `trusted_now` (CLOCK1's corroboration
    against the workspace ledger), so a sandbox whose clock never synced is
    still corrected; only the ZONE it is expressed in is decided here.
    """
    zone = user_local_zone(workspace_root, env)
    try:
        from trusted_now import trusted_now_local_naive

        return trusted_now_local_naive(workspace_root, env_date=env_date,
                                       machine_zone=zone)
    except Exception:  # noqa: BLE001 — a clock read never raises
        if zone is None:
            return _dt.datetime.now()
        return _dt.datetime.now(_dt.timezone.utc).astimezone(zone).replace(tzinfo=None)


def to_user_local_naive(value: Optional[_dt.datetime], workspace_root=None,
                        env=None) -> Optional[_dt.datetime]:
    """Aware → naive in the seat's user-local clock; naive passes through.

    A naive value reaching here is ALREADY in that clock by the invariant the
    scheduling helpers maintain (`late_fire`'s module header). Re-interpreting
    it would be the LATETZ defect in reverse.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    zone = user_local_zone(workspace_root, env)
    if zone is None:
        return value.astimezone().replace(tzinfo=None)
    return value.astimezone(zone).replace(tzinfo=None)


def diagnostic_stamp(now: Optional[_dt.datetime] = None) -> str:
    """A UTC-AWARE ISO stamp for a lock sidecar or a stale-lock payload.

    Never naive, never local, on any seat. Three writers used
    `datetime.now().isoformat()`: a container reading PDT beside a VM writing
    UTC left two stamps seven hours apart with nothing on either to say so,
    and `atomic_write._read_lock_payload` turned the naive string into an
    epoch through the READER's zone — so a stale-lock window was computed
    against a clock the writer never used.

    Imports nothing from this tree on purpose: `writer_lock` and
    `atomic_write` sit below everything else and must be able to call this
    without an import cycle.
    """
    value = now or _dt.datetime.now(_dt.timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=_dt.timezone.utc)
    return value.astimezone(_dt.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Provenance — what a receipt or a health line may say about the clock
# ---------------------------------------------------------------------------

#: `clock_fields()` says this when the seat is positively cloud but its
#: workspace zone could not be resolved, so the math ran on the container's
#: clock after all. It is a THIRD value, not one of the two decisions: the
#: decision was `workspace_zone` and the outcome was the host's.
CLOCK_HOST_FALLBACK = "host_clock"


def clock_fields(workspace_root=None, env=None) -> dict:
    """`{"clock": "<what the math actually ran on>", "zone": "<name>"|None}`
    — additive provenance for a receipt's `data`.

    REPORTS THE OUTCOME, NOT THE INTENT (TZ1 fix round 1, review M-3). The
    first spelling returned `clock_source()` straight through, and
    `clock_source` answers the DECISION: on positive cloud evidence it says
    `workspace_zone` whether or not the zone could be found. But
    `user_local_zone` independently returns `None` when the zone will not
    resolve, and `user_local_now` then falls back to the host clock — so in
    that one state a receipt asserted a provenance the run did not have. A
    provenance field that can be wrong is worse than no provenance field:
    it is the "agreement is not correctness" trap this whole lane is about,
    told by the very verb a health line would quote.

    Three outcomes, and the pair is always legible:
      * `{"clock": "workspace_zone", "zone": "Asia/Tokyo"}` — cloud seat,
        zone resolved, the math ran there.
      * `{"clock": "host_clock", "zone": None}` — cloud seat, the zone would
        not resolve, the math ran on the container's clock. This is the state
        ruling §0.34's banner attaches to (R-TZ1-2).
      * `{"clock": "machine_local", "zone": None}` — legacy seat, R8, and
        the machine clock is the right answer there.

    Never a customer sentence: this is ledger vocabulary, the same posture as
    `receipts.machine_fields`.
    """
    source = clock_source(workspace_root, env)
    if source == CLOCK_MACHINE_LOCAL:
        return {"clock": CLOCK_MACHINE_LOCAL, "zone": None}
    zone = workspace_zone(workspace_root, env)
    if zone is None:
        return {"clock": CLOCK_HOST_FALLBACK, "zone": None}
    return {"clock": CLOCK_WORKSPACE_ZONE, "zone": str(zone)}


__all__ = [
    "CLOCK_HOST_FALLBACK",
    "CLOCK_MACHINE_LOCAL",
    "CLOCK_WORKSPACE_ZONE",
    "HELPER_READ_TRIGGER_MAP",
    "POSIX_TZ_ENV",
    "WORKSPACE_TZ_ENV",
    "clock_fields",
    "clock_source",
    "diagnostic_stamp",
    "to_user_local_naive",
    "trigger_map_reading",
    "user_local_now",
    "user_local_zone",
    "workspace_zone",
]


def _cli(argv: Optional[list] = None) -> int:
    """`python3 shared/scripts/clock_policy.py [--workspace PATH]` — say which
    clock this seat is on and what time it is there. Read-only."""
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    zone = user_local_zone(args.workspace)
    out = {
        "clock": clock_source(args.workspace),
        "zone": str(zone) if zone is not None else None,
        "now": user_local_now(args.workspace).isoformat(timespec="seconds"),
        "diagnostic_stamp": diagnostic_stamp(),
    }
    if args.json:
        print(_json.dumps(out))
    else:
        for key in ("clock", "zone", "now"):
            print("{}: {}".format(key, out[key]))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(_cli())
