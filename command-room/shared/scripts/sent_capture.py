#!/usr/bin/env python3
"""
Sent-mail commitment capture — deterministic half of the email-SENT leg
(v4.6.2, BUG-3719).

WHY THIS EXISTS
---------------
The operator's own outbound email promises had NO capture path (BUG-3719,
critical): inbox-triage's extractor covers both directions but only ever sees
`is:unread in:inbox` threads — a thread the operator read and replied to the
same day was never a candidate, so a promise made in that reply was never
scanned. reconcile-sent scans Sent daily but only CLOSED commitments — a
promise never logged can never be reconciled. The Slack leg (v4.6.0 MC3, `slack_capture.classify_direction`)
already treats the user's own sent messages as the PRIMARY promise source;
email was the only channel where the user's own promises were second-class.
This module mirrors that pattern for Gmail/Outlook Sent.

Division of labor (same as the Slack leg): the SKILL does the semantic
extraction — it reads the Step-2 Sent fetch, applies the Stage-D capture
floor (owner + deliverable + consequence) and COMMITMENT_SCHEMA.md's
extraction triggers, and decides what is a real commissive. This module does
the parts that must be exact:

  1. The capture block — `build_sent_commitment_event` runs THE shared
     Stage-D / S2 / Stage-E gate (`capture_gate.gate_commitment_data`) every
     capture writer runs: required `kind` classified at extraction,
     due-or-no_due with silence rejected, task-with-counterparty rejected,
     counterparty receipts joined into person_ids, and the pending_review
     safety inversion. Owner is ALWAYS the resolved primary user — this leg
     captures only the user's OWN outbound promises (inbound asks are
     inbox-triage's lane; direction is fixed by the surface itself).
  2. Cross-channel restatement dedup — `capture_gate.matches_open_commitment`
     (corroboration-style: shared non-user party + content-token overlap)
     against the OPEN set, so a sent email restating a meeting-sourced or
     triage-sourced commitment MERGES into the item that already tracks it
     instead of double-tracking (the case study's rec #4).
  3. Idempotency — `already_captured` codifies the scan's Step-4 dedup rule
     ((source_ref, title) — case-insensitive first-60-chars) with
     `gmail:<message_id>` as the per-message source_ref anchor, so re-running
     the sent pass never re-captures the same message. The restatement match
     is the second layer (catches a re-extract whose title drifted).
  4. The relevance gate — items route through W4c's `classify_capture`
     (caution rail > org override > mode). The user is a party to every own
     promise, so under party-only these OPEN — an org-level observed-only
     override still routes them set-aside, and dated/money items always open.
  5. The orchestration — `capture_sent_items` runs 1–4 over a batch and
     appends through `event_gate.append_event` (ONE locked call), returning
     a summary receipt. Per-item gate failures are collected LOUDLY into
     `summary["errors"]` (never silently dropped) rather than crashing the
     whole silent scheduled fire — reconcile-sent folds the counts into its
     `sent_reconcile` audit event, so a bad extraction is visible in the
     substrate, and the closes that already landed stay landed.

Events omit `seq` (auto-stamped in the writer lock); `ts` is backdated to the
message's send time (schema: ts is when the commitment was MADE). No parent
event exists for sent mail (nothing writes `interaction` events for outbound
threads — that gap IS this bug), so like the Slack leg there is no
`source_event_seq`; the `gmail:<message_id>` source_ref is the provenance.

Callers: reconcile-sent (via `reconcile_sent_commitments.reconcile_and_receipt
(..., sent_commitment_items=...)`, the daily rescue path) and
scan-for-commitments' outbound/Sent pass (the historical backfill path,
`append=False` — the scan folds the built events into its own single batch).

stdlib only; nothing here calls the network.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from capture_gate import (  # noqa: E402
    build_observed_event,
    classify_capture,
    gate_commitment_data,
    matches_open_commitment,
    observed_from_commitment_event,
    resolve_capture_mode,
    workspace_capture_context,
)

_TITLE_DEDUP_CHARS = 60  # scan-for-commitments Step 4: first 60 chars, ci


class SentItemError(ValueError):
    """An extracted sent-mail item was malformed — fail loud so a bad
    extraction is visible and goes back to the extractor, never silently
    dropped or silently written wrong (the F-31 bug class; SweepItemError /
    SlackItemError's sibling)."""


def sent_source_ref(message_id: str) -> str:
    """Provenance ref for a sent-mail capture: `gmail:<message_id>` — the
    spelling COMMITMENT_SCHEMA.md reserves for email artifacts. The message
    id is the connector's canonical per-message id (stable across re-fetch),
    so it is both the dedup anchor and traceable back to the send."""
    mid = (message_id or "").strip()
    if mid.startswith("gmail:"):
        mid = mid[len("gmail:"):].strip()
    if not mid:
        raise SentItemError("a sent-mail capture needs the message id")
    return f"gmail:{mid}"


def _parse_iso_date(value) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        _dt.date.fromisoformat(value.strip()[:10])
        return True
    except ValueError:
        return False


def build_sent_commitment_event(
    title: str,
    *,
    message_id: str,
    kind: str,
    user_person_id: str,
    ts: str = "",
    due: Optional[str] = None,
    no_due: bool = False,
    counterparty_id: Optional[str] = None,
    counterparty_name: Optional[str] = None,
    evidence: str = "",
    primary_thread_id: Optional[str] = None,
    person_ids: Optional[List[str]] = None,
    classification_confidence: Optional[float] = None,
    pending_review: bool = False,
    review_reason: str = "",
    source_skill: str = "reconcile-sent",
) -> dict:
    """One qualifying sent message → one canonical `commitment` event dict,
    with the full capture block enforced in code (Stage-D kind, S2 due-nudge,
    Stage-E counterparty receipts, pending_review inversion). Construction
    only — append through `event_gate.append_event` (ids minted and seq
    stamped inside the writer lock; C4's semantic dedup fires there).

    OWNER IS ALWAYS THE USER: this leg exists for the user's own outbound
    promises, so `owner_id` is stamped from `user_person_id` — which MUST be
    the `resolve_primary_user` result, never a guess (Bug #102: a wrong/None
    user makes every downstream owner gate match nothing). An empty user id
    fails loud here rather than minting an ownerless promise.

    Resolve relative due phrases ("next week", "by Friday") against the
    MESSAGE's send date, not the scan date, before calling this.

    Raises SentItemError on anything the extraction must go back and do.
    """
    title = (title or "").strip()
    if not title:
        raise SentItemError("a sent-mail commitment needs a non-empty title")
    source_ref = sent_source_ref(message_id)
    if not (user_person_id or "").strip():
        raise SentItemError(
            f"sent-mail commitment {source_ref} has no resolved user — "
            f"resolve via resolve_primary_user (Bug #102), never guess; "
            f"a sent-mail capture is by definition the user's own promise"
        )

    due_str = (due or "").strip()
    data: dict = {
        "title": title,
        "kind": kind,
        "due": due_str,
        "owner_id": user_person_id,
        "source_ref": source_ref,
        "channel": "email",
        # No parent event exists for a sent message (nothing writes outbound
        # interaction events — that gap IS BUG-3719); like the Slack leg, the
        # source_ref is the provenance and there is no source_event_seq.
    }
    if no_due:
        data["no_due"] = True
    if counterparty_id:
        data["counterparty_id"] = counterparty_id
    if counterparty_name and not counterparty_id:
        data["counterparty_name"] = counterparty_name
    if evidence:
        data["evidence"] = evidence[:200]
    if pending_review:
        data["pending_review"] = True
        if review_reason:
            data["review_reason"] = review_reason

    # THE shared capture block (v4.6.1 W4c consolidation — one implementation
    # for every writer): Stage-D kind, S2 due-nudge, the promise-vs-task rule,
    # and the pending_review safety inversion (absence of the flag is not
    # consent; never unsets an extractor-set True).
    gate_commitment_data(
        data,
        subject=f"sent-mail commitment {source_ref}",
        classification_confidence=classification_confidence,
        error_cls=SentItemError,
    )

    data["status"] = "open"
    if due_str and _parse_iso_date(due_str):
        if _dt.date.fromisoformat(due_str[:10]) < _dt.datetime.now(
            _dt.timezone.utc
        ).date():
            data["status"] = "overdue"

    # Stage E: a resolved counterparty is also a person reference — the
    # dual-layer reader links via person_ids.
    pids = [p for p in (person_ids or []) if p]
    if user_person_id not in pids:
        pids.append(user_person_id)
    if counterparty_id and counterparty_id not in pids:
        pids.append(counterparty_id)

    event: dict = {
        "type": "commitment",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "person_ids": pids,
        "data": data,
    }
    if classification_confidence is not None:
        event["classification_confidence"] = classification_confidence
    if (ts or "").strip():
        event["ts"] = ts.strip()  # backdate to when the promise was made
    return event


def _title_key(title) -> str:
    return (str(title or "").strip().lower())[:_TITLE_DEDUP_CHARS]


def already_captured(workspace_root, message_id: str, title: str) -> bool:
    """Step-4 idempotency for the sent leg, codified: True when a commitment
    with the same `gmail:<message_id>` source_ref AND the same title (ci,
    first 60 chars — the scan's documented rule) is already on disk, OR the
    source_ref is already covered by a `commitment_resolved` /
    `thread_resolved` event. Shard-transparent via events_io. This keys on
    the scan's own (source_ref, title) rule only — the restatement match
    (`capture_gate.matches_open_commitment`) and C4's semantic append-path
    layer run separately."""
    try:
        from events_io import iter_events
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(_HERE))
        from events_io import iter_events

    ref = sent_source_ref(message_id)
    want_title = _title_key(title)
    for ev in iter_events(workspace_root):
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        ev_ref = str(data.get("source_ref") or "").strip()
        if ev_ref != ref:
            continue
        etype = ev.get("type")
        if etype in ("commitment_resolved", "thread_resolved"):
            return True
        if etype == "commitment":
            ev_title = _title_key(data.get("title") or data.get("summary"))
            if ev_title and want_title and (
                ev_title in want_title or want_title in ev_title
            ):
                return True
    return False


def capture_sent_items(
    workspace_root,
    items,
    *,
    user_person_id,
    opens=None,
    source_skill: str = "reconcile-sent",
    append: bool = True,
) -> dict:
    """Run the full sent-capture pipeline over a batch of SKILL-extracted
    commissives and (when `append=True`) land the survivors in ONE locked
    append. The BUG-3719 fix's write path.

    `items` — extraction dicts from the skill's semantic pass, one per
    commissive found in the user's own sent messages:
      {"message_id", "ts" (send-time ISO), "title", "kind",
       "due" | "no_due": True, "counterparty_id" | "counterparty_name",
       "evidence", "org_id"/"org_name" (the recipient's resolved org, for the
       per-org override), "primary_thread_id", "person_ids",
       "classification_confidence", "pending_review"/"review_reason"}

    Per item: (1) `already_captured` — a message already on disk is skipped
    (idempotent re-runs); (2) the capture block via
    `build_sent_commitment_event` — a malformed item lands LOUDLY in
    `errors`, never silently dropped and never crashing the batch (this runs
    inside a silent scheduled fire whose closes have already landed);
    (3) restatement dedup vs the OPEN set + earlier items in this batch
    (`matches_open_commitment`, user excluded from the party test) — a
    cross-channel restatement records as `merged`, no write; (4) W4c
    relevance routing (`classify_capture` with the org override resolved per
    item) — observed-tier items convert via `observed_from_commitment_event`.

    `opens` — pass a pre-loaded `load_open_commitments` projection to pin the
    dedup baseline (reconcile-sent passes its PRE-close projection so a
    restatement merges into its original even when this same fire just closed
    it); None loads fresh.

    Returns {"ran": True, "opened", "merged", "observed", "skipped_existing",
    "errors", "n_opened", "n_merged", "n_observed", "n_skipped", "n_errors",
    "events"} — `events` carries the built dicts when `append=False` (the
    scan folds them into its own single batch + preview).
    """
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if opens is None:
        from cru_match import load_open_commitments

        opens = load_open_commitments(str(events_path))

    ctx = workspace_capture_context(workspace_root)
    user_names = ctx.get("user_names") or []

    opened, merged, observed, skipped, errors = [], [], [], [], []
    batch: list = []
    accepted_open_events: list = []  # in-batch restatement guard

    for item in items or []:
        if not isinstance(item, dict):
            continue
        mid = item.get("message_id") or ""
        title = item.get("title") or ""
        try:
            if already_captured(workspace_root, mid, title):
                skipped.append({"title": title, "message_id": mid})
                continue
            ev = build_sent_commitment_event(
                title,
                message_id=mid,
                kind=item.get("kind"),
                user_person_id=user_person_id,
                ts=item.get("ts") or "",
                due=item.get("due"),
                no_due=bool(item.get("no_due")),
                counterparty_id=item.get("counterparty_id"),
                counterparty_name=item.get("counterparty_name"),
                evidence=item.get("evidence") or "",
                primary_thread_id=item.get("primary_thread_id"),
                person_ids=item.get("person_ids"),
                classification_confidence=item.get("classification_confidence"),
                pending_review=bool(item.get("pending_review")),
                review_reason=item.get("review_reason") or "",
                source_skill=source_skill,
            )
        except SentItemError as e:
            errors.append({"title": title, "message_id": mid, "error": str(e)})
            continue

        match = matches_open_commitment(
            ev["data"],
            list(opens) + accepted_open_events,
            person_ids=ev.get("person_ids") or (),
            exclude_party_ids={user_person_id},
            exclude_party_names=user_names,
        )
        if match is not None:
            md = match.get("data") or {}
            merged.append({
                "title": title,
                "message_id": mid,
                "merged_into_id": md.get("id") or match.get("seq"),
                "merged_into_title": md.get("title") or md.get("summary") or "",
            })
            continue

        tier = classify_capture(
            ev["data"],
            mode=resolve_capture_mode(
                workspace_root,
                org_id=item.get("org_id"),
                org_name=item.get("org_name"),
            ),
            user_id=user_person_id,
            user_names=user_names,
            team_ids=ctx.get("team_ids") or (),
            known_ids=ctx.get("known_ids") or (),
        )
        if tier.get("tier") == "observed":
            obs = observed_from_commitment_event(ev, reason=tier.get("reason") or "")
            batch.append(obs)
            observed.append({"title": title, "message_id": mid,
                             "reason": tier.get("reason") or ""})
        else:
            batch.append(ev)
            accepted_open_events.append(ev)
            opened.append({
                "title": title,
                "message_id": mid,
                "kind": ev["data"].get("kind"),
                "due": ev["data"].get("due") or "",
                "pending_review": bool(ev["data"].get("pending_review")),
            })

    if append and batch:
        from event_gate import append_event

        append_event(events_path, batch, holder=source_skill)

    out = {
        "ran": True,
        "opened": opened,
        "merged": merged,
        "observed": observed,
        "skipped_existing": skipped,
        "errors": errors,
        "n_opened": len(opened),
        "n_merged": len(merged),
        "n_observed": len(observed),
        "n_skipped": len(skipped),
        "n_errors": len(errors),
    }
    if not append:
        out["events"] = batch
    return out


__all__ = [
    "SentItemError",
    "sent_source_ref",
    "build_sent_commitment_event",
    "already_captured",
    "capture_sent_items",
]
