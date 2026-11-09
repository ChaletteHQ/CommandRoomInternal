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
  * the ARCHIVE ask -> `brain_proposals.propose(kind="dormancy", ...)`, the
    same rail, fingerprint `dont_forget:archive:<thread_id>` (cr1#845: dormant
    is automatic, archive is a visible step). The pass never archives. The
    owner's `archive` answer lands through `thread_archive.archive_thread`,
    THE archive chokepoint, in the handler that dispatches the answer.
  * the owner's `active` answer -> `answer_active`, the one writer for it.
  * revive -> the same typed status write as the dormant flip, and any archive
    question still open on a project that is no longer dormant is retired.

NO NEW AUTO-APPLY CLASS. Everything above is a THREAD STATUS transition, the
class the lifecycle state machine has always owned (ORG_AND_THREAD_MODEL
30/60/180) — not a Living Brain auto-tier proposal. `propose()` is called at
`tier="confirm"` only; nothing here touches `AUTO_ALLOWED`.

CAPS, AND WHY EVERY CLASS HAS ONE
---------------------------------
A workspace that has never run this pass can carry years of quiet threads. An
uncapped first fire would flip dozens of projects in one silent job, which
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
import os
import sys
import time
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from entities_io import entities_collection  # noqa: E402

# --- The lifecycle thresholds (ORG_AND_THREAD_MODEL 30/60/180, unchanged from
# --- the Pulse Phase 4 prose they replace). Days of quiet.
PROPOSE_DORMANT_DAYS = 30      # 4b — ask
AUTO_DORMANT_DAYS = 60         # 4b — flip, only after the ask expired unanswered
AUTO_ARCHIVE_DAYS = 180        # 4c — dormant long enough to ASK about filing away (cr1#845)
REVIVE_WINDOW_DAYS = 7         # 4d — activity this recent brings a thread back

# The proposal's TTL and its family natural key. BOTH are wire contracts:
# the TTL is what "expired unanswered" is measured against, and the prefix is
# what cross-rail dedup against a pre-migration fossil row keys on.
#
# SWITCH2 5.2 (M's ruling 10, carried from night 10 as CAP-F12) — WHY THIS
# NAME IS LONGER THAN IT WAS. This constant was `PROPOSAL_TTL_DAYS`, which is
# also the name `commitment_policy` gives a DIFFERENT number (4). Two live
# constants, one name, no shared import: a reader of either file had to know
# which file they were in to know which number they were reading.
#
# They are not one number and must not become one. They govern two
# populations with two clocks:
#
#   * `commitment_policy.PROPOSAL_TTL_DAYS` = 4 — the CHIP. A suggestion
#     about one item that resolves ITSELF four days later, applying its own
#     evidence or retracting. Four days is M's standing window for a question
#     the reader is expected to see this week.
#   * this one = 30 — the DORMANT-ORG ask. "Has this gone quiet for good?"
#     is a question about a month of silence, and it is warranted for a month
#     (`FLIP_AUTHORITY_DAYS` below is deliberately the same number, for the
#     reason the comment there gives).
#
# So: two names, one per population, and a census pin that exactly ONE
# `PROPOSAL_TTL_DAYS` exists in the tree (`run_switch2_test.py` section [5]).
DORMANT_PROPOSAL_TTL_DAYS = 30
FINGERPRINT_PREFIX = "dont_forget:"
#: cr1#845 (ruling F2 of 2026-11-04: dormant is automatic, archive is a visible
#: step). The archive question rides the same dormancy rail, same kind and
#: same three answers, under its own natural key so the dormant ask and the
#: archive ask for one thread never collapse into each other.
ARCHIVE_FINGERPRINT_PREFIX = "dont_forget:archive:"
ANSWER_BACK_LINE = "{title} is back to active."
ANSWER_KEPT_LINE = "{title} stays active."
ANSWER_NOT_ACTIVE_LINE = ("{title} is {status}, so that answer changed nothing. "
                          "Say `revive {title}` to bring it back.")
ANSWER_NOT_SAVED_LINE = ("That answer could not be saved, because your project "
                         "records could not be read. Nothing changed; try it again.")
ANSWER_GONE_LINE = "That question is already answered; nothing changed."

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
FLIP_AUTHORITY_DAYS = DORMANT_PROPOSAL_TTL_DAYS

DETECTOR = "lifecycle"
SOURCE_SKILL = "lifecycle"

# The receipt. `lifecycle` is a canonical task id (receipts.CANONICAL_TASK_IDS)
# and `lifecycle_run` is its receipt type — the dispatcher's due-ness signal and
# this job's own success validator, per MAINT1's "a job vouches for itself".
RECEIPT_TASK_ID = "lifecycle"
RECEIPT_EVENT_TYPE = "lifecycle_run"

# cr1#665 (lane 3 of cr1#609) - THE DAILY PROJECT UPDATE. The Sunday
# `lifecycle` job stays the weekly deep pass, unchanged. The daily
# `project-update` job runs this module with a window: only the threads whose
# derived activity landed inside the window are considered, and only the
# REVIVE leg runs, through the same rule (`REVIVE_WINDOW_DAYS`), the same
# writer (`_set_status`) and the same cap. A project whose meeting landed
# yesterday is back to active this morning instead of next Sunday. The three
# QUIET legs (ask, flip to dormant, archive) are judgements about silence,
# and silence is a week's question, so they stay Sunday's.
#
# Its receipt is a `pack_run` under its OWN task id, never a
# `lifecycle_run`, so the daily run can never mark the Sunday pass served.
DAILY_TASK_ID = "project-update"
DAILY_RECEIPT_TYPE = "pack_run"

# cr1#738 - the detector on a `project` proposal from a connector job. One
# name for all of them, so a decline holds whichever job sees the board next.
SEEN_PROJECT_DETECTOR = "connector-projects"


def _effective_fired_via(explicit):
    """`receipts.effective_fired_via`, behind an import that cannot break.

    FIX3 F3-6, fix pass 2 (review N-1). This job's receipt hardcoded
    `fired_via="scheduled"` and had no resolver at all, so a typed brief on a
    merged seat recorded its own catch-up as a scheduled fire and nothing in
    the process could say otherwise. The resolver's floor off a merged seat
    is today's `scheduled`, so the un-merged fleet is byte-identical.
    """
    try:
        from receipts import effective_fired_via
    except Exception:  # noqa: BLE001 — a receipt never fails on a stamp
        return "scheduled"
    try:
        return effective_fired_via(explicit)
    except Exception:  # noqa: BLE001
        return "scheduled"

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

# Statuses this pass reads. cr1#753 (owner ruling 2026-11-04): an `archived`
# thread is RETIRED and never comes back on its own. Movement on it is
# recorded as its own kind of row, a question the owner answers once
# (`ARCHIVED_ACTIVITY_KIND`): a decline silences further notices for that
# project; its yes, or `revive <project>`, brings it back (`bring_back`). A
# `dormant` thread (gone quiet, never retired by the owner) still returns to
# active as before.
_ACTIVE_STATUSES = frozenset({"active"})
_DORMANT_STATUSES = frozenset({"dormant"})
_ARCHIVED_STATUSES = frozenset({"archived"})
_REVIVABLE_STATUSES = frozenset({"dormant"})

#: cr1#753 - the question written for movement on a retired project.
ARCHIVED_ACTIVITY_KIND = "archived_activity"
ARCHIVED_ACTIVITY_PREFIX = "archived_activity:"
ARCHIVED_ACTIVITY_DETECTOR = "lifecycle-archived"
ARCHIVED_ACTIVITY_TTL_DAYS = 30


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
    threads = entities_collection(ent, "projects")
    return [t for t in threads if isinstance(t, dict) and t.get("id")]


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
    return {tid: dt for tid, (dt, _seq) in _last_activity_full(workspace_root).items()}


def _last_activity_full(workspace_root) -> dict:
    """{thread_id: (datetime, seq|None)}: the same derivation, with the
    activity event's place in the book beside its time (cr1#761: the daily
    pass asks whether the activity REACHED the book after its last run, not
    only whether it is dated inside the window)."""
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
        seq = getattr(act, "seq", None)
        if seq is None and isinstance(act, dict):
            seq = act.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            seq = None
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
            out[str(tid)] = (dt, seq)
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
    archive_asks: dict = {}    # proposal_id -> thread_id (cr1#845)
    for ev in events:
        etype = ev.get("type")
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if etype == "brain_proposal":
            fp = str(d.get("fingerprint") or "")
            if not fp.startswith(FINGERPRINT_PREFIX):
                continue
            pid = d.get("proposal_id") or d.get("id")
            if pid and fp.startswith(ARCHIVE_FINGERPRINT_PREFIX):
                # cr1#845: only the ANSWER to an archive question speaks to
                # this ladder. Open or lapsed, it says nothing about an
                # active thread, and it shares this prefix, so it must not
                # be read as a dormancy ask for a thread called `archive:...`.
                archive_asks[str(pid)] = fp[len(ARCHIVE_FINGERPRINT_PREFIX):]
            elif pid:
                opened[str(pid)] = (fp[len(FINGERPRINT_PREFIX):],
                                    _parse(ev.get("ts")))
        elif etype == "brain_proposal_resolved":
            if d.get("proposal_id"):
                answered[str(d["proposal_id"])] = _parse(ev.get("ts"))
        elif etype == "brain_proposal_expired":
            if d.get("proposal_id"):
                expired[str(d["proposal_id"])] = _parse(ev.get("ts"))
    for pid, tid in archive_asks.items():
        if tid and pid in answered:
            _note_answer(_slot(tid), answered[pid] or now)
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
                when = opened_dt + _dt.timedelta(days=DORMANT_PROPOSAL_TTL_DAYS)
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
        if opened_dt is not None and (now - opened_dt).days > DORMANT_PROPOSAL_TTL_DAYS:
            # A fossil row writes no tombstone, so its expiry is derived:
            # opened + the TTL is the moment the question stopped standing.
            _note_expiry(slot, opened_dt + _dt.timedelta(days=DORMANT_PROPOSAL_TTL_DAYS))
        else:
            slot["open"] = True

    return state


def _archive_already_asked(workspace_root, now_iso: str) -> set:
    """Thread ids whose archive question needs no asking this pass: it stands
    open, or the owner answered it inside the shared cooldown. `propose` would
    drop these rows anyway, so leaving them in the plan spends the per-fire
    cap on nothing and the threads behind them are never asked."""
    from brain_proposals import open_brain_proposals
    from proposal_ledger import active_cooldowns

    keys = {r.get("fingerprint") for r in
            open_brain_proposals(workspace_root, now_iso=now_iso)}
    keys |= active_cooldowns(workspace_root, DETECTOR, now_iso=now_iso)
    return {str(k)[len(ARCHIVE_FINGERPRINT_PREFIX):] for k in keys
            if str(k).startswith(ARCHIVE_FINGERPRINT_PREFIX)}


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


def daily_window(workspace_root, *, since_iso: Optional[str] = None,
                 now_iso: Optional[str] = None) -> dict:
    """The window the daily project update reads on this run: the one
    maintenance window rule (`catchup.maintenance_window`, since the job's
    own last receipt, 30 days on a first run), or, when `since_iso` is
    handed in explicitly, the same rule over that point
    (`catchup.window_from_cursor`). No window math of its own."""
    import catchup

    if since_iso:
        return catchup.window_from_cursor(DAILY_TASK_ID, since_iso,
                                          now=now_iso)
    return catchup.maintenance_window(workspace_root, DAILY_TASK_ID,
                                      now=now_iso)


def _project_on_file(workspace_root, name: str):
    """`(thread id or None, ambiguous)`: the one project an EXACT name or
    alias lands on. A fuzzy hit is not a match here: a near name becomes a
    question for the owner, never activity on somebody else's project."""
    from entity_resolve import resolve_all

    hits = {str(r.record.get("id")) for r in resolve_all(workspace_root, name)
            if r.entity_type == "project"
            and r.matched_via in ("exact_alias", "exact_canonical")}
    # `aliases.json` is the resolver's alias table; a thread's own `aliases`
    # are on its record, so an exact one is read there.
    want = name.strip().lower()
    for t in _threads(workspace_root):
        if want in {str(a).strip().lower() for a in t.get("aliases") or []}:
            hits.add(str(t.get("id")))
    if len(hits) == 1:
        return next(iter(hits)), False
    return None, len(hits) > 1


def project_is_on_file(workspace_root, name: str) -> bool:
    """Does `name` land on a project in the book, one or more than one?"""
    return any(_project_on_file(workspace_root, name))


def note_seen_projects(workspace_root, seen, *, source_skill: str) -> dict:
    """Bring the projects a connector job saw into the book (cr1#738).

    `seen`: dicts of `{name, source_ref, ref, kind, ts}`, one per item,
    already past the connector's `update_projects` dial and its lists.

      * A project on file gets ONE activity event for this run, its latest
        item, dated when the item moved. An `interaction` is an activity
        type the pass already reads through THE derivation, so the daily
        project update revives the project by the rule it already has. It
        carries the pointer and no text from the item.
      * A project not on file becomes a `project` proposal with the item as
        its source pointer: the owner decides, on the brain family's own
        verbs. `propose` drops an open repeat and a recent decline. It is
        stamped `surface_on` from the day count the people share (cr1#773):
        no more than `DAILY_CONNECTOR_QUESTION_CAP` new connector questions
        come up on a day, and the rest wait with every row kept.

    An item with no date is no activity: it cannot be placed on a timeline,
    and dating it at the read would bring a quiet project back on the strength
    of an old file. A name that lands on two projects writes nothing.

    Returns the receipt fields, `n_projects_waiting` among them: every
    project row still held for a later day, not only this read's. A refused
    row is counted, never raised."""
    from event_gate import append_event
    from identity_reconcile import (CONNECTOR_SECTION_GAP_S,
                                    CONNECTOR_SECTION_ROWS, connector_clock)

    out: dict = {"projects_proposed": [], "projects_touched": [],
                 "n_projects_undated": 0, "n_projects_unnamed": 0,
                 "n_projects_ambiguous": 0, "project_errors": []}
    rows = [r for r in seen or [] if isinstance(r, dict)]
    today = connector_clock(workspace_root)[1]
    if not rows:
        out["n_projects_waiting"] = _projects_waiting(workspace_root, today)
        return out
    latest: dict = {}
    undated: set = set()
    new: dict = {}
    placed: dict = {}
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            out["n_projects_unnamed"] += 1
            continue
        key = name.lower()
        if key not in placed:
            placed[key] = _project_on_file(workspace_root, name)
        tid, ambiguous = placed[key]
        if ambiguous:
            out["n_projects_ambiguous"] += 1
        elif tid is None:
            new.setdefault(key, dict(row, name=name))
        else:
            when = _parse(row.get("ts"))
            if when is None:
                undated.add(tid)
            elif tid not in latest or when > latest[tid][0]:
                latest[tid] = (when, row)
    out["n_projects_undated"] = len(undated - set(latest))

    for tid, (when, row) in sorted(latest.items()):
        try:
            # The capture chokepoint refuses a repeat of (source_ref, summary),
            # so the date in the summary is what lets the same item count
            # again on a later day, and only once on any one day.
            written = append_event(_events_path(workspace_root), [{
                "type": "interaction",
                "source_skill": source_skill,
                "primary_thread_id": tid,
                "ts": row.get("ts"),
                "data": {"summary": (f"Moved in your {row.get('kind')} on "
                                     f"{when.date().isoformat()}"),
                         "direction": "connector_item",
                         "origin": "connector",
                         "source_ref": row.get("source_ref"),
                         "connector_source_ref": dict(row.get("ref") or {})},
            }], holder=source_skill)
        except Exception as exc:  # noqa: BLE001 - one project never aborts the job
            out["project_errors"].append({"thread_id": tid,
                                          "error": f"{type(exc).__name__}: {exc}"})
            continue
        if written:
            out["projects_touched"].append(tid)

    plan, read_to = _plan_seen_projects(workspace_root, new, out)
    for at in range(0, len(plan), CONNECTOR_SECTION_ROWS):
        time.sleep(CONNECTOR_SECTION_GAP_S if at else 0)
        _append_seen_projects(workspace_root, plan[at:at + CONNECTOR_SECTION_ROWS],
                              out, today, source_skill, read_to)
    out["n_projects_waiting"] = _projects_waiting(workspace_root, today)
    return out


def _plan_seen_projects(workspace_root, new: dict, out: dict) -> tuple:
    """Which new projects may be asked about, off one read and outside any
    lock (cr1#773). `(plan, read_to)`: each plan row carries its unstamped
    ledger rows, and `read_to` is the newest seq that read saw."""
    from brain_proposals import prepare_proposals

    if not new:
        return [], 0
    rows = sorted(new.items())
    read_to = max((e["seq"] for e in _load_events(workspace_root)
                   if isinstance(e.get("seq"), int)), default=0)
    specs = [dict(
        kind="project",
        tier="confirm",
        fingerprint=f"project:{key}",
        detector=SEEN_PROJECT_DETECTOR,
        evidence=f"seen on an item in your {row.get('kind')}",
        render_line=(f"{row['name']} showed up in your {row.get('kind')}: "
                     "track it as a project?"),
        action_tuples=[{"action": "confirm proposal"},
                       {"action": "dismiss proposal"},
                       {"action": "snooze proposal 7d"}],
        extra={"name": row["name"], "title": row["name"],
               "source_ref": row.get("source_ref"),
               "connector_source_ref": dict(row.get("ref") or {})})
        for key, row in rows]
    plan = []
    for (key, row), res in zip(rows, prepare_proposals(workspace_root, specs)):
        if res.get("status") == "error":
            out["project_errors"].append({"name": row["name"],
                                          "error": res.get("error")})
        elif res.get("status") == "proposed":
            plan.append({"name": row["name"], "fingerprint": f"project:{key}",
                         "rows": res["rows"]})
    return plan, read_to


def _append_seen_projects(workspace_root, plan: list, out: dict, today: str,
                          source_skill: str, read_to: int) -> None:
    """One short section: what a day holds is read here, so the stamps and the
    one append land here too. A project another job proposed since `read_to`
    is left out. A section that fails is counted against its names, and the
    next read brings them back."""
    from atomic_write import events_write_section
    from event_gate import append_event
    from events_io import iter_events_org_scoped
    from identity_reconcile import connector_slots, next_surface_day

    try:
        with events_write_section(_events_path(workspace_root),
                                  holder=source_skill):
            events = list(iter_events_org_scoped(workspace_root))
            slots = connector_slots(events)
            since = {(e.get("data") or {}).get("fingerprint") for e in events
                     if e.get("type") == "brain_proposal"
                     and isinstance(e.get("seq"), int) and e["seq"] > read_to}
            batch = []
            for item in plan:
                if item["fingerprint"] in since:
                    continue
                day = next_surface_day(slots, today)
                item["rows"][0]["data"]["surface_on"] = day
                slots.setdefault(day, set()).add(item["fingerprint"])
                batch += item["rows"]
            written = append_event(_events_path(workspace_root), batch,
                                   holder="brain_proposals") if batch else []
    except Exception as exc:  # noqa: BLE001 - contained per job, loud per name
        out["project_errors"] += [{"name": item["name"],
                                   "error": f"{type(exc).__name__}: {exc}"}
                                  for item in plan]
        return
    out["projects_proposed"] += [(w.get("data") or {}).get("name")
                                 for w in written]


def _projects_waiting(workspace_root, today: str) -> int:
    """How many open project rows are stamped for a later day and will still
    ask on it: a project in the book by then does not."""
    from brain_proposals import open_brain_proposals
    from confirm_flow import may_surface

    return sum(1 for row in open_brain_proposals(workspace_root)
               if row.get("kind") == "project"
               and not may_surface(row.get("surface_on"), today)
               and not project_is_on_file(
                   workspace_root, row.get("name") or row.get("title") or ""))


def _window_start_utc(window) -> Optional[_dt.datetime]:
    """The instant a window opens at, as the naive UTC this module compares
    in. `since_aware` carries the machine's offset; a bare `since` is
    machine-local naive and is localized first, never read as UTC. None when
    neither parses."""
    if not isinstance(window, dict):
        return None
    raw = window.get("since_aware")
    if isinstance(raw, str) and raw.strip():
        parsed = _parse(raw)
        if parsed is not None:
            return parsed
    raw = window.get("since")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        naive = _dt.datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    aware = naive.astimezone() if naive.tzinfo is None else naive
    return aware.astimezone(_dt.timezone.utc).replace(tzinfo=None)


def plan_lifecycle(workspace_root, *, now_iso: Optional[str] = None,
                   caps: Optional[dict] = None,
                   window: Optional[dict] = None) -> dict:
    """The full fire plan, WITHOUT writing anything.

    `window` (cr1#665): a maintenance window dict (`daily_window`). Given,
    this is the DAILY plan: only threads whose activity landed in the window
    are considered, and only the revive leg is planned. Omitted, the weekly
    plan exactly as before.

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
    full = _last_activity_full(workspace_root)
    last = {tid: dt for tid, (dt, _seq) in full.items()}
    seqs = {tid: seq for tid, (_dt_, seq) in full.items()}
    asks = _dormancy_ask_state(events, now)
    daily = window is not None
    daily_start = _window_start_utc(window) if daily else None
    daily_seq = window.get("since_seq") if daily and isinstance(window, dict) else None
    if not isinstance(daily_seq, int) or isinstance(daily_seq, bool):
        daily_seq = None

    revive: list = []
    propose: list = []
    dormant: list = []
    archive: list = []
    archived_activity: list = []
    considered = 0
    # cr1#753 - what the owner already answered about retired projects.
    answered = _archived_activity_answers(events)

    for t in threads:
        if t.get("kind") in EXCLUDED_KINDS:
            continue
        tid = str(t["id"])
        if daily:
            # cr1#665: the daily pass considers only a thread whose activity
            # landed in the window. cr1#761: "landed" is tested two ways,
            # by the activity's own time and by when it reached the book
            # (its place after the window's receipt), so a meeting held
            # before the last run but captured after it is this run's. No
            # start, no activity, or activity before the window on both
            # counts: Sunday's question, never guessed at here.
            act = last.get(tid)
            seq = seqs.get(tid)
            dated_in = (daily_start is not None and act is not None
                        and act >= daily_start)
            arrived_in = (daily_seq is not None and seq is not None
                          and seq > daily_seq)
            if act is None or not (dated_in or arrived_in):
                continue
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
                                  or (act_dt is not None
                                      and act_dt > flipped)))
            if recent and respects_flip:
                revive.append({"thread_id": tid, "title": title,
                               "status": status, "quiet_days": round(quiet, 1),
                               "reason": "new activity since it went quiet"})
                continue

        if status in _ARCHIVED_STATUSES:
            # cr1#753 - movement on a RETIRED project is never a silent
            # return to active. It is one question, asked once; the owner's
            # answer stands: silenced is silenced for good, and "bring it
            # back" (an applied answer newer than the archive) revives it
            # through the same typed status write as a dormant revive.
            act_dt = last.get(tid)
            flipped, flipped_auto, flip_unplaceable = _last_status_change(
                events, tid)
            moved = (act_dt is not None
                     and 0 <= (now - act_dt).total_seconds() / 86400.0
                     <= REVIVE_WINDOW_DAYS)
            # A HUMAN's archive is movement-proof against anything older
            # than it; this pass's OWN archive yields to in-window activity,
            # late-captured evidence included (the dormant revive's rule).
            after_archive = (not flip_unplaceable
                             and (flipped is None or flipped_auto
                                  or act_dt is None or act_dt > flipped))
            ans = answered.get(tid) or {}
            if ans.get("revive_at") is not None and (
                    flipped is None or ans["revive_at"] > flipped):
                revive.append({"thread_id": tid, "title": title,
                               "status": status, "quiet_days": round(quiet, 1),
                               "reason": "you asked to bring it back"})
                continue
            if moved and after_archive and not ans.get("silenced"):
                archived_activity.append({
                    "thread_id": tid, "title": title, "status": status,
                    "quiet_days": round(quiet, 1),
                    "activity_on": act_dt.date().isoformat() if act_dt else "",
                    "reason": "movement on a retired project"})
            continue

        if daily:
            # cr1#665: the ask, the dormant flip and the archive are about
            # silence, the weekly pass's question. The daily plan stops here.
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

    if archive:
        # cr1#845: before the cap, or the oldest threads hold every slot while
        # their questions stand and nobody behind them is ever asked.
        asked = _archive_already_asked(workspace_root, now_iso)
        archive = [r for r in archive if r["thread_id"] not in asked]

    plan = {"now": now_iso, "considered": considered, "spilled": {}}
    if daily:
        plan["daily"] = True
        plan["maintenance_window"] = window
    for name, rows in (("revive", revive), ("propose", propose),
                       ("dormant", dormant), ("archive", archive),
                       ("archived_activity", archived_activity)):
        rows.sort(key=lambda r: (-r["quiet_days"], r["thread_id"]))
        cap = caps.get(name)
        if cap and len(rows) > cap:
            plan["spilled"][name] = len(rows) - cap
            rows = rows[:cap]
        plan[name] = rows
    return plan


def _archived_activity_answers(events: list) -> dict:
    """cr1#753 - `{thread_id: {"silenced": bool, "revive_at": datetime|None}}`
    from the owner's answers to the archived-activity question, read off
    the ledger's proposal rows and their resolutions: a declined question
    silences further notices for that project for good; an applied one
    ("bring it back") revives it, when it post-dates the archive."""
    out: dict = {}
    fp_by_id: dict = {}
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        et = ev.get("type")
        if et == "brain_proposal":
            fp = str(d.get("fingerprint") or "")
            if fp.startswith(ARCHIVED_ACTIVITY_PREFIX) and d.get("proposal_id"):
                fp_by_id[str(d["proposal_id"])] = fp
        elif et == "brain_proposal_resolved":
            fp = fp_by_id.get(str(d.get("proposal_id") or ""))
            if not fp:
                continue
            tid = fp[len(ARCHIVED_ACTIVITY_PREFIX):]
            action = str(d.get("user_action") or "")
            row = out.setdefault(tid, {"silenced": False, "revive_at": None})
            if action in ("declined", "superseded"):
                row["silenced"] = True
            elif action in ("applied", "edited"):
                when = _parse(ev.get("ts"))
                if when is not None and (row["revive_at"] is None or when > row["revive_at"]):
                    row["revive_at"] = when
    return out


def _ask_archived_activity(workspace_root, rows: list, applied: dict,
                           failed: list) -> None:
    """cr1#753 - write the question for movement on a retired project, once
    per project (the proposal rail dedups an open one and honours a
    decline)."""
    if not rows:
        return
    from brain_proposals import propose
    for row in rows:
        try:
            res = propose(
                workspace_root,
                kind=ARCHIVED_ACTIVITY_KIND,
                tier="confirm",
                fingerprint=f"{ARCHIVED_ACTIVITY_PREFIX}{row['thread_id']}",
                detector=ARCHIVED_ACTIVITY_DETECTOR,
                evidence=f"movement on {row.get('activity_on') or 'a recent day'}",
                render_line=(f"{row['title'] or 'A retired project'} is retired, and "
                             "something moved on it: bring it back, or say not "
                             "relevant to keep it quiet for good."),
                ttl_days=ARCHIVED_ACTIVITY_TTL_DAYS,
                thread_id=row["thread_id"],
                # The decline silences for good (`_archived_activity_answers`),
                # so the row carries the decline whose label says permanent.
                action_tuples=[{"action": "confirm proposal"},
                               {"action": "proposal not relevant"}],
                extra={"title": row["title"], "activity_on": row.get("activity_on"),
                       "archived_activity": True},
            )
            if res.get("status") == "proposed":
                applied["archived_activity"] += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"class": "archived_activity",
                           "thread_id": row["thread_id"],
                           "error": f"{type(exc).__name__}: {exc}"})


def bring_back(workspace_root, thread_id: str, *, actor: str,
               source_skill: str = SOURCE_SKILL,
               reason: str = "You brought it back") -> dict:
    """cr1#753 - the owner's "bring it back": a dormant or an archived project
    to active, as the owner's act, through the one typed status write, so a
    retired project's archive stamps clear with it. Every door lands here: a
    typed `revive <project>`, the question's yes, the Hub's press.

    The questions standing on the project are answered too: the retired one
    as applied, the dormancy one as declined, or the next pass would read the
    old silence as still standing and flip the project straight back; an
    archive question (cr1#845) as superseded, its premise gone. The dormancy
    ask's cooldown is written as `answer_active` writes it, so the next pass
    does not ask it again.

    Answers `{"status": "updated", "from_status"}`, or `{"status":
    "not_found"}`, or `{"status": "noop", "from_status"}` for a project that
    is neither dormant nor archived, and then writes nothing."""
    from event_gate import append_event

    record = next((t for t in _threads(workspace_root) if t.get("id") == thread_id), None)
    if record is None:
        return {"status": "not_found"}
    status = str(record.get("status") or "").lower()
    if status not in _REVIVABLE_STATUSES | _ARCHIVED_STATUSES:
        return {"status": "noop", "from_status": status}
    _set_status(workspace_root, thread_id, from_status=status, to_status="active",
                reason=reason, source_skill=source_skill)
    # The owner just said this project is live while the clock still reads it
    # quiet: the dormancy ask's cooldown, as `answer_active` writes it, or the
    # next pass asks the same question again.
    from proposal_ledger import append_decision
    append_decision(workspace_root, pass_name=DETECTOR,
                    fingerprint=f"{FINGERPRINT_PREFIX}{thread_id}",
                    user_action="declined", summary=reason)
    # The archive question (cr1#845) loses its premise once the project is
    # active: `superseded`, as the pass's own retirement writes it, so the
    # record never reads as the owner's decline.
    answers = {ARCHIVED_ACTIVITY_PREFIX + thread_id: "applied",
               FINGERPRINT_PREFIX + thread_id: "declined",
               ARCHIVE_FINGERPRINT_PREFIX + thread_id: "superseded"}
    events = _load_events(workspace_root)
    resolved = {str((e.get("data") or {}).get("proposal_id") or "") for e in events
                if e.get("type") == "brain_proposal_resolved"}
    for ev in events:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        answer = answers.get(str(d.get("fingerprint") or ""))
        pid = str(d.get("proposal_id") or "")
        if ev.get("type") == "brain_proposal" and answer and pid and pid not in resolved:
            append_event(_events_path(workspace_root),
                         [{"type": "brain_proposal_resolved", "source_skill": source_skill,
                           "data": {"proposal_id": pid, "user_action": answer,
                                    "resolved_by": actor, "note": reason}}],
                         holder=SOURCE_SKILL)
    return {"status": "updated", "from_status": status}


def _set_status(workspace_root, thread_id: str, *, from_status: str,
                to_status: str, reason: str,
                source_skill: str = SOURCE_SKILL) -> None:
    """Record first, event second — the ARCHFIX ordering. A failed append
    leaves the record telling the truth; the reverse order would leave a
    status_change asserting a transition the substrate does not have."""
    import thread_writer
    from event_gate import append_event
    from thread_archive import (ARCHIVED_STATUS, build_thread_status_event,
                                clear_archive_stamps)

    thread_writer.update_thread(workspace_root, thread_id,
                                source_skill=source_skill, status=to_status)
    # LIFECYCLE1 fix round — a thread LEAVING `archived` must not keep the two
    # stamps that say it is archived. Through the writer that owns them, never
    # a field this module sets by hand. Done AFTER the status write and
    # tolerantly: the status change is the transition, and a thread that is
    # active-with-a-stale-stamp is recoverable, while an exception here would
    # abandon the revive halfway.
    if from_status == ARCHIVED_STATUS:
        try:
            clear_archive_stamps(workspace_root, thread_id,
                                 source_skill=source_skill)
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
                                            source_skill=source_skill)],
                 holder=source_skill)


def answer_active(workspace_root, proposal_id: str, *, resolved_by: str,
                  source_skill: str = "stalled-projects") -> dict:
    """A WRITER. The owner's `active` on a `bp_` dormancy row (cr1#845).

    The plain dormancy ask (`active` means nothing changes) resolves the row
    `declined` and writes nothing else. The ARCHIVE question (`data.ask` is
    `archive`) is asked about a project already dormant, so `active` there
    also flips it dormant -> active, in the owner's name, and closes the
    dormancy ask's cooldown too: the project is still quiet by the clock, and
    the owner has just answered, so the next pass must neither re-ask it nor
    read an older silence as standing.

    Returns `{status, line, flipped?}`: `kept_active`; `noted` when a plain
    ask lands on a project that is not active (the row is answered, the status
    stays, and the line says so); `blocked` when the project records cannot be
    read; or `already_resolved` when the row is gone (both write nothing)."""
    import brain_proposals as bp
    from proposal_ledger import append_decision

    row = next((r for r in bp.open_brain_proposals(workspace_root,
                                                   include_lapsed=True)
                if r["id"] == proposal_id), None)
    if row is None or row.get("kind") != "dormancy":
        return {"status": "already_resolved", "proposal_id": proposal_id,
                "line": ANSWER_GONE_LINE}
    tid = str(row.get("thread_id") or "")
    threads = _threads(workspace_root)
    if not threads:
        # An unreadable book says nothing about this project, and answering
        # would close the question without acting on it.
        return {"status": "blocked", "proposal_id": proposal_id,
                "line": ANSWER_NOT_SAVED_LINE}
    thread = next((t for t in threads if str(t.get("id")) == tid), {})
    flipped = (row.get("ask") == "archive"
               and thread.get("status") in _DORMANT_STATUSES)
    if flipped:
        _set_status(workspace_root, tid, from_status=thread["status"],
                    to_status="active", reason="the owner said active",
                    source_skill=source_skill)
        append_decision(workspace_root, pass_name=row["detector"],
                        fingerprint=f"{FINGERPRINT_PREFIX}{tid}",
                        user_action="declined",
                        summary=row.get("evidence") or "")
    bp.resolve_proposal(workspace_root, proposal_id, "declined",
                        resolved_by=resolved_by, source_skill=source_skill)
    title = row.get("title") or "That project"
    status = str(thread.get("status") or "")
    if flipped or status in _ACTIVE_STATUSES:
        return {"status": "kept_active", "flipped": bool(flipped),
                "line": (ANSWER_BACK_LINE if flipped
                         else ANSWER_KEPT_LINE).format(title=title)}
    return {"status": "noted", "flipped": False,
            "line": ANSWER_NOT_ACTIVE_LINE.format(title=title,
                                                  status=status or "not on file")}


def _retire_settled_archive_questions(workspace_root, now_iso: str) -> int:
    """Retire every open archive question whose project is no longer dormant
    (cr1#845). The question's premise is gone, whichever door changed the
    status: this pass's revive, the Hub, or a typed `revive` or `archive`.
    `superseded`, so the record never reads as the owner's decline."""
    import brain_proposals as bp

    status = {str(t["id"]): t.get("status") for t in _threads(workspace_root)}
    if not status:
        return 0  # an unreadable book says nothing about any project
    n = 0
    for row in bp.open_brain_proposals(workspace_root, now_iso=now_iso):
        fp = str(row.get("fingerprint") or "")
        if not fp.startswith(ARCHIVE_FINGERPRINT_PREFIX):
            continue
        tid = fp[len(ARCHIVE_FINGERPRINT_PREFIX):]
        if tid not in status or status[tid] in _DORMANT_STATUSES:
            continue  # a missing id proves nothing; only a known status does
        res = bp.resolve_proposal(workspace_root, row["id"], "superseded",
                                  resolved_by="system",
                                  source_skill=SOURCE_SKILL,
                                  note="the project is no longer dormant")
        if res.get("status") == "resolved":
            n += 1
    return n


def run_lifecycle_pass(workspace_root, *, apply: bool = False,
                       now_iso: Optional[str] = None,
                       caps: Optional[dict] = None,
                       write_receipt: bool = True,
                       fired_via: Optional[str] = None,
                       triggered_by: Optional[str] = None,
                       window: Optional[dict] = None) -> dict:
    """Execute the plan. `apply=False` (the default) is a dry run that writes
    NOTHING — not even the receipt — so the job stays due and a dry run can
    never be mistaken for a served slot (identity_reconcile's `--apply`
    lesson: the flag matters).

    `window` (cr1#665): the DAILY project update. `plan_lifecycle`'s daily
    plan (revive only, threads active in the window), the same writer, and
    ONE `pack_run` receipt under `DAILY_TASK_ID` naming the window. Omitted,
    the weekly pass exactly as before.

    Returns the plan dict plus {"applied": {class: n}, "failed": [...],
    "receipt": <event|None>}.
    """
    daily = window is not None
    plan = (plan_lifecycle(workspace_root, now_iso=now_iso, caps=caps,
                           window=window) if daily
            else plan_lifecycle(workspace_root, now_iso=now_iso, caps=caps))
    applied = {"revive": 0, "propose": 0, "dormant": 0, "archive": 0,
               "archived_activity": 0,
               "archive_proposed": 0, "archive_question_retired": 0}
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

    # 1b. A question about a project that came back has nothing left to ask.
    try:
        applied["archive_question_retired"] = _retire_settled_archive_questions(
            workspace_root, plan["now"])
    except Exception as exc:  # noqa: BLE001
        failed.append({"class": "archive_question", "thread_id": "",
                       "error": f"{type(exc).__name__}: {exc}"})
    # 1c. cr1#753 - the question for movement on a retired project.
    _ask_archived_activity(workspace_root, plan.get("archived_activity") or [],
                           applied, failed)

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
                ttl_days=DORMANT_PROPOSAL_TTL_DAYS,
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

    # 4. DORMANT -> the ARCHIVE QUESTION (cr1#845; ruling F2 of 2026-11-04:
    #    dormant is automatic, archive is a visible step). The pass never
    #    archives on its own: it proposes, on the dormancy rail, and the
    #    owner's `archive` answer lands through THE archive chokepoint
    #    (`thread_archive.archive_thread`, the proposal's own dispatch) with
    #    `archived_at` stamped. `propose` drops an open repeat and a recent
    #    decline, so a standing question is never asked twice.
    for row in plan["archive"]:
        try:
            from brain_proposals import propose
            res = propose(
                workspace_root,
                kind="dormancy",
                tier="confirm",
                fingerprint=f"{ARCHIVE_FINGERPRINT_PREFIX}{row['thread_id']}",
                detector=DETECTOR,
                evidence=f"dormant and quiet {int(row['quiet_days'])} days",
                render_line=(f"{row['title'] or 'this project'} has been dormant "
                             f"{int(row['quiet_days'])} days — archive it?"),
                ttl_days=DORMANT_PROPOSAL_TTL_DAYS,
                thread_id=row["thread_id"],
                action_tuples=[{"action": "archive"}, {"action": "active"},
                               {"action": "snooze 14d"}],
                extra={"title": row["title"], "ask": "archive"},
            )
            if res.get("status") == "proposed":
                applied["archive_proposed"] += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"class": "archive", "thread_id": row["thread_id"],
                           "error": f"{type(exc).__name__}: {exc}"})

    receipt = None
    if write_receipt and daily:
        # cr1#665: the daily project update's own receipt, naming its window.
        try:
            from catchup import (MAINTENANCE_WINDOW_FIELD,
                                 WINDOW_INCOMPLETE_FIELD,
                                 receipt_window_marker)
            from receipts import log_receipt
            extra = {
                "mode": "daily",
                "considered": plan["considered"],
                "revived": applied["revive"],
                "archived_activity_asked": applied["archived_activity"],
                "spilled": plan["spilled"],
                "errors": failed,
                MAINTENANCE_WINDOW_FIELD: window,
            }
            # A capped run left revives inside the window: the batch-cap
            # marker sends the next daily run back over the same window (a
            # re-read is idempotent: a revived thread is active now) instead
            # of leaving the spill to Sunday.
            marker = receipt_window_marker(
                {"start": window.get("since"), "end": window.get("until")},
                incomplete=bool(plan["spilled"]))
            if marker:
                extra[WINDOW_INCOMPLETE_FIELD] = marker
            receipt = log_receipt(
                workspace_root, DAILY_TASK_ID,
                receipt_type=DAILY_RECEIPT_TYPE,
                fired_via=_effective_fired_via(fired_via),
                triggered_by=triggered_by,
                extra_data=extra)
        except Exception as exc:  # noqa: BLE001
            failed.append({"class": "receipt", "thread_id": "",
                           "error": f"{type(exc).__name__}: {exc}"})
    elif write_receipt:
        try:
            from receipts import log_receipt
            receipt = log_receipt(
                workspace_root, RECEIPT_TASK_ID,
                receipt_type=RECEIPT_EVENT_TYPE,
                # FIX3 F3-6, fix pass 2: the SEAT answers, not a literal.
                fired_via=_effective_fired_via(fired_via),
                triggered_by=triggered_by,
                surfaced=applied["propose"] + applied["archive_proposed"],
                extra_data={
                    "considered": plan["considered"],
                    "revived": applied["revive"],
                "archived_activity_asked": applied["archived_activity"],
                    "proposed": applied["propose"],
                    "went_dormant": applied["dormant"],
                    "archived": applied["archive"],   # always 0 since cr1#845; kept so old readers parse
                    "archive_proposed": applied["archive_proposed"],
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
    # FIX3 F3-6, fix pass 2 (review N-1). The rendered leg ends in these two
    # flags and this command used to exit 2 on them, so the Sunday family's
    # lifecycle leg died with a usage error whenever a typed surface ran the
    # catch-up.
    parser.add_argument("--fired-via", default=None,
                        choices=("scheduled", "manual", "catchup"),
                        help="how this run was started; the seat decides "
                             "when nothing is said")
    parser.add_argument("--triggered-by", default=None,
                        help="the surface that asked for this run")
    # cr1#665: the daily project update. `--daily` reads the one maintenance
    # window (since this job's own last receipt, 30 days on a first run);
    # `--since <iso>` names the window's start instead, under the same rule.
    parser.add_argument("--daily", action="store_true",
                        help="the daily project update: revive projects "
                             "active in the window; receipt under "
                             "project-update")
    parser.add_argument("--since", default=None,
                        help="with --daily: the window's start (ISO); "
                             "default is since this job's own last receipt")
    args = parser.parse_args(argv)
    if args.since and not args.daily:
        parser.error("--since needs --daily")
    # The same export the other job CLIs make, so every composer below reads
    # who asked from one place instead of a dozen signatures.
    if getattr(args, "triggered_by", None):
        os.environ["CR_TRIGGERED_BY"] = str(args.triggered_by)
    window = (daily_window(args.workspace, since_iso=args.since,
                           now_iso=args.now) if args.daily else None)
    result = run_lifecycle_pass(args.workspace, apply=args.apply,
                                now_iso=args.now,
                                fired_via=args.fired_via,
                                triggered_by=args.triggered_by,
                                window=window)
    print(json.dumps(result, indent=2, default=str))
    return 0


__all__ = [
    "PROPOSE_DORMANT_DAYS",
    "AUTO_DORMANT_DAYS",
    "ARCHIVE_FINGERPRINT_PREFIX",
    "AUTO_ARCHIVE_DAYS",
    "REVIVE_WINDOW_DAYS",
    "answer_active",
    "DORMANT_PROPOSAL_TTL_DAYS",
    "FLIP_AUTHORITY_DAYS",
    "FINGERPRINT_PREFIX",
    "DEFAULT_CAPS",
    "EXCLUDED_KINDS",
    "RECEIPT_TASK_ID",
    "RECEIPT_EVENT_TYPE",
    "DAILY_TASK_ID",
    "DAILY_RECEIPT_TYPE",
    "SEEN_PROJECT_DETECTOR",
    "daily_window",
    "note_seen_projects",
    "plan_lifecycle",
    "run_lifecycle_pass",
]


if __name__ == "__main__":
    sys.exit(main())
