#!/usr/bin/env python3
"""apply-choices audit-event builder (T2.2 — FS-18a).

WHY THIS EXISTS
RV-5 caught a FALSE-SUCCESS audit: a `confirm proposal` whose handler refused
(nothing written) was logged `outcome: "ok"` in the `apply_choices_applied`
event — the batch reporter recorded the DISPATCH, not the WRITE. An audit
event that says "ok" over a refusal poisons every downstream reader
(usage-report, corrections passes, the change feed's honesty).

THE RULE (pinned in apply-choices Step 5): an action's `outcome` derives from
the HANDLER'S ACTUAL RESULT — the writer's return dict / raised error —
never from "the handler was called". This module mechanizes the derivation
so the mapping can't drift per session.

USAGE (apply-choices Step 5):

    from apply_audit import build_apply_choices_applied_event
    event = build_apply_choices_applied_event(
        source="cr-brain",
        actions=[{"n": n, "action": verb, "handler_result": result_or_error},
                 ...])
    # append via atomic_append_jsonl / event_gate (seq/ts auto-stamped)

`handler_result` per action is REQUIRED — one of:
  - the handler's return dict (its `status` field drives the outcome),
  - an Exception instance or its string (→ "error"),
  - None ONLY for pure no-write actions (skip/snooze acks) → "ok".

stdlib only.
"""
from __future__ import annotations

from typing import Any, List

# status strings that mean "the write landed" (the handlers' own vocabulary).
_OK_STATUSES = frozenset({
    "ok", "closed", "resolved", "applied", "added", "created", "updated",
    "proposed", "archived", "undone", "reopened", "cleared", "dismissed",
    "snoozed", "sent", "drafted", "registered", "merged", "reassigned",
    "split", "promoted",
    # WATCHGATE: a weak proposal answered by a bulk gesture is PARKED on
    # watch rather than closed. That is a write that landed and a row that
    # has been dealt with — the page-set should stop offering it, exactly as
    # it stops offering a closed one.
    "watching",
})
# statuses that mean "nothing needed writing" — honest no-ops, counted apart.
_NOOP_STATUSES = frozenset({
    "already_resolved", "already_closed", "already_inactive", "duplicate_open",
    "suppressed_cooldown", "already_merged", "noop",
    # WATCHGATE: the row was already parked, so this answer wrote nothing.
    # An honest no-op, not a failure and not a second park.
    "already_watching",
    # ARCHFIX: thread_archive.archive_thread on an already-archived thread
    # writes nothing — no second status_change. Without this row the honest
    # no-op maps to the unknown-status default ("error"), inflating n_errors
    # and leaving an archived project still offered in the page-set.
    "already_archived",
})
# statuses that mean the handler REFUSED or could not complete the write.
_REFUSED_STATUSES = frozenset({
    "error", "refused", "failed", "needs_confirm", "blocked", "invalid",
    # WATCHGATE / BULKGUARD holds: the handler deliberately declined to write
    # because the row could not be proved. Named explicitly rather than left
    # to the unknown-status default, so the reason is legible in the source
    # instead of inferred from a fall-through.
    "held_weak_evidence", "held_pending_review", "confirmed_open",
})


def derive_outcome(handler_result: Any) -> str:
    """Map a handler's actual result to the audit outcome enum
    ("ok" | "already_resolved" | "error").

    - Exception (instance or string that looks like one) → "error".
    - dict: `status` in the OK set → "ok"; no-op set → "already_resolved";
      refused set → "error". An UNKNOWN status maps to "error" — when the
      reporter can't prove the write landed, it must not claim it did
      (FS-18a is exactly the optimistic default).
    - None → "ok" (reserved for pure no-write actions).
    """
    if handler_result is None:
        return "ok"
    if isinstance(handler_result, BaseException):
        return "error"
    if isinstance(handler_result, str):
        return "error"  # a bare string result is an error message by convention
    if isinstance(handler_result, dict):
        status = str(handler_result.get("status") or "").lower()
        if status in _OK_STATUSES:
            return "ok"
        if status in _NOOP_STATUSES:
            return "already_resolved"
        if status in _REFUSED_STATUSES:
            return "error"
        return "error"  # unknown status: never optimistic
    return "error"


def build_apply_choices_applied_event(*, source: str, actions: List[dict]) -> dict:
    """Build the ONE `apply_choices_applied` audit event for a batch. Each
    entry in `actions` is {n, action, handler_result} (+ optional
    `note`). Outcomes derive from handler_result via derive_outcome — the
    caller cannot pass an outcome directly (that's the FS-18a hole).

    Returns the event dict WITHOUT seq/ts (the append gate auto-stamps).
    """
    rows = []
    n_errors = 0
    for a in actions:
        # Review F-1: an entry that OMITS handler_result entirely is a
        # reporter bug — the caller never captured what the handler did, so
        # the outcome cannot be claimed "ok". Only an EXPLICIT None (a pure
        # no-write ack) maps to "ok"; the absent key maps to "error".
        if "handler_result" not in a:
            outcome = "error"
        else:
            outcome = derive_outcome(a.get("handler_result"))
        if outcome == "error":
            n_errors += 1
        row = {"n": a.get("n"), "action": a.get("action"), "outcome": outcome}
        if a.get("note"):
            row["note"] = str(a["note"])[:200]
        rows.append(row)
    return {
        "type": "apply_choices_applied",
        "source_skill": "apply-choices",
        "data": {
            "source": source,
            "n_choices": len(actions),
            "actions": rows,
            "n_errors": n_errors,
        },
    }


__all__ = ["derive_outcome", "build_apply_choices_applied_event"]
