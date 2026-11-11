#!/usr/bin/env python3
"""The update's one line about scheduled chats, and the record that clears it (cr1#970).

THE RULING (the owner, 2026-11-09). Scheduled chats are created, changed,
switched off or removed by exactly two skills: `enable-command-room-schedules`
(*set up command room schedules*) and `cockpit-setup` (*set up my hub* /
*rebuild my hub*). The update bridge touches none of them. An update and a Hub
rebuild started a minute apart had both rewritten the same four Hub tasks, two
writers on one task set, which is how a task gets lost, doubled or switched
off under the other chat's feet.

So the schedule work that ships with an update (the shelved chats' removal,
the retirement offers, the readiness retirement, the fold, the pauses a
release item asks for) lives in
`skills/enable-command-room-schedules/references/schedule-changes.md`, and
both skills run it every time they run. It ends by writing ONE
`schedule_changes_applied` row naming the installed version. The update reads
that row back and says at most one line while the work is still waiting.

The functions:

  pending_line(workspace_root, plugin_version=None, tools=None)
      A HELPER (read door): a pure read of the activity log. The one sentence
      only when schedule work is pending (`schedule_work_pending`: a version
      since the seat's last applied schedule work ships a schedule item, or
      schedule setup never ran here), and "" on a seat that cannot set
      schedules up at all. It never reads the scheduler and writes nothing.

  records_after_removals(records, removals)
      Pure: the listing with every chat the removal step just removed (or
      tried to) taken out, so nothing later in the reference pauses, folds
      or offers to switch off a chat that was just deleted.

  owner_line(plan, removed, failed), may_record_applied(plan, ...),
  schedule_fingerprint(), schedule_work_pending(...)
      Pure: the reference's line from the deletes that ran, its marker gate,
      the digest the marker carries, and the pending rule `pending_line`
      reads.

  log_schedule_changes_applied(workspace_root, plugin_version=None,
                               source_skill=...)
      A WRITER (write door): one row per installed version, written only by
      the two skills that own the scheduled chats, at the end of the
      reference. Idempotent: the same version twice writes one row.

`plugin_version` defaults to the version in the `plugin.json` shipped beside
this module, which is the installed version wherever the module runs (the
runtime installed beside the data carries the same file).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

#: The event type the reference writes last, and the only one this module reads.
EVENT_TYPE = "schedule_changes_applied"

#: The two skills that own the scheduled chats (the ruling). The writer refuses
#: every other caller, the update bridge included.
OWNER_SKILLS = ("enable-command-room-schedules", "cockpit-setup")

#: The release-manifest items the update bridge never runs (cr1#970), each
#: with the version whose manifest carries it. Two act on scheduled chats
#: (the fold and the readiness item) and run in the schedule-changes
#: reference with a listing of its own; every other one tells the owner to
#: say `set up command room schedules`, which only `pending_line` may say
#: during an update. The rule that finds them (an `auto_apply` item whose
#: action acts on scheduled chats, or any item whose text names the setup
#: phrase) is re-derived from the shipped manifests by
#: `run_cr970_bridge_no_scheduler_test`, so a new one cannot be missed. The
#: bridge's Phase 4.8 names the same ids.
SCHEDULE_RELEASE_ITEMS = {
    "v3110_add_friday_wrap_to_existing_schedules": "3.11.0",
    "v3143_post_update_rebind_recovery": "3.14.3",
    "v450_reregister_schedules": "4.5.0",
    "v480_reregister_maintenance": "4.8.0",
    "v490_instruct_staff_meeting_cadence": "4.9.0",
    "v500_instruct_commitments_split_migration": "5.0.0",
    "v5110_instruct_pick_up_the_merge_on_this_computer": "5.11.0",
    "v5140_instruct_set_up_schedules": "5.14.0",
    "v5190_instruct_reassert_schedules": "5.19.0",
    "v5210_instruct_schedule_reregistration": "5.21.0",
    "v5300_fold_scheduled_fires": "5.30.0",
    "v5310_readiness_retirement": "5.31.0",
    "v5330_instruct_schedule_setup": "5.33.0",
}
BRIDGE_SKIPPED_RELEASE_ITEMS = tuple(SCHEDULE_RELEASE_ITEMS)

#: The versions whose manifest ships schedule work: what `pending_line`
#: compares the seat's last applied schedule version against.
SCHEDULE_WORK_VERSIONS = tuple(sorted(set(SCHEDULE_RELEASE_ITEMS.values()),
                                      key=lambda v: tuple(int(x) for x in v.split("."))))

#: The additive field the update bridge sets on its `plugin_update` row from
#: cr1#970 on: that update did no schedule work, so the row is never read as
#: "schedule work applied through this version" (`_baseline`).
HANDED_OFF_KEY = "schedules_handed_off"

#: The rows that prove schedule setup or a Hub set-up ran on this seat at
#: least once (each registration lands a `schedule_created`).
SETUP_EVIDENCE_TYPES = ("schedule_created", EVENT_TYPE)

#: The one sentence the update says while schedule work is waiting. It names
#: both owners' phrases; it promises nothing about what the work is.
PENDING_LINE = (
    "Your scheduled chats aren't set up on this version yet. Say "
    "`set up command room schedules` once, or `rebuild my hub` if you use "
    "the Hub, and they will be."
)


def _installed_version(plugin_version: Optional[str]) -> Optional[str]:
    """The version given, else the one in the `plugin.json` beside this module."""
    if isinstance(plugin_version, str) and plugin_version.strip():
        return plugin_version.strip()
    try:
        payload = json.loads((_HERE.parent.parent / ".claude-plugin" / "plugin.json")
                             .read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - no file, no version
        return None
    version = payload.get("version") if isinstance(payload, dict) else None
    return version.strip() if isinstance(version, str) and version.strip() else None


def _events(workspace_root) -> list:
    try:
        from events_io import load_events_owner_scoped
        events, _skipped = load_events_owner_scoped(workspace_root)
    except Exception:  # noqa: BLE001 - an unreadable ledger reads as empty
        return []
    return [ev for ev in events or [] if isinstance(ev, dict)]


def _type(ev) -> str:
    return str(ev.get("type") or ev.get("event") or "")


def _newest_applied(workspace_root, events=None) -> Optional[dict]:
    """The newest `schedule_changes_applied` row's data, or None."""
    newest = None
    for ev in _events(workspace_root) if events is None else events:
        if _type(ev) != EVENT_TYPE:
            continue
        newest = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    return newest


def _vt(version) -> Optional[tuple]:
    """`"5.38.0"` -> (5, 38, 0); a suffix after the numbers is ignored; a
    version with fewer than three parts is padded (`"5.33"` reads as
    `5.33.0`); an unreadable version is None."""
    parts = []
    for seg in str(version or "").strip().split("."):
        digits = ""
        for ch in seg:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
        if len(digits) != len(seg):
            break
    if not parts:
        return None
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _baseline(events, installed: tuple) -> Optional[tuple]:
    """The version this seat's schedule work was last applied through: the
    newest marker's version; else, on a seat set up before cr1#970 (when the
    update bridge did the schedule work), the newest `plugin_update` row's
    `to_version` below the installed one, from a row an update that did the
    schedule work wrote. A row carrying `HANDED_OFF_KEY` (every update since
    cr1#970) did none, so it never counts. None when nothing is readable."""
    data = _newest_applied(None, events)
    if data:
        return _vt(data.get("plugin_version"))
    best = None
    for ev in events:
        if _type(ev) != "plugin_update":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if d.get(HANDED_OFF_KEY):
            continue
        v = _vt(d.get("to_version"))
        if v and v < installed and (best is None or v > best):
            best = v
    return best


def applied_version(workspace_root) -> Optional[str]:
    """The version the newest `schedule_changes_applied` row names, or None."""
    data = _newest_applied(workspace_root)
    if not data:
        return None
    version = data.get("plugin_version")
    return str(version).strip() if isinstance(version, str) and version.strip() else None


#: The fixed placeholders the baked Maintenance prompt is composed with for
#: the fingerprint: the shape of a real pair and folder, never a seat's.
PLACEHOLDER_PAIR = ("acct-" + "0" * 12, "0" * 64)
PLACEHOLDER_PATH = "/Users/sample/Sample Workspace"


def schedule_fingerprint() -> str:
    """A short digest of what an update's schedule work depends on (cr1#970
    review): Maintenance's REGISTERED prompt, both plain and in the baked form
    cloud and merged seats register (composed for a placeholder folder with
    a fixed, shape-valid placeholder writer pair, so it is the registration's
    shape, never a seat's), and the shelved, retired and superseded sets. The
    marker stores it; a change to any of them brings the update's line back.
    Pure; no scheduler read.

    cr1#972: Maintenance registers a short stub whose text is composed at
    every fire, so the registered prompt is the stub
    (`schedule_backend.compose_registration_body`), and a change to a
    maintenance job alone changes nothing registered and does not bring the
    line back. The version stamp is left empty, so the digest is the same on
    every version until the stub itself changes."""
    import hashlib
    import schedule_config as sc

    def _registered(pair):
        try:
            import schedule_backend as sb  # noqa: WPS433 - lazy
            return sb.compose_registration_body(
                "maintenance", basename=PLACEHOLDER_PATH.rsplit("/", 1)[-1],
                plugin_version="", abs_path=PLACEHOLDER_PATH, writer_pair=pair)
        except Exception:  # noqa: BLE001 - an uncomposable prompt still fingerprints
            return ""

    maint = _registered(None)
    baked = _registered(PLACEHOLDER_PAIR)
    parts = {
        "maintenance": maint,
        "maintenance_baked": baked,
        "shelved": sorted(str(t) for t in sc.SHELVED_TASK_IDS),
        "retired": sorted(str(t) for t in sc.RETIRED_TASKS),
        "superseded": sorted((str(k), sorted(str(x) for x in (v or [])))
                             for k, v in sc.SUPERSEDED_BY.items()),
    }
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def schedule_work_pending(workspace_root, plugin_version: Optional[str] = None) -> bool:
    """Is schedule work waiting on this seat? A pure read of the activity log
    and this module's table; no scheduler read, no write.

    True when (a) a shipped version after the one this seat's schedule work
    was last applied through, up to the installed one, carries a schedule
    release item (`SCHEDULE_WORK_VERSIONS`), (b) the seat has no record of
    schedule setup or a Hub set-up ever having run (no `schedule_created`, no
    marker), (c) the newest marker, at or below the installed version, has a
    `fingerprint` that is not today's `schedule_fingerprint()` (Maintenance's
    registered stub, or the shelved, retired or superseded set, changed
    since), or (d)
    the seat has no marker and an update since cr1#970 has run here. A
    version bump with no schedule work is False."""
    installed = _vt(_installed_version(plugin_version))
    if not installed:
        return False
    events = _events(workspace_root)
    if not any(_type(ev) in SETUP_EVIDENCE_TYPES for ev in events):
        return True
    marker = _newest_applied(None, events)
    if marker is None:
        # cr1#970 review: a seat set up before this change has no marker. Once
        # an update since this change has run here (its row carries
        # `HANDED_OFF_KEY`), that update did no schedule work, so the seat's
        # schedule work is waiting until an owner runs and writes the marker.
        for ev in events:
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if _type(ev) == "plugin_update" and d.get(HANDED_OFF_KEY):
                return True
    else:
        # (c) only when the marker is not AHEAD of this seat: an older
        # computer on a synced folder never nags about, or re-applies, a newer
        # computer's work.
        mv = _vt(marker.get("plugin_version"))
        if (mv is None or mv <= installed) and marker.get("fingerprint") != schedule_fingerprint():
            return True
    base = _baseline(events, installed)
    if base is None:
        return True
    return any(base < _vt(v) <= installed for v in SCHEDULE_WORK_VERSIONS)


def pending_line(workspace_root, plugin_version: Optional[str] = None,
                 tools: Optional[Iterable] = None) -> str:
    """The update bridge's ONE line about scheduled chats (cr1#970), or "".

    Said only when schedule work is pending (`schedule_work_pending`): a
    version since the seat's last applied schedule work ships a schedule
    item, or schedule setup has never run here. "" otherwise, and "" on a
    seat that cannot set schedules up (the bridge says nothing about
    schedules there, the rule its old scheduler guard kept). The line goes
    through the one invite producer (`schedule_config.setup_invite_line`). A
    pure read: no scheduler call, no write.
    """
    if not schedule_work_pending(workspace_root, plugin_version):
        return ""
    try:
        import schedule_config as sc
        return sc.setup_invite_line(tools, workspace_root=workspace_root,
                                    invite=PENDING_LINE, alternative="")
    except Exception:  # noqa: BLE001 - a sentence never fails the update
        return ""


def log_schedule_changes_applied(workspace_root, plugin_version: Optional[str] = None,
                                 source_skill: str = "enable-command-room-schedules") -> dict:
    """A WRITER (cr1#970). The last step of `references/schedule-changes.md`:
    one `schedule_changes_applied` row naming the installed version, so the
    update's `pending_line` goes quiet. Only the two owner skills may write it.

    Returns `{written: bool, plugin_version, reason?}`. A second call for the
    same version writes nothing (`reason: already_applied`)."""
    out = {"written": False, "plugin_version": None}
    skill = str(source_skill or "").strip()
    if skill not in OWNER_SKILLS:
        out["reason"] = "not_an_owner"
        return out
    version = _installed_version(plugin_version)
    out["plugin_version"] = version
    if not version:
        out["reason"] = "no_version"
        return out
    fingerprint = schedule_fingerprint()
    newest = _newest_applied(workspace_root) or {}
    if (_vt(newest.get("plugin_version")) == _vt(version)
            and newest.get("fingerprint") == fingerprint):
        out["reason"] = "already_applied"
        return out
    event = {"type": EVENT_TYPE, "source_skill": skill,
             "data": {"plugin_version": version, "applied_by": skill,
                      "fingerprint": fingerprint}}
    try:
        from event_gate import append_event
        events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        append_event(events_path, event, holder="schedule:changes-applied")
    except Exception:  # noqa: BLE001 - a failed record leaves the line on, never a raise
        out["reason"] = "append_failed"
        return out
    out["written"] = True
    return out


def _rec_value(rec, key):
    if isinstance(rec, dict):
        return rec.get(key)
    return getattr(rec, key, None)


def may_record_applied(plan, *, refreshed=(), failed_removals=()) -> bool:
    """May the schedule-changes reference write the marker on this run?
    (cr1#970 review.) Not when step 2's plan still holds work this run did
    not finish: a create, a survivor prompt update that no step of this run
    made or judged current (`refreshed` names the task ids step 6 rewrote or
    judged current), or a planned
    delete that failed (`failed_removals`). The update then keeps naming
    `set up command room schedules`. A plan that could not answer (the older
    desktop scheduler, no scheduler) holds no such work."""
    if [t for t in (failed_removals or []) if str(t or "").strip()]:
        return False
    if not isinstance(plan, dict):
        return True
    if plan.get("creates"):
        return False
    done = {str(t) for t in (refreshed or [])}
    for upd in plan.get("updates") or []:
        tid = upd.get("task_id") if isinstance(upd, dict) else getattr(upd, "task_id", None)
        if str(tid) not in done:
            return False
    return True


#: Said for a chat whose delete failed in this run: it is never named as
#: removed, and the next run tries again.
NOT_REMOVED_LINE = ("{names} could not be removed this time; it is tried again the next "
                    "time you say `set up command room schedules` or `rebuild my hub`.")


def owner_line(plan, removed_task_ids, failed_task_ids=()) -> str:
    """The schedule-changes step's ONE line, composed from the removals that
    actually ran (cr1#970 review): `removed_task_ids` are the chats whose
    delete succeeded, `failed_task_ids` those whose delete failed. Equals the
    plan's own `owner_line` when every planned delete ran. "" when the step
    has no news. Pure; the words are `schedule_backend`'s."""
    if not isinstance(plan, dict) or not plan.get("ok"):
        return ""
    import schedule_backend as sb
    import schedule_config as sc
    done = {str(t) for t in (removed_task_ids or [])}
    failed = [str(t) for t in (failed_task_ids or []) if str(t or "").strip()]
    removed_names, hub = [], False
    for r in plan.get("removals") or []:
        tid = r.get("task_id") if isinstance(r, dict) else getattr(r, "task_id", None)
        if str(tid) not in done:
            continue
        if tid == sb.ONE_TASK_COCKPIT_ID:
            hub = True
            continue
        name = sc.task_display_name(tid)
        if name not in removed_names:
            removed_names.append(name)
    tail = str(plan.get("owner_tail") or "")
    if failed:
        names = sorted({sc.task_display_name(t) for t in failed if t != sb.ONE_TASK_COCKPIT_ID})
        if names:
            tail += " " + NOT_REMOVED_LINE.format(names=sb._join_names(names))
    if not (removed_names or hub or tail.strip()):
        return ""
    line = sb._with_tail(sb.reinstall_line([], removed_names,
                                           plan.get("kept_names_all") or plan.get("kept_names") or [],
                                           []), tail)
    if hub:
        line += " " + sb.HUB_ONE_TASK_LINE
    return line


def records_after_removals(records, removals) -> list:
    """The listing the rest of the schedule-changes reference reads, with
    every chat step 2's removals named taken out (cr1#970 review): a deleted
    chat is never paused, folded or offered for switch-off after its delete,
    whether the delete call succeeded or not. A removal is matched by its
    trigger id; one with no trigger id (a planner that names none) by its
    task id. Pure."""
    trig, tids = set(), set()
    for plan in removals or []:
        args = _rec_value(plan, "args") or {}
        tid = _rec_value(plan, "task_id") or (args.get("task_id") if isinstance(args, dict) else None)
        trigger = args.get("trigger_id") if isinstance(args, dict) else None
        if trigger:
            trig.add(str(trigger))
        elif tid:
            tids.add(str(tid))
    out = []
    for rec in records or []:
        rt = _rec_value(rec, "trigger_id")
        rid = _rec_value(rec, "taskId") or _rec_value(rec, "task_id")
        if (rt and str(rt) in trig) or (rid and str(rid) in tids):
            continue
        out.append(rec)
    return out
