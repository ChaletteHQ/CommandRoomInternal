#!/usr/bin/env python3
"""TTL1 — every question expires (SPEC_FLOW1 Lane G, night 10, 2026-09-07).

WHY THIS MODULE EXISTS
----------------------
On M's own book on 2026-09-07 the Staff Meeting carried a queue whose oldest
identity rows had been waiting since June 30 — 109 identity questions, nine of
the ten rows on one fire spelling variants of people already on file. Nothing
was wrong with any individual asker. What was missing was the OTHER HALF of a
question: a question that nobody answers must stop being a question.

Four of the five question classes already had a lifetime somewhere in the
code. None of them had the same shape, two of them never ran on a schedule
anybody could name, and NONE of them was reported to the customer as a
decision the product made on their behalf. So a queue only ever grew.

THIS MODULE IS THE REGISTRY AND THE ENGINE, and it is deliberately thin:

  * the REGISTRY (`CLASSES`) names, for every question class, its LIFETIME,
    its DEFAULT, its REVERSER and the RAIL that applies it. Each lifetime is
    read from the module that already owns that number — one number, one
    home, never a second spelling (the QUIET1 `DEFAULT_WINDOW_DAYS` posture);
  * the ENGINE (`run_question_expiry`) runs the classes in lifetime order
    under ONE batch id, so a whole night's expiry is one `undo`;
  * the IMPORTANCE RULE (`importance_hold`) is a pure predicate every class
    consults BEFORE a destructive default: an expiry never closes or drops a
    row that is overdue, due this week, or carries a client or money. It
    applies the "track it" default instead — the question goes away, the row
    stays open and keeps its place on the plate.

WHAT IT DOES NOT DO
-------------------
It does not rebuild a single existing drain. `chip_ttl_action` /
`resolve_stale_chips` (proposals, four days) and the review-expiry lapse
(capture-card, two days) already work; this engine CALLS them with its own
batch id instead of re-deciding what they decide. The identity class is
IDENT1's: the lifetime constant and the pure default resolver live in
`identity_reconcile` next to the gate whose refusals they read, and the
registry points at them by name.

SEAMS (named, because they are not all in this tree yet)
--------------------------------------------------------
  * IDENT1 (`~/repos/wt-ident1`, branch `ident1`) exposes
    `identity_reconcile.identity_aged_default(...)` (pure — decides, never
    writes), `identity_reconcile.identity_question_age_days(...)` and
    `identity_reconcile.IDENTITY_QUESTION_TTL_DAYS = 14`. This module
    resolves all three BY NAME at call time (`_ident1()`), so it is correct
    on the merged tree and honest on this one: with IDENT1 absent the
    identity class reports `unavailable` and applies nothing rather than
    inventing a second identity default.
  * ATTRIB2 (branch `attrib2`) — every act this engine writes is a MACHINE
    act. Where `event_types.resolve_actor` exists (post-ATTRIB2) the actor is
    resolved through it; where it does not, this module writes ATTRIB2's own
    vocabulary directly (`actor_kind: "machine"`, the rail's own name as the
    actor), so the merged tree reads one way and this tree never signs the
    customer's name to a timeout.
  * LEDGERFENCE1 (branch `ledgerfence1`) — the batch id shape `brain_undo`
    lists: a `<prefix><UTC compact>-<8 hex>` string, prefix `qex_` here.
  * INTAKE1 (branch `intake1`) — its ONE day-7 Staff Meeting question for a
    held row with a client or money is INSIDE the ceiling this module
    publishes (`staff_meeting_question_ceiling`), never on top of it.
  * QUIET1 (`quiet.QUESTION_BUDGET`) — the light budget is the ceiling. This
    module never invents a budget of its own; it reads QUIET1's.

WRITES. Through the sanctioned writers only: `event_gate.append_event` for
its own run receipt and the proposal tombstones, `commitment_state
.clear_review_flags` for the "track it" default, `attribution_doors
.apply_counterparty_default` for the capture-card default, and the classes'
own rails for everything else. Every full-history read goes through
`events_io`. Names in docstrings and tests are the Sample/Stone roster.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

SOURCE_SKILL = "question-expiry"

# ---------------------------------------------------------------------------
# The classes
# ---------------------------------------------------------------------------

CLASS_CAPTURE_CARD = "capture_card"
CLASS_PROPOSAL = "proposal"
CLASS_DEAL_SIGNAL = "deal_signal"
CLASS_HYGIENE = "hygiene"
CLASS_IDENTITY = "identity"
#: DOORS1 1.4 (M's ruling R6, the proposal half, 2026-09-13). A
#: `decision_supersede_proposed` row — "a meeting may have reversed this
#: decision; a human decides where the decision lives". The attended test
#: found 742 of them unanswered, written in bursts of 100 / 109 / 74, with no
#: lifetime of any kind: the only question class in the product that could
#: never age out.
CLASS_DECISION_SUPERSEDE = "decision_supersede"

#: Run order = lifetime order, shortest first. The order is load-bearing only
#: for the receipt's reading order; no class depends on another's result.
CLASS_ORDER = (CLASS_CAPTURE_CARD, CLASS_PROPOSAL, CLASS_DECISION_SUPERSEDE,
               CLASS_DEAL_SIGNAL, CLASS_HYGIENE, CLASS_IDENTITY)

# The defaults a class may apply. `TRACK_IT` is the importance rule's answer
# and the one default that is never destructive: the question stops being
# asked and the row stays exactly where it was, open, on the plate.
DEFAULT_APPLY_LIKELY = "apply_likely"
DEFAULT_TRACK_IT = "track_it"
DEFAULT_LET_GO = "let_go"
DEFAULT_RESOLVE_SELF = "resolve_self"
#: DOORS1 1.4 — the recommend-only default. The proposal STOPS BEING OFFERED.
#: It is the only default in the table that changes nothing on the book: the
#: decision it named keeps the status its owner gave it, the proposal event
#: stays in history exactly as written, and what ends is the OFFER. That is
#: what "an unanswered supersede proposal never applies" means in a product
#: whose matcher is recommend-only by ruling (`decision_match.py`
#: RECOMMEND_ONLY_SUPERSEDES).
DEFAULT_RETRACT = "retract"
DEFAULTS = (DEFAULT_APPLY_LIKELY, DEFAULT_TRACK_IT, DEFAULT_LET_GO,
            DEFAULT_RESOLVE_SELF, DEFAULT_RETRACT)

#: A class that writes NOTHING has nothing to put back, and saying so is not
#: the same as forgetting to name a reverser. `assert_reversible` accepts this
#: sentinel ONLY on a class whose `destructive` flag is False — so it can
#: never be used to smuggle a drop past CONTRACT Rule 35.
REVERSER_NONE = "none"

#: Lane G, ruled 2026-09-07: the deal-signal lifetime. It is SHORTER than the
#: brain-proposal default (14) because a deal signal is a claim about a live
#: negotiation — a week-old guess about whether an org is a live deal has
#: either been settled by a fact (DEALNAG1's retirement rails) or was wrong.
DEAL_SIGNAL_TTL_DAYS = 7

#: The identity lifetime IDENT1 owns. Read from `identity_reconcile` when
#: that branch is present; this is the value the spec ruled, kept here ONLY
#: as the honest fallback for a tree without IDENT1, and never used to
#: contradict IDENT1's own constant.
IDENTITY_TTL_FALLBACK_DAYS = 14

#: The batch prefix a bare `undo` lists this run under (LEDGERFENCE1 shape).
EXPIRY_BATCH_PREFIX = "qex_"

#: The reverser class the proposal tombstones this module writes are stamped
#: with (registered in `brain_undo.REVERSERS`).
PROPOSAL_EXPIRY_CHANGE_CLASS = "brain_proposal_expiry"
#: The additive marker that reverser appends; `brain_proposals` folds it.
PROPOSAL_REOPENED_EVENT = "brain_proposal_reopened"

#: The run receipt this module writes (one per run, drop-empty).
RUN_EVENT_TYPE = "question_expiry_run"

#: The per-workspace switch. Same store as `auto_close_from_transcript`
#: (`commitment-policy`), DEFAULT ON, off by a plain phrase, FAIL-TO-DEFAULT
#: — the SPEC_FLOW1 switch contract. The key is spelled here rather than in
#: `commitment_policy.FLOW_SWITCH_KEYS` so this lane adds no line to a file
#: three other night-10 lanes are editing; at merge it belongs in that tuple.
EXPIRE_SWITCH_KEY = "questions.expire_to_default"
EXPIRE_SWITCH_DEFAULT = True
_SWITCH_SKILL_KEY = "commitment-policy"


class QuestionTTLError(ValueError):
    """A class name, default or lifetime outside the registry."""


# ---------------------------------------------------------------------------
# Seam resolvers — every one of them by NAME, none of them at import time
# ---------------------------------------------------------------------------

def _ident1():
    """IDENT1's identity-question seam, or None on a tree without it.

    Returns the module when it carries ALL THREE names the registry points
    at. A partial match is treated as absent on purpose: half a seam is how
    a lane invents a second default for a class it does not own."""
    try:
        import identity_reconcile as ir
    except Exception:  # pragma: no cover — the module ships beside this one
        return None
    needed = ("identity_aged_default", "identity_question_age_days",
              "IDENTITY_QUESTION_TTL_DAYS")
    return ir if all(hasattr(ir, n) for n in needed) else None


def _machine_sentinel() -> str:
    """ATTRIB2's `event_types.MACHINE` by NAME when that branch is present,
    and its own literal value when it is not. The sentinel a call site hands
    a writer to say "the product decided this, not the person"."""
    try:
        from event_types import MACHINE
        return MACHINE
    except Exception:
        return "machine"


#: Passed as `actor=` to every writer this engine DELEGATES to (fix round 1,
#: reviewer F-2). Two of the four acts a run writes go through somebody
#: else's writer, and until this was threaded they carried no `actor_kind`
#: and no actor field ATTRIB2 recognises — so `is_customer_act` answered
#: True and a timeout read as M's own gesture on the merged tree.
_MACHINE_SENTINEL = _machine_sentinel()


def _machine_actor(source_skill: str = SOURCE_SKILL) -> tuple:
    """ATTRIB2 — `(actor, actor_kind)` for an act this engine performs.

    An expiry is never the customer's gesture: nobody answered, which is the
    whole premise. Post-ATTRIB2 the answer comes from `resolve_actor` so the
    vocabulary is theirs; pre-ATTRIB2 the same two values are written
    directly rather than left blank, so a merged tree needs no backfill."""
    try:
        from event_types import MACHINE, resolve_actor
        return resolve_actor(MACHINE, source_skill=source_skill)
    except Exception:
        return (source_skill or "system", "machine")


def _actor_stamp(source_skill: str = SOURCE_SKILL) -> dict:
    actor, kind = _machine_actor(source_skill)
    try:
        from event_types import ACTOR_KIND_KEY
    except Exception:
        ACTOR_KIND_KEY = "actor_kind"  # noqa: N806 — ATTRIB2's own spelling
    return {"actor_id": actor, ACTOR_KIND_KEY: kind}


def _run_mode(explicit=None) -> Optional[str]:
    """What this run was TOLD it was, or nothing at all.

    FIX3 F3-6, fix pass 2 (review N-1). This module's run record has never
    carried a `fired_via`, so a resolver whose FLOOR is `scheduled` would
    mint a key onto every un-merged seat's ledger — the exact shape change
    fix pass 1 was told to scope away under L-1, and standing fence 8
    forbids it. So: a run that was told (the flag a typed brief renders, or
    the variable a merged seat exports) says so on its own record; a run
    that was told nothing keeps the shape it has always had. Additive, the
    same posture `triggered_by` itself uses.

    SPEC_NIGHTM3_LANES §5 P-2 (REVIEW_NIGHTM2_FIX3 I-2, ruling R-RW-5). On a
    MERGED VM seat a bare run told nothing now records a mode like its two
    siblings (`identity_reconcile`, `lifecycle_pass`) do — through
    `receipts.effective_fired_via`, whose merged-seat floor is `manual`. Off a
    merged seat the branch is never taken, so an un-merged run told nothing
    still carries no key at all (R-FIX3-6's shape; fence 8).
    """
    import os

    raw = explicit if explicit else os.environ.get("CR_FIRED_VIA")
    if not raw:
        return _merged_seat_mode()
    try:
        from receipts import normalize_fired_via

        return normalize_fired_via(raw) or None
    except Exception:  # noqa: BLE001 — a run record never fails on a stamp
        return str(raw).strip().lower() or None


def _merged_seat_mode() -> Optional[str]:
    """`manual` (or the forwarded mode) on a merged VM seat; None anywhere
    else. Import-tolerant: a run record never fails on a stamp, and a tree
    without the resolver says nothing rather than something false."""
    try:
        from receipts import _is_vm_seat, effective_fired_via
    except Exception:  # noqa: BLE001
        return None
    try:
        if not _is_vm_seat():
            return None
        return effective_fired_via(None) or None
    except Exception:  # noqa: BLE001
        return None


def _asked_by(explicit=None) -> Optional[str]:
    """The surface that asked for this run, or nothing (same posture)."""
    import os

    raw = explicit if explicit else os.environ.get("CR_TRIGGERED_BY")
    raw = str(raw or "").strip()
    return raw or None


def expiry_enabled(workspace_root, *, default: bool = EXPIRE_SWITCH_DEFAULT) -> bool:
    """Is `questions.expire_to_default` on for this workspace?

    Only a literal `False` turns it off. A missing key, a string, a malformed
    store and an unreadable file all read as ON — a typo in a config file is
    not a customer saying "keep asking me forever". Post-IDENT1 this is the
    identical contract to `commitment_policy.flow_switch_enabled`; it is
    spelled here so this lane edits no shared switch tuple."""
    if workspace_root is None:
        return default
    # NOTE FOR THE MERGE. Post-IDENT1, `commitment_policy.flow_switch_enabled`
    # is this identical contract, keyed on `FLOW_SWITCH_KEYS`. This key is not
    # in that tuple (that reader RAISES on an unknown key, deliberately), so
    # the store is read here the same way rather than through a reader that
    # would refuse it — and adding the key to that tuple is the one-line
    # tidy-up at merge. Read locally so this lane adds no line to a file three
    # other night-10 lanes are editing at the same time.
    try:
        from skill_config_writer import load_skill_config
        stored = load_skill_config(workspace_root, _SWITCH_SKILL_KEY) or {}
        cfg = stored.get("config") if isinstance(stored, dict) else None
        val = (cfg or {}).get(EXPIRE_SWITCH_KEY) if isinstance(cfg, dict) else None
        return False if val is False else default
    except Exception:
        return default


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

def _lifetime_capture_card(workspace_root=None) -> int:
    """Two days — `commitment_backlog_sweep.UNCONFIRMED_NAG_DAYS`, and the
    workspace's own `review_expiry_days` when it configured one. The card's
    own sentence reads THIS, so a workspace tuned to five days can never be
    told "two days" by the widget that is about to wait five."""
    from commitment_backlog_sweep import (UNCONFIRMED_NAG_DAYS,
                                          _configured_review_expiry_days)
    if workspace_root is not None:
        configured = _configured_review_expiry_days(workspace_root)
        if isinstance(configured, int) and configured > 0:
            return configured
    return int(UNCONFIRMED_NAG_DAYS)


def _lifetime_proposal(workspace_root=None) -> int:
    from commitment_policy import PROPOSAL_TTL_DAYS
    return int(PROPOSAL_TTL_DAYS)


def _lifetime_decision_supersede(workspace_root=None) -> int:
    """Four days — `commitment_policy.PROPOSAL_TTL_DAYS`, the number that
    already governs every other recommend-only proposal in the product. Read
    from its own home, never restated, so a decision that moves it moves this
    with it."""
    from commitment_policy import PROPOSAL_TTL_DAYS
    return int(PROPOSAL_TTL_DAYS)


def _lifetime_deal_signal(workspace_root=None) -> int:
    return int(DEAL_SIGNAL_TTL_DAYS)


def _lifetime_hygiene(workspace_root=None) -> int:
    from brain_proposals import DEFAULT_TTL_DAYS
    return int(DEFAULT_TTL_DAYS)


def _lifetime_identity(workspace_root=None) -> int:
    ir = _ident1()
    return int(getattr(ir, "IDENTITY_QUESTION_TTL_DAYS",
                       IDENTITY_TTL_FALLBACK_DAYS)) if ir \
        else IDENTITY_TTL_FALLBACK_DAYS


#: THE REGISTRY. One row per question class:
#:   lifetime   — a callable returning days, reading the number's OWN home
#:   default    — what applies when nobody answers
#:   reverser   — the registered `brain_undo.REVERSERS` key (or TUPLE of
#:                keys) that puts this class's default back. FIX ROUND 1
#:                (reviewer F-7): this field is now ALWAYS keys and never
#:                prose, so `assert_reversible` can walk the whole registry
#:                mechanically and the engine can REFUSE to write a
#:                destructive default whose reverser is not registered. The
#:                identity row named its two reversers in a sentence, which
#:                meant no check could ever read it.
#:   applier    — the dotted name of the rail that performs it (documentation
#:                that a reader can grep; the engine calls it by import)
#:   asks_on    — the customer surface the question renders on
#:   destructive— DOCUMENTATION: True when this class's default CAN close or
#:                drop a row. The importance rule is NOT gated on it — that
#:                question is asked per candidate, of the default the ROW
#:                would take (`expiry_candidates`), because a class whose
#:                default is harmless can still produce a row whose own
#:                default is a drop (the capture-card question with no likely
#:                answer is exactly that row)
CLASSES: dict = {
    CLASS_CAPTURE_CARD: {
        "label": "questions on a meeting card",
        "lifetime": _lifetime_capture_card,
        "default": DEFAULT_APPLY_LIKELY,
        "reverser": "commitment_reassign",
        "applier": "attribution_doors.apply_counterparty_default",
        "asks_on": "the meeting card",
        "destructive": False,
    },
    CLASS_PROPOSAL: {
        "label": "evidence chips riding an open row",
        "lifetime": _lifetime_proposal,
        "default": DEFAULT_RESOLVE_SELF,
        "reverser": "commitment_close",
        "applier": "commitment_policy_pass.resolve_stale_chips",
        "asks_on": "the row itself (never a queue row)",
        # A chip resolves itself by CLOSING on its own completion evidence or
        # RETRACTING. A timeout alone never closes anything (the close needs
        # `close_evidence` to pass AND the CUT-A switch to be on), so the
        # importance rule has nothing to bite on here — pinned by test.
        "destructive": False,
    },
    CLASS_DECISION_SUPERSEDE: {
        "label": "meetings that may have reversed a decision",
        "lifetime": _lifetime_decision_supersede,
        "default": DEFAULT_RETRACT,
        # NOTHING IS WRITTEN, so there is nothing to put back. The proposal
        # event stays in the ledger byte for byte; the decision it named was
        # never touched, by design (`decision_match.RECOMMEND_ONLY_SUPERSEDES`
        # — an unanswered proposal never applies). What ends at four days is
        # the OFFER: `render_decision_log` stops noting it, and the Staff
        # Meeting stops counting it as a question waiting on the customer.
        #
        # THE ALTERNATIVE, AND WHY NOT IT (recorded because the next reader
        # will ask). A `decision_supersede_retracted` event with a batch id
        # would give a per-proposal `undo` — and would need
        # `brain_undo.REVERSERS["decision_supersede_retraction"]`, in a file
        # DOORS1 does not own on this train (CLOSETRUTH1 does). A rule that
        # writes nothing needs no reverser and loses nothing, so the door
        # ships whole now and the write-based version is a follow-up if M
        # wants the undo.
        "reverser": REVERSER_NONE,
        "applier": "question_ttl._retract_supersede_proposals",
        "asks_on": "the decision log, on the decision's own line",
        "destructive": False,
    },
    CLASS_DEAL_SIGNAL: {
        "label": "deal signals waiting for a yes",
        "lifetime": _lifetime_deal_signal,
        "default": DEFAULT_LET_GO,
        "reverser": PROPOSAL_EXPIRY_CHANGE_CLASS,
        "applier": "question_ttl._expire_brain_proposals",
        "asks_on": "the Staff Meeting (MONEY)",
        "destructive": True,
    },
    CLASS_HYGIENE: {
        "label": "housekeeping questions",
        "lifetime": _lifetime_hygiene,
        "default": DEFAULT_LET_GO,
        "reverser": PROPOSAL_EXPIRY_CHANGE_CLASS,
        "applier": "question_ttl._expire_brain_proposals",
        "asks_on": "the Staff Meeting (HYGIENE)",
        "destructive": True,
    },
    CLASS_IDENTITY: {
        "label": "who-is-this questions",
        "lifetime": _lifetime_identity,
        "default": "identity_reconcile.identity_aged_default",
        # IDENT1's own two, as KEYS (fix round 1, reviewer F-7): a link it
        # made, and the tombstone it wrote over a row it let go.
        "reverser": ("person_link", "person_proposal_tombstone"),
        "applier": "identity_reconcile.run_identity_reconcile (rule 3)",
        "asks_on": "the Staff Meeting (IDENTITY)",
        "destructive": True,
    },
}


def registered_classes() -> tuple:
    """The class names, in run order."""
    return CLASS_ORDER


def class_spec(name: str) -> dict:
    if name not in CLASSES:
        raise QuestionTTLError(
            f"{name!r} is not a registered question class "
            f"(known: {', '.join(CLASS_ORDER)})")
    return CLASSES[name]


def class_reversers(name: str) -> tuple:
    """The `brain_undo.REVERSERS` keys that put this class's default back.

    EMPTY for a class that writes nothing (`REVERSER_NONE`): there is no way
    back because there was no way forward. `assert_reversible` is what makes
    that claim checkable rather than assertable."""
    r = class_spec(name)["reverser"]
    if r == REVERSER_NONE:
        return ()
    return tuple(r) if isinstance(r, (tuple, list)) else (str(r),)


def unregistered_reversers(name: str) -> tuple:
    """The class's reverser keys that `brain_undo` does NOT know, in order.

    Empty means every way back this class promises actually exists."""
    try:
        from brain_undo import REVERSERS
    except Exception:  # pragma: no cover — the module ships beside this one
        return class_reversers(name)
    return tuple(k for k in class_reversers(name) if k not in REVERSERS)


def assert_reversible(name: str) -> None:
    """CONTRACT Rule 35 part 1, ENFORCED AT RUN TIME (fix round 1, F-7).

    "A registry row whose reverser is not registered in
    `brain_undo.REVERSERS` is a defect" was a sentence in a contract and
    nothing checked it: with the entry removed the engine still tombstoned
    every candidate and only the `undo` failed, afterwards, which is exactly
    what `brain_proposals.expire_stale` has been doing silently. The engine
    now asks this BEFORE any destructive write and refuses the class."""
    if class_spec(name)["reverser"] == REVERSER_NONE:
        # The sentinel is legal ONLY on a class that cannot write a drop. A
        # destructive class claiming "nothing to put back" is the exact
        # sentence Rule 35 exists to refuse, so it is refused by name.
        if class_spec(name).get("destructive"):
            raise QuestionTTLError(
                f"the {name!r} question class declares no reverser while "
                "declaring itself destructive — a default that can close or "
                "drop a row must name a registered way back")
        return
    missing = unregistered_reversers(name)
    if missing:
        raise QuestionTTLError(
            f"the {name!r} question class may not expire: its way back "
            f"({', '.join(missing)}) is not registered in "
            "brain_undo.REVERSERS — a default with no reverser is a "
            "permanent drop, and this engine never writes one")


def class_lifetime_days(name: str, workspace_root=None) -> int:
    """The class's lifetime in days, from the module that owns the number."""
    return int(class_spec(name)["lifetime"](workspace_root))


def lifetimes(workspace_root=None) -> dict:
    """{class: days} — the whole table, for a receipt or a report."""
    return {c: class_lifetime_days(c, workspace_root) for c in CLASS_ORDER}


# ---------------------------------------------------------------------------
# THE READ-SIDE GATE (FOLD1-B 1.2 item 1)
# ---------------------------------------------------------------------------
#
# The engine above decides what a question BECOMES when nobody answers it.
# This predicate decides whether a question may still be OFFERED. They are
# two halves of one rule and TTL1 only ever shipped the first: on the
# operator's book the Staff Meeting still fronted identity questions first
# asked in June, because the card ranks and caps but never asks how old a
# question is. Ranking oldest-first makes that worse, not better — the rows
# past their lifetime are exactly the rows the rotation puts at the front.
#
# This is a READ gate and never a second expiry: it writes nothing, it
# applies no default, it schedules nothing. The row it hides is handed to
# its class default by the next `run_question_expiry`, with that run's
# receipt and that run's `undo`, exactly as before.

#: A row's SHAPE (the Staff Meeting's own grouping) → the question class it
#: belongs to. The same mapping `_brain_proposal_questions` /
#: `_identity_questions` already make when they read the queue; named here so
#: the read gate and the expiry engine cannot drift onto two answers.
SHAPE_CLASSES = {
    "money": CLASS_DEAL_SIGNAL,
    "identity": CLASS_IDENTITY,
    "hygiene": CLASS_HYGIENE,
}


def question_class_of(row) -> Optional[str]:
    """The registered question class this row belongs to, or None.

    None means "no registered class", which is not the same as "expired":
    a row this module does not govern is nobody's to hide, so the gate
    below passes it through untouched.
    """
    if not isinstance(row, dict):
        return None
    named = str(row.get("question_class") or "").strip()
    if named in CLASSES:
        return named
    return SHAPE_CLASSES.get(str(row.get("shape") or "").strip())


def row_age_days(row, *, now_iso=None) -> Optional[int]:
    """How many days this row has been waiting, or None when it cannot be read.

    THE CLOCK IS LAST ACTIVITY, not the birthday — the same order
    `_shape_questions` reads (fix round 1, reviewer F-1), so a row the
    customer put back with `undo` is measured from the reopen on the read
    side too. A row that already carries `age_days` (the needs-your-call
    queue view computes one) is believed rather than re-derived: two clocks
    over one row is how a gate and a title come to disagree.
    """
    if not isinstance(row, dict):
        return None
    age = row.get("age_days")
    if isinstance(age, bool):
        age = None
    if isinstance(age, int):
        return age
    now = _now(now_iso)
    for key in ("last_activity_at", "opened_at", "captured_ts", "ts"):
        got = _age_days(row.get(key), now)
        if got is not None:
            return got
    return None


def within_lifetime(row, workspace_root, *, now_iso) -> bool:
    """May this question still be OFFERED on a surface? (FOLD1-B 1.2 item 1)

    True for every row a surface may render. False only for a row whose age
    has reached its class lifetime AND whose default the expiry engine would
    actually apply.

    THAT SECOND CLAUSE IS THE WHOLE SAFETY PROPERTY, so it is spelled out.
    `expiry_candidates` never expires a row the importance rule holds — one
    that is overdue, due this week, or carries a client or money — it applies
    "track it" and the row stays open. A read gate that hid such a row would
    hide it FOREVER: past the bar on every future fire, and never taken by
    any expiry run. So this predicate asks the importance rule the same
    question, of the same default, in the same order the engine asks it, and
    a held row stays on the card. The design rule's fourth clause says the
    same thing from the other end: whatever a fold hides, it never hides an
    overdue item or one due this week.

    Fails OPEN, three times over: an unregistered class, an unreadable clock
    and a lifetime that cannot be computed all return True. A question nobody
    can date is a question the customer still gets to answer.
    """
    cls = question_class_of(row)
    if cls is None:
        return True
    age = row_age_days(row, now_iso=now_iso)
    if age is None:
        return True
    try:
        ttl = class_lifetime_days(cls, workspace_root)
    except Exception as exc:  # pragma: no cover — a seam that cannot be read
        sys.stderr.write(f"[question_ttl] {cls} lifetime unreadable: {exc}\n")
        return True
    if age < ttl:
        return True
    raw = class_spec(cls)["default"]
    if cls == CLASS_CAPTURE_CARD and not row.get("has_default"):
        # The same branch `expiry_candidates` takes: a card question with no
        # likely answer is a LET GO, so the importance rule gets asked of it.
        raw = DEFAULT_LET_GO
    if raw != DEFAULT_LET_GO:
        return False
    try:
        hold = importance_hold(row.get("row") if isinstance(row.get("row"), dict)
                               else row,
                               now=_now(now_iso),
                               client_ids=_client_ids(workspace_root),
                               workspace_root=workspace_root)
    except Exception as exc:  # pragma: no cover — degrade to SHOWING the row
        sys.stderr.write(f"[question_ttl] importance read degraded: {exc}\n")
        return True
    return bool(hold)


#: The number as a CEO reads it. FIX ROUND 1 (reviewer F-8): one spelling,
#: one home. `expiry_note` rendered "2 days" while the card's own sentence
#: rendered "two days" — one number, two spellings, inside one lane. Words up
#: to seven (a lifetime longer than a week is read as a figure), which is the
#: spelling the shipped card constants already use.
_DAY_WORDS = {1: "one day", 2: "two days", 3: "three days", 4: "four days",
              5: "five days", 6: "six days", 7: "seven days"}


def days_phrase(days: int) -> str:
    """"two days" — THE spelling of a lifetime on a customer surface.

    `attribution_doors` reads this rather than keeping its own copy, so the
    card's sentence and the class's note can never disagree about how a
    number is written."""
    n = int(days)
    return _DAY_WORDS.get(n, f"{n} days")


def expiry_note(name: str, workspace_root=None) -> str:
    """The ONE sentence a surface says about what happens to an unanswered
    question of this class. Every renderer of a question reads this instead
    of spelling its own number, which is how "two days" ended up hard-typed
    in three constants beside a drain that reads a configurable one.

    Callers today: `attribution_doors.card_expiry_sentence` (the meeting
    card's own line, rendered on every card that asks anything) and, through
    it, `build_card_questions_view`'s section note."""
    days = class_lifetime_days(name, workspace_root)
    phrase = days_phrase(days)
    default = class_spec(name)["default"]
    if default == DEFAULT_APPLY_LIKELY:
        tail = "the likely answer applies on its own"
    elif default == DEFAULT_LET_GO:
        tail = "it is let go"
    elif default == DEFAULT_TRACK_IT:
        tail = "it stays on your plate, just not as a question"
    else:
        tail = "it settles on its own evidence"
    return (f"after {phrase} with no answer, {tail} — "
            "`undo` brings it back")


# ---------------------------------------------------------------------------
# The importance rule (Lane G, ruled 2026-09-07)
# ---------------------------------------------------------------------------

#: A row due inside this many days is "due this week" for the importance rule.
DUE_SOON_DAYS = 7

IMPORTANCE_OVERDUE = "overdue"
IMPORTANCE_DUE_SOON = "due this week"
IMPORTANCE_CLIENT = "a client is on it"
IMPORTANCE_MONEY = "money is on it"


def _row_data(row) -> dict:
    if not isinstance(row, dict):
        return {}
    d = row.get("data")
    if isinstance(d, dict):
        return d
    return row


def _row_due(row) -> Optional[str]:
    d = _row_data(row)
    due = d.get("due") or d.get("due_date") or (row or {}).get("due")
    return str(due)[:10] if isinstance(due, str) and due.strip() else None


def _client_ids(workspace_root) -> set:
    """Person and org ids whose `relationship_type` is `client`."""
    out: set = set()
    if workspace_root is None:
        return out
    try:
        import json

        from entities_io import entities_collection
        doc = json.loads((Path(workspace_root) / "_hq" / "data"
                          / "entities.json").read_text(encoding="utf-8"))
        for kind in ("people", "orgs"):
            for rec in entities_collection(doc, kind) or []:
                if str((rec or {}).get("relationship_type") or "").strip().lower() \
                        == "client":
                    rid = (rec or {}).get("id")
                    if rid:
                        out.add(str(rid))
    except Exception:
        return out
    return out


def _workspace_today(now: datetime, workspace_root=None) -> datetime:
    """MIDNIGHT of the WORKSPACE'S own day, as a UTC-comparable datetime.

    FIX ROUND 1 (reviewer F-9). "Overdue" and "due this week" are calendar
    questions, and a calendar question answered on UTC midnight is wrong for
    most of the day on every seat west of Greenwich: a row due today reads
    overdue from 5pm Pacific onwards, and the importance rule then holds a
    question it did not need to hold. The house convention for turning a
    stamp into a workspace-local DATE is `tz.localize_date` (G14 / TZDATE2),
    so that is what this reads; a workspace with no configured zone, or no
    workspace at all, falls back to the UTC date exactly as before."""
    base = now or datetime.now(timezone.utc)
    try:
        from tz import localize_date
        local = localize_date(base.isoformat(), workspace_root)
        if local:
            return datetime.strptime(local[:10], "%Y-%m-%d").replace(
                tzinfo=timezone.utc)
    except Exception:
        pass
    return base.replace(hour=0, minute=0, second=0, microsecond=0)


def importance_hold(row, *, now, client_ids=None, workspace_root=None) -> Optional[str]:
    """THE IMPORTANCE RULE, pure. The plain-words reason this row's question
    may not expire to a destructive default, or None.

    An expiry NEVER closes or drops a row that is overdue, due this week, or
    carries a client or money. It applies the "track it" default instead: the
    question goes away, the row stays open and keeps its place. This is the
    same ordering CUT-D pinned for the plate cap (overdue / this-week are
    never what a cap hides) applied to the other end of a row's life."""
    d = _row_data(row)
    due = _row_due(row)
    if due:
        try:
            due_dt = datetime.strptime(due, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            due_dt = None
        if due_dt is not None:
            today = _workspace_today(now, workspace_root)
            if due_dt < today:
                return IMPORTANCE_OVERDUE
            if due_dt <= today + timedelta(days=DUE_SOON_DAYS):
                return IMPORTANCE_DUE_SOON
    ids = client_ids if client_ids is not None else set()
    for key in ("counterparty_id", "owner_id", "org_id", "person_id"):
        val = d.get(key)
        if isinstance(val, str) and val in ids:
            return IMPORTANCE_CLIENT
    for val in (d.get("person_ids") or []):
        if isinstance(val, str) and val in ids:
            return IMPORTANCE_CLIENT
    if _has_money(d):
        return IMPORTANCE_MONEY
    return None


#: The row fields a money figure can be written into. Named once because two
#: readers now ask the same question (the question default's importance hold
#: and the unconfirmed door's money hold, DOORS1 1.2).
MONEY_FIELDS = ("amount", "deal_value", "value", "invoice_amount")
#: The row text a money figure can be written into.
MONEY_TEXT_FIELDS = ("title", "evidence", "note")


def _has_money(d: dict) -> bool:
    """Money on the row: an explicit amount field, or a currency figure in the
    row's own words. Deliberately narrow — "budget" as a topic is not money
    on a row, and a false positive here parks a question forever.

    THE WORDS ARE READ WITH `capture_gate._MONEY_RE`, NOT A SECOND REGEX
    (DOORS1 1.2, M's ruling R2 of 2026-09-13). This used to carry its own
    `[$€£]\\s?\\d`, which is the symbol arm of the caution rail's detector and
    only that arm — so "the D Creations invoice, 1,800 dollars" read as money
    to one half of the product and as nothing to the other. R2 exempts the
    money lane from the two-day unconfirmed door, and a door with its own
    private definition of money is a door that disagrees with the rail on the
    exact rows the ruling is about. One definition, imported.

    An agent read of the live book before this was written found NOTHING
    writing `invoice_amount` or `amount` onto a meeting-captured row, which is
    why the text arm — not the field arm — is what actually catches the
    inbound invoice the record names (B2.2, lapsed twice).

    The rail's detector failing to import must never turn the hold OFF
    silently: the fallback is the narrow symbol arm this function used to
    carry, which is strictly weaker and never wider."""
    for key in MONEY_FIELDS:
        v = d.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
            return True
        if isinstance(v, str) and v.strip():
            return True
    text = " ".join(str(d.get(k) or "") for k in MONEY_TEXT_FIELDS)
    try:
        from capture_gate import _MONEY_RE
        return bool(_MONEY_RE.search(text))
    except Exception:  # noqa: BLE001 — weaker, never wider, never silent-off
        import re
        return bool(re.search(r"[$€£]\s?\d", text))


# ---------------------------------------------------------------------------
# Reading the open questions, by class
# ---------------------------------------------------------------------------

def _now(now_iso) -> datetime:
    from event_time import parse_ts
    return parse_ts(now_iso) or datetime.now(timezone.utc)


def _events(workspace_root, events=None) -> list:
    if events is not None:
        return list(events)
    import events_io
    evs, _skipped = events_io.load_events_owner_scoped(workspace_root)
    return evs


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _age_days(ts, now: datetime) -> Optional[int]:
    from event_time import parse_ts
    dt = parse_ts(ts)
    if dt is None:
        return None
    try:
        return (now - dt).days
    except Exception:
        return None


#: The acts the PRODUCT performs on a row, which the house movement set
#: counts as movement (they are, for the stuck/blocked question they were
#: written for — somebody's attention did reach the row) but which are not
#: the customer answering a question about it. Fix round 2, reviewer R-2.
# AT MERGE (night 10, M's ruling R-3 via CLEANUP1): `draft_created` — our own
# unsent draft — is no longer in `commitment_activity.MOVEMENT_EVENT_TYPES`
# at all (`UNSENT_DRAFT_EVENT_TYPE`), so it is not subtracted here either;
# this set names only the machine acts that ARE still movement for the
# brain family and must not restart a question's clock.
MACHINE_ACT_TYPES = frozenset({ "outreach_sent"})


def card_movement_types():
    """The movement set the CAPTURE-CARD clock measures against:
    `commitment_activity.MOVEMENT_EVENT_TYPES` minus the machine's own acts.

    SUBTRACTION from the one canonical set, never a restated list — the same
    idiom `commitment_backlog_sweep._movement_types_without_reopen` uses, and
    for the same reason: a movement type added to that set tomorrow must
    shield a question the same day, and a hand-written copy keeps that
    promise only for the types somebody remembered.

    The two families agree after this: a machine act restarts NEITHER clock
    (the brain family's `last_activity_at` has only ever moved on a customer
    reopen). Everything the customer or the counterparty does is untouched —
    a re-date, a re-title, a re-owner, an adjudication, a reopen.

    Returns `None` if the canonical set cannot be read, which is the
    parameter's own "use the default" — the pre-fix behaviour, i.e. the row
    is HELD rather than answered for the customer. A degraded import must
    never make the product act more."""
    try:
        from commitment_activity import MOVEMENT_EVENT_TYPES
        return frozenset(MOVEMENT_EVENT_TYPES) - MACHINE_ACT_TYPES
    except Exception:  # pragma: no cover — degrade to the house default
        return None


def _capture_card_questions(workspace_root, *, now, events=None) -> list:
    """Open capture-card questions: rows in the needs-review tier carrying a
    `who_is_you` question, with their age and whether a default exists.

    THE CLOCK IS LAST ACTIVITY, NOT CAPTURE (fix round 1, reviewer F-1).
    `commitment_backlog_sweep.last_activity_map` is the movement baseline the
    review-expiry drain this job runs immediately after already measures
    against — the same map, unnarrowed, so a `commitment_reopened` written by
    an `undo` shields the row exactly as that rail's own prose promises ("a
    row the user put back stays put back"). Measuring from the capture stamp
    instead meant a row the customer restored on Monday was let go again on
    Tuesday: an undo with a one-day life. The map is seeded with each row's
    own capture ts, so a row that has never moved is still measured from when
    it was written, and the numbers are unchanged for everything untouched.

    AND A MACHINE ACT IS NOT ACTIVITY (fix round 2, reviewer R-2). The house
    movement set counts a `draft_created` and an `outreach_sent`, so a draft
    the product wrote and a chase the product sent both restarted this
    two-day clock — while the BRAIN family's clock moves only on the
    customer's own reopen. Two question families, two answers to the same
    question, from one engine. F-1's rule is the one that survives: activity
    means the customer's act or the counterparty's. `CARD_MOVEMENT_TYPES`
    subtracts the machine's two acts and nothing else, so the customer's
    edit and the customer's `undo` shield the row exactly as before."""
    from attribution_doors import question_default, question_of
    from commitment_backlog_sweep import last_activity_map
    from cru_match import _commitment_id, load_needs_review
    from event_time import event_time
    path = str(_events_path(workspace_root))
    rows = load_needs_review(path, workspace_root=str(workspace_root))
    try:
        activity = last_activity_map(path,
                                     movement_types=card_movement_types())
    except Exception as exc:  # pragma: no cover — degrade to the capture ts
        sys.stderr.write(f"[question_ttl] activity map degraded: {exc}\n")
        activity = {}
    out = []
    for ev in rows or []:
        q = question_of(ev)
        if not q or not q.get("options"):
            continue
        cid = _commitment_id(ev)
        seen = activity.get(cid)
        age = (_age_days(seen.isoformat(), now)
               if hasattr(seen, "isoformat") else _age_days(event_time(ev), now))
        out.append({
            "question_class": CLASS_CAPTURE_CARD,
            "id": cid,
            "age_days": age,
            "has_default": bool(question_default(ev)),
            "row": ev,
        })
    return out


def _queue_rows(workspace_root, *, now_iso=None) -> list:
    """EVERY open queue row the product is holding, deduped by id.

    The union of two reads, and both halves are load-bearing:

      * `load_open_proposals(ws, None, ...)` — the DIAGNOSTIC read the module
        documents for exactly this ("a withheld-but-open row must still be
        visible to an audit"). It is the only read that includes the LEGACY
        adapter families, which is where the queue actually lives: the person
        proposals, the commitment reviews, the org/project rows, the
        dont-forget rows. A read scoped to brain-family events alone would
        have counted a handful of rows on a book carrying scores of them.
      * `_open_brain_proposals(events, now=None)` — brain rows past their own
        `ttl_days`, which the projector's computed-TTL screen HIDES from
        every render while leaving them live in the ledger forever. Nothing
        tombstones those until the weekly cleanup's silent `expire_stale`,
        which carries no batch and no way back.

    The class's lifetime is what retires a question. The render screen only
    ever decided what one page showed."""
    from brain_proposals import (_load_events, _open_brain_proposals,
                                 load_open_proposals)
    rows: dict = {}
    try:
        for it in load_open_proposals(workspace_root, None,
                                      now_iso=now_iso) or []:
            if it.get("id"):
                rows[str(it["id"])] = it
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"[question_ttl] queue read degraded: {exc}\n")
    try:
        for it in _open_brain_proposals(_load_events(workspace_root),
                                        now=None) or []:
            rows.setdefault(str(it.get("id") or ""), it)
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"[question_ttl] stale-brain read degraded: {exc}\n")
    rows.pop("", None)
    return [r for r in rows.values() if r.get("tier") != "auto"]


def _shape_questions(workspace_root, *, now, shape: str, klass: str,
                     now_iso=None) -> list:
    out = []
    for it in _queue_rows(workspace_root, now_iso=now_iso):
        if (it.get("shape") or "hygiene") != shape:
            continue
        out.append({
            "question_class": klass,
            "id": it.get("id"),
            "kind": it.get("kind"),
            # THE CLOCK IS LAST ACTIVITY (fix round 1, reviewer F-1).
            # `opened_at` is a BIRTHDAY: a row the customer put back with
            # `undo` still carries the day it was first asked, so aging from
            # it let the very next daily run expire it again — the undo was
            # a one-day promise, in every class. `last_activity_at` moves to
            # the reopen (`brain_proposals._open_brain_proposals` folds it),
            # which is the same rule the neighbouring review drain states in
            # its own prose. Legacy adapter rows that carry no such field
            # fall back to `opened_at` and are unchanged.
            "age_days": _age_days(it.get("last_activity_at")
                                  or it.get("opened_at"), now),
            "row": it,
        })
    return out


def _brain_proposal_questions(workspace_root, *, now, shape: str,
                              kinds=None, events=None) -> list:
    """Open queue rows of one SHAPE (the Staff Meeting's own grouping)."""
    return _shape_questions(
        workspace_root, now=now, shape=shape,
        klass=(CLASS_DEAL_SIGNAL if shape == "money" else CLASS_HYGIENE),
        now_iso=_iso_or_none(now))


def _identity_questions(workspace_root, *, now, events=None) -> list:
    """Open identity questions. IDENT1 owns the cluster shape; without that
    branch the Staff Meeting's own IDENTITY lane is the honest stand-in for
    counting, and NOTHING is applied (see `_apply_identity`)."""
    return _shape_questions(workspace_root, now=now, shape="identity",
                            klass=CLASS_IDENTITY, now_iso=_iso_or_none(now))


def _iso_or_none(now) -> Optional[str]:
    return now.isoformat() if isinstance(now, datetime) else None


def _proposal_chips(workspace_root, *, now, events=None) -> list:
    """Open POLICY1 chips past nothing yet — the whole live set, with age.
    The chip's own rail decides its fate; this read exists so the report can
    count the class beside the others."""
    import commitment_policy as policy
    evs = _events(workspace_root, events)
    try:
        ledger = policy.newest_open_proposals(evs)
    except Exception:
        return []
    out = []
    for cid, rec in (ledger or {}).items():
        out.append({
            "question_class": CLASS_PROPOSAL,
            "id": str(cid),
            # A chip's clock runs from the EVIDENCE that raised it, which is
            # the same stamp `chip_ttl_action` reads — never a second one.
            #
            # FIX ROUND 1, reviewer F-1 — and this class is the DELIBERATE
            # exception to "age from last activity". A chip is not a row put
            # back by an undo: `newest_open_proposals` already returns only
            # the NEWEST open chip per commitment, so a chip re-raised after
            # a reversal arrives with a fresh `evidence_ts` of its own and
            # the clock restarts without any help from here. Reading a
            # second stamp would make this module's count disagree with
            # `chip_ttl_action`'s decision, which is the rail that actually
            # acts — and the engine only ever counts this class.
            "age_days": _age_days(rec.get("evidence_ts"), now),
            "row": dict(rec, commitment_id=str(cid)),
        })
    return out


#: The event a supersede proposal is written as, and the two events that
#: SETTLE the decision it named. Literals, not imports: `decision_match` names
#: them too, and this reader has to keep parsing rows written by every version
#: that ever shipped.
SUPERSEDE_PROPOSED_EVENT = "decision_supersede_proposed"
_SUPERSEDE_SETTLED_EVENTS = ("decision_superseded", "decision_resolved")


def open_supersede_proposals(workspace_root, *, now=None, events=None) -> list:
    """Every supersede proposal still waiting on a human, newest per decision.

    OPEN means three things, and each one closes a way a settled row could be
    counted as a live question:

      * the decision it names has not been SETTLED since the proposal was
        written (`decision_superseded` / `decision_resolved`) — somebody
        adjudicated, which is the answer the proposal was asking for;
      * only the NEWEST proposal per decision is a question. The record found
        these written in bursts (100 / 109 / 74 on three days), many of them
        re-proposals of the same decision from a later meeting; counting all
        of them would report one unanswered question as ninety;
      * a proposal with no decision id is not a question anybody can answer,
        and is dropped rather than aged.

    Age runs from the proposal's own instant, which is the only clock it has.
    """
    from event_time import event_time
    evs = _events(workspace_root, events)
    newest: dict = {}
    settled_at: dict = {}
    for ev in evs:
        t = ev.get("type") or ev.get("event")
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        did = d.get("decision_id")
        if not did:
            continue
        when = event_time(ev)
        if t in _SUPERSEDE_SETTLED_EVENTS:
            prev = settled_at.get(did)
            if prev is None or (when or "") > prev:
                settled_at[did] = when or ""
            continue
        if t != SUPERSEDE_PROPOSED_EVENT:
            continue
        cur = newest.get(did)
        if cur is None or (when or "") >= (cur[0] or ""):
            newest[did] = (when, ev, d)
    out = []
    for did, (when, ev, d) in newest.items():
        done = settled_at.get(did)
        if done and when and done >= when:
            continue
        out.append({
            "question_class": CLASS_DECISION_SUPERSEDE,
            "id": str(did),
            "age_days": _age_days(when, now),
            "row": {"decision_id": str(did), "title": d.get("title") or "",
                    "evidence": d.get("evidence") or "", "score": d.get("score"),
                    "proposed_at": when},
        })
    return out


def supersede_proposal_expired(proposed_at, *, now=None,
                               workspace_root=None) -> bool:
    """Has this proposal outlived its four days? THE predicate, so the decision
    log and the expiry engine can never disagree about which rows are still an
    offer."""
    age = _age_days(proposed_at, now or _now(None))
    if age is None:
        return False
    return age >= _lifetime_decision_supersede(workspace_root)


_READERS = {
    CLASS_CAPTURE_CARD: lambda ws, now, evs: _capture_card_questions(
        ws, now=now, events=evs),
    CLASS_PROPOSAL: lambda ws, now, evs: _proposal_chips(ws, now=now, events=evs),
    CLASS_DECISION_SUPERSEDE: lambda ws, now, evs: open_supersede_proposals(
        ws, now=now, events=evs),
    CLASS_DEAL_SIGNAL: lambda ws, now, evs: _brain_proposal_questions(
        ws, now=now, shape="money", events=evs),
    CLASS_HYGIENE: lambda ws, now, evs: _brain_proposal_questions(
        ws, now=now, shape="hygiene", events=evs),
    CLASS_IDENTITY: lambda ws, now, evs: _identity_questions(ws, now=now, events=evs),
}


def open_questions(workspace_root, *, now_iso=None, events=None,
                   classes=None) -> dict:
    """{class: [question rows]} — every open question the product is holding,
    by class, each with its age in days. Pure read."""
    now = _now(now_iso)
    evs = _events(workspace_root, events)
    out: dict = {}
    for name in (classes or CLASS_ORDER):
        try:
            out[name] = _READERS[name](workspace_root, now, evs)
        except Exception as exc:  # a class that cannot be read is EMPTY, loudly
            sys.stderr.write(f"[question_ttl] {name} read skipped: {exc}\n")
            out[name] = []
    return out


def expiry_candidates(workspace_root, *, now_iso=None, events=None,
                      classes=None) -> dict:
    """{class: [candidates]} — the open questions AT OR PAST their class's
    lifetime, each carrying the default that would apply and the importance
    hold that would change it. Pure: it decides, it never writes.

    THE BOUNDARY IS `>=`, and it is cited (fix round 1, reviewer F-9). With
    `>` a two-day class acted on day THREE, because `.days` floors: the card
    promised "after two days the likely answer applies" and the engine
    waited three, and a housekeeping row was hidden from every render for a
    whole extra day before anything would settle it. Both rails this engine
    sits beside act ON the day — `review_expiry_candidates` screens on
    `seen <= now - 2d` and `expire_stale` on `opened + ttl < now` — so `>=`
    on elapsed days is the SAME instant they use, not a third convention."""
    now = _now(now_iso)
    ids = _client_ids(workspace_root)
    everything = open_questions(workspace_root, now_iso=now_iso, events=events,
                               classes=classes)
    out: dict = {}
    for name, rows in everything.items():
        spec = class_spec(name)
        ttl = class_lifetime_days(name, workspace_root)
        picked = []
        for r in rows:
            age = r.get("age_days")
            if age is None or age < ttl:
                continue
            entry = dict(r)
            entry["ttl_days"] = ttl
            raw = spec["default"]
            if name == CLASS_CAPTURE_CARD and not r.get("has_default"):
                # F-5's other half: a card question with no likely answer is
                # LET GO by the review-expiry lapse, not applied here.
                raw = DEFAULT_LET_GO
                entry["applied_by_rail"] = "commitment_backlog_sweep"
            # THE IMPORTANCE RULE is asked of the DEFAULT THIS ROW WOULD TAKE,
            # not of its class. A class-level test would have missed the one
            # place the rule most has to bite: the capture-card row with no
            # likely answer, whose class default is harmless (apply the likely
            # answer) but whose OWN default is a drop.
            hold = (importance_hold(r.get("row"), now=now, client_ids=ids,
                                    workspace_root=workspace_root)
                    if raw == DEFAULT_LET_GO else None)
            entry["importance_hold"] = hold
            entry["default"] = DEFAULT_TRACK_IT if hold else raw
            picked.append(entry)
        out[name] = picked
    return out


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

def mint_expiry_batch_id(now_iso=None) -> str:
    """One batch id per expiry RUN — `undo` reverses the whole run."""
    import secrets
    dt = _now(now_iso)
    return (f"{EXPIRY_BATCH_PREFIX}{dt.strftime('%Y%m%dT%H%M%SZ')}"
            f"-{secrets.token_hex(4)}")


def _expire_brain_proposals(workspace_root, candidates, *, batch_id,
                            now_iso=None, apply: bool = True) -> dict:
    """Tombstone aged Living-Brain proposals (the deal-signal and hygiene
    classes) UNDER THE RUN'S BATCH ID, so one `undo` reopens them.

    The shipped `brain_proposals.expire_stale` writes the same tombstone but
    carries no batch and no change class, so nothing could ever put one back
    — a silent, permanent drop. This writes the identical event plus the two
    fields `brain_undo` needs, which is what makes an automatic expiry legal
    at all."""
    # THE FENCE ON WHAT THIS WRITER MAY TOUCH. The queue read is deliberately
    # wide (it is the only read that sees the legacy adapter families, which
    # is where the backlog actually lives), so the WRITE has to be narrow:
    # only a BRAIN-FAMILY row can take a `brain_proposal_expired` tombstone,
    # because that is the only family whose reader folds it and whose reverser
    # is registered here. A person proposal, a commitment review, an
    # org/project row and a dont-forget row each have their OWN tombstone
    # shape and their OWN reverser; writing a brain tombstone over one of them
    # would be an expiry nothing could reverse and nothing would honour.
    # They are COUNTED for the report and named as their rail's, never
    # expired from here.
    foreign = [c for c in candidates
               if (c.get("row") or {}).get("source_family") not in (None, "brain")]
    rows = [c for c in candidates
            if c.get("default") == DEFAULT_LET_GO and c not in foreign]
    out = {"n_expired": 0, "expired": [], "n_held_important": 0,
           "n_other_rail": len(foreign),
           "other_rails": sorted({str((c.get("row") or {}).get("source_family"))
                                  for c in foreign})}
    out["n_held_important"] = sum(1 for c in candidates
                                  if c.get("importance_hold"))
    if not rows or not apply:
        out["n_planned"] = len(rows)
        return out
    # RULE 35 PART 1, ENFORCED (fix round 1, reviewer F-7). The way back has
    # to EXIST before the drop is written, not merely be claimed by the
    # registry. Removing the `brain_proposal_expiry` entry used to leave the
    # tombstones written and only the later `undo` failing — a permanent
    # drop, which is precisely what this engine exists not to do. It refuses
    # the class LOUDLY instead: the questions stay on the page.
    klasses = {c.get("question_class") for c in rows if c.get("question_class")}
    for k in sorted(klasses):
        try:
            assert_reversible(k)
        except QuestionTTLError as exc:
            sys.stderr.write(f"[question_ttl] {exc}\n")
            out.update({"status": "refused_no_reverser", "why": str(exc),
                        "n_planned": len(rows), "n_expired": 0})
            return out
    from event_gate import append_event
    stamp = _actor_stamp()
    events = []
    for c in rows:
        it = c.get("row") or {}
        data = {
            "proposal_id": it.get("id"),
            "kind": it.get("kind"),
            "fingerprint": it.get("fingerprint"),
            "detector": it.get("detector"),
            "opened_ts": it.get("opened_at"),
            "question_class": c.get("question_class"),
            "ttl_days": c.get("ttl_days"),
            "brain_batch_id": batch_id,
            "brain_change_class": PROPOSAL_EXPIRY_CHANGE_CLASS,
        }
        data.update(stamp)
        events.append({"type": "brain_proposal_expired",
                       "source_skill": SOURCE_SKILL, "data": data})
    append_event(_events_path(workspace_root), events, holder=SOURCE_SKILL)
    out["n_expired"] = len(events)
    out["expired"] = [c.get("id") for c in rows]
    out["n_planned"] = len(rows)
    return out


def _track_it(workspace_root, candidates, *, batch_id, now_iso=None,
              apply: bool = True) -> dict:
    """The importance rule's own act: the question goes away, the row stays.

    For a row in the needs-review tier that means CLEARING the review flag —
    the row leaves the queue and takes its place on the plate, open, with its
    date and its counterparty untouched. `clear_review_flags` stamps
    `commitment_confirm`, whose registered reverser puts the row back in the
    queue, so "track it" is as reversible as a drop."""
    rows = [c for c in candidates
            if c.get("importance_hold") and c.get("row") is not None
            and c.get("question_class") == CLASS_CAPTURE_CARD]
    out = {"n_tracked": 0, "tracked": [], "n_planned": len(rows)}
    if not rows or not apply:
        return out
    from commitment_state import clear_review_flags
    actor, _kind = _machine_actor()
    for c in rows:
        try:
            res = clear_review_flags(
                workspace_root, c["id"], cleared_by=actor,
                source_skill=SOURCE_SKILL,
                note=f"kept on your plate — {c['importance_hold']}",
                mint_now_iso=now_iso,
                # FIX ROUND 1 (reviewer F-2) — the DELEGATED act carries the
                # machine stamp too. Nobody answered is the premise of this
                # whole rail, so no surface may read it as M's gesture.
                actor=_MACHINE_SENTINEL,
                brain_batch_id=batch_id,
                brain_change_class="commitment_confirm")
        except Exception as exc:  # noqa: BLE001 — one refusal never kills a run
            sys.stderr.write(f"[question_ttl] track-it refused {c['id']}: {exc}\n")
            continue
        if res.get("status") == "cleared":
            out["n_tracked"] += 1
            out["tracked"].append(c["id"])
    return out


def _apply_capture_card(workspace_root, candidates, *, batch_id, now_iso=None,
                        apply: bool = True) -> dict:
    """The two-day default on a meeting-card question: the likely answer is
    applied on its own, receipted and reversible.

    This is the half that "exists in code and never ran on its own schedule":
    `attribution_doors.apply_counterparty_default` had exactly one caller,
    inside the review-expiry lapse, so a workspace whose lapse was configured
    away, refused, or simply never fired kept the question forever."""
    rows = [c for c in candidates if c.get("default") == DEFAULT_APPLY_LIKELY]
    out = {"n_applied": 0, "applied": [], "n_planned": len(rows)}
    if not rows or not apply:
        return out
    from attribution_doors import apply_counterparty_default
    actor, _kind = _machine_actor()
    for c in rows:
        try:
            res = apply_counterparty_default(
                workspace_root, c["id"], applied_by=actor, batch_id=batch_id,
                source_skill=SOURCE_SKILL, row=c.get("row"),
                mint_now_iso=now_iso,
                # FIX ROUND 1 (reviewer F-2) — named explicitly rather than
                # left to the default, so the machine answer is visible at
                # the call site that most needs it.
                actor=_MACHINE_SENTINEL)
        except Exception as exc:  # noqa: BLE001 — one refusal never kills a run
            sys.stderr.write(f"[question_ttl] card default refused "
                             f"{c['id']}: {exc}\n")
            continue
        if res.get("status") == "confirmed":
            out["n_applied"] += 1
            out["applied"].append(c["id"])
    return out


def _apply_identity(workspace_root, candidates, *, batch_id, now_iso=None,
                    apply: bool = True) -> dict:
    """IDENT1's rail, by name. `identity_aged_default` decides and
    `run_identity_reconcile` applies on the receipts and reversers already
    registered there; this engine schedules the class and counts it.

    Without IDENT1 in the tree the class reports `unavailable` and applies
    NOTHING. A second identity default written here would be exactly the
    duplicate the registry exists to prevent."""
    ir = _ident1()
    if ir is None:
        return {"status": "unavailable", "n_planned": len(candidates),
                "n_applied": 0,
                "why": "identity_reconcile.identity_aged_default is IDENT1's "
                       "(branch `ident1`) and is not in this tree"}
    return {"status": "delegated", "n_planned": len(candidates),
            "n_applied": 0,
            "why": "identity_reconcile.run_identity_reconcile applies rule 3 "
                   "on its own rail with its own receipts and reversers"}


def _apply_proposal_chips(workspace_root, candidates, *, batch_id,
                          now_iso=None, apply: bool = True) -> dict:
    """The four-day chip rail — reused, not rebuilt. `resolve_stale_chips`
    already applies-or-retracts every chip past `PROPOSAL_TTL_DAYS` and takes
    the batch id, so the engine hands it THIS run's id and reports its
    counts."""
    from commitment_policy_pass import resolve_stale_chips
    res = resolve_stale_chips(workspace_root, now_iso=now_iso,
                              batch_id=batch_id, apply=apply)
    return {"n_planned": int(res.get("n_ttl_planned") or 0),
            "n_applied": int(res.get("n_applied") or 0),
            "n_retracted": int(res.get("n_retracted") or 0),
            "batch_id": res.get("batch_id")}


def run_question_expiry(workspace_root, *, apply: bool = False, now_iso=None,
                        batch_id=None, classes=None, events=None,
                        fired_via: Optional[str] = None,
                        triggered_by: Optional[str] = None) -> dict:
    """THE ENGINE. Every question class expires to its default, reversibly,
    under ONE batch id, and the run is reported once.

    `apply=False` plans only (no writes, no receipt). Returns
    `{ran, applied, batch_id, now, by_class, n_expired, n_tracked,
      n_held_important, switch_on, receipt_line}`."""
    now = _now(now_iso)
    now_iso = now_iso or now.isoformat()
    switch_on = expiry_enabled(workspace_root)
    out = {"ran": True, "applied": False, "switch_on": switch_on,
           "now": now_iso, "batch_id": None, "by_class": {},
           "n_expired": 0, "n_tracked": 0, "n_held_important": 0,
           "n_retracted": 0, "receipt_line": ""}
    if not switch_on:
        out.update({"ran": False,
                    "receipt_line": ("Questions are set to wait for you on "
                                     "this workspace — nothing expired.")})
        return out
    cands = expiry_candidates(workspace_root, now_iso=now_iso, events=events,
                              classes=classes)
    total = sum(len(v) for v in cands.values())
    if not total:
        out["receipt_line"] = "No question was old enough to answer itself."
        return out
    bid = batch_id or mint_expiry_batch_id(now_iso)
    out["batch_id"] = bid
    for name in (classes or CLASS_ORDER):
        rows = cands.get(name) or []
        if not rows:
            out["by_class"][name] = {"n_candidates": 0}
            continue
        held = sum(1 for c in rows if c.get("importance_hold"))
        block = {"n_candidates": len(rows), "n_held_important": held,
                 "ttl_days": class_lifetime_days(name, workspace_root)}
        if name == CLASS_CAPTURE_CARD:
            block.update(_apply_capture_card(workspace_root, rows,
                                             batch_id=bid, now_iso=now_iso,
                                             apply=apply))
            block["track_it"] = _track_it(workspace_root, rows, batch_id=bid,
                                          now_iso=now_iso, apply=apply)
        elif name == CLASS_PROPOSAL:
            block.update(_apply_proposal_chips(workspace_root, rows,
                                               batch_id=bid, now_iso=now_iso,
                                               apply=apply))
        elif name == CLASS_DECISION_SUPERSEDE:
            block.update(_retract_supersede_proposals(
                workspace_root, rows, batch_id=bid, now_iso=now_iso,
                apply=apply, events=events))
            # NOT folded into `n_expired`: that number is what the run's
            # sentence offers to undo, and this class writes nothing an undo
            # could reach. It is reported on its own line and on the receipt.
            out["n_retracted"] += int(block.get("n_retracted") or 0)
        elif name == CLASS_IDENTITY:
            block.update(_apply_identity(workspace_root, rows, batch_id=bid,
                                         now_iso=now_iso, apply=apply))
        else:
            block.update(_expire_brain_proposals(workspace_root, rows,
                                                 batch_id=bid, now_iso=now_iso,
                                                 apply=apply))
        out["by_class"][name] = block
        out["n_expired"] += int(block.get("n_expired") or 0) \
            + int(block.get("n_applied") or 0)
        out["n_tracked"] += int((block.get("track_it") or {}).get("n_tracked") or 0)
        out["n_held_important"] += held
    out["applied"] = bool(apply)
    out["receipt_line"] = _receipt_line(out)
    if apply and (out["n_expired"] or out["n_tracked"] or out["n_retracted"]):
        _write_run_receipt(workspace_root, out, now_iso=now_iso,
                           fired_via=fired_via, triggered_by=triggered_by)
    return out


def previous_run_instant(workspace_root, *, now=None, events=None):
    """The instant of this job's PREVIOUS run receipt, or None on a seat that
    has never run it.

    FIX ROUND 1 (reviewer F1). `open_supersede_proposals` recomputes the whole
    pool from the raw ledger on every fire and the retraction writes nothing,
    so without this the same pile is "retracted" again every single run: the
    reviewer's fixture reported 5 twice and left two receipts, and on a copy of
    the operator's book the count drifted 202 -> 207 -> 272 across thirty hours
    because it is a live recount of a pool nothing ever leaves. The spec's
    sentence is "ONE batch retracts them", and one batch means one receipt.

    The previous receipt is the boundary because it is what the customer was
    last told. A receipt AT this run's instant counts as previous (a replay or
    a fixture may fire twice on one frozen clock, which is exactly the case
    that must not double-count)."""
    from event_time import event_time, parse_ts
    cutoff = now or datetime.now(timezone.utc)
    newest = None
    for ev in _events(workspace_root, events):
        if (ev.get("type") or ev.get("event")) != RUN_EVENT_TYPE:
            continue
        when = parse_ts(event_time(ev))
        if when is None or when > cutoff:
            continue
        if newest is None or when > newest:
            newest = when
    return newest


def _crossed_bar_at(candidate: dict, ttl_days: int):
    """When this proposal passed the four-day bar — its own instant plus the
    lifetime. A row with no readable instant returns None and is treated as
    NEW, because the safe direction is telling the customer once too often
    rather than never."""
    from event_time import parse_ts
    row = candidate.get("row") if isinstance(candidate.get("row"), dict) else {}
    when = parse_ts(row.get("proposed_at"))
    if when is None:
        return None
    return when + timedelta(days=int(ttl_days))


def _retract_supersede_proposals(workspace_root, candidates, *, batch_id,
                                 now_iso=None, apply: bool = True,
                                 events=None) -> dict:
    """DOORS1 1.4 — the four-day default for a supersede proposal: it stops
    being offered. NOTHING IS WRITTEN, and that is the whole design.

    The proposal never applied and never could (the matcher is recommend-only
    by ruling), the decision it named still carries the status its owner gave
    it, and the proposal event stays in the ledger exactly as written. So the
    default here is not a drop that needs reversing — it is the end of an
    offer, and the readers honour it through ONE predicate
    (`supersede_proposal_expired`) rather than through a tombstone.

    `assert_reversible` is still asked, and still means something: it refuses
    any class that declares no reverser while declaring itself destructive.

    ONE BATCH PER PILE, NOT ONE PER FIRE (fix round 1, reviewer F1). Only the
    proposals that crossed the bar SINCE THIS JOB'S PREVIOUS RECEIPT are
    counted and reported. Everything older was already counted, already said
    once, and saying it again would be the product re-deciding the same two
    hundred suggestions every morning and telling the customer about it every
    morning. A fire with no new crossings counts zero, writes no receipt, and
    is the quiet posture `maintenance_dispatcher.QUIET_RUN_JOBS` already
    scores as complete.

    Returns `{n_planned, n_retracted, n_already_counted, retracted}` and never
    an `n_expired` — the run's "say `undo` to put it all back" sentence must
    never count a row an `undo` cannot reach.
    """
    rows = [c for c in candidates if c.get("default") == DEFAULT_RETRACT]
    out = {"n_planned": len(rows), "n_retracted": 0,
           "n_already_counted": 0, "retracted": []}
    if not rows:
        return out
    previous = previous_run_instant(workspace_root, now=_now(now_iso),
                                    events=events)
    if previous is not None:
        ttl = class_lifetime_days(CLASS_DECISION_SUPERSEDE, workspace_root)
        fresh = []
        for c in rows:
            crossed = _crossed_bar_at(c, ttl)
            if crossed is None or crossed > previous:
                fresh.append(c)
        out["n_already_counted"] = len(rows) - len(fresh)
        rows = fresh
        if not rows:
            return out
    try:
        assert_reversible(CLASS_DECISION_SUPERSEDE)
    except QuestionTTLError as exc:
        sys.stderr.write("[question_ttl] " + str(exc) + chr(10))
        out.update({"status": "refused_no_reverser", "why": str(exc)})
        return out
    out["n_retracted"] = len(rows)
    out["retracted"] = [str(c.get("id")) for c in rows]
    out["writes"] = 0
    return out


#: The one sentence every surface uses for a retracted pile (MF-6 / merged-tree
#: review F-4): the maintenance receipt and the wrap's named form both import
#: this; `{n}` / `{suggestions}` are filled by the caller.
RETRACTION_SENTENCE = ("Stopped offering {n} old {suggestions} that a decision "
                       "was reversed in a later meeting")


def retraction_sentence(n: int) -> str:
    return RETRACTION_SENTENCE.format(
        n=n, suggestions=("suggestion" if n == 1 else "suggestions"))


def _receipt_line(out: dict) -> str:
    """One plain sentence. No class names, no batch id, no counts of things
    the customer never saw."""
    n = int(out.get("n_expired") or 0)
    t = int(out.get("n_tracked") or 0)
    r = int(out.get("n_retracted") or 0)
    if not n and not t and not r:
        return "No question was old enough to answer itself."
    bits = []
    if n:
        bits.append(f"answered {n} question{'' if n == 1 else 's'} "
                    "nobody had got to")
    if t:
        bits.append(f"kept {t} on your plate rather than let "
                    f"{'it' if t == 1 else 'them'} go")
    line = ""
    if bits:
        line = (" and ".join(bits).capitalize()
                + " — say `undo` to put it all back.")
    if r:
        # A SEPARATE SENTENCE, never folded into the one above: nothing was
        # written for these, so the undo offer must not appear to cover them.
        stopped = (retraction_sentence(r)
                   + " — the decisions themselves are untouched.")
        line = f"{line} {stopped}".strip() if line else stopped
    return line


def _write_run_receipt(workspace_root, out: dict, *, now_iso=None,
                       fired_via=None, triggered_by=None) -> None:
    from event_gate import append_event
    data = {
        "batch_id": out.get("batch_id"),
        "n_expired": out.get("n_expired"),
        "n_retracted": out.get("n_retracted"),
        "n_tracked": out.get("n_tracked"),
        "n_held_important": out.get("n_held_important"),
        "by_class": {k: {kk: vv for kk, vv in (v or {}).items()
                         if isinstance(vv, (int, str))}
                     for k, v in (out.get("by_class") or {}).items()},
        "lifetimes": lifetimes(workspace_root),
    }
    data.update(_actor_stamp())
    # FIX3 F3-6, fix pass 2 (review N-1). HOW this run was started and WHICH
    # surface asked for it, when either was said — written only when present,
    # so a run nobody told keeps the record it has always had.
    via = _run_mode(fired_via)
    if via:
        data["fired_via"] = via
    asked_by = _asked_by(triggered_by)
    if asked_by:
        data["triggered_by"] = asked_by
    ev = {"type": RUN_EVENT_TYPE, "source_skill": SOURCE_SKILL, "data": data}
    if now_iso:
        # A caller that named the run's clock gets that clock on the receipt
        # (QUIET1's `submit_questions` posture) — a replay or a fixture must
        # not stamp its own findings with wall-clock now.
        ev["ts"] = _now(now_iso).isoformat()
    append_event(_events_path(workspace_root), ev, holder=SOURCE_SKILL)


# ---------------------------------------------------------------------------
# The wrap's "Decided for you" block (QUIET1 D6 seam)
# ---------------------------------------------------------------------------

def decided_for_you_lines(workspace_root, since_iso, *, now_iso=None,
                          events=None) -> list:
    """The lines TTL1 contributes to the Friday wrap's "Decided for you" —
    one per expiry run in the window, each naming what it settled and how to
    put it back. Read-only; drop-empty.

    ATTRIB2's fold applies here too: a run whose batch a later `undo` already
    reversed is not news this block may report."""
    from event_time import event_time, parse_ts
    since = parse_ts(since_iso)
    now = _now(now_iso)
    evs = _events(workspace_root, events)
    # FIX ROUND 1 (reviewer F-10) — the fold matches the batch EXACTLY.
    # `any(bid in u for u in undone)` folded a run away whenever some other
    # reversed batch's ref merely CONTAINED its id, so a run could vanish
    # from the wrap because a different one was reversed. `batch_ref` is a
    # marker written by `brain_undo`, so the id is parsed out of it once and
    # compared as a value.
    from brain_undo import batch_id_from_ref
    undone: set = set()
    for ev in evs:
        if ev.get("type") != "brain_change_undone":
            continue
        ref = str((ev.get("data") or {}).get("batch_ref") or "")
        if not ref:
            continue
        undone.add(batch_id_from_ref(ref))
    lines = []
    for ev in evs:
        if ev.get("type") != RUN_EVENT_TYPE:
            continue
        dt = parse_ts(event_time(ev))
        if dt is None or dt > now or (since is not None and dt <= since):
            continue
        d = ev.get("data") or {}
        bid = str(d.get("batch_id") or "")
        if bid and bid in undone:      # EXACT, never a substring (F-10)
            continue
        n = int(d.get("n_expired") or 0)
        t = int(d.get("n_tracked") or 0)
        # FIX ROUND 1 (reviewer F1, second half). `n_retracted` belongs in this
        # gate. Without it a run whose ONLY content was retractions contributed
        # no line at all, so the one act the spec says is "counted in *decided
        # for you* by name" was the one act no surface ever named — the product
        # stopped offering two hundred suggestions and told the customer
        # nothing, ever.
        # MF-6 (trial merge 2026-09-14): `n_retracted` is NOT rendered here.
        # The wrap and End of Day read ONE "decided for you" producer -
        # `change_feed.decided_for_you`'s named form, which renders the
        # retraction batch off this same receipt (NAMED_ACT_DOORS
        # "proposals_retracted"). Rendering it here too printed the act twice
        # (SURFACEFIX1 re-verification, shared fixture).
        if not n and not t:
            continue
        parts = []
        if n:
            parts.append(f"answered {n} question{'' if n == 1 else 's'} "
                         "nobody had got to")
        if t:
            parts.append(f"kept {t} on your plate because "
                         f"{'it was' if t == 1 else 'they were'} "
                         "due or had a client on it")
        line = ""
        if parts:
            line = (" and ".join(parts).capitalize()
                    + " — say `undo` and pick it from the list to reverse")
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# The ceiling (QUIET1's budget, published for the Staff Meeting)
# ---------------------------------------------------------------------------

def staff_meeting_question_ceiling(workspace_root=None, *, now_iso=None,
                                   events=None, preset=None) -> int:
    """THE CEILING for one Staff Meeting page — QUIET1's question budget for
    the effective preset, never more than the shipped page bound.

    The Staff Meeting accumulated because the only thing bounding it was a
    PAGE cap (21 rows a fire) while the queue behind it grew without limit.
    A page bound is not a budget: it decides what one screen holds, not how
    many questions a week the person agreed to answer. Under `light` that
    number is five, and INTAKE1's one day-7 held-with-client question is
    INSIDE it, not on top of it.

    Never returns less than 1: a ceiling of zero is indistinguishable from
    the surface being switched off, which is a different act."""
    # AT MERGE (night 10, REVIEW_FOLD1A at-merge 3 / REVIEW_TTL1 R-1): ONE
    # ceiling. FOLD1-A's `quiet.staff_meeting_question_ceiling` is the
    # budget's REMAINDER for the week (what the other askers already spent
    # comes off), and this page must read the same number, or a `light`
    # seat sees 2 + 5 = 7 where it agreed to five. The shipped page bound
    # stays the fallback for a ceiling that cannot read.
    from proposal_digests import STAFF_PAGE_ROW_CAP
    try:
        import quiet
        return int(quiet.staff_meeting_question_ceiling(
            workspace_root, now_iso=now_iso, events=events, preset=preset))
    except Exception:  # pragma: no cover — a ceiling that cannot read falls
        return int(STAFF_PAGE_ROW_CAP)  # back to the shipped page bound


# ---------------------------------------------------------------------------
# CLI — the report and the run
# ---------------------------------------------------------------------------

def report(workspace_root, *, now_iso=None, events=None) -> dict:
    """Counts only: open questions by class and age band, and what each
    class's lifetime would expire, to which default."""
    now = _now(now_iso)
    ids = _client_ids(workspace_root)
    everything = open_questions(workspace_root, now_iso=now_iso, events=events)
    out: dict = {"now": now.isoformat(), "lifetimes": lifetimes(workspace_root),
                 "by_class": {}}
    for name, rows in everything.items():
        spec = class_spec(name)
        ttl = class_lifetime_days(name, workspace_root)
        bands = {"0-1": 0, "2-6": 0, "7-13": 0, "14-29": 0, "30+": 0,
                 "undated": 0}
        by_default: dict = {}
        n_past = 0
        n_held = 0
        for r in rows:
            age = r.get("age_days")
            if age is None:
                bands["undated"] += 1
                continue
            if age <= 1:
                bands["0-1"] += 1
            elif age <= 6:
                bands["2-6"] += 1
            elif age <= 13:
                bands["7-13"] += 1
            elif age <= 29:
                bands["14-29"] += 1
            else:
                bands["30+"] += 1
            if age >= ttl:   # the same `>=` boundary `expiry_candidates` uses
                n_past += 1
                raw = spec["default"]
                if name == CLASS_CAPTURE_CARD and not r.get("has_default"):
                    raw = DEFAULT_LET_GO
                # Same question `expiry_candidates` asks, and asked the same
                # way — of the default THIS ROW would take, never of its class.
                hold = (importance_hold(r.get("row"), now=now, client_ids=ids,
                                        workspace_root=workspace_root)
                        if raw == DEFAULT_LET_GO else None)
                if hold:
                    n_held += 1
                    raw = DEFAULT_TRACK_IT
                by_default[raw] = by_default.get(raw, 0) + 1
        out["by_class"][name] = {
            "n_open": len(rows), "ttl_days": ttl, "age_bands": bands,
            "n_past_ttl": n_past, "n_held_important": n_held,
            "by_default": by_default,
        }
    return out


def main(argv=None) -> int:
    import argparse
    import json
    import os
    ap = argparse.ArgumentParser(description="TTL1 — every question expires")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--now")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", action="store_true")
    # FIX3 F3-6, fix pass 2 (review N-1). `maintenance_dispatcher` renders
    # this command with both flags on the end and a typed brief pastes it
    # verbatim; before this pass the command answered "unrecognized
    # arguments" and exited 2, so a weekday brief whose catch-up was due ran
    # a job that died with a usage error on every legacy and local seat.
    ap.add_argument("--fired-via", default=None,
                    choices=("scheduled", "manual", "catchup"),
                    help="how this run was started; unset says nothing, "
                         "which is what an un-merged seat has always said")
    ap.add_argument("--triggered-by", default=None,
                    help="the surface that asked for this run")
    args = ap.parse_args(argv)
    # The same export the other job CLIs make, so every composer below reads
    # who asked from one place instead of a dozen signatures.
    if getattr(args, "triggered_by", None):
        os.environ["CR_TRIGGERED_BY"] = str(args.triggered_by)
    if args.report:
        print(json.dumps(report(args.workspace, now_iso=args.now), indent=2))
        return 0
    res = run_question_expiry(args.workspace, apply=args.apply,
                              now_iso=args.now,
                              fired_via=args.fired_via,
                              triggered_by=args.triggered_by)
    print(json.dumps(res, indent=2, default=str))
    return 0


__all__ = [
    "CLASS_CAPTURE_CARD", "CLASS_PROPOSAL", "CLASS_DEAL_SIGNAL",
    "CLASS_HYGIENE", "CLASS_IDENTITY", "CLASS_DECISION_SUPERSEDE",
    "CLASS_ORDER", "CLASSES", "DEFAULT_RETRACT", "REVERSER_NONE",
    "MONEY_FIELDS", "MONEY_TEXT_FIELDS",
    "SUPERSEDE_PROPOSED_EVENT", "open_supersede_proposals",
    "supersede_proposal_expired", "previous_run_instant",
    "DEFAULT_APPLY_LIKELY", "DEFAULT_TRACK_IT", "DEFAULT_LET_GO",
    "DEFAULT_RESOLVE_SELF", "DEFAULTS",
    "DEAL_SIGNAL_TTL_DAYS", "IDENTITY_TTL_FALLBACK_DAYS", "DUE_SOON_DAYS",
    "EXPIRY_BATCH_PREFIX", "PROPOSAL_EXPIRY_CHANGE_CLASS",
    "PROPOSAL_REOPENED_EVENT", "RUN_EVENT_TYPE",
    "EXPIRE_SWITCH_KEY", "EXPIRE_SWITCH_DEFAULT", "expiry_enabled",
    "QuestionTTLError", "registered_classes", "class_spec",
    "class_reversers", "unregistered_reversers", "assert_reversible",
    "class_lifetime_days", "lifetimes", "expiry_note", "days_phrase",
    "SHAPE_CLASSES", "question_class_of", "row_age_days", "within_lifetime",
    "IMPORTANCE_OVERDUE", "IMPORTANCE_DUE_SOON", "IMPORTANCE_CLIENT",
    "IMPORTANCE_MONEY", "importance_hold",
    "MACHINE_ACT_TYPES", "card_movement_types",
    "open_questions", "expiry_candidates", "mint_expiry_batch_id",
    "run_question_expiry", "decided_for_you_lines",
    "staff_meeting_question_ceiling", "report", "main",
]

if __name__ == "__main__":
    raise SystemExit(main())
