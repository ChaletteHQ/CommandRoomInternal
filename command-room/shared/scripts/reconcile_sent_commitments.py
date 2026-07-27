"""Sent-mail → open-commitment reconciliation (v3.18.3+, Bug #85 layer 1).

WHY THIS EXISTS
---------------
The closure engine already exists — `cru_match.match_send_to_commitments`
scores an outbound send against open commitments, and apply-choices closes
HIGH-confidence matches when a send goes through the IN-PRODUCT draft path
(`N send`). But when the CEO sends a follow-up DIRECTLY FROM GMAIL (outside the
product), no in-product send event is produced, the match engine never runs,
and the commitment stays open forever. That is the v3.18.1 trust-killer: the
morning brief listed already-sent follow-ups to two real counterparties as
still owed and told
the CEO to redo done work.

This module is the missing CALLER: it takes the open commitments + a batch of
outbound "Sent" messages (the skill fetches them from the Gmail MCP) and runs
each through the SAME `match_send_to_commitments` engine, splitting matches into
HIGH-confidence auto-close vs MEDIUM pending-review using the SAME shared
confidence thresholds. It is pure (no connector I/O, no datetime.now()): the
skill fetches Sent mail + resolves recipient person_ids, passes dicts in, and
emits the returned `commitment_resolved` events. Auto-closes are surfaced with
an undo affordance ("closed N you'd already sent — say `undo`"); the schema and
the resolved-event shape are unchanged. As of Phase 2 Stage B the closures are
written through `commitment_state.close_commitment` — the single closure path
(F2) — with matching logic here untouched.

INPUT SHAPE (sent_messages)
  [{"message_id": str, "ts": iso-str, "recipient_person_ids": [str, ...],
    "subject": str|None, "body": str|None}, ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from cru_match import (  # noqa: E402
    match_send_to_commitments,
    build_commitment_resolved_event,
    load_open_commitments,
    _commitment_id,
)
from connector_adapters.provenance import canonical_dedup_key  # noqa: E402


# FS-11 (M ruling 2026-07-15): auto-close MODERATE-confidence sent-mail matches
# too — the CEO said twice "if they are closed, just close them." Only
# multi-candidate AMBIGUITY (one send that plausibly fulfills more than one open
# commitment) stays a confirm proposal; every unambiguous moderate match closes
# automatically, narrated in the change feed with an `undo` (commitment_close is
# an AUTO_ALLOWED reversible class — the reopen reverser is the safety net).
AUTO_CLOSE_MODERATE = True

# TTL for the ambiguous confirm proposals that DO stay queued — an unconfirmed
# commitment_review_proposed older than this expires instead of accumulating.
REVIEW_PROPOSAL_TTL_DAYS = 14


def _short_date(ts):
    """'2026-05-31T14:00:00' → '2026-05-31'. Defensive — return '' on junk."""
    if not isinstance(ts, str) or not ts:
        return ""
    return ts[:10]


def reconcile_sent(
    open_commitments,
    sent_messages,
    *,
    user_person_id,
    provider="gmail",
):
    """Match a batch of outbound Sent messages to open commitments.

    Returns:
      {
        "auto_close": [ {commitment_id, score, title, owner_id, primary_thread_id,
                         message_id, ts, evidence} ],   # HIGH confidence
        "pending":    [ same shape ],                   # MEDIUM — confirm before close
        "partial":    [ {commitment_id, title, primary_thread_id, score,
                         receipts: [{counterparty_id, message_id, ts, evidence}],
                         skipped_names: [str]} ],       # HYG1: multi-cp per-person receipts
        "cursor_ts":  str | None,                       # max message ts seen (advance the cursor)
      }

    Each commitment appears at most once across auto_close/pending — the
    highest-scoring send wins, and auto_close takes precedence over pending
    for the same id.

    HYG1 Item 1 (the MC1 4.7 wire-up): a `partial_received` recommendation —
    cru_match's downgrade of an AUTO-RESOLVE-grade match against a
    multi-counterparty commitment — now lands in `partial` instead of being
    dropped. Rules: only counterparties with RESOLVED ids ride `receipts`
    (name-only matches land in `skipped_names` — never guess an id from a
    name token at write time); one receipt per (commitment, counterparty)
    within the batch; a commitment with a partial receipt this run is
    EXCLUDED from `pending` (the per-person receipt is the more precise
    record of the same send evidence — a whole-close confirm next to it
    would double-surface). The BUG-3719 self-closure guard applies
    unchanged: the own-message filter runs BEFORE matching, so a receipt is
    never recorded from the message that opened the commitment.

    Pure: no I/O, no clock. The caller emits the events + persists the cursor.
    """
    if not user_person_id:
        return {"auto_close": [], "pending": [], "partial": [], "cursor_ts": None}

    best: dict[str, dict] = {}      # commitment_id → best proposal so far
    partial_by_cid: dict[str, dict] = {}  # commitment_id → accumulated receipts
    cursor_ts = None

    for msg in sent_messages or []:
        if not isinstance(msg, dict):
            continue
        ts = msg.get("ts")
        if isinstance(ts, str) and (cursor_ts is None or ts > cursor_ts):
            cursor_ts = ts

        # BUG-3719 self-closure guard: a commitment CAPTURED FROM this very
        # message (sent-promise capture) must never be closed BY this message —
        # the promise's origin is not its completion evidence. Without this,
        # any catch-up / wide re-scan that re-fetches the message closes the
        # promise it opened last run, breaking the "an over-wide window is
        # always safe" invariant.
        #
        # R16 (connector-agnostic-v1): identity is the CANONICAL dedup key, so
        # the guard holds across formats — a legacy `gmail:<Id>` source_ref
        # (any case) and a structured-provenance re-observation of the same
        # message reduce to one key (the old byte-compare missed both).
        mid = str(msg.get("message_id") or "").strip()
        own_key = canonical_dedup_key(provider=provider or "gmail", native_id=mid) if mid else None
        opens_for_msg = open_commitments
        if own_key:
            opens_for_msg = [
                c for c in open_commitments
                if canonical_dedup_key(event=c) != own_key
            ]

        results = match_send_to_commitments(
            open_commitments=opens_for_msg,
            sender_person_id=user_person_id,
            recipient_person_ids=msg.get("recipient_person_ids") or [],
            subject=msg.get("subject"),
            body=msg.get("body"),
            # Bug #103 recall fallback: recipient display names + email local-parts
            # so a commitment that names the recipient in its title ("Send Bo a
            # recap") still matches even when the counterparty isn't linked into
            # person_ids or has no email on file.
            recipient_names=msg.get("recipient_names") or [],
        )
        for r in results:
            rec = r.get("recommendation")
            if rec == "partial_received":
                # HYG1: an auto-grade match on a multi-counterparty item —
                # accumulate ONE receipt per resolved counterparty; name-only
                # matches are reported, never written.
                cid = r.get("commitment_id")
                if not cid:
                    continue
                slot = partial_by_cid.setdefault(cid, {
                    "commitment_id": cid,
                    "title": r.get("title") or "",
                    "primary_thread_id": r.get("primary_thread_id") or "",
                    "score": r.get("score"),
                    "receipts": [],
                    "skipped_names": [],
                })
                if (r.get("score") or 0) > (slot["score"] or 0):
                    slot["score"] = r.get("score")
                seen_cps = {x["counterparty_id"] for x in slot["receipts"]}
                evidence = (
                    "delivered by your sent message"
                    + (f" \"{msg.get('subject')}\"" if msg.get("subject") else "")
                    + (f" ({_short_date(ts)})" if _short_date(ts) else "")
                )
                for cp_id in r.get("matched_counterparty_ids") or []:
                    if cp_id and cp_id not in seen_cps:
                        slot["receipts"].append({
                            "counterparty_id": cp_id,
                            "message_id": msg.get("message_id") or "",
                            "ts": ts or "",
                            "evidence": evidence,
                        })
                        seen_cps.add(cp_id)
                if not r.get("matched_counterparty_ids"):
                    for nm in r.get("matched_counterparty_names") or []:
                        if nm and nm not in slot["skipped_names"]:
                            slot["skipped_names"].append(nm)
                continue
            if rec not in ("auto_resolve", "pending_review"):
                continue  # no_action — ignore
            cid = r.get("commitment_id")
            if not cid:
                continue
            proposal = {
                "commitment_id": cid,
                "score": r.get("score"),
                "title": r.get("title") or "",
                "owner_id": r.get("owner_id") or user_person_id,
                "primary_thread_id": r.get("primary_thread_id") or "",
                "message_id": msg.get("message_id") or "",
                "ts": ts or "",
                "recommendation": rec,
                "evidence": (
                    "matched your sent message"
                    + (f" \"{msg.get('subject')}\"" if msg.get("subject") else "")
                    + (f" ({_short_date(ts)})" if _short_date(ts) else "")
                ),
            }
            prev = best.get(cid)
            # Keep the strongest evidence: auto_resolve beats pending_review;
            # within the same tier, higher score wins.
            if prev is None:
                best[cid] = proposal
            else:
                prev_auto = prev["recommendation"] == "auto_resolve"
                new_auto = rec == "auto_resolve"
                if (new_auto and not prev_auto) or (
                    new_auto == prev_auto and (proposal["score"] or 0) > (prev["score"] or 0)
                ):
                    best[cid] = proposal

    auto_close = [p for p in best.values() if p["recommendation"] == "auto_resolve"]
    # A commitment with a partial receipt this run leaves pending — the
    # per-person receipt is the more precise record of the same evidence.
    partial = [p for p in partial_by_cid.values() if p["receipts"] or p["skipped_names"]]
    partial_cids = {p["commitment_id"] for p in partial if p["receipts"]}
    pending_all = [
        p for p in best.values()
        if p["recommendation"] == "pending_review"
        and p["commitment_id"] not in partial_cids
    ]
    # FS-11: promote UNAMBIGUOUS moderate matches to auto-close. Ambiguity = one
    # sent message that matched more than one open commitment at moderate grade
    # (which one did the send actually fulfill? — keep those for confirm). A
    # moderate match that is 1:1 with its send is closed, flagged `moderate` so
    # the feed narrates it honestly ("probably handled — undo if not").
    from collections import Counter as _Counter
    _msg_key = lambda p: (p.get("message_id") or f"__nomid_{p['commitment_id']}")
    _msg_counts = _Counter(_msg_key(p) for p in pending_all)
    pending = []
    for p in pending_all:
        if AUTO_CLOSE_MODERATE and _msg_counts[_msg_key(p)] == 1:
            promoted = dict(p)
            promoted["moderate"] = True
            promoted["evidence"] = "probably handled — " + (
                p.get("evidence") or "matched an outbound send")
            auto_close.append(promoted)
        else:
            pending.append(p)
    auto_close.sort(key=lambda p: p["score"] or 0, reverse=True)
    pending.sort(key=lambda p: p["score"] or 0, reverse=True)
    partial.sort(key=lambda p: p["score"] or 0, reverse=True)

    return {"auto_close": auto_close, "pending": pending, "partial": partial,
            "cursor_ts": cursor_ts}


def to_resolved_events(closures, *, source_skill, seq_start):
    """LEGACY shape helper — construction only, superseded by
    `commitment_state.close_commitment` (Phase 2 Stage B, F2).

    `reconcile_and_receipt` no longer calls this: it closes through the single
    closure path (`close_commitments`), which normalizes legacy ids, refuses
    orphan tombstones loudly, is idempotent over the full resolved-id set, and
    honors pending_review. Kept only so pre-Stage-B callers/tests that inspect
    the event shape keep working; do NOT append these events directly in new
    code.
    """
    events = []
    for i, c in enumerate(closures or []):
        events.append(
            build_commitment_resolved_event(
                commitment_id=c["commitment_id"],
                resolved_by="sent_reconcile",
                primary_thread_id=c.get("primary_thread_id") or "",
                source_skill=source_skill,
                evidence=c.get("evidence") or "matched an outbound send",
                next_seq=seq_start + i,
            )
        )
    return events


# --------------------------------------------------------------------------
# Orchestrator + receipt (v3.18.9, Bug #98 — kill the reconciliation theater)
# --------------------------------------------------------------------------
#
# The v3.18.5 fix made the brief PRINT a "reconciliation status line" but could
# not make the model RUN the work — an output-contract gate constrains emitted
# text, not tool calls, so the model printed a plausible line and skipped the
# expensive Sent fetch + the multi-step write procedure (RA85: cursor frozen,
# zero `resolved_by=sent_reconcile` events). The old procedure was ALSO spread
# across the brief as seven manual steps (read cursor → fetch → reconcile →
# reserve seq → build closers → append → advance cursor), so there were seven
# places to half-do it.
#
# This orchestrator collapses ALL of that into ONE call. The only step it cannot
# do (the Gmail Sent fetch — an MCP call) stays with the brief; everything else
# — load opens, read the cursor, run the matcher, write the `commitment_resolved`
# events, advance the cursor — happens here, atomically, as a side effect of
# actually running. It returns a RECEIPT whose fields exist only because the work
# ran (cursor_after, events_written, resolved titles). The brief's contract
# becomes: fetch Sent → call this → paste `receipt["summary"]` verbatim. A skip
# produces NO receipt, which the brief's fail-loud post-condition turns into a
# visible "reconciliation DID NOT RUN" instead of a fabricated success line.
# (`shared/scripts/reconcile_and_receipt` is the checkable artifact; the printed
# claim is not.)


def _entities_path(workspace_root):
    return Path(workspace_root) / "_hq" / "data" / "entities.json"


def _read_cursor(workspace_root):
    """Read workspace.sent_reconcile_cursor (defensive about the wrapper shape).
    Returns (cursor_or_None, raw_entities_dict)."""
    import json
    p = _entities_path(workspace_root)
    raw = json.loads(p.read_text(encoding="utf-8"))
    inner = raw["entities"] if isinstance(raw.get("entities"), dict) else raw
    ws = inner.get("workspace") if isinstance(inner.get("workspace"), dict) else {}
    return ws.get("sent_reconcile_cursor"), raw


def _write_cursor(workspace_root, raw, new_cursor, *, source_skill):
    """Persist workspace.sent_reconcile_cursor, preserving the wrapper shape.
    Uses the locked JSON writer (concurrent-writer safe)."""
    from atomic_write import atomic_write_json_locked
    inner = raw["entities"] if isinstance(raw.get("entities"), dict) else raw
    ws = inner.setdefault("workspace", {})
    if not isinstance(ws, dict):
        ws = {}
        inner["workspace"] = ws
    ws["sent_reconcile_cursor"] = new_cursor
    atomic_write_json_locked(_entities_path(workspace_root), raw, holder=source_skill)


def reconcile_and_receipt(
    workspace_root,
    sent_messages,
    *,
    user_person_id,
    source_skill="morning-briefing",
    outcome_watch_summary=None,
    fired_via="scheduled",
    sent_commitment_items=None,
    provider="gmail",
):
    """Run Sent→commitment reconciliation end-to-end and return a tamper-proof
    receipt. Does the I/O the brief used to do by hand (Bug #98).

    The caller's ONLY job before this: fetch the Gmail "Sent" batch since the
    cursor and resolve recipient person_ids (an MCP call a script can't make).
    Everything else — load opens, read cursor, match, write the
    `commitment_resolved` events for HIGH-confidence closes, advance the cursor —
    happens here.

    `sent_commitment_items` (v4.6.2, BUG-3719) — commissives the skill
    extracted from the SAME Step-2 fetch (the user's own outbound promises,
    Stage-D floor applied; shape per `sent_capture.capture_sent_items`).
    When not None, this run also OPENS commitments for promises with no
    matching open item: each item runs the shared capture block, dedups
    against the PRE-close open set via `capture_gate.matches_open_commitment`
    (cross-channel restatements merge, never double-track), routes through
    W4c's relevance gate, and lands in one locked append. A promise never
    logged can now be rescued by the same daily pass that closes handled
    ones. None (the default) = pre-4.6.2 behavior, byte-identical.

    Returns a receipt:
      {
        "ran": True,                 # present iff this function executed
        "cursor_before": str|None,
        "cursor_after": str|None,
        "cursor_advanced": bool,
        "n_fetched": int,            # len(sent_messages) handed in
        "n_open_before": int,
        "n_auto_closed": int,
        "n_pending": int,
        "events_written": int,
        "resolved": [ {commitment_id, title, ts} ],   # for the undo line
        "pending":  [ {commitment_id, title, ts} ],    # for the confirm line
        "n_opened": int,             # BUG-3719 capture pass (0 when not run)
        "opened":  [ {title, message_id, kind, due, pending_review} ],
        "capture": dict|None,        # full capture_sent_items summary
        "summary": str,              # code-generated line the brief pastes verbatim
      }

    Idempotent across a day: a re-run fetches only newer Sent mail; an empty
    batch closes nothing and leaves the cursor where it is; a re-extracted
    commissive is skipped by (source_ref, title) + restatement dedup.
    """
    cursor_before, raw = _read_cursor(workspace_root)
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    opens = load_open_commitments(str(events_path))
    n_open_before = len(opens)

    res = reconcile_sent(opens, sent_messages or [], user_person_id=user_person_id,
                         provider=provider)
    auto_close = res["auto_close"]
    pending = res["pending"]
    partial = res.get("partial") or []

    # Write the HIGH-confidence closers through THE closure path (Stage B, F2):
    # legacy-id normalization, loud refusal of orphan tombstones, full-set
    # idempotency, pending_review floor — matching above is unchanged, only
    # event construction moved into close_commitment. (Proposal ids come from
    # _commitment_id over the open set, so they are already canonical.)
    events_written = 0
    if auto_close:
        from commitment_state import close_commitments
        results = close_commitments(
            workspace_root,
            [{
                "commitment_id": c["commitment_id"],
                "resolved_by": "sent_reconcile",
                "evidence": c.get("evidence") or "matched an outbound send",
                "primary_thread_id": c.get("primary_thread_id") or "",
            } for c in auto_close],
            source_skill=source_skill,
        )
        by_id = {str(c["commitment_id"]): c for c in auto_close}
        closed_or_already: set[str] = set()
        for r in results:
            rid = str(r.get("commitment_id"))
            if r["status"] == "closed":
                events_written += 1
                closed_or_already.add(rid)
            elif r["status"] == "already_resolved":
                closed_or_already.add(rid)
            elif r.get("error") == "PendingReviewError" and rid in by_id:
                # F2/F5: a pending_review commitment is never auto-resolved —
                # demote it to the confirm list instead of closing it.
                pending.append(by_id[rid])
            # CommitmentIdError: logged loudly by close_commitments; the
            # proposal is dropped rather than written as an orphan tombstone.
        auto_close = [c for c in auto_close if str(c["commitment_id"]) in closed_or_already]

    # HYG1 Item 1 (the MC1 4.7 wire-up): auto-record per-person receipts for
    # partial_received recommendations — non-destructive by construction
    # (mark_partial_received NEVER closes; a completed roster only stamps the
    # derived all_counterparties_received PROPOSE-closure signal). Idempotent
    # per (commitment, counterparty): a counterparty already in the item's
    # accumulated received_from (the pre-close `opens` projection) never gets
    # a second receipt — the write-side mirror of the orchestrator's "never
    # chase a counterparty already in received_from". Name-only matches were
    # already routed to skipped_names by reconcile_sent (never guess an id).
    n_partial_receipts = 0
    partial_recorded: list = []      # slim rows for the receipt
    partial_propose_closure: list = []  # rosters completed by this run
    partial_skipped_names: list = []
    if partial:
        from commitment_parties import received_from_ids as _rcv_ids
        from commitment_state import mark_partial_received
        already_by_cid = {}
        for c in opens:
            already_by_cid[str(_commitment_id(c))] = set(_rcv_ids(c))
        for p in partial:
            already = already_by_cid.get(str(p["commitment_id"]), set())
            recorded_cps = []
            proposed = False
            for r in p["receipts"]:
                cp_id = r["counterparty_id"]
                if cp_id in already:
                    continue
                try:
                    result = mark_partial_received(
                        workspace_root,
                        p["commitment_id"],
                        # Person-shaped like every other caller (apply-choices
                        # passes sender_person_id, the orchestrator owner_id) —
                        # the sender IS the user in a Sent reconcile. A skill
                        # string here would surprise any future reader of
                        # data.received_by.
                        received_by=user_person_id,
                        counterparty_id=cp_id,
                        evidence=r.get("evidence") or "delivered by an outbound send",
                        source_skill=source_skill,
                    )
                except Exception:
                    # A bad id / race is logged by the writer's own guards;
                    # never let one receipt failure abort the reconcile run.
                    continue
                if result.get("status") == "received":
                    n_partial_receipts += 1
                    recorded_cps.append(cp_id)
                    already.add(cp_id)
                    if result.get("propose_closure"):
                        proposed = True
            for nm in p.get("skipped_names") or []:
                partial_skipped_names.append(
                    {"commitment_id": p["commitment_id"], "name": nm}
                )
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

    # Stage E (F5): the 0.30–0.55 pending band MUST NOT evaporate. Persist
    # each pending proposal as a commitment_review_proposed event so the next
    # Commitments chat surfaces it for one-click confirm/deny — before this,
    # pending existed only inside the returned receipt (one brief line, then
    # gone). Deduped against the OPEN proposal set (a still-open proposal for
    # the same commitment is not re-written; confirmed/dismissed ones may be
    # re-proposed by genuinely new sends). Cursor mechanics untouched.
    reviews_written = 0
    if pending:
        from cru_match import load_open_review_proposals
        from event_gate import append_event
        already_proposed = {
            (p.get("data") or {}).get("commitment_id")
            for p in load_open_review_proposals(str(events_path))
        }
        for p in pending:
            if p["commitment_id"] in already_proposed:
                continue
            append_event(events_path, [{
                "type": "commitment_review_proposed",
                "source_skill": source_skill,
                "primary_thread_id": p.get("primary_thread_id") or "",
                "data": {
                    "commitment_id": p["commitment_id"],
                    "proposed_resolution": "auto_resolve",
                    "match_score": round(p.get("score") or 0, 3),
                    "evidence": p.get("evidence") or "matched an outbound send",
                    # FB-19: the row's own name. Without it the LB1 adapter
                    # has no title, `_row_name` falls back to the shape label,
                    # and the card renders a bare "Housekeeping — matched your
                    # sent message X" with nothing to identify WHAT matched
                    # (the live 2026-07-16 render). The title is right here in
                    # hand at write time — dropping it was the whole bug.
                    "title": p.get("title") or "",
                    # FS-11: only genuinely ambiguous matches reach here now
                    # (unambiguous moderate matches auto-closed above). Carry a
                    # TTL so an un-adjudicated proposal expires instead of
                    # accumulating; the LB1 review adapter drops expired ones.
                    "ttl_days": REVIEW_PROPOSAL_TTL_DAYS,
                    "ambiguous": True,
                },
            }], holder=source_skill)
            reviews_written += 1

    # BUG-3719 (v4.6.2): OPEN commitments from the user's own sent commissives
    # that match nothing open — the rescue path for promises the unread-gated
    # inbox triage never saw (thread read+replied same day → never a triage
    # candidate → the outbound promise never scanned; close-only reconcile
    # could never reconcile a promise that was never logged). Dedup runs
    # against the PRE-close `opens` projection loaded above, so a sent
    # restatement merges into its original even when this same fire's matcher
    # just closed it (a promise already tracked is never double-tracked).
    # Per-item gate failures land LOUDLY in capture["errors"] + the audit
    # counts — never a crash after the closes above already landed.
    capture = None
    if sent_commitment_items is not None:
        from sent_capture import capture_sent_items
        capture = capture_sent_items(
            workspace_root,
            sent_commitment_items,
            user_person_id=user_person_id,
            opens=opens,
            source_skill=source_skill,
            provider=provider,
        )

    # Advance the cursor to the newest Sent ts we saw (never backwards).
    cursor_after = cursor_before
    new_ts = res.get("cursor_ts")
    if new_ts and (cursor_before is None or new_ts > cursor_before):
        cursor_after = new_ts
        _write_cursor(workspace_root, raw, cursor_after, source_skill=source_skill)

    def _slim(items):
        from event_time import event_time
        return [{"commitment_id": c["commitment_id"], "title": c.get("title") or "",
                 "ts": event_time(c)} for c in items]

    n_auto = len(auto_close)
    n_pend = len(pending)
    n_fetched = len(sent_messages or [])

    # ALWAYS emit a `sent_reconcile` AUDIT event — even on a 0-scan run — so every
    # run leaves a verifiable trace in events.jsonl (Bug #98-v3). Enforcement now
    # points at THIS event (cursor_from / cursor_to / sent_scanned_count), NOT at
    # a printed sentence: the v3.18.9 receipt gate checked the narration, and the
    # model gamed it by feeding the matcher a curated message list and printing a
    # truthful-looking line without a real fetch. A validator reads this event back
    # (validate_reconcile_ran) — a cursor delta backed by a scan count can't be
    # faked the way a sentence can.
    from next_seq import next_seq as _next_seq
    from atomic_write import atomic_append_jsonl as _append
    from cru_match import _now_iso as _audit_ts
    audit_event = {
        "seq": _next_seq(str(events_path)),
        "ts": _audit_ts(),
        "type": "sent_reconcile",
        "source_skill": source_skill,
        "data": {
            # v4.5.2 R1 receipt-contract fields (shared/RECEIPT_CONTRACT.md).
            "task_id": "reconcile-sent",
            "kind": "reconcile-sent",
            "status": "complete",
            "fired_via": fired_via,
            "cursor_from": cursor_before,
            "cursor_to": cursor_after,
            "sent_scanned_count": n_fetched,
            "n_closed": n_auto,
            "n_pending": n_pend,
            # HYG1 Item 1 — per-person receipts auto-recorded this run
            # (extend the data dict, no new event type).
            "n_partial_receipts": n_partial_receipts,
        },
    }
    try:
        from receipts import _machine_name

        _machine = _machine_name()
        if _machine:
            audit_event["data"]["machine"] = _machine
    except Exception:
        pass
    # B6: fold the outcome-watch counts (replies/no-reply/bounced) into the SAME
    # audit event so one fire leaves one verifiable trace. Free-form `data`, so
    # no schema change for the audit part. Co-locating two silent WRITES is fine
    # (the Bug #98 anti-pattern was a silent write next to a visible deliverable).
    if isinstance(outcome_watch_summary, dict):
        audit_event["data"]["outcome_watch"] = {
            k: outcome_watch_summary.get(k)
            for k in ("checked", "replied", "no_reply_7d", "bounced", "still_pending")
        }
    # BUG-3719: when the capture pass ran (even finding nothing), the audit
    # carries its counts — the same one-verifiable-trace-per-fire doctrine as
    # the outcome watch. Absent fields = a pre-4.6.2 run or items not passed.
    if isinstance(capture, dict):
        audit_event["data"]["n_opened"] = capture["n_opened"]
        audit_event["data"]["n_capture_merged"] = capture["n_merged"]
        audit_event["data"]["n_capture_observed"] = capture["n_observed"]
        audit_event["data"]["n_capture_errors"] = capture["n_errors"]
    _append(events_path, [audit_event])

    if n_fetched == 0:
        summary = (f"No new sent mail since {_short_date(cursor_before) or 'the last check'} "
                   f"— nothing to reconcile.")
    elif n_auto == 0:
        summary = (f"Checked {n_fetched} sent message{'s' if n_fetched != 1 else ''} "
                   f"— nothing matched an open commitment.")
    else:
        tail = f", {n_pend} to confirm" if n_pend else ""
        summary = (f"Reconciled your sent mail through {_short_date(cursor_after)}: "
                   f"closed {n_auto} you'd already handled{tail}.")
    n_opened = capture["n_opened"] if isinstance(capture, dict) else 0
    if n_opened:
        summary += (f" Started tracking {n_opened} new "
                    f"promise{'s' if n_opened != 1 else ''} from your sent mail.")
    if n_partial_receipts:
        summary += (
            f" Noted delivery to {n_partial_receipts} "
            f"recipient{'s' if n_partial_receipts != 1 else ''} on group items"
            " — those stay open until everyone's received theirs."
        )
    if partial_propose_closure:
        titles = ", ".join(
            f"\"{p['title']}\"" for p in partial_propose_closure if p.get("title")
        ) or "a group item"
        summary += (
            f" Everyone on {titles} has now received theirs — close it when ready."
        )

    return {
        "ran": True,
        "cursor_before": cursor_before,
        "cursor_after": cursor_after,
        "cursor_advanced": cursor_after != cursor_before,
        "n_fetched": n_fetched,
        "n_open_before": n_open_before,
        "n_auto_closed": n_auto,
        "n_pending": n_pend,
        "events_written": events_written,
        # Stage E: pending proposals persisted this run (deduped) — the next
        # Commitments chat's review section reads them back.
        "reviews_written": reviews_written,
        "resolved": _slim(auto_close),
        "pending": _slim(pending),
        # HYG1 Item 1 — per-person receipt pass (additive).
        "n_partial_receipts": n_partial_receipts,
        "partial": partial_recorded,
        "partial_propose_closure": partial_propose_closure,
        "partial_skipped_names": partial_skipped_names,
        # BUG-3719 capture pass (additive; zeros/None when items not passed).
        "n_opened": n_opened,
        "opened": list(capture["opened"]) if isinstance(capture, dict) else [],
        "capture": capture,
        "summary": summary,
    }


def validate_reconcile_ran(workspace_root, *, since_cursor=None) -> dict:
    """Read events.jsonl back and confirm a REAL reconciliation ran (Bug #98-v3).

    The ungameable check the v3.18.9 narration-gate lacked: a printed "reconciled"
    sentence with no `sent_reconcile` audit event in the log returns ok=False.
    Looks at the LATEST `sent_reconcile` event. If `since_cursor` is given, this
    run's audit must carry `cursor_from == since_cursor` (so a stale prior audit
    can't pass for the current run).

    Returns {ok, ran, reason?, cursor_from, cursor_to, sent_scanned_count, n_closed}.
    A 0-scan run is still a valid run (ok=True) — it ran, found nothing; the audit
    event proves the fetch happened.
    """
    import json
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not events_path.exists():
        return {"ok": False, "ran": False, "reason": "no events.jsonl"}
    latest = None
    for line in events_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") == "sent_reconcile":
            latest = e  # append-ordered → keep the last one seen
    if latest is None:
        return {"ok": False, "ran": False,
                "reason": "no sent_reconcile audit event — reconciliation did not actually run"}
    d = latest.get("data") or {}
    if since_cursor is not None and d.get("cursor_from") != since_cursor:
        return {"ok": False, "ran": True,
                "reason": f"latest audit is from a prior run (cursor_from={d.get('cursor_from')!r} "
                          f"!= expected {since_cursor!r})",
                "cursor_to": d.get("cursor_to")}
    return {
        "ok": True, "ran": True,
        "cursor_from": d.get("cursor_from"),
        "cursor_to": d.get("cursor_to"),
        "sent_scanned_count": d.get("sent_scanned_count"),
        "n_closed": d.get("n_closed"),
    }


__all__ = ["reconcile_sent", "to_resolved_events", "reconcile_and_receipt",
           "validate_reconcile_ran"]


if __name__ == "__main__":
    # Convenience CLI: read a JSON payload {open_commitments, sent_messages,
    # user_person_id} from the file path in argv[1], print the reconcile result
    # as JSON. Lets a skill shell in without inlining the matching logic.
    import json

    if len(sys.argv) < 2:
        print("usage: reconcile_sent_commitments.py <payload.json>", file=sys.stderr)
        raise SystemExit(2)
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = reconcile_sent(
        payload.get("open_commitments") or [],
        payload.get("sent_messages") or [],
        user_person_id=payload.get("user_person_id") or "",
    )
    print(json.dumps(out, indent=2))
