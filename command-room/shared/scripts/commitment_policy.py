#!/usr/bin/env python3
"""POLICY1-A — ONE resolution policy: closing on evidence, part one.

WHY THIS MODULE EXISTS
----------------------
Three closers (the transcript pass, the sent-mail rail, the reply rail) and
one proposal builder each carried their own idea of when a match is strong
enough to close, when it is worth a question, and how long a question may
stand. The thresholds lived in `confidence.py`, the ladder lived in each
matcher, the TTL lived in `reconcile_sent_commitments.py` (14 days) and
nowhere at all for the transcript rail, and the orchestrator prose repeated
the numbers. Measured on the operator's own month (SPEC_POLICY1 v2 §5): 726
proposals, 561 of them aimed at rows that were ALREADY a question, 87 items
asked three times or more, one item asked 21 times, and not one proposal or
close carrying the words that triggered it.

This module is the one home for:

  * the two MATCH thresholds (`MATCH_SCORE_AUTO_RESOLVE`,
    `MATCH_SCORE_PENDING_REVIEW`) — `confidence.py` re-exports them so its
    importers do not move, and its two match accessors delegate here;
  * `decide()` — the pure, total resolution table (DD-1);
  * the proposal LIFECYCLE readers (once per item + source, supersession on
    re-score, retract at `PROPOSAL_TTL_DAYS`) — D4 / D15;
  * the completion-turn QUOTE every proposal and transcript close carries
    (D5) and the refusal of the old fixed f-string;
  * the CLOSEID2 door a matcher may honestly claim (`"match"`).

RULINGS ENCODED (do not re-decide here)
  * 2026-08-01 "transcript auto-close KEEP AS-IS": a CONFIRMED row closes at
    score >= 0.55 with a completion signal. No preset moves that bar.
  * 2026-07-15 FS-11 ("if they are closed, just close them"): an unambiguous
    moderate sent-mail match closes. SENTMATCH: a delivered artifact closes.
    REPLYCLOSE R1: a thread-anchored reply from the owner closes. These are
    NAMED ROWS of the table, not exceptions to it.
  * 2026-09-03 M ruling 1 (D3): a >= 0.55 + completion match on an
    UNCONFIRMED guess CLOSES IT AS DONE, automatically — `confirmed_by:
    "transcript"`, the verbatim completion turn as the evidence on the row,
    `resolution_reason: auto_closed_transcript_evidence`, receipted and
    undoable. No question, no proposal in that band. `confirm_close` is the
    action; the writer's pending guard has a `confirmed_by` door for it.
  * 2026-09-03 M ruling 1 (the narrower chip band): only a CORROBORATING
    match scoring inside [`CHIP_BAND_LOW`, `CHIP_BAND_HIGH`] (0.65-0.80)
    writes anything at all — ONE in-row chip carrying the quote (PLATE1 P7
    `data.proposal`) that ACTS OR RETRACTS ITSELF at the TTL. It never
    repeats, never nags, and never waits on a human answer. Outside that
    band a corroborating match is silent.
  * 2026-09-03 M ruling 5: the standing unanswered window is FOUR days
    (`PROPOSAL_TTL_DAYS = 4`) everywhere it appears. UNCONFEXP1's 2-day
    lapse for unconfirmed captures is unchanged.
  * UNCONFEXP1: below the bar on a pending row NOTHING is written — the row
    is already a question and the daily drain owns its lapse.
  * DEVELOPMENT.md:56 additive-only history: a re-score APPENDS a proposal
    carrying `supersedes_seq`; `match_score` is never edited.

DD-1 — `decide` is PURE and TOTAL. Inputs are plain values; every valid
input yields an action; invalid `target_state` / `preset` raise (a typo must
fail loud, never fall through to a permissive default). The table is a
module-level dict the suite enumerates cell by cell.

Imports NOTHING from `confidence` at module level (F10): `confidence` imports
the two constants from HERE, and `_BAKED` references them, so the override
file (`_hq/data/confidence-overrides.json`, the Loop-4 calibration path — not
a user slider) keeps being honoured for both keys through ONE reader below.

stdlib only. Names in docstrings/tests are the Sample/Stone roster.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# THE THRESHOLDS — the one home (D1). Moved from confidence.py:76/:80.
# ---------------------------------------------------------------------------

# At or above this score, WITH a completion-class signal, a CONFIRMED target
# closes (the 2026-08-01 ruling; 202 completion closes on the operator's
# month came through it). Below it but >= PENDING, the match is worth ONE
# question. Used by: cru_match HIGH_CONFIDENCE_THRESHOLD (alias).
MATCH_SCORE_AUTO_RESOLVE = 0.55

# Below this: no action on the title path. Used by: cru_match
# PENDING_REVIEW_THRESHOLD (alias).
MATCH_SCORE_PENDING_REVIEW = 0.30

# D15 / M ruling 5 — how long an in-row chip stands before it RESOLVES
# ITSELF: it applies its own evidence (a close, when that evidence meets the
# close bar) or retracts (a `commitment_review_dismissed`, reason
# `policy_retracted` — CLOSEID2: a dismissal is not a closure). Never a
# repeat, never a nag, never a wait. FOUR days is M's standing window
# (2026-09-03 ruling 5; was 7 in the spec, 14 privately in the sent rail).
PROPOSAL_TTL_DAYS = 4

MATCH_THRESHOLD_NAMES = ("MATCH_SCORE_AUTO_RESOLVE", "MATCH_SCORE_PENDING_REVIEW")

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

TARGET_CONFIRMED = "confirmed"
TARGET_PENDING = "pending_review"
TARGET_CLOSED = "closed"
TARGET_STATES = (TARGET_CONFIRMED, TARGET_PENDING, TARGET_CLOSED)

ACTION_CLOSE = "close"
# M ruling 1 — the guess lane: the target is an UNCONFIRMED capture and the
# evidence meets the close bar, so it closes as done through the writer's
# `confirmed_by` door. A separate action from `close` because the WRITE
# differs (the door, the reason, `data.confirmed_by`) and because every
# reader that wants to count machine confirmations apart from ordinary
# closes can.
ACTION_CONFIRM_CLOSE = "confirm_close"
ACTION_PROPOSE = "propose"
ACTION_NONE = "none"
ACTION_RETRACT = "retract"
ACTIONS = (ACTION_CLOSE, ACTION_CONFIRM_CLOSE, ACTION_PROPOSE, ACTION_NONE,
           ACTION_RETRACT)
# `park` is lane B (D10) and is deliberately NOT registered here — no cell of
# the table can yield it.
LANE_B_ACTIONS = ("park",)

# M ruling 1 — the ONLY band that still writes a question-shaped row. A
# corroborating match inside it writes ONE in-row chip (PLATE1 P7
# `data.proposal`) that resolves ITSELF at `PROPOSAL_TTL_DAYS`; outside it a
# corroborating match writes nothing at all.
CHIP_BAND_LOW = 0.65
CHIP_BAND_HIGH = 0.80

# The door and the reason a machine close states about itself. The spellings
# live in `event_types` with the rest of the resolution vocabulary; the local
# fallback keeps import order safe (this module imports nothing heavy).
try:
    from event_types import AUTO_CLOSE_CONFIRMED_BY as CONFIRMED_BY_TRANSCRIPT
    from event_types import AUTO_TRANSCRIPT_CLOSE_REASON as AUTO_CLOSE_REASON
except Exception:  # pragma: no cover — vocabulary home unreadable
    CONFIRMED_BY_TRANSCRIPT = "transcript"
    AUTO_CLOSE_REASON = "auto_closed_transcript_evidence"

# Presets (D13 / DD-8, the canonical three rows shared with QUIET1). Presets
# govern ASKING, never closing: the close column is identical in all three.
PRESET_ENGAGED = "engaged"
PRESET_LIGHT = "light"
PRESET_QUIET = "quiet"
PRESETS = (PRESET_ENGAGED, PRESET_LIGHT, PRESET_QUIET)
DEFAULT_PRESET = PRESET_ENGAGED
PRESET_SKILL_KEY = "commitment-policy"   # skill_config.schema.json allowlist
PRESET_CONFIG_KEY = "preset"
# CUT-A (M ruling R-A, 2026-09-06) — the transcript closing-on-evidence
# SWITCH. One key in the same store, read by ONE reader
# (`transcript_closes_enabled`), default OFF and fail-to-OFF: a config that
# is missing, corrupt, or carries anything but the literal `true` reads as
# OFF. It is a second key, not a fourth preset, because no preset moves a
# close (`commitment_policy_pass._effective_preset`'s contract) and this
# key moves nothing BUT the close: with it off, a transcript completion
# signal may at most write a review proposal on the row — never a close.
TRANSCRIPT_CLOSES_CONFIG_KEY = "auto_close_from_transcript"
TRANSCRIPT_CLOSES_DEFAULT = False

# Evidence classes — what the (score, signals) pair amounts to.
EVIDENCE_STRONG = "strong"              # meets a close bar (a named row below)
EVIDENCE_CORROBORATING = "corroborating"  # worth one question on a confirmed row
EVIDENCE_WEAK = "weak"                  # nothing to act on
EVIDENCE_CLASSES = (EVIDENCE_STRONG, EVIDENCE_CORROBORATING, EVIDENCE_WEAK)

# Signal keys `decide` reads. Anything else in `signals` is ignored.
SIGNAL_COMPLETION = "completion"          # transcript completion language
SIGNAL_SCHEDULE_SHIFT = "schedule_shift"  # transcript: the date moved
SIGNAL_NEW_ASK = "new_ask"                # transcript: a new ask layered on
SIGNAL_SENT = "sent"                      # the user's OWN outbound send matched (sent rail title path)
SIGNAL_DELIVERY = "delivery"              # SENTMATCH: the artifact was delivered
SIGNAL_REPLY = "reply"                    # REPLYCLOSE R1: thread-anchored owner reply
SIGNAL_UNAMBIGUOUS = "unambiguous"        # FS-11: one send <-> one item, moderate band
SIGNAL_NAMED_COUNTERPARTY = "named_counterparty"  # `light` preset gate
SIGNAL_KEYS = (SIGNAL_COMPLETION, SIGNAL_SCHEDULE_SHIFT, SIGNAL_NEW_ASK,
               SIGNAL_SENT, SIGNAL_DELIVERY, SIGNAL_REPLY, SIGNAL_UNAMBIGUOUS,
               SIGNAL_NAMED_COUNTERPARTY)

# The named rows that make evidence STRONG. Each is a standing ruling; the
# names are what the `reason` field carries so a reader of the ledger can
# see WHICH bar a close met.
BAR_COMPLETION = "completion_at_bar"      # score >= AUTO_RESOLVE + completion
BAR_SENT = "own_send_at_bar"              # score >= AUTO_RESOLVE on the user's own send (sent rail, pre-POLICY1 rule)
BAR_DELIVERY = "artifact_delivered"       # SENTMATCH
BAR_REPLY = "thread_reply"                # REPLYCLOSE R1
BAR_MODERATE_UNAMBIGUOUS = "moderate_unambiguous"  # FS-11 (M, 2026-07-15)

# Lanes (D1 signature). Not consulted by the lane-A table; carried so the
# lane-B owed-to-you rule (D11) lands as a column, not a rewrite.
LANE_OWED_BY_YOU = "owed_by_you"
LANE_OWED_TO_YOU = "owed_to_you"

# D5 — the fixed f-string the orchestrator used to write as evidence (635 of
# 726 window proposals, 198 of 212 transcript closes). REFUSED by the builder
# and by the transcript closer. Prefix match, case-sensitive: this is the one
# string, spelled the one way it was spelled.
FIXED_EVIDENCE_PREFIX = "Past meeting transcript ("

# `data.signal` on a proposal — the four words the f-string used to carry,
# moved to a field so nothing that read the prose loses information (DD-3).
SIGNAL_VALUE_COMPLETION = "completion"
SIGNAL_VALUE_SCHEDULE_SHIFT = "schedule_shift"
SIGNAL_VALUE_NEW_ASK = "new_ask"
SIGNAL_VALUE_TITLE_MATCH = "title_match"
SIGNAL_VALUES = (SIGNAL_VALUE_COMPLETION, SIGNAL_VALUE_SCHEDULE_SHIFT,
                 SIGNAL_VALUE_NEW_ASK, SIGNAL_VALUE_TITLE_MATCH)

# The CLOSEID2 door a MATCHER may claim (F5 / R6): "match" = the closer
# resolved the id by scoring the substrate; never "id", which means a
# rendered surface embedded it. Registered in
# commitment_state.RESOLVED_BY_MATCH_VALUES; the writer requires
# `data.match_score` beside it.
MATCH_DOOR = "match"

# D15 — the retract reason on the dismissal, under the shared
# `resolution_reason` key. The spelling lives in `event_types` beside the
# other lapse reasons and INSIDE `NON_DISMISSAL_RESOLUTION_REASONS` (REVIEW_
# MERGED_v5280 F-2: a system retract is not the customer's Skip); the local
# fallback keeps import order safe.
try:
    from event_types import POLICY_RETRACT_REASON as RETRACT_REASON
except Exception:  # pragma: no cover — vocabulary home unreadable
    RETRACT_REASON = "policy_retracted"

# D5 — the quote budget. Verbatim, speaker marker stripped, word-boundary cut.
QUOTE_MAX_CHARS = 240

# Batch-id prefix for a transcript fire's closes. POLICY1-B DD-5: the fire is
# the RUN (parent) batch; every close carries a GROUP batch under it — one
# group per meeting inside the fire — so `undo <group>` reverses one
# meeting's closes and `undo <run>` / `undo all` reverses the fire.
FIRE_BATCH_PREFIX = "cru_"

# DD-5 — the two keys every automatic act carries beside `brain_batch_id`.
# `parent_batch_id` is the run the group belongs to (equal to the group id
# when nothing grouped the row); `undo_group` is the human key the group was
# formed on (a thread id, a meeting ref, or the run itself), for the listing.
PARENT_BATCH_KEY = "parent_batch_id"
UNDO_GROUP_KEY = "undo_group"

# POLICY1-B (a) — WHY a transcript close was refused, by name, on the counts.
# Each is a fence working, never a close that landed.
REFUSAL_NO_TRANSCRIPT_TS = "NoTranscriptTs"     # the transcript's own start time was not handed in
REFUSAL_STALE_EVIDENCE = "StaleEvidence"        # the row was captured after the meeting started
REFUSAL_NO_COMPLETION_TURN = "NoCompletionTurn"  # no single turn both says "done" and names the item

# The change class the transcript closer stamps with its batch id, so
# `brain_undo`'s registered reverser (reopen) finds the rows (pairing rule).
CLOSE_CHANGE_CLASS = "commitment_close"


class FixedEvidenceError(ValueError):
    """D5 — the evidence string is the retired fixed f-string, not a quote."""


class PolicyInputError(ValueError):
    """`decide` was handed a target_state / preset outside the vocabulary."""


# ---------------------------------------------------------------------------
# Threshold reader — the ONE reader of the override file for the match keys
# ---------------------------------------------------------------------------

def _overrides_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "confidence-overrides.json"


def match_overrides(workspace_root) -> dict:
    """`{name: value}` for the two match keys only, from
    `_hq/data/confidence-overrides.json` (`thresholds` block), or {} when
    absent / unreadable / out of [0, 1]. Never raises. Same file, same
    validation as `confidence.load_overrides`; this is the match-key half of
    it, living where the constants live."""
    if workspace_root is None:
        return {}
    path = _overrides_path(workspace_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    thresholds_block = data.get("thresholds") if isinstance(data, dict) else None
    if not isinstance(thresholds_block, dict):
        return {}
    out = {}
    for k in MATCH_THRESHOLD_NAMES:
        v = thresholds_block.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) \
                and 0.0 <= float(v) <= 1.0:
            out[k] = float(v)
    return out


def thresholds(workspace_root=None) -> tuple:
    """(auto_resolve, pending_review), honouring a workspace override when a
    root is given, else the shipped constants. `None` reproduces the
    pre-override behaviour byte-for-byte."""
    ov = match_overrides(workspace_root) if workspace_root is not None else {}
    return (ov.get("MATCH_SCORE_AUTO_RESOLVE", MATCH_SCORE_AUTO_RESOLVE),
            ov.get("MATCH_SCORE_PENDING_REVIEW", MATCH_SCORE_PENDING_REVIEW))


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

def effective_preset(workspace_root=None) -> str:
    """The workspace's `commitment-policy.preset`, else `engaged` (DD-8:
    until QUIET1 lands, policy reads the key directly with the same
    fallback). Unknown / malformed values fall back — a preset is a posture,
    and a typo must not silence every question."""
    if workspace_root is None:
        return DEFAULT_PRESET
    try:
        from skill_config_writer import load_skill_config
        stored = load_skill_config(workspace_root, PRESET_SKILL_KEY) or {}
        cfg = stored.get("config") if isinstance(stored, dict) else None
        val = (cfg or {}).get(PRESET_CONFIG_KEY) if isinstance(cfg, dict) else None
        val = str(val or "").strip().lower()
        return val if val in PRESETS else DEFAULT_PRESET
    except Exception:
        return DEFAULT_PRESET


def transcript_closes_enabled(workspace_root=None) -> bool:
    """CUT-A (R-A) — is the transcript closing-on-evidence pass ON for this
    workspace? Modelled on `effective_preset`: the stored
    `commitment-policy.auto_close_from_transcript` key, read by IDENTITY
    (`is True`) — a string "true", a 1, a missing key, a malformed file or
    a read error are all OFF. Never fails open. `None` (no workspace) is
    OFF too: a caller with no store to read has no grant."""
    if workspace_root is None:
        return TRANSCRIPT_CLOSES_DEFAULT
    try:
        from skill_config_writer import load_skill_config
        stored = load_skill_config(workspace_root, PRESET_SKILL_KEY) or {}
        cfg = stored.get("config") if isinstance(stored, dict) else None
        val = (cfg or {}).get(TRANSCRIPT_CLOSES_CONFIG_KEY) if isinstance(cfg, dict) else None
        return val is True
    except Exception:
        return TRANSCRIPT_CLOSES_DEFAULT


# ---------------------------------------------------------------------------
# DD-1 — evidence class + the decide table
# ---------------------------------------------------------------------------

def _flag(signals, key) -> bool:
    return bool((signals or {}).get(key))


def evidence_class(score, signals=None, *, hi=None, pend=None) -> tuple:
    """(class, bar) for a (score, signals) pair.

      strong         a named close row is met — `bar` says which one
                     (completion at the bar; the user's own send at the bar;
                     a delivered artifact; a thread-anchored reply; an
                     unambiguous moderate send — each a standing ruling)
      corroborating  pend <= score with any transcript signal, or score >= hi
                     with no signal at all (the topic came up), or a new ask
                     at the bar — worth ONE question on a confirmed target
      weak           nothing to act on

    Delivery / reply / unambiguous rows are the mail rails' own findings and
    are read as signals here, so the three closers meet ONE table."""
    hi = MATCH_SCORE_AUTO_RESOLVE if hi is None else hi
    pend = MATCH_SCORE_PENDING_REVIEW if pend is None else pend
    try:
        s = float(score or 0.0)
    except (TypeError, ValueError):
        s = 0.0
    completion = _flag(signals, SIGNAL_COMPLETION)
    shift = _flag(signals, SIGNAL_SCHEDULE_SHIFT)
    new_ask = _flag(signals, SIGNAL_NEW_ASK)
    if _flag(signals, SIGNAL_DELIVERY):
        return EVIDENCE_STRONG, BAR_DELIVERY
    if _flag(signals, SIGNAL_REPLY):
        return EVIDENCE_STRONG, BAR_REPLY
    if s >= hi and completion:
        return EVIDENCE_STRONG, BAR_COMPLETION
    if s >= hi and _flag(signals, SIGNAL_SENT):
        return EVIDENCE_STRONG, BAR_SENT
    if _flag(signals, SIGNAL_UNAMBIGUOUS) and pend <= s < hi:
        return EVIDENCE_STRONG, BAR_MODERATE_UNAMBIGUOUS
    if s >= hi:
        # High title match: a shift, a new ask, or nothing but the topic.
        return EVIDENCE_CORROBORATING, ""
    if s >= pend and (completion or shift or new_ask):
        return EVIDENCE_CORROBORATING, ""
    return EVIDENCE_WEAK, ""


# THE TABLE (engaged). Cells: (target_state, evidence_class) -> action.
# Presets change only the `propose` cells (below); the close column is
# identical in all three (D13: presets govern asking, never closing).
DECIDE_TABLE = {
    (TARGET_CONFIRMED, EVIDENCE_STRONG): ACTION_CLOSE,          # D2 — today's closer
    (TARGET_CONFIRMED, EVIDENCE_CORROBORATING): ACTION_PROPOSE,  # once per (item, source)
    (TARGET_CONFIRMED, EVIDENCE_WEAK): ACTION_NONE,
    (TARGET_PENDING, EVIDENCE_STRONG): ACTION_CONFIRM_CLOSE,     # M ruling 1: close it as done, with the quote
    (TARGET_PENDING, EVIDENCE_CORROBORATING): ACTION_NONE,       # already a question (UNCONFEXP1)
    (TARGET_PENDING, EVIDENCE_WEAK): ACTION_NONE,
    (TARGET_CLOSED, EVIDENCE_STRONG): ACTION_NONE,
    (TARGET_CLOSED, EVIDENCE_CORROBORATING): ACTION_NONE,
    (TARGET_CLOSED, EVIDENCE_WEAK): ACTION_NONE,
}

_REASONS = {
    (TARGET_CONFIRMED, EVIDENCE_STRONG): "confirmed target met a close bar",
    (TARGET_CONFIRMED, EVIDENCE_CORROBORATING): "confirmed target, evidence worth one question",
    (TARGET_CONFIRMED, EVIDENCE_WEAK): "below the pending band",
    (TARGET_PENDING, EVIDENCE_STRONG): "unconfirmed guess, evidence at the close bar — closed as done, confirmed by the transcript (M ruling 1)",
    (TARGET_PENDING, EVIDENCE_CORROBORATING): "already a question; the review drain owns its lapse",
    (TARGET_PENDING, EVIDENCE_WEAK): "below the pending band",
    (TARGET_CLOSED, EVIDENCE_STRONG): "target already closed",
    (TARGET_CLOSED, EVIDENCE_CORROBORATING): "target already closed",
    (TARGET_CLOSED, EVIDENCE_WEAK): "target already closed",
}


def decide(*, target_state, score, signals=None, evidence=None,
           preset=None, kind=None, lane=None, hi=None, pend=None) -> dict:
    """The one resolution decision (DD-1). Pure; total over valid inputs.

    Returns {"action", "reason", "evidence_class", "bar", "preset",
             "proposed_resolution", "undo_group"}:
      action               close | confirm_close | propose | none
                           (`confirm_close` = an UNCONFIRMED target whose
                           evidence meets the bar — the transcript rail's
                           guess lane; the mail rails demote it, F-7)
      proposed_resolution  "supersede" when the propose is a new-ask
                           supersession, "auto_resolve" for a propose,
                           None otherwise
      undo_group           the evidence `source_ref` (the group a lane-B
                           `undo <group>` will name), "" when unknown

    `evidence` is {quote, source_ref, evidence_ts} and is carried, not judged:
    the builder and the closer refuse a bad quote; a decision does not.
    """
    if target_state not in TARGET_STATES:
        raise PolicyInputError(
            f"decide: target_state {target_state!r} is not one of {TARGET_STATES}")
    preset = DEFAULT_PRESET if preset is None else str(preset).strip().lower()
    if preset not in PRESETS:
        raise PolicyInputError(f"decide: preset {preset!r} is not one of {PRESETS}")
    cls, bar = evidence_class(score, signals, hi=hi, pend=pend)
    action = DECIDE_TABLE[(target_state, cls)]
    reason = _REASONS[(target_state, cls)]
    if bar:
        reason = f"{reason} ({bar})"
    if action == ACTION_PROPOSE:
        # M ruling 1 — the chip band. Outside [0.65, 0.80] a corroborating
        # match is silent: no chip, no question, nothing to answer.
        try:
            _s = float(score or 0.0)
        except (TypeError, ValueError):
            _s = 0.0
        if not (CHIP_BAND_LOW <= _s <= CHIP_BAND_HIGH):
            action, reason = ACTION_NONE, (
                f"outside the chip band {CHIP_BAND_LOW}-{CHIP_BAND_HIGH} — nothing is written")
        elif preset == PRESET_QUIET:
            action, reason = ACTION_NONE, "quiet preset writes no chip"
        elif preset == PRESET_LIGHT and not _flag(signals, SIGNAL_NAMED_COUNTERPARTY):
            action, reason = ACTION_NONE, "light preset chips only where the row names a counterparty"
    proposed_resolution = None
    if action == ACTION_PROPOSE:
        proposed_resolution = (
            "supersede"
            if (_flag(signals, SIGNAL_NEW_ASK) and not _flag(signals, SIGNAL_COMPLETION)
                and cls == EVIDENCE_CORROBORATING)
            else "auto_resolve")
    ev = evidence if isinstance(evidence, dict) else {}
    return {
        "action": action,
        "reason": reason,
        "evidence_class": cls,
        "bar": bar,
        "preset": preset,
        "proposed_resolution": proposed_resolution,
        "undo_group": str(ev.get("source_ref") or ""),
    }


# ---------------------------------------------------------------------------
# D5 — the quote
# ---------------------------------------------------------------------------

def is_fixed_evidence(text) -> bool:
    """True when `text` is the retired fixed f-string (D5)."""
    return isinstance(text, str) and text.lstrip().startswith(FIXED_EVIDENCE_PREFIX)


def refuse_fixed_evidence(text, *, where: str) -> None:
    if is_fixed_evidence(text):
        raise FixedEvidenceError(
            f"{where}: refusing evidence {text[:48]!r} — that is the retired "
            "fixed string, not the words that triggered this. Pass the "
            "completion turn (commitment_policy.completion_quote) verbatim.")


# The speaker marker shapes ATTRIB1-A reads (meeting_capture._SPEAKER_LABEL_RE,
# duplicated here as a FALLBACK only — the live regex is imported when the
# module is importable, so the two cannot drift while both exist).
_FALLBACK_SPEAKER_RE = re.compile(
    r"(?:(?<=\s)|^)(Me|Them|Speaker \d{1,2}|"
    r"[A-Z][a-z]+(?: [A-Z][A-Za-z'.-]+){0,2}):\s"
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD_RE = re.compile(r"[a-z0-9']+")


def _speaker_re():
    try:
        from meeting_capture import _SPEAKER_LABEL_RE
        return _SPEAKER_LABEL_RE
    except Exception:  # pragma: no cover — fallback keeps the quote working
        return _FALLBACK_SPEAKER_RE


def _completion_phrases() -> tuple:
    try:
        from cru_match import COMPLETION_PHRASES, _phrases
        return tuple(_phrases("completion_phrases", COMPLETION_PHRASES))
    except Exception:  # pragma: no cover
        return ("sent the", "sent over", "sent it", "delivered", "shared the",
                "as promised", "here's the", "attached", "wrapped up",
                "finished the", "done with", "all set on")


def transcript_turns_text(transcript_text) -> list:
    """The transcript as `[(turn_index, body)]` — one entry per speaker turn
    when markers exist (marker stripped), else one per sentence/line. Pure."""
    text = str(transcript_text or "")
    if not text.strip():
        return []
    marks = list(_speaker_re().finditer(text))
    out = []
    if marks:
        head = text[:marks[0].start()].strip()
        idx = 0
        if head:
            out.append((idx, head))
            idx += 1
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            body = text[m.end():end].strip()
            if body:
                out.append((idx, body))
            idx += 1
        return out
    for i, seg in enumerate(s for s in _SENTENCE_SPLIT_RE.split(text) if s and s.strip()):
        out.append((i, seg.strip()))
    return out


def _clip_quote(text, n: int = QUOTE_MAX_CHARS) -> str:
    s = " ".join(str(text or "").split())
    if len(s) <= n:
        return s
    cut = s[: n - 1]
    sp = cut.rfind(" ")
    if sp >= int(n * 0.6):
        cut = cut[:sp]
    return cut.rstrip() + "…"


def _tokens(text) -> set:
    return set(_WORD_RE.findall(str(text or "").lower()))


def completion_quote(transcript_text, title, *, prefer_completion: bool = True) -> dict:
    """The completion TURN — the span of the transcript the matcher scored,
    verbatim, speaker marker stripped, <= QUOTE_MAX_CHARS (D5 / DD-3).

    Picks the turn that (a) carries a completion phrase and (b) shares the
    most content words with the title; ties by earliest turn. Falls back to
    the best title-overlap turn when no turn carries completion language, and
    to the topic-aware `extract_snippet` window when no turn overlaps at all.

    Returns {"quote": str, "turn_index": int | None, "has_completion": bool}.
    `quote` is "" only when the transcript is empty."""
    turns = transcript_turns_text(transcript_text)
    if not turns:
        return {"quote": "", "turn_index": None, "has_completion": False}
    phrases = _completion_phrases() if prefer_completion else ()
    title_toks = _tokens(title)
    best = None
    for idx, body in turns:
        lo = body.lower()
        has_phrase = any(p in lo for p in phrases)
        overlap = len(title_toks & _tokens(body)) if title_toks else 0
        key = (1 if has_phrase else 0, overlap, -idx)
        if best is None or key > best[0]:
            best = (key, idx, body, has_phrase)
    key, idx, body, has_phrase = best
    overlap = key[1]
    if not has_phrase and overlap == 0:
        try:
            from cru_match import extract_snippet
            snip = extract_snippet(title, str(transcript_text or ""),
                                   length=QUOTE_MAX_CHARS)
        except Exception:
            snip = ""
        if snip:
            return {"quote": _clip_quote(snip.strip(".").strip()),
                    "turn_index": None, "has_completion": False}
        idx, body = turns[0]
    return {"quote": _clip_quote(body), "turn_index": idx,
            "has_completion": bool(has_phrase)}


def signal_value(*, has_completion=None, has_schedule_shift=None,
                 has_new_ask=None) -> str:
    """`data.signal` — the four words the fixed f-string used to carry, as a
    field (DD-3). Same precedence the f-string had."""
    if has_completion:
        return SIGNAL_VALUE_COMPLETION
    if has_schedule_shift:
        return SIGNAL_VALUE_SCHEDULE_SHIFT
    if has_new_ask:
        return SIGNAL_VALUE_NEW_ASK
    return SIGNAL_VALUE_TITLE_MATCH


# ---------------------------------------------------------------------------
# D4 / D15 — the proposal lifecycle, read purely over an event list
# ---------------------------------------------------------------------------

def _seq_of(ev) -> Optional[int]:
    v = (ev or {}).get("seq")
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _supersedes_seq(ev) -> Optional[int]:
    """Top-level or data-scope `supersedes_seq` (event_types SUPERSEQ vocabulary)."""
    for scope in (ev, (ev or {}).get("data") or {}):
        if not isinstance(scope, dict):
            continue
        v = scope.get("supersedes_seq")
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            return v
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
    return None


def _cid_of(ev) -> str:
    d = (ev or {}).get("data") or {}
    cid = (d.get("commitment_id") or d.get("target_id") or ev.get("commitment_id")
           or "")
    return str(cid) if cid else ""


_CLOSER_TYPES = ("commitment_resolved", "thread_resolved", "commitment_superseded")

# The producer strength stamp (watch_gate STAMP_*_FIELD). Spelled from the
# owning module when it is importable; the fallback is the same three words
# so a record built without watch_gate on the path still carries them.
try:
    from watch_gate import (STAMP_BLOCKED_FIELD as _STAMP_BLOCKED,
                            STAMP_REASON_FIELD as _STAMP_REASON,
                            STAMP_STRENGTH_FIELD as _STAMP_STRENGTH)
except Exception:  # pragma: no cover
    _STAMP_STRENGTH, _STAMP_REASON, _STAMP_BLOCKED = (
        "evidence_strength", "strength_reason", "auto_close_blocked")
_STAMP_FIELDS = (_STAMP_STRENGTH, _STAMP_REASON, _STAMP_BLOCKED)


def fold_proposals(events: Iterable[dict]) -> dict:
    """ONE pass -> the proposal ledger.

    Returns {
      "by_seq":   {seq: record}   every `commitment_review_proposed`
      "by_key":   {(cid, source_ref): [seq, ...]}  in append order
      "open":     {seq, ...}      proposals still standing
    }
    record = {seq, ts, commitment_id, source_ref, score, evidence,
              evidence_ts, has_completion_signal, source_skill,
              proposed_resolution, supersedes_seq, superseded_by,
              dismissed_by, retracted, target_closed, open}

    What closes a proposal (D4 readers): a later proposal carrying
    `supersedes_seq` = its seq; a `commitment_review_dismissed` naming it by
    `data.proposal_seq`; a LEGACY dismissal (no proposal_seq) for the same
    commitment written AFTER it — the pre-POLICY1 semantics, kept for the
    rows already on disk; any closer for the commitment written after it.
    Order-aware by append position, never by ts."""
    by_seq: dict = {}
    by_key: dict = {}
    order: list = []
    for ev in events or ():
        if not isinstance(ev, dict):
            continue
        et = ev.get("type") or ev.get("event") or ""
        d = ev.get("data") or {}
        if et == "commitment_review_proposed":
            seq = _seq_of(ev)
            cid = _cid_of(ev)
            if seq is None or not cid:
                continue
            src = str(d.get("source_ref") or "")
            rec = {
                "seq": seq, "ts": ev.get("ts") or "", "commitment_id": cid,
                "source_ref": src,
                "score": d.get("match_score", d.get("score")),
                "evidence": d.get("evidence") or "",
                # POLICY1-B (a) — the chip's own title, so the TTL apply leg
                # can ask whether the quote is THE completion turn for it.
                "title": d.get("title") or "",
                "evidence_ts": d.get("evidence_ts") or "",
                "has_completion_signal": d.get("has_completion_signal"),
                "source_skill": ev.get("source_skill") or "",
                "proposed_resolution": d.get("proposed_resolution") or "",
                "supersedes_seq": _supersedes_seq(ev),
                "superseded_by": None, "dismissed_by": None,
                "retracted": False, "target_closed": False, "open": True,
            }
            # REVIEW_MERGED_v5280 F-1 — the PRODUCER'S OWN strength stamp
            # rides the record under watch_gate's field names, so the TTL
            # leg's screen (`chip_ttl_action`) reads the same stamp a human
            # confirm click reads. GRANOLA1's non-attendee lane writes the
            # closing SHAPE (a score, completion, a pointer) and stamps it
            # `auto_close_blocked: true` — a record that dropped the stamp
            # would let the machine close what the lane said must not close.
            for k in _STAMP_FIELDS:
                if k in d:
                    rec[k] = d.get(k)
            if d.get("nonattendee_lane") is not None:
                rec["nonattendee_lane"] = bool(d.get("nonattendee_lane"))
            by_seq[seq] = rec
            by_key.setdefault((cid, src), []).append(seq)
            order.append(seq)
            prior = rec["supersedes_seq"]
            if prior is not None and prior in by_seq and prior != seq:
                by_seq[prior]["superseded_by"] = seq
                by_seq[prior]["open"] = False
        elif et == "commitment_review_dismissed":
            cid = _cid_of(ev)
            pseq = d.get("proposal_seq")
            reason = str(d.get("resolution_reason") or "").strip().lower()
            dseq = _seq_of(ev)
            if isinstance(pseq, int) and not isinstance(pseq, bool):
                rec = by_seq.get(pseq)
                if rec is not None:
                    rec["dismissed_by"] = dseq
                    rec["retracted"] = reason == RETRACT_REASON
                    rec["open"] = False
            elif cid:
                for seq in order:
                    rec = by_seq[seq]
                    if rec["commitment_id"] == cid and rec["open"]:
                        rec["dismissed_by"] = dseq
                        rec["retracted"] = reason == RETRACT_REASON
                        rec["open"] = False
        elif et in _CLOSER_TYPES:
            cid = _cid_of(ev) or str(d.get("thread_id") or d.get("id") or "")
            if not cid:
                continue
            for seq in order:
                rec = by_seq[seq]
                if rec["commitment_id"] == cid and rec["open"]:
                    rec["target_closed"] = True
                    rec["open"] = False
    return {"by_seq": by_seq, "by_key": by_key,
            "open": {s for s, r in by_seq.items() if r["open"]}}


def closed_proposal_seqs(events: Iterable[dict]) -> set:
    """Seqs of proposals that a supersession or a seq-addressed dismissal
    closed — the two D4/D15 closes legacy readers cannot see. Order-aware."""
    ledger = fold_proposals(events)
    return {s for s, r in ledger["by_seq"].items()
            if r["superseded_by"] is not None
            or (r["dismissed_by"] is not None)}


def proposal_state(ledger: dict, commitment_id, source_ref) -> Optional[dict]:
    """The NEWEST proposal for (item, source_ref), or None. `ledger` is
    `fold_proposals(...)`'s return."""
    seqs = ledger["by_key"].get((str(commitment_id), str(source_ref or "")))
    if not seqs:
        return None
    return ledger["by_seq"][seqs[-1]]


def newest_open_proposals(events: Iterable[dict]) -> dict:
    """PLATE1 P7's `data.proposal` shape, folded from the ledger:
    {commitment_id: {score, evidence, evidence_ts, nags, seq, source_ref,
                     has_completion_signal}}
    from the NEWEST open proposal per commitment; `nags` = how many OPEN
    proposals stack on that commitment (a superseded chain counts once)."""
    ledger = fold_proposals(events)
    out: dict = {}
    counts: dict = {}
    for seq in sorted(ledger["open"]):
        rec = ledger["by_seq"][seq]
        cid = rec["commitment_id"]
        counts[cid] = counts.get(cid, 0) + 1
        out[cid] = {
            "score": rec["score"],
            "evidence": rec["evidence"],
            "evidence_ts": rec["evidence_ts"] or rec["ts"],
            "nags": counts[cid],
            "seq": seq,
            "source_ref": rec["source_ref"],
            "has_completion_signal": rec["has_completion_signal"],
        }
    for cid, row in out.items():
        row["nags"] = counts[cid]
    return out


def _parse_ts(value):
    from datetime import datetime, timezone
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def chip_ttl_action(rec: dict, *, hi=None, promise_ts=None, now_iso=None) -> str:
    """M ruling 1 / ruling 5 — what a chip does to ITSELF at the TTL.

    `close`   the chip's own evidence meets the CLOSE BAR: it proposed a
              resolution (never a supersede), carries a completion signal at
              or above the match bar, AND passes THE SAME SCREEN A HUMAN
              CONFIRM CLICK PASSES (`watch_gate`): the producer's own stamp
              (`auto_close_blocked` / `evidence_strength: weak` — GRANOLA1's
              non-attendee lane writes the closing shape and stamps it
              blocked; REVIEW_MERGED_v5280 F-1), the evidence text
              (`weakness_reason`: no evidence, a title match, no completion),
              and the ordering check (`temporal_warning`: evidence that
              predates the promise). The default applies — the row closes as
              done, confirmed by the evidence the chip is carrying.
    `retract` everything else. There is nothing to apply, so the chip
              withdraws itself rather than waiting for an answer nobody owes.

    The machine must not be able to close what a click could not, so the
    screen is imported from `watch_gate` — never re-spelled — and a screen
    that cannot be loaded fails CLOSED (retract). `promise_ts` is the
    target's capture time when the caller has it; `now_iso` is the apply
    moment. Never a third outcome: a chip that outlives its window and does
    neither is the nag this ruling ends. Pure."""
    hi = MATCH_SCORE_AUTO_RESOLVE if hi is None else hi
    if str(rec.get("proposed_resolution") or "auto_resolve") != "auto_resolve":
        return ACTION_RETRACT
    if rec.get("has_completion_signal") is not True:
        return ACTION_RETRACT
    try:
        s = float(rec.get("score") or 0.0)
    except (TypeError, ValueError):
        return ACTION_RETRACT
    if s < hi:
        return ACTION_RETRACT
    # The click's screen, verbatim from its home. Fail CLOSED.
    try:
        from watch_gate import (WEAK, stamped_strength, temporal_warning,
                                weakness_reason)
    except Exception:  # pragma: no cover — no screen, no close
        return ACTION_RETRACT
    # The explicit flag on its own, FIRST: `stamped_strength` reports
    # `blocked` only beside a strength stamp, and a producer that wrote the
    # bare flag ("never auto-close this") meant it whether or not it also
    # graded the evidence. The non-attendee lane's own marker rides with it.
    if rec.get(_STAMP_BLOCKED) is True or rec.get("nonattendee_lane"):
        return ACTION_RETRACT
    stamp = stamped_strength(rec)
    if stamp and (stamp.get("blocked") or stamp.get("strength") == WEAK):
        return ACTION_RETRACT
    warn = temporal_warning(evidence_ts=rec.get("evidence_ts"),
                            promise_ts=promise_ts, apply_ts=now_iso)
    if weakness_reason(rec.get("evidence"),
                       completion_signal=rec.get("has_completion_signal"),
                       temporal=warn):
        return ACTION_RETRACT
    return ACTION_CLOSE


def ttl_candidates(events: Iterable[dict], *, now_iso,
                   ttl_days: Optional[int] = None, hi=None,
                   pending_ids=None, promise_ts_by_id=None) -> list:
    """Every OPEN chip older than `ttl_days` (default PROPOSAL_TTL_DAYS),
    each carrying `ttl_action` (`close` | `retract`) from `chip_ttl_action`
    and `target_pending`. Oldest first. Pure.

    BOTH target states are included. Under M ruling 1 an unconfirmed guess
    whose evidence meets the close bar closes as done, so a standing chip on
    a pending row resolves exactly as one on a confirmed row does — which is
    also how the pile of legacy proposals already on disk drains, with no
    separate one-shot. `pending_ids` decides which DOOR the caller writes
    through, never whether the chip resolves. `promise_ts_by_id`
    ({commitment_id: capture ts}) feeds the click's ordering check: evidence
    that predates the promise retracts."""
    days = PROPOSAL_TTL_DAYS if ttl_days is None else int(ttl_days)
    now = _parse_ts(now_iso)
    if now is None:
        return []
    from datetime import timedelta
    cutoff = now - timedelta(days=max(0, days))
    live_pending = {str(x) for x in (pending_ids or ())}
    ledger = fold_proposals(events)
    out = []
    for seq in sorted(ledger["open"]):
        rec = dict(ledger["by_seq"][seq])
        when = _parse_ts(rec["ts"])
        if when is None or when > cutoff:
            continue
        rec["ttl_action"] = chip_ttl_action(
            rec, hi=hi, now_iso=now_iso,
            promise_ts=(promise_ts_by_id or {}).get(rec["commitment_id"]))
        rec["target_pending"] = rec["commitment_id"] in live_pending
        out.append(rec)
    return out


def retract_candidates(events: Iterable[dict], *, now_iso, pending_ids=None,
                       ttl_days: Optional[int] = None) -> list:
    """The subset of `ttl_candidates` that RETRACTS itself — what the
    receipt's retract count is derived from."""
    return [r for r in ttl_candidates(events, now_iso=now_iso,
                                      ttl_days=ttl_days, pending_ids=pending_ids)
            if r["ttl_action"] == ACTION_RETRACT]


def standing_completion_evidence(events: Iterable[dict], commitment_ids,
                                 *, hi=None) -> set:
    """R2's honesty count — the ids among `commitment_ids` whose NEWEST open
    proposal carries `has_completion_signal: true` at score >= hi. The lapse
    stays `dropped` (ruling); the receipt names how many lapsed WITH
    completion evidence so the lane-B gate can be graded."""
    hi = MATCH_SCORE_AUTO_RESOLVE if hi is None else hi
    want = {str(c) for c in (commitment_ids or ())}
    if not want:
        return set()
    out = set()
    for cid, row in newest_open_proposals(events).items():
        if cid not in want:
            continue
        try:
            s = float(row.get("score") or 0.0)
        except (TypeError, ValueError):
            continue
        if row.get("has_completion_signal") is True and s >= hi:
            out.add(cid)
    return out


def group_batch_id(run_batch_id, group_key) -> str:
    """DD-5 — the GROUP batch id under a run: `<run>-<8 hex of the key>`.
    Deterministic for (run, key), so every row of one group inside one run
    lands on the same id whichever call wrote it. An empty key means "the
    run itself" and returns the run id unchanged — an ungrouped row is its
    own group and `undo <run>` still finds it by `parent_batch_id`."""
    import hashlib
    run = str(run_batch_id or "")
    key = str(group_key or "").strip()
    if not key or key == run:
        return run
    return f"{run}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]}"


def group_stamps(run_batch_id, group_key) -> dict:
    """The three undo keys a grouped automatic act carries (DD-5)."""
    gid = group_batch_id(run_batch_id, group_key)
    return {"brain_batch_id": gid,
            PARENT_BATCH_KEY: str(run_batch_id or ""),
            UNDO_GROUP_KEY: str(group_key or "").strip() or str(run_batch_id or "")}


def close_evidence(quote, title, *, hi=None) -> dict:
    """POLICY1-B (a) — is this quote the COMPLETION TURN for THIS item?

    The transcript-wide score and the transcript-wide completion flag are
    the bar (untouched, 2026-08-01). They are not the evidence: a 75-minute
    call contains "sent the" somewhere and most short titles' words
    somewhere else, and the v5.27.0 supervised test watched that close two
    promises the call never mentioned. The evidence a close carries must be
    one turn that (a) carries completion language and (b) scores at the bar
    against the title ON ITS OWN. Pure; the same scorer the matcher uses.

    Returns {"ok": bool, "quote_score": float, "quote_has_completion": bool,
             "reason": str}."""
    q = " ".join(str(quote or "").split())
    if not q:
        return {"ok": False, "quote_score": 0.0, "quote_has_completion": False,
                "reason": "no quote"}
    lo = q.lower()
    has = any(p in lo for p in _completion_phrases())
    try:
        from cru_match import score_match as _score
        sc = float(_score(q, title))
    except Exception:
        sc = 0.0
    bar = float(hi if hi is not None else MATCH_SCORE_AUTO_RESOLVE)
    ok = bool(has and sc >= bar)
    if ok:
        reason = "the turn says it was done and names the item"
    elif not has:
        reason = "the quoted turn carries no completion language"
    else:
        reason = "the quoted turn does not name the item"
    return {"ok": ok, "quote_score": round(sc, 3), "quote_has_completion": has,
            "reason": reason}


def _row_party_ids(row: dict) -> set:
    """The row's party ids. F-3: a `commitment` event's envelope carries
    `person_ids` at the TOP level (the gate stamps it there) as well as in
    `data`; both scopes are read, like the meeting side."""
    d = (row or {}).get("data") or {}
    out = set()
    for scope in ((row or {}), d):
        v = scope.get("person_ids") if isinstance(scope, dict) else None
        if isinstance(v, list):
            out.update(str(x) for x in v if x)  # F-3: both scopes
    v = d.get("counterparty_ids")
    if isinstance(v, list):
        out.update(str(x) for x in v if x)
    for key in ("counterparty_id", "owner_id"):
        v = d.get(key)
        if isinstance(v, str) and v.strip():
            out.add(v.strip())
    return out


def _meeting_party_ids(ev: dict) -> set:
    """The meeting's party ids. On the operator's book `person_ids` rides
    the TOP level of the `meeting` event (405 of 439) and `data.person_ids`
    on a few (10) — both scopes are read, plus `attendee_person_ids`."""
    out = set()
    for scope in (ev or {}, (ev or {}).get("data") or {}):
        if not isinstance(scope, dict):
            continue
        for key in ("person_ids", "attendee_person_ids"):
            v = scope.get(key)
            if isinstance(v, list):
                out.update(str(x) for x in v if x)
    return out


def _meeting_time(ev: dict):
    d = (ev or {}).get("data") or {}
    return _parse_ts(d.get("start_ts") or ev.get("ts"))


def calendar_matches(rows, meeting_events, *, user_id="", now_iso=None,
                     window_days: int = 45) -> list:
    """DD-7 / D7 — the calendar closer's predicate, pure.

    A row (an open confirmed `scheduling` commitment, or an observed-tier
    scheduling row — the caller decides which tier it hands in) MATCHES a
    `meeting` event when the meeting's time (`data.start_ts`, else `ts`)
    lies inside (capture ts, capture ts + window_days] and at least one
    NON-USER party id on the row appears in the meeting's `person_ids`.
    Never `data.status`. The earliest matching meeting wins.

    Returns [{commitment_id, title, meeting_seq, meeting_ts, meeting_ref,
    n_parties, primary_thread_id, row}] oldest-meeting first."""
    from datetime import timedelta
    from event_time import event_time
    out = []
    now = _parse_ts(now_iso) if now_iso else None
    meetings = []
    for ev in meeting_events or []:
        when = _meeting_time(ev)
        if when is None:
            continue
        if now is not None and when > now:
            continue
        meetings.append((when, ev))
    meetings.sort(key=lambda t: t[0])
    for row in rows or []:
        d = (row or {}).get("data") or {}
        cap = _parse_ts(event_time(row))
        if cap is None:
            continue
        parties = {p for p in _row_party_ids(row) if p and p != str(user_id or "")}
        if not parties:
            continue
        limit = cap + timedelta(days=max(1, int(window_days)))
        for when, ev in meetings:
            if not (cap < when <= limit):
                continue
            shared = parties & _meeting_party_ids(ev)
            if not shared:
                continue
            md = ev.get("data") or {}
            out.append({
                "commitment_id": _cid_of(row) or str(d.get("id") or ""),
                "title": d.get("title") or "",
                "meeting_seq": _seq_of(ev),
                "meeting_ts": when.isoformat(),
                "meeting_ref": md.get("source_ref") or "",
                "n_parties": len(_meeting_party_ids(ev)),
                "primary_thread_id": row.get("primary_thread_id") or "",
                "row": row,
            })
            break
    out.sort(key=lambda c: c["meeting_ts"])
    return out


def mint_fire_batch_id(now_iso=None) -> str:
    """`cru_<UTC>-<8hex>` — one batch per transcript fire (lane A). Same
    salt width as the sweep's `swb_` mint, for the same birthday reason."""
    import secrets
    from datetime import datetime, timezone
    dt = _parse_ts(now_iso) or datetime.now(timezone.utc)
    return f"{FIRE_BATCH_PREFIX}{dt.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"


__all__ = [
    "MATCH_SCORE_AUTO_RESOLVE", "MATCH_SCORE_PENDING_REVIEW",
    "PROPOSAL_TTL_DAYS", "MATCH_THRESHOLD_NAMES",
    "TARGET_CONFIRMED", "TARGET_PENDING", "TARGET_CLOSED", "TARGET_STATES",
    "ACTION_CLOSE", "ACTION_CONFIRM_CLOSE", "ACTION_PROPOSE", "ACTION_NONE",
    "ACTION_RETRACT", "ACTIONS", "LANE_B_ACTIONS",
    "CHIP_BAND_LOW", "CHIP_BAND_HIGH",
    "CONFIRMED_BY_TRANSCRIPT", "AUTO_CLOSE_REASON",
    "PRESET_ENGAGED", "PRESET_LIGHT", "PRESET_QUIET", "PRESETS",
    "DEFAULT_PRESET", "PRESET_SKILL_KEY", "PRESET_CONFIG_KEY",
    "TRANSCRIPT_CLOSES_CONFIG_KEY", "TRANSCRIPT_CLOSES_DEFAULT",
    "transcript_closes_enabled",
    "EVIDENCE_STRONG", "EVIDENCE_CORROBORATING", "EVIDENCE_WEAK",
    "EVIDENCE_CLASSES", "SIGNAL_KEYS", "SIGNAL_COMPLETION",
    "SIGNAL_SCHEDULE_SHIFT", "SIGNAL_NEW_ASK", "SIGNAL_SENT", "SIGNAL_DELIVERY",
    "SIGNAL_REPLY", "SIGNAL_UNAMBIGUOUS", "SIGNAL_NAMED_COUNTERPARTY",
    "BAR_COMPLETION", "BAR_SENT", "BAR_DELIVERY", "BAR_REPLY", "BAR_MODERATE_UNAMBIGUOUS",
    "LANE_OWED_BY_YOU", "LANE_OWED_TO_YOU",
    "FIXED_EVIDENCE_PREFIX", "SIGNAL_VALUES", "SIGNAL_VALUE_COMPLETION",
    "SIGNAL_VALUE_SCHEDULE_SHIFT", "SIGNAL_VALUE_NEW_ASK",
    "SIGNAL_VALUE_TITLE_MATCH", "MATCH_DOOR", "RETRACT_REASON",
    "QUOTE_MAX_CHARS", "FIRE_BATCH_PREFIX", "CLOSE_CHANGE_CLASS",
    "FixedEvidenceError", "PolicyInputError",
    "match_overrides", "thresholds", "effective_preset",
    "evidence_class", "DECIDE_TABLE", "decide",
    "is_fixed_evidence", "refuse_fixed_evidence", "transcript_turns_text",
    "completion_quote", "signal_value",
    "fold_proposals", "closed_proposal_seqs", "proposal_state",
    "newest_open_proposals", "chip_ttl_action", "ttl_candidates",
    "retract_candidates",
    "standing_completion_evidence", "mint_fire_batch_id",
    "PARENT_BATCH_KEY", "UNDO_GROUP_KEY", "group_batch_id", "group_stamps",
    "close_evidence", "REFUSAL_NO_TRANSCRIPT_TS", "REFUSAL_STALE_EVIDENCE",
    "REFUSAL_NO_COMPLETION_TURN", "calendar_matches",
]
