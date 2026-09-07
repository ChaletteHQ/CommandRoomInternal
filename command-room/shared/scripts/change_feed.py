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


def _transcript_closes_on(workspace_root) -> bool:
    """CUT-A — the switch, through its one reader; unreadable is OFF."""
    try:
        from commitment_policy_pass import _closes_enabled
        return _closes_enabled(workspace_root) is True
    except Exception:  # pragma: no cover
        return False


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
        "changes_undone": 0,
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
        "proposals_retracted": 0,
        # CUT-A (R-A) — while closing on evidence is OFF, the promises a
        # meeting shows were kept land as review proposals instead of
        # closes; ONE line counts them from the written proposals.
        "kept_in_meetings_proposed": 0,
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
    # POLICY1-B DD-10 — the meetings line narrates per GROUP: one entry per
    # meeting under the fire (`undo_group`), each with the group batch id
    # a person can name (`undo 1a`), attached to the line as `groups`.
    meeting_groups: dict = {}

    for pos, ev in enumerate(events):
        if not _in_window(ev):
            continue
        etype = ev.get("type")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        seq = ev.get("seq")

        if etype == "sent_reconcile":
            n = data.get("n_closed") or 0
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
            # DEALNAG1 (M ruling 4) — the automatic prospect -> client
            # promotion gets ONE line here and nowhere else: it is not a
            # decision to route to a chat, so it is never a queue row, never
            # a question, and never repeated. Narrated from the WRITTEN
            # promotion event (the PID1 receipt-honesty rule — the event IS
            # the receipt), refs traceable, undo standing.
            counts["orgs_promoted"] += 1
            refs["orgs_promoted"].append(seq)
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
            counts["changes_undone"] += 1
            refs["changes_undone"].append(seq)
        elif etype == "commitment_resolved":
            # UNCONFEXP1 — one counted line for the unconfirmed-pile expiry
            # (COVERQUIET1 posture: speaks only when N>0, and the standing
            # `undo` rides in the sentence because the batch is one gesture
            # away). Counts every closure stamped with the canonical lapse
            # reason — the scheduled daily drain and a hand-typed `review
            # amnesty` write the identical event, and they are the same fact
            # to the reader: captures lapsed unanswered.
            if (str(data.get(_RRK) or "").strip().lower() == _RER):
                counts["unconfirmed_expired"] += 1
                refs["unconfirmed_expired"].append(seq)
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
                counts["parked_quiet"] += 1
                refs["parked_quiet"].append(seq)
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
    n = counts["orgs_promoted"]
    if n:
        _line("orgs_promoted",
              f"Promoted {n} {_plural(n, 'prospect')} to client — they had "
              f"a paid or signed record on file; say `undo` to put "
              f"{'them' if n > 1 else 'it'} back.")
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
        _line("unconfirmed_expired",
              f"Closed {n} stale unconfirmed "
              f"{_plural(n, 'extraction')} nobody answered — say `undo` "
              f"to put {'it' if n == 1 else 'them'} back.")
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


__all__ = ["changes_since"]
