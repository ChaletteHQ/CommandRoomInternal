#!/usr/bin/env python3
"""TESTMEET1 (cr1#1295) - test meetings are not real work.

WHAT THIS IS FOR
----------------
A meeting someone holds to try the product out ("Sample sync (test meeting,
safe to delete)", attended only by an address on example.com) used to be
captured like any other: its promises landed on the owner's book and topped
the Today line. The owner's ruling of 2026-11-16 is two halves:

  1. capture skips a meeting that is MARKED as a test in its title, and a
     meeting whose only other attendees are on reserved test domains
     (example.com, example.net, example.org and their subdomains, and the
     reserved names .test, .example, .invalid, .localhost);
  2. anything already captured from such a meeting is retired on the next
     maintenance.

`meeting_test_verdict` is the one test both halves use, so the capture door
(`meeting_discovery.dedup_meetings`, action `skip_test`) and the retire pass
(`retire_test_meeting_captures`, run inside the daily `review-expiry`
maintenance job) can never disagree about which meetings are tests.

WHAT COUNTS AS MARKED, AND WHY SO NARROW
----------------------------------------
A real meeting can be ABOUT testing ("Pen test meeting with the IT firm",
"Testing the new intake form"), so the word alone is not a mark. A title is
marked only when:

  * a bracketed or parenthesised group is just "test", or opens with
    "test meeting" or "test," ("(test)", "[TEST]", "(test meeting, safe to
    delete)"), never "(test drive)" or "[test env]";
  * it says "safe to delete";
  * it starts with "test:".

THE DOMAIN HALF
---------------
Only addresses decide it. The owner's own addresses are set aside first
("other attendees"). When the owner's own address is itself on a reserved
domain the workspace is a sample brain, and the domain half does not apply
there (the title half still does); nor does it when the owner's address
cannot be read at all. The meeting is a test when at least one other attendee
remains and EVERY one of them carries an address on a reserved domain. An
attendee known only by name has no domain to read, so it counts as a real
person and the meeting is NOT a test: when in doubt, capture.

WHAT RETIRING MEANS
-------------------
Every OPEN commitment whose source pointer names a test meeting closes as
`dropped` with `resolution_reason: test_meeting_retired`, through the one
closure path (`commitment_backlog_sweep._close_rows`, which carries the undo
stamps), in one batch, stamped as the machine's own act. One `undo` puts the
batch back. The meeting record, its decisions and its people are left as they
are: nothing is deleted.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

RESERVED_DOMAINS = ("example.com", "example.net", "example.org")
RESERVED_TLDS = ("test", "example", "invalid", "localhost")

REASON_TITLE = "the title marks it as a test"
REASON_DOMAINS = "every other attendee is on a reserved test domain"

RETIRE_SOURCE_SKILL = "maintenance:test-meeting-retire"
RETIRE_EVIDENCE = ("captured from a test meeting - retired by maintenance "
                   "(cr1#1295)")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-']+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
_TITLE_MARKS = (
    # "(test)" / "[TEST]" alone, or a group opening "test meeting" / "test,"
    re.compile(r"[\(\[]\s*test\s*(?:[\)\]]|,|meeting\b)", re.IGNORECASE),
    re.compile(r"\bsafe to delete\b", re.IGNORECASE),
    re.compile(r"^\s*test\s*:", re.IGNORECASE),
)


def is_reserved_domain(domain) -> bool:
    """True for example.com / .net / .org and their subdomains, and for any
    name under the reserved .test / .example / .invalid / .localhost."""
    d = str(domain or "").strip().lower().rstrip(".")
    if not d:
        return False
    if any(d == r or d.endswith("." + r) for r in RESERVED_DOMAINS):
        return True
    return d.rsplit(".", 1)[-1] in RESERVED_TLDS


def title_marks_test(title) -> bool:
    """True when the title itself says the meeting is a test (see module
    docstring for the three marks, and why the word alone is not one)."""
    text = str(title or "")
    return any(p.search(text) for p in _TITLE_MARKS)


def _email_of(attendee) -> str:
    if isinstance(attendee, dict):
        attendee = attendee.get("email") or attendee.get("name") or ""
    m = _EMAIL_RE.search(str(attendee or ""))
    return m.group(0).lower() if m else ""


def meeting_test_verdict(title, attendees: Iterable = (), *,
                         user_emails: Iterable = ()) -> dict:
    """`{"test": bool, "reason": str}` for one meeting.

    `attendees` is any mix of address strings, "Name <address>" strings,
    plain names and `{"name", "email"}` dicts. `user_emails` are the owner's
    own addresses, set aside before the domain half is read."""
    if title_marks_test(title):
        return {"test": True, "reason": REASON_TITLE}
    mine = {str(e or "").strip().lower() for e in user_emails or () if e}
    if not mine or any(is_reserved_domain(e.rsplit("@", 1)[-1]) for e in mine):
        # The domain half needs the owner's own address, and a real one. An
        # owner on a reserved domain is a sample brain, where every attendee
        # is on one too; an owner whose address cannot be read might be one.
        # Either way only the title half applies.
        return {"test": False, "reason": ""}
    others: List[str] = []
    for a in attendees or ():
        email = _email_of(a)
        if email and email in mine:
            continue
        if not email:
            if str((a.get("name") if isinstance(a, dict) else a) or "").strip():
                return {"test": False, "reason": ""}
            continue
        others.append(email)
    if others and all(is_reserved_domain(e.rsplit("@", 1)[-1]) for e in others):
        return {"test": True, "reason": REASON_DOMAINS}
    return {"test": False, "reason": ""}


def user_emails_of(workspace_root) -> List[str]:
    """The owner's own addresses, lowercased; [] when none can be read."""
    if not workspace_root:
        return []
    try:
        from email_outcomes import _resolve_user_emails
        return [str(a).strip().lower()
                for a in _resolve_user_emails(workspace_root) if a]
    except Exception:
        return []


def marked_test_meetings(workspace_root, *, user_emails=None) -> List[dict]:
    """Every `meeting` record in the book that is a test meeting:
    `[{"source_ref", "reason"}]`, in book order. Read only."""
    from connector_adapters.provenance import dedup_key_of
    from events_io import iter_events
    path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not path.exists():
        return []
    mine = user_emails if user_emails is not None else user_emails_of(workspace_root)
    out: List[dict] = []
    seen = set()
    for ev in iter_events(str(path)):
        if not isinstance(ev, dict) or ev.get("type") != "meeting":
            continue
        data = ev.get("data") or {}
        ref = str(data.get("source_ref") or "").strip()
        key = dedup_key_of(ref) if ref else ""
        if not key or key in seen:
            continue
        attendees = list(data.get("attendees") or []) + list(
            data.get("attendees_external") or [])
        verdict = meeting_test_verdict(data.get("title"), attendees,
                                       user_emails=mine)
        if verdict["test"]:
            seen.add(key)
            out.append({"source_ref": ref, "reason": verdict["reason"]})
    return out


def retire_plan(workspace_root) -> dict:
    """The open commitments captured from test meetings. Read only.

    `{"rows": [{"commitment_id", "title", "owner_id", "primary_thread_id",
    "pending", "source_ref"}], "meetings": [...]}`, sub-items first so a
    parent never refuses to close over a child of its own."""
    from cru_match import (_commitment_field, _commitment_id,
                           load_open_commitments, partition_subitems)
    from meeting_capture import meeting_ref_keys
    meetings = marked_test_meetings(workspace_root)
    wanted = set()
    for m in meetings:
        wanted |= meeting_ref_keys(m["source_ref"])
    if not wanted:
        return {"rows": [], "meetings": meetings}
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    opens = load_open_commitments(str(events_path),
                                  workspace_root=workspace_root)
    tops, subs = partition_subitems(opens)
    rows: List[dict] = []
    for ev in list(subs) + list(tops):
        ref = str(_commitment_field(ev, "source_ref") or "").strip()
        if not ref or not (meeting_ref_keys(ref) & wanted):
            continue
        rows.append({
            "commitment_id": _commitment_id(ev),
            "title": _commitment_field(ev, "title") or "",
            "owner_id": _commitment_field(ev, "owner_id") or "",
            "primary_thread_id": ev.get("primary_thread_id") or "",
            "pending": bool(_commitment_field(ev, "pending_review")),
            "source_ref": ref,
        })
    return {"rows": rows, "meetings": meetings}


def retire_test_meeting_captures(workspace_root, *, apply: bool = False,
                                 batch_id: Optional[str] = None,
                                 now_iso=None) -> dict:
    """Retire what test meetings left open, in ONE undoable batch.

    `apply=False` plans only. Returns `{"n_planned", "n_retired",
    "n_refused", "n_meetings", "batch_id"}`; never raises (a failure here is
    loud on stderr and must not take the maintenance job down)."""
    out = {"n_planned": 0, "n_retired": 0, "n_refused": 0,
           "n_meetings": 0, "batch_id": None}
    try:
        plan = retire_plan(workspace_root)
        rows = plan["rows"]
        out["n_planned"] = len(rows)
        out["n_meetings"] = len(plan["meetings"])
        if not apply or not rows:
            return out
        from commitment_backlog_sweep import _close_rows, _mint_batch_id
        from event_types import MACHINE, TEST_MEETING_REASON
        batch_id = batch_id or _mint_batch_id(now_iso)
        out["batch_id"] = batch_id
        results: list = []
        # Two calls, one batch: an unconfirmed capture needs the permission
        # flag to close at all, a confirmed one must never carry it.
        for pending in (True, False):
            # `resolved_by` stays empty on purpose: the batch closer does not
            # forward `actor`, and a person id there with the permission flag
            # would stamp the machine's own retire as the owner's act.
            part = [dict(r, evidence=RETIRE_EVIDENCE, owner_id="")
                    for r in rows if r["pending"] is pending]
            if not part:
                continue
            results += _close_rows(
                workspace_root, part, batch_id=batch_id,
                source_skill=RETIRE_SOURCE_SKILL, resolution="dropped",
                extra={"resolution_reason": TEST_MEETING_REASON},
                user_confirmed=pending, actor=MACHINE)
        out["n_retired"] = sum(1 for r in results
                               if r.get("status") == "closed")
        out["n_refused"] = len(rows) - out["n_retired"]
    except Exception as exc:  # loud, never fatal
        print(f"[test-meeting] retire FAILED: {type(exc).__name__}: {exc}",
              file=sys.stderr)
    return out


__all__ = [
    "RESERVED_DOMAINS", "RESERVED_TLDS", "REASON_TITLE", "REASON_DOMAINS",
    "is_reserved_domain", "title_marks_test", "meeting_test_verdict",
    "user_emails_of", "marked_test_meetings", "retire_plan",
    "retire_test_meeting_captures",
]
