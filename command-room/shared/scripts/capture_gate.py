#!/usr/bin/env python3
"""
Shared commitment-capture gate (v4.6.1 W4c).

Two layers, one module:

  1. THE CAPTURE BLOCK (consolidation) — the Stage-D / S2 / Stage-E gate
     every commitment writer runs (`gate_commitment_data`). See below.

  2. THE RELEVANCE GATE + OBSERVED TIER (W4c — the volume fix, maintainer +
     high-volume-operator feedback 2026-07-08). An item enters the ledger as an OPEN commitment
     only if the workspace owner is a party (owes it or is owed it).
     Third-party↔third-party items observed in meetings/Slack/sessions, and
     items whose attribution can't be confidently resolved (amber), are
     stored as `commitment_observed` events instead — the full record, kept
     silently: searchable, feeds prep context, promotable — but they create
     NO open item, NO count, NO triage row.

     OBSERVED1 (2026-08-24) — the CONFIRM QUEUES read the tier. "No
     confirm-section row" was the shipped state for six weeks and it was a
     reader-coverage gap, not a design: prep cited observed rows as live work
     while no surface could answer one (24 rows, zero ever confirmed or
     dropped, on the first workspace measured). `live_observed` below is the
     canonical liveness read; needs-your-call and `show watching` render live
     rows with confirm/drop verbs. Confirm = `promote_observed` (the tier's
     one defined transition) + `clear_review_flags`; drop = `promote_observed`
     + `close_commitment(resolution="dropped")` — the standard writers, no new
     event type. The WRITE path here is untouched.

     STORAGE DECISION (ratified in this module): a dedicated event type,
     `commitment_observed` (with `data.tier: "observed"`), NOT
     `data.observed_commitments[]` on the meeting event and NOT
     `type: commitment` + a tier field. Two reasons:
       (a) Fail-safe by construction — every open-set reader in the product
           filters `type == "commitment"`; a separate type is invisible to
           all of them (counts, triage, confirm, chase, CRU) with ZERO
           reader edits. A tier field on `type: commitment` would leak into
           any reader that forgets the exclusion — the exact bug class W4c
           exists to kill. Same doctrine as the W4a reminder lane
           ("separate types keep them out structurally").
       (b) Source-uniform — Slack and session captures have NO parent
           meeting event to hang an `observed_commitments[]` array on; a
           standalone event works identically for every source.

     MODES (customize layer, SCL1 rails — read at capture time, stored as
     directives in `_hq/custom/scan-for-commitments.md`, the capture-policy
     holder for ALL commitment writers):
       party-only (DEFAULT)   only what the owner owes / is owed opens.
       team-delegation        also open items a team member commits to
                              (people in the workspace's own org).
       track-everything       pre-W4c behavior — everything opens.
       observed-only          (org-override value) keep everything from
                              that org on file without asking.
     Per-org overrides beat the global mode and are routed via the
     meeting's resolved org. Grammar: `capture mode: <mode>` (global),
     `for <org>: <mode>` (override). Principle: CAPTURE EVERYTHING, GATE
     ONLY SURFACING — no mode loses data, so the line is movable
     retroactively.

     ACCOUNT-SCOPE QUALIFICATION (connector-agnostic-v1, 2026-07-11 — R1).
     "CAPTURE EVERYTHING / no mode loses data" is scoped to IN-SCOPE
     accounts. Per shared/ACCOUNT_SCOPE.md the two-dial model overrides this
     doctrine for out-of-scope accounts: a connector read from an account
     whose `write_to_business` dial is OFF (personal / mixed-account unknown
     sender) files NOTHING — not even an observed-tier event. The relevance
     gate here decides what SURFACES vs OPENS *within* the business-scoped
     stream; the account-scope wall decides whether the item enters the
     stream at all, and it runs FIRST. Where the account map is empty (live
     client mid-upgrade), every account is in-scope and the original blanket
     doctrine holds unchanged (R4). The structural enforcement is the
     writer-side scope check landing in Phase 3 (event_gate/capture_gate/
     sent_capture/slack_capture/people_writer); this docstring records the
     doctrine change that gates it.

     ASYMMETRIC CAUTION RAIL: an item carrying a due date or a money amount
     ALWAYS surfaces as open, regardless of mode or override (miss-cost
     asymmetry). `build_observed_event` enforces the rail in code by
     refusing dated/money items. NOTE (R1): the forced-open rail applies only
     to IN-SCOPE accounts — a due-date/money item from an out-of-scope
     personal account is never force-opened into business records, because it
     never passes the account-scope wall to reach this rail in the first
     place.

     CORROBORATION (amber promotion — a checkable rule, not vibes): an
     observed item is promoted into the confirm flow (a real `commitment`
     event with `data.pending_review: true` — W4b's flow picks it up by
     data contract) when a LATER event from a DIFFERENT source both
     (i) shares a party (person id, or a resolvable name token) and
     (ii) overlaps its content (stopword-stripped title-token Jaccard
     ≥ 0.5, or ≥ 3 shared content tokens). The user referencing it
     explicitly ("track that") promotes unconditionally. Prep context
     SURFACES observed items for meetings with those parties
     (`prep_context_observed`) with promotion one tap away — it never
     auto-promotes, so a weekly recurring meeting can't re-create the
     confirm-row volume the gate just removed.

     AUDIT AFFORDANCE: `observed_counts` backs the weekly cleanup note's
     one-liner ("N items set aside this week — review") — a filter the
     user can inspect is a filter the user can trust.

     VERB-DRIVEN TUNING (consent, never silent): `propose_gate_directives`
     mines Not-mine/Drop/dismiss outcomes per counterparty org and PROPOSES
     an observed-only override ("dismissed 12 of 15 vendor captures — stop
     surfacing those?"); one tap calls `apply_gate_proposal`, which writes
     the directive through skill_custom_writer. The gate NEVER adjusts
     itself — a proposal the user didn't approve changes nothing.

Layer 1 detail (consolidation step):

WHY THIS EXISTS
---------------
The Stage-D / S2 / Stage-E capture block (v4.5.2 C1, F-31 parity with
scan-for-commitments Step 3) was implemented TWICE: once as
`session_sweep._gate_commitment` and once inline in
`slack_capture.build_slack_commitment_event`, with `meeting_capture` carrying
a third partial copy of the pending_review inversion. Three copies of one
contract is the drift bug class this repo keeps paying for (five spellings of
one event type, two meeting writers on different contracts — F-46). This
module is now the ONE implementation; every capture writer calls it:

  - session_sweep._gate_commitment  -> gate_commitment_data (thin wrapper)
  - slack_capture.build_slack_commitment_event -> gate_commitment_data
  - meeting_capture.build_meeting_commitment_event -> gate_commitment_data

THE BLOCK (identical semantics to both prior copies — proven by the existing
suites before anything new was added on top):

  Stage D  `data.kind` is classified AT EXTRACTION, one of KIND_VALUES;
           missing/invalid rejects loud.
  S2       due-nudge: a parseable `data.due` (YYYY-MM-DD) OR explicit
           `data.no_due: true`. Silence rejects; both together rejects
           ("BOTH ... pick one").
  Stage E  promise-vs-task rule: a `task` carrying a counterparty rejects
           (a deliverable owed to/by a named person is a promise).
  Safety   pending_review inversion: the flag is STAMPED (never unset) when
           attribution is not confidently resolved — counterparty name with
           no person record, promise with no counterparty, promise with no
           resolved owner, or extraction confidence below
           CONFIDENCE_SURFACE_MIN. Absence of the flag is an assertion of
           confident attribution, never an accident.

Callers pass their own `subject` string (how the item is named in error
messages) and their own error class (SweepItemError / SlackItemError /
ValueError) so fail-loud messages keep their source-specific voice.

stdlib only. Construction/validation only — nothing here touches disk;
appends stay with the callers through `event_gate.append_event`.
"""
from __future__ import annotations
try:
    from text_clip import clip  # noqa: E402
except ImportError:  # pragma: no cover — direct-path fallback
    import sys as _sys_tc
    from pathlib import Path as _Path_tc
    _sys_tc.path.insert(0, str(_Path_tc(__file__).resolve().parent))
    from text_clip import clip  # noqa: E402

import datetime as _dt
import hashlib
import math
import re
import sys
from pathlib import Path
from typing import Optional, Type

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from event_types import KIND_VALUES  # noqa: E402
# REVAMN1 §0-3 — the lapse-vs-dismissal test, imported not restated. See
# `event_types.is_non_dismissal_closure`.
from event_types import is_non_dismissal_closure as _is_non_dismissal  # noqa: E402
# THREADSTAMP1 DD-1 — the derivation ladder is imported, never restated. One
# ladder for the capture gate, the append gate and the repair tool.
from event_types import derive_primary_thread_id  # noqa: E402

# SPEC PROV2 — identity comparisons on STORED source pointers route through
# Layer A4's derivation, never a raw `==` (guard G30). Stored pointers preserve
# the native id's case now, so two spellings of one artifact coexist on disk.
from connector_adapters.provenance import dedup_key_of  # noqa: E402

try:
    from confidence import CONFIDENCE_SURFACE_MIN  # noqa: E402
except Exception:  # pragma: no cover
    CONFIDENCE_SURFACE_MIN = 0.7


# ---------------------------------------------------------------------------
# CONFCLAMP1 — `classification_confidence` is validated AT THE WRITE SEAM.
# ---------------------------------------------------------------------------
#
# `events.schema.json` bounds the field to [0.0, 1.0] and NOTHING enforced it
# at write, so out-of-range and non-numeric values were reaching the permanent
# record (integrity_check C15 detects them after the fact; by then they are
# history, and history is append-only). Worse, the surface floor below was
# `isinstance`-guarded, so a malformed value SKIPPED the floor entirely — the
# junk capture got a free pass through the exact gate that exists to catch
# low-quality captures.
#
# Two write behaviours, deliberately different:
#
#   numeric      -> CLAMPED into [0.0, 1.0]. The writer meant "very confident"
#                   / "not confident"; the magnitude is the bug, not the
#                   intent, so the score is repaired rather than lost.
#   non-numeric  -> DROPPED from the event, with a tell on stderr. The event
#                   still writes, UNSCORED: absence is the honest
#                   representation of an unusable score, and the capture
#                   itself is usually fine — killing it over a malformed
#                   annotation is disproportionate.
#
# Strings are NOT coerced ("0.8" -> 0.8). Coercion would repair the symptom
# and hide the writer bug the tell exists to surface.
#
# The two halves are one posture, not a contradiction: the WRITE seam drops an
# unreadable score so no junk enters the permanent record, and the FLOOR routes
# the capture carrying it to REVIEW so no junk enters the ledger unexamined.
# The capture survives; nobody is asked to trust it.
CONFIDENCE_MIN = 0.0
CONFIDENCE_MAX = 1.0

# The four branches the surface floor can take. Named constants (not a bare
# bool) so a test can assert WHICH branch a value took — an unscored pass and
# an above-floor pass are the same outcome and very different meanings.
#
# UNSCORED and MALFORMED are deliberately NOT the same branch. An absent score
# is legitimate (infrastructure events carry none) and passes. A score that is
# present but unreadable is a defect, and routing it to REVIEW is not a drop —
# it is the CAPTUREFLOW posture: doubt becomes a question, never a silent pass.
# Collapsing the two would make a malformed score strictly WEAKER than a bad
# numeric one, which is the regression this branch exists to prevent.
CONFIDENCE_UNSCORED = "unscored"
CONFIDENCE_MALFORMED = "malformed"
CONFIDENCE_BELOW_FLOOR = "below_floor"
CONFIDENCE_OK = "ok"


def is_scored_confidence(value) -> bool:
    """Is `value` a usable classification confidence — a real number?

    `bool` is excluded on purpose: `True` is an `int` in Python, and a boolean
    is a flag, not a score. NaN is excluded for the same reason — it compares
    False against every bound, so a floor test on it silently passes.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    # `math.isnan` CONVERTS its argument to float, so an over-large int
    # (10**400) raises OverflowError inside what is supposed to be a total
    # predicate. An int can never be NaN, so only floats are asked.
    return not (isinstance(value, float) and math.isnan(value))


def clamp_confidence(value):
    """A numeric confidence, clamped into [0.0, 1.0]; `None` for anything that
    is not a score.

    An already-in-range value is returned UNCHANGED (same object, same type) —
    the clamp must be invisible to every writer that was already correct.
    """
    if not is_scored_confidence(value):
        return None
    if CONFIDENCE_MIN <= value <= CONFIDENCE_MAX:
        return value
    return CONFIDENCE_MIN if value < CONFIDENCE_MIN else CONFIDENCE_MAX


def stamp_confidence(ev: dict, value, *, holder: str = "capture_gate") -> dict:
    """Write `classification_confidence` onto `ev` under the rules above.

    THE one write seam for this field in this module — both event builders
    route through it, so the bound is enforced in a single place instead of
    being restated (and forgotten) per builder. Mutates and returns `ev`.
    Never raises: a bad score must not be able to destroy a good capture.
    """
    if value is None:
        return ev
    clamped = clamp_confidence(value)
    if clamped is None:
        ev.pop("classification_confidence", None)
        sys.stderr.write(
            f"[capture_gate] dropped non-numeric classification_confidence "
            f"{value!r} (holder={holder}) — the event writes UNSCORED. The "
            f"schema bounds this field to [{CONFIDENCE_MIN}, {CONFIDENCE_MAX}] "
            f"and strings are deliberately NOT coerced; fix the writer.\n"
        )
        return ev
    if clamped != value:
        sys.stderr.write(
            f"[capture_gate] clamped classification_confidence {value!r} -> "
            f"{clamped} (holder={holder}) — the schema bounds this field to "
            f"[{CONFIDENCE_MIN}, {CONFIDENCE_MAX}].\n"
        )
    ev["classification_confidence"] = clamped
    return ev


def classify_confidence_for_floor(value, floor) -> str:
    """Which branch of the surface floor a confidence takes.

    Four cases, each stated out loud:

      UNSCORED     `None`, and only `None`. Infrastructure events legitimately
                   carry no score, and unscored PASSES the floor per the
                   standing read-side doctrine that the floor must never
                   silently drop an unscored capture.
      MALFORMED    a score that is PRESENT but unreadable — a string, a bool,
                   NaN, a dict. Routed to REVIEW with its own reason, never
                   clamped and never silently passed. Before CONFCLAMP1 this
                   case was the accidental fall-through of an `isinstance`
                   guard: the malformed value SKIPPED the floor entirely, which
                   is the free pass this build exists to close. Folding it in
                   with UNSCORED instead would leave `False` strictly weaker
                   than it was before this build (it used to compare as 0 and
                   flag below-floor), which is the wrong direction.
      BELOW_FLOOR  a real score under the floor — flagged, exactly as before.
      OK           a real score at or above the floor.
    """
    if value is None:
        return CONFIDENCE_UNSCORED
    if not is_scored_confidence(value):
        return CONFIDENCE_MALFORMED
    return CONFIDENCE_BELOW_FLOOR if value < floor else CONFIDENCE_OK


class CaptureGateError(ValueError):
    """A capture item was malformed — fail loud so a bad extraction is visible
    and goes back to the extractor, never silently dropped or written wrong
    (the F-31 bug class; SweepItemError / SlackItemError's shared parent in
    spirit — callers substitute their own class via `error_cls`)."""


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


def parse_iso_date(value) -> bool:
    """True iff `value` is a string whose first 10 chars parse as an ISO date."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        _dt.date.fromisoformat(value.strip()[:10])
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Provenance schemes (FLOOR2 C2, intake BUG_2026-08-06_myplate-synthetic-
# granola-sourceref).
# ---------------------------------------------------------------------------
#
# A live user-initiated capture ("Add to My Plate") wrote
# `source_ref: "granola:past-meetings-2026-08-04"` — the CONNECTOR scheme
# carrying a value no connector ever minted. Two costs, both real:
#
#   * anything that treats `granola:<x>` as a fetchable meeting id (transcript
#     verification, the V1 sampler, FLOOR2's own re-scan, HIST1 enrichment)
#     mis-resolves or fails on it, and
#   * `account_scope_gate` sniffs the scheme prefix to decide whether a
#     commitment is connector-derived, so a hand-typed task was being weighed
#     as a connector read.
#
# A user-initiated capture is legitimately evidence-less and gate-exempt — it
# should be IDENTIFIABLE as such by its scheme, not disguised as a connector
# read. So: one scheme for them, and a shape rule for the connector scheme they
# were borrowing.
USER_SOURCE_REF_MY_PLATE = "user:my_plate"

# Granola mints UUIDs. Nothing else belongs behind this prefix.
_UUID_RE = re.compile(
    r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def granola_ref_ok(source_ref) -> bool:
    """True unless `source_ref` is the `granola:` scheme carrying a value that
    is not UUID-shaped. Anything that is not a `granola:` ref at all — another
    scheme, a bare id, "" — is not this rule's business and passes."""
    s = str(source_ref or "").strip()
    if not s.lower().startswith("granola:"):
        return True
    return bool(_UUID_RE.match(s.split(":", 1)[1].strip()))


def user_initiated_source_ref(
    source_ref=None, *, scheme: str = USER_SOURCE_REF_MY_PLATE
) -> str:
    """The provenance a USER-INITIATED capture writes.

    Keeps a real originating ref (that IS the provenance, and a genuine
    `granola:<uuid>` row the user promoted by hand should still point at its
    meeting); substitutes the user scheme for a synthetic `granola:` ref and
    for no ref at all. Never raises — a one-tap user action must not be lost
    to a provenance quibble; the wrong ref is what gets dropped, not the
    capture."""
    s = str(source_ref or "").strip()
    if not s or not granola_ref_ok(s):
        return scheme
    return s


def gate_commitment_data(
    data: dict,
    *,
    subject: str,
    classification_confidence: Optional[float] = None,
    error_cls: Type[Exception] = CaptureGateError,
    workspace_root=None,
) -> None:
    """THE Stage-D / S2 / Stage-E capture block, shared by every commitment
    writer (v4.5.2 C1 parity with scan-for-commitments Step 3).

    Reads/mutates `data` in place: validates kind / due / promise-vs-task and
    stamps the pending_review safety inversion (never unsets an extractor-set
    True). Raises `error_cls` on anything the extraction must go back and do.

    `subject` names the item in error messages (e.g. "recovered commitment
    (session X)" or "Slack commitment slack:<permalink>").
    """
    # Stage D: kind is classified AT EXTRACTION, never defaulted here.
    kind = data.get("kind")
    if kind not in KIND_VALUES:
        raise error_cls(
            f"{subject} needs data.kind, one of {sorted(KIND_VALUES)} — "
            f"classify it at extraction (counterparty promise -> promise; "
            f"self-owed -> task; scheduling intent -> scheduling; "
            f"discuss item -> agenda). 'send X to [person]' has a "
            f"counterparty — it is a promise, not a task."
        )

    # S2 due-nudge: every capture proposes a `due` from the source language
    # (resolve relative phrases against the SOURCE's date, not the scan date)
    # OR sets data.no_due: true explicitly. Silence is not an option; an
    # undated capture sinks in every ranking at exactly the moment it matters
    # (F-31 -> F-44).
    due = data.get("due")
    no_due = data.get("no_due")
    if no_due is True:
        if due:
            raise error_cls(
                f"{subject} sets BOTH data.due={due!r} and "
                f"data.no_due: true — pick one"
            )
    elif not parse_iso_date(due):
        raise error_cls(
            f"{subject} needs a due date: propose data.due as YYYY-MM-DD "
            f"from the source language (resolve relative phrases like "
            f"'tomorrow'/'Thursday' against the source's date) or set "
            f"data.no_due: true explicitly (S2 due-nudge; got due={due!r})"
        )

    # Stage E + the promise-vs-task rule. A task is self-owed with NO
    # counterparty by definition — a counterparty makes it a promise
    # (F-31: "send briefs to collaborator" is a promise). MC1: read the FULL
    # roster (legacy single + counterparty_ids/counterparty_names lists).
    from commitment_parties import (
        counterparty_ids as _cp_ids,
        counterparty_names as _cp_names,
    )
    cp_ids = _cp_ids(data)
    cp_names = _cp_names(data)
    if kind == "task" and (cp_ids or cp_names):
        raise error_cls(
            f"{subject} is kind 'task' but carries a counterparty "
            f"({(cp_ids + cp_names)[0]!r}) — a deliverable owed to/by a named "
            f"person is a promise, not a task; reclassify"
        )
    # EXTRACT1 D-A (dragger 4) — the owner can never be their own
    # counterparty. Two live rows listed the user on both ends of one
    # promise; that is not a judgement call, it is a shape that cannot be a
    # promise. The meeting route strips the self-reference BEFORE it reaches
    # here (and says so on the row); every other writer, and any caller
    # that bypasses the route, is refused at this seam.
    owner = str(data.get("owner_id") or "").strip()
    if owner and owner in cp_ids:
        raise error_cls(
            f"{subject} names its owner ({owner!r}) as its own counterparty "
            f"— a person cannot owe something to themselves; drop the "
            f"self-reference or resolve who is really on the other end"
        )

    # Safety inversion (v4.5.2): pending_review defaults ON whenever
    # attribution is not confidently resolved — absence of the flag is not
    # consent (CRU auto-resolution gates on it; a low-confidence capture that
    # forgets the flag would auto-resolve with no human gate). Never unsets
    # an extractor-set True.
    reasons = []
    if cp_names and not cp_ids:
        reasons.append(f"counterparty '{cp_names[0]}' has no person record")
    if kind == "promise":
        if not cp_ids and not cp_names:
            reasons.append("no counterparty identified for a promise")
        if not data.get("owner_id"):
            reasons.append("no resolved owner")
    # BUG-8330 item 6 — the floor resolves through the calibration accessor
    # (confidence.surface_min) so a workspace override moves THIS gate too;
    # the baked-constant import stays only as the accessor's own fallback.
    try:
        from confidence import surface_min as _surface_min
        _floor = _surface_min(workspace_root)
    except Exception:
        _floor = CONFIDENCE_SURFACE_MIN
    # CONFCLAMP1 DD-2 — the four floor branches are named, and the malformed
    # one is stated rather than fallen through. See
    # `classify_confidence_for_floor`.
    _conf_branch = classify_confidence_for_floor(
        classification_confidence, _floor
    )
    if _conf_branch == CONFIDENCE_BELOW_FLOOR:
        reasons.append(
            f"extraction confidence {classification_confidence} below threshold"
        )
    elif _conf_branch == CONFIDENCE_MALFORMED:
        reasons.append(
            f"extraction confidence {classification_confidence!r} is not a "
            f"number — unreadable, so this routes for review rather than "
            f"being trusted"
        )
    if reasons:
        data["pending_review"] = True
        data.setdefault("review_reason", "; ".join(reasons))

    # EXIT1 (SPEC_FLOW1 Lane B item 1) — WHAT WOULD SHOW THIS DONE, written
    # down at the door. One word: a message to them, their reply, a meeting
    # with them, a payment or a signature — or, for a row with nobody on the
    # other end, `your_own_word`, which is the honest answer for 197 of the
    # 317 rows on the operator's book and the reason routes 2 and 3 exist.
    #
    # ADDITIVE AND NEVER FATAL. Every builder passes through this gate, so
    # stamping here reaches all of them at once; a derivation that raised
    # would turn a legible-provenance nicety into a capture outage, so it
    # never raises. An explicit value a caller already set is respected.
    # The reader (`exit_doors.stored_proof`) DERIVES the same answer for any
    # row that carries none — which is every row written before tonight —
    # so nothing depends on this stamp having been there.
    try:
        if not str(data.get("proof") or "").strip():
            from exit_doors import proof_for_row
            data["proof"] = proof_for_row({"data": data})
    except Exception:  # noqa: BLE001 — a legibility stamp never blocks a capture
        pass


# =============================================================================
# Layer 2 — W4c relevance gate, observed tier, modes, corroboration, tuning.
# =============================================================================

OBSERVED_TYPE = "commitment_observed"

MODE_PARTY_ONLY = "party-only"
MODE_TEAM_DELEGATION = "team-delegation"
MODE_TRACK_EVERYTHING = "track-everything"
MODE_OBSERVED_ONLY = "observed-only"  # org-override value (power setting)
DEFAULT_MODE = MODE_PARTY_ONLY
CAPTURE_MODES = (
    MODE_PARTY_ONLY,
    MODE_TEAM_DELEGATION,
    MODE_TRACK_EVERYTHING,
    MODE_OBSERVED_ONLY,
)

# The SCL1 directive holder for capture policy. One policy file governs EVERY
# commitment writer (scan, sweep, meeting leg, Slack leg) — capture relevance
# is a workspace-wide question, not a per-writer one. scan-for-commitments is
# the capture family's front door, so its name holds the file.
CAPTURE_POLICY_SKILL = "scan-for-commitments"

# Tuning-proposal floors (verb-driven, consent-gated). Mirrors the
# commitment_noise / triage_feedback propose-approve pattern.
TUNING_MIN_ITEMS = 5
TUNING_MIN_DISMISS_RATE = 0.7
TUNING_CAP = 3
TUNING_WINDOW_DAYS = 30

# Dismiss-family resolutions: the CEO saying "this wasn't mine to track."
# NOT sufficient on its own since REVAMN1 — a `dropped` carrying a review-tier
# `resolution_reason` is a bulk lapse nobody adjudicated, and the reader below
# pairs this set with `event_types.is_non_dismissal_closure` to tell them apart.
_DISMISS_RESOLUTIONS = frozenset({"dropped", "not_mine", "not mine"})

# The caution rail's money detector — deliberately conservative: a currency
# symbol followed by a number, or a number followed by a currency word. Bare
# "5k" does NOT match ("5k run", "10k users" would false-positive the rail
# into exactly the noise W4c removes).
# LEARNFIX1 1.7 (M's ruling R-21) — AND the paper that carries the number.
# "Send the engagement letter with the fee quote" is a money row: it is the
# document a fee is agreed on. It carried no currency symbol and no currency
# word, so the shared detector said no money, `question_ttl.importance_hold`
# let it through, and it lapsed with the other 45 on 2026-09-16 (record B4).
# This is a VOCABULARY change to the ONE definition of money, not a second
# predicate and not a second regex — `_has_money` imports this and DOORS1
# 1.2's hold consults that. Deliberately four named instruments and nothing
# looser: "letter", "quote" and "fee" on their own are ordinary words, and a
# false positive here parks a question forever.
_MONEY_RE = re.compile(
    r"""(?ix)
      [$€£]\s*\d
    | \b\d[\d,]*(?:\.\d+)?\s*(?:k|m|mm)?\s*
      (?:dollars|bucks|usd|eur|euros|gbp|pounds|grand)\b
    | \bengagement\s+letter\b
    | \bletter\s+of\s+engagement\b
    | \bfee\s+quote\b
    | \bretainer\s+letter\b
    """
)

_STOPWORDS = frozenset(
    "the a an to of for and or on in with by at from about that this it its "
    "is are was be will would i we you he she they them their our your my me "
    "us up out over".split()
)
_MIN_NAME_TOKEN = 3  # initials false-positive on everything (slack_capture rule)


def _iter_ws_events(workspace_root, since_ts=None):
    try:
        from events_io import iter_events

        yield from iter_events(workspace_root, since_ts=since_ts)
    except Exception:
        return


def _ev_time(ev) -> str:
    try:
        from event_time import event_time

        return event_time(ev) or ""
    except Exception:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        return ev.get("ts") or d.get("ts") or ""


# ---------------------------------------------------------------------------
# Observed-tier expiry (HYG1 Item 2 — the W4c deferred decay, derive-on-read).
# ---------------------------------------------------------------------------
#
# W4c shipped the observed tier with "30d observed expiry NOT implemented" —
# set-aside items accumulated forever: observed_counts grew unbounded, a
# stale observation could corroborate-promote off a fresh event months later,
# and cleanup's Beat-1 set-aside line inflated. Expiry is DERIVED AT READ
# TIME — no scheduler, no new event type, and events are NEVER deleted
# (append-only doctrine: an expired item stays in the log and stays
# searchable via transcript-search; it just stops surfacing and promoting).
#
# An observed item is EXPIRED when it is older than OBSERVED_EXPIRY_DAYS and
# was never promoted. Promotion is permanent — an item promoted while live
# is a real commitment forever; the observed source aging changes nothing.
#
# M-tunable builder constant (flagged in the HYG1 report, PIPE1
# haircut-weights style): 30 days matches the tier's design intent — an
# observed item is context for the current stretch of work, not an archive.
OBSERVED_EXPIRY_DAYS = 30


def observed_expired(ev, *, promoted_ids=None, now=None) -> bool:
    """True iff this observed event is past OBSERVED_EXPIRY_DAYS and was
    never promoted. `promoted_ids` is the caller's precomputed
    `_promoted_ids(events)` set (every reader below already has the event
    list); `now` is an aware datetime for tests. An event with no parseable
    timestamp never expires (conservative: visible beats silently-vanished)."""
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    oid = str(d.get("id") or "")
    if oid and promoted_ids and oid in promoted_ids:
        return False
    # INTAKE1 — the clock starts when the row was HELD, not when the promise
    # was made. A row with no `held_at` (every row written before this build)
    # ages from its own timestamp exactly as it always did.
    ts = str((ev.get("data") or {}).get(HELD_AT_KEY) or "") or _ev_time(ev)
    if not ts:
        return False
    try:
        when = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    now = now or _clock_now()
    return (now - when) > _dt.timedelta(days=OBSERVED_EXPIRY_DAYS)


# ---------------------------------------------------------------------------
# Mode resolution — SCL1 directives, read at capture time.
# ---------------------------------------------------------------------------

_MODE_PATTERNS = (
    (re.compile(r"(?i)track[- ]everything"), MODE_TRACK_EVERYTHING),
    (
        re.compile(r"(?i)team[- ]delegation|what my team commits|team commitments"),
        MODE_TEAM_DELEGATION,
    ),
    (
        re.compile(r"(?i)observed[- ]only|keep on file without asking"),
        MODE_OBSERVED_ONLY,
    ),
    (
        re.compile(r"(?i)party[- ]only|only (?:track )?what involves me"),
        MODE_PARTY_ONLY,
    ),
)
_ORG_OVERRIDE_RE = re.compile(r"(?i)^for\s+(.+?)\s*[:,—-]\s*(.+)$")


def _match_mode(text: str) -> Optional[str]:
    for pat, mode in _MODE_PATTERNS:
        if pat.search(text or ""):
            return mode
    return None


def parse_capture_directives(directives) -> dict:
    """Parse SCL1 directive dicts (`skill_custom_writer.load_directives` shape)
    into `{"mode": <global mode or None>, "org_overrides": {org_token: mode}}`.

    Grammar (the spellings `apply_gate_proposal` writes; hand-typed variants
    are matched loosely by the same phrase table):
      - global:      `capture mode: party-only` (or the mode phrase alone)
      - org override: `for <org name or id>: observed-only`
    Later directives win (SCL1 precedence: the later-dated rule beats the
    earlier on conflict). Unrecognized directives are ignored — this parser
    only ever reads; it never rejects the file."""
    mode: Optional[str] = None
    overrides: dict = {}
    for d in directives or []:
        text = str((d or {}).get("text") or "").strip()
        if not text:
            continue
        m = _ORG_OVERRIDE_RE.match(text)
        if m:
            org_mode = _match_mode(m.group(2))
            if org_mode:
                overrides[m.group(1).strip().lower()] = org_mode
            continue
        g = _match_mode(text)
        if g and g != MODE_OBSERVED_ONLY:  # observed-only is org-scoped
            mode = g
    return {"mode": mode, "org_overrides": overrides}


def _load_capture_policy(workspace_root) -> dict:
    try:
        from skill_custom_writer import load_directives

        return parse_capture_directives(
            load_directives(workspace_root, CAPTURE_POLICY_SKILL)
        )
    except Exception:
        return {"mode": None, "org_overrides": {}}


def resolve_capture_mode(
    workspace_root,
    *,
    org_id: Optional[str] = None,
    org_name: Optional[str] = None,
) -> str:
    """The effective capture mode for one capture, org override first.

    Reads the SCL1 directives fresh (skip-not-fail: absent/malformed file →
    DEFAULT_MODE). `org_id`/`org_name` is the capture source's RESOLVED org
    (the meeting's org via the entities layer) — pass what you have; the
    override key matches either, case-insensitive."""
    policy = _load_capture_policy(workspace_root)
    for token in (org_id, org_name):
        t = (str(token or "").strip().lower())
        if t and t in policy["org_overrides"]:
            return policy["org_overrides"][t]
    return policy["mode"] or DEFAULT_MODE


# ---------------------------------------------------------------------------
# Workspace capture context — who is the user, who is the team.
# ---------------------------------------------------------------------------


def workspace_capture_context(workspace_root) -> dict:
    """Everything `classify_capture` needs, resolved once per run:
    `{user_id, user_names, team_ids, known_ids, mode, org_overrides}`.

    Fail-open by design: when the primary user can't be resolved (Bug #102
    family), party-ness is undecidable, so `mode` is forced to
    track-everything and the relevance gate is inert — a broken entities
    file must never silently swallow real commitments into the observed
    tier."""
    user_id = None
    user_names: list = []
    team_ids: set = set()
    known_ids: set = set()
    # INTAKE1 rule 5 — people whose org the workspace calls a CLIENT. Read
    # here because this is the one place the entity file is already open, and
    # `classify_capture` stays pure.
    client_ids: set = set()
    client_names: set = set()
    try:
        import json as _json

        p = Path(workspace_root) / "_hq" / "data" / "entities.json"
        raw = _json.loads(p.read_text(encoding="utf-8"))
        ent = raw["entities"] if isinstance(raw.get("entities"), dict) else raw
        try:
            from primary_user import resolve_primary_user_from_entities

            user_id = resolve_primary_user_from_entities(ent)
        except Exception:
            user_id = None
        ws = ent.get("workspace") if isinstance(ent.get("workspace"), dict) else {}
        self_orgs = {
            o.get("id")
            for o in ent.get("orgs") or []
            if (o.get("relationship_type") or "").strip().lower() == "self"
        }
        client_orgs = {
            o.get("id")
            for o in ent.get("orgs") or []
            if (o.get("relationship_type") or "").strip().lower() == "client"
        }
        for person in ent.get("people") or []:
            pid = person.get("id")
            if pid:
                known_ids.add(pid)
            if pid and person.get("org_id") in client_orgs:
                client_ids.add(pid)
                for n in [person.get("canonical_name")] + list(
                        person.get("aliases") or []):
                    if n and str(n).strip():
                        client_names.add(str(n).strip().lower())
            if pid and pid == user_id:
                for n in [person.get("canonical_name")] + list(
                    person.get("aliases") or []
                ):
                    if n and str(n).strip():
                        user_names.append(str(n).strip())
            elif pid and person.get("org_id") in self_orgs:
                team_ids.add(pid)
        for key in ("user_first_name", "first_name"):
            n = (ws.get(key) or "").strip()
            if n:
                user_names.append(n)
    except Exception:
        pass

    policy = _load_capture_policy(workspace_root)
    mode = policy["mode"] or DEFAULT_MODE
    if not user_id:
        mode = MODE_TRACK_EVERYTHING  # fail-open — see docstring
    return {
        "user_id": user_id,
        "user_names": user_names,
        "team_ids": team_ids,
        "known_ids": known_ids,
        # INTAKE1 — additive keys. A caller written before this build reads
        # the same dict it always did; a caller that passes them on gets
        # rules 3 and 5.
        "client_ids": client_ids,
        "client_names": client_names,
        "second_witness": second_witness_enabled(workspace_root),
        "mode": mode,
        "org_overrides": policy["org_overrides"],
    }


# ---------------------------------------------------------------------------
# The caution rail + the relevance classification.
# ---------------------------------------------------------------------------


def carries_due_or_money(data: dict) -> bool:
    """The asymmetric caution rail's test: a parseable due date, or a money
    amount in the title/evidence (conservative detector — see _MONEY_RE)."""
    if parse_iso_date((data or {}).get("due")):
        return True
    text = f"{(data or {}).get('title') or ''} {(data or {}).get('evidence') or ''}"
    return bool(_MONEY_RE.search(text))


# ---------------------------------------------------------------------------
# INTAKE1 — the home rule, the second witness, and the importance safeguards.
# ---------------------------------------------------------------------------
#
# THE PROBLEM, MEASURED (SPEC_FLOW1, M's book 2026-09-07): 670 captures since
# Aug 8; 68% arrive with no date and 26% with nothing to belong to; 317 sit
# open, 238 of them undated, and 197 carry nobody on the other end. Every one
# of those rows was admitted by this module. The plate is not long because the
# customer promises too much — it is long because the door lets in rows that
# nothing can ever finish, date, chase or close.
#
# THE RULE (SPEC_FLOW1 lane A, ruled by M 2026-09-07): a row OPENS only when
# it has an owner AND at least one home — a due date, a project it belongs to,
# or a counterparty who is on file. Anything else is written to the tier this
# module already has (`commitment_observed`): kept, searchable, promotable,
# rendered only by `show me what you'd hide`. NEVER a question at capture.
#
# THREE THINGS THIS RULE DELIBERATELY DOES NOT DO.
#
#   1. It does not touch the CAUTION RAIL. A row carrying a due date or a
#      money amount still opens on the first mention, ahead of everything
#      here — that is `carries_due_or_money`, shipped since W4c, and it is
#      also SPEC_FLOW1's importance safeguard (rule 5) said in the code that
#      already existed. So the home rule can only ever hold a row that is
#      UNDATED AND MONEYLESS, which is the exact population the measurement
#      above is about. It also keeps the observed writer's own refusal of
#      dated rows true, and every reader that leans on it
#      (`needs_review_queue._observed_view_groups` renders `due: None` for
#      the tier on the strength of it).
#   2. It never drops anything. Held is a tier, not a bin.
#   3. It never asks. A held row is silent until a second source corroborates
#      it (`find_corroborations`, shipped) or the day-7 safeguard below
#      turns the important ones into ONE pre-picked Staff Meeting question.
#
# COUNTERPARTY ON RECORD MEANS *NOT THE OWNER* (SELFMAIL1's finding, 2026-09-07:
# a commitment's `person_ids` carries its OWNER as well as the other side, so
# "there is a person on this row" is not the same statement as "somebody else
# is on this row"). A row whose only named person is the person who owes it has
# nobody to chase, nobody to reply, and no mail or meeting that can ever prove
# it done — it is one of the 197.

HOME_DUE = "due"
HOME_PROJECT = "project"
HOME_COUNTERPARTY = "counterparty"
HOME_KINDS = (HOME_DUE, HOME_PROJECT, HOME_COUNTERPARTY)

# The two held-at-the-door reasons. Stored on `data.observed_reason`; rendered
# by `review_reasons.render_clause`, which carries a sentence for each.
HOME_REASON_NO_OWNER = "nobody owns this yet"
HOME_REASON_NO_HOME = "no date, no project and nobody else on it"
# Rule 3 — a guess held until a second source says the same thing.
WITNESS_REASON = "a guess with no second source yet"
# Rule 4 — the door's own dedup (`commitment_dedup` collapses onto the first
# row and stamps this reason).
DOOR_DUPLICATE_REASON = "the same ask is already on your plate"

HOME_RULE_REASONS = (HOME_REASON_NO_OWNER, HOME_REASON_NO_HOME,
                     WITNESS_REASON, DOOR_DUPLICATE_REASON)

# A project binding is a PROJECT, never a mail thread. `primary_thread_id`
# carries both spellings on live rows — the group header that printed a
# Superhuman message id on M's plate (attended test B1.3, LEAK2's regression)
# is what a mail id in this field looks like on a surface. Only `project_NNN`
# is a home.
_PROJECT_ID_RE = re.compile(r"(?i)^project_[0-9a-z_]+$")

# Rule 3's corroboration window, in days. `find_corroborations` has no window
# of its own (an observed row can be promoted by a source of any age up to the
# tier's 30-day expiry); the SECOND-WITNESS rule names 7 days as the span in
# which a guess is still about the same stretch of work.
WITNESS_WINDOW_DAYS = 7

# Rule 6's window: a held row carrying money or a client, with no witness in
# this many days, becomes ONE pre-picked Staff Meeting question rather than
# lapsing where nobody ever saw it.
HELD_IMPORTANT_ASK_DAYS = 7


def project_binding_of(data: dict, primary_thread_id=None) -> str:
    """The PROJECT this row belongs to, or "" — never a mail/meeting thread id.

    Reads, in order: the caller's own `primary_thread_id` (the meeting's or
    the thread's resolved binding, which the route holds as an argument and
    not on the item), then the payload's own `primary_thread_id`, then the
    legacy flat `project_id` pre-v2.7.15 rows carry."""
    data = data or {}
    for candidate in (primary_thread_id, data.get("primary_thread_id"),
                      data.get("project_id")):
        s = str(candidate or "").strip()
        if s and _PROJECT_ID_RE.match(s):
            return s
    return ""


def counterparty_on_record(data: dict, known_ids=frozenset(),
                           owner_id=None, person_ids=()) -> str:
    """The id of a counterparty who is BOTH on file and NOT the owner, or "".

    SELFMAIL1's finding said in this module's own vocabulary: the owner is a
    party to every row, so a party check that counts them answers "is anyone
    named here?" when the question the home rule asks is "is anyone ELSE named
    here, whose reply or mail or meeting could ever show this done?".

    IT READS THE ENVELOPE AS WELL AS THE PAYLOAD (fix round 1, review F-2).
    Not every writer puts the other side inside `data`: `session_sweep` lifts
    a recovered row's people to the EVENT's top-level `person_ids` and leaves
    `data` with no counterparty at all, so a swept row that genuinely named
    somebody was judged to name nobody and held for "no date, no project and
    nobody else on it" — with `run_session_sweep_test` red to say so. The
    same not-the-owner and on-file tests apply to those ids; a caller holding
    only the payload passes nothing and gets exactly the shipped answer."""
    from commitment_parties import counterparty_ids as _cp_ids

    owner = str(owner_id or (data or {}).get("owner_id") or "").strip()
    known = set(known_ids or ())
    candidates = list(_cp_ids(data or {})) + list(person_ids or ())
    for cid in candidates:
        c = str(cid or "").strip()
        if not c or c == owner:
            continue
        # An empty roster means the entity file could not be read; that is the
        # fail-open case `workspace_capture_context` already documents, and a
        # resolved id is evidence enough on its own there.
        if not known or c in known:
            return c
    return ""


def home_of(data: dict, *, known_ids=frozenset(), primary_thread_id=None,
            owner_id=None, person_ids=()) -> dict:
    """Does this row have an OWNER and a HOME? Pure.

    Returns `{"owner": <id or "">, "home": <one of HOME_KINDS or "">,
    "reason": <"" when it may open, else a HOME_RULE_REASONS string>}`."""
    data = data or {}
    owner = str(owner_id or data.get("owner_id") or "").strip()
    if not owner:
        return {"owner": "", "home": "", "reason": HOME_REASON_NO_OWNER}
    if parse_iso_date(data.get("due")):
        return {"owner": owner, "home": HOME_DUE, "reason": ""}
    if project_binding_of(data, primary_thread_id):
        return {"owner": owner, "home": HOME_PROJECT, "reason": ""}
    if counterparty_on_record(data, known_ids, owner, person_ids):
        return {"owner": owner, "home": HOME_COUNTERPARTY, "reason": ""}
    return {"owner": owner, "home": "", "reason": HOME_REASON_NO_HOME}


def is_important_capture(data: dict, *, client_ids=frozenset(),
                         client_names=frozenset()) -> bool:
    """SPEC_FLOW1 rule 5 — a row the second-witness rule may never hold back:
    it carries money, or a due date the speaker said (both of those are the
    shipped caution rail, `carries_due_or_money`), or a counterparty whose org
    the workspace calls a CLIENT.

    Rule 5 is scoped to rule 3 by its own words ("the second-witness rule can
    never apply to it") — an important row still needs a home, and rule 6 is
    what stops an important held row lapsing where nobody saw it.

    THE CLIENT TEST READS NAMES AS WELL AS IDS, and that is not belt-and-
    braces. A counterparty who is on file with an id and is not the owner is
    a HOME (rule 1), so that row opens and is never held — which would leave
    rule 6 guarding a population of nearly zero. The held row with a client
    on it is, in practice, the one whose client is named and NOT yet a person
    record: exactly the Stone shape the attended test found four passes
    running. `client_names` is that set, lowercased, built once per
    run by `workspace_capture_context`."""
    data = data or {}
    if carries_due_or_money(data):
        return True
    if not client_ids and not client_names:
        return False
    from commitment_parties import (counterparty_ids as _cp_ids,
                                    counterparty_names as _cp_names)

    owner = str(data.get("owner_id") or "").strip()
    if any(str(c).strip() and str(c).strip() != owner
           and str(c).strip() in set(client_ids)
           for c in _cp_ids(data)):
        return True
    names = {str(n or "").strip().lower() for n in (client_names or ())}
    names.discard("")
    if not names:
        return False
    return any(str(n or "").strip().lower() in names for n in _cp_names(data))


def needs_second_witness(data: dict, *, client_ids=frozenset(),
                         client_names=frozenset()) -> bool:
    """SPEC_FLOW1 rule 3 — is this row a GUESS that must wait for a second
    source? True iff the extractor stamped `pending_review` and rule 5 does
    not exempt it.

    A DUPLICATE SUSPICION IS NOT A GUESS ABOUT THE PROMISE. `pending_review`
    is one flag carrying several different doubts, and `commitment_dedup`
    stamps it to mean "this may be the same ask as an open row" — which says
    nothing about whether the ask is real. Rule 4 owns that shape now: inside
    the door window it folds onto the first row, outside it the shipped flag
    still asks. Holding it here as well would answer a question rule 4 has
    already answered, in the one case where the SAME act is demonstrably
    already on the plate. Measured on the operator's book: 7 of the 25 real
    rows this rule would otherwise have held were this shape, every one of
    them still tracked by the row it duplicates."""
    data = data or {}
    if data.get("pending_review") is not True:
        return False
    # THE CAPTURE FLOOR'S VERDICT IS NOT THE EXTRACTOR'S DOUBT. `floor_gated`
    # means "we heard this and it does not amount to much" — a judgement about
    # the ITEM — and M ruled on 2026-08-01 exactly where such a row goes: to
    # the queue, answerable in one tap, never dropped. Holding it here as well
    # would overturn that ruling as a side effect rather than as a decision,
    # and the home rule above still judges it on its own terms. Measured on
    # the operator's book: including these took the real rows held from 11 to
    # 20 of 672, and 9 of the 9 were the floor's "nothing depends on it".
    if data.get("floor_gated") or data.get("floor_code"):
        return False
    if data.get("suspected_duplicate_of"):
        reasons = [r.strip() for r in
                   str(data.get("review_reason") or "").split(";")]
        others = [r for r in reasons
                  if r and not r.startswith("looks like a duplicate")]
        if not others:
            return False
    return not is_important_capture(data, client_ids=client_ids,
                                    client_names=client_names)


def second_witness_enabled(workspace_root=None) -> bool:
    """Is the `intake.second_witness` switch ON for this workspace?

    ONE reader, and it is IDENT1's (`commitment_policy.flow_switch_enabled`) —
    that lane owns the SPEC_FLOW1 switch family and reserved this key by name
    so this lane adds a call, not a second reader with its own fallback. The
    fallback below exists only because this branch is cut from the v5.29.0
    tip, where that function does not exist yet; it is the same rule in the
    same words (default ON, only a literal `False` turns it off, every failure
    reads as ON) so the behaviour is identical before and after IDENT1 merges.
    A malformed config file is not a customer saying stop."""
    try:
        from commitment_policy import (INTAKE_SECOND_WITNESS_KEY,
                                       flow_switch_enabled)
        return flow_switch_enabled(workspace_root, INTAKE_SECOND_WITNESS_KEY)
    except ImportError:
        pass
    except Exception:
        return True
    if workspace_root is None:
        return True
    try:
        from skill_config_writer import load_skill_config
        stored = load_skill_config(workspace_root, "commitment-policy") or {}
        cfg = stored.get("config") if isinstance(stored, dict) else None
        val = (cfg or {}).get("intake.second_witness") \
            if isinstance(cfg, dict) else None
        return False if val is False else True
    except Exception:
        return True


def bind_owner_from_source(data: dict, *, user_id=None) -> bool:
    """INTAKE1 rule 2 — OWN WORDS TAKE THE USER AS OWNER. Mutates `data` in
    place; returns True when it wrote an owner.

    The narrow case, and it is narrow on purpose: a row with NO owner and NO
    counterparty of any kind, whose `kind` already means "mine by definition"
    (`task` / `scheduling` / `agenda`), captured from a source that is the
    user's OWN words — a dictation, a working session, a line the user typed.
    There is nobody else it could belong to, and "nobody said whose this is"
    is not a thing to ask about when the source only had one voice in it.
    That is the whole of the fix for the four ownerless to-dos the attended
    test found rendered as "delegated" to nobody.

    It refuses every other shape. A row naming somebody else, a row with an
    owner already, a `promise` — none of those is the user's by construction,
    and guessing on them would record a guess as a fact. On a MEETING the
    presumption stays a presumption for the same reason (`meeting_capture`'s
    `presumed_self_owed`): somebody else was in the room."""
    from commitment_parties import (counterparty_ids as _cp_ids,
                                    counterparty_names as _cp_names)

    uid = str(user_id or "").strip()
    data = data if isinstance(data, dict) else {}
    if not uid or str(data.get("owner_id") or "").strip():
        return False
    if str(data.get("owner_external") or "").strip():
        return False
    if _cp_ids(data) or _cp_names(data):
        return False
    if data.get("kind") not in ("task", "scheduling", "agenda"):
        return False
    data["owner_id"] = uid
    pids = data.get("person_ids")
    if isinstance(pids, list) and uid not in pids:
        pids.append(uid)
    return True


def intake_kwargs(capture_context: Optional[dict] = None,
                  primary_thread_id=None, person_ids=None,
                  own_words: bool = False) -> dict:
    """The INTAKE1 keyword arguments `classify_capture` takes, read off a
    `workspace_capture_context` dict, with the caller's own project binding
    and — for a writer that keeps the other side on the envelope rather than
    in the payload — the event's own `person_ids` (fix round 1, F-2).

    ONE place assembles them so a writer wires the gate by adding
    `**intake_kwargs(ctx, primary_thread_id=…)` to a call it already makes —
    and so a context built by an older caller (no `client_ids`, no
    `second_witness`) degrades to the shipped defaults rather than raising."""
    ctx = capture_context or {}
    return {
        "primary_thread_id": primary_thread_id,
        "person_ids": tuple(person_ids or ()),
        "own_words": bool(own_words),
        "client_ids": ctx.get("client_ids") or frozenset(),
        "client_names": ctx.get("client_names") or frozenset(),
        "second_witness": ctx.get("second_witness", True) is not False,
    }


def _name_matches(name, user_names) -> bool:
    """ci match of a free-text party name against the user's names: full-string
    equality, or a ≥3-char user-name token on a word boundary (the
    slack_capture user_names rule — initials never match)."""
    s = str(name or "").strip().lower()
    if not s:
        return False
    for un in user_names or ():
        u = str(un or "").strip().lower()
        if not u:
            continue
        if s == u:
            return True
        for tok in u.split():
            if len(tok) >= _MIN_NAME_TOKEN and re.search(
                rf"\b{re.escape(tok)}\b", s
            ):
                return True
    return False


def classify_capture(
    data: dict,
    *,
    mode: str = DEFAULT_MODE,
    user_id: Optional[str] = None,
    user_names=(),
    team_ids=frozenset(),
    known_ids=frozenset(),
    org_override: Optional[str] = None,
    primary_thread_id: Optional[str] = None,
    person_ids=(),
    own_words: bool = False,
    client_ids=frozenset(),
    client_names=frozenset(),
    second_witness: bool = True,
) -> dict:
    """Which tier a gated commitment payload lands in. No I/O, and no write
    to `data` EXCEPT the one the caller asks for by passing `own_words` (see
    below), which only ever fills an owner in where there was none.

    Returns `{"tier": "open"|"observed", "reason": <plain string>}`.
    Precedence: caution rail > org override > mode > INTAKE1's home rule and
    second witness. Run AFTER `gate_commitment_data` (the relevance gate
    assumes a valid capture).

    INTAKE1 (2026-09-07) adds three keyword arguments, all of them additive
    and all defaulting to the shape a pre-INTAKE1 caller passes:
    `primary_thread_id` is the meeting's / thread's resolved project binding
    (the route holds it as an argument, not on the item); `client_ids` is the
    set of people whose org the workspace calls a client (rule 5); and
    `second_witness` is the `intake.second_witness` switch's position, read
    once per run by the caller through `second_witness_enabled`. Fix round 1
    adds a fourth, `person_ids`: the EVENT's own people, for the writers that
    keep the other side on the envelope instead of in the payload (see
    `counterparty_on_record`); and a fifth, `own_words`, which says the
    source has already answered "whose is this?" so the owner is bound
    before the caution rail rather than after it.

    THE ORDER IS THE CONTRACT. The relevance verdict runs FIRST and is
    untouched: a third-party item is observed for its own reason, and the
    caution rail still lifts a dated or money row over everything. Only a row
    that WOULD HAVE OPENED reaches the two new gates, and each of them can
    only move it from `open` to `observed` — never the other way. So no row
    that used to be set aside now opens.

    Party test: the user is a party when their person id is the owner or the
    counterparty, or a free-text party name matches them; a `task` /
    `scheduling` / `agenda` item with NO party fields at all is presumed
    self-owed (that is what those kinds mean) and therefore party.
    Anything else is third-party (every named slot resolves away from the
    user) or amber (attribution unresolved) — both land observed, silently;
    corroboration or an explicit user reference promotes."""
    data = data or {}
    # THE OWNER IS BOUND BEFORE THE RAIL, NOT AFTER IT (fix round 1, review
    # F-6). The caution rail returns before `home_of` is ever consulted, so
    # an ownerless row that carries a date or an amount opens ownerless —
    # and an open row with no owner is what renders as "delegated" to nobody
    # (attended test, Part E). Binding first cannot make any row more hidden:
    # it only ever ADDS an owner, and every verdict below is at least as open
    # with one as without. Doing it here rather than in each writer makes the
    # ORDER a property of the one admission function instead of a thing five
    # callers have to remember.
    #
    # `own_words` is the caller's statement that the SOURCE has already
    # answered "whose is this?" — a dictation, a working session, a line the
    # user typed, where there was nobody else in the room. On a call it stays
    # OFF, and that is ATTRIB1's rule, untouched: the self-owed presumption
    # `classify_capture` makes for RELEVANCE stays a presumption and is never
    # written onto the row, because somebody else was in the room and a guess
    # must not be recorded as a fact. `bind_owner_from_source` refuses
    # everything else on its own — any row naming anybody, any row with an
    # owner, any `promise`.
    if own_words:
        bind_owner_from_source(data, user_id=user_id)
    if carries_due_or_money(data):
        return {"tier": "open", "reason": "carries a due date or money — always surfaces"}

    effective = org_override or mode or DEFAULT_MODE
    if effective == MODE_TRACK_EVERYTHING:
        # INTAKE1 does NOT reach this branch, deliberately. `track-everything`
        # is both a customer's explicit "keep all of it" AND the fail-open
        # mode `workspace_capture_context` forces when the primary user
        # cannot be resolved (Bug #102 family) — and in that second case
        # every row reads as ownerless, so a home rule applied here would
        # set aside a whole workspace's captures on the strength of a broken
        # entities file. Fail-open stays fail-open.
        return {"tier": "open", "reason": "track-everything"}
    if effective == MODE_OBSERVED_ONLY:
        return {"tier": "observed", "reason": "kept on file per preference for this relationship"}

    from commitment_parties import (
        counterparty_ids as _cp_ids,
        counterparty_names as _cp_names,
    )
    owner_id = data.get("owner_id")
    # MC1: the user is a party if they own it OR are ANY counterparty in the
    # full roster.
    party_ids = {i for i in ([owner_id] + _cp_ids(data)) if i}
    party_names = [data.get("owner_external")] + _cp_names(data)

    user_is_party = bool(user_id) and user_id in party_ids
    if not user_is_party:
        user_is_party = any(_name_matches(n, user_names) for n in party_names if n)
    if (
        not user_is_party
        and not party_ids
        and not any(str(n or "").strip() for n in party_names)
        and data.get("kind") in ("task", "scheduling", "agenda")
    ):
        user_is_party = True  # self-owed presumption — that's what these kinds mean

    def _intake_gates(open_reason: str) -> dict:
        """INTAKE1 rules 1 and 3, applied to a row the relevance gate would
        have opened. Held beats open; between the two held reasons the HOME
        rule speaks first, because "nobody owns this" and "this belongs
        nowhere" are statements about the row itself, while the witness rule
        is a statement about how sure the extractor was."""
        verdict = home_of(data, known_ids=known_ids,
                          primary_thread_id=primary_thread_id,
                          person_ids=person_ids)
        if verdict["reason"]:
            return {"tier": "observed", "reason": verdict["reason"]}
        if second_witness and needs_second_witness(
                data, client_ids=client_ids, client_names=client_names):
            return {"tier": "observed", "reason": WITNESS_REASON}
        return {"tier": "open", "reason": open_reason}

    if user_is_party:
        return _intake_gates("you are a party")
    if effective == MODE_TEAM_DELEGATION and party_ids & set(team_ids or ()):
        return _intake_gates("a team member is a party")

    named = [str(n or "").strip() for n in party_names]
    unresolved = any(named) or (party_ids - set(known_ids or ()))
    if party_ids and not unresolved:
        return {"tier": "observed", "reason": "between other people"}
    return {"tier": "observed", "reason": "couldn't confidently tell whose this is"}


# ---------------------------------------------------------------------------
# Observed-tier construction.
# ---------------------------------------------------------------------------


def observed_id(source_ref: str, title: str) -> str:
    """Deterministic observed-item id — `obs_<sha256[:12]>` of
    (source_ref | normalized title), so a re-scan minting the same item is
    idempotent by construction."""
    basis = f"{(source_ref or '').strip()}|{(title or '').strip().lower()}"
    return "obs_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def build_observed_event(
    title: str,
    *,
    source_ref: str,
    reason: str,
    kind: Optional[str] = None,
    owner_id: str = "",
    owner_external: str = "",
    counterparty_id: Optional[str] = None,
    counterparty_name: Optional[str] = None,
    evidence: str = "",
    channel: str = "",
    primary_thread_id: Optional[str] = None,
    person_ids=None,
    classification_confidence: Optional[float] = None,
    source_skill: str = "scan-for-commitments",
    extra_data: Optional[dict] = None,
    held_at=None,
) -> dict:
    """One set-aside item → one `commitment_observed` event dict. Context, not
    a commitment: no open item, no count, no triage/confirm row — searchable,
    feeds prep, promotable via `promote_observed`.

    ENFORCES THE CAUTION RAIL IN CODE: refuses an item carrying a due date or
    money (those ALWAYS surface as open — build a `commitment` instead).
    Construction only; append through `event_gate.append_event`."""
    title = (title or "").strip()
    if not title:
        raise CaptureGateError("an observed item needs a non-empty title")
    if not (source_ref or "").strip():
        raise CaptureGateError(f"observed item '{title}' needs a source_ref")
    probe = dict(extra_data or {})
    probe.update({"title": title, "evidence": evidence})
    if carries_due_or_money(probe):
        raise CaptureGateError(
            f"observed item '{title}' carries a due date or a money amount — "
            f"the caution rail says dated/money items ALWAYS surface as open "
            f"commitments regardless of mode; build a commitment event instead"
        )
    data: dict = {
        "title": title,
        "tier": "observed",
        "observed_reason": reason,
        "source_ref": (source_ref or "").strip(),
        "id": observed_id(source_ref, title),
    }
    if kind in ("promise", "task", "scheduling", "agenda"):
        data["kind"] = kind
    if owner_id:
        data["owner_id"] = owner_id
    elif owner_external:
        data["owner_external"] = owner_external
    if counterparty_id:
        data["counterparty_id"] = counterparty_id
    if counterparty_name and not counterparty_id:
        data["counterparty_name"] = counterparty_name
    if evidence:
        data["evidence"] = clip(evidence)
    if channel:
        data["channel"] = channel
    if extra_data:
        for k, v in extra_data.items():
            data.setdefault(k, v)
    pids = [p for p in (person_ids or []) if p]
    for pid in (owner_id, counterparty_id):
        if pid and pid not in pids:
            pids.append(pid)
    # THREADSTAMP1 DD-1, seam 1 of 3. The caller's value wins when it is a real
    # id; a blank one (None, "", whitespace — the `x.get(...) or ""` shape ~40
    # writer sites propagate) is not a value, so the PAYLOAD is asked instead.
    # ABSENT STAYS ABSENT: nothing derivable means the key is not written at
    # all, never "" and never an invented id.
    stamped_thread = (
        primary_thread_id.strip() if isinstance(primary_thread_id, str) else ""
    ) or derive_primary_thread_id({"data": data}) or ""
    ev: dict = {
        "type": OBSERVED_TYPE,
        "source_skill": source_skill,
        "person_ids": pids,
        "data": data,
    }
    if stamped_thread:
        ev["primary_thread_id"] = stamped_thread
    # CONFCLAMP1 DD-1 — validated at the write seam, not just read-side.
    stamp_confidence(ev, classification_confidence, holder="build_observed_event")
    # INTAKE1 — the day it was held (see `stamp_held_at`).
    stamp_held_at(ev, now_iso=held_at)
    return ev


HELD_AT_KEY = "held_at"


def stamp_held_at(ev: dict, *, now_iso=None, workspace_root=None) -> dict:
    """INTAKE1 — record WHEN a row was set aside, on the row.

    WHY IT IS NOT THE ROW'S OWN `ts`. A commitment's `ts` is the moment the
    promise was MADE (a meeting's start, a message's send time), and the tier
    ages rows against it — which was right while the tier only ever held
    things heard in the current stretch of work. It stops being right the
    moment the door starts holding rows, because a past-meetings backfill of
    an August call writes rows stamped August: born expired, invisible to
    `show me what you'd hide`, and — the part that actually breaks a rule —
    unable to be promoted by the second witness rule 3 promises them, since
    an expired row does not promote. So the tier ages a held row from the day
    it was HELD, and this is that day. Absent on every legacy row, and
    `observed_expired` falls back to the old reading for those."""
    d = ev.setdefault("data", {}) if isinstance(ev, dict) else {}
    if not isinstance(d, dict) or d.get(HELD_AT_KEY):
        return ev
    when = str(now_iso or "").strip()
    if not when:
        when = _clock_now(workspace_root).isoformat()
    d[HELD_AT_KEY] = when
    return ev


def observed_from_commitment_event(event: dict, *, reason: str,
                                   now_iso=None) -> dict:
    """Convert a fully-gated `commitment` event dict (pre-append) into its
    observed-tier form — used by writers that classify AFTER building the
    open shape (session_sweep). Drops the open-item-only fields
    (status / pending_review / review_reason: observed is silent by
    definition) and keeps everything else, so promotion loses nothing."""
    src = dict(event.get("data") or {})
    data = {
        k: v
        for k, v in src.items()
        if k not in ("status", "pending_review", "review_reason", "id")
    }
    data["tier"] = "observed"
    data["observed_reason"] = reason
    # INTAKE1 — KEEP THE EXTRACTOR'S OWN REASON, under a name that says it is
    # not a question. `pending_review` and `review_reason` are dropped above
    # because observed is silent by definition and a queue reader must never
    # find a question here — but since rule 3 sends every unsure capture down
    # this path, dropping the SENTENCE too would lose the one thing that says
    # what the door was unsure about ("'Rowan' isn't in your contacts yet"),
    # on the tier where that sentence is now most often the only one there is.
    # `show me what you'd hide` renders it; `promote_observed` carries it back.
    held_reason = str(src.get("review_reason") or "").strip()
    if held_reason:
        data["held_review_reason"] = held_reason
    data["id"] = observed_id(src.get("source_ref") or "", src.get("title") or src.get("summary") or "")
    out = dict(event)
    out["type"] = OBSERVED_TYPE
    out["data"] = data
    # THREADSTAMP1 DD-1, seam 2 of 3. This seam INHERITS the whole envelope, so
    # it inherits the caller's blank `primary_thread_id` too — the exact
    # propagate-emptiness shape the spec names. Derive from the payload it is
    # carrying; drop a blank key rather than re-emitting an empty sentinel.
    if not str(out.get("primary_thread_id") or "").strip():
        derived = derive_primary_thread_id(out)
        if derived:
            out["primary_thread_id"] = derived
        else:
            out.pop("primary_thread_id", None)
    # INTAKE1 — the tier ages a held row from the day it was held. See
    # `stamp_held_at`.
    stamp_held_at(out, now_iso=now_iso)
    return out


# ---------------------------------------------------------------------------
# Corroboration — the checkable promotion rule (amber is silent by default).
# ---------------------------------------------------------------------------


def _content_tokens(text) -> set:
    toks = re.findall(r"[a-z0-9']+", str(text or "").lower())
    return {t for t in toks if len(t) >= 2 and t not in _STOPWORDS}


def _party_tokens(ev: dict) -> tuple:
    """(person_id set, name-token set) for an event's parties."""
    from commitment_parties import (
        counterparty_ids as _cp_ids,
        counterparty_names as _cp_names,
    )
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    ids = {p for p in (ev.get("person_ids") or []) if p}
    if data.get("owner_id"):
        ids.add(data["owner_id"])
    ids.update(_cp_ids(data))  # MC1: full counterparty roster
    names = set()
    for text in [data.get("owner_external")] + _cp_names(data):
        for tok in str(text or "").lower().split():
            if len(tok) >= _MIN_NAME_TOKEN:
                names.add(tok)
    return ids, names


def corroborates(observed_ev: dict, candidate_ev: dict) -> bool:
    """True when `candidate_ev` corroborates `observed_ev` under the checkable
    rule: LATER event, DIFFERENT non-empty source_ref, type in
    commitment/interaction/meeting/note, sharing (i) a party — person id or
    a ≥3-char name token — AND (ii) content — stopword-stripped title-token
    Jaccard ≥ 0.5, or ≥ 3 shared content tokens. Pure."""
    if candidate_ev.get("type") not in ("commitment", "interaction", "meeting", "note"):
        return False
    o_data = observed_ev.get("data") if isinstance(observed_ev.get("data"), dict) else {}
    c_data = candidate_ev.get("data") if isinstance(candidate_ev.get("data"), dict) else {}
    o_ref = str(o_data.get("source_ref") or "").strip()
    c_ref = str(c_data.get("source_ref") or "").strip()
    # PROV2 — "DIFFERENT source_ref" is an IDENTITY question, so it is asked of
    # the derived key. Two case-variant spellings of one artifact (a legacy
    # lowercased row and a post-PROV2 case-preserved one) are ONE source, and
    # one source seen twice corroborates nothing.
    if not c_ref or dedup_key_of(c_ref) == dedup_key_of(o_ref):
        return False
    o_ts, c_ts = _ev_time(observed_ev), _ev_time(candidate_ev)
    if not o_ts or not c_ts or c_ts <= o_ts:
        return False
    o_ids, o_names = _party_tokens(observed_ev)
    c_ids, c_names = _party_tokens(candidate_ev)
    if not ((o_ids & c_ids) or (o_names & c_names)):
        return False
    o_tok = _content_tokens(o_data.get("title") or o_data.get("summary"))
    c_tok = _content_tokens(
        f"{c_data.get('title') or ''} {c_data.get('summary') or ''} "
        f"{c_data.get('evidence') or ''}"
    )
    if not o_tok or not c_tok:
        return False
    shared = o_tok & c_tok
    jaccard = len(shared) / len(o_tok | c_tok)
    return jaccard >= 0.5 or len(shared) >= 3


def matches_open_commitment(
    data: dict,
    open_events,
    *,
    person_ids=(),
    exclude_party_ids=frozenset(),
    exclude_party_names=(),
) -> Optional[dict]:
    """Corroboration-style RESTATEMENT match of a NEW capture payload against
    the OPEN set (BUG-3719 cross-channel dedup): the same real-world promise
    made in a meeting and restated in a sent email must MERGE into the item
    that already tracks it, never double-track.

    Same thresholds as `corroborates`, applied capture-side: the new item and
    an open commitment match when they share (i) a party — a person id or a
    ≥3-char name token — AND (ii) content — stopword-stripped title-token
    Jaccard ≥ 0.5, or ≥ 3 shared content tokens.

    `exclude_party_ids` / `exclude_party_names` MUST carry the workspace
    owner's id and names when the new capture is the user's own promise
    (sent mail, own Slack messages): the user is a party to every own promise
    and to most open items, so user-overlap alone would link two unrelated
    items — the party test has to be carried by the OTHER side (the
    counterparty). A new item with no non-user party never matches here
    (fail-open: the append path's semantic dedup, v4.6.0 C4, still flags
    suspects).

    Returns the FIRST matching open event (append order), else None. Pure.
    """
    data = data or {}
    excl_ids = {str(i) for i in (exclude_party_ids or ()) if i}
    excl_names = set()
    for n in exclude_party_names or ():
        for tok in str(n or "").lower().split():
            if len(tok) >= _MIN_NAME_TOKEN:
                excl_names.add(tok)

    new_ids = {str(p) for p in (person_ids or ()) if p}
    for k in ("owner_id", "counterparty_id"):
        if data.get(k):
            new_ids.add(str(data[k]))
    new_ids -= excl_ids
    new_names = set()
    for k in ("owner_external", "counterparty_name"):
        for tok in str(data.get(k) or "").lower().split():
            if len(tok) >= _MIN_NAME_TOKEN:
                new_names.add(tok)
    new_names -= excl_names
    if not new_ids and not new_names:
        return None

    new_tok = _content_tokens(data.get("title") or data.get("summary"))
    if not new_tok:
        return None

    for ev in open_events or []:
        if ev.get("type") != "commitment":
            continue
        ev_ids, ev_names = _party_tokens(ev)
        ev_ids = {str(i) for i in ev_ids} - excl_ids
        ev_names = ev_names - excl_names
        if not ((new_ids & ev_ids) or (new_names & ev_names)):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        ev_tok = _content_tokens(
            f"{d.get('title') or ''} {d.get('summary') or ''} "
            f"{d.get('evidence') or ''}"
        )
        if not ev_tok:
            continue
        shared = new_tok & ev_tok
        jaccard = len(shared) / len(new_tok | ev_tok)
        if jaccard >= 0.5 or len(shared) >= 3:
            return ev
    return None


def _promoted_ids(events) -> set:
    """Observed ids a promotion took out of the tier — MINUS the ones a
    later `promotion_reversed` marker put back (POLICY1-B DD-7 / F-1: the
    calendar closer's undo returns an observed guess to the tier exactly as
    it was; the promoted commitment stays closed history, never a question)."""
    promoted: dict = {}
    reversed_ids: set = set()
    for ev in events:
        d = ev.get("data") or {}
        if ev.get("type") == "commitment" and d.get("promoted_from"):
            promoted[str(d.get("promoted_from"))] = str(d.get("id") or "")
        elif ev.get("type") == "commitment_updated" and d.get("promotion_reversed") is True:
            if d.get("observed_id"):
                reversed_ids.add(str(d.get("observed_id")))
    return {oid for oid in promoted if oid not in reversed_ids}


def find_corroborations(workspace_root, *, since_ts=None, now=None) -> list:
    """Scan the event log for observed items whose corroboration has arrived.
    Returns `[{observed, corroborated_by}]` (first corroborating event per
    item), excluding already-promoted items AND expired items (HYG1: a stale
    observation must not promote off a fresh event). One pass, never raises."""
    events = list(_iter_ws_events(workspace_root, since_ts=since_ts))
    promoted = _promoted_ids(events)
    out = []
    for obs in events:
        if obs.get("type") != OBSERVED_TYPE:
            continue
        oid = (obs.get("data") or {}).get("id")
        if oid and str(oid) in promoted:
            continue
        if observed_expired(obs, promoted_ids=promoted, now=now):
            continue
        for cand in events:
            if corroborates(obs, cand):
                out.append({"observed": obs, "corroborated_by": cand})
                break
    return out


def promote_observed(
    workspace_root,
    observed_ref,
    *,
    corroborated_by: str = "",
    evidence: str = "",
    due: Optional[str] = None,
    source_skill: str = "scan-for-commitments",
) -> dict:
    """Promote one observed item into the confirm flow: append a REAL
    `commitment` event carrying the observed item's payload +
    `pending_review: true` + `promoted_from: <obs id>`. W4b's confirm
    section picks it up purely by data contract (a pending_review capture) —
    nothing here renders anything.

    `observed_ref` is the observed item's `data.id` (or its seq).
    `corroborated_by` is a human-readable pointer ("user" for an explicit
    reference, else the corroborating event's source_ref). Idempotent: a
    second promotion of the same item is a no-op. Returns
    `{ok, already?, commitment?, reason?}`."""
    events = list(_iter_ws_events(workspace_root))
    want = str(observed_ref)
    obs = None
    for ev in events:
        if ev.get("type") != OBSERVED_TYPE:
            continue
        d = ev.get("data") or {}
        if str(d.get("id")) == want or str(ev.get("seq")) == want:
            obs = ev
    if obs is None:
        return {"ok": False, "reason": f"no set-aside item matches {observed_ref!r}"}
    od = obs.get("data") or {}
    oid = str(od.get("id") or "")
    promoted = _promoted_ids(events)
    if oid and oid in promoted:
        return {"ok": True, "already": True}
    if observed_expired(obs, promoted_ids=promoted):
        # HYG1: past the 30-day window and never promoted — it no longer
        # counts, surfaces, or promotes. The event itself stays in the log
        # (append-only); re-observe the item fresh if it's still real.
        return {"ok": False, "reason": (
            f"set-aside item {observed_ref!r} is more than "
            f"{OBSERVED_EXPIRY_DAYS} days old and expired — if it's still "
            "real, capture it fresh from a current mention")}

    # REVIEW TITLEMINT1 R-1 — the promoted row is a COMMITMENT, and no
    # commitment may reach the substrate without a title. `build_observed_event`
    # refuses an empty one, so a well-formed observed row always carries it;
    # a legacy or malformed row does not, and `gate_commitment_data` below does
    # not check titles. This branch matters more than the arithmetic suggests:
    # the promoted commitment is stamped `pending_review: True` just below, and
    # that flag is one of the three downgrades that turns a non-title-basis
    # `auto_resolve` into a PROPOSAL — so a titleless promotion is the shortest
    # path to handing an empty title to `build_pending_review_event`, which
    # refuses it with an uncaught raise inside a nightly reconcile.
    _promoted_title = str(od.get("title") or "").strip()
    if not _promoted_title:
        return {"ok": False, "reason": (
            f"set-aside item {observed_ref!r} has no title — a commitment "
            "with no subject cannot be surfaced, confirmed, or asked about; "
            "re-capture it fresh from a current mention")}
    data: dict = {
        "title": _promoted_title,
        "kind": od.get("kind") or "promise",
        "source_ref": od.get("source_ref") or "",
        "promoted_from": oid or str(obs.get("seq")),
        "pending_review": True,
        "review_reason": (
            f"set-aside item promoted (corroborated by "
            f"{corroborated_by or 'a later mention'}) — confirm before it counts"
        ),
    }
    # Origin discriminator (ACCOUNT_SCOPE §4a): a promoted set-aside item was
    # extracted from a connector read — stamp origin so the account-scope wall
    # treats it STRICT. Stamped only when the observed item carried provenance
    # (it always should — observed items require it); a legacy provenance-less
    # one stays unstamped and gets the legacy scope_only treatment.
    if data["source_ref"]:
        data["origin"] = "connector"
    if parse_iso_date(due):
        data["due"] = (due or "").strip()[:10]
    else:
        data["no_due"] = True
    for k in ("owner_id", "owner_external", "counterparty_id", "counterparty_name",
              "counterparty_ids", "counterparty_names", "evidence", "channel",
              # INTAKE1 — THE CONVERSATION ANCHOR AND THE SOURCE POINTERS COME
              # BACK TOO. A promotion used to drop `thread_ref`, which is the
              # only thing the reply rail grades a close on (REPLYCLOSE R1's
              # THREAD_BASIS): a promoted row could never be closed by the
              # reply that answered it. That was survivable while almost
              # nothing was held; since rule 3 sends every unsure capture down
              # this path it would silently disarm the exit door for most of
              # them, so it is fixed here rather than worked around downstream.
              "thread_ref", "meeting_date", "source_event_seq",
              "primary_thread_id", "project_id"):
        if od.get(k):
            data[k] = od[k]
    if evidence:
        data["evidence"] = clip(evidence)
    gate_commitment_data(data, subject=f"promoted item {oid or observed_ref}")
    data["status"] = "open"
    if (data.get("due")
            and _dt.date.fromisoformat(data["due"])
            < _clock_now(workspace_root).date()):
        data["status"] = "overdue"

    # THREADSTAMP1 DD-1, seam 3 of 3. A promotion is a NEW `commitment` row —
    # a thread-bound type — built from the observed row's envelope. Inherit the
    # observed thread id when it is real, else derive from what the observed
    # row carries (its own payload came through seam 1, but a legacy row
    # captured before this train has neither). Absent stays absent.
    promoted_thread = str(obs.get("primary_thread_id") or "").strip() or (
        derive_primary_thread_id(obs) or ""
    )
    ev: dict = {
        "type": "commitment",
        "source_skill": source_skill,
        "person_ids": list(obs.get("person_ids") or []),
        "data": data,
    }
    if promoted_thread:
        ev["primary_thread_id"] = promoted_thread
    # CONFCLAMP1 DD-1 — the observed row this promotion inherits from may
    # predate the clamp, so the score is validated on the way OUT too.
    stamp_confidence(
        ev, obs.get("classification_confidence"), holder="promote_observed"
    )
    from event_gate import append_event

    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    # REVIEW OBSERVED1 E2 — return the STAMPED copy (allocated seq, minted
    # id), not the pre-append dict: append_event returns it precisely so
    # callers can address what they just wrote without re-scanning the log
    # (BUG-8330 item 7), and the confirm queues' dispatch does exactly that.
    stamped = append_event(events_path, ev, holder=source_skill)
    return {"ok": True, "commitment": stamped[0] if stamped else ev}


# ---------------------------------------------------------------------------
# Audit affordance + prep-context surface.
# ---------------------------------------------------------------------------


def observed_counts(workspace_root, *, since_ts=None, now=None) -> dict:
    """The weekly cleanup note's data: how many items the gate set aside.
    Returns `{observed, promoted, expired, by_reason}` for the window
    (all-time when `since_ts` is None). `observed` counts LIVE items only
    (HYG1: the set-aside sentence must not inflate with 30-day-expired
    items); `expired` is the audit-line count of never-promoted items past
    the window. Backs the one-liner 'N items set aside this week — review'.
    Never raises."""
    observed = 0
    promoted = 0
    expired = 0
    by_reason: dict = {}
    events = list(_iter_ws_events(workspace_root, since_ts=since_ts))
    promoted_ids = _promoted_ids(events)
    for ev in events:
        ts = _ev_time(ev)
        if since_ts and ts and ts < str(since_ts):
            continue
        d = ev.get("data") or {}
        if ev.get("type") == OBSERVED_TYPE:
            if observed_expired(ev, promoted_ids=promoted_ids, now=now):
                expired += 1
                continue
            observed += 1
            reason = str(d.get("observed_reason") or "other")
            by_reason[reason] = by_reason.get(reason, 0) + 1
        elif ev.get("type") == "commitment" and d.get("promoted_from"):
            promoted += 1
    return {"observed": observed, "promoted": promoted, "expired": expired,
            "by_reason": by_reason}


def prep_context_observed(workspace_root, attendee_person_ids, *, limit: int = 5) -> list:
    """Observed items involving any of these attendees — the prep-context
    surface ('last time Mira owed Lyra the report'). SURFACING ONLY: prep
    renders these with a track-it affordance; promotion stays one explicit
    tap away (`promote_observed(..., corroborated_by="user")`) so recurring
    meetings never auto-refill the confirm flow. Newest first."""
    want = {p for p in (attendee_person_ids or []) if p}
    if not want:
        return []
    events = list(_iter_ws_events(workspace_root))
    promoted = _promoted_ids(events)
    hits = []
    for ev in events:
        if ev.get("type") != OBSERVED_TYPE:
            continue
        d = ev.get("data") or {}
        if str(d.get("id")) in promoted:
            continue
        if observed_expired(ev, promoted_ids=promoted):
            continue  # HYG1: expired items never resurface in prep context
        ids, _ = _party_tokens(ev)
        if ids & want:
            hits.append(ev)
    hits.sort(key=_ev_time, reverse=True)
    return hits[:limit]


# ---------------------------------------------------------------------------
# OBSERVED1 — the confirm queues' read of the tier.
# ---------------------------------------------------------------------------

# The verbs an observed row renders, on BOTH confirm queues (needs-your-call
# and `show watching`) — defined ONCE, here, in the tier's own module, for the
# same reason QUEUE_ROW_ACTIONS is defined once in needs_review_queue: a verb
# that appears on one surface and not the other is two queues wearing one
# name. Deliberately NOT the full queue verb set: `already done` attests a
# completion, and an observed row was never tracked, so there is nothing whose
# completion can be attested; `not mine` is what the observed tier already IS
# (a third-party item the gate kept without opening), so the honest dismissal
# is `drop`.
OBSERVED_ROW_ACTIONS = ["confirm", "drop"]

# The section label both queues render live observed rows under. One string,
# so the two surfaces cannot drift into two names for the same tier.
OBSERVED_SECTION_TITLE = "SET ASIDE — heard on your calls, not tracked"


def live_observed(workspace_root, *, since_ts=None, now=None) -> list[dict]:
    """Every LIVE observed event — unexpired and never promoted — append
    order preserved. THE liveness read the confirm queues use (OBSERVED1).

    "Live" is exactly the predicate the tier's other readers already apply
    inline (`find_corroborations`, `prep_context_observed`): not past the
    HYG1 30-day window, and not already promoted into the confirm flow — a
    promotion is permanent, and a promoted row's question now lives on the
    promoted commitment. Duplicate `data.id`s (a re-scan writing the same
    deterministic id) collapse to the LATEST event. Never raises.

    Why the queues need their own entry point rather than reusing prep's:
    `prep_context_observed` filters by attendee and caps at 5 — correct for a
    brief, and exactly the shape that made this tier's bug invisible (a row
    prep CITED had no surface where a human could answer it). The confirm
    queues must see the whole live tier or the negative pin in
    `run_observed1_test.py` — nothing prep cites is unanswerable — cannot
    hold."""
    events = list(_iter_ws_events(workspace_root, since_ts=since_ts))
    promoted = _promoted_ids(events)
    by_id: dict = {}
    order: list = []
    for ev in events:
        if ev.get("type") != OBSERVED_TYPE:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        oid = str(d.get("id") or "")
        # REVIEW OBSERVED1 R1 — a legacy row with NO data.id promotes by SEQ
        # (`promote_observed` writes `promoted_from: str(seq)` for exactly
        # that shape), so the promoted-skip has to honor both spellings or
        # the answered row keeps rendering as live.
        sid = str(ev.get("seq")) if ev.get("seq") is not None else ""
        if (oid and oid in promoted) or (sid and sid in promoted):
            continue
        if observed_expired(ev, promoted_ids=promoted, now=now):
            continue
        key = oid or f"seq:{ev.get('seq')}"
        if key not in by_id:
            order.append(key)
        by_id[key] = ev
    return [by_id[k] for k in order]


def held_important_asks(workspace_root, *, now=None, client_ids=None,
                        client_names=None, limit: int = 3) -> list[dict]:
    """SPEC_FLOW1 rule 6 — the held rows that have earned ONE question.

    A row this module set aside at the door, carrying money or a CLIENT
    counterparty, with no second source in `HELD_IMPORTANT_ASK_DAYS`, is the
    one class the design rule does allow a question for: it is important, it
    is geared, and the alternative is that it lapses where nobody ever saw
    it. Everything else in the tier stays silent and lapses on the tier's
    own window — that is the whole point of holding it.

    Oldest first, capped (the Staff Meeting's budget is the surface's, not
    this reader's, but a reader that can hand it fifty rows has given the
    budget nothing to work with). Returns the observed EVENTS; the surface
    composes the row. Never raises — a fire must survive an unreadable
    entities file, and no question is the safe side of this call."""
    try:
        rows = live_observed(workspace_root, now=now)
    except Exception:  # pragma: no cover — defensive, same posture as callers
        return []
    if not rows:
        return []
    if client_ids is None or client_names is None:
        try:
            _ctx = workspace_capture_context(workspace_root)
        except Exception:  # pragma: no cover
            _ctx = {}
        if client_ids is None:
            client_ids = _ctx.get("client_ids") or frozenset()
        if client_names is None:
            client_names = _ctx.get("client_names") or frozenset()
    now = now or _clock_now(workspace_root)
    cutoff = _dt.timedelta(days=HELD_IMPORTANT_ASK_DAYS)
    out: list = []
    for ev in rows:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if str(d.get("observed_reason") or "") not in HOME_RULE_REASONS:
            # Not a row THIS rule held — a third-party item or an
            # observed-only org is a different tier decision with a different
            # answer, and neither becomes a question by ageing.
            continue
        if not is_important_capture(d, client_ids=client_ids,
                                    client_names=client_names):
            continue
        ts = str(d.get(HELD_AT_KEY) or "") or _ev_time(ev)
        try:
            when = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=_dt.timezone.utc)
        if (now - when) < cutoff:
            continue
        out.append((when, ev))
    out.sort(key=lambda pair: pair[0])
    return [ev for _when, ev in out[:max(0, int(limit))]]


# ---------------------------------------------------------------------------
# Verb-driven tuning — propose-only, one tap writes a directive.
# ---------------------------------------------------------------------------


def _gate_fingerprint(group_key: str) -> str:
    return "cgd_" + hashlib.sha256(str(group_key).encode("utf-8")).hexdigest()[:16]


def propose_gate_directives(
    workspace_root,
    *,
    min_items: int = TUNING_MIN_ITEMS,
    min_dismiss_rate: float = TUNING_MIN_DISMISS_RATE,
    cap: int = TUNING_CAP,
    cooldown_fingerprints=None,
) -> list:
    """Mine Not-mine/Drop/dismiss outcomes per counterparty org (falling back
    to the person) and PROPOSE per-org observed-only overrides — the
    'dismissed 12 of 15 vendor captures — stop surfacing those?' rule.

    Consumed by the weekly insights pass (spec: COMMITMENT_SCHEMA.md
    § Observed tier): proposals ride the existing confirm/edit/skip widget;
    an approval calls `apply_gate_proposal` (ONE tap → ONE directive);
    a decline goes to the proposal ledger's 60-day fingerprint cooldown.
    THE GATE NEVER SELF-ADJUSTS — this function only ever reads.

    Signals: `commitment_resolved` with a dropped/not-mine resolution,
    `commitment_reassigned` (it was real, just not mine), and `chat_dismissal`
    targeting the item. Floors: ≥ `min_items` captured for the group and
    ≥ `min_dismiss_rate` of them dismissed. Returns up to `cap` proposals:
    `{fingerprint, group_key, name, total, dismissed, rate, directive_text,
    plain}`. Never raises."""
    people_org: dict = {}
    org_names: dict = {}
    try:
        import json as _json

        p = Path(workspace_root) / "_hq" / "data" / "entities.json"
        raw = _json.loads(p.read_text(encoding="utf-8"))
        ent = raw["entities"] if isinstance(raw.get("entities"), dict) else raw
        for person in ent.get("people") or []:
            if person.get("id") and person.get("org_id"):
                people_org[person["id"]] = person["org_id"]
        for org in ent.get("orgs") or []:
            if org.get("id"):
                org_names[org["id"]] = org.get("name") or org["id"]
    except Exception:
        pass

    commitments: dict = {}
    dismissed_ids: set = set()
    for ev in _iter_ws_events(workspace_root):
        t = ev.get("type")
        d = ev.get("data") or {}
        if t == "commitment":
            cid = d.get("id")
            if cid:
                commitments[str(cid)] = d
        elif t == "commitment_resolved":
            res = str(d.get("resolution") or "").strip().lower()
            cid = d.get("commitment_id") or d.get("id")
            # REVAMN1 §0-3 — a bulk LAPSE is not a dismissal. The review-tier
            # verbs close a pile nobody adjudicated row by row, so counting
            # them here would propose suppressing a counterparty the user
            # never complained about — and would defeat the ruling that a
            # re-mention after an expiry is fresh evidence. The reason set
            # lives in `event_types`, spelled once for both learners.
            if cid and res in _DISMISS_RESOLUTIONS and not _is_non_dismissal(d):
                dismissed_ids.add(str(cid))
        elif t == "commitment_reassigned":
            cid = d.get("commitment_id") or d.get("target_id")
            if cid:
                dismissed_ids.add(str(cid))
        elif t == "chat_dismissal":
            cid = d.get("target_id") or d.get("commitment_id")
            if cid:
                dismissed_ids.add(str(cid))

    from commitment_parties import (
        primary_counterparty_id as _p_cp_id,
        primary_counterparty_name as _p_cp_name,
    )
    groups: dict = {}
    for cid, d in commitments.items():
        # MC1: group by the PRIMARY (first) counterparty — the documented
        # single-value degrade; per-counterparty tuning stays out of scope.
        cp = _p_cp_id(d) or _p_cp_name(d)
        if not cp:
            continue
        org = people_org.get(cp)
        key = org or str(cp)
        name = org_names.get(org) if org else (_p_cp_name(d) or str(cp))
        slot = groups.setdefault(key, {"name": name, "total": 0, "dismissed": 0})
        slot["total"] += 1
        if cid in dismissed_ids:
            slot["dismissed"] += 1

    cooling = set(cooldown_fingerprints or ())
    existing = {
        (o or "").strip().lower()
        for o in _load_capture_policy(workspace_root)["org_overrides"]
    }
    out = []
    for key, s in sorted(groups.items(), key=lambda kv: -kv[1]["dismissed"]):
        if s["total"] < min_items or s["dismissed"] / s["total"] < min_dismiss_rate:
            continue
        if str(s["name"]).strip().lower() in existing or str(key).strip().lower() in existing:
            continue
        fp = _gate_fingerprint(key)
        if fp in cooling:
            continue
        out.append({
            "fingerprint": fp,
            "group_key": key,
            "name": s["name"],
            "total": s["total"],
            "dismissed": s["dismissed"],
            "rate": round(s["dismissed"] / s["total"], 2),
            "directive_text": f"for {s['name']}: observed-only",
            "plain": (
                f"You set aside {s['dismissed']} of the last {s['total']} things "
                f"I captured about {s['name']} — want me to keep those on file "
                f"without asking?"
            ),
        })
        if len(out) >= cap:
            break
    return out


def apply_gate_proposal(workspace_root, proposal: dict) -> dict:
    """Write ONE approved tuning proposal as a capture-policy directive
    (SCL1 `add_directive`, origin 'learned'). Called ONLY from an explicit
    user approval — never from any capture or scheduled path."""
    text = (proposal or {}).get("directive_text") or ""
    if not text.strip():
        return {"ok": False, "reason": "proposal carries no directive text"}
    try:
        from skill_custom_writer import add_directive

        return add_directive(
            workspace_root, CAPTURE_POLICY_SKILL, text.strip(), origin="learned"
        )
    except Exception as e:  # pragma: no cover
        return {"ok": False, "reason": str(e)}


__all__ = [
    "CONFIDENCE_SURFACE_MIN",
    "CaptureGateError",
    "parse_iso_date",
    "gate_commitment_data",
    "USER_SOURCE_REF_MY_PLATE",
    "granola_ref_ok",
    "user_initiated_source_ref",
    # W4c relevance gate + observed tier
    "OBSERVED_TYPE",
    "MODE_PARTY_ONLY",
    "MODE_TEAM_DELEGATION",
    "MODE_TRACK_EVERYTHING",
    "MODE_OBSERVED_ONLY",
    "DEFAULT_MODE",
    "CAPTURE_MODES",
    "CAPTURE_POLICY_SKILL",
    "parse_capture_directives",
    "resolve_capture_mode",
    "workspace_capture_context",
    "carries_due_or_money",
    "classify_capture",
    "intake_kwargs",
    "bind_owner_from_source",
    # INTAKE1 — the home rule, the second witness and the importance safeguards.
    "HOME_DUE",
    "HOME_PROJECT",
    "HOME_COUNTERPARTY",
    "HOME_KINDS",
    "HOME_REASON_NO_OWNER",
    "HOME_REASON_NO_HOME",
    "HOME_RULE_REASONS",
    "WITNESS_REASON",
    "WITNESS_WINDOW_DAYS",
    "DOOR_DUPLICATE_REASON",
    "HELD_IMPORTANT_ASK_DAYS",
    "project_binding_of",
    "counterparty_on_record",
    "home_of",
    "is_important_capture",
    "needs_second_witness",
    "second_witness_enabled",
    "held_important_asks",
    "HELD_AT_KEY",
    "stamp_held_at",
    "observed_id",
    "build_observed_event",
    "observed_from_commitment_event",
    "corroborates",
    "matches_open_commitment",
    "find_corroborations",
    "promote_observed",
    "observed_counts",
    "prep_context_observed",
    "propose_gate_directives",
    "apply_gate_proposal",
]
