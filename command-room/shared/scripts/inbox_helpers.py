#!/usr/bin/env python3
"""The inbox chain's named entry points, for the workspace access layer.

WHY THIS MODULE EXISTS. The inbox orchestrator and `inbox-triage` used to do
their work in eleven inline `python3 -c` bodies: import a reader, open the
workspace, print a line. On a legacy seat that works, because the helpers and
the customer's files are on one filesystem. On a merged seat the helpers are in
a cloud container and the files are on the customer's own machine, so every one
of those blocks opens nothing and the fire stops — the honest stop the gate walk
hits. The access layer's answer is `run_helper`: one NAMED function, called
where the data is, answering with JSON. This module is the inbox chain's half of
that: the functions those eleven blocks now name.

THE RULE EVERY FUNCTION HERE OBEYS (workspace_access review finding B-1). It
READS or COMPUTES and writes nothing — not through a gated writer either. The
helper door applies the path fences and nothing else, so a writer reachable
through it goes round `append_jsonl`'s registered-file rule and `write`'s
append-only rule. Writes are the layer's two write verbs, and the orchestrator
makes them: a function here that needs a row written COMPOSES the row and hands
it back, and the caller appends it with `plan append_jsonl`.

COMPOSING IS NOT HAND-ROLLING. Where a row's writer lives in another module,
the compose half is assembled out of that module's OWN exported parts —
`receipts.machine_fields`, `inbound_capture.build_inbound_commitment_event`,
`cru_match.build_pending_review_event` — never out of a literal dict written
here. `tests/run_orch1_test.py` pins each composed row against what the writer
itself puts on disk for the same input, so the two cannot drift apart quietly.

WHAT IS NOT HERE (and is named in the ORCH1 record as a seam). The CRU pass's
reply-CLOSURE leg goes through the single closure path in `exit_doors`, which
is a gated writer with receipts of its own that `append_jsonl` cannot express.
`plan_inbound_reconcile` therefore returns the closure PROPOSALS and closes
nothing; the reviews and the schedule-shift markers, whose builders are public,
come back as rows the caller appends.

ONE WRITER LIVES HERE, AND ONLY ON THE WRITE DOOR (INBOXDRIVE1, ruling
R-RW3-1 (a)). `run_inbox_surface` is the chain after the fetch in one call; it
is on `workspace_access.RUN_WRITER_ALLOWLIST` and on no read list, and it lands
what it writes through the layer's own `write` and `append_jsonl` - the same
two doors the rule above names, called beside the data instead of by the
model. Every other function here keeps the rule.

Every function takes `workspace_root` first and returns JSON-serialisable data.
3.10-safe: this module ships in the runtime manifest and runs on the sandbox
VM's interpreter.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, Optional

WIDGET_DIR_REL = "_hq/.system/widgets"

#: The surface these helpers serve. Spelled once so the receipt, the telemetry
#: and the widget page all agree without three string literals.
TASK_ID = "inbox"


# ---------------------------------------------------------------------------
# Phase 2.9 — the clock and the lateness tier
# ---------------------------------------------------------------------------

def lateness(workspace_root: str, *, task_id: str = TASK_ID,
             fired_via: str = "manual",
             env_date: str = "",
             now: Optional[Any] = None) -> Dict[str, Any]:
    """This fire's lateness verdict, computed and NOT recorded.

    `late_fire.check_lateness` normally appends its own telemetry on the note
    and degrade tiers. Here it is called with `emit=False`, because on a merged
    seat this helper has no write door — so the verdict comes back with
    `telemetry_row` beside it, and the caller appends that row through
    `plan append_jsonl`. The tier, the banner, the directive and
    `receipt_fired_via` are the module's own, unchanged.

    `now` is the instant the tier is measured against - a datetime, or an ISO
    string this function parses. Omitted, the real clock, exactly as before.
    It exists because a FIXTURE that races the wall clock asserts a tier that
    is only reachable for part of the day: `check_lateness` has always taken
    an instant and this verb had no way to hand one over, so its suites were
    green at midnight and red at eight in the morning. A caller in production
    passes nothing.
    """
    import datetime as _dt

    from late_fire import check_lateness

    at = now
    if isinstance(at, str) and at.strip():
        try:
            at = _dt.datetime.fromisoformat(at.strip().replace("Z", "+00:00"))
        except ValueError:
            at = None
    with _captured_appends() as captured:
        verdict = check_lateness(workspace_root, task_id, fired_via=fired_via,
                                 env_date=env_date or None, emit=False,
                                 now=at if isinstance(at, _dt.datetime) else None)
    out = dict(verdict)
    out["telemetry_row"] = _late_telemetry_row(verdict, task_id)
    pending = list(captured)
    if out["telemetry_row"] is not None:
        pending.append(out["telemetry_row"])
    out["pending_rows"] = pending
    return out


def post_order(verdict: Dict[str, Any], *, widget: bool = True) -> Dict[str, Any]:
    """The turn's shape, from this fire's lateness verdict (IDENT1 I-4).

    `[clock notice] [ack - rerun tier | banner - note/degrade] -> widget ->
    STOP`. THE WALK (2026-09-22, HOLD driver 3): the re-run ack landed AFTER
    the widget, with a narration line and a three-line summary behind it,
    and five working notes sat above it. M ruled (R-RW2-6) the ack renders
    ABOVE the widget and nothing renders after it. So the lines that may
    precede the widget are these and only these, in this order, and
    `after_widget` is always empty - the one post order every branch of
    Phase 8 obeys.

    THE NO-WIDGET SHAPE (INBOXDRIVE1; ruling R-RW3-4). On a run with no widget
    tool the surface is the grouped text list, and it obeys the same STOP:
    `widget=False` answers `widget: False`, and `after_surface` - empty on
    BOTH shapes - is the one list of lines allowed after the surface's last
    line (the saved-at line on the text shape). The re-walk's fire closed
    with a diagnosis paragraph after its summary, on the phone too; that
    paragraph has nowhere to go."""
    verdict = verdict if isinstance(verdict, dict) else {}
    before: List[str] = []
    clock = verdict.get("clock") if isinstance(verdict.get("clock"), dict) else {}
    if clock.get("notice"):
        before.append(str(clock["notice"]))
    tier = verdict.get("tier")
    if tier == "rerun" and verdict.get("ack"):
        before.append(str(verdict["ack"]))
    elif tier in ("note", "degrade"):
        for key in ("banner", "degrade_notice"):
            if verdict.get(key):
                before.append(str(verdict[key]))
    return {"before_widget": before, "widget": bool(widget), "after_widget": [],
            "after_surface": []}


class _Captured(list):
    """The rows a delegated call TRIED to append, collected instead."""


def _captured_appends():
    """Hold the event gate open and collect, for the length of one call.

    `check_lateness(emit=False)` suppresses the three rows it is documented to
    write — but `_clock_field` appends a `clock_untrusted` row unconditionally,
    and that row is real: it is how the ledger says which clock produced the
    dates in a surface. A helper here may not write it, and dropping it would
    be worse than writing it, so it is CAPTURED and returned in `pending_rows`
    for the caller's `plan append_jsonl`. SEAM: one `emit` guard on
    `late_fire._clock_field` would make this shim unnecessary; that file is
    another lane's tonight.
    """
    import contextlib

    @contextlib.contextmanager
    def hold():
        import atomic_write
        import event_gate

        rows = _Captured()

        def collect(_path, event, *args, **kwargs):
            batch = event if isinstance(event, list) else [event]
            rows.extend(batch)
            return len(batch)

        # BOTH doors: the gate is what most writers use, and the raw appender
        # is what a best-effort audit inside a composer reaches for. Patching
        # one and not the other is how a "read-only" helper keeps a write.
        originals = {
            (event_gate, "append_event"): event_gate.append_event,
            (atomic_write, "atomic_append_jsonl"): atomic_write.atomic_append_jsonl,
        }
        for (module, name) in originals:
            setattr(module, name, collect)
        try:
            yield rows
        finally:
            for (module, name), original in originals.items():
                setattr(module, name, original)

    return hold()


def _late_telemetry_row(verdict: Dict[str, Any],
                        task_id: str) -> Optional[Dict[str, Any]]:
    """The `late_fire` row the note/degrade tiers owe, or None.

    THE ROW IS THE WRITER'S ROW, KEY FOR KEY (fix round 1, F-1). `late_fire`
    stamps its own telemetry with `data.taskId` — camel, the spelling its two
    readers use (`late_fire.detect_chronic_lateness` and
    `schedule_proposals`) — and with `receipt_fired_via`, which is `catchup` on
    exactly the tiers that emit this row. Spelling either one differently here
    does not fail anything: it writes a row that looks right and that every
    consumer silently skips. So the fields are the writer's, and
    `tests/run_orch1_test.py` DRIVES the writer and this composer on the same
    fixture with the same `env_date` and compares the two rows — the pin the
    module docstring promises, now actually against the row that drifted.

    `fired_via` falls back to the normalised `fired_via` only if a future
    verdict stops carrying `receipt_fired_via`; on every tier that reaches this
    function today the writer's value is `receipt_fired_via`.
    """
    tier = verdict.get("tier")
    if tier not in ("note", "degrade"):
        return None
    return {
        "type": "late_fire",
        "source_skill": task_id,
        "data": {
            "taskId": task_id,
            "tier": tier,
            "lateness_minutes": verdict.get("lateness_minutes"),
            "scheduled_for": verdict.get("scheduled_for"),
            "fired_via": (verdict.get("receipt_fired_via")
                          or verdict.get("fired_via")),
        },
    }


def header_date(workspace_root: str) -> str:
    """`"<Weekday>, <Mon> <D>"` — both halves off ONE instant, localised
    through the workspace's own timezone (SCHEDVIEW1 5.5). Never composed in
    prose: a header that says Tuesday over a Wednesday date costs more trust
    than no weekday at all."""
    import tz

    return tz.local_header_date(workspace_root)


# ---------------------------------------------------------------------------
# Phase 2 — setup reads
# ---------------------------------------------------------------------------

def setup(workspace_root: str, *, task_id: str = TASK_ID,
          fired_via: str = "manual", env_date: str = "",
          tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """One call for everything Phase 2 used to open the workspace five times
    for: the clock verdict, the day in the sub-header, who the primary user is,
    the learned sender-priority rules, the learned surface suppressions, and
    the mail/calendar seams resolved off the tools this session actually has.

    One call rather than five because each one is a device-shell round trip on
    a merged seat, and a fire that needs five of them before it reads any mail
    is a fire that times out on a slow mount.
    """
    from primary_user import resolve_primary_user

    out: Dict[str, Any] = {
        "lateness": lateness(workspace_root, task_id=task_id,
                             fired_via=fired_via, env_date=env_date),
        "header_date": header_date(workspace_root),
        "priority_rules": priority_rules(workspace_root),
        "surface_preferences": surface_preferences(workspace_root),
        "seams": mail_seams(workspace_root, tools=tools or []),
    }
    try:
        out["primary_user"] = resolve_primary_user(workspace_root)
    except Exception as exc:  # noqa: BLE001 — the caller decides what an
        # unresolved user means for its own phase; the CRU pass aborts on it,
        # the surface does not.
        out["primary_user"] = ""
        out["primary_user_error"] = f"{type(exc).__name__}: {exc}"
    return out


def priority_rules(workspace_root: str) -> List[Dict[str, Any]]:
    """The approved learned sender-priority rules (Loop 1). A missing store is
    an empty list, so a fresh workspace scores exactly as it did before."""
    from triage_feedback import load_sender_priority_rules

    loaded = load_sender_priority_rules(workspace_root) or {}
    return list(loaded.get("rules") or [])


def apply_priority_rules(workspace_root: str,
                         candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each candidate's score after the learned rules, applied LAST — after the
    hard-coded scoring and the financial-signal override, before ranking.

    `candidates` is `[{score, sender, domain}, …]`; the answer is the same list
    with `score` rewritten and the original kept as `score_before`, so the fire
    can see what the learning did rather than only its result.
    """
    from triage_feedback import apply_rules_to_score

    rules = priority_rules(workspace_root)
    out = []
    for row in candidates or []:
        before = row.get("score") or 0
        after = apply_rules_to_score(before, sender=row.get("sender") or "",
                                     domain=row.get("domain") or "",
                                     rules=rules)
        item = dict(row)
        item["score_before"] = before
        item["score"] = after
        out.append(item)
    return out


def surface_preferences(workspace_root: str) -> Dict[str, Any]:
    """The learned suppressions (Loop 2). Missing store → empty → no-op."""
    from surface_preferences import load_surface_preferences

    return dict(load_surface_preferences(workspace_root) or {})


def filter_by_preferences(workspace_root: str, items: List[Dict[str, Any]], *,
                          surface: str = TASK_ID) -> Dict[str, Any]:
    """`{kept, dropped}` — the top-5 with anything the CEO has told the system
    to stop surfacing removed, BEFORE the data view is built.

    Each item carries `item_class` and `entity_id`; nothing else is read, so a
    caller cannot accidentally hand a whole mail body to a preference check.
    """
    from surface_preferences import is_suppressed

    prefs = surface_preferences(workspace_root)
    kept, dropped = [], []
    for item in items or []:
        hit = is_suppressed(prefs, surface,
                            item_class=item.get("item_class") or "",
                            entity_id=item.get("entity_id") or "")
        (dropped if hit else kept).append(item)
    return {"kept": kept, "dropped": dropped, "n_dropped": len(dropped)}


#: The ledger row a silent fire composes when its declared connector is not in
#: this session's registry (R13), and the `triggered_by` it carries.
DRIFT_FLAG_TYPE = "connector_detected"
DRIFT_TRIGGER = "scheduled_fire_drift"


def _silent_default(silent: Optional[bool]) -> bool:
    """`silent` as the caller said it, else from the run mode the fire
    exported (`CR_FIRED_VIA=scheduled`). A typed chat never sets it."""
    if silent is not None:
        return bool(silent)
    import os as _os

    return str(_os.environ.get("CR_FIRED_VIA", "")).strip().lower() == "scheduled"


def _open_drift_flag(workspace_root: str, category: str,
                     candidate: Optional[str]) -> bool:
    """True when an unresolved flag for this category (and this candidate)
    is already on the ledger - a `connector_detected` with no later
    `connector_backend_changed` for the category. The composer dedups
    against it so a fire that re-finds the same drift appends nothing."""
    try:
        from events_io import iter_events
    except Exception:  # noqa: BLE001
        return False
    open_flag = False
    try:
        for ev in iter_events(workspace_root):
            if not isinstance(ev, dict):
                continue
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            kind = ev.get("type")
            if kind == "connector_backend_changed" and data.get("category") == category:
                open_flag = False
            elif kind == DRIFT_FLAG_TYPE and data.get("category") == category \
                    and data.get("candidate_server_id") == candidate:
                open_flag = True
    except Exception:  # noqa: BLE001
        return False
    return open_flag


def _drift_answer(workspace_root: str, category: str, descriptors,
                  declared: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """R-RW2-1, the HYBRID, for ONE category on a SILENT fire.

    None when there is no drift (the declared server or a recorded alias is
    in the registry, or nothing is declared). Otherwise `{resolved_via,
    discovered_server_id, candidates_count, flag_row, declared_for_ops}`:
    EXACTLY ONE server fingerprinting to the declared provider resolves the
    ops against it (`resolved_via: fingerprint`) and composes the flag that
    RECORDS it (`recorded_alias: true`), so the next fire matches exactly;
    zero or several is today's skip-and-flag (`recorded_alias: false`, the
    candidates named by count only - several mailboxes per customer is the
    normal case, and the match is never "any mailbox")."""
    if not declared or not declared.get("server_id"):
        return None
    from tool_discovery import detect_backend_drift

    drift = detect_backend_drift(descriptors, declared)
    if drift is None:
        return None
    candidates = list(drift.get("candidates") or [])
    one = drift.get("candidate_server_id")
    data: Dict[str, Any] = {
        "category": category,
        "declared_server_id": declared.get("server_id"),
        "provider": drift.get("candidate_provider") or declared.get("provider"),
        "fingerprint_matched": bool(one),
        "recorded_alias": bool(one),
        "n_candidates": len(candidates),
        "triggered_by": DRIFT_TRIGGER,
    }
    if one:
        data["server_id"] = one
        data["candidate_server_id"] = one
    flag = {"type": DRIFT_FLAG_TYPE, "source_skill": "inbox", "data": data}
    if _open_drift_flag(workspace_root, category, one):
        flag = None
    out: Dict[str, Any] = {
        "resolved_via": "fingerprint" if one else None,
        "discovered_server_id": one,
        "candidates_count": len(candidates),
        "flag_row": flag,
        "declared_for_ops": None,
    }
    if one:
        out["declared_for_ops"] = {"server_id": one, "server_ids": [one],
                                   "provider": declared.get("provider"),
                                   "label": declared.get("label")}
    else:
        out["reason"] = (
            f"the declared {category} connector is not in this run's tools "
            f"and {len(candidates)} connected servers look like it, so this "
            "leg was skipped and flagged for the next interactive chat")
    return out


def mail_seams(workspace_root: str,
               tools: Optional[List[Dict[str, Any]]] = None, *,
               silent: Optional[bool] = None,
               accounts: Optional[List[Any]] = None,
               discovery_ran: Optional[bool] = None) -> Dict[str, Any]:
    """The mail and calendar operations resolved through the seams, one answer.

    The declared backend wins, with its own operation vocabulary; nothing else
    is substituted for it, Zapier legs are excluded from native discovery, and
    an empty map is today's fingerprint fallback. A provider tool id is never
    named by the caller — that is the whole point of the seam.

    THE HYBRID ON A SILENT FIRE (IDENT1 I-1, ruling R-RW2-1). `silent`
    defaults from `CR_FIRED_VIA=scheduled`. When the declared server AND
    every recorded alias are absent from `tools` on a silent fire, EXACTLY
    ONE server fingerprinting to the declared provider resolves the ops
    (each seam says `resolved_via: fingerprint`) and the answer carries the
    composed `connector_detected` flag that records it (`flag_rows` - the
    composer reads, the caller appends through `append_jsonl`); zero or
    several candidates is today's skip-and-flag. On an interactive seat
    nothing changes: the drift reason stands and the person confirms a
    re-pin (workspace-manager's drift-detect flow).
    """
    from tool_discovery import ToolDescriptor, discover_for_category

    try:
        import connector_config
        # FIX3 F3-8. THE ROOT THIS HELPER WAS HANDED, not the one its cwd
        # happens to sit in. Omitting it made `load_entities(None)` read
        # nothing, so on 2026-09-21 a seat with a declared mail backend came
        # back `declared_email: null` and the chain fell through to substring
        # discovery — the fallback for a workspace that has declared NOTHING,
        # silently used on one that had. A helper never resolves a root it
        # was given.
        declared_mail = connector_config.declared_backend("email", workspace_root)
        declared_cal = connector_config.declared_backend("calendar", workspace_root)
    except Exception:  # noqa: BLE001 — nothing declared is a supported state
        declared_mail = declared_cal = None

    descriptors = [
        ToolDescriptor(tool_id=t.get("name") or t.get("tool_id") or "",
                       name=t.get("display") or t.get("name") or "",
                       description=t.get("description") or "")
        for t in (tools or [])
    ]
    out: Dict[str, Any] = {"declared_email": declared_mail,
                           "declared_calendar": declared_cal,
                           "email": {}, "calendar": {}}
    is_silent = _silent_default(silent)
    drift: Dict[str, Any] = {}
    use_mail, use_cal = declared_mail, declared_cal
    if is_silent:
        for category, declared in (("email", declared_mail),
                                   ("calendar", declared_cal)):
            try:
                answer = _drift_answer(workspace_root, category, descriptors,
                                       declared)
            except Exception:  # noqa: BLE001 - drift detection never blocks
                answer = None
            if answer is not None:
                drift[category] = answer
        if (drift.get("email") or {}).get("declared_for_ops"):
            use_mail = drift["email"]["declared_for_ops"]
        if (drift.get("calendar") or {}).get("declared_for_ops"):
            use_cal = drift["calendar"]["declared_for_ops"]
    for op in ("search", "draft", "send", "thread", "label"):
        out["email"][op] = _seam(_declared_seam(discover_for_category, use_mail),
                                 "email", op, descriptors, use_mail)
    for op in ("respond", "find", "create", "update"):
        out["calendar"][op] = _seam(_declared_seam(discover_for_category, use_cal),
                                    "calendar", op, descriptors, use_cal)
    if drift:
        for category, answer in drift.items():
            for seam in out[category].values():
                if answer.get("resolved_via"):
                    seam["resolved_via"] = answer["resolved_via"]
                elif answer.get("reason") and seam.get("tool_id") is None:
                    seam["reason"] = answer["reason"]
        out["drift"] = {c: {k: v for k, v in a.items() if k != "declared_for_ops"}
                        for c, a in drift.items()}
        out["flag_rows"] = [a["flag_row"] for a in drift.values()
                            if a.get("flag_row")]
    if is_silent:
        # IDENT1 I-12 (5): a silent fire READS. It never sends, labels,
        # archives or marks anything read - those are a person's clicks on
        # the widget, never the fire's own act.
        for op in SILENT_READ_ONLY_OPS:
            out["email"][op] = {"tool_id": None, "reason": SILENT_READ_ONLY_REASON,
                                "candidates_considered": None}
    # IDENT1 I-14: say how many tools this answer was computed over and
    # whether the fire loaded them first. An empty list is "never loaded",
    # never "no mail backend" - the merged fire shape's connectors are
    # deferred stubs until the host's tool search loads them.
    out["n_tools_seen"] = len(descriptors)
    out["discovery_ran"] = bool(discovery_ran)
    if not descriptors:
        for category in ("email", "calendar"):
            for seam in out[category].values():
                if seam.get("tool_id") is None and                         seam.get("reason") != SILENT_READ_ONLY_REASON:
                    seam["reason"] = TOOLS_NOT_LOADED_REASON
    if accounts is not None:
        # IDENT1 I-12 (2): the fetch runs once per linked account.
        out["fetch_plan"] = fetch_plan(workspace_root, accounts)
        out["n_accounts"] = len(out["fetch_plan"])
    return out


#: The DECLARED path's named seams (IDENT1 I-1). `discover_for_category`
#: called with a bare operation word substring-matches it against the tool
#: ids, which on Superhuman bound `send` to `undo_send`, `create` (calendar)
#: to `create_or_update_draft`, and found no `search` at all - so a fire whose
#: declaration DID resolve would still have read no mail. The mail seams and
#: the calendar seam carry each provider's own operation vocabulary; a
#: declared backend goes through them. An UNDECLARED seat keeps today's
#: answer exactly (the fallback reason), byte for byte.
_MAIL_SEAM_FNS = {"search": "discover_mail_search_tool",
                  "draft": "discover_mail_draft_tool",
                  "send": "discover_mail_send_tool",
                  "thread": "discover_mail_thread_fetch_tool"}
_CALENDAR_SEAM_OPS = {"respond": "respond_to_event", "find": "find_events",
                      "create": "create_event", "update": "update_event"}


def _declared_seam(fallback, declared):
    """The resolver for one seam: the named, vocabulary-aware seam when a
    backend is declared, today's category call otherwise."""
    if not (isinstance(declared, dict) and declared.get("server_id")):
        return fallback
    import tool_discovery as td

    def resolve(category, op, descriptors, declared=None):
        if category == "email" and op in _MAIL_SEAM_FNS:
            return getattr(td, _MAIL_SEAM_FNS[op])(descriptors, declared=declared)
        if category == "calendar" and op in _CALENDAR_SEAM_OPS:
            return td.discover_calendar_tool(descriptors, _CALENDAR_SEAM_OPS[op],
                                             declared=declared)
        return td.discover_for_category(
            category, op, descriptors, declared=declared,
            op_candidates=td._declared_op_candidates(
                (declared or {}).get("provider"), op, [op]))
    return resolve


def _seam(discover, category: str, op: str, descriptors, declared):
    try:
        found = discover(category, op, descriptors, declared=declared)
    except Exception as exc:  # noqa: BLE001
        return {"tool_id": None, "reason": f"{type(exc).__name__}: {exc}"}
    return {"tool_id": getattr(found, "tool_id", None),
            "reason": getattr(found, "reason", None),
            "candidates_considered": getattr(found, "candidates_considered", None)}


def renderer_preflight(workspace_root: str) -> Dict[str, Any]:
    """Phase 8 step 1, as a verb instead of an import line.

    The old block's whole job was to print `OK` or abort the fire, which meant
    the fire's first proof that its renderer exists was a string comparison on
    stdout. Here it is a named answer: `{ok, missing}` — and on a merged seat it
    proves the imports resolve in the RUNTIME beside the data, which is the
    thing that was actually in doubt.
    """
    missing = []
    for module, names in (
        ("widget_transport", ("render_and_persist",)),
        ("chat_output_renderer", ("validate_chat_output", "CANONICAL_ACTIONS",
                                  "CanonicalActionError", "LeakDetectedError",
                                  "WrapperContractError")),
    ):
        try:
            loaded = __import__(module)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{module} ({type(exc).__name__}: {exc})")
            continue
        for name in names:
            if not hasattr(loaded, name):
                missing.append(f"{module}.{name}")
    return {"ok": not missing, "missing": missing}


# ---------------------------------------------------------------------------
# Phase 5.4 — inbound capture, composed
# ---------------------------------------------------------------------------

def plan_inbound_capture(workspace_root: str, items: List[Dict[str, Any]], *,
                         user_person_id: str,
                         source_skill: str = TASK_ID,
                         provider: Optional[str] = None) -> Dict[str, Any]:
    """`{rows, counters, summary, errors}` — the commitments this fire would
    capture, composed and NOT written.

    The gate is `inbound_capture`'s own, piece by piece: the per-message
    idempotency check (`already_captured`), the declared below-bar exclusion,
    the per-fire volume cap, and the canonical event builder. What changes is
    only where the append happens — the caller's `plan append_jsonl`, one
    locked write, exactly as the module's own writer did it.
    """
    import inbound_capture as ic

    cap = getattr(ic, "DEFAULT_CAPTURE_CAP", 25)
    rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    n_candidates = n_below = n_deduped = n_capped = 0
    user_names = _user_names(workspace_root, user_person_id)

    for raw in items or []:
        n_candidates += 1
        if raw.get("below_bar"):
            n_below += 1
            continue
        if len(rows) >= cap:
            n_capped += 1
            continue
        try:
            title = str(raw.get("title") or "")
            message_id = str(raw.get("message_id") or "")
            if ic.already_captured(workspace_root, message_id, title,
                                   provider=provider):
                n_deduped += 1
                continue
            rows.append(ic.build_inbound_commitment_event(
                title,
                message_id=message_id,
                kind=str(raw.get("kind") or "promise"),
                direction=str(raw.get("direction") or ""),
                user_person_id=user_person_id,
                sender_person_id=str(raw.get("sender_person_id") or ""),
                sender_name=str(raw.get("sender_name") or ""),
                user_names=user_names,
                ts=str(raw.get("ts") or ""),
                due=raw.get("due"),
                no_due=bool(raw.get("no_due")),
                evidence=str(raw.get("evidence") or ""),
                primary_thread_id=raw.get("primary_thread_id"),
                person_ids=raw.get("person_ids"),
                classification_confidence=raw.get("classification_confidence"),
                source_skill=source_skill,
                provider=provider,
                thread_id=raw.get("thread_id"),
            ))
        except Exception as exc:  # noqa: BLE001 — a bad item never takes the
            # fire down; it is counted and named, which is what separates a
            # quiet mailbox from a dead rail.
            errors.append({"message_id": raw.get("message_id"),
                           "reason": f"{type(exc).__name__}: {exc}"})

    directions = [str((r.get("data") or {}).get("direction") or "")
                  for r in rows]
    counters = {
        "n_candidates": n_candidates, "n_captured": len(rows),
        "n_deduped": n_deduped, "n_below_bar": n_below,
        "n_capped": n_capped, "n_errors": len(errors),
        # FIX3 F3-11 — the same number under the name the RECEIPT will
        # use for it, so the two counts stop colliding at Phase 7.
        # `n_errors` stays for one release, for readers that have it.
        "n_capture_errors": len(errors),
        # The two the summary line names: they are what tell "three people owe
        # you something" from "three replies are on your hook".
        "n_waiting_on": directions.count(ic.DIRECTION_WAITING_ON),
        "n_reply_owed": directions.count(ic.DIRECTION_REPLY_OWED),
    }
    return {"rows": rows, "counters": counters, "errors": errors,
            "summary": ic._summary(dict(counters), cap), "cap": cap}


def _user_names(workspace_root: str, user_person_id: str) -> List[str]:
    """Every name and address the primary user answers to, for the direction
    doctrine: the CEO's own messages never create waiting-on-them items."""
    try:
        from entity_resolve import resolve

        record = resolve(workspace_root, user_person_id) or {}
    except Exception:  # noqa: BLE001
        return []
    names = [record.get("name") or "", record.get("email") or ""]
    names += list(record.get("aliases") or [])
    return [str(n) for n in names if n]


# ---------------------------------------------------------------------------
# Phase 5.5 — the CRU pass, matched here and closed elsewhere
# ---------------------------------------------------------------------------

def plan_inbound_reconcile(workspace_root: str,
                           inbound_messages: List[Dict[str, Any]], *,
                           user_person_id: str,
                           source_skill: str = TASK_ID,
                           exclude_captured_since: Optional[str] = None,
                           provider: Optional[str] = None,
                           fetch_blocked: Optional[str] = None) -> Dict[str, Any]:
    """The inbound reply scan: what it MATCHED, and the rows that follow.

    `rows` are the review proposals and the schedule-shift markers — both have
    public builders, so both are composed here and appended by the caller
    through `plan append_jsonl`. `closures` are the auto-close candidates and
    they are NOT written: closing runs through the single closure path, which
    is a gated writer with its own receipts and reversals, and a helper that
    could reach it would be `run_helper` writing (review B-1). The caller
    surfaces nothing either way — this pass has always been silent.

    `fetch_blocked` is carried through untouched: a fire that could not read
    mail at all must not be recorded as a fire that read everything and found
    nothing.
    """
    from cru_match import (build_pending_review_event, cru_eligible,
                           load_open_commitments)
    from reconcile_inbound_commitments import reconcile_inbound

    if fetch_blocked:
        return {"rows": [], "closures": [], "blocked": fetch_blocked,
                "counters": {"n_fetched": 0, "n_auto_close": 0, "n_pending": 0,
                             "n_updated": 0, "n_held": 0},
                "signal_fields": {}}

    events_path = str(Path(workspace_root) / "_hq" / "data" /
                      "events.jsonl")
    opens = cru_eligible(load_open_commitments(events_path))
    matched = reconcile_inbound(
        opens, inbound_messages or [],
        user_person_id=user_person_id,
        provider=provider,
        exclude_captured_since=exclude_captured_since,
        workspace_root=workspace_root,
    )
    rows: List[Dict[str, Any]] = []
    for proposal in matched.get("pending") or []:
        row = build_pending_review_event(
            commitment_id=proposal.get("commitment_id") or "",
            primary_thread_id=proposal.get("primary_thread_id") or "",
            source_skill=source_skill,
            proposed_resolution="auto_resolve",
            score=proposal.get("score") or 0,
            evidence=proposal.get("evidence") or "matched their reply",
            next_seq=None,
            title=proposal.get("title") or "",
            evidence_ts=proposal.get("ts") or None,
            has_completion_signal=proposal.get("has_completion_signal"),
        )
        rows.append(row)
    for shifted in matched.get("updated") or []:
        rows.append(_updated_row(shifted, source_skill))
    counters = {
        "n_fetched": len(inbound_messages or []),
        "n_auto_close": len(matched.get("auto_close") or []),
        "n_pending": len(matched.get("pending") or []),
        "n_updated": len(matched.get("updated") or []),
        "n_held": len(matched.get("held") or []),
    }
    return {"rows": rows,
            "closures": list(matched.get("auto_close") or []),
            "held": list(matched.get("held") or []),
            "counters": counters,
            "signal_fields": dict(matched.get("signal_fields") or {}),
            "blocked": None}


def _updated_row(shifted: Dict[str, Any], source_skill: str) -> Dict[str, Any]:
    from reconcile_inbound_commitments import build_commitment_updated_event

    return build_commitment_updated_event(
        commitment_id=shifted.get("commitment_id") or "",
        primary_thread_id=shifted.get("primary_thread_id") or "",
        source_skill=source_skill,
        change_summary="Counter-party shifted their own deadline (inbound mail)",
        evidence=shifted.get("evidence") or "matched their reply",
        next_seq=None,
    )


# ---------------------------------------------------------------------------
# Phase 8 — the page, rendered where the data is
# ---------------------------------------------------------------------------

def _inside_runtime_cache(path: Path) -> bool:
    """True when `path` lies under a workspace's installed runtime cache
    (`_hq/.cache/cr-runtime/...`)."""
    parts = [p.lower() for p in Path(path).parts]
    for i in range(len(parts) - 2):
        if parts[i:i + 3] == ["_hq", ".cache", "cr-runtime"]:
            return True
    return False


def _base_cwd():
    """Run the render from the directory the pre-door CLI ran it from, on
    every seat but the VM (ruling R-ORCH2C-1, ORCH2C's review).

    Through the door a helper child starts INSIDE the workspace folder, so the
    renderer's chat-email backstop finds the workspace from its working
    directory and emits `gate_ran` audit rows. On a legacy seat the old CLI ran
    from the plugin root, found no workspace and wrote none - and the gate
    report's dark rule reads a seat whose audit suddenly appears and then goes
    quiet as a gate that stopped. So a legacy or local render runs from the
    plugin root, byte for byte today's rows; the VM keeps the rows (they come
    back in `pending_rows` for the caller's append)."""
    import contextlib
    import os as _os

    @contextlib.contextmanager
    def moved():
        if str(_os.environ.get("CR_HOST_MODE", "") or "").strip().lower() == "vm":
            yield
            return
        prev = _os.getcwd()
        target = Path(__file__).resolve().parents[2]
        if _inside_runtime_cache(target):
            # A runtime staged INSIDE the workspace (what `plan run_helper`
            # renders on a legacy seat with an installed runtime): its base
            # directory is inside the workspace, where the renderer would find
            # it again. Render from the system temp directory instead - no
            # workspace above it, so no gate audit (fix pass 1, M-2).
            import tempfile as _tempfile

            target = Path(_tempfile.gettempdir())
        try:
            _os.chdir(str(target))
        except OSError:
            yield
            return
        try:
            yield
        finally:
            _os.chdir(prev)

    return moved()


def quiet_sub_header(data_view: Dict[str, Any]) -> Dict[str, Any]:
    """IDENT1 I-10 (cosmetic, carried honestly). A fire that read NOTHING
    (`n_fetched == 0`) has no noise to report, so the "Noise filtered: ..."
    sub-header - zeros in every bucket on 2026-09-22 - is replaced by the one
    summary line the view carries (`summary_line`), or dropped. A view that
    does not say `n_fetched` renders exactly as before."""
    if not isinstance(data_view, dict) or data_view.get("n_fetched") != 0:
        return data_view
    sub = str(data_view.get("sub_header") or "")
    if not sub.lower().startswith("noise filtered"):
        return data_view
    view = dict(data_view)
    summary = str(view.get("summary_line") or "").strip()
    if summary:
        view["sub_header"] = summary
    else:
        view.pop("sub_header", None)
    return view


def render_inbox_page(workspace_root: str, data_view: Dict[str, Any], *,
                      wrapper: str = "fragment",
                      name_hint: str = TASK_ID) -> Dict[str, Any]:
    """`{html, page_rel, bytes_len}` — the sealed widget, rendered in the VM.

    The renderer belongs where the data is: the view it validates carries the
    customer's own names and subjects, and the leak scan compares paths against
    the resolved workspace. So the render runs here, through the same renderer
    and the same wrapper-contract validator the canonical transport runs, and
    the HTML comes back in the envelope for `mcp__visualize__show_widget`.

    The persisted audit page is NOT written here — `page_rel` names where it
    goes and the caller lands it with `plan write`, which is the only write
    door. `tests/run_orch1_test.py` pins this HTML byte-for-byte against
    `widget_transport.render_and_persist`, so the two renders cannot diverge.
    """
    from chat_output_renderer import (render_chat_output_widget,
                                      validate_rendered_widget)
    from widget_transport import _safe_filename

    data_view = quiet_sub_header(data_view)
    with _captured_appends() as captured, _base_cwd():
        html = render_chat_output_widget(data_view, wrapper=wrapper)
        validate_rendered_widget(html, surface=data_view.get("surface"))
    stamp = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    surface = name_hint or data_view.get("surface") or "widget"
    page_rel = f"{WIDGET_DIR_REL}/{_safe_filename(surface)}_{stamp}.html"
    # The gate audit the render emits is the ledger's evidence that this page
    # went through the gates rather than being hand-composed, so it comes back
    # for the caller's append instead of being written from a read verb.
    return {"html": html, "page_rel": page_rel, "bytes_len": len(html),
            "wrapper": wrapper, "pending_rows": list(captured)}


# ---------------------------------------------------------------------------
# IDENT1 I-12 - the widget tool is absent on the device shape: the surface
# still lands, in the shape of the app's own "Inbox triage" template
# (rulings R-RW2-7 (a), R-RW2-8 (a), R-RW2-9; Probes B, C, C2, E)
# ---------------------------------------------------------------------------

#: The widget tool, and the two delivery tools the device shape carries.
WIDGET_TOOL = "mcp__visualize__show_widget"


def widget_tool_name(tools) -> Optional[str]:
    """The widget tool in this run's tool list, by the name the host lists it
    under, or None (WIDGETLOAD1, cr1#100). The literal name first; else any
    id whose last segment is `show_widget` - on the merged seat the host can
    list the same tool under a server-id prefix, and a listed deferred tool
    counts as present. A run with no such name at all is the text shape."""
    names = _tool_names(tools)
    if WIDGET_TOOL in names:
        return WIDGET_TOOL
    for n in sorted(names):
        if n == "show_widget" or n.endswith("__show_widget"):
            return n
    return None
SEND_MESSAGE_TOOL = "SendUserMessage"
SEND_FILE_TOOL = "SendUserFile"
ARTIFACT_TOOL = "Artifact"

#: The four groups of the text surface, in the order they render. A group
#: with no items is omitted, never rendered empty.
TEXT_GROUPS = (("urgent", "Urgent"), ("this_week", "This week"),
               ("fyi", "FYI"), ("skip", "Skip"))

#: The line that closes the text surface, naming where the full page went.
WIDGET_ABSENT_LINE = ("The live view could not be shown in this chat, so here "
                      "is the list; the full page is saved at `{path}`.")
WIDGET_ABSENT_WORDS_LINE = ("The live view could not be shown in this chat, so "
                            "here is the list; the full page is saved under "
                            "{rel} in your Command Room folder.")

#: The ONE fixed line that offers the draft affordance on the text surface.
#: Exempt from the unattended-ask family BY IDENTITY (I-16) - never by
#: wording; a fire that composes its own version of it is refused.
DRAFT_HINT_LINE = ("To queue one of these drafts, reply with draft and its "
                   "number; nothing is sent until you do.")

#: The ONE row form the three text surfaces share - this inbox list, and My
#: Plate / Staff Meeting through `surface_text._row_line` (ROUTE2; review B
#: N-2, walk F-OFF-11). A row written `4. ...` is a Markdown ordered-list
#: item, and the chat renumbers a run of them from its first number: rows 1
#: and 4 show as 1 and 2, and `draft 4` then acts on a row the customer never
#: saw numbered 4. The bold number the chat renderer already heads an item
#: with (`**4.**`, `chat_output_renderer._render_item`; the validator's
#: `ITEM_NUMBER_PATTERN`) is text, never a list marker, so the number the
#: customer sees IS the row's action key (`n` / `display_n`, unchanged).
TEXT_ROW_FORM = "**{n}.** {text}"


def text_row_line(n: Any, item: Dict[str, Any]) -> str:
    """One row of a text surface in `TEXT_ROW_FORM`: the row's own number,
    then the name, the subject and the one-line why, dot-separated. Not
    validated here - each composer passes it through its own gate as content
    (the row carries the customer's words)."""
    parts = [str(item.get("name") or "").strip(),
             str(item.get("subject") or "").strip(),
             str(item.get("context_tag") or item.get("why") or "").strip()]
    return TEXT_ROW_FORM.format(n=n, text=" · ".join(p for p in parts if p))

#: The push text a fire sends, per outcome - counts only, never a tool name,
#: never a stop message it composed itself (I-12 (vi), I-14 (2)).
PUSH_LINES = {
    "delivered": "Inbox: {urgent} urgent, {this_week} this week; it is ready in your Inbox chat.",
    "text_fallback": "Inbox: {urgent} urgent, {this_week} this week; the list is in your Inbox chat.",
    "stopped": "{line}",
}

#: The operations a silent fire never resolves: it reads, it never acts on a
#: mailbox (I-12 (5)). A person's click on the widget is the only door to them.
SILENT_READ_ONLY_OPS = ("send", "label", "archive", "mark_read")
SILENT_READ_ONLY_REASON = "silent_fire_read_only"


def _fire_mode() -> Optional[str]:
    """This process's run mode when it is a fire's (I-16), else None."""
    import os as _os

    via = str(_os.environ.get("CR_FIRED_VIA", "") or "").strip().lower()
    return via if via in ("scheduled", "catchup") else None


def _validated(line: str, *, content: bool = False) -> str:
    """A composed line, or a raise naming the family that refused it. On a
    fire's turn the unattended-ask family reads it too (I-16) - but only the
    fire's OWN composed sentences: `content=True` marks a line that carries
    the customer's mail (a subject, a draft preview), which may end in a
    question mark and is never the fire asking one (IDENT1 fix pass 1, B-1)."""
    from chat_output_validator import validate_chat_output

    result = validate_chat_output(line, fired_via=None if content else _fire_mode())
    if not getattr(result, "ok", True):
        raise ValueError("a composed inbox line did not pass the gate: "
                         + "; ".join(str(v) for v in result.violations))
    return line


def _item_group(item: Dict[str, Any]) -> str:
    """Which of the four groups an item belongs to.

    The fire's own classification wins (`item["group"]`). Otherwise the item's
    shape decides: a calendar invite is this week's, a row the customer can
    act on now (a reply with a draft, a send) is urgent, a read-only tracked
    row is FYI, and a row marked noise is Skip."""
    group = str(item.get("group") or "").strip().lower().replace(" ", "_")
    if group in dict(TEXT_GROUPS):
        return group
    if item.get("noise") or item.get("skip"):
        return "skip"
    actions = " ".join(str(a) for a in (item.get("actions") or [])).lower()
    if "accept" in actions or item.get("icon") == "\U0001f4c5":
        return "this_week"
    if "send" in actions or "draft" in actions or item.get("body_lines"):
        return "urgent"
    return "fyi"


def _draft_preview(item: Dict[str, Any]) -> List[str]:
    """The draft under an item, per the email-preview convention: a
    blockquote with bold To / Subject, then the body."""
    body = [str(b) for b in (item.get("body_lines") or []) if str(b).strip()]
    if not body:
        return []
    meta = {str(k).lower(): str(v) for k, v in (item.get("metadata") or [])
            if isinstance(k, str)}
    out = []
    if meta.get("to"):
        out.append(f"> **To:** {meta['to']}")
    if meta.get("subject"):
        out.append(f"> **Subject:** {meta['subject']}")
    if out:
        out.append(">")
    out.extend(f"> {line}" for line in body)
    return out


def render_inbox_text(workspace_root: str, data_view: Dict[str, Any]) -> Dict[str, Any]:
    """The inbox surface as the app's own template shapes it, for a run with
    no widget tool: `{text, groups, pending_rows}`.

    Four headings - Urgent / This week / FYI / Skip - one line per item
    carrying the SAME row number the widget's action grammar uses (so `draft 3`
    means the same row in both surfaces), the sender as the view names it, the
    subject and the one-line why; a drafted item carries its PREVIEW under the
    line (nothing touches the mailbox here). Every line goes through the gate;
    the gate audit rows the render emits come back for the caller's append,
    exactly as the widget render's do."""
    groups: Dict[str, List[Dict[str, Any]]] = {key: [] for key, _t in TEXT_GROUPS}
    for section in data_view.get("sections") or []:
        for item in section.get("items") or []:
            groups[_item_group(item)].append(item)
    lines: List[str] = []
    with _captured_appends() as captured:
        header = str(data_view.get("header") or "").strip()
        if header:
            lines.append(_validated(header))
        any_draft = False
        for key, title in TEXT_GROUPS:
            items = groups[key]
            if not items:
                continue
            lines.append("")
            lines.append(_validated(f"**{title}**"))
            for item in items:
                lines.append(_validated(text_row_line(item.get("n"), item), content=True))
                preview = _draft_preview(item)
                if preview:
                    any_draft = True
                    for pline in preview:
                        lines.append(_validated(pline, content=True))
        if any_draft:
            lines.append("")
            lines.append(_validated(DRAFT_HINT_LINE))
        try:
            from chat_output_renderer import validate_chat_output as _gate

            _gate("\n".join(lines))
        except ImportError:
            pass
    return {"text": "\n".join(lines).strip() + "\n",
            "groups": {k: [i.get("n") for i in v] for k, v in groups.items()},
            "counts": {k: len(v) for k, v in groups.items()},
            "pending_rows": list(captured)}


def push_line(kind: str, *, urgent: int = 0, this_week: int = 0,
              line: str = "") -> str:
    """The ONE push sentence for this outcome, from `PUSH_LINES`, validated."""
    template = PUSH_LINES.get(kind)
    if template is None:
        raise ValueError(f"no push line for {kind!r}")
    return _validated(template.format(urgent=int(urgent), this_week=int(this_week),
                                      line=line))


def widget_absent_line(page_rel: str, device_root: Optional[str] = None) -> str:
    """The closing line of the text surface: the page's path on the
    customer's own computer in a code span, or the words form."""
    import deliverables as dl

    if device_root:
        device = dl.device_join(device_root, page_rel)
        return _validated(WIDGET_ABSENT_LINE.format(path=dl.opener_spelling(device)))
    return _validated(WIDGET_ABSENT_WORDS_LINE.format(rel=page_rel))


def _tool_names(tools) -> set:
    names = set()
    for t in tools or []:
        if isinstance(t, str):
            names.add(t)
        elif isinstance(t, dict):
            names.add(str(t.get("name") or t.get("tool_id") or ""))
    return names


#: Where the fire puts its container-side copy of the landed page (relative
#: to the fire's own working directory - never the customer's folder).
CONTAINER_PAGE_DIR = "cr-pages"


def _landed_page(workspace_root: str, page_rel: str) -> Optional[str]:
    """The landed page's text, byte for byte (read, never written), or None."""
    try:
        path = (Path(str(workspace_root)) / str(page_rel)).resolve()
        root = Path(str(workspace_root)).resolve()
        path.relative_to(root)
        return path.read_bytes().decode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def plan_delivery(workspace_root: str, *, tools, page_rel: str, text: str = "",
                  counts: Optional[Dict[str, int]] = None, n_accounts: int = 1,
                  device_root: Optional[str] = None,
                  task_id: str = TASK_ID) -> Dict[str, Any]:
    """How THIS run hands the surface over, decided from the tools it has.

    `show_widget` present -> today's path, unchanged. ABSENT (the device
    shape, Probes B/C/C2) -> the page is landed and named, the text surface is
    delivered through `SendUserMessage` (or posted as the chat turn when that
    is absent too), the page follows through `SendUserFile` when present, and
    the push is the counts-only sentence. The Artifact republish is gated by
    the workspace's own setting (`fire_delivery`), republishes only a STORED
    page, page-only, and otherwise composes ONE flag row. Nothing here sends,
    publishes or writes: it answers the calls to make and the rows to land."""
    import fire_delivery as fd

    if device_root is None:
        # Through the read door the customer's own folder path is FORWARDED
        # (`CR_DEVICE_WORKSPACE`, on the rendered line) - an argument naming
        # it would be refused by the door's path fence, since it is outside
        # the workspace the door can see (fix pass 1, found while pinning M-4).
        import os as _os

        device_root = str(_os.environ.get("CR_DEVICE_WORKSPACE", "") or "").strip() or None
    names = _tool_names(tools)
    counts = dict(counts or {})
    urgent, week = int(counts.get("urgent") or 0), int(counts.get("this_week") or 0)
    out: Dict[str, Any] = {"calls": [], "rows": [], "lines": []}
    widget_tool = widget_tool_name(tools)
    if widget_tool:
        out.update({
            "branch": "widget",
            "push": push_line("delivered", urgent=urgent, this_week=week),
            "receipt_flags": {"widget_rendered": True, "widget_posted": True,
                              "text_fallback": False, "n_accounts": int(n_accounts),
                              "push_planned": True},
        })
        out["calls"].append({"tool": widget_tool})
        return out
    closing = widget_absent_line(page_rel, device_root)
    body_lines = [text.rstrip("\n"), "", closing] if text else [closing]
    flags = {"widget_rendered": True, "widget_posted": False, "text_fallback": True,
             "n_accounts": int(n_accounts), "push_planned": True}
    page_path = page_rel
    stage = None
    if device_root:
        # THE CONTAINER CANNOT READ THE DEVICE (fix pass 1, M-4). The tools
        # that hand the page over (`SendUserFile`, the Artifact republish) run
        # in the container, so they get a container-side COPY of the landed
        # bytes - planned here, read here beside the data - never the path on
        # the customer's computer.
        import hashlib as _hashlib

        landed = _landed_page(workspace_root, page_rel)
        page_path = None
        if landed is not None:
            page_path = CONTAINER_PAGE_DIR + "/" + Path(str(page_rel)).name
            stage = {"tool": "Write", "file_path": page_path, "content": landed,
                     "sha256": _hashlib.sha256(landed.encode("utf-8")).hexdigest()}
    if fd.artifact_publish_enabled(workspace_root):
        url = fd.stored_artifact_url(workspace_root, task_id)
        if page_path is None:
            # the page did not land: that is the reason, whatever the tool
            # or the stored address (seam S-B, fix pass 2)
            out["rows"].append(fd.flag_row(task_id, fd.FLAG_NO_PAGE))
        elif ARTIFACT_TOOL in names and url:
            out["calls"].append(fd.republish_call(url, page_path))
            # MF-23 (merged-tree review F-10; I-12 (6)): the saved-at line
            # stays LAST - the one page line goes directly above it.
            body_lines.insert(len(body_lines) - 1,
                              _validated(fd.ARTIFACT_LINE.format(url=url)))
            flags["artifact_url"] = url
            out["on_refused_row"] = fd.flag_row(task_id, fd.FLAG_REFUSED)
        else:
            out["rows"].append(fd.flag_row(
                task_id, fd.FLAG_NO_URL if ARTIFACT_TOOL in names else fd.FLAG_NO_TOOL))
    message = "\n".join(body_lines).strip() + "\n"
    if SEND_MESSAGE_TOOL in names:
        out["calls"].insert(0, {"tool": SEND_MESSAGE_TOOL, "text": message})
    else:
        out["calls"].insert(0, {"tool": "chat_turn", "text": message})
    if SEND_FILE_TOOL in names and page_path is not None:
        out["calls"].append({"tool": SEND_FILE_TOOL, "path": page_path})
    if stage is not None and any(c["tool"] in (SEND_FILE_TOOL, ARTIFACT_TOOL)
                                 for c in out["calls"]):
        out["calls"].insert(1, stage)
    out.update({"branch": "text", "message": message,
                "push": push_line("text_fallback", urgent=urgent, this_week=week),
                "receipt_flags": flags})
    return out


def typed_reply_action(text: str, data_view: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """`draft 3` typed into the task chat, read as the SAME `{n, action}` the
    widget's own button for row 3 would send (R-RW2-8 (a); Probe E: the reply
    arrives under the replying device's composer, so it is resolved by the
    row number and the page, never by the fire's transcript). None when the
    row has no such action."""
    import re as _re

    m = _re.match(r"^\s*(?:(draft|send|done)\s+(\d+)|(\d+)\s+(draft|send|done))\s*$",
                  str(text or "").strip(), _re.IGNORECASE)
    if not m:
        return None
    verb = (m.group(1) or m.group(4)).lower()
    n = int(m.group(2) or m.group(3))
    for section in data_view.get("sections") or []:
        for item in section.get("items") or []:
            if item.get("n") != n:
                continue
            for action in item.get("actions") or []:
                if str(action).strip().lower() == f"{n} {verb}":
                    return {"n": n, "action": verb}
    return None


def fetch_plan(workspace_root: str, accounts: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """The mail fetch, once per linked account (the connector's own rule:
    never answer from the primary account alone when several are linked).

    `accounts` is the connector's `list_accounts` answer (addresses, or dicts
    carrying one). One account (or none listed) -> ONE fetch with no
    `acting_email`, byte for byte today's path. Several -> one fetch per
    address with `acting_email`; an address the workspace classified as
    personal is left out (the F-3 scope), an unclassified one is in scope."""
    addresses = []
    for a in accounts or []:
        addr = a.get("email") or a.get("address") if isinstance(a, dict) else a
        addr = str(addr or "").strip()
        if addr and addr not in addresses:
            addresses.append(addr)
    if len(addresses) <= 1:
        return [{"acting_email": None}]
    try:
        import connector_config as cc

        roles = {str(r.get("address") or "").strip().lower(): r
                 for r in cc.accounts(workspace_root)}
    except Exception:  # noqa: BLE001 - scope falls back to every account
        roles = {}
    plan = []
    for addr in addresses:
        rec = roles.get(addr.lower()) or {}
        if str(rec.get("role") or "").lower() == "personal":
            continue
        plan.append({"acting_email": addr})
    return plan or [{"acting_email": None}]


# ---------------------------------------------------------------------------
# IDENT1 I-14 - the fire loads its connector tools before it looks for them
# (Probes C / C2: on the merged fire shape the connectors are DEFERRED stubs,
# display-named, loaded through the host's tool search)
# ---------------------------------------------------------------------------

#: The categories this fire resolves through the seams.
CONNECTOR_DISCOVERY_CATEGORIES = ("email", "calendar")

#: The fallback queries when a category declares nothing: the provider names
#: the capability manifest knows for those categories, product words only.
CONNECTOR_DISCOVERY_QUERIES = ("Superhuman", "Gmail", "Outlook", "Google Calendar")

#: The one sentence when the tools never load, even after one refresh. It
#: says what was skipped; it never asks, never suggests reconnecting (I-16).
CONNECTOR_ABSENT_LINE = ("Your mail was not read this time, because its "
                         "connection was not available to this scheduled chat.")

#: The reason a seam gives when the fire looked before it loaded anything.
TOOLS_NOT_LOADED_REASON = ("tools not loaded: the connector tools were never "
                           "loaded in this run, so nothing could be resolved")


def connector_discovery_queries(workspace_root: str) -> List[str]:
    """The words to load the connectors by: each declared backend's LABEL
    (the product's name, e.g. "Superhuman"), else the fallback provider
    names. Derived from the declaration, so a re-declared connector changes
    the step by itself."""
    out: List[str] = []
    try:
        import connector_config as cc

        for category in CONNECTOR_DISCOVERY_CATEGORIES:
            row = cc.declared_backend(category, entities=cc.load_entities(workspace_root))
            label = str((row or {}).get("label") or "").split(" (")[0].strip()
            if label and label not in out:
                out.append(label)
    except Exception:  # noqa: BLE001 - an unreadable declaration falls back
        out = []
    return out or list(CONNECTOR_DISCOVERY_QUERIES)


def connector_discovery_step(workspace_root: str) -> Dict[str, Any]:
    """The discovery step the inbox fire runs BEFORE it looks for its mail
    tools, rendered from the declaration: `{step, queries}`.

    On the merged seat's fire shape every connector arrives as a deferred
    stub; a fire that looks before it loads sees none and would read that
    as "no mail backend". So: load by the host's own tool search, refresh
    the tool list ONCE if nothing loaded, then pass what you can see to the
    seams. Still nothing -> the one composed sentence; never a question."""
    queries = connector_discovery_queries(workspace_root)
    quoted = ", ".join(f'"{q}"' for q in queries)
    lines = [
        "Before looking for the mail and calendar tools, LOAD them:",
        f"1. Run the host's tool search for each of {quoted}.",
        "2. If nothing loaded, refresh the tool list once and search again.",
        "3. Pass the tools you can now see to the seams, with discovery_ran true.",
        "4. Still none: this run skips the mail leg and says, as its only line about it:",
        f"   {CONNECTOR_ABSENT_LINE}",
        "   Never ask, never suggest reconnecting; the seams record a flag.",
    ]
    return {"step": "\n".join(lines), "queries": queries,
            "absent_line": _validated(CONNECTOR_ABSENT_LINE)}


# ---------------------------------------------------------------------------
# Phase 7 — the one receipt
# ---------------------------------------------------------------------------

#: IDENT1 I-5 (R-FIX3-4): the one sentence the receipt composer refuses with
#: when the fire read mail and the record of that read has not landed.
CONNECTOR_READ_MISSING_LINE = (
    "The mail this run read has not been recorded yet, so its summary was "
    "not saved.")
CONNECTOR_READ_LEG = "inbox-fire"


class ConnectorReadMissing(RuntimeError):
    """The fire read mail and its `connector_read` row is not on the ledger.

    THE WALK (2026-09-22). The fire COMPOSED the read row and never appended
    it - composed is not landed - and its receipt went out anyway. The
    receipt phase now cannot complete until the read row lands: the door
    turns this into `reason: connector_read_missing` with the sentence."""

    cr_refusal_reason = "connector_read_missing"

    def __init__(self, line: str = CONNECTOR_READ_MISSING_LINE):
        super().__init__(line)
        self.line = line
        self.cause = "connector_read_missing"


def _connector_read_landed(workspace_root: str, since: Optional[str]) -> bool:
    """True when the ledger carries a `connector_read` row for the inbox
    fire's leg at or after `since` (the fire's own start). No `since` = any
    such row."""
    import datetime as _dt

    try:
        from events_io import iter_events
        from event_time import event_dt, parse_ts
    except Exception:  # noqa: BLE001
        return True
    floor = None
    if since:
        try:
            floor = parse_ts(str(since))
        except Exception:  # noqa: BLE001
            floor = None
    if floor is not None and floor.tzinfo is None:
        floor = floor.replace(tzinfo=_dt.timezone.utc)
    try:
        for ev in iter_events(workspace_root):
            if not isinstance(ev, dict) or ev.get("type") != "connector_read":
                continue
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if data.get("leg") != CONNECTOR_READ_LEG:
                continue
            if floor is None:
                return True
            at = event_dt(ev)
            if at is None:
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=_dt.timezone.utc)
            if at >= floor:
                return True
    except Exception:  # noqa: BLE001 - a reader never blocks on its own error
        return True
    return False


def plan_fire_receipt(workspace_root: str, *, task_id: str = TASK_ID,
                      fired_via: Optional[str] = None,
                      receipt_type: str = "pack_run",
                      status: str = "complete",
                      surfaced: Optional[int] = None,
                      duration_ms: Optional[int] = None,
                      late_tier: Optional[str] = None,
                      telemetry: Optional[Dict[str, Any]] = None,
                      extra_data: Optional[Dict[str, Any]] = None
                      ) -> Dict[str, Any]:
    """The fire's ONE receipt, composed exactly as `receipts.log_receipt`
    composes it — and handed back instead of appended.

    Every piece is that module's own: its task-id and fired-via normalisers,
    its canonical vocabularies, its machine fields, ITS MODEL FIELDS, its
    lateness field name, and `late_fire.slot_provenance` for where this fire
    landed relative to its slot. Nothing is a literal written here, and the
    suite compares this row against what `log_receipt` actually writes for the
    same input, so drift is a red check rather than a shape that reads right.
    The model stamp was the one piece that had drifted: `log_receipt` has
    stamped it since the §0.6 ruling and this composer never did, so the gate
    walk's fire receipt had no `data.model` on a seat whose harness named one.

    `telemetry` is the BLOCK, landing at `data.telemetry`. The walk's fire
    wrote `data.telemetry.telemetry` because the producer returns a wrapper
    and the caller handed the wrapper through as the value; a wrapper arriving
    either way is unwrapped here (`receipts.telemetry_block`), so the row has
    one shape no matter which caller composed it.
    """
    import receipts as rc

    # IDENT1 I-0 — the writer is named FIRST, exactly as `log_receipt` names
    # it. The composer writes nothing, but the row it hands back is appended
    # by the caller, and a row with no writer on a merged seat is the row
    # R-RW2-3 refuses. The door turns the raise into
    # `reason: writer_identity_required` with the one sentence.
    rc.require_writer_identity(workspace_root=workspace_root)
    # IDENT1 I-5 (R-FIX3-4): a fire that READ mail owes the read row BEFORE
    # its receipt. `n_fetched` and `fire_started_at` ride in `extra_data`;
    # none fetched (the mail leg dark) owes no read row.
    extra = extra_data if isinstance(extra_data, dict) else {}
    try:
        fetched = int(extra.get("n_fetched") or 0)
    except (TypeError, ValueError):
        fetched = 0
    if fetched > 0 and not _connector_read_landed(
            workspace_root, extra.get("fire_started_at")):
        raise ConnectorReadMissing()
    return _compose_fire_receipt(
        workspace_root, task_id=task_id, fired_via=fired_via,
        receipt_type=receipt_type, status=status, surfaced=surfaced,
        duration_ms=duration_ms, late_tier=late_tier, telemetry=telemetry,
        extra_data=extra_data)


def _compose_fire_receipt(workspace_root: str, *, task_id: str = TASK_ID,
                          fired_via: Optional[str] = None,
                          receipt_type: str = "pack_run",
                          status: str = "complete",
                          surfaced: Optional[int] = None,
                          duration_ms: Optional[int] = None,
                          late_tier: Optional[str] = None,
                          telemetry: Optional[Dict[str, Any]] = None,
                          extra_data: Optional[Dict[str, Any]] = None
                          ) -> Dict[str, Any]:
    """`plan_fire_receipt`'s row, WITHOUT its two preconditions (INBOXDRIVE1).

    Private on purpose - never on an allow-list, so no door can reach the
    receipt past its fences. The one-command driver composes the receipt
    through this FIRST, before anything is written, so a vocabulary error
    refuses the fire while the book is still untouched; the row it actually
    appends comes from `plan_fire_receipt`, fences and all, after the read row
    has landed."""
    import receipts as rc

    canonical = rc.normalize_task_id(task_id)
    if canonical not in rc.CANONICAL_TASK_IDS:
        raise ValueError(f"unknown task_id {task_id!r}")
    if receipt_type not in rc.ALL_RECEIPT_TYPES:
        raise ValueError(f"{receipt_type!r} is not a registered receipt type")
    if receipt_type not in rc.RECEIPT_TYPES[canonical]["types"]:
        raise ValueError(f"receipt type {receipt_type!r} does not belong to "
                         f"task {canonical!r}")
    # F2-4: the orchestrator passes `receipt_fired_via` and always should; a
    # call that omits it reads `CR_FIRED_VIA` instead of claiming the slot.
    via = rc.resolve_fired_via(fired_via)
    if via not in rc.FIRED_VIA:
        raise ValueError(f"fired_via must be one of {sorted(rc.FIRED_VIA)}")

    data: Dict[str, Any] = {"task_id": canonical, "kind": canonical,
                            "status": status, "fired_via": via}
    if surfaced is not None:
        data["surfaced"] = int(surfaced)
    if duration_ms is not None:
        data["duration_ms"] = int(duration_ms)
    if late_tier is not None:
        data[rc.LATENESS_FIELD] = late_tier
    data.update(rc.machine_fields())
    data.update(rc.model_fields())
    if telemetry is not None:
        data["telemetry"] = rc.telemetry_block(telemetry)
    for key, value in (extra_data or {}).items():
        if key not in data:
            data[key] = rc.telemetry_block(value) if key == "telemetry" else value
    # FIX3 F3-11. TWO THINGS WERE TRAVELLING UNDER ONE NAME. The Phase 7 call
    # passes the receipt's own `errors` list AND `n_errors` from the capture,
    # and `extra_data` copies both verbatim — so the 2026-09-21 fire's receipt
    # read `n_errors: 0` beside a one-element `errors`. A reader counting
    # failures off that number counted none, in the one receipt that had one.
    # The count is DERIVED from the list whenever a list is present; the
    # capture's own figure keeps its meaning under its own name.
    if isinstance(data.get("errors"), list):
        data["n_errors"] = len(data["errors"])
    try:
        from late_fire import slot_provenance

        for key, value in slot_provenance(workspace_root, canonical,
                                          fired_via=via).items():
            data.setdefault(key, value)
    except Exception:  # noqa: BLE001 — provenance never blocks a receipt
        pass
    return {"type": receipt_type, "source_skill": canonical, "data": data}


def pack_run_telemetry(workspace_root: str, *, prompt_text: str = "",
                       response_text: str = "",
                       connector_calls: Optional[List[Dict[str, Any]]] = None,
                       duration_ms: Optional[int] = None,
                       widget_bytes: Optional[int] = None) -> Dict[str, Any]:
    """The telemetry block that rides on the receipt, computed not guessed.

    THE BLOCK, NOT THE WRAPPER (fix round 2). `build_pack_run_telemetry`
    answers `{"telemetry": {...}}` — the shape a caller MERGES into `data`.
    This helper is called through the access layer and its answer is handed
    back as one value, so returning the wrapper made the caller write
    `data.telemetry.telemetry`, which is what the gate walk's fire receipt
    carried and what left the usage report reading zeroes for that fire. The
    inner block is what rides on the receipt, so the inner block is what comes
    back.
    """
    from telemetry import build_pack_run_telemetry

    built = build_pack_run_telemetry(
        prompt_text=prompt_text, response_text=response_text,
        connector_calls=list(connector_calls or []),
        duration_ms=duration_ms)
    block = dict(built.get("telemetry") or {})
    if widget_bytes is not None:
        # IDENT1 I-10: when the turn IS the widget, its size is the page's
        # `bytes_len` - not the length of a phrase describing it (the
        # 2026-09-22 receipt said 17, the length of "5091 bytes widget").
        try:
            block["response_chars"] = int(widget_bytes) + len(response_text or "")
        except (TypeError, ValueError):
            pass
    return block


# ---------------------------------------------------------------------------
# INBOXDRIVE1 (R-RW3-1 (a), coordinator decision D-2) - the inbox chain's
# one-command driver after the fetch
# ---------------------------------------------------------------------------
#
# THE WALK (2026-09-23, re-walk 3, HOLD driver 1). The 7:15 fire on the real
# "Require this computer" shape ran every door step up to and including the
# mail fetch, then stopped following the orchestrator: fifteen phases, each a
# rendered verb, and the model triaged fifty threads in improvised bodies,
# wrote nothing and closed with a paragraph about why. The door answered every
# question it was asked. So the chain after the fetch is ONE verb - this
# writer - the FB-7 shape the Waiting On, My Plate and Staff Meeting chats
# already have: everything lands beside the data in one call, and the call
# RETURNS what the fire says, so the model has nothing left to decide.

#: How many priority rows the surface carries (Phase 4's "top 5").
DRIVER_TOP_N = 5

#: The empty-state view's "on the books" rows (v3.2.3+ tracked_items): at most
#: this many, financial-signal threads that scored but missed the top rows.
TRACKED_CAP = 7

#: The noise buckets the sub-header always counts, in the order it names them.
NOISE_CLASSES = (("listings", "listings"), ("marketing", "marketing"),
                 ("calendar", "calendar"), ("security", "security"),
                 ("self_test", "self-test"))

#: Phase 4.5's one reason (a reply the CEO already sent on a sibling thread).
SIBLING_REPLY_REASON = "counterparty_reply_in_sibling_thread"

#: The one sentence when the page could not be landed and verified. Nothing
#: after it is written: no capture, no read row, no receipt.
PAGE_NOT_LANDED_LINE = ("Your inbox list could not be saved to your Command "
                        "Room folder this time, so nothing was recorded.")

#: The one sentence when the rows could not be appended after the page landed.
#: REWORDED (INBOXDRIVE1 review F-1): the page lands FIRST, by design, so by
#: the time the one append can refuse, the list IS saved - the old sentence
#: ("...so its summary was not saved") said the opposite of what was on disk.
NOT_RECORDED_LINE = ("Your inbox list was saved, but this run could not be "
                     "recorded in your Command Room folder.")


def _fire_batch(rows: Optional[List[Dict[str, Any]]],
                read_row: Optional[Dict[str, Any]],
                receipt: Dict[str, Any], *, n_fetched: Any) -> List[Dict[str, Any]]:
    """The driver's ONE append, composed in memory (INBOXDRIVE1 review F-1).

    MUST 1: nothing is written after a refusal point. The fire's rows, its
    `connector_read` row and its `pack_run` receipt reach the ledger in ONE
    `append_jsonl` call, so they land together or not at all. The read-row
    fence is therefore evaluated HERE, on the batch, before anything lands:
    a fire that fetched mail and composed no read row raises
    `ConnectorReadMissing` with the book untouched. The read row sits
    immediately before the receipt, and the receipt is last. (The ledger
    search `plan_fire_receipt` makes is not needed: the read row cannot be
    missing from a batch it is part of - and a fire start stamped later than
    the ledger's clock no longer refuses a read that is in the same call.)"""
    try:
        fetched = int(n_fetched or 0)
    except (TypeError, ValueError):
        fetched = 0
    has_read_row = (isinstance(read_row, dict)
                    and read_row.get("type") == "connector_read")
    if fetched > 0 and not has_read_row:
        raise ConnectorReadMissing()
    batch = [r for r in (rows or []) if isinstance(r, dict)]
    if has_read_row:
        batch.append(read_row)
    batch.append(receipt)
    return batch

#: The actions each row kind carries, exactly the orchestrator's item shapes
#: (FB-17 / v2.14.38 clusters); `{n}` is the row number.
EMAIL_ACTIONS = ("{n} send", "{n} draft", "{n} escalate to memo",
                 "{n} snooze 3d", "{n} not relevant")
INVITE_ACTIONS = ("{n} accept", "{n} propose [time]", "{n} decline [reason]",
                  "{n} not relevant")

#: G38 (the re-run guard's derivation): this writer takes `fired_via` and
#: `rerun_of` and writes the surface's receipt inside the call, but it does
#: not call `surface_drivers.run_surface` - the marker below is how the guard
#: counts it among the surface drivers (SPEC_V5330_FIXLANES §2 item 5).
CR_SURFACE_DRIVERS = ("run_inbox_surface",)


class InboxDriverRefused(RuntimeError):
    """A refusal the driver answers with instead of writing: `reason` plus the
    sentences that are the fire's whole final response."""

    def __init__(self, reason: str, lines: List[str]):
        super().__init__(reason)
        self.reason = reason
        self.lines = [str(x) for x in lines if str(x).strip()]


def _sender_domain(item: Dict[str, Any]) -> str:
    domain = str(item.get("domain") or "").strip().lower()
    if domain:
        return domain
    addr = str(item.get("sender_email") or item.get("sender") or "")
    return addr.rsplit("@", 1)[-1].strip().lower() if "@" in addr else ""


def _item_kind(item: Dict[str, Any]) -> str:
    kind = str(item.get("kind") or item.get("type") or "email_reply").strip().lower()
    return kind if kind in ("email_reply", "calendar_invite", "noise") else "email_reply"


def _age_key(item: Dict[str, Any]) -> str:
    """Oldest first when nothing scored: the raw timestamp sorts as text."""
    return str(item.get("ts") or "")


def select_priority(workspace_root: str, items: List[Dict[str, Any]], *,
                    top_n: int = DRIVER_TOP_N) -> Dict[str, Any]:
    """Phases 4, 4.5 and 5.6 over the fetched threads, as one computation:
    `{top, suppressed, dropped, noise, scored}`.

    Each thread carries the score the hard-coded Phase 4 rules gave it
    (`score`), its kind (`email_reply` / `calendar_invite` / `noise`, with
    `noise_class`), and - when Phase 4.5's sibling search found the CEO's own
    reply - `suppressed`. The learned rules are applied LAST
    (`apply_priority_rules`), the sibling-replied threads leave, the top rows
    are taken by score (by age when nothing scored above zero), and the
    learned suppressions drop what the CEO has told the system to stop
    showing (`filter_by_preferences`). Reads only."""
    noise = {key: 0 for key, _label in NOISE_CLASSES}
    candidates: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if _item_kind(item) == "noise":
            cls = str(item.get("noise_class") or "marketing").strip().lower()
            cls = "self_test" if cls in ("self-test", "self_test") else cls
            if cls in noise:
                noise[cls] += 1
            continue
        if item.get("from_me"):
            continue
        if str(item.get("suppressed") or "").strip():
            suppressed.append(item)
            continue
        candidates.append(item)
    scored_in = [{"score": c.get("score") or 0,
                  "sender": str(c.get("sender_email") or c.get("sender") or ""),
                  "domain": _sender_domain(c)} for c in candidates]
    scored = apply_priority_rules(workspace_root, scored_in)
    ranked = []
    for item, score in zip(candidates, scored):
        row = dict(item)
        row["score"] = score.get("score")
        row["score_before"] = score.get("score_before")
        ranked.append(row)
    positive = [r for r in ranked if (r.get("score") or 0) > 0]
    if positive:
        positive.sort(key=lambda r: -(r.get("score") or 0))
        top = positive[:top_n]
    else:
        top = sorted(ranked, key=_age_key)[:top_n]
    prefs_in = [{"n": i, "item_class": ("newsletter" if str(r.get("noise_class") or "")
                                        == "marketing" else "sender"),
                 "entity_id": str(r.get("sender_person_id") or _sender_domain(r))}
                for i, r in enumerate(top)]
    filtered = filter_by_preferences(workspace_root, prefs_in, surface=TASK_ID)
    kept_idx = {k["n"] for k in filtered["kept"]}
    kept = [r for i, r in enumerate(top) if i in kept_idx]
    dropped = [r for i, r in enumerate(top) if i not in kept_idx]
    top_ids = {id(r) for r in top}
    tracked = [r for r in positive if id(r) not in top_ids and r.get("financial")]
    return {"top": kept, "suppressed": suppressed, "dropped": dropped,
            "noise": noise, "scored": len(ranked), "tracked": tracked}


def _noise_line(noise: Dict[str, int]) -> str:
    total = sum(int(v) for v in noise.values())
    parts = ", ".join(f"{label} ({int(noise.get(key, 0))})"
                      for key, label in NOISE_CLASSES)
    return f"Noise filtered ({total} total): {parts}"


def _view_item(n: int, item: Dict[str, Any]) -> Dict[str, Any]:
    """One row of the data view, in the orchestrator's per-item shape."""
    name = str(item.get("sender_name") or item.get("name")
               or item.get("sender_email") or "").strip()
    subject = str(item.get("subject") or "").strip()
    why = str(item.get("why") or item.get("context_tag") or "").strip()
    row: Dict[str, Any] = {"n": n, "name": name, "subject": subject}
    if why:
        row["context_tag"] = why
    group = str(item.get("group") or "").strip()
    if group:
        row["group"] = group
    if _item_kind(item) == "calendar_invite":
        row["icon"] = "\U0001f4c5"
        row["actions"] = [a.format(n=n) for a in INVITE_ACTIONS]
        return row
    row["icon"] = "✉"
    to = str(item.get("sender_email") or "").strip()
    meta = []
    if subject:
        meta.append(["Subject", subject])
    if to:
        meta.append(["To", to])
    if meta:
        row["metadata"] = meta
    draft = [str(x).rstrip() for x in (item.get("draft") or item.get("body_lines") or [])
             if str(x).strip()]
    if draft:
        row["body_lines"] = draft
    body = str(item.get("body") or "").strip()
    if body:
        thread: Dict[str, Any] = {"author": name + (f" <{to}>" if to else ""),
                                  "date": str(item.get("date") or ""),
                                  "subject": subject,
                                  "body": body[:800] + ("…" if len(body) > 800 else "")}
        if item.get("url"):
            thread["url"] = str(item["url"])
        row["original_thread"] = thread
    row["actions"] = [a.format(n=n) for a in EMAIL_ACTIONS]
    return row


def _tracked_items(selection: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The empty state's read-only rows (v3.2.3+): financial-signal threads
    that scored above zero and did not make the top rows - vendor estimates,
    invoices - capped at `TRACKED_CAP`. "Populate what's there, don't pad."""
    rows = []
    for item in (selection.get("tracked") or [])[:TRACKED_CAP]:
        name = str(item.get("sender_name") or item.get("sender_email") or "").strip()
        subject = str(item.get("subject") or "").strip()[:60]
        rows.append({"direction": "Read-only",
                     "title": " \u00b7 ".join(p for p in (name, subject) if p),
                     "due": str(item.get("age") or "")})
    return rows


def build_inbox_view(workspace_root: str, selection: Dict[str, Any], *,
                     n_fetched: int, header_date_text: Optional[str] = None,
                     fetch_blocked: Optional[str] = None) -> Dict[str, Any]:
    """The data view the page and the text are rendered from, built in code -
    the header's day from `header_date`, never composed in prose (SCHEDVIEW1
    5.5); the noise line always present (the filter's work is visible); the
    quiet form when nothing was read (IDENT1 I-10)."""
    day = header_date_text or header_date(workspace_root)
    top = list(selection.get("top") or [])
    noise_line = _noise_line(selection.get("noise") or {})
    if not top:
        view: Dict[str, Any] = {
            "widget_mode": "all_clear_summary",
            "source_skill": TASK_ID, "surface": TASK_ID,
            "header": f"Inbox · {day} · nothing pressing this morning.",
            "sub_header": noise_line,
            "counters": [
                {"label": "Unread", "value": int(n_fetched)},
                {"label": "Priority", "value": 0},
                {"label": "Calendar invites", "value": 0},
                {"label": "Drafted replies", "value": 0},
            ],
            "summary_line": (f"{day}: no mail was read this time."
                             if (fetch_blocked or not n_fetched) else
                             f"{day}: {int(n_fetched)} unread, nothing that needs you."),
            "tracked_items": _tracked_items(selection),
            "footer": None,
            "n_fetched": 0 if (fetch_blocked or not n_fetched) else int(n_fetched),
        }
        return view
    rows = [_view_item(i + 1, item) for i, item in enumerate(top)]
    n_drafts = sum(1 for r in rows if r.get("body_lines"))
    word = "thread" if len(rows) == 1 else "threads"
    header = f"Inbox · {day} · {len(rows)} priority {word}."
    if n_drafts:
        header += " Drafts ready to review."
    return {"widget_mode": "all_batch_widget", "source_skill": TASK_ID,
            "surface": TASK_ID, "header": header, "sub_header": noise_line,
            "sections": [{"title": None, "count": None, "items": rows}],
            "n_fetched": int(n_fetched)}


def _capture_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Phase 5.4's candidates: each thread's `captures`, anchored to the
    thread's own ids and timestamp unless the candidate names its own."""
    out = []
    for item in items or []:
        if not isinstance(item, dict) or item.get("from_me"):
            continue
        for cand in item.get("captures") or []:
            if not isinstance(cand, dict):
                continue
            row = dict(cand)
            for key in ("message_id", "thread_id", "ts", "sender_person_id",
                        "sender_name"):
                if not row.get(key) and item.get(key):
                    row[key] = item[key]
            out.append(row)
    return out


def _inbound_messages(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Phase 5.5's list: every fetched thread's latest INBOUND message, a
    thread whose sender did not resolve included (`sender_person_id: ''`)."""
    out = []
    for item in items or []:
        if not isinstance(item, dict) or item.get("from_me"):
            continue
        if _item_kind(item) == "noise":
            continue
        out.append({"message_id": str(item.get("message_id") or ""),
                    "ts": str(item.get("ts") or ""),
                    "sender_person_id": str(item.get("sender_person_id") or ""),
                    "sender_email": str(item.get("sender_email") or ""),
                    "subject": str(item.get("subject") or ""),
                    "body": str(item.get("body") or ""),
                    "thread_id": str(item.get("thread_id") or ""),
                    "has_attachment": bool(item.get("has_attachment"))})
    return out


def _suppressed_rows(selection: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Phase 4.5's audit rows (SHOULD 8): one `chat_suppressed` per thread the
    sibling-reply check took off the surface."""
    rows = []
    for item in selection.get("suppressed") or []:
        rows.append({"type": "chat_suppressed", "source_skill": TASK_ID,
                     "data": {"reason": str(item.get("suppressed") or SIBLING_REPLY_REASON),
                              "suppressed_thread_id": str(item.get("thread_id") or "")}})
    return rows


def _provider(seams: Optional[Dict[str, Any]], provider: Optional[str]) -> str:
    if provider:
        return str(provider)
    declared = (seams or {}).get("declared_email")
    if isinstance(declared, dict):
        return str(declared.get("provider") or declared.get("label") or "")
    return str(declared or "")


def _elapsed_ms(fire_started_at: Optional[str]) -> Optional[int]:
    if not fire_started_at:
        return None
    try:
        start = _dt.datetime.fromisoformat(str(fire_started_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=_dt.timezone.utc)
    delta = _dt.datetime.now(_dt.timezone.utc) - start
    ms = int(delta.total_seconds() * 1000)
    return ms if ms >= 0 else None


def _refusal(reason: str, lines: List[str]) -> Dict[str, Any]:
    """The whole answer when the driver refuses: the sentences ARE the fire's
    final response, and nothing was written after the refusal point."""
    lines = [str(x) for x in lines if str(x).strip()]
    text = "\n".join(lines).strip() + "\n" if lines else ""
    out: Dict[str, Any] = {"ok": False, "refused": reason, "lines": lines,
                           "text": text, "rows_written": []}
    try:
        out["push"] = push_line("stopped", line=lines[0]) if lines else None
    except ValueError:
        out["push"] = None
    return out


def run_inbox_surface(workspace_root: str, *,
                      items: Optional[List[Dict[str, Any]]] = None,
                      n_accounts: Optional[int] = None,
                      accounts_seen: Optional[List[Any]] = None,
                      seams: Optional[Dict[str, Any]] = None,
                      user_person_id: Optional[str] = None,
                      fired_via: Optional[str] = None,
                      rerun_of: Optional[str] = None,
                      task_id: str = TASK_ID,
                      tools: Optional[List[Any]] = None,
                      device_root: Optional[str] = None,
                      fire_started_at: Optional[str] = None,
                      telemetry: Optional[Dict[str, Any]] = None,
                      lateness: Optional[Dict[str, Any]] = None,
                      provider: Optional[str] = None,
                      fetch_blocked: Optional[str] = None,
                      window_days: int = 14,
                      prompt_text: str = "",
                      now: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER (on `RUN_WRITER_ALLOWLIST` only) - the inbox chain after the
    fetch, in ONE call (coordinator decision D-2; ruling R-RW3-1 (a)).

    `items` is one dict per fetched thread: its ids (`thread_id`,
    `message_id`), the raw `ts`, the sender (`sender_email`, `sender_name`,
    `sender_person_id` when resolved), `subject`, the latest inbound `body`,
    `has_attachment`, `url`, `from_me` when the newest message is the CEO's,
    the Phase 4 `score`, the Phase 5 `kind` (`email_reply` /
    `calendar_invite` / `noise` + `noise_class`), `suppressed` from the
    Phase 4.5 sibling check, the drafted reply's lines (`draft`), the one-line
    `why`, an optional `group`, and `captures` - the Phase 5.4 candidates the
    thread carries. The model's judgement stops there. Beside the data this
    call then:

      1. names the writer (`receipts.require_writer_identity`) and refuses a
         stale mount - before anything is read or rendered;
      2. COMPOSES everything: the priority cut (learned rules, the sibling
         reply, the learned suppressions), the capture rows, the reconcile
         rows, the page, the grouped text ending in the saved-at line, the
         delivery plan, the read row and - through the private composer - the
         receipt itself, so a vocabulary error refuses with the book untouched;
         the read-row fence is evaluated here too, in memory (`_fire_batch`);
      3. lands the page through the layer's own `write` - verified, and the
         bytes written must equal `bytes_len` (R-3) - or refuses, writing
         nothing more;
      4. appends, in ONE call to the layer's one append door (review F-1),
         the capture and reconcile rows, the render audits, the
         suppressed-thread rows, the delivery flags, the `connector_read` row
         (leg `inbox-fire`) and the receipt LAST - they land together or not
         at all. A refused append leaves the book untouched and says the
         list was saved (it was: the page landed in step 3).

    It answers `{ok, text, page_rel, page_pc_path, bytes_len, push, calls,
    rows_written, receipt, counts, n_accounts, branch}` - `text` is the whole
    message (the post-order lines, the grouped list, the saved-at line last)
    and `calls` are the delivery calls in order. On a refusal
    (`writer_identity_required`, `mount_stale`, `page_not_landed`,
    `connector_read_missing`) it answers `{ok: false, refused, lines, text}`;
    `lines` is the fire's whole final response."""
    import os as _os

    import receipts as rc
    import workspace_access as wa

    ws = Path(_os.path.abspath(str(workspace_root)))
    root = str(ws)
    items = [i for i in (items or []) if isinstance(i, dict)]
    verdict = lateness if isinstance(lateness, dict) else {}
    tier = verdict.get("tier")
    via = fired_via or verdict.get("receipt_fired_via") or None
    if not rerun_of and tier == "rerun":
        rerun_of = verdict.get("rerun_of") or None
    late_tier = tier if tier in ("note", "degrade") else None
    # 1. the writer, then the mount - before any read or render.
    try:
        rc.require_writer_identity(workspace_root=root)
    except Exception as exc:  # noqa: BLE001 - only the refusal is expected
        if type(exc).__name__ != "WriterIdentityRequired":
            raise
        return _refusal("writer_identity_required",
                        [getattr(exc, "line", "") or rc.WRITER_IDENTITY_REQUIRED_LINE])
    try:
        import surface_drivers as sd

        sd.refuse_if_mount_stale(root)
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ != "MountStaleError":
            raise
        return _refusal("mount_stale", list(getattr(exc, "lines", None) or [str(exc)]))

    # 2. compose everything.
    errors: List[Dict[str, Any]] = []
    user = str(user_person_id or "").strip()
    if not user:
        try:
            from primary_user import resolve_primary_user

            user = str(resolve_primary_user(root) or "")
        except Exception:  # noqa: BLE001
            user = ""
    prov = _provider(seams, provider)
    if n_accounts is None:
        if accounts_seen is not None:
            n_accounts = len(list(accounts_seen)) or 1
        else:
            n_accounts = int((seams or {}).get("n_accounts") or 1)
    n_fetched = 0 if fetch_blocked else len(items)
    selection = select_priority(root, items)
    view = build_inbox_view(root, selection, n_fetched=n_fetched,
                            fetch_blocked=fetch_blocked)
    capture = {"rows": [], "counters": {"n_candidates": 0, "n_captured": 0,
                                        "n_deduped": 0, "n_below_bar": 0,
                                        "n_capped": 0, "n_capture_errors": 0}}
    reconcile: Dict[str, Any] = {"rows": []}
    if user:
        capture = plan_inbound_capture(root, _capture_items(items),
                                       user_person_id=user, source_skill=TASK_ID,
                                       provider=prov or None)
        reconcile = plan_inbound_reconcile(
            root, [] if fetch_blocked else _inbound_messages(items),
            user_person_id=user, source_skill=TASK_ID,
            exclude_captured_since=fire_started_at, provider=prov or None,
            fetch_blocked=fetch_blocked)
    elif items:
        # An unresolved user aborts both passes: direction is derived from
        # owner vs user, so a clean zero here would be a lie.
        errors.append({"phase": "5.4_inbound_capture",
                       "reason": "primary_user_unresolved"})
    page = render_inbox_page(root, view, wrapper="fragment", name_hint=TASK_ID)
    # R-3 is about BYTES ON DISK: the render's `bytes_len` counts characters,
    # and a page carrying the row icons is longer in UTF-8 than in characters.
    # The driver measures what `write` must land, and checks exactly that.
    page["bytes_len"] = len(page["html"].encode("utf-8"))
    text_out = render_inbox_text(root, view)
    before = post_order(verdict, widget=False)["before_widget"] if verdict else []
    counts = dict(text_out.get("counts") or {})

    def _delivery() -> Dict[str, Any]:
        plan = plan_delivery(root, tools=tools or [], page_rel=page["page_rel"],
                             text=text_out["text"], counts=counts,
                             n_accounts=int(n_accounts or 1),
                             device_root=device_root, task_id=task_id)
        message = plan.get("message") or ""
        if tier == "degrade" and verdict.get("degrade_notice"):
            message = str(verdict["degrade_notice"]).strip() + "\n"
            first = dict(plan["calls"][0]) if plan.get("calls") else {"tool": "chat_turn"}
            first["text"] = message
            plan["calls"] = [first]
            plan["push"] = None
        elif before and plan.get("branch") == "text":
            message = "\n".join(before) + "\n\n" + message
            for call in plan["calls"]:
                if call.get("tool") in (SEND_MESSAGE_TOOL, "chat_turn"):
                    call["text"] = message
        plan["message"] = message
        return plan

    delivery = _delivery()          # composed first: a bad line refuses here
    read_row = None
    if not fetch_blocked:
        from chat_context import connector_read_row

        read_row = connector_read_row(
            provider=prov or "unknown", scope=TASK_ID, n_results=n_fetched,
            leg=CONNECTOR_READ_LEG, window_days=window_days,
            source_skill="inbox-triage")["row"]
        # The read row names the writer the receipt names (the pass line:
        # both `data.machine: acct-...`), stamped by the receipt module's own
        # helper so the two cannot disagree. Additive: no reader keys on it.
        read_row = dict(read_row, data=dict(read_row.get("data") or {},
                                            **rc.machine_fields()))
    n_drafts = sum(1 for sec in view.get("sections") or []
                   for it in sec.get("items") or [] if it.get("body_lines"))
    surfaced = sum(len(sec.get("items") or []) for sec in view.get("sections") or [])
    widget_branch = delivery.get("branch") == "widget"
    extra: Dict[str, Any] = {
        "errors": errors, "fire_started_at": fire_started_at,
        "items_drafted_text": n_drafts, "n_fetched": n_fetched,
        "items_persisted_to_gmail": 0,
        "n_suppressed_sibling": len(selection.get("suppressed") or []),
        "n_dropped_by_preference": len(selection.get("dropped") or []),
        "widget_bytes": int(page["bytes_len"]),
    }
    for key in ("n_candidates", "n_captured", "n_below_bar", "n_capped",
                "n_deduped", "n_capture_errors"):
        extra[key] = int((capture.get("counters") or {}).get(key) or 0)
    extra.update(delivery.get("receipt_flags") or {})
    if rerun_of:
        extra[_rerun_field()] = rerun_of
    duration = _elapsed_ms(fire_started_at)
    tele = telemetry if isinstance(telemetry, dict) else pack_run_telemetry(
        root, prompt_text=prompt_text,
        response_text="" if widget_branch else delivery.get("message") or "",
        connector_calls=[], duration_ms=duration,
        widget_bytes=int(page["bytes_len"]) if widget_branch else None)
    receipt_args = dict(task_id=task_id, fired_via=via, surfaced=surfaced,
                        duration_ms=duration, late_tier=late_tier,
                        telemetry=tele, extra_data=extra)
    receipt = _compose_fire_receipt(root, **receipt_args)  # refuses before any write
    # Review F-1: the read-row fence, IN MEMORY, before any write - the
    # `connector_read_missing` refusal can only fire with the book untouched.
    try:
        _fire_batch([], read_row, receipt, n_fetched=n_fetched)
    except ConnectorReadMissing as exc:
        return _refusal("connector_read_missing", [exc.line])

    # 3. the page, through the layer's own write door, verified.
    ctx = wa.resolve(start=ws)
    landed = wa.write(page["page_rel"], page["html"], None, ctx=ctx)
    if not (landed.get("ok") and landed.get("verified")
            and int(landed.get("bytes_written") or -1) == int(page["bytes_len"])):
        return dict(_refusal("page_not_landed", [_validated(PAGE_NOT_LANDED_LINE)]),
                    detail={k: landed.get(k) for k in (
                        "reason", "verified", "bytes_written", "bytes_expected",
                        "bytes_found", "mode") if k in landed})
    delivery = _delivery()          # recomposed: the page is there to copy

    # 4. the rows, the read row and the receipt LAST - ONE append (review
    # F-1): they land together or not at all.
    rows: List[Dict[str, Any]] = []
    rows += list(capture.get("rows") or [])
    rows += list(reconcile.get("rows") or [])
    rows += list(page.get("pending_rows") or [])
    rows += list(text_out.get("pending_rows") or [])
    rows += _suppressed_rows(selection)
    rows += list(delivery.get("rows") or [])
    batch = _fire_batch(rows, read_row, receipt, n_fetched=n_fetched)
    appended = wa.append_jsonl("_hq/data/events.jsonl", batch, holder=TASK_ID,
                               ctx=ctx)
    if not appended.get("ok"):
        # The page IS saved (step 3), so the sentence says so; the door's own
        # reason and line ride in `detail` for the run log.
        return dict(_refusal(str(appended.get("reason") or "append_refused"),
                             [_validated(NOT_RECORDED_LINE)]),
                    page_rel=page["page_rel"],
                    detail={k: appended.get(k) for k in ("reason", "line", "detail")
                            if appended.get(k)})
    written: List[Dict[str, Any]] = list(appended.get("stamped") or batch)
    stamped_receipt = written[-1]

    page_pc_path = None
    dev = device_root or str(_os.environ.get("CR_DEVICE_WORKSPACE", "") or "").strip() or None
    if dev:
        try:
            import deliverables as dl

            page_pc_path = dl.device_join(dev, page["page_rel"])
        except Exception:  # noqa: BLE001 - a path spelling never blocks
            page_pc_path = None
    out: Dict[str, Any] = {
        "ok": True, "branch": delivery.get("branch"),
        "text": delivery.get("message") or "",
        "page_rel": page["page_rel"], "page_pc_path": page_pc_path,
        "bytes_len": int(page["bytes_len"]),
        "bytes_written": int(landed.get("bytes_written") or 0),
        "push": delivery.get("push"), "calls": delivery.get("calls") or [],
        "rows_written": written, "receipt": stamped_receipt,
        "counts": counts, "n_accounts": int(n_accounts or 1),
        "before_widget": list(before),
    }
    if widget_branch:
        out["html"] = page["html"]
    return out


def _rerun_field() -> str:
    """`late_fire`'s own spelling of the re-run key (SPEC RERUNFAN1)."""
    try:
        import late_fire as lf

        return str(getattr(lf, "RERUN_OF_FIELD", "rerun_of") or "rerun_of")
    except Exception:  # noqa: BLE001
        return "rerun_of"


# ---------------------------------------------------------------------------
# The phrase door, and the draft scan
# ---------------------------------------------------------------------------

def billing_door(workspace_root: str, *, vendor: str = "",
                 sender: str = "") -> Dict[str, Any]:
    """`treat <vendor> as billing` — the offer line, and the file the answer
    would change, composed rather than written.

    The list's own writer keeps it ordered, idempotent and creates it on first
    use; here the same ordering runs over the file's current contents and the
    result comes back as `{rel, text}` for `plan write`. A vendor name with no
    domain in it comes back as a question, which is what the composer says for
    you — and the file this phrase writes is never named to the reader.
    """
    import surface_composers as sc

    rel = getattr(sc, "BILLING_DOMAINS_REL", "_hq/data/known-billing-domains.txt")
    out: Dict[str, Any] = {
        "line": sc.billing_door_line(vendor, workspace=workspace_root),
        "rel": str(rel).replace("\\", "/"),
        "text": None,
        "result": {"domain": "", "added": False, "total": 0,
                   "needs_domain": True},
        "receipt": "",
    }
    domain = sc._domain_of(sender)
    if not domain or "." not in domain:
        out["receipt"] = sc.billing_door_receipt(out["result"],
                                                 workspace=workspace_root)
        return out
    current = _read_lines(workspace_root, out["rel"])
    if domain in current:
        out["result"] = {"domain": domain, "added": False,
                         "total": len(current)}
    else:
        # The writer's own discipline: order-preserving, appended at the end,
        # created on first use. Only the append itself moves to the caller.
        out["result"] = {"domain": domain, "added": True,
                         "total": len(current) + 1}
        out["text"] = "\n".join(current + [domain]) + "\n"
    out["receipt"] = sc.billing_door_receipt(out["result"],
                                             workspace=workspace_root)
    return out


def _read_lines(workspace_root: str, rel: str) -> List[str]:
    from pathlib import Path

    path = Path(workspace_root) / rel
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [ln.strip().lower() for ln in raw.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def triage_config(workspace_root: str, *,
                  skill_name: str = "inbox-triage",
                  defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """`{config, configured}` — the saved first-run choices over the defaults.

    Read-only on purpose: the FRP1 read path never writes, and the first-fire
    SAVE is a separate act the caller makes through `plan write`, so a fire that
    only reads the config cannot mint one.
    """
    from skill_config_writer import get_config, is_configured

    return {"config": dict(get_config(workspace_root, skill_name,
                                      dict(defaults or {}))),
            "configured": bool(is_configured(workspace_root, skill_name))}


def gate_reply(workspace_root: str, *, text: str = "",
               surface: str = TASK_ID, relayed: str = "",
               customer_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The whole reply through the one door, returned rather than printed.

    A composer gates the sentence it built; it cannot gate the sentences typed
    after it. So everything intended for the customer goes in here once, and
    what comes back is the entire reply.
    """
    from surface_composers import post

    with _captured_appends() as captured:
        gated = post(text, surface=surface, workspace=workspace_root,
                     relayed=relayed or None,
                     customer_rows=customer_rows or None)
    # The door records that it ran. That row is the ledger's evidence the
    # reply was gated at all, so it is captured rather than dropped and comes
    # back for the caller's append — the same posture the lateness verb takes
    # with the clock row.
    return {"text": gated, "pending_rows": list(captured)}


def check_draft_dates(workspace_root: str, *, draft_text: str = "",
                      row: Optional[Dict[str, Any]] = None,
                      today: str = "") -> Dict[str, Any]:
    """`{ok, phrases}` — every date phrase the row cannot support.

    The scan itself raises; a verb cannot, because the envelope is the answer.
    So the error's own message comes back as data and the caller treats a
    non-empty list exactly as the raise told it to: drop the day from the
    draft, or set a real date on the row first. Never send anyway.
    """
    from draft_date_scan import DraftDateError, assert_draft_dates

    try:
        assert_draft_dates(draft_text, row or {},
                           today=today or header_date(workspace_root))
    except DraftDateError as exc:
        return {"ok": False, "phrases": _phrases_of(exc), "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "phrases": [], "detail": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "phrases": [], "detail": ""}


def _phrases_of(exc: Exception) -> List[str]:
    found = getattr(exc, "phrases", None)
    if isinstance(found, (list, tuple, set)):
        return [str(p) for p in found]
    return []
