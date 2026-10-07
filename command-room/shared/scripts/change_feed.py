#!/usr/bin/env python3
"""Living Brain change feed — the READER that narrates what the brain did
(SPEC LB1, D6).

The audit substrate already records everything the system does
(`sent_reconcile`, `session_sweep_run`, `cleanup_run`, `maintenance_run`,
the brain_proposal tombstones); what was missing is the user-facing reader.
"Since yesterday I closed 3 commitments from your sent mail and have 2
things to confirm" is the intuitiveness win — and the narration that makes
auto-apply safe.

DOCTRINE
  - **Narration is never the enforcement artifact.** Enforcement binds to
    the audit events themselves (the reconcile-sent
    `validate_reconcile_ran` doctrine); this module only READS. Every line
    carries `refs` — the audit event seq(s) it aggregates — so any claim is
    traceable to substrate.
  - **Drop-empty.** A category with nothing to say emits no line; a fully
    quiet window returns an empty list (surfaces render their own honest
    steady-state form).
  - Consumers: morning-briefing CHANGED line (1–3 lines), coach Phase 2A′
    (≤3 lines), weekly-recap Phase 4 roll-up, system-health / Staff Meeting
    ("what I did on my own").

Read-only. stdlib only. Never raises into a caller on malformed events.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _now_iso() -> str:
    # Full precision, not second-truncated: the event gate stamps
    # microsecond timestamps, and a second-truncated "now" upper bound
    # would exclude an event written in the same second the reader runs.
    return datetime.now(timezone.utc).isoformat()


def _load_events(workspace_root) -> list[dict]:
    import event_refs

    path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not path.exists():
        return []
    return event_refs.load_events(path)


def _machine_resolved(ev: dict) -> bool:
    """REVIEW v5.28.0 F-3 — True when a brain_proposal_resolved row was
    written by the system rather than a person: `resolved_by` is the literal
    "system" (deal_signal_retire) or equals the event's own `source_skill`
    (every auto rail passes `resolved_by=source_skill`). A person id, or an
    absent `resolved_by` on a legacy confirm-card row, is a human."""
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    by = data.get("resolved_by")
    if not isinstance(by, str) or not by.strip():
        return False
    by = by.strip()
    if by.lower() == "system":
        return True
    skill = ev.get("source_skill")
    return isinstance(skill, str) and by == skill.strip()


def _plural(n: int, singular: str, plural: Optional[str] = None) -> str:
    return singular if n == 1 else (plural or singular + "s")


#: MF-11c-1 — `fired_via` spellings that mean "nobody typed anything".
#: End of Day keeps the same tuple (`SCHEDULED_FIRED_VIA`); the suite
#: pins the two equal so they cannot drift apart.
SCHEDULED_FIRED_VIA = ("scheduled", "cron", "schedule")


def _job_batches(events) -> set:
    """MF-11c-1 — the batch ids a JOB ran, off the job's own receipts.

    A `pack_run` that names a batch (`batch_id` / `brain_batch_id`) and
    either fired on a schedule or was written under a machine source is
    a job's. Read from the receipt so that a job writing its rows under
    a customer verb's stamps (the retired age-out job did exactly that
    on 2026-09-13) is still narrated as the job it was. Pure, one pass.
    """
    try:
        from event_types import is_machine_source as _machine_source
    except Exception:  # pragma: no cover
        _machine_source = lambda s: False
    out: set = set()
    for ev in events or ():
        if not isinstance(ev, dict) or ev.get("type") != "pack_run":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        fired = str(d.get("fired_via") or "").strip().lower()
        if not (fired in SCHEDULED_FIRED_VIA
                or _machine_source(ev.get("source_skill"))):
            continue
        for key in ("batch_id", "brain_batch_id"):
            val = str(d.get(key) or "").strip()
            if val:
                out.add(val)
    return out


def _transcript_closes_on(workspace_root) -> bool:
    """CUT-A — the switch, through its one reader; unreadable is OFF."""
    try:
        from commitment_policy_pass import _closes_enabled
        return _closes_enabled(workspace_root) is True
    except Exception:  # pragma: no cover
        return False


def _unrest_change_class() -> str:
    """EXIT1 (review R-5) — the un-rest's change class, read from the rail
    that writes it rather than restated here."""
    try:
        from exit_doors import UNREST_CHANGE_CLASS
        return UNREST_CHANGE_CLASS
    except Exception:  # pragma: no cover
        return "counter_evidence_unrest"


def _counter_evidence_change_class() -> str:
    """EXIT1 (review R-4) — the put-back's change class, read from the rail
    that writes it rather than restated here, so the two can never drift."""
    try:
        from exit_doors import COUNTER_EVIDENCE_CHANGE_CLASS
        return COUNTER_EVIDENCE_CHANGE_CLASS
    except Exception:  # pragma: no cover
        return "counter_evidence_reopen"


def _learning_classes() -> frozenset:
    """SPEC_LEARN1 — the learning job's change classes, read from the rail
    that writes them rather than restated here, so the two cannot drift."""
    try:
        from learning_pass import AUTO_CLASSES
        return frozenset(AUTO_CLASSES)
    except Exception:  # pragma: no cover
        return frozenset({"voice_block_update", "prep_section_weight",
                          "exemplar_promotion"})


_LEARNING_CLASSES = _learning_classes()


def changes_since(
    workspace_root,
    since_ts: str,
    *,
    now_iso: Optional[str] = None,
    max_lines: Optional[int] = None,
) -> dict:
    """Aggregate what the brain did between `since_ts` (exclusive) and now
    into ranked plain-English lines. Returns:
        {"lines": [{"text", "category", "refs": [seq, ...]}, ...],
         "counts": {...}, "since_ts": ..., "now": ...}
    Ranking: user-visible substance first (commitment closes, sweep
    recoveries), then proposal resolutions/undos, then quiet housekeeping
    (expiries, maintenance meta). Empty window → lines == []."""
    from event_time import event_dt, parse_ts

    now_iso = now_iso or _now_iso()
    since_dt = parse_ts(since_ts)
    now_dt = parse_ts(now_iso)

    def _in_window(ev) -> bool:
        dt = event_dt(ev)
        if dt is None:
            return False
        if since_dt is not None and dt <= since_dt:
            return False
        if now_dt is not None and dt > now_dt:
            return False
        return True

    counts = {
        "closed_from_sent": 0, "opened_from_sent": 0, "swept": 0,
        "people_added": 0, "people_linked": 0, "people_auto_linked": 0,
        "orgs_promoted": 0,
        "facts_noted": 0,
        "cleanup_runs": 0, "maintenance_jobs": 0,
        "proposals_resolved": 0, "proposals_declined": 0,
        "proposals_expired": 0, "unconfirmed_expired": 0,
        # NUMBER1 3.6 (R-22) — the same split MF-11c-1 made for the
        # aged-out close: a review lapse a JOB ran is the door's act;
        # the identical event written by a customer typing `review
        # amnesty` is the customer's. One reason, two actors, two
        # sentences — and only the door's is "decided for you".
        "cleared_review_amnesty": 0,
        # MF-11c-1 (night 11c trial merge, BRIEF2 seam) — the aged-out
        # close. Two lines because two different hands write the same
        # stamp: a JOB's pass (the retired age-out job wrote 48 rows on
        # 2026-09-13 under the amnesty leg's own stamps, signed as the
        # reader) and the customer's own hand-typed `commitment amnesty`.
        # Told apart by the RECEIPT, never by the actor stamp: a `pack_run`
        # that names the batch and fired on a schedule is a job's.
        "let_go_backlog": 0, "cleared_amnesty": 0,
        "changes_undone": 0,
        # ATTRIB2 — the machine's own reversals, counted for the record and
        # rendered on NO customer surface (see the fold below).
        "changes_reversed_by_machine": 0,
        "new_proposals": 0,
        # POLICY1-A D14 — the transcript closer's own line and the retract
        # line, counted from the WRITTEN events (receipt-honesty rule).
        "closed_from_meetings": 0, "closed_unconfirmed": 0,
        # POLICY1-B DD-7 / DD-9 (fix F-2) — the two automatic acts lane B
        # added, each narrated ONCE from the written rows (never a plan):
        # calendar closes (`data.calendar_close`) and quiet-lane parks
        # (`brain_change_class: commitment_park`, still open).
        "closed_from_calendar": 0, "closed_from_calendar_unconfirmed": 0,
        "parked_quiet": 0,
        # EXIT1 (SPEC_FLOW1 Lane B) — the three exit rails, each narrated
        # ONCE from the written rows. They are SEPARATE from the transcript
        # closer's line above and must stay separate: that line says "your
        # meetings said these were done", which is exactly what these did
        # NOT do. `closed_from_your_word` is what the customer said in their
        # own turn; `closed_from_deal` is a signed agreement or a paid
        # invoice; `rested_quiet` and `let_go_quiet` are the silence rail's
        # two ages.
        "closed_from_your_word": 0, "closed_from_deal": 0,
        "rested_quiet": 0, "let_go_quiet": 0,
        # EXIT1 FIX ROUND 2 (review R-4) — the put-back. Their reply on a row
        # one of those rails had closed puts it back, and an act with a
        # receipt nobody reads is an act nobody sees. (review R-5) — and the
        # un-rest: their reply on a row the silence rail had RESTED takes it
        # off the resting list.
        "put_back": 0, "unrested": 0,
        "proposals_retracted": 0,
        # CUT-A (R-A) — while closing on evidence is OFF, the promises a
        # meeting shows were kept land as review proposals instead of
        # closes; ONE line counts them from the written proposals.
        "kept_in_meetings_proposed": 0,
        # SPEC_LEARN1 D2 — the learning job's own line. One category, three
        # classes, counted from the WRITTEN change events (the receipt-honesty
        # rule: the event IS the receipt), each carrying its own plain
        # sentence so the feed never has to restate a threshold or a store.
        "learned": 0,
    }
    refs: dict[str, list] = {k: [] for k in counts}

    # UNCONFEXP1 — the auto-expiry's disclosure is narrated from the WRITTEN
    # closures themselves (the PID1 receipt-honesty rule: the event IS the
    # receipt), keyed on the canonical reason vocabulary rather than a
    # hand-spelled string, so a human's Drop, a `done`, and every other
    # closure stay out of this count by construction.
    try:
        from event_types import RESOLUTION_REASON_KEY as _RRK
        from event_types import REVIEW_EXPIRY_REASON as _RER
    except Exception:  # pragma: no cover — vocabulary home unreadable
        _RRK, _RER = "resolution_reason", "review_expired"
    # MF-11c-1 — the age-out reason, read from the module that writes it.
    try:
        from commitment_backlog_sweep import AGE_OUT_REASON as _AGED_OUT
    except Exception:  # pragma: no cover — vocabulary home unreadable
        _AGED_OUT = "aged_out"
    try:
        from commitment_policy import MATCH_DOOR as _MATCH_DOOR
        from commitment_policy import RETRACT_REASON as _RETRACT
        from commitment_policy import CLOSE_CHANGE_CLASS as _CLOSE_CLASS
    except Exception:  # pragma: no cover
        _MATCH_DOOR, _RETRACT, _CLOSE_CLASS = "match", "policy_retracted", "commitment_close"

    events = _load_events(workspace_root)
    # POLICY1-B (c) — a close a later undo reversed (inside this window) is
    # not narrated as a close: the row is open now. The undo itself is
    # narrated by `changes_undone`. One fold, the closure chain's own.
    try:
        from closure_index import reversed_closer_positions as _reversed
        reversed_at = _reversed(events, until=now_dt)
    except Exception:  # pragma: no cover
        reversed_at = set()
    # ATTRIB2 (M's ruling 2026-09-07) — the SAME fold, one family wider: an
    # act an undo has already reversed is not news this feed may report. The
    # 14:xx brief on 2026-09-07 announced a prospect promoted to client after
    # that promotion had been put back, and the Friday wrap listed a second
    # one under "Decided for you" WITH an undo offer.
    try:
        from closure_index import reversed_act_positions as _reversed_acts
        reversed_acts_at = _reversed_acts(events, until=now_dt)
    except Exception:  # pragma: no cover — never widen a count on a read failure
        reversed_acts_at = set()
    # ATTRIB2 fix round 1 (reviewer F-4) — the SENT rail's line is counted
    # from the receipt's own `n_closed`, so neither position fold above can
    # reach it: on 2026-09-07 the brief still offered an undo for 4 sent-mail
    # closes when the same fire had reopened 2 of them a minute later. Fold
    # the receipt at its own grain, so the line and its undo offer describe
    # the same rows a person's `undo` of that batch would actually reverse.
    try:
        from closure_index import reversed_sent_closes_by_receipt as _rev_sent
        reversed_sent_at = _rev_sent(events, until=now_dt)
    except Exception:  # pragma: no cover — never shrink a count on a read failure
        reversed_sent_at = {}
    # ATTRIB2 — WHO acted. "You" is a claim about the customer, so it is asked
    # of the act's own actor rather than assumed.
    try:
        from event_types import is_customer_act as _customer_act
    except Exception:  # pragma: no cover
        _customer_act = lambda ev: True
    # POLICY1-B DD-10 — the meetings line narrates per GROUP: one entry per
    # meeting under the fire (`undo_group`), each with the group batch id
    # a person can name (`undo 1a`), attached to the line as `groups`.
    meeting_groups: dict = {}
    # SPEC_LEARN1 DD-3 — the plain sentences the learning job wrote on its own
    # events, in append order. The feed renders THEM rather than composing a
    # sentence of its own: the job knows which phrase it stopped using and
    # this reader does not, and a second sentence about the same act is a
    # second place for it to drift.
    learned_lines: List[str] = []
    # NUMBER1 3.8 / 3.7 -- per-batch and per-event detail the renderers
    # below need in order to say ONE true sentence each.
    learned_batches: dict = {}
    promotion_evidence: List[tuple] = []
    # MF-11c-1 — which batches a JOB ran, read off the job's own receipt
    # (`pack_run` naming the batch, fired on a schedule or under a machine
    # source). Collected over the WHOLE stream, not the window: the
    # receipt is written seconds after its rows and a window edge can
    # fall between them.
    job_batches = _job_batches(events)

    for pos, ev in enumerate(events):
        if not _in_window(ev):
            continue
        etype = ev.get("type")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        seq = ev.get("seq")

        if etype == "sent_reconcile":
            n = data.get("n_closed") or 0
            if isinstance(n, (int, float)) and n > 0:
                # ATTRIB2 fix round 1 — minus the ones already put back. A
                # run whose every close was reversed says nothing at all.
                n = int(n) - int(reversed_sent_at.get(pos, 0))
            if isinstance(n, (int, float)) and n > 0:
                counts["closed_from_sent"] += int(n)
                refs["closed_from_sent"].append(seq)
            n_open = data.get("n_opened") or 0
            if isinstance(n_open, (int, float)) and n_open > 0:
                counts["opened_from_sent"] += int(n_open)
                refs["opened_from_sent"].append(seq)
            # CONTACT1 — contacts auto-created from the CEO's own sent mail
            # join the SAME `people_added` line the identity reconciler feeds:
            # one thing happened ("you have new contacts"), so it reads as one
            # line with one undo affordance, whichever feeder produced it.
            # Counted from the RECEIPT of what was written, never a plan.
            n_contacts = data.get("n_contacts_added") or 0
            if isinstance(n_contacts, (int, float)) and n_contacts > 0:
                counts["people_added"] += int(n_contacts)
                refs["people_added"].append(seq)
        elif etype == "session_sweep_run":
            n = None
            for key in ("events_recovered", "n_recovered", "recovered",
                        "events_promoted"):
                if isinstance(data.get(key), (int, float)):
                    n = int(data[key])
                    break
            if n:
                counts["swept"] += n
                refs["swept"].append(seq)
        elif etype == "cleanup_run":
            counts["cleanup_runs"] += 1
            refs["cleanup_runs"].append(seq)
        elif etype == "maintenance_run":
            n = data.get("jobs_completed")
            if isinstance(n, list):
                n = len(n)
            if isinstance(n, (int, float)) and n > 0:
                counts["maintenance_jobs"] += int(n)
                refs["maintenance_jobs"].append(seq)
        elif etype == "identity_reconcile_run":
            # PID1 D6 — auto-applied identity creations are narrated from
            # the RECEIPT of what was actually written, never from a plan
            # (honesty rule). Same for the §0-2 exact-email links.
            n = data.get("n_auto_added") or 0
            if isinstance(n, (int, float)) and n > 0:
                counts["people_added"] += int(n)
                refs["people_added"].append(seq)
            n = data.get("n_linked") or 0
            if isinstance(n, (int, float)) and n > 0:
                counts["people_linked"] += int(n)
                refs["people_linked"].append(seq)
            # UXR1 D3 — the auto-link lane (exact-unique-clean name
            # mentions), narrated with the undo affordance: the links
            # reverse via brain_undo (reopen + a confirm row returns the
            # decision to the human).
            n = data.get("n_auto_linked") or 0
            if isinstance(n, (int, float)) and n > 0:
                counts["people_auto_linked"] += int(n)
                refs["people_auto_linked"].append(seq)
        elif etype == "org_promoted":
            if pos in reversed_acts_at:  # ATTRIB2 — put back since; not news
                continue
            # DEALNAG1 (M ruling 4) — the automatic prospect -> client
            # promotion gets ONE line here and nowhere else: it is not a
            # decision to route to a chat, so it is never a queue row, never
            # a question, and never repeated. Narrated from the WRITTEN
            # promotion event (the PID1 receipt-honesty rule — the event IS
            # the receipt), refs traceable, undo standing.
            counts["orgs_promoted"] += 1
            refs["orgs_promoted"].append(seq)
            # NUMBER1 3.7 -- keep each promotion's own evidence clause.
            promotion_evidence.append(
                (_promotion_clause(workspace_root, data), seq))
        elif etype in ("person_fact_observed", "org_fact_observed"):
            # HIST1 Part 2 (D3/S1) — ONLY auto-noted structured facts are
            # narrated (they carry the brain_change_class stamp); explicit
            # user facts and confirmed-proposal facts are the user's own
            # actions, not "what the brain did". Narrated from the WRITTEN
            # events themselves (the PID1 receipt-honesty rule — the fact
            # event IS the receipt), refs traceable, undo standing.
            if data.get("brain_change_class") == "entity_fact_structured":
                counts["facts_noted"] += 1
                refs["facts_noted"].append(seq)
        elif etype in ("voice_block_updated", "prep_weights_updated",
                       "exemplar_promoted"):
            # SPEC_LEARN1 — ONLY changes the learning job made on its own are
            # narrated here: the class stamp is what says so (a future writer
            # of these types without the stamp - a seed, a confirmed card -
            # would be the customer's own act, not "what the brain did"). An act an undo has already reversed is
            # not news (the ATTRIB2 fold).
            if (data.get("brain_change_class") in _LEARNING_CLASSES
                    and pos not in reversed_acts_at):
                counts["learned"] += 1
                refs["learned"].append(seq)
                line = data.get("delta") or data.get("render_line")
                if isinstance(line, str) and line.strip():
                    learned_lines.append(line.strip())
                # NUMBER1 3.8 (routed in from LEARNFIX1 1.3) -- the BATCH and
                # the skill it touched, so the feed can say one sentence for
                # the whole pass instead of one per change.
                #
                # FIX ROUND 1 (REVIEW F-7) -- A BATCHLESS EVENT IS ITS OWN
                # BATCH. This keyed on `str(brain_batch_id or "")`, so every
                # learning event in one window that carried NO batch id
                # collapsed under the empty-string key into a single line:
                # two unrelated writes read as one pass. `learning_pass`
                # always mints an id, but any other writer of
                # `voice_block_updated` / `prep_weights_updated` /
                # `exemplar_promoted` need not, and a conflation nobody
                # asked for is the defect class this lane exists to close.
                # Keyed on the event's own seq when it carries no batch id.
                # Insertion order is FIRST-SEEN order, the same ordering
                # `promotion_groups` uses beside it (it was `sorted()` by
                # batch-id string, which is chronological only because real
                # ids happen to be timestamps).
                _bkey = (str(data.get("brain_batch_id"))
                         if data.get("brain_batch_id") else "seq:%s" % seq)
                learned_batches.setdefault(
                    _bkey, {"skills": [], "seqs": [], "texts": []})
                _lb = learned_batches[_bkey]
                _lb["seqs"].append(seq)
                if isinstance(line, str) and line.strip():
                    _lb["texts"].append(line.strip())
                if data.get("skill"):
                    _lb["skills"].append(str(data.get("skill")))
        elif etype == "brain_proposal":
            counts["new_proposals"] += 1
            refs["new_proposals"].append(seq)
        elif etype == "brain_proposal_resolved":
            action = data.get("user_action")
            if action == "declined":
                counts["proposals_declined"] += 1
                refs["proposals_declined"].append(seq)
            elif action in ("applied", "edited") and not _machine_resolved(ev):
                # REVIEW v5.28.0 F-3 — "You confirmed N proposals" is a claim
                # about what the PERSON did, so it counts only a human's
                # confirm. Every auto rail (person_link, commitment_merge,
                # entity facts, org_promotion) resolves its own proposal as
                # `applied` with `resolved_by=<its own skill>`, and the
                # deal-signal retirement resolves as `superseded` with
                # `resolved_by="system"`; on a customer's morning that read
                # as "You confirmed 2 proposals" under "Promoted 1 prospect
                # to client" — they clicked `mark won` and confirmed nothing.
                # The false line also outranked real news for the three-line
                # slot. A human confirm carries a person id (apply-choices
                # passes the user's person_id); a legacy row with no
                # `resolved_by` at all is still counted, because that shape
                # only ever came from the confirm card. Skips / supersedes
                # are neither a confirm nor a decline and say nothing here.
                counts["proposals_resolved"] += 1
                refs["proposals_resolved"].append(seq)
        elif etype == "brain_proposal_expired":
            counts["proposals_expired"] += 1
            refs["proposals_expired"].append(seq)
        elif etype == "brain_change_undone":
            # ATTRIB2 — "Undid N changes YOU reversed" is a claim about what
            # the PERSON did. The maintenance fire that read its own sent-mail
            # closes, judged them wrong and reopened both (seqs 15503-15506)
            # reversed them on its OWN judgment; the brief nevertheless told M
            # "reopened on your undo". The machine's own reversals are counted
            # apart and say NOTHING here: what they reversed is already folded
            # out of every line above, so there is no act left to report and
            # one false sentence leaves the morning.
            if _customer_act(ev):
                counts["changes_undone"] += 1
                refs["changes_undone"].append(seq)
            else:
                counts["changes_reversed_by_machine"] += 1
                refs["changes_reversed_by_machine"].append(seq)
        elif etype == "commitment_resolved":
            # UNCONFEXP1 — one counted line for the unconfirmed-pile expiry
            # (COVERQUIET1 posture: speaks only when N>0, and the standing
            # `undo` rides in the sentence because the batch is one gesture
            # away). Counts every closure stamped with the canonical lapse
            # reason — the scheduled daily drain and a hand-typed `review
            # amnesty` write the identical event, and they are the same fact
            # to the reader: captures lapsed unanswered.
            #
            # SPEC SURFACEFIX1 5.6 — AND A LAPSE PUT BACK IS NOT A LAPSE.
            # This was the ONE closer branch below that never consulted
            # `reversed_at`: every sibling (own word, deal, silence, meetings,
            # calendar) has folded reversals out since POLICY1-B (c), and this
            # one counted a row the customer had already restored. It matters
            # twice over — the count is wrong, and the sentence's standing
            # `undo` offers to reverse something already reversed, which is
            # the exact defect ATTRIB2 fix round 1 removed from the sent rail.
            # It is also what 5.6's named form inherits: `decided_for_you`
            # deliberately does no fold of its own and reads this
            # classification, so the fold has to be true HERE or it is true
            # nowhere.
            if (str(data.get(_RRK) or "").strip().lower() == _RER):
                if pos not in reversed_at:
                    # NUMBER1 3.6 (R-22) -- WHO RAN IT, off the job's own
                    # `pack_run` receipt (`_job_batches`) and never off the
                    # actor stamp. The 45-row batch of 2026-09-16 06:41 is
                    # signed `actor_kind: person / person_001` on the ledger
                    # because the review-expiry job writes its rows under the
                    # backlog-sweep verb's signature; its receipt names the
                    # run, which is what marks it (MF-11c-5's rule, read
                    # from one place).
                    own = str(data.get("brain_batch_id") or "")
                    parent = str(data.get("parent_batch_id") or "")
                    key = ("unconfirmed_expired"
                           if (own and own in job_batches)
                           or (parent and parent in job_batches)
                           else "cleared_review_amnesty")
                    counts[key] += 1
                    refs[key].append(seq)
            # MF-11c-1 BEGIN — the aged-out close. Before this branch the
            # 09-13 batch (48 rows) matched NOTHING here: it carries no
            # `exit_route`, no `confirmed_by`, no `resolved_by_match`, so
            # the brief's CHANGED strip said nothing and the undo phrase
            # BRIEF2 attaches had no line to ride. A job's pass and the
            # customer's own amnesty get different sentences (see the
            # counts dict); a close an undo put back is not narrated.
            elif str(data.get(_RRK) or "").strip().lower() == _AGED_OUT:
                if pos in reversed_at:
                    continue
                own = str(data.get("brain_batch_id") or "")
                parent = str(data.get("parent_batch_id") or "")
                key = ("let_go_backlog"
                       if (own and own in job_batches)
                       or (parent and parent in job_batches)
                       else "cleared_amnesty")
                counts[key] += 1
                refs[key].append(seq)
            # MF-11c-1 END
            # EXIT1 — the customer's OWN WORD. FIRST, and before the
            # transcript branch, because an own-word close also comes through
            # the `match` door and would otherwise be narrated as "your
            # meetings said this was done" — which is the sentence for the
            # closer that is OFF, and is not what happened. Keyed on the
            # writer's own confirmation stamp, which no other rail may write.
            elif (str(data.get("confirmed_by") or "") == "own_word"
                  and data.get("brain_batch_id")
                  and str(data.get("resolution") or "done") == "done"):
                if pos not in reversed_at:
                    counts["closed_from_your_word"] += 1
                    refs["closed_from_your_word"].append(seq)
            # EXIT1 — a signed agreement or a paid invoice on file.
            elif (str(data.get("exit_route") or "") == "fact"
                  and str(data.get("proof") or "") == "payment_or_agreement"
                  and data.get("brain_batch_id")
                  and str(data.get("resolution") or "done") == "done"):
                if pos not in reversed_at:
                    counts["closed_from_deal"] += 1
                    refs["closed_from_deal"].append(seq)
            # EXIT1 — the silence rail's let-go. A `dropped`, never a `done`:
            # nothing says it was finished, only that nobody touched it for
            # two months. It is the WRAP's line, not the brief's.
            elif (str(data.get("exit_route") or "") == "silence"
                  and data.get("brain_batch_id")
                  and str(data.get("resolution") or "") == "dropped"):
                if pos not in reversed_at:
                    counts["let_go_quiet"] += 1
                    refs["let_go_quiet"].append(seq)
            # POLICY1-A D14 — a close the TRANSCRIPT closer wrote through the
            # `match` door with its fire batch (the pairing brain_undo lists):
            # the brain's own act, narrated once with its undo. A user's
            # close (any other door) is the user's, not the brain's.
            elif (data.get("resolved_by_match") == _MATCH_DOOR
                  and data.get("brain_change_class") == _CLOSE_CLASS
                  and data.get("brain_batch_id")
                  and str(data.get("resolution") or "done") == "done"):
                if pos in reversed_at:  # (c): undone since — open now, not a close
                    continue
                counts["closed_from_meetings"] += 1
                refs["closed_from_meetings"].append(seq)
                g = meeting_groups.setdefault(
                    str(data.get("brain_batch_id")),
                    {"batch_id": str(data.get("brain_batch_id")),
                     "parent_batch_id": str(data.get("parent_batch_id") or ""),
                     "group": str(data.get("undo_group") or ""), "n": 0, "refs": []})
                g["n"] += 1
                g["refs"].append(seq)
                # M ruling 2026-09-03 — how many of them were captures the
                # CEO had never confirmed. Counted from the row's own
                # `confirmed_by` / reason, never inferred.
                try:
                    from event_types import is_automatic_transcript_close as _auto
                except Exception:  # pragma: no cover
                    _auto = lambda d: str((d or {}).get("confirmed_by") or "") == "transcript"
                if _auto(data):
                    counts["closed_unconfirmed"] += 1
                    refs["closed_unconfirmed"].append(seq)
            # POLICY1-B DD-7 (F-2) — the CALENDAR closer's own close: the
            # meeting the row was about happened with the other side present.
            # Keyed on the writer's own stamp + the batch the reverser lists;
            # a close undone since is not narrated as a close.
            elif (data.get("calendar_close") is True
                  and data.get("brain_batch_id")
                  and str(data.get("resolution") or "done") == "done"):
                if pos in reversed_at:  # (c): undone since — not a close
                    continue
                counts["closed_from_calendar"] += 1
                refs["closed_from_calendar"].append(seq)
                try:
                    from event_types import is_automatic_calendar_close as _cal
                except Exception:  # pragma: no cover
                    _cal = lambda d: str((d or {}).get("confirmed_by") or "") == "calendar"
                if _cal(data):
                    counts["closed_from_calendar_unconfirmed"] += 1
                    refs["closed_from_calendar_unconfirmed"].append(seq)
        elif etype == "commitment_updated":
            # POLICY1-B DD-9 (F-2) — a PARK the product wrote on its own
            # (the quiet owed-to-you lane): the row is still open, it moved
            # to PARKED with its reason. Counted from the written hint with
            # its batch; a user's own park (no batch) is the user's.
            if (data.get("brain_change_class") == "commitment_park"
                    and data.get("brain_batch_id")
                    and data.get("status_hint") == "parked"):
                # FIX ROUND 1 (reviewer F-4) — A PARK THE CUSTOMER PUT BACK IS
                # NOT A PARK THIS FEED MAY REPORT. Every closer branch here
                # consults a fold; these two never did, because they sit on
                # `commitment_updated` and `reversed_closer_positions` only
                # covers closers. `undo_batch` writes one `brain_change_undone`
                # per reversed change carrying `change_ref: seq:<the park's
                # own seq>`, which is exactly what `reversed_act_positions`
                # reads — the SAME fold `org_promoted` and the learning rail
                # already take, one family wider. Without it the wrap NAMED
                # rows M had already un-parked, with an undo offer for a row
                # that was already back.
                if pos in reversed_acts_at:
                    continue
                # EXIT1 — the silence rail's rest has its own line: the
                # existing one says "owed to you", and these are mostly items
                # the customer owes themselves.
                key = ("rested_quiet"
                       if str(data.get("exit_route") or "") == "silence"
                       else "parked_quiet")
                counts[key] += 1
                refs[key].append(seq)
            # EXIT1 (review R-5) — the un-rest: a row that stopped resting
            # because the other side came back about it.
            elif (data.get("brain_change_class") == _unrest_change_class()
                  and data.get("brain_batch_id")
                  and data.get("unparked")):
                counts["unrested"] += 1
                refs["unrested"].append(seq)
        elif etype == "commitment_reopened":
            # EXIT1 (review R-4) — ONLY the counter-evidence put-back. Every
            # other reopen in the stream is somebody's undo, and an undo is
            # already narrated as one (`brain_change_undone`).
            if (data.get("brain_change_class")
                    == _counter_evidence_change_class()
                    and data.get("brain_batch_id")):
                counts["put_back"] += 1
                refs["put_back"].append(seq)
        elif etype == "commitment_review_dismissed":
            # POLICY1-A D15 — a retract is the brain withdrawing its own
            # question; counted from the written dismissal, never a plan.
            if str(data.get(_RRK) or "").strip().lower() == _RETRACT:
                counts["proposals_retracted"] += 1
                refs["proposals_retracted"].append(seq)
        elif etype == "commitment_review_proposed":
            # CUT-A — a proposal written off a MEETING that carried a
            # completion signal (the row the pass would have closed with
            # the switch on). Read by identity; a paste or a note is not a
            # meeting and says nothing here.
            if (data.get("has_completion_signal") is True
                    and str(data.get("source_ref") or "").startswith("granola:")):
                counts["kept_in_meetings_proposed"] += 1
                refs["kept_in_meetings_proposed"].append(seq)

    # NUMBER1 3.7 -- one group per distinct evidence clause, in first-seen
    # order, so two promotions with the same evidence share one line and
    # two with different evidence never do.
    promotion_groups: List[tuple] = []
    for clause, seq in promotion_evidence:
        hit = next((g for g in promotion_groups if g[0] == clause), None)
        if hit is None:
            promotion_groups.append((clause, [seq]))
        else:
            hit[1].append(seq)

    lines: List[dict] = []

    def _line(category: str, text: str, **extra) -> None:
        row = {"text": text, "category": category,
               "refs": [r for r in refs[category] if r is not None]}
        row.update(extra)
        lines.append(row)

    n = counts["closed_from_sent"]
    if n:
        _line("closed_from_sent",
              f"Closed {n} {_plural(n, 'commitment')} matched to your sent "
              f"mail — say `undo` to reopen any.")
    if counts["orgs_promoted"]:
        # NUMBER1 3.7 -- THE EVENT'S OWN EVIDENCE, never a sentence the
        # event does not carry. This line asserted "they had a paid or
        # signed record on file" over every promotion; on 2026-09-16 it
        # stood over a hand `mark won` on a deal with no value, already
        # undone (B1.4). `org_promotion` already writes the distinction on
        # the event (`evidence`, from `promotion_reason`), so the feed reads
        # it rather than restating one of the two. Promotions whose evidence
        # differs get one line each.
        for clause, seqs in promotion_groups:
            n = len(seqs)
            # FIX ROUND 1 (REVIEW F-8) -- the separator belongs to the
            # sentence, not to the tail. An event carrying no evidence read
            # "Promoted 1 prospect to client; say `undo` ...", the semicolon
            # landing straight on "client" with no clause in front of it.
            # The house separator is the em dash; the semicolon only ever
            # divides the evidence clause from the undo.
            undo = f"say `undo` to put {'them' if n > 1 else 'it'} back."
            head = f"Promoted {n} {_plural(n, 'prospect')} to client"
            _line("orgs_promoted",
                  (f"{head} — {clause}; {undo}" if clause
                   else f"{head} — {undo}"),
                  refs=seqs)
    n = counts["closed_from_meetings"]
    if n:
        # POLICY1-A D14 — the FB-20 read-only grammar: one line, the count
        # off the written closes, the standing `undo` in the sentence (the
        # fire batch reverses through the registered reopen reverser).
        n_unconf = counts["closed_unconfirmed"]
        groups = sorted(meeting_groups.values(), key=lambda g: (-g["n"], g["batch_id"]))
        n_groups = len(groups)
        # DD-10 — one sentence, and the group count in it when the closes
        # came from more than one meeting: `undo` lists each meeting as its
        # own line, so a person can put back one meeting's closes and keep
        # the rest.
        tail = (" — each carries the words that closed it; say `undo` to "
                "reopen any.")
        if n_groups > 1:
            tail = (f" across {n_groups} meetings — each carries the words that "
                    "closed it; say `undo` to see the meetings one by one and "
                    "reopen any of them.")
        _line("closed_from_meetings",
              f"Closed {n} {_plural(n, 'commitment')} your meetings said "
              f"{'was' if n == 1 else 'were'} done"
              + (f" ({n_unconf} of them {'a capture' if n_unconf == 1 else 'captures'} "
                 f"you had not confirmed)" if n_unconf else "")
              + tail, groups=groups)
    n = counts["kept_in_meetings_proposed"]
    if n and not _transcript_closes_on(workspace_root):
        # CUT-A (R-A) — the pass is off, so the closes it would have made
        # are proposals; one plain sentence, no `undo` (nothing moved), no
        # question, and the two verbs that act on it.
        _line("kept_in_meetings_proposed",
              f"{n} {_plural(n, 'promise')} {'looks' if n == 1 else 'look'} kept in "
              f"your meetings — say `turn on closing on evidence` to let "
              f"{'it' if n == 1 else 'them'} close on {'its' if n == 1 else 'their'} "
              f"own, or `needs your call` to see {'it' if n == 1 else 'them'}.")
    n = counts["closed_from_calendar"]
    if n:
        # POLICY1-B DD-7 (F-2) — one line, one `undo`, and it says when a
        # close was of a guess nobody confirmed (F-1).
        n_g = counts["closed_from_calendar_unconfirmed"]
        _line("closed_from_calendar",
              f"Closed {n} scheduling {_plural(n, 'item')} whose meeting has since "
              f"happened with the other side in the room"
              + (f" ({n_g} of them {'a guess' if n_g == 1 else 'guesses'} you had "
                 f"never confirmed)" if n_g else "")
              + " — say `undo` to reopen any.")
    n = counts["closed_from_your_word"]
    if n:
        # EXIT1 — plain words, the customer's own act named as theirs to
        # recognise, the machine's act offered back with its undo.
        _line("closed_from_your_word",
              f"Closed {n} {_plural(n, 'item')} you said you had finished — "
              f"say `undo` to put {'it' if n == 1 else 'them'} back.")
    n = counts["closed_from_deal"]
    if n:
        _line("closed_from_deal",
              f"Closed {n} {_plural(n, 'item')} the record shows signed or "
              f"paid — say `undo` to put {'it' if n == 1 else 'them'} back.")
    n = counts["rested_quiet"]
    if n:
        _line("rested_quiet",
              f"Rested {n} {_plural(n, 'item')} nobody has touched in six "
              f"weeks — still yours, just off the working list; say `undo` "
              f"to put {'it' if n == 1 else 'them'} back.")
    n = counts["let_go_quiet"]
    if n:
        # UNDOLAND1 (M's ruling 7b) — the line names WHERE the `undo` puts
        # them. A put-back let-go goes back on the RESTING list with its
        # quiet clock restarted, not onto the working list; the same sentence
        # is composed in `exit_doors.let_go_wrap_line` and the two must
        # agree.
        _line("let_go_quiet",
              f"Let go {n} {_plural(n, 'item')} you never touched — say "
              f"`undo` to put {'it' if n == 1 else 'them'} back on the "
              f"resting list.")
    n = counts["put_back"]
    if n:
        _line("put_back",
              f"Put {n} {_plural(n, 'item')} back — they came back about "
              f"{'it' if n == 1 else 'them'} after I had closed "
              f"{'it' if n == 1 else 'them'}; say `undo` to close "
              f"{'it' if n == 1 else 'them'} again.")
    n = counts["unrested"]
    if n:
        _line("unrested",
              f"Put {n} {_plural(n, 'item')} back on your working list — "
              f"they came back about {'it' if n == 1 else 'them'} after I "
              f"had rested {'it' if n == 1 else 'them'}; say `undo` to rest "
              f"{'it' if n == 1 else 'them'} again.")
    n = counts["parked_quiet"]
    if n:
        # POLICY1-B DD-9 (F-2) — a park is not a close: the row is open and
        # on the plate, and the sentence says so.
        _line("parked_quiet",
              f"Parked {n} {_plural(n, 'item')} owed to you that went quiet — "
              f"still open, under PARKED with the reason; say `undo` to put "
              f"{'it' if n == 1 else 'them'} back.")
    n = counts["opened_from_sent"]
    if n:
        _line("opened_from_sent",
              f"Started tracking {n} new {_plural(n, 'promise')} from your "
              f"sent mail.")
    n = counts["swept"]
    if n:
        _line("swept",
              f"Recovered {n} {_plural(n, 'item')} from your ad-hoc chats "
              f"into the workspace record.")
    n = counts["people_added"]
    if n:
        # PID1 D6 — the brief's read-only grammar (FB-20): a chat-phrase
        # undo affordance, no verbs, no rows. `undo` reverses the whole
        # batch via brain_undo (adds archive — never delete).
        _line("people_added",
              f"Added {n} {_plural(n, 'person', 'people')} from "
              f"corroborated evidence — say `undo` to reverse.")
    n = counts["people_linked"]
    if n:
        _line("people_linked",
              f"Linked {n} {_plural(n, 'name')} to "
              f"{_plural(n, 'a contact', 'contacts')} already on file "
              f"(exact email match).")
    n = counts["people_auto_linked"]
    if n:
        # UXR1 D3 — the ruled receipt line, verbatim shape: the read-only
        # FB-20 grammar (chat-phrase undo affordance, no verbs, no rows).
        _line("people_auto_linked",
              f"Linked {n} {_plural(n, 'name-mention')} to existing "
              f"contacts — say `undo` to reverse any.")
    n = counts["facts_noted"]
    if n:
        # HIST1 Part 2 — the FB-20 read-only grammar: a chat-phrase undo
        # affordance, no verbs, no rows. `undo` retracts the batch via
        # brain_undo (appends entity_fact_retracted — never edits history).
        _line("facts_noted",
              f"Noted {n} {_plural(n, 'fact')} from your connected "
              f"sources — say `undo` to reverse.")
    n = counts["learned"]
    if n:
        # One line per change, the job's own words, plus the undo affordance
        # in the same sentence. No threshold, no decimal, no store name, no
        # skill name — A12's per-skill noun is already inside the sentence.
        # NUMBER1 3.8 (routed in from LEARNFIX1 1.3) -- ONE LINE PER BATCH.
        # The job applied four changes on 2026-09-15 and the brief narrated
        # ONE of them (B6.3): four ordinary lines met an ordinary cap of
        # three and three real changes to how the product writes reached no
        # surface. One line naming every skill the pass touched is shorter
        # than four, carries the whole batch's undo -- which is what the
        # reader needs -- and cannot be eaten three-quarters of the way
        # through. M allowed four lines as the alternative; the default is
        # this one (SPEC_FIXTRAIN_v5310 ruling 2, taken).
        #
        # FIX ROUND 1 (REVIEW F-3, coordinator ruling): ONE LINE PER WINDOW,
        # not one per batch. One line per batch is one line on a same-day
        # window (`learning_pass.run` mints one batch id per pass) and N
        # UNCAPPED lines on a catch-up window covering several passes —
        # measured: four batches of one produced four lines, all four
        # surviving the brief's cap of three, where at base at most three
        # reached the strip. `learned` sits in the uncapped machine-batch
        # group, so nothing downstream would have cut them. Spec ruling 2
        # says "one line naming the skills, not four"; a window is what the
        # reader is looking at, so the window is the unit.
        #
        # The nouns are UNIONED across the window's batches, first-seen
        # order, and a window holding more than one pass says so in plain
        # words ("over 3 passes") rather than implying one.
        #
        # WHAT `undo` REVERSES, stated: the LATEST batch in the window. One
        # pointer cannot name several batches, `undo` means the last thing
        # done everywhere else in the product, and reversing every pass in
        # the window off a line the reader has only just been shown would
        # undo work they were never told about one pass at a time.
        # THE EXPOSURE THAT REMAINS: on a multi-pass window the earlier
        # passes are named in the sentence but not reachable from its
        # `undo` — they stay applied, and `what have you learned` is where
        # they are reviewed and reversed one at a time.
        batches = list(learned_batches.values())   # first-seen order
        n_batches = len(batches)
        nouns = _learned_nouns([s for b in batches for s in b["skills"]])
        latest = max(batches, key=lambda b: max(b["seqs"]))
        over = (" over %d passes" % n_batches) if n_batches > 1 else ""
        undo = ("say `undo` to put the last pass back."
                if n_batches > 1 else "say `undo` to put it back.")
        if n_batches == 1 and len(batches[0]["seqs"]) == 1 and batches[0]["texts"]:
            # A BATCH OF ONE IS ITS OWN SENTENCE. Collapsing here would
            # throw away the change's own receipt wording (the exemplar
            # leg's "Made that the standard layout for your ...") and
            # buy nothing: one line is already one line.
            text = batches[0]["texts"][0]
            if not text.rstrip().endswith("back."):
                text = text + " Say `undo` to put it back."
        elif nouns:
            text = ("Picked up how you rewrite your " + _and_list(nouns)
                    + over + " — " + undo)
        else:
            text = ("Picked up " + str(n) + " " + _plural(n, "thing")
                    + " from how you rewrite my drafts" + over
                    + " — " + undo)
        _line("learned", text,
              refs=(latest["seqs"] if n_batches > 1
                    else [s for b in batches for s in b["seqs"]]))
    n = counts["proposals_resolved"]
    if n:
        _line("proposals_resolved",
              f"You confirmed {n} {_plural(n, 'proposal')} — applied through "
              f"the standard writers.")
    n = counts["changes_undone"]
    if n:
        _line("changes_undone",
              f"Undid {n} {_plural(n, 'change')} you reversed.")
    n = counts["unconfirmed_expired"]
    if n:
        # UNCONFEXP1 — the quiet line, only when something actually lapsed
        # (never filler), with the recovery affordance in the same sentence:
        # the whole batch reopens on one `undo`, back into the queue exactly
        # as it left.
        # NUMBER1 3.6 (R-22) -- CREDITED TO ITS DOOR. The door's name is
        # `NAMED_ACT_DOORS`', the same string End of Day's named form and
        # the wrap's "Decided for you" print, so one act is never described
        # by two vocabularies. On 2026-09-16 this line said only "Closed 45
        # stale unconfirmed extractions" and End of Day said "the review
        # door"; a reader comparing the two surfaces could not tell they
        # were about one batch.
        _line("unconfirmed_expired",
              f"{NAMED_ACT_DOORS['unconfirmed_expired'].capitalize()} closed "
              f"{n} stale unconfirmed {_plural(n, 'extraction')} nobody "
              f"answered — say `undo` to put "
              f"{'it' if n == 1 else 'them'} back.")
    n = counts["cleared_review_amnesty"]
    if n:
        # ...and the customer's own amnesty says "you", because they typed
        # it. Never a door, never "decided for you".
        _line("cleared_review_amnesty",
              f"You cleared {n} stale unconfirmed "
              f"{_plural(n, 'extraction')} nobody had answered — say "
              f"`undo` to put {'it' if n == 1 else 'them'} back.")
    # MF-11c-1 — the aged-out lines. The job's says who acted (a pass,
    # not the reader); the amnesty's says "you" because the customer
    # typed it. Both carry the way back: the whole batch reverses on one
    # `undo` (a ref naming the run resolves every group under it).
    n = counts["let_go_backlog"]
    if n:
        _line("let_go_backlog",
              f"A background pass let go {n} old "
              f"{_plural(n, 'commitment')} nobody had touched — say "
              f"`undo` to put {'it' if n == 1 else 'them'} back.")
    n = counts["cleared_amnesty"]
    if n:
        _line("cleared_amnesty",
              f"You cleared {n} old {_plural(n, 'commitment')} in an "
              f"amnesty — say `undo` to put "
              f"{'it' if n == 1 else 'them'} back.")
    n = counts["proposals_retracted"]
    if n:
        _line("proposals_retracted",
              f"Withdrew {n} unanswered {_plural(n, 'question')} nothing "
              f"came back on (nothing was changed).")
    n = counts["proposals_expired"]
    if n:
        _line("proposals_expired",
              f"{n} unanswered {_plural(n, 'proposal')} expired quietly "
              f"(nothing was changed).")
    n = counts["cleanup_runs"]
    if n:
        _line("cleanup_runs", "Ran the weekly cleanup pass.")
    n = counts["maintenance_jobs"]
    if n:
        _line("maintenance_jobs",
              f"Completed {n} background maintenance "
              f"{_plural(n, 'job')} on schedule.")

    if max_lines is not None:
        lines = lines[:max_lines]
    return {"lines": lines, "counts": counts,
            "since_ts": since_ts, "now": now_iso}


# ---------------------------------------------------------------------------
# SPEC SURFACEFIX1 5.6 — "DECIDED FOR YOU" NAMES THE ACT
# ---------------------------------------------------------------------------
#
# `changes_since` above says COUNTS ("Withdrew 3 unanswered questions"), which
# is the right shape for the brief's CHANGED strip and the wrong one for the
# wrap's "Decided for you" and the day-close's machine-acts block: a reader
# who cannot see WHICH row moved cannot judge whether the machine was right,
# and "3" is not something anybody can check. The v5.30.0 attended test
# (B2.7, B1.4) saw both surfaces render the counts and nothing else.
#
# This reader adds the NAMED form on top of the same classification — it does
# not re-classify. `changes_since` already folded every reversed act out of
# `refs` (the ATTRIB2 / POLICY1-B folds), so a seq that reaches this function
# is an act that STOOD at `now_iso`, and a reversed act can never be named
# here. That is the whole of "never a reversed act": one fold, in one place,
# and this function inherits it rather than repeating it.
#
# One line per act: the row's own title, the door that moved it, the date.
# Never a count in the line itself (the header count is the caller's option),
# never a wire id, never a batch id, never a skill name.

#: The categories that have ROWS to name. Every other category in
#: `changes_since` is a receipt-level batch (`closed_from_sent`'s
#: `n_closed`, `maintenance_jobs`) with no per-row event to title, or a
#: customer act (`closed_from_your_word`, `changes_undone`) which is not
#: something that was decided FOR them.
NAMED_ACT_DOORS = {
    "unconfirmed_expired": "the review door",
    "rested_quiet": "the silence door",
    "let_go_quiet": "the silence door",
    "parked_quiet": "the quiet lane",
    "closed_from_calendar": "the calendar",
    "closed_from_meetings": "your meetings",
    "closed_from_deal": "a signed or paid record",
    "orgs_promoted": "the deal record",
    # FIX ROUND 1, THE WRAP SEAM (coordinator's call, 2026-09-14). DOORS1's
    # four-day retraction takes a whole PILE out of the offer queue at once
    # and writes one receipt per pile. It is decided for the reader and the
    # wrap said nothing about it, because this lane's named form is the ONE
    # producer the wrap and End of Day read. See `BATCH_ACT_DOORS`.
    "proposals_retracted": "the question door",
    # MF-11c-1 — a job's aged-out pass. The customer's own amnesty
    # (`cleared_amnesty`) is NOT here: it was not decided for them.
    "let_go_backlog": "a background pass",
}

#: The categories whose acts are BATCHES rather than rows, named one line per
#: batch off the job's own receipt. `changes_since` counts `proposals_retracted`
#: from the chip rail's per-row dismissals; DOORS1's supersede retraction
#: writes no per-row event at all — the receipt IS the act — so this reader
#: goes to the receipt and the generic per-seq loop skips the category.
BATCH_ACT_DOORS = ("proposals_retracted",)

#: The receipt DOORS1's retraction rides on, and the field that carries the
#: size of the pile. Spelled here because that code is on another branch;
#: the coordinator verifies the join on the trial-merged tree.
RETRACTION_RECEIPT_TYPE = "question_expiry_run"
RETRACTION_COUNT_FIELD = "n_retracted"

#: The batch line's own head. A batch act HAS to carry its count — there is
#: no row to name — so this is the one place a number leads. No `undo`
#: phrase: the retraction writes nothing, so there is nothing to reverse.
try:
    from question_ttl import RETRACTION_SENTENCE as _RETRACTION_SENTENCE
except Exception:  # pragma: no cover
    _RETRACTION_SENTENCE = ("Stopped offering {n} old {suggestions} that a "
                            "decision was reversed in a later meeting")

BATCH_ACT_TITLES = {
    # MF-6 / F-4: ONE constant, imported - the wrap and the maintenance chat
    # cannot describe one act two ways.
    "proposals_retracted": _RETRACTION_SENTENCE,
}

#: The named line. Title first — it is the only part a reader recognises.
NAMED_ACT_LINE = "{title} — {door}, {date}"

#: What a row with no resolvable title says. Never invented, never a wire id.

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


#: NUMBER1 3.9 — THE CONDITION OF THE MACHINERY, NAMED. These two
#: categories report the plumbing's own state rather than the customer's
#: work: "Ran the weekly cleanup pass." and "Completed N background
#: maintenance jobs on schedule." Under M's 2026-09-07 ruling and its
#: extension they leave the morning brief entirely and are reported by the
#: maintenance run, which is the only surface where a cleanup can actually
#: be offered and performed. They are NOT deleted here: a line reported
#: nowhere is a worse fix than the defect. `surface_drivers` drops them
#: from the brief's CHANGED strip and `skills/cleanup/SKILL.md` composes
#: them into the Monday note.
PLUMBING_CATEGORIES = ("cleanup_runs", "maintenance_jobs")


def plumbing_lines(feed: dict) -> list:
    """The feed's plumbing lines, for the maintenance report (NUMBER1 3.9).

    The mirror of what the customer surfaces drop, so the two can never
    disagree about which sentences those are.
    """
    return [str(l.get("text") or "") for l in (feed.get("lines") or [])
            if isinstance(l, dict)
            and l.get("category") in PLUMBING_CATEGORIES
            and l.get("text")]


def _and_list(items) -> str:
    """`a`, `a and b`, `a, b and c` — one composer (NUMBER1 3.8)."""
    items = [str(i) for i in items if str(i).strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _learned_nouns(skills) -> list:
    """What a change to each skill's voice is CALLED on a customer line
    (NUMBER1 3.8), deduped and in first-seen order.

    Read from `voice_corrections.VOICE_SURFACE_NOUNS` — A12's own
    vocabulary, the one place a skill id becomes customer words — so this
    sentence can never name a skill. A skill with no entry contributes
    nothing rather than its id, and a batch that resolves to no noun at
    all falls back to the counted sentence.
    """
    try:
        from voice_corrections import VOICE_SURFACE_NOUNS
    except Exception:  # pragma: no cover — vocabulary home unreadable
        return []
    out: list = []
    for skill in skills or ():
        noun = VOICE_SURFACE_NOUNS.get(str(skill) or "")
        if not noun:
            continue
        # "in your emails" -> "emails": the preposition belongs to the
        # sentence this list is being poured into, not to the item.
        for prefix in ("in your ", "in the ", "in "):
            if noun.startswith(prefix):
                noun = noun[len(prefix):]
                break
        if noun not in out:
            out.append(noun)
    return out


_PROMOTION_ON_RE = re.compile(r"\s+on\s+(\d{4}-\d{2}-\d{2}\S*)\s*$")


def _promotion_clause(workspace_root, data) -> str:
    """The promotion's OWN evidence, as a customer sentence (NUMBER1 3.7).

    `org_promotion` writes it (`"their deal was marked won"` for a
    `deal_won` reason, `"a paid or signed record"` otherwise) with the date
    appended in ISO. The clause is returned verbatim except for that date,
    which is re-rendered through this module's own date phrase so no
    surface prints a machine date. An event carrying no evidence returns
    `""` and the line makes no claim at all.
    """
    clause = str((data or {}).get("evidence") or "").strip()
    if not clause:
        return ""
    m = _PROMOTION_ON_RE.search(clause)
    if m:
        clause = (clause[:m.start()] + " on "
                  + _act_date(workspace_root, m.group(1)))
    return clause


def _title_index(events) -> dict:
    """commitment/decision id -> title, from ONE pass over the events already
    loaded. Mirrors `end_of_day._win_join_index`'s posture (batch, never a
    record read per event) at the one grain this reader needs."""
    idx: dict = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        title = str(data.get("title") or "").strip()
        if not title:
            continue
        for key in ("id", "commitment_id", "org_id"):
            val = data.get(key)
            if val:
                idx.setdefault(str(val), title)
        if ev.get("id"):
            idx.setdefault(str(ev["id"]), title)
    return idx


def _act_title(ev: dict, idx: dict) -> str:
    """This act's row title — read, then joined, never invented."""
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for key in ("title", "commitment_title", "name", "org_name"):
        val = str(data.get(key) or "").strip()
        if val:
            return val
    for key in ("commitment_id", "id", "org_id", "proposal_id"):
        val = data.get(key)
        if val and str(val) in idx:
            return idx[str(val)]
    # WRAPSTAFF1 fix round 3 (review N-2) — ONE call swap, by the
    # coordinator's ruling: the untitled fallback is the BOARD's, so a row
    # with no title on record reads the same words on the wrap as it does on
    # the plate. `plate_view.row_title()` with no candidate returns
    # `commitment_state.UNTITLED_PLACEHOLDER`, the tree's one such constant.
    from plate_view import row_title
    return row_title()


def _act_date(workspace_root, ts) -> str:
    """The act's date in the WORKSPACE's own day (DATE1), plain words."""
    from event_time import parse_ts

    dt = parse_ts(ts) if ts else None
    if dt is None:
        return "date not on record"
    try:
        from tz import localize_date
        local = localize_date(dt.isoformat(), workspace_path=workspace_root)
    except Exception:  # pragma: no cover — a tz read never breaks a reader
        local = None
    iso = str(local or dt.date().isoformat())[:10]
    # Spelled by hand rather than with `strftime("%b %-d")`: the no-pad
    # directive is platform-specific (`%-d` on POSIX, `%#d` on Windows) and a
    # date that renders differently on two of the fleet's machines is a date
    # two receipts disagree about.
    try:
        y, m, d = (int(x) for x in iso.split("-"))
        return "%s %d" % (_MONTHS[m - 1], d)
    except Exception:  # pragma: no cover
        return iso


def window_floor(workspace_root, since_ts, *, now_iso: Optional[str] = None):
    """The lower bound a NAMED read may use, never `None` (fix round 1,
    reviewer F-1).

    `changes_since` applies no lower bound when `since_ts` is unreadable,
    which is correct for its own callers (the brief's CHANGED strip asks
    "since the last fire" and an absent anchor there means "since forever" by
    design). It is NOT correct for a reader that offers `undo` on what it
    names: a day whose morning brief never fired has `since_ts is None`, and
    the day-close then named 481 acts going back to August with an undo offer
    against every one of them.

    So this borrows End of Day's own floor (`end_of_day._window` →
    workspace-local midnight, SPEC WINSFLOOR1) rather than spelling a second
    one. A floor that cannot be resolved returns `None`, and the caller drops
    the read — an unresolvable floor is not a licence to read everything.
    """
    if since_ts:
        return since_ts
    try:
        import end_of_day
        since, _source = end_of_day._window(workspace_root, None,
                                            now_iso=now_iso)
        return since.isoformat()
    except Exception:  # pragma: no cover — never widen the window
        return None


def decided_for_you(workspace_root, since_ts: str, *,
                    now_iso: Optional[str] = None,
                    max_acts: Optional[int] = None) -> dict:
    """SPEC SURFACEFIX1 5.6 / amendment W-3 — the machine's own acts in the
    window, NAMED: one line per act, `title — door, date`.

    Returns `{"acts": [{"title", "door", "date", "category", "ref", "line"},
    ...], "n": <acts named>, "n_total": <acts in window>, "lines": [str],
    "since_ts", "now"}`.

    Reversed acts never appear: the classification this reads (`changes_since`)
    folds them out — every closer door through `reversed_closer_positions`,
    every other act through `reversed_act_positions`, including the park and
    rest doors (fix round 1, reviewer F-4). That fold lives there and nowhere
    else, and this reader inherits it. Drop-empty — a window in which the
    machine decided nothing returns no acts and no lines, and the caller
    renders nothing.

    THE WINDOW HAS A FLOOR (fix round 1, reviewer F-1). A `None` `since_ts`
    floors to workspace-local midnight through `window_floor`; a floor that
    cannot be resolved returns nothing rather than everything.

    Read-only, stdlib only, never raises into a caller.
    """
    floored = window_floor(workspace_root, since_ts, now_iso=now_iso)
    if floored is None:
        return {"acts": [], "lines": [], "n": 0, "n_total": 0,
                "since_ts": since_ts, "now": now_iso}
    since_ts = floored
    feed = changes_since(workspace_root, since_ts, now_iso=now_iso)
    wanted: dict = {}
    for category, door in NAMED_ACT_DOORS.items():
        if category in BATCH_ACT_DOORS:
            continue          # named off its receipt, below — not per row
        for seq in _refs_for(feed, category):
            wanted[seq] = (category, door)
    events = _load_events(workspace_root)
    batch_acts = _batch_acts(events, since_ts, now_iso=feed.get("now"),
                             workspace_root=workspace_root)
    if not wanted and not batch_acts:
        return {"acts": [], "lines": [], "n": 0, "n_total": 0,
                "since_ts": since_ts, "now": feed.get("now")}
    idx = _title_index(events)
    acts: List[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        seq = ev.get("seq")
        if seq is None or seq not in wanted:
            continue
        category, door = wanted[seq]
        title = _act_title(ev, idx)
        date = _act_date(workspace_root, ev.get("ts"))
        acts.append({"title": title, "door": door, "date": date,
                     "category": category, "ref": seq,
                     "line": NAMED_ACT_LINE.format(title=title, door=door,
                                                   date=date)})
    acts.extend(batch_acts)
    n_total = len(acts)
    if max_acts is not None and max_acts >= 0:
        acts = acts[:max_acts]
    return {"acts": acts, "lines": [a["line"] for a in acts],
            "n": len(acts), "n_total": n_total,
            "since_ts": since_ts, "now": feed.get("now")}


def _undone_batch_ids(events) -> set:
    """Every batch id an `undo` has reversed, taken apart on the marker's own
    shape (`brain_undo.batch_id_from_ref`) rather than by substring — the
    same fold `question_ttl.decided_for_you_lines` takes."""
    try:
        from brain_undo import batch_id_from_ref
    except Exception:  # pragma: no cover
        def batch_id_from_ref(label):
            s = str(label or "")
            return s.split(":", 1)[1].strip() if ":" in s else s.strip()
    out: set = set()
    for ev in events or ():
        if not isinstance(ev, dict) or ev.get("type") != "brain_change_undone":
            continue
        ref = str((ev.get("data") or {}).get("batch_ref") or "")
        if ref:
            out.add(batch_id_from_ref(ref))
    return out


def _batch_acts(events, since_ts, *, now_iso=None, workspace_root=None) -> list:
    """The window's BATCH-level machine acts, one line per receipt.

    Today that is DOORS1's four-day retraction and nothing else: a pile of
    suggestions nobody answered stops being offered, one receipt per pile,
    and no per-row event is written — so the row-level reader above can see
    none of it and the wrap said nothing about an act that was decided for
    the reader. The line carries its own count because there is no row to
    name, and it carries NO `undo` phrase: nothing was written, so there is
    nothing to reverse.

    A receipt whose batch an `undo` has already reversed is skipped, the same
    fold the row-level acts take.

    Read-only, never raises into a caller.
    """
    try:
        from event_time import event_time as _event_time
        from event_time import parse_ts as _parse
    except Exception:  # pragma: no cover
        return []
    since = _parse(since_ts)
    until = _parse(now_iso) if now_iso else None
    undone = _undone_batch_ids(events)
    out: List[dict] = []
    for ev in events or ():
        if not isinstance(ev, dict) or ev.get("type") != RETRACTION_RECEIPT_TYPE:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        try:
            n = int(data.get(RETRACTION_COUNT_FIELD) or 0)
        except (TypeError, ValueError):  # pragma: no cover
            continue
        if n <= 0:
            continue
        batch = str(data.get("batch_id") or "").strip()
        if batch and batch in undone:
            continue
        when = _parse(_event_time(ev))
        if when is None:
            continue
        if since is not None and when <= since:
            continue
        if until is not None and when > until:
            continue
        door = NAMED_ACT_DOORS["proposals_retracted"]
        title = BATCH_ACT_TITLES["proposals_retracted"].format(
            n=n, suggestions=("suggestion" if n == 1 else "suggestions"))
        date = _act_date(workspace_root, ev.get("ts"))
        out.append({"title": title, "door": door, "date": date,
                    "category": "proposals_retracted", "ref": ev.get("seq"),
                    "n": n,
                    "line": NAMED_ACT_LINE.format(title=title, door=door,
                                                  date=date)})
    return out


def _refs_for(feed: dict, category: str) -> list:
    """The seqs `changes_since` recorded for one category — from the RENDERED
    line when it has one, so a category the feed suppressed (a fold emptied
    it, a cap dropped it) names nothing here either."""
    for row in (feed.get("lines") or []):
        if isinstance(row, dict) and row.get("category") == category:
            return [r for r in (row.get("refs") or []) if r is not None]
    return []


__all__ = ["changes_since", "decided_for_you", "window_floor",
           "NAMED_ACT_DOORS", "BATCH_ACT_DOORS", "BATCH_ACT_TITLES",
           "RETRACTION_RECEIPT_TYPE", "RETRACTION_COUNT_FIELD",
           "NAMED_ACT_LINE"]
