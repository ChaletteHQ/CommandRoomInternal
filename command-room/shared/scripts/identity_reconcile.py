#!/usr/bin/env python3
"""People identity reconciler — three-tier identity model + backfill (SPEC PID1).

WHY THIS EXISTS
The propose-only posture on people was burying the user: a ~25-row identity
queue every staff meeting, one person fragmented across multiple rows, and
FS-19-suppressed "already on file" proposals sitting open forever. Where the
evidence is strong, people-adding becomes AUTOMATIC — safely, on the existing
LB1 auto rail (`AUTO_ALLOWED["person_org_creation_structured_fact"]` + the
registered archive reverser), narrated and batch-undoable. Where it isn't, the
queue gets radically cheaper: one person = one identity-clustered row.

THE THREE TIERS (computed in code, per cluster — a rule table, never a score;
all four §0 decisions RULED by M 2026-07-18):

  AUTO-ADD       multi-token full name AND (an OBSERVED email in the cluster's
                 own captured text that the text TIES to this person
                 (`person_backlog_sweep._attributed_email`, cr1#769), OR ≥2
                 independent source families with an identifier on one of
                 them (cr1#754: a family is the kind stamped on the proposal
                 when it was written, never a word in its text, and two
                 sightings of a bare name do not add a person; see
                 `cluster_identifier`), OR a calendar-attendee record.
                 A calendar attendee counts two ways: as a machine-written
                 `calendar:<address>` source_ref, which keeps its address, or
                 as a display name and address in the evidence ("Name
                 <address>"), which ties under the same rule as anything else.
                 An address merely mentioned in calendar prose does not tie:
                 that row stays a question.)
                 AND zero same-name collision AND not already on file.
                 Applied via `people_writer.auto_add_person` (its internal
                 same-name gate is defense-in-depth: needs_confirm demotes to
                 CONFIRM, never forks), tombstones stamped with ONE
                 brain_batch_id so `brain_undo.undo_batch` reverses the run.
  CONFIRM        everything ambiguous. Lone first names live here permanently
                 (Bug #19); same-name collisions live here even with an email.
  MERGE-PROPOSE  evidence pointing at an EXISTING record. Never an auto-merge
                 (`merge_person_into` has no reverser — confirm-only forever).
                 Exact-email matches MAY silently resolve `same_as` (§0-2
                 ruling: YES) — EXCEPT role-shaped addresses (info@/office@/
                 admin@/support@/hello@-class), which stay merge-propose rows:
                 a shared inbox is the one way an exact email lies about
                 identity.

Plus: unnamed Granola speakers NEVER become person rows — they become
`unidentified_attendee_observed` annotations (fully silent per §0-4, one count
line in the weekly staff meeting), resolved when a name/known-email attaches.

ONE entry point (`run_identity_reconcile`) serves both the Sunday
`identity-reconcile` maintenance job (STEADY_CAPS) and the one-time
per-workspace backfill (BACKFILL_CAPS) — one classifier, two cap sets.
`person_backlog_sweep.plan_sweep` delegates its classification here (D7 —
one rule table, never forked). Caps spill NARRATED, never silent (§0-3).

DRY-RUN IS THE DEFAULT. `--apply` performs the writes. stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional


def _effective_fired_via(explicit):
    """`receipts.effective_fired_via`, behind an import that cannot break."""
    try:
        from receipts import effective_fired_via
    except Exception:  # noqa: BLE001 - a resolver that raises is worse
        return explicit if explicit is not None else "scheduled"
    try:
        return effective_fired_via(explicit)
    except Exception:  # noqa: BLE001
        return explicit if explicit is not None else "scheduled"

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# TZDATE3 — canonical date localizer (tz.py, hoisted TZDATE2). Guarded: a
# stripped install missing tz.py keeps the pre-TZDATE3 UTC-slice behavior
# exactly (matches the render_*.py precedent).
try:
    from tz import localize_date as _localize_date  # noqa: E402
except ImportError:
    def _localize_date(ts: str | None, workspace_path: str | None = None) -> str:
        return ts[:10] if isinstance(ts, str) and ts else ""

# ---------------------------------------------------------------------------
# Policy constants (§0 rulings — widen by ruling, not by drift)
# ---------------------------------------------------------------------------

# §0-3 caps. Overflow spills to the review pile and is COUNTED in the receipt
# (no-silent-caps rule) — never silently dropped.
STEADY_CAPS = {"auto_add": 10, "merge_propose": 10, "aged_default": 200}
BACKFILL_CAPS = {"auto_add": 15, "merge_propose": 10, "aged_default": 200}
# IDENT1 rule 3's cap is an order of magnitude above the other two ON PURPOSE.
# The add and propose caps exist to stop a bad run FLOODING a customer with
# new rows; the aged-default pass only ever REMOVES rows, under one batch id
# and one `undo`. A cap of ten there would leave a book of 85 aged questions
# needing nine quiet weeks to drain, which is the leak, not the fix. It is
# capped at all so a corrupted queue cannot write unbounded events in one go.

# §0-2 role-address guard: local parts that mark a SHARED inbox — an exact
# email match on one of these must NOT silently link (it stays a
# merge-propose row), and a shared role address is never duplicate-suspect
# evidence between existing records. The pattern list is a constant with a
# test (the ruling's exact words).
#
# CONTACT1 second-eyes 2026-08-03 WIDENED this set and made the match
# TOKEN-BASED. A survey of realistic shared-inbox local parts found 45 of 55
# clearing the guard entirely — `orders@`, `invoices@`, `accounting@`,
# `dispatch@`, `claims@` and their kin were all reading as people.
#
# WHAT WIDENING COSTS, said accurately. Most callers use this to WITHHOLD, and
# for them a wider set is strictly safer: no silent link on an exact email
# match, no shared inbox corroborating a mention, no auto-created contact from
# a mailbox. Two callers are NOT in that shape and should be read before
# widening again:
#   * `scan_existing_duplicates` uses it to EXCLUDE an address from duplicate
#     suspicion, so a wider set means fewer duplicate pairs proposed — the
#     permissive direction, though it only ever costs a proposal the user
#     could still make by hand.
#   * CONTACT1's own gate refuses SILENTLY. A person caught here does not
#     become a question; they simply do not become a contact from mail. That
#     is the ruled fail-safe posture, not a free lunch.
#
# KNOWN FALSE-POSITIVE CLASS, accepted deliberately: a real person whose local
# part carries a role token — `mark.orders@`, `ana.press@`, an `ap` or `ar`
# that is somebody's initials — reads as a mailbox. They get no record and no
# question from this rail; they still arrive through every other identity
# path. Carving them back out would mean judging a person from an address,
# which is the inference this whole gate exists to refuse. A person-shaped
# carve-out is logged as a follow-up, not built here.
#
# The size and contents of this set are PINNED by test (the AUTO_ALLOWED
# pattern): it may not silently shrink OR grow, because both directions change
# who becomes a contact without anyone deciding to.
ROLE_ADDRESS_LOCAL_PARTS = frozenset({
    # -- the original ruling's words, unchanged ---------------------------
    "info", "office", "admin", "support", "hello", "contact", "sales",
    "team", "billing", "accounts", "help", "hr", "careers", "jobs",
    "noreply", "no-reply", "donotreply", "mail", "enquiries", "inquiries",
    # -- money in and money out -------------------------------------------
    "accounting", "accountspayable", "accountsreceivable", "payable",
    "receivable", "invoice", "invoices", "invoicing", "payments", "payment",
    "remittance", "remittances", "collections", "finance", "payroll",
    "bookkeeping", "treasury",
    # The two-letter finance desks. Short enough to be worth stating why they
    # are safe: `ap` and `ar` are accounts-payable/receivable inboxes, and a
    # personal local part does not carry either as a whole token.
    "ap", "ar",
    # -- selling, buying, and the paperwork between ------------------------
    "orders", "order", "quotes", "quote", "estimates", "estimating", "bids",
    "rfp", "rfps", "proposals", "procurement", "purchasing", "vendors",
    "suppliers", "contracts",
    # -- service desks -----------------------------------------------------
    "service", "services", "customerservice", "customercare", "clientservices",
    "clientservice", "helpdesk", "servicedesk", "care", "claims", "returns",
    "rma", "warranty", "reception", "frontdesk", "concierge",
    # -- scheduling and logistics ------------------------------------------
    "dispatch", "scheduling", "bookings", "reservations", "appointments",
    "shipping", "receiving", "warehouse", "logistics", "inventory", "parts",
    "operations",
    # -- the company talking, not a person ---------------------------------
    "press", "media", "marketing", "newsletter", "news", "updates",
    "communications", "events", "rsvp", "webinars", "community", "partners",
    "partnerships", "affiliates", "referrals", "feedback", "unsubscribe",
    # -- back office and governance ----------------------------------------
    "legal", "compliance", "privacy", "security", "safety", "quality",
    "training", "recruiting", "recruitment", "hiring", "talent",
    "applications", "membership", "members", "donations", "giving",
    "volunteer",
    # -- machine senders ---------------------------------------------------
    "postmaster", "webmaster", "abuse", "bounce", "bounces", "notification",
    "notifications", "notify", "alerts", "automated", "mailer", "mailbox",
    "system", "robot", "noreplies", "reply", "replies",
    # -- generic catchalls -------------------------------------------------
    "general", "main", "office365", "staff", "everyone", "all", "group",
    "distribution", "list", "listserv",
    # -- rooms, calendars and scheduling bots ------------------------------
    # IDENT1 fix round 1, review F-4, and CITED rather than quietly re-sized.
    # The set was built for the CONTACT1 mail rail, where a room mailbox
    # rarely appears. IDENT1's invite rail reads whole ATTENDEE LISTS, and a
    # corporate invite routinely carries the room and the scheduling bot as
    # participants — with two-token display names ("Northstar Calendar",
    # "Acme Scheduling Bot"), which is precisely the shape that passes the
    # rest of the bar. Measured by the reviewer: `calendar@`, `meetings@`,
    # `bot@` and `webinar@` at a company domain were ELIGIBLE, i.e. filed as
    # people. The whole point of this fence is that a shared inbox stored as a
    # person's address poisons Tier-1 email resolution permanently.
    "calendar", "calendars", "meeting", "meetings", "invite", "invites",
    "scheduler", "schedules", "scheduling", "room", "rooms", "conf",
    "conference", "boardroom", "bot", "bots", "webinar", "webinars",
})

# Second-eyes F4 (2026-07-19, live-proven): a canonical name is letters plus
# name punctuation (space - ' . ’). Anything else — parentheses, digits,
# slashes, '?', quotes, commas — marks a capture-side annotation or guess
# (the live queue carried "<name> (or <other> alt account)"), and a guess
# string must never become a record name on the AUTO rail. Conservative by
# design: a name this regex flags still renders as a confirm row for a
# human decision (non-Latin scripts therefore demote to confirm, never
# silently drop).
_NAME_ANNOTATION_RE = re.compile(r"[^A-Za-zÀ-ÖØ-öø-ÿ\s\-'.’]")

# cr1#754: what an id or company field holds when it names nothing, compared
# with the edge punctuation stripped (`_is_placeholder`).
_PLACEHOLDERS = frozenset({
    "", "0", "freelance", "n.a", "n/a", "na", "nil", "none", "not specified",
    "null", "self", "tba", "tbd", "unknown", "unknown company", "user"})
# The endings that do not tell one company from another.
_COMPANY_ENDINGS = frozenset({"co", "corp", "llc", "ltd"})


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


def _events_path(ws) -> Path:
    return Path(ws) / "_hq" / "data" / "events.jsonl"


# ---------------------------------------------------------------------------
# cr1#665 (lane 3 of cr1#609) - THE DAILY PEOPLE UPDATE
# ---------------------------------------------------------------------------
# The Sunday `identity-reconcile` job stays the weekly DEEP pass, unchanged.
# The daily `people-update` job runs this same module with a window: only the
# identity clusters a mention captured inside the window touches are judged,
# through the same tier table, the same writers and the same caps, so a person
# first seen in yesterday's meeting is added (where corroborated) or asked
# about (the rest) the next morning instead of the next Sunday. A cluster is
# judged WHOLE once any of its rows is in the window: corroboration from an
# older mention still counts, which is the existing rule, not a new one.
#
# What the daily pass leaves to Sunday, because each is a whole-book scan or
# a clock that runs on weeks, not days: the fourteen-day aged defaults, the
# FS-17 expiry, the nickname-collision rule, the rename and duplicate scans
# over existing records, and the annotation-resolution join.
#
# Its receipt is a `pack_run` under its OWN task id, never an
# `identity_reconcile_run`: that type implies the Sunday task by type alone
# (`receipts._TYPE_IMPLIES_TASK`), so a daily receipt of it would mark the
# Sunday job served and the deep pass would never run.
DAILY_TASK_ID = "people-update"
DAILY_RECEIPT_TYPE = "pack_run"


def daily_window(workspace_root, *, since_iso: Optional[str] = None,
                 now_iso: Optional[str] = None) -> dict:
    """The window the daily people update reads on this run: the one
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


def next_surface_day(slots: dict, today: str, *, cap: Optional[int] = None) -> str:
    """The first day from `today` with room for one more connector question
    (cr1#773). `slots` is {YYYY-MM-DD: names stamped for it}. The stamp is the
    whole schedule: a later day needs no second read and no second write."""
    if cap is None:
        from brain_proposals import DAILY_CONNECTOR_QUESTION_CAP as cap
    day = _dt.date.fromisoformat(today)
    while len(slots.get(day.isoformat()) or ()) >= max(1, int(cap)):
        day += _dt.timedelta(days=1)
    return day.isoformat()


# cr1#773: rows one write section stamps and appends. The section reads the
# ledger once and appends once, so it stays far inside the writer lease whatever
# the read's size; a longer list takes more sections, and other writers get in
# between them: the lock is not a queue, so a pause is what lets them.
CONNECTOR_SECTION_ROWS = 100
CONNECTOR_SECTION_GAP_S = 0.5


def connector_clock(workspace_root) -> tuple:
    """`(now_iso, today)` for a connector job: the instant, and its
    workspace-local date, which is what a stamp and a day's count are in."""
    now_iso = _clock_now(workspace_root).strftime("%Y-%m-%dT%H:%M:%SZ")
    return now_iso, _localize_date(now_iso, str(workspace_root))


def connector_slots(events) -> dict:
    """{YYYY-MM-DD: questions stamped for that day}, people and projects in
    one count (cr1#773). Read this and write the stamp inside one
    `events_write_section`, or two jobs at once both fill the same day. Keep
    that section to this read, the stamps and one append: anything slower,
    per row, outlives the lease and every later row is refused."""
    from confirm_flow import (PERSON_NAME_KEYS, PROPOSAL_TYPES, _first_str,
                              _surface_day)

    slots: dict = {}
    for ev in events:
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        day = _surface_day(data.get("surface_on"))
        if day and ev.get("type") in PROPOSAL_TYPES:
            slots.setdefault(day, set()).add(
                _norm_name(_first_str(data, PERSON_NAME_KEYS)))
        elif day and ev.get("type") == "brain_proposal":
            slots.setdefault(day, set()).add(str(data.get("fingerprint")))
    return slots


def _names_up(workspace_root, events, now_iso: str) -> set:
    """The names a render shows the owner now. Open is not the same thing: a
    row past FS-17, or under a snooze, is on the ledger and renders nothing."""
    from confirm_flow import load_open_person_proposals
    from mute_ledger import active_dismissal_target_ids

    rows = load_open_person_proposals(
        _events_path(workspace_root), surfacing_at=now_iso,
        dismissed_target_ids=active_dismissal_target_ids(events, now_iso))
    return {_norm_name(r.get("name"))
            for c in person_queue_view(rows, now_iso=now_iso)["clusters"]
            for r in c["rows"]}


def people_waiting(workspace_root, now_iso: str) -> int:
    """How many names on the ledger have not come up yet: stamped for a later
    day, no row of the name up, and not on file since (a name on file never
    comes up)."""
    from confirm_flow import (load_open_person_proposals, may_surface,
                              person_name_on_file)
    from events_io import iter_events_org_scoped

    path = _events_path(workspace_root)
    today = _localize_date(now_iso, str(workspace_root))
    held = {r["name"] for r in load_open_person_proposals(path)
            if r.get("name") and not may_surface(r.get("surface_on"), today)}
    if not held:
        return 0
    up = _names_up(workspace_root, list(iter_events_org_scoped(workspace_root)),
                   now_iso)
    return len({_norm_name(n) for n in held if _norm_name(n) not in up
                and not person_name_on_file(workspace_root, n)})


def propose_seen_people(workspace_root, seen, *, source_skill: str) -> dict:
    """Put the people a connector job saw on the identity queue (cr1#738).

    `seen`: dicts of `{name, source_ref, ref, kind}`, one per name per item,
    already past the connector's `update_people` dial and its lists. Each new
    name becomes ONE `person_proposal` through the builder meeting capture
    uses, with the connector item as its source pointer. The daily people
    update then judges it by the rules it already has: a proposal is the
    input that pass reads, so nothing about it changes here.

    A person on file is left alone, and a name the queue has held before,
    open or answered, is never asked again: a board re-read on every fire
    must not re-open what the owner already said. The one row still written
    for such a name is a new sighting beside rows that are all open and
    stamped (cr1#754): another kind, or an id or company that kind has not
    shown, because two of those are two people and the cluster has to see
    them. It joins the same person row, so nothing new is asked. The evidence
    is a fixed phrase, never a board or document name.

    No more than `DAILY_CONNECTOR_QUESTION_CAP` new names come up on one day
    (cr1#773). Every row is still written, each stamped `surface_on`, the
    workspace-local day it may come up: today while today has room, else the
    next day that has. A name counts once, a day's count stays full after its
    questions are answered, and the count is shared with the projects the
    same read brings (`lifecycle_pass.note_seen_projects`). A new sighting
    takes no room when the name is up now, and needs no stamp, or when it
    waits, and keeps that day. A name open on the ledger that renders nothing
    is neither: this sighting brings it back, so it takes a day like a new
    name. An org rail row is not a person question and takes none.

    `kind` is stamped as the row's source. `source_user_id` and
    `source_company`, when a row carries them, are the connector's own id for
    this person and the company it states for them. Only the tasks connector
    has an id to give and none has a company field yet; a row without either
    stays name-only.

    Returns the receipt fields, `n_people_waiting` among them: every name on
    the ledger still held for a later day, not only this read's. A refused
    row is counted, never raised: one name never loses the job its receipt."""
    ws = Path(workspace_root)
    now_iso, today = connector_clock(ws)
    out: dict = {"people_proposed": [], "n_people_known": 0,
                 "n_people_asked_before": 0, "people_errors": []}
    rows = [r for r in seen or [] if isinstance(r, dict)
            and str(r.get("name") or "").strip()]
    if rows:
        plan, known, repeats, days, up, read_to = _plan_seen_people(
            ws, rows, out, source_skill, now_iso)
        ours: set = set()
        for at in range(0, len(plan), CONNECTOR_SECTION_ROWS):
            time.sleep(CONNECTOR_SECTION_GAP_S if at else 0)
            _append_seen_people(ws, plan[at:at + CONNECTOR_SECTION_ROWS], out,
                                source_skill, today, days, up, repeats, read_to,
                                ours)
        out["n_people_known"] = len(known)
        out["n_people_asked_before"] = len(repeats)
    out["n_people_waiting"] = people_waiting(ws, now_iso)
    return out


def _append_seen_people(ws, plan, out, source_skill, today, days, up, repeats,
                        read_to, ours) -> None:
    """One short section: what a day holds is read here, so the stamps and
    the one append land here too. A row another job wrote for a name since
    `read_to`, the plan's own read, makes this row a repeat; `ours` is the
    seqs this job's earlier sections wrote, which are not another job's.
    `days` and `up` change only once the append has landed, so a section that
    fails leaves no later row believing its name is up. It is counted against
    its names, and the next read brings them back."""
    from atomic_write import events_write_section
    from confirm_flow import (PERSON_NAME_KEYS, PROPOSAL_TYPES, _first_str,
                              may_surface)
    from event_gate import append_event
    from events_io import iter_events_org_scoped

    try:
        with events_write_section(_events_path(ws), holder=source_skill):
            events = list(iter_events_org_scoped(ws))
            slots = connector_slots(events)
            since = {_norm_name(_first_str(e.get("data") or {},
                                           PERSON_NAME_KEYS))
                     for e in events if e.get("type") in PROPOSAL_TYPES
                     and isinstance(e.get("seq"), int) and e["seq"] > read_to
                     and e["seq"] not in ours}
            batch = []
            new_days: dict = {}
            new_up: set = set()
            for item in plan:
                key, ev = item["key"], item["ev"]
                if key in since:
                    repeats.add(key)
                    continue
                batch.append(ev)
                if ev.get("type") == "org_proposal":
                    continue
                waiting = [d for d in list(days.get(key, ()))
                           + new_days.get(key, [])
                           if d and not may_surface(d, today)]
                if key in up or key in new_up:
                    surface_on = None
                elif waiting:
                    surface_on = min(waiting)
                else:
                    surface_on = next_surface_day(slots, today)
                new_days.setdefault(key, []).append(surface_on)
                if surface_on:
                    ev["data"]["surface_on"] = surface_on
                    slots.setdefault(surface_on, set()).add(key)
                    if may_surface(surface_on, today):
                        new_up.add(key)
            written = append_event(_events_path(ws), batch,
                                   holder=source_skill) if batch else []
    except Exception as exc:  # noqa: BLE001 - contained per job, loud per name
        out["people_errors"] += [{"name": item["name"],
                                  "error": f"{type(exc).__name__}: {exc}"}
                                 for item in plan]
        return
    landed = {_norm_name((w.get("data") or {}).get("name")) for w in written}
    ours.update(w.get("seq") for w in written)
    for key in landed & set(new_days):
        days.setdefault(key, []).extend(new_days[key])
    up |= landed & new_up
    for item in plan:
        if item["key"] in landed and item["name"] not in out["people_proposed"]:
            out["people_proposed"].append(item["name"])


def _plan_seen_people(ws, rows, out, source_skill, now_iso) -> tuple:
    """Which rows are new, off ONE read of the ledger and outside any lock:
    the name, dedup and gate checks are the slow part. Returns `(plan, known,
    repeats, days, up, read_to)`. A plan row is built and gated, not stamped."""
    from confirm_flow import (PERSON_NAME_KEYS, PROPOSAL_TYPES, _first_str,
                              load_open_person_proposals, person_name_on_file)
    from event_gate import gate_events
    from events_io import iter_events_org_scoped
    from meeting_capture import build_person_proposal_event

    events = list(iter_events_org_scoped(ws))
    read_to = max((e["seq"] for e in events if isinstance(e.get("seq"), int)),
                  default=0)
    up = _names_up(ws, events, now_iso)
    asked: dict = {}
    ever: dict = {}
    for ev in events:
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev.get("type") in PROPOSAL_TYPES:
            name_seen = _first_str(data, PERSON_NAME_KEYS)
            key = _norm_name(name_seen)
            asked[key] = asked.get(key, 0) + 1
            ever.setdefault(key, set()).add(_sighting(
                dict(data, name=name_seen), data.get("source_kind"))[:2])
        elif ev.get("type") == "org_proposal":
            key = _norm_name(data.get("name"))
            asked[key] = asked.get(key, 0) + 1
    sightings: dict = {}
    days: dict = {}
    for open_row in load_open_person_proposals(_events_path(ws)):
        key = _norm_name(open_row.get("name"))
        sightings.setdefault(key, []).append(
            _sighting(open_row, open_row.get("source_kind")))
        days.setdefault(key, []).append(open_row.get("surface_on"))

    def asked_before(key, sighting) -> bool:
        kind, tool_id, company = sighting
        held = sightings.get(key) or []
        if key not in asked:
            return False
        if not kind or len(held) != asked[key] or \
                any(s[0] is None for s in held):
            return True  # answered, an old row, or no kind to tell it apart
        same_kind = [s for s in held if s[0] == kind]
        return bool(same_kind) \
            and (not tool_id or tool_id in {s[1] for s in same_kind}) \
            and (not company or company in {s[2] for s in same_kind})

    done: set = set()
    known: set = set()
    repeats: set = set()
    to_org_rail: set = set()
    plan: list = []
    for row in rows:
        name = str(row["name"]).strip()
        key = _norm_name(name)
        sighting = _sighting(dict(row, name=name), row.get("kind"))
        if (key, sighting) in done:
            continue
        done.add((key, sighting))
        if person_name_on_file(ws, name):
            # cr1#825: the record keeps what it was added on, so a sighting
            # that says another person is the one row a name on file gets,
            # once. The same id, or nothing to compare, is the person on file.
            if sighting_on_file(ws, dict(row, name=name))[1] != "different" \
                    or sighting[:2] in ever.get(key, ()):
                known.add(key)
                continue
        elif asked_before(key, sighting):
            repeats.add(key)
            continue
        try:
            ev = build_person_proposal_event(
                name, source_ref=row.get("source_ref"),
                source_skill=source_skill,
                evidence=(row.get("evidence")
                          or f"named on an item in your {row.get('kind')}"),
                workspace_root=ws, source_kind=row.get("kind"),
                inferred_role=row.get("inferred_role"),
                inferred_org=row.get("inferred_org"),
                source_user_id=row.get("source_user_id"),
                source_company=row.get("source_company"))
            if ev.get("type") == "org_proposal":
                if key in to_org_rail:
                    continue  # one question per name, whatever saw it
                to_org_rail.add(key)
            if "ref" in row:  # a door sighting is no connector row
                ev["data"]["connector_source_ref"] = dict(row.get("ref") or {})
            # A row the gate refuses is this name's, never the batch's.
            gate_events([ev], strict_enum=True, holder=source_skill)
        except Exception as exc:  # noqa: BLE001 - loud per item, contained per job
            out["people_errors"].append({"name": name,
                                         "error": f"{type(exc).__name__}: {exc}"})
            continue
        asked[key] = asked.get(key, 0) + 1
        sightings.setdefault(key, []).append(sighting)
        plan.append({"name": name, "key": key, "ev": ev})
    return plan, known, repeats, days, up, read_to


def _window_start(window) -> Optional[_dt.datetime]:
    """The aware instant a window opens at. `since_aware` carries the
    machine's offset; a bare `since` is machine-local naive, so it is
    localized rather than read as UTC. None when neither parses."""
    if not isinstance(window, dict):
        return None
    from event_time import parse_ts

    start = parse_ts(window.get("since_aware") or "")
    if start is not None:
        return start
    raw = window.get("since")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        naive = _dt.datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    return naive.astimezone() if naive.tzinfo is None else naive


def _daily_spill_marker(window, spilled: bool) -> Optional[str]:
    """The batch-cap marker for a daily run that hit a cap, through the
    one writer of it (`catchup.receipt_window_marker`): the window's own
    start, so the next run re-covers the span. None when nothing spilled."""
    if not spilled or not isinstance(window, dict):
        return None
    from catchup import receipt_window_marker

    return receipt_window_marker(
        {"start": window.get("since"), "end": window.get("until")},
        incomplete=True)


def _captured_since(row: dict, start: Optional[_dt.datetime]) -> bool:
    """True when the proposal row was captured at or after `start`. An
    unplaceable row, or no start at all, is NOT in the window: the Sunday
    pass still sees it, so the daily pass never guesses."""
    if start is None:
        return False
    from confirm_flow import question_clock
    from event_time import parse_ts

    # cr1#773: a held row's clock is its surface day, so it is in every window
    # that opens before that day: each daily pass re-judges it while it waits,
    # which writes nothing unless its evidence settles it. The windows tile
    # (each opens at the last receipt), so the first pass on or after its day
    # is one of them, and that pass asks the link it held back. A spill keeps
    # the window open; failing all that, Sunday's pass has no window.
    when = parse_ts(question_clock(row) or "")
    return when is not None and when >= start


def _restrict_view_to_window(view: dict, start) -> dict:
    """The clustered queue cut to what the window touched: a cluster with
    at least one row captured in the window (judged whole), and the
    nameless and standalone update rows captured in it."""
    return {
        "clusters": [c for c in view["clusters"]
                     if any(_captured_since(r, start) for r in c["rows"])],
        "updates": [r for r in view["updates"] if _captured_since(r, start)],
        "nameless": [r for r in view["nameless"]
                     if _captured_since(r, start)],
    }


_ROLE_TOKEN_SPLIT_RE = re.compile(r"[^a-z]+")


def is_role_address(email: str | None) -> bool:
    """True when the address's local part is role-shaped (shared inbox).

    TOKEN-BASED since CONTACT1 second-eyes (2026-08-03), not exact membership.
    Real shared inboxes are routinely decorated — `orders-us@`, `info.uk@`,
    `support2@`, `ap_west@` — and an exact-set test read every one of them as a
    person. The local part is split on everything that is not a letter, and any
    token landing in `ROLE_ADDRESS_LOCAL_PARTS` marks the address. The
    undecorated local part is still checked whole first, so a hyphenated member
    of the set (`no-reply`) keeps matching as itself.

    Sub-addressing is included on purpose: `sales+eu@` is the sales inbox.

    The asymmetry is deliberate and safe. Every caller uses this to WITHHOLD,
    so a false positive costs a question the human answers, while a false
    negative costs a silent wrong write — a shared inbox stored as a person's
    address poisons Tier-1 email resolution permanently."""
    if not email or "@" not in email:
        return False
    local = email.split("@", 1)[0].strip().lower()
    if not local:
        return False
    if local in ROLE_ADDRESS_LOCAL_PARTS:
        return True
    return any(tok in ROLE_ADDRESS_LOCAL_PARTS
               for tok in _ROLE_TOKEN_SPLIT_RE.split(local) if tok)


def _norm_name(s: str | None) -> str:
    # Same semantics as people_writer._normalize_name (whitespace-collapsed
    # lowercase) — inlined to keep this module import-light; the writer-side
    # gates still run people_writer's own normalization at write time.
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _source_family(row: dict) -> Optional[str]:
    """Which independent source FAMILY a proposal row came from (D2:
    independence is by family, not by row count): the kind stamped when the
    row was written (cr1#754). No stamp → None, and neither its pointer nor
    its evidence is read to guess one, so an old row never counts toward
    corroboration."""
    from confirm_flow import PERSON_SOURCE_KINDS

    kind = row.get("source_kind")
    return kind if kind in PERSON_SOURCE_KINDS else None


def _independent_families(rows: list) -> list:
    """The source families these rows show, sorted. One pointer is one source
    whatever kind a row states, and a row with no pointer is none (cr1#825),
    so two sightings count as two only when their refs differ."""
    from connector_adapters.provenance import dedup_key_of

    families: set = set()
    seen: set = set()
    for row in sorted(rows, key=lambda r: r.get("captured_ts") or ""):
        ref = dedup_key_of(row.get("source_ref"))
        family = _source_family(row)
        if family and ref and ref not in seen:
            families.add(family)
        seen.add(ref)
    return sorted(families)


# ---------------------------------------------------------------------------
# Clustering (D1/D3) — a READ-side projection, like every other projector
# ---------------------------------------------------------------------------

def cluster_open_proposals(rows: list[dict]) -> dict:
    """Group open person-proposal rows into identity clusters.

    Input: rows from `confirm_flow.load_open_person_proposals` (any filter
    posture — the caller owns suppression/TTL choices). Returns:

      {"clusters":  [...],   # named add-clusters (one person = one cluster)
       "updates":   [...],   # standalone update-type rows (person_id-bearing
                             #   or no matching cluster) — existence is their
                             #   premise, they render separately
       "nameless":  [...]}   # nameless add-type rows (annotation tier — D5;
                             #   NEVER rendered as person rows)

    Add-type rows group by normalized name (exact — a bare "Quinn" is never
    absorbed into "Quinn Alvarez"; the fuller-name cluster stays distinct,
    Bug #19). Update-type rows with no person_id whose name matches an
    add-cluster fold into that cluster's evidence (the person is still
    proposed). Cluster fields:
      key, name (longest spelling), rows (newest-first), add_rows,
      update_rows, seqs (int seqs), fingerprints (seq-less rows' D8
      fingerprints), row_id (stable wire id), inferred_role, inferred_org,
      source_families.
    """
    clusters: dict[str, dict] = {}
    updates: list[dict] = []
    nameless: list[dict] = []
    pending_updates: list[dict] = []

    for row in rows:
        name = (row.get("name") or "").strip()
        if row.get("type") == "person_update_proposal":
            if row.get("person_id") or not name:
                updates.append(row)
            else:
                pending_updates.append(row)
            continue
        if not name:
            nameless.append(row)
            continue
        key = _norm_name(name)
        c = clusters.setdefault(key, {
            "key": key, "name": name, "rows": [], "add_rows": [],
            "update_rows": [], "seqs": [], "fingerprints": [],
            "inferred_role": None, "inferred_org": None,
        })
        c["rows"].append(row)
        c["add_rows"].append(row)
        c["name"] = _better_name(c["name"], name)
        _absorb_row(c, row)

    for row in pending_updates:
        key = _norm_name(row.get("name"))
        c = clusters.get(key)
        if c is None:
            updates.append(row)  # no proposed person to attach to
            continue
        c["rows"].append(row)
        c["update_rows"].append(row)
        _absorb_row(c, row)

    out = []
    for c in clusters.values():
        c["rows"].sort(key=lambda r: r.get("captured_ts") or "", reverse=True)
        c["source_families"] = _independent_families(c["add_rows"])
        c["row_id"] = _cluster_row_id(c)
        out.append(c)
    # Oldest cluster first (matches the queue's age ordering).
    out.sort(key=lambda c: min((r.get("captured_ts") or "") for r in c["rows"]))
    return {"clusters": out, "updates": updates, "nameless": nameless}


def _better_name(current: str, candidate: str) -> str:
    """The cluster's display title: more tokens wins; among equals, the
    spelling with more capitalization (a lowercase dictation transcript of
    the same name never displaces the properly-cased one)."""
    if len(_norm_name(candidate).split()) > len(_norm_name(current).split()):
        return candidate
    if _norm_name(candidate) == _norm_name(current):
        cap = sum(1 for ch in candidate if ch.isupper())
        cur = sum(1 for ch in current if ch.isupper())
        if cap > cur:
            return candidate
    return current


def _absorb_row(cluster: dict, row: dict) -> None:
    seq = row.get("seq")
    if isinstance(seq, int) and not isinstance(seq, bool):
        cluster["seqs"].append(seq)
    elif row.get("fingerprint"):
        cluster["fingerprints"].append(row["fingerprint"])
    for field in ("inferred_role", "inferred_org"):
        if not cluster[field] and row.get(field):
            cluster[field] = row[field]


def _cluster_row_id(cluster: dict) -> str:
    """Stable wire id: the oldest add row's seq (`person:<seq>` — the shape
    live widgets already carry), falling back to the D8 fingerprint for an
    all-seq-less cluster."""
    for row in sorted(cluster["add_rows"],
                      key=lambda r: r.get("captured_ts") or ""):
        seq = row.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            return f"person:{seq}"
    if cluster["fingerprints"]:
        return f"person:fp:{cluster['fingerprints'][0]}"
    return f"person:{cluster['key']}"


def person_queue_view(rows: list[dict], *, now_iso: Optional[str] = None) -> dict:
    """The RENDERED person-queue projection (D3) — what the Staff Meeting
    adapter emits and what the morning brief's pointer counts (step 10:
    pointer honesty — the two can never disagree because both call this).

    Applies the FS-17 low-context age-out per row, then clusters. Nameless
    add rows are in the view but NEVER counted/rendered (annotation tier)."""
    from event_time import parse_ts
    from confirm_flow import person_row_aged_out

    now = parse_ts(now_iso or _now_iso())
    # An aged name-only mention never surfaces (FS-17).
    return cluster_open_proposals(
        [row for row in rows if not person_row_aged_out(row, now)])


def count_person_rows(rows: list[dict], *, now_iso: Optional[str] = None) -> int:
    """How many person rows actually RENDER: named clusters + standalone
    update rows. THE count the morning-brief pointer uses (one person = one
    row — never the raw proposal-event count)."""
    view = person_queue_view(rows, now_iso=now_iso)
    return len(view["clusters"]) + len(view["updates"])


# ---------------------------------------------------------------------------
# Classification (D2 — the rule table)
# ---------------------------------------------------------------------------

def _cluster_email(cluster: dict, reader):
    """(email, source_row): the first address `reader` finds across the
    cluster's add rows, newest first."""
    for row in sorted(cluster["add_rows"],
                      key=lambda r: r.get("captured_ts") or "", reverse=True):
        probe = dict(row)
        probe["name"] = cluster["name"]
        email = reader(probe)
        if email and is_role_address(email):
            # Second-eyes F1 (2026-07-19): §0-2's own doctrine applied to
            # CAPTURE — a role-shaped local part (shared inbox) never
            # attributes to a person. It neither corroborates an auto-add
            # nor lands on a record (a stored info@ would poison Tier-1
            # email resolution forever). Keep scanning older rows.
            continue
        if email:
            return email, row
    return None, None


def cluster_observed_email(cluster: dict):
    """(email, source_row): the first address the cluster's own text TIES to
    this person, under `person_backlog_sweep._attributed_email` (ONE
    implementation, imported and never copied). cr1#769: this answer may land
    on a record and license an add, so an address that merely appears in the
    text is not enough."""
    from person_backlog_sweep import _attributed_email

    return _cluster_email(cluster, _attributed_email)


def cluster_mentioned_email(cluster: dict):
    """(email, source_row): the first address the cluster's text mentions, on
    `person_backlog_sweep._observed_email`. For routing to the link lane only:
    nothing reads it to write an address or to add a person."""
    from person_backlog_sweep import _observed_email

    return _cluster_email(cluster, _observed_email)


def _is_placeholder(value) -> bool:
    return _norm_name(str(value or "")).strip(" .()-_?") in _PLACEHOLDERS


def _tool_user_id(row: dict) -> Optional[str]:
    """The source tool's own id on this row, or None. A connector can stage a
    person where the id goes, so a placeholder, text with a space or an @, or
    a spelling of the name itself is not taken as an id."""
    raw = str(row.get("source_user_id") or "").strip()
    if _is_placeholder(raw) or re.search(r"[\s@]", raw):
        return None
    words = re.findall(r"[^\W_]+", _norm_name(row.get("name")))
    folded = "".join(re.findall(r"[^\W_]+", raw.lower()))
    if folded in words or sorted(folded) == sorted("".join(words)):
        return None
    return raw


def _company_key(value) -> str:
    """A company name with case, punctuation and its legal ending folded
    away, so "Acme Co." and "ACME, LLC" compare as one company."""
    words = re.findall(r"[^\W_]+", _norm_name(str(value or "")))
    while words and words[-1] in _COMPANY_ENDINGS:
        words.pop()
    return " ".join(words)


def _source_company(row: dict) -> Optional[str]:
    """The company the source states on this row, folded, or None. A
    placeholder names none, and the person's own name is no company."""
    folded = _company_key(row.get("source_company"))
    name = _norm_name(row.get("name"))
    if _is_placeholder(row.get("source_company")) or not folded or \
            folded == name or folded in name.split():
        return None
    return folded


def _is_owners_company(company: str, owners: set) -> bool:
    """Does this folded company lead with an owner company's words, or the
    other way round? Any other ending on the owner's name is still the owner,
    and if it is not, the row only asks."""
    return any(company == own or company.startswith(own + " ")
               or own.startswith(company + " ") for own in owners)


def _sighting(row: dict, kind) -> tuple:
    """What one row says about a person: (kind, id, company)."""
    return (kind, _tool_user_id(row), _source_company(row))


def _owner_companies(workspace_root) -> Optional[set]:
    """The owner's own company names, folded: self orgs and the org on the
    owner's record. None when the book cannot say, because the owner does not
    resolve to a record or the book is unusable: no company counts then, so
    the row asks."""
    if workspace_root is None:
        return None
    try:
        from entities_io import entities_collection
        from primary_user import resolve_primary_user

        data = json.loads((Path(workspace_root) / "_hq" / "data" /
                           "entities.json").read_text(encoding="utf-8"))
        owner = resolve_primary_user(workspace_root)
        records = [p for p in entities_collection(data, "people")
                   if isinstance(p, dict) and p.get("id") == owner]
        if not owner or not records:
            return None
        own_org_ids = {p.get("primary_org_id") or p.get("org_id")
                       for p in records}
        names: set = set()
        for org in entities_collection(data, "orgs"):
            if isinstance(org, dict) and (
                    org.get("id") in own_org_ids or str(
                        org.get("relationship_type") or "").strip().lower()
                    == "self"):
                names.update(_company_key(n) for n in
                             [org.get("canonical_name")]
                             + list(org.get("aliases") or []))
        return names - {""}
    except Exception:
        return None


def _cluster_rows(cluster: dict) -> list:
    name = cluster.get("name") or ""
    return [dict(r, name=name) for r in cluster.get("add_rows") or []]


def _tool_ids_by_kind(rows: list) -> dict:
    ids: dict = {}
    for row in rows:
        tool_id = _tool_user_id(row)
        if tool_id and _source_family(row):
            ids.setdefault(_source_family(row), set()).add(tool_id)
    return ids


def cluster_disagreement(cluster: dict) -> Optional[str]:
    """What says this cluster may be two people, or None: two addresses its
    text ties to the name, two ids from one kind of tool, or two companies
    on any row. It comes before anything that could add them (cr1#754)."""
    from person_backlog_sweep import _attributed_email

    rows = _cluster_rows(cluster)
    tied = {a.strip().lower() for a in map(_attributed_email, rows) if a}
    if len(tied) > 1:
        return "two addresses"
    if any(len(found) > 1 for found in _tool_ids_by_kind(rows).values()):
        return "two source tool user ids"
    if len({c for c in map(_source_company, rows) if c}) > 1:
        return "two companies"
    return None


def cluster_identifier(cluster: dict, workspace_root=None) -> Optional[str]:
    """What sets this person apart from another of the same name, or None
    (cr1#754, owner ruling 2026-11-04): an address the text ties to them, the
    source tool's own user id, or a company name the source itself supplied
    (`source_company`). A job title does not count, nor a word in the
    evidence, nor `inferred_org`: that is a model's inference, and an
    inference does not license an add (maintainer's ruling, 2026-11-05).

    Only a row with a source can carry the id or the company: an old row is
    not one of the two sightings. A cluster that disagrees with itself
    identifies nobody (`cluster_disagreement`). The owner's own company
    separates nobody on the owner's own board."""
    if cluster_disagreement(cluster):
        return None
    if cluster_observed_email(cluster)[0]:
        return "address"
    rows = _cluster_rows(cluster)
    if _tool_ids_by_kind(rows):
        return "source tool user id"
    companies = {c for c in map(_source_company, rows) if c}
    if any(_source_company(r) and _source_family(r) for r in rows):
        owners = _owner_companies(workspace_root)
        if owners is not None and not any(
                _is_owners_company(c, owners) for c in companies):
            return "company name"
    return None


def cluster_source_identifiers(cluster: dict, workspace_root=None) -> list:
    """What an add keeps on the record (`source_identifiers`, cr1#825): the
    source's ids and the companies it supplied, under the rules that let them
    count in `cluster_identifier`. A company is kept with the spelling the
    source gave; the owner's own company identifies nobody, so it stays off."""
    rows = _cluster_rows(cluster)
    kept = [{"kind": kind, "tool_id": tool_id}
            for kind, ids in sorted(_tool_ids_by_kind(rows).items())
            for tool_id in sorted(ids)]
    if any(_source_company(r) and _source_family(r) for r in rows):
        owners = _owner_companies(workspace_root)
        seen: set = set()
        for row in rows if owners is not None else []:
            company, kind = _source_company(row), _source_family(row)
            if company and kind and company not in seen \
                    and not _is_owners_company(company, owners):
                seen.add(company)
                kept.append({"kind": kind,
                             "company": str(row["source_company"]).strip()})
    return kept


def sighting_against_record(record: dict, sighting: tuple) -> Optional[str]:
    """Is this sighting (`_sighting`) the person on `record`? "same",
    "different", or None when the two have nothing to compare (cr1#825). An
    id speaks only against an id of the same kind, as in
    `cluster_disagreement`. A company stays on the record as an identifier and
    decides nothing here: spellings differ and people move, so a different
    company alone asks nothing."""
    kind, tool_id, _company = sighting
    ids = {k.get("tool_id") for k in (record or {}).get("source_identifiers") or []
           if isinstance(k, dict) and k.get("kind") == kind and k.get("tool_id")}
    if not kind or not tool_id or not ids:
        return None
    return "same" if tool_id in ids else "different"


def sighting_on_file(workspace_root, row: dict) -> tuple:
    """(record, verdict) for a sighting whose name resolves to one live record
    on file. (None, None) when it carries no id, no one record holds the name,
    or the record is archived: an archived record is nobody's answer. The
    row's own address on the record, or the owner's yes to this id, makes it
    "same" whatever the id says."""
    from people_writer import MultipleCandidatesError, find_existing_person

    sighting = _sighting(row, row.get("kind"))
    if not sighting[1]:
        return None, None
    try:
        record = find_existing_person(workspace_root, name=row.get("name"))
    except MultipleCandidatesError:
        return None, None
    if not record or record.get("status") == "archived":
        return None, None
    said = sighting_against_record(record, sighting)
    if said == "different" and (
            _row_holds_records_address(row, record)
            or _owner_said_same(workspace_root, row, sighting, record)):
        said = "same"
    return record, said


def _row_holds_records_address(row: dict, record) -> bool:
    """Does this one row tie an address that is on the record? That beats a
    different id (cr1#825). Only the row's own attributed address counts: one
    merely mentioned in its text, or tied on another row, settles nothing."""
    address = cluster_observed_email(
        {"name": row.get("name"), "add_rows": [row]})[0]
    return bool(address) and address.strip().lower() in {
        e.strip().lower() for e in _record_emails(record or {})}


def _owner_said_same(workspace_root, row: dict, sighting: tuple, record) -> bool:
    """Did a row of this name with this kind and id end in `same_as` on this
    record, read from the ledger? The tombstone is the owner's yes on every
    surface, so no surface has to write the id back. A reopen lifts it."""
    from confirm_flow import PERSON_NAME_KEYS, PROPOSAL_TYPES, _first_str
    from events_io import iter_events_org_scoped

    key = _norm_name(row.get("name"))
    mine: set = set()
    yes: set = set()
    for ev in iter_events_org_scoped(Path(workspace_root)):
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev.get("type") in PROPOSAL_TYPES:
            name = _first_str(data, PERSON_NAME_KEYS)
            if _norm_name(name) == key and _sighting(
                    dict(data, name=name),
                    data.get("source_kind"))[:2] == sighting[:2]:
                mine.add(str(ev.get("seq")))
        elif ev.get("type") == "person_proposal_resolved" \
                and data.get("resolution") == "same_as" \
                and data.get("person_id") == record.get("id"):
            yes.add(str(data.get("proposal_seq")))
        elif ev.get("type") == "person_proposal_reopened":
            yes.discard(str(data.get("proposal_seq")))
    return bool(mine & yes)


def _kinds_saying_another_person(cluster: dict, record) -> list:
    """The kinds of tool that list this name under another id than the
    record's. Each row is judged on its own, so an address on one row does not
    silence an id on another. Only a row with a source can say so."""
    return sorted({
        _source_family(r) for r in _cluster_rows(cluster)
        if sighting_against_record(
            record or {}, _sighting(r, _source_family(r))) == "different"
        and not _row_holds_records_address(r, record)})


def _cluster_says_another_person(cluster: dict, record) -> bool:
    return bool(_kinds_saying_another_person(cluster, record))


def judge_sighting(workspace_root, sighting: dict) -> tuple:
    """(cluster, verdict): the rule table `classify_cluster` applies on the
    runs, over the open rows of this name plus this sighting, which is not on
    the ledger yet (cr1#825). A row on hold is left out, so a held name is
    never added on what it holds: it needs what the new sighting brings."""
    from confirm_flow import load_open_person_proposals, person_source_kind

    ws = Path(workspace_root)
    try:
        import event_refs
        from mute_ledger import active_dismissal_target_ids

        held = active_dismissal_target_ids(
            event_refs.load_events(_events_path(ws)), _now_iso()) \
            if _events_path(ws).exists() else []
    except Exception:
        held = []
    key = _norm_name(sighting.get("name"))
    rows = [r for r in load_open_person_proposals(
        _events_path(ws), dismissed_target_ids=[str(t) for t in held])
        if _norm_name(r.get("name")) == key]
    rows.append(dict(
        sighting, type="person_proposal", seq=None, fingerprint=None,
        source_kind=person_source_kind(sighting.get("source_ref"),
                                       sighting.get("kind")),
        captured_ts=_now_iso()))
    cluster = next(c for c in cluster_open_proposals(rows)["clusters"]
                   if c["key"] == key)
    return cluster, classify_cluster(ws, cluster)


def ask_about_sighting(workspace_root, sighting: dict, *,
                       source_skill: str) -> dict:
    """Put one door sighting on the queue: the question cr1#754 raises, by the
    rules `propose_seen_people` holds (one person is one question, an answered
    name stays answered, an org goes to the org rail). It takes no surface
    day, because the daily cap is the connector rows' (cr1#773). A row that
    lands nothing is a repeat: `asked` with `already_asked` while a row of the
    name is open on the person or the org rail, else `already_answered`,
    because no question exists then. A row the gate refuses raises."""
    from brain_proposals import _open_org_proposal_names
    from confirm_flow import load_open_person_proposals
    from event_gate import append_event
    from events_io import iter_events_org_scoped

    ws = Path(workspace_root)
    out: dict = {"people_errors": []}
    plan = _plan_seen_people(ws, [sighting], out, source_skill, _now_iso())[0]
    if out["people_errors"]:
        raise ValueError(out["people_errors"][0]["error"])
    landed = append_event(_events_path(ws), [item["ev"] for item in plan],
                          holder=source_skill) if plan else []
    if landed:
        return {"status": "asked", "record": None}
    key = _norm_name(sighting.get("name"))
    if any(_norm_name(r.get("name")) == key
           for r in load_open_person_proposals(_events_path(ws))) \
            or key in _open_org_proposal_names(list(iter_events_org_scoped(ws))):
        return {"status": "asked", "already_asked": True, "record": None}
    return {"status": "already_answered", "record": None}


def close_sightings(workspace_root, cluster: dict, person_id: str, *,
                    source_skill: str) -> None:
    """A door add closes the rows it used: one `person_added` tombstone each,
    as the runs write. The sighting not yet on the ledger has none to close."""
    from confirm_flow import build_person_proposal_resolved_event
    from event_gate import append_event

    tombs = []
    for row in cluster["rows"]:
        seq = row.get("seq")
        kwargs = {}
        if not isinstance(seq, int) or isinstance(seq, bool):
            if not row.get("fingerprint"):
                continue
            seq, kwargs = None, {"proposal_fingerprint": row["fingerprint"]}
        tombs.append(build_person_proposal_resolved_event(
            seq, resolution="person_added", source_skill=source_skill,
            person_id=person_id, note="added through the door", **kwargs))
    if tombs:
        append_event(_events_path(workspace_root), tombs, holder=source_skill)


def _name_is_someone_elses(workspace_root, name: str, record, *,
                           if_unreadable: bool = True) -> bool:
    """Is `name` another record's own name, with `record` not called that?
    The read-side twin of `add_person_alias`'s refusal: one predicate, so the
    plan and the writer cannot disagree. A check that cannot run answers
    `if_unreadable`: True for routing, because the safe side of an identity
    is a question; False for a sentence, which must not claim what it did not
    read."""
    if not record:
        return False
    try:
        from people_writer import another_record_named

        return another_record_named(workspace_root, record.get("id"),
                                    name) is not None
    except Exception:
        return if_unreadable


def classify_cluster(workspace_root, cluster: dict) -> dict:
    """Tier one identity cluster: {"tier": "auto"|"confirm"|"merge_propose"|
    "annotation", "why": ..., "email": ..., "matched_person": ...}.

    A rule table mirroring the AUTO_ALLOWED doctrine — auditable, never a
    confidence score. The write-side gates (`auto_add_person`'s same-name
    gate, `find_existing_person`'s tiers) still run at apply time; this
    classification is the read-side plan."""
    from confirm_flow import person_name_on_file
    from people_writer import (MultipleCandidatesError, find_existing_person,
                               list_same_name_people)

    name = (cluster.get("name") or "").strip()
    if not name:
        return {"tier": "annotation", "why": "no name captured — an unnamed "
                "attendee is an annotation, never a person row (D5)",
                "email": None, "matched_person": None}

    email, _src = cluster_observed_email(cluster)
    # cr1#769: two questions, two answers. `email` is the address the text
    # ties to this person: it alone may be written or license an add.
    # `pointing` is main's reading, any address the text mentions: it sends
    # the row to the link lane exactly as before, where nothing writes an
    # address. Every link branch reads `pointing`; the add tiers read `email`.
    pointing = cluster_mentioned_email(cluster)[0] or email

    if person_name_on_file(workspace_root, name):
        matched = None
        try:
            matched = find_existing_person(workspace_root, name=name,
                                           email=pointing)
        except MultipleCandidatesError:
            matched = None
        except Exception:
            matched = None
        if _name_is_someone_elses(workspace_root, name, matched):
            # cr1#768: the name is one record's and the address is another's.
            # The email tier wins the lookup, so the two signals DISAGREE:
            # the same human link row as F2 below, never the §0-2 silent link.
            return {"tier": "merge_propose", "email": pointing,
                    "matched_person": matched, "silent_link_ok": False,
                    "why": f"{name!r} is another record's own name and "
                           f"{pointing} belongs to "
                           f"{matched.get('canonical_name')!r}: a human "
                           "decides, never a silent link (D4a)"}
        if _cluster_says_another_person(cluster, matched):
            # cr1#825: the record keeps the id it was added on, and this
            # sighting names another: one name, maybe two people.
            return {"tier": "merge_propose", "email": pointing,
                    "matched_person": matched, "silent_link_ok": False,
                    "why": f"{name!r} is on file under another source id: a "
                           "human decides, never a silent link (cr1#825)"}
        return {"tier": "merge_propose", "email": pointing,
                "matched_person": matched, "silent_link_ok": True,
                "why": f"{name!r} confidently resolves to an existing record "
                       "— link/merge is a recorded resolution, not a render "
                       "filter (D4a)"}

    if pointing:
        # Second-eyes F2 (2026-07-19): an observed address that EXACTLY
        # matches an existing record means the evidence points at someone
        # already on file — a duplicate add (the auto tier) is never the
        # right write. The name did NOT confidently resolve (the branch
        # above), so the two signals DISAGREE: this is a human link ROW,
        # never the §0-2 silent link (F-3 sole-address attribution is
        # fallible — a quoted thread's sender address can misattribute).
        matched = None
        try:
            matched = find_existing_person(workspace_root, email=pointing)
        except MultipleCandidatesError:
            return {"tier": "confirm", "email": email,
                    "matched_person": None, "silent_link_ok": False,
                    "why": f"observed address {pointing} is ambiguous across "
                           "existing records — never auto on a disputed "
                           "match"}
        except Exception:
            matched = None
        if matched is not None:
            return {"tier": "merge_propose", "email": pointing,
                    "matched_person": matched, "silent_link_ok": False,
                    "why": f"observed address {pointing} already belongs to "
                           f"{(matched.get('canonical_name') or matched.get('id'))!r}"
                           " — link it, never a duplicate add (D4a)"}

    if _NAME_ANNOTATION_RE.search(name):
        # Second-eyes F4 (2026-07-19, live-proven): guess/annotation markers
        # in the captured name ("<name> (or <other> alt account)") — a
        # captured note is not a canonical name; a human decides.
        return {"tier": "confirm", "email": email, "matched_person": None,
                "why": "name carries annotation/guess markers — a captured "
                       "note is not a canonical name, never auto"}

    # IDENT1 rule 2 — a SPELLING VARIANT of exactly one record on file is a
    # link question, not an add question. It never reached the link lane
    # before, because every branch here keys on a name the record already
    # holds: `person_name_on_file` says no for a misspelling, the observed
    # address is usually absent on these captures, and the row then fell
    # through to the same-name-collision confirm below and sat there. Note
    # what this branch does NOT do: it routes the row, it does not link it.
    # `auto_link_eligible`'s (a′)-(d) gate still has to find a substrate
    # fact — the same org or a shared work domain — before anything is
    # written, and refuses on two candidates exactly as it always has.
    try:
        variants = variant_candidates(workspace_root, name)
    except Exception:
        variants = []
    if len(variants) == 1:
        return {"tier": "merge_propose", "email": pointing,
                "matched_person": variants[0], "silent_link_ok": False,
                "why": f"{name!r} is a spelling of "
                       f"{(variants[0].get('canonical_name') or variants[0].get('id'))!r} "
                       "— a link question, never a second record (IDENT1 "
                       "rule 2)"}

    # IDENT1 rule 2, second half — a name that is a RELATION of exactly one
    # record on file ("Bo" against "Bo Sample") is also a link question, and
    # was also never routed to the link lane. The two branches below sent it
    # to `confirm` first: "lone first name" for a one-token capture, and
    # "same-name collision" for a multi-token one. Both of those reasons are
    # about ADDING A SECOND RECORD, which is what Bug #19 forbids forever
    # and what this branch does not do. Linking a spelling to the single
    # on-file person who carries it, when the substrate corroborates it, is
    # what UXR1 D3 licensed and `_name_related` was written for; the row
    # simply never got there. `auto_link_eligible` still has to say yes, and
    # it still refuses on two candidates, a conflicting address, a
    # contradicting org or a duplicate-suspect record.
    try:
        token_candidates = list_same_name_people(workspace_root, name)
    except Exception:
        token_candidates = None
    if token_candidates is not None and len(token_candidates) == 1 and \
            _name_related(name, token_candidates[0].get("canonical_name") or ""):
        return {"tier": "merge_propose", "email": pointing,
                "matched_person": token_candidates[0], "silent_link_ok": False,
                "why": f"{name!r} names the one "
                       f"{(token_candidates[0].get('canonical_name') or token_candidates[0].get('id'))!r} "
                       "on file — a link question, never a second record "
                       "(IDENT1 rule 2)"}

    if len(_norm_name(name).split()) < 2:
        # Bug #19 pin: a lone first name is a permanent human decision — even
        # with role AND org AND an observed email.
        return {"tier": "confirm", "email": email, "matched_person": None,
                "why": "lone first name — a human decision, never auto "
                       "(Bug #19)"}

    try:
        collisions = list_same_name_people(workspace_root, name)
    except Exception:
        collisions = None  # unreadable substrate → fail safe, never auto
    if collisions is None:
        return {"tier": "confirm", "email": email, "matched_person": None,
                "why": "same-name collision check unavailable — never auto "
                       "on an unverified bar"}
    if collisions:
        names = ", ".join(sorted(
            (c.get("canonical_name") or c.get("id") or "?")
            for c in collisions)[:4])
        return {"tier": "confirm", "email": email, "matched_person": None,
                "why": f"same-name collision with {names} — auto-add with a "
                       "collision is Bug #19's exact shape"}

    # cr1#754: a cluster that says it may be two people is a question, and no
    # address, id or source count below can add it. No address is chosen.
    disagreement = cluster_disagreement(cluster)
    if disagreement:
        return {"tier": "confirm", "email": None, "matched_person": None,
                "why": f"{disagreement} under one name may be two people: "
                       "a human decides (cr1#754)"}
    n_families = len(cluster.get("source_families") or [])
    # cr1#754: two sources add a person only when one of them identifies
    # them. A name can be seen twice and still be two people.
    identifier = cluster_identifier(cluster, workspace_root) \
        if n_families >= 2 else None
    # SPEC_FLOW1 `identity.auto_create` — the workspace switch on the two
    # branches below, and ONLY on them: these are the two that put a new
    # record on file without asking. Off means the row asks, which is what
    # the final branch returns anyway, so a seat that turns it off gets the
    # pre-IDENT1 behaviour and nothing else changes.
    if (email or identifier) and not auto_create_enabled(workspace_root):
        return {"tier": "confirm", "email": email, "matched_person": None,
                "why": "adding people automatically is turned off for this "
                       "workspace — the row asks instead"}
    if email:
        # Covers the calendar-attendee route too: a calendar invitee's
        # display-name+address reaches the proposal as observed evidence text
        # (D10 capture-side attach), which lands in this branch.
        return {"tier": "auto", "email": email, "matched_person": None,
                "why": f"full name + observed address {email} + zero "
                       "collision (D2)"}
    if identifier:
        return {"tier": "auto", "email": None, "matched_person": None,
                "why": "full name + "
                       f"{n_families} independent source families "
                       f"({'/'.join(cluster['source_families'])}) + "
                       f"{identifier} + zero collision (D2)"}
    if n_families >= 2:
        return {"tier": "confirm", "email": email, "matched_person": None,
                "why": f"seen in {n_families} source families "
                       f"({'/'.join(cluster['source_families'])}) but by name "
                       "alone: two bare names never add a person without "
                       "asking (cr1#754)"}
    return {"tier": "confirm", "email": email, "matched_person": None,
            "why": "full name but a single source and no observed address — "
                   "stays a human confirm (D2)"}


# ---------------------------------------------------------------------------
# UXR1 D3 — the auto-link gate (M ruling 2026-07-21): a person_link
# auto-applies instead of minting a confirm row ONLY when ALL of (a)-(d)
# hold. The gate is load-bearing — widen it by ruling, never by drift; the
# pin test moves WITH the policy, never around it.
# ---------------------------------------------------------------------------

def _record_org_name(workspace_root, record: dict) -> str:
    """The on-file record's org display name (primary_org_id resolved via
    entities.json; legacy org_id honored). "" when none / unreadable."""
    org_id = (record.get("primary_org_id") or record.get("org_id") or "")
    if not org_id:
        return ""
    try:
        data = json.loads((Path(workspace_root) / "_hq" / "data" /
                           "entities.json").read_text(encoding="utf-8"))
        ent = data.get("entities") if isinstance(data.get("entities"), dict) \
            else data
        for o in ent.get("orgs") or []:
            if o.get("id") == org_id:
                return (o.get("canonical_name") or "").strip()
    except Exception:
        return ""
    return ""


def sole_record_for_email(workspace_root, addr: str):
    """AUTOAPPLY §4a — the ONE non-archived person record carrying `addr`,
    or None when zero or 2+ do (normalized, case-folded).

    "Exactly one" is the whole strength of the clause: an address on two
    records is a collision, not an identifier, and must never auto-link.
    Fail-safe — an unreadable substrate returns None, so the caller asks."""
    addr = (addr or "").strip().lower()
    if not addr:
        return None
    try:
        from entities_io import entities_collection
        from people_writer import get_person_emails

        data = json.loads((Path(workspace_root) / "_hq" / "data" /
                           "entities.json").read_text(encoding="utf-8"))
        hits = []
        for p in entities_collection(data, "people"):
            if not isinstance(p, dict) or p.get("status") == "archived":
                continue
            if addr in {e.strip().lower() for e in get_person_emails(p)}:
                hits.append(p)
        return hits[0] if len(hits) == 1 else None
    except Exception:
        return None


# STAFFCUT §3.4 — free-mail domains. A shared free-mail domain identifies
# NOBODY: two mentions "at gmail.com" have nothing in common, so a free-mail
# domain is never contextual corroboration.
#
# A LITERAL SET WAS NOT ENOUGH, and the gap was not theoretical. An exact-match
# list missed every localized storefront of the same providers — `yahoo.fr`,
# `hotmail.es`, `outlook.de`, `live.co.uk`, `protonmail.ch` all corroborated,
# and this ships to a fleet that includes a Spanish-market instance, so the
# miss lands on real people. Subdomains missed too: `corp.yahoo.com` and
# `mail.gmail.com` are as non-identifying as their parents.
#
# So the rule is THREE tests, cheapest first (see `is_free_mail_domain`):
#   1. the literal set below;
#   2. any PARENT domain in that set — a subdomain of a free host is free;
#   3. the registrable FIRST LABEL in FREE_MAIL_FIRST_LABELS — `yahoo.<any>` is
#      Yahoo whichever country's TLD it wears.
# Test 3 is deliberately conservative in the safe direction: a real company
# whose domain happens to start with one of those labels loses DOMAIN
# corroboration and its mentions keep asking. Asking too often is a cost; a
# wrong auto-link is a defect.
FREE_MAIL_DOMAINS = frozenset({
    # Google / Microsoft / Yahoo / Apple / AOL — the exact hosts, with the
    # first-label rule below covering their ccTLD storefronts.
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "passport.com", "yahoo.com", "ymail.com", "rocketmail.com",
    "aol.com", "aim.com", "icloud.com", "me.com", "mac.com",
    # Privacy / independent providers.
    "proton.me", "protonmail.com", "protonmail.ch", "pm.me", "tutanota.com",
    # RIDERS1 item 3 — the one host org_writer's retired local list covered that
    # this set did not. Folded in when the two lists became one, so consolidating
    # onto the stronger predicate lost no coverage at the weaker one's call site.
    "duck.com",
    "tutanota.de", "tuta.io", "hey.com", "fastmail.com", "fastmail.fm",
    "posteo.de", "mailbox.org", "runbox.com", "hushmail.com", "startmail.com",
    "zoho.com", "zohomail.com", "mail.com", "email.com", "usa.com",
    "gmx.com", "gmx.de", "gmx.net", "gmx.at", "gmx.ch", "web.de",
    "freenet.de", "t-online.de", "arcor.de",
    # Regional / ISP mail — no first-label rule for these (their labels are
    # ordinary words), so the ccTLD spellings are listed where they exist.
    "yandex.com", "yandex.ru", "mail.ru", "inbox.ru", "list.ru", "bk.ru",
    "rambler.ru", "qq.com", "foxmail.com", "163.com", "126.com", "sina.com",
    "sohu.com", "naver.com", "hanmail.net", "daum.net", "nate.com",
    "rediffmail.com", "sify.com",
    "comcast.net", "verizon.net", "att.net", "sbcglobal.net", "bellsouth.net",
    "cox.net", "charter.net", "earthlink.net", "juno.com", "netzero.net",
    "roadrunner.com", "optonline.net", "frontier.com", "windstream.net",
    "btinternet.com", "sky.com", "virginmedia.com", "talktalk.net",
    "ntlworld.com", "blueyonder.co.uk",
    "orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "laposte.net",
    "bbox.fr", "numericable.fr", "neuf.fr", "aliceadsl.fr",
    "libero.it", "virgilio.it", "alice.it", "tin.it", "tiscali.it",
    "fastwebnet.it", "email.it",
    "terra.com.br", "uol.com.br", "bol.com.br", "globo.com", "ig.com.br",
    "prodigy.net.mx", "telefonica.net", "movistar.es", "ono.com",
    "telefonica.es", "euskaltel.net", "wanadoo.es", "terra.es",
    "shaw.ca", "rogers.com", "sympatico.ca", "telus.net", "videotron.ca",
    "bigpond.com", "bigpond.net.au", "optusnet.com.au", "iinet.net.au",
    "tpg.com.au", "internode.on.net", "xtra.co.nz",
    "seznam.cz", "centrum.cz", "onet.pl", "wp.pl", "interia.pl", "o2.pl",
    "abv.bg", "mynet.com", "walla.co.il", "nifty.com", "biglobe.ne.jp",
    "ocn.ne.jp", "docomo.ne.jp", "ezweb.ne.jp", "softbank.ne.jp",
})

# Registrable FIRST LABELS of the global providers — the ccTLD-blind test. Only
# labels that are UNAMBIGUOUSLY a mail provider belong here: `yahoo.fr` and
# `hotmail.es` are Yahoo and Hotmail, while a label like `mail` or `orange`
# is an ordinary word that a real company can legitimately own.
FREE_MAIL_FIRST_LABELS = frozenset({
    "gmail", "googlemail", "hotmail", "outlook", "live", "msn", "yahoo",
    "ymail", "aol", "aim", "icloud", "protonmail", "tutanota", "gmx",
})


def is_free_mail_domain(domain) -> bool:
    """True when `domain` identifies a mail PROVIDER rather than an
    organisation — so it can never be contextual corroboration (§3.4).

    Three tests: the literal set, any parent domain in it (a subdomain of a
    free host is free), and the registrable first label among the global
    providers (ccTLD-blind). Defensive: a malformed value is treated as free,
    because "we could not read this domain" must not license a link."""
    dom = str(domain or "").strip().lower().rstrip(".")
    if not dom or "." not in dom:
        return True
    if dom in FREE_MAIL_DOMAINS:
        return True
    labels = dom.split(".")
    # Any PARENT domain in the set: corp.yahoo.com -> yahoo.com.
    for i in range(1, len(labels) - 1):
        if ".".join(labels[i:]) in FREE_MAIL_DOMAINS:
            return True
    # Any label but the TLD. Checking only the FIRST label missed the two cases
    # combined — `mx.hotmail.co.uk` is a subdomain of a ccTLD storefront, so
    # neither the parent-domain test (no `hotmail.co.uk` literal) nor a
    # first-label test (`mx`) catches it. Excluding the TLD keeps a hypothetical
    # TLD sharing a provider's name from tripping this. Deliberately
    # conservative: a company mail subdomain like `outlook.<theirdomain>` loses
    # DOMAIN corroboration and its mentions keep asking, which is a cost worth
    # paying against a wrong auto-link.
    return any(label in FREE_MAIL_FIRST_LABELS for label in labels[:-1])

# The corroboration clause names, one per §3.4 bullet. They ride the gate's
# `why` string and the tombstone's `auto_predicate`, so a later audit reads WHY
# a link fired off the event instead of a vanished chat.
CORROBORATION_EMAIL_DOMAIN = "email_domain"
CORROBORATION_SAME_ORG = "same_org"
CORROBORATION_MEETING = "meeting_co_occurrence"
CORROBORATION_KINDS = (CORROBORATION_EMAIL_DOMAIN, CORROBORATION_SAME_ORG,
                       CORROBORATION_MEETING)

_EMAIL_IN_TEXT_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _email_domain(addr) -> str:
    addr = str(addr or "").strip().lower()
    return addr.rsplit("@", 1)[-1] if "@" in addr else ""


def _corroborating_domains(addresses) -> set:
    """The domains among `addresses` that can corroborate anything: role-shaped
    local parts and free-mail domains contribute NOTHING. Both exclusions are
    §3.4's, and both are pinned: a role address identifies a mailbox rather than
    a person, and a free-mail domain identifies no organisation at all."""
    out: set = set()
    for addr in addresses or ():
        if is_role_address(addr):
            continue
        dom = _email_domain(addr)
        if dom and not is_free_mail_domain(dom):
            out.add(dom)
    return out


def _mention_domains(cluster: dict) -> set:
    """Corroborating domains visible in the MENTION's own captured text.

    Deliberately a raw text scan rather than `_observed_email`: that helper
    answers "which single address attributes to THIS person" (the F-3 rule) and
    returns nothing when a capture carries several addresses — which is exactly
    the shape whose DOMAIN is still a real signal. The strong per-address bars
    are unaffected: gate (c) still refuses any attributable address that does
    not belong to the record, and it runs BEFORE this."""
    out: set = set()
    for row in cluster.get("add_rows") or []:
        text = f"{row.get('source_ref') or ''} {row.get('evidence') or ''}"
        out |= _corroborating_domains(_EMAIL_IN_TEXT_RE.findall(text))
    return out


def _record_domains(workspace_root, record: dict) -> set:
    """Corroborating domains registered against the matched RECORD — its own
    addresses, plus its org's declared domain / website host when entities.json
    carries one. Defensive: an unreadable substrate yields what it can, and an
    empty set simply means this clause cannot fire."""
    out = _corroborating_domains(_record_emails(record or {}))
    org_id = ((record or {}).get("primary_org_id")
              or (record or {}).get("org_id") or "")
    if not org_id:
        return out
    try:
        data = json.loads((Path(workspace_root) / "_hq" / "data" /
                           "entities.json").read_text(encoding="utf-8"))
        ent = data.get("entities") if isinstance(data.get("entities"), dict) \
            else data
        for o in ent.get("orgs") or []:
            if o.get("id") != org_id:
                continue
            raw = []
            for key in ("domain", "domains", "email_domain", "website", "url"):
                val = o.get(key)
                if isinstance(val, str):
                    raw.append(val)
                elif isinstance(val, (list, tuple)):
                    raw.extend(str(v) for v in val)
            for val in raw:
                host = str(val).strip().lower()
                host = host.split("//", 1)[-1].split("/", 1)[0]
                host = host.split("@", 1)[-1].lstrip("*.")
                if host.startswith("www."):
                    host = host[4:]
                if "." in host and not is_free_mail_domain(host):
                    out.add(host)
            break
    except Exception:
        return out
    return out


def _meeting_co_occurrence(workspace_root, cluster: dict,
                           matched: dict) -> str:
    """The mention and the record appear in the SAME meeting's substrate
    events — connector-agnostic by construction: it reads `meeting` /
    `meeting_processed` events through the canonical iterator and matches the
    mention's own `source_ref`, never a Gmail/Granola-specific path.

    Returns the meeting's title (or "" when it has none but matched), and "" when
    there is no co-occurrence. Fail-safe: any read error returns "" and the
    clause simply does not fire."""
    try:
        from meeting_capture import _norm_ref_keys

        refs: set = set()
        for row in cluster.get("add_rows") or []:
            refs |= _norm_ref_keys(row.get("source_ref"))
        if not refs:
            return ""
        mid = (matched or {}).get("id")
        emails = {e.strip().lower() for e in _record_emails(matched or {})
                  if str(e).strip()}
        from events_io import iter_events

        for ev in iter_events(Path(workspace_root)):
            if ev.get("type") not in ("meeting", "meeting_processed"):
                continue
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            keys = (_norm_ref_keys(d.get("source_ref"))
                    | _norm_ref_keys(d.get("meeting_id")))
            if not (keys & refs):
                continue
            # BUG-8244: fold every binding variant — ids via the shared
            # normalizer, emails via attendee_emails_of (attendees /
            # attendee_emails / invitees), not one hand-picked field each.
            from event_refs import attendee_emails_of, meeting_person_ids
            hit = bool(mid and mid in meeting_person_ids(ev))
            if not hit and emails:
                hit = bool(emails & attendee_emails_of(ev))
            if hit:
                return str(d.get("title") or "").strip() or "the same call"
    except Exception:
        return ""
    return ""


def _name_related(name: str, matched_name: str) -> bool:
    """Is the mention's spelling a NAME RELATION of the record's, rather than a
    coincidence? Normalized token sets, one a non-empty subset of the other.

    Deliberately NOT fuzzy and NOT phonetic. "Bo" against "Bo Sample" is a
    relation the corroboration clause may act on; a typo ("Skylar" against
    "Skyler") shares no token and keeps asking, because a misspelling plus a
    shared office is exactly the pair a human should look at. This is a
    NECESSARY condition and never a sufficient one — corroboration is what
    licenses the link, and §3.4's highest-stakes invariant is that name
    similarity ALONE never auto-links."""
    a = {t for t in _norm_name(name).split() if len(t) > 1}
    b = {t for t in _norm_name(matched_name).split() if len(t) > 1}
    if not a or not b:
        return False
    return a <= b or b <= a


def auto_create_enabled(workspace_root) -> bool:
    """SPEC_FLOW1 `identity.auto_create` — may this workspace put a person
    on file on its own? Default ON, off by word, fail-to-default."""
    try:
        import commitment_policy as _policy

        return _policy.flow_switch_enabled(workspace_root,
                                           _policy.IDENTITY_AUTO_CREATE_KEY)
    except Exception:
        return True


def auto_merge_enabled(workspace_root) -> bool:
    """SPEC_FLOW1 `identity.auto_merge` — may this workspace fold a spelling
    into a record on its own? Default ON, off by word, fail-to-default."""
    try:
        import commitment_policy as _policy

        return _policy.flow_switch_enabled(workspace_root,
                                           _policy.IDENTITY_AUTO_MERGE_KEY)
    except Exception:
        return True


def _edit_distance(a: str, b: str, *, cap: int = 3) -> int:
    """Plain Levenshtein, bounded. Returns `cap + 1` for anything further
    apart than `cap` — the callers only ever ask "is this within two?"."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


SURNAME_VARIANT_MAX_DISTANCE = 2
SURNAME_VARIANT_MIN_LENGTH = 4


def surname_variant(name: str, other: str) -> bool:
    """Is `name` the SAME full name as `other`, spelled differently in the
    surname? (SPEC_FLOW1 Lane F rule 2, M 2026-09-07.)

    `_name_related` above deliberately refuses this case, and says why: "a
    typo ('Skylar' against 'Skyler') shares no token and keeps asking,
    because a misspelling plus a shared office is exactly the pair a human
    should look at." That was the right default when the alternative was a
    silent write nobody could see. It is the wrong default now: the write is
    receipted and undoable, and the pairs it kept asking about sat on M's
    Staff Meeting from June to September without a single answer.

    The relation is deliberately NARROW, and every clause below is there to
    keep two DIFFERENT people apart rather than to catch more spellings:

      * both names carry at least two tokens — a lone first name is Bug
        #19's permanent human decision and never reaches here;
      * every token but the last matches EXACTLY after normalization, so a
        different given name is a different person no matter how close the
        surnames run;
      * the surnames differ (an exact match is `_name_related`'s job) by at
        most `SURNAME_VARIANT_MAX_DISTANCE` characters;
      * the shorter surname is at least `SURNAME_VARIANT_MIN_LENGTH`
        characters — at two characters of slack, three-letter surnames are
        nearly all "variants" of each other, which is a coincidence
        generator, not a relation.

    Like `_name_related`, this is a NECESSARY condition and never a
    sufficient one: the caller still has to find a substrate fact that says
    the two are the same person. Name similarity alone never auto-links, and
    that invariant is untouched here."""
    a = _norm_name(name or "").split()
    b = _norm_name(other or "").split()
    if len(a) < 2 or len(b) < 2:
        return False
    if a[:-1] != b[:-1]:
        return False
    sa, sb = a[-1], b[-1]
    if sa == sb:
        return False
    if min(len(sa), len(sb)) < SURNAME_VARIANT_MIN_LENGTH:
        return False
    return _edit_distance(sa, sb) <= SURNAME_VARIANT_MAX_DISTANCE


def variant_candidates(workspace_root, name: str) -> list[dict]:
    """Every non-archived person record whose canonical name (or any
    display spelling) is a `surname_variant` of `name`. The variant
    clause's own candidate set — bar (b)'s "exactly one on-file candidate"
    is checked against THIS list for a variant, because
    `list_same_name_people` is a TOKEN match and a misspelled surname
    shares no token with the record it belongs to (which is the whole
    reason these rows never resolved)."""
    from entities_io import entities_collection
    from people_writer import get_person_display_names

    try:
        doc = json.loads((Path(workspace_root) / "_hq" / "data"
                          / "entities.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[dict] = []
    for rec in entities_collection(doc, "people") or []:
        if not isinstance(rec, dict) or rec.get("status") == "archived":
            continue
        for surface in get_person_display_names(rec):
            if surname_variant(name, surface):
                out.append(rec)
                break
    return out


def link_corroboration(workspace_root, cluster: dict, matched: dict):
    """(kind, detail) when the mention is contextually corroborated against the
    matched record, else None — STAFFCUT §3.4 / M ruling 1 (2026-08-02):
    "it has to match somehow on context."

    A name relation is required FIRST, then at least ONE of the three context
    clauses. Each clause is a fact about the substrate, not a similarity score:

      email_domain          a non-role address in the mention's own captured
                            text shares a non-free-mail domain registered to
                            the record (its addresses or its org's);
      same_org              the mention's inferred org RESOLVES TO the record's
                            org — the exact inverse of gate (c)'s contradiction
                            check, so the two can never disagree;
      meeting_co_occurrence the mention and the record appear in the same
                            meeting's events (attendee id or address).

    Order is cheapest-first and decides only WHICH clause is recorded; any one
    of them licenses the link. Returns None on anything unverifiable, which the
    caller turns into a confirm row."""
    name = (cluster.get("name") or "").strip()
    matched_name = ((matched or {}).get("canonical_name") or "").strip()
    if not name or not matched_name:
        return None
    related = _name_related(name, matched_name)
    # IDENT1 — the SPELLING-VARIANT relation (rule 2) is admitted here, and
    # on a SHORTER list of clauses than the token relation. A token relation
    # ("Bo" against "Bo Sample") is an abbreviation of a name the record
    # already holds; co-occurrence in one meeting is enough to say they are
    # the same person. A variant is a name the record does NOT hold, so the
    # corroboration has to be an identifying fact about the person —
    # their org or their mail domain — not merely "both were in a room".
    # Two different people in one meeting is the ordinary case; two people
    # with near-identical surnames at the same company is not.
    variant = False if related else surname_variant(name, matched_name)
    if not related and not variant:
        return None

    inferred_org = (cluster.get("inferred_org") or "").strip()
    if inferred_org:
        record_org = _record_org_name(workspace_root, matched)
        if record_org and _norm_name(inferred_org) == _norm_name(record_org):
            return CORROBORATION_SAME_ORG, record_org

    shared = _mention_domains(cluster) & _record_domains(workspace_root,
                                                         matched)
    if shared:
        return CORROBORATION_EMAIL_DOMAIN, sorted(shared)[0]

    if variant:
        return None
    title = _meeting_co_occurrence(workspace_root, cluster, matched)
    if title:
        return CORROBORATION_MEETING, title
    return None


# The `auto_predicate` recorded on an auto-link tombstone (§7 — one string
# naming WHY). Keyed off the gate's own `why` markers so the reason on the event
# and the reason in the gate can never drift apart.
_PREDICATE_MARKERS = (
    ("belongs to exactly one record", "id_fact:email_match"),
    (f"corroborated by {CORROBORATION_SAME_ORG}",
     f"context:{CORROBORATION_SAME_ORG}"),
    (f"corroborated by {CORROBORATION_EMAIL_DOMAIN}",
     f"context:{CORROBORATION_EMAIL_DOMAIN}"),
    (f"corroborated by {CORROBORATION_MEETING}",
     f"context:{CORROBORATION_MEETING}"),
)


def link_auto_predicate(why: str) -> str:
    """The §7 predicate string for an auto-link, derived from the gate's own
    reason. Unknown reasons fall back to the exact-name clause, which is the
    only remaining way the gate can say yes."""
    text = str(why or "")
    for marker, predicate in _PREDICATE_MARKERS:
        if marker in text:
            return predicate
    return "exact_name:unique_clean"


# ---------------------------------------------------------------------------
# IDENT1 rule 3 (SPEC_FLOW1 Lane F) — an identity question older than 14 days
# resolves to its DEFAULT, reversibly, and the queue drains.
#
# The queue is the reason this rule exists. On the 2026-09-07 book copy 109
# identity questions were open and 85 of them had been open longer than
# fourteen days — the oldest since June. A question nobody answers in two
# months is not a question, it is a leak, and the customer pays for it twice:
# once on the Staff Meeting, where the rows crowd out the ones that matter,
# and once at the exit door, which cannot prove a close against a
# counterparty who is still "maybe someone".
#
# The default is deliberately one of THREE outcomes, not two:
#   * LINK    — there is a likeliest match and nothing in the substrate
#               argues against it. The row resolves onto that record on the
#               same rail an eligible auto-link uses, with the same receipt
#               and the same `undo`.
#   * LET GO  — there is no likeliest match, or the substrate positively
#               CONTRADICTS the one on offer. Nothing is asserted about who
#               this is; the QUESTION is dropped, reversibly. This is the
#               spec's "not a person" default read honestly: the product is
#               not claiming the mention was never a person, it is saying it
#               will stop asking.
#   * KEEP OPEN — the substrate could not be read, or the customer turned
#               the merging switch off. An unreadable file is not an answer,
#               and a customer who said "ask me" is not aged out of it.
# ---------------------------------------------------------------------------

IDENTITY_QUESTION_TTL_DAYS = 14
IDENTITY_DEFAULT_LINK = "link"
IDENTITY_DEFAULT_LET_GO = "let_go"
IDENTITY_DEFAULT_KEEP_OPEN = "keep_open"

# Refusal reasons from `auto_link_eligible` that are the substrate saying NO,
# as distinct from the substrate saying NOT YET. Keyed on stable fragments of
# the gate's own sentences, the same technique `_PREDICATE_MARKERS` uses, so
# the gate and this table cannot drift apart into two policies.
_CONTRADICTION_MARKERS = (
    "conflicting signal",          # an observed address that is not the record's
    "role-shaped",                 # a shared inbox is not a person
    "contradicts the",             # the mention's org against the record's
    "IDM1 class",                  # two candidates — there is no likeliest
    "duplicate-suspect set",       # the record itself is under question
)
# Refusals that mean the check could not RUN. Never a default: a file that
# would not open is not a customer's answer.
_UNAVAILABLE_MARKER = "unavailable"
_SWITCH_OFF_MARKER = "is turned off for this workspace"


def link_refusal_is_contradiction(why: str) -> bool:
    """Does this `auto_link_eligible` refusal mean the substrate argues the
    two are DIFFERENT people, rather than merely that it cannot vouch they
    are the same?"""
    text = str(why or "")
    return any(m in text for m in _CONTRADICTION_MARKERS)


def identity_question_age_days(cluster: dict, now) -> Optional[int]:
    """How long this identity question has been asked, in days, measured
    from its OLDEST member's `question_clock`: the capture, or for a row held
    back the day it came up (cr1#773). None when no member carries a readable
    timestamp — an undated row never ages out."""
    from confirm_flow import question_clock
    from event_time import parse_ts

    if now is None or not isinstance(cluster, dict):
        return None
    stamps = []
    for row in cluster.get("rows") or []:
        ts = parse_ts(question_clock(row))
        if ts is not None:
            stamps.append(ts)
    if not stamps:
        return None
    try:
        return (now - min(stamps)).days
    except Exception:
        return None


def identity_aged_default(workspace_root, cluster: dict, matched, *,
                          age_days: Optional[int],
                          ttl_days: int = IDENTITY_QUESTION_TTL_DAYS,
                          email: str | None = None) -> Optional[dict]:
    """The default for ONE aged identity question, or None when the question
    has not aged out yet (SPEC_FLOW1 Lane F rule 3).

    Returns `{"default", "why", "matched", "age_days"}`. THIS is the function
    TTL1's generic expiry engine registers for the identity class — the
    lifetime (`IDENTITY_QUESTION_TTL_DAYS`) and the default live here, next
    to the gate whose refusals they read, and the engine owns the schedule.

    Pure: it decides, it never writes. `run_identity_reconcile` applies the
    decision on the existing rails, so the receipts and the reversers are
    the ones already registered."""
    if age_days is None or age_days <= int(ttl_days):
        return None
    out = {"cluster": cluster, "matched": matched, "age_days": age_days}
    if matched is None:
        # Nothing on file is a likelier answer than anything else. The
        # question stops being asked; no record is created, none is touched.
        # Review F-18 — this sentence is written into the tombstone `note` on
        # every aged let-go (71 of them on the operator's copy), so it has to
        # read like English. It used to interpolate `age_days // 30 or 1`
        # followed by a literal "th" and say "asked a 1th month".
        return dict(out, default=IDENTITY_DEFAULT_LET_GO,
                    why=f"asked for {age_days} days with no match on file — "
                        f"let go rather than asked for another month (window "
                        f"{ttl_days}d)")
    eligible, gate_why = auto_link_eligible(workspace_root, cluster, matched,
                                            email)
    if eligible:
        # The ordinary auto-link rail already claimed this one in the same
        # run; the aged pass never double-applies.
        return dict(out, default=IDENTITY_DEFAULT_LINK,
                    why=f"asked for {age_days} days; {gate_why}")
    if _SWITCH_OFF_MARKER in str(gate_why):
        return dict(out, default=IDENTITY_DEFAULT_KEEP_OPEN,
                    why="merging duplicate people is turned off for this "
                        "workspace — an aged question is still the "
                        "customer's to answer")
    if _UNAVAILABLE_MARKER in str(gate_why):
        return dict(out, default=IDENTITY_DEFAULT_KEEP_OPEN,
                    why=f"{gate_why} — a check that could not run is not an "
                        "answer, and age does not make it one")
    if link_refusal_is_contradiction(gate_why):
        return dict(out, default=IDENTITY_DEFAULT_LET_GO,
                    why=f"asked for {age_days} days and the record on offer "
                        f"is contradicted ({gate_why}) — the question is let "
                        "go, nobody is linked (window "
                        f"{ttl_days}d)")
    # Not enough to vouch for it on day one; after two weeks unanswered the
    # likeliest match IS the default, and it comes back with one `undo`.
    return dict(out, default=IDENTITY_DEFAULT_LINK,
                why=f"asked for {age_days} days and unanswered — resolved to "
                    f"the likeliest match on file (window {ttl_days}d; it "
                    "was not auto-linked on day one because the spelling "
                    "differs and nothing corroborated it)")


# ---------------------------------------------------------------------------
# IDENT1 rule 4 — a nickname that names two people is dropped BY RULE
#
# A saved alias is a PERMANENT resolution shortcut: every future capture of
# that spelling resolves, silently and forever, to the record it was saved
# on. That is exactly what makes it valuable and exactly what makes an
# ambiguous one dangerous — a short first name saved onto one record quietly
# swallows every mention of the OTHER person who answers to it, and nothing
# on any surface ever says so. On the 2026-09-07 book copy this had already
# happened, and the only repair available was a hand edit of a data file,
# which is the thing SPEC_FLOW1 exists to end.
#
# The rule is narrow on purpose. A spelling is dropped only when it names a
# DIFFERENT record on file as a whole name — the same `_name_related`
# relation the link gate uses, so "Bo" saved on the one record that answers
# to it stays, and "Bo" saved on one of two people who both do goes. The
# record's own canonical name is never touchable (`remove_person_alias`
# refuses it): a record must always have something to be called by.
# ---------------------------------------------------------------------------

ALIAS_COLLISION_CHANGE_CLASS = "person_alias_dropped"
ALIAS_DROPPED_EVENT = "person_alias_dropped"


def _unresolved_label() -> str:
    """The base-era label, read live from LEAK2's `narration_names` when that
    module is on the path so this branch alone and the LEAK2-merged tree read
    ONE shared label instead of two hard-coded copies drifting apart (LEAK2
    @ 39f858ab renamed the constant's value from "(name on file)" to
    "(no name on file)"). Falls back to the base-era literal when the module
    is absent, so pre-merge behaviour is unchanged."""
    try:
        from narration_names import UNRESOLVED_LABEL

        return UNRESOLVED_LABEL
    except Exception:
        return "(name on file)"


def _display_name(candidate, *, fallback: str = None) -> str:
    """The name a receipt or a card may print. Delegates to LEAK2's
    `narration_names.safe_name` when that module is present, and keeps its
    contract when it is not: an internal id is NEVER printed as a name, and
    a record with no name reads as an honest label instead of a blank."""
    if fallback is None:
        fallback = _unresolved_label()
    try:
        from narration_names import safe_name

        return safe_name(candidate, fallback=fallback)
    except Exception:
        text = str(candidate or "").strip()
        if not text:
            return fallback
        return fallback if re.match(
            r"^(person|project|org|event|matter|engagement)_\d{3,}$",
            text, re.IGNORECASE) else text


def colliding_aliases(workspace_root) -> list[dict]:
    """Every saved alias that names more than one person on file.

    `[{person_id, person_name, alias, collides_with, collides_with_name}]`,
    stable order. Pure — it finds, it never writes."""
    from entities_io import entities_collection
    from people_writer import get_person_display_names

    try:
        doc = json.loads((Path(workspace_root) / "_hq" / "data"
                          / "entities.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    people = [p for p in (entities_collection(doc, "people") or [])
              if isinstance(p, dict) and p.get("status") != "archived"]
    out: list[dict] = []
    for rec in people:
        canon = (rec.get("canonical_name") or "").strip()
        canon_norm = _norm_name(canon)
        seen: set = set()
        for surface in get_person_display_names(rec)[1:]:
            alias = str(surface or "").strip()
            alias_norm = _norm_name(alias)
            if not alias_norm or alias_norm == canon_norm or alias_norm in seen:
                continue
            seen.add(alias_norm)
            for other in people:
                if other.get("id") == rec.get("id"):
                    continue
                other_name = (other.get("canonical_name") or "").strip()
                if not other_name:
                    continue
                if _name_related(alias, other_name):
                    out.append({
                        "person_id": rec.get("id"),
                        "person_name": _display_name(canon),
                        "alias": alias,
                        "collides_with": other.get("id"),
                        "collides_with_name": _display_name(other_name),
                    })
                    break
    return out


def apply_alias_collisions(workspace_root, *, batch_id: str,
                           source_skill: str = "identity-reconcile",
                           limit: int = 25) -> list[dict]:
    """Drop every colliding alias found above, one receipted, reversible act
    each. Returns what was ACTUALLY dropped (the honesty rule).

    Each drop appends a `person_alias_dropped` event carrying the person and
    the spelling, batch-stamped with the `person_alias_dropped` change class
    — which is the whole reason `undo` can put the nickname back."""
    from event_gate import append_event
    from people_writer import remove_person_alias

    dropped: list[dict] = []
    for hit in colliding_aliases(workspace_root)[:max(0, int(limit))]:
        try:
            res = remove_person_alias(workspace_root, hit["person_id"],
                                      hit["alias"], source_skill=source_skill)
        except Exception:
            continue
        if res.get("status") != "removed":
            continue
        append_event(
            _events_path(Path(workspace_root)),
            [{"type": ALIAS_DROPPED_EVENT, "source_skill": source_skill,
              "data": {"person_id": hit["person_id"],
                       "alias": hit["alias"],
                       "collides_with": hit["collides_with"],
                       "brain_batch_id": batch_id,
                       "brain_change_class": ALIAS_COLLISION_CHANGE_CLASS,
                       "note": (f"{hit['alias']!r} named both "
                                f"{hit['person_name']} and "
                                f"{hit['collides_with_name']} — a nickname "
                                "that names two people resolves neither")}}],
            holder=source_skill)
        dropped.append(dict(hit, **{k: res[k] for k in
                                    ("mapping_removed", "record_written")}))
    return dropped


def auto_link_eligible(workspace_root, cluster: dict, matched: dict,
                       email: str | None) -> tuple[bool, str]:
    """(eligible, why) — the UXR1 D3 (a)-(d) gate for ONE merge-propose
    entry whose cluster confidently resolved to `matched`.

      (a′) EITHER an exact normalized full-name match of ≥2 tokens, OR the
          mention carries an email address that exactly matches exactly ONE
          on-file person's address — and that person is `matched`
          (AUTOAPPLY §4a, M ruling "act when the evidence is corroborated
          and the action is reversible"). An id-level address is strictly
          stronger evidence than a name match, so requiring the name match
          ON TOP of it was the gate asking a question it already had the
          answer to: a first-name mention and its full-name record with the
          same address on both
          sides. Role/shared-inbox addresses are excluded (they identify a
          mailbox, not a person), and a lone first name still needs the
          address — the bare-name case alone is Bug #19 forever;
      (b) exactly ONE on-file candidate for that name — two same-name
          records are the IDM1 class, NEVER auto. THIS is also what keeps
          (a′)'s email clause honest: a mention named X whose only observed
          address belongs to record Y (the misattributed quoted-thread
          sender, second-eyes F2) fails here, because X's same-name
          candidate set never equals {Y};
      (c) no conflicting signal: the mention's observed email, when present,
          must belong to the record (a role/shared-inbox address is NOT
          corroboration and never auto-links — §0-2's guard applied here);
          the mention's inferred org, when present and resolvable, must not
          contradict the record's org;
      (d) the pair is not in scan_existing_duplicates' suspect set (a
          record under duplicate suspicion is not a safe link target).

    STAFFCUT §3.4 (M ruling 2026-08-02) added a THIRD clause to (a′) and
    changed nothing else: a spelling RELATION (`_name_related`) that the
    substrate CORROBORATES ON CONTEXT — a shared non-free-mail email domain, the
    same org, or co-occurrence in the same meeting's events (see
    `link_corroboration`). 15 of the 33 person rows on the audit day were
    link-shaped and failed on (a′) alone, at a median answer latency of 43 days;
    where context says the two are the same person, asking is the wrong default
    for a change this reversible.

    NAME SIMILARITY ALONE NEVER AUTO-LINKS. That is the highest-stakes
    invariant in the build, and it is why the clause needs a substrate fact on
    top of the relation. Bars (b), (c) and (d) are byte-intact: a widened (a′)
    still has to clear exactly one on-file candidate, no conflicting
    org/address signal, and no duplicate suspicion.

    Fail-safe throughout: any unverifiable bar returns False (the row still
    renders as a confirm ask — a human decision is the safe floor)."""
    name = (cluster.get("name") or "").strip()
    matched_name = (matched or {}).get("canonical_name") or ""
    # SPEC_FLOW1 — the workspace switch. Off means the row asks, which is
    # exactly what the whole gate below returns anyway, so a seat that turns
    # it off gets the pre-IDENT1 behaviour with no other change.
    if not auto_merge_enabled(workspace_root):
        return False, ("merging duplicate people is turned off for this "
                       "workspace — the row asks instead")
    if _NAME_ANNOTATION_RE.search(name):
        return False, "name carries annotation/guess markers — never auto"
    if _cluster_says_another_person(cluster, matched):
        # cr1#825: "conflicting signal" is a contradiction marker, so an aged
        # default lets this question go and never links the two.
        return False, ("conflicting signal: the record keeps another source "
                       "id for this name")
    # (c) observed-address scan — hoisted above (a′) because the email clause
    # reads it. RAW scan, not cluster_observed_email (that helper deliberately
    # SKIPS role-shaped addresses at capture — the F1 guard — so relying on it
    # here would auto-link a mention whose only observed address is a shared
    # inbox; the ruling says that case ASKS).
    observed: list[str] = []
    try:
        from person_backlog_sweep import _observed_email

        for row in cluster.get("add_rows") or []:
            probe = dict(row)
            probe["name"] = name
            addr = _observed_email(probe)
            if addr:
                observed.append(addr)
    except Exception:
        return False, "observed-address scan unavailable — never auto"
    record_emails = {e.strip().lower() for e in _record_emails(matched)}
    for addr in observed:
        if is_role_address(addr):
            return False, ("observed address is role-shaped (shared inbox) "
                           "— not corroboration, still asks (§0-2)")
        if addr.strip().lower() not in record_emails:
            return False, ("observed address does not belong to the record "
                           "— conflicting signal, still asks")
    # (b), first half, ahead of (a′) so no softer refusal can stand in for it
    # and let the aged default link on it. cr1#768: `list_same_name_people`
    # below leaves out archived records, and an archived record still answers
    # to its name in `find_existing_person`, so a record CALLED this is a
    # second candidate whatever its status. Only that: an archived record
    # sharing one word with the name is not a candidate for it.
    if _name_is_someone_elses(workspace_root, name, matched):
        return False, (f"{name!r} is another record's own name: two "
                       "candidates are the IDM1 class, never auto")
    # (a′) exact multi-token name match OR id-level email corroboration OR a
    # name relation that CONTEXT corroborates (STAFFCUT §3.4).
    exact_name = (_norm_name(name) == _norm_name(matched_name)
                  and len(_norm_name(name).split()) >= 2)
    id_level_email = None
    if not exact_name:
        for addr in observed:
            sole = sole_record_for_email(workspace_root, addr)
            if sole is not None and sole.get("id") == matched.get("id"):
                id_level_email = addr.strip().lower()
                break
    corroboration = None
    if not exact_name and not id_level_email:
        # The third clause, and the ONLY one M's 2026-08-02 ruling added: a
        # spelling relation the substrate corroborates on CONTEXT. It runs last
        # so the two stronger clauses keep reporting their own reasons, and it
        # can only ever ADD a yes — the bars below ((b), (c)'s org check, (d))
        # are untouched and every one of them still has to pass.
        corroboration = link_corroboration(workspace_root, cluster, matched)
    if not exact_name and not id_level_email and not corroboration:
        if _norm_name(name) != _norm_name(matched_name):
            return False, ("spelling differs from the record and nothing "
                           "corroborates it — no id-level address, no shared "
                           "domain, no shared org, no shared meeting: a human "
                           "decision")
        return False, "lone first name with no address — never auto (Bug #19)"
    # (b) exactly one on-file candidate
    from people_writer import list_same_name_people

    try:
        candidates = list_same_name_people(workspace_root, name)
    except Exception:
        return False, "same-name candidate check unavailable — never auto"
    ids = {c.get("id") for c in (candidates or []) if c.get("id")}
    # IDENT1 — a misspelled surname shares NO token with the record it
    # belongs to, so the token-level candidate set is empty for exactly the
    # rows the variant clause exists to resolve. Bar (b) is not relaxed: it
    # is asked of the candidate set the relation itself defines, and the
    # answer must still be exactly one record, and that record must be the
    # match. A spelling within two characters of TWO people on file is the
    # IDM1 class and keeps asking, same as before.
    if corroboration is not None and surname_variant(name, matched_name):
        try:
            variants = variant_candidates(workspace_root, name)
        except Exception:
            return False, "variant candidate check unavailable — never auto"
        vids = {c.get("id") for c in (variants or []) if c.get("id")}
        if vids != {matched.get("id")}:
            return False, (f"{len(vids)} on-file spellings within reach of "
                           f"{name!r} — two candidates are the IDM1 class, "
                           "never auto")
        # A variant that ALSO token-collides with other records (a shared
        # first name) is still only ever linked to the one variant match;
        # the token set is allowed to be wider here and is not a veto.
        ids = vids
    if ids != {matched.get("id")}:
        return False, (f"{len(ids)} on-file candidates for {name!r} — "
                       "two same-name records are the IDM1 class, never auto")
    # (c) no conflicting signal — org
    inferred_org = (cluster.get("inferred_org") or "").strip()
    if inferred_org:
        record_org = _record_org_name(workspace_root, matched)
        if record_org and _norm_name(inferred_org) != _norm_name(record_org):
            return False, (f"mention's org {inferred_org!r} contradicts the "
                           f"record's {record_org!r} — still asks")
    # (d) not a duplicate suspect
    try:
        suspects = scan_existing_duplicates(workspace_root)
    except Exception:
        return False, "duplicate-suspect scan unavailable — never auto"
    mid = matched.get("id")
    for s in suspects:
        if mid in (s["keep"].get("id"), s["duplicate"].get("id")):
            return False, ("record is in the duplicate-suspect set — "
                           "still asks")
    if id_level_email:
        return True, (f"{id_level_email} belongs to exactly one record — "
                      f"{matched_name!r} (UXR1 D3 gate a'-d, AUTOAPPLY §4a)")
    if corroboration:
        kind, detail = corroboration
        return True, (f"{name!r} matches {matched_name!r} and is corroborated "
                      f"by {kind} ({detail}) — never name similarity alone "
                      "(STAFFCUT §3.4, M ruling 2026-08-02; gate b-d unchanged)")
    return True, (f"exact unique clean match to {matched_name!r} "
                  "(UXR1 D3 gate a-d)")


# ---------------------------------------------------------------------------
# Existing-record duplicate scan (D4b — the absorbed LB2 dedup detector)
# ---------------------------------------------------------------------------

def scan_existing_duplicates(workspace_root) -> list[dict]:
    """Duplicate suspects among EXISTING non-archived records: exact
    multi-token normalized canonical_name pairs, or a shared personal email.
    Single-token name pairs are EXCLUDED from name-based suspicion entirely
    (the live person_077/person_124 two-short-names pin — two different real
    people); role-shaped shared addresses are excluded from email-based
    suspicion (a shared inbox is not an identity). PROPOSE-only input —
    nothing here merges anything."""
    from entities_io import entities_collection
    from people_writer import get_person_emails

    try:
        data = json.loads((Path(workspace_root) / "_hq" / "data" /
                           "entities.json").read_text(encoding="utf-8"))
        people = [p for p in entities_collection(data, "people")
                  if p.get("status") != "archived"]
    except Exception:
        return []

    pairs: dict[tuple, dict] = {}

    by_name: dict[str, list[dict]] = {}
    for p in people:
        norm = _norm_name(p.get("canonical_name"))
        if len(norm.split()) >= 2:
            by_name.setdefault(norm, []).append(p)
    for norm, group in by_name.items():
        if len(group) < 2:
            continue
        for a, b in zip(group, group[1:]):
            keep, dup = _pick_keep(a, b)
            pairs[(keep["id"], dup["id"])] = {
                "keep": keep, "duplicate": dup,
                "why": f"exact canonical-name match {norm!r} on two records"}

    by_email: dict[str, list[dict]] = {}
    for p in people:
        for e in get_person_emails(p):
            e = e.strip().lower()
            if e and not is_role_address(e):
                by_email.setdefault(e, []).append(p)
    for email, group in by_email.items():
        seen_ids = {g["id"] for g in group}
        if len(seen_ids) < 2:
            continue
        uniq = list({g["id"]: g for g in group}.values())
        for a, b in zip(uniq, uniq[1:]):
            keep, dup = _pick_keep(a, b)
            pairs.setdefault((keep["id"], dup["id"]), {
                "keep": keep, "duplicate": dup,
                "why": f"both records carry {email}"})
    return list(pairs.values())


def _pick_keep(a: dict, b: dict) -> tuple[dict, dict]:
    """Keeper = the richer record (more non-empty fields); tie → the older
    (lower-numbered) id. The proposal row embeds both ids verbatim — the
    user's click is the verdict, this is just a sensible default order."""
    def richness(p):
        return sum(1 for v in p.values() if v not in (None, "", [], {}))
    ra, rb = richness(a), richness(b)
    if ra != rb:
        return (a, b) if ra > rb else (b, a)
    return (a, b) if str(a.get("id")) <= str(b.get("id")) else (b, a)


# ---------------------------------------------------------------------------
# Annotations (D5) — unnamed speakers are meeting annotations, never rows
# ---------------------------------------------------------------------------

def load_open_annotations(workspace_root) -> list[dict]:
    """Open `unidentified_attendee_observed` events: every annotation whose
    seq is NOT listed in any later `identity_reconcile_run` receipt's
    `data.annotations_resolved`. Fully silent surface (§0-4) — the ONLY
    render is the weekly staff meeting's one count line."""
    import event_refs

    path = _events_path(workspace_root)
    if not path.exists():
        return []
    events = event_refs.load_events(path)
    resolved: set[int] = set()
    for ev in events:
        if ev.get("type") != "identity_reconcile_run":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        for s in data.get("annotations_resolved") or []:
            if isinstance(s, int):
                resolved.add(s)
    out = []
    for ev in events:
        if ev.get("type") != "unidentified_attendee_observed":
            continue
        seq = ev.get("seq")
        if isinstance(seq, int) and seq in resolved:
            continue
        out.append(ev)
    return out


def count_open_annotations(workspace_root) -> int:
    """The staff meeting's ONE count line source (§0-4): 'N unnamed speakers
    pending identification — resolving against calendars.' Drop-empty."""
    return len(load_open_annotations(workspace_root))


# ---------------------------------------------------------------------------
# The plan (pure classify — no writes)
# ---------------------------------------------------------------------------

def plan_reconcile(workspace_root, *, now_iso: Optional[str] = None,
                   window: Optional[dict] = None) -> dict:
    """Classify every open person proposal into the execution plan. Pure
    over the substrate — no writes. Reads WITHOUT suppress_on_file (like the
    sweep: on-file collisions must be SEEN to be resolved, not re-hidden).

    `window` (cr1#665): a maintenance window dict (`daily_window`). Given,
    this is the DAILY plan: only the clusters a row captured in the window
    touches, no aged defaults, no FS-17 expiry, no record scans. Omitted, the
    plan is the weekly one, byte for byte."""
    from brain_proposals import (PERSON_LOW_CONTEXT_STALE_DAYS,
                                 person_proposal_is_low_context)
    from confirm_flow import load_open_person_proposals, question_clock
    from event_time import parse_ts

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    now = parse_ts(now_iso)

    # Honor the ACTIVE dismissal set (review F-2 posture, verbatim from the
    # sweep): a snoozed identity is never adjudicated mid-snooze — a cluster
    # containing ANY snoozed row is wholly kept open.
    snoozed_seqs: set = set()
    snoozed_fps: set = set()
    try:
        import event_refs
        from mute_ledger import active_dismissal_target_ids

        events = event_refs.load_events(_events_path(ws)) \
            if _events_path(ws).exists() else []
        for tid in active_dismissal_target_ids(events, now_iso):
            t = str(tid)
            if t.startswith("person:fp:"):
                snoozed_fps.add(t.split("person:fp:", 1)[1])
                continue
            if t.startswith("person:"):
                t = t.split(":", 1)[1]
            if t.isdigit():
                snoozed_seqs.add(int(t))
    except Exception:
        snoozed_seqs, snoozed_fps = set(), set()

    rows = load_open_person_proposals(_events_path(ws))
    view = cluster_open_proposals(rows)
    daily = window is not None
    if daily:
        view = _restrict_view_to_window(view, _window_start(window))

    plan: dict = {"auto": [], "confirm": [], "merge_propose": [],
                  "annotations": [], "expire": [], "keep_open": [],
                  # IDENT1 rule 3 — questions past their fourteen days, with
                  # the default each one resolves to. ADDITIVE: an aged row
                  # still sits in the bucket its tier put it in, so every
                  # reader of `confirm` / `merge_propose` sees exactly what
                  # it saw before. `run_identity_reconcile` is what drains.
                  "aged_default": [],
                  "updates": view["updates"], "now_iso": now_iso}
    if daily:
        plan["daily"] = True
        plan["maintenance_window"] = window

    for row in view["nameless"]:
        if row.get("seq") in snoozed_seqs or \
                (row.get("fingerprint") in snoozed_fps):
            plan["keep_open"].append({"cluster": None, "proposal": row,
                                      "why": "snoozed by the user — the mute "
                                             "is honored"})
            continue
        plan["annotations"].append({
            "proposal": row,
            "why": "no name captured — converts to an unnamed-attendee "
                   "annotation (D5); never a person row"})

    for cluster in view["clusters"]:
        if any(s in snoozed_seqs for s in cluster["seqs"]) or \
                any(f in snoozed_fps for f in cluster["fingerprints"]):
            plan["keep_open"].append({"cluster": cluster,
                                      "why": "snoozed by the user — the "
                                             "reconciler never adjudicates "
                                             "a snoozed identity"})
            continue
        cls = classify_cluster(ws, cluster)
        # IDENT1 rule 3 — decide the aged default BEFORE the tier routing
        # below, and record it beside the row rather than instead of it.
        # Weekly only (cr1#665): a fourteen-day clock is the deep pass's.
        if cls["tier"] in ("confirm", "merge_propose") and not daily:
            aged = identity_aged_default(
                ws, cluster, cls.get("matched_person"),
                age_days=identity_question_age_days(cluster, now),
                email=cls.get("email"))
            if aged is not None:
                aged["row_id"] = cluster["row_id"]
                aged["tier"] = cls["tier"]
                aged["email"] = cls.get("email")
                plan["aged_default"].append(aged)
        if cls["tier"] == "merge_propose":
            email = cls["email"]
            matched = cls["matched_person"]
            exact_email = bool(
                cls.get("silent_link_ok")
                and email and matched and not is_role_address(email)
                and email.strip().lower() in {
                    e.strip().lower() for e in _record_emails(matched)})
            plan["merge_propose"].append({
                "cluster": cluster, "matched": matched, "email": email,
                "exact_email": exact_email, "why": cls["why"]})
            continue
        # FS-17 expiry (kept from the sweep): an AGED name-only single
        # mention expires instead of queueing forever. Only single-row
        # clusters — a second mention is corroboration, not staleness.
        # Weekly only (cr1#665).
        if cls["tier"] == "confirm" and len(cluster["rows"]) == 1 \
                and not daily:
            row = cluster["rows"][0]
            if person_proposal_is_low_context(row):
                opened = parse_ts(question_clock(row))
                if opened is not None and now is not None and \
                        (now - opened).days > PERSON_LOW_CONTEXT_STALE_DAYS:
                    plan["expire"].append({
                        "cluster": cluster, "proposal": row,
                        "why": f"name-only mention, "
                               f"{(now - opened).days} days old (window "
                               f"{PERSON_LOW_CONTEXT_STALE_DAYS}d)"})
                    continue
        plan[cls["tier"]].append({"cluster": cluster, "email": cls["email"],
                                  "why": cls["why"]})

    if daily:
        # cr1#665: the scans over EXISTING records are the weekly deep
        # pass's; the daily plan judges only what the window brought in.
        plan["rename_suspects"] = []
        plan["merge_suspects"] = []
        return plan

    # cr1#195 - an empty shell beside a misnamed record is the rename
    # question, so that pair is never ALSO offered as a plain merge: the merge
    # keeps the richer record's name, which is exactly the wrong one here.
    plan["rename_suspects"] = scan_rename_suspects(ws)
    shell_pairs = {frozenset((s["person_id"], s["shell_id"]))
                   for s in plan["rename_suspects"]}
    plan["merge_suspects"] = [
        s for s in scan_existing_duplicates(ws)
        if frozenset((s["keep"].get("id"), s["duplicate"].get("id")))
        not in shell_pairs]
    return plan


# ---------------------------------------------------------------------------
# cr1#195 - an empty shell beside a misnamed record
# ---------------------------------------------------------------------------

def _split_born_ids(events: list) -> set:
    """Person ids a split minted. The split stamps the `person_split` change
    class on the one row that creates the record and on nothing else, so the
    class alone names them (no type string is read here)."""
    out = set()
    for ev in events:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if d.get("brain_change_class") == "person_split" and d.get("person_id"):
            out.add(d["person_id"])
    return out


def _referenced_outside_lifecycle(events: list) -> set:
    """Person ids any row other than the record's own `person_*` lifecycle
    rows points at. A record outside this set has no history of its own."""
    import event_refs

    out = set()
    for ev in events:
        if str(ev.get("type") or "").startswith("person_"):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        out |= event_refs.persons_of(ev)
        cp = d.get("counterparty_id")
        if isinstance(cp, str) and cp:
            out.add(cp)
    return out


def record_history_names(workspace_root, person_id: str) -> list[str]:
    """The person names the ledger OBSERVED on rows bound to this record.

    A row counts only when it binds the record and names the person in the
    same slot: `counterparty_id` with `counterparty_name`, which is how a
    commitment carries the person as heard. The record's own lifecycle rows
    (`person_*`) copy the record's name and are no evidence about it, so they
    never carry that pair. Never raises: an unreadable ledger is no history.
    """
    try:
        import event_refs
        events = event_refs.load_events(_events_path(workspace_root))
    except Exception:
        return []
    out: list[str] = []
    for ev in events:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if d.get("counterparty_id") != person_id:
            continue
        name = d.get("counterparty_name")
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
    return out


def name_belongs_to_other(workspace_root, record: dict,
                          incoming_name: str) -> bool:
    """Does this record's own history belong to `incoming_name` rather than
    to the name the record carries?

    True only when the history names the incoming person AND never names the
    record by any name it answers to. A history name answers to the record
    when its words are all words of one of the record's names
    (`get_person_display_names`): the full name, an alias, a nickname, or
    any part of one, so "Bo" on "Bo Sample" is the record's own history.
    That errs toward OWN on purpose (a bare shared surname counts as own
    too): a wrong "own" only means the split behaves as before, a wrong
    "other" asks the owner a question with no basis. Mixed or absent history
    says nothing. This only ever decides whether to ASK; the owner's
    confirmation is the act.
    """
    from people_writer import _normalize_name, get_person_display_names

    seen = {_normalize_name(n) for n in
            record_history_names(workspace_root, record.get("id") or "")}
    seen.discard("")
    if _normalize_name(incoming_name) not in seen:
        return False
    own_words = [set(_normalize_name(n).split())
                 for n in get_person_display_names(record)]
    own_words = [w for w in own_words if w]
    return not any(set(n.split()) <= w for n in seen for w in own_words)


def shell_is_empty(workspace_root, shell_id: str) -> bool:
    """Is this record still an empty shell: minted by a split, and pointed at
    by no row outside its own `person_*` lifecycle? Read at the moment of the
    owner's click by `people_writer.confirm_person_rename`. An unreadable
    ledger is NOT empty: the fold that follows is permanent."""
    import event_refs

    try:
        events = event_refs.load_events(_events_path(workspace_root))
    except Exception:
        return False
    return shell_id in _split_born_ids(events)         and shell_id not in _referenced_outside_lifecycle(events)


def scan_rename_suspects(workspace_root) -> list[dict]:
    """Empty shells a split minted beside a record whose own history is the
    shell's person's.

    A SHELL is a live record a split created (`person_split` class) that no
    row outside its own lifecycle has ever pointed at. Its NEIGHBOUR is a live
    record holding the same personal address. The pair is a suspect when the
    neighbour's history names the shell's person and never the neighbour's
    own names (`name_belongs_to_other`, the split's own rule).
    PROPOSE-only input, like `scan_existing_duplicates`: nothing here writes.
    Never raises; an unreadable substrate is no suspects.
    """
    from entities_io import entities_collection
    import event_refs
    from people_writer import get_person_emails

    try:
        data = json.loads((Path(workspace_root) / "_hq" / "data" /
                           "entities.json").read_text(encoding="utf-8"))
        people = [p for p in entities_collection(data, "people")
                  if p.get("status") != "archived"]
        events = event_refs.load_events(_events_path(workspace_root))
    except Exception:
        return []
    born = _split_born_ids(events)
    busy = _referenced_outside_lifecycle(events)
    out: list[dict] = []
    seen: set = set()
    for shell in people:
        sid = shell.get("id")
        if sid not in born or sid in busy:
            continue
        name = str(shell.get("canonical_name") or "").strip()
        if not name:
            continue
        for e in get_person_emails(shell):
            e = e.strip().lower()
            if not e or is_role_address(e):
                continue
            for other in people:
                oid = other.get("id")
                if oid == sid or (oid, sid) in seen:
                    continue
                if e not in {x.strip().lower()
                             for x in get_person_emails(other)}:
                    continue
                try:
                    hit = name_belongs_to_other(workspace_root, other, name)
                except Exception:
                    hit = False
                if not hit:
                    continue
                seen.add((oid, sid))
                out.append({
                    "person_id": oid,
                    "current_name": other.get("canonical_name") or "",
                    "new_name": name, "shell_id": sid, "email": e,
                    "record": other,
                    "why": f"{name} has an empty record, and the record "
                           f"named {other.get('canonical_name')} at {e} "
                           f"holds {name}'s history"})
    return out


def _record_emails(record: dict) -> list[str]:
    from people_writer import get_person_emails

    try:
        return get_person_emails(record)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Execution (D7 — one entry point, two cap sets)
# ---------------------------------------------------------------------------

def run_identity_reconcile(
    workspace_root,
    *,
    apply: bool = False,
    caps: Optional[dict] = None,
    now_iso: Optional[str] = None,
    exact_email_autolink: bool = True,
    fired_via=None,
    triggered_by: Optional[str] = None,
    source_skill: str = "identity-reconcile",
    window: Optional[dict] = None,
) -> dict:
    """Plan and (with apply=True) execute the reconcile pass.

    `window` (cr1#665): a maintenance window dict (`daily_window`). Given,
    this is the DAILY people update: `plan_reconcile`'s daily plan, the same
    writers and caps below, no nickname-collision rule and no annotation-
    resolution join, and ONE `pack_run` receipt under `DAILY_TASK_ID` naming
    the window. Omitted, the weekly pass runs exactly as before.

    The steps:

      - AUTO clusters   → `auto_add_person` on the existing R1 rail; every
                          member proposal tombstoned `person_added`, stamped
                          `brain_batch_id` + `brain_change_class` so ONE
                          `brain_undo.undo_batch` archives the adds and
                          reopens the expiry/annotation tombstones.
      - exact-email     → silent `same_as` (alias + tombstones, narrated in
        on-file matches   CHANGED via the receipt) — §0-2 YES ruling, with
                          the role-address guard; everything else on file
                          becomes a capped merge-propose row (kind
                          `person_link` on the bp rail).
      - dup suspects    → capped `person_merge` bp proposals (D4b). NO code
                          path merges records — `merge person records` is a
                          user click, forever.
      - nameless rows   → `unidentified_attendee_observed` annotation + a
                          reopenable tombstone (D5).
      - annotations     → resolved when their observed address now matches an
                          existing record (Tier-1 email — the substrate-side
                          join; calendar/Granola joins are capture-side D10).
      - aged low-ctx    → expire tombstone (FS-17, kept from the sweep).

    Ends with ONE `identity_reconcile_run` receipt (also the job's due-ness
    signal and the D6 CHANGED-narration source). Honesty rule: every receipt
    count comes from what was ACTUALLY written, never from the plan.
    """
    # FIX3 F3-6: a literal default IS an explicit value by the time the
    # resolver sees it (the FIX2 M-3 lesson), so this signature says
    # nothing and the seat answers. A legacy or local seat still reads
    # `scheduled`, byte for byte; a merged seat with nothing forwarded
    # reads `manual`, which is what a typed brief actually is.
    fired_via = _effective_fired_via(fired_via)
    ws = Path(workspace_root)
    caps = dict(caps or STEADY_CAPS)
    now_iso = now_iso or _now_iso()
    daily = window is not None
    plan = (plan_reconcile(ws, now_iso=now_iso, window=window) if daily
            else plan_reconcile(ws, now_iso=now_iso))
    batch_id = "idr_" + _clock_now(workspace_root).strftime(
        "%Y%m%dT%H%M%SZ")
    plan["batch_id"] = batch_id
    plan["caps"] = caps
    if not apply:
        plan["applied"] = False
        return plan

    from event_gate import append_event
    from confirm_flow import build_person_proposal_resolved_event
    from people_writer import add_person_alias, auto_add_person

    events_path = _events_path(ws)
    results: dict = {"added": [], "needs_confirm": [], "linked": [],
                     "auto_linked": [], "already_on_file": [],
                     "merge_rows_proposed": 0, "annotations": [],
                     "annotations_resolved": [], "expired": [], "errors": [],
                     # IDENT1 rule 3 — the two aged outcomes counted apart,
                     # because "we linked 25 for you" and "we stopped asking
                     # about 60" are different sentences to a customer.
                     "aged_linked": [], "aged_let_go": [], "aliases_dropped": [],
                     "spilled": {"auto_add": 0, "merge_propose": 0,
                                 "aged_default": 0}}

    def _tombstone(cluster_or_row, *, resolution, person_id=None, alias=None,
                   note="", change_class="person_proposal_tombstone",
                   extra_data=None):
        """One tombstone per member proposal (int seq or D8 fingerprint),
        batch-stamped for undo. `extra_data` (UXR1 D3) rides on every member
        tombstone — the person_link reverser's re-propose payload. Returns
        the events appended."""
        if isinstance(cluster_or_row, dict) and "rows" in cluster_or_row:
            members = cluster_or_row["rows"]
        else:
            members = [cluster_or_row]
        evs = []
        for row in members:
            seq = row.get("seq")
            kwargs = {}
            if not (isinstance(seq, int) and not isinstance(seq, bool)):
                seq = None
                kwargs["proposal_fingerprint"] = row.get("fingerprint")
            tomb = build_person_proposal_resolved_event(
                seq, resolution=resolution, source_skill=source_skill,
                person_id=person_id, alias=alias, note=note, **kwargs)
            tomb["data"]["brain_batch_id"] = batch_id
            tomb["data"]["brain_change_class"] = change_class
            if person_id:
                tomb["data"]["person_id"] = person_id
            if isinstance(extra_data, dict):
                for k, v in extra_data.items():
                    tomb["data"].setdefault(k, v)
            evs.append(tomb)
        if evs:
            append_event(events_path, evs, holder=source_skill)
        return evs

    # ---- AUTO tier (capped, spill narrated) --------------------------------
    for entry in plan["auto"]:
        cluster = entry["cluster"]
        if len(results["added"]) >= int(caps.get("auto_add", 0)):
            results["spilled"]["auto_add"] += 1
            plan["keep_open"].append({"cluster": cluster,
                                      "why": "auto cap reached — spilled to "
                                             "the review pile (§0-3, "
                                             "narrated never silent)"})
            continue
        email = entry["email"]
        _e, src_row = cluster_observed_email(cluster) if email else (None, None)
        try:
            res = auto_add_person(
                ws,
                canonical_name=cluster["name"],
                email=email,
                email_provenance=({"via": "person_proposal",
                                   "proposal_seq": (src_row or {}).get("seq"),
                                   "source_ref": (src_row or {}).get("source_ref")}
                                  if email else None),
                source_skill=source_skill,
                role=cluster.get("inferred_role") or None,
                source_identifiers=cluster_source_identifiers(cluster, ws),
            )
        except Exception as exc:  # loud per-item, contained per-batch
            results["errors"].append({"row_id": cluster["row_id"],
                                      "error": f"{type(exc).__name__}: {exc}"})
            continue
        if res.get("status") == "needs_confirm":
            # Same-name gate fired at write time (defense-in-depth) — the
            # cluster DEMOTES to confirm, never forks.
            results["needs_confirm"].append({"row_id": cluster["row_id"],
                                             "name": cluster["name"]})
            continue
        record = res["record"]
        _tombstone(cluster, resolution="person_added",
                   person_id=record["id"],
                   note=f"identity reconcile {batch_id} — {entry['why']}",
                   change_class="person_org_creation_structured_fact")
        results["added"].append({
            "row_id": cluster["row_id"], "name": cluster["name"],
            "person_id": record["id"], "email": email,
            "n_proposals": len(cluster["rows"]),
            "email_dropped_no_provenance":
                res.get("email_dropped_no_provenance")})

    # ---- MERGE-PROPOSE tier -------------------------------------------------
    merge_cap = int(caps.get("merge_propose", 0))
    # AUTOAPPLY §4a consequence rule — records linked in THIS run, and the
    # clusters still open at the end of the loop (the sweep below pairs them).
    auto_linked_records: dict = {}
    still_open_clusters: list = [e["cluster"] for e in plan["confirm"]]
    pending_links: list = []
    for entry in plan["merge_propose"]:
        cluster, matched = entry["cluster"], entry["matched"]
        if exact_email_autolink and entry["exact_email"] and matched:
            # §0-2 ruling: exact-email matches resolve silently — narrated in
            # CHANGED via the receipt, reopenable via the tombstone reverser.
            try:
                if _norm_name(cluster["name"]) != \
                        _norm_name(matched.get("canonical_name")):
                    # cr1#768: nobody asked for this alias, so it declares
                    # nothing and the writer refuses a name on file. The plan
                    # has already routed one away; this is the same gate at
                    # write time.
                    add_person_alias(ws, matched["id"], cluster["name"],
                                     source_skill=source_skill)
            except Exception as exc:
                # An alias mapped to a DIFFERENT person (or any write error)
                # is a human decision — demote to a merge-propose row.
                results["errors"].append({"row_id": cluster["row_id"],
                                          "error": f"{type(exc).__name__}: {exc}"})
                entry = dict(entry, exact_email=False)
            else:
                _tombstone(cluster, resolution="same_as",
                           person_id=matched["id"], alias=cluster["name"],
                           note=f"identity reconcile {batch_id} — exact-email "
                                "link (§0-2)")
                results["linked"].append({"row_id": cluster["row_id"],
                                          "name": cluster["name"],
                                          "person_id": matched["id"],
                                          "email": entry["email"]})
                continue
        # UXR1 D3 (M ruling 2026-07-21) — the OBVIOUS link auto-applies
        # instead of asking: exact-unique-clean per gate (a)-(d). The LB2
        # auto lifecycle contract holds — propose(tier="auto") + apply +
        # resolve in the SAME run; the auto proposal never rests open. A
        # non-"proposed" status (open confirm row after an undo, or the
        # 60d decline cooldown) falls through to the confirm row path —
        # the human's standing answer is never steamrolled.
        if matched is not None:
            eligible, why = auto_link_eligible(ws, cluster, matched,
                                               entry["email"])
            if eligible and _auto_apply_person_link(
                    ws, cluster, matched, entry, why, batch_id,
                    _tombstone, results, source_skill):
                if matched.get("id"):
                    auto_linked_records[matched["id"]] = matched
                continue
        # NOT proposed yet — the §4a sweep below runs FIRST. Proposing here
        # would mint a confirm row for a cluster the sweep is about to
        # resolve, leaving an orphaned ask pointing at a closed proposal.
        pending_links.append(entry)

    # ---- AUTOAPPLY §4a consequence rule: post-link already_on_file ---------
    # M's complaint was "duplicates asking to confirm" — the SAME person
    # rendered as two rows. Where both rows come from ONE cluster the
    # tombstone fan-out above already collapses them. This closes the other
    # half: a SEPARATE add cluster (a different captured spelling of the same
    # person — the typo'd mention gate (b) cannot token-match) that the link
    # just answered, which would otherwise keep asking to add someone now
    # demonstrably on file.
    #
    # The bar is deliberately the SAME evidence the link itself required — an
    # id-level address on the record, or an exact normalized full-name match.
    # No new evidence class is admitted here, so this adds no new risk: it
    # only stops re-asking a question already answered in this run.
    swept: set = set()
    for cluster in still_open_clusters + [e["cluster"] for e in pending_links]:
        resolved = _post_link_already_on_file(ws, cluster, auto_linked_records)
        if resolved is None:
            continue
        record, why = resolved
        try:
            # `same_as` is the shipped resolution for "this mention IS that
            # record" (PROPOSAL_RESOLUTIONS has no already_on_file member, and
            # inventing one would be a shared-vocabulary change for a local
            # need). No alias is passed — see _auto_apply_person_link on why
            # the auto rail stays alias-free. change_class keeps the
            # registered person_proposal_tombstone reverser, batch-stamped, so
            # `undo` reopens these rows with the rest of the batch.
            _tombstone(cluster, resolution="same_as",
                       person_id=record.get("id"),
                       note=f"identity reconcile {batch_id} — {why}",
                       change_class="person_proposal_tombstone")
            swept.add(cluster["row_id"])
            results["already_on_file"].append({
                "row_id": cluster["row_id"], "name": cluster.get("name"),
                "person_id": record.get("id"), "why": why})
        except Exception as exc:  # loud per-item, contained per-batch
            results["errors"].append({"row_id": cluster["row_id"],
                                      "error": f"{type(exc).__name__}: {exc}"})

    # ---- IDENT1 rule 4: nicknames that name two people ---------------------
    # Runs before the aged defaults on purpose: an ambiguous spelling is a
    # resolution shortcut, and a default that resolves a name THROUGH one
    # would inherit its ambiguity. A whole-book rule: weekly only (cr1#665).
    if not daily:
        try:
            results["aliases_dropped"] = apply_alias_collisions(
                ws, batch_id=batch_id, source_skill=source_skill)
        except Exception as exc:  # loud, contained
            results["errors"].append({"row_id": "alias_collisions",
                                      "error": f"{type(exc).__name__}: {exc}"})

    # ---- IDENT1 rule 3: the aged defaults ----------------------------------
    # Runs AFTER the auto-link rail (an eligible link belongs to its own lane,
    # with its own predicate on the event) and BEFORE the confirm rows are
    # minted below — otherwise the pass would mint an ask for a question it is
    # about to answer, which is the exact orphan the §4a sweep exists to stop.
    aged_done: set = set()
    for entry in plan["aged_default"]:
        cluster = entry["cluster"]
        row_id = cluster["row_id"]
        if row_id in swept or row_id in aged_done:
            continue
        if entry["default"] == IDENTITY_DEFAULT_KEEP_OPEN:
            plan["keep_open"].append({"cluster": cluster, "why": entry["why"]})
            continue
        if (len(results["aged_linked"]) + len(results["aged_let_go"])
                >= int(caps.get("aged_default", 0))):
            results["spilled"]["aged_default"] += 1
            plan["keep_open"].append({
                "cluster": cluster,
                "why": "aged-default cap reached — the rest drain on the "
                       "next run (§0-3, narrated never silent)"})
            continue
        matched = entry.get("matched")
        if entry["default"] == IDENTITY_DEFAULT_LINK and matched is not None:
            # The SAME rail an eligible auto-link uses: propose(tier="auto")
            # + apply + resolve, `person_link` change class, so the
            # registered reverser reopens the mention AND hands the decision
            # back as a confirm row. A default that could not be taken back
            # would not be a default, it would be a verdict.
            if _auto_apply_person_link(ws, cluster, matched, entry,
                                       entry["why"], batch_id, _tombstone,
                                       results, source_skill):
                aged_done.add(row_id)
                results["aged_linked"].append({
                    "row_id": row_id, "name": cluster.get("name"),
                    "person_id": matched.get("id"),
                    "age_days": entry.get("age_days"), "why": entry["why"]})
                if matched.get("id"):
                    auto_linked_records[matched["id"]] = matched
                continue
            # The link would not apply (a standing human answer, a decline
            # cooldown). Falling through to LET GO would drop a row the
            # customer has already spoken about, so it keeps its question.
            plan["keep_open"].append({
                "cluster": cluster,
                "why": "an aged default tried the likeliest match and the "
                       "link rail declined — a standing human answer is "
                       "never steamrolled by a clock"})
            continue
        try:
            _tombstone(cluster, resolution="not_relevant",
                       note=f"identity reconcile {batch_id} — aged default: "
                            f"{entry['why']}",
                       change_class="person_proposal_tombstone")
        except Exception as exc:  # loud per-item, contained per-batch
            results["errors"].append({"row_id": row_id,
                                      "error": f"{type(exc).__name__}: {exc}"})
            continue
        aged_done.add(row_id)
        results["aged_let_go"].append({
            "row_id": row_id, "name": cluster.get("name"),
            "age_days": entry.get("age_days"), "why": entry["why"]})

    # ---- deferred confirm rows for everything the sweep left open ----------
    from confirm_flow import may_surface
    from people_writer import held_question_answer
    today = _localize_date(plan["now_iso"], str(ws))
    for entry in pending_links:
        cluster, matched = entry["cluster"], entry["matched"]
        if cluster["row_id"] in swept or cluster["row_id"] in aged_done:
            continue
        # cr1#824: the owner already answered this pair on the writer's row,
        # which holds no sighting, so the answer closes this one and nobody
        # is asked twice.
        said = held_question_answer(ws, cluster["name"], (matched or {}).get("id"))
        if said:
            _tombstone(cluster, person_id=matched.get("id") if said == "yes" else None,
                       resolution="same_as" if said == "yes" else "not_relevant",
                       note=f"identity reconcile {batch_id} - answered on the held row")
            results.setdefault("held_answered", []).append(
                {"row_id": cluster["row_id"], "name": cluster["name"], "answer": said})
            continue
        # cr1#773: a link row is a question, so a name held for a later day
        # is not asked about here. Its day's pass asks (`_captured_since`).
        if not any(may_surface(r.get("surface_on"), today)
                   for r in cluster["rows"]):
            plan["keep_open"].append({"cluster": cluster,
                                      "why": "waiting for its day (cr1#773)"})
            continue
        if results["merge_rows_proposed"] >= merge_cap:
            results["spilled"]["merge_propose"] += 1
            continue
        if _propose_person_link(ws, cluster, matched, source_skill):
            results["merge_rows_proposed"] += 1

    # ---- cr1#195 an empty shell beside a misnamed record ------------------
    # Asked, never applied: the owner's confirm is
    # `people_writer.confirm_person_rename`.
    # Rides the merge-propose cap, since it is the same kind of question.
    for suspect in plan.get("rename_suspects") or []:
        if results["merge_rows_proposed"] >= merge_cap:
            results["spilled"]["merge_propose"] += 1
            continue
        try:
            from people_writer import propose_person_rename

            asked = propose_person_rename(
                ws, record=suspect["record"], new_name=suspect["new_name"],
                shared_email=suspect.get("email") or "",
                shell_id=suspect["shell_id"], evidence=suspect.get("why", ""))
        except Exception as exc:
            results["errors"].append({"person_id": suspect.get("person_id"),
                                      "error": f"{type(exc).__name__}: {exc}"})
            continue
        if asked.get("status") == "proposed":
            results["merge_rows_proposed"] += 1

    # ---- D4b existing-record duplicate suspects ----------------------------
    for suspect in plan["merge_suspects"]:
        if results["merge_rows_proposed"] >= merge_cap:
            results["spilled"]["merge_propose"] += 1
            continue
        if _propose_person_merge(ws, suspect, source_skill):
            results["merge_rows_proposed"] += 1

    # ---- Annotations (D5) ---------------------------------------------------
    # Second-eyes F3 (2026-07-19): conversion is IDEMPOTENT per
    # (meeting_source_ref, attendee_hint). Undoing a conversion batch reopens
    # the proposal tombstone but the annotation event is immutable history —
    # without this guard the next run would mint a duplicate annotation and
    # the staff-meeting count line would double-count one speaker.
    _open_ann_keys = {
        ((a.get("data") or {}).get("meeting_source_ref"),
         (a.get("data") or {}).get("attendee_hint"))
        for a in load_open_annotations(ws)}
    for entry in plan["annotations"]:
        row = entry["proposal"]
        try:
            from meeting_capture import build_unidentified_attendee_event
            from person_backlog_sweep import _observed_email

            hint = (row.get("review_reason") or row.get("evidence")
                    or "unidentified attendee")
            ev = build_unidentified_attendee_event(
                row.get("source_ref") or "unknown",
                attendee_hint=hint[:120],
                attendee_email=_observed_email(row),
                source_skill=source_skill,
            )
            key = (ev["data"]["meeting_source_ref"],
                   ev["data"]["attendee_hint"])
            if key not in _open_ann_keys:
                append_event(events_path, [ev], holder=source_skill)
                _open_ann_keys.add(key)
            _tombstone(row, resolution="not_relevant",
                       note=f"identity reconcile {batch_id} — converted to "
                            "an unnamed-attendee annotation (D5)")
            results["annotations"].append({"seq": row.get("seq"),
                                           "source_ref": row.get("source_ref")})
        except Exception as exc:
            results["errors"].append({"seq": row.get("seq"),
                                      "error": f"{type(exc).__name__}: {exc}"})

    # ---- Annotation resolution (substrate-side join) -----------------------
    # A whole-book join, and the open-annotation reader only reads the
    # weekly receipt's resolved list: weekly only (cr1#665).
    for ann in ([] if daily else load_open_annotations(ws)):
        data = ann.get("data") if isinstance(ann.get("data"), dict) else {}
        email = (data.get("attendee_email") or "").strip()
        if not email:
            continue
        try:
            from people_writer import find_existing_person

            if find_existing_person(ws, email=email) is not None:
                results["annotations_resolved"].append(ann.get("seq"))
        except Exception:
            continue  # ambiguity/error → the annotation stays open

    # ---- FS-17 expiry -------------------------------------------------------
    for entry in plan["expire"]:
        row = entry["proposal"]
        try:
            _tombstone(row, resolution="not_relevant",
                       note=f"expired by identity reconcile {batch_id} — "
                            f"{entry['why']}")
            results["expired"].append({"seq": row.get("seq"),
                                       "name": row.get("name")})
        except Exception as exc:
            results["errors"].append({"seq": row.get("seq"),
                                      "error": f"{type(exc).__name__}: {exc}"})

    # ---- ONE receipt (honesty: counts from what was WRITTEN) ---------------
    from receipts import log_receipt

    receipt_data = {
        "batch_id": batch_id,
        "n_auto_added": len(results["added"]),
        "n_linked": len(results["linked"]),
        # UXR1 D3 — the auto-link lane's own count (distinct from the
        # §0-2 exact-email n_linked): change_feed narrates it with the
        # undo affordance.
        "n_auto_linked": len(results["auto_linked"]),
        "people_auto_linked": [{"person_id": a["person_id"],
                                "name": a["name"]}
                               for a in results["auto_linked"]],
        "n_merge_proposed": results["merge_rows_proposed"],
        # cr1#773: a name still waiting for its day is not a question yet.
        "n_clustered": sum(
            1 for e in plan["confirm"]
            if any(may_surface(r.get("surface_on"), today)
                   for r in e["cluster"]["rows"])),
        "n_annotations": len(results["annotations"]),
        "n_annotations_resolved": len(results["annotations_resolved"]),
        "n_expired": len(results["expired"]),
        # IDENT1 rule 3 — what the fourteen-day default did, counted from
        # what was WRITTEN (the honesty rule), never from the plan.
        "n_aged_linked": len(results["aged_linked"]),
        "n_aged_let_go": len(results["aged_let_go"]),
        # IDENT1 rule 4 — nicknames the collision rule took back.
        "n_aliases_dropped": len(results.get("aliases_dropped") or []),
        "identity_question_ttl_days": IDENTITY_QUESTION_TTL_DAYS,
        "n_kept_open": len(plan["keep_open"]),
        "n_errors": len(results["errors"]),
        "caps": caps,
        "spilled": results["spilled"],
        "people_added": [{"person_id": a["person_id"], "name": a["name"]}
                         for a in results["added"]],
        "annotations_resolved": [s for s in results["annotations_resolved"]
                                 if isinstance(s, int)],
    }
    if daily:
        # cr1#665: the daily people update's own receipt. A `pack_run` under
        # its own task id (see DAILY_TASK_ID for why never the weekly type),
        # naming the window it read, so a two-day gap says two days.
        from catchup import MAINTENANCE_WINDOW_FIELD, WINDOW_INCOMPLETE_FIELD

        receipt_data["mode"] = "daily"
        receipt_data[MAINTENANCE_WINDOW_FIELD] = window
        # A capped run left clusters unjudged inside the window: the batch-cap
        # marker sends the next daily run back over the same window (a
        # re-read is idempotent: what this run added or linked is no longer
        # an open proposal) instead of leaving the spill to Sunday.
        marker = _daily_spill_marker(window, any(results["spilled"].values()))
        if marker:
            receipt_data[WINDOW_INCOMPLETE_FIELD] = marker
        log_receipt(
            ws, DAILY_TASK_ID,
            receipt_type=DAILY_RECEIPT_TYPE,
            fired_via=fired_via,
            triggered_by=triggered_by,
            extra_data=receipt_data,
        )
    else:
        log_receipt(
            ws, "identity-reconcile",
            receipt_type="identity_reconcile_run",
            fired_via=fired_via,
            # FIX3 F3-6, fix pass 2 (review N-1). WHICH surface asked. The
            # argument wins; `log_receipt` falls back to `CR_TRIGGERED_BY`, so
            # a merged seat that forwarded the variable is covered either way,
            # and a run nobody asked for writes no key at all.
            triggered_by=triggered_by,
            extra_data=receipt_data,
        )
    plan["applied"] = True
    plan["results"] = results
    return plan


def _post_link_already_on_file(ws, cluster: dict, linked: dict):
    """AUTOAPPLY §4a consequence rule — `(record, why)` when this still-open
    add cluster is answered by a link applied EARLIER IN THE SAME RUN, else
    None.

    WHY THIS IS NOT `person_proposal_already_on_file` (a deliberate deviation
    from SPEC §4a's stated mechanism): that predicate delegates to
    `confirm_flow.person_name_on_file`, which is confident-matches-only and
    returns False for a lone first name — the Bug #19 discipline. Loosening
    it to catch the lone-first-name case would change the answer for every
    OTHER caller too (the shared queue loader's suppress_on_file, the morning
    brief, the commitments chat), silently hiding lone-first-name add rows
    across three surfaces on evidence none of them has seen. The re-ask this
    rule kills is local to the reconcile run, so the fix is local to it.

    Two bars, both exactly what `auto_link_eligible` already demanded of the
    link itself — no new evidence class is admitted:
      * an observed address on the cluster that belongs to exactly ONE on-file
        record, and that record is the one just linked (role addresses out);
      * an exact normalized multi-token full-name match to that record.
    Anything weaker keeps asking. Fail-safe: any error returns None."""
    if not linked:
        return None
    name = (cluster.get("name") or "").strip()
    if not name or _NAME_ANNOTATION_RE.search(name):
        return None
    try:
        from person_backlog_sweep import _observed_email

        for row in cluster.get("add_rows") or []:
            probe = dict(row)
            probe["name"] = name
            addr = _observed_email(probe)
            if not addr or is_role_address(addr):
                continue
            sole = sole_record_for_email(ws, addr)
            if sole is not None and sole.get("id") in linked:
                # cr1#768: the address is that record's, the name is somebody
                # else's. The link answered nothing about them.
                if _name_is_someone_elses(ws, name, sole):
                    continue
                return linked[sole["id"]], (
                    f"already on file — {addr.strip().lower()} belongs to the "
                    "record linked earlier in this run")
        for pid, record in linked.items():
            canon = (record or {}).get("canonical_name") or ""
            if canon and _norm_name(name) == _norm_name(canon) \
                    and len(_norm_name(name).split()) >= 2 \
                    and not _cluster_says_another_person(cluster, record):
                return record, ("already on file — exact name match to the "
                                "record linked earlier in this run")
    except Exception:
        return None
    return None


def _auto_apply_person_link(ws, cluster, matched, entry, why, batch_id,
                            tombstone, results, source_skill) -> bool:
    """UXR1 D3 — auto-apply ONE exact-unique-clean person_link on the LB2
    auto rail: propose(tier="auto") + apply + resolve in the SAME run.

    The APPLY is the same_as tombstone fan-out over the cluster's member
    proposals (change_class="person_link", batch-stamped + carrying the
    reverser's re-propose payload).

    NO ALIAS IS EVER WRITTEN HERE. Before AUTOAPPLY §4a the reason was that
    gate (a) required the normalized-exact spelling, so there was nothing to
    save. Gate (a′) admits an email-corroborated link whose spelling DIFFERS
    (a first-name mention onto its full-name record), so that reason is
    gone — but the rule stands
    on a stronger one: an alias is a SECOND mutation, on the record itself,
    and the registered `person_link` reverser does not remove it. Writing
    one would make the auto tier only partly reversible, which §3 forbids
    outright. The cost is that a future bare-name mention with no address
    asks again — correct, because without the address that mention really is
    ambiguous. (The exact-email §0-2 path keeps its own alias behavior: it is
    reached only when the NAME confidently resolves, a different bar.)

    Returns True when the link applied; False on ANY failure or non-
    "proposed" propose status (open confirm row after an undo; the 60d
    decline cooldown) — the caller falls through to the confirm-row path,
    so a human's standing answer is never steamrolled."""
    from brain_proposals import propose, resolve_proposal

    matched_name = (matched or {}).get("canonical_name") or ""
    matched_id = (matched or {}).get("id")
    fingerprint = f"person_link:{cluster['key']}:{matched_id or 'unresolved'}"
    evidence = (cluster["rows"][0].get("evidence") or
                cluster["rows"][0].get("source_ref") or "")
    try:
        res = propose(
            ws,
            kind="person_link",
            fingerprint=fingerprint,
            evidence=evidence,
            action_tuples=[{"action": "confirm proposal"},
                           {"action": "dismiss proposal"},
                           {"action": "snooze proposal 7d"}],
            tier="auto",
            change_class="person_link",
            detector="identity-reconcile",
            render_line=(f"Linked {cluster['name']} to {matched_name} "
                         f"({why})"),
            person_id=matched_id,
            extra={"title": cluster["name"],
                   "cluster_seqs": list(cluster["seqs"]),
                   "cluster_fingerprints": list(cluster["fingerprints"]),
                   "alias_name": cluster["name"],
                   "matched_name": matched_name},
        )
        if res.get("status") != "proposed":
            return False
        tombstone(cluster, resolution="same_as", person_id=matched_id,
                  alias=cluster["name"],
                  note=f"identity reconcile {batch_id} — auto-link "
                       f"(UXR1 D3: {why})",
                  change_class="person_link",
                  extra_data={"link_fingerprint": fingerprint,
                              "link_evidence": evidence,
                              "matched_name": matched_name,
                              # §7 — WHICH corroboration clause fired, one
                              # string, so a later audit reads the reason off
                              # the event instead of a vanished chat. Derived
                              # by `link_auto_predicate` from the gate's own
                              # reason, so the event and the gate cannot drift
                              # (STAFFCUT added three context clauses; a local
                              # ternary here would have silently mislabelled
                              # every one of them as an exact-name link).
                              "auto_predicate": link_auto_predicate(why)})
        resolve_proposal(ws, res["proposal_id"], "applied",
                         resolved_by=source_skill, source_skill=source_skill)
        results["auto_linked"].append({"row_id": cluster["row_id"],
                                       "name": cluster["name"],
                                       "person_id": matched_id,
                                       "why": why})
        return True
    except Exception as exc:  # loud per-item, contained per-batch
        results["errors"].append({"row_id": cluster["row_id"],
                                  "error": f"{type(exc).__name__}: {exc}"})
        return False


def _link_differentiator(ws, matched) -> str:
    """UXR1 D4 — what makes the on-file record recognizable in one glance:
    its org, else an email, else "last touched {date}", else the honest
    "no details on file". Never empty — a decision row must show what
    changes on a click."""
    org = _record_org_name(ws, matched or {})
    if org:
        return org
    for e in _record_emails(matched or {}):
        e = (e or "").strip()
        if e:
            return e
    rec = matched or {}
    date = (rec.get("last_interaction") or rec.get("last_touched_at")
            or rec.get("first_seen") or "")
    if date:
        from brain_proposals import _short_date

        # TZDATE3 — `ws` (the workspace root already used above by
        # `_record_org_name`) localizes the date through `tz.localize_date`
        # before formatting, so an evening-local touch doesn't read as
        # tomorrow's UTC date. The module-level `_localize_date` fallback
        # (same localizer `_short_date` uses internally) replaces the old
        # raw `str(date)[:10]` slice for the unparseable-Jul-8-format case.
        short = _short_date(str(date), ws)
        if short:
            return f"last touched {short}"
        return f"last touched {_localize_date(str(date), ws)}"
    return "no details on file"


def person_link_ask_line(ws, name: str, matched, evidence: str) -> str:
    """UXR1 D4 — the decision-grade ask for a person_link row that still
    ASKS (it failed the D3 auto gate, or an undo returned the decision to
    the human): where the mention came from (its evidence — on every row,
    never rendered before D4) and which record it would link to (the
    differentiator: org > email > last touched > "no details on file").
    A multi-candidate name names the COUNT and never pre-fills —
    consistent with SPEC_IDM1's daily-rail ruling."""
    matched_name = (matched or {}).get("canonical_name") or "an existing record"
    appeared = (evidence or "").strip() or "a captured mention"
    if (matched or {}).get("id"):
        diff = _link_differentiator(ws, matched)
        # cr1#768: a yes here saves the name onto this record, so the row says
        # whose name it already is. The answer is the owner's to give.
        taken = (f"{name} is already on file as someone else. "
                 if _name_is_someone_elses(ws, name, matched,
                                           if_unreadable=False) else "")
        return (f"'{name}' appeared as {appeared}. {taken}"
                f"Same as {matched_name} ({diff})? — link it?")
    # No single resolved record — count the same-name candidates so the
    # collision is named, never ranked-and-picked (IDM1).
    n = 0
    try:
        from people_writer import list_same_name_people
        n = len(list_same_name_people(ws, name) or [])
    except Exception:
        n = 0
    if n >= 2:
        return (f"'{name}' appeared as {appeared}. "
                f"{n} people named {name} on file — "
                "same as one of them? — link it?")
    return (f"'{name}' appeared as {appeared}. "
            f"Same as {matched_name}? — link it?")


def _propose_person_link(ws, cluster, matched, source_skill) -> bool:
    """One D4a merge-propose row on the bp rail. Adjudicated via the generic
    bp verbs (apply-choices `cr-brain` kind `person_link` handlers).
    Render line: person_link_ask_line (UXR1 D4 — decision-grade)."""
    from brain_proposals import propose

    matched_name = (matched or {}).get("canonical_name") or "an existing record"
    matched_id = (matched or {}).get("id")
    evidence = (cluster["rows"][0].get("evidence") or
                cluster["rows"][0].get("source_ref") or "")
    # cr1#825: the fact behind the question leads the evidence, so a surface
    # that cuts the line keeps it. The id itself means nothing to the owner.
    facts = [f"a different user in your {kind} tool"
             for kind in _kinds_saying_another_person(cluster, matched)]
    evidence = "; ".join(facts + [evidence] if evidence else facts)
    render_line = person_link_ask_line(ws, cluster["name"], matched, evidence)
    try:
        res = propose(
            ws,
            kind="person_link",
            fingerprint=f"person_link:{cluster['key']}:{matched_id or 'unresolved'}",
            evidence=evidence,
            action_tuples=[{"action": "confirm proposal"},
                           {"action": "dismiss proposal"},
                           {"action": "snooze proposal 7d"}],
            tier="confirm",
            detector="identity-reconcile",
            render_line=render_line,
            person_id=matched_id,
            extra={"title": cluster["name"],
                   "cluster_seqs": list(cluster["seqs"]),
                   "cluster_fingerprints": list(cluster["fingerprints"]),
                   "alias_name": cluster["name"],
                   "matched_name": matched_name},
        )
        return res.get("status") == "proposed"
    except Exception:
        return False


def _propose_person_merge(ws, suspect, source_skill) -> bool:
    """One D4b duplicate-suspect row. The `merge person records` verb — a
    USER CLICK in apply-choices, never this module — dispatches the record
    merge (no reverser exists; never in AUTO_ALLOWED)."""
    from brain_proposals import propose

    keep, dup = suspect["keep"], suspect["duplicate"]
    pair = sorted([str(keep.get("id")), str(dup.get("id"))])
    try:
        res = propose(
            ws,
            kind="person_merge",
            fingerprint=f"person_merge:{pair[0]}:{pair[1]}",
            evidence=suspect.get("why") or "",
            action_tuples=[{"action": "merge person records"},
                           {"action": "proposal not relevant"},
                           {"action": "snooze proposal 7d"}],
            tier="confirm",
            detector="identity-reconcile",
            render_line=(f"{keep.get('canonical_name')} and "
                         f"{dup.get('canonical_name')} look like the same "
                         f"person ({suspect.get('why')}) — merge the "
                         "records?"),
            person_id=keep.get("id"),
            extra={"title": f"{keep.get('canonical_name')} / "
                            f"{dup.get('canonical_name')}",
                   "keep_id": keep.get("id"),
                   "duplicate_id": dup.get("id")},
        )
        return res.get("status") == "proposed"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _narrate(plan: dict) -> str:
    lines = []
    mode = "APPLIED" if plan.get("applied") else "DRY RUN — nothing written"
    lines.append(f"Identity reconcile ({mode})")
    if plan.get("daily"):
        win = plan.get("maintenance_window") or {}
        lines.append(f"  daily people update: window since {win.get('since')} "
                     f"({win.get('reason')}, {win.get('days')} days)")
    lines.append(f"  auto-add: {len(plan['auto'])}")
    for e in plan["auto"]:
        lines.append(f"    + {e['cluster']['name']!r} — {e['why']}")
    lines.append(f"  confirm clusters: {len(plan['confirm'])}")
    lines.append(f"  merge-propose (on file): {len(plan['merge_propose'])}")
    for e in plan["merge_propose"]:
        tag = "exact-email link" if e["exact_email"] else "row"
        lines.append(f"    ~ {e['cluster']['name']!r} → {tag}")
    lines.append(f"  duplicate suspects: {len(plan.get('merge_suspects') or [])}")
    lines.append(f"  annotations (no name): {len(plan['annotations'])}")
    lines.append(f"  expire (aged, name-only): {len(plan['expire'])}")
    aged = plan.get("aged_default") or []
    if aged:
        counts: dict = {}
        for e in aged:
            counts[e["default"]] = counts.get(e["default"], 0) + 1
        lines.append(
            f"  aged past {IDENTITY_QUESTION_TTL_DAYS}d (rule 3): "
            f"{counts.get(IDENTITY_DEFAULT_LINK, 0)} to the likeliest match, "
            f"{counts.get(IDENTITY_DEFAULT_LET_GO, 0)} let go, "
            f"{counts.get(IDENTITY_DEFAULT_KEEP_OPEN, 0)} still asked")
    lines.append(f"  left open: {len(plan['keep_open'])}")
    if plan.get("applied"):
        r = plan["results"]
        lines.append(
            f"  applied: {len(r['added'])} added, {len(r['linked'])} linked, "
            f"{len(r.get('auto_linked') or [])} auto-linked (UXR1 D3), "
            f"{r['merge_rows_proposed']} merge rows proposed, "
            f"{len(r['annotations'])} annotations, {len(r['expired'])} "
            f"expired, {len(r['needs_confirm'])} held for a same-name "
            f"confirm, {len(r['errors'])} errors")
        if r.get("aged_linked") or r.get("aged_let_go"):
            lines.append(
                f"  aged default applied: {len(r['aged_linked'])} linked to "
                f"the likeliest match, {len(r['aged_let_go'])} questions let "
                f"go — all under batch id {plan['batch_id']}")
        sp = r["spilled"]
        if sp["auto_add"] or sp["merge_propose"] or sp.get("aged_default"):
            lines.append(f"  cap spill (narrated, never silent): "
                         f"{sp['auto_add']} auto, {sp['merge_propose']} "
                         f"merge-propose, {sp.get('aged_default', 0)} aged "
                         f"— they stay in the review pile")
        lines.append(f"  undo: the adds and tombstones reverse with batch id "
                     f"{plan['batch_id']} (adds archive, expiries reopen)")
    return "\n".join(lines)


def main() -> int:
    # Windows pipes default to cp1252 — the narration carries non-ASCII.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="perform the writes (default: dry-run plan only)")
    ap.add_argument("--backfill", action="store_true",
                    help="use the one-time backfill caps (auto-add 15) "
                         "instead of the weekly steady-state caps")
    ap.add_argument("--now", default=None, help="ISO now override (tests)")
    ap.add_argument("--json", action="store_true",
                    help="emit the machine-readable plan as well")
    # FIX3 F3-6, fix pass 2 (review N-1). The rendered leg ends in these two
    # flags and this command used to exit 2 on them, so the Sunday family's
    # identity leg died with a usage error whenever a typed surface ran the
    # catch-up.
    ap.add_argument("--fired-via", default=None,
                    choices=("scheduled", "manual", "catchup"),
                    help="how this run was started; the seat decides when "
                         "nothing is said (receipts.effective_fired_via)")
    ap.add_argument("--triggered-by", default=None,
                    help="the surface that asked for this run")
    # cr1#665: the daily people update. `--daily` reads the one maintenance
    # window (since this job's own last receipt, 30 days on a first run);
    # `--since <iso>` names the window's start instead, under the same rule.
    ap.add_argument("--daily", action="store_true",
                    help="the daily people update: judge only what the "
                         "window brought in; receipt under people-update")
    ap.add_argument("--since", default=None,
                    help="with --daily: the window's start (ISO); default "
                         "is since this job's own last receipt")
    args = ap.parse_args()
    if args.since and not args.daily:
        ap.error("--since needs --daily")
    if args.daily and args.backfill:
        ap.error("--daily and --backfill are different passes")
    # The same export the other job CLIs make, so every composer below reads
    # who asked from one place instead of a dozen signatures.
    if getattr(args, "triggered_by", None):
        os.environ["CR_TRIGGERED_BY"] = str(args.triggered_by)
    window = (daily_window(args.workspace, since_iso=args.since,
                           now_iso=args.now) if args.daily else None)
    plan = run_identity_reconcile(
        args.workspace, apply=args.apply,
        caps=BACKFILL_CAPS if args.backfill else STEADY_CAPS,
        now_iso=args.now,
        window=window,
        # The flag wins. Absent it, a backfill is still the one-time run a
        # person asked for; anything else says NOTHING and lets
        # `_effective_fired_via` decide, so a legacy seat reads `scheduled`
        # byte for byte and a merged seat with nothing forwarded reads
        # `manual` — the M-3 rule this round exists to apply.
        fired_via=args.fired_via or ("manual" if args.backfill else None),
        triggered_by=args.triggered_by)
    print(_narrate(plan))
    if args.json:
        print(json.dumps(plan, default=str))
    return 0


__all__ = [
    "DAILY_TASK_ID",
    "DAILY_RECEIPT_TYPE",
    "daily_window",
    "propose_seen_people",
    "STEADY_CAPS",
    "BACKFILL_CAPS",
    "ROLE_ADDRESS_LOCAL_PARTS",
    "FREE_MAIL_DOMAINS",
    "FREE_MAIL_FIRST_LABELS",
    "is_free_mail_domain",
    "CORROBORATION_KINDS",
    "CORROBORATION_EMAIL_DOMAIN",
    "CORROBORATION_SAME_ORG",
    "CORROBORATION_MEETING",
    "is_role_address",
    "link_corroboration",
    "link_auto_predicate",
    "cluster_open_proposals",
    "person_queue_view",
    "count_person_rows",
    "cluster_identifier",
    "cluster_observed_email",
    "cluster_mentioned_email",
    "classify_cluster",
    "auto_link_eligible",
    "person_link_ask_line",
    "scan_existing_duplicates",
    "load_open_annotations",
    "count_open_annotations",
    "plan_reconcile",
    "run_identity_reconcile",
]


if __name__ == "__main__":
    raise SystemExit(main())
