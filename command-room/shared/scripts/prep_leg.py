#!/usr/bin/env python3
"""
The morning-brief fire's PREP LEG — isolation, ordering, one receipt
(SPEC BRIEFMERGE §A/§B/§D, M's ruling 2026-08-08).

WHY
---

Meeting prep used to be its own scheduled chat firing half an hour before the
morning brief. Two tasks where one fire suffices, and the top defect class in
this product is a scheduled task dying silently: if the prep chat stopped, the
brief said nothing, the receipt nobody read said nothing, and the customer
found out by walking into a meeting cold. Every extra task is another silent-
death surface.

So prep generation becomes a LEG of the morning-brief fire, ordered FIRST, and
the fire writes ONE receipt covering both legs. The watchdog can finally see
"the brief ran, the prep didn't" — which is exactly the sentence nothing could
say before.

THE THREE CONTRACTS THIS MODULE OWNS
------------------------------------

**§A ordering.** Prep runs, then the brief renders and READS the leg's
outcomes. There is no second discovery pass: the meeting section is built from
what the leg returned, so a brief cannot describe a prep the leg never made.
The ordering is enforced structurally, not by convention — `meeting_lines`
refuses a leg result it was not handed (`LegNotRunError`), and every outcome
carries the monotonic `seq` it was produced at, so a caller (or a test) can
assert on SEQUENCE rather than on a sleep.

**§B isolation.** The prep leg can NEVER kill the brief. A per-meeting failure
degrades to one brief line naming the meeting and how to regenerate it; a
whole-leg failure yields the brief plus one degrade banner. `run_prep_leg`
catches at BOTH levels — the per-meeting `except` inside the loop and the
whole-leg `except` around discovery — because a failure in the step that finds
the meetings is not a failure of any one meeting and must not be reported as
one. The brief ALWAYS renders.

**§D one receipt, both legs.** The fire's existing `pack_run` receipt (the
morning-brief shape `receipts.log_receipt` already writes) gains a `legs`
block and a `prep_leg` block. No new receipt type — the maintenance dispatcher
established the vocabulary for "what was due, what landed, what failed" and a
second receipt shape for the same idea is a second place to look.

WHAT THIS MODULE DOES NOT DO
----------------------------

It does not generate a prep brief. Generation is `prep_pipeline` +
`brief_writer`, driven by the orchestrator prose that gathers the five blocks;
this module is the harness around that call. Handing it a `generate` callable
keeps the isolation fence testable by REMOVAL — an exception-throwing fixture
proves the brief still renders, and deleting the fence turns the suite red.

REUSE, AND WHY `ran` IS NOT A SYNONYM FOR IT (SPEC BRIEFFIX1 Item B)
--------------------------------------------------------------------

The v186 stamp shipped three outcomes, and `skipped` renders no link. So an
agent that found a perfectly good prep already on disk had exactly two moves:
report `skipped` and withhold the link the CEO actually wants, or report `ran`
and claim work it did not do. It picked the second, every time — and on
2026-08-09 a 22:25 fire rendered a prep built at 16:30 as if it had just made
it. The vocabulary was the bug: there was no word for the true thing.

`reused` is that word. A meeting whose prep receipt already exists is NOT
regenerated (forcing a rebuild to earn a link is work for the machine's
benefit), it renders the SAME link line, and its receipt row records the
`prep_brief` seq it leaned on — so the audit trail says which document the
CEO was handed and which fire made it.

The other half is enforcement: `ran` may appear ONLY beside a prep receipt
written by THIS fire. `validate_leg_result` is that check, and it is a check
rather than prose because a leg dict can be hand-built (this module's own
tests build one) and prose cannot stop that. A `ran` with no same-fire receipt
seq is the bypass, named.

Stdlib only. Pure except the disk probes `workspace_paths` makes when deciding
whether an attachment is on this machine yet, and the substrate read that
answers "is there already a prep for this meeting".
"""
from __future__ import annotations

import datetime as _dt
import itertools
import sys
from pathlib import Path
from typing import Callable, Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from workspace_paths import (  # noqa: E402
    SYNCING_TEXT,
    assert_workspace_relative,
    attach_line,
    resolve_pointer,
)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

LEG_ID = "prep"
BRIEF_LEG_ID = "brief"

# Per-meeting outcomes. The first three are the words the maintenance
# dispatcher's job vocabulary uses for the same three states, deliberately —
# one receipt vocabulary across every multi-leg fire.
#
# `reused` (BRIEFFIX1 Item B) is the fourth, and it is a FACT the other three
# could not express: a prep for this meeting already exists, so none was made,
# and the link is still owed. It is not a flavour of `ran` (no work happened)
# and not a flavour of `skipped` (the CEO still gets the document).
OUTCOME_RAN = "ran"
OUTCOME_REUSED = "reused"
OUTCOME_DEGRADED = "degraded"
OUTCOME_SKIPPED = "skipped"
OUTCOMES = (OUTCOME_RAN, OUTCOME_REUSED, OUTCOME_DEGRADED, OUTCOME_SKIPPED)

# The outcomes that render a link line. Kept as data so the renderer and the
# receipt reader agree, and so adding a fifth outcome forces a decision here
# rather than defaulting to silence.
LINKED_OUTCOMES = (OUTCOME_RAN, OUTCOME_REUSED)

# WHAT MAKES A RECEIPT "THIS MEETING'S PREP" (revised after second-eyes review,
# 2026-08-09 — the first cut got this wrong and the reviewer reproduced it).
#
# The first cut bounded reuse by the receipt's AGE: 24 hours, justified as
# call-prep's `auto_fire: 24h` horizon. Two things were wrong with that. The
# horizon is a PRE-GENERATION LEAD TIME ("build the brief 24h before the
# meeting"), not a statement of how long a document stays fresh — and it is
# user-configurable. And a clock window structurally cannot separate two
# instances of a recurring meeting: for anything daily or weekday-cadenced the
# whole window sits INSIDE the recurrence interval, so a stable series id
# reused yesterday's document at 14h and at 23.9h. Reproduced, both.
#
# Identity is the right test, not age. A prep receipt is THIS meeting's prep
# only when it names this calendar event AND this instance's start. Two
# instances of a weekly standup share an id and differ in exactly one field,
# which is the field that has to match.
#
# On top of identity sits ONE bound: the receipt must have been written today,
# in the fire's own local day. It is a deliberate, stated choice rather than a
# derivation — a prep built for today's meeting during yesterday's fire is
# probably still good, and regenerating it costs one document. Getting it wrong
# the other way hands the CEO a stale brief and calls it fresh.
#
# Everything here fails CLOSED: an unknown instance start (on the receipt or on
# the meeting), an unresolvable clock, a receipt with no filename — every one of
# them regenerates. The worst case is a duplicate document; the failure this
# replaces was a stale one presented as current.
REUSE_REQUIRES_INSTANCE_MATCH = True

# Where the ONE generator puts prep briefs (CONTRACT.md Rule 3 / brief_path.
# get_brief_path). The `prep_brief` receipt records the FILENAME, not the
# path — deliberately, and BRIEFFIX1 does not widen that shape — so the reuse
# path rebuilds the pointer from the one directory the contract allows.
PREP_DIR = "_hq/meetings"

# The bypass detector's check id. Named, because a red line nobody can name is
# a red line nobody can fix.
CHECK_RAN_WITHOUT_RECEIPT = "prep-ran-without-receipt"

# Leg-level status. `skipped` is a THIRD state, not a flavour of degraded: the
# leg was deliberately not run, which is a different fact from the leg trying
# and failing, and the watchdog must not raise a finding over it.
STATUS_RAN = "ran"
STATUS_DEGRADED = "degraded"
STATUS_SKIPPED = "skipped"
LEG_STATUSES = (STATUS_RAN, STATUS_DEGRADED, STATUS_SKIPPED)

# The one reason a degrade-tier fire skips the leg, as a constant so the
# orchestrator prose and the receipt agree on the words.
SKIP_DEGRADE_TIER = "degrade-tier fire"

# The reason an ON-DEMAND brief has no prep leg (BRIEFFIX1 F1). "Brief me" is
# the digest half only — it never generated prep and is not supposed to — but
# it still owes the fire receipt, because it still posts numbered items the CEO
# can close by number. A skip reason is how that receipt stays honest about
# what did not happen, instead of borrowing the degrade-tier wording for a
# situation that has nothing to do with lateness.
SKIP_NO_LEG = "on-demand brief — no prep leg"

# The whole-leg degrade banner. ONE line, and it is the entire thing the brief
# says about a leg that could not run at all — the meetings are still on the
# calendar section, they just carry no prep.
WHOLE_LEG_BANNER = (
    "Meeting prep didn't run this morning — say `prep me for my next call` "
    "for any meeting you want prepped."
)

_seq_counter = itertools.count(1)


def _next_seq() -> int:
    """A monotonic within-process sequence number. The ordering pin asserts on
    THIS, never on wall-clock time: two steps in the same fire can share a
    millisecond, and a sleep-based ordering test proves patience, not order."""
    return next(_seq_counter)


class LegNotRunError(RuntimeError):
    """The brief tried to render its meeting section without the prep leg's
    result. THE §A ordering fence: prep runs first and the brief reads what it
    returned, so a render that never saw the leg is a contract violation, not
    a brief with a thinner meeting section."""


# ---------------------------------------------------------------------------
# Degrade lines
# ---------------------------------------------------------------------------

def meeting_label(meeting) -> str:
    """How the brief names one meeting in a degrade line: the time when the
    calendar gave one ("2:15"), else the title. Time first because that is how
    the CEO refers to a meeting when regenerating it — "prep me for my 2:15"
    is the phrase call-prep already answers."""
    if not isinstance(meeting, dict):
        return str(meeting or "").strip() or "that meeting"
    label = str(meeting.get("time_label") or "").strip()
    if label:
        return label
    title = str(meeting.get("title") or "").strip()
    return title or "that meeting"


def degrade_line(meeting) -> str:
    """The ONE line a failed per-meeting prep contributes to the brief.

    It names the meeting and the exact phrase that regenerates it — a degrade
    the reader can act on in one sentence. It never names the error: the
    reason rides the receipt (§D), where the watchdog reads it, and a stack
    class in the morning digest helps nobody.
    """
    if isinstance(meeting, dict) and str(meeting.get("time_label") or "").strip():
        label = str(meeting["time_label"]).strip()
        return (f"prep failed for the {label} — say `prep me for my {label}` "
                f"to regenerate")
    label = meeting_label(meeting)
    return (f"prep failed for {label} — say `prep me for the {label}` "
            f"to regenerate")


# ---------------------------------------------------------------------------
# §A + §B — the leg itself
# ---------------------------------------------------------------------------

def _outcome(meeting, outcome, *, reason=None, brief_path=None, seq=None,
             receipt_seq=None, source_receipt_seq=None) -> dict:
    """One per-meeting outcome row.

    Two receipt fields, and they are NOT interchangeable (BRIEFFIX1 Item B):
      * `receipt_seq`        — the `prep_brief` THIS fire wrote. Present only
                               on `ran`, and its absence there IS the bypass.
      * `source_receipt_seq` — the `prep_brief` an earlier fire wrote and this
                               one leaned on. Present only on `reused`.
    Keeping them apart is the whole audit value: "which fire made the document
    the CEO just opened" has one answer, and it is in this row.
    """
    return {
        "meeting_id": str((meeting or {}).get("meeting_id") or "").strip() or None,
        "title": str((meeting or {}).get("title") or "").strip() or None,
        "label": meeting_label(meeting),
        "outcome": outcome,
        "reason": reason,
        "brief_path": brief_path,
        "seq": seq,
        "receipt_seq": receipt_seq,
        "source_receipt_seq": source_receipt_seq,
    }


def _substrate_prep_lookup(workspace_root):
    """The default receipt-based prep detector: `prep_brief` receipts for one
    meeting id, newest first (F-29 — the detector and the writer read ONE
    signal; never a folder glob, never a slug guess).

    Returns a callable, or None when there is no workspace to read. A caller
    can pass its own through `run_prep_leg(prep_lookup=...)`; the leg does not
    care where the answer comes from, only that it is receipt-shaped.
    """
    if not workspace_root:
        return None

    def lookup(meeting_id, *, since=None):
        if not meeting_id:
            return None
        try:
            from receipts import prep_receipts

            rows = prep_receipts(workspace_root, meeting_ids=[meeting_id],
                                 since=since)
        except Exception:  # noqa: BLE001 — a substrate read never kills a leg
            return None
        if not rows:
            return None
        row = rows[-1]
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        seq = raw.get("seq")
        return {
            "seq": seq if isinstance(seq, int) else None,
            "artifact": row.get("artifact"),
            "dt": row.get("dt"),
            # The instance discriminator. Absent on every pre-BRIEFFIX1 receipt,
            # which is exactly why its absence must mean "cannot prove" rather
            # than "close enough".
            "meeting_start": data.get("meeting_start"),
        }

    return lookup


def normalize_instant(value) -> Optional[str]:
    """One canonical spelling for an instant, so two records of the same
    meeting start compare equal across the shapes connectors emit.

    `2026-08-10T09:30:00Z`, `2026-08-10T09:30:00+00:00` and
    `2026-08-10T02:30:00-07:00` are the same instant written three ways; a
    string comparison would call them three different meetings and regenerate
    every prep forever. Returns None for anything unparseable — which the
    callers treat as "cannot prove", never as "matches".
    """
    if isinstance(value, _dt.datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            dt = _dt.datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def local_day_floor(now=None, workspace_root=None):
    """Midnight of the fire's own local day, as an aware UTC instant.

    The workspace timezone is the right one — it is the clock the CEO's day is
    measured in and the one every rendered time already uses. When it cannot be
    resolved (a fixture, a workspace with no timezone set), this falls back to
    THIS MACHINE's local day rather than to UTC: the fire runs on the machine,
    machine-local is what the scheduler's own math uses, and a UTC day boundary
    on a US workspace would put "today" 5-8 hours in the wrong place. Never
    raises — a clock read must not kill the leg.
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    local = None
    if workspace_root:
        try:
            from tz import to_local

            local = to_local(now, workspace_path=str(workspace_root))
        except Exception:  # noqa: BLE001 — fall through to machine-local
            local = None
    if local is None:
        local = now.astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(_dt.timezone.utc)


def _is_this_instance(found, meeting) -> bool:
    """Does this receipt name THIS meeting instance?

    Both sides must state the instance start and the two must be the same
    instant. A missing value on either side is a `False`: the whole point of
    the identity test is that "we don't know" and "it matches" are different
    answers, and the 2026-08-09 review found what happens when they are
    conflated (two instances of a recurring meeting sharing one document).
    """
    receipt_start = normalize_instant((found or {}).get("meeting_start"))
    if not receipt_start:
        return False
    meeting_start = normalize_instant(
        (meeting or {}).get("start") or (meeting or {}).get("meeting_start"))
    if not meeting_start:
        return False
    return receipt_start == meeting_start


def _reused_pointer(found) -> Optional[str]:
    """The workspace-relative pointer for a reused prep, rebuilt from the
    receipt's filename under the ONE directory the contract allows. Returns
    None when the receipt carries no filename — in which case there is nothing
    to link and the meeting must not claim a link it cannot render."""
    artifact = str((found or {}).get("artifact") or "").strip()
    if not artifact:
        return None
    artifact = artifact.replace("\\", "/").rsplit("/", 1)[-1]
    if not artifact:
        return None
    return f"{PREP_DIR}/{artifact}"


def skipped_leg(reason: str = SKIP_DEGRADE_TIER) -> dict:
    """A leg result for a fire that deliberately did NOT run prep.

    The degrade-tier path is the case that exists today: nothing renders on
    that tier, so generating prep would produce documents the customer never
    receives AND a `prep_brief` receipt that makes tomorrow's no-prep detector
    call the meeting prepped. The fire still owes its combined receipt, which
    means it needs a leg result — and hand-rolling that dict at the call site
    is the hand-rolled-shape drift class the receipt contract bans everywhere
    else (review F-5). So the shape has a constructor.

    Deliberately NOT `degraded`: a skip is a decision, and the watchdog raises
    no finding over a decision.
    """
    return {
        "status": STATUS_SKIPPED,
        "banner": None,
        "reason": (reason or SKIP_DEGRADE_TIER).strip() or SKIP_DEGRADE_TIER,
        "outcomes": [],
        "counts": {o: 0 for o in OUTCOMES},
        "started_seq": _next_seq(),
        "completed_seq": _next_seq(),
    }


def run_prep_leg(
    discover: Callable[[], Iterable[dict]],
    generate: Callable[[dict], Optional[dict]],
    *,
    workspace_root=None,
    prep_lookup=None,
    now=None,
) -> dict:
    """Run prep for today's meetings. NEVER raises.

    `discover()` returns the meetings to prep — the calendar pass the fire was
    going to make anyway. `generate(meeting)` produces one prep brief and
    returns `{"brief_path": <workspace-relative>, ...}`, or `None` to record a
    deliberate skip (auto-prep switched off, a personal block). Anything
    either callable raises is CAUGHT here.

    **Reuse comes FIRST, and it is an IDENTITY test (BRIEFFIX1 Item B, revised
    after review).** Before `generate` is called for a meeting,
    `prep_lookup(meeting_id)` asks the substrate for a `prep_brief` receipt
    written TODAY (the fire's own local day) for that calendar id; the leg then
    requires the receipt's `meeting_start` to be THIS instance's start. Both
    conditions hold or the generator runs. A clock-age window was the first cut
    and it was wrong: for a daily or weekday-cadenced meeting the window sits
    inside the recurrence interval, so a stable series id reused yesterday's
    document. Reuse is decided from RECEIPTS (F-29), never from a folder
    listing — the detector and the writer must read one signal or they disagree
    about reality, which is how "no prep" was reported over a document that
    existed.

    `discover()` must therefore give each meeting a `start` — the instance's own
    start time. Without it nothing can be proven and every meeting regenerates:
    safe, and visibly wasteful, which is the right way for this to fail.

    Two failure levels, deliberately distinct:
      * `generate` raises            → that meeting is `degraded`; the rest of
                                       the loop continues.
      * `discover` raises            → the whole leg is `degraded` with one
                                       banner and NO per-meeting rows. The
                                       step that finds the meetings failing is
                                       not a failure of any meeting, and
                                       reporting it as one would invent rows
                                       for meetings nobody enumerated.

    Returns
    -------
    {"status", "banner", "reason", "outcomes", "counts", "completed_seq"}

    `completed_seq` is the sequence number the leg finished at; the brief's
    render carries a later one (see `meeting_lines`). That pair IS the
    ordering proof.
    """
    started_seq = _next_seq()
    started_at = now or _dt.datetime.now(_dt.timezone.utc)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=_dt.timezone.utc)
    if prep_lookup is None:
        prep_lookup = _substrate_prep_lookup(workspace_root)
    try:
        meetings = list(discover() or [])
    except Exception as exc:  # noqa: BLE001 — isolation is the whole point
        return {
            "status": STATUS_DEGRADED,
            "banner": WHOLE_LEG_BANNER,
            "reason": f"{type(exc).__name__}: {exc}"[:300],
            "outcomes": [],
            "counts": {o: 0 for o in OUTCOMES},
            "started_seq": started_seq,
            "completed_seq": _next_seq(),
        }

    def _lookup(meeting_id, *, since=None):
        if prep_lookup is None:
            return None
        try:
            return prep_lookup(meeting_id, since=since)
        except Exception:  # noqa: BLE001 — a detector failure is not a leg
            return None                      # failure; it just means "unknown"

    outcomes = []
    for meeting in meetings:
        if not isinstance(meeting, dict):
            continue
        meeting_id = str(meeting.get("meeting_id") or "").strip()

        # --- reuse, decided before any generation ------------------------
        # The floor is TODAY, and the match is THIS INSTANCE. Both, not either:
        # the floor alone lets a recurring meeting inherit its own earlier
        # instance's document inside the day, and the instance match alone
        # would resurrect an arbitrarily old prep for a meeting that never
        # moved. Reuse is the narrow case, and everything unproven regenerates.
        found = _lookup(meeting_id, since=local_day_floor(started_at,
                                                          workspace_root))
        if found and _is_this_instance(found, meeting):
            relative = _reused_pointer(found)
            if relative:
                outcomes.append(_outcome(
                    meeting, OUTCOME_REUSED,
                    brief_path=assert_workspace_relative(
                        relative, field="brief_path"),
                    source_receipt_seq=found.get("seq"),
                    seq=_next_seq(),
                ))
                continue
            # A receipt with no filename cannot be linked. Fall through and
            # generate rather than reuse a pointer we cannot build — an
            # unlinkable "reused" would be the withheld-link bug wearing the
            # new word.

        try:
            produced = generate(meeting)
        except Exception as exc:  # noqa: BLE001 — per-meeting isolation
            outcomes.append(_outcome(
                meeting, OUTCOME_DEGRADED,
                reason=f"{type(exc).__name__}: {exc}"[:300],
                seq=_next_seq(),
            ))
            continue
        if not produced:
            outcomes.append(_outcome(
                meeting, OUTCOME_SKIPPED,
                reason=(meeting.get("skip_reason") or "no prep requested"),
                seq=_next_seq(),
            ))
            continue
        raw_path = (produced or {}).get("brief_path") if isinstance(produced, dict) else None
        resolved = resolve_pointer(raw_path, workspace_root)
        relative = resolved["relative"]
        if raw_path and not relative:
            # A pointer this workspace cannot make portable. Persisting it
            # would re-create the exact rot this build exists to end, so the
            # meeting degrades honestly instead — carrying the resolver's own
            # REASON, not a generic sentence, so the receipt says which way it
            # was unresolvable months later (review F-6b).
            outcomes.append(_outcome(
                meeting, OUTCOME_DEGRADED,
                reason=("prep brief path could not be resolved "
                        f"workspace-relative: {resolved['reason']}"),
                seq=_next_seq(),
            ))
            continue
        # The same-fire receipt. `since=started_at` is what makes it SAME-fire:
        # a receipt older than this leg is a prior fire's, and counting it here
        # would let a generator that wrote nothing inherit an old proof.
        fresh = _lookup(meeting_id, since=started_at)
        outcomes.append(_outcome(
            meeting, OUTCOME_RAN,
            brief_path=assert_workspace_relative(relative, field="brief_path"),
            receipt_seq=(fresh or {}).get("seq"),
            seq=_next_seq(),
        ))

    counts = {o: 0 for o in OUTCOMES}
    for row in outcomes:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
    return {
        "status": STATUS_RAN,
        "banner": None,
        "reason": None,
        "outcomes": outcomes,
        "counts": counts,
        "started_seq": started_seq,
        "completed_seq": _next_seq(),
    }


# ---------------------------------------------------------------------------
# §A — what the brief renders from the leg (no second discovery pass)
# ---------------------------------------------------------------------------

def meeting_lines(leg_result, *, workspace_root=None) -> list:
    """The brief's meeting-section lines, built from the LEG'S OUTCOMES.

    Raises `LegNotRunError` when handed no leg result. That is the §A fence:
    the brief may not describe prep it never watched happen, and "the leg
    hasn't run yet" must be a loud failure rather than a brief that quietly
    reports no prep for meetings that were prepped a second later.

    One line per meeting:
      * ran       → a workspace-relative markdown link, or the `syncing`
                    sentence when Drive hasn't landed the file on this machine
                    yet. Never a dead card.
      * reused    → the SAME line as `ran` (BRIEFFIX1 Item B). The CEO asked
                    for the document, not for a report on which fire built it;
                    the provenance belongs on the receipt, where the audit
                    reads it, and withholding the link because no work happened
                    this morning is the bug this outcome exists to end.
      * degraded  → the regenerate line (`degrade_line`).
      * skipped   → nothing. A deliberate skip is not news; padding the brief
                    with "no prep requested" lines is exactly the growth §5
                    forbids.

    The links are WORKSPACE-RELATIVE here, deliberately: these lines go into
    the composed digest, which is saved in that form. The chat copy is
    converted at post time by the ONE chokepoint
    (`chat_output_renderer.absolutize_doc_links`) — BRIEFFIX1 Item A.

    Prep outcomes are LINES, not a section: the merged fire must not grow the
    brief past its existing caps.
    """
    if leg_result is None or not isinstance(leg_result, dict) or "outcomes" not in leg_result:
        raise LegNotRunError(
            "the prep leg must run BEFORE the brief renders its meeting section "
            "(SPEC BRIEFMERGE §A) — pass run_prep_leg()'s result"
        )
    leg_result["render_seq"] = _next_seq()
    lines = []
    if leg_result.get("status") == STATUS_DEGRADED and leg_result.get("banner"):
        lines.append(leg_result["banner"])
    for row in leg_result.get("outcomes") or []:
        if row.get("outcome") in LINKED_OUTCOMES:
            label = f"Prep — {row['label']}" if row.get("label") else "Prep"
            lines.append(attach_line(label, row.get("brief_path"), workspace_root))
        elif row.get("outcome") == OUTCOME_DEGRADED:
            lines.append(degrade_line({
                "time_label": row.get("label") if row.get("label") != row.get("title") else None,
                "title": row.get("title"),
            }))
    return lines


def rendered_after_leg(leg_result) -> bool:
    """True when the brief's meeting section was rendered AFTER the leg
    finished. The ordering assertion, in code: `render_seq` is stamped by
    `meeting_lines`, `completed_seq` by `run_prep_leg`, both from one
    monotonic counter. No clock, no sleep."""
    if not isinstance(leg_result, dict):
        return False
    render_seq = leg_result.get("render_seq")
    completed = leg_result.get("completed_seq")
    if render_seq is None or completed is None:
        return False
    return render_seq > completed


# ---------------------------------------------------------------------------
# §D — ONE receipt, both legs
# ---------------------------------------------------------------------------

def validate_leg_result(leg_result) -> list:
    """THE bypass detector (BRIEFFIX1 Item B): every `ran` row that carries no
    same-fire `prep_brief` receipt seq.

    Why a check and not a rule in prose: the leg result is a plain dict. Any
    caller — an orchestrator agent following instructions, a future helper, a
    test — can build one that says `ran`, and prose cannot stop it. What CAN
    is a named finding that rides the receipt, so "this fire claimed to
    generate prep and left no proof" is a readable fact months later instead
    of an inference nobody makes.

    `reused` is deliberately NOT checked for a same-fire receipt: leaning on an
    earlier fire's receipt is the whole point of the outcome, and its
    `source_receipt_seq` is checked instead. Returns `[]` on a clean leg, and
    on anything that is not a leg dict (a shape complaint is not this
    function's job — `meeting_lines` already owns the missing-leg fence).

    Findings: `{check, meeting_id, label, detail}`.
    """
    if not isinstance(leg_result, dict):
        return []
    findings = []
    for row in leg_result.get("outcomes") or []:
        if not isinstance(row, dict):
            continue
        outcome = row.get("outcome")
        if outcome == OUTCOME_RAN and row.get("receipt_seq") is None:
            findings.append({
                "check": CHECK_RAN_WITHOUT_RECEIPT,
                "meeting_id": row.get("meeting_id"),
                "label": row.get("label"),
                "detail": ("outcome `ran` with no prep_brief receipt from this "
                           "fire — either the generator wrote no receipt or the "
                           "row was built without running it"),
            })
        elif outcome == OUTCOME_REUSED and row.get("source_receipt_seq") is None:
            findings.append({
                "check": CHECK_RAN_WITHOUT_RECEIPT,
                "meeting_id": row.get("meeting_id"),
                "label": row.get("label"),
                "detail": ("outcome `reused` with no source receipt seq — "
                           "reuse must name the prep_brief it leaned on"),
            })
    return findings


def prep_leg_block(leg_result) -> dict:
    """The `prep_leg` payload on the fire's receipt: leg status, the three
    counts, and one row per meeting carrying its outcome and reason.

    Reasons ride HERE and not in the digest — the receipt is where the
    watchdog looks, and a fire that degraded for a resolvable reason should
    leave that reason on disk even though the CEO never sees it.
    """
    if not isinstance(leg_result, dict):
        return {
            "status": STATUS_DEGRADED,
            "counts": {o: 0 for o in OUTCOMES},
            "meetings": [],
            "reason": "prep leg produced no result",
        }
    meetings = []
    for row in (leg_result.get("outcomes") or []):
        entry = {
            "meeting_id": row.get("meeting_id"),
            "outcome": row.get("outcome"),
            "reason": row.get("reason"),
        }
        # Provenance rides ONLY where it means something: which fire made the
        # document. A None on every row would be noise on the majority of them.
        if row.get("receipt_seq") is not None:
            entry["receipt_seq"] = row["receipt_seq"]
        if row.get("source_receipt_seq") is not None:
            entry["source_receipt_seq"] = row["source_receipt_seq"]
        meetings.append(entry)
    block = {
        "status": leg_result.get("status") or STATUS_DEGRADED,
        "counts": dict(leg_result.get("counts") or {o: 0 for o in OUTCOMES}),
        "meetings": meetings,
    }
    bypass = validate_leg_result(leg_result)
    if bypass:
        # BRIEFFIX1 Item B — the finding lands ON the receipt rather than
        # raising. Refusing the write would lose the whole fire's audit over a
        # provenance gap, which trades one blind spot for a bigger one; a
        # named field is readable forever and blocks nothing.
        block["bypass"] = bypass
    if leg_result.get("reason"):
        block["reason"] = leg_result["reason"]
    if leg_result.get("banner"):
        block["banner"] = leg_result["banner"]
    return block


def legs_block(leg_result, *, brief_status: str = STATUS_RAN) -> dict:
    """The leg-status map the watchdog reads: `{"brief": ..., "prep": ...}`.

    This is the field that finally makes "brief ran, prep didn't" a readable
    fact. `prep` is `degraded` when the leg failed as a whole OR when any
    single meeting degraded — a fire that prepped four of five meetings is not
    a clean fire, and rounding it to `ran` is how a partial failure becomes
    invisible.
    """
    block = prep_leg_block(leg_result)
    prep_status = block.get("status") or STATUS_DEGRADED
    if prep_status == STATUS_RAN and (block.get("counts") or {}).get(OUTCOME_DEGRADED):
        prep_status = STATUS_DEGRADED
    # A SKIPPED leg passes through untouched — a decision is not a failure,
    # and rounding it to either `ran` or `degraded` would lie in one of the
    # two directions the watchdog acts on.
    return {BRIEF_LEG_ID: brief_status, LEG_ID: prep_status}


def log_combined_receipt(
    workspace_root,
    *,
    leg_result,
    brief_status: str = STATUS_RAN,
    fired_via: str = "scheduled",
    duration_ms: Optional[int] = None,
    late_tier: Optional[str] = None,
    extra_data: Optional[dict] = None,
):
    """THE fire's one receipt — the existing morning-brief `pack_run` shape
    with both legs' outcomes folded in. No new receipt type (§D: extend,
    don't invent), so every reader that already parses a morning-brief receipt
    keeps working and gains the leg detail for free.

    Every persisted file-pointer in `extra_data` goes through the writer-side
    assert on the way in: this is the one call site where the merged fire
    records where it put things, and an absolute path here is a dead link on
    every other machine (SPEC BRIEFMERGE §C).
    """
    from receipts import log_receipt

    data = dict(extra_data or {})
    from workspace_paths import POINTER_FIELDS

    for field in POINTER_FIELDS:
        if field in data:
            data[field] = assert_workspace_relative(data[field], field=field)
    data["legs"] = legs_block(leg_result, brief_status=brief_status)
    data["prep_leg"] = prep_leg_block(leg_result)
    return log_receipt(
        workspace_root,
        "morning-brief",
        receipt_type="pack_run",
        fired_via=fired_via,
        duration_ms=duration_ms,
        late_tier=late_tier,
        extra_data=data,
    )


# ---------------------------------------------------------------------------
# §D — the read side (what system-health / the watchdog see)
# ---------------------------------------------------------------------------

def read_latest_leg_status(workspace_root) -> Optional[dict]:
    """The newest morning-brief receipt's leg detail, or None when the
    substrate carries no leg-aware receipt yet (every pre-BRIEFMERGE receipt,
    forever — history is append-only and this reader tolerates it silently).

    "Newest LEG-AWARE" excludes a fire that had no leg at all — an on-demand
    brief, whose receipt carries `prep_leg.reason == SKIP_NO_LEG` (review
    RV-2). Such a receipt is newer but says nothing about prep, and letting it
    win meant one "brief me" silenced a real degraded-prep finding until the
    next scheduled fire. A degrade-tier skip is deliberately NOT excluded: that
    fire genuinely decided not to prep, which is a statement about the leg.

    Returns `{"dt", "legs", "prep_leg"}`.
    """
    try:
        from receipts import iter_receipts
    except Exception:  # noqa: BLE001 — a health read never breaks a fire
        return None
    try:
        rows = iter_receipts(workspace_root, task_ids=["morning-brief"])
    except Exception:  # noqa: BLE001
        return None
    for row in reversed(rows or []):
        raw = row.get("raw") if isinstance(row, dict) else None
        data = (raw or {}).get("data") if isinstance(raw, dict) else None
        if not isinstance(data, dict):
            continue
        if "prep_leg" not in data and "legs" not in data:
            continue
        if (data.get("prep_leg") or {}).get("reason") == SKIP_NO_LEG:
            # A LEG-LESS fire says nothing about the prep leg (review RV-2).
            # An on-demand brief never had one, so its receipt is not evidence
            # that the last real leg is fine — and because it is the newest
            # leg-aware receipt, treating it as one switched OFF a live
            # "prep failed for N meetings" finding the moment the CEO typed
            # "brief me". Skip it and keep looking: the newest receipt from a
            # fire that ACTUALLY RAN the leg is the one with something to say.
            # This is what SKIP_NO_LEG exists for; nothing consumed it before.
            continue
        return {
            "dt": row.get("dt"),
            "legs": data.get("legs") or {},
            "prep_leg": data.get("prep_leg") or {},
        }
    return None


def prep_leg_finding(workspace_root) -> Optional[dict]:
    """The watchdog's "brief ran, prep didn't" finding, or None.

    Fires ONLY on the newest leg-aware receipt, and only when the brief leg
    ran while the prep leg did not — the asymmetry is the whole signal. A fire
    where both legs degraded is already covered by the task-level line; a fire
    with no leg-aware receipt says nothing at all rather than guessing.
    """
    latest = read_latest_leg_status(workspace_root)
    if not latest:
        return None
    legs = latest.get("legs") or {}
    if legs.get(BRIEF_LEG_ID) != STATUS_RAN:
        return None
    if legs.get(LEG_ID) in (STATUS_RAN, STATUS_SKIPPED):
        # `skipped` is a decision the fire recorded, not a failure it hit.
        # Raising a finding over it would train the reader to ignore the
        # finding that matters.
        return None
    block = latest.get("prep_leg") or {}
    counts = block.get("counts") or {}
    degraded = int(counts.get(OUTCOME_DEGRADED) or 0)
    whole_leg = (block.get("status") == STATUS_DEGRADED)
    if whole_leg:
        line = (
            "Your Morning Brief ran, but meeting prep didn't — open the "
            "Morning Brief in the Scheduled section and press Run Now once, "
            "and check the prep briefs land."
        )
    else:
        noun = "meeting" if degraded == 1 else "meetings"
        line = (
            f"Your Morning Brief ran, but prep failed for {degraded} {noun} — "
            f"say `prep me for` that meeting to regenerate it."
        )
    return {
        "leg": LEG_ID,
        "status": block.get("status") or STATUS_DEGRADED,
        "degraded": degraded,
        "whole_leg": whole_leg,
        "line": line,
        "last_receipt": latest["dt"].isoformat() if latest.get("dt") else None,
    }


__all__ = [
    "BRIEF_LEG_ID",
    "LEG_ID",
    "LegNotRunError",
    "OUTCOMES",
    "OUTCOME_DEGRADED",
    "OUTCOME_RAN",
    "OUTCOME_REUSED",
    "OUTCOME_SKIPPED",
    "LINKED_OUTCOMES",
    "REUSE_REQUIRES_INSTANCE_MATCH",
    "local_day_floor",
    "normalize_instant",
    "PREP_DIR",
    "CHECK_RAN_WITHOUT_RECEIPT",
    "validate_leg_result",
    "LEG_STATUSES",
    "SKIP_DEGRADE_TIER",
    "SKIP_NO_LEG",
    "STATUS_DEGRADED",
    "STATUS_RAN",
    "STATUS_SKIPPED",
    "SYNCING_TEXT",
    "WHOLE_LEG_BANNER",
    "degrade_line",
    "skipped_leg",
    "legs_block",
    "log_combined_receipt",
    "meeting_label",
    "meeting_lines",
    "prep_leg_block",
    "prep_leg_finding",
    "read_latest_leg_status",
    "rendered_after_leg",
    "resolve_pointer",
    "run_prep_leg",
]
