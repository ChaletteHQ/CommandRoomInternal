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


def _plural(n: int, singular: str, plural: Optional[str] = None) -> str:
    return singular if n == 1 else (plural or singular + "s")


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
        "facts_noted": 0,
        "cleanup_runs": 0, "maintenance_jobs": 0,
        "proposals_resolved": 0, "proposals_declined": 0,
        "proposals_expired": 0, "changes_undone": 0,
        "new_proposals": 0,
    }
    refs: dict[str, list] = {k: [] for k in counts}

    for ev in _load_events(workspace_root):
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
            if data.get("user_action") == "declined":
                counts["proposals_declined"] += 1
                refs["proposals_declined"].append(seq)
            else:
                counts["proposals_resolved"] += 1
                refs["proposals_resolved"].append(seq)
        elif etype == "brain_proposal_expired":
            counts["proposals_expired"] += 1
            refs["proposals_expired"].append(seq)
        elif etype == "brain_change_undone":
            counts["changes_undone"] += 1
            refs["changes_undone"].append(seq)

    lines: List[dict] = []

    def _line(category: str, text: str) -> None:
        lines.append({"text": text, "category": category,
                      "refs": [r for r in refs[category] if r is not None]})

    n = counts["closed_from_sent"]
    if n:
        _line("closed_from_sent",
              f"Closed {n} {_plural(n, 'commitment')} matched to your sent "
              f"mail — say `undo` to reopen any.")
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
