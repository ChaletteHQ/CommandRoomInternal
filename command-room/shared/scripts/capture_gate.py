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
     NO open item, NO count, NO triage row, NO confirm-section row.

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
import re
import sys
from pathlib import Path
from typing import Optional, Type

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from event_types import KIND_VALUES  # noqa: E402

try:
    from confidence import CONFIDENCE_SURFACE_MIN  # noqa: E402
except Exception:  # pragma: no cover
    CONFIDENCE_SURFACE_MIN = 0.7


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
    if (
        isinstance(classification_confidence, (int, float))
        and classification_confidence < _floor
    ):
        reasons.append(
            f"extraction confidence {classification_confidence} below threshold"
        )
    if reasons:
        data["pending_review"] = True
        data.setdefault("review_reason", "; ".join(reasons))


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
_DISMISS_RESOLUTIONS = frozenset({"dropped", "not_mine", "not mine"})

# The caution rail's money detector — deliberately conservative: a currency
# symbol followed by a number, or a number followed by a currency word. Bare
# "5k" does NOT match ("5k run", "10k users" would false-positive the rail
# into exactly the noise W4c removes).
_MONEY_RE = re.compile(
    r"""(?ix)
      [$€£]\s*\d
    | \b\d[\d,]*(?:\.\d+)?\s*(?:k|m|mm)?\s*
      (?:dollars|bucks|usd|eur|euros|gbp|pounds|grand)\b
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
    ts = _ev_time(ev)
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
        for person in ent.get("people") or []:
            pid = person.get("id")
            if pid:
                known_ids.add(pid)
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
) -> dict:
    """Which tier a gated commitment payload lands in. Pure — no I/O.

    Returns `{"tier": "open"|"observed", "reason": <plain string>}`.
    Precedence: caution rail > org override > mode. Run AFTER
    `gate_commitment_data` (the relevance gate assumes a valid capture).

    Party test: the user is a party when their person id is the owner or the
    counterparty, or a free-text party name matches them; a `task` /
    `scheduling` / `agenda` item with NO party fields at all is presumed
    self-owed (that is what those kinds mean) and therefore party.
    Anything else is third-party (every named slot resolves away from the
    user) or amber (attribution unresolved) — both land observed, silently;
    corroboration or an explicit user reference promotes."""
    data = data or {}
    if carries_due_or_money(data):
        return {"tier": "open", "reason": "carries a due date or money — always surfaces"}

    effective = org_override or mode or DEFAULT_MODE
    if effective == MODE_TRACK_EVERYTHING:
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

    if user_is_party:
        return {"tier": "open", "reason": "you are a party"}
    if effective == MODE_TEAM_DELEGATION and party_ids & set(team_ids or ()):
        return {"tier": "open", "reason": "a team member is a party"}

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
    ev: dict = {
        "type": OBSERVED_TYPE,
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "person_ids": pids,
        "data": data,
    }
    if classification_confidence is not None:
        ev["classification_confidence"] = classification_confidence
    return ev


def observed_from_commitment_event(event: dict, *, reason: str) -> dict:
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
    data["id"] = observed_id(src.get("source_ref") or "", src.get("title") or src.get("summary") or "")
    out = dict(event)
    out["type"] = OBSERVED_TYPE
    out["data"] = data
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
    if not c_ref or c_ref == o_ref:
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
    return {
        str((ev.get("data") or {}).get("promoted_from"))
        for ev in events
        if ev.get("type") == "commitment"
        and (ev.get("data") or {}).get("promoted_from")
    }


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

    data: dict = {
        "title": od.get("title") or "",
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
              "counterparty_ids", "counterparty_names", "evidence", "channel"):
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

    ev: dict = {
        "type": "commitment",
        "source_skill": source_skill,
        "primary_thread_id": obs.get("primary_thread_id"),
        "person_ids": list(obs.get("person_ids") or []),
        "data": data,
    }
    if obs.get("classification_confidence") is not None:
        ev["classification_confidence"] = obs["classification_confidence"]
    from event_gate import append_event

    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    append_event(events_path, ev, holder=source_skill)
    return {"ok": True, "commitment": ev}


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
            if cid and res in _DISMISS_RESOLUTIONS:
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
