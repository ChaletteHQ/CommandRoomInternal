"""Inbound-mail → open-commitment reconciliation (REPLYCLOSE, 2026-07).

WHY THIS EXISTS
---------------
M ruled 2026-07-29 that a counterparty's reply should close a "waiting on X"
item. Two scheduled fires already FETCH inbound mail and already run
`cru_match` Path 4 with closure power — inbox-triage's Phase 5.5 and the daily
Commitments/Waiting-On backstop's Phase 2.6 — but both did the whole job as
hand-rolled prose: load, match, close, append, per message, at the call site.
That is precisely the shape Bug #98-v3 diagnosed on the sent rail:

  * seven manual steps means seven places to half-do it,
  * nothing writes a verifiable trace, so a fire that silently did nothing is
    indistinguishable from a fire that found nothing,
  * the closures carry no batch stamp, so `undo` cannot list them, and
  * the fence parameters `cru_match` exposes are simply never passed.

This module is the missing ORCHESTRATOR — the inbound mirror of
`reconcile_sent_commitments.reconcile_and_receipt`. One call takes the batch of
inbound messages the skill already fetched and does everything else: match,
close through THE closure path, persist the mid-confidence proposals, record
the schedule-shift updates, and emit ONE `inbound_reconcile` audit event whose
fields exist only because the work ran.

WHAT IS DELIBERATELY *NOT* MIRRORED FROM THE SENT RAIL
-----------------------------------------------------
**FS-11 does not apply here.** On the sent rail an unambiguous moderate match
is auto-closed, because M ruled twice "if they are closed, just close them"
about mail HE sent — his own outbound act is the fulfillment. Inbound is
somebody else's message about somebody else's work, and REPLYCLOSE §2.2 is
explicit that a non-thread inbound match proposes at best. So a
`pending_review` here stays a `pending_review`; nothing is promoted.

**No cursor.** Both callers already own their own window (inbox-triage scans
the threads it fetched; the Commitments backstop scans since its last fire), and
a second cursor would be a second source of truth for the same window with no
reader to reconcile them. The audit event's timestamp plus
`validate_inbound_reconcile_ran` is the run trace instead.

INPUT SHAPE (inbound_messages)
  [{"message_id": str, "ts": iso-str, "sender_person_id": str,
    "subject": str|None, "body": str|None,
    "thread_id": str|None,                  # REPLYCLOSE R1 — the thread anchor
    "has_attachment": bool}, ...]           # REPLYCLOSE — the fulfillment shape
`thread_id` and `has_attachment` are optional and absent → the behavior that
field enables simply does not fire (never a guess). A message with no resolvable
`sender_person_id` is skipped and COUNTED — an unresolvable sender is the single
most common reason this rail goes quiet, and a quiet rail must never look like a
clean one.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from cru_match import (  # noqa: E402
    _commitment_id,
    build_commitment_updated_event,
    cru_eligible,
    load_open_commitments,
    match_inbound_to_commitments,
    AMBIGUOUS_REPLY_BASIS as _AMBIGUOUS_REPLY_BASIS,
    REPLY_BASIS as _REPLY_BASIS,
    REPLY_PROPOSED_BASIS as _REPLY_PROPOSED_BASIS,
)
from connector_adapters.provenance import (  # noqa: E402
    canonical_dedup_key,
    is_same_artifact,
    primary_artifact_key,
    resolve_mail_provider,
)

# Bug #102 — ONE exception type across both reconcile rails, so a caller that
# already catches the sent rail's abort catches this one too. Importing it
# rather than declaring a sibling is deliberate: two names for one failure is
# how a caller ends up handling half of it.
from reconcile_sent_commitments import PrimaryUserUnresolvedError  # noqa: E402

# The undo batch class every closure written here is stamped with. Matches the
# `commitment_close` reverser registered in `brain_undo.REVERSERS` — the D2
# legality check `brain_proposals.propose(tier="auto")` runs.
CHANGE_CLASS = "commitment_close"

# TTL for the confirm proposals this rail queues, mirroring the sent rail's so
# an un-adjudicated proposal expires instead of accumulating.
REVIEW_PROPOSAL_TTL_DAYS = 14


def _empty_signal_fields() -> dict:
    """The REPLYCLOSE observability block, zeroed.

    PRESENCE IS COUNTED SEPARATELY FROM TRUTH, and that distinction is the whole
    point (the SENTMATCH F-4/F-5 discipline). A message carrying
    `has_attachment: False` is the connector ANSWERING; a message whose dict
    never carried the key at all is the connector never being ASKED. Only the
    second means the rail is dead, and a counter that conflated them would
    report the dead-rail state as an ordinary quiet day.

    `n_from_user_skipped` and `n_sender_unresolved` are the two ways this rail
    goes quiet without any defect in the matcher, and both are invisible from
    the outcome alone.
    """
    return {
        "n_fetched": 0,
        "n_scored": 0,
        "n_sender_unresolved": 0,
        "n_from_user_skipped": 0,
        "n_thread_field_present": 0,
        "n_with_thread_ref": 0,
        "n_attachment_field_present": 0,
        "n_with_attachment": 0,
        "n_closed_on_reply": 0,
        "n_proposed_on_reply": 0,
        # EVORDER — candidates refused because the reply predates the promise it
        # would have closed. Non-zero is the fence working, not an error.
        "n_stale_evidence_skipped": 0,
    }


def _empty_coverage() -> dict:
    """How much of the open set this rail can even REACH (spec §1).

    A reply is matched to an item by the item's OWNER, so an item with no
    resolvable owner can never be closed here no matter what arrives. On the
    reference substrate 40 of 254 owned items carry neither a counterparty id
    nor a name; the equivalent unreachable class on this rail is the UNOWNED
    bucket, and it is reported rather than quietly excluded. A receipt that
    claimed full coverage of the open set would be lying by omission.
    """
    return {"n_open_eligible": 0, "n_waiting_on": 0, "n_unreachable_unowned": 0}


def _coverage_for(opens, user_person_id) -> dict:
    """Classify the eligible open set through THE canonical projector
    (`surface_split`, CTS1 §2.4) — never a re-derived owner comparison."""
    from surface_split import (SURFACE_UNOWNED, SURFACE_WAITING_ON,
                               classify_surface)
    out = _empty_coverage()
    eligible = cru_eligible(opens or [])
    out["n_open_eligible"] = len(eligible)
    for ev in eligible:
        surface = classify_surface(ev, user_person_id)
        if surface == SURFACE_WAITING_ON:
            out["n_waiting_on"] += 1
        elif surface == SURFACE_UNOWNED:
            out["n_unreachable_unowned"] += 1
    return out


def _short_date(ts):
    """'2026-05-31T14:00:00' → '2026-05-31'. Defensive — return '' on junk."""
    if not isinstance(ts, str) or not ts:
        return ""
    return ts[:10]


def _record_blocked_run(workspace_root, events_path, *, reason, source_skill,
                        fired_via, provider=None, batch_id=None) -> dict:
    """TRAINFIX F-4 — the receipt for a fire whose INBOUND read could not run.

    THE GATE THIS PAYS. MAILSEAM item 8 gave the SENT rail a loud receipt for a
    read that never happened; the inbound rail shipped without one. `fetch_blocked`
    appeared 3x in `reconcile_sent_commitments.py` and 0x here, so an inbound fire
    with no mail connector, an exhausted connector budget, or an unclassified
    account wrote the byte-identical CLEAN audit (`inbound_scanned_count: 0`,
    `n_closed: 0`, `status: "complete"`) that a healthy fire writes when the
    counterparty simply sent nothing. `validate_inbound_reconcile_ran` then
    returned ok=True. That is the dead-rail shape the whole receipt contract
    exists to make impossible, and it was live on one of the two mail rails.

    Deliberately parallel to the sent rail's `_record_blocked_run`, minus the
    cursor: this rail has none (the module docstring says why — both callers own
    their own window), so the sentence that matters is "nothing was read, so
    nothing closed and no confirm was queued" rather than "the cursor stayed
    where it was". Same three properties otherwise: the audit IS still written
    (a blocked fire that leaves no trace is indistinguishable from a fire that
    never happened), it carries `status: "blocked"` + the plain-English reason,
    and the validator refuses it.

    A blocked run also writes NO `commitment_review_proposed` rows and NO
    schedule-shift markers, because it never matched anything — the receipt's
    zeros are honest zeros about a read that did not occur, which is exactly
    what `status` is for.
    """
    from atomic_write import atomic_append_jsonl as _append
    from cru_match import _now_iso as _audit_ts
    from next_seq import next_seq as _next_seq

    batch_id = batch_id or ("inr_" + _dt.datetime.now(_dt.timezone.utc)
                            .strftime("%Y%m%dT%H%M%SZ"))
    audit_event = {
        "seq": _next_seq(str(events_path)),
        "ts": _audit_ts(),
        "type": "inbound_reconcile",
        "source_skill": source_skill,
        "data": {
            "kind": "reconcile-inbound",
            "status": "blocked",
            "blocked_reason": reason,
            "fired_via": fired_via,
            "batch_id": batch_id,
            "inbound_scanned_count": 0,
            "n_closed": 0,
            "n_pending": 0,
            "n_updated": 0,
            "n_partial_receipts": 0,
            "mail_provider": provider,
            "signal_fields": _empty_signal_fields(),
            "coverage": _empty_coverage(),
        },
    }
    try:
        from receipts import _machine_name

        _machine = _machine_name()
        if _machine:
            audit_event["data"]["machine"] = _machine
    except Exception:
        pass
    _append(events_path, [audit_event])

    summary = (f"The inbound check did not run: {reason}. Nothing was read, so "
               "nothing you were waiting on was closed and no confirm was "
               "queued — the next run reads the whole window.")
    print("reconcile-inbound BLOCKED: " + reason, file=sys.stderr)
    return {
        "ran": False,
        "blocked": True,
        "blocked_reason": reason,
        "batch_id": batch_id,
        "n_fetched": 0,
        "n_open_before": 0,
        "n_auto_closed": 0,
        "n_pending": 0,
        "n_updated": 0,
        "events_written": 0,
        "reviews_written": 0,
        "resolved": [],
        "pending": [],
        "updated": [],
        "n_partial_receipts": 0,
        "partial": [],
        "partial_propose_closure": [],
        "signal_fields": _empty_signal_fields(),
        "coverage": _empty_coverage(),
        "mail_provider": provider,
        "summary": summary,
    }


def reconcile_inbound(
    open_commitments,
    inbound_messages,
    *,
    user_person_id,
    provider=None,
    exclude_captured_since=None,
    workspace_root=None,
):
    """Match a batch of INBOUND messages to open waiting-on commitments.

    Returns::

        {
          "auto_close": [ {commitment_id, score, title, owner_id,
                           primary_thread_id, message_id, ts, evidence,
                           close_basis, recommendation} ],
          "pending":    [ same shape ],   # confirm before close
          "updated":    [ same shape ],   # the counterparty moved their own date
          "partial":    [ {commitment_id, title, primary_thread_id, score,
                           receipts: [...]} ],   # MC1 per-person receipts
          "signal_fields": { ... },       # did the reply checks actually run?
        }

    Each commitment appears at most once across auto_close/pending — the
    strongest evidence wins (a close beats a confirm; within a tier, the higher
    score).

    THE FENCE (spec §3, non-negotiable in this build). Layer 1 runs in two
    complementary halves: a caller-side exclusion through `is_same_artifact`
    (provider-label tolerant — it matches both the resolved backend's key and
    the legacy `gmail:` anchor, and falls back to native ids when no provider
    is known) and the matcher's own `commitment_matches_source_ref` on
    `inbound_source_ref` (ref-shape tolerant — merged_source_refs, structured
    provenance, the gmail-id field). Layer 2 is `exclude_captured_since`,
    forwarded from the caller.

    Layer 1 is load-bearing HERE in a way it is not on the sent rail:
    inbox-triage stamps `data.source_ref: <provider>:<message_id>` on the
    commitments it extracts from inbound mail, so without it the message that
    CREATED a waiting-on item is the message that closes it on the next scan.

    `provider` (MAILSEAM item 4) — the provider tag of the mail connector this
    batch was read from. None is not "Gmail": this function is PURE, so an
    unresolved provider is normal rather than exceptional, and the identity
    helpers degrade honestly instead of building `gmail:<id>` keys that match
    nothing on a Superhuman backend. `reconcile_inbound_and_receipt` resolves
    it once from the declared backend before calling here.

    THE DIRECTION CHECK (spec §2.3) runs twice, deliberately. Here, so a
    message from the user is skipped and COUNTED rather than silently dropped;
    and inside the matcher, as a hard stop, so a caller that reaches Path 4
    without going through this function still cannot close a waiting-on item
    with the user's own words.

    `workspace_root` (F-28 post-review F-1) — forwarded to Path 4 so the roster
    reader can resolve a free-text `counterparty_name` against the entity graph
    and count one person written as BOTH an id and that person's name once.
    Without it Path 4 carries a parameter nothing fills — the AUTOAPPLY F-5
    dead-rail shape, and the exact defect the F-28 fix exists to close. This
    function still does no I/O of its own; it hands the path down to the one
    reader that reads the entity graph, and only when a caller supplies it.
    `None` (the default) is byte-identically pre-F-28.

    Pure: no I/O, no clock. The caller writes the events.
    """
    if not user_person_id:
        # Bug #102 — the pure matcher keeps a safe degrade (empty result, no
        # raise) so a diagnostic caller is not turned into a crash, but the
        # SILENT part is what makes the dead rail invisible. The orchestrator
        # below turns this same condition into a hard abort.
        print(
            "reconcile_inbound: no user_person_id — direction cannot be "
            "established, so every reply basis is inert and this run can only "
            "return zero. Resolve the user with "
            "primary_user.resolve_primary_user (Bug #102).",
            file=sys.stderr,
        )
        return {"auto_close": [], "pending": [], "updated": [], "partial": [],
                "signal_fields": _empty_signal_fields()}

    best: dict[str, dict] = {}
    updated_by_cid: dict[str, dict] = {}
    partial_by_cid: dict[str, dict] = {}
    signals = _empty_signal_fields()
    # EVORDER — one dict for the whole run; the matcher increments per dropped
    # candidate and the total folds into the receipt below.
    _evorder_diag: dict = {}

    for msg in inbound_messages or []:
        if not isinstance(msg, dict):
            continue
        signals["n_fetched"] += 1
        ts = msg.get("ts")
        sender = str(msg.get("sender_person_id") or "").strip()
        if not sender:
            # An address the workspace has no person for. Real and common; the
            # matcher cannot attribute the message to anyone, so nothing is
            # scored. Counted, because "nobody resolved" and "nobody delivered"
            # produce the identical zero.
            signals["n_sender_unresolved"] += 1
            continue
        if sender == user_person_id:
            # The user's own message in an inbound batch — a thread they replied
            # to last, or a Sent row that leaked into an inbox fetch. Skipped
            # here AND refused by the matcher; counted so a batch that is mostly
            # the user's own mail is visible rather than mysterious.
            signals["n_from_user_skipped"] += 1
            continue

        # MAILSEAM item 4, inbound flavor: identity is compared through
        # `is_same_artifact`, never a single key built from a literal. The
        # `provider or "gmail"` this line used to carry built `gmail:<id>`
        # against commitments stored as `superhuman:<id>`, so the layer-1
        # self-ref fence matched nothing at all — a guard that had stopped
        # guarding without failing. It bites HARDER here than on the sent rail:
        # inbox-triage stamps `data.source_ref: <provider>:<message_id>` on the
        # commitments it extracts from inbound mail, so an unresolved provider
        # means the message that CREATED a waiting-on item is the message that
        # closes it on the next scan.
        mid = str(msg.get("message_id") or "").strip()
        own_key = primary_artifact_key(provider, mid)
        # The CONVERSATION key, derived exactly like the message key above so
        # one thread never reads as two.
        tid = str(msg.get("thread_id") or "").strip()
        own_thread_key = primary_artifact_key(provider, tid)
        # Count the FETCH, not just the outcome. `in msg` is the presence test
        # on purpose (see `_empty_signal_fields`).
        if "thread_id" in msg:
            signals["n_thread_field_present"] += 1
        if own_thread_key:
            signals["n_with_thread_ref"] += 1
        if "has_attachment" in msg:
            signals["n_attachment_field_present"] += 1
        if msg.get("has_attachment"):
            signals["n_with_attachment"] += 1
        signals["n_scored"] += 1

        # MAILSEAM item 4, inbound flavor — the caller-side half of layer 1,
        # mirroring `reconcile_sent`'s. `is_same_artifact` matches BOTH the
        # resolved provider's key and the legacy `gmail:` anchor when a
        # provider is known (a workspace has rows from before and after its
        # backend was declared), and compares native ids when it is not. The
        # matcher's own `commitment_matches_source_ref` still runs on
        # `own_key` below and reaches channels this cannot (merged_source_refs
        # after a C4 merge, structured provenance, the gmail-id field), so the
        # two are complementary rather than redundant: this one is provider-
        # label-tolerant, that one is ref-shape-tolerant.
        opens_for_msg = open_commitments
        if mid:
            opens_for_msg = [
                c for c in open_commitments
                if not is_same_artifact(canonical_dedup_key(event=c),
                                        provider, mid)
            ]

        results = match_inbound_to_commitments(
            open_commitments=opens_for_msg,
            sender_person_id=sender,
            subject=msg.get("subject"),
            body=msg.get("body"),
            # REPLYCLOSE — direction. Without it both new bases stay dead and
            # the matcher's own hard stop is inert.
            user_person_id=user_person_id,
            # Fence layer 1 — the ref of the message being scored.
            inbound_source_ref=own_key,
            # Fence layer 2 — commitments this same fire captured are not
            # independent evidence for closing themselves.
            exclude_captured_since=exclude_captured_since,
            # R1 — the thread anchor. Inert when the fetch carried no thread id.
            inbound_thread_ref=own_thread_key,
            # The fulfillment shape. bool() so a provider reporting an
            # attachment COUNT or a list still reads correctly, and an absent
            # key stays False: no evidence is not weak evidence.
            has_attachment=bool(msg.get("has_attachment")),
            # EVORDER layer 3 — when the reply actually arrived. A reply cannot
            # be evidence for a promise captured after it. `ts` is already read
            # above for the cursor-free window; absent → the guard is inert.
            inbound_ts=ts,
            diagnostics=_evorder_diag,
            # F-28 — the entity graph is what tells the roster reader that one
            # person written as an id AND that person's name is one
            # counterparty, not two. Absent → the raw union, pre-F-28.
            workspace_root=workspace_root,
        )
        for r in results:
            rec = r.get("recommendation")
            cid = r.get("commitment_id")
            if not cid:
                continue
            basis = r.get("close_basis") or ""
            if basis == _REPLY_BASIS:
                lede = "they replied on this thread with what you were waiting for"
            elif basis == _AMBIGUOUS_REPLY_BASIS:
                lede = ("their reply fits more than one thing you're waiting on")
            elif basis == _REPLY_PROPOSED_BASIS:
                lede = "their reply looks like the thing you were waiting for"
            else:
                lede = "matched their reply"
            evidence = (
                lede
                + (f" \"{msg.get('subject')}\"" if msg.get("subject") else "")
                + (f" ({_short_date(ts)})" if _short_date(ts) else "")
            )
            if rec == "partial_received":
                slot = partial_by_cid.setdefault(cid, {
                    "commitment_id": cid,
                    "title": r.get("title") or "",
                    "primary_thread_id": r.get("primary_thread_id") or "",
                    "score": r.get("score"),
                    "receipts": [],
                })
                if (r.get("score") or 0) > (slot["score"] or 0):
                    slot["score"] = r.get("score")
                seen = {x["counterparty_id"] for x in slot["receipts"]}
                for cp_id in r.get("matched_counterparty_ids") or []:
                    if cp_id and cp_id not in seen:
                        slot["receipts"].append({
                            "counterparty_id": cp_id,
                            "message_id": msg.get("message_id") or "",
                            "ts": ts or "",
                            "evidence": evidence,
                        })
                        seen.add(cp_id)
                continue
            if rec == "commitment_updated":
                # The counterparty moved their OWN date. Not a closure and not a
                # proposal — the item stays open with a note. Unchanged
                # behavior; it just lands in a named bucket instead of an
                # inline append at the call site.
                prev = updated_by_cid.get(cid)
                if prev is None or (r.get("score") or 0) > (prev["score"] or 0):
                    updated_by_cid[cid] = {
                        "commitment_id": cid,
                        "score": r.get("score"),
                        "title": r.get("title") or "",
                        "owner_id": r.get("owner_id") or sender,
                        "primary_thread_id": r.get("primary_thread_id") or "",
                        "message_id": msg.get("message_id") or "",
                        "ts": ts or "",
                        "recommendation": rec,
                        "close_basis": basis,
                        "evidence": evidence,
                    }
                continue
            if rec not in ("auto_resolve", "pending_review"):
                continue  # no_action
            proposal = {
                "commitment_id": cid,
                "score": r.get("score"),
                "title": r.get("title") or "",
                # The sender IS the owner on this path (Path 4's candidacy
                # gate), so the closure is attributed to whoever delivered.
                "owner_id": r.get("owner_id") or sender,
                "primary_thread_id": r.get("primary_thread_id") or "",
                "message_id": msg.get("message_id") or "",
                "ts": ts or "",
                "recommendation": rec,
                "close_basis": basis,
                "evidence": evidence,
                # WATCHGATE N-2 — the matcher's OWN fulfillment finding, carried
                # instead of dropped. `match_inbound_to_commitments` has always
                # returned it; nothing read it here, so the proposal this rail
                # persisted could never say whether completion language was
                # found, and the bulk-accept fence had nothing to weigh.
                "has_completion_signal": r.get("has_completion_signal"),
            }
            prev = best.get(cid)
            if prev is None:
                best[cid] = proposal
            else:
                prev_auto = prev["recommendation"] == "auto_resolve"
                new_auto = rec == "auto_resolve"
                if (new_auto and not prev_auto) or (
                    new_auto == prev_auto
                    and (proposal["score"] or 0) > (prev["score"] or 0)
                ):
                    best[cid] = proposal

    auto_close = [p for p in best.values()
                  if p["recommendation"] == "auto_resolve"]
    partial = [p for p in partial_by_cid.values() if p["receipts"]]
    partial_cids = {p["commitment_id"] for p in partial}
    pending = [p for p in best.values()
               if p["recommendation"] == "pending_review"
               and p["commitment_id"] not in partial_cids]
    updated = [u for u in updated_by_cid.values()
               if u["commitment_id"] not in {p["commitment_id"]
                                             for p in auto_close}]

    auto_close.sort(key=lambda p: p["score"] or 0, reverse=True)
    pending.sort(key=lambda p: p["score"] or 0, reverse=True)
    updated.sort(key=lambda p: p["score"] or 0, reverse=True)
    partial.sort(key=lambda p: p["score"] or 0, reverse=True)

    # Close the loop from field to outcome. Read from the FINAL lists, so these
    # describe what actually happened rather than the matcher's intermediate
    # grades.
    for p in auto_close:
        if p.get("close_basis") == _REPLY_BASIS:
            signals["n_closed_on_reply"] += 1
    for p in pending:
        if p.get("close_basis") in (_REPLY_PROPOSED_BASIS,
                                    _AMBIGUOUS_REPLY_BASIS):
            signals["n_proposed_on_reply"] += 1

    # EVORDER — fold layer 3's drop count into the receipt, same as the sent rail.
    signals["n_stale_evidence_skipped"] = int(
        _evorder_diag.get("stale_evidence_dropped", 0))

    return {"auto_close": auto_close, "pending": pending, "updated": updated,
            "partial": partial, "signal_fields": signals}


def reconcile_inbound_and_receipt(
    workspace_root,
    inbound_messages,
    *,
    user_person_id,
    source_skill="inbox",
    fired_via="scheduled",
    provider=None,
    exclude_captured_since=None,
    batch_id=None,
    fetch_blocked=None,
):
    """Run inbound→commitment reconciliation end-to-end and return a receipt.

    The caller's ONLY job before this: fetch the inbound batch and resolve each
    sender's email → `person_id` (MCP calls a script cannot make). Everything
    else — load opens, match, close through `commitment_state.close_commitments`,
    persist the confirm proposals, record schedule-shift updates, emit the
    `inbound_reconcile` audit event — happens here.

    RAISES `PrimaryUserUnresolvedError` when `user_person_id` is falsy (Bug
    #102, inherited from the sent rail and MORE important here: on this path the
    user id is not only the owner gate, it is the DIRECTION check. Unresolved,
    the direction check cannot run, every reply basis is inert, and a clean
    audit would report that as a healthy quiet day). Nothing is read, nothing is
    written, and no audit event exists for the run — so
    `validate_inbound_reconcile_ran` reports the run as not-having-happened,
    which is the truth.

    Returns::

        {"ran": True, "batch_id": str, "n_fetched": int, "n_open_before": int,
         "n_auto_closed": int, "n_pending": int, "n_updated": int,
         "events_written": int, "reviews_written": int,
         "resolved": [...], "pending": [...], "updated": [...],
         "n_partial_receipts": int, "partial": [...],
         "signal_fields": {...}, "coverage": {...},
         "mail_provider": str|None, "summary": str}

    `batch_id` is the undo handle. Every closure is stamped
    `data.brain_batch_id` + `data.brain_change_class`, which is the shape
    `brain_undo.recent_auto_batches` lists and `brain_undo.undo_batch` reverses
    — so a run this rail narrates is reversible by the same `undo` the sent rail
    advertises, with no new reverser and no new batch kind.

    `fetch_blocked` (TRAINFIX F-4 — the inbound mirror of MAILSEAM item 8) — a
    plain-English reason the INBOUND read could not happen (no mail connector,
    connector budget exhausted, an unclassified account). Passing it records the
    run as BLOCKED: the audit event carries `status: "blocked"` + the reason,
    `validate_inbound_reconcile_ran` refuses it, and the summary says what was
    missing. Without it, a fire that never read anything wrote the identical
    clean `inbound_scanned_count: 0` audit as a fire that read everything and
    found nothing — the dead-rail shape this receipt contract exists to make
    impossible, and it was live on this rail while the sent rail was covered. A
    blocked run closes nothing and queues no confirm.
    """
    if not user_person_id:
        msg = (
            "inbound reconciliation ABORTED: the primary user is unresolved "
            "(resolve_primary_user returned None/empty). Direction is derived "
            "from owner_id vs the primary user, so with no user the reply "
            "bases are inert and this run would write a clean audit claiming "
            "zero to close. No audit event written. Fix: pass the WORKSPACE "
            "ROOT (not _hq) to resolve_primary_user, or set "
            "workspace.user_person_id in entities.json (Bug #102)."
        )
        print(msg, file=sys.stderr)
        raise PrimaryUserUnresolvedError(msg)

    # MAILSEAM: resolve the provider ONCE, here, and use it for every ref this
    # run compares. An explicit argument wins; otherwise the declared email
    # backend answers. Unresolved stays unresolved — `is_same_artifact` then
    # compares native ids rather than inventing a label, and the receipt says
    # which provider (if any) this run was reading as.
    provider = resolve_mail_provider(workspace_root, provider)

    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"

    # TRAINFIX F-4 — a read that never happened is not a read that found
    # nothing. Record the run as blocked and stop: no matching (there is nothing
    # to match), no closure, no queued confirm. Checked AFTER the Bug #102 abort
    # and the provider resolve, in the same order as the sent rail — an
    # unresolved primary user is a worse failure than a blocked read and must
    # still raise, and the receipt should name the provider it would have read
    # as even when the read never happened.
    blocked = str(fetch_blocked or "").strip()
    if blocked:
        return _record_blocked_run(
            workspace_root, events_path, reason=blocked,
            source_skill=source_skill, fired_via=fired_via, provider=provider,
            batch_id=batch_id,
        )

    # F-28 — same reasoning as the sent driver: one workspace per fire, so the
    # projection's all-received stamp and the roster reader agree.
    opens = load_open_commitments(str(events_path), workspace_root=workspace_root)
    n_open_before = len(opens)
    coverage = _coverage_for(opens, user_person_id)

    res = reconcile_inbound(opens, inbound_messages or [],
                            user_person_id=user_person_id,
                            provider=provider,
                            exclude_captured_since=exclude_captured_since,
                            # F-28 — this wrapper HOLDS the workspace and used
                            # to keep it, leaving Path 4's roster fix
                            # unreachable on the rail that fires daily.
                            workspace_root=workspace_root)
    auto_close = res["auto_close"]
    pending = list(res["pending"])
    updated = res["updated"]
    partial = res["partial"]
    signal_fields = res["signal_fields"]

    # §7 precedent (efb_/idr_/pbs_/rcc_): ONE batch per RUN, timestamped. A
    # per-SKILL constant would group every close this skill ever applied into
    # one undoable batch, so a single `undo` would reach back across days.
    batch_id = batch_id or ("inr_" + _dt.datetime.now(_dt.timezone.utc)
                            .strftime("%Y%m%dT%H%M%SZ"))

    events_written = 0
    if auto_close:
        from commitment_state import close_commitments
        results = close_commitments(
            workspace_root,
            [{
                "commitment_id": c["commitment_id"],
                # Person-shaped, NOT a skill string: on this rail the closure
                # really was performed by the counterparty, and `resolved_by`
                # is the only place that fact is recorded. (The sent rail's
                # `resolved_by="sent_reconcile"` is the odd one out — it is
                # load-bearing there only because its undo batch is keyed off
                # that literal; this rail is keyed off `brain_batch_id`, so it
                # is free to be honest.)
                "resolved_by": c.get("owner_id") or "",
                "evidence": c.get("evidence") or "matched their reply",
                "primary_thread_id": c.get("primary_thread_id") or "",
                "extra_data": {"brain_batch_id": batch_id,
                               "brain_change_class": CHANGE_CLASS},
            } for c in auto_close],
            source_skill=source_skill,
        )
        by_id = {str(c["commitment_id"]): c for c in auto_close}
        closed_or_already: set = set()
        for r in results:
            rid = str(r.get("commitment_id"))
            if r["status"] == "closed":
                events_written += 1
                closed_or_already.add(rid)
            elif r["status"] == "already_resolved":
                closed_or_already.add(rid)
            elif r.get("error") == "PendingReviewError" and rid in by_id:
                # A pending_review commitment is never auto-resolved — demote it
                # to the confirm list instead of closing it.
                pending.append(by_id[rid])
        auto_close = [c for c in auto_close
                      if str(c["commitment_id"]) in closed_or_already]

    # MC1 per-person receipts, same contract as the sent rail: informational,
    # never a closure, idempotent per (commitment, counterparty).
    n_partial_receipts = 0
    partial_recorded: list = []
    partial_propose_closure: list = []
    if partial:
        from commitment_parties import received_from_ids as _rcv_ids
        from commitment_state import mark_partial_received
        already_by_cid = {str(_commitment_id(c)): set(_rcv_ids(c))
                          for c in opens}
        for p in partial:
            already = already_by_cid.get(str(p["commitment_id"]), set())
            recorded_cps: list = []
            proposed = False
            for r in p["receipts"]:
                cp_id = r["counterparty_id"]
                if cp_id in already:
                    continue
                try:
                    result = mark_partial_received(
                        workspace_root,
                        p["commitment_id"],
                        # The counterparty delivered, so the receipt is theirs.
                        received_by=cp_id,
                        counterparty_id=cp_id,
                        evidence=r.get("evidence") or "delivered in their reply",
                        source_skill=source_skill,
                    )
                except Exception:
                    continue
                if result.get("status") == "received":
                    n_partial_receipts += 1
                    recorded_cps.append(cp_id)
                    already.add(cp_id)
                    if result.get("propose_closure"):
                        proposed = True
            if recorded_cps:
                partial_recorded.append({
                    "commitment_id": p["commitment_id"],
                    "title": p.get("title") or "",
                    "counterparty_ids": recorded_cps,
                })
            if proposed:
                partial_propose_closure.append({
                    "commitment_id": p["commitment_id"],
                    "title": p.get("title") or "",
                })

    # The confirm band MUST NOT evaporate — persist each proposal as a
    # `commitment_review_proposed` so the next Waiting On chat surfaces it for
    # one-click confirm. Deduped against the OPEN proposal set.
    #
    # WATCHGATE N-2 (RIDERS1 item 5): through `build_pending_review_event`, THE
    # writer for this type — the only thing that persists the two fields the
    # shared bulk-accept fence weighs. Hand-built, this rail's rows were STRONG
    # by construction: the matcher's completion finding and the reply's own time
    # were both in hand and neither reached the event.
    reviews_written = 0
    if pending:
        from cru_match import (build_pending_review_event,
                               load_open_review_proposals)
        from event_gate import append_event
        already_proposed = {
            (p.get("data") or {}).get("commitment_id")
            for p in load_open_review_proposals(str(events_path))
        }
        for p in pending:
            if p["commitment_id"] in already_proposed:
                continue
            ev = build_pending_review_event(
                commitment_id=p["commitment_id"],
                primary_thread_id=p.get("primary_thread_id") or "",
                source_skill=source_skill,
                proposed_resolution="auto_resolve",
                score=p.get("score") or 0,
                evidence=p.get("evidence") or "matched their reply",
                next_seq=None,   # the gate stamps seq inside the writer lock
                title=p.get("title") or "",
                # The reply's own time (EVORDER layer 3's input, now also the
                # fence's) and the matcher's fulfillment finding. `None` on
                # either means "not assessed" and weakens nothing.
                evidence_ts=p.get("ts") or None,
                has_completion_signal=p.get("has_completion_signal"),
            )
            ev["data"].update({
                "ttl_days": REVIEW_PROPOSAL_TTL_DAYS,
                "ambiguous": p.get("close_basis") == _AMBIGUOUS_REPLY_BASIS,
            })
            append_event(events_path, [ev], holder=source_skill)
            reviews_written += 1

    # Schedule-shift markers — the counterparty moved their own date. Same
    # builder the prose call sites used; only the loop moved.
    if updated:
        from atomic_write import atomic_append_jsonl as _append_upd
        from next_seq import next_seq as _peek
        seq = _peek(str(events_path))
        rows = []
        for u in updated:
            rows.append(build_commitment_updated_event(
                commitment_id=u["commitment_id"],
                primary_thread_id=u.get("primary_thread_id") or "",
                source_skill=source_skill,
                change_summary="Counter-party shifted their own deadline (inbound mail)",
                evidence=u.get("evidence") or "matched their reply",
                next_seq=seq,
            ))
            seq += 1
        _append_upd(events_path, rows)

    n_auto = len(auto_close)
    n_pend = len(pending)
    n_upd = len(updated)
    n_fetched = signal_fields["n_fetched"]

    # ALWAYS emit the audit event — even on a 0-scan run — so every fire leaves
    # a verifiable trace in events.jsonl. Enforcement points at THIS event, not
    # at a printed sentence: a model can narrate "checked the inbox" without
    # checking anything; it cannot fabricate a row that carries the counts.
    from atomic_write import atomic_append_jsonl as _append
    from cru_match import _now_iso as _audit_ts
    from next_seq import next_seq as _next_seq
    audit_event = {
        "seq": _next_seq(str(events_path)),
        "ts": _audit_ts(),
        "type": "inbound_reconcile",
        "source_skill": source_skill,
        "data": {
            "kind": "reconcile-inbound",
            "status": "complete",
            "fired_via": fired_via,
            "batch_id": batch_id,
            "inbound_scanned_count": n_fetched,
            "n_closed": n_auto,
            "n_pending": n_pend,
            "n_updated": n_upd,
            "n_partial_receipts": n_partial_receipts,
            # MAILSEAM — the provider every ref this run compared was built
            # under. None means the run could not establish one, which is a
            # fact worth reading back off the trace rather than a silent
            # fallback to a literal.
            "mail_provider": provider,
            # Did the reply checks RUN, or was there nothing to find? Folded
            # into the SAME audit event as the counts: one fire, one verifiable
            # trace, free-form `data`, no schema change.
            "signal_fields": signal_fields,
            # How much of the open set this rail can reach at all.
            "coverage": coverage,
        },
    }
    try:
        from receipts import _machine_name
        _machine = _machine_name()
        if _machine:
            audit_event["data"]["machine"] = _machine
    except Exception:
        pass
    _append(events_path, [audit_event])

    if n_fetched == 0:
        summary = "No new inbound mail to check against what you're waiting on."
    elif n_auto == 0:
        summary = (f"Checked {n_fetched} inbound message"
                   f"{'s' if n_fetched != 1 else ''} — nothing you're waiting "
                   f"on came in.")
    else:
        tail = f", {n_pend} to confirm" if n_pend else ""
        summary = (f"Closed {n_auto} thing{'s' if n_auto != 1 else ''} you were "
                   f"waiting on — they came in by email{tail}.")
    if n_upd:
        summary += (f" {n_upd} moved to a new date on their side.")

    # The counters are for a validator; this sentence is for the HUMAN, and it
    # goes LAST because it is a caveat on everything above it. It fires only in
    # the dead-rail state — the fetch carried NEITHER field on ANY message, so
    # the reply checks could not run at all and the zero above means nothing.
    # A fetch that carried the fields and found no deliveries says nothing
    # extra: that is an honest zero, and a warning that always fires is as
    # useless as one that never does. Plain language, no field names (Rule 4).
    if signal_fields["n_scored"] and not (
            signal_fields["n_thread_field_present"]
            or signal_fields["n_attachment_field_present"]):
        summary += (
            " Heads up: none of those messages came through with conversation"
            " or attachment details, so the checks that recognize a reply as"
            " the thing you were waiting for could not run — only the wording"
            " was compared."
        )
    # The other way this rail goes quiet: every message was skipped before it
    # was ever scored. Two different defects (nobody resolvable / the batch was
    # the user's own mail) and both produce the same clean zero without this.
    if n_fetched and not signal_fields["n_scored"]:
        summary += (
            f" Heads up: none of the {n_fetched} message"
            f"{'s' if n_fetched != 1 else ''} could be checked — "
            + ("they were all from you"
               if signal_fields["n_from_user_skipped"] >= n_fetched
               else "the senders could not be matched to anyone on file")
            + "."
        )

    def _slim(items):
        # `message_id` rides the slim rows so the caller can answer "did THIS
        # message close anything?" without re-running the matcher. Phase 2.6's
        # resolution-miss telemetry asks exactly that, per message, and the
        # first cut of these rows dropped the id — leaving an instruction in
        # the orchestrator that the shape could not satisfy. The alternative
        # (have the rider call `reconcile_inbound` itself for the non-slim
        # proposals) would run the matcher twice per fire and give the same
        # question two answers; one additive field is the cheaper truth.
        #
        # DELIBERATE ASYMMETRY with the sent rail (review F-3): the two `_slim`
        # shapes now differ — `reconcile_sent_commitments`' rows carry no
        # `message_id`, because nothing downstream of the sent receipt asks a
        # per-message question. Added there it would be an unread field, and an
        # unread field is the thing that later reads as a contract. If the sent
        # rail ever grows the same telemetry, match it then.
        return [{"commitment_id": c["commitment_id"],
                 "title": c.get("title") or "", "ts": c.get("ts") or "",
                 "message_id": c.get("message_id") or ""}
                for c in items]

    return {
        "ran": True,
        "batch_id": batch_id,
        "n_fetched": n_fetched,
        "n_open_before": n_open_before,
        "n_auto_closed": n_auto,
        "n_pending": n_pend,
        "n_updated": n_upd,
        "events_written": events_written,
        "reviews_written": reviews_written,
        "resolved": _slim(auto_close),
        "pending": _slim(pending),
        "updated": _slim(updated),
        "n_partial_receipts": n_partial_receipts,
        "partial": partial_recorded,
        "partial_propose_closure": partial_propose_closure,
        "signal_fields": signal_fields,
        "coverage": coverage,
        "mail_provider": provider,
        "summary": summary,
    }


def validate_inbound_reconcile_ran(workspace_root, *, since_ts=None) -> dict:
    """Read events.jsonl back and confirm a REAL inbound reconciliation ran.

    The ungameable half of the contract: a printed "checked your inbox" line
    with no `inbound_reconcile` audit event in the log returns ok=False. Looks
    at the LATEST audit. With `since_ts` given, that audit's own `ts` must be at
    or after it, so a stale prior audit cannot pass for the current run — the
    role `cursor_from` plays on the sent rail, without inventing a second
    window cursor for a caller that already owns one.

    A 0-scan run is still a valid run (ok=True): it ran and found nothing, and
    the audit event proves the pass happened.

    TRAINFIX F-4 — EXCEPT when the audit says `status: "blocked"`: the inbound
    read never happened, so the zero means nothing, and ok=False carries the
    recorded reason. Without this the blocked audit would be read back as a
    healthy empty run, which is the exact dead rail the blocked status exists to
    expose (a clean audit over a skipped read). Mirrors
    `reconcile_sent_commitments.validate_reconcile_ran`.
    """
    from cru_match import load_events_defensively
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not events_path.exists():
        return {"ok": False, "ran": False, "reason": "no events.jsonl"}
    latest = None
    # EVGUARD — the hand-rolled loop that used to live here wrapped only the
    # parse in `except Exception`, so a bare-string line reached `e.get()` and
    # raised AttributeError out of the validator (Sub-bug #14b, second half):
    # one junk line and the ungameable-reconcile check stopped answering at
    # all. The canonical loader skips both malformed shapes and is
    # shard-transparent; since_ts=None = full history.
    # Kept BYTE-FOR-BYTE parallel with reconcile_sent_commitments.
    # validate_reconcile_ran — the two are copy-clones by design.
    events, _skipped = load_events_defensively(events_path, since_ts=None)
    for e in events:
        if e.get("type") == "inbound_reconcile":
            latest = e  # append-ordered → keep the last one seen
    if latest is None:
        return {"ok": False, "ran": False,
                "reason": "no inbound_reconcile audit event — the inbound "
                          "pass did not actually run"}
    d = latest.get("data") or {}
    if d.get("status") == "blocked":
        return {"ok": False, "ran": False,
                "reason": ("the inbound read did not happen — "
                           + (d.get("blocked_reason") or "recorded as blocked")),
                "batch_id": d.get("batch_id"),
                "inbound_scanned_count": d.get("inbound_scanned_count"),
                "n_closed": d.get("n_closed")}
    if since_ts is not None:
        # Parsed, never string-compared. The audit stamps `...Z` and a caller's
        # `datetime.now(timezone.utc).isoformat()` stamps `...+00:00`; those two
        # spellings of the same instant do not order lexicographically, and a
        # validator that silently mis-orders them is worse than no validator.
        from event_time import parse_ts as _parse
        seen, want = _parse(latest.get("ts")), _parse(since_ts)
        stale = (seen is None or want is None) or seen < want
        if stale:
            return {"ok": False, "ran": True,
                    "reason": f"latest audit is not this run's (ts="
                              f"{latest.get('ts')!r}, expected at or after "
                              f"{since_ts!r})",
                    "batch_id": d.get("batch_id")}
    return {
        "ok": True, "ran": True,
        "batch_id": d.get("batch_id"),
        "inbound_scanned_count": d.get("inbound_scanned_count"),
        "n_closed": d.get("n_closed"),
        "n_pending": d.get("n_pending"),
        "signal_fields": d.get("signal_fields"),
        "coverage": d.get("coverage"),
    }


__all__ = ["reconcile_inbound", "reconcile_inbound_and_receipt",
           "validate_inbound_reconcile_ran", "PrimaryUserUnresolvedError",
           "CHANGE_CLASS"]
