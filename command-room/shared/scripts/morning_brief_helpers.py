#!/usr/bin/env python3
"""The morning brief's named entry points, for the workspace access layer.

WHY THIS MODULE EXISTS (ORCH2, Night M3). `morning-briefing/SKILL.md` and
`orchestrator-morning-brief.md` carried eleven python blocks that opened the
customer's workspace in-process: the first-run settings, the brief's shape
settings and render, the chat context leg, the per-project payloads, the open
commitment read, the brief state, the lateness check, the call-prep knob, the
prep leg and the one combined receipt. On a merged seat those blocks run in a
container that has no workspace, so every one of them opened nothing. This
module is their other half: each block is now ONE `plan run_helper` line naming
a function here, answered with JSON by the process that holds the data.

THE RULE EVERY FUNCTION HERE OBEYS (workspace_access review B-1, R-M2-6). It
READS or COMPUTES and writes nothing. A row the delegated writer would have
appended is CAPTURED (`inbox_helpers._captured_appends` — both append doors)
and handed back in `pending_rows`, in the order the writer tried to append it,
for the caller's `plan append_jsonl`. A write that is not a ledger row — a
settings file, a document — is not composed here at all: the caller names the
writer on `RUN_WRITER_ALLOWLIST` with `plan run_writer`.

WHY CAPTURE AND NOT COMPOSE. Where the writer's own call can run with its
appends held, the row that comes back IS the writer's row — its normalisers,
its stamp, its dedup — byte for byte, with no second spelling to drift. That is
how the lateness verdict, the chat-context touches and the combined receipt are
produced. The ONE row composed here is `brief_state`, because its writer
(`commitment_state.compute_and_log_brief_state`) reaches `plate_view`'s number
map (a whole-file write the capture cannot hold) whenever no plate count is
handed in; that row is assembled from the module's own pieces and pinned
against what the writer puts on disk for the same input in
`tests/run_orch2_test.py`.

Every function takes `workspace_root` first and returns JSON-serialisable data.
3.10-safe: this module ships in the runtime manifest and runs on the sandbox
VM's interpreter.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

#: The scheduled task this module serves.
TASK_ID = "morning-brief"

#: The skill whose settings the brief reads (`skill_config_writer`'s key).
SKILL_NAME = "morning-briefing"

#: SPEC FRP1 — the brief's three first-run decisions. Moved here from the
#: SKILL's python block so the defaults have ONE home the helper and the
#: first-fire save both read.
BRIEF_DEFAULTS: Dict[str, Any] = {
    "depth": "headline",
    "leads_with": "synthesis",
    "going_quiet": {"enabled": True},
}

#: call-prep's one knob the scheduled prep leg honours (Phase 2.95 Step B).
PREP_DEFAULTS: Dict[str, Any] = {"auto_fire": "24h"}


def answers(fn):
    """Every verb here ANSWERS; none raises through the door.

    The access layer reports a raising helper as `helper_failed` with a stack
    tail, which is a mechanism the fire must never narrate (preamble rule 6)
    and which the caller cannot tell from a missing runtime. So a failure
    comes back as data — `{"error": "<Type>: <message>"}` — and the prose
    treats an `error` key exactly as it treats `ok:false`: a stop, said in one
    plain sentence. The old python blocks crashed on the same inputs; the
    outcome for the fire is the same, and now it is readable.

    EXCEPT A REFUSAL (MF-27, merge-fix 2; the merge-fix reader's R-2). A
    writer that cannot name who it writes for raises `WriterIdentityRequired`
    (any exception carrying `cr_refusal_reason`), and the DOOR turns that into
    `ok:false, reason: writer_identity_required` plus its one sentence - the
    "ok:false is a stop" every prompt already obeys. Swallowed here it came
    back as `ok:true` with `{"error": "WriterIdentityRequired: ..."}` inside:
    a stop no prompt caught, and a class name that is a mechanism leak if
    posted. So a refusal passes through untouched, by the same test the door
    uses; every other exception is still answered as data.
    """
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — the answer IS the error
            if (getattr(exc, "cr_refusal_reason", None)
                    or type(exc).__name__ == "WriterIdentityRequired"):
                raise
            return {"error": f"{type(exc).__name__}: {exc}"[:400]}

    return wrapper


#: The plugin root these modules ship in (`shared/scripts/..`/..).
_PLUGIN_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


class base_cwd:
    """Render from the plugin root on every seat that is NOT a VM seat
    (ruling R-ORCH2C-1, the same guard ORCH2C put on Waiting On).

    WHY. The chat-email backstop (`turn_backstop._resolve_workspace_root`)
    finds the workspace from the process's working directory. The base
    in-process blocks and CLI legs ran from the plugin root, found nothing
    and wrote no `gate_ran` row. The door runs every verb with the workspace
    as its working directory, so the same render would land an audit row on a
    legacy seat that the base never wrote — a legacy ledger change that makes
    writer_gate_report's dark rule cry wolf. Legacy seats stay byte for byte
    the base; on a VM seat (`CR_HOST_MODE=vm`) the working directory is left
    alone and the audit lands, as it does for every migrated chain there.
    """

    def __enter__(self):
        import os

        self.prev = None
        if os.environ.get("CR_HOST_MODE") != "vm":
            self.prev = os.getcwd()
            os.chdir(str(_PLUGIN_ROOT))
        return self

    def __exit__(self, *exc):
        import os

        if self.prev is not None:
            os.chdir(self.prev)
        return False


def _jsonable(value: Any) -> Any:
    """Datetimes and tuples to their JSON spelling, recursively. The answers
    here cross a process boundary as JSON; a datetime left in one would make
    the envelope itself fail."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    return value


def _parse_instant(value: Any) -> Optional[_dt.datetime]:
    """An ISO string (or a datetime) to an aware datetime, else None."""
    if isinstance(value, _dt.datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = _dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Phase 2.9 — lateness, with every row the check would write handed back
# ---------------------------------------------------------------------------

@answers
def lateness_verdict(workspace_root: str, task_id: str, *,
                     fired_via: str = "manual", env_date: str = "",
                     now: Optional[Any] = None) -> Dict[str, Any]:
    """`late_fire.check_lateness` for ANY scheduled surface, written nowhere.

    The check runs with `emit=True`, exactly as the old python block ran it,
    so every row it writes on the way — the clock record, the served-slot
    `skipped` receipt behind a `skip_render` directive, the pre-registration
    skip, the `late_fire` telemetry — is the writer's own row. Each append is
    held and handed back in `pending_rows`, in order, for ONE
    `plan append_jsonl`. The verdict itself is unchanged.

    (ORCH1's inbox helper calls the check with `emit=False` and composes the
    telemetry row by hand; that also drops the served-slot skip receipt the
    directive promises is on the ledger. Capturing is the stricter shape, and
    it is the one the four daily surfaces use.)

    `now` pins the instant for a fixture; production passes nothing.
    """
    from inbox_helpers import _captured_appends
    from late_fire import check_lateness

    # The check measures against a machine-local NAIVE instant, so a pinned
    # `now` keeps whatever zone it was given (the inbox verb's own parse).
    at = now if isinstance(now, _dt.datetime) else None
    if isinstance(now, str) and now.strip():
        try:
            at = _dt.datetime.fromisoformat(now.strip().replace("Z", "+00:00"))
        except ValueError:
            at = None
    with _captured_appends() as captured:
        verdict = check_lateness(workspace_root, task_id, fired_via=fired_via,
                                 env_date=env_date or None, emit=True, now=at)
    out = _jsonable(dict(verdict))
    out["pending_rows"] = _jsonable(list(captured))
    return out


@answers
def lateness(workspace_root: str, *, fired_via: str = "manual",
             env_date: str = "", now: Optional[Any] = None) -> Dict[str, Any]:
    """The morning brief's lateness verdict (Phase 2.9)."""
    return lateness_verdict(workspace_root, TASK_ID, fired_via=fired_via,
                            env_date=env_date, now=now)


# ---------------------------------------------------------------------------
# Settings — the first-run knobs and the brief's shape
# ---------------------------------------------------------------------------

@answers
def skill_config(workspace_root: str, *, skill_name: str = SKILL_NAME,
                 defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """`{config, configured, defaults}` — `skill_config_writer.get_config`
    over the defaults, and `is_configured`. Read-only: the first-fire save is
    `skill_config_writer:save_skill_config` through `plan run_writer`, a
    separate act, so a fire that only reads a config cannot mint one."""
    from skill_config_writer import get_config, is_configured

    if defaults is None:
        defaults = BRIEF_DEFAULTS if skill_name == SKILL_NAME else {}
    return {"config": _jsonable(get_config(workspace_root, skill_name,
                                           dict(defaults))),
            "configured": bool(is_configured(workspace_root, skill_name)),
            "defaults": _jsonable(dict(defaults))}


@answers
def brief_config(workspace_root: str) -> Dict[str, Any]:
    """The brief's own three first-run decisions (SPEC FRP1)."""
    return skill_config(workspace_root, skill_name=SKILL_NAME,
                        defaults=BRIEF_DEFAULTS)


@answers
def prep_config(workspace_root: str) -> Dict[str, Any]:
    """call-prep's `auto_fire` knob, read for the scheduled prep leg."""
    return skill_config(workspace_root, skill_name="call-prep",
                        defaults=PREP_DEFAULTS)


@answers
def settings_for_fire(workspace_root: str, *,
                      surface: str = SKILL_NAME) -> Dict[str, Any]:
    """`{settings, notes, stated}` — the ten shape settings in the one legal
    order (settings, then the free-text residue beneath them), and which axes
    the reader stated. `brief_settings.settings_for_fire` + `stated_axes`."""
    from brief_settings import settings_for_fire as _settings_for_fire
    from brief_settings import stated_axes

    settings, notes = _settings_for_fire(workspace_root, surface)
    return {"settings": _jsonable(settings), "notes": _jsonable(notes),
            "stated": _jsonable(sorted(stated_axes(workspace_root, surface)))}


@answers
def render_for_fire(workspace_root: str, view: Dict[str, Any], *,
                    surface: str = SKILL_NAME) -> Dict[str, Any]:
    """The composed surface as the reader's settings say it should look —
    `brief_settings.render_for_fire`, the door that reads the store in order
    and renders with it."""
    from brief_settings import render_for_fire as _render_for_fire

    return _jsonable(_render_for_fire(workspace_root, dict(view or {}),
                                      surface=surface))


# ---------------------------------------------------------------------------
# Step 2 — the chat context leg (one line, never a row)
# ---------------------------------------------------------------------------

#: MIGRATE3-MB fix round 1 (N-1): the most chat messages one chat context
#: call reads. The door walks every argument item and refuses past the
#: door's argument cap (`workspace_access.ARG_WALK_CAP`); a message carrying
#: the fields the leg reads (`ts`, `text` and the five pointer fields) walks
#: 15, so 80 of them walk about 1,200.
CHAT_CONTEXT_MAX_MESSAGES = 80


#: MIGRATE3-MB fix round 2: the ONE line when the message ceiling bit.
CHAT_CAP_LINE = ("{n} older chat messages were not read for this brief; it "
                 "read the newest {kept}.")
CHAT_CAP_LINE_ONE = ("1 older chat message was not read for this brief; it "
                     "read the newest {kept}.")


def chat_cap_line(n_left_out: int,
                  kept: int = CHAT_CONTEXT_MAX_MESSAGES) -> str:
    """The pinned line for `n_left_out` messages the ceiling dropped; `""`
    when nothing was dropped."""
    n = int(n_left_out or 0)
    if n <= 0:
        return ""
    if n == 1:
        return CHAT_CAP_LINE_ONE.format(kept=kept)
    return CHAT_CAP_LINE.format(n=n, kept=kept)


def newest_chat_messages(messages: Optional[List[Any]],
                         ceiling: int = CHAT_CONTEXT_MAX_MESSAGES) -> List[Any]:
    """At most `ceiling` messages, the newest by `ts`, in their own order."""
    items = [m for m in (messages or []) if isinstance(m, dict)]
    if len(items) <= ceiling:
        return items
    keep = sorted(range(len(items)), key=lambda i: str(items[i].get("ts") or ""),
                  reverse=True)[:ceiling]
    return [items[i] for i in sorted(keep)]


def tracked_entities_for_chat(workspace_root: str) -> List[Dict[str, Any]]:
    """`[{"id", "kind", "names"}]` for every person and org on the book, the
    primary user excepted, read beside the data from `entities.json` (the
    record's canonical name and aliases) and `aliases.json` (every raw
    spelling mapped to that record). A READ: nothing is written.

    MIGRATE3-MB fix round 2 (R-2): `aliases.json` is read through the
    resolver's own readers (`entity_resolve._load_aliases`,
    `_iter_alias_mappings`), which take BOTH shapes the product has written:
    `mappings` as a flat LIST of `{raw, canonical_id, type}` (a real book) and
    as a DICT of `people` / `projects` / `orgs` lists (the schema's shape). A
    missing file, an empty or unreadable file, or a missing key reads as no
    aliases. And every part of the body is inside a guard: a shape nobody
    has seen yet degrades to what the other file holds, never an error."""
    import json
    from pathlib import Path

    root = Path(workspace_root)
    data_dir = root / "_hq" / "data"
    out: Dict[str, Dict[str, Any]] = {}
    try:
        from entities_io import entities_collection

        raw = json.loads((data_dir / "entities.json").read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except Exception:  # noqa: BLE001 - a read never breaks a fire
        raw = {}
    try:
        from primary_user import resolve_primary_user

        me = resolve_primary_user(workspace_root) or ""
    except Exception:  # noqa: BLE001
        me = ""
    for kind, coll in (("person", "people"), ("org", "orgs")):
        try:
            rows = entities_collection(raw, coll) if raw else []
        except Exception:  # noqa: BLE001
            rows = []
        for row in rows if isinstance(rows, list) else []:
            try:
                if not isinstance(row, dict):
                    continue
                eid = str(row.get("id") or "").strip()
                if not eid or eid == me:
                    continue
                aliases = row.get("aliases")
                aliases = aliases if isinstance(aliases, list) else []
                names = []
                for name in [row.get("canonical_name") or row.get("name")] + aliases:
                    name = str(name or "").strip()
                    if name and name not in names:
                        names.append(name)
                if names:
                    out[eid] = {"id": eid, "kind": kind, "names": names}
            except Exception:  # noqa: BLE001 - one odd record never sinks the rest
                continue
    try:
        from entity_resolve import _iter_alias_mappings, _load_aliases

        loaded = _load_aliases(root)
        mappings = _iter_alias_mappings(loaded if isinstance(loaded, dict) else {})
    except Exception:  # noqa: BLE001 - no aliases is the honest degrade
        mappings = []
    for m in mappings:
        try:
            slot = out.get(str(m.get("canonical_id") or ""))
            name = str(m.get("raw") or "").strip()
            if slot is not None and name and name not in slot["names"]:
                slot["names"].append(name)
        except Exception:  # noqa: BLE001
            continue
    return list(out.values())


@answers
def chat_context(workspace_root: str, chat_messages: Optional[List[Any]] = None,
                 tracked_entities: Optional[List[Dict[str, Any]]] = None, *,
                 provider: Optional[str] = None,
                 source_skill: Optional[str] = None,
                 chat_messages_total: Optional[int] = None) -> Dict[str, Any]:
    """`chat_context.run_chat_context` over the fetch the fire already has.

    The leg appends bounded per-entity touches as it goes; here those appends
    are held and come back in `pending_rows` for the caller's
    `plan append_jsonl`. With no chat backend declared the leg returns its
    skipped block and there is nothing to append.

    MIGRATE3-MB fix round 1 (N-1): the tracked entities are the BOOK, and the
    book is not handed through the door (290 people and orgs walked past
    the door's argument cap of the time; the by-path rule stands, D-T2B-7).
    When `tracked_entities` is not handed in, the list is
    built HERE, beside the data, by `tracked_entities_for_chat`. A handed-in
    list is still honoured. The messages are the chat's own fetch and are
    capped at `CHAT_CONTEXT_MAX_MESSAGES` (the newest kept) so a caller that
    sends more reads the same as one that obeyed the ceiling.
    """
    from chat_context import ReadBudget, run_chat_context
    from connector_adapters import chat as chat_seam
    from inbox_helpers import _captured_appends

    if tracked_entities is None:
        tracked_entities = tracked_entities_for_chat(workspace_root)
    handed = len([m for m in (chat_messages or []) if isinstance(m, dict)])
    chat_messages = newest_chat_messages(chat_messages)
    cap_line = chat_cap_line(max(int(chat_messages_total or 0), handed)
                             - len(chat_messages))
    resolved = chat_seam.resolve_chat_provider(workspace_root, provider)
    plan = chat_seam.plan_scan(resolved, date_filtered=True) if resolved else None
    kwargs: Dict[str, Any] = {"provider": resolved, "scan_plan": plan,
                              "budget": ReadBudget()}
    if source_skill:
        kwargs["source_skill"] = source_skill
    with _captured_appends() as captured:
        out = run_chat_context(workspace_root, list(chat_messages or []),
                               list(tracked_entities or []), **kwargs)
    result = _jsonable(dict(out))
    result["pending_rows"] = _jsonable(list(captured))
    if cap_line:
        # MIGRATE3-MB fix round 2: the message cap never truncates silently.
        result["message_cap_line"] = cap_line
    return result


# ---------------------------------------------------------------------------
# Step 3a / 3b / 3d — the substrate reads the digest renders from
# ---------------------------------------------------------------------------

@answers
def thread_payloads(workspace_root: str, thread_ids: Optional[List[str]] = None,
                    *, profile: str = "brief-line",
                    now_iso: Optional[str] = None) -> Dict[str, Any]:
    """`{thread_id: payload}` — `load_thread_knowledge(workspace_root,
    thread_id, "brief-line")`, ONE call per thread that renders its own detail
    line. A thread the loader refuses comes back as `{"error": ...}` beside
    the others rather than failing the whole read."""
    from load_thread_knowledge import load_thread_knowledge

    out: Dict[str, Any] = {}
    for tid in thread_ids or []:
        try:
            out[str(tid)] = _jsonable(load_thread_knowledge(
                workspace_root, str(tid), profile, now_iso=now_iso or None))
        except Exception as exc:  # noqa: BLE001 — one thread never sinks the rest
            out[str(tid)] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def _events_path(workspace_root: str) -> str:
    from pathlib import Path

    return str(Path(workspace_root) / "_hq" / "data" / "events.jsonl")


def _require_named_writer(workspace_root: str) -> None:
    """Refuse on a merged seat's sandbox VM when no writer can be named.

    `receipts.require_writer_identity` asked ONLY where `_is_vm_seat` says
    this is the sandbox VM (BRIEFDOOR1 MUST 4, ruling R-RW3-8). On every
    legacy and local seat this returns having read nothing but the
    environment, so their answers are byte-identical to the base.
    """
    import receipts

    if receipts._is_vm_seat():
        receipts.require_writer_identity(workspace_root=workspace_root)


@answers
def open_commitments(workspace_root: str) -> Dict[str, Any]:
    """`{opens, fields}` — `cru_match.load_open_commitments` RAW (the pending
    rows stay in: the counting API owns that partition), plus the three
    shape-aware field reads (`_commitment_field` for owner, due and status,
    `_commitment_confidence`) per row, so the caller never re-derives a
    field from one of the five shapes by hand."""
    from cru_match import (_commitment_confidence, _commitment_field,
                           load_open_commitments)

    opens = load_open_commitments(_events_path(workspace_root))
    fields = []
    for ev in opens:
        fields.append({
            "id": _commitment_field(ev, "id"),
            "owner_id": _commitment_field(ev, "owner_id"),
            "due": _commitment_field(ev, "due"),
            "status": _commitment_field(ev, "status"),
            "confidence": _commitment_confidence(ev),
        })
    return {"opens": _jsonable(opens), "fields": _jsonable(fields),
            "n_open": len(opens)}


#: MIGRATE3-MB fix round 1 (N-4): the most calendar events one brief state
#: call takes. The door walks every argument item and refuses past the
#: door's argument cap (`workspace_access.ARG_WALK_CAP`); a trimmed event
#: (five fields) walks about 16, so 30 walk about 500 and leave
#: room for a full day of meetings beside them.
BRIEF_STATE_MAX_CALENDAR_EVENTS = 30

#: The fields `compute_brief_state` reads off a calendar event (Step 3c-bis).
BRIEF_STATE_EVENT_FIELDS = ("attendee_person_ids", "summary", "created_ts",
                            "accepted_by", "calendar_event_id")


#: MIGRATE3-MB fix round 2: the ONE line a brief prints when the event
#: ceiling left events out, so a cap never truncates silently.
CALENDAR_CAP_LINE = ("{n} more calendar events were not checked against what "
                     "you owe; this brief checked the {kept} soonest.")
CALENDAR_CAP_LINE_ONE = ("1 more calendar event was not checked against what "
                         "you owe; this brief checked the {kept} soonest.")


def calendar_cap_line(n_left_out: int,
                      kept: int = BRIEF_STATE_MAX_CALENDAR_EVENTS) -> str:
    """The pinned line for `n_left_out` events the ceiling dropped; `""`
    when nothing was dropped (the line is omitted, never padded)."""
    n = int(n_left_out or 0)
    if n <= 0:
        return ""
    if n == 1:
        return CALENDAR_CAP_LINE_ONE.format(kept=kept)
    return CALENDAR_CAP_LINE.format(n=n, kept=kept)


def trim_calendar_events(events: Optional[List[Any]], now_iso: str = "",
                         ceiling: int = BRIEF_STATE_MAX_CALENDAR_EVENTS
                         ) -> List[Dict[str, Any]]:
    """At most `ceiling` events, the SOONEST: nearest `now_iso` by the
    event's own start (`start` / `start_ts`) when it carries one, else by
    `created_ts` (newest first when there is no instant); each carrying only
    the five fields the brief state reads. Pure: runs in the plugin, reads
    nothing. `calendar_cap_line` says how many were left out."""
    items = [e for e in (events or []) if isinstance(e, dict)]
    rows = [{k: e.get(k) for k in BRIEF_STATE_EVENT_FIELDS if k in e}
            for e in items]
    if len(rows) <= ceiling:
        return rows
    now = _parse_instant(now_iso)

    def distance(index):
        src = items[index]
        when = _parse_instant(src.get("start") or src.get("start_ts")
                              or src.get("created_ts"))
        if when is None:
            return float("inf")
        if now is None:
            return -when.timestamp()
        return abs((when - now).total_seconds())

    keep = sorted(range(len(rows)), key=distance)[:ceiling]
    return [rows[i] for i in sorted(keep)]


@answers
def brief_state(workspace_root: str, *, now_iso: str = "",
                threads: Optional[Dict[str, Any]] = None,
                calendar_events: Optional[List[Any]] = None,
                thread_activity: Optional[Dict[str, Any]] = None,
                sent_reconcile_cursor: Optional[str] = None,
                todays_meetings: Optional[List[Any]] = None,
                fired_via: Optional[str] = None,
                plate_open: Optional[int] = None,
                source_skill: str = SKILL_NAME,
                calendar_events_total: Optional[int] = None) -> Dict[str, Any]:
    """`{state, pending_rows}` — the brief state (Step 3d, Bug #99) and its
    `brief_state` audit row, composed and NOT written.

    `commitment_state.compute_brief_state` computes the state — the open set,
    the primary user and the commitment movement map are derived here exactly
    as `compute_and_log_brief_state` derives them. The audit row is that
    wrapper's own payload: the counts without `by_kind`/`stuck`, the
    projection (`plate_open` when the caller hands the plate's open count in,
    otherwise an honest null — the wrapper's own answer for a derivation it
    cannot make), `open_confirmed`, the attention sizes, `reconcile_stale`, the
    normalised `fired_via` and `receipts.machine_fields`. The same render
    arriving twice inside `BRIEF_STATE_REFIRE_GUARD` composes NO row (the
    wrapper's NUMBER1 3.5 dedup, read through its own reader).
    """
    from pathlib import Path

    import commitment_state as cs
    from cru_match import load_open_commitments
    from primary_user import resolve_primary_user

    # BRIEFDOOR1 MUST 4 — the row this answer composes is stamped with the
    # writer's identity, so on a merged seat's sandbox VM the writer must be
    # NAMED before anything is composed. With no forwarded pair the answer is
    # the one sentence (the door turns the raise into `writer_identity_
    # required`), never a row carrying `machine_id_fallback`. A legacy or local
    # seat is not a VM seat and composes exactly as before.
    _require_named_writer(workspace_root)
    events_path = _events_path(workspace_root)
    # MIGRATE3-MB fix round 1 (N-4): the thread activity is the book's, so it
    # is derived HERE when not handed in (the pack's own canonical
    # derivation); the calendar events are capped at the stated ceiling.
    if thread_activity is None:
        import surface_drivers as _sd

        thread_activity = _sd._brief_thread_activity(workspace_root)
    cap_line = ""
    if calendar_events is not None:
        handed = len([e for e in calendar_events if isinstance(e, dict)])
        calendar_events = trim_calendar_events(calendar_events,
                                               now_iso=now_iso)
        total = max(int(calendar_events_total or 0), handed)
        cap_line = calendar_cap_line(total - len(calendar_events))
    kwargs: Dict[str, Any] = {
        "open_commitments": load_open_commitments(events_path),
        "user_person_id": resolve_primary_user(workspace_root),
        "now_iso": now_iso or _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "threads": threads if threads is not None else None,
        "calendar_events": calendar_events,
        "thread_activity": thread_activity,
        "sent_reconcile_cursor": sent_reconcile_cursor,
        "todays_meetings": todays_meetings,
        "workspace_root": workspace_root,
    }
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    try:
        from commitment_activity import derive_commitment_movement
        kwargs["commitment_movement"] = derive_commitment_movement(
            Path(events_path))
    except Exception:  # noqa: BLE001 — the wrapper's own degrade
        pass
    state = cs.compute_brief_state(**kwargs)

    counts = {k: v for k, v in state["counts"].items()
              if k not in ("by_kind", "stuck")}
    projection = (plate_open if isinstance(plate_open, int)
                  and not isinstance(plate_open, bool) else None)
    counts["open_confirmed"] = counts.get("total")
    if projection is not None:
        counts["total"] = projection
    try:
        from receipts import FIRED_VIA, resolve_fired_via
        via = resolve_fired_via(fired_via)
        via = via if via in FIRED_VIA else "manual"
    except Exception:  # noqa: BLE001
        via = fired_via if fired_via in ("scheduled", "manual", "catchup") else "manual"
    payload: Dict[str, Any] = {
        "counts": counts,
        "projection": projection,
        "n_needs_attention": len(state["needs_attention"]),
        "n_meeting_linked": len(state.get("meeting_linked") or []),
        "reconcile_stale": state["reconcile_stale"],
        "fired_via": via,
    }
    try:
        from receipts import machine_fields
        payload.update(machine_fields())
    except Exception:  # noqa: BLE001 — the wrapper's own tolerance
        pass
    rows: List[Dict[str, Any]] = []
    if not cs._brief_state_already_written(events_path, payload):
        rows.append({"type": "brief_state", "source_skill": source_skill,
                     "data": payload})
    answer = {"state": _jsonable(state), "pending_rows": _jsonable(rows)}
    if cap_line:
        # MIGRATE3-MB fix round 2: the cap never truncates silently.
        answer["calendar_cap_line"] = cap_line
    return answer


# ---------------------------------------------------------------------------
# Phase 2.95 — the prep leg, in two verbs around the generator
# ---------------------------------------------------------------------------

def _receipt_lookup(workspace_root: str, before: Optional[_dt.datetime]):
    """The leg's receipt lookup (F-29 — `prep_brief` receipts, newest first),
    with ONE difference: a REUSE question (a `since` earlier than `before`)
    never sees a receipt written at or after `before`. That is what lets the
    leg be replayed after the generator ran: the prep THIS fire wrote is the
    same-fire proof for a `ran` row, and must not also read as an earlier
    fire's prep that this one reused."""
    from receipts import prep_receipts

    def lookup(meeting_id, *, since=None):
        if not meeting_id:
            return None
        try:
            rows = prep_receipts(workspace_root, meeting_ids=[meeting_id],
                                 since=since)
        except Exception:  # noqa: BLE001 — a substrate read never kills a leg
            return None
        if before is not None and (since is None or since < before):
            rows = [r for r in rows if r.get("dt") is not None and r["dt"] < before]
        if not rows:
            return None
        row = rows[-1]
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        seq = raw.get("seq")
        return {"seq": seq if isinstance(seq, int) else None,
                "artifact": row.get("artifact"), "dt": row.get("dt"),
                "meeting_start": data.get("meeting_start")}

    return lookup


@answers
def prep_leg_plan(workspace_root: str, meetings: Optional[List[Dict[str, Any]]] = None,
                  *, now_iso: str = "") -> Dict[str, Any]:
    """Step C, first half: which of today's meetings the generator must run
    for. `{started_at, reuse, generate}`.

    The leg's own reuse rule (BRIEFFIX1 Item B) decides — a `prep_brief`
    receipt written TODAY for THIS meeting instance (the calendar id AND the
    instance's own start). A meeting it cannot prove is in `generate`; the
    caller runs `skills/call-prep/SKILL.md` end to end for each one, then hands
    what each run returned to `prep_leg_result`. `started_at` is the leg's
    clock; pass it back unchanged.
    """
    import prep_leg as pl

    started = _parse_instant(now_iso) or _dt.datetime.now(_dt.timezone.utc)
    lookup = _receipt_lookup(workspace_root, None)
    floor = pl.local_day_floor(started, workspace_root)
    reuse, generate = [], []
    for meeting in meetings or []:
        if not isinstance(meeting, dict):
            continue
        found = lookup(str(meeting.get("meeting_id") or "").strip(), since=floor)
        if found and pl._is_this_instance(found, meeting) and pl._reused_pointer(found):
            reuse.append(str(meeting.get("meeting_id") or ""))
        else:
            generate.append(meeting)
    return {"started_at": started.isoformat(), "reuse": reuse,
            "generate": _jsonable(generate)}


@answers
def prep_leg_result(workspace_root: str, meetings: Optional[List[Dict[str, Any]]] = None,
                    generated: Optional[Dict[str, Any]] = None, *,
                    started_at: str = "",
                    discover_error: Optional[str] = None) -> Dict[str, Any]:
    """Step C, second half: the leg result, from what the generator did.

    `prep_leg.run_prep_leg` runs here for real — its reuse rule, its
    per-meeting isolation, its workspace-relative fence, its same-fire receipt
    proof — over the meetings Step A found and a generator that REPLAYS what
    each call-prep run reported: `generated[meeting_id]` is the dict call-prep
    returned (`{"brief_path", "sources"}`), `null` for a deliberate skip, or
    `{"error": "<why>"}` for a run that raised. `discover_error` is the Step A
    failure, which degrades the whole leg to its one banner.
    """
    import prep_leg as pl

    started = _parse_instant(started_at) or _dt.datetime.now(_dt.timezone.utc)
    reported = dict(generated or {})
    items = list(meetings or [])

    def discover():
        if discover_error:
            raise RuntimeError(str(discover_error))
        return items

    def generate(meeting):
        mid = str((meeting or {}).get("meeting_id") or "")
        got = reported.get(mid)
        if isinstance(got, dict) and got.get("error"):
            raise RuntimeError(str(got["error"]))
        return got

    leg = pl.run_prep_leg(discover, generate, workspace_root=workspace_root,
                          prep_lookup=_receipt_lookup(workspace_root, started),
                          now=started)
    return _jsonable(leg)


@answers
def prep_meeting_lines(workspace_root: str,
                       leg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """`{lines}` — `prep_leg.meeting_lines(leg, workspace_root=...)`, the
    calendar-section lines built from the leg's outcomes. Refuses (the leg's
    own `LegNotRunError`) when handed no leg."""
    import prep_leg as pl

    return {"lines": list(pl.meeting_lines(dict(leg) if leg else None,
                                           workspace_root=workspace_root))}


# ---------------------------------------------------------------------------
# Phase 5 — the ONE combined receipt, composed by its own writer
# ---------------------------------------------------------------------------

@answers
def plan_combined_receipt(workspace_root: str, *,
                          leg_result: Optional[Dict[str, Any]] = None,
                          brief_status: str = "ran",
                          fired_via: Optional[str] = None,
                          duration_ms: Optional[int] = None,
                          late_tier: Optional[str] = None,
                          extra_data: Optional[Dict[str, Any]] = None
                          ) -> Dict[str, Any]:
    """`{rows}` — the fire's ONE `pack_run` receipt covering both legs.

    `prep_leg.log_combined_receipt` runs here with its append held: the row
    that comes back is the one it writes — `receipts.log_receipt`'s shape,
    the `legs` and `prep_leg` blocks, the workspace-relative pointer assert,
    the writer's identity stamp — and the caller lands it with ONE
    `plan append_jsonl`. A leg the caller never ran is `skipped_leg()`'s
    shape, never a hand-rolled dict.
    """
    import prep_leg as pl
    from inbox_helpers import _captured_appends

    leg = dict(leg_result) if isinstance(leg_result, dict) else pl.skipped_leg(
        pl.SKIP_NO_LEG)
    with _captured_appends() as captured:
        pl.log_combined_receipt(workspace_root, leg_result=leg,
                                brief_status=brief_status,
                                fired_via=fired_via, duration_ms=duration_ms,
                                late_tier=late_tier,
                                extra_data=dict(extra_data or {}))
    return {"rows": _jsonable(list(captured))}


# ---------------------------------------------------------------------------
# Step 0 — the catch-up's ONE maintenance receipt, composed by its own writer
# ---------------------------------------------------------------------------

@answers
def plan_maintenance_receipt(workspace_root: str, *,
                             jobs_due: Optional[List[str]] = None,
                             jobs_completed: Optional[List[str]] = None,
                             jobs_failed: Optional[List[str]] = None,
                             skipped_disabled: Optional[List[str]] = None,
                             skipped_min_gap: bool = False,
                             fired_via: Optional[str] = "manual",
                             triggered_by: Optional[str] = TASK_ID
                             ) -> Dict[str, Any]:
    """`{rows}` — THE one `maintenance_run` row a maintenance pass owes.

    `maintenance_dispatcher.maintenance_receipt` runs here with its append
    held; the row it would write comes back for ONE `plan append_jsonl`. Two
    callers: the brief's Step 0 catch-up, whose defaults these are — `manual`
    + `triggered_by: morning-brief` (R-RW-5: a catch-up rides in front of a
    surface a person typed, so it is never a scheduled fire) — and the
    scheduled maintenance fire's merged branch (M3), which passes
    `scheduled` and `triggered_by: null`.
    """
    import maintenance_dispatcher as md
    from inbox_helpers import _captured_appends

    with _captured_appends() as captured:
        md.maintenance_receipt(workspace_root,
                               jobs_due=list(jobs_due or []),
                               jobs_completed=list(jobs_completed or []),
                               jobs_failed=list(jobs_failed or []),
                               skipped_disabled=list(skipped_disabled or []),
                               skipped_min_gap=bool(skipped_min_gap),
                               fired_via=fired_via, triggered_by=triggered_by)
    return {"rows": _jsonable(list(captured))}


# ---------------------------------------------------------------------------
# BRIEFDOOR1 MUST 3 — the SKILL's last in-process blocks, as named verbs
# ---------------------------------------------------------------------------
#
# The typed brief on the merged seat (re-walk 3, HOLD driver 2) ran the
# SKILL's python blocks and the pack driver FROM THE STAGED RUNTIME ON THE
# DEVICE, where no account lives, so every write refused and nothing landed.
# Each block below is now ONE `plan` line: the three reads on the read list,
# and the two verbs that build the plate on the WRITE list — building the
# plate mints its display numbers under the ledger lock (`plate_view.
# mint_display_numbers`), which is why My Plate's one-command driver is a
# writer too (`plate_helpers:run_my_plate_surface`).


@answers
def reminders(workspace_root: str, today: str = "", *,
              surface: str = "m_facing") -> Dict[str, Any]:
    """Step 3f — `{rows}`: `reminders.load_active_reminders(workspace_root,
    today, surface=...)`, the user's own pins (never commitments). `today` is
    the workspace-TZ date; empty means the workspace's own today. The brief
    is an owner-facing surface, so personal reminders render (`m_facing`)."""
    from reminders import load_active_reminders

    day = today
    if not day:
        try:
            from tz import localize_date

            day = localize_date(_dt.datetime.now(_dt.timezone.utc).isoformat(),
                                workspace_path=workspace_root)
        except Exception:  # noqa: BLE001 — the host's date is the fallback
            day = None
        day = day or _dt.date.today().isoformat()
    day = str(day)[:10]
    return {"rows": _jsonable(load_active_reminders(workspace_root, day,
                                                    surface=surface))}


@answers
def confirm_lines(workspace_root: str, now_iso: str = "") -> Dict[str, Any]:
    """Step 3g — `{n_confirm, pointer}`: the confirm-section count with the
    selectors the Waiting On view uses, over the RAW open set, and the ONE
    pointer line (`None` when the section is empty — the line is omitted,
    never padded). Person proposals count CLUSTERS (PID1), never raw events;
    people already on file are not counted (FS-19)."""
    from confirm_flow import (confirm_pointer_line, load_open_person_proposals,
                              select_confirm_items, select_promotion_proposals)
    from cru_match import load_open_commitments
    from events_io import iter_events
    from identity_reconcile import count_person_rows
    from mute_ledger import active_dismissal_target_ids

    now = now_iso or _dt.datetime.now(_dt.timezone.utc).isoformat()
    events_path = _events_path(workspace_root)
    opens = load_open_commitments(events_path)
    dismissed = active_dismissal_target_ids(iter_events(workspace_root), now)
    person_rows = load_open_person_proposals(
        events_path, dismissed_target_ids=dismissed, suppress_on_file=True)
    n_confirm = (len(select_confirm_items(opens, now, dismissed_ids=dismissed))
                 + len(select_promotion_proposals(opens, dismissed_ids=dismissed))
                 + count_person_rows(person_rows, now_iso=now))
    return {"n_confirm": int(n_confirm),
            "pointer": confirm_pointer_line(n_confirm)}


@answers
def money_lines(workspace_root: str, now_iso: str = "", *,
                cap: int = 3) -> Dict[str, Any]:
    """Step 3h — `{money_lines, count}`: FB-20's one carve-out (money-class
    proposals as one prose sentence each, propose-only) and the staff
    meeting's queue count, from the SAME projector the staff meeting renders
    (`brain_proposals.load_open_proposals(..., "staff-meeting")`, auto-tier
    items out). The pack carries both already; this is the direct path."""
    from brain_proposals import load_open_proposals, money_prose_lines

    queue = [i for i in load_open_proposals(workspace_root, "staff-meeting",
                                            now_iso=now_iso or None)
             if i.get("tier") != "auto"]
    return {"money_lines": list(money_prose_lines(queue, cap=int(cap))),
            "count": len(queue)}


@answers
def plate_lines(workspace_root: str, now_iso: str = "", *,
                exclude_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Step 3i's direct path — the plate's brief cut when the pack is not in
    hand: `plate_view.build_plate(...)` then `plate_view.render_plate(view,
    "brief", False, exclude_ids=...)`, no `ask_lines` (the brief does not ask
    — REVIEW_NIGHT11C H-5). Answers the renderer's own dict; `text` renders
    verbatim. A WRITER: the build mints the rows' display numbers."""
    import plate_view

    view = plate_view.build_plate(workspace_root, now_iso=now_iso or None)
    if view.get("error"):
        return {"refused": True, "line": view.get("error") or "", "rows": [],
                "text": view.get("error") or ""}
    rendered = plate_view.render_plate(view, "brief", False,
                                       exclude_ids=list(exclude_ids or []))
    return _jsonable(dict(rendered))


#: Where the pack's audit copy lives, workspace-relative (the pack builder's
#: own `_hq/.system/briefs/morning-pack-<stamp>.json`).
PACK_DIR_REL = "_hq/.system/briefs"


def pack_file_text(pack: Dict[str, Any]) -> str:
    """The pack's audit copy, as the text `plan write` lands.

    The pack builder's own spelling (`json.dumps(pack, indent=2,
    ensure_ascii=False)`), with ONE change: every apostrophe is written as its
    JSON escape. The copy travels inside a single-quoted `--json '...'`
    argument on a pasted command line, where a raw apostrophe ("what's on my
    plate") would end the argument early; the escape parses back to the same
    character, so the file reads as the same pack."""
    import json

    return json.dumps(_jsonable(pack), indent=2,
                      ensure_ascii=False).replace("'", "\\u0027")


@answers
def morning_pack(workspace_root: str, now_iso: str = "", *,
                 log_state: bool = False) -> Dict[str, Any]:
    """Step 3h / 3i on every seat — `{pack, pending_rows, pack_file}`.

    `surface_drivers.build_morning_brief_pack(mode="manual")`, run beside the
    data with BOTH append doors held and its own audit-copy write held:

      pack          the pack the SKILL renders from (`lead`, `plate`,
                    `changed`, `money_lines`, `queue_pointer`, `fold_lines`,
                    `explain_once_line`, ...), exactly as the driver built it.
      pending_rows  every ledger row the driver tried to append, in order,
                    for ONE `plan append_jsonl`. The driver's own `brief_state`
                    row is WITHHELD unless `log_state` is true: on this path
                    Step 3d's `brief_state` verb composes the fire's one audit
                    row, with the connector-fed inputs the driver never has
                    (NUMBER1 3.5 — one row per render). A direct call that
                    runs no Step 3d passes `log_state: true`.
      pack_file     `{rel, data}` — the audit copy, for ONE `plan write`
                    (`expected_mtime: null`); `data` is `pack_file_text`.

    A WRITER, on `RUN_WRITER_ALLOWLIST`: the driver builds the plate, and
    the build mints display numbers under the ledger lock. The rows and the
    pack file do not land here — they come back for the two write doors.
    """
    import contextlib
    import json
    from pathlib import Path

    import atomic_write
    from inbox_helpers import _captured_appends
    import surface_drivers

    now = now_iso or _dt.datetime.now(_dt.timezone.utc).isoformat()
    held: List[Dict[str, Any]] = []
    briefs = (Path(workspace_root) / "_hq" / ".system" / "briefs").resolve()
    real_write_text = atomic_write.atomic_write_text

    def hold_pack_copy(path, content, *args, **kwargs):
        target = Path(path)
        try:
            is_copy = (target.resolve().parent == briefs
                       and target.name.startswith("morning-pack-"))
        except OSError:
            is_copy = False
        if is_copy:
            held.append({"name": target.name})
            return None
        return real_write_text(path, content, *args, **kwargs)

    @contextlib.contextmanager
    def hold_copy():
        atomic_write.atomic_write_text = hold_pack_copy
        try:
            yield
        finally:
            atomic_write.atomic_write_text = real_write_text

    with _captured_appends() as captured, hold_copy():
        pack = surface_drivers.build_morning_brief_pack(
            workspace_root, mode="manual", now_iso=now)
    rows = list(captured)
    if not log_state:
        rows = [r for r in rows
                if not (isinstance(r, dict) and r.get("type") == "brief_state")]
    stamp = str(now)[:19].replace(":", "-")
    name = held[0]["name"] if held else f"morning-pack-{stamp}.json"
    body = json.loads(json.dumps(_jsonable(pack), ensure_ascii=False))
    return {"pack": body, "pending_rows": _jsonable(rows),
            "pack_file": {"rel": f"{PACK_DIR_REL}/{name}",
                          "data": pack_file_text(body)}}


# ---------------------------------------------------------------------------
# BRIEFDOOR1 SHOULD 6 — the post-time conversion and the Phase 6.1 mark
# ---------------------------------------------------------------------------

@answers
def post_text(workspace_root: str, text: str = "", *,
              drive_web_urls: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """`{text}` — `chat_output_renderer.absolutize_doc_links(text,
    workspace_root, drive_web_url=...)`, run beside the data: the copy that
    goes to CHAT, with every workspace-relative document link turned into the
    opener this machine can follow. `drive_web_urls` is `{relative path: web
    link}` for a cloud-mounted workspace (the callable the in-process form
    took cannot cross a command line); a path it does not carry gets `""`,
    which leaves that link as the conversion's own honest no-href form."""
    from chat_output_renderer import absolutize_doc_links

    urls = dict(drive_web_urls or {})
    resolver = (lambda rel: urls.get(str(rel), "")) if urls else None
    return {"text": absolutize_doc_links(str(text or ""), workspace_root,
                                         drive_web_url=resolver)}


@answers
def plan_lane_asked(workspace_root: str,
                    brief_state: Optional[Dict[str, Any]] = None, *,
                    now_iso: str = "") -> Dict[str, Any]:
    """`{result, rows}` — Phase 6.1's mark, composed and NOT written.

    `end_of_day.mark_lane_asked(workspace_root, brief_state,
    source_skill="morning-brief")` runs here with both append doors held: the
    `commitment_updated` rows it would append (one per asked row, through
    `commitment_state.mark_asked`, stamped with the morning's surface id)
    come back in order for ONE `plan append_jsonl`, AFTER the post.

    A WRITER, on `RUN_WRITER_ALLOWLIST`: `mark_asked`'s idempotency read
    runs under the ledger lock, and taking that lock writes its sidecar -
    so this goes through the write door even though its rows land later."""
    from end_of_day import mark_lane_asked
    from inbox_helpers import _captured_appends

    with _captured_appends() as captured:
        result = mark_lane_asked(workspace_root, dict(brief_state or {}),
                                 source_skill=TASK_ID,
                                 now_iso=now_iso or None)
    return {"result": _jsonable(result), "rows": _jsonable(list(captured))}


# ---------------------------------------------------------------------------
# MIGRATE3-MB (Train 2b, F-T2-15) - the pack driver and call-prep's last
# in-process blocks, as named verbs
# ---------------------------------------------------------------------------
#
# The scheduled Morning Brief stopped on the merged seat with the migration
# gate's one sentence, because Phase 3.9 still ran the pack CLI in the
# session's own shell and the prep leg ran call-prep end to end, whose four
# python blocks opened the workspace in-process. Each is ONE door line now.


@answers
def run_morning_brief_pack(workspace_root: str, mode: str = "scheduled",
                           now_iso: str = "") -> Dict[str, Any]:
    """Phase 3.9 - `{pack}`: the SAME builder the `surface_drivers.py
    morning-brief` CLI wraps (`build_morning_brief_pack(workspace_root,
    mode=..., now_iso=...)`), run beside the data by the WRITE door.

    A writer: the build logs the `brief_state` row, persists the pack's audit
    copy and mints the plate's display numbers under the ledger lock, exactly
    as the CLI does on a seat whose files are local. `pack` is the dict the
    CLI printed after `CR-BRIEF-PACK: `, round-tripped through JSON the way
    the CLI's own dump spells it (`default=str`).

    A build that raises fails the way the CLI branch fails (SURFACEFIX1 5.3):
    the `surface_failed` receipt is written and the answer is `{pack: None,
    surface_failed: True, line}`, where `line` is the product's one sentence.
    Never a traceback, never a class name on the screen.
    """
    import json

    import surface_drivers as sd

    run_mode = mode if mode in ("scheduled", "manual") else "scheduled"
    now = now_iso or None
    try:
        with base_cwd():
            pack = sd.build_morning_brief_pack(workspace_root, mode=run_mode,
                                               now_iso=now)
        body = json.loads(json.dumps(pack, ensure_ascii=False, default=str))
    except Exception as exc:  # noqa: BLE001 - one sentence, never a trace
        if (getattr(exc, "cr_refusal_reason", None)
                or type(exc).__name__ == "WriterIdentityRequired"):
            raise
        sd.log_surface_failed(workspace_root, sd.SURFACE_FAILED_BRIEF, exc,
                              mode=run_mode, now_iso=now)
        return {"pack": None, "surface_failed": True,
                "line": sd.SURFACE_FAILED_LINES[sd.SURFACE_FAILED_BRIEF]}
    return {"pack": body}


#: call-prep's own first-run knobs (SPEC FRP1). Moved here from the SKILL's
#: python block so the defaults have ONE home the read and the first-fire
#: save both take them from.
CALL_PREP_DEFAULTS: Dict[str, Any] = {"depth": "standard", "auto_fire": "24h"}


@answers
def call_prep_config(workspace_root: str) -> Dict[str, Any]:
    """`{config, configured, defaults}` for call-prep - `get_config` over
    `CALL_PREP_DEFAULTS` and `is_configured`. Read-only: the first-fire save
    is `skill_config_writer:save_skill_config` through the write door."""
    return skill_config(workspace_root, skill_name="call-prep",
                        defaults=CALL_PREP_DEFAULTS)


@answers
def prep_constraints(workspace_root: str, recipient_id: Optional[str] = None,
                     domain: Optional[str] = None,
                     draft: Optional[str] = None) -> Dict[str, Any]:
    """`draft_constraints.load_draft_constraints(workspace_root, "call-prep",
    recipient_id=..., domain=...)`, read beside the data. When `draft` is
    handed in, the answer also carries it through `apply_draft_constraints`
    as `draft` (the learned paragraph ceiling and the dropped phrases)."""
    from draft_constraints import apply_draft_constraints, load_draft_constraints

    found = load_draft_constraints(workspace_root, "call-prep",
                                   recipient_id=recipient_id or None,
                                   domain=domain or None)
    out = _jsonable(dict(found))
    if isinstance(draft, str):
        out["draft"] = apply_draft_constraints(draft, found)
    return out


@answers
def coaching_handoff(workspace_root: str,
                     attendee_person_ids: Optional[List[str]] = None,
                     title: str = "") -> Dict[str, Any]:
    """`coach_state.coaching_handoff_for_meeting` (SPEC COACH1 §4.6), read
    beside the data: `{defer, kind, thread_id, name, reason}`."""
    from coach_state import coaching_handoff_for_meeting

    return _jsonable(dict(coaching_handoff_for_meeting(
        workspace_root, attendee_person_ids=list(attendee_person_ids or []),
        title=str(title or ""))))


@answers
def prep_opener(workspace_root: str, rel: str = "",
                drive_web_url: str = "") -> Dict[str, Any]:
    """`{session_scoped, opener_url}` for a landed prep: `brief_path.
    is_session_scoped_path` and `get_brief_opener_url` over the document at
    `rel` (the landing answer's workspace-relative name), with the web link
    the chat looked up for a cloud-mounted folder, if any."""
    from pathlib import Path

    from brief_path import get_brief_opener_url, is_session_scoped_path

    path = str(Path(workspace_root) / str(rel or ""))
    return {"session_scoped": bool(is_session_scoped_path(path)),
            "opener_url": get_brief_opener_url(path, drive_web_url or "")}


#: D-T2B-1 (M, 2026-09-27): a typed prep never runs a close writer. A row the
#: record already shows finished stays in the Owed table and carries this
#: ONE statement after its title. It asks nothing and offers nothing (EXIT1's
#: "never offer" half stands).
PROVED_ROW_SUFFIX = "looks finished on the record"


def proved_commitment_ids(fact_plan: Optional[Dict[str, Any]]) -> List[str]:
    """The commitment ids `exit_doors.plan_fact_closes` reports as proved
    (its `calendar` and `deal` legs), in the order it reports them, once."""
    out: List[str] = []
    plan = fact_plan if isinstance(fact_plan, dict) else {}
    # MIGRATE3-MB fix round 1 (N-11): a customer who switched the calendar
    # closer off does not read "looks finished" from that same evidence.
    legs = ("deal",) if plan.get("enabled_calendar") is False else ("calendar",
                                                                    "deal")
    for leg in legs:
        for item in plan.get(leg) or []:
            cid = str((item or {}).get("commitment_id") or "").strip()
            if cid and cid not in out:
                out.append(cid)
    return out


def mark_proved_rows(rows: Optional[List[Dict[str, Any]]],
                     fact_plan: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The Owed table's matched rows with `PROVED_ROW_SUFFIX` after the title
    of every row the fact plan proves, ONCE (a row already carrying it is
    left as it is). Pure: copies in, copies out, nothing closed, nothing
    written. Feed the answer to `prep_pipeline.build_owed_table`."""
    proved = set(proved_commitment_ids(fact_plan))
    tail = " (" + PROVED_ROW_SUFFIX + ")"
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        copy = dict(row or {})
        cid = str(copy.get("commitment_id") or copy.get("id") or "").strip()
        title = str(copy.get("title") or "").strip()
        if cid and cid in proved and not title.endswith(tail):
            copy["title"] = (title or "(untitled item)") + tail
        out.append(copy)
    return out


# ---------------------------------------------------------------------------
# MIGRATE3-MB fix round 1 (N-3): a refused or timed-out step is recorded
# ---------------------------------------------------------------------------


class DoorStepRefused(Exception):
    """The class name the `surface_failed` receipt records when the Morning
    Brief stopped because a door answer was `ok: false` mid-chain (a refusal,
    or the door's own timeout, which kills the child before the writer's own
    failure branch can run)."""


def record_fire_stopped(workspace_root: str, *, step: str = "",
                        mode: str = "scheduled") -> Dict[str, Any]:
    """The fire stopped mid-chain: a WRITER, on the write list only.

    Writes the Morning Brief's `surface_failed` receipt (the same one a pack
    that could not build writes, `surface_drivers.log_surface_failed`) so a
    stopped morning is never silent, and answers the one sentence to post,
    `surface_drivers.SURFACE_FAILED_LINES`'s Morning Brief line. `step` names
    the form that was refused, for the chat's own reading only; it never
    reaches the receipt's words or the screen."""
    import surface_drivers as sd

    run_mode = mode if mode in ("scheduled", "manual") else "scheduled"
    receipt = sd.log_surface_failed(workspace_root, sd.SURFACE_FAILED_BRIEF,
                                    DoorStepRefused(str(step or "")),
                                    mode=run_mode)
    return {"line": sd.SURFACE_FAILED_LINES[sd.SURFACE_FAILED_BRIEF],
            "receipt": bool(receipt), "step": str(step or "")}
