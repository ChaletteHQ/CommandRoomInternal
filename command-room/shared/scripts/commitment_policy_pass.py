#!/usr/bin/env python3
"""POLICY1-A — the I/O half of the resolution policy: the CRU pass through
`decide`, the TTL retract leg, and the mail rails' close gate.

`commitment_policy` is pure (DD-1). This module is where policy meets the
substrate — every write goes through the shipped writers (`close_commitment`,
`build_pending_review_event` + `event_gate.append_event`,
`build_commitment_review_dismissed_event`), never a hand-built event and
never an edit to an existing line. Every full-history read goes through
`events_io`.

  apply_transcript_results  Phase 4.6 of the past-meetings orchestrator, as a
                            FUNCTION (DD-2). The prose snippet used to carry
                            the whole ladder inline, with a fixed f-string as
                            evidence and no batch id; it now calls this.
  resolve_stale_chips       DD-4 leg 2 (D15 / M ruling 5): every chip older
                            than PROPOSAL_TTL_DAYS resolves ITSELF — it
                            applies its own evidence (a close, when that
                            evidence meets the close bar) or retracts (a
                            `commitment_review_dismissed`, reason
                            `policy_retracted`). It never waits, never
                            repeats, never nags.
  policy_gate_closes        D8: the sent / inbound rails run their auto-close
                            rows through `decide` before `close_commitments`;
                            a row policy will not close is demoted to the
                            rail's propose list, never closed.

Batches (POLICY1-B DD-5): the transcript fire is the RUN batch
(`cru_<UTC>-<8hex>`, the parent); every close inside it carries a GROUP batch
under that run — one group per meeting (`group_batch_id(run, meeting_ref)`),
stamped `brain_change_class: commitment_close` + `parent_batch_id` +
`undo_group` — so `brain_undo` lists the fire with one line per meeting,
`undo <group>` reopens one meeting's closes and `undo <run>` / `undo all`
reopens the fire.

The evidence bar for a close (POLICY1-B (a), ATTENDED_TEST_v5.27.0 B4.4):
nothing closes without (1) the transcript's own start time, handed to THIS
function, (2) the row having been captured BEFORE that start, and (3) a
verbatim completion TURN that names the item (`commitment_policy.
close_evidence`). Each refusal is counted by name in `close_refusals`. A
machine close is stamped `resolved_by=<the rail>`, never a person id.

THE SWITCH (CUT-A, M ruling R-A 2026-09-06): the whole transcript
closing-on-evidence pass ships OFF. `_closes_enabled(ws)` is the ONE reader
(fail-to-OFF), consulted as a single early guard on every path that could
turn transcript evidence into a close — `apply_transcript_results` (Phase
4.6, meeting-notes 5e-bis, follow-up-ritual, the re-run path), the chip TTL
apply leg (`resolve_stale_chips`) and the calendar closer. While OFF the
bar is still evaluated and every refusal still COUNTED (M watches
`NoCompletionTurn`), a row that WOULD have closed is counted as
`n_close_withheld` and written as a review proposal instead (the chip
shape, `commitment_review_proposed`) when it scores at or above the chip
band's floor — never a close. ON restores the shipped behaviour exactly.
`set_transcript_closes` is the ONE writer (a receipted, undoable batch).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import commitment_policy as policy  # noqa: E402

SOURCE_SKILL_TRANSCRIPT = "past-meetings"
RETRACT_SOURCE_SKILL = "commitment-backlog-sweep:review-expiry"
TTL_BATCH_PREFIX = "cht_"
# CUT-A — the switch's own batch prefix and change class (registered in
# `brain_undo.REVERSERS` beside `commitment_preset`, same reverser: the
# previous stored config comes back exactly, or the key is cleared).
SWITCH_BATCH_PREFIX = "qtc_"
SWITCH_CHANGE_CLASS = "commitment_transcript_closes"
# The verbs M flips it by, and the one-line acks each returns.
VERB_TRANSCRIPT_CLOSES_ON = "turn on closing on evidence"
VERB_TRANSCRIPT_CLOSES_OFF = "turn off closing on evidence"
ACK_TRANSCRIPT_CLOSES_ON = (
    "Done — promises a meeting shows were kept will now close on their own, "
    "each carrying the words that closed it; say `undo` to reverse a fire, "
    "or `turn off closing on evidence` to stop.")
ACK_TRANSCRIPT_CLOSES_OFF = (
    "Done — nothing closes on meeting evidence now; a promise a meeting "
    "shows was kept shows up under `needs your call` instead. Say `undo` "
    "to put this back.")
ACK_TRANSCRIPT_CLOSES_ALREADY_ON = "Closing on evidence is already on."
ACK_TRANSCRIPT_CLOSES_ALREADY_OFF = "Closing on evidence is already off."
# The prose closers whose direct `close_commitment` call the door now refuses
# on transcript evidence (belt-and-braces for meeting-notes 5e-bis). Widened
# 2026-09-06 with `team-intelligence` (REVIEW_CUTA F2); the writer's copy
# `commitment_state._TRANSCRIPT_PROSE_CLOSERS` is kept identical and pinned so.
TRANSCRIPT_PROSE_CLOSERS = frozenset({"meeting-notes", "follow-up-ritual", "team-intelligence"})


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _load_all(workspace_root) -> list:
    """Full history through the OWNER-TIER door (PGUARD1 D1 / the D4c
    firewall): `events_io.load_events_owner_scoped` is the allowlisted shard
    reader's owner form — shard-transparent, defensive, skipped-lines
    channel preserved, no mask (a closer legitimately projects the whole
    book). The raw `load_all` form is a new raw-read site to the structural
    guard, and this pass has no business being one."""
    from events_io import load_events_owner_scoped
    events, _skipped = load_events_owner_scoped(workspace_root)
    return events


def _pending_ids_from(events, events_path) -> set:
    """The LIVE pending_review id set — the loader's projection, the same
    one `close_commitment`'s guard reads since F1."""
    from cru_match import _commitment_id, _is_pending_review, load_open_commitments
    out = set()
    for row in load_open_commitments(events_path, events=events):
        if _is_pending_review(row):
            out.add(_commitment_id(row))
    return out


def _open_ids_from(events, events_path) -> set:
    from cru_match import _commitment_id, load_open_commitments
    return {_commitment_id(row) for row in load_open_commitments(events_path, events=events)}


def _effective_preset(workspace_root) -> str:
    """QUIET1 DD-1 — the preset the closers ASK under: the stored posture
    stepped down after fourteen silent days (`quiet.effective_preset`), so a
    seat nobody answers writes fewer chips without anyone touching the key.
    Falls back to the stored key alone if the quiet module is unavailable.
    The close column never reads this (no preset moves a close)."""
    try:
        import quiet
        return quiet.effective_preset(workspace_root)
    except Exception:  # pragma: no cover — the stored key is the floor
        return policy.effective_preset(workspace_root)


def _closes_enabled(workspace_root) -> bool:
    """CUT-A (R-A) — the ONE seam every closer asks: is closing on
    transcript evidence ON for this workspace? Modelled on
    `held_tier._load_config`: a read that fails is the DEFAULT, which is
    OFF. Never fails open."""
    try:
        return policy.transcript_closes_enabled(workspace_root) is True
    except Exception:  # noqa: BLE001 — an unreadable switch is OFF
        return False


def _would_close_refusal(*, cid, quote, evidence_at, cap_raw, title, hi) -> Optional[str]:
    """CUT-A — the three POLICY1-B fences (plus NoQuote) as a pure question
    for the OFF path: which refusal WOULD the close have met, or None when
    it would have closed. Same predicates as the ON path below, so the
    counts M watches (`NoCompletionTurn` above all) move identically with
    the switch off; nothing here writes."""
    from cru_match import _parse_ts
    if not quote:
        return "NoQuote"
    if evidence_at is None:
        return policy.REFUSAL_NO_TRANSCRIPT_TS
    cap = _parse_ts(cap_raw) if cap_raw else None
    if cap is None or cap > evidence_at:
        if cap is None:  # F-8, same words as the ON path: refused LOUDLY
            print(f"CRU skip {cid}: StaleEvidence — no readable capture ts "
                  f"({cap_raw!r}); ordering against the meeting cannot be judged",
                  file=sys.stderr)
        return policy.REFUSAL_STALE_EVIDENCE
    ce = policy.close_evidence(quote, title or "", hi=hi)
    if ce["ok"] is not True:
        return policy.REFUSAL_NO_COMPLETION_TURN
    return None


def _empty_counts() -> dict:
    return {
        "n_closed": 0, "n_confirm_closed": 0, "n_updated": 0, "n_proposed": 0,
        "n_rescored": 0,
        "n_silent_pending": 0, "n_silent_dup": 0, "n_silent_retracted": 0,
        "n_silent_closed": 0, "n_silent_weak": 0, "n_silent_preset": 0,
        "n_silent_out_of_band": 0,
        "n_demoted_by_matcher": 0, "n_suppressed": 0, "n_close_refused": 0,
        "n_quote_missing": 0, "close_refusals": {},
        # CUT-A — the switch's own numbers: is it on, and how many closes
        # it withheld (rows that met every fence and were written as a
        # proposal, or nothing, instead).
        "closes_enabled": False, "n_close_withheld": 0,
    }


def apply_transcript_results(
    workspace_root,
    results: list,
    *,
    meeting_ref: str,
    transcript_ts=None,
    already_proposed: set,
    review_budget: dict,
    batch_id: Optional[str] = None,
    preset: Optional[str] = None,
    now_iso=None,
    source_skill: str = SOURCE_SKILL_TRANSCRIPT,
) -> dict:
    """DD-2 — the CRU pass calls policy before it builds anything.

    `results` are `cru_match.match_transcript_to_commitments` rows for ONE
    transcript (they carry `evidence_quote`, `signal`, the three signal
    flags, `pending_review`). `meeting_ref` is that transcript's own pointer
    (`granola:<id>`) — the proposal ledger's second key and the closure's
    `source_ref`. `already_proposed` and `review_budget` are the per-FIRE
    fences the orchestrator threads across transcripts (unchanged: policy
    runs BEFORE them). `batch_id` is the fire's batch (minted here when the
    caller passes none — thread ONE across the fire so `undo` reverses the
    whole fire).

    Per row:
      close         -> close_commitment(evidence=<quote>,
                       source_ref=meeting_ref, resolved_by_match="match",
                       extra_data={brain_batch_id, brain_change_class,
                       match_score, signal}) — a CONFIRMED target at the bar.
      confirm_close -> the same call plus `confirmed_by="transcript"` and
                       `resolution_reason: auto_closed_transcript_evidence`
                       (M ruling 2026-09-03): an UNCONFIRMED guess whose
                       evidence meets the close bar closes as done, with the
                       quote on the row, receipted and undoable. No question
                       is written for it, ever.
      propose       -> the chip, and ONLY inside [0.65, 0.80]: ONE per (item,
                       meeting_ref); a standing chip at the same score is
                       silent; at a different score a new row is APPENDED
                       carrying `supersedes_seq`; a retracted or dismissed
                       (item, meeting_ref) is silent forever; a second source
                       for an item that already has an open chip is silent
                       (the per-commitment fence); then the per-fire cap. The
                       chip resolves itself at the TTL (`resolve_stale_chips`).
      none          -> nothing written.

    The matcher's STRUCTURAL fences still outrank policy: a parent with open
    sub-items (SUB1 D3) is never closed by this function — it becomes a chip
    when the score is in band and silence otherwise — and a multi-counterparty
    row keeps its per-person receipt lane untouched.
    A `commitment_updated` recommendation (schedule shift) is not a
    resolution and is written as before, with the quote as evidence.

    Returns the counts dict (every key written, zero or not).
    """
    from cru_match import (build_commitment_updated_event,
                           build_pending_review_event, cap_review_proposals)
    from commitment_state import (AmbiguousTargetError, CommitmentIdError,
                                  OpenSubitemsError, PendingReviewError,
                                  close_commitment)
    from event_gate import append_event

    counts = _empty_counts()
    closes_on = _closes_enabled(workspace_root)  # CUT-A: read ONCE per fire
    counts["closes_enabled"] = closes_on
    if not results:
        return counts
    events_path = _events_path(workspace_root)
    events = _load_all(workspace_root)
    pending_ids = _pending_ids_from(events, events_path)
    open_ids = _open_ids_from(events, events_path)
    ledger = policy.fold_proposals(events)
    preset = preset or _effective_preset(workspace_root)
    hi, pend = policy.thresholds(workspace_root)
    batch_id = batch_id or policy.mint_fire_batch_id(now_iso)
    meeting_ref = str(meeting_ref or "")
    # DD-5 — the fire is the run; THIS meeting's closes are one group under it.
    stamps = policy.group_stamps(batch_id, meeting_ref)
    # (a) — the ordering fence lives HERE too, not only in the matcher: the
    # two calls are separate lines of prose and a value the matcher was not
    # handed must still fence the close. `None` refuses every close (a close
    # needs the meeting's own time); junk fails safe the same way.
    from cru_match import (_normalize_fire_start, _parse_ts, _commitment_id,
                           load_open_commitments)
    from event_time import event_time
    evidence_at = (_normalize_fire_start(transcript_ts, "transcript_ts")
                   if transcript_ts else None)
    capture_ts_by_id = {_commitment_id(r): event_time(r)
                        for r in load_open_commitments(events_path, events=events)}

    def _refuse(name):
        counts["n_close_refused"] += 1
        counts["close_refusals"][name] = counts["close_refusals"].get(name, 0) + 1

    to_propose: list = []      # (result row, decision, supersedes_seq)
    to_append: list = []       # commitment_updated rows
    for r in results:
        rec = r.get("recommendation")
        cid = r.get("commitment_id")
        if not cid or rec in (None, "no_action", "partial_received"):
            continue
        quote = str(r.get("evidence_quote") or "").strip()
        signal = r.get("signal") or policy.signal_value(
            has_completion=r.get("has_completion_signal"),
            has_schedule_shift=r.get("has_schedule_shift_signal"),
            has_new_ask=r.get("has_new_ask_signal"))
        if rec == "commitment_updated":
            to_append.append(build_commitment_updated_event(
                commitment_id=cid,
                primary_thread_id=r.get("primary_thread_id") or "",
                source_skill=source_skill,
                change_summary="Schedule shifted in transcript",
                evidence=quote,
                next_seq=None,
                source_ref=meeting_ref or None,
            ))
            counts["n_updated"] += 1
            continue
        if cid not in open_ids:
            target_state = policy.TARGET_CLOSED
        elif cid in pending_ids:
            target_state = policy.TARGET_PENDING
        else:
            target_state = policy.TARGET_CONFIRMED
        # Three-state key read by IDENTITY: only an assessed-and-found True
        # is a completion signal; False and None (never assessed) both mean
        # "no signal to act on" for `decide`, and the chip below carries the
        # producer's own three-state value, never a coerced one.
        signals = {
            policy.SIGNAL_COMPLETION: r.get("has_completion_signal") is True,
            policy.SIGNAL_SCHEDULE_SHIFT: bool(r.get("has_schedule_shift_signal")),
            policy.SIGNAL_NEW_ASK: bool(r.get("has_new_ask_signal")),
            policy.SIGNAL_NAMED_COUNTERPARTY: bool(r.get("counterparty_id")
                                                  or r.get("counterparty_name")),
        }
        d = policy.decide(target_state=target_state, score=r.get("score"),
                          signals=signals,
                          evidence={"quote": quote, "source_ref": meeting_ref,
                                    "evidence_ts": transcript_ts},
                          preset=preset, hi=hi, pend=pend)
        action = d["action"]
        if action in (policy.ACTION_CLOSE, policy.ACTION_CONFIRM_CLOSE) \
                and not closes_on:  # CUT-A switch: closing on evidence is OFF
            # M ruling R-A (2026-09-06) — the pass does not act until M
            # re-enables it by word. The bar is still judged and every
            # refusal still counted (the numbers M watches do not go dark)
            # and a refused row writes nothing, as under ON; a row that
            # WOULD have closed is counted as withheld and becomes a review
            # proposal (the chip shape — the most a transcript completion
            # signal may write) when it scores at or above the chip band's
            # FLOOR (the ceiling exists only because above it the row
            # closes, and closing is what is off), and silence otherwise.
            # Never a close, on either target state.
            why = _would_close_refusal(
                cid=cid, quote=quote, evidence_at=evidence_at,
                cap_raw=capture_ts_by_id.get(cid), title=r.get("title"), hi=hi)
            if why is not None:
                # A refused close is refused on either setting: counted,
                # nothing written (exactly the ON path's `continue`).
                if why == "NoQuote":
                    counts["n_quote_missing"] += 1
                _refuse(why)
                continue
            counts["n_close_withheld"] += 1
            try:
                _sc = float(r.get("score") or 0.0)
            except (TypeError, ValueError):
                _sc = 0.0
            if _sc >= policy.CHIP_BAND_LOW:
                action = policy.ACTION_PROPOSE
            else:
                counts["n_silent_out_of_band"] += 1
                continue
        if action in (policy.ACTION_CLOSE, policy.ACTION_CONFIRM_CLOSE) \
                and r.get("parent_blocks"):
            # SUB1 D3 — a parent with OPEN sub-items is never auto-closed,
            # whatever the evidence says: closing it would close children
            # nobody looked at. Policy never out-ranks a structural fence.
            # It becomes a chip if the score is in the chip band, and silence
            # if it is not — never a question outside the band.
            counts["n_demoted_by_matcher"] += 1
            try:
                _sc = float(r.get("score") or 0.0)
            except (TypeError, ValueError):
                _sc = 0.0
            if policy.CHIP_BAND_LOW <= _sc <= policy.CHIP_BAND_HIGH:
                action = policy.ACTION_PROPOSE
            else:
                action = policy.ACTION_NONE
                counts["n_silent_out_of_band"] += 1
                continue
        if action == policy.ACTION_NONE:
            if target_state == policy.TARGET_CLOSED:
                counts["n_silent_closed"] += 1
            elif target_state == policy.TARGET_PENDING:
                counts["n_silent_pending"] += 1
            elif d["evidence_class"] == policy.EVIDENCE_WEAK:
                counts["n_silent_weak"] += 1
            elif "chip band" in (d.get("reason") or ""):
                counts["n_silent_out_of_band"] += 1
            else:
                counts["n_silent_preset"] += 1
            continue
        if action in (policy.ACTION_CLOSE, policy.ACTION_CONFIRM_CLOSE):
            machine_confirm = action == policy.ACTION_CONFIRM_CLOSE
            if not quote:
                counts["n_quote_missing"] += 1
                _refuse("NoQuote")
                continue
            # (a) fence 1 — no start time, no close. "Now" is never the
            # meeting's time and a guessed value fences nothing.
            if evidence_at is None:  # (a) fence 1: the transcript's own start time
                _refuse(policy.REFUSAL_NO_TRANSCRIPT_TS)
                continue
            # (a) fence 2 — a meeting cannot be evidence that a promise made
            # after it started was already kept (EVORDER layer 3, re-checked
            # at the writer's door). A capture time we cannot read refuses.
            _cap_raw = capture_ts_by_id.get(cid)
            _cap = _parse_ts(_cap_raw) if _cap_raw else None
            if evidence_at is not None and (_cap is None or _cap > evidence_at):  # (a) fence 2: captured after the meeting, or no readable capture time
                # F-8: a row whose capture time cannot be read is refused
                # LOUDLY, not skipped past — ordering cannot be judged.
                if _cap is None:
                    print(f"CRU skip {cid}: StaleEvidence — no readable capture ts "
                          f"({_cap_raw!r}); ordering against the meeting cannot be judged",
                          file=sys.stderr)
                _refuse(policy.REFUSAL_STALE_EVIDENCE)
                continue
            # (a) fence 3 — the quote must be THE completion turn for THIS
            # item: it says "done" and it names the item, on its own.
            ce = policy.close_evidence(quote, r.get("title") or "", hi=hi)
            if ce["ok"] is not True:  # (a) fence 3: the completion turn names the item
                _refuse(policy.REFUSAL_NO_COMPLETION_TURN)
                continue
            extra = {
                "brain_change_class": policy.CLOSE_CHANGE_CLASS,
                "match_score": round(float(r.get("score") or 0.0), 3),
                "quote_score": ce["quote_score"],
                "quote_has_completion": ce["quote_has_completion"],
                "signal": signal,
                "policy_bar": d.get("bar") or "",
            }
            extra.update(stamps)
            if machine_confirm:
                # M ruling 2026-09-03 — the guess lane. The row says out loud
                # that nobody was asked: `confirmed_by` names what confirmed
                # it, the reason names the automatic close, and the quote is
                # the whole of the evidence.
                extra["resolution_reason"] = policy.AUTO_CLOSE_REASON
            try:
                # (a) — a machine close is stamped with the RAIL, never a
                # person: the owner did not close it, the transcript did.
                res = close_commitment(
                    workspace_root, cid,
                    resolved_by=source_skill,
                    evidence=quote,
                    source_skill=source_skill,
                    source_ref=meeting_ref or None,
                    resolved_by_match=policy.MATCH_DOOR,
                    confirmed_by=(policy.CONFIRMED_BY_TRANSCRIPT
                                  if machine_confirm else None),
                    extra_data=extra,
                )
                if res.get("status") == "closed":
                    counts["n_confirm_closed" if machine_confirm else "n_closed"] += 1
            except (CommitmentIdError, PendingReviewError, OpenSubitemsError,
                    AmbiguousTargetError, ValueError) as exc:
                # NAME THE REFUSAL (the reconcile-sent pattern): every one of
                # these is a guard working, and a close that never landed
                # must not be counted as one that did.
                name = type(exc).__name__
                _refuse(name)
                print(f"CRU skip {cid}: {name}", file=sys.stderr)
            continue
        # action == propose
        prior = policy.proposal_state(ledger, cid, meeting_ref)
        supersedes = None
        if prior is not None:
            if prior.get("retracted") or prior.get("dismissed_by") is not None:
                counts["n_silent_retracted"] += 1
                continue
            if prior.get("open"):
                try:
                    same = abs(float(prior.get("score") or 0.0)
                               - round(float(r.get("score") or 0.0), 3)) < 1e-9
                except (TypeError, ValueError):
                    same = False
                if same:
                    counts["n_silent_dup"] += 1
                    continue
                supersedes = int(prior["seq"])
            # a closed-target / superseded prior with no open row: a fresh
            # proposal is legal (the target reopened, say) — falls through
        if supersedes is None and cid in already_proposed:
            counts["n_silent_dup"] += 1
            continue
        to_propose.append((r, d, supersedes, signal, quote))

    # The per-fire cap is a VOLUME bound, not a threshold (TITLEMINT1) — it
    # runs after policy and only over what policy chose to ask. A re-score
    # replaces an open question and does not spend the budget.
    fresh = [t for t in to_propose if t[2] is None]
    rescores = [t for t in to_propose if t[2] is not None]
    kept_rows = cap_review_proposals([t[0] for t in fresh], budget=review_budget)
    kept_ids = {row["commitment_id"] for row in kept_rows}
    counts["n_suppressed"] = len(fresh) - len(kept_rows)
    for r, d, supersedes, signal, quote in rescores + [t for t in fresh if t[0]["commitment_id"] in kept_ids]:
        cid = r["commitment_id"]
        to_append.append(build_pending_review_event(
            commitment_id=cid,
            primary_thread_id=r.get("primary_thread_id") or "",
            source_skill=source_skill,
            proposed_resolution=d.get("proposed_resolution") or "auto_resolve",
            score=r.get("score") or 0.0,
            evidence=quote,
            next_seq=None,
            title=r.get("title") or "",
            has_completion_signal=r.get("has_completion_signal"),
            evidence_ts=str(transcript_ts) if transcript_ts else None,
            source_ref=meeting_ref or None,
            supersedes_seq=supersedes,
            signal=signal,
        ))
        if supersedes is not None:
            counts["n_rescored"] += 1
        else:
            counts["n_proposed"] += 1
            already_proposed.add(cid)
    if to_append:
        append_event(events_path, to_append, holder=source_skill)
    counts["batch_id"] = batch_id
    counts["preset"] = preset
    return counts


def resolve_stale_chips(workspace_root, *, now_iso=None, ttl_days=None,
                        batch_id: Optional[str] = None, apply: bool = True,
                        source_skill: str = RETRACT_SOURCE_SKILL) -> dict:
    """DD-4 leg 2 (D15 / M rulings 1 and 5) — every chip past
    `PROPOSAL_TTL_DAYS` resolves ITSELF. Two outcomes, never a third:

      APPLY    the chip's own evidence meets the close bar (it proposed a
               resolution, carries a completion signal, scored at or above
               the bar) — the row CLOSES as done through `close_commitment`
               with the chip's quote as evidence, its source as the pointer,
               the `match` door and its score. On an unconfirmed target the
               close goes through the `confirmed_by="transcript"` door and
               carries `resolution_reason: auto_closed_transcript_evidence`,
               exactly as the live pass does.
      RETRACT  everything else — a bare title match, a schedule shift, a new
               ask, a sub-bar score. A `commitment_review_dismissed` naming
               the proposal (`proposal_seq`), reason `policy_retracted`. A
               dismissal is not a closure (CLOSEID2), and a retracted
               (item, source_ref) is never proposed again.

    Both outcomes carry the run's `brain_batch_id`; the closes also carry
    `brain_change_class: commitment_close`, so one `undo` reverses them.
    A close the writer refuses (a parent with open sub-items, a target that
    closed since) RETRACTS instead — nothing is left waiting.

    `apply=False` plans only. Returns {n_ttl_planned, n_applied, n_retracted,
    n_apply_refused, batch_id, applied: [...], retracted: [...]}."""
    from cru_match import build_commitment_review_dismissed_event
    from commitment_state import (AmbiguousTargetError, CommitmentIdError,
                                  OpenSubitemsError, PendingReviewError,
                                  close_commitment)
    from event_gate import append_event
    from datetime import datetime, timezone

    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    events_path = _events_path(workspace_root)
    events = _load_all(workspace_root)
    pending_ids = _pending_ids_from(events, events_path)
    open_ids = _open_ids_from(events, events_path)
    hi, _pend = policy.thresholds(workspace_root)
    # The click's ordering check needs each target's capture time (the
    # promise's own ts): evidence that predates the promise cannot show it
    # was kept.
    from cru_match import _commitment_id, load_open_commitments
    from event_time import event_time
    promise_ts_by_id = {_commitment_id(r): event_time(r)
                        for r in load_open_commitments(events_path, events=events)}
    cands = [c for c in policy.ttl_candidates(
                 events, now_iso=now_iso, ttl_days=ttl_days, hi=hi,
                 pending_ids=pending_ids, promise_ts_by_id=promise_ts_by_id)
             if c["commitment_id"] in open_ids]
    out = {"n_ttl_planned": len(cands),
           "n_apply_planned": sum(1 for c in cands
                                  if c["ttl_action"] == policy.ACTION_CLOSE),
           "n_retract_planned": sum(1 for c in cands
                                    if c["ttl_action"] == policy.ACTION_RETRACT),
           "n_applied": 0, "n_retracted": 0, "n_apply_refused": 0,
           "batch_id": batch_id, "applied": [], "retracted": [],
           "apply_refusals": {}}
    if not apply or not cands:
        return out
    batch_id = batch_id or (TTL_BATCH_PREFIX
                            + policy.mint_fire_batch_id(now_iso).split("_", 1)[1])
    out["batch_id"] = batch_id
    out["n_apply_no_completion_turn"] = 0
    closes_on = _closes_enabled(workspace_root)  # CUT-A: read ONCE per fire
    out["closes_enabled"] = closes_on
    out["n_apply_withheld"] = 0
    to_retract: list = []
    for c in cands:
        cid = c["commitment_id"]
        quote = str(c.get("evidence") or "").strip()
        src = str(c.get("source_ref") or "").strip()
        if c["ttl_action"] == policy.ACTION_CLOSE and quote and src \
                and not closes_on:  # CUT-A switch: a chip never becomes a close while OFF
            # R-A — the chip's evidence is still graded so the count M
            # watches moves; then it RETRACTS (the ruled fate of a chip
            # nothing acted on), never closes. A chip that would have
            # closed is counted as withheld.
            ce = policy.close_evidence(quote, c.get("title") or "", hi=hi)
            if ce["ok"] is not True:
                out["n_apply_no_completion_turn"] += 1
            else:
                out["n_apply_withheld"] += 1
            to_retract.append(c)
            continue
        if c["ttl_action"] == policy.ACTION_CLOSE and quote and src:
            # (a) — the chip's own evidence must be THE completion turn for
            # this item (says done, names it) or the chip retracts instead.
            ce = policy.close_evidence(quote, c.get("title") or "", hi=hi)
            if ce["ok"] is not True:  # (a) fence 3 on the chip leg
                out["n_apply_no_completion_turn"] += 1
                to_retract.append(c)
                continue
            machine_confirm = bool(c.get("target_pending"))
            extra = {
                "brain_change_class": policy.CLOSE_CHANGE_CLASS,
                "match_score": round(float(c.get("score") or 0.0), 3),
                "quote_score": ce["quote_score"],
                "quote_has_completion": ce["quote_has_completion"],
                "policy_bar": policy.BAR_COMPLETION,
                "applied_proposal_seq": int(c["seq"]),
            }
            # DD-5 — one group per source (the meeting the chip came from)
            # under the TTL run.
            extra.update(policy.group_stamps(batch_id, src))
            if machine_confirm:
                extra["resolution_reason"] = policy.AUTO_CLOSE_REASON
            try:
                res = close_commitment(
                    workspace_root, cid,
                    resolved_by=source_skill, evidence=quote,
                    source_skill=source_skill,
                    source_ref=src,
                    resolved_by_match=policy.MATCH_DOOR,
                    confirmed_by=(policy.CONFIRMED_BY_TRANSCRIPT
                                  if machine_confirm else None),
                    extra_data=extra,
                )
                if res.get("status") == "closed":
                    out["n_applied"] += 1
                    out["applied"].append({"commitment_id": cid,
                                           "proposal_seq": int(c["seq"]),
                                           "source_ref": src})
                    continue
                # already_resolved — the chip has nothing left to do; it is
                # closed by the fold, so nothing is written for it at all.
                continue
            except (CommitmentIdError, PendingReviewError, OpenSubitemsError,
                    AmbiguousTargetError, ValueError) as exc:
                name = type(exc).__name__
                out["n_apply_refused"] += 1
                out["apply_refusals"][name] = out["apply_refusals"].get(name, 0) + 1
                print(f"chip TTL apply refused {cid}: {name}", file=sys.stderr)
                # A refusal must not leave the chip standing: it retracts.
        to_retract.append(c)
    rows = []
    for c in to_retract:
        rows.append(build_commitment_review_dismissed_event(
            commitment_id=c["commitment_id"],
            primary_thread_id="",
            source_skill=source_skill,
            next_seq=None,
            proposal_seq=int(c["seq"]),
            resolution_reason=policy.RETRACT_REASON,
            brain_batch_id=batch_id,
        ))
        out["retracted"].append({"commitment_id": c["commitment_id"],
                                 "proposal_seq": int(c["seq"]),
                                 "source_ref": c.get("source_ref") or ""})
    if rows:
        append_event(events_path, rows, holder=source_skill)
    out["n_retracted"] = len(rows)
    return out


# The pre-ruling name, kept so nothing that already imported it breaks; the
# behaviour is the ruled one (apply or retract, never wait).
retract_stale_proposals = resolve_stale_chips


def set_transcript_closes(workspace_root, enabled: bool, *, origin: str = "m_action",
                          triggered_by: str = "", now_iso=None) -> dict:
    """CUT-A — THE writer for the switch (`turn on closing on evidence` /
    `turn off closing on evidence`). Modelled on `quiet.stamp_preset`: the
    key is written through the typed store as ONE `brain_batch` (`qtc_…`,
    class `commitment_transcript_closes`) carrying the previous config, so
    a bare `undo` lists it and the registered reverser puts the previous
    config back exactly (or clears the key when there was none). Every
    OTHER key in the store (the preset) is carried over untouched.
    Idempotent: the same value already stored is a no-op with no receipt.

    Returns {ran, enabled, prev_enabled, batch_id, line} — `line` is the
    one-line ack the surface says verbatim."""
    from skill_config_writer import load_skill_config, save_skill_config
    if enabled is not True and enabled is not False:
        raise ValueError("set_transcript_closes: enabled must be a bool")
    stored = load_skill_config(workspace_root, policy.PRESET_SKILL_KEY) or {}
    prev_cfg = stored.get("config") if isinstance(stored, dict) else None
    prev_cfg = prev_cfg if isinstance(prev_cfg, dict) else None
    prev = _closes_enabled(workspace_root)
    if prev is enabled:
        return {"ran": False, "enabled": enabled, "prev_enabled": prev, "batch_id": None,
                "line": (ACK_TRANSCRIPT_CLOSES_ALREADY_ON if enabled
                         else ACK_TRANSCRIPT_CLOSES_ALREADY_OFF)}
    from datetime import datetime, timezone
    import secrets
    from cru_match import _parse_ts
    dt = (_parse_ts(now_iso) if now_iso else None) or datetime.now(timezone.utc)
    batch_id = f"{SWITCH_BATCH_PREFIX}{dt.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"
    cfg = dict(prev_cfg or {})
    cfg[policy.TRANSCRIPT_CLOSES_CONFIG_KEY] = enabled
    save_skill_config(
        workspace_root, policy.PRESET_SKILL_KEY, cfg,
        is_reconfigure=bool(prev_cfg), origin=origin,
        event_extra={"brain_batch_id": batch_id,
                     "brain_change_class": SWITCH_CHANGE_CLASS,
                     "skill_name": policy.PRESET_SKILL_KEY,
                     "prev_config_present": prev_cfg is not None,
                     "prev_config": prev_cfg,
                     "triggered_by": triggered_by},
        event_ts=(dt.isoformat() if now_iso else None))
    return {"ran": True, "enabled": enabled, "prev_enabled": prev, "batch_id": batch_id,
            "line": ACK_TRANSCRIPT_CLOSES_ON if enabled else ACK_TRANSCRIPT_CLOSES_OFF}


def apply_meeting_closes(workspace_root, *, meeting_ref: str, transcript_ts,
                         transcript_text: str, attendee_person_ids,
                         source_skill: str = "meeting-notes",
                         exclude_captured_since=None, now_iso=None,
                         batch_id: Optional[str] = None) -> dict:
    """CUT-A — the ONE python entry meeting-notes Step 5e-bis (and
    follow-up-ritual's mirror, and team-intelligence's pointer to it) calls
    for "did this meeting close anything already on the book". The model
    NEVER calls `close_commitment` from those steps: this runs the shipped
    matcher and `apply_transcript_results` — the same three refusals Phase
    4.6 has, the same switch. With the switch OFF the only write is a
    review proposal in the medium band; with it ON the tightened pass
    closes, stamped with the rail, receipted, undoable.

    `transcript_ts` is the MEETING's own start (offset-carrying or UTC) —
    never the processing clock; without it every close is refused by name
    (`NoTranscriptTs`) and only proposals write. `meeting_ref` is the
    transcript's own pointer (`granola:<id>`) and is also the self-evidence
    fence. Returns the counts dict plus `n_results`."""
    from cru_match import (load_open_commitments, match_transcript_to_commitments,
                           open_review_proposal_ids)
    events_path = _events_path(workspace_root)
    diag: dict = {}
    results = match_transcript_to_commitments(
        open_commitments=load_open_commitments(events_path),
        attendee_person_ids=list(attendee_person_ids or []),
        transcript_text=transcript_text or "",
        transcript_source_ref=meeting_ref,
        exclude_captured_since=exclude_captured_since,
        transcript_ts=transcript_ts, diagnostics=diag,
        workspace_root=workspace_root)
    counts = apply_transcript_results(
        workspace_root, results, meeting_ref=meeting_ref,
        transcript_ts=transcript_ts,
        already_proposed=open_review_proposal_ids(events_path),
        review_budget={}, batch_id=batch_id, now_iso=now_iso,
        source_skill=source_skill)
    counts["n_results"] = len(results)
    counts["n_stale_evidence_skipped"] = int(diag.get("stale_evidence_dropped", 0) or 0)
    return counts


_RAIL_SIGNALS = {
    # D8 — each rail's own findings, read as the named rows of the table.
    "sent": lambda r: {
        policy.SIGNAL_SENT: (r.get("close_basis") or "") == "",
        policy.SIGNAL_DELIVERY: (r.get("close_basis") or "") == "delivery_evidence",
        policy.SIGNAL_UNAMBIGUOUS: bool(r.get("moderate")),
    },
    "inbound": lambda r: {
        policy.SIGNAL_REPLY: (r.get("close_basis") or "") == "reply_evidence",
        policy.SIGNAL_COMPLETION: r.get("has_completion_signal") is True
        and (r.get("close_basis") or "") == "",
    },
}


def policy_gate_closes(rows: list, *, rail: str, pending_ids, workspace_root=None,
                       preset: Optional[str] = None) -> tuple:
    """D8 — the mail rails' auto-close rows pass through `decide` at the same
    table the transcript closer uses. Returns (kept, demoted): `kept` closes
    through `close_commitments` as before; `demoted` rows join the rail's
    propose list carrying `policy_reason`.

    The rows' own grades are the signals: the sent rail's title match at the
    bar (`own_send_at_bar`), SENTMATCH delivery evidence, FS-11's unambiguous
    moderate promotion (`moderate: True`); the inbound rail's thread reply
    (REPLYCLOSE R1) and completion at the bar. A row whose target is
    `pending_review` (the LIVE projection) is demoted here, before the
    writer refuses it — the same fact, one door earlier."""
    if rail not in _RAIL_SIGNALS:
        raise ValueError(f"policy_gate_closes: unknown rail {rail!r}")
    hi, pend = policy.thresholds(workspace_root)
    preset = preset or _effective_preset(workspace_root)
    live_pending = {str(x) for x in (pending_ids or ())}
    kept, demoted = [], []
    for r in rows or []:
        cid = str(r.get("commitment_id") or "")
        target = policy.TARGET_PENDING if cid in live_pending else policy.TARGET_CONFIRMED
        d = policy.decide(target_state=target, score=r.get("score"),
                          signals=_RAIL_SIGNALS[rail](r), preset=preset, hi=hi, pend=pend)
        if d["action"] == policy.ACTION_CLOSE:
            kept.append(r)
        else:
            row = dict(r)
            if d["action"] == policy.ACTION_CONFIRM_CLOSE:
                # REVIEW_MERGED_v5280 F-7 — an EXPLICIT rail policy, not an
                # accident of `confirm_close != close`: the mail rails never
                # close a guess (that is the transcript rail's lane, and its
                # evidence is a quote from a meeting, not a message). The
                # row is demoted and the reason says what happened — never
                # that it closed.
                row["policy_reason"] = (
                    f"unconfirmed guess — the {rail} rail never closes a "
                    "guess; it stays a proposal for the customer")
            else:
                row["policy_reason"] = d["reason"]
            row["recommendation"] = "pending_review"
            # The receipt names the refusal by the class the WRITER would
            # have raised: a pending target is the pending-review floor met
            # one door earlier (the rails' receipt suites read that name);
            # anything else is the policy table itself.
            row["policy_refusal"] = ("PendingReviewError"
                                     if target == policy.TARGET_PENDING
                                     else "PolicyGate")
            demoted.append(row)
    return kept, demoted


__all__ = [
    "SOURCE_SKILL_TRANSCRIPT", "RETRACT_SOURCE_SKILL", "TTL_BATCH_PREFIX",
    "SWITCH_BATCH_PREFIX", "SWITCH_CHANGE_CLASS",
    "VERB_TRANSCRIPT_CLOSES_ON", "VERB_TRANSCRIPT_CLOSES_OFF",
    "ACK_TRANSCRIPT_CLOSES_ON", "ACK_TRANSCRIPT_CLOSES_OFF",
    "TRANSCRIPT_PROSE_CLOSERS",
    "apply_transcript_results", "resolve_stale_chips",
    "retract_stale_proposals", "policy_gate_closes",
    "set_transcript_closes", "apply_meeting_closes",
]
