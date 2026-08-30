#!/usr/bin/env python3
"""
TASKALARM1 — the client-visible surface for a dead scheduled surface.

BACKLOG row TASKALARM1: nothing told anyone when a scheduled surface stopped
firing. One workspace went six days with every daily surface dark and its
operator processed seven meetings by hand before noticing; an IT contact
asked four different ways how he would know something had stopped, and the
honest answer was "the output gets worse." The watchdog
(`shared/scripts/task_watchdog.py`, Phase 3 W1) already computes exactly the
three classes this spec surfaces — `late` / `receipt_gap` / `never_authorized`
— per-task, every fire. What was missing was any PROACTIVE, client-visible
line for them; system-health's on-demand table existed, but nobody sees it
unless they think to ask.

RULING (§0, binding — kept verbatim from the BACKLOG row and the spec):
machines sleep, and Cowork's catch-up-on-wake model makes "late" normal, so
lateness alone is never the alarm. The alarm condition is **never receipted
past its window** — a task whose newest fire served its slot late but DID
write a receipt (the watchdog's own `caught_up` finding) is explicitly ruled
NOT alarming. `classify_dark_surfaces` below skips every `caught_up` report
before it ever reaches classification — that single skip IS the ruling, in
code, and is the mutation fence the acceptance pin's second named mutation
targets ("alarm on lateness -> the late-but-receipted pin red").

ZERO NEW DETECTION LOGIC (ruling #1). Every class, every threshold, every
receipt read here already lives in `task_watchdog.check_tasks` /
`schedule_config`. This module is a SURFACE: it classifies the watchdog's own
report rows into the three named classes, formats one plain sentence per row,
and — the one genuinely new piece of state — keeps a render-once ledger so
the same dark spell alarms exactly once (ruling #3, "honest, not noisy"),
imitating the CRU walk ledger's shape (`eod_incremental.py`,
`_hq/.system/cru_walk_ledger.json`): keyed entries, an anchor that changes
when the underlying fact changes, atomic writes, best-effort (a ledger that
cannot be written costs a re-alarm next fire, never the current one).

THE LEDGER KEY (a "dark window"). Keyed by `task_id` alone with an `anchor`
value that identifies WHICH dark spell is being alarmed: the task's last
known receipt timestamp (or, for `never_authorized`, the workspace's
registration timestamp — there is no receipt to anchor to). While a task
stays dark, its last receipt never changes, so the anchor never changes, so
the ledger keeps suppressing it — one alarm per spell. The moment a NEW
receipt lands (recovery) and the task later goes dark again, the anchor is a
new timestamp: a different dark window, a fresh alarm. This is deliberately
NOT keyed by rendering surface (morning-brief vs end-of-day vs system-health)
— ruling #3 reads "one line per dark surface" as one line per DEAD TASK, and
a customer who saw the line on the morning brief should not see it repeated
verbatim on the same day's end-of-day close. `classify_dark_surfaces` (no
ledger, always current) is the escape hatch for system-health's on-demand
table, which must never be silenced by a chat's own earlier alarm (ruling
§0.4 — "check my schedules" always renders the full current truth).

NEVER_AUTHORIZED VS LATE — TWO DIFFERENT SENTENCES (ruling #3). A task that
never fired at all (the global_limit-skip class — Cowork's one-time
permission gate was never cleared) and a task that fired before and then
stopped have different fixes, and conflating them sends the customer to
press the wrong button. `_never_authorized_line` always reads "was never set
up on this machine"; `_late_line` / `_receipt_gap_line` always read "has
stopped firing" / "ran but hasn't recorded any work" — never the other
sentence. The acceptance pin checks this by literal substring.
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

import task_watchdog as _tw  # noqa: E402
from event_time import parse_ts  # noqa: E402
from schedule_config import load_schedule_config  # noqa: E402

# Where the render-once ledger lives. `.system` is the workspace's own
# diagnostic sidecar home (widgets, briefs audit copies, the CRU walk
# ledger) — this is bookkeeping about alarms already surfaced through
# canonical readers, never substrate.
ALARM_LEDGER_RELPATH = "_hq/.system/task_alarm_ledger.json"

# How long a recorded alarm stays in the ledger once its dark window is long
# past (e.g. the task was deleted, or `now` jumped far forward in a test).
# Mirrors EODSPEED1's EVIDENCE_WINDOW_DAYS posture — bound the file, never
# let it grow forever.
ALARM_LEDGER_WINDOW_DAYS = 90

_DAY = _dt.timedelta(days=1)


# ---------------------------------------------------------------------------
# Clock (identical posture to task_watchdog._now_local — the same clock the
# watchdog's own lateness math runs on; a different clock here would let this
# surface and the watchdog disagree at a boundary).
# ---------------------------------------------------------------------------

def _now_local() -> _dt.datetime:
    try:
        from trusted_now import trusted_now_local_naive

        return trusted_now_local_naive()
    except Exception:
        return _dt.datetime.now()


def _to_local_naive(dt: Optional[_dt.datetime]) -> Optional[_dt.datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


# ---------------------------------------------------------------------------
# The render-once ledger
# ---------------------------------------------------------------------------

def _ledger_path(workspace_root) -> Path:
    return Path(workspace_root) / ALARM_LEDGER_RELPATH


def load_alarm_ledger(workspace_root) -> dict:
    """The ledger, `{task_id: entry}` — `{}` on any failure. A corrupt or
    missing ledger degrades to "nothing has alarmed yet", which re-alarms:
    the safe direction (an extra sentence beats a silently-suppressed one)."""
    try:
        raw = _ledger_path(workspace_root).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _already_alarmed(ledger: dict, task_id: str, anchor: str) -> bool:
    """THE HONOR FENCE — named separately so the mutation suite can remove
    the call site whole and prove the render-once pin goes red (the
    acceptance's first named mutation, "drop the ledger")."""
    entry = ledger.get(task_id)
    if not isinstance(entry, dict):
        return False
    return entry.get("anchor") == anchor


def _record_alarms(workspace_root, findings: list[dict], *, now: _dt.datetime) -> None:
    """Merge newly-alarmed findings into the ledger, prune entries whose
    alarm is older than the retention window, write atomically. Best-effort:
    a ledger that cannot be written costs a re-alarm next fire, never this
    one (mirrors `eod_incremental.record_walk`)."""
    if not findings:
        return
    ledger = load_alarm_ledger(workspace_root)
    for f in findings:
        ledger[f["task_id"]] = {
            "anchor": f["_anchor"],
            "class": f["class"],
            "alarmed_at": now.isoformat(),
        }
    horizon = now - _dt.timedelta(days=ALARM_LEDGER_WINDOW_DAYS)
    pruned = {}
    for tid, entry in ledger.items():
        if not isinstance(entry, dict):
            continue
        try:
            alarmed_at = _dt.datetime.fromisoformat(str(entry.get("alarmed_at", "")))
        except ValueError:
            continue
        if alarmed_at < horizon:
            continue
        pruned[tid] = entry
    try:
        from atomic_write import atomic_write_json

        path = _ledger_path(workspace_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, pruned)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Classification (no new detection logic — every field read here is already
# on task_watchdog.check_tasks's report rows)
# ---------------------------------------------------------------------------

def _windows_missed(cron: Optional[str], last_receipt: Optional[_dt.datetime],
                    now: _dt.datetime, *, cap: int = 30) -> Optional[int]:
    """How many of the task's expected fires have gone by since its last
    receipt (or since `now`, when there has never been one). Bounded — a
    dead task's count stops growing informative past a couple of dozen."""
    if not cron:
        return None
    try:
        fires = _tw.expected_fires(cron, now=now, count=cap)
    except Exception:  # noqa: BLE001
        return None
    if last_receipt is None:
        return len(fires)
    return sum(1 for f in fires if f > last_receipt)


def _spoken_name(r: dict) -> str:
    """Same posture as task_watchdog._spoken_name (EOD2): on a machine
    running a renamed predecessor task, say the name the customer's own
    Scheduled list shows, not the successor's registry name."""
    from schedule_config import task_display_name

    if r.get("served_by"):
        return task_display_name(r["served_by"])
    return r["display_name"]


def _never_authorized_line(name: str) -> str:
    # The "never set up" sentence — MUST NOT read like "stopped firing"
    # (ruling #3: different fixes, must not conflate).
    return (
        f"Your {name} task was never set up on this machine — open it in the "
        f"Scheduled section and press Run Now once."
    )


def _late_line(name: str, last_receipt: Optional[_dt.datetime],
              now: _dt.datetime) -> str:
    # The "stopped firing" sentence — MUST NOT read like "was never set up"
    # (ruling #3).
    if last_receipt is not None:
        days = max((now - last_receipt) // _DAY, 1)
        word = "day" if days == 1 else "days"
        return (
            f"Your {name} task has stopped firing — no receipt in {days} {word}. "
            f"Open it in the Scheduled section and press Run Now to catch up."
        )
    return (
        f"Your {name} task has stopped firing — I can't tell from here why. "
        f"Open it in the Scheduled section and press Run Now to catch up."
    )


def _receipt_gap_line(name: str, last_receipt: Optional[_dt.datetime],
                      now: _dt.datetime) -> str:
    if last_receipt is not None:
        days = max((now - last_receipt) // _DAY, 1)
        word = "day" if days == 1 else "days"
        return (
            f"The schedule shows your {name} task running, but it hasn't recorded "
            f"any work in {days} {word} — open it and press Run Now once, and "
            f"check the result looks right."
        )
    return (
        f"The schedule shows your {name} task running, but it has never recorded "
        f"any work — open it and press Run Now once, and check the result looks right."
    )


DEAD_ROOT_TASK_ID = "workspace-root"


def _dead_root_line(plan_class: str, candidates: Optional[list] = None) -> str:
    # SPEC PATHREPAIR1 — the THIRD distinct sentence (ruling #3's posture
    # extended): must read like neither "was never set up on this machine"
    # (never_authorized) NOR "has stopped firing" (late/receipt_gap) —
    # those two describe a TASK; this describes the WORKSPACE ITSELF having
    # moved out from under every task at once, which needs its own fix
    # (reconnect the folder), not a Run Now.
    if plan_class == "ambiguous":
        n = len(candidates or [])
        noun = "folder" if n == 1 else "folders"
        return (
            f"Your Command Room workspace folder doesn't match what's on "
            f"record, and I found {n} {noun} that could be it — I won't "
            f"guess which one. Open Command Room and say \"set up command "
            f"room schedules\" to confirm which folder to use."
        )
    # no_candidate / no_fingerprint — the honest common sentence: something
    # is wrong with the registration and this module found no safe way to
    # fix it itself.
    return (
        "Your Command Room workspace folder doesn't match what's on record "
        "— it may have moved or been renamed, and I couldn't find where it "
        "went. Open Command Room and say \"set up command room schedules\" "
        "to reconnect it."
    )


def _classify_dead_root(workspace_root, *, now: _dt.datetime,
                        machine: Optional[str] = None) -> Optional[dict]:
    """The 4th TASKALARM1 class (SPEC PATHREPAIR1 v2): the workspace's OWN
    registration record points at a root that is affirmatively DEAD from
    this vantage, and `path_repair` could not auto-repoint it with high
    confidence. Recomputed LIVE from `path_repair.plan_repair` every call —
    zero new detection logic beyond what `path_repair.repair` itself would
    decide, exactly the posture the other three classes take toward
    `task_watchdog.check_tasks` (this module never persists its own
    'unrepairable' signal; `path_repair` writes an event only on a
    SUCCESSFUL repair — ruling 2's no-write-on-ambiguous rule — so this is
    the only place that verdict is visible at all).

    Returns None when the root is ALIVE, repairable-with-trust (the dispatch
    preamble already fixes that case before this ever renders), UNKNOWN
    (ruling 1/2/3 — a session-mount vantage, another machine's registration,
    or a disconnected drive is never alarm-worthy: "nobody answered the
    knock" is not evidence the workspace moved), or when path_repair is
    unavailable. `machine` is an optional identity override — real callers
    never pass it (real per-machine identity, resolved lazily); tests pass
    an explicit value so a multi-machine fixture never touches this box's
    own identity marker."""
    try:
        import path_repair as pr
    except Exception:  # noqa: BLE001
        return None
    reg = pr.registration(workspace_root)
    vantage = pr.current_vantage(workspace_root, machine=machine)
    plan = pr.plan_repair(reg, trusted_candidate=workspace_root, vantage=vantage)
    if plan["class"] not in pr.UNREPAIRABLE_CLASSES:
        return None  # "repaired"/"alive"/"unknown" — not this module's concern
    candidates = plan.get("candidates") or []
    anchor = json.dumps(
        {"stored_root": reg.get("workspace_root"),
         "class": plan["class"],
         "candidates": sorted(str(c) for c in candidates)},
        sort_keys=True,
    )
    return {
        "task_id": DEAD_ROOT_TASK_ID,
        "class": "dead_root",
        "last_receipt": None,
        "windows_missed": None,
        "line": _dead_root_line(plan["class"], candidates),
        "_anchor": anchor,
    }


def classify_dark_surfaces(workspace_root, *, now: Optional[_dt.datetime] = None,
                           task_records=None, machine: Optional[str] = None) -> list[dict]:
    """Every currently-dark task, classified — no ledger, no suppression.
    This is the ONE entry point system-health's on-demand table reads
    (ruling §0.4): a chat the customer explicitly asked must always answer
    with the current truth, never with what an earlier chat already alarmed.

    Returns dicts: {task_id, class, last_receipt (ISO|None), windows_missed
    (int|None), line, _anchor} — `_anchor` is private plumbing for the
    render-once ledger; callers outside this module should not read it
    (`dark_surfaces` strips it before returning).

    `dead_root` (SPEC PATHREPAIR1) is checked FIRST. When the workspace's
    own registration points at a root `path_repair` could not auto-repoint
    with high confidence, this function returns EXACTLY that one finding —
    a workspace whose own registration can't be trusted has no business
    asserting facts about individual tasks' freshness either, so
    `task_watchdog.check_tasks` is never even called in that case, and no
    dead_root finding co-occurs with the other three. Otherwise, worst-first
    ordering among the other three is never_authorized, then late, then
    receipt_gap; within a class, the longest-dark task first.
    """
    now = now or _now_local()
    dead_root_finding = _classify_dead_root(workspace_root, now=now, machine=machine)
    if dead_root_finding is not None:
        return [dead_root_finding]
    reports = _tw.check_tasks(workspace_root, now=now, task_records=task_records)

    ws_config = _tw.read_workspace_config(workspace_root)
    registered_at = _to_local_naive(parse_ts(ws_config.get("registered_at") or ""))

    entities = Path(workspace_root) / "_hq" / "data" / "entities.json"
    try:
        config = load_schedule_config(entities)
    except Exception:  # noqa: BLE001
        config = {}

    out: list[dict] = []
    for r in reports:
        # THE RULING, IN CODE: a newest fire that served its slot late but
        # DID write a receipt is not alarming — skip it before it can ever
        # be classified. Removing this line is the acceptance pin's second
        # named mutation ("alarm on lateness").
        if r.get("caught_up"):
            continue

        if r["status"] == "never_authorized":
            cls = "never_authorized"
        elif r["status"] == "late":
            cls = "late"
        elif r["status"] == "ok" and r.get("receipt_gap"):
            cls = "receipt_gap"
        else:
            continue  # ok / never_fired / not_registered — not this spec's alarm

        name = _spoken_name(r)
        last_fired_iso = r.get("last_fired")
        last_receipt = None
        if last_fired_iso:
            try:
                last_receipt = _dt.datetime.fromisoformat(last_fired_iso)
            except ValueError:
                last_receipt = None

        if cls == "never_authorized":
            line = _never_authorized_line(name)
            windows_missed = None
            anchor = registered_at.isoformat() if registered_at else "unregistered"
        elif cls == "late":
            cron = (config.get(r["task"]) or {}).get("cron")
            windows_missed = _windows_missed(cron, last_receipt, now)
            line = _late_line(name, last_receipt, now)
            anchor = last_fired_iso or "never"
        else:  # receipt_gap
            cron = (config.get(r["task"]) or {}).get("cron")
            windows_missed = _windows_missed(cron, last_receipt, now)
            line = _receipt_gap_line(name, last_receipt, now)
            anchor = last_fired_iso or (r.get("last_run_at") or "never")

        out.append({
            "task_id": r["task"],
            "class": cls,
            "last_receipt": last_fired_iso,
            "windows_missed": windows_missed,
            "line": line,
            "_anchor": anchor,
        })

    order = {"never_authorized": 0, "late": 1, "receipt_gap": 2}
    out.sort(key=lambda e: (order.get(e["class"], 9), e["last_receipt"] or ""))
    return out


def _strip_private(findings: list[dict]) -> list[dict]:
    return [{k: v for k, v in f.items() if not k.startswith("_")} for f in findings]


def dark_surfaces(workspace_root, now: Optional[_dt.datetime] = None, *,
                  task_records=None, record: bool = True,
                  machine: Optional[str] = None) -> list[dict]:
    """SPEC TASKALARM1's public entry point:
    `dark_surfaces(workspace_root, now) -> [{task_id, class, last_receipt,
    windows_missed, line}]`.

    Classifies fresh every call (`classify_dark_surfaces` — zero new
    detection logic), then applies the render-once ledger: a finding whose
    (task_id, dark-window) pair already alarmed is dropped from the return,
    and every finding this call DOES return is recorded as alarmed before
    returning (best-effort — a ledger write failure costs a re-alarm next
    fire, never this one). Pass `record=False` to read the current truth
    without touching the ledger (the on-demand table's need — see
    `classify_dark_surfaces`, which this delegates to identically when
    `record` is False).
    """
    now = now or _now_local()
    findings = classify_dark_surfaces(workspace_root, now=now, task_records=task_records,
                                      machine=machine)
    if not record:
        return _strip_private(findings)

    ledger = load_alarm_ledger(workspace_root)
    newly = [f for f in findings if not _already_alarmed(ledger, f["task_id"], f["_anchor"])]
    _record_alarms(workspace_root, newly, now=now)
    return _strip_private(newly)


def dark_surface_lines(workspace_root, now: Optional[_dt.datetime] = None, *,
                       task_records=None, cap: int = 3,
                       machine: Optional[str] = None) -> list[str]:
    """The morning-brief / end-of-day render (spec item 2): `dark_surfaces`'
    lines, worst-first, capped at `cap` with a trailing "and N more" line —
    never a second sentence about the same dead task, never a raw count with
    no names (ruling #2: a client who never digs still sees the line)."""
    findings = dark_surfaces(workspace_root, now=now, task_records=task_records,
                             machine=machine)
    if not findings:
        return []
    lines = [f["line"] for f in findings[:cap]]
    rest = len(findings) - cap
    if rest > 0:
        word = "task" if rest == 1 else "tasks"
        lines.append(
            f"...and {rest} more background {word} gone dark — say 'health check' "
            f"for the full list."
        )
    return lines


__all__ = [
    "ALARM_LEDGER_RELPATH",
    "DEAD_ROOT_TASK_ID",
    "classify_dark_surfaces",
    "dark_surfaces",
    "dark_surface_lines",
    "load_alarm_ledger",
]
