#!/usr/bin/env python3
"""
lifecycle_pass.py — the project lifecycle pass (SPEC LIFECYCLE1).

WHAT MOVED HERE, AND WHY
========================
Pulse (the 9 AM weekday chat, `orchestrator-dont-forget.md`) carried two jobs
in one surface: a cracks/cadence NARRATIVE the CEO read every weekday morning,
and a project LIFECYCLE pass that quietly proposed dormancy, flipped long-quiet
threads, and revived ones that came back. M's ruling 2026-08-02: eliminate the
chat, fold the real jobs. The narrative is gone — the morning brief, the staff
meeting and relationship-moves already carry that load. The lifecycle pass is
this module, and it runs as the `lifecycle` JOB inside the already-authorized
`maintenance` task (MAINT1), weekly instead of weekdaily. Less firing at the
CEO is the point, not a side effect.

DETECTION IS CODE, NEVER PROSE MATH
-----------------------------------
The Pulse version was ~60 lines of instructions asking a model to compute day
counts, compare them to three thresholds, and decide what to write. That is the
Bug #99 class twice over (arithmetic in prose) and the Bug #98 class once (a
write that only happens if the prose is obeyed). Every threshold here is a
constant, every comparison is a branch, and the whole plan is inspectable
before anything is written (`plan_lifecycle`), the way `identity_reconcile`
splits `plan_reconcile` from `run_identity_reconcile`.

ONE DERIVATION, SHARED WITH THE ASKING SURFACE
----------------------------------------------
`thread_activity.derive_thread_activity` with the workspace's own saved
stalled-projects `activity_event_types` and `honor_reclassifications=True` —
byte-for-byte the call `stall_detector` makes. That is deliberate and
load-bearing (FINDINGS F-54): the day-count this pass acts on and the
day-count `stalled projects` quotes to the CEO can never disagree for the same
thread on the same day. Never inline a `max(ts)` scan here.

WHAT IT WRITES, AND THROUGH WHAT
--------------------------------
  * the dormancy ASK  -> `brain_proposals.propose(kind="dormancy", ...)`, the
    LB2 rail, TTL and cooldown semantics unchanged, fingerprint still
    `dont_forget:<thread_id>` (the family natural key — renaming it would let
    a pre-migration fossil row and a new row propose the same thread twice).
    The rows are ON-DEMAND (STAFFCUT §3.7): they render where the CEO ASKS —
    `stalled projects` — and on no scheduled surface.
  * active -> dormant -> `thread_writer.update_thread` + ONE canonical
    `status_change` built by `thread_archive.build_thread_status_event`.
  * dormant -> archived -> `thread_archive.archive_thread`, THE archive
    chokepoint. Pulse's prose hand-wrote its own status_change here and
    stamped no `archived_at`, so the archive sorted under the empty string in
    MASTER_TRACKER's Recently Archived section and sank off the list — the
    same defect ARCHFIX fixed at the other two call sites. Routing this leg
    through the chokepoint closes it in passing.
  * revive -> the same typed status write as the dormant flip.

NO NEW AUTO-APPLY CLASS. Everything above is a THREAD STATUS transition, the
class the lifecycle state machine has always owned (ORG_AND_THREAD_MODEL
30/60/180) — not a Living Brain auto-tier proposal. `propose()` is called at
`tier="confirm"` only; nothing here touches `AUTO_ALLOWED`.

CAPS, AND WHY EVERY CLASS HAS ONE
---------------------------------
A workspace that has never run this pass can carry years of quiet threads. An
uncapped first fire would archive dozens of projects in one silent job, which
is exactly the shape of change nobody can review. Each class is bounded per
fire and the spill is RECORDED in the receipt (never silently dropped) — the
remainder is still due next week, and the pass self-heals like every other
maintenance job.

stdlib only. Every write is best-effort at the ITEM level: one thread that
cannot be written is recorded in `failed` and never aborts the rest.
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

# --- The lifecycle thresholds (ORG_AND_THREAD_MODEL 30/60/180, unchanged from
# --- the Pulse Phase 4 prose they replace). Days of quiet.
PROPOSE_DORMANT_DAYS = 30      # 4b — ask
AUTO_DORMANT_DAYS = 60         # 4b — flip, only after the ask expired unanswered
AUTO_ARCHIVE_DAYS = 180        # 4c — dormant long enough to file away
REVIVE_WINDOW_DAYS = 7         # 4d — activity this recent brings a thread back

# The proposal's TTL and its family natural key. BOTH are wire contracts:
# the TTL is what "expired unanswered" is measured against, and the prefix is
# what cross-rail dedup against a pre-migration fossil row keys on.
PROPOSAL_TTL_DAYS = 30
FINGERPRINT_PREFIX = "dont_forget:"

# How long an UNANSWERED question keeps authorising the active->dormant flip
# (LIFECYCLE1 fix round). Silence is the flip's whole warrant, and the first
# cut treated that warrant as permanent and unconditional. Two ways that bit:
#
#   * a thread whose ask expired months ago, then came back to life and went
#     quiet again, flipped on the OLD silence — forever, after every revive,
#     without the CEO ever being asked a second time;
#   * a thread carrying BOTH an old expired ask and a fresh OPEN one flipped
#     while the CEO was looking at the open question. Answering it would have
#     changed nothing, which is the worst thing a question can do.
#
# So the warrant is bounded and conditional: no OPEN ask may exist, and the
# expiry must be recent. The window is the TTL itself — the question stood
# unanswered for 30 days, so the silence it produced is good for 30 more. Past
# that the silence is stale evidence about a project that has had a month to
# change, and the honest move is to ask again rather than act on it. Any
# shorter and a fortnight of holiday costs the flip; any longer and "he never
# answered" outlives the question that was never answered.
FLIP_AUTHORITY_DAYS = PROPOSAL_TTL_DAYS

DETECTOR = "lifecycle"
SOURCE_SKILL = "lifecycle"

# The receipt. `lifecycle` is a canonical task id (receipts.CANONICAL_TASK_IDS)
# and `lifecycle_run` is its receipt type — the dispatcher's due-ness signal and
# this job's own success validator, per MAINT1's "a job vouches for itself".
RECEIPT_TASK_ID = "lifecycle"
RECEIPT_EVENT_TYPE = "lifecycle_run"

# Per-fire bounds. See the module docstring — spill is narrated in the receipt,
# stays due, and drains at the next weekly fire.
DEFAULT_CAPS = {
    "propose": 5,
    "dormant": 5,
    "archive": 5,
    # A revive is a CORRECTION of this pass's own earlier call, and leaving a
    # thread wrongly dormant is the failure mode that actually costs the CEO
    # something. Bounded far higher than the others on purpose.
    "revive": 25,
}

# Thread kinds this pass never touches. Same fence, same reasons, as
# `stall_detector.detect_stalled_projects`: deal rot is stage-dependent (PIPE1
# D7) and objective drift is binding-dependent (OBJ1), and each reports through
# its own surface with its own state machine. One quiet deal must never be
# flipped by two lifecycles.
EXCLUDED_KINDS = frozenset({"deal", "objective"})

# Statuses this pass reads. `archived` threads are eligible for REVIVE only.
_ACTIVE_STATUSES = frozenset({"active"})
_DORMANT_STATUSES = frozenset({"dormant"})
_REVIVABLE_STATUSES = frozenset({"dormant", "archived"})


def _clock_now(workspace_root=None):
    """CLOCK1 - the corroborated UTC instant this module stamps from.

    Swaps the CLOCK SOURCE only: every window, cutoff, threshold and output
    format around it is unchanged. A machine clock that has not synced used to
    write its own wrong reading straight into the permanent record; this reads
    the same clock, cross-checked against the newest timestamp the workspace
    already holds. Falls back to the raw machine clock if the helper is
    unavailable, so a stamp can never fail for want of corroboration.

    `workspace_root` is threaded in wherever the calling function already
    has one, because a helper that has to GUESS which workspace it is in
    guesses wrong exactly when it matters: a fire's early phases run in
    their own subprocesses, before anything has registered a root.
    """
    try:
        from trusted_now import trusted_now_utc

        return trusted_now_utc(workspace_root)
    except Exception:
        import datetime as _clock_dt

        return _clock_dt.datetime.now(_clock_dt.timezone.utc)


def _now_iso() -> str:
    return _clock_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts) -> Optional[_dt.datetime]:
    if not isinstance(ts, str) or not ts.strip():
        return None
    raw = ts.strip().replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = _dt.datetime.strptime(ts.strip()[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return dt.replace(tzinfo=None) if dt.tzinfo is None else \
        dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _entities(workspace_root) -> dict:
    """Shape-defensive entities read (flat or `entities`-wrapped). An
    unreadable file yields no threads rather than an exception — a maintenance
    job that crashes on a malformed substrate takes every later job with it."""
    try:
        p = Path(workspace_root) / "_hq" / "data" / "entities.json"
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data.get("entities") if isinstance(data.get("entities"), dict) else data


def _threads(workspace_root) -> list:
    ent = _entities(workspace_root)
    threads = ent.get("threads")
    if not isinstance(threads, list):
        threads = ent.get("projects")
    return [t for t in (threads or []) if isinstance(t, dict) and t.get("id")]


def _load_events(workspace_root) -> list:
    """Defensive event load, sharing the reader every other consumer uses."""
    try:
        from cru_match import load_events_defensively
        events, _ = load_events_defensively(_events_path(workspace_root))
        return events
    except Exception:
        return []


def _activity_types(workspace_root) -> list:
    """The workspace's OWN activity-type set — the same one `stall_detector`
    reads, so the two surfaces cannot quote different day-counts (F-54)."""
    from stall_detector import DEFAULT_CONFIG
    try:
        from skill_config_writer import load_skill_config
        saved = load_skill_config(workspace_root, "stalled-projects")
    except Exception:
        saved = None
    cfg = ((saved or {}).get("config") or {})
    return list(cfg.get("activity_event_types")
                or DEFAULT_CONFIG["activity_event_types"])


def _last_activity(workspace_root) -> dict:
    """{thread_id: datetime} — THE derivation. Never `thread.last_activity`
    (deprecated, F-61: no code path maintains it)."""
    try:
        from thread_activity import derive_thread_activity
        rows = derive_thread_activity(
            workspace_root,
            activity_types=_activity_types(workspace_root),
            honor_reclassifications=True)
    except Exception:
        return {}
    out: dict = {}
    for tid, act in (rows or {}).items():
        ts = getattr(act, "ts", None)
        if ts is None and isinstance(act, dict):
            ts = act.get("ts")
        # `derive_thread_activity` returns a tz-AWARE datetime, not a string.
        # An earlier draft passed it straight to `_parse`, which type-checks for
        # str, silently returned None, and left every thread falling back to
        # `first_seen` — every day-count in the pass was then the thread's AGE.
        # It looked like it worked (old threads still crossed the thresholds);
        # it was answering a different question. Normalize both shapes here.
        if isinstance(ts, _dt.datetime):
            dt = ts.astimezone(_dt.timezone.utc).replace(tzinfo=None) \
                if ts.tzinfo is not None else ts
        else:
            dt = _parse(ts)
        if dt is not None:
            out[str(tid)] = dt
    return out


def _quiet_days(thread: dict, last: dict, now: _dt.datetime) -> Optional[float]:
    """Days since the thread's most recent activity event; falls back to
    `first_seen` when the thread has no activity at all (the loudest stalls
    are things that started and never moved). None when neither is readable —
    an unplaceable thread is never flipped."""
    dt = last.get(str(thread.get("id")))
    if dt is None:
        dt = _parse(thread.get("first_seen"))
    if dt is None:
        return None
    quiet = (now - dt).total_seconds() / 86400.0
    # FUTURE-DATED ACTIVITY (LIFECYCLE1 fix round). A scheduled upcoming
    # meeting is an ordinary shape on a live thread, and its `ts` is ahead of
    # now — which made `quiet` NEGATIVE. A negative day-count is not a small
    # cosmetic wrong: the plan sorts on it, so a thread with a meeting next
    # month sorted ahead of one quiet for two years and took its cap slot, and
    # the receipt reported the negative number as fact. Clamp at zero: a thread
    # with something on the calendar is as un-quiet as a thread can be, which
    # is the honest reading AND the safe direction — it can never archive.
    return max(0.0, quiet)


def _last_status_change(events: list, thread_id: str) -> tuple:
    """`(when, was_automatic, unplaceable)` for this thread's most recent
    status change.

    The FLOOR a revive measures against — but only when a HUMAN set it, and
    that distinction is the whole point (LIFECYCLE1 fix round):

      * a MANUAL flip is a decision. The CEO said "this is dormant" yesterday;
        a meeting from the day before that does not get to overrule him today.
        Activity must POST-DATE a manual flip to revive.
      * an AUTOMATIC flip is this job's own arithmetic over the data it could
        see at the time. When data it could NOT see turns up — a meeting
        captured late, a transcript processed after the pass ran — the new
        evidence wins. Requiring it to post-date the flip meant an
        auto-archived project whose meeting was ingested an hour later stayed
        archived FOREVER: the derivation would never look past the flip again.

    A flip counts as automatic when this pass wrote it (`source_skill`) or when
    it carries the legacy `triggered_by: auto` / `auto_revive` marker the
    pre-LIFECYCLE1 prose used. Anything else is treated as a human gesture —
    the conservative reading, since mistaking a person for a machine is the
    error that overrides someone.

    `unplaceable` is True when ANY status change for this thread carries a
    timestamp we cannot read. Malformed lines are documented reality in this
    substrate, and round 1 simply skipped them — which handed the verdict to
    an OLDER event and, measured, let a HUMAN archive stamped `not-a-date` be
    overridden as if this pass had made it. When one exists we do not know
    which change is newest, so we cannot say the flip was automatic AND we
    cannot say the activity post-dates it. The caller refuses the revive
    outright: an explicit `revive [project]` still works, and a thread wrongly
    left archived is recoverable in a way an overridden decision is not.
    """
    newest = None
    newest_auto = False
    unplaceable = False
    for ev in events:
        if ev.get("type") != "status_change":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        tid = (ev.get("primary_thread_id") or d.get("primary_thread_id")
               or d.get("thread_id") or d.get("project_id"))
        if str(tid or "") != thread_id:
            continue
        dt = _parse(ev.get("ts"))
        if dt is None:
            # A status change we cannot PLACE on the timeline (malformed ts —
            # documented reality in this substrate, not a hypothetical). Round
            # 1 skipped it, which quietly handed the auto/manual verdict to an
            # OLDER event: a HUMAN archive stamped `not-a-date` was overridden
            # as if this pass had made it. Unplaceable means unknown, and
            # unknown must fall to the side that protects a person's decision.
            unplaceable = True
            continue
        if newest is not None and dt <= newest:
            continue
        newest = dt
        newest_auto = (ev.get("source_skill") == SOURCE_SKILL
                       or str(d.get("triggered_by") or "").startswith("auto"))
    if unplaceable:
        # We cannot prove the flip we can read is the LATEST one, so we can
        # claim neither that it was automatic nor that anything post-dates it.
        newest_auto = False
    return newest, newest_auto, unplaceable


def _dormancy_ask_state(events: list, now: _dt.datetime) -> dict:
    """{thread_id: {"open": bool, "expired_at": datetime|None}} for the
    dormancy ask, across BOTH rails.

    `open` is True while ANY ask for the thread is still live on either rail —
    it blocks the flip outright, because acting while a question is on screen
    makes answering it pointless.

    `expired_at` is the NEWEST expiry among asks that went unanswered, so the
    flip's warrant can be aged out (`FLIP_AUTHORITY_DAYS`). The first cut
    carried a bare `expired_unanswered` boolean, which had no age and could
    therefore never lapse.

    `answered_at` is the NEWEST resolution of any ask for the thread, and it is
    what makes an ANSWER beat an older silence. Round 1 dropped a resolved
    proposal on the floor (`continue`, recording nothing), so the comparison
    could not be made: an ask that lapsed 25 days ago and a `keep active` the
    CEO clicked 10 days ago left the stale silence in charge, and the thread
    flipped anyway. That sequence is not exotic — it is what the shipped pass
    itself produces: ask lapses, pass re-asks, CEO answers, and the first
    silence is still inside its 30-day warrant.

    The bp rail: a `brain_proposal` whose fingerprint is
    `dont_forget:<thread_id>`, then any `brain_proposal_resolved` (the CEO
    answered — never auto-flip over an answer) or `brain_proposal_expired`
    (the TTL passed in silence — the flip's precondition) for that id.

    The fossil rail: a pre-migration `dont_forget_dormant_proposal` event,
    expired when it is older than the TTL and nothing declined, snoozed or
    status-changed the target since. Fossil readers are permanent.
    """
    state: dict = {}

    def _slot(tid: str) -> dict:
        return state.setdefault(str(tid), {"open": False, "expired_at": None,
                                           "answered_at": None})

    def _note_expiry(slot: dict, when: Optional[_dt.datetime]) -> None:
        """Keep the NEWEST expiry. A thread can have been asked several times
        over its life; the warrant belongs to the most recent silence, never
        the first one."""
        if when is None:
            return
        if slot["expired_at"] is None or when > slot["expired_at"]:
            slot["expired_at"] = when

    def _note_answer(slot: dict, when: Optional[_dt.datetime]) -> None:
        """Keep the NEWEST answer, for the same reason and the opposite
        effect: the CEO's most recent word on this thread is the one that
        counts against the silence."""
        if when is None:
            return
        if slot["answered_at"] is None or when > slot["answered_at"]:
            slot["answered_at"] = when

    # --- bp rail
    opened: dict = {}          # proposal_id -> (thread_id, opened_dt)
    answered: dict = {}        # proposal_id -> when the CEO resolved it
    expired: dict = {}         # proposal_id -> when the sweep tombstoned it
    for ev in events:
        etype = ev.get("type")
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if etype == "brain_proposal":
            fp = str(d.get("fingerprint") or "")
            if not fp.startswith(FINGERPRINT_PREFIX):
                continue
            pid = d.get("proposal_id") or d.get("id")
            if pid:
                opened[str(pid)] = (fp[len(FINGERPRINT_PREFIX):],
                                    _parse(ev.get("ts")))
        elif etype == "brain_proposal_resolved":
            if d.get("proposal_id"):
                answered[str(d["proposal_id"])] = _parse(ev.get("ts"))
        elif etype == "brain_proposal_expired":
            if d.get("proposal_id"):
                expired[str(d["proposal_id"])] = _parse(ev.get("ts"))
    for pid, (tid, opened_dt) in opened.items():
        if not tid:
            continue
        slot = _slot(tid)
        if pid in answered:
            # RECORD it — do not drop it. An answer is evidence, and
            # `_flip_authorised` needs its date to weigh against a silence.
            # A resolution whose own ts will not parse still counts as an
            # answer, and it counts as the NEWEST thing known: fall back to
            # `now`, never the proposal's open date. The open date is the
            # OLDEST defensible value — under it, an expiry that landed
            # between the open and the real answer outranks the CEO's word
            # (asks can overlap across the two rails in historical data).
            # Same doctrine as the unplaceable status_change: when a stamp
            # cannot be read, the conservative reading wins and the flip
            # waits for a fresh ask to lapse AFTER the answer.
            _note_answer(slot, answered[pid] or now)
            continue
        if pid in expired:
            # Fall back to opened + TTL when the tombstone carries no readable
            # timestamp, rather than treating the warrant as ageless.
            when = expired[pid]
            if when is None and opened_dt is not None:
                when = opened_dt + _dt.timedelta(days=PROPOSAL_TTL_DAYS)
            _note_expiry(slot, when)
        else:
            slot["open"] = True

    # --- fossil rail
    fossil: dict = {}
    retired: set = set()
    for ev in events:
        etype = ev.get("type")
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        tid = ""
        for k in ("thread_id", "project_id", "person_id", "target_id", "target"):
            v = d.get(k)
            if isinstance(v, str) and v:
                tid = v
                break
        if not tid:
            tid = str(ev.get("primary_thread_id") or "")
        if not tid:
            continue
        if etype == "dont_forget_dormant_proposal":
            fossil[tid] = _parse(ev.get("ts"))
        elif etype in ("dont_forget_dormant_proposal_declined",
                       "dont_forget_snooze", "status_change"):
            retired.add(tid)
    for tid, opened_dt in fossil.items():
        if tid in retired:
            continue
        slot = _slot(tid)
        if opened_dt is not None and (now - opened_dt).days > PROPOSAL_TTL_DAYS:
            # A fossil row writes no tombstone, so its expiry is derived:
            # opened + the TTL is the moment the question stopped standing.
            _note_expiry(slot, opened_dt + _dt.timedelta(days=PROPOSAL_TTL_DAYS))
        else:
            slot["open"] = True

    return state


def _flip_authorised(ask: dict, now: _dt.datetime) -> bool:
    """Is an unanswered dormancy question standing behind this flip, and is it
    still recent enough to act on? Both halves are load-bearing — see
    FLIP_AUTHORITY_DAYS."""
    if ask.get("open"):
        return False
    when = ask.get("expired_at")
    if when is None:
        return False
    # An ANSWER supersedes an older silence. The CEO said something about this
    # thread after it last went unanswered, so the unanswered-ness is no longer
    # the latest thing we know — and `workspace-manager` PROMISES exactly this
    # ("an ANSWER of any kind stops the ladder; the CEO's decision stands").
    answered = ask.get("answered_at")
    if answered is not None and answered >= when:
        return False
    return 0 <= (now - when).total_seconds() / 86400.0 <= FLIP_AUTHORITY_DAYS


def plan_lifecycle(workspace_root, *, now_iso: Optional[str] = None,
                   caps: Optional[dict] = None) -> dict:
    """The full fire plan, WITHOUT writing anything.

    Returns {"now", "revive": [...], "propose": [...], "dormant": [...],
             "archive": [...], "spilled": {class: n}, "considered": n}.
    Each action dict: {thread_id, title, status, quiet_days, reason}.

    Order in the returned dict is the order `run_lifecycle_pass` executes and
    it is deliberate: REVIVE first, so a thread that came back is corrected
    before any rule downstream can flip it further.
    """
    now_iso = now_iso or _now_iso()
    now = _parse(now_iso) or _clock_now(workspace_root).replace(tzinfo=None)
    caps = {**DEFAULT_CAPS, **(caps or {})}

    threads = _threads(workspace_root)
    events = _load_events(workspace_root)
    last = _last_activity(workspace_root)
    asks = _dormancy_ask_state(events, now)

    revive: list = []
    propose: list = []
    dormant: list = []
    archive: list = []
    considered = 0

    for t in threads:
        if t.get("kind") in EXCLUDED_KINDS:
            continue
        tid = str(t["id"])
        status = str(t.get("status") or "")
        title = (t.get("canonical_name") or t.get("display_name")
                 or t.get("name") or "").strip()
        quiet = _quiet_days(t, last, now)
        if quiet is None:
            continue
        considered += 1
        ask = asks.get(tid) or {}

        # REVIVE is checked first and, when it fires, it is the WHOLE answer for
        # this thread — a thread that just came back must not also be judged for
        # archiving in the same pass. When it does NOT fire, control falls
        # through to the retire rules below. (An earlier draft `continue`d
        # unconditionally on any revivable status, which meant every dormant
        # thread returned to the top of the loop and the 180-day archive rule
        # was unreachable.)
        if status in _REVIVABLE_STATUSES:
            act_dt = last.get(tid)
            flipped, flipped_auto, flip_unplaceable = _last_status_change(
                events, tid)
            # The window is BOUNDED AT BOTH ENDS (LIFECYCLE1 fix round). The
            # first cut tested only the upper bound, so a future-dated event —
            # a scheduled upcoming meeting, the commonest shape there is —
            # satisfied "within the last 7 days" by being 400 days ahead of it,
            # and re-revived the same thread every single week. A thing that
            # has not happened yet is not activity that has happened.
            recent = (act_dt is not None
                      and 0 <= (now - act_dt).total_seconds() / 86400.0
                      <= REVIVE_WINDOW_DAYS)
            # A MANUAL flip is only undone by activity that came after it (see
            # `_last_status_change`); this pass's OWN flip yields to any
            # in-window activity, including evidence that arrived late.
            # An unreadable status change means the timeline cannot be
            # ordered, so neither branch below can be honestly evaluated —
            # refuse rather than guess in the direction that overrides a human.
            respects_flip = (not flip_unplaceable
                             and (flipped is None or flipped_auto
                                  or act_dt > flipped))
            if recent and respects_flip:
                revive.append({"thread_id": tid, "title": title,
                               "status": status, "quiet_days": round(quiet, 1),
                               "reason": "new activity since it went quiet"})
                continue

        if status in _DORMANT_STATUSES:
            if quiet > AUTO_ARCHIVE_DAYS:
                archive.append({"thread_id": tid, "title": title,
                                "status": status, "quiet_days": round(quiet, 1),
                                "reason": f"dormant and quiet {int(quiet)} days"})
            continue

        if status in _ACTIVE_STATUSES:
            if quiet > AUTO_DORMANT_DAYS and _flip_authorised(ask, now):
                dormant.append({"thread_id": tid, "title": title,
                                "status": status, "quiet_days": round(quiet, 1),
                                "reason": f"quiet {int(quiet)} days, the "
                                          "dormancy question went unanswered"})
            elif quiet > PROPOSE_DORMANT_DAYS and not ask.get("open"):
                propose.append({"thread_id": tid, "title": title,
                                "status": status, "quiet_days": round(quiet, 1),
                                "reason": f"{int(quiet)} days quiet"})

    plan = {"now": now_iso, "considered": considered, "spilled": {}}
    for name, rows in (("revive", revive), ("propose", propose),
                       ("dormant", dormant), ("archive", archive)):
        rows.sort(key=lambda r: (-r["quiet_days"], r["thread_id"]))
        cap = caps.get(name)
        if cap and len(rows) > cap:
            plan["spilled"][name] = len(rows) - cap
            rows = rows[:cap]
        plan[name] = rows
    return plan


def _set_status(workspace_root, thread_id: str, *, from_status: str,
                to_status: str, reason: str) -> None:
    """Record first, event second — the ARCHFIX ordering. A failed append
    leaves the record telling the truth; the reverse order would leave a
    status_change asserting a transition the substrate does not have."""
    import thread_writer
    from event_gate import append_event
    from thread_archive import (ARCHIVED_STATUS, build_thread_status_event,
                                clear_archive_stamps)

    thread_writer.update_thread(workspace_root, thread_id,
                                source_skill=SOURCE_SKILL, status=to_status)
    # LIFECYCLE1 fix round — a thread LEAVING `archived` must not keep the two
    # stamps that say it is archived. Through the writer that owns them, never
    # a field this module sets by hand. Done AFTER the status write and
    # tolerantly: the status change is the transition, and a thread that is
    # active-with-a-stale-stamp is recoverable, while an exception here would
    # abandon the revive halfway.
    if from_status == ARCHIVED_STATUS:
        try:
            clear_archive_stamps(workspace_root, thread_id,
                                 source_skill=SOURCE_SKILL)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(
                f"[lifecycle_pass] revive of {thread_id}: archive stamps not "
                f"cleared ({type(exc).__name__}: {exc}) — status is correct, "
                "the stamps are stale until the next pass.\n")
    append_event(_events_path(workspace_root),
                 [build_thread_status_event(thread_id,
                                            from_status=from_status,
                                            to_status=to_status,
                                            reason=reason,
                                            source_skill=SOURCE_SKILL)],
                 holder=SOURCE_SKILL)


def run_lifecycle_pass(workspace_root, *, apply: bool = False,
                       now_iso: Optional[str] = None,
                       caps: Optional[dict] = None,
                       write_receipt: bool = True) -> dict:
    """Execute the plan. `apply=False` (the default) is a dry run that writes
    NOTHING — not even the receipt — so the job stays due and a dry run can
    never be mistaken for a served slot (identity_reconcile's `--apply`
    lesson: the flag matters).

    Returns the plan dict plus {"applied": {class: n}, "failed": [...],
    "receipt": <event|None>}.
    """
    plan = plan_lifecycle(workspace_root, now_iso=now_iso, caps=caps)
    applied = {"revive": 0, "propose": 0, "dormant": 0, "archive": 0}
    failed: list = []
    if not apply:
        plan["applied"] = applied
        plan["failed"] = failed
        plan["receipt"] = None
        plan["dry_run"] = True
        return plan

    # 1. REVIVE — corrections first.
    for row in plan["revive"]:
        try:
            _set_status(workspace_root, row["thread_id"],
                        from_status=row["status"], to_status="active",
                        reason="new activity — back in play")
            applied["revive"] += 1
        except Exception as exc:  # noqa: BLE001 — one thread never aborts the pass
            failed.append({"class": "revive", "thread_id": row["thread_id"],
                           "error": f"{type(exc).__name__}: {exc}"})

    # 2. PROPOSE — the ask, on the LB2 rail, on-demand by kind (STAFFCUT §3.7).
    for row in plan["propose"]:
        try:
            from brain_proposals import propose
            res = propose(
                workspace_root,
                kind="dormancy",
                tier="confirm",
                fingerprint=f"{FINGERPRINT_PREFIX}{row['thread_id']}",
                detector=DETECTOR,
                evidence=f"{int(row['quiet_days'])} days quiet",
                render_line=(f"{row['title'] or 'this project'} has gone "
                             "quiet — still active, or archive it?"),
                ttl_days=PROPOSAL_TTL_DAYS,
                thread_id=row["thread_id"],
                action_tuples=[{"action": "active"}, {"action": "archive"},
                               {"action": "snooze 14d"}],
                extra={"title": row["title"]},
            )
            if res.get("status") == "proposed":
                applied["propose"] += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"class": "propose", "thread_id": row["thread_id"],
                           "error": f"{type(exc).__name__}: {exc}"})

    # 3. ACTIVE -> DORMANT.
    for row in plan["dormant"]:
        try:
            _set_status(workspace_root, row["thread_id"],
                        from_status=row["status"], to_status="dormant",
                        reason=row["reason"])
            applied["dormant"] += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"class": "dormant", "thread_id": row["thread_id"],
                           "error": f"{type(exc).__name__}: {exc}"})

    # 4. DORMANT -> ARCHIVED, through THE archive chokepoint. Never a
    #    hand-built status_change: that is what left Pulse's archives with no
    #    `archived_at` and no tracker row.
    for row in plan["archive"]:
        try:
            from thread_archive import archive_thread
            res = archive_thread(workspace_root, row["thread_id"],
                                 reason=row["reason"],
                                 source_skill=SOURCE_SKILL)
            if res.get("status") == "archived":
                applied["archive"] += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"class": "archive", "thread_id": row["thread_id"],
                           "error": f"{type(exc).__name__}: {exc}"})

    receipt = None
    if write_receipt:
        try:
            from receipts import log_receipt
            receipt = log_receipt(
                workspace_root, RECEIPT_TASK_ID,
                receipt_type=RECEIPT_EVENT_TYPE,
                fired_via="scheduled",
                surfaced=applied["propose"],
                extra_data={
                    "considered": plan["considered"],
                    "revived": applied["revive"],
                    "proposed": applied["propose"],
                    "went_dormant": applied["dormant"],
                    "archived": applied["archive"],
                    "spilled": plan["spilled"],
                    "errors": failed,
                })
        except Exception as exc:  # noqa: BLE001
            failed.append({"class": "receipt", "thread_id": "",
                           "error": f"{type(exc).__name__}: {exc}"})

    plan["applied"] = applied
    plan["failed"] = failed
    plan["receipt"] = receipt
    plan["dry_run"] = False
    return plan


def main(argv: Optional[list] = None) -> int:
    """CLI: `python3 lifecycle_pass.py --workspace <root> [--apply]`.

    Without `--apply` this prints the plan and writes nothing — including no
    receipt, so a dry run leaves the job due.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Command Room project lifecycle pass")
    parser.add_argument("--workspace", required=True,
                        help="absolute path to the workspace root")
    parser.add_argument("--apply", action="store_true",
                        help="execute the plan (without it: dry run, no writes)")
    parser.add_argument("--now", default=None,
                        help="frozen ISO instant (testing/simulation)")
    args = parser.parse_args(argv)
    result = run_lifecycle_pass(args.workspace, apply=args.apply,
                                now_iso=args.now)
    print(json.dumps(result, indent=2, default=str))
    return 0


__all__ = [
    "PROPOSE_DORMANT_DAYS",
    "AUTO_DORMANT_DAYS",
    "AUTO_ARCHIVE_DAYS",
    "REVIVE_WINDOW_DAYS",
    "PROPOSAL_TTL_DAYS",
    "FLIP_AUTHORITY_DAYS",
    "FINGERPRINT_PREFIX",
    "DEFAULT_CAPS",
    "EXCLUDED_KINDS",
    "RECEIPT_TASK_ID",
    "RECEIPT_EVENT_TYPE",
    "plan_lifecycle",
    "run_lifecycle_pass",
]


if __name__ == "__main__":
    sys.exit(main())
